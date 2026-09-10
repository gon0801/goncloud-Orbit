"""Tests SP-API 01 A.4 — ingesta Inventario FBA v1.

(a) UNITARIOS: parseo del shape del acta 0.4 (pagination HERMANA de
payload, token repetido, pagina vacia con token, ultima sin token).
Cero red real, cero DB.
(b) INTEGRACION: migracion 0035 (clave unica, CHECK cantidades, trigger
de metric_date con inmunidad a TimeZone, append-only, grants) e ingesta
punta a punta idempotente en Postgres de test (skipea sin ORBIT_TEST_DSN).
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

from app.spapi import inventario
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
    "lwa_app_id": "id-inventario",
    "lwa_client_secret": "secreto-inventario",
    "refresh_token": "refresh-inventario",
}
_TOKEN_LWA = "tk-spapi-inventario-fixture-lwa"
MID_MX = "A1AM78C64UM0Y8"

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


def _summary(sku="SKU-A4-1", qty=7, **mas):
    # Shape del acta 0.4 (claves_item pineadas en la sonda real).
    base = {
        "asin": "B0TEST0001",
        "condition": "New",
        "fnSku": "FBA0001",
        "lastUpdatedTime": "2026-09-08T11:00:00Z",
        "productName": "Producto de prueba",
        "sellerSku": sku,
        "stores": ["MX"],
        "totalQuantity": qty,
    }
    base.update(mas)
    return base


def _pagina(summaries, token=None):
    # pagination HERMANA de payload (correccion ronda 2 del acta 0.4).
    cuerpo: dict = {"payload": {"inventorySummaries": summaries}}
    if token is not None:
        cuerpo["pagination"] = {"nextToken": token}
    return cuerpo


def test_parsea_summary_completo():
    inv = inventario.parsear_summary(_summary())
    assert inv.seller_sku == "SKU-A4-1"
    assert inv.asin == "B0TEST0001"
    assert inv.fn_sku == "FBA0001"
    assert inv.total_quantity == 7
    assert inv.fulfillable_quantity is None


def test_sin_sku_se_omite():
    cuerpo = _summary()
    del cuerpo["sellerSku"]
    with pytest.raises(inventario.InventarioOmitido, match="sin_sku"):
        inventario.parsear_summary(cuerpo)


def test_sin_cantidad_se_omite():
    cuerpo = _summary()
    del cuerpo["totalQuantity"]
    with pytest.raises(inventario.InventarioOmitido, match="sin_cantidad"):
        inventario.parsear_summary(cuerpo)
    # Cantidad negativa o no entera tampoco es el dato de la tabla.
    for mala in (-1, 2.5, "7", True):
        with pytest.raises(inventario.InventarioOmitido, match="sin_cantidad"):
            inventario.parsear_summary(_summary(qty=mala))


def test_fulfillable_solo_con_details():
    inv = inventario.parsear_summary(_summary(**{"inventoryDetails": {"fulfillableQuantity": 5}}))
    assert inv.fulfillable_quantity == 5
    malo = inventario.parsear_summary(_summary(**{"inventoryDetails": {"fulfillableQuantity": -2}}))
    assert malo.fulfillable_quantity is None


def test_token_repetido_para_y_marca():
    paginas = [
        _pagina([_summary()], token="T"),
        {"token_entrada": "T", "cuerpo": _pagina([_summary()], token="T")},
    ]
    cliente = _cliente(paginas)
    resenas, info = inventario.recorrer_summaries(cliente, _params(), max_paginas=5)
    assert info["aviso_paginacion"] == "next_token_repetido"
    assert info["paginas"] == 2
    assert len(resenas) == 2


def test_pagina_vacia_con_token_para_y_marca():
    paginas = [
        _pagina([_summary()], token="T2"),
        {"token_entrada": "T2", "cuerpo": _pagina([], token="T3")},
    ]
    cliente = _cliente(paginas)
    resenas, info = inventario.recorrer_summaries(cliente, _params(), max_paginas=5)
    assert info["aviso_paginacion"] == "pagina_vacia_con_token"
    assert len(resenas) == 1


def test_ultima_pagina_sin_token_cierra_limpio():
    paginas = [
        _pagina([_summary()], token="T2"),
        {"token_entrada": "T2", "cuerpo": _pagina([_summary(sku="SKU-A4-2")])},
    ]
    cliente = _cliente(paginas)
    resenas, info = inventario.recorrer_summaries(cliente, _params(), max_paginas=5)
    assert info["aviso_paginacion"] is None
    assert info["paginas"] == 2
    assert len(resenas) == 2


def test_tope_de_paginas_para_y_marca():
    paginas = [
        _pagina([_summary()], token="Q1"),
        _pagina([_summary(sku="SKU-A4-2")], token="Q2"),
    ]
    cliente = _cliente(paginas)
    resenas, info = inventario.recorrer_summaries(cliente, _params(), max_paginas=1)
    assert info["aviso_paginacion"] == "limite_max_paginas"
    assert info["paginas"] == 1
    assert len(resenas) == 1


def test_contrato_inesperado_es_fatal():
    for cuerpo in (
        {"payload": {"pagination": {"nextToken": "X"}}},
        {"payload": [1, 2]},
        {"payload": {"inventorySummaries": {"no": "lista"}}},
        [{"sellerSku": "suelta"}],
    ):
        cliente = _cliente([{"cuerpo": cuerpo}])
        with pytest.raises(inventario.IngestaInventarioError, match="contrato inesperado"):
            inventario.recorrer_summaries(cliente, _params())


def _params():
    return {
        "granularityType": "Marketplace",
        "granularityId": MID_MX,
        "marketplaceIds": MID_MX,
    }


def _cliente(paginas, *, llamadas=None, dormidas=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        if llamadas is not None:
            llamadas.append(request)
        assert request.url.params.get("granularityType") == "Marketplace"
        assert request.url.params.get("granularityId") == MID_MX
        assert request.url.params.get("marketplaceIds") == MID_MX
        token = request.url.params.get("nextToken")
        if token is None:
            cuerpo = paginas[0]
        else:
            cuerpo = next((p for p in paginas if p.get("token_entrada") == token), None)
            if cuerpo is None:
                return httpx.Response(200, json=_pagina([]))
        return httpx.Response(200, json=cuerpo.get("cuerpo", cuerpo))

    return SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=(dormidas.append if dormidas is not None else lambda _s: None),
        clock=lambda: 1000.0,
    )


# ---------------------------------------------------------------------------
# (b) INTEGRACION
# ---------------------------------------------------------------------------


@contextmanager
def db_inventario(prefijo: str = "orbit_spapi_a4i"):
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


@_skip_db
def test_migracion_clave_cantidades_append_only_y_grants():
    import psycopg

    with db_inventario() as conn:
        conn.execute(
            "INSERT INTO spapi_inventario_observation"
            " (seller_sku, asin, fn_sku, platform, metric_date, observed_at,"
            " total_quantity, fulfillable_quantity, api_version, ingest_run_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                "SKU-A4-1",
                "B0TEST0001",
                "FBA0001",
                "amazon_mx",
                datetime(2026, 9, 9).date(),
                AHORA,
                7,
                5,
                "v1",
                None,
            ),
        )
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO spapi_inventario_observation"
                " (seller_sku, platform, metric_date, observed_at, total_quantity,"
                " api_version)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                ("SKU-A4-1", "amazon_mx", datetime(2026, 9, 9).date(), AHORA, 7, "v1"),
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO spapi_inventario_observation"
                " (seller_sku, platform, metric_date, observed_at, total_quantity,"
                " api_version)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                ("SKU-A4-9", "amazon_mx", datetime(2026, 9, 9).date(), AHORA, -1, "v1"),
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("UPDATE spapi_inventario_observation SET total_quantity = 9")
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("DELETE FROM spapi_inventario_observation")
        conn.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute("TRUNCATE spapi_inventario_observation")
        conn.rollback()
        assert conn.execute(
            "SELECT has_table_privilege('app_read', 'spapi_inventario_observation', 'SELECT')"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_inventario_observation', 'INSERT')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_decide', 'spapi_inventario_observation', 'INSERT')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_inventario_observation', 'UPDATE')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_table_privilege('app_ingest', 'spapi_inventario_observation', 'DELETE')"
        ).fetchone()[0]


@_skip_db
def test_trigger_metric_date_rechaza_dia_y_es_inmune_a_timezone():
    import psycopg

    with db_inventario() as conn:
        # Dia inconsistente truena aunque la sesion este en UTC.
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO spapi_inventario_observation"
                " (seller_sku, platform, metric_date, observed_at, total_quantity,"
                " api_version)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                ("SKU-A4-9", "amazon_mx", datetime(2026, 9, 8).date(), AHORA, 1, "v1"),
            )
        conn.rollback()
        # Inmune a la TimeZone de la sesion: con America/Mexico_City el
        # `::date` del observed_at daria 09-08 (la trampa que un CHECK
        # pisaria), pero el trigger exige el dia UTC 09-09.
        conn.execute("SET TIME ZONE 'America/Mexico_City'")
        assert (
            conn.execute("SELECT (TIMESTAMPTZ '2026-09-09 00:30:00+00')::date").fetchone()[0]
            == datetime(2026, 9, 8).date()
        )
        conn.execute(
            "INSERT INTO spapi_inventario_observation"
            " (seller_sku, platform, metric_date, observed_at, total_quantity,"
            " api_version)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                "SKU-A4-9",
                "amazon_mx",
                datetime(2026, 9, 9).date(),
                datetime(2026, 9, 9, 0, 30, 0, tzinfo=UTC),
                1,
                "v1",
            ),
        )
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO spapi_inventario_observation"
                " (seller_sku, platform, metric_date, observed_at, total_quantity,"
                " api_version)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    "SKU-A4-8",
                    "amazon_mx",
                    datetime(2026, 9, 8).date(),
                    datetime(2026, 9, 9, 0, 30, 0, tzinfo=UTC),
                    1,
                    "v1",
                ),
            )
        conn.rollback()


@_skip_db
def test_pase_punta_a_punta_idempotente():
    llamadas: list = []
    dormidas: list = []
    reloj = {"v": 5000.0}
    paginas = [
        _pagina([_summary(), _summary(sku="SKU-A4-2", qty=3)], token="P2"),
        {
            "token_entrada": "P2",
            "cuerpo": _pagina(
                [
                    {"sellerSku": "SKU-A4-3", "totalQuantity": 0},
                    {"sellerSku": "SKU-A4-4"},
                ]
            ),
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})
        llamadas.append(request)
        token = request.url.params.get("nextToken")
        if token is None:
            cuerpo = paginas[0]
        else:
            cuerpo = next((p for p in paginas if p.get("token_entrada") == token), None)
            assert cuerpo is not None
        return httpx.Response(200, json=cuerpo.get("cuerpo", cuerpo))

    cliente = SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=dormidas.append,
        clock=lambda: reloj["v"],
    )
    with db_inventario() as conn:
        resultado = inventario.ejecutar_ingesta(
            conn, cliente, platform="amazon_mx", ahora=AHORA, max_paginas=25
        )
        assert resultado.ok
        assert resultado.escritas == 3
        assert resultado.paginas == 2
        assert resultado.llamadas == 2
        assert resultado.aviso_paginacion is None
        run = conn.execute(
            "SELECT ok, rows_written, rows_skipped, skip_reason, llamadas"
            " FROM ingest_run WHERE id = %s",
            (resultado.run_id,),
        ).fetchone()
        assert run[0] is True
        assert run[1] == 3
        assert run[2] == 1
        assert run[3] == "1x sin_cantidad"
        assert run[4] == 2
        fila = conn.execute(
            "SELECT seller_sku, asin, fn_sku, total_quantity, metric_date, api_version"
            " FROM spapi_inventario_observation WHERE seller_sku = 'SKU-A4-1'"
        ).fetchone()
        assert fila == (
            "SKU-A4-1",
            "B0TEST0001",
            "FBA0001",
            7,
            datetime(2026, 9, 9).date(),
            "v1",
        )
        cero = conn.execute(
            "SELECT total_quantity FROM spapi_inventario_observation WHERE seller_sku = 'SKU-A4-3'"
        ).fetchone()
        assert cero[0] == 0

        # Re-pase con el mismo observed_at: idempotente.
        segunda = inventario.ejecutar_ingesta(
            conn, cliente, platform="amazon_mx", ahora=AHORA, max_paginas=25
        )
        assert segunda.escritas == 0
        assert conn.execute("SELECT count(*) FROM spapi_inventario_observation").fetchone()[0] == 3
        run2 = conn.execute(
            "SELECT rows_skipped, skip_reason FROM ingest_run WHERE id = %s",
            (segunda.run_id,),
        ).fetchone()
        assert "duplicada" in (run2[1] or "")


@_skip_db
def test_paginacion_incompleta_sella_ok_false_con_lo_escrito():
    paginas = [
        _pagina([_summary()], token="Q"),
        {"token_entrada": "Q", "cuerpo": _pagina([_summary(sku="SKU-A4-2")], token="Q")},
    ]
    cliente = _cliente(paginas)
    with db_inventario() as conn:
        resultado = inventario.ejecutar_ingesta(
            conn, cliente, platform="amazon_mx", ahora=AHORA, max_paginas=5
        )
        assert resultado.ok is False
        assert resultado.escritas == 2
        assert conn.execute("SELECT count(*) FROM spapi_inventario_observation").fetchone()[0] == 2
        run = conn.execute(
            "SELECT ok, rows_written, skip_reason FROM ingest_run WHERE id = %s",
            (resultado.run_id,),
        ).fetchone()
        assert run[0] is False
        assert run[1] == 2
        assert run[2] == "paginacion_incompleta:next_token_repetido"


def test_cli_sin_dsn_falla_cerrado(monkeypatch):
    from app import cli

    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    assert cli.main(["ingest", "spapi_inventario", "--platform", "amazon_mx"]) == 2
