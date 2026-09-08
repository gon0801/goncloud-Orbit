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


def test_product_targeting_parcial_bloquea_solo_ese_rol():
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
                    return _respuesta(_fila("PAT_ASIN", "B0AAAAAAAA", 4, 5, 6))

            return Respuesta()

    resultado = br.consultar_roles(
        Cliente(),
        profile_id=101,
        moneda="MXN",
        asins=("B0CCCCCCCC",),
        expresiones_por_rol={"product_targeting": solicitadas},
    )["product_targeting"]
    assert resultado.bid is None
    assert resultado.faltantes == (solicitadas[1],)
    assert resultado.recomendaciones[0].sugerido == Decimal("5")


def test_auto_incompleto_no_inventa_bid():
    class Cliente:
        @staticmethod
        def recommend_bids(cuerpo, *, profile_id):
            assert profile_id == 101

            class Respuesta:
                @staticmethod
                def json():
                    return _respuesta(
                        _fila("LOOSE_MATCH", None, 0.9, 0.99, 1.08),
                        _fila("SUBSTITUTES", None, 0.9, 0.99, 1.08),
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
    )
    assert resultado["auto_discovery"].bid is None
    assert [r.expresion.tipo for r in resultado["auto_discovery"].recomendaciones] == [
        "LOOSE_MATCH",
        "SUBSTITUTES",
    ]
    assert resultado["auto_discovery"].faltantes == (
        br.Expresion("CLOSE_MATCH"),
        br.Expresion("COMPLEMENTS"),
    )
