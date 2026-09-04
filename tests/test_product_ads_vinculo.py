"""Vinculo product_ad -> listing_id (ORBIT 06 0.4).

El grano es el product ad, no el ad group: listing_id vive SOLO en filas
kind='product_ad'. ENABLED y PAUSED se materializan; ARCHIVED no entra.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path as _Path

import pytest
from test_schema import SQL, SQL4, _hay_postgres_local, _test_dsn

from app.ads.structure import EstructuraAds, EstructuraPerfil, PerfilAds, sync_structure

# BIDS 01 2.1: sync_structure escribe first_seen_at; sin la 0017 el upsert
# real revienta con UndefinedColumn.
_SQL17 = (_Path(__file__).resolve().parents[1] / "migrations" / "0017_first_seen_at.sql").read_text(
    encoding="utf-8"
)

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))


def _perfil_us() -> PerfilAds:
    return PerfilAds(
        profile_id=101,
        country="US",
        currency_code="USD",
        account_type="seller",
        valid_payment_method=True,
        account_name="Cuenta Test",
        aceptado=True,
        platform="amazon_us",
        moneda="USD",
    )


def _estructura(product_ads: list[dict]) -> EstructuraAds:
    perfil = _perfil_us()
    return EstructuraAds(
        perfiles=[perfil],
        estructuras=[
            EstructuraPerfil(
                perfil=perfil,
                campanas=[
                    {
                        "campaignId": "9001",
                        "name": "Camp US",
                        "targetingType": "MANUAL",
                        "state": "ENABLED",
                    }
                ],
                ad_groups=[
                    {
                        "adGroupId": "9101",
                        "name": "AG Uno",
                        "campaignId": "9001",
                        "state": "ENABLED",
                        "defaultBid": 0.75,
                    }
                ],
                keywords=[],
                targets=[],
                product_ads=product_ads,
            )
        ],
    )


def _conectar_base_con_0004():
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"orbit_pad_test_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
    conn = psycopg.connect(dsn, dbname=db, autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute(SQL)
    conn.execute(SQL4)
    conn.execute(_SQL17)  # BIDS 01 2.1: first_seen_at
    return psycopg, pgsql, admin, conn, db


def _cerrar(psycopg, pgsql, admin, conn, db) -> None:
    if conn is not None:
        conn.close()
    admin.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db)))
    admin.close()


def _sembrar_listing(conn, *, asin: str, con_costo: bool) -> int:
    product_id = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (f"SKU-{asin}", f"Producto {asin}"),
    ).fetchone()[0]
    listing_id = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_us', %s, %s) RETURNING id",
        (product_id, asin, f"AMZ-{asin}"),
    ).fetchone()[0]
    if con_costo:
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency,"
            " includes_tax, valid_from) VALUES (%s, 10.0000, 'USD', false, '2026-01-01')",
            (product_id,),
        )
    return listing_id


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_product_ad_resuelve_listing_id_y_no_toca_ad_group():
    psycopg, pgsql, admin, conn, db = _conectar_base_con_0004()
    try:
        listing_id = _sembrar_listing(conn, asin="B0LISTED01", con_costo=True)
        res = sync_structure(
            conn,
            _estructura(
                [
                    {
                        "adId": "9401",
                        "adGroupId": "9101",
                        "campaignId": "9001",
                        "asin": "B0LISTED01",
                        "state": "ENABLED",
                    },
                    {
                        "adId": "9402",
                        "adGroupId": "9101",
                        "asin": "B0LISTED01",
                        "state": "PAUSED",
                    },
                ]
            ),
        )
        assert res.ok is True
        assert res.counts[("amazon_us", "product_ad")] == 2
        assert "product ad con listing" in (res.skip_reason or "")

        filas = conn.execute(
            "SELECT external_id, listing_id FROM ad_entity"
            " WHERE kind = 'product_ad' ORDER BY external_id"
        ).fetchall()
        assert filas == [("9401", listing_id), ("9402", listing_id)]

        ag_listing = conn.execute(
            "SELECT listing_id FROM ad_entity WHERE kind = 'ad_group' AND external_id = '9101'"
        ).fetchone()[0]
        assert ag_listing is None
    finally:
        _cerrar(psycopg, pgsql, admin, conn, db)


@pytest.mark.skipif(
    not _DSN_EXPLICITO and not _hay_postgres_local(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_product_ad_sin_listing_se_escribe_con_listing_id_null():
    psycopg, pgsql, admin, conn, db = _conectar_base_con_0004()
    try:
        res = sync_structure(
            conn,
            _estructura(
                [
                    {
                        "adId": "9409",
                        "adGroupId": "9101",
                        "asin": "B0MISSING99",
                        "state": "ENABLED",
                    }
                ]
            ),
        )
        assert res.ok is True
        assert res.counts[("amazon_us", "product_ad")] == 1
        assert "product ad sin listing" in (res.skip_reason or "")

        fila = conn.execute(
            "SELECT listing_id FROM ad_entity WHERE kind = 'product_ad' AND external_id = '9409'"
        ).fetchone()
        assert fila is not None
        assert fila[0] is None
    finally:
        _cerrar(psycopg, pgsql, admin, conn, db)
