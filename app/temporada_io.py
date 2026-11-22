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
from app.optimizer import temporada
from app.optimizer.caso import CambioFechado, HistoriaBid, ManoExterna
from app.optimizer.recuperacion import TOLERANCIA_CADENA, _cadena_desde
from app.optimizer.temporada import EpisodioVigente, FinReduccion, FronteraHoja

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


@dataclass(frozen=True)
class EpisodioPresupuesto:
    """Hechos de un episodio para la pantalla de dinero (bloque G)."""

    id: int
    abierto_el: dt.date
    cerrado_el: dt.date | None


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
                            AND a.resultado = 'fallo:readback_ambiguo') THEN 'en_vuelo'
            WHEN EXISTS (SELECT 1 FROM apply_attempt a
                          WHERE a.decision_id = e.decision_id
                            AND a.finished_at IS NULL) THEN 'en_vuelo'
            WHEN EXISTS (SELECT 1 FROM apply_attempt a
                          WHERE a.decision_id = e.decision_id) THEN 'obsoleto'
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
    cerca = None
    primera = conn.execute(_SQL_CERCA_PLATAFORMA, (platform,)).fetchone()[0]
    if primera is not None:
        cerca = (primera.astimezone(dt.UTC) + dt.timedelta(days=1)).date()
        if cerca < hoy - dt.timedelta(days=temporada.DIAS_CERCA):
            cerca = None
    return EstadoTemporada(
        episodio=episodio,
        efectos_abiertos=efectos,
        excluidas={h: frozenset(e) for h, e in excluidas.items()},
        cerca_plataforma=cerca,
    )


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
                WHERE a.decision_id = d.id AND a.finished_at IS NOT NULL)
   AND NOT EXISTS (SELECT 1 FROM apply_attempt a
                    WHERE a.decision_id = d.id AND a.finished_at IS NULL)
   AND NOT EXISTS (SELECT 1 FROM apply_attempt a
                    WHERE a.decision_id = d.id AND a.resultado = 'fallo:readback_ambiguo')
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
    if plataforma is None or plataforma.modo != "live":
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
    inputs = dict(pendiente.inputs)
    inputs.setdefault("temporada_episodio_id", episodio_id)
    return replace(pendiente, inputs=inputs)


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


_SQL_CADENA_HOJA = """
SELECT c.confirmado_el, c.decision_id, c.bid_antes, c.bid_despues, c.origen,
       c.motivo_temporada, c.episodio_id
  FROM v_cambio_bid c
 WHERE c.hoja_id = %s AND c.confirmado_el >= %s AND c.origen <> 'ajuste_de_campana'
 UNION ALL
SELECT x.detectada_el, NULL, x.bid_visto, x.bid_visto, 'exclusion', NULL, x.episodio_id
  FROM temporada_exclusion x
 WHERE x.ad_entity_id = %s AND x.bid_visto IS NOT NULL AND x.detectada_el >= %s
 ORDER BY 1, 2
"""

_SQL_FINES_HOJA = """
SELECT f.episodio_id FROM temporada_fin_reduccion f WHERE f.ad_entity_id = %s
"""

_SQL_BID_HOJA = """
SELECT s.current_bid
  FROM ad_entity k
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = k.id
 WHERE k.id = %s
"""


def _config_live(conn: psycopg.Connection, platform: str) -> temporada.ConfigTemporada | None:
    """ConfigTemporada si `ads_temporada_<platform>` esta en `live`; None en otro caso."""
    crudo = settings_vigentes(conn)
    if not isinstance(crudo, Mapping):
        return None
    if temporada.modo_desde_settings(crudo, platform) != "live":
        return None
    return temporada.desde_settings(crudo)


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
    estado = lee_estado(conn, platform, hoy)
    return estado.cerca_plataforma


def frontera_hoja(
    conn: psycopg.Connection,
    ad_entity_id: int,
    hoy: dt.date,
    *,
    piso: Decimal,
    techo: Decimal,
) -> FronteraHoja | None:
    """La misma temporada.frontera para UNA hoja, para la revalidacion de la
    cola al liberar (la conecta el bloque D). None sin config o temporada apagada."""
    plataforma = conn.execute(
        "SELECT platform::text FROM ad_entity WHERE id = %s", (ad_entity_id,)
    ).fetchone()
    if plataforma is None:
        return None
    platform = plataforma[0]
    config = _config_live(conn, platform)
    if config is None:
        return None
    estacion = temporada.resuelve(config, hoy)
    estado = lee_estado(conn, platform, hoy)
    desde = hoy - dt.timedelta(days=365)
    viejo = conn.execute(
        "SELECT min(abierto_el) FROM temporada_episodio"
        " WHERE platform = %s::platform AND cerrado_el IS NULL",
        (platform,),
    ).fetchone()
    if viejo[0] is not None:
        desde = min(desde, viejo[0].astimezone(dt.UTC).date())
    cambios = []
    manos = []
    for fila in conn.execute(_SQL_CADENA_HOJA, (ad_entity_id, desde, ad_entity_id, desde)):
        instante = fila[0].astimezone(dt.UTC)
        if fila[4] == "exclusion":
            manos.append(ManoExterna(instante=instante, bid_visto=fila[2], origen="exclusion"))
            continue
        cambios.append(
            CambioFechado(
                confirmado_el=instante,
                decision_id=fila[1],
                bid_antes=fila[2],
                bid_despues=fila[3],
                origen=fila[4],
                motivo_temporada=fila[5],
                episodio_id=fila[6],
            )
        )
    fines = frozenset(int(f[0]) for f in conn.execute(_SQL_FINES_HOJA, (ad_entity_id,)).fetchall())
    bid_cache = conn.execute(_SQL_BID_HOJA, (ad_entity_id,)).fetchone()
    bid = bid_cache[0] if bid_cache is not None else None
    cadena = None
    if cambios or manos:
        cadena = _cadena_desde(
            HistoriaBid(
                desde=desde, cambios=tuple(cambios), fines=fines, manos_externas=tuple(manos)
            ),
            bid,
        )
    return temporada.frontera(
        estacion,
        cadena,
        episodio=estado.episodio,
        excluida_en=estado.excluidas.get(ad_entity_id, frozenset()),
        cerca_plataforma=estado.cerca_plataforma,
        bid=bid,
        piso=piso,
        techo=techo,
    )
