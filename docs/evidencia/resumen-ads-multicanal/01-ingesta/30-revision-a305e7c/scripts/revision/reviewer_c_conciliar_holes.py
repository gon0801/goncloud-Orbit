# ruff: noqa
import datetime as dt
import importlib.util
import sqlite3
import sys
import tempfile
import types
from decimal import Decimal
from pathlib import Path

SCRIPT = Path(
    "/Users/dn/dev/goncloud-Orbit/docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-10266d91/scripts/conciliar.py"
)
spec = importlib.util.spec_from_file_location("conciliar_holes", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
DIA = "2026-10-28"
ORBIT = [("C1", dt.date.fromisoformat(DIA), Decimal("1.0000"), Decimal("2.0000"), 3, 4, "MXN")]


class Result:
    def fetchall(self):
        return ORBIT


class Conn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query):
        return Result()


mod.psycopg = types.SimpleNamespace(connect=lambda dsn: Conn())


def run(label, campaigns, days, currency=None):
    path = Path(tempfile.mkdtemp(prefix="reviewer-c-holes-")) / "source.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE meli_ads_campaigns_daily (campaign_id TEXT, date TEXT, spend REAL, ad_sales REAL, impressions INTEGER, clicks INTEGER"
        + (", currency TEXT" if currency else "")
        + ")"
    )
    con.execute(
        "CREATE TABLE meli_ads_metrics_daily (date TEXT, clicks INTEGER, impressions INTEGER, spend REAL, ad_sales REAL)"
    )
    for row in campaigns:
        con.execute(
            "INSERT INTO meli_ads_campaigns_daily VALUES (?,?,?,?,?,?"
            + (",?" if currency else "")
            + ")",
            (*row, currency) if currency else row,
        )
    con.executemany("INSERT INTO meli_ads_metrics_daily VALUES (?,?,?,?,?)", days)
    con.commit()
    con.close()
    rc = mod.main([str(path), "disposable"])
    print(f"CASE={label} EXIT={rc}")


base_c = ("C1", DIA, 1.0, 2.0, 3, 4)
base_d = (DIA, 4, 3, 1.0, 2.0)
run("USD_EN_ORIGEN", [base_c], [base_d], "USD")
run("PRECISION_ORIGEN_DESCARTADA", [("C1", DIA, 1.00001, 2.0, 3, 4)], [base_d])
run("CAMPANA_DUPLICADA_OCULTA", [("C1", DIA, 9.0, 2.0, 3, 4), base_c], [base_d])
run("CONTROL_DUPLICADO_OCULTO", [base_c], [(DIA, 4, 3, 9.0, 2.0), base_d])
