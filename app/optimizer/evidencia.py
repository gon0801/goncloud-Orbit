"""Gamma incompleta regularizada en Decimal (BIDS 02 M.3: lo que quedo de la
politica por evidencia). PURO: cero IO (no importa psycopg, no conn, no
now(); cubierto por test_motor_puro_sin_io como el resto de
app/optimizer).

Quedan: gamma_p/gamma_q (las consume la politica nueva) y los 2 motivos de
abstencion (vocabulario historico que traduce el feed de decisiones). El
pliegue jerarquico (Conteo/Previa/previa_jerarquica/resta_conteo), el juicio
de bandas y el roll-up por familia se borraron con evidencia_v2 (nunca
decidio en vivo).

Toda la aritmetica corre bajo localcontext(prec=_PRECISION) con UN solo
valor en el modulo. Sin float, sin scipy (regla del motor puro).
"""

from __future__ import annotations

from decimal import Decimal, localcontext

_PRECISION = 28  # unica precision del modulo (gamma)
_CORTE_SERIE = Decimal("1e-18")  # 10^-(prec-10): termino despreciable
_MAX_ITER_SERIE = 100000  # defensa; el rango operativo usa < 1000
_MAX_A_EXACTA = 10000  # cabeza O(a) mas alla esbordaria el ciclo; fail-loud

# Motivos de abstencion v2 (fuente unica; los traduce el feed de decisiones).
MOTIVO_EVIDENCIA_INSUFICIENTE = "evidencia_insuficiente"
MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE = "cpc_post_cambio_insuficiente"


def gamma_p(a: Decimal, x: Decimal) -> Decimal:
    """Gamma incompleta regularizada inferior P(a, x), en Decimal.
    Cola (suma n >= a) si x < a+1; complemento de la cabeza (suma n <
    a) si no. Cada lado suma la probabilidad CHICA directamente y evita
    la cancelacion 1-Q / 1-P del lado contrario. `a` ENTERA >= 1
    (assert ruidoso: llega de ceil/floor(pedidos) + 1; si `a` se vuelve
    fraccionaria hay que implementar Lanczos)."""
    _valida_gamma(a, x)
    if x == 0:
        return Decimal(0)
    with localcontext(prec=_PRECISION):
        if x < a + 1:
            return _gamma_p_serie(a, x)
        return Decimal(1) - _gamma_q_cabeza(a, x)


def gamma_q(a: Decimal, x: Decimal) -> Decimal:
    """Gamma incompleta regularizada superior Q(a, x) = 1 - P(a, x).
    Q directa (cabeza) en region x >= a+1: sin cancelacion 1-P cuando
    P ~ 1."""
    _valida_gamma(a, x)
    if x == 0:
        return Decimal(1)
    with localcontext(prec=_PRECISION):
        if x < a + 1:
            return Decimal(1) - _gamma_p_serie(a, x)
        return _gamma_q_cabeza(a, x)


def _valida_gamma(a: Decimal, x: Decimal) -> None:
    if a <= 0 or a != int(a):
        raise ValueError(f"gamma entera exige a entera >= 1, llego {a!r}")
    if x < 0:
        raise ValueError(f"gamma exige x >= 0, llego {x!r}")
    if a > _MAX_A_EXACTA:
        raise ArithmeticError(f"a = {a} excede el rango operativo {_MAX_A_EXACTA}")


def _gamma_p_serie(a: Decimal, x: Decimal) -> Decimal:
    """P(a, x) = e^-x . SUM_{n>=a} x^n/n!, arranque directo en n = a
    (pmf(a) = e^-x.x^a/a!; evita iterar a terminos). Parada: n > x (pasado
    el pico) y termino < corte. Tope ruidoso (fail-loud, jamas parcial)."""
    entero = int(a)
    pmf = (-x).exp() * (x**entero) / Decimal(_factorial(entero))
    total = pmf
    n = entero
    while True:
        n += 1
        pmf = pmf * x / Decimal(n)
        total += pmf
        if n > x and pmf < _CORTE_SERIE:
            return +total  # unario: redondea a la precision del contexto
        if n - entero > _MAX_ITER_SERIE:
            raise ArithmeticError(f"serie gamma sin converger (a={a}, x={x})")


def _factorial(n: int) -> int:
    resultado = 1
    for i in range(2, n + 1):
        resultado *= i
    return resultado


def _gamma_q_cabeza(a: Decimal, x: Decimal) -> Decimal:
    """Q(a, x) = e^-x . SUM_{n<a} x^n/n!: suma DIRECTA de a terminos,
    exacta, sin logica de convergencia. Desviacion del diseno (que pedia
    Lentz): la fraccion continua de Lentz tiene patologia PROBADA con a
    entera en Decimal exacto (en i == a el numerador an se anula, d.c ==
    1 persiste y todo del posterior es ~1 falso: convergencia fantasma);
    con a = 1 + orders la cabeza O(a) es mas simple y exacta."""
    entero = int(a)
    total = Decimal(1)
    termino = Decimal(1)
    for n in range(1, entero):
        termino = termino * x / Decimal(n)
        total += termino
    return (-x).exp() * total
