"""Experimento 1: semantica de psycopg3 SIN autocommit (como app.db.connect).

Verifica que:
 1. un SELECT abre transaccion implicita;
 2. `with conn.transaction():` con transaccion implicita previa = SAVEPOINT
    (NO commit);
 3. conn.close() sin commit -> ROLLBACK (otra sesion no ve la fila).
"""

import os
import sys

import psycopg

sys.exit_code = 0
dsn = os.environ["ORBIT_TEST_DSN"]

admin = psycopg.connect(dsn, autocommit=True)
admin.execute("DROP DATABASE IF EXISTS orbit_adv_exp1 WITH (FORCE)")
admin.execute("CREATE DATABASE orbit_adv_exp1")
admin.close()

setup = psycopg.connect(dsn, dbname="orbit_adv_exp1", autocommit=True)
setup.execute("CREATE TABLE t (x int)")
setup.close()

# Conexion como produccion: SIN autocommit
conn = psycopg.connect(dsn, dbname="orbit_adv_exp1")
conn.execute("SELECT 1")  # transaccion implicita abierta por un SELECT
estado1 = conn.info.transaction_status  # 2 = INTRANS
with conn.transaction():
    conn.execute("INSERT INTO t VALUES (1)")
    estado_dentro = conn.info.transaction_status
estado2 = conn.info.transaction_status  # si sigue INTRANS -> savepoint, no commit

otra = psycopg.connect(dsn, dbname="orbit_adv_exp1", autocommit=True)
visible = otra.execute("SELECT count(*) FROM t").fetchone()[0]
conn.close()  # cierre sin commit
visible_tras_close = otra.execute("SELECT count(*) FROM t").fetchone()[0]
otra.close()

print(f"tras SELECT: status={estado1} (2=INTRANS)")
print(f"dentro del bloque: status={estado_dentro}")
print(f"tras bloque transaction(): status={estado2} (2=INTRANS => savepoint)")
print(f"filas visibles por otra sesion tras el bloque: {visible}")
print(f"filas visibles tras conn.close(): {visible_tras_close}")

admin = psycopg.connect(dsn, autocommit=True)
admin.execute("DROP DATABASE IF EXISTS orbit_adv_exp1 WITH (FORCE)")
admin.close()
