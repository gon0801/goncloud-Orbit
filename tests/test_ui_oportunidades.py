import pytest

from app import ui


@pytest.mark.parametrize(
    "archivo",
    [
        "cortes.html",
        "gasto_sin_venta.html",
        "busquedas_sin_venta.html",
        "keywords_danadas.html",
        "productos_sin_clics.html",
        "inertes.html",
    ],
)
def test_oportunidades_comparten_jerarquia_visual(archivo):
    fuente = (ui._TEMPLATES_DIR / archivo).read_text(encoding="utf-8")
    assert 'class="oportunidad-resumen' in fuente
    assert 'class="oportunidad-contenido' in fuente
    assert 'class="oportunidad-detalle' in fuente
    assert "Hola, David" not in fuente
