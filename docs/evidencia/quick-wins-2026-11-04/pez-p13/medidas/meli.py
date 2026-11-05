"""P13: GET de medidas y todas las variantes; sin refresh ni escritura remota."""

import json
from datetime import UTC, datetime

from app.reputacion_clientes import ClienteMeli, MeliCredentials

PARES = [
    (1562, "MLM2163407464", "194936687129"),
    (1563, "MLM1890653227", "194936530433"),
    (1578, "MLM4735255462", "190065698608"),
    (1579, "MLM2724304859", "190063357552"),
    (1582, "MLM2727437671", "196650442795"),
    (1583, "MLM2727363007", "196650403797"),
    (1584, "MLM2727328203", "196650403671"),
    (1585, "MLM4749600210", "196764932943"),
    (1590, "MLM4763716652", "190333223922"),
]
cliente = ClienteMeli(MeliCredentials.from_secrets_dir(), max_intentos=1, timeout=15)
filas = []
try:
    for listing, item, variante in PARES:
        dato, observado = cliente.get(f"/items/{item}", {"include_attributes": "all"})
        desc, desc_observado = cliente.get(f"/items/{item}/description")
        filas.append(
            {
                "listing_id": listing,
                "item_id": item,
                "variante_ext": variante,
                "title": dato.get("title"),
                "status": dato.get("status"),
                "last_updated": dato.get("last_updated"),
                "atributos_item": dato.get("attributes", []),
                "shipping_dimensions": (dato.get("shipping") or {}).get("dimensions"),
                "descripcion": desc.get("plain_text"),
                "pictures": dato.get("pictures", []),
                "variantes": [
                    {
                        k: v.get(k)
                        for k in ["id", "attributes", "attribute_combinations", "picture_ids"]
                    }
                    for v in dato.get("variations", [])
                ],
                "observado_utc": observado.isoformat(),
                "descripcion_observada_utc": desc_observado.isoformat(),
            }
        )
finally:
    cliente.close()
print(
    json.dumps(
        {"fecha_utc": datetime.now(UTC).isoformat(), "filas": filas}, ensure_ascii=False, indent=2
    )
)
