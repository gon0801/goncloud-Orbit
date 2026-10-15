"""Protocolo de `precio_cambio` (sale de `app/spapi/precio_write.py`,
que se queda solo con lo de Amazon).

Orden inmutable de S5: `INSERT` de la fila `pendiente` + `COMMIT` ->
escritura via `mercado.escribir` -> sello por ack -> readback informativo
-> cierre por observacion del dia siguiente. El motor generico no conoce
Amazon: la unidad, el precio vivo y el PATCH salen del `Mercado`.

Candados propios (tests/test_architecture.py): este archivo no importa
`app.spapi`, `app.meli`, `httpx` ni lee tablas `spapi_*`. Escribe en
`precio_cambio` y toma el claim `precio:<platform>` en `ads_optimizer_lock`
(el mismo que la corrida: reversa y corrida no corren a la vez).

Conexion en autocommit; cada bloque confirma al salir: la fila `pendiente`
ya es durable cuando sale la escritura.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import psycopg.errors
from psycopg.types.json import Jsonb

from app.precio.compuerta import modo_efectivo
from app.precio.config import _modo as _modo_config
from app.precio.config import _modo_universo as _modo_universo_config
from app.precio.puerto import EscrituraNoDisponible, Importe, Miembro, UnidadPrecio

logger = logging.getLogger(__name__)

__all__ = [
    "PrecioVivoAusente",
    "CambioNoReversible",
    "DecisionSinAccion",
    "PublicacionSinSku",
    "CorridaEnCurso",
    "UniversoEnLive",
    "PrecioVivo",
    "ResultadoReversion",
    "ResultadoCambio",
    "SeleccionPorCambios",
    "SeleccionPorFecha",
    "SeleccionPorCorrida",
    "Seleccion",
    "FilaReversa",
    "PlanReversa",
    "aplicar",
    "revertir",
    "planear_reversa",
    "revertir_lote",
    "cerrar_huerfanas",
    "cerrar_por_observacion",
    "insertar_virtual",
]


class PrecioVivoAusente(Exception):
    """No hay precio propio legible: no se escribe nada."""


class CambioNoReversible(ValueError):
    """`revertir` mal usado: el cambio no existe o no es revertible."""


class DecisionSinAccion(ValueError):
    """`aplicar` mal usado: la decision no mueve precio."""


class PublicacionSinSku(ValueError):
    """La publicacion no tiene SKU con que armar la escritura."""


class CorridaEnCurso(Exception):
    """El claim `precio:<platform>` lo tiene la corrida: la reversa no corre a la vez."""


class UniversoEnLive(Exception):
    """Algun universo del lote esta en `live`: apaga antes de revertir."""


@dataclass(frozen=True)
class PrecioVivo:
    precio: Decimal
    moneda: str


@dataclass(frozen=True)
class ResultadoReversion:
    id_reversa: int | None
    estado: str  # "enviado" | "error" | "saltado"
    motivo: str | None

    def __post_init__(self) -> None:
        if self.estado not in ("enviado", "error", "saltado"):
            raise ValueError(f"estado fuera de vocabulario: {self.estado!r}")


@dataclass(frozen=True)
class ResultadoCambio:
    id_cambio: int | None
    estado: str  # "enviado" | "error" | "saltado"
    motivo: str | None

    def __post_init__(self) -> None:
        if self.estado not in ("enviado", "error", "saltado"):
            raise ValueError(f"estado fuera de vocabulario: {self.estado!r}")


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

_ESTADOS_PLAN = ("revertible", "ya_revertido", "sin_efecto", "no_es_real")


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


def _normalizar_ahora(ahora: datetime | None) -> datetime:
    """`ahora` siempre en UTC (r1-A7d): naive se asume UTC, aware se convierte."""
    if ahora is None:
        return datetime.now(UTC)
    if ahora.tzinfo is None:
        return ahora.replace(tzinfo=UTC)
    return ahora.astimezone(UTC)


def _unidad_de_listing(
    listing_id: int, platform: str, product_id: int | None, sku: str | None, asin: str | None
) -> UnidadPrecio:
    """La unidad del cambio: fila de `listing` con su (unico) miembro Amazon."""
    return UnidadPrecio(
        listing_id=listing_id,
        plataforma=platform,
        externo=asin or "",
        canal=None,
        producto_ancla=product_id or 0,
        miembros=(Miembro(product_id, sku or "", None),),
    )


def _fila_cambio(conn: psycopg.Connection, cambio_id: int):
    fila = conn.execute(
        "SELECT c.id, c.listing_id, c.platform, c.precio_antes, c.precio_antes_currency,"
        " c.precio_despues, c.precio_despues_currency, c.aplicado, c.estado,"
        " c.enviado_at, c.es_reversa, l.seller_sku, l.external_id, l.product_id"
        " FROM precio_cambio c JOIN listing l"
        " ON l.id = c.listing_id AND l.platform = c.platform"
        " WHERE c.id = %s",
        (cambio_id,),
    ).fetchone()
    if fila is None:
        raise CambioNoReversible(f"cambio {cambio_id} inexistente")
    return fila


def _sellar(
    conn: psycopg.Connection, cambio_id: int, *, estado: str, ack=None, error_code=None
) -> None:
    with conn.transaction():
        if estado == "enviado":
            conn.execute(
                "UPDATE precio_cambio SET estado = 'enviado',"
                " ack = COALESCE(ack, %s) WHERE id = %s",
                (Jsonb(ack), cambio_id),
            )
        else:
            conn.execute(
                "UPDATE precio_cambio SET estado = 'error', error_code = %s WHERE id = %s",
                (error_code, cambio_id),
            )


def _readback(
    conn: psycopg.Connection,
    cambio_id: int,
    vivo: Importe | None,
    ahora: datetime,
    *,
    origen: str,
) -> None:
    with conn.transaction():
        if vivo is None:
            conn.execute(
                "UPDATE precio_cambio SET readback_estado = 'fallido', readback_at = %s"
                " WHERE id = %s",
                (ahora, cambio_id),
            )
        else:
            conn.execute(
                "UPDATE precio_cambio SET readback_precio = %s, readback_precio_currency = %s,"
                " readback_estado = 'ok', readback_at = %s WHERE id = %s",
                (vivo.valor, vivo.moneda, ahora, cambio_id),
            )
    estado_rb = "ok" if vivo is not None else "fallido"
    logger.info("%s cambio=%s readback=%s", origen, cambio_id, estado_rb)


def _leer_vivo_suave(mercado, unidad: UnidadPrecio) -> Importe | None:
    # El readback nunca tumba una operacion ya sellada (r1-A6): cualquier
    # fallo del mercado leyendo el vivo tambien es `fallido`. Hoy la tupla
    # era (PrecioVivoAusente, SpapiError, httpx.HTTPError); sin plataforma
    # concreta en este modulo, el catch es todo el mercado.
    try:
        return mercado.precio_vivo(unidad)
    except Exception:  # noqa: BLE001 - lectura informativa, jamas tumba
        return None


def _observada_del_dia(conn: psycopg.Connection, mercado, unidad: UnidadPrecio, *, dia: date):
    """Observacion propia del dia (para `precio_observado_antes`)."""
    return mercado.observado(conn, unidad, despues_de=dia - timedelta(days=1), hasta=dia)


def _escribir_y_sellar(
    conn: psycopg.Connection,
    cambio_id: int,
    *,
    mercado,
    unidad: UnidadPrecio,
    destino: Importe,
    momento: datetime,
    origen: str,
    exito,
    fallo,
) -> None:
    """Escritura via el mercado -> sello por ack -> readback informativo.

    `mercado.escribir` devuelve el acuse ya saneado; los fallos conocidos
    de plataforma llegan como `Acuse(aceptada=False, codigo=...)`. Una
    `EscrituraNoDisponible` (forma sin sellar) sella la fila a error y
    sube para que la corrida deje de aplicar; cualquier otra excepcion se
    sella con su tipo y sube igual.
    """
    try:
        acuse = mercado.escribir(unidad, destino)
    except EscrituraNoDisponible:
        logger.info("%s cambio=%s error=parche_sin_sellar", origen, cambio_id)
        _sellar(conn, cambio_id, estado="error", error_code="parche_sin_sellar")
        raise
    except Exception as exc:  # noqa: BLE001 - se sella y sube (r1-A6 solo cubre red)
        ruta = getattr(mercado, "_ruta_error", None) or "escribir"
        codigo = f"PATCH {ruta} excepcion:{type(exc).__name__}"
        logger.info("%s cambio=%s error=%s", origen, cambio_id, codigo)
        _sellar(conn, cambio_id, estado="error", error_code=codigo)
        vivo_rb = _leer_vivo_suave(mercado, unidad)
        _readback(conn, cambio_id, vivo_rb, momento, origen=origen)
        raise
    vivo_rb = _leer_vivo_suave(mercado, unidad)
    if acuse.aceptada:
        _sellar(conn, cambio_id, estado="enviado", ack=dict(acuse.cuerpo))
        logger.info("%s cambio=%s estado=enviado", origen, cambio_id)
        _readback(conn, cambio_id, vivo_rb, momento, origen=origen)
        return exito()
    codigo = acuse.codigo or "desconocido"
    logger.info("%s cambio=%s error=%s", origen, cambio_id, codigo)
    _sellar(conn, cambio_id, estado="error", error_code=codigo)
    _readback(conn, cambio_id, vivo_rb, momento, origen=origen)
    return fallo(codigo)


def aplicar(
    conn: psycopg.Connection,
    mercado,
    decision_id: int,
    *,
    ahora: datetime | None = None,
) -> ResultadoCambio:
    """Lleva UNA decision `subir`/`bajar` de modo `live` a la plataforma.

    Idempotente por compare-and-set contra el precio vivo: llamarla dos
    veces escribe una (la segunda salta con `ya_aplicado`).     Cualquier otro
    resultado o modo es `DecisionSinAccion` (la base tambien lo rechaza).
    Sin precio vivo no hay fila que insertar (`precio_antes` es NOT NULL).
    Orden S5: INSERT + COMMIT -> PATCH de escritura -> sello. Si el
    proceso muere entre el INSERT (COMMIT) y el PATCH, queda una fila
    pendiente huerfana que el indice de abierto unico vuelve visible (todo
    reintento la ve abierta y salta); `cerrar_huerfanas` la detecta al
    arrancar.
    """
    if not conn.autocommit:
        raise ValueError(
            "aplicar exige conexión en autocommit: el INSERT tiene que"
            " confirmar antes de la escritura (S5)"
        )
    momento = _normalizar_ahora(ahora)
    dec = conn.execute(
        "SELECT d.listing_id, d.platform, d.resultado, d.mode,"
        " d.p_actual, d.p_actual_currency, d.p_aplicado, d.p_aplicado_currency,"
        " l.seller_sku, l.external_id, l.product_id"
        " FROM precio_decision d JOIN listing l"
        " ON l.id = d.listing_id AND l.platform = d.platform"
        " WHERE d.id = %s",
        (decision_id,),
    ).fetchone()
    if dec is None:
        raise DecisionSinAccion(f"decision {decision_id} inexistente")
    (
        listing_id,
        platform,
        resultado,
        mode,
        p_actual,
        p_actual_moneda,
        p_aplicado,
        p_moneda,
        sku,
        asin,
        product_id,
    ) = dec
    if platform != mercado.clave:
        raise ValueError(
            f"plataforma_distinta: mercado={mercado.clave} fila={platform}"
            " (el mercado se abre con la platform de la fila)"
        )
    if resultado not in ("subir", "bajar") or mode != "live":
        raise DecisionSinAccion(
            f"decision {decision_id}: solo subir/bajar en live mueven precio,"
            f" llego {resultado}/{mode}"
        )
    if not sku:
        raise PublicacionSinSku("listing_sin_sku: sin seller_sku no hay ruta que escribir")
    unidad = _unidad_de_listing(listing_id, platform, product_id, sku, asin)
    mercado.validar_destino(unidad)
    destino = Importe(Decimal(p_aplicado), p_moneda)
    try:
        vivo = mercado.precio_vivo(unidad)
    except Exception as exc:  # noqa: BLE001 - fallo del mercado leyendo el vivo
        logger.info(
            "aplicar decision=%s error=sin_precio_vivo (%s)",
            decision_id,
            type(exc).__name__,
        )
        return ResultadoCambio(id_cambio=None, estado="error", motivo="sin_precio_vivo")
    if (vivo.valor, vivo.moneda) == (p_aplicado, p_moneda):
        logger.info("aplicar decision=%s saltado=ya_aplicado", decision_id)
        return ResultadoCambio(id_cambio=None, estado="saltado", motivo="ya_aplicado")
    otro_abierto = conn.execute(
        "SELECT count(*) FROM precio_cambio"
        " WHERE listing_id = %s AND platform = %s"
        " AND estado IN ('pendiente', 'enviado')",
        (listing_id, platform),
    ).fetchone()[0]
    if otro_abierto:
        logger.info("aplicar decision=%s saltado=listing_con_cambio_abierto", decision_id)
        return ResultadoCambio(
            id_cambio=None, estado="saltado", motivo="listing_con_cambio_abierto"
        )
    if (vivo.valor, vivo.moneda) != (p_actual, p_actual_moneda):
        motivo = (
            f"precio_vivo_distinto: vivo={vivo.valor}/{vivo.moneda}"
            f" vs p_actual={p_actual}/{p_actual_moneda}"
        )
        logger.info("aplicar decision=%s saltado=%s", decision_id, motivo)
        return ResultadoCambio(id_cambio=None, estado="saltado", motivo=motivo)
    obs = _observada_del_dia(conn, mercado, unidad, dia=momento.date())
    obs_precio = obs.precio.valor if obs is not None else None
    obs_moneda = obs.precio.moneda if obs is not None else None
    # Carrera r5-L4: la base protege el invariante y aqui se vuelve
    # saltado, no excepcion cruda.
    try:
        with conn.transaction():
            id_cambio = conn.execute(
                "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_observado_antes,"
                " precio_observado_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, enviado_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, 'pendiente', %s)"
                " RETURNING id",
                (
                    decision_id,
                    listing_id,
                    platform,
                    vivo.valor,
                    vivo.moneda,
                    obs_precio,
                    obs_moneda,
                    p_aplicado,
                    p_moneda,
                    momento,
                ),
            ).fetchone()[0]
    except psycopg.errors.UniqueViolation:
        logger.info("aplicar decision=%s saltado=listing_con_cambio_abierto", decision_id)
        return ResultadoCambio(
            id_cambio=None, estado="saltado", motivo="listing_con_cambio_abierto"
        )
    return _escribir_y_sellar(
        conn,
        id_cambio,
        mercado=mercado,
        unidad=unidad,
        destino=destino,
        momento=momento,
        origen="aplicar",
        exito=lambda: ResultadoCambio(id_cambio=id_cambio, estado="enviado", motivo=None),
        fallo=lambda codigo: ResultadoCambio(id_cambio=id_cambio, estado="error", motivo=codigo),
    )


def _revertir_fila(
    conn: psycopg.Connection,
    mercado,
    fila,
    momento: datetime,
    *,
    lote: str | None,
) -> ResultadoReversion:
    """Revierte UNA fila ya leida: vivo, INSERT (+COMMIT) y escritura sellada.

    Nucleo compartido por `revertir` y `revertir_lote`. El original abierto
    se salta aqui (`original_abierto`): el lote cierra el `enviado` cuyo vivo
    coincide ANTES de llamar. `lote` sella `lote_reversa` en la reversa
    (`None` en la reversa unitaria).
    """
    (
        cambio_id,
        listing_id,
        platform,
        antes,
        antes_moneda,
        despues,
        despues_moneda,
        aplicado,
        estado,
        enviado_at,
        es_reversa,
        sku,
        asin,
        product_id,
    ) = fila[:14]
    if es_reversa or not aplicado or enviado_at is None:
        raise CambioNoReversible(
            f"cambio {cambio_id}: solo un cambio real, no-reversa, con enviado_at"
        )
    if estado in ("pendiente", "enviado"):
        logger.info("revertir cambio=%s saltado=original_abierto", cambio_id)
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo="original_abierto")
    if not sku:
        raise PublicacionSinSku("listing_sin_sku: sin seller_sku no hay ruta que escribir")
    unidad = _unidad_de_listing(listing_id, platform, product_id, sku, asin)
    mercado.validar_destino(unidad)
    otro_abierto = conn.execute(
        "SELECT count(*) FROM precio_cambio"
        " WHERE listing_id = %s AND platform = %s"
        " AND estado IN ('pendiente', 'enviado')",
        (listing_id, platform),
    ).fetchone()[0]
    if otro_abierto:
        logger.info("revertir cambio=%s saltado=listing_con_cambio_abierto", cambio_id)
        return ResultadoReversion(
            id_reversa=None, estado="saltado", motivo="listing_con_cambio_abierto"
        )
    try:
        vivo = mercado.precio_vivo(unidad)
    except Exception as exc:  # noqa: BLE001 - fallo del mercado leyendo el vivo
        logger.info(
            "revertir cambio=%s saltado=sin_precio_vivo (%s)",
            cambio_id,
            type(exc).__name__,
        )
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo="sin_precio_vivo")
    if (vivo.valor, vivo.moneda) != (despues, despues_moneda):
        motivo = (
            f"precio_vivo_distinto: vivo={vivo.valor}/{vivo.moneda}"
            f" vs despues={despues}/{despues_moneda}"
        )
        logger.info("revertir cambio=%s saltado=%s", cambio_id, motivo)
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo=motivo)
    obs = _observada_del_dia(conn, mercado, unidad, dia=momento.date())
    obs_precio = obs.precio.valor if obs is not None else None
    obs_moneda = obs.precio.moneda if obs is not None else None
    # Carrera r5-L4: otro proceso abrio el par entre el chequeo y el
    # INSERT; la base protege el invariante y aqui se vuelve saltado.
    try:
        with conn.transaction():
            id_reversa = conn.execute(
                "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
                " precio_antes_currency, precio_observado_antes,"
                " precio_observado_antes_currency, precio_despues, precio_despues_currency,"
                " aplicado, estado, enviado_at, es_reversa, reversa_de, lote_reversa)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, 'pendiente', %s, true, %s, %s)"
                " RETURNING id",
                (
                    listing_id,
                    platform,
                    vivo.valor,
                    vivo.moneda,
                    obs_precio,
                    obs_moneda,
                    antes,
                    antes_moneda,
                    momento,
                    cambio_id,
                    lote,
                ),
            ).fetchone()[0]
    except psycopg.errors.UniqueViolation:
        logger.info("revertir cambio=%s saltado=listing_con_cambio_abierto", cambio_id)
        return ResultadoReversion(
            id_reversa=None, estado="saltado", motivo="listing_con_cambio_abierto"
        )
    return _escribir_y_sellar(
        conn,
        id_reversa,
        mercado=mercado,
        unidad=unidad,
        destino=Importe(Decimal(antes), antes_moneda),
        momento=momento,
        origen="revertir",
        exito=lambda: ResultadoReversion(id_reversa=id_reversa, estado="enviado", motivo=None),
        fallo=lambda codigo: ResultadoReversion(
            id_reversa=id_reversa, estado="error", motivo=codigo
        ),
    )


def revertir(
    conn: psycopg.Connection,
    cambio_id: int,
    *,
    mercado,
    ahora: datetime | None = None,
) -> ResultadoReversion:
    """Reversa manual de un cambio real: vuelve a su `precio_antes`.

    Solo un cambio real, no-reversa, con `enviado_at`; cualquier otro uso
    es `CambioNoReversible` (funcion mal usada). Si el original sigue
    abierto (`pendiente` o `enviado`) se salta con `original_abierto`: la
    reversa solo es posible cuando el original ya cerro; un cambio en
    `error` si es reversible el mismo dia cuando el vivo coincide. Si el
    precio vivo ya no coincide con el `precio_despues` del cambio, se
    salta con su razon, sin fila y sin escritura. Misma ventana
    COMMIT-PATCH que `aplicar`: si el proceso muere entre el INSERT
    (COMMIT) y el PATCH, queda una fila pendiente huerfana que el
    indice de abierto unico vuelve visible.
    """
    if not conn.autocommit:
        raise ValueError(
            "revertir exige conexión en autocommit: el INSERT tiene que"
            " confirmar antes de la escritura (S5)"
        )
    momento = _normalizar_ahora(ahora)
    fila = _fila_cambio(conn, cambio_id)
    (
        _id,
        listing_id,
        platform,
        antes,
        antes_moneda,
        despues,
        despues_moneda,
        aplicado,
        estado,
        enviado_at,
        es_reversa,
        sku,
        asin,
        product_id,
    ) = fila
    if platform != mercado.clave:
        raise ValueError(
            f"plataforma_distinta: mercado={mercado.clave} fila={platform}"
            " (el mercado se abre con la platform de la fila)"
        )
    if es_reversa or not aplicado:
        raise CambioNoReversible(
            f"cambio {cambio_id}: solo un cambio real, no-reversa, con enviado_at"
        )
    if estado in ("pendiente", "enviado"):
        logger.info("revertir cambio=%s saltado=original_abierto", cambio_id)
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo="original_abierto")
    if enviado_at is None:
        raise CambioNoReversible(
            f"cambio {cambio_id}: solo un cambio real, no-reversa, con enviado_at"
        )
    return _revertir_fila(conn, mercado, fila, momento, lote=None)


# Claim `precio:<platform>` COMPARTIDO con la corrida (patron copiado de
# `app/precio/corrida.py`, sin importar: `corrida` importa este modulo).
# Mismo `job_key` y mismo advisory: la segunda en llegar sale con
# "hay una corrida en curso".
_SQL_CLAIM_LOTE = """
INSERT INTO ads_optimizer_lock (job_key, owner, claimed_at, heartbeat_at, ttl_seconds)
VALUES (%s, %s, now(), now(), %s)
ON CONFLICT (job_key) DO UPDATE
   SET owner = EXCLUDED.owner, claimed_at = now(), heartbeat_at = now(),
       ttl_seconds = EXCLUDED.ttl_seconds
 WHERE ads_optimizer_lock.heartbeat_at
       + make_interval(secs => ads_optimizer_lock.ttl_seconds) <= now()
RETURNING claimed_at
"""

_SQL_LIBERAR_LOTE = "DELETE FROM ads_optimizer_lock WHERE job_key = %s AND owner = %s"

_SQL_ADVISORY_LOTE_TOMAR = "SELECT pg_try_advisory_lock(hashtext('precio:' || %s))"
_SQL_ADVISORY_LOTE_SOLTAR = "SELECT pg_advisory_unlock(hashtext('precio:' || %s))"

_TTL_LOTE_SEGUNDOS = 3600


def _tomar_candado_lote(conn: psycopg.Connection, platform: str, owner: str) -> None:
    job = f"precio:{platform}"
    fila = conn.execute(_SQL_CLAIM_LOTE, (job, owner, _TTL_LOTE_SEGUNDOS)).fetchone()
    if fila is None:
        raise CorridaEnCurso(f"hay una corrida en curso ({job} en curso): reintenta")
    if not conn.execute(_SQL_ADVISORY_LOTE_TOMAR, (platform,)).fetchone()[0]:
        conn.execute(_SQL_LIBERAR_LOTE, (job, owner))
        raise CorridaEnCurso(f"hay una corrida en curso ({job} en curso): reintenta")


def _soltar_candado_lote(conn: psycopg.Connection, platform: str, owner: str) -> None:
    try:
        conn.execute(_SQL_ADVISORY_LOTE_SOLTAR, (platform,)).fetchone()
    finally:
        conn.execute(_SQL_LIBERAR_LOTE, (f"precio:{platform}", owner))


_SQL_FILA_PLAN = """
SELECT c.id, c.listing_id, c.platform, c.precio_antes, c.precio_antes_currency,
 c.precio_despues, c.precio_despues_currency, c.aplicado, c.estado,
 c.enviado_at, c.es_reversa, l.seller_sku, c.error_code, c.readback_precio,
 c.readback_precio_currency,
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

_SQL_CORRIDA_DE = "SELECT platform, fecha FROM precio_corrida WHERE id = %s"

_SQL_UNIVERSOS_LOTE = """
SELECT DISTINCT c.platform, d.canal::text FROM precio_cambio c
 LEFT JOIN precio_decision d ON d.id = c.decision_id WHERE c.id = ANY(%s)
"""

_SQL_SETTINGS_VIGENTE_LOTE = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"


def _clasificar_plan(fila) -> str:
    """Estado informativo de la fila (el go re-decide contra el vivo)."""
    aplicado = fila[7]
    estado = fila[8]
    enviado_at = fila[9]
    es_reversa = fila[10]
    if es_reversa or not aplicado or enviado_at is None:
        return "no_es_real"
    if fila[15]:
        return "ya_revertido"
    if estado == "error" and (
        fila[12] == "huerfana_sin_efecto" or (fila[13], fila[14]) == (fila[3], fila[4])
    ):
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
        filas_db = conn.execute(_SQL_PLAN_POR_CAMBIOS, (list(seleccion.ids),)).fetchall()
        vistos = {f[0] for f in filas_db}
        for pedido in seleccion.ids:
            if pedido not in vistos:
                raise ValueError(f"cambio {pedido} inexistente")
    elif isinstance(seleccion, SeleccionPorFecha):
        filas_db = conn.execute(
            _SQL_PLAN_POR_FECHA, (seleccion.plataforma, seleccion.dia)
        ).fetchall()
    elif isinstance(seleccion, SeleccionPorCorrida):
        corrida = conn.execute(_SQL_CORRIDA_DE, (seleccion.corrida_id,)).fetchone()
        if corrida is None:
            raise ValueError(f"corrida {seleccion.corrida_id} inexistente")
        # La corrida no estampa `corrida_id` en cambios ni decisiones: el
        # lote de una corrida es lo enviado ese dia en su plataforma.
        filas_db = conn.execute(_SQL_PLAN_POR_FECHA, (corrida[0], corrida[1])).fetchall()
    else:
        raise ValueError(f"seleccion desconocida: {seleccion!r}")
    plats = sorted({f[2] for f in filas_db})
    if len(plats) > 1:
        raise ValueError(f"lote multi-plataforma: {plats} (un lote, una plataforma)")
    if plats and plats != [vitrina.clave]:
        raise ValueError(
            f"plataforma_distinta: mercado={vitrina.clave} lote={plats[0]}"
            " (el mercado se abre con la platform del lote)"
        )
    filas = tuple(
        FilaReversa(
            cambio_id=f[0],
            listing_id=f[1],
            seller_sku=f[11] or "",
            volver_a=Importe(f[3], f[4]),
            desde=Importe(f[5], f[6]),
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


def _revertir_una(
    conn: psycopg.Connection, mercado, fila, momento: datetime, huella: str
) -> ResultadoReversion:
    """Una fila del lote: cierra el original `enviado` si el vivo coincide y revierte.

    Un `pendiente` se salta (`original_abierto`): su COMMIT sin escritura lo
    resuelve la corrida al arrancar. Una fila que ya no es revertible se
    salta (`no_es_real`, `sin_sku`); una explosion al escribir sella su fila
    y sigue el lote (`error`). Solo la infra (`psycopg.Error`) o la forma
    sin sellar (`EscrituraNoDisponible`) abortan el lote.
    """
    cid = fila[0]
    if fila[8] == "pendiente":
        logger.info("revertir_lote cambio=%s saltado=original_abierto", cid)
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo="original_abierto")
    if fila[8] == "enviado":
        unidad = _unidad_de_listing(fila[1], fila[2], fila[13], fila[11], fila[12])
        vivo = _leer_vivo_suave(mercado, unidad)
        if vivo is None:
            logger.info("revertir_lote cambio=%s saltado=sin_precio_vivo", cid)
            return ResultadoReversion(id_reversa=None, estado="saltado", motivo="sin_precio_vivo")
        if (vivo.valor, vivo.moneda) != (fila[5], fila[6]):
            motivo = (
                f"precio_vivo_distinto: vivo={vivo.valor}/{vivo.moneda}"
                f" vs despues={fila[5]}/{fila[6]}"
            )
            logger.info("revertir_lote cambio=%s saltado=%s", cid, motivo)
            return ResultadoReversion(id_reversa=None, estado="saltado", motivo=motivo)
        with conn.transaction():
            conn.execute(
                "UPDATE precio_cambio SET estado = 'confirmado',"
                " confirmado_por = 'lectura_viva' WHERE id = %s",
                (cid,),
            )
        logger.info("revertir_lote cambio=%s confirmado=lectura_viva", cid)
        fila = _fila_cambio(conn, cid)
    try:
        return _revertir_fila(conn, mercado, fila, momento, lote=huella)
    except CambioNoReversible:
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo="no_es_real")
    except PublicacionSinSku:
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo="sin_sku")
    except (psycopg.Error, EscrituraNoDisponible):
        raise
    except Exception as exc:  # noqa: BLE001 - reporta error y el lote sigue; la
        # pendiente huerfana, si quedo, la resuelve cerrar_huerfanas al arrancar
        motivo = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        logger.info("revertir_lote cambio=%s error=%s", cid, motivo)
        return ResultadoReversion(id_reversa=None, estado="error", motivo=motivo)


def revertir_lote(
    conn: psycopg.Connection,
    mercado,
    plan: PlanReversa,
    *,
    huella: str,
    go_literal: str,
    owner: str,
    ahora: datetime | None = None,
) -> tuple[tuple[int, ResultadoReversion], ...]:
    """Reversa manual en lote. Nunca automatica.

    Toma el MISMO claim `precio:<platform>` que la corrida (`CorridaEnCurso`
    si lo tiene otro dueno) y aborta si el modo efectivo de algun universo
    del lote es `live` (`UniversoEnLive`), sin tocar nada. Por fila relee el
    original: un `enviado` con el vivo aplicado se cierra `confirmado` /
    `lectura_viva` y se revierte el mismo dia; un vivo movido entre el plan
    y el go salta esa fila y el lote sigue. Cada reversa sella
    `lote_reversa` con la huella. Devuelve `(cambio_id, resultado)` por fila.
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
    momento = _normalizar_ahora(ahora)
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
            fila = _fila_cambio(conn, cid)
            if fila[2] != platform:
                raise ValueError(
                    f"plataforma_distinta: mercado={platform} fila={fila[2]}"
                    " (el mercado se abre con la platform del lote)"
                )
            resultados.append((cid, _revertir_una(conn, mercado, fila, momento, huella)))
        return tuple(resultados)
    finally:
        _soltar_candado_lote(conn, platform, owner)


# La huerfana (d): COMMIT sin escritura detectada al arrancar, con el lock ya
# tomado. S.4 la resuelve leyendo el precio vivo: quieto -> `error` con
# `huerfana_sin_efecto` (no gasta cooldown); movido al destino -> `enviado`
# (ack `huerfana_recuperada`) y luego `confirmado/lectura_viva`; otro o
# ilegible -> `error/huerfana_sin_patch` como antes. Solo no-reversas (la
# `pendiente` reversa es del dueno).
_SQL_HUERFANAS_ABIERTAS = """
SELECT c.id, c.listing_id, c.precio_antes, c.precio_antes_currency,
 c.precio_despues, c.precio_despues_currency, l.seller_sku, l.external_id,
 l.product_id
 FROM precio_cambio c JOIN listing l
 ON l.id = c.listing_id AND l.platform = c.platform
 WHERE c.platform = %s AND c.estado = 'pendiente' AND NOT c.es_reversa
 ORDER BY c.id
"""


def cerrar_huerfanas(conn: psycopg.Connection, mercado) -> int:
    """Resuelve huerfanas de la plataforma del mercado; devuelve cuantas."""
    huerfanas = conn.execute(_SQL_HUERFANAS_ABIERTAS, (mercado.clave,)).fetchall()
    for cid, lid, antes, antes_moneda, despues, despues_moneda, sku, asin, pid in huerfanas:
        unidad = _unidad_de_listing(lid, mercado.clave, pid, sku, asin)
        try:
            vivo = mercado.precio_vivo(unidad)
        except Exception as exc:  # noqa: BLE001 - ilegible se sella sin_patch
            logger.info("huerfana cambio=%s vivo_ilegible (%s)", cid, type(exc).__name__)
            vivo = None
        if vivo is not None and (vivo.valor, vivo.moneda) == (antes, antes_moneda):
            _sellar(conn, cid, estado="error", error_code="huerfana_sin_efecto")
            logger.info("huerfana cambio=%s error=huerfana_sin_efecto", cid)
        elif vivo is not None and (vivo.valor, vivo.moneda) == (despues, despues_moneda):
            _sellar(conn, cid, estado="enviado", ack={"origen": "huerfana_recuperada"})
            with conn.transaction():
                conn.execute(
                    "UPDATE precio_cambio SET estado = 'confirmado',"
                    " confirmado_por = 'lectura_viva' WHERE id = %s",
                    (cid,),
                )
            logger.info("huerfana cambio=%s confirmado=lectura_viva", cid)
        else:
            _sellar(conn, cid, estado="error", error_code="huerfana_sin_patch")
            logger.info("huerfana cambio=%s error=huerfana_sin_patch", cid)
    if huerfanas:
        logger.info("corrida %s huerfanas=%s", mercado.clave, len(huerfanas))
    return len(huerfanas)


def cerrar_por_observacion(conn: psycopg.Connection, vitrina, hoy) -> dict:
    """Cierra por la observacion propia del dia posterior al envio.

    Como hoy, pero SOLO los cambios de `vitrina.clave` y leyendo
    `vitrina.observado` (la observacion mas reciente en `(dia_envio,
    hoy]`): igual a `precio_despues` (importe Y moneda) -> `confirmado` /
    `confirmado_por = 'observacion'`; distinta -> `no_confirmado`; sin
    observacion -> no se toca (no se adivina). Vale igual para reversas.
    """
    if not conn.autocommit:
        raise ValueError(
            "cerrar_por_observacion exige conexión en autocommit:"
            " cada cierre confirma al salir del bloque"
        )
    abiertos = conn.execute(
        "SELECT c.id, c.listing_id, c.precio_despues, c.precio_despues_currency,"
        " (c.enviado_at AT TIME ZONE 'UTC')::date, l.seller_sku, l.external_id,"
        " l.product_id"
        " FROM precio_cambio c JOIN listing l"
        " ON l.id = c.listing_id AND l.platform = c.platform"
        " WHERE c.platform = %s AND c.estado = 'enviado'"
        " AND (c.enviado_at AT TIME ZONE 'UTC')::date < %s"
        " ORDER BY c.id",
        (vitrina.clave, hoy),
    ).fetchall()
    cuenta = {"confirmado": 0, "no_confirmado": 0, "intactos": 0}
    for cid, lid, despues, despues_moneda, dia_envio, sku, asin, product_id in abiertos:
        unidad = _unidad_de_listing(lid, vitrina.clave, product_id, sku, asin)
        obs = vitrina.observado(conn, unidad, despues_de=dia_envio, hasta=hoy)
        if obs is None:
            cuenta["intactos"] += 1
            continue
        estado = (
            "confirmado"
            if (obs.precio.valor, obs.precio.moneda) == (despues, despues_moneda)
            else "no_confirmado"
        )
        with conn.transaction():
            conn.execute(
                "UPDATE precio_cambio SET estado = %s, confirmado_por = 'observacion'"
                " WHERE id = %s",
                (estado, cid),
            )
        cuenta[estado] += 1
        logger.info("cerrar cambio=%s estado=%s", cid, estado)
    return cuenta


def insertar_virtual(
    conn: psycopg.Connection,
    decision_id: int,
    listing_id: int,
    platform: str,
    decision,
    ahora: datetime,
) -> int:
    """Sombra fiel (S4 #13): el cambio virtual nace `confirmado`/`virtual`
    con `precio_antes = p_actual` (sin GET de Pricing en sombra), consume
    cooldown y freno sin ocupar el indice de abierto. Cero escrituras."""
    assert decision.p_actual is not None and decision.p_aplicado is not None
    return conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado, enviado_at, confirmado_por)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, false,"
        " 'confirmado', %s, 'virtual') RETURNING id",
        (
            decision_id,
            listing_id,
            platform,
            decision.p_actual.valor,
            decision.p_actual.moneda,
            decision.p_aplicado.valor,
            decision.p_aplicado.moneda,
            ahora,
        ),
    ).fetchone()[0]
