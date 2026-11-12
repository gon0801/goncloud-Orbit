"""Guarda de "metricas de ayer listas" (ingesta-01 A.3).

Ningun comando que decide con las metricas del dia anterior lo hace sobre
datos que no llegaron: `cycle`, `impulso-vigia`, `avisos-campana` y
`jev-senales` consultan estas guardas ANTES de abrir envelopes, escrituras o
mensajes; sin la corrida ok de hoy con filas de D-1 llaman a
`salida_sin_metricas`. Antes de `fin_de_intentos` (la hora desde la que la
guarda da el dia por perdido) imprimen
`sin métricas de ayer: espera al siguiente horario` y salen 75 (espera: aun
queda un horario hoy, y `cron-avisa` no cuenta el 75 como fallo ni como
exito). Desde esa hora avisan por stderr y salen 1 para que el cron
reporte el dia perdido. Los cuatro aceptan `--forzar` para correr a mano sin
guarda.

Un número, una fuente (regla 2): la verdad de "llegaron las métricas" es la
misma consulta para todos los consumidores, viva en este modulo. Los
invariantes de tiempo van con UTC fijado en la expresion, como en el resto
del esquema.
"""

from __future__ import annotations

import datetime as dt
import sys

import psycopg

SOURCE_METRICAS = "amazon_ads_reports_v3"
SOURCE_PRODUCTOS = "amazon_ads_products_v3"
SOURCE_PLACEMENTS = "amazon_ads_placements_v3"

TEXTO_ESPERA = "sin métricas de ayer: espera al siguiente horario"

# Mismo codigo que cron-avisa trata como "saltada" (no cuenta como fallo ni
# como exito y no toca el estado del aviso): el dia aun puede tener metricas.
SALIDA_ESPERA = 75


def fin_de_intentos(hoy: dt.date) -> dt.datetime:
    """Momento UTC desde el que la guarda da el dia por perdido: inicio del
    ultimo HORARIOS_INGESTA mas TOPE_PARED_SEGUNDOS (13:15 + 2 h 45 = 16:00).

    La ingesta no se corta a esa hora. El ultimo intento empieza a las 13:15
    y su sondeo tiene tope duro PLAZO_SONDEO_SEGUNDOS (2 h 30, 15:45, en
    app.ads.descarga); los 15 min restantes son la holgura de escrituras y
    avisos del diseno. Derivado de app.ads.diario y app.ads.descarga, no
    copiado (un numero, una fuente). Import local: diario importa este
    modulo."""
    from app.ads.diario import HORARIOS_INGESTA, TOPE_PARED_SEGUNDOS

    hora, minuto = (int(parte) for parte in HORARIOS_INGESTA[-1].split(":"))
    inicio = dt.datetime.combine(hoy, dt.time(hora, minuto), tzinfo=dt.UTC)
    return inicio + dt.timedelta(seconds=TOPE_PARED_SEGUNDOS)


def salida_sin_metricas(
    comando: str, *, ahora: dt.datetime | None = None, incluye_placements: bool = False
) -> int:
    """Lo que hace un consumidor cuando `guarda` dijo que no.

    Antes de fin_de_intentos(hoy): imprime TEXTO_ESPERA en stdout y devuelve
    SALIDA_ESPERA (75), porque queda otro horario hoy. Desde ese momento
    imprime en stderr que el dia se perdio y devuelve 1 para que cron-avisa
    avise. Nunca escribe en la base ni manda Telegram por su cuenta (el canal
    de "trabajo que no hizo su trabajo" es cron-avisa).

    `incluye_placements` lo pasa avisos-campana porque su guarda exige ademas
    la corrida de placements: el aviso nombra las dos fuentes y no atribuye a
    las metricas una falta que puede ser de los placements."""
    ahora = ahora or dt.datetime.now(dt.UTC)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=dt.UTC)
    fin = fin_de_intentos(ahora.date())
    if ahora < fin:
        print(TEXTO_ESPERA)
        return SALIDA_ESPERA
    from app.ads.descarga import PLAZO_SONDEO_SEGUNDOS
    from app.ads.diario import TOPE_PARED_SEGUNDOS

    inicio = fin - dt.timedelta(seconds=TOPE_PARED_SEGUNDOS)
    sondeo = inicio + dt.timedelta(seconds=PLAZO_SONDEO_SEGUNDOS)
    faltan = (
        "las métricas o los placements de ayer" if incluye_placements else "las métricas de ayer"
    )
    print(
        f"{comando}: hoy no corrió; {faltan} no llegaron en ningún"
        " intento de la ingesta."
        f" El último intento empieza a las {inicio:%H:%M} UTC y su sondeo se corta"
        f" solo a las {sondeo:%H:%M} UTC; desde las {fin:%H:%M} UTC la guarda da el"
        " día por perdido. Mañana se intenta otra vez.",
        file=sys.stderr,
    )
    return 1


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
    exigir_placements, la corrida de placements de hoy).

    Deja la conexion como la recibio: sin autocommit, el SELECT abre una
    transaccion y el `SET TRANSACTION ISOLATION LEVEL` que viene despues
    (jev-senales, `_leer_mundo`) revienta con ActiveSqlTransaction."""
    if forzar:
        return True
    sin_transaccion = conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    try:
        for plataforma in plataformas:
            if not metricas_de_ayer_listas(conn, plataforma, hoy=hoy):
                return False
            if exigir_placements and not placements_de_ayer_listos(conn, plataforma, hoy=hoy):
                return False
        return True
    finally:
        if sin_transaccion:
            conn.rollback()
