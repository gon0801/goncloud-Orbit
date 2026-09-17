"""Banco de pruebas de las reglas puras (REPRICING 01 A.2).

Parte de los numeros del acta 0.3: P=116, I=100, C=40, F=15, L=0, R=2.50
-> contribucion 42.50, con POLITICA_FBA_MX_SELLADA (iva_divisor 1.16,
isr_tasa 0.025, precio_incluye_iva True). Todo en Decimal.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.estimacion_venta import DetalleFee
from app.precio.tipos import (
    MOTIVOS_FRENADO,
    MOTIVOS_GOAL_INALCANZABLE,
    MOTIVOS_MANTENER,
    MOTIVOS_NO_EVALUADO,
    RESULTADOS,
    SUBMOTIVOS_SIN_DATO,
    Importe,
    SenalVentas,
    exigir_decimal,
    motivo_permitido,
)

MXN = "MXN"


def imp(valor: str, moneda: str = MXN) -> Importe:
    return Importe(Decimal(valor), moneda)


# ---------------------------------------------------------------- tipos


def test_tipos_seis_resultados_exactos():
    assert RESULTADOS == (
        "subir",
        "bajar",
        "mantener",
        "no_evaluado",
        "goal_inalcanzable",
        "frenado",
    )


def test_tipos_importe_rechaza_float():
    with pytest.raises(TypeError):
        Importe(116.01, MXN)  # type: ignore[arg-type]


def test_tipos_importe_rechaza_int_y_moneda_vacia():
    with pytest.raises(TypeError):
        Importe(116, MXN)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Importe(Decimal("116"), "  ")


def test_tipos_exigir_decimal_rechaza_float_y_no_finito():
    with pytest.raises(TypeError):
        exigir_decimal(0.30, campo="goal")
    with pytest.raises(ValueError):
        exigir_decimal(Decimal("NaN"), campo="goal")


def test_tipos_vocabulario_mantener_cerrado():
    assert motivo_permitido("mantener", "en_tolerancia")
    assert motivo_permitido("mantener", "ventas_sin_dato:racha_incompleta")
    assert not motivo_permitido("mantener", "ventas_sin_dato:inventado")
    assert not motivo_permitido("mantener", "en_tolerancia:extra")
    assert not motivo_permitido("mantener", None)


def test_tipos_vocabulario_no_evaluado_cerrado():
    assert motivo_permitido("no_evaluado", "fee_error:fee_http_429")
    assert not motivo_permitido("no_evaluado", "fee_error")
    assert not motivo_permitido("no_evaluado", "inventado")
    for motivo in (
        "oferta_desactualizada",
        "fx_ausente",
        "fee_ausente",
        "impuesto_fee_pendiente",
        "precio_divergente",
        "moneda_divergente",
    ):
        assert motivo_permitido("no_evaluado", motivo), motivo
    assert MOTIVOS_NO_EVALUADO and MOTIVOS_MANTENER


def test_tipos_subir_bajar_sin_motivo():
    assert motivo_permitido("subir", None)
    assert not motivo_permitido("subir", "en_tolerancia")
    assert motivo_permitido("bajar", None)
    for motivo in MOTIVOS_GOAL_INALCANZABLE:
        assert motivo_permitido("goal_inalcanzable", motivo)
    for motivo in MOTIVOS_FRENADO:
        assert motivo_permitido("frenado", motivo)
    assert len(SUBMOTIVOS_SIN_DATO) == 8


def test_tipos_senal_sin_dato_exige_submotivo_conocido():
    with pytest.raises(ValueError):
        SenalVentas("sin_dato", "inventado", 0, 0, 0, 0, 0)
    with pytest.raises(ValueError):
        SenalVentas("perdiendo", "racha_incompleta", 1, 1, 1, 1, 3)


def test_tipos_detalle_fee_reexportado_sin_io():
    det = DetalleFee("ReferralFee", Decimal("15"), None, ())
    assert det.fee_type == "ReferralFee"


HOY = date(2026, 9, 17)
AHORA = datetime(2026, 9, 17, 13, 10, tzinfo=UTC)


# ---------------------------------------------------------------- config

BASE_CONFIG = {
    "precio_caida_ventas_pct": "0.40",
    "precio_senal_dias": 3,
    "precio_u60_min": 20,
    "precio_fechas_excluidas": [],
    "precio_escalon_max_pct": "0.10",
    "precio_movimiento_min_pct": "0.01",
    "precio_movimiento_min_abs_mxn": "1.00",
    "precio_movimiento_min_abs_usd": "0.10",
    "precio_tolerancia": "0.005",
    "precio_dias_entre_cambios": 7,
    "precio_freno_cambios": 3,
    "precio_divergencia_max_pct": "0.01",
}


def cfg(**cambios):
    from app.precio.config import leer_config

    base = dict(BASE_CONFIG)
    base.update(cambios)
    return leer_config(base)


def test_config_lee_valores_iniciales_del_plan():
    from app.precio.config import leer_config

    c = leer_config(BASE_CONFIG)
    assert c.caida_ventas_pct == Decimal("0.40")
    assert c.senal_dias == 3
    assert c.u60_min == 20
    assert c.fechas_excluidas == ()
    assert c.tolerancia == Decimal("0.005")


def test_config_clave_ausente_nombra_la_clave():
    from app.precio.config import leer_config

    base = dict(BASE_CONFIG)
    del base["precio_tolerancia"]
    with pytest.raises(ValueError, match="precio_tolerancia"):
        leer_config(base)


@pytest.mark.parametrize(
    ("clave", "debajo", "encima"),
    [
        ("precio_caida_ventas_pct", "0.09", "0.91"),
        ("precio_senal_dias", 0, 8),
        ("precio_u60_min", 0, 1001),
        ("precio_escalon_max_pct", "0.009", "0.26"),
        ("precio_movimiento_min_pct", "-0.01", "0.11"),
        ("precio_tolerancia", "-0.001", "0.051"),
        ("precio_dias_entre_cambios", 0, 31),
        ("precio_freno_cambios", 1, 11),
        ("precio_divergencia_max_pct", "-0.01", "0.11"),
    ],
)
def test_config_bordes_fuera_de_cota_revientan(clave, debajo, encima):
    from app.precio.config import leer_config

    for valor in (debajo, encima):
        base = dict(BASE_CONFIG)
        base[clave] = valor
        with pytest.raises(ValueError, match=clave):
            leer_config(base)


@pytest.mark.parametrize(
    ("clave", "minimo", "maximo"),
    [
        ("precio_caida_ventas_pct", "0.10", "0.90"),
        ("precio_senal_dias", 1, 7),
        ("precio_u60_min", 1, 1000),
        ("precio_escalon_max_pct", "0.01", "0.25"),
        ("precio_movimiento_min_pct", "0", "0.10"),
        ("precio_tolerancia", "0", "0.05"),
        ("precio_dias_entre_cambios", 1, 30),
        ("precio_freno_cambios", 2, 10),
        ("precio_divergencia_max_pct", "0", "0.10"),
    ],
)
def test_config_bordes_en_cota_pasan(clave, minimo, maximo):
    from app.precio.config import leer_config

    for valor in (minimo, maximo):
        base = dict(BASE_CONFIG)
        base[clave] = valor
        leer_config(base)


def test_config_absolutos_positivos_y_basura_no_numerica():
    from app.precio.config import leer_config

    for clave in ("precio_movimiento_min_abs_mxn", "precio_movimiento_min_abs_usd"):
        base = dict(BASE_CONFIG)
        base[clave] = "0"
        with pytest.raises(ValueError, match=clave):
            leer_config(base)
        base[clave] = "doce"
        with pytest.raises(ValueError, match=clave):
            leer_config(base)


def test_config_fechas_excluidas_rangos_iso():
    c = cfg(precio_fechas_excluidas=[["2026-09-01", "2026-09-03"]])
    assert c.fechas_excluidas == ((date(2026, 9, 1), date(2026, 9, 3)),)
    with pytest.raises(ValueError, match="precio_fechas_excluidas"):
        cfg(precio_fechas_excluidas=[["2026-09-03", "2026-09-01"]])
    with pytest.raises(ValueError, match="precio_fechas_excluidas"):
        cfg(precio_fechas_excluidas=["2026-09-01"])


# ---------------------------------------------------------------- ventas (S4 #5)

from datetime import timedelta  # noqa: E402


def serie_diaria(hoy, desde_hace, hasta_hace, qty):
    return tuple((hoy - timedelta(days=d), qty) for d in range(hasta_hace, desde_hace - 1, -1))


def insumos_sanos(hoy, *, qty15=6, qty60=10, primera=None, cubierta=None, **extras):
    ventas = dict(serie_diaria(hoy, 1, 15, qty15))
    ventas.update(dict(serie_diaria(hoy, 16, 75, qty60)))
    dias15 = [hoy - timedelta(days=d) for d in range(1, 16)]
    base = {
        "ventas": tuple(sorted(ventas.items())),
        "inventario": tuple((d, 5) for d in dias15),
        "listing_activo": tuple((d, True) for d in dias15),
        "primera_venta": hoy - timedelta(days=200) if primera is None else primera,
        "dia_cubierto_hasta": (hoy - timedelta(days=1)) if cubierta is None else cubierta,
    }
    base.update(extras)
    from app.precio.tipos import VentasInsumos

    return VentasInsumos(**base)


def senal(insumos, hoy=HOY, **kw):
    from app.precio.ventas import evaluar_senal

    return evaluar_senal(insumos, hoy=hoy, config=cfg(), racha_previa=kw.pop("racha_previa", 0))


def test_ventas_borde_90_no_dispara_89_si():
    # u60 = 600 (10/dia x 60), esperado = 600/60*15*0.6 = 90.
    assert senal(insumos_sanos(HOY, qty15=6)).estado == "no_perdiendo"
    ins = insumos_sanos(HOY, qty15=6, primera=HOY - timedelta(days=200))
    ventas = dict(ins.ventas)
    ventas[HOY - timedelta(days=1)] = 5  # u15 = 89
    from app.precio.tipos import VentasInsumos

    ins = VentasInsumos(
        ventas=tuple(sorted(ventas.items())),
        inventario=ins.inventario,
        listing_activo=ins.listing_activo,
        primera_venta=ins.primera_venta,
        dia_cubierto_hasta=ins.dia_cubierto_hasta,
    )
    s = senal(ins, racha_previa=2)
    assert (s.estado, s.u15, s.u60, s.racha) == ("perdiendo", 89, 600, 3)


def test_ventas_ventanas_inclusive_ayer_y_anteayer():
    from app.precio.tipos import VentasInsumos

    ventas = {
        HOY - timedelta(days=1): 1,  # u15
        HOY - timedelta(days=15): 2,  # u15 (borde)
        HOY - timedelta(days=16): 4,  # u60 (borde)
        HOY - timedelta(days=75): 8,  # u60 (borde)
        HOY - timedelta(days=76): 100,  # fuera
        HOY: 100,  # hoy se descarta
    }
    dias15 = [HOY - timedelta(days=d) for d in range(1, 16)]
    ins = VentasInsumos(
        ventas=tuple(sorted(ventas.items())),
        inventario=tuple((d, 5) for d in dias15),
        listing_activo=tuple((d, True) for d in dias15),
        primera_venta=HOY - timedelta(days=200),
        dia_cubierto_hasta=HOY - timedelta(days=1),
    )
    s = senal(ins)
    assert (s.u15, s.n15, s.u60, s.n60) == (3, 15, 12, 60)


def test_ventas_hueco_ledger_sin_cobertura():
    from app.precio.tipos import VentasInsumos

    ins = insumos_sanos(HOY)
    sin_cobertura = VentasInsumos(
        ventas=ins.ventas,
        inventario=ins.inventario,
        listing_activo=ins.listing_activo,
        primera_venta=ins.primera_venta,
        dia_cubierto_hasta=None,
    )
    assert senal(sin_cobertura).submotivo == "ledger_hueco"
    ins = insumos_sanos(HOY, cubierta=HOY - timedelta(days=5))
    s = senal(ins)
    assert (s.estado, s.submotivo) == ("sin_dato", "ledger_hueco")


def test_ventas_u60_bajo_minimo():
    s = senal(insumos_sanos(HOY, qty60=0, qty15=0))
    assert s.u60 == 0
    ins = insumos_sanos(HOY, qty60=0, qty15=0)
    from app.precio.tipos import VentasInsumos

    ventas = dict(ins.ventas)
    for d in [HOY - timedelta(days=x) for x in range(16, 76)]:
        ventas[d] = 0
    # 19 unidades repartidas en la ventana de 60
    for d in [HOY - timedelta(days=x) for x in range(16, 35)]:
        ventas[d] = 1
    ins = VentasInsumos(
        ventas=tuple(sorted(ventas.items())),
        inventario=ins.inventario,
        listing_activo=ins.listing_activo,
        primera_venta=ins.primera_venta,
        dia_cubierto_hasta=ins.dia_cubierto_hasta,
    )
    s = senal(ins)
    assert (s.estado, s.submotivo, s.u60) == ("sin_dato", "u60_bajo_minimo", 19)


def test_ventas_n15_insuficiente_con_excluidas():
    excl = [(HOY - timedelta(days=10), HOY - timedelta(days=6))]  # 5 dias fuera
    excl2 = [(HOY - timedelta(days=15), HOY - timedelta(days=15))]  # 1 dia fuera
    from app.precio.config import leer_config

    c = leer_config(
        dict(
            BASE_CONFIG,
            precio_fechas_excluidas=[[d.isoformat(), h.isoformat()] for d, h in excl + excl2],
        )
    )
    from app.precio.ventas import evaluar_senal

    s = evaluar_senal(insumos_sanos(HOY), hoy=HOY, config=c, racha_previa=0)
    assert (s.estado, s.submotivo, s.n15) == ("sin_dato", "n15_insuficiente", 9)


def test_ventas_historia_corta_74_dias():
    ins = insumos_sanos(HOY, primera=HOY - timedelta(days=74))
    assert senal(ins).submotivo == "historia_corta"
    ins = insumos_sanos(HOY, primera=HOY - timedelta(days=75))
    assert senal(ins).submotivo != "historia_corta"


def test_ventas_dia_sin_stock_y_sin_observacion():
    dias15 = [HOY - timedelta(days=d) for d in range(1, 16)]
    inv = [(d, 0 if d == HOY - timedelta(days=3) else 5) for d in dias15]
    ins = insumos_sanos(HOY, inventario=tuple(inv))
    assert senal(ins).submotivo == "dia_sin_stock"
    inv = [(d, 5) for d in dias15 if d != HOY - timedelta(days=3)]
    ins = insumos_sanos(HOY, inventario=tuple(inv))
    assert senal(ins).submotivo == "dia_sin_observacion_inventario"


def test_ventas_listing_inactivo_un_dia():
    dias15 = [HOY - timedelta(days=d) for d in range(1, 16)]
    act = [(d, d != HOY - timedelta(days=7)) for d in dias15]
    assert senal(insumos_sanos(HOY, listing_activo=tuple(act))).submotivo == "listing_inactivo"


def test_ventas_racha_dos_de_tres_es_sin_dato():
    s = senal(insumos_sanos(HOY, qty15=0), racha_previa=1)
    assert (s.estado, s.submotivo, s.racha) == ("sin_dato", "racha_incompleta", 2)


def test_ventas_sin_caida_resetea_racha():
    s = senal(insumos_sanos(HOY, qty15=6), racha_previa=2)
    assert (s.estado, s.racha) == ("no_perdiendo", 0)


def test_ventas_excluidas_escalan_el_promedio():
    # Sin excluidas: u60=600, n60=60, n15=15, esperado=90.
    # Excluyo 30 dias de la ventana 60 (u60=300, n60=30) y 5 de la de 15
    # (n15=10): esperado = 300/30*10*0.6 = 60; u15 = 10*6 = 60 -> no pierde.
    excl = [
        (HOY - timedelta(days=75), HOY - timedelta(days=46)),
        (HOY - timedelta(days=11), HOY - timedelta(days=7)),
    ]
    from app.precio.config import leer_config
    from app.precio.ventas import evaluar_senal

    c = leer_config(
        dict(
            BASE_CONFIG,
            precio_fechas_excluidas=[[d.isoformat(), h.isoformat()] for d, h in excl],
        )
    )
    s = evaluar_senal(insumos_sanos(HOY, qty15=6, qty60=10), hoy=HOY, config=c, racha_previa=0)
    assert (s.u60, s.n60, s.n15, s.estado) == (300, 30, 10, "no_perdiendo")
