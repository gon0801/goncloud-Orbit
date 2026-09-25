"""Pantalla y avisos del motor de precios (REPRICING 01, A.6) — bloque AVISOS.

`notifica_precio` en `app/notifica.py`: un solo sender en flanco por racha
(S7), fail-silent como los `notifica_*` existentes. Telegram siempre falso
(`httpx.MockTransport`); la base es real (`ORBIT_TEST_DSN`, cero `skipped`
con el DSN de VERIFY). Otros carriles ANEXAN sus bloques al final de este
archivo: no borrar ni reordenar lo existente.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
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

FAKE_BOT_TOKEN = "7700000002:AAF-fake-token-precio"
FAKE_CHAT_ID = "555002"

ERROR_BODY = "Traceback RuntimeError: boom s3cr3t-r3 en patch_spapi ack={...}"

# B9: el mensaje exacto cuando la 0039 no esta aplicada (sin numero de
# migracion; la plantilla tampoco lo nombra).
_MSG_SIN_MOTOR = "el motor de precios todavía no está instalado en esta base"


def _dsn_base() -> str:
    return os.environ.get("ORBIT_TEST_DSN", "postgresql://orbit:orbit@localhost:5432/postgres")


def _dsn_de_db(dsn_base: str, db: str) -> str:
    partes = urlsplit(dsn_base)
    return urlunsplit((partes.scheme, partes.netloc, "/" + db, partes.query, partes.fragment))


@contextmanager
def _db_nueva(*, omit=()):
    """UNICO helper de BD temporal (B11): nombre con uuid corto (PG corta a
    63 bytes y el hostname completo perdia pid y contador); `DROP` solo si
    el `CREATE` salio bien; `omit` lista que migracion saltar (la sin-0039
    omite `0039_precio.sql`). Entrega `(conn, dsn)` en autocommit."""
    from psycopg import sql as pgsql

    dsn = _dsn_base()
    db = f"pp_{uuid.uuid4().hex[:8]}"
    omitidas = set(omit)
    admin = psycopg.connect(dsn, autocommit=True)
    creada = False
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        creada = True
        conn = psycopg.connect(_dsn_de_db(dsn, db), autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN59:
            if nombre in omitidas:
                continue
            conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
        yield conn, _dsn_de_db(dsn, db)
    finally:
        if conn is not None:
            conn.close()
        if creada:
            admin.execute(
                pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
            )
        admin.close()


@contextmanager
def _db_temp():
    """DB temporal con ORDEN59; entrega conn en autocommit; la borra al salir."""
    with _db_nueva() as (conn, _dsn):
        yield conn


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


def test_precio_aviso_apagado_devuelve_true(tmp_path, monkeypatch):
    """B10: el canal se apaga de forma EXPLICITA (dir sin telegram.json +
    `_reset`, como `tests/test_notifica.py`): en una maquina con secretos
    reales este test no sale a la red."""
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path))
    notifica._reset()
    try:
        assert notifica.canal_activo() is False
        for tipo, payload in [
            (
                "no_evaluado",
                notifica.GrupoPrecio(
                    "amazon_mx", "no_evaluado", "precio_sin_observar", 1, ("SKU-1",)
                ),
            ),
            (
                "goal_inalcanzable",
                notifica.GrupoPrecio(
                    "amazon_mx", "goal_inalcanzable", "fee_no_lineal", 1, ("SKU-1",)
                ),
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
    finally:
        notifica._reset()


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
    avisos = [
        r for r in caplog.records if r.name == "app.notifica" and r.levelno >= logging.WARNING
    ]
    assert avisos, "el fallo de envio deja WARNING en app.notifica"
    assert FAKE_BOT_TOKEN not in caplog.text
    assert FAKE_CHAT_ID not in caplog.text
    # T1: el `except` propio de `notifica_precio` (fallo armando) tambien
    # deja WARNING con scrub (sin log, el scrub se afirma en vacio).
    caplog.clear()

    def _romper(_grupo):
        raise RuntimeError(f"boom armando {FAKE_BOT_TOKEN}")

    monkeypatch.setattr(notifica, "aviso_precio_grupo", _romper)
    with (
        _canal_falso(tmp_path, monkeypatch),
        caplog.at_level(logging.WARNING, logger="app.notifica"),
    ):
        assert notifica.notifica_precio("frenado", grupo) is False
    armados = [
        r
        for r in caplog.records
        if r.name == "app.notifica"
        and r.levelno >= logging.WARNING
        and "fallo armando el aviso de precio" in r.getMessage()
    ]
    assert armados, "el fallo armando deja WARNING en app.notifica"
    assert FAKE_BOT_TOKEN not in caplog.text


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
        # B5: el estado tambien recorre prohibidos (jamas `goal_*` crudo).
        notifica.aviso_precio_producto(
            notifica.ProductoPrecio(
                "amazon_mx",
                "no_confirmado",
                "SKU-7",
                "B0PP000007",
                "116.00",
                "127.60",
                "MXN",
                "goal_inalcanzable",
                "cooldown",
            )
        ),
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
    # T2: la racha cuenta dias SEGUIDOS hasta hoy, no el total de presentes.
    con_cola = {hoy, hoy - timedelta(days=1), hoy - timedelta(days=2), hoy - timedelta(days=4)}
    assert notifica.flanco_umbral(con_cola, hoy, 3) is True
    con_hueco = {hoy, hoy - timedelta(days=2), hoy - timedelta(days=3)}
    assert notifica.flanco_umbral(con_hueco, hoy, 3) is False
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


def _resumen(huerfanas=0, decisiones=1):
    from types import SimpleNamespace

    return SimpleNamespace(huerfanas=huerfanas, decisiones=decisiones)


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
    "ads_ingest",  # ADS PROTECCION 01 A.3: salud de ingesta principal
)


@contextmanager
def _db_pantalla(sufijo=""):
    """Como _db_temp pero entrega (conn, dsn) para el TestClient de lectura
    (`sufijo` solo compat: el nombre ya es unico por uuid)."""
    with _db_nueva() as (conn, dsn):
        yield conn, dsn


@contextmanager
def _db_sin_0039():
    """DB sin la 0039 (sin tablas de precios): el camino de degradacion."""
    with _db_nueva(omit=("0039_precio.sql",)) as (conn, dsn):
        yield conn, dsn


def _config_pantalla(conn):
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        (
            "test-pantalla",
            Json(
                {
                    "precio_aviso_dias_sin_evaluar": 3,
                    "precio_catalogo_max_dias_sin_reportar": 7,
                    "precio_cap_amazon_mx": 7,
                    "precio_cap_amazon_us": 7,
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
        assert _MSG_SIN_MOTOR in caplog.text
        resp = _cliente_pantalla(dsn, monkeypatch).get("/api/dashboard/precios")
        assert resp.status_code != 500
        assert resp.status_code == 503
        assert resp.json()["detail"] == _MSG_SIN_MOTOR


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
        # B3 r2: el Estado va en palabras («subida», mismo mapa de la
        # pantalla), jamás el id crudo «subir».
        assert "subida" in html.lower()
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
        # A5: el bloque (e) exige la racha (N=3): se siembran los 3 dias.
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        try:
            for n in range(3):
                _r1b_noeval(conn, lid, motivo="precio_divergente", dia=_dias_atras(conn, n))
        finally:
            conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        html = _html_precios(conn, dsn, monkeypatch)
        seccion = html.split('id="bloque-divergente-amazon_mx"')[1].split("</section>")[0]
        assert "divergente" in seccion.lower() or "divergencia" in seccion.lower()
        assert "distinto del publicado" in seccion.lower()
        assert "SKU-UI-60" in seccion
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
        assert _MSG_SIN_MOTOR in resp.text


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
    # C (r1): dentro de la rama que llama a `correr`, no antes de `--reporte`.
    assert fuente.index("from app.notifica import avisar_precio") > fuente.index("args.reporte")


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
        with _canal_falso(tmp_path, monkeypatch, tumbar=True) as mensajes:
            assert (
                notifica.notifica_precio(
                    "frenado",
                    notifica.GrupoPrecio("amazon_mx", "frenado", "api_error", 1, ("SKU-CIERRE-2",)),
                )
                is False
            )
            res = _corre(conn, red, avisar=notifica.avisar_precio)
        # Telegram caido: hubo intento de envio y `res.errores` no trae `avisar`.
        assert len(mensajes) >= 1, "con Telegram caido hay intento de envio"
        assert all("avisar" not in e for e in res.errores)
        assert res.decisiones == 1
        assert conn.execute("SELECT count(*) FROM precio_decision").fetchone()[0] == 2


# --- fin bloque CIERRE (otros carriles anexan debajo) ---

# ---------------------------------------------------------------------------
# Bloque R1 (REPRICING 01, A.6 r1): A1, A2, B2, B4, B6, B7, T2-base, CMismatch.
# Corrida real (`correr` con `avisar=avisar_precio`, Telegram falso);
# ERROR_BODY real en `precio_cambio.ack` y afirmado ausente del texto.
# ---------------------------------------------------------------------------


def _r1_mediodia(dia):
    from datetime import UTC, datetime

    return datetime(dia.year, dia.month, dia.day, 12, tzinfo=UTC)


def _r1_decision_subir_live(conn, listing, *, platform="amazon_mx", dia):
    """`subir` live minima (como `_decision_subir_pantalla` pero live y con
    fecha explicita; exige el trigger de fecha apagado)."""
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, decision_date, resultado,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, mode)"
        " VALUES (%s, %s, %s, 'subir', 0.30, 0.24, 116, 'MXN', 116, 'MXN', 116, 'MXN',"
        " 100, 'MXN', 40, 'MXN', 15, 'MXN', 0, 'MXN', 2.50, 'MXN', 'live') RETURNING id",
        (listing, platform, dia),
    ).fetchone()[0]


def _r1_cambio_pendiente(
    conn,
    dec,
    listing,
    *,
    platform="amazon_mx",
    antes="110",
    despues="116",
    enviado_en=None,
    creado_en=None,
):
    """Cambio real nacido `pendiente` (con `enviado_at`/`created_at` puestos)."""
    cols = (
        "decision_id, listing_id, platform, precio_antes, precio_antes_currency,"
        " precio_despues, precio_despues_currency, aplicado, estado, enviado_at"
    )
    vals = (dec, listing, platform, antes, "MXN", despues, "MXN", True, "pendiente", enviado_en)
    if creado_en is not None:
        cols += ", created_at"
        vals += (creado_en,)
    return conn.execute(
        f"INSERT INTO precio_cambio ({cols}) VALUES (%s, %s, %s, %s, %s,"
        " %s, %s, %s, %s, %s" + (", %s" if creado_en is not None else "") + ") RETURNING id",
        vals,
    ).fetchone()[0]


def _r1_umbral(conn, dias=3):
    actual = dict(
        conn.execute("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1").fetchone()[0]
    )
    actual["precio_aviso_dias_sin_evaluar"] = dias
    conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s)",
        ("test-r1", Json(actual)),
    )


@_skip_sin_pg
def test_r1_a1_no_confirmado_avisa_en_corrida_real(tmp_path, monkeypatch):
    """A1: `enviado` de ayer + observacion de hoy distinta -> un aviso
    `no_confirmado` de ese SKU; un `no_confirmado` viejo (goal anterior)
    no calla el nuevo. ERROR_BODY real en `ack`, ausente del texto."""
    from test_precio_corrida import _cadena, _corre, _price_obs, _RedFalsa
    from test_precio_corrida import _goal as _goal_corrida

    from app.spapi.precio_write import cerrar_por_observacion

    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        datos = _cadena(conn, sku="SKU-A1", ext="B0A1000001", mode="live")
        lid = datos["listing"]
        hoy = _dias_atras(conn, 0)
        ayer = _dias_atras(conn, 1)
        hace31 = _dias_atras(conn, 31)
        hace30 = _dias_atras(conn, 30)
        hace60 = _dias_atras(conn, 60)
        # Goal anterior (cerrado) + `no_confirmado` viejo del mismo listing
        # (se cierra en la semilla: un solo cambio abierto por listing).
        _goal_corrida(conn, lid, platform="amazon_mx", mode="live", desde=hace60, hasta=hace31)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        dec_vieja = _r1_decision_subir_live(conn, lid, dia=hace31)
        cam_viejo = _r1_cambio_pendiente(
            conn, dec_vieja, lid, antes="100", enviado_en=_r1_mediodia(hace31)
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', ack = %s WHERE id = %s",
            (Json({"origen": "seed", "detalle": ERROR_BODY}), cam_viejo),
        )
        _price_obs(conn, asin="B0A1000001", precio="100", dia=hace30)
        cerrados = cerrar_por_observacion(conn, hoy)
        assert cerrados["no_confirmado"] == 1
        # Cambio de ayer, todavia `enviado`: lo cierra la corrida de hoy.
        dec_ayer = _r1_decision_subir_live(conn, lid, dia=ayer)
        cam_ayer = _r1_cambio_pendiente(
            conn, dec_ayer, lid, antes="110", enviado_en=_r1_mediodia(ayer)
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', ack = %s WHERE id = %s",
            (Json({"origen": "seed", "detalle": ERROR_BODY}), cam_ayer),
        )
        _price_obs(conn, asin="B0A1000001", precio="120", dia=hoy)
        conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        _r1_umbral(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red, avisar=notifica.avisar_precio)
        assert res.decisiones == 1
        fila = conn.execute(
            "SELECT resultado, motivo FROM precio_decision"
            " WHERE platform = 'amazon_mx' AND decision_date = %s",
            (hoy,),
        ).fetchone()
        assert fila == ("frenado", "no_confirmado")
        avisos_nc = [m for m in mensajes if "[Orbit] precio: no confirmado" in m["text"]]
        assert len(avisos_nc) == 1, [m["text"] for m in mensajes]
        assert "SKU-A1" in avisos_nc[0]["text"]
        assert "116" in avisos_nc[0]["text"]
        for m in mensajes:
            assert ERROR_BODY not in m["text"]


@_skip_sin_pg
def test_r1_a2_huerfana_es_evento_en_corrida_real(tmp_path, monkeypatch):
    """A2: `pendiente` con `created_at` de ayer -> un aviso; una huerfana
    vieja cerrada no calla la nueva; segunda corrida el mismo dia -> cero
    avisos de huerfana. ERROR_BODY real en `ack`, ausente del texto."""
    from test_precio_corrida import _cadena, _corre, _RedFalsa
    from test_precio_corrida import _goal as _goal_corrida
    from test_precio_corrida import _listing as _listing_corrida
    from test_precio_corrida import _producto as _producto_corrida

    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        datos = _cadena(conn, sku="SKU-A2-1", ext="B0A2000001")
        hoy = _dias_atras(conn, 0)
        ayer = _dias_atras(conn, 1)
        hace31 = _dias_atras(conn, 31)
        hace30 = _dias_atras(conn, 30)
        hace60 = _dias_atras(conn, 60)
        # Listing 2: `subir` live de hoy + `pendiente` nacida ayer.
        prod2 = _producto_corrida(conn, sku="ODOO-SKU-A2-2")
        lid2 = _listing_corrida(conn, prod2, ext="B0A2000002", sku="SKU-A2-2")
        _goal_corrida(conn, lid2, platform="amazon_mx", mode="live")
        dec2 = _r1_decision_subir_live(conn, lid2, dia=hoy)
        pen2 = _r1_cambio_pendiente(conn, dec2, lid2, creado_en=_r1_mediodia(ayer))
        conn.execute(
            "UPDATE precio_cambio SET ack = %s WHERE id = %s",
            (Json({"origen": "seed", "detalle": ERROR_BODY}), pen2),
        )
        # Historia: huerfana vieja ya cerrada (no calla la nueva).
        prod3 = _producto_corrida(conn, sku="ODOO-SKU-A2-3")
        lid3 = _listing_corrida(conn, prod3, ext="B0A2000003", sku="SKU-A2-3")
        _goal_corrida(conn, lid3, platform="amazon_mx", mode="live", desde=hace60, hasta=hace31)
        _goal_corrida(conn, lid3, platform="amazon_mx", mode="live")
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        dec3 = _r1_decision_subir_live(conn, lid3, dia=hace31)
        pen3 = _r1_cambio_pendiente(conn, dec3, lid3, creado_en=_r1_mediodia(hace30))
        conn.execute(
            "UPDATE precio_cambio SET estado = 'error', error_code = 'huerfana_sin_patch'"
            " WHERE id = %s",
            (pen3,),
        )
        conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        _r1_umbral(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res = _corre(conn, red, avisar=notifica.avisar_precio)
        assert res.decisiones >= 1
        assert res.huerfanas == 1
        avisos_h = [m for m in mensajes if "huerfanas sin parche" in m["text"]]
        assert len(avisos_h) == 1, [m["text"] for m in mensajes]
        assert "amazon_mx" in avisos_h[0]["text"]
        for m in mensajes:
            assert ERROR_BODY not in m["text"]
        # Segunda corrida el mismo dia -> cero avisos nuevos.
        antes = len(mensajes)
        res2 = _corre(conn, red, avisar=notifica.avisar_precio)
        assert res2.decisiones == 0
        assert len(mensajes) == antes


@_skip_sin_pg
def test_r1_b2_reejecucion_mismo_dia_no_reenvia(tmp_path, monkeypatch):
    """B2: dos `correr` seguidos el mismo dia -> la segunda manda cero
    (la primera deja un aviso de grupo que sin la guarda se reenviaria)."""
    from test_precio_corrida import _cadena, _corre, _price_obs, _RedFalsa

    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        datos = _cadena(conn, sku="SKU-B2", ext="B0B2000001", mode="live")
        lid = datos["listing"]
        hoy = _dias_atras(conn, 0)
        ayer = _dias_atras(conn, 1)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        dec_ayer = _r1_decision_subir_live(conn, lid, dia=ayer)
        cam_ayer = _r1_cambio_pendiente(
            conn, dec_ayer, lid, antes="110", enviado_en=_r1_mediodia(ayer)
        )
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', ack = %s WHERE id = %s",
            (Json({"origen": "seed", "detalle": ERROR_BODY}), cam_ayer),
        )
        _price_obs(conn, asin="B0B2000001", precio="120", dia=hoy)
        conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        _r1_umbral(conn)
        red = _RedFalsa(skus={datos["evento"]: datos["sku"]})
        res1 = _corre(conn, red, avisar=notifica.avisar_precio)
        assert res1.decisiones == 1
        assert len(mensajes) >= 1
        antes = len(mensajes)
        res2 = _corre(conn, red, avisar=notifica.avisar_precio)
        assert res2.decisiones == 0
        assert len(mensajes) == antes
        for m in mensajes:
            assert ERROR_BODY not in m["text"]


@_skip_sin_pg
def test_r1_b4_umbral_invalido_solo_apaga_no_evaluado(tmp_path, monkeypatch, caplog):
    """B4: clave ausente/invalida -> sin aviso `no_evaluado` (warning) pero
    los demas tipos salen igual."""
    import logging as _logging

    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s)",
            ("test-b4", Json({})),
        )
        prod = _producto(conn)
        lid1 = _listing(conn, prod, sku="SKU-B4-1", ext="B0B4000001")
        lid2 = _listing(conn, prod, sku="SKU-B4-2", ext="B0B4000002")
        _goal(conn, lid1)
        _goal(conn, lid2)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        hoy = _dias_atras(conn, 0)
        _decision(conn, lid1, resultado="frenado", motivo="api_error", dia=hoy)
        for n in range(3):
            _decision(conn, lid2, dia=_dias_atras(conn, n), motivo="precio_sin_observar")
        with caplog.at_level(_logging.WARNING, logger="app.notifica"):
            enviados = notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen())
        assert enviados == 1
        assert len(mensajes) == 1
        assert "[Orbit] precio: frenados" in mensajes[0]["text"]
        assert "precio_aviso_dias_sin_evaluar" in caplog.text


@_skip_sin_pg
def test_r1_b6_un_tipo_roto_no_calla_los_otros(tmp_path, monkeypatch, caplog):
    """B6: un `try` por tipo; si los grupos fallan, la huerfana sale igual."""
    import logging as _logging

    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)

        def _grupos_rotos(_conn, _platform, _dia):
            raise RuntimeError("boom grupos")

        monkeypatch.setattr(notifica, "_grupos_precio_dia", _grupos_rotos)
        with caplog.at_level(_logging.WARNING, logger="app.notifica"):
            enviados = notifica.avisar_precio(
                conn, "amazon_mx", _dias_atras(conn, 0), _resumen(huerfanas=2)
            )
        assert enviados == 1
        assert len(mensajes) == 1
        assert "huerfanas sin parche" in mensajes[0]["text"]
        assert "boom grupos" not in mensajes[0]["text"]


@_skip_sin_pg
def test_r1_t2_hueco_en_base_no_avisa(tmp_path, monkeypatch):
    """T2: `{hoy, hoy-2, hoy-3}` con umbral 3 no avisa (hay hueco)."""
    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        prod = _producto(conn)
        lid = _listing(conn, prod, sku="SKU-T2", ext="B0T2000001")
        _goal(conn, lid)
        for n in (0, 2, 3):
            _decision(conn, lid, dia=_dias_atras(conn, n), motivo="precio_sin_observar")
        hoy = _dias_atras(conn, 0)
        assert notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen()) == 0
        assert mensajes == []


def test_r1_c_payload_con_otro_tipo_no_envia(tmp_path, monkeypatch, caplog):
    """C: `payload.tipo != tipo` -> warning + False (ni siquiera con el
    canal apagado sale)."""
    import logging as _logging

    grupo = notifica.GrupoPrecio("amazon_mx", "no_evaluado", "precio_sin_observar", 1, ("S",))
    with caplog.at_level(_logging.WARNING, logger="app.notifica"):
        assert notifica.notifica_precio("frenado", grupo) is False
    assert any(r.name == "app.notifica" and r.levelno >= _logging.WARNING for r in caplog.records)


# --- fin bloque R1 (otros carriles anexan debajo) ---

# ---------------------------------------------------------------------------
# Bloque R1-b (REPRICING 01, A.6 r1): A3, A4, A5, B1, B9, T3 + B12 de pantalla.
# Base real (ORBIT_TEST_DSN). Otros carriles anexan debajo: no borrar.
# ---------------------------------------------------------------------------


def _r1b_cambio_real(conn, dec, listing, *, antes, despues, estado="enviado", platform="amazon_mx"):
    """Cambio real (aplicado) no-reversa: nace `pendiente` (lo exige el
    trigger de nacimiento) y pasa a `enviado` con ack si se pide."""
    cam = conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado) VALUES (%s, %s, %s, %s, 'MXN', %s, 'MXN', true, 'pendiente')"
        " RETURNING id",
        (dec, listing, platform, antes, despues),
    ).fetchone()[0]
    if estado == "enviado":
        conn.execute(
            "UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(), ack = %s"
            " WHERE id = %s",
            (Json({"origen": "seed"}), cam),
        )
    return cam


def _r1b_cambio_virtual(conn, dec, listing, *, antes, despues, platform="amazon_mx"):
    """Cambio virtual de sombra (aplicado=false, nace cerrado con
    `enviado_at` puesto y sin ack)."""
    conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado, confirmado_por, enviado_at)"
        " VALUES (%s, %s, %s, %s, 'MXN', %s, 'MXN', false, 'confirmado', 'virtual', now())",
        (dec, listing, platform, antes, despues),
    )


def _r1b_subir_shadow(conn, listing, *, platform="amazon_mx", antes="116", despues="127.60"):
    """`subir` shadow con `p_aplicado` = precio que se habria aplicado (el
    trigger de coherencia exige `precio_despues = p_aplicado`)."""
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, mode)"
        " VALUES (%s, %s, 'subir', 0.30, 0.24, %s, 'MXN', %s, 'MXN', %s, 'MXN',"
        " 100, 'MXN', 40, 'MXN', 15, 'MXN', 0, 'MXN', 2.50, 'MXN', 'shadow')"
        " RETURNING id",
        (listing, platform, antes, despues, despues),
    ).fetchone()[0]


def _r1b_bajar_live(conn, listing, *, platform="amazon_mx", p_actual="127.60"):
    """`bajar` live con cuenta completa (el trigger fija goal y fecha)."""
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, mode)"
        " VALUES (%s, %s, 'bajar', 0.30, 0.24, %s, 'MXN', 116, 'MXN', 116, 'MXN',"
        " 100, 'MXN', 40, 'MXN', 15, 'MXN', 0, 'MXN', 2.50, 'MXN', 'live') RETURNING id",
        (listing, platform, p_actual),
    ).fetchone()[0]


def _r1b_noeval(conn, listing, *, motivo, dia=None, p_actual=None, platform="amazon_mx"):
    """`no_evaluado` con `p_actual` opcional (el trigger fija la fecha si no
    se apaga; con `dia` hay que apagarlo fuera)."""
    if dia is None:
        dia = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, decision_date, resultado,"
        " motivo, p_actual, p_actual_currency, p_objetivo_currency, p_aplicado_currency,"
        " i_currency, c_currency, f_currency, l_currency, r_currency, mode)"
        " VALUES (%s, %s, %s, 'no_evaluado', %s, %s,"
        " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'shadow')"
        " RETURNING id",
        (listing, platform, dia, motivo, p_actual),
    ).fetchone()[0]


def _r1b_config_sin_caps(conn):
    """Settings con las claves de dias pero SIN `precio_cap_*` (camino B9)."""
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        (
            "test-r1b-sin-caps",
            Json(
                {
                    "precio_aviso_dias_sin_evaluar": 3,
                    "precio_catalogo_max_dias_sin_reportar": 7,
                }
            ),
        ),
    ).fetchone()[0]


def _r1b_siembra_a3(conn):
    """Fixture A3: cada conteo distinto de cero y entre si (evaluados=8,
    movidos=3, sombra=2, goal_inalcanzable=1, huerfanas=4, used=5, cap=7)."""
    from test_precio_corrida import _goal as _goal_corrida

    _config_pantalla(conn)
    hoy = _dias_atras(conn, 0)
    ayer = _dias_atras(conn, 1)
    prod = _producto(conn)
    # 3x subir live de hoy con cambio real no-reversa (movidos).
    for i in (1, 2, 3):
        lid = _listing(conn, prod, sku=f"SKU-A3-L{i}", ext=f"B0A30000{i}")
        _goal_live_pantalla(conn, lid)
        dec = _r1_decision_subir_live(conn, lid, dia=hoy)
        _r1b_cambio_real(conn, dec, lid, antes="110.00", despues="116.00")
    # 2x subir shadow de hoy (sombra).
    for i in (4, 5):
        lid = _listing(conn, prod, sku=f"SKU-A3-S{i}", ext=f"B0A30000{i}")
        _goal(conn, lid)
        dec = _r1b_subir_shadow(conn, lid)
        _r1b_cambio_virtual(conn, dec, lid, antes="116.00", despues="127.60")
    # 1x goal_inalcanzable de hoy.
    lid = _listing(conn, prod, sku="SKU-A3-G", ext="B0A300006")
    _goal(conn, lid)
    _decision(conn, lid, resultado="goal_inalcanzable", motivo="sobre_goal_sin_perdida")
    # 2x no_evaluado de hoy (uno por motivo).
    for sku, ext, motivo in (
        ("SKU-A3-N1", "B0A300007", "precio_sin_observar"),
        ("SKU-A3-N2", "B0A300008", "moneda_divergente"),
    ):
        lid = _listing(conn, prod, sku=sku, ext=ext)
        _goal(conn, lid)
        _decision(conn, lid, motivo=motivo)
    # 2x frenado de hoy con el mismo motivo.
    for sku, ext in (("SKU-A3-F1", "B0A300009"), ("SKU-A3-F2", "B0A300010")):
        lid = _listing(conn, prod, sku=sku, ext=ext)
        _goal(conn, lid)
        _decision(conn, lid, resultado="frenado", motivo="api_error")
    # 4x huerfana pendiente de ayer (no cuentan como movidos: otro dia).
    conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
    try:
        for i in (1, 2, 3, 4):
            lid = _listing(conn, prod, sku=f"SKU-A3-H{i}", ext=f"B0A30001{i}")
            _goal_corrida(conn, lid, platform="amazon_mx", mode="live")
            dec = _r1_decision_subir_live(conn, lid, dia=ayer)
            conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado) VALUES (%s, %s, 'amazon_mx', 110, 'MXN', 116, 'MXN',"
                " true, 'pendiente')",
                (dec, lid),
            )
    finally:
        conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
    # Cuota del dia: used=5 de cap=7.
    conn.execute(
        "INSERT INTO apply_quota_state (motor, quota_date, cap, used)"
        " VALUES ('precio:amazon_mx', %s, 7, 5)",
        (hoy,),
    )
    return hoy


@_skip_sin_pg
def test_r1b_a3_salud_precios_clave_por_clave_vs_select():
    """A3: cada clave de `precios` contra un SELECT directo, todo distinto
    de cero y entre si; `cobertura` es la misma serializacion que /precios."""
    with _db_temp() as conn:
        hoy = _r1b_siembra_a3(conn)
        salud = dash.salud(conn)["plataformas"]["amazon_mx"]["precios"]
        json_precios = dash.precios(conn)["plataformas"]["amazon_mx"]
        assert salud["hoy"] == hoy.isoformat()
        assert salud["cobertura"] == json_precios["recuadro"]
        assert salud["evaluados"] == 8
        assert (
            salud["evaluados"]
            == conn.execute(
                "SELECT count(*) FROM precio_decision WHERE platform = 'amazon_mx'"
                " AND decision_date = %s AND resultado IN ('subir', 'bajar', 'mantener',"
                " 'frenado', 'goal_inalcanzable')",
                (hoy,),
            ).fetchone()[0]
        )
        assert salud["movidos"] == 3
        assert (
            salud["movidos"]
            == conn.execute(
                "SELECT count(*) FROM precio_decision d WHERE d.platform = 'amazon_mx'"
                " AND d.decision_date = %s AND d.mode = 'live' AND EXISTS (SELECT 1"
                " FROM precio_cambio c WHERE c.decision_id = d.id AND c.aplicado"
                " AND NOT c.es_reversa)",
                (hoy,),
            ).fetchone()[0]
        )
        assert salud["sombra"] == 2
        assert (
            salud["sombra"]
            == conn.execute(
                "SELECT count(*) FROM precio_decision WHERE platform = 'amazon_mx'"
                " AND decision_date = %s AND mode = 'shadow'"
                " AND resultado IN ('subir', 'bajar')",
                (hoy,),
            ).fetchone()[0]
        )
        assert salud["no_evaluados"] == {
            "precio_sin_observar": 1,
            "moneda_divergente": 1,
        }
        assert salud["no_evaluados"] == {
            fila[0]: fila[1]
            for fila in conn.execute(
                "SELECT motivo, count(*) FROM precio_decision"
                " WHERE platform = 'amazon_mx' AND decision_date = %s"
                " AND resultado = 'no_evaluado' GROUP BY motivo",
                (hoy,),
            ).fetchall()
        }
        assert salud["frenados"] == {"api_error": 2}
        assert salud["frenados"] == {
            fila[0]: fila[1]
            for fila in conn.execute(
                "SELECT motivo, count(*) FROM precio_decision"
                " WHERE platform = 'amazon_mx' AND decision_date = %s"
                " AND resultado = 'frenado' GROUP BY motivo",
                (hoy,),
            ).fetchall()
        }
        assert salud["goal_inalcanzable"] == 1
        assert (
            salud["goal_inalcanzable"]
            == conn.execute(
                "SELECT count(*) FROM precio_decision WHERE platform = 'amazon_mx'"
                " AND decision_date = %s AND resultado = 'goal_inalcanzable'",
                (hoy,),
            ).fetchone()[0]
        )
        assert salud["cuota"] == {"used": 5, "cap": 7}
        assert (
            salud["cuota"]["used"]
            == conn.execute(
                "SELECT used FROM apply_quota_state"
                " WHERE motor = 'precio:amazon_mx' AND quota_date = %s",
                (hoy,),
            ).fetchone()[0]
        )
        assert salud["huerfanas"] == 4
        assert salud["huerfanas"] == _select_huerfanas(conn, "amazon_mx")
        assert salud["decisiones"] == _select_decisiones(conn, "amazon_mx", hoy)
        assert (
            len(
                {
                    salud["evaluados"],
                    salud["movidos"],
                    salud["sombra"],
                    salud["goal_inalcanzable"],
                    salud["huerfanas"],
                    salud["cuota"]["used"],
                    salud["cuota"]["cap"],
                }
            )
            == 7
        )


@_skip_sin_pg
def test_r1b_a4_con_goal_y_acciones_frases_exactas():
    """A4: una `shadow` que sube y una `live` que baja con su cambio real:
    frases exactas, la de `live` sin «habría»."""
    with _db_temp() as conn:
        _config_pantalla(conn)
        prod = _producto(conn)
        lid_s = _listing(conn, prod, sku="SKU-A4-S", ext="B0A400001")
        _goal(conn, lid_s)
        dec_s = _r1b_subir_shadow(conn, lid_s)
        _r1b_cambio_virtual(conn, dec_s, lid_s, antes="116.00", despues="127.60")
        lid_b = _listing(conn, prod, sku="SKU-A4-B", ext="B0A400002")
        _goal_live_pantalla(conn, lid_b)
        dec_b = _r1b_bajar_live(conn, lid_b)
        _r1b_cambio_real(conn, dec_b, lid_b, antes="127.60", despues="116.00")
        bloque = dash.precios(conn)["plataformas"]["amazon_mx"]
        frases = {a["sku"]: a["frase"] for a in bloque["acciones"]}
        assert frases["SKU-A4-S"] == "SKU-A4-S habría subido de 116.00 a 127.60 MXN"
        assert frases["SKU-A4-B"] == "SKU-A4-B bajó de 127.60 a 116.00 MXN"
        assert "habría" not in frases["SKU-A4-B"]
        filas = {f["sku"]: f for f in bloque["con_goal"]}
        assert set(filas) == {"SKU-A4-S", "SKU-A4-B"}
        assert filas["SKU-A4-B"]["ultimo_cambio"]["estado"] == "enviado"
        assert filas["SKU-A4-B"]["ultimo_cambio"]["antes"] == "127.6000"
        assert filas["SKU-A4-B"]["ultimo_cambio"]["despues"] == "116.0000"
        assert filas["SKU-A4-S"]["modo"] == "shadow"
        assert filas["SKU-A4-B"]["modo"] == "live"


@_skip_sin_pg
def test_r1b_a5_divergente_racha_de_tres():
    """A5: con N=3, 3 dias seguidos entra; 2 dias no; 3 cortados por un dia
    evaluado tampoco. `moneda_divergente` no va aqui."""
    with _db_temp() as conn:
        _config_pantalla(conn)
        dias = [_dias_atras(conn, n) for n in range(4)]
        prod = _producto(conn)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        try:
            lid_a = _listing(conn, prod, sku="SKU-A5-A", ext="B0A500001")
            _goal(conn, lid_a)
            for dia in dias[:3]:
                _r1b_noeval(conn, lid_a, motivo="precio_divergente", dia=dia, p_actual="116")
            lid_b = _listing(conn, prod, sku="SKU-A5-B", ext="B0A500002")
            _goal(conn, lid_b)
            for dia in dias[:2]:
                _r1b_noeval(conn, lid_b, motivo="precio_divergente", dia=dia)
            lid_c = _listing(conn, prod, sku="SKU-A5-C", ext="B0A500003")
            _goal(conn, lid_c)
            _r1b_noeval(conn, lid_c, motivo="precio_divergente", dia=dias[0])
            _r1b_noeval(conn, lid_c, motivo="precio_divergente", dia=dias[1])
            _decision(conn, lid_c, resultado="mantener", motivo="en_tolerancia", dia=dias[2])
            _r1b_noeval(conn, lid_c, motivo="precio_divergente", dia=dias[3])
            lid_m = _listing(conn, prod, sku="SKU-A5-M", ext="B0A500004")
            _goal(conn, lid_m)
            for dia in dias[:3]:
                _r1b_noeval(conn, lid_m, motivo="moneda_divergente", dia=dia)
        finally:
            conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        divs = dash.precios(conn)["plataformas"]["amazon_mx"]["divergentes"]
        assert [d["sku"] for d in divs] == ["SKU-A5-A"]
        assert divs[0]["dias"] == 3
        assert divs[0]["p_actual"] == "116.0000"
        assert divs[0]["moneda"] == "MXN"


@_skip_sin_pg
def test_r1b_b1_no_evaluados_filas_del_dia_en_palabras():
    """B1: (d) sale de las decisiones `no_evaluado` de hoy (una fila por
    SKU, motivo en palabras), aunque el listing no este en el recuadro."""
    with _db_temp() as conn:
        _config_pantalla(conn)
        prod = _producto(conn)
        lid1 = _listing(conn, prod, sku="SKU-B1-1", ext="B0B100001")
        _goal(conn, lid1)
        _decision(conn, lid1, motivo="precio_sin_observar")
        lid2 = _listing(conn, prod, sku="SKU-B1-2", ext="B0B100002")
        _goal(conn, lid2)
        _decision(conn, lid2, motivo="moneda_divergente")
        lid3 = _listing(conn, prod, sku="SKU-B1-3", ext="B0B100003")
        _goal(conn, lid3)
        _decision_subir_pantalla(conn, lid3)
        bloque = dash.precios(conn)["plataformas"]["amazon_mx"]
        filas = {f["sku"]: f for f in bloque["no_evaluados_filas"]}
        assert set(filas) == {"SKU-B1-1", "SKU-B1-2"}
        assert filas["SKU-B1-1"]["motivo_es"] == "sin observacion de precio del dia"
        assert filas["SKU-B1-2"]["motivo_es"] == "moneda distinta entre observacion y escenario"
        for f in filas.values():
            assert f["motivo"] in ("precio_sin_observar", "moneda_divergente")
        assert bloque["no_evaluados"] == {"precio_sin_observar": 1, "moneda_divergente": 1}


@_skip_sin_pg
def test_r1b_b9_errores_pantalla_tres_casos(monkeypatch, caplog):
    """B9: tres mensajes exactos; la plantilla no nombra la migracion en
    ninguno de los tres."""
    import logging as _logging

    # 1. Sin 0039: el mensaje del motor no instalado.
    with _db_sin_0039() as (_conn, dsn):
        cliente = _cliente_pantalla(dsn, monkeypatch)
        resp = cliente.get("/api/dashboard/precios")
        assert resp.status_code == 503
        assert resp.json()["detail"] == _MSG_SIN_MOTOR
        html = cliente.get("/precios").text
        assert _MSG_SIN_MOTOR in html
        assert "migracion 0039" not in html and "migración 0039" not in html
        # B12: /salud por TestClient con la base sin 0039: 200 y None.
        salud = cliente.get("/api/dashboard/salud").json()
        for plataforma in ("amazon_us", "amazon_mx"):
            assert salud["plataformas"][plataforma]["precios"] is None
    # 2. Clave de config ausente: el mensaje nombra la clave (con scrub y
    # registro); la plantilla tampoco nombra la migracion.
    with _db_pantalla("b9-config") as (_conn, dsn):
        _r1b_config_sin_caps(_conn)
        cliente = _cliente_pantalla(dsn, monkeypatch)
        with caplog.at_level(_logging.WARNING, logger="app.api_dashboard"):
            resp = cliente.get("/api/dashboard/precios")
        assert resp.status_code == 503
        assert "precio_cap_amazon_us" in resp.json()["detail"]
        assert "precio_cap_amazon_us" in caplog.text
        html = cliente.get("/precios").text
        assert resp.status_code == 503
        assert "precio_cap_amazon_us" in html
        assert "migracion 0039" not in html and "migración 0039" not in html
    # 3. Cualquier otro fallo: «precios de <plataforma> ilegibles».
    with _db_pantalla("b9-otro") as (conn, dsn):
        _config_pantalla(conn)
        import app.precio.fuentes as _fuentes

        real = _fuentes.leer_publicaciones

        def _rota(conn_lectura, *, platform, hoy):
            if platform == "amazon_mx":
                raise RuntimeError("boom puente roto")
            return real(conn_lectura, platform=platform, hoy=hoy)

        monkeypatch.setattr(_fuentes, "leer_publicaciones", _rota)
        cliente = _cliente_pantalla(dsn, monkeypatch)
        resp = cliente.get("/api/dashboard/precios")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "precios de amazon_mx ilegibles"
        html = cliente.get("/precios").text
        assert "precios de amazon_mx ilegibles" in html
        assert "migracion 0039" not in html and "migración 0039" not in html


@_skip_sin_pg
def test_r1b_t3_puente_bajo_5_sin_aviso_y_frase_por_fila(monkeypatch):
    """T3: con puente == activas no hay aviso; por fila, la `live` no dice
    «habría» y la `shadow` sí."""
    with _db_pantalla("t3-bajo5") as (conn, dsn):
        _config_pantalla(conn)
        prod = _producto(conn)
        lid_s = _listing(conn, prod, sku="SKU-T3-S", ext="B0T300001")
        _estado_pantalla(conn, sku="SKU-T3-S")
        _oferta_pantalla(conn, lid_s, sku="SKU-T3-S", ext="B0T300001")
        _goal(conn, lid_s)
        dec_s = _r1b_subir_shadow(conn, lid_s)
        _r1b_cambio_virtual(conn, dec_s, lid_s, antes="116.00", despues="127.60")
        lid_b = _listing(conn, prod, sku="SKU-T3-B", ext="B0T300002")
        _estado_pantalla(conn, sku="SKU-T3-B")
        _oferta_pantalla(conn, lid_b, sku="SKU-T3-B", ext="B0T300002")
        _goal_live_pantalla(conn, lid_b)
        dec_b = _r1b_bajar_live(conn, lid_b)
        _r1b_cambio_real(conn, dec_b, lid_b, antes="127.60", despues="116.00")
        html = _html_precios(conn, dsn, monkeypatch)
        assert "aviso: puente" not in html
        seccion = html.split('id="bloque-hecho-amazon_mx"')[1].split("</section>")[0]
        assert "SKU-T3-S habría bajado" not in seccion
        assert "SKU-T3-S habría subido de 116.00 a 127.60 MXN" in seccion
        assert "SKU-T3-B bajó de 127.60 a 116.00 MXN" in seccion
        fila_b = seccion.split("SKU-T3-B")[1].split("</li>")[0]
        assert "habría" not in fila_b


@_skip_sin_pg
def test_r1b_b12_ecuacion_con_no_evaluadas_y_fuera():
    """B12: la ecuacion cuadra con `no_evaluadas` y `fuera_de_alcance`
    distintos de cero."""
    with _db_temp() as conn:
        _config_pantalla(conn)
        prod = _producto(conn)
        lid1 = _listing(conn, prod, sku="SKU-B12-1", ext="B0B120001")
        _estado_pantalla(conn, sku="SKU-B12-1")
        _oferta_pantalla(conn, lid1, sku="SKU-B12-1", ext="B0B120001")
        _goal(conn, lid1)
        _decision_subir_pantalla(conn, lid1)
        lid2 = _listing(conn, prod, sku="SKU-B12-2", ext="B0B120002")
        _estado_pantalla(conn, sku="SKU-B12-2")
        _oferta_pantalla(conn, lid2, sku="SKU-B12-2", ext="B0B120002")
        _goal(conn, lid2)
        _decision(conn, lid2, motivo="precio_sin_observar")
        lid3 = _listing(conn, prod, sku="SKU-B12-3", ext="B0B120003")
        _estado_pantalla(conn, sku="SKU-B12-3")
        _oferta_pantalla(conn, lid3, sku="SKU-B12-3", ext="B0B120003", canal="fbm")
        rec = dash.precios(conn)["plataformas"]["amazon_mx"]["recuadro"]
        assert rec["activas"] == 3
        assert sum(n["count"] for n in rec["no_evaluadas"]) == 1
        assert sum(n["count"] for n in rec["fuera_de_alcance"]) == 1
        assert rec["activas"] == (
            rec["evaluadas"]
            + sum(n["count"] for n in rec["no_evaluadas"])
            + len(rec["sin_goal"])
            + sum(n["count"] for n in rec["fuera_de_alcance"])
        )


def test_r1b_b12_sin_consultas_duplicadas_en_ui():
    """B12: el chequeo de consultas duplicadas tambien sobre `app/ui.py`."""
    for ruta in ("app/api_dashboard.py", "app/ui.py"):
        fuente = (RAIZ / ruta).read_text(encoding="utf-8")
        for tabla in ("spapi_listing_estado_observation", "estimacion_oferta_observation"):
            assert tabla not in fuente, f"consulta duplicada en {ruta}: {tabla}"


@_skip_sin_pg
def test_r1b_b12_cero_cdn_absoluto_en_precios(monkeypatch):
    """B12: cero CDN como ningun `src`/`href` con `http(s)://` absoluto."""
    import re

    with _db_pantalla("b12-cdn") as (conn, dsn):
        _config_pantalla(conn)
        html = _html_precios(conn, dsn, monkeypatch)
        assert not re.findall(r"(?:src|href)=\"https?://", html)


@_skip_sin_pg
def test_r1b_b12_salud_html_bloque_precios_con_numeros(monkeypatch):
    """B12: el bloque de `/salud` HTML trae sus numeros (cuota incluida)."""
    with _db_pantalla("b12-salud") as (conn, dsn):
        _config_pantalla(conn)
        hoy = _dias_atras(conn, 0)
        prod = _producto(conn)
        lid = _listing(conn, prod, sku="SKU-B12-H", ext="B0B120009")
        _goal(conn, lid)
        _decision(conn, lid, resultado="frenado", motivo="api_error")
        conn.execute(
            "INSERT INTO apply_quota_state (motor, quota_date, cap, used)"
            " VALUES ('precio:amazon_mx', %s, 7, 2)",
            (hoy,),
        )
        resp = _cliente_pantalla(dsn, monkeypatch).get("/salud")
        assert resp.status_code == 200
        seccion = resp.text.split('id="bloque-precios-amazon_mx"')[1].split("</div>")[0]
        assert "frenados 1" in seccion
        assert "cupo 2 de 7" in seccion


@_skip_sin_pg
def test_r1b_c_barra_dice_precios_como_el_titulo(monkeypatch):
    """C: la barra dice «Precios», igual que el titulo de la pantalla."""
    with _db_pantalla("b12-barra") as (conn, dsn):
        _config_pantalla(conn)
        html = _html_precios(conn, dsn, monkeypatch)
        assert ">Precios</a>" in html
        assert "<h1>Precios</h1>" in html


# --- fin bloque R1-b (otros carriles anexan debajo) ---

# ---------------------------------------------------------------------------
# Bloque R1-c (REPRICING 01, A.6 r1): B3, B5, B10, B11 + C.
# Base real (ORBIT_TEST_DSN) donde aplica. Otros carriles anexan debajo.
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_r1_b3_buybox_7_avisos_5_mas_agrupado(tmp_path, monkeypatch):
    """B3: 7 buy box perdidas -> 5 avisos por producto + UN agrupado de ese
    tipo con conteo y hasta 5 SKUs (6 mensajes, no 7 ni 429)."""
    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)
        prod = _producto(conn)
        hoy = _dias_atras(conn, 0)
        for i in range(1, 8):
            lid = _listing(conn, prod, sku=f"SKU-B3-{i:02d}", ext=f"B0B30000{i:02d}")
            _goal(conn, lid)
            _decision(
                conn,
                lid,
                resultado="mantener",
                motivo="en_tolerancia",
                dia=hoy,
                buy_box=False,
            )
        enviados = notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen())
        assert enviados == 6
        assert len(mensajes) == 6
        agrupados = [m for m in mensajes if "\nskus:" in m["text"]]
        assert len(agrupados) == 1
        assert "total: 2" in agrupados[0]["text"]
        assert "SKU-B3-06" in agrupados[0]["text"]
        assert "SKU-B3-07" in agrupados[0]["text"]
        individuales = [m for m in mensajes if "\nskus:" not in m["text"]]
        assert len(individuales) == 5


def test_r1_b5_estado_en_palabras_y_asin_solo_amazon():
    """B5: el `estado` va en palabras (jamas `goal_inalcanzable` crudo: el
    texto no dice «goal»); la etiqueta `asin` solo sale en Amazon."""
    crudo = notifica.aviso_precio_producto(
        notifica.ProductoPrecio(
            "amazon_mx",
            "buy_box_perdida",
            "SKU-5",
            "B05",
            None,
            "110.00",
            "MXN",
            "goal_inalcanzable",
            "buy_box_perdida",
        )
    )
    assert "goal_inalcanzable" not in crudo
    assert "goal" not in crudo.lower()
    assert "objetivo inalcanzable" in crudo
    assert "asin: B05" in crudo
    meli = notifica.aviso_precio_producto(
        notifica.ProductoPrecio(
            "meli",
            "buy_box_perdida",
            "SKU-6",
            "MLM6",
            None,
            "110.00",
            "MXN",
            "frenado",
            "buy_box_perdida",
        )
    )
    assert "asin:" not in meli
    assert "MLM6" in meli


@_skip_sin_pg
def test_r1_b11_helper_unico_nombre_corto_y_migracion_omitida():
    """B11: helper unico con nombre uuid corto (<= 63 bytes de PG) y
    parametro de que migracion omitir."""
    with _db_nueva() as (conn, dsn):
        nombre = urlsplit(dsn).path.lstrip("/")
        assert len(nombre) <= 63
        assert (
            conn.execute("SELECT to_regclass('precio_decision')").fetchone()[0] == "precio_decision"
        )
    with _db_nueva(omit=("0039_precio.sql",)) as (conn2, _dsn2):
        assert conn2.execute("SELECT to_regclass('precio_decision')").fetchone()[0] is None


# --- fin bloque R1-c (otros carriles anexan debajo) ---

# ---------------------------------------------------------------------------
# Bloque R2 (REPRICING 01, A.6 r2): L1, L2, L14, L15, R2, R3, R4.
# Base real (ORBIT_TEST_DSN), Telegram falso. Otros carriles anexan debajo.
# ---------------------------------------------------------------------------


def _r2_decision_live(conn, listing, resultado, motivo, dia, *, platform="amazon_mx"):
    """Decision live con fecha explicita (exige el trigger de fecha apagado);
    `frenado`/`goal_inalcanzable` con motivo (el CHECK lo exige fuera de
    subir/bajar)."""
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, decision_date, resultado,"
        " motivo, p_actual_currency, p_objetivo_currency, p_aplicado_currency,"
        " i_currency, c_currency, f_currency, l_currency, r_currency, mode)"
        " VALUES (%s, %s, %s, %s, %s,"
        " 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'live')"
        " RETURNING id",
        (listing, platform, dia, resultado, motivo),
    ).fetchone()[0]


def _r2_decision_subir_live(conn, listing, *, platform="amazon_mx", dia, p_aplicado):
    """`subir` live con `p_aplicado` dado (el trigger de coherencia exige
    `precio_despues = p_aplicado` en su cambio)."""
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, decision_date, resultado,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, mode)"
        " VALUES (%s, %s, %s, 'subir', 0.30, 0.24, %s, 'MXN', %s, 'MXN', %s, 'MXN',"
        " 100, 'MXN', 40, 'MXN', 15, 'MXN', 0, 'MXN', 2.50, 'MXN', 'live') RETURNING id",
        (listing, platform, dia, p_aplicado, p_aplicado, p_aplicado),
    ).fetchone()[0]


def _r2_cambio_no_confirmado(conn, dec, listing, *, antes, despues, enviado_en):
    """Cambio real `no_confirmado`: nace `pendiente` y sigue la progresion
    sellada (`pendiente -> enviado -> no_confirmado`, como haria el cierre
    por observacion)."""
    cam = _r1_cambio_pendiente(
        conn, dec, listing, antes=antes, despues=despues, enviado_en=enviado_en
    )
    conn.execute(
        "UPDATE precio_cambio SET estado = 'enviado', ack = %s WHERE id = %s",
        (Json({"origen": "seed-r2"}), cam),
    )
    conn.execute(
        "UPDATE precio_cambio SET estado = 'no_confirmado', confirmado_por = 'observacion'"
        " WHERE id = %s",
        (cam,),
    )
    return cam


@_skip_sin_pg
def test_r2_l1_frenado_api_error_no_da_aviso_no_confirmado(tmp_path, monkeypatch):
    """L1: un `frenado(api_error)` de hoy no da aviso `no_confirmado` aunque
    el listing tenga un cambio real `no_confirmado` en su historia (el
    conjunto es solo `frenado` con motivo `no_confirmado`)."""
    from test_precio_corrida import _goal as _goal_corrida

    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)
        prod = _producto(conn, sku="SKU-R2L1")
        lid = _listing(conn, prod, sku="SKU-R2L1", ext="B0R2L10001")
        hoy = _dias_atras(conn, 0)
        ayer = _dias_atras(conn, 1)
        _goal_corrida(conn, lid, platform="amazon_mx", mode="live", desde=_dias_atras(conn, 60))
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        dec_old = _r1_decision_subir_live(conn, lid, dia=ayer)
        _r2_cambio_no_confirmado(
            conn, dec_old, lid, antes="110", despues="116", enviado_en=_r1_mediodia(ayer)
        )
        _r2_decision_live(conn, lid, "frenado", "api_error", hoy)
        conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        enviados = notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen())
        assert enviados == 1
        assert len(mensajes) == 1
        assert "[Orbit] precio: frenados" in mensajes[0]["text"]
        assert all("[Orbit] precio: no confirmado" not in m["text"] for m in mensajes)


@_skip_sin_pg
def test_r2_l2_frenado_noconf_ayer_y_hoy_cero_avisos(tmp_path, monkeypatch):
    """L2: `frenado(no_confirmado)` ayer y hoy -> cero avisos por producto
    (la racha sigue); el cambio `no_confirmado` existe para que el mutante
    `sorted(nc_hoy)` si avisaria."""
    from test_precio_corrida import _goal as _goal_corrida

    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)
        prod = _producto(conn, sku="SKU-R2L2")
        lid = _listing(conn, prod, sku="SKU-R2L2", ext="B0R2L20001")
        hoy = _dias_atras(conn, 0)
        ayer = _dias_atras(conn, 1)
        hace2 = _dias_atras(conn, 2)
        _goal_corrida(conn, lid, platform="amazon_mx", mode="live", desde=_dias_atras(conn, 60))
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        dec_old = _r1_decision_subir_live(conn, lid, dia=hace2)
        _r2_cambio_no_confirmado(
            conn, dec_old, lid, antes="110", despues="116", enviado_en=_r1_mediodia(hace2)
        )
        _r2_decision_live(conn, lid, "frenado", "no_confirmado", ayer)
        _r2_decision_live(conn, lid, "frenado", "no_confirmado", hoy)
        conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        enviados = notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen())
        assert enviados == 0
        assert mensajes == []


@_skip_sin_pg
def test_r2_l14_grupo_presente_ayer_y_hoy_no_reavisa(tmp_path, monkeypatch):
    """L14: un grupo `goal_inalcanzable` o `frenado` presente ayer y hoy no
    reavisa, por `avisar_precio` con base (el flanco es por grupo nuevo)."""
    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)
        prod = _producto(conn, sku="SKU-R2L14")
        lid1 = _listing(conn, prod, sku="SKU-R2L14-1", ext="B0R2L14001")
        lid2 = _listing(conn, prod, sku="SKU-R2L14-2", ext="B0R2L14002")
        _goal(conn, lid1)
        _goal(conn, lid2)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        hoy = _dias_atras(conn, 0)
        ayer = _dias_atras(conn, 1)
        _decision(conn, lid1, resultado="goal_inalcanzable", motivo="fee_no_lineal", dia=ayer)
        _decision(conn, lid1, resultado="goal_inalcanzable", motivo="fee_no_lineal", dia=hoy)
        _decision(conn, lid2, resultado="frenado", motivo="api_error", dia=ayer)
        _decision(conn, lid2, resultado="frenado", motivo="api_error", dia=hoy)
        conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        enviados = notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen())
        assert enviados == 0
        assert mensajes == []


@_skip_sin_pg
def test_r2_l15_aviso_lleva_precios_del_no_confirmado_nuevo(tmp_path, monkeypatch):
    """L15: con un `no_confirmado` viejo y uno nuevo de precios distintos,
    el aviso lleva los del nuevo (el ultimo por id, no el primero)."""
    from test_precio_corrida import _goal as _goal_corrida

    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)
        prod = _producto(conn, sku="SKU-R2L15")
        lid = _listing(conn, prod, sku="SKU-R2L15", ext="B0R2L15001")
        hoy = _dias_atras(conn, 0)
        ayer = _dias_atras(conn, 1)
        hace30 = _dias_atras(conn, 30)
        _goal_corrida(conn, lid, platform="amazon_mx", mode="live", desde=_dias_atras(conn, 60))
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        dec_vieja = _r2_decision_subir_live(conn, lid, dia=hace30, p_aplicado="110")
        cam_viejo = _r2_cambio_no_confirmado(
            conn,
            dec_vieja,
            lid,
            antes="100",
            despues="110",
            enviado_en=_r1_mediodia(hace30),
        )
        dec_nueva = _r2_decision_subir_live(conn, lid, dia=ayer, p_aplicado="210")
        cam_nuevo = _r2_cambio_no_confirmado(
            conn,
            dec_nueva,
            lid,
            antes="200",
            despues="210",
            enviado_en=_r1_mediodia(ayer),
        )
        assert cam_nuevo > cam_viejo
        _r2_decision_live(conn, lid, "frenado", "no_confirmado", hoy)
        conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        enviados = notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen())
        avisos = [m for m in mensajes if "[Orbit] precio: no confirmado" in m["text"]]
        assert len(avisos) == 1, [m["text"] for m in mensajes]
        assert enviados >= 1
        assert "200" in avisos[0]["text"] and "210" in avisos[0]["text"]
        assert "100" not in avisos[0]["text"] and "110" not in avisos[0]["text"]


def test_r2_r2_api_usa_validador_compartido_de_notifica():
    """R2: `app/api_dashboard.py` usa `validar_precio_aviso_dias` de
    `app/notifica.py` (la copia local `_dias_aviso_desde_settings` no
    existe)."""
    assert not hasattr(dash, "_dias_aviso_desde_settings")
    assert dash.validar_precio_aviso_dias is notifica.validar_precio_aviso_dias


@_skip_sin_pg
def test_r2_r2_umbral_de_config_ausente_nombra_la_clave(monkeypatch):
    """R2: el cableado usa el validador compartido: sin
    `precio_aviso_dias_sin_evaluar` en la config, la pantalla 503 y nombra
    la clave (un umbral fijo en codigo pasaria de largo)."""
    with _db_pantalla("r2-umbral") as (conn, dsn):
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES (%s, %s)",
            (
                "test-r2",
                Json(
                    {
                        "precio_catalogo_max_dias_sin_reportar": 7,
                        "precio_cap_amazon_mx": 7,
                        "precio_cap_amazon_us": 7,
                    }
                ),
            ),
        )
        resp = _cliente_pantalla(dsn, monkeypatch).get("/api/dashboard/precios")
        assert resp.status_code == 503
        assert "precio_aviso_dias_sin_evaluar" in resp.json()["detail"]


@_skip_sin_pg
def test_r2_r3_buybox_una_pieza_rota_no_calla_las_otras(tmp_path, monkeypatch, caplog):
    """R3: en `_avisar_buybox_precio` cada pieza va en su `try`: una que
    levanta al armarse no calla las demas (sale 1 aviso, no 0)."""
    import logging as _logging

    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)
        prod = _producto(conn, sku="SKU-R2R3")
        lid1 = _listing(conn, prod, sku="SKU-R2R3-01", ext="B0R2R30001")
        lid2 = _listing(conn, prod, sku="SKU-R2R3-02", ext="B0R2R30002")
        _goal(conn, lid1)
        _goal(conn, lid2)
        hoy = _dias_atras(conn, 0)
        _decision(conn, lid1, resultado="mantener", motivo="en_tolerancia", dia=hoy, buy_box=False)
        _decision(conn, lid2, resultado="mantener", motivo="en_tolerancia", dia=hoy, buy_box=False)
        real = notifica.ProductoPrecio

        class _PiezaRota(real):
            def __init__(self, *args, **kwargs):
                sku = args[2] if len(args) >= 3 else kwargs.get("sku")
                if sku == "SKU-R2R3-01":
                    raise RuntimeError("boom pieza r3")
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(notifica, "ProductoPrecio", _PiezaRota)
        with caplog.at_level(_logging.WARNING, logger="app.notifica"):
            enviados = notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen())
        assert enviados == 1
        assert len(mensajes) == 1
        assert "SKU-R2R3-02" in mensajes[0]["text"]
        assert "boom pieza r3" not in mensajes[0]["text"]


def test_r2_r4_avisar_precio_docstring_declara_residuo_segunda_corrida():
    """R4: el docstring de `avisar_precio` declara el residuo de la segunda
    corrida del mismo dia que persiste decisiones nuevas (reenvia todo)."""
    doc = notifica.avisar_precio.__doc__ or ""
    assert "segunda corrida" in doc
    assert "reenv" in doc


# --- fin bloque R2 (otros carriles anexan debajo) ---

# ---------------------------------------------------------------------------
# Bloque R2-b (REPRICING 01, A.6 r2): A1, L8, B1, B2, B3, B4.
# Base real (ORBIT_TEST_DSN) donde aplica; A1 va directo contra
# `_acciones_precio` (repro del lead, sin PG). Otros carriles anexan debajo.
# ---------------------------------------------------------------------------


def _r2b_fila_subir_live() -> dict:
    """Detalle fabricado `subir`/`live` (repro A1 del lead)."""
    return {
        "id": 1,
        "listing_id": 7,
        "sku": "SKU-1",
        "resultado": "subir",
        "mode": "live",
        "motivo": None,
        "m_actual": "0.24",
        "goal": "0.30",
        "p_actual": "116.00",
        "p_actual_currency": "MXN",
        "p_objetivo": "127.60",
        "p_objetivo_currency": "MXN",
        "p_aplicado": "127.60",
        "canal": "fba",
    }


def _r2b_cambio(estado: str) -> dict:
    """Ultimo cambio real no-reversa fabricado en el estado pedido."""
    return {
        1: {
            "antes": "116.00",
            "antes_moneda": "MXN",
            "despues": "127.60",
            "despues_moneda": "MXN",
            "real": True,
            "estado": estado,
            "fecha": "2026-09-18",
        }
    }


def test_r2b_a1_live_sin_cambio_no_dice_subio():
    """A1: `live` sin cambio real: «decidió subir ...; sin cambio aplicado»
    (nunca «subió»: nada se movió)."""
    (accion,) = dash._acciones_precio([_r2b_fila_subir_live()], {}, "amazon_mx")
    assert accion["frase"] == "SKU-1 decidió subir de 116.00 a 127.60 MXN; sin cambio aplicado"
    assert "subió" not in accion["frase"]


def test_r2b_a1_live_cambio_pendiente_dice_pendiente():
    """A1: `live` con el cambio `pendiente`: la frase lo dice."""
    (accion,) = dash._acciones_precio(
        [_r2b_fila_subir_live()], _r2b_cambio("pendiente"), "amazon_mx"
    )
    assert accion["frase"] == "SKU-1 subió de 116.00 a 127.60 MXN; pendiente de confirmación"


def test_r2b_a1_live_cambio_error_como_esta():
    """A1: `live` con el cambio en `error`: la frase queda como ya estaba."""
    (accion,) = dash._acciones_precio([_r2b_fila_subir_live()], _r2b_cambio("error"), "amazon_mx")
    assert accion["frase"] == "SKU-1 subió de 116.00 a 127.60 MXN; el cambio quedó en error"


def _r2b_bajar_shadow(conn, listing, *, platform="amazon_mx"):
    """`bajar` shadow con cuenta completa (el trigger fija goal y fecha)."""
    return conn.execute(
        "INSERT INTO precio_decision (listing_id, platform, resultado,"
        " goal, m_actual, p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,"
        " p_aplicado, p_aplicado_currency, i_valor, i_currency, c_valor, c_currency,"
        " f_valor, f_currency, l_valor, l_currency, r_valor, r_currency, mode)"
        " VALUES (%s, %s, 'bajar', 0.30, 0.24, 127.60, 'MXN', 116, 'MXN', 116, 'MXN',"
        " 100, 'MXN', 40, 'MXN', 15, 'MXN', 0, 'MXN', 2.50, 'MXN', 'shadow') RETURNING id",
        (listing, platform),
    ).fetchone()[0]


@_skip_sin_pg
def test_r2b_l8_shadow_que_baja_cuenta_en_sombra():
    """L8: una `shadow` que baja cuenta en `sombra` (no solo la que sube)."""
    with _db_temp() as conn:
        _config_pantalla(conn)
        prod = _producto(conn)
        lid = _listing(conn, prod, sku="SKU-R2B-L8", ext="B0R2B80001")
        _goal(conn, lid)
        dec = _r2b_bajar_shadow(conn, lid)
        _r1b_cambio_virtual(conn, dec, lid, antes="127.60", despues="116.00")
        hoy = _dias_atras(conn, 0)
        salud = dash.salud(conn)["plataformas"]["amazon_mx"]["precios"]
        assert salud["sombra"] == 1
        assert (
            salud["sombra"]
            == conn.execute(
                "SELECT count(*) FROM precio_decision WHERE platform = 'amazon_mx'"
                " AND decision_date = %s AND mode = 'shadow'"
                " AND resultado IN ('subir', 'bajar')",
                (hoy,),
            ).fetchone()[0]
        )
        frases = {
            a["sku"]: a["frase"] for a in dash.precios(conn)["plataformas"]["amazon_mx"]["acciones"]
        }
        assert frases["SKU-R2B-L8"] == "SKU-R2B-L8 habría bajado de 127.60 a 116.00 MXN"


def test_r2b_b1_porcentaje_filtro_con_numero_del_fixture():
    """B1: el filtro propio pinta el tanto por uno en porcentaje."""
    from app import ui

    assert ui.porcentaje_ui("0.2150") == "21.50 %"
    assert ui.porcentaje_ui(None) is None
    assert ui.porcentaje_ui("roto") == "roto"


@_skip_sin_pg
def test_r2b_b1_margen_y_goal_en_porcentaje_en_html(monkeypatch):
    """B1: «Margen hoy» y «Objetivo» van en porcentaje (no como dinero)."""
    with _db_pantalla("r2b-b1") as (conn, dsn):
        _config_pantalla(conn)
        prod = _producto(conn)
        lid = _listing(conn, prod, sku="SKU-R2B-B1", ext="B0R2B10001")
        _goal(conn, lid)
        _decision_subir_pantalla(conn, lid)
        html = _html_precios(conn, dsn, monkeypatch)
        seccion = html.split('id="bloque-con-goal-amazon_mx"')[1].split("</section>")[0]
        assert "24.00 %" in seccion
        assert "25.00 %" in seccion
        assert "0.24" not in seccion
        assert "0.25" not in seccion


def test_r2b_b2_docstring_movidos_declara_patch_aceptado():
    """B2: el docstring de `movidos` dice que hubo PATCH aceptado."""
    assert "PATCH aceptado" in (dash._precios_de.__doc__ or "")


@_skip_sin_pg
def test_r2b_b2_movidos_solo_con_patch_aceptado():
    """B2: `movidos` solo cuenta `enviado`/`confirmado`/`no_confirmado`: un
    cambio real en `error` (o `pendiente`) no movió el precio."""
    with _db_temp() as conn:
        _config_pantalla(conn)
        prod = _producto(conn)
        lid_e = _listing(conn, prod, sku="SKU-R2B-B2E", ext="B0R2B20001")
        _goal_live_pantalla(conn, lid_e)
        dec_e = _r1b_bajar_live(conn, lid_e)
        cam_e = _r1_cambio_pendiente(conn, dec_e, lid_e, antes="127.60", despues="116")
        conn.execute(
            "UPDATE precio_cambio SET estado = 'error', error_code = 'patch_rechazado'"
            " WHERE id = %s",
            (cam_e,),
        )
        lid_p = _listing(conn, prod, sku="SKU-R2B-B2P", ext="B0R2B20002")
        _goal_live_pantalla(conn, lid_p)
        dec_p = _r1b_bajar_live(conn, lid_p)
        _r1_cambio_pendiente(conn, dec_p, lid_p, antes="127.60", despues="116")
        lid_ok = _listing(conn, prod, sku="SKU-R2B-B2K", ext="B0R2B20003")
        _goal_live_pantalla(conn, lid_ok)
        dec_ok = _r1b_bajar_live(conn, lid_ok)
        _r1b_cambio_real(conn, dec_ok, lid_ok, antes="127.60", despues="116.00")
        salud = dash.salud(conn)["plataformas"]["amazon_mx"]["precios"]
        assert salud["movidos"] == 1


@_skip_sin_pg
def test_r2b_b3_estado_en_palabras_con_mismo_mapa(monkeypatch):
    """B3: la columna Estado dice «objetivo inalcanzable» / «no evaluado»
    (mismo mapa de la pantalla), jamás los ids crudos."""
    with _db_pantalla("r2b-b3") as (conn, dsn):
        _config_pantalla(conn)
        prod = _producto(conn)
        lid_g = _listing(conn, prod, sku="SKU-R2B-B3G", ext="B0R2B30001")
        _goal(conn, lid_g)
        _decision(conn, lid_g, resultado="goal_inalcanzable", motivo="sobre_goal_sin_perdida")
        lid_n = _listing(conn, prod, sku="SKU-R2B-B3N", ext="B0R2B30002")
        _goal(conn, lid_n)
        _decision(conn, lid_n, motivo="precio_sin_observar")
        html = _html_precios(conn, dsn, monkeypatch)
        seccion = html.split('id="bloque-con-goal-amazon_mx"')[1].split("</section>")[0]
        assert "objetivo inalcanzable" in seccion
        assert "no evaluado" in seccion
        assert "goal_inalcanzable" not in seccion
        assert "no_evaluado" not in seccion


@_skip_sin_pg
def test_r2b_b4_divergente_racha_real_y_umbral_en_texto(monkeypatch):
    """B4: el bloque (e) cuenta la racha real (5 dias, no el umbral 3) con
    tope declarado, y el texto escribe el número del umbral."""
    with _db_pantalla("r2b-b4") as (conn, dsn):
        _config_pantalla(conn)
        dias = [_dias_atras(conn, n) for n in range(31)]
        prod = _producto(conn)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        try:
            lid = _listing(conn, prod, sku="SKU-R2B-B4", ext="B0R2B40001")
            _goal(conn, lid)
            for dia in dias[:5]:
                _r1b_noeval(conn, lid, motivo="precio_divergente", dia=dia, p_actual="116")
            lid_t = _listing(conn, prod, sku="SKU-R2B-B4T", ext="B0R2B40002")
            _goal(conn, lid_t)
            for dia in dias[:31]:
                _r1b_noeval(conn, lid_t, motivo="precio_divergente", dia=dia, p_actual="116")
        finally:
            conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        bloque = dash.precios(conn)["plataformas"]["amazon_mx"]
        divs = {d["sku"]: d for d in bloque["divergentes"]}
        assert divs["SKU-R2B-B4"]["dias"] == 5
        assert divs["SKU-R2B-B4T"]["dias"] == dash._TOPE_RACHA_DIVERGENTE
        html = _html_precios(conn, dsn, monkeypatch)
        seccion = html.split('id="bloque-divergente-amazon_mx"')[1].split("</section>")[0]
        assert "3 días seguidos" in seccion
        assert "N días" not in seccion


# --- fin bloque R2-b (otros carriles anexan debajo) ---


def _r3_fila_bajar_live() -> dict:
    """Detalle fabricado `bajar`/`live` sin cambio real (N2)."""
    fila = _r2b_fila_subir_live()
    # Sin cambio real, el «a» de la frase sale de `p_aplicado`.
    fila.update(
        {
            "resultado": "bajar",
            "p_actual": "127.60",
            "p_objetivo": "116.00",
            "p_aplicado": "116.00",
        }
    )
    return fila


def test_r3_n2_live_bajar_sin_cambio_no_dice_bajo():
    """N2: `live` que baja sin cambio real: «decidió bajar ...; sin cambio
    aplicado» (nunca «bajó»: nada se movió)."""
    (accion,) = dash._acciones_precio([_r3_fila_bajar_live()], {}, "amazon_mx")
    assert accion["frase"] == "SKU-1 decidió bajar de 127.60 a 116.00 MXN; sin cambio aplicado"
    assert "bajó" not in accion["frase"]


# ---------------------------------------------------------------------------
# Bloque R1-b2 (REPRICING 01, Fase 11 r1-cierre-r2): M1, M17 y M58 del lead.
# Solo tests (app/ intacto). Evidencia:
# docs/evidencia/repricing-01/R.1/bis-r2.md. Otros carriles anexan debajo.
# ---------------------------------------------------------------------------


@_skip_sin_pg
def test_r1b2_m1_racha_que_sigue_no_reavisa(tmp_path, monkeypatch):
    """M1: grupo `no_evaluado(precio_sin_observar)` presente 4 dias seguidos
    (hoy-3..hoy): `avisar_precio` manda 0 avisos (la racha que sigue no
    reavisa); contra la consulta real, no la funcion pura."""
    with _db_temp() as conn, _canal_falso(tmp_path, monkeypatch) as mensajes:
        _config(conn)
        conn.execute("ALTER TABLE precio_decision DISABLE TRIGGER precio_decision_fecha_utc")
        try:
            prod = _producto(conn)
            lid = _listing(conn, prod)
            _goal(conn, lid)
            for n in range(4):
                _decision(conn, lid, dia=_dias_atras(conn, n), motivo="precio_sin_observar")
        finally:
            conn.execute("ALTER TABLE precio_decision ENABLE TRIGGER precio_decision_fecha_utc")
        hoy = _dias_atras(conn, 0)
        assert notifica.avisar_precio(conn, "amazon_mx", hoy, _resumen()) == 0
        assert mensajes == []


@_skip_sin_pg
def test_r1b2_m17_salud_huerfana_reversa_no_cuenta():
    """M17: en /salud, un cambio pendiente con `es_reversa` no cuenta como
    huerfana (0); uno no-reversa si (1)."""
    with _db_temp() as conn:
        _config_pantalla(conn)
        prod = _producto(conn)
        # Un solo cambio abierto por listing: la reversa va en uno y la
        # pendiente comun en otro.
        lid1 = _listing(conn, prod, sku="SKU-R1B2-M17R", ext="B0R1B20017")
        _goal_live_pantalla(conn, lid1)
        dec1 = _decision_subir_pantalla(conn, lid1, mode="live")
        orig = _r1b_cambio_real(conn, dec1, lid1, antes="110.00", despues="116.00")
        conn.execute(
            "UPDATE precio_cambio SET estado = 'confirmado', confirmado_por = 'observacion'"
            " WHERE id = %s",
            (orig,),
        )
        conn.execute(
            "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, es_reversa, reversa_de)"
            " VALUES (%s, 'amazon_mx', 116.00, 'MXN', 110.00, 'MXN', true,"
            " 'pendiente', true, %s)",
            (lid1, orig),
        )
        precios = dash.salud(conn)["plataformas"]["amazon_mx"]["precios"]
        assert precios["huerfanas"] == 0
        assert precios["huerfanas"] == _select_huerfanas(conn, "amazon_mx")
        lid2 = _listing(conn, prod, sku="SKU-R1B2-M17P", ext="B0R1B20018")
        _goal_live_pantalla(conn, lid2)
        dec2 = _decision_subir_pantalla(conn, lid2, mode="live")
        _r1_cambio_pendiente(conn, dec2, lid2, antes="110", despues="116")
        precios = dash.salud(conn)["plataformas"]["amazon_mx"]["precios"]
        assert precios["huerfanas"] == 1
        assert precios["huerfanas"] == _select_huerfanas(conn, "amazon_mx")


@_skip_sin_pg
def test_r1b2_m58_goal_anulado_hoy_fuera_de_con_goal():
    """M58: un goal con `valid_from = valid_to = hoy` no sale en `con_goal`
    de /precios; el vigente si."""
    with _db_temp() as conn:
        _config_pantalla(conn)
        hoy = _dias_atras(conn, 0)
        prod = _producto(conn)
        lid_ok = _listing(conn, prod, sku="SKU-R1B2-M58K", ext="B0R1B20058")
        _goal(conn, lid_ok)
        lid_an = _listing(conn, prod, sku="SKU-R1B2-M58X", ext="B0R1B20059")
        conn.execute(
            "INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,"
            " valid_from, valid_to, creado_por)"
            " VALUES (%s, 'amazon_mx', '0.25', 'shadow', %s, %s, 'test-r1b2')",
            (lid_an, hoy, hoy),
        )
        filas = dash.precios(conn)["plataformas"]["amazon_mx"]["con_goal"]
        assert [f["sku"] for f in filas] == ["SKU-R1B2-M58K"]
