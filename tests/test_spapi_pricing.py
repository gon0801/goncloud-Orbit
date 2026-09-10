"""Tests SP-API 01 A.3 — pase Pricing v0 con Buy Box.

(a) UNITARIOS: parseo de ofertas + competitivo (E/0.2), 0 ofertas, precio
sin moneda, is_own, contrato fatal, limitador 0.5/s. Cero red real, cero DB.
(b) INTEGRACION: migracion 0032 e ingesta punta a punta idempotente en
Postgres de test (skipea sin ORBIT_TEST_DSN).
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.spapi import pricing
from app.spapi.client import SpapiClient

ROOT = Path(__file__).resolve().parents[1]
ORDEN = (
    "0001_initial.sql",
    "0032_spapi_pricing.sql",
    "0033_ingest_run_llamadas.sql",
    "0034_ingest_run_llamadas_grant.sql",
    "0036_ingest_run_platform.sql",
)  # A.5: el sello escribe platform

AHORA = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
CRED = {
    "lwa_app_id": "id-pricing",
    "lwa_client_secret": "secreto-pricing",
    "refresh_token": "refresh-pricing",
}
_TOKEN_LWA = "tk-spapi-pricing-fixture-lwa"
PROPIO = "A29XRL07YRN0L"

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


def _oferta(seller, precio, moneda="MXN", *, ganadora=False, fba=False):
    return {
        "SellerId": seller,
        "IsBuyBoxWinner": ganadora,
        "IsFulfilledByAmazon": fba,
        "ListingPrice": {"Amount": precio, "CurrencyCode": moneda},
    }


def _cuerpo_ofertas(ofertas):
    return {"payload": {"ASIN": "B0TEST0001", "status": "Success", "Offers": ofertas}}


def _cuerpo_competitivo(entradas=()):
    # Forma real (modelo oficial productPricingV0.json): payload LISTA de
    # Price con Product.CompetitivePricing.CompetitivePrices.
    return {
        "payload": [
            {
                "status": "Success",
                "ASIN": "B0TEST0001",
                "Product": {
                    "CompetitivePricing": {
                        "CompetitivePrices": list(entradas),
                        "NumberOfOfferListings": [],
                    }
                },
            }
        ]
    }


def _propia_competitiva(precio=95.0, moneda="MXN"):
    return {
        "CompetitivePriceId": "1",
        "Price": {
            "ListingPrice": {"Amount": precio, "CurrencyCode": moneda},
            "LandedPrice": {"Amount": precio, "CurrencyCode": moneda},
        },
        "condition": "New",
        # belongsToRequester NO esta en el modelo oficial: se lee tolerante
        # en el codigo; pendiente de pinar en sonda.
        "belongsToRequester": True,
    }


def test_parsea_completo_con_ganadora_propia():
    precio = pricing.parsear_precios(
        "B0TEST0001",
        _cuerpo_ofertas(
            [
                _oferta(PROPIO, 100.0, ganadora=True, fba=True),
                _oferta("OTRO1", 99.0, fba=True),
                _oferta("OTRO2", 120.0),
            ]
        ),
        _cuerpo_competitivo([_propia_competitiva()]),
        vendedor_propio=PROPIO,
    )
    assert precio.own_price == Decimal("100.0000")
    assert precio.own_currency == "MXN"
    assert precio.buy_box_price == Decimal("100.0000")
    assert precio.buy_box_seller_id == PROPIO
    assert precio.buy_box_is_own is True
    assert precio.offers_count == 3
    assert precio.fba_offers_count == 2
    assert precio.lowest_price == Decimal("99.0000")
    assert precio.lowest_currency == "MXN"


def test_ganadora_ajena_y_propia_por_competitivo():
    precio = pricing.parsear_precios(
        "B0TEST0001",
        _cuerpo_ofertas([_oferta("OTRO1", 99.0, ganadora=True)]),
        _cuerpo_competitivo([_propia_competitiva(95.0)]),
        vendedor_propio=PROPIO,
    )
    assert precio.buy_box_price == Decimal("99.0000")
    assert precio.buy_box_seller_id == "OTRO1"
    assert precio.buy_box_is_own is False
    assert precio.own_price == Decimal("95.0000")
    assert precio.lowest_price == Decimal("99.0000")


def test_sin_vendedor_propio_is_own_nulo():
    precio = pricing.parsear_precios(
        "B0TEST0001",
        _cuerpo_ofertas([_oferta("OTRO1", 99.0, ganadora=True)]),
        _cuerpo_competitivo(),
        vendedor_propio=None,
    )
    assert precio.buy_box_seller_id == "OTRO1"
    assert precio.buy_box_is_own is None
    assert precio.own_price is None


def test_cero_ofertas_fila_nula():
    precio = pricing.parsear_precios(
        "B0TEST0001",
        _cuerpo_ofertas([]),
        _cuerpo_competitivo([_propia_competitiva()]),
        vendedor_propio=PROPIO,
    )
    assert precio.offers_count == 0
    assert precio.fba_offers_count == 0
    assert precio.own_price is None
    assert precio.buy_box_price is None
    assert precio.lowest_price is None
    assert precio.buy_box_is_own is None


def test_precio_sin_moneda_se_omite():
    with pytest.raises(pricing.PrecioOmitido, match="precio_sin_moneda"):
        pricing.parsear_precios(
            "B0TEST0001",
            _cuerpo_ofertas(
                [
                    {"SellerId": "X", "ListingPrice": {"Amount": 5.0}},
                    {"SellerId": "Y", "ListingPrice": {"Amount": 5.0, "CurrencyCode": "EUR"}},
                ]
            ),
            _cuerpo_competitivo(),
            vendedor_propio=PROPIO,
        )


def test_contrato_inesperado_es_fatal():
    for ofertas, competitivo in (
        # Sin status Success y sin Offers: envoltorio irreconocible (F4).
        ({"payload": {}}, _cuerpo_competitivo()),
        ({"payload": {"Offers": {"no": "lista"}}}, _cuerpo_competitivo()),
        ([1, 2], _cuerpo_competitivo()),
        (_cuerpo_ofertas([_oferta("X", 1.0)]), [1, 2]),
        # Competitivo con payload objeto (forma vieja inventada): fatal.
        (
            _cuerpo_ofertas([_oferta("X", 1.0)]),
            {"payload": {"Product": {"CompetitivePrices": []}}},
        ),
    ):
        with pytest.raises(pricing.IngestaPricingError, match="contrato inesperado"):
            pricing.parsear_precios("B0TEST0001", ofertas, competitivo, vendedor_propio=PROPIO)


def _resumen(total, fba, minimo, moneda="MXN"):
    return {
        "TotalOfferCount": total,
        "NumberOfOffers": [
            {"condition": "New", "fulfillmentChannel": "Amazon", "OfferCount": fba},
            {"condition": "New", "fulfillmentChannel": "Merchant", "OfferCount": 6},
        ],
        "LowestPrices": [
            {
                "condition": "New",
                "fulfillmentChannel": "Amazon",
                "ListingPrice": {"Amount": minimo, "CurrencyCode": moneda},
            }
        ],
    }


def _cuerpo_ofertas_con_resumen(ofertas, resumen):
    cuerpo = _cuerpo_ofertas(ofertas)
    cuerpo["payload"]["Summary"] = resumen
    return cuerpo


def test_conteo_fba_resumen_solo_condicion_new():
    # CodeRabbit PR #241: la ingesta pide ItemCondition=New y el minimo ya
    # filtra condition New; el conteo FBA debe hacer lo mismo: una entrada
    # Used con canal Amazon no suma.
    resumen = _resumen(10, 4, 90.0)
    resumen["NumberOfOffers"].append(
        {"condition": "Used", "fulfillmentChannel": "Amazon", "OfferCount": 7}
    )
    precio = pricing.parsear_precios(
        "B0TEST0001",
        _cuerpo_ofertas_con_resumen(
            [
                _oferta(PROPIO, 100.0, ganadora=True, fba=True),
                _oferta("OTRO1", 99.0, fba=True),
            ],
            resumen,
        ),
        _cuerpo_competitivo(),
        vendedor_propio=PROPIO,
    )
    assert precio.fba_offers_count == 4
    assert precio.offers_count == 10


def test_sin_clave_offers_con_success_es_cero_filas():
    # F4: el modelo no marca Offers como requerida; sin la clave y con
    # status Success = cero ofertas, no error.
    precio = pricing.parsear_precios(
        "B0TEST0001",
        {"payload": {"ASIN": "B0TEST0001", "status": "Success"}},
        _cuerpo_competitivo([_propia_competitiva()]),
        vendedor_propio=PROPIO,
    )
    assert precio.offers_count == 0
    assert precio.fba_offers_count == 0
    assert precio.lowest_price is None


def test_conteos_y_minimo_salen_de_summary():
    # F3: la pagina trae 2 ofertas pero el Summary declara 10 totales,
    # 4 FBA y minimo 90: Fase B debe leer totales, no pagina.
    precio = pricing.parsear_precios(
        "B0TEST0001",
        _cuerpo_ofertas_con_resumen(
            [
                _oferta(PROPIO, 100.0, ganadora=True, fba=True),
                _oferta("OTRO1", 99.0, fba=True),
            ],
            _resumen(10, 4, 90.0),
        ),
        _cuerpo_competitivo([_propia_competitiva()]),
        vendedor_propio=PROPIO,
    )
    assert precio.offers_count == 10
    assert precio.fba_offers_count == 4
    assert precio.lowest_price == Decimal("90.0000")
    assert precio.lowest_currency == "MXN"


def test_sin_summary_la_pagina_es_respaldo():
    # F3: sin Summary los conteos vuelven a la pagina (respaldo declarado).
    precio = pricing.parsear_precios(
        "B0TEST0001",
        _cuerpo_ofertas(
            [
                _oferta(PROPIO, 100.0, ganadora=True, fba=True),
                _oferta("OTRO1", 99.0),
            ]
        ),
        _cuerpo_competitivo(),
        vendedor_propio=PROPIO,
    )
    assert precio.offers_count == 2
    assert precio.fba_offers_count == 1
    assert precio.lowest_price == Decimal("99.0000")


def test_vendedores_propios_cubren_mx_y_us():
    assert pricing.VENDEDORES_PROPIOS["A1AM78C64UM0Y8"] == PROPIO
    assert pricing.VENDEDORES_PROPIOS["ATVPDKIKX0DER"] == PROPIO


def test_limitador_una_llamada_cada_2s():
    dormidas: list = []
    reloj = {"v": 5000.0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        return httpx.Response(200, json=_cuerpo_ofertas([_oferta("X", 10.0)]))

    from app.spapi.client import CuboTasa

    cubo = CuboTasa(sleep=dormidas.append, clock=lambda: reloj["v"], capacidad=1, tasa=0.5)
    cubo.consumir()
    assert dormidas == []
    cubo.consumir()
    assert dormidas == [pytest.approx(2.0)]


# ---------------------------------------------------------------------------
# (b) INTEGRACION
# ---------------------------------------------------------------------------


@contextmanager
def db_pricing(prefijo: str = "orbit_spapi_a3"):
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
    for i, (platform, asin) in enumerate(filas):
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
            (f"SKU-A3-{platform}-{i}", f"Prod {asin}"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, %s, %s, %s)",
            (pid, platform, asin, f"SS-{asin}"),
        )


@_skip_db
def test_migracion_clave_append_only_y_grants():
    import psycopg

    with db_pricing() as conn:
        base = (
            "B0TEST0001",
            "amazon_mx",
            datetime(2026, 9, 9).date(),
            datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
            Decimal("100.0000"),
            "MXN",
            Decimal("100.0000"),
            "MXN",
            PROPIO,
            True,
            3,
            2,
            Decimal("99.0000"),
            "MXN",
            None,
        )
        conn.execute(
            "INSERT INTO spapi_price_observation"
            " (asin, platform, metric_date, observed_at, own_listing_price,"
            " own_listing_currency, buy_box_price, buy_box_currency,"
            " buy_box_seller_id, buy_box_is_own, offers_count, fba_offers_count,"
            " lowest_price, lowest_currency, ingest_run_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            base,
        )
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO spapi_price_observation"
                " (asin, platform, metric_date, observed_at)"
                " VALUES (%s, %s, %s, %s)",
                (
                    "B0TEST0001",
                    "amazon_mx",
                    datetime(2026, 9, 9).date(),
                    datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
                ),
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO spapi_price_observation"
                " (asin, platform, metric_date, observed_at, own_listing_price)"
                " VALUES (%s, %s, %s, %s, %s)",
                (
                    "B0TEST0002",
                    "amazon_mx",
                    datetime(2026, 9, 9).date(),
                    datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
                    Decimal("1.0000"),
                ),
            )
        conn.rollback()
        # Revision #7: negativas de cada par precio/moneda (ambas
        # direcciones del CHECK parejo) y conteos.
        for columna, moneda in (
            ("buy_box_price", "buy_box_currency"),
            ("lowest_price", "lowest_currency"),
        ):
            base = (
                "B0TEST0003",
                "amazon_mx",
                datetime(2026, 9, 9).date(),
                datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO spapi_price_observation"
                    f" (asin, platform, metric_date, observed_at, {columna})"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (*base, Decimal("1.0000")),
                )
            conn.rollback()
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO spapi_price_observation"
                    f" (asin, platform, metric_date, observed_at, {moneda})"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (*base, "MXN"),
                )
            conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO spapi_price_observation"
                " (asin, platform, metric_date, observed_at, offers_count,"
                " fba_offers_count)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    "B0TEST0004",
                    "amazon_mx",
                    datetime(2026, 9, 9).date(),
                    datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
                    -1,
                    -2,
                ),
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE spapi_price_observation SET offers_count = 9")
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM spapi_price_observation")
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE spapi_price_observation")
        conn.rollback()
        assert conn.execute(
            "SELECT has_table_privilege('app_read', 'spapi_price_observation', 'SELECT')"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_price_observation', 'INSERT')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_decide', 'spapi_price_observation', 'INSERT')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_price_observation', 'UPDATE')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_price_observation', 'DELETE')"
        ).fetchone()[0]


@_skip_db
def test_grant_update_llamadas_app_ingest():
    """0034 (bug de produccion A.6): 0001 otorga el UPDATE de ingest_run a
    app_ingest POR COLUMNA y 0033 agrego `llamadas` sin su GRANT — el sello
    del pase (UPDATE ... llamadas) reventaba con permission denied (corridas
    reales 145/147 abiertas). El sello necesita UPDATE en las seis columnas.
    """
    with db_pricing() as conn:
        for columna in (
            "finished_at",
            "rows_written",
            "rows_skipped",
            "skip_reason",
            "ok",
            "llamadas",
        ):
            assert conn.execute(
                "SELECT has_column_privilege('app_ingest', 'ingest_run', %s, 'UPDATE')",
                (columna,),
            ).fetchone()[0], columna
        assert not conn.execute(
            "SELECT has_column_privilege('app_read', 'ingest_run', 'llamadas', 'UPDATE')"
        ).fetchone()[0]


@_skip_db
def test_trigger_metric_date_es_el_dia_utc_de_observed_at():
    # Invariante de tiempo en trigger con UTC fijado EN LA EXPRESION
    # (patron 0028/0030, regla del repo), nunca en CHECK dependiente de la
    # TZ de sesion: metric_date es el dia UTC de observed_at.
    import psycopg

    with db_pricing() as conn:
        # Dia distinto al de la captura UTC: la fila se rechaza.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO spapi_price_observation"
                " (asin, platform, metric_date, observed_at)"
                " VALUES (%s, %s, %s, %s)",
                (
                    "B0TEST0001",
                    "amazon_mx",
                    datetime(2026, 9, 8).date(),
                    datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC),
                ),
            )
        conn.rollback()
        # UTC fijado en la expresion: con la sesion en otra TZ la fecha
        # correcta EN UTC (10) entra aunque en la TZ local sea dia 9;
        # un CHECK con ::date dependeria de la sesion y la rechazaria.
        conn.execute("SET TIME ZONE 'America/Mexico_City'")
        conn.execute(
            "INSERT INTO spapi_price_observation"
            " (asin, platform, metric_date, observed_at)"
            " VALUES (%s, %s, %s, %s)",
            (
                "B0TEST0002",
                "amazon_mx",
                datetime(2026, 9, 10).date(),
                datetime(2026, 9, 10, 1, 0, 0, tzinfo=UTC),
            ),
        )
        conn.execute("SET TIME ZONE 'UTC'")
        conn.commit()
        assert (
            conn.execute(
                "SELECT metric_date FROM spapi_price_observation WHERE asin = 'B0TEST0002'"
            ).fetchone()[0]
            == datetime(2026, 9, 10).date()
        )


def _handler_pase(respuestas, llamadas):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        llamadas.append(request)
        path = request.url.path
        if "/offers" in path:
            asin = next((a for a in respuestas if a in path), None)
            assert asin is not None
            cuerpo = respuestas[asin]["ofertas"]
        else:
            # competitivePrice lleva el ASIN en el query param Asins.
            asin = request.url.params.get("Asins")
            assert asin in respuestas
            cuerpo = respuestas[asin]["competitivo"]
        return httpx.Response(200, json=cuerpo)

    return handler


@_skip_db
def test_pase_punta_a_punta_idempotente():
    llamadas: list = []
    asins = ["B0TEST0001", "B0TEST0002", "B0TEST0003"]
    respuestas = {
        "B0TEST0001": {
            "ofertas": _cuerpo_ofertas(
                [
                    _oferta(PROPIO, 100.0, ganadora=True, fba=True),
                    _oferta("OTRO1", 99.0, fba=True),
                ]
            ),
            "competitivo": _cuerpo_competitivo([_propia_competitiva(100.0)]),
        },
        # Estilo US de E/0.2: 0 ofertas, competitivo con dato (ausencia: NULLs).
        "B0TEST0002": {
            "ofertas": _cuerpo_ofertas([]),
            "competitivo": _cuerpo_competitivo([_propia_competitiva(50.0)]),
        },
        # Precios inutilizables: fila no escrita.
        "B0TEST0003": {
            "ofertas": _cuerpo_ofertas([{"SellerId": "X", "ListingPrice": {"Amount": 1.0}}]),
            "competitivo": _cuerpo_competitivo(),
        },
    }
    reloj = {"v": 5000.0}
    dormidas: list = []
    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(_handler_pase(respuestas, llamadas)),
        sleep=dormidas.append,
        clock=lambda: reloj["v"],
    )
    with db_pricing() as conn:
        _sembrar_universo(conn, [("amazon_mx", a) for a in asins])
        resultado = pricing.ejecutar_ingesta(conn, cliente, platform="amazon_mx", ahora=AHORA)
        assert resultado.ok
        assert resultado.escritas == 2
        assert resultado.asins_vistos == 3
        assert resultado.llamadas == 6
        assert resultado.segundos >= 0
        # 2 llamadas por ASIN con el cubo 0.5/s: 5 esperas de 2 s.
        assert dormidas == [pytest.approx(2.0)] * 5
        run = conn.execute(
            "SELECT ok, rows_written, rows_skipped, skip_reason, llamadas"
            " FROM ingest_run WHERE id = %s",
            (resultado.run_id,),
        ).fetchone()
        assert run[0] is True
        assert run[1] == 2
        assert run[2] == 1
        assert run[3] == "1x precio_sin_moneda"
        # Revision #7: el conteo de llamadas queda en ingest_run (DoD A.3).
        assert run[4] == 6
        fila = conn.execute(
            "SELECT own_listing_price, buy_box_price, buy_box_seller_id,"
            " buy_box_is_own, offers_count, fba_offers_count,"
            " lowest_price, metric_date"
            " FROM spapi_price_observation WHERE asin = 'B0TEST0001'"
        ).fetchone()
        assert fila == (
            Decimal("100.0000"),
            Decimal("100.0000"),
            PROPIO,
            True,
            2,
            2,
            Decimal("99.0000"),
            datetime(2026, 9, 9).date(),
        )
        vacia = conn.execute(
            "SELECT offers_count, own_listing_price, buy_box_price"
            " FROM spapi_price_observation WHERE asin = 'B0TEST0002'"
        ).fetchone()
        assert vacia == (0, None, None)

        # Re-pase con el mismo observed_at: idempotente.
        llamadas.clear()
        segunda = pricing.ejecutar_ingesta(conn, cliente, platform="amazon_mx", ahora=AHORA)
        assert segunda.escritas == 0
        assert conn.execute("SELECT count(*) FROM spapi_price_observation").fetchone()[0] == 2
        run2 = conn.execute(
            "SELECT rows_skipped, skip_reason FROM ingest_run WHERE id = %s",
            (segunda.run_id,),
        ).fetchone()
        assert "duplicada" in (run2[1] or "")


def _handler_estados(estados, llamadas):
    """estados[asin] = {"ofertas": cuerpo | ("status", codigo), "competitivo": ...}."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        llamadas.append(request)
        if "/offers" in request.url.path:
            asin = next((a for a in estados if a in request.url.path), None)
            cuerpo = estados[asin]["ofertas"]
        else:
            asin = request.url.params.get("Asins")
            cuerpo = estados[asin]["competitivo"]
        if isinstance(cuerpo, tuple) and cuerpo[0] == "status":
            return httpx.Response(cuerpo[1], json={})
        if isinstance(cuerpo, tuple) and cuerpo[0] == "red":
            raise cuerpo[1]("corte de red simulado")
        return httpx.Response(200, json=cuerpo)

    return handler


def _cliente_estados(estados, llamadas):
    return SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(_handler_estados(estados, llamadas)),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )


def _ok(asin, precio=100.0):
    return {
        "ofertas": _cuerpo_ofertas([_oferta(PROPIO, precio, ganadora=True, fba=True)]),
        "competitivo": _cuerpo_competitivo([_propia_competitiva(precio)]),
    }


@_skip_db
def test_fallo_en_asin_3_conserva_filas_y_sella_lo_escrito():
    # F1: transaccion por ASIN; el fallo del tercero no revierte los dos
    # primeros y el sello ok=false trae su conteo.
    llamadas: list = []
    asins = ["B0TEST0001", "B0TEST0002", "B0TEST0003", "B0TEST0004"]
    estados = {
        "B0TEST0001": _ok("B0TEST0001"),
        "B0TEST0002": _ok("B0TEST0002", 50.0),
        "B0TEST0003": {
            "ofertas": ("status", 500),
            "competitivo": _cuerpo_competitivo(),
        },
        "B0TEST0004": _ok("B0TEST0004"),
    }
    with db_pricing() as conn:
        _sembrar_universo(conn, [("amazon_mx", a) for a in asins])
        with pytest.raises(pricing.IngestaPricingError, match="umbral"):
            pricing.ejecutar_ingesta(
                conn, _cliente_estados(estados, llamadas), platform="amazon_mx", ahora=AHORA
            )
        assert conn.execute("SELECT count(*) FROM spapi_price_observation").fetchone()[0] == 2
        run = conn.execute(
            "SELECT ok, rows_written, llamadas FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run[0] is False
        assert run[1] == 2
        # 2+2 intentos de los dos primeros + 1 del tercero que fallo.
        assert run[2] == 5


@_skip_db
@pytest.mark.parametrize("codigo", [403, 404])
def test_asin_con_403_o_404_se_omite_y_el_pase_sigue(codigo):
    # F2: publicacion retirada o puntual sin permiso = skip http_40x.
    llamadas: list = []
    estados = {
        "B0TEST0001": _ok("B0TEST0001"),
        "B0TEST0002": {
            "ofertas": ("status", codigo),
            "competitivo": _cuerpo_competitivo(),
        },
        "B0TEST0003": _ok("B0TEST0003", 50.0),
    }
    with db_pricing() as conn:
        _sembrar_universo(
            conn, [("amazon_mx", a) for a in ("B0TEST0001", "B0TEST0002", "B0TEST0003")]
        )
        resultado = pricing.ejecutar_ingesta(
            conn, _cliente_estados(estados, llamadas), platform="amazon_mx", ahora=AHORA
        )
        assert resultado.ok
        assert resultado.escritas == 2
        assert resultado.asins_vistos == 3
        # El ASIN omitido solo gasto su primera llamada.
        assert resultado.llamadas == 5
        run = conn.execute(
            "SELECT ok, rows_written, skip_reason FROM ingest_run WHERE id = %s",
            (resultado.run_id,),
        ).fetchone()
        assert run[0] is True
        assert run[1] == 2
        assert run[2] == f"1x http_{codigo}"


@_skip_db
@pytest.mark.parametrize("codigo", [401, 429])
def test_401_persistente_y_429_agotado_son_fatales(codigo):
    # F2: el cliente ya reintento una vez; si sigue, el pase aborta.
    llamadas: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        llamadas.append(request)
        return httpx.Response(codigo, json={})

    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    with db_pricing() as conn:
        _sembrar_universo(conn, [("amazon_mx", "B0TEST0001")])
        with pytest.raises(pricing.IngestaPricingError, match="persistente"):
            pricing.ejecutar_ingesta(conn, cliente, platform="amazon_mx", ahora=AHORA)
        run = conn.execute(
            "SELECT ok, rows_written FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run[0] is False
        assert run[1] == 0


@_skip_db
@pytest.mark.parametrize("error_red", [httpx.ConnectError, httpx.ReadTimeout])
def test_timeout_aislado_no_detiene_el_pase(error_red):
    # Revision #6: la red sigue la politica F2; un fallo aislado tras cinco
    # exitos se cuenta (skip "red") sin tumbar el universo restante.
    llamadas: list = []
    asins = [f"B0TEST000{i}" for i in range(1, 8)]
    estados = {a: _ok(a) for a in asins}
    estados["B0TEST0006"] = {
        "ofertas": ("red", error_red),
        "competitivo": _cuerpo_competitivo(),
    }
    with db_pricing() as conn:
        _sembrar_universo(conn, [("amazon_mx", a) for a in asins])
        resultado = pricing.ejecutar_ingesta(
            conn, _cliente_estados(estados, llamadas), platform="amazon_mx", ahora=AHORA
        )
        assert resultado.ok
        assert resultado.escritas == 6
        assert resultado.llamadas == 13
        run = conn.execute(
            "SELECT ok, rows_written, rows_skipped, skip_reason, llamadas"
            " FROM ingest_run WHERE id = %s",
            (resultado.run_id,),
        ).fetchone()
        assert run == (True, 6, 1, "1x red", 13)


@_skip_db
def test_muro_total_aborta_por_umbral():
    # F2: todos los ASIN con 500 = el primer fallo ya supera el 20 %.
    llamadas: list = []
    estados = {
        asin: {"ofertas": ("status", 500), "competitivo": _cuerpo_competitivo()}
        for asin in ("B0TEST0001", "B0TEST0002", "B0TEST0003")
    }
    with db_pricing() as conn:
        _sembrar_universo(conn, [("amazon_mx", a) for a in estados])
        with pytest.raises(pricing.IngestaPricingError, match="umbral de fallos"):
            pricing.ejecutar_ingesta(
                conn, _cliente_estados(estados, llamadas), platform="amazon_mx", ahora=AHORA
            )
        assert conn.execute("SELECT count(*) FROM spapi_price_observation").fetchone()[0] == 0
        run = conn.execute(
            "SELECT ok, skip_reason FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run[0] is False
        assert "umbral de fallos" in (run[1] or "")


@_skip_db
def test_fallo_http_sella_ok_false():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        return httpx.Response(500, json={})

    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )
    with db_pricing() as conn:
        _sembrar_universo(conn, [("amazon_mx", "B0TEST0001")])
        with pytest.raises(pricing.IngestaPricingError):
            pricing.ejecutar_ingesta(conn, cliente, platform="amazon_mx", ahora=AHORA)
        run = conn.execute(
            "SELECT ok, rows_written FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run[0] is False
        assert run[1] == 0


def test_cli_sin_dsn_falla_cerrado(monkeypatch):
    from app import cli

    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert cli.main(["ingest", "spapi_pricing", "--platform", "amazon_mx"]) == 2


def test_cli_registra_pipeline():
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "ingest", "--help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert "spapi_pricing" in proc.stdout
