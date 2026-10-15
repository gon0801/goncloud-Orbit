"""MercadoMeli (REPRICING 02 M.4): el adaptador MeLi del puerto `Mercado`.

Espejo de `app.spapi.precio_mercado.MercadoAmazon` con fuentes MeLi:
catalogo de `meli_publicacion_observation` (activa canonica G27), cuenta
de `leer_cuenta` (la fila trae la del miembro que manda, M.3), historia
con estados/stock/visitas de la publicacion, cotizacion por
`CotizadorMeli`, vivo por GET y escritura por `MeliWriteClient` (forma
sin sellar: solo `sonda=True`).

Todo lo que la plataforma aporta vive aqui: SQL MeLi, `partir_fees_meli`
para los fees, `_disponibilidad_meli`, `_detalle_fee_meli` (los nodos
MeLi no traen tax/included). Lo compartido (cambios, historial, ventas,
oferta-para-cotizar, tipos `Lectura`/`MetaGoal`) vive en `app/precio/` y
se importa: este modulo jamas importa `app.spapi.*` (candado M.4:
`test_meli_no_importa_spapi`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import psycopg

from app.estimacion_insumos import oferta_para_cotizar
from app.estimacion_reader import leer_cuenta
from app.estimacion_venta import DetalleFee
from app.meli.estimacion import partir_fees_meli
from app.precio import puerto
from app.precio.cambios import PublicacionSinSku
from app.precio.config import ConfigPrecio
from app.precio.fuentes import (
    cambios_previos,
    historial_margenes,
    ultima_subida,
    ventas_unidad,
)
from app.precio.puerto import Lectura, MetaGoal
from app.precio.tipos import (
    EntradaDecision,
    Escenario,
    Fees,
    Importe,
    ObservacionPricing,
)
from app.precio.ventas import evaluar_senal
from app.reputacion_clientes import ReputacionError

logger = logging.getLogger(__name__)

__all__ = ["MercadoMeli", "Mercado", "historia_meli_unidad"]

_VENTANA_HISTORIA_DIAS = 75

_SQL_CATALOGO = (
    "SELECT l.id, l.seller_sku, l.product_id, l.external_id,"
    " o.precio, o.moneda, (o.observed_at AT TIME ZONE 'UTC')::date"
    " FROM listing l"
    " JOIN (SELECT DISTINCT ON (item_id) item_id, estado, precio, moneda, observed_at"
    " FROM meli_publicacion_observation"
    " ORDER BY item_id, observed_at DESC, id DESC) o"
    " ON o.item_id = l.external_id"
    " WHERE l.platform = 'meli' AND o.estado = 'active'"
)

_SQL_MIEMBROS = (
    "SELECT listing_id, seller_sku, product_id, variante_ext FROM listing_miembro"
    " WHERE listing_id = ANY(%s) AND platform = 'meli' AND activo"
    " ORDER BY listing_id, seller_sku"
)


def _detalle_fee_meli(nodo: dict) -> DetalleFee:
    """Nodo MeLi (`fee_type`/`final_fee`, sin tax) a `DetalleFee`.

    Espejo de Amazon: lo corrupto sale como `ValueError`, nunca crudo.
    """
    if not isinstance(nodo, dict):
        raise ValueError(f"detalle de fee no es objeto: {nodo!r}")
    try:
        return DetalleFee(
            fee_type=nodo["fee_type"],
            final_fee=Decimal(str(nodo["final_fee"])),
            tax_amount=None,
            included_fee_details=(),
        )
    except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"detalle de fee invalido: {nodo!r}") from exc


def _disponibilidad_meli(estados: list[str], stocks: list) -> str:
    """Disponibilidad del dia desde estados y stocks observados.

    Espejo estricto de Amazon FBA: cualquier no-`active` o stock <= 0 es
    `no_disponible`; `disponible` exige todo activo y todo stock > 0; sin
    prueba, `sin_observacion`.
    """
    activos = [e == "active" for e in estados]
    if any(s is not None and s <= 0 for s in stocks) or any(not b for b in activos):
        return "no_disponible"
    if activos and all(activos) and stocks and all(s is not None and s > 0 for s in stocks):
        return "disponible"
    return "sin_observacion"


def historia_meli_unidad(
    conn: psycopg.Connection, unidad: puerto.UnidadPrecio, hoy: date
) -> puerto.HistoriaUnidad:
    """Historia de 75 dias de la unidad MeLi (para `elegir_senal`).

    Ventas y cobertura salen del lector compartido `ventas_unidad`
    (misma fuente que Amazon); estados, stocks y visitas (D10: sirven
    como trafico) de `meli_publicacion_observation`. Sin buy box ni
    clics: MeLi no los da.
    """
    platform = unidad.plataforma
    desde = hoy - timedelta(days=_VENTANA_HISTORIA_DIAS)
    ventas = ventas_unidad(
        conn,
        listing_id=unidad.listing_id,
        producto_ancla=unidad.producto_ancla,
        platform=platform,
        desde=desde,
        hoy=hoy,
    )
    estados: dict = {}
    stocks: dict = {}
    visitas: dict = {}
    for dia, estado, stock, visita in conn.execute(
        "SELECT metric_date, estado, stock, visitas FROM meli_publicacion_observation"
        " WHERE item_id = %s AND metric_date BETWEEN %s AND %s"
        " ORDER BY observed_at, id",
        (unidad.externo, desde, hoy),
    ).fetchall():
        estados.setdefault(dia, []).append(estado)
        stocks.setdefault(dia, []).append(stock)
        if visita is not None:
            visitas[dia] = visita
    dias = []
    for dia in sorted(set(ventas.dias) | set(estados) | set(stocks) | set(visitas)):
        dias.append(
            puerto.DiaUnidad(
                dia=dia,
                unidades=ventas.dias.get(dia, 0),
                disponibilidad=_disponibilidad_meli(estados.get(dia, []), stocks.get(dia, [])),
                exposiciones=visitas.get(dia),
                clics=None,
                buy_box_propia=None,
            )
        )
    return puerto.HistoriaUnidad(
        dias=tuple(dias),
        primera_venta=ventas.primera_venta,
        sin_atribuir=ventas.sin_atribuir,
        ledger_cubierto_hasta=ventas.ledger_cubierto_hasta,
    )


def _cotizacion_de_arbol_meli(
    precio: Importe, total: Decimal, detalles: tuple[DetalleFee, ...]
) -> puerto.Cotizacion:
    """`Cotizacion` desde un arbol MeLi ya validado (suma + partido)."""
    suma = sum((d.final_fee for d in detalles), Decimal(0))
    if suma != total:
        return puerto.Cotizacion(
            precio=precio,
            fees=None,
            estimada_en=None,
            error_code="fee_error:fee_no_concilia",
        )
    partido = partir_fees_meli(detalles, total)
    if isinstance(partido, str):
        return puerto.Cotizacion(precio=precio, fees=None, estimada_en=None, error_code=partido)
    variable, fijo = partido
    return puerto.Cotizacion(
        precio=precio,
        fees=Fees(
            total=Importe(total, precio.moneda),
            variable=Importe(variable, precio.moneda),
            fijo=Importe(fijo, precio.moneda),
            impuesto_pendiente=False,
        ),
        estimada_en=None,
        error_code=None,
    )


class MercadoMeli:
    """`puerto.Mercado` sobre MeLi: mismo protocolo, fuentes MeLi."""

    clave: str
    moneda: str
    grupo_tasa = "meli"

    def __init__(
        self,
        clave: str,
        *,
        lector=None,
        escritor=None,
        fees=None,
        limitador=None,
    ) -> None:
        if clave != "meli":
            raise ValueError(f"clave de mercado MeLi invalida: {clave!r}")
        self.clave = clave
        self.moneda = "MXN"
        self._lector = lector
        self._escritor = escritor
        self._fees = fees
        self._limitador = limitador
        self._ruta_error: str | None = None
        if lector is not None:
            lector_user_agent = getattr(lector, "user_agent", None)
            if callable(lector_user_agent):
                lector_user_agent("orbit-precio")

    def _exigir_clientes(self) -> None:
        if self._lector is None or self._escritor is None or self._fees is None:
            raise RuntimeError("mercado sin clientes: vitrina de solo lectura")

    def validar_destino(self, unidad: puerto.UnidadPrecio) -> None:
        """El PUT exige item MeLi (la ruta no lleva SKU)."""
        if not unidad.externo:
            raise PublicacionSinSku(f"listing_sin_item: listing {unidad.listing_id}")

    def catalogo(self, conn, hoy: date) -> tuple[puerto.UnidadActiva, ...]:
        """Activa canonica de MeLi (G27): ultima obs `active` por item.

        INNER a proposito (difiere de Amazon): M.1 crea listing+obs en
        una transaccion por item, asi que huerfanas no existen; una obs
        sin listing es corrupcion y no debe fluir con `unidad=None`.
        """
        filas = conn.execute(_SQL_CATALOGO).fetchall()
        if not filas:
            return ()
        miembros_por_lid: dict[int, list] = {}
        for lid, sku, pid, variante in conn.execute(
            _SQL_MIEMBROS, ([f[0] for f in filas],)
        ).fetchall():
            miembros_por_lid.setdefault(lid, []).append(puerto.Miembro(pid, str(sku), variante))
        activas = []
        for lid, ancla, producto, externo, _precio, _moneda, observado_en in filas:
            miembros = miembros_por_lid.get(lid, [])
            activas.append(
                puerto.UnidadActiva(
                    unidad=puerto.UnidadPrecio(
                        listing_id=lid,
                        plataforma=self.clave,
                        externo=externo,
                        canal=None,
                        producto_ancla=producto,
                        miembros=tuple(miembros),
                    ),
                    seller_sku=str(ancla),
                    precio=None,
                    dias_sin_reportar=(hoy - observado_en).days if observado_en else None,
                )
            )
        return tuple(activas)

    def insumos(
        self,
        conn,
        unidades: Sequence[puerto.UnidadPrecio],
        *,
        hoy: date,
        ahora: datetime,
        config: ConfigPrecio,
        metas: Mapping[int, MetaGoal],
    ) -> Mapping[int, Lectura]:
        """La entrada del dia de cada unidad, por `listing_id` (por lote).

        Espejo de Amazon: nunca levanta por un dato faltante (el fallo
        viaja en la Lectura). Antes de la cuenta: variante sin mapear
        (`variante_sin_mapear` con el SKU) o unidad sin miembros
        (`costo_ausente`, como la siembra).
        """
        from app.precio.config import _entero as _config_entero
        from app.precio.fuentes import config_vigente_settings

        trafico_min = _config_entero(
            config_vigente_settings(conn), "precio_senal_trafico_min", minimo=20, maximo=5000
        )
        por_listing = list(dict.fromkeys(u.listing_id for u in unidades))
        obs_por_item = {
            fila[0]: fila
            for fila in conn.execute(
                "SELECT DISTINCT ON (item_id) item_id, precio, moneda, observed_at"
                " FROM meli_publicacion_observation"
                " WHERE item_id = ANY(%s) AND metric_date = %s AND precio IS NOT NULL"
                " ORDER BY item_id, observed_at DESC, id DESC",
                ([u.externo for u in unidades], hoy),
            ).fetchall()
        }
        racha_por_lid = {
            fila[0]: fila[1]
            for fila in conn.execute(
                "SELECT DISTINCT ON (listing_id) listing_id, racha_senal"
                " FROM precio_decision"
                " WHERE listing_id = ANY(%s) AND platform = %s AND decision_date < %s"
                " ORDER BY listing_id, decision_date DESC",
                (por_listing, self.clave, hoy),
            ).fetchall()
        }
        lecturas: dict[int, Lectura] = {}
        for unidad in unidades:
            meta = metas[unidad.listing_id]
            sin_mapear = [m.seller_sku for m in unidad.miembros if m.product_id is None]
            if sin_mapear:
                lecturas[unidad.listing_id] = Lectura(
                    entrada=None,
                    motivo="variante_sin_mapear",
                    diagnostico=f"variantes sin mapear: {', '.join(sorted(sin_mapear))}",
                    ids=None,
                    cotizable=None,
                    fees_escenario="variante_sin_mapear",
                )
                continue
            if not unidad.miembros:
                lecturas[unidad.listing_id] = Lectura(
                    entrada=None,
                    motivo="costo_ausente",
                    diagnostico="listing sin miembros activos",
                    ids=None,
                    cotizable=None,
                    fees_escenario="costo_ausente",
                )
                continue
            try:
                cuenta = leer_cuenta(conn, unidad.listing_id, as_of=ahora)
            except ValueError as exc:
                lecturas[unidad.listing_id] = Lectura(
                    entrada=None,
                    motivo="escenario_incoherente",
                    diagnostico=str(exc),
                    ids=None,
                    cotizable=None,
                    fees_escenario="escenario_incoherente",
                )
                continue
            try:
                lecturas[unidad.listing_id] = self._lectura_unidad(
                    conn,
                    unidad,
                    meta,
                    cuenta,
                    hoy=hoy,
                    ahora=ahora,
                    config=config,
                    obs_por_item=obs_por_item,
                    racha_por_lid=racha_por_lid,
                    trafico_min=trafico_min,
                )
            except psycopg.Error:
                raise
            except Exception as exc:  # noqa: BLE001 - B1: una publicacion no tumba al lote
                lecturas[unidad.listing_id] = Lectura(
                    entrada=None,
                    motivo="escenario_incoherente",
                    diagnostico=str(exc),
                    ids=None,
                    cotizable=None,
                    fees_escenario="escenario_incoherente",
                )
        return lecturas

    def _lectura_unidad(
        self,
        conn,
        unidad,
        meta,
        cuenta,
        *,
        hoy,
        ahora,
        config,
        obs_por_item,
        racha_por_lid,
        trafico_min,
    ) -> Lectura:
        """La Lectura de una unidad con cuenta disponible (levanta ValueError)."""
        if cuenta is None:
            return Lectura(
                entrada=None,
                motivo="precio_ausente",
                diagnostico="sin escenario disponible",
                ids=None,
                cotizable=None,
                fees_escenario="precio_ausente",
            )
        if isinstance(cuenta, str):
            return Lectura(
                entrada=None,
                motivo=cuenta,
                diagnostico=f"escenario no disponible: {cuenta}",
                ids=None,
                cotizable=None,
                fees_escenario=cuenta,
            )
        fees_escenario: Fees | str
        partido = partir_fees_meli(cuenta.fee_detalles, cuenta.componentes.fees.valor)
        if isinstance(partido, str):
            fees_escenario = partido
        else:
            variable, fijo = partido
            fees_escenario = Fees(
                total=cuenta.componentes.fees,
                variable=Importe(variable, cuenta.componentes.fees.moneda),
                fijo=Importe(fijo, cuenta.componentes.fees.moneda),
                impuesto_pendiente=False,
            )
        observacion = None
        fila_obs = obs_por_item.get(unidad.externo)
        if fila_obs is not None:
            observacion = ObservacionPricing(
                precio=Importe(Decimal(fila_obs[1]), fila_obs[2]),
                observada_en=fila_obs[3],
            )
        historia = historia_meli_unidad(conn, unidad=unidad, hoy=hoy)
        cambios = cambios_previos(conn, unidad.listing_id, self.clave)
        senal = evaluar_senal(
            historia,
            hoy=hoy,
            config=config,
            racha_previa=int(racha_por_lid.get(unidad.listing_id) or 0),
            ultima_subida=ultima_subida(cambios),
            trafico_min=trafico_min,
        )
        escenario = Escenario(
            componentes=cuenta.componentes,
            fee_detalles=cuenta.fee_detalles,
            precio_cotizado=cuenta.precio_cotizado,
            iva_divisor=cuenta.iva_divisor,
            isr_tasa=cuenta.isr_tasa,
            precio_incluye_iva=cuenta.precio_incluye_iva,
            oferta_observada_en=cuenta.oferta_observada_en,
        )
        cotizable = oferta_para_cotizar(conn, cuenta.refs.oferta_observation_id)
        entrada = EntradaDecision(
            listing_id=unidad.listing_id,
            platform=self.clave,
            product_id=unidad.producto_ancla,
            canal=cuenta.canal,
            mode=meta.mode,
            goal=meta.goal,
            escenario=escenario,
            pricing=observacion,
            senal=senal,
            cambios=cambios,
            historial=historial_margenes(conn, unidad.listing_id, self.clave),
            goal_vigente_desde=meta.goal_vigente_desde,
            motivo_estimacion=None,
            buy_box_is_own=None,
        )
        return Lectura(
            entrada=entrada,
            motivo=None,
            diagnostico="",
            ids={
                "escenario_id": cuenta.refs.escenario_id,
                "fee_observation_id": cuenta.refs.fee_observation_id,
                "oferta_observation_id": cuenta.refs.oferta_observation_id,
                "logistica_origen": cuenta.logistica_origen,
                "envio_muestra_id": cuenta.refs.envio_muestra_id,
            },
            cotizable=cotizable,
            fees_escenario=fees_escenario,
            historia=historia,
        )

    def observado(
        self,
        conn,
        unidad: puerto.UnidadPrecio,
        *,
        despues_de: date,
        hasta: date,
    ) -> ObservacionPricing | None:
        """Ultima obs con precio en `(despues_de, hasta]` (espejo Amazon)."""
        fila = conn.execute(
            "SELECT precio, moneda, observed_at FROM meli_publicacion_observation"
            " WHERE item_id = %s AND precio IS NOT NULL"
            " AND metric_date > %s AND metric_date <= %s"
            " ORDER BY observed_at DESC, id DESC LIMIT 1",
            (unidad.externo, despues_de, hasta),
        ).fetchone()
        if fila is None:
            return None
        return ObservacionPricing(precio=Importe(Decimal(fila[0]), fila[1]), observada_en=fila[2])

    def cotizar(self, conn, oferta, precio: Importe) -> puerto.Cotizacion:
        """Cotiza los fees a este precio por `CotizadorMeli` (GET)."""
        self._exigir_clientes()
        if precio.moneda != oferta.price_currency:
            raise ValueError(
                f"moneda del pedido ({precio.moneda}) != moneda de la oferta"
                f" ({oferta.price_currency})"
            )

        def _reloj_base():
            fila = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0]
            return fila.replace(tzinfo=UTC)

        resultado = self._fees.cotizar(oferta, precio.valor, ahora=_reloj_base)
        if resultado.estado != "success" or resultado.total_fees is None:
            return puerto.Cotizacion(
                precio=precio,
                fees=None,
                estimada_en=None,
                error_code=resultado.error_code or "desconocido",
            )
        detalles = tuple(_detalle_fee_meli(nodo) for nodo in (resultado.fee_details or []))
        return _cotizacion_de_arbol_meli(precio, resultado.total_fees, detalles)

    def cotizacion_guardada(
        self, conn, *, evento: str, listing_id: int, hoy: date, oferta_id: int, pedido
    ) -> tuple[int, puerto.Cotizacion] | None:
        """La cotizacion de hoy para este intento, ya como `puerto.Cotizacion`.

        Solo reusa si es la misma oferta al mismo precio (si difiere,
        revienta visible: no se mezcla).
        """
        fila = conn.execute(
            "SELECT id, quoted_price, quoted_price_currency, total_fees, fee_details,"
            " estado, error_code, oferta_observation_id FROM precio_cotizacion"
            " WHERE source_event_id = %s",
            (evento,),
        ).fetchone()
        if fila is None:
            return None
        (cid, precio_q, moneda_q, total, detalles, estado, codigo, oferta_guardada) = fila
        if (
            oferta_guardada != oferta_id
            or precio_q != pedido.precio.valor
            or moneda_q != pedido.precio.moneda
        ):
            raise ValueError(
                f"cotizacion {cid} de hoy no coincide con el pedido"
                f" (oferta {oferta_guardada}/{oferta_id}, {precio_q} {moneda_q}"
                f" vs {pedido.precio.valor} {pedido.precio.moneda})"
            )
        precio = Importe(Decimal(precio_q), moneda_q)
        if estado != "success":
            return cid, puerto.Cotizacion(
                precio=precio, fees=None, estimada_en=None, error_code=codigo or "desconocido"
            )
        detalles_q = tuple(_detalle_fee_meli(nodo) for nodo in (detalles or []))
        return cid, _cotizacion_de_arbol_meli(precio, Decimal(total), detalles_q)

    def precio_vivo(self, unidad: puerto.UnidadPrecio) -> Importe:
        """Precio propio leido ahora (GET del item)."""
        self._exigir_clientes()
        try:
            datos, _fetched = self._lector.get(f"/items/{unidad.externo}")
        except ReputacionError as exc:
            raise puerto.PrecioVivoAusente(f"vivo MeLi ilegible: {exc}") from exc
        try:
            precio = Decimal(str(datos.get("price")))
            moneda = datos.get("currency_id")
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise puerto.PrecioVivoAusente(f"vivo MeLi ilegible: {exc}") from exc
        if not precio.is_finite() or precio <= 0 or not moneda:
            raise puerto.PrecioVivoAusente("vivo MeLi ilegible: price/currency_id")
        return Importe(precio, moneda)

    def escribir(
        self, unidad: puerto.UnidadPrecio, precio: Importe, *, sonda: bool = False
    ) -> puerto.Acuse:
        """PUT del precio via `MeliWriteClient`; devuelve el acuse saneado."""
        self._exigir_clientes()
        self.validar_destino(unidad)
        self._ruta_error = f"/items/{unidad.externo}"
        variantes = [m.variante for m in unidad.miembros if m.variante]
        return self._escritor.poner_precio(
            unidad.externo, variantes, precio.valor, precio.moneda, sonda=sonda
        )

    def cerrar(self) -> None:
        """Cierra los clientes sin tumbar la corrida si uno falla."""
        for cliente in (self._lector, self._escritor, self._fees):
            cierre = getattr(cliente, "close", None)
            if callable(cierre):
                try:
                    cierre()
                except Exception as exc:  # noqa: BLE001 - best effort
                    logger.warning(
                        "cerrar: fallo best-effort (%s): %s",
                        type(cliente).__name__,
                        type(exc).__name__,
                    )


Mercado = MercadoMeli
