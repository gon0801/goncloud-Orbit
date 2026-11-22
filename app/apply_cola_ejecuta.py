"""Ejecucion de mutaciones y reversas de cortes `pause`/`negative` (ORBIT 04).

Extraido de `app/apply_cola.py` (Ola D3b). Conserva el candado
`PERMITIDOS_IMPORTAR_ADS_WRITE`: no importa `app.ads.write`, usa el cliente
recibido por parametro o detras de `Aplicador._cliente()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import psycopg

from app import apply, apply_harvest, apply_producto, biblioteca
from app.apply_cuota import CapSaturado
from app.db import settings_vigentes, tx_durable
from app.lecturas_historia import presupuestos_campanas_hoy
from app.optimizer import recuperacion, temporada

if TYPE_CHECKING:
    from app.apply import Aplicador

_SQL_CACHE_ESTADO = """
UPDATE ad_entity_state SET status = %s, synced_at = now() WHERE ad_entity_id = %s
"""

_SQL_FILA = """
SELECT id, kind, ad_entity_id, search_term, decision_id, request_payload, estado
  FROM apply_queue WHERE id = %s
"""

# TEMPORADA 01 F: grupos (ad_group) con una pausa de producto en vuelo. UNA
# fuente: la usan el recorrido del ciclo (salta hojas) y el aplicador de
# reactivaciones (no reactiva una hoja de un grupo con pausa de producto).
SQL_GRUPOS_PAUSA_PRODUCTO = """
SELECT DISTINCT pa.parent_id
  FROM apply_queue q
  JOIN ad_entity pa ON pa.id = q.ad_entity_id AND pa.kind = 'product_ad'
 WHERE q.platform = %s::platform AND q.kind = 'pause' AND q.modo = 'live'
   AND q.estado IN ('pending_veto', 'released', 'applying') AND q.vetoed_at IS NULL
   AND (SELECT count(*) FILTER (WHERE li.product_id IS NULL) = 0
               AND count(DISTINCT li.product_id) = 1
          FROM ad_entity o
          LEFT JOIN ad_entity_state s ON s.ad_entity_id = o.id
          LEFT JOIN listing li ON li.id = o.listing_id AND li.platform = o.platform
         WHERE o.parent_id = pa.parent_id AND o.kind = 'product_ad'
           AND s.status IS DISTINCT FROM 'ARCHIVED')
"""

_SQL_RESUMES_CICLO = """
SELECT id, ad_entity_id, inputs
  FROM decision
 WHERE cycle_id = %s AND kind = 'resume'
 ORDER BY id
"""

_SQL_ENTIDAD_REACTIVACION = """
SELECT k.parent_id AS ad_group_id, ag.parent_id AS campaign_id, s.status
  FROM ad_entity k
  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group'
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = k.id
 WHERE k.id = %s
"""


@dataclass(frozen=True)
class FilaCola:
    id: int
    kind: str
    ad_entity_id: int
    search_term: str | None
    decision_id: int
    request_payload: dict
    estado: str


@dataclass(frozen=True)
class DecisionResume:
    """Una decision kind='resume' del ciclo (motor temporada, bloque F)."""

    id: int
    ad_entity_id: int
    inputs: dict


@dataclass(frozen=True)
class ResumenReactivacion:
    """Resultado del lote de reactivaciones: confirmadas por Amazon, fallidas,
    sin cupo, omitidas por una guarda y avisos de cap saturado."""

    confirmadas: int = 0
    fallidas: int = 0
    sin_quota: int = 0
    omitidas: int = 0
    caps_saturados: tuple[CapSaturado, ...] = ()


def fila_cola(conn: psycopg.Connection, queue_id: int) -> FilaCola | None:
    """Carga UNA fila de la cola como FilaCola (None si no existe)."""
    fila = conn.execute(_SQL_FILA, (queue_id,)).fetchone()
    return FilaCola(*fila) if fila is not None else None


def grupos_con_pausa_producto(conn: psycopg.Connection, platform: str) -> frozenset[int]:
    """Los ad_group con una pausa de producto en vuelo (solo lectura)."""
    return frozenset(f[0] for f in conn.execute(SQL_GRUPOS_PAUSA_PRODUCTO, (platform,)).fetchall())


def resumes_del_ciclo(conn: psycopg.Connection, cycle_id: int) -> list[DecisionResume]:
    """Las decisiones resume del ciclo, para el aplicador de reactivaciones."""
    return [
        DecisionResume(id=f[0], ad_entity_id=f[1], inputs=f[2] or {})
        for f in conn.execute(_SQL_RESUMES_CICLO, (cycle_id,)).fetchall()
    ]


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


def aplica_reactivaciones(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    decisiones: list[DecisionResume],
    *,
    tick=None,
) -> ResumenReactivacion:
    """Reactiva (resume) las pausas que el ciclo decidio reactivar, con TODAS
    las guardas del diseno: temporada en live, campana y grupo ENABLED, goal
    habilitado y no 'off', sin pausa de producto en vuelo del grupo, campana no
    topada, hoja PAUSED y fuera de «Mantener pausada»; cuota 'pause' y la
    reversa de la fila de la cola (tope de 3 en el ledger, que no se reinicia
    aunque haya otra resume). Sin cupo o con una guarda en contra no se toca
    Amazon: el ciclo siguiente lo vuelve a decidir."""
    if not decisiones:
        return ResumenReactivacion()
    platform = aplicador._platform
    settings = settings_vigentes(conn)
    if temporada.modo_desde_settings(settings, platform) != "live":
        return ResumenReactivacion(omitidas=len(decisiones))
    config = temporada.desde_settings(settings)
    if config is None:
        # live sin configuracion de calendario: no se inventa una reactivacion.
        return ResumenReactivacion(omitidas=len(decisiones))
    mantener = config.mantener_pausadas
    topada = temporada.campana_topada_desde_settings(settings)
    hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    grupos_pausa = grupos_con_pausa_producto(conn, platform)
    presupuestos = presupuestos_campanas_hoy(conn, platform, hoy, topada)
    confirmadas = fallidas = sin_quota = omitidas = 0
    caps_saturados: list[CapSaturado] = []
    for decision in decisiones:
        apply._tick(tick)
        queue_id = decision.inputs.get("queue_id")
        fila = fila_cola(conn, queue_id) if queue_id is not None else None
        if fila is None or fila.ad_entity_id != decision.ad_entity_id:
            omitidas += 1
            continue
        if decision.ad_entity_id in mantener:
            omitidas += 1
            continue
        if apply.gate_ancestros(conn, decision.ad_entity_id) is not None:
            omitidas += 1
            continue
        if aplicador.modo_efectivo(conn, decision.ad_entity_id, escalera_global="live") != "live":
            omitidas += 1
            continue
        entidad = conn.execute(_SQL_ENTIDAD_REACTIVACION, (decision.ad_entity_id,)).fetchone()
        if entidad is None or entidad[2] != "PAUSED" or entidad[0] in grupos_pausa:
            omitidas += 1
            continue
        presupuesto = presupuestos.get(entidad[1])
        if recuperacion.presupuesto_topado_de(presupuesto) is True:
            omitidas += 1
            continue
        usada, saturada = apply.consume_quota_y_sello(conn, platform, "pause")
        if not usada:
            sin_quota += 1
            continue
        if saturada:
            evento = apply.evento_cap_saturado(conn, platform, "pause")
            if evento is not None:
                caps_saturados.append(evento)
        if reversa_pause(conn, _cliente_mutacion(aplicador), fila, tick=tick, durable=True):
            confirmadas += 1
        else:
            fallidas += 1
    return ResumenReactivacion(
        confirmadas=confirmadas,
        fallidas=fallidas,
        sin_quota=sin_quota,
        omitidas=omitidas,
        caps_saturados=tuple(caps_saturados),
    )
