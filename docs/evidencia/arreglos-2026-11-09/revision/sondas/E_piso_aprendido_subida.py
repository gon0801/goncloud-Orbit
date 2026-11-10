"""Sonda E-H2: el dueno revierte una SUBIDA del motor y el motor ya no puede recortar.

Camino real:
- v_cambio_bid (migracion 0067) rama 2: toda reversa ok de una decision kind='bid'
  sale con origen 'regreso_del_dueno', bid_antes NULL y bid_despues = d.old_value.
- lecturas_caso._cambios_por_hoja deriva bid_antes = bid_despues del cambio previo.
- caso.Trayectoria.piso_aprendido = max(bid_antes) de los regresos.
- politica R17 / _paso bloquean todo recorte que deje el bid <= piso_aprendido.

Escenario: motor sube 1.00 -> 1.15; el dueno revierte esa subida (POST /reversa/bid,
apply.reversa_manual acepta cualquier decision bid aplicada). Bid vigente 1.00.
La hoja pierde dinero con certeza (deberia recibir -25%).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.lecturas_caso import _cambios_por_hoja
from app.optimizer import politica
from app.optimizer.caso import (
    BidVigente,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EfectoCambio,
    EvidenciaNivel,
    InsumosPausa,
    PrecioVentana,
    Tramo,
    Trayectoria,
)

D = dt.date(2026, 11, 9)
UTC = dt.UTC

# Filas tal como las devuelve _SQL_CAMBIOS: (hoja, confirmado_el, bid_antes, bid_despues, origen, revierte_new)
filas = [
    (1, dt.datetime(2026, 10, 1, 9, tzinfo=UTC), Decimal("1.00"), Decimal("1.15"), "motor", None),
    # reversa del dueno de ESA subida: bid_antes NULL (rama 2 de la vista), bid_despues = old_value
    (1, dt.datetime(2026, 10, 3, 9, tzinfo=UTC), None, Decimal("1.00"), "regreso_del_dueno", Decimal("1.15")),
]
cambios = _cambios_por_hoja(filas)[1]
print("cambios derivados:")
for c in cambios:
    print("  ", c.fecha, c.origen, "antes", c.bid_antes, "despues", c.bid_despues, "direccion", c.direccion)

efecto = EfectoCambio(
    dias_post=30,
    impresiones_pre7=5000,
    clics_pre7=80,
    impresiones_post=5000,
    dias_post_trafico=7,
    clics_post=200,
    gasto_post=Decimal("200"),
    clics_pre=200,
    gasto_pre=Decimal("200"),
    vendia=True,
)
tray = Trayectoria(cambios=cambios, efecto=efecto)
print("piso_aprendido =", tray.piso_aprendido)

# Hoja que pierde dinero: 400 clics x 1 USD, 4 pedidos de 20 USD (ACoS ~500%).
propia = EvidenciaNivel(
    reciente=Tramo(clics=400, pedidos=4, venta=Decimal("80"), gasto=Decimal("400"), impresiones=40000),
    antiguo=Tramo(clics=0, pedidos=0, venta=Decimal("0"), gasto=Decimal("0"), impresiones=0),
)
econ = EconomiaPlataforma(
    moneda="USD",
    equilibrio_acos_pct=Decimal("24.76"),
    gasto_para_concluir=Decimal("36"),
    confianza_recorte=Decimal("0.80"),
    confianza_subida=Decimal("0.70"),
)


def caso(trayectoria):
    return CasoHoja(
        plataforma="amazon_us",
        hoja_id=1,
        ad_group_id=2,
        bid=BidVigente(valor=Decimal("1.00"), moneda="USD", piso=Decimal("0.10"), techo=Decimal("2.50")),
        economia=Economia(plataforma=econ, target_acos_pct=Decimal("19.8")),
        propia=propia,
        pedidos_inmaduros=0,
        grupo=propia,
        cuenta=propia,
        precio=PrecioVentana(gasto=Decimal("400"), clics=400),
        trayectoria=trayectoria,
        pausa=InsumosPausa(
            cortes=None,
            umbral_clics=100,
            gasto_minimo=Decimal("40"),
            expected_clicks=None,
            politica_economica=None,
        ),
        ventana_desde=D - dt.timedelta(days=90),
        ventana_hasta=D - dt.timedelta(days=10),
        observado_al=dt.datetime(2026, 11, 8, tzinfo=UTC),
    )


sin_regreso = Trayectoria(cambios=(cambios[0],), efecto=efecto)
print("solo la subida del motor (sin la reversa):", politica.decide(caso(sin_regreso)))
print("subida + reversa del dueno               :", politica.decide(caso(tray)))
