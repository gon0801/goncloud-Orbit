# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""P2b: la guarda de desplome restringida a hojas con ventas recientes y volumen. Mide disparo en recortes
contra falsos positivos en hojas-fecha comparables sin cambio de bid. Tambien usa clics en vez de impresiones."""

import csv, collections, datetime as dt

F = lambda x: float(x or 0)
I = lambda x: int(float(x or 0))
D = dt.date.fromisoformat
B = "datos/"
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
        x = dia[h].get(a + dt.timedelta(days=i))
        if x:
            for j in range(4):
                t[j] += x[j]
    return t


cambios = collections.defaultdict(list)
for d in csv.DictReader(open(B + "decisiones.csv")):
    if d["kind"] == "bid" and d["aplicada"] == "t" and d["new_value"]:
        cambios[d["hoja_id"]].append((D(d["dia"]), F(d["old_value"]), F(d["new_value"])))
td = dt.timedelta


def caso(h, d):
    a = suma(h, d - td(days=7), d - td(days=1))
    b = suma(h, d + td(days=1), d + td(days=7))
    prev = suma(h, d - td(days=28), d - td(days=1))
    return a, b, prev


fechas = [dt.date(2026, 8, 30) + td(days=i) for i in range(0, 31, 2)]
for nombre, cond in (
    (
        "vendedora: >=2 pedidos en 28d previos y >=1000 impr en 7d previos",
        lambda a, p: p[2] >= 2 and a[0] >= 1000,
    ),
    ("con clics: >=30 clics en 7d previos", lambda a, p: a[1] >= 30),
    ("volumen: >=3000 impr en 7d previos", lambda a, p: a[0] >= 3000),
):
    cut = []
    ctl = []
    for h, l in cambios.items():
        for d, old, new in l:
            if new < old and d + td(days=9) <= ULT:
                a, b, p = caso(h, d)
                if cond(a, p):
                    cut.append((b[0] / max(a[0], 1), b[1] / max(a[1], 1), h, d, old, new, p))
    for h in dia:
        for d in fechas:
            if any(abs((c[0] - d).days) <= 10 for c in cambios.get(h, [])) or d + td(days=9) > ULT:
                continue
            a, b, p = caso(h, d)
            if cond(a, p):
                ctl.append((b[0] / max(a[0], 1), b[1] / max(a[1], 1)))
    print(f"\n== {nombre}: recortes {len(cut)} | control {len(ctl)}")
    for u in (0.3, 0.4, 0.5):
        for idx, m in ((0, "impresiones"), (1, "clics")):
            n = sum(1 for x in cut if x[idx] < u)
            f = sum(1 for x in ctl if x[idx] < u)
            print(
                f"   {m:11} < {u}: recortes {n}/{len(cut)} ({100 * n / max(len(cut), 1):.0f}%) | control {f}/{len(ctl)} ({100 * f / max(len(ctl), 1):.0f}%)"
            )
# que tamano de paso tenian los recortes a vendedoras, y cuantos recortes seguidos
print("\nrecortes aplicados a hojas con >=2 pedidos en 28 dias previos, por paso:")
c = collections.Counter()
for h, l in cambios.items():
    for d, old, new in l:
        if new < old:
            p = suma(h, d - td(days=28), d - td(days=1))
            if p[2] >= 2:
                c["-25%" if new / old <= 0.86 else "-12%"] += 1
print("  ", dict(c))
