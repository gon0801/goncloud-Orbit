"""Tests de lo que quedo de evidencia (BIDS 02 M.3): Gamma contra oraculos.
El pliegue jerarquico, el juicio de bandas y el roll-up por familia se
borraron con evidencia_v2 (nunca decidio en vivo); sus pruebas se fueron
con ellos.
"""

from decimal import Decimal, localcontext

import pytest

from app.optimizer import evidencia

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
