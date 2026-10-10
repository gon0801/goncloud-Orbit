# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""P6: candidatos a impulsar. Productos con ventas por cualquier canal (ledger) y sin anuncio activo, o con
anuncio activo y casi sin clics de ads. Activo = product ad ENABLED en ad group y campana ENABLED."""

import csv, collections

I = lambda x: int(float(x or 0))
F = lambda x: float(x or 0)
B = "datos/"
hojas = list(csv.DictReader(open(B + "hojas.csv")))
ag_act = {
    (h["platform"], h["ad_group_id"])
    for h in hojas
    if h["status_ad_group"] == "ENABLED" and h["status_campana"] == "ENABLED"
}
pa = list(csv.DictReader(open(B + "product_ads.csv")))
vt = list(csv.DictReader(open(B + "ventas_totales_mes.csv")))
asem = list(csv.DictReader(open(B + "asin_semana.csv")))
for plat in ("amazon_mx", "amazon_us"):
    activos = collections.defaultdict(set)
    todos = collections.defaultdict(set)
    sin_pid = 0
    act_total = 0
    for r in pa:
        if r["platform"] != plat:
            continue
        es = r["status_product_ad"] == "ENABLED" and (plat, r["ad_group_id"]) in ag_act
        if es:
            act_total += 1
            if not r["product_id"]:
                sin_pid += 1
        if r["product_id"]:
            todos[r["product_id"]].add(r["asin"])
            if es:
                activos[r["product_id"]].add(r["asin"])
    rec = collections.defaultdict(int)
    tot = collections.defaultdict(int)
    for r in vt:
        if r["platform"] == plat:
            tot[r["product_id"]] += I(r["ordenes"])
            if "2026-07-01" <= r["mes"] <= "2026-09-01":
                rec[r["product_id"]] += I(r["ordenes"])
    clics = collections.defaultdict(int)
    pedads = collections.defaultdict(int)
    asin2pid = {a: p for p, s in todos.items() for a in s}
    for r in asem:
        if r["platform"] == plat and r["asin"] in asin2pid:
            clics[asin2pid[r["asin"]]] += I(r["clicks"])
            pedads[asin2pid[r["asin"]]] += I(r["orders"])
    prods = set(tot) | set(todos)
    sin_anuncio = [p for p in prods if p not in activos]
    a = [p for p in sin_anuncio if rec[p] >= 2]
    b = [p for p in activos if rec[p] >= 2 and clics[p] < 20]
    c = [p for p in activos if tot[p] == 0]
    print(
        f"\n== {plat}: productos con ventas o anuncios: {len(prods)} | con anuncio activo: {len(activos)} | product ads activos {act_total}, sin product_id {sin_pid}"
    )
    print(
        f"  A) sin anuncio activo y >=2 ordenes jul-sep (cualquier canal): {len(a)}  (ordenes jul-sep: {sum(rec[p] for p in a)})"
    )
    print(
        f"  B) con anuncio activo, >=2 ordenes jul-sep y <20 clics de ads desde 08-03: {len(b)} (ordenes jul-sep: {sum(rec[p] for p in b)})"
    )
    print(f"  C) con anuncio activo y 0 ordenes por cualquier canal desde 2025-11: {len(c)}")
    top = sorted(a, key=lambda p: -rec[p])[:5]
    print("  top A (product_id: ordenes jul-sep):", [(p, rec[p]) for p in top])
