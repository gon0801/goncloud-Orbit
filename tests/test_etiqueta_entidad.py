"""Etiqueta humana de ad_entity: NUNCA el JSON de expression en la UI."""

from __future__ import annotations

from app.etiqueta_entidad import EtiquetaEntidad, etiqueta_entidad, linea_entidad


def test_target_asin_same_as_es_texto_no_json():
    crudo = '[{"type":"ASIN_SAME_AS","value":"B086TVLJ43"}]'
    etiqueta = etiqueta_entidad(
        kind="product_target",
        name=crudo,
        keyword_text=None,
        campana="Campana A",
    )
    assert etiqueta.hoja == "mismo ASIN B086TVLJ43"
    assert etiqueta.linea() == "mismo ASIN B086TVLJ43 · Campana A"
    assert "ASIN_SAME_AS" not in etiqueta.linea()
    assert "[" not in etiqueta.linea()


def test_target_accessory_y_query_sin_value():
    accesorio = linea_entidad(
        kind="product_target",
        name='[{"type":"ASIN_ACCESSORY_RELATED"}]',
        keyword_text=None,
        campana="Campana A",
    )
    query = linea_entidad(
        kind="product_target",
        name='[{"type":"QUERY_HIGH_REL_MATCHES"}]',
        keyword_text=None,
        campana="Campana A",
    )
    assert accesorio == "accesorio · Campana A"
    assert query == "query cercana · Campana A"
    assert "ASIN_ACCESSORY_RELATED" not in accesorio
    assert "QUERY_HIGH_REL_MATCHES" not in query


def test_keyword_usa_keyword_text_no_name():
    linea = linea_entidad(
        kind="keyword",
        name=None,
        keyword_text="zapato blanco",
        campana="Campana A",
    )
    assert linea == "zapato blanco · Campana A"


def test_campana_es_solo_el_nombre():
    linea = linea_entidad(kind="campaign", name="Campana A", keyword_text=None, campana="Campana A")
    assert linea == "Campana A"


def test_campana_sin_nombre_es_none():
    assert linea_entidad(kind="campaign", name=None, keyword_text=None, campana=None) is None


def test_ad_group_sin_nombre_cae_a_campana():
    linea = linea_entidad(kind="ad_group", name=None, keyword_text=None, campana="Campana A")
    assert linea == "Campana A"


def test_json_roto_no_se_vuelca():
    linea = linea_entidad(
        kind="product_target",
        name="[{type:ROTO",
        keyword_text=None,
        campana="Campana A",
    )
    assert linea == "Campana A"
    assert "[" not in linea


def test_tipo_desconocido_no_vuelca_el_token():
    linea = linea_entidad(
        kind="product_target",
        name='[{"type":"ASIN_NUEVO_TIPO","value":"B0NEW"}]',
        keyword_text=None,
        campana=None,
    )
    assert linea == "nuevo tipo B0NEW"
    assert "ASIN_NUEVO_TIPO" not in linea


def test_inertes_usa_hoja_sin_repetir_campana():
    etiqueta = etiqueta_entidad(
        kind="product_target",
        name='[{"type":"ASIN_SAME_AS","value":"B086TVLJ43"}]',
        keyword_text=None,
        campana="Campana A",
    )
    assert etiqueta == EtiquetaEntidad(hoja="mismo ASIN B086TVLJ43", campana="Campana A")
