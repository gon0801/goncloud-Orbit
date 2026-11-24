"""Cola de cortes del aplicador (ORBIT 04, task 2.2).

La cola de CORTES (pause/negative/harvest) con ventana de veto: los bids
aplican en su ciclo SIN tocar esta cola (sellado 1). Este modulo NO importa
app.ads.write (candado de test_architecture: solo app/apply.py).

La re-validacion economica y por evidencia de `pause`/`negative` vive en
`app/apply_revalidacion.py` (Ola D3b), y sus simbolos se re-exportan desde aqui.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import psycopg
from psycopg import pq
from psycopg.types.json import Json

from app import (
    apply,
    apply_harvest,
    apply_producto,
    apply_revalidacion,
    notifica,
    proteccion_venta,
)
from app.ads.client import AdsApiError
from app.apply import (
    Aplicador,
    CapSaturado,
    consume_quota_y_sello,
    evento_cap_saturado,
)
from app.apply_cola_ejecuta import (  # noqa: F401 - re-export estable
    _SQL_CACHE_ESTADO,
    _SQL_FILA,
    _SQL_RESUMES_CICLO,
    SQL_GRUPOS_PAUSA_PRODUCTO,
    DecisionResume,
    FilaCola,
    ResumenReactivacion,
    _cliente_mutacion,
    _ejecuta_negative,
    _ejecuta_pause,
    _ejecuta_rama,
    _termina,
    aplica_reactivaciones,
    fila_cola,
    grupos_con_pausa_producto,
    resuelve_reversa_abierta,
    resumes_del_ciclo,
    reversa_negative,
    reversa_pause,
)
from app.apply_revalidacion import (  # noqa: F401 - re-export estable
    _SQL_CAMPANA_DE_GRUPO,
    _SQL_CONFIG_TARGET,
    _SQL_DECISION_INPUTS,
    _SQL_ESCRIBE_NOTAS_CICLO,
    _SQL_GRACIA,
    _SQL_HAY_GOAL,
    _SQL_INSERT_REACTIVACION,
    _SQL_LEE_NOTAS_CICLO,
    _SQL_NOTAS_CICLO_APLICADOR,
    _SQL_PADRE,
    _SQL_PAUSE_PROPIO,
    _SQL_PAUSE_TARGET_STATE,
    _TARGET_REVALIDA,
    GRACIA_REACTIVACION,
    MOTIVO_ALCANCE_CAMBIO,
    MOTIVO_CAMPANA_NO_ENABLED,
    MOTIVO_ENTIDAD_NO_VIVA,
    MOTIVO_ESPERA_PROTECCION,
    MOTIVO_ESPERA_TARGET,
    MOTIVO_GRUPO_NO_ENABLED,
    MOTIVO_MODO_NO_LIVE,
    MOTIVO_PROTECCION_APAGADA,
    MOTIVO_REACTIVACION_MANUAL,
    MOTIVO_VENDIO_EN_VENTANA,
    MOTIVO_YA_NO_CALIFICA,
    VeredictoRevalidacion,
    _alcance_cambio,
    _economia_elegida,
    _familia_respaldo,
    _flag_pause_economica,
    _gracia_activa,
    _hay_goal,
    _pause_propio_verificado,
    _peldano_congelado,
    _producto_fresco,
    _registra_evidencia,
    _settings_vigentes,
    _target_pause_vigente,
    _target_pause_vigente_detalle,
    veredicto_de_motivo,
)
from app.optimizer import cortes, productos, windows
from app.optimizer import goals as g

# Ventana de veto de 48h al encolar (sellado 2).
VENTANA_VETO = dt.timedelta(hours=48)
MOTIVO_VETO_PENDIENTE = "veto_pendiente"

_SQL_CLAVES_BLOQUEADAS = """
SELECT ad_entity_id, familia, search_term
  FROM apply_queue
 WHERE platform = %s::platform
   AND (estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
        OR (estado = 'vetoed' AND vence_el > %s))
"""

_SQL_CLAVES_RELEASED = """
SELECT ad_entity_id, familia, search_term
  FROM apply_queue
 WHERE platform = %s::platform AND estado = 'released'
"""

# Solo los cortes que todavia no tienen fila en la cola NI otro desenlace: una
# decision ya encolada (por su propio ciclo o por un ejecutor anterior)
# conserva su fila, y una que ya se cerro sin fila (choque_clave) no se
# reevalua, aunque las condiciones hayan cambiado (ARREGLOS 05 A).
_SQL_CORTES_CICLO = """
SELECT d.id, d.ad_entity_id, d.kind, d.search_term
  FROM decision d
 WHERE d.cycle_id = %s AND d.kind IN ('pause', 'negative', 'harvest')
   AND NOT EXISTS (SELECT 1 FROM apply_queue q WHERE q.decision_id = d.id)
   AND NOT EXISTS (SELECT 1 FROM decision_application da WHERE da.decision_id = d.id)
   AND NOT EXISTS (SELECT 1 FROM decision_sin_aplicar sa WHERE sa.decision_id = d.id)
 ORDER BY d.id
"""

_SQL_PLATFORM_CICLO = """
SELECT platform::text FROM optimizer_cycle WHERE id = %s
"""

_SQL_INSERT_COLA = """
INSERT INTO apply_queue (platform, ad_entity_id, kind, search_term, decision_id,
                         modo, estado, vence_el, request_payload)
VALUES (%s::platform, %s, %s, %s, %s, %s, 'pending_veto', %s, %s)
RETURNING id
"""

_SQL_VENCIDAS = """
SELECT id, kind, ad_entity_id, search_term, decision_id, request_payload, estado
  FROM apply_queue
 WHERE platform = %s::platform AND modo = 'live'
   AND ((estado = 'pending_veto' AND vence_el <= %s) OR estado = 'released')
 ORDER BY encolado_at, id
"""

_SQL_LIBERA = """
UPDATE apply_queue SET estado = 'released', released_at = now()
 WHERE id = %s AND estado = 'pending_veto'
RETURNING id
"""

_SQL_CLAIM = """
UPDATE apply_queue SET estado = 'applying', applying_at = now()
 WHERE id = %s AND estado = 'released'
RETURNING id
"""

_SQL_DESCARTA = """
UPDATE apply_queue SET estado = 'discarded', discarded_at = now(), discard_motivo = %s
 WHERE id = %s AND estado = %s
RETURNING id
"""

_SQL_DESCARTA_ESPERAS = """
UPDATE apply_queue q
   SET estado = 'discarded', discarded_at = now(), discard_motivo = %s
  FROM ad_entity e
 WHERE e.id = q.ad_entity_id AND e.platform = %s::platform
   AND q.estado = 'released' AND q.modo = 'live' AND q.kind = 'pause'
   AND (SELECT s.motivo
          FROM decision_sin_aplicar s
         WHERE s.decision_id = q.decision_id
           AND s.motivo NOT IN ('sin_respuesta', 'espera_target')
         ORDER BY s.cycle_id DESC
         LIMIT 1) = 'espera_proteccion'
RETURNING q.id
"""

_SQL_EXTERNALES_GRUPO = """
SELECT grp.external_id, cam.external_id
  FROM ad_entity grp
  JOIN ad_entity cam ON cam.id = grp.parent_id AND cam.kind = 'campaign'
 WHERE grp.id = %s
"""

_SQL_ANUNCIO_VIVO = """
SELECT ag.parent_id AS campaign_id, (s.ad_entity_id IS NOT NULL) AS tiene_state
  FROM ad_entity pa
  JOIN ad_entity ag ON ag.id = pa.parent_id AND ag.kind = 'ad_group'
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = pa.id
 WHERE pa.id = %s AND pa.kind = 'product_ad'
"""

_SQL_CANDADO_PAUSA_PRODUCTO = (
    "SELECT pg_advisory_xact_lock_shared(hashtext('auto03-pausa-' || %s::text))"
)

_SQL_GRUPO_VIVO = """
SELECT e.parent_id AS campaign_id, (s.ad_entity_id IS NOT NULL) AS tiene_state
  FROM ad_entity e
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = e.id
 WHERE e.id = %s AND e.kind = 'ad_group'
"""


@dataclass(frozen=True)
class ResumenEncolado:
    encoladas_live: int
    encoladas_shadow: int
    choques: list[str]
    avisos: tuple[notifica.CorteEncolado, ...] = ()


@dataclass(frozen=True)
class ResultadoLiberacion:
    liberadas: int
    aplicadas: int
    fallidas: int
    descartadas: list[str]
    sin_quota: int
    carreras_perdidas: int
    revalida_sin_respuesta: int = 0
    espera_target: int = 0
    espera_proteccion: int = 0
    alertas: tuple[apply_harvest.AlertaHarvest, ...] = ()
    caps_saturados: tuple[CapSaturado, ...] = ()
    revalidaciones_economicas: tuple[dict, ...] = ()


def claves_bloqueadas(
    conn: psycopg.Connection, platform: str, ahora: dt.datetime
) -> set[tuple[int, str, str | None]]:
    """Claves de efecto con fila NO terminal en-vuelo O fila vetoed vigente."""
    return {
        (fila[0], fila[1], fila[2])
        for fila in conn.execute(_SQL_CLAVES_BLOQUEADAS, (platform, ahora)).fetchall()
    }


def claves_released(
    conn: psycopg.Connection, platform: str
) -> frozenset[tuple[int, str, str | None]]:
    """Claves con una fila released (ORBIT 07 M1)."""
    return frozenset(
        (fila[0], fila[1], fila[2])
        for fila in conn.execute(_SQL_CLAVES_RELEASED, (platform,)).fetchall()
    )


def skip_por_clave(
    conn: psycopg.Connection,
    platform: str,
    claves,
    ahora: dt.datetime,
) -> set[tuple[int, str, str | None]]:
    """Subconjunto bloqueado de los candidatos."""
    return set(claves) & claves_bloqueadas(conn, platform, ahora)


def _meet_goals(conn: psycopg.Connection, platform: str, campaign_id, escalera_global: str) -> str:
    """Meet escalera + goal (precedencia campana > plataforma)."""
    goal_campana: g.Goal | None = None
    goal_plataforma: g.Goal | None = None
    for f in conn.execute(apply._SQL_GOALS_ENTIDAD, (platform, campaign_id)).fetchall():
        goal = g.Goal(*f)
        if goal.scope == "campaign":
            goal_campana = goal
        else:
            goal_plataforma = goal
    resuelto = g.resuelve_goal(goal_campana, goal_plataforma)
    if resuelto is None or not resuelto.enabled:
        return "shadow"
    return g.modo_efectivo(escalera_global, resuelto.mode)


def _modo_efectivo_corte(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    ad_entity_id: int,
    *,
    escalera_global: str,
) -> str:
    """Re-resuelve el modo POR DECISION para un corte."""
    identidad = apply._identidad(conn, ad_entity_id)
    if identidad is None:
        return "shadow"
    if identidad[0] in ("keyword", "product_target"):
        return aplicador.modo_efectivo(conn, ad_entity_id, escalera_global=escalera_global)
    if identidad[0] == "product_ad":
        return _modo_pausa_producto(conn, platform, ad_entity_id, escalera_global)
    fila = conn.execute(_SQL_GRUPO_VIVO, (ad_entity_id,)).fetchone()
    if fila is None or not fila[1]:
        return "shadow"
    return _meet_goals(conn, platform, fila[0], escalera_global)


def _modo_pausa_producto(
    conn: psycopg.Connection, platform: str, ad_entity_id: int, escalera_global: str
) -> str:
    """AUTO-03 H.4: modo live para pausa de product_ad."""
    fila = conn.execute(_SQL_ANUNCIO_VIVO, (ad_entity_id,)).fetchone()
    if fila is None or not fila[1]:
        return "shadow"
    if _meet_goals(conn, platform, fila[0], escalera_global) != "live":
        return "shadow"
    vigente = conn.execute(_SQL_CONFIG_TARGET).fetchone()
    settings = vigente[0] if vigente is not None else {}
    if platform not in productos.PLATAFORMAS:
        return "shadow"
    return (
        "live" if productos.modos_desde_settings(settings, platform).pausa == "live" else "shadow"
    )


def _payload_pause(kind_entidad: str, external_id: str) -> dict:
    campo = "keywordId" if kind_entidad == "keyword" else "targetId"
    return {campo: external_id, "state": apply.ESTADO_PUT_PAUSED}


def _payload_term(grupo_ext: str, campana_ext: str, term: str) -> dict:
    return {
        "adGroupId": grupo_ext,
        "campaignId": campana_ext,
        "keywordText": term,
        "matchType": "NEGATIVE_EXACT",
        "state": "ENABLED",
    }


def encola_cortes(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    cycle_id: int,
    *,
    modo_envelope: str,
    ahora: dt.datetime,
) -> ResumenEncolado:
    """Encola TODAS las decisiones de corte del ciclo (pause/negative/harvest)."""
    platform = conn.execute(_SQL_PLATFORM_CICLO, (cycle_id,)).fetchone()[0]
    live = 0
    shadow = 0
    choques: list[str] = []
    avisos: list[notifica.CorteEncolado] = []
    candado_tomado = False
    for dec_id, entidad, kind, term in conn.execute(_SQL_CORTES_CICLO, (cycle_id,)).fetchall():
        identidad = apply._identidad(conn, entidad)
        es_pausa_producto = (
            kind == "pause" and identidad is not None and identidad[0] == "product_ad"
        )
        if es_pausa_producto and not candado_tomado:
            conn.execute(_SQL_CANDADO_PAUSA_PRODUCTO, (platform,))
            candado_tomado = True
        modo = _modo_efectivo_corte(
            conn, aplicador, platform, entidad, escalera_global=modo_envelope
        )
        if (
            kind == "pause"
            and identidad is not None
            and identidad[0]
            in (
                "keyword",
                "product_target",
            )
        ):
            payload = _payload_pause(identidad[0], identidad[1])
        elif es_pausa_producto:
            payload = {"adId": identidad[1], "state": apply.ESTADO_PUT_PAUSED}
        elif kind == "pause":
            payload = {}
        else:
            fila = conn.execute(_SQL_EXTERNALES_GRUPO, (entidad,)).fetchone()
            payload = _payload_term(fila[0], fila[1], term) if fila is not None else {}
        vence = ahora + VENTANA_VETO
        try:
            with conn.transaction():
                conn.execute(
                    _SQL_INSERT_COLA,
                    (
                        platform,
                        entidad,
                        kind,
                        term,
                        dec_id,
                        modo,
                        vence,
                        Json(payload),
                    ),
                )
            if modo == "live":
                live += 1
            else:
                shadow += 1
            avisos.append(
                notifica.CorteEncolado(
                    platform=platform,
                    kind=kind,
                    search_term=term,
                    vence_el=vence,
                    modo=modo,
                )
            )
        except psycopg.errors.UniqueViolation:
            if modo_envelope == "live":
                apply.registra_sin_aplicar(
                    conn,
                    dec_id,
                    cycle_id,
                    apply.MOTIVO_CHOQUE_CLAVE,
                    detalle={"kind": kind, "entidad": entidad, "termino": term},
                )
            choques.append(
                f"decision {dec_id}: clave de efecto en vuelo"
                f" (entidad {entidad}, kind {kind}, termino {term!r})"
            )
    return ResumenEncolado(
        encoladas_live=live, encoladas_shadow=shadow, choques=choques, avisos=tuple(avisos)
    )


def _revalida_pause(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    fila: FilaCola,
    ahora: dt.datetime,
    evidencias: list[dict] | None = None,
    proteccion_cache: proteccion_venta.CacheProteccion | None = None,
    *,
    evidencia_grupos: dict | None = None,
    temporada_ctx=None,
) -> str | None:
    """Wrapper estable sobre `apply_revalidacion.revalida_pause` (respeta parches del modulo)."""
    return apply_revalidacion.revalida_pause(
        conn,
        aplicador,
        platform,
        fila,
        ahora,
        evidencias,
        proteccion_cache,
        evidencia_grupos=evidencia_grupos,
        temporada_ctx=temporada_ctx,
        gracia_activa_fn=_gracia_activa,
        pause_propio_fn=_pause_propio_verificado,
        flag_economica_fn=_flag_pause_economica,
        target_vigente_fn=_target_pause_vigente,
        hay_goal_fn=_hay_goal,
    )


def _revalida_negative(
    conn: psycopg.Connection,
    platform: str,
    fila: FilaCola,
    ahora: dt.datetime,
    *,
    evidencia_grupos: dict | None = None,
    temporada_neg=None,
) -> str | None:
    return apply_revalidacion.revalida_negative(
        conn,
        platform,
        fila,
        ahora,
        evidencia_grupos=evidencia_grupos,
        temporada_neg=temporada_neg,
    )


def _revalida_ancestros(conn: psycopg.Connection, fila: FilaCola) -> str | None:
    return apply.gate_ancestros(conn, fila.ad_entity_id)


def _revalida(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    fila: FilaCola,
    ahora: dt.datetime,
    evidencias: list[dict] | None = None,
    proteccion_cache: proteccion_venta.CacheProteccion | None = None,
    evidencia_grupos: dict | None = None,
    temporada_ctx=None,
    temporada_neg=None,
) -> str | None:
    motivo = _revalida_ancestros(conn, fila)
    if motivo is not None:
        return motivo
    rama = _rama(conn, fila)
    if rama == "pause_hoja":
        return _revalida_pause(
            conn,
            aplicador,
            platform,
            fila,
            ahora,
            evidencias,
            proteccion_cache,
            evidencia_grupos=evidencia_grupos,
            temporada_ctx=temporada_ctx,
        )
    if rama == "pause_producto":
        return apply_producto.revalida_pause_producto(conn, aplicador, fila, ahora)
    if rama == "negative":
        return _revalida_negative(
            conn,
            platform,
            fila,
            ahora,
            evidencia_grupos=evidencia_grupos,
            temporada_neg=temporada_neg,
        )
    return apply_harvest.revalida_harvest(conn, platform, fila, ahora)


def _rama(conn: psycopg.Connection, fila: FilaCola) -> str:
    if fila.kind == "pause":
        identidad = apply._identidad(conn, fila.ad_entity_id)
        if identidad is not None and identidad[0] in apply._KINDS_DECISORAS:
            return "pause_hoja"
        if identidad is not None and identidad[0] == "product_ad":
            return "pause_producto"
    elif fila.kind in ("negative", "harvest"):
        return fila.kind
    raise ValueError(
        f"apply_queue {fila.id}: sin rama para {fila.kind} sobre la entidad {fila.ad_entity_id}"
    )


def descarta_esperas_proteccion(conn: psycopg.Connection, platform: str) -> int:
    filas = conn.execute(_SQL_DESCARTA_ESPERAS, (MOTIVO_PROTECCION_APAGADA, platform)).fetchall()
    return len(filas)


def _descartes_al_apagar_proteccion(
    conn: psycopg.Connection, platform: str, proteccion_activa: bool
) -> list[str]:
    if proteccion_activa:
        return []
    return [MOTIVO_PROTECCION_APAGADA] * descarta_esperas_proteccion(conn, platform)


def _procesa_harvest_liberada(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    fila: FilaCola,
    alertas_harvest: list[apply_harvest.AlertaHarvest],
    caps_saturados: list[CapSaturado],
    registra,
) -> tuple[int, int, int, int]:
    """Ejecuta el hook harvest y devuelve deltas `(aplicadas, fallidas, carreras, sin_quota)`."""
    resultado_h = apply_harvest.aplica_harvest(conn, aplicador, fila, platform=platform)
    if resultado_h.alerta is not None:
        alertas_harvest.append(resultado_h.alerta)
    caps_saturados.extend(resultado_h.caps_saturados)
    if resultado_h.estado == "applied":
        return (1, 0, 0, 0)
    if resultado_h.estado == "failed":
        return (0, 1, 0, 0)
    if resultado_h.estado == "perdida":
        return (0, 0, 1, 0)
    registra(fila, apply.MOTIVO_SIN_QUOTA)
    return (0, 0, 0, 1)


def _aisla_fila(conn: psycopg.Connection) -> None:
    """Cierra lo que dejo abierto la fila de la cola que fallo (ARREGLOS 05
    A.3): lo durable (`tx_durable`, commits del ledger) queda; lo escrito a
    medias se descarta. El barrido sigue con la fila siguiente."""
    if not conn.autocommit and conn.info.transaction_status != pq.TransactionStatus.IDLE:
        conn.rollback()


def _aisla_corte(conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola, rama: str) -> str:
    """Ejecuta UN corte ya reclamado. Un fallo remoto de esa fila
    (`AdsApiError`: 5xx, red o lectura ilegible) cuenta como fallido y la deja
    como esta, `applying` con su ledger sin sello: es el rastro que la
    conciliacion del ciclo siguiente resuelve con una lectura. Los fallos
    globales (claim perdido, base, identidad) siguen propagando."""
    try:
        return _ejecuta_rama(conn, aplicador, fila, rama)
    except AdsApiError as exc:
        if apply.es_rechazo_de_identidad(exc):
            raise
        _aisla_fila(conn)
        return "failed"


def _aisla_harvest(
    conn: psycopg.Connection,
    aplicador: Aplicador,
    platform: str,
    fila: FilaCola,
    alertas_harvest: list[apply_harvest.AlertaHarvest],
    caps_saturados: list[CapSaturado],
    registra,
) -> tuple[int, int, int, int]:
    """`_procesa_harvest_liberada` con el mismo aislamiento por fila que
    `_aisla_corte`: un fallo remoto del job cuenta como fallido."""
    try:
        return _procesa_harvest_liberada(
            conn, aplicador, platform, fila, alertas_harvest, caps_saturados, registra
        )
    except AdsApiError as exc:
        if apply.es_rechazo_de_identidad(exc):
            raise
        _aisla_fila(conn)
        return (0, 1, 0, 0)


def libera_vencidos(
    conn: psycopg.Connection,
    platform: str,
    *,
    ahora: dt.datetime,
    aplicador: Aplicador,
    cycle_id: int | None = None,
    proteccion_activa: bool = False,
) -> ResultadoLiberacion:
    """Barrido FIFO de filas vencidas y released en modo live.

    Aislamiento por fila (ARREGLOS 05 A.3): un fallo remoto de UN corte
    (`AdsApiError`: 5xx, red o lectura ilegible al ejecutarlo) cuenta como
    fallido y lo deja como esta, `applying` con su ledger sin sello, que es
    el rastro que la conciliacion del ciclo siguiente resuelve con una
    lectura. Los fallos globales (claim perdido, base indisponible, identidad
    o permisos invalidos) siguen propagando y detienen el barrido.

    Precalcula `windows.ventanas_evidencia_ad_group` a lo sumo UNA vez por
    barrido para las filas `pause`/`negative` (lazy en la primera que llega a
    revalidacion), evitando re-agregar 80 dias de metricas por cada corte
    `pause`/`negative`. Las filas `harvest` siguen delegando en
    `apply_harvest.revalida_harvest`.
    """
    descartadas = _descartes_al_apagar_proteccion(conn, platform, proteccion_activa)
    filas = [FilaCola(*f) for f in conn.execute(_SQL_VENCIDAS, (platform, ahora)).fetchall()]
    proteccion_cache = (
        proteccion_venta.lee_cache(conn, platform, ahora) if proteccion_activa else None
    )
    evidencia_grupos: dict | None = None
    hoy_utc = ahora.astimezone(dt.UTC).date() if ahora.tzinfo is not None else ahora.date()
    sin_cargar = object()
    temporada_ctx = sin_cargar
    temporada_neg = sin_cargar

    def _registra(fila: FilaCola, motivo: str) -> None:
        if cycle_id is not None and fila.decision_id is not None:
            apply.registra_sin_aplicar(
                conn, fila.decision_id, cycle_id, motivo, detalle={"kind": fila.kind}
            )

    liberadas = aplicadas = fallidas = sin_quota = carreras = sin_respuesta = 0
    espera_target = espera_proteccion = 0
    alertas_harvest: list[apply_harvest.AlertaHarvest] = []
    caps_saturados: list[CapSaturado] = []
    revalidaciones_economicas: list[dict] = []
    for fila in filas:
        if fila.estado == "pending_veto":
            if conn.execute(_SQL_LIBERA, (fila.id,)).fetchone() is None:
                carreras += 1
                continue
            liberadas += 1
        if (
            _modo_efectivo_corte(
                conn, aplicador, platform, fila.ad_entity_id, escalera_global="live"
            )
            != "live"
        ):
            conn.execute(_SQL_DESCARTA, (MOTIVO_MODO_NO_LIVE, fila.id, "released"))
            descartadas.append(MOTIVO_MODO_NO_LIVE)
            continue
        if evidencia_grupos is None and fila.kind in ("pause", "negative"):
            evidencia_grupos = windows.ventanas_evidencia_ad_group(conn, platform, ahora)
        if fila.kind == "pause" and temporada_ctx is sin_cargar:
            temporada_ctx = apply_revalidacion.temporada_io.lee_contexto_revalidacion(
                conn, platform, hoy_utc
            )
        if fila.kind == "negative" and temporada_neg is sin_cargar:
            temporada_neg = (
                apply_revalidacion.temporada_io.mult_negativa(conn, platform, hoy_utc),
                apply_revalidacion.temporada_io.cerca_plataforma(conn, platform, hoy_utc),
            )
        try:
            motivo = _revalida(
                conn,
                aplicador,
                platform,
                fila,
                ahora,
                revalidaciones_economicas,
                proteccion_cache,
                evidencia_grupos,
                None if temporada_ctx is sin_cargar else temporada_ctx,
                None if temporada_neg is sin_cargar else temporada_neg,
            )
        except AdsApiError as exc:
            if apply.es_rechazo_de_identidad(exc):
                raise
            sin_respuesta += 1
            _registra(fila, apply.MOTIVO_SIN_RESPUESTA)
            continue
        veredicto = veredicto_de_motivo(motivo)
        if veredicto.accion == "esperar":
            assert veredicto.motivo is not None
            if veredicto.motivo == MOTIVO_ESPERA_TARGET:
                espera_target += 1
            else:
                espera_proteccion += 1
            _registra(fila, veredicto.motivo)
            continue
        if veredicto.accion == "descartar":
            assert veredicto.motivo is not None
            conn.execute(_SQL_DESCARTA, (veredicto.motivo, fila.id, "released"))
            descartadas.append(veredicto.motivo)
            continue
        if fila.kind == "harvest":
            da, df, dc, dq = _aisla_harvest(
                conn, aplicador, platform, fila, alertas_harvest, caps_saturados, _registra
            )
            aplicadas += da
            fallidas += df
            carreras += dc
            sin_quota += dq
            continue
        rama = _rama(conn, fila)
        usada, saturada = consume_quota_y_sello(conn, platform, fila.kind)
        if not usada:
            sin_quota += 1
            _registra(fila, apply.MOTIVO_SIN_QUOTA)
            continue
        if saturada:
            evento = evento_cap_saturado(conn, platform, fila.kind)
            if evento is not None:
                caps_saturados.append(evento)
        if conn.execute(_SQL_CLAIM, (fila.id,)).fetchone() is None:
            carreras += 1
            continue
        ok = _aisla_corte(conn, aplicador, fila, rama)
        if ok == "applied":
            aplicadas += 1
        elif ok == "failed":
            fallidas += 1
    return ResultadoLiberacion(
        liberadas=liberadas,
        aplicadas=aplicadas,
        fallidas=fallidas,
        descartadas=descartadas,
        sin_quota=sin_quota,
        carreras_perdidas=carreras,
        revalida_sin_respuesta=sin_respuesta,
        espera_target=espera_target,
        espera_proteccion=espera_proteccion,
        alertas=tuple(alertas_harvest),
        caps_saturados=tuple(caps_saturados),
        revalidaciones_economicas=tuple(revalidaciones_economicas),
    )


_ = cortes  # re-exportado para parches de tests sobre `apply_cola.cortes`
