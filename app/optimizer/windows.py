"""Ventanas de datos y guardas del optimizador (ORBIT 03, task 2.1).

Este modulo NO decide: define las ventanas sobre las que decidiran 2.2 (bids)
y 2.3 (hygiene), los agregados colapsados de esas ventanas y las guardas de
plataforma. Cero escrituras: son queries de LECTURA sobre un
psycopg.Connection que llega por parametro. Las decisiones las escribe el
orquestador (3.1), que tambien persiste el motivo de cada guarda en
optimizer_cycle.status/notes.

Decisiones selladas (plans/orbit-03.md task 2.1 + Spec delta de CONTEXTO.md):

- DOBLE VENTANA, 30 dias calendario INCLUSIVE (inicio = fin - 29d):
  * bids: termina en max(metric_date de ESA entidad en v_metric_latest) - 3d
    (frescura; el dia en curso y los 2 previos se descartan).
  * cortes (pause/negative/harvest): agregado SEPARADO, con SU PROPIA query
    sobre SU ventana, que termina en min(max(metric_date) - 3d,
    decided_at(UTC) - 10d). Prohibido calcular con la ventana de bids y solo
    "bajar" window_end: aqui es imposible por construccion (ventana_cortes y
    terminos_cortes derivan su ventana desde cero). El trigger
    decision_madurez_corte es la segunda capa (test de acople incluido).
- ANTI DOBLE CONTEO (regla 5): las tablas son bitemporales (el mismo
  (entidad, fecha) tiene N observaciones por source_report_id). SIEMPRE se
  colapsa a la ultima observacion por fecha: metricas via v_metric_latest;
  terminos via DISTINCT ON (ad_entity_id, search_term, metric_date)
  ORDER BY observed_at DESC, source_report_id DESC (no hay vista para
  terminos; el desempate por source_report_id cierra el caso dos
  observaciones con el MISMO observed_at — mismo run de backfill, reportes
  distintos — que sin el elegia de forma no determinista; minor de la review
  de 2.1). La plataforma no va en la clave de terminos: entra por el WHERE
  por ad_entity_id.
- CADA VENTANA SE ANCLA EN LOS DATOS QUE AGREGA (regla 8: forma real del
  dato verificada contra la ingesta): las metricas anclan en
  v_metric_latest de la entidad, pero los search terms viven en ad groups
  (SEARCH_TERMS_CFG.kind = "ad_group" en app.ads.reports) y NO existe
  reporte de metricas de ad groups (REPORTES_CFG: campanas, keywords,
  targets, search terms). Anclar terminos_cortes en v_metric_latest del ad
  group devolveria () SIEMPRE en produccion (hallazgo convergente
  codex+grok, cross-review 2026-08-23): la ventana de terminos se ancla en
  max(metric_date) de SUS PROPIAS observaciones de terminos.
- COMPLETITUD: la unidad es la ENTIDAD: >= 7 fechas distintas de la entidad
  dentro de SU ventana (AgregadoMetricas.completa sobre las fechas de
  metricas; TerminosCortes.completa sobre las fechas de los terminos de la
  entidad), evaluada por ventana: la de bids para 2.2, la de cortes para
  2.3. Los terminos NO exigen fechas propias: la evidencia por termino la
  dan sus umbrales de clicks/cost; exigir 7 fechas por termino mataria los
  negativos long-tail.
- GUARDAS DE PLATAFORMA (evaluaciones puras que devuelven motivo; 3.1 decide
  status skipped/degraded):
  * watermark = MAX(metric_date) en v_metric_latest entre las entidades de la
    plataforma (JOIN con ad_entity por platform). Si (hoy UTC) - watermark
    > 7d -> plataforma saltada con motivo.
  * frescura de estructura = MAX(ad_entity_state.synced_at) de la plataforma.
    Si now(UTC) - synced_at > 48h -> plataforma saltada con motivo.
  * comparadores ESTRICTOS: exactamente 7d o exactamente 48h NO salta.
  * plataforma sin metricas NI estado: motivo explicito 'sin_datos', jamas
    excepcion. Fallo parcial (solo metricas o solo estado) tambien salta con
    su motivo: fail-closed.
- DETERMINISMO: no hay now() escondido; decided_at/ahora llegan por
  parametro y SIEMPRE tz-aware (uno naive evaluaria segun la TZ local del
  proceso: se rechaza ruidosamente). guarda_plataforma deriva su `hoy` del
  propio `ahora`: un solo reloj por llamada (regla 2). Las ventanas se
  calculan en Python con UTC explicito, no en SQL: nada depende de la
  TimeZone de sesion.

Supuestos declarados (nuevos, de este modulo):

- AGREGADOS PARCIALES VENENADOS (regla 3): si dentro de la ventana alguna
  observacion colapsada trae una metrica en NULL, el SUM de esa metrica es
  None (CASE WHEN bool_and(...)); jamas un agregado parcial disfrazado de
  completo. Mismo criterio que la fusion de terminos de app.ads.reports.
- ASIN-LIKE FAIL-CLOSED: is_asin_like via bool_or por termino: si ALGUNA
  observacion colapsada del termino es ASIN-like, el termino se reporta
  ASIN-like (2.3 lo salta siempre). El clasificador es determinista sobre el
  texto (regla 2), asi que en produccion todas las filas de un termino
  coinciden; bool_or es la eleccion fail-closed ante cualquier divergencia.
- observed_at_max: la observacion mas reciente que entro a cada agregado.
  Es el insumo de decision.data_observed_at (NOT NULL en el esquema): sin
  esta columna, 3.1 tendria que inventarla o reconsultar (hallazgo codex).
- MONEDA UNICA POR ENTIDAD: el trigger metric_moneda_de_plataforma sella una
  sola moneda por plataforma, asi que min(metric_currency) ES la moneda de
  toda la ventana. Se devuelve como evidencia para que 2.2/2.3 no reconsulten.
- VENTANA VACIA: una entidad con observaciones pero NINGUNA dentro de su
  ventana (ej. solo datos de los 3 dias frescos) devuelve el agregado con
  fechas=() y sumas None, completa=False; None solo cuando la entidad no
  tiene NINGUNA observacion. La distincion importa: vacia es un hecho
  auditable, sin observaciones es ausencia de camino.
- COSTO POR ENTIDAD: cada llamada consulta UNA entidad (3.1 itera las
  elegibles); no se construye ningun barrido de plataforma (regla 1: eso es
  del orquestador, si lo necesita).
- CONTRATO DE SNAPSHOT PARA 3.1 (hallazgo codex, cross-review ronda 2): estas
  funciones NO gestionan transacciones -- cada SELECT es su propio snapshot
  bajo READ COMMITTED, y terminos_cortes hace tres lecturas (ancla, fechas de
  entidad, agregado). Una ingesta concurrente podria desalinear completitud y
  agregados dentro de una misma llamada. El fix correcto NO es por funcion:
  el ORQUESTADOR del ciclo debe abrir TODA su fase de lecturas en una sola
  transaccion REPEATABLE READ, que da un snapshot uniforme a ventanas,
  terminos y watermark. Mientras 3.1 no exista, el unico llamador son los
  tests (sin concurrencia).

SQL del modulo (todas de LECTURA; las parsea el test de sintaxis con pglast):
_SQL_MAX_FECHA_ENTIDAD, _SQL_AGREGA_METRICAS, _SQL_MAX_FECHA_TERMINOS,
_SQL_TERMINOS_CORTES, _SQL_FECHAS_ENTIDAD_TERMINOS, _SQL_WATERMARK_PLATAFORMA,
_SQL_SYNC_PLATAFORMA.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import psycopg

# ---------------------------------------------------------------------------
# Constantes selladas (fuente: plans/orbit-03.md task 2.1 + diseno v2)
# ---------------------------------------------------------------------------

DIAS_VENTANA = 30  # dias calendario INCLUSIVE
DIAS_FRESCURA_BIDS = 3  # window_end_bids = max(metric_date) - 3d
DIAS_MADUREZ_CORTES = 10  # window_end_cortes <= decided_at - 10d (regla 6)
MIN_FECHAS_COMPLETITUD = 7  # fechas distintas por ENTIDAD en su ventana
MAX_EDAD_WATERMARK = dt.timedelta(days=7)  # estricto: exacto NO salta
MAX_EDAD_SYNC = dt.timedelta(hours=48)  # estricto: exacto NO salta

# ---------------------------------------------------------------------------
# Estructuras de salida (documentadas para 2.2/2.3/3.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotivoSkip:
    """Motivo estructurado de una guarda que salta; 3.1 lo persiste en
    optimizer_cycle.status/notes. `guarda` es un vocabulario CERRADO y
    tipado: 'watermark' | 'synced_at' | 'sin_datos'."""

    guarda: Literal["watermark", "synced_at", "sin_datos"]
    detalle: str


@dataclass(frozen=True)
class AgregadoMetricas:
    """Sumas de UNA entidad sobre UNA ventana, colapsadas a la ultima
    observacion por fecha (v_metric_latest). Dinero en Decimal, contadores en
    int; una metrica con alguna observacion NULL en ventana -> None (regla 3).
    `fechas` son las fechas distintas presentes, orden ascendente;
    `observed_at_max` alimenta decision.data_observed_at (hallazgo codex)."""

    window_start: dt.date
    window_end: dt.date
    fechas: tuple[dt.date, ...]
    metric_currency: str | None
    cost: Decimal | None
    ad_revenue: Decimal | None
    revenue_same_sku: Decimal | None
    impressions: int | None
    clicks: int | None
    orders: int | None
    observed_at_max: dt.datetime | None

    @property
    def fechas_distintas(self) -> int:
        return len(self.fechas)

    @property
    def completa(self) -> bool:
        """Unidad sellada: la ENTIDAD en SU ventana (>= 7 fechas distintas)."""
        return len(self.fechas) >= MIN_FECHAS_COMPLETITUD


@dataclass(frozen=True)
class AgregadoTermino:
    """Sumas de UN (ad_entity_id, search_term) sobre la ventana de CORTES,
    colapsadas con DISTINCT ON por (entidad, termino, fecha). Sin umbral de
    fechas propio: la completitud la aporta la entidad (sellado).
    `is_asin_like` es bool_or de las observaciones: fail-closed (hallazgo
    codex; el clasificador es determinista, cualquier divergencia salta)."""

    ad_entity_id: int
    search_term: str
    metric_currency: str | None
    cost: Decimal | None
    ad_revenue: Decimal | None
    clicks: int | None
    orders: int | None
    fechas_distintas: int
    is_asin_like: bool
    observed_at_max: dt.datetime | None


@dataclass(frozen=True)
class TerminosCortes:
    """Los terminos de UNA entidad sobre SU ventana de cortes + la completitud
    de la ENTIDAD medida en fechas de terminos (los ad groups no tienen
    metricas en produccion: ver el ancla en el docstring del modulo).
    window_start/window_end son None solo si la entidad no tiene NINGUNA
    observacion de terminos; una ventana vacia (terminos=(), fechas_entidad=())
    sigue siendo un resultado auditable, no None."""

    ad_entity_id: int
    window_start: dt.date | None
    window_end: dt.date | None
    fechas_entidad: tuple[dt.date, ...]
    terminos: tuple[AgregadoTermino, ...]

    @property
    def fechas_distintas(self) -> int:
        return len(self.fechas_entidad)

    @property
    def completa(self) -> bool:
        """Misma unidad sellada que AgregadoMetricas: la ENTIDAD (>= 7 fechas
        distintas), medida sobre SUS observaciones de terminos."""
        return len(self.fechas_entidad) >= MIN_FECHAS_COMPLETITUD


@dataclass(frozen=True)
class VentanasEntidad:
    """El par de ventanas de una entidad + sus terminos, en un solo pedido.
    `bids`/`cortes` son None solo si la entidad no tiene NINGUNA observacion
    de metricas (los ad groups en produccion: no hay reporte de ellos);
    `terminos` vive SIEMPRE anclado en SUS observaciones (2.3)."""

    ad_entity_id: int
    bids: AgregadoMetricas | None
    cortes: AgregadoMetricas | None
    terminos: TerminosCortes


# ---------------------------------------------------------------------------
# SQL de LECTURA (parametros %s; %s::platform evita ambiguedad de tipos enum)
# ---------------------------------------------------------------------------

_SQL_MAX_FECHA_ENTIDAD = """
SELECT max(metric_date) FROM v_metric_latest WHERE ad_entity_id = %s
"""

# bool_and(...IS NOT NULL): un agregado parcial jamas se disfraza de completo
# (regla 3); array_agg DISTINCT deja la lista de fechas de la ventana como
# evidencia congelable en decision.inputs. Los sum() de contadores llevan
# ::bigint (aqui y en _SQL_TERMINOS_CORTES): sum(BIGINT) devuelve numeric y
# psycopg lo mapea a Decimal; los contadores son int y un Decimal aqui
# reventaria el json.dumps de decision.inputs en 3.1.
_SQL_AGREGA_METRICAS = """
SELECT array_agg(DISTINCT metric_date ORDER BY metric_date),
       min(metric_currency),
       CASE WHEN bool_and(cost IS NOT NULL) THEN sum(cost) END,
       CASE WHEN bool_and(ad_revenue IS NOT NULL) THEN sum(ad_revenue) END,
       CASE WHEN bool_and(revenue_same_sku IS NOT NULL) THEN sum(revenue_same_sku) END,
       CASE WHEN bool_and(impressions IS NOT NULL) THEN sum(impressions)::bigint END,
       CASE WHEN bool_and(clicks IS NOT NULL) THEN sum(clicks)::bigint END,
       CASE WHEN bool_and(orders IS NOT NULL) THEN sum(orders)::bigint END,
       max(observed_at)
  FROM v_metric_latest
 WHERE ad_entity_id = %s
   AND metric_date BETWEEN %s AND %s
"""

# Ancla de la ventana de terminos: max(metric_date) de SUS observaciones. Sin
# DISTINCT ON a proposito: el colapso elige UNA fila por (entidad, termino,
# fecha), asi que el CONJUNTO de fechas presentes es identico con o sin
# colapso, y max() sobre el conjunto crudo da el mismo resultado (regla 5
# intacta: el colapso que importa es el de las FILAS que se SUMAN, en
# _SQL_TERMINOS_CORTES).
_SQL_MAX_FECHA_TERMINOS = """
SELECT max(metric_date) FROM search_term_observation WHERE ad_entity_id = %s
"""

# El colapso de terminos NO tiene vista: DISTINCT ON manual con la clave
# (ad_entity_id, search_term, metric_date) ORDER BY observed_at DESC,
# source_report_id DESC. El desempate extra cierra el caso dos observaciones
# con el MISMO observed_at (mismo run de backfill, reportes distintos): sin
# el, DISTINCT ON elegia de forma NO determinista (minor declarado en la
# review de 2.1; testeado en tests/test_optimizer_hygiene.py relajando la PK
# en la DB temporal, porque bajo el esquema sellado el empate no puede entrar
# a la tabla). La plataforma no va en la clave: entra por el WHERE por
# ad_entity_id (la entidad pertenece a una sola plataforma). is_asin_like via
# bool_or: fail-closed (ver docstring de AgregadoTermino); observed_at max
# alimenta decision.data_observed_at de la decision sobre ese termino.
_SQL_TERMINOS_CORTES = """
SELECT ad_entity_id,
       search_term,
       min(metric_currency),
       CASE WHEN bool_and(cost IS NOT NULL) THEN sum(cost) END,
       CASE WHEN bool_and(ad_revenue IS NOT NULL) THEN sum(ad_revenue) END,
       CASE WHEN bool_and(clicks IS NOT NULL) THEN sum(clicks)::bigint END,
       CASE WHEN bool_and(orders IS NOT NULL) THEN sum(orders)::bigint END,
       count(DISTINCT metric_date),
       bool_or(is_asin_like),
       max(observed_at)
  FROM (
      SELECT DISTINCT ON (ad_entity_id, search_term, metric_date)
             ad_entity_id, search_term, metric_date, metric_currency,
             cost, ad_revenue, clicks, orders, is_asin_like, observed_at,
             source_report_id
        FROM search_term_observation
       WHERE ad_entity_id = %s
         AND metric_date BETWEEN %s AND %s
       ORDER BY ad_entity_id, search_term, metric_date, observed_at DESC,
                source_report_id DESC
  ) colapsado
 GROUP BY ad_entity_id, search_term
 ORDER BY search_term
"""

# Fechas distintas de la ENTIDAD (todas las observaciones de terminos dentro
# de la ventana): la unidad de completitud sellada para hygiene (2.3). Misma
# razon de max() sin colapso: el conjunto de fechas no cambia con el colapso.
_SQL_FECHAS_ENTIDAD_TERMINOS = """
SELECT array_agg(DISTINCT metric_date ORDER BY metric_date)
  FROM search_term_observation
 WHERE ad_entity_id = %s
   AND metric_date BETWEEN %s AND %s
"""

# Watermark de plataforma: MAX(metric_date) colapsado (v_metric_latest) entre
# las entidades de la plataforma.
_SQL_WATERMARK_PLATAFORMA = """
SELECT max(v.metric_date)
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.platform = %s::platform
"""

_SQL_SYNC_PLATAFORMA = """
SELECT max(s.synced_at)
  FROM ad_entity_state s
  JOIN ad_entity e ON e.id = s.ad_entity_id
 WHERE e.platform = %s::platform
"""


# ---------------------------------------------------------------------------
# Parte pura: aritmetica de ventanas y comparadores de guarda
# ---------------------------------------------------------------------------


def inicio_ventana(window_end: dt.date) -> dt.date:
    """Inicio de la ventana 30d inclusive: fin - 29d."""
    return window_end - dt.timedelta(days=DIAS_VENTANA - 1)


def fin_ventana_bids(max_metric_date: dt.date) -> dt.date:
    """Bids: la ventana termina en max(metric_date) - 3d (dia en curso y los
    2 previos fuera; el reporte de hoy esta inflado ~1.5x)."""
    return max_metric_date - dt.timedelta(days=DIAS_FRESCURA_BIDS)


def fin_ventana_cortes(max_metric_date: dt.date, decided_at: dt.datetime) -> dt.date:
    """Cortes (pause/negative/harvest): min(max - 3d, decided_at(UTC) - 10d).
    decided_at DEBE ser tz-aware; su fecha se toma en UTC explicito (mismo
    principio que el trigger decision_madurez_corte)."""
    fecha_decision = _fecha_utc(decided_at)
    return min(
        fin_ventana_bids(max_metric_date),
        fecha_decision - dt.timedelta(days=DIAS_MADUREZ_CORTES),
    )


def salta_por_watermark(hoy: dt.date, watermark: dt.date) -> bool:
    """Comparador ESTRICTO: exactamente 7d de antiguedad NO salta."""
    return (hoy - watermark) > MAX_EDAD_WATERMARK


def salta_por_sync(ahora: dt.datetime, synced_at: dt.datetime) -> bool:
    """Comparador ESTRICTO: exactamente 48h sin sync NO salta."""
    return (ahora - synced_at) > MAX_EDAD_SYNC


def _fecha_utc(momento: dt.datetime) -> dt.date:
    """Fecha UTC de un datetime tz-aware; un naive evaluaria segun la TZ local
    del proceso y se rechaza ruidosamente (determinismo)."""
    if momento.tzinfo is None:
        raise ValueError(
            "decided_at/ahora debe ser tz-aware (UTC): un naive evaluaria segun la TZ local"
        )
    return momento.astimezone(dt.UTC).date()


# ---------------------------------------------------------------------------
# Parte con IO (SOLO lecturas; el conn llega por parametro)
# ---------------------------------------------------------------------------


def _max_fecha(conn: psycopg.Connection, ad_entity_id: int) -> dt.date | None:
    return conn.execute(_SQL_MAX_FECHA_ENTIDAD, (ad_entity_id,)).fetchone()[0]


def _agrega_metricas(
    conn: psycopg.Connection, ad_entity_id: int, window_start: dt.date, window_end: dt.date
) -> AgregadoMetricas:
    fila = conn.execute(_SQL_AGREGA_METRICAS, (ad_entity_id, window_start, window_end)).fetchone()
    fechas = tuple(fila[0]) if fila[0] else ()
    return AgregadoMetricas(
        window_start=window_start,
        window_end=window_end,
        fechas=fechas,
        metric_currency=fila[1],
        cost=fila[2],
        ad_revenue=fila[3],
        revenue_same_sku=fila[4],
        impressions=fila[5],
        clicks=fila[6],
        orders=fila[7],
        observed_at_max=fila[8],
    )


def ventana_bids(conn: psycopg.Connection, ad_entity_id: int) -> AgregadoMetricas | None:
    """Ventana de bids de UNA entidad: SU max(metric_date) - 3d, agregado sobre
    SU propia ventana. None si la entidad no tiene ninguna observacion."""
    max_fecha = _max_fecha(conn, ad_entity_id)
    if max_fecha is None:
        return None
    window_end = fin_ventana_bids(max_fecha)
    return _agrega_metricas(conn, ad_entity_id, inicio_ventana(window_end), window_end)


def ventana_cortes(
    conn: psycopg.Connection, ad_entity_id: int, decided_at: dt.datetime
) -> AgregadoMetricas | None:
    """Ventana de CORTES de una entidad: agregado SEPARADO cuya ventana termina
    en min(max - 3d, decided_at(UTC) - 10d). Se calcula desde cero sobre SU
    ventana: reutilizar el agregado de bids y "bajar" window_end esta prohibido
    y aqui es imposible por construccion."""
    max_fecha = _max_fecha(conn, ad_entity_id)
    if max_fecha is None:
        return None
    window_end = fin_ventana_cortes(max_fecha, decided_at)
    return _agrega_metricas(conn, ad_entity_id, inicio_ventana(window_end), window_end)


def terminos_cortes(
    conn: psycopg.Connection, ad_entity_id: int, decided_at: dt.datetime
) -> TerminosCortes:
    """Terminos (entity, termino) agregados sobre la ventana de CORTES de la
    entidad, colapsados a la ultima observacion por (entidad, termino, fecha).

    La ventana se ancla en max(metric_date) de las PROPIAS observaciones de
    terminos de la entidad (NO en v_metric_latest): los ad groups, donde viven
    los terminos, no tienen metricas en produccion -- no existe reporte de
    ad groups (REPORTES_CFG en app.ads.reports) -- y anclar en metricas
    devolveria () siempre (hallazgo codex+grok). La ventana la deriva esta
    funcion desde decided_at: el llamador no puede pasarle por error la de
    bids. window_start/window_end son None solo sin NINGUNA observacion."""
    max_fecha = conn.execute(_SQL_MAX_FECHA_TERMINOS, (ad_entity_id,)).fetchone()[0]
    if max_fecha is None:
        return TerminosCortes(
            ad_entity_id=ad_entity_id,
            window_start=None,
            window_end=None,
            fechas_entidad=(),
            terminos=(),
        )
    window_end = fin_ventana_cortes(max_fecha, decided_at)
    window_start = inicio_ventana(window_end)
    fila_fechas = conn.execute(
        _SQL_FECHAS_ENTIDAD_TERMINOS, (ad_entity_id, window_start, window_end)
    ).fetchone()
    filas = conn.execute(
        _SQL_TERMINOS_CORTES,
        (ad_entity_id, window_start, window_end),
    ).fetchall()
    return TerminosCortes(
        ad_entity_id=ad_entity_id,
        window_start=window_start,
        window_end=window_end,
        fechas_entidad=tuple(fila_fechas[0]) if fila_fechas[0] else (),
        terminos=tuple(
            AgregadoTermino(
                ad_entity_id=fila[0],
                search_term=fila[1],
                metric_currency=fila[2],
                cost=fila[3],
                ad_revenue=fila[4],
                clicks=fila[5],
                orders=fila[6],
                fechas_distintas=fila[7],
                is_asin_like=fila[8],
                observed_at_max=fila[9],
            )
            for fila in filas
        ),
    )


def ventanas_entidad(
    conn: psycopg.Connection, ad_entity_id: int, decided_at: dt.datetime
) -> VentanasEntidad:
    """Las DOS ventanas de una entidad + sus terminos en un solo pedido
    (conveniencia para 3.1). Cada agregado sale de su propia query sobre su
    propia ventana."""
    return VentanasEntidad(
        ad_entity_id=ad_entity_id,
        bids=ventana_bids(conn, ad_entity_id),
        cortes=ventana_cortes(conn, ad_entity_id, decided_at),
        terminos=terminos_cortes(conn, ad_entity_id, decided_at),
    )


def guarda_plataforma(
    conn: psycopg.Connection, platform: str, *, ahora: dt.datetime
) -> MotivoSkip | None:
    """Guardas de plataforma. None = seguir; MotivoSkip = plataforma saltada y
    su motivo (3.1 decide status skipped/degraded y lo persiste). UN SOLO
    reloj por llamada: `ahora` llega por parametro (determinismo), debe ser
    tz-aware, y el `hoy` del watermark se DERIVA de el en UTC (dos relojes
    manuales independientes medirian la edad contra fechas que nadie
    garantiza coherentes; regla 2).

    - watermark: MAX(metric_date) en v_metric_latest entre las entidades de la
      plataforma; (hoy - watermark) > 7d -> salta.
    - frescura de estructura: MAX(ad_entity_state.synced_at);
      (ahora - synced_at) > 48h -> salta.
    - sin metricas NI estado -> 'sin_datos'; falta solo una de las dos -> salta
      por su guarda (fail-closed: metricas sin estructura no son operables)."""
    hoy = _fecha_utc(ahora)  # un naive revienta aqui con mensaje claro, no en la resta
    watermark = conn.execute(_SQL_WATERMARK_PLATAFORMA, (platform,)).fetchone()[0]
    synced_at = conn.execute(_SQL_SYNC_PLATAFORMA, (platform,)).fetchone()[0]
    if watermark is None and synced_at is None:
        return MotivoSkip(
            guarda="sin_datos",
            detalle=f"plataforma {platform} sin metricas ni estado de estructura",
        )
    if watermark is None:
        return MotivoSkip(
            guarda="watermark",
            detalle=f"plataforma {platform} sin observaciones en v_metric_latest",
        )
    if synced_at is None:
        return MotivoSkip(
            guarda="synced_at",
            detalle=f"plataforma {platform} sin filas en ad_entity_state",
        )
    if salta_por_watermark(hoy, watermark):
        return MotivoSkip(
            guarda="watermark",
            detalle=(
                f"plataforma {platform}: watermark {watermark.isoformat()} "
                f"> {MAX_EDAD_WATERMARK.days}d respecto de {hoy.isoformat()}"
            ),
        )
    if salta_por_sync(ahora, synced_at):
        return MotivoSkip(
            guarda="synced_at",
            detalle=(
                f"plataforma {platform}: estructura sincronizada hace "
                f"{ahora - synced_at} > {MAX_EDAD_SYNC.total_seconds() / 3600:g}h"
            ),
        )
    return None
