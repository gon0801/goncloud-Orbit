"""Primitivas de evidencia jerarquica (BIDS 02 M.3: lo que quedo de la
politica por evidencia). PURO: cero IO (no importa psycopg, no conn, no
now(); cubierto por test_motor_puro_sin_io como el resto de
app/optimizer).

Quedan: gamma_p/gamma_q (las consume la politica nueva), Conteo/Previa
+ previa_jerarquica + resta_conteo (pliegue jerarquico), MIN_CLICS_CPC
y los 2 motivos de abstencion (vocabulario historico que traduce el
feed de decisiones). El juicio de bandas y el roll-up por familia se
borraron con evidencia_v2 (nunca decidio en vivo).

Toda la aritmetica corre bajo localcontext(prec=_PRECISION) con UN solo
valor en el modulo. Sin float, sin scipy (regla del motor puro).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

_PRECISION = 28  # unica precision del modulo (fold + estima + gamma)
_CORTE_SERIE = Decimal("1e-18")  # 10^-(prec-10): termino despreciable
_MAX_ITER_SERIE = 100000  # defensa; el rango operativo usa < 1000
_MAX_A_EXACTA = 10000  # cabeza O(a) mas alla esbordaria el ciclo; fail-loud

K_PREVIA = Decimal("1")  # seudo-ordenes del padre por nivel (calibrar en shadow)
MIN_CLICS_CPC = 20  # clics pagados al bid vigente para conocer el CPC

# Multiplicadores de banda: ESPEJO de bid.MULT_* (el ciclo de imports
# impide importarlos; tests/test_evidencia.py pinnea la igualdad).
MULT_BAJA_FUERTE = Decimal("1.35")
MULT_BAJA_SUAVE = Decimal("1.15")
MULT_SUBIDA = Decimal("0.85")

# Motivos de abstencion v2 (fuente unica; bid.py los re-exporta).
MOTIVO_EVIDENCIA_INSUFICIENTE = "evidencia_insuficiente"
MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE = "cpc_post_cambio_insuficiente"

# Motivos de banda v2 (literales; pineados contra bid._MOTIVO_BANDA).

Nivel = Literal["plataforma", "familia", "subfamilia", "ad_group", "hoja"]


@dataclass(frozen=True)
class Conteo:
    """Conversion de UN nivel en la ventana madura D-90..D-10. Metrica
    None = envenenada (algun grano NULL o negativo en la frontera; regla
    3): el pliegue la salta, jamas la cuenta como cero. Un conteo con
    orders None no aporta ni CVR ni AOV; con revenue None aporta CVR
    pero no AOV."""

    clicks: int | None
    orders: int | None
    ad_revenue: Decimal | None


@dataclass(frozen=True)
class Previa:
    """Previa resuelta para una hoja: el pliegue de sus ancestros (la HOJA
    no entra: entra como `conversion`, cero doble conteo). Invariante:
    cvr > 0 y aov > 0. `niveles` = los que aportaron, de arriba a abajo
    (auditoria). `familia_id`/`subfamilia_id` = mapeo resuelto del grano,
    haya aportado o no (el auditor cruza mapeo vs aporte)."""

    cvr: Decimal
    aov: Decimal
    niveles: tuple[Nivel, ...]
    familia_id: int | None = None
    subfamilia_id: int | None = None


def resta_conteo(acum: Conteo | None, grano: Conteo) -> Conteo | None:
    """Resta la hoja de UN nivel del roll-up (leave-one-out, A6): la previa
    de la hoja se pliega SIN su propio grano (cero doble conteo). Espejo
    de _suma: veneno None-OR por metrica (acum None -> None; metrica None
    en el grano con acum conocido -> None, fail-closed: imposible en el
    mismo snapshot porque el veneno baja del grano). Resto negativo ->
    None por metrica (fail-closed, jamas clamp a 0: el nivel agregado
    contiene a la hoja, un negativo es dato inconsistente). Resto total
    cero (la hoja ERA el nivel) -> None: el nivel queda ausente, espejo
    de enrolla_granos (nivel sin granos no entra al dict) y no aporta al
    pliegue. Pura, O(1), cero queries."""
    if acum is None:
        return None
    resto = Conteo(
        clicks=_resta_ent(acum.clicks, grano.clicks),
        orders=_resta_ent(acum.orders, grano.orders),
        ad_revenue=_resta_dec(acum.ad_revenue, grano.ad_revenue),
    )
    if resto.clicks == 0 and resto.orders == 0 and resto.ad_revenue == 0:
        return None
    return resto


def _resta_ent(a: int | None, b: int | None) -> int | None:
    """Resta leave-one-out entera, fail-closed: veneno None se propaga,
    resto negativo -> None (jamas clamp a 0: inventaria densidad)."""
    if a is None or b is None:
        return None
    return a - b if a >= b else None


def _resta_dec(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """Resta leave-one-out decimal, fail-closed (espejo de _resta_ent)."""
    if a is None or b is None:
        return None
    return a - b if a >= b else None


def previa_jerarquica(
    cadena: list[tuple[Nivel, Conteo | None]],
    *,
    familia_id: int | None = None,
    subfamilia_id: int | None = None,
) -> Previa | None:
    """Pliegue de arriba a abajo de UNA cadena ya construida. El leave-one-out
    NO vive aqui: el llamador lo aplica al armar la cadena (parciales_evidencia
    usa _cadena_niveles(..., resta=grano.conteo) para la hoja con historia; la
    hoja nueva hereda SIN resta porque no esta en los agregados). Este pliegue
    consume los conteos tal cual: cada nivel ya viaja menos el grano de la hoja
    (cero doble conteo es invariant del llamador via resta_conteo).
    cadena[0] es la plataforma (raiz): exige orders > 0, clicks > 0 y revenue > 0
    (si no, None: sin previa no hay posterior). Cada nivel presente encoge hacia
    el padre con K_PREVIA seudo-ordenes: cvr_n = (o + K)/(c + K/cvr_p),
    aov_n = (rev + K.aov_p)/(o + K). CVR y AOV se pliegan POR SEPARADO: nivel sin
    metricas para uno lo salta para ese (None), o clicks == 0 lo salta para CVR
    (ordenes sin clics son artefacto, no evidencia). Determinista en Decimal
    bajo _PRECISION."""
    if not cadena:
        return None
    raiz = cadena[0][1]
    if (
        raiz is None
        or raiz.orders is None
        or raiz.orders <= 0
        or raiz.clicks is None
        or raiz.clicks <= 0
        or raiz.ad_revenue is None
        or raiz.ad_revenue <= 0
    ):
        return None
    with localcontext(prec=_PRECISION):
        cvr = Decimal(raiz.orders) / Decimal(raiz.clicks)
        aov = raiz.ad_revenue / Decimal(raiz.orders)
        niveles: list[Nivel] = ["plataforma"]
        for nivel, conteo in cadena[1:]:
            if conteo is None:
                continue
            aporto = False
            if (
                conteo.orders is not None
                and conteo.orders >= 0
                and conteo.clicks is not None
                and conteo.clicks > 0
            ):
                # Conjugada Poisson-Gamma: K seudo-ORDENES en el numerador
                # (unidades: conteos) y K/cvr_p seudo-clics en el
                # denominador; la media previa implicita es cvr_p (K.cvr_p
                # en el numerador daria cvr_p^2: bug atrapado en review).
                cvr = (Decimal(conteo.orders) + K_PREVIA) / (
                    Decimal(conteo.clicks) + K_PREVIA / cvr
                )
                aporto = True
            if (
                conteo.orders is not None
                and conteo.orders >= 0
                and conteo.ad_revenue is not None
                and conteo.ad_revenue >= 0
            ):
                aov = (conteo.ad_revenue + K_PREVIA * aov) / (Decimal(conteo.orders) + K_PREVIA)
                aporto = True
            if aporto:
                niveles.append(nivel)
    return Previa(
        cvr=cvr,
        aov=aov,
        niveles=tuple(niveles),
        familia_id=familia_id,
        subfamilia_id=subfamilia_id,
    )


def gamma_p(a: Decimal, x: Decimal) -> Decimal:
    """Gamma incompleta regularizada inferior P(a, x), en Decimal.
    Cola (suma n >= a) si x < a+1; complemento de la cabeza (suma n <
    a) si no. Cada lado suma la probabilidad CHICA directamente y evita
    la cancelacion 1-Q / 1-P del lado contrario. `a` ENTERA >= 1
    (assert ruidoso: K_PREVIA entera + orders int; si K se recalibra
    fraccionario hay que implementar Lanczos)."""
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
