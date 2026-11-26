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
