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
def test_pregunta_respondida_no_pendiente_ni_total():
    """Kimi A.R H2: pregunta respondida (ultima fila ANSWERED) no
    reaparece por su fila vieja UNANSWERED; el total baja."""
    with db_reputacion("orbit_repui") as (conn, _dsn):
        _pregunta(conn, "MLM1", "Q9", "UNANSWERED")
        _pregunta(conn, "MLM1", "Q9", "ANSWERED")
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            resp = TestClient(app).get("/api/reputacion/resumen")
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)
        cuerpo = resp.json()
        assert cuerpo["preguntas_pendientes"] == []
        assert cuerpo["total_pendientes"] == 0


@_skip_db
def test_reviews_recientes_solo_publicadas():
    """Kimi A.R H3: review moderada no sale como verificada."""
    with db_reputacion("orbit_repui") as (conn, _dsn):
        _review(conn, "MLM1", "R9", 5, True, FETCH - DIA)
        _review(conn, "MLM1", "R9", 5, False, FETCH)
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            resp = TestClient(app).get("/api/reputacion/resumen")
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)
        assert resp.json()["reviews_recientes"] == []


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


# ---------------------------------------------------------------------------
# Rediseno /reputacion (handoff Orbit UI 2026-09-08): 4 KPIs, alertas
# abiertas primero, listings con estrellas y chips, filtros GET, contador
# de alertas en el sidebar. Mismos datos de carga_resumen, sin quitar.
# ---------------------------------------------------------------------------


@_skip_db
def test_api_resumen_agrega_kpis_y_severidades():
    with db_reputacion("orbit_repui2") as (conn, _dsn):
        conn.execute(
            "INSERT INTO reputation_snapshot"
            " (platform, external_id, alcance, metric_date, rating, review_count, fetched_at)"
            " VALUES ('meli', 'MLM1', 'listing', %s, 4.5, 400, %s),"
            " ('meli', 'MLM2', 'listing', %s, 4.7, 800, %s),"
            " ('amazon_mx', 'B0X', 'listing', %s, 4.9, 10, %s)",
            (HOY, FETCH, HOY, FETCH, HOY, FETCH),
        )
        _alerta(conn, "caida_rating", "critica", "meli", "MLM1", "bajo a 4.3")
        _alerta(conn, "reclamos_suben", "aviso", "meli", None, "3 abiertas")
        _alerta(conn, "salud_cuenta", "info", "meli", None, "menor")
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            cuerpo = TestClient(app).get("/api/reputacion/resumen").json()
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)
        assert cuerpo["kpi_rating_meli"] == {"promedio": 4.6, "n": 2}
        assert cuerpo["kpi_reviews_meli"] == {"total": 1200}
        assert cuerpo["alertas_por_severidad"] == {"critica": 1, "aviso": 1, "info": 1}
        assert cuerpo["preguntas_pendientes"] == [] or all(
            {"asked_at", "observed_at"} <= set(p) for p in cuerpo["preguntas_pendientes"]
        )


@_skip_db
def test_api_contador_alertas_abiertas():
    with db_reputacion("orbit_repui2") as (conn, _dsn):
        _alerta(conn, "caida_rating", "critica", "meli", "MLM1", "bajo a 4.3")
        _alerta(conn, "reclamos_suben", "aviso", "meli", None, "3 abiertas")
        _alerta(conn, "rating_bajo", "aviso", "meli", None, "resuelta", resolved=True)
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            resp = TestClient(app).get("/api/reputacion/contador")
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)
        assert resp.status_code == 200
        assert resp.json() == {"total_alertas": 2}


@_skip_db
def test_pagina_rediseno_kpis_alertas_primero_y_chips():
    with db_reputacion("orbit_repui2") as (conn, _dsn):
        _snap(conn, "meli", "MLM1", HOY, 4.5)
        _pregunta(conn, "MLM1", "Q1", "UNANSWERED")
        _alerta(conn, "caida_rating", "critica", "meli", "MLM1", "bajo a 4.3")
        conn.execute(
            "INSERT INTO seller_reputation_snapshot"
            " (platform, metric_date, level_id, disputas_total, disputas_abiertas, fetched_at)"
            " VALUES ('meli', %s, '5_green', 41, 3, %s)",
            (HOY, FETCH),
        )
        html = _get_pagina(conn).text
        for kpi in (
            "Rating MeLi",
            "Reviews verificadas",
            "Preguntas pendientes",
            "Alertas abiertas",
        ):
            assert kpi in html
        assert html.index("Alertas abiertas") < html.index("Listings")
        assert "★" in html and "Sin verificar" in html
        assert "Disputas totales" in html and ">41<" in html
        assert "No hay reviews de Amazon en esta versi" in html
        assert 'id="reputacion-contador"' in html
        assert "Responder en MeLi</span>" in html


@_skip_db
def test_pagina_filtros_listings_get():
    with db_reputacion("orbit_repui2") as (conn, _dsn):
        _snap(conn, "meli", "MLM1", HOY, 4.5)
        _snap(conn, "amazon_mx", "B0X", HOY, 4.9)
        app.dependency_overrides[_conexion_lectura] = lambda: conn
        try:
            cliente = TestClient(app)
            solo_meli = cliente.get("/reputacion?plataforma=meli").text
            assert "MLM1" in solo_meli and "B0X" not in solo_meli
            por_q = cliente.get("/reputacion?q=B0X").text
            assert "B0X" in por_q and "MLM1" not in por_q
            assert cliente.get("/reputacion?tendencia=baja").status_code == 200
            assert cliente.get("/reputacion?plataforma=noexiste").status_code == 422
        finally:
            app.dependency_overrides.pop(_conexion_lectura, None)


@_skip_db
def test_pagina_pregunta_muestra_antiguedad():
    with db_reputacion("orbit_repui2") as (conn, _dsn):
        conn.execute(
            "INSERT INTO meli_question"
            " (external_id, question_external_id, estado, texto, asked_at, fetched_at)"
            " VALUES ('MLM1', 'Q7', 'UNANSWERED', 'texto?',"
            " now() - interval '3 hours 50 minutes', now())"
        )
        assert "hace 3 h" in _get_pagina(conn).text


def test_fecha_corta():
    from app import ui

    assert ui.fecha_corta("2026-09-06T04:10:00", True) == "6 sep 04:10"
    assert ui.fecha_corta("2026-09-06") == "6 sep"
    assert ui.fecha_corta(dt.date(2026, 9, 6)) == "6 sep"
    assert ui.fecha_corta(None) == "—"


def test_hace():
    from app import ui

    ahora = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.UTC)
    assert ui.hace("2026-09-08T08:00:00+00:00", ahora) == "hace 4 h"
    assert ui.hace("2026-09-08T11:20:00+00:00", ahora) == "hace 40 min"
    assert ui.hace("2026-09-07T11:00:00+00:00", ahora) == "ayer"
    assert ui.hace("2026-09-05T12:00:00+00:00", ahora) == "hace 3 d"
    assert ui.hace(None, ahora) == "—"
    assert ui.hace("2026-09-09T12:00:00+00:00", ahora) == "—"


def _resumen_fabricado() -> dict:
    return {
        "listings": [
            {
                "platform": "meli",
                "external_id": "MLM1",
                "rating": 4.3,
                "review_count": 412,
                "tendencia": "baja",
                "estado_fuente": "ok",
                "texto": "verificado",
                "url": None,
                "metric_date": "2026-09-06",
            },
        ],
        "cuenta_meli": {
            "metric_date": "2026-09-06",
            "level_id": "5_green",
            "power_scalar": None,
            "power_seller": "platinum",
            "disputas_total": 41,
            "disputas_abiertas": 3,
        },
        "preguntas_pendientes": [
            {
                "external_id": "MLM1",
                "question_external_id": "Q1",
                "texto": "texto?",
                "asked_at": "2026-09-08T08:00:00+00:00",
                "observed_at": "2026-09-08T08:00:00+00:00",
            }
        ],
        "total_pendientes": 18,
        "alertas_abiertas": [
            {
                "tipo": "caida_rating",
                "severidad": "critica",
                "platform": "meli",
                "external_id": "MLM1",
                "mensaje": "bajo a 4.3",
                "created_at": "2026-09-06T04:10:00",
            }
        ],
        "total_alertas": 2,
        "reviews_recientes": [
            {
                "external_id": "MLM1",
                "review_external_id": "RV1",
                "rating": 2,
                "titulo": "titulo",
                "texto": None,
                "published_at": "2026-09-06T10:00:00",
            }
        ],
        "amazon_texto": "sin-verificar",
        "kpi_rating_meli": {"promedio": 4.3, "n": 1},
        "kpi_reviews_meli": {"total": 412},
        "alertas_por_severidad": {"critica": 1, "aviso": 1, "info": 0},
    }


def _render_reputacion(resumen: dict) -> str:
    from app import ui

    ahora = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.UTC)
    return ui.templates.env.get_template("reputacion.html").render(
        pantalla="reputacion",
        resumen=resumen,
        ahora=ahora,
        filtros={"q": None, "plataforma": None, "tendencia": None},
    )


def test_plantilla_reputacion_render_sin_db():
    """Humo sin Postgres: KPIs, alertas primero, estrellas, chips y copias."""
    import re

    html = _render_reputacion(_resumen_fabricado())
    for kpi in ("Rating MeLi", "Reviews verificadas", "Preguntas pendientes", "Alertas abiertas"):
        assert kpi in html
    assert "4.3" in html and "1 crítica · 1 aviso" in html
    assert html.index("Alertas abiertas") < html.index("Listings")
    assert 'aria-label="4.3 de 5"' in html
    assert '<span class="chip alerta">baja</span>' in html
    assert '<span class="chip aviso">Sin verificar</span>' not in html  # este listing es verificado
    assert '<span class="chip ok">Verificado</span>' in html
    assert "— sin URL" in html and "— sin texto" in html
    assert "hace 4 h" in html and "Responder en MeLi</span>" in html
    assert "No hay reviews de Amazon en esta versi" in html
    assert 'href="http' not in html
    inlines = [s for s in re.findall(r"<script[^>]*>", html) if "src=" not in s]
    assert inlines == []
