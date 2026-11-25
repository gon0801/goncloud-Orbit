"""Fase de higiene de campanas hermanas tras el harvest (FABRICA 02, A.3).

Extraido de `app/apply_harvest.py` (Ola D3b). Opera sobre el roster congelado en
`harvest_job.external_ids`: nunca re-valida membresia viva, nunca consume quota
y nunca revierte la keyword destino confirmada.
"""

from __future__ import annotations

import re
from dataclasses import replace

import psycopg

from app import apply, notifica
from app.ads.client import AdsApiError
from app.ads.sp_wire import (
    errores_de_ack as _errores_de_ack,
)
from app.ads.sp_wire import (
    id_de_ack as _id_de_ack,
)
from app.apply_estados import EstadoCambio, transicion_desde_error
from app.apply_harvest_list import (
    MOTIVO_ENTIDAD_INCOMPLETA,
    MOTIVO_HERMANAS_PENDIENTES,
    ROLES_DISCOVERY,
    TOPE_CICLOS_HERMANAS,
    TOPE_INTENTOS_HERMANA,
    AlertaHarvest,
    ListaIncompleta,
    _coincidencias,
    _Contexto,
    _Job,
    _lista_completa,
)
from app.db import tx_durable

_SQL_PADRE = """
SELECT parent_id FROM ad_entity WHERE id = %s
"""

_SQL_ROSTER = """
SELECT r.rol::text, ce.external_id, ae.external_id
  FROM campana_grupo_rol r
  JOIN ad_entity ce ON ce.id = r.ad_entity_id
  JOIN ad_entity ae ON ae.id = r.ad_group_ad_entity_id
 WHERE r.grupo_id = %s
"""

_SQL_TIPO_GRUPO = """
SELECT tipo FROM campana_grupo WHERE id = %s
"""

_SQL_ROL_ORIGEN = """
SELECT rol::text FROM campana_grupo_rol WHERE ad_entity_id = %s
"""

_SQL_ESTADO_COLA = """
SELECT estado FROM apply_queue WHERE id = %s
"""


def _roster_hermanas(conn: psycopg.Connection, job: _Job, ctx: _Contexto) -> dict | str:
    """Roster congelado {rol: {campaign_id, ad_group_id}} en orden canonico."""
    if ctx.grupo_id is None:
        return MOTIVO_ENTIDAD_INCOMPLETA
    tipo = conn.execute(_SQL_TIPO_GRUPO, (ctx.grupo_id,)).fetchone()
    tipo_str = tipo[0] if tipo is not None else None
    if tipo_str == "impulso":
        return {}
    por_rol = {r: (c, a) for r, c, a in conn.execute(_SQL_ROSTER, (ctx.grupo_id,)).fetchall()}
    padre = conn.execute(_SQL_PADRE, (job.ad_entity_id,)).fetchone()
    rol_origen = None
    if padre is not None:
        fila_rol = conn.execute(_SQL_ROL_ORIGEN, (padre[0],)).fetchone()
        rol_origen = fila_rol[0] if fila_rol is not None else None
    if rol_origen not in ROLES_DISCOVERY:
        return MOTIVO_ENTIDAD_INCOMPLETA
    roster: dict[str, dict] = {}
    for rol in ROLES_DISCOVERY:
        if rol == rol_origen:
            continue
        if rol not in por_rol:
            if tipo_str == "legado":
                continue
            return MOTIVO_ENTIDAD_INCOMPLETA
        camp_ext, ag_ext = por_rol[rol]
        roster[rol] = {"campaign_id": camp_ext, "ad_group_id": ag_ext}
    return roster


def _termina_cola_si_applying(
    conn: psycopg.Connection, queue_id: int | None, *, termina_cola_fn
) -> None:
    if queue_id is None:
        return
    fila = conn.execute(_SQL_ESTADO_COLA, (queue_id,)).fetchone()
    if fila is not None and fila[0] == "applying":
        termina_cola_fn(conn, queue_id, "applied")


def _motivo_rechazo_hermana(rol: str, status: int | None) -> str:
    if rol == "product_targeting" and (status is None or status < 500):
        return "pt_no_acepta_negative_keyword"
    if status is not None and status >= 500:
        return "http_5xx"
    return "http_400"


def _motivo_ambiguo_hermana(exc: Exception) -> str:
    m = re.search(r"status=(\d{3})", str(exc))
    if m is not None and int(m.group(1)) >= 500:
        return "http_5xx"
    return "red_ambigua"


def _cierra_o_sigue(
    conn: psycopg.Connection,
    job: _Job,
    objetivo: dict,
    hermanas: dict,
    ciclos: int,
    queue_id: int | None,
    *,
    avanza_fn,
    termina_cola_fn,
    notifica_mod=notifica,
) -> tuple[str, AlertaHarvest | None]:
    """Cierre o continuacion tras persistir un ciclo de higiene."""
    restantes = [
        r for r in ROLES_DISCOVERY if r in objetivo and "negative_id" not in hermanas.get(r, {})
    ]
    if not restantes:
        with tx_durable(conn):
            avanza_fn(conn, job, "done", {"hermanas_pendientes": {}})
            _termina_cola_si_applying(conn, queue_id, termina_cola_fn=termina_cola_fn)
        return "done", None
    if ciclos >= TOPE_CICLOS_HERMANAS:
        pendientes = {r: hermanas[r].get("motivo", "red_ambigua") for r in restantes}
        resueltas = [r for r in ROLES_DISCOVERY if r in objetivo and r not in restantes]
        detalle = (
            f"{len(resueltas)}/{len(objetivo)} hermanas aplicadas"
            f" ({', '.join(resueltas)}); pendientes: "
            + ", ".join(f"{r}: {pendientes[r]}" for r in restantes)
        )
        alerta = AlertaHarvest(
            motivo=MOTIVO_HERMANAS_PENDIENTES,
            decision_id=job.decision_id,
            search_term=job.search_term,
            plataforma=job.plataforma,
            job_id=job.id,
            detalle=detalle,
        )
        if not notifica_mod.notifica_harvest_hermanas(alerta):
            alerta = replace(alerta, envio_fallido=True)
        with tx_durable(conn):
            avanza_fn(conn, job, "done", {"hermanas_pendientes": pendientes})
            _termina_cola_si_applying(conn, queue_id, termina_cola_fn=termina_cola_fn)
        return "done", alerta
    return "sigue", None


def _acks_hermana(intentos: list[tuple[int, dict | None, bool]]) -> set[str]:
    """Ids de ack duraderas de los intentos previos de una hermana."""
    ids: set[str] = set()
    for _i, ack, _abierta in intentos:
        if not isinstance(ack, dict):
            continue
        id_ = _id_de_ack(ack, "negativeKeywordId")
        if id_ is not None:
            ids.add(str(id_))
    return ids


def _post_hermana(
    conn: psycopg.Connection, cliente, job: _Job, rol: str, ag_ext: str, camp_ext: str
) -> tuple[str | None, int | None, str | None, dict | None]:
    """UN intento POST a una hermana ausente del previo."""
    if apply._intentos_hermana(conn, job.decision_id, ag_ext) >= TOPE_INTENTOS_HERMANA:
        return ("tope_intentos", None, None, None)
    payload = {
        "adGroupId": ag_ext,
        "campaignId": camp_ext,
        "keywordText": job.search_term,
        "matchType": "NEGATIVE_EXACT",
        "state": "ENABLED",
    }
    id_attempt = apply._ledger(conn, job.decision_id, "hermana", payload, quota_cobrada=False)
    if id_attempt is None:
        return ("tope_intentos", None, None, None)
    conn.commit()
    try:
        resp = cliente.crear_negative_exacto(ag_ext, camp_ext, job.search_term)
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        if transicion_desde_error(
            EstadoCambio.PENDIENTE, exc, es_mutacion_determinista=True
        ).propaga_aborto:
            raise
        return (_motivo_rechazo_hermana(rol, exc.status), None, None, None)
    except AdsApiError as exc_ambigua:
        return (_motivo_ambiguo_hermana(exc_ambigua), None, None, None)
    ack = apply._json_seguro(resp)
    errores = _errores_de_ack(ack)
    neg_id = _id_de_ack(ack, "negativeKeywordId")
    if errores or neg_id is None:
        motivo = _motivo_rechazo_hermana(rol, None) if errores else "ack_sin_id"
        with tx_durable(conn):
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado=f"fallo:{motivo}")
        return (motivo, None, None, None)
    with tx_durable(conn):
        apply._guarda_ack(conn, id_attempt, ack)
    return (None, id_attempt, neg_id, ack)


def _paso_hermanas(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    queue_id: int | None,
    *,
    avanza_fn,
    termina_cola_fn,
    lista_completa_fn=_lista_completa,
    notifica_mod=notifica,
) -> tuple[str, AlertaHarvest | None]:
    """Fase hermanas_negadas (F2, A.3): higiene posterior al evento de valor."""
    ext = dict(job.external_ids)
    objetivo = dict(ext.get("hermanas_objetivo") or {})
    hermanas = {r: dict(v) for r, v in (ext.get("hermanas") or {}).items()}
    ciclos = int(ext.get("hermanas_ciclos") or 0)
    pendientes = [
        r for r in ROLES_DISCOVERY if r in objetivo and "negative_id" not in hermanas.get(r, {})
    ]
    if not pendientes:
        with tx_durable(conn):
            avanza_fn(
                conn,
                job,
                "done",
                {
                    "hermanas": hermanas,
                    "hermanas_ciclos": ciclos + 1,
                    "hermanas_pendientes": {},
                },
            )
            _termina_cola_si_applying(conn, queue_id, termina_cola_fn=termina_cola_fn)
        return "done", None
    if apply.gate_ancestros(conn, job.ad_entity_id) is not None:
        for rol in pendientes:
            hermanas[rol] = {"motivo": "ancestro_no_enabled"}
        with tx_durable(conn):
            avanza_fn(conn, job, None, {"hermanas": hermanas, "hermanas_ciclos": ciclos + 1})
        return _cierra_o_sigue(
            conn,
            job,
            objetivo,
            hermanas,
            ciclos + 1,
            queue_id,
            avanza_fn=avanza_fn,
            termina_cola_fn=termina_cola_fn,
            notifica_mod=notifica_mod,
        )
    cliente = aplicador._cliente()
    try:
        previo = lista_completa_fn(
            cliente,
            "/sp/negativeKeywords/list",
            grupos=tuple(str(objetivo[r]["ad_group_id"]) for r in pendientes),
        )
    except ListaIncompleta as exc:
        motivo = "list_truncado" if exc.causa == "truncado" else "list_ambiguo"
        for rol in pendientes:
            hermanas[rol] = {"motivo": motivo}
        with tx_durable(conn):
            avanza_fn(conn, job, None, {"hermanas": hermanas, "hermanas_ciclos": ciclos + 1})
        return _cierra_o_sigue(
            conn,
            job,
            objetivo,
            hermanas,
            ciclos + 1,
            queue_id,
            avanza_fn=avanza_fn,
            termina_cola_fn=termina_cola_fn,
            notifica_mod=notifica_mod,
        )
    posteadas: list[tuple[str, int, str, str, dict]] = []
    for rol in pendientes:
        ag_ext = objetivo[rol]["ad_group_id"]
        camp_ext = objetivo[rol]["campaign_id"]
        halladas = _coincidencias(previo, ag_ext, job.search_term)
        intentos = apply._intentos_hermana_detalle(conn, job.decision_id, ag_ext)
        probadas = [x for x in halladas if str(x.get("keywordId")) in _acks_hermana(intentos)]
        abiertas = [i for (i, _ack, abierta) in intentos if abierta]
        if probadas:
            propia = probadas[0]
            prob_ids = {str(x.get("keywordId")) for x in probadas}
            with tx_durable(conn):
                for _i, _ack, _abierta in intentos:
                    if not _abierta or not isinstance(_ack, dict):
                        continue
                    _id = _id_de_ack(_ack, "negativeKeywordId")
                    if _id is not None and str(_id) in prob_ids:
                        apply._sella_resultado(conn, _i, "ok")
            hermanas[rol] = {"negative_id": propia.get("keywordId"), "creada": True}
            continue
        if abiertas:
            hermanas[rol] = {"motivo": "red_ambigua"}
            continue
        if halladas:
            hermanas[rol] = {"negative_id": halladas[0].get("keywordId"), "creada": False}
            continue
        motivo, id_attempt, neg_id, ack_post = _post_hermana(
            conn, cliente, job, rol, ag_ext, camp_ext
        )
        if motivo is not None:
            hermanas[rol] = {"motivo": motivo}
            continue
        assert id_attempt is not None and neg_id is not None
        posteadas.append((rol, id_attempt, neg_id, ag_ext, ack_post))
    if posteadas:
        try:
            posterior = lista_completa_fn(
                cliente,
                "/sp/negativeKeywords/list",
                grupos=tuple(str(ag) for _, _, _, ag, _ in posteadas),
            )
        except ListaIncompleta as exc:
            motivo = "list_truncado" if exc.causa == "truncado" else "list_ambiguo"
            for rol, _id_attempt, _neg_id, _ag_ext, _ack_post in posteadas:
                hermanas[rol] = {"motivo": motivo}
            with tx_durable(conn):
                avanza_fn(conn, job, None, {"hermanas": hermanas, "hermanas_ciclos": ciclos + 1})
            return _cierra_o_sigue(
                conn,
                job,
                objetivo,
                hermanas,
                ciclos + 1,
                queue_id,
                avanza_fn=avanza_fn,
                termina_cola_fn=termina_cola_fn,
                notifica_mod=notifica_mod,
            )
        for rol, id_attempt, neg_id, ag_ext, _ack_post in posteadas:
            halladas = _coincidencias(posterior, ag_ext, job.search_term)
            if neg_id in {str(x.get("keywordId")) for x in halladas}:
                with tx_durable(conn):
                    apply._sella_resultado(conn, id_attempt, "ok")
                hermanas[rol] = {"negative_id": neg_id, "creada": True}
            else:
                with tx_durable(conn):
                    apply._sella_resultado(conn, id_attempt, "fallo:ack_sin_prueba")
                if halladas:
                    hermanas[rol] = {
                        "negative_id": halladas[0].get("keywordId"),
                        "creada": False,
                    }
                else:
                    hermanas[rol] = {"motivo": "red_ambigua"}
    with tx_durable(conn):
        avanza_fn(conn, job, None, {"hermanas": hermanas, "hermanas_ciclos": ciclos + 1})
    return _cierra_o_sigue(
        conn,
        job,
        objetivo,
        hermanas,
        ciclos + 1,
        queue_id,
        avanza_fn=avanza_fn,
        termina_cola_fn=termina_cola_fn,
        notifica_mod=notifica_mod,
    )
