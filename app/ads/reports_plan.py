"""Planificacion pura, validacion y fusion de filas de reportes Amazon Ads (Fase 3.5).

Extraido de `app/ads/reports.py`: sin IO de red ni de base de datos. Contiene
los parsers numericos, clasificacion ASIN-like, suma con envenenamiento de
None, envenenamiento de claves duplicadas y los tres planificadores
(`_planea_filas`, `_planea_filas_terminos`, `_planea_filas_productos`).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)


class AdsReportsError(Exception):
    """Error de forma en la respuesta de la API de reportes.

    El mensaje no toca secretos por construccion (ids de reporte y paths sin
    query, nunca headers ni url firmada); `scrub()` como ultima linea de
    defensa.
    """

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


@dataclass
class _FilaPlano:
    """Una fila planificada: lo que se inserta si ningun gate la salta."""

    external_id: str
    metric_date: dt.date
    cost: Decimal | None
    ad_revenue: Decimal | None
    revenue_same_sku: Decimal | None
    impressions: int | None
    clicks: int | None
    orders: int | None


@dataclass
class _FilaTermino:
    """Una fila de search term planificada: lo que se inserta si ningun gate la salta."""

    external_id: str  # adGroupId como str
    search_term: str
    is_asin_like: bool
    metric_date: dt.date
    cost: Decimal | None
    ad_revenue: Decimal | None
    clicks: int | None
    orders: int | None


@dataclass
class _FilaProducto:
    """Una fila de producto anunciado planificada (ORBIT 19 B.1)."""

    asin: str
    sku: str
    metric_date: dt.date
    impressions: int | None
    clicks: int | None
    cost: Decimal | None
    purchases30d: Decimal | None
    sales30d: Decimal | None
    purchases_same_sku30d: Decimal | None
    attributed_sales_same_sku_30d: Decimal | None
    campaign_ids: list[str]
    ad_ids: list[str]


def _decimal_reporte(valor: object, campo: str) -> Decimal | None:
    """Convierte una metrica de dinero a Decimal (via str, sin float a NUMERIC).

    None (ausente) -> None (regla 3). Un valor presente pero no numerico o no
    finito es dato corrupto: ValueError para que la fila se salte con motivo.
    """
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str, Decimal)):
        raise ValueError(f"{campo} no numerico")
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{campo} no numerico") from None
    if not numero.is_finite():
        raise ValueError(f"{campo} no numerico")
    return numero


def _entero_reporte(valor: object, campo: str) -> int | None:
    """Convierte un contador a int. None -> None; fraccion o no numerico -> ValueError."""
    if valor is None:
        return None
    numero = _decimal_reporte(valor, campo)
    assert numero is not None
    if numero != numero.to_integral_value():
        raise ValueError(f"{campo} fraccionario")
    return int(numero)


def _valida_fecha_fila(
    raw_date: object,
    *,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
    skips: Counter[str],
    etiqueta: str,
    contexto: str,
) -> dt.date | None:
    """Valida formato ISO, cierre de dia de marketplace y pertenencia al rango."""
    try:
        if not isinstance(raw_date, str):
            raise ValueError
        metric_date = dt.date.fromisoformat(raw_date)
    except ValueError:
        skips["fila con date invalida"] += 1
        logger.debug("date invalida en fila de %s: %r", etiqueta, raw_date)
        return None
    if metric_date > ultimo_cerrado:
        skips["fila de un día que aún no cierra"] += 1
        logger.debug(
            "dia que aun no cierra en fila de %s: %s > %s",
            etiqueta,
            metric_date,
            ultimo_cerrado,
        )
        return None
    if metric_date < fecha_ini or metric_date > fecha_fin:
        skips["fila con metric_date fuera del rango solicitado"] += 1
        logger.debug(
            "metric_date %s fuera del rango %s..%s en %s %s",
            metric_date,
            fecha_ini,
            fecha_fin,
            etiqueta,
            contexto,
        )
        return None
    return metric_date


def _planea_filas(
    reporte_cfg: dict,
    filas: list[dict],
    *,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> tuple[list[_FilaPlano], Counter[str]]:
    """Valida cada fila ANTES de tocar la base."""
    plan: list[_FilaPlano] = []
    skips: Counter[str] = Counter()
    etiqueta = reporte_cfg["nombre"]
    campo_id = reporte_cfg["id_field"]

    for fila in filas:
        external = fila.get(campo_id)
        if external is None:
            skips[f"fila de {etiqueta} sin {campo_id}"] += 1
            continue
        external_id = str(external)
        metric_date = _valida_fecha_fila(
            fila.get("date"),
            ultimo_cerrado=ultimo_cerrado,
            fecha_ini=fecha_ini,
            fecha_fin=fecha_fin,
            skips=skips,
            etiqueta=etiqueta,
            contexto=external_id,
        )
        if metric_date is None:
            continue
        try:
            cost = _decimal_reporte(fila.get("cost"), "cost")
            ad_revenue = _decimal_reporte(fila.get("sales30d"), "sales30d")
            revenue_same_sku = _decimal_reporte(
                fila.get("attributedSalesSameSku30d"), "attributedSalesSameSku30d"
            )
            impressions = _entero_reporte(fila.get("impressions"), "impressions")
            clicks = _entero_reporte(fila.get("clicks"), "clicks")
            orders = _entero_reporte(fila.get("purchases30d"), "purchases30d")
        except ValueError:
            skips[f"fila de {etiqueta} con metrica no numerica o fraccionaria"] += 1
            continue
        if (
            cost is None
            and ad_revenue is None
            and revenue_same_sku is None
            and impressions is None
            and clicks is None
            and orders is None
        ):
            skips["fila sin ninguna metrica"] += 1
            logger.debug("fila sin ninguna metrica: %s %s %s", etiqueta, external_id, metric_date)
            continue
        if (
            revenue_same_sku is not None
            and ad_revenue is not None
            and revenue_same_sku > ad_revenue
        ):
            skips["fila con revenue_same_sku > ad_revenue"] += 1
            logger.debug(
                "same_sku > ad_revenue en %s %s (%s > %s)",
                etiqueta,
                external_id,
                revenue_same_sku,
                ad_revenue,
            )
            continue
        plan.append(
            _FilaPlano(
                external_id=external_id,
                metric_date=metric_date,
                cost=cost,
                ad_revenue=ad_revenue,
                revenue_same_sku=revenue_same_sku,
                impressions=impressions,
                clicks=clicks,
                orders=orders,
            )
        )

    return plan, skips


# re.ASCII evita que IGNORECASE acepte signos Unicode con case-folding a ASCII
# (como U+212A Kelvin -> "k").
_RE_ASIN = re.compile(r"^b0[0-9a-z]{8}$", re.IGNORECASE | re.ASCII)


def es_asin_like(termino: str) -> bool:
    """Clasifica un search term del reporte como ASIN-like o no."""
    return bool(_RE_ASIN.match(termino.strip()))


def _sumar_decimal(actual: Decimal | None, aporte: Decimal | None) -> Decimal | None:
    """Fusion de dinero por clave duplicada, con ENVENENAMIENTO de None."""
    if actual is None or aporte is None:
        return None
    return actual + aporte


def _sumar_entero(actual: int | None, aporte: int | None) -> int | None:
    """Espejo entero de _sumar_decimal (mismo envenenamiento de None)."""
    if actual is None or aporte is None:
        return None
    return actual + aporte


def _envenenar_clave(
    clave: tuple,
    *,
    por_clave: dict,
    plan: list | None = None,
    envenenadas: set,
    skips: Counter[str],
) -> None:
    """Un constituyente INVALIDO envenena su clave para TODO el reporte (O(1) con dict)."""
    previa = por_clave.pop(clave, None)
    if previa is not None:
        if plan is not None:
            plan.remove(previa)
        skips["fila de clave envenenada (subtotal parcial)"] += 1
    envenenadas.add(clave)
    logger.debug("fila invalida envenena la clave (subtotal parcial): %s", clave)


def _planea_filas_terminos(
    reporte_cfg: dict,
    filas: list[dict],
    *,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> tuple[list[_FilaTermino], Counter[str]]:
    """Valida, CLASIFICA y FUSIONA las filas de search terms ANTES de la base."""
    skips: Counter[str] = Counter()
    etiqueta = reporte_cfg["nombre"]
    campo_id = reporte_cfg["id_field"]
    # `por_clave` preserva orden de insercion y permite borrado O(1) al envenenar.
    por_clave: dict[tuple[str, str, dt.date], _FilaTermino] = {}
    envenenadas: set[tuple[str, str, dt.date]] = set()

    for fila in filas:
        external = fila.get(campo_id)
        if external is None:
            skips[f"fila de {etiqueta} sin {campo_id}"] += 1
            continue
        external_id = str(external)
        raw_term = fila.get("searchTerm")
        if not isinstance(raw_term, str) or not raw_term.strip():
            skips["fila de search_terms sin searchTerm"] += 1
            logger.debug("fila de %s sin searchTerm: %r", etiqueta, raw_term)
            continue
        search_term = raw_term.strip()
        metric_date = _valida_fecha_fila(
            fila.get("date"),
            ultimo_cerrado=ultimo_cerrado,
            fecha_ini=fecha_ini,
            fecha_fin=fecha_fin,
            skips=skips,
            etiqueta=etiqueta,
            contexto=external_id,
        )
        if metric_date is None:
            continue
        clave = (external_id, search_term, metric_date)
        try:
            cost = _decimal_reporte(fila.get("cost"), "cost")
            ad_revenue = _decimal_reporte(fila.get("sales7d"), "sales7d")
            clicks = _entero_reporte(fila.get("clicks"), "clicks")
            orders = _entero_reporte(fila.get("purchases7d"), "purchases7d")
        except ValueError:
            skips[f"fila de {etiqueta} con metrica no numerica o fraccionaria"] += 1
            _envenenar_clave(clave, por_clave=por_clave, envenenadas=envenenadas, skips=skips)
            continue
        if cost is None and ad_revenue is None and clicks is None and orders is None:
            skips["fila sin ninguna metrica"] += 1
            logger.debug("fila sin ninguna metrica: %s %s %s", etiqueta, external_id, metric_date)
            _envenenar_clave(clave, por_clave=por_clave, envenenadas=envenenadas, skips=skips)
            continue
        if (
            (cost is not None and cost < 0)
            or (ad_revenue is not None and ad_revenue < 0)
            or (clicks is not None and clicks < 0)
            or (orders is not None and orders < 0)
        ):
            raise AdsReportsError(
                f"fila de {etiqueta} con metrica negativa (dato corrupto): "
                "la corrida aborta fail-closed"
            )
        if orders is None and (clicks is not None or cost is not None):
            skips["fila con pedidos vacíos"] += 1
            logger.debug("fila con pedidos vacios: %s %s %s", etiqueta, external_id, metric_date)
            _envenenar_clave(clave, por_clave=por_clave, envenenadas=envenenadas, skips=skips)
            continue
        if clave in envenenadas:
            skips["fila de clave envenenada (subtotal parcial)"] += 1
            logger.debug("fila descartada por clave envenenada: %s %s", etiqueta, clave)
            continue
        previa = por_clave.get(clave)
        if previa is not None:
            previa.cost = _sumar_decimal(previa.cost, cost)
            previa.ad_revenue = _sumar_decimal(previa.ad_revenue, ad_revenue)
            previa.clicks = _sumar_entero(previa.clicks, clicks)
            previa.orders = _sumar_entero(previa.orders, orders)
            skips["fila agregada por clave duplicada en el reporte"] += 1
            logger.debug(
                "fila agregada por clave duplicada en el reporte: %s %s %s %s",
                etiqueta,
                external_id,
                search_term,
                metric_date,
            )
            if (
                previa.cost is None
                and previa.ad_revenue is None
                and previa.clicks is None
                and previa.orders is None
            ):
                logger.debug(
                    "fusion dejo la clave sin ninguna metrica (par envenenado), "
                    "no se escribe: %s %s",
                    etiqueta,
                    clave,
                )
                _envenenar_clave(clave, por_clave=por_clave, envenenadas=envenenadas, skips=skips)
            continue
        por_clave[clave] = _FilaTermino(
            external_id=external_id,
            search_term=search_term,
            is_asin_like=es_asin_like(search_term),
            metric_date=metric_date,
            cost=cost,
            ad_revenue=ad_revenue,
            clicks=clicks,
            orders=orders,
        )

    return list(por_clave.values()), skips


def _fusionar_fila_producto(
    previa: _FilaProducto,
    *,
    asin: str,
    sku: str,
    metric_date: dt.date,
    impressions: int | None,
    clicks: int | None,
    cost: Decimal | None,
    purchases: Decimal | None,
    sales: Decimal | None,
    purchases_same_sku: Decimal | None,
    sales_same_sku: Decimal | None,
    campaign_id: str | None,
    ad_id: str | None,
    por_clave: dict[tuple[str, str, dt.date], _FilaProducto],
    plan: list[_FilaProducto] | None = None,
    envenenadas: set[tuple[str, str, dt.date]],
    skips: Counter[str],
) -> None:
    """Absorbe una fila duplicada (mismo asin/sku/fecha, otra campana) hacia
    el grano sellado con envenenamiento de None (O(1) con dict)."""
    previa.impressions = _sumar_entero(previa.impressions, impressions)
    previa.clicks = _sumar_entero(previa.clicks, clicks)
    previa.cost = _sumar_decimal(previa.cost, cost)
    previa.purchases30d = _sumar_decimal(previa.purchases30d, purchases)
    previa.sales30d = _sumar_decimal(previa.sales30d, sales)
    previa.purchases_same_sku30d = _sumar_decimal(previa.purchases_same_sku30d, purchases_same_sku)
    previa.attributed_sales_same_sku_30d = _sumar_decimal(
        previa.attributed_sales_same_sku_30d, sales_same_sku
    )
    if campaign_id is not None and campaign_id not in previa.campaign_ids:
        previa.campaign_ids.append(campaign_id)
    if ad_id is not None and ad_id not in previa.ad_ids:
        previa.ad_ids.append(ad_id)
    skips["fila agregada por clave duplicada en el reporte"] += 1
    logger.debug(
        "fila agregada por clave duplicada en el reporte: %s/%s %s", asin, sku, metric_date
    )
    if all(
        valor is None
        for valor in (
            previa.impressions,
            previa.clicks,
            previa.cost,
            previa.purchases30d,
            previa.sales30d,
            previa.purchases_same_sku30d,
            previa.attributed_sales_same_sku_30d,
        )
    ):
        del por_clave[(asin, sku, metric_date)]
        if plan is not None:
            plan.remove(previa)
        envenenadas.add((asin, sku, metric_date))
        skips["fila sin ninguna metrica"] += 1
        logger.debug(
            "fusion dejo la clave sin ninguna metrica (par envenenado), no se escribe: %s/%s %s",
            asin,
            sku,
            metric_date,
        )


def _planea_filas_productos(
    filas: list[dict],
    *,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> tuple[list[_FilaProducto], Counter[str]]:
    """Valida y AGREGA las filas de spAdvertisedProduct por SUMA al grano
    (asin, sku, fecha) ANTES de la base (ORBIT 19 B.1, politica 0.4 §2)."""
    skips: Counter[str] = Counter()
    por_clave: dict[tuple[str, str, dt.date], _FilaProducto] = {}
    envenenadas: set[tuple[str, str, dt.date]] = set()

    for fila in filas:
        raw_asin = fila.get("advertisedAsin")
        if not isinstance(raw_asin, str) or not raw_asin.strip():
            skips["fila de productos sin advertisedAsin"] += 1
            continue
        raw_sku = fila.get("advertisedSku")
        if not isinstance(raw_sku, str) or not raw_sku.strip():
            skips["fila de productos sin advertisedSku"] += 1
            continue
        asin = raw_asin.strip()
        sku = raw_sku.strip()
        metric_date = _valida_fecha_fila(
            fila.get("date"),
            ultimo_cerrado=ultimo_cerrado,
            fecha_ini=fecha_ini,
            fecha_fin=fecha_fin,
            skips=skips,
            etiqueta="productos",
            contexto=f"{asin}/{sku}",
        )
        if metric_date is None:
            continue
        clave = (asin, sku, metric_date)
        try:
            impressions = _entero_reporte(fila.get("impressions"), "impressions")
            clicks = _entero_reporte(fila.get("clicks"), "clicks")
            cost = _decimal_reporte(fila.get("cost"), "cost")
            purchases = _decimal_reporte(fila.get("purchases30d"), "purchases30d")
            sales = _decimal_reporte(fila.get("sales30d"), "sales30d")
            purchases_same_sku = _decimal_reporte(
                fila.get("purchasesSameSku30d"), "purchasesSameSku30d"
            )
            sales_same_sku = _decimal_reporte(
                fila.get("attributedSalesSameSku30d"), "attributedSalesSameSku30d"
            )
        except ValueError:
            skips["fila de productos con metrica no numerica o fraccionaria"] += 1
            _envenenar_clave(clave, por_clave=por_clave, envenenadas=envenenadas, skips=skips)
            continue
        if all(
            valor is None
            for valor in (
                impressions,
                clicks,
                cost,
                purchases,
                sales,
                purchases_same_sku,
                sales_same_sku,
            )
        ):
            skips["fila sin ninguna metrica"] += 1
            logger.debug("fila sin ninguna metrica: producto %s/%s %s", asin, sku, metric_date)
            _envenenar_clave(clave, por_clave=por_clave, envenenadas=envenenadas, skips=skips)
            continue
        if any(
            valor is not None and valor < 0
            for valor in (
                impressions,
                clicks,
                cost,
                purchases,
                sales,
                purchases_same_sku,
                sales_same_sku,
            )
        ):
            raise AdsReportsError(
                "fila de productos con metrica negativa (dato corrupto): "
                "la corrida aborta fail-closed"
            )
        if (sales_same_sku is not None and sales is not None and sales_same_sku > sales) or (
            purchases_same_sku is not None
            and purchases is not None
            and purchases_same_sku > purchases
        ):
            skips["fila con metrica same_sku mayor que el total"] += 1
            logger.debug(
                "same_sku > total en producto %s/%s (%s/%s vs %s/%s)",
                asin,
                sku,
                sales_same_sku,
                purchases_same_sku,
                sales,
                purchases,
            )
            _envenenar_clave(clave, por_clave=por_clave, envenenadas=envenenadas, skips=skips)
            continue
        campaign_id = str(fila["campaignId"]) if fila.get("campaignId") is not None else None
        ad_id = str(fila["adId"]) if fila.get("adId") is not None else None

        if clave in envenenadas:
            skips["fila de clave envenenada (subtotal parcial)"] += 1
            logger.debug("fila descartada por clave envenenada: %s/%s %s", asin, sku, metric_date)
            continue
        previa = por_clave.get(clave)
        if previa is not None:
            _fusionar_fila_producto(
                previa,
                asin=asin,
                sku=sku,
                metric_date=metric_date,
                impressions=impressions,
                clicks=clicks,
                cost=cost,
                purchases=purchases,
                sales=sales,
                purchases_same_sku=purchases_same_sku,
                sales_same_sku=sales_same_sku,
                campaign_id=campaign_id,
                ad_id=ad_id,
                por_clave=por_clave,
                envenenadas=envenenadas,
                skips=skips,
            )
            continue
        por_clave[clave] = _FilaProducto(
            asin=asin,
            sku=sku,
            metric_date=metric_date,
            impressions=impressions,
            clicks=clicks,
            cost=cost,
            purchases30d=purchases,
            sales30d=sales,
            purchases_same_sku30d=purchases_same_sku,
            attributed_sales_same_sku_30d=sales_same_sku,
            campaign_ids=[campaign_id] if campaign_id is not None else [],
            ad_ids=[ad_id] if ad_id is not None else [],
        )

    return list(por_clave.values()), skips
