"""Tests del umbral adaptativo de cortes (`app.optimizer.cortes`, CORTES 01 1.2).

PUROS (sin DB): un test por candado con nombre descriptivo (feedback de 1.1:
el mega-test cubre todo pero localiza mal las fallas). Cada minimo de
elegibilidad discrimina SOLO (regla 9: 2/3 ordenes, 59/60 clicks, 13/14
fechas), el ceil es DEL PRODUCTO (jamais ceil-luego-multiplica), el PISO
legacy gana cuando el adaptativo baja, orders=0 JAMAS se divide (la
elegibilidad va ANTES de la division) y la evidencia envenenada (None por
metrica) o ausente cae a fallback, jamas a un numero inventado (regla 3).

Numeros sellados (spec v3): O_min=3, C_min=60, Z_min=14, M=1.5, F_neg=40,
F_pause=50, legacy 20 negative / 25 pause (el piso solo SUBE umbrales).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.optimizer import cortes
from app.optimizer.windows import EvidenciaAdGroup


def _evid(*, clicks, orders, fechas) -> EvidenciaAdGroup:
    """Evidencia sintetica del grupo con lo UNICO que la regla consume
    (clicks/orders/fechas_distintas); el resto son valores neutros."""
    return EvidenciaAdGroup(
        ad_group_id=1,
        ventana_desde=dt.date(2026, 5, 24),
        ventana_hasta=dt.date(2026, 8, 12),
        fechas_distintas=fechas,
        metric_currency="USD",
        cost=Decimal("0"),
        ad_revenue=Decimal("0"),
        revenue_same_sku=Decimal("0"),
        impressions=0,
        clicks=clicks,
        orders=orders,
        observed_at_max=dt.datetime(2026, 8, 12, 1, tzinfo=dt.UTC),
    )


def test_evidencia_none_fallback_con_evidencia_null():
    """Grupo ausente del dict de evidencia (sin datos en la ventana):
    fallback por regla con evidencia null en el freeze, elegible False y
    expected None (regla 3: jamas un numero inventado)."""
    res = cortes.umbral_corte(None, "negative")
    assert res.umbral == cortes.F_NEG
    assert res.elegible is False
    assert res.expected_clicks is None
    res_pause = cortes.umbral_corte(None, "pause")
    assert res_pause.umbral == cortes.F_PAUSE


def test_minimo_ordenes_discrimina_solo():
    """orders=2 no califica (fallback 40); orders=3 si (mismo grupo en lo
    demas). Solo el minimo de ordenes cambia el resultado."""
    dos = cortes.umbral_corte(_evid(clicks=100, orders=2, fechas=20), "negative")
    tres = cortes.umbral_corte(_evid(clicks=100, orders=3, fechas=20), "negative")
    assert dos.elegible is False and dos.umbral == cortes.F_NEG
    assert tres.elegible is True
    # 100/3 -> expected 33.33... x 1.5 = 50 -> ceil 50
    assert tres.umbral == 50


def test_minimo_clicks_discrimina_solo():
    """clicks=59 no califica (fallback 40); clicks=60 si. Solo el minimo de
    clicks cambia el resultado."""
    c59 = cortes.umbral_corte(_evid(clicks=59, orders=3, fechas=14), "negative")
    c60 = cortes.umbral_corte(_evid(clicks=60, orders=3, fechas=14), "negative")
    assert c59.elegible is False and c59.umbral == cortes.F_NEG
    assert c60.elegible is True
    # 60/3 -> expected 20 x 1.5 = 30 -> umbral 30
    assert c60.umbral == 30


def test_minimo_fechas_discrimina_solo():
    """fechas_distintas=13 no califica (fallback 40); 14 si. Solo Z_min
    cambia el resultado (sumar conteos por hoja inflaria Z y calificaria
    grupos inmaduros)."""
    z13 = cortes.umbral_corte(_evid(clicks=60, orders=3, fechas=13), "negative")
    z14 = cortes.umbral_corte(_evid(clicks=60, orders=3, fechas=14), "negative")
    assert z13.elegible is False and z13.umbral == cortes.F_NEG
    assert z14.elegible is True and z14.umbral == 30


def test_orders_cero_jamas_divide():
    """orders=0 con clicks/fechas de sobra: NO elegible SIN dividir nunca
    (regla 9: una implementacion que divide antes de la elegibilidad solo
    explota aqui -- ZeroDivisionError o Infinity)."""
    res = cortes.umbral_corte(_evid(clicks=10_000, orders=0, fechas=81), "negative")
    assert res.elegible is False
    assert res.umbral == cortes.F_NEG
    assert res.expected_clicks is None


def test_ceil_fraccionario_61_clicks_3_ordenes_da_31():
    """DoD: 61/3 -> expected 20.33... x 1.5 = 30.5 -> ceil 31 (piso 20 no
    alcanza; el FINAL es 31)."""
    res = cortes.umbral_corte(_evid(clicks=61, orders=3, fechas=14), "negative")
    assert res.elegible is True
    assert res.umbral == 31
    assert res.expected_clicks == Decimal(61) / Decimal(3)


def test_ceil_entero_expected_50_da_75():
    """DoD: expected entero 50 (150/3) x 1.5 = 75 exacto -> umbral 75."""
    res = cortes.umbral_corte(_evid(clicks=150, orders=3, fechas=20), "negative")
    assert res.umbral == 75


def test_ceil_del_producto_jamais_ceil_luego_multiplica():
    """64/3: expected 21.33... x 1.5 = 32 EXACTO (racional 3*64/(2*3)) ->
    umbral 32. ceil-luego-multiplica daria ceil(21.33)=22 -> 22x1.5=33:
    inventaria umbral MAS DURO que el sellado (regla 9)."""
    res = cortes.umbral_corte(_evid(clicks=64, orders=3, fechas=14), "negative")
    assert res.umbral == 32


def test_piso_legacy_negative_gana_sobre_el_adaptativo():
    """DoD: elegible con expected 10 (60/6) -> 10x1.5=15 < legacy 20 ->
    umbral FINAL 20. Sin el max() el umbral seria 15: MAS agresivo que hoy,
    contra el proposito del plan (piso sellado por el dueno)."""
    res = cortes.umbral_corte(_evid(clicks=60, orders=6, fechas=20), "negative")
    assert res.elegible is True
    assert res.umbral == cortes.LEGACY_NEGATIVE == 20


def test_piso_legacy_pause_es_25():
    """Mismo grupo bajo regla pause: expected 10 -> 15 < legacy 25 -> 25
    (el piso es POR REGLA; pause la consume 1.3)."""
    res = cortes.umbral_corte(_evid(clicks=60, orders=6, fechas=20), "pause")
    assert res.umbral == cortes.LEGACY_PAUSE == 25


def test_fallback_pause_es_50():
    """Grupo no elegible bajo pause: fallback F_pause=50 (lo consume 1.3)."""
    res = cortes.umbral_corte(_evid(clicks=59, orders=3, fechas=14), "pause")
    assert res.umbral == cortes.F_PAUSE == 50


def test_veneno_clicks_none_no_califica():
    """Evidencia con clicks envenenado (None por metrica del grupo, regla 3
    de windows): NO califica -> fallback. Jamas se lee como 0 ni se divide."""
    res = cortes.umbral_corte(_evid(clicks=None, orders=5, fechas=30), "negative")
    assert res.elegible is False
    assert res.umbral == cortes.F_NEG
    assert res.expected_clicks is None


def test_veneno_orders_none_no_califica():
    """orders envenenado (None): idem -- sin el dato no hay elegibilidad ni
    expected inventado."""
    res = cortes.umbral_corte(_evid(clicks=100, orders=None, fechas=30), "negative")
    assert res.elegible is False
    assert res.umbral == cortes.F_NEG


def test_regla_fuera_de_vocabulario_rechaza():
    """`regla` es un vocabulario CERRADO {negative, pause}: cualquier otra
    revienta ruidosamente (jamas un fallback silencioso a una regla que
    nadie sello)."""
    with pytest.raises(ValueError, match="negative|pause"):
        cortes.umbral_corte(None, "harvest")


def test_umbral_resuelto_es_int_y_expected_decimal():
    """Tipos sellados: umbral int (columna/compare del motor), expected
    Decimal (se congela como string en inputs.corte)."""
    res = cortes.umbral_corte(_evid(clicks=61, orders=3, fechas=14), "negative")
    assert isinstance(res.umbral, int)
    assert isinstance(res.expected_clicks, Decimal)
