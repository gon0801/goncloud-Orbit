"""GET y descarga de un reporte existente: dos campanas candidatas, sin crear reportes."""

import datetime as dt
import gzip
import json
import os
from decimal import Decimal

from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.db import connect
from app.redaction import register_secret, scrub

report_id = "7d1234d4-881c-4505-a499-7c9620f79f5c"
profile_id = 4387591165211712
ids = {"110131350133029": 3911, "114399001706085": 3920}
out = {"utc": dt.datetime.now(dt.UTC), "report_id": report_id, "profile_id": profile_id}
try:
    client = AdsClient(AdsCredentials.from_secrets_dir())
    response = client.get_report(report_id, profile_id=profile_id)
    out["http_status"] = response.status_code
    response.raise_for_status()
    data = response.json()
    out["metadata"] = {
        k: data.get(k)
        for k in ("status", "startDate", "endDate", "createdAt", "updatedAt", "fileSize")
    }
    url = data.get("url")
    if not url:
        out["blocker"] = "reporte_existente_sin_url_descargable"
    else:
        register_secret(url)
        download = client.download(url)
        download.raise_for_status()
        content = download.content
        if content[:2] == b"\x1f\x8b":
            content = gzip.decompress(content)
        rows = json.loads(content, parse_float=Decimal, parse_int=Decimal)
        selected = [x for x in rows if str(x.get("campaignId")) in ids]
        out["download_rows"] = len(rows)
        out["selected_rows"] = selected
        dsn = os.environ["ORBIT_DSN_READ"]
        register_secret(dsn)
        sql = (
            "SELECT ad_entity_id,metric_date,cost,ad_revenue,metric_currency::"
            "text FROM ads_metric_observation WHERE ad_entity_id=ANY(%s) AND s"
            "ource_report_id=%s ORDER BY ad_entity_id,metric_date"
        )
        with connect(dsn) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout='15s'")
            out["sql"] = sql
            out["db_rows"] = list(conn.execute(sql, (list(ids.values()), report_id)))
    print(json.dumps(out, default=str))
except Exception as exc:
    out["error_type"] = type(exc).__name__
    out["error"] = scrub(str(exc))
    print(json.dumps(out, default=str))
