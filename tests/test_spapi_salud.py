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
import re
import socket
import sys
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
    "0037_ingest_run_salud_idx.sql",
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
        r2 = _sellar(
            conn,
            "spapi_orders",
            "amazon_mx",
            True,
            "4x duplicada",
            escritas=9,
            skips=4,
            llamadas=3,
        )
        # 429 y despues una ok: ultima_429 sobrevive a la ok posterior.
        r429 = _sellar(conn, "spapi_pricing", "amazon_mx", False, "http_429: pricing status=429")
        rok = _sellar(conn, "spapi_pricing", "amazon_mx", True, escritas=2, llamadas=1)
        # Motivo con 429 en el MEDIO (caso del brief): no es ultima_429.
        _sellar(
            conn,
            "spapi_listings",
            "amazon_mx",
            False,
            "contrato: codigo 429 en texto libre",
        )
        rlwa = _sellar(conn, "spapi_listings", "amazon_mx", False, "lwa_fallido: LWA rechazo (401)")
        # Trampa del comodin LIKE: con LIKE 'http_429%' (salud.py anterior)
        # '_' matchea 'X' y esta fila CAERIA en ultima_429.
        _sellar(conn, "spapi_inventario", "amazon_mx", False, "httpX429: trampa")
        conn.commit()
        bloque = bloque_salud(conn, "amazon_mx")
        ultima = bloque["spapi_orders"]["ultima"]
        # Id exacto: mata ORDER BY id ASC y rows_skipped hardcodeado.
        assert ultima["id"] == r2
        assert ultima["ok"] is True
        assert ultima["rows_written"] == 9
        assert ultima["rows_skipped"] == 4
        assert ultima["skip_reason"] == "4x duplicada"
        assert ultima["llamadas"] == 3
        assert ultima["finished_at"] == AHORA.isoformat()
        # started_at real, no fin copiado (mata started_at = fin).
        assert ultima["started_at"] == (AHORA - timedelta(minutes=5)).isoformat()
        assert bloque["spapi_orders"]["ultima_429"] is None
        assert bloque["spapi_pricing"]["ultima"]["id"] == rok
        assert bloque["spapi_pricing"]["ultima_429"]["id"] == r429
        assert bloque["spapi_pricing"]["ultima_429"]["skip_reason"].startswith("http_429")
        assert bloque["spapi_listings"]["ultimo_lwa"]["id"] == rlwa
        assert bloque["spapi_listings"]["ultima_429"] is None
        assert bloque["spapi_inventario"]["ultima_429"] is None
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


@_skip_db
@pytest.mark.parametrize(
    "modulo,error",
    [
        (pricing, pricing.IngestaPricingError),
        (listings, listings.IngestaListingsError),
    ],
)
def test_muro_503_umbral_sella_http_5xx(modulo, error):
    """Opt-9: muro de 503s -> la muerte por umbral lleva el ultimo status y
    el sello sale con prefijo http_5xx (no 'contrato'). Mata la mutacion
    'mensaje de umbral sin ultimo status'."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _lwa_ok(request)
        return httpx.Response(503, json={})

    with db_salud() as conn:
        _sembrar_sku(conn)
        with pytest.raises(error, match="umbral de fallos"):
            modulo.ejecutar_ingesta(
                conn, _cliente_spapi(handler), platform="amazon_mx", ahora=AHORA
            )
        motivo = conn.execute(
            "SELECT skip_reason FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert motivo.startswith("http_5xx: ")
        assert "ultimo status=503" in motivo


def test_vigilar_umbral_racha_lleva_ultimo_status():
    """Opt-9: la via racha>=25 tambien clasifica (unidad: sin 125 fixtures
    el mensaje es el contrato con prefijo_motivo)."""
    for modulo, error in (
        (pricing, pricing.IngestaPricingError),
        (listings, listings.IngestaListingsError),
    ):
        try:
            modulo._vigilar_umbral("X-1", 25, 25, 125, 503)
        except error as exc:
            assert "ultimo status=503" in str(exc)
            assert prefijo_motivo(exc) == "http_5xx"
        else:
            raise AssertionError("umbral racha 25 no aborto")


# ---------------------------------------------------------------------------
# 3 y 4. Flanco, 429 y LWA
# ---------------------------------------------------------------------------


@_skip_db
def test_flanco_una_alerta_por_racha(tmp_path, monkeypatch):
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 1")
        r2 = _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 2")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", r2)
        assert len(mensajes) == 1
        # Tercera fallida de la misma racha: cero alertas nuevas.
        r3 = _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 3")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", r3)
        assert len(mensajes) == 1
        # Una ok rompe la racha; dos fallidas nuevas = una alerta nueva.
        rok = _sellar(conn, "spapi_orders", "amazon_mx", True, escritas=3)
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", rok)
        assert len(mensajes) == 1
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 4")
        r5 = _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 5")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", r5)
        assert len(mensajes) == 2


@_skip_db
def test_429_persistente_alerta_inmediata(tmp_path, monkeypatch):
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        rid = _sellar(conn, "spapi_pricing", "amazon_mx", False, "http_429: pricing status=429")
        conn.commit()
        evaluar_alertas(conn, "spapi_pricing", "amazon_mx", rid)
        assert len(mensajes) == 1
        # Etiquetas exactas: el intercambio fuente/plataforma en la llamada
        # las romperia (mutacion salud.py: notifica_spapi_fallo(platform,
        # fuente, motivo)).
        assert "fuente: spapi_pricing" in mensajes[0]
        assert "plataforma: amazon_mx" in mensajes[0]
        # El matiz de Ads es SOLO para LWA, no para 429.
        assert "El ciclo de Ads no se afecta" not in mensajes[0]


@_skip_db
def test_lwa_alerta_con_matiz_ads(tmp_path, monkeypatch):
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        rid = _sellar(conn, "spapi_orders", "amazon_mx", False, "lwa_fallido: LWA rechazo (401)")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", rid)
        assert len(mensajes) == 1
        assert "El ciclo de Ads no se afecta (procesos y credenciales distintos)." in (mensajes[0])


@_skip_db
def test_429_tres_seguidos_una_sola_alerta(tmp_path, monkeypatch):
    """Tres 429 seguidos = UN mensaje (el de apertura). Mata la mutacion
    'inmediata siempre alerta' (sin comparar con la anterior)."""
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        for i in range(1, 4):
            rid = _sellar(
                conn,
                "spapi_pricing",
                "amazon_mx",
                False,
                f"http_429: pricing status=429 ({i})",
            )
            conn.commit()
            evaluar_alertas(conn, "spapi_pricing", "amazon_mx", rid)
        assert len(mensajes) == 1


@_skip_db
def test_lwa_tres_seguidos_una_sola_alerta(tmp_path, monkeypatch):
    """Tres LWA seguidos = UN mensaje. Misma mutacion que el de 429."""
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        for i in range(1, 4):
            rid = _sellar(
                conn,
                "spapi_orders",
                "amazon_mx",
                False,
                f"lwa_fallido: LWA rechazo (401) ({i})",
            )
            conn.commit()
            evaluar_alertas(conn, "spapi_orders", "amazon_mx", rid)
        assert len(mensajes) == 1


@_skip_db
def test_cambio_de_clase_dentro_de_racha_re_alerta(tmp_path, monkeypatch):
    """contrato,contrato -> 1 mensaje; lwa_fallido despues -> 2do mensaje
    (cambio de clase = informacion nueva). Mata la mutacion 'inmediata
    solo si la anterior NO estaba fallida' (sin comparar CLASE)."""
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 1")
        r2 = _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 2")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", r2)
        assert len(mensajes) == 1
        r3 = _sellar(conn, "spapi_orders", "amazon_mx", False, "lwa_fallido: LWA rechazo (401)")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", r3)
        assert len(mensajes) == 2


@_skip_db
def test_una_sola_contrato_sin_historia_cero_mensajes(tmp_path, monkeypatch):
    """Una fallida 'contrato' sin historia NO alerta (el resto espera la
    segunda). Mata la mutacion `if len(ultimas) < 2: return
    ultimas[0][3] is False`."""
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        rid = _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom unico")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", rid)
        assert mensajes == []


@_skip_db
def test_evaluar_alertas_fail_silent(tmp_path, monkeypatch):
    with db_salud() as conn, _canal(tmp_path, monkeypatch):
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 1")
        r2 = _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 2")
        conn.commit()
        monkeypatch.setattr(
            "app.spapi.salud.notifica_spapi_fallo",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("notifica roto")),
        )
        assert evaluar_alertas(conn, "spapi_orders", "amazon_mx", r2) is None


@_skip_db
def test_evaluar_alertas_deja_conexion_idle(tmp_path, monkeypatch):
    """B1: tras evaluar_alertas la conexion queda IDLE (la lectura va en su
    propio bloque y commitea). Mata la mutacion 'SELECT suelto sin with
    conn.transaction()' (deja INTRANS)."""
    psycopg = pytest.importorskip("psycopg")

    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 1")
        r2 = _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 2")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", r2)
        assert len(mensajes) == 1
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


@_skip_db
def test_dos_ingestas_persisten_desde_otra_conexion():
    """B1: dos ejecutar_ingesta seguidas sobre la misma conexion y la
    persistencia verificada DESDE OTRA conexion. Sin el bloque de lectura
    en evaluar_alertas, la segunda ingesta vive en savepoints que el close
    descarta: la otra conexion ve 0 filas."""
    import psycopg
    from psycopg.conninfo import make_conninfo

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return _lwa_ok(request)
        return httpx.Response(200, json={"payload": {"orders": []}})

    with db_salud() as conn:
        for _ in range(2):
            resultado = orders.ejecutar_ingesta(
                conn, _cliente_spapi(handler), platform="amazon_mx", ahora=AHORA
            )
            assert resultado.ok
        otra = psycopg.connect(make_conninfo(_test_dsn(), dbname=conn.info.dbname), autocommit=True)
        try:
            filas = otra.execute("SELECT count(*) FROM ingest_run").fetchone()[0]
        finally:
            otra.close()
        assert filas == 2


@_skip_db
def test_huerfana_abierta_no_enmascara_racha(tmp_path, monkeypatch):
    """B4: fallida + huerfana ABIERTA (ok NULL) + fallida => alerta en la
    segunda fallida. Mata la mutacion 'sin AND ok IS NOT NULL' (la huerfana
    en ultimas[1] rompe la racha)."""
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 1")
        conn.execute(
            "INSERT INTO ingest_run (source, platform, started_at) VALUES (%s, %s, %s)",
            ("spapi_orders", "amazon_mx", AHORA - timedelta(minutes=3)),
        )
        r3 = _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 2")
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", r3)
        assert len(mensajes) == 1


@_skip_db
def test_ancla_corrida_sellada_ante_nuevas(tmp_path, monkeypatch):
    """B4: con corridas mas nuevas (una SELLADA ok y una ABIERTA), la alerta
    evalua la sellada r2 (ancla `id <= run_id`), no las mas nuevas. Mata la
    mutacion 'sin AND id <= %s' (ultimas[0] seria r3-ok, motivo vacio y sin
    racha: 0 mensajes en vez de 1)."""
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 1")
        r2 = _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 2")
        _sellar(conn, "spapi_orders", "amazon_mx", True, escritas=5)
        conn.execute(
            "INSERT INTO ingest_run (source, platform, started_at) VALUES (%s, %s, %s)",
            ("spapi_orders", "amazon_mx", AHORA - timedelta(minutes=1)),
        )
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", r2)
        assert len(mensajes) == 1


@_skip_db
def test_motivo_con_secreto_no_sale_por_telegram(tmp_path, monkeypatch):
    """Regla dura: un secreto/PII que llegue al motivo (ya scrubbeado en el
    sello; doble scrub en el aviso) JAMAS sale por Telegram."""
    from app.redaction import REDACTED, register_secret

    secreto = "tok-secreto-simulado-a5-xyz"
    register_secret(secreto)
    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        rid = _sellar(
            conn,
            "spapi_orders",
            "amazon_mx",
            False,
            f"lwa_fallido: LWA rechazo con {secreto} en el cuerpo",
        )
        conn.commit()
        evaluar_alertas(conn, "spapi_orders", "amazon_mx", rid)
        assert len(mensajes) == 1
        assert secreto not in mensajes[0]
        assert REDACTED in mensajes[0]


# ---------------------------------------------------------------------------
# 5. Aislamiento del ciclo de Ads
# ---------------------------------------------------------------------------


def _es_ruta_ads(nombre: str) -> bool:
    return nombre == "app.ads" or nombre.startswith("app.ads.")


def test_aislamiento_sin_imports_ads():
    """salud.py y las 4 ingestas no importan app.ads DIRECTO (AST, como
    test_architecture: ni siquiera diferido en funciones). Cubre `import
    x`, `from x import y`, `from app import ads`, `__import__` e
    `importlib.import_module` con literal app.ads*."""
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
                assert not any(_es_ruta_ads(a.name) for a in nodo.names), rel
            elif isinstance(nodo, ast.ImportFrom):
                if nodo.module == "app":
                    assert not any(n == "ads" or n.startswith("ads.") for n in nodo.names), rel
                elif nodo.module:
                    assert not _es_ruta_ads(nodo.module), rel
            elif isinstance(nodo, ast.Call):
                func = nodo.func
                dinamico = (
                    isinstance(func, ast.Name)
                    and func.id == "__import__"
                    or isinstance(func, ast.Attribute)
                    and func.attr == "import_module"
                )
                if dinamico and nodo.args and isinstance(nodo.args[0], ast.Constant):
                    assert not _es_ruta_ads(str(nodo.args[0].value)), rel


def test_aislamiento_grafo_runtime_solo_config_inerte():
    """En runtime `import app.spapi.salud` SI mete app.ads en sys.modules,
    pero SOLO app.ads.config (constantes inertes, cero IO) via
    app/notifica.py:38 — nunca app.ads.write/client. Allowlist explicita
    en subproceso limpio (el proceso de pytest ya trae medio mundo)."""
    import subprocess

    codigo = (
        "import sys, app.spapi.salud; "
        "mods = sorted(m for m in sys.modules "
        'if m == "app.ads" or m.startswith("app.ads.")); '
        "print(mods)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", codigo],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert ast.literal_eval(proc.stdout.strip()) == ["app.ads", "app.ads.config"]


@_skip_db
@pytest.mark.parametrize(
    "pipeline,modulo",
    [
        ("spapi_orders", orders),
        ("spapi_pricing", pricing),
        ("spapi_listings", listings),
        ("spapi_inventario", inventario),
    ],
)
def test_aislamiento_lwa_caido_sella_alerta_y_retorno_limpio(
    tmp_path, monkeypatch, pipeline, modulo
):
    """B5: refresh LWA caido en CADA pipeline: main retorna 1 SIN excepcion
    no controlada, la fila sellada tiene ok=false + prefijo lwa_fallido +
    platform amazon_mx, y sale EXACTAMENTE un mensaje.

    Mata: borrar evaluar_alertas de la rama except (0 mensajes); abrir el
    run con plataforma cambiada o NULL (platform != amazon_mx).
    """
    from app import cli

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(500, json={})
        raise AssertionError("sin token no hay llamadas SP-API")

    def fabrica(**kwargs):
        return _cliente_spapi(handler)

    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        from psycopg.conninfo import make_conninfo

        # pricing/listings leen su universo de listing: sin una fila el
        # pase sellaria ok=true sin tocar la red (falso verde).
        if pipeline in ("spapi_pricing", "spapi_listings"):
            _sembrar_sku(conn)
            conn.commit()
        monkeypatch.setattr(modulo, "SpapiClient", fabrica)
        dsn_test = make_conninfo(_test_dsn(), dbname=conn.info.dbname)
        monkeypatch.setenv("ORBIT_DSN_INGEST", dsn_test)
        from app import db as modulo_db

        real_connect = modulo_db.connect
        monkeypatch.setattr(modulo_db, "connect", lambda _dsn: real_connect(dsn_test))
        assert cli.main(["ingest", pipeline, "--platform", "amazon_mx"]) == 1
        fila = conn.execute(
            "SELECT ok, skip_reason, platform FROM ingest_run ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert fila[0] is False
        assert fila[1].startswith("lwa_fallido: ")
        assert fila[2] == "amazon_mx"
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


@_skip_db
def test_historial_roto_no_impide_el_sello(tmp_path, monkeypatch, caplog):
    """El fail-silent cubre tambien la excepcion de BD dentro de
    evaluar_alertas (sin este test, `_historial` puede reventar sin que
    nada lo note). Mata la mutacion 'evaluar_alertas = return None' Y
    'sin try/except en evaluar_alertas'."""
    import psycopg

    from app.spapi import salud as modulo_salud

    with db_salud() as conn, _canal(tmp_path, monkeypatch) as mensajes:
        _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 1")
        r2 = _sellar(conn, "spapi_orders", "amazon_mx", False, "contrato: boom 2")
        conn.commit()

        def historial_roto(_conn, _fuente, _platform, _run_id):
            raise psycopg.errors.InFailedSqlTransaction("transaccion abortada")

        monkeypatch.setattr(modulo_salud, "_historial", historial_roto)
        with caplog.at_level("WARNING", logger="app.spapi.salud"):
            assert evaluar_alertas(conn, "spapi_orders", "amazon_mx", r2) is None
        # Sello intacto, cero mensajes, salida limpia con el log esperado.
        fila = conn.execute("SELECT ok FROM ingest_run WHERE id = %s", (r2,)).fetchone()
        assert fila[0] is False
        assert mensajes == []
        assert any("no pudo correr" in r.getMessage() for r in caplog.records)


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
            "0037_ingest_run_salud_idx.sql",
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
        r2 = _sellar(conn, "spapi_orders", "amazon_mx", True, escritas=6, llamadas=2)
        r429 = _sellar(conn, "spapi_pricing", "amazon_mx", False, "http_429: pricing status=429")
        rok = _sellar(conn, "spapi_pricing", "amazon_mx", True, escritas=1, llamadas=1)
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_lectura)
        data = TestClient(app).get("/api/dashboard/salud").json()["plataformas"]
        mx = data["amazon_mx"]["spapi"]
        # Id exacto de la ultima (mata ORDER BY ASC) y valores, no claves.
        assert mx["spapi_orders"]["ultima"]["id"] == r2
        assert mx["spapi_orders"]["ultima"]["rows_written"] == 6
        assert mx["spapi_orders"]["ultima"]["llamadas"] == 2
        assert mx["spapi_pricing"]["ultima"]["id"] == rok
        assert mx["spapi_pricing"]["ultima_429"]["id"] == r429
        assert mx["spapi_pricing"]["ultima_429"]["skip_reason"].startswith("http_429")
        assert mx["spapi_listings"] == {"ultima": None, "ultima_429": None, "ultimo_lwa": None}
        assert data["amazon_us"]["spapi"]["spapi_orders"]["ultima"] is None


@_skip_db
def test_ui_salud_renderiza_spapi(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    with db_pantalla() as (conn, dsn_lectura):
        r1 = _sellar(
            conn,
            "spapi_orders",
            "amazon_mx",
            True,
            "2x duplicada",
            escritas=4,
            skips=2,
            llamadas=1,
        )
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_lectura)
        resp = TestClient(app).get("/salud")
        assert resp.status_code == 200
        assert "SP-API" in resp.text
        # Fila spapi_orders de MX con celdas REALES (mata las 6 celdas en
        # '-'): chip ok, #id, filas, skips y motivo. (La plantilla no
        # muestra llamadas: se afirman las celdas que SI renderiza.)
        # (La tarjeta MX es la segunda: PLATAFORMAS_MONEDA arranca en US.)
        mx = resp.text.split("<h2>amazon_mx", 1)[1]
        m = re.search(r"<td>spapi_orders</td>(.*?)</tr>", mx, re.S)
        assert m, "fila spapi_orders de MX presente"
        fila = m.group(1)
        assert ">ok<" in fila
        assert f"#{r1}" in fila
        assert ">4<" in fila
        assert ">2<" in fila
        assert "2x duplicada" in fila
        assert "sin corridas" in mx


@_skip_db
def test_ui_salud_sobrevive_bloque_spapi_roto(monkeypatch):
    """Opt-8: si bloque_salud levanta (p. ej. 0036 sin aplicar), /salud
    responde 200 y conserva el resto; el bloque spapi queda en None.
    Mata la mutacion 'sin try/except en _spapi_de' (500 en la pagina)."""
    import psycopg
    from fastapi.testclient import TestClient

    from app import api_dashboard
    from app.main import app

    with db_pantalla() as (_conn, dsn_lectura):
        monkeypatch.setenv("ORBIT_DSN_READ", dsn_lectura)

        def roto(_conn, _platform):
            raise psycopg.errors.UndefinedColumn('columna "platform" no existe')

        monkeypatch.setattr(api_dashboard, "bloque_salud", roto)
        pagina = TestClient(app).get("/salud")
        assert pagina.status_code == 200
        assert "Historico" in pagina.text
        assert "<h3>SP-API</h3>" not in pagina.text
        data = TestClient(app).get("/api/dashboard/salud").json()["plataformas"]
        assert data["amazon_mx"]["spapi"] is None
        assert "watermark" in data["amazon_mx"]
        assert "quota" in data["amazon_mx"]


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
        # B3: el privilegio que las 4 ingestas REALMENTE ejercen es INSERT
        # (apertura del run). Mata la mutacion `REVOKE INSERT ON ingest_run
        # FROM app_ingest` en 0036 (el DO $$ tambien la mata: la migracion
        # revienta en el assert positivo de INSERT).
        assert conn.execute(
            "SELECT has_column_privilege('app_ingest', 'ingest_run', 'platform', 'INSERT')"
        ).fetchone()[0]
        conn.execute("SET ROLE app_ingest")
        try:
            conn.execute(
                "INSERT INTO ingest_run (source, platform) VALUES (%s, %s)",
                ("spapi_orders", "amazon_mx"),
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE ingest_run SET platform = 'amazon_us'")
        finally:
            conn.rollback()
            conn.execute("RESET ROLE")
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


@_skip_db
def test_migracion_0037_indice_salud():
    """Opt-7: el indice (source, platform, id DESC) existe (las 3 consultas
    de /salud por fuente+plataforma lo usan)."""
    with db_salud() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM pg_indexes"
                " WHERE indexname = 'ingest_run_source_platform_id_idx'"
            ).fetchone()[0]
            == 1
        )
