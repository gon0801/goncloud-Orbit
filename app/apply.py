"""Nucleo del aplicador de decisiones (ORBIT 04, task 2.1).

El nucleo que EJECUTA mutaciones en Amazon: re-resuelve el modo POR
DECISION, cobra quota, nace la fila del ledger PRE-HTTP, muta, hace readback
con el scope sellado y sella ledger + resumen + cache. Vive fuera de
app/optimizer/ como cycle.py (importa psycopg y el cliente de escritura; el
motor sigue puro).

Este modulo es el UNICO importador de `app.ads.write` para el aplicador y
re-exporta la superficie estable de `app.apply_cuota`, `app.apply_store`,
`app.apply_reconcilia` y `app.apply_reversa`. Incluye la lectura fresca del bid
antes del cupo y del PUT (TEMPORADA 01 E).

Aislamiento por entidad (ARREGLOS 05 A.3): un fallo remoto de UNA puja
(5xx, red o lectura ilegible) la deja incierta y no detiene a las demas pujas
ni a las fases que siguen (cortes, reactivaciones). Los fallos globales
(claim perdido, base indisponible, identidad o permisos invalidos) si
detienen todas las mutaciones.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Literal

import httpx
import psycopg
from psycopg.errors import LockNotAvailable

from app import lock, temporada_io
from app.ads.client import AdsApiError as AdsApiError  # noqa: F401 - re-export estable
from app.ads.config import AdsCredentials

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
    MOTIVO_VIGENCIA_VENCIDA,
    MOTIVO_YA_APLICADA,
    MOTIVO_YA_INTENTADA,
    MOTIVOS_SIN_APLICAR,
    RESULTADO_ANCESTRO_NO_ENABLED,
    CapSaturado,
    DecisionBid,
    ResultadoAplicador,
    _cap_de_config,
    bids_del_ciclo,
    bids_recuperables,
    cap_desde_settings,
    consume_quota,
    consume_quota_y_sello,
    estado_quota,
    evento_cap_saturado,
    expira_no_intentadas,
    gate_ancestros,
    hojas_con_bid_sin_sello,
    intentos_sin_sello,
    motor_quota,
    orden_bajo_cupo,
    prioridad_bajo_cupo,
    registra_sin_aplicar,
)
from app.apply_reconcilia import (  # noqa: F401 - re-export estable
    _SQL_INTENTOS_BID_AMBIGUOS_SIN_DESENLACE,
)
from app.apply_reconcilia import (
    _reconcilia_ambiguos_sellados as _reconcilia_ambiguos_sellados_impl,
)
from app.apply_reconcilia import (
    reconcilia_bids as _reconcilia_bids_impl,
)
from app.apply_reversa import (  # noqa: F401 - re-export estable
    _SQL_ACK_OK,
    _SQL_DECISION_POR_ID,
    _SQL_PLATAFORMA_FILA,
    _SQL_RESUMEN_APLICADA,
    _SQL_REVERSA_OK,
    NegativeIdNoResoluble,
    ReversaInexistente,
    ReversaNoAplicada,
    ReversaYaHecha,
    SinPerfilAjuste,
    SinPerfilReversa,
    _negative_id_del_ledger,
    _reversa_ya_hecha,
)
from app.apply_reversa import (
    reversa_bid as _reversa_bid_impl,
)
from app.apply_reversa import (
    reversa_manual as _reversa_manual_impl,
)
from app.apply_store import (  # noqa: F401 - re-export estable
    _KINDS_DECISORAS,
    _SQL_ACTUALIZA_CACHE,
    _SQL_APPLIED_COUNT,
    _SQL_CONFIRMAR_RESUMEN,
    _SQL_COUNT_INTENTOS,
    _SQL_GUARDA_ACK,
    _SQL_HERMANA_POR_ADGROUP,
    _SQL_IDENTIDAD,
    _SQL_INSERT_LEDGER,
    _SQL_INTENTOS_HERMANA_DETALLE,
    _SQL_MAX_SEQ,
    _SQL_SELLA_CICLO_EJECUTOR,
    _SQL_SELLA_LEDGER,
    _SQL_SELLA_RESULTADO,
    _SQL_YA_APLICADA,
    ESTADO_WIRE_ARCHIVED,
    ESTADO_WIRE_ENABLED,
    ESTADO_WIRE_PAUSED,
    TOPE_INTENTOS,
    TOPE_PAGINAS_READBACK,
    _actualiza_cache,
    _bid_de_fila,
    _bid_de_readback,
    _bid_leido,
    _confirma_resumen,
    _estado_de_fila,
    _estado_de_readback,
    _estado_leido,
    _fila_de_filas,
    _fila_de_lista,
    _fila_de_readback,
    _guarda_ack,
    _identidad,
    _intentos_hermana,
    _intentos_hermana_detalle,
    _json_seguro,
    _ledger,
    _sella_ledger,
    _sella_resultado,
    _tick,
    _ya_aplicada,
    perfil_aceptado_de,
)
from app.db import tx_durable
from app.optimizer import goals as g

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


# Lo que le pasa a UNA mutacion de puja cuyo ledger ya nacio PRE-HTTP.
# `incierta`: Amazon pudo aplicarla (5xx o red al enviar, o lectura de vuelta
# ilegible tras un ack). Su fila queda sin sello y la decision sin desenlace
# hasta que la conciliacion la resuelve con una lectura: JAMAS se vuelve a
# enviar.
DesenlaceBid = Literal["aplicada", "divergente", "rechazada", "incierta"]


class DuenoDelClaimCambio(Exception):
    """El sello encontro la fila del claim en manos de otro owner: este
    proceso fue desplazado mientras esperaba una respuesta de Amazon (ARREGLOS
    05 A). El intento queda sin sellar; lo trata igual que `ApplyAbortado`."""


# Cerca del sello: la fila del claim bloqueada en la transaccion que sella.
_SQL_DUENO_CERCA = "SELECT owner FROM ads_optimizer_lock WHERE job_key = %s FOR UPDATE"

# Un 403, o un 401 que sigue despues de refrescar el token, no rechaza ESA
# hoja: Amazon rechaza la identidad o los permisos de toda la corrida.
STATUS_IDENTIDAD_RECHAZADA = frozenset({401, 403})


def es_rechazo_de_identidad(exc: BaseException) -> bool:
    """True si `exc` es un rechazo de mutacion por identidad o permisos.

    Es un fallo global, no el desenlace de una hoja: quien lo recibe sella SU
    intento con el rechazo (Amazon no aplico) y lo propaga. Asi se detienen las
    mutaciones que faltan y las fases despues (ARREGLOS 05 A, revision)."""
    return isinstance(exc, AdsApiErrorMutacion) and exc.status in STATUS_IDENTIDAD_RECHAZADA


def _bid_quantizado(bid: Decimal) -> Decimal:
    """Decimal quantizado con la MISMA fuente que el wire (`app.ads.write._bid_payload`)."""
    return Decimal(_bid_payload(bid))


def _payload_bid(kind_entidad: str, external_id: str, bid: Decimal) -> dict:
    """Payload EXACTO del ledger: MISMO shape y MISMA serializacion que el write client."""
    campo = "keywordId" if kind_entidad == "keyword" else "targetId"
    return {campo: external_id, "bid": _bid_payload(bid)}


def _reconcilia_ambiguos_sellados(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    cliente_holder: list,
) -> tuple[int, int]:
    return _reconcilia_ambiguos_sellados_impl(
        conn, aplicador, platform, cliente_holder, bid_quantizado=_bid_quantizado
    )


def reconcilia_bids(
    conn: psycopg.Connection, aplicador: Aplicador, platform: str
) -> tuple[int, int]:
    """Reconciliacion del ledger de BIDS sin sello y ambiguos de UNA plataforma."""
    return _reconcilia_bids_impl(conn, aplicador, platform, bid_quantizado=_bid_quantizado)


class _TransportGuardado(httpx.BaseTransport):
    """Transport decorado con el guard pre-HTTP (gancho 2.4, decision 11): el
    callback corre ANTES de despachar CADA request."""

    def __init__(self, base: httpx.BaseTransport, guard: Callable[[], None]) -> None:
        self._base = base
        self._guard = guard

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._guard()
        return self._base.handle_request(request)


class Aplicador:
    """Aplica decisiones de bid de UN ciclo ejecutor sobre UNA plataforma.

    El write client se construye LAZY en el primer apply que re-resuelva 'live'.
    En shadow: cero HTTP (ni token LWA).
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
        ciclo_origen: int | None = None,
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
        # ARREGLOS 05 A: el ciclo cuyas notas y evaluaciones son el contexto de
        # decision. Es el propio, salvo en un ejecutor de recuperacion, que
        # completa las decisiones de otro ciclo y no decidio nada.
        self.ciclo_origen = cycle_id_ejecutor if ciclo_origen is None else ciclo_origen
        self.owner = owner
        self.job_key = job_key
        self._tick_fn = tick
        self._transport = transport
        self._sleep = sleep
        self._guard_http = guard_http
        self._write_client: AdsWriteClient | None = None

    def modo_efectivo(
        self, conn: psycopg.Connection, decision: DecisionBid | int, *, escalera_global: str
    ) -> str:
        """Re-resuelve el modo PARA ESTA decision (o ad_entity_id) contra la base FRESCA."""
        entidad_id = decision if isinstance(decision, int) else decision.ad_entity_id
        fila = conn.execute(_SQL_ENTIDAD_VIVA, (entidad_id,)).fetchone()
        if fila is None or not fila[1]:
            return "shadow"
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
        return g.modo_efectivo(escalera_global, resuelto.mode)

    def _cliente(self) -> AdsWriteClient:
        if self._credentials is None or self._profile_id is None:
            raise ValueError(
                "Aplicador sin credentials/profile_id: el modo live exige la "
                "fabrica del ciclo (2.4), no un cliente a ciegas"
            )
        if self._write_client is None:
            transport: httpx.BaseTransport | None = self._transport
            if self._guard_http is not None:
                base = self._transport if self._transport is not None else httpx.HTTPTransport()
                transport = _TransportGuardado(base, self._guard_http)
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

    def aplica_bids(
        self, decisiones: list[DecisionBid], *, escalera_global: str
    ) -> ResultadoAplicador:
        """Aplica el lote de bids del ciclo en el ORDEN sellado bajo cap."""
        ordenadas = orden_bajo_cupo(decisiones)
        skips: list[str] = []
        descartadas: list[str] = []
        aplicadas = 0
        divergencias = 0
        inciertos = 0
        caps_saturados: list[CapSaturado] = []
        lote_cortado_429 = False
        for decision in ordenadas:
            modo = self.modo_efectivo(self._conn, decision, escalera_global=escalera_global)
            if modo != "live":
                skips.append(MOTIVO_MODO_NO_LIVE)
                if not _ya_aplicada(self._conn, decision.id):
                    registra_sin_aplicar(
                        self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_MODO_NO_LIVE
                    )
                continue
            if _ya_aplicada(self._conn, decision.id):
                skips.append(MOTIVO_YA_APLICADA)
                continue
            motivo_datos, identidad = self._guarda_de_datos(decision)
            if motivo_datos is not None:
                skips.append(motivo_datos)
                if motivo_datos != MOTIVO_YA_INTENTADA:
                    registra_sin_aplicar(
                        self._conn, decision.id, self.cycle_id_ejecutor, motivo_datos
                    )
                continue
            assert identidad is not None  # sin motivo, _guarda_de_datos la devuelve
            if lote_cortado_429:
                # Las que faltan tras un 429 tampoco se consultan, pero SI dejan
                # su desenlace: sin fila, la vista de auditoria las leeria como
                # huerfanas de un ciclo live cerrado.
                skips.append(MOTIVO_SIN_RESPUESTA)
                registra_sin_aplicar(
                    self._conn,
                    decision.id,
                    self.cycle_id_ejecutor,
                    MOTIVO_SIN_RESPUESTA,
                    detalle={"lote_cortado": "429"},
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
                    lote_cortado_429 = True
                    continue
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
                descartadas.append(MOTIVO_FUERA_DE_CAP)
                registra_sin_aplicar(
                    self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_FUERA_DE_CAP
                )
                continue
            if saturada:
                evento = evento_cap_saturado(self._conn, self._platform, "bid")
                if evento is not None:
                    caps_saturados.append(evento)
            payload = _payload_bid(identidad[0], identidad[1], decision.new_value)
            # La reserva (cuota y fila del ledger) nace solo del dueno del
            # claim: un owner vencido no deja intencion durable ni gasta cupo.
            _tick(self._tick_fn)
            id_attempt = _ledger(self._conn, decision.id, "normal", payload, quota_cobrada=True)
            if id_attempt is None:
                skips.append(MOTIVO_TOPE_INTENTOS)
                registra_sin_aplicar(
                    self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_TOPE_INTENTOS
                )
                continue
            desenlace = self._ejecuta_mutacion(decision, identidad, id_attempt)
            if desenlace == "aplicada":
                aplicadas += 1
            elif desenlace == "divergente":
                divergencias += 1
            elif desenlace == "rechazada":
                skips.append(MOTIVO_FALLO_HTTP)
                registra_sin_aplicar(
                    self._conn, decision.id, self.cycle_id_ejecutor, MOTIVO_FALLO_HTTP
                )
            else:
                inciertos += 1
        return ResultadoAplicador(
            orden=[d.id for d in ordenadas],
            aplicadas=aplicadas,
            descartadas=descartadas,
            skips=skips,
            divergencias=divergencias,
            inciertos=inciertos,
            caps_saturados=tuple(caps_saturados),
        )

    def _guarda_de_datos(self, decision: DecisionBid) -> tuple[str | None, tuple[str, str] | None]:
        """Guardas LOCALES de datos previas al LIST (cero HTTP): bid completo,
        entidad decisora, tope de intentos y, para una recuperacion, que la
        decision no tenga ya un intento. Devuelve (motivo, identidad); motivo
        None = la puja llega a la lectura fresca. Las comparten el lote normal
        y el corte por 429 para que una puja descartable nunca quede como
        `sin_respuesta`.

        ARREGLOS 05 A.3: una decision con un intento `normal`, cierto o
        incierto, no vuelve a salir por el aplicador (`ya_intentada`: etiqueta
        en memoria, sin desenlace propio). Solo la conciliacion la resuelve,
        leyendo; asi ni un 5xx ni un ejecutor de recuperacion repetiran la
        misma mutacion."""
        if decision.new_value is None or decision.value_currency is None:
            return MOTIVO_BID_INCOMPLETO, None
        identidad = _identidad(self._conn, decision.ad_entity_id)
        if identidad is None or identidad[0] not in _KINDS_DECISORAS:
            return MOTIVO_ENTIDAD_NO_DECISORA, None
        # Verificar tope antes de cobrar quota para no quemar unidades en
        # decisiones agotadas (_ledger vuelve a verificar ante carreras).
        intentos = self._conn.execute(_SQL_COUNT_INTENTOS, (decision.id,)).fetchone()[0]
        if intentos >= TOPE_INTENTOS:
            return MOTIVO_TOPE_INTENTOS, None
        if intentos > 0:
            return MOTIVO_YA_INTENTADA, None
        # Campana y ad group ENABLED (misma fuente que la cola y los
        # reconciliadores): al decidir ya se miraron; una recuperacion horas
        # despues los vuelve a mirar.
        if gate_ancestros(self._conn, decision.ad_entity_id) is not None:
            return MOTIVO_ENTIDAD_NO_DECISORA, None
        return None, identidad

    def _ejecuta_mutacion(
        self, decision: DecisionBid, identidad: tuple[str, str], id_attempt: int
    ) -> DesenlaceBid:
        """Secuencia sellada de UNA mutacion cuyo ledger ya nacio PRE-HTTP.

        Aislamiento por entidad (ARREGLOS 05 A.3): un fallo remoto de ESTA
        puja (`AdsApiError`: 5xx, red o lectura de vuelta ilegible) la deja
        `incierta` y no detiene a las demas ni a las fases que siguen. Su fila
        del ledger queda SIN sello, que es el rastro de siempre (APPLY.md
        §6.1): lo resuelve la conciliacion del ciclo siguiente con una
        lectura, jamas otro envio. Los fallos globales (claim perdido, base
        indisponible, identidad o permisos invalidos) siguen propagando. Se
        sella solo con el claim todavia en poder de este owner: `_tick`
        valida al dueno antes de cada sello."""
        self._conn.commit()
        cliente = self._cliente()
        try:
            _tick(self._tick_fn)
            resp_http = self._muta_bid(
                cliente, identidad, decision.new_value, decision.value_currency
            )
        except AdsApiErrorMutacion as exc:
            # Rechazo determinista (4xx con cuerpo): Amazon NO la aplico.
            _tick(self._tick_fn)
            identidad_o_permisos = es_rechazo_de_identidad(exc)
            with tx_durable(self._conn):
                _sella_ledger(
                    self._conn,
                    id_attempt,
                    ack=None,
                    resultado=f"fallo http {exc.status}: {exc.cuerpo}",
                )
                if identidad_o_permisos:
                    # Fallo global: la fase aborta y `aplica_bids` no llegara a
                    # registrar el desenlace de esta puja. Se cierra aqui, en la
                    # misma transaccion que el sello: sin desenlace quedaria
                    # huerfana (tiene intento: no se reintenta, no se concilia
                    # ni vence) y el plan abriria un ejecutor en cada horario.
                    registra_sin_aplicar(
                        self._conn,
                        decision.id,
                        self.cycle_id_ejecutor,
                        MOTIVO_FALLO_HTTP,
                        detalle={"http": str(exc.status), "fallo": "identidad_o_permisos"},
                    )
            if identidad_o_permisos:
                raise
            return "rechazada"
        except AdsApiError:
            # Amazon pudo aplicarla: la fila queda sin sello (el rastro).
            return "incierta"
        ack = _json_seguro(resp_http)
        _tick(self._tick_fn)
        try:
            bid_leido = self._readback(cliente, identidad)
        except AdsApiError:
            return "incierta"
        verify_ok = bid_leido is not None and bid_leido == _bid_quantizado(decision.new_value)
        resultado = (
            "ok"
            if verify_ok
            else (
                "fallo:divergencia_readback" if bid_leido is not None else "fallo:readback_sin_bid"
            )
        )
        # La lectura de vuelta pudo tardar: el dueno se vuelve a validar al
        # volver y, dentro del sello, con la fila del claim bloqueada.
        _tick(self._tick_fn)
        with tx_durable(self._conn):
            _sella_ledger(self._conn, id_attempt, ack=ack, resultado=resultado)
            self.confirma_cercada(decision.id, ack, verify_ok, leido=bid_leido)
            if bid_leido is not None:
                _actualiza_cache(self._conn, decision.ad_entity_id, bid_leido)
        return "aplicada" if verify_ok else "divergente"

    def exige_dueno(self) -> None:
        """Cerca del claim para el sello en curso (ARREGLOS 05 A).

        Lee la fila del claim con FOR UPDATE dentro de la transaccion que
        sella: el sello y cualquier cambio de dueno se ordenan, porque quien
        claima actualiza esa misma fila. Si la fila nombra a otro owner, el
        proceso fue desplazado mientras esperaba una respuesta y no sella: el
        intento queda sin sellar y la conciliacion del sucesor lo resuelve.
        Sin fila tampoco se sella: el claim se solto (este owner al terminar, o
        un sucesor que lo tomo y lo solto entre el aviso anterior y la cerca),
        asi que ya no es dueno de la ejecucion."""
        # Cerca con tope: si otra transaccion retiene la fila y no la suelta a
        # tiempo, el dueno no se puede probar y se trata como perdido.
        lock.acota_espera_de_cerrojos(self._conn)
        try:
            fila = self._conn.execute(_SQL_DUENO_CERCA, (self.job_key,)).fetchone()
        except LockNotAvailable as exc:
            raise DuenoDelClaimCambio(
                f"lock {self.job_key} retenido por otra transaccion: no se sella"
            ) from exc
        if fila is not None and fila[0] == self.owner:
            return
        raise DuenoDelClaimCambio(
            f"lock {self.job_key} ya no es nuestro"
            f" (owner: {fila[0] if fila is not None else 'sin fila'}): no se sella"
        )

    def confirma_cercada(
        self,
        decision_id: int,
        ack: dict,
        verify_ok: bool,
        *,
        leido: Decimal | None = None,
    ) -> None:
        """Confirma el resumen solo si el claim sigue en manos de este owner
        (misma transaccion que el sello del ledger)."""
        self.exige_dueno()
        _confirma_resumen(
            self._conn, decision_id, ack, verify_ok, self.cycle_id_ejecutor, leido=leido
        )

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
        """LIST FRESCO PAGINADO con el MISMO scope sellado."""
        kind, external_id = identidad
        _, path, contenedor, param = _KINDS_DECISORAS[kind]
        return _bid_de_readback(cliente, path, contenedor, param, external_id)


def _cliente_live_por_plataforma(
    platform: str,
    *,
    transport: httpx.BaseTransport | None,
    error_cls: type[Exception],
    etiqueta: str,
) -> AdsWriteClient:
    """Construye `AdsWriteClient` live resolviendo perfil via `perfil_aceptado_de`."""
    credentials = AdsCredentials.from_secrets_dir()
    perfil = perfil_aceptado_de(credentials, platform, transport=transport)
    if perfil is None:
        raise error_cls(
            f"sin perfil aceptado para {platform} en /v2/profiles: {etiqueta}"
            " aborta fail-closed (regla 3: jamas un profile inventado)"
        )
    return AdsWriteClient(
        credentials,
        platform=platform,
        profile_id=perfil.profile_id,
        modo_confirmado=MODO_CONFIRMADO_LIVE,
        transport=transport,
    )


def _cliente_reversa(platform: str, *, transport: httpx.BaseTransport | None) -> AdsWriteClient:
    """Write client de la reversa manual (mismo camino del ciclo via perfil_aceptado_de)."""
    return _cliente_live_por_plataforma(
        platform, transport=transport, error_cls=SinPerfilReversa, etiqueta="la reversa"
    )


def _cliente_ajuste(platform: str, *, transport) -> AdsWriteClient:
    """Write client del ajuste (mismo camino del ciclo)."""
    return _cliente_live_por_plataforma(
        platform, transport=transport, error_cls=SinPerfilAjuste, etiqueta="el ajuste"
    )


def reversa_bid(
    conn: psycopg.Connection,
    cliente: AdsWriteClient,
    decision: DecisionBid,
    *,
    tick: Callable[[], None] | None = None,
) -> bool:
    """Reversa del bid: PUT con old_value, ledger tipo 'reversa', readback y sello."""
    return _reversa_bid_impl(
        conn,
        cliente,
        decision,
        tick=tick,
        payload_bid=_payload_bid,
        bid_quantizado=_bid_quantizado,
        error_mutacion_cls=AdsApiErrorMutacion,
    )


def reversa_manual(
    conn: psycopg.Connection,
    *,
    tipo: str,
    decision_id: int | None = None,
    queue_id: int | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Reversa MANUAL (regla 7, sellado 12): bid, pause o negative."""
    return _reversa_manual_impl(
        conn,
        tipo=tipo,
        decision_id=decision_id,
        queue_id=queue_id,
        transport=transport,
        cliente_reversa=_cliente_reversa,
        reversa_bid_fn=reversa_bid,
    )
