#!/usr/bin/env python3
"""Sonda 3 de V.0 (BIDS 02): cambiar un ajuste por placement y regresarlo.

Que sella: el cuerpo que `PUT /sp/campaigns` acepta para `dynamicBidding`
(strategy leida + lista parcial de `placementBidding`), lo que responde, y si
una lista parcial reemplaza la entera o se mezcla. En vivo solo se vieron
PLACEMENT_TOP y PLACEMENT_PRODUCT_PAGE: elige el primero si la campana no lo
tiene, y si lo tiene, el segundo. Manda ese placement con su porcentaje mas 1.

Si `antes` traia otra entrada, la lectura dice si sigue (ahi se responde si la
lista parcial reemplaza). Si no traia otra, manda un segundo PUT con solo el
otro placement en 1 y lee si el primero sigue. Regresa con la lista de
`antes`; lo que sobre lo manda en 0 y anota si el 0 borra la entrada.

Por que no afecta entregas: toca una sola campana PAUSED
(`A1U - Auto Discovery - US`).

Seguros: aborta sin escribir (exit 2) si no hay exactamente una campana con
ese nombre, si no esta PAUSED, o si la campana no trae `strategy`.

Contrato V.0: un solo JSON por `scrub` con cada cuerpo enviado, cada
respuesta, cada lectura, `iguales` (llave por llave de `antes` vs lectura
final; una entrada con 0 % cuenta como ausente, y el orden no importa) y
`resultado`. Exit 0 con OK, 2 con ABORTADO, 1 con REVISAR. Sin la bandera
imprime `antes` y los cuerpos planeados y sale 0 sin escribir.

Regla CodeRabbit: una respuesta no-2xx es FALLA (un 207 con errores es
rechazo). Tras cualquier falla el `finally` lee el estado y solo manda el PUT
de regreso si la lectura confirma lo que esta sonda puso; si confirma lo de
`antes`, no hay nada que revertir y no se repite ningun PUT a ciegas.

Corre (una sonda a la vez, con el PR de codigo mergeado):

    ssh goncloud 'docker exec -i orbit-app-1 python -' < tools/sonda_ajuste_placement.py
    ssh goncloud 'docker exec -i orbit-app-1 python - --acepto-mutacion-real' \\
      < tools/sonda_ajuste_placement.py
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
PRIMERO = "PLACEMENT_TOP"
SEGUNDO = "PLACEMENT_PRODUCT_PAGE"

SALIR_OK = 0
SALIR_REVISAR = 1
SALIR_ABORTADO = 2

AUSENTE = "<ausente>"


def _argumentos(argv=None):
    interprete = argparse.ArgumentParser(prog="sonda_ajuste_placement.py")
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


def _lee_campana(cliente, perfil_id, campana_id=None):
    """Lista igual que siempre; con pin elige por ID (filtrado local, sin
    campaignIdFilter), sin pin elige por nombre (solo la lectura inicial)."""
    todas = listar_todo(cliente, "/sp/campaigns/list", profile_id=perfil_id)
    if campana_id is not None:
        for campana in todas:
            if str(campana.get("campaignId")) == str(campana_id):
                return campana
        return None
    matches = [c for c in todas if c.get("name") == CAMPANA]
    return matches[0] if len(matches) == 1 else None


def _pon_ajustes(cliente, perfil_id, campana_id, strategy, lista):
    """PUT por fuera del guard read-only (app/ads/client.py no autoriza PUT)."""
    token = cliente._ensure_token()
    encabezados = cliente._build_headers(token, perfil_id, path=None, method="PUT")
    encabezados["Content-Type"] = VENDOR
    encabezados["Accept"] = VENDOR
    cuerpo_envio = {
        "campaigns": [
            {
                "campaignId": str(campana_id),
                "dynamicBidding": {"strategy": strategy, "placementBidding": lista},
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


def _lista_de(campana):
    db = (campana or {}).get("dynamicBidding") or {}
    lista = db.get("placementBidding") or []
    return lista if isinstance(lista, list) else []


def _porcentaje(lista, placement):
    for entrada in lista:
        if isinstance(entrada, dict) and entrada.get("placement") == placement:
            return entrada.get("percentage")
    return None


def _normaliza_db(db):
    """Copia donde el 0 % cuenta como ausente y el orden no importa."""
    if not isinstance(db, dict):
        return db
    lista = db.get("placementBidding") or []
    filtrada = sorted(
        (e for e in lista if not (isinstance(e, dict) and e.get("percentage") == 0)),
        key=lambda e: str(e.get("placement") if isinstance(e, dict) else e),
    )
    copia = dict(db)
    copia["placementBidding"] = filtrada
    return copia


def _normaliza_campana(campana):
    copia = dict(campana)
    copia["dynamicBidding"] = _normaliza_db(campana.get("dynamicBidding"))
    return copia


def _compara(antes, final):
    """Compara llave por llave sobre la vista normalizada."""
    if not isinstance(antes, dict) or not isinstance(final, dict):
        return False, [{"llave": "<objeto>", "antes": antes, "final": final}]
    vista_antes = _normaliza_campana(antes)
    vista_final = _normaliza_campana(final)
    diferencias = []
    for llave in sorted(set(vista_antes) | set(vista_final)):
        valor_antes = vista_antes.get(llave, AUSENTE)
        valor_final = vista_final.get(llave, AUSENTE)
        if valor_antes != valor_final:
            diferencias.append({"llave": llave, "antes": valor_antes, "final": valor_final})
    return len(diferencias) == 0, diferencias


def _mezcla(lista, placement, percentage):
    """Lo que quedaria si Amazon mezcla: la lista con esa entrada puesta."""
    nueva = [e for e in lista if not (isinstance(e, dict) and e.get("placement") == placement)]
    nueva.append({"placement": placement, "percentage": percentage})
    return nueva


def _veredicto_parcial(
    registro, tras, placement, nombres, clave_sigue, clave_extra=None, *, cero_ausente
):
    """Sella lista_parcial_reemplaza SOLO si los PUTs nombrados pasaron y la
    lectura es dict (consulta envios[].rechazado); con rechazo o lectura
    fallida registra "sin sellar"/"lectura_fallida" y fuerza REVISAR."""
    rechazado = any(e.get("nombre") in nombres and e.get("rechazado") for e in registro["envios"])
    if not isinstance(tras, dict):
        motivo = "lectura_fallida"
    elif rechazado:
        motivo = "sin sellar"
    else:
        motivo = None
    if motivo is not None:
        registro["lista_parcial_reemplaza"] = motivo
        registro["lista_parcial_sin_sellar"] = True
        return
    if cero_ausente:
        sigue = _porcentaje(_lista_de(tras), placement) not in (None, 0)
    else:
        sigue = _porcentaje(_lista_de(tras), placement) is not None
    registro[clave_sigue] = sigue
    if clave_extra is not None:
        registro[clave_extra] = placement
    registro["lista_parcial_reemplaza"] = not sigue


def _imprime(registro):
    print(scrub(json.dumps(registro, ensure_ascii=False, indent=1, default=str)))


def _abortado(registro, motivo):
    registro["iguales"] = None
    registro["resultado"] = f"ABORTADO sin escribir: {motivo}"
    _imprime(registro)
    return SALIR_ABORTADO


def _fase_regreso(cliente, perfil_id, registro, contexto):
    """Regresa la lista de `antes`, en 0 lo que sobre, solo si aplica."""
    antes = contexto["antes"]
    lista_antes = contexto["lista_antes"]
    actual = _lee_campana(cliente, perfil_id, antes.get("campaignId"))
    registro["lecturas"].append({"etapa": "previa_regreso", "campana": actual})
    if not isinstance(actual, dict):
        registro["iguales"] = False
        registro["resultado"] = "REVISAR: no se pudo leer tras el cambio; sin regreso a ciegas"
        return SALIR_REVISAR
    db_actual = _normaliza_db(actual.get("dynamicBidding") or {})
    db_antes = _normaliza_db(antes.get("dynamicBidding") or {})
    if db_actual == db_antes:
        iguales, diferencias = _compara(antes, actual)
        registro["iguales"] = iguales
        registro["diferencias"] = diferencias
        registro["resultado"] = (
            "REVISAR: el cambio no aplico (PUT rechazado o sin efecto), sin sellar; "
            "sin regreso porque la lectura confirma lo de antes"
        )
        return SALIR_REVISAR
    if db_actual not in contexto["confirmables"]:
        registro["iguales"] = False
        registro["resultado"] = "REVISAR: ajustes inesperados; sin regreso a ciegas"
        return SALIR_REVISAR
    status, cuerpo, envio = _pon_ajustes(
        cliente, perfil_id, antes.get("campaignId"), contexto["strategy"], lista_antes
    )
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
    final = _lee_campana(cliente, perfil_id, antes.get("campaignId"))
    registro["lecturas"].append({"etapa": "tras_regreso", "campana": final})
    if isinstance(final, dict) and not mal:
        sobrantes = [
            e.get("placement")
            for e in _lista_de(final)
            if isinstance(e, dict)
            and e.get("percentage") != 0
            and _porcentaje(lista_antes, e.get("placement")) is None
        ]
        if sobrantes:
            lista_cero = list(lista_antes) + [
                {"placement": nombre, "percentage": 0} for nombre in sobrantes
            ]
            status0, cuerpo0, envio0 = _pon_ajustes(
                cliente, perfil_id, antes.get("campaignId"), contexto["strategy"], lista_cero
            )
            registro["envios"].append(
                {
                    "nombre": "cero",
                    "cuerpo": envio0,
                    "status": status0,
                    "respuesta": cuerpo0,
                    "rechazado": _rechazado(status0, cuerpo0),
                }
            )
            time.sleep(ESPERA_SEGUNDOS)
            final = _lee_campana(cliente, perfil_id, antes.get("campaignId"))
            registro["lecturas"].append({"etapa": "tras_cero", "campana": final})
            lista_final = _lista_de(final) if isinstance(final, dict) else []
            registro["cero_borra"] = all(
                _porcentaje(lista_final, nombre) is None for nombre in sobrantes
            )
            registro["cero_sobrantes"] = sobrantes
    iguales, diferencias = _compara(antes, final)
    registro["iguales"] = iguales
    registro["diferencias"] = diferencias
    if iguales and not mal and not registro.get("lista_parcial_sin_sellar"):
        registro["resultado"] = "OK: cambio y regreso; la lectura final es igual a antes"
        return SALIR_OK
    if iguales and registro.get("lista_parcial_sin_sellar"):
        registro["resultado"] = (
            "REVISAR: la lista parcial no se sello; revertido y verificado, sin sellar"
        )
        return SALIR_REVISAR
    registro["resultado"] = "REVISAR: el regreso fue rechazado o se aparto de antes; ver envios"
    return SALIR_REVISAR


def main(argv=None, cliente=None):
    """Punto de entrada; `cliente` existe solo para inyectar un falso en simulacion."""
    args = _argumentos(argv)
    registro = {
        "sonda": "ajuste_placement",
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
        db_antes = antes.get("dynamicBidding") or {}
        strategy = db_antes.get("strategy")
        if not strategy:
            return _abortado(registro, "la campana no trae strategy")
        lista_antes = _lista_de(antes)
    except Exception as exc:
        return _abortado(registro, f"error al leer: {type(exc).__name__}: {exc}")
    tiene_primero = _porcentaje(lista_antes, PRIMERO) not in (None, 0)
    objetivo = SEGUNDO if tiene_primero else PRIMERO
    otro = PRIMERO if objetivo == SEGUNDO else SEGUNDO
    porcentaje = _porcentaje(lista_antes, objetivo)
    if porcentaje in (None, 0):
        porcentaje = 0
    elif not isinstance(porcentaje, (int, float)) or isinstance(porcentaje, bool):
        return _abortado(registro, f"porcentaje ilegible para {objetivo}: {porcentaje!r}")
    objetivo_mas_uno = porcentaje + 1
    otras = [e for e in lista_antes if isinstance(e, dict) and e.get("placement") != objetivo]
    registro["objetivo"] = {"placement": objetivo, "de": porcentaje, "a": objetivo_mas_uno}

    def _cuerpo(lista):
        return {
            "campaigns": [
                {
                    "campaignId": str(antes.get("campaignId")),
                    "dynamicBidding": {"strategy": strategy, "placementBidding": lista},
                }
            ]
        }

    registro["cuerpos_planeados"] = {
        "cambio": _cuerpo([{"placement": objetivo, "percentage": objetivo_mas_uno}]),
        "put2_si_antes_sin_otra": _cuerpo([{"placement": otro, "percentage": 1}]),
        "regreso": _cuerpo(lista_antes),
    }
    if not args.acepto_mutacion_real:
        registro["iguales"] = None
        registro["resultado"] = (
            "OK: simulacro sin escritura; cuerpos_planeados trae lo que mandaria"
        )
        _imprime(registro)
        return SALIR_OK
    contexto = {
        "antes": antes,
        "lista_antes": lista_antes,
        "strategy": strategy,
        "confirmables": [
            _normaliza_db(
                {
                    "strategy": strategy,
                    "placementBidding": [{"placement": objetivo, "percentage": objetivo_mas_uno}],
                }
            ),
            _normaliza_db(
                {
                    "strategy": strategy,
                    "placementBidding": _mezcla(lista_antes, objetivo, objetivo_mas_uno),
                }
            ),
        ],
    }
    campana_id = antes.get("campaignId")
    try:
        status, cuerpo, envio = _pon_ajustes(
            cliente,
            perfil_id,
            campana_id,
            strategy,
            [{"placement": objetivo, "percentage": objetivo_mas_uno}],
        )
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
        tras_cambio = _lee_campana(cliente, perfil_id, campana_id)
        registro["lecturas"].append({"etapa": "tras_cambio", "campana": tras_cambio})
        if isinstance(tras_cambio, dict):
            contexto["confirmables"].append(_normaliza_db(tras_cambio.get("dynamicBidding") or {}))
        if otras:
            _veredicto_parcial(
                registro,
                tras_cambio,
                otras[0].get("placement"),
                ("cambio",),
                "otra_entrada_sigue",
                "otra_entrada",
                cero_ausente=True,
            )
        else:
            status2, cuerpo2, envio2 = _pon_ajustes(
                cliente, perfil_id, campana_id, strategy, [{"placement": otro, "percentage": 1}]
            )
            registro["envios"].append(
                {
                    "nombre": "put2",
                    "cuerpo": envio2,
                    "status": status2,
                    "respuesta": cuerpo2,
                    "rechazado": _rechazado(status2, cuerpo2),
                }
            )
            time.sleep(ESPERA_SEGUNDOS)
            tras_put2 = _lee_campana(cliente, perfil_id, campana_id)
            registro["lecturas"].append({"etapa": "tras_put2", "campana": tras_put2})
            if isinstance(tras_put2, dict):
                contexto["confirmables"].append(
                    _normaliza_db(tras_put2.get("dynamicBidding") or {})
                )
            _veredicto_parcial(
                registro,
                tras_put2,
                objetivo,
                ("cambio", "put2"),
                "primero_sigue_tras_put2",
                cero_ausente=False,
            )
    except Exception as exc:
        registro["error_cambio"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            salida = _fase_regreso(cliente, perfil_id, registro, contexto)
        except Exception as exc:
            registro["error_regreso"] = f"{type(exc).__name__}: {exc}"
            registro["iguales"] = False
            registro["resultado"] = "REVISAR: el regreso lanzo excepcion; ver lecturas y envios"
            salida = SALIR_REVISAR
    _imprime(registro)
    return salida


if __name__ == "__main__":
    raise SystemExit(main())
