"""Ejecucion de mutaciones y reversas de cortes `pause`/`negative` (ORBIT 04).

Extraido de `app/apply_cola.py` (Ola D3b). Conserva el candado
`PERMITIDOS_IMPORTAR_ADS_WRITE`: no importa `app.ads.write`, usa el cliente
recibido por parametro o detras de `Aplicador._cliente()`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psycopg
from psycopg import pq

from app import apply, apply_producto
from app.ads.client import AdsApiError
from app.apply_cuota import CapSaturado
from app.apply_estados import (
    RESULTADO_NO_APLICADO,
    RESULTADO_OK,
    RESULTADO_PAUSADA_POR_DUENO,
    AccionCambio,
    EstadoCambio,
    EventoCambio,
    Transicion,
    decidir,
    estado_inicial_desde_hechos,
    evento_desde_lectura_estado,
    transicion_desde_error,
)
from app.apply_negative import (
    _ejecuta_negative,
    reversa_negative,  # noqa: F401 - re-export estable
)
from app.apply_store import hechos_reversa as _hechos_reversa
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
    # Con la lectura sin estado: el intento queda abierto y falta leerla.
    pendientes_de_lectura: int = 0
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
        if transicion_desde_error(
            EstadoCambio.PENDIENTE, exc, es_mutacion_determinista=True
        ).propaga_aborto:
            raise
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
        aplicador.confirma_cercada(fila.decision_id, ack, verify)
        if estado is not None:
            conn.execute(_SQL_CACHE_ESTADO, (estado, fila.ad_entity_id))
        _termina(conn, fila, "applied" if verify else "failed")
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
    cerca_dueno=None,
) -> bool | str:
    """Reversa del pause: reanudar keyword/target, readback y cache con lo leido.

    Si la lectura de vuelta no trae el estado devuelve `PENDIENTE_LECTURA`: el
    intento queda ABIERTO (Amazon pudo haber habilitado la hoja) y la
    recuperacion lo lee antes de cobrar cuota o reenviar (ARREGLOS 05 A,
    revision). Reenviar sin leer repetiria un PUT que quizas ya aplico.

    `durable=True` (TEMPORADA 01 F) confirma el sello con `tx_durable`: en la
    fase de apply la conexion de produccion ya esta en INTRANS y un
    `conn.transaction()` seria un savepoint; un aborto posterior perderia el
    sello de una habilitacion que Amazon si aplico y el ciclo la repetiria.

    `cerca_dueno`, si se entrega (ruta automatica), bloquea y valida la fila
    del claim dentro de la transaccion que sella y actualiza el cache: si un
    sucesor tomo el claim con el LIST en vuelo, el ejecutor desplazado no sella
    ni sobreescribe `ad_entity_state`."""
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
        cierre = tx_durable(conn) if durable else conn.transaction()
        with cierre:
            if cerca_dueno is not None:
                cerca_dueno()
            apply._sella_ledger(
                conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
            )
        if transicion_desde_error(
            EstadoCambio.PENDIENTE, exc, es_mutacion_determinista=True
        ).propaga_aborto:
            raise
        return False
    ack = apply._json_seguro(resp_http)
    if tick is not None:
        tick()
    _, path, contenedor, param = apply._KINDS_DECISORAS[identidad[0]]
    estado = apply._estado_de_readback(cliente, path, contenedor, param, identidad[1])
    transicion = _aplica_lectura_reversa(
        conn,
        intento_id=id_attempt,
        ad_entity_id=fila.ad_entity_id,
        estado_vivo=estado,
        ack_put=ack,
        durable=durable,
        cerca_dueno=cerca_dueno,
    )
    if transicion.accion == AccionCambio.MANTENER_INCIERTO:
        return PENDIENTE_LECTURA
    return transicion.accion == AccionCambio.SELLAR_APLICADO


# Lo que devuelve `reversa_pause` cuando la lectura de vuelta no trajo el estado
# de la hoja: el intento queda abierto y la hoja pendiente de lectura.
PENDIENTE_LECTURA = "pendiente_de_lectura"

_SQL_REVERSA_SIN_SELLO = """
SELECT id, started_at FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'reversa' AND finished_at IS NULL
 ORDER BY seq DESC
 LIMIT 1
"""

_SQL_REVERSAS_PAUSE_ABIERTAS = """
SELECT q.id, q.kind, q.ad_entity_id, q.search_term, q.decision_id,
       q.request_payload, q.estado
  FROM apply_queue q
  JOIN ad_entity e ON e.id = q.ad_entity_id
 WHERE q.platform = %s::platform
   AND q.kind = 'pause'
   AND e.kind IN ('keyword', 'product_target')
   AND EXISTS (
       SELECT 1 FROM apply_attempt a
        WHERE a.decision_id = q.decision_id
          AND a.tipo = 'reversa'
          AND a.finished_at IS NULL
   )
 ORDER BY q.id
"""

_SQL_ESTADO_CACHE_PREVIO = """
SELECT status, synced_at FROM ad_entity_state WHERE ad_entity_id = %s
"""

_SQL_CANDADO_REVERSA_TOMAR = "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))"
_SQL_CANDADO_REVERSA_SOLTAR = "SELECT pg_advisory_unlock(hashtextextended(%s, 0))"


@contextmanager
def candado_reversa_pause(conn: psycopg.Connection, decision_id: int) -> Iterator[bool]:
    """Exclusion de sesion compartida por decision entre la reactivacion
    automatica y la reversa manual (ARREGLOS 05 A, revision).

    Sobrevive al `conn.commit()` previo al PUT y PostgreSQL la libera si la
    conexion cae. No bloquea (`pg_try_advisory_lock`): si ya hay una reversa
    activa sobre esa decision cede `False` para no conciliar ni reenviar sobre
    un intento en vuelo."""
    llave = f"orbit:reversa-pause:{decision_id}"
    tomado = bool(conn.execute(_SQL_CANDADO_REVERSA_TOMAR, (llave,)).fetchone()[0])
    if not conn.autocommit:
        conn.commit()
    try:
        yield tomado
    finally:
        if tomado and not conn.closed:
            if not conn.autocommit and conn.info.transaction_status == pq.TransactionStatus.INERROR:
                conn.rollback()
            conn.execute(_SQL_CANDADO_REVERSA_SOLTAR, (llave,))
            if not conn.autocommit:
                conn.commit()


def _aplica_lectura_reversa(
    conn: psycopg.Connection,
    *,
    intento_id: int,
    ad_entity_id: int,
    estado_vivo: str | None,
    ack_put: dict | None = None,
    cache_previo_estado: str | None = None,
    cache_previo_posterior_al_intento: bool = False,
    durable: bool = True,
    cerca_dueno=None,
) -> Transicion:
    """Camino unico para toda lectura de vuelta de una reversa `pause`:
    inmediata tras PUT (`reversa_pause`) y de conciliacion
    (`resuelve_reversa_abierta`).

    Clasifica `estado_vivo` con `evento_desde_lectura_estado`, consulta
    `decidir(EstadoCambio.INCIERTO, evento)` y, cuando `sella_intento`, sella
    el ledger y actualiza el cache bajo `cerca_dueno`. Un desenlace terminal
    (`ARCHIVED` u otro estado no PAUSED/ENABLED) se persiste como
    `fallo:entidad_<estado>` tanto en la lectura inmediata como en la
    conciliacion."""
    evento = evento_desde_lectura_estado(
        estado_vivo,
        destino=apply.ESTADO_WIRE_ENABLED,
        cache_previo_estado=cache_previo_estado,
        cache_previo_posterior_al_intento=cache_previo_posterior_al_intento,
    )
    transicion = decidir(EstadoCambio.INCIERTO, evento)
    if not transicion.sella_intento:
        return transicion
    assert estado_vivo is not None  # toda transicion con sella_intento viene de lectura con estado
    if transicion.accion == AccionCambio.SELLAR_APLICADO:
        resultado = RESULTADO_OK
        ack = ack_put if ack_put is not None else {"conciliado": True}
    elif transicion.accion == AccionCambio.SELLAR_PAUSADO_DUENO:
        resultado = RESULTADO_PAUSADA_POR_DUENO
        ack = {"conciliado": True, "cache_previo": apply.ESTADO_WIRE_ENABLED}
    elif transicion.accion == AccionCambio.CONTAR_INTENTO:
        resultado = "fallo:divergencia_readback" if ack_put is not None else RESULTADO_NO_APLICADO
        ack = ack_put
    else:
        resultado = f"fallo:entidad_{estado_vivo.lower()}"
        ack = ack_put
    cierre = tx_durable(conn) if durable else conn.transaction()
    with cierre:
        if cerca_dueno is not None:
            cerca_dueno()
        apply._sella_ledger(conn, intento_id, ack=ack, resultado=resultado)
        conn.execute(_SQL_CACHE_ESTADO, (estado_vivo, ad_entity_id))
    return transicion


def resuelve_reversa_abierta(
    conn: psycopg.Connection,
    cliente,
    fila: FilaCola,
    *,
    tick=None,
    cerca_dueno=None,
) -> Transicion | None:
    """Resuelve por LECTURA la reversa de esta fila que quedo sin sello usando
    la tabla unica `app.apply_estados.TABLA_TRANSICIONES`.

    Devuelve `None` si no habia intento abierto, o la `Transicion` decidida por
    `decidir(EstadoCambio.INCIERTO, evento)`:
    - `SELLAR_APLICADO` (`APLICADO`): sella `ok` con `conciliado: True` y cache.
    - `SELLAR_PAUSADO_DUENO` (`PAUSADO_POR_DUENO`): sella
      `RESULTADO_PAUSADA_POR_DUENO` sin atribuir `ok` ni reenviar.
    - `CONTAR_INTENTO` (`PENDIENTE`): sella `ok:no_aplicado` y actualiza cache.
    - `SELLAR_TERMINAL` (`ARCHIVADO` / `SIN_IDENTIDAD`): cierra sin reenviar.
    - `MANTENER_INCIERTO` (`INCIERTO`): deja el intento abierto sin tocar nada.

    Si se entrega `cerca_dueno` (ruta automatica), valida el claim dentro de la
    transaccion que sella y actualiza el cache."""
    intento = conn.execute(_SQL_REVERSA_SIN_SELLO, (fila.decision_id,)).fetchone()
    if intento is None:
        return None
    intento_id, iniciado_el = intento
    cache_previo = conn.execute(_SQL_ESTADO_CACHE_PREVIO, (fila.ad_entity_id,)).fetchone()
    identidad = apply._identidad(conn, fila.ad_entity_id)
    if identidad is None or identidad[0] not in apply._KINDS_DECISORAS:
        return decidir(EstadoCambio.INCIERTO, EventoCambio.ENTIDAD_SIN_IDENTIDAD)
    apply._tick(tick)
    _, path, contenedor, param = apply._KINDS_DECISORAS[identidad[0]]
    estado = apply._estado_de_readback(cliente, path, contenedor, param, identidad[1])
    posterior_al_intento = (
        cache_previo is not None and cache_previo[1] is not None and cache_previo[1] >= iniciado_el
    )
    return _aplica_lectura_reversa(
        conn,
        intento_id=intento_id,
        ad_entity_id=fila.ad_entity_id,
        estado_vivo=estado,
        cache_previo_estado=cache_previo[0] if cache_previo is not None else None,
        cache_previo_posterior_al_intento=posterior_al_intento,
        durable=True,
        cerca_dueno=cerca_dueno,
    )


def resuelve_reversa_sin_sello(
    conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola, *, tick=None
) -> Transicion | None:
    """Version con cerca del claim para el barrido automatico de reactivaciones."""
    if conn.execute(_SQL_REVERSA_SIN_SELLO, (fila.decision_id,)).fetchone() is None:
        return None
    return resuelve_reversa_abierta(
        conn, aplicador._cliente(), fila, tick=tick, cerca_dueno=aplicador.exige_dueno
    )


@dataclass(frozen=True)
class ReconciliacionReversas:
    """Resultado de `reconcilia_reversas_pause`: resumen agregado y mapa
    `queue_id -> Transicion | None` (`None` = error remoto al leer) de cada
    fila abierta procesada en esta corrida."""

    resumen: ResumenReactivacion = ResumenReactivacion()
    por_queue_id: Mapping[int, Transicion | None] = ()  # type: ignore[assignment]


def hay_reversas_pause_abiertas(conn: psycopg.Connection, platform: str) -> bool:
    """True si existe al menos una reversa `pause` con intento abierto (solo SQL)."""
    return conn.execute(_SQL_REVERSAS_PAUSE_ABIERTAS, (platform,)).fetchone() is not None


def reconcilia_reversas_pause(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    *,
    tick=None,
) -> ReconciliacionReversas:
    """Concilia por LECTURA de forma incondicional toda reversa `pause` con
    intento abierto en el ledger, independientemente del origen, del dia, del
    tope de intentos o de cualquier apagador (temporada o global) (ARREGLOS 05
    A, 8a y 9a pasadas de Astra).

    Corre en `_fase_reconciliacion` antes de decidir: nunca cobra cuota ni
    envia PUT; solo lee y sella segun `TABLA_TRANSICIONES`."""
    filas = [
        FilaCola(*f) for f in conn.execute(_SQL_REVERSAS_PAUSE_ABIERTAS, (platform,)).fetchall()
    ]
    if not filas:
        return ReconciliacionReversas(resumen=ResumenReactivacion(), por_queue_id={})
    confirmadas = fallidas = omitidas = pendientes_de_lectura = 0
    por_queue_id: dict[int, Transicion | None] = {}
    for fila in filas:
        apply._tick(tick)
        with candado_reversa_pause(conn, fila.decision_id) as tomado:
            hechos = _hechos_reversa(conn, fila.decision_id) if tomado else None
            estado = estado_inicial_desde_hechos(
                en_vuelo_ajeno=not tomado,
                tiene_intento_abierto=hechos.intento_abierto_id is not None if hechos else False,
                resultado_terminal=hechos.resultado_terminal if hechos else None,
                intentos_totales=hechos.intentos_totales if hechos else 0,
                tope_intentos=apply.TOPE_INTENTOS,
            )
            transicion = decidir(estado, EventoCambio.NUEVO_HORARIO)
            if transicion.accion == AccionCambio.LEER_RESULTADO:
                try:
                    tr_lectura = resuelve_reversa_sin_sello(conn, aplicador, fila, tick=tick)
                except AdsApiError as exc:
                    if transicion_desde_error(EstadoCambio.INCIERTO, exc).propaga_aborto:
                        raise
                    _rollback_si_abierta(conn)
                    pendientes_de_lectura += 1
                    por_queue_id[fila.id] = None
                    continue
                if tr_lectura is not None:
                    por_queue_id[fila.id] = tr_lectura
                    dc, df, do, dp = _contabiliza_transicion_reactivacion(tr_lectura)
                    confirmadas += dc
                    fallidas += df
                    omitidas += do
                    pendientes_de_lectura += dp
                continue
            por_queue_id[fila.id] = transicion
            dc, df, do, dp = _contabiliza_transicion_reactivacion(transicion)
            confirmadas += dc
            fallidas += df
            omitidas += do
            pendientes_de_lectura += dp
    return ReconciliacionReversas(
        resumen=ResumenReactivacion(
            confirmadas=confirmadas,
            fallidas=fallidas,
            omitidas=omitidas,
            pendientes_de_lectura=pendientes_de_lectura,
        ),
        por_queue_id=por_queue_id,
    )


def _rollback_si_abierta(conn: psycopg.Connection) -> None:
    if not conn.autocommit and conn.info.transaction_status != pq.TransactionStatus.IDLE:
        conn.rollback()


def _contabiliza_transicion_reactivacion(transicion: Transicion) -> tuple[int, int, int, int]:
    """Traduce una `Transicion` a deltas `(confirmadas, fallidas, omitidas, pendientes)`
    actuando solo sobre `accion` (jamas sobre `estado_siguiente`).

    Solo `SELLAR_APLICADO` atribuye confirmacion a esta corrida: un `NO_TOCAR`
    sobre un estado `APLICADO` previo cuenta como omitida y no vuelve a sumar a
    `applied_count`."""
    if transicion.accion == AccionCambio.SELLAR_APLICADO:
        return (1, 0, 0, 0)
    if transicion.origen_incompleto:
        return (0, 0, 0, 1)
    if transicion.accion in (AccionCambio.SELLAR_TERMINAL, AccionCambio.SELLAR_RECHAZADO):
        return (0, 1, 0, 0)
    return (0, 0, 1, 0)


def _elegible_para_put_reactivacion(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    decision: DecisionResume,
    mantener,
    grupos_pausa: set[int],
    presupuestos: dict,
) -> bool:
    """Guardas que condicionan cobrar cuota y enviar un nuevo PUT de reactivacion."""
    if decision.ad_entity_id in mantener:
        return False
    if apply.gate_ancestros(conn, decision.ad_entity_id) is not None:
        return False
    if aplicador.modo_efectivo(conn, decision.ad_entity_id, escalera_global="live") != "live":
        return False
    entidad = conn.execute(_SQL_ENTIDAD_REACTIVACION, (decision.ad_entity_id,)).fetchone()
    if entidad is None or entidad[2] != "PAUSED" or entidad[0] in grupos_pausa:
        return False
    return recuperacion.presupuesto_topado_de(presupuestos.get(entidad[1])) is not True


def _contabiliza_ya_reconciliada(tr: Transicion | None) -> tuple[int, int, int, int] | None:
    """Si la fila ya se reconcilio en `_fase_reconciliacion` de esta misma
    corrida y NO quedo lista para reenviar (`CONTAR_INTENTO`), devuelve sus
    deltas sin repetir la lectura; `None` si corresponde continuar a envio."""
    if tr is None:
        return (0, 0, 0, 1)
    if tr.accion == AccionCambio.CONTAR_INTENTO:
        return None
    return _contabiliza_transicion_reactivacion(tr)


def _contexto_reactivacion(conn: psycopg.Connection, platform: str):
    """Carga el contexto de elegibilidad para nuevos PUT de reactivacion.
    Los intentos abiertos se concilian siempre; este contexto solo condiciona ENVIAR."""
    settings = settings_vigentes(conn)
    config = (
        temporada.desde_settings(settings)
        if temporada.modo_desde_settings(settings, platform) == "live"
        else None
    )
    if config is None:
        return False, frozenset(), frozenset(), {}
    topada = temporada.campana_topada_desde_settings(settings)
    hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    return (
        True,
        config.mantener_pausadas,
        grupos_con_pausa_producto(conn, platform),
        presupuestos_campanas_hoy(conn, platform, hoy, topada),
    )


def aplica_reactivaciones(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    decisiones: list[DecisionResume],
    *,
    tick=None,
    reconciliadas_en_corrida: Mapping[int, Transicion | None] | None = None,
) -> ResumenReactivacion:
    """Reactiva (resume) las pausas que el ciclo decidio reactivar consultando
    la tabla unica `app.apply_estados.TABLA_TRANSICIONES`.

    Toda decision pasa por `estado_inicial_desde_hechos` + `decidir(estado,
    NUEVO_HORARIO)` bajo `candado_reversa_pause`: lo en vuelo se respeta, lo
    incierto se lee antes de mirar filtros o tope, lo pausado por el dueno o
    agotado al tope (`apply.TOPE_INTENTOS`) no vuelve a salir, y solo lo
    pendiente y elegible cobra cuota y envia PUT."""
    if not decisiones:
        return ResumenReactivacion()
    platform = aplicador._platform
    temporada_en_vivo, mantener, grupos_pausa, presupuestos = _contexto_reactivacion(conn, platform)
    confirmadas = fallidas = sin_quota = omitidas = pendientes_de_lectura = 0
    caps_saturados: list[CapSaturado] = []
    ya_reconciliadas = reconciliadas_en_corrida or {}
    for decision in decisiones:
        apply._tick(tick)
        queue_id = decision.inputs.get("queue_id")
        fila = fila_cola(conn, queue_id) if queue_id is not None else None
        if fila is None or fila.ad_entity_id != decision.ad_entity_id:
            omitidas += 1
            continue
        if fila.id in ya_reconciliadas:
            deltas = _contabiliza_ya_reconciliada(ya_reconciliadas[fila.id])
            if deltas is not None:
                confirmadas += deltas[0]
                fallidas += deltas[1]
                omitidas += deltas[2]
                pendientes_de_lectura += deltas[3]
                continue
        with candado_reversa_pause(conn, fila.decision_id) as tomado:
            hechos = _hechos_reversa(conn, fila.decision_id) if tomado else None
            estado = estado_inicial_desde_hechos(
                en_vuelo_ajeno=not tomado,
                tiene_intento_abierto=hechos.intento_abierto_id is not None if hechos else False,
                resultado_terminal=hechos.resultado_terminal if hechos else None,
                intentos_totales=hechos.intentos_totales if hechos else 0,
                tope_intentos=apply.TOPE_INTENTOS,
            )
            transicion = decidir(estado, EventoCambio.NUEVO_HORARIO)
            if transicion.accion == AccionCambio.LEER_RESULTADO:
                try:
                    tr_lectura = resuelve_reversa_sin_sello(conn, aplicador, fila, tick=tick)
                except AdsApiError as exc:
                    if transicion_desde_error(EstadoCambio.INCIERTO, exc).propaga_aborto:
                        raise
                    _rollback_si_abierta(conn)
                    pendientes_de_lectura += 1
                    continue
                if tr_lectura is not None and tr_lectura.accion != AccionCambio.CONTAR_INTENTO:
                    dc, df, do, dp = _contabiliza_transicion_reactivacion(tr_lectura)
                    confirmadas += dc
                    fallidas += df
                    omitidas += do
                    pendientes_de_lectura += dp
                    continue
                # El intento incierto se sello como no aplicado: reevalua contra
                # el tope antes de cobrar cuota o enviar un nuevo PUT.
                assert hechos is not None
                estado_post = estado_inicial_desde_hechos(
                    en_vuelo_ajeno=False,
                    tiene_intento_abierto=False,
                    resultado_terminal=None,
                    intentos_totales=hechos.intentos_totales,
                    tope_intentos=apply.TOPE_INTENTOS,
                )
                transicion = decidir(estado_post, EventoCambio.NUEVO_HORARIO)
            if transicion.accion != AccionCambio.ENVIAR:
                dc, df, do, dp = _contabiliza_transicion_reactivacion(transicion)
                confirmadas += dc
                fallidas += df
                omitidas += do
                pendientes_de_lectura += dp
                continue
            if not temporada_en_vivo or not _elegible_para_put_reactivacion(
                conn, aplicador, decision, mantener, grupos_pausa, presupuestos
            ):
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
            try:
                reanudada = reversa_pause(
                    conn,
                    _cliente_mutacion(aplicador),
                    fila,
                    tick=tick,
                    durable=True,
                    cerca_dueno=aplicador.exige_dueno,
                )
            except AdsApiError as exc:
                if transicion_desde_error(EstadoCambio.INCIERTO, exc).propaga_aborto:
                    raise
                _rollback_si_abierta(conn)
                pendientes_de_lectura += 1
                continue
            confirmadas += reanudada is True
            pendientes_de_lectura += reanudada is PENDIENTE_LECTURA
            fallidas += reanudada is False
    return ResumenReactivacion(
        confirmadas=confirmadas,
        fallidas=fallidas,
        sin_quota=sin_quota,
        omitidas=omitidas,
        pendientes_de_lectura=pendientes_de_lectura,
        caps_saturados=tuple(caps_saturados),
    )
