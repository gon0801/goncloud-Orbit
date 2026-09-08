"""Tests de A.6: API GET + pantalla /reputacion (acta 0.5 §6).

(a) API: resumen por listing (rating, count, tendencia, estado de
    fuente), cuenta MeLi, preguntas pendientes, alertas abiertas,
    reviews recientes; vacio no rompe; URLs solo allowlist.
(b) UI: /reputacion 200 server-rendered; texto externo escapado
    (autoescape Jinja, cero JS inline); sidebar con enlace y sin chip
    Reviews; ausencia visible; digest texto plano fijado.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from test_reputacion import _skip_db, db_reputacion
from test_reputacion_alertas import FETCH, HOY, _review, _seller, _snap

from app.api import _conexion_lectura
from app.main import app

DIA = dt.timedelta(days=1)


def _pregunta(conn, item_id, qid, estado):
    conn.execute(
        "INSERT INTO meli_question"
        " (external_id, question_external_id, estado, fetched_at)"
        " VALUES (%s, %s, %s, %s)",
        (item_id, qid, estado, FETCH),
    )


def _alerta(conn, tipo, sev, platform, ext, mensaje, resolved=False):
    conn.execute(
        "INSERT INTO reputation_alert"
        " (platform, external_id, tipo, severidad, mensaje, resolved, resolved_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END)",
        (platform, ext, tipo, sev, mensaje, resolved, resolved),
    )


@_skip_db
def test_api_resumen_listings_con_tendencia():
    with db_reputacion("orbit_repui") as (conn, _dsn):
        _snap(conn, "meli", "MLM1", HOY - 7 * DIA, 4.8)
        _snap(conn, "meli", "MLM1", HOY, 4.5)
        _snap(conn, "amazon_mx", "B0X", HOY - 7 * DIA, 4.9)
        _snap(conn, "amazon_mx", "B0X", HOY, 4.9)
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            resp = TestClient(app).get("/api/reputacion/resumen")
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)
        assert resp.status_code == 200
        por_id = {li["external_id"]: li for li in resp.json()["listings"]}
        assert por_id["MLM1"]["rating"] == 4.5
        assert por_id["MLM1"]["tendencia"] == "baja"
        assert por_id["MLM1"]["texto"] == "sin-reviews"
        assert por_id["MLM1"]["url"] is None
        assert por_id["B0X"]["tendencia"] == "estable"
        assert por_id["B0X"]["texto"] == "sin-verificar"
        assert por_id["B0X"]["url"] == "https://www.amazon.com.mx/dp/B0X"


@_skip_db
def test_api_preguntas_distinct_por_pregunta():
    """Reviewer A.6 minor-1: N filas misma pregunta -> 1 (re-observacion
    diaria 0026; el lector toma la mas reciente)."""
    with db_reputacion("orbit_repui") as (conn, _dsn):
        _pregunta(conn, "MLM1", "Q1", "UNANSWERED")
        _pregunta(conn, "MLM1", "Q1", "UNANSWERED")
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            resp = TestClient(app).get("/api/reputacion/resumen")
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)
        pendientes = resp.json()["preguntas_pendientes"]
        assert [p["question_external_id"] for p in pendientes] == ["Q1"]


@_skip_db
def test_api_resumen_cuenta_pendientes_alertas():
    with db_reputacion("orbit_repui") as (conn, _dsn):
        _seller(conn, HOY, "5_green", 3)
        _pregunta(conn, "MLM1", "Q1", "UNANSWERED")
        _pregunta(conn, "MLM1", "Q2", "ANSWERED")
        _alerta(conn, "rating_bajo", "aviso", "meli", "MLM1", "rating 4.1 < 4.2")
        _alerta(conn, "rating_bajo", "aviso", "meli", "MLM9", "vieja", resolved=True)
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            resp = TestClient(app).get("/api/reputacion/resumen")
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)
        cuerpo = resp.json()
        assert cuerpo["cuenta_meli"]["level_id"] == "5_green"
        assert [p["question_external_id"] for p in cuerpo["preguntas_pendientes"]] == ["Q1"]
        assert [a["mensaje"] for a in cuerpo["alertas_abiertas"]] == ["rating 4.1 < 4.2"]
        assert cuerpo["amazon_texto"] == "sin-verificar"


@_skip_db
def test_api_resumen_reviews_distinct_y_json_crudo():
    with db_reputacion("orbit_repui") as (conn, _dsn):
        _review(conn, "MLM1", "R1", 1, True, FETCH - DIA)
        conn.execute(
            "INSERT INTO review_event"
            " (platform, external_id, review_external_id, rating, titulo, publicada,"
            "  fetched_at, observed_at)"
            " VALUES ('meli', 'MLM1', 'R1', 1, %s, TRUE, %s, %s)",
            ("<script>alert(1)</script>", FETCH, FETCH),
        )
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            resp = TestClient(app).get("/api/reputacion/resumen")
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)
        reviews = resp.json()["reviews_recientes"]
        assert len(reviews) == 1  # DISTINCT ON: N filas -> 1 review
        # La API devuelve JSON crudo (string intacto); el escape es del template.
        assert reviews[0]["titulo"] == "<script>alert(1)</script>"


@_skip_db
def test_api_resumen_vacio_no_rompe():
    with db_reputacion("orbit_repui") as (conn, _dsn):
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            resp = TestClient(app).get("/api/reputacion/resumen")
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)
        assert resp.status_code == 200
        cuerpo = resp.json()
        assert cuerpo["listings"] == []
        assert cuerpo["cuenta_meli"] is None
        assert cuerpo["preguntas_pendientes"] == []
        assert cuerpo["alertas_abiertas"] == []


# ---------------------------------------------------------------------------
# Pantalla /reputacion (server-rendered, autoescape, cero JS)
# ---------------------------------------------------------------------------


def _get_pagina(conn):
    app.dependency_overrides[_conexion_lectura] = lambda: conn
    try:
        return TestClient(app).get("/reputacion")
    finally:
        app.dependency_overrides.pop(_conexion_lectura, None)


@_skip_db
def test_pagina_reputacion_200_y_secciones():
    with db_reputacion("orbit_repui") as (conn, _dsn):
        _snap(conn, "meli", "MLM1", HOY, 4.5)
        _seller(conn, HOY, "5_green", 3)
        _pregunta(conn, "MLM1", "Q1", "UNANSWERED")
        _alerta(conn, "rating_bajo", "aviso", "meli", "MLM1", "rating 4.1 < 4.2")
        _review(conn, "MLM1", "R1", 5, True, FETCH)
        resp = _get_pagina(conn)
        assert resp.status_code == 200
        html = resp.text
        for seccion in ("Listings", "Reviews", "Preguntas", "Alertas", "Cuenta"):
            assert seccion in html
        assert "MLM1" in html and "5_green" in html and "Q1" in html


@_skip_db
def test_pagina_escapa_texto_externo():
    with db_reputacion("orbit_repui") as (conn, _dsn):
        conn.execute(
            "INSERT INTO review_event"
            " (platform, external_id, review_external_id, rating, titulo, publicada,"
            "  fetched_at, observed_at)"
            " VALUES ('meli', 'MLM1', 'RX', 1, %s, TRUE, %s, %s)",
            ("<script>alert(1)</script>", FETCH, FETCH),
        )
        html = _get_pagina(conn).text
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        # Blindaje CSP: cero scripts inline (los del layout traen src
        # estatico /static, permitido por default-src 'self').
        import re

        inlines = [s for s in re.findall(r"<script[^>]*>", html) if "src=" not in s]
        assert inlines == []


@_skip_db
def test_pagina_sin_texto_ni_datos_no_rompe():
    with db_reputacion("orbit_repui") as (conn, _dsn):
        resp = _get_pagina(conn)
        assert resp.status_code == 200
        assert "Sin datos" in resp.text
        assert "Sin verificar" in resp.text


@_skip_db
def test_sidebar_reputacion_enlace_y_sin_chip_reviews():
    with db_reputacion("orbit_repui") as (conn, _dsn):
        html = _get_pagina(conn).text
        assert 'href="/reputacion"' in html
        assert "Reviews <span" not in html
        assert "Repricing" in html  # sigue "pronto", no se toco de mas


@_skip_db
def test_pagina_sin_img_y_enlaces_allowlist():
    import re

    with db_reputacion("orbit_repui") as (conn, _dsn):
        _snap(conn, "amazon_mx", "B0X", HOY, 4.9)
        html = _get_pagina(conn).text
        assert "<img" not in html  # imagenes fuera de v1
        assert 'href="http://' not in html
        for href in re.findall(r'href="(https://[^"]+)"', html):
            assert href.startswith(
                ("https://www.amazon.com.mx/dp/", "https://www.amazon.com/dp/")
            ), href


def test_digest_texto_plano_fija_contrato():
    """Telegram sin parse_mode: el marcado viaja literal, no interpreta.

    DoD A.6 "escape segun parse_mode": con parse_mode ausente el
    escape correcto es ninguno; este test fija el contrato display.
    """
    from app.notifica import digest_ciclo

    texto = digest_ciclo(
        {
            "cycle_id": 1,
            "plataforma": "meli",
            "status": "ok",
            "decisions_count": 0,
            "apply": {},
            "reputacion": [
                {
                    "tipo": "rating_bajo",
                    "severidad": "aviso",
                    "platform": "meli",
                    "external_id": "MLM1",
                    "mensaje": "<b>4.1</b>",
                }
            ],
        }
    )
    assert "<b>4.1</b>" in texto


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
