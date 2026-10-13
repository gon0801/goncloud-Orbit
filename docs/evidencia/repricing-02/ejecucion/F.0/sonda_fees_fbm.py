"""REPRICING 02 · F.0 punto 2 · sonda Product Fees para una oferta FBM.

Consulta de solo lectura a SP-API Product Fees v0 con
``IsAmazonFulfilled: false`` para el SKU FBM de cada universo. No cambia
nada: es el mismo POST feesEstimate de lectura que ya corre la estimacion.

Por que no ProductFeesClient: fija ``IsAmazonFulfilled`` en verdadero en el
cuerpo (``construir_request_body``) y su validador rechaza la respuesta si
el identificador no lo trae en verdadero. Esta sonda arma el cuerpo a mano
y lo manda con ``SpapiClient.post_fees``, que es la puerta permitida para
ese POST.

Se corre DENTRO del contenedor, donde viven las credenciales:

    ssh goncloud 'docker exec -i orbit-app-1 python - --sku <SKU FBM de MX> \
        --sku-us <SKU de US> --precio-mx 1489.00 --precio-us 68.50' \
        < docs/evidencia/repricing-02/ejecucion/F.0/sonda_fees_fbm.py

La salida no trae el SKU, el ASIN ni el id del vendedor: cada universo se
identifica por su nombre y el SKU por el id interno de producto que se
pase en --producto-mx / --producto-us (solo para leer la salida).
"""

from __future__ import annotations

import argparse
import json
import sys

from app.spapi.client import MERCADOS, SpapiClient

IDENTIFICADOR = "rp02-f0-sonda-fees-fbm"


def cuerpo(marketplace: str, moneda: str, precio: str) -> bytes:
    return json.dumps(
        {
            "FeesEstimateRequest": {
                "MarketplaceId": marketplace,
                "IsAmazonFulfilled": False,
                "PriceToEstimateFees": {"ListingPrice": {"CurrencyCode": moneda, "Amount": precio}},
                "Identifier": IDENTIFICADOR,
            }
        }
    ).encode("utf-8")


def sondear(cliente: SpapiClient, sku: str, marketplace: str, moneda: str, precio: str) -> dict:
    respuesta = cliente.post_fees(sku, cuerpo(marketplace, moneda, precio))
    salida: dict = {"http": respuesta.status_code}
    if respuesta.status_code != 200:
        return salida
    datos = respuesta.json()
    resultado = (datos.get("payload", datos) or {}).get("FeesEstimateResult") or {}
    salida["Status"] = resultado.get("Status")
    error = resultado.get("Error")
    if isinstance(error, dict):
        salida["Error"] = {
            clave: error.get(clave) for clave in ("Code", "Message", "Detail") if error.get(clave)
        }
    identificador = resultado.get("FeesEstimateIdentifier") or {}
    # Del identificador solo sale el eco del flag FBM: IdValue (SKU) y
    # IdType no se imprimen.
    salida["identificador_is_amazon_fulfilled"] = identificador.get("IsAmazonFulfilled")
    estimacion = resultado.get("FeesEstimate") or {}
    detalles = estimacion.get("FeeDetailList") or []
    salida["tipos_de_fee"] = [detalle.get("FeeType") for detalle in detalles]
    total = estimacion.get("TotalFeesEstimate") or {}
    salida["total_fees_estimate"] = {
        "Amount": total.get("Amount"),
        "CurrencyCode": total.get("CurrencyCode"),
    }
    salida["fees_con_impuesto"] = [
        detalle.get("FeeType")
        for detalle in detalles
        if ((detalle.get("TaxAmount") or {}).get("Amount") or 0) != 0
    ]
    return salida


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sku", required=True, help="SKU FBM de amazon_mx")
    parser.add_argument("--sku-us", required=True, help="SKU FBM de amazon_us")
    parser.add_argument("--precio-mx", default="500.00", help="precio a cotizar en MXN")
    parser.add_argument("--precio-us", default="100.00", help="precio a cotizar en USD")
    parser.add_argument("--producto-mx", type=int, help="id interno de product (MX), solo rotulo")
    parser.add_argument("--producto-us", type=int, help="id interno de product (US), solo rotulo")
    args = parser.parse_args()

    cliente = SpapiClient()
    plan = (
        (
            "amazon_mx/fbm",
            args.sku,
            MERCADOS["amazon_mx"],
            "MXN",
            args.precio_mx,
            args.producto_mx,
        ),
        (
            "amazon_us/fbm",
            args.sku_us,
            MERCADOS["amazon_us"],
            "USD",
            args.precio_us,
            args.producto_us,
        ),
    )
    for universo, sku, marketplace, moneda, precio, producto in plan:
        rotulo = f"{universo} (product_id={producto})" if producto else universo
        print(f"== {rotulo} ==")
        print(
            json.dumps(
                sondear(cliente, sku, marketplace, moneda, precio),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
