# ruff: noqa
import os
import socket
import sqlite3
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql as pgsql

from app.meli.ads_ingest import ingest_meli_ads
from app.meli.ads_source import MeliAdsError, _dinero, leer_origen
from test_meli_ads_ingest import D1, _snapshot
from test_schema import SQL, _test_dsn


def updated_at_invalido():
    with tempfile.TemporaryDirectory() as tmp:
        snap = _snapshot(
            Path(tmp) / "mixto.db",
            [
                (D1.isoformat(), "C1", "A", 1, 2, 3.0, 4.0),
                (D1.isoformat(), "C2", "B", 1, 2, 3.0, 4.0),
            ],
            as_of="2026-10-29 08:00:00",
        )
        con = sqlite3.connect(snap)
        con.execute(
            "UPDATE meli_ads_campaigns_daily SET updated_at = ? WHERE campaign_id = ?",
            ("not-a-date", "C1"),
        )
        con.commit()
        con.close()
        origen = leer_origen(snap)
        print("UPDATED_AT_INVALIDO", len(origen.filas), dict(origen.rechazos), origen.as_of)
        assert origen.rechazos, "updated_at ilegible aceptado"


def dinero_redondeado():
    for valor in ("1.23456", "9999999999.99996"):
        try:
            _dinero(valor)
        except MeliAdsError:
            continue
        raise AssertionError(f"dinero invalido aceptado: {valor}")
    print("DINERO", "rechazado")


def base_temporal(nombre, cuerpo):
    dsn = _test_dsn()
    db = f"orbit_review_{nombre}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute(SQL)
        cuerpo(conn)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def duplicados_conflicting():
    def cuerpo(conn):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "sin_pk.db"
            con = sqlite3.connect(snap)
            con.execute(
                "CREATE TABLE meli_ads_campaigns_daily "
                "(date TEXT, campaign_id TEXT, campaign_name TEXT, clicks INTEGER, "
                "impressions INTEGER, spend REAL, ad_sales REAL, updated_at TEXT)"
            )
            con.executemany(
                "INSERT INTO meli_ads_campaigns_daily VALUES (?,?,?,?,?,?,?,?)",
                [
                    (D1.isoformat(), "C1", "A", 1, 2, 1.0, 4.0, "2026-10-29 08:00:00"),
                    (D1.isoformat(), "C1", "A", 1, 2, 2.0, 4.0, "2026-10-29 08:00:00"),
                ],
            )
            con.commit()
            con.close()
            try:
                ingest_meli_ads(conn, snap)
            except MeliAdsError:
                costos = conn.execute("SELECT cost FROM ads_metric_observation").fetchall()
                print("DUPLICADOS", "rechazado", costos)
                assert costos == [], "escritura parcial de un snapshot duplicado"
                return
            raise AssertionError("claves canonicas duplicadas aceptadas")

    base_temporal("duplicados", cuerpo)


def corrida_ausente():
    def cuerpo(conn):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "sin_tabla.db"
            sqlite3.connect(snap).close()
            try:
                ingest_meli_ads(conn, snap)
            except MeliAdsError:
                pass
            corridas = conn.execute("SELECT count(*) FROM ingest_run").fetchone()[0]
            print("CORRIDA_AUSENTE", corridas)
            assert corridas == 1, "fallo de adaptador sin ingest_run"

    base_temporal("corrida", cuerpo)


if __name__ == "__main__":
    fallos = []
    for caso in (updated_at_invalido, dinero_redondeado, duplicados_conflicting, corrida_ausente):
        try:
            caso()
        except AssertionError as exc:
            fallos.append((caso.__name__, str(exc)))
    print("FALLOS", fallos)
    raise SystemExit(bool(fallos))
