"""Pipeline de metricas Amazon Ads (reporting v3 asincrono) -> ads_metric_observation.

ORBIT 03, task 1.3. NO toca app.ads.client: su superficie sellada (get,
create_report, get_report, download) ya cubre todo el camino.

EVIDENCIA DE LA CORRIDA REAL (2026-08-22, sondeo con credenciales reales;
todas las formas de abajo fueron verificadas en vivo):

- POST /reporting/reports (metodo `create_report` del cliente, ya existente,
  application/json aceptado) responde 200 con {"reportId": ..., "status":
  "PENDING"}. `groupBy` es ARRAY y `columns` son STRINGS: con objetos en
  columns la API responde 400.
- GET /reporting/reports/{reportId} (`get_report`, sin Accept especial) trae
  status PENDING|PROCESSING|COMPLETED|FAILED, failureReason, url,
  urlExpiresAt y fileSize. Un reporte real tardo ~40s en pasar a COMPLETED.
- `download(url)` trae gzip; al descomprimir es un ARRAY JSON de filas
  {"date":"2026-08-20","campaignId":138625505369345(NUMERO),"cost":9.25,
  "purchases30d":0,"sales30d":118.0,"attributedSalesSameSku30d":0,"clicks":25,
  "impressions":1660} -- solo filas con actividad. Se tolera que el cuerpo
  venga SIN gzip (se distingue por los magic bytes 1f 8b).
- Report types verificados: campanas "spCampaigns"/groupBy ["campaign"] (rango
  max 31 dias, retencion 95 dias); keywords "spTargeting"/groupBy ["targeting"]
  con filters [{"field":"keywordType","values":["BROAD","PHRASE","EXACT"]}];
  targets igual que keywords pero con values ["TARGETING_EXPRESSION",
  "TARGETING_EXPRESSION_PREDEFINED"], y la fila trae "keywordId" que ES el
  targetId (verificado: 13/13 ids del reporte == targetIds de
  /sp/targets/list) -> kind "product_target" con external_id=str(keywordId).
- Metricas (las mismas en los tres): impressions, clicks, cost, purchases30d,
  sales30d, attributedSalesSameSku30d. Columnas validas confirmadas por la API
  (52 para spCampaigns): sales30d y attributedSalesSameSku30d existen;
  "salesSameSku30d" NO existe.
- Moneda: las filas NO traen moneda; es la del perfil (us->USD, mx->MXN). El
  trigger metric_moneda_de_plataforma la sella en la base.
- Sondeo 2026-08-23 01:15 UTC: la API sirve el dia de marketplace EN CURSO
  etiquetado con su misma fecha (reporte de "hoy" devolvio 6 filas). El
  riesgo de vintage parcial del ultimo dia queda acotado por la guarda de
  frescura del motor (ventana termina en max(metric_date)-3d) y la
  bitemporalidad; nota operativa para el cron de 4.2: la tirada diaria debe
  re-incluir D-1 para que todo dia cierre con observacion post-cierre
  (dedupe-safe).

Arquitectura (regla 1, igual que app.ads.structure): IO de API separada de IO
de DB, y fase de API COMPLETA antes de la fase de DB (ninguna transaccion de
base queda abierta durante el polling de reportes, que tarda minutos) pero
ENVUELTA: todo fallo de la fase API abre una run y la sella ok=false antes de
re-lanzar (toda invocacion deja fila auditable en ingest_run):

    evaluar_perfiles(client)    (de structure: unica fuente del gate; los
                                rechazados quedan como warning + evidencia)
    solicitar_reporte / esperar_reporte / descargar_filas   (IO de API)
    ingest_metrics(conn, ...)    (IO de DB, un reporte a la vez)
    sync_metrics(conn, client, ...)   (orquestador: 3 reportes por perfil aceptado)

Decisiones selladas de esta task:

- NO hay reporte de ad groups: nada aguas abajo lo consume (regla 1); si un
  dia se necesitara, se agrega su cfg al REPORTES_CFG y nada mas cambia.
- UNA ingest_run por llamada a sync_metrics (source amazon_ads_reports_v3),
  sellada en exito y en fallo igual que sync_structure: nace abierta en su
  propia transaccion, el trabajo va en otra, y en fallo hay rollback atomico
  + sello best-effort ok=false con rows_skipped=0 EXPLICITO (la columna es
  NOT NULL y su DEFAULT no aplica en UPDATE; una corrida que no termino su
  trabajo tiene verdad contable 0, no NULL). Los fallos de la FASE API
  (perfiles, crear, poll, descarga) tambien dejan run sellada ok=false con
  el error: sin eso, una corrida que revienta antes de la fase DB no dejaba
  rastro contable (hallazgo cross-review codex).
- Cero perfiles aceptados NO es exito: la run se abre y sella ok=false con
  "ningun perfil aceptado: <motivos de los rechazados>" (o "...
  /v2/profiles devolvio 0 perfiles" si la lista vino vacia). Sin esto,
  Amazon moviendo /v2/profiles dejaba corridas ok=true con 0 filas: el verde
  falso que el COMMENT de ingest_run denuncia (hallazgo grok).
- Un fallo en CUALQUIER reporte (creacion, poll FAILED o agotado, descarga)
  aborta la corrida entera: fail-closed. Re-correr es seguro: el indice
  parcial metric_dedupe_reporte hace imposible duplicar las filas de un mismo
  reporte aunque cambie observed_at.
- Descargas con tope (hallazgo codex): fileSize > MAX_BYTES_REPORTE en el
  poll -> error ANTES de descargar; contenido descargado > MAX_BYTES_REPORTE
  -> mismo error. Un backfill legitimo de 31 dias son unos pocos MB; 512 MB
  solo lo alcanza un gzip anormal.
- observed_at la fija la ingesta como now() (reloj de la DB); el invariante
  observado >= metric_date NO vive en un CHECK de la base (un cast
  date::timestamptz en CHECK se evalua segun la TimeZone de cada sesion; ver
  el comentario de la tabla) -- es responsabilidad DECLARADA de este codigo:
  el guard de fecha futura usa el MISMO reloj que escribe observed_at (la
  fecha de now() calculada con UTC FIJADO en la expresion, en la misma
  transaccion que abre la run; hallazgo grok). El skew Python-DB ya no puede
  romper el invariante: solo queda el reloj de la DB.
- metric_date fuera del rango solicitado -> skip: una descarga equivocada no
  puede contaminar fechas ajenas al reporte pedido (hallazgo codex).
- Fila sin NINGUNA metrica -> skip: una observacion vacia posterior puede
  llegar a v_metric_latest y TAPAR datos completos anteriores; fila sin
  ningun dato no se escribe (espiritu de regla 3; hallazgo codex).
- Pre-valida revenue_same_sku <= ad_revenue ANTES del INSERT: la API puede
  traer el par incoherente (clawbacks asimetricos) y el CHECK
  metric_same_sku_cabe abortaria el lote ENTERO; la fila se salta con motivo.
  El resto de los CHECKs (p.ej. metricas negativas) NO se pre-validan: dato
  asi de corrupto aborta la corrida fail-closed (sello ok=false) a proposito
  -- ese nivel de corrupcion se quiere ver, no tragar fila por fila.
- Entidad ausente en ad_entity -> skip "correr sync de estructura": las
  metricas JAMAS crean entidades (regla 1: la estructura tiene un solo dueno,
  app.ads.structure).
- Dinero Decimal via str (jamas float a NUMERIC); metrica ausente -> None
  (regla 3, la API solo trae filas con actividad y columnas pueden faltar);
  impressions/clicks/orders a int (fraccion o no numerico -> skip con motivo).

`python -m app.ads.reports`: carga AdsCredentials.from_secrets_dir()
(ORBIT_SECRETS_DIR) y la DSN de ORBIT_DSN_INGEST (via app.db.connect),
sincroniza por defecto AYER UTC (--fecha / --fecha-fin, max 31 dias, el tope
de la API) para los perfiles aceptados e imprime por stdout un resumen SIN
secretos. Sin ORBIT_DSN_INGEST -> mensaje claro y exit 2 (fail-closed, patron
structure.main). La retencion de 95 dias NO se gatea aqui: pedir mas viejo
falla en la API y eso ya es fail-closed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import logging
import os
import sys
import time
import zlib
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import psycopg

from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import (
    _SQL_ABRIR_RUN,
    PerfilAds,
    _formato_skip_reason,
    _sellar_run,
    evaluar_perfiles,
)
from app.db import connect
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "amazon_ads_reports_v3"

# Tope de rango de la API (verificado: spCampaigns max 31 dias).
MAX_RANGO_DIAS = 31

# Poll por defecto: 120 intentos x 5s = hasta 10 min por reporte (un reporte
# real tardo ~40s; el tope cubre picos de la cola de Amazon sin colgar la
# corrida para siempre).
INTENTOS_POLL = 120
ESPERA_POLL_SEGUNDOS = 5.0

# Tope de descarga (hallazgo codex): un backfill legitimo de 31 dias son unos
# pocos MB; 512 MB solo lo alcanza un gzip anormal (bug de la API o payload
# corrupto). Dos guardas fail-closed: el fileSize del poll ANTES de descargar
# y el tamano real tras descargar.
MAX_BYTES_REPORTE = 512 * 1024 * 1024

# Metricas comunes a los tres reportes (verificado en vivo): el mapeo a
# columnas de ads_metric_observation es:
#   cost -> cost, sales30d -> ad_revenue,
#   attributedSalesSameSku30d -> revenue_same_sku, purchases30d -> orders
_METRICAS = (
    "impressions",
    "clicks",
    "cost",
    "purchases30d",
    "sales30d",
    "attributedSalesSameSku30d",
)

# Config de los reportes (formas VERIFICADAS en vivo el 2026-08-22). Cada cfg
# declara ademas como ingerir sus filas: kind de ad_entity y el campo de la
# fila que trae el external id. NO hay cfg de ad groups (regla 1, docstring).
CAMPANAS_CFG = {
    "nombre": "campanas",
    "reportTypeId": "spCampaigns",
    "groupBy": ["campaign"],
    "columns": ["date", "campaignId", *_METRICAS],
    "kind": "campaign",
    "id_field": "campaignId",
}
KEYWORDS_CFG = {
    "nombre": "keywords",
    "reportTypeId": "spTargeting",
    "groupBy": ["targeting"],
    "filters": [{"field": "keywordType", "values": ["BROAD", "PHRASE", "EXACT"]}],
    "columns": ["date", "campaignId", "adGroupId", "keywordId", *_METRICAS],
    "kind": "keyword",
    "id_field": "keywordId",
}
TARGETS_CFG = {
    "nombre": "targets",
    "reportTypeId": "spTargeting",
    "groupBy": ["targeting"],
    "filters": [
        {
            "field": "keywordType",
            "values": ["TARGETING_EXPRESSION", "TARGETING_EXPRESSION_PREDEFINED"],
        }
    ],
    # VERIFICADO en vivo: en el reporte spTargeting de targets, el "keywordId"
    # de la fila ES el targetId de /sp/targets/list (13/13 ids coincidieron).
    "columns": ["date", "campaignId", "adGroupId", "keywordId", *_METRICAS],
    "kind": "product_target",
    "id_field": "keywordId",
}
REPORTES_CFG = (CAMPANAS_CFG, KEYWORDS_CFG, TARGETS_CFG)

# Resolucion de entidades en un solo SELECT por reporte (las filas de un
# reporte comparten platform/kind; los external_id van de a miles).
_SQL_ENTIDADES = """
    SELECT external_id, id FROM ad_entity
    WHERE platform = %s AND kind = %s AND external_id = ANY(%s)
"""

# El "hoy" del guard de fecha futura sale del MISMO reloj que escribe
# observed_at (now() de la DB, constante dentro de la transaccion) con UTC
# FIJADO en la expresion: misma defensa que los triggers del esquema
# (hallazgo grok). Se calcula en la misma transaccion que abre la run.
_SQL_FECHA_HOY = "SELECT (now() AT TIME ZONE 'UTC')::date"

# Append-only (regla 5): JAMAS UPDATE. El ON CONFLICT apunta al indice parcial
# metric_dedupe_reporte (re-ingestar el MISMO reporte no duplica aunque cambie
# observed_at); sin RETURNING row la fila fue absorbida por el dedupe y cuenta
# como rows_skipped con motivo. observed_at es now() del servidor: lo fija la
# ingesta, no el payload. RETURNING ad_entity_id (la tabla NO tiene columna id:
# su PK es compuesta; el RETURNING solo distingue insert de absorbida).
_SQL_INSERT_METRICA = """
    INSERT INTO ads_metric_observation
        (ad_entity_id, metric_date, observed_at, metric_currency, cost,
         ad_revenue, revenue_same_sku, impressions, clicks, orders,
         source_report_id, ingest_run_id)
    VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ad_entity_id, metric_date, source_report_id)
        WHERE source_report_id IS NOT NULL
    DO NOTHING
    RETURNING ad_entity_id
"""


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
class ResultadoIngesta:
    """Outcome contable de UN reporte ingerido.

    `skips` (motivo -> contador) se expone para que sync_metrics acumule sin
    re-parsear el texto de skip_reason (una sola fuente del formato:
    _formato_skip_reason de structure).
    """

    report_id: str
    filas: int
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    skips: Counter[str]


@dataclass
class ResumenReporte:
    """Evidencia por (perfil, reporte) para el resumen del __main__."""

    profile_id: int
    platform: str | None
    nombre: str
    report_id: str
    status: str
    filas: int
    rows_written: int
    rows_skipped: int
    skip_reason: str | None


@dataclass
class ResultadoSync:
    """Outcome contable de la corrida (espejo de la ingest_run sellada)."""

    run_id: int
    ok: bool
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    reportes: list[ResumenReporte]
    # Evidencia de perfiles rechazados (hallazgo reviewer): una corrida con
    # cero perfiles aceptados NO debe poder pasar en silencio.
    perfiles_rechazados: list[PerfilAds] = field(default_factory=list)


# ---------------------------------------------------------------------------
# IO de API: solicitar / esperar / descargar
# ---------------------------------------------------------------------------


def _json_de(resp, metodo: str, path: str):
    try:
        return resp.json()
    except ValueError:
        raise AdsReportsError(f"respuesta no-JSON de {metodo} {path}") from None


def solicitar_reporte(
    client: AdsClient,
    perfil: PerfilAds,
    reporte_cfg: dict,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> str:
    """POST /reporting/reports -> report_id (forma del body verificada en vivo).

    `groupBy` ARRAY y `columns` STRINGS (con objetos la API da 400); los
    filters solo existen para keywords/targets (spTargeting).
    """
    configuration: dict = {
        "adProduct": "SPONSORED_PRODUCTS",
        "groupBy": list(reporte_cfg["groupBy"]),
        "columns": list(reporte_cfg["columns"]),
        "reportTypeId": reporte_cfg["reportTypeId"],
        "timeUnit": "DAILY",
        "format": "GZIP_JSON",
    }
    if "filters" in reporte_cfg:
        configuration["filters"] = reporte_cfg["filters"]
    body = {
        "name": (
            f"orbit-{reporte_cfg['nombre']}-{perfil.profile_id}"
            f"-{fecha_ini.isoformat()}_{fecha_fin.isoformat()}"
        ),
        "startDate": fecha_ini.isoformat(),
        "endDate": fecha_fin.isoformat(),
        "configuration": configuration,
    }
    data = _json_de(
        client.create_report(body, profile_id=perfil.profile_id),
        "POST",
        "/reporting/reports",
    )
    report_id = data.get("reportId") if isinstance(data, dict) else None
    if not isinstance(report_id, str) or not report_id:
        raise AdsReportsError("respuesta de POST /reporting/reports sin reportId")
    return report_id


def esperar_reporte(
    client: AdsClient,
    perfil: PerfilAds,
    report_id: str,
    *,
    intentos: int = INTENTOS_POLL,
    espera: float = ESPERA_POLL_SEGUNDOS,
    sleep=time.sleep,
) -> str:
    """Poll de GET /reporting/reports/{id} hasta COMPLETED -> url de descarga.

    FAILED -> AdsReportsError con failureReason (redactado por scrub al
    construir la excepcion); statuses desconocidos y polls agotados tambien
    son error claro. fileSize (campo verificado en vivo) mayor a
    MAX_BYTES_REPORTE -> error ANTES de descargar (hallazgo codex: un gzip
    anormal no debe entrar a memoria). El sleep se inyecta para los tests.
    """
    path = f"/reporting/reports/{report_id}"
    for intento in range(1, intentos + 1):
        data = _json_de(client.get_report(report_id, profile_id=perfil.profile_id), "GET", path)
        status = data.get("status") if isinstance(data, dict) else None
        if status == "COMPLETED":
            url = data.get("url")
            if not isinstance(url, str) or not url:
                raise AdsReportsError(f"reporte {report_id} COMPLETED sin url de descarga")
            file_size = data.get("fileSize")
            if (
                isinstance(file_size, (int, float))
                and not isinstance(file_size, bool)
                and file_size > MAX_BYTES_REPORTE
            ):
                # fileSize ausente o no numerico NO se valida (regla 3: sin
                # dato no se inventa); el tope real lo garantiza descargar.
                raise AdsReportsError(
                    f"reporte {report_id} con fileSize {int(file_size)} bytes excede el "
                    f"tope de {MAX_BYTES_REPORTE} (descarga anormal): la corrida aborta "
                    "fail-closed"
                )
            return url
        if status == "FAILED":
            raise AdsReportsError(f"reporte {report_id} FAILED: {data.get('failureReason')!r}")
        if status not in ("PENDING", "PROCESSING"):
            raise AdsReportsError(f"reporte {report_id} con status desconocido: {status!r}")
        if intento < intentos:
            sleep(espera)
    raise AdsReportsError(
        f"reporte {report_id} sin COMPLETED tras {intentos} intentos de poll "
        f"({espera}s entre intentos): la corrida aborta fail-closed"
    )


def descargar_filas(client: AdsClient, url: str) -> list[dict]:
    """download(url) -> array JSON de filas del reporte.

    El cuerpo viene gzip (format GZIP_JSON) pero se tolera sin gzip: se
    distingue por magic bytes. Cualquier otra forma (no gzip corrupto, no
    utf-8, no array JSON de objetos) es error claro, nunca una fila inventada.
    El contenido descargado mayor a MAX_BYTES_REPORTE es error fail-closed
    (hallazgo codex): la segunda guarda, para cuando el poll no trajo
    fileSize o mintio.
    """
    contenido = client.download(url).content
    if len(contenido) > MAX_BYTES_REPORTE:
        raise AdsReportsError(
            f"descarga del reporte excede el tope de {MAX_BYTES_REPORTE} bytes "
            f"(recibidos {len(contenido)}): la corrida aborta fail-closed"
        )
    if contenido[:2] == b"\x1f\x8b":
        try:
            texto = gzip.decompress(contenido).decode("utf-8")
        except (OSError, EOFError, zlib.error, UnicodeDecodeError):
            raise AdsReportsError("gzip del reporte corrupto o no utf-8") from None
    else:
        try:
            texto = contenido.decode("utf-8")
        except UnicodeDecodeError:
            raise AdsReportsError("descarga del reporte no es gzip ni utf-8") from None
    try:
        filas = json.loads(texto)
    except ValueError:
        raise AdsReportsError("descarga del reporte no es JSON") from None
    if not isinstance(filas, list) or any(not isinstance(fila, dict) for fila in filas):
        raise AdsReportsError("descarga del reporte no es un array JSON de filas")
    return filas


# ---------------------------------------------------------------------------
# Planificacion pura (sin DB): filas validas + skips con motivo
# ---------------------------------------------------------------------------


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
    """Convierte un contador a int. None -> None; fraccion o no numerico ->
    ValueError (truncar 3.5 a 3 en silencio seria inventar dato)."""
    if valor is None:
        return None
    numero = _decimal_reporte(valor, campo)
    assert numero is not None  # _decimal_reporte solo devuelve None con None
    if numero != numero.to_integral_value():
        raise ValueError(f"{campo} fraccionario")
    return int(numero)


def _planea_filas(
    reporte_cfg: dict,
    filas: list[dict],
    *,
    hoy: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> tuple[list[_FilaPlano], Counter[str]]:
    """Valida cada fila ANTES de tocar la base.

    Gates (todos dejan la fila fuera con motivo, ninguno aborta la corrida):
    id ausente, date invalida, fecha futura (la fecha del payload NO puede ser
    futura: observed_at lo fija la ingesta en now(), el invariante observado
    >= hecho es responsabilidad declarada de este modulo), metric_date fuera
    del rango solicitado (una descarga equivocada no contamina fechas ajenas
    al reporte pedido; hallazgo codex), metricas no numericas/fraccionarias,
    fila sin NINGUNA metrica (una observacion vacia posterior llegaria a
    v_metric_latest y taparia datos completos anteriores; hallazgo codex) y
    revenue_same_sku > ad_revenue (el CHECK metric_same_sku_cabe abortaria el
    lote entero si llegara al INSERT).
    """
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
        raw_date = fila.get("date")
        try:
            if not isinstance(raw_date, str):
                raise ValueError
            metric_date = dt.date.fromisoformat(raw_date)
        except ValueError:
            skips["fila con date invalida"] += 1
            logger.debug("date invalida en fila de %s: %r", etiqueta, raw_date)
            continue
        if metric_date > hoy:
            skips["fila con metric_date futura"] += 1
            logger.debug("metric_date futura en fila de %s: %s > %s", etiqueta, metric_date, hoy)
            continue
        if metric_date < fecha_ini or metric_date > fecha_fin:
            skips["fila con metric_date fuera del rango solicitado"] += 1
            logger.debug(
                "metric_date %s fuera del rango %s..%s en %s %s",
                metric_date,
                fecha_ini,
                fecha_fin,
                etiqueta,
                external_id,
            )
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
            # Vocabulario CERRADO de motivos (hallazgo reviewer): el detalle
            # por fila va al log; la clave del contador tiene que tener
            # cardinalidad acotada o un backfill de 31 dias llena skip_reason
            # de miles de claves 1x.
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


# ---------------------------------------------------------------------------
# IO de DB: ingest_metrics
# ---------------------------------------------------------------------------


def ingest_metrics(
    conn: psycopg.Connection,
    perfil: PerfilAds,
    reporte_cfg: dict,
    report_id: str,
    filas: list[dict],
    *,
    run_id: int,
    hoy: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> ResultadoIngesta:
    """Inserta las filas de UN reporte en ads_metric_observation (append-only).

    Unidad contable: la FILA del reporte. Insertada -> rows_written; absorbida
    por el dedupe del indice parcial metric_dedupe_reporte -> rows_skipped
    "fila duplicada del reporte <id>"; entidad ausente en ad_entity ->
    rows_skipped "correr sync de estructura" (las metricas jamas crean
    entidades). La moneda es la del perfil (us->USD, mx->MXN) y la sella el
    trigger metric_moneda_de_plataforma en la base.

    `run_id` es la ingest_run que abre sync_metrics (UNA por corrida, no una
    por reporte). `hoy` es la fecha del reloj de la DB (UTC fijado) calculada
    en la misma transaccion que abre la run: el guard de fecha futura usa el
    MISMO reloj que escribe observed_at con now() (hallazgo grok).
    `fecha_ini`/`fecha_fin` delimitan el rango solicitado: una fila con
    metric_date fuera del rango (descarga equivocada) se salta con motivo.
    """
    if not perfil.aceptado or perfil.platform is None or perfil.moneda is None:
        raise AdsReportsError(
            f"ingest_metrics exige un perfil aceptado (profile_id={perfil.profile_id!r})"
        )
    plan, skips = _planea_filas(
        reporte_cfg, filas, hoy=hoy, fecha_ini=fecha_ini, fecha_fin=fecha_fin
    )

    # Resolucion de entidades en un solo SELECT por reporte: las filas
    # comparten (platform, kind); los ids que falten son skip, no error.
    externos = sorted({fila.external_id for fila in plan})
    ids: dict[str, int] = {}
    if externos:
        ids = {
            str(external): entidad_id
            for external, entidad_id in conn.execute(
                _SQL_ENTIDADES, (perfil.platform, reporte_cfg["kind"], externos)
            ).fetchall()
        }

    kind = reporte_cfg["kind"]
    written = 0
    for fila in plan:
        entidad_id = ids.get(fila.external_id)
        if entidad_id is None:
            # Clave generica por kind (cardinalidad acotada); el id exacto va
            # al log (hallazgo reviewer).
            skips[f"entidad ausente en ad_entity ({kind})"] += 1
            logger.debug(
                "entidad ausente en ad_entity: %s %s (correr sync de estructura)",
                kind,
                fila.external_id,
            )
            continue
        insertado = conn.execute(
            _SQL_INSERT_METRICA,
            (
                entidad_id,
                fila.metric_date,
                perfil.moneda,
                fila.cost,
                fila.ad_revenue,
                fila.revenue_same_sku,
                fila.impressions,
                fila.clicks,
                fila.orders,
                report_id,
                run_id,
            ),
        ).fetchone()
        if insertado is None:
            skips[f"fila duplicada del reporte {report_id}"] += 1
            continue
        written += 1

    return ResultadoIngesta(
        report_id=report_id,
        filas=len(filas),
        rows_written=written,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
        skips=skips,
    )


# ---------------------------------------------------------------------------
# Orquestador + rango de fechas
# ---------------------------------------------------------------------------


def _validar_rango(fecha_ini: dt.date, fecha_fin: dt.date) -> None:
    """El rango de los reportes: [fecha_ini, fecha_fin], max MAX_RANGO_DIAS."""
    dias = (fecha_fin - fecha_ini).days + 1
    if dias < 1:
        raise ValueError(f"rango invertido: {fecha_ini} > {fecha_fin}")
    if dias > MAX_RANGO_DIAS:
        raise ValueError(f"rango de {dias} dias excede el maximo de {MAX_RANGO_DIAS} de la API")


def _run_de_fallo_de_api(conn: psycopg.Connection, exc: BaseException) -> None:
    """Fallo de la fase API: abre una run y la sella ok=false (best-effort).

    Toda invocacion deja fila auditable en ingest_run (hallazgo codex): sin
    esto, una corrida que revienta en perfiles/crear/poll/descarga no dejaba
    rastro contable. Abrir y sellar van en UNA transaccion: nunca existe una
    run abierta de un fallo de fase API. Si la conexion tambien esta muerta,
    se deja el rastro en el log y la excepcion ORIGINAL sube igual.
    """
    try:
        with conn.transaction():
            run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]
            _sellar_run(
                conn,
                run_id,
                ok=False,
                rows_skipped=0,
                skip_reason=scrub(str(exc)) or type(exc).__name__,
            )
    except Exception:
        logger.warning(
            "fallo de fase API sin run auditable (sello tambien fallo): %s",
            scrub(str(exc)),
        )


def sync_metrics(
    conn: psycopg.Connection,
    client: AdsClient,
    *,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
    sleep=time.sleep,
) -> ResultadoSync:
    """Orquestador: perfiles aceptados -> 3 reportes por perfil -> ingesta.

    Fase de API COMPLETA antes de la fase de DB (ninguna transaccion queda
    abierta durante el polling) y ENVUELTA: todo fallo de esa fase abre una
    run y la sella ok=false con el error antes de re-lanzar (toda invocacion
    deja fila auditable; hallazgo codex). Cero perfiles aceptados NO es
    excepcion ni exito: la run se sella ok=false con los motivos de rechazo
    (hallazgo grok). La fase de DB abre la run en su propia transaccion y
    calcula ahi mismo el "hoy" del guard con now() de la DB (UTC fijado);
    en fallo, rollback atomico del trabajo + sello best-effort ok=false
    (patron sync_structure). Un fallo en cualquier reporte aborta la corrida
    entera (fail-closed): re-correr es seguro por el dedupe de source_report_id.
    """
    _validar_rango(fecha_ini, fecha_fin)

    descargados: list[tuple[PerfilAds, dict, str, list[dict]]] = []
    perfiles_rechazados: list[PerfilAds] = []
    try:
        # Evidencia de perfiles RECHAZADOS al log (hallazgo reviewer): sin esto,
        # Amazon moviendo /v2/profiles dejaria corridas ok=true con 0 filas y sin
        # rastro -- el verde falso que el COMMENT de ingest_run denuncia. La
        # corrida sigue con los aceptados (fail-closed por perfil, no global).
        todos = evaluar_perfiles(client)
        for perfil in todos:
            if not perfil.aceptado:
                logger.warning(
                    "perfil %s rechazado, sin metricas esta corrida: %s",
                    perfil.profile_id,
                    perfil.motivo,
                )
        perfiles = [perfil for perfil in todos if perfil.aceptado]
        perfiles_rechazados = [perfil for perfil in todos if not perfil.aceptado]
        if not perfiles:
            # Verde falso (hallazgo grok): sin perfiles aceptados la corrida se
            # sella ok=false CON los motivos, jamas ok=true con 0 filas.
            motivo = (
                "ningun perfil aceptado: /v2/profiles devolvio 0 perfiles"
                if not perfiles_rechazados
                else "ningun perfil aceptado: "
                + "; ".join(perfil.motivo or "sin motivo" for perfil in perfiles_rechazados)
            )
            with conn.transaction():
                run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]
                _sellar_run(conn, run_id, ok=False, rows_skipped=0, skip_reason=motivo)
            return ResultadoSync(
                run_id=run_id,
                ok=False,
                rows_written=0,
                rows_skipped=0,
                skip_reason=motivo,
                reportes=[],
                perfiles_rechazados=perfiles_rechazados,
            )
        for perfil in perfiles:
            for cfg in REPORTES_CFG:
                report_id = solicitar_reporte(client, perfil, cfg, fecha_ini, fecha_fin)
                url = esperar_reporte(client, perfil, report_id, sleep=sleep)
                descargados.append((perfil, cfg, report_id, descargar_filas(client, url)))
    except BaseException as exc:
        _run_de_fallo_de_api(conn, exc)
        raise

    with conn.transaction():
        run_id = conn.execute(_SQL_ABRIR_RUN, (SOURCE,)).fetchone()[0]
        # El "hoy" del guard sale del MISMO reloj que escribe observed_at
        # (now() de la DB, constante dentro de la transaccion) con UTC FIJADO
        # en la expresion: el skew Python-DB ya no puede romper el invariante
        # observado >= hecho (hallazgo grok).
        hoy = conn.execute(_SQL_FECHA_HOY).fetchone()[0]

    escritos = 0
    skips: Counter[str] = Counter()
    reportes: list[ResumenReporte] = []
    try:
        with conn.transaction():
            for perfil, cfg, report_id, filas in descargados:
                resultado = ingest_metrics(
                    conn,
                    perfil,
                    cfg,
                    report_id,
                    filas,
                    run_id=run_id,
                    hoy=hoy,
                    fecha_ini=fecha_ini,
                    fecha_fin=fecha_fin,
                )
                escritos += resultado.rows_written
                skips.update(resultado.skips)
                reportes.append(
                    ResumenReporte(
                        profile_id=perfil.profile_id,
                        platform=perfil.platform,
                        nombre=cfg["nombre"],
                        report_id=report_id,
                        # solo se resume lo que llego a descargarse: un
                        # reporte no-COMPLETED aborta la corrida entera.
                        status="COMPLETED",
                        filas=resultado.filas,
                        rows_written=resultado.rows_written,
                        rows_skipped=resultado.rows_skipped,
                        skip_reason=resultado.skip_reason,
                    )
                )
            _sellar_run(
                conn,
                run_id,
                ok=True,
                rows_written=escritos,
                rows_skipped=sum(skips.values()),
                skip_reason=_formato_skip_reason(skips),
            )
    except BaseException as exc:
        # El with de arriba ya hizo rollback del trabajo (nada a medias); la
        # run abierta se sella como fallo si la conexion sigue viva
        # (best-effort). BaseException, no solo Exception: un Ctrl-C en una
        # corrida de minutos tambien deja la run sellada, no abierta. Si el
        # sello TAMBIEN falla, se deja rastro en el log (patron sync_structure).
        try:
            with conn.transaction():
                _sellar_run(
                    conn,
                    run_id,
                    ok=False,
                    # rows_skipped=0 EXPLICITO: la columna es NOT NULL y su
                    # DEFAULT no aplica en UPDATE; 0 es la verdad contable de
                    # una corrida que no llego a terminar su trabajo.
                    rows_skipped=0,
                    skip_reason=scrub(str(exc)) or type(exc).__name__,
                )
        except Exception:
            logger.warning(
                "ingest_run %s quedo ABIERTA: fallo tambien su sello de fallo; "
                "el error original de la corrida era: %s",
                run_id,
                scrub(str(exc)),
            )
        raise

    return ResultadoSync(
        run_id=run_id,
        ok=True,
        rows_written=escritos,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
        reportes=reportes,
        perfiles_rechazados=perfiles_rechazados,
    )


# ---------------------------------------------------------------------------
# __main__: corrida real (resumen por stdout, sin secretos)
# ---------------------------------------------------------------------------


def _rango_desde_args(
    fecha: str | None, fecha_fin: str | None, hoy: dt.date
) -> tuple[dt.date, dt.date]:
    """Args del CLI -> (fecha_ini, fecha_fin). Puro y testeable.

    Default: ayer UTC (un solo dia). --fecha sola: ese dia. --fecha-fin:
    rango cerrado (requiere --fecha). Cualquier problema es ValueError claro.
    """
    if fecha_fin is not None and fecha is None:
        raise ValueError("--fecha-fin requiere --fecha")

    def parsear(texto: str, etiqueta: str) -> dt.date:
        try:
            return dt.date.fromisoformat(texto)
        except ValueError:
            raise ValueError(f"{etiqueta} no es una fecha YYYY-MM-DD: {texto!r}") from None

    fecha_ini = parsear(fecha, "--fecha") if fecha else hoy - dt.timedelta(days=1)
    fecha_limite = parsear(fecha_fin, "--fecha-fin") if fecha_fin is not None else fecha_ini
    _validar_rango(fecha_ini, fecha_limite)
    return fecha_ini, fecha_limite


def _imprimir_resumen(fecha_ini: dt.date, fecha_fin: dt.date, resultado: ResultadoSync) -> None:
    print(f"== Sync de metricas Amazon Ads ({SOURCE}) ==")
    print(f"rango {fecha_ini} .. {fecha_fin} (UTC)")
    for perfil in resultado.perfiles_rechazados:
        print(f"  perfil {perfil.profile_id} RECHAZADO (sin metricas): {perfil.motivo}")
    for resumen in resultado.reportes:
        print(
            f"  perfil {resumen.profile_id} {resumen.platform} {resumen.nombre}: "
            f"report_id={resumen.report_id} status={resumen.status} "
            f"filas={resumen.filas} written={resumen.rows_written} "
            f"skipped={resumen.rows_skipped}"
        )
        if resumen.skip_reason:
            print(f"    skip_reason: {resumen.skip_reason}")
    print(
        f"ingest_run {resultado.run_id}: ok={resultado.ok} "
        f"rows_written={resultado.rows_written} rows_skipped={resultado.rows_skipped}"
    )
    if resultado.skip_reason:
        print(f"skip_reason: {resultado.skip_reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sincroniza metricas de Amazon Ads (reporting v3) a ads_metric_observation"
    )
    parser.add_argument("--fecha", help="YYYY-MM-DD del dia inicial (default: ayer UTC)")
    parser.add_argument(
        "--fecha-fin", help="YYYY-MM-DD del dia final (rango max 31 dias; requiere --fecha)"
    )
    args = parser.parse_args(argv)

    try:
        fecha_ini, fecha_fin = _rango_desde_args(
            args.fecha, args.fecha_fin, dt.datetime.now(dt.UTC).date()
        )
    except ValueError as exc:
        print(f"rango invalido: {exc}", file=sys.stderr)
        return 2

    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print(
            "ORBIT_DSN_INGEST no esta definido: no se pueden escribir las metricas (fail-closed)",
            file=sys.stderr,
        )
        return 2
    try:
        client = AdsClient(AdsCredentials.from_secrets_dir())
        conn = connect(dsn)
        try:
            resultado = sync_metrics(conn, client, fecha_ini=fecha_ini, fecha_fin=fecha_fin)
        finally:
            conn.close()
    except Exception as exc:
        print(
            "sync de metricas fallo (la ingest_run quedo sellada ok=false cuando "
            f"fue posible): {scrub(str(exc))}",
            file=sys.stderr,
        )
        return 1
    _imprimir_resumen(fecha_ini, fecha_fin, resultado)
    if not resultado.ok:
        # Cero perfiles aceptados: la run quedo sellada ok=false en la base;
        # el proceso tambien reporta fallo al cron (la excepcion pura sale por
        # el except de arriba con el mismo exit 1).
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
