"""Ejecucion de mutaciones y reversas de cortes `pause`/`negative` (ORBIT 04).

Extraido de `app/apply_cola.py` (Ola D3b). Conserva el candado
`PERMITIDOS_IMPORTAR_ADS_WRITE`: no importa `app.ads.write`, usa el cliente
recibido por parametro o detras de `Aplicador._cliente()`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg

from app import apply, apply_harvest, apply_producto, biblioteca
from app.db import tx_durable

if TYPE_CHECKING:
    from app.apply import Aplicador
    from app.apply_cola import FilaCola

_SQL_CACHE_ESTADO = """
UPDATE ad_entity_state SET status = %s, synced_at = now() WHERE ad_entity_id = %s
"""


def _termina(conn: psycopg.Connection, fila: FilaCola, estado: str) -> None:
    sello = "applied_at" if estado == "applied" else "failed_at"
    conn.execute(
        f"UPDATE apply_queue SET estado = %s, {sello} = now() WHERE id = %s", (estado, fila.id)
    )


def _cliente_mutacion(aplicador: Aplicador):
    return aplicador._cliente()


def _ejecuta_pause(conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola) -> str:
    identidad = apply._identidad(conn, fila.ad_entity_id)
    if identidad is None or identidad[0] not in apply._KINDS_DECISORAS:
        _termina(conn, fila, "failed")
        return "failed"
    id_attempt = apply._ledger(
        conn, fila.decision_id, "normal", fila.request_payload, quota_cobrada=True
    )
    if id_attempt is None:
        _termina(conn, fila, "failed")
        return "failed"
    conn.commit()
    cliente = _cliente_mutacion(aplicador)
    try:
        if identidad[0] == "keyword":
            resp_http = cliente.pausar_keyword(identidad[1])
        else:
            resp_http = cliente.pausar_target(identidad[1])
    except apply.AdsApiErrorMutacion as exc:
        with tx_durable(conn):
            apply._sella_ledger(
                conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
            )
            _termina(conn, fila, "failed")
        return "failed"
    ack = apply._json_seguro(resp_http)
    _, path, contenedor, param = apply._KINDS_DECISORAS[identidad[0]]
    estado = apply._estado_de_readback(cliente, path, contenedor, param, identidad[1])
    verify = estado == apply.ESTADO_WIRE_PAUSED
    resultado = (
        "ok"
        if verify
        else ("fallo:divergencia_readback" if estado is not None else "fallo:readback_sin_estado")
    )
    with tx_durable(conn):
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        apply._confirma_resumen(conn, fila.decision_id, ack, verify, aplicador.cycle_id_ejecutor)
        if estado is not None:
            conn.execute(_SQL_CACHE_ESTADO, (estado, fila.ad_entity_id))
        _termina(conn, fila, "applied" if verify else "failed")
    return "applied" if verify else "failed"


def _ejecuta_negative(conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola) -> str:
    payload = fila.request_payload
    id_attempt = apply._ledger(conn, fila.decision_id, "normal", payload, quota_cobrada=True)
    if id_attempt is None:
        _termina(conn, fila, "failed")
        return "failed"
    conn.commit()
    try:
        resp_http = _cliente_mutacion(aplicador).crear_negative_exacto(
            payload["adGroupId"], payload["campaignId"], payload["keywordText"]
        )
    except apply.AdsApiErrorMutacion as exc:
        with tx_durable(conn):
            apply._sella_ledger(
                conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
            )
            _termina(conn, fila, "failed")
        return "failed"
    ack = apply._json_seguro(resp_http)
    verify = apply_harvest._id_de_ack(ack, "negativeKeywordId") is not None
    resultado = "ok" if verify else "fallo:ack_sin_id"
    rastro_biblio: dict | None = None
    grupo_biblio: int | None = None
    with tx_durable(conn):
        if verify:
            grupo = biblioteca.grupo_de_ad_group(conn, fila.ad_entity_id)
            if grupo is not None:
                grupo_biblio = grupo[0]
                rastro_biblio = biblioteca.registra_negative(
                    conn,
                    grupo_id=grupo[0],
                    tipo_producto=grupo[1],
                    platform=aplicador._platform,
                    texto=fila.search_term,
                    origen=f"grupo:{grupo[0]}/campana:{grupo[2]}/decision:{fila.decision_id}",
                    decision_id=fila.decision_id,
                )
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        apply._confirma_resumen(conn, fila.decision_id, ack, verify, aplicador.cycle_id_ejecutor)
        _termina(conn, fila, "applied" if verify else "failed")
    biblioteca.avisa_si_fallo(
        "negative",
        rastro_biblio,
        plataforma=aplicador._platform,
        grupo_id=grupo_biblio,
        decision_id=fila.decision_id,
        job_id=None,
        texto=fila.search_term,
    )
    return "applied" if verify else "failed"


def _ejecuta_rama(conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola, rama: str) -> str:
    if rama == "pause_hoja":
        return _ejecuta_pause(conn, aplicador, fila)
    if rama == "pause_producto":
        return apply_producto.ejecuta_pause_producto(conn, aplicador, fila)
    return _ejecuta_negative(conn, aplicador, fila)


def reversa_pause(
    conn: psycopg.Connection,
    cliente,
    fila: FilaCola,
    *,
    tick=None,
    durable: bool = False,
) -> bool:
    """Reversa del pause: reanudar keyword/target, readback y cache con lo leido.

    `durable=True` (TEMPORADA 01 F) confirma el sello con `tx_durable`: en la
    fase de apply la conexion de produccion ya esta en INTRANS y un
    `conn.transaction()` seria un savepoint; un aborto posterior perderia el
    sello de una habilitacion que Amazon si aplico y el ciclo la repetiria."""
    identidad = apply._identidad(conn, fila.ad_entity_id)
    if identidad is None or identidad[0] not in apply._KINDS_DECISORAS:
        return False
    campo = "keywordId" if identidad[0] == "keyword" else "targetId"
    payload = {campo: identidad[1], "state": apply.ESTADO_PUT_ENABLED}
    id_attempt = apply._ledger(conn, fila.decision_id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return False
    conn.commit()
    try:
        if identidad[0] == "keyword":
            resp_http = cliente.reanudar_keyword(identidad[1])
        else:
            resp_http = cliente.reanudar_target(identidad[1])
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        return False
    ack = apply._json_seguro(resp_http)
    if tick is not None:
        tick()
    _, path, contenedor, param = apply._KINDS_DECISORAS[identidad[0]]
    estado = apply._estado_de_readback(cliente, path, contenedor, param, identidad[1])
    verify = estado == apply.ESTADO_WIRE_ENABLED
    resultado = (
        "ok"
        if verify
        else ("fallo:divergencia_readback" if estado is not None else "fallo:readback_sin_estado")
    )
    cierre = tx_durable(conn) if durable else conn.transaction()
    with cierre:
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        if estado is not None:
            conn.execute(_SQL_CACHE_ESTADO, (estado, fila.ad_entity_id))
    return verify


def reversa_negative(
    conn: psycopg.Connection,
    cliente,
    fila: FilaCola,
    negative_id: str | int,
    *,
    tick=None,
) -> bool:
    """Reversa del negative: POST /sp/negativeKeywords/delete con verificacion 207."""
    payload = {"negativeKeywordIdFilter": {"include": [str(negative_id)]}}
    id_attempt = apply._ledger(conn, fila.decision_id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return False
    conn.commit()
    try:
        resp_http = cliente.borrar_negative(negative_id)
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        return False
    ack = apply._json_seguro(resp_http)
    if tick is not None:
        tick()
    if apply_harvest._reversa_rechazada(ack, "negativeKeywordId", negative_id):
        with conn.transaction():
            apply._sella_ledger(
                conn,
                id_attempt,
                ack=ack,
                resultado=apply_harvest._resultado_reversa_rechazada(ack),
            )
        return False
    with conn.transaction():
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
    return True
