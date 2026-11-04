#!/usr/bin/env python3
"""Sonda 2 de V.0 (BIDS 02): subir 1 el presupuesto diario y regresarlo.

Que sella: el cuerpo que `PUT /sp/campaigns` acepta para `budget`, lo que
responde, si las demas llaves quedan iguales, y el presupuesto diario minimo
de cada pais (cambio 5: un PUT previo en 0.01; si Amazon lo rechaza, el cuerpo
literal nombra el minimo; si lo acepta, el minimo aceptado es 0.01).

Por que no afecta entregas: toca una sola campana PAUSED. En `amazon_us` es
`A1U - Auto Discovery - US` por nombre; con `--platform amazon_mx` elige por
lectura la PAUSED de `campaignId` menor con `budgetType` DAILY e imprime cual.
Se corre una vez por pais.

Seguros: aborta sin escribir (exit 2) si no hay exactamente una campana (o
ninguna candidata en MX), si no esta PAUSED, o si `budget.budgetType` no es
DAILY.

Contrato V.0: un solo JSON por `scrub` con cada cuerpo enviado, cada
respuesta, cada lectura, `iguales` (llave por llave de `antes` vs lectura
final) y `resultado`. Exit 0 con OK, 2 con ABORTADO, 1 con REVISAR. Sin la
bandera imprime `antes` y los cuerpos planeados y sale 0 sin escribir.

Regla CodeRabbit: una respuesta no-2xx es FALLA (un 207 con errores es
rechazo). Tras cualquier falla el `finally` lee el estado y solo manda el PUT
de regreso si la lectura confirma un presupuesto que esta sonda puso (0.01 o
antes+1); si confirma el original, no hay nada que revertir y no se repite
ningun PUT a ciegas. El `finally` regresa el presupuesto tanto si el 0.01 fue
rechazado como si fue aceptado.

Corre (una sonda a la vez, con el PR de codigo mergeado):

    ssh goncloud 'docker exec -i orbit-app-1 python -' < tools/sonda_presupuesto_campana.py
    ssh goncloud 'docker exec -i orbit-app-1 python - --acepto-mutacion-real' \\
      < tools/sonda_presupuesto_campana.py
"""

import argparse
import json
import re
import time
from decimal import Decimal

from app.ads.client import AdsClient, AdsCredentials
from app.ads.structure_api import listar_todo, perfiles_aceptados
from app.redaction import scrub

CAMPANA_US = "A1U - Auto Discovery - US"
VENDOR = "application/vnd.spcampaign.v3+json"  # precedente tools/reactiva_campanas.py
ESPERA_SEGUNDOS = 3
PRESUPUESTO_MINIMO_PRUEBA = 0.01

SALIR_OK = 0
SALIR_REVISAR = 1
SALIR_ABORTADO = 2

AUSENTE = "<ausente>"


def _argumentos(argv=None):
    interprete = argparse.ArgumentParser(prog="sonda_presupuesto_campana.py")
    interprete.add_argument(
        "--acepto-mutacion-real",
        action="store_true",
        help="sin esta bandera solo lee e imprime lo que mandaria",
    )
    interprete.add_argument(
        "--platform",
        choices=("amazon_us", "amazon_mx"),
        default="amazon_us",
        help="amazon_mx elige por lectura la PAUSED de campaignId menor con DAILY",
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


def _pon_presupuesto(cliente, perfil_id, campana_id, presupuesto):
    """PUT por fuera del guard read-only (app/ads/client.py no autoriza PUT)."""
    token = cliente._ensure_token()
    encabezados = cliente._build_headers(token, perfil_id, path=None, method="PUT")
    encabezados["Content-Type"] = VENDOR
    encabezados["Accept"] = VENDOR
    cuerpo_envio = {
        "campaigns": [
            {
                "campaignId": str(campana_id),
                "budget": {"budget": presupuesto, "budgetType": "DAILY"},
            }
        ]
    }
    respuesta = cliente._client.request(
        "PUT", f"{cliente._base_url}/sp/campaigns", headers=encabezados, json=cuerpo_envio
    )
    try:
        cuerpo = respuesta.json()
    except ValueError:
        cuerpo = {"texto": respuesta.text[:300]}
    return respuesta.status_code, cuerpo, cuerpo_envio


def _presupuesto_de(campana):
    return (campana.get("budget") or {}).get("budget") if isinstance(campana, dict) else None


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


def _numeros_en_rechazo(respuesta):
    """Numeros del rechazo; la fuente es el cuerpo literal, R02 confirma el minimo."""
    texto = json.dumps(respuesta, ensure_ascii=False, default=str)
    return list(dict.fromkeys(re.findall(r"\d+\.\d+|\d+", texto)))[:10]


def _imprime(registro):
    print(scrub(json.dumps(registro, ensure_ascii=False, indent=1, default=str)))


def _abortado(registro, motivo):
    registro["iguales"] = None
    registro["resultado"] = f"ABORTADO sin escribir: {motivo}"
    _imprime(registro)
    return SALIR_ABORTADO


def _elige_campana(cliente, perfil_id, platform):
    """Devuelve (campana, elegida_por) o (None, motivo)."""
    todas = listar_todo(cliente, "/sp/campaigns/list", profile_id=perfil_id)
    if platform == "amazon_mx":
        candidatas = [
            c
            for c in todas
            if c.get("state") == "PAUSED" and (c.get("budget") or {}).get("budgetType") == "DAILY"
        ]
        if not candidatas:
            return None, "sin candidatas PAUSED con DAILY en amazon_mx"
        elegida = min(candidatas, key=lambda c: int(str(c.get("campaignId"))))
        return elegida, "menor campaignId entre PAUSED con DAILY"
    matches = [c for c in todas if c.get("name") == CAMPANA_US]
    if len(matches) != 1:
        return None, f"hay {len(matches)} campanas {CAMPANA_US}, no exactamente una"
    return matches[0], "nombre fijo"


def _cambio_sellado(registro, presupuesto_nuevo):
    """El OK exige el cambio sellado, no solo el regreso (B1): sin
    error_cambio, un envio 'cambio' no rechazado y tras_cambio mostrando
    presupuesto_nuevo."""
    if registro.get("error_cambio"):
        return False
    cambios = [e for e in registro.get("envios", []) if e.get("nombre") == "cambio"]
    if len(cambios) != 1 or cambios[0].get("rechazado"):
        return False
    lecturas = [
        lec.get("campana")
        for lec in registro.get("lecturas", [])
        if lec.get("etapa") == "tras_cambio"
    ]
    return len(lecturas) == 1 and _presupuesto_de(lecturas[0]) == presupuesto_nuevo


def _fase_regreso(
    cliente, perfil_id, registro, antes, presupuesto_antes, puestos, presupuesto_nuevo
):
    """Regresa el presupuesto solo si la lectura confirma un valor puesto aqui."""
    actual = _lee_unica(cliente, perfil_id, registro)
    registro["lecturas"].append({"etapa": "previa_regreso", "campana": actual})
    if not isinstance(actual, dict):
        registro["iguales"] = False
        registro["resultado"] = "REVISAR: no se pudo leer tras el cambio; sin regreso a ciegas"
        return SALIR_REVISAR
    valor = _presupuesto_de(actual)
    if valor == presupuesto_antes:
        iguales, diferencias = _compara(antes, actual)
        registro["iguales"] = iguales
        registro["diferencias"] = diferencias
        registro["demas_llaves_iguales"] = all(d["llave"] == "budget" for d in diferencias)
        registro["resultado"] = (
            "REVISAR: el cambio no aplico (PUT rechazado o sin efecto), sin sellar; "
            "sin regreso porque la lectura confirma el presupuesto original"
        )
        return SALIR_REVISAR
    if valor not in puestos:
        registro["iguales"] = False
        registro["resultado"] = f"REVISAR: presupuesto inesperado {valor}; sin regreso a ciegas"
        return SALIR_REVISAR
    campana_id = antes.get("campaignId")
    status, cuerpo, envio = _pon_presupuesto(cliente, perfil_id, campana_id, presupuesto_antes)
    mal = _rechazado(status, cuerpo)
    registro["envios"].append(
        {
            "nombre": "regreso",
            "cuerpo": envio,
            "status": status,
            "respuesta": cuerpo,
            "rechazado": mal,
        }
    )
    time.sleep(ESPERA_SEGUNDOS)
    final = _lee_unica(cliente, perfil_id, registro)
    registro["lecturas"].append({"etapa": "final", "campana": final})
    iguales, diferencias = _compara(antes, final)
    registro["iguales"] = iguales
    registro["diferencias"] = diferencias
    registro["demas_llaves_iguales"] = all(d["llave"] == "budget" for d in diferencias)
    cambio_ok = _cambio_sellado(registro, presupuesto_nuevo)
    if iguales and not mal and not registro.get("minimo_sin_sellar") and cambio_ok:
        registro["resultado"] = "OK: subio 1 y regreso; la lectura final es igual a antes"
        return SALIR_OK
    if iguales and registro.get("minimo_sin_sellar"):
        registro["resultado"] = (
            "REVISAR: status y lectura del minimo discrepan; revertido y verificado, sin sellar"
        )
        return SALIR_REVISAR
    if iguales and not cambio_ok:
        registro["resultado"] = (
            "REVISAR: el cambio no quedo sellado; revertido y verificado, sin sellar"
        )
        return SALIR_REVISAR
    registro["resultado"] = "REVISAR: el regreso fue rechazado o se aparto de antes; ver envios"
    return SALIR_REVISAR


def _lee_unica(cliente, perfil_id, registro):
    """Relee la campana PINNEADA al ID elegido al inicio (filtrado local sobre
    el mismo listar_todo; ausente = None y el llamador cierra el camino)."""
    pin = str((registro.get("campana") or {}).get("campaignId"))
    for campana in listar_todo(cliente, "/sp/campaigns/list", profile_id=perfil_id):
        if str(campana.get("campaignId")) == pin:
            return campana
    return None


def main(argv=None, cliente=None):
    """Punto de entrada; `cliente` existe solo para inyectar un falso en simulacion."""
    args = _argumentos(argv)
    registro = {
        "sonda": "presupuesto_campana",
        "platform": args.platform,
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
        perfiles = [p for p in perfiles_aceptados(cliente) if p.platform == args.platform]
        if len(perfiles) != 1:
            return _abortado(registro, f"sin perfil unico {args.platform}: hay {len(perfiles)}")
        perfil_id = perfiles[0].profile_id
        registro["perfil_id"] = perfil_id
        antes, elegida_por = _elige_campana(cliente, perfil_id, args.platform)
        if antes is None:
            return _abortado(registro, elegida_por)
        registro["campana"] = {
            "campaignId": str(antes.get("campaignId")),
            "name": antes.get("name"),
        }
        registro["campana_elegida_por"] = elegida_por
        registro["antes"] = antes
        registro["lecturas"].append({"etapa": "antes", "campana": antes})
        if antes.get("state") != "PAUSED":
            return _abortado(registro, f"la campana esta {antes.get('state')}, no PAUSED")
        tipo = (antes.get("budget") or {}).get("budgetType")
        if tipo != "DAILY":
            return _abortado(registro, f"budgetType es {tipo}, no DAILY")
        presupuesto_antes = _presupuesto_de(antes)
        if not isinstance(presupuesto_antes, (int, float)) or isinstance(presupuesto_antes, bool):
            return _abortado(registro, f"presupuesto ilegible: {presupuesto_antes!r}")
    except Exception as exc:
        return _abortado(registro, f"error al leer: {type(exc).__name__}: {exc}")
    campana_id = antes.get("campaignId")
    presupuesto_nuevo = float(Decimal(str(presupuesto_antes)) + 1)

    def _cuerpo(valor):
        return {
            "campaigns": [
                {
                    "campaignId": str(campana_id),
                    "budget": {"budget": valor, "budgetType": "DAILY"},
                }
            ]
        }

    registro["cuerpos_planeados"] = {
        "minimo": _cuerpo(PRESUPUESTO_MINIMO_PRUEBA),
        "cambio": _cuerpo(presupuesto_nuevo),
        "regreso": _cuerpo(presupuesto_antes),
    }
    if not args.acepto_mutacion_real:
        registro["iguales"] = None
        registro["resultado"] = (
            "OK: simulacro sin escritura; cuerpos_planeados trae lo que mandaria"
        )
        _imprime(registro)
        return SALIR_OK
    puestos = []
    try:
        status, cuerpo, envio = _pon_presupuesto(
            cliente, perfil_id, campana_id, PRESUPUESTO_MINIMO_PRUEBA
        )
        puestos.append(PRESUPUESTO_MINIMO_PRUEBA)
        mal_minimo = _rechazado(status, cuerpo)
        registro["envios"].append(
            {
                "nombre": "minimo",
                "cuerpo": envio,
                "status": status,
                "respuesta": cuerpo,
                "rechazado": mal_minimo,
            }
        )
        time.sleep(ESPERA_SEGUNDOS)
        tras_minimo = _lee_unica(cliente, perfil_id, registro)
        registro["lecturas"].append({"etapa": "tras_minimo", "campana": tras_minimo})
        minimo_aplico = _presupuesto_de(tras_minimo) == PRESUPUESTO_MINIMO_PRUEBA
        if minimo_aplico and not mal_minimo:
            registro["minimo"] = {"aceptado": True, "minimo_aceptado": PRESUPUESTO_MINIMO_PRUEBA}
        elif mal_minimo and not minimo_aplico:
            registro["minimo"] = {
                "aceptado": False,
                "rechazo_literal": cuerpo,
                "numeros_en_rechazo": _numeros_en_rechazo(cuerpo),
            }
        else:
            registro["minimo"] = {
                "aceptado": minimo_aplico,
                "status_rechazado": mal_minimo,
                "valor_leido": _presupuesto_de(tras_minimo),
            }
            registro["minimo_sin_sellar"] = True
        status, cuerpo, envio = _pon_presupuesto(cliente, perfil_id, campana_id, presupuesto_nuevo)
        puestos.append(presupuesto_nuevo)
        registro["envios"].append(
            {
                "nombre": "cambio",
                "cuerpo": envio,
                "status": status,
                "respuesta": cuerpo,
                "rechazado": _rechazado(status, cuerpo),
            }
        )
        time.sleep(ESPERA_SEGUNDOS)
        tras_cambio = _lee_unica(cliente, perfil_id, registro)
        registro["lecturas"].append({"etapa": "tras_cambio", "campana": tras_cambio})
    except Exception as exc:
        registro["error_cambio"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            salida = _fase_regreso(
                cliente, perfil_id, registro, antes, presupuesto_antes, puestos, presupuesto_nuevo
            )
        except Exception as exc:
            registro["error_regreso"] = f"{type(exc).__name__}: {exc}"
            registro["iguales"] = False
            registro["resultado"] = "REVISAR: el regreso lanzo excepcion; ver lecturas y envios"
            salida = SALIR_REVISAR
    _imprime(registro)
    return salida


if __name__ == "__main__":
    raise SystemExit(main())
