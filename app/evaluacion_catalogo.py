"""Evaluacion de publicaciones para el catalogo (ORBIT 19 B.4, politica 0.4).

Modulo PURO: sin IO de red ni DB dentro de la logica de etiquetas y ratios.
El adaptador de API (app/fabrica_web.py) consulta la base y llama aqui.

Contrato (docs/evidencia/orbit-19/0.4/politica-comparacion.md, cerrada):

- Ratios SIEMPRE desde sumas de filas por (platform, advertised_asin,
  advertised_sku), nunca promedio de porcentajes (§4). No se reparten
  agregados de campana: las filas del mismo ASIN en varias campanas se SUMAN
  hacia la clave (§2).
- Ausencia = None, jamas 0 (regla 3). El cero escrito en la fila es un cero
  OBSERVADO, no ausencia.
- Etiqueta Ads con precedencia cerrada (§4):
    1. Reporte faltante / cobertura no demostrada / ASIN ausente del gzip
       solo-actividad -> Sin datos. COMPLETED no basta.
    2. Por probar SOLO con cobertura demostrada: hoy NO existe esa
       demostracion, esta etiqueta NO se asigna nunca (regla cerrada 0.4).
    3. Gasto > 0 y sales30d = 0 observado -> Gasto sin ventas, ACoS null.
    4. sales30d > 0 -> ACoS = 100 * suma(cost) / suma(sales30d). Con objetivo
       explicito: <= objetivo -> Dentro (igualdad cuenta Dentro); > objetivo
       -> Por encima. Sin objetivo: solo el numero (D2). Evidencia inmadura
       -> provisional.
    5. Resto: datos sin etiqueta.
- Madurez (§2): una fila de metric_date D es madura para columnas 30d solo
  si existe observacion con observed_at (UTC) >= D + 30 dias. El paso del
  calendario no basta.
- Halo 30d solo si sales30d y attributedSalesSameSku30d AMBAS presentes
  (§1); no se mezcla con halo 7d.
- Ninguna etiqueta bloquea la seleccion de publicaciones (AC3/AC10).
- Ordenes (§7): margen observado, ventas totales, Revenue Ads, gasto, ACoS,
  CPC, CVR, compras; asc/desc; NULL al final en ambas direcciones; desempate
  estable listing_id; MX/US no se mezclan; muestra 1-29 fechas NO entra al
  sort (D4: v_economia_producto solo expone margen maduro, la muestra va
  aparte).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # el dataclass es solo anotacion: el modulo queda PURO en
    # runtime (sin psycopg via app.economia_observada, que si es adaptador).
    from app.economia_observada import EconomiaProducto

# Etiquetas Ads (precedencia §4). No existe constante "por_probar": esa
# etiqueta no se asigna hasta cobertura demostrada del universo de ASINs
# anunciados, demostracion que hoy no existe (0.4 §4.2, regla cerrada).
ETIQUETA_SIN_DATOS = "sin_datos"
ETIQUETA_GASTO_SIN_VENTAS = "gasto_sin_ventas"
ETIQUETA_DENTRO_DEL_OBJETIVO = "dentro_del_objetivo"
ETIQUETA_POR_ENCIMA_DEL_OBJETIVO = "por_encima_del_objetivo"

# Metricas de orden soportadas (§7 D1).
METRICAS_ORDEN = frozenset(
    {
        "margen_observado",
        "ventas_totales",
        "revenue_ads",
        "gasto",
        "acos",
        "cpc",
        "cvr",
        "compras",
    }
)


@dataclass(frozen=True)
class ObservacionAds:
    """Una fila append-only de ads_product_metric_observation del grano
    (platform, advertised_asin, advertised_sku). Columnas None = ausencia."""

    metric_date: dt.date
    observed_at: dt.datetime  # UTC
    clicks: int | None = None
    cost: Decimal | None = None
    purchases30d: Decimal | None = None
    sales30d: Decimal | None = None
    attributed_sales_same_sku30d: Decimal | None = None


@dataclass(frozen=True)
class EvaluacionAds:
    """Evaluacion Ads de UNA clave en UNA ventana.

    Toda suma/ratio es None cuando el dato falta (regla 3, jamas 0).
    `muestra` = fechas con fila dentro de la ventana (§2).
    """

    etiqueta: str | None
    maduro: bool
    provisional: bool
    muestra: int
    moneda: str | None = None
    cost: Decimal | None = None
    clicks: int | None = None
    sales30d: Decimal | None = None
    purchases30d: Decimal | None = None
    promoted30d: Decimal | None = None
    halo30d: Decimal | None = None
    acos_pct: Decimal | None = None
    cpc: Decimal | None = None
    cvr_pct: Decimal | None = None


def _suma(valores: Iterable[Decimal | None]) -> Decimal | None:
    """Suma con envenenamiento de ausencias (regla 3; 0.4 §4.1): si ALGUNA
    fila de la ventana trae la metrica ausente, el subtotal es parcial y se
    reporta como desconocido (None), no como la suma de las presentes."""
    total = Decimal("0")
    hay = False
    for valor in valores:
        if valor is None:
            return None
        hay = True
        total += valor
    return total if hay else None


def _fecha_madura(fecha: dt.date, observed_at: dt.datetime) -> bool:
    """§2: fecha D es madura para columnas 30d solo si SU observacion (la mas
    reciente de esa fecha tras el colapso) tiene observed_at >= D + 30 dias.
    El maximo global de la ventana NO vale: otra fecha observada tarde no
    madura a esta (hallazgo cross-review codex 2026-09-07)."""
    limite = dt.datetime.combine(fecha + dt.timedelta(days=30), dt.time.min, dt.UTC)
    return observed_at >= limite


def evaluar_ads(
    filas: Sequence[ObservacionAds],
    ventana: tuple[dt.date, dt.date],
    objetivo: Decimal | None = None,
    moneda: str | None = None,
) -> EvaluacionAds:
    """Etiqueta y ratios de una clave en una ventana (precedencia §4).

    `objetivo` es el del grupo en preparacion (D2: manual_lanzamiento o
    margen_medido); None => ACoS sin etiqueta dentro/fuera, solo el numero.
    """
    desde, hasta = ventana
    en_ventana = [f for f in filas if desde <= f.metric_date <= hasta]

    # (1) Reporte faltante / ASIN ausente del gzip solo-actividad: sin filas
    # en la ventana => Sin datos. Jamas "por_probar" (cobertura no demostrada).
    if not en_ventana:
        return EvaluacionAds(
            etiqueta=ETIQUETA_SIN_DATOS, maduro=False, provisional=False, muestra=0
        )

    # Colapso append-only: de cada fecha cuenta SOLO la observacion mas
    # reciente (max observed_at); el cron D-31..D-1 re-observa cada fecha ~31
    # veces y sin colapso el gasto/ventas se inflaria (hallazgo B.R).
    por_fecha: dict[dt.date, ObservacionAds] = {}
    for f in en_ventana:
        actual = por_fecha.get(f.metric_date)
        if actual is None or f.observed_at > actual.observed_at:
            por_fecha[f.metric_date] = f
    en_ventana = list(por_fecha.values())

    # Madurez POR FECHA con la observacion colapsada de cada fecha (§2): la
    # fecha observada solo en D+1 no madura porque otra fecha se observara
    # >= D+30 (hallazgo cross-review: el maximo global mentia).
    maduro = all(_fecha_madura(f.metric_date, f.observed_at) for f in en_ventana)

    cost = _suma(f.cost for f in en_ventana)
    clicks_m = _suma(Decimal(f.clicks) if f.clicks is not None else None for f in en_ventana)
    clicks = clicks_m
    sales = _suma(f.sales30d for f in en_ventana)
    purchases = _suma(f.purchases30d for f in en_ventana)
    promoted = _suma(f.attributed_sales_same_sku30d for f in en_ventana)

    # Halo 30d SOLO si ambas presentes (§1); no se resta con una falta.
    halo = sales - promoted if (sales is not None and promoted is not None) else None

    # CPC = suma(cost)/suma(clicks) si clicks > 0 (§4); CVR con la misma guarda.
    cpc = cost / clicks if (cost is not None and clicks is not None and clicks > 0) else None
    cvr = (
        Decimal("100") * purchases / clicks
        if (purchases is not None and clicks is not None and clicks > 0)
        else None
    )

    acos: Decimal | None = None
    etiqueta: str | None = None
    if cost is not None and cost > 0 and sales is not None and sales == 0:
        # (3) Cero observado en la fila es observado, no ausencia.
        etiqueta = ETIQUETA_GASTO_SIN_VENTAS
    elif sales is not None and sales > 0 and cost is not None:
        # (4) Ratio desde sumas. Igualdad cuenta Dentro.
        acos = Decimal("100") * cost / sales
        if objetivo is not None:
            etiqueta = (
                ETIQUETA_DENTRO_DEL_OBJETIVO
                if acos <= objetivo
                else ETIQUETA_POR_ENCIMA_DEL_OBJETIVO
            )
    # (5) Resto (incluye ventas ausentes o cost ausente): sin etiqueta.

    return EvaluacionAds(
        etiqueta=etiqueta,
        maduro=maduro,
        provisional=not maduro,
        muestra=len(en_ventana),
        moneda=moneda,
        cost=cost,
        clicks=clicks,
        sales30d=sales,
        purchases30d=purchases,
        promoted30d=promoted,
        halo30d=halo,
        acos_pct=acos,
        cpc=cpc,
        cvr_pct=cvr,
    )


def objetivo_comparacion(targets: Iterable[Decimal | None]) -> Decimal | None:
    """D2/§3: objetivo = el del grupo en preparacion. Sin grupo => None; grupos
    con targets DISTINTOS => None (jamas un promedio, §3 explicito)."""
    valores = {t for t in targets if t is not None}
    if len(valores) == 1:
        return valores.pop()
    return None


def motivos_margen(margen: Decimal | None, objetivo_acos: Decimal | None) -> tuple[str, ...]:
    """D2/AC3: el manual no acredita rentabilidad; el preview declara el
    margen cero/negativo/inferior. Los motivos INFORMAN, jamas bloquean."""
    motivos: list[str] = []
    if margen is None:
        motivos.append("Margen sin medir.")
    elif margen == 0:
        motivos.append("Margen cero.")
    elif margen < 0:
        motivos.append("Margen negativo.")
    if objetivo_acos is not None and margen is not None and 0 < margen < objetivo_acos:
        motivos.append("Margen inferior al objetivo.")
    return tuple(motivos)


@dataclass(frozen=True)
class EvaluacionListing:
    """Evaluacion completa de una publicacion: economia (B.2) + Ads (B.1/B.4)
    + disponibilidad (B.3) + objetivo del grupo en preparacion (D2).

    `seleccionable` es SIEMPRE True: ninguna etiqueta (Ads, margen ni
    disponibilidad) bloquea la seleccion (AC3/AC10).
    """

    listing_id: int
    platform: str
    product_id: int
    asin: str | None
    seller_sku: str | None
    economia: EconomiaProducto
    ads: EvaluacionAds
    disponibilidad: dict
    objetivo_acos_pct: Decimal | None
    seleccionable: bool = True
    motivos: tuple[str, ...] = field(default_factory=tuple)


def evaluar_listing(
    listing_id: int,
    platform: str,
    product_id: int,
    asin: str | None,
    seller_sku: str | None,
    filas_ads: Sequence[ObservacionAds],
    ventana: tuple[dt.date, dt.date],
    economia: EconomiaProducto,
    disponibilidad: dict,
    objetivos_grupos: Iterable[Decimal | None] = (),
    moneda_ads: str | None = None,
) -> EvaluacionListing:
    """Capa de integracion pura: B.2 + B.3 + Ads + objetivo D2 por listing.

    `moneda_ads` es la del PERFIL de Ads (amazon_us -> USD), que puede diferir
    de la de la economia; si viene None se conserva la de la economia (regla
    4: la moneda viaja con las cifras que etiqueta)."""
    objetivo = objetivo_comparacion(objetivos_grupos)
    ads = evaluar_ads(filas_ads, ventana, objetivo=objetivo, moneda=moneda_ads or economia.moneda)
    return EvaluacionListing(
        listing_id=listing_id,
        platform=platform,
        product_id=product_id,
        asin=asin,
        seller_sku=seller_sku,
        economia=economia,
        ads=ads,
        disponibilidad=disponibilidad,
        objetivo_acos_pct=objetivo,
        motivos=motivos_margen(economia.margen_neto_pct, objetivo),
    )


_CLAVES_ORDEN = {
    "margen_observado": lambda e: e.economia.margen_neto_pct,
    "ventas_totales": lambda e: e.economia.venta_total,
    "revenue_ads": lambda e: e.ads.sales30d,
    "gasto": lambda e: e.ads.cost,
    "acos": lambda e: e.ads.acos_pct,
    "cpc": lambda e: e.ads.cpc,
    "cvr": lambda e: e.ads.cvr_pct,
    "compras": lambda e: e.ads.purchases30d,
}


def ordenar(
    evaluaciones: Sequence[EvaluacionListing], metrica: str, descendente: bool = False
) -> list[EvaluacionListing]:
    """Orden estable (§7 D1): NULL al final en AMBAS direcciones, desempate
    listing_id, MX/US no se mezclan (fail-loud con ValueError)."""
    if metrica not in METRICAS_ORDEN:
        raise ValueError(f"metrica de orden no soportada: {metrica!r}")
    plataformas = {e.platform for e in evaluaciones}
    if len(plataformas) > 1:
        raise ValueError(f"no se mezclan plataformas: {sorted(plataformas)}")
    clave = _CLAVES_ORDEN[metrica]
    # Base listing_id asc: el desempate es estable y predecible.
    base = sorted(evaluaciones, key=lambda e: e.listing_id)
    con_valor = [e for e in base if clave(e) is not None]
    sin_valor = [e for e in base if clave(e) is None]
    con_valor.sort(key=lambda e: clave(e), reverse=descendente)
    return con_valor + sin_valor
