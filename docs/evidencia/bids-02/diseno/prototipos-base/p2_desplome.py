# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""Prototipo P2: guarda de desplome. Tras un recorte aplicado en el dia d, compara impresiones de los 7 dias
previos (d-7..d-1) contra los 7 posteriores (d+1..d+7). Mide tambien la volatilidad natural en hojas sin
cambio de bid, para calibrar el umbral (falsos positivos)."""

import csv, collections, datetime as dt, statistics as st

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
print("ultimo dia con datos:", ULT)


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
        cambios[d["hoja_id"]].append(
            (D(d["dia"]), F(d["old_value"]), F(d["new_value"]), d["platform"])
        )
MIN = 200


def ratio(h, d):
    a = suma(h, d - dt.timedelta(days=7), d - dt.timedelta(days=1))
    b = suma(h, d + dt.timedelta(days=1), d + dt.timedelta(days=7))
    return a, b


# recortes
filas = []
for h, l in cambios.items():
    for d, old, new, plat in l:
        if new >= old or d + dt.timedelta(days=9) > ULT:
            continue
        a, b = ratio(h, d)
        if a[0] < MIN:
            continue
        filas.append((plat, h, d, old, new, a, b, b[0] / a[0]))
# control: hojas-fecha sin ningun cambio aplicado en +-10 dias
ctrl = []
fechas = [dt.date(2026, 9, 2) + dt.timedelta(days=i) for i in range(0, 28, 3)]
for h in dia:
    for d in fechas:
        if any(abs((c[0] - d).days) <= 10 for c in cambios.get(h, [])):
            continue
        a, b = ratio(h, d)
        if a[0] < MIN:
            continue
        ctrl.append(b[0] / a[0])
print(
    f"recortes aplicados con >= {MIN} impresiones previas y 9 dias de datos despues: {len(filas)} | hojas-fecha de control: {len(ctrl)}"
)
q = lambda xs, p: sorted(xs)[int(p * (len(xs) - 1))]
rs = [f[7] for f in filas]
print(
    f"razon impresiones despues/antes  recortadas: p10 {q(rs, 0.1):.2f} p25 {q(rs, 0.25):.2f} mediana {q(rs, 0.5):.2f} p75 {q(rs, 0.75):.2f}"
)
print(
    f"                                  control:    p10 {q(ctrl, 0.1):.2f} p25 {q(ctrl, 0.25):.2f} mediana {q(ctrl, 0.5):.2f} p75 {q(ctrl, 0.75):.2f}"
)
for u in (0.2, 0.3, 0.4, 0.5):
    n = sum(1 for r in rs if r < u)
    m = sum(1 for r in ctrl if r < u)
    print(
        f"  umbral {u:.1f}: dispara en {n}/{len(rs)} recortes ({100 * n / len(rs):.0f}%) | falsos positivos en control {m}/{len(ctrl)} ({100 * m / len(ctrl):.0f}%)"
    )
print("\nrecortes con desplome (<0.4) que tenian pedidos en los 14 dias previos:")
for plat, h, d, old, new, a, b, r in sorted(filas, key=lambda f: f[7]):
    if r < 0.4:
        prev = suma(h, d - dt.timedelta(days=14), d - dt.timedelta(days=1))
        if prev[2] > 0:
            print(
                f"  {plat} hoja {h} {d} bid {old:.2f}->{new:.2f} ({100 * (new / old - 1):.0f}%) impr {a[0]}->{b[0]} clics {a[1]}->{b[1]} | 14d previos: {prev[2]} pedidos, venta {prev[3]:.0f}"
            )
# por tamano del recorte acumulado: bid relativo al inicial vs desplome
print("\ndesplome segun el recorte de ese paso:")
for nombre, f in (
    ("paso -12%", lambda x: x[4] / x[3] > 0.86),
    ("paso -25%", lambda x: x[4] / x[3] <= 0.86),
):
    s = [x[7] for x in filas if f(x)]
    if s:
        print(
            f"  {nombre}: n={len(s)} mediana {q(s, 0.5):.2f} | <0.4 en {sum(1 for r in s if r < 0.4)}"
        )
