"""Contrato puro de las sugerencias de puja de Amazon para fabrica."""

from decimal import Decimal

import pytest

from app import fabrica_bids as br


def _respuesta(*filas):
    return {"bidRecommendations": [{"bidRecommendationsForTargetingExpressions": list(filas)}]}


def _fila(tipo, valor, bajo, sugerido, alto):
    expresion = {"type": tipo}
    if valor is not None:
        expresion["value"] = valor
    return {
        "targetingExpression": expresion,
        "bidValues": [
            {"suggestedBid": bajo},
            {"suggestedBid": sugerido},
            {"suggestedBid": alto},
        ],
    }


def test_interpreta_terna_sin_convertir_dinero_a_float():
    solicitadas = (br.Expresion("KEYWORD_EXACT_MATCH", "arras"),)
    resultado = br.interpretar(
        _respuesta(_fila("KEYWORD_EXACT_MATCH", "arras", 8.58, 9.8, 11.37)),
        solicitadas,
    )

    assert resultado == (
        br.Recomendacion(
            expresion=solicitadas[0],
            minimo=Decimal("8.58"),
            sugerido=Decimal("9.8"),
            maximo=Decimal("11.37"),
        ),
    )


def test_interpretar_aborta_si_amazon_omite_una_expresion():
    solicitadas = (
        br.Expresion("KEYWORD_EXACT_MATCH", "arras"),
        br.Expresion("KEYWORD_EXACT_MATCH", "boda"),
    )
    with pytest.raises(br.RecomendacionIncompleta, match="boda"):
        br.interpretar(
            _respuesta(_fila("KEYWORD_EXACT_MATCH", "arras", 8, 9, 10)),
            solicitadas,
        )


def test_bid_inicial_es_mediana_clampeada_y_redondeada_por_moneda():
    recomendaciones = (
        br.Recomendacion(br.Expresion("CLOSE_MATCH"), Decimal("1"), Decimal("50"), Decimal("60")),
        br.Recomendacion(
            br.Expresion("LOOSE_MATCH"), Decimal("1"), Decimal("10.005"), Decimal("20")
        ),
        br.Recomendacion(br.Expresion("SUBSTITUTES"), Decimal("1"), Decimal("20"), Decimal("30")),
    )
    assert br.bid_inicial(recomendaciones, "MXN") == Decimal("20.00")
    assert br.bid_inicial(recomendaciones[:1], "MXN") == Decimal("45.00")


def test_payload_fija_tipo_de_recomendacion_asins_y_expresiones():
    expresiones = (
        br.Expresion("CLOSE_MATCH"),
        br.Expresion("KEYWORD_PHRASE_MATCH", "arras para boda"),
    )
    assert br.payload(("B0BBBBBBBB", "B0AAAAAAAA"), expresiones) == {
        "recommendationType": "BIDS_FOR_NEW_AD_GROUP",
        "asins": ["B0AAAAAAAA", "B0BBBBBBBB"],
        "bidding": {"strategy": "LEGACY_FOR_SALES"},
        "targetingExpressions": [
            {"type": "CLOSE_MATCH"},
            {"type": "KEYWORD_PHRASE_MATCH", "value": "arras para boda"},
        ],
    }


def test_consultar_roles_devuelve_bid_inicial_y_evidencia_por_objetivo():
    class Cliente:
        def __init__(self):
            self.llamadas = []

        def recommend_bids(self, cuerpo, *, profile_id):
            self.llamadas.append((cuerpo, profile_id))
            filas = [
                _fila(exp["type"], exp.get("value"), 4, 5, 6)
                for exp in cuerpo["targetingExpressions"]
            ]

            class Respuesta:
                @staticmethod
                def json():
                    return _respuesta(*filas)

            return Respuesta()

    cliente = Cliente()
    semillas = {
        "category_exact": (br.Expresion("KEYWORD_EXACT_MATCH", "exacta"),),
        "category_phrase": (br.Expresion("KEYWORD_PHRASE_MATCH", "frase"),),
        "category_broad": (br.Expresion("KEYWORD_BROAD_MATCH", "amplia"),),
        "product_targeting": (br.Expresion("PAT_ASIN", "B0BBBBBBBB"),),
        "auto_discovery": (br.Expresion("CLOSE_MATCH"),),
    }
    resultado = br.consultar_roles(
        cliente,
        profile_id=101,
        moneda="MXN",
        asins=("B0AAAAAAAA",),
        expresiones_por_rol=semillas,
    )

    assert len(cliente.llamadas) == 3
    assert set(resultado) == set(semillas)
    assert all(valor.bid == Decimal("5.00") for valor in resultado.values())
    assert resultado["category_exact"].recomendaciones[0].expresion.valor == "exacta"


def test_consultar_roles_marca_rol_sin_objetivos_y_no_inventa_bid():
    class Cliente:
        def recommend_bids(self, *args, **kwargs):
            raise AssertionError("no debe consultar parcialmente")

    resultado = br.consultar_roles(
        Cliente(),
        profile_id=101,
        moneda="MXN",
        asins=("B0AAAAAAAA",),
        expresiones_por_rol={"category_exact": ()},
    )
    assert resultado["category_exact"].bid is None
    assert resultado["category_exact"].faltantes == ()


def test_consultar_roles_rellena_faltantes_con_promedio_del_rol():
    """Decisión del dueño 2026-09-09: se quita el todo o nada; 3 de 5 con su
    sugerido, 2 con el promedio simple del rol, fuentes marcadas por expresión."""
    solicitadas = tuple(
        br.Expresion("KEYWORD_PHRASE_MATCH", kw) for kw in ("a", "b", "c", "d", "e")
    )

    class Cliente:
        @staticmethod
        def recommend_bids(cuerpo, *, profile_id):
            class Respuesta:
                @staticmethod
                def json():
                    return _respuesta(
                        _fila("KEYWORD_PHRASE_MATCH", "a", 4, 9.80, 12),
                        _fila("KEYWORD_PHRASE_MATCH", "b", 4, 4.13, 12),
                        _fila("KEYWORD_PHRASE_MATCH", "c", 4, 13.45, 12),
                    )

            return Respuesta()

    resultado = br.consultar_roles(
        Cliente(),
        profile_id=101,
        moneda="MXN",
        asins=("B0CCCCCCCC",),
        expresiones_por_rol={"category_phrase": solicitadas},
    )["category_phrase"]
    assert resultado.bid == Decimal("9.80"), "defaultBid conserva la mediana de las reales"
    assert [r.expresion for r in resultado.recomendaciones] == list(solicitadas)
    assert [r.fuente for r in resultado.recomendaciones] == [
        "amazon_v4",
        "amazon_v4",
        "amazon_v4",
        "promedio_rol",
        "promedio_rol",
    ]
    assert [r.sugerido for r in resultado.recomendaciones] == [
        Decimal("9.80"),
        Decimal("4.13"),
        Decimal("13.45"),
        Decimal("9.13"),
        Decimal("9.13"),
    ]
    assert all(
        (r.minimo, r.sugerido, r.maximo) == (Decimal("9.13"), Decimal("9.13"), Decimal("9.13"))
        for r in resultado.recomendaciones[3:]
    )
    assert resultado.faltantes == ()
    assert resultado.promedio == Decimal("9.13")


def test_consultar_roles_con_cero_sugerencias_sigue_manual():
    """Regla 3: sin nada que promediar no se inventa número; el rol queda manual."""
    solicitadas = (
        br.Expresion("PAT_ASIN", "B0AAAAAAAA"),
        br.Expresion("PAT_ASIN", "B0BBBBBBBB"),
    )

    class Cliente:
        @staticmethod
        def recommend_bids(cuerpo, *, profile_id):
            class Respuesta:
                @staticmethod
                def json():
                    return _respuesta()

            return Respuesta()

    resultado = br.consultar_roles(
        Cliente(),
        profile_id=101,
        moneda="MXN",
        asins=("B0CCCCCCCC",),
        expresiones_por_rol={"product_targeting": solicitadas},
    )["product_targeting"]
    assert resultado.bid is None
    assert resultado.recomendaciones == ()
    assert resultado.faltantes == solicitadas
    assert resultado.promedio is None


def test_promedio_rol_acota_por_piso_y_techo_de_la_moneda():
    base = br.Expresion("KEYWORD_PHRASE_MATCH", "a")
    bajas = (
        br.Recomendacion(base, Decimal("0.4"), Decimal("0.50"), Decimal("0.7")),
        br.Recomendacion(base, Decimal("0.4"), Decimal("0.60"), Decimal("0.7")),
    )
    assert br.promedio_rol(bajas, "MXN") == Decimal("1.00"), "piso MXN"
    altas = (
        br.Recomendacion(base, Decimal("40"), Decimal("50"), Decimal("60")),
        br.Recomendacion(base, Decimal("40"), Decimal("60"), Decimal("70")),
    )
    assert br.promedio_rol(altas, "MXN") == Decimal("45.00"), "techo MXN"
    with pytest.raises(br.RecomendacionIncompleta):
        br.promedio_rol((), "MXN")


def test_auto_parcial_promedia_los_faltantes():
    class Cliente:
        @staticmethod
        def recommend_bids(cuerpo, *, profile_id):
            assert profile_id == 101

            class Respuesta:
                @staticmethod
                def json():
                    return _respuesta(
                        _fila("LOOSE_MATCH", None, 3.0, 4.0, 5.0),
                        _fila("SUBSTITUTES", None, 5.0, 6.0, 7.0),
                    )

            return Respuesta()

    expresiones = tuple(
        br.Expresion(tipo) for tipo in ("CLOSE_MATCH", "LOOSE_MATCH", "SUBSTITUTES", "COMPLEMENTS")
    )
    resultado = br.consultar_roles(
        Cliente(),
        profile_id=101,
        moneda="MXN",
        asins=("B0AAAAAAAA",),
        expresiones_por_rol={"auto_discovery": expresiones},
    )["auto_discovery"]
    assert resultado.bid == Decimal("5.00"), "mediana de las reales"
    assert [r.expresion.tipo for r in resultado.recomendaciones] == [
        "CLOSE_MATCH",
        "LOOSE_MATCH",
        "SUBSTITUTES",
        "COMPLEMENTS",
    ]
    assert [r.fuente for r in resultado.recomendaciones] == [
        "promedio_rol",
        "amazon_v4",
        "amazon_v4",
        "promedio_rol",
    ]
    assert resultado.faltantes == ()
    assert resultado.promedio == Decimal("5.00")
