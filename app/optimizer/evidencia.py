"""Politica de pujas por evidencia (A4, contrafactual v2). PURO: cero IO
(no importa psycopg, no conn, no now(); cubierto por
test_motor_puro_sin_io como el resto de app/optimizer).

Fisica sellada (diseno runner-A + sintesis 2026-10-02): ACoS = CPC /
(CVR x AOV). Cada factor toma su ventana: CVR/AOV no dependen del bid
-> ventana madura larga D-90..D-10 con pliegue jerarquico plataforma ->
familia -> subfamilia -> ad group -> hoja; CPC depende del bid -> SOLO
dias pagados con el bid vigente (>= 20 clics). Las bandas se juzgan
sobre la posterior Gamma del CVR de la hoja: bajar si P(ACoS > m.t) >=
confianza de recorte, subir si P(ACoS < 0.85.t) >= confianza de subida
(A3, resueltas por el ciclo; este modulo jamas conoce los defaults).

Sin previa ni encogimiento, acos_central == cost/ad_revenue de la
ventana (la definicion sellada del repo); la previa solo cambia CUANTA
confianza hay. Posterior Gamma(a = K+o, b = K/cvr_previa + c): aprox.
Poisson valida porque CVR << 1. AOV puntual encogido (su posterior se
ignora: el CVR domina la incertidumbre; aproximacion aceptada).

Toda la aritmetica corre bajo localcontext(prec=_PRECISION) con UN solo
valor en el modulo: el replay re-deriva bit-exacto ante cualquier
contexto del llamador. Sin float, sin scipy (regla del motor puro).

Vocabulario: los 2 motivos de abstencion de este modulo se DEFINEN aqui
y bid.py los re-exporta (fuente unica, cero duplicados; el ciclo de
imports bid <-> evidencia lo prohibe al reves). Los motivos de banda
que factor_por_evidencia retorna son literales pineados por test contra
bid._MOTIVO_BANDA (misma razon); igual los multiplicadores de banda
contra bid.MULT_*.
"""

from __future__ import annotations

import datetime as dt
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
_MOTIVO_BANDA_MENOS_25 = "banda_menos_25"
_MOTIVO_BANDA_MENOS_12 = "banda_menos_12"
_MOTIVO_BANDA_MAS_15 = "banda_mas_15"

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
class GranoHoja:
    """UNA fila del grano de conversion_jerarquica: la hoja en la ventana
    madura + su mapeo pre-resuelto a la jerarquia (campana -> grupo ->
    slug -> familia; legacy sin eslabon = None)."""

    hoja_id: int
    ad_group_id: int | None
    familia_id: int | None
    subfamilia_id: int | None
    conteo: Conteo


@dataclass(frozen=True)
class MapeoHoja:
    """Mapeo ESTRUCTURAL hoja -> jerarquia (de ad_entity, sin metricas):
    la hoja nueva sin grano hereda la previa de SUS niveles (r3: antes
    caia a plataforma aunque su familia tuviera datos)."""

    hoja_id: int
    ad_group_id: int | None
    familia_id: int | None
    subfamilia_id: int | None


@dataclass(frozen=True)
class ConversionPlataforma:
    """DOS lecturas por plataforma y ciclo (grano + mapeo) + roll-up O(H).
    Dicts: nivel con granos; nivel sin granos o totalmente envenenado =
    AUSENTE (regla 3). `moneda` es la de la plataforma (el query filtra;
    fuente unica para el freeze). `mapeo_hoja` cubre TODAS las hojas
    (con o sin grano): sin el, la hoja nueva no heredaria (r3)."""

    moneda: str
    ventana_desde: dt.date
    ventana_hasta: dt.date
    plataforma: Conteo | None
    por_familia: dict[int, Conteo]
    por_subfamilia: dict[int, Conteo]
    por_ad_group: dict[int, Conteo]
    por_hoja: dict[int, GranoHoja]
    mapeo_hoja: dict[int, MapeoHoja]


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


@dataclass(frozen=True)
class CostoPorClic:
    """CPC pagado con el bid VIGENTE (medido por windows, juzgado aqui).
    `post_cambio` False = sin BID aplicado en la ventana (se usa entera).
    cost/clicks None = inmedible (veneno de frontera)."""

    cost: Decimal | None
    clicks: int | None
    desde: dt.date
    hasta: dt.date
    post_cambio: bool


@dataclass(frozen=True)
class EvidenciaHoja:
    """Todo lo que decide_bid v2 consume de una hoja. `conversion` None =
    hoja nueva (posterior = previa pura). Se congela entera en
    decision.inputs.evidencia_v2: el replay re-decide sin la DB."""

    previa: Previa
    conversion: Conteo | None
    cpc: CostoPorClic


@dataclass(frozen=True)
class EstimacionAcos:
    """Salida auditable (DERIVADA: no se congela; replay y dashboard la
    re-derivan exacta con estima_acos). `p_sobre` = P(ACoS > m.t) por
    multiplicador; `p_bajo` = P(ACoS < 0.85.t). `ordenes_efectivas` =
    K + orders (cuanto pesa la hoja vs la previa)."""

    acos_central: Decimal
    cvr_post: Decimal
    aov_post: Decimal
    p_sobre: dict[Decimal, Decimal]
    p_bajo: Decimal
    ordenes_efectivas: Decimal


def enrolla_granos(
    granos: list[GranoHoja],
    *,
    moneda: str,
    ventana_desde: dt.date,
    ventana_hasta: dt.date,
    mapeo: list[MapeoHoja] | tuple[MapeoHoja, ...] = (),
) -> ConversionPlataforma:
    """Roll-up puro O(H) del grano a niveles (incluida la raiz
    plataforma: suma de los granos, mismo veneno; sin granos -> None).
    Veneno por metrica: nivel con ALGUN grano envenenado en M -> M None
    en ese nivel (semantica identica al bool_and global por nivel).
    Nivel sin granos -> ausente del dict (regla 3). `mapeo` = estructura
    hoja -> jerarquia (incluye hojas sin grano: heredan, r3)."""
    por_familia: dict[int, Conteo] = {}
    por_subfamilia: dict[int, Conteo] = {}
    por_ad_group: dict[int, Conteo] = {}
    por_hoja: dict[int, GranoHoja] = {}
    mapeo_hoja = {hoja.hoja_id: hoja for hoja in mapeo}
    plataforma: Conteo | None = None
    for grano in granos:
        por_hoja[grano.hoja_id] = grano
        plataforma = _suma(plataforma, grano.conteo)
        if grano.ad_group_id is not None:
            por_ad_group[grano.ad_group_id] = _suma(
                por_ad_group.get(grano.ad_group_id), grano.conteo
            )
        if grano.familia_id is not None:
            por_familia[grano.familia_id] = _suma(por_familia.get(grano.familia_id), grano.conteo)
        if grano.subfamilia_id is not None:
            por_subfamilia[grano.subfamilia_id] = _suma(
                por_subfamilia.get(grano.subfamilia_id), grano.conteo
            )
    return ConversionPlataforma(
        moneda=moneda,
        ventana_desde=ventana_desde,
        ventana_hasta=ventana_hasta,
        plataforma=plataforma,
        por_familia=por_familia,
        por_subfamilia=por_subfamilia,
        por_ad_group=por_ad_group,
        por_hoja=por_hoja,
        mapeo_hoja=mapeo_hoja,
    )


def _suma(acumulado: Conteo | None, grano: Conteo) -> Conteo:
    """Suma un grano al acumulado con veneno por metrica (None OR)."""
    if acumulado is None:
        return Conteo(clicks=grano.clicks, orders=grano.orders, ad_revenue=grano.ad_revenue)
    return Conteo(
        clicks=_suma_ent(acumulado.clicks, grano.clicks),
        orders=_suma_ent(acumulado.orders, grano.orders),
        ad_revenue=_suma_dec(acumulado.ad_revenue, grano.ad_revenue),
    )


def _suma_ent(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return a + b


def _suma_dec(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return a + b


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
    """Pliegue de arriba a abajo. cadena[0] es la plataforma (raiz):
    exige orders > 0, clicks > 0 y revenue > 0 (si no, None: sin previa
    no hay posterior). Cada nivel presente encoge hacia el padre con
    K_PREVIA seudo-ordenes: cvr_n = (o + K)/(c + K/cvr_p),
    aov_n = (rev + K.aov_p)/(o + K). CVR y AOV se pliegan POR SEPARADO:
    nivel sin metricas para uno lo salta para ese (None), o clicks == 0
    lo salta para CVR (ordenes sin clics son artefacto, no evidencia).
    Determinista en Decimal bajo _PRECISION."""
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


def clasifica(
    previa: Previa | None,
    conversion: Conteo | None,
    cpc: CostoPorClic | None,
) -> EvidenciaHoja | str:
    """Arma la EvidenciaHoja o devuelve el motivo de abstencion. Orden
    pineado CPC -> previa -> conversion (el replay re-deriva con esta
    MISMA funcion: orden compartido, cero drift). Split por subsistema:
    rechazo lado-CPC -> cpc_post_cambio_insuficiente; lado
    previa/conversion -> evidencia_insuficiente. `conversion` None = hoja
    nueva (posterior = previa pura); presente exige las 3 metricas sanas
    (veneno parcial en la propia hoja = abstenerse, regla 3)."""
    if (
        cpc is None
        or cpc.clicks is None
        or cpc.clicks < MIN_CLICS_CPC
        or cpc.cost is None
        or cpc.cost <= 0
    ):
        return MOTIVO_CPC_POST_CAMBIO_INSUFICIENTE
    if previa is None:
        return MOTIVO_EVIDENCIA_INSUFICIENTE
    if conversion is not None and (
        conversion.clicks is None or conversion.orders is None or conversion.ad_revenue is None
    ):
        return MOTIVO_EVIDENCIA_INSUFICIENTE
    return EvidenciaHoja(previa=previa, conversion=conversion, cpc=cpc)


def _cadena_niveles(
    conv: ConversionPlataforma,
    *,
    ad_group_id: int | None,
    familia_id: int | None,
    subfamilia_id: int | None,
    resta: Conteo | None = None,
) -> list[tuple[Nivel, Conteo | None]]:
    """Cadena plataforma -> familia -> subfamilia -> ad_group con los
    conteos del roll-up (UNICO constructor: grano y mapeo comparten
    forma, cero drift entre hoja con y sin historia). Con `resta` (A6,
    leave-one-out), cada nivel PRESENTE (raiz incluida) viaja menos el
    grano de la hoja; los niveles con id None siguen None (la resta no
    inventa niveles)."""

    def _menos(nivel: Conteo | None) -> Conteo | None:
        if nivel is None or resta is None:
            return nivel
        return resta_conteo(nivel, resta)

    return [
        ("plataforma", _menos(conv.plataforma)),
        (
            "familia",
            _menos(conv.por_familia.get(familia_id)) if familia_id is not None else None,
        ),
        (
            "subfamilia",
            _menos(conv.por_subfamilia.get(subfamilia_id)) if subfamilia_id is not None else None,
        ),
        (
            "ad_group",
            _menos(conv.por_ad_group.get(ad_group_id)) if ad_group_id is not None else None,
        ),
    ]


def parciales_evidencia(
    conv: ConversionPlataforma,
    hoja_id: int,
    cpc: CostoPorClic | None,
) -> tuple[Previa | None, Conteo | None, CostoPorClic | None]:
    """Parciales as-measured para (previa, conversion, cpc): cada uno
    nullable por separado (el freeze los congela tal cual y el replay
    re-deriva con clasifica). Hoja CON grano: la previa se pliega
    leave-one-out (A6: la cadena viaja menos el grano de la hoja, cero
    doble conteo). Hoja ausente del grano = hoja nueva (conversion None,
    hereda previa de SUS niveles via mapeo_hoja SIN resta (no esta en
    los agregados; sin mapeo = desconocida total, solo plataforma).
    Funcion TOTAL: el pliegue no divide por cero por construccion (toda
    division lleva guarda)."""
    grano = conv.por_hoja.get(hoja_id)
    if grano is None:
        return _parciales_hoja_nueva(conv, hoja_id, cpc)
    cadena = _cadena_niveles(
        conv,
        ad_group_id=grano.ad_group_id,
        familia_id=grano.familia_id,
        subfamilia_id=grano.subfamilia_id,
        resta=grano.conteo,
    )
    previa = previa_jerarquica(
        cadena, familia_id=grano.familia_id, subfamilia_id=grano.subfamilia_id
    )
    return (previa, grano.conteo, cpc)


def _parciales_hoja_nueva(
    conv: ConversionPlataforma,
    hoja_id: int,
    cpc: CostoPorClic | None,
) -> tuple[Previa | None, Conteo | None, CostoPorClic | None]:
    """Hoja sin filas en la ventana madura: conversion None (posterior =
    previa pura) + cadena de SUS niveles estructurales (r3: la keyword
    nueva de familia conocida hereda su familia, no el pais)."""
    mapeo = conv.mapeo_hoja.get(hoja_id)
    if mapeo is None:
        cadena: list[tuple[Nivel, Conteo | None]] = [("plataforma", conv.plataforma)]
        return (previa_jerarquica(cadena), None, cpc)
    cadena = _cadena_niveles(
        conv,
        ad_group_id=mapeo.ad_group_id,
        familia_id=mapeo.familia_id,
        subfamilia_id=mapeo.subfamilia_id,
    )
    previa = previa_jerarquica(
        cadena, familia_id=mapeo.familia_id, subfamilia_id=mapeo.subfamilia_id
    )
    return (previa, None, cpc)


def evidencia_hoja(
    conv: ConversionPlataforma,
    hoja_id: int,
    cpc: CostoPorClic | None,
) -> EvidenciaHoja | str:
    """Evidencia de UNA hoja desde el roll-up precomputado + su CPC:
    parciales + clasifica (el replay usa la misma composicion sobre los
    parciales congelados: orden compartido, cero drift)."""
    previa, conversion, cpc_medido = parciales_evidencia(conv, hoja_id, cpc)
    return clasifica(previa, conversion, cpc_medido)


def estima_acos(ev: EvidenciaHoja, target_acos_pct: Decimal) -> EstimacionAcos:
    """Posterior del CVR de la hoja ~ Gamma(a = K+o, b = K/cvr_previa +
    c) + banda->CVR (cvr_m = CPC/(m.t.aov_post)): p_sobre[m] = P(CVR <
    cvr_m), p_bajo = P(CVR > cvr_0.85). Precondiciones (las garantiza
    clasifica): cpc.cost > 0, cpc.clicks >= 20, previa con cvr/aov > 0,
    conversion None o total. target > 0 (decide_bid lo valida antes)."""
    assert ev.cpc.cost is not None and ev.cpc.clicks is not None  # clasifica lo sella
    ordenes = ev.conversion.orders if ev.conversion is not None else 0
    clics = ev.conversion.clicks if ev.conversion is not None else 0
    ingreso = ev.conversion.ad_revenue if ev.conversion is not None else Decimal(0)
    assert ordenes is not None and clics is not None and ingreso is not None
    with localcontext(prec=_PRECISION):
        forma = K_PREVIA + Decimal(ordenes)
        tasa = K_PREVIA / ev.previa.cvr + Decimal(clics)
        aov_post = (ingreso + K_PREVIA * ev.previa.aov) / (Decimal(ordenes) + K_PREVIA)
        cvr_post = forma / tasa
        cpc_valor = ev.cpc.cost / Decimal(ev.cpc.clicks)
        objetivo = target_acos_pct / Decimal(100)
        acos_central = cpc_valor / (cvr_post * aov_post)
        p_sobre = {
            MULT_BAJA_FUERTE: gamma_p(
                forma, tasa * _cvr_banda(cpc_valor, MULT_BAJA_FUERTE, objetivo, aov_post)
            ),
            MULT_BAJA_SUAVE: gamma_p(
                forma, tasa * _cvr_banda(cpc_valor, MULT_BAJA_SUAVE, objetivo, aov_post)
            ),
        }
        p_bajo = gamma_q(forma, tasa * _cvr_banda(cpc_valor, MULT_SUBIDA, objetivo, aov_post))
    return EstimacionAcos(
        acos_central=acos_central,
        cvr_post=cvr_post,
        aov_post=aov_post,
        p_sobre=p_sobre,
        p_bajo=p_bajo,
        ordenes_efectivas=forma,
    )


def _cvr_banda(cpc: Decimal, mult: Decimal, objetivo: Decimal, aov: Decimal) -> Decimal:
    """Umbral de banda en CVR: ACoS > m.t <=> CVR < CPC/(m.t.aov)."""
    return cpc / (mult * objetivo * aov)


def factor_por_evidencia(
    est: EstimacionAcos, confianza_recorte: Decimal, confianza_subida: Decimal
) -> str:
    """Motivo de banda v2 (motivo-only: bid.py mapea motivo->factor con
    la inversa de _MOTIVO_BANDA; cero imports, cero duplicados).
    Precedencia sellada: -25 > -12 > +15. Exclusion mutua ESTRUCTURAL
    (no depende de los defaults): {CVR < cvr_1.15} y {CVR > cvr_0.85}
    son disjuntos con masa intermedia > 0 (densidad Gamma > 0 en
    (0, inf)), luego ambas disparan => conf_rec + conf_sub < 1,
    imposible con el piso configurable 0.50 + 0.50. Sin disparo ->
    evidencia_insuficiente (posterior inconclusa)."""
    if est.p_sobre[MULT_BAJA_FUERTE] >= confianza_recorte:
        return _MOTIVO_BANDA_MENOS_25
    if est.p_sobre[MULT_BAJA_SUAVE] >= confianza_recorte:
        return _MOTIVO_BANDA_MENOS_12
    if est.p_bajo >= confianza_subida:
        return _MOTIVO_BANDA_MAS_15
    return MOTIVO_EVIDENCIA_INSUFICIENTE


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
