import datetime as dt

import orbit_review_d1bdbe5 as r
from app.meli import ads_ingest as ai
from test_meli_ads_ingest import D1, _RelojFijo, _snapshot

name, conn = r.fresh("final_10266_future_boundary")
try:
    inicio = dt.datetime.now(dt.UTC).replace(microsecond=0)
    asof_futuro = inicio + dt.timedelta(hours=1)

    def snap(nombre, costo, as_of):
        filas = [(D1.isoformat(), "C1", "ALFA", 1, 2, costo, 8.0)]
        return _snapshot(r.TMP / nombre, filas, as_of=as_of.isoformat())

    ai.ingest_meli_ads(
        _RelojFijo(conn, inicio), snap("future-boundary-one.db", 5.0, asof_futuro)
    )
    ai.ingest_meli_ads(_RelojFijo(conn, inicio), snap("future-boundary-two.db", 6.0, inicio))
    despues = asof_futuro - dt.timedelta(minutes=4)
    try:
        ai.ingest_meli_ads(
            _RelojFijo(conn, despues), snap("future-boundary-three.db", 7.0, despues)
        )
        error = None
    except Exception as exc:
        error = str(exc)
    vigente = str(r.latest(conn)[0])
    print("FRONTERA_FUTURO", {"error": error, "latest": vigente, "expected": "7.0000"})
    reproducido = error is not None and vigente == "6.0000"
    print("BLOQUEANTE_REPRODUCIDO", reproducido)
    raise SystemExit(0 if reproducido else 1)
finally:
    r.drop(name, conn)
