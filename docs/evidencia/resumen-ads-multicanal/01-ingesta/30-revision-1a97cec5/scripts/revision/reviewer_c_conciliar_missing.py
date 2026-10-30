# ruff: noqa
import runpy
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

snapshot = Path(tempfile.mkdtemp(prefix="reviewer-c-conciliar-")) / "source.db"
con = sqlite3.connect(snapshot)
con.execute(
    "CREATE TABLE meli_ads_campaigns_daily (campaign_id TEXT, date TEXT, spend REAL, ad_sales REAL, impressions INTEGER, clicks INTEGER)"
)
con.execute(
    "CREATE TABLE meli_ads_metrics_daily (date TEXT, clicks INTEGER, impressions INTEGER, spend REAL, ad_sales REAL)"
)
con.execute("INSERT INTO meli_ads_campaigns_daily VALUES ('C1', '2026-10-28', 1.0, 2.0, 3, 4)")
con.execute("INSERT INTO meli_ads_metrics_daily VALUES ('2026-10-28', 4, 3, 1.0, 2.0)")
con.commit()
con.close()


class EmptyResult:
    def fetchall(self):
        return []


class EmptyConnection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query):
        return EmptyResult()


sys.modules["psycopg"] = types.SimpleNamespace(connect=lambda dsn: EmptyConnection())
sys.argv = ["conciliar.py", str(snapshot), "disposable"]
runpy.run_path(
    "/Users/dn/dev/goncloud-Orbit/docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-10266d91/scripts/conciliar.py",
    run_name="__main__",
)
