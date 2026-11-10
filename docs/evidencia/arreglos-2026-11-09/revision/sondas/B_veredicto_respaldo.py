"""Sonda B3: la misma keyword (ACoS 15 %, 20 pedidos) con el target que deja
cada version el primer ciclo tras perder elegibilidad (sonda B2): AUTO-03 4.5,
M2 25.0. Equilibrio de R3 = margen de plataforma 51 % (peldano margen_plataforma).
Pura: sin base."""
import datetime as dt
from decimal import Decimal as D

from app.optimizer import politica as pol
from app.optimizer.caso import (BidVigente, CasoHoja, Economia, EconomiaPlataforma,
                                EvidenciaNivel, InsumosPausa, PrecioVentana, Tramo, Trayectoria)

AHORA = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dt.UTC)
HOY = AHORA.date()


def caso(target, equilibrio, acos_gasto="300"):
    propia = EvidenciaNivel(
        reciente=Tramo(clics=400, pedidos=20, venta=D("2000"), gasto=D(acos_gasto), impresiones=20000),
        antiguo=Tramo(clics=0, pedidos=0, venta=D("0"), gasto=D("0"), impresiones=0),
    )
    return CasoHoja(
        plataforma="amazon_mx", hoja_id=7, ad_group_id=6,
        bid=BidVigente(valor=D("2.00"), moneda="MXN", piso=D("1.00"), techo=D("45.00")),
        economia=Economia(
            plataforma=EconomiaPlataforma(
                moneda="MXN",
                equilibrio_acos_pct=D(equilibrio) if equilibrio else None,
                gasto_para_concluir=D("500"), confianza_recorte=D("0.80"),
                confianza_subida=D("0.70")),
            target_acos_pct=D(target)),
        propia=propia, pedidos_inmaduros=0, grupo=propia, cuenta=propia,
        precio=PrecioVentana(gasto=D(acos_gasto), clics=400),
        trayectoria=Trayectoria(cambios=(), efecto=None),
        pausa=InsumosPausa(cortes=None, umbral_clics=100, gasto_minimo=D("500"),
                           expected_clicks=None, politica_economica=None),
        ventana_desde=HOY - dt.timedelta(days=90), ventana_hasta=HOY - dt.timedelta(days=10),
        observado_al=AHORA - dt.timedelta(days=3))


print("ACoS 15 %:")
print("  M2 producto elegible (target 4, equilibrio producto 8):", pol.decide(caso("4", "8")))
print("  AUTO-03 tras perder elegibilidad (target 4.5, eq. 51):  ", pol.decide(caso("4.5", "51")))
print("  M2 tras perder elegibilidad (target 25.0, eq. 51):      ", pol.decide(caso("25.0", "51")))
print("  economia de la familia (target 12, eq. 51):             ", pol.decide(caso("12", "51")))
