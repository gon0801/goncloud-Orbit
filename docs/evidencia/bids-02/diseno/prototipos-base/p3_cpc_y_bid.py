# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""P3: (a) cuanto baja el CPC cuando baja el bid (14 dias antes vs 14 despues de un recorte aplicado, con
>=15 clics en ambos lados); (b) que tan bien sustituye el CPC del ad group al CPC de la hoja; (c) CPC/bid."""

import csv, collections, datetime as dt, statistics as st

F = lambda x: float(x or 0)
I = lambda x: int(float(x or 0))
D = dt.date.fromisoformat
td = dt.timedelta
B = "datos/"
hojas = {r["hoja_id"]: r for r in csv.DictReader(open(B + "hojas.csv"))}
dia = collections.defaultdict(dict)
for r in csv.DictReader(open(B + "hojas_dia.csv")):
    dia[r["hoja_id"]][D(r["dia"])] = (
        I(r["impresiones"]),
        I(r["clicks"]),
        F(r["costo"]),
        I(r["orders"]),
        F(r["venta_ads"]),
    )


def suma(h, a, b):
    t = [0, 0, 0.0, 0, 0.0]
    for i in range((b - a).days + 1):
        x = dia[h].get(a + td(days=i))
        if x:
            for j in range(5):
                t[j] += x[j]
    return t


cambios = collections.defaultdict(list)
for d in csv.DictReader(open(B + "decisiones.csv")):
    if d["kind"] == "bid" and d["aplicada"] == "t" and d["new_value"]:
        cambios[d["hoja_id"]].append((D(d["dia"]), F(d["old_value"]), F(d["new_value"])))
q = lambda xs, p: sorted(xs)[int(p * (len(xs) - 1))]
print("(a) elasticidad del CPC al bid")
for nombre, f in (
    ("paso -12%", lambda o, n: n / o > 0.86),
    ("paso -25%", lambda o, n: n / o <= 0.86),
):
    filas = []
    for h, l in cambios.items():
        l = sorted(l)
        for i, (d, old, new) in enumerate(l):
            if new >= old or not f(old, new):
                continue
            ini = max(d - td(days=14), l[i - 1][0] + td(days=1)) if i > 0 else d - td(days=14)
            fin = (
                min(d + td(days=14), l[i + 1][0] - td(days=1))
                if i + 1 < len(l)
                else d + td(days=14)
            )
            a = suma(h, ini, d - td(days=1))
            b = suma(h, d + td(days=1), fin)
            if a[1] >= 15 and b[1] >= 15:
                filas.append(
                    (
                        (b[2] / b[1]) / (a[2] / a[1]),
                        new / old,
                        (a[2] / a[1]) / old,
                        (b[2] / b[1]) / new,
                    )
                )
    if filas:
        print(
            f"  {nombre}: n={len(filas)} | bid x{st.median(x[1] for x in filas):.2f} -> CPC x{st.median(x[0] for x in filas):.2f} (p25 {q([x[0] for x in filas], 0.25):.2f}, p75 {q([x[0] for x in filas], 0.75):.2f}) | CPC/bid antes {st.median(x[2] for x in filas):.2f}, despues {st.median(x[3] for x in filas):.2f}"
        )
print(
    "\n(b) CPC de la hoja contra CPC de su ad group (30 dias, 2026-09-06..10-05), hojas con >=20 clics"
)
ag = collections.defaultdict(lambda: [0, 0.0])
hj = {}
for h in dia:
    if h not in hojas:
        continue
    s = suma(h, dt.date(2026, 9, 6), dt.date(2026, 10, 5))
    if s[1] > 0:
        ag[hojas[h]["ad_group_id"]][0] += s[1]
        ag[hojas[h]["ad_group_id"]][1] += s[2]
        hj[h] = s
for plat in ("amazon_mx", "amazon_us"):
    r = []
    for h, s in hj.items():
        if hojas[h]["platform"] != plat or s[1] < 20:
            continue
        g = ag[hojas[h]["ad_group_id"]]
        gc = (g[1] - s[2]) / max(g[0] - s[1], 1)  # leave-one-out
        if g[0] - s[1] >= 20 and gc > 0:
            r.append((s[2] / s[1]) / gc)
    if r:
        print(
            f"  {plat}: n={len(r)} razon CPC hoja / CPC resto del grupo: p10 {q(r, 0.1):.2f} p25 {q(r, 0.25):.2f} mediana {q(r, 0.5):.2f} p75 {q(r, 0.75):.2f} p90 {q(r, 0.9):.2f} | dentro de +-30%: {100 * sum(1 for x in r if 0.7 <= x <= 1.3) / len(r):.0f}%"
        )
print("\n(c) CPC pagado / bid actual, hojas activas con >=20 clics en los ultimos 14 dias")
for plat in ("amazon_mx", "amazon_us"):
    r = []
    for h in dia:
        hh = hojas.get(h)
        if not hh or hh["platform"] != plat or not hh["bid_actual"]:
            continue
        ult = sorted(cambios.get(h, []))[-1][0] if cambios.get(h) else dt.date(2026, 9, 1)
        s = suma(h, max(ult + td(days=1), dt.date(2026, 9, 20)), dt.date(2026, 10, 8))
        if s[1] >= 20:
            r.append((s[2] / s[1]) / F(hh["bid_actual"]))
    if r:
        print(
            f"  {plat}: n={len(r)} p10 {q(r, 0.1):.2f} mediana {q(r, 0.5):.2f} p90 {q(r, 0.9):.2f} | CPC mayor que el bid en {sum(1 for x in r if x > 1.0)}"
        )
