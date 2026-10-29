# ruff: noqa
import datetime as dt
import sqlite3

import orbit_review_d1bdbe5 as r
from app.meli import ads_ingest as ai
from test_meli_ads_ingest import D1, _RelojFijo, _snapshot

name, conn = r.fresh("review_a_recopy_retry_matrix")
try:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def snap(filename, cost, asof, name="ALFA"):
        return _snapshot(
            r.TMP / filename,
            [(D1.isoformat(), "C1", name, 1, 2, cost, 8.0)],
            as_of=asof.isoformat(),
        )

    original = snap("matrix-original.db", 5.0, now - dt.timedelta(hours=3))
    recopy = snap("matrix-recopy.db", 5.0, now - dt.timedelta(hours=2))
    correction = snap("matrix-correction.db", 7.0, now - dt.timedelta(hours=1))
    ai.ingest_meli_ads(_RelojFijo(conn, now), original)
    ai.ingest_meli_ads(_RelojFijo(conn, now), recopy)
    ai.ingest_meli_ads(_RelojFijo(conn, now), correction)
    before = conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0]
    retry = ai.ingest_meli_ads(_RelojFijo(conn, now), recopy)
    retry_again = ai.ingest_meli_ads(_RelojFijo(conn, now), recopy)
    after = conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0]
    same_asof_name = snap("matrix-same-asof-name.db", 7.0, now - dt.timedelta(hours=1), name="OTRO")
    same_name = ai.ingest_meli_ads(_RelojFijo(conn, now), same_asof_name)
    final_name = r.latest(conn)[1]
    print(
        "RECOPY_RETRY_MATRIX",
        {
            "retry": retry,
            "retry_again": retry_again,
            "observations_before_after": (before, after),
            "same_asof_name": same_name,
            "latest": tuple(map(str, r.latest(conn))),
            "final_name": final_name,
        },
    )
    ok = (
        before == after == 2
        and retry["rows_written"] == retry_again["rows_written"] == 0
        and str(r.latest(conn)[0]) == "7.0000"
        and final_name == "ALFA"
    )
    raise SystemExit(0 if ok else 1)
finally:
    r.drop(name, conn)
