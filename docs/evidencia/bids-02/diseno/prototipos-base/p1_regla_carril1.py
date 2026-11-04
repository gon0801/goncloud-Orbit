# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""Prototipo P1: rejuega los recortes decididos en vivo (2026-09-02..10-09) con la regla del carril 1.
Regla: (a) sin pedidos y gasto < umbral => no hay evidencia propia; (b) entonces manda el ad group a 90 dias:
ACoS de grupo > 1.15 x target (o grupo sin venta con gasto >= umbral) => recorta; si no => no recorta.
(c) con pedidos => se deja la banda actual (se mide aparte el 'freno por valor')."""

import csv, collections, datetime as dt, sys

F = lambda x: float(x or 0)
I = lambda x: int(float(x or 0))
D = dt.date.fromisoformat
B = "datos/"
hojas = {r["hoja_id"]: r for r in csv.DictReader(open(B + "hojas.csv"))}
sem = collections.defaultdict(list)
for r in csv.DictReader(open(B + "hojas_semana.csv")):
    h = hojas.get(r["hoja_id"])
    if h:
        sem[(r["platform"], h["ad_group_id"])].append(
            (D(r["semana"]), F(r["costo"]), F(r["venta_ads"]), I(r["orders"]), I(r["clicks"]))
        )
TARGET = {"amazon_mx": 20.72, "amazon_us": float(sys.argv[1]) if len(sys.argv) > 1 else 20.79}
UMBRAL = {"amazon_mx": 350.0, "amazon_us": 36.0}


def grupo(plat, ag, dia):
    c = v = o = k = 0
    for s, co, ve, od, cl in sem[(plat, ag)]:
        if dia - dt.timedelta(days=97) <= s <= dia - dt.timedelta(days=10):
            c += co
            v += ve
            o += od
            k += cl
    return c, v, o, k


res = collections.defaultdict(collections.Counter)
for d in csv.DictReader(open(B + "decisiones.csv")):
    if (
        d["kind"] != "bid"
        or d["modo_ciclo"] != "live"
        or not d["new_value"]
        or F(d["new_value"]) >= F(d["old_value"])
    ):
        continue
    plat = d["platform"]
    h = hojas.get(d["hoja_id"])
    t = TARGET[plat] / 100
    u = UMBRAL[plat]
    res[plat]["recortes decididos"] += 1
    if I(d["v_orders"]) > 0:
        res[plat]["con pedidos (banda actual)"] += 1
        acos = F(d["v_costo"]) / F(d["v_venta"]) if F(d["v_venta"]) else 9
        res[plat]["  de esos, ACoS de la hoja bajo margen aprox (2x target)"] += acos < 2 * t
        continue
    if F(d["v_costo"]) >= u:
        res[plat]["sin pedidos, con gasto >= umbral: recorta"] += 1
        continue
    c, v, o, k = grupo(plat, h["ad_group_id"], D(d["dia"])) if h else (0, 0, 0, 0)
    if v > 0 and c > 1.15 * t * v and o >= 3:
        res[plat]["sin evidencia propia, grupo en sangria: recorta"] += 1
    elif v == 0 and c >= u:
        res[plat]["sin evidencia propia, grupo sin venta con gasto: recorta"] += 1
    elif v > 0 and c <= t * v:
        res[plat]["sin evidencia propia, grupo mejor que target: NO recorta"] += 1
    else:
        res[plat]["sin evidencia propia, grupo entre target y 1.15x o sin datos: NO recorta"] += 1
for plat, c in res.items():
    print(f"\n== {plat} target {TARGET[plat]}% umbral {UMBRAL[plat]}")
    for k, v in c.items():
        print(f"  {v:4}  {k}")
    quedan = (
        sum(v for k, v in c.items() if "recorta" in k and "NO" not in k)
        + c["con pedidos (banda actual)"]
    )
    print(
        f"  => con la regla nueva quedan {quedan} de {c['recortes decididos']} recortes ({100 * quedan / c['recortes decididos']:.0f}%)"
    )
