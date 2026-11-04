# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""A1. Rejuego a un paso: cada recorte decidido en vivo (2026-09-02..10-09) se vuelve a decidir con
`niveles_v3` sobre el caso reconstruido para ese dia (historia real de bids aplicados hasta ese dia).
Uso: python3 a1_rejuego.py [target_us] [variante]"""

import collections
import sys

import politica as P
from carga import *

if len(sys.argv) > 1:
    P.TARGET["amazon_us"] = float(sys.argv[1])
variante = sys.argv[2] if len(sys.argv) > 2 else "completa"
res = collections.defaultdict(collections.Counter)
gasto = collections.defaultdict(collections.Counter)
vistos = collections.defaultdict(set)
for d in decisiones:
    if d["kind"] != "bid" or d["modo_ciclo"] != "live" or not d["new_value"]:
        continue
    old, new = F(d["old_value"]), F(d["new_value"])
    plat = d["platform"]
    if d["hoja_id"] not in hojas:
        continue
    caso = P.arma_caso(d["hoja_id"], D(d["dia"]), old)
    accion, factor, motivo = P.decide(caso, variante)
    sentido = "recorte" if new < old else "subida"
    res[(plat, sentido)][f"{accion}:{motivo}"] += 1
    res[(plat, sentido)]["_total"] += 1
    gasto[(plat, sentido)][f"{accion}:{motivo}"] += F(d["v_costo"])
    if accion == "recortar":
        vistos[plat].add(d["hoja_id"])
for (plat, sentido), c in sorted(res.items()):
    tot = c.pop("_total")
    print(
        f"\n== {plat} | {sentido}s decididos en vivo: {tot} | target {P.TARGET[plat]} | variante {variante}"
    )
    quedan = sum(v for k, v in c.items() if k.startswith("recortar"))
    for k, v in sorted(c.items(), key=lambda kv: -kv[1]):
        print(f"   {v:4}  {k:45} gasto 30d de esas decisiones {gasto[(plat, sentido)][k]:10.2f}")
    if sentido == "recorte":
        print(
            f"   => recortes que la politica nueva tambien haria: {quedan} de {tot} ({100 * quedan / tot:.0f}%), en {len(vistos[plat])} hojas"
        )
