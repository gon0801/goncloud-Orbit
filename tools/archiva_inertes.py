#!/usr/bin/env python3
"""Archivo MANUAL de keywords inertes con reversa (BIDS 01, D5).

Las hojas sin trafico no se ajustan (guarda entidad_inerte del ciclo):
se diagnostican en v_entidad_inerte y, con go del dueno, se archivan por
lote con esta herramienta. Es una operacion de NEGOCIO ejecutada por API
(equivalente a archivar en la consola), NO una decision del motor: no
pasa por apply_queue ni por la escalera.

QUE HACE, EN ORDEN:

 1. Plan desde la BASE (ORBIT_DSN_READ read-only): keywords de
    v_entidad_inerte con los filtros (--plataforma, --clasificacion con
    default peso_muerto, --min-dias-sin-impresiones con default 30,
    --limite). Solo kind='keyword': los product_target se cuentan como
    excluidos (residual 1: solo se reportan, jamas se archivan).
 2. Dry-run por defecto: imprime la tabla del plan y no toca Amazon.
 3. Mutacion (--acepto-mutacion-real --esperado N --go "<literal>"):
    len(plan) == N o aborta SIN abrir HTTP. Por keyword: LIST previo
    (vivo ENABLED o se salta con nota, sin pisar decision ajena) ->
    fila 'planeado' en keyword_archivo_manual + commit (intencion
    durable ANTES del HTTP) -> POST /sp/keywords/delete con
    keywordIdFilter (id como STRING, vendor v3 en Content-Type y Accept)
    -> readback LIST: ARCHIVED -> 'applied'; distinto -> 'failed' y el
    lote SE DETIENE. Una linea JSON por mutacion (scrub) y
    reconciliacion final.
 4. Reversa (--reponer <lote>): por cada fila 'applied', POST
    /sp/keywords con {adGroupId, campaignId, keywordText, matchType del
    ledger, state ENABLED, bid} y readback por el id creado (texto+match
    +grupo contra el ledger); la fila pasa a 'repuesto' con el external
    nuevo. Sin bid en el ledger no se repone (regla 3: no se inventa).

CORRIDA (en el server, dentro del contenedor app - ahi viven secrets y
DSN; la imagen solo trae app/, asi que el tool entra por stdin, como
reactiva_campanas):

    docker exec -i orbit-app-1 python - < tools/archiva_inertes.py
    docker exec -i orbit-app-1 python - --acepto-mutacion-real \
      --esperado 12 --go "<literal del dueno>" < tools/archiva_inertes.py
    docker exec -i orbit-app-1 python - \
      --reponer inertes-2026-09-05 --acepto-mutacion-real \
      < tools/archiva_inertes.py

HTTP propio con el sello v3 (igual que reactiva_campanas): el POST de
mutacion va directo con httpx (el guard read-only del cliente no cubre
mutaciones) y el readback por list_objects. Este modulo NO importa el
cliente de escritura: un segundo dueno de la mutacion (candado en
tests/test_architecture.py).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import logging
import os
import sys
import time
from decimal import ROUND_HALF_EVEN, Decimal
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

# Vendor del POST de keywords (create y delete): el mismo par sellado del
# allowlist de mutaciones (probe 2.5) y de reactiva_campanas.
VENDOR_KEYWORDS = "application/vnd.spkeyword.v3+json"

# matchType que Amazon acepta en el create (enum UPPER, probe 2.5). Otro
# valor en el ledger = fila corrupta: se declara y el lote se detiene.
MATCH_VALIDOS = frozenset({"EXACT", "PHRASE", "BROAD"})

CLASIFICACIONES = ("peso_muerto", "gasto_sin_ventas", "con_ventas_previas")

# Moneda esperada por plataforma para el create de --reponer (misma ley
# que el mapa de la capa HTTP; el candado de moneda solo escanea app/).
MONEDA_POR_PLATAFORMA = {"amazon_mx": "MXN", "amazon_us": "USD"}

_ESTADO_VIVO = "ENABLED"
_ESTADO_ARCHIVADO = "ARCHIVED"

# Revision del lead 2026-09-04: el primer parametro va CASTEADO
# (`%s::platform IS NULL`). Sin el cast Postgres no puede inferir su tipo y la
# consulta revienta con `IndeterminateDatatype: could not determine data type
# of parameter $1` — la herramienta NUNCA habia corrido contra una base real
# porque sus tests usan una conexion falsa (`_ConnFalsa`): validaban el
# plumbing de Python, jamas el SQL. Hay un test contra Postgres de verdad.
_SQL_PLAN = """
SELECT v.id, v.platform, v.kind, v.keyword_text, e.match_type, v.external_id,
       v.ad_group_id, g.external_id, v.campaign_id, c.external_id,
       s.current_bid, s.bid_currency, v.clasificacion, v.dias_sin_impresiones
  FROM v_entidad_inerte v
  JOIN ad_entity e ON e.id = v.id
  JOIN ad_entity g ON g.id = v.ad_group_id
  JOIN ad_entity c ON c.id = v.campaign_id
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = v.id
 WHERE v.kind = 'keyword'
   AND (%s::platform IS NULL OR v.platform = %s::platform)
   AND v.clasificacion = %s
   AND (v.dias_sin_impresiones IS NULL OR v.dias_sin_impresiones >= %s)
 ORDER BY v.platform, v.dias_sin_impresiones NULLS FIRST, v.id
"""

_SQL_EXCLUIDOS = """
SELECT count(*) FROM v_entidad_inerte
 WHERE kind = 'product_target'
   AND (%s::platform IS NULL OR platform = %s::platform)
   AND clasificacion = %s
   AND (dias_sin_impresiones IS NULL OR dias_sin_impresiones >= %s)
"""

_SQL_INSERT_PLANEADO = """
INSERT INTO keyword_archivo_manual
  (lote, ad_entity_id, platform, campaign_external, ad_group_external,
   keyword_external, keyword_text, match_type, bid, bid_currency,
   clasificacion, dias_sin_impresiones, go_literal, estado)
VALUES (%s, %s, %s::platform, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'planeado')
RETURNING id
"""

_SQL_SELLA_APPLIED = """
UPDATE keyword_archivo_manual
   SET estado = 'applied', ack = %s::jsonb, readback_estado = %s
 WHERE id = %s
"""

_SQL_SELLA_FAILED = """
UPDATE keyword_archivo_manual
   SET estado = 'failed', ack = %s::jsonb, readback_estado = %s
 WHERE id = %s
"""

_SQL_REPONER = """
SELECT id, ad_entity_id, platform, campaign_external, ad_group_external,
       keyword_external, keyword_text, match_type, bid, bid_currency,
       clasificacion, dias_sin_impresiones
  FROM keyword_archivo_manual
 WHERE lote = %s AND estado = 'applied'
 ORDER BY id
"""

_SQL_SELLA_REPUESTO = """
UPDATE keyword_archivo_manual
   SET estado = 'repuesto', repuesto_at = now(),
       repuesto_external = %s, repuesto_ack = %s::jsonb
 WHERE id = %s
"""


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


def _dsn_admin() -> str:
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        raise Abortar("ORBIT_DSN_ADMIN no esta en el entorno (corre dentro del contenedor app)")
    return dsn


def _perfiles(cliente_lectura: AdsClient) -> dict[str, int]:
    """platform -> profile_id ACEPTADO (una fuente: evaluar_perfiles)."""
    out: dict[str, int] = {}
    for p in evaluar_perfiles(cliente_lectura):
        if p.aceptado and p.platform and p.profile_id is not None:
            out[p.platform] = p.profile_id
    return out


def _plan_inertes(
    conn: psycopg.Connection,
    plataforma: str | None,
    clasificacion: str,
    min_dias: int,
    limite: int | None,
) -> tuple[list[dict], int]:
    """Candidatas a archivar desde v_entidad_inerte (regla 2: la vista es
    la unica fuente) + conteo de product_target excluidos con los MISMOS
    filtros. dias NULL = nunca impresiono en 90d: pasa como infinito (es
    el caso mas muerto; excluirlo vaciaria peso_muerto)."""
    sql = _SQL_PLAN + (" LIMIT %s" if limite is not None else "")
    params: tuple = (plataforma, plataforma, clasificacion, min_dias)
    if limite is not None:
        params = (*params, limite)
    filas = conn.execute(sql, params).fetchall()
    plan = [
        {
            "id": f[0],
            "platform": f[1],
            "kind": f[2],
            "texto": f[3],
            "match": f[4],
            "external_id": f[5],
            "ad_group_id": f[6],
            "ad_group_external": f[7],
            "campaign_id": f[8],
            "campaign_external": f[9],
            "bid": f[10],
            "bid_currency": f[11],
            "clasificacion": f[12],
            "dias": f[13],
        }
        for f in filas
    ]
    excluidos = conn.execute(
        _SQL_EXCLUIDOS, (plataforma, plataforma, clasificacion, min_dias)
    ).fetchone()[0]
    return plan, excluidos


def _huella_conjunto(plan: list[dict]) -> str:
    """Huella del CONJUNTO autorizado (BIDS 01 2.4, H4): sha256 de
    `platform:external_id` ordenadas. Con `platform` porque el external_id
    solo es unico con ella (mismo id en MX y US es posible)."""
    ids = sorted(f"{p['platform']}:{p['external_id']}" for p in plan)
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def _linea_plan(p: dict) -> str:
    bid = f"{p['bid']} {p['bid_currency']}" if p["bid"] is not None else "sin bid"
    return (
        f"{p['platform']} | {p['campaign_external']} | {p['ad_group_external']} | "
        f"{p['texto']} | {p['match']} | {p['external_id']} | "
        f"{p['clasificacion']} | dias={p['dias']} | {bid}"
    )


def _post(
    client: httpx.Client,
    token: str,
    cred: AdsCredentials,
    profile: int,
    path: str,
    payload: dict,
    envolver: str | None = None,
) -> dict:
    """POST de mutacion con el vendor v3 EXACTO en Content-Type Y Accept
    (sin Accept la API responde 415 - sello de reactiva_campanas). Con
    `envolver`, el objeto viaja como unica entrada de la lista bajo esa
    clave (sello del probe 2.5: objeto desnudo = 400); sin el, viaja tal
    cual (filtros de /delete, sello de borrar_keyword)."""
    if envolver is not None:
        payload = {envolver: [payload]}
    resp = client.post(
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Amazon-Advertising-API-ClientId": cred.client_id,
            "Amazon-Advertising-API-Scope": str(profile),
            "Content-Type": VENDOR_KEYWORDS,
            "Accept": VENDOR_KEYWORDS,
        },
        json=payload,
    )
    cuerpo: dict = {}
    with contextlib.suppress(ValueError):
        cuerpo = resp.json()
    return {"status": resp.status_code, "cuerpo": cuerpo, "texto": scrub(resp.text[:400])}


def _errores_207(ack: dict) -> list:
    """Todos los errores del 207 multi-status, cualquiera sea el contenedor
    (mismo criterio que el aplicador: success/error ANIDADOS)."""
    cuerpo = ack.get("cuerpo")
    if not isinstance(cuerpo, dict):
        return [{"no_json": ack.get("texto")}]
    errores: list = []
    for valor in cuerpo.values():
        if isinstance(valor, dict):
            errores.extend(valor.get("error") or [])
    return errores


def _ack_ok(ack: dict) -> bool:
    return ack.get("status") in (200, 207) and not _errores_207(ack)


def _readback_salvo(cliente_lectura: AdsClient, profile: int, keyword_external: str) -> dict | None:
    """LIST que no lanza: el cliente real lanza AdsApiError en >=400, asi
    que un error de red/API es None y el caller lo trata como readback
    ausente (fail-closed), jamas como traceback crudo."""
    try:
        return _readback_keyword(cliente_lectura, profile, keyword_external)
    except Exception:
        return None


def _readback_keyword(
    cliente_lectura: AdsClient, profile: int, keyword_external: str
) -> dict | None:
    """Objeto vivo de la keyword por POST /sp/keywords/list (el GET directo
    da 403 - sello del probe 2.5). None = la API no respondio."""
    resp = cliente_lectura.list_objects(
        "/sp/keywords/list",
        {"keywordIdFilter": {"include": [keyword_external]}},
        profile_id=profile,
    )
    if resp.status_code != 200:
        return None
    for fila in resp.json().get("keywords") or []:
        if str(fila.get("keywordId")) == str(keyword_external):
            return fila
    return None


def _id_creado_de_ack(ack: dict) -> str | None:
    """El keywordId del objeto creado segun el ack 207 (success ANIDADOS
    por recurso; el id vive en el primer success - criterio del
    aplicador). None = ack sin id legible (regla 3: jamas inventado)."""
    cuerpo = ack.get("cuerpo")
    if not isinstance(cuerpo, dict):
        return None
    for valor in cuerpo.values():
        if not isinstance(valor, dict):
            continue
        success = valor.get("success")
        if not isinstance(success, list):
            continue
        for item in success:
            if not isinstance(item, dict):
                continue
            if item.get("keywordId") is not None:
                return str(item["keywordId"])
            for sub in item.values():  # {"keyword": {"keywordId": ...}}
                if isinstance(sub, dict) and sub.get("keywordId") is not None:
                    return str(sub["keywordId"])
    return None


def _bid_wire(bid: Decimal) -> float:
    """Bid para el PAYLOAD JSON: NUMERO cuantizado a 2 decimales (misma
    presentacion sellada que el cliente de escritura: el NUMERIC del
    ledger trae 4). Es encoding final, no aritmetica: float prohibido
    para guardar o decidir (regla 4)."""
    if not isinstance(bid, Decimal):
        raise TypeError(f"bid debe ser Decimal, no {type(bid).__name__}")
    return float(bid.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def _lote_hoy() -> str:
    return f"inertes-{datetime.datetime.now(datetime.UTC).date().isoformat()}"


def _inserta_planeado(conn: psycopg.Connection, lote: str, p: dict, go: str) -> int:
    """Intencion durable ANTES del HTTP (regla 7): si el POST falla, la
    fila 'planeado' ya existe con commit. Los NULL viajan (regla 3)."""
    fila = conn.execute(
        _SQL_INSERT_PLANEADO,
        (
            lote,
            p["id"],
            p["platform"],
            p["campaign_external"],
            p["ad_group_external"],
            p["external_id"],
            p["texto"],
            p["match"],
            p["bid"],
            p["bid_currency"],
            p["clasificacion"],
            p["dias"],
            go,
        ),
    ).fetchone()
    conn.commit()
    return fila[0]


def _sella(conn: psycopg.Connection, sql: str, params: tuple) -> None:
    conn.execute(sql, params)
    conn.commit()


def _archivar(args, conn_read: psycopg.Connection) -> int:
    plan, excluidos = _plan_inertes(
        conn_read,
        args.plataforma,
        args.clasificacion,
        args.min_dias_sin_impresiones,
        args.limite,
    )
    # La lectura termino: se cierra su txn ANTES de la fase de red (el plan
    # ya vive en memoria; nada que escribir en esta conn).
    conn_read.commit()
    lote = _lote_hoy()
    for p in plan:
        print(_linea_plan(p), flush=True)
    if excluidos:
        print(f"excluidas product_target (solo se reportan): {excluidos}", flush=True)
    huella = _huella_conjunto(plan)
    print(f"huella del conjunto: {huella}", flush=True)
    _log("plan", lote=lote, candidatas=len(plan), excluidas_targets=excluidos, huella=huella)

    if not args.acepto_mutacion_real:
        _log(
            "dry_run",
            lote=lote,
            candidatas=len(plan),
            excluidas_targets=excluidos,
            huella=huella,
            nota="sin --acepto-mutacion-real no se toca Amazon",
        )
        return 0

    if args.esperado is None:
        raise Abortar("mutacion real exige --esperado N (anti-typo del lote)")
    if not args.go:
        raise Abortar("mutacion real exige --go con el literal del dueno (no vacio)")
    if len(plan) != args.esperado:
        raise Abortar(
            f"--esperado {args.esperado} != candidatas del plan {len(plan)}: "
            "el plan cambio, se re-autoriza con el dueno"
        )
    if not args.huella:
        raise Abortar("mutacion real exige --huella del dry-run (autorizacion por conjunto)")
    if args.huella != huella:
        raise Abortar(
            f"--huella {args.huella} != huella del plan {huella}: "
            "el conjunto cambio (salen y entran con el mismo N), se re-autoriza con el dueno"
        )

    cred = AdsCredentials.from_secrets_dir()
    conn_admin = connect(_dsn_admin())
    cliente_lectura = AdsClient(cred)
    http = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0))

    perfiles = _perfiles(cliente_lectura)
    _log("perfiles", perfiles=perfiles)
    for p in plan:
        if p["platform"] not in perfiles:
            raise Abortar(f"sin perfil aceptado para {p['platform']} ({p['external_id']})")

    token = _token_lwa(cred, http)

    archivadas = 0
    saltadas = 0
    for p in plan:
        profile = perfiles[p["platform"]]
        vivo = _readback_salvo(cliente_lectura, profile, p["external_id"])
        if vivo is None:
            raise Abortar(
                f"LIST previo de {p['external_id']} no respondio: sin estado vivo no se muta"
            )
        estado_vivo = vivo.get("state")
        if estado_vivo != _ESTADO_VIVO:
            _log(
                "skip_no_enabled",
                external_id=p["external_id"],
                texto=p["texto"],
                estado=estado_vivo,
                nota="ya no esta viva en Amazon: no se pisa decision ajena",
            )
            saltadas += 1
            continue
        fila_id = _inserta_planeado(conn_admin, lote, p, args.go)
        try:
            ack = _post(
                http,
                token,
                cred,
                profile,
                "/sp/keywords/delete",
                {"keywordIdFilter": {"include": [str(p["external_id"])]}},
            )
        except Exception as exc:  # red caida a mitad: la intencion ya es durable
            ack = {"status": "excepcion", "cuerpo": {}, "texto": scrub(str(exc))[:300]}
        if not _ack_ok(ack):
            _sella(conn_admin, _SQL_SELLA_FAILED, (json.dumps(ack, default=str), None, fila_id))
            motivo = (
                f"DELETE de {p['external_id']} rechazado "
                f"(status {ack.get('status')}): el lote se detiene"
            )
            _log("archivo", external_id=p["external_id"], ok=False, motivo=motivo)
            _log(
                "lote_detenido", lote=lote, motivo=motivo, archivadas=archivadas, saltadas=saltadas
            )
            raise Abortar(motivo)
        time.sleep(0.3)  # cortesia de rate limit
        leido = _readback_salvo(cliente_lectura, profile, p["external_id"])
        readback = leido.get("state") if leido else None
        _log(
            "archivo",
            external_id=p["external_id"],
            texto=p["texto"],
            ack=ack["cuerpo"],
            readback=readback,
            ok=readback == _ESTADO_ARCHIVADO,
        )
        if readback != _ESTADO_ARCHIVADO:
            _sella(conn_admin, _SQL_SELLA_FAILED, (json.dumps(ack, default=str), readback, fila_id))
            motivo = f"readback de {p['external_id']} != ARCHIVED ({readback}): el lote se detiene"
            _log(
                "lote_detenido", lote=lote, motivo=motivo, archivadas=archivadas, saltadas=saltadas
            )
            raise Abortar(motivo)
        _sella(conn_admin, _SQL_SELLA_APPLIED, (json.dumps(ack, default=str), readback, fila_id))
        archivadas += 1

    _log("reconciliacion_final", lote=lote, archivadas=archivadas, saltadas=saltadas, ok=True)
    return 0


def _fila_reponer(f: tuple) -> dict:
    return {
        "id": f[0],
        "ad_entity_id": f[1],
        "platform": f[2],
        "campaign_external": f[3],
        "ad_group_external": f[4],
        "keyword_external": f[5],
        "texto": f[6],
        "match": f[7],
        "bid": f[8],
        "bid_currency": f[9],
        "clasificacion": f[10],
        "dias": f[11],
    }


def _reponer(args) -> int:
    conn_admin = connect(_dsn_admin())
    filas = [_fila_reponer(f) for f in conn_admin.execute(_SQL_REPONER, (args.reponer,)).fetchall()]
    # La lectura termino: se cierra su txn ANTES del bucle HTTP (las filas
    # ya viven en memoria; los sellos commitean por separado).
    conn_admin.commit()
    if not filas:
        raise Abortar(f"lote {args.reponer} sin filas 'applied': nada que reponer")
    for f in filas:
        print(
            f"{f['platform']} | {f['campaign_external']} | {f['ad_group_external']} | "
            f"{f['texto']} | {f['match']} | {f['keyword_external']} | "
            f"bid={f['bid']} {f['bid_currency']}",
            flush=True,
        )
    _log("plan_reponer", lote=args.reponer, candidatas=len(filas))

    if not args.acepto_mutacion_real:
        _log(
            "dry_run",
            modo="reponer",
            lote=args.reponer,
            candidatas=len(filas),
            nota="sin --acepto-mutacion-real no se crea nada",
        )
        return 0

    cred = AdsCredentials.from_secrets_dir()
    cliente_lectura = AdsClient(cred)
    http = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0))

    perfiles = _perfiles(cliente_lectura)
    _log("perfiles", perfiles=perfiles)
    for f in filas:
        if f["platform"] not in perfiles:
            raise Abortar(f"sin perfil aceptado para {f['platform']} ({f['keyword_external']})")

    # Validacion SIN HTTP del lote completo ANTES del token: un lote
    # condenado no quema ni un request (fail-closed temprano).
    for f in filas:
        if f["match"] not in MATCH_VALIDOS:
            motivo = (
                f"fila {f['id']} con match {f['match']!r} fuera de "
                f"{sorted(MATCH_VALIDOS)}: el lote se detiene"
            )
            _log("lote_detenido", lote=args.reponer, motivo=motivo, repuestas=0)
            raise Abortar(motivo)
        if f["bid"] is None or f["bid_currency"] is None:
            motivo = (
                f"fila {f['id']} ({f['keyword_external']}) sin bid en el "
                "ledger: no se inventa (regla 3), el lote se detiene"
            )
            _log("reponer_sin_bid", fila=f["id"], external_id=f["keyword_external"], motivo=motivo)
            _log("lote_detenido", lote=args.reponer, motivo=motivo, repuestas=0)
            raise Abortar(motivo)
        if MONEDA_POR_PLATAFORMA.get(f["platform"]) != f["bid_currency"]:
            motivo = (
                f"fila {f['id']}: moneda {f['bid_currency']!r} != "
                f"moneda de {f['platform']}: no se escribe (regla 4)"
            )
            _log("lote_detenido", lote=args.reponer, motivo=motivo, repuestas=0)
            raise Abortar(motivo)

    token = _token_lwa(cred, http)

    repuestas = 0
    for f in filas:
        profile = perfiles[f["platform"]]
        try:
            ack = _post(
                http,
                token,
                cred,
                profile,
                "/sp/keywords",
                {
                    "adGroupId": str(f["ad_group_external"]),
                    "campaignId": str(f["campaign_external"]),
                    "keywordText": f["texto"],
                    "matchType": f["match"],
                    "state": _ESTADO_VIVO,
                    "bid": _bid_wire(f["bid"]),
                },
                envolver="keywords",
            )
        except Exception as exc:
            ack = {"status": "excepcion", "cuerpo": {}, "texto": scrub(str(exc))[:300]}
        if not _ack_ok(ack):
            motivo = (
                f"CREATE de {f['keyword_external']} rechazado "
                f"(status {ack.get('status')}): el lote se detiene"
            )
            _log(
                "reponer",
                external_id=f["keyword_external"],
                ack=ack["cuerpo"],
                ok=False,
                motivo=motivo,
            )
            _log("lote_detenido", lote=args.reponer, motivo=motivo, repuestas=repuestas)
            raise Abortar(motivo)
        nuevo = _id_creado_de_ack(ack)
        if nuevo is None:
            motivo = (
                f"el ack del CREATE de {f['keyword_external']} no trae "
                "keywordId: sin id no hay readback, el lote se detiene"
            )
            _log(
                "reponer",
                external_id=f["keyword_external"],
                ack=ack["cuerpo"],
                ok=False,
                motivo=motivo,
            )
            _log("lote_detenido", lote=args.reponer, motivo=motivo, repuestas=repuestas)
            raise Abortar(motivo)
        time.sleep(0.3)  # cortesia de rate limit
        leido = _readback_salvo(cliente_lectura, profile, nuevo)
        cuadra = (
            leido is not None
            and leido.get("state") == _ESTADO_VIVO
            and str(leido.get("keywordText")) == f["texto"]
            and leido.get("matchType") == f["match"]
            and str(leido.get("adGroupId")) == str(f["ad_group_external"])
        )
        _log(
            "reponer",
            external_id=f["keyword_external"],
            nuevo_external=nuevo,
            ack=ack["cuerpo"],
            leido=leido,
            ok=cuadra,
        )
        if not cuadra:
            motivo = (
                f"readback de la repuesta {nuevo} no cuadra con el ledger "
                "(texto+match+grupo): el lote se detiene"
            )
            _log("lote_detenido", lote=args.reponer, motivo=motivo, repuestas=repuestas)
            raise Abortar(motivo)
        _sella(conn_admin, _SQL_SELLA_REPUESTO, (nuevo, json.dumps(ack, default=str), f["id"]))
        repuestas += 1

    _log("reconciliacion_final", lote=args.reponer, repuestas=repuestas, ok=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--plataforma",
        default=None,
        choices=("amazon_mx", "amazon_us"),
        help="filtra el plan a una plataforma (default: las dos)",
    )
    ap.add_argument("--clasificacion", default="peso_muerto", choices=CLASIFICACIONES)
    ap.add_argument("--min-dias-sin-impresiones", type=int, default=30)
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument(
        "--acepto-mutacion-real",
        action="store_true",
        help="obligatorio para tocar Amazon; sin el = dry-run",
    )
    ap.add_argument(
        "--esperado",
        type=int,
        default=None,
        help="candidatas que el dueno autorizo viendo el dry-run",
    )
    ap.add_argument(
        "--go", default=None, help="literal del dueno que autoriza el lote (va al ledger)"
    )
    ap.add_argument(
        "--huella",
        default=None,
        help="huella del conjunto que publico el dry-run (si el conjunto "
        "cambio, el go aborta aunque N coincida)",
    )
    ap.add_argument("--reponer", default=None, help="lote del ledger a recrear (reversa)")
    args = ap.parse_args()

    if args.reponer is not None:
        return _reponer(args)
    conn_read = connect(_dsn_read())
    return _archivar(args, conn_read)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        _log("ABORTAR_FAIL_CLOSED", motivo=str(exc))
        sys.exit(2)
