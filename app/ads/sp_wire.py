"""Parser unico de respuestas HTTP 207 / acks de Amazon Ads SP v3.

Consolida las variantes que antes vivian duplicadas en `app/ads/write.py`,
`app/ads/archivar.py` y `app/apply_harvest.py`. Fail-closed en `errores_put`
(un 207 sin contenedor/success o cuerpo ilegible se reporta como error, nunca
exito).
"""

from __future__ import annotations

import httpx


def errores_put(respuesta: httpx.Response, contenedor: str = "campaigns") -> list[str]:
    """Errores anidados de un PUT/POST bulk v3 (fail-closed en 207).

    Procesa tanto el objeto `{error, success}` (sondas V.0) como listas por item.
    Cuerpo ilegible, item no-dict o 207 sin contenedor ni `success` son error.
    Lista vacia solo con exito limpio.
    """
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return ["respuesta sin JSON"]
    if not isinstance(cuerpo, dict):
        return ["respuesta no es objeto"]
    errores: list[str] = []
    top = cuerpo.get("errors")
    if isinstance(top, list) and top:
        errores.extend(str(e) for e in top)
    codigo_tope = str(cuerpo.get("code", ""))
    if codigo_tope[:1] in ("4", "5"):
        errores.append(cuerpo.get("description") or f"code {codigo_tope}")
    items = cuerpo.get(contenedor)
    es_multi = respuesta.status_code == 207
    if items is None:
        if es_multi and not errores:
            errores.append(f"respuesta sin contenedor {contenedor}")
    elif isinstance(items, dict):
        fallos = items.get("error")
        if isinstance(fallos, list) and fallos:
            errores.extend(str(e) for e in fallos)
        elif (
            es_multi
            and not errores
            and not (isinstance(items.get("success"), list) and items["success"])
        ):
            errores.append(f"contenedor {contenedor} sin success ni error")
    elif isinstance(items, list):
        if es_multi and not items and not errores:
            errores.append(f"contenedor {contenedor} vacio")
        for item in items:
            if not isinstance(item, dict):
                errores.append(f"item de {contenedor} ilegible")
                continue
            anidados = item.get("errors")
            if isinstance(anidados, list) and anidados:
                errores.extend(str(e) for e in anidados)
            codigo = str(item.get("code", ""))
            if codigo[:1] in ("4", "5"):
                errores.append(item.get("description") or f"code {codigo}")
    else:
        errores.append(f"contenedor {contenedor} invalido")
    return errores


def errores_de_ack(ack: object) -> list:
    """Entradas de `error[]` de un ack 207 anidado `{"<recurso>": {"error": [...]}}`."""
    if not isinstance(ack, dict):
        return []
    for valor in ack.values():
        if isinstance(valor, dict):
            error = valor.get("error")
            if isinstance(error, list) and error:
                return error
    return []


def _estado_id_plano_de(dic: dict, claves: tuple[str, ...]) -> tuple[bool, str | None]:
    """Devuelve `(presente, id_valido)` para las claves de id de un diccionario.

    Distingue ausencia real `(False, None)` de un id presente pero invalido
    (booleano, cadena vacia/blanca, tipo no escalar -> `(True, None)`), para
    que los deletes puedan rechazar acuses corruptos sin romper acuses que
    omiten el id por completo.
    """
    hubo_invalido = False
    for clave in claves:
        if clave not in dic:
            continue
        valor = dic[clave]
        if valor is None:
            continue
        if isinstance(valor, list):
            if not valor:
                continue
            primero = valor[0]
            if (
                isinstance(primero, str | int)
                and not isinstance(primero, bool)
                and str(primero).strip()
            ):
                return True, str(primero)
            hubo_invalido = True
            continue
        if isinstance(valor, str | int) and not isinstance(valor, bool) and str(valor).strip():
            return True, str(valor)
        hubo_invalido = True
    return hubo_invalido, None


def estado_id_de_ack(
    ack: object,
    clave_principal: str,
    *,
    claves_extra: tuple[str, ...] = ("keywordId", "keywordIdList"),
) -> tuple[bool, str | None]:
    """Devuelve `(presente, id_valido)` recorriendo el ack 207 (plano y anidado)."""
    if not isinstance(ack, dict):
        return False, None
    claves = (clave_principal, f"{clave_principal}List", *claves_extra)
    presente, id_ = _estado_id_plano_de(ack, claves)
    if id_ is not None:
        return True, id_
    hubo_invalido = presente
    for valor in ack.values():
        if not isinstance(valor, dict):
            continue
        success = valor.get("success")
        if not isinstance(success, list):
            continue
        for item in success:
            if not isinstance(item, dict):
                continue
            p_item, id_ = _estado_id_plano_de(item, claves)
            if id_ is not None:
                return True, id_
            hubo_invalido = hubo_invalido or p_item
            for sub in item.values():
                if isinstance(sub, dict):
                    p_sub, id_ = _estado_id_plano_de(sub, claves)
                    if id_ is not None:
                        return True, id_
                    hubo_invalido = hubo_invalido or p_sub
    return hubo_invalido, None


def id_de_ack(
    ack: object,
    clave_principal: str,
    *,
    claves_extra: tuple[str, ...] = ("keywordId", "keywordIdList"),
) -> str | None:
    """Id del objeto creado/borrado segun el ack 207 (anidado o plano). `None` si ilegible."""
    _, id_ = estado_id_de_ack(ack, clave_principal, claves_extra=claves_extra)
    return id_


def id_product_ad_de_ack(ack: object) -> str | None:
    """`adId` o `productAdId` devuelto al crear un product ad; `None` si ilegible."""
    return id_de_ack(ack, "adId", claves_extra=("productAdId",))
