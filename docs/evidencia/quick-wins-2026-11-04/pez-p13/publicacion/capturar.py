"""Snapshot MeLi para publicar PEZ22: solo GET, sin renovar credenciales."""

import json
from datetime import UTC, datetime

from app.reputacion_clientes import ClienteMeli, MeliCredentials

ITEMS = [
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
cliente = ClienteMeli(MeliCredentials.from_secrets_dir(), max_intentos=1, timeout=15)
filas = []
try:
    usuario, _ = cliente.get("/users/me")
    for item_id in ITEMS:
        item, fecha_item = cliente.get(f"/items/{item_id}", {"include_attributes": "all"})
        desc, fecha_desc = cliente.get(f"/items/{item_id}/description")
        assert item["id"] == item_id and item["seller_id"] == usuario["id"]
        filas.append(
            {
                "item": item,
                "descripcion": desc,
                "observado_item_utc": fecha_item.isoformat(),
                "observado_descripcion_utc": fecha_desc.isoformat(),
            }
        )
finally:
    cliente.close()
print(
    json.dumps(
        {
            "observado_utc": datetime.now(UTC).isoformat(),
            "seller_id": usuario["id"],
            "filas": filas,
        },
        ensure_ascii=False,
        indent=2,
    )
)
