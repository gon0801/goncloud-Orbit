"""Sonda G-3: entradas extremas contra las reglas puras (reglas.decidir).

Busca un precio aplicado de 0, centavos, negativo o >2x. Config de prod
(D.0): escalon 0.10, tolerancia 0.005, divergencia 0.01, cooldown 7.
"""

from datetime import UTC, date, datetime
from decimal import Decimal as D

from app.estimacion_venta import DetalleFee
from app.precio import puerto
from app.precio.config import leer_config
from app.precio.reglas import decidir
from app.precio.tipos import (
    Componentes, EntradaDecision, Escenario, Fees, Importe, ObservacionPricing,
    PideCotizacion, SenalVentas,
)

cfg = leer_config({
    "precio_caida_ventas_pct": 0.40, "precio_senal_dias": 3, "precio_u60_min": 20,
    "precio_fechas_excluidas": [], "precio_escalon_max_pct": 0.10,
    "precio_movimiento_min_pct": 0.01, "precio_movimiento_min_abs_mxn": 1.00,
    "precio_movimiento_min_abs_usd": 0.10, "precio_tolerancia": 0.005,
    "precio_dias_entre_cambios": 7, "precio_freno_cambios": 3,
    "precio_divergencia_max_pct": 0.01,
})
HOY = date(2026, 11, 9)
M = "MXN"


def entrada(*, p, costo, ref_pct, fijo, envio, goal, perdiendo, iva=D("1.16"), isr=D("0.0125")):
    ingreso = (p / iva).quantize(D("0.0001"))
    isr_v = (isr * ingreso).quantize(D("0.0001"))
    referral = (p * ref_pct).quantize(D("0.01"))
    fees_total = referral + fijo
    detalles = (DetalleFee("ReferralFee", referral, None, ()),
                DetalleFee("FBAFees", fijo, None, ()))
    comp = Componentes(Importe(p, M), Importe(ingreso, M), Importe(costo, M),
                       Importe(fees_total, M), Importe(envio, M), Importe(isr_v, M))
    esc = Escenario(comp, detalles, Importe(p, M), iva, isr_v / ingreso, True,
                    datetime(2026, 11, 9, 12, tzinfo=UTC))
    senal = (SenalVentas("perdiendo", None, "unidades", 1, 60, 15, 60, 3) if perdiendo
             else SenalVentas("no_perdiendo", None, "unidades", 10, 60, 15, 60, 0))
    e = EntradaDecision(1, "amazon_mx", 1, "fba", "live", goal, esc,
                        ObservacionPricing(Importe(p, M), datetime(2026, 11, 9, 12, tzinfo=UTC)),
                        senal, (), (), date(2026, 10, 1))
    fees = Fees(Importe(fees_total, M), Importe(referral, M), Importe(fijo, M), False)
    return e, fees, ref_pct, fijo


def cotizador(ref_pct, fijo):
    def cot(precio):
        referral = (precio.valor * ref_pct).quantize(D("0.01"))
        return puerto.Cotizacion(precio=precio, fees=Fees(
            Importe(referral + fijo, M), Importe(referral, M), Importe(fijo, M), False),
            estimada_en=None, error_code=None)
    return cot


def correr(nombre, **kw):
    e, fees, ref, fijo = entrada(**kw)
    hechas = []
    for _ in range(3):
        out = decidir(e, hoy=HOY, config=cfg, cotizaciones=tuple(hechas), fees_escenario=fees)
        if not isinstance(out, PideCotizacion):
            break
        hechas.append(cotizador(ref, fijo)(out.precio))
    pa = out.p_aplicado.valor if out.p_aplicado else None
    print(f"{nombre:44s} -> {out.resultado:17s} {str(out.motivo):22s} "
          f"P={kw['p']} P*={out.p_objetivo.valor if out.p_objetivo else None} aplicado={pa}")


# Costo absurdo bajo (unidad mal cargada: 1.50 en vez de 150) con perdida de ventas
correr("costo 1.50 (100x bajo), perdiendo", p=D("500"), costo=D("1.50"), ref_pct=D("0.15"),
       fijo=D("60"), envio=D("0"), goal=D("0.20"), perdiendo=True)
# Costo absurdo alto (15000 en vez de 150): P* > 2P
correr("costo 15000 (100x alto)", p=D("500"), costo=D("15000"), ref_pct=D("0.15"),
       fijo=D("60"), envio=D("0"), goal=D("0.20"), perdiendo=False)
# Goal en el techo de banda (0.60) con referral alto: denominador <= 0
correr("goal 0.60, referral 0.30", p=D("500"), costo=D("150"), ref_pct=D("0.30"),
       fijo=D("60"), envio=D("0"), goal=D("0.60"), perdiendo=False)
# Precio de centavos actual (0.50 MXN) y margen bajo
correr("P actual 0.50 MXN", p=D("0.50"), costo=D("0.10"), ref_pct=D("0.15"),
       fijo=D("0.05"), envio=D("0"), goal=D("0.20"), perdiendo=False)
# Fijo cero, envio cero, costo minimo: el piso es el goal
correr("costo 0.01, fijo 0, perdiendo", p=D("500"), costo=D("0.01"), ref_pct=D("0.15"),
       fijo=D("0"), envio=D("0"), goal=D("0.10"), perdiendo=True)
