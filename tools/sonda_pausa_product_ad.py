#!/usr/bin/env python3
"""Sonda 1 de V.0 (BIDS 02): pausar y reactivar UN product ad.

Que sella: que `PUT /sp/productAds` acepta `state` PAUSED y ENABLED, con que
cuerpo y que responde, para sacar un producto de una bolsa compartida con una
pausa reversible en vez de archivar (I.4).

Por que no afecta entregas: el anuncio esta ENABLED dentro de una campana que
ya esta PAUSED (`A1U - Auto Discovery - US`), asi que pausarlo y reactivarlo
no cambia lo que Amazon muestra.

Seguros: aborta sin escribir (exit 2) si el anuncio no esta ENABLED, si la
campana no esta PAUSED, si el anuncio no es de esa campana, o si no hay
exactamente una campana con ese nombre. Toca un solo anuncio.

Contrato V.0: un solo JSON por `scrub` con cada cuerpo enviado, cada
respuesta, cada lectura, `iguales` (llave por llave de `antes` vs lectura
final) y `resultado`. Exit 0 con OK, 2 con ABORTADO, 1 con REVISAR. Sin la
bandera imprime `antes` y los cuerpos planeados y sale 0 sin escribir.

Regla CodeRabbit: una respuesta no-2xx es FALLA. Tras cualquier falla el
`finally` lee el estado y solo manda el PUT de regreso si la lectura confirma
el estado que cree revertir (PAUSED); si confirma el original, no hay nada que
revertir y no se repite ningun PUT a ciegas.

Corre (una sonda a la vez, con el PR de codigo mergeado):

    ssh goncloud 'docker exec -i orbit-app-1 python -' < tools/sonda_pausa_product_ad.py
    ssh goncloud 'docker exec -i orbit-app-1 python - --acepto-mutacion-real' \\
      < tools/sonda_pausa_product_ad.py
"""

import argparse
import json
import time

from app.ads.client import AdsClient, AdsCredentials
from app.ads.structure_api import listar_todo, perfiles_aceptados
from app.redaction import scrub

AD_ID = "284583606382521"  # ASIN B0B384M2M8; elegido por lectura el 2026-10-09
CAMPANA = "A1U - Auto Discovery - US"
PLATFORM = "amazon_us"
VENDOR = "application/vnd.spproductad.v3+json"  # sellado para crear y archivar product ads
ESPERA_SEGUNDOS = 3

SALIR_OK = 0
SALIR_REVISAR = 1
SALIR_ABORTADO = 2

AUSENTE = "<ausente>"


def _argumentos(argv=None):
    interprete = argparse.ArgumentParser(prog="sonda_pausa_product_ad.py")
    interprete.add_argument(
        "--acepto-mutacion-real",
        action="store_true",
        help="sin esta bandera solo lee e imprime lo que mandaria",
    )
    return interprete.parse_args(argv)


def _errores_put(cuerpo):
    """Errores anidados de un PUT (mismo criterio que tools/reactiva_campanas.py)."""
    if not isinstance(cuerpo, dict):
        return [{"cuerpo_sin_forma": cuerpo}]
    errores = []
    for valor in cuerpo.values():
        if isinstance(valor, dict):
            errores.extend(valor.get("error") or [])
    return errores


def _rechazado(status, cuerpo):
    """No-2xx es falla; un 2xx con errores anidados (207) tambien."""
    return not 200 <= status <= 299 or bool(_errores_put(cuerpo))


def _lee_anuncio(cliente, perfil_id):
    cuerpo = cliente.list_objects(
        "/sp/productAds/list", {"adIdFilter": {"include": [AD_ID]}}, profile_id=perfil_id
    ).json()
    anuncios = cuerpo.get("productAds") or []
    return anuncios[0] if len(anuncios) == 1 else None


def _lee_campanas(cliente, perfil_id):
    return [
        c
        for c in listar_todo(cliente, "/sp/campaigns/list", profile_id=perfil_id)
        if c.get("name") == CAMPANA
    ]


def _pon_estado(cliente, perfil_id, estado):
    """PUT por fuera del guard read-only (app/ads/client.py no autoriza PUT)."""
    token = cliente._ensure_token()
    encabezados = cliente._build_headers(token, perfil_id, path=None, method="PUT")
    encabezados["Content-Type"] = VENDOR
    encabezados["Accept"] = VENDOR
    respuesta = cliente._client.request(
        "PUT",
        f"{cliente._base_url}/sp/productAds",
        headers=encabezados,
        json={"productAds": [{"adId": AD_ID, "state": estado}]},
    )
    try:
        cuerpo = respuesta.json()
    except ValueError:
        cuerpo = {"texto": respuesta.text[:300]}
    return respuesta.status_code, cuerpo


def _compara(antes, final):
    """Compara llave por llave; devuelve (iguales, diferencias)."""
    if not isinstance(antes, dict) or not isinstance(final, dict):
        return False, [{"llave": "<objeto>", "antes": antes, "final": final}]
    diferencias = []
    for llave in sorted(set(antes) | set(final)):
        valor_antes = antes.get(llave, AUSENTE)
        valor_final = final.get(llave, AUSENTE)
        if valor_antes != valor_final:
            diferencias.append({"llave": llave, "antes": valor_antes, "final": valor_final})
    return len(diferencias) == 0, diferencias


def _imprime(registro):
    print(scrub(json.dumps(registro, ensure_ascii=False, indent=1, default=str)))


def _abortado(registro, motivo):
    registro["iguales"] = None
    registro["resultado"] = f"ABORTADO sin escribir: {motivo}"
    _imprime(registro)
    return SALIR_ABORTADO


def _motivo_seguros(antes, campanas):
    if antes is None:
        return "el anuncio no se lee (cero o mas de uno)"
    if antes.get("state") != "ENABLED":
        return f"el anuncio esta {antes.get('state')}, no ENABLED"
    if len(campanas) != 1:
        return f"hay {len(campanas)} campanas con ese nombre, no exactamente una"
    if campanas[0].get("state") != "PAUSED":
        return f"la campana esta {campanas[0].get('state')}, no PAUSED"
    return "el anuncio no es de esa campana"


def _fase_regreso(cliente, perfil_id, registro, cuerpos, antes, original, esperado):
    """PUT de regreso solo si la lectura confirma el estado a revertir."""
    actual = _lee_anuncio(cliente, perfil_id)
    registro["lecturas"].append({"etapa": "previa_regreso", "anuncio": actual})
    if actual is None:
        registro["iguales"] = False
        registro["resultado"] = "REVISAR: no se pudo leer tras el cambio; sin regreso a ciegas"
        return SALIR_REVISAR
    estado = actual.get("state")
    if estado == original:
        iguales, diferencias = _compara(antes, actual)
        registro["iguales"] = iguales
        registro["diferencias"] = diferencias
        registro["resultado"] = (
            "REVISAR: el cambio no aplico (PUT rechazado o sin efecto), sin sellar; "
            "sin regreso porque la lectura confirma el estado original"
        )
        return SALIR_REVISAR
    if estado != esperado:
        registro["iguales"] = False
        registro["resultado"] = f"REVISAR: estado inesperado {estado}; sin regreso a ciegas"
        return SALIR_REVISAR
    status, cuerpo = _pon_estado(cliente, perfil_id, original)
    mal = _rechazado(status, cuerpo)
    registro["envios"].append(
        {
            "nombre": "regreso",
            "cuerpo": cuerpos["regreso"],
            "status": status,
            "respuesta": cuerpo,
            "rechazado": mal,
        }
    )
    time.sleep(ESPERA_SEGUNDOS)
    final = _lee_anuncio(cliente, perfil_id)
    registro["lecturas"].append({"etapa": "final", "anuncio": final})
    iguales, diferencias = _compara(antes, final)
    registro["iguales"] = iguales
    registro["diferencias"] = diferencias
    if iguales and not mal:
        registro["resultado"] = "OK: pauso y reactivo; la lectura final es igual a antes"
        return SALIR_OK
    registro["resultado"] = "REVISAR: el regreso fue rechazado o se aparto de antes; ver envios"
    return SALIR_REVISAR


def main(argv=None, cliente=None):
    """Punto de entrada; `cliente` existe solo para inyectar un falso en simulacion."""
    args = _argumentos(argv)
    registro = {
        "sonda": "pausa_product_ad",
        "platform": PLATFORM,
        "mutacion": args.acepto_mutacion_real,
        "ad_id": AD_ID,
        "envios": [],
        "lecturas": [],
    }
    if cliente is None:
        try:
            cliente = AdsClient(AdsCredentials.from_secrets_dir())
        except Exception as exc:
            return _abortado(registro, f"sin credenciales: {type(exc).__name__}: {exc}")
    try:
        perfiles = [p for p in perfiles_aceptados(cliente) if p.platform == PLATFORM]
        if len(perfiles) != 1:
            return _abortado(registro, f"sin perfil unico {PLATFORM}: hay {len(perfiles)}")
        perfil_id = perfiles[0].profile_id
        registro["perfil_id"] = perfil_id
        antes = _lee_anuncio(cliente, perfil_id)
        campanas = _lee_campanas(cliente, perfil_id)
        campana = campanas[0] if len(campanas) == 1 else None
        registro["campana"] = (
            {"campaignId": str(campana.get("campaignId")), "name": CAMPANA} if campana else None
        )
        registro["antes"] = {"anuncio": antes, "campana": campana}
        registro["lecturas"].append({"etapa": "antes", "anuncio": antes})
        seguro = (
            antes is not None
            and antes.get("state") == "ENABLED"
            and campana is not None
            and campana.get("state") == "PAUSED"
            and str(campana.get("campaignId")) == str(antes.get("campaignId"))
        )
        if not seguro:
            return _abortado(registro, _motivo_seguros(antes, campanas))
    except Exception as exc:
        return _abortado(registro, f"error al leer: {type(exc).__name__}: {exc}")
    cuerpos = {
        "cambio": {"productAds": [{"adId": AD_ID, "state": "PAUSED"}]},
        "regreso": {"productAds": [{"adId": AD_ID, "state": "ENABLED"}]},
    }
    registro["cuerpos_planeados"] = cuerpos
    if not args.acepto_mutacion_real:
        registro["iguales"] = None
        registro["resultado"] = (
            "OK: simulacro sin escritura; cuerpos_planeados trae lo que mandaria"
        )
        _imprime(registro)
        return SALIR_OK
    original, esperado = "ENABLED", "PAUSED"
    try:
        status, cuerpo = _pon_estado(cliente, perfil_id, esperado)
        registro["envios"].append(
            {
                "nombre": "cambio",
                "cuerpo": cuerpos["cambio"],
                "status": status,
                "respuesta": cuerpo,
                "rechazado": _rechazado(status, cuerpo),
            }
        )
        time.sleep(ESPERA_SEGUNDOS)
        tras_cambio = _lee_anuncio(cliente, perfil_id)
        registro["lecturas"].append({"etapa": "tras_cambio", "anuncio": tras_cambio})
    except Exception as exc:
        registro["error_cambio"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            salida = _fase_regreso(cliente, perfil_id, registro, cuerpos, antes, original, esperado)
        except Exception as exc:
            registro["error_regreso"] = f"{type(exc).__name__}: {exc}"
            registro["iguales"] = False
            registro["resultado"] = "REVISAR: el regreso lanzo excepcion; ver lecturas y envios"
            salida = SALIR_REVISAR
    _imprime(registro)
    return salida


if __name__ == "__main__":
    raise SystemExit(main())
