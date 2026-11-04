"""Guarda de "metricas de ayer listas" (ingesta-01 A.3).

Ningun comando que decide con las metricas del dia anterior lo hace sobre
datos que no llegaron: `cycle`, `impulso-vigia`, `avisos-campana` y
`jev-senales` consultan estas guardas ANTES de abrir envelopes, escrituras o
mensajes; sin la corrida ok de hoy con filas de D-1 imprimen
`sin métricas de ayer: espera al siguiente horario` y salen 0. Los cuatro
aceptan `--forzar` para correr a mano sin guarda.

Un número, una fuente (regla 2): la verdad de "llegaron las métricas" es la
misma consulta para todos los consumidores, viva en este modulo. Los
invariantes de tiempo van con UTC fijado en la expresion, como en el resto
del esquema.
"""

from __future__ import annotations

import datetime as dt

import psycopg

SOURCE_METRICAS = "amazon_ads_reports_v3"
SOURCE_PRODUCTOS = "amazon_ads_products_v3"
SOURCE_PLACEMENTS = "amazon_ads_placements_v3"

TEXTO_ESPERA = "sin métricas de ayer: espera al siguiente horario"

# ads_metric_observation no tiene platform: la cruza con ad_entity (la
# plataforma de la entidad es la unica fuente).
_SQL_METRICAS_LISTAS = """
SELECT 1
  FROM ads_metric_observation o
  JOIN ingest_run r ON r.id = o.ingest_run_id
  JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE r.source = %s
   AND r.ok = true
   AND (r.started_at AT TIME ZONE 'UTC')::date = %s
   AND o.metric_date = %s
   AND e.platform = %s
 LIMIT 1
"""

# ads_product_metric_observation lleva platform desnormalizada y trazabilidad
# de la run (ingesta 0020).
_SQL_PRODUCTOS_LISTOS = """
SELECT 1
  FROM ads_product_metric_observation o
  JOIN ingest_run r ON r.id = o.ingest_run_id
 WHERE r.source = %s
   AND r.ok = true
   AND (r.started_at AT TIME ZONE 'UTC')::date = %s
   AND o.metric_date = %s
   AND o.platform = %s
 LIMIT 1
"""

# ads_placement_observation (0062) NO trae ingest_run_id: la observacion no
# se liga a la run. La guarda es doble existe: run ok de hoy del source Y
# observaciones de D-1 de la plataforma.
_SQL_PLACEMENTS_LISTOS = """
SELECT 1
  FROM ads_placement_observation o
 WHERE o.metric_date = %s
   AND o.platform = %s
   AND EXISTS (
         SELECT 1
           FROM ingest_run r
          WHERE r.source = %s
            AND r.ok = true
            AND (r.started_at AT TIME ZONE 'UTC')::date = %s
       )
 LIMIT 1
"""


def metricas_de_ayer_listas(
    conn: psycopg.Connection, platform: str, *, hoy: dt.date | None = None
) -> bool:
    """True si una ingest_run `amazon_ads_reports_v3` ok=true iniciada HOY
    escribio filas con metric_date = D-1 de entidades de `platform`."""
    hoy = hoy or dt.datetime.now(dt.UTC).date()
    ayer = hoy - dt.timedelta(days=1)
    fila = conn.execute(_SQL_METRICAS_LISTAS, (SOURCE_METRICAS, hoy, ayer, platform)).fetchone()
    return fila is not None


def productos_de_ayer_listos(
    conn: psycopg.Connection, platform: str, *, hoy: dt.date | None = None
) -> bool:
    """Igual para la corrida de productos (source propio): la tabla lleva
    platform desnormalizada y `ingest_run_id`, asi que la corrida se liga
    fila por fila."""
    hoy = hoy or dt.datetime.now(dt.UTC).date()
    ayer = hoy - dt.timedelta(days=1)
    fila = conn.execute(_SQL_PRODUCTOS_LISTOS, (SOURCE_PRODUCTOS, hoy, ayer, platform)).fetchone()
    return fila is not None


def placements_de_ayer_listos(
    conn: psycopg.Connection, platform: str, *, hoy: dt.date | None = None
) -> bool:
    """Igual para la corrida propia de placements: avisos-campana lee
    ads_placement_observation (via pantalla_dinero.lee_ubicaciones), asi que
    su guarda exige tambien esta corrida ok de hoy. La observacion no liga
    run (0062): doble existe (run ok de hoy + D-1 de la plataforma)."""
    hoy = hoy or dt.datetime.now(dt.UTC).date()
    ayer = hoy - dt.timedelta(days=1)
    fila = conn.execute(_SQL_PLACEMENTS_LISTOS, (ayer, platform, SOURCE_PLACEMENTS, hoy)).fetchone()
    return fila is not None


def guarda(
    conn: psycopg.Connection,
    plataformas,
    *,
    forzar: bool = False,
    exigir_placements: bool = False,
    hoy: dt.date | None = None,
) -> bool:
    """True si el comando puede correr: con --forzar siempre; si no, cada
    plataforma pedida debe tener las metricas de ayer (y, con
    exigir_placements, la corrida de placements de hoy)."""
    if forzar:
        return True
    for plataforma in plataformas:
        if not metricas_de_ayer_listas(conn, plataforma, hoy=hoy):
            return False
        if exigir_placements and not placements_de_ayer_listos(conn, plataforma, hoy=hoy):
            return False
    return True
