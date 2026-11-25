"""Revalidacion externa. Ejecutar desde checkout o definir ORBIT_REVIEW_REPO.

Solo escribe en la base desechable thermo_optimizer_current. Las unicas
fronteras reemplazadas son notificaciones; el ciclo, SQL y motores son reales.
La suite externa contiene expectativas de cierre, no expectativas del bug.
"""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(os.environ.get("ORBIT_REVIEW_REPO", os.getcwd())).resolve()
if not (ROOT / "app/cycle.py").is_file():
    raise RuntimeError("cwd/ORBIT_REVIEW_REPO debe ser el checkout revisado")
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from app import cycle
from app.optimizer import replay
import test_cycle as seeds

DB = "thermo_optimizer_current"


def metadata():
    sha = os.environ.get("ORBIT_REVIEW_SHA")
    if not sha:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    for mod in (cycle, replay, seeds):
        if not Path(mod.__file__).resolve().is_relative_to(ROOT):
            raise RuntimeError(f"Import fuera del checkout: {mod.__file__}")
    return {"sha": sha, "python": sys.version, "repo": str(ROOT),
            "imports": [m.__file__ for m in (cycle, replay, seeds)]}


@contextmanager
def database():
    dsn = os.environ["ORBIT_OPTIMIZER_TEST_DSN"]
    params = conninfo_to_dict(dsn)
    if (params.get("host"), params.get("port")) not in {
        ("127.0.0.1", "55449"), ("orbit-review-current-pg", "5432")
    } or params.get("dbname") != "postgres":
        raise RuntimeError("DSN fuera del PG desechable autorizado")
    with psycopg.connect(dsn, autocommit=True) as admin:
        assert admin.info.server_version // 10000 == 16
        roles = {r[0] for r in admin.execute("SELECT rolname FROM pg_roles")}
        assert {"app_read", "app_ingest", "app_decide", "app_admin", "app_jev"} <= roles
        # CREATE falla si existe: nunca borrar una base que no creo esta corrida.
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB)))
        try:
            with psycopg.connect(dsn, dbname=DB, autocommit=True) as conn:
                conn.execute("SET TIME ZONE 'UTC'")
                applied = []
                for path in sorted((ROOT / "migrations").glob("*.sql")):
                    if path.name.startswith("0011_") or "_reversa_" in path.name:
                        continue
                    conn.execute(path.read_text())
                    applied.append(path.name)
                print(json.dumps({"database": DB, "migrations": len(applied),
                                  "last": applied[-1]}), flush=True)
                yield conn
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(DB)))


def run_cycle(conn, decided=seeds.DECIDED_AT):
    # Evita leer secretos o enviar avisos. Ninguna decision se sustituye.
    with patch.object(cycle, "_fase_notifica", return_value={}):
        return cycle.corre_ciclo(
            conn, platform="amazon_us", owner="review-optimizer",
            decided_at=decided,
            aplicador_factory=lambda conn, **kwargs: cycle.Aplicador(conn, **kwargs),
        )


# ARREGLOS 05 J retiro la pausa de la era bandas: sin `ads_bid_politica` la
# hoja solo registra `politica_apagada`. El escenario declara la politica viva
# para conservar la pausa que estas aserciones siempre exigieron; no cambia
# ninguna asercion.
POLITICA_VIVA = {"ads_bid_politica_amazon_us": "niveles_v3"}


def harvest_scenario(conn, *, reobservado=False, keyword_propia=False):
    ids = seeds._siembra_maestra(conn, settings=POLITICA_VIVA)
    if keyword_propia:
        seeds._entidad(conn, "amazon_us", "keyword", "review-own", parent=ids["ag"],
                       match_type="EXACT", keyword_text="Buena Yarda")
    if reobservado:
        run = seeds._run(conn)
        # Backfill de una fecha madura que entra despues del reloj inicial,
        # antes de adquirir el snapshot TX2. INSERT append-only, sin mocks.
        seeds._termino(conn, run, ids["ag"], "buena yarda", dt.date(2026, 7, 15),
                       seeds.DECIDED_AT + dt.timedelta(seconds=1),
                       cost="3.33", ad_revenue="40", clicks=2, orders=1)
    try:
        res = run_cycle(conn)
        rows = seeds._decisions_de(conn, res.cycle_id)
        return {"status": res.status, "kinds": [r[1] for r in rows],
                "rows": rows, "notes": json.loads(res.notes)}
    except psycopg.errors.CheckViolation as exc:
        return {"status": conn.execute(
            "SELECT status FROM optimizer_cycle ORDER BY id DESC LIMIT 1").fetchone()[0],
            "constraint": exc.diag.constraint_name,
            "persisted": conn.execute("SELECT count(*) FROM decision").fetchone()[0]}


def convergence_scenario(conn):
    seeds._siembra_margen(conn)
    # Envelope live, goals shadow: recorrido/lecturas/TX3/apply-cola reales,
    # sin efectos externos. La plataforma aprende el target del ciclo live.
    conn.execute("UPDATE ads_optimizer_goal SET mode='shadow'")
    base = {"ads_optimizer_mode": "live", "ads_target_acos_pct_amazon_us": 30,
            "ads_target_fraccion_margen_amazon_us": "0.5",
            "ads_bid_politica_amazon_us": "niveles_v3"}
    seeds._config_version(conn, base)
    first = run_cycle(conn)
    initial = json.loads(first.notes)["target"]
    # Simula envejecimiento de la ultima ingesta completada en la fixture.
    # No altera vistas ni resolutores. El estado equivale a un ledger que
    # lleva >7 dias sin sincronizar y un setting nuevo de 20.
    conn.execute("UPDATE ingest_run SET started_at=CURRENT_TIMESTAMP-interval '10 days', "
                 "finished_at=CURRENT_TIMESTAMP-interval '10 days' "
                 "WHERE source='accounting_ledger_events'")
    seeds._config_version(conn, {**base, "ads_target_acos_pct_amazon_us": 20})
    steps = []
    for _ in range(3):
        res = run_cycle(conn)
        notes = json.loads(res.notes)
        targets = conn.execute("SELECT target_acos_pct FROM target_acos_ciclo WHERE cycle_id=%s",
                               (res.cycle_id,)).fetchall()
        steps.append({"status": res.status, "target": notes["target"], "leaves": targets})
    return {"initial_status": first.status, "initial": initial, "steps": steps}


def main():
    print(json.dumps(metadata(), ensure_ascii=False), flush=True)
    for name, fn in (("12", convergence_scenario),
                     ("13-control", harvest_scenario),
                     ("13", lambda c: harvest_scenario(c, reobservado=True)),
                     ("ARREGLOS01-B", lambda c: harvest_scenario(c, keyword_propia=True))):
        with database() as conn:
            result = fn(conn)
            result.pop("rows", None)
            print(json.dumps({"case": name, "result": result}, default=str), flush=True)


if __name__ == "__main__":
    main()
