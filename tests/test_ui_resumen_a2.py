import re

from test_ui import _ctx_resumen_con_payload

from app import ui


def test_resumen_a2_prioriza_pendientes_y_evita_saludos():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_resumen_con_payload())
    assert "<h1>Resumen</h1>" in html
    assert 'class="accion-primaria" href="/campanas/nuevas"' in html
    assert "Hola, David" not in html
    assert "Esto es lo más importante" not in html
    assert "Pendientes de revisión" in html
    assert "Ventas atribuidas" in html
    assert "Canales" in html
    assert 'class="resumen-pendientes superficie"' in html
    assert 'class="resumen-monedas-principales"' in html
    assert html.index("resumen-pendientes") < html.index("resumen-monedas-principales")
    assert html.index("resumen-monedas-principales") < html.index("chart-card")
    assert 'class="resumen-principal"' in html
    assert '<aside class="resumen-canales">' in html
    assert 'id="datos-resumen"' in html
    assert 'class="custom-range"' in html


def test_resumen_a2_no_duplica_la_navegacion_en_el_pie():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_resumen_con_payload())
    assert 'class="footer-links"' not in html


def test_resumen_tendencia_va_antes_de_canales_y_totales_son_claros():
    html = ui.templates.env.get_template("resumen.html").render(**_ctx_resumen_con_payload())
    assert re.search(r'</section>\s*</div>\s*<aside class="resumen-canales">', html)
    assert html.index("sec-tendencia") < html.index("resumen-canales") < html.index("sec-detalle")

    css = (ui._TEMPLATES_DIR.parent / "static" / "css" / "resumen.css").read_text(encoding="utf-8")
    assert "grid-template-columns: minmax(0, 1.75fr) minmax(260px, 1fr)" not in css
    assert re.search(r"\.resumen-canales \.channel-grid\s*\{[^}]*auto-fit", css)
    curr = re.search(r"\.curr-card\s*\{([^}]+)\}", css)
    assert curr is not None
    assert "background: var(--o-card)" in curr.group(1)
    assert "color: var(--o-ink)" in curr.group(1)
    assert "background: #0f172a" not in curr.group(1)


def test_kpis_principales_distinguen_tipo_de_metrica_con_color():
    contexto = _ctx_resumen_con_payload()
    contexto["contrato"]["totales_por_moneda"] = [
        {
            "moneda": "USD",
            "cost": "1.0000",
            "ad_revenue": "2.0000",
            "acos": "50.00",
            "roas": "2.00",
            "clicks": 1,
            "orders": 1,
        }
    ]
    html = ui.templates.env.get_template("resumen.html").render(**contexto)
    for clave in ("ad_revenue", "cost", "acos", "roas"):
        assert f'class="kpi kpi-{clave}"' in html
    css = (ui._TEMPLATES_DIR.parent / "static" / "css" / "resumen.css").read_text(encoding="utf-8")
    assert ".kpi-ad_revenue" in css
    assert ".kpi-cost" in css
    assert ".kpi-acos" in css
    assert ".kpi-roas" in css
    assert "#eef0ff" in css
    assert "#fff4e8" in css
    assert "#edf4ff" in css
    assert "#eaf8f2" in css
