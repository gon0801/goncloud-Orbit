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
_VENDEDOR_RUTA = re.compile(r"/users/\d+")

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
            elif isinstance(clave, str) and _ID_ITEM.fullmatch(clave):
                salida["<item>"] = sanea(valor)
            else:
                salida[clave] = sanea(valor)
        return salida
    if isinstance(obj, list):
        return [sanea(item) for item in obj]
    if isinstance(obj, str):
        texto = _ID_ITEM.sub("<item>", obj)
        return _VENDEDOR_RUTA.sub("/users/{vendedor}", texto)
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
        print(f"ERROR: {sanea(str(exc))}")
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
        "sku_campo_seller_custom_field_raiz": sku_presente(item.get("seller_custom_field")),
        "precio_raiz": item.get("price"),
        "moneda": item.get("currency_id"),
        "n_variaciones": len(variaciones),
        "sku_por_variacion": [
            {
                "sku_campo_seller_sku": sku_presente(variacion.get("seller_sku")),
                "sku_campo_seller_custom_field": sku_presente(variacion.get("seller_custom_field")),
                "price": variacion.get("price"),
                "available_quantity": variacion.get("available_quantity"),
            }
            for variacion in variaciones
        ],
    }
    return proyeccion


def cargar_credenciales() -> tuple[MeliCredentials, dict]:
    """Lee meli_tokens.json sin exigir client_id/client_secret.

    En produccion ese archivo trae solo la respuesta OAuth (access_token,
    refresh_token, scope, ...). Con el token vivo los GET bastan; si el
    archivo trajera client_id/client_secret (futuro), se usan tambien. Un
    401 con client_id vacio hace que el refresh del cliente falle ANTES de
    reescribir el archivo: la sonda reporta el error y no rompe nada.
    """
    import json

    from app.ads.config import directorio_secretos
    from app.reputacion_clientes import MELI_TOKENS_FILENAME

    ruta = directorio_secretos() / MELI_TOKENS_FILENAME
    datos = json.loads(ruta.read_text())
    creds = MeliCredentials(
        access_token=datos["access_token"],
        refresh_token=datos.get("refresh_token", ""),
        client_id=datos.get("client_id", ""),
        client_secret=datos.get("client_secret", ""),
        ruta=ruta,
    )
    return creds, datos


def main() -> int:
    credenciales, archivo_token = cargar_credenciales()
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
    agregado = {
        "publicaciones": 0,
        "con_variantes": 0,
        "variantes_totales": 0,
        "variantes_con_seller_sku": 0,
        "variantes_con_seller_custom_field": 0,
        "raices_con_seller_custom_field": 0,
        "raiz_o_variaciones_con_forma_odoo": 0,
    }
    for id_item in ids[:30]:
        item = None
        try:
            item, _ = cliente.get(f"/items/{id_item}")
        except ReputacionError as exc:
            print(f"items/{{id}}: ERROR {sanea(str(exc))}")
            continue
        agregado["publicaciones"] += 1
        variaciones = item.get("variations") or []
        raiz_custom = item.get("seller_custom_field")
        if variaciones:
            agregado["con_variantes"] += 1
            agregado["variantes_totales"] += len(variaciones)
            agregado["variantes_con_seller_sku"] += sum(
                1 for v in variaciones if sku_presente(v.get("seller_sku"))["presente"]
            )
            agregado["variantes_con_seller_custom_field"] += sum(
                1 for v in variaciones if sku_presente(v.get("seller_custom_field"))["presente"]
            )
        if sku_presente(raiz_custom)["presente"]:
            agregado["raices_con_seller_custom_field"] += 1
        if forma_oddo(raiz_custom) or any(forma_oddo(v.get("seller_sku")) for v in variaciones):
            agregado["raiz_o_variaciones_con_forma_oddo"] += 1
        if con_variantes is None and variaciones:
            con_variantes = item
        if sin_variantes is None and not variaciones:
            sin_variantes = item
    imprimir("1: agregado de SKU en publicaciones (hasta 30)", "/items/{id}", agregado)
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
    # Las rutas de pricing/llm dieron 404 en la primera corrida; se prueban
    # las alternativas publicas de cargo por categoria, tipo y precio.
    precio = con_variantes.get("price")
    categoria = con_variantes.get("category_id")
    tipo = con_variantes.get("listing_type_id")
    id_con_variantes = con_variantes.get("id")
    rutas_cargos = (
        (
            "2: cargos de la publicacion con variantes",
            "/pricing/llm/listing-charges",
            {"item_id": id_con_variantes, "price": precio, "quantity": 1},
        ),
        (
            "2: tabla de cargos por categoria, tipo y precio",
            "/pricing/llm/listing-charges/tables",
            {"category_id": categoria, "listing_type_id": tipo, "price": precio, "quantity": 1},
        ),
        (
            "2: comision de venta por categoria y tipo a este precio",
            f"/categories/{categoria}/listing_types/{tipo}/sale_fee",
            {"price": precio, "quantity": 1},
        ),
        (
            "2: comision de venta de la publicacion sin variantes",
            "/sale_fee",
            {"item_id": sin_variantes.get("id"), "price": sin_variantes.get("price")},
        ),
        (
            "2: precio de publicacion por categoria y tipo",
            "/listing_prices",
            {
                "price": precio,
                "category_id": categoria,
                "listing_type_id": tipo,
            },
        ),
        (
            "2: tipo de publicacion (estructura de cargos del sitio)",
            f"/sites/{con_variantes.get('site_id')}/listing_types/{tipo}",
        ),
        (
            "2: tipo de publicacion en la categoria (excepciones de cargo)",
            f"/categories/{categoria}/listing_types/{tipo}",
        ),
    )
    for titulo, ruta, *params in rutas_cargos:
        consultar(cliente, titulo, ruta, params[0] if params else None)

    # Visitas de la misma publicacion (trafico, D10).
    rutas_visitas = (
        (
            "visitas de la publicacion con variantes (ventana de 7 dias)",
            f"/items/{id_con_variantes}/visits/time-window",
            {"last": 7, "unit": "day"},
            "/items/{id}/visits/time-window",
        ),
        (
            "visitas de la publicacion con variantes (total)",
            f"/items/{id_con_variantes}/visits",
            None,
            "/items/{id}/visits",
        ),
        (
            "visitas por ids (total)",
            "/visits/items",
            {"ids": id_con_variantes},
            "/visits/items?ids={id}",
        ),
    )
    for titulo, ruta, params, mostrar in rutas_visitas:
        consultar(cliente, titulo, ruta, params, ruta_mostrar=mostrar)

    # 4: permisos que la aplicacion declara para este vendedor. Dos caminos:
    # el detalle por aplicacion (si hay client_id) o la lista de
    # aplicaciones autorizadas; de respaldo, el scope del propio token.
    permisos = None
    if credenciales.client_id:
        permisos = consultar(
            cliente,
            "4: permisos del token (scopes declarados por la aplicacion)",
            f"/users/{vendedor}/applications/{credenciales.client_id}",
            ruta_mostrar="/users/{vendedor}/applications/{aplicacion}",
        )
        if permisos is not None:
            print(f"\nscopes: {sorted(permisos.get('scopes') or [])}")
    if permisos is None:
        lista = consultar(
            cliente,
            "4: aplicaciones autorizadas por el vendedor",
            f"/users/{vendedor}/applications",
            ruta_mostrar="/users/{vendedor}/applications",
        )
        scopes = None
        for entrada in lista or []:
            if isinstance(entrada, dict) and entrada.get("scopes"):
                scopes = sorted(entrada.get("scopes") or [])
                print(f"\nscopes declarados por la aplicacion: {scopes}")
        if scopes is None:
            print("\nscopes del token (del archivo, sin consulta):")
            print(f"scope: {archivo_token.get('scope')}")

    cliente.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
