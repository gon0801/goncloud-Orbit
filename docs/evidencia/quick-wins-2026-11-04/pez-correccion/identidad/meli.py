"""Identidad PEZ por atributo de variante; GET sin refresh ni persistencia."""

import json
from datetime import UTC, datetime

from app.redaction import scrub
from app.reputacion_clientes import ClienteMeli, MeliCredentials

PARES = [
    ("MLM2163407464", "194936687129"),
    ("MLM1890653227", "194936530433"),
    ("MLM4735255462", "190065698608"),
    ("MLM2724304859", "190063357552"),
    ("MLM2727437671", "196650442795"),
    ("MLM2727363007", "196650403797"),
    ("MLM2727328203", "196650403671"),
    ("MLM4749600210", "196764932943"),
    ("MLM4763716652", "190333223922"),
]
cliente = ClienteMeli(MeliCredentials.from_secrets_dir(), max_intentos=1, timeout=15)
filas = []
try:
    for item, variante in PARES:
        try:
            dato, observado = cliente.get(f"/items/{item}", {"include_attributes": "all"})
            v = next(v for v in dato.get("variations", []) if str(v["id"]) == variante)
            filas.append(
                {
                    "item_id": item,
                    "variante_ext": variante,
                    "title": dato.get("title"),
                    "atributos_item": dato.get("attributes", []),
                    "pictures": dato.get("pictures", []),
                    "picture_ids_variante": v.get("picture_ids", []),
                    "descripcion": cliente.get(f"/items/{item}/description")[0].get("plain_text"),
                    "status": dato.get("status"),
                    "observado_utc": observado.isoformat(),
                    "stock_variante": v.get("available_quantity"),
                    "seller_custom_field": v.get("seller_custom_field"),
                    "atributos": [
                        {k: a.get(k) for k in ["id", "name", "value_id", "value_name"]}
                        for a in v.get("attributes", [])
                    ],
                    "combinaciones": [
                        {k: a.get(k) for k in ["id", "name", "value_id", "value_name"]}
                        for a in v.get("attribute_combinations", [])
                    ],
                }
            )
        except Exception as exc:
            filas.append({"item_id": item, "error": scrub(str(exc))})
finally:
    cliente.close()
print(
    json.dumps(
        {"fecha_utc": datetime.now(UTC).isoformat(), "filas": filas}, ensure_ascii=False, indent=2
    )
)
