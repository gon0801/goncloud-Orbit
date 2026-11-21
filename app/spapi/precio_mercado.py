"""MercadoAmazon: el adaptador de Amazon del motor de precios.

Aqui aterriza lo que hoy estaba repartido entre `corrida.py` (lecturas de
`spapi_price_observation` y compania, `oferta_para_cotizar` (en
`estimacion_insumos`), `_verificada`,
`_detalle_fee`, el armado de la entrada) y la mitad Amazon de la escritura.
La corrida recibe esta clase duck-typed: no implementa todavia el Protocol
estricto del puerto (eso llega con S/F), pero ya entrega cotizaciones
`puerto.Cotizacion` con los fees partidos y escribe por `Acuse`.

Sin clientes (`lector is None`) es solo vitrina: `precio_vivo`, `escribir`
y `cotizar` levantan `RuntimeError` (la pantalla lee catalogo/insumos/
observado solamente).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import psycopg

from app.estimacion_fees import ProductFeesClient, cotizar_a_precio
from app.estimacion_reader import leer_cuenta
from app.estimacion_venta import DetalleFee
from app.precio import puerto
from app.precio.config import ConfigPrecio
from app.precio.fuentes import ventas_unidad
from app.precio.objetivo import ErrorObjetivo, derivar_ref_fijo
from app.precio.puerto import Lectura, MetaGoal
from app.precio.tipos import Fees, Importe, ObservacionPricing
from app.precio_mercado_base import (
    construir_lectura_unidad,
    detalle_fee_desde_dict,
    dia_utc,
    disponibilidad_estricta,
    lectura_error,
    leer_trafico_min_y_rachas,
    suma_top,
    tiene_tax,
)
from app.redaction import install_scrub_filter
from app.spapi import precio_write as _precio_write
from app.spapi.client import SpapiAuthError, construir_ruta_listings
from app.spapi.precio_write import (
    PublicacionSinSku,
    _ack_saneado,
    _estado_aceptado,
    _ruta_sin_sku,
    leer_precio_vivo,
)
from app.spapi.write_client import validar_patch_listings

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

__all__ = ["MercadoAmazon", "Mercado"]

# La activa canonica de Amazon (BUYABLE de SP-API): la ultima observacion
# por (seller_sku, platform) cuyo status contiene BUYABLE, con LEFT JOIN a
# `listing` (dos listings con el mismo SKU cuentan uno; una activa sin
# listing llega con listing_id NULL). El filtro va DESPUES del DISTINCT ON:
# una observacion no BUYABLE mas reciente que la ultima BUYABLE apaga el
# SKU (el orden invertido contaria publicaciones muertas).
_SQL_CANO = (
    "SELECT listing_id, seller_sku, platform, asin, status, observed_at FROM ("
    " SELECT DISTINCT ON (e.seller_sku, e.platform)"
    " l.id AS listing_id, e.seller_sku, e.platform, e.asin, e.status, e.observed_at"
    " FROM spapi_listing_estado_observation e"
    " LEFT JOIN listing l ON l.seller_sku = e.seller_sku AND l.platform = e.platform"
    " WHERE e.platform = %s"
    " ORDER BY e.seller_sku, e.platform, e.observed_at DESC, l.id NULLS LAST"
    ") u WHERE u.status LIKE '%%BUYABLE%%'"
)


def _detalle_fee(nodo: dict) -> DetalleFee:
    """`fee_details` normalizado (forma de `parsear_respuesta_fees`) a arbol."""
    return detalle_fee_desde_dict(nodo, con_hijos=True)


_tiene_tax = tiene_tax
_suma_top = suma_top
_dia_utc = dia_utc


def _disponibilidad(*, canal: str | None, estados: list, stocks: list) -> puerto.Disponibilidad:
    """Disponibilidad del dia segun el canal (F.4, criterio conservador r3-K5)."""
    buyables = ["BUYABLE" in (estado or "") for estado in estados]
    return disponibilidad_estricta(buyables, stocks, solo_estado=(canal == "fbm"))


def historia_unidad(
    conn: psycopg.Connection,
    *,
    unidad: puerto.UnidadPrecio,
    canal: str | None,
    hoy: date,
) -> puerto.HistoriaUnidad:
    """Ventas y disponibilidad de ESTA unidad (F.4).

    Ventas atribuidas de `v_precio_venta_unidad`; lo del producto sin
    unidad decidible se suma en `sin_atribuir` (enteros, nunca inventados).
    `dias` trae un `DiaUnidad` por dia observado en `[hoy-75, hoy]`: las
    ventas sin atribuir no abren dia (no son de esta unidad). Trafico de
    `ads_product_metric_observation` (la observacion mas reciente del dia;
    `None` se conserva) y buy box de `spapi_price_observation`.
    """
    platform = unidad.plataforma
    skus = [m.seller_sku for m in unidad.miembros]
    desde = hoy - timedelta(days=75)
    ventas = ventas_unidad(
        conn,
        listing_id=unidad.listing_id,
        producto_ancla=unidad.producto_ancla,
        platform=platform,
        desde=desde,
        hoy=hoy,
    )
    unidades = ventas.dias
    primera = ventas.primera_venta
    sin_atribuir = ventas.sin_atribuir
    cubierto = ventas.ledger_cubierto_hasta
    estados: dict = {}
    stocks: dict = {}
    for codigo in skus:
        for estado, obs in conn.execute(
            "SELECT status, observed_at FROM spapi_listing_estado_observation"
            " WHERE seller_sku = %s AND platform = %s",
            (codigo, platform),
        ).fetchall():
            dia = _dia_utc(obs)
            if desde <= dia <= hoy:
                estados.setdefault(dia, []).append(estado)
        for dia_obs, qty in conn.execute(
            "SELECT metric_date, total_quantity FROM spapi_inventario_observation"
            " WHERE seller_sku = %s AND platform = %s",
            (codigo, platform),
        ).fetchall():
            if desde <= dia_obs <= hoy:
                stocks.setdefault(dia_obs, []).append(qty)
    trafico: dict = {}
    for dia_obs, obs, impresiones, clics in conn.execute(
        "SELECT metric_date, observed_at, impressions, clicks"
        " FROM ads_product_metric_observation"
        " WHERE platform = %s AND advertised_asin = %s AND advertised_sku = ANY(%s)"
        " ORDER BY metric_date, observed_at",
        (platform, unidad.externo, skus),
    ).fetchall():
        if not (desde <= dia_obs <= hoy):
            continue
        previo = trafico.get(dia_obs)
        if previo is None or (obs is not None and obs > previo[0]):
            trafico[dia_obs] = (obs, impresiones, clics)
    buy_box: dict = {}
    for dia_obs, obs, propia in conn.execute(
        "SELECT metric_date, observed_at, buy_box_is_own FROM spapi_price_observation"
        " WHERE asin = %s AND platform = %s"
        " ORDER BY metric_date, observed_at",
        (unidad.externo, platform),
    ).fetchall():
        if not (desde <= dia_obs <= hoy):
            continue
        previo = buy_box.get(dia_obs)
        if previo is None or (obs is not None and obs > previo[0]):
            buy_box[dia_obs] = (obs, propia)
    dias = tuple(
        puerto.DiaUnidad(
            dia=dia,
            unidades=unidades.get(dia, 0),
            disponibilidad=_disponibilidad(
                canal=canal, estados=estados.get(dia, []), stocks=stocks.get(dia, [])
            ),
            buy_box_propia=buy_box[dia][1] if dia in buy_box else None,
            exposiciones=trafico[dia][1] if dia in trafico else None,
            clics=trafico[dia][2] if dia in trafico else None,
        )
        for dia in sorted(set(unidades) | set(estados) | set(stocks) | set(trafico) | set(buy_box))
    )
    return puerto.HistoriaUnidad(
        dias=dias,
        primera_venta=primera,
        ledger_cubierto_hasta=cubierto,
        sin_atribuir=sin_atribuir,
    )


def _cotizacion_de_arbol(
    precio: Importe,
    total: Decimal,
    detalles: tuple[DetalleFee, ...],
    estimada_en,
    codigo: str | None,
) -> puerto.Cotizacion:
    """Arbol de fees -> `puerto.Cotizacion` con los fees partidos.

    La conciliacion (`sum(final_fee) != total`) y el parting
    (`derivar_ref_fijo`) se validan AQUI: quien recibe los fees ya partidos
    (`reglas`, `objetivo`) no vuelve a sumar el arbol.
    """
    if codigo is not None:
        return puerto.Cotizacion(precio=precio, fees=None, estimada_en=None, error_code=codigo)
    if _suma_top(detalles) != total:
        return puerto.Cotizacion(
            precio=precio, fees=None, estimada_en=None, error_code="fee_error:fee_no_concilia"
        )
    try:
        _ref, fijo = derivar_ref_fijo(detalles, total, precio.valor)
    except ErrorObjetivo as exc:
        return puerto.Cotizacion(precio=precio, fees=None, estimada_en=None, error_code=exc.motivo)
    moneda = precio.moneda
    fees = Fees(
        total=Importe(total, moneda),
        variable=Importe(total - fijo, moneda),
        fijo=Importe(fijo, moneda),
        impuesto_pendiente=_tiene_tax(detalles),
        detalle=detalles,
    )
    return puerto.Cotizacion(precio=precio, fees=fees, estimada_en=estimada_en, error_code=None)


class MercadoAmazon:
    """El mercado de `amazon_mx`/`amazon_us` (una clase, la clave decide)."""

    clave: str
    moneda: str
    grupo_tasa = "spapi"
    filtra_catalogo = True

    def __init__(
        self,
        clave: str,
        *,
        lector=None,
        escritor=None,
        fees: ProductFeesClient | None = None,
        limitador=None,
        construir_cuerpo=None,
    ) -> None:
        if clave not in ("amazon_mx", "amazon_us"):
            raise ValueError(f"clave de mercado Amazon invalida: {clave!r}")
        if escritor is not None and escritor.platform != clave:
            raise ValueError(
                f"plataforma_distinta: escritor={escritor.platform} fila={clave}"
                " (el escritor se construye con la platform de la fila)"
            )
        self.clave = clave
        self.moneda = "MXN" if clave == "amazon_mx" else "USD"
        self._lector = lector
        self._escritor = escritor
        self._fees = fees
        self._limitador = limitador
        self._construir_cuerpo = construir_cuerpo
        self._ruta_error: str | None = None

    def _exigir_clientes(self) -> None:
        if self._lector is None:
            raise RuntimeError("mercado sin clientes")

    def validar_destino(self, unidad: puerto.UnidadPrecio) -> None:
        """Todo lo que puede fallar sin red, ANTES del INSERT (r1-A2): SKU
        nulo o vacio (`listing_sin_sku`) y la ruta del PATCH."""
        sku = unidad.miembros[0].seller_sku if unidad.miembros else ""
        if not sku:
            raise PublicacionSinSku("listing_sin_sku: sin seller_sku no hay ruta que escribir")
        seller = self._escritor.seller_id if self._escritor is not None else ""
        validar_patch_listings(construir_ruta_listings(seller, sku), seller, sku)

    def catalogo(self, conn, *, hoy: date) -> tuple[puerto.UnidadActiva, ...]:
        """Activas canonicas con su unidad (o `None` sin fila en listing)."""
        cano = conn.execute(_SQL_CANO, (self.clave,)).fetchall()
        ids = [fila[0] for fila in cano if fila[0] is not None]
        unidades = self.unidades(conn, ids)
        activas: list[puerto.UnidadActiva] = []
        for listing_id, sku, _plat, _asin, _status, observed in cano:
            unidad = unidades.get(listing_id) if listing_id is not None else None
            activas.append(
                puerto.UnidadActiva(
                    unidad=unidad,
                    seller_sku=sku,
                    precio=None,
                    dias_sin_reportar=(hoy - observed.astimezone(UTC).date()).days,
                )
            )
        return tuple(activas)

    def unidades(self, conn, listing_ids: Sequence[int]) -> Mapping[int, puerto.UnidadPrecio]:
        """La unidad de cada listing de Amazon, por lote: un solo miembro
        `(product_id, seller_sku, None)`, como hoy. Un listing que no existe o
        no es de esta plataforma no viene en el mapa.
        """
        ids = list(dict.fromkeys(listing_ids))
        if not ids:
            return {}
        filas = conn.execute(
            "SELECT id, product_id, external_id, seller_sku FROM listing"
            " WHERE id = ANY(%s) AND platform = %s",
            (ids, self.clave),
        ).fetchall()
        return {
            lid: puerto.UnidadPrecio(
                listing_id=lid,
                plataforma=self.clave,
                externo=externo,
                canal=None,
                producto_ancla=product_id,
                miembros=(puerto.Miembro(product_id, sku or "", None),),
            )
            for lid, product_id, externo, sku in filas
        }

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

        Nunca levanta por un dato faltante: el fallo viaja en la Lectura
        (`motivo`/`diagnostico`), como hoy viajaban `EscenarioNoDisponible`
        e `InsumoIncoherente`. La clave de trafico se lee una vez, fuera
        del lote: sin ella no hay senal para nadie y se falla fuerte.
        """
        por_listing = list(dict.fromkeys(u.listing_id for u in unidades))
        trafico_min, racha_por_lid = leer_trafico_min_y_rachas(conn, self.clave, por_listing, hoy)
        pricing_por_asin = {
            fila[0]: fila
            for fila in conn.execute(
                "SELECT DISTINCT ON (asin) asin, own_listing_price, own_listing_currency,"
                " observed_at, buy_box_is_own"
                " FROM spapi_price_observation"
                " WHERE asin = ANY(%s) AND platform = %s AND metric_date = %s"
                " AND own_listing_price IS NOT NULL"
                " ORDER BY asin, observed_at DESC",
                ([u.externo for u in unidades], self.clave, hoy),
            ).fetchall()
        }
        lecturas: dict[int, Lectura] = {}
        for unidad in unidades:
            meta = metas[unidad.listing_id]
            try:
                cuenta = leer_cuenta(conn, unidad.listing_id, as_of=ahora)
            except ValueError as exc:
                lecturas[unidad.listing_id] = lectura_error("escenario_incoherente", str(exc))
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
                    pricing_por_asin=pricing_por_asin,
                    racha_por_lid=racha_por_lid,
                    trafico_min=trafico_min,
                )
            except psycopg.Error:
                raise
            except Exception as exc:
                lecturas[unidad.listing_id] = lectura_error("escenario_incoherente", str(exc))
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
        pricing_por_asin,
        racha_por_lid,
        trafico_min,
    ) -> Lectura:
        """La Lectura de una unidad con cuenta disponible (levanta ValueError)."""
        if cuenta is None:
            return lectura_error("precio_ausente", "sin escenario disponible")
        if isinstance(cuenta, str):
            return lectura_error(cuenta, f"escenario no disponible: {cuenta}")
        fees_escenario: Fees | str
        try:
            _ref, fijo = derivar_ref_fijo(
                cuenta.fee_detalles,
                cuenta.componentes.fees.valor,
                cuenta.precio_cotizado.valor,
            )
            fees_escenario = Fees(
                total=cuenta.componentes.fees,
                variable=Importe(
                    cuenta.componentes.fees.valor - fijo, cuenta.componentes.fees.moneda
                ),
                fijo=Importe(fijo, cuenta.componentes.fees.moneda),
                impuesto_pendiente=_tiene_tax(cuenta.fee_detalles),
            )
        except ErrorObjetivo as exc:
            fees_escenario = exc.motivo
        observacion = None
        buy_box = None
        fila_pricing = pricing_por_asin.get(unidad.externo)
        if fila_pricing is not None:
            observacion = ObservacionPricing(
                precio=Importe(Decimal(fila_pricing[1]), fila_pricing[2]),
                observada_en=fila_pricing[3],
            )
            buy_box = fila_pricing[4]
        historia = historia_unidad(conn, unidad=unidad, canal=cuenta.canal, hoy=hoy)
        return construir_lectura_unidad(
            conn,
            platform=self.clave,
            unidad=unidad,
            meta=meta,
            cuenta=cuenta,
            fees_escenario=fees_escenario,
            observacion=observacion,
            buy_box=buy_box,
            historia=historia,
            hoy=hoy,
            config=config,
            racha_por_lid=racha_por_lid,
            trafico_min=trafico_min,
            ids={
                "escenario_id": cuenta.refs.escenario_id,
                "fee_observation_id": cuenta.refs.fee_observation_id,
                "oferta_observation_id": cuenta.refs.oferta_observation_id,
                # FBA vieja (pre-0054, sin columna): su L salio de la
                # politica por diseno del universo, no es invento.
                "logistica_origen": cuenta.logistica_origen
                if cuenta.logistica_origen is not None or cuenta.canal != "fba"
                else "politica",
                "envio_muestra_id": cuenta.refs.envio_muestra_id,
            },
        )

    def observado(
        self, conn, unidad: puerto.UnidadPrecio, *, despues_de: date, hasta: date
    ) -> ObservacionPricing | None:
        """La observacion propia mas reciente en `(despues_de, hasta]`."""
        fila = conn.execute(
            "SELECT own_listing_price, own_listing_currency, observed_at"
            " FROM spapi_price_observation"
            " WHERE asin = %s AND platform = %s AND metric_date > %s AND metric_date <= %s"
            " AND own_listing_price IS NOT NULL"
            " ORDER BY metric_date DESC, observed_at DESC LIMIT 1",
            (unidad.externo, self.clave, despues_de, hasta),
        ).fetchone()
        if fila is None:
            return None
        return ObservacionPricing(precio=Importe(Decimal(fila[0]), fila[1]), observada_en=fila[2])

    def cotizar(self, conn, oferta, precio: Importe) -> puerto.Cotizacion:
        """Fees reales a `precio` via ProductFees; ya partidos y conciliados."""
        self._exigir_clientes()

        def _reloj_base():
            fila = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0]
            return fila.replace(tzinfo=UTC)

        resultado = cotizar_a_precio(
            self._fees, oferta, precio.valor, observed_at=None, now_utc=_reloj_base
        )
        if resultado.estado != "success":
            return puerto.Cotizacion(
                precio=precio,
                fees=None,
                estimada_en=None,
                error_code=resultado.error_code or "desconocido",
            )
        detalles = tuple(_detalle_fee(nodo) for nodo in (resultado.fee_details or []))
        total = Decimal(resultado.total_fees)
        return _cotizacion_de_arbol(precio, total, detalles, resultado.fees_estimated_at, None)

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
        detalles_q = tuple(_detalle_fee(nodo) for nodo in (detalles or []))
        return cid, _cotizacion_de_arbol(precio, Decimal(total), detalles_q, None, None)

    def precio_vivo(self, unidad: puerto.UnidadPrecio) -> Importe:
        """Precio propio leido ahora (GET de ofertas de Pricing)."""
        self._exigir_clientes()
        vivo = leer_precio_vivo(
            self._lector, platform=self.clave, asin=unidad.externo, limitador=self._limitador
        )
        return Importe(vivo.precio, vivo.moneda)

    def escribir(self, unidad: puerto.UnidadPrecio, precio: Importe) -> puerto.Acuse:
        """PATCH del precio via Listings Items; devuelve el acuse saneado."""
        self._exigir_clientes()
        sku = unidad.miembros[0].seller_sku if unidad.miembros else ""
        if not sku:
            raise PublicacionSinSku("listing_sin_sku: sin seller_sku no hay ruta que escribir")
        arma = self._construir_cuerpo or _precio_write.construir_cuerpo_parche
        try:
            cuerpo = arma(platform=self.clave, sku=sku, precio=precio.valor, moneda=precio.moneda)
        except Exception as exc:
            raise puerto.EscrituraNoDisponible(str(exc)) from exc
        seller = self._escritor.seller_id
        validar_patch_listings(construir_ruta_listings(seller, sku), seller, sku)
        ruta = _ruta_sin_sku(seller)
        self._ruta_error = ruta
        try:
            resp = self._escritor.patch_listing(sku, cuerpo)
        except SpapiAuthError:
            logger.info("escribir %s error=PATCH %s lwa", self.clave, ruta)
            return puerto.Acuse(aceptada=False, codigo=f"PATCH {ruta} lwa", cuerpo={})
        except httpx.HTTPError as exc:
            logger.info("escribir %s error=PATCH %s red (%s)", self.clave, ruta, type(exc).__name__)
            return puerto.Acuse(aceptada=False, codigo=f"PATCH {ruta} red", cuerpo={})
        aceptado, _cuerpo = _estado_aceptado(resp)
        if aceptado:
            return puerto.Acuse(aceptada=True, codigo=None, cuerpo=_ack_saneado(resp))
        return puerto.Acuse(aceptada=False, codigo=f"PATCH {ruta} {resp.status_code}", cuerpo={})

    def cerrar(self) -> None:
        """Cierra clientes si puede (best-effort)."""
        for cliente in (self._lector, self._fees):
            cerrar = getattr(cliente, "close", None)
            if cerrar is None:
                continue
            try:
                cerrar()
            except Exception:
                logger.warning("cerrar cliente de %s fallo", self.clave, exc_info=True)


# `precio_mercados` abre el mercado con `Mercado(clave)`; sin inyeccion de
# clientes la instancia es solo vitrina (ver `_exigir_clientes`).
Mercado = MercadoAmazon
