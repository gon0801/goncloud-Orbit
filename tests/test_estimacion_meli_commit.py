"""El carril MeLi de la ingesta commitea de verdad (falla real D.4 2026-10-16).

`resolver_oferta_meli` lee con SELECTs pelados que dejan la conexion
INTRANS; sin el bloque de lectura en `ejecutar_ingesta`, cada `with
conn.transaction()` de persistir era SAVEPOINT y todo el carril (mas el
sello del run) se revertia al cerrar. La prueba corre la ingesta con una
conexion como la de app (autocommit=False), la cierra, y verifica desde
OTRA conexion que oferta/fee/escenario quedaron visibles y el run sellado.
"""

from __future__ import annotations

import itertools
import os
import socket
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

RAIZ = Path(__file__).resolve().parents[1]

ORDEN = (
    "0001_initial.sql",
    "0028_estimacion_venta.sql",
    "0039_precio.sql",
    "0047_familias.sql",
    "0053_precio02_canal_meli.sql",
    "0054_precio02_base.sql",
    "0055_precio02_escenario_exige.sql",
    "0073_precio02_meli_miembro_costo.sql",
)

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
FETCH = NOW - timedelta(hours=1)

_CONTADOR = itertools.count()

POLITICA_MELI = {
    "universo": "meli/meli",
    "formula_version": "S3",
    "iva_divisor": "1.16",
    "isr_tasa": "0.025",
    "logistica": "0",
    "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",
    "retencion_iva_reconciliacion": "0.08",
    "precio_incluye_iva": True,
    "fee_tax_amount_requiere_politica": True,
}

RESPUESTA_FEE = {
    "currency_id": "MXN",
    "sale_fee_amount": 190.93,
    "sale_fee_details": {"percentage_fee": 19.5, "fixed_fee": 0, "gross_amount": 190.93},
    "listing_fee_amount": 0,
    "listing_fee_details": {"fixed_fee": 0, "gross_amount": 0},
}


def _dsn_base() -> str:
    # Fuente unica del DSN de prueba: la copia local quedo con la contrasena
    # redactada (`orbit:***@`) y autenticaba mal sin ORBIT_TEST_DSN.
    return _test_dsn()


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db_dsn():
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"meli_commit_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        dsn_db = _dsn_de_db(dsn, db)
        mig = psycopg.connect(dsn_db, autocommit=True)
        try:
            mig.execute("SET TIME ZONE 'UTC'")
            for nombre in ORDEN:
                mig.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        finally:
            mig.close()
        yield dsn_db
    finally:
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _semilla(conn) -> int:
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        ("SKU-COMMIT-A", "SKU-COMMIT-A"),
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'meli', %s, %s) RETURNING id",
        (pid, "MLM-COMMIT-1", "SKU-COMMIT-A"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO listing_miembro"
        " (listing_id, platform, variante_ext, seller_sku, product_id, activo, visto_at)"
        " VALUES (%s, 'meli', %s, %s, %s, true, %s)",
        (lid, "v-SKU-COMMIT-A", "SKU-COMMIT-A", pid, FETCH),
    )
    conn.execute(
        "INSERT INTO meli_publicacion_observation"
        " (item_id, metric_date, observed_at, estado, precio, moneda, stock,"
        " tipo_publicacion, categoria_id, logistica, visitas, variantes)"
        " VALUES (%s, %s, %s, 'active', '979.11', 'MXN', 3,"
        " 'gold_pro', 'MLM1440', NULL, NULL, '[]')",
        ("MLM-COMMIT-1", FETCH.date(), FETCH),
    )
    run = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, rows_written,"
        " rows_skipped, ok) VALUES ('accounting_sku_costs', %s, %s, 1, 0, TRUE)"
        " RETURNING id",
        (FETCH, FETCH),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
        " valid_from, ingest_run_id) VALUES (%s, '400.0000', 'MXN', FALSE, '2026-01-01', %s)",
        (pid, run),
    )
    conn.execute(
        "INSERT INTO precio_envio_muestra (product_id, platform, canal, ventana_desde,"
        " ventana_hasta, envios, percentil, valor, valor_currency, mediana,"
        " mediana_currency, p90, p90_currency, maximo, maximo_currency, origen,"
        " familia_id, donantes, regla_version)"
        " VALUES (%s, 'meli', 'meli', '2026-03-12', '2026-09-07', 4, 'mediana',"
        " '30.0000', 'MXN', '30.0000', 'MXN', '30.0000', 'MXN', '30.0000', 'MXN',"
        " 'propio', NULL, NULL, 'f2.1')",
        (pid,),
    )
    conn.execute(
        "INSERT INTO estimacion_politica_version"
        " (created_at, label, universo, formula_version, settings, valid_from)"
        " VALUES (%s, 'commit-test', 'meli/meli', 'S3', %s, '2026-01-01')",
        (FETCH, Json(POLITICA_MELI)),
    )
    return lid


def _puente_vacio(tmp_path: Path) -> Path:
    ruta = tmp_path / "bridge.db"
    con = sqlite3.connect(ruta)
    con.execute(
        "CREATE TABLE amazon_listing_prices (seller_sku TEXT, asin TEXT,"
        " marketplace_id TEXT, marketplace_name TEXT, price TEXT,"
        " fulfillment_channel TEXT, fetched_at TEXT)"
    )
    con.commit()
    con.close()
    return ruta


def _cliente_meli():
    from app.reputacion_clientes import ClienteMeli, MeliCredentials

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESPUESTA_FEE)

    return ClienteMeli(
        MeliCredentials(access_token="tok"),
        transport=httpx.MockTransport(handler),
        backoff_base=0.0,
    )


@_skip_sin_pg
def test_carril_meli_persiste_y_sella_run(tmp_path):
    """D.4 2026-10-16: lo del carril MeLi queda visible desde otra conexion
    y el ingest_run queda sellado; sin el fix se revertia todo al cerrar."""
    from app.estimacion_ingest import ejecutar_ingesta

    with _db_dsn() as dsn:
        admin = psycopg.connect(dsn, autocommit=True)
        lid = _semilla(admin)
        puente = _puente_vacio(tmp_path)

        app_conn = psycopg.connect(dsn)  # como app/db.connect: autocommit=False
        try:
            res = ejecutar_ingesta(
                app_conn,
                ruta_sqlite=puente,
                now_utc=NOW,
                meli_client=_cliente_meli(),
            )
        finally:
            app_conn.close()  # como al salir del proceso: lo no commiteado se pierde

        assert res.ok is True
        oferta = admin.execute(
            "SELECT id FROM estimacion_oferta_observation WHERE listing_id = %s", (lid,)
        ).fetchone()
        assert oferta is not None, "la oferta MeLi debe sobrevivir al cierre"
        fee = admin.execute(
            "SELECT id FROM estimacion_fee_observation WHERE oferta_observation_id = %s",
            (oferta[0],),
        ).fetchone()
        assert fee is not None, "el fee MeLi debe sobrevivir al cierre"
        esc = admin.execute(
            "SELECT id FROM estimacion_escenario WHERE oferta_observation_id = %s", (oferta[0],)
        ).fetchone()
        assert esc is not None, "el escenario MeLi debe sobrevivir al cierre"
        sello = admin.execute(
            "SELECT ok, rows_written FROM ingest_run WHERE source = 'bridge_estimacion_fees'"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert sello is not None and sello[0] is True, "el run debe quedar sellado"
        assert (sello[1] or 0) > 0
        admin.close()


@_skip_sin_pg
def test_carril_meli_cuenta_decision(tmp_path):
    """El resultado reporta la decision MeLi (no skip silencioso)."""
    from app.estimacion_ingest import ejecutar_ingesta

    with _db_dsn() as dsn:
        admin = psycopg.connect(dsn, autocommit=True)
        _semilla(admin)
        puente = _puente_vacio(tmp_path)

        app_conn = psycopg.connect(dsn)
        try:
            res = ejecutar_ingesta(
                app_conn,
                ruta_sqlite=puente,
                now_utc=NOW,
                meli_client=_cliente_meli(),
            )
        finally:
            app_conn.close()
        assert res.ofertas_nuevas + res.ofertas_reutilizadas >= 1
        assert res.escenarios_nuevos + res.escenarios_reutilizados >= 1
        admin.close()
