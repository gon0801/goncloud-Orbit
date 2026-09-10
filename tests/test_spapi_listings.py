"""Tests SP-API 01 A.4 — ingesta Listings Items 2021-08-01.

(a) UNITARIOS: parseo del shape del acta 0.3 (summary completo, sin
lastUpdatedDate, sin summary del marketplace pedido, contrato fatal).
Cero red real, cero DB.
(b) INTEGRACION: migracion 0035 (clave unica, append-only, grants) e
ingesta punta a punta idempotente en Postgres de test (skipea sin
ORBIT_TEST_DSN). D2: listing intacto tras la ingesta.
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.spapi import listings
from app.spapi.client import SpapiClient

ROOT = Path(__file__).resolve().parents[1]
ORDEN = (
    "0001_initial.sql",
    "0033_ingest_run_llamadas.sql",
    "0034_ingest_run_llamadas_grant.sql",
    "0035_spapi_listings_inventario.sql",
)

AHORA = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
CRED = {
    "lwa_app_id": "id-listings",
    "lwa_client_secret": "secreto-listings",
    "refresh_token": "refresh-listings",
}
_TOKEN_LWA = "tk-spapi-listings-fixture-lwa"
MID_MX = "A1AM78C64UM0Y8"
PROPIO = "A29XRL07YRN0L"

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


def _summary(mid=MID_MX, **mas):
    # Shape del acta 0.3 (claves_item pineadas en la sonda real).
    base = {
        "asin": "B0TEST0001",
        "conditionType": "New",
        "createdDate": "2026-09-01T10:00:00Z",
        "itemName": "Producto de prueba",
        "lastUpdatedDate": "2026-09-08T11:00:00Z",
        "mainImage": {"link": "https://ejemplo.test/i.jpg", "height": 75, "width": 75},
        "marketplaceId": mid,
        "productType": "PRODUCT",
        "status": "OPEN",
    }
    base.update(mas)
    return base


def _cuerpo_listings(summaries, sku="SKU-A4-1"):
    return {"sku": sku, "summaries": summaries}


def test_parsea_summary_completo():
    estado = listings.parsear_estado(
        "SKU-A4-1", _cuerpo_listings([_summary()]), marketplace_id=MID_MX
    )
    assert estado.seller_sku == "SKU-A4-1"
    assert estado.asin == "B0TEST0001"
    assert estado.status == "OPEN"
    assert estado.product_type == "PRODUCT"
    assert estado.last_updated_date == datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC)


def test_sin_last_updated_date_es_nulo_no_falla():
    cuerpo = _cuerpo_listings([_summary()])
    del cuerpo["summaries"][0]["lastUpdatedDate"]
    estado = listings.parsear_estado("SKU-A4-1", cuerpo, marketplace_id=MID_MX)
    assert estado.status == "OPEN"
    assert estado.last_updated_date is None


def test_sin_summary_del_marketplace_se_omite():
    with pytest.raises(listings.EstadoOmitido, match="sin_summary"):
        listings.parsear_estado(
            "SKU-A4-1",
            _cuerpo_listings([_summary(mid="ATVPDKIKX0DER")]),
            marketplace_id=MID_MX,
        )


def test_sin_summaries_se_omite():
    with pytest.raises(listings.EstadoOmitido, match="sin_summary"):
        listings.parsear_estado("SKU-A4-1", {"sku": "SKU-A4-1"}, marketplace_id=MID_MX)


def test_elige_el_summary_del_marketplace_pedido():
    estado = listings.parsear_estado(
        "SKU-A4-1",
        _cuerpo_listings([_summary(mid="ATVPDKIKX0DER", status="CLOSED"), _summary(status="OPEN")]),
        marketplace_id=MID_MX,
    )
    assert estado.status == "OPEN"


def test_contrato_inesperado_es_fatal():
    for cuerpo in ([1, 2], {"summaries": {"no": "lista"}}, "hilo"):
        with pytest.raises(listings.IngestaListingsError, match="contrato inesperado"):
            listings.parsear_estado("SKU-A4-1", cuerpo, marketplace_id=MID_MX)


# ---------------------------------------------------------------------------
# (b) INTEGRACION
# ---------------------------------------------------------------------------


@contextmanager
def db_listings(prefijo: str = "orbit_spapi_a4l"):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=False)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        conn.commit()
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sembrar_universo(conn, filas):
    for i, (platform, sku, asin) in enumerate(filas):
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
            (f"SKU-A4-{platform}-{i}", f"Prod {sku}"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku,"
            " listing_price, price_currency)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (pid, platform, asin, sku, "100.0000", "MXN"),
        )


@_skip_db
def test_migracion_clave_append_only_y_grants():
    import psycopg

    with db_listings() as conn:
        conn.execute(
            "INSERT INTO spapi_listing_estado_observation"
            " (seller_sku, asin, platform, status, product_type,"
            " last_updated_date, api_version, observed_at, ingest_run_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                "SKU-A4-1",
                "B0TEST0001",
                "amazon_mx",
                "OPEN",
                "PRODUCT",
                datetime(2026, 9, 8, 11, 0, 0, tzinfo=UTC),
                "2021-08-01",
                AHORA,
                None,
            ),
        )
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO spapi_listing_estado_observation"
                " (seller_sku, platform, api_version, observed_at)"
                " VALUES (%s, %s, %s, %s)",
                ("SKU-A4-1", "amazon_mx", "2021-08-01", AHORA),
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE spapi_listing_estado_observation SET status = 'x'")
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM spapi_listing_estado_observation")
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE spapi_listing_estado_observation")
        conn.rollback()
        assert conn.execute(
            "SELECT has_table_privilege('app_read', 'spapi_listing_estado_observation', 'SELECT')"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_listing_estado_observation', 'INSERT')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_decide', 'spapi_listing_estado_observation', 'INSERT')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_listing_estado_observation', 'UPDATE')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_listing_estado_observation', 'DELETE')"
        ).fetchone()[0]


def _handler_pase(respuestas, llamadas):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        llamadas.append(request)
        assert request.url.params.get("marketplaceIds") == MID_MX
        sku = request.url.path.rsplit("/", 1)[-1]
        cuerpo = respuestas[sku]
        if isinstance(cuerpo, tuple) and cuerpo[0] == "status":
            return httpx.Response(cuerpo[1], json={})
        return httpx.Response(200, json=cuerpo)

    return handler


@_skip_db
def test_pase_punta_a_punta_idempotente():
    llamadas: list = []
    respuestas = {
        "SKU-A4-1": _cuerpo_listings([_summary()], sku="SKU-A4-1"),
        # Publicacion retirada: skip contado, la corrida sigue.
        "SKU-A4-2": ("status", 404),
        # Sin summary del marketplace: fila no escrita, skip contado.
        "SKU-A4-3": _cuerpo_listings([_summary(mid="ATVPDKIKX0DER")], sku="SKU-A4-3"),
    }
    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(_handler_pase(respuestas, llamadas)),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    with db_listings() as conn:
        _sembrar_universo(
            conn,
            [
                ("amazon_mx", "SKU-A4-1", "B0TEST0001"),
                ("amazon_mx", "SKU-A4-2", "B0TEST0002"),
                ("amazon_mx", "SKU-A4-3", "B0TEST0003"),
            ],
        )
        resultado = listings.ejecutar_ingesta(conn, cliente, platform="amazon_mx", ahora=AHORA)
        assert resultado.ok
        assert resultado.escritas == 1
        assert resultado.asins_vistos == 3
        assert resultado.llamadas == 3
        run = conn.execute(
            "SELECT ok, rows_written, rows_skipped, skip_reason, llamadas"
            " FROM ingest_run WHERE id = %s",
            (resultado.run_id,),
        ).fetchone()
        assert run[0] is True
        assert run[1] == 1
        assert run[2] == 2
        assert run[3] == "1x http_404, 1x sin_summary"
        assert run[4] == 3
        fila = conn.execute(
            "SELECT seller_sku, asin, status, product_type, api_version"
            " FROM spapi_listing_estado_observation WHERE seller_sku = 'SKU-A4-1'"
        ).fetchone()
        assert fila == ("SKU-A4-1", "B0TEST0001", "OPEN", "PRODUCT", "2021-08-01")

        # Re-pase con el mismo observed_at: idempotente.
        segunda = listings.ejecutar_ingesta(conn, cliente, platform="amazon_mx", ahora=AHORA)
        assert segunda.escritas == 0
        total = conn.execute("SELECT count(*) FROM spapi_listing_estado_observation").fetchone()[0]
        assert total == 1
        run2 = conn.execute(
            "SELECT rows_skipped, skip_reason FROM ingest_run WHERE id = %s",
            (segunda.run_id,),
        ).fetchone()
        assert "duplicada" in (run2[1] or "")


@_skip_db
def test_muro_aborta_con_lo_escrito():
    llamadas: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        llamadas.append(request)
        return httpx.Response(500, json={})

    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    with db_listings() as conn:
        _sembrar_universo(conn, [("amazon_mx", "SKU-A4-1", "B0TEST0001")])
        with pytest.raises(listings.IngestaListingsError, match="umbral"):
            listings.ejecutar_ingesta(conn, cliente, platform="amazon_mx", ahora=AHORA)
        run = conn.execute(
            "SELECT ok, rows_written FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run[0] is False


@_skip_db
def test_listing_intacto_tras_ambas_ingestas():
    # D2: ni listings ni inventario escriben precio/stock en listing.
    from app.spapi import inventario

    llamadas: list = []
    respuestas = {"SKU-A4-1": _cuerpo_listings([_summary()])}
    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(_handler_pase(respuestas, llamadas)),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    inv_llamadas: list = []

    def inv_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        inv_llamadas.append(request)
        return httpx.Response(
            200,
            json={
                "payload": {
                    "inventorySummaries": [
                        {"sellerSku": "SKU-A4-1", "asin": "B0TEST0001", "totalQuantity": 7}
                    ]
                }
            },
        )

    inv_cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(inv_handler),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    with db_listings() as conn:
        _sembrar_universo(conn, [("amazon_mx", "SKU-A4-1", "B0TEST0001")])
        antes = conn.execute(
            "SELECT id, product_id, platform, external_id, seller_sku,"
            " listing_price, price_currency FROM listing ORDER BY id"
        ).fetchall()
        assert len(antes) == 1
        listings.ejecutar_ingesta(conn, cliente, platform="amazon_mx", ahora=AHORA)
        inventario.ejecutar_ingesta(conn, inv_cliente, platform="amazon_mx", ahora=AHORA)
        despues = conn.execute(
            "SELECT id, product_id, platform, external_id, seller_sku,"
            " listing_price, price_currency FROM listing ORDER BY id"
        ).fetchall()
        assert antes == despues


def test_cli_sin_dsn_falla_cerrado(monkeypatch):
    from app import cli

    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert cli.main(["ingest", "spapi_listings", "--platform", "amazon_mx"]) == 2


def test_cli_registra_pipeline():
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "ingest", "--help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert "spapi_listings" in proc.stdout
