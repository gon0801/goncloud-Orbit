"""Experimento 3: ciclo LIVE cuya fabrica aborta (sin ORBIT_SECRETS_DIR ->
AdsCredentials.from_secrets_dir revienta). El camino fail-closed-auditado:

- TX4 encola cortes modo LIVE (el Aplicador sin credenciales re-resuelve modo);
- notas['apply_error'] queda SOLO en memoria: el sello se pierde con el
  rollback del close (produccion, sin autocommit).
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
assert "ORBIT_SECRETS_DIR" not in os.environ, "este experimento exige SIN secrets dir"

db = f"orbit_adv_exp3_{socket.gethostname().lower()}_{os.getpid()}"
admin = psycopg.connect(dsn, autocommit=True)
admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
admin.close()

setup = psycopg.connect(dsn, dbname=db, autocommit=True)
setup.execute("SET TIME ZONE 'UTC'")
setup.execute(SQL)
setup.execute(SQL2)
_siembra_maestra(setup, escalera="live")
setup.close()

conn = psycopg.connect(dsn, dbname=db)  # produccion: sin autocommit
conn.execute("SET TIME ZONE 'UTC'")
res = ciclo.corre_ciclo(
    conn, platform="amazon_us", owner="adv:host:2", decided_at=DECIDED_AT, heartbeat_cada=1
)
conn.close()

ver = psycopg.connect(dsn, dbname=db, autocommit=True)
cola = ver.execute(
    "SELECT modo, estado, count(*) FROM apply_queue GROUP BY modo, estado"
).fetchall()
env = ver.execute(
    "SELECT status, notes FROM optimizer_cycle WHERE id = %s", (res.cycle_id,)
).fetchone()
ver.close()

print("status devuelto:", res.status)
print("notas in-memory tienen apply_error:", '"apply_error"' in res.notes)
print("filas apply_queue (persistidas):", cola)
print("envelope status:", env[0])
print("envelope notes tienen apply_error:", '"apply_error"' in (env[1] or ""))

admin = psycopg.connect(dsn, autocommit=True)
admin.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db)))
admin.close()
