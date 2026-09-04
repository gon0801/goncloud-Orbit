from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import ui
from app.api import _conexion_lectura
from app.main import app
from app.ui import dinero_ui

_RAIZ = Path(ui.__file__).resolve().parent


def _html_sin_db(monkeypatch, ruta: str, **parches) -> str:
    for nombre, fake in parches.items():
        monkeypatch.setattr(ui.dash, nombre, fake)
    app.dependency_overrides[_conexion_lectura] = lambda: None
    try:
        resp = TestClient(app).get(ruta)
    finally:
        app.dependency_overrides.pop(_conexion_lectura, None)
    assert resp.status_code == 200
    return resp.text


def _serie_vacia(conn, platform):
    return {
        "plataforma": platform,
        "moneda": "USD",
        "desde": "2026-07-01",
        "hasta": "2026-07-31",
        "ventana_inmaduros": {"desde": "2026-07-24", "hasta": "2026-07-31"},
        "series": [],
    }


def test_ui_html_default_dia_y_boton_tema(monkeypatch):
    html = _html_sin_db(monkeypatch, "/", serie_plataforma=_serie_vacia)
    assert 'data-tema="dia"' in html
    assert 'id="btn-tema"' in html
    assert 'src="/static/js/tema.js"' in html
    assert "onclick=" not in html
    assert "<style" not in html
    assert "http://" not in html and "https://" not in html


def test_tema_js_persiste_y_invalido_es_dia():
    fuente = (_RAIZ / "static" / "js" / "tema.js").read_text(encoding="utf-8")
    assert "localStorage" in fuente
    assert "orbit-tema" in fuente
    assert 'TEMA_DIA = "dia"' in fuente
    assert 'TEMA_NOCHE = "noche"' in fuente
    assert "temaValido" in fuente
    assert "valor === TEMA_NOCHE ? TEMA_NOCHE : TEMA_DIA" in fuente


def test_css_tokens_noche_y_celdas_envuelven():
    css = (_RAIZ / "static" / "css" / "dashboard.css").read_text(encoding="utf-8")
    assert 'data-tema="noche"' in css
    assert "overflow-wrap" in css
    assert "--color-borde" in css
    assert "--color-acento: #e08a4a" in css
    assert "overflow-x: hidden" not in css
    assert not re.search(r"th,\s*td\s*\{[^}]*white-space:\s*nowrap", css)
    assert "white-space: nowrap" not in css
    tarjeta = re.search(r"\.tarjeta\s*\{[^}]+\}", css)
    assert tarjeta is not None
    assert "overflow-x: auto" not in tarjeta.group(0)
    assert "overflow-x: visible" in tarjeta.group(0)


def test_dinero_ui_presentacion_sin_inventar():
    assert dinero_ui("26.0000") == "26.00"
    assert dinero_ui(None) is None
    assert dinero_ui("x") == "x"


def test_campanas_html_dinero_a_2_decimales_clicks_enteros(monkeypatch):
    item = {
        "ad_entity_id": 1,
        "nombre": "Alfa US",
        "status": "ENABLED",
        "plataforma": "amazon_us",
        "moneda": "USD",
        "metricas_30d": {
            "cost": "9.0000",
            "ad_revenue": "40.0000",
            "clicks": 0,
            "acos": "22.50",
            "sin_ventas": False,
            "inmaduro": True,
        },
        "target_efectivo": {"valor": "25.00", "peldano": "goal_plataforma"},
        "goal": None,
    }
    html = _html_sin_db(
        monkeypatch, "/campanas", campanas=lambda conn, platform=None: {"items": [item]}
    )
    assert "9.00" in html and "40.00" in html
    assert "9.0000" not in html and "40.0000" not in html
    assert 'class="num">0<' in html.replace("</td>", "<")
    assert ">Goal</th>" not in html
    assert "sin goal" in html


def test_paginas_siguen_200_con_data_pantalla(monkeypatch):
    html_campanas = _html_sin_db(
        monkeypatch, "/campanas", campanas=lambda conn, platform=None: {"items": []}
    )
    assert 'data-pantalla="campanas"' in html_campanas
    html_decisiones = _html_sin_db(
        monkeypatch,
        "/decisiones",
        decisiones=lambda conn, cursor=None: {
            "items": [],
            "next_cursor": None,
            "has_more": False,
        },
    )
    assert 'data-pantalla="decisiones"' in html_decisiones
    html_contrib = _html_sin_db(
        monkeypatch,
        "/contribucion",
        contribucion_campanas=lambda conn: {
            "plataformas": {
                "amazon_mx": {"ventana": None, "filas": []},
                "amazon_us": {"ventana": None, "filas": []},
            }
        },
    )
    assert 'data-pantalla="contribucion"' in html_contrib
    assert 'data-tema="dia"' in html_campanas
    assert 'id="btn-tema"' in html_decisiones


def test_css_campanas_apila_tabla_en_40rem():
    css = (_RAIZ / "static" / "css" / "dashboard.css").read_text(encoding="utf-8")
    assert 'body[data-pantalla="campanas"]' in css
    bloques = re.findall(
        r"@media\s*\(\s*max-width:\s*40rem\s*\)\s*\{(?:[^{}]|\{[^{}]*\})*\}",
        css,
    )
    campanas = [b for b in bloques if 'body[data-pantalla="campanas"]' in b]
    assert campanas, "falta el bloque 40rem acotado a campanas"
    bloque = campanas[0]
    assert re.search(
        r"body\[data-pantalla=\"campanas\"\]\s+table,"
        r"\s*body\[data-pantalla=\"campanas\"\]\s+thead,"
        r"\s*body\[data-pantalla=\"campanas\"\]\s+tbody,"
        r"\s*body\[data-pantalla=\"campanas\"\]\s+tr,"
        r"\s*body\[data-pantalla=\"campanas\"\]\s+th,"
        r"\s*body\[data-pantalla=\"campanas\"\]\s+td\s*\{"
        r"\s*display:\s*block",
        bloque,
    )
    assert re.search(
        r"body\[data-pantalla=\"campanas\"\]\s+thead tr\s*\{"
        r"\s*display:\s*flex;\s*flex-wrap:\s*wrap",
        bloque,
    )
    assert re.search(r"td\.num\s*\{\s*text-align:\s*left", bloque)
    assert re.search(
        r"\.filtros input,\s*"
        r"body\[data-pantalla=\"campanas\"\]\s+\.filtros select\s*\{"
        r"[^}]*min-width:\s*0[^}]*max-width:\s*100%",
        bloque,
    )
