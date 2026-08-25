"""Umbral adaptativo de cortes por producto (CORTES 01, tasks 1.2/1.3).

Modulo PURO (cero IO; test_architecture lo sella con el resto del motor):
resuelve el umbral de clicks que consumen los motores de cortes. Vive FUERA
de hygiene porque bid (1.3) tambien lo consume y hygiene ya importa bid --
meterla ahi crearia un import circular; en windows violaria la frontera IO
(spec v3).

Numeros sellados (spec v3, dueno 2026-08-24): O_min=3, C_min=60, Z_min=14,
M=1.5, F_neg=40, F_pause=50. PISO (ronda 1 grok): umbral_final =
max(legacy_regla, bruto) con legacy 20 negative / 25 pause -- el adaptativo
solo puede SUBIR umbrales, jamas bajar de los actuales (sin piso, un
producto de conversion rapida -- 60 clicks / 6 ordenes -> expected 10 ->
umbral 15 -- quedaria MAS agresivo que hoy, contra el proposito del plan).

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
F_PAUSE = 50  # fallback pause (lo consume 1.3)
LEGACY_NEGATIVE = 20  # piso: el adaptativo jamas baja de aqui
LEGACY_PAUSE = 25  # piso pause (lo consume 1.3)

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


def umbral_corte(evidencia: EvidenciaAdGroup | None, regla: Regla) -> UmbralResuelto:
    """Resuelve el umbral de clicks de UNA regla de corte para el ad group
    de `evidencia`. La elegibilidad (orders>=O_MIN ∧ clicks>=C_MIN ∧
    fechas_distintas>=Z_MIN, todo inclusivo) se evalua ANTES de dividir;
    sin ella el umbral es el fallback F de la regla. SIEMPRE aplica el piso:
    max(LEGACY[regla], bruto) -- el adaptativo solo SUBE umbrales (sellado).
    `regla` fuera de {negative, pause} revienta ruidosamente."""
    if regla not in FALLBACK:
        raise ValueError(f"regla fuera del vocabulario sellado {{negative, pause}}: {regla!r}")
    elegible = (
        evidencia is not None
        and evidencia.orders is not None
        and evidencia.orders >= O_MIN
        and evidencia.clicks is not None
        and evidencia.clicks >= C_MIN
        and evidencia.fechas_distintas >= Z_MIN
    )
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
