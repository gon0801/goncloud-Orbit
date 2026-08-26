"""Experimento 4: filas huerfanas de la cola.

A) PAUSE/NEGATIVE released que no alcanzo quota: libera_vencidos SOLO
   selecciona estado='pending_veto' -> al dia siguiente (quota disponible)
   la fila released NUNCA se re-procesa; reconcilia_harvest solo mira jobs
   de harvest y filas applying de negative. La clave de efecto queda
   bloqueada para siempre (claves_bloqueadas la ve NO terminal).

B) PAUSE en applying tras fallo ambiguo (AdsApiError 5xx/red en el PUT):
   la matriz §6.1 exige reconciliar "Cola applying huerfana - pause"; la
   implementacion no tiene NINGUN camino para pause.
"""

import os
import socket
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg import sql as pgsql  # noqa: E402
from test_apply_cola import (  # noqa: E402
    _aplicador,
    _decision_corte,
    _encola_fila,
    _fechas,
    _handler_cortes,
    _metrica,
    _payload_pause,
    _semilla,
    claves_bloqueadas,
    libera_vencidos,
)
from test_schema import SQL, SQL2  # noqa: E402

from app import apply_harvest  # noqa: E402

dsn = os.environ["ORBIT_TEST_DSN"]


@contextmanager
def _db_temporal(prefijo):
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
    conn = psycopg.connect(dsn, dbname=db, autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute(SQL)
    conn.execute(SQL2)
    try:
        yield conn
    finally:
        conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _semilla_hemorragia(conn, ids, entidad):
    d = ids["ahora"]
    for fecha in _fechas(d.date() - timedelta(days=28), d.date() - timedelta(days=11)):
        _metrica(conn, ids["run"], entidad, fecha, clicks=5, cost=2, orders=0)


print("=== A) released sin quota: nunca se re-intenta, clave bloqueada ===")
with _db_temporal("orbit_adv4a") as conn:
    ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 1})
    d = ids["ahora"]
    _semilla_hemorragia(conn, ids, ids["kw"])
    _semilla_hemorragia(conn, ids, ids["kw2"])
    dec1 = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
    q1 = _encola_fila(
        conn,
        dec1,
        ids["kw"],
        "pause",
        encolado=d - timedelta(days=3),
        payload=_payload_pause("7201"),
    )
    ciclo2 = conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
    ).fetchone()[0]
    dec2 = _decision_corte(conn, ciclo2, ids["config"], ids["kw2"], "pause")
    q2 = _encola_fila(
        conn,
        dec2,
        ids["kw2"],
        "pause",
        encolado=d - timedelta(days=2),
        payload=_payload_pause("7202"),
    )
    handler, vistos = _handler_cortes()
    res = libera_vencidos(
        conn, "amazon_us", ahora=d, aplicador=_aplicador(conn, handler, ids["ciclo_ejec"])
    )
    print(f"dia 1 (cap 1): aplicadas={res.aplicadas} sin_quota={res.sin_quota}")
    fila_q1 = conn.execute("SELECT estado FROM apply_queue WHERE id=%s", (q1,)).fetchone()[0]
    print(f"  fila q1 (aplicada): estado={fila_q1}")

    # Dia 2: quota NUEVA (fila del dia nuevo) y de nuevo libera_vencidos +
    # reconcilia (exactamente lo que corre un ciclo live al dia siguiente).
    conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('dia2',"
        " '{\"ads_apply_cap_amazon_us_pause\": 1}')"
    )
    d2 = d + timedelta(days=1)
    handler2, vistos2 = _handler_cortes()
    res2 = libera_vencidos(
        conn, "amazon_us", ahora=d2, aplicador=_aplicador(conn, handler2, ids["ciclo_ejec"])
    )
    rec = apply_harvest.reconcilia_harvest(
        conn, _aplicador(conn, handler2, ids["ciclo_ejec"]), "amazon_us"
    )
    fila_q2_dia2 = conn.execute("SELECT estado FROM apply_queue WHERE id=%s", (q2,)).fetchone()[0]
    puts2 = [r for r in vistos2 if r.method == "PUT"]
    print(f"dia 2 (cap disponible): liberadas={res2.liberadas} aplicadas={res2.aplicadas}")
    print(f"  reconcilia_harvest: jobs_done={rec.jobs_done} negativas={rec.negativas_confirmadas}")
    print(
        f"  fila q2 (la que esperaba quota FIFO) tras el dia 2: estado={fila_q2_dia2}"
        f"  (PUTs del dia 2: {len(puts2)})"
    )
    bloqueadas = claves_bloqueadas(conn, "amazon_us", d2)
    print(
        f"  clave de efecto de q2 sigue bloqueada: {(ids['kw2'], 'entity_cut', None) in bloqueadas}"
    )

print()
print("=== B) pause applying tras 5xx: sin reconciliador ===")


def _handler_5xx_put():
    """Token OK; el PUT de mutacion muere con error de RED (ambiguo para un
    POST/PUT no idempotente -> AdsApiError SIN retry)."""
    vistos = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        vistos.append(request)
        if request.method == "PUT":
            raise httpx.ConnectError("red muerta tras enviar el PUT")
        return httpx.Response(
            200, json={"keywords": [{"keywordId": "7201", "state": "enabled", "bid": "1.00"}]}
        )

    return handler, vistos


with _db_temporal("orbit_adv4b") as conn:
    ids = _semilla(conn, caps={"ads_apply_cap_amazon_us_pause": 2})
    d = ids["ahora"]
    _semilla_hemorragia(conn, ids, ids["kw"])
    dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
    q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
    handler, vistos = _handler_5xx_put()
    aplicador = _aplicador(conn, handler, ids["ciclo_ejec"])
    try:
        libera_vencidos(conn, "amazon_us", ahora=d, aplicador=aplicador)
        print("libera_vencidos termino sin excepcion (inesperado)")
    except Exception as exc:  # AdsApiError ambiguo SUBE (diseno declarado)
        print(f"fallo ambiguo en el PUT (sube, por diseno): {type(exc).__name__}")
    estado = conn.execute("SELECT estado FROM apply_queue WHERE id=%s", (q,)).fetchone()[0]
    ledger = conn.execute(
        "SELECT count(*), count(finished_at) FROM apply_attempt WHERE decision_id=%s", (dec,)
    ).fetchone()
    print(f"  fila q: estado={estado}, ledger: filas={ledger[0]} selladas={ledger[1]}")

    # Ciclo siguiente: la UNICA reconciliacion que existe (harvest/negatives)
    handler_ok, _ = _handler_cortes()
    aplicador2 = _aplicador(conn, handler_ok, ids["ciclo_ejec"])
    rec = apply_harvest.reconcilia_harvest(conn, aplicador2, "amazon_us")
    estado2 = conn.execute("SELECT estado FROM apply_queue WHERE id=%s", (q,)).fetchone()[0]
    print(
        f"  tras reconcilia_harvest del ciclo siguiente: estado={estado2} "
        f"(jobs_done={rec.jobs_done}, negativas_confirmadas={rec.negativas_confirmadas})"
    )
    bloqueadas = claves_bloqueadas(conn, "amazon_us", d + timedelta(days=1))
    print(
        f"  clave entity_cut de la kw bloqueada para siempre: "
        f"{(ids['kw'], 'entity_cut', None) in bloqueadas}"
    )
