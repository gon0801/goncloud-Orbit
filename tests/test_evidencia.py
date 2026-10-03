"""Tests de tabla de la politica por evidencia (A4).

Pinnean los casos del diseno (CPC 3 MXN, target 20.8 %, previa 2.1 %,
AOV 1000): 0/16 -> nada, 0/100 -> -12, 0/150 -> -25 (CF); hoja nueva
hereda familia; Gamma contra oraculos independientes (suma
complemento a prec 100, acuerdo serie<->CF, formas cerradas a=1
verificables con calculadora); mutante "ignora confianzas" muerto por
casos no-default (registrado: MUTANTE-A4-CONF).
"""

import datetime as dt
from decimal import Decimal, localcontext

import pytest

from app.optimizer import bid, evidencia
from app.optimizer.evidencia import (
    Conteo,
    CostoPorClic,
    EstimacionAcos,
    EvidenciaHoja,
    GranoHoja,
    MapeoHoja,
    Previa,
)

TOL = Decimal("1e-12")

# Fixture headline del diseno (AOV 1000 pineado; B9/N5d).
_CPC = Decimal("3")
_TARGET = Decimal("20.8")
_AOV = Decimal("1000")


def _previa_fija(cvr="0.021", aov="1000.00"):
    return Previa(cvr=Decimal(cvr), aov=Decimal(aov), niveles=("plataforma",))


def _cpc_fijo(clicks=100, cost="300.00"):
    return CostoPorClic(
        cost=Decimal(cost),
        clicks=clicks,
        desde=dt.date(2026, 9, 1),
        hasta=dt.date(2026, 9, 30),
        post_cambio=True,
    )


def _evidencia_conteo(clicks, orders, revenue="0.00", cpc_clicks=100, cpc_cost="300.00"):
    return EvidenciaHoja(
        previa=_previa_fija(),
        conversion=Conteo(clicks=clicks, orders=orders, ad_revenue=Decimal(revenue)),
        cpc=_cpc_fijo(cpc_clicks, cpc_cost),
    )


# ---------------------------------------------------------------------------
# Espejos pineados contra bid.py (el ciclo de imports impide importarlos)
# ---------------------------------------------------------------------------


def test_multiplicadores_iguales_a_bid():
    assert evidencia.MULT_BAJA_FUERTE == bid.MULT_BAJA_FUERTE
    assert evidencia.MULT_BAJA_SUAVE == bid.MULT_BAJA_SUAVE
    assert evidencia.MULT_SUBIDA == bid.MULT_SUBIDA


def test_motivos_banda_iguales_a_bid():
    assert bid._MOTIVO_BANDA[bid.FACTOR_BAJA_FUERTE] == evidencia._MOTIVO_BANDA_MENOS_25
    assert bid._MOTIVO_BANDA[bid.FACTOR_BAJA_SUAVE] == evidencia._MOTIVO_BANDA_MENOS_12
    assert bid._MOTIVO_BANDA[bid.FACTOR_SUBIDA] == evidencia._MOTIVO_BANDA_MAS_15


def test_motivos_abstencion_reexportados_por_bid():
    assert bid.MOTIVO_EVIDENCIA_INSUFICIENTE == "evidencia_insuficiente"
    assert bid.MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE == "cpc_post_cambio_insuficiente"
    assert bid.MOTIVO_EVIDENCIA_INSUFICIENTE == evidencia.MOTIVO_EVIDENCIA_INSUFICIENTE
    assert bid.MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE == evidencia.MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE


# ---------------------------------------------------------------------------
# Gamma: oraculos independientes
# ---------------------------------------------------------------------------


def _oraculo_complemento(a: int, x: Decimal) -> Decimal:
    """P(a,x) = 1 - e^-x.SUM_{n<a} x^n/n!, a prec 100. Formula DISTINTA
    a la del motor (suma cabeza vs suma cola): discrepa si la
    recurrencia del motor esta mal."""
    with localcontext(prec=100):
        total = Decimal(1)
        termino = Decimal(1)
        for n in range(1, a):
            termino = termino * x / Decimal(n)
            total += termino
        return Decimal(1) - (-x).exp() * total


@pytest.mark.parametrize(
    ("a", "x"),
    [
        (1, "0.7979"),
        (1, "1.8514"),
        (1, "2.1113"),
        (2, "0.5"),
        (2, "1.0"),
        (2, "3.5"),
        (3, "2.0"),
        (3, "5.0"),
        (5, "4.0"),
        (5, "9.0"),
        (10, "3.0"),
        (10, "12.0"),
    ],
)
def test_gamma_contra_oraculo_complemento(a, x):
    p = evidencia.gamma_p(Decimal(a), Decimal(x))
    assert isinstance(p, Decimal)
    assert abs(p - _oraculo_complemento(a, Decimal(x))) < TOL


@pytest.mark.parametrize(
    ("a", "x"),
    [(1, "0.7979"), (2, "1.0"), (2, "3.5"), (3, "2.0"), (5, "4.0"), (10, "12.0")],
)
def test_gamma_cola_cabeza_acuerdan(a, x):
    """Cola (desde pmf(a) hacia arriba) + cabeza (desde t_0): dos
    recurrencias independientes suman 1 (tolerancia 1e-15, margen sobre
    el corte 1e-18)."""
    da, dx = Decimal(a), Decimal(x)
    with localcontext(prec=28):
        serie = evidencia._gamma_p_serie(da, dx)
        cabeza = evidencia._gamma_q_cabeza(da, dx)
        assert abs(serie + cabeza - 1) < Decimal("1e-15")


def test_gamma_forma_cerrada_a_uno():
    """P(1,x) = 1 - e^-x: verificable con calculadora."""
    for x in ("0.5", "0.7979", "1.8514", "2.1113", "5.0"):
        with localcontext(prec=28):
            assert abs(evidencia.gamma_p(Decimal(1), Decimal(x)) - (1 - (-Decimal(x)).exp())) < TOL


def test_gamma_cotas_y_monotonia():
    for a in (1, 2, 5):
        previo = Decimal(0)
        for x in ("0", "0.1", "1", "2", "5", "20"):
            p = evidencia.gamma_p(Decimal(a), Decimal(x))
            q = evidencia.gamma_q(Decimal(a), Decimal(x))
            assert Decimal(0) <= p <= 1
            assert abs(p + q - 1) < TOL
            assert p >= previo
            previo = p
    assert evidencia.gamma_p(Decimal(3), Decimal(0)) == 0
    assert evidencia.gamma_q(Decimal(3), Decimal(0)) == 1


def test_gamma_rechaza_fuera_de_dominio():
    with pytest.raises(ValueError):
        evidencia.gamma_p(Decimal(0), Decimal(1))
    with pytest.raises(ValueError):
        evidencia.gamma_p(Decimal("1.5"), Decimal(1))
    with pytest.raises(ValueError):
        evidencia.gamma_p(Decimal(1), Decimal("-0.1"))
    with pytest.raises(ArithmeticError):
        evidencia.gamma_p(Decimal(10001), Decimal(5))


def test_gamma_k_fraccionaria_falla_ruidoso(monkeypatch):
    """A8: si K se recalibra fraccionario, a deja de ser entera y gamma
    revienta (hay que implementar Lanczos; no se permite deriva muda)."""
    monkeypatch.setattr(evidencia, "K_PREVIA", Decimal("1.5"))
    ev = _evidencia_conteo(100, 0)
    with pytest.raises(ValueError, match="entera"):
        evidencia.estima_acos(ev, _TARGET)


# ---------------------------------------------------------------------------
# Pliegue jerarquico
# ---------------------------------------------------------------------------


def _conteo(clicks, orders, revenue):
    return Conteo(
        clicks=clicks,
        orders=orders,
        ad_revenue=Decimal(revenue) if revenue is not None else None,
    )


def test_previa_raiz_sola():
    previa = evidencia.previa_jerarquica([("plataforma", _conteo(1000, 21, "21000.00"))])
    assert previa is not None
    assert previa.cvr == Decimal(21) / Decimal(1000)
    assert previa.aov == Decimal("21000.00") / Decimal(21)
    assert previa.niveles == ("plataforma",)


def test_previa_salta_nivel_ausente_y_encoge():
    raiz = _conteo(1000, 21, "21000.00")
    grupo = _conteo(100, 0, "0.00")
    previa = evidencia.previa_jerarquica(
        [("plataforma", raiz), ("familia", None), ("ad_group", grupo)]
    )
    assert previa is not None
    assert previa.niveles == ("plataforma", "ad_group")
    # K=1 conjugada: cvr = (0 + 1)/(100 + 1/0.021) < 0.021 (encoge al padre)
    assert previa.cvr < Decimal("0.021")
    with localcontext(prec=28):
        esperado = (Decimal(0) + Decimal(1)) / (Decimal(100) + Decimal(1) / Decimal("0.021"))
        assert abs(previa.cvr - esperado) < Decimal("1e-24")


def test_previa_sin_ordenes_en_raiz_es_none():
    assert evidencia.previa_jerarquica([("plataforma", _conteo(100, 0, "0.00"))]) is None
    assert evidencia.previa_jerarquica([("plataforma", None)]) is None
    assert evidencia.previa_jerarquica([]) is None


def test_previa_raiz_sin_clics_o_sin_ticket_es_none():
    assert evidencia.previa_jerarquica([("plataforma", _conteo(0, 5, "500.00"))]) is None
    assert evidencia.previa_jerarquica([("plataforma", _conteo(100, 5, "0.00"))]) is None


def test_previa_salta_nivel_sin_clics_para_cvr():
    raiz = _conteo(1000, 21, "21000.00")
    raro = _conteo(0, 5, "500.00")  # ordenes sin clics: artefacto
    previa = evidencia.previa_jerarquica([("plataforma", raiz), ("ad_group", raro)])
    assert previa is not None
    # CVR intacto de la raiz (el nivel no aporto CVR); AOV si encogio
    assert previa.cvr == Decimal(21) / Decimal(1000)
    assert previa.aov != Decimal("21000.00") / Decimal(21)


def test_previa_veneno_por_metrica_no_zero_fill():
    """A4: nivel con orders None no aporta CVR ni AOV (jamas cuenta 0)."""
    raiz = _conteo(1000, 21, "21000.00")
    envenenado = Conteo(clicks=100, orders=None, ad_revenue=Decimal("500.00"))
    previa = evidencia.previa_jerarquica([("plataforma", raiz), ("ad_group", envenenado)])
    assert previa is not None
    assert previa.niveles == ("plataforma",)
    assert previa.cvr == Decimal(21) / Decimal(1000)


def test_previa_ids_de_mapeo_viajan_aunque_no_aporten():
    raiz = _conteo(1000, 21, "21000.00")
    previa = evidencia.previa_jerarquica(
        [("plataforma", raiz), ("familia", None)],
        familia_id=7,
        subfamilia_id=None,
    )
    assert previa is not None
    assert previa.familia_id == 7
    assert previa.subfamilia_id is None
    assert previa.niveles == ("plataforma",)


def test_enrolla_veneno_or_hacia_arriba():
    sanos = GranoHoja(
        hoja_id=1,
        ad_group_id=9,
        familia_id=None,
        subfamilia_id=None,
        conteo=_conteo(50, 1, "1000.00"),
    )
    mal = GranoHoja(
        hoja_id=2,
        ad_group_id=9,
        familia_id=None,
        subfamilia_id=None,
        conteo=Conteo(clicks=60, orders=None, ad_revenue=Decimal("10.00")),
    )
    conv = evidencia.enrolla_granos(
        [sanos, mal],
        moneda="MXN",
        ventana_desde=dt.date(2026, 7, 4),
        ventana_hasta=dt.date(2026, 9, 23),
    )
    grupo = conv.por_ad_group[9]
    assert grupo.clicks == 110  # suma de sanos
    assert grupo.orders is None  # un grano envenenado envenena la metrica
    assert grupo.ad_revenue == Decimal("1010.00")
    assert conv.plataforma == grupo  # la raiz suma los mismos granos
    assert 9 not in conv.por_familia
    assert conv.por_hoja[1] is sanos


# ---------------------------------------------------------------------------
# clasifica: orden CPC -> previa -> conversion, split por subsistema
# ---------------------------------------------------------------------------


def test_clasifica_cpc_primero_aunque_previa_falte():
    """Orden pineado: cpc None + previa None -> motivo CPC (no previa)."""
    assert evidencia.clasifica(None, _conteo(10, 0, "0.00"), None) == "cpc_post_cambio_insuficiente"


def test_clasifica_cpc_insuficiente_por_rama():
    previa = _previa_fija()
    conv = _conteo(100, 0, "0.00")
    base = {"desde": dt.date(2026, 9, 1), "hasta": dt.date(2026, 9, 30), "post_cambio": True}
    assert evidencia.clasifica(previa, conv, _cpc_fijo(19, "57.00")) == (
        "cpc_post_cambio_insuficiente"
    )
    assert (
        evidencia.clasifica(previa, conv, CostoPorClic(cost=Decimal(0), clicks=50, **base))
        == "cpc_post_cambio_insuficiente"
    )
    assert (
        evidencia.clasifica(previa, conv, CostoPorClic(cost=Decimal("-1"), clicks=50, **base))
        == "cpc_post_cambio_insuficiente"
    )
    assert (
        evidencia.clasifica(previa, conv, CostoPorClic(cost=None, clicks=50, **base))
        == "cpc_post_cambio_insuficiente"
    )
    assert (
        evidencia.clasifica(previa, conv, CostoPorClic(cost=Decimal(50), clicks=None, **base))
        == "cpc_post_cambio_insuficiente"
    )


def test_clasifica_previa_none_y_conversion_envenenada():
    cpc = _cpc_fijo(50, "150.00")
    assert evidencia.clasifica(None, _conteo(100, 0, "0.00"), cpc) == ("evidencia_insuficiente")
    assert (
        evidencia.clasifica(
            _previa_fija(),
            Conteo(clicks=100, orders=None, ad_revenue=Decimal("0.00")),
            cpc,
        )
        == "evidencia_insuficiente"
    )


def test_clasifica_hoja_nueva_arma_con_previa():
    cpc = _cpc_fijo(50, "150.00")
    resultado = evidencia.clasifica(_previa_fija(), None, cpc)
    assert isinstance(resultado, EvidenciaHoja)
    assert resultado.conversion is None


# ---------------------------------------------------------------------------
# Casos headline del diseno (CPC 3, target 20.8, previa 2.1 %, AOV 1000)
# ---------------------------------------------------------------------------


def test_headline_0_en_16_no_recorta():
    ev = _evidencia_conteo(16, 0, "0.00", cpc_clicks=16, cpc_cost="48.00")
    assert isinstance(ev, EvidenciaHoja)
    est = evidencia.estima_acos(ev, _TARGET)
    # a=1: P = 1 - e^-x con x = b.cvr_1.15 (verificable con calculadora)
    with localcontext(prec=28):
        b = Decimal(1) / Decimal("0.021") + 16
        x = b * Decimal(3) / (Decimal("1.15") * Decimal("0.208") * 1000)
        assert abs(est.p_sobre[Decimal("1.15")] - (1 - (-x).exp())) < TOL
    assert est.p_sobre[Decimal("1.15")] < Decimal("0.80")
    assert (
        evidencia.factor_por_evidencia(est, Decimal("0.80"), Decimal("0.70"))
        == "evidencia_insuficiente"
    )


def test_headline_0_en_100_recorta_12():
    ev = _evidencia_conteo(100, 0, "0.00")
    est = evidencia.estima_acos(ev, _TARGET)
    assert est.p_sobre[Decimal("1.35")] < Decimal("0.80")
    assert est.p_sobre[Decimal("1.15")] >= Decimal("0.80")
    assert evidencia.factor_por_evidencia(est, Decimal("0.80"), Decimal("0.70")) == "banda_menos_12"


def test_headline_0_en_150_recorta_25_por_complemento():
    """B9: x = 2.1113 >= a+1 = 2 -> region complemento de cabeza exacta."""
    ev = _evidencia_conteo(150, 0, "0.00", cpc_clicks=150, cpc_cost="450.00")
    est = evidencia.estima_acos(ev, _TARGET)
    with localcontext(prec=28):
        b = Decimal(1) / Decimal("0.021") + 150
        x = b * Decimal(3) / (Decimal("1.35") * Decimal("0.208") * 1000)
        assert x >= 2  # region complemento pineada
        assert abs(est.p_sobre[Decimal("1.35")] - (1 - (-x).exp())) < TOL
    assert evidencia.factor_por_evidencia(est, Decimal("0.80"), Decimal("0.70")) == "banda_menos_25"


def test_headline_hoja_nueva_toma_previa_familia():
    """Hoja nueva: posterior = previa pura (cvr_post == cvr previo)."""
    ev = EvidenciaHoja(previa=_previa_fija("0.035", "1200.00"), conversion=None, cpc=_cpc_fijo())
    est = evidencia.estima_acos(ev, _TARGET)
    assert est.cvr_post == Decimal("0.035")
    assert est.aov_post == Decimal("1200.00")
    assert est.ordenes_efectivas == 1


def test_subida_cuando_acos_bajo_085_con_confianza():
    ev = _evidencia_conteo(200, 8, "8000.00", cpc_clicks=200, cpc_cost="600.00")
    est = evidencia.estima_acos(ev, _TARGET)
    assert evidencia.factor_por_evidencia(est, Decimal("0.80"), Decimal("0.70")) == "banda_mas_15"


# ---------------------------------------------------------------------------
# Mutante MUTANTE-A4-CONF: ignora/swap confianzas (B8)
# ---------------------------------------------------------------------------


def test_confianza_recorte_no_default_mata_mutante():
    """0/100 da P(-25) = 0.793, P(-12) = 0.843: con recorte 0.95 nada,
    con 0.70 recorta -25 por precedencia. Un mutante que hardcodea
    (0.80, 0.70) daria -12 y uno que los swapea daria nada: ambos
    fallan aqui."""
    ev = _evidencia_conteo(100, 0, "0.00")
    est = evidencia.estima_acos(ev, _TARGET)
    assert (
        evidencia.factor_por_evidencia(est, Decimal("0.95"), Decimal("0.70"))
        == "evidencia_insuficiente"
    )
    assert evidencia.factor_por_evidencia(est, Decimal("0.70"), Decimal("0.70")) == "banda_menos_25"


def test_confianza_subida_no_default_mata_mutante():
    ev = _evidencia_conteo(200, 8, "8000.00", cpc_clicks=200, cpc_cost="600.00")
    est = evidencia.estima_acos(ev, _TARGET)
    assert Decimal("0.70") <= est.p_bajo < Decimal("0.99")
    assert (
        evidencia.factor_por_evidencia(est, Decimal("0.80"), Decimal("0.99"))
        == "evidencia_insuficiente"
    )


def test_exclusion_mutua_en_piso_050_050():
    """A12: eventos disjuntos + masa intermedia > 0 => jamas ambas, aun
    con confianzas al minimo configurable."""
    casos = [
        _evidencia_conteo(16, 0, "0.00", 16, "48.00"),
        _evidencia_conteo(100, 0, "0.00"),
        _evidencia_conteo(150, 0, "0.00", 150, "450.00"),
        _evidencia_conteo(200, 8, "8000.00", 200, "600.00"),
        _evidencia_conteo(50, 3, "3000.00", 50, "150.00"),
    ]
    for ev in casos:
        est = evidencia.estima_acos(ev, _TARGET)
        dispara_baja = est.p_sobre[Decimal("1.15")] >= Decimal("0.50") or est.p_sobre[
            Decimal("1.35")
        ] >= Decimal("0.50")
        dispara_subida = est.p_bajo >= Decimal("0.50")
        assert not (dispara_baja and dispara_subida)


def test_precedencia_menos25_sobre_menos12():
    preciso = EstimacionAcos(
        acos_central=Decimal("0.5"),
        cvr_post=Decimal("0.01"),
        aov_post=Decimal("1000"),
        p_sobre={Decimal("1.35"): Decimal("0.81"), Decimal("1.15"): Decimal("0.95")},
        p_bajo=Decimal("0.01"),
        ordenes_efectivas=Decimal("1"),
    )
    assert (
        evidencia.factor_por_evidencia(preciso, Decimal("0.80"), Decimal("0.70"))
        == "banda_menos_25"
    )


# ---------------------------------------------------------------------------
# evidencia_hoja: cableado grano -> previa -> clasifica + fail-stance
# ---------------------------------------------------------------------------


def _conv_minima(granos, mapeo=()):
    return evidencia.enrolla_granos(
        granos,
        moneda="MXN",
        ventana_desde=dt.date(2026, 7, 4),
        ventana_hasta=dt.date(2026, 9, 23),
        mapeo=mapeo,
    )


def test_evidencia_hoja_hereda_familia_y_mapea_ids():
    fam = _conteo(500, 20, "20000.00")
    conv = _conv_minima(
        [
            GranoHoja(
                hoja_id=5,
                ad_group_id=9,
                familia_id=7,
                subfamilia_id=None,
                conteo=_conteo(16, 0, "0.00"),
            ),
            GranoHoja(
                hoja_id=6,
                ad_group_id=10,
                familia_id=7,
                subfamilia_id=None,
                conteo=fam,
            ),
        ]
    )
    resultado = evidencia.evidencia_hoja(conv, 5, _cpc_fijo(30, "90.00"))
    assert isinstance(resultado, EvidenciaHoja)
    assert resultado.previa.familia_id == 7
    assert "familia" in resultado.previa.niveles
    assert "ad_group" in resultado.previa.niveles


def test_evidencia_hoja_nueva_sin_grano():
    """Hoja SIN filas en ad group y familia CON datos: hereda SUS
    niveles (r3, bloqueante CAMBIOS r2: antes caia a plataforma)."""
    otra = GranoHoja(
        hoja_id=1,
        ad_group_id=9,
        familia_id=7,
        subfamilia_id=None,
        conteo=_conteo(2000, 42, "42000.00"),
    )
    mapeo = [MapeoHoja(hoja_id=999, ad_group_id=9, familia_id=7, subfamilia_id=None)]
    conv = _conv_minima([otra], mapeo=mapeo)
    resultado = evidencia.evidencia_hoja(conv, 999, _cpc_fijo(30, "90.00"))
    assert isinstance(resultado, EvidenciaHoja)
    assert resultado.conversion is None
    assert resultado.previa.niveles == ("plataforma", "familia", "ad_group")
    assert resultado.previa.familia_id == 7


def test_evidencia_hoja_sin_mapeo_solo_plataforma():
    """Hoja desconocida total (sin grano NI mapeo): solo plataforma
    (fallback conservado de r1)."""
    otra = GranoHoja(
        hoja_id=1,
        ad_group_id=9,
        familia_id=7,
        subfamilia_id=None,
        conteo=_conteo(2000, 42, "42000.00"),
    )
    conv = _conv_minima([otra])
    resultado = evidencia.evidencia_hoja(conv, 999, _cpc_fijo(30, "90.00"))
    assert isinstance(resultado, EvidenciaHoja)
    assert resultado.conversion is None
    assert resultado.previa.niveles == ("plataforma",)


def test_evidencia_hoja_sin_plataforma_abstiene():
    conv = _conv_minima([])
    assert evidencia.evidencia_hoja(conv, 999, _cpc_fijo(30, "90.00")) == "evidencia_insuficiente"


def test_sin_estimacion_sin_floats():
    """Todo numero que sale del modulo es Decimal o int (regla 4)."""
    ev = _evidencia_conteo(100, 0, "0.00")
    est = evidencia.estima_acos(ev, _TARGET)
    assert isinstance(est.acos_central, Decimal)
    assert isinstance(est.cvr_post, Decimal)
    assert isinstance(est.aov_post, Decimal)
    assert all(isinstance(p, Decimal) for p in est.p_sobre.values())
    assert isinstance(est.p_bajo, Decimal)


def test_parciales_igual_que_evidencia_hoja():
    """parciales + clasifica == evidencia_hoja (misma composicion que el
    replay usa sobre lo congelado; cero drift ciclo<->replay)."""
    conv = _conv_minima(
        [
            GranoHoja(
                hoja_id=5,
                ad_group_id=9,
                familia_id=7,
                subfamilia_id=None,
                conteo=_conteo(16, 0, "0.00"),
            ),
            GranoHoja(
                hoja_id=6,
                ad_group_id=10,
                familia_id=7,
                subfamilia_id=None,
                conteo=_conteo(500, 20, "20000.00"),
            ),
        ]
    )
    cpc = _cpc_fijo(30, "90.00")
    previa, conversion, cpc_medido = evidencia.parciales_evidencia(conv, 5, cpc)
    assert previa is not None and previa.familia_id == 7
    assert conversion is not None and conversion.clicks == 16
    assert cpc_medido is cpc
    assert evidencia.clasifica(previa, conversion, cpc_medido) == evidencia.evidencia_hoja(
        conv, 5, cpc
    )


# ---------------------------------------------------------------------------
# tools/compara_evidencia.py: nucleo puro (buckets + invariantes + vocabulario)
# ---------------------------------------------------------------------------


def test_compara_buckets_y_fold():
    from tools import compara_evidencia as ce

    filas = [
        {
            "id": 1,
            "kind": "bid",
            "motivo": "banda_menos_12",
            "factor": "-0.12",
            "evidencia_v2": {
                "veredicto": {"kind": "bid", "motivo": "banda_menos_12", "factor": "-0.12"}
            },
        },
        {
            "id": 2,
            "kind": "bid",
            "motivo": "banda_menos_12",
            "factor": "-0.12",
            "evidencia_v2": {
                "veredicto": {"kind": None, "motivo": "evidencia_insuficiente", "factor": None}
            },
        },
        {
            "id": 3,
            "kind": "bid",
            "motivo": "banda_menos_25_cero_ventas",
            "factor": "-0.25",
            "evidencia_v2": {
                "veredicto": {"kind": "bid", "motivo": "banda_menos_12", "factor": "-0.12"}
            },
        },
        {
            "id": 4,
            "kind": "pause",
            "motivo": "pause_umbral",
            "factor": None,
            "evidencia_v2": {
                "via": "pause_intacto",
                "veredicto": {"kind": "pause", "motivo": "pause_umbral"},
            },
        },
        {"id": 5, "kind": "bid", "motivo": "banda_menos_12", "factor": "-0.12"},
    ]
    resumen = ce.resume(filas)
    assert resumen["decisiones"] == 5
    assert resumen["buckets"] == {"mantiene": 2, "quita": 1, "cambia_banda": 1, "pre_a4": 1}
    # cero_ventas se foldea al renglon -25 (res B12)
    assert resumen["por_motivo_v1"]["banda_menos_25"] == {
        "mantiene": 0,
        "quita": 0,
        "cambia_banda": 1,
    }
    assert "NO MEDIBLE" in resumen["agrega_puro"]


def test_compara_vocabulario_cerrado_e_invariantes():
    from tools import compara_evidencia as ce

    assert (
        ce.clasifica(
            "bid",
            "banda_menos_12",
            "-0.12",
            {"kind": None, "motivo": "pause_economica_dato_faltante", "factor": None},
        )
        == "quita"
    )
    # Fila pause: el motivo congelado es eco del vivo (no se valida).
    assert (
        ce.clasifica(
            "pause", "pause_economica", None, {"kind": "pause", "motivo": "pause_economica"}
        )
        == "mantiene"
    )
    with pytest.raises(ValueError, match="vocabulario cerrado"):
        ce.clasifica("bid", "banda_menos_12", "-0.12", {"kind": "bid", "motivo": "motivo_futuro"})
    with pytest.raises(ValueError, match="invariante roto"):
        ce.clasifica("pause", "pause_umbral", None, {"kind": "bid", "motivo": "banda_menos_12"})
    with pytest.raises(ValueError, match="invariante roto"):
        ce.clasifica("bid", "banda_menos_12", "-0.12", {"kind": "pause", "motivo": "pause_umbral"})
