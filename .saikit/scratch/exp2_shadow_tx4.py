"""Experimento 2 (CRITICO): ciclo SHADOW con conexion de PRODUCCION (sin
autocommit, como app.db.connect + app.cli): TX4 (encola_cortes) y el sello
notes['apply'] quedan en una transaccion abierta que el close() revierte.

Control: misma corrida con autocommit=True (como los tests) -> las filas
persisten. La delta ES la disciplina de commits, no la logica.
"""

import os
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import psycopg  # noqa: E402
from psycopg import sql as pgsql  # noqa: E402
from test_cycle import DECIDED_AT, _siembra_maestra  # noqa: E402
from test_schema import SQL, SQL2  # noqa: E402

from app import cycle as ciclo  # noqa: E402

dsn = os.environ["ORBIT_TEST_DSN"]
RESULTADOS = {}

for modo in ("produccion_sin_autocommit", "control_tests_autocommit"):
    db = f"orbit_adv_exp2_{modo[:12]}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
    admin.close()

    setup = psycopg.connect(dsn, dbname=db, autocommit=True)
    setup.execute("SET TIME ZONE 'UTC'")
    setup.execute(SQL)
    setup.execute(SQL2)
    _siembra_maestra(setup, escalera="shadow")  # decisiones: bid+pause+negative+harvest
    setup.close()

    # La conexion del ciclo: como app.cli -> app.db.connect (SIN autocommit en
    # produccion; los tests usan autocommit=True)
    conn = psycopg.connect(dsn, dbname=db, autocommit=(modo.startswith("control")))
    conn.execute("SET TIME ZONE 'UTC'")
    res = ciclo.corre_ciclo(
        conn, platform="amazon_us", owner="adv:host:1", decided_at=DECIDED_AT, heartbeat_cada=1
    )
    conn.close()  # el CLI cierra sin commit en el finally

    ver = psycopg.connect(dsn, dbname=db, autocommit=True)
    cola = ver.execute("SELECT count(*) FROM apply_queue").fetchone()[0]
    estados = ver.execute("SELECT estado, count(*) FROM apply_queue GROUP BY estado").fetchall()
    env = ver.execute(
        "SELECT status, notes FROM optimizer_cycle WHERE id = %s", (res.cycle_id,)
    ).fetchone()
    decisiones = ver.execute(
        "SELECT kind, count(*) FROM decision WHERE cycle_id = %s GROUP BY kind", (res.cycle_id,)
    ).fetchall()
    ver.close()

    RESULTADOS[modo] = {
        "cycle_id": res.cycle_id,
        "status_devuelto": res.status,
        "notas_inmemory_tienen_apply": '"apply"' in res.notes,
        "filas_apply_queue": cola,
        "estados_cola": estados,
        "envelope_status": env[0],
        "envelope_notes_tienen_apply": '"apply"' in (env[1] or ""),
        "decisiones": decisiones,
    }

    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db)))
    admin.close()

for modo, r in RESULTADOS.items():
    print(f"\n=== {modo} ===")
    for k, v in r.items():
        print(f"  {k}: {v}")
