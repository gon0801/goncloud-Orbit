"""GET vivo con pase existente; cliente no refresca ni persiste tokens."""

import json
from datetime import UTC, datetime

from app.redaction import scrub
from app.reputacion_clientes import ClienteMeli, MeliCredentials

IDS = [
    "MLM2163407464",
    "MLM1890653227",
    "MLM4735255462",
    "MLM2724304859",
    "MLM2727437671",
    "MLM2727363007",
    "MLM2727328203",
    "MLM4749600210",
    "MLM4763716652",
]
client = ClienteMeli(MeliCredentials.from_secrets_dir(), max_intentos=1, timeout=15)
filas = []
try:
    for item in IDS:
        try:
            data, observado = client.get(f"/items/{item}")
            r = {
                k: data.get(k)
                for k in [
                    "id",
                    "status",
                    "sub_status",
                    "available_quantity",
                    "catalog_listing",
                    "catalog_product_id",
                    "last_updated",
                    "currency_id",
                ]
            }
            r["observado_utc"] = observado.isoformat()
            r["variantes"] = [
                {
                    "id": v.get("id"),
                    "seller_sku": v.get("seller_sku"),
                    "seller_custom_field": v.get("seller_custom_field"),
                    "available_quantity": v.get("available_quantity"),
                }
                for v in data.get("variations", [])
            ]
            filas.append(r)
        except Exception as exc:
            filas.append({"id": item, "error": scrub(str(exc))})
finally:
    client.close()
print(
    json.dumps(
        {"fecha_utc": datetime.now(UTC).isoformat(), "filas": filas}, ensure_ascii=False, indent=2
    )
)
