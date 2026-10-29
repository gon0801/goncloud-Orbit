"""Adaptador READ-ONLY de Mercado Libre Ads campana/dia (RESUMEN-ADS-01).

Fuente: `meli_ads_campaigns_daily` del snapshot de la SQLite de contabilidad
(PK `(date, campaign_id)`), la tabla que escribe `sync_meli_ads.py`. El plan
nombraba `meli_ads_metrics_daily`, pero esa tabla es el total del advertiser
por dia (PK `date`) y no tiene campana: la puerta de fuente del 2026-10-29 lo
comprobo en vivo. `meli_ads_metrics_daily` queda solo como control de
conciliacion.

Este modulo es la FRONTERA del origen: abre el snapshot con `mode=ro`, valida
cada fila y la convierte a la forma canonica. No conoce Postgres ni escribe
nada. El dueno de la persistencia y de la politica temporal es
`app.meli.ads_ingest`.

Contrato comprobado en vivo (docs/evidencia/resumen-ads-multicanal/01-ingesta):

1. Grano campana/dia. Cada fila es una campana en un dia.
2. Identidad estable: `campaign_id` numerico.
3. Moneda: la tabla no guarda moneda. El site es MLM, los cargos de ads en
   `ledger_events` son MXN y el trigger `metric_moneda_de_plataforma` sella
   `meli -> MXN`. El adaptador acepta una columna opcional `currency` y
   rechaza cualquier valor distinto de MXN.
4. `spend` es el costo de ads y `ad_sales` el revenue atribuido de la API de
   MeLi Ads. NO se derivan del ledger ni de ventas totales.
5. Ceros reales: `spend=0`/`ad_sales=0` son filas escritas, no huecos. Un
   dato ausente se conserva NULL.
6. Correcciones: el sync pisa el valor en el origen sin revision y reescribe
   `updated_at` en cada corrida. `source_version` es el hash de la fila
   canonica (campana, fecha, moneda y todas las metricas; sin el nombre).
7. `updated_at` es obligatorio en cada fila no vacia. Una fila sin el, con
   fraccion de segundo o con un anio fuera de 2000-2100 rechaza el snapshot
   completo. Un as-of mas de 24 h adelante del reloj de ingesta se rechaza.
8. Las claves `(campaign_id, metric_date)` deben ser unicas; un snapshot sin
   PK que las repita se rechaza completo.
9. Dinero con la disciplina de app/costs.py: ruido binario < 1e-5 se
   cuantiza; precision genuina de mas de 4 decimales se rechaza; el rango se
   valida despues de cuantizar; un positivo que cuantiza a cero se rechaza.
10. `campaign_id` o `revision` con `|` se rechazan para no romper la
    identidad de `source_report_id`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

TABLA_ORIGEN = "meli_ads_campaigns_daily"
PLATAFORMA = "meli"
KIND = "campaign"
MONEDA_PLATAFORMA = "MXN"
_SEPARADOR = "\x1f"
_DECIMALES = Decimal("0.0001")
_COLUMNAS_OBLIGATORIAS = (
    "date",
    "campaign_id",
    "campaign_name",
    "clicks",
    "impressions",
    "spend",
    "ad_sales",
    "updated_at",
)
_MAX_DINERO = Decimal(10) ** 10  # 10 enteros + 4 decimales = NUMERIC(14,4)
_RUIDO_FLOAT = Decimal("0.00001")  # ruido binario del REAL de SQLite (costs.py:223)
_MAX_BIGINT = 2**63 - 1  # impressions/clicks: BIGINT
_MAX_INTEGER = 2**31 - 1  # orders: INTEGER
_TOLERANCIA_RELOJ = dt.timedelta(hours=24)  # limite duro de un as-of futuro


class MeliAdsError(Exception):
    """Snapshot ilegible o fila del origen que no cumple el contrato."""


@dataclass(frozen=True)
class OrigenMeliAds:
    """Snapshot leido completo: filas validas, rechazos y su as-of.

    `as_of` es el maximo `updated_at` del origen (UTC). Es la unica marca de
    orden que publica la fuente: no hay revision y `updated_at` no sirve como
    version (cambia en cada sync), pero SI como "cuando escribio el origen".
    """

    filas: tuple[FilaMeliAds, ...]
    rechazos: Counter
    as_of: dt.datetime | None
    total: int


@dataclass(frozen=True)
class FilaMeliAds:
    """Una fila canonica de ads de MeLi, independiente del esquema del origen."""

    campaign_external_id: str
    campaign_name: str | None
    metric_date: dt.date
    source_version: str
    metric_currency: str
    cost: Decimal | None
    ad_revenue: Decimal | None
    impressions: int | None
    clicks: int | None
    orders: int | None


def _abre_origen(ruta: Path | str) -> tuple[sqlite3.Connection, set[str]]:
    """Abre el snapshot en mode=ro y confirma que trae la tabla del contrato.

    Nunca se abre la SQLite viva: el runbook copia con la API `.backup()` y
    pasa la copia. `mode=ro` es la segunda capa (abrir una ruta equivocada no
    puede escribir).
    """
    uri = f"{Path(ruta).resolve().as_uri()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    columnas = {fila[1] for fila in con.execute(f"PRAGMA table_info({TABLA_ORIGEN})")}
    if not columnas:
        con.close()
        raise MeliAdsError(f"el snapshot no trae la tabla {TABLA_ORIGEN}")
    faltantes = [nombre for nombre in _COLUMNAS_OBLIGATORIAS if nombre not in columnas]
    if faltantes:
        con.close()
        raise MeliAdsError(
            f"{TABLA_ORIGEN} no cumple el contrato; faltan columnas: {', '.join(faltantes)}"
        )
    return con, columnas


def _texto_dinero(valor: Decimal | None) -> str:
    if valor is None:
        return ""
    return format(valor.quantize(_DECIMALES), "f")


def _texto_entero(valor: int | None) -> str:
    return "" if valor is None else str(valor)


def version_canonica(
    campaign_external_id: str,
    metric_date: dt.date,
    metric_currency: str,
    cost: Decimal | None,
    ad_revenue: Decimal | None,
    impressions: int | None,
    clicks: int | None,
    orders: int | None,
) -> str:
    """Hash determinista de la fila canonica (sin el nombre de campana).

    Incluye campana, fecha, moneda y TODAS las metricas. NO incluye
    `campaign_name`: renombrar una campana no cambia el hecho monetario y no
    debe agregar una observacion. Tampoco incluye `updated_at` (se reescribe
    en cada sync aunque el valor no cambie).
    """
    partes = (
        campaign_external_id,
        metric_date.isoformat(),
        metric_currency,
        _texto_dinero(cost),
        _texto_dinero(ad_revenue),
        _texto_entero(impressions),
        _texto_entero(clicks),
        _texto_entero(orders),
    )
    return hashlib.sha256(_SEPARADOR.join(partes).encode("utf-8")).hexdigest()


def _dinero(valor: object) -> Decimal | None:
    """Dinero exacto con la disciplina de app/costs.py:223 y :247.

    Tolera solo el ruido binario del REAL de SQLite (residuo < 1e-5).
    Rechaza precision genuina de mas de 4 decimales, en vez de redondearla en
    silencio. Valida el rango DESPUES de cuantizar y rechaza un positivo que
    cuantiza a cero (seria un importe perdido).
    """
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str, Decimal)):
        raise MeliAdsError(f"importe ilegible: {valor!r}")
    try:
        numero = Decimal(str(valor))
    except InvalidOperation:
        raise MeliAdsError(f"importe ilegible: {valor!r}") from None
    if not numero.is_finite():
        raise MeliAdsError(f"importe no finito: {valor!r}")
    if numero < 0:
        raise MeliAdsError(f"importe negativo: {valor!r}")
    try:
        cuantizado = numero.quantize(_DECIMALES)
    except InvalidOperation:
        raise MeliAdsError(f"importe fuera de NUMERIC(14,4): {valor!r}") from None
    if abs(numero - cuantizado) >= _RUIDO_FLOAT:
        raise MeliAdsError(f"importe con mas de 4 decimales: {valor!r}")
    if cuantizado >= _MAX_DINERO:
        raise MeliAdsError(f"importe fuera de NUMERIC(14,4): {valor!r}")
    if numero > 0 and cuantizado == 0:
        raise MeliAdsError(f"importe positivo que cuantiza a cero: {valor!r}")
    if cuantizado == 0:
        return Decimal("0.0000")  # cero negativo y cero positivo: misma version
    return cuantizado


def _entero(valor: object, maximo: int) -> int | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise MeliAdsError(f"entero ilegible: {valor!r}")
    if isinstance(valor, int):
        numero = valor
    elif isinstance(valor, float) and valor.is_integer():
        numero = int(valor)
    elif isinstance(valor, str) and re.fullmatch(r"-?\d+", valor.strip()):
        texto = valor.strip()
        if len(texto.lstrip("-")) > 19:
            raise MeliAdsError(f"entero fuera de rango (demasiados digitos): {valor!r}")
        numero = int(texto)
    else:
        raise MeliAdsError(f"entero ilegible: {valor!r}")
    if numero < 0:
        raise MeliAdsError(f"entero negativo: {valor!r}")
    if numero > maximo:
        raise MeliAdsError(f"entero fuera de rango ({maximo}): {valor!r}")
    return numero


def _nombre(valor: object) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if "\x00" in texto:
        raise MeliAdsError("campaign_name con NUL no soportado")
    return texto or None


def _sin_nul(valor: str, campo: str) -> str:
    """Rechaza NUL en un campo de texto antes de que llegue a PostgreSQL."""
    if "\x00" in valor:
        raise MeliAdsError(f"{campo} con NUL no soportado")
    return valor


def _fila(row: sqlite3.Row, columnas: set[str], hoy: dt.date) -> FilaMeliAds:
    """Convierte una fila del origen a la forma canonica y valida el contrato."""
    campaign_id = _sin_nul(str(row["campaign_id"] or "").strip(), "campaign_id")
    if not campaign_id:
        raise MeliAdsError("campaign_id vacio: identidad de campana ausente")
    if "|" in campaign_id:
        raise MeliAdsError("campaign_id con separador '|' no soportado")

    fecha_texto = str(row["date"] or "").strip()
    try:
        metric_date = dt.date.fromisoformat(fecha_texto)
    except ValueError:
        raise MeliAdsError(f"fecha ilegible en {campaign_id}: {fecha_texto!r}") from None
    if metric_date > hoy:
        raise MeliAdsError(f"fecha futura en {campaign_id}: {metric_date.isoformat()}")

    moneda = MONEDA_PLATAFORMA
    if "currency" in columnas and row["currency"] is not None:
        moneda = str(row["currency"]).strip().upper()
    if moneda != MONEDA_PLATAFORMA:
        raise MeliAdsError(f"moneda fuera de dominio ({MONEDA_PLATAFORMA}): {moneda}")

    cost = _dinero(row["spend"])
    ad_revenue = _dinero(row["ad_sales"])
    impressions = _entero(row["impressions"], _MAX_BIGINT)
    clicks = _entero(row["clicks"], _MAX_BIGINT)
    # El origen no publica orders: dato ausente = NULL, jamas una constante.
    orders = _entero(row["orders"], _MAX_INTEGER) if "orders" in columnas else None

    # El origen vivo no tiene revision (comprobado 2026-10-29). Si un snapshot
    # futuro la trae, ES la version: manda sobre el hash de las metricas.
    revision = row["revision"] if "revision" in columnas else None
    revision_texto = str(revision).strip() if revision is not None else ""
    revision_texto = _sin_nul(revision_texto, "revision")
    if "|" in revision_texto:
        raise MeliAdsError("revision con separador '|' no soportada")
    if not revision_texto:
        source_version = version_canonica(
            campaign_id, metric_date, moneda, cost, ad_revenue, impressions, clicks, orders
        )
    else:
        source_version = f"rev:{revision_texto}"

    return FilaMeliAds(
        campaign_external_id=campaign_id,
        campaign_name=_nombre(row["campaign_name"]),
        metric_date=metric_date,
        source_version=source_version,
        metric_currency=moneda,
        cost=cost,
        ad_revenue=ad_revenue,
        impressions=impressions,
        clicks=clicks,
        orders=orders,
    )


def _momento_utc(texto: object) -> dt.datetime | None:
    """Parsea un `updated_at` de SQLite como UTC (naive = UTC, el sync lo es).

    Rechaza cualquier fraccion de segundo: la fuente viva usa
    `datetime('now')` con precision de segundo y el id no puede ordenar dos
    correcciones del mismo segundo. Un offset que desborda el rango de
    `datetime` al normalizar a UTC (p. ej. `0001-01-01T00:00:00+00:01`) se
    rechaza, no se propaga.
    """
    if texto is None:
        return None
    valor = str(texto).strip()
    if not valor or "." in valor:
        return None
    try:
        momento = dt.datetime.fromisoformat(valor)
    except (ValueError, OverflowError):
        return None
    try:
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=dt.UTC)
        momento = momento.astimezone(dt.UTC)
    except (OverflowError, OSError, ValueError):
        return None
    if momento.microsecond != 0:
        return None
    if not 2000 <= momento.year <= 2100:
        return None
    return momento


def leer_origen(ruta: Path | str) -> OrigenMeliAds:
    """Lee y valida TODO el snapshot antes de que el dueno escriba nada.

    Una fila que no cumple el contrato se cuenta con su motivo y la corrida
    se aborta completa (fail-closed). `updated_at` es obligatorio en cada
    fila no vacia. El `as_of` es el maximo `updated_at` (UTC) y no puede
    estar mas de `_TOLERANCIA_RELOJ` adelante del reloj de ingesta. Las
    claves `(campaign_id, metric_date)` deben ser unicas.
    """
    hoy = dt.datetime.now(dt.UTC).date()
    ahora = dt.datetime.now(dt.UTC)
    rechazos: Counter = Counter()
    filas: list[FilaMeliAds] = []
    vistas: dict[tuple[str, str], FilaMeliAds] = {}
    as_of: dt.datetime | None = None
    total = 0
    con, columnas = _abre_origen(ruta)
    try:
        consulta = (
            f"SELECT date, campaign_id, campaign_name, clicks, impressions,"
            f" spend, ad_sales, updated_at{', currency' if 'currency' in columnas else ''}"
            f"{', orders' if 'orders' in columnas else ''}"
            f"{', revision' if 'revision' in columnas else ''}"
            f" FROM {TABLA_ORIGEN} ORDER BY date, campaign_id"
        )
        for row in con.execute(consulta):
            total += 1
            momento = _momento_utc(row["updated_at"])
            if momento is None:
                rechazos["updated_at ausente o ilegible"] += 1
            elif as_of is None or momento > as_of:
                as_of = momento
            try:
                fila = _fila(row, columnas, hoy)
            except MeliAdsError as exc:
                rechazos[str(exc)] += 1
                continue
            clave = (fila.campaign_external_id, fila.metric_date.isoformat())
            if clave in vistas:
                rechazos["clave canonica duplicada (campaign_id, metric_date)"] += 1
                continue
            vistas[clave] = fila
            filas.append(fila)
    finally:
        con.close()
    if as_of is not None and as_of > ahora + _TOLERANCIA_RELOJ:
        rechazos["as_of futuro fuera de la tolerancia de reloj"] += 1
    return OrigenMeliAds(filas=tuple(filas), rechazos=rechazos, as_of=as_of, total=total)
