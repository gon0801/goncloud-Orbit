# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""Prototipo P7 (seccion 7): que mostraria la pantalla "Busquedas que gastan sin vender".
Datos: datos/terminos.csv (search_term_observation, ultima observacion por fecha, ~100 dias, extraido el
2026-10-09 en solo lectura). Solo campanas activas. Biblioteca estandar."""

import collections
import csv
import re

D = {"amazon_mx": 350.0, "amazon_us": 36.0}  # gasto para concluir
VACIAS = set(
    "de la el los las para y con en un una por del a al que the of for and to in on with".split()
)
F = lambda x: float(x or 0)
N = lambda x: int(float(x or 0))

filas = [r for r in csv.DictReader(open("datos/terminos.csv")) if r["estado_campana"] == "ENABLED"]
for plat in ("amazon_mx", "amazon_us"):
    R = [r for r in filas if r["platform"] == plat]
    d = D[plat]
    # --- referencia de azar, por termino dentro de su ad group
    t = collections.defaultdict(lambda: [0.0, 0, 0])
    for r in R:
        x = t[(r["ad_group_id"], r["search_term"])]
        x[0] += F(r["costo"])
        x[1] += N(r["clicks"])
        x[2] += N(r["orders"])
    gasto = sum(v[0] for v in t.values())
    clics = sum(v[1] for v in t.values())
    ped = sum(v[2] for v in t.values())
    p = ped / clics
    visto = sum(v[0] for v in t.values() if v[2] == 0) / gasto
    azar = sum(v[0] * (1 - p) ** v[1] for v in t.values()) / gasto
    print(f"\n== {plat}: gasto {gasto:,.0f}, pedidos {ped}, conversion {100 * p:.2f}%")
    print(
        f"   gasto en busquedas sin pedido: {100 * visto:.0f}% | lo que daria el azar: {100 * azar:.0f}%"
    )
    # --- ASIN ajenos, sumados en toda la cuenta
    a = collections.defaultdict(lambda: [0.0, 0, 0, set()])
    for r in R:
        if r["is_asin_like"] == "t":
            x = a[r["search_term"].upper()]
            x[0] += F(r["costo"])
            x[1] += N(r["clicks"])
            x[2] += N(r["orders"])
            x[3].add(r["campana"])
    cand = sorted(
        ((v[0], k, v[1], len(v[3])) for k, v in a.items() if v[2] == 0 and v[0] >= d), reverse=True
    )
    casi = [1 for v in a.values() if v[2] == 0 and d / 2 <= v[0] < d]
    print(
        f"   ASIN que pasaron el gasto para concluir sin un pedido: {len(cand)} | a medio camino: {len(casi)}"
    )
    for c in cand:
        print(f"        {c[0]:8.2f}  {c[2]:4} clics  {c[3]} campanas  {c[1]}")
    # --- palabras de una y de dos, sumadas en toda la cuenta
    for n in (1, 2):
        w = collections.defaultdict(lambda: [0.0, 0, 0, set()])
        for r in R:
            if r["is_asin_like"] == "t":
                continue
            toks = re.findall(r"[a-záéíóúñü0-9]+", r["search_term"].lower())
            if n == 1:
                toks = [x for x in toks if x not in VACIAS and len(x) > 2]
            for gr in {" ".join(toks[i : i + n]) for i in range(len(toks) - n + 1)}:
                x = w[gr]
                x[0] += F(r["costo"])
                x[1] += N(r["clicks"])
                x[2] += N(r["orders"])
                x[3].add(r["search_term"])
        cand = sorted(
            ((v[0], k, v[1], len(v[3])) for k, v in w.items() if v[2] == 0 and v[0] >= d),
            reverse=True,
        )
        print(f"   palabras de {n} que pasaron el gasto para concluir sin un pedido: {len(cand)}")
        for c in cand:
            print(f"        {c[0]:8.2f}  {c[2]:4} clics  {c[3]:3} busquedas  «{c[1]}»")
