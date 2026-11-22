"""Nucleo del aplicador de decisiones (ORBIT 04, task 2.1).

El nucleo que EJECUTA mutaciones en Amazon: re-resuelve el modo POR
DECISION, cobra quota, nace la fila del ledger PRE-HTTP, muta, hace readback
con el scope sellado y sella ledger + resumen + cache. Vive fuera de
app/optimizer/ como cycle.py (importa psycopg y el cliente de escritura; el
motor sigue puro). Los BIDS aplican en su ciclo SIN pasar por la cola (sellado
1); la cola de CORTES es 2.2 y la integracion en corre_ciclo es 2.4.

Diseno SELLADO (plans/orbit-04.md decisiones 7-12, 14, 16, 21; docs/APPLY.md
§3-§5, §8-§9; la secuencia §3 adaptada a bids — sin cola):

- RE-RESOLUCION POR DECISION (`Aplicador.modo_efectivo`): JAMAS filtra por
  inputs.modo ni cycle.mode (el residual sellado de cycle.py: una decision
  nacida en ciclo shadow puede aplicarse en un ciclo live y viceversa).
  Re-resuelve escalera global (la del envelope, pasada por parametro) +
  goal de la entidad FRESCO de la base (goals.resuelve_goal + goal.mode +
  enabled + existencia de entidad/state). 'live' solo si TODO aplica; si no,
  'shadow': no aplica, cero HTTP (ni token LWA — el write client se
  construye LAZY en el primer apply live).
- QUOTA ATOMICA (`consume_quota`): INSERT ... ON CONFLICT (motor, quota_date)
  DO UPDATE SET used = used + 1 WHERE used < cap RETURNING used, con
  quota_date = dia UTC DE LA BASE en la expresion ((now() AT TIME ZONE
  'UTC')::date — el trigger de 0002 valida contra ese dia, no contra
  CURRENT_DATE) y cap leido de la config VIGENTE (misma resolucion que el
  trigger apply_cap_de_config). Sin clave -> False y NO nace fila
  (fail-closed, sellado 8). Se consume ANTES del HTTP, UNA vez por
  operacion logica; el 429 reintenta el write client SIN re-cobrar (mismo
  intento del ledger). Reversas EXENTAS.
- SELECCION DE BIDS BAJO CAP (sellado 8): prioridad por urgencia de
  hemorragia banda_menos_25 > banda_menos_12 > banda_mas_15 (inputs.motivo,
  el vocabulario del motor de bids) y dentro de cada banda por costo de la
  ventana (inputs.ventanas.cortes.cost como Decimal) DESC — costo None
  (regla 3: desconocido) queda al final de SU banda. Los que no cupieron
  vuelven como descarte estructurado (motivo), JAMAS reintentados; la
  ausencia de fila no se confunde con un bug porque el conteo lo declara.
- SECUENCIA SELLADA POR MUTACION: re-resolucion -> LECTURA FRESCA del bid
  (TEMPORADA 01 E: si Amazon no responde o trae un bid distinto del visto al
  decidir, no hay cuota ni ledger ni PUT: `sin_respuesta` o
  `bid_cambiado_fuera`, exclusion de temporada y cache con lo leido) ->
  consume_quota (si no cabe: descartado, JAMAS HTTP) -> fila del ledger
  PRE-HTTP (seq = count(*)+1 del decision_id; tope 3: COUNT >= 3 -> no existe
  4o intento, salta con motivo) + COMMIT (la intencion durable ANTES del HTTP) -> HTTP
  (write client) -> readback por LIST FRESCO con el MISMO scope sellado
  (probe 2.5: el GET directo de entidad sp responde 403, retirado) ->
  sellar ledger (ack/resultado/finished_at una vez) -> UPSERT
  decision_application (la terna confirmed_at+platform_ack+verify_ok JUNTA
  al confirmar; applied_cycle_id = ciclo EJECUTOR solo al confirmar) ->
  UPDATE del cache ad_entity_state con LO LEIDO (jamas lo enviado, sellado
  16: sin esto el ciclo siguiente calcula sobre el bid viejo) -> applied_count
  del ciclo ejecutor (sellado 21).
- FALLOS: >=400 (AdsApiErrorMutacion) se captura y el ledger sella
  resultado='fallo http <status>: <cuerpo saneado>'; la decision NO se marca
  aplicada. 5xx/fallo ambiguo (AdsApiError) SUBE sin capturar: el ledger
  queda SIN sello — la fila ES el rastro del crash (reconciliacion 2.2/2.3)
  y NO se reintenta.
- REVERSA DE BID (regla 7, `reversa_bid`): PUT con old_value, misma
  secuencia (ledger tipo 'reversa', quota_cobrada=false EXENTA), readback,
  sello. No toca decision_application ni applied_count: el resumen queda
  como estaba.
- RECONCILIACION DE LEDGER SIN SELLO (`reconcilia_bids`, ADV-04 de la review
  adversaria de phase 2; matriz §6.1 "Ledger sin sello - bid"): los bids no
  viven en apply_queue, asi que su rastro de crash SOLO existe en el ledger.
  LIST fresco del readback: lectura == pedido → confirmar (sello
  ok:reconciliado + resumen + ciclo EJECUTOR + cache); divergencia → sello
  de fallo y UN reintento bajo tope-3 (quota ya cobrada); ambiguo → failed
  SIN reintento. Filas reversa/probe sin sello: RESIDUAL declarado (APPLY.md
  §13).

SELLADO por el probe 2.5 (corrida autorizada del dueno 2026-08-26, ledger
apply_attempt ids 1-20, log out/smoke-apply-20260826.log): el readback de
entidad vive por LIST (POST /sp/{keywords|targets}/list — el GET directo
responde 403, retirado; apply_attempt 4-5), contenedores 'keywords'/
'targetingClauses' y estados del wire UPPER (ESTADO_WIRE_*). El state del
REQUEST del PUT de pause/resume quedo SELLADO el 2026-08-27: UPPER, igual
que el wire del readback (write.py ESTADO_PUT_*; re-exportada aqui — QW2,
una sola fuente).

Ronda de CROSS-REVIEW del dueno (codex+qwen, shapes del probe 2.5,
out/cross-review-shapes-*.log): el readback por LIST PAGINA por nextToken
con tope (CX1/QW1 + CX6) — el body {} solo trae la PRIMERA pagina y la
cuenta real tiene 1334 keywords / 549 targets: una entidad en pagina 2+
era "ausente" (falsa divergencia con quota cobrada).

`owner`/`job_key` se guardan para el ownership-check pre-HTTP de la
integracion (2.4, decision 11); el heartbeat `tick` se llama DURANTE la
mutacion y el readback.

GANCHOS de 2.4 (declarados): (a) `credentials`/`profile_id` OPCIONALES — el
ciclo construye un Aplicador sin credenciales cuando solo necesita
`modo_efectivo` (escalera shadow: cero HTTP por construccion) y `_cliente()`
revienta fail-closed si un camino live lo intenta sin ellas; (b)
`guard_http` — callback que corre ANTES DE CADA HTTP del write client
(envolviendo el transport: mutacion, readback, listas y token LWA incluidos).
Es la unica forma de garantizar el ownership-check pre-HTTP de la decision 11
SIN tocar write.py (su allowlist de imports esta sellada): todos los HTTP del
Aplicador pasan por el transport.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal

import httpx
import psycopg
from psycopg.types.json import Json

from app import temporada_io
from app.ads.client import AdsApiError, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import PerfilAds, evaluar_perfiles

# Re-export para apply_cola/apply_harvest: solo app/apply.py importa app.ads.write,
# asi el enum del PUT de pause/resume mantiene una sola fuente en write.py.
from app.ads.write import (
    ESTADO_PUT_ENABLED as ESTADO_PUT_ENABLED,
)
from app.ads.write import (
    ESTADO_PUT_PAUSED as ESTADO_PUT_PAUSED,
)
from app.ads.write import (
    MODO_CONFIRMADO_LIVE,
    PLATAFORMA_MONEDA,
    AdsApiErrorMutacion,
    AdsWriteClient,
    _bid_payload,
)
from app.apply_cuota import (  # noqa: F401 - re-export estable para apply_cola/harvest/cycle
    _SQL_INTENTOS_BID_SIN_SELLO,
    CAP_MAXIMO,
    KINDS_QUOTA,
    MOTIVO_BID_CAMBIADO_FUERA,
    MOTIVO_BID_EN_VUELO,
    MOTIVO_BID_INCOMPLETO,
    MOTIVO_CAMPANA_NO_ENABLED,
    MOTIVO_CHOQUE_CLAVE,
    MOTIVO_ENTIDAD_NO_DECISORA,
    MOTIVO_ESPERA_PROTECCION,
    MOTIVO_ESPERA_TARGET,
    MOTIVO_FALLO_HTTP,
    MOTIVO_FUERA_DE_CAP,
    MOTIVO_GRUPO_NO_ENABLED,
    MOTIVO_MODO_NO_LIVE,
    MOTIVO_PERDIDA,
    MOTIVO_SIN_QUOTA,
    MOTIVO_SIN_RESPUESTA,
    MOTIVO_TOPE_INTENTOS,
    MOTIVO_YA_APLICADA,
    MOTIVOS_SIN_APLICAR,
    RESULTADO_ANCESTRO_NO_ENABLED,
    CapSaturado,
    DecisionBid,
    ResultadoAplicador,
    _cap_de_config,
    bids_del_ciclo,
    cap_desde_settings,
    consume_quota,
    consume_quota_y_sello,
    estado_quota,
    evento_cap_saturado,
    gate_ancestros,
    hojas_con_bid_sin_sello,
    intentos_sin_sello,
    motor_quota,
    orden_bajo_cupo,
    prioridad_bajo_cupo,
    registra_sin_aplicar,
)
from app.db import tx_durable
from app.optimizer import goals as g
from app.redaction import scrub

# Tope de intentos por decision: "no existe 4o intento" es un COUNT verificable
# contra el ledger (sellado 10 / brief §4.1).
TOPE_INTENTOS = 3


# Excepciones de la REVERSA MANUAL (3.1): el endpoint de app/api_write.py las
# mapea 1:1 a HTTP (409 precondicion / 409 ya revertida / 404 inexistente /
# 422 id no resoluble / 503 sin perfil). Van aqui para que el candado de
# test_architecture (solo app/apply.py construye AdsWriteClient) siga intacto.
class ReversaNoAplicada(Exception):
    """La reversa exige un apply confirmado y no lo hay: precondicion (409)."""


class ReversaYaHecha(Exception):
    """Ya existe UNA reversa confirmada (tipo 'reversa' resultado 'ok') para la
    decision: una reversa es UNA por decision (regla 7; ADV-3 — sin este
    candado el endpoint despachaba HTTP real ilimitado en loop). 409."""

    def __init__(self, decision_id: int) -> None:
        super().__init__(
            f"ya revertida: la decision {decision_id} ya tiene una reversa"
            " confirmada en el ledger (una reversa es UNA por decision)"
        )


class ReversaInexistente(Exception):
    """La decision/fila pedida no existe (404)."""


class NegativeIdNoResoluble(Exception):
    """El negative_id no se resolvio del ledger (422; regla 3: el id jamas se
    inventa — y desde ADV-2 el caller JAMAS lo pasa: una sola fuente)."""


class SinPerfilReversa(Exception):
    """Sin perfil ACEPTADO para la plataforma en /v2/profiles: la reversa
    aborta fail-closed (503; regla 3: jamas un profile inventado)."""


def perfil_aceptado_de(
    credentials: AdsCredentials, platform: str, *, transport: httpx.BaseTransport | None = None
) -> PerfilAds | None:
    """Perfil ACEPTADO de la plataforma desde GET /v2/profiles + evaluar_perfiles.

    UNA fuente de la resolucion (regla 2): la fabrica del ciclo
    (`cycle._aplicador_real`) y la reversa manual (`reversa_manual`) comparten
    este camino — el profile_id JAMAS se inventa ni se duplica la resolucion.
    None = sin perfil aceptado para la plataforma (el caller aborta
    fail-closed)."""
    for perfil in evaluar_perfiles(AdsClient(credentials, transport=transport)):
        if perfil.aceptado and perfil.platform == platform:
            return perfil
    return None


# Tope de paginas para readback por /list (page size v3 ~1000, cubre ~20k
# filas): evita colgar el ciclo ante un nextToken infinito. Si la cuenta supera
# este tope, una entidad mas alla aparece como ausente.
TOPE_PAGINAS_READBACK = 20

# Estados del WIRE en el readback por LIST. SELLADO por el probe 2.5
# (2026-08-26, ledger probe ids 1-20, log out/smoke-apply-20260826.log): el
# list trae state UPPER — ENABLED/PAUSED/ARCHIVED (apply_attempt 19-20:
# targets list con ENABLED y PAUSED vivos). 'userPaused' NO existe en la
# RESPUESTA: el REQUEST del PUT quedo SELLADO el 2026-08-27 (corrida de
# reactivacion autorizada por el dueno, evidencia
# out/reactiva-campanas-20260827.log) con el MISMO enum UPPER del wire
# (write.py ESTADO_PUT_* = PAUSED/ENABLED). ARCHIVED = operativamente muerto
# (el "delete" v3 archiva): NO confirma entidad viva ni pause verificado.
ESTADO_WIRE_ENABLED = "ENABLED"
ESTADO_WIRE_PAUSED = "PAUSED"
ESTADO_WIRE_ARCHIVED = "ARCHIVED"


_SQL_IDENTIDAD = """
SELECT kind, external_id FROM ad_entity WHERE id = %s
"""

# El tope cuenta solo intentos 'normal': las reversas nunca consumen
# presupuesto de reintentos.
_SQL_COUNT_INTENTOS = """
SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'normal'
"""

# F2 (A.3): la secuencia es GLOBAL por decision (max+1 sobre TODOS los
# tipos): el tope de reintentos cuenta aparte y solo 'normal'. Sin esto, la
# segunda hermana repetiria seq (el count de normales no se mueve con las
# hermanas) y dos filas compartirian seq.
_SQL_MAX_SEQ = """
SELECT coalesce(max(seq), 0) FROM apply_attempt WHERE decision_id = %s
"""

_SQL_INSERT_LEDGER = """
INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload, quota_cobrada)
VALUES (%s, %s, %s, %s, %s)
RETURNING id
"""

_SQL_SELLA_LEDGER = """
UPDATE apply_attempt SET ack = %s, resultado = %s, finished_at = now() WHERE id = %s
"""

_SQL_YA_APLICADA = """
SELECT EXISTS (
    SELECT 1 FROM decision_application WHERE decision_id = %s AND verify_ok IS TRUE
)
"""

_SQL_CONFIRMAR_RESUMEN = """
INSERT INTO decision_application (decision_id, confirmed_at, platform_ack, verify_ok)
VALUES (%s, now(), %s, %s)
ON CONFLICT (decision_id) DO UPDATE
   SET confirmed_at = EXCLUDED.confirmed_at,
       platform_ack = EXCLUDED.platform_ack,
       verify_ok = EXCLUDED.verify_ok
"""

_SQL_SELLA_CICLO_EJECUTOR = """
UPDATE decision_application SET applied_cycle_id = %s WHERE decision_id = %s
"""

_SQL_APPLIED_COUNT = """
UPDATE optimizer_cycle SET applied_count = coalesce(applied_count, 0) + 1 WHERE id = %s
"""

_SQL_ACTUALIZA_CACHE = """
UPDATE ad_entity_state SET current_bid = %s, synced_at = now() WHERE ad_entity_id = %s
"""


# TEMPORADA 01 B (revision): intentos SELLADOS con lectura ambigua y sin
# desenlace. Amazon puede haber APLICADO el cambio (el sync ya trajo el
# destino al cache): liberar la hoja a ciegas produce doble recorte. Se
# reconcilian con GET fresco JAMAS reenviando el PUT:
#   puja == destino      -> confirmar la decision (resumen + efecto
#                           'confirmado' + cache), con un probe como rastro;
#   puja == old_value    -> no aplico: 'perdida' en decision_sin_aplicar y
#                           efecto 'sin_aplicar' (la hoja queda libre; el
#                           siguiente ciclo decide de nuevo sobre el bid real);
#   un tercer valor      -> divergencia (efecto 'divergencia' + exclusion
#                           si difiere del old_value);
#   GET ambiguo de nuevo -> queda TODO abierto y el siguiente ciclo lo
#                           reintenta.
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


def _reconcilia_ambiguos_sellados(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    cliente_holder: list,
) -> tuple[int, int]:
    """Los intentos SELLADOS con lectura ambigua sin desenlace (TEMPORADA 01
    B, revision): GET fresco JAMAS reenvia el PUT. Puja == destino ->
    confirmar (resumen + probe 'ok:reconciliado' + cache); puja == old_value
    -> no aplico ('perdida' + efecto 'sin_aplicar', la hoja queda libre); un
    tercer valor -> divergencia (efecto + exclusion si difiere del
    old_value); GET ambiguo de nuevo -> queda abierto y el siguiente ciclo lo
    reintenta. El cliente compartido vive en `cliente_holder` (lazy tras el
    gate de ancestros)."""
    filas = conn.execute(_SQL_INTENTOS_BID_AMBIGUOS_SIN_DESENLACE, (platform,)).fetchall()
    confirmadas = fallidas = 0
    for _id_attempt, decision_id, ad_entity_id, old_value, new_value in filas:
        if gate_ancestros(conn, ad_entity_id) is not None:
            # TEMPORADA 01 B (revision 4): resultado DESCONOCIDO (Amazon puede
            # haber aplicado el PUT): SIN desenlace terminal. Sin HTTP mientras
            # los ancestros esten fuera; la reconciliacion se retoma al
            # reactivarlos y resuelve el desenlace con GET fresco.
            fallidas += 1
            continue
        if cliente_holder[0] is None:
            cliente_holder[0] = aplicador._cliente()
        identidad = _identidad(conn, ad_entity_id)
        if identidad is None or identidad[0] not in _KINDS_DECISORAS or new_value is None:
            # Mismo criterio conservador: el PUT ya viajo y el resultado es
            # desconocido; sin desenlace terminal.
            fallidas += 1
            continue
        kind, external_id = identidad
        _, path, contenedor, param = _KINDS_DECISORAS[kind]
        try:
            # Amazon Ads v3 retiro el GET directo de entidad; readback via /list paginado.
            bid_leido = _bid_de_readback(cliente_holder[0], path, contenedor, param, external_id)
        except AdsApiError:
            # desconocido de nuevo: el efecto queda abierto y el siguiente
            # ciclo reintenta (jamás se reenvía el PUT).
            fallidas += 1
            continue
        ack = {"fuente": "readback", "bid": str(bid_leido)} if bid_leido is not None else None
        if bid_leido is None:
            # TEMPORADA 01 B (revision 4): sin puja legible el resultado NO es
            # concluyente (no demuestra ningun valor): el efecto queda ABIERTO
            # y el siguiente ciclo reintenta la lectura. Cerrarlo aqui podria
            # calcular otra entrada sobre el bid ya reducido.
            fallidas += 1
            continue
        if bid_leido == Decimal(_bid_payload(new_value)):
            # Amazon YA aplico el destino: confirmar sin reenviar el PUT.
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
        elif old_value is not None and bid_leido == old_value:
            # No aplico: Amazon conserva el bid ANTERIOR; la hoja queda libre
            # ('perdida' + efecto 'sin_aplicar') y el siguiente ciclo decide
            # de nuevo sobre el bid real.
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
            # Tercer valor: DIVERGENCIA DEFINITIVA — resumen verify_ok FALSE en
            # la misma transaccion que el probe y el cache: la decision deja
            # de calificar para futuras conciliaciones (una puja posterior al
            # destino jamas confirma esta decision) y el efecto cierra con
            # exclusion si el valor difiere del old_value.
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
    conn: psycopg.Connection, aplicador: Aplicador, platform: str
) -> tuple[int, int]:
    """Reconciliacion del ledger de BIDS sin sello (ADV-04; la llaman al
    inicio de la fase de apply de los ciclos live) de UNA plataforma: el
    lock del ciclo es por plataforma, asi que la fila sin sello de OTRA
    plataforma la conduce su propio ciclo (bug PR27-1). Por fila, GET FRESCO
    del readback con el MISMO scope sellado:

    - GET == pedido (quantizado) → confirmar: sello 'ok:reconciliado' +
      resumen con verify_ok + applied_cycle_id del EJECUTOR + cache con lo
      LEIDO (sellado 16);
    - divergencia → sello 'fallo:divergencia_readback' (resumen verify_ok
      FALSE, cache con lo leido). JAMAS REENVIA: el unico escritor de bids es
      aplica_bids (ARREGLOS 01 K, F-H1); la hoja se decide de nuevo con el bid
      real leido;
    - ambiguo (5xx agotado / sin bid legible) → failed SIN reintento
      (conserva su cobro, matriz §6.1).

    Devuelve (confirmadas, fallidas).

    TEMPORADA 01 B (revision): PRIMERO reconcilia los intentos SELLADOS con
    lectura ambigua sin desenlace (Amazon puede haber aplicado el cambio):
    GET fresco JAMAS reenvia el PUT. Puja == destino -> confirmar (resumen +
    probe 'ok:reconciliado' + efecto 'confirmado'); puja == old_value -> no
    aplico ('perdida' + efecto 'sin_aplicar', la hoja queda libre); un tercer
    valor -> divergencia (efecto + exclusion si difiere del old_value); GET
    ambiguo de nuevo -> queda abierto y el siguiente ciclo lo reintenta.

    CAMPANA ACTIVA 01 · 1.6: gate de ancestros PRIMERO, antes de cualquier
    HTTP (readback incluido) — campaña/ad group no ENABLED en el cache sella
    'fallo:ancestro_no_enabled'."""
    confirmadas = fallidas = 0
    # TEMPORADA 01 B (revision): PRIMERO los sellados-ambiguos sin desenlace
    # (eran anteriores a esta fase: su GET fresco los resuelve); despues los
    # sin sello, que pueden volverse ambiguos ESTE ciclo y quedan para el
    # siguiente.
    cliente_holder: list = [None]
    c_a, f_a = _reconcilia_ambiguos_sellados(conn, aplicador, platform, cliente_holder)
    confirmadas += c_a
    fallidas += f_a
    filas = conn.execute(_SQL_INTENTOS_BID_SIN_SELLO, (platform,)).fetchall()
    if not filas:
        return (confirmadas, fallidas)
    # Cliente lazy tras gate_ancestros: si faltan credenciales, las filas
    # cerradas por el gate igualmente quedan selladas antes de construirlo.
    cliente = None
    for id_attempt, decision_id, _payload, ad_entity_id, new_value, _moneda in filas:
        if gate_ancestros(conn, ad_entity_id) is not None:
            # TEMPORADA 01 B (revision): si la decision tiene un efecto de
            # temporada ABIERTO, el PUT pudo haberse aplicado antes de que la
            # campana/grupo quedara fuera: SIN desenlace terminal y SIN HTTP.
            # El intento queda pendiente (sin sello) y la reconciliacion lo
            # retoma al reactivar los ancestros, resolviendo el desenlace con
            # GET fresco. Sin efecto de temporada, el comportamiento historico
            # se mantiene.
            con_efecto_temporada = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM temporada_efecto te"
                "  WHERE te.decision_id = %s AND te.cerrado_el IS NULL)",
                (decision_id,),
            ).fetchone()[0]
            if con_efecto_temporada:
                fallidas += 1
                continue
            with tx_durable(conn):
                _sella_ledger(conn, id_attempt, ack=None, resultado=RESULTADO_ANCESTRO_NO_ENABLED)
                # TEMPORADA 01 B (revision): un sello SIN resumen dejaria el
                # efecto de temporada abierto y TX3 reventaria para siempre
                # contra el indice unico (una fila abierta por hoja). El
                # desenlace terminal viaja con el sello.
                registra_sin_aplicar(
                    conn,
                    decision_id,
                    aplicador.cycle_id_ejecutor,
                    MOTIVO_ENTIDAD_NO_DECISORA,
                    detalle={"sello": RESULTADO_ANCESTRO_NO_ENABLED},
                )
            fallidas += 1
            continue
        if cliente is None:
            cliente = aplicador._cliente()
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
            # Amazon Ads v3 retiro el GET directo de entidad; readback via /list paginado.
            bid_leido = _bid_de_readback(cliente, path, contenedor, param, external_id)
        except AdsApiError:
            # Resultado DESCONOCIDO (Amazon puede haber aplicado el cambio):
            # el sello queda SIN desenlace y el efecto de temporada ABIERTO
            # (la hoja no re-decide); la reconciliacion de sellados-ambiguos
            # lo retoma el siguiente ciclo con GET fresco, jamas reenviando
            # el PUT.
            with tx_durable(conn):
                _sella_ledger(conn, id_attempt, ack=None, resultado="fallo:readback_ambiguo")
            fallidas += 1
            continue
        if bid_leido == Decimal(_bid_payload(new_value)):
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
        if bid_leido is None:
            # TEMPORADA 01 B (revision nativa): sin puja legible el resultado NO
            # es concluyente (no demuestra ningun valor). Si la decision tiene
            # un efecto de temporada ABIERTO, queda pendiente SIN sello ni
            # resumen: el siguiente ciclo reintenta la lectura (jamas otra
            # entrada sobre el bid ya reducido) y el gate bid_en_vuelo sigue
            # bloqueando nuevas pujas para la hoja.
            con_efecto_temporada = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM temporada_efecto te"
                "  WHERE te.decision_id = %s AND te.cerrado_el IS NULL)",
                (decision_id,),
            ).fetchone()[0]
            if con_efecto_temporada:
                fallidas += 1
                continue
        with tx_durable(conn):
            _sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
            if bid_leido is not None:
                _actualiza_cache(conn, ad_entity_id, bid_leido)  # sellado 16: lo LEIDO
            _confirma_resumen(
                conn, decision_id, ack or {}, False, aplicador.cycle_id_ejecutor, leido=bid_leido
            )
        fallidas += 1
    return (confirmadas, fallidas)


def _ledger(
    conn: psycopg.Connection,
    decision_id: int,
    tipo: str,
    payload: dict,
    *,
    quota_cobrada: bool,
) -> int | None:
    """Nace la fila del ledger PRE-HTTP: seq = max(seq)+1 GLOBAL por
    decision_id (F2, A.3: normal + hermana + reversa comparten la secuencia,
    monotona sin huecos repetidos). El TOPE 3 (sellado 10) cuenta SOLO
    intentos 'normal' (CX1/GK1: reversas, probes y hermanas no consumen
    presupuesto de intentos): tres normales no bloquean hermanas ni
    reversas, y las hermanas no amplian el presupuesto normal (su tope es
    por (decision, adGroupId), ver _intentos_hermana). Tope normal
    alcanzado -> None y no existe 4o intento normal."""
    if tipo == "normal":
        count = conn.execute(_SQL_COUNT_INTENTOS, (decision_id,)).fetchone()[0]
        if count >= TOPE_INTENTOS:
            return None
    seq = conn.execute(_SQL_MAX_SEQ, (decision_id,)).fetchone()[0] + 1
    return conn.execute(
        _SQL_INSERT_LEDGER,
        (decision_id, seq, tipo, Json(payload), quota_cobrada),
    ).fetchone()[0]


# F2 (A.3): tope de TRES intentos POST por (decision_id, adGroupId) para las
# hermanas — independiente del tope normal y de TOPE_CICLOS_HERMANAS (el
# ciclo admite como maximo un POST por identidad, asi que el cap solo muerde
# ante reintentos fuera de ciclo o filas huerfanas). Cuenta TODAS las filas
# 'hermana' (abiertas y selladas): cada intento POST nace su fila.
_SQL_HERMANA_POR_ADGROUP = """
SELECT count(*) FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'hermana' AND request_payload->>'adGroupId' = %s
"""


def _intentos_hermana(conn: psycopg.Connection, decision_id: int, ad_group_id: str) -> int:
    """Intentos POST de hermana acumulados para (decision, adGroupId)."""
    return conn.execute(_SQL_HERMANA_POR_ADGROUP, (decision_id, str(ad_group_id))).fetchone()[0]


# F2 (A.3): detalle de intentos por hermana para la procedencia por ID
# (r2): el cruce consume `_intentos_hermana_detalle`; el conteo simple
# vive en `_intentos_hermana` (tope por identidad).
_SQL_INTENTOS_HERMANA_DETALLE = """
SELECT id, ack, finished_at IS NULL AS abierta FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'hermana' AND request_payload->>'adGroupId' = %s
 ORDER BY id
"""


def _intentos_hermana_detalle(
    conn: psycopg.Connection, decision_id: int, ad_group_id: str
) -> list[tuple[int, dict | None, bool]]:
    """Filas de hermana de (decision, adGroupId) con su ack y si sigue
    abierta: la base de la procedencia por ID (F2, A.3 r2). Solo un ack
    duradero que aparece en el readback prueba propiedad; un intento
    abierto sin ack (crash) no prueba nada por si solo."""
    return [
        (f[0], f[1], bool(f[2]))
        for f in conn.execute(
            _SQL_INTENTOS_HERMANA_DETALLE, (decision_id, str(ad_group_id))
        ).fetchall()
    ]


def _sella_ledger(
    conn: psycopg.Connection, id_attempt: int, ack: dict | None, resultado: str
) -> None:
    """Sella UNA vez (el trigger de 0002 lo hace cumplir): ack=respuesta del
    HTTP (o None en el rechazo >=400), resultado, finished_at."""
    conn.execute(_SQL_SELLA_LEDGER, (Json(ack) if ack is not None else None, resultado, id_attempt))


_SQL_GUARDA_ACK = """
UPDATE apply_attempt SET ack = %s WHERE id = %s AND finished_at IS NULL
"""

_SQL_SELLA_RESULTADO = """
UPDATE apply_attempt SET resultado = %s, finished_at = now() WHERE id = %s
"""


def _guarda_ack(conn: psycopg.Connection, id_attempt: int, ack: dict) -> None:
    """Guarda el ack SIN sellar el resultado (F2, A.3 r3): la id del ack
    queda durable ANTES del readback posterior, con la fila abierta. El
    trigger lo admite (cada columna pasa NULL -> valor una vez, de a una):
    el sello final via `_sella_resultado`. Si la fila ya se sello, no toca
    nada (el caller decide con el conteo)."""
    conn.execute(_SQL_GUARDA_ACK, (Json(ack), id_attempt))


def _sella_resultado(conn: psycopg.Connection, id_attempt: int, resultado: str) -> None:
    """Sella resultado + finished_at de una fila cuyo ack ya puede estar
    guardado (F2, A.3 r3): no reescribe el ack (reescribirlo violaria el
    sello unico). Para el sello completo en un paso sigue `_sella_ledger`."""
    conn.execute(_SQL_SELLA_RESULTADO, (resultado, id_attempt))


def _identidad(conn: psycopg.Connection, ad_entity_id: int) -> tuple[str, str] | None:
    """(kind, external_id) de la entidad — el id EXTERNO es lo que viaja al
    payload, jamas la PK interna."""
    fila = conn.execute(_SQL_IDENTIDAD, (ad_entity_id,)).fetchone()
    return (fila[0], fila[1]) if fila is not None else None


def _payload_bid(kind_entidad: str, external_id: str, bid: Decimal) -> dict:
    """Payload EXACTO del ledger: MISMO shape y MISMA serializacion que el
    write client (_bid_payload de app.ads.write — una sola fuente del
    quantize a 2 decimales, regla 2)."""
    campo = "keywordId" if kind_entidad == "keyword" else "targetId"
    return {campo: external_id, "bid": _bid_payload(bid)}


def _json_seguro(resp: httpx.Response) -> dict:
    """El JSON del ack SANEADO (GK9 de la cross-review: el body pasa SIEMPRE
    por scrub — un 2xx tambien puede ecoar secretos; antes solo el camino
    >=400 redactaba); si el body no parsea, evidencia minima (el ack crudo
    JAMAS debe tumbar el sello del ledger)."""
    try:
        data = resp.json()
    except ValueError:
        return {"status": resp.status_code, "body": scrub(resp.text[:200])}
    if not isinstance(data, dict):
        return {"status": resp.status_code, "body": scrub(str(data)[:200])}
    return json.loads(scrub(json.dumps(data)))


def _fila_de_filas(filas, id_campo: str, external_id: str) -> dict:
    """El cruce de id PURO: la primera fila de `filas` cuyo id CRUZA con el
    pedido (CX6/GK8 — nunca filas[0]). Vive separado para que el lector de
    UNA pagina y el paginado compartan el MISMO cruce (una sola fuente)."""
    for fila in filas or []:
        if isinstance(fila, dict) and str(fila.get(id_campo)) == str(external_id):
            return fila
    return {}


def _fila_de_lista(resp: httpx.Response, contenedor: str, id_campo: str, external_id: str) -> dict:
    """La fila de ESTA entidad en UNA pagina de la respuesta del LIST (cruce
    de id CX6/GK8): el list trae TODAS las filas de la cuenta, asi que se
    ESCANEA por el id — nunca filas[0]. None/{} = la entidad pedida no esta
    en la respuesta (una respuesta de OTRA entidad no es evidencia de esta
    decision ni toca el cache). Un 2xx MALFORMADO (no-JSON o no-dict) NO es
    ausencia: SUBE AdsApiError (ambiguo, mismo canal del 5xx — P1 Greptile
    PR #35: como ausencia, una entidad VIVA se clasificaba entidad_no_viva)."""
    try:
        cuerpo = resp.json()
    except ValueError:
        raise AdsApiError("readback malformado: 2xx sin JSON en el LIST") from None
    if not isinstance(cuerpo, dict):
        raise AdsApiError("readback malformado: el JSON del LIST no es un objeto")
    filas = cuerpo.get(contenedor)
    if not isinstance(filas, list):
        raise AdsApiError(f"readback malformado: el LIST no trae '{contenedor}' como lista")
    return _fila_de_filas(filas, id_campo, external_id)


def _fila_de_readback(
    cliente: AdsWriteClient, path: str, contenedor: str, id_campo: str, external_id: str
) -> dict:
    """La fila de ESTA entidad recorriendo TODAS las paginas del LIST de
    readback (CX1/QW1 de la cross-review del dueno): el body {} trae SOLO la
    primera pagina y la cuenta real tiene miles de keywords/targets — una
    entidad en pagina 2+ seria "ausente" (falsa divergencia con quota
    cobrada). Corta EN CUANTO la halla; sigue el nextToken con tope
    (TOPE_PAGINAS_READBACK: una lista que nunca termina no cuelga el ciclo).
    {} = no esta en ninguna pagina visitada. AdsApiError (5xx/ambiguo) SUBE:
    quien decide el sello lo captura como antes. Un 2xx MALFORMADO (no-JSON,
    no-dict, o sin el contenedor como lista — el wire sellado por el probe 2.5
    SIEMPRE lo trae) TAMBIEN sube AdsApiError (P1 Greptile PR #35): no es
    ausencia — como ausencia sellaba fallos de readback definitivos y
    clasificaba entidades VIVAS como entidad_no_viva."""
    token: str | None = None
    for _ in range(TOPE_PAGINAS_READBACK):
        body = {"nextToken": token} if token else {}
        try:
            data = cliente.list_sellado(path, body).json()
        except ValueError:
            raise AdsApiError(f"readback malformado: 2xx sin JSON en POST {path}") from None
        if not isinstance(data, dict):
            raise AdsApiError(f"readback malformado: el JSON de POST {path} no es un objeto")
        filas = data.get(contenedor)
        if not isinstance(filas, list):
            raise AdsApiError(f"readback malformado: POST {path} no trae '{contenedor}' como lista")
        fila = _fila_de_filas(filas, id_campo, external_id)
        if fila:
            return fila
        token = data.get("nextToken")
        if not token:
            return {}
    return {}


def _bid_de_fila(fila: dict) -> Decimal | None:
    """El bid de la fila del LIST ya cruzada. Shape SELLADO por el probe 2.5
    (apply_attempt 4-7 y 19-20: campo 'bid' NUMERO en el wire). None = sin
    bid legible."""
    bid = fila.get("bid")
    if bid is None:
        return None
    try:
        return Decimal(str(bid))
    except ArithmeticError:
        return None


def _estado_de_fila(fila: dict) -> str | None:
    """El estado de la fila del LIST ya cruzada, vocabulario UPPER del wire
    (ESTADO_WIRE_*). None = ilegible."""
    estado = fila.get("state")
    return estado if isinstance(estado, str) else None


def _bid_de_readback(
    cliente: AdsWriteClient, path: str, contenedor: str, id_campo: str, external_id: str
) -> Decimal | None:
    """El bid del READBACK paginado por LIST (espejo paginado de _bid_leido;
    misma extraccion _bid_de_fila, una sola fuente). None = sin bid legible
    de ESTA entidad en NINGUNA pagina."""
    return _bid_de_fila(_fila_de_readback(cliente, path, contenedor, id_campo, external_id))


def _estado_de_readback(
    cliente: AdsWriteClient, path: str, contenedor: str, id_campo: str, external_id: str
) -> str | None:
    """El estado del READBACK paginado por LIST (espejo paginado de
    _estado_leido para cortes y sus reconciliaciones). Vive AQUI (una sola
    fuente): apply_cola y apply_harvest lo reusan por import."""
    return _estado_de_fila(_fila_de_readback(cliente, path, contenedor, id_campo, external_id))


def _bid_leido(
    resp: httpx.Response, contenedor: str, id_campo: str, external_id: str
) -> Decimal | None:
    """El bid del READBACK por LIST de UNA pagina, SOLO de la fila cuyo id
    CRUZA con el pedido. Shape SELLADO por el probe 2.5 (2026-08-26,
    apply_attempt 4-7 y 19-20: contenedor 'keywords'/'targetingClauses',
    campo 'bid' NUMERO en el wire). None = sin bid legible de ESTA entidad."""
    return _bid_de_fila(_fila_de_lista(resp, contenedor, id_campo, external_id))


def _estado_leido(
    resp: httpx.Response, contenedor: str, id_campo: str, external_id: str
) -> str | None:
    """El estado del READBACK por LIST de UNA pagina, SOLO de la fila cuyo id
    CRUZA con el pedido (espejo de _bid_leido para cortes). El vocabulario
    del wire es UPPER — ESTADO_WIRE_* (probe 2.5, apply_attempt 19-20);
    'userPaused' NO existe en la respuesta. None = ilegible/vacio/de otra
    entidad. Vive AQUI (una sola fuente): apply_cola y apply_harvest lo
    reusan por import."""
    return _estado_de_fila(_fila_de_lista(resp, contenedor, id_campo, external_id))


def _ya_aplicada(conn: psycopg.Connection, decision_id: int) -> bool:
    return conn.execute(_SQL_YA_APLICADA, (decision_id,)).fetchone()[0]


def _confirma_resumen(
    conn: psycopg.Connection,
    decision_id: int,
    ack: dict,
    verify_ok: bool,
    cycle_id_ejecutor: int,
    *,
    leido: Decimal | None = None,
) -> None:
    """UPSERT del resumen: la terna confirmed_at+platform_ack+verify_ok JUNTA
    (CHECKs de 0001); reintentos = UPDATE del resumen + fila nueva del ledger
    (sellado 10). applied_cycle_id y applied_count SOLO con verify_ok: un
    crash o una divergencia NO cuentan como applied (sellado 21).

    TEMPORADA 01 B: en la MISMA transaccion cierra el efecto de temporada con
    'confirmado' o 'divergencia' (y excluye la hoja solo si el bid leido
    difiere tambien del old_value: si Amazon tiene el bid de antes, el cambio
    no llego y manana la hoja se deriva otra vez). `leido` es el bid del
    readback; None conserva la semantica de los llamadores viejos."""
    conn.execute(_SQL_CONFIRMAR_RESUMEN, (decision_id, Json(ack), verify_ok))
    if verify_ok:
        conn.execute(_SQL_SELLA_CICLO_EJECUTOR, (cycle_id_ejecutor, decision_id))
        conn.execute(_SQL_APPLIED_COUNT, (cycle_id_ejecutor,))
    temporada_io.cierra_efecto_resumen(
        conn, decision_id=decision_id, verify_ok=verify_ok, leido=leido
    )


def _actualiza_cache(conn: psycopg.Connection, ad_entity_id: int, bid_leido: Decimal) -> None:
    """El cache queda con LO LEIDO del readback (sellado 16: la fuente es
    Amazon y el readback ES de Amazon; con lo enviado, el ciclo siguiente
    calcularia sobre el bid viejo)."""
    conn.execute(_SQL_ACTUALIZA_CACHE, (bid_leido, ad_entity_id))


def _tick(tick: Callable[[], None] | None) -> None:
    if tick is not None:
        tick()


_SQL_ENTIDAD_VIVA = """
SELECT ag.parent_id AS campaign_id, (s.ad_entity_id IS NOT NULL) AS tiene_state
  FROM ad_entity k
  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group'
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = k.id
 WHERE k.id = %s
"""

_SQL_GOALS_ENTIDAD = """
SELECT scope, ad_entity_id, platform, target_acos_pct, bid_floor, bid_ceiling,
       bid_currency, harvest_campaign_id, harvest_ad_group_id,
       harvest_default_bid, enabled, mode
  FROM ads_optimizer_goal
 WHERE (scope = 'platform' AND platform = %s)
    OR (scope = 'campaign' AND ad_entity_id = %s)
"""

# (kind de la entidad) -> (metodo del write client, path del LIST de
# readback, contenedor de la respuesta, campo del id para el cruce). Shape
# SELLADO por el probe 2.5 (2026-08-26, apply_attempt 4-5 y 18-20): el GET
# directo esta retirado (403) — el contenedor de targets es
# 'targetingClauses', el MISMO del list.
_KINDS_DECISORAS = {
    "keyword": ("bid_keyword", "/sp/keywords/list", "keywords", "keywordId"),
    "product_target": ("bid_target", "/sp/targets/list", "targetingClauses", "targetId"),
}


class _TransportGuardado(httpx.BaseTransport):
    """Transport decorado con el guard pre-HTTP (gancho 2.4, decision 11): el
    callback corre ANTES de despachar CADA request. Asi el ownership-check del
    ciclo cubre mutacion, readback, listas y hasta el token LWA sin tocar el
    write client (candado de imports sellado). El guard que lanza aborta la
    fase de apply sin que el request salga — el transport espia de los tests
    jamas lo ve."""

    def __init__(self, base: httpx.BaseTransport, guard: Callable[[], None]) -> None:
        self._base = base
        self._guard = guard

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._guard()
        return self._base.handle_request(request)


class Aplicador:
    """Aplica decisiones de bid de UN ciclo ejecutor sobre UNA plataforma.

    El write client NO se construye en __init__: se construye LAZY en el
    primer apply que re-resuelva 'live' — AdsWriteClient revienta si el modo
    no es exactamente 'live' (fail-closed), asi que la re-resolucion POR
    DECISION es la que decide si existe cliente. En shadow: cero HTTP (ni
    token LWA). `transport`/`sleep` son la inyeccion de tests (MockTransport),
    misma puerta que AdsClient.

    2.4: `credentials`/`profile_id` son OPCIONALES — un Aplicador sin ellos
    sirve para re-resolver modo_efectivo (escalera shadow o fabrica que aborto)
    y JAMAS construye cliente (fail-closed en _cliente, regla 3: no existe
    profile inventado). `guard_http` corre antes de CADA HTTP del write client
    (ownership-check + heartbeat de la decision 11; ver docstring del modulo).
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        platform: str,
        cycle_id_ejecutor: int,
        owner: str,
        job_key: str,
        tick: Callable[[], None] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        credentials: AdsCredentials | None = None,
        profile_id: str | int | None = None,
        guard_http: Callable[[], None] | None = None,
    ) -> None:
        if platform not in PLATAFORMA_MONEDA:
            raise ValueError(
                f"platform invalida: {platform!r} "
                f"(vocabulario cerrado: {sorted(PLATAFORMA_MONEDA)})"
            )
        self._conn = conn
        self._platform = platform
        self._profile_id = profile_id
        self._credentials = credentials
        self.cycle_id_ejecutor = cycle_id_ejecutor
        # Para el ownership-check pre-HTTP de la integracion (2.4, sellado 11):
        # lease perdido = abortar el apply fail-closed.
        self.owner = owner
        self.job_key = job_key
        self._tick_fn = tick
        self._transport = transport
        self._sleep = sleep
        self._guard_http = guard_http
        self._write_client: AdsWriteClient | None = None

    # -- modo efectivo por decision (JAMAS inputs.modo ni cycle.mode) ------

    def modo_efectivo(
        self, conn: psycopg.Connection, decision: DecisionBid, *, escalera_global: str
    ) -> str:
        """Re-resuelve el modo PARA ESTA decision, contra la base FRESCA:

        escalera global (la del envelope, pasada por parametro) meet goal de
        la entidad (precedencia campana > plataforma con goals.resuelve_goal;
        goal.mode + enabled) y existencia de la entidad/state. 'live' solo si
        TODO aplica; cualquier otra cosa -> 'shadow' (no aplica, cero HTTP).
        El residual de cycle.py: la decision nacio con inputs.modo/cycle.mode
        de OTRO ciclo — aqui no se miran JAMAS."""
        fila = conn.execute(_SQL_ENTIDAD_VIVA, (decision.ad_entity_id,)).fetchone()
        if fila is None or not fila[1]:
            return "shadow"  # entidad inexistente o sin state (regla 3)
        campaign_id = fila[0]
        goal_campana: g.Goal | None = None
        goal_plataforma: g.Goal | None = None
        for f in conn.execute(_SQL_GOALS_ENTIDAD, (self._platform, campaign_id)).fetchall():
            goal = g.Goal(
                scope=f[0],
                ad_entity_id=f[1],
                platform=f[2],
                target_acos_pct=f[3],
                bid_floor=f[4],
                bid_ceiling=f[5],
                bid_currency=f[6],
                harvest_campaign_id=f[7],
                harvest_ad_group_id=f[8],
                harvest_default_bid=f[9],
                enabled=f[10],
                mode=f[11],
            )
            if goal.scope == "campaign":
                goal_campana = goal
            else:
                goal_plataforma = goal
        resuelto = g.resuelve_goal(goal_campana, goal_plataforma)
        if resuelto is None or not resuelto.enabled:
            return "shadow"
        # Meet del reticulo off < shadow < live (goals.modo_efectivo, puro).
        return g.modo_efectivo(escalera_global, resuelto.mode)

    # -- write client lazy ---------------------------------------------------

    def _cliente(self) -> AdsWriteClient:
        if self._credentials is None or self._profile_id is None:
            # Aplicador sin credenciales/profile (solo modo_efectivo): un
            # camino live NO puede construir cliente — fail-closed ruidoso,
            # jamas un profile inventado (regla 3).
            raise ValueError(
                "Aplicador sin credentials/profile_id: el modo live exige la "
                "fabrica del ciclo (2.4), no un cliente a ciegas"
            )
        if self._write_client is None:
            transport: httpx.BaseTransport | None = self._transport
            if self._guard_http is not None:
                # Gancho 2.4 (decision 11): TODOS los HTTP del write client
                # pasan por el transport — el guard corre ANTES de cada
                # request (mutacion, readback, listas y token LWA incluidos).
                base = self._transport if self._transport is not None else httpx.HTTPTransport()
                transport = _TransportGuardado(base, self._guard_http)
            # Solo inyectar sleep cuando lo provee el caller (tests) para
            # conservar el backoff real de AdsWriteClient en produccion.
            extra: dict = {"sleep": self._sleep} if self._sleep is not None else {}
            self._write_client = AdsWriteClient(
                self._credentials,
                platform=self._platform,
                profile_id=self._profile_id,
                modo_confirmado=MODO_CONFIRMADO_LIVE,
                transport=transport,
                **extra,
            )
        return self._write_client

    # -- apply de bids ---------------------------------------------------------

    def aplica_bids(
        self, decisiones: list[DecisionBid], *, escalera_global: str
    ) -> ResultadoAplicador:
        """Aplica el lote de bids del ciclo en el ORDEN sellado bajo cap.

        Por decision: re-resolucion -> tope de intentos -> LECTURA FRESCA del
        bid (TEMPORADA 01 E: sin respuesta o con un bid distinto del visto al
        decidir no hay cuota, ni ledger, ni PUT) -> quota (si no cabe:
        descartado, JAMAS HTTP) -> ledger PRE-HTTP + COMMIT -> HTTP -> readback
        sellado -> sello de ledger + resumen + cache + applied_count (ver
        docstring del modulo). >=400 se captura (ledger sellado con el
        cuerpo); 5xx/fallo ambiguo SUBE (ledger sin sello: la fila ES el
        rastro)."""
        ordenadas = orden_bajo_cupo(decisiones)
        skips: list[str] = []
        descartadas: list[str] = []
        aplicadas = 0
        divergencias = 0
        caps_saturados: list[CapSaturado] = []
        for indice, decision in enumerate(ordenadas):
            modo = self.modo_efectivo(self._conn, decision, escalera_global=escalera_global)
            if modo != "live":
                skips.append(MOTIVO_MODO_NO_LIVE)
                # Una decision ya aplicada no se registra como no-apply si un re-run
                # la ve fuera de live.
                if not _ya_aplicada(self._conn, decision.id):
                    registra_sin_aplicar(
                        self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_MODO_NO_LIVE
                    )
                continue
            if _ya_aplicada(self._conn, decision.id):
                # Excepcion declarada (D.1): ya_aplicada NO se registra — su
                # desenlace YA es decision_application.
                skips.append(MOTIVO_YA_APLICADA)
                continue
            if decision.new_value is None or decision.value_currency is None:
                skips.append(MOTIVO_BID_INCOMPLETO)
                registra_sin_aplicar(
                    self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_BID_INCOMPLETO
                )
                continue
            identidad = _identidad(self._conn, decision.ad_entity_id)
            if identidad is None or identidad[0] not in _KINDS_DECISORAS:
                skips.append(MOTIVO_ENTIDAD_NO_DECISORA)
                registra_sin_aplicar(
                    self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_ENTIDAD_NO_DECISORA
                )
                continue
            # Verificar tope antes de cobrar quota para no quemar unidades en
            # decisiones agotadas (_ledger vuelve a verificar ante carreras).
            if self._conn.execute(_SQL_COUNT_INTENTOS, (decision.id,)).fetchone()[0] >= (
                TOPE_INTENTOS
            ):
                skips.append(MOTIVO_TOPE_INTENTOS)
                registra_sin_aplicar(
                    self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_TOPE_INTENTOS
                )
                continue
            # TEMPORADA 01 E: lectura fresca ANTES de la cuota y del PUT, para
            # TODA decision de bid. Amazon no ofrece compare-and-set: entre
            # este LIST y el PUT queda una ventana corta, pero el bid que se
            # pisa ya no es el que Orbit vio al decidir.
            cliente = self._cliente()
            try:
                leido = self._readback(cliente, identidad)
            except AdsApiError as exc:
                if exc.status == 429:
                    # Amazon esta limitando la ruta de lectura: el cliente ya
                    # agoto sus reintentos para ESTA puja. Repetir el LIST por
                    # cada puja multiplica la espera del ciclo y retrasa la
                    # liberacion de cortes (otra ruta). Esta puja queda sin
                    # respuesta y el lote de bids se corta aqui. Cero cuota,
                    # cero PUT.
                    skips.append(MOTIVO_SIN_RESPUESTA)
                    registra_sin_aplicar(
                        self._conn,
                        decision.id,
                        self.cycle_id_ejecutor,
                        MOTIVO_SIN_RESPUESTA,
                        detalle={"http": "429"},
                    )
                    # Las que faltan tampoco se consultan, pero SI dejan su
                    # desenlace: sin fila, la vista de auditoria las leeria
                    # como huerfanas de un ciclo live cerrado.
                    skips.extend(
                        self._registra_lote_cortado(
                            ordenadas[indice + 1 :], escalera_global=escalera_global
                        )
                    )
                    break
                # Una lectura fallida NO cancela el lote: antes del PUT no hay
                # escritura ambigua que reconciliar, asi que esta puja queda
                # sin respuesta y las demas siguen (mismo criterio que la
                # revalidacion de cortes). ApplyAbortado y los fallos del PUT
                # siguen propagando.
                leido = None
            if leido is None:
                skips.append(MOTIVO_SIN_RESPUESTA)
                registra_sin_aplicar(
                    self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_SIN_RESPUESTA
                )
                continue
            # old_value None: la fila vieja no trae contra que comparar (regla
            # 3); sin comparacion, sigue el camino de hoy.
            if (
                decision.old_value is not None
                and abs(leido - decision.old_value) > temporada_io.TOLERANCIA_CADENA
            ):
                # Otra mano (el dueno, otro proceso) movio la hoja: CERO PUT del
                # destino viejo. El desenlace es mano_del_dueno; en temporada la
                # hoja queda excluida del episodio. El cache lleva LO LEIDO para
                # que el ciclo siguiente decida desde el valor real.
                # tx_durable: con la conexion SIN autocommit de produccion la
                # carga de pujas ya dejo la sesion en INTRANS; un
                # conn.transaction() a secas seria un savepoint y el rollback
                # de la fase (cycle.py) borraria la mano del dueno.
                with tx_durable(self._conn):
                    registra_sin_aplicar(
                        self._conn,
                        decision.id,
                        self.cycle_id_ejecutor,
                        MOTIVO_BID_CAMBIADO_FUERA,
                        detalle={"leido": str(leido)},
                    )
                    temporada_io.excluye_mano_del_aplicador(
                        self._conn, decision_id=decision.id, leido=leido
                    )
                    _actualiza_cache(self._conn, decision.ad_entity_id, leido)
                skips.append(MOTIVO_BID_CAMBIADO_FUERA)
                continue
            usada, saturada = consume_quota_y_sello(self._conn, self._platform, "bid")
            if not usada:
                # Bids fuera de cap = DESCARTADOS, jamas reintentados (8).
                descartadas.append(MOTIVO_FUERA_DE_CAP)
                registra_sin_aplicar(
                    self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_FUERA_DE_CAP
                )
                continue
            if saturada:
                # Preflight 1.4: la unidad que llevo used a cap dispara UN
                # evento (el rechazo del siguiente bid no pasa por aqui).
                evento = evento_cap_saturado(self._conn, self._platform, "bid")
                if evento is not None:
                    caps_saturados.append(evento)
            payload = _payload_bid(identidad[0], identidad[1], decision.new_value)
            id_attempt = _ledger(self._conn, decision.id, "normal", payload, quota_cobrada=True)
            if id_attempt is None:
                skips.append(MOTIVO_TOPE_INTENTOS)
                registra_sin_aplicar(
                    self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_TOPE_INTENTOS
                )
                continue
            resultado_bid = self._ejecuta_mutacion(decision, identidad, id_attempt)
            if resultado_bid is None:
                skips.append(MOTIVO_FALLO_HTTP)
                registra_sin_aplicar(
                    self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_FALLO_HTTP
                )
            elif resultado_bid:
                aplicadas += 1
            else:
                divergencias += 1
        return ResultadoAplicador(
            orden=[d.id for d in ordenadas],
            aplicadas=aplicadas,
            descartadas=descartadas,
            skips=skips,
            divergencias=divergencias,
            caps_saturados=tuple(caps_saturados),
        )

    def _registra_lote_cortado(
        self, restantes: list[DecisionBid], *, escalera_global: str
    ) -> list[str]:
        """El 429 agoto la lectura fresca y corta el lote: cada puja restante
        deja su desenlace SIN HTTP (modo y aplicacion previa son lecturas
        locales). Una puja de campana en practica conserva `modo_no_live`, como
        en el camino normal; las elegibles quedan `sin_respuesta` con
        `detalle.lote_cortado` porque no llegaron a consultarse."""
        motivos: list[str] = []
        for restante in restantes:
            if _ya_aplicada(self._conn, restante.id):
                motivos.append(MOTIVO_YA_APLICADA)
                continue
            if self.modo_efectivo(self._conn, restante, escalera_global=escalera_global) != "live":
                motivos.append(MOTIVO_MODO_NO_LIVE)
                registra_sin_aplicar(
                    self._conn, restante.id, self.cycle_id_ejecutor, MOTIVO_MODO_NO_LIVE
                )
                continue
            motivos.append(MOTIVO_SIN_RESPUESTA)
            registra_sin_aplicar(
                self._conn,
                restante.id,
                self.cycle_id_ejecutor,
                MOTIVO_SIN_RESPUESTA,
                detalle={"lote_cortado": "429"},
            )
        return motivos

    def _ejecuta_mutacion(
        self, decision: DecisionBid, identidad: tuple[str, str], id_attempt: int
    ) -> bool | None:
        """La secuencia sellada de UNA mutacion cuyo ledger ya nacio (quota
        cobrada, fila PRE-HTTP). True = confirmada por readback; False =
        sellada con divergencia o sin bid legible; None = rechazo >=400
        capturado (ledger sellado con el cuerpo saneado)."""
        self._conn.commit()  # intencion durable PRE-HTTP
        cliente = self._cliente()
        try:
            _tick(self._tick_fn)
            resp_http = self._muta_bid(
                cliente, identidad, decision.new_value, decision.value_currency
            )
        except AdsApiErrorMutacion as exc:
            _sella_ledger(
                self._conn,
                id_attempt,
                ack=None,
                resultado=f"fallo http {exc.status}: {exc.cuerpo}",
            )
            self._conn.commit()
            return None
        # 5xx/fallo ambiguo (AdsApiError): SUBE — el ledger queda SIN sello,
        # la fila ES el rastro del crash; NO se reintenta (sellado 8).
        ack = _json_seguro(resp_http)
        _tick(self._tick_fn)
        bid_leido = self._readback(cliente, identidad)
        verify_ok = bid_leido is not None and bid_leido == Decimal(_bid_payload(decision.new_value))
        resultado = (
            "ok"
            if verify_ok
            else (
                "fallo:divergencia_readback" if bid_leido is not None else "fallo:readback_sin_bid"
            )
        )
        with tx_durable(self._conn):
            _sella_ledger(self._conn, id_attempt, ack=ack, resultado=resultado)
            _confirma_resumen(
                self._conn, decision.id, ack, verify_ok, self.cycle_id_ejecutor, leido=bid_leido
            )
            if bid_leido is not None:
                _actualiza_cache(self._conn, decision.ad_entity_id, bid_leido)
        return verify_ok

    def _muta_bid(
        self,
        cliente: AdsWriteClient,
        identidad: tuple[str, str],
        bid: Decimal,
        moneda: str,
    ) -> httpx.Response:
        kind, external_id = identidad
        if kind == "keyword":
            return cliente.actualizar_bid_keyword(external_id, bid, moneda)
        return cliente.actualizar_bid_target(external_id, bid, moneda)

    def _readback(self, cliente: AdsWriteClient, identidad: tuple[str, str]) -> Decimal | None:
        """LIST FRESCO PAGINADO con el MISMO scope sellado (list_sellado: el
        re-check JAMAS pasa un profile a mano — sellado 16; probe 2.5: el GET
        directo de entidad esta retirado, 403; CX1/QW1: el body {} solo trae
        la primera pagina de la cuenta)."""
        kind, external_id = identidad
        _, path, contenedor, param = _KINDS_DECISORAS[kind]
        return _bid_de_readback(cliente, path, contenedor, param, external_id)


def reversa_bid(
    conn: psycopg.Connection,
    cliente: AdsWriteClient,
    decision: DecisionBid,
    *,
    tick: Callable[[], None] | None = None,
) -> bool:
    """Reversa del bid: PUT con old_value, misma secuencia sellada (ledger
    tipo 'reversa', quota_cobrada=false EXENTA de quota), readback y sello.
    True = la reversa quedo confirmada por readback. No toca
    decision_application ni applied_count (el resumen de la decision original
    no se re-escribe; una reversa NO limpia el cooldown — sellado 12)."""
    if decision.old_value is None or decision.value_currency is None:
        raise ValueError(
            f"reversa de bid {decision.id}: exige old_value y value_currency (regla 7)"
        )
    identidad = _identidad(conn, decision.ad_entity_id)
    if identidad is None or identidad[0] not in _KINDS_DECISORAS:
        return False
    payload = _payload_bid(identidad[0], identidad[1], decision.old_value)
    id_attempt = _ledger(conn, decision.id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return False  # tope 3 del ledger: no existe 4o intento
    conn.commit()  # intencion durable PRE-HTTP
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
    except AdsApiErrorMutacion as exc:
        _sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        return False
    ack = _json_seguro(resp_http)
    _tick(tick)
    _, path, contenedor, param = _KINDS_DECISORAS[identidad[0]]
    bid_leido = _bid_de_readback(cliente, path, contenedor, param, identidad[1])
    resultado = (
        "ok"
        if bid_leido is not None and bid_leido == Decimal(_bid_payload(decision.old_value))
        else ("fallo:divergencia_readback" if bid_leido is not None else "fallo:readback_sin_bid")
    )
    with conn.transaction():
        _sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        if bid_leido is not None:
            _actualiza_cache(conn, decision.ad_entity_id, bid_leido)
    return bid_leido is not None and bid_leido == Decimal(_bid_payload(decision.old_value))


# La decision de bid POR ID (columnas de DecisionBid + ciclo para plataforma:
# UNA fuente — la plataforma es la del ciclo que decidio, jamas un parametro
# del caller).
_SQL_DECISION_POR_ID = """
SELECT d.id, d.cycle_id, d.ad_entity_id, d.old_value, d.new_value,
       d.value_currency, d.inputs, oc.platform::text
  FROM decision d
  JOIN optimizer_cycle oc ON oc.id = d.cycle_id
 WHERE d.id = %s
"""

# "Confirmada como aplicada" = decision_application.applied_cycle_id sellado
# AL CONFIRMAR (sellado 10/21): un crash o una divergencia NO cuentan.
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

# Exige maximo una reversa confirmada por decision: las reversas no consumen
# quota ni tope de intentos ni cambian el estado en apply_queue.
_SQL_REVERSA_OK = """
SELECT EXISTS (
    SELECT 1 FROM apply_attempt
     WHERE decision_id = %s AND tipo = 'reversa' AND resultado = 'ok'
)
"""


def _reversa_ya_hecha(conn: psycopg.Connection, decision_id: int) -> bool:
    """True si la decision ya tiene UNA reversa confirmada (resultado 'ok'):
    una reversa es UNA por decision (regla 7; ADV-3). Una reversa FALLIDA no
    bloquea: el reintento queda vivo."""
    return conn.execute(_SQL_REVERSA_OK, (decision_id,)).fetchone()[0]


def _cliente_reversa(platform: str, *, transport: httpx.BaseTransport | None) -> AdsWriteClient:
    """El write client de la reversa manual: credenciales del secrets dir +
    perfil por /v2/profiles (el MISMO camino del ciclo via perfil_aceptado_de,
    regla 2) y modo confirmado live — la reversa existe para DESHACER un apply
    ya confirmado; no re-resuelve escalera (quien llama decidio deshacer)."""
    credentials = AdsCredentials.from_secrets_dir()
    perfil = perfil_aceptado_de(credentials, platform, transport=transport)
    if perfil is None:
        raise SinPerfilReversa(
            f"sin perfil aceptado para {platform} en /v2/profiles: la reversa"
            " aborta fail-closed (regla 3: jamas un profile inventado)"
        )
    return AdsWriteClient(
        credentials,
        platform=platform,
        profile_id=perfil.profile_id,
        modo_confirmado=MODO_CONFIRMADO_LIVE,
        transport=transport,
    )


def _negative_id_del_ledger(conn: psycopg.Connection, decision_id: int) -> str:
    """Resuelve el negative_id del ack del ultimo intento normal ok del ledger
    (mismo parseo del camino de apply_cola al ejecutar el negative: el helper
    _id_de_ack de apply_harvest). No resoluble -> NegativeIdNoResoluble: el id
    jamas se inventa (regla 3)."""
    from app import apply_harvest  # import diferido: apply_harvest importa apply

    fila = conn.execute(_SQL_ACK_OK, (decision_id,)).fetchone()
    if fila is not None and isinstance(fila[0], dict):
        negative_id = apply_harvest._id_de_ack(fila[0], "negativeKeywordId")
        if negative_id is not None:
            return negative_id
    raise NegativeIdNoResoluble(
        f"no se resolvio el negative_id de la decision {decision_id} desde el"
        " ledger (sin intento normal resultado ok con ack legible)"
    )


def reversa_manual(
    conn: psycopg.Connection,
    *,
    tipo: str,
    decision_id: int | None = None,
    queue_id: int | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Reversa MANUAL (regla 7, sellado 12): un solo entry point para las tres
    formas (bid -> PUT old_value; pause -> resume; negative -> DELETE).

    Precondiciones ANTES de construir cliente o HTTP (fail-closed ruidoso):
    bid exige decision existente + confirmada aplicada
    (decision_application.applied_cycle_id sellado) + old_value/moneda;
    pause/negative exigen fila existente en estado 'applied'. Plataforma: la
    del ciclo de la decision (bid) o la de la fila (pause/negative) — una
    sola fuente, jamas parametro del caller. Y desde ADV-2/ADV-3: el
    negative_id del DELETE sale SIEMPRE del ledger (el caller JAMAS lo pasa)
    y una decision con reversa ya confirmada no se revierte dos veces
    (ReversaYaHecha — sin ese candado, el loop del endpoint despachaba HTTP
    real ilimitado).

    Despacha a las funciones ya testeadas (reversa_bid / apply_cola.reversa_
    pause / apply_cola.reversa_negative: ledger tipo reversa EXENTO de quota,
    readback, cache con lo leido; una reversa NO limpia el cooldown — sellado
    12, este wrapper no toca nada de eso). `transport` es la puerta de tests
    (MockTransport); el endpoint NO lo pasa.

    Devuelve {tipo, identificadores, confirmada: bool} — confirmada false =
    la reversa quedo sellada como fallo en el ledger (el detalle vive ahi).
    """
    from app import apply_cola  # diferidos: importan apply (circular)

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
        confirmada = reversa_bid(conn, _cliente_reversa(fila[7], transport=transport), decision)
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
        cliente = _cliente_reversa(platform, transport=transport)
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


class SinPerfilAjuste(Exception):
    pass


def _cliente_ajuste(platform: str, *, transport) -> AdsWriteClient:
    """Write client del ajuste (mismo camino del ciclo)."""
    credentials = AdsCredentials.from_secrets_dir()
    perfil = perfil_aceptado_de(credentials, platform, transport=transport)
    if perfil is None:
        raise SinPerfilAjuste(
            f"sin perfil aceptado para {platform} en /v2/profiles: el ajuste"
            " aborta fail-closed (regla 3: jamas un profile inventado)"
        )
    return AdsWriteClient(
        credentials,
        platform=platform,
        profile_id=perfil.profile_id,
        modo_confirmado=MODO_CONFIRMADO_LIVE,
        transport=transport,
    )
