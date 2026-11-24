"""Archivo MANUAL de keywords inertes con reversa (BIDS 01, D5).

Motor del CLI `tools/archiva_inertes.py`: construye el plan desde
`v_entidad_inerte` (solo `kind='keyword'`), valida la ceremonia con
`app.ceremonia`, registra la intencion durable en `keyword_archivo_manual`
antes de mutar Amazon por HTTP v3 y ofrece reconciliacion (`--reconciliar`) y
reposicion (`--reponer`). Amazon NO des-archiva: archivar es IRREVERSIBLE en la
practica y `--reponer` CREA otra keyword en estado PAUSED sin historia.

ARREGLOS 05 D: las tres mutaciones (DELETE del archivo, POST de la reposicion y
POST de su reversa) salen por `app.ads.write.AdsWriteClient` con el perfil
sellado; aqui solo viven la ceremonia, el ledger y la traduccion del payload
durable de C a la operacion tipada.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Collection
from decimal import Decimal
from typing import Any

import psycopg

from app.ads import archivar, reposicion, sp_wire
from app.ads.client import AdsApiError, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import evaluar_perfiles
from app.ads.write import MODO_CONFIRMADO_LIVE, AdsWriteClient
from app.ceremonia import ErrorCeremonia, agrega_flags_ceremonia, valida_ceremonia
from app.db import connect
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import install_scrub_filter, scrub

install_scrub_filter(logging.getLogger())

CLASIFICACIONES = ("peso_muerto", "gasto_sin_ventas", "con_ventas_previas")

_ESTADO_VIVO = "ENABLED"
_ESTADO_ARCHIVADO = "ARCHIVED"

_SQL_PLAN = """
SELECT v.id, v.platform, v.kind, v.keyword_text, e.match_type, v.external_id,
       v.ad_group_id, g.external_id, v.campaign_id, c.external_id,
       s.current_bid, s.bid_currency, v.clasificacion, v.dias_sin_impresiones,
       e.first_seen_at
  FROM v_entidad_inerte v
  JOIN ad_entity e ON e.id = v.id
  JOIN ad_entity g ON g.id = v.ad_group_id
  JOIN ad_entity c ON c.id = v.campaign_id
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = v.id
 WHERE v.kind = 'keyword'
   AND (%s::platform IS NULL OR v.platform = %s::platform)
   AND v.clasificacion = %s
   AND (v.dias_sin_impresiones IS NULL OR v.dias_sin_impresiones >= %s)
   AND e.first_seen_at <= (now() AT TIME ZONE 'UTC')::date - %s
 ORDER BY v.platform, v.dias_sin_impresiones NULLS FIRST, v.id
"""

_SQL_EXCLUIDOS = """
SELECT count(*) FROM v_entidad_inerte
 WHERE kind = 'product_target'
   AND (%s::platform IS NULL OR platform = %s::platform)
   AND clasificacion = %s
   AND (dias_sin_impresiones IS NULL OR dias_sin_impresiones >= %s)
"""

_SQL_EXCLUIDOS_JOVENES = """
SELECT count(*)
  FROM v_entidad_inerte v
  JOIN ad_entity e ON e.id = v.id
 WHERE v.kind = 'keyword'
   AND (%s::platform IS NULL OR v.platform = %s::platform)
   AND v.clasificacion = %s
   AND (v.dias_sin_impresiones IS NULL OR v.dias_sin_impresiones >= %s)
   AND e.first_seen_at > (now() AT TIME ZONE 'UTC')::date - %s
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

_SQL_PENDIENTES = """
SELECT id, lote, platform, keyword_external
  FROM keyword_archivo_manual
 WHERE estado IN ('planeado', 'failed')
   AND (%s::text IS NULL OR lote = %s)
 ORDER BY id
"""

_SQL_PROMUEVE_APPLIED = """
UPDATE keyword_archivo_manual
   SET estado = 'applied', ack = %s::jsonb, readback_estado = %s
 WHERE id = %s AND estado IN ('planeado', 'failed')
"""


class Abortar(RuntimeError):
    """Fail-closed: la realidad difiere del plan o la API rechaza."""


def _cerrar(recurso: Any) -> None:
    close = getattr(recurso, "close", None)
    if callable(close):
        close()


def _cerra_escritores(escritores: dict[str, AdsWriteClient]) -> None:
    for escritor in escritores.values():
        escritor.close()


def _log(evento: str, **campos: Any) -> None:
    print(scrub(json.dumps({"evento": evento, **campos}, default=str)), flush=True)


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


def _perfiles(
    cliente_lectura: AdsClient,
    *,
    plataformas: Collection[str] | None = None,
) -> dict[str, int]:
    """platform -> profile_id ACEPTADO (fail-closed ante duplicados del mercado)."""
    todos = evaluar_perfiles(cliente_lectura)
    out: dict[str, int] = {}
    paises_aceptados: dict[str, str] = {}
    for p in todos:
        if p.aceptado and p.platform and p.profile_id is not None:
            if p.platform in out and out[p.platform] != p.profile_id:
                if plataformas is None or p.platform in plataformas:
                    raise Abortar(
                        f"multiples perfiles aceptados para {p.platform}: "
                        f"{out[p.platform]} y {p.profile_id} (fail-closed)"
                    )
                out.pop(p.platform, None)
                continue
            out[p.platform] = p.profile_id
            pais = getattr(p, "country", None)
            if pais:
                paises_aceptados[pais] = p.platform
    for p in todos:
        pais = getattr(p, "country", None)
        motivo = getattr(p, "motivo", None) or ""
        if not p.aceptado and pais in paises_aceptados and "duplicado" in motivo:
            plat = paises_aceptados[pais]
            if plataformas is None or plat in plataformas:
                raise Abortar(f"multiples perfiles para pais {pais} ({plat}): fail-closed")
            out.pop(plat, None)
    return out


def _edad_dias(primera_vista) -> int | None:
    """Edad en dias desde first_seen_at hasta hoy UTC (None si ausente)."""
    if primera_vista is None:
        return None
    if isinstance(primera_vista, datetime.datetime):
        primera_vista = primera_vista.date()
    return (datetime.datetime.now(datetime.UTC).date() - primera_vista).days


def _plan_inertes(
    conn: psycopg.Connection,
    plataforma: str | None,
    clasificacion: str,
    min_dias: int,
    limite: int | None,
    min_antiguedad: int = 30,
) -> tuple[list[dict], int, int]:
    """Candidatas desde v_entidad_inerte + product_target excluidos + jovenes."""
    sql = _SQL_PLAN + (" LIMIT %s" if limite is not None else "")
    params: tuple = (plataforma, plataforma, clasificacion, min_dias, min_antiguedad)
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
            "primera_vista": f[14],
        }
        for f in filas
    ]
    excluidos = conn.execute(
        _SQL_EXCLUIDOS, (plataforma, plataforma, clasificacion, min_dias)
    ).fetchone()[0]
    jovenes = conn.execute(
        _SQL_EXCLUIDOS_JOVENES,
        (plataforma, plataforma, clasificacion, min_dias, min_antiguedad),
    ).fetchone()[0]
    return plan, excluidos, jovenes


def _huella_conjunto(plan: list[dict]) -> str:
    """Huella sha256 de `platform:external_id` ordenadas."""
    ids = sorted(f"{p['platform']}:{p['external_id']}" for p in plan)
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def _linea_plan(p: dict) -> str:
    bid = f"{p['bid']} {p['bid_currency']}" if p["bid"] is not None else "sin bid"
    edad = _edad_dias(p.get("primera_vista"))
    edad_txt = f"edad={edad}d" if edad is not None else "edad=?d"
    return (
        f"{p['platform']} | {p['campaign_external']} | {p['ad_group_external']} | "
        f"{p['texto']} | {p['match']} | {p['external_id']} | "
        f"{p['clasificacion']} | dias={p['dias']} | {edad_txt} | {bid}"
    )


def _escritores(
    cred: AdsCredentials, perfiles: dict[str, int], *, modo_confirmado: str
) -> dict[str, AdsWriteClient]:
    """UN escritor sellado por plataforma con perfil aceptado (ARREGLOS 05 D)."""
    return {
        plataforma: AdsWriteClient(
            cred,
            platform=plataforma,
            profile_id=perfil,
            modo_confirmado=modo_confirmado,
        )
        for plataforma, perfil in perfiles.items()
    }


def _ack_de_mutacion(
    llamada,
) -> dict:
    """ACK canonico de una mutacion: un rechazo o un fallo tambien es un ACK."""
    try:
        return sp_wire.ack_de(llamada())
    except AdsApiError as exc:
        return sp_wire.ack_de_error(exc)


def _enviar_keyword(escritores: dict[str, AdsWriteClient]) -> reposicion.Enviar:
    """Traduce el payload durable de la reposicion (bloque C) al cliente.

    La moneda sale del mapa unico de plataformas: el cliente la revalida antes
    del HTTP. El perfil que recibe `Enviar` se ignora a proposito: el scope ya
    vive sellado en el escritor de esa plataforma.
    """

    def enviar(plataforma: str, _profile: int, payload: dict) -> dict:
        escritor = escritores[plataforma]
        return _ack_de_mutacion(
            lambda: escritor.crear_keyword(
                payload["adGroupId"],
                payload["campaignId"],
                payload["keywordText"],
                Decimal(str(payload["bid"])),
                PLATAFORMAS_MONEDA[plataforma],
                match_type=payload["matchType"],
                estado=payload["state"],
            )
        )

    return enviar


def _eliminar_keyword(escritores: dict[str, AdsWriteClient]) -> reposicion.Enviar:
    """Reversa de la reposicion: archiva SOLO el id repuesto propio."""

    def eliminar(plataforma: str, _profile: int, payload: dict) -> dict:
        (keyword_id,) = payload["keywordIdFilter"]["include"]
        escritor = escritores[plataforma]
        return _ack_de_mutacion(lambda: escritor.borrar_keyword(keyword_id))

    return eliminar


def _errores_207(ack: dict) -> list:
    """Errores del 207 multi-status (reusa `sp_wire.errores_de_ack`)."""
    cuerpo = ack.get("cuerpo")
    if not isinstance(cuerpo, dict):
        return [{"no_json": ack.get("texto")}]
    return sp_wire.errores_de_ack(cuerpo)


def _ack_ok(ack: dict) -> bool:
    return ack.get("status") in (200, 207) and not _errores_207(ack)


def _readback_salvo(cliente_lectura: AdsClient, profile: int, keyword_external: str) -> dict | None:
    """LIST que devuelve None ante error de red/API (fail-closed)."""
    try:
        return _readback_keyword(cliente_lectura, profile, keyword_external)
    except Exception:
        return None


def _readback_keyword(
    cliente_lectura: AdsClient, profile: int, keyword_external: str
) -> dict | None:
    """Objeto vivo de la keyword por POST /sp/keywords/list."""
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


def _lote_hoy() -> str:
    return f"inertes-{datetime.datetime.now(datetime.UTC).date().isoformat()}"


def _inserta_planeado(conn: psycopg.Connection, lote: str, p: dict, go: str) -> int:
    """Intencion durable ANTES del HTTP (regla 7)."""
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
    plan, excluidos, jovenes = _plan_inertes(
        conn_read,
        args.plataforma,
        args.clasificacion,
        args.min_dias_sin_impresiones,
        args.limite,
        args.min_antiguedad_dias,
    )
    conn_read.commit()
    lote = _lote_hoy()
    for p in plan:
        print(_linea_plan(p), flush=True)
    if excluidos:
        print(f"excluidas product_target (solo se reportan): {excluidos}", flush=True)
    if jovenes:
        print(
            f"excluidas jovenes (first_seen_at posterior a hoy - {args.min_antiguedad_dias}d,"
            " en espera, NO archivadas): "
            f"{jovenes}",
            flush=True,
        )
    huella = _huella_conjunto(plan)
    print(f"huella del conjunto: {huella}", flush=True)
    _log(
        "plan",
        lote=lote,
        candidatas=len(plan),
        excluidas_targets=excluidos,
        excluidas_jovenes=jovenes,
        huella=huella,
    )

    try:
        mutar = valida_ceremonia(
            acepto=bool(args.acepto_mutacion_real),
            go=args.go,
            huella=args.huella,
            huella_plan=huella,
            esperado=args.esperado,
            real=len(plan),
            exige_go=True,
            exige_huella=True,
            exige_esperado=True,
            rechaza_sin_acepto=False,
        )
    except ErrorCeremonia as exc:
        raise Abortar(str(exc)) from exc

    if not mutar:
        _log(
            "dry_run",
            lote=lote,
            candidatas=len(plan),
            excluidas_targets=excluidos,
            excluidas_jovenes=jovenes,
            huella=huella,
            nota="sin --acepto-mutacion-real no se toca Amazon",
        )
        return 0

    cred = AdsCredentials.from_secrets_dir()
    cliente_lectura = AdsClient(cred)
    with contextlib.ExitStack() as stack:
        conn_admin = connect(_dsn_admin())
        stack.callback(_cerrar, conn_admin)

        perfiles = _perfiles(cliente_lectura, plataformas={p["platform"] for p in plan})
        _log("perfiles", perfiles=perfiles)
        for p in plan:
            if p["platform"] not in perfiles:
                raise Abortar(f"sin perfil aceptado para {p['platform']} ({p['external_id']})")

        # La ceremonia ya autorizo la mutacion: ese es el modo real que recibe
        # el escritor (ARREGLOS 05 D).
        escritores = _escritores(cred, perfiles, modo_confirmado=MODO_CONFIRMADO_LIVE)
        stack.callback(_cerra_escritores, escritores)

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
                ack = _ack_de_mutacion(
                    lambda p=p: escritores[p["platform"]].borrar_keyword(p["external_id"])
                )
            except Exception as exc:  # noqa: BLE001 - se sella como incierto
                ack = {
                    "status": sp_wire.ACK_SIN_RESPUESTA,
                    "cuerpo": {},
                    "texto": scrub(str(exc))[:300],
                }
            if not _ack_ok(ack):
                _sella(conn_admin, _SQL_SELLA_FAILED, (json.dumps(ack, default=str), None, fila_id))
                motivo = (
                    f"DELETE de {p['external_id']} rechazado "
                    f"(status {ack.get('status')}): el lote se detiene"
                )
                _log("archivo", external_id=p["external_id"], ok=False, motivo=motivo)
                _log(
                    "lote_detenido",
                    lote=lote,
                    motivo=motivo,
                    archivadas=archivadas,
                    saltadas=saltadas,
                )
                raise Abortar(motivo)
            time.sleep(0.3)
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
                _sella(
                    conn_admin,
                    _SQL_SELLA_FAILED,
                    (json.dumps(ack, default=str), readback, fila_id),
                )
                motivo = (
                    f"readback de {p['external_id']} != ARCHIVED ({readback}): el lote se detiene"
                )
                _log(
                    "lote_detenido",
                    lote=lote,
                    motivo=motivo,
                    archivadas=archivadas,
                    saltadas=saltadas,
                )
                raise Abortar(motivo)
            _sella(
                conn_admin,
                _SQL_SELLA_APPLIED,
                (json.dumps(ack, default=str), readback, fila_id),
            )
            archivadas += 1

        _log("reconciliacion_final", lote=lote, archivadas=archivadas, saltadas=saltadas, ok=True)
    return 0


def _reconciliar(
    conn_admin: psycopg.Connection,
    cliente_lectura: AdsClient,
    perfiles: dict[str, int],
    lote: str | None,
) -> dict:
    """Cruza pendientes (`planeado`/`failed`) contra el LIST real (H3)."""
    filas = conn_admin.execute(_SQL_PENDIENTES, (lote, lote)).fetchall()
    conn_admin.commit()
    resumen = {"pendientes": len(filas), "recuperadas": 0, "vivas": 0, "sin_verificar": 0}
    for fid, lote_fila, platform, kw_ext in filas:
        plataforma = str(platform)
        profile = perfiles.get(plataforma)
        if profile is None:
            _log(
                "reconciliar_sin_perfil",
                fila=fid,
                lote=lote_fila,
                platform=plataforma,
                external_id=str(kw_ext),
                nota="sin perfil aceptado no hay LIST: intacta",
            )
            resumen["sin_verificar"] += 1
            continue
        vivo = _readback_salvo(cliente_lectura, profile, str(kw_ext))
        if vivo is None:
            _log(
                "reconciliar_sin_verificar",
                fila=fid,
                lote=lote_fila,
                external_id=str(kw_ext),
                nota="el LIST no respondio: intacta, se re-corre",
            )
            resumen["sin_verificar"] += 1
            continue
        estado = vivo.get("state")
        if estado != _ESTADO_ARCHIVADO:
            _log(
                "reconciliar_viva",
                fila=fid,
                lote=lote_fila,
                external_id=str(kw_ext),
                estado=estado,
                nota="el DELETE no se aplico: intacta, el archivo la retoma",
            )
            resumen["vivas"] += 1
            continue
        ack = {
            "fuente": "reconciliar",
            "state": _ESTADO_ARCHIVADO,
            "keyword_external": str(kw_ext),
            "lote": lote_fila,
        }
        conn_admin.execute(_SQL_PROMUEVE_APPLIED, (json.dumps(ack), _ESTADO_ARCHIVADO, fid))
        conn_admin.commit()
        _log(
            "reconciliar_recuperada",
            fila=fid,
            lote=lote_fila,
            external_id=str(kw_ext),
            nota="archivada en Amazon sin sello: promovida a applied",
        )
        resumen["recuperadas"] += 1
    _log("reconciliacion", lote=lote, **resumen)
    if resumen["sin_verificar"]:
        raise Abortar(
            f"reconciliacion con {resumen['sin_verificar']} fila(s) sin verificar "
            f"(de {resumen['pendientes']}): se re-corre, nada se promovio a ciegas"
        )
    return resumen


def _owner_cli() -> str:
    return f"archiva_inertes:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _resultados_log(evento: str, lote: str | None, resultados) -> None:
    for r in resultados:
        _log(
            evento,
            lote=lote,
            fila=r.archivo_id,
            external_id=r.keyword_external,
            resultado=r.resultado,
            detalle=r.detalle,
            nuevo_external=r.external_id,
        )


def _primer_fallo(resultados) -> str | None:
    ok = ("repuesto", "revertido", "ya_repuesto", "ya_revertido", "reintentable")
    for r in resultados:
        if r.resultado not in ok:
            return f"{r.keyword_external}: {r.resultado} {r.detalle}".strip()
    return None


def _reconciliar_cmd(args) -> int:
    """`--reconciliar [--lote X]`: LISTs, promociones y conciliacion de intentos."""
    conn_admin = connect(_dsn_admin())
    try:
        cred = AdsCredentials.from_secrets_dir()
        cliente_lectura = AdsClient(cred)
        perfiles = _perfiles(cliente_lectura, plataformas=set())
        _log("perfiles", perfiles=perfiles)
        _reconciliar(conn_admin, cliente_lectura, perfiles, args.lote)
        resultados = archivar.reconciliar_reposicion(
            conn_admin,
            lote=args.lote,
            cliente=cliente_lectura,
            perfiles=perfiles,
            owner=_owner_cli(),
        )
        _resultados_log("reconciliar_reposicion", args.lote, resultados)
    finally:
        _cerrar(conn_admin)
    return 0


def _mutar(args, modo: str) -> bool:
    """Ceremonia comun de reponer/revertir. True solo con mutacion autorizada."""
    try:
        mutar = valida_ceremonia(
            acepto=bool(args.acepto_mutacion_real),
            go=args.go,
            exige_go=True,
            exige_huella=False,
            exige_esperado=False,
            rechaza_sin_acepto=False,
        )
    except ErrorCeremonia as exc:
        raise Abortar(str(exc)) from exc
    if not mutar:
        _log(
            "dry_run",
            modo=modo,
            lote=getattr(args, modo),
            nota="sin --acepto-mutacion-real no se toca Amazon",
        )
    return mutar


def _reponer(args) -> int:
    with contextlib.ExitStack() as stack:
        conn_admin = connect(_dsn_admin())
        stack.callback(_cerrar, conn_admin)
        if not _mutar(args, "reponer"):
            resultados = archivar.reponer_keyword(
                conn_admin,
                lote=args.reponer,
                cliente=None,
                perfiles={},
                enviar=None,
                owner=_owner_cli(),
                ejecutar=False,
            )
            _resultados_log("plan_reponer", args.reponer, resultados)
            return 0
        cred = AdsCredentials.from_secrets_dir()
        cliente_lectura = AdsClient(cred)
        perfiles = _perfiles(cliente_lectura, plataformas=set())
        _log("perfiles", perfiles=perfiles)
        escritores = _escritores(cred, perfiles, modo_confirmado=MODO_CONFIRMADO_LIVE)
        stack.callback(_cerra_escritores, escritores)

        # La recuperacion del ledger de archivo sigue siendo parte de --reponer
        # con mutacion: una fila failed archivada en Amazon se promueve antes
        # de leer las filas reponibles.
        _reconciliar(conn_admin, cliente_lectura, perfiles, args.reponer)

        enviar = _enviar_keyword(escritores)

        try:
            resultados = archivar.reponer_keyword(
                conn_admin,
                lote=args.reponer,
                cliente=cliente_lectura,
                perfiles=perfiles,
                enviar=enviar,
                owner=_owner_cli(),
                ejecutar=True,
                avisar=print,
            )
        except (archivar.ReposicionInvalida, archivar.AbortoFase) as exc:
            raise Abortar(str(exc)) from exc
        _resultados_log("reponer", args.reponer, resultados)
        if not resultados:
            _log("sin_candidatas", modo="reponer", lote=args.reponer, ok=True)
            return 0
        fallo = _primer_fallo(resultados)
        if fallo is not None:
            raise Abortar(f"lote {args.reponer} con fallos: {fallo}")
        _log("reconciliacion_final", lote=args.reponer, repuestas=len(resultados), ok=True)
    return 0


def _revertir(args) -> int:
    with contextlib.ExitStack() as stack:
        conn_admin = connect(_dsn_admin())
        stack.callback(_cerrar, conn_admin)
        if not _mutar(args, "revertir_reposicion"):
            resultados = archivar.revertir_reposicion(
                conn_admin,
                lote=args.revertir_reposicion,
                cliente=None,
                perfiles={},
                eliminar=None,
                owner=_owner_cli(),
                ejecutar=False,
            )
            _resultados_log("plan_revertir", args.revertir_reposicion, resultados)
            return 0
        cred = AdsCredentials.from_secrets_dir()
        cliente_lectura = AdsClient(cred)
        perfiles = _perfiles(cliente_lectura, plataformas=set())
        _log("perfiles", perfiles=perfiles)
        escritores = _escritores(cred, perfiles, modo_confirmado=MODO_CONFIRMADO_LIVE)
        stack.callback(_cerra_escritores, escritores)

        eliminar = _eliminar_keyword(escritores)

        try:
            resultados = archivar.revertir_reposicion(
                conn_admin,
                lote=args.revertir_reposicion,
                cliente=cliente_lectura,
                perfiles=perfiles,
                eliminar=eliminar,
                owner=_owner_cli(),
                ejecutar=True,
                avisar=print,
            )
        except (archivar.ReposicionInvalida, archivar.AbortoFase) as exc:
            raise Abortar(str(exc)) from exc
        _resultados_log("revertir_reposicion", args.revertir_reposicion, resultados)
        if not resultados:
            _log(
                "sin_candidatas",
                modo="revertir_reposicion",
                lote=args.revertir_reposicion,
                ok=True,
            )
            return 0
        fallo = _primer_fallo(resultados)
        if fallo is not None:
            raise Abortar(f"lote {args.revertir_reposicion} con fallos: {fallo}")
        _log(
            "reconciliacion_final",
            lote=args.revertir_reposicion,
            revertidas=len(resultados),
            ok=True,
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plataforma", default=None, choices=("amazon_mx", "amazon_us"))
    ap.add_argument("--clasificacion", default="peso_muerto", choices=CLASIFICACIONES)
    ap.add_argument("--min-dias-sin-impresiones", type=int, default=30)
    ap.add_argument("--min-antiguedad-dias", type=int, default=30)
    ap.add_argument("--limite", type=int, default=None)
    agrega_flags_ceremonia(ap, con_huella=True, con_esperado=True)
    ap.add_argument("--reponer", default=None)
    ap.add_argument("--revertir-reposicion", default=None)
    ap.add_argument("--reconciliar", action="store_true")
    ap.add_argument("--lote", default=None)
    args = ap.parse_args()

    if args.reconciliar:
        return _reconciliar_cmd(args)
    if args.revertir_reposicion is not None:
        return _revertir(args)
    if args.reponer is not None:
        return _reponer(args)
    conn_read = connect(_dsn_read())
    try:
        return _archivar(args, conn_read)
    finally:
        _cerrar(conn_read)
