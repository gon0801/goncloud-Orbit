# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""A4. Numeros que sostienen cuatro decisiones del diseno.
(b) holgura CPC/bid: donde cae la frontera entre "el CPC sigue al bid" y "el CPC no sigue al bid".
(c) que sustituye a los 20 clics al bid vigente: error de tres candidatos contra el CPC real tras el recorte.
(e) guarda de desplome con 4 dias posteriores y filtro "vendia": disparos tras recorte contra agosto sin motor.
(a) donde actua la regla de grupo: gasto de las hojas sin evidencia propia en grupos que sangran."""

import collections
import statistics as st

import politica as P
from carga import *

print(
    "(b) holgura = CPC pagado / bid, hojas activas con >= 20 clics desde su ultimo cambio (o 2026-09-06)"
)
for plat in ("amazon_mx", "amazon_us"):
    r = []
    for h in activa:
        hh = hojas[h]
        if hh["platform"] != plat or not hh["bid_actual"]:
            continue
        ini = (
            max(aplicadas[h][-1][0] + td(days=1), dt.date(2026, 9, 6))
            if aplicadas.get(h)
            else dt.date(2026, 9, 6)
        )
        s = suma(h, ini, dt.date(2026, 10, 6))
        if s[1] >= 20:
            r.append((s[2] / s[1]) / F(hh["bid_actual"]))
    cortes = [0.3, 0.5, 0.75, 0.9, 1.1]
    hist = [sum(1 for x in r if a <= x < b) for a, b in zip([0] + cortes, cortes + [99])]
    print(
        f"   {plat}: n={len(r)} mediana {st.median(r):.2f} | <0.3:{hist[0]} 0.3-0.5:{hist[1]} 0.5-0.75:{hist[2]} 0.75-0.9:{hist[3]} 0.9-1.1:{hist[4]} >=1.1:{hist[5]}"
    )

print(
    "\n(c) CPC tras un recorte aplicado (>= 15 clics antes y despues, 14 dias por lado): error relativo de cada sustituto"
)
err = collections.defaultdict(list)
por_regimen = collections.defaultdict(list)
for h, l in aplicadas.items():
    if h not in hojas:
        continue
    for i, (d, old, new, _) in enumerate(l):
        if new >= old:
            continue
        ini = max(d - td(days=14), l[i - 1][0] + td(days=1)) if i > 0 else d - td(days=14)
        fin = min(d + td(days=14), l[i + 1][0] - td(days=1)) if i + 1 < len(l) else d + td(days=14)
        a, b = suma(h, ini, d - td(days=1)), suma(h, d + td(days=1), fin)
        if a[1] < 15 or b[1] < 15:
            continue
        real, pre = b[2] / b[1], a[2] / a[1]
        holg = pre / old
        proy = (
            pre * new / old if holg >= P.HOLGURA_PEGADO else min(pre, new)
        )  # variante de dos regimenes (descartada)
        g = por_grupo[(hojas[h]["platform"], hojas[h]["ad_group_id"])]
        gs = suma_hojas([x for x in g if x != h], d + td(days=1), fin)
        err["ventana entera (CPC de antes, sin tocar)"].append(pre / real - 1)
        err["escalado 1 a 1 por el cambio de bid (elegido)"].append(pre * new / old / real - 1)
        err["dos regimenes (descartado)"].append(proy / real - 1)
        if gs[1] >= 15:
            err["CPC del resto del ad group"].append(gs[2] / gs[1] / real - 1)
        por_regimen["pegado" if holg >= P.HOLGURA_PEGADO else "holgado"].append(
            (real / pre, new / old)
        )
for k, v in err.items():
    ab = sorted(abs(x) for x in v)
    print(
        f"   {k:42} n={len(v):3} sesgo mediano {st.median(v):+.2f} | error absoluto mediano {st.median(ab):.2f} | p90 {q(ab, 0.9):.2f}"
    )
for k, v in por_regimen.items():
    print(
        f"   regimen {k}: n={len(v)} bid x{st.median(x[1] for x in v):.2f} -> CPC x{st.median(x[0] for x in v):.2f}"
    )

print("\n(e) guarda de desplome: razon de impresiones por dia, 4 dias posteriores contra 7 previos")


def razon(h, d, dias=4):
    a, b = suma(h, d - td(days=7), d - td(days=1)), suma(h, d + td(days=1), d + td(days=dias))
    vend = suma(h, d - td(days=90), d - td(days=1))[3] >= 1
    return a, b, vend, ((b[0] / dias) / (a[0] / 7) if a[0] else None)


for dias in (4, 7):
    base, cut = [], []
    for h in activa:
        for d in (
            dt.date(2026, 8, 9),
            dt.date(2026, 8, 13),
            dt.date(2026, 8, 17),
            dt.date(2026, 8, 21),
            dt.date(2026, 8, 25),
        ):
            a, b, vend, rz = razon(h, d, dias)
            if vend and (a[0] >= 1000 or a[1] >= 20) and rz is not None:
                base.append(rz)
    for h, l in aplicadas.items():
        if h not in activa:
            continue
        for d, old, new, _ in l:
            if new < old and d + td(days=dias + 1) <= ULTIMO:
                a, b, vend, rz = razon(h, d, dias)
                if vend and (a[0] >= 1000 or a[1] >= 20) and rz is not None:
                    cut.append((rz, new / old))
    f = lambda xs, u: (
        f"{sum(1 for x in xs if x < u)}/{len(xs)} ({100 * sum(1 for x in xs if x < u) / max(len(xs), 1):.0f}%)"
    )
    c = [x[0] for x in cut]
    print(
        f"   {dias} dias post | hojas que vendian, con volumen | agosto sin motor: <0.30 {f(base, 0.3)} ; <0.70 {f(base, 0.7)} | tras recorte: <0.30 {f(c, 0.3)} ; <0.70 {f(c, 0.7)}"
    )
    for nombre, sel in (
        ("-12%", [x[0] for x in cut if x[1] > 0.86]),
        ("-25%", [x[0] for x in cut if x[1] <= 0.86]),
    ):
        print(
            f"       paso {nombre}: n={len(sel)} mediana {st.median(sel):.2f} | <0.30 {f(sel, 0.3)} | >=0.70 {len(sel) - sum(1 for x in sel if x < 0.7)}"
        )

print(
    "\n(a) regla de grupo: hojas sin pedidos y con gasto menor al umbral, en grupos que sangran (dia 2026-10-09)"
)
dia = dt.date(2026, 10, 9)
for plat in ("amazon_mx", "amazon_us"):
    t, u = P.TARGET[plat], P.GASTO_PARA_CONCLUIR[plat]
    tot_sangra = n_sin = g_sin = g_con = 0
    tramos_gasto = collections.Counter()
    for (pl, ag), hs in por_grupo.items():
        if pl != plat:
            continue
        g = P.grupo(pl, ag, dia)
        if P.estado_nivel(g, t, u) != "sangra":
            continue
        for h in hs:
            if h not in activa:
                continue
            p = P.tramos([h], dia)
            if p["costo_crudo"] <= 0:
                continue
            tot_sangra += p["costo_crudo"]
            if p["pedidos_crudo"] == 0 and p["costo_crudo"] < u:
                n_sin += 1
                g_sin += p["costo_crudo"]
                fr = p["costo_crudo"] / u
                tramos_gasto[
                    "<10% del umbral"
                    if fr < 0.1
                    else "10-25%"
                    if fr < 0.25
                    else "25-50%"
                    if fr < 0.5
                    else "50-100%"
                ] += 1
                tramos_gasto[
                    "$"
                    + (
                        "<10% del umbral"
                        if fr < 0.1
                        else "10-25%"
                        if fr < 0.25
                        else "25-50%"
                        if fr < 0.5
                        else "50-100%"
                    )
                ] += p["costo_crudo"]
            else:
                g_con += p["costo_crudo"]
    print(
        f"   {plat}: gasto maduro 90d de hojas activas en grupos que sangran {tot_sangra:.0f}; con evidencia propia {g_con:.0f} ({100 * g_con / max(tot_sangra, 1):.0f}%); sin evidencia propia {g_sin:.0f} en {n_sin} hojas"
    )
    for k in ("<10% del umbral", "10-25%", "25-50%", "50-100%"):
        print(
            f"        gasto de la hoja {k:16}: {tramos_gasto[k]:3} hojas, {tramos_gasto['$' + k]:8.2f}"
        )
