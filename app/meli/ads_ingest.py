"""Ingesta de metricas de Mercado Libre Ads desde contabilidad (RESUMEN-ADS-01).

Fuente: snapshot READ-ONLY de la SQLite de accounting,
`meli_ads_campaigns_daily` (grano campana/dia, PK `(date, campaign_id)`), la
tabla que escribe `sync_meli_ads.py`. El plan nombraba
`meli_ads_metrics_daily`, pero esa tabla es el total del advertiser por dia
(PK `date`) y no tiene campana: la puerta de fuente del 2026-10-29 lo
comprobo en vivo y el operador autorizo leer la tabla hermana del mismo
snapshot. `meli_ads_metrics_daily` queda solo como control de conciliacion,
nunca como fuente de entidades.

Destino: `ad_entity` (solo `platform='meli'`, `kind='campaign'`) y
`ads_metric_observation` (append-only). No hay migracion: se reutilizan el
esquema, el dedupe `metric_dedupe_reporte` y `v_metric_latest`.

Contrato comprobado en vivo (docs/evidencia/resumen-ads-multicanal/01-ingesta):

1. Grano campana/dia. Cada fila es una campana en un dia. En el snapshot
   comprobado del 2026-10-29 habia 4 filas por dia; eso describe la muestra,
   no un invariante del esquema (la ingesta no exige 4).
2. Identidad estable: `campaign_id` numerico, 196 dias contiguos por campana.
3. Moneda: la tabla no guarda moneda. El site es MLM, los cargos de ads en
   `ledger_events` son MXN y el trigger `metric_moneda_de_plataforma` sella
   `meli -> MXN`. El adaptador acepta una columna opcional `currency` en el
   snapshot y rechaza cualquier valor distinto de MXN.
4. `spend` es el costo de ads y `ad_sales` el revenue atribuido de la API de
   MeLi Ads. NO se derivan del ledger ni de ventas totales de Mercado Libre.
5. Ceros reales: `spend=0`/`ad_sales=0` son filas escritas, no huecos. Se
   conservan como cero. Un dato ausente se conserva NULL.
6. Correcciones: el sync pisa el valor en el origen (`ON CONFLICT DO UPDATE`),
   sin revision. `updated_at` se reescribe en cada corrida aunque el valor no
   cambie, asi que NO sirve como version. `source_version` se calcula como
   hash de la fila canonica (campana, fecha, moneda y todas las metricas).
   Reingestar el mismo snapshot es no-op; una correccion agrega una
   observacion nueva y `v_metric_latest` expone el valor corregido.
7. Sin madurez D-8: MeLi no hereda ese contrato de Amazon. Se ingesta lo que
   el origen publica, incluido T+0 parcial. El sync de accounting finaliza
   T+1/T+2 y su correccion llega como observacion nueva.
8. Orden de snapshots: `updated_at` (UTC, segundo) es el as-of del snapshot.
   La marca es el maximo as-of ACEPTADO y es GLOBAL, no por campana. Se guarda
   en el `skip_reason` de cada corrida exitosa como `as_of=<epoch>` porque el
   esquema no tiene columna de watermark, y se conserva aunque la recopia no
   inserte metricas. Se toma el maximo entre TODAS las marcas exitosas y las
   observaciones, bajo el candado; el id de `ingest_run` no ordena porque los
   runs nacen antes del candado. Un snapshot anterior a la marca se rechaza
   COMPLETO, aunque sus metricas coincidan: no puede tocar el nombre. Con el
   mismo as-of solo se admite el reintento exacto. El nombre solo se aplica
   desde un snapshot estrictamente posterior a la marca. Un as-of hasta 24 h
   adelante se acepta, pero no envenena una correccion real: un snapshot con
   as-of <= ahora+5 min compara solo contra marcas reales; uno futuro compara
   contra todas. `observed_at` es un reloj logico: nunca queda por detras del
   maximo ya publicado, aunque el reloj de pared retroceda.
9. `updated_at` es obligatorio en cada fila no vacia. Una fila sin el, con
   fraccion de segundo o con un anio fuera de 2000-2100 rechaza el snapshot
   completo. Un as-of mas de 24 h adelante del reloj de ingesta se rechaza.
10. Dedupe: el `ON CONFLICT` apunta SOLO al indice parcial
    `metric_dedupe_reporte`; una colision de la PK
    `(ad_entity_id, metric_date, observed_at)` aborta la corrida, no se
    disfraza de duplicado. Las razones separan "misma version canonica
    (recopia o reintento)" de "source_report_id ya ingerido".
11. Dinero con la disciplina de app/costs.py: ruido binario < 1e-5 se
    cuantiza; precision genuina de mas de 4 decimales se rechaza; el rango se
    valida despues de cuantizar; un positivo que cuantiza a cero se rechaza.
    impressions/clicks respetan BIGINT; orders respeta INTEGER.
12. Las claves `(campaign_id, metric_date)` deben ser unicas. Un snapshot sin
    PK que las repita se rechaza completo; no se elige una fila y se cuenta la
    otra como duplicado. Un `campaign_id` o una `revision` con `|` se rechazan
    para no romper la identidad de `source_report_id`.

Idempotencia: `source_report_id =
meli-ads|<campaign_id>|<fecha>|<version>|<as_of_epoch>` y
`INSERT ... ON CONFLICT DO NOTHING` contra `metric_dedupe_reporte`. El
`ingest_run_id` NUNCA es clave de dedupe. Cada snapshot es atomico: una fila
invalida o un snapshot atrasado aborta la corrida completa sin escrituras
parciales y sella `ingest_run.ok=false` con el motivo. La corrida exitosa
sella `skip_reason = "as_of=<epoch>; <duplicados>"`; ese prefijo es la marca
temporal, no una razon de skip.

`python -m app.cli ingest meli-ads --sqlite RUTA`: sin ORBIT_DSN_INGEST o sin
snapshot -> exit 2 fail-closed; fallo de la corrida -> exit 1; exito -> 0.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import os
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

from app.db import connect
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "accounting_meli_ads_campaigns"
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
_SKEW_RELOJ = dt.timedelta(minutes=5)  # futuro que aun se considera reloj real

_SQL_ABRIR_RUN = "INSERT INTO ingest_run (source) VALUES (%s) RETURNING id"

_SQL_SELLAR_RUN = """
UPDATE ingest_run
   SET finished_at = now(),
       rows_written = %s,
       rows_skipped = %s,
       skip_reason = %s,
       ok = %s
 WHERE id = %s
"""

# El nombre se actualiza solo cuando el origen trae uno distinto: uno ausente
# (NULL) no borra el ya publicado. Sin cambio real, el WHERE corta el UPDATE y
# no hay escritura (reingestar el mismo snapshot es no-op tambien en el
# catalogo). Un snapshot atrasado se rechaza ANTES de tocar el catalogo, y con
# el mismo as-of el nombre no se aplica: una copia editada no puede restaurar
# un nombre viejo.
_SQL_UPSERT_ENTIDAD = """
INSERT INTO ad_entity (platform, kind, external_id, name)
VALUES ('meli', 'campaign', %s, %s)
ON CONFLICT (platform, kind, external_id) DO UPDATE
   SET name = EXCLUDED.name
 WHERE EXCLUDED.name IS NOT NULL
   AND ad_entity.name IS DISTINCT FROM EXCLUDED.name
RETURNING id, (xmax = 0) AS es_nueva
"""

_SQL_ID_ENTIDAD = (
    "SELECT id FROM ad_entity WHERE platform = 'meli' AND kind = 'campaign' AND external_id = %s"
)

# Ultima observacion por campana/dia: su version y su as-of viven en el
# source_report_id. La consulta se hace dentro de la transaccion de escritura.
_SQL_ULTIMAS = """
SELECT DISTINCT ON (e.external_id, o.metric_date)
       e.external_id, o.metric_date, o.source_report_id
  FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE e.platform = 'meli' AND e.kind = 'campaign'
 ORDER BY e.external_id, o.metric_date, o.observed_at DESC
"""

# Serializa SOLO las ingestas de MeLi: dos corridas concurrentes no pueden
# leer el mismo "ultimo" y escribir versiones distintas. No comparte candado
# con ningun otro pipeline.
_SQL_LOCK_MELI = "SELECT pg_advisory_xact_lock(%s, %s)"
_LOCK_MELI = (7071, 1)

# Reloj logico de observacion: si el reloj de pared retrocede, el nuevo
# observed_at debe superar al maximo ya publicado o v_metric_latest elegiria
# la fila anterior. Solo mira MeLi: no acopla el reloj de Amazon.
_SQL_MAX_OBSERVADO = """
SELECT max(o.observed_at)
  FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE e.platform = 'meli' AND e.kind = 'campaign'
"""

# Marcas temporales de TODOS los snapshots ACEPTADOS. Viven en el skip_reason
# de cada corrida exitosa porque el esquema no tiene columna de watermark y
# esta fase no crea migraciones. El id de ingest_run no sirve como orden: los
# runs se abren antes del candado y pueden terminar en otro orden. Se toma el
# maximo de las marcas, no la ultima por id.
_SQL_MARCA = (
    "SELECT skip_reason FROM ingest_run"
    " WHERE source = %s AND ok = true"
    " AND (skip_reason LIKE 'as_of=%%' OR skip_reason LIKE 'futuro=%%')"
)
_MARCA_RE = re.compile(r"^as_of=(\d+)(?:;|$)")
_FUTURO_RE = re.compile(r"(?:^|; )futuro=(\d+)")

_SQL_INSERTAR_OBS = """
INSERT INTO ads_metric_observation
    (ad_entity_id, metric_date, observed_at, metric_currency, cost, ad_revenue,
     impressions, clicks, orders, source_report_id, ingest_run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ad_entity_id, metric_date, source_report_id)
    WHERE source_report_id IS NOT NULL
DO NOTHING
"""


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


# ---------------------------------------------------------------------------
# Adaptador SQLite (los nombres de columna del origen viven SOLO aqui)
# ---------------------------------------------------------------------------


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
    return texto or None


def _fila(row: sqlite3.Row, columnas: set[str], hoy: dt.date) -> FilaMeliAds:
    """Convierte una fila del origen a la forma canonica y valida el contrato."""
    campaign_id = str(row["campaign_id"] or "").strip()
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
    correcciones del mismo segundo.
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
    # La fuente viva usa datetime('now') con precision de segundo. Una
    # fraccion de segundo es un dato distinto que el id no puede ordenar.
    if momento.microsecond != 0:
        return None
    # Un anio fuera de rango es un timestamp desbordado, no una fecha.
    if not 2000 <= momento.year <= 2100:
        return None
    return momento


def leer_origen(ruta: Path | str) -> OrigenMeliAds:
    """Lee y valida TODO el snapshot antes de escribir.

    Una fila que no cumple el contrato se cuenta con su motivo y la corrida
    se aborta completa (fail-closed): nunca hay escrituras parciales.
    `updated_at` es obligatorio en cada fila no vacia; una sola fila ausente o
    ilegible rechaza el snapshot completo. El `as_of` es el maximo
    `updated_at` (UTC) y no puede estar mas de `_TOLERANCIA_RELOJ` adelante del
    reloj de ingesta. Las claves `(campaign_id, metric_date)` deben ser unicas:
    un snapshot sin PK que repita una clave se rechaza, no se deduplica.
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


def _fuente_report_id(fila: FilaMeliAds, as_of: dt.datetime) -> str:
    """Identidad del hecho + version + as-of del snapshot.

    El as-of forma parte del id (y por tanto del indice de dedupe) para poder
    ordenar snapshots sin columnas nuevas. Dos snapshots con la misma version
    y el mismo as-of comparten id: reingestar es no-op por schema.
    """
    epoch = int(as_of.timestamp())
    return (
        f"meli-ads|{fila.campaign_external_id}|{fila.metric_date.isoformat()}"
        f"|{fila.source_version}|{epoch}"
    )


def _marca_de_as_of(skip_reason: str | None) -> int | None:
    """Lee el epoch del marcador `as_of=<epoch>` de una corrida exitosa."""
    if not skip_reason:
        return None
    coincidencia = _MARCA_RE.match(skip_reason)
    return int(coincidencia.group(1)) if coincidencia else None


def _futuro_de_marca(skip_reason: str | None) -> int | None:
    """Lee el epoch del marcador `futuro=<epoch>` de una corrida exitosa."""
    if not skip_reason:
        return None
    coincidencia = _FUTURO_RE.search(skip_reason)
    return int(coincidencia.group(1)) if coincidencia else None


def _version_y_as_of(source_report_id: str) -> tuple[str, int] | None:
    """Recupera (version, as_of_epoch) del id de una observacion de MeLi.

    None = id con formato desconocido (no se puede ordenar: fail-closed).
    """
    try:
        sin_epoch, epoch_texto = source_report_id.rsplit("|", 1)
        _, version = sin_epoch.rsplit("|", 1)
        return version, int(epoch_texto)
    except (AttributeError, ValueError):
        return None


def _formato_skip_reason(skips: Counter) -> str | None:
    if not skips:
        return None
    return ", ".join(f"{n}x {motivo}" for motivo, n in sorted(skips.items()))


def _sellar_run(
    conn: psycopg.Connection,
    run_id: int,
    *,
    ok: bool,
    rows_written: int,
    rows_skipped: int,
    skip_reason: str | None,
) -> None:
    conn.execute(_SQL_SELLAR_RUN, (rows_written, rows_skipped, skip_reason, ok, run_id))


def _entidad(
    conn: psycopg.Connection,
    campaign_external_id: str,
    nombre: str | None,
) -> tuple[int, bool, bool]:
    """Devuelve (id, es_nueva, nombre_actualizado).

    Un nombre ausente en el origen no borra el ya publicado. El renombre de un
    snapshot atrasado no llega aqui: ese snapshot se rechaza antes.
    """
    resultado = conn.execute(_SQL_UPSERT_ENTIDAD, (campaign_external_id, nombre)).fetchone()
    if resultado is not None:
        return int(resultado[0]), bool(resultado[1]), not bool(resultado[1])
    return int(conn.execute(_SQL_ID_ENTIDAD, (campaign_external_id,)).fetchone()[0]), False, False


def _nombres_vigentes(filas: list[FilaMeliAds]) -> dict[str, str]:
    """Nombre mas reciente por campana dentro del snapshot.

    Un renombre a mitad de la ventana deja nombres viejos en las filas viejas;
    el catalogo debe quedarse con el nombre de la fecha mas nueva, no con el
    de la primera fila que se procesa.
    """
    nombres: dict[str, str] = {}
    for fila in sorted(filas, key=lambda f: (f.metric_date, f.campaign_external_id)):
        if fila.campaign_name is not None:
            nombres[fila.campaign_external_id] = fila.campaign_name
    return nombres


def _marcas_corridas(conn: psycopg.Connection) -> tuple[int, int]:
    """Maximos as-of REAL y FUTURO de todas las corridas exitosas.

    Son dos relojes separados. Un as-of que fue futuro al aceptarse queda en
    cuarentena y no bloquea correcciones reales aunque el reloj se acerque.
    """
    reales: list[int] = []
    futuras: list[int] = []
    for fila in conn.execute(_SQL_MARCA, (SOURCE,)).fetchall():
        if (real := _marca_de_as_of(fila[0])) is not None:
            reales.append(real)
        if (futura := _futuro_de_marca(fila[0])) is not None:
            futuras.append(futura)
    return max(reales, default=-1), max(futuras, default=-1)


def _estado_temporal(
    conn: psycopg.Connection,
    ultimas: dict[tuple[str, dt.date], tuple[str, int]],
) -> tuple[dt.datetime, int, int, int]:
    """Reloj logico de observacion y marcas real/todas bajo el candado."""
    ahora_reloj = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    max_observado = conn.execute(_SQL_MAX_OBSERVADO).fetchone()[0]
    observed_at = (
        max(ahora_reloj, max_observado + dt.timedelta(microseconds=1))
        if max_observado is not None
        else ahora_reloj
    )
    marca_real_corrida, marca_futura = _marcas_corridas(conn)
    obs_max = max((partes[1] for partes in ultimas.values()), default=-1)
    # Sin marcas (base legada): las observaciones existentes son el unico
    # orden disponible.
    marca_real = obs_max if marca_real_corrida < 0 and marca_futura < 0 else marca_real_corrida
    limite_real = int(ahora_reloj.timestamp()) + int(_SKEW_RELOJ.total_seconds())
    return observed_at, marca_real, marca_futura, limite_real


def _marcadores_finales(
    marca_real: int,
    marca_futura: int,
    as_of_epoch: int,
    es_futuro: bool,
) -> tuple[int, int]:
    """Avanza solo el reloj que corresponde, sin pisar el otro."""
    if es_futuro:
        return marca_real, max(marca_futura, as_of_epoch)
    return max(marca_real, as_of_epoch), marca_futura


def _valida_orden(
    filas: tuple[FilaMeliAds, ...],
    ultimas: dict[tuple[str, dt.date], tuple[str, int]],
    marca: int,
    as_of_epoch: int,
) -> bool:
    """Valida el snapshot contra la marca y devuelve si puede tocar el nombre.

    Un as-of anterior a la marca rechaza el snapshot completo, aunque las
    metricas coincidan. Con el mismo as-of solo se admite el reintento exacto.
    """
    todas_iguales = True
    for fila in filas:
        vigente = ultimas.get((fila.campaign_external_id, fila.metric_date))
        if vigente is None or vigente[0] != fila.source_version:
            todas_iguales = False
            break
    if filas and (as_of_epoch < marca or (as_of_epoch == marca and not todas_iguales)):
        raise MeliAdsError(
            "snapshot atrasado: su as_of no es posterior a la marca"
            f" aceptada ({marca}); no se escribio nada"
        )
    return as_of_epoch > marca


def ingest_meli_ads(
    conn: psycopg.Connection,
    sqlite_path: Path | str,
) -> dict[str, int]:
    """Ingesta atomica de un snapshot de `meli_ads_campaigns_daily`.

    Abre la `ingest_run` ANTES de leer el snapshot: una tabla ausente, una
    SQLite corrupta o una fila invalida dejan corrida `ok=false` cuando hay
    PostgreSQL. Lee y valida el snapshot completo y escribe entidades y
    observaciones en UNA transaccion. Un snapshot atrasado (o una fila
    invalida) se rechaza completo, sin escrituras parciales. En cualquier
    fallo: rollback y sello best-effort.
    """
    origen: OrigenMeliAds | None = None
    rechazo_total = False

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    entidades_nuevas = 0
    entidades_actualizadas = 0
    insertadas = 0
    duplicados_version = 0
    duplicados_id = 0
    try:
        origen = leer_origen(sqlite_path)
        filas = origen.filas
        rechazos = origen.rechazos
        if rechazos:
            rechazo_total = True
            raise MeliAdsError(
                "snapshot rechazado (cero escrituras): "
                + (", ".join(f"{n}x {motivo}" for motivo, n in sorted(rechazos.items())))
            )
        if filas and origen.as_of is None:
            rechazo_total = True
            raise MeliAdsError(
                "snapshot rechazado (cero escrituras): sin as_of util"
                " (updated_at ausente o ilegible)"
            )
        with conn.transaction():
            conn.execute(_SQL_LOCK_MELI, _LOCK_MELI)
            # DESPUES del candado: el reloj de ingesta no puede quedar antes
            # que el de una corrida que termino mientras esta esperaba, o
            # v_metric_latest elegiria la observacion equivocada.
            ultimas: dict[tuple[str, dt.date], tuple[str, int]] = {}
            for external_id, metric_date, report_id in conn.execute(_SQL_ULTIMAS):
                partes = _version_y_as_of(report_id)
                if partes is None:
                    raise MeliAdsError(
                        "observacion vigente sin as_of legible en su source_report_id:"
                        f" {external_id} {metric_date.isoformat()}"
                    )
                ultimas[(external_id, metric_date)] = partes

            # Reloj logico y marcas bajo el candado. El id de ingest_run no
            # ordena. Un as-of futuro no puede envenenar una correccion real:
            # para un snapshot <= ahora+skew solo cuentan las marcas reales;
            # para uno futuro se compara contra todas.
            observed_at, marca_real, marca_futura, limite_real = _estado_temporal(
                conn, ultimas
            )
            as_of_epoch = int(origen.as_of.timestamp()) if origen.as_of is not None else -1
            es_futuro = as_of_epoch > limite_real
            marca_para = max(marca_real, marca_futura) if es_futuro else marca_real
            try:
                # El nombre solo se aplica desde un snapshot estrictamente
                # posterior a la marca aplicable.
                permite_nombre = _valida_orden(filas, ultimas, marca_para, as_of_epoch)
            except MeliAdsError:
                rechazo_total = True
                raise

            cache: dict[str, tuple[int, bool, bool]] = {}
            nombres = _nombres_vigentes(filas)
            for fila in filas:
                entidad = cache.get(fila.campaign_external_id)
                if entidad is None:
                    entidad = _entidad(
                        conn,
                        fila.campaign_external_id,
                        nombres.get(fila.campaign_external_id) if permite_nombre else None,
                    )
                    cache[fila.campaign_external_id] = entidad
                    entidades_nuevas += int(entidad[1])
                    entidades_actualizadas += int(entidad[2])
                vigente = ultimas.get((fila.campaign_external_id, fila.metric_date))
                if vigente is not None and vigente[0] == fila.source_version:
                    # Mismo hecho canonico (recopia o reintento con otro
                    # as-of): no es una correccion. El catalogo ya se
                    # actualizo arriba; no se agrega otra observacion.
                    duplicados_version += 1
                    continue
                cursor = conn.execute(
                    _SQL_INSERTAR_OBS,
                    (
                        entidad[0],
                        fila.metric_date,
                        observed_at,
                        fila.metric_currency,
                        fila.cost,
                        fila.ad_revenue,
                        fila.impressions,
                        fila.clicks,
                        fila.orders,
                        _fuente_report_id(fila, origen.as_of),
                        run_id,
                    ),
                )
                if cursor.rowcount:
                    insertadas += 1
                else:
                    duplicados_id += 1

            skips: Counter = Counter()
            if duplicados_version:
                skips["misma version canonica (recopia o reintento)"] = duplicados_version
            if duplicados_id:
                skips["source_report_id ya ingerido"] = duplicados_id
            # Cada reloj avanza por separado: una recopia real conserva su
            # propio as-of aunque exista una marca futura en cuarentena.
            real_final, futuro_final = _marcadores_finales(
                marca_real, marca_futura, as_of_epoch, es_futuro
            )
            partes = []
            if real_final >= 0:
                partes.append(f"as_of={real_final}")
            if futuro_final >= 0:
                partes.append(f"futuro={futuro_final}")
            motivo = _formato_skip_reason(skips)
            if motivo:
                partes.append(motivo)
            skip_reason = "; ".join(partes) or None
            _sellar_run(
                conn,
                run_id,
                ok=True,
                rows_written=insertadas,
                rows_skipped=duplicados_version + duplicados_id,
                skip_reason=skip_reason,
            )
    except BaseException as exc:
        # El with de arriba ya hizo rollback del trabajo. La run se sella como
        # fallo si la conexion sigue viva (best-effort). BaseException: un
        # Ctrl-C tambien deja la corrida sellada, no abierta.
        motivo = scrub(str(exc)) or type(exc).__name__
        rows_skipped = origen.total if (origen is not None and rechazo_total) else 0
        try:
            with conn.transaction():
                _sellar_run(
                    conn,
                    run_id,
                    ok=False,
                    rows_written=0,
                    rows_skipped=rows_skipped,
                    skip_reason=motivo,
                )
        except Exception:
            logger.warning(
                "ingest_run %s quedo ABIERTA: fallo tambien su sello de fallo;"
                " el error original de la corrida era: %s",
                run_id,
                motivo,
            )
        raise

    duplicados = duplicados_version + duplicados_id
    return {
        "run_id": run_id,
        "filas_origen": origen.total if origen is not None else 0,
        "rows_written": insertadas,
        "rows_skipped": duplicados,
        "duplicados": duplicados,
        "duplicados_version": duplicados_version,
        "duplicados_id": duplicados_id,
        "rechazos": sum(origen.rechazos.values()) if origen is not None else 0,
        "entidades_nuevas": entidades_nuevas,
        "entidades_actualizadas": entidades_actualizadas,
    }


# ---------------------------------------------------------------------------
# main del pipeline (el CLI despacha aqui; cero logica de argparse en el CLI)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli ingest meli-ads",
        description=(
            "Ingesta campana/dia de Mercado Libre Ads desde un snapshot"
            " read-only de la SQLite de contabilidad (runbook: docs/DEPLOY.md)."
        ),
    )
    parser.add_argument(
        "--sqlite",
        required=True,
        help="ruta del snapshot (producido con la API .backup(); ver runbook)",
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se puede ingerir MeLi Ads (fail-closed)",
            file=sys.stderr,
        )
        return 2
    ruta = Path(args.sqlite)
    if not ruta.is_file():
        print(
            f"el snapshot no existe: {ruta} (runbook: docs/DEPLOY.md, ingesta de MeLi Ads)",
            file=sys.stderr,
        )
        return 2

    try:
        conn = connect(dsn)
        try:
            resultado = ingest_meli_ads(conn, ruta)
        finally:
            conn.close()
    except Exception as exc:
        print(
            "ingesta de MeLi Ads fallo (la ingest_run quedo sellada ok=false"
            f" cuando fue posible): {scrub(str(exc))}",
            file=sys.stderr,
        )
        return 1

    print(f"== Ingesta de MeLi Ads desde contabilidad ({SOURCE}) ==")
    print(
        f"run_id={resultado['run_id']} ok=True"
        f" rows_written={resultado['rows_written']}"
        f" rows_skipped={resultado['rows_skipped']}"
        f" filas_origen={resultado['filas_origen']}"
    )
    print(
        f"entidades: nuevas={resultado['entidades_nuevas']}"
        f" actualizadas={resultado['entidades_actualizadas']}"
        f" duplicados={resultado['duplicados']}"
        f" rechazos={resultado['rechazos']}"
    )
    return 0
