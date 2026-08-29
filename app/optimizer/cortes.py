"""Umbral adaptativo de cortes por producto (CORTES 01, tasks 1.2/1.3/1.4).

Modulo PURO (cero IO; test_architecture lo sella con el resto del motor):
resuelve el umbral de clicks que consumen los motores de cortes. Vive FUERA
de hygiene porque bid (1.3) tambien lo consume y hygiene ya importa bid --
meterla ahi crearia un import circular; en windows violaria la frontera IO
(spec v3).

Numeros sellados (spec v3, dueno 2026-08-24): O_min=3, C_min=60, Z_min=14,
M=1.5, F_neg=40. CORTES 03 (dueno 2026-08-28; origen spot-check ORBIT 04
4.4 fila 30 / decision 774: 72 clicks / 25.21 USD / 0 ventas pauso
prematuramente): F_pause=100 y legacy pause=100 (antes 50/25 por CORTES 01).
PISO (ronda 1 grok): umbral_final = max(legacy_regla, bruto) con legacy
20 negative / 100 pause -- el piso pause aplica en AMBOS caminos (elegible y
fallback, via max()) y el adaptativo solo puede SUBIR umbrales, jamas bajar
de los actuales (sin piso, un producto de conversion rapida -- 60 clicks /
6 ordenes -> expected 10 -> umbral 15 -- quedaria MAS agresivo que hoy,
contra el proposito del plan; bajo pause ese ejemplo lo cubre el piso 100 y
bajo negative el legacy 20).

La evidencia es del AD GROUP (proxy de producto sellado: suma de sus hojas
keyword+product_target sobre la ventana literal D-90..D-10; windows.py,
task 1.1). La ELEGIBILIDAD va ANTES de la division: orders=0 jamas se
divide (una implementacion que divide primero solo explota ahi). Evidencia
None (grupo sin filas), envenenada (clicks/orders None por metrica) o por
debajo de CUALQUIERA de los tres minimos -> fallback F por regla con
elegible False (regla 3: grupo sin datos, historia corta o evidencia
envenenada NO califica; jamas un numero inventado).

ceil DEL PRODUCTO, jamas ceil-luego-multiplica: umbral_bruto =
ceil(expected x M) con expected = Decimal(clicks)/Decimal(orders); con
M=1.5 equivale al racional ceil(3*clicks/(2*orders)). math.ceil sobre
Decimal es exacto: cero float en todo el camino.

PISO DE COST del camino negative (CORTES 01 1.4, decision 5bis): la funcion
hermana `piso_corte` resuelve el umbral de DINERO del negative, por
PLATAFORMA (la moneda manda) y SOLO de ese camino (los pisos de cost de
pause 40/500 viven en bid; CORTES 03 los subio de 12/200, decision del
dueno 2026-08-28). Mismo contrato que el umbral:
elegibilidad 3/60/14 ANTES de dividir, AOV = ad_revenue/orders SOLO con
evidencia elegible y revenue sano (envenenado -> respaldo, jamas un AOV
inventado), bruto = AOV x K (K=1.0 sellado) o respaldo {us: 45, mx: 600},
y piso = max(legacy 8/130, bruto): el adaptativo solo SUBE pisos. La
INDEPENDENCIA entre ambas resoluciones esta sellada: un mismo grupo puede
llegar ELEGIBLE a umbral_corte (clicks/orders sanos) y a RESPALDO aqui
(revenue None) -- dos lecturas de la misma evidencia, sin acoplarse.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.optimizer.windows import EvidenciaAdGroup

# ---------------------------------------------------------------------------
# Numeros sellados (spec v3; el spec manda, esto es su unica fuente en codigo)
# ---------------------------------------------------------------------------

O_MIN = 3  # ordenes del grupo para calificar (>=)
C_MIN = 60  # clicks del grupo para calificar (>=)
Z_MIN = 14  # fechas distintas de la UNION de hojas para calificar (>=)
M = Decimal("1.5")  # multiplicador sobre expected_clicks
F_NEG = 40  # fallback negative (grupo no elegible)
F_PAUSE = 100  # fallback pause (lo consume 1.3); CORTES 03, antes 50
LEGACY_NEGATIVE = 20  # piso: el adaptativo jamas baja de aqui
LEGACY_PAUSE = 100  # piso pause (lo consume 1.3); CORTES 03, antes 25

# Historia congelada (cierre CORTES 03, decision del lead 2026-08-28): el
# umbral de clicks del pause que VIGIA antes de CORTES 01. SOLO lo consume
# el replay (_replay_bid en cycle.py) para filas sin inputs.corte; JAMAS el
# camino vivo (F_PAUSE/LEGACY_PAUSE siguen siendo el fallback/piso VIGENTES:
# 100/100).
REPLAY_PAUSE_CLICKS_PRE_CORTES01 = 25


# Piso de COST legacy del camino negative (1.4): vivia en hygiene, ahora
# tiene UNA fuente aqui junto a los demas sellados (hygiene lo importa). El
# piso adaptativo jamas baja de estos valores.
NEGATIVE_COST_MIN: dict[str, Decimal] = {
    "amazon_us": Decimal("8"),
    "amazon_mx": Decimal("130"),
}
K = Decimal("1.0")  # multiplicador del piso de cost sobre el AOV (5bis)
RESPALDO_PISO_NEGATIVE: dict[str, Decimal] = {  # grupo no elegible o revenue envenenado
    "amazon_us": Decimal("45"),
    "amazon_mx": Decimal("600"),
}

# Vocabulario cerrado de reglas: fallback y legacy por regla
FALLBACK: dict[str, int] = {"negative": F_NEG, "pause": F_PAUSE}
LEGACY: dict[str, int] = {"negative": LEGACY_NEGATIVE, "pause": LEGACY_PAUSE}

Regla = Literal["negative", "pause"]


@dataclass(frozen=True)
class UmbralResuelto:
    """Resultado de umbral_corte: TODO lo que el orquestador congela en
    `inputs.corte` (spec) sale de AQUI -- un solo calculo, una sola fuente.
    `umbral` es el FINAL (con piso aplicado), int: lo que el motor compara;
    `expected_clicks` es None cuando no califica (jamas un expected de un
    grupo que no dividio; se congela como string Decimal en cycle.py)."""

    umbral: int
    elegible: bool
    expected_clicks: Decimal | None


def _califica(evidencia: EvidenciaAdGroup | None) -> bool:
    """Elegibilidad sellada 3/60/14 (todo inclusivo, veneno None incluido):
    UNA fuente compartida por umbral_corte (1.2) y piso_corte (1.4) -- si
    alguna divergiera, el umbral y el piso hablarian de grupos distintos."""
    return (
        evidencia is not None
        and evidencia.orders is not None
        and evidencia.orders >= O_MIN
        and evidencia.clicks is not None
        and evidencia.clicks >= C_MIN
        and evidencia.fechas_distintas >= Z_MIN
    )


def umbral_corte(evidencia: EvidenciaAdGroup | None, regla: Regla) -> UmbralResuelto:
    """Resuelve el umbral de clicks de UNA regla de corte para el ad group
    de `evidencia`. La elegibilidad (orders>=O_MIN ∧ clicks>=C_MIN ∧
    fechas_distintas>=Z_MIN, todo inclusivo, via _califica) se evalua ANTES
    de dividir; sin ella el umbral es el fallback F de la regla. SIEMPRE
    aplica el piso: max(LEGACY[regla], bruto) -- el adaptativo solo SUBE
    umbrales (sellado). `regla` fuera de {negative, pause} revienta
    ruidosamente."""
    if regla not in FALLBACK:
        raise ValueError(f"regla fuera del vocabulario sellado {{negative, pause}}: {regla!r}")
    elegible = _califica(evidencia)
    expected: Decimal | None = None
    if elegible:
        assert evidencia is not None  # elegible True implica evidencia con clicks/orders
        expected = Decimal(evidencia.clicks) / Decimal(evidencia.orders)
        bruto = math.ceil(expected * M)
    else:
        bruto = FALLBACK[regla]
    return UmbralResuelto(
        umbral=max(LEGACY[regla], bruto),
        elegible=elegible,
        expected_clicks=expected,
    )


@dataclass(frozen=True)
class PisoResuelto:
    """Resultado de piso_corte (CORTES 01 1.4): el piso de COST del camino
    negative que el orquestador pasa a hygiene y congela en inputs.corte
    (`piso_cost_usado`/`aov` como string Decimal). `aov` es None cuando no
    se calculo (grupo no elegible o revenue envenenado): jamas un AOV de un
    grupo que no dividio (regla 3)."""

    piso_cost: Decimal
    aov: Decimal | None


def piso_corte(evidencia: EvidenciaAdGroup | None, platform: str) -> PisoResuelto:
    """Resuelve el piso de COST del camino negative para el ad group de
    `evidencia` en la moneda de `platform` (5bis). Mismo contrato que
    umbral_corte: elegibilidad 3/60/14 ANTES de dividir; con ella y
    ad_revenue sano, AOV = ad_revenue/orders (elegible implica orders int
    >= O_MIN: division segura) y bruto = AOV x K (K=1.0); sin elegibilidad o
    con revenue envenenado, bruto = RESPALDO_PISO_NEGATIVE[platform]. El
    piso final SIEMPRE aplica max(NEGATIVE_COST_MIN[platform], bruto): el
    adaptativo solo SUBE pisos. INDEPENDIENTE de umbral_corte: este calculo
    no exige revenue (un grupo elegible con revenue None responde respaldo,
    no inventa AOV). `platform` fuera de {amazon_us, amazon_mx} revienta
    ruidosamente."""
    if platform not in NEGATIVE_COST_MIN:
        raise ValueError(
            f"plataforma fuera del vocabulario sellado {{amazon_us, amazon_mx}}: {platform!r}"
        )
    aov: Decimal | None = None
    if _califica(evidencia):
        assert evidencia is not None  # _califica True implica evidencia con clicks/orders
        if evidencia.ad_revenue is not None:
            aov = evidencia.ad_revenue / Decimal(evidencia.orders)
    bruto = aov * K if aov is not None else RESPALDO_PISO_NEGATIVE[platform]
    return PisoResuelto(piso_cost=max(NEGATIVE_COST_MIN[platform], bruto), aov=aov)
