# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""Carga comun de los prototipos del runner A. Solo biblioteca estandar.
Datos: ./datos (enlace a prototipos/datos). Serie diaria por hoja: hojas_dia.csv desde 2026-08-01;
antes de esa fecha se usa hojas_semana.csv con el total de la semana puesto en su lunes (aproximacion
declarada: solo afecta al tramo antiguo de la ventana de 90 dias)."""

import collections
import csv
import datetime as dt

B = "datos/"
F = lambda x: float(x or 0)
I = lambda x: int(float(x or 0))
D = dt.date.fromisoformat
td = dt.timedelta
INICIO_DIARIO = dt.date(2026, 8, 1)

hojas = {r["hoja_id"]: r for r in csv.DictReader(open(B + "hojas.csv"))}
activa = {
    h
    for h, r in hojas.items()
    if r["status_hoja"] == r["status_ad_group"] == r["status_campana"] == "ENABLED"
}
# serie[h][fecha] = [impresiones, clicks, costo, orders, venta]
serie = collections.defaultdict(dict)
for r in csv.DictReader(open(B + "hojas_dia.csv")):
    serie[r["hoja_id"]][D(r["dia"])] = [
        I(r["impresiones"]),
        I(r["clicks"]),
        F(r["costo"]),
        I(r["orders"]),
        F(r["venta_ads"]),
    ]
for r in csv.DictReader(open(B + "hojas_semana.csv")):
    s = D(r["semana"])
    if s + td(days=6) < INICIO_DIARIO:
        serie[r["hoja_id"]][s] = [
            I(r["impresiones"]),
            I(r["clicks"]),
            F(r["costo"]),
            I(r["orders"]),
            F(r["venta_ads"]),
        ]
ULTIMO = max(max(v) for v in serie.values())
por_grupo = collections.defaultdict(list)
for h, r in hojas.items():
    if h in serie:
        por_grupo[(r["platform"], r["ad_group_id"])].append(h)
decisiones = list(csv.DictReader(open(B + "decisiones.csv")))
aplicadas = collections.defaultdict(list)  # hoja -> [(dia, old, new, motivo)]
for d in decisiones:
    if d["kind"] == "bid" and d["aplicada"] == "t" and d["new_value"]:
        aplicadas[d["hoja_id"]].append(
            (D(d["dia"]), F(d["old_value"]), F(d["new_value"]), d["motivo"])
        )
for l in aplicadas.values():
    l.sort()


def suma(h, a, b):
    """[impresiones, clicks, costo, orders, venta] de la hoja entre a y b inclusive."""
    t = [0, 0, 0.0, 0, 0.0]
    s = serie.get(h)
    if not s:
        return t
    for f, x in s.items():
        if a <= f <= b:
            for j in range(5):
                t[j] += x[j]
    return t


def suma_hojas(hs, a, b):
    t = [0, 0, 0.0, 0, 0.0]
    for h in hs:
        x = suma(h, a, b)
        for j in range(5):
            t[j] += x[j]
    return t


def q(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))] if xs else float("nan")
