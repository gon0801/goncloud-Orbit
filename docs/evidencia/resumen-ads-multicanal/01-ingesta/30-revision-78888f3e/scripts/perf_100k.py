# ruff: noqa
"""Perf gate: 3 ingestas frescas de 100,000 filas canonicas y su tamano."""

from __future__ import annotations

import datetime as dt
import os
import socket
import sqlite3
import tempfile
import time
from pathlib import Path

import psycopg
from psycopg import sql

from app.meli.ads_ingest import ingest_meli_ads
from test_schema import SQL, _test_dsn

FILAS = 100_000
CAMPANAS = 100
DIAS = FILAS // CAMPANAS
HOY = dt.datetime.now(dt.UTC).date()
AS_OF = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
TMP = Path(tempfile.mkdtemp(prefix="orbit-perf-"))
SNAP = TMP / "snapshot-100k.db"


def crea_snapshot() -> None:
    con = sqlite3.connect(SNAP)
    con.execute(
        "CREATE TABLE meli_ads_campaigns_daily ("
        " date TEXT NOT NULL, campaign_id TEXT NOT NULL,"
        " campaign_name TEXT DEFAULT '', clicks INTEGER DEFAULT 0,"
        " impressions INTEGER DEFAULT 0, spend REAL DEFAULT 0,"
        " ad_sales REAL DEFAULT 0, acos REAL DEFAULT 0,"
        " updated_at TEXT DEFAULT (datetime('now')),"
        " PRIMARY KEY (date, campaign_id))"
    )
    filas = []
    for c in range(CAMPANAS):
        for d in range(DIAS):
            fecha = (HOY - dt.timedelta(days=d + 1)).isoformat()
            filas.append(
                (
                    fecha,
                    f"C{c:03d}",
                    f"CAMPANA {c:03d}",
                    d % 40,
                    (d % 40) * 100,
                    round((c + 1) * 0.5 + (d % 7) * 0.01, 2),
                    round((c + 1) * 2.0 + (d % 11) * 0.02, 2),
                )
            )
    con.executemany(
        "INSERT INTO meli_ads_campaigns_daily"
        " (date, campaign_id, campaign_name, clicks, impressions, spend, ad_sales, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(*fila, AS_OF) for fila in filas],
    )
    con.commit()
    con.close()


def main() -> None:
    crea_snapshot()
    print(f"snapshot={SNAP} bytes={SNAP.stat().st_size} filas={FILAS} as_of={AS_OF}")
    dsn = _test_dsn()
    for corrida in range(1, 4):
        db = f"orbit_perf_{corrida}_{socket.gethostname().lower()}_{os.getpid()}"
        admin = psycopg.connect(dsn, autocommit=True)
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))
        conn = None
        try:
            conn = psycopg.connect(dsn, dbname=db, autocommit=True)
            conn.execute("SET TIME ZONE 'UTC'")
            conn.execute(SQL)
            inicio = time.perf_counter()
            resultado = ingest_meli_ads(conn, SNAP)
            segundos = time.perf_counter() - inicio
            print(
                f"corrida={corrida} segundos={segundos:.3f}"
                f" rows_written={resultado['rows_written']}"
                f" entidades={resultado['entidades_nuevas']}"
            )
            assert resultado["rows_written"] == FILAS
            assert segundos < 120, f"ingesta de 100k excede 120 s: {segundos:.1f}"
        finally:
            if conn is not None:
                conn.close()
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db))
            )
            admin.close()


if __name__ == "__main__":
    main()
