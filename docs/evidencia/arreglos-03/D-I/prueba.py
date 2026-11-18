# ARREGLOS 03 D-I.4: prueba-real del bloque, para docs/evidencia/arreglos-01/en_prod.sh.
# Corre en el contenedor de produccion con el codigo desplegado: solo funciones puras,
# sin base ni red. Antes del despliegue falla con
# ImportError: cannot import name '_original_de'.
# Uso: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-03/D-I/prueba.py
import datetime as dt
from decimal import Decimal

from app.ledger import _original_de

dia = dt.date(2026, 11, 12)


def usd(monto):
    return {"CurrencyCode": "USD", "Amount": monto}


venta = {"ItemPrice": usd("120.00"), "ItemTax": usd("9.60"), "ShippingPrice": usd("5.00"),
         "ShippingTax": usd("0.40"), "PromotionDiscount": usd("10.00"), "fx_source": "exact"}
assert _original_de(venta, "sale", "MXN", dia) == (Decimal("125.0000"), "USD", dia), "venta exacta"
assert _original_de({**venta, "fx_source": "nearest_prior"}, "sale", "MXN", dia) == (Decimal("125.0000"), "USD", None), "tasa de otro dia"
assert _original_de({"orig_currency": "USD", "fx_source": "exact"}, "fee", "MXN", dia) == (None, "USD", dia), "cargo sin original"
print("D-I desplegado")
