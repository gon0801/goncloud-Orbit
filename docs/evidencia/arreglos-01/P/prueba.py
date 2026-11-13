"""P (AD-U7): el codigo desplegado no sube el bid de una hoja cuya pausa
espera por una venta reciente (C-H4). Funcion pura: sin base ni red.
Se corre con `bash docs/evidencia/arreglos-01/en_prod.sh .../P/prueba.py`.
"""

import datetime as dt
from decimal import Decimal

from app.optimizer.caso import (
    VERSION_PROTECCION_VENTA,
    BidVigente,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EpisodioProteccion,
    EvidenciaNivel,
    InsumosPausa,
    InsumosProteccion,
    LecturaReciente,
    PrecioVentana,
    Tramo,
    Trayectoria,
)
from app.optimizer.politica import MOTIVO_SUBIDA_EN_ESPERA, Mantener, Mover, decide
from app.optimizer.windows import AgregadoMetricas

HOY = dt.date(2026, 11, 9)
ANCLA = HOY - dt.timedelta(days=4)
VENCE = ANCLA + dt.timedelta(days=10)
OBS = dt.datetime(2026, 11, 9, 8, 0, tzinfo=dt.UTC)


def tramo(clics=0, pedidos=0, venta="0", gasto="0", impresiones=0):
    return Tramo(
        clics=clics,
        pedidos=pedidos,
        venta=Decimal(venta),
        gasto=Decimal(gasto),
        impresiones=impresiones,
    )


def nivel(reciente=None):
    return EvidenciaNivel(reciente=reciente if reciente is not None else tramo(), antiguo=tramo())


def cortes():
    fechas = tuple(HOY - dt.timedelta(days=20 - i) for i in range(10))
    return AgregadoMetricas(
        window_start=fechas[0],
        window_end=fechas[-1],
        fechas=fechas,
        metric_currency="MXN",
        cost=Decimal("500"),
        ad_revenue=Decimal("0"),
        revenue_same_sku=Decimal("0"),
        impressions=5000,
        clicks=120,
        orders=0,
        observed_at_max=OBS,
    )


def proteccion(*, frena_subidas):
    return InsumosProteccion(
        version=VERSION_PROTECCION_VENTA,
        evaluado_el=HOY,
        reciente=LecturaReciente(pedidos=1, ancla=ANCLA, cubierta=True, observado_al=OBS),
        ventana_desde=HOY - dt.timedelta(days=20),
        ventana_hasta=HOY - dt.timedelta(days=11),
        episodio=EpisodioProteccion(ancla=ANCLA, vence_el=VENCE),
        frena_subidas=frena_subidas,
    )


def caso(frena_subidas):
    pausa = InsumosPausa(
        cortes(),
        100,
        Decimal("500"),
        None,
        None,
        proteccion=proteccion(frena_subidas=frena_subidas),
    )
    propia = nivel(tramo(clics=50, pedidos=5, venta="5000", gasto="200", impresiones=2000))
    cuenta = nivel(
        tramo(clics=10000, pedidos=100, venta="100000", gasto="9000", impresiones=400000)
    )
    return CasoHoja(
        plataforma="amazon_mx",
        hoja_id=1001,
        ad_group_id=2001,
        bid=BidVigente(Decimal("10.00"), "MXN", Decimal("0.50"), Decimal("50.00")),
        economia=Economia(
            plataforma=EconomiaPlataforma(
                moneda="MXN",
                equilibrio_acos_pct=Decimal("41.43"),
                gasto_para_concluir=Decimal("350"),
                confianza_recorte=Decimal("0.80"),
                confianza_subida=Decimal("0.70"),
            ),
            target_acos_pct=Decimal("20.72"),
        ),
        propia=propia,
        pedidos_inmaduros=0,
        grupo=propia,
        cuenta=cuenta,
        precio=PrecioVentana(Decimal("100"), 50),
        trayectoria=Trayectoria(cambios=(), efecto=None),
        pausa=pausa,
        ventana_desde=HOY - dt.timedelta(days=90),
        ventana_hasta=HOY - dt.timedelta(days=10),
        observado_al=OBS,
    )


en_espera = decide(caso(True))
assert en_espera == Mantener(MOTIVO_SUBIDA_EN_ESPERA), en_espera
print(f"frena_subidas=True: {en_espera}")

historica = decide(caso(False))
esperado = Mover(Decimal("0.15"), Decimal("11.5000"), "bajo_target", "hoja")
assert historica == esperado, historica
print(f"frena_subidas=False: {historica}")

congelado = CasoHoja.desde_json(caso(True).como_json())
assert decide(congelado) == en_espera
print("congelado: reproduce la espera")
print("P OK")
