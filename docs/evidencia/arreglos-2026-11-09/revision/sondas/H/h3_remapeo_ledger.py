"""Sonda H3: tras corregir el mapa ASIN -> producto (re-mapeo legitimo de la
ingesta de listings), la MISMA venta del ledger calcula otro product_id, pero
la fila ya escrita no cambia: INSERT ... ON CONFLICT DO NOTHING por
source_event_id y trigger ledger_append_only. El margen por producto sigue
cargando la venta al producto viejo, con el costo del producto viejo.

Pura: solo mapear_destino + el SQL que elige app/ledger.py.
"""

import json

from app.ledger import FilaOrigenLedger, _sql_insert_para, mapear_destino

venta = FilaOrigenLedger(
    platform="amazon",
    order_id="701-0000000-0000000",
    event_type="sale_gross",
    fee_category=None,
    sku="01-5LZU-V9KZ",
    quantity=1,
    amount=899.0,
    currency="MXN",
    event_date="2026-10-01 18:00:00",
    cogs_at_sale=None,
    raw_payload=json.dumps({"ASIN": "B0EJEMPLO1", "QuantityShipped": 1}),
    dedupe_key="ship-123-item-1",
)

antes = mapear_destino(venta, listings={("amazon_mx", "B0EJEMPLO1"): 101}, productos={})
despues = mapear_destino(venta, listings={("amazon_mx", "B0EJEMPLO1"): 202}, productos={})
print("product_id en la 1a ingesta (mapa equivocado, p.ej. NH-):", antes.product_id)
print("product_id que calcularia hoy (mapa corregido, p.ej. SET-):", despues.product_id)
print("mismo source_event_id:", antes.source_event_id == despues.source_event_id)
print("SQL usado en la re-ingesta:", " ".join(_sql_insert_para(despues).split()))
