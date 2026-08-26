#!/usr/bin/env python3
"""Probe AUTORIZADO de las cuatro formas de mutacion real (ORBIT 04, task 2.5).

RUNBOOK DEL PROBE — leer ANTES de tocar nada (decision 23; APPLY.md §11d).
La corrida real la AUTORIZA EL DUENO con una campana sacrificable y la
coordina el lead; esta herramienta JAMAS se ejecuta "para ver si funciona".

QUE HACE: LAS CUATRO formas selladas, cada una con fila de ledger tipo
'probe' (decision_id NULL, quota EXENTA) ANTES del HTTP, captura del ack
completo, readback, reversa y verificacion estado-final == estado-inicial:

  bid_keyword  PUT /sp/keywords con bid+0.01 sobre la PRIMERA keyword EXACT
               de la campana con bid, y reversa al ORIGINAL LEIDO.
  bid_target   idem sobre el primer product_target con bid (PUT /sp/targets).
  negative     POST + DELETE de un negative exacto sobre un termino BASURA
               (neto cero).
  keyword      POST + DELETE de una keyword EXACT basura (el corazon del
               harvest; neto cero). El bid del POST sale de una fuente REAL:
               el bid LEIDO de la primera keyword EXACT de la campana.

PASO A PASO DE LA CORRIDA AUTORIZADA:

 1. El dueno elige la campana sacrificable y su plataforma (amazon_us o
    amazon_mx) y AUTORIZA la corrida en ese momento (nada pre-autorizado).
 2. Un admin siembra la clave de allowlist en la config VIGENTE (nueva fila
    de config_version, append-only, rol app_admin):
      INSERT INTO config_version (label, settings)
      VALUES ('smoke 2.5', '<settings vigentes + clave>'::jsonb);
    donde la clave es ads_smoke_campaign_<platform> = external_id de la
    campana. OJO: config_version se resuelve por ULTIMA fila — hay que copiar
    los settings vigentes y AGREGAR la clave (sembrar solo la clave apagaria
    los caps ads_apply_cap_* para las lecturas de ese dia).
    El tool JAMAS acepta la campana por flag/env: solo desde config.
 3. El dueno setea el token efimero SOLO para esta corrida:
      export ORBIT_SMOKE_AUTH="<token de un uso>"   # se BORRA al terminar
 4. Corre el tool EN EL SERVER (donde viven los secrets y el tunel a la base;
    ver docs/DEPLOY.md), con ORBIT_DSN_DECIDE en el entorno (identidad del
    motor: sus filas de ledger nacen tipo probe auditable) y capturando la
    evidencia:
      python tools/smoke_apply.py --forma todas --platform <platform> \
        --acepto-mutacion-real 2>&1 | tee out/smoke-apply-<fecha>.log
    Dos capas: el env efimero SOLO no corre nada — el flag
    --acepto-mutacion-real es obligatorio (nada por accidente).
 5. Verificar el exit code (0 = las formas corrieron con neto cero) y que
    cada linea JSON de stdout trae ok=true y neto_cero=true. Cada linea es
    la evidencia de una forma: request exacto, ack (body + headers sin
    secretos), readback y reversa. Las filas de apply_attempt tipo 'probe'
    quedan como rastro durable en la base.
 6. FIJAR LOS SHAPES (el objetivo de la corrida): contra cada ack/readback
    real, confirmar o corregir las HIPOTESIS de mas abajo y los shapes que
    los tests de readback de 2.1-2.3 tienen marcados "pendientes de shape"
    (regla 8); re-sellar esos tests contra el shape real.
 7. Cerrar: el dueno BORRA ORBIT_SMOKE_AUTH y el admin siembra una config
    NUEVA sin la clave de campana (append-only: quitar = fila nueva sin la
    clave). La evidencia (log + SELECT del ledger probe) va al registro de
    ORBIT 04; la re-verificacion E2E de 4.3 re-usa esta misma herramienta.

HIPOTESIS SIN VERIFICAR (ORDEN EXPLICITA DEL DUENO): los ENUMS/tipos del
REQUEST de mutacion (matchType, state, bid como string) y los shapes de los
acks NO corrieron nunca contra la API real — la corrida autorizada los fija.
Estan declarados en HIPOTESIS_SHAPES y viajan en la evidencia de cada forma.

Uso: python tools/smoke_apply.py --forma <bid_keyword|bid_target|negative|keyword|todas>
     --platform <amazon_us|amazon_mx> --acepto-mutacion-real
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

# Bootstrap del repo: el tool corre como script (python tools/smoke_apply.py)
# y app/ no esta en sys.path desde tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg.types.json import Json  # noqa: E402

from app.ads.client import AdsApiError, AdsClient  # noqa: E402
from app.ads.config import AdsCredentials  # noqa: E402
from app.ads.structure import evaluar_perfiles  # noqa: E402
from app.ads.write import (  # noqa: E402
    MODO_CONFIRMADO_LIVE,
    PLATAFORMA_MONEDA,
    AdsApiErrorMutacion,
    AdsWriteClient,
    _bid_payload,
)
from app.db import connect  # noqa: E402
from app.redaction import scrub  # noqa: E402

# Autorizacion EFIMERA: el dueno setea ORBIT_SMOKE_AUTH solo para la corrida
# y lo borra despues. Capa 1 de dos (la capa 2 es --acepto-mutacion-real).
AUTORIZACION_ENV = "ORBIT_SMOKE_AUTH"

FORMAS = ("bid_keyword", "bid_target", "negative", "keyword")
FORMA_TODAS = "todas"

# La perturbacion del probe: +/-0.01 sobre el bid LEIDO (sellado 23). Va como
# Decimal; la presentacion (quantize a 2 decimales, string) es de write.py.
DELTA_BID = Decimal("0.01")

# Tope de paginacion de los list v3 (mismo patron verificado de structure.py:
# primera pagina body {}, siguientes {"nextToken": ...}).
TOPE_PAGINAS = 50

# Headers del ack que la evidencia conserva: WHITELIST (jamas Authorization
# ni Amazon-Advertising-API-ClientId, que viajan en TODO request).
HEADERS_ACK = ("x-amz-request-id", "x-amz-rid", "x-amzn-requestid", "request-id", "content-type")

# HIPOTESIS SIN VERIFICAR (orden del dueno; APPLY.md §13.2): ninguno de estos
# enums/tipos corrio contra la API real. La corrida autorizada los CONFIRMA o
# CORRIGE; corregir = arreglar write.py y re-sellar los tests de readback de
# 2.1-2.3 contra el shape real. La evidencia de cada forma los nombra.
HIPOTESIS_SHAPES = {
    "match_type_exact": (
        "matchType 'exact' en el POST/PUT de keyword y negative (alternativa: "
        "'negativeExact' en negatives)"
    ),
    "state_user_paused": (
        "state 'userPaused'/'enabled' en pause/resume (formas no ejercidas "
        "por el smoke; el motor las usa en cortes)"
    ),
    "bid_string": "el bid viaja como string quantizado a 2 decimales (alternativa: numero)",
    "campo_id_ack": (
        "campo del id creado en el ack del POST (keywordId; alternativa: "
        "negativeKeywordId) — el smoke prefiere el id del LIST readback "
        "(shape verificado, regla 8)"
    ),
    "contenedor_get": (
        "contenedor del GET de readback de bid ('keywords'/'targets'; alternativa: otro contenedor)"
    ),
    "payload_delete": "DELETE con body {'keywordId': ...} (alternativa: query string)",
}

HIPOTESIS_POR_FORMA = {
    "bid_keyword": ("bid_string", "contenedor_get"),
    "bid_target": ("bid_string", "contenedor_get"),
    "negative": ("match_type_exact", "campo_id_ack", "payload_delete"),
    "keyword": ("match_type_exact", "campo_id_ack", "bid_string", "payload_delete"),
}

# Contenedores de los list v3: los cuatro verificados en vivo por el sync de
# estructura (targets responde 'targetingClauses') y negatives por regla 8.
CONTENEDOR_LIST = {
    "/sp/keywords/list": "keywords",
    "/sp/targets/list": "targetingClauses",
    "/sp/adGroups/list": "adGroups",
    "/sp/negativeKeywords/list": "negativeKeywords",
}

_SQL_CONFIG_VIGENTE = """
SELECT settings FROM config_version ORDER BY id DESC LIMIT 1
"""

_SQL_NACE_PROBE = """
INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)
VALUES (NULL, %s, 'probe', %s, false)
RETURNING id
"""

_SQL_SELLA_PROBE = """
UPDATE apply_attempt SET ack = %s, resultado = %s, finished_at = now() WHERE id = %s
"""


class SmokeError(Exception):
    """Fallo del smoke. El mensaje pasa por scrub (ultima linea de defensa)."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


# ---------------------------------------------------------------------------
# Puertas: autorizacion efimera + campana allowlisted (fail-closed)
# ---------------------------------------------------------------------------


def autorizacion_ok(valor_env: str | None, flag_acepto: bool) -> tuple[bool, str]:
    """Las DOS capas de autorizacion (sellado 23): env efimero no vacio AND
    flag explicito. Corre ANTES de abrir cualquier conexion/credencial."""
    if valor_env is None or not valor_env.strip():
        return False, (
            f"falta {AUTORIZACION_ENV}: el probe exige el token efimero que el "
            "dueno setea SOLO para la corrida autorizada (y borra despues). "
            "Sin el, no se abre ninguna conexion ni credencial (fail-closed)."
        )
    if not flag_acepto:
        return False, (
            "falta --acepto-mutacion-real: el env efimero solo no corre nada — "
            "el flag explicito es la segunda capa (nada por accidente)."
        )
    return True, ""


def clave_campana(platform: str) -> str:
    """Clave de allowlist en config_version: ads_smoke_campaign_<platform>."""
    return f"ads_smoke_campaign_{platform}"


def campana_allowlisted(conn: psycopg.Connection, platform: str) -> str | None:
    """El external_id de la campana sacrificable, SOLO desde la config
    VIGENTE (misma resolucion que apply.py/_cap_de_config). None = sin clave
    -> fail-closed. Clave presente pero corrupta (no string no vacio) es
    config ROTA: ruidosa, jamas silencio (regla 3)."""
    clave = clave_campana(platform)
    fila = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    if fila is None:
        return None
    valor = (fila[0] or {}).get(clave)
    if valor is None:
        return None
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"{clave}: valor corrupto en la config vigente: {valor!r}")
    return valor.strip()


# ---------------------------------------------------------------------------
# Ledger probe: nace PRE-HTTP, se sella UNA vez (trigger de 0002)
# ---------------------------------------------------------------------------


def nace_probe(conn: psycopg.Connection, payload: dict, seq: int) -> int:
    """Fila del ledger tipo 'probe' (decision_id NULL permitido SOLO para
    probes por el CHECK de 0002), quota_cobrada=false: el probe JAMAS
    consume quota (decision 23). El caller hace commit ANTES del HTTP."""
    return conn.execute(_SQL_NACE_PROBE, (seq, Json(payload))).fetchone()[0]


def sella_probe(
    conn: psycopg.Connection, id_attempt: int, ack: dict | None, resultado: str
) -> None:
    """Sella el ack/resultado/finished_at UNA vez (mismo UPDATE acotado que
    apply.py; el trigger de 0002 lo exige)."""
    conn.execute(_SQL_SELLA_PROBE, (Json(ack) if ack is not None else None, resultado, id_attempt))


# ---------------------------------------------------------------------------
# Listas read-only con el MISMO profile sellado del cliente
# ---------------------------------------------------------------------------


@dataclass
class ContextoSmoke:
    """Todo lo que una forma necesita: la conexion (ledger), el write client
    (scope sellado a la instancia), la campana allowlisted y el contador de
    seq de la corrida (auditable: las filas probe quedan 1..N en orden)."""

    conn: psycopg.Connection
    cliente: AdsWriteClient
    platform: str
    campana: str
    profile_id: str
    secuencia: int = 0


def listar_paginado(ctx: ContextoSmoke, path: str) -> list[dict]:
    """POST de LECTURA (list v3) con el MISMO profile del cliente sellado.

    r1 del brief §13: los metodos heredados aceptan profile arbitrario;
    aqui se pasa la UNICA variable de profile de la corrida (la misma con
    que se construyo el write client). Paginacion nextToken verificada en
    vivo (structure.py): primera pagina {}, siguientes nextToken, tope."""
    contenedor = CONTENEDOR_LIST[path]
    items: list[dict] = []
    next_token: str | None = None
    for _ in range(TOPE_PAGINAS):
        body = {"nextToken": next_token} if next_token else {}
        data = ctx.cliente.list_objects(path, body, profile_id=ctx.profile_id).json()
        if not isinstance(data, dict):
            raise SmokeError(f"respuesta no-dict de {path}")
        items.extend(item for item in data.get(contenedor) or [] if isinstance(item, dict))
        next_token = data.get("nextToken")
        if not next_token:
            return items
    raise SmokeError(f"paginacion de {path} excede {TOPE_PAGINAS} paginas")


def _bid_valido(valor: object) -> Decimal | None:
    """Decimal del bid del payload (via str, sin float) o None; bid <= 0 no
    es elegible (misma puerta que structure._bid_decimal)."""
    try:
        bid = Decimal(str(valor))
    except (ArithmeticError, ValueError, TypeError):
        return None
    return bid if bid > 0 else None


def primer_keyword_exacta_con_bid(items: list[dict], campana: str) -> dict | None:
    """La PRIMERA keyword EXACT de la campana allowlisted con bid existente
    (diseno sellado 2.5). matchType se compara lower: el valor exacto del
    enum es HIPOTESIS (regla 8 lo trae 'exact' en negatives)."""
    for item in items:
        if str(item.get("campaignId", "")) != campana:
            continue
        if str(item.get("matchType", "")).lower() != "exact":
            continue
        if _bid_valido(item.get("bid")) is None:
            continue
        return item
    return None


def primer_target_con_bid(items: list[dict], campana: str) -> dict | None:
    """El PRIMER product_target de la campana con bid existente (los targets
    no tienen matchType: son targeting clauses)."""
    for item in items:
        if str(item.get("campaignId", "")) != campana:
            continue
        if _bid_valido(item.get("bid")) is None:
            continue
        return item
    return None


def primer_ad_group_de_campana(items: list[dict], campana: str) -> dict | None:
    """El PRIMER ad group de la campana allowlisted (destino del termino
    basura de las formas negative/keyword)."""
    for item in items:
        if str(item.get("campaignId", "")) == campana and item.get("adGroupId") is not None:
            return item
    return None


def termino_basura(ahora: dt.datetime | None = None) -> str:
    """Termino BASURA unico por corrida (prefijo zz + timestamp UTC): jamas
    colisiona con una keyword real de la cuenta."""
    ts = (ahora or dt.datetime.now(dt.UTC)).strftime("%Y%m%d%H%M%S")
    return f"zzsmokeprobe{ts}"


# ---------------------------------------------------------------------------
# Evidencia: respuesta HTTP con headers whitelist y body saneado
# ---------------------------------------------------------------------------


def _json_seguro(resp: httpx.Response) -> dict:
    """El JSON del ack; si el body no parsea, evidencia minima (el ack crudo
    JAMAS debe tumbar la evidencia). Misma regla que apply._json_seguro."""
    try:
        return resp.json()
    except ValueError:
        return {"status": resp.status_code, "body": resp.text[:200]}


def _evidencia_respuesta(resp: httpx.Response) -> dict:
    return {
        "status": resp.status_code,
        "headers": {k: resp.headers[k] for k in HEADERS_ACK if k in resp.headers},
        "cuerpo": _json_seguro(resp),
    }


# ---------------------------------------------------------------------------
# Un paso de mutacion: ledger PRE-HTTP -> HTTP -> ack -> sello
# ---------------------------------------------------------------------------


def _paso_mutacion(ctx: ContextoSmoke, nombre: str, payload: dict, funcion) -> dict:
    """La secuencia sellada de UNA mutacion del probe:

    1. fila probe PRE-HTTP con el payload EXACTO + COMMIT (intencion durable
       ANTES del HTTP — la prueba regla 9 de los tests espia la base desde el
       propio transport);
    2. HTTP (write client: allowlist + scope sellado + quantize);
    3. sello: ack/resultado/finished_at UNA vez.

    >=400 determinista (AdsApiErrorMutacion): la fila se sella con el cuerpo
    del rechazo. Ambiguo (AdsApiError, 5xx/red): la fila queda SIN sello —
    la fila ES el rastro (misma semantica que apply.py). El paso devuelve
    ok=False en ambos casos; el caller decide la reversa best-effort."""
    ctx.secuencia += 1
    id_attempt = nace_probe(ctx.conn, payload, ctx.secuencia)
    ctx.conn.commit()  # intencion durable PRE-HTTP
    paso: dict = {
        "paso": nombre,
        "ledger": {"attempt_id": id_attempt, "payload": payload, "quota_cobrada": False},
    }
    try:
        resp = funcion()
    except AdsApiErrorMutacion as exc:
        sella_probe(ctx.conn, id_attempt, None, f"fallo http {exc.status}: {exc.cuerpo}")
        ctx.conn.commit()
        paso.update(ok=False, error=scrub(str(exc)))
        return paso
    except AdsApiError as exc:  # ambiguo: la fila queda sin sello (el rastro)
        paso.update(ok=False, error=scrub(str(exc)), ledger={**paso["ledger"], "sin_sello": True})
        return paso
    ack = _json_seguro(resp)
    sella_probe(ctx.conn, id_attempt, ack, "ok")
    ctx.conn.commit()
    paso.update(ok=True, http=_evidencia_respuesta(resp))
    return paso


def _paso_readback_bid(
    ctx: ContextoSmoke, es_keyword: bool, ext: str
) -> tuple[Decimal | None, dict]:
    """GET de readback con el scope sellado (get_sellado). HIPOTESIS
    contenedor_get: 'keywords'/'targets' con campo 'bid'. Devuelve (bid leido
    o None, evidencia del paso); JAMAS lanza (un readback roto es un hallazgo
    del probe, no un crash)."""
    path = "/sp/keywords" if es_keyword else "/sp/targets"
    param = "keywordId" if es_keyword else "targetId"
    contenedor = "keywords" if es_keyword else "targets"
    try:
        resp = ctx.cliente.get_sellado(path, params={param: ext})
        filas = (resp.json() or {}).get(contenedor) or []
        crudo = filas[0].get("bid") if filas else None
        bid = _bid_valido(crudo)
        return bid, {
            "paso": "readback",
            "http": _evidencia_respuesta(resp),
            "bid_leido": str(bid) if bid is not None else None,
            "bid_crudo": crudo,
        }
    except (AdsApiError, ValueError, TypeError, IndexError, AttributeError) as exc:
        return None, {"paso": "readback", "error": scrub(str(exc)), "bid_leido": None}


# ---------------------------------------------------------------------------
# Formas bid_keyword / bid_target: +0.01 con reversa al ORIGINAL LEIDO
# ---------------------------------------------------------------------------


def _forma_bid(ctx: ContextoSmoke, forma: str) -> dict:
    es_keyword = forma == "bid_keyword"
    if es_keyword:
        items = listar_paginado(ctx, "/sp/keywords/list")
        sel = primer_keyword_exacta_con_bid(items, ctx.campana)
        campo = "keywordId"
    else:
        items = listar_paginado(ctx, "/sp/targets/list")
        sel = primer_target_con_bid(items, ctx.campana)
        campo = "targetId"
    if sel is None:
        return {
            "ok": False,
            "neto_cero": None,
            "pasos": [],
            "error": (
                f"sin {'keyword EXACT' if es_keyword else 'target'} con bid en la campana "
                f"{ctx.campana} (no hay entidad elegible para el probe)"
            ),
        }
    ext = str(sel[campo])
    bid_original = _bid_valido(sel["bid"])
    assert bid_original is not None  # el selector ya lo valido
    bid_nuevo = bid_original + DELTA_BID
    moneda = PLATAFORMA_MONEDA[ctx.platform]

    def _mutar(bid: Decimal):
        if es_keyword:
            return lambda: ctx.cliente.actualizar_bid_keyword(ext, bid, moneda)
        return lambda: ctx.cliente.actualizar_bid_target(ext, bid, moneda)

    pasos = [
        {
            "paso": "estado_inicial",
            "entidad": ext,
            "bid_original": _bid_payload(bid_original),
            "bid_objetivo": _bid_payload(bid_nuevo),
        }
    ]
    esperado = _bid_payload(bid_nuevo)
    mutacion = _paso_mutacion(
        ctx, "http_mutacion", {campo: ext, "bid": esperado}, _mutar(bid_nuevo)
    )
    pasos.append(mutacion)
    if mutacion["ok"]:
        leido, paso_rb = _paso_readback_bid(ctx, es_keyword, ext)
        pasos.append({**paso_rb, "esperado": esperado})
    else:
        leido = None
        pasos.append({"paso": "readback", "omitido": "mutacion rechazada (>=400)"})
    # La reversa corre cuando el HTTP pudo haber salido (ok) o quedo AMBIGUO
    # (AdsApiError sin sello); escribe el ORIGINAL LEIDO — jamas nuevo-0.01.
    # Un rechazo determinista (>=400 con sello) no toco nada: sin reversa.
    ambiguo = bool(mutacion.get("ledger", {}).get("sin_sello"))
    if mutacion["ok"] or ambiguo:
        reversa = _paso_mutacion(
            ctx, "reversa", {campo: ext, "bid": _bid_payload(bid_original)}, _mutar(bid_original)
        )
        pasos.append(reversa)
        if reversa["ok"]:
            final, paso_final = _paso_readback_bid(ctx, es_keyword, ext)
            pasos.append(
                {**paso_final, "paso": "readback_final", "esperado": _bid_payload(bid_original)}
            )
            neto_cero = final is not None and final == bid_original
        else:
            neto_cero = False
    else:
        pasos.append(
            {"paso": "reversa", "omitida": "mutacion rechazada (>=400): nada que revertir"}
        )
        neto_cero = None
    ok = bool(mutacion["ok"] and leido is not None and leido == Decimal(esperado) and neto_cero)
    return {"ok": ok, "neto_cero": neto_cero, "pasos": pasos}


# ---------------------------------------------------------------------------
# Formas negative / keyword: create + delete NETO CERO sobre termino basura
# ---------------------------------------------------------------------------


def _buscar_por_identidad(
    ctx: ContextoSmoke, es_negative: bool, grupo: str, termino: str
) -> tuple[dict | None, list[dict]]:
    """Identidad completa (sellado 13): adGroupId + keywordText + matchType
    exacto. Devuelve (item o None, TODOS los items del list): el caller usa
    la lista para el veredicto de ausencia (neto cero)."""
    items = listar_paginado(
        ctx, "/sp/negativeKeywords/list" if es_negative else "/sp/keywords/list"
    )
    for item in items:
        if str(item.get("adGroupId", "")) != grupo:
            continue
        if item.get("keywordText") != termino:
            continue
        if str(item.get("matchType", "")).lower() != "exact":
            continue
        return item, items
    return None, items


def _forma_create_delete(ctx: ContextoSmoke, forma: str) -> dict:
    es_negative = forma == "negative"
    grupos = listar_paginado(ctx, "/sp/adGroups/list")
    grupo_item = primer_ad_group_de_campana(grupos, ctx.campana)
    if grupo_item is None:
        return {
            "ok": False,
            "neto_cero": None,
            "pasos": [],
            "error": (
                f"sin ad group en la campana {ctx.campana} (no hay destino para el termino basura)"
            ),
        }
    grupo = str(grupo_item["adGroupId"])
    termino = termino_basura()
    pasos: list[dict] = [{"paso": "estado_inicial", "ad_group": grupo, "termino_basura": termino}]
    if not es_negative:
        fuente = primer_keyword_exacta_con_bid(
            listar_paginado(ctx, "/sp/keywords/list"), ctx.campana
        )
        if fuente is None:
            return {
                "ok": False,
                "neto_cero": None,
                "pasos": pasos,
                "error": (
                    "sin keyword EXACT con bid en la campana: no hay fuente REAL de bid (regla 3)"
                ),
            }
        bid = _bid_valido(fuente["bid"])
        assert bid is not None
    else:
        bid = None

    # Payloads ESPEJO de write.py (regla 2, una sola fuente del shape: si
    # write.py cambia, estos dict lo siguen — los tests de payload EXACTO lo cazan).
    payload_post = {
        "adGroupId": grupo,
        "campaignId": ctx.campana,
        "keywordText": termino,
        "matchType": "exact",  # HIPOTESIS match_type_exact
    }
    if bid is not None:
        payload_post["bid"] = _bid_payload(bid)  # HIPOTESIS bid_string

    def _post():
        if es_negative:
            return ctx.cliente.crear_negative_exacto(grupo, ctx.campana, termino)
        assert bid is not None
        return ctx.cliente.crear_keyword_exacta(
            grupo, ctx.campana, termino, bid, PLATAFORMA_MONEDA[ctx.platform]
        )

    def _delete(id_creado: str):
        if es_negative:
            return lambda: ctx.cliente.borrar_negative(id_creado)
        return lambda: ctx.cliente.borrar_keyword(id_creado)

    post = _paso_mutacion(ctx, "http_create", payload_post, _post)
    pasos.append(post)
    creado, _items = _buscar_por_identidad(ctx, es_negative, grupo, termino)
    pasos.append(
        {
            "paso": "readback_create",
            "hallado": creado is not None,
            "id_creado": str(creado["keywordId"]) if creado is not None else None,
        }
    )
    if creado is None:
        # El POST pudo quedar ambiguo: reversa best-effort por identidad; si
        # el list NO lo ve, no hay nada que borrar (y el ack no da id usable).
        pasos.append({"paso": "reversa_best_effort", "hallado": False, "delete": None})
        return {
            "ok": False,
            "neto_cero": None,
            "pasos": pasos,
            "error": "el readback no encontro lo creado (shape o fallo del POST; ver ack)",
        }
    id_creado = str(creado["keywordId"])
    delete = _paso_mutacion(ctx, "http_delete", {"keywordId": id_creado}, _delete(id_creado))
    pasos.append(delete)
    final, items_final = _buscar_por_identidad(ctx, es_negative, grupo, termino)
    neto_cero = final is None
    pasos.append({"paso": "readback_final", "ausente": neto_cero})
    ok = bool(post["ok"] and delete["ok"] and neto_cero)
    if not ok and delete["ok"] and not neto_cero:
        # El DELETE dice ok pero el termino sigue: re-intento best-effort UNA vez.
        reintento = _paso_mutacion(
            ctx, "reversa_best_effort", {"keywordId": id_creado}, _delete(id_creado)
        )
        final2, _ = _buscar_por_identidad(ctx, es_negative, grupo, termino)
        pasos.append(reintento)
        pasos.append({"paso": "readback_tras_reintento", "ausente": final2 is None})
        neto_cero = final2 is None
        ok = False  # la corrida ya no es limpia: se reporta igual
    return {"ok": ok, "neto_cero": neto_cero, "pasos": pasos}


# ---------------------------------------------------------------------------
# Orquestacion de formas y evidencia
# ---------------------------------------------------------------------------


def corre_forma(ctx: ContextoSmoke, forma: str) -> dict:
    """Corre UNA forma y devuelve su evidencia. NUNCA lanza: un fallo vive en
    ok=False (con reversa best-effort ya corrida por la forma)."""
    if forma in ("bid_keyword", "bid_target"):
        evidencia = _forma_bid(ctx, forma)
    elif forma in ("negative", "keyword"):
        evidencia = _forma_create_delete(ctx, forma)
    else:
        raise ValueError(f"forma desconocida: {forma!r}")
    evidencia.update(
        forma=forma,
        campana=ctx.campana,
        plataforma=ctx.platform,
        perfil=ctx.profile_id,
        hipotesis={k: HIPOTESIS_SHAPES[k] for k in HIPOTESIS_POR_FORMA[forma]},
    )
    return evidencia


def corre_formas(ctx: ContextoSmoke, formas: list[str]) -> int:
    """Corre las formas EN ORDEN imprimiendo la evidencia JSON (scrub) por
    stdout, una linea por forma + resumen. Fail-closed: la PRIMERA forma que
    falla DETIENE la corrida (si un shape esta podrido, correr mas formas
    multiplica el riesgo, no la evidencia). Exit 0 solo si TODO ok."""
    rc = 0
    corridas: list[dict] = []
    for forma in formas:
        evidencia = corre_forma(ctx, forma)
        corridas.append(evidencia)
        print(scrub(json.dumps(evidencia, default=str, ensure_ascii=False)))
        if not evidencia["ok"]:
            rc = 1
            break
    resumen = {
        "resumen": {
            "formas": [e["forma"] for e in corridas],
            "ok": [e["ok"] for e in corridas],
            "neto_cero": [e["neto_cero"] for e in corridas],
        },
        "exit": rc,
    }
    print(scrub(json.dumps(resumen, default=str, ensure_ascii=False)))
    return rc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python tools/smoke_apply.py",
        description=(
            "Probe AUTORIZADO de las 4 formas de mutacion real (ORBIT 04 2.5). "
            "Requiere ORBIT_SMOKE_AUTH (token efimero del dueno) Y "
            "--acepto-mutacion-real; la campana sacrificable viene SOLO de la "
            "clave ads_smoke_campaign_<platform> en config vigente."
        ),
    )
    parser.add_argument(
        "--forma", required=True, choices=(*FORMAS, FORMA_TODAS), help="forma a probar"
    )
    parser.add_argument(
        "--platform", required=True, choices=sorted(PLATAFORMA_MONEDA), help="plataforma"
    )
    parser.add_argument(
        "--acepto-mutacion-real",
        dest="acepto_mutacion_real",
        action="store_true",
        help="segunda capa de autorizacion: confirma que ESTA corrida muta Amazon de verdad",
    )
    return parser.parse_args(argv)


def _perfil_de_plataforma(credentials: AdsCredentials, platform: str) -> int | None:
    """Profile de la plataforma desde GET /v2/profiles + evaluar_perfiles
    (la MISMA fuente del ciclo; el profile NO se inventa — regla 2)."""
    for perfil in evaluar_perfiles(AdsClient(credentials)):
        if perfil.aceptado and perfil.platform == platform:
            return perfil.profile_id
    return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Capa 1+2 ANTES de abrir cualquier conexion/credencial.
    ok, mensaje = autorizacion_ok(os.environ.get(AUTORIZACION_ENV), args.acepto_mutacion_real)
    if not ok:
        print(mensaje, file=sys.stderr)
        return 2
    dsn = os.environ.get("ORBIT_DSN_DECIDE")
    if not dsn:
        print(
            "ORBIT_DSN_DECIDE no esta definido: el smoke corre con la identidad "
            "del motor (sus filas de ledger nacen tipo probe) — fail-closed",
            file=sys.stderr,
        )
        return 2
    conn: psycopg.Connection | None = None
    try:
        conn = connect(dsn)
        campana = campana_allowlisted(conn, args.platform)
        if campana is None:
            print(
                f"sin clave {clave_campana(args.platform)} en la config vigente: no hay "
                "campana allowlisted (fail-closed; se siembra con ceremonia de admin "
                "— ver runbook del modulo)",
                file=sys.stderr,
            )
            return 2
        credentials = AdsCredentials.from_secrets_dir()
        perfil = _perfil_de_plataforma(credentials, args.platform)
        if perfil is None:
            print(
                f"sin perfil aceptado para {args.platform} en /v2/profiles — fail-closed",
                file=sys.stderr,
            )
            return 2
        cliente = AdsWriteClient(
            credentials,
            platform=args.platform,
            profile_id=perfil,
            modo_confirmado=MODO_CONFIRMADO_LIVE,
        )
        ctx = ContextoSmoke(
            conn=conn,
            cliente=cliente,
            platform=args.platform,
            campana=campana,
            profile_id=str(perfil),
        )
        formas = list(FORMAS) if args.forma == FORMA_TODAS else [args.forma]
        return corre_formas(ctx, formas)
    except Exception as exc:
        print(
            f"smoke fallo (estado impreso en la evidencia que alcanzo a salir): {scrub(str(exc))}",
            file=sys.stderr,
        )
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
