"""El episodio de temporada y sus efectos (TEMPORADA 01 B).

`app.temporada_io` es el UNICO escritor de episodios: abre y cierra
`temporada_episodio`, reconcilia y abre `temporada_efecto` y guarda fines y
exclusiones — siempre en TX3 (la misma transaccion que las decisiones del
ciclo) o desde la confirmacion del aplicador (la misma transaccion que
`decision_application`). Las lecturas de TX2 son solo SELECT: episodio
abierto, efectos en vuelo u obsoletos, excluidas y cerca de plataforma.

En practica (ads_temporada_<plat> en shadow) NO se escribe nada: ni episodio,
ni fin, ni exclusion (el caller solo entra aqui en live). Los cierres de
efecto en `_confirma_resumen` y `registra_sin_aplicar` son la contraparte del
aplicador y viven aqui como funciones que esos modulos llaman.

Firmas del diseno (docs/evidencia/temporada-01/DISENO.md, «Firmas y
seudocodigo»): una diferencia registrada contra el seudocodigo —
`registra_ciclo` recibe `config_version_id` porque la columna es NOT NULL y
la identidad del config que abrio el episodio es dato del ciclo (el
seudocodigo no la traia).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Literal

import psycopg

from app.db import settings_vigentes
from app.lecturas_historia import historia_de_hoja
from app.optimizer import temporada
from app.optimizer.recuperacion import TOLERANCIA_CADENA, _cadena_desde
from app.optimizer.temporada import (
    EpisodioPresupuesto,
    EpisodioVigente,
    FinReduccion,
    FronteraHoja,
)

MOTIVO_ENTRA = "entra_temporada_baja"
MOTIVO_AJUSTA = "ajusta_temporada_baja"
MOTIVO_SALE = "sale_temporada_baja"
MOTIVOS_TEMPORADA = (MOTIVO_ENTRA, MOTIVO_AJUSTA, MOTIVO_SALE)

# inputs.motivo -> temporada_efecto.tipo
_TIPO_POR_MOTIVO = {MOTIVO_ENTRA: "entra", MOTIVO_AJUSTA: "ajusta", MOTIVO_SALE: "sale"}

# La cerca de plataforma caduca a los 90 dias (temporada.DIAS_CERCA: una fuente).


@dataclass(frozen=True)
class EstadoTemporada:
    """Lo que TX2 lee UNA vez por plataforma (solo SELECT)."""

    episodio: EpisodioVigente | None
    # efectos abiertos por hoja: 'en_vuelo' (ledger sin sello) o 'obsoleto'
    # (desenlace ya escrito, o ciclo anterior sin intento). Una hoja con
    # efecto 'en_vuelo' no recibe decision de temporada; una con 'obsoleto'
    # si (TX3 lo cierra antes).
    efectos_abiertos: dict[int, Literal["en_vuelo", "obsoleto"]]
    excluidas: dict[int, frozenset[int]]  # hoja -> episodios donde David movio
    cerca_plataforma: dt.date | None


@dataclass(frozen=True)
class TemporadaPlataforma:
    """Lo que el ciclo congela UNA vez por plataforma para decidir y estampar."""

    estacion: temporada.Estacion
    modo: Literal["shadow", "live"]
    config: temporada.ConfigTemporada
    meta_alta_pct: Decimal
    estado: EstadoTemporada

    @property
    def en_vivo(self) -> bool:
        return self.modo == "live"


@dataclass(frozen=True)
class Reactivacion:
    """Una pausa de Orbit de una baja ya cerrada que toca reactivar (bloque F)."""

    ad_entity_id: int
    queue_id: int
    pausa_decision_id: int
    episodio_id: int

    def como_json(self) -> dict:
        return {
            "ad_entity_id": self.ad_entity_id,
            "queue_id": self.queue_id,
            "pausa_decision_id": self.pausa_decision_id,
            "episodio_id": self.episodio_id,
        }


_SQL_EPISODIO_ABIERTO = """
SELECT id, abierto_el FROM temporada_episodio
 WHERE platform = %s::platform AND cerrado_el IS NULL
"""

# Un efecto abierto es 'obsoleto' si su decision ya tiene desenlace
# (decision_application o decision_sin_aplicar) o si es de un ciclo que ya
# termino sin NINGUN intento en el ledger (solo se envian los bids del ciclo);
# 'en_vuelo' si su ledger quedo sin sello (apply.hojas_con_bid_sin_sello ya
# bloquea el movimiento; aqui la hoja entera espera).
_SQL_EFECTOS_ABIERTOS = """
SELECT d.ad_entity_id,
       CASE WHEN da.decision_id IS NOT NULL OR sa.decision_id IS NOT NULL THEN 'obsoleto'
            WHEN EXISTS (SELECT 1 FROM apply_attempt a
                          WHERE a.decision_id = e.decision_id
                            AND a.tipo = 'normal'
                            AND a.resultado = 'fallo:readback_ambiguo') THEN 'en_vuelo'
            WHEN EXISTS (SELECT 1 FROM apply_attempt a
                          WHERE a.decision_id = e.decision_id
                            AND a.tipo = 'normal'
                            AND a.finished_at IS NULL) THEN 'en_vuelo'
            WHEN EXISTS (SELECT 1 FROM apply_attempt a
                          WHERE a.decision_id = e.decision_id
                            AND a.tipo = 'normal') THEN 'obsoleto'
            WHEN oc.status IS DISTINCT FROM 'running' THEN 'obsoleto'
            ELSE 'en_vuelo' END
  FROM temporada_efecto e
  JOIN decision d         ON d.id = e.decision_id
  JOIN ad_entity k        ON k.id = d.ad_entity_id AND k.platform = %s::platform
  JOIN optimizer_cycle oc ON oc.id = d.cycle_id
  LEFT JOIN decision_application da ON da.decision_id = e.decision_id
  LEFT JOIN decision_sin_aplicar sa ON sa.decision_id = e.decision_id
 WHERE e.cerrado_el IS NULL
"""

_SQL_EXCLUIDAS = """
SELECT x.ad_entity_id, x.episodio_id
  FROM temporada_exclusion x
  JOIN temporada_episodio e ON e.id = x.episodio_id
 WHERE e.platform = %s::platform
"""

# La cerca de la plataforma: el dia UTC siguiente a la PRIMERA confirmacion de
# la ULTIMA TRANSICION (entrada o salida) de la plataforma. Un ajuste de la
# reduccion a media baja NO es transicion: no reinicia la cerca.
_SQL_CERCA_PLATAFORMA = """
SELECT max(primera)
  FROM (
    SELECT min(c.confirmado_el) AS primera
      FROM v_cambio_bid c
      JOIN temporada_episodio e ON e.id = c.episodio_id AND e.platform = %s::platform
     WHERE c.origen = 'temporada'
       AND c.motivo_temporada IN ('entra', 'sale')
     GROUP BY e.id, c.motivo_temporada
  ) t
"""


def _lee_cerca_plataforma(conn: psycopg.Connection, platform: str, hoy: dt.date) -> dt.date | None:
    primera = conn.execute(_SQL_CERCA_PLATAFORMA, (platform,)).fetchone()[0]
    if primera is None:
        return None
    cerca = (primera.astimezone(dt.UTC) + dt.timedelta(days=1)).date()
    if cerca < hoy - dt.timedelta(days=temporada.DIAS_CERCA):
        return None
    return cerca


def lee_estado(conn: psycopg.Connection, platform: str, hoy: dt.date) -> EstadoTemporada:
    """Solo SELECT (TX2). Un efecto abierto es 'obsoleto' si su decision ya
    tiene desenlace o quedo sin intento en un ciclo terminado; 'en_vuelo' si su
    ledger quedo sin sello. cerca_plataforma = dia siguiente a la primera
    confirmacion de la ultima transicion de la plataforma, si cae en los
    ultimos 90 dias."""
    fila = conn.execute(_SQL_EPISODIO_ABIERTO, (platform,)).fetchone()
    episodio = EpisodioVigente(id=fila[0], abierto_el=fila[1]) if fila is not None else None
    efectos: dict[int, Literal["en_vuelo", "obsoleto"]] = {}
    for hoja, estado in conn.execute(_SQL_EFECTOS_ABIERTOS, (platform,)).fetchall():
        efectos[int(hoja)] = estado
    excluidas: dict[int, set[int]] = {}
    for hoja, episodio_id in conn.execute(_SQL_EXCLUIDAS, (platform,)).fetchall():
        excluidas.setdefault(int(hoja), set()).add(int(episodio_id))
    cerca = _lee_cerca_plataforma(conn, platform, hoy)
    return EstadoTemporada(
        episodio=episodio,
        efectos_abiertos=efectos,
        excluidas={h: frozenset(e) for h, e in excluidas.items()},
        cerca_plataforma=cerca,
    )


def episodio_vigente_en_estacion(
    episodio: EpisodioVigente | None, estacion: temporada.Estacion
) -> EpisodioVigente | None:
    """Normaliza un episodio abierto de otra baja: si estamos en baja y el
    episodio abierto es anterior al bloque actual, ya no es el de esta baja."""
    if (
        estacion.fase == "baja"
        and episodio is not None
        and episodio.abierto_el.astimezone(dt.UTC).date() < estacion.inicio_bloque
    ):
        return None
    return episodio


_SQL_RECONCILIA_DESENLACE = """
UPDATE temporada_efecto e SET cerrado_el = now(),
       desenlace = CASE
           WHEN da.decision_id IS NOT NULL THEN
               CASE WHEN da.verify_ok IS TRUE THEN 'confirmado' ELSE 'divergencia' END
           ELSE CASE WHEN sa.motivo = 'bid_cambiado_fuera' THEN 'mano_del_dueno'
                     ELSE 'sin_aplicar' END END
  FROM decision d
  JOIN ad_entity k ON k.id = d.ad_entity_id AND k.platform = %s::platform
  LEFT JOIN decision_application da ON da.decision_id = d.id
  LEFT JOIN decision_sin_aplicar sa ON sa.decision_id = d.id
 WHERE e.decision_id = d.id AND e.cerrado_el IS NULL
   AND (da.decision_id IS NOT NULL OR sa.decision_id IS NOT NULL)
"""

_SQL_RECONCILIA_ABANDONADO = """
UPDATE temporada_efecto e SET cerrado_el = now(), desenlace = 'abandonado'
  FROM decision d
  JOIN ad_entity k        ON k.id = d.ad_entity_id AND k.platform = %s::platform
  JOIN optimizer_cycle oc ON oc.id = d.cycle_id
 WHERE e.decision_id = d.id AND e.cerrado_el IS NULL
   AND NOT EXISTS (SELECT 1 FROM decision_application da WHERE da.decision_id = d.id)
   AND NOT EXISTS (SELECT 1 FROM decision_sin_aplicar sa WHERE sa.decision_id = d.id)
   AND NOT EXISTS (SELECT 1 FROM apply_attempt a WHERE a.decision_id = d.id)
   AND oc.status IS DISTINCT FROM 'running'
"""

# Cinturon de la revision (TEMPORADA 01 B): un intento SELLADO sin desenlace
# (p. ej. fallo:readback_ambiguo de una era sin registro terminal) deja el
# efecto abierto y TX3 reventaria contra el indice unico al decidir otra vez.
# El cierre es terminal ('sin_aplicar': no se confirmo nada) y SOLO si TODOS
# los intentos de la decision estan sellados: un intento sin sello sigue en
# vuelo y el efecto queda abierto (la hoja espera). Los intentos con lectura
# AMBIGUA quedan FUERA: su resultado es desconocido (Amazon puede haber
# aplicado el cambio) y los retoma reconcilia_bids con GET fresco sin
# reenviar el PUT.
_SQL_RECONCILIA_SELLADO_SIN_DESENLACE = """
UPDATE temporada_efecto e SET cerrado_el = now(), desenlace = 'sin_aplicar'
  FROM decision d
  JOIN ad_entity k ON k.id = d.ad_entity_id AND k.platform = %s::platform
 WHERE e.decision_id = d.id AND e.cerrado_el IS NULL
   AND NOT EXISTS (SELECT 1 FROM decision_application da WHERE da.decision_id = d.id)
   AND NOT EXISTS (SELECT 1 FROM decision_sin_aplicar sa WHERE sa.decision_id = d.id)
   AND EXISTS (SELECT 1 FROM apply_attempt a
                WHERE a.decision_id = d.id AND a.tipo = 'normal' AND a.finished_at IS NOT NULL)
   AND NOT EXISTS (SELECT 1 FROM apply_attempt a
                    WHERE a.decision_id = d.id AND a.tipo = 'normal' AND a.finished_at IS NULL)
   AND NOT EXISTS (SELECT 1 FROM apply_attempt a
                    WHERE a.decision_id = d.id AND a.tipo = 'normal'
                      AND a.resultado = 'fallo:readback_ambiguo')
"""

_SQL_INSERT_EPISODIO = """
INSERT INTO temporada_episodio (platform, abierto_el, abierto_en_ciclo, config_version_id)
VALUES (%s::platform, %s, %s, %s) RETURNING id
"""

_SQL_CIERRA_EPISODIO = """
UPDATE temporada_episodio SET cerrado_el = %s, cerrado_en_ciclo = %s
 WHERE id = %s AND cerrado_el IS NULL
"""

_SQL_INSERT_FIN = """
INSERT INTO temporada_fin_reduccion (episodio_id, ad_entity_id, motivo, cycle_id)
VALUES (%s, %s, %s, %s)
ON CONFLICT (episodio_id, ad_entity_id) DO NOTHING
"""

_SQL_INSERT_EXCLUSION = """
INSERT INTO temporada_exclusion (episodio_id, ad_entity_id, motivo, bid_visto,
                                 bid_visto_moneda, decision_id)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (episodio_id, ad_entity_id) DO NOTHING
"""


def _reconcilia_efectos(conn: psycopg.Connection, platform: str) -> None:
    """Paso 1 de registra_ciclo: cerrar los efectos abiertos que ya tienen
    desenlace (cinturon: el camino normal los cierra en la misma transaccion
    del desenlace), los que quedaron sin NINGUN intento en un ciclo terminado
    (crash antes del ledger): 'abandonado', y los con TODOS los intentos
    sellados sin desenlace (sello terminal sin registro): 'sin_aplicar' —
    sin este cierre, el indice unico revienta TX3 en cada ciclo."""
    conn.execute(_SQL_RECONCILIA_DESENLACE, (platform,))
    conn.execute(_SQL_RECONCILIA_ABANDONADO, (platform,))
    conn.execute(_SQL_RECONCILIA_SELLADO_SIN_DESENLACE, (platform,))


def registra_ciclo(
    conn: psycopg.Connection,
    platform: str,
    *,
    cycle_id: int,
    decidido_el: dt.datetime,
    plataforma: TemporadaPlataforma | None,
    config_version_id: int | None,
) -> EpisodioVigente | None:
    """TX3, antes de insertar decisiones. Solo en modo live de temporada.
    Reconcilia efectos, abre episodio en baja sin episodio, cierra en salida y
    abre uno nuevo si el abierto es de otra baja. Un segundo INSERT concurrente
    choca el indice unico `temporada_episodio_uno_abierto` y el ciclo falla
    cerrado. Devuelve el episodio abierto tras todo, o None."""
    if plataforma is None or not plataforma.en_vivo:
        return None
    estacion = plataforma.estacion
    _reconcilia_efectos(conn, platform)
    ep = None
    fila = conn.execute(_SQL_EPISODIO_ABIERTO, (platform,)).fetchone()
    if fila is not None:
        ep = EpisodioVigente(id=fila[0], abierto_el=fila[1])
    if estacion.fase == "baja":
        de_otra_baja = (
            ep is not None and ep.abierto_el.astimezone(dt.UTC).date() < estacion.inicio_bloque
        )
        if de_otra_baja:
            conn.execute(_SQL_CIERRA_EPISODIO, (decidido_el, cycle_id, ep.id))
            ep = None
        if ep is None:
            if config_version_id is None:
                raise ValueError(
                    "temporada live en baja sin config_version_id del ciclo:"
                    " el episodio no se abre sin su config (regla 3)"
                )
            nuevo = conn.execute(
                _SQL_INSERT_EPISODIO,
                (platform, decidido_el, cycle_id, config_version_id),
            ).fetchone()
            ep = EpisodioVigente(id=nuevo[0], abierto_el=decidido_el)
        return ep
    if ep is not None:
        conn.execute(_SQL_CIERRA_EPISODIO, (decidido_el, cycle_id, ep.id))
    return None


def registra_fines(
    conn: psycopg.Connection,
    platform: str,
    *,
    cycle_id: int,
    episodio_abierto: int | None,
    fines: dict[int, FinReduccion],
    exclusiones: dict[int, Decimal],
    moneda: str | None,
) -> None:
    """TX3, en la misma transaccion que el INSERT de decisiones. Solo en modo
    live. Guarda el fin de TODA hoja leida por TX2 con `FronteraHoja.fin`
    (tambien las que terminan en Mantener o inertes) y las exclusiones de la
    mano sin fecha vista hoy, con su bid_visto."""
    for hoja in sorted(fines):
        fin = fines[hoja]
        conn.execute(_SQL_INSERT_FIN, (fin.episodio_id, hoja, fin.motivo, cycle_id))
        if fin.excluye_abierto:
            _excluye(conn, episodio_abierto, hoja, "mano_del_dueno", fin.bid_visto, moneda, None)
    for hoja in sorted(exclusiones):
        _excluye(conn, episodio_abierto, hoja, "mano_del_dueno", exclusiones[hoja], moneda, None)


def _excluye(
    conn: psycopg.Connection,
    episodio_id: int | None,
    hoja: int,
    motivo: str,
    bid_visto: Decimal | None,
    moneda: str | None,
    decision_id: int | None,
) -> None:
    if episodio_id is None:
        raise ValueError(
            "exclusion de temporada sin episodio abierto: la mano se vio sin"
            " baja y no se guarda (bug: registra_ciclo abre el episodio antes)"
        )
    conn.execute(
        _SQL_INSERT_EXCLUSION,
        (
            episodio_id,
            hoja,
            motivo,
            bid_visto,
            moneda if bid_visto is not None else None,
            decision_id,
        ),
    )


def estampa(pendiente: Any, episodio_id: int | None) -> Any:
    """inputs.temporada_episodio_id = frontera.episodio_id o el abierto en este
    TX3, en las decisiones de temporada y en las pausas con fase_hoja == "baja".
    Fuera de inputs.caso: el replay no lo lee. Con episodio None no estampa."""
    if episodio_id is None:
        return pendiente
    inputs = pendiente.inputs or {}
    es_temporada = inputs.get("motivo") in MOTIVOS_TEMPORADA
    es_pausa_de_baja = False
    if not es_temporada and pendiente.kind == "pause":
        caso = inputs.get("caso")
        temp = caso.get("temporada") if isinstance(caso, Mapping) else None
        frontera = temp.get("frontera") if isinstance(temp, Mapping) else None
        es_pausa_de_baja = isinstance(frontera, Mapping) and frontera.get("fase_hoja") == "baja"
    if not (es_temporada or es_pausa_de_baja):
        return pendiente
    copia = dict(inputs)
    copia.setdefault("temporada_episodio_id", episodio_id)
    return replace(pendiente, inputs=copia)


def abre_efectos(conn: psycopg.Connection, episodio_id: int | None, pendientes, ids) -> None:
    """TX3, despues del INSERT de decisiones: una fila temporada_efecto por
    decision entra/ajusta/sale. El episodio del efecto es el ESTAMPADO en la
    decision (el regreso lleva el episodio que origino su reduccion aunque
    TX3 lo acabe de cerrar); el abierto en este TX3 cubre las decisiones que
    aun no traen identificador. SIN ON CONFLICT: el choque del indice unico
    'temporada_efecto_uno_pendiente' es un bug (TX2 ya excluyo hojas en
    vuelo) y el ciclo falla cerrado."""
    if episodio_id is None and not any(
        (p.inputs or {}).get("temporada_episodio_id") for p in pendientes
    ):
        return
    for pendiente, decision_id in zip(pendientes, ids, strict=True):
        tipo = _TIPO_POR_MOTIVO.get((pendiente.inputs or {}).get("motivo"))
        if tipo is None:
            continue
        ep = (pendiente.inputs or {}).get("temporada_episodio_id") or episodio_id
        if ep is None:
            raise ValueError(
                "decision de temporada sin episodio estampado ni abierto en TX3: "
                "la fila temporada_efecto exige su episodio (bug de estampado)"
            )
        conn.execute(
            "INSERT INTO temporada_efecto (episodio_id, ad_entity_id, decision_id, tipo)"
            " VALUES (%s, %s, %s, %s)",
            (ep, pendiente.ad_entity_id, decision_id, tipo),
        )


_SQL_EPISODIOS = """
SELECT id, abierto_el, cerrado_el
  FROM temporada_episodio
 WHERE platform = %s::platform
 ORDER BY cerrado_el IS NOT NULL, cerrado_el DESC NULLS FIRST, id DESC
 LIMIT 2
"""


def episodios(conn: psycopg.Connection, platform: str) -> tuple[EpisodioPresupuesto, ...]:
    """El abierto y el ultimo cerrado, para la pantalla de dinero (bloque G)."""
    filas = conn.execute(_SQL_EPISODIOS, (platform,)).fetchall()
    return tuple(
        EpisodioPresupuesto(
            id=f[0],
            abierto_el=f[1].astimezone(dt.UTC).date(),
            cerrado_el=f[2].astimezone(dt.UTC).date() if f[2] is not None else None,
        )
        for f in filas
    )


def sql_pausas_de_baja(
    *,
    select_cols: str,
    joins_extra: str = "",
    where_extra: str = "",
    order_by: str = "",
) -> str:
    """Unica fuente SQL de pausas confirmadas de Orbit en temporada baja
    (compartida por `reactivaciones_pendientes` y `dashboard_temporada.pausadas_de_baja`).
    Orden posicional de parametros: `(plataforma, *params_de_where_extra, mantener)`."""
    bloques_join = f"\n{joins_extra.rstrip()}" if joins_extra.strip() else ""
    bloque_where = f"\n{where_extra.rstrip()}" if where_extra.strip() else ""
    bloque_order = f"\n{order_by.rstrip()}" if order_by.strip() else ""
    return (
        f"SELECT {select_cols}\n"
        "  FROM apply_queue q\n"
        "  JOIN decision d               ON d.id = q.decision_id\n"
        "  JOIN decision_application da  ON da.decision_id = d.id AND da.verify_ok IS TRUE\n"
        f"  JOIN ad_entity_state s        ON s.ad_entity_id = q.ad_entity_id{bloques_join}\n"
        " WHERE q.platform = %s::platform\n"
        "   AND q.kind = 'pause' AND q.modo = 'live' AND q.estado = 'applied'\n"
        "   AND d.inputs->'caso'->'temporada'->'frontera'->>'fase_hoja' = 'baja'\n"
        f"   AND s.status = 'PAUSED'{bloque_where}\n"
        "   AND NOT (q.ad_entity_id = ANY(%s::bigint[]))\n"
        "   AND NOT EXISTS (SELECT 1 FROM apply_attempt a\n"
        "                    WHERE a.decision_id = q.decision_id AND a.tipo = 'reversa'\n"
        "                    GROUP BY a.decision_id\n"
        "                   HAVING bool_or(\n"
        "                       a.resultado IN ('ok', 'omitida:pausada_tras_habilitar')\n"
        "                   ) OR count(*) >= 3)\n"
        "   AND NOT EXISTS (SELECT 1 FROM reactivacion_manual r\n"
        "                    WHERE r.ad_entity_id = q.ad_entity_id\n"
        "                      AND r.detectada_en > q.applied_at)\n"
        "   AND q.id = (SELECT q2.id FROM apply_queue q2\n"
        "                WHERE q2.ad_entity_id = q.ad_entity_id AND q2.kind = 'pause'\n"
        "                  AND q2.estado = 'applied'\n"
        f"                ORDER BY q2.applied_at DESC LIMIT 1){bloque_order}\n"
    )


_SQL_PAUSAS_DE_BAJA = sql_pausas_de_baja(
    select_cols="q.ad_entity_id, q.id, q.decision_id, e.id",
    joins_extra=(
        "  JOIN temporada_episodio e     ON e.id = (d.inputs->>'temporada_episodio_id')::bigint\n"
        "                               AND e.cerrado_el IS NOT NULL"
    ),
)


def reactivaciones_pendientes(
    conn: psycopg.Connection, platform: str, mantener: frozenset[int]
) -> tuple[Reactivacion, ...]:
    """Pausas de Orbit confirmadas, decididas en una baja ya cerrada, que toca
    reactivar (TEMPORADA 01 F). Solo SELECT e idempotente: una reversa ok o
    agotada sale del conjunto, igual que una hoja que el dueno reactivo a mano,
    una en «Mantener pausada» o una pausa que ya no es la ultima de la hoja."""
    filas = conn.execute(_SQL_PAUSAS_DE_BAJA, (platform, list(mantener))).fetchall()
    return tuple(
        Reactivacion(ad_entity_id=f[0], queue_id=f[1], pausa_decision_id=f[2], episodio_id=f[3])
        for f in filas
    )


def cierra_efecto_resumen(
    conn: psycopg.Connection, *, decision_id: int, verify_ok: bool, leido: Decimal | None
) -> None:
    """La MISMA transaccion que decision_application (_confirma_resumen):
    cierra el efecto con 'confirmado' o 'divergencia'; en divergencia de una
    decision de temporada, excluye la hoja SOLO si el bid leido difiere tambien
    del old_value (si Amazon tiene el bid de antes, el cambio no llego: sin
    exclusion, el efecto cierra y manana la hoja se deriva otra vez)."""
    conn.execute(
        "UPDATE temporada_efecto SET cerrado_el = now(),"
        " desenlace = CASE WHEN %s THEN 'confirmado' ELSE 'divergencia' END"
        " WHERE decision_id = %s AND cerrado_el IS NULL",
        (verify_ok, decision_id),
    )
    if verify_ok or leido is None:
        return
    fila = conn.execute(
        "SELECT e.episodio_id, e.ad_entity_id, d.old_value, d.value_currency"
        " FROM temporada_efecto e JOIN decision d ON d.id = e.decision_id"
        " WHERE e.decision_id = %s",
        (decision_id,),
    ).fetchone()
    if fila is None:
        return  # la decision no era de temporada: nada que excluir
    episodio_id, hoja, old_value, moneda = fila
    if old_value is None or abs(leido - Decimal(old_value)) <= TOLERANCIA_CADENA:
        return  # Amazon tiene el bid de antes: el cambio no llego, sin exclusion
    _excluye(conn, episodio_id, hoja, "divergencia", leido, moneda, decision_id)


def excluye_mano_del_aplicador(
    conn: psycopg.Connection, *, decision_id: int, leido: Decimal
) -> None:
    """La lectura fresca del aplicador encontro en Amazon un bid distinto del
    que Orbit vio al decidir: excluye la hoja del episodio de la decision.

    Solo las decisiones de temporada tienen fila en temporada_efecto (TX3 la
    abre); fuera de temporada no hay episodio que excluir y no se escribe nada.
    Se llama en la MISMA transaccion que `registra_sin_aplicar` (que cierra el
    efecto con 'mano_del_dueno'); esta guarda la exclusion durable que el
    siguiente ciclo lee en TX2."""
    fila = conn.execute(
        "SELECT e.episodio_id, e.ad_entity_id, d.value_currency"
        "  FROM temporada_efecto e JOIN decision d ON d.id = e.decision_id"
        " WHERE e.decision_id = %s",
        (decision_id,),
    ).fetchone()
    if fila is None:
        return
    _excluye(conn, fila[0], fila[1], "mano_del_dueno", leido, fila[2], decision_id)


def cierra_efecto_sin_aplicar(conn: psycopg.Connection, *, decision_id: int, motivo: str) -> None:
    """La MISMA transaccion que decision_sin_aplicar (registra_sin_aplicar):
    cierra el efecto con 'sin_aplicar', o 'mano_del_dueno' cuando el motivo es
    bid_cambiado_fuera (Amazon ya tenia otra mano). Idempotente."""
    desenlace = "mano_del_dueno" if motivo == "bid_cambiado_fuera" else "sin_aplicar"
    conn.execute(
        "UPDATE temporada_efecto SET cerrado_el = now(), desenlace = %s"
        " WHERE decision_id = %s AND cerrado_el IS NULL",
        (desenlace, decision_id),
    )


_SQL_BID_HOJA = """
SELECT s.current_bid
  FROM ad_entity k
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = k.id
 WHERE k.id = %s
"""


@dataclass(frozen=True)
class ContextoRevalidacion:
    """Estado de temporada de la plataforma cargado UNA sola vez por barrido de
    `libera_vencidos`, evitando repetir config/estado/episodio en cada fila."""

    platform: str
    hoy: dt.date
    estacion: temporada.Estacion
    estado: EstadoTemporada
    desde_historia: dt.date


def _config_live(conn: psycopg.Connection, platform: str) -> temporada.ConfigTemporada | None:
    """ConfigTemporada si `ads_temporada_<platform>` esta en `live`; None en otro caso."""
    crudo = settings_vigentes(conn)
    if not isinstance(crudo, Mapping):
        return None
    if temporada.modo_desde_settings(crudo, platform) != "live":
        return None
    return temporada.desde_settings(crudo)


def lee_contexto_revalidacion(
    conn: psycopg.Connection, platform: str, hoy: dt.date
) -> ContextoRevalidacion | None:
    """Precarga una vez por plataforma el estado de temporada para revalidar
    cortes en `libera_vencidos`. None sin config o temporada fuera de live."""
    config = _config_live(conn, platform)
    if config is None:
        return None
    estacion = temporada.resuelve(config, hoy)
    estado = lee_estado(conn, platform, hoy)
    desde = hoy - dt.timedelta(days=365)
    if estado.episodio is not None:
        desde = min(desde, estado.episodio.abierto_el.astimezone(dt.UTC).date())
    estado = replace(estado, episodio=episodio_vigente_en_estacion(estado.episodio, estacion))
    return ContextoRevalidacion(
        platform=platform,
        hoy=hoy,
        estacion=estacion,
        estado=estado,
        desde_historia=desde,
    )


def mult_negativa(conn: psycopg.Connection, platform: str, hoy: dt.date) -> Decimal | None:
    """El multiplicador del dueno para los NEGATIVES cuando la plataforma
    decide la baja en live (TEMPORADA 01 D, al liberar): la estacion del
    config vigente. None sin temporada, en shadow o fuera de baja."""
    config = _config_live(conn, platform)
    if config is None:
        return None
    estacion = temporada.resuelve(config, hoy)
    return estacion.mult_cortes_baja if estacion.fase == "baja" else None


def cerca_plataforma(conn: psycopg.Connection, platform: str, hoy: dt.date) -> dt.date | None:
    """La cerca de la PLATAFORMA (decision 8 del plan): el dia UTC siguiente a
    la primera confirmacion de la ultima transicion (entrada o salida) — lo
    que grupo y cuenta usan para sus ventanas de falla. None sin temporada
    encendida o sin transicion en 90 dias."""
    if _config_live(conn, platform) is None:
        return None
    return _lee_cerca_plataforma(conn, platform, hoy)


def frontera_hoja(
    conn: psycopg.Connection,
    ad_entity_id: int,
    hoy: dt.date,
    *,
    piso: Decimal,
    techo: Decimal,
    contexto: ContextoRevalidacion | None = None,
) -> FronteraHoja | None:
    """La misma temporada.frontera para UNA hoja, para la revalidacion de la
    cola al liberar (la conecta el bloque D). None sin config o temporada apagada."""
    if contexto is None:
        plataforma = conn.execute(
            "SELECT platform::text FROM ad_entity WHERE id = %s", (ad_entity_id,)
        ).fetchone()
        if plataforma is None:
            return None
        contexto = lee_contexto_revalidacion(conn, plataforma[0], hoy)
        if contexto is None:
            return None
    historia = historia_de_hoja(conn, ad_entity_id, contexto.desde_historia)
    bid_cache = conn.execute(_SQL_BID_HOJA, (ad_entity_id,)).fetchone()
    bid = bid_cache[0] if bid_cache is not None else None
    cadena = _cadena_desde(historia, bid)
    return temporada.frontera(
        contexto.estacion,
        cadena,
        episodio=contexto.estado.episodio,
        excluida_en=contexto.estado.excluidas.get(ad_entity_id, frozenset()),
        cerca_plataforma=contexto.estado.cerca_plataforma,
        bid=bid,
        piso=piso,
        techo=techo,
    )
