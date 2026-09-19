"""Pantalla y avisos del motor de precios (REPRICING 01, A.6) — bloque AVISOS.

`notifica_precio` en `app/notifica.py`: un solo sender en flanco por racha
(S7), fail-silent como los `notifica_*` existentes. Telegram siempre falso
(`httpx.MockTransport`); la base es real (`ORBIT_TEST_DSN`, cero `skipped`
con el DSN de VERIFY). Otros carriles ANEXAN sus bloques al final de este
archivo: no borrar ni reordenar lo existente.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import socket
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente

from app import api_dashboard as dash
from app import notifica
from app.main import app

RAIZ = Path(__file__).resolve().parents[1]

ORDEN59 = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0028_estimacion_venta.sql",
    "0032_spapi_pricing.sql",
    "0035_spapi_listings_inventario.sql",
    "0039_precio.sql",
)

_skip_sin_pg = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

_CONTADOR = itertools.count()

FAKE_BOT_TOKEN = "7700000002:AAF-fake-token-precio"
FAKE_CHAT_ID = "555002"

ERROR_BODY = "Traceback RuntimeError: boom s3cr3t-r3 en patch_spapi ack={...}"


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db_temp():
    """DB temporal con ORDEN59; entrega conn en autocommit; la borra al salir."""
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"pantalla_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN59:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _config(conn, settings=None):
    base = {"precio_aviso_dias_sin_evaluar": 3}
    if settings is not None:
        base.update(settings)
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        ("test-pantalla", Json(base)),
    ).fetchone()[0]


def _producto(conn, sku="SKU-PP-1") -> int:
    return conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id",
        (sku, f"prod {sku}"),
    ).fetchone()[0]


def _listing(conn, producto: int, *, platform="amazon_mx", sku="SKU-PP-1", ext="B0PP000001"):
    return conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (producto, platform, ext, sku),
    ).fetchone()[0]


def _goal(conn, listing: int, *, platform="amazon_mx"):
    conn.execute(
        "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
        " valid_from, creado_por) VALUES (%s, %s, '0.25', 'shadow',"
        " ((now() AT TIME ZONE 'UTC')::date - 30), 'test-pantalla')",
        (listing, platform),
    )


def _decision(
    conn,
    listing: int,
    *,
    platform="amazon_mx",
    resultado="no_evaluado",
    motivo="precio_sin_observar",
    dia: date | None = None,
    buy_box=None,
):
    if dia is None:
        dia = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, decision_date, resultado,"
        " motivo, p_actual_currency, p_objetivo_currency, p_aplicado_currency,"
        " i_currency, c_currency, f_currency, l_currency, r_currency, mode,"
        " buy_box_is_own)"
        " VALUES (%s, %s, %s, %s, %s,"
        " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'shadow', %s)"
        " RETURNING id",
        (listing, platform, dia, resultado, motivo, buy_box),
    ).fetchone()[0]


def _dias_atras(conn, n: int) -> date:
    return conn.execute("SELECT ((now() AT TIME ZONE 'UTC')::date - %s::int)", (n,)).fetchone()[0]


def _canal_falso(tmp_path, monkeypatch, *, tumbar=False):
    """Canal configurado con transport falso que captura; yield mensajes."""
    from contextlib import contextmanager as _cm

    mensajes: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        mensajes.append({"path": request.url.path, **json.loads(request.content)})
        if tumbar:
            raise httpx.ConnectError(f"failed to connect to {request.url}")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    @_cm
    def _ctx():
        d = tmp_path / "secrets-precio"
        d.mkdir(exist_ok=True)
        (d / "telegram.json").write_text(
            json.dumps({"bot_token": FAKE_BOT_TOKEN, "chat_id": FAKE_CHAT_ID}),
            encoding="utf-8",
        )
        monkeypatch.setenv("ORBIT_SECRETS_DIR", str(d))
        monkeypatch.setattr(notifica, "_transporte_test", httpx.MockTransport(handler))
        notifica._reset()
        try:
            yield mensajes
        finally:
            notifica._reset()

    return _ctx()


# ---------------------------------------------------------------------------
# 1. fail-silent: canal apagado -> True
# ---------------------------------------------------------------------------


def test_precio_aviso_apagado_devuelve_true():
    for tipo, payload in [
        (
            "no_evaluado",
            notifica.GrupoPrecio("amazon_mx", "no_evaluado", "precio_sin_observar", 1, ("SKU-1",)),
        ),
        (
            "goal_inalcanzable",
            notifica.GrupoPrecio("amazon_mx", "goal_inalcanzable", "fee_no_lineal", 1, ("SKU-1",)),
        ),
        ("frenado", notifica.GrupoPrecio("amazon_mx", "frenado", "api_error", 1, ("SKU-1",))),
        (
            "no_confirmado",
            notifica.ProductoPrecio(
                "amazon_mx",
                "no_confirmado",
                "SKU-1",
                "B01",
                "116.00",
                "127.60",
                "MXN",
                "no_confirmado",
                "no_confirmado",
            ),
        ),
        (
            "buy_box_perdida",
            notifica.ProductoPrecio(
                "amazon_mx",
                "buy_box_perdida",
                "SKU-1",
                "B01",
                "116.00",
                "110.00",
                "MXN",
                "buy_box_perdida",
                "buy_box_perdida",
            ),
        ),
        ("huerfana_sin_patch", notifica.HuerfanaPrecio("amazon_mx", 2)),
    ]:
        assert notifica.notifica_precio(tipo, payload) is True


# ---------------------------------------------------------------------------
# 2. fail-silent: excepcion -> warning con scrub + False, jamas levanta
# ---------------------------------------------------------------------------


def test_precio_aviso_excepcion_warning_scrub_y_false(tmp_path, monkeypatch, caplog):
    grupo = notifica.GrupoPrecio("amazon_mx", "frenado", "api_error", 1, ("SKU-1",))
    with (
        _canal_falso(tmp_path, monkeypatch, tumbar=True),
        caplog.at_level(logging.WARNING, logger="app.notifica"),
    ):
        assert notifica.notifica_precio("frenado", grupo) is False
    assert FAKE_BOT_TOKEN not in caplog.text
    assert FAKE_CHAT_ID not in caplog.text


# ---------------------------------------------------------------------------
# 3. builders sin costo/margen/goal ni cuerpos de error (cada builder)
# ---------------------------------------------------------------------------


def test_precio_aviso_builders_sin_prohibidos():
    textos = [
        notifica.aviso_precio_grupo(
            notifica.GrupoPrecio(
                "amazon_mx", "no_evaluado", "precio_no_cubre_costo", 2, ("SKU-1", "SKU-2")
            )
        ),
        notifica.aviso_precio_grupo(
            notifica.GrupoPrecio(
                "amazon_mx", "goal_inalcanzable", "sobre_goal_sin_perdida", 1, ("SKU-3",)
            )
        ),
        notifica.aviso_precio_grupo(
            notifica.GrupoPrecio("amazon_mx", "frenado", "api_error", 1, ("SKU-4",))
        ),
        notifica.aviso_precio_producto(
            notifica.ProductoPrecio(
                "amazon_mx",
                "no_confirmado",
                "SKU-5",
                "B0PP000005",
                "116.00",
                "127.60",
                "MXN",
                "no_confirmado",
                "no_confirmado",
            )
        ),
        notifica.aviso_precio_producto(
            notifica.ProductoPrecio(
                "amazon_mx",
                "buy_box_perdida",
                "SKU-6",
                "B0PP000006",
                "130.00",
                "125.00",
                "MXN",
                "buy_box_perdida",
                "buy_box_perdida",
            )
        ),
        notifica.aviso_precio_huerfana(notifica.HuerfanaPrecio("amazon_mx", 2)),
    ]
    for texto in textos:
        bajo = texto.lower()
        assert "costo" not in bajo
        assert "margen" not in bajo
        assert "goal" not in bajo
        assert ERROR_BODY not in texto
    assert "SKU-1" in textos[0] and "amazon_mx" in textos[0]
    assert "116.00" in textos[3] and "127.60" in textos[3] and "MXN" in textos[3]
    assert "2" in textos[5] and "amazon_mx" in textos[5]


# ---------------------------------------------------------------------------
# 4. tope de 5 SKUs por aviso de grupo
# ---------------------------------------------------------------------------


def test_precio_aviso_tope_5_skus():
    skus = tuple(f"SKU-{i:03d}" for i in range(1, 8))
    texto = notifica.aviso_precio_grupo(
        notifica.GrupoPrecio("amazon_mx", "frenado", "api_error", 7, skus)
    )
    for i in range(1, 6):
        assert f"SKU-{i:03d}" in texto
    assert "SKU-006" not in texto
    assert "y 2 mas" in texto


# ---------------------------------------------------------------------------
# 5. 200 productos con el mismo motivo -> UN aviso
# ---------------------------------------------------------------------------


def test_precio_aviso_200_mismo_motivo_un_aviso(tmp_path, monkeypatch):
    skus = tuple(f"SKU-{i:03d}" for i in range(1, 201))
    grupo = notifica.GrupoPrecio("amazon_mx", "frenado", "api_error", 200, skus)
    with _canal_falso(tmp_path, monkeypatch) as mensajes:
        assert notifica.notifica_precio("frenado", grupo) is True
    assert len(mensajes) == 1
    assert "200" in mensajes[0]["text"]
    assert "SKU-006" not in mensajes[0]["text"]


# ---------------------------------------------------------------------------
# 6. flanco: la racha que sigue no vuelve a avisar
# ---------------------------------------------------------------------------


def test_precio_aviso_flanco_sigue_no_reavisa():
    hoy = date(2026, 9, 18)
    presentes = {hoy - timedelta(days=n) for n in range(4)}
    assert notifica.flanco_umbral(presentes, hoy, 3) is False
    assert notifica.flanco_nuevos(["a", "b"], ["a", "b"]) == []


# ---------------------------------------------------------------------------
# 7. flanco: la racha que se corta y vuelve si avisa
# ---------------------------------------------------------------------------


def test_precio_aviso_flanco_corta_y_vuelve():
    hoy = date(2026, 9, 18)
    assert notifica.flanco_umbral(set(), hoy, 3) is False
    presentes = {hoy - timedelta(days=n) for n in range(3)}
    assert notifica.flanco_umbral(presentes, hoy, 3) is True
    assert notifica.flanco_nuevos(["a", "b"], ["a"]) == ["b"]


# ---------------------------------------------------------------------------
# 8. umbral de dias: cota 1-14 con ValueError que nombra la clave + flanco
# ---------------------------------------------------------------------------


def test_precio_aviso_umbral_dias():
    assert notifica.validar_precio_aviso_dias({"precio_aviso_dias_sin_evaluar": 3}) == 3
    for mala in (
        {},
        {"precio_aviso_dias_sin_evaluar": 0},
        {"precio_aviso_dias_sin_evaluar": 15},
        {"precio_aviso_dias_sin_evaluar": "x"},
        {"precio_aviso_dias_sin_evaluar": True},
    ):
        with pytest.raises(ValueError, match="precio_aviso_dias_sin_evaluar"):
            notifica.validar_precio_aviso_dias(mala)


@_skip_sin_pg
def test_precio_aviso_umbral_flanco_en_base(tmp_path, monkeypatch):
    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        prod = _producto(conn)
        lid = _listing(conn, prod)
        _goal(conn, lid)
        hoy = _dias_atras(conn, 0)
        _decision(conn, lid, dia=_dias_atras(conn, 1), motivo="precio_sin_observar")
        _decision(conn, lid, dia=hoy, motivo="precio_sin_observar")
        enviados = notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen())
        assert enviados == 0
        assert mensajes == []
        lid2 = _listing(conn, prod, sku="SKU-PP-2", ext="B0PP000002")
        _goal(conn, lid2)
        _decision(conn, lid2, dia=_dias_atras(conn, 2), motivo="precio_sin_observar")
        _decision(conn, lid2, dia=_dias_atras(conn, 1), motivo="precio_sin_observar")
        _decision(conn, lid2, dia=hoy, motivo="precio_sin_observar")
        enviados = notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen())
        assert enviados == 1
        assert len(mensajes) == 1


def _resumen(huerfanas=0):
    from types import SimpleNamespace

    return SimpleNamespace(huerfanas=huerfanas)


# ---------------------------------------------------------------------------
# 9. huerfana_sin_patch: un aviso por plataforma, en flanco
# ---------------------------------------------------------------------------


def test_precio_aviso_huerfana_texto():
    texto = notifica.aviso_precio_huerfana(notifica.HuerfanaPrecio("amazon_mx", 2))
    bajo = texto.lower()
    assert "costo" not in bajo and "margen" not in bajo and "goal" not in bajo
    assert ERROR_BODY not in texto
    assert "amazon_mx" in texto and "2" in texto


@_skip_sin_pg
def test_precio_aviso_huerfana_flanco_en_base(tmp_path, monkeypatch):
    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)
        hoy = _dias_atras(conn, 0)
        assert notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen(huerfanas=2)) == 1
        assert len(mensajes) == 1
        assert "amazon_mx" in mensajes[0]["text"]


# ---------------------------------------------------------------------------
# 10. el sender jamas levanta (tipo y payload desconocidos; hook con base rota)
# ---------------------------------------------------------------------------


def test_precio_aviso_sender_jamas_levanta(tmp_path, monkeypatch, caplog):
    grupo = notifica.GrupoPrecio("amazon_mx", "frenado", "api_error", 1, ("SKU-1",))
    with (
        _canal_falso(tmp_path, monkeypatch),
        caplog.at_level(logging.WARNING, logger="app.notifica"),
    ):
        assert notifica.notifica_precio("inexistente", grupo) is False
        assert notifica.notifica_precio("frenado", "no-es-un-grupo") is False
    assert notifica.avisar_precio(_conn_cerrada(), "amazon_mx", date(2026, 9, 18), _resumen()) == 0


def _conn_cerrada():
    class _Cerrada:
        def execute(self, *args, **kwargs):
            raise psycopg.OperationalError("base caida")

    return _Cerrada()


# --- fin bloque avisos (otros carriles anexan debajo) ---

# ---------------------------------------------------------------------------
# Bloque PANTALLA (REPRICING 01, A.6): GET /api/dashboard/precios +
# plataformas.<p>.precios en /salud. El recuadro se arma EN VIVO con
# app.precio.fuentes + app.precio.cobertura (cero consultas duplicadas);
# /salud suma la clave sin cambiar las existentes. Base real
# (ORBIT_TEST_DSN), Telegram fuera de este bloque.
# ---------------------------------------------------------------------------

_CLAVES_SALUD_PREVIAS = (
    "watermark",
    "synced_at",
    "ultimo_ciclo",
    "historico_14d",
    "skips",
    "harvest_destino",
    "quota",
    "target_margen",
    "spapi",
)


@contextmanager
def _db_pantalla(sufijo=""):
    """Como _db_temp pero entrega (conn, dsn) para el TestClient de lectura."""
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"pantalla{_sufijo(sufijo)}_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN59:
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn, _dsn_de_db(dsn, db)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _sufijo(sufijo):
    return f"_{sufijo}" if sufijo else ""


@contextmanager
def _db_sin_0039():
    """DB sin la 0039 (sin tablas de precios): el camino de degradacion."""
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"pantalla_sin39_{socket.gethostname().lower()}_{os.getpid()}_{next(_CONTADOR)}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN59:
            if nombre == "0039_precio.sql":
                continue
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn, _dsn_de_db(dsn, db)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _config_pantalla(conn):
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        (
            "test-pantalla",
            Json(
                {
                    "precio_aviso_dias_sin_evaluar": 3,
                    "precio_catalogo_max_dias_sin_reportar": 7,
                }
            ),
        ),
    ).fetchone()[0]


def _estado_pantalla(conn, *, sku, platform="amazon_mx", status="BUYABLE"):
    return conn.execute(
        "INSERT INTO spapi_listing_estado_observation (seller_sku, platform, status,"
        " api_version, observed_at) VALUES (%s, %s, %s, 'v1', now()) RETURNING id",
        (sku, platform, status),
    ).fetchone()[0]


def _oferta_pantalla(
    conn, listing, *, platform="amazon_mx", sku="SKU-PP-1", ext="B0PP000001", canal="fba"
):
    return conn.execute(
        "INSERT INTO estimacion_oferta_observation (listing_id, platform, seller_sku, asin,"
        " canal, price_amount, price_currency, fetched_at, observed_at, source_event_id,"
        " canonical_input, context_fingerprint)"
        " VALUES (%s, %s, %s, %s, %s, 116, 'MXN', now(), now(), %s, %s, %s)"
        " RETURNING id",
        (
            listing,
            platform,
            sku,
            ext,
            canal,
            f"oferta-pp-{sku}-{listing}",
            Json({}),
            f"ctx-pp-{sku}-{listing}",
        ),
    ).fetchone()[0]


def _decision_subir_pantalla(conn, listing, *, platform="amazon_mx", mode="shadow"):
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, mode)"
        " VALUES (%s, %s, 'subir', 0.30, 0.24, 116, 'MXN', 116, 'MXN', 116, 'MXN',"
        " 100, 'MXN', 40, 'MXN', 15, 'MXN', 0, 'MXN', 2.50, 'MXN', %s) RETURNING id",
        (listing, platform, mode),
    ).fetchone()[0]


def _cambio_pendiente_pantalla(conn, dec, listing, *, platform="amazon_mx"):
    return conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado, enviado_at)"
        " VALUES (%s, %s, %s, 100, 'MXN', 116, 'MXN', true, 'pendiente', now())"
        " RETURNING id",
        (dec, listing, platform),
    ).fetchone()[0]


def _goal_live_pantalla(conn, listing, *, platform="amazon_mx"):
    conn.execute(
        "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
        " valid_from, creado_por, go_literal) VALUES (%s, %s, '0.25', 'live',"
        " ((now() AT TIME ZONE 'UTC')::date - 30), 'test-pantalla', 'go-test')",
        (listing, platform),
    )


def _cliente_pantalla(dsn, monkeypatch):
    monkeypatch.setenv("ORBIT_DSN_READ", dsn)
    return TestClient(app)


def _select_decisiones(conn, platform, hoy):
    return {
        fila[0]: fila[1]
        for fila in conn.execute(
            "SELECT resultado, count(*) FROM precio_decision"
            " WHERE platform = %s AND decision_date = %s GROUP BY resultado",
            (platform, hoy),
        ).fetchall()
    }


def _select_huerfanas(conn, platform):
    return conn.execute(
        "SELECT count(*) FROM precio_cambio"
        " WHERE platform = %s AND estado = 'pendiente' AND NOT es_reversa",
        (platform,),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# 1. la ruta existe y es GET con su contrato (hoy + plataformas)
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_pantalla_ruta_get_contrato(monkeypatch):
    with _db_pantalla("ruta") as (conn, dsn):
        _config_pantalla(conn)
        data = _cliente_pantalla(dsn, monkeypatch).get("/api/dashboard/precios").json()
        assert set(data) == {"hoy", "plataformas"}
        assert set(data["plataformas"]) == {"amazon_us", "amazon_mx"}
        assert (
            data["hoy"]
            == conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0].isoformat()
        )


# ---------------------------------------------------------------------------
# 2. recuadro en vivo via fuentes+cobertura, cero consultas duplicadas
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_pantalla_recuadro_en_vivo_sin_consultas_duplicadas(monkeypatch):
    fuente = (RAIZ / "app" / "api_dashboard.py").read_text(encoding="utf-8")
    for tabla in ("spapi_listing_estado_observation", "estimacion_oferta_observation"):
        assert tabla not in fuente, f"consulta duplicada en api_dashboard: {tabla}"
    import app.precio.fuentes as _fuentes

    llamadas: list = []
    real = _fuentes.leer_publicaciones

    def espia(conn, *, platform, hoy):
        llamadas.append(platform)
        return real(conn, platform=platform, hoy=hoy)

    monkeypatch.setattr(_fuentes, "leer_publicaciones", espia)
    with _db_pantalla("vivo") as (conn, _dsn):
        _config_pantalla(conn)
        dash.precios(conn)
        assert "amazon_mx" in llamadas, "el recuadro no cruza fuentes.leer_publicaciones en vivo"


# ---------------------------------------------------------------------------
# 3. decisiones del dia EXACTAS vs SELECT (por resultado)
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_pantalla_decisiones_exactas_vs_select():
    with _db_temp() as conn:
        _config_pantalla(conn)
        prod = _producto(conn)
        lid1 = _listing(conn, prod, sku="SKU-PP-10", ext="B0PP000010")
        lid2 = _listing(conn, prod, sku="SKU-PP-11", ext="B0PP000011")
        lid3 = _listing(conn, prod, sku="SKU-PP-12", ext="B0PP000012")
        _goal(conn, lid1)
        _goal(conn, lid2)
        _goal(conn, lid3)
        _decision(conn, lid1, motivo="precio_sin_observar")
        _decision(conn, lid2, motivo="precio_sin_observar")
        _decision_subir_pantalla(conn, lid3)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        data = dash.precios(conn)
        bloque = data["plataformas"]["amazon_mx"]
        assert bloque["hoy"] == hoy.isoformat()
        assert bloque["decisiones"] == _select_decisiones(conn, "amazon_mx", hoy)
        assert bloque["decisiones"] == {"no_evaluado": 2, "subir": 1}


# ---------------------------------------------------------------------------
# 4. la ecuacion del recuadro cuadra en vivo
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_pantalla_ecuacion_cuadra_en_vivo():
    with _db_temp() as conn:
        _config_pantalla(conn)
        prod = _producto(conn)
        lid1 = _listing(conn, prod, sku="SKU-PP-20", ext="B0PP000020")
        lid2 = _listing(conn, prod, sku="SKU-PP-21", ext="B0PP000021")
        _estado_pantalla(conn, sku="SKU-PP-20")
        _oferta_pantalla(conn, lid1, sku="SKU-PP-20", ext="B0PP000020")
        _goal(conn, lid1)
        _decision_subir_pantalla(conn, lid1)
        _estado_pantalla(conn, sku="SKU-PP-21")
        _oferta_pantalla(conn, lid2, sku="SKU-PP-21", ext="B0PP000021")
        rec = dash.precios(conn)["plataformas"]["amazon_mx"]["recuadro"]
        assert rec["activas"] == 2
        assert rec["evaluadas"] == 1
        assert [d["sku"] for d in rec["sin_goal"]] == ["SKU-PP-21"]
        assert rec["activas"] == (
            rec["evaluadas"]
            + sum(n["count"] for n in rec["no_evaluadas"])
            + len(rec["sin_goal"])
            + sum(n["count"] for n in rec["fuera_de_alcance"])
        )


# ---------------------------------------------------------------------------
# 5. huerfanas pendientes EXACTAS vs SELECT
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_pantalla_huerfanas_exactas_vs_select():
    with _db_temp() as conn:
        _config_pantalla(conn)
        prod = _producto(conn)
        lid = _listing(conn, prod, sku="SKU-PP-30", ext="B0PP000030")
        _goal_live_pantalla(conn, lid)
        dec = _decision_subir_pantalla(conn, lid, mode="live")
        _cambio_pendiente_pantalla(conn, dec, lid)
        bloque = dash.precios(conn)["plataformas"]["amazon_mx"]
        assert bloque["huerfanas"] == 1
        assert bloque["huerfanas"] == _select_huerfanas(conn, "amazon_mx")


# ---------------------------------------------------------------------------
# 6. /salud suma precios SIN cambiar las claves existentes
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_pantalla_salud_agrega_precios_sin_cambiar_claves():
    with _db_temp() as conn:
        _config_pantalla(conn)
        data = dash.salud(conn)
        for plataforma in ("amazon_us", "amazon_mx"):
            assert set(data["plataformas"][plataforma]) == {
                *_CLAVES_SALUD_PREVIAS,
                "precios",
            }


# ---------------------------------------------------------------------------
# 7. /salud.precios EXACTO vs SELECT (decisiones del dia + huerfanas)
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_pantalla_salud_precios_exactos_vs_select():
    with _db_temp() as conn:
        _config_pantalla(conn)
        prod = _producto(conn)
        lid1 = _listing(conn, prod, sku="SKU-PP-40", ext="B0PP000040")
        lid2 = _listing(conn, prod, sku="SKU-PP-41", ext="B0PP000041")
        _goal(conn, lid1)
        _goal_live_pantalla(conn, lid2)
        _decision(conn, lid1, motivo="precio_sin_observar")
        dec = _decision_subir_pantalla(conn, lid2, mode="live")
        _cambio_pendiente_pantalla(conn, dec, lid2)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        precios = dash.salud(conn)["plataformas"]["amazon_mx"]["precios"]
        assert precios["hoy"] == hoy.isoformat()
        assert precios["decisiones"] == _select_decisiones(conn, "amazon_mx", hoy)
        assert precios["huerfanas"] == _select_huerfanas(conn, "amazon_mx") == 1


# ---------------------------------------------------------------------------
# 8. sin 0039: /salud None+warning y /precios error claro, nunca 500
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_pantalla_sin_0039_none_warning_y_error_claro(monkeypatch, caplog):
    import logging as _logging

    with _db_sin_0039() as (conn, dsn):
        with caplog.at_level(_logging.WARNING, logger="app.api_dashboard"):
            data = dash.salud(conn)
        for plataforma in ("amazon_us", "amazon_mx"):
            assert data["plataformas"][plataforma]["precios"] is None
        assert "0039" in caplog.text
        resp = _cliente_pantalla(dsn, monkeypatch).get("/api/dashboard/precios")
        assert resp.status_code != 500
        assert resp.status_code == 503
        assert "0039" in resp.json()["detail"]


# --- fin bloque pantalla (otros carriles anexan debajo) ---

# ---------------------------------------------------------------------------
# Bloque UI (REPRICING 01, A.6): GET /precios server-rendered + enlace en
# base + bloque precios en /salud HTML. Cinco bloques S7 en orden
# (cobertura, con goal, hecho/habria-hecho, no evaluados, divergente),
# frases con numeros, estados sin-datos/error/exito. Base real
# (ORBIT_TEST_DSN). Otros carriles anexan debajo: no borrar ni reordenar.
# ---------------------------------------------------------------------------

_ORDEN_BLOQUES_UI = (
    "bloque-cobertura",
    "bloque-con-goal",
    "bloque-hecho",
    "bloque-no-evaluados",
    "bloque-divergente",
)


def _semilla_cobertura_ui(conn):
    """2 activas canonicas: 1 evaluada (subir, shadow) + 1 sin goal."""
    prod = _producto(conn)
    lid1 = _listing(conn, prod, sku="SKU-UI-01", ext="B0UI000001")
    lid2 = _listing(conn, prod, sku="SKU-UI-02", ext="B0UI000002")
    _estado_pantalla(conn, sku="SKU-UI-01")
    _oferta_pantalla(conn, lid1, sku="SKU-UI-01", ext="B0UI000001")
    _goal(conn, lid1)
    _decision_subir_pantalla(conn, lid1)
    _estado_pantalla(conn, sku="SKU-UI-02")
    _oferta_pantalla(conn, lid2, sku="SKU-UI-02", ext="B0UI000002")
    return prod


def _html_precios(conn, dsn, monkeypatch):
    resp = _cliente_pantalla(dsn, monkeypatch).get("/precios")
    assert resp.status_code == 200
    return resp.text


# ---------------------------------------------------------------------------
# U1. la ruta existe, es GET y trae los cinco bloques S7 en orden
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_ui_ruta_get_cinco_bloques_en_orden(monkeypatch):
    with _db_pantalla("ui-orden") as (conn, dsn):
        _config_pantalla(conn)
        _semilla_cobertura_ui(conn)
        html = _html_precios(conn, dsn, monkeypatch)
        posiciones = [html.find(bloque) for bloque in _ORDEN_BLOQUES_UI]
        assert all(pos >= 0 for pos in posiciones), "falta un bloque S7"
        assert posiciones == sorted(posiciones), "bloques S7 fuera de orden"


# ---------------------------------------------------------------------------
# U2. cobertura arriba: la ecuacion cuadra con numeros + puente + aviso 5%
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_ui_cobertura_cuadra_puente_y_aviso_5(monkeypatch):
    with _db_pantalla("ui-puente") as (conn, dsn):
        _config_pantalla(conn)
        _semilla_cobertura_ui(conn)
        prod = _producto(conn, sku="SKU-UI-PUENTE")
        for i in range(3):
            _listing(conn, prod, sku=f"SKU-UI-P{i}", ext=f"B0UI00010{i}")
        html = _html_precios(conn, dsn, monkeypatch)
        assert "2 activas" in html
        assert "puente" in html.lower()
        assert "5%" in html


# ---------------------------------------------------------------------------
# U3. con goal: evaluadas con numeros + sin goal listado con precio y canal
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_ui_con_goal_con_numeros(monkeypatch):
    with _db_pantalla("ui-goal") as (conn, dsn):
        _config_pantalla(conn)
        _semilla_cobertura_ui(conn)
        html = _html_precios(conn, dsn, monkeypatch)
        assert "1 evaluada" in html
        assert "SKU-UI-02" in html
        assert "116" in html
        assert "fba" in html


# ---------------------------------------------------------------------------
# U4. hecho/habria-hecho con condicional sombra
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_ui_hecho_habria_hecho_sombra(monkeypatch):
    with _db_pantalla("ui-hecho") as (conn, dsn):
        _config_pantalla(conn)
        prod = _producto(conn)
        lid1 = _listing(conn, prod, sku="SKU-UI-40", ext="B0UI000040")
        lid2 = _listing(conn, prod, sku="SKU-UI-41", ext="B0UI000041")
        _goal(conn, lid1)
        _decision_subir_pantalla(conn, lid1)
        _goal_live_pantalla(conn, lid2)
        dec = _decision_subir_pantalla(conn, lid2, mode="live")
        _cambio_pendiente_pantalla(conn, dec, lid2)
        html = _html_precios(conn, dsn, monkeypatch)
        assert "habr" in html
        assert "subir" in html.lower()
        assert "pendiente" in html.lower()


# ---------------------------------------------------------------------------
# U5. no evaluados del dia con su motivo en palabras (sin id crudo)
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_ui_no_evaluados_en_palabras(monkeypatch):
    with _db_pantalla("ui-palabras") as (conn, dsn):
        _config_pantalla(conn)
        prod = _producto(conn)
        lid = _listing(conn, prod, sku="SKU-UI-50", ext="B0UI000050")
        _estado_pantalla(conn, sku="SKU-UI-50")
        _oferta_pantalla(conn, lid, sku="SKU-UI-50", ext="B0UI000050")
        _goal(conn, lid)
        _decision(conn, lid, motivo="precio_sin_observar")
        html = _html_precios(conn, dsn, monkeypatch)
        assert "sin observaci" in html.lower()
        assert "precio_sin_observar" not in html


# ---------------------------------------------------------------------------
# U6. bloque divergente: con divergencia y sin ella
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_ui_divergente_con_y_sin(monkeypatch):
    with _db_pantalla("ui-div") as (conn, dsn):
        _config_pantalla(conn)
        prod = _producto(conn)
        lid = _listing(conn, prod, sku="SKU-UI-60", ext="B0UI000060")
        _estado_pantalla(conn, sku="SKU-UI-60")
        _oferta_pantalla(conn, lid, sku="SKU-UI-60", ext="B0UI000060")
        _goal(conn, lid)
        _decision(conn, lid, motivo="precio_divergente")
        html = _html_precios(conn, dsn, monkeypatch)
        seccion = html.split('id="bloque-divergente-amazon_mx"')[1].split("</section>")[0]
        assert "divergente" in seccion.lower() or "divergencia" in seccion.lower()
        assert "distinto del publicado" in seccion.lower()
        assert "precio_divergente" not in html
    with _db_pantalla("ui-sindiv") as (conn, dsn):
        _config_pantalla(conn)
        html = _html_precios(conn, dsn, monkeypatch)
        assert "sin divergencias" in html.lower()


# ---------------------------------------------------------------------------
# U7. sin datos: base con config pero sin activas -> 200 con estado visible
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_ui_sin_datos_estado_visible(monkeypatch):
    with _db_pantalla("ui-vacia") as (conn, dsn):
        _config_pantalla(conn)
        html = _html_precios(conn, dsn, monkeypatch)
        assert "sin publicaciones activas" in html.lower()


# ---------------------------------------------------------------------------
# U8. sin 0039: /precios HTML 503 claro con 0039, nunca 500
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_ui_sin_0039_error_claro_nunca_500(monkeypatch):
    with _db_sin_0039() as (conn, dsn):
        resp = _cliente_pantalla(dsn, monkeypatch).get("/precios")
        assert resp.status_code != 500
        assert resp.status_code == 503
        assert "0039" in resp.text


# ---------------------------------------------------------------------------
# U9. /salud HTML trae el bloque precios con numeros
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_ui_salud_html_bloque_precios(monkeypatch):
    with _db_pantalla("ui-salud") as (conn, dsn):
        _config_pantalla(conn)
        _semilla_cobertura_ui(conn)
        resp = _cliente_pantalla(dsn, monkeypatch).get("/salud")
        assert resp.status_code == 200
        assert "bloque-precios" in resp.text
        assert "huerfana" in resp.text.lower()


# ---------------------------------------------------------------------------
# U10. nav con enlace + SKU hostil escapado + SKU largo visible
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_precio_ui_nav_enlace_y_skus(monkeypatch):
    with _db_pantalla("ui-nav") as (conn, dsn):
        _config_pantalla(conn)
        prod = _producto(conn)
        hostil = "<script>alert(1)</script>"
        largo = "SKU-UI-" + "X" * 60
        lid1 = _listing(conn, prod, sku=hostil, ext="B0UI000071")
        lid2 = _listing(conn, prod, sku=largo, ext="B0UI000072")
        for sku, lid, ext in ((hostil, lid1, "B0UI000071"), (largo, lid2, "B0UI000072")):
            _estado_pantalla(conn, sku=sku)
            _oferta_pantalla(conn, lid, sku=sku, ext=ext)
        html = _html_precios(conn, dsn, monkeypatch)
        assert 'href="/precios"' in html
        assert hostil not in html
        assert "&lt;script&gt;" in html
        assert largo in html
        assert "cero CDN" in html or "cdn" not in html.lower().replace("cero cdn", "")


# --- fin bloque UI (otros carriles anexan debajo) ---

# ---------------------------------------------------------------------------
# Bloque CIERRE (REPRICING 01, A.6): el CLI pasa el gancho de avisos +
# corrida real con avisar que levanta y con Telegram caido: decisiones y
# resumen ok. Base real (ORBIT_TEST_DSN); el CLI puro va con `correr`
# falso (sin PG). Otros carriles anexan debajo: no borrar ni reordenar.
# ---------------------------------------------------------------------------


def test_precio_cierre_cli_pasa_avisar_a_correr(monkeypatch, capsys):
    """C1: `precio --platform` pasa `avisar=avisar_precio` a `correr`."""
    from app import cli as cli_mod

    llamadas = {}

    def _falso_correr(conn, platform, **kw):
        llamadas["platform"] = platform
        llamadas["kw"] = kw
        from types import SimpleNamespace

        return SimpleNamespace(decisiones=1, escritas=0, lineas=(), errores=())

    monkeypatch.setenv("ORBIT_DSN_DECIDE", "postgresql://falso/db")
    monkeypatch.setattr(cli_mod, "_conexion_decide", lambda: ("conn-falsa", lambda: None))
    monkeypatch.setattr(cli_mod, "_clientes_precio", lambda platform: ("l", "e", "f", "cubo"))
    monkeypatch.setattr("app.precio.corrida.correr", _falso_correr)
    assert cli_mod.main(["precio", "--platform", "amazon_mx"]) == 0
    assert llamadas["platform"] == "amazon_mx"
    assert llamadas["kw"].get("avisar") is notifica.avisar_precio


def test_precio_cierre_import_tardio_en_precio():
    """C2: el import de avisos vive DENTRO de `_precio` (D9: no tumba otros)."""
    import inspect

    from app import cli as cli_mod

    fuente = inspect.getsource(cli_mod._precio)
    assert "from app.notifica import avisar_precio" in fuente


@_skip_sin_pg
def test_precio_cierre_corrida_real_avisar_que_levanta():
    """C3: corrida real con un avisar que levanta: decide igual y lo registra."""
    from test_precio_corrida import _cadena, _corre, _price_obs, _RedFalsa

    with _db_temp() as conn:
        datos = _cadena(conn)
        _price_obs(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})

        def _avisar_malo(*args):
            raise RuntimeError("sender caido")

        res = _corre(conn, red, avisar=_avisar_malo)
        assert res.decisiones == 1
        assert conn.execute("SELECT count(*) FROM precio_decision").fetchone()[0] == 1
        assert any("avisar" in e for e in res.errores)


@_skip_sin_pg
def test_precio_cierre_corrida_real_telegram_caido(tmp_path, monkeypatch):
    """C4: corrida real con el gancho real y Telegram caido: decide y no levanta."""
    from test_precio_corrida import _cadena, _corre, _price_obs, _RedFalsa

    with _db_temp() as conn:
        datos = _cadena(conn)
        _price_obs(conn)
        prod2 = _producto(conn, sku="SKU-CIERRE-2")
        lid2 = _listing(conn, prod2, sku="SKU-CIERRE-2", ext="B0CIERRE02")
        _goal(conn, lid2)
        _decision(conn, lid2, resultado="frenado", motivo="api_error")
        actual = dict(
            conn.execute("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1").fetchone()[
                0
            ]
        )
        actual["precio_aviso_dias_sin_evaluar"] = 3
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s)",
            ("test-cierre", Json(actual)),
        )
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        with _canal_falso(tmp_path, monkeypatch, tumbar=True):
            assert (
                notifica.notifica_precio(
                    "frenado",
                    notifica.GrupoPrecio("amazon_mx", "frenado", "api_error", 1, ("SKU-CIERRE-2",)),
                )
                is False
            )
            res = _corre(conn, red, avisar=notifica.avisar_precio)
        assert res.decisiones == 1
        assert conn.execute("SELECT count(*) FROM precio_decision").fetchone()[0] == 2


# --- fin bloque CIERRE (otros carriles anexan debajo) ---
