"""Reconciliacion del ledger de bids sin sello y ambiguos (ORBIT 04 / TEMPORADA 01 B).

Extraido de `app/apply.py` (Ola D3a). No importa `app.ads.write` en runtime:
consume el cliente lazy de `Aplicador` y reutiliza el mismo holder en ambas
pasadas (ambiguos sellados y sin sello).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING

import psycopg

from app.ads.client import AdsApiError
from app.apply_cuota import (
    _SQL_INTENTOS_BID_SIN_SELLO,
    MOTIVO_ENTIDAD_NO_DECISORA,
    MOTIVO_PERDIDA,
    RESULTADO_ANCESTRO_NO_ENABLED,
    gate_ancestros,
    registra_sin_aplicar,
)
from app.apply_store import (
    _KINDS_DECISORAS,
    _actualiza_cache,
    _bid_de_readback,
    _confirma_resumen,
    _identidad,
    _ledger,
    _sella_ledger,
)
from app.db import tx_durable
from app.optimizer.recuperacion import TOLERANCIA_CADENA

if TYPE_CHECKING:
    from app.apply import Aplicador

# TEMPORADA 01 B (revision): intentos SELLADOS con lectura ambigua y sin
# desenlace. Amazon puede haber APLICADO el cambio: se reconcilian con GET
# fresco JAMAS reenviando el PUT.
_SQL_INTENTOS_BID_AMBIGUOS_SIN_DESENLACE = """
SELECT a.id, a.decision_id, d.ad_entity_id, d.old_value, d.new_value
  FROM apply_attempt a
  JOIN decision d         ON d.id = a.decision_id AND d.kind = 'bid'
  JOIN ad_entity k        ON k.id = d.ad_entity_id
 WHERE a.tipo = 'normal' AND a.finished_at IS NOT NULL
   AND a.resultado = 'fallo:readback_ambiguo'
   AND k.platform = %s::platform
   AND NOT EXISTS (SELECT 1 FROM decision_application da WHERE da.decision_id = d.id)
   AND NOT EXISTS (SELECT 1 FROM decision_sin_aplicar sa WHERE sa.decision_id = d.id)
 ORDER BY a.started_at, a.id
"""

_SQL_EFECTO_TEMPORADA_ABIERTO = """
SELECT EXISTS (
    SELECT 1 FROM temporada_efecto te
     WHERE te.decision_id = %s AND te.cerrado_el IS NULL
)
"""


def _con_efecto_temporada_abierto(conn: psycopg.Connection, decision_id: int) -> bool:
    return bool(conn.execute(_SQL_EFECTO_TEMPORADA_ABIERTO, (decision_id,)).fetchone()[0])


def _reconcilia_ambiguos_sellados(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    cliente_holder: list,
    *,
    bid_quantizado: Callable[[Decimal], Decimal],
) -> tuple[int, int]:
    """Los intentos SELLADOS con lectura ambigua sin desenlace (TEMPORADA 01 B):
    GET fresco JAMAS reenvia el PUT. El cliente compartido vive en
    `cliente_holder` (lazy tras el gate de ancestros)."""
    filas = conn.execute(_SQL_INTENTOS_BID_AMBIGUOS_SIN_DESENLACE, (platform,)).fetchall()
    confirmadas = fallidas = 0
    for _id_attempt, decision_id, ad_entity_id, old_value, new_value in filas:
        if gate_ancestros(conn, ad_entity_id) is not None:
            fallidas += 1
            continue
        if cliente_holder[0] is None:
            cliente_holder[0] = aplicador._cliente()
        identidad = _identidad(conn, ad_entity_id)
        if identidad is None or identidad[0] not in _KINDS_DECISORAS or new_value is None:
            fallidas += 1
            continue
        kind, external_id = identidad
        _, path, contenedor, param = _KINDS_DECISORAS[kind]
        try:
            bid_leido = _bid_de_readback(cliente_holder[0], path, contenedor, param, external_id)
        except AdsApiError:
            fallidas += 1
            continue
        ack = {"fuente": "readback", "bid": str(bid_leido)} if bid_leido is not None else None
        if bid_leido is None:
            fallidas += 1
            continue
        if bid_leido == bid_quantizado(new_value):
            with tx_durable(conn):
                id_probe = _ledger(
                    conn, decision_id, "probe", {"leido": str(bid_leido)}, quota_cobrada=False
                )
                if id_probe is not None:
                    _sella_ledger(conn, id_probe, ack=ack, resultado="ok:reconciliado")
                _confirma_resumen(
                    conn, decision_id, ack, True, aplicador.cycle_id_ejecutor, leido=bid_leido
                )
                _actualiza_cache(conn, ad_entity_id, bid_leido)
            confirmadas += 1
        elif old_value is not None and abs(bid_leido - old_value) <= TOLERANCIA_CADENA:
            with tx_durable(conn):
                id_probe = _ledger(
                    conn, decision_id, "probe", {"leido": str(bid_leido)}, quota_cobrada=False
                )
                if id_probe is not None:
                    _sella_ledger(conn, id_probe, ack=ack, resultado="ok:no_aplicado")
                registra_sin_aplicar(
                    conn,
                    decision_id,
                    aplicador.cycle_id_ejecutor,
                    MOTIVO_PERDIDA,
                    detalle={"sello": "fallo:readback_ambiguo", "leido": str(bid_leido)},
                )
                _actualiza_cache(conn, ad_entity_id, bid_leido)
            fallidas += 1
        else:
            with tx_durable(conn):
                id_probe = _ledger(
                    conn, decision_id, "probe", {"leido": str(bid_leido)}, quota_cobrada=False
                )
                if id_probe is not None:
                    _sella_ledger(conn, id_probe, ack=ack, resultado="fallo:divergencia_readback")
                _confirma_resumen(
                    conn,
                    decision_id,
                    ack or {},
                    False,
                    aplicador.cycle_id_ejecutor,
                    leido=bid_leido,
                )
                _actualiza_cache(conn, ad_entity_id, bid_leido)
            fallidas += 1
    return confirmadas, fallidas


def reconcilia_bids(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    *,
    bid_quantizado: Callable[[Decimal], Decimal],
) -> tuple[int, int]:
    """Reconciliacion del ledger de BIDS sin sello (ADV-04) de UNA plataforma.

    PRIMERO reconcilia intentos sellados-ambiguos sin desenlace (TEMPORADA 01 B);
    despues los sin sello, reutilizando el mismo `cliente_holder` lazy.
    Devuelve (confirmadas, fallidas)."""
    confirmadas = fallidas = 0
    cliente_holder: list = [None]
    c_a, f_a = _reconcilia_ambiguos_sellados(
        conn, aplicador, platform, cliente_holder, bid_quantizado=bid_quantizado
    )
    confirmadas += c_a
    fallidas += f_a
    filas = conn.execute(_SQL_INTENTOS_BID_SIN_SELLO, (platform,)).fetchall()
    if not filas:
        return (confirmadas, fallidas)
    for id_attempt, decision_id, _payload, ad_entity_id, new_value, _moneda in filas:
        if gate_ancestros(conn, ad_entity_id) is not None:
            if _con_efecto_temporada_abierto(conn, decision_id):
                fallidas += 1
                continue
            with tx_durable(conn):
                _sella_ledger(conn, id_attempt, ack=None, resultado=RESULTADO_ANCESTRO_NO_ENABLED)
                registra_sin_aplicar(
                    conn,
                    decision_id,
                    aplicador.cycle_id_ejecutor,
                    MOTIVO_ENTIDAD_NO_DECISORA,
                    detalle={"sello": RESULTADO_ANCESTRO_NO_ENABLED},
                )
            fallidas += 1
            continue
        if cliente_holder[0] is None:
            cliente_holder[0] = aplicador._cliente()
        identidad = _identidad(conn, ad_entity_id)
        if identidad is None or identidad[0] not in _KINDS_DECISORAS or new_value is None:
            with tx_durable(conn):
                _sella_ledger(
                    conn, id_attempt, ack=None, resultado="fallo:reconciliado_sin_identidad"
                )
                registra_sin_aplicar(
                    conn,
                    decision_id,
                    aplicador.cycle_id_ejecutor,
                    MOTIVO_ENTIDAD_NO_DECISORA,
                    detalle={"sello": "fallo:reconciliado_sin_identidad"},
                )
            fallidas += 1
            continue
        kind, external_id = identidad
        _, path, contenedor, param = _KINDS_DECISORAS[kind]
        try:
            bid_leido = _bid_de_readback(cliente_holder[0], path, contenedor, param, external_id)
        except AdsApiError:
            with tx_durable(conn):
                _sella_ledger(conn, id_attempt, ack=None, resultado="fallo:readback_ambiguo")
            fallidas += 1
            continue
        if bid_leido == bid_quantizado(new_value):
            ack = {"fuente": "readback", "bid": str(bid_leido)}
            with tx_durable(conn):
                _sella_ledger(conn, id_attempt, ack=ack, resultado="ok:reconciliado")
                _confirma_resumen(
                    conn, decision_id, ack, True, aplicador.cycle_id_ejecutor, leido=bid_leido
                )
                _actualiza_cache(conn, ad_entity_id, bid_leido)
            confirmadas += 1
            continue
        resultado = (
            "fallo:divergencia_readback" if bid_leido is not None else "fallo:readback_sin_bid"
        )
        ack = {"fuente": "readback", "bid": str(bid_leido)} if bid_leido is not None else None
        if bid_leido is None and _con_efecto_temporada_abierto(conn, decision_id):
            fallidas += 1
            continue
        with tx_durable(conn):
            _sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
            if bid_leido is not None:
                _actualiza_cache(conn, ad_entity_id, bid_leido)
            _confirma_resumen(
                conn, decision_id, ack or {}, False, aplicador.cycle_id_ejecutor, leido=bid_leido
            )
        fallidas += 1
    return (confirmadas, fallidas)
