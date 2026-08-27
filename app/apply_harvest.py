"""Harvest real: job al liberar, reconciliacion viva y reversas (ORBIT 04, 2.3).

La EJECUCION del corte harvest (sellados 13, 14 y 12-reversas; APPLY.md
secciones 6-7). La COLA manda: harvest_job NACE AL LIBERAR (primer paso
del apply, JAMAS al decidir) y este modulo lo conduce por la cadena
pending -> negative_created -> exact_created -> done|failed (triggers de
0001/0002). NO importa app.ads.write (candado de test_architecture): el
write client llega via el Aplicador (`aplicador._cliente()`).

Cadena sellada de UN job (matriz §6.1): (1) POST del negative EXACT en el
ORIGEN → negative_created; (2) bid sugerido (regla 8) clampeado [floor,
ceiling] → INTENCION con el bid EFECTIVO en el ledger PRE-POST (sellado
14); (3) POST de la keyword EXACT en el destino del goal → exact_created;
(4) readback por LISTA con IDENTIDAD COMPLETA → done. Fallo DEFINITIVO
(>=400) en negative_created → REVERSA AUTOMATICA + failed + ALERTA
ESTRUCTURADA (AlertaHarvest; 3.3 la consume). En exact_created sin keyword
en destino → reversa completa (§7: keyword PRIMERO). 5xx/ambiguo SUBE:
ledger sin sello, la fila ES el rastro.

QUOTA (sellado 8): harvest = 1 OPERACION LOGICA (2 HTTPs, UNA unidad),
declarada en el PRIMER intento del ledger; el 2do HTTP lleva
quota_cobrada=false. Reversas EXENTAS.

Elecciones DECLARADAS de esta task:

- El hook `aplica_harvest` VIVE AQUI (2.2 dejo el stub en la cola): la
  cola lo llama tras la re-validacion y este modulo ejecuta TODO (job →
  quota → claim → ledger → HTTPs). El SQL del claim/terminacion se ESPEJA
  de apply_cola: el ciclo de imports lo impide; la maquina la sella el
  trigger.
- Quota = asunto del CALLER: la liberacion cobra antes del claim; la
  reconciliacion cobra SOLO la primera vez (fila released sin ledger) y
  reclama la fila antes de cualquier HTTP (el veto puede ganar el claim).
  El applying huerfano CONSERVA su cobro: jamas se recobra.
- La reconciliacion CIERRA (failed) los jobs cuya fila de cola murio
  vetoed/discarded: un job eterno bloquearia la clave del termino para
  siempre (el evil del sellado 13, en la ventana ancha de "released
  esperando quota SIGUE vetable").
- `applied_cycle_id` tardio (por reconciliacion) = ciclo EJECUTOR que
  reconcilia (pendiente del brief §13 fijado aqui con test: sus
  reintentos son mutaciones que ese ciclo corre).
- La continuacion cuelga TODO del decision_id del JOB (la clave de efecto
  de la cola garantiza que es el mismo corte).
- Los request payloads de los /list van VACIOS (payload exacto = unknown #4 del
  brief; la respuesta si esta verificada, log
  out/regla8-negkeywords.log); identidad filtrada cliente-side con tope
  de paginacion.

SELLADO por el probe 2.5 (corrida autorizada del dueno 2026-08-26, ledger
apply_attempt ids 1-20, log out/smoke-apply-20260826.log): acks 207 con
success/error anidados por recurso (_id_de_ack ya los parsea), matchType del
wire NEGATIVE_EXACT/EXACT, el "delete" v3 ARCHIVA (state=ARCHIVED en el list:
operativamente AUSENTE para la identidad viva) y el estado del readback vive
por LIST con vocabulario UPPER (apply.ESTADO_WIRE_* — el GET directo de
entidad esta retirado, 403).

Ronda de CROSS-REVIEW del dueno (codex+grok+qwen, ORBIT 04 P2): el tope-3
del ledger cuenta SOLO intentos 'normal' (las reversas son el mecanismo de
seguridad, CX1/GK1); el nacimiento del job absorbe el choque del unico
parcial con SAVEPOINT para no abortar la transaccion de produccion (CX2);
la reversa completa JAMAS borra el negativo si la keyword no se borro
(CX3); las fases avanzan SOLO con ids REALES del ack o de la evidencia viva
— fail-closed: el termino no se cosecha sin cortarse en origen (GK2), con
_id_de_ack parseando tambien el shape 207 anidado; y el barrido de
reconciliacion cierra las filas harvest applying cuyo job ya no vive (GK4).

Ronda de CROSS-REVIEW de shapes (codex+qwen, out/cross-review-shapes-*.log):
el 207 NO es exito automatico — el reintento de negatives de la
reconciliacion exige sin-error[] + id de success[] (CX2), los deletes de
reversa exigen sin rechazo por-item con el id del objeto borrado (CX3,
'fallo:reversa_rechazada' con el cuerpo) y el senuelo exige OTRO ad group
VIVO (_solo_en_otro_ad_group, CX4: un ARCHIVED del propio grupo reintenta);
el estado del readback pagina por nextToken (CX1/QW1,
apply._estado_de_readback).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import psycopg
from psycopg.types.json import Json

from app import apply, notifica
from app.ads.client import AdsApiError, AdsClientError
from app.optimizer import cortes, hygiene, windows
from app.optimizer import goals as g

if TYPE_CHECKING:
    from app.apply import Aplicador
    from app.apply_cola import FilaCola

logger = logging.getLogger(__name__)

# PENDIENTE-DE-REGLA-8 (lead, 2026-08-25; log out/regla8-bidrec.log): el
# endpoint de BID SUGERIDO quedo NO pineado — v2 retirado (405/404), v3
# responde 403 que exige firma AWS SigV4 (fuera del cliente LWA). El path
# vive como CONSTANTE hasta que la regla 8 en vivo fije path/metodo/shape;
# cualquier error → None SILENCIOSO (fail-open al default sellado, regla 3;
# es una LECTURA: sin ledger, solo logging debug).
PATH_BID_SUGERIDO = "/sp/keywords/bidRecommendations"

# Contenedor de la respuesta por path de list. VERIFICADOS en vivo:
# negativeKeywords (regla 8, 2026-08-25, log out/regla8-negkeywords.log) y
# keywords (probe 2.5, 2026-08-26, apply_attempt 16-17: readback por LIST de
# la keyword creada y ausencia tras el delete-archiva).
_CONTENEDORES_LIST = {
    "/sp/keywords/list": "keywords",
    "/sp/negativeKeywords/list": "negativeKeywords",
}
# Tope de paginaciones por lista: lo leido basta y el ciclo no se cuelga.
TOPE_PAGINAS_LIST = 20

# Vocabulario CERRADO de motivos de alerta de harvest (3.3 los consume tal
# cual; los tests lo fijan literal).
MOTIVO_SIN_CONFIG = "harvest_sin_config"
MOTIVO_ENTIDAD_INCOMPLETA = "entidad_incompleta"
MOTIVO_BID_DEFAULT_FALTANTE = "bid_default_faltante"
MOTIVO_MONEDA_INCOHERENTE = "moneda_incoherente"
MOTIVO_FALLO_NEGATIVE = "fallo_negative"
MOTIVO_FALLO_KEYWORD = "fallo_keyword"
MOTIVO_KEYWORD_AUSENTE = "keyword_ausente"
MOTIVO_TOPE_INTENTOS = "tope_intentos"

# ---------------------------------------------------------------------------
# SQL del modulo (misma maquina de estados que apply_cola; ver docstring)
# ---------------------------------------------------------------------------

_SQL_INSERT_JOB = """
INSERT INTO harvest_job (decision_id, search_term, platform, ad_entity_id, fase)
VALUES (%s, %s, %s::platform, %s, 'pending')
RETURNING id, decision_id, search_term, ad_entity_id, fase, external_ids, platform::text
"""

_SQL_JOB_EXISTENTE = """
SELECT id, decision_id, search_term, ad_entity_id, fase, external_ids, platform::text
  FROM harvest_job
 WHERE platform = %s::platform AND ad_entity_id = %s AND search_term = %s
   AND fase IN ('pending', 'negative_created', 'exact_created')
"""

_SQL_JOBS_EN_VUELO = """
SELECT id, decision_id, search_term, ad_entity_id, fase, external_ids, platform::text
  FROM harvest_job
 WHERE platform = %s::platform
   AND fase IN ('pending', 'negative_created', 'exact_created')
 ORDER BY id
"""

_SQL_AVANZA_FASE = """
UPDATE harvest_job SET fase = %s, external_ids = %s, updated_at = now() WHERE id = %s
"""

_SQL_UPDATE_IDS = """
UPDATE harvest_job SET external_ids = %s, updated_at = now() WHERE id = %s
"""

_SQL_JOB_FAILED = """
UPDATE harvest_job SET fase = 'failed', updated_at = now() WHERE id = %s
"""

# Espejo de apply_cola._SQL_CLAIM (ver docstring: el ciclo de imports obliga).
_SQL_CLAIM = """
UPDATE apply_queue SET estado = 'applying', applying_at = now()
 WHERE id = %s AND estado = 'released'
RETURNING id
"""

_SQL_EXTERNALES = """
SELECT grp.external_id, cam.external_id
  FROM ad_entity grp
  JOIN ad_entity cam ON cam.id = grp.parent_id AND cam.kind = 'campaign'
 WHERE grp.id = %s
"""

_SQL_PADRE = """
SELECT parent_id FROM ad_entity WHERE id = %s
"""

_SQL_DECISION = """
SELECT new_value, value_currency FROM decision WHERE id = %s
"""

_SQL_COLA_DE = """
SELECT id, estado FROM apply_queue WHERE decision_id = %s ORDER BY id DESC LIMIT 1
"""

_SQL_NEGATIVAS_APLICANDO = """
SELECT id, ad_entity_id, search_term, decision_id
  FROM apply_queue
 WHERE platform = %s::platform AND estado = 'applying' AND kind = 'negative'
 ORDER BY id
"""

_SQL_SELLA_PENDIENTES = """
UPDATE apply_attempt SET resultado = %s, finished_at = now()
 WHERE decision_id = %s AND finished_at IS NULL AND resultado IS NULL
"""

# GK4 (cross-review del dueno): red de seguridad — filas apply_queue applying
# de kind harvest SIN job en vuelo que las conduzca (p.ej. un cierre de job
# que dejo la cola viva). Sin este barrido la clave term_cut quedaria
# bloqueada para siempre (applying eterno, no terminal).
_SQL_HARVEST_APLICANDO = """
SELECT id, decision_id
  FROM apply_queue
 WHERE platform = %s::platform AND estado = 'applying' AND kind = 'harvest'
 ORDER BY id
"""

_SQL_JOB_EN_VUELO_DE = """
SELECT EXISTS (
    SELECT 1 FROM harvest_job
     WHERE decision_id = %s AND fase IN ('pending', 'negative_created', 'exact_created')
)
"""

# ADV-03 (review adversaria, matriz §6.1 fila faltante): pausas applying
# huerfanas (fallo ambiguo 5xx/red en el PUT). Espejos de apply_cola (el
# ciclo de imports apply_cola -> apply_harvest obliga a NO importarlo):
# seleccion, lectura del estado del readback, gracia y cache.
_SQL_PAUSES_APLICANDO = """
SELECT id, ad_entity_id, decision_id
  FROM apply_queue
 WHERE platform = %s::platform AND estado = 'applying' AND kind = 'pause'
 ORDER BY id
"""

_SQL_PAUSE_PROPIO = """
SELECT EXISTS (
    SELECT 1
      FROM decision_application da
      JOIN decision d ON d.id = da.decision_id
     WHERE d.ad_entity_id = %s AND d.kind = 'pause' AND da.verify_ok IS TRUE
)
"""

_SQL_INSERT_REACTIVACION = """
INSERT INTO reactivacion_manual (ad_entity_id) VALUES (%s) ON CONFLICT DO NOTHING
"""

_SQL_CACHE_ESTADO = """
UPDATE ad_entity_state SET status = %s, synced_at = now() WHERE ad_entity_id = %s
"""

# ADV-05: constantes de firma de la re-decision (mismo trato declarado que
# apply_cola._TARGET/_FLOOR/_CEILING_REVALIDA: JAMAS se persisten, regla 3).
_TARGET_REVALIDA = Decimal("100")
_FLOOR_REVALIDA = Decimal("0.01")
_CEILING_REVALIDA = Decimal("10000")

# ---------------------------------------------------------------------------
# Estructuras de salida
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertaHarvest:
    """SENAL estructurada de fallo (sellado 13): 3.3/notifica la consume;
    este modulo solo la produce. `detalle` = la evidencia. `envio_fallido`
    (3.3, sellados 2/19): True cuando el aviso por Telegram NO salio — la
    bandera viaja con la alerta hasta el ciclo, que la convierte en la NOTA
    notes['telegram'] (la unica visibilidad del fallo del canal)."""

    motivo: str
    decision_id: int
    search_term: str
    plataforma: str
    job_id: int | None
    detalle: str
    envio_fallido: bool = False


@dataclass(frozen=True)
class ResultadoHarvest:
    """Estado en {applied, failed, sin_quota, perdida} (perdida = el claim
    vio 0 filas: un veto gano la carrera)."""

    estado: str
    alerta: AlertaHarvest | None = None


@dataclass(frozen=True)
class ResumenReconciliacion:
    """Barrido de reconciliacion (2.2/2.4 la invocan). `jobs_cerrados_por_cola`
    = jobs de filas muertas vetoed/discarded (la cola manda). Desde la review
    adversaria (ADV-03) tambien reporta las pausas applying huerfanas
    resueltas por LIST fresco (matriz §6.1); desde la cross-review del dueno
    (GK4) reporta ADEMAS las filas harvest applying cerradas por el barrido
    de seguridad (sin job en vuelo que las conduzca)."""

    jobs_done: int
    jobs_failed: int
    negativas_confirmadas: int
    negativas_fallidas: int
    jobs_cerrados_por_cola: int
    alertas: tuple[AlertaHarvest, ...]
    pausas_confirmadas: int = 0
    pausas_fallidas: int = 0
    harvest_huerfanas_cerradas: int = 0


@dataclass
class _Job:
    """Espejo de harvest_job (mutable: las fases avanzan in-place)."""

    id: int
    decision_id: int
    search_term: str
    ad_entity_id: int
    fase: str
    external_ids: dict
    plataforma: str


@dataclass(frozen=True)
class _Contexto:
    """Ejecucion FRESCA de la base: origen externo, destino del goal,
    default congelado en decision.new_value (sellado 14) y su moneda."""

    plataforma: str
    grupo_ext: str
    campana_ext: str
    destino_grupo: str
    destino_campana: str
    default_bid: Decimal
    moneda: str
    floor: Decimal
    ceiling: Decimal


# ---------------------------------------------------------------------------
# Bid sugerido (regla 8) y clampeo sellado
# ---------------------------------------------------------------------------


def bid_sugerido(cliente, keyword_id: str | int | None = None) -> Decimal | None:
    """La sugerencia de bid de Amazon para una keyword, o None.

    PENDIENTE-DE-REGLA-8 (log out/regla8-bidrec.log): path NO pineado (v2
    retirado, v3 exige SigV4); se consulta por GET sellado a la coleccion
    (sin keyword_id — el termino cosechado NO tiene keyword propia antes de
    nacer) o al item. CUALQUIER error (403/404/5xx/red/body ilegible) →
    None SILENCIOSAMENTE: fail-open AL DEFAULT sellado (regla 3). Es una
    LECTURA: sin fila de ledger, sin quota; el intento queda en logging
    debug."""
    path = PATH_BID_SUGERIDO if keyword_id is None else f"{PATH_BID_SUGERIDO}/{keyword_id}"
    try:
        resp = cliente.get_sellado(path)
        data = resp.json()
    except (AdsClientError, httpx.HTTPError, ValueError):
        logger.debug("bid sugerido no disponible (fail-open al default sellado): %s", path)
        return None
    return _sugerido_de(data)


def _sugerido_de(data: object) -> Decimal | None:
    """El bid de la respuesta, probando los shapes candidatos (el real lo
    fija la regla 8 / probe 2.5). None = sin sugerencia legible (regla 3)."""
    if not isinstance(data, dict):
        return None
    candidatos = [data.get("suggestedBid"), data.get("bid")]
    anidados = data.get("recommendations")
    if isinstance(anidados, list) and anidados and isinstance(anidados[0], dict):
        candidatos.extend([anidados[0].get("suggestedBid"), anidados[0].get("bid")])
    for valor in candidatos:
        if isinstance(valor, str | int) and str(valor).strip():
            try:
                return Decimal(str(valor))
            except ArithmeticError:
                continue
    return None


def bid_efectivo(
    sugerido: Decimal | None,
    default: Decimal | None,
    floor: Decimal,
    ceiling: Decimal,
) -> Decimal:
    """El bid que sale al POST (sellado 14): la sugerencia si la hay, si no
    el default congelado del goal (regla 3), CLAMPEADO [floor, ceiling] del
    goal en cualquier caso. Sin sugerencia NI default → ValueError ruidoso:
    jamas un 0 inventado."""
    base = sugerido if sugerido is not None else default
    if base is None:
        raise ValueError("sin sugerencia ni default de bid: no se inventa (regla 3)")
    return min(max(base, floor), ceiling)


# ---------------------------------------------------------------------------
# Identidad completa contra Amazon VIVO (listas)
# ---------------------------------------------------------------------------


def _lista_todos(cliente, path: str, profile_id: str | int) -> list[dict]:
    """Todas las paginas de un list v3 (POST de LECTURA con el profile
    SELLADO del aplicador). El request payload va vacio (unknown #4 del
    brief: el shape del filtro no esta capturado) y la identidad se filtra
    cliente-side; paginacion por nextToken con tope."""
    contenedor = _CONTENEDORES_LIST[path]
    items: list[dict] = []
    token = None
    for _ in range(TOPE_PAGINAS_LIST):
        body = {"nextToken": token} if token else {}
        resp = cliente.list_objects(path, body, profile_id=profile_id)
        try:
            data = resp.json()
        except ValueError:
            raise AdsApiError(f"respuesta de lista ilegible: {_CONTENEDORES_LIST[path]}") from None
        items.extend(x for x in data.get(contenedor) or [] if isinstance(x, dict))
        token = data.get("nextToken")
        if not token:
            break
    return items


def _identidad(items: list[dict], ad_group_id: str, keyword_text: str) -> dict | None:
    """IDENTIDAD COMPLETA (sellado 13): mismo adGroupId + mismo texto + match
    EXACT. Shapes del WIRE verificados por el probe 2.5 (2026-08-26, ledger
    ids 1-20): matchType viaja con el enum completo (NEGATIVE_EXACT en
    negatives, EXACT en keywords — se normaliza el prefijo) y el "delete" v3
    ARCHIVA: un item con state=ARCHIVED esta operativamente MUERTO y NO
    cuenta (el mismo criterio del smoke). La misma keyword_text en OTRO ad
    group NO cuenta — el señuelo de r2 codex 7."""
    for item in items:
        if str(item.get("state", "")).upper() == apply.ESTADO_WIRE_ARCHIVED:
            continue  # delete-archiva: operativamente AUSENTE (probe 2.5)
        if str(item.get("adGroupId", "")) != str(ad_group_id):
            continue
        if item.get("keywordText") != keyword_text:
            continue
        if str(item.get("matchType", "")).casefold().replace("negative_", "") != "exact":
            continue
        return item
    return None


def _solo_en_otro_ad_group(items: list[dict], ad_group_id: str, keyword_text: str) -> bool:
    """El texto existe en Amazon PERO no en el ad group esperado (señuelo):
    la matriz §6.1 NO confirma con el y cierra failed. CX4 de la
    cross-review del dueno: SOLO cuentan items VIVOS de OTRO ad group — un
    ARCHIVED del MISMO grupo (delete-archiva del probe 2.5: operativamente
    AUSENTE) o una variante de otro matchType en el PROPIO grupo NO son
    señuelo: el reintento del POST sigue vivo en vez de cerrar en
    'fallo:senuelo_otro_ad_group'."""
    propio = _identidad(items, ad_group_id, keyword_text)
    if propio is not None:
        return False
    for item in items:
        if str(item.get("state", "")).upper() == apply.ESTADO_WIRE_ARCHIVED:
            continue  # delete-archiva: operativamente AUSENTE (probe 2.5)
        if str(item.get("adGroupId", "")) == str(ad_group_id):
            continue  # del MISMO grupo (otro matchType): no es señuelo
        if item.get("keywordText") == keyword_text:
            return True
    return False


def _id_de_ack(ack: dict, clave_principal: str) -> str | None:
    """El id del objeto creado segun el ack. Shape SELLADO por el probe 2.5
    (2026-08-26, apply_attempt 13 y 16): 207 con success/error ANIDADOS por
    recurso, el id vive en el primer success — las variantes planas/listas
    siguen probandose como defensa (GK2(c) de la cross-review). None = ack
    sin id legible (regla 3: jamas inventado; la reversa resuelve el id por
    lista si hace falta)."""
    if not isinstance(ack, dict):
        return None
    claves = (clave_principal, f"{clave_principal}List", "keywordId", "keywordIdList")
    id_ = _id_plano_de(ack, claves)
    if id_ is not None:
        return id_
    for valor in ack.values():
        if not isinstance(valor, dict):
            continue
        success = valor.get("success")
        if not isinstance(success, list):
            continue
        for item in success:
            if not isinstance(item, dict):
                continue
            id_ = _id_plano_de(item, claves)
            if id_ is not None:
                return id_
            for sub in item.values():  # {"keyword": {"keywordId": ...}}
                if isinstance(sub, dict):
                    id_ = _id_plano_de(sub, claves)
                    if id_ is not None:
                        return id_
    return None


def _id_plano_de(dic: dict, claves: tuple[str, ...]) -> str | None:
    """El id bajo las claves candidatas de UN dict plano: valor directo o
    lista de ids (toma el primero). None = nada legible ahi."""
    for clave in claves:
        valor = dic.get(clave)
        if isinstance(valor, str | int) and not isinstance(valor, bool) and str(valor).strip():
            return str(valor)
        if isinstance(valor, list) and valor and isinstance(valor[0], str | int):
            return str(valor[0])
    return None


def _errores_de_ack(ack: dict) -> list:
    """Las entradas de error[] del ack 207 anidado (shape sellado por el
    probe 2.5, apply_attempt 13-17: {"<recurso>": {"error": [...], "success":
    [...]}}): la fila RECHAZADA por-item viaja en error[] y un 2xx NO es
    exito automatico (CX2/CX3 de la cross-review del dueno). [] = sin
    rechazos legibles (regla 3: un shape sin anidado no inventa errores)."""
    if not isinstance(ack, dict):
        return []
    for valor in ack.values():
        if isinstance(valor, dict):
            error = valor.get("error")
            if isinstance(error, list) and error:
                return error
    return []


def _reversa_rechazada(ack: dict, clave_id: str, objeto_id) -> bool:
    """CX3: el veredicto del ack de UN delete — True = la reversa NO se
    confirma. Rechazado si error[] trae la fila (el 207 con rechazo
    por-item) o si el id legible de success[] NO es el del objeto borrado
    (fail-closed: borrar OTRA cosa tampoco confirma). Un ack SIN estructura
    legible y sin error[] sigue en pie (el 2xx del delete es la evidencia)."""
    if _errores_de_ack(ack):
        return True
    id_ack = _id_de_ack(ack, clave_id)
    return id_ack is not None and id_ack != str(objeto_id)


def _resultado_reversa_rechazada(ack: dict) -> str:
    """El resultado del ledger de una reversa rechazada por-item (CX3): la
    etiqueta + el cuerpo del ack (el ack completo ya vive saneado en su
    columna; aqui va compacto para el resultado)."""
    return f"fallo:reversa_rechazada: {str(ack)[:300]}"


# ---------------------------------------------------------------------------
# Carga del job y su contexto fresco
# ---------------------------------------------------------------------------


def _job_de_fila(fila) -> _Job:
    return _Job(
        id=fila[0],
        decision_id=fila[1],
        search_term=fila[2],
        ad_entity_id=fila[3],
        fase=fila[4],
        external_ids=dict(fila[5] or {}),
        plataforma=fila[6],
    )


def _nace_job(conn: psycopg.Connection, platform: str, fila) -> _Job:
    """Nace 'pending' AL LIBERAR, antes de cualquier HTTP (trigger 0001). Clave
    con job en vuelo → se CONTINUA ese (bloquea duplicados). CX2 de la
    cross-review: el INSERT va con SAVEPOINT (conn.transaction()) para que el
    choque del unico parcial NO aborte la transaccion de produccion (SIN
    autocommit, como app.db.connect) — sin el savepoint, el SELECT del reuso
    reventaria con InFailedSqlTransaction."""
    try:
        with conn.transaction():  # savepoint: absorbe el choque sin abortar la tx
            fila_job = conn.execute(
                _SQL_INSERT_JOB,
                (fila.decision_id, fila.search_term, platform, fila.ad_entity_id),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        fila_job = conn.execute(
            _SQL_JOB_EXISTENTE, (platform, fila.ad_entity_id, fila.search_term)
        ).fetchone()
        if fila_job is None:
            raise
    return _job_de_fila(fila_job)


def _goal_del_grupo(conn: psycopg.Connection, platform: str, campaign_pk: int) -> g.Goal | None:
    """Goal resuelto del GRUPO origen (precedencia campana > plataforma, la
    misma resolucion que Aplicador.modo_efectivo; espejo local porque este
    modulo no puede importar apply_cola — ver docstring). El SELECT de 2.1
    devuelve las columnas en el ORDEN del dataclass Goal: g.Goal(*f)."""
    goals = [g.Goal(*f) for f in conn.execute(apply._SQL_GOALS_ENTIDAD, (platform, campaign_pk))]
    goal_campana = next((x for x in goals if x.scope == "campaign"), None)
    goal_plataforma = next((x for x in goals if x.scope != "campaign"), None)
    return g.resuelve_goal(goal_campana, goal_plataforma)


def _contexto(conn: psycopg.Connection, job: _Job) -> _Contexto:
    """Origen, destino y default FRESCOS de la base. Dato faltante →
    ValueError con su MOTIVO (regla 3: se falla el job, no se improvisa)."""
    externos = conn.execute(_SQL_EXTERNALES, (job.ad_entity_id,)).fetchone()
    if externos is None:
        raise ValueError(MOTIVO_ENTIDAD_INCOMPLETA)
    padre = conn.execute(_SQL_PADRE, (job.ad_entity_id,)).fetchone()
    goal = _goal_del_grupo(conn, job.plataforma, padre[0]) if padre is not None else None
    if goal is None or goal.harvest_campaign_id is None or goal.harvest_ad_group_id is None:
        raise ValueError(MOTIVO_SIN_CONFIG)
    new_value, value_currency = conn.execute(_SQL_DECISION, (job.decision_id,)).fetchone()
    if new_value is None:
        raise ValueError(MOTIVO_BID_DEFAULT_FALTANTE)
    if value_currency != goal.bid_currency:
        raise ValueError(MOTIVO_MONEDA_INCOHERENTE)
    floor, ceiling = g.resuelve_floor_ceiling(goal)
    return _Contexto(
        plataforma=job.plataforma,
        grupo_ext=externos[0],
        campana_ext=externos[1],
        destino_grupo=goal.harvest_ad_group_id,
        destino_campana=goal.harvest_campaign_id,
        default_bid=new_value,
        moneda=value_currency,
        floor=floor,
        ceiling=ceiling,
    )


# ---------------------------------------------------------------------------
# Avances de fase y cierre
# ---------------------------------------------------------------------------


def _avanza(conn: psycopg.Connection, job: _Job, fase: str | None, ids: dict | None = None) -> None:
    """Acumula ids externos y opcionalmente avanza la fase (trigger sella
    la progresion; UPDATE sin cambio de fase es legitimo segun 0002)."""
    ext = dict(job.external_ids)
    if ids:
        ext.update({k: v for k, v in ids.items() if v is not None})
    job.external_ids = ext
    if fase is not None:
        job.fase = fase
        conn.execute(_SQL_AVANZA_FASE, (fase, Json(ext), job.id))
    else:
        conn.execute(_SQL_UPDATE_IDS, (Json(ext), job.id))


def _termina_cola(conn: psycopg.Connection, queue_id: int, estado: str) -> None:
    """applied|failed de la fila de la cola (espejo de apply_cola)."""
    sello = "applied_at" if estado == "applied" else "failed_at"
    conn.execute(
        f"UPDATE apply_queue SET estado = %s, {sello} = now() WHERE id = %s", (estado, queue_id)
    )


def _sella_pendientes(conn: psycopg.Connection, decision_id: int, resultado: str) -> None:
    """Sella las filas sin sello (crash/5xx) cuando la evidencia viva ya
    resolvio el veredicto (UNA vez; solo resultado IS NULL)."""
    conn.execute(_SQL_SELLA_PENDIENTES, (resultado, decision_id))


# El lector de estado vive en app.apply (una sola fuente, shape del probe
# 2.5): el ciclo de imports apply_cola -> apply_harvest obligaba el espejo
# local; ahora ambos reusan el de apply (escaneo por cruce de id + wire
# UPPER) en su version PAGINADA (CX1/QW1: _estado_de_readback).


def _falla_job(
    conn: psycopg.Connection,
    job: _Job,
    motivo: str,
    *,
    queue_id: int | None,
    detalle: str = "",
) -> tuple[str, AlertaHarvest]:
    """Cierra el job en failed (legal desde cualquier fase en vuelo),
    termina su fila de cola y produce la ALERTA ESTRUCTURADA. GK4 de la
    cross-review: job y cola se cierran en UNA transaccion — nunca un job
    failed con la fila applying viva (esa combinacion es la que bloquea la
    clave para siempre). 3.3 (sellados 13/19): la alerta SALE por el canal
    Telegram AQUI, el punto unico de fallo definitivo (la reversa automatica
    ya corrio en el caller); si el envio fallo, la bandera envio_fallido
    viaja con la alerta hasta el ciclo para la NOTA notes['telegram']."""
    with conn.transaction():
        conn.execute(_SQL_JOB_FAILED, (job.id,))
        if queue_id is not None:
            _termina_cola(conn, queue_id, "failed")
    job.fase = "failed"
    alerta = AlertaHarvest(
        motivo=motivo,
        decision_id=job.decision_id,
        search_term=job.search_term,
        plataforma=job.plataforma,
        job_id=job.id,
        detalle=detalle,
    )
    if not notifica.notifica_harvest_failed(alerta):
        alerta = replace(alerta, envio_fallido=True)
    return "failed", alerta


# ---------------------------------------------------------------------------
# Reversas de harvest (regla 7 / sellado 12)
# ---------------------------------------------------------------------------


def _reversa_delete(
    conn: psycopg.Connection, cliente, decision_id: int, clase: str, objeto_id
) -> bool:
    """UN delete de reversa con su fila de ledger (tipo 'reversa', EXENTA de
    quota) nacida PRE-HTTP y sellada al volver. El "delete" v3 es POST
    /sp/{recurso}/delete con FILTRO de ids (probe 2.5, apply_attempt 14 y 17)
    y ARCHIVA: operativamente muerto. CX3 de la cross-review: el 207 NO es
    exito automatico — exige sin rechazo por-item (error[]) y, si el ack
    expone id, que sea el del objeto borrado; si no, 'fallo:reversa_rechazada'
    con el cuerpo y False."""
    filtro = f"{clase}IdFilter" if clase == "keyword" else "negativeKeywordIdFilter"
    payload = {filtro: {"include": [str(objeto_id)]}}
    id_attempt = apply._ledger(conn, decision_id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return False
    conn.commit()  # intencion durable PRE-HTTP
    try:
        if clase == "keyword":
            resp = cliente.borrar_keyword(objeto_id)
        else:
            resp = cliente.borrar_negative(objeto_id)
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        return False
    ack = apply._json_seguro(resp)
    clave = "keywordId" if clase == "keyword" else "negativeKeywordId"
    if _reversa_rechazada(ack, clave, objeto_id):
        with conn.transaction():
            apply._sella_ledger(
                conn, id_attempt, ack=ack, resultado=_resultado_reversa_rechazada(ack)
            )
        return False
    with conn.transaction():
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
    return True


def reversa_harvest_parcial(
    conn: psycopg.Connection, cliente, decision_id: int, negative_id
) -> bool:
    """Reversa del harvest PARCIAL (la keyword no nacio): delete del
    negativo. Ledger tipo 'reversa', EXENTA de quota (sellado 12)."""
    return _reversa_delete(conn, cliente, decision_id, "negative", negative_id)


def reversa_harvest_completo(
    conn: psycopg.Connection, cliente, decision_id: int, negative_id, keyword_id
) -> bool:
    """Reversa del harvest COMPLETO (§7, ORDEN SELLADO): delete de la KEYWORD
    PRIMERO, negativo DESPUES (invertido, el termino volveria a competir en
    el origen con la keyword muerta). Exentas ambas. CX3 de la cross-review:
    si el delete de la keyword FALLA, el delete del negativo JAMAS sale — la
    reversa se aborta (fila/queue consistentes) para REINTENTAR en el ciclo
    siguiente; borrar el negativo con la keyword viva devolveria el termino
    a competir en origen Y destino."""
    if not _reversa_delete(conn, cliente, decision_id, "keyword", keyword_id):
        return False
    return _reversa_delete(conn, cliente, decision_id, "negative", negative_id)


def _reversa_automatica(conn: psycopg.Connection, aplicador, job: _Job, ctx: _Contexto) -> str:
    """La reversa del fallo definitivo (sellado 13): borrar lo creado, en el
    ORDEN sellado (keyword PRIMERO). Best-effort: el detalle declara que
    fallo (la alerta es la senal que el operador ve).

    Semantica DECLARADA de la cola de GK1 (cross-review): parcial = lo que
    nacio se revierte. Si el negative_id NO se puede resolver (ack sin id y
    ausente de la lista de origen), la keyword nacida SE BORRA IGUAL y el
    detalle lo declara — el retorno temprano viejo dejaba la exacta huerfana
    en destino. El keyword_id ausente se resuelve por IDENTIDAD en el
    destino (simetrico al negativo)."""
    try:
        cliente = aplicador._cliente()
        ext = dict(job.external_ids)
        neg_id = ext.get("negative_id")
        kw_id = ext.get("keyword_id")
        if kw_id is None:
            kws = _lista_todos(cliente, "/sp/keywords/list", aplicador._profile_id)
            propio = _identidad(kws, ctx.destino_grupo, job.search_term)
            if propio is not None:
                kw_id = propio.get("keywordId")
        if kw_id is not None and not _reversa_delete(
            conn, cliente, job.decision_id, "keyword", kw_id
        ):
            return f"reversa: fallo borrando keyword {kw_id}"
        if neg_id is None:
            items = _lista_todos(cliente, "/sp/negativeKeywords/list", aplicador._profile_id)
            propio = _identidad(items, ctx.grupo_ext, job.search_term)
            if propio is not None:
                neg_id = propio.get("keywordId")
        if neg_id is None:
            if kw_id is None:
                return "reversa: nada que borrar (sin keyword ni negativo)"
            return "reversa: keyword borrada; negativo no encontrado (nada mas que borrar)"
        if not _reversa_delete(conn, cliente, job.decision_id, "negative", neg_id):
            return f"reversa: fallo borrando negativo {neg_id}"
        return "reversa: ok"
    except AdsClientError as exc:
        return f"reversa: fallo ({exc})"


# ---------------------------------------------------------------------------
# La cadena de fases (matriz §6.1): cada paso cae al siguiente
# ---------------------------------------------------------------------------


def _payload_keyword(ctx: _Contexto, term: str, bid: Decimal) -> dict:
    """Payload EXACTO del POST de la keyword (MISMO shape que el write client:
    enums del wire REAL — probe 2.5, apply_attempt 16: matchType EXACT + state
    ENABLED — y el bid quantizado por _bid_payload, una sola fuente, regla 2;
    el wire lo serializa como NUMERO _bid_wire)."""
    return {
        "adGroupId": ctx.destino_grupo,
        "campaignId": ctx.destino_campana,
        "keywordText": term,
        "matchType": "EXACT",
        "state": "ENABLED",
        "bid": apply._bid_payload(bid),
    }


def _paso_negative(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    ctx: _Contexto,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    """Fase pending → negative_created. LISTA contra Amazon VIVO antes de
    escribir (POST no idempotente): el negativo YA esta (crash post-POST
    pre-sello) → avanza por evidencia SIN re-postear. GK2(a) de la
    cross-review: tras el POST, un ack SIN id resuelto FAILS CLOSED — el paso
    NO avanza (el termino no se cosecha sin cortarse en origen): ledger
    'fallo:ack_sin_id', reversa best-effort, failed + alerta."""
    cliente = aplicador._cliente()
    items = _lista_todos(cliente, "/sp/negativeKeywords/list", aplicador._profile_id)
    propio = _identidad(items, ctx.grupo_ext, job.search_term)
    if propio is not None:
        _sella_pendientes(conn, job.decision_id, "ok:reconciliado")
        _avanza(conn, job, "negative_created", {"negative_id": propio.get("keywordId")})
        return "avanza", None
    payload = {
        # Espejo del wire REAL (probe 2.5, apply_attempt 13): enums UPPER.
        "adGroupId": ctx.grupo_ext,
        "campaignId": ctx.campana_ext,
        "keywordText": job.search_term,
        "matchType": "NEGATIVE_EXACT",
        "state": "ENABLED",
    }
    id_attempt = apply._ledger(conn, job.decision_id, "normal", payload, quota_cobrada=True)
    if id_attempt is None:
        return _falla_job(conn, job, MOTIVO_TOPE_INTENTOS, queue_id=queue_id)
    conn.commit()  # intencion durable PRE-HTTP
    try:
        resp = cliente.crear_negative_exacto(ctx.grupo_ext, ctx.campana_ext, job.search_term)
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        return _falla_job(
            conn, job, MOTIVO_FALLO_NEGATIVE, queue_id=queue_id, detalle=f"fallo http {exc.status}"
        )
    ack = apply._json_seguro(resp)
    neg_id = _id_de_ack(ack, "negativeKeywordId")
    if neg_id is None:
        # GK2(a): fail-closed — sin id del ack no hay evidencia del corte en
        # origen; se sella el fallo y se revierte lo que pudo nacer.
        with conn.transaction():
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado="fallo:ack_sin_id")
        conn.commit()
        detalle = (
            _reversa_automatica(conn, aplicador, job, ctx) + " | ack sin negative_id (fail-closed)"
        )
        return _falla_job(conn, job, MOTIVO_FALLO_NEGATIVE, queue_id=queue_id, detalle=detalle)
    with conn.transaction():
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
        _avanza(conn, job, "negative_created", {"negative_id": neg_id})
    return "avanza", None


def _paso_keyword(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    ctx: _Contexto,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    """Fase negative_created → exact_created. Si la keyword YA nacio en el
    destino (identidad completa), avanza sin re-postear; si no: bid sugerido
    (regla 8, fail-open al default) clampeado, INTENCION con el bid EFECTIVO
    en el ledger PRE-POST (sellado 14) y POST. Fallo definitivo → reversa
    automatica + failed + alerta. GK2(b) de la cross-review: la keyword
    JAMAS se postea sin negative_id resuelto (external_ids o evidencia viva
    del origen — fail-closed), y su ack SIN id tambien cierra failed con
    reversa completa."""
    cliente = aplicador._cliente()
    if not job.external_ids.get("negative_id"):
        # id del negativo para la reversa: evidencia viva si el ack no lo dio
        items = _lista_todos(cliente, "/sp/negativeKeywords/list", aplicador._profile_id)
        propio = _identidad(items, ctx.grupo_ext, job.search_term)
        if propio is not None:
            _avanza(conn, job, None, {"negative_id": propio.get("keywordId")})
    if not job.external_ids.get("negative_id"):
        # GK2(b): sin negativo cortado en origen NO se cosecha el termino.
        return _falla_job(
            conn,
            job,
            MOTIVO_FALLO_NEGATIVE,
            queue_id=queue_id,
            detalle="negative_id ausente: no se cosecha sin corte en origen (fail-closed)",
        )
    kws = _lista_todos(cliente, "/sp/keywords/list", aplicador._profile_id)
    encontrado = _identidad(kws, ctx.destino_grupo, job.search_term)
    if encontrado is not None:
        _sella_pendientes(conn, job.decision_id, "ok:reconciliado")
        _avanza(conn, job, "exact_created", {"keyword_id": encontrado.get("keywordId")})
        return "avanza", None
    sugerido = bid_sugerido(cliente)  # PENDIENTE-DE-REGLA-8: sin id del termino pre-creacion
    try:
        bid = bid_efectivo(sugerido, ctx.default_bid, ctx.floor, ctx.ceiling)
    except ValueError:
        return _falla_job(
            conn, job, MOTIVO_BID_DEFAULT_FALTANTE, queue_id=queue_id, detalle="sin bid efectivo"
        )
    payload = _payload_keyword(ctx, job.search_term, bid)
    id_attempt = apply._ledger(conn, job.decision_id, "normal", payload, quota_cobrada=False)
    if id_attempt is None:
        detalle = _reversa_automatica(conn, aplicador, job, ctx)
        return _falla_job(conn, job, MOTIVO_TOPE_INTENTOS, queue_id=queue_id, detalle=detalle)
    conn.commit()  # INTENCION PRE-POST (sellado 14): el bid efectivo es durable
    try:
        resp = cliente.crear_keyword_exacta(
            ctx.destino_grupo, ctx.destino_campana, job.search_term, bid, ctx.moneda
        )
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        detalle = _reversa_automatica(conn, aplicador, job, ctx) + f" | fallo http {exc.status}"
        return _falla_job(conn, job, MOTIVO_FALLO_KEYWORD, queue_id=queue_id, detalle=detalle)
    ack = apply._json_seguro(resp)
    kw_id = _id_de_ack(ack, "keywordId")
    if kw_id is None:
        # GK2(b/c): fail-closed — reversa completa (la keyword nacida se
        # resuelve por IDENTIDAD en el destino) y cierre con alerta.
        with conn.transaction():
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado="fallo:ack_sin_id")
        conn.commit()
        detalle = _reversa_automatica(conn, aplicador, job, ctx) + " | ack sin keyword_id"
        return _falla_job(conn, job, MOTIVO_FALLO_KEYWORD, queue_id=queue_id, detalle=detalle)
    with conn.transaction():
        apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
        _avanza(conn, job, "exact_created", {"keyword_id": kw_id})
    return "avanza", None


def _paso_readback(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    ctx: _Contexto,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    """Fase exact_created → done|failed. Readback por LISTA del destino con
    IDENTIDAD COMPLETA: existe → done (resumen + ciclo EJECUTOR); el señuelo
    en otro ad group NO cuenta → failed + reversa (§7) + alerta."""
    cliente = aplicador._cliente()
    kws = _lista_todos(cliente, "/sp/keywords/list", aplicador._profile_id)
    encontrado = _identidad(kws, ctx.destino_grupo, job.search_term)
    if encontrado is None:
        detalle = _reversa_automatica(conn, aplicador, job, ctx) + " | keyword ausente en destino"
        return _falla_job(conn, job, MOTIVO_KEYWORD_AUSENTE, queue_id=queue_id, detalle=detalle)
    ack = {"fuente": "list", "keywordId": encontrado.get("keywordId")}
    with conn.transaction():
        _sella_pendientes(conn, job.decision_id, "ok:reconciliado")
        _avanza(conn, job, "done")
        apply._confirma_resumen(conn, job.decision_id, ack, True, aplicador.cycle_id_ejecutor)
        if queue_id is not None:
            _termina_cola(conn, queue_id, "applied")
    return "done", None


def _continua_job(
    conn: psycopg.Connection, aplicador, job: _Job, *, queue_id: int | None
) -> tuple[str, AlertaHarvest | None]:
    """Conduce el job DESDE su fase actual hasta done|failed (cascada). La
    quota es del caller (cobrada pre-claim). Ambiguo (AdsApiError) SUBE:
    ledger sin sello, job en su fase, la fila ES el rastro."""
    try:
        ctx = _contexto(conn, job)
    except ValueError as exc:
        return _falla_job(conn, job, str(exc), queue_id=queue_id)
    if job.fase == "pending":
        estado, alerta = _paso_negative(conn, aplicador, job, ctx, queue_id)
        if estado != "avanza":
            return estado, alerta
    if job.fase == "negative_created":
        estado, alerta = _paso_keyword(conn, aplicador, job, ctx, queue_id)
        if estado != "avanza":
            return estado, alerta
    return _paso_readback(conn, aplicador, job, ctx, queue_id)


# ---------------------------------------------------------------------------
# El hook de la cola: harvest_job nace AL LIBERAR (sellado 13)
# ---------------------------------------------------------------------------


def aplica_harvest(
    conn: psycopg.Connection, aplicador: Aplicador, fila: FilaCola, *, platform: str
) -> ResultadoHarvest:
    """El apply del corte harvest (lo llama libera_vencidos tras la
    re-validacion): nace el job pending, se cobra la UNICA unidad, claim
    atomico y la cadena de fases. Sin quota la fila QUEDA released (FIFO,
    vetable) con el job ya nacido; claim perdido contra un veto = pierde
    LIMPIO (residual declarado de 2.2: unidad cobrada sin intento)."""
    job = _nace_job(conn, platform, fila)
    if not apply.consume_quota(conn, platform, "harvest"):
        return ResultadoHarvest(estado="sin_quota")
    if conn.execute(_SQL_CLAIM, (fila.id,)).fetchone() is None:
        return ResultadoHarvest(estado="perdida")
    estado, alerta = _continua_job(conn, aplicador, job, queue_id=fila.id)
    return ResultadoHarvest(estado="applied" if estado == "done" else estado, alerta=alerta)


def revalida_harvest(
    conn: psycopg.Connection, platform: str, fila: FilaCola, ahora: dt.datetime
) -> str | None:
    """Re-validacion PRE-claim del corte HARVEST (ADV-05 de la review
    adversaria; sellado 6: 'jamas se corta por silencio contra la regla').
    El hogar natural de la regla del harvest es este modulo: re-evalua
    decide_hygiene con la ventana FRESCA del termino al reloj de LIBERACION,
    umbral/piso RE-RESUELTOS de la evidencia fresca del grupo y config de
    harvest + keywords de destino resueltas FRESCAS del goal.

    None = sigue calificando (la cadena corre). Motivo = discard: el
    vocabulario es el de apply_cola (vendio_en_ventana / ya_no_califica /
    ...), espejado como string porque apply_cola importa este modulo. El
    target/floor/ceiling de firma son los mismos valores declarados de
    apply_cola (_TARGET_REVALIDA=100 deja el tope ACoS del harvest en el cap
    fijo 35, igual que cualquier target real >= 35): JAMAS se persisten
    (regla 3)."""
    grupo = fila.ad_entity_id
    padre = conn.execute(_SQL_PADRE, (grupo,)).fetchone()
    goal = _goal_del_grupo(conn, platform, padre[0]) if padre is not None else None
    config: hygiene.ConfigHarvest | None = None
    keywords: frozenset[str] = frozenset()
    if (
        goal is not None
        and goal.harvest_campaign_id is not None
        and goal.harvest_ad_group_id is not None
        and goal.harvest_default_bid is not None
    ):
        config = hygiene.ConfigHarvest(
            campaign_id=goal.harvest_campaign_id,
            ad_group_id=goal.harvest_ad_group_id,
            default_bid=goal.harvest_default_bid,
            moneda=goal.bid_currency,
        )
        keywords = hygiene.keywords_campana_destino(conn, platform, goal.harvest_campaign_id)
    evidencia = windows.ventanas_evidencia_ad_group(conn, platform, ahora).get(grupo)
    umbral = cortes.umbral_corte(evidencia, "negative").umbral
    piso = cortes.piso_corte(evidencia, platform).piso_cost
    terminos = windows.terminos_cortes(conn, grupo, ahora)
    term = next((t for t in terminos.terminos if t.search_term == fila.search_term), None)
    if term is None:
        # Sin observaciones frescas del termino en la ventana: ausencia, no
        # ceros inventados (regla 3) — el harvest de evidencia rancia muere.
        return "ya_no_califica"
    single = windows.TerminosCortes(
        ad_entity_id=grupo,
        window_start=terminos.window_start,
        window_end=terminos.window_end,
        fechas_entidad=terminos.fechas_entidad,
        terminos=(term,),
    )
    (resultado,) = hygiene.decide_hygiene(
        platform=platform,
        terminos=single,
        target_acos_pct=_TARGET_REVALIDA,
        config_harvest=config,
        keywords_existentes=keywords,
        umbral_negative=umbral,
        piso_negative=piso,
    )
    if resultado.kind == "harvest":
        return None
    # Para el harvest TODO motivo de no-calificacion (ACoS sobre tope, sin
    # banda, config, duplicado) es el mismo discard: la regla fresca dijo no.
    return "ya_no_califica"


def _reconcilia_pauses(conn: psycopg.Connection, aplicador, platform: str) -> tuple[int, int]:
    """Cola applying huerfana kind PAUSE (ADV-03; matriz §6.1): un fallo
    ambiguo (5xx/red) dejo la fila applying con su ledger sin sello y su
    clave entity_cut bloqueada para siempre. LIST FRESCO de estado decide
    (probe 2.5: el GET directo esta retirado; wire UPPER): PAUSED (Amazon SI
    proceso) → confirmar/applied; ENABLED (no proceso, o el dueno re-activo)
    → failed y, con pause propio verificado, INSERT reactivacion_manual
    (gracia 7d, sellado 17). Lectura ilegible/ambigua → se salta al ciclo
    siguiente (la fila sin sello ES el rastro)."""
    confirmadas = fallidas = 0
    cliente = None
    filas = conn.execute(_SQL_PAUSES_APLICANDO, (platform,)).fetchall()
    for q_id, entidad, decision_id in filas:
        identidad = apply._identidad(conn, entidad)
        if identidad is None or identidad[0] not in apply._KINDS_DECISORAS:
            _sella_pendientes(conn, decision_id, "fallo:entidad_sin_identidad")
            _termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        if cliente is None:
            cliente = aplicador._cliente()
        _, path, contenedor, param = apply._KINDS_DECISORAS[identidad[0]]
        try:
            # CX1/QW1: el LIST PAGINA — la entidad puede vivir en la pagina
            # 2+ y leer solo la primera la daba por ausente (estado ilegible,
            # fila applying eterna).
            estado = apply._estado_de_readback(cliente, path, contenedor, param, identidad[1])
        except AdsApiError:
            continue  # lectura ambigua: proximo ciclo (la fila ES el rastro)
        if estado == apply.ESTADO_WIRE_PAUSED:
            ack = {"fuente": "list", "state": estado}
            with conn.transaction():
                _sella_pendientes(conn, decision_id, "ok:reconciliado")
                apply._confirma_resumen(conn, decision_id, ack, True, aplicador.cycle_id_ejecutor)
                conn.execute(_SQL_CACHE_ESTADO, (estado, entidad))
                _termina_cola(conn, q_id, "applied")
            confirmadas += 1
            continue
        if estado == apply.ESTADO_WIRE_ENABLED:
            with conn.transaction():
                _sella_pendientes(conn, decision_id, "fallo:reconciliado_enabled")
                if conn.execute(_SQL_PAUSE_PROPIO, (entidad,)).fetchone()[0]:
                    # Pause propio verificado + ENABLED vivo: el dueno
                    # re-activo a mano (sellado 17) — gracia de 7d.
                    conn.execute(_SQL_INSERT_REACTIVACION, (entidad,))
                _termina_cola(conn, q_id, "failed")
            fallidas += 1
        # Otro estado (ARCHIVED/ilegible): ambiguo, proximo ciclo.
    return (confirmadas, fallidas)


# ---------------------------------------------------------------------------
# Reconciliacion al inicio del ciclo (sellado 13; matriz §6.1)
# ---------------------------------------------------------------------------


def _cola_de(conn: psycopg.Connection, decision_id: int) -> tuple[int | None, str | None]:
    fila = conn.execute(_SQL_COLA_DE, (decision_id,)).fetchone()
    return (fila[0], fila[1]) if fila is not None else (None, None)


def _reconcilia_negativas(conn: psycopg.Connection, aplicador, platform: str) -> tuple[int, int]:
    """Cola applying huerfana kind NEGATIVE (matriz §6.1): existe con
    identidad → confirmar; SOLO en otro ad group (señuelo) → failed; no
    existe → reintento (tope 3) o failed. El applying conserva su cobro:
    el reintento NO recobra."""
    confirmadas = fallidas = 0
    cliente = None
    filas = conn.execute(_SQL_NEGATIVAS_APLICANDO, (platform,)).fetchall()
    for q_id, entidad, term, decision_id in filas:
        if cliente is None:
            cliente = aplicador._cliente()
        externos = conn.execute(_SQL_EXTERNALES, (entidad,)).fetchone()
        if externos is None:
            _sella_pendientes(conn, decision_id, "fallo:entidad_sin_externos")
            _termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        grupo_ext, campana_ext = externos
        try:
            items = _lista_todos(cliente, "/sp/negativeKeywords/list", aplicador._profile_id)
        except AdsApiError:
            continue  # lectura ambigua: proximo ciclo
        propio = _identidad(items, grupo_ext, term)
        if propio is not None:
            ack = {"fuente": "list", "keywordId": propio.get("keywordId")}
            with conn.transaction():
                _sella_pendientes(conn, decision_id, "ok:reconciliado")
                apply._confirma_resumen(conn, decision_id, ack, True, aplicador.cycle_id_ejecutor)
                _termina_cola(conn, q_id, "applied")
            confirmadas += 1
            continue
        if _solo_en_otro_ad_group(items, grupo_ext, term):
            _sella_pendientes(conn, decision_id, "fallo:senuelo_otro_ad_group")
            _termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        # El tope cuenta SOLO intentos 'normal' (CX1/GK1, apply._SQL_COUNT_INTENTOS):
        # las reversas son el mecanismo de seguridad y jamas consumen
        # presupuesto de intentos (bug PR27-2: el count(*) crudo cerraba en
        # tope_intentos con 2 normales + 1 reversa).
        total = conn.execute(apply._SQL_COUNT_INTENTOS, (decision_id,)).fetchone()[0]
        if total >= apply.TOPE_INTENTOS:
            _sella_pendientes(conn, decision_id, "fallo:tope_intentos")
            _termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        payload = {
            # Espejo del wire REAL (probe 2.5, apply_attempt 13): enums UPPER.
            "adGroupId": grupo_ext,
            "campaignId": campana_ext,
            "keywordText": term,
            "matchType": "NEGATIVE_EXACT",
            "state": "ENABLED",
        }
        id_attempt = apply._ledger(conn, decision_id, "normal", payload, quota_cobrada=False)
        if id_attempt is None:
            _termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        conn.commit()  # intencion durable PRE-HTTP
        try:
            resp = cliente.crear_negative_exacto(grupo_ext, campana_ext, term)
        except apply.AdsApiErrorMutacion as exc:
            apply._sella_ledger(
                conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
            )
            conn.commit()
            _termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        except AdsApiError:
            continue  # ambiguo: la fila sin sello ES el rastro
        ack = apply._json_seguro(resp)
        errores = _errores_de_ack(ack)
        neg_id = _id_de_ack(ack, "negativeKeywordId")
        if errores or neg_id is None:
            # CX2 de la cross-review: el 207 NO es exito automatico — la fila
            # rechazada viaja en error[] (fallo CON el cuerpo) y un ack sin id
            # no es evidencia del corte (GK2: fail-closed).
            resultado = (
                f"fallo:ack_con_error: {str(errores)[:300]}" if errores else "fallo:ack_sin_id"
            )
            with conn.transaction():
                apply._sella_ledger(conn, id_attempt, ack=ack, resultado=resultado)
                _termina_cola(conn, q_id, "failed")
            fallidas += 1
            continue
        with conn.transaction():
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
            _sella_pendientes(conn, decision_id, "ok:reconciliado")
            apply._confirma_resumen(conn, decision_id, ack, True, aplicador.cycle_id_ejecutor)
            _termina_cola(conn, q_id, "applied")
        confirmadas += 1
    return confirmadas, fallidas


def _reconcilia_harvest_huerfanas(conn: psycopg.Connection, platform: str) -> int:
    """Red de seguridad GK4 (cross-review del dueno): filas apply_queue
    applying de kind harvest cuyo job YA no esta en vuelo (failed/done o jamas
    nacio) → failed con nota en el ledger pendiente. Sin este barrido la
    clave term_cut quedaria bloqueada para siempre. La fila CON job en vuelo
    NO se toca: el job la conduce. Devuelve cuantas cerro."""
    cerradas = 0
    for q_id, decision_id in conn.execute(_SQL_HARVEST_APLICANDO, (platform,)).fetchall():
        if conn.execute(_SQL_JOB_EN_VUELO_DE, (decision_id,)).fetchone()[0]:
            continue  # el job en vuelo conduce esta fila
        with conn.transaction():
            _sella_pendientes(conn, decision_id, "fallo:huerfana_sin_job")
            _termina_cola(conn, q_id, "failed")
        cerradas += 1
    return cerradas


def reconcilia_harvest(
    conn: psycopg.Connection, aplicador: Aplicador, platform: str
) -> ResumenReconciliacion:
    """El barrido de reconciliacion al INICIO del ciclo (2.2/2.4 la invocan),
    contra Amazon VIVO por lista con IDENTIDAD COMPLETA: jobs en vuelo (la
    matriz decide por fase: ya aplicada avanza por EVIDENCIA, falta
    reintenta el POST seguro), la cola applying huerfana de negatives
    normales, la de PAUSES por LIST fresco de estado (ADV-03, matriz §6.1),
    los jobs de filas muertas (vetoed/discarded → failed: la cola manda) y
    la red de seguridad de filas harvest applying sin job vivo (GK4).
    Quota SOLO la primera vez; antes de cualquier HTTP reclama la fila (el
    veto puede ganar el claim). Ambiguo por job → se salta al ciclo siguiente
    (la fila sin sello ES el rastro)."""
    alertas: list[AlertaHarvest] = []
    jobs_done = jobs_failed = cerrados = 0
    for fila_job in conn.execute(_SQL_JOBS_EN_VUELO, (platform,)).fetchall():
        job = _job_de_fila(fila_job)
        queue_id, queue_estado = _cola_de(conn, job.decision_id)
        if queue_estado in ("vetoed", "discarded"):
            conn.execute(_SQL_JOB_FAILED, (job.id,))
            cerrados += 1
            continue
        if queue_estado == "released":
            if not apply.consume_quota(conn, platform, "harvest"):
                continue  # sigue esperando quota (y vetable)
            if queue_id is None or conn.execute(_SQL_CLAIM, (queue_id,)).fetchone() is None:
                continue  # un veto gano la carrera del claim: la cola manda
        try:
            estado, alerta = _continua_job(conn, aplicador, job, queue_id=queue_id)
        except AdsApiError:
            continue
        if estado == "done":
            jobs_done += 1
        elif estado == "failed":
            jobs_failed += 1
            if alerta is not None:
                alertas.append(alerta)
    negativas_confirmadas, negativas_fallidas = _reconcilia_negativas(conn, aplicador, platform)
    pausas_confirmadas, pausas_fallidas = _reconcilia_pauses(conn, aplicador, platform)
    huerfanas = _reconcilia_harvest_huerfanas(conn, platform)
    return ResumenReconciliacion(
        jobs_done=jobs_done,
        jobs_failed=jobs_failed,
        negativas_confirmadas=negativas_confirmadas,
        negativas_fallidas=negativas_fallidas,
        jobs_cerrados_por_cola=cerrados,
        alertas=tuple(alertas),
        pausas_confirmadas=pausas_confirmadas,
        pausas_fallidas=pausas_fallidas,
        harvest_huerfanas_cerradas=huerfanas,
    )
