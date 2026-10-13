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
redactados (repo publico); el cruce contra ``product`` se imprime solo por
conteos. Las cuatro preguntas:

1. De donde sale el SKU de cada variante (una publicacion con variantes y
   una sin variantes): del atributo ``SELLER_SKU`` del item, que solo llega
   pidiendo ``include_attributes=all`` (ronda r2: sin el parametro las
   variaciones no traen ``attributes``).
2. Si hay consulta de cargos por precio, categoria y tipo de publicacion
   que separe porcentaje y fijo: ``/sites/{sitio}/listing_prices``.
3. Si el precio de una publicacion con variantes va una vez o por variante.
4. Si el token permite escribir, leido de los permisos que declara la
   aplicacion (nunca escribiendo).

De paso, visitas de la publicacion con variantes (senal de trafico, D10) y
la estructura de cargos del sitio y de la categoria como dato de apoyo.
"""

from __future__ import annotations

import json
import re
import sys

import psycopg

from app.db import aplicar_host_override
from app.reputacion_clientes import ClienteMeli, MeliCredentials, ReputacionError

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


def sku_de_atributos(atributos) -> str | None:
    """El SKU del item vive en el atributo SELLER_SKU (value_name)."""
    for atributo in atributos or []:
        if atributo.get("id") == "SELLER_SKU":
            valor = atributo.get("value_name")
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
    return None


def presente(valor) -> bool:
    return isinstance(valor, str) and bool(valor.strip())


def proyectar_item(item: dict) -> dict:
    """La forma de la publicacion sin SKU ni id: presencia y precios."""
    variaciones = item.get("variations") or []
    return {
        "status": item.get("status"),
        "category_id": item.get("category_id"),
        "listing_type_id": item.get("listing_type_id"),
        "sku_en_atributo_raiz": presente(sku_de_atributos(item.get("attributes"))),
        "seller_custom_field_raiz": presente(item.get("seller_custom_field")),
        "precio_raiz": item.get("price"),
        "moneda": item.get("currency_id"),
        "n_variaciones": len(variaciones),
        "variaciones": [
            {
                "sku_en_atributo": presente(sku_de_atributos(variacion.get("attributes"))),
                "campo_seller_sku": presente(variacion.get("seller_sku")),
                "campo_seller_custom_field": presente(variacion.get("seller_custom_field")),
                "price": variacion.get("price"),
                "available_quantity": variacion.get("available_quantity"),
            }
            for variacion in variaciones
        ],
    }


def cruzar_con_product(miembros: list[tuple[int, str | None]]) -> dict | None:
    """Cruce por conteo contra product.odoo_sku, rol lector, READ ONLY.

    Los SKU nunca se imprimen: solo cuantos miembros mapean a un producto y
    cuantas publicaciones quedan con todos, alguno o ningun miembro mapeado
    (la misma cuenta que la revision R01-r2 reprodujo con gen_cruce.py).
    """
    import os

    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        return None
    sql_consulta = (
        "with m(pub, sku) as (select unnest(%s::int[]), unnest(%s::text[])),"
        " x as (select m.pub, m.sku, p.id as pid from m"
        " left join product p on p.odoo_sku = m.sku),"
        " porpub as (select pub, count(*) as n, count(pid) as mapeados from x group by 1)"
        " select 'miembros', count(*), 'con_sku', count(sku),"
        " 'mapeados_a_product', count(pid), 'productos_distintos',"
        " count(distinct pid) from x"
        " union all select 'publicaciones', count(*), 'todos_mapeados',"
        " count(*) filter (where mapeados = n), 'alguno',"
        " count(*) filter (where mapeados > 0 and mapeados < n), 'ninguno',"
        " count(*) filter (where mapeados = 0) from porpub"
    )
    try:
        with psycopg.connect(aplicar_host_override(dsn, os.environ.get("ORBIT_PG_HOST"))) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(sql_consulta, ([p for p, _ in miembros], [s for _, s in miembros]))
                filas = cur.fetchall()
            conn.rollback()
        return {"lineas": ["|".join(map(str, f)) for f in filas]}
    except Exception as exc:  # noqa: BLE001
        return {"error": sanea(f"{type(exc).__name__}: {exc}")[:200]}


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


def inspeccionar(cliente: ClienteMeli, ids: list[str]):
    """Recorre hasta 30 publicaciones con include_attributes=all."""
    agregado = {
        "publicaciones": 0,
        "con_variantes": 0,
        "variantes": 0,
        "var_campo_seller_sku": 0,
        "var_campo_seller_custom_field": 0,
        "var_con_clave_attributes": 0,
        "var_con_attr_SELLER_SKU": 0,
        "raices_con_seller_custom_field": 0,
        "raices_con_attr_SELLER_SKU": 0,
        "pub_con_variantes_todas_con_sku": 0,
        "pub_sin_variantes_con_sku_en_raiz": 0,
        "pub_con_variantes_precio_distinto": 0,
    }
    skus_variacion: list[str] = []
    miembros: list[tuple[int, str | None]] = []
    con_variantes = None
    sin_variantes = None
    for indice, id_item in enumerate(ids[:30], 1):
        item = None
        try:
            item, _ = cliente.get(f"/items/{id_item}", {"include_attributes": "all"})
        except ReputacionError as exc:
            print(f"items/{{id}}: ERROR {sanea(str(exc))}")
            continue
        agregado["publicaciones"] += 1
        variaciones = item.get("variations") or []
        sku_raiz = sku_de_atributos(item.get("attributes"))
        if sku_raiz:
            agregado["raices_con_attr_SELLER_SKU"] += 1
        if presente(item.get("seller_custom_field")):
            agregado["raices_con_seller_custom_field"] += 1
        if variaciones:
            agregado["con_variantes"] += 1
            agregado["variantes"] += len(variaciones)
            con_sku = 0
            for variacion in variaciones:
                if presente(variacion.get("seller_sku")):
                    agregado["var_campo_seller_sku"] += 1
                if presente(variacion.get("seller_custom_field")):
                    agregado["var_campo_seller_custom_field"] += 1
                if "attributes" in variacion:
                    agregado["var_con_clave_attributes"] += 1
                sku = sku_de_atributos(variacion.get("attributes"))
                miembros.append((indice, sku))
                if sku:
                    agregado["var_con_attr_SELLER_SKU"] += 1
                    skus_variacion.append(sku)
                    con_sku += 1
            if con_sku == len(variaciones):
                agregado["pub_con_variantes_todas_con_sku"] += 1
            if len({v.get("price") for v in variaciones} | {item.get("price")}) > 1:
                agregado["pub_con_variantes_precio_distinto"] += 1
            if con_variantes is None:
                con_variantes = item
        else:
            miembros.append((indice, sku_raiz))
            if sku_raiz:
                agregado["pub_sin_variantes_con_sku_en_raiz"] += 1
            if sin_variantes is None:
                sin_variantes = item
    agregado["sku_variacion_distintos"] = len(set(skus_variacion))
    return agregado, miembros, skus_variacion, con_variantes, sin_variantes


def pregunta_cargos_y_visitas(
    cliente: ClienteMeli, con_variantes: dict, sin_variantes: dict
) -> None:
    """Pregunta 2 (cargos por precio/categoria/tipo) y visitas de apoyo."""
    # cargos: consulta de cargos por precio, categoria y tipo de publicacion.
    # La ruta es /sites/{sitio}/listing_prices (ronda r2: /listing_prices
    # en la raiz daba 404). Tres precios, incluido uno bajo.
    categoria = con_variantes.get("category_id")
    tipo = con_variantes.get("listing_type_id")
    sitio = con_variantes.get("site_id")
    for precio in (con_variantes.get("price"), sin_variantes.get("price"), 150):
        consultar(
            cliente,
            f"2: cargos por precio {precio} (categoria y tipo de la muestra)",
            f"/sites/{sitio}/listing_prices",
            {"price": precio, "category_id": categoria, "listing_type_id": tipo},
            ruta_mostrar="/sites/{sitio}/listing_prices",
        )
    # Dato de apoyo: la estructura porcentual del sitio y de la categoria.
    consultar(
        cliente,
        "2: apoyo · tipo de publicacion (estructura de cargos del sitio)",
        f"/sites/{sitio}/listing_types/{tipo}",
        ruta_mostrar="/sites/{sitio}/listing_types/{tipo}",
    )
    consultar(
        cliente,
        "2: apoyo · tipo de publicacion en la categoria",
        f"/categories/{categoria}/listing_types/{tipo}",
        ruta_mostrar="/categories/{categoria}/listing_types/{tipo}",
    )

    # Visitas de la misma publicacion (trafico, D10). La ventana lleva
    # guion bajo (ronda r2: time-window con guion daba 404).
    consultar(
        cliente,
        "visitas de la publicacion con variantes (ventana de 7 dias)",
        f"/items/{con_variantes.get('id')}/visits/time_window",
        {"last": 7, "unit": "day"},
        ruta_mostrar="/items/{id}/visits/time_window",
    )
    consultar(
        cliente,
        "visitas por ids (total)",
        "/visits/items",
        {"ids": con_variantes.get("id")},
        ruta_mostrar="/visits/items?ids={id}",
    )


def pregunta_permisos(
    cliente: ClienteMeli, credenciales: MeliCredentials, archivo_token: dict, vendedor
) -> None:
    """Pregunta 4: scopes declarados por la aplicacion (sin escribir)."""
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


def main() -> int:
    credenciales, archivo_token = cargar_credenciales()
    cliente = ClienteMeli(credenciales)

    # Punto de partida: quien vende (el id del vendedor no se imprime).
    try:
        yo, _ = cliente.get("/users/me")
    except ReputacionError as exc:
        print(f"/users/me: ERROR {sanea(str(exc))}")
        return 1
    vendedor = yo.get("id")
    print("== vendedor ==")
    print(f"ruta: /users/me -> {{'id': '<id>', 'site_id': {yo.get('site_id')!r}}}")

    # 1 y 3: una publicacion con variantes y una sin, con TODOS los atributos.
    busqueda = consultar(
        cliente,
        "publicaciones (ids redactados)",
        f"/users/{vendedor}/items/search",
        {"search_type": "scan", "limit": 50},
        ruta_mostrar="/users/{vendedor}/items/search",
    )
    ids = [str(i) for i in (busqueda or {}).get("results") or []]
    print(f"\ntotal publicaciones en la primera pagina: {len(ids)}")

    agregado, miembros, skus_variacion, con_variantes, sin_variantes = inspeccionar(cliente, ids)
    imprimir(
        "1: agregado con include_attributes=all (hasta 30 publicaciones)",
        "/items/{id}?include_attributes=all",
        agregado,
    )
    if con_variantes is None or sin_variantes is None:
        print("FALTA: no se encontro una publicacion de cada tipo")
        return 1

    # La misma publicacion SIN el parametro: por que la ronda r2 no veia SKUs.
    mostrar_sin_parametro = None
    try:
        plano, _ = cliente.get(f"/items/{con_variantes.get('id')}")
        plano_variaciones = plano.get("variations") or []
        mostrar_sin_parametro = {
            "variaciones": len(plano_variaciones),
            "variaciones_con_clave_attributes": sum(
                1 for v in plano_variaciones if "attributes" in v
            ),
            "raiz_con_attr_SELLER_SKU": presente(sku_de_atributos(plano.get("attributes"))),
        }
    except ReputacionError as exc:
        mostrar_sin_parametro = {"error": sanea(str(exc))}
    imprimir(
        "1: la misma publicacion SIN include_attributes",
        "/items/{id}",
        mostrar_sin_parametro,
    )

    imprimir(
        "1 y 3: publicacion CON variantes (forma)",
        "/items/{id}?include_attributes=all",
        proyectar_item(con_variantes),
    )
    imprimir(
        "1: publicacion SIN variantes (forma)",
        "/items/{id}?include_attributes=all",
        proyectar_item(sin_variantes),
    )

    # Cruce real contra product.odoo_sku: por conteo, sin imprimir SKU.
    imprimir(
        "1: cruce de los SKU leidos contra product.odoo_sku (conteos)",
        "select ... from (values pub,sku) left join product on odoo_sku = sku",
        cruzar_con_product(miembros) or {"error": "sin ORBIT_DSN_READ"},
    )

    pregunta_cargos_y_visitas(cliente, con_variantes, sin_variantes)

    pregunta_permisos(cliente, credenciales, archivo_token, vendedor)

    cliente.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
