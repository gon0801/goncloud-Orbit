"""Regreso del dueno sobre rachas de recortes de bids (extraido de app/apply.py, Fase 3.4).

Reusa `apply.reversa_manual(tipo="bid")` sobre la primera decision de la racha
vigente de recortes. No importa `app.ads.write` (el candado de arquitectura
mantiene a `app/apply.py` como unico dueno del cliente de escritura).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx
import psycopg
from psycopg import pq

from app import apply
from app.pantalla_danadas import lee_danadas
from app.redaction import scrub

ORIGEN_REGRESO_DUENO = "regreso_del_dueno"
# TEMPORADA 01 (0093): los movimientos de temporada son invisibles para la
# racha de la ACCION (no cuentan ni cortan), igual que en la pantalla.
ORIGEN_TEMPORADA = "temporada"

# La racha sale de v_cambio_bid, no de otro camino (fuente unica de la
# historia de bids). Sin filtro por motivo: la racha se deriva de los bids.
_SQL_CAMBIOS_HOJA = """
SELECT confirmado_el, bid_antes, bid_despues, origen, decision_id, moneda
  FROM v_cambio_bid
 WHERE hoja_id = %s
 ORDER BY confirmado_el, decision_id
"""

# El sello de la ultima reversa de la decision: de ahi sale el motivo cuando
# Amazon no confirma (el detalle vive en el ledger, regla 10).
_SQL_MOTIVO_REVERSA = """
SELECT resultado FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'reversa'
 ORDER BY seq DESC LIMIT 1
"""

# El bid vigente real (el bid puede cambiar fuera de Orbit: la vista de
# cambios no lo sabe y su ultimo bid_despues queda rancio).
_SQL_VIGENTE = """
SELECT current_bid FROM v_hoja_activa WHERE hoja_id = %s
"""

# Sello y destino confirmado del regreso: el `bid` del payload es lo que se
# envio y cuya lectura de vuelta confirmo (ARREGLOS 05 B).
_SQL_FIN_REVERSA = """
SELECT finished_at, request_payload->>'bid' FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'reversa' AND resultado = 'ok'
 ORDER BY seq DESC LIMIT 1
"""


class SinRachaDeRecortes(Exception):
    """La hoja no tiene recortes vigentes que regresar (409)."""


class ConfirmacionDesactualizada(Exception):
    """La confirmacion no trae el N vigente de pendientes (409: la lista cambio)."""


class RegresoNoConfirmado(Exception):
    """Amazon no confirmo el regreso (502: el sello vive en el ledger)."""


class VigentePorEncima(Exception):
    """El bid vigente ya esta por encima del destino del regreso (409: no
    se mueve; un regreso nunca baja el bid)."""


@dataclass(frozen=True)
class RegresoHecho:
    hoja_id: int
    bid_antes: Decimal  # el vigente al regresar (de v_hoja_activa; el ultimo cambio rancia)
    bid_ahora: Decimal  # el restaurado (bid anterior a la racha)
    moneda: str
    decision_revertida_id: int
    actor: str
    regreso_confirmado_at: str | None = None

    def como_dict(self) -> dict:
        return {
            "hoja_id": self.hoja_id,
            "bid_antes": str(self.bid_antes),
            "bid_ahora": str(self.bid_ahora),
            "moneda": self.moneda,
            "decision_revertida_id": self.decision_revertida_id,
            "actor": self.actor,
            "regreso_confirmado_at": self.regreso_confirmado_at,
        }


def _celda_cambio(fila, indice: int, nombre: str):
    """Tupla o dict_row (la conexion puede venir con dict_row)."""
    return fila[nombre] if isinstance(fila, dict) else fila[indice]


def _clave_orden_cambio(cambio):
    """Ordena cambios por (confirmado_el, decision_id) sin reventar ante
    la tercera rama de la vista (panel: `decision_id` NULL de
    `ajuste_de_campana` empatado con un id rompia min/max con TypeError).
    El NULL ordena ultimo, como en el SQL."""
    return (cambio[0], cambio[4] is None, cambio[4] or 0)


def _racha_vigente_hoja(cambios: list[tuple]) -> list[tuple]:
    """Recortes posteriores a la ultima subida y al ultimo regreso del
    dueno: la racha de la ACCION (esta funcion) corta en el ultimo
    regreso_del_dueno y su primera es el primer recorte posterior a el.
    Difiere de la racha de la PANTALLA (pantalla_danadas._racha_vigente,
    guia P.3a cambio 2, donde el regreso no corta). Sin regreso previo,
    igual que la pantalla: el regreso no cuenta (su bid_antes es NULL) y
    una subida del motor si corta, incluido un regreso_por_desplome. Los
    movimientos de temporada (TEMPORADA 01, origen 'temporada') tampoco
    cuentan ni cortan: un movimiento planeado no es dano ni el fin del
    dano del motor. Cada cambio es (confirmado_el, bid_antes, bid_despues,
    origen, decision_id, moneda)."""
    subidas = [
        c[0]
        for c in cambios
        if c[3] != ORIGEN_TEMPORADA and c[1] is not None and c[2] is not None and c[2] > c[1]
    ]
    ultima = max(subidas) if subidas else None
    regresos = [(c[0], c[4]) for c in cambios if c[3] == ORIGEN_REGRESO_DUENO]
    ultimo_regreso = max(regresos) if regresos else None
    return [
        c
        for c in cambios
        if c[3] not in (ORIGEN_REGRESO_DUENO, ORIGEN_TEMPORADA)
        and c[1] is not None
        and c[2] is not None
        and c[2] < c[1]
        and (ultima is None or c[0] >= ultima)
        and (ultimo_regreso is None or (c[0], c[4]) > ultimo_regreso)
    ]


def _actor_no_vacio(actor: str) -> None:
    """El actor es rastro obligatorio: "   " pasa el min_length de pydantic
    y lo caza aqui (el endpoint lo mapea a 422, como el descarte C.5)."""
    if not actor or not actor.strip():
        raise ValueError("el regreso exige actor no vacio")


def regreso_del_dueno(
    conn: psycopg.Connection,
    *,
    hoja_id: int,
    actor: str,
    transport: httpx.BaseTransport | None = None,
) -> RegresoHecho:
    """Regresa el bid de la hoja al que tenia antes de su racha vigente de
    recortes. Reusa `apply.reversa_manual(tipo="bid")` sobre la PRIMERA decision
    de la racha (ledger pre-HTTP, readback, exenta de cupo): no hay escritura
    nueva a Amazon. El motor lo ve en `v_cambio_bid` con origen
    `regreso_del_dueno`. Idempotente: una racha ya regresada levanta
    ReversaYaHecha (409), nunca escribe dos veces. Si el vigente ya esta
    por encima del destino levanta VigentePorEncima (409): un regreso
    nunca baja el bid, y tampoco se mueve si el vigente ya es el destino.
    `transport` es la puerta
    de tests (MockTransport); el endpoint NO lo pasa."""
    _actor_no_vacio(actor)
    cambios = [
        (
            _celda_cambio(f, 0, "confirmado_el"),
            _celda_cambio(f, 1, "bid_antes"),
            _celda_cambio(f, 2, "bid_despues"),
            _celda_cambio(f, 3, "origen"),
            _celda_cambio(f, 4, "decision_id"),
            _celda_cambio(f, 5, "moneda"),
        )
        for f in conn.execute(_SQL_CAMBIOS_HOJA, (hoja_id,)).fetchall()
    ]
    racha = _racha_vigente_hoja(cambios)
    if not racha:
        raise SinRachaDeRecortes(f"la hoja {hoja_id} no tiene racha vigente de recortes")
    primera = min(racha, key=_clave_orden_cambio)
    ultimo = max(cambios, key=_clave_orden_cambio)
    # El vigente se lee ANTES del HTTP: despues nace la fila del regreso.
    fila_vigente = conn.execute(_SQL_VIGENTE, (hoja_id,)).fetchone()
    vigente = _celda_cambio(fila_vigente, 0, "current_bid") if fila_vigente is not None else None
    # ARREGLOS 05 B: el destino del regreso es el bid que la reversa va a
    # restaurar, cuantizado como el envio (el leido antes del PUT; old_value
    # solo para historia anterior a la 0095). Guarda, respuesta e historial
    # usan ese mismo valor.
    previo = apply.bid_previo_comprobado(conn, primera[4], respaldo=primera[1])
    destino = None if previo is None else apply.bid_del_wire(previo)
    if apply.hechos_reversa(conn, primera[4]).intento_abierto_id is not None:
        # Un intento abierto se resuelve SOLO por lectura, antes de la guarda: si
        # Amazon aplico el regreso y se perdio la respuesta, la ingesta sincroniza
        # el destino y la guarda de un envio nuevo lo rechazaria para siempre.
        resuelta = apply.reversa_manual(
            conn,
            tipo="bid",
            decision_id=primera[4],
            transport=transport,
            solo_resolver=True,
        )
        if resuelta["confirmada"]:
            return _hecho(
                conn, hoja_id, primera, vigente=vigente, ultimo=ultimo, destino=destino, actor=actor
            )
    # Si el vigente ya esta EN el destino o por encima, no se mueve: un regreso
    # nunca baja el bid ni cierra la racha sin mover la puja. Con vigente
    # desconocido (NULL: hereda del grupo) no hay nada que comparar y se sigue
    # como antes.
    if vigente is not None and destino is not None and vigente >= destino:
        raise VigentePorEncima(
            f"la hoja {hoja_id} trae vigente {vigente} por encima del destino {destino}"
        )
    reversa = apply.reversa_manual(conn, tipo="bid", decision_id=primera[4], transport=transport)
    if not reversa["confirmada"]:
        fila = conn.execute(_SQL_MOTIVO_REVERSA, (primera[4],)).fetchone()
        motivo = fila[0] if fila is not None else "sin sello en el ledger"
        raise RegresoNoConfirmado(f"regreso de la hoja {hoja_id} sin confirmar: {motivo}")
    return _hecho(
        conn, hoja_id, primera, vigente=vigente, ultimo=ultimo, destino=destino, actor=actor
    )


def _hecho(
    conn: psycopg.Connection,
    hoja_id: int,
    primera,
    *,
    vigente,
    ultimo,
    destino,
    actor: str,
) -> RegresoHecho:
    """El regreso hecho: `bid_ahora` es el bid del intento de reversa que quedo
    ok, lo que se envio y cuya lectura confirmo (ARREGLOS 05 B)."""
    fin = conn.execute(_SQL_FIN_REVERSA, (primera[4],)).fetchone()
    sello = fin[0].isoformat() if fin is not None and fin[0] is not None else None
    confirmado = Decimal(fin[1]) if fin is not None and fin[1] is not None else None
    return RegresoHecho(
        hoja_id=hoja_id,
        bid_antes=vigente if vigente is not None else ultimo[2],
        bid_ahora=confirmado if confirmado is not None else destino,
        moneda=primera[5],
        decision_revertida_id=primera[4],
        actor=actor,
        regreso_confirmado_at=sello,
    )


def regreso_del_dueno_todas(
    conn: psycopg.Connection,
    *,
    plataforma: str,
    confirmacion: str,
    actor: str,
    transport: httpx.BaseTransport | None = None,
) -> tuple[RegresoHecho | str, ...]:
    """Boton "Regresar todas" (decision D6). Relee la pantalla de danadas,
    exige la confirmacion literal `REGRESAR <N> KEYWORDS` con el N vigente
    (numero de hojas con `ya_regresada` en falso; 409 si la lista cambio) y
    llama a `regreso_del_dueno` hoja por hoja. Una falla no detiene a las
    demas: en su lugar va el motivo en texto, con su hoja. Idempotente."""
    _actor_no_vacio(actor)
    pantalla = lee_danadas(conn, plataforma=plataforma)
    pendientes = [h for h in pantalla.hojas if not h.ya_regresada]
    esperada = f"REGRESAR {len(pendientes)} KEYWORDS"
    if confirmacion != esperada:
        raise ConfirmacionDesactualizada(
            f"confirmacion desactualizada: esperaba {esperada!r} (la lista cambio)"
        )
    salidas: list[RegresoHecho | str] = []
    for hoja in pendientes:
        try:
            salidas.append(
                regreso_del_dueno(conn, hoja_id=hoja.hoja_id, actor=actor, transport=transport)
            )
        except Exception as exc:
            if not conn.autocommit and conn.info.transaction_status == pq.TransactionStatus.INERROR:
                conn.rollback()
            salidas.append(f"hoja {hoja.hoja_id}: {scrub(str(exc))}")
    return tuple(salidas)
