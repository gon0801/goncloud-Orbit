"""Estado y avisos persistentes de la ingesta principal de Amazon Ads."""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from collections.abc import Callable
from typing import Any

from app import notifica
from app.db import connect
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

SOURCE = "amazon_ads_reports_v3"
MAX_INTENTOS = 6


def incidentes_de_run(
    *, source: str, ok: bool, unidades: list[tuple]
) -> list[tuple[int | None, str | None, str]]:
    """Una alerta por perfil afectado; el reporte y motivo quedan en A.2."""
    if source != SOURCE:
        return []
    scopes = {
        (profile_id, platform, "fallo")
        if profile_id is not None and platform is not None
        else (None, None, "fallo")
        for profile_id, platform, _report_name, estado in unidades
        if estado in ("failed", "rejected", "global_failed") or (not ok and estado == "pending")
    }
    if not ok and not unidades:
        scopes.add((None, None, "fallo"))
    return sorted(scopes, key=lambda scope: (scope[1] or "", scope[0] or 0))


def unidades_atrasadas(
    ultimos: list[tuple[int, str, dt.date | None]], hoy: dt.date
) -> list[tuple[int, str, str]]:
    return [(perfil, plataforma, "atraso") for perfil, plataforma, fecha in ultimos if fecha != hoy]


_SQL_RUN = "SELECT source, ok FROM ingest_run WHERE id = %s"
_SQL_UNIDADES = """
SELECT profile_id, platform::text, report_name, status
  FROM ads_report_result WHERE ingest_run_id = %s
"""
_SQL_ABRIR = """
INSERT INTO ads_ingest_incident (profile_id, platform, tipo, opened_run_id)
VALUES (%s, %s::platform, %s, %s)
ON CONFLICT (profile_id, platform, tipo) WHERE closed_at IS NULL DO NOTHING
"""
_SQL_RECUPERAR = """
UPDATE ads_ingest_incident
   SET recovered_run_id = %s, recovered_at = now(),
       closed_at = CASE WHEN alert_sent_at IS NULL THEN now() ELSE closed_at END
 WHERE profile_id IS NOT DISTINCT FROM %s
   AND platform IS NOT DISTINCT FROM %s::platform
   AND closed_at IS NULL AND recovered_at IS NULL
"""
_SQL_PENDIENTES = """
SELECT id, profile_id, platform::text, tipo, recovered_at IS NOT NULL,
       alert_sent_at IS NOT NULL, alert_attempts, recovery_attempts
  FROM ads_ingest_incident
 WHERE closed_at IS NULL
   AND ((alert_sent_at IS NULL AND recovered_at IS NULL AND alert_attempts < %s)
        OR (alert_sent_at IS NOT NULL AND recovered_at IS NOT NULL
            AND recovery_sent_at IS NULL AND recovery_attempts < %s))
 ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 20
"""
_SQL_INTENTO_ALERTA = """
UPDATE ads_ingest_incident
   SET alert_attempts = alert_attempts + 1, last_attempt_at = now(),
       alert_sent_at = CASE WHEN %s THEN now() ELSE alert_sent_at END
 WHERE id = %s
"""
_SQL_INTENTO_RECUPERACION = """
UPDATE ads_ingest_incident
   SET recovery_attempts = recovery_attempts + 1, last_attempt_at = now(),
       recovery_sent_at = CASE WHEN %s THEN now() ELSE recovery_sent_at END,
       closed_at = CASE WHEN %s THEN now() ELSE closed_at END
 WHERE id = %s
"""


def _texto(tipo: str, perfil: int | None, plataforma: str | None) -> str:
    scope = "global" if perfil is None else f"{plataforma} / perfil {perfil}"
    if tipo == "recuperacion":
        return f"[Orbit Ads] ingesta principal recuperada: {scope}. Exito real registrado."
    if tipo == "atraso":
        return f"[Orbit Ads] ingesta principal atrasada a las 10:30 UTC: {scope}."
    return f"[Orbit Ads] fallo de ingesta principal: {scope}. Revisar /salud."


def entregar_pendientes(conn: Any, *, enviar: Callable[[str], bool] | None = None) -> None:
    """Reintenta sin convertir un canal desactivado en entrega exitosa.

    El bloqueo de fila cubre el POST y el acuse; otro cron no envia la misma
    alerta a la vez. Un fallo deja pending con intento contado.
    """
    if not notifica.canal_activo():
        return
    enviar = enviar or notifica._envia_texto
    with conn.transaction():
        filas = conn.execute(_SQL_PENDIENTES, (MAX_INTENTOS, MAX_INTENTOS)).fetchall()
        for id_, perfil, plataforma, tipo, recuperado, entregado, _a, _r in filas:
            clase = "recuperacion" if recuperado and entregado else tipo
            enviado = enviar(_texto(clase, perfil, plataforma))
            if clase == "recuperacion":
                conn.execute(_SQL_INTENTO_RECUPERACION, (enviado, enviado, id_))
            else:
                conn.execute(_SQL_INTENTO_ALERTA, (enviado, id_))


def procesar_run(conn: Any, run_id: int) -> None:
    """Abre fallos de la run sellada y recupera solo scopes escritos en ella."""
    with conn.transaction():
        run = conn.execute(_SQL_RUN, (run_id,)).fetchone()
        if run is None or run[1] is None or run[0] != SOURCE:
            return
        source, ok = run
        unidades = conn.execute(_SQL_UNIDADES, (run_id,)).fetchall()
        fallos = incidentes_de_run(source=source, ok=ok, unidades=unidades)
        for perfil, plataforma, tipo in fallos:
            conn.execute(_SQL_ABRIR, (perfil, plataforma, tipo, run_id))
        if ok:
            exitos = {(p, plat) for p, plat, _nombre, estado in unidades if estado == "written"}
            for perfil, plataforma in exitos:
                conn.execute(_SQL_RECUPERAR, (run_id, perfil, plataforma))
            if exitos and (None, None, "fallo") not in fallos:
                conn.execute(_SQL_RECUPERAR, (run_id, None, None))
    entregar_pendientes(conn)


_SQL_ULTIMOS_EXITO = """
SELECT scope.profile_id, scope.platform::text,
       max((r.finished_at AT TIME ZONE 'UTC')::date) FILTER
           (WHERE r.ok = true AND a.status = 'written') AS fecha_exito
  FROM (SELECT DISTINCT profile_id, platform FROM ads_report_result
         WHERE profile_id IS NOT NULL AND platform IS NOT NULL
           AND ingest_run_id IN (SELECT id FROM ingest_run WHERE source = %s)) scope
  LEFT JOIN ads_report_result a
    ON a.profile_id = scope.profile_id AND a.platform = scope.platform
  LEFT JOIN ingest_run r ON r.id = a.ingest_run_id AND r.source = %s
 GROUP BY scope.profile_id, scope.platform
"""
_SQL_EXITO_GLOBAL_HOY = """
SELECT EXISTS (SELECT 1 FROM ingest_run WHERE source = %s AND ok = true
  AND (finished_at AT TIME ZONE 'UTC')::date = %s)
"""


def comprobar_atraso(conn: Any, *, ahora: dt.datetime | None = None) -> None:
    """Cron a las 10:30 UTC y reintentos posteriores; no alerta antes."""
    ahora = ahora or dt.datetime.now(dt.UTC)
    if ahora.astimezone(dt.UTC).time() < dt.time(10, 30):
        return
    hoy = ahora.astimezone(dt.UTC).date()
    with conn.transaction():
        ultimos = conn.execute(_SQL_ULTIMOS_EXITO, (SOURCE, SOURCE)).fetchall()
        for perfil, plataforma, tipo in unidades_atrasadas(ultimos, hoy):
            conn.execute(_SQL_ABRIR, (perfil, plataforma, tipo, None))
        if not ultimos and not conn.execute(_SQL_EXITO_GLOBAL_HOY, (SOURCE, hoy)).fetchone()[0]:
            conn.execute(_SQL_ABRIR, (None, None, "atraso", None))
    entregar_pendientes(conn)


_SQL_SALUD = """
WITH exitos AS (
  SELECT DISTINCT ON (a.profile_id, a.report_name)
         a.profile_id, a.report_name, r.finished_at, a.source_report_id
    FROM ads_report_result a JOIN ingest_run r ON r.id = a.ingest_run_id
   WHERE r.source = %s AND r.ok = true AND a.status = 'written'
     AND a.platform = %s::platform
   ORDER BY a.profile_id, a.report_name, r.finished_at DESC
), recientes AS (
  SELECT DISTINCT ON (a.profile_id, a.report_name)
         a.profile_id, a.report_name, a.status, a.ingest_run_id, a.observed_at
    FROM ads_report_result a JOIN ingest_run r ON r.id = a.ingest_run_id
   WHERE r.source = %s AND a.platform = %s::platform
     AND a.report_name IS NOT NULL
   ORDER BY a.profile_id, a.report_name, a.ingest_run_id DESC, a.id DESC
), fechas AS (
  SELECT a.profile_id, a.report_name, max(m.metric_date) AS metric_date
    FROM ads_report_result a JOIN ingest_run r ON r.id = a.ingest_run_id
    JOIN ads_metric_observation m
      ON m.source_report_id = a.source_report_id AND m.ingest_run_id = a.ingest_run_id
   WHERE r.source = %s AND r.ok = true AND a.status = 'written'
     AND a.platform = %s::platform AND a.report_name <> 'search_terms'
   GROUP BY a.profile_id, a.report_name
  UNION ALL
  SELECT a.profile_id, a.report_name, max(m.metric_date)
    FROM ads_report_result a JOIN ingest_run r ON r.id = a.ingest_run_id
    JOIN search_term_observation m
      ON m.source_report_id = a.source_report_id AND m.ingest_run_id = a.ingest_run_id
   WHERE r.source = %s AND r.ok = true AND a.status = 'written'
     AND a.platform = %s::platform AND a.report_name = 'search_terms'
   GROUP BY a.profile_id, a.report_name
)
SELECT scope.profile_id, scope.report_name, e.finished_at, f.metric_date,
       reciente.status, reciente.ingest_run_id, reciente.observed_at
  FROM (SELECT DISTINCT profile_id, report_name FROM ads_report_result
         WHERE platform = %s::platform AND report_name IS NOT NULL
           AND ingest_run_id IN (SELECT id FROM ingest_run WHERE source = %s)) scope
  LEFT JOIN exitos e USING (profile_id, report_name)
  LEFT JOIN recientes reciente USING (profile_id, report_name)
  LEFT JOIN fechas f USING (profile_id, report_name)
 ORDER BY scope.profile_id, scope.report_name
"""
_SQL_SALUD_INCIDENTES = """
SELECT profile_id, platform::text, tipo, alert_sent_at, recovered_at, recovery_sent_at,
       alert_attempts, recovery_attempts, opened_at
  FROM ads_ingest_incident
 WHERE (platform = %s::platform OR platform IS NULL) AND closed_at IS NULL
 ORDER BY opened_at DESC
"""


def bloque_salud(conn: Any, plataforma: str, *, ahora: dt.datetime | None = None) -> dict:
    ahora = ahora or dt.datetime.now(dt.UTC)
    filas = conn.execute(
        _SQL_SALUD,
        (
            SOURCE,
            plataforma,
            SOURCE,
            plataforma,
            SOURCE,
            plataforma,
            SOURCE,
            plataforma,
            plataforma,
            SOURCE,
        ),
    ).fetchall()
    reportes = [
        {
            "profile_id": perfil,
            "reporte": reporte,
            "ultimo_exito": exito.isoformat() if exito else None,
            "metric_date": fecha.isoformat() if fecha else None,
            "edad_dias": (ahora.astimezone(dt.UTC).date() - fecha).days if fecha else None,
            "ultimo_estado": estado,
            "ultima_corrida": run_id,
            "ultimo_evento": evento.isoformat() if evento else None,
        }
        for perfil, reporte, exito, fecha, estado, run_id, evento in filas
    ]
    filas_incidentes = conn.execute(_SQL_SALUD_INCIDENTES, (plataforma,)).fetchall()
    incidentes = []
    for fila in filas_incidentes:
        (
            perfil,
            scope_platform,
            tipo,
            enviado,
            recuperado,
            recuperacion,
            intentos,
            intentos_recuperacion,
            abierto,
        ) = fila
        incidentes.append(
            {
                "profile_id": perfil,
                "platform": scope_platform,
                "tipo": tipo,
                "estado_entrega": "sent" if enviado else "pending",
                "recuperado": recuperado is not None,
                "recuperacion_entregada": recuperacion is not None,
                "intentos": intentos,
                "intentos_recuperacion": intentos_recuperacion,
                "abierto_desde": abierto.isoformat(),
            }
        )
    return {"fuente": SOURCE, "reportes": reportes, "incidentes": incidentes}


def intentar_procesar_run(conn: Any, run_id: int) -> None:
    """El canal de salud jamas cambia el resultado contable de ingesta."""
    try:
        procesar_run(conn, run_id)
    except Exception as exc:  # noqa: BLE001 - aviso auxiliar
        logger.warning("ads salud: no pude procesar run %s: %s", run_id, scrub(str(exc)))


def main(argv: list[str] | None = None) -> int:
    if argv:
        print(f"argumentos desconocidos para 'ads-salud': {argv}", file=sys.stderr)
        return 2
    dsn = os.environ.get("ORBIT_DSN_INGEST")
    if not dsn:
        print("ORBIT_DSN_INGEST no esta definido", file=sys.stderr)
        return 2
    try:
        with connect(dsn) as conn:
            comprobar_atraso(conn)
    except Exception as exc:
        print(f"ads-salud fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    return 0
