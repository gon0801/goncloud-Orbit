"""Sonda B2 (angulo M2): a que target cae una hoja cuando su producto pierde
elegibilidad, con las funciones reales del ciclo (app.cycle._margen_familia_de_hoja,
productos.meta_de_hoja, goals.cascada_target_acos). Pura: sin base.

Escenario: producto con margen 8 % (meta 4 %), familia con margen 24 % (12 %),
plataforma en 25.5 (destino del respaldo), setting 30. El ciclo anterior la hoja
quedo en 4 % con procedencia margen_producto. Este ciclo el producto pierde
elegibilidad por un motivo PROPIO (p. ej. halo_sin_margen: una venta atribuida de
otro SKU); familia y plataforma siguen midiendo. Se corren N ciclos seguidos.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from app import cycle as c
from app.optimizer import goals as g
from app.optimizer import productos as p

AHORA = dt.datetime(2026, 11, 9, 14, 0, tzinfo=dt.UTC)
HOY = AHORA.date()
FAM = 900
CAMP = 5
HOJA = 7
PLAT = D("25.5")
SETTING = D("30")


def med(margen):
    return g.MedicionMargen(
        margen_neto_pct=margen,
        cobertura=D("1"),
        dias_con_venta=60,
        venta_cubierta=D("1000"),
        ledger_fresco_at=AHORA,
        moneda="MXN",
        ventana_desde=dt.date(2026, 2, 20),
        ventana_hasta=HOY - dt.timedelta(days=15),
    )


def familias(previo, economia):
    return c._FamiliasCiclo(
        mediciones={FAM: med(D("24"))},
        padres={FAM: None},
        fam_por_campana={CAMP: FAM},
        previos={HOJA: previo} if previo is not None else {},
        anclas_grupo={},
        fraccion=D("0.5"),
        hoy=HOY,
        destino=PLAT,
        notas={},
        economia=economia,
    )


for economia in (p.ECONOMIA_AUTO03, p.POLITICA_ECONOMIA):
    previo = c._PrevioHoja(D("4"), "margen_producto", 100, 6)
    serie = []
    for ciclo in range(1, 31):
        fam = familias(previo, economia)
        t_fam, _ = c._margen_familia_de_hoja(fam, CAMP, HOJA)
        destino = g.cascada_target_acos(None, SETTING, PLAT, "platform", t_fam)
        # El producto ya no mide (halo_sin_margen): la meta de la evaluacion es SinDato.
        res = p.meta_de_hoja(
            p.SinDato(("halo_sin_margen",)),
            ultimo=previo.target,
            destino=destino,
            tiene_previo=previo.procedencia == "margen_producto",
            version=economia,
        )
        target = g.cascada_target_acos(
            None, SETTING, PLAT, "platform", t_fam, target_margen_producto=res.aplicado
        )
        pel = g.peldano_target_acos(
            None, "platform", PLAT, SETTING, t_fam, target_margen_producto=res.aplicado
        )
        serie.append((ciclo, target, pel))
        previo = c._PrevioHoja(target, pel, 100 + ciclo, 6)
    print(f"== {economia} ==")
    for ciclo, target, pel in serie:
        if ciclo <= 3 or ciclo in (10, 16, 20, 26, 27, 30):
            print(f"  ciclo {ciclo:2d}: target={target} ({pel})")
    sobre_familia = sum(1 for _, t, _ in serie if t > D("12"))
    print(f"  ciclos con target por encima de la economia de la familia (12): {sobre_familia}")
