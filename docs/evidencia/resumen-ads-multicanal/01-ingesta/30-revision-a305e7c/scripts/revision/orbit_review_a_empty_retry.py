# ruff: noqa
import orbit_review_d1bdbe5 as r
from app.meli import ads_ingest as ai
from test_meli_ads_ingest import _snapshot

name, conn = r.fresh("review_a_empty_retry")
try:
    empty = _snapshot(r.TMP / "empty.db", [])
    try:
        first = ai.ingest_meli_ads(conn, empty)
        error = None
    except Exception as exc:
        first = None
        error = str(exc)
    runs = conn.execute(
        "SELECT ok, rows_written, rows_skipped, skip_reason FROM ingest_run ORDER BY id"
    ).fetchall()
    print("EMPTY_FIRST", {"first": first, "error": error, "runs": runs})
    raise SystemExit(0 if error is not None else 1)
finally:
    r.drop(name, conn)
