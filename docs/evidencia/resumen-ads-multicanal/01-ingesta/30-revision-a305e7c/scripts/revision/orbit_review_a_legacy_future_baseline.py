# ruff: noqa
import datetime as dt

import orbit_review_d1bdbe5 as r
from app.meli import ads_ingest as ai
from app.meli.ads_source import version_canonica
from test_meli_ads_ingest import D1, _RelojFijo, _snapshot
from decimal import Decimal

name, conn = r.fresh("review_a_legacy_future_baseline")
try:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    legacy_asof = now - dt.timedelta(hours=1)
    stale_asof = now - dt.timedelta(hours=2)
    future_asof = now + dt.timedelta(hours=1)
    run_id = conn.execute(
        "INSERT INTO ingest_run(source,ok,finished_at) VALUES('legacy',true,now()) RETURNING id"
    ).fetchone()[0]
    entity_id = conn.execute(
        "INSERT INTO ad_entity(platform,kind,external_id,name) VALUES('meli','campaign','C1','LEGACY') RETURNING id"
    ).fetchone()[0]
    legacy_version = version_canonica("C1", D1, "MXN", Decimal("10"), Decimal("8"), 2, 1, None)
    conn.execute(
        "INSERT INTO ads_metric_observation(ad_entity_id,metric_date,observed_at,metric_currency,cost,ad_revenue,impressions,clicks,orders,source_report_id,ingest_run_id) "
        "VALUES(%s,%s,%s,'MXN',10,8,2,1,NULL,%s,%s)",
        (
            entity_id,
            D1,
            now,
            f"meli-ads|C1|{D1.isoformat()}|{legacy_version}|{int(legacy_asof.timestamp())}",
            run_id,
        ),
    )
    future = _snapshot(
        r.TMP / "legacy-future.db",
        [(D1.isoformat(), "C1", "FUTURE", 1, 2, 20.0, 8.0)],
        as_of=future_asof.isoformat(),
    )
    stale = _snapshot(
        r.TMP / "legacy-stale.db",
        [(D1.isoformat(), "C1", "STALE", 1, 2, 5.0, 8.0)],
        as_of=stale_asof.isoformat(),
    )
    ai.ingest_meli_ads(_RelojFijo(conn, now), future)
    try:
        result = ai.ingest_meli_ads(_RelojFijo(conn, now), stale)
        error = None
    except Exception as exc:
        result = None
        error = str(exc)
    latest = r.latest(conn)
    marks = conn.execute(
        "SELECT ok, skip_reason FROM ingest_run WHERE source=%s ORDER BY id", (ai.SOURCE,)
    ).fetchall()
    print(
        "LEGACY_FUTURE_BASELINE",
        {
            "legacy_asof": int(legacy_asof.timestamp()),
            "stale_asof": int(stale_asof.timestamp()),
            "stale_result": result,
            "error": error,
            "latest": tuple(map(str, latest)),
            "marks": marks,
        },
    )
    raise SystemExit(0 if error is None and str(latest[0]) == "5.0000" else 1)
finally:
    r.drop(name, conn)
