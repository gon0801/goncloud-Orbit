"""Tests SP-API 01 A.5 — salud y alertas.

bloque_salud y evaluar_alertas leen SOLO ingest_run (regla 2); la
plataforma por corrida viene de la columna platform (migracion 0036).
Taxonomia de motivos con prefijo (lwa_fallido, http_429, http_5xx, red,
contrato); alertas solo en flanco; LWA caido no toca el ciclo de Ads.

Cero red real: todo SP-API y Telegram contra httpx.MockTransport.
"""

from __future__ import annotations

import ast
import json
import os
import socket
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app import notifica
from app.spapi import inventario, listings, orders, pricing
from app.spapi.client import SpapiAuthError, SpapiClient, SpapiRechazoLWA, prefijo_motivo
from app.spapi.salud import FUENTES_SPAPI, bloque_salud, evaluar_alertas

ROOT = Path(__file__).resolve().parents[1]
ORDEN = (
    "0001_initial.sql",
    "0030_spapi_orders.sql",
    "0031_spapi_orders_bitemporal.sql",
    "0032_spapi_pricing.sql",
    "0033_ingest_run_llamadas.sql",
    "0034_ingest_run_llamadas_grant.sql",
    "0035_spapi_listings_inventario.sql",
    "0036_ingest_run_platform.sql",
)

AHORA = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
CRED = {
    "lwa_app_id": "id-salud",
    "lwa_client_secret": "secreto-salud",
    "refresh_token": "refresh-salud",
}
_TOKEN_LWA = "tk-spapi-salud-fixture-lwa"

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


@contextmanager
def db_salud(prefijo: str = "orbit_spapi_a5"):
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


def _sellar(
    conn,
    source,
    platform,
    ok,
    motivo=None,
    escritas=0,
    skips=0,
    llamadas=0,
    inicio=None,
    fin=None,
):
    return conn.execute(
        "INSERT INTO ingest_run (source, platform, ok, rows_written, rows_skipped,"
        " skip_reason, llamadas, started_at, finished_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            source,
            platform,
            ok,
            escritas,
            skips,
            motivo,
            llamadas,
            inicio or AHORA - timedelta(minutes=5),
            AHORA if fin is None else fin,
        ),
    ).fetchone()[0]


@contextmanager
def _canal(tmp_path, monkeypatch, *, tumbar=False):
    """Telegram falso (patron tests/test_notifica.py): telegram.json en tmp
    + transporte mockeado; yield la lista de textos capturados."""
    d = tmp_path / "secrets"
    d.mkdir(exist_ok=True)
    (d / "telegram.json").write_text(
        json.dumps({"bot_token": "tok-falsa-a5-12345", "chat_id": "99"}), encoding="utf-8"
    )
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(d))
    mensajes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        mensajes.append(json.loads(request.content)["text"])
        if tumbar:
            raise httpx.ConnectError(f"failed to connect to {request.url}")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(notifica, "_transporte_test", httpx.MockTransport(handler))
    notifica._reset()
    yield mensajes
    notifica._reset()


# ---------------------------------------------------------------------------
# 1. bloque_salud
# ---------------------------------------------------------------------------


@_skip_db
def test_bloque_salud_sin_corridas_todo_none():
    with db_salud() as conn:
        bloque = bloque_salud(conn, "amazon_mx")
        assert set(bloque) == set(FUENTES_SPAPI)
        for fuente in FUENTES_SPAPI:
            assert bloque[fuente] == {"ultima": None, "ultima_429": None, "ultimo_lwa": None}


@_skip_db
def test_bloque_salud_lee_solo_ingest_run():
    with db_salud() as conn:
        # Corrida vieja sin plataforma: invisible para el bloque (0036).
        _sellar(conn, "spapi_orders", None, True, escritas=5, llamadas=1)
        _sellar(
            conn,
            "spapi_orders",
            "amazon_mx",
            True,
            escritas=7,
            llamadas=2,
            inicio=AHORA - timedelta(minutes=10),
        )
        _sellar(conn, "spapi_pricing", "amazon_mx", False, "http_429: pricing status=429")
        _sellar(conn, "spapi_listings", "amazon_mx", False, "lwa_fallido: LWA rechazo (401)")
        conn.commit()
        bloque = bloque_salud(conn, "amazon_mx")
        ultima = bloque["spapi_orders"]["ultima"]
        assert ultima["ok"] is True
        assert ultima["rows_written"] == 7
        assert ultima["llamadas"] == 2
        assert ultima["finished_at"] == AHORA.isoformat()
        assert bloque["spapi_orders"]["ultima_429"] is None
        assert bloque["spapi_pricing"]["ultima"]["ok"] is False
        assert bloque["spapi_pricing"]["ultima_429"]["skip_reason"].startswith("http_429")
        assert bloque["spapi_listings"]["ultimo_lwa"]["skip_reason"].startswith("lwa_fallido")
        assert bloque["spapi_inventario"] == {
            "ultima": None,
            "ultima_429": None,
            "ultimo_lwa": None,
        }
        # La otra plataforma no ve corridas ajenas.
        assert bloque_salud(conn, "amazon_us")["spapi_orders"]["ultima"] is None


# ---------------------------------------------------------------------------
# 2. Taxonomia del motivo
# ---------------------------------------------------------------------------


def test_prefijo_motivo_taxonomia():
    assert prefijo_motivo(SpapiAuthError("respuesta LWA sin access_token")) == "lwa_fallido"
    assert prefijo_motivo(SpapiRechazoLWA(401)) == "lwa_fallido"
    assert prefijo_motivo(httpx.ConnectError("boom")) == "red"
    assert prefijo_motivo(httpx.ReadTimeout("boom")) == "red"
    assert prefijo_motivo(orders.IngestaOrdersError("orders status=429")) == "http_429"
    assert prefijo_motivo(pricing.IngestaPricingError("pricing x status=429 persistente")) == (
        "http_429"
    )
    assert prefijo_motivo(orders.IngestaOrdersError("orders status=500")) == "http_5xx"
    assert prefijo_motivo(inventario.IngestaInventarioError("inventario status=503")) == (
        "http_5xx"
    )
    assert prefijo_motivo(ValueError("algo raro")) == "contrato"
    assert prefijo_motivo(orders.IngestaOrdersError("orders respuesta no JSON")) == "contrato"
    assert (
        prefijo_motivo(pricing.IngestaPricingError("pricing x: umbral de fallos (3/3)"))
        == "contrato"
    )


def _cliente_spapi(handler):
    return SpapiClient(
        credentials=CRED,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        clock=lambda: 1000.0,
    )


def _lwa_ok(request):
    return httpx.Response(200, json={"access_token": _TOKEN_LWA, "expires_in": 3600})


def _sembrar_sku(conn, sku="SKU-A5-1", asin="B0TEST0001"):
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (f"P-{sku}", f"Prod {sku}"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku,"
        " listing_price, price_currency)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (pid, "amazon_mx", asin, sku, "100.0000", "MXN"),
    )


@_skip_db
def test_orders_500_sella_http_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _lwa_ok(request)
        return httpx.Response(500, json={})

    with db_salud() as conn:
        with pytest.raises(orders.IngestaOrdersError):
            orders.ejecutar_ingesta(
                conn, _cliente_spapi(handler), platform="amazon_mx", ahora=AHORA
            )
        motivo = conn.execute(
            "SELECT skip_reason FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert motivo.startswith("http_5xx: ")


@_skip_db
def test_pricing_429_persistente_sella_http_429():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _lwa_ok(request)
        return httpx.Response(429, json={})

    with db_salud() as conn:
        _sembrar_sku(conn)
        with pytest.raises(pricing.IngestaPricingError, match="persistente"):
            pricing.ejecutar_ingesta(
                conn, _cliente_spapi(handler), platform="amazon_mx", ahora=AHORA
            )
        motivo = conn.execute(
            "SELECT skip_reason FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert motivo.startswith("http_429: ")


@_skip_db
def test_listings_lwa_caido_sella_lwa_fallido():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(500, json={})
        raise AssertionError("sin token no hay llamadas SP-API")

    with db_salud() as conn:
        _sembrar_sku(conn)
        # El raise original se propaga (diseno A.1-A.4); el sello con
        # prefijo es lo que este test exige.
        with pytest.raises(SpapiRechazoLWA):
            listings.ejecutar_ingesta(
                conn, _cliente_spapi(handler), platform="amazon_mx", ahora=AHORA
            )
        motivo = conn.execute(
            "SELECT skip_reason FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert motivo.startswith("lwa_fallido: ")


@_skip_db
def test_inventario_red_sella_red():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _lwa_ok(request)
        raise httpx.ConnectError("corte de red simulado")

    with db_salud() as conn:
        # Como arriba: se propaga el ConnectError original; el sello con
        # prefijo es lo que se exige.
        with pytest.raises(httpx.ConnectError):
            inventario.ejecutar_ingesta(
                conn, _cliente_spapi(handler), platform="amazon_mx", ahora=AHORA
            )
        motivo = conn.execute(
            "SELECT skip_reason FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert motivo.startswith("red: ")


@_skip_db
def test_orders_contrato_sella_contrato():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _lwa_ok(request)
        return httpx.Response(200, json={"payload": [1, 2]})

    with db_salud() as conn:
        with pytest.raises(orders.IngestaOrdersError, match="contrato"):
            orders.ejecutar_ingesta(
                conn, _cliente_spapi(handler), platform="amazon_mx", ahora=AHORA
            )
        motivo = conn.execute(
            "SELECT skip_reason FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert motivo.startswith("contrato: ")


# ---------------------------------------------------------------------------
# 3 y 4. Flanco, 429 y LWA
# ---------------------------------------------------------------------------


@_skip_db
def test_flanco_una_alerta_por_racha(tmp_path, monkeypatch):
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 1")
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 2")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx")
        assert len(mensajes) == 1
        # Tercera fallida de la misma racha: cero alertas nuevas.
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 3")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx")
        assert len(mensajes) == 1
        # Una ok rompe la racha; dos fallidas nuevas = una alerta nueva.
        _sellar(conn, "spapi_orders", "amazon_mx", True, escritas=3)
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx")
        assert len(mensajes) == 1
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 4")
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 5")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx")
        assert len(mensajes) == 2


@_skip_db
def test_429_persistente_alerta_inmediata(tmp_path, monkeypatch):
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        _sellar(conn, "spapi_pricing", "amazon_mx", False, "http_429: pricing status=429")
        conn.commit()
        evaluar_alertas(conn, "spapi_pricing", "amazon_mx")
        assert len(mensajes) == 1
        assert "spapi_pricing" in mensajes[0] and "amazon_mx" in mensajes[0]


@_skip_db
def test_lwa_alerta_con_matiz_ads(tmp_path, monkeypatch):
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        _sellar(conn, "spapi_orders", "amazon_mx", False, "lwa_fallido: LWA rechazo (401)")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx")
        assert len(mensajes) == 1
        assert "Ads" in mensajes[0]


@_skip_db
def test_evaluar_alertas_fail_silent(tmp_path, monkeypatch):
    with db_salud() as conn, _canal(tmp_path, monkeypatch):
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 1")
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 2")
        conn.commit()
        monkeypatch.setattr(
            "app.spapi.salud.notifica_spapi_fallo",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("notifica roto")),
        )
        assert evaluar_alertas(conn, "spapi_orders", "amazon_mx") is None


# ---------------------------------------------------------------------------
# 5. Aislamiento del ciclo de Ads
# ---------------------------------------------------------------------------


def test_aislamiento_sin_imports_ads():
    """salud.py y las 4 ingestas no importan app.ads en runtime (AST, como
    test_architecture: ni siquiera diferido en funciones)."""
    import sys

    for rel in (
        "app/spapi/salud.py",
        "app/spapi/orders.py",
        "app/spapi/pricing.py",
        "app/spapi/listings.py",
        "app/spapi/inventario.py",
    ):
        arbol = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                prohibido = any(
                    a.name == "app.ads" or a.name.startswith("app.ads.") for a in nodo.names
                )
                assert not prohibido, rel
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                assert nodo.module != "app.ads", rel
                assert not nodo.module.startswith("app.ads."), rel
    sys.modules.pop("app.spapi.salud", None)


@_skip_db
def test_aislamiento_lwa_caido_sella_alerta_y_retorno_limpio(tmp_path, monkeypatch):
    """Refresh LWA caido: la ingesta sella ok=false con prefijo, alerta por
    Telegram y main retorna 1 SIN excepcion no controlada."""
    from app import cli
    from app.spapi import orders as modulo_orders

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(500, json={})
        raise AssertionError("sin token no hay llamadas SP-API")

    def fabrica(**kwargs):
        return _cliente_spapi(handler)

    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        from psycopg.conninfo import make_conninfo

        monkeypatch.setattr(modulo_orders, "SpapiClient", fabrica)
        dsn_test = make_conninfo(_test_dsn(), dbname=conn.info.dbname)
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_test)
        from app import db as modulo_db

        real_connect = modulo_db.connect
        monkeypatch.setattr(modulo_db, "connect", lambda _dsn: real_connect(dsn_test))
        assert cli.main(["ingest", "spapi_orders", "--platform", "amazon_mx"]) == 1
        fila = conn.execute(
            "SELECT ok, skip_reason FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert fila[0] is False
        assert fila[1].startswith("lwa_fallido: ")
        assert len(mensajes) == 1


@_skip_db
def test_canal_roto_no_impide_el_sello(tmp_path, monkeypatch):
    """Con notifica roto (red que revienta), la ingesta igual sella ok=false."""
    from app import cli
    from app.spapi import orders as modulo_orders

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(500, json={})
        raise AssertionError("sin token no hay llamadas SP-API")

    def fabrica(**kwargs):
        return _cliente_spapi(handler)

    with db_salud() as conn, _canal(tmp_path, monkeypatch, tumbar=True):
        from psycopg.conninfo import make_conninfo

        monkeypatch.setattr(modulo_orders, "SpapiClient", fabrica)
        dsn_test = make_conninfo(_test_dsn(), dbname=conn.info.dbname)
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_test)
        from app import db as modulo_db

        real_connect = modulo_db.connect
        monkeypatch.setattr(modulo_db, "connect", lambda _dsn: real_connect(dsn_test))
        assert cli.main(["ingest", "spapi_orders", "--platform", "amazon_mx"]) == 1
        fila = conn.execute("SELECT ok FROM ingest_run ORDER BY id DESC LIMIT 1").fetchone()
        assert fila[0] is False


# ---------------------------------------------------------------------------
# 6. /salud con bloque spapi (API y pagina)
# ---------------------------------------------------------------------------


@contextmanager
def db_pantalla(prefijo: str = "orbit_spapi_a5ui"):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in (
            "0001_initial.sql",
            "0002_apply.sql",
            "0033_ingest_run_llamadas.sql",
            "0034_ingest_run_llamadas_grant.sql",
            "0036_ingest_run_platform.sql",
        ):
            conn.execute((ROOT / "migrations" / nombre).read_text(encoding="utf-8"))
        from psycopg.conninfo import make_conninfo

        yield conn, make_conninfo(dsn, dbname=db)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


@_skip_db
def test_dashboard_salud_incluye_spapi_con_y_sin_corridas(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    with db_pantalla() as (conn, dsn_lectura):
        _sellar(conn, "spapi_orders", "amazon_mx", True, escritas=4, llamadas=1)
        _sellar(conn, "spapi_pricing", "amazon_mx", False, "http_429: pricing status=429")
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_lectura)
        data = TestClient(app).get("/api/dashboard/salud").json()["plataformas"]
        mx = data["amazon_mx"]["spapi"]
        assert mx["spapi_orders"]["ultima"]["rows_written"] == 4
        assert mx["spapi_pricing"]["ultima_429"]["skip_reason"].startswith("http_429")
        assert mx["spapi_listings"] == {"ultima": None, "ultima_429": None, "ultimo_lwa": None}
        assert data["amazon_us"]["spapi"]["spapi_orders"]["ultima"] is None


@_skip_db
def test_ui_salud_renderiza_spapi(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    with db_pantalla() as (conn, dsn_lectura):
        _sellar(conn, "spapi_orders", "amazon_mx", True, escritas=4, llamadas=1)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_lectura)
        resp = TestClient(app).get("/salud")
        assert resp.status_code == 200
        assert "SP-API" in resp.text
        assert "spapi_orders" in resp.text


# ---------------------------------------------------------------------------
# Migracion 0036
# ---------------------------------------------------------------------------


@_skip_db
def test_migracion_0036_platform_y_grants():
    import psycopg

    with db_salud() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM information_schema.columns WHERE table_name = 'ingest_run'"
                " AND column_name = 'platform'"
            ).fetchone()[0]
            == 1
        )
        # Filas viejas: plataforma desconocida (NULL), no inventada.
        rid = conn.execute(
            "INSERT INTO ingest_run (source) VALUES ('spapi_orders') RETURNING id"
        ).fetchone()[0]
        assert (
            conn.execute("SELECT platform FROM ingest_run WHERE id = %s", (rid,)).fetchone()[0]
            is None
        )
        conn.rollback()
        for rol in ("app_read", "app_ingest", "app_decide", "app_admin"):
            assert conn.execute(
                "SELECT has_column_privilege(%s, 'ingest_run', 'platform', 'SELECT')",
                (rol,),
            ).fetchone()[0], rol
        # Inmutabilidad por permisos: ni ingest puede reatribuir plataforma.
        assert not conn.execute(
            "SELECT has_column_privilege('app_ingest', 'ingest_run', 'platform', 'UPDATE')"
        ).fetchone()[0]
        # La plataforma viaja en el INSERT (permiso de tabla ya existente).
        conn.execute(
            "INSERT INTO ingest_run (source, platform) VALUES (%s, %s)",
            ("spapi_orders", "amazon_mx"),
        )
        conn.rollback()
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            conn.execute(
                "INSERT INTO ingest_run (source, platform) VALUES (%s, %s)",
                ("spapi_orders", "plataforma_inventada"),
            )
        conn.rollback()
