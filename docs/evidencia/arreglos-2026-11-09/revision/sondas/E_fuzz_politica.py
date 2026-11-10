"""Sonda E: fuzz de politica.decide (niveles_v3) con entradas extremas.

Invariantes que se verifican sobre cada veredicto:
- Mover: bid_nuevo en [piso, techo]; delta en [-30%, +20%] del vigente; direccion = signo del factor.
- Regresar: bid_nuevo en [piso, techo] y > vigente.
- Ninguna excepcion (una excepcion por hoja tumba el ciclo entero de la plataforma).
"""

from __future__ import annotations

import datetime as dt
import random
import sys
import traceback
from collections import Counter
from decimal import Decimal

from app.optimizer import politica
from app.optimizer.caso import (
    BidVigente,
    CambioBid,
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
from app.optimizer.windows import AgregadoMetricas

rnd = random.Random(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
D = dt.date(2026, 11, 9)


def dec(lo, hi, nd=2):
    return Decimal(str(round(rnd.uniform(lo, hi), nd)))


def maybe(v, p=0.05):
    return None if rnd.random() < p else v


def tramo():
    clics = rnd.choice([0, 0, 1, 5, 20, 60, 300, 2000])
    pedidos = rnd.choice([0, 0, 0, 1, 2, 3, 10, 50]) if clics else rnd.choice([0, 0, 1])
    gasto = dec(0, 3, 2) * clics if clics else Decimal(0)
    venta = dec(5, 80) * pedidos if pedidos else Decimal(0)
    imp = clics * rnd.choice([10, 50, 200]) if clics else rnd.choice([0, 100])
    return Tramo(maybe(clics), maybe(pedidos), maybe(venta), maybe(gasto), maybe(imp))


def nivel():
    return EvidenciaNivel(reciente=tramo(), antiguo=tramo())


def suma(a: EvidenciaNivel, b: EvidenciaNivel) -> EvidenciaNivel:
    def s(x, y):
        return None if x is None or y is None else x + y

    def t(x: Tramo, y: Tramo):
        return Tramo(*(s(getattr(x, f), getattr(y, f)) for f in ("clics", "pedidos", "venta", "gasto", "impresiones")))

    return EvidenciaNivel(reciente=t(a.reciente, b.reciente), antiguo=t(a.antiguo, b.antiguo))


def cortes():
    if rnd.random() < 0.1:
        return None
    fechas = tuple(D - dt.timedelta(days=10 + i) for i in range(rnd.choice([3, 7, 30])))
    return AgregadoMetricas(
        window_start=D - dt.timedelta(days=39),
        window_end=D - dt.timedelta(days=10),
        fechas=fechas,
        metric_currency=rnd.choice(["USD", "USD", "MXN"]),
        cost=maybe(dec(0, 900)),
        ad_revenue=maybe(dec(0, 900)),
        revenue_same_sku=None,
        impressions=1000,
        clicks=maybe(rnd.choice([0, 10, 100, 400])),
        orders=maybe(rnd.choice([0, 0, 1, 3])),
        observed_at_max=dt.datetime(2026, 11, 8, tzinfo=dt.UTC),
    )


def trayectoria(valor):
    if rnd.random() < 0.5:
        return Trayectoria(cambios=(), efecto=None)
    n = rnd.choice([1, 2, 3])
    cambios = []
    fecha = D - dt.timedelta(days=rnd.choice([3, 8, 20, 60]))
    for i in range(n):
        antes = dec(0.05, 5)
        despues = antes * Decimal(rnd.choice(["0.75", "0.88", "1.15", "1.0", "1.33"]))
        origen = rnd.choice(["motor", "motor", "regreso_por_desplome", "regreso_del_dueno", "ajuste_de_campana"])
        if origen == "ajuste_de_campana":
            despues = antes
        cambios.append(CambioBid(fecha=fecha - dt.timedelta(days=(n - i) * 5), bid_antes=antes, bid_despues=despues, origen=origen))
    efecto = EfectoCambio(
        dias_post=rnd.choice([0, 3, 4, 7, 10, 15, 40]),
        impresiones_pre7=maybe(rnd.choice([0, 100, 2000])),
        clics_pre7=maybe(rnd.choice([0, 5, 25])),
        impresiones_post=maybe(rnd.choice([0, 10, 500, 3000])),
        dias_post_trafico=rnd.choice([0, 4, 7]),
        clics_post=maybe(rnd.choice([0, 5, 25, 100])),
        gasto_post=maybe(dec(0, 100)),
        clics_pre=maybe(rnd.choice([0, 5, 50])),
        gasto_pre=maybe(dec(0, 100)),
        vendia=rnd.choice([True, False, None]),
    )
    return Trayectoria(cambios=tuple(cambios), efecto=efecto)


def caso():
    plataforma = rnd.choice(["amazon_us", "amazon_mx"])
    moneda = "USD" if plataforma == "amazon_us" else "MXN"
    piso = Decimal("0.10") if moneda == "USD" else Decimal("1.00")
    techo = Decimal("2.50") if moneda == "USD" else Decimal("45.00")
    if rnd.random() < 0.3:
        piso = dec(0.02, 1)
        techo = piso + dec(0, 5)
    valor = maybe(rnd.choice([Decimal("0.02"), dec(0.05, 1), dec(1, 10), dec(10, 80)]), 0.05)
    propia = None if rnd.random() < 0.1 else nivel()
    grupo = None if rnd.random() < 0.1 else (suma(propia, nivel()) if propia is not None else nivel())
    cuenta = None if rnd.random() < 0.05 else (suma(grupo, nivel()) if grupo is not None else nivel())
    target = rnd.choice([Decimal("0.01"), Decimal("5"), Decimal("25"), Decimal("55"), Decimal("200"), dec(1, 80)])
    equilibrio = rnd.choice([None, Decimal("-5"), Decimal("0"), Decimal("0.001"), dec(5, 60)])
    econ = EconomiaPlataforma(
        moneda=moneda,
        equilibrio_acos_pct=equilibrio,
        gasto_para_concluir=Decimal("36") if moneda == "USD" else Decimal("350"),
        confianza_recorte=rnd.choice([Decimal("0.5"), Decimal("0.8"), Decimal("0.99")]),
        confianza_subida=rnd.choice([Decimal("0.5"), Decimal("0.7"), Decimal("0.99")]),
    )
    pausa = InsumosPausa(
        cortes=cortes(),
        umbral_clics=rnd.choice([20, 100, 300]),
        gasto_minimo=Decimal("40") if moneda == "USD" else Decimal("500"),
        expected_clicks=None,
        politica_economica=rnd.choice([None, "economic_pause_v1"]),
    )
    return CasoHoja(
        plataforma=plataforma,
        hoja_id=1,
        ad_group_id=2,
        bid=BidVigente(valor=valor, moneda=moneda, piso=piso, techo=techo),
        economia=Economia(plataforma=econ, target_acos_pct=target),
        propia=propia,
        pedidos_inmaduros=maybe(rnd.choice([0, 0, 1])),
        grupo=grupo,
        cuenta=cuenta,
        precio=PrecioVentana(gasto=maybe(dec(0, 100)), clics=maybe(rnd.choice([0, 10, 100]))),
        trayectoria=trayectoria(valor),
        pausa=pausa,
        ventana_desde=D - dt.timedelta(days=90),
        ventana_hasta=D - dt.timedelta(days=10),
        observado_al=dt.datetime(2026, 11, 8, tzinfo=dt.UTC),
    )


N = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
tipos = Counter()
violaciones = []
excepciones = Counter()
ejemplo_exc = {}
for _ in range(N):
    c = caso()
    try:
        v = politica.decide(c)
    except Exception as exc:  # noqa: BLE001
        clave = f"{type(exc).__name__}: {str(exc)[:80]}"
        excepciones[clave] += 1
        ejemplo_exc.setdefault(clave, (c, traceback.format_exc()))
        continue
    tipos[type(v).__name__ + ":" + v.motivo] += 1
    b = c.bid
    if isinstance(v, politica.Mover):
        delta = v.bid_nuevo - b.valor
        if not (b.piso <= v.bid_nuevo <= b.techo):
            violaciones.append(("mover_fuera_rango", b, v))
        if delta > Decimal("0.20") * b.valor or -delta > Decimal("0.30") * b.valor:
            violaciones.append(("mover_magnitud", b, v))
        if (v.factor < 0) != (delta < 0):
            violaciones.append(("mover_direccion", b, v))
    if isinstance(v, politica.Regresar):
        if not (b.piso <= v.bid_nuevo <= b.techo) or v.bid_nuevo <= b.valor:
            violaciones.append(("regresar_invalido", b, v))
        if v.bid_nuevo - b.valor > Decimal("0.20") * b.valor:
            tipos["regresar_sobre_20pct"] += 1

print(f"casos={N}")
print("veredictos (top 30):")
for k, n in tipos.most_common(30):
    print(f"  {n:6d} {k}")
print(f"violaciones={len(violaciones)}")
for viol in violaciones[:10]:
    print("  ", viol)
print(f"excepciones={sum(excepciones.values())}")
for k, n in excepciones.most_common():
    print(f"  {n:6d} {k}")
    c, tb = ejemplo_exc[k]
    print("    bid:", c.bid, "target:", c.economia.target_acos_pct, "equilibrio:", c.economia.plataforma.equilibrio_acos_pct)
    print("    ", tb.strip().splitlines()[-3:])
