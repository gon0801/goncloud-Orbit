#!/usr/bin/env bash
# M.3: mide el ciclo shadow con niveles_v3 por plataforma sobre la copia de
# produccion (la que arma ejecucion/0.b/copia.sh con las tablas de M.3).
# Solo LEE produccion (jamas la toca: todo corre sobre la copia local
# desechable). No imprime DSNs ni credenciales.
# Uso (lo corre el lead, NO el owner de M.3):
#   cd ~/dev/wt-bids-02-s2 && bash docs/evidencia/bids-02/ejecucion/M.3/mide_ciclo.sh [--dsn DSN_COPIA]
# Protocolo: 5 corridas shadow seguidas por plataforma sobre la misma copia;
# reporta cada tiempo y la mediana. El lead corre el mismo script en esta
# rama y en origin/master sobre copias frescas identicas: esperado, la
# mediana de niveles_v3 no pasa de la de master mas 30 % (el veto entre
# corridas afecta igual a ambas ramas, la comparacion sigue valida).
# La pata de master corre con MIDE_SIN_CLAVE=1 (sin clave de politica:
# ese codigo falla cerrado con niveles_v3); la de esta rama sin ella.
# La copia trae el esquema de prod (sin la 0060) y sin la clave de politica:
# el medidor aplica la 0060 e inserta una config_version con niveles_v3 en
# LA COPIA (desechable, jamas produccion) y lo declara en la salida.
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
cd "$REPO"

DSN_ARG=""
if [ "${1:-}" = "--dsn" ]; then DSN_ARG="${2:-}"; fi

ORBIT_M3_DSN="${ORBIT_M3_DSN:-}" PYTHONPATH=. .venv/bin/python - "$DSN_ARG" <<'PYEOF'
"""Medidor M.3 (corre dentro de mide_ciclo.sh, nunca a mano)."""
import datetime as dt
import os
import sys
import time
from statistics import median

import psycopg

BASE_COPIA = "orbit_copia_bids02_0b"
CORRIDAS = 5
PLATAFORMAS = ("amazon_mx", "amazon_us")

sys.path.insert(0, os.getcwd())
from app import cycle as ciclo  # noqa: E402


def _dsn() -> str:
    if len(sys.argv) > 1 and sys.argv[1]:
        return sys.argv[1]
    if os.environ.get("ORBIT_M3_DSN"):
        return os.environ["ORBIT_M3_DSN"]
    base = os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")
    return base.rsplit("/", 1)[0] + "/" + BASE_COPIA


_REPARA_SECUENCIAS = """
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT s.relname AS sec, t.relname AS tabla, a.attname AS col
               FROM pg_class s
               JOIN pg_depend d ON d.objid = s.oid
               JOIN pg_class t ON t.oid = d.refobjid
               JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
              WHERE s.relkind = 'S' AND t.relnamespace = 'public'::regnamespace
    LOOP
        EXECUTE format(
            'SELECT setval(%L, COALESCE(max(%I), 1)) FROM %I',
            'public.' || r.sec, r.col, r.tabla
        );
    END LOOP;
END $$;
"""


def _asegura_base(conn) -> None:
    existe = conn.execute("SELECT to_regclass('public.v_hoja_activa') IS NOT NULL").fetchone()[0]
    if not existe:
        conn.execute(open("migrations/0060_bids02_base_lectura.sql", encoding="utf-8").read())
        print("base: aplique 0060 a la copia (esquema de prod no la trae)")
    else:
        print("base: la copia ya trae v_hoja_activa")
    conn.execute(_REPARA_SECUENCIAS)
    print("base: secuencias reparadas (el dump excluye *_seq)")
    sin_clave = os.environ.get("MIDE_SIN_CLAVE") == "1"
    if sin_clave:
        print("base: pata de origin/master, sin clave de politica (falla cerrado con niveles_v3)")
        conn.execute(
            "INSERT INTO config_version (settings) SELECT settings"
            " - 'ads_bid_politica_amazon_mx' - 'ads_bid_politica_amazon_us'"
            " || '{\"ads_optimizer_mode\": \"shadow\"}'"
            " FROM config_version ORDER BY id DESC LIMIT 1"
        )
        print("base: inserte config_version shadow sin clave en la copia (hereda el resto)")
        return
    n = conn.execute(
        "SELECT count(*) FROM config_version WHERE settings ? 'ads_bid_politica_amazon_us'"
    ).fetchone()[0]
    if n == 0:
        conn.execute(
            "INSERT INTO config_version (settings) SELECT settings"
            " || '{\"ads_optimizer_mode\": \"shadow\","
            " \"ads_bid_politica_amazon_mx\": \"niveles_v3\","
            " \"ads_bid_politica_amazon_us\": \"niveles_v3\"}'"
            " FROM config_version ORDER BY id DESC LIMIT 1"
        )
        print("base: inserte config_version con niveles_v3 en la copia (hereda target/fraccion)")
    else:
        print("base: la copia ya trae la clave de politica")


def main() -> int:
    dsn = _dsn()
    assert BASE_COPIA in dsn, f"el DSN no apunta a la copia {BASE_COPIA}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        _asegura_base(conn)
        for plataforma in PLATAFORMAS:
            tiempos = []
            for i in range(CORRIDAS):
                inicio = time.perf_counter()
                res = ciclo.corre_ciclo(
                    conn,
                    platform=plataforma,
                    owner=f"mide-ciclo:{i}",
                    decided_at=dt.datetime.now(dt.UTC),
                    heartbeat_cada=1000,
                )
                tiempos.append(time.perf_counter() - inicio)
                assert res.status == "done", (res.status, res.notes)
            hojas = conn.execute(
                "SELECT count(*) FROM v_hoja_activa WHERE platform = %s", (plataforma,)
            ).fetchone()[0]
            det = " ".join(f"{t:.1f}" for t in tiempos)
            print(f"{plataforma}: ciclo shadow {det} s; mediana {median(tiempos):.1f} s"
                  f" ({hojas} hojas activas, {CORRIDAS} corridas)")
    return 0


raise SystemExit(main())
PYEOF
