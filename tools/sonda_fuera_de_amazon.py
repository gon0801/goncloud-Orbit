#!/usr/bin/env python3
"""Sonda 4 de V.0 (BIDS 02): probar valores de gasto fuera de Amazon.

Que sella: que valores acepta `PUT /sp/campaigns` para
`offAmazonSettings.offAmazonBudgetControlStrategy` (nombre del campo segun la
pagina `sponsored-products/3-0/openapi/prod` de la documentacion de Amazon
Ads). Prueba a lo mas dos candidatos, en orden: MINIMIZE_SPEND y despues
MAXIMIZE_REACH. Con un rechazo guarda literal el cuerpo (ahi lista Amazon lo
que acepta) y prueba el siguiente una vez.

Por que no afecta entregas: toca una sola campana PAUSED
(`A1U - Auto Discovery - US`).

Seguros: aborta sin escribir (exit 2) si no hay exactamente una campana con
ese nombre o si no esta PAUSED.

Contrato V.0: un solo JSON por `scrub` con cada cuerpo enviado, cada
respuesta, cada lectura, `iguales` (llave por llave de `antes` vs lectura
final) y `resultado`. Exit 0 con OK, 2 con ABORTADO, 1 con REVISAR. Sin la
bandera imprime el `offAmazonSettings` de hoy y los dos cuerpos, y sale 0 sin
escribir.

Regla CodeRabbit: una respuesta no-2xx es FALLA (un 207 con errores es
rechazo). Tras cualquier falla se lee el estado: el siguiente candidato solo
se prueba si la lectura confirma que nada cambio, y el PUT de regreso solo se
manda si la lectura confirma el valor que se cree revertir. Jamas se repite un
PUT a ciegas.

Excepcion autorizada: si Amazon no deja regresar a `{}`, queda el valor que
minimiza el gasto (MINIMIZE_SPEND), se anota en `desviacion` y el resultado es
OK con desviacion para reportar bajo Desviaciones. La campana esta pausada,
asi que ese valor no cambia ninguna entrega.

Corre (una sonda a la vez, con el PR de codigo mergeado):

    ssh goncloud 'docker exec -i orbit-app-1 python -' < tools/sonda_fuera_de_amazon.py
    ssh goncloud 'docker exec -i orbit-app-1 python - --acepto-mutacion-real' \\
      < tools/sonda_fuera_de_amazon.py
"""

import argparse
import json
import time

from app.ads.client import AdsClient, AdsCredentials
from app.ads.structure_api import listar_todo, perfiles_aceptados
from app.redaction import scrub

CAMPANA = "A1U - Auto Discovery - US"
PLATFORM = "amazon_us"
VENDOR = "application/vnd.spcampaign.v3+json"  # precedente tools/reactiva_campanas.py
ESPERA_SEGUNDOS = 3
CAMPO = "offAmazonBudgetControlStrategy"
CANDIDATOS = ("MINIMIZE_SPEND", "MAXIMIZE_REACH")
MINIMIZA_GASTO = "MINIMIZE_SPEND"

SALIR_OK = 0
SALIR_REVISAR = 1
SALIR_ABORTADO = 2

AUSENTE = "<ausente>"


def _argumentos(argv=None):
    interprete = argparse.ArgumentParser(prog="sonda_fuera_de_amazon.py")
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


def _lee_campana(cliente, perfil_id):
    matches = [
        c
        for c in listar_todo(cliente, "/sp/campaigns/list", profile_id=perfil_id)
        if c.get("name") == CAMPANA
    ]
    return matches[0] if len(matches) == 1 else None


def _pon_off(cliente, perfil_id, campana_id, off):
    """PUT por fuera del guard read-only (app/ads/client.py no autoriza PUT)."""
    token = cliente._ensure_token()
    encabezados = cliente._build_headers(token, perfil_id, path=None, method="PUT")
    encabezados["Content-Type"] = VENDOR
    encabezados["Accept"] = VENDOR
    cuerpo_envio = {"campaigns": [{"campaignId": str(campana_id), "offAmazonSettings": off}]}
    respuesta = cliente._client.request(
        "PUT", f"{cliente._base_url}/sp/campaigns", headers=encabezados, json=cuerpo_envio
    )
    try:
        cuerpo = respuesta.json()
    except ValueError:
        cuerpo = {"texto": respuesta.text[:300]}
    return respuesta.status_code, cuerpo, cuerpo_envio


def _off_de(campana):
    return (campana or {}).get("offAmazonSettings") or {}


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


def _envia(cliente, perfil_id, registro, nombre, campana_id, off):
    status, cuerpo, envio = _pon_off(cliente, perfil_id, campana_id, off)
    mal = _rechazado(status, cuerpo)
    registro["envios"].append(
        {"nombre": nombre, "cuerpo": envio, "status": status, "respuesta": cuerpo, "rechazado": mal}
    )
    time.sleep(ESPERA_SEGUNDOS)
    lectura = _lee_campana(cliente, perfil_id)
    registro["lecturas"].append({"etapa": f"tras_{nombre}", "campana": lectura})
    return mal, lectura


def _deja_minimo(cliente, perfil_id, registro, antes, off_antes):
    """Excepcion autorizada: no se pudo volver a {}; queda MINIMIZE_SPEND."""
    actual = _lee_campana(cliente, perfil_id)
    registro["lecturas"].append({"etapa": "previa_desviacion", "campana": actual})
    if _off_de(actual) != {CAMPO: MINIMIZA_GASTO}:
        mal, actual = _envia(
            cliente,
            perfil_id,
            registro,
            "minimiza",
            antes.get("campaignId"),
            {CAMPO: MINIMIZA_GASTO},
        )
        if mal or _off_de(actual) != {CAMPO: MINIMIZA_GASTO}:
            registro["iguales"] = False
            registro["resultado"] = (
                "REVISAR: ni el regreso a {} ni MINIMIZE_SPEND quedaron; ver lecturas"
            )
            return SALIR_REVISAR
    iguales, diferencias = _compara(antes, actual)
    registro["iguales"] = iguales
    registro["diferencias"] = diferencias
    registro["desviacion"] = {
        "off_antes": off_antes,
        "off_final": _off_de(actual),
        "motivo": "Amazon no dejo regresar a {}; queda el valor que minimiza el gasto",
    }
    registro["resultado"] = (
        "OK con desviacion: queda MINIMIZE_SPEND porque Amazon no dejo regresar a {}; "
        "reportar bajo Desviaciones (la campana esta pausada: no cambia entregas)"
    )
    return SALIR_OK


def _fase_regreso(cliente, perfil_id, registro, antes, off_antes, aceptado):
    """Regresa a lo leido; si {} no vuelve, aplica la excepcion autorizada."""
    actual = _lee_campana(cliente, perfil_id)
    registro["lecturas"].append({"etapa": "previa_regreso", "campana": actual})
    if not isinstance(actual, dict):
        registro["iguales"] = False
        registro["resultado"] = "REVISAR: no se pudo leer tras el cambio; sin regreso a ciegas"
        return SALIR_REVISAR
    off_actual = _off_de(actual)
    if off_actual == off_antes:
        iguales, diferencias = _compara(antes, actual)
        registro["iguales"] = iguales
        registro["diferencias"] = diferencias
        if aceptado is None:
            registro["resultado"] = (
                "REVISAR: ambos candidatos rechazados, sin sellar; la entidad quedo igual"
            )
        else:
            registro["resultado"] = (
                "REVISAR: el PUT aceptado no aplico; sin regreso porque la lectura "
                "confirma lo de antes"
            )
        return SALIR_REVISAR
    if aceptado is None or off_actual != {CAMPO: aceptado}:
        registro["iguales"] = False
        registro["resultado"] = "REVISAR: valor inesperado fuera de Amazon; sin regreso a ciegas"
        return SALIR_REVISAR
    mal, final = _envia(cliente, perfil_id, registro, "regreso", antes.get("campaignId"), off_antes)
    if mal or _off_de(final) != off_antes:
        if off_antes == {}:
            return _deja_minimo(cliente, perfil_id, registro, antes, off_antes)
        registro["iguales"] = False
        registro["resultado"] = (
            "REVISAR: el regreso fue rechazado o no aplico; ver envios y lecturas"
        )
        return SALIR_REVISAR
    iguales, diferencias = _compara(antes, final)
    registro["iguales"] = iguales
    registro["diferencias"] = diferencias
    if iguales and not registro.get("aceptado_pese_a_rechazo"):
        registro["resultado"] = f"OK: Amazon acepto {aceptado}; la lectura final es igual a antes"
        return SALIR_OK
    if iguales:
        registro["resultado"] = (
            "REVISAR: el PUT reporto falla pero aplico; revertido y verificado, sin sellar"
        )
        return SALIR_REVISAR
    registro["resultado"] = "REVISAR: el regreso se aparto de antes en otras llaves; ver envios"
    return SALIR_REVISAR


def main(argv=None, cliente=None):
    """Punto de entrada; `cliente` existe solo para inyectar un falso en simulacion."""
    args = _argumentos(argv)
    registro = {
        "sonda": "fuera_de_amazon",
        "platform": PLATFORM,
        "mutacion": args.acepto_mutacion_real,
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
        antes = _lee_campana(cliente, perfil_id)
        if antes is None:
            return _abortado(registro, f"sin exactamente una campana {CAMPANA}")
        registro["campana"] = {
            "campaignId": str(antes.get("campaignId")),
            "name": antes.get("name"),
        }
        registro["antes"] = antes
        registro["lecturas"].append({"etapa": "antes", "campana": antes})
        if antes.get("state") != "PAUSED":
            return _abortado(registro, f"la campana esta {antes.get('state')}, no PAUSED")
    except Exception as exc:
        return _abortado(registro, f"error al leer: {type(exc).__name__}: {exc}")
    campana_id = antes.get("campaignId")
    off_antes = _off_de(antes)
    registro["off_hoy"] = off_antes
    registro["cuerpos_planeados"] = [
        {
            "campaigns": [
                {
                    "campaignId": str(campana_id),
                    "offAmazonSettings": {CAMPO: candidato},
                }
            ]
        }
        for candidato in CANDIDATOS
    ]
    if not args.acepto_mutacion_real:
        registro["iguales"] = None
        registro["resultado"] = "OK: simulacro sin escritura; cuerpos_planeados trae los dos"
        _imprime(registro)
        return SALIR_OK
    aceptado = None
    try:
        for candidato in CANDIDATOS:
            mal, lectura = _envia(
                cliente,
                perfil_id,
                registro,
                f"candidato_{candidato.lower()}",
                campana_id,
                {CAMPO: candidato},
            )
            off_leido = _off_de(lectura)
            if off_leido == {CAMPO: candidato}:
                aceptado = candidato
                registro["aceptado"] = candidato
                if mal:
                    registro["aceptado_pese_a_rechazo"] = True
                break
            registro.setdefault("rechazos", []).append(
                {"candidato": candidato, "rechazo_literal": registro["envios"][-1]["respuesta"]}
            )
            if off_leido != off_antes:
                registro["lectura_inesperada"] = off_leido
                break
    except Exception as exc:
        registro["error_cambio"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            salida = _fase_regreso(cliente, perfil_id, registro, antes, off_antes, aceptado)
        except Exception as exc:
            registro["error_regreso"] = f"{type(exc).__name__}: {exc}"
            registro["iguales"] = False
            registro["resultado"] = "REVISAR: el regreso lanzo excepcion; ver lecturas y envios"
            salida = SALIR_REVISAR
    _imprime(registro)
    return salida


if __name__ == "__main__":
    raise SystemExit(main())
