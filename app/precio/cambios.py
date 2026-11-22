"""Protocolo de `precio_cambio` (sale de `app/spapi/precio_write.py`,
que se queda solo con lo de Amazon).

Orden inmutable de S5: `INSERT` de la fila `pendiente` + `COMMIT` ->
escritura via `mercado.escribir` -> sello por ack -> cierre por
observacion del dia siguiente (ARREGLOS 03 P-C: sin relectura inmediata,
que veia el precio viejo porque la plataforma tarda en mostrar el nuevo).
El motor generico no conoce Amazon: la unidad, el precio vivo y el PATCH
salen del `Mercado`.

Candados propios (tests/test_architecture.py): este archivo no importa
`app.spapi`, `app.meli`, `httpx` ni lee tablas `spapi_*`. Escribe en
`precio_cambio` y toma el claim `precio:<platform>` en `ads_optimizer_lock`
via `app/lock.py`. El flujo de reversa en lote vive en `app/precio/reversas.py`
y se reexporta aqui.

Conexion en autocommit; cada bloque confirma al salir: la fila `pendiente`
ya es durable cuando sale la escritura.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import psycopg.errors
from psycopg.types.json import Jsonb

from app.precio import reversas as reversas_mod
from app.precio.puerto import (
    EscrituraNoDisponible,
    Importe,
    PrecioVivoAusente,
    UnidadPrecio,
)
from app.precio.reversas import (
    CorridaEnCurso,
    FilaReversa,
    PlanReversa,
    Seleccion,
    SeleccionPorCambios,
    SeleccionPorCorrida,
    SeleccionPorFecha,
    UniversoEnLive,
    _soltar_candado_lote,
    _tomar_candado_lote,
    planear_reversa,
)

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
    "_tomar_candado_lote",
    "_soltar_candado_lote",
]


class CambioNoReversible(ValueError):
    """`revertir` mal usado: el cambio no existe o no es revertible."""


class DecisionSinAccion(ValueError):
    """`aplicar` mal usado: la decision no mueve precio."""


class PublicacionSinSku(ValueError):
    """La publicacion no tiene SKU con que armar la escritura."""


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
class _FilaCambio:
    """Proyeccion explicita de `_fila_cambio` (sin indices posicionales)."""

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
    external_id: str | None
    product_id: int | None


def _normalizar_ahora(ahora: datetime | None) -> datetime:
    """`ahora` siempre en UTC (r1-A7d): naive se asume UTC, aware se convierte."""
    if ahora is None:
        return datetime.now(UTC)
    if ahora.tzinfo is None:
        return ahora.replace(tzinfo=UTC)
    return ahora.astimezone(UTC)


def _unidad(mercado, conn: psycopg.Connection, listing_id: int) -> UnidadPrecio:
    """La unidad real del listing: la MISMA que decide y escribe.

    Sale del unico lector (`Vitrina.unidades`); sin ella no hay ruta que
    escribir (`PublicacionSinSku`), nunca se arma un miembro falso.
    """
    unidad = mercado.unidades(conn, [listing_id]).get(listing_id)
    if unidad is None:
        raise PublicacionSinSku(f"listing_sin_unidad: listing {listing_id}")
    return unidad


def _fila_cambio(conn: psycopg.Connection, cambio_id: int) -> _FilaCambio:
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
    return _FilaCambio(*fila)


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


def _observada_del_dia(conn: psycopg.Connection, mercado, unidad: UnidadPrecio, *, dia):
    """Observacion propia del dia (para `precio_observado_antes`)."""
    return mercado.observado(conn, unidad, despues_de=dia - timedelta(days=1), hasta=dia)


def _escribir_y_sellar[R](
    conn: psycopg.Connection,
    cambio_id: int,
    *,
    mercado,
    unidad: UnidadPrecio,
    destino: Importe,
    origen: str,
    exito: Callable[[], R],
    fallo: Callable[[str], R],
) -> R:
    """Escritura via el mercado -> sello por ack (sin relectura, P-C)."""
    try:
        acuse = mercado.escribir(unidad, destino)
    except EscrituraNoDisponible:
        logger.info("%s cambio=%s error=parche_sin_sellar", origen, cambio_id)
        _sellar(conn, cambio_id, estado="error", error_code="parche_sin_sellar")
        raise
    except Exception as exc:
        ruta = getattr(mercado, "_ruta_error", None) or "escribir"
        codigo = f"PATCH {ruta} excepcion:{type(exc).__name__}"
        logger.info("%s cambio=%s error=%s", origen, cambio_id, codigo)
        _sellar(conn, cambio_id, estado="error", error_code=codigo)
        raise
    if acuse.aceptada:
        _sellar(conn, cambio_id, estado="enviado", ack=dict(acuse.cuerpo))
        logger.info("%s cambio=%s estado=enviado", origen, cambio_id)
        return exito()
    codigo = acuse.codigo or "desconocido"
    logger.info("%s cambio=%s error=%s", origen, cambio_id, codigo)
    _sellar(conn, cambio_id, estado="error", error_code=codigo)
    return fallo(codigo)


def _hay_cambio_abierto(conn: psycopg.Connection, listing_id: int, platform: str) -> bool:
    return bool(
        conn.execute(
            "SELECT count(*) FROM precio_cambio"
            " WHERE listing_id = %s AND platform = %s"
            " AND estado IN ('pendiente', 'enviado')",
            (listing_id, platform),
        ).fetchone()[0]
    )


def _ejecutar_cas[R](
    conn: psycopg.Connection,
    *,
    mercado,
    listing_id: int,
    platform: str,
    momento: datetime,
    origen: str,
    etiqueta_id: str,
    esperado: Importe,
    etiqueta_esperado: str,
    destino: Importe,
    estado_sin_vivo: str,
    saltar_si_ya_destino: bool,
    chequear_abierto_antes_de_vivo: bool,
    insertar_fn: Callable[[Importe, Decimal | None, str | None], int],
    resultado_fn: Callable[[int | None, str, str | None], R],
) -> R:
    """Nucleo compare-and-set compartido entre `aplicar` y `_revertir_fila`.

    Secuencia sellada: validar unidad y miembros mapeados -> `validar_destino`
    -> precio vivo + abierto -> comparar contra `esperado` -> observada del dia
    -> INSERT + COMMIT (con catch `UniqueViolation`) -> `_escribir_y_sellar`.
    """
    unidad = _unidad(mercado, conn, listing_id)
    sin_producto = sum(1 for m in unidad.miembros if m.product_id is None)
    if sin_producto:
        logger.info(
            "%s %s saltado=variante_sin_mapear (%s sin producto)",
            origen,
            etiqueta_id,
            sin_producto,
        )
        return resultado_fn(None, "saltado", "variante_sin_mapear")
    mercado.validar_destino(unidad)

    if chequear_abierto_antes_de_vivo and _hay_cambio_abierto(conn, listing_id, platform):
        logger.info("%s %s saltado=listing_con_cambio_abierto", origen, etiqueta_id)
        return resultado_fn(None, "saltado", "listing_con_cambio_abierto")

    try:
        vivo = mercado.precio_vivo(unidad)
    except Exception as exc:
        logger.info(
            "%s %s %s=sin_precio_vivo (%s)",
            origen,
            etiqueta_id,
            estado_sin_vivo,
            type(exc).__name__,
        )
        return resultado_fn(None, estado_sin_vivo, "sin_precio_vivo")

    if saltar_si_ya_destino and (vivo.valor, vivo.moneda) == (destino.valor, destino.moneda):
        logger.info("%s %s saltado=ya_aplicado", origen, etiqueta_id)
        return resultado_fn(None, "saltado", "ya_aplicado")

    if not chequear_abierto_antes_de_vivo and _hay_cambio_abierto(conn, listing_id, platform):
        logger.info("%s %s saltado=listing_con_cambio_abierto", origen, etiqueta_id)
        return resultado_fn(None, "saltado", "listing_con_cambio_abierto")

    if (vivo.valor, vivo.moneda) != (esperado.valor, esperado.moneda):
        motivo = (
            f"precio_vivo_distinto: vivo={vivo.valor}/{vivo.moneda}"
            f" vs {etiqueta_esperado}={esperado.valor}/{esperado.moneda}"
        )
        logger.info("%s %s saltado=%s", origen, etiqueta_id, motivo)
        return resultado_fn(None, "saltado", motivo)

    obs = _observada_del_dia(conn, mercado, unidad, dia=momento.date())
    obs_precio = obs.precio.valor if obs is not None else None
    obs_moneda = obs.precio.moneda if obs is not None else None
    try:
        with conn.transaction():
            nuevo_id = insertar_fn(vivo, obs_precio, obs_moneda)
    except psycopg.errors.UniqueViolation:
        logger.info("%s %s saltado=listing_con_cambio_abierto", origen, etiqueta_id)
        return resultado_fn(None, "saltado", "listing_con_cambio_abierto")

    return _escribir_y_sellar(
        conn,
        nuevo_id,
        mercado=mercado,
        unidad=unidad,
        destino=destino,
        origen=origen,
        exito=lambda: resultado_fn(nuevo_id, "enviado", None),
        fallo=lambda codigo: resultado_fn(nuevo_id, "error", codigo),
    )


def aplicar(
    conn: psycopg.Connection,
    mercado,
    decision_id: int,
    *,
    ahora: datetime | None = None,
    corrida_id: int | None = None,
) -> ResultadoCambio:
    """Lleva UNA decision `subir`/`bajar` de modo `live` a la plataforma.

    Idempotente por compare-and-set contra el precio vivo: llamarla dos veces
    escribe una (la segunda salta con `ya_aplicado`). Orden S5: INSERT + COMMIT
    -> PATCH de escritura -> sello. Si el proceso muere entre el INSERT (COMMIT)
    y el PATCH, queda una fila pendiente huerfana que el indice de abierto unico
    vuelve visible (todo reintento la ve abierta y salta); `cerrar_huerfanas`
    la detecta al arrancar.
    """
    if not conn.autocommit:
        raise ValueError(
            "aplicar exige conexión en autocommit: el INSERT tiene que"
            " confirmar antes de la escritura (S5)"
        )
    momento = _normalizar_ahora(ahora)
    dec = conn.execute(
        "SELECT d.listing_id, d.platform, d.resultado, d.mode,"
        " d.p_actual, d.p_actual_currency, d.p_aplicado, d.p_aplicado_currency"
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

    def _insertar(vivo: Importe, obs_precio: Decimal | None, obs_moneda: str | None) -> int:
        return conn.execute(
            "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_observado_antes,"
            " precio_observado_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, enviado_at, corrida_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, 'pendiente', now(), %s)"
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
                corrida_id,
            ),
        ).fetchone()[0]

    return _ejecutar_cas(
        conn,
        mercado=mercado,
        listing_id=listing_id,
        platform=platform,
        momento=momento,
        origen="aplicar",
        etiqueta_id=f"decision={decision_id}",
        esperado=Importe(Decimal(p_actual), p_actual_moneda),
        etiqueta_esperado="p_actual",
        destino=Importe(Decimal(p_aplicado), p_moneda),
        estado_sin_vivo="error",
        saltar_si_ya_destino=True,
        chequear_abierto_antes_de_vivo=False,
        insertar_fn=_insertar,
        resultado_fn=lambda cid, est, mot: ResultadoCambio(id_cambio=cid, estado=est, motivo=mot),
    )


def _revertir_fila(
    conn: psycopg.Connection,
    mercado,
    fila: _FilaCambio,
    momento: datetime,
    *,
    lote: str | None,
) -> ResultadoReversion:
    """Revierte UNA fila ya leida: vivo, INSERT (+COMMIT) y escritura sellada."""
    if fila.es_reversa or not fila.aplicado or fila.enviado_at is None:
        raise CambioNoReversible(
            f"cambio {fila.cambio_id}: solo un cambio real, no-reversa, con enviado_at"
        )
    if fila.estado in ("pendiente", "enviado"):
        logger.info("revertir cambio=%s saltado=original_abierto", fila.cambio_id)
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo="original_abierto")

    def _insertar(vivo: Importe, obs_precio: Decimal | None, obs_moneda: str | None) -> int:
        return conn.execute(
            "INSERT INTO precio_cambio (listing_id, platform, precio_antes,"
            " precio_antes_currency, precio_observado_antes,"
            " precio_observado_antes_currency, precio_despues, precio_despues_currency,"
            " aplicado, estado, enviado_at, es_reversa, reversa_de, lote_reversa)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, 'pendiente',"
            " now(), true, %s, %s) RETURNING id",
            (
                fila.listing_id,
                fila.platform,
                vivo.valor,
                vivo.moneda,
                obs_precio,
                obs_moneda,
                fila.precio_antes,
                fila.precio_antes_currency,
                fila.cambio_id,
                lote,
            ),
        ).fetchone()[0]

    return _ejecutar_cas(
        conn,
        mercado=mercado,
        listing_id=fila.listing_id,
        platform=fila.platform,
        momento=momento,
        origen="revertir",
        etiqueta_id=f"cambio={fila.cambio_id}",
        esperado=Importe(Decimal(fila.precio_despues), fila.precio_despues_currency),
        etiqueta_esperado="despues",
        destino=Importe(Decimal(fila.precio_antes), fila.precio_antes_currency),
        estado_sin_vivo="saltado",
        saltar_si_ya_destino=False,
        chequear_abierto_antes_de_vivo=True,
        insertar_fn=_insertar,
        resultado_fn=lambda rid, est, mot: ResultadoReversion(
            id_reversa=rid, estado=est, motivo=mot
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

    Misma ventana COMMIT-PATCH que `aplicar`: si el proceso muere entre el
    INSERT (COMMIT) y el PATCH, queda una fila pendiente huerfana que el indice
    de abierto unico vuelve visible (todo reintento la ve abierta y salta).
    """
    if not conn.autocommit:
        raise ValueError(
            "revertir exige conexión en autocommit: el INSERT tiene que"
            " confirmar antes de la escritura (S5)"
        )
    momento = _normalizar_ahora(ahora)
    fila = _fila_cambio(conn, cambio_id)
    if fila.platform != mercado.clave:
        raise ValueError(
            f"plataforma_distinta: mercado={mercado.clave} fila={fila.platform}"
            " (el mercado se abre con la platform de la fila)"
        )
    if fila.es_reversa or not fila.aplicado:
        raise CambioNoReversible(
            f"cambio {cambio_id}: solo un cambio real, no-reversa, con enviado_at"
        )
    if fila.estado in ("pendiente", "enviado"):
        logger.info("revertir cambio=%s saltado=original_abierto", cambio_id)
        return ResultadoReversion(id_reversa=None, estado="saltado", motivo="original_abierto")
    if fila.enviado_at is None:
        raise CambioNoReversible(
            f"cambio {cambio_id}: solo un cambio real, no-reversa, con enviado_at"
        )
    return _revertir_fila(conn, mercado, fila, momento, lote=None)


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
    """Reversa manual en lote (delega a `app/precio/reversas.py`)."""
    return reversas_mod.revertir_lote(
        conn,
        mercado,
        plan,
        huella=huella,
        go_literal=go_literal,
        owner=owner,
        ahora=ahora,
        normalizar_ahora_fn=_normalizar_ahora,
        leer_cambio_fn=_fila_cambio,
        unidad_fn=_unidad,
        revertir_fila_fn=_revertir_fila,
        error_no_reversible=CambioNoReversible,
        error_sin_sku=PublicacionSinSku,
        resultado_cls=ResultadoReversion,
    )


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
    for cid, lid, antes, antes_moneda, despues, despues_moneda, _sku, _asin, _pid in huerfanas:
        unidad = _unidad(mercado, conn, lid)
        try:
            vivo = mercado.precio_vivo(unidad)
        except Exception as exc:
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


_SQL_ABIERTOS_A_OBSERVAR = """
SELECT c.id, c.listing_id, c.estado, c.precio_antes, c.precio_antes_currency,
 c.precio_despues, c.precio_despues_currency,
 (c.enviado_at AT TIME ZONE 'UTC')::date
 FROM precio_cambio c JOIN listing l
 ON l.id = c.listing_id AND l.platform = c.platform
 WHERE c.platform = %s AND c.aplicado
   AND (c.estado = 'enviado' OR (c.estado = 'error' AND c.confirmado_por IS NULL))
   AND (c.enviado_at AT TIME ZONE 'UTC')::date < %s
 ORDER BY c.id
"""


def cerrar_por_observacion(conn: psycopg.Connection, vitrina, hoy) -> dict:
    """Cierra por la observacion propia del dia posterior al envio."""
    if not conn.autocommit:
        raise ValueError(
            "cerrar_por_observacion exige conexión en autocommit:"
            " cada cierre confirma al salir del bloque"
        )
    abiertos = conn.execute(_SQL_ABIERTOS_A_OBSERVAR, (vitrina.clave, hoy)).fetchall()
    cuenta = {"confirmado": 0, "no_confirmado": 0, "sin_efecto": 0, "intactos": 0}
    for cid, lid, estado, antes, antes_moneda, despues, despues_moneda, dia_envio in abiertos:
        unidad = _unidad(vitrina, conn, lid)
        obs = vitrina.observado(conn, unidad, despues_de=dia_envio, hasta=hoy)
        visto = None if obs is None else (obs.precio.valor, obs.precio.moneda)
        if visto is None:
            cierre = None
        elif visto == (despues, despues_moneda):
            cierre = "confirmado"
        elif estado == "enviado":
            cierre = "no_confirmado"
        elif visto == (antes, antes_moneda):
            cierre = "sin_efecto"
        else:
            cierre = None
        if cierre is None:
            cuenta["intactos"] += 1
            continue
        with conn.transaction():
            if cierre == "sin_efecto":
                conn.execute(
                    "UPDATE precio_cambio SET confirmado_por = 'observacion' WHERE id = %s",
                    (cid,),
                )
            else:
                conn.execute(
                    "UPDATE precio_cambio SET estado = %s, confirmado_por = 'observacion',"
                    " ack = COALESCE(ack, %s) WHERE id = %s",
                    (cierre, Jsonb({"origen": "observacion"}), cid),
                )
        cuenta[cierre] += 1
        logger.info("cerrar cambio=%s %s estado=%s", cid, estado, cierre)
    return cuenta


def insertar_virtual(
    conn: psycopg.Connection,
    decision_id: int,
    listing_id: int,
    platform: str,
    decision,
    *,
    corrida_id: int,
) -> int:
    """Sombra fiel (S4 #13): el cambio virtual nace `confirmado`/`virtual`."""
    assert decision.p_actual is not None and decision.p_aplicado is not None
    return conn.execute(
        "INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,"
        " precio_antes_currency, precio_despues, precio_despues_currency, aplicado,"
        " estado, enviado_at, confirmado_por, corrida_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, false,"
        " 'confirmado', now(), 'virtual', %s) RETURNING id",
        (
            decision_id,
            listing_id,
            platform,
            decision.p_actual.valor,
            decision.p_actual.moneda,
            decision.p_aplicado.valor,
            decision.p_aplicado.moneda,
            corrida_id,
        ),
    ).fetchone()[0]
