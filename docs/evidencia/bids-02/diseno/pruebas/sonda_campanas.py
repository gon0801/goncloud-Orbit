# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""Sonda de SOLO LECTURA: configuracion real de las campanas (presupuesto, estrategia, placements).
Usa el cliente read-only de Orbit (default-deny de mutaciones). No escribe en Amazon ni en la base."""

import collections, json, sys
from app.ads.client import AdsClient, AdsCredentials
from app.ads.structure_api import perfiles_aceptados, listar_todo

c = AdsClient(AdsCredentials.from_secrets_dir())
salida = {}
for p in perfiles_aceptados(c):
    camps = listar_todo(c, "/sp/campaigns/list", profile_id=p.profile_id)
    claves = collections.Counter(k for x in camps for k in x)
    db_claves = collections.Counter(k for x in camps for k in (x.get("dynamicBidding") or {}))
    estr = collections.Counter(
        ((x.get("dynamicBidding") or {}).get("strategy"), x.get("state")) for x in camps
    )
    filas = []
    for x in camps:
        if x.get("state") != "ENABLED":
            continue
        db = x.get("dynamicBidding") or {}
        filas.append(
            {
                "campaignId": str(x.get("campaignId")),
                "nombre": x.get("name"),
                "tipo": x.get("targetingType"),
                "presupuesto": x.get("budget"),
                "estrategia": db.get("strategy"),
                "placements": db.get("placementBidding"),
                "otros_db": {
                    k: v for k, v in db.items() if k not in ("strategy", "placementBidding")
                },
            }
        )
    salida[p.platform] = {
        "moneda": p.moneda,
        "campanas": len(camps),
        "claves_campana": dict(claves),
        "claves_dynamicBidding": dict(db_claves),
        "estrategia_x_estado": {f"{k[0]}|{k[1]}": v for k, v in estr.items()},
        "activas": filas,
    }
json.dump(salida, sys.stdout, ensure_ascii=False, default=str)
