"""Pipeline de metricas Amazon Ads (reporting v3 asincrono) -> ads_metric_observation
+ search_term_observation.

ORBIT 03, tasks 1.3 (metricas) y 1.4 (search terms). NO toca app.ads.client:
su superficie sellada (get, create_report, get_report, download) ya cubre todo
el camino.

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
- MADURACION DE LA ATRIBUCION (hallazgo codex, cross-review 1.4): las
  metricas atribuidas de un dia SIGUEN CRECIENDO despues de ese dia --
  purchases7d/sales7d de search terms maduran 7 dias, las 30d de metricas
  maduran 30. Una tirada diaria que solo pidiera "ayer" congelaria cada dia
  con ~1 dia de maduracion: orders/ad_revenue subestimados y una higiene que
  podria negativizar terminos que convirtieron despues. La bitemporalidad es
  el remedio: re-pedir un dia ya ingerido crea una observacion NUEVA (report
  id distinto) y el motor colapsa a la mas reciente. Requisito operacional
  del cron de 4.2: la tirada diaria re-incluye la ventana de maduracion --
  MINIMO D-8..D-1 por los 7d de search terms. Sello 4.2: el cron pide
  D-31..D-1 (31 dias = tope de un request de reporting v3, cubre las
  columnas 30d; el lookback maximo medido en 1.5 es 95d y queda para
  backfill, no para la cadencia diaria). Ver docs/DEPLOY.md. Sin esa
  ventana, los cortes de higiene deciden sobre datos inmaduros (regla 6).

Arquitectura (regla 1, igual que app.ads.structure): IO de API separada de IO
de DB, y fase de API COMPLETA antes de la fase de DB (ninguna transaccion de
base queda abierta durante el polling de reportes, que tarda minutos) pero
ENVUELTA: todo fallo de la fase API abre una run y la sella ok=false antes de
re-lanzar (toda invocacion deja fila auditable en ingest_run):

    evaluar_perfiles(client)    (de structure: unica fuente del gate; los
                                rechazados quedan como warning + evidencia)
    descarga(...)   (IO de API: app.ads.descarga pide y sondea todos los
                     reportes de una vez, con repetidos -r1/-r2 y plazo de
                     2 h 30 min; los grupos los arma ingest diario)
    escribir_grupo(conn, ...)   (IO de DB: una transaccion por grupo, un
                                 reporte a la vez)
    solicitar_reporte / descargar_filas   (primitivas que consume descarga)

Decisiones selladas de esta task:

- NO hay reporte de ad groups: nada aguas abajo lo consume (regla 1); si un
  dia se necesitara, se agrega su cfg al REPORTES_CFG y nada mas cambia.
- UNA ingest_run por grupo de descarga (source amazon_ads_reports_v3 para
  metricas o amazon_ads_products_v3 para productos),
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
  el guard de dia cerrado usa el MISMO reloj que escribe observed_at (now()
  de la DB, en la misma transaccion que abre la run; hallazgo grok) y solo
  deja pasar hasta ultimo_dia_cerrado(plataforma, now()) de app.dia_cerrado
  (ARREGLOS 03 D-E): el dia que aun no termina en la zona del marketplace no
  se guarda. El skew Python-DB ya no puede romper el invariante: solo queda
  el reloj de la DB.
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

Search terms (task 1.4):

- 4to reporte del MISMO pipeline (un solo job del cron de 4.2, UNA ingest_run
  por corrida): SEARCH_TERMS_CFG -> tabla search_term_observation. PROVENANCE
  de la forma: la unica evidencia VERIFICADA EN VIVO es la del sistema viejo
  goncloud-MCP-2 (_request_search_term_report): reportTypeId "spSearchTerm"
  (SINGULAR "Term"), groupBy ["searchTerm"], metricas purchases7d/sales7d
  (ventana 7d: para search terms NO hay evidencia de 30d y las reglas de
  higiene del diseno v2 se calibraron sobre esa ventana). La re-verificacion
  contra la API queda para la corrida real de 1.5.
- Nuestras columns son un SUBCONJUNTO de las que el viejo verifico (date,
  adGroupId, searchTerm, clicks, cost, purchases7d, sales7d): no se piden
  campaignId/keyword/matchType/impressions porque la tabla no los guarda
  (regla "un numero, una fuente": no pedir lo que no se almacena). Mapeo:
  cost -> cost, sales7d -> ad_revenue, purchases7d -> orders, clicks ->
  clicks. La tabla NO tiene impressions ni revenue_same_sku: no se piden ni
  se guardan (y por eso no hay pre-check same_sku en el plan de terminos).
- Attachment al ad_group (kind "ad_group", id_field "adGroupId"): el
  comentario del esquema dice "campana/ad_group origen" y el reporte siempre
  trae adGroupId; la unidad de completitud del motor (2.1) es la entidad.
- Clasificador OBLIGATORIO es_asin_like: un search term ASIN-like es una
  UBICACION de pagina de producto, no una busqueda tipeada, y siempre se
  saltan en negativos (regla sealed own-ASIN; ver el COMMENT de
  search_term_observation.is_asin_like en la migracion). Patron: EXACTAMENTE
  10 caracteres alfanumericos ASCII que EMPIEZAN con "B0", case-insensitive,
  full-match sobre el termino sin espacios extremos; un "b0" embebido en un
  termino mas largo NO es ASIN. La clasificacion corre en la PLANIFICACION
  de cada fila: toda fila escrita llega clasificada. La columna es NOT NULL
  SIN DEFAULT a proposito y esta funcion es el UNICO clasificador.
- search_term se guarda STRIPPEADO de espacios extremos (normalizacion de
  identidad para la PK, no invencion de dato).
- GRANO MULTIPLE del reporte (verificado EN VIVO, sondeo 1.5 del
  2026-08-23): el mismo (adGroupId, searchTerm, date) llega en varias filas
  -- el termino convierte via keywords distintas del mismo ad group.
  Evidencia: US, reporte del 2026-08-19: 113 filas -> 110 claves, 3
  repetidas x2 (p.ej. ("153443759852430", "arras for wedding ceremony",
  "2026-08-19") x2); MX: 42 filas, 0 repetidas. First-wins perdia las
  metricas de las filas absorbidas (un orders subestimado puede generar un
  NEGATIVE_EXACT erroneo en el motor 2.3), asi que _planea_filas_terminos
  FUSIONA las filas validas de la misma clave SUMANDO metricas: los
  aportes por keyword al mismo hecho son disjuntos (sumar no inventa dato)
  hacia la identidad (ad_group, termino, fecha) del esquema sellado, la que
  el motor consume. DISYUNCION VERIFICADA EN VIVO (sondeo con la columna
  keyword, cross-review 1.5): las 3 claves repetidas del 2026-08-19 eran
  aportes por keywords DISTINTAS con vectores de metricas distintos, 0
  copias identicas; el payload que guardamos NO trae keywordId, asi que la
  disyuncion no es re-verificable desde los datos almacenados (hallazgo
  reviewer). SEMANTICA DE None EN LA FUSION -- DECISION SELLADA (review del
  PR #11, major; supersede el mensaje del commit e1492bd): un campo ausente
  es DESCONOCIDO, no cero; si ALGUN aporte trajo la metrica ausente, la
  fusionada queda None (envenenamiento: la suma con sumando desconocido es
  desconocida, y 0+None=0 en orders dispararia NEGATIVE_EXACT contra un
  termino convertidor -- el bug exacto que la fusion vino a matar; regla 3).
  Solo se suma cuando TODOS los aportes la reportaron. Y si la fusion deja
  la clave SIN NINGUNA metrica (par complementario envenenado), la clave NO
  se escribe: una observacion 100% vacia taparia datos completos anteriores
  en el colapso a-la-mas-reciente (mismo hallazgo alto que 1.3 cerro para
  metricas) y contaria como skip "fila sin ninguna metrica". Metrica
  NEGATIVA en cualquier fila cruda aborta la corrida fail-closed (hallazgos
  codex+grok): la fusion podria compensar el negativo con un positivo
  hermano y colarlo bajo el CHECK st_metric_no_negativos -- ese nivel de
  corrupcion se quiere VER, no tragar. Cada fila absorbida cuenta como
  rows_skipped "fila agregada por clave duplicada en el reporte": la
  contabilidad rows_written + rows_skipped == filas sigue cuadrando y la
  fusion queda visible en skip_reason de la run.
- Dedupe por source_report_id (indice parcial st_metric_dedupe_reporte),
  igual que metricas: la defensa para la RE-INGESTA del mismo reporte (la
  fusion del planificador ya hace imposible dos inserts de la misma clave
  dentro de un reporte).

Productos anunciados (ORBIT 19 B.1):

- 5to reporte spAdvertisedProduct (verificado en vivo 2026-09-06, evidencia
  docs/evidencia/orbit-19/0.3) a tabla PROPIA ads_product_metric_observation
  (migracion 0020), grano (platform, advertisedAsin, advertisedSku,
  metric_date): las filas del mismo asin/sku en varias campanas se SUMAN;
  JAMAS se reparte un agregado de campana entre productos (politica 0.4 §2).
  NO entra en REPORTES_CFG: va en el grupo productos de la corrida diaria
  (ingest diario), misma ventana D-31..D-1 para re-pedir las 30d.
  campaignId/adGroupId/adId viajan como JSONB de trazabilidad. "salesSameSku30d"
  NO se pide (400 vivo); attributedSalesSameSku30d/purchasesSameSku30d si.
  Moneda: la del perfil (mx->MXN, us->USD), sellada por el trigger compartido.

Este modulo no tiene `__main__`: el orquestador vivo es `python -m app.cli
ingest diario` (app/ads/diario.py), que arma los grupos de metricas,
productos y placements sobre descarga + escribir_grupo, carga las
credenciales y la DSN de ingesta y resume por stdout SIN secretos. La
retencion de 95 dias NO se gatea ahi: pedir mas viejo falla en la API y eso
ya es fail-closed.
"""

from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import logging
import re
import zlib
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import psycopg
from psycopg.types.json import Jsonb

from app.ads.client import AdsClient
from app.ads.salud import SOURCE  # noqa: F401 - reexport que usa app.ads.diario
from app.ads.structure import (
    PerfilAds,
    _formato_skip_reason,
    _sellar_run,
)
from app.dia_cerrado import ultimo_dia_cerrado
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE_PRODUCTOS = "amazon_ads_products_v3"

_SQL_RESULTADO_REPORTE = """
    INSERT INTO ads_report_result
        (ingest_run_id, profile_id, platform, report_name, source_report_id, status, reason,
         fecha_desde, fecha_hasta, cobertura_integra)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _registrar_resultado(
    conn: psycopg.Connection,
    run_id: int,
    perfil: PerfilAds | None,
    cfg: dict | None,
    report_id: str | None,
    status: str,
    reason: str | None = None,
    *,
    rango: tuple[dt.date, dt.date] | None = None,
    cobertura_integra: bool | None = None,
) -> None:
    """`rango` es el que se pidio a Amazon (AUTO-03 D.7): sin el, la fila no
    acredita ninguna fecha. `cobertura_integra` solo la sella el written del
    reporte de productos (ResultadoIngesta.cobertura_integra)."""
    profile_id = perfil.profile_id if perfil else None
    # Un perfil rechazado conserva el payload crudo; puede traer str, bool,
    # lista o entero fuera de BIGINT. NULL expresa ID desconocido sin
    # bloquear los reportes de otros perfiles validos.
    if (
        not isinstance(profile_id, int)
        or isinstance(profile_id, bool)
        or not -(2**63) <= profile_id < 2**63
    ):
        profile_id = None
    conn.execute(
        _SQL_RESULTADO_REPORTE,
        (
            run_id,
            profile_id,
            perfil.platform if perfil else None,
            cfg["nombre"] if cfg else None,
            report_id,
            status,
            scrub(reason) if reason else None,
            rango[0] if rango else None,
            rango[1] if rango else None,
            cobertura_integra,
        ),
    )


# Tope de rango de la API (verificado: spCampaigns max 31 dias).
MAX_RANGO_DIAS = 31

# Tope fail-closed de descarga (se valida en fileSize del poll y tras bajar):
# un backfill de 31 dias pesa pocos MB; 512 MB indica payload anormal.
MAX_BYTES_REPORTE = 512 * 1024 * 1024

# Metricas comunes a los tres reportes de metricas (verificado en vivo): el
# mapeo a columnas de ads_metric_observation es:
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
# 4to reporte (task 1.4). PROVENANCE DISTINTA: esta forma NO la verifico
# nuestro sondeo, la verifico EN VIVO el sistema viejo goncloud-MCP-2
# (_request_search_term_report: "spSearchTerm" SINGULAR, groupBy
# ["searchTerm"], purchases7d/sales7d); re-sellado contra la API en la
# corrida real de 1.5. "tabla" es la clave que usa escribir_grupo para
# despachar esta cfg a ingest_search_terms.
SEARCH_TERMS_CFG = {
    "nombre": "search_terms",
    "reportTypeId": "spSearchTerm",
    "groupBy": ["searchTerm"],
    "columns": ["date", "adGroupId", "searchTerm", "clicks", "cost", "purchases7d", "sales7d"],
    "kind": "ad_group",
    "id_field": "adGroupId",
    "tabla": "search_term_observation",
}
REPORTES_CFG = (CAMPANAS_CFG, KEYWORDS_CFG, TARGETS_CFG, SEARCH_TERMS_CFG)
# 5to reporte (ORBIT 19 B.1): metricas por PRODUCTO ANUNCIADO a grano
# (platform, advertisedAsin, advertisedSku) -> tabla PROPIA
# ads_product_metric_observation (migracion 0020). NO entra en REPORTES_CFG:
# su ingesta no resuelve ad_entity (el producto anunciado no es entidad) y va
# en el grupo productos de la corrida diaria (ingest diario), mismo
# patron de fase API/DB y misma ventana del cron (D-31..D-1). Forma
# verificada en vivo 2026-09-06 (docs/evidencia/orbit-19/0.3/reporte.md):
# reportTypeId spAdvertisedProduct, groupBy ["advertiser"], 767 filas MX,
# cost/clicks conciliados. PROHIBIDO pedir "salesSameSku30d" (400 vivo);
# attributedSalesSameSku30d y purchasesSameSku30d SI estan en el allowlist.
# campaignId/adGroupId/adId se guardan SOLO como trazabilidad JSONB, jamas
# para repartir agregados de campana (politica 0.4 §2).
PRODUCTOS_CFG = {
    "nombre": "productos",
    "reportTypeId": "spAdvertisedProduct",
    "groupBy": ["advertiser"],
    "columns": [
        "date",
        "advertisedAsin",
        "advertisedSku",
        "campaignId",
        "adGroupId",
        "adId",
        "impressions",
        "clicks",
        "cost",
        "purchases30d",
        "sales30d",
        "purchasesSameSku30d",
        "attributedSalesSameSku30d",
    ],
    "tabla": "ads_product_metric_observation",
}

# Resolucion de entidades en un solo SELECT por reporte (las filas de un
# reporte comparten platform/kind; los external_id van de a miles).
_SQL_ENTIDADES = """
    SELECT external_id, id FROM ad_entity
    WHERE platform = %s AND kind = %s AND external_id = ANY(%s)
"""

# Mismo reloj transaccional que observed_at (now() de la DB, timestamptz
# independiente de la TimeZone de sesion) para calcular el ultimo dia cerrado
# en la zona de cada plataforma.
_SQL_FECHA_HOY = "SELECT now()"

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

# Espejo append-only para search_term_observation: el ON CONFLICT apunta al
# indice parcial st_metric_dedupe_reporte (misma disciplina que metricas);
# sin RETURNING row la fila fue absorbida por el dedupe y cuenta como
# rows_skipped con motivo. platform va DESNORMALIZADA en la fila (la PK la
# incluye) y el trigger st_metric_moneda_sellada sella la moneda Y cruza
# plataforma<->entidad. RETURNING search_term solo distingue insert de
# absorbida.
_SQL_INSERT_TERMINO = """
    INSERT INTO search_term_observation
        (platform, ad_entity_id, search_term, metric_date, observed_at, metric_currency,
         cost, clicks, orders, ad_revenue, is_asin_like, source_report_id, ingest_run_id)
    VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (platform, ad_entity_id, search_term, metric_date, source_report_id)
        WHERE source_report_id IS NOT NULL
    DO NOTHING
    RETURNING search_term
"""

# Espejo append-only para ads_product_metric_observation (ORBIT 19 B.1): mismo
# patron de dedupe por source_report_id. platform va en la fila (la PK la
# incluye); la moneda es la del perfil y la sella el trigger compartido
# metric_moneda_de_plataforma (migracion 0020). campaign_ids/ad_ids viajan
# como JSONB de trazabilidad. RETURNING advertised_asin solo distingue insert
# de absorbida.
_SQL_INSERT_PRODUCTO = """
    INSERT INTO ads_product_metric_observation
        (platform, advertised_asin, advertised_sku, metric_date, observed_at,
         metric_currency, impressions, clicks, cost, purchases30d, sales30d,
         purchases_same_sku30d, attributed_sales_same_sku_30d,
         campaign_ids, ad_ids, source_report_id, ingest_run_id)
    VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (platform, advertised_asin, advertised_sku, metric_date, source_report_id)
        WHERE source_report_id IS NOT NULL
    DO NOTHING
    RETURNING advertised_asin
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
class _FilaTermino:
    """Una fila de search term planificada: lo que se inserta si ningun gate la salta.

    `search_term` ya viene STRIPPEADO (normalizacion de identidad para la PK)
    y `is_asin_like` calculado en la planificacion: la columna es NOT NULL
    SIN DEFAULT, asi que el clasificador corre ANTES de tocar la base.
    """

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
    """Una fila de producto anunciado planificada (ORBIT 19 B.1).

    Identidad del grano sellado (asin, sku, fecha); `campaign_ids`/`ad_ids`
    son trazabilidad de que filas de la API aportaron a la clave (strings,
    sin repeticion) -- JAMAS insumo para repartir agregados de campana.
    """

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


@dataclass
class ResultadoIngesta:
    """Outcome contable de UN reporte ingerido.

    `skips` (motivo -> contador) se expone para que escribir_grupo acumule
    sin re-parsear el texto de skip_reason (una sola fuente del formato:
    _formato_skip_reason de structure).
    """

    report_id: str
    filas: int
    rows_written: int
    rows_skipped: int
    skip_reason: str | None
    skips: Counter[str]
    # AUTO-03 D.7: solo productos la calcula (None en los demas reportes).
    cobertura_integra: bool | None = None


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
    *,
    sufijo: str = "",
) -> str:
    """POST /reporting/reports -> report_id (forma del body verificada en vivo).

    `groupBy` ARRAY y `columns` STRINGS (con objetos la API da 400); los
    filters solo existen para keywords/targets (spTargeting). `sufijo` (`-r1`,
    `-r2`) distingue los repetidos que pide app.ads.descarga (ingesta-01).
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
            f"-{fecha_ini.isoformat()}_{fecha_fin.isoformat()}{sufijo}"
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
            with gzip.GzipFile(fileobj=io.BytesIO(contenido)) as gz:
                descomprimido = gz.read(MAX_BYTES_REPORTE + 1)
            if len(descomprimido) > MAX_BYTES_REPORTE:
                raise AdsReportsError(
                    f"reporte descomprimido excede el tope de {MAX_BYTES_REPORTE} bytes: "
                    "la corrida aborta fail-closed"
                )
            texto = descomprimido.decode("utf-8")
        except AdsReportsError:
            raise
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
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> tuple[list[_FilaPlano], Counter[str]]:
    """Valida cada fila ANTES de tocar la base.

    Gates (todos dejan la fila fuera con motivo, ninguno aborta la corrida):
    id ausente, date invalida, dia que aun no cierra (metric_date posterior a
    `ultimo_cerrado`, el ultimo dia que ya termino en la zona del marketplace,
    ARREGLOS 03 D-E; tambien cubre la fecha futura: observed_at lo fija la
    ingesta en now(), el invariante observado >= hecho es responsabilidad
    declarada de este modulo), metric_date fuera
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
        if metric_date > ultimo_cerrado:
            skips["fila de un día que aún no cierra"] += 1
            logger.debug(
                "dia que aun no cierra en fila de %s: %s > %s",
                etiqueta,
                metric_date,
                ultimo_cerrado,
            )
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
            # Clave de cardinalidad acotada (el detalle por fila va al log)
            # para evitar miles de claves en skip_reason durante un backfill.
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
    """Clasifica un search term del reporte como ASIN-like o no.

    Un search term ASIN-like es una UBICACION de pagina de producto, no una
    busqueda tipeada, y los ASIN-like SIEMPRE se saltan en negativos (regla
    sealed own-ASIN: negativizar un ASIN propio es apagarte a ti mismo; ver
    el COMMENT de search_term_observation.is_asin_like en la migracion).

    Criterio: EXACTAMENTE 10 caracteres alfanumericos ASCII que EMPIEZAN con
    "B0" (patron de ASIN de Amazon), case-insensitive, full-match sobre el
    termino sin espacios extremos. Un "b0" embebido en un termino mas largo
    NO es ASIN. Esta funcion es el UNICO clasificador de la columna NOT NULL
    SIN DEFAULT: corre en la planificacion de cada fila, asi ninguna fila
    escrita llega sin clasificar (un default false convertiria "no lo
    revise" en "no es ASIN", el dato faltante disfrazado que prohibe la
    regla 3).
    """
    return bool(_RE_ASIN.match(termino.strip()))


def _sumar_decimal(actual: Decimal | None, aporte: Decimal | None) -> Decimal | None:
    """Fusion de dinero por clave duplicada, con ENVENENAMIENTO de None.

    DECISION SELLADA (review PR #11, major; SUPERSEDE el mensaje del commit
    e1492bd, que decia "None aporta nada; todo-None queda None"): si ALGUN
    aporte de la clave trajo la metrica AUSENTE, la fusionada queda None. Un
    campo ausente en el reporte es DESCONOCIDO, no cero: la suma con un
    sumando desconocido es desconocida, y guardar 0+None como 0 trataria el
    ausente como aporte nulo inventado (regla 3) -- en orders, un 0+None=0
    dispararia NEGATIVE_EXACT contra un termino convertidor, exactamente el
    bug que la fusion vino a matar. Solo se suma cuando TODOS los aportes
    reportaron la metrica; None + None = None."""
    if actual is None or aporte is None:
        return None
    return actual + aporte


def _sumar_entero(actual: int | None, aporte: int | None) -> int | None:
    """Espejo entero de _sumar_decimal (mismo envenenamiento de None: decision
    sellada de la review del PR #11, supersede el mensaje del commit e1492bd)."""
    if actual is None or aporte is None:
        return None
    return actual + aporte


def _planea_filas_terminos(
    reporte_cfg: dict,
    filas: list[dict],
    *,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> tuple[list[_FilaTermino], Counter[str]]:
    """Valida, CLASIFICA y FUSIONA las filas de search terms ANTES de la base.

    Espejo de _planea_filas con el vocabulario CERRADO de skips (mismas
    claves de fecha/metrica; solo cambian las de identidad de la fila):
    adGroupId ausente, searchTerm ausente/vacio (tras strip), date invalida,
    dia que aun no cierra, fuera del rango solicitado, metricas no
    numericas/fraccionarias y fila sin NINGUNA metrica. NO hay pre-check
    same_sku: la tabla no tiene esa columna. Toda fila planificada llega con
    is_asin_like calculado y search_term strippeado.

    FUSION por clave duplicada (grano multiple del reporte, verificado EN
    VIVO en el sondeo 1.5 del 2026-08-23): el mismo (adGroupId, searchTerm,
    date) llega en varias filas -- el termino convierte via keywords
    distintas del mismo ad group, DISYUNCION confirmada con la columna
    keyword (0 copias identicas; cross-review 1.5) -- y las filas VALIDAS de
    la misma clave se fusionan SUMANDO metricas (cost/ad_revenue como
    Decimal y clicks/orders como int via _sumar_decimal/_sumar_entero, con
    ENVENENAMIENTO: si algun aporte trajo la metrica ausente, la fusionada
    queda None -- un agregado parcial jamas se guarda como completo, regla
    3). Metrica NEGATIVA en cualquier fila cruda -> AdsReportsError (la
    corrida aborta fail-closed: la fusion podria compensarla bajo el CHECK
    de la base; ese nivel de corrupcion se quiere ver). Sumar no inventa
    dato hacia la identidad (ad_group, termino, fecha) del esquema sellado,
    la que el motor consume -- un orders subestimado por first-wins puede
    generar un NEGATIVE_EXACT erroneo en 2.3. is_asin_like no se toca: es
    funcion del termino, igual en toda la clave. Cada fila absorbida cuenta
    como skip "fila agregada por clave duplicada en el reporte" (clave de
    vocabulario CERRADO: la contabilidad rows_written + rows_skipped ==
    filas sigue cuadrando y la fusion queda visible en skip_reason de la
    run; el detalle de la clave va al log). El acumulador por dict preserva
    el orden de aparicion: el plan sale en el orden de PRIMERA aparicion de
    cada clave (determinista).

    Constituyente invalido -> clave ENVENENADA para todo el reporte
    (ARREGLOS 03 D-G, TN05; misma regla que productos via _envenenar_clave):
    una metrica no numerica/fraccionaria, una fila sin NINGUNA metrica, una
    fila con clics o gasto y pedidos vacios (motivo "fila con pedidos
    vacíos") o una fusion que queda 100% vacia retiran el subtotal ya
    acumulado de la clave y descartan sus filas posteriores ("fila de clave
    envenenada (subtotal parcial)"): un termino que vendio jamas se publica
    con un subtotal de 0 pedidos (candidato a NEGATIVE por error), en
    cualquier orden de filas. Los negativos siguen abortando.
    """
    plan: list[_FilaTermino] = []
    skips: Counter[str] = Counter()
    etiqueta = reporte_cfg["nombre"]
    campo_id = reporte_cfg["id_field"]
    por_clave: dict[tuple[str, str, dt.date], _FilaTermino] = {}
    # Veneno ADHESIVO (ARREGLOS 03 D-G, TN05; la misma regla que productos):
    # una clave invalidada lo queda para todo el reporte, sin importar el
    # orden de las filas en el gzip.
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
        raw_date = fila.get("date")
        try:
            if not isinstance(raw_date, str):
                raise ValueError
            metric_date = dt.date.fromisoformat(raw_date)
        except ValueError:
            skips["fila con date invalida"] += 1
            logger.debug("date invalida en fila de %s: %r", etiqueta, raw_date)
            continue
        if metric_date > ultimo_cerrado:
            skips["fila de un día que aún no cierra"] += 1
            logger.debug(
                "dia que aun no cierra en fila de %s: %s > %s",
                etiqueta,
                metric_date,
                ultimo_cerrado,
            )
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
        # La clave se resuelve ANTES de validar metricas: una fila invalida
        # de la clave la ENVENENA para todo el reporte (ARREGLOS 03 D-G,
        # TN05). Cada causa conserva su motivo de skip.
        clave = (external_id, search_term, metric_date)
        try:
            cost = _decimal_reporte(fila.get("cost"), "cost")
            ad_revenue = _decimal_reporte(fila.get("sales7d"), "sales7d")
            clicks = _entero_reporte(fila.get("clicks"), "clicks")
            orders = _entero_reporte(fila.get("purchases7d"), "purchases7d")
        except ValueError:
            skips[f"fila de {etiqueta} con metrica no numerica o fraccionaria"] += 1
            _envenenar_clave(
                clave, por_clave=por_clave, plan=plan, envenenadas=envenenadas, skips=skips
            )
            continue
        if cost is None and ad_revenue is None and clicks is None and orders is None:
            skips["fila sin ninguna metrica"] += 1
            logger.debug("fila sin ninguna metrica: %s %s %s", etiqueta, external_id, metric_date)
            _envenenar_clave(
                clave, por_clave=por_clave, plan=plan, envenenadas=envenenadas, skips=skips
            )
            continue
        # Validar negativos antes de fusionar: un positivo hermano podria
        # compensarlos y ocultarlos al CHECK st_metric_no_negativos.
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
        # Clics o gasto sin el dato de pedidos (purchases7d ausente o nulo):
        # guardarla dejaria orders vacio y la senal de JEV lo suma como 0
        # pedidos con todos sus clics -- candidato a NEGATIVE por error. No se
        # convierte el faltante en cero: la clave no se guarda ese dia. Va
        # DESPUES del chequeo de negativos para que un negativo siga abortando.
        if orders is None and (clicks is not None or cost is not None):
            skips["fila con pedidos vacíos"] += 1
            logger.debug("fila con pedidos vacios: %s %s %s", etiqueta, external_id, metric_date)
            _envenenar_clave(
                clave, por_clave=por_clave, plan=plan, envenenadas=envenenadas, skips=skips
            )
            continue
        if clave in envenenadas:
            skips["fila de clave envenenada (subtotal parcial)"] += 1
            logger.debug("fila descartada por clave envenenada: %s %s", etiqueta, clave)
            continue
        previa = por_clave.get(clave)
        if previa is not None:
            # Grano multiple del reporte: los aportes por keyword al mismo
            # hecho (ad_group, termino, fecha) son disjuntos -> se SUMAN; el
            # skip mantiene visible la fila absorbida en la contabilidad.
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
            # Si la fusion dejo todas las metricas en None, se retira y
            # envenena la clave para no escribir una fila vacia que tape
            # observaciones previas ni publicar luego un subtotal parcial.
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
                _envenenar_clave(
                    clave, por_clave=por_clave, plan=plan, envenenadas=envenenadas, skips=skips
                )
            continue
        fila = _FilaTermino(
            external_id=external_id,
            search_term=search_term,
            is_asin_like=es_asin_like(search_term),
            metric_date=metric_date,
            cost=cost,
            ad_revenue=ad_revenue,
            clicks=clicks,
            orders=orders,
        )
        por_clave[clave] = fila
        plan.append(fila)

    return plan, skips


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
    plan: list[_FilaProducto],
    envenenadas: set[tuple[str, str, dt.date]],
    skips: Counter[str],
) -> None:
    """Absorbe una fila duplicada (mismo asin/sku/fecha, otra campana) hacia
    el grano sellado: los aportes son disjuntos -> se SUMAN con
    envenenamiento de None. Un par complementario que deja la clave SIN
    ninguna metrica la descarta y la marca envenenada: una fila posterior no
    puede recrearla con solo sus valores (hallazgo cross-review codex)."""
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
    # Gate POST-fusion (mismo sellado que terminos): una observacion 100%
    # vacia no se escribe (taparia datos completos anteriores en el colapso
    # a-la-mas-reciente).
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
        plan.remove(previa)
        envenenadas.add((asin, sku, metric_date))
        skips["fila sin ninguna metrica"] += 1
        logger.debug(
            "fusion dejo la clave sin ninguna metrica (par envenenado), no se escribe: %s/%s %s",
            asin,
            sku,
            metric_date,
        )


def _envenenar_clave(
    clave: tuple,
    *,
    por_clave: dict,
    plan: list,
    envenenadas: set,
    skips: Counter[str],
) -> None:
    """Un constituyente INVALIDO envenena su clave para TODO el reporte: si
    otra fila de la misma clave ya trajo datos validos, ese subtotal es
    PARCIAL y no se publica como completo; las filas posteriores de la clave
    tampoco la recrean. La usan productos (asin, sku, fecha) y terminos
    (ad group, termino, fecha) (hallazgo cross-review codex 2026-09-07;
    ARREGLOS 03 D-G, TN05). Quien llama cuenta el motivo de su fila; aqui
    solo se cuenta la fila planificada que se retira."""
    previa = por_clave.pop(clave, None)
    if previa is not None:
        plan.remove(previa)
        # Los aportes fusionados ya se contaron; falta solo la fila retirada.
        skips["fila de clave envenenada (subtotal parcial)"] += 1
    envenenadas.add(clave)
    logger.debug("fila invalida envenena la clave (subtotal parcial): %s", clave)


def _planea_filas_productos(
    filas: list[dict],
    *,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> tuple[list[_FilaProducto], Counter[str]]:
    """Valida y AGREGA las filas de spAdvertisedProduct por SUMA al grano
    (asin, sku, fecha) ANTES de la base (ORBIT 19 B.1, politica 0.4 §2).

    El reporte trae una fila por (date, asin, sku, campaignId, adGroupId,
    adId); la tabla sella el grano (platform, asin, sku, fecha), asi que las
    filas del MISMO asin/sku en varias campanas (o varios ads) se FUSIONAN
    SUMANDO metricas (via _sumar_decimal/_sumar_entero con ENVENENAMIENTO de
    None: si algun aporte trajo la metrica ausente, la fusionada queda None --
    regla 3). NUNCA se reparte un agregado de campana entre productos: este
    planificador no ve agregados de campana, solo filas por producto.

    Espejo de _planea_filas_terminos (vocabulario CERRADO de skips): asin/sku
    ausentes, date invalida, dia que aun no cierra, fuera del rango solicitado,
    metricas no numericas/fraccionarias, fila sin NINGUNA metrica y la fila
    absorbida por la fusion. Las filas con metrica no numerica o sin NINGUNA
    metrica ENVENENAN la clave (asin, sku, fecha): el subtotal parcial de otra
    campana no se publica como completo (hallazgo cross-review codex
    2026-09-07). Metrica NEGATIVA en fila cruda aborta fail-closed
    (la fusion podria compensarla bajo el CHECK apm_no_negativos). Pre-check
    same_sku <= total por fila cruda (el CHECK apm_same_sku_cabe abortaria el
    lote entero si llegara al INSERT).
    """
    plan: list[_FilaProducto] = []
    skips: Counter[str] = Counter()
    por_clave: dict[tuple[str, str, dt.date], _FilaProducto] = {}
    # Claves invalidadas para todo el reporte: impide que una fila posterior
    # recree la clave y publique un subtotal parcial segun el orden del gzip.
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
        raw_date = fila.get("date")
        try:
            if not isinstance(raw_date, str):
                raise ValueError
            metric_date = dt.date.fromisoformat(raw_date)
        except ValueError:
            skips["fila con date invalida"] += 1
            logger.debug("date invalida en fila de productos: %r", raw_date)
            continue
        if metric_date > ultimo_cerrado:
            skips["fila de un día que aún no cierra"] += 1
            logger.debug("dia que aun no cierra en productos: %s > %s", metric_date, ultimo_cerrado)
            continue
        if metric_date < fecha_ini or metric_date > fecha_fin:
            skips["fila con metric_date fuera del rango solicitado"] += 1
            logger.debug(
                "metric_date %s fuera del rango %s..%s en producto %s/%s",
                metric_date,
                fecha_ini,
                fecha_fin,
                asin,
                sku,
            )
            continue
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
            _envenenar_clave(
                (asin, sku, metric_date),
                por_clave=por_clave,
                plan=plan,
                envenenadas=envenenadas,
                skips=skips,
            )
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
            _envenenar_clave(
                (asin, sku, metric_date),
                por_clave=por_clave,
                plan=plan,
                envenenadas=envenenadas,
                skips=skips,
            )
            continue
        # Negativos = dato corrupto y la corrida ABORTA (fail-closed): debe
        # correr ANTES de same_sku > total para que un total negativo (ej.
        # same_sku=0, sales=-5) no se descarte silenciosamente como skip.
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
            # Pre-check para evitar que apm_same_sku_cabe aborte el lote entero;
            # envenena la clave para no publicar un subtotal parcial de otra campana.
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
            _envenenar_clave(
                (asin, sku, metric_date),
                por_clave=por_clave,
                plan=plan,
                envenenadas=envenenadas,
                skips=skips,
            )
            continue
        # Trazabilidad: ids de la API como strings, sin repeticion (regla 1:
        # no se usan para repartir, solo para auditar de donde salio la suma).
        campaign_id = str(fila["campaignId"]) if fila.get("campaignId") is not None else None
        ad_id = str(fila["adId"]) if fila.get("adId") is not None else None

        previa = por_clave.get((asin, sku, metric_date))
        if (asin, sku, metric_date) in envenenadas:
            skips["fila de clave envenenada (subtotal parcial)"] += 1
            logger.debug("fila descartada por clave envenenada: %s/%s %s", asin, sku, metric_date)
            continue
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
                plan=plan,
                envenenadas=envenenadas,
                skips=skips,
            )
            continue
        nueva = _FilaProducto(
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
        por_clave[(asin, sku, metric_date)] = nueva
        plan.append(nueva)

    return plan, skips


def ingest_metrics(
    conn: psycopg.Connection,
    perfil: PerfilAds,
    reporte_cfg: dict,
    report_id: str,
    filas: list[dict],
    *,
    run_id: int,
    ultimo_cerrado: dt.date,
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

    `run_id` es la ingest_run que abre la corrida de descarga (UNA por grupo,
    no una por reporte). `ultimo_cerrado` es ultimo_dia_cerrado(plataforma del
    perfil, now() de la DB) calculado en la misma transaccion que abre la run:
    el guard de dia cerrado usa el MISMO reloj que escribe observed_at con
    now() (hallazgo grok) y descarta la fila de un dia que aun no termina en
    la zona del marketplace (ARREGLOS 03 D-E).
    `fecha_ini`/`fecha_fin` delimitan el rango solicitado: una fila con
    metric_date fuera del rango (descarga equivocada) se salta con motivo.
    """
    if not perfil.aceptado or perfil.platform is None or perfil.moneda is None:
        raise AdsReportsError(
            f"ingest_metrics exige un perfil aceptado (profile_id={perfil.profile_id!r})"
        )
    plan, skips = _planea_filas(
        reporte_cfg, filas, ultimo_cerrado=ultimo_cerrado, fecha_ini=fecha_ini, fecha_fin=fecha_fin
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
            # Clave por kind (cardinalidad acotada); el id exacto va al log.
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


def ingest_search_terms(
    conn: psycopg.Connection,
    perfil: PerfilAds,
    reporte_cfg: dict,
    report_id: str,
    filas: list[dict],
    *,
    run_id: int,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> ResultadoIngesta:
    """Inserta las filas de UN reporte en search_term_observation (append-only).

    Espejo de ingest_metrics con la unidad contable (fila del reporte) y los
    mismos outcomes: insertada -> rows_written; absorbida por el dedupe del
    indice parcial st_metric_dedupe_reporte -> rows_skipped "fila duplicada
    del reporte <id>"; entidad (ad_group) ausente en ad_entity -> rows_skipped
    "entidad ausente en ad_entity (ad_group)". Toda fila escrita llega con
    is_asin_like clasificado por es_asin_like (la columna es NOT NULL SIN
    DEFAULT). La platform va en la fila (la PK la incluye) y la moneda es la
    del perfil: el trigger st_metric_moneda_sellada sella la moneda Y cruza
    plataforma<->entidad. Si un MISMO reporte trae dos filas del mismo
    (ad_group, termino, fecha) -- p.ej. via keywords distintas, grano
    multiple verificado en vivo -- el PLANIFICADOR las FUSIONA sumando
    metricas y la fila absorbida cuenta como rows_skipped "fila agregada
    por clave duplicada en el reporte" (ver _planea_filas_terminos): sin
    perdida silenciosa. El dedupe de source_report_id queda como defensa
    para la re-ingesta del mismo reporte.

    `run_id`/`ultimo_cerrado`/`fecha_ini`/`fecha_fin`: misma semantica que
    ingest_metrics (una ingest_run por corrida; el guard de dia cerrado usa
    el MISMO reloj que escribe observed_at).
    """
    if not perfil.aceptado or perfil.platform is None or perfil.moneda is None:
        raise AdsReportsError(
            f"ingest_search_terms exige un perfil aceptado (profile_id={perfil.profile_id!r})"
        )
    plan, skips = _planea_filas_terminos(
        reporte_cfg, filas, ultimo_cerrado=ultimo_cerrado, fecha_ini=fecha_ini, fecha_fin=fecha_fin
    )

    # Resolucion de entidades en un solo SELECT por reporte (kind ad_group):
    # los adGroupId que falten son skip, no error.
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
            skips[f"entidad ausente en ad_entity ({kind})"] += 1
            logger.debug(
                "entidad ausente en ad_entity: %s %s (correr sync de estructura)",
                kind,
                fila.external_id,
            )
            continue
        insertado = conn.execute(
            _SQL_INSERT_TERMINO,
            (
                perfil.platform,
                entidad_id,
                fila.search_term,
                fila.metric_date,
                perfil.moneda,
                fila.cost,
                fila.clicks,
                fila.orders,
                fila.ad_revenue,
                fila.is_asin_like,
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


# AUTO-03 D.7 (S6): descartes que NO pueden esconder actividad de una fecha
# del rango pedido. La fusion suma la fila en su clave; fuera de rango o
# de un dia que aun no cierra (ARREGLOS 03 D-E) no es una fecha que se
# pueda guardar; el duplicado ya esta escrito por otra
# corrida. Cualquier otro descarte (vocabulario cerrado de
# _planea_filas_productos) deja la cobertura en falso: fail-closed ante un
# motivo nuevo.
_SKIPS_SIN_ACTIVIDAD_DEL_RANGO = frozenset(
    {
        "fila agregada por clave duplicada en el reporte",
        "fila con metric_date fuera del rango solicitado",
        "fila de un día que aún no cierra",
    }
)


def _cobertura_integra(skips: Counter[str], report_id: str) -> bool:
    duplicado = f"fila duplicada del reporte {report_id}"
    return all(
        motivo in _SKIPS_SIN_ACTIVIDAD_DEL_RANGO or motivo == duplicado
        for motivo, n in skips.items()
        if n
    )


def ingest_productos(
    conn: psycopg.Connection,
    perfil: PerfilAds,
    reporte_cfg: dict,
    report_id: str,
    filas: list[dict],
    *,
    run_id: int,
    ultimo_cerrado: dt.date,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
) -> ResultadoIngesta:
    """Inserta las filas de UN reporte spAdvertisedProduct en
    ads_product_metric_observation (append-only, ORBIT 19 B.1).

    Diferencias selladas con ingest_metrics: NO resuelve ad_entity (el grano
    es platform/asin/sku, el producto anunciado no es entidad) y el
    PLANIFICADOR ya agrego por SUMA las filas del mismo (asin, sku, fecha) --
    las de varias campanas suman, los agregados de campana jamas se reparten.
    campaign_ids/ad_ids viajan como JSONB de trazabilidad. La moneda es la
    del perfil y la sella el trigger compartido metric_moneda_de_plataforma.

    `run_id`/`ultimo_cerrado`/`fecha_ini`/`fecha_fin`: misma semantica que
    ingest_metrics (una ingest_run por corrida; el guard de dia cerrado usa
    el MISMO reloj que escribe observed_at).
    """
    if not perfil.aceptado or perfil.platform is None or perfil.moneda is None:
        raise AdsReportsError(
            f"ingest_productos exige un perfil aceptado (profile_id={perfil.profile_id!r})"
        )
    plan, skips = _planea_filas_productos(
        filas, ultimo_cerrado=ultimo_cerrado, fecha_ini=fecha_ini, fecha_fin=fecha_fin
    )

    written = 0
    for fila in plan:
        insertado = conn.execute(
            _SQL_INSERT_PRODUCTO,
            (
                perfil.platform,
                fila.asin,
                fila.sku,
                fila.metric_date,
                perfil.moneda,
                fila.impressions,
                fila.clicks,
                fila.cost,
                fila.purchases30d,
                fila.sales30d,
                fila.purchases_same_sku30d,
                fila.attributed_sales_same_sku_30d,
                Jsonb(fila.campaign_ids),
                Jsonb(fila.ad_ids),
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
        cobertura_integra=_cobertura_integra(skips, report_id),
    )


def _validar_rango(fecha_ini: dt.date, fecha_fin: dt.date) -> None:
    """El rango de los reportes: [fecha_ini, fecha_fin], max MAX_RANGO_DIAS."""
    dias = (fecha_fin - fecha_ini).days + 1
    if dias < 1:
        raise ValueError(f"rango invertido: {fecha_ini} > {fecha_fin}")
    if dias > MAX_RANGO_DIAS:
        raise ValueError(f"rango de {dias} dias excede el maximo de {MAX_RANGO_DIAS} de la API")


def escribir_grupo(
    conn: psycopg.Connection,
    run_id: int,
    grupo,
    items: list,
    *,
    fecha_ini: dt.date,
    fecha_fin: dt.date,
):
    """Fase DB de UN grupo de metricas/productos: la escribe `al_completar` de
    app.ads.descarga en el momento en que el grupo cierra su fase de API.

    Una sola transaccion: el instante del guard de dia cerrado sale del MISMO
    reloj y transaccion que observed_at (now() de la DB); cada reporte saca
    de el su ultimo_dia_cerrado en la zona de su plataforma (ARREGLOS 03
    D-E). El despacho por cfg["tabla"]
    va a ingest_metrics / ingest_search_terms / ingest_productos, cada reporte
    queda `written` y el sello ok=true cierra la misma transaccion. En fallo:
    rollback atomico del trabajo + sello best-effort ok=false (registrando la
    unidad que fallo, o global si aun no habia empezado) y la excepcion
    original sube: descarga marca el grupo y sigue con los demas.
    `intentar_procesar_run` lo llama descarga despues de CADA sello.
    """
    from app.ads.descarga import ResultadoEscritura

    del grupo  # el grupo entero es su source: la fase DB solo despacha por cfg
    escritos = 0
    skips: Counter[str] = Counter()
    resumenes: list[ResumenReporte] = []
    perfil_actual: PerfilAds | None = None
    cfg_actual: dict | None = None
    report_id_actual: str | None = None
    try:
        with conn.transaction():
            ahora = conn.execute(_SQL_FECHA_HOY).fetchone()[0]
            for item in items:
                perfil, cfg = item.perfil, item.cfg
                report_id = item.report_id
                filas = item.filas
                perfil_actual, cfg_actual, report_id_actual = perfil, cfg, report_id
                ultimo_cerrado = ultimo_dia_cerrado(perfil.platform, ahora)
                # Despacho fail-closed por tabla: un valor desconocido falla en
                # lugar de caer en ingest_metrics.
                tabla = cfg.get("tabla")
                if tabla is None:
                    resultado = ingest_metrics(
                        conn,
                        perfil,
                        cfg,
                        report_id,
                        filas,
                        run_id=run_id,
                        ultimo_cerrado=ultimo_cerrado,
                        fecha_ini=fecha_ini,
                        fecha_fin=fecha_fin,
                    )
                elif tabla == "search_term_observation":
                    resultado = ingest_search_terms(
                        conn,
                        perfil,
                        cfg,
                        report_id,
                        filas,
                        run_id=run_id,
                        ultimo_cerrado=ultimo_cerrado,
                        fecha_ini=fecha_ini,
                        fecha_fin=fecha_fin,
                    )
                elif tabla == "ads_product_metric_observation":
                    resultado = ingest_productos(
                        conn,
                        perfil,
                        cfg,
                        report_id,
                        filas,
                        run_id=run_id,
                        ultimo_cerrado=ultimo_cerrado,
                        fecha_ini=fecha_ini,
                        fecha_fin=fecha_fin,
                    )
                else:
                    raise AdsReportsError(
                        f"cfg de reporte {cfg.get('nombre')!r} con tabla desconocida: {tabla!r}"
                    )
                escritos += resultado.rows_written
                skips.update(resultado.skips)
                resumenes.append(
                    ResumenReporte(
                        profile_id=perfil.profile_id,
                        platform=perfil.platform,
                        nombre=cfg["nombre"],
                        report_id=report_id,
                        status="COMPLETED",
                        filas=resultado.filas,
                        rows_written=resultado.rows_written,
                        rows_skipped=resultado.rows_skipped,
                        skip_reason=resultado.skip_reason,
                    )
                )
                _registrar_resultado(
                    conn,
                    run_id,
                    perfil,
                    cfg,
                    report_id,
                    "written",
                    rango=(fecha_ini, fecha_fin),
                    cobertura_integra=resultado.cobertura_integra,
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
        # (best-effort). BaseException, no solo Exception: un Ctrl-C tambien
        # deja la run sellada, no abierta.
        try:
            with conn.transaction():
                _registrar_resultado(
                    conn,
                    run_id,
                    perfil_actual,
                    cfg_actual,
                    report_id_actual,
                    "failed" if perfil_actual and cfg_actual else "global_failed",
                    str(exc) or type(exc).__name__,
                    rango=(fecha_ini, fecha_fin) if perfil_actual and cfg_actual else None,
                )
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

    return ResultadoEscritura(
        rows_written=escritos,
        rows_skipped=sum(skips.values()),
        skip_reason=_formato_skip_reason(skips),
        reportes=resumenes,
    )
