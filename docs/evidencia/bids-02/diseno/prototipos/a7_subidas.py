# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""A7. Trae trafico una subida de +15 %? Razon de impresiones por dia (7 dias despues entre 7 antes) tras las
subidas aplicadas, contra la variacion natural de agosto (sin motor), en hojas con volumen. Y holgura (CPC/bid)
de las hojas que el motor viejo subio."""

import statistics as st

from carga import *


def razon(h, d):
    a, b = suma(h, d - td(days=7), d - td(days=1)), suma(h, d + td(days=1), d + td(days=7))
    return (b[0] / a[0], a) if a[0] >= 1000 else (None, a)


base = [
    r
    for h in activa
    for d in (
        dt.date(2026, 8, 9),
        dt.date(2026, 8, 13),
        dt.date(2026, 8, 17),
        dt.date(2026, 8, 21),
        dt.date(2026, 8, 25),
    )
    for r in [razon(h, d)[0]]
    if r is not None
]
for plat in ("amazon_mx", "amazon_us"):
    sub, holg = [], []
    for h, l in aplicadas.items():
        if h not in hojas or hojas[h]["platform"] != plat:
            continue
        for d, old, new, _ in l:
            if new > old and d + td(days=8) <= ULTIMO:
                r, a = razon(h, d)
                if r is not None:
                    sub.append(r)
                    if a[1] > 0:
                        holg.append((a[2] / a[1]) / old)
    f = lambda xs, u: (
        f"{sum(1 for x in xs if x >= u)}/{len(xs)} ({100 * sum(1 for x in xs if x >= u) / max(len(xs), 1):.0f}%)"
    )
    if sub:
        print(
            f"{plat}: subidas aplicadas con volumen n={len(sub)} | razon mediana {st.median(sub):.2f} | >=1.10: {f(sub, 1.1)} | holgura mediana antes de subir {st.median(holg):.2f}"
        )
print(
    f"agosto sin motor: n={len(base)} | razon mediana {st.median(base):.2f} | >=1.10: {sum(1 for x in base if x >= 1.1)}/{len(base)} ({100 * sum(1 for x in base if x >= 1.1) / len(base):.0f}%)"
)
