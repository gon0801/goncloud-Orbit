import datetime as dt

import orbit_review_d1bdbe5 as r
from test_meli_ads_ingest import D1, _RelojFijo, _snapshot

from app.meli import ads_ingest as ai

name, conn = r.fresh("final_1a97_future_reclassified")
try:
    inicio = dt.datetime.now(dt.UTC).replace(microsecond=0)
    asof_futuro = inicio + dt.timedelta(minutes=10)

    def snap(nombre, costo, as_of):
        filas = [(D1.isoformat(), "C1", "ALFA", 1, 2, costo, 8.0)]
        return _snapshot(r.TMP / nombre, filas, as_of=as_of.isoformat())

    futuro = snap("quarantine-future.db", 5.0, asof_futuro)
    correccion = snap("quarantine-correction.db", 6.0, inicio + dt.timedelta(minutes=6))
    ai.ingest_meli_ads(_RelojFijo(conn, inicio), futuro)
    reintento = ai.ingest_meli_ads(_RelojFijo(conn, inicio + dt.timedelta(minutes=11)), futuro)
    marca = conn.execute(
        "SELECT skip_reason FROM ingest_run WHERE id=%s", (reintento["run_id"],)
    ).fetchone()[0]
    try:
        ai.ingest_meli_ads(_RelojFijo(conn, inicio + dt.timedelta(minutes=11)), correccion)
        error = None
    except Exception as exc:
        error = str(exc)
    print(
        "FUTURO_RECLASIFICADO",
        {
            "marca_reintento": marca,
            "correccion_real_error": error,
            "latest": str(r.latest(conn)[0]),
            "latest_esperado": "6.0000",
        },
    )
    raise SystemExit(1 if error is not None else 0)
finally:
    r.drop(name, conn)
