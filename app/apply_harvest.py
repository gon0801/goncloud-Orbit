"""Ejecucion del harvest real: job al liberar y reversas (ORBIT 04, 2.3).

La revalidacion PRE-claim y la reconciliacion de inicio de ciclo viven en
`app.apply_harvest_reconciliacion` (FABRICA 02, A.3a); aqui quedan la
ejecucion y dos envolturas compatibles que delegan con import local.

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
import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import psycopg
from psycopg.types.json import Json

from app import apply, notifica
from app.ads.client import AdsApiError, AdsClientError
from app.optimizer import goals as g
from app.optimizer import harvest_destino, hygiene

if TYPE_CHECKING:
    from app.apply import Aplicador, CapSaturado
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
MOTIVO_ARCHIVADO_EN_VUELO = "archivado_en_vuelo"
MOTIVO_HERMANAS_PENDIENTES = "hermanas_pendientes"

# FABRICA 02 (A.3): fase de higiene tras el readback de la keyword. Orden
# canonico de roles discovery (el roster excluye `category_exact`, que es el
# destino, y el rol de origen, que ya fue negado): como maximo TRES
# identidades objetivo por job. TOPE_CICLOS_HERMANAS = 3 sellado por el
# brief; el paso inicial tras el sello cuenta como ciclo 1.
ROLES_DISCOVERY = (
    "auto_discovery",
    "category_phrase",
    "category_broad",
    "product_targeting",
)
TOPE_CICLOS_HERMANAS = 3
TOPE_INTENTOS_HERMANA = 3

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
   AND fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas')
"""

_SQL_AVANZA_FASE = """
UPDATE harvest_job SET fase = %s, external_ids = %s, updated_at = now() WHERE id = %s
"""

# BIDS 01 2.2 (b), H2 acotado: la keyword YA fue archivada a mano entre la
# decision y el apply. _identidad la ve AUSENTE (sellado probe 2.5 para
# reconciliacion: NO se toca); este chequeo del LEDGER es lo que impide el
# POST duplicado. Identidad = la de _identidad (mismo ad group + mismo texto
# + EXACT) + plataforma; texto comparado con lower+btrim, coherente con la
# normalizacion del dedupe de decision.
#
# CUALQUIER estado no repuesto bloquea (revision del PR #155, CodeRabbit
# Major): con `estado = 'applied'` quedaba una carrera real — el archivador
# commitea `planeado` ANTES del DELETE y solo sella `applied` tras el
# readback, asi que en esa ventana (el ida y vuelta HTTP) el harvest no veia
# nada y creaba el duplicado. Ampliarlo es SEGURO porque este chequeo solo se
# alcanza cuando `_identidad` YA fallo, es decir cuando la keyword no esta
# viva en el destino: si el archivo se quedo en `planeado` o `failed` pero la
# keyword sigue viva, `_identidad` la encuentra y reconcilia sin llegar aqui.
# Y cubre el residual H3 de grok (archivada en Amazon con el sello sin
# promover). repuesto_at con valor = la reversa ya repuso: NO bloquea.
_SQL_ARCHIVO_APLICADO = """
SELECT id, estado FROM keyword_archivo_manual
 WHERE platform = %s::platform
   AND ad_group_external = %s
   AND btrim(lower(keyword_text)) = btrim(lower(%s))
   AND match_type = 'EXACT'
   AND repuesto_at IS NULL
 LIMIT 1
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
SELECT new_value, value_currency, inputs FROM decision WHERE id = %s
"""

# FABRICA 02 (A.1): el destino congelado pertenece a esa campana y a esa
# plataforma (parentesco por ad_entity, sin HTTP; la existencia viva la
# cubre el LIST del flujo). Sin fila -> no es hermana.
_SQL_DESTINO_PERTENECE = """
SELECT 1
  FROM ad_entity ag
  JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
 WHERE ag.kind = 'ad_group'
   AND ag.external_id = %s AND c.external_id = %s
   AND ag.platform = %s::platform AND c.platform = %s::platform
"""

# FABRICA 02 (A.3): roster de hermanas por `grupo_id` (jamas por nombre):
# rol + externos de campana y ad group de cada rol del grupo. El ad group
# viaja GUARDADO en `campana_grupo_rol` (no resuelto por parent_id).
_SQL_ROSTER = """
SELECT r.rol::text, ce.external_id, ae.external_id
  FROM campana_grupo_rol r
  JOIN ad_entity ce ON ce.id = r.ad_entity_id
  JOIN ad_entity ae ON ae.id = r.ad_group_ad_entity_id
 WHERE r.grupo_id = %s
"""

_SQL_ROL_ORIGEN = """
SELECT rol::text FROM campana_grupo_rol WHERE ad_entity_id = %s
"""

_SQL_SELLA_PENDIENTES = """
UPDATE apply_attempt SET resultado = %s, finished_at = now()
 WHERE decision_id = %s AND finished_at IS NULL AND resultado IS NULL
"""

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
    # Preflight 1.4 (D3d): el cobro de la unidad lo hace ESTE hook — si ese
    # cobro llevo used a cap, el evento viaja al ciclo (aviso fail-silent).
    caps_saturados: tuple[CapSaturado, ...] = ()


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
    # Preflight 1.4 (D3d): caps de quota llevados a su transicion por el
    # re-cobro de filas released — viajan al ciclo (aviso fail-silent).
    caps_saturados: tuple[CapSaturado, ...] = ()


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
    default congelado en decision.new_value (sellado 14) y su moneda.
    `resuelto_por`/`grupo_id` viajan del congelado F2 (None en el camino de
    goal fresco pre-F2, que cierra como hoy sin fase nueva)."""

    plataforma: str
    grupo_ext: str
    campana_ext: str
    destino_grupo: str
    destino_campana: str
    default_bid: Decimal
    moneda: str
    floor: Decimal
    ceiling: Decimal
    resuelto_por: str | None = None
    grupo_id: int | None = None


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


def _valida_token_list(token) -> str:
    """Estado del `nextToken` de una pagina (r3, AC-1): "fin" si ausente o
    null; "mas" si es string no vacio; "ambiguo" en cualquier otra forma
    (numero, lista, dict, string vacio) — unknown, jamas se asume pagina
    final ni se reenvia un token ilegible."""
    if token is None:
        return "fin"
    if isinstance(token, str) and token:
        return "mas"
    return "ambiguo"


def _id_list_valido(valor) -> bool:
    """Id de LIST (keywordId/adGroupId): string/int no vacio. bool es int
    en Python y no es identidad; estructuras tampoco."""
    return isinstance(valor, str | int) and not isinstance(valor, bool) and bool(str(valor).strip())


# Enums UPPER del wire por contenedor LIST. Desconocido = pagina unknown
# (fail-closed): no se infiere ausencia ni se adopta. Sin casefold.
_MATCH_POR_CONTENEDOR = {
    "negativeKeywords": frozenset({"NEGATIVE_EXACT", "NEGATIVE_PHRASE"}),
    "keywords": frozenset({"EXACT", "PHRASE", "BROAD"}),
}
_ESTADOS_LIST = frozenset(
    {
        apply.ESTADO_WIRE_ENABLED,
        apply.ESTADO_WIRE_PAUSED,
        apply.ESTADO_WIRE_ARCHIVED,
    }
)


def _texto_list_valido(valor) -> bool:
    """Campo textual de LIST (keywordText): string no vacio."""
    return isinstance(valor, str) and bool(valor.strip())


def _elemento_list_valido(item, contenedor: str) -> bool:
    """Fila LIST con identidad y enums del contenedor. Un dict parcial o
    un matchType/state fuera del vocabulario UPPER no es observacion:
    keywordId ausente se adoptaba como None y un matchType=ROTO se
    trataba como ausencia (POST duplicado)."""
    if not isinstance(item, dict):
        return False
    if not _id_list_valido(item.get("keywordId")):
        return False
    if not _id_list_valido(item.get("adGroupId")):
        return False
    if not _texto_list_valido(item.get("keywordText")):
        return False
    match = item.get("matchType")
    if not isinstance(match, str) or match not in _MATCH_POR_CONTENEDOR.get(
        contenedor, frozenset()
    ):
        return False
    state = item.get("state")
    return isinstance(state, str) and state in _ESTADOS_LIST


def _valida_pagina_list(data, contenedor: str) -> tuple[list[dict], bool]:
    """(elementos, pagina_valida) canonica (r3/r4, AC-1): body no-dict,
    contenedor ausente/no-lista o ALGUN elemento incompleto/enum
    desconocido -> ([], False). Cada elemento exige keywordId, adGroupId
    (string/int no vacio, sin bool ni estructuras), keywordText (string
    no vacio) y matchType/state del vocabulario UPPER del contenedor.
    Un 200 que no dice nada util es unknown: cero avance destructivo."""
    if not isinstance(data, dict):
        return ([], False)
    crudo = data.get(contenedor)
    if not isinstance(crudo, list):
        return ([], False)
    if not all(_elemento_list_valido(x, contenedor) for x in crudo):
        return ([], False)
    return (list(crudo), True)


def _lista_completa(cliente, path: str) -> tuple[list[dict], bool]:
    """Barrido paginado con senal de completitud (F2, A.3 r1/r3) por la
    puerta sellada `list_sellado` (scope de la instancia, sin profile del
    caller): (items, True) solo si cada pagina fue valida y `nextToken` se
    agoto dentro del tope; pagina malformada (elemento incompleto o
    matchType/state fuera del enum del contenedor inclusive), token
    invalido/repetido o `nextToken` vivo al tope ->
    (items, False). `_lista_todos` historico no
    da la senal y no se toca: sus callers la asumen completa. La reversa
    manual la exige (concluir ausencia sobre lectura trunca es borrar a
    ciegas)."""
    contenedor = _CONTENEDORES_LIST[path]
    items: list[dict] = []
    vistos: set[str] = set()
    token = None
    for _ in range(TOPE_PAGINAS_LIST):
        body = {"nextToken": token} if token else {}
        try:
            data = cliente.list_sellado(path, body).json()
        except ValueError:
            raise AdsApiError(f"respuesta de lista ilegible: {contenedor}") from None
        pagina, valida = _valida_pagina_list(data, contenedor)
        if not valida:
            return (items, False)
        items.extend(pagina)
        estado_token = _valida_token_list(data.get("nextToken"))
        if estado_token == "fin":
            return (items, True)
        if estado_token == "ambiguo":
            return (items, False)
        token = data.get("nextToken")
        if token in vistos:
            return (items, False)  # el token se repite: no avanza
        vistos.add(token)
    return (items, False)


def _lista_filtrada(cliente, ad_group_ids: list[str]) -> tuple[list[dict], str]:
    """UN barrido logico batched de negativeKeywords (F2, A.3): un solo LIST
    paginado con `adGroupIdFilter` en TODAS las paginas (prohibido un LIST
    por hermana), por la puerta sellada `list_sellado` (scope de la
    instancia, jamas profile a mano). Devuelve (items, estado) con estado
    en {"ok", "truncado", "ambiguo"}: `nextToken` vivo al tope ->
    "truncado" (fail-closed: cero POST); token invalido/repetido, item
    fuera del filtro (el filtro parece ignorado) o pagina malformada
    (body no-dict, contenedor no-lista, elemento incompleto o
    matchType/state fuera del enum del contenedor: r3/r4, AC-1) ->
    "ambiguo" (unknown: cero POST — un LIST que no dijo nada
    jamas habilita una mutacion). (`totalResults` NO se usa: su semantica con filtro no
    esta verificada en vivo.) No toca el contrato de `_lista_todos` (sus
    callers historicos siguen intactos)."""
    path = "/sp/negativeKeywords/list"
    contenedor = _CONTENEDORES_LIST[path]
    dentro = {str(x) for x in ad_group_ids}
    items: list[dict] = []
    vistos: set[str] = set()
    token = None
    for _ in range(TOPE_PAGINAS_LIST):
        body: dict = {"adGroupIdFilter": {"include": [str(x) for x in ad_group_ids]}}
        if token:
            body["nextToken"] = token
        try:
            data = cliente.list_sellado(path, body).json()
        except ValueError:
            raise AdsApiError(f"respuesta de lista ilegible: {contenedor}") from None
        pagina, valida = _valida_pagina_list(data, contenedor)
        if not valida:
            return (items, "ambiguo")  # 200 malformado: unknown, cero POST
        for x in pagina:
            if str(x.get("adGroupId", "")) not in dentro:
                return (items, "ambiguo")  # el filtro parece ignorado
            items.append(x)
        estado_token = _valida_token_list(data.get("nextToken"))
        if estado_token == "fin":
            return (items, "ok")
        if estado_token == "ambiguo":
            return (items, "ambiguo")
        token = data.get("nextToken")
        if token in vistos:
            return (items, "ambiguo")  # el token se repite: no avanza
        vistos.add(token)
    return (items, "truncado")  # nextToken vivo al tope: fail-closed


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


def _coincidencias(items: list[dict], ad_group_id: str, keyword_text: str) -> list[dict]:
    """TODAS las identidades vivas coincidentes (F2, A.3 r1): el mismo
    criterio de `_identidad` (ad group + texto + EXACT, sin ARCHIVED), pero
    en lista para contar. La procedencia por ID exige cardinalidad: un solo
    abierto + un solo hallazgo atribuye; el resto es pendiente. `_identidad`
    no se toca (callers sellados)."""
    return [
        item
        for item in items
        if str(item.get("state", "")).upper() != apply.ESTADO_WIRE_ARCHIVED
        and str(item.get("adGroupId", "")) == str(ad_group_id)
        and item.get("keywordText") == keyword_text
        and str(item.get("matchType", "")).casefold().replace("negative_", "") == "exact"
    ]


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
    """Origen, destino y default: del CONGELADO F2 cuando la decision lo
    trae (FABRICA 02, A.1), del goal FRESCO en otro caso (decisiones de eras
    previas: D.1 exige cola de harvest vacia al desplegar, asi que en vivo
    no hay mezcla; los tests viejos siembran sin congelado y siguen por el
    camino actual). Dato faltante → ValueError con su MOTIVO (regla 3: se
    falla el job, no se improvisa). Jamas se re-rutea: el destino es el
    congelado o nada."""
    externos = conn.execute(_SQL_EXTERNALES, (job.ad_entity_id,)).fetchone()
    if externos is None:
        raise ValueError(MOTIVO_ENTIDAD_INCOMPLETA)
    padre = conn.execute(_SQL_PADRE, (job.ad_entity_id,)).fetchone()
    new_value, value_currency, inputs = conn.execute(_SQL_DECISION, (job.decision_id,)).fetchone()
    congelado = ((inputs or {}).get("goal") or {}).get("harvest") or {}
    if congelado.get("resuelto_por") in (
        harvest_destino.RESUELTO_GRUPO,
        harvest_destino.RESUELTO_EXCEPCION,
        harvest_destino.RESUELTO_TERNA,
    ):
        return _contexto_congelado(
            conn, job, padre, externos, new_value, value_currency, inputs, congelado
        )
    goal = _goal_del_grupo(conn, job.plataforma, padre[0]) if padre is not None else None
    if goal is None or goal.harvest_campaign_id is None or goal.harvest_ad_group_id is None:
        raise ValueError(MOTIVO_SIN_CONFIG)
    if new_value is None:
        raise ValueError(MOTIVO_BID_DEFAULT_FALTANTE)
    if value_currency != goal.bid_currency:
        raise ValueError(MOTIVO_MONEDA_INCOHERENTE)
    # Defaults POR MONEDA (preflight 1.2): la moneda es la del PROPIO goal
    # (ya cruzada contra value_currency de la decision, dos lineas arriba).
    floor, ceiling = g.resuelve_floor_ceiling(goal, goal.bid_currency)
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
        resuelto_por=None,
        grupo_id=None,
    )


def _contexto_congelado(
    conn: psycopg.Connection,
    job: _Job,
    padre,
    externos,
    new_value,
    value_currency,
    inputs: dict,
    congelado: dict,
) -> _Contexto:
    """Destino del congelado F2, re-validado antes del POST (A.1): si
    `resuelto_por` es grupo y la exacta vigente del grupo ya no es la
    congelada → descarte `destino_desincronizado` (jamas re-rutear ni
    postear a un congelado que ya no es hermana); y el par congelado debe
    pertenecer a esa campana y a esa plataforma en `ad_entity` (la
    existencia viva la cubre el LIST del flujo). floor/ceiling salen del
    freeze del goal (ya efectivos al decidir)."""
    if new_value is None:
        raise ValueError(MOTIVO_BID_DEFAULT_FALTANTE)
    destino_campana = congelado.get("campaign_id")
    destino_grupo = congelado.get("ad_group_id")
    if not destino_campana or not destino_grupo:
        raise ValueError(MOTIVO_SIN_CONFIG)
    if value_currency != congelado.get("moneda"):
        raise ValueError(MOTIVO_MONEDA_INCOHERENTE)
    if congelado.get("resuelto_por") == harvest_destino.RESUELTO_GRUPO and padre is not None:
        vigente = harvest_destino.resolver_destino(conn, job.plataforma, padre[0])
        if not (
            isinstance(vigente, harvest_destino.DestinoHarvest)
            and vigente.resuelto_por == harvest_destino.RESUELTO_GRUPO
            and vigente.campaign_external == destino_campana
            and vigente.ad_group_external == destino_grupo
        ):
            raise ValueError(hygiene.MOTIVO_DESTINO_DESINCRONIZADO)
    pertenece = conn.execute(
        _SQL_DESTINO_PERTENECE,
        (destino_grupo, destino_campana, job.plataforma, job.plataforma),
    ).fetchone()
    if pertenece is None:
        raise ValueError(hygiene.MOTIVO_DESTINO_DESINCRONIZADO)
    goal_congelado = inputs.get("goal") or {}
    return _Contexto(
        plataforma=job.plataforma,
        grupo_ext=externos[0],
        campana_ext=externos[1],
        destino_grupo=destino_grupo,
        destino_campana=destino_campana,
        default_bid=new_value,
        moneda=value_currency,
        floor=Decimal(str(goal_congelado.get("bid_floor"))),
        ceiling=Decimal(str(goal_congelado.get("bid_ceiling"))),
        resuelto_por=congelado.get("resuelto_por"),
        grupo_id=congelado.get("grupo_id"),
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


def _delete_reversa_pendiente(
    conn: psycopg.Connection, cliente, decision_id: int, clase: str, objeto_id
) -> tuple[int | None, dict | None]:
    """POST de delete para la reversa MANUAL (F2, A.3 r1): crea la fila
    pre-HTTP tipo='reversa' sin quota y la sella SOLO en fallo definitivo
    (Mutacion/rechazo por-item). Con ack aceptado la deja ABIERTA y devuelve
    (id_attempt, ack): el caller la sella ok UNICAMENTE tras readback de
    ausente. Sellar ok antes del readback es inseguro al reanudar (el
    ledger diria borrado lo que sigue vivo y la reanudacion lo saltaria,
    pudiendo borrar el origen). El payload es identico al de
    `_reversa_delete` (el cruce por ledger lo lee igual). Retorna
    (None, None) si ni la fila nacio o el HTTP fue ambiguo (la fila abierta
    es el rastro; reintentar es seguro e idempotente)."""
    filtro = f"{clase}IdFilter" if clase == "keyword" else "negativeKeywordIdFilter"
    payload = {filtro: {"include": [str(objeto_id)]}}
    id_attempt = apply._ledger(conn, decision_id, "reversa", payload, quota_cobrada=False)
    if id_attempt is None:
        return (None, None)
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
        return (None, None)
    except AdsApiError:
        return (id_attempt, None)  # ambiguo: fila abierta, sin ack
    ack = apply._json_seguro(resp)
    clave = "keywordId" if clase == "keyword" else "negativeKeywordId"
    if _reversa_rechazada(ack, clave, objeto_id):
        with conn.transaction():
            apply._sella_ledger(
                conn, id_attempt, ack=ack, resultado=_resultado_reversa_rechazada(ack)
            )
        return (None, None)
    return (id_attempt, ack)


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


# ---------------------------------------------------------------------------
# Reversa manual del harvest con hermanas (F2, A.3; la ejecuta el tool)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PasoReversa:
    """Un delete de la reversa manual, en orden canonico de ejecucion:
    keyword destino -> hermanas (propias probadas y ACKs aun no resueltos,
    en orden ROLES_DISCOVERY) -> negativo de origen. `rol` solo en
    hermanas; las adoptadas (`creada = false`) jamas entran al plan.
    `provisional` = id de un ACK aceptado aun no resuelto (r3, AC-7): se
    busca fail-closed antes de tocarlo y se omite si esta ausente."""

    clase: str  # "keyword" | "negative"
    rol: str | None
    ad_group_ext: str
    objeto_id: str
    provisional: bool = False


_SQL_JOB_POR_ID = """
SELECT id, decision_id, search_term, ad_entity_id, fase, external_ids, platform::text
  FROM harvest_job WHERE id = %s
"""

_SQL_VERIFY_OK = """
SELECT verify_ok FROM decision_application WHERE decision_id = %s
"""

_SQL_ULTIMA_COLA = """
SELECT estado FROM apply_queue WHERE decision_id = %s ORDER BY id DESC LIMIT 1
"""

_SQL_ACKS_HERMANA = """
SELECT request_payload->>'adGroupId', ack FROM apply_attempt
 WHERE decision_id = %s AND tipo = 'hermana' AND ack IS NOT NULL
 ORDER BY id
"""

_SQL_REVERSA_OK_KEYWORD = """
SELECT EXISTS (
    SELECT 1 FROM apply_attempt
     WHERE decision_id = %s AND tipo = 'reversa' AND resultado = 'ok'
       AND request_payload->'keywordIdFilter'->'include' @> %s::jsonb
)
"""

_SQL_REVERSA_OK_NEGATIVE = """
SELECT EXISTS (
    SELECT 1 FROM apply_attempt
     WHERE decision_id = %s AND tipo = 'reversa' AND resultado = 'ok'
       AND request_payload->'negativeKeywordIdFilter'->'include' @> %s::jsonb
)
"""


def plan_reversa_harvest(
    conn: psycopg.Connection, job_id: int
) -> tuple[str, str, int, list[PasoReversa]]:
    """Deriva de la base el plan de reversa de un job (platform, termino,
    decision_id, pasos en orden canonico). Precondiciones fail-closed
    (ValueError si no proceden): job `done`, decision confirmada
    (`verify_ok`), cola `applied`, keyword y negativo de origen conocidos.
    Las hermanas adoptadas no entran (jamas se borra lo ajeno). Las ids de
    ACK aceptado aun no resueltas entran como provisionales (r3, AC-7): la
    ejecucion las busca fail-closed y las omite si estan ausentes, asi una
    id aceptada por Amazon nunca desaparece del estado operativo."""
    fila = conn.execute(_SQL_JOB_POR_ID, (job_id,)).fetchone()
    if fila is None:
        raise ValueError(f"job {job_id}: no existe")
    job = _job_de_fila(fila)
    if job.fase != "done":
        raise ValueError(f"job {job_id}: fase {job.fase} (la reversa manual solo acepta done)")
    ver = conn.execute(_SQL_VERIFY_OK, (job.decision_id,)).fetchone()
    if ver is None or ver[0] is not True:
        raise ValueError(f"job {job_id}: decision {job.decision_id} sin verify_ok")
    cola = conn.execute(_SQL_ULTIMA_COLA, (job.decision_id,)).fetchone()
    if cola is None or cola[0] != "applied":
        raise ValueError(f"job {job_id}: cola no applied")
    ext = dict(job.external_ids)
    keyword_id = ext.get("keyword_id")
    negative_id = ext.get("negative_id")
    if keyword_id is None or negative_id is None:
        raise ValueError(f"job {job_id}: sin keyword_id o negative_id en external_ids")
    _dec = conn.execute(_SQL_DECISION, (job.decision_id,)).fetchone()
    congelado = (((_dec[2] or {}).get("goal") or {}).get("harvest") or {}) if _dec else {}
    destino_ag = congelado.get("ad_group_id")
    if not destino_ag:
        raise ValueError(f"job {job_id}: sin destino congelado para el readback")
    externos = conn.execute(_SQL_EXTERNALES, (job.ad_entity_id,)).fetchone()
    if externos is None:
        raise ValueError(f"job {job_id}: origen sin externos")
    objetivo = dict(ext.get("hermanas_objetivo") or {})
    hermanas = dict(ext.get("hermanas") or {})
    registradas = {
        str(v.get("negative_id"))
        for v in hermanas.values()
        if isinstance(v, dict) and v.get("negative_id") is not None
    }
    ag_por_rol = {
        str(v.get("ad_group_id")): rol
        for rol, v in objetivo.items()
        if isinstance(v, dict) and v.get("ad_group_id") is not None
    }
    provisionales: dict[str, list[str]] = {}
    for ag, ack in conn.execute(_SQL_ACKS_HERMANA, (job.decision_id,)).fetchall():
        if ag is None or ag not in ag_por_rol or not isinstance(ack, dict):
            continue
        id_ = _id_de_ack(ack, "negativeKeywordId")
        if id_ is not None and str(id_) not in registradas:
            provisionales.setdefault(ag_por_rol[ag], []).append(str(id_))
    pasos = [
        PasoReversa(clase="keyword", rol=None, ad_group_ext=destino_ag, objeto_id=str(keyword_id))
    ]
    for rol in ROLES_DISCOVERY:
        reg = hermanas.get(rol)
        if isinstance(reg, dict) and reg.get("creada") is True:
            ag = (objetivo.get(rol) or {}).get("ad_group_id")
            if not ag:
                raise ValueError(f"job {job_id}: hermana {rol} sin ad group en el roster")
            pasos.append(
                PasoReversa(
                    clase="negative",
                    rol=rol,
                    ad_group_ext=ag,
                    objeto_id=str(reg["negative_id"]),
                )
            )
        for extra in provisionales.get(rol, []):
            ag = (objetivo.get(rol) or {}).get("ad_group_id")
            if not ag:
                raise ValueError(f"job {job_id}: hermana {rol} sin ad group en el roster")
            pasos.append(
                PasoReversa(
                    clase="negative",
                    rol=rol,
                    ad_group_ext=ag,
                    objeto_id=extra,
                    provisional=True,
                )
            )
    pasos.append(
        PasoReversa(
            clase="negative", rol=None, ad_group_ext=externos[0], objeto_id=str(negative_id)
        )
    )
    return (job.plataforma, job.search_term, job.decision_id, pasos)


def _reversa_confirmada(
    conn: psycopg.Connection, decision_id: int, clase: str, objeto_id: str
) -> bool:
    """El ledger ya confirma el delete de este objeto (fila tipo='reversa'
    con resultado ok cuyo filtro DE SU CLASE incluye el id): reejecutar lo
    salta, no lo repite. Sin guard global: la procedencia es por objeto. El
    cruce es por clase (r4): una keyword y una negativa que compartan id no
    se confirman entre si."""
    sql = _SQL_REVERSA_OK_KEYWORD if clase == "keyword" else _SQL_REVERSA_OK_NEGATIVE
    fila = conn.execute(sql, (decision_id, Json([str(objeto_id)]))).fetchone()
    return fila is not None and fila[0] is True


def _preexiste_provisional(cliente, term: str, paso: PasoReversa) -> str:
    """Pre-readback fail-closed de un paso provisional (r3 AC-7, r4):
    "proceder" si la id esta viva con identidad exacta; "omitir" si la id
    esta ausente (muerta o inexistente: no hay nada que borrar);
    "stop-unknown" si la lectura es unknown; "stop-discordante" si la id
    vive con otra identidad (otro termino o match: detener TODA la reversa
    — el objeto cambio bajo los pies y seguir hasta el origen seria borrar
    a ciegas). La coincidencia es por ID exacta, no por identidad suelta."""
    if paso.clase == "keyword":
        items, completa = _lista_completa(cliente, "/sp/keywords/list")
        if not completa:
            return "stop-unknown"
    else:
        items, estado = _lista_filtrada(cliente, [paso.ad_group_ext])
        if estado != "ok":
            return "stop-unknown"
    vivas = {
        str(x.get("keywordId"))
        for x in items
        if str(x.get("state", "")).upper() != apply.ESTADO_WIRE_ARCHIVED
    }
    if paso.objeto_id not in vivas:
        return "omitir"
    if paso.objeto_id in {
        str(x.get("keywordId")) for x in _coincidencias(items, paso.ad_group_ext, term)
    }:
        return "proceder"
    return "stop-discordante"


def ejecuta_reversa_harvest(
    conn: psycopg.Connection,
    cliente,
    decision_id: int,
    term: str,
    pasos: list[PasoReversa],
) -> tuple[bool, str]:
    """Ejecuta el plan en orden canonico, con readback entre deletes y stop
    al primer fallo (True, "reversa: ok" solo si todo confirma). Cada delete
    nace su fila pre-HTTP tipo='reversa' sin quota; la fila se sella ok SOLO
    tras readback de ARCHIVED/ausente (r1: sellar antes es saltable al
    reanudar con el objeto vivo). Readback no concluyente (lista trunca o
    ambigua) o identidad viva = stop sin tocar lo siguiente, con la fila
    abierta para reintentar. Los pasos provisionales (ACK aceptado aun no
    resuelto, r3 AC-7) se buscan fail-closed ANTES de tocarlos: lectura
    unknown -> stop; ausente -> se omiten (nada que borrar); viva con la id
    -> se eliminan con ledger/readback igual que los propios. Reejecutar
    salta lo confirmado por ledger. Nunca borra el origen mientras falte
    confirmar una hermana propia: el orden lo garantiza (el origen va
    ultimo) y el stop tambien."""
    omitidas: list[str] = []
    for paso in pasos:
        quien = paso.rol or paso.clase
        if _reversa_confirmada(conn, decision_id, paso.clase, paso.objeto_id):
            continue
        if paso.provisional:
            pre = _preexiste_provisional(cliente, term, paso)
            if pre == "stop-unknown":
                return (False, f"reversa: {quien} {paso.objeto_id} readback no concluyente")
            if pre == "stop-discordante":
                return (False, f"reversa: {quien} {paso.objeto_id} identidad discordante")
            if pre == "omitir":
                omitidas.append(paso.objeto_id)
                continue
        id_attempt, ack = _delete_reversa_pendiente(
            conn, cliente, decision_id, paso.clase, paso.objeto_id
        )
        if id_attempt is None or ack is None:
            return (False, f"reversa: stop en {quien} {paso.objeto_id}")
        if paso.clase == "keyword":
            items, completa = _lista_completa(cliente, "/sp/keywords/list")
            if not completa:
                return (False, f"reversa: {quien} {paso.objeto_id} readback no concluyente")
        else:
            items, estado = _lista_filtrada(cliente, [paso.ad_group_ext])
            if estado != "ok":
                return (False, f"reversa: {quien} {paso.objeto_id} readback no concluyente")
        # La confirmacion es POR ID (r3/r4): la borrada ya no esta viva
        # ENTRE TODOS los objetos no ARCHIVED, SIN filtrar por identidad.
        # Si la id sigue viva con otro termino/match, el objeto muto bajo
        # los pies: fallo:sigue_vivo, jamas ok. Otra identidad coincidente
        # con id distinta (adoptada/ajena) no revive al borrado.
        vivas = {
            str(x.get("keywordId"))
            for x in items
            if str(x.get("state", "")).upper() != apply.ESTADO_WIRE_ARCHIVED
        }
        if paso.objeto_id in vivas:
            with conn.transaction():
                apply._sella_ledger(conn, id_attempt, ack=ack, resultado="fallo:sigue_vivo")
            return (False, f"reversa: {quien} {paso.objeto_id} sigue vivo")
        with conn.transaction():
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado="ok")
    if omitidas:
        return (True, f"reversa: ok (provisionales ausentes omitidas: {', '.join(omitidas)})")
    return (True, "reversa: ok")


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
    archivada = conn.execute(
        _SQL_ARCHIVO_APLICADO, (job.plataforma, ctx.destino_grupo, job.search_term)
    ).fetchone()
    if archivada is not None:
        # BIDS 01 2.2 (b): archivada ENTRE la decision y el apply (el LIST
        # la trae ARCHIVED e _identidad la ve ausente, sellado probe 2.5).
        # Recrearla seria duplicar lo archivado a mano: NO hay POST y el
        # job cierra declarando el motivo (terminal, sin reintento). El
        # estado del archivo viaja al detalle: `planeado` significa que el
        # archivador esta en su ventana HTTP justo ahora.
        return _falla_job(
            conn,
            job,
            MOTIVO_ARCHIVADO_EN_VUELO,
            queue_id=queue_id,
            detalle=(
                "keyword archivada en vuelo: fila"
                f" {archivada[1]} sin reponer en keyword_archivo_manual"
                " para la identidad; no se recrea"
            ),
        )
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


def _roster_hermanas(conn: psycopg.Connection, job: _Job, ctx: _Contexto) -> dict | str:
    """Roster congelado {rol: {campaign_id, ad_group_id}} en orden canonico
    (ROLES_DISCOVERY menos el rol de origen; `category_exact` nunca es
    origen: el resolutor lo salta antes). Derivado por `grupo_id`, jamas por
    nombre. El mapa guardado NO promete orden (jsonb reordena claves): los
    consumidores iteran ROLES_DISCOVERY. Devuelve el MOTIVO (str) si no se
    puede derivar: el caller cierra fail-closed con reversa (la keyword ya
    nacio)."""
    if ctx.grupo_id is None:
        return MOTIVO_ENTIDAD_INCOMPLETA
    por_rol = {r: (c, a) for r, c, a in conn.execute(_SQL_ROSTER, (ctx.grupo_id,)).fetchall()}
    padre = conn.execute(_SQL_PADRE, (job.ad_entity_id,)).fetchone()
    rol_origen = None
    if padre is not None:
        fila_rol = conn.execute(_SQL_ROL_ORIGEN, (padre[0],)).fetchone()
        rol_origen = fila_rol[0] if fila_rol is not None else None
    if rol_origen not in ROLES_DISCOVERY:
        return MOTIVO_ENTIDAD_INCOMPLETA
    roster: dict[str, dict] = {}
    for rol in ROLES_DISCOVERY:
        if rol == rol_origen:
            continue
        if rol not in por_rol:
            return MOTIVO_ENTIDAD_INCOMPLETA
        camp_ext, ag_ext = por_rol[rol]
        roster[rol] = {"campaign_id": camp_ext, "ad_group_id": ag_ext}
    return roster


def _paso_readback(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    ctx: _Contexto,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    """Fase exact_created → done|failed|hermanas_negadas. Readback por LISTA
    del destino con IDENTIDAD COMPLETA: existe → done (resumen + ciclo
    EJECUTOR), salvo harvest resuelto por grupo, que SELLA el evento de
    valor (resumen + cola applied + fase + roster en UNA transaccion) y
    avanza a la higiene; el señuelo en otro ad group NO cuenta → failed +
    reversa (§7) + alerta."""
    cliente = aplicador._cliente()
    kws = _lista_todos(cliente, "/sp/keywords/list", aplicador._profile_id)
    encontrado = _identidad(kws, ctx.destino_grupo, job.search_term)
    if encontrado is None:
        detalle = _reversa_automatica(conn, aplicador, job, ctx) + " | keyword ausente en destino"
        return _falla_job(conn, job, MOTIVO_KEYWORD_AUSENTE, queue_id=queue_id, detalle=detalle)
    ack = {"fuente": "list", "keywordId": encontrado.get("keywordId")}
    if ctx.resuelto_por != harvest_destino.RESUELTO_GRUPO:
        with conn.transaction():
            _sella_pendientes(conn, job.decision_id, "ok:reconciliado")
            _avanza(conn, job, "done")
            apply._confirma_resumen(conn, job.decision_id, ack, True, aplicador.cycle_id_ejecutor)
            if queue_id is not None:
                _termina_cola(conn, queue_id, "applied")
        return "done", None
    # Ruta de grupo (F2, A.3): el evento de valor se confirma ANTES de la
    # higiene. El sello (keyword confirmada, resumen, cola, fase y roster)
    # es durable en una transaccion: otra conexion lo ve antes del primer
    # POST de hermana.
    roster = _roster_hermanas(conn, job, ctx)
    if isinstance(roster, str):
        detalle = _reversa_automatica(conn, aplicador, job, ctx) + f" | roster imposible ({roster})"
        return _falla_job(conn, job, roster, queue_id=queue_id, detalle=detalle)
    with conn.transaction():
        _sella_pendientes(conn, job.decision_id, "ok:reconciliado")
        _avanza(
            conn,
            job,
            "hermanas_negadas",
            {
                "keyword_id": encontrado.get("keywordId"),
                "hermanas_objetivo": roster,
                "hermanas": {},
                "hermanas_ciclos": 0,
            },
        )
        apply._confirma_resumen(conn, job.decision_id, ack, True, aplicador.cycle_id_ejecutor)
        if queue_id is not None:
            _termina_cola(conn, queue_id, "applied")
    return "avanza", None


_SQL_ESTADO_COLA = """
SELECT estado FROM apply_queue WHERE id = %s
"""


def _termina_cola_si_applying(conn: psycopg.Connection, queue_id: int | None) -> None:
    """Termina en applied la cola que quedo applying (reanudacion de un
    estado que el sello atomico no deja: en el flujo normal ya esta applied
    y el trigger prohibe el UPDATE sin cambio, asi que solo se toca si
    sigue applying)."""
    if queue_id is None:
        return
    fila = conn.execute(_SQL_ESTADO_COLA, (queue_id,)).fetchone()
    if fila is not None and fila[0] == "applying":
        _termina_cola(conn, queue_id, "applied")


def _motivo_rechazo_hermana(rol: str, status: int | None) -> str:
    """Motivo estable de una hermana rechazada: PT rechazada lleva motivo
    propio (no es exclusion, se reintenta igual); 5xx es ambiguo del
    servidor; lo demas es 400. `status` None = ack sin estructura de
    rechazo pero sin id (ack_sin_id lo pone el caller)."""
    if rol == "product_targeting" and (status is None or status < 500):
        return "pt_no_acepta_negative_keyword"
    if status is not None and status >= 500:
        return "http_5xx"
    return "http_400"


def _motivo_ambiguo_hermana(exc: Exception) -> str:
    """Motivo de un fallo ambiguo (AdsApiError base: red o 5xx-sin-retry en
    POST no idempotente — el cliente no los distingue con tipo). El status
    viaja en el mensaje propio (`status=503 sin retry...`): si se lee un
    5xx es `http_5xx`, si no `red_ambigua`. El formato del mensaje es de
    este repo (una sola fuente); si cambia, el test del 503 avisa."""
    m = re.search(r"status=(\d{3})", str(exc))
    if m is not None and int(m.group(1)) >= 500:
        return "http_5xx"
    return "red_ambigua"


def _cierra_o_sigue(
    conn: psycopg.Connection,
    job: _Job,
    objetivo: dict,
    hermanas: dict,
    ciclos: int,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    """Cierre o continuacion tras persistir un ciclo de higiene (F2, A.3):
    sin restantes → done limpio (sin alerta: todo resuelto); restantes al
    tope de ciclos → done + `hermanas_pendientes` + AlertaHarvest veraz
    (enviada aqui, patron `_falla_job`; el texto jamas dice "failed": el
    evento de valor quedo aplicado); si no → "sigue". La reconciliacion
    recoge la alerta aunque el estado sea done."""
    restantes = [
        r for r in ROLES_DISCOVERY if r in objetivo and "negative_id" not in hermanas.get(r, {})
    ]
    if not restantes:
        with conn.transaction():
            _avanza(conn, job, "done", {"hermanas_pendientes": {}})
            _termina_cola_si_applying(conn, queue_id)
        return "done", None
    if ciclos >= TOPE_CICLOS_HERMANAS:
        # Defensive .get: todo camino que deja pendiente escribe motivo; el
        # default es inalcanzable por construccion.
        pendientes = {r: hermanas[r].get("motivo", "red_ambigua") for r in restantes}
        resueltas = [r for r in ROLES_DISCOVERY if r in objetivo and r not in restantes]
        detalle = (
            f"{len(resueltas)}/{len(objetivo)} hermanas aplicadas"
            f" ({', '.join(resueltas)}); pendientes: "
            + ", ".join(f"{r}: {pendientes[r]}" for r in restantes)
        )
        alerta = AlertaHarvest(
            motivo=MOTIVO_HERMANAS_PENDIENTES,
            decision_id=job.decision_id,
            search_term=job.search_term,
            plataforma=job.plataforma,
            job_id=job.id,
            detalle=detalle,
        )
        if not notifica.notifica_harvest_hermanas(alerta):
            alerta = replace(alerta, envio_fallido=True)
        with conn.transaction():
            _avanza(conn, job, "done", {"hermanas_pendientes": pendientes})
            _termina_cola_si_applying(conn, queue_id)
        return "done", alerta
    return "sigue", None


def _acks_hermana(intentos: list[tuple[int, dict | None, bool]]) -> set[str]:
    """Ids de ack duraderas de los intentos previos de una hermana: la
    prueba de procedencia (r2). Un intento sin ack (crash con respuesta
    perdida) no aporta id; uno sellado con ack, si."""
    ids: set[str] = set()
    for _i, ack, _abierta in intentos:
        if not isinstance(ack, dict):
            continue
        id_ = _id_de_ack(ack, "negativeKeywordId")
        if id_ is not None:
            ids.add(str(id_))
    return ids


def _post_hermana(
    conn: psycopg.Connection, cliente, job: _Job, rol: str, ag_ext: str, camp_ext: str
) -> tuple[str | None, int | None, str | None, dict | None]:
    """UN intento POST a una hermana ausente del previo (F2, A.3): como
    maximo uno por identidad y por ciclo. Devuelve (motivo, id_attempt,
    negative_id, ack): motivo None = aceptado con id (la confirmacion la
    hace el barrido posterior contra ESA id); motivo != None = pendiente
    con ese motivo (la fila queda sellada con el fallo, salvo ambiguo, que
    queda abierta como rastro). El tope por (decision, adGroupId) muerde
    antes de crear la fila."""
    if apply._intentos_hermana(conn, job.decision_id, ag_ext) >= TOPE_INTENTOS_HERMANA:
        return ("tope_intentos", None, None, None)
    payload = {
        # Espejo del wire REAL (probe 2.5, apply_attempt 13): enums UPPER.
        "adGroupId": ag_ext,
        "campaignId": camp_ext,
        "keywordText": job.search_term,
        "matchType": "NEGATIVE_EXACT",
        "state": "ENABLED",
    }
    id_attempt = apply._ledger(conn, job.decision_id, "hermana", payload, quota_cobrada=False)
    if id_attempt is None:  # defensivo: 'hermana' no tiene tope normal
        return ("tope_intentos", None, None, None)
    conn.commit()  # intencion durable PRE-HTTP
    try:
        resp = cliente.crear_negative_exacto(ag_ext, camp_ext, job.search_term)
    except apply.AdsApiErrorMutacion as exc:
        apply._sella_ledger(
            conn, id_attempt, ack=None, resultado=f"fallo http {exc.status}: {exc.cuerpo}"
        )
        conn.commit()
        return (_motivo_rechazo_hermana(rol, exc.status), None, None, None)
    except AdsApiError as exc_ambigua:
        # Ambiguo (red o 5xx-sin-retry): la fila sin sello ES el rastro.
        return (_motivo_ambiguo_hermana(exc_ambigua), None, None, None)
    ack = apply._json_seguro(resp)
    errores = _errores_de_ack(ack)
    neg_id = _id_de_ack(ack, "negativeKeywordId")
    if errores or neg_id is None:
        # El 207 NO es exito automatico: la fila en error[] es rechazo
        # por-item (motivo de rechazo); sin id ni errores es ack ilegible
        # (fail-closed, sin evidencia del corte).
        motivo = _motivo_rechazo_hermana(rol, None) if errores else "ack_sin_id"
        with conn.transaction():
            apply._sella_ledger(conn, id_attempt, ack=ack, resultado=f"fallo:{motivo}")
        conn.commit()
        return (motivo, None, None, None)
    # ACK aceptado: queda durable ANTES del readback posterior (r3, AC-4),
    # con la fila abierta. El posterior solo sella el resultado.
    with conn.transaction():
        apply._guarda_ack(conn, id_attempt, ack)
    return (None, id_attempt, neg_id, ack)


def _paso_hermanas(
    conn: psycopg.Connection,
    aplicador,
    job: _Job,
    queue_id: int | None,
) -> tuple[str, AlertaHarvest | None]:
    """Fase hermanas_negadas (F2, A.3): higiene posterior al evento de valor
    (ya confirmado: resumen, cola y fase sellados). Opera SOLO sobre el
    roster congelado en `external_ids`: jamas re-valida membresia viva (un
    cambio de grupo posterior no agrega ni sustituye objetivos) ni recibe
    `_Contexto` (construirlo re-validaria y podria fallar un harvest
    confirmado). Un ciclo = barrido previo batched, como maximo un POST
    por identidad ausente y, si hubo POST, barrido posterior. Nunca quota,
    nunca failed, nunca reversa de la keyword por una hermana: lo no
    resuelto queda pendiente y el job sigue. Retorna ("done", alerta|None)
    al cerrar, ("sigue", None) si continua."""
    _ = queue_id  # la cola ya quedo applied en el sello; aqui no se toca
    ext = dict(job.external_ids)
    objetivo = dict(ext.get("hermanas_objetivo") or {})
    hermanas = {r: dict(v) for r, v in (ext.get("hermanas") or {}).items()}
    ciclos = int(ext.get("hermanas_ciclos") or 0)
    pendientes = [
        r for r in ROLES_DISCOVERY if r in objetivo and "negative_id" not in hermanas.get(r, {})
    ]
    if not pendientes:
        with conn.transaction():
            _avanza(
                conn,
                job,
                "done",
                {
                    "hermanas": hermanas,
                    "hermanas_ciclos": ciclos + 1,
                    "hermanas_pendientes": {},
                },
            )
            _termina_cola_si_applying(conn, queue_id)
        return "done", None
    # Gate del ORIGEN (no de las hermanas): si el origen dejo de estar
    # ENABLED durante la higiene, cada pendiente queda con
    # `ancestro_no_enabled`, sin HTTP. Jamas falla el harvest sellado.
    if apply.gate_ancestros(conn, job.ad_entity_id) is not None:
        for rol in pendientes:
            hermanas[rol] = {"motivo": "ancestro_no_enabled"}
        with conn.transaction():
            _avanza(conn, job, None, {"hermanas": hermanas, "hermanas_ciclos": ciclos + 1})
        return _cierra_o_sigue(conn, job, objetivo, hermanas, ciclos + 1, queue_id)
    cliente = aplicador._cliente()
    previo, estado_previo = _lista_filtrada(
        cliente, [objetivo[r]["ad_group_id"] for r in pendientes]
    )
    if estado_previo != "ok":
        # Fail-closed: barrido incompleto o ambiguo -> cero POST; las
        # pendientes quedan con el motivo del barrido y el job sigue. El
        # precheck truncado tambien nutre ciclos (el sello es el rastro).
        motivo = "list_truncado" if estado_previo == "truncado" else "list_ambiguo"
        for rol in pendientes:
            hermanas[rol] = {"motivo": motivo}
        with conn.transaction():
            _avanza(conn, job, None, {"hermanas": hermanas, "hermanas_ciclos": ciclos + 1})
        return _cierra_o_sigue(conn, job, objetivo, hermanas, ciclos + 1, queue_id)
    posteadas: list[tuple[str, int, str, str, dict]] = []
    for rol in pendientes:
        ag_ext = objetivo[rol]["ad_group_id"]
        camp_ext = objetivo[rol]["campaign_id"]
        halladas = _coincidencias(previo, ag_ext, job.search_term)
        intentos = apply._intentos_hermana_detalle(conn, job.decision_id, ag_ext)
        probadas = [x for x in halladas if str(x.get("keywordId")) in _acks_hermana(intentos)]
        abiertas = [i for (i, _ack, abierta) in intentos if abierta]
        if probadas:
            # Procedencia por ID (r2/r3): la id hallada coincide con el ack
            # duradero de un intento previo -> propia. Las abiertas cuya
            # ack-id aparece se sellan ok (la prueba llego tarde pero
            # llego); las demas abiertas siguen abiertas (su resultado es
            # genuinamente desconocido; sellarlas mentiria).
            propia = probadas[0]
            prob_ids = {str(x.get("keywordId")) for x in probadas}
            with conn.transaction():
                for _i, _ack, _abierta in intentos:
                    if not _abierta or not isinstance(_ack, dict):
                        continue
                    _id = _id_de_ack(_ack, "negativeKeywordId")
                    if _id is not None and str(_id) in prob_ids:
                        apply._sella_resultado(conn, _i, "ok")
            hermanas[rol] = {"negative_id": propia.get("keywordId"), "creada": True}
            continue
        if abiertas:
            # Sin prueba (crash sin ack, o ack que no aparece): pendiente,
            # filas abiertas. Jamas se marca propia una id sin procedencia
            # (r2): la reversa podria borrar lo ajeno. Nunca
            # `_sella_pendientes`: cerraria intentos ambiguos de otras
            # hermanas.
            hermanas[rol] = {"motivo": "red_ambigua"}
            continue
        if halladas:
            # Sin intento propio, lo encontrado se adopta (`creada =
            # false`): jamas se marca propio lo ajeno.
            hermanas[rol] = {"negative_id": halladas[0].get("keywordId"), "creada": False}
            continue
        motivo, id_attempt, neg_id, ack_post = _post_hermana(
            conn, cliente, job, rol, ag_ext, camp_ext
        )
        if motivo is not None:
            hermanas[rol] = {"motivo": motivo}
            continue
        assert id_attempt is not None and neg_id is not None  # contrato de _post_hermana
        posteadas.append((rol, id_attempt, neg_id, ag_ext, ack_post))
    if posteadas:
        posterior, estado_posterior = _lista_filtrada(cliente, [ag for _, _, _, ag, _ in posteadas])
        if estado_posterior != "ok":
            motivo = "list_truncado" if estado_posterior == "truncado" else "list_ambiguo"
            for rol, _id_attempt, _neg_id, _ag_ext, _ack_post in posteadas:
                hermanas[rol] = {"motivo": motivo}  # filas abiertas: el proximo previo las cruza
            with conn.transaction():
                _avanza(conn, job, None, {"hermanas": hermanas, "hermanas_ciclos": ciclos + 1})
            # Por el cierre por tope (r1): tambien el posterior del ciclo 3
            # cierra; no existe cuarto ciclo.
            return _cierra_o_sigue(conn, job, objetivo, hermanas, ciclos + 1, queue_id)
        for rol, id_attempt, neg_id, ag_ext, _ack_post in posteadas:
            # Procedencia por ID (r2/r3): se registra propia SOLO la id del
            # ack que aparece en el readback; el ack ya quedo durable antes
            # del posterior (AC-4), asi que el sello final no lo reescribe.
            # Si no aparece, la hermana NO se marca propia: otra id viva se
            # adopta (el termino queda bloqueado igual), ninguna id viva
            # deja pendiente (la id del ack sigue durable para prueba
            # tardia).
            halladas = _coincidencias(posterior, ag_ext, job.search_term)
            if neg_id in {str(x.get("keywordId")) for x in halladas}:
                with conn.transaction():
                    apply._sella_resultado(conn, id_attempt, "ok")
                hermanas[rol] = {"negative_id": neg_id, "creada": True}
            else:
                with conn.transaction():
                    apply._sella_resultado(conn, id_attempt, "fallo:ack_sin_prueba")
                if halladas:
                    hermanas[rol] = {
                        "negative_id": halladas[0].get("keywordId"),
                        "creada": False,
                    }
                else:
                    hermanas[rol] = {"motivo": "red_ambigua"}
    with conn.transaction():
        _avanza(conn, job, None, {"hermanas": hermanas, "hermanas_ciclos": ciclos + 1})
    return _cierra_o_sigue(conn, job, objetivo, hermanas, ciclos + 1, queue_id)


def _continua_job(
    conn: psycopg.Connection, aplicador, job: _Job, *, queue_id: int | None
) -> tuple[str, AlertaHarvest | None]:
    """Conduce el job DESDE su fase actual hasta done|failed|sigue
    (cascada). "sigue" = evento de valor confirmado y la higiene continua en
    `hermanas_negadas` (F2). Un job ya sellado en higiene NO pasa por
    `_contexto` (r1): re-validar la membresia viva podria fallar un harvest
    confirmado ignorando el roster congelado. La quota es del caller
    (cobrada pre-claim). Ambiguo (AdsApiError) SUBE: ledger sin sello, job
    en su fase, la fila ES el rastro."""
    if job.fase == "hermanas_negadas":
        return _paso_hermanas(conn, aplicador, job, queue_id)
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
    if job.fase == "exact_created":
        estado, alerta = _paso_readback(conn, aplicador, job, ctx, queue_id)
        if estado != "avanza":
            return estado, alerta
    return _paso_hermanas(conn, aplicador, job, queue_id)


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
    usada, saturada = apply.consume_quota_y_sello(conn, platform, "harvest")
    if not usada:
        return ResultadoHarvest(estado="sin_quota")
    caps = ()
    if saturada:
        # Preflight 1.4 (D3a): la unidad que llevo used a cap dispara UN
        # evento; el resultado de la carrera del claim no lo cambia.
        evento = apply.evento_cap_saturado(conn, platform, "harvest")
        if evento is not None:
            caps = (evento,)
    if conn.execute(_SQL_CLAIM, (fila.id,)).fetchone() is None:
        return ResultadoHarvest(estado="perdida", caps_saturados=caps)
    estado, alerta = _continua_job(conn, aplicador, job, queue_id=fila.id)
    # "sigue" (F2) cuenta como aplicada: el evento de valor quedo confirmado
    # (resumen + cola) y solo la higiene continua; mandarlo a otro estado
    # mentiria en los contadores de la cola (apply_cola no conoce "sigue").
    return ResultadoHarvest(
        estado="applied" if estado in ("done", "sigue") else estado,
        alerta=alerta,
        caps_saturados=caps,
    )


# ---------------------------------------------------------------------------
# Compatibilidad A.3a: revalidacion y reconciliacion delegadas (la logica
# vive en app.apply_harvest_reconciliacion; aqui solo la superficie que
# consumen apply_cola, cycle y los tests, sin ciclo de imports en top-level)
# ---------------------------------------------------------------------------


def revalida_harvest(
    conn: psycopg.Connection, platform: str, fila: FilaCola, ahora: dt.datetime
) -> str | None:
    """Compatibilidad (FABRICA 02, A.3a): la re-validacion PRE-claim vive en
    `app.apply_harvest_reconciliacion`. Esta envoltura conserva la firma y la
    superficie que consumen `apply_cola` y los tests; delega con import local,
    ya con este modulo inicializado (sin ciclo de imports en top-level)."""
    from app import apply_harvest_reconciliacion

    return apply_harvest_reconciliacion.revalida_harvest(conn, platform, fila, ahora)


def reconcilia_harvest(
    conn: psycopg.Connection, aplicador: Aplicador, platform: str
) -> ResumenReconciliacion:
    """Compatibilidad (FABRICA 02, A.3a): el barrido de reconciliacion vive
    en `app.apply_harvest_reconciliacion`. Esta envoltura conserva la firma y
    la superficie que consumen el ciclo y los tests; delega con import local,
    ya con este modulo inicializado (sin ciclo de imports en top-level)."""
    from app import apply_harvest_reconciliacion

    return apply_harvest_reconciliacion.reconcilia_harvest(conn, aplicador, platform)
