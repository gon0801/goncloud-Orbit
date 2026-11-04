# ruff: noqa  (evidencia congelada del diseno de BIDS 02: se conserva como se corrio)
"""Sonda de SOLO LECTURA: que variante del reporte por placement acepta Amazon, con el detalle del rechazo.
Solo llama a POST /reporting/reports (crear un reporte: una lectura, no cambia la cuenta)."""

import datetime as dt, json, sys
from app.ads.client import AdsClient, AdsCredentials, REPORT_REQUEST_PATH
from app.ads.structure_api import perfiles_aceptados

c = AdsClient(AdsCredentials.from_secrets_dir())
hoy = dt.datetime.now(dt.timezone.utc).date()
ini, fin = hoy - dt.timedelta(days=33), hoy - dt.timedelta(days=3)
BASE = ["campaignId", "placementClassification", "impressions", "clicks", "cost"]
VARIANTES = {
    "V1_DAILY_minimo": ("DAILY", ["date", *BASE]),
    "V2_SUMMARY_minimo": ("SUMMARY", ["startDate", "endDate", *BASE]),
    "V3_DAILY_ventas": ("DAILY", ["date", *BASE, "purchases30d", "sales30d"]),
    "V4_DAILY_nombre_estado": ("DAILY", ["date", *BASE, "campaignName", "campaignStatus"]),
    "V5_DAILY_presupuesto": (
        "DAILY",
        ["date", *BASE, "campaignBudgetAmount", "campaignBudgetCurrencyCode"],
    ),
    "V6_DAILY_tosis": ("DAILY", ["date", *BASE, "topOfSearchImpressionShare"]),
}
perfil = [p for p in perfiles_aceptados(c) if p.platform == "amazon_us"][0]
salida = {}
for nombre, (unidad, columnas) in VARIANTES.items():
    body = {
        "name": f"orbit-sonda-{nombre}-{ini}_{fin}",
        "startDate": ini.isoformat(),
        "endDate": fin.isoformat(),
        "configuration": {
            "adProduct": "SPONSORED_PRODUCTS",
            "groupBy": ["campaign", "campaignPlacement"],
            "columns": columnas,
            "reportTypeId": "spCampaigns",
            "timeUnit": unidad,
            "format": "GZIP_JSON",
        },
    }
    token = c._ensure_token()
    headers = c._build_headers(token, perfil.profile_id, path=REPORT_REQUEST_PATH, method="POST")
    resp = c._client.request(
        "POST", f"{c._base_url}{REPORT_REQUEST_PATH}", headers=headers, json=body
    )
    try:
        cuerpo = resp.json()
    except ValueError:
        cuerpo = {"texto": resp.text[:300]}
    salida[nombre] = {
        "status": resp.status_code,
        "reportId": cuerpo.get("reportId") if isinstance(cuerpo, dict) else None,
        "detalle": (cuerpo.get("detail") or cuerpo.get("message") or cuerpo.get("texto"))
        if isinstance(cuerpo, dict)
        else None,
    }
json.dump(salida, sys.stdout, ensure_ascii=False, indent=1)
