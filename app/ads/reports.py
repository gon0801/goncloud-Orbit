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
import zlib
from collections import Counter
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from app.ads.client import AdsClient
from app.ads.reports_plan import (  # noqa: F401 - re-export estable de planificacion pura
    _RE_ASIN,
    AdsReportsError,
    _decimal_reporte,
    _entero_reporte,
    _envenenar_clave,
    _FilaPlano,
    _FilaProducto,
    _FilaTermino,
    _fusionar_fila_producto,
    _planea_filas,
    _planea_filas_productos,
    _planea_filas_terminos,
    _sumar_decimal,
    _sumar_entero,
    es_asin_like,
)
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
