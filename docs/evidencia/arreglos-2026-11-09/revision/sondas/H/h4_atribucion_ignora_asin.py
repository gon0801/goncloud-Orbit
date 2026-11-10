"""Sonda H4: el atribuidor de Amazon decide la publicacion por producto +
canal de la orden y no mira el ASIN de la venta (que viaja en ev.payload y
ya identifica la publicacion: listing UNIQUE(platform, external_id)).

Escenario: producto 10 con dos publicaciones; la A (ASIN-A) conservo su
seller_sku FBM (la ingesta de listings guarda el primero si un ASIN tiene dos
SKU); la B (ASIN-B) es FBA. Una venta FBA del ASIN-A se atribuye a B.
"""

from types import SimpleNamespace

from app.ledger_atribucion import AtribuidorAmazon


class DbFalsa:
    def execute(self, sql, params):
        if "FROM listing" in sql:
            filas = [(1, "A-FBM"), (2, "B-FBA")]
        elif "spapi_order_observation" in sql:
            filas = [("AFN",)]
        elif "spapi_inventario_observation" in sql:
            filas = [(1,)] if params[1] == "B-FBA" else []
        else:
            filas = []
        return SimpleNamespace(fetchall=lambda: filas, fetchone=lambda: filas[0] if filas else None)


venta = SimpleNamespace(
    kind="sale",
    platform="amazon_mx",
    product_id=10,
    order_id="701-1",
    payload={"ASIN": "ASIN-A"},  # la venta es de la publicacion 1
)
print("listing atribuido:", AtribuidorAmazon(conn=DbFalsa()).atribuir(venta))
