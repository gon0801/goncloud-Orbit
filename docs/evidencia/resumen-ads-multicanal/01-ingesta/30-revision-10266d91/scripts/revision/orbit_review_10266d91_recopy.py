import datetime as dt

import orbit_review_d1bdbe5 as r
from app.meli import ads_ingest as ai
from test_meli_ads_ingest import D1, _snapshot

name, conn = r.fresh("final_10266_real_recopy")
try:
    ahora = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def snap(nombre, costo, as_of):
        filas = [(D1.isoformat(), "C1", "ALFA", 1, 2, costo, 8.0)]
        return _snapshot(r.TMP / nombre, filas, as_of=as_of.isoformat())

    ai.ingest_meli_ads(conn, snap("recopy-base.db", 5.0, ahora - dt.timedelta(hours=3)))
    ai.ingest_meli_ads(conn, snap("recopy-future.db", 6.0, ahora + dt.timedelta(hours=1)))
    copia = ai.ingest_meli_ads(
        conn, snap("recopy-real.db", 6.0, ahora - dt.timedelta(hours=2))
    )
    marca = conn.execute(
        "SELECT skip_reason FROM ingest_run WHERE id=%s", (copia["run_id"],)
    ).fetchone()[0]
    try:
        ai.ingest_meli_ads(
            conn,
            snap(
                "recopy-middle.db",
                7.0,
                ahora - dt.timedelta(hours=2, minutes=30),
            ),
        )
        error = None
    except Exception as exc:
        error = str(exc)
    vigente = str(r.latest(conn)[0])
    print("RECOPIA_REAL", copia)
    print("MARCA_RECOPIA", marca)
    print("INTERMEDIO", {"error": error, "latest": vigente, "expected": "6.0000"})
    reproducido = error is None and vigente == "7.0000"
    print("BLOQUEANTE_REPRODUCIDO", reproducido)
    raise SystemExit(0 if reproducido else 1)
finally:
    r.drop(name, conn)
