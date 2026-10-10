"""Tests de lo que quedo de evidencia (BIDS 02 M.3): Gamma contra oraculos,
pliegue jerarquico (previa_jerarquica) y resta_conteo. El juicio de bandas
y el roll-up por familia se borraron con evidencia_v2 (nunca decidio en
vivo); sus pruebas se fueron con ellos.
"""

from decimal import Decimal, localcontext

import pytest

from app.optimizer import evidencia
from app.optimizer.evidencia import Conteo

TOL = Decimal("1e-12")


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


# Pliegue jerarquico
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


def test_resta_conteo_veneno_negativo_y_vacio():
    """A6-M3 (clamp-a-0): la resta es fail-closed por metrica, jamas
    clamp a 0; A6-M4 (suma-parcial): el veneno se propaga, no se ignora."""
    acum = _conteo(516, 20, "20000.00")
    assert evidencia.resta_conteo(acum, _conteo(16, 0, "0.00")) == _conteo(500, 20, "20000.00")
    assert evidencia.resta_conteo(None, _conteo(16, 0, "0.00")) is None
    # Grano envenenado en orders con acum conocido -> orders None (el
    # resto de metricas si resta: veneno OR por metrica, espejo de _suma).
    assert evidencia.resta_conteo(
        acum, Conteo(clicks=16, orders=None, ad_revenue=Decimal("0.00"))
    ) == Conteo(clicks=500, orders=None, ad_revenue=Decimal("20000.00"))
    # Resto negativo (inconsistente: el nivel contiene a la hoja) -> None
    # por metrica, NO 0 (un 0 contaria como evidencia de "sin clics").
    assert evidencia.resta_conteo(_conteo(10, 1, "100.00"), _conteo(20, 0, "0.00")) == Conteo(
        clicks=None, orders=1, ad_revenue=Decimal("100.00")
    )
    # La hoja ERA el nivel -> ausente (no aporta al pliegue).
    assert evidencia.resta_conteo(_conteo(16, 0, "0.00"), _conteo(16, 0, "0.00")) is None
