# ruff: noqa
import datetime as dt

import orbit_review_d1bdbe5 as r
from app.meli import ads_ingest as ai
from test_meli_ads_ingest import D1, _RelojFijo, _snapshot


def fila(costo):
    return [(D1.isoformat(), "C1", "ALFA", 1, 2, costo, 8.0)]


def futuro_dentro_de_tolerancia():
    name, conn = r.fresh("final_future_tolerance")
    try:
        ahora = dt.datetime.now(dt.UTC).replace(microsecond=0)
        futuro = _snapshot(
            r.TMP / "final-future.db",
            fila(5.0),
            as_of=(ahora + dt.timedelta(hours=1)).isoformat(),
        )
        actual = _snapshot(
            r.TMP / "final-current.db", fila(6.0), as_of=ahora.isoformat()
        )
        ai.ingest_meli_ads(conn, futuro)
        try:
            ai.ingest_meli_ads(conn, actual)
            error = None
        except Exception as exc:
            error = str(exc)
        vigente = str(r.latest(conn)[0])
        print("FUTURO_1H", {"correccion_actual_error": error, "latest": vigente})
        return error is not None and vigente == "5.0000"
    finally:
        r.drop(name, conn)


def reloj_retrocede():
    name, conn = r.fresh("final_clock_backwards")
    try:
        as_of = dt.datetime.now(dt.UTC).replace(microsecond=0) - dt.timedelta(hours=2)
        reloj = dt.datetime.now(dt.UTC).replace(microsecond=0)
        uno = _snapshot(r.TMP / "final-clock-one.db", fila(5.0), as_of=as_of.isoformat())
        dos = _snapshot(
            r.TMP / "final-clock-two.db",
            fila(6.5),
            as_of=(as_of + dt.timedelta(hours=1)).isoformat(),
        )
        ai.ingest_meli_ads(_RelojFijo(conn, reloj), uno)
        ai.ingest_meli_ads(_RelojFijo(conn, reloj - dt.timedelta(seconds=1)), dos)
        vigente = str(r.latest(conn)[0])
        print("RELOJ_RETROCEDE", {"latest": vigente, "expected": "6.5000"})
        return vigente == "5.0000"
    finally:
        r.drop(name, conn)


fallos = [futuro_dentro_de_tolerancia(), reloj_retrocede()]
print("BLOQUEANTES_REPRODUCIDOS", fallos)
raise SystemExit(0 if all(fallos) else 1)
