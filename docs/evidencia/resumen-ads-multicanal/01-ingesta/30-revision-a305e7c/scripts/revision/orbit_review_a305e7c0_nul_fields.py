# ruff: noqa
import sqlite3

import orbit_review_d1bdbe5 as r
from app.meli import ads_ingest as ai
from test_meli_ads_ingest import AS_OF_INICIAL, D1, _snapshot

casos = (
    ("campaign_name", "C1", "ALFA\x00NUL", None),
    ("revision", "C1", "ALFA", "r\x00NUL"),
    ("campaign_id", "C\x00NUL", "ALFA", None),
)
for etiqueta, campaign, nombre, revision in casos:
    name, conn = r.fresh(f"review_b_nul_{etiqueta}")
    try:
        path = _snapshot(
            r.TMP / f"nul-{etiqueta}.db",
            [(D1.isoformat(), campaign, nombre, 1, 2, 5.0, 8.0)],
            as_of=AS_OF_INICIAL,
        )
        if revision is not None:
            con = sqlite3.connect(path)
            con.execute("ALTER TABLE meli_ads_campaigns_daily ADD COLUMN revision TEXT")
            con.execute("UPDATE meli_ads_campaigns_daily SET revision=?", (revision,))
            con.commit()
            con.close()
        try:
            ai.ingest_meli_ads(conn, path)
            error = None
        except Exception as exc:
            error = repr(exc)
        run = conn.execute(
            "SELECT ok, finished_at, rows_skipped, skip_reason FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()
        print("NUL_CAMPO", etiqueta, {"error": error, "run": run})
    finally:
        r.drop(name, conn)
