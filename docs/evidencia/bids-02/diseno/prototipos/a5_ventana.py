# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""A5. Ventana de 90 dias con mas peso a lo reciente, en enteros por tramo. Compara, por ad group, que
ventana predice mejor la conversion de las 4 semanas maduras mas recientes (2026-09-06..10-03):
90 dias planos, 60 dias planos, dos tramos (ultimos 41 dias peso 1, los 40 anteriores peso 1/2) y peso
exponencial (vida media 30 dias). Mismo diseno que glm/scripts-a/p2_5_estabilidad.py (n chico: se lee la
direccion, no el decimal). Tambien cuenta cuantas decisiones cambian por redondear pedidos fraccionarios."""

import collections
import math

import politica as P
from carga import *

FIN = dt.date(2026, 9, 5)
OUT = (dt.date(2026, 9, 6), dt.date(2026, 10, 3))


def rangos(xs):
    o = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and xs[o[j + 1]] == xs[o[i]]:
            j += 1
        for k in range(i, j + 1):
            r[o[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = rangos(a), rangos(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else float("nan")


def pesado(hs, peso):
    c = o = 0.0
    for h in hs:
        for f, x in serie.get(h, {}).items():
            if f <= FIN:
                w = peso((FIN - f).days)
                c += w * x[1]
                o += w * x[3]
    return c, o


esquemas = {
    "90 dias planos": lambda e: 1.0 if e <= 89 else 0.0,
    "60 dias planos": lambda e: 1.0 if e <= 59 else 0.0,
    "dos tramos (1 y 1/2)": lambda e: 1.0 if e <= 40 else (0.5 if e <= 80 else 0.0),
    "exponencial, vida media 30 d": lambda e: math.exp(-math.log(2) * e / 30) if e <= 110 else 0.0,
}
plat_cvr = {}
for plat in ("amazon_mx", "amazon_us"):
    hs = [h for h in serie if h in hojas and hojas[h]["platform"] == plat]
    c, o = pesado(hs, esquemas["90 dias planos"])
    plat_cvr[plat] = o / c
print(
    "ventana                        | n  | Spearman | error absoluto medio (puntos de conversion)"
)
for nombre, peso in esquemas.items():
    pred, real = [], []
    for (plat, ag), hs in por_grupo.items():
        s = suma_hojas(hs, *OUT)
        c, o = pesado(hs, peso)
        c90, _ = pesado(hs, esquemas["90 dias planos"])
        if s[1] < 30 or c90 < 30:
            continue
        pred.append(
            (o + 40 * plat_cvr[plat]) / (c + 40)
        )  # encogido a la plataforma con 40 clics, como p2_5
        real.append(s[3] / s[1])
    mae = 100 * sum(abs(a - b) for a, b in zip(pred, real)) / len(pred)
    print(f"{nombre:30} | {len(pred):2} | {spearman(pred, real):8.2f} | {mae:.2f}")

# Redondeo en contra del movimiento: cuantas decisiones del mes cambian frente a no redondear.
cambios = total = 0
for d in decisiones:
    if (
        d["kind"] != "bid"
        or d["modo_ciclo"] != "live"
        or not d["new_value"]
        or d["hoja_id"] not in hojas
    ):
        continue
    c = P.arma_caso(d["hoja_id"], D(d["dia"]), F(d["old_value"]))
    if c["propia"]["pedidos_crudo"] < 1 or c["propia"]["pedidos"] == int(c["propia"]["pedidos"]):
        continue
    total += 1
    a = P.decide(c)
    c2 = dict(c)
    c2["propia"] = dict(c["propia"])
    c2["propia"]["pedidos"] = round(c["propia"]["pedidos"] + 0.01)  # mitad hacia arriba
    b = P.decide(c2)
    cambios += a[0] != b[0]
print(
    f"\nredondeo: de {total} decisiones del mes con pedidos fraccionarios, {cambios} cambian de accion si se redondea al entero mas cercano"
)
