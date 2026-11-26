import re

import pytest

from app import ui


@pytest.mark.parametrize(
    "archivo", ["precios.html", "reputacion.html", "envios.html", "familias.html"]
)
def test_catalogo_usa_resumen_y_conserva_contenido(archivo):
    fuente = (ui._TEMPLATES_DIR / archivo).read_text(encoding="utf-8")
    assert 'class="catalogo-resumen' in fuente
    assert "Hola, David" not in fuente


def test_precios_no_renderiza_un_resumen_vacio_en_error():
    fuente = (ui._TEMPLATES_DIR / "precios.html").read_text(encoding="utf-8")
    assert fuente.count("{% if plataformas is defined and not error %}") == 1


@pytest.mark.parametrize("archivo", ["salud.html", "settings.html"])
def test_sistema_usa_resumen_y_no_oculta_formularios(archivo):
    fuente = (ui._TEMPLATES_DIR / archivo).read_text(encoding="utf-8")
    assert 'class="sistema-resumen' in fuente
    if archivo == "settings.html":
        assert 'id="settings-token"' in fuente
        assert "data-settings-config" in fuente
        assert "data-settings-goal" in fuente
    else:
        assert 'class="salud-plataformas' in fuente
        assert 'class="salud-tabs' in fuente
        assert '<article class="salud-tarjeta">' in fuente


@pytest.mark.parametrize(
    "archivo",
    ["_precios_goals.html", "reputacion.html", "familias.html", "settings.html"],
)
def test_cada_formulario_tiene_una_accion_primaria(archivo):
    fuente = (ui._TEMPLATES_DIR / archivo).read_text(encoding="utf-8")
    formularios = re.findall(r"<form\b.*?</form>", fuente, flags=re.DOTALL)
    assert formularios
    for formulario in formularios:
        submits = re.findall(r"<button\b[^>]*\btype=\"submit\"[^>]*>", formulario)
        assert len(submits) == 1
        assert len(re.findall(r'class="[^"]*\baccion-primaria\b', submits[0])) == 1
        assert "accion-primaria" not in formulario.replace(submits[0], "")
