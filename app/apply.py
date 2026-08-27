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
- SECUENCIA SELLADA POR MUTACION: re-resolucion -> consume_quota (si no
  cabe: descartado, JAMAS HTTP) -> fila del ledger PRE-HTTP (seq =
  count(*)+1 del decision_id; tope 3: COUNT >= 3 -> no existe 4o intento,
  salta con motivo) + COMMIT (la intencion durable ANTES del HTTP) -> HTTP
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
'targetingClauses' y estados del wire UPPER (ESTADO_WIRE_*). Unica hipotesis
PENDIENTE: el state del REQUEST del PUT de pause/resume
(write.py ESTADO_PUT_*; re-exportada aqui — QW2, una sola fuente).

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
from dataclasses import dataclass
from decimal import Decimal

import httpx
import psycopg
from psycopg.types.json import Json

from app.ads.client import AdsApiError, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import PerfilAds, evaluar_perfiles

# Re-export QW2 (cross-review del dueno): el state del REQUEST del PUT de
# pause/resume vive SOLO en write.py (ESTADO_PUT_*); apply — el unico modulo
# que puede importar write — lo re-exporta para que apply_cola/apply_harvest
# usen LA constante. Cuando la corrida real del pause fije el enum, se toca
# UN lugar (write.py) y el payload del ledger lo sigue.
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


# Tope de paginaciones del LIST de readback (CX1/QW1 + CX6 de la
# cross-review del dueno): una cuenta real trae >1000 keywords y el body {}
# solo trae la PRIMERA pagina, asi que el readback recorre TODAS por
# nextToken (patron sellado de structure.py MAX_PAGINAS / smoke TOPE_PAGINAS
# / apply_harvest TOPE_PAGINAS_LIST) con tope: una lista que nunca termina no
# cuelga el ciclo. RESIDUAL DECLARADO (reviewer): el page size real del list
# v3 es 1000 (log del probe: 1334 keywords = 2 paginas), asi que 20 paginas
# cubren ~20k filas (~15x la cuenta actual); si la cuenta algun dia supera
# el tope, una entidad mas alla se manifiesta como "ausente" (misma falsa
# divergencia que este fix elimina) — subir el tope ese dia.
TOPE_PAGINAS_READBACK = 20

# Estados del WIRE en el readback por LIST. SELLADO por el probe 2.5
# (2026-08-26, ledger probe ids 1-20, log out/smoke-apply-20260826.log): el
# list trae state UPPER — ENABLED/PAUSED/ARCHIVED (apply_attempt 19-20:
# targets list con ENABLED y PAUSED vivos). 'userPaused' NO existe en la
# RESPUESTA: es vocabulario del REQUEST del PUT (hipotesis pendiente,
# write.py ESTADO_PUT_*). ARCHIVED = operativamente muerto (el "delete" v3
# archiva): NO confirma entidad viva ni pause verificado.
ESTADO_WIRE_ENABLED = "ENABLED"
ESTADO_WIRE_PAUSED = "PAUSED"
ESTADO_WIRE_ARCHIVED = "ARCHIVED"

# Vocabulario cerrado de motivos del aplicador (skips y descartes
# estructurados; el digest de 3.3 los consume tal cual).
MOTIVO_MODO_NO_LIVE = "modo_no_live"
MOTIVO_YA_APLICADA = "ya_aplicada"
MOTIVO_TOPE_INTENTOS = "tope_intentos"
MOTIVO_FUERA_DE_CAP = "fuera_de_cap"
MOTIVO_FALLO_HTTP = "fallo_http"
MOTIVO_BID_INCOMPLETO = "bid_incompleto"
MOTIVO_ENTIDAD_NO_DECISORA = "entidad_no_decisora"

# ---------------------------------------------------------------------------
# Quota: motor, cap desde config, consumo atomico
# ---------------------------------------------------------------------------


def motor_quota(platform: str, kind: str) -> str:
    """Clave del motor de quota: `ads_optimizer:<platform>:<kind>`.

    UNA fuente en la app de este vocabulario; su espejo de schema es el
    mapeo de `apply_cap_de_config` (0002), que resuelve la clave de config
    `ads_apply_cap_<platform>_<kind>` para ESTE motor. kind en
    {bid, pause, negative, harvest} — kinds nuevos de quota = decision nueva
    del dueno (sellado 8)."""
    return f"ads_optimizer:{platform}:{kind}"


_SQL_CONFIG_VIGENTE = """
SELECT settings FROM config_version ORDER BY id DESC LIMIT 1
"""


def _cap_de_config(conn: psycopg.Connection, platform: str, kind: str) -> int | None:
    """Cap de la config VIGENTE (ultima config_version por id — la misma
    resolucion que el trigger apply_cap_de_config de 0002). None = sin clave
    o sin config: fail-closed, no nace fila del dia."""
    fila = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    if fila is None:
        return None
    settings = fila[0] or {}
    valor = settings.get(f"ads_apply_cap_{platform}_{kind}")
    if valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        # Clave presente pero corrupta es config ROTA, no dato faltante
        # (regla 3): ruidoso, jamas disfraz de cap infinito.
        raise ValueError(
            f"ads_apply_cap_{platform}_{kind}: valor no numerico en la config vigente: {valor!r}"
        ) from None


def consume_quota(conn: psycopg.Connection, platform: str, kind: str) -> bool:
    """Consume UNA unidad de quota del dia, atomicamente.

    INSERT ... ON CONFLICT DO UPDATE en UNA sentencia (no SELECT-luego-UPSERT:
    dos sesiones concurrentes no pueden pasarse del cap). La fila NACE con
    used=1 — el primer consumo tambien cuenta (un INSERT sin used naceria en
    0 y el primer apply del dia seria gratis). El dia va fijado a UTC EN LA
    EXPRESION — el trigger de 0002 valida quota_date contra el dia UTC de la
    base y CURRENT_DATE dependeria de la TimeZone de la sesion (r2 codex).
    Sin fila devuelta (cap agotado o sin clave) -> False. El cap del INSERT
    sale de la config VIGENTE: el trigger lo re-valida igual."""
    cap = _cap_de_config(conn, platform, kind)
    if cap is None:
        return False
    if cap <= 0:
        # cap 0 = rampa apagada por config (fail-closed): cero applies, sin
        # nacer fila — la visibilidad del estado es la config misma.
        return False
    fila = conn.execute(
        """
        INSERT INTO apply_quota_state (motor, quota_date, cap, used)
        VALUES (%s, (now() AT TIME ZONE 'UTC')::date, %s, 1)
        ON CONFLICT (motor, quota_date) DO UPDATE
           SET used = apply_quota_state.used + 1
         WHERE apply_quota_state.used < apply_quota_state.cap
        RETURNING used
        """,
        (motor_quota(platform, kind), cap),
    ).fetchone()
    return fila is not None


# ---------------------------------------------------------------------------
# Decision bid: fila de la base, carga del ciclo, orden sellado bajo cap
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionBid:
    """Una decision kind='bid' lista para aplicar (espejo de la fila de
    `decision`; `inputs` trae el congelado del ciclo — el aplicador SOLO lee
    de ahi el motivo (banda) y el costo de la ventana para el orden; JAMAS
    inputs.modo)."""

    id: int
    ad_entity_id: int
    old_value: Decimal | None
    new_value: Decimal | None
    value_currency: str | None
    inputs: dict


@dataclass(frozen=True)
class ResultadoAplicador:
    """Resultado estructurado del lote: el ORDEN sellado (ids), cuantas
    quedaron confirmadas por readback, los motivos de descarte/skip (uno por
    decision; el digest de 3.3 los consume tal cual) y el conteo de
    DIVERGENCIAS de readback (GK10/QW4 de la cross-review: la mutacion SALIO,
    Amazon quedo con OTRO bid — antes esa decision desaparecia del resumen;
    vocabulario cerrado de notes['apply'] via cycle)."""

    orden: list[int]
    aplicadas: int
    descartadas: list[str]
    skips: list[str]
    divergencias: int = 0


_SQL_BIDS_CICLO = """
SELECT id, ad_entity_id, old_value, new_value, value_currency, inputs
  FROM decision
 WHERE cycle_id = %s AND kind = 'bid'
 ORDER BY id
"""


def bids_del_ciclo(conn: psycopg.Connection, cycle_id: int) -> list[DecisionBid]:
    """Las decisiones kind='bid' de un ciclo (los bids aplican en su ciclo;
    los cortes van a la cola, sellado 1)."""
    return [
        DecisionBid(
            id=fila[0],
            ad_entity_id=fila[1],
            old_value=fila[2],
            new_value=fila[3],
            value_currency=fila[4],
            inputs=fila[5] or {},
        )
        for fila in conn.execute(_SQL_BIDS_CICLO, (cycle_id,)).fetchall()
    ]


# Prioridad por urgencia de hemorragia (sellado 8): el motivo es el
# vocabulario del motor de bids (bid.MOTIVO_* via _MOTIVO_BANDA).
_PRIORIDAD_BANDA = {"banda_menos_25": 0, "banda_menos_12": 1, "banda_mas_15": 2}
# Clave de orden para costo desconocido (regla 3): dentro de SU banda, al
# final — es una clave de ORDEN, no un valor de negocio.
_COSTO_DESCONOCIDO = Decimal(-1)


def _clave_orden(decision: DecisionBid) -> tuple[int, Decimal]:
    motivo = decision.inputs.get("motivo")
    banda = _PRIORIDAD_BANDA.get(motivo, len(_PRIORIDAD_BANDA))
    ventanas = decision.inputs.get("ventanas") or {}
    cortes = ventanas.get("cortes") or {}
    cost = cortes.get("cost")
    try:
        costo = Decimal(str(cost)) if cost is not None else _COSTO_DESCONOCIDO
    except ArithmeticError:  # InvalidOperation: cost corrupto -> como None
        costo = _COSTO_DESCONOCIDO
    return (banda, -costo)


def orden_bids(decisiones: list[DecisionBid]) -> list[DecisionBid]:
    """Orden sellado de seleccion bajo cap: banda_menos_25 > banda_menos_12 >
    banda_mas_15 y, dentro de cada banda, costo de la ventana DESC (la
    hemorragia mas cara primero)."""
    return sorted(decisiones, key=_clave_orden)


# ---------------------------------------------------------------------------
# SQL del ledger, el resumen y el cache
# ---------------------------------------------------------------------------

_SQL_IDENTIDAD = """
SELECT kind, external_id FROM ad_entity WHERE id = %s
"""

# CX1/GK1 (cross-review): el tope cuenta SOLO intentos 'normal' — las REVERSAS
# son el mecanismo de seguridad (regla 7) y jamas consumen presupuesto de
# intentos: un harvest completo (2 normal) + su reversa completa (2 reversa)
# deja el reintento normal vivo.
_SQL_COUNT_INTENTOS = """
SELECT count(*) FROM apply_attempt WHERE decision_id = %s AND tipo = 'normal'
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

# El crash entre ledger y HTTP deja la fila SIN finished_at: eso ES el rastro
# que la reconciliacion (2.2/2.3) consulta al inicio del ciclo.
_SQL_INTENTOS_SIN_SELLO = """
SELECT id, decision_id, seq, tipo, request_payload, started_at
  FROM apply_attempt
 WHERE finished_at IS NULL
 ORDER BY started_at, id
"""


def intentos_sin_sello(conn: psycopg.Connection) -> list[tuple]:
    """Filas del ledger nacidas PRE-HTTP que nunca se sellaron (crash entre
    ledger y HTTP, o 5xx ambiguo): la entrada de la reconciliacion."""
    return conn.execute(_SQL_INTENTOS_SIN_SELLO).fetchall()


# ADV-04 (review adversaria, matriz §6.1 "Ledger sin sello - bid"): los BIDS
# no viven en apply_queue, asi que su rastro de crash SOLO existe aqui. Solo
# intentos 'normal' de decisions kind bid — reversa/probe quedan RESIDUALES
# declarados (APPLY.md §13). Filtro de plataforma via el ciclo de la decision
# (bug PR27-1: los locks del ciclo son POR plataforma — job_key_de —, asi que
# el ciclo de una plataforma JAMAS reconcilia filas de la otra, como todos los
# demas reconciliadores: _SQL_PAUSES_APLICANDO, _SQL_NEGATIVAS_APLICANDO,
# _SQL_HARVEST_APLICANDO, _SQL_JOBS_EN_VUELO).
_SQL_INTENTOS_BID_SIN_SELLO = """
SELECT a.id, a.decision_id, a.request_payload, d.ad_entity_id, d.new_value,
       d.value_currency
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id
  JOIN optimizer_cycle oc ON oc.id = d.cycle_id
 WHERE a.finished_at IS NULL AND a.tipo = 'normal' AND d.kind = 'bid'
   AND oc.platform = %s::platform
 ORDER BY a.started_at, a.id
"""


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
      FALSE, cache con lo leido) y REINTENTO bajo tope-3 (fila nueva del
      ledger, quota_cobrada=false: la unidad la pago el intento original);
    - ambiguo (5xx agotado / sin bid legible) → failed SIN reintento
      (conserva su cobro, matriz §6.1).

    El reintento divergente se resuelve en la MISMA pasada (PUT + readback);
    un ambiguo del reintento deja la fila nueva SIN sello: ES el rastro del
    proximo ciclo. Devuelve (confirmadas, fallidas)."""
    filas = conn.execute(_SQL_INTENTOS_BID_SIN_SELLO, (platform,)).fetchall()
    if not filas:
        return (0, 0)
    cliente = aplicador._cliente()
    confirmadas = fallidas = 0
    for id_attempt, decision_id, payload, ad_entity_id, new_value, moneda in filas:
        identidad = _identidad(conn, ad_entity_id)
        if identidad is None or identidad[0] not in _KINDS_DECISORAS or new_value is None:
            with conn.transaction():
                _sella_ledger(
                    conn, id_attempt, ack=None, resultado="fallo:reconciliado_sin_identidad"
                )
            fallidas += 1
            continue
        kind, external_id = identidad
        _, path, contenedor, param = _KINDS_DECISORAS[kind]
        try:
            # Readback por LIST PAGINADO con el MISMO scope sellado (probe
            # 2.5: el GET directo retirado; CX1/QW1: el body {} solo trae la
            # primera pagina y la entidad puede vivir en la 2+).
            bid_leido = _bid_de_readback(cliente, path, contenedor, param, external_id)
        except AdsApiError:
            with conn.transaction():
                _sella_ledger(conn, id_attempt, ack=None, resultado="fallo:readback_ambiguo")
            fallidas += 1
            continue
        if bid_leido == Decimal(_bid_payload(new_value)):
            ack = {"fuente": "readback", "bid": str(bid_leido)}
            with conn.transaction():
                _sella_ledger(conn, id_attempt, ack=ack, resultado="ok:reconciliado")
                _confirma_resumen(conn, decision_id, ack, True, aplicador.cycle_id_ejecutor)
                _actualiza_cache(conn, ad_entity_id, bid_leido)
            confirmadas += 1
            continue
        resultado = (
            "fallo:divergencia_readback" if bid_leido is not None else "fallo:readback_sin_bid"
        )
        ack = {"fuente": "readback", "bid": str(bid_leido)} if bid_leido is not None else None
        with conn.transaction():
            _sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
            if bid_leido is not None:
                _actualiza_cache(conn, ad_entity_id, bid_leido)  # sellado 16: lo LEIDO
            _confirma_resumen(conn, decision_id, ack or {}, False, aplicador.cycle_id_ejecutor)
        if bid_leido is None:
            fallidas += 1  # ambiguo: failed SIN reintento (matriz §6.1)
            continue
        # Divergencia con tope disponible → UN reintento (quota ya cobrada).
        count = conn.execute(_SQL_COUNT_INTENTOS, (decision_id,)).fetchone()[0]
        rescata = False
        if count < TOPE_INTENTOS:
            id_retry = _ledger(conn, decision_id, "normal", payload, quota_cobrada=False)
            if id_retry is not None:
                conn.commit()  # intencion durable PRE-HTTP
                rescata = _reintento_divergente(
                    conn,
                    cliente,
                    id_retry,
                    decision_id,
                    ad_entity_id,
                    identidad,
                    new_value,
                    moneda,
                    aplicador,
                )
        if rescata:
            confirmadas += 1
        else:
            fallidas += 1
    return (confirmadas, fallidas)


def _reintento_divergente(
    conn: psycopg.Connection,
    cliente: AdsWriteClient,
    id_retry: int,
    decision_id: int,
    ad_entity_id: int,
    identidad: tuple[str, str],
    new_value: Decimal,
    moneda: str | None,
    aplicador: Aplicador,
) -> bool:
    """El UNICO reintento de un bid divergente reconciliado (tope-3 del
    ledger, quota ya cobrada por el intento original). True = el reintento
    quedo confirmado por readback. Ambiguo del PUT → la fila nueva queda SIN
    sello: ES el rastro del proximo ciclo (misma semantica que _ejecuta_mutacion)."""
    kind, external_id = identidad
    _, path, contenedor, param = _KINDS_DECISORAS[kind]
    try:
        _tick(aplicador._tick_fn)
        if kind == "keyword":
            resp_http = cliente.actualizar_bid_keyword(external_id, new_value, moneda)
        else:
            resp_http = cliente.actualizar_bid_target(external_id, new_value, moneda)
    except AdsApiErrorMutacion as exc:
        _sella_ledger(conn, id_retry, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}")
        conn.commit()
        return False
    except AdsApiError:
        return False  # ambiguo: la fila sin sello ES el rastro
    ack_http = _json_seguro(resp_http)
    _tick(aplicador._tick_fn)
    try:
        # CX1/QW1: readback paginado (la entidad puede vivir en pagina 2+).
        bid2 = _bid_de_readback(cliente, path, contenedor, param, external_id)
    except AdsApiError:
        _sella_ledger(conn, id_retry, ack=ack_http, resultado="fallo:readback_ambiguo")
        conn.commit()
        return False
    ok = bid2 is not None and bid2 == Decimal(_bid_payload(new_value))
    resultado2 = (
        "ok"
        if ok
        else ("fallo:divergencia_readback" if bid2 is not None else "fallo:readback_sin_bid")
    )
    with conn.transaction():
        _sella_ledger(conn, id_retry, ack=ack_http, resultado=resultado2)
        _confirma_resumen(conn, decision_id, ack_http, ok, aplicador.cycle_id_ejecutor)
        if bid2 is not None:
            _actualiza_cache(conn, ad_entity_id, bid2)
    return ok


def _ledger(
    conn: psycopg.Connection,
    decision_id: int,
    tipo: str,
    payload: dict,
    *,
    quota_cobrada: bool,
) -> int | None:
    """Nace la fila del ledger PRE-HTTP: seq = count(*)+1 del decision_id
    contando SOLO intentos 'normal' (CX1/GK1: reversas y probes no consumen
    presupuesto de intentos). Tope 3 (sellado 10): COUNT >= 3 -> None y no
    existe 4o intento."""
    count = conn.execute(_SQL_COUNT_INTENTOS, (decision_id,)).fetchone()[0]
    if count >= TOPE_INTENTOS:
        return None
    return conn.execute(
        _SQL_INSERT_LEDGER,
        (decision_id, count + 1, tipo, Json(payload), quota_cobrada),
    ).fetchone()[0]


def _sella_ledger(
    conn: psycopg.Connection, id_attempt: int, ack: dict | None, resultado: str
) -> None:
    """Sella UNA vez (el trigger de 0002 lo hace cumplir): ack=respuesta del
    HTTP (o None en el rechazo >=400), resultado, finished_at."""
    conn.execute(_SQL_SELLA_LEDGER, (Json(ack) if ack is not None else None, resultado, id_attempt))


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
) -> None:
    """UPSERT del resumen: la terna confirmed_at+platform_ack+verify_ok JUNTA
    (CHECKs de 0001); reintentos = UPDATE del resumen + fila nueva del ledger
    (sellado 10). applied_cycle_id y applied_count SOLO con verify_ok: un
    crash o una divergencia NO cuentan como applied (sellado 21)."""
    conn.execute(_SQL_CONFIRMAR_RESUMEN, (decision_id, Json(ack), verify_ok))
    if verify_ok:
        conn.execute(_SQL_SELLA_CICLO_EJECUTOR, (cycle_id_ejecutor, decision_id))
        conn.execute(_SQL_APPLIED_COUNT, (cycle_id_ejecutor,))


def _actualiza_cache(conn: psycopg.Connection, ad_entity_id: int, bid_leido: Decimal) -> None:
    """El cache queda con LO LEIDO del readback (sellado 16: la fuente es
    Amazon y el readback ES de Amazon; con lo enviado, el ciclo siguiente
    calcularia sobre el bid viejo)."""
    conn.execute(_SQL_ACTUALIZA_CACHE, (bid_leido, ad_entity_id))


def _tick(tick: Callable[[], None] | None) -> None:
    if tick is not None:
        tick()


# ---------------------------------------------------------------------------
# Aplicador
# ---------------------------------------------------------------------------

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
            # ADV-07 (review adversaria): SIN el lambda no-op — el sleep solo
            # viaja cuando el test lo inyecta; en produccion el write client
            # conserva SU time.sleep real (backoff de 429 y refresh LWA).
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

        Por decision: re-resolucion -> quota (si no cabe: descartado, JAMAS
        HTTP) -> ledger PRE-HTTP + COMMIT -> HTTP -> readback sellado ->
        sello de ledger + resumen + cache + applied_count (ver docstring del
        modulo). >=400 se captura (ledger sellado con el cuerpo); 5xx/fallo
        ambiguo SUBE (ledger sin sello: la fila ES el rastro)."""
        ordenadas = orden_bids(decisiones)
        skips: list[str] = []
        descartadas: list[str] = []
        aplicadas = 0
        divergencias = 0
        for decision in ordenadas:
            modo = self.modo_efectivo(self._conn, decision, escalera_global=escalera_global)
            if modo != "live":
                skips.append(MOTIVO_MODO_NO_LIVE)
                continue
            if _ya_aplicada(self._conn, decision.id):
                skips.append(MOTIVO_YA_APLICADA)
                continue
            if decision.new_value is None or decision.value_currency is None:
                skips.append(MOTIVO_BID_INCOMPLETO)
                continue
            identidad = _identidad(self._conn, decision.ad_entity_id)
            if identidad is None or identidad[0] not in _KINDS_DECISORAS:
                skips.append(MOTIVO_ENTIDAD_NO_DECISORA)
                continue
            # GK5/QW2: el tope se chequea ANTES del cobro — la unidad NO se
            # quema en una decision ya a tope (el _ledger re-chequea igual
            # como red de seguridad ante carreras).
            if self._conn.execute(_SQL_COUNT_INTENTOS, (decision.id,)).fetchone()[0] >= (
                TOPE_INTENTOS
            ):
                skips.append(MOTIVO_TOPE_INTENTOS)
                continue
            if not consume_quota(self._conn, self._platform, "bid"):
                # Bids fuera de cap = DESCARTADOS, jamas reintentados (8).
                descartadas.append(MOTIVO_FUERA_DE_CAP)
                continue
            payload = _payload_bid(identidad[0], identidad[1], decision.new_value)
            id_attempt = _ledger(self._conn, decision.id, "normal", payload, quota_cobrada=True)
            if id_attempt is None:
                skips.append(MOTIVO_TOPE_INTENTOS)
                continue
            resultado_bid = self._ejecuta_mutacion(decision, identidad, id_attempt)
            if resultado_bid is None:
                skips.append(MOTIVO_FALLO_HTTP)
            elif resultado_bid:
                aplicadas += 1
            else:
                # GK10/QW4: la mutacion SALIO y Amazon quedo con OTRO bid —
                # observable en el campo propio del resultado.
                divergencias += 1
        return ResultadoAplicador(
            orden=[d.id for d in ordenadas],
            aplicadas=aplicadas,
            descartadas=descartadas,
            skips=skips,
            divergencias=divergencias,
        )

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
        # QW1: la divergencia sella SIEMPRE la misma etiqueta que la
        # reconciliacion (antes 'ok' con verify_ok False).
        resultado = (
            "ok"
            if verify_ok
            else (
                "fallo:divergencia_readback" if bid_leido is not None else "fallo:readback_sin_bid"
            )
        )
        with self._conn.transaction():
            _sella_ledger(self._conn, id_attempt, ack=ack, resultado=resultado)
            _confirma_resumen(self._conn, decision.id, ack, verify_ok, self.cycle_id_ejecutor)
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


# ---------------------------------------------------------------------------
# Reversa de bid (regla 7)
# ---------------------------------------------------------------------------


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
    # Readback por LIST PAGINADO (probe 2.5: GET directo retirado; CX1/QW1),
    # scope sellado.
    bid_leido = _bid_de_readback(cliente, path, contenedor, param, identidad[1])
    # QW1: misma etiqueta de divergencia que el apply y la reconciliacion.
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


# ---------------------------------------------------------------------------
# Reversa MANUAL (ORBIT 04, task 3.1): el unico AdsWriteClient fuera del ciclo
# ---------------------------------------------------------------------------

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

# El ack del ULTIMO intento normal ok de la decision: de ahi sale SIEMPRE el
# negative_id del DELETE (ADV-2: una sola fuente — el body no lo acepta).
_SQL_ACK_OK = """
SELECT ack FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'normal' AND resultado = 'ok'
 ORDER BY seq DESC LIMIT 1
"""

# ADV-3: una reversa confirmada por decision — sin esto, el endpoint
# despachaba HTTP real ilimitado en loop (las reversas estan exentas de quota
# y del tope-3, y la fila de la cola no cambia de estado al revertir).
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
