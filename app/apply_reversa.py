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

from app.ads.client import AdsApiError
from app.ads.sp_wire import id_de_ack
from app.apply_cuota import DecisionBid
from app.apply_estados import (
    RESULTADO_NO_APLICADO,
    RESULTADO_OK,
    TOPE_INTENTOS_CAMBIO,
    AccionCambio,
    EstadoCambio,
    EventoCambio,
    Transicion,
    decidir,
    envia_tras_lectura,
    estado_inicial_desde_hechos,
    evento_desde_lectura_bid,
    transicion_desde_error,
)
from app.apply_store import (
    _KINDS_DECISORAS,
    _actualiza_cache,
    _bid_de_readback,
    _identidad,
    _json_seguro,
    _ledger,
    _sella_ledger,
    _tick,
    bid_previo_comprobado,
    estado_de_reversa,
    hechos_reversa,
    ultima_escritura_desde,
)
from app.db import tx_durable

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

_SQL_REVERSA_OK = """
SELECT EXISTS (
    SELECT 1 FROM apply_attempt
     WHERE decision_id = %s AND tipo = 'reversa' AND resultado = 'ok'
)
"""


def _reversa_ya_hecha(conn: psycopg.Connection, decision_id: int) -> bool:
    """True si la decision ya tiene UNA reversa confirmada (resultado 'ok')."""
    return conn.execute(_SQL_REVERSA_OK, (decision_id,)).fetchone()[0]


# Fuente unica del negative_id: la confirmacion sellada de la decision. Un ACK
# remoto trae `negativeKeywordId`; una confirmacion por LIST (ARREGLOS 05 B)
# declara su fuente y trae el id en `keywordId`. El cuerpo de la API jamas lo
# acepta.
_SQL_RESUMEN_ACK = """
SELECT platform_ack FROM decision_application WHERE decision_id = %s
"""


def _id_del_resumen(ack: object) -> str | None:
    from app.apply_negative import FUENTE_LIST

    if not isinstance(ack, dict):
        return None
    if ack.get("fuente") == FUENTE_LIST:
        candidato = ack.get("keywordId")
        if (
            isinstance(candidato, str | int)
            and not isinstance(candidato, bool)
            and str(candidato).strip()
        ):
            return str(candidato)
        return None
    return id_de_ack(ack, "negativeKeywordId")


# Sin resumen sellado, queda el ACK remoto del ultimo intento aplicado.
_SQL_ACK_APLICADO = """
SELECT ack FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'normal' AND resultado = 'ok' AND ack IS NOT NULL
 ORDER BY seq DESC LIMIT 1
"""


def _negative_id_del_ledger(conn: psycopg.Connection, decision_id: int) -> str:
    """El id del negative aplicado de la decision, desde su confirmacion
    durable: el resumen sellado (el ACK remoto de la creacion o la
    confirmacion por LIST de un efecto reconciliado) y, sin resumen, el ACK
    remoto del ultimo intento aplicado (ARREGLOS 05 B). No basta un
    `resultado`: hace falta un id legible en el ACK."""
    fila = conn.execute(_SQL_RESUMEN_ACK, (decision_id,)).fetchone()
    negative_id = _id_del_resumen(fila[0] if fila is not None else None)
    if negative_id is None:
        fila = conn.execute(_SQL_ACK_APLICADO, (decision_id,)).fetchone()
        negative_id = id_de_ack(fila[0], "negativeKeywordId") if fila is not None else None
    if negative_id is not None:
        return negative_id
    raise NegativeIdNoResoluble(
        f"no se resolvio el negative_id de la decision {decision_id} desde su"
        " confirmacion (sin platform_ack con id legible)"
    )


def _lee_bid_vivo(cliente: AdsWriteClient, identidad: tuple[str, str]) -> Decimal | None:
    """El bid que Amazon tiene ahora en la hoja. None = sin lectura legible."""
    _, path, contenedor, param = _KINDS_DECISORAS[identidad[0]]
    return _bid_de_readback(cliente, path, contenedor, param, identidad[1])


def _confirma_reversa_bid_por_lectura(
    conn: psycopg.Connection,
    decision: DecisionBid,
    identidad: tuple[str, str],
    payload_bid: Callable[[str, str, Decimal], dict],
    previo: Decimal,
) -> bool:
    """Amazon ya esta en el valor previo: la reversa queda hecha sin enviar
    nada. La fila nace y se sella en la misma transaccion, con un ack que
    declara su fuente."""
    id_attempt = _ledger(
        conn,
        decision.id,
        "reversa",
        payload_bid(identidad[0], identidad[1], previo),
        quota_cobrada=False,
        bid_previo=previo,
    )
    if id_attempt is None:
        return False
    with tx_durable(conn):
        _sella_ledger(
            conn,
            id_attempt,
            ack={"conciliado": True, "fuente": "readback", "bid": str(previo)},
            resultado=RESULTADO_OK,
        )
        _actualiza_cache(conn, decision.ad_entity_id, previo)
    return True


def _aplica_lectura_reversa_bid(
    conn: psycopg.Connection,
    *,
    intento_id: int,
    ad_entity_id: int,
    bid_vivo: Decimal | None,
    destino: Decimal,
    escritos: frozenset[Decimal],
    ack_put: dict | None = None,
    inmediata: bool = False,
) -> Transicion:
    """Camino unico para toda lectura de una reversa de bid: la inmediata
    tras el PUT y la que resuelve un intento abierto antes de otro envio.

    Clasifica con `evento_desde_lectura_bid`, consulta
    `decidir(EstadoCambio.INCIERTO, evento)` y, cuando `sella_intento`, sella
    el ledger y deja en el cache lo leido."""
    transicion = decidir(
        EstadoCambio.INCIERTO,
        evento_desde_lectura_bid(bid_vivo, destino=destino, escritos=escritos),
    )
    if not transicion.sella_intento:
        return transicion
    if transicion.accion == AccionCambio.SELLAR_APLICADO:
        resultado = RESULTADO_OK
        ack = ack_put if ack_put is not None else {"conciliado": True, "fuente": "readback"}
    elif transicion.accion == AccionCambio.CONTAR_INTENTO and not inmediata:
        resultado, ack = RESULTADO_NO_APLICADO, ack_put
    else:
        resultado, ack = "fallo:divergencia_readback", ack_put
    with tx_durable(conn):
        _sella_ledger(conn, intento_id, ack=ack, resultado=resultado)
        if bid_vivo is not None:
            _actualiza_cache(conn, ad_entity_id, bid_vivo)
    return transicion


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
    """Reversa del bid: PUT con el bid que Orbit leyo en Amazon antes del PUT
    aplicado, ledger tipo 'reversa' exento de quota, readback y sello. No
    toca decision_application ni applied_count.

    ARREGLOS 05 B. El valor al que se vuelve es `apply_attempt.bid_previo` del
    intento aplicado. Solo una decision aplicada antes de 0095, sin esa
    lectura, vuelve a `decision.old_value`, el unico valor registrado.
    Un intento de reversa abierto se resuelve leyendo antes de otro envio.
    Antes del PUT se relee la hoja: si el valor vivo no lo escribio Orbit (otra
    mano lo movio despues), no se envia; si ya esta en el valor previo, se
    confirma por lectura. El cache queda con LO LEIDO."""
    previo = bid_previo_comprobado(conn, decision.id, respaldo=decision.old_value)
    if previo is None:
        raise ReversaNoAplicada(
            f"reversa de bid {decision.id}: sin bid leido antes del PUT ni old_value:"
            " no hay valor al que volver y no se inventa (regla 3)"
        )
    if decision.value_currency is None:
        raise ValueError(f"reversa de bid {decision.id}: exige value_currency (regla 7)")
    identidad = _identidad(conn, decision.ad_entity_id)
    if identidad is None or identidad[0] not in _KINDS_DECISORAS:
        return False
    destino = bid_quantizado(previo)
    # Solo la ultima escritura confirmada desde esta decision prueba que la
    # puja vigente es de Orbit (revision): un importe antiguo suyo que vuelve
    # a la hoja lo puso otra mano.
    ultima = ultima_escritura_desde(conn, decision.ad_entity_id, decision.id)
    escritos = frozenset() if ultima is None else {bid_quantizado(ultima)}

    transicion = decidir(estado_de_reversa(conn, decision.id), EventoCambio.NUEVO_HORARIO)
    if transicion.accion == AccionCambio.LEER_RESULTADO:
        abierto = hechos_reversa(conn, decision.id).intento_abierto_id
        if abierto is not None:
            vivo = _lee_bid_vivo(cliente, identidad)
            resuelta = _aplica_lectura_reversa_bid(
                conn,
                intento_id=abierto,
                ad_entity_id=decision.ad_entity_id,
                bid_vivo=None if vivo is None else bid_quantizado(vivo),
                destino=destino,
                escritos=escritos,
            )
            if resuelta.accion != AccionCambio.CONTAR_INTENTO:
                return resuelta.accion == AccionCambio.SELLAR_APLICADO
        transicion = decidir(estado_de_reversa(conn, decision.id), EventoCambio.NUEVO_HORARIO)
    if transicion.accion != AccionCambio.ENVIAR:
        return False
    vivo = _lee_bid_vivo(cliente, identidad)
    evento = evento_desde_lectura_bid(
        None if vivo is None else bid_quantizado(vivo), destino=destino, escritos=escritos
    )
    previa = decidir(EstadoCambio.PENDIENTE, evento)
    if previa.accion == AccionCambio.SELLAR_APLICADO:
        return _confirma_reversa_bid_por_lectura(conn, decision, identidad, payload_bid, destino)
    if not envia_tras_lectura(EstadoCambio.PENDIENTE, evento):
        return False
    id_attempt = _ledger(
        conn,
        decision.id,
        "reversa",
        payload_bid(identidad[0], identidad[1], previo),
        quota_cobrada=False,
        bid_previo=vivo,
    )
    if id_attempt is None:
        return False
    conn.commit()
    try:
        _tick(tick)
        if identidad[0] == "keyword":
            resp_http = cliente.actualizar_bid_keyword(
                identidad[1], previo, decision.value_currency
            )
        else:
            resp_http = cliente.actualizar_bid_target(identidad[1], previo, decision.value_currency)
    except error_mutacion_cls as exc:
        _sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        if transicion_desde_error(
            EstadoCambio.PENDIENTE, exc, es_mutacion_determinista=True
        ).propaga_aborto:
            raise
        return False
    ack = _json_seguro(resp_http)
    _tick(tick)
    bid_leido = _lee_bid_vivo(cliente, identidad)
    if bid_leido is None:
        with conn.transaction():
            _sella_ledger(conn, id_attempt, ack=ack, resultado="fallo:readback_sin_bid")
        return False
    resuelta = _aplica_lectura_reversa_bid(
        conn,
        intento_id=id_attempt,
        ad_entity_id=decision.ad_entity_id,
        bid_vivo=bid_quantizado(bid_leido),
        destino=destino,
        escritos=escritos,
        ack_put=ack,
        inmediata=True,
    )
    return resuelta.accion == AccionCambio.SELLAR_APLICADO


def _confirmada_en_la_frontera_manual(accion) -> bool:
    """Resultado de una reversa MANUAL. Un rechazo de identidad o permisos de
    Amazon (401/403) es un resultado, `confirmada: false`: la API responde con
    el cuerpo estructurado y no con un 500. El barrido automatico de
    reactivaciones recibe el mismo error y lo propaga, porque detras de ese
    rechazo no debe salir ninguna mutacion (ARREGLOS 05 A, revision). Los
    demas errores de la API siguen propagandose."""
    try:
        # Una lectura sin estado (PENDIENTE_LECTURA) no confirma.
        return accion() is True
    except AdsApiError as exc:
        if transicion_desde_error(EstadoCambio.PENDIENTE, exc).propaga_aborto:
            return False
        raise


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
        # ARREGLOS 05 B: la reversa vuelve al bid que Orbit leyo en Amazon antes
        # del PUT aplicado (o al old_value de la cache, si se aplico antes de
        # 0095). Sin ningun valor al que volver no se construye el cliente.
        if bid_previo_comprobado(conn, decision_id, respaldo=fila[3]) is None or fila[5] is None:
            raise ReversaNoAplicada(
                f"decision {decision_id} sin bid leido antes del PUT ni old_value, o"
                " sin moneda: no hay valor al que volver (regla 7)"
            )
        confirmada = _confirmada_en_la_frontera_manual(
            lambda: reversa_bid_fn(conn, cliente_reversa(fila[7], transport=transport), decision)
        )
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

            def _reversa_pause_manual() -> bool | str:
                with apply_cola.candado_reversa_pause(conn, fila.decision_id) as tomado:
                    hechos = apply_cola._hechos_reversa(conn, fila.decision_id) if tomado else None
                    estado = estado_inicial_desde_hechos(
                        en_vuelo_ajeno=not tomado,
                        tiene_intento_abierto=(
                            hechos.intento_abierto_id is not None if hechos else False
                        ),
                        resultado_terminal=hechos.resultado_terminal if hechos else None,
                        intentos_totales=hechos.intentos_totales if hechos else 0,
                        tope_intentos=TOPE_INTENTOS_CAMBIO,
                    )
                    if estado == EstadoCambio.APLICADO:
                        raise ReversaYaHecha(fila.decision_id)
                    transicion = decidir(estado, EventoCambio.NUEVO_HORARIO)
                    if transicion.accion == AccionCambio.LEER_RESULTADO:
                        tr_lectura = apply_cola.resuelve_reversa_abierta(conn, cliente, fila)
                        if (
                            tr_lectura is not None
                            and tr_lectura.accion != AccionCambio.CONTAR_INTENTO
                        ):
                            return tr_lectura.accion == AccionCambio.SELLAR_APLICADO
                        assert hechos is not None
                        estado_post = estado_inicial_desde_hechos(
                            en_vuelo_ajeno=False,
                            tiene_intento_abierto=False,
                            resultado_terminal=None,
                            intentos_totales=hechos.intentos_totales,
                            tope_intentos=TOPE_INTENTOS_CAMBIO,
                        )
                        transicion = decidir(estado_post, EventoCambio.NUEVO_HORARIO)
                    if transicion.accion != AccionCambio.ENVIAR:
                        return False
                    return apply_cola.reversa_pause(conn, cliente, fila)

            confirmada = _confirmada_en_la_frontera_manual(_reversa_pause_manual)
            return {"tipo": "pause", "queue_id": queue_id, "confirmada": confirmada}
        negative_id = _negative_id_del_ledger(conn, fila.decision_id)
        confirmada = _confirmada_en_la_frontera_manual(
            lambda: apply_cola.reversa_negative(conn, cliente, fila, negative_id)
        )
        return {
            "tipo": "negative",
            "queue_id": queue_id,
            "negative_id": str(negative_id),
            "confirmada": confirmada,
        }

    raise ValueError(f"tipo de reversa desconocido: {tipo!r} (bid|pause|negative)")
