# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""Sonda de SOLO LECTURA: ¿el reporte de campanas por placement acepta datos DIARIOS?
Usa las funciones de reportes de Orbit (crear reporte, esperar, descargar). Un reporte es una lectura:
no cambia nada en la cuenta de Amazon. No escribe en la base."""

import collections, datetime as dt, json, sys
from app.ads.client import AdsClient, AdsCredentials
from app.ads.structure_api import perfiles_aceptados
from app.ads import reports

c = AdsClient(AdsCredentials.from_secrets_dir())
hoy = dt.datetime.now(dt.timezone.utc).date()
ini, fin = hoy - dt.timedelta(days=33), hoy - dt.timedelta(days=3)
cfg = {
    "nombre": "sonda-placement",
    "reportTypeId": "spCampaigns",
    "groupBy": ["campaign", "campaignPlacement"],
    "columns": [
        "date",
        "campaignId",
        "campaignName",
        "campaignStatus",
        "placementClassification",
        "impressions",
        "clicks",
        "cost",
        "purchases30d",
        "sales30d",
        "campaignBudgetAmount",
        "campaignBudgetCurrencyCode",
    ],
}
salida = {"rango": [ini.isoformat(), fin.isoformat()]}
for p in perfiles_aceptados(c):
    try:
        rid = reports.solicitar_reporte(c, p, cfg, ini, fin)
        url = reports.esperar_reporte(c, p, rid)
        filas = reports.descargar_filas(c, url)
    except Exception as exc:  # la sonda reporta el error tal cual, ya redactado por el cliente
        salida[p.platform] = {"error": f"{type(exc).__name__}: {exc}"}
        continue
    fechas = sorted({f.get("date") for f in filas if f.get("date")})
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    por_camp = collections.defaultdict(
        lambda: collections.defaultdict(lambda: collections.defaultdict(float))
    )
    for f in filas:
        pl = f.get("placementClassification")
        activo = "activa" if f.get("campaignStatus") == "ENABLED" else "no_activa"
        for k in ("impressions", "clicks", "cost", "purchases30d", "sales30d"):
            v = f.get(k) or 0
            agg[(pl, activo)][k] += float(v)
            por_camp[f.get("campaignName")][pl][k] += float(v)
    salida[p.platform] = {
        "filas": len(filas),
        "claves": sorted({k for f in filas for k in f}),
        "fechas_distintas": len(fechas),
        "primera": fechas[:1],
        "ultima": fechas[-1:],
        "ejemplo": filas[:2],
        "por_placement": {f"{k[0]}|{k[1]}": dict(v) for k, v in agg.items()},
        "por_campana": {c_: {pl: dict(m) for pl, m in d.items()} for c_, d in por_camp.items()},
    }
json.dump(salida, sys.stdout, ensure_ascii=False, default=str)
