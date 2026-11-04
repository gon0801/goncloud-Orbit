# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""P2c: volatilidad natural medida en agosto (antes del 2026-09-02 el motor no aplicaba) sobre hojas hoy
activas, contra lo que paso tras los recortes aplicados. Condicion: hoja con volumen (>=1000 impresiones en
los 7 dias previos)."""

import csv, collections, datetime as dt

F = lambda x: float(x or 0)
I = lambda x: int(float(x or 0))
D = dt.date.fromisoformat
td = dt.timedelta
B = "datos/"
hojas = {r["hoja_id"]: r for r in csv.DictReader(open(B + "hojas.csv"))}
act = {
    h
    for h, r in hojas.items()
    if r["status_hoja"] == "ENABLED"
    and r["status_ad_group"] == "ENABLED"
    and r["status_campana"] == "ENABLED"
}
dia = collections.defaultdict(dict)
for r in csv.DictReader(open(B + "hojas_dia.csv")):
    dia[r["hoja_id"]][D(r["dia"])] = (
        I(r["impresiones"]),
        I(r["clicks"]),
        I(r["orders"]),
        F(r["venta_ads"]),
    )
ULT = max(max(v) for v in dia.values())


def suma(h, a, b):
    t = [0, 0, 0, 0.0]
    for i in range((b - a).days + 1):
        x = dia[h].get(a + td(days=i))
        if x:
            for j in range(4):
                t[j] += x[j]
    return t


cambios = collections.defaultdict(list)
for d in csv.DictReader(open(B + "decisiones.csv")):
    if d["kind"] == "bid" and d["aplicada"] == "t" and d["new_value"]:
        cambios[d["hoja_id"]].append((D(d["dia"]), F(d["old_value"]), F(d["new_value"])))


def r(h, d):
    a = suma(h, d - td(days=7), d - td(days=1))
    b = suma(h, d + td(days=1), d + td(days=7))
    return a, b


for MINI in (1000, 3000):
    base = []
    for h in act:
        for d in (
            dt.date(2026, 8, 9),
            dt.date(2026, 8, 13),
            dt.date(2026, 8, 17),
            dt.date(2026, 8, 21),
            dt.date(2026, 8, 25),
        ):
            a, b = r(h, d)
            if a[0] >= MINI:
                base.append(b[0] / a[0])
    cut = []
    for h, l in cambios.items():
        if h not in act:
            continue
        for d, old, new in l:
            if new < old and d + td(days=9) <= ULT:
                a, b = r(h, d)
                if a[0] >= MINI:
                    cut.append((b[0] / a[0], new / old, h, d, a, b))
    q = lambda xs, p: sorted(xs)[int(p * (len(xs) - 1))]
    print(f"\n== hojas activas hoy con >= {MINI} impresiones en 7 dias previos")
    print(
        f"   base agosto (sin motor): n={len(base)} p10 {q(base, 0.1):.2f} p25 {q(base, 0.25):.2f} mediana {q(base, 0.5):.2f}"
    )
    cr = [c[0] for c in cut]
    print(
        f"   tras recorte aplicado:  n={len(cr)} p10 {q(cr, 0.1):.2f} p25 {q(cr, 0.25):.2f} mediana {q(cr, 0.5):.2f}"
    )
    for u in (0.3, 0.4, 0.5):
        print(
            f"   umbral {u}: tras recorte {sum(1 for x in cr if x < u)}/{len(cr)} ({100 * sum(1 for x in cr if x < u) / len(cr):.0f}%) | base agosto {sum(1 for x in base if x < u)}/{len(base)} ({100 * sum(1 for x in base if x < u) / len(base):.0f}%)"
        )
    for nombre, f in (("paso -12%", lambda c: c[1] > 0.86), ("paso -25%", lambda c: c[1] <= 0.86)):
        s = [c[0] for c in cut if f(c)]
        if s:
            print(
                f"   {nombre}: n={len(s)} mediana {q(s, 0.5):.2f} | <0.4: {sum(1 for x in s if x < 0.4)} ({100 * sum(1 for x in s if x < 0.4) / len(s):.0f}%)"
            )
