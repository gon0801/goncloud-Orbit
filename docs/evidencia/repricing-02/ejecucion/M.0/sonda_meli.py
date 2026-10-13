"""REPRICING 02 · M.0 · sonda de lectura de Mercado Libre (solo GET).

Una consulta por pregunta, todas por ``ClienteMeli.get`` (GET-only). Nada
escribe: no hay PUT ni POST de recurso, y la unica peticion de
autenticacion posible es el refresh que el propio cliente hace tras un 401.

Se corre DENTRO del contenedor, donde viven las credenciales y donde un
refresco de token reescribe el archivo correcto (fuera del contenedor, con
una copia de los secretos, ese refresco deja inservible el token de
produccion):

    ssh goncloud 'docker exec -i orbit-app-1 python -' \
        < docs/evidencia/repricing-02/ejecucion/M.0/sonda_meli.py

Antes de imprimir, cada respuesta pasa por ``sanea``: ids de publicacion,
de vendedor y de aplicacion, titulos, enlaces, imagenes y SKUs salen
redactados (repo publico). Las cuatro preguntas:

1. De donde sale el SKU de cada variante (una publicacion con variantes y
   una sin variantes).
2. Si hay consulta de cargos por precio, categoria y tipo de publicacion
   que separe porcentaje y fijo.
3. Si el precio de una publicacion con variantes va una vez o por variante.
4. Si el token permite escribir, leido de los permisos que declara la
   aplicacion (nunca escribiendo).

De paso, visitas de la publicacion con variantes (senal de trafico, D10).
"""

from __future__ import annotations

import json
import re
import sys

from app.reputacion_clientes import ClienteMeli, MeliCredentials, ReputacionError

FORMA_ODDO = re.compile(r"^[A-Z0-9]{2}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
_ID_ITEM = re.compile(r"\bMLM\d{6,}\b")

CLAVES_ID = {
    "id",
    "item_id",
    "seller_id",
    "user_id",
    "application_id",
    "client_id",
    "catalog_product_id",
    "parent_item_id",
    "variation_id",
    "seller_sku",
}
CLAVES_FUERA = {
    "title",
    "permalink",
    "thumbnail",
    "secure_thumbnail",
    "pictures",
    "descriptions",
    "seller_contact",
    "location",
    "phone",
    "queries",
    "scroll_id",
    "first_name",
    "last_name",
    "nickname",
    "address",
    "email",
}


def sanea(obj):
    """Copia con ids, titulos, enlaces y SKUs redactados."""
    if isinstance(obj, dict):
        salida = {}
        for clave, valor in obj.items():
            if clave in CLAVES_FUERA:
                salida[clave] = "<fuera>"
            elif clave in CLAVES_ID:
                salida[clave] = "<id>"
            else:
                salida[clave] = sanea(valor)
        return salida
    if isinstance(obj, list):
        return [sanea(item) for item in obj]
    if isinstance(obj, str):
        return _ID_ITEM.sub("<item>", obj)
    return obj


def imprimir(titulo: str, ruta: str, datos) -> None:
    print(f"\n== {titulo} ==")
    print(f"ruta: {ruta}")
    print(json.dumps(sanea(datos), ensure_ascii=False, indent=2, default=str))


def consultar(
    cliente: ClienteMeli,
    titulo: str,
    ruta: str,
    params: dict | None = None,
    ruta_mostrar: str | None = None,
):
    try:
        datos, _ = cliente.get(ruta, params)
    except ReputacionError as exc:
        print(f"\n== {titulo} ==")
        print(f"ruta: {ruta_mostrar or ruta}")
        print(f"ERROR: {exc}")
        return None
    imprimir(titulo, ruta_mostrar or ruta, datos)
    return datos


def forma_oddo(valor) -> bool | None:
    if not isinstance(valor, str) or not valor:
        return None
    return bool(FORMA_ODDO.match(valor))


def sku_presente(valor) -> dict:
    """Dice SI un campo trae SKU y si calza con la forma de odoo_sku."""
    presente = isinstance(valor, str) and bool(valor.strip())
    return {
        "presente": presente,
        "longitud": len(valor) if presente else None,
        "forma_odoo_sku": forma_oddo(valor) if presente else None,
    }


def proyectar_item(item: dict) -> dict:
    """La forma de la publicacion sin SKU ni id: solo presencia y precios."""
    variaciones = item.get("variations") or []
    proyeccion = {
        "status": item.get("status"),
        "category_id": item.get("category_id"),
        "listing_type_id": item.get("listing_type_id"),
        "seller_custom_field_raiz": sku_presente(item.get("seller_custom_field")),
        "precio_raiz": item.get("price"),
        "moneda": item.get("currency_id"),
        "n_variaciones": len(variaciones),
        "sku_por_variacion": [
            {
                "seller_sku": sku_presente(variacion.get("seller_sku")),
                "seller_custom_field": sku_presente(variacion.get("seller_custom_field")),
                "price": variacion.get("price"),
                "available_quantity": variacion.get("available_quantity"),
            }
            for variacion in variaciones
        ],
    }
    return proyeccion


def main() -> int:
    credenciales = MeliCredentials.from_secrets_dir()
    cliente = ClienteMeli(credenciales)

    # Punto de partida: quien vende (el id del vendedor no se imprime).
    try:
        yo, _ = cliente.get("/users/me")
    except ReputacionError as exc:
        print(f"/users/me: ERROR {exc}")
        return 1
    vendedor = yo.get("id")
    print("== vendedor ==")
    print(f"ruta: /users/me -> {{'id': '<id>', 'site_id': {yo.get('site_id')!r}}}")

    # 1 y 3: una publicacion con variantes y una sin.
    busqueda = consultar(
        cliente,
        "publicaciones (ids redactados)",
        f"/users/{vendedor}/items/search",
        {"search_type": "scan", "limit": 50},
        ruta_mostrar="/users/{vendedor}/items/search",
    )
    ids = [str(i) for i in (busqueda or {}).get("results") or []]
    print(f"\ntotal publicaciones en la primera pagina: {len(ids)}")

    con_variantes = None
    sin_variantes = None
    candidatos = []
    for id_item in ids[:30]:
        item = None
        try:
            item, _ = cliente.get(f"/items/{id_item}")
        except ReputacionError as exc:
            print(f"items/{{id}}: ERROR {exc}")
            continue
        candidatos.append(
            {
                "variantes": len(item.get("variations") or []),
                "status": item.get("status"),
            }
        )
        if con_variantes is None and item.get("variations"):
            con_variantes = item
        if sin_variantes is None and not item.get("variations"):
            sin_variantes = item
        if con_variantes is not None and sin_variantes is not None:
            break
    print(
        f"publicaciones inspeccionadas: {len(candidatos)} "
        f"(con variantes: {sum(1 for c in candidatos if c['variantes'])})"
    )
    if con_variantes is None or sin_variantes is None:
        print("FALTA: no se encontro una publicacion de cada tipo")
        return 1

    imprimir(
        "1 y 3: publicacion CON variantes (forma)",
        "/items/{id}",
        proyectar_item(con_variantes),
    )
    imprimir(
        "1: publicacion SIN variantes (forma)",
        "/items/{id}",
        proyectar_item(sin_variantes),
    )

    # 2: consulta de cargos por precio, categoria y tipo de publicacion.
    precio = con_variantes.get("price")
    categoria = con_variantes.get("category_id")
    tipo = con_variantes.get("listing_type_id")
    consultar(
        cliente,
        "2: cargos de la publicacion con variantes",
        "/pricing/llm/listing-charges",
        {"item_id": con_variantes.get("id"), "price": precio, "quantity": 1},
    )
    consultar(
        cliente,
        "2: tabla de cargos por categoria, tipo y precio",
        "/pricing/llm/listing-charges/tables",
        {
            "category_id": categoria,
            "listing_type_id": tipo,
            "price": precio,
            "quantity": 1,
        },
    )
    # Visitas de la misma publicacion (trafico, D10).
    consultar(
        cliente,
        "visitas de la publicacion con variantes (7 dias)",
        f"/items/{con_variantes.get('id')}/visits/time-window",
        {"last": 7, "unit": "day"},
        ruta_mostrar="/items/{id}/visits/time-window",
    )

    # 4: permisos que la aplicacion declara para este vendedor.
    permisos = consultar(
        cliente,
        "4: permisos del token (scopes declarados por la aplicacion)",
        f"/users/{vendedor}/applications/{credenciales.client_id}",
        ruta_mostrar="/users/{vendedor}/applications/{aplicacion}",
    )
    if permisos is not None:
        print(f"\nscopes: {sorted(permisos.get('scopes') or [])}")

    cliente.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
