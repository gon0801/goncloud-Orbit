"""Lectura acotada a las siete entidades candidatas; sin estados actuales historizados."""

import datetime as dt
import json
import os

from psycopg.rows import dict_row

from app.db import connect
from app.redaction import register_secret, scrub

ids = [3859, 3911, 3920, 4924, 5891, 5892, 5896]
queries = {
    "config_por_ciclo": (
        "SELECT DISTINCT d.cycle_id,v.id AS config_version_id,v.settings->"
        "'ads_pause_economica' AS pause_economica FROM decision d JOIN con"
        "fig_version v ON v.id=d.config_version_id WHERE d.decided_at >= '"
        "2026-10-05' AND d.decided_at < '2026-11-04' ORDER BY d.cycle_id,v"
        ".id"
    ),
    "decisiones_candidatos": (
        "SELECT d.id,d.cycle_id,d.ad_entity_id,d.kind,d.decided_at,d.windo"
        "w_start,d.window_end,d.inputs FROM decision d WHERE d.ad_entity_i"
        "d=ANY(%s) AND d.decided_at >= '2026-10-05' AND d.decided_at < '20"
        "26-11-04' ORDER BY d.cycle_id,d.ad_entity_id"
    ),
    "ciclos": (
        "SELECT id,platform,status,notes FROM optimizer_cycle WHERE starte"
        "d_at >= '2026-10-05' AND started_at < '2026-11-04' AND platform I"
        "S NOT NULL ORDER BY id"
    ),
    "identidades_actuales": (
        "SELECT e.id,e.kind,e.external_id,e.parent_id FROM ad_entity e WHERE e.id=ANY(%s)"
    ),
    "tablas_historicas": (
        "SELECT table_name,column_name FROM information_schema.columns WHE"
        "RE table_schema='public' AND (table_name ILIKE '%state%' OR table"
        "_name ILIKE '%goal%' OR table_name ILIKE '%report%' OR table_name"
        " ILIKE '%descarg%') ORDER BY table_name,ordinal_position"
    ),
    "reportes_fuente": (
        "SELECT o.ad_entity_id,o.source_report_id,min(o.metric_date) AS de"
        "sde,max(o.metric_date) AS hasta,min(o.observed_at) AS observado,a"
        ".profile_id,a.report_name FROM ads_metric_observation o LEFT JOIN"
        " ads_report_result a ON a.source_report_id=o.source_report_id AND"
        " a.status='written' WHERE o.ad_entity_id=ANY(%s) AND o.observed_a"
        "t >= '2026-10-05' AND o.observed_at < '2026-11-04' GROUP BY o.ad_"
        "entity_id,o.source_report_id,a.profile_id,a.report_name ORDER BY "
        "min(o.observed_at) DESC LIMIT 28"
    ),
}
dsn = os.environ["ORBIT_DSN_READ"]
register_secret(dsn)
out = {"utc": dt.datetime.now(dt.UTC), "queries": {}}
try:
    with connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='15s'")
        for name, sql in queries.items():
            rows = (
                conn.execute(sql, (ids,))
                if name in ("decisiones_candidatos", "identidades_actuales", "reportes_fuente")
                else conn.execute(sql)
            ).fetchall()
            out["queries"][name] = {
                "sql": sql,
                "params": ids
                if name in ("decisiones_candidatos", "identidades_actuales", "reportes_fuente")
                else [],
                "rows": rows,
            }
    print(json.dumps(out, default=str))
except Exception as exc:
    print(json.dumps({"error_type": type(exc).__name__, "error": scrub(str(exc))}))
    raise SystemExit(1) from None
