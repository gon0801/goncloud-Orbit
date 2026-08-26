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
  (write client) -> readback GET FRESCO con el MISMO scope sellado ->
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

PENDIENTES del probe autorizado 2.5 (brief §13, sellado 23): el path y el
shape del readback (contenedor 'keywords'/'targets', campo 'bid') son
supuestos de esta task sellados por tests contra MockTransport; el probe los
fija contra las formas reales.

`owner`/`job_key` se guardan para el ownership-check pre-HTTP de la
integracion (2.4, decision 11); el heartbeat `tick` se llama DURANTE la
mutacion y el readback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import httpx
import psycopg
from psycopg.types.json import Json

from app.ads.config import AdsCredentials
from app.ads.write import (
    MODO_CONFIRMADO_LIVE,
    PLATAFORMA_MONEDA,
    AdsApiErrorMutacion,
    AdsWriteClient,
    _bid_payload,
)
from app.optimizer import goals as g

# Tope de intentos por decision: "no existe 4o intento" es un COUNT verificable
# contra el ledger (sellado 10 / brief §4.1).
TOPE_INTENTOS = 3

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
    quedaron confirmadas por readback, y los motivos de descarte/skip (uno
    por decision; el digest de 3.3 los consume tal cual)."""

    orden: list[int]
    aplicadas: int
    descartadas: list[str]
    skips: list[str]


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

_SQL_COUNT_INTENTOS = """
SELECT count(*) FROM apply_attempt WHERE decision_id = %s
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


def _ledger(
    conn: psycopg.Connection,
    decision_id: int,
    tipo: str,
    payload: dict,
    *,
    quota_cobrada: bool,
) -> int | None:
    """Nace la fila del ledger PRE-HTTP: seq = count(*)+1 del decision_id.
    Tope 3 (sellado 10): COUNT >= 3 -> None y no existe 4o intento."""
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
    """El JSON del ack; si el body no parsea, evidencia minima (el ack crudo
    JAMAS debe tumbar el sello del ledger)."""
    try:
        return resp.json()
    except ValueError:
        return {"status": resp.status_code, "body": resp.text[:200]}


def _bid_leido(resp: httpx.Response, contenedor: str) -> Decimal | None:
    """El bid del READBACK fresco. PENDIENTE del probe 2.5: el shape
    (contenedor 'keywords'/'targets' con el campo 'bid') es supuesto sellado
    por tests; el probe lo fija contra la API real. None = sin bid legible."""
    try:
        filas = resp.json().get(contenedor)
        bid = filas[0].get("bid") if filas else None
    except (ValueError, AttributeError, IndexError, KeyError, TypeError):
        return None
    if bid is None:
        return None
    try:
        return Decimal(str(bid))
    except ArithmeticError:
        return None


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

# (kind de la entidad) -> (metodo del write client, path del readback,
# contenedor de la respuesta, param del GET). PENDIENTE del probe 2.5 para
# el shape del readback.
_KINDS_DECISORAS = {
    "keyword": ("bid_keyword", "/sp/keywords", "keywords", "keywordId"),
    "product_target": ("bid_target", "/sp/targets", "targets", "targetId"),
}


class Aplicador:
    """Aplica decisiones de bid de UN ciclo ejecutor sobre UNA plataforma.

    El write client NO se construye en __init__: se construye LAZY en el
    primer apply que re-resuelva 'live' — AdsWriteClient revienta si el modo
    no es exactamente 'live' (fail-closed), asi que la re-resolucion POR
    DECISION es la que decide si existe cliente. En shadow: cero HTTP (ni
    token LWA). `transport`/`sleep` son la inyeccion de tests (MockTransport),
    misma puerta que AdsClient."""

    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        platform: str,
        profile_id: str | int,
        credentials: AdsCredentials,
        cycle_id_ejecutor: int,
        owner: str,
        job_key: str,
        tick: Callable[[], None] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
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
        if self._write_client is None:
            self._write_client = AdsWriteClient(
                self._credentials,
                platform=self._platform,
                profile_id=self._profile_id,
                modo_confirmado=MODO_CONFIRMADO_LIVE,
                transport=self._transport,
                sleep=self._sleep if self._sleep is not None else (lambda seconds: None),
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
        return ResultadoAplicador(
            orden=[d.id for d in ordenadas],
            aplicadas=aplicadas,
            descartadas=descartadas,
            skips=skips,
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
        resultado = "ok" if bid_leido is not None else "fallo:readback_sin_bid"
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
        """GET FRESCO con el MISMO scope sellado (get_sellado: el re-check
        JAMAS pasa un profile a mano — sellado 16)."""
        kind, external_id = identidad
        _, path, contenedor, param = _KINDS_DECISORAS[kind]
        resp = cliente.get_sellado(path, params={param: external_id})
        return _bid_leido(resp, contenedor)


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
    resp = cliente.get_sellado(path, params={param: identidad[1]})
    bid_leido = _bid_leido(resp, contenedor)
    resultado = "ok" if bid_leido is not None else "fallo:readback_sin_bid"
    with conn.transaction():
        _sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
        if bid_leido is not None:
            _actualiza_cache(conn, decision.ad_entity_id, bid_leido)
    return bid_leido is not None and bid_leido == Decimal(_bid_payload(decision.old_value))
