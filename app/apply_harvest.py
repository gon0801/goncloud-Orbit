"""Ejecucion del harvest real: job al liberar, pasos y fachada estable (ORBIT 04, 2.3).

Particion de Ola D3b sin cambio de comportamiento:
- `app.apply_harvest_list`: tipos, `ListaIncompleta`, `_lista_completa`, identidad y acks.
- `app.apply_harvest_contexto`: resolucion del destino y del bid por defecto
  (`_contexto`, `_contexto_congelado`, `_goal_del_grupo`), del congelado F2 o
  del goal fresco (ARREGLOS 05 B, presupuesto de tamano).
- `app.apply_harvest_reversa`: reversa automatica, parcial, completa y manual.
- `app.apply_harvest_hermanas`: fase de higiene `hermanas_negadas`.
- `app.apply_harvest_reconciliacion`: revalidacion PRE-claim y reconciliacion.

ARREGLOS 05 B: la creacion guarda su ACK antes de la lectura de vuelta y el
intento sigue abierto hasta que esa lectura verifique el ID del ACK con su
identidad (`evento_de_identidad`, el resolver unico de `app.apply_estados`).

Este modulo mantiene la maquina principal de ejecucion (`aplica_harvest`,
`_continua_job`, `_paso_negative`, `_paso_keyword`, `_paso_readback`) y
re-exporta toda la superficie estable consumida por `apply_cola`,
`apply_harvest_reconciliacion`, `tools/reversa_harvest.py` y los tests.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

import psycopg
from psycopg.types.json import Json

from app import (
    apply,
    apply_harvest_hermanas,
    apply_harvest_reversa,
    biblioteca,
    notifica,
)
from app.ads.client import AdsApiError
from app.ads.sp_wire import (  # noqa: F401 - re-export estable
    errores_de_ack as _errores_de_ack,
)
from app.ads.sp_wire import (
    id_de_ack as _id_de_ack,
)
from app.apply_estados import (
    RESULTADO_IDENTIDAD_SUSTITUIDA,
    AccionCambio,
    EstadoCambio,
    EventoCambio,
    decidir,
    envia_tras_lectura,
    transicion_desde_error,
)
from app.apply_harvest_contexto import (  # noqa: F401 - re-export estable
    _SQL_DESTINO_PERTENECE,
    _contexto,
    _contexto_congelado,
    _goal_del_grupo,
)
from app.apply_harvest_hermanas import (  # noqa: F401 - re-export estable
    _SQL_ESTADO_COLA,
    _SQL_ROL_ORIGEN,
    _SQL_ROSTER,
    _SQL_TIPO_GRUPO,
    _acks_hermana,
    _motivo_ambiguo_hermana,
    _motivo_rechazo_hermana,
    _post_hermana,
    _roster_hermanas,
)
from app.apply_harvest_list import (  # noqa: F401 - re-export estable
    _CONTENEDORES_LIST,
    _ESTADOS_LIST,
    _MATCH_POR_CONTENEDOR,
    MOTIVO_ARCHIVADO_EN_VUELO,
    MOTIVO_BID_DEFAULT_FALTANTE,
    MOTIVO_ENTIDAD_INCOMPLETA,
    MOTIVO_FALLO_KEYWORD,
    MOTIVO_FALLO_NEGATIVE,
    MOTIVO_HERMANAS_PENDIENTES,
    MOTIVO_KEYWORD_AUSENTE,
    MOTIVO_MONEDA_INCOHERENTE,
    MOTIVO_SIN_CONFIG,
    MOTIVO_TOPE_INTENTOS,
    PATH_BID_SUGERIDO,
    ROLES_DISCOVERY,
    TOPE_CICLOS_HERMANAS,
    TOPE_INTENTOS_HERMANA,
    TOPE_PAGINAS_LIST,
    AlertaHarvest,
    ListaIncompleta,
    PasoReversa,
    ResultadoHarvest,
    ResumenReconciliacion,
    _coincidencias,
    _Contexto,
    _elemento_list_valido,
    _id_list_valido,
    _identidad,
    _Job,
    _job_de_fila,
    _lista_completa,
    _resultado_reversa_rechazada,
    _reversa_rechazada,
    _solo_en_otro_ad_group,
    _sugerido_de,
    _texto_list_valido,
    _valida_pagina_list,
    _valida_token_list,
    ausencia_previa,
    bid_efectivo,
    bid_sugerido,
    evento_de_identidad,
    motivo_identidad,
)
from app.apply_harvest_reversa import (  # noqa: F401 - re-export estable
    _SQL_ACKS_HERMANA,
    _SQL_DECISION,
    _SQL_EXTERNALES,
    _SQL_INTENTO_NORMAL_OBJETO,
    _SQL_INTENTOS_NORMAL_OK_NEGATIVE,
    _SQL_JOB_POR_ID,
    _SQL_PADRE,
    _SQL_REVERSA_OK_KEYWORD,
    _SQL_REVERSA_OK_NEGATIVE,
    _SQL_ULTIMA_COLA,
    _SQL_VERIFY_OK,
    _delete_reversa_pendiente,
    _intento_normal_objeto,
    _reversa_confirmada,
    _reversa_delete,
    plan_reversa_harvest,
    plan_reversa_origen_harvest,
    reversa_harvest_completo,
    reversa_harvest_parcial,
)
from app.apply_store import intento_normal_abierto
from app.db import tx_durable
from app.optimizer import harvest_destino

if TYPE_CHECKING:
    from app.apply import Aplicador
    from app.apply_cola import FilaCola

logger = logging.getLogger(__name__)

_SQL_INSERT_JOB = """
INSERT INTO harvest_job (decision_id, search_term, platform, ad_entity_id, fase)
VALUES (%s, %s, %s::platform, %s, 'pending')
RETURNING id, decision_id, search_term, ad_entity_id, fase, external_ids, platform::text
"""

_SQL_JOB_EXISTENTE = """
SELECT id, decision_id, search_term, ad_entity_id, fase, external_ids, platform::text
  FROM harvest_job
 WHERE platform = %s::platform AND ad_entity_id = %s AND search_term = %s
   AND fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas')
"""

_SQL_AVANZA_FASE = """
UPDATE harvest_job SET fase = %s, external_ids = %s, updated_at = now() WHERE id = %s
"""

_SQL_ARCHIVO_APLICADO = """
SELECT id, estado FROM keyword_archivo_manual
 WHERE platform = %s::platform
   AND ad_group_external = %s
   AND btrim(lower(keyword_text)) = btrim(lower(%s))
   AND match_type = 'EXACT'
   AND repuesto_at IS NULL
 LIMIT 1
"""

_SQL_UPDATE_IDS = """
UPDATE harvest_job SET external_ids = %s, updated_at = now() WHERE id = %s
"""

_SQL_JOB_FAILED = """
UPDATE harvest_job SET fase = 'failed', updated_at = now() WHERE id = %s
"""

_SQL_CLAIM = """
UPDATE apply_queue SET estado = 'applying', applying_at = now()
 WHERE id = %s AND estado = 'released'
RETURNING id
"""


_SQL_SELLA_PENDIENTES = """
UPDATE apply_attempt SET resultado = %s, finished_at = now()
 WHERE decision_id = %s AND finished_at IS NULL AND resultado IS NULL
"""

# ARREGLOS 05 B: nadie retoma un job cerrado, asi que cerrarlo resuelve sus
# intentos abiertos en la misma transaccion. `ok` solo lo pone la lectura que
# verifico el objeto en Amazon: una creacion que cerro sin lectura de vuelta
# lleva el motivo del cierre (su ACK, si lo hubo, se conserva) y nunca se dice
# aplicada. Una reversa con la respuesta perdida tampoco lleva el motivo de la
# cosecha: su desenlace no se comprobo.
RESULTADO_REVERSA_SIN_VERIFICAR = "fallo:reversa_sin_verificar"

_SQL_SELLA_ABIERTOS_AL_CERRAR = """
UPDATE apply_attempt
   SET resultado = CASE WHEN tipo = 'reversa' THEN %s ELSE %s END,
       finished_at = now()
 WHERE decision_id = %s AND finished_at IS NULL AND resultado IS NULL
"""


def _nace_job(conn: psycopg.Connection, platform: str, fila) -> _Job:
    """Nace 'pending' AL LIBERAR, antes de cualquier HTTP (con SAVEPOINT)."""
    try:
        with conn.transaction():
            fila_job = conn.execute(
                _SQL_INSERT_JOB,
                (fila.decision_id, fila.search_term, platform, fila.ad_entity_id),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        fila_job = conn.execute(
            _SQL_JOB_EXISTENTE, (platform, fila.ad_entity_id, fila.search_term)
        ).fetchone()
        if fila_job is None:
            raise
    return _job_de_fila(fila_job)


def _avanza(conn: psycopg.Connection, job: _Job, fase: str | None, ids: dict | None = None) -> None:
    ext = dict(job.external_ids)
    if ids:
        ext.update({k: v for k, v in ids.items() if v is not None})
    job.external_ids = ext
    if fase is not None:
        job.fase = fase
        conn.execute(_SQL_AVANZA_FASE, (fase, Json(ext), job.id))
    else:
        conn.execute(_SQL_UPDATE_IDS, (Json(ext), job.id))


def _termina_cola(conn: psycopg.Connection, queue_id: int, estado: str) -> None:
    sello = "applied_at" if estado == "applied" else "failed_at"
    conn.execute(
        f"UPDATE apply_queue SET estado = %s, {sello} = now() WHERE id = %s", (estado, queue_id)
    )


def _sella_pendientes(conn: psycopg.Connection, decision_id: int, resultado: str) -> None:
    conn.execute(_SQL_SELLA_PENDIENTES, (resultado, decision_id))


def _sella_abiertos_al_cerrar(conn: psycopg.Connection, decision_id: int, resultado: str) -> None:
    """Sella los intentos abiertos del job cuando este se cierra (ARREGLOS 05 B).

    Una creacion abierta toma `resultado` (el motivo del cierre) y conserva su
    ACK: nadie la leyo en Amazon, asi que no se dice aplicada. Una reversa
    abierta quedo sin respuesta: se sella como no verificada.
    """
    conn.execute(
        _SQL_SELLA_ABIERTOS_AL_CERRAR, (RESULTADO_REVERSA_SIN_VERIFICAR, resultado, decision_id)
    )


def _falla_job(
    conn: psycopg.Connection,
    job: _Job,
    motivo: str,
    *,
    queue_id: int | None,
    detalle: str = "",
) -> tuple[str, AlertaHarvest]:
    """Cierra el job como fallido. Sus intentos abiertos se resuelven aqui, en
    la misma transaccion: la conciliacion no retoma jobs cerrados y un intento
    con evidencia previa abierto bloquearia la reversa de la migracion."""
    with tx_durable(conn):
        conn.execute(_SQL_JOB_FAILED, (job.id,))
        _sella_abiertos_al_cerrar(conn, job.decision_id, f"fallo:{motivo}")
        if queue_id is not None:
            _termina_cola(conn, queue_id, "failed")
    job.fase = "failed"
    alerta = AlertaHarvest(
        motivo=motivo,
        decision_id=job.decision_id,
        search_term=job.search_term,
        plataforma=job.plataforma,
        job_id=job.id,
        detalle=detalle,
    )
    if not notifica.notifica_harvest_failed(alerta):
        alerta = replace(alerta, envio_fallido=True)
    return "failed", alerta


def ejecuta_reversa_harvest(
    conn: psycopg.Connection,
    cliente,
    decision_id: int,
    term: str,
    pasos: list[PasoReversa],
) -> tuple[bool, str]:
    """Ejecuta el plan de reversa manual (wrapper que respeta parches del modulo)."""
    return apply_harvest_reversa.ejecuta_reversa_harvest(
        conn,
        cliente,
        decision_id,
        term,
        pasos,
        reversa_confirmada_fn=_reversa_confirmada,
        lista_completa_fn=_lista_completa,
    )


# Un job cuya reversa automatica se interrumpe porque Amazon rechazo la identidad
# o los permisos: se cierra como fallido con este motivo antes de propagar.
MOTIVO_REVERSA_RECHAZADA = "reversa_rechazo_de_identidad"
_SQL_COLA_APLICANDO_DEL_JOB = """
SELECT id FROM apply_queue
 WHERE decision_id = %s AND estado = 'applying'
 ORDER BY id DESC
 LIMIT 1
"""


def _reversa_automatica(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    ctx: _Contexto,
) -> str:
    """Reversa automatica tras fallo definitivo (wrapper que respeta parches).

    Si Amazon rechaza la identidad o los permisos durante la reversa (401/403),
    el rechazo se propaga para detener el barrido, pero ANTES el job se cierra
    como fallido: la creacion que fallo ya es definitiva y, si quedara en su
    fase, la recuperacion volveria a intentar crear la keyword. El negativo del
    origen queda sin retirar y consta en el detalle (ARREGLOS 05 A, revision)."""
    try:
        return apply_harvest_reversa._reversa_automatica(
            conn,
            aplicador,
            job,
            ctx,
            lista_completa_fn=_lista_completa,
            reversa_delete_fn=_reversa_delete,
        )
    except AdsApiError as exc:
        if not transicion_desde_error(EstadoCambio.INCIERTO, exc).propaga_aborto:
            raise
        cola = conn.execute(_SQL_COLA_APLICANDO_DEL_JOB, (job.decision_id,)).fetchone()
        _falla_job(
            conn,
            job,
            MOTIVO_REVERSA_RECHAZADA,
            queue_id=cola[0] if cola is not None else None,
            detalle=(
                "reversa interrumpida: Amazon rechazo la identidad o los permisos"
                f" (http {getattr(exc, 'status', None)}); el negativo del origen queda sin retirar"
            ),
        )
        raise


def _termina_cola_si_applying(conn: psycopg.Connection, queue_id: int | None) -> None:
    apply_harvest_hermanas._termina_cola_si_applying(conn, queue_id, termina_cola_fn=_termina_cola)


def _cierra_o_sigue(
    conn: psycopg.Connection,
    job: _Job,
    objetivo: dict,
    hermanas: dict,
    ciclos: int,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    return apply_harvest_hermanas._cierra_o_sigue(
        conn,
        job,
        objetivo,
        hermanas,
        ciclos,
        queue_id,
        avanza_fn=_avanza,
        termina_cola_fn=_termina_cola,
        notifica_mod=notifica,
    )


def _paso_hermanas(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    return apply_harvest_hermanas._paso_hermanas(
        conn,
        aplicador,
        job,
        queue_id,
        avanza_fn=_avanza,
        termina_cola_fn=_termina_cola,
        lista_completa_fn=_lista_completa,
        notifica_mod=notifica,
    )


_IDENTIDAD_KW = "EXACT"
_IDENTIDAD_NEG = "NEGATIVE_EXACT"
_LISTA_NEG = "/sp/negativeKeywords/list"
_LISTA_KW = "/sp/keywords/list"


def _no_autoriza(evento: EventoCambio, quien: str) -> ListaIncompleta:
    """La lectura no permite concluir: el job queda pendiente en su fase, sin
    escritura, y el ciclo siguiente lo lee de nuevo (mismo tratamiento que una
    lista incompleta)."""
    return ListaIncompleta(f"ambiguo: {motivo_identidad(evento)} ({quien})")


def _paso_negative(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    ctx: _Contexto,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    """Fase pending -> negative_created.

    ARREGLOS 05 B: antes del POST una lectura completa demuestra que la
    identidad no esta viva y esa evidencia nace con la fila del ledger. Si ya
    vive uno con esa identidad es adoptado (nunca propio); si hay varios, o el
    unico no se puede atribuir, el job queda pendiente sin escribir."""
    cliente = aplicador._cliente()
    items = _lista_completa(cliente, _LISTA_NEG)
    evento = evento_de_identidad(
        items,
        id_propio=None,
        ad_group=ctx.grupo_ext,
        texto=job.search_term,
        match=_IDENTIDAD_NEG,
    )
    transicion = decidir(EstadoCambio.PENDIENTE, evento)
    if transicion.accion == AccionCambio.SELLAR_APLICADO:
        adoptado = _coincidencias(items, ctx.grupo_ext, job.search_term)[0]
        procedencia = (
            "incierta"
            if _intento_normal_objeto(conn, job, ctx.grupo_ext, _IDENTIDAD_NEG)
            else False
        )
        _sella_pendientes(conn, job.decision_id, "ok:reconciliado")
        _avanza(
            conn,
            job,
            "negative_created",
            {"negative_id": adoptado.get("keywordId"), "negative_creada": procedencia},
        )
        return "avanza", None
    if not envia_tras_lectura(EstadoCambio.PENDIENTE, evento):
        raise _no_autoriza(evento, f"negativo de origen {job.search_term}")
    payload = {
        "adGroupId": ctx.grupo_ext,
        "campaignId": ctx.campana_ext,
        "keywordText": job.search_term,
        "matchType": _IDENTIDAD_NEG,
        "state": "ENABLED",
    }
    id_attempt = apply._ledger(
        conn,
        job.decision_id,
        "normal",
        payload,
        quota_cobrada=True,
        ausencia_previa=ausencia_previa(
            job.plataforma, _LISTA_NEG, ctx.grupo_ext, job.search_term, _IDENTIDAD_NEG
        ),
    )
    if id_attempt is None:
        return _falla_job(conn, job, MOTIVO_TOPE_INTENTOS, queue_id=queue_id)
    conn.commit()
    try:
        resp = cliente.crear_negative_exacto(ctx.grupo_ext, ctx.campana_ext, job.search_term)
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        if transicion_desde_error(
            EstadoCambio.PENDIENTE, exc, es_mutacion_determinista=True
        ).propaga_aborto:
            raise
        return _falla_job(
            conn, job, MOTIVO_FALLO_NEGATIVE, queue_id=queue_id, detalle=f"fallo http {exc.status}"
        )
    ack = apply._json_seguro(resp)
    errores = _errores_de_ack(ack)
    neg_id = _id_de_ack(ack, "negativeKeywordId")
    if errores or neg_id is None:
        with tx_durable(conn):
            resultado = (
                f"fallo:ack_con_error: {str(errores)[:300]}" if errores else "fallo:ack_sin_id"
            )
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        conn.commit()
        detalle = _reversa_automatica(conn, aplicador, job, ctx) + (
            " | negative rechazado (fail-closed)"
            if errores
            else " | ack sin negative_id; procedencia no comprobada"
        )
        return _falla_job(conn, job, MOTIVO_FALLO_NEGATIVE, queue_id=queue_id, detalle=detalle)
    with tx_durable(conn):
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
        _avanza(conn, job, "negative_created", {"negative_id": neg_id, "negative_creada": True})
    return "avanza", None


def _payload_keyword(ctx: _Contexto, term: str, bid: Decimal) -> dict:
    return {
        "adGroupId": ctx.destino_grupo,
        "campaignId": ctx.destino_campana,
        "keywordText": term,
        "matchType": _IDENTIDAD_KW,
        "state": "ENABLED",
        "bid": apply._bid_payload(bid),
    }


def _paso_keyword(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    ctx: _Contexto,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    """Fase negative_created -> exact_created.

    ARREGLOS 05 B: el ID del ACK queda en el job y el ACK en el ledger ANTES de
    la lectura de vuelta; el intento sigue abierto hasta que esa lectura lo
    verifique. Una keyword preexistente unica se adopta (nunca propia)."""
    cliente = aplicador._cliente()
    if not job.external_ids.get("negative_id"):
        items = _lista_completa(cliente, _LISTA_NEG)
        propio = _identidad(items, ctx.grupo_ext, job.search_term)
        if propio is not None:
            procedencia = (
                "incierta"
                if _intento_normal_objeto(conn, job, ctx.grupo_ext, _IDENTIDAD_NEG)
                else False
            )
            _avanza(
                conn,
                job,
                None,
                {"negative_id": propio.get("keywordId"), "negative_creada": procedencia},
            )
    if not job.external_ids.get("negative_id"):
        return _falla_job(
            conn,
            job,
            MOTIVO_FALLO_NEGATIVE,
            queue_id=queue_id,
            detalle="negative_id ausente: no se cosecha sin corte en origen (fail-closed)",
        )
    kws = _lista_completa(cliente, _LISTA_KW)
    evento = evento_de_identidad(
        kws,
        id_propio=None,
        ad_group=ctx.destino_grupo,
        texto=job.search_term,
        match=_IDENTIDAD_KW,
    )
    transicion = decidir(EstadoCambio.PENDIENTE, evento)
    if transicion.accion == AccionCambio.SELLAR_APLICADO:
        adoptada = _coincidencias(kws, ctx.destino_grupo, job.search_term)[0]
        procedencia = (
            "incierta"
            if _intento_normal_objeto(conn, job, ctx.destino_grupo, _IDENTIDAD_KW)
            else False
        )
        _sella_pendientes(conn, job.decision_id, "ok:reconciliado")
        _avanza(
            conn,
            job,
            "exact_created",
            {"keyword_id": adoptada.get("keywordId"), "keyword_creada": procedencia},
        )
        return "avanza", None
    if not envia_tras_lectura(EstadoCambio.PENDIENTE, evento):
        raise _no_autoriza(evento, f"keyword destino {job.search_term}")
    archivada = conn.execute(
        _SQL_ARCHIVO_APLICADO, (job.plataforma, ctx.destino_grupo, job.search_term)
    ).fetchone()
    if archivada is not None:
        return _falla_job(
            conn,
            job,
            MOTIVO_ARCHIVADO_EN_VUELO,
            queue_id=queue_id,
            detalle=(
                "keyword archivada en vuelo: fila"
                f" {archivada[1]} sin reponer en keyword_archivo_manual"
                " para la identidad; no se recrea"
            ),
        )
    sugerido = bid_sugerido(cliente)
    try:
        bid = bid_efectivo(sugerido, ctx.default_bid, ctx.floor, ctx.ceiling)
    except ValueError:
        detalle = _reversa_automatica(conn, aplicador, job, ctx) + " | sin bid efectivo"
        return _falla_job(
            conn, job, MOTIVO_BID_DEFAULT_FALTANTE, queue_id=queue_id, detalle=detalle
        )
    payload = _payload_keyword(ctx, job.search_term, bid)
    id_attempt = apply._ledger(
        conn,
        job.decision_id,
        "normal",
        payload,
        quota_cobrada=False,
        ausencia_previa=ausencia_previa(
            job.plataforma, _LISTA_KW, ctx.destino_grupo, job.search_term, _IDENTIDAD_KW
        ),
    )
    if id_attempt is None:
        detalle = _reversa_automatica(conn, aplicador, job, ctx)
        return _falla_job(conn, job, MOTIVO_TOPE_INTENTOS, queue_id=queue_id, detalle=detalle)
    conn.commit()
    try:
        resp = cliente.crear_keyword_exacta(
            ctx.destino_grupo, ctx.destino_campana, job.search_term, bid, ctx.moneda
        )
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        if transicion_desde_error(
            EstadoCambio.PENDIENTE, exc, es_mutacion_determinista=True
        ).propaga_aborto:
            raise
        detalle = _reversa_automatica(conn, aplicador, job, ctx) + f" | fallo http {exc.status}"
        return _falla_job(conn, job, MOTIVO_FALLO_KEYWORD, queue_id=queue_id, detalle=detalle)
    ack = apply._json_seguro(resp)
    errores = _errores_de_ack(ack)
    kw_id = _id_de_ack(ack, "keywordId")
    if errores or kw_id is None:
        with tx_durable(conn):
            resultado = (
                f"fallo:ack_con_error: {str(errores)[:300]}" if errores else "fallo:ack_sin_id"
            )
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        conn.commit()
        detalle = _reversa_automatica(conn, aplicador, job, ctx) + (
            " | keyword rechazada"
            if errores
            else " | ack sin keyword_id; procedencia no comprobada"
        )
        return _falla_job(conn, job, MOTIVO_FALLO_KEYWORD, queue_id=queue_id, detalle=detalle)
    # El ID del ACK es inmutable y queda durable antes del readback; el
    # intento sigue abierto hasta que la lectura de vuelta lo verifique.
    with tx_durable(conn):
        apply._guarda_ack(conn, id_attempt, ack)
        _avanza(conn, job, "exact_created", {"keyword_id": kw_id, "keyword_creada": True})
    return "avanza", None


# Resultado con el que se sella el intento abierto cuando la lectura de vuelta
# no encuentra viva la keyword propia bajo su identidad.
_DESENLACE_LECTURA = {
    EventoCambio.NO_APLICADO_EN_AMAZON: "fallo:keyword_ausente",
    EventoCambio.ENTIDAD_ARCHIVADA: "fallo:entidad_archived",
    EventoCambio.IDENTIDAD_SUSTITUIDA: RESULTADO_IDENTIDAD_SUSTITUIDA,
}


def _paso_readback(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    ctx: _Contexto,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    """Fase exact_created -> done|failed|hermanas_negadas.

    ARREGLOS 05 B: la lectura verifica el ID del ACK (el de `keyword_id`, que
    no cambia) con su grupo, texto y match. Si lo que vive con esa identidad
    es otro ID, es ajeno: no se adopta ni se borra. Si la lectura no permite
    concluir, el job queda pendiente en esta fase. Lo propio ausente o
    sustituido es fallo definitivo, con reversa automatica de lo propio vivo."""
    cliente = aplicador._cliente()
    kws = _lista_completa(cliente, _LISTA_KW)
    kw_id = job.external_ids.get("keyword_id")
    abierto = intento_normal_abierto(
        conn,
        job.decision_id,
        identidad={
            "adGroupId": ctx.destino_grupo,
            "keywordText": job.search_term,
            "matchType": _IDENTIDAD_KW,
        },
    )
    evento = evento_de_identidad(
        kws,
        id_propio=kw_id,
        ad_group=ctx.destino_grupo,
        texto=job.search_term,
        match=_IDENTIDAD_KW,
    )
    estado = EstadoCambio.INCIERTO if abierto is not None else EstadoCambio.PENDIENTE
    transicion = decidir(estado, evento)
    if transicion.accion == AccionCambio.MANTENER_INCIERTO or (
        transicion.accion == AccionCambio.NO_TOCAR and evento != EventoCambio.NO_APLICADO_EN_AMAZON
    ):
        raise _no_autoriza(evento, f"keyword {kw_id} en {ctx.destino_grupo}")
    if transicion.accion != AccionCambio.SELLAR_APLICADO:
        if abierto is not None:
            with tx_durable(conn):
                apply._sella_ledger(
                    conn, abierto.id, ack=abierto.ack, resultado=_DESENLACE_LECTURA[evento]
                )
        detalle = (
            _reversa_automatica(conn, aplicador, job, ctx)
            + " | "
            + (
                f"keyword propia {kw_id} ausente en destino"
                if evento == EventoCambio.NO_APLICADO_EN_AMAZON
                else motivo_identidad(evento)
            )
        )
        return _falla_job(conn, job, MOTIVO_KEYWORD_AUSENTE, queue_id=queue_id, detalle=detalle)
    ack = {"fuente": "list", "keywordId": kw_id}
    if ctx.resuelto_por != harvest_destino.RESUELTO_GRUPO:
        with tx_durable(conn):
            if abierto is not None:
                apply._sella_ledger(conn, abierto.id, ack=abierto.ack, resultado="ok")
            _sella_pendientes(conn, job.decision_id, "ok:reconciliado")
            _avanza(conn, job, "done")
            aplicador.confirma_cercada(job.decision_id, ack, True)
            if queue_id is not None:
                _termina_cola(conn, queue_id, "applied")
        return "done", None
    roster = _roster_hermanas(conn, job, ctx)
    if isinstance(roster, str):
        # La lectura ya verifico la creacion: se sella antes de compensar, y
        # la compensacion queda en sus propias filas de reversa.
        if abierto is not None:
            with tx_durable(conn):
                apply._sella_ledger(conn, abierto.id, ack=abierto.ack, resultado="ok")
        detalle = _reversa_automatica(conn, aplicador, job, ctx) + f" | roster imposible ({roster})"
        return _falla_job(conn, job, roster, queue_id=queue_id, detalle=detalle)
    with tx_durable(conn):
        if abierto is not None:
            apply._sella_ledger(conn, abierto.id, ack=abierto.ack, resultado="ok")
        _sella_pendientes(conn, job.decision_id, "ok:reconciliado")
        _avanza(
            conn,
            job,
            "hermanas_negadas",
            {
                "keyword_id": kw_id,
                "hermanas_objetivo": roster,
                "hermanas": {},
                "hermanas_ciclos": 0,
            },
        )
        aplicador.confirma_cercada(job.decision_id, ack, True)
        if queue_id is not None:
            _termina_cola(conn, queue_id, "applied")
        rastro_biblioteca = biblioteca.registra_keyword(
            conn,
            grupo_id=ctx.grupo_id,
            tipo_producto=biblioteca.tipo_producto_de_grupo(conn, ctx.grupo_id),
            platform=job.plataforma,
            texto=job.search_term,
            origen=f"grupo:{ctx.grupo_id}/campana:{job.ad_entity_id}/harvest:{job.id}",
            job_id=job.id,
            decision_id=job.decision_id,
        )
        _avanza(conn, job, None, {"biblioteca": rastro_biblioteca})
    biblioteca.avisa_si_fallo(
        "harvest",
        rastro_biblioteca,
        plataforma=job.plataforma,
        grupo_id=ctx.grupo_id,
        decision_id=job.decision_id,
        job_id=job.id,
        texto=job.search_term,
    )
    return "avanza", None


def _continua_job(
    conn: psycopg.Connection, aplicador, job: _Job, *, queue_id: int | None
) -> tuple[str, AlertaHarvest | None]:
    """Conduce el job DESDE su fase actual hasta done|failed|sigue."""
    if job.fase == "hermanas_negadas":
        return _paso_hermanas(conn, aplicador, job, queue_id)
    try:
        ctx = _contexto(conn, job)
    except ValueError as exc:
        return _falla_job(conn, job, str(exc), queue_id=queue_id)
    if job.fase == "pending":
        estado, alerta = _paso_negative(conn, aplicador, job, ctx, queue_id)
        if estado != "avanza":
            return estado, alerta
    if job.fase == "negative_created":
        estado, alerta = _paso_keyword(conn, aplicador, job, ctx, queue_id)
        if estado != "avanza":
            return estado, alerta
    if job.fase == "exact_created":
        estado, alerta = _paso_readback(conn, aplicador, job, ctx, queue_id)
        if estado != "avanza":
            return estado, alerta
    return _paso_hermanas(conn, aplicador, job, queue_id)


def aplica_harvest(
    conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola, *, platform: str
) -> ResultadoHarvest:
    """Apply del corte harvest: job pending -> quota -> claim -> cadena de fases."""
    job = _nace_job(conn, platform, fila)
    usada, saturada = apply.consume_quota_y_sello(conn, platform, "harvest")
    if not usada:
        return ResultadoHarvest(estado="sin_quota")
    caps = ()
    if saturada:
        evento = apply.evento_cap_saturado(conn, platform, "harvest")
        if evento is not None:
            caps = (evento,)
    if conn.execute(_SQL_CLAIM, (fila.id,)).fetchone() is None:
        return ResultadoHarvest(estado="perdida", caps_saturados=caps)
    estado, alerta = _continua_job(conn, aplicador, job, queue_id=fila.id)
    return ResultadoHarvest(
        estado="applied" if estado in ("done", "sigue") else estado,
        alerta=alerta,
        caps_saturados=caps,
    )


def revalida_harvest(
    conn: psycopg.Connection, platform: str, fila: FilaCola, ahora: dt.datetime
) -> str | None:
    """Compatibilidad: delega con import local en `app.apply_harvest_reconciliacion`."""
    from app import apply_harvest_reconciliacion

    return apply_harvest_reconciliacion.revalida_harvest(conn, platform, fila, ahora)


def reconcilia_harvest(
    conn: psycopg.Connection, aplicador: Aplicador, platform: str
) -> ResumenReconciliacion:
    """Compatibilidad: delega con import local en `app.apply_harvest_reconciliacion`."""
    from app import apply_harvest_reconciliacion

    return apply_harvest_reconciliacion.reconcilia_harvest(conn, aplicador, platform)
