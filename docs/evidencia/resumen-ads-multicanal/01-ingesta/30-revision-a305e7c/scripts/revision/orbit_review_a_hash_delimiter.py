# ruff: noqa
import datetime as dt
import sqlite3

import orbit_review_d1bdbe5 as r
from app.meli import ads_ingest as ai
from test_meli_ads_ingest import D1, _RelojFijo, _snapshot

name, conn = r.fresh("review_a_hash_delimiter")
try:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def snap(filename, campaign, revision):
        path = _snapshot(
            r.TMP / filename,
            [(D1.isoformat(), campaign, "ALFA", 1, 2, 5.0, 8.0)],
            as_of=now.isoformat(),
        )
        con = sqlite3.connect(path)
        con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN revision TEXT")
        con.execute("UPDATE meli_ads_campaigns_daily SET revision=?", (revision,))
        con.commit()
        con.close()
        return path

    first_path = snap("delimiter-first.db", "A", "X\x1frev:Y")
    second_path = snap("delimiter-second.db", "A\x1frev:X", "Y")
    first = ai.ingest_meli_ads(_RelojFijo(conn, now), first_path)
    try:
        second = ai.ingest_meli_ads(_RelojFijo(conn, now), second_path)
        error = None
    except Exception as exc:
        second = None
        error = str(exc)
    entities = conn.execute("SELECT external_id FROM ad_entity ORDER BY external_id").fetchall()
    print(
        "HASH_DELIMITER",
        {"first": first, "different_snapshot": second, "error": error, "entities": entities},
    )
    raise SystemExit(0 if error is None and len(entities) == 1 else 1)
finally:
    r.drop(name, conn)
