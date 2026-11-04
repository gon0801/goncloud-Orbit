# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""A2. Simulacion del mes con la politica nueva y SU propia historia: cada dia de ciclo (2026-09-02..10-09)
decide toda hoja activa hoy que no este inerte (sin impresiones en 14 dias), y cada cambio decidido se da
por aplicado ese dia. Las metricas son las reales (las que dejo el motor viejo): despues del primer cambio
distinto el mundo real ya no es el simulado; se declara como limite.
Uso: python3 a2_simulacion.py [target_us] [variante]"""

import collections
import sys

import politica as P
from carga import *

if len(sys.argv) > 1:
    P.TARGET["amazon_us"] = float(sys.argv[1])
variante = sys.argv[2] if len(sys.argv) > 2 else "completa"
INI, FIN = dt.date(2026, 9, 2), dt.date(2026, 10, 9)
bid = {}
for h in activa:
    if h in aplicadas:
        bid[h] = aplicadas[h][0][1]
    elif hojas[h]["bid_actual"]:
        bid[h] = F(hojas[h]["bid_actual"])
hist = collections.defaultdict(list)
cuenta = collections.defaultdict(collections.Counter)
tocadas = collections.defaultdict(lambda: collections.defaultdict(set))
bitacora = collections.defaultdict(list)
dia = INI
while dia <= FIN:
    for h in sorted(bid):
        plat = hojas[h]["platform"]
        if suma(h, dia - td(days=15), dia - td(days=2))[0] == 0:
            cuenta[plat]["(inerte, no se decide)"] += 1
            continue
        caso = P.arma_caso(h, dia, bid[h], historia=hist[h])
        accion, factor, motivo = P.decide(caso, variante)
        cuenta[plat][f"{accion}:{motivo}"] += 1
        if accion == "mantener":
            continue
        viejo = bid[h]
        nuevo = hist[h][-1][1] if accion == "regresar" else viejo * (1 + factor)
        origen = "regreso_por_desplome" if accion == "regresar" else "motor"
        hist[h].append((dia, viejo, nuevo, origen))
        bid[h] = nuevo
        tocadas[plat][accion].add(h)
        bitacora[h].append((dia, accion, motivo, round(viejo, 3), round(nuevo, 3)))
    dia += td(days=1)
G30 = lambda h: suma(h, dt.date(2026, 9, 6), dt.date(2026, 10, 5))[2]
for plat in ("amazon_mx", "amazon_us"):
    hs = [h for h in bid if hojas[h]["platform"] == plat]
    total = sum(G30(h) for h in hs)
    print(
        f"\n== {plat} | target {P.TARGET[plat]} | variante {variante} | hojas activas con bid {len(hs)} | gasto 30d {total:.0f}"
    )
    for acc in ("recortar", "subir", "regresar"):
        s = tocadas[plat][acc]
        n = sum(v for k, v in cuenta[plat].items() if k.startswith(acc))
        print(
            f"   {acc:9}: {n:4} decisiones en {len(s):3} hojas, que llevan {100 * sum(G30(h) for h in s) / total:5.1f}% del gasto de 30 dias"
        )
    real_c = {h for h in hs if any(n < o for (_, o, n, _) in aplicadas.get(h, []))}
    real_n = sum(1 for h in hs for (_, o, n, _) in aplicadas.get(h, []) if n < o)
    print(
        f"   (real, motor viejo: {real_n} recortes aplicados en {len(real_c)} de estas hojas, {100 * sum(G30(h) for h in real_c) / total:.1f}% del gasto)"
    )
    for k, v in sorted(cuenta[plat].items(), key=lambda kv: -kv[1]):
        print(f"      {v:6}  {k}")
if __name__ == "__main__":
    for h in ("2963", "4924", "3861", "2871", "5888", "4919"):
        print(
            f"\n-- hoja {h} ({hojas[h]['platform']}), simulada desde el 2026-09-02:",
            bitacora.get(h) or "ningun cambio",
        )
