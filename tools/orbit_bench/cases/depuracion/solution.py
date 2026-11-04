import json
import sys
from decimal import Decimal, InvalidOperation

ACTUAL = {
    "amazon_us": (100, Decimal("40.0000"), "USD"),
    "amazon_mx": (100, Decimal("500.0000"), "MXN"),
}


def decidir(data: dict) -> dict:
    try:
        # BUG: tambien usa la politica actual durante un replay historico.
        min_clicks, min_cost, currency = ACTUAL[data["platform"]]
        clicks = int(data["clicks"])
        orders = int(data["orders"])
        cost = Decimal(data["cost"])
    except (KeyError, ValueError, InvalidOperation):
        return {"action": "none", "reason": "dato_faltante"}
    if data.get("currency") != currency:
        return {"action": "none", "reason": "moneda_invalida"}
    if orders == 0 and clicks >= min_clicks and cost >= min_cost:
        return {"action": "pause", "reason": "corte"}
    return {"action": "none", "reason": "evidencia_insuficiente"}


json.dump(decidir(json.load(sys.stdin)), sys.stdout)
