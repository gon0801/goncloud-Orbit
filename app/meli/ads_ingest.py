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

1. Grano campana/dia. Cada fila es una campana en un dia; 4 filas por dia.
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

Idempotencia: `source_report_id = meli-ads:<campaign_id>:<fecha>:<version>` y
`INSERT ... ON CONFLICT DO NOTHING` contra `metric_dedupe_reporte`. El
`ingest_run_id` NUNCA es clave de dedupe. Cada snapshot es atomico: una fila
invalida aborta la corrida completa sin escrituras parciales y sella
`ingest_run.ok=false` con el motivo.

`python -m app.cli ingest meli-ads --sqlite RUTA`: sin ORBIT_DSN_INGEST o sin
snapshot -> exit 2 fail-closed; fallo de la corrida -> exit 1; exito -> 0.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import os
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
)
_MAX_DINERO = Decimal(10) ** 10  # 10 enteros + 4 decimales = NUMERIC(14,4)

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

# El nombre solo se actualiza cuando MEJORA: un nombre ausente en el origen no
# borra el nombre ya publicado. Sin cambio real, el WHERE corta el UPDATE y no
# hay escritura (reingestar el mismo snapshot es no-op tambien en el catalogo).
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

_SQL_INSERTAR_OBS = """
INSERT INTO ads_metric_observation
    (ad_entity_id, metric_date, observed_at, metric_currency, cost, ad_revenue,
     impressions, clicks, orders, source_report_id, ingest_run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


class MeliAdsError(Exception):
    """Snapshot ilegible o fila del origen que no cumple el contrato."""


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
    if numero >= _MAX_DINERO:
        raise MeliAdsError(f"importe fuera de NUMERIC(14,4): {valor!r}")
    return numero.quantize(_DECIMALES)


def _entero(valor: object) -> int | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise MeliAdsError(f"entero ilegible: {valor!r}")
    if isinstance(valor, int):
        numero = valor
    elif isinstance(valor, float) and valor.is_integer():
        numero = int(valor)
    elif isinstance(valor, str) and valor.strip().lstrip("-").isdigit():
        numero = int(valor.strip())
    else:
        raise MeliAdsError(f"entero ilegible: {valor!r}")
    if numero < 0:
        raise MeliAdsError(f"entero negativo: {valor!r}")
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
    impressions = _entero(row["impressions"])
    clicks = _entero(row["clicks"])
    # El origen no publica orders: dato ausente = NULL, jamas una constante.
    orders = _entero(row["orders"]) if "orders" in columnas else None

    # El origen vivo no tiene revision (comprobado 2026-10-29). Si un snapshot
    # futuro la trae, ES la version: manda sobre el hash de las metricas.
    revision = row["revision"] if "revision" in columnas else None
    if revision is None or not str(revision).strip():
        source_version = version_canonica(
            campaign_id, metric_date, moneda, cost, ad_revenue, impressions, clicks, orders
        )
    else:
        source_version = f"rev:{str(revision).strip()}"

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


def leer_filas(ruta: Path | str) -> tuple[list[FilaMeliAds], Counter]:
    """Lee y valida TODO el snapshot antes de escribir.

    Devuelve (filas canonicas, rechazos). Una fila que no cumple el contrato
    se cuenta con su motivo y NO entra a la lista: la corrida se aborta
    completa (fail-closed), asi que nunca hay escrituras parciales.
    """
    hoy = dt.datetime.now(dt.UTC).date()
    rechazos: Counter = Counter()
    filas: list[FilaMeliAds] = []
    con, columnas = _abre_origen(ruta)
    try:
        consulta = (
            f"SELECT date, campaign_id, campaign_name, clicks, impressions,"
            f" spend, ad_sales{', currency' if 'currency' in columnas else ''}"
            f"{', orders' if 'orders' in columnas else ''}"
            f"{', revision' if 'revision' in columnas else ''}"
            f" FROM {TABLA_ORIGEN} ORDER BY date, campaign_id"
        )
        for row in con.execute(consulta):
            try:
                filas.append(_fila(row, columnas, hoy))
            except MeliAdsError as exc:
                rechazos[str(exc)] += 1
    finally:
        con.close()
    return filas, rechazos


def _fuente_report_id(fila: FilaMeliAds) -> str:
    return (
        f"meli-ads:{fila.campaign_external_id}:{fila.metric_date.isoformat()}:{fila.source_version}"
    )


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
    """Devuelve (id, es_nueva, nombre_actualizado) sin degradar el nombre."""
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


def ingest_meli_ads(
    conn: psycopg.Connection,
    sqlite_path: Path | str,
) -> dict[str, int]:
    """Ingesta atomica de un snapshot de `meli_ads_campaigns_daily`.

    Abre la `ingest_run` en su propia transaccion, valida el snapshot completo,
    escribe entidades y observaciones en UNA transaccion y sella la corrida.
    En fallo: rollback del trabajo y sello `ok=false` best-effort.
    """
    filas, rechazos = leer_filas(sqlite_path)

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]

    entidades_nuevas = 0
    entidades_actualizadas = 0
    insertadas = 0
    duplicados = 0
    observed_at = dt.datetime.now(dt.UTC)
    try:
        if rechazos:
            raise MeliAdsError(
                "snapshot rechazado (cero escrituras): "
                + (", ".join(f"{n}x {motivo}" for motivo, n in sorted(rechazos.items())))
            )
        with conn.transaction():
            cache: dict[str, tuple[int, bool, bool]] = {}
            nombres = _nombres_vigentes(filas)
            for fila in filas:
                entidad = cache.get(fila.campaign_external_id)
                if entidad is None:
                    entidad = _entidad(
                        conn,
                        fila.campaign_external_id,
                        nombres.get(fila.campaign_external_id),
                    )
                    cache[fila.campaign_external_id] = entidad
                    entidades_nuevas += int(entidad[1])
                    entidades_actualizadas += int(entidad[2])
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
                        _fuente_report_id(fila),
                        run_id,
                    ),
                )
                if cursor.rowcount:
                    insertadas += 1
                else:
                    duplicados += 1

            skips: Counter = Counter()
            if duplicados:
                skips["snapshot ya ingerido (dedupe source_report_id)"] = duplicados
            skip_reason = _formato_skip_reason(skips)
            _sellar_run(
                conn,
                run_id,
                ok=True,
                rows_written=insertadas,
                rows_skipped=duplicados,
                skip_reason=skip_reason,
            )
    except BaseException as exc:
        # El with de arriba ya hizo rollback del trabajo. La run se sella como
        # fallo si la conexion sigue viva (best-effort). BaseException: un
        # Ctrl-C tambien deja la corrida sellada, no abierta.
        motivo = scrub(str(exc)) or type(exc).__name__
        try:
            with conn.transaction():
                _sellar_run(
                    conn,
                    run_id,
                    ok=False,
                    rows_written=0,
                    rows_skipped=sum(rechazos.values()) or 1,
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

    return {
        "run_id": run_id,
        "filas_origen": len(filas) + sum(rechazos.values()),
        "rows_written": insertadas,
        "rows_skipped": duplicados,
        "duplicados": duplicados,
        "rechazos": sum(rechazos.values()),
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
