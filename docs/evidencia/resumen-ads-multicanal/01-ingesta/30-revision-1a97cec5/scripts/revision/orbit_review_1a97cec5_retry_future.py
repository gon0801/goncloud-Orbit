import datetime as dt

import orbit_review_d1bdbe5 as r
from test_meli_ads_ingest import D1, _RelojFijo, _snapshot

from app.meli import ads_ingest as ai

name, conn = r.fresh("final_1a97_retry_future")
try:
    ahora = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def snap(nombre, costo, as_of):
        filas = [(D1.isoformat(), "C1", "ALFA", 1, 2, costo, 8.0)]
        return _snapshot(r.TMP / nombre, filas, as_of=as_of.isoformat())

    futuro = snap("retry-future.db", 5.0, ahora + dt.timedelta(hours=1))
    real = snap("retry-real.db", 6.0, ahora)
    primero = ai.ingest_meli_ads(_RelojFijo(conn, ahora), futuro)
    ai.ingest_meli_ads(_RelojFijo(conn, ahora), real)
    try:
        reintento = ai.ingest_meli_ads(_RelojFijo(conn, ahora), futuro)
        error = None
    except Exception as exc:
        reintento = None
        error = str(exc)
    print(
        "REINTENTO_FUTURO_EXACTO",
        {
            "primero": primero,
            "reintento": reintento,
            "error": error,
            "latest": str(r.latest(conn)[0]),
            "observaciones": conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[
                0
            ],
        },
    )
    raise SystemExit(1 if error is not None else 0)
finally:
    r.drop(name, conn)
