import orbit_review_d1bdbe5 as r
from test_meli_ads_ingest import _FILAS, AS_OF_INICIAL, _snapshot

from app.meli import ads_ingest as ai

name, conn = r.fresh("final_1a97_nul")
try:
    snap = _snapshot(
        r.TMP / "nul-currency.db",
        _FILAS,
        con_moneda=True,
        moneda="MXN\x00USD",
        as_of=AS_OF_INICIAL,
    )
    try:
        ai.ingest_meli_ads(conn, snap)
        error = None
    except Exception as exc:
        error = repr(exc)
    run = conn.execute(
        "SELECT ok, finished_at, rows_skipped, skip_reason FROM ingest_run ORDER BY id"
    ).fetchall()
    datos = (
        conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0],
        conn.execute("SELECT count(*) FROM ad_entity WHERE platform='meli'").fetchone()[0],
    )
    print("NUL_MONEDA", {"error": error, "runs": run, "datos": datos})
    raise SystemExit(1 if any(row[0] is None for row in run) else 0)
finally:
    r.drop(name, conn)
