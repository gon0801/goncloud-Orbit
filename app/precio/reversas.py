"""Plan y ejecucion de reversas en lote de `precio_cambio` (REPRICING 02 S.5).

Extraido de `app/precio/cambios.py` (Ola E). Comparte el candado de corrida
`precio:<platform>` via `app/lock.py` y el nucleo compare-and-set de
`app/precio/cambios.py`. Escribe UNICAMENTE en `precio_cambio`.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg

from app.lock import soltar_lock_con_advisory, tomar_lock_con_advisory
from app.precio.compuerta import modo_efectivo
from app.precio.config import leer_modo as _modo_config
from app.precio.config import leer_modo_universo as _modo_universo_config
from app.precio.puerto import EscrituraNoDisponible, Importe, UnidadPrecio
from app.precio.tipos import cambio_sin_efecto

logger = logging.getLogger(__name__)

__all__ = [
    "CorridaEnCurso",
    "UniversoEnLive",
    "SeleccionPorCambios",
    "SeleccionPorFecha",
    "SeleccionPorCorrida",
    "Seleccion",
    "FilaReversa",
    "PlanReversa",
    "planear_reversa",
    "revertir_lote",
    "_tomar_candado_lote",
    "_soltar_candado_lote",
]

_TTL_LOTE_SEGUNDOS = 3600
_ESTADOS_PLAN = ("revertible", "ya_revertido", "sin_efecto", "no_es_real")


class CorridaEnCurso(Exception):
    """El claim `precio:<platform>` lo tiene la corrida: la reversa no corre a la vez."""


class UniversoEnLive(Exception):
    """Algun universo del lote esta en `live`: apaga antes de revertir."""


@dataclass(frozen=True)
class SeleccionPorCambios:
    ids: tuple[int, ...]


@dataclass(frozen=True)
class SeleccionPorFecha:
    plataforma: str
    dia: date


@dataclass(frozen=True)
class SeleccionPorCorrida:
    corrida_id: int


Seleccion = SeleccionPorCambios | SeleccionPorFecha | SeleccionPorCorrida


@dataclass(frozen=True)
class FilaReversa:
    cambio_id: int
    listing_id: int
    seller_sku: str
    volver_a: Importe
    desde: Importe
    estado_plan: str  # uno de _ESTADOS_PLAN (solo informativo: el go re-decide)

    def __post_init__(self) -> None:
        if self.estado_plan not in _ESTADOS_PLAN:
            raise ValueError(f"estado_plan fuera de vocabulario: {self.estado_plan!r}")


@dataclass(frozen=True)
class PlanReversa:
    """`huella` = hash de la seleccion y de (cambio_id, volver_a) de cada fila.

    NO incluye el precio vivo: lo que el dueno aprueba es "revertir este
    conjunto". Si un precio se movio entre el plan y el go, esa fila se
    salta sola y las demas siguen.
    """

    seleccion: Seleccion
    filas: tuple[FilaReversa, ...]
    huella: str


@dataclass(frozen=True)
class _FilaPlanDb:
    """Proyeccion explicita de `_SQL_FILA_PLAN` (sin indices posicionales)."""

    cambio_id: int
    listing_id: int
    platform: str
    precio_antes: Decimal
    precio_antes_currency: str
    precio_despues: Decimal
    precio_despues_currency: str
    aplicado: bool
    estado: str
    enviado_at: datetime | None
    es_reversa: bool
    seller_sku: str | None
    confirmado_por: str | None
    ya_revertido: bool


def _tomar_candado_lote(conn: psycopg.Connection, platform: str, owner: str) -> None:
    job = f"precio:{platform}"
    tomar_lock_con_advisory(
        conn,
        job,
        owner,
        ttl_seconds=_TTL_LOTE_SEGUNDOS,
        error_cls=CorridaEnCurso,
        mensaje_ocupado=f"hay una corrida en curso ({job} en curso): reintenta",
    )


def _soltar_candado_lote(conn: psycopg.Connection, platform: str, owner: str) -> None:
    soltar_lock_con_advisory(conn, f"precio:{platform}", owner)


_SQL_FILA_PLAN = """
SELECT c.id, c.listing_id, c.platform, c.precio_antes, c.precio_antes_currency,
 c.precio_despues, c.precio_despues_currency, c.aplicado, c.estado,
 c.enviado_at, c.es_reversa, l.seller_sku, c.confirmado_por,
 EXISTS (SELECT 1 FROM precio_cambio r
         WHERE r.reversa_de = c.id AND r.estado IN ('enviado', 'confirmado'))
 FROM precio_cambio c JOIN listing l
 ON l.id = c.listing_id AND l.platform = c.platform
 LEFT JOIN precio_decision d ON d.id = c.decision_id
"""

_SQL_PLAN_POR_CAMBIOS = _SQL_FILA_PLAN + " WHERE c.id = ANY(%s) ORDER BY c.id"

_SQL_PLAN_POR_FECHA = (
    _SQL_FILA_PLAN
    + " WHERE c.platform = %s AND NOT c.es_reversa AND c.aplicado"
    + " AND c.enviado_at IS NOT NULL"
    + " AND (c.enviado_at AT TIME ZONE 'UTC')::date = %s ORDER BY c.id"
)

# P-A.2: el lote de una corrida son SUS cambios (los que estampan su
# `corrida_id`), no todo lo enviado ese dia en la plataforma. Las corridas
# viejas (sin `corrida_id`) dan un plan vacio: para ellas esta `--fecha`.
_SQL_PLAN_POR_CORRIDA = (
    _SQL_FILA_PLAN + " WHERE c.corrida_id = %s AND NOT c.es_reversa AND c.aplicado ORDER BY c.id"
)

_SQL_CORRIDA_DE = "SELECT platform, fecha FROM precio_corrida WHERE id = %s"

_SQL_UNIVERSOS_LOTE = """
SELECT DISTINCT c.platform, d.canal::text FROM precio_cambio c
 LEFT JOIN precio_decision d ON d.id = c.decision_id WHERE c.id = ANY(%s)
"""

_SQL_SETTINGS_VIGENTE_LOTE = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"


def _clasificar_plan(fila: _FilaPlanDb) -> str:
    """Estado informativo de la fila (el go re-decide contra el vivo)."""
    if fila.es_reversa or not fila.aplicado or fila.enviado_at is None:
        return "no_es_real"
    if fila.ya_revertido:
        return "ya_revertido"
    if cambio_sin_efecto(fila.estado, fila.confirmado_por):
        return "sin_efecto"
    return "revertible"


def _huella_reversa(seleccion: Seleccion, filas: tuple[FilaReversa, ...]) -> str:
    """Hash de la seleccion y de (cambio_id, volver_a) por fila. Sin vivo."""
    if isinstance(seleccion, SeleccionPorCambios):
        sel = "cambios:" + ",".join(str(i) for i in sorted(seleccion.ids))
    elif isinstance(seleccion, SeleccionPorFecha):
        sel = f"fecha:{seleccion.plataforma}:{seleccion.dia.isoformat()}"
    elif isinstance(seleccion, SeleccionPorCorrida):
        sel = f"corrida:{seleccion.corrida_id}"
    else:
        raise ValueError(f"seleccion desconocida: {seleccion!r}")
    partes = [
        f"{f.cambio_id}:{f.volver_a.valor}:{f.volver_a.moneda}"
        for f in sorted(filas, key=lambda f: f.cambio_id)
    ]
    return hashlib.sha256("|".join([sel, *partes]).encode()).hexdigest()[:16]


def planear_reversa(conn: psycopg.Connection, vitrina, seleccion: Seleccion) -> PlanReversa:
    """Arma el lote desde la base, sin tocar la plataforma (cero cuota).

    Un lote, una plataforma: ids de varias plataformas abortan. La huella
    cubre la seleccion y el precio al que vuelve cada fila, no el vivo.
    """
    if isinstance(seleccion, SeleccionPorCambios):
        filas_raw = conn.execute(_SQL_PLAN_POR_CAMBIOS, (list(seleccion.ids),)).fetchall()
        vistos = {f[0] for f in filas_raw}
        for pedido in seleccion.ids:
            if pedido not in vistos:
                raise ValueError(f"cambio {pedido} inexistente")
    elif isinstance(seleccion, SeleccionPorFecha):
        filas_raw = conn.execute(
            _SQL_PLAN_POR_FECHA, (seleccion.plataforma, seleccion.dia)
        ).fetchall()
    elif isinstance(seleccion, SeleccionPorCorrida):
        corrida = conn.execute(_SQL_CORRIDA_DE, (seleccion.corrida_id,)).fetchone()
        if corrida is None:
            raise ValueError(f"corrida {seleccion.corrida_id} inexistente")
        filas_raw = conn.execute(_SQL_PLAN_POR_CORRIDA, (seleccion.corrida_id,)).fetchall()
    else:
        raise ValueError(f"seleccion desconocida: {seleccion!r}")
    filas_db = [_FilaPlanDb(*f) for f in filas_raw]
    plats = sorted({f.platform for f in filas_db})
    if len(plats) > 1:
        raise ValueError(f"lote multi-plataforma: {plats} (un lote, una plataforma)")
    if plats and plats != [vitrina.clave]:
        raise ValueError(
            f"plataforma_distinta: mercado={vitrina.clave} lote={plats[0]}"
            " (el mercado se abre con la platform del lote)"
        )
    filas = tuple(
        FilaReversa(
            cambio_id=f.cambio_id,
            listing_id=f.listing_id,
            seller_sku=f.seller_sku or "",
            volver_a=Importe(f.precio_antes, f.precio_antes_currency),
            desde=Importe(f.precio_despues, f.precio_despues_currency),
            estado_plan=_clasificar_plan(f),
        )
        for f in filas_db
    )
    return PlanReversa(seleccion=seleccion, filas=filas, huella=_huella_reversa(seleccion, filas))


def _universos_en_live(conn: psycopg.Connection, universos_db) -> list[str]:
    """Universos del lote cuyo modo efectivo es `live`.

    Goal fijo `live`: la reversa es manual, el dueno ya autorizo y el goal
    no restringe. Sin canal conocido no hay universo que medir (igual que
    la corrida: el universo no restringe).
    """
    universos = sorted({f"{p}/{c}" for p, c in universos_db if c is not None})
    if not universos:
        return []
    fila_cfg = conn.execute(_SQL_SETTINGS_VIGENTE_LOTE).fetchone()
    if fila_cfg is None or fila_cfg[0] is None:
        raise ValueError("config sin precio_modo_global: sin config_version")
    settings = fila_cfg[0]
    global_ = _modo_config(settings, "precio_modo_global")
    por_universo = _modo_universo_config(settings, "precio_modo_universo")
    return [
        u for u in universos if modo_efectivo(global_, por_universo.get(u, "off"), "live") == "live"
    ]


def _leer_vivo_suave(mercado, unidad: UnidadPrecio) -> Importe | None:
    try:
        return mercado.precio_vivo(unidad)
    except Exception:
        return None


def _revertir_una(
    conn: psycopg.Connection,
    mercado,
    fila,
    momento: datetime,
    huella: str,
    *,
    leer_cambio_fn,
    unidad_fn,
    revertir_fila_fn,
    error_no_reversible,
    error_sin_sku,
    resultado_cls,
):
    """Una fila del lote: cierra el original `enviado` si el vivo coincide y revierte."""
    cid = fila.cambio_id
    if fila.estado == "pendiente":
        logger.info("revertir_lote cambio=%s saltado=original_abierto", cid)
        return resultado_cls(id_reversa=None, estado="saltado", motivo="original_abierto")
    if fila.estado == "enviado":
        unidad = unidad_fn(mercado, conn, fila.listing_id)
        vivo = _leer_vivo_suave(mercado, unidad)
        if vivo is None:
            logger.info("revertir_lote cambio=%s saltado=sin_precio_vivo", cid)
            return resultado_cls(id_reversa=None, estado="saltado", motivo="sin_precio_vivo")
        if (vivo.valor, vivo.moneda) != (fila.precio_despues, fila.precio_despues_currency):
            motivo = (
                f"precio_vivo_distinto: vivo={vivo.valor}/{vivo.moneda}"
                f" vs despues={fila.precio_despues}/{fila.precio_despues_currency}"
            )
            logger.info("revertir_lote cambio=%s saltado=%s", cid, motivo)
            return resultado_cls(id_reversa=None, estado="saltado", motivo=motivo)
        with conn.transaction():
            conn.execute(
                "UPDATE precio_cambio SET estado = 'confirmado',"
                " confirmado_por = 'lectura_viva' WHERE id = %s",
                (cid,),
            )
        logger.info("revertir_lote cambio=%s confirmado=lectura_viva", cid)
        fila = leer_cambio_fn(conn, cid)
    try:
        return revertir_fila_fn(conn, mercado, fila, momento, lote=huella)
    except error_no_reversible:
        return resultado_cls(id_reversa=None, estado="saltado", motivo="no_es_real")
    except error_sin_sku:
        return resultado_cls(id_reversa=None, estado="saltado", motivo="sin_sku")
    except (psycopg.Error, EscrituraNoDisponible):
        raise
    except Exception as exc:
        motivo = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        logger.info("revertir_lote cambio=%s error=%s", cid, motivo)
        return resultado_cls(id_reversa=None, estado="error", motivo=motivo)


def revertir_lote(
    conn: psycopg.Connection,
    mercado,
    plan: PlanReversa,
    *,
    huella: str,
    go_literal: str,
    owner: str,
    ahora: datetime | None = None,
    normalizar_ahora_fn=None,
    leer_cambio_fn=None,
    unidad_fn=None,
    revertir_fila_fn=None,
    error_no_reversible=None,
    error_sin_sku=None,
    resultado_cls=None,
) -> tuple:
    """Reversa manual en lote. Nunca automatica.

    Toma el MISMO claim `precio:<platform>` que la corrida (`CorridaEnCurso`
    si lo tiene otro dueno) y aborta si el modo efectivo de algun universo
    del lote es `live` (`UniversoEnLive`), sin tocar nada.
    """
    if not conn.autocommit:
        raise ValueError(
            "revertir_lote exige conexión en autocommit: cada INSERT tiene que"
            " confirmar antes de su escritura (S5)"
        )
    if not go_literal:
        raise ValueError("falta go_literal: la reversa manual exige el go del dueno")
    if huella != plan.huella:
        raise ValueError(f"--huella {huella} != huella del plan {plan.huella}: re-corre el dry-run")
    if (
        normalizar_ahora_fn is None
        or leer_cambio_fn is None
        or unidad_fn is None
        or revertir_fila_fn is None
        or error_no_reversible is None
        or error_sin_sku is None
        or resultado_cls is None
    ):
        import app.precio.cambios as _cambios

        normalizar_ahora_fn = normalizar_ahora_fn or _cambios._normalizar_ahora
        leer_cambio_fn = leer_cambio_fn or _cambios._fila_cambio
        unidad_fn = unidad_fn or _cambios._unidad
        revertir_fila_fn = revertir_fila_fn or _cambios._revertir_fila
        error_no_reversible = error_no_reversible or _cambios.CambioNoReversible
        error_sin_sku = error_sin_sku or _cambios.PublicacionSinSku
        resultado_cls = resultado_cls or _cambios.ResultadoReversion

    momento = normalizar_ahora_fn(ahora)
    platform = mercado.clave
    _tomar_candado_lote(conn, platform, owner)
    try:
        ids = [f.cambio_id for f in plan.filas]
        universos_db = conn.execute(_SQL_UNIVERSOS_LOTE, (ids,)).fetchall()
        en_live = _universos_en_live(conn, universos_db)
        if en_live:
            raise UniversoEnLive(
                f"universo(s) en live: {', '.join(en_live)}: apaga antes de revertir"
            )
        resultados = []
        for cid in ids:
            fila = leer_cambio_fn(conn, cid)
            if fila.platform != platform:
                raise ValueError(
                    f"plataforma_distinta: mercado={platform} fila={fila.platform}"
                    " (el mercado se abre con la platform del lote)"
                )
            resultados.append(
                (
                    cid,
                    _revertir_una(
                        conn,
                        mercado,
                        fila,
                        momento,
                        huella,
                        leer_cambio_fn=leer_cambio_fn,
                        unidad_fn=unidad_fn,
                        revertir_fila_fn=revertir_fila_fn,
                        error_no_reversible=error_no_reversible,
                        error_sin_sku=error_sin_sku,
                        resultado_cls=resultado_cls,
                    ),
                )
            )
        return tuple(resultados)
    finally:
        _soltar_candado_lote(conn, platform, owner)
