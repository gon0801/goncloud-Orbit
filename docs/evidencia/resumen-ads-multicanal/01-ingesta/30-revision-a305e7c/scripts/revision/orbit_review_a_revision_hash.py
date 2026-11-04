# ruff: noqa
import datetime as dt
import sqlite3

import orbit_review_d1bdbe5 as r
from app.meli import ads_ingest as ai
from test_meli_ads_ingest import D1, _RelojFijo, _snapshot

name, conn = r.fresh("review_a_revision_hash")
try:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def snap(filename, cost):
        path = _snapshot(
            r.TMP / filename,
            [(D1.isoformat(), "C1", "ALFA", 1, 2, cost, 8.0)],
            as_of=now.isoformat(),
        )
        con = sqlite3.connect(path)
        con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN revision TEXT")
        con.execute("UPDATE meli_ads_campaigns_daily SET revision='r1'")
        con.commit()
        con.close()
        return path

    first = ai.ingest_meli_ads(_RelojFijo(conn, now), snap("revision-first.db", 5.0))
    try:
        second = ai.ingest_meli_ads(_RelojFijo(conn, now), snap("revision-changed.db", 9.0))
        error = None
    except Exception as exc:
        second = None
        error = str(exc)
    print(
        "REVISION_HASH",
        {
            "first": first,
            "changed_same_asof": second,
            "error": error,
            "latest": str(r.latest(conn)[0]),
        },
    )
    raise SystemExit(0 if error is None and str(r.latest(conn)[0]) == "5.0000" else 1)
finally:
    r.drop(name, conn)
