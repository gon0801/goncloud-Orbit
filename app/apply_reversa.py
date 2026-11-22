"""Reversas de bids y reversa manual unificada (regla 7, ORBIT 04).

Extraido de `app/apply.py` (Ola D3a). No importa `app.ads.write` en runtime ni
hace import diferido de `app.apply_harvest`: parsea `negativeKeywordId`
directamente con `app.ads.sp_wire.id_de_ack` y recibe la fabrica de cliente
desde `app/apply.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import psycopg

from app.ads.sp_wire import id_de_ack
from app.apply_cuota import DecisionBid
from app.apply_store import (
    _KINDS_DECISORAS,
    _actualiza_cache,
    _bid_de_readback,
    _identidad,
    _json_seguro,
    _ledger,
    _sella_ledger,
    _tick,
)

if TYPE_CHECKING:
    from app.ads.write import AdsWriteClient


class ReversaNoAplicada(Exception):
    """La reversa exige un apply confirmado y no lo hay: precondicion (409)."""


class ReversaYaHecha(Exception):
    """Ya existe UNA reversa confirmada (tipo 'reversa' resultado 'ok') para la
    decision: una reversa es UNA por decision (regla 7; ADV-3). 409."""

    def __init__(self, decision_id: int) -> None:
        super().__init__(
            f"ya revertida: la decision {decision_id} ya tiene una reversa"
            " confirmada en el ledger (una reversa es UNA por decision)"
        )


class ReversaInexistente(Exception):
    """La decision/fila pedida no existe (404)."""


class NegativeIdNoResoluble(Exception):
    """El negative_id no se resolvio del ledger (422; regla 3)."""


class SinPerfilReversa(Exception):
    """Sin perfil ACEPTADO para la plataforma en /v2/profiles (503)."""


class SinPerfilAjuste(Exception):
    """Sin perfil ACEPTADO para la plataforma al aplicar un ajuste (503)."""


_SQL_DECISION_POR_ID = """
SELECT d.id, d.cycle_id, d.ad_entity_id, d.old_value, d.new_value,
       d.value_currency, d.inputs, oc.platform::text
  FROM decision d
  JOIN optimizer_cycle oc ON oc.id = d.cycle_id
 WHERE d.id = %s
"""

_SQL_RESUMEN_APLICADA = """
SELECT applied_cycle_id FROM decision_application WHERE decision_id = %s
"""

_SQL_PLATAFORMA_FILA = """
SELECT platform::text FROM apply_queue WHERE id = %s
"""

# Ultimo ack normal ok de la decision: unica fuente del negative_id para DELETE.
_SQL_ACK_OK = """
SELECT ack FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'normal' AND resultado = 'ok'
 ORDER BY seq DESC LIMIT 1
"""

_SQL_REVERSA_OK = """
SELECT EXISTS (
    SELECT 1 FROM apply_attempt
     WHERE decision_id = %s AND tipo = 'reversa' AND resultado = 'ok'
)
"""


def _reversa_ya_hecha(conn: psycopg.Connection, decision_id: int) -> bool:
    """True si la decision ya tiene UNA reversa confirmada (resultado 'ok')."""
    return conn.execute(_SQL_REVERSA_OK, (decision_id,)).fetchone()[0]


def _negative_id_del_ledger(conn: psycopg.Connection, decision_id: int) -> str:
    """Resuelve el negative_id del ack del ultimo intento normal ok del ledger."""
    fila = conn.execute(_SQL_ACK_OK, (decision_id,)).fetchone()
    if fila is not None and isinstance(fila[0], dict):
        negative_id = id_de_ack(fila[0], "negativeKeywordId")
        if negative_id is not None:
            return negative_id
    raise NegativeIdNoResoluble(
        f"no se resolvio el negative_id de la decision {decision_id} desde el"
        " ledger (sin intento normal resultado ok con ack legible)"
    )


def reversa_bid(
    conn: psycopg.Connection,
    cliente: AdsWriteClient,
    decision: DecisionBid,
    *,
    tick: Callable[[], None] | None = None,
    payload_bid: Callable[[str, str, Decimal], dict],
    bid_quantizado: Callable[[Decimal], Decimal],
    error_mutacion_cls: type[Exception],
) -> bool:
    """Reversa del bid: PUT con old_value, ledger tipo 'reversa' exento de quota,
    readback y sello. No toca decision_application ni applied_count."""
    if decision.old_value is None or decision.value_currency is None:
        raise ValueError(
            f"reversa de bid {decision.id}: exige old_value y value_currency (regla 7)"
        )
    identidad = _identidad(conn, decision.ad_entity_id)
    if identidad is None or identidad[0] not in _KINDS_DECISORAS:
        return False
    payload = payload_bid(identidad[0], identidad[1], decision.old_value)
    id_attempt = _ledger(conn, decision.id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return False
    conn.commit()
    try:
        _tick(tick)
        if identidad[0] == "keyword":
            resp_http = cliente.actualizar_bid_keyword(
                identidad[1], decision.old_value, decision.value_currency
            )
        else:
            resp_http = cliente.actualizar_bid_target(
                identidad[1], decision.old_value, decision.value_currency
            )
    except error_mutacion_cls as exc:
        _sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        return False
    ack = _json_seguro(resp_http)
    _tick(tick)
    _, path, contenedor, param = _KINDS_DECISORAS[identidad[0]]
    bid_leido = _bid_de_readback(cliente, path, contenedor, param, identidad[1])
    esperado = bid_quantizado(decision.old_value)
    confirmada = bid_leido is not None and bid_leido == esperado
    resultado = (
        "ok"
        if confirmada
        else ("fallo:divergencia_readback" if bid_leido is not None else "fallo:readback_sin_bid")
    )
    with conn.transaction():
        _sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        if bid_leido is not None:
            _actualiza_cache(conn, decision.ad_entity_id, bid_leido)
    return confirmada


def reversa_manual(
    conn: psycopg.Connection,
    *,
    tipo: str,
    decision_id: int | None = None,
    queue_id: int | None = None,
    transport: httpx.BaseTransport | None = None,
    cliente_reversa: Callable[..., AdsWriteClient],
    reversa_bid_fn: Callable[[psycopg.Connection, AdsWriteClient, DecisionBid], bool],
) -> dict:
    """Reversa MANUAL (regla 7, sellado 12): un solo entry point para bid, pause
    y negative. Precondiciones antes de construir cliente o HTTP."""
    from app import apply_cola  # diferido: apply_cola importa apply

    if tipo == "bid":
        if decision_id is None:
            raise ValueError("reversa de bid exige decision_id")
        fila = conn.execute(_SQL_DECISION_POR_ID, (decision_id,)).fetchone()
        if fila is None:
            raise ReversaInexistente(f"decision {decision_id} no existe")
        resumen = conn.execute(_SQL_RESUMEN_APLICADA, (decision_id,)).fetchone()
        if resumen is None or resumen[0] is None:
            raise ReversaNoAplicada(
                f"decision {decision_id} no esta confirmada como aplicada"
                " (applied_cycle_id): no existe reversa sin apply"
            )
        if _reversa_ya_hecha(conn, decision_id):
            raise ReversaYaHecha(decision_id)
        decision = DecisionBid(
            id=fila[0],
            ad_entity_id=fila[2],
            old_value=fila[3],
            new_value=fila[4],
            value_currency=fila[5],
            inputs=fila[6] or {},
        )
        if decision.old_value is None or decision.value_currency is None:
            raise ReversaNoAplicada(
                f"decision {decision_id} sin old_value/moneda: nada que revertir (regla 7)"
            )
        confirmada = reversa_bid_fn(conn, cliente_reversa(fila[7], transport=transport), decision)
        return {"tipo": "bid", "decision_id": decision_id, "confirmada": confirmada}

    if tipo in ("pause", "negative"):
        if queue_id is None:
            raise ValueError(f"reversa de {tipo} exige queue_id")
        fila = apply_cola.fila_cola(conn, queue_id)
        if fila is None:
            raise ReversaInexistente(f"fila {queue_id} de apply_queue no existe")
        if fila.estado != "applied":
            raise ReversaNoAplicada(
                f"fila {queue_id} en estado {fila.estado}: la reversa exige applied"
            )
        if _reversa_ya_hecha(conn, fila.decision_id):
            raise ReversaYaHecha(fila.decision_id)
        platform = conn.execute(_SQL_PLATAFORMA_FILA, (queue_id,)).fetchone()[0]
        cliente = cliente_reversa(platform, transport=transport)
        if tipo == "pause":
            confirmada = apply_cola.reversa_pause(conn, cliente, fila)
            return {"tipo": "pause", "queue_id": queue_id, "confirmada": confirmada}
        negative_id = _negative_id_del_ledger(conn, fila.decision_id)
        confirmada = apply_cola.reversa_negative(conn, cliente, fila, negative_id)
        return {
            "tipo": "negative",
            "queue_id": queue_id,
            "negative_id": str(negative_id),
            "confirmada": confirmada,
        }

    raise ValueError(f"tipo de reversa desconocido: {tipo!r} (bid|pause|negative)")
