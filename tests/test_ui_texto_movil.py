from app import ui


def test_texto_secundario_se_acorta_en_movil_y_se_expande_sin_js():
    plantilla = ui._TEMPLATES_DIR / "_texto_extensible.html"
    assert plantilla.is_file(), "falta el componente nativo de texto extensible"
    html = ui.templates.env.from_string(
        '{% import "_texto_extensible.html" as ux %}'
        '{% call ux.texto_extensible("action-desc") %}Una evidencia larga{% endcall %}'
    ).render()
    assert '<details class="action-desc texto-extensible">' in html
    assert "<summary>" in html
    assert 'class="texto-ver-mas"' in html
    assert 'class="texto-ver-menos"' in html
    assert '<div class="texto-extensible-contenido">Una evidencia larga</div>' in html
    assert "onclick=" not in html

    css = (ui._TEMPLATES_DIR.parent / "static" / "css" / "dashboard.css").read_text(
        encoding="utf-8"
    )
    assert ".texto-extensible:not([open]) > .texto-extensible-contenido" in css
    assert "-webkit-line-clamp: 3" in css
    assert ".texto-extensible > summary" in css
    assert "min-height: 44px" in css

    for nombre in ("resumen.html", "_senal.html", "reputacion.html"):
        fuente = (ui._TEMPLATES_DIR / nombre).read_text(encoding="utf-8")
        assert "_texto_extensible.html" in fuente, f"{nombre} no usa el componente"
