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
    assert len(SUBMOTIVOS_SIN_DATO) == 10


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


# ---------------------------------------------------------------- objetivo (S4 #3 y #11)

from app.precio.objetivo import (  # noqa: E402
    ErrorObjetivo,
    ResultadoObjetivo,
    derivar_ref_fijo,
    margen_a_precio,
    paso,
    piso_centavo,
    precio_estrella,
    techo_centavo,
)


def detalles_lineales():
    # Acta 0.3: F = 15 a P = 116 -> ReferralFee 12 + logistica FBA 3.
    return (
        DetalleFee("ReferralFee", Decimal("12"), None, ()),
        DetalleFee("FbaFee", Decimal("3"), None, ()),
    )


def test_objetivo_derivar_ref_y_fijo_del_acta():
    ref, fijo = derivar_ref_fijo(detalles_lineales(), Decimal("15"), Decimal("116"))
    assert ref == Decimal("12") / Decimal("116")
    assert fijo == Decimal("3")


def test_objetivo_sin_referral_o_doble_o_precio_cero():
    with pytest.raises(ErrorObjetivo, match="referral_ausente"):
        derivar_ref_fijo(
            (DetalleFee("FbaFee", Decimal("3"), None, ()),), Decimal("3"), Decimal("116")
        )
    with pytest.raises(ErrorObjetivo, match="referral_ausente"):
        derivar_ref_fijo(
            (
                DetalleFee("ReferralFee", Decimal("6"), None, ()),
                DetalleFee("ReferralFee", Decimal("6"), None, ()),
            ),
            Decimal("12"),
            Decimal("116"),
        )
    with pytest.raises(ErrorObjetivo, match="referral_ausente"):
        derivar_ref_fijo(detalles_lineales(), Decimal("15"), Decimal("0"))


def test_objetivo_estrella_y_margen_cierran_con_el_acta():
    # C=40, fijo=3, L=0, r=0.025, goal=0.30, d=1.16, ref=12/116.
    estrella = precio_estrella(
        Decimal("40"),
        Decimal("3"),
        Decimal("0"),
        Decimal("0.025"),
        Decimal("0.30"),
        Decimal("1.16"),
        True,
        Decimal("12") / Decimal("116"),
    )
    assert estrella == Decimal("43") / (
        (Decimal("1") - Decimal("0.025") - Decimal("0.30")) / Decimal("1.16")
        - Decimal("12") / Decimal("116")
    )
    m = margen_a_precio(
        estrella,
        Decimal("40"),
        Decimal("12") / Decimal("116"),
        Decimal("3"),
        Decimal("0"),
        Decimal("0.025"),
        Decimal("1.16"),
        True,
    )
    # Division finita de Decimal: cierra dentro de la tolerancia, no exacto
    # (por eso la maquina compara con `tol`, nunca con `==`).
    assert abs(m - Decimal("0.30")) <= Decimal("0.005")


def test_objetivo_denominador_no_positivo_es_margen_imposible():
    with pytest.raises(ErrorObjetivo, match="margen_imposible"):
        precio_estrella(
            Decimal("40"),
            Decimal("3"),
            Decimal("0"),
            Decimal("0.025"),
            Decimal("0.30"),
            Decimal("1.16"),
            True,
            Decimal("0.90"),
        )


def test_objetivo_redondeo_techo_y_piso():
    assert techo_centavo(Decimal("131.911")) == Decimal("131.92")
    assert techo_centavo(Decimal("131.92")) == Decimal("131.92")
    assert piso_centavo(Decimal("127.619")) == Decimal("127.61")
    assert piso_centavo(Decimal("127.61")) == Decimal("127.61")


def cotizacion(precio, detalles, total, estado="success", codigo=None):
    from app.precio.tipos import CotizacionVerificada

    return CotizacionVerificada(imp(precio), detalles, Decimal(total), estado, codigo)


def test_objetivo_paso_cero_cotizaciones_pide_estrella():
    from app.precio.tipos import PideCotizacion

    pedido = paso(
        costo=Decimal("60"),
        fijo=Decimal("3"),
        envio=Decimal("0"),
        isr_tasa=Decimal("0.025"),
        goal=Decimal("0.30"),
        iva_divisor=Decimal("1.16"),
        incluye_iva=True,
        ref=Decimal("12") / Decimal("116"),
        moneda=MXN,
        tolerancia=Decimal("0.005"),
        cotizaciones=(),
    )
    assert isinstance(pedido, PideCotizacion) and pedido.intento == 1
    assert pedido.precio.valor == techo_centavo(
        precio_estrella(
            Decimal("60"),
            Decimal("3"),
            Decimal("0"),
            Decimal("0.025"),
            Decimal("0.30"),
            Decimal("1.16"),
            True,
            Decimal("12") / Decimal("116"),
        )
    )


def test_objetivo_paso_una_lineal_verifica():
    pedido = paso(
        costo=Decimal("60"),
        fijo=Decimal("3"),
        envio=Decimal("0"),
        isr_tasa=Decimal("0.025"),
        goal=Decimal("0.30"),
        iva_divisor=Decimal("1.16"),
        incluye_iva=True,
        ref=Decimal("12") / Decimal("116"),
        moneda=MXN,
        tolerancia=Decimal("0.005"),
        cotizaciones=(),
    )
    p1 = pedido.precio.valor
    # Fees lineales: la cotizacion confirma el goal al centavo.
    f1 = (Decimal("12") / Decimal("116") * p1 + Decimal("3")).quantize(Decimal("0.01"))
    ref1 = (f1 - Decimal("3")) / p1
    c1 = cotizacion(
        str(p1),
        (
            DetalleFee("ReferralFee", (f1 - Decimal("3")), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        str(f1),
    )
    final = paso(
        costo=Decimal("60"),
        fijo=Decimal("3"),
        envio=Decimal("0"),
        isr_tasa=Decimal("0.025"),
        goal=Decimal("0.30"),
        iva_divisor=Decimal("1.16"),
        incluye_iva=True,
        ref=Decimal("12") / Decimal("116"),
        moneda=MXN,
        tolerancia=Decimal("0.005"),
        cotizaciones=(c1,),
    )
    assert isinstance(final, ResultadoObjetivo)
    assert (final.resultado, final.precio.valor) == ("verificado", p1)
    assert ref1 > 0


def test_objetivo_paso_dos_no_lineales_es_fee_no_lineal():
    c1 = cotizacion(
        "140.00",
        (
            DetalleFee("ReferralFee", Decimal("14.50"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        "17.50",
    )
    pedido = paso(
        costo=Decimal("60"),
        fijo=Decimal("3"),
        envio=Decimal("0"),
        isr_tasa=Decimal("0.025"),
        goal=Decimal("0.30"),
        iva_divisor=Decimal("1.16"),
        incluye_iva=True,
        ref=Decimal("12") / Decimal("116"),
        moneda=MXN,
        tolerancia=Decimal("0.005"),
        cotizaciones=(c1,),
    )
    from app.precio.tipos import PideCotizacion

    assert isinstance(pedido, PideCotizacion) and pedido.intento == 2
    c2 = cotizacion(
        str(pedido.precio.valor),
        (
            DetalleFee("ReferralFee", Decimal("20.00"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        "23.00",
    )
    final = paso(
        costo=Decimal("60"),
        fijo=Decimal("3"),
        envio=Decimal("0"),
        isr_tasa=Decimal("0.025"),
        goal=Decimal("0.30"),
        iva_divisor=Decimal("1.16"),
        incluye_iva=True,
        ref=Decimal("12") / Decimal("116"),
        moneda=MXN,
        tolerancia=Decimal("0.005"),
        cotizaciones=(c1, c2),
    )
    assert (final.resultado, final.motivo) == ("goal_inalcanzable", "fee_no_lineal")


def test_objetivo_paso_nunca_pide_tercera():
    c1 = cotizacion(
        "140.00",
        (
            DetalleFee("ReferralFee", Decimal("14.50"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        "17.50",
    )
    c2 = cotizacion(
        "150.00",
        (
            DetalleFee("ReferralFee", Decimal("15.50"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        "18.50",
    )
    from app.precio.tipos import PideCotizacion

    final = paso(
        costo=Decimal("60"),
        fijo=Decimal("3"),
        envio=Decimal("0"),
        isr_tasa=Decimal("0.025"),
        goal=Decimal("0.30"),
        iva_divisor=Decimal("1.16"),
        incluye_iva=True,
        ref=Decimal("12") / Decimal("116"),
        moneda=MXN,
        tolerancia=Decimal("0.005"),
        cotizaciones=(c1, c2),
    )
    assert not isinstance(final, PideCotizacion)


# ---------------------------------------------------------------- reglas

from app.precio.tipos import (  # noqa: E402
    CambioPrevio,
    Componentes,
    EntradaDecision,
    Escenario,
    HistorialMargen,
    ObservacionPricing,
)


def comp(costo="40", precio="116", ingreso="100", fees="15", envio="0", isr="2.50"):
    return Componentes(imp(precio), imp(ingreso), imp(costo), imp(fees), imp(envio), imp(isr))


def escenario(costo="40", precio="116", tasa_isr="0.025"):
    return Escenario(
        componentes=comp(costo=costo, precio=precio),
        fee_detalles=detalles_lineales(),
        precio_cotizado=imp(precio),
        iva_divisor=Decimal("1.16"),
        isr_tasa=Decimal(tasa_isr),
        precio_incluye_iva=True,
        oferta_observada_en=AHORA,
    )


# ---------------------------------------------------------------- r1-A1


def test_r1_a1_isr_incoherente_no_sube_bajando():
    from dataclasses import replace

    esc = escenario(costo="60")
    esc = replace(esc, componentes=comp(costo="60", isr="20"))
    d = resuelve(entrada(costo="60", escenario=esc))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")
    assert "R" in d.diagnostico


def test_r1_a1_tasas_incoherentes_no_bajan_subiendo():
    from dataclasses import replace

    esc = escenario(tasa_isr="0.30")
    esc = replace(esc, componentes=comp(isr="0"))
    d = resuelve(entrada(escenario=esc, senal=senal_perdiendo()))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")


def test_r1_a1_precio_cotizado_distinto_no_pasa():
    from dataclasses import replace

    esc = escenario(costo="60")
    esc = replace(esc, precio_cotizado=imp("58"))
    d = resuelve(entrada(costo="60", escenario=esc))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")


# ---------------------------------------------------------------- r2-A1


MOTIVOS_ESTIMACION_22 = [
    "oferta_desactualizada",
    "oferta_futura",
    "costo_desactualizado",
    "costo_no_vigente",
    "fx_ausente",
    "fee_ausente",
    "impuesto_fee_pendiente",
    "fee_incompatible",
    "fee_invalido",
    "precio_ausente",
    "costo_ausente",
    "politica_ausente",
    "politica_no_vigente",
    "politica_ambigua",
    "politica_invalida",
    "fx_direccion_invalida",
    "fx_tasa_invalida",
    "precio_invalido",
    "costo_invalido",
    "costo_impuesto_incompatible",
    "costo_base_fiscal_ausente",
    "identidad_ambigua",
]


@pytest.mark.parametrize("motivo", MOTIVOS_ESTIMACION_22)
def test_r2_a1_motivos_estimacion_pasan_tal_cual(motivo):
    d = decide(entrada(motivo_estimacion=motivo))
    assert (d.resultado, d.motivo) == ("no_evaluado", motivo)


def test_r2_a1_motivo_desconocido_no_revienta():
    d = decide(entrada(motivo_estimacion="motivo_del_futuro"))
    assert (d.resultado, d.motivo) == ("no_evaluado", "estimacion_motivo_desconocido")
    assert "motivo_del_futuro" in d.diagnostico


# ---------------------------------------------------------------- r2-A2


def test_r2_a2_precio_no_positivo_no_truena():
    from dataclasses import replace

    esc = escenario()
    esc = replace(esc, componentes=comp(precio="0.00", ingreso="0.00", costo="0.00"))
    d = decide(entrada(escenario=esc))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")
    d = decide(entrada(pricing=ObservacionPricing(imp("0.00"), AHORA)))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")


# ---------------------------------------------------------------- r2-A3


def test_r2_a3_cambio_futuro_cuenta_para_cooldown():
    ent = entrada(
        costo="53.01",
        cambios=(CambioPrevio(HOY + timedelta(days=1), "subir", "enviado"),),
    )
    d = decide(ent)
    assert (d.resultado, d.motivo) == ("mantener", "cooldown")
    assert "futura" in d.diagnostico


def test_r2_a3_subida_futura_frena():
    ent = entrada(
        costo="40",
        senal=senal_perdiendo(),
        cambios=(CambioPrevio(HOY + timedelta(days=1), "subir", "confirmado"),),
    )
    d = decide(ent)
    assert (d.resultado, d.motivo) == ("frenado", "perdiendo_tras_subida")
    assert "futura" in d.diagnostico


# ---------------------------------------------------------------- r2-B


def test_r2_b_r2_borde_3050_tolerancia_3051_pide():
    # m = 30.50 % exacto -> dentro de tol aunque haya senal; 30.51 % -> subir.
    from app.precio.tipos import PideCotizacion

    d = decide(entrada(costo="52.00", senal=senal_perdiendo()))
    assert (d.resultado, d.motivo) == ("mantener", "en_tolerancia")
    assert d.m_actual == Decimal("0.3050")
    assert isinstance(decide(entrada(costo="51.99", senal=senal_perdiendo())), PideCotizacion)


def test_r2_b_r6_divergencia_exacta_100_no_diverge():
    d = decide(entrada(pricing=ObservacionPricing(imp("117.16"), AHORA)))
    assert (d.resultado, d.motivo) == ("mantener", "sobre_goal_sin_perdida")


def test_r2_b_n1b_cotizado_otra_moneda_es_incoherente():
    from dataclasses import replace

    from app.precio.tipos import Importe

    esc = escenario(costo="60")
    esc = replace(esc, precio_cotizado=Importe(Decimal("116"), "USD"))
    d = decide(entrada(costo="60", escenario=esc))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")


def test_r2_b_n2_fees_que_no_suman_f_es_incoherente():
    from dataclasses import replace

    esc = escenario(costo="60")
    esc = replace(esc, componentes=comp(costo="60", fees="16"))
    d = decide(entrada(costo="60", escenario=esc))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")
    assert "final_fee" in d.diagnostico


def test_r2_b_n3_i_borde_001_pasa_0011_no():
    from dataclasses import replace

    from app.precio.tipos import PideCotizacion

    esc = escenario(costo="60")
    esc = replace(esc, componentes=comp(costo="60", ingreso="100.01"))
    assert isinstance(decide(entrada(costo="60", escenario=esc)), PideCotizacion)
    esc = replace(esc, componentes=comp(costo="60", ingreso="100.011"))
    d = decide(entrada(costo="60", escenario=esc))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")


def test_r2_b_n4_r_borde_001_pasa_0011_no():
    from dataclasses import replace

    from app.precio.tipos import PideCotizacion

    esc = escenario(costo="60")
    esc = replace(esc, componentes=comp(costo="60", isr="2.51"))
    assert isinstance(decide(entrada(costo="60", escenario=esc)), PideCotizacion)
    esc = replace(esc, componentes=comp(costo="60", isr="2.511"))
    d = decide(entrada(costo="60", escenario=esc))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")


def test_r2_b_n8_bajar_pide_doble_no_pide_segunda():
    from app.precio.tipos import CotizacionVerificada, PideCotizacion

    ent = entrada(senal=senal_perdiendo())
    pedido = decide(ent)
    assert isinstance(pedido, PideCotizacion)
    p1 = pedido.precio.valor
    assert p1 <= 2 * Decimal("116")
    referral = (Decimal("0.45") * p1).quantize(Decimal("0.01"))
    c1 = CotizacionVerificada(
        pedido.precio,
        (
            DetalleFee("ReferralFee", referral, None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        referral + Decimal("3"),
        "success",
        None,
    )
    salida = decide(ent, cotizaciones=(c1,))
    assert not isinstance(salida, PideCotizacion)
    assert (salida.resultado, salida.motivo) == ("goal_inalcanzable", "precio_mayor_al_doble")


def test_r2_b_n9_verificado_sobre_doble_no_sube_ni_baja():
    from app.precio.tipos import CotizacionVerificada

    c_subir = CotizacionVerificada(
        imp("250"),
        (
            DetalleFee("ReferralFee", Decimal("82.47"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        Decimal("85.47"),
        "success",
        None,
    )
    d = decide(entrada(costo="60"), cotizaciones=(c_subir,))
    assert (d.resultado, d.motivo) == ("goal_inalcanzable", "precio_mayor_al_doble")
    c_bajar = CotizacionVerificada(
        imp("250"),
        (
            DetalleFee("ReferralFee", Decimal("102.474"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        Decimal("105.474"),
        "success",
        None,
    )
    d = decide(entrada(senal=senal_perdiendo()), cotizaciones=(c_bajar,))
    assert (d.resultado, d.motivo) == ("goal_inalcanzable", "precio_mayor_al_doble")


def test_r2_b_n15_punto_mismo_dia_del_goal_cuenta():
    hist = (
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=10)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=10)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=10)),
    )
    d = decide(entrada(costo="60", historial=hist, goal_vigente_desde=HOY - timedelta(days=10)))
    assert (d.resultado, d.motivo) == ("frenado", "no_converge")


def test_r2_b_n16_subida_mismo_dia_del_goal_frena():
    ent = entrada(
        costo="40",
        senal=senal_perdiendo(),
        cambios=(CambioPrevio(HOY - timedelta(days=5), "subir", "confirmado"),),
        goal_vigente_desde=HOY - timedelta(days=5),
    )
    assert decide(ent).motivo == "perdiendo_tras_subida"


# ---------------------------------------------------------------- r1-C


def test_r1_c_v4_u60_en_el_minimo_si_evalua():
    # u60 = 20 con minimo 20: el borde es inclusivo.
    ins = insumos_sanos(HOY, qty15=6, qty60=0)
    from app.precio.tipos import VentasInsumos

    ventas = dict(ins.ventas)
    for d in [HOY - timedelta(days=x) for x in range(16, 76)]:
        ventas[d] = 0
    for d in [HOY - timedelta(days=x) for x in range(16, 36)]:
        ventas[d] = 1
    ins = VentasInsumos(
        ventas=tuple(sorted(ventas.items())),
        inventario=ins.inventario,
        listing_activo=ins.listing_activo,
        primera_venta=ins.primera_venta,
        dia_cubierto_hasta=ins.dia_cubierto_hasta,
    )
    s = senal(ins)
    assert (s.estado, s.u60) == ("no_perdiendo", 20)


def test_r1_c_v9_exclusiones_asimetricas_cambian_veredicto():
    # 5 dias fuera solo en la ventana de 15: esperado = 600/60*10*0.6 = 60,
    # u15 = 60 -> no pierde. Con 15/60 fijo daria 90 -> perderia.
    from app.precio.config import leer_config
    from app.precio.ventas import evaluar_senal

    excl = [(HOY - timedelta(days=11), HOY - timedelta(days=7))]
    c = leer_config(
        dict(
            BASE_CONFIG,
            precio_fechas_excluidas=[[d.isoformat(), h.isoformat()] for d, h in excl],
        )
    )
    s = evaluar_senal(insumos_sanos(HOY, qty15=6, qty60=10), hoy=HOY, config=c, racha_previa=2)
    assert (s.estado, s.n15, s.u15) == ("no_perdiendo", 10, 60)


def test_r1_c_r4_freno_22_si_23_no():
    base = dict(costo="40", senal=senal_perdiendo())
    ent = entrada(**base, cambios=(CambioPrevio(HOY - timedelta(days=22), "subir", "confirmado"),))
    assert decide(ent).motivo == "perdiendo_tras_subida"
    from app.precio.tipos import PideCotizacion

    ent = entrada(**base, cambios=(CambioPrevio(HOY - timedelta(days=23), "subir", "confirmado"),))
    assert isinstance(decide(ent), PideCotizacion)


def test_r1_c_r7_no_confirmado_no_frena():
    from app.precio.tipos import PideCotizacion

    ent = entrada(
        costo="40",
        senal=senal_perdiendo(),
        cambios=(CambioPrevio(HOY - timedelta(days=10), "subir", "no_confirmado"),),
    )
    assert isinstance(decide(ent), PideCotizacion)


def test_r1_c_r8_piso_sube_al_centavo():
    # P = 116.01: piso = 104.409 -> 104.41 hacia arriba.
    d = resuelve(entrada(costo="40", precio="116.01", senal=senal_perdiendo()))
    assert d.resultado == "bajar"
    assert d.p_aplicado.valor == Decimal("104.41")


def test_r1_c_r14_reversa_no_es_cooldown():
    from app.precio.tipos import PideCotizacion

    ent = entrada(
        costo="53.01",
        cambios=(CambioPrevio(HOY - timedelta(days=3), "subir", "enviado", es_reversa=True),),
    )
    assert isinstance(decide(ent), PideCotizacion)


def test_r1_c_o3_tax_anidado_frena():
    base = dict(
        costo=Decimal("60"),
        fijo=Decimal("3"),
        envio=Decimal("0"),
        isr_tasa=Decimal("0.025"),
        goal=Decimal("0.30"),
        iva_divisor=Decimal("1.16"),
        incluye_iva=True,
        ref=Decimal("12") / Decimal("116"),
        moneda=MXN,
        tolerancia=Decimal("0.005"),
    )
    anidado = cotizacion(
        "140.00",
        (
            DetalleFee(
                "ReferralFee",
                Decimal("14.50"),
                None,
                (DetalleFee("SubFee", Decimal("2.00"), Decimal("1.00"), ()),),
            ),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        "17.50",
    )
    assert paso(cotizaciones=(anidado,), **base).motivo == "impuesto_fee_pendiente"


def test_r1_c_o4_igual_a_tol_verifica():
    c1 = cotizacion(
        "140.00",
        (
            DetalleFee("ReferralFee", Decimal("14.50"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        "17.50",
    )
    ref1, fijo1 = derivar_ref_fijo(c1.detalles, c1.fee_total, c1.precio.valor)
    m1 = margen_a_precio(
        c1.precio.valor,
        Decimal("60"),
        ref1,
        fijo1,
        Decimal("0"),
        Decimal("0.025"),
        Decimal("1.16"),
        True,
    )
    tol_exacta = abs(m1 - Decimal("0.30"))
    assert tol_exacta > 0
    final = paso(
        costo=Decimal("60"),
        fijo=Decimal("3"),
        envio=Decimal("0"),
        isr_tasa=Decimal("0.025"),
        goal=Decimal("0.30"),
        iva_divisor=Decimal("1.16"),
        incluye_iva=True,
        ref=Decimal("12") / Decimal("116"),
        moneda=MXN,
        tolerancia=tol_exacta,
        cotizaciones=(c1,),
    )
    assert isinstance(final, ResultadoObjetivo) and final.resultado == "verificado"


def test_r1_c_o5_segundo_p_sale_de_ref1():
    from app.precio.tipos import PideCotizacion

    c1 = cotizacion(
        "140.00",
        (
            DetalleFee("ReferralFee", Decimal("14.50"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        "17.50",
    )
    pedido = paso(
        costo=Decimal("60"),
        fijo=Decimal("3"),
        envio=Decimal("0"),
        isr_tasa=Decimal("0.025"),
        goal=Decimal("0.30"),
        iva_divisor=Decimal("1.16"),
        incluye_iva=True,
        ref=Decimal("12") / Decimal("116"),
        moneda=MXN,
        tolerancia=Decimal("0.005"),
        cotizaciones=(c1,),
    )
    assert isinstance(pedido, PideCotizacion) and pedido.intento == 2
    ref1, fijo1 = derivar_ref_fijo(c1.detalles, c1.fee_total, c1.precio.valor)
    esperado = techo_centavo(
        precio_estrella(
            Decimal("60"),
            fijo1,
            Decimal("0"),
            Decimal("0.025"),
            Decimal("0.30"),
            Decimal("1.16"),
            True,
            ref1,
        )
    )
    con_ref_viejo = techo_centavo(
        precio_estrella(
            Decimal("60"),
            Decimal("3"),
            Decimal("0"),
            Decimal("0.025"),
            Decimal("0.30"),
            Decimal("1.16"),
            True,
            Decimal("12") / Decimal("116"),
        )
    )
    assert esperado != con_ref_viejo
    assert pedido.precio.valor == esperado


def test_r1_c_o7_denominador_cero_es_imposible():
    ref_cero = Decimal("0.675") / Decimal("1.16")
    with pytest.raises(ErrorObjetivo, match="margen_imposible"):
        precio_estrella(
            Decimal("40"),
            Decimal("3"),
            Decimal("0"),
            Decimal("0.025"),
            Decimal("0.30"),
            Decimal("1.16"),
            True,
            ref_cero,
        )


def test_r1_c_o8_referral_en_cero_no_cuenta():
    with pytest.raises(ErrorObjetivo, match="referral_ausente"):
        derivar_ref_fijo(
            (
                DetalleFee("ReferralFee", Decimal("0"), None, ()),
                DetalleFee("FbaFee", Decimal("3"), None, ()),
            ),
            Decimal("3"),
            Decimal("116"),
        )


def test_r1_c_o9_sin_iva_no_usa_divisor():
    # d = 1: P* = 43 / (0.69 - 0.15) = 43 / 0.54.
    assert precio_estrella(
        Decimal("40"),
        Decimal("3"),
        Decimal("0"),
        Decimal("0.01"),
        Decimal("0.30"),
        Decimal("1.16"),
        False,
        Decimal("0.15"),
    ) == Decimal("43") / Decimal("0.54")


# ---------------------------------------------------------------- r1-B4


def test_r1_b4_dia_sin_estado_listing_tiene_submotivo_propio():
    dias15 = [HOY - timedelta(days=d) for d in range(1, 16)]
    act = [(d, True) for d in dias15 if d != HOY - timedelta(days=4)]
    assert senal(insumos_sanos(HOY, listing_activo=tuple(act))).submotivo == (
        "dia_sin_estado_listing"
    )


def test_r1_b4_ventana_60_toda_excluida():
    from app.precio.config import leer_config
    from app.precio.ventas import evaluar_senal

    c = leer_config(
        dict(
            BASE_CONFIG,
            precio_fechas_excluidas=[
                [(HOY - timedelta(days=75)).isoformat(), (HOY - timedelta(days=16)).isoformat()]
            ],
        )
    )
    s = evaluar_senal(insumos_sanos(HOY), hoy=HOY, config=c, racha_previa=0)
    assert (s.estado, s.submotivo, s.n60) == ("sin_dato", "ventana_60_excluida", 0)


# ---------------------------------------------------------------- r1-B3


def test_r1_b3_subida_anterior_al_goal_no_frena_posterior_si():
    from app.precio.tipos import PideCotizacion

    base = dict(
        costo="40",
        senal=senal_perdiendo(),
        cambios=(CambioPrevio(HOY - timedelta(days=10), "subir", "confirmado"),),
    )
    # Subida de hace 10 días (fuera de cooldown), goal desde hace 3: no frena.
    d = decide(entrada(**base, goal_vigente_desde=HOY - timedelta(days=3)))
    assert isinstance(d, PideCotizacion)
    # Mismo caso con goal desde hace 15: posterior, frena.
    d = decide(entrada(**base, goal_vigente_desde=HOY - timedelta(days=15)))
    assert (d.resultado, d.motivo) == ("frenado", "perdiendo_tras_subida")


def test_r1_b3_historial_viejo_no_frena_nuevo_si():
    from app.precio.tipos import PideCotizacion

    viejo = (
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=40)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=39)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=38)),
    )
    d = decide(entrada(costo="60", historial=viejo, goal_vigente_desde=HOY - timedelta(days=10)))
    assert isinstance(d, PideCotizacion)
    nuevo = (
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=9)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=8)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=7)),
    )
    d = decide(entrada(costo="60", historial=nuevo, goal_vigente_desde=HOY - timedelta(days=10)))
    assert (d.resultado, d.motivo) == ("frenado", "no_converge")


def test_r1_b3_cooldown_ignora_el_goal():
    ent = dict(
        costo="53.01",
        cambios=(CambioPrevio(HOY - timedelta(days=2), "subir", "enviado"),),
        goal_vigente_desde=HOY - timedelta(days=1),
    )
    assert decide(entrada(**ent)).motivo == "cooldown"


# ---------------------------------------------------------------- r1-B2


def test_r1_b2_freno6_virtual_no_frena_en_live_si_en_shadow():
    from app.precio.tipos import PideCotizacion

    ent = dict(
        costo="40",
        senal=senal_perdiendo(),
        cambios=(CambioPrevio(HOY - timedelta(days=10), "subir", "confirmado", aplicado=False),),
    )
    # En live el virtual no frena: sigue a la maquina de bajada.
    assert isinstance(decide(entrada(**ent)), PideCotizacion)
    assert decide(entrada(**ent, mode="shadow")).motivo == "perdiendo_tras_subida"


def test_r1_b2_cooldown_virtual_no_frena_en_live_si_en_shadow():
    ent = dict(
        costo="53.01",
        cambios=(CambioPrevio(HOY - timedelta(days=2), "subir", "enviado", aplicado=False),),
    )
    from app.precio.tipos import PideCotizacion

    assert isinstance(decide(entrada(**ent)), PideCotizacion)
    assert decide(entrada(**ent, mode="shadow")).motivo == "cooldown"


def test_r1_b2_freno10_virtual_no_frena_en_live_si_en_shadow():
    from app.precio.tipos import PideCotizacion

    hist = (
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=10), aplicado=False),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=9), aplicado=False),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=8), aplicado=False),
    )
    ent = dict(costo="60", historial=hist)
    assert isinstance(decide(entrada(**ent)), PideCotizacion)
    assert decide(entrada(**ent, mode="shadow")).motivo == "no_converge"


# ---------------------------------------------------------------- r1-B1


def test_r1_b1_cubierto_hace_3_pasa_con_13_dias():
    # Uniforme 6/dia: u15 = 78, n15 = 13; esperado = 600/60*13*0.6 = 78.
    ins = insumos_sanos(HOY, cubierta=HOY - timedelta(days=3))
    s = senal(ins)
    assert (s.estado, s.n15, s.u15) == ("no_perdiendo", 13, 78)


def test_r1_b1_cubierto_hace_4_es_hueco():
    ins = insumos_sanos(HOY, cubierta=HOY - timedelta(days=4))
    assert senal(ins).submotivo == "ledger_hueco"


# ---------------------------------------------------------------- r1-A3


def test_r1_a3_bajar_verifica_con_cotizacion():
    # Espejo de subir: la bajada tambien cotiza; P_goal es el verificado.
    from app.precio.tipos import PideCotizacion

    ent = entrada(senal=senal_perdiendo())
    pedido = decide(ent)
    assert isinstance(pedido, PideCotizacion)
    d = resuelve(ent)
    assert d.resultado == "bajar"
    assert d.p_objetivo.valor == pedido.precio.valor


def test_r1_a3_bajar_tax_error_y_no_lineal():
    from app.precio.tipos import CotizacionVerificada, PideCotizacion

    ent = entrada(senal=senal_perdiendo())
    pedido = decide(ent)
    assert isinstance(pedido, PideCotizacion)
    con_tax = CotizacionVerificada(
        pedido.precio,
        (DetalleFee("ReferralFee", Decimal("10"), Decimal("1"), ()),),
        Decimal("10"),
        "success",
        None,
    )
    d = decide(ent, cotizaciones=(con_tax,))
    assert (d.resultado, d.motivo) == ("no_evaluado", "impuesto_fee_pendiente")
    en_error = CotizacionVerificada(pedido.precio, (), Decimal("0"), "error", "fee_http_500")
    d = decide(ent, cotizaciones=(en_error,))
    assert (d.resultado, d.motivo) == ("no_evaluado", "fee_error:fee_http_500")
    c1 = CotizacionVerificada(
        imp("100"),
        (
            DetalleFee("ReferralFee", Decimal("1.69"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        Decimal("4.69"),
        "success",
        None,
    )
    pedido2 = decide(ent, cotizaciones=(c1,))
    assert isinstance(pedido2, PideCotizacion)
    c2 = CotizacionVerificada(
        pedido2.precio,
        (
            DetalleFee("ReferralFee", Decimal("30"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        Decimal("33"),
        "success",
        None,
    )
    d = decide(ent, cotizaciones=(c1, c2))
    assert (d.resultado, d.motivo) == ("goal_inalcanzable", "fee_no_lineal")


def test_r1_a3_candado_direccion_bajar_con_verificado_arriba():
    # Espejo de A1-capa-2: escenario coherente (m=0.425 -> bajar), pero una
    # cotizacion verifica P1=130 > P=116 -> escenario_incoherente.
    from app.precio.tipos import CotizacionVerificada, PideCotizacion

    ent = entrada(senal=senal_perdiendo())
    pedido = decide(ent)
    assert isinstance(pedido, PideCotizacion)
    amañada = CotizacionVerificada(
        imp("130"),
        (
            DetalleFee("ReferralFee", Decimal("32.65"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        Decimal("35.65"),
        "success",
        None,
    )
    d = decide(ent, cotizaciones=(amañada,))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")


# ---------------------------------------------------------------- r1-A2


def test_r1_a2_segundo_p_mayor_al_doble_no_se_pide():
    # costo=60 -> la maquina pide P1; con referral del 45 % el P2 saldria
    # ~480 > 2P=232: termina en inalcanzable SIN emitir ese PideCotizacion.
    from app.precio.tipos import CotizacionVerificada, PideCotizacion

    ent = entrada(costo="60")
    pedido = decide(ent)
    assert isinstance(pedido, PideCotizacion)
    p1 = pedido.precio.valor
    assert p1 <= 2 * Decimal("116")
    referral = (Decimal("0.45") * p1).quantize(Decimal("0.01"))
    c1 = CotizacionVerificada(
        pedido.precio,
        (
            DetalleFee("ReferralFee", referral, None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        referral + Decimal("3"),
        "success",
        None,
    )
    salida = decide(ent, cotizaciones=(c1,))
    assert not isinstance(salida, PideCotizacion)
    assert (salida.resultado, salida.motivo) == ("goal_inalcanzable", "precio_mayor_al_doble")


def test_r1_a1_candado_direccion_subir_con_verificado_abajo():
    # Capa 2 sin capa 1: escenario coherente (m=0.29 -> subir), pero una
    # cotizacion real verifica P1=100 < P=116 (fees no lineales: F1=4.69
    # -> m(100)=0.30 exacto).
    from app.precio.tipos import CotizacionVerificada, PideCotizacion

    ent = entrada(costo="53.5")
    pedido = decide(ent)
    assert isinstance(pedido, PideCotizacion)
    amañada = CotizacionVerificada(
        imp("100"),
        (
            DetalleFee("ReferralFee", Decimal("1.69"), None, ()),
            DetalleFee("FbaFee", Decimal("3"), None, ()),
        ),
        Decimal("4.69"),
        "success",
        None,
    )
    d = decide(ent, cotizaciones=(amañada,))
    assert (d.resultado, d.motivo) == ("no_evaluado", "escenario_incoherente")


def senal_perdiendo():
    return SenalVentas("perdiendo", None, 89, 600, 15, 60, 3)


def senal_sana():
    return SenalVentas("no_perdiendo", None, 90, 600, 15, 60, 0)


def entrada(costo="40", precio="116", **kw):
    base = dict(
        listing_id=1,
        platform="amazon_mx",
        product_id=7,
        canal="fba",
        mode="live",
        goal=Decimal("0.30"),
        escenario=escenario(costo=costo, precio=precio),
        pricing=ObservacionPricing(imp(precio), AHORA),
        senal=senal_sana(),
        ingreso_60d=imp("15000"),
        cambios=(),
        historial=(),
        goal_vigente_desde=HOY - timedelta(days=100),
    )
    base.update(kw)
    return EntradaDecision(**base)


def decide(ent, hoy=HOY, cotizaciones=()):
    from app.precio.reglas import decidir

    return decidir(ent, hoy=hoy, config=cfg(), cotizaciones=cotizaciones)


def cotiza_lineal(pedido, ref=Decimal("12") / Decimal("116"), fijo=Decimal("3")):
    from app.precio.tipos import CotizacionVerificada

    p = pedido.precio.valor
    total = (ref * p + fijo).quantize(Decimal("0.01"))
    return CotizacionVerificada(
        pedido.precio,
        (
            DetalleFee("ReferralFee", total - fijo, None, ()),
            DetalleFee("FbaFee", fijo, None, ()),
        ),
        total,
        "success",
        None,
    )


def resuelve(ent, hoy=HOY):
    from app.precio.tipos import PideCotizacion

    hechas = []
    for _ in range(3):
        salida = decide(ent, hoy=hoy, cotizaciones=tuple(hechas))
        if not isinstance(salida, PideCotizacion):
            return salida
        hechas.append(cotiza_lineal(salida))
    raise AssertionError("la maquina pidio una tercera cotizacion")


def test_reglas_passthrough_del_motivo_de_estimacion():
    d = decide(entrada(motivo_estimacion="fee_ausente"))
    assert (d.resultado, d.motivo) == ("no_evaluado", "fee_ausente")


def test_reglas_sin_pricing_es_precio_sin_observar():
    d = decide(entrada(pricing=None))
    assert (d.resultado, d.motivo) == ("no_evaluado", "precio_sin_observar")


def test_reglas_divergencia_101_si_099_no():
    d = decide(entrada(pricing=ObservacionPricing(imp("117.18"), AHORA)))
    assert (d.resultado, d.motivo) == ("no_evaluado", "precio_divergente")
    assert "117.18" in d.diagnostico and "116" in d.diagnostico
    d = decide(entrada(pricing=ObservacionPricing(imp("117.14"), AHORA)))
    assert (d.resultado, d.motivo) != ("no_evaluado", "precio_divergente")


def test_reglas_moneda_divergente():
    d = decide(entrada(pricing=ObservacionPricing(imp("116", "USD"), AHORA)))
    assert (d.resultado, d.motivo) == ("no_evaluado", "moneda_divergente")


def test_reglas_borde_tolerancia_mantener_y_subir():
    # m = 29.50% -> dentro de tol -> mantener; 29.49% -> subir.
    d = decide(entrada(costo="53"))
    assert (d.resultado, d.motivo) == ("mantener", "en_tolerancia")
    assert d.m_actual == Decimal("0.2950")
    from app.precio.tipos import PideCotizacion

    d = decide(entrada(costo="53.01"))
    assert isinstance(d, PideCotizacion)


def test_reglas_tope_del_escalon_hacia_abajo():
    d = resuelve(entrada(costo="60", precio="116.01"))
    assert d.resultado == "subir"
    assert d.p_aplicado.valor == Decimal("127.61")
    assert d.p_objetivo.valor > d.p_aplicado.valor


def test_reglas_sin_recorte_el_margen_alcanza_el_goal():
    d = resuelve(entrada(costo="53.5"))
    assert d.resultado == "subir"
    assert d.p_aplicado.valor == d.p_objetivo.valor
    from app.precio.objetivo import margen_a_precio

    m = margen_a_precio(
        d.p_aplicado.valor,
        Decimal("53.5"),
        Decimal("12") / Decimal("116"),
        Decimal("3"),
        Decimal("0"),
        Decimal("0.025"),
        Decimal("1.16"),
        True,
    )
    assert m >= Decimal("0.30")


def test_reglas_freno_tras_subida_antes_que_todo():
    ent = entrada(
        costo="40",
        senal=senal_perdiendo(),
        cambios=(CambioPrevio(HOY - timedelta(days=10), "subir", "confirmado"),),
    )
    d = decide(ent)
    assert (d.resultado, d.motivo) == ("frenado", "perdiendo_tras_subida")


def test_reglas_cooldown_seis_si_siete_no():
    ent = entrada(cambios=(CambioPrevio(HOY - timedelta(days=6), "subir", "no_confirmado"),))
    assert decide(ent).motivo == "cooldown"
    from app.precio.tipos import PideCotizacion

    ent = entrada(
        costo="53.01",
        cambios=(CambioPrevio(HOY - timedelta(days=7), "subir", "no_confirmado"),),
    )
    assert isinstance(decide(ent), PideCotizacion)


def test_reglas_no_converge_tres_sin_acercarse():
    hist = (
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=30)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=20)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=10)),
    )
    d = decide(entrada(costo="60", historial=hist))
    assert (d.resultado, d.motivo) == ("frenado", "no_converge")


def test_reglas_convergiendo_o_goal_nuevo_no_frena():
    from app.precio.tipos import PideCotizacion

    def no_frena(salida):
        return isinstance(salida, PideCotizacion) or (salida.resultado, salida.motivo) != (
            "frenado",
            "no_converge",
        )

    hist = (
        HistorialMargen("subir", Decimal("0.08"), HOY - timedelta(days=30)),
        HistorialMargen("subir", Decimal("0.07"), HOY - timedelta(days=20)),
        HistorialMargen("subir", Decimal("0.06"), HOY - timedelta(days=10)),
    )
    assert no_frena(decide(entrada(costo="55", historial=hist)))
    hist = (
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=30)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=20)),
        HistorialMargen("bajar", Decimal("0.05"), HOY - timedelta(days=10)),
    )
    assert no_frena(decide(entrada(costo="55", historial=hist)))
    hist = (
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=30)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=20)),
        HistorialMargen("subir", Decimal("0.05"), HOY - timedelta(days=10)),
    )
    assert no_frena(
        decide(entrada(costo="55", historial=hist, goal_vigente_desde=HOY + timedelta(days=1)))
    )


def test_reglas_freno_antes_que_cooldown():
    ent = entrada(
        costo="40",
        senal=senal_perdiendo(),
        cambios=(
            CambioPrevio(HOY - timedelta(days=10), "subir", "confirmado"),
            CambioPrevio(HOY - timedelta(days=2), "subir", "enviado"),
        ),
    )
    assert decide(ent).motivo == "perdiendo_tras_subida"


def test_reglas_p_estrella_mayor_al_doble_no_cotiza():
    from app.precio.tipos import PideCotizacion

    ref = Decimal("0.57")
    referral = (ref * Decimal("116")).quantize(Decimal("0.01"))
    detalles = (
        DetalleFee("ReferralFee", referral, None, ()),
        DetalleFee("FbaFee", Decimal("3"), None, ()),
    )
    esc = escenario(costo="60")
    from dataclasses import replace

    esc = replace(esc, fee_detalles=detalles, componentes=comp(costo="60", fees="69.12"))
    d = decide(entrada(escenario=esc, costo="60"))
    assert not isinstance(d, PideCotizacion)
    assert (d.resultado, d.motivo) == ("goal_inalcanzable", "precio_mayor_al_doble")


def test_reglas_regla11_cubre_costo_predicado():
    from app.precio.reglas import motivo_regla11

    assert motivo_regla11(Decimal("50"), Decimal("116"), Decimal("40"), Decimal("0")) is None
    assert (
        motivo_regla11(Decimal("250"), Decimal("116"), Decimal("40"), Decimal("0"))
        == "precio_mayor_al_doble"
    )
    assert (
        motivo_regla11(Decimal("50"), Decimal("116"), Decimal("40"), Decimal("20"))
        == "precio_no_cubre_costo"
    )


def test_reglas_bajar_con_senal_y_escalon():
    d = resuelve(entrada(senal=senal_perdiendo()))
    assert d.resultado == "bajar"
    # P_goal ~ 90 < piso 104.40 -> aplica un escalon.
    assert d.p_aplicado.valor == Decimal("104.40")
    assert d.p_objetivo.valor < Decimal("116")


def test_reglas_sobre_goal_sin_perdida_y_sin_dato():
    d = decide(entrada())
    assert (d.resultado, d.motivo) == ("mantener", "sobre_goal_sin_perdida")
    senal = SenalVentas("sin_dato", "racha_incompleta", 89, 600, 15, 60, 2)
    d = decide(entrada(senal=senal))
    assert (d.resultado, d.motivo) == ("mantener", "ventas_sin_dato:racha_incompleta")


def test_reglas_movimiento_minimo_no_consume():
    d = resuelve(entrada(costo="53.01"))
    assert (d.resultado, d.motivo) == ("mantener", "movimiento_minimo")


def test_reglas_prioridad_registrada():
    d = resuelve(entrada(costo="60"))
    assert d.prioridad == abs(d.m_actual - Decimal("0.30")) * Decimal("15000")


def test_reglas_buy_box_solo_se_registra():
    # Perderla no frena ni cambia el resultado; se guarda tal cual (S4 #7).
    d = resuelve(entrada(costo="60", buy_box_is_own=False))
    assert (d.resultado, d.buy_box_is_own) == ("subir", False)
    d = decide(entrada(buy_box_is_own=None))
    assert (d.resultado, d.buy_box_is_own) == ("mantener", None)


def test_reglas_sombra_igual_con_aplicado_falso():
    ent_live = entrada(costo="60")
    ent_shadow = entrada(costo="60", mode="shadow")
    viva = resuelve(ent_live)
    sombra = resuelve(ent_shadow)
    assert viva.resultado == "subir" and viva.aplicado is True
    assert sombra.aplicado is False
    assert sombra.resultado == viva.resultado == "subir"
    assert sombra.p_aplicado.valor == viva.p_aplicado.valor
    assert sombra.motivo == viva.motivo


def test_reglas_cupo_por_prioridad_con_desempate():
    from app.precio.reglas import repartir_cupo

    a = resuelve(entrada(costo="60"))
    b = resuelve(entrada(costo="55"))
    assert a.prioridad > b.prioridad
    primero, segundo = repartir_cupo(((2, b), (1, a)), cupo=1)
    assert primero.resultado == "subir" and segundo.motivo == "cuota"
    # Empate de prioridad: listing_id ascendente pasa primero (se distingue
    # por p_aplicado: cada costo da un P* distinto).
    from dataclasses import replace

    assert a.p_aplicado.valor != b.p_aplicado.valor
    a2 = replace(a, prioridad=b.prioridad)
    primero, segundo = repartir_cupo(((2, a2), (1, b)), cupo=1)
    assert primero.p_aplicado.valor == b.p_aplicado.valor
    assert segundo.motivo == "cuota"


def test_reglas_goal_float_revienta():
    from app.precio.tipos import CotizacionVerificada

    with pytest.raises(TypeError):
        entrada(goal=0.30)
    with pytest.raises(TypeError):
        CotizacionVerificada(imp("116"), (), 17.5, "success")


def test_reglas_sin_decimal_no_hay_float():
    import ast
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent / "app" / "precio"
    for path in sorted(raiz.glob("*.py")):
        arbol = ast.parse(path.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            assert not (isinstance(nodo, ast.Constant) and isinstance(nodo.value, float)), (
                f"{path.name}: literal float"
            )
            assert not (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Name)
                and nodo.func.id == "float"
            ), f"{path.name}: llamada a float("


# ---------------------------------------------------------------- cotizar_a_precio

from app.estimacion_fees import FeesClientError, cotizar_a_precio  # noqa: E402


def oferta_mx(precio="116.00"):
    from app.estimacion_insumos import OfertaResuelta

    return OfertaResuelta(
        listing_id=1,
        platform="amazon_mx",
        seller_sku="SKU1",
        asin="B0EST01",
        canal="fba",
        price_amount=Decimal(precio),
        price_currency="MXN",
        fetched_at=AHORA,
        canonical_input={},
        context_fingerprint="x",
        source_event_id="evento-1",
    )


class ClienteFalso:
    def __init__(self, body=None, error=None):
        self.body = body
        self.error = error
        self.pedidos = []

    def cotizar(self, oferta):
        self.pedidos.append(oferta)
        if self.error is not None:
            raise self.error
        return self.body(oferta)


def cuerpo_exito(oferta):
    p = float(oferta.price_amount)
    return {
        "payload": {
            "FeesEstimateResult": {
                "Status": "Success",
                "FeesEstimateIdentifier": {
                    "MarketplaceId": "A1AM78C64UM0Y8",
                    "IdType": "SellerSKU",
                    "IdValue": oferta.seller_sku,
                    "SellerInputIdentifier": oferta.source_event_id,
                    "IsAmazonFulfilled": True,
                    "PriceToEstimateFees": {"ListingPrice": {"CurrencyCode": "MXN", "Amount": p}},
                },
                "FeesEstimate": {
                    "TimeOfFeesEstimation": AHORA.isoformat(),
                    "TotalFeesEstimate": {"CurrencyCode": "MXN", "Amount": 15.0},
                    "FeeDetailList": [
                        {
                            "FeeType": "ReferralFee",
                            "FeeAmount": {"CurrencyCode": "MXN", "Amount": 12.0},
                            "FinalFee": {"CurrencyCode": "MXN", "Amount": 12.0},
                        },
                        {
                            "FeeType": "FBAFees",
                            "FeeAmount": {"CurrencyCode": "MXN", "Amount": 3.0},
                            "FinalFee": {"CurrencyCode": "MXN", "Amount": 3.0},
                        },
                    ],
                },
            }
        }
    }


def test_cotizar_a_precio_sustituye_sin_mutar_y_sin_persistir():
    oferta = oferta_mx()
    cliente = ClienteFalso(body=cuerpo_exito)
    resultado = cotizar_a_precio(cliente, oferta, Decimal("130.00"), observed_at=AHORA)
    assert resultado.estado == "success"
    assert cliente.pedidos[0].price_amount == Decimal("130.00")
    assert oferta.price_amount == Decimal("116.00")
    assert cliente.pedidos[0] is not oferta


def test_cotizar_a_precio_rechaza_precio_invalido():
    oferta = oferta_mx()
    for malo in (130.0, "130.00", Decimal("0"), Decimal("-1"), Decimal("NaN")):
        with pytest.raises(ValueError):
            cotizar_a_precio(cliente := ClienteFalso(body=cuerpo_exito), oferta, malo)
        assert cliente.pedidos == []


def test_cotizar_a_precio_no_amplia_universo_y_propoaga_error():
    from dataclasses import replace

    oferta = oferta_mx()
    fbm = replace(oferta, canal="fbm")
    resultado = cotizar_a_precio(ClienteFalso(body=cuerpo_exito), fbm, Decimal("130.00"))
    assert (resultado.estado, resultado.error_code) == ("error", "fee_universo_no_soportado")
    cliente = ClienteFalso(error=FeesClientError("fee_timeout"))
    resultado = cotizar_a_precio(cliente, oferta, Decimal("130.00"), observed_at=AHORA)
    assert (resultado.estado, resultado.error_code) == ("error", "fee_timeout")


def test_objetivo_paso_tax_y_error_y_no_concilia():
    base = dict(
        costo=Decimal("60"),
        fijo=Decimal("3"),
        envio=Decimal("0"),
        isr_tasa=Decimal("0.025"),
        goal=Decimal("0.30"),
        iva_divisor=Decimal("1.16"),
        incluye_iva=True,
        ref=Decimal("12") / Decimal("116"),
        moneda=MXN,
        tolerancia=Decimal("0.005"),
    )
    con_tax = cotizacion(
        "140.00",
        (DetalleFee("ReferralFee", Decimal("14.50"), Decimal("1.00"), ()),),
        "14.50",
    )
    assert paso(cotizaciones=(con_tax,), **base).motivo == "impuesto_fee_pendiente"
    en_error = cotizacion("140.00", (), "0", estado="error", codigo="fee_http_429")
    assert paso(cotizaciones=(en_error,), **base).motivo == "fee_error:fee_http_429"
    no_concilia = cotizacion(
        "140.00",
        (DetalleFee("ReferralFee", Decimal("14.50"), None, ()),),
        "99.99",
    )
    assert paso(cotizaciones=(no_concilia,), **base).motivo == "fee_error:fee_no_concilia"


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
