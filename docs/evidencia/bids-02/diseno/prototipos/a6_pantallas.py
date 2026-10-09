# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""A6. Lo que mostrarian dos pantallas con los datos de hoy, y los numeros del veredicto del impulso.
(1) Keywords danadas: hoja activa que vendia (>= 1 pedido en los 90 dias previos a su racha de recortes),
    con bid de hoy menor al de antes de la racha, y cuyos clics de los ultimos 14 dias legibles son menos de
    30 % de los 14 dias previos a la racha.
(2) CTR y CPC por plataforma (hojas activas, 2026-09-06..10-05): de ahi salen los pisos del veredicto."""

from carga import *

HOY = ULTIMO
print("(1) Keywords danadas")
for plat in ("amazon_mx", "amazon_us"):
    filas = []
    for h in activa:
        if hojas[h]["platform"] != plat or not aplicadas.get(h):
            continue
        l = aplicadas[h]
        # racha de recortes vigente: desde el ultimo cambio que no fue recorte
        i = len(l)
        while i > 0 and l[i - 1][2] < l[i - 1][1]:
            i -= 1
        racha = l[i:]
        if not racha:
            continue
        inicio, bid_antes, bid_hoy = racha[0][0], racha[0][1], racha[-1][2]
        antes = suma(h, inicio - td(days=14), inicio - td(days=1))
        ahora = suma(h, HOY - td(days=15), HOY - td(days=2))
        vendia = suma(h, inicio - td(days=90), inicio - td(days=1))
        if vendia[3] >= 1 and antes[1] > 0 and ahora[1] < 0.3 * antes[1]:
            filas.append(
                (vendia[4], h, len(racha), bid_antes, bid_hoy, antes[1], ahora[1], vendia[3])
            )
    filas.sort(reverse=True)
    print(
        f"   {plat}: {len(filas)} hojas; venta de 90 dias antes de la racha: {sum(f[0] for f in filas):.0f}"
    )
    for f in filas[:8]:
        print(
            f"      hoja {f[1]:5} {hojas[f[1]]['match_type'] or hojas[f[1]]['kind']:14} {f[2]} recortes, bid {f[3]:.2f} -> {f[4]:.2f}, clics 14 d {f[5]} -> {f[6]}, vendia {f[7]} pedidos / {f[0]:.0f}"
        )

print("\n(2) CTR, CPC y clics que compra el tope de aprendizaje")
for plat, tope in (("amazon_mx", 350.0), ("amazon_us", 36.0)):
    hs = [h for h in activa if hojas[h]["platform"] == plat]
    s = suma_hojas(hs, dt.date(2026, 9, 6), dt.date(2026, 10, 5))
    ctr, cpc = s[1] / s[0], s[2] / s[1]
    print(
        f"   {plat}: CTR {100 * ctr:.2f}% | CPC {cpc:.2f} | el tope de {tope:.0f} compra ~{tope / cpc:.0f} clics, que piden ~{tope / cpc / ctr:,.0f} impresiones | 20 clics piden ~{20 / ctr:,.0f} impresiones"
    )
