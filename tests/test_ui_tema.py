from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import ui
from app.api import _conexion_lectura
from app.api_dashboard import PageWindow
from app.formato import dinero, fecha_hora
from app.main import app

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


def _contrato_vacio(conn, periodo=30, desde=None, hasta=None, canal=None):
    return {
        "desde": "2026-07-02",
        "hasta": "2026-07-31",
        "dias": 30,
        "periodo": 30,
        "recortado": False,
        "hasta_solicitado": "2026-07-31",
        "canales": {},
        "totales_por_moneda": [],
    }


def test_ui_html_default_dia_y_boton_tema(monkeypatch):
    html = _html_sin_db(monkeypatch, "/", resumen_ads=_contrato_vacio)
    assert 'data-tema="dia"' in html
    assert 'id="btn-tema"' in html
    assert 'src="/static/js/tema.js?v=' in html
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
    assert 'params.get("tema")' in fuente
    assert "valor === TEMA_NOCHE ? TEMA_NOCHE : TEMA_DIA" in fuente


def test_css_tokens_noche_y_celdas_envuelven():
    css = (_RAIZ / "static" / "css" / "dashboard.css").read_text(encoding="utf-8")
    assert 'data-tema="noche"' in css
    assert "overflow-wrap" in css
    assert "--color-borde" in css
    assert "--color-acento: #9385f8" in css
    tokens = (
        "--color-texto",
        "--color-mutado",
        "--color-borde",
        "--color-acento",
        "--color-ok",
        "--color-alerta",
    )
    dia, noche = css.split('data-tema="noche"', 1)
    for token in tokens:
        assert token in dia
        assert token in noche
    assert "overflow-x: hidden" not in css
    assert not re.search(r"th,\s*td\s*\{[^}]*white-space:\s*nowrap", css)
    assert "white-space: nowrap" not in css
    tarjeta = re.search(r"\.tarjeta\s*\{[^}]+\}", css)
    assert tarjeta is not None
    assert "overflow-x: auto" not in tarjeta.group(0)
    assert "overflow-x: visible" in tarjeta.group(0)


def test_dinero_presentacion_sin_inventar():
    assert dinero("26.0000") == "26.00"
    assert dinero(None) == "—"
    assert dinero("x") == "x"


def test_fecha_hora_sin_microsegundos_en_hora_de_mexico():
    assert fecha_hora("2026-09-04 01:40:19.865606+00:00") == "3 sep, 19:40"
    assert fecha_hora(None) == "—"


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
        decisiones_pagina=lambda conn, page=1, page_size=50, tipo="todas", q=None: (
            [],
            PageWindow.desde_total(total=0, page=page, page_size=page_size),
        ),
        decisiones_conteos=lambda conn, q=None: {
            "todas": 0,
            "pausa": 0,
            "puja_sube": 0,
            "puja_baja": 0,
            "negativo": 0,
            "captura": 0,
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


def test_css_campanas_hace_scroll_horizontal_en_40rem():
    """En movil la tabla scrollea dentro de su caja. No se apilan las celdas."""
    dashboard = (_RAIZ / "static" / "css" / "dashboard.css").read_text(encoding="utf-8")
    campanas = (_RAIZ / "static" / "css" / "campanas.css").read_text(encoding="utf-8")
    assert 'body[data-pantalla="campanas"] table,' not in dashboard
    assert 'body[data-pantalla="campanas"] thead' not in dashboard
    assert "overflow-x: auto" in campanas
    assert "min-width: 1100px" in campanas
    bloques = re.findall(
        r"@media\s*\(\s*max-width:\s*40rem\s*\)\s*\{(?:[^{}]|\{[^{}]*\})*\}",
        campanas,
    )
    assert bloques, "falta el bloque 40rem de campanas"
    bloque = bloques[0]
    assert "overflow-x: auto" in bloque
    assert "min-width: 1100px" in bloque
    assert "display: block" not in bloque
    assert re.search(
        r"\.filtros input,\s*"
        r"body\[data-pantalla=\"campanas\"\]\s+\.filtros select\s*\{"
        r"[^}]*min-width:\s*0[^}]*max-width:\s*100%",
        bloque,
    )


def test_numero_miles_y_none():
    from app.formato import numero

    assert numero("1735") == "1,735"
    assert numero(1127) == "1,127"
    assert numero(None) == "—"
    assert numero("xyz") == "xyz"
