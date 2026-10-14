"""Apagador (S.2) y fusible (S.3) del motor de precios (REPRICING 02).

`app/precio/compuerta.py`: `modo_efectivo`, `evaluar` (fusible por universo
e insumo sistemico), `es_nueva`, `salto`, `armar_deriva` y `Cortacircuito`.
Puro, sin DB.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.precio.compuerta import (
    Abierta,
    Anterior,
    Cortacircuito,
    Deriva,
    Intencion,
    Retenida,
    armar_deriva,
    es_nueva,
    evaluar,
    modo_efectivo,
    salto,
)
from app.precio.config import leer_config_motor


@pytest.mark.parametrize(
    "global_, universo, goal, esperado",
    [
        ("live", "live", "live", "live"),
        ("shadow", "live", "live", "shadow"),
        ("live", "shadow", "live", "shadow"),
        ("live", "live", "shadow", "shadow"),
        ("shadow", "shadow", "live", "shadow"),
        ("off", "live", "live", "off"),
        ("live", "off", "live", "off"),
        ("live", "live", "off", "off"),
        ("off", "off", "off", "off"),
        ("shadow", "off", "live", "off"),
    ],
)
def test_s2_modo_efectivo_es_el_menor_de_los_tres(global_, universo, goal, esperado):
    """S.2: el efectivo es el minimo en off < shadow < live, gane quien gane."""
    assert modo_efectivo(global_, universo, goal) == esperado


@pytest.mark.parametrize("lugar", ["global", "universo", "goal"])
@pytest.mark.parametrize("malo", ["turbo", "", None, "LIVE", "apagado"])
def test_s2_modo_efectivo_vocabulario_cerrado(lugar, malo):
    """S.2: fuera de off/shadow/live, ValueError que nombra el lugar."""
    modos = {"global": "live", "universo": "live", "goal": "live"}
    modos[lugar] = malo
    with pytest.raises(ValueError, match=lugar):
        modo_efectivo(modos["global"], modos["universo"], modos["goal"])


# ---------------------------------------------------------------------------
# S.3: fusible por universo, insumo sistemico y corte (puros)
# ---------------------------------------------------------------------------


def _motor(**ajustes):
    """ConfigMotor con los valores de la 0054 (fusible 0.50/10, salto
    0.05/0.30, corte 5) salvo ajustes."""
    settings = {
        "precio_modo_global": "live",
        "precio_modo_universo": {"amazon_mx/fba": "live"},
        "precio_goal_min_pct": "0.10",
        "precio_goal_max_pct": "0.60",
        "precio_freno_dias_error": 3,
        "precio_catalogo_max_dias_sin_reportar": 3,
        "precio_aviso_dias_sin_evaluar": 3,
        "precio_fusible_frac": "0.50",
        "precio_fusible_min_movimientos": 10,
        "precio_insumo_salto_pct": "0.05",
        "precio_insumo_salto_frac": "0.30",
        "precio_corte_errores_consecutivos": 5,
        "precio_senal_trafico_min": 200,
        "precio_cohorte_caida_pct": "0.25",
        "precio_envio_ventana_dias": 180,
    }
    settings.update(ajustes)
    return leer_config_motor(settings)


def _intenciones(nuevas: int, *, desde: int = 0) -> list[Intencion]:
    return [
        Intencion(
            decision_id=desde + i,
            listing_id=desde + i,
            canal="fba",
            nueva=True,
            liberada=False,
        )
        for i in range(nuevas)
    ]


def test_s3_fusible_80_de_158_retienen_79_pasan():
    """S.3: 80 nuevas sobre 158 medidas retienen (mayor ESTRICTO: 80 >
    0.50*158 = 79); 79 no (79 > 79 es falso)."""
    reten = evaluar(_intenciones(80), medidas=158, derivas=(), config=_motor())
    assert isinstance(reten, Retenida)
    assert reten.causa == "movimiento_masivo"
    assert (reten.medidas, reten.nuevas) == (158, 80)
    assert reten.insumo is None
    assert reten.detalle and "80" in reten.detalle and "158" in reten.detalle
    pasa = evaluar(_intenciones(79), medidas=158, derivas=(), config=_motor())
    assert pasa == Abierta(medidas=158, nuevas=79)


def test_s3_fusible_exige_minimo_de_movimientos():
    """S.3: 3 nuevas sobre 3 medidas superan la fraccion pero no el minimo
    de 10: pasan."""
    assert evaluar(_intenciones(3), medidas=3, derivas=(), config=_motor()) == Abierta(
        medidas=3, nuevas=3
    )


def test_s3_insumo_sistemico_gana_a_movimiento_masivo():
    """S.3: con los dos disparos, gana el que dice QUE insumo salto."""
    costo = Deriva(canal="fba", insumo="costo", comparables=158, con_salto=60)
    veredicto = evaluar(_intenciones(80), medidas=158, derivas=(costo,), config=_motor())
    assert isinstance(veredicto, Retenida)
    assert veredicto.causa == "insumo_sistemico"
    assert veredicto.insumo == "costo"
    assert veredicto.detalle and "costo" in veredicto.detalle


def test_s3_insumo_sistemico_umbrales():
    """S.3: el insumo dispara con mayor estricto y minimo, igual que el
    fusible (60 de 158 con frac 0.30 dispara; 47 no; 9 de 10 no por minimo)."""
    config = _motor()
    dispara = Deriva(canal="fba", insumo="costo", comparables=158, con_salto=60)
    assert isinstance(
        evaluar(_intenciones(10), medidas=158, derivas=(dispara,), config=config), Retenida
    )
    # 47 > 0.30*158 = 47.4 es falso.
    no_dispara = Deriva(canal="fba", insumo="costo", comparables=158, con_salto=47)
    assert evaluar(_intenciones(10), medidas=158, derivas=(no_dispara,), config=config) == (
        Abierta(medidas=158, nuevas=10)
    )
    pocas = Deriva(canal="fba", insumo="costo", comparables=10, con_salto=9)
    assert evaluar(_intenciones(1), medidas=10, derivas=(pocas,), config=config) == Abierta(
        medidas=10, nuevas=1
    )


def test_s3_liberadas_no_cuentan_en_ninguno_de_los_dos():
    """S.3: una liberada no es intencion nueva (79 efectivas de 80 pasan) y
    tampoco entra en comparables ni con_salto de la deriva."""
    intenciones = _intenciones(80)
    intenciones[0] = Intencion(decision_id=0, listing_id=0, canal="fba", nueva=True, liberada=True)
    assert evaluar(intenciones, medidas=158, derivas=(), config=_motor()) == Abierta(
        medidas=158, nuevas=79
    )
    pares = [(Decimal(100), Decimal(120), False)] * 50
    pares += [(Decimal(100), Decimal(120), True)] * 10
    pares += [(Decimal(100), Decimal(100), False)] * 98
    deriva = armar_deriva("fba", "costo", pares, pct=Decimal("0.05"))
    assert (deriva.comparables, deriva.con_salto) == (148, 50)


@pytest.mark.parametrize(
    "anterior, goal_hoy, esperada",
    [
        (None, 7, True),  # sin ayer, toda intencion es nueva
        (Anterior("subir", None, 7), 7, False),  # segundo escalon al mismo goal
        (Anterior("bajar", None, 7), 7, False),
        (Anterior("mantener", "cooldown", 7), 7, False),  # ola de 7 dias
        (Anterior("subir", None, 9), 7, True),  # otro goal: nueva
        (Anterior("mantener", "en_tolerancia", 7), 7, True),  # la ola termino
        (Anterior("no_evaluado", "precio_ausente", 7), 7, True),
        (Anterior("frenado", "api_error", 7), 7, True),
        (Anterior("subir", None, None), 7, True),  # goal previo desconocido
        (Anterior("subir", None, 7), None, True),  # goal de hoy desconocido
    ],
)
def test_s3_segunda_ola_al_mismo_goal_no_es_nueva(anterior, goal_hoy, esperada):
    """S.3: solo subir/bajar o mantener(cooldown) hacia el MISMO goal hacen
    que la intencion de hoy no sea nueva."""
    assert es_nueva(anterior, goal_id=goal_hoy) is esperada


@pytest.mark.parametrize(
    "antes, ahora, esperado",
    [
        (Decimal(100), Decimal(120), True),  # +20 % con salto 5 %
        (Decimal(100), Decimal(105), False),  # igual al umbral no salta
        (Decimal(100), Decimal("104.99"), False),
        (Decimal(100), Decimal(80), True),  # el salto es en cualquier direccion
        (None, Decimal(120), None),  # ausente no compara
        (Decimal(100), None, None),
        (Decimal(0), Decimal(0), False),
        (Decimal(0), Decimal(5), True),
    ],
)
def test_s3_salto_estricto_y_no_comparable(antes, ahora, esperado):
    """S.3: salto relativo ESTRICTO sobre el pct; sin los dos lados no hay
    comparacion."""
    assert salto(antes, ahora, pct=Decimal("0.05")) == esperado


def test_s3_cortacircuito_abre_tras_n_y_exito_lo_reinicia():
    """S.3: 5 errores seguidos abren; un exito (enviado o confirmado)
    reinicia; saltado no mueve el contador; estado fuera de vocabulario
    revienta; `tras` no muta."""
    corte = Cortacircuito(seguidos=0, limite=5)
    assert corte.abierto is False
    for _ in range(4):
        corte = corte.tras("error")
        assert corte.abierto is False
    assert corte.tras("saltado").seguidos == 4
    abierto = corte.tras("error")
    assert abierto.abierto is True
    assert corte.abierto is False  # el valor es inmutable
    assert abierto.tras("enviado") == Cortacircuito(seguidos=0, limite=5)
    assert abierto.tras("confirmado") == Cortacircuito(seguidos=0, limite=5)
    with pytest.raises(ValueError, match="boom"):
        corte.tras("boom")
    with pytest.raises(ValueError, match="limite"):
        Cortacircuito(seguidos=0, limite=0)
