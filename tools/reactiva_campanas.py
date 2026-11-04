#!/usr/bin/env python3
"""Reactivacion AUTORIZADA de campanas + pausas de dedup (CAMPANAS 01).

RUNBOOK — decision del dueno 2026-08-27 ("hazlo tu por API"): reactivar las
5 campanas candidatas del analisis (out/campanas-01-analisis-20260827.md) y
pausar las keywords duplicadas de la lista de dedup
(out/campanas-01-dedup-20260827.md). Es una operacion de NEGOCIO ejecutada
por API (equivalente a los clics de la consola), NO una decision del motor:
no pasa por apply_queue, ni por la escalera, ni por el write client sellado
(su allowlist no cubre resume de campana — shape nuevo, ver abajo). Reversa:
todo es reversible (resume <-> pause); no hay creates ni deletes.

QUE HACE, EN ORDEN (pausas ANTES que resumes: jamas hay ventana con el
mismo texto+match ENABLED en dos campanas a la vez):

 1. Plan desde la BASE (regla 8, ORBIT_DSN_READ read-only): resuelve los
    external_id por (campaign_id interno, texto, match_type) y verifica que
    cada keyword a pausar existe y hoy esta ENABLED; fail-closed si la
    realidad difiere de la lista.
 2. Pausas de keywords: PUT /sp/keywords state='PAUSED' — enum SELLADO en
    vivo 2026-08-27 (la hipotesis 'userPaused' de write.py quedo REFUTADA:
    el PUT v3 exige UPPER; evidencia en out/reactiva-campanas-20260827.log).
    El id viaja como STRING (con numero: 400 'NUMBER_VALUE...') y los headers
    llevan el vendor v3 EXACTO en Content-Type Y Accept (sin Accept: 415).
 3. Resume de las 5 campanas: PUT /sp/campaigns state='ENABLED' (mismo sello;
    vendor application/vnd.spcampaign.v3+json): PRIMERO una sola campana
    (A1U Exact US, chica y necesaria para 4.2) con readback; solo si esa
    cierra, las demas.
 4. Readback de TODO por POST /{recurso}/list (el GET directo da 403,
    probe 2.5) y reconciliacion final: estado vivo == estado objetivo.

CORRIDA (en el server, dentro del contenedor app — ahi viven secrets y DSN;
la evidencia se captura fuera):

    ssh goncloud 'docker exec -i orbit-app-1 python - --acepto-mutacion-real' \
      < tools/reactiva_campanas.py | tee out/reactiva-campanas-<fecha>.log

Sin --acepto-mutacion-real es DRY-RUN: imprime el plan resuelto y no toca
Amazon. Cada mutacion imprime UNA linea JSON (scrub: app.redaction) con
request/ack/readback; la linea final es la reconciliacion.

MODO --solo-campana (ORBIT 05 preflight 1.6a): reactiva SOLO la campana
indicada (id interno ad_entity) por el MISMO camino sellado de arriba.
El plan se reduce a ESA campana (nombre resuelto de la base), las pausas
de dedup se SALTAN (cero keywords) y ANTES de mutar — tambien en dry-run —
se verifica el estado VIVO por POST /sp/campaigns/list: si la campana ya
esta ENABLED, NO se muta (fail-closed: declararlo y seguir desde el paso
4 del runbook 1.6a). La mutacion real exige ademas --esperado-external
<external_id>: el external que el dueno autorizo viendo el dry-run; si la
base resuelve otro, aborta (anti-typo, cross-review grok). Con --dedup-1-6a
(parte 2, GO del dueno 2026-08-29) las 9 keywords EXACT de PAUSAS_DEDUP_1_6A
se pausan ANTES del resume por el MISMO camino sellado de la Fase 1 — y ese
modo SOLO acepta --solo-campana 3919: las 9 pausas estan autorizadas atadas
al resume de ESA campana (cross-review codex). Corrida:

    ssh goncloud 'docker exec -i orbit-app-1 python - --solo-campana 3919 \
      --esperado-external 251723662158466 [--dedup-1-6a] [--acepto-mutacion-real]' \
      < tools/reactiva_campanas.py

MODO --pausas-post-1-6a (GO del dueno 2026-08-29, "3. go"): pausa SOLO las
keywords de PAUSAS_POST_1_6A y CERO campanas — es EXCLUYENTE con
--solo-campana/--dedup-1-6a, porque un GO para PAUSAR jamas puede arrastrar
un resume. Misma disciplina anti-typo (--esperado-external obligatorio en
mutacion real, contra el external de la KEYWORD) y el plan debe resolver
EXACTAMENTE 1 keyword. Corrida:

    ssh goncloud 'docker exec -i orbit-app-1 python - --pausas-post-1-6a \
      --esperado-external 363015968886921 [--acepto-mutacion-real]' \
      < tools/reactiva_campanas.py

PRE-LECTURA ANTES DE CADA PAUSA (todas las fases, no solo el modo nuevo):
justo antes del PUT se lee el estado VIVO por POST /sp/keywords/list y si no
es ENABLED se ABORTA — misma clase que el hallazgo Greptile del PR #54 del
lado del resume: entre el SELECT del cache y la mutacion alguien pudo
pausarla o archivarla, y pausar igual seria pisar una decision ajena.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import time
from typing import Any

import httpx
import psycopg

from app.ads.client import DEFAULT_BASE_URL, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import evaluar_perfiles
from app.db import connect
from app.redaction import install_scrub_filter, register_secret, scrub

install_scrub_filter(logging.getLogger())  # root logger: todo sale scrubbed

API = DEFAULT_BASE_URL  # https://advertising-api.amazon.com
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

# Las 5 campanas a REACTIVAR (ids internos de ad_entity; verificados contra
# la base viva en el analisis 2026-08-27). AGM2M (165) queda FUERA: su
# veredicto es reactivar-con-ajuste y el ajuste fino es decision aparte.
CAMPANAS_REACTIVAR = {
    108: "Arras Manual (MX)",
    3934: "Wedding Coin - Asin Targeting (US)",
    3911: "A1U - Category Phrase - US",
    3909: "A1U - Category Exact - US",
    3926: "AU2 - Category Exact - US",
}

# Pausas de dedup: (campaign_id interno, keyword_text, match_type).
# Fuente: out/campanas-01-dedup-20260827.md (economia por lado, regla 8).
PAUSAS_DEDUP: list[tuple[int, str, str]] = [
    # Dentro de Arras Manual (pierden contra AC Phrase/Exact activas)
    (108, "arras matrimoniales", "EXACT"),
    (108, "arras de matrimonio", "PHRASE"),
    (108, "arras de plata", "EXACT"),
    (108, "arras de plata", "PHRASE"),
    # AD_READY (los broads que vendian mejor en Arras Manual o empatan 0/0)
    (157, "arras de oro", "BROAD"),
    (158, "arras de oro", "BROAD"),
    (159, "arras de oro", "BROAD"),
    (160, "arras de oro", "BROAD"),
    (158, "arras", "BROAD"),
    (159, "arras", "BROAD"),
    (160, "arras", "BROAD"),
    (157, "arras de plata", "BROAD"),
    (158, "arras de plata", "BROAD"),
    (159, "arras de plata", "BROAD"),
    (160, "arras de plata", "BROAD"),
    (157, "arras de matrimonio", "BROAD"),
    (158, "arras de matrimonio", "BROAD"),
    (157, "arras matrimoniales de oro", "BROAD"),
    (158, "arras matrimoniales de oro", "BROAD"),
    (159, "arras matrimoniales de oro", "BROAD"),
    # NOTA regla 8 (2026-08-27): el doc de dedup decia "AD_READY x4" para
    # 'arras matrimoniales de oro', pero la base VIVA confirma que la 160
    # NO la tiene (fail-closed de la 1a corrida lo atajo) — son 25 pausas.
    (158, "arras matrimoniales de plata", "BROAD"),
    (160, "arras matrimoniales de plata", "BROAD"),
    # AU2 Phrase US (las 3 PHRASE que gana A1U Phrase 3911)
    (3920, "unity coins", "PHRASE"),
    (3920, "arras for wedding ceremony", "PHRASE"),
    (3920, "silver arras for wedding", "PHRASE"),
]

# Dedup de ORBIT 05 preflight 1.6a (GO del dueno 2026-08-29, "go con la 1"):
# 9 pausas de keywords EXACT ANTES del resume de la 3919, para que jamas
# quede el mismo texto+match ENABLED en dos campanas. Fuente: brief parte 2
# del lead + evidencia out/orbit-05-preflight-1-6a-20260829.md seccion 2.0
# (criterio "gana quien convierte"; datos 90d). La fila 'silver arras for
# wedding' estuvo en disputa (la tabla del lead venia de sumar SIN colapsar
# la bitemporalidad; la re-derivacion colapsada la volteria hacia la 3909)
# y el lead la resolvio el 2026-08-29 con la respuesta literal "A": la
# lista se ejecuta TAL CUAL (esa fila pausa la copia de la 3919).
# Campana cuyo resume autoriza ese GO: las 9 pausas lo PROTEGEN (el dedup
# existe para reactivarla sin competencia propia). --dedup-1-6a solo acepta
# --solo-campana con ESTA campana (cross-review codex: con otra campana se
# pausarian las 9 fijas y se reactivaria una no autorizada).
CAMPANA_DEDUP_1_6A = 3919

PAUSAS_DEDUP_1_6A: list[tuple[int, str, str]] = [
    (3919, "arras para boda catolica", "EXACT"),
    (3919, "arras for wedding ceremony", "EXACT"),
    (3919, "wedding arras coins set", "EXACT"),
    (3919, "unity coins", "EXACT"),
    (3919, "silver arras for wedding", "EXACT"),
    (3919, "arras matrimoniales", "EXACT"),
    (3919, "arras de boda centenario", "EXACT"),
    (3919, "b0bvqfltlq", "EXACT"),
    (3926, "arras de boda cristiana", "EXACT"),
]

# Pausa suelta POST-1.6a (GO del dueno 2026-08-29, literal "3. go"): el dedup
# de 1.6a dejo un duplicado abierto que la tabla del lead no vio. Al reactivar
# la 3919, su copia de 'arras de boda cristiana' quedo ENABLED a la vez que la
# de A1U (3909) — la tabla listaba "—" para la 3909 porque no tiene datos, no
# porque no existiera. Datos 90d COLAPSADOS (v_metric_latest, regla 5): la de
# 3909 tiene 0 clics / $0 / 0 ordenes y ni un dia de metrica; la de 3919 tiene
# 35 clics / $41.05 / 1 orden / $106.20. Gana quien convierte -> se pausa la
# de 3909. Es UNA pausa y NINGUN resume: este modo no reactiva nada.
PAUSAS_POST_1_6A: list[tuple[int, str, str]] = [
    (3909, "arras de boda cristiana", "EXACT"),
]

# Enum del REQUEST de pause/resume: SELLADO EN VIVO 2026-08-27 (evidencia en
# out/reactiva-campanas-20260827.log y los probes del mismo dia): el PUT v3
# exige UPPER ('PAUSED'/'ENABLED'; 'paused' minuscula responde 400 con el
# enum exacto [ENABLED, PROPOSED, PAUSED]). La hipotesis previa del repo
# ('userPaused') queda REFUTADA — write.py se corrige en este mismo cambio.
ENUM_PAUSE = "PAUSED"
ENUM_RESUME = "ENABLED"

# Vendor Content-Type/Accept por path (el PUT sin vendor responde 415 — la
# API lista el tipo exacto disponible; mismos tipos que write.py
# MUTATION_REQUEST_TYPES + el de campanas sellado en esta corrida).
VENDOR_POR_PATH = {
    "/sp/keywords": "application/vnd.spkeyword.v3+json",
    "/sp/campaigns": "application/vnd.spcampaign.v3+json",
}


class Abortar(RuntimeError):
    """Fail-closed: la realidad difiere del plan o la API rechaza."""


def _log(evento: str, **campos: Any) -> None:
    print(scrub(json.dumps({"evento": evento, **campos}, default=str)), flush=True)


def _token_lwa(cred: AdsCredentials, client: httpx.Client) -> str:
    resp = client.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": cred.refresh_token,
            "client_id": cred.client_id,
            "client_secret": cred.client_secret,
        },
    )
    if resp.status_code != 200:
        raise Abortar(f"LWA {resp.status_code}: {scrub(resp.text[:300])}")
    token = resp.json()["access_token"]
    register_secret(token)
    return token


def _dsn_read() -> str:
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        raise Abortar("ORBIT_DSN_READ no esta en el entorno (corre dentro del contenedor app)")
    return dsn


def _perfiles(cliente_lectura: AdsClient) -> dict[str, int]:
    """platform -> profile_id ACEPTADO (una fuente: evaluar_perfiles)."""
    out: dict[str, int] = {}
    for p in evaluar_perfiles(cliente_lectura):
        if p.aceptado and p.platform and p.profile_id is not None:
            out[p.platform] = p.profile_id
    return out


def _resolver_keywords(conn: psycopg.Connection, pausas: list[tuple[int, str, str]]) -> list[dict]:
    """external_id y plataforma de las keywords a pausar, contra la base VIVA
    (un unico camino para el dedup del camino original y el del 1.6a).
    Fail-closed: cada (campana, texto, match) debe resolver EXACTAMENTE 1
    fila en estado ENABLED; la realidad difiere de la lista -> Abortar ANTES
    de tocar Amazon."""
    keywords: list[dict] = []
    for camp_id, texto, match in pausas:
        rows = conn.execute(
            """SELECT k.external_id, s.status, c.platform
               FROM ad_entity k
               JOIN ad_entity g ON g.id = k.parent_id
               JOIN ad_entity c ON c.id = g.parent_id
               JOIN ad_entity_state s ON s.ad_entity_id = k.id
               WHERE c.id = %s AND k.kind = 'keyword'
                 AND lower(k.keyword_text) = %s AND k.match_type = %s""",
            (camp_id, texto, match),
        ).fetchall()
        if len(rows) != 1:
            raise Abortar(
                f"keyword ({camp_id}, {texto!r}, {match}) resuelve a {len(rows)} filas "
                "(esperaba EXACTAMENTE 1; la realidad difiere de la lista de dedup)"
            )
        ext, estado, platform = rows[0]
        if estado != "ENABLED":
            raise Abortar(f"keyword {ext} ({texto!r}) hoy esta {estado}, no ENABLED")
        keywords.append(
            {
                "camp_id": camp_id,
                "texto": texto,
                "match": match,
                "external_id": ext,
                "platform": platform,
            }
        )
        _log(
            "keyword_resuelta",
            camp_id=camp_id,
            texto=texto,
            match=match,
            external_id=ext,
            platform=platform,
        )
    return keywords


def _resolver_plan(
    conn: psycopg.Connection,
    solo_campana: int | None = None,
    dedup_1_6a: bool = False,
    pausas_post_1_6a: bool = False,
) -> tuple[dict[int, dict], list[dict]]:
    """external_ids y plataforma de campanas y keywords, contra la base VIVA.
    Fail-closed: cualquier faltante o estado inesperado aborta ANTES de
    tocar Amazon.

    Con solo_campana (ORBIT 05 preflight 1.6a): el plan se reduce a ESA
    campana (id interno ad_entity); con dedup_1_6a resuelve TAMBIEN las 9
    pausas de PAUSAS_DEDUP_1_6A por el MISMO camino del helper (parte 2, GO
    del dueno 2026-08-29: dedup ANTES del resume); sin el flag las keywords
    quedan VACIAS.

    Con pausas_post_1_6a (GO del dueno 2026-08-29, "3. go"): el plan son SOLO
    las pausas de PAUSAS_POST_1_6A y CERO campanas — este modo no reactiva
    nada, asi que la fase de resumes queda vacia por construccion."""
    if pausas_post_1_6a:
        return {}, _resolver_keywords(conn, PAUSAS_POST_1_6A)

    if solo_campana is not None:
        row = conn.execute(
            """SELECT e.external_id, e.platform, e.name, s.status
               FROM ad_entity e JOIN ad_entity_state s ON s.ad_entity_id = e.id
               WHERE e.id = %s AND e.kind = 'campaign'""",
            (solo_campana,),
        ).fetchone()
        if row is None:
            raise Abortar(f"campana {solo_campana} no existe en la base (kind 'campaign')")
        external_id, platform, name, status = row
        # Regla 3: e.name NULL -> el external_id es el nombre declarado, sin inventar.
        nombre = name or external_id
        _log(
            "campana_resuelta",
            id=solo_campana,
            nombre=nombre,
            external_id=external_id,
            platform=platform,
            estado_cache=status,
        )
        keywords = _resolver_keywords(conn, PAUSAS_DEDUP_1_6A) if dedup_1_6a else []
        return (
            {
                solo_campana: {
                    "external_id": external_id,
                    "platform": platform,
                    "status": status,
                    "nombre": nombre,
                }
            },
            keywords,
        )

    campanas: dict[int, dict] = {}
    for cid, nombre in CAMPANAS_REACTIVAR.items():
        row = conn.execute(
            """SELECT e.external_id, e.platform, s.status
               FROM ad_entity e JOIN ad_entity_state s ON s.ad_entity_id = e.id
               WHERE e.id = %s AND e.kind = 'campaign'""",
            (cid,),
        ).fetchone()
        if row is None:
            raise Abortar(f"campaña {cid} ({nombre}) no existe en la base")
        campanas[cid] = {
            "external_id": row[0],
            "platform": row[1],
            "status": row[2],
            "nombre": nombre,
        }
        _log(
            "campana_resuelta",
            id=cid,
            nombre=nombre,
            external_id=row[0],
            platform=row[1],
            estado_cache=row[2],
        )

    keywords = _resolver_keywords(conn, PAUSAS_DEDUP)
    return campanas, keywords


def _put(
    client: httpx.Client,
    token: str,
    cred: AdsCredentials,
    profile: int,
    path: str,
    payload: dict,
) -> dict:
    vendor = VENDOR_POR_PATH[path]
    resp = client.put(
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Amazon-Advertising-API-ClientId": cred.client_id,
            "Amazon-Advertising-API-Scope": str(profile),
            # Vendor EXACTO en los dos headers (mismo criterio que el write
            # client sellado): sin Accept la API responde 415.
            "Content-Type": vendor,
            "Accept": vendor,
        },
        json=payload,
    )
    cuerpo: dict = {}
    with contextlib.suppress(ValueError):
        cuerpo = resp.json()
    return {"status": resp.status_code, "cuerpo": cuerpo, "texto": scrub(resp.text[:400])}


def _errores_207(ack: dict) -> list:
    """Todos los errores del 207 multi-status, cualquiera sea el contenedor
    (mismo criterio que apply_harvest._id_de_ack: success/error ANIDADOS)."""
    cuerpo = ack["cuerpo"]
    if not isinstance(cuerpo, dict):
        return [{"no_json": ack["texto"]}]
    errores: list = []
    for valor in cuerpo.values():
        if isinstance(valor, dict):
            errores.extend(valor.get("error") or [])
    return errores


def _readback_estado(
    cliente_lectura: AdsClient,
    profile: int,
    path_list: str,
    filtro: str,
    contenedor: str,
    id_campo: str,
    external_id: str,
) -> str | None:
    resp = cliente_lectura.list_objects(
        path_list, {filtro: {"include": [external_id]}}, profile_id=profile
    )
    if resp.status_code != 200:
        return None
    for fila in resp.json().get(contenedor) or []:
        if str(fila.get(id_campo)) == str(external_id):
            return fila.get("state")
    return None


def _put_estado(
    http: httpx.Client,
    token: str,
    cred: AdsCredentials,
    profile: int,
    path: str,
    contenedor: str,
    id_campo: str,
    external_id: str,
    estado_put: str,
) -> dict:
    """PUT de estado con el enum SELLADO (ENUM_PAUSE/ENUM_RESUME). El id viaja
    como STRING — con numero JSON la API responde 400 'NUMBER_VALUE can not
    be converted to a String' (probe 2026-08-27). Abortar si la API rechaza:
    fail-closed, jamas 'adivina' silencioso."""
    ack = _put(
        http,
        token,
        cred,
        profile,
        path,
        {contenedor: [{id_campo: str(external_id), "state": estado_put}]},
    )
    errores = _errores_207(ack)
    if ack["status"] in (200, 207) and not errores:
        return ack
    _log(
        "estado_rechazado",
        external_id=external_id,
        enum_intentado=estado_put,
        status=ack["status"],
        errores=errores,
    )
    raise Abortar(f"PUT {path} rechazado para {external_id} (status {ack['status']})")


def _orden_resumes(campanas: dict[int, dict]) -> list[int]:
    """Orden de la fase de resumes: A1U Exact 3909 (el shape nuevo, sellado en
    vivo 2026-08-27) encabeza SOLO si esta en el plan; el resto conserva el
    orden del plan. Con --solo-campana el plan ES una sola campana y va tal
    cual (review r2: el orden hardcodeado [3909]+... reventaba con KeyError
    porque 3909 no esta en un plan reducido)."""
    if 3909 in campanas:
        return [3909] + [c for c in campanas if c != 3909]
    return list(campanas)


def _valida_modos(args) -> None:
    """Guards de FLAGS (antes de abrir credenciales, base o red).

    Viven aparte de main() por el candado anti-monolito del repo: main ya
    orquesta plan + perfiles + dos fases de mutacion, y cada modo nuevo le
    sumaba ramas. La regla que protegen: un GO para PAUSAR jamas puede
    arrastrar un resume, y las 9 pausas del 1.6a solo protegen SU campana."""
    if args.pausas_post_1_6a and (args.solo_campana is not None or args.dedup_1_6a):
        raise Abortar(
            "--pausas-post-1-6a es excluyente con --solo-campana/--dedup-1-6a: "
            "este modo pausa y NO reactiva nada"
        )
    if args.dedup_1_6a:
        if args.solo_campana is None:
            raise Abortar("--dedup-1-6a requiere --solo-campana")
        if args.solo_campana != CAMPANA_DEDUP_1_6A:
            raise Abortar(
                f"--dedup-1-6a esta atado al resume de la campana {CAMPANA_DEDUP_1_6A} "
                f"(las 9 pausas protegen ESA reactivacion, GO 2026-08-29); "
                f"para la {args.solo_campana} es otra tarea con su propia lista"
            )


def _valida_anti_typo(args, campanas: dict[int, dict], keywords: list[dict]) -> None:
    """Anti-typo (cross-review grok, media): la mutacion real queda ATADA al
    external que el dueno autorizo VIENDO el dry-run. Un id interno
    equivocado revienta aqui, no en Amazon; el desacuerdo aborta tambien en
    dry-run. Aplica a la campana de --solo-campana y a la keyword de
    --pausas-post-1-6a."""
    if (
        args.esperado_external is not None
        and args.solo_campana is None
        and not args.pausas_post_1_6a
    ):
        raise Abortar(
            "--esperado-external solo tiene sentido junto a --solo-campana o --pausas-post-1-6a"
        )

    if args.pausas_post_1_6a:
        # La lista es de UNA sola keyword por diseno (un GO = una pausa); si
        # alguien la alarga, el candado obliga a revisar este bloque antes de
        # mutar en vez de atar el external a una keyword arbitraria.
        if len(keywords) != 1:
            raise Abortar(
                f"--pausas-post-1-6a espera EXACTAMENTE 1 keyword y el plan trae "
                f"{len(keywords)}: revisa PAUSAS_POST_1_6A y el anti-typo antes de mutar"
            )
        _exige_external(args, keywords[0]["external_id"], f"keyword {keywords[0]['texto']!r}")

    if args.solo_campana is not None:
        _exige_external(
            args, campanas[args.solo_campana]["external_id"], f"campana {args.solo_campana}"
        )


def _exige_external(args, resuelto: str, que: str) -> None:
    """El external autorizado debe coincidir con el resuelto, y en mutacion
    real no puede faltar (una fuente para los dos modos)."""
    if args.esperado_external is not None and args.esperado_external != resuelto:
        raise Abortar(
            f"--esperado-external {args.esperado_external} != external resuelto "
            f"{resuelto} ({que}): realidad difiere del plan"
        )
    if args.acepto_mutacion_real and args.esperado_external is None:
        raise Abortar(f"mutacion real exige --esperado-external ({que}); el resuelto es {resuelto}")


def _valida_perfiles(perfiles: dict, campanas: dict[int, dict], keywords: list[dict]) -> None:
    """Sin perfil aceptado no hay mutacion posible. Las KEYWORDS tambien se
    validan: sin esto el `perfiles[kw['platform']]` de la fase 1 reventaria
    con KeyError crudo en vez de fail-closed declarado."""
    for cid, c in campanas.items():
        if c["platform"] not in perfiles:
            raise Abortar(f"sin perfil aceptado para {c['platform']} (campana {cid})")
    for kw in keywords:
        if kw["platform"] not in perfiles:
            raise Abortar(
                f"sin perfil aceptado para {kw['platform']} (keyword {kw['external_id']})"
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--acepto-mutacion-real",
        action="store_true",
        help="obligatorio para tocar Amazon; sin el = dry-run",
    )
    ap.add_argument(
        "--solo-campana",
        type=int,
        default=None,
        help="id interno ad_entity de UNA campana: reduce el plan a ESA campana y "
        "salta las pausas de dedup (ORBIT 05 preflight 1.6a)",
    )
    ap.add_argument(
        "--esperado-external",
        default=None,
        help="external_id que la base DEBE resolver para --solo-campana (anti-typo, "
        "cross-review grok): OBLIGATORIO con --acepto-mutacion-real; aborta si difiere",
    )
    ap.add_argument(
        "--dedup-1-6a",
        action="store_true",
        help="con --solo-campana: pausar TAMBIEN las 9 keywords EXACT de "
        "PAUSAS_DEDUP_1_6A antes del resume (parte 2, GO del dueno 2026-08-29)",
    )
    ap.add_argument(
        "--pausas-post-1-6a",
        action="store_true",
        help="pausar SOLO las keywords de PAUSAS_POST_1_6A: cero resumes "
        "(duplicado que quedo abierto tras 1.6a; GO del dueno 2026-08-29)",
    )
    args = ap.parse_args()

    # Guards de flags ANTES de abrir credenciales, base o red (fail-closed).
    _valida_modos(args)

    cred = AdsCredentials.from_secrets_dir()
    conn = connect(_dsn_read())
    cliente_lectura = AdsClient(cred)
    http = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0))

    campanas, keywords = _resolver_plan(
        conn,
        args.solo_campana,
        dedup_1_6a=args.dedup_1_6a,
        pausas_post_1_6a=args.pausas_post_1_6a,
    )

    _valida_anti_typo(args, campanas, keywords)

    perfiles = _perfiles(cliente_lectura)
    _log("perfiles", perfiles=perfiles)
    _valida_perfiles(perfiles, campanas, keywords)

    # Guard 1.6a (SOLO --solo-campana): el estado VIVO se declara ANTES de
    # mutar; si la campana ya esta ENABLED, NO se muta. Tambien dispara en
    # dry-run: es la declaracion fail-closed que pide el runbook 1.6a.
    if args.solo_campana is not None:
        c = campanas[args.solo_campana]
        profile = perfiles[c["platform"]]
        vivo = _readback_estado(
            cliente_lectura,
            profile,
            "/sp/campaigns/list",
            "campaignIdFilter",
            "campaigns",
            "campaignId",
            c["external_id"],
        )
        _log("estado_vivo", camp_id=args.solo_campana, external_id=c["external_id"], estado=vivo)
        if vivo is None:
            raise Abortar(
                f"readback vivo de la campana {args.solo_campana} ({c['external_id']}) no respondio"
            )
        if vivo != "PAUSED":
            raise Abortar(
                f"campana {args.solo_campana} ya esta {vivo} en Amazon: no se muta "
                "(declararlo y sigue desde el paso 4 del runbook 1.6a)"
            )

    if not args.acepto_mutacion_real:
        _log(
            "dry_run",
            pausas=len(keywords),
            resumes=len(campanas),
            nota="sin --acepto-mutacion-real no se toca Amazon",
        )
        return 0

    token = _token_lwa(cred, http)

    # ---- Fase 1: pausas de dedup (enum del PUT SELLADO: ENUM_PAUSE) ----
    for kw in keywords:
        profile = perfiles[kw["platform"]]
        # PRE-LECTURA del estado VIVO justo antes del PUT (misma clase que el
        # hallazgo Greptile del PR #54, ahora del lado de la pausa): entre el
        # SELECT del cache y esta mutacion alguien —persona o automatismo—
        # pudo pausarla o archivarla. Pausar igual seria pisar una decision
        # ajena, y sobre una ARCHIVED el PUT ni siquiera significa lo mismo.
        vivo = _readback_estado(
            cliente_lectura,
            profile,
            "/sp/keywords/list",
            "keywordIdFilter",
            "keywords",
            "keywordId",
            kw["external_id"],
        )
        _log(
            "estado_vivo_prev_pause",
            external_id=kw["external_id"],
            texto=kw["texto"],
            camp_id=kw["camp_id"],
            estado=vivo,
        )
        if vivo != "ENABLED":
            raise Abortar(
                f"keyword {kw['external_id']} ({kw['texto']!r}) ya esta {vivo} en Amazon: "
                "no se muta (no se pisa una decision ajena)"
            )
        _put_estado(
            http,
            token,
            cred,
            profile,
            "/sp/keywords",
            "keywords",
            "keywordId",
            kw["external_id"],
            ENUM_PAUSE,
        )
        time.sleep(0.3)  # cortesia de rate limit
        estado = _readback_estado(
            cliente_lectura,
            profile,
            "/sp/keywords/list",
            "keywordIdFilter",
            "keywords",
            "keywordId",
            kw["external_id"],
        )
        _log(
            "pause",
            external_id=kw["external_id"],
            texto=kw["texto"],
            match=kw["match"],
            camp_id=kw["camp_id"],
            readback=estado,
            ok=estado == "PAUSED",
        )
        if estado != "PAUSED":
            raise Abortar(f"readback del pause de {kw['external_id']} != PAUSED: {estado}")

    # ---- Fase 2: resumes (PRIMERO A1U Exact 3909 sola: shape nuevo) ----
    for cid in _orden_resumes(campanas):
        c = campanas[cid]
        profile = perfiles[c["platform"]]
        # RE-LECTURA justo antes del PUT (hallazgo Greptile PR #54): entre el
        # guard de arriba y este resume corren las pausas de dedup (9 PUT +
        # readback), y en esa ventana alguien —persona o automatismo— pudo
        # pausar la campana a proposito. Reactivarla igual seria pisar una
        # decision ajena. El chequeo SOLO aplica a --solo-campana (el camino
        # original de CAMPANAS 01 reactiva un lote ya acordado).
        if args.solo_campana is not None:
            vivo_ahora = _readback_estado(
                cliente_lectura,
                profile,
                "/sp/campaigns/list",
                "campaignIdFilter",
                "campaigns",
                "campaignId",
                c["external_id"],
            )
            _log("estado_vivo_prev_resume", camp_id=cid, estado=vivo_ahora)
            if vivo_ahora != "PAUSED":
                raise Abortar(
                    f"campana {cid} ({c['external_id']}) cambio a {vivo_ahora} DURANTE la "
                    "corrida (entre el guard y el resume): no se pisa una decision ajena"
                )
        _put_estado(
            http,
            token,
            cred,
            profile,
            "/sp/campaigns",
            "campaigns",
            "campaignId",
            c["external_id"],
            ENUM_RESUME,
        )
        time.sleep(0.3)
        estado = _readback_estado(
            cliente_lectura,
            profile,
            "/sp/campaigns/list",
            "campaignIdFilter",
            "campaigns",
            "campaignId",
            c["external_id"],
        )
        _log(
            "resume",
            camp_id=cid,
            nombre=c["nombre"],
            external_id=c["external_id"],
            readback=estado,
            ok=estado == "ENABLED",
        )
        if estado != "ENABLED":
            raise Abortar(f"resume de {cid} fallo (readback {estado}) — las demas NO se tocaron")

    _log("reconciliacion_final", pausas_ok=len(keywords), resumes_ok=len(campanas), ok=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        _log("ABORTAR_FAIL_CLOSED", motivo=str(exc))
        sys.exit(2)
