"""Lectura por lote y guardado de las decisiones por producto (AUTO-03, bloque D).

Adaptador de app/optimizer/productos.py: aqui vive todo el SQL; el resolutor
decide sobre entradas validas.

- lee_entradas_productos (TX2, bajo el snapshot del ciclo): una
  EntradaProducto por producto con un product ad no archivado y listing en la
  plataforma. Margen de v_margen_producto como goals.MedicionMargen. Metricas
  del reporte de productos colapsadas por (asin, sku, fecha) a la ultima
  observacion anterior a la decision (evaluacion_catalogo.colapsa_por_fecha;
  el SQL ya trae solo la ultima para no cargar las ~90 reobservaciones de
  cada fecha) y sumadas por producto solo con advertised_sku =
  listing.seller_sku (S5). Halo en [D-90, max(metric_date)-3] con las fechas
  con fila (S7). Actividad de la pausa en [D-60, D-30) con la cobertura S6:
  una fecha sin fila es cero solo si un written integro de una corrida ok que
  empezo con la fecha ya madura pidio un rango que la contiene (D.7).
- Topologia por ad group: product ads no archivados, incluidos los PAUSED y
  los de estado desconocido, -> listing -> producto. Exclusivo = todos con
  listing del mismo producto. La huella es sha256 de sus anuncios ordenados.
- guarda_evaluaciones (TX3, siempre dentro de un SAVEPOINT): todas las filas
  o ninguna, con el error devuelto y no lanzado.
- resumen_cobertura: la unica funcion de conteo (notes del ciclo y
  tools/auto03_verifica.py).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from app import config_write
from app.ads.reports import PRODUCTOS_CFG
from app.evaluacion_catalogo import (
    ObservacionAds,
    colapsa_por_fecha,
    fecha_madura,
    suma_con_veneno,
)
from app.optimizer import goals as g
from app.optimizer import productos as p
from app.optimizer import windows
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import scrub

# Topologia de product ads. Un anuncio sin estado sincronizado cuenta como no
# archivado: desconocido no vuelve exclusivo a un grupo.
_SQL_ANUNCIOS = """
SELECT pa.id, pa.parent_id, ag.parent_id, pa.listing_id, li.product_id, s.status,
       pa.external_id, pa.first_seen_at
  FROM ad_entity pa
  JOIN ad_entity ag ON ag.id = pa.parent_id AND ag.kind = 'ad_group'
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = pa.id
  LEFT JOIN listing li ON li.id = pa.listing_id AND li.platform = pa.platform
 WHERE pa.platform = %s::platform AND pa.kind = 'product_ad'
   AND s.status IS DISTINCT FROM 'ARCHIVED'
"""

_SQL_ANUNCIOS_GRUPO = """
SELECT pa.id, pa.listing_id, li.product_id
  FROM ad_entity pa
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = pa.id
  LEFT JOIN listing li ON li.id = pa.listing_id AND li.platform = pa.platform
 WHERE pa.parent_id = %s AND pa.kind = 'product_ad'
   AND s.status IS DISTINCT FROM 'ARCHIVED'
"""

_SQL_HOJAS = """
SELECT k.parent_id, k.id
  FROM ad_entity k
  JOIN ad_entity_state s ON s.ad_entity_id = k.id
 WHERE k.platform = %s::platform AND k.kind IN ('keyword', 'product_target')
   AND s.status = 'ENABLED' AND k.parent_id = ANY(%s)
 ORDER BY k.id
"""

_SQL_LISTINGS = """
SELECT product_id, external_id, seller_sku
  FROM listing
 WHERE platform = %s::platform AND product_id = ANY(%s)
"""

_SQL_MARGEN = """
SELECT product_id, margen_neto_pct, cobertura, dias_con_venta, venta_cubierta,
       ledger_fresco_at, moneda, ventana_desde, ventana_hasta
  FROM v_margen_producto
 WHERE platform = %s::platform AND product_id = ANY(%s)
"""

_SQL_METRICAS = """
SELECT DISTINCT ON (advertised_asin, advertised_sku, metric_date)
       advertised_asin, advertised_sku, metric_date, observed_at, clicks, cost,
       purchases30d, sales30d, attributed_sales_same_sku_30d, ad_ids
  FROM ads_product_metric_observation
 WHERE platform = %s::platform AND advertised_asin = ANY(%s)
   AND metric_date >= %s AND metric_date < %s AND observed_at <= %s
 ORDER BY advertised_asin, advertised_sku, metric_date, observed_at DESC
"""

# S6: los written integros del reporte de productos con rango guardado (D.7)
# de corridas ok anteriores a la decision. Un rango NULL no cubre nada.
_SQL_COBERTURA = """
SELECT a.id, a.fecha_desde, a.fecha_hasta, r.started_at
  FROM ads_report_result a
  JOIN ingest_run r ON r.id = a.ingest_run_id
 WHERE a.platform = %s::platform AND a.report_name = %s AND a.status = 'written'
   AND r.ok AND a.cobertura_integra
   AND a.fecha_desde < %s AND a.fecha_hasta >= %s AND r.started_at <= %s
"""

# Claves de efecto de la familia entity_cut en vuelo o con veto vigente: el
# mismo predicado que apply_cola._SQL_CLAVES_BLOQUEADAS. Con ignora_practicas
# (simulacion, H.8) las filas shadow no terminales de pause no bloquean: son
# las que I.7 descarta al encender la pausa. Con excluye_cola (revalidacion de
# I.3) la fila que se revalida no bloquea su propio anuncio.
_SQL_CLAVES_EN_VUELO = """
SELECT ad_entity_id
  FROM apply_queue
 WHERE platform = %(plataforma)s::platform AND familia = 'entity_cut'
   AND ((estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
         AND NOT (%(ignora_practicas)s AND modo = 'shadow' AND kind = 'pause'))
        OR (estado = 'vetoed' AND vence_el > %(ahora)s))
   AND id IS DISTINCT FROM %(excluye_cola)s::bigint
"""

_SQL_IMPULSO_ABIERTO = """
SELECT ad_group_ad_entity_id, product_id
  FROM v_ad_group_impulso_abierto
 WHERE platform = %s::platform
"""

_SQL_REGRESADOS = """
SELECT ad_entity_id, retiro_id, regresado_el
  FROM v_anuncio_regresado
 WHERE platform = %s::platform
"""

_SQL_CIERRA_REACTIVADO = "SELECT cierra_retiro_reactivado(%s)"

_SQL_PRIMER_DATO = """
SELECT min(metric_date) FROM ads_product_metric_observation WHERE platform = %s::platform
"""

_SQL_INSERTA = """
INSERT INTO evaluacion_producto_ciclo
    (cycle_id, platform, product_id, decided_at, config_version_id, politica, moneda,
     gasto, ventas, clics, compras, meta_pct, entrada, resultado, huella_alcance,
     ventana_desde, ventana_hasta)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id
"""


@dataclass(frozen=True)
class _Anuncio:
    ad_entity_id: int
    listing_id: int | None
    product_id: int | None


@dataclass(frozen=True)
class _Cubierta:
    resultado_id: int
    desde: dt.date
    hasta: dt.date
    empezo: dt.datetime


class SinMetricasDeAnuncios(ValueError):
    """El mercado no tiene watermark: ninguna metrica de anuncios que leer."""


@dataclass(frozen=True)
class ResultadoGuardado:
    ids: dict[tuple[str, int], int]
    error: str | None
    retiros_cerrados: tuple[int, ...] = ()


def lee_entradas_productos(
    conn: psycopg.Connection,
    plataforma: str,
    *,
    decidido_el: dt.datetime,
    settings: Mapping,
    goals: tuple[g.Goal | None, Mapping[int, g.Goal]],
    ignora_practicas: bool = False,
    observadas_hasta: dt.datetime | None = None,
    solo_productos: Sequence[int] | None = None,
    excluye_cola: int | None = None,
) -> tuple[p.EntradaProducto, ...]:
    """Las entradas del ciclo. `observadas_hasta` (por omision decidido_el)
    acota las observaciones y las corridas de cobertura: con decidido_el en
    el pasado no se ve el futuro (regla 5). `ignora_practicas` no cuenta como
    clave en vuelo las filas shadow de pause (simulacion, H.8).
    `solo_productos` (revalidacion de I.3) devuelve solo esos productos y lee
    solo sus metricas; la topologia y la exclusividad siguen siendo las de
    toda la plataforma. `excluye_cola` es la fila de apply_queue que se
    revalida: no cuenta como clave en vuelo de su propio anuncio."""
    observadas_hasta = observadas_hasta or decidido_el
    moneda = PLATAFORMAS_MONEDA[plataforma]
    hoy = decidido_el.astimezone(dt.UTC).date()
    watermark = windows.watermark_plataforma(conn, plataforma)
    if watermark is None:
        raise SinMetricasDeAnuncios(
            f"plataforma {plataforma} sin watermark: no hay ventana de halo"
        )
    ventana_halo = p.Ventana(
        hoy - dt.timedelta(days=windows.LOOKBACK_EVIDENCIA),
        windows.fin_ventana_bids(watermark) + dt.timedelta(days=1),
    )
    ventana_pausa = p.ventana_pausa(decidido_el)

    grupos: dict[int, list[_Anuncio]] = defaultdict(list)
    campana_de: dict[int, int] = {}
    habilitados: dict[int, list[tuple[_Anuncio, int, str, dt.datetime | None]]] = defaultdict(list)
    for fila in conn.execute(_SQL_ANUNCIOS, (plataforma,)):
        pa, grupo, campana, listing, producto, estado, externo, alta = fila
        anuncio = _Anuncio(pa, listing, producto)
        grupos[grupo].append(anuncio)
        campana_de[grupo] = campana
        if estado == "ENABLED" and producto is not None:
            habilitados[producto].append((anuncio, grupo, externo, alta))
    productos_ids = sorted(
        {a.product_id for anuncios in grupos.values() for a in anuncios if a.product_id}
    )
    if solo_productos is not None:
        productos_ids = [x for x in productos_ids if x in set(solo_productos)]
    if not productos_ids:
        return ()
    exclusivos = {
        grupo: anuncios[0].product_id
        for grupo, anuncios in grupos.items()
        if len({a.product_id for a in anuncios}) == 1 and anuncios[0].product_id is not None
    }
    hojas: dict[int, list[int]] = defaultdict(list)
    for grupo, hoja in conn.execute(_SQL_HOJAS, (plataforma, list(exclusivos))):
        hojas[grupo].append(hoja)

    claves = claves_por_producto(conn, plataforma, productos_ids)
    margenes = {
        fila[0]: g.MedicionMargen(
            margen_neto_pct=fila[1],
            cobertura=fila[2],
            dias_con_venta=fila[3],
            venta_cubierta=fila[4],
            ledger_fresco_at=fila[5],
            moneda=str(fila[6]) if fila[6] is not None else None,
            ventana_desde=fila[7],
            ventana_hasta=fila[8],
        )
        for fila in conn.execute(_SQL_MARGEN, (plataforma, productos_ids))
    }
    observaciones, ad_ids = _lee_observaciones(
        conn,
        plataforma,
        asins=sorted({asin for lista in claves.values() for asin, _ in lista}),
        desde=min(ventana_halo.desde, ventana_pausa.desde),
        hasta=max(ventana_halo.hasta_exclusivo, ventana_pausa.hasta_exclusivo),
        decidido_el=observadas_hasta,
    )
    cubiertas = [
        _Cubierta(*fila)
        for fila in conn.execute(
            _SQL_COBERTURA,
            (
                plataforma,
                PRODUCTOS_CFG["nombre"],
                ventana_pausa.hasta_exclusivo,
                ventana_pausa.desde,
                observadas_hasta,
            ),
        )
    ]
    alcance = _Alcance.lee(
        conn,
        plataforma,
        decidido_el=decidido_el,
        ignora_practicas=ignora_practicas,
        excluye_cola=excluye_cola,
        goals=goals,
        campana_de=campana_de,
        grupos=grupos,
        habilitados=habilitados,
        ad_ids=ad_ids,
    )
    fraccion_valor = g.fraccion_desde_settings(settings, plataforma)
    fraccion: Decimal | p.SinDato = (
        fraccion_valor if fraccion_valor is not None else p.SinDato(("sin_fraccion",))
    )
    limites = p.limites_pausa_desde_settings(settings, plataforma)

    entradas = []
    for producto in productos_ids:
        mismo_sku, impedimentos = _filas_del_producto(claves[producto], observaciones)
        dias_halo, faltantes = _dias_halo(ventana_halo, mismo_sku, moneda)
        if faltantes:
            impedimentos.append("ads_con_metrica_ausente")
        grupos_aptos = tuple(
            p.GrupoAptoParaMeta(
                grupo_id=grupo,
                campana_id=campana_de[grupo],
                hojas=tuple(hojas.get(grupo, ())),
                ventana_evidencia_hojas=ventana_halo,
                referencias_identidad_y_halo=tuple(
                    f"product_ad:{a.ad_entity_id}:listing:{a.listing_id}"
                    for a in sorted(grupos[grupo], key=lambda a: a.ad_entity_id)
                ),
                huella_alcance=_huella(grupos[grupo]),
            )
            for grupo, dueno in sorted(exclusivos.items())
            if dueno == producto
        )
        anuncios, excluidos, externos = alcance.del_producto(producto, claves[producto])
        actividad = _actividad(ventana_pausa, mismo_sku, cubiertas, moneda)
        entradas.append(
            p.EntradaProducto(
                producto=p.Producto(plataforma, producto),
                decidido_el=decidido_el,
                margen=margenes.get(producto, p.SinDato(("sin_margen",))),
                fraccion=fraccion,
                ventana_halo=ventana_halo,
                dias_halo=dias_halo,
                actividad_pausa=actividad,
                anuncios=anuncios,
                grupos_aptos_para_meta=grupos_aptos,
                impedimentos=tuple(dict.fromkeys(impedimentos)),
                limites_pausa=limites,
                excluidos=excluidos,
                bloqueos_pausa=(
                    ("impulso_abierto",) if producto in alcance.productos_impulso else ()
                ),
                gasto_actuable=(
                    _gasto_actuable(
                        claves[producto], observaciones, ad_ids, externos, ventana_pausa, moneda
                    )
                    if isinstance(actividad, p.AdsCompletos)
                    else None
                ),
            )
        )
    return tuple(entradas)


@dataclass(frozen=True)
class _Alcance:
    """Lo que decide si un product ad ENABLED del producto esta al alcance de
    la pausa (S4, S17), leido una vez por lectura."""

    decidido_el: dt.datetime
    inicio_ventana: dt.datetime
    goals: tuple[g.Goal | None, Mapping[int, g.Goal]]
    campana_de: Mapping[int, int]
    grupos: Mapping[int, list[_Anuncio]]
    habilitados: Mapping[int, list[tuple[_Anuncio, int, str, dt.datetime | None]]]
    ids_por_clave: Mapping[tuple[str, str], set[str]]
    en_vuelo: frozenset[int]
    grupos_impulso: frozenset[int]
    productos_impulso: frozenset[int]
    regresados: Mapping[int, tuple[int, dt.datetime | None]]

    @classmethod
    def lee(
        cls,
        conn: psycopg.Connection,
        plataforma: str,
        *,
        decidido_el: dt.datetime,
        ignora_practicas: bool,
        excluye_cola: int | None,
        goals: tuple[g.Goal | None, Mapping[int, g.Goal]],
        campana_de: Mapping[int, int],
        grupos: Mapping[int, list[_Anuncio]],
        habilitados: Mapping[int, list[tuple[_Anuncio, int, str, dt.datetime | None]]],
        ad_ids: Mapping[tuple[str, str, dt.date], frozenset[str]],
    ) -> _Alcance:
        ventana = p.ventana_pausa(decidido_el)
        ids_por_clave: dict[tuple[str, str], set[str]] = defaultdict(set)
        for (asin, sku, fecha), ids in ad_ids.items():
            if ventana.contiene(fecha):
                ids_por_clave[(asin, sku)].update(ids)
        impulso = conn.execute(_SQL_IMPULSO_ABIERTO, (plataforma,)).fetchall()
        en_vuelo = conn.execute(
            _SQL_CLAVES_EN_VUELO,
            {
                "plataforma": plataforma,
                "ignora_practicas": ignora_practicas,
                "ahora": decidido_el,
                "excluye_cola": excluye_cola,
            },
        ).fetchall()
        return cls(
            decidido_el=decidido_el,
            inicio_ventana=dt.datetime.combine(ventana.desde, dt.time.min, dt.UTC),
            goals=goals,
            campana_de=campana_de,
            grupos=grupos,
            habilitados=habilitados,
            ids_por_clave=ids_por_clave,
            en_vuelo=frozenset(fila[0] for fila in en_vuelo),
            grupos_impulso=frozenset(fila[0] for fila in impulso),
            productos_impulso=frozenset(fila[1] for fila in impulso),
            regresados={
                fila[0]: (fila[1], fila[2]) for fila in conn.execute(_SQL_REGRESADOS, (plataforma,))
            },
        )

    def _motivo_fuera(
        self, anuncio: _Anuncio, grupo: int, externo: str, alta, en_ventana: set[str]
    ) -> str | None:
        """None si el anuncio es elegible. El regreso va primero: un
        reactivado a mano lleva su retiro para que TX3 lo cierre."""
        regreso = self.regresados.get(anuncio.ad_entity_id)
        if regreso is not None and (
            regreso[1] is None or self.decidido_el - regreso[1] < p.GRACIA_REGRESADO
        ):
            return "regresado_por_ti"
        goal_plataforma, por_campana = self.goals
        goal = g.resuelve_goal(por_campana.get(self.campana_de[grupo]), goal_plataforma)
        if goal is None or not goal.enabled or goal.mode != "live":
            return "goal_no_live"
        if alta is None or alta >= self.inicio_ventana:
            return "alta_en_ventana"
        if externo not in en_ventana:
            return "sin_actividad_en_ventana"
        if grupo in self.grupos_impulso:
            return "impulso_abierto"
        if anuncio.ad_entity_id in self.en_vuelo:
            return "clave_en_vuelo"
        return None

    def del_producto(
        self, producto: int, claves: Sequence[tuple[str, str | None]]
    ) -> tuple[tuple[p.AnuncioElegible, ...], tuple[p.AnuncioExcluido, ...], set[str]]:
        """Los anuncios elegibles, los excluidos con su motivo y los adId
        externos de los elegibles."""
        en_ventana: set[str] = set().union(*(self.ids_por_clave.get(c, ()) for c in claves))
        elegibles = []
        excluidos = []
        externos: set[str] = set()
        for anuncio, grupo, externo, alta in self.habilitados.get(producto, ()):
            motivo = self._motivo_fuera(anuncio, grupo, externo, alta, en_ventana)
            if motivo is None:
                externos.add(externo)
                elegibles.append(
                    p.AnuncioElegible(
                        ad_entity_id=anuncio.ad_entity_id,
                        listing_id=anuncio.listing_id,
                        grupo_id=grupo,
                        huella_alcance=_huella(self.grupos[grupo]),
                    )
                )
                continue
            regreso = self.regresados.get(anuncio.ad_entity_id)
            reactivado = motivo == "regresado_por_ti" and regreso is not None and regreso[1] is None
            excluidos.append(
                p.AnuncioExcluido(anuncio.ad_entity_id, motivo, regreso[0] if reactivado else None)
            )
        return (
            tuple(sorted(elegibles, key=lambda a: a.ad_entity_id)),
            tuple(sorted(excluidos, key=lambda a: a.ad_entity_id)),
            externos,
        )


def _gasto_actuable(
    claves: Sequence[tuple[str, str | None]],
    observaciones: Mapping[tuple[str, str], Mapping[dt.date, ObservacionAds]],
    ad_ids: Mapping[tuple[str, str, dt.date], frozenset[str]],
    externos: set[str],
    ventana: p.Ventana,
    moneda: str,
) -> p.Dinero:
    """S4: el gasto de las filas de la ventana de la pausa (mismo SKU) cuyos
    ad_ids son todos anuncios elegibles. Una fila con otro anuncio o sin
    ad_ids no es gasto sobre el que Orbit pueda actuar: pertenencia, nunca
    reparto. Solo se llama con la actividad completa (sin costos ausentes)."""
    total = Decimal(0)
    for asin, sku in claves:
        for fecha, obs in observaciones.get((asin, sku), {}).items():
            ids = ad_ids.get((asin, sku, fecha))
            if ventana.contiene(fecha) and ids and ids <= externos and obs.cost is not None:
                total += obs.cost
    return p.Dinero(total, moneda)


def claves_por_producto(
    conn: psycopg.Connection, plataforma: str, productos_ids: Sequence[int]
) -> dict[int, list[tuple[str, str | None]]]:
    """La vinculacion S5 de cada producto: (ASIN, seller_sku) de sus listings."""
    claves: dict[int, list[tuple[str, str | None]]] = defaultdict(list)
    for producto, asin, seller_sku in conn.execute(
        _SQL_LISTINGS, (plataforma, list(productos_ids))
    ):
        claves[producto].append((asin, seller_sku))
    return claves


def _lee_observaciones(
    conn: psycopg.Connection,
    plataforma: str,
    *,
    asins: list[str],
    desde: dt.date,
    hasta: dt.date,
    decidido_el: dt.datetime,
) -> tuple[
    dict[tuple[str, str], dict[dt.date, ObservacionAds]],
    dict[tuple[str, str, dt.date], frozenset[str]],
]:
    """Las observaciones colapsadas por clave y fecha, y los ad_ids de cada
    fila colapsada (pertenencia S4; jamas insumo para repartir)."""
    crudas: dict[tuple[str, str], list[ObservacionAds]] = defaultdict(list)
    ad_ids: dict[tuple[str, str, dt.date], frozenset[str]] = {}
    for fila in conn.execute(_SQL_METRICAS, (plataforma, asins, desde, hasta, decidido_el)):
        ad_ids[(fila[0], fila[1], fila[2])] = frozenset(str(i) for i in fila[9] or ())
        crudas[(fila[0], fila[1])].append(
            ObservacionAds(
                metric_date=fila[2],
                observed_at=fila[3],
                clicks=fila[4],
                cost=fila[5],
                purchases30d=fila[6],
                sales30d=fila[7],
                attributed_sales_same_sku30d=fila[8],
            )
        )
    return {clave: colapsa_por_fecha(filas) for clave, filas in crudas.items()}, ad_ids


def _filas_del_producto(
    claves: Sequence[tuple[str, str | None]],
    observaciones: Mapping[tuple[str, str], Mapping[dt.date, ObservacionAds]],
) -> tuple[dict[dt.date, list[ObservacionAds]], list[str]]:
    """S5: las fechas del producto con el SKU de su listing, por fecha. Otro
    SKU del mismo ASIN con gasto (o gasto ausente) es un impedimento."""
    seller_por_asin = dict(claves)
    por_fecha: dict[dt.date, list[ObservacionAds]] = defaultdict(list)
    impedimentos: list[str] = []
    for (asin, sku), filas in observaciones.items():
        if asin not in seller_por_asin:
            continue
        if sku == seller_por_asin[asin]:
            for fecha, obs in filas.items():
                por_fecha[fecha].append(obs)
        elif any(obs.cost is None or obs.cost > 0 for obs in filas.values()):
            impedimentos.append("sku_distinto_en_asin")
    return por_fecha, impedimentos


def _dias_halo(
    ventana: p.Ventana, por_fecha: Mapping[dt.date, list[ObservacionAds]], moneda: str
) -> tuple[tuple[p.DiaProducto, ...], bool]:
    """S7: solo las fechas con fila. Devuelve tambien si alguna fecha trajo
    una metrica ausente (la suma con veneno no la puede publicar)."""
    dias = []
    faltantes = False
    for fecha in sorted(f for f in por_fecha if ventana.contiene(f)):
        suma = _suma_dia(por_fecha[fecha])
        if suma is None:
            faltantes = True
            continue
        gasto, clics, compras, ventas, mismo_sku = suma
        dias.append(
            p.DiaProducto(
                fecha=fecha,
                gasto=p.Dinero(gasto, moneda),
                clics=clics,
                compras=compras,
                ventas=p.Dinero(ventas, moneda),
                ventas_mismo_sku=p.Dinero(mismo_sku, moneda),
            )
        )
    return tuple(dias), faltantes


def _suma_dia(
    filas: Iterable[ObservacionAds],
) -> tuple[Decimal, int, Decimal, Decimal, Decimal] | None:
    filas = list(filas)
    gasto = suma_con_veneno(f.cost for f in filas)
    clics = suma_con_veneno(Decimal(f.clicks) if f.clicks is not None else None for f in filas)
    compras = suma_con_veneno(f.purchases30d for f in filas)
    ventas = suma_con_veneno(f.sales30d for f in filas)
    mismo_sku = suma_con_veneno(f.attributed_sales_same_sku30d for f in filas)
    if None in (gasto, clics, compras, ventas, mismo_sku):
        return None
    return gasto, int(clics), compras, ventas, mismo_sku


def _actividad(
    ventana: p.Ventana,
    por_fecha: Mapping[dt.date, list[ObservacionAds]],
    cubiertas: Sequence[_Cubierta],
    moneda: str,
) -> p.AdsCompletos | p.SinDato:
    fechas = [
        ventana.desde + dt.timedelta(days=n)
        for n in range((ventana.hasta_exclusivo - ventana.desde).days)
    ]
    if not any(f in por_fecha for f in fechas):
        return p.SinDato(("sin_filas_en_ventana",))
    motivos: list[str] = []
    referencias: list[str] = []
    filas: list[ObservacionAds] = []
    for fecha in fechas:
        obs = por_fecha.get(fecha)
        if obs:
            if all(fecha_madura(fecha, o.observed_at) for o in obs):
                filas.extend(obs)
                referencias.append(f"{fecha.isoformat()}:fila")
            else:
                motivos.append("fecha_inmadura")
            continue
        cubierta = next(
            (c for c in cubiertas if c.desde <= fecha <= c.hasta and fecha_madura(fecha, c.empezo)),
            None,
        )
        if cubierta is None:
            motivos.append("fecha_desconocida")
        else:
            referencias.append(
                f"{fecha.isoformat()}:cero:ads_report_result={cubierta.resultado_id}"
            )
    if motivos:
        return p.SinDato(tuple(dict.fromkeys(motivos)))
    suma = _suma_dia(filas)
    if suma is None:
        return p.SinDato(("ads_con_metrica_ausente",))
    gasto, clics, compras, ventas, mismo_sku = suma
    return p.AdsCompletos(
        ventana=ventana,
        gasto=p.Dinero(gasto, moneda),
        clics=clics,
        compras_total=compras,
        ventas_total=p.Dinero(ventas, moneda),
        ventas_mismo_sku=p.Dinero(mismo_sku, moneda),
        referencias_por_fecha=tuple(referencias),
    )


def _huella(anuncios: Iterable[_Anuncio]) -> str:
    lineas = sorted(
        f"{a.ad_entity_id}:{a.listing_id if a.listing_id is not None else '-'}:"
        f"{a.product_id if a.product_id is not None else '-'}"
        for a in anuncios
    )
    return "sha256:" + hashlib.sha256("\n".join(lineas).encode()).hexdigest()


def huella_grupo(conn: psycopg.Connection, ad_group_id: int) -> str:
    """La huella vigente de un ad group, con la misma regla que la entrada."""
    return _huella(_Anuncio(*fila) for fila in conn.execute(_SQL_ANUNCIOS_GRUPO, (ad_group_id,)))


def huella_producto(entrada: p.EntradaProducto) -> str:
    """Huella del alcance del producto: las de sus grupos aptos, ordenadas."""
    lineas = sorted(
        f"{grupo.grupo_id}:{grupo.huella_alcance}" for grupo in entrada.grupos_aptos_para_meta
    )
    return "sha256:" + hashlib.sha256("\n".join(lineas).encode()).hexdigest()


def resultado_json(evaluacion: p.EvaluacionProducto, sombra_hojas: Sequence[dict]) -> dict:
    """El resultado persistido: la evaluacion sin su entrada (va en su propia
    columna), las pausas propuestas y la sombra por hoja del ciclo."""
    datos = evaluacion.como_json()
    del datos["entrada"]
    return {**datos, "sombra_hojas": list(sombra_hojas)}


def evaluacion_de_fila(entrada: Mapping, resultado: Mapping) -> p.EvaluacionProducto:
    return p.EvaluacionProducto.desde_json({**resultado, "entrada": entrada})


def guarda_evaluaciones(
    conn: psycopg.Connection,
    cycle_id: int,
    evaluaciones: Sequence[p.EvaluacionProducto],
    *,
    config_version_id: int | None,
    sombra_hojas: Mapping[int, Sequence[dict]],
    cierra_reactivados: bool = False,
) -> ResultadoGuardado:
    """TX3, dentro de su propio SAVEPOINT: todas las evaluaciones o ninguna.
    Un error queda en el resultado para las notas del ciclo, no se lanza.
    Con `cierra_reactivados` (la pausa encendida) cierra ademas el retiro de
    cada anuncio reactivado a mano en Amazon que la lectura detecto (S17): su
    gracia corre desde este ciclo; con la llave del retiro ocupada lo intenta
    el ciclo siguiente. Con la pausa apagada no toca el ledger de retiro."""
    try:
        with conn.transaction():
            ids: dict[tuple[str, int], int] = {}
            cerrados: list[int] = []
            for ev in evaluaciones:
                entrada = ev.entrada
                producto = entrada.producto
                actividad = entrada.actividad_pausa
                completa = isinstance(actividad, p.AdsCompletos)
                ventana = p.ventana_pausa(entrada.decidido_el)
                meta = ev.meta.porcentaje if isinstance(ev.meta, p.MetaReferencia) else None
                fila = conn.execute(
                    _SQL_INSERTA,
                    (
                        cycle_id,
                        producto.plataforma,
                        producto.product_id,
                        entrada.decidido_el,
                        config_version_id,
                        ev.politica,
                        PLATAFORMAS_MONEDA[producto.plataforma],
                        actividad.gasto.valor if completa else None,
                        actividad.ventas_total.valor if completa else None,
                        actividad.clics if completa else None,
                        actividad.compras_total if completa else None,
                        meta,
                        Jsonb(entrada.como_json()),
                        Jsonb(resultado_json(ev, sombra_hojas.get(producto.product_id, ()))),
                        huella_producto(entrada),
                        ventana.desde,
                        ventana.hasta_exclusivo,
                    ),
                ).fetchone()
                ids[(producto.plataforma, producto.product_id)] = fila[0]
                for excluido in entrada.excluidos if cierra_reactivados else ():
                    if excluido.retiro_id is None:
                        continue
                    cerrado = conn.execute(
                        _SQL_CIERRA_REACTIVADO, (excluido.retiro_id,)
                    ).fetchone()[0]
                    if cerrado is not None:
                        cerrados.append(excluido.retiro_id)
    except Exception as exc:  # noqa: BLE001 - el camino de productos jamas tumba el ciclo
        return ResultadoGuardado({}, scrub(str(exc)) or type(exc).__name__)
    return ResultadoGuardado(ids, None, tuple(cerrados))


def resumen_cobertura(
    plataforma: str,
    evaluaciones: Sequence[p.EvaluacionProducto],
    goals: tuple[g.Goal | None, Mapping[int, g.Goal]],
) -> dict:
    """La unica funcion de conteo: notes.productos del ciclo, la herramienta
    y la medicion. Una hoja esta pisada si el goal resuelto de su campana
    trae target (S1); es efectiva si su producto tiene meta y su goal la deja
    entrar (enabled, modo distinto de off, sin target). Un candidato a pausa
    cumple productos.cumple_limites_pausa; sus anuncios afectables son los
    AnuncioElegible de su entrada. Las propuestas de pausa son las de la
    regla completa (H.2): una por anuncio elegible de un producto sin ventas,
    sin impulso abierto ni impedimento de identidad."""
    goal_plataforma, por_campana = goals
    grupos_aptos = hojas = pisadas = efectivas = con_meta = completas = 0
    candidatos = afectables = elegibles = con_pausa = propuestas = 0
    abstenciones: Counter[str] = Counter()
    motivos_pausa: Counter[str] = Counter()
    sin_pausa: Counter[str] = Counter()
    excluidos: Counter[str] = Counter()
    for ev in evaluaciones:
        tiene_meta = isinstance(ev.meta, p.MetaReferencia)
        con_meta += tiene_meta
        if isinstance(ev.meta, p.SinDato):
            abstenciones.update(ev.meta.motivos)
        elegibles += len(ev.entrada.anuncios)
        excluidos.update(x.motivo for x in ev.entrada.excluidos)
        if p.cumple_limites_pausa(ev.entrada):
            candidatos += 1
            afectables += len(ev.entrada.anuncios)
        con_pausa += bool(ev.pausas)
        propuestas += len(ev.pausas)
        if p.cumple_limites_pausa(ev.entrada) and not ev.pausas:
            sin_pausa.update(ev.motivos_pausa)
        actividad = ev.entrada.actividad_pausa
        if isinstance(actividad, p.AdsCompletos):
            completas += 1
        else:
            motivos_pausa.update(actividad.motivos)
        for grupo in ev.entrada.grupos_aptos_para_meta:
            grupos_aptos += 1
            hojas += len(grupo.hojas)
            goal = g.resuelve_goal(por_campana.get(grupo.campana_id), goal_plataforma)
            if goal is not None and goal.target_acos_pct is not None:
                pisadas += len(grupo.hojas)
            elif tiene_meta and goal is not None and goal.enabled and goal.mode != "off":
                efectivas += len(grupo.hojas)
    return {
        "plataforma": plataforma,
        "politica": p.POLITICA_PRODUCTO,
        "productos_evaluados": len(evaluaciones),
        "con_meta": con_meta,
        "grupos_aptos": grupos_aptos,
        "hojas_bajo_grupo_apto": hojas,
        "hojas_pisadas_por_goal_con_target": pisadas,
        "hojas_efectivas": efectivas,
        "ventana_pausa_completa": completas,
        "candidatos_pausa": candidatos,
        "anuncios_afectables": afectables,
        "anuncios_elegibles": elegibles,
        "alcance_anuncios": "S4 completo: impulso abierto, claves en vuelo y regresados fuera",
        "productos_con_pausa": con_pausa,
        "propuestas_pausa": propuestas,
        "candidatos_sin_propuesta": dict(sorted(sin_pausa.items())),
        "anuncios_excluidos": dict(sorted(excluidos.items())),
        "abstenciones": dict(sorted(abstenciones.items())),
        "motivos_ventana_pausa": dict(sorted(motivos_pausa.items())),
    }


def limites_de_simulacion(
    settings: Mapping, plataforma: str, *, clics: str | None, gasto: str | None
) -> p.LimitesPausa:
    """Los limites vigentes con los que pida la pantalla, leidos con el mismo
    lector (C.7) y validados con los mismos pisos que el guardado: un limite
    invalido lanza config_write.SettingsInvalido (422 en la ruta)."""
    crudos = dict(settings)
    if clics is not None:
        crudos[p.clave_limite("clics", plataforma)] = clics
    if gasto is not None:
        crudos[p.clave_limite("gasto", plataforma)] = gasto
    try:
        limites = p.limites_pausa_desde_settings(crudos, plataforma)
    except ValueError as exc:
        raise config_write.SettingsInvalido(str(exc)) from exc
    config_write.valida_limites_pausa(plataforma, limites)
    return limites


def simula_pausa(
    conn: psycopg.Connection,
    plataforma: str,
    *,
    ahora: dt.datetime,
    settings: Mapping,
    goals: tuple[g.Goal | None, Mapping[int, g.Goal]],
    limites: p.LimitesPausa,
) -> p.SimulacionPausa:
    """H.8, solo lectura: la regla de la pausa con `limites` sobre las
    entradas de hoy (sin contar las practicas abiertas como clave en vuelo:
    es el alcance que tendria en vivo) y sobre las de hace 30 dias, leidas
    sin observaciones posteriores. No escribe en la cola."""
    antes = ahora - dt.timedelta(days=p.DIAS_PAUSA_DESDE - p.DIAS_PAUSA_HASTA)
    comunes = {"settings": settings, "goals": goals, "ignora_practicas": True}
    hoy = lee_entradas_productos(conn, plataforma, decidido_el=ahora, **comunes)
    previas = lee_entradas_productos(
        conn, plataforma, decidido_el=antes, observadas_hasta=antes, **comunes
    )
    primer_dato = conn.execute(_SQL_PRIMER_DATO, (plataforma,)).fetchone()[0]
    return p.simula_pausa(hoy, previas, limites, decidido_el=ahora, primer_dato=primer_dato)
