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
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import httpx
import psycopg

from app import temporada_io
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


@dataclass(frozen=True)
class DecisionResume:
    """Una decision kind='resume' del ciclo (motor temporada, bloque F)."""

    id: int
    ad_entity_id: int
    inputs: dict


_SQL_RESUMES_CICLO = """
SELECT id, ad_entity_id, inputs
  FROM decision
 WHERE cycle_id = %s AND kind = 'resume'
 ORDER BY id
"""


def resumes_del_ciclo(conn: psycopg.Connection, cycle_id: int) -> list[DecisionResume]:
    """Las decisiones resume del ciclo, para el aplicador de reactivaciones."""
    return [
        DecisionResume(id=f[0], ad_entity_id=f[1], inputs=f[2] or {})
        for f in conn.execute(_SQL_RESUMES_CICLO, (cycle_id,)).fetchall()
    ]


@dataclass(frozen=True)
class ResumenReactivacion:
    """Resultado del lote de reactivaciones: confirmadas por Amazon, fallidas,
    sin cupo y omitidas por una guarda (van al ciclo siguiente sin tocar nada)."""

    confirmadas: int = 0
    fallidas: int = 0
    sin_quota: int = 0
    omitidas: int = 0


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
        self.owner = owner
        self.job_key = job_key
        self._tick_fn = tick
        self._transport = transport
        self._sleep = sleep
        self._guard_http = guard_http
        self._write_client: AdsWriteClient | None = None

    def modo_efectivo(
        self, conn: psycopg.Connection, decision: DecisionBid, *, escalera_global: str
    ) -> str:
        """Re-resuelve el modo PARA ESTA decision contra la base FRESCA."""
        fila = conn.execute(_SQL_ENTIDAD_VIVA, (decision.ad_entity_id,)).fetchone()
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
        caps_saturados: list[CapSaturado] = []
        for indice, decision in enumerate(ordenadas):
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
                registra_sin_aplicar(self._conn, decision.id, self.cycle_id_ejecutor, motivo_datos)
                continue
            assert identidad is not None  # sin motivo, _guarda_de_datos la devuelve
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

    def _guarda_de_datos(self, decision: DecisionBid) -> tuple[str | None, tuple[str, str] | None]:
        """Guardas LOCALES de datos previas al LIST (cero HTTP): bid completo,
        entidad decisora y tope de intentos. Devuelve (motivo, identidad);
        motivo None = la puja llega a la lectura fresca. Las comparten el lote
        normal y el corte por 429 para que una puja descartable nunca quede
        como `sin_respuesta`."""
        if decision.new_value is None or decision.value_currency is None:
            return MOTIVO_BID_INCOMPLETO, None
        identidad = _identidad(self._conn, decision.ad_entity_id)
        if identidad is None or identidad[0] not in _KINDS_DECISORAS:
            return MOTIVO_ENTIDAD_NO_DECISORA, None
        # Verificar tope antes de cobrar quota para no quemar unidades en
        # decisiones agotadas (_ledger vuelve a verificar ante carreras).
        if self._conn.execute(_SQL_COUNT_INTENTOS, (decision.id,)).fetchone()[0] >= (TOPE_INTENTOS):
            return MOTIVO_TOPE_INTENTOS, None
        return None, identidad

    def _registra_lote_cortado(
        self, restantes: list[DecisionBid], *, escalera_global: str
    ) -> list[str]:
        """El 429 agoto la lectura fresca y corta el lote: cada puja restante
        deja su desenlace SIN HTTP, con las MISMAS guardas locales del camino
        normal (modo, aplicacion previa y datos). Una puja de campana en
        practica conserva `modo_no_live`, una descartable conserva su motivo
        (`bid_incompleto`, `entidad_no_decisora`, `tope_intentos`), y solo la
        que habria llegado al LIST queda `sin_respuesta` con
        `detalle.lote_cortado`."""
        motivos: list[str] = []
        for restante in restantes:
            if self.modo_efectivo(self._conn, restante, escalera_global=escalera_global) != "live":
                motivos.append(MOTIVO_MODO_NO_LIVE)
                if not _ya_aplicada(self._conn, restante.id):
                    registra_sin_aplicar(
                        self._conn, restante.id, self.cycle_id_ejecutor, MOTIVO_MODO_NO_LIVE
                    )
                continue
            if _ya_aplicada(self._conn, restante.id):
                motivos.append(MOTIVO_YA_APLICADA)
                continue
            motivo, _identidad = self._guarda_de_datos(restante)
            if motivo is not None:
                motivos.append(motivo)
                registra_sin_aplicar(self._conn, restante.id, self.cycle_id_ejecutor, motivo)
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

    def aplica_reactivaciones(
        self, conn: psycopg.Connection, decisiones: list[DecisionResume], *, tick=None
    ) -> ResumenReactivacion:
        """Reactiva (resume) las pausas que el ciclo decidio reactivar, con
        TODAS las guardas del diseno: temporada en live, campana y grupo
        ENABLED, goal habilitado y no 'off', sin pausa de producto en vuelo del
        grupo, campana no topada, hoja PAUSED y fuera de «Mantener pausada»;
        cuota 'pause' y la reversa de la fila de la cola (tope de 3 en el
        ledger, que no se reinicia aunque haya otra resume). Sin cupo o con una
        guarda en contra no se toca Amazon: el ciclo siguiente lo vuelve a
        decidir. En los inputs viaja la pausa original y su cola."""
        from app import apply_cola
        from app.lecturas_historia import presupuesto_campana_hoy
        from app.optimizer import recuperacion, temporada

        fila_settings = conn.execute(
            "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"
        ).fetchone()
        settings = (fila_settings[0] if fila_settings is not None else None) or {}
        if temporada.modo_desde_settings(settings, self._platform) != "live":
            return ResumenReactivacion(omitidas=len(decisiones))
        config = temporada.desde_settings(settings)
        if config is None:
            # live sin configuracion de calendario: no se inventa una reactivacion.
            return ResumenReactivacion(omitidas=len(decisiones))
        mantener = config.mantener_pausadas
        topada = temporada.campana_topada_desde_settings(settings)
        hoy = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
        grupos_pausa = apply_cola.grupos_con_pausa_producto(conn, self._platform)
        confirmadas = fallidas = sin_quota = omitidas = 0
        for decision in decisiones:
            _tick(tick)
            fila = (
                apply_cola.fila_cola(conn, decision.inputs["queue_id"])
                if decision.inputs.get("queue_id") is not None
                else None
            )
            if fila is None or fila.ad_entity_id != decision.ad_entity_id:
                omitidas += 1
                continue
            if decision.ad_entity_id in mantener:
                omitidas += 1
                continue
            if gate_ancestros(conn, decision.ad_entity_id) is not None:
                omitidas += 1
                continue
            dummy = DecisionBid(
                id=decision.id,
                ad_entity_id=decision.ad_entity_id,
                old_value=None,
                new_value=None,
                value_currency=None,
                inputs={},
            )
            if self.modo_efectivo(conn, dummy, escalera_global="live") != "live":
                omitidas += 1
                continue
            viva = conn.execute(_SQL_ENTIDAD_VIVA, (decision.ad_entity_id,)).fetchone()
            estado = conn.execute(
                "SELECT status FROM ad_entity_state WHERE ad_entity_id = %s",
                (decision.ad_entity_id,),
            ).fetchone()
            if viva is None or not viva[1] or estado is None or estado[0] != "PAUSED":
                omitidas += 1
                continue
            grupo = conn.execute(
                "SELECT parent_id FROM ad_entity WHERE id = %s", (decision.ad_entity_id,)
            ).fetchone()
            if grupo is None or grupo[0] in grupos_pausa:
                omitidas += 1
                continue
            presupuesto = presupuesto_campana_hoy(conn, self._platform, viva[0], hoy, topada)
            if recuperacion.presupuesto_topado_de(presupuesto) is True:
                omitidas += 1
                continue
            usada, _saturada = consume_quota_y_sello(conn, self._platform, "pause")
            if not usada:
                sin_quota += 1
                continue
            if apply_cola.reversa_pause(conn, self._cliente(), fila, tick=tick):
                confirmadas += 1
            else:
                fallidas += 1
        return ResumenReactivacion(
            confirmadas=confirmadas,
            fallidas=fallidas,
            sin_quota=sin_quota,
            omitidas=omitidas,
        )

    def _ejecuta_mutacion(
        self, decision: DecisionBid, identidad: tuple[str, str], id_attempt: int
    ) -> bool | None:
        """Secuencia sellada de UNA mutacion cuyo ledger ya nacio PRE-HTTP."""
        self._conn.commit()
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
        ack = _json_seguro(resp_http)
        _tick(self._tick_fn)
        bid_leido = self._readback(cliente, identidad)
        verify_ok = bid_leido is not None and bid_leido == _bid_quantizado(decision.new_value)
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
