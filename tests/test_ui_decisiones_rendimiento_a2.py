from app import ui


def test_decisiones_prioriza_historial_y_conserva_filtros():
    fuente = (ui._TEMPLATES_DIR / "decisiones.html").read_text(encoding="utf-8")
    assert 'class="historial-resumen' in fuente
    assert 'class="decisiones-filtros' in fuente
    assert 'class="decisiones-dia' in fuente
    assert 'class="paginador' in fuente
    assert "Detalle de ciclos" not in fuente


def test_ruido_y_rendimiento_usan_superficies_sin_cambiar_datos():
    ruido = (ui._TEMPLATES_DIR / "ruido.html").read_text(encoding="utf-8")
    contribucion = (ui._TEMPLATES_DIR / "contribucion.html").read_text(encoding="utf-8")
    dinero = (ui._TEMPLATES_DIR / "donde_poner_el_dinero.html").read_text(encoding="utf-8")
    assert 'class="historial-resumen' in ruido
    assert 'class="contribucion-plataformas rendimiento-resumen"' in contribucion
    assert 'class="rendimiento-resumen' in dinero
    assert "/donde-poner-el-dinero?plataforma=amazon_us" in dinero
    assert "por_campana" in dinero


def test_contribucion_aplica_superficie_a_las_tarjetas_principales():
    fuente = (ui._TEMPLATES_DIR / "contribucion.html").read_text(encoding="utf-8")
    assert 'class="tarjeta contribucion-tarjeta superficie"' in fuente


def test_contribucion_tiene_un_solo_resumen_de_rendimiento():
    fuente = (ui._TEMPLATES_DIR / "contribucion.html").read_text(encoding="utf-8")
    assert fuente.count('class="contribucion-plataformas rendimiento-resumen"') == 1
    assert fuente.count("rendimiento-resumen") == 1
    assert '<div class="rendimiento-resumen">' not in fuente
