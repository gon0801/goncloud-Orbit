r"""Lectura de la cobertura del catalogo (REPRICING 01 A.7).

Solo SELECT (candado propio en `tests/test_architecture.py`): las
activas las arma el catalogo de la vitrina (el adaptador; ver
`MercadoAmazon._SQL_CANO`), canal de la ultima
`estimacion_oferta_observation` por listing, goals vigentes
(`valid_from <= hoy AND (valid_to IS NULL OR valid_to > hoy)`),
decisiones del dia y cuenta de identidad (`listing` por plataforma:
identidad, no activas).

Las consultas viven tambien en `docs/evidencia/repricing-01/A.7/consultas/`
(las mismas, con `\set` explicitos para el readback del lead).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from app.precio.cobertura import FilaPublicacion
from app.precio.compuerta import Retenida
from app.precio.config import _entero as config_entero
from app.precio.config import _modo_universo as config_modo_universo
from app.precio.config import _numero as config_numero
from app.precio.tipos import CambioPrevio, HistorialMargen

__all__ = [
    "max_dias_desde_settings",
    "config_vigente_settings",
    "hoy_base",
    "leer_publicaciones",
    "contar_listing_identidad",
    "universos_encendidos",
    "UltimaCorrida",
    "EstadoSeguridad",
    "leer_seguridad",
    "VentasUnidad",
    "ventas_unidad",
    "cambios_previos",
    "ultima_subida",
    "historial_margenes",
]

_SQL_CANAL = (
    "SELECT DISTINCT ON (listing_id) listing_id, canal, price_amount, price_currency"
    " FROM estimacion_oferta_observation WHERE platform = %s"
    " ORDER BY listing_id, observed_at DESC"
)
_SQL_GOALS = (
    "SELECT listing_id FROM precio_goal WHERE platform = %s"
    " AND valid_from <= %s AND (valid_to IS NULL OR valid_to > %s)"
)
_SQL_DECISIONES = (
    "SELECT listing_id, resultado, motivo FROM precio_decision"
    " WHERE platform = %s AND decision_date = %s"
)
_SQL_LISTING_IDENTIDAD = "SELECT count(*) FROM listing WHERE platform = %s"
_SQL_HOY = "SELECT (now() AT TIME ZONE 'UTC')::date"


def _numero(settings: Mapping, clave: str) -> Decimal:
    """El ayudante unico de `app/precio/config.py` (REPRICING 02 0.a)."""
    return config_numero(settings, clave)


def _entero(settings: Mapping, clave: str, *, minimo: int, maximo: int) -> int:
    return config_entero(settings, clave, minimo=minimo, maximo=maximo)


def max_dias_desde_settings(settings: Mapping) -> int:
    """Dias sin reportar que marcan `catalogo_desactualizado` (cota 1-14)."""
    return _entero(settings, "precio_catalogo_max_dias_sin_reportar", minimo=1, maximo=14)


def config_vigente_settings(conn: psycopg.Connection) -> dict[str, Any]:
    """Settings de la `config_version` vigente (ultimo `id`)."""
    fila = conn.execute("SELECT settings FROM config_version ORDER BY id DESC LIMIT 1").fetchone()
    if fila is None:
        raise ValueError("config sin precio_catalogo_max_dias_sin_reportar: sin config_version")
    return fila[0]


def hoy_base(conn: psycopg.Connection) -> date:
    """Dia UTC de la base (el reloj que manda, no el local)."""
    return conn.execute(_SQL_HOY).fetchone()[0]


def universos_encendidos(conn: psycopg.Connection) -> dict | None:
    """`(plataforma, canal) -> encendido` desde la config vigente.

    `None` = la config vigente no trae `precio_modo_global` (fail-open:
    todo encendido, comportamiento de hoy). Solo lee los modos (no el
    motor completo: la cobertura no exige las demas claves H).
    """
    from app.precio.config import MODOS

    settings = config_vigente_settings(conn)
    global_modo = settings.get("precio_modo_global")
    if global_modo is None:
        return None
    if global_modo not in MODOS:
        raise ValueError(f"setting precio_modo_global: modo invalido: {global_modo!r}")
    por_universo = settings.get("precio_modo_universo") or {}
    orden = {"off": 0, "shadow": 1, "live": 2}
    universos: dict[tuple[str, str | None], bool] = {}
    for fila in conn.execute(
        "SELECT DISTINCT platform, canal::text FROM estimacion_oferta_observation"
        " WHERE platform IS NOT NULL"
        " UNION SELECT DISTINCT platform, NULL::text FROM listing WHERE platform IS NOT NULL"
    ).fetchall():
        platform, canal = fila[0], fila[1]
        if canal is None:
            universos[(platform, canal)] = global_modo != "off"
            continue
        modo = por_universo.get(f"{platform}/{canal}", "off")
        if modo not in MODOS:
            raise ValueError(f"setting precio_modo_universo: modo invalido: {modo!r}")
        universos[(platform, canal)] = min(global_modo, modo, key=lambda m: orden[m]) != "off"
    return universos


def leer_publicaciones(conn: psycopg.Connection, vitrina, *, hoy: date) -> list[FilaPublicacion]:
    """Activas canonicas de `vitrina.clave` con canal, goal y decision del dia.

    La fila la arma el catalogo de la vitrina (`UnidadActiva`); el canal
    sale de la ultima oferta del listing.
    """
    platform = vitrina.clave
    catalogo = vitrina.catalogo(conn, hoy=hoy)
    canales = {
        fila[0]: (fila[1], fila[2], fila[3])
        for fila in conn.execute(_SQL_CANAL, (platform,)).fetchall()
    }
    goals = {fila[0] for fila in conn.execute(_SQL_GOALS, (platform, hoy, hoy)).fetchall()}
    decisiones = {
        fila[0]: (fila[1], fila[2])
        for fila in conn.execute(_SQL_DECISIONES, (platform, hoy)).fetchall()
    }
    filas: list[FilaPublicacion] = []
    for activa in catalogo:
        listing_id = activa.unidad.listing_id if activa.unidad is not None else None
        canal, precio, moneda = (
            canales.get(listing_id, (None, None, None))
            if listing_id is not None
            else (None, None, None)
        )
        resultado, motivo = decisiones.get(listing_id, (None, None))
        filas.append(
            FilaPublicacion(
                listing_id=listing_id,
                seller_sku=activa.seller_sku,
                platform=platform,
                canal=str(canal) if canal is not None else None,
                precio=precio,
                moneda=str(moneda) if moneda is not None else None,
                dias_sin_reportar=activa.dias_sin_reportar,
                tiene_goal=listing_id in goals,
                resultado_hoy=resultado,
                motivo_hoy=motivo,
            )
        )
    return filas


def contar_listing_identidad(conn: psycopg.Connection, *, platform: str) -> int:
    """Filas de `listing` por plataforma: identidad, no activas (el estado
    del puente no esta en Orbit)."""
    return int(conn.execute(_SQL_LISTING_IDENTIDAD, (platform,)).fetchone()[0])


@dataclass(frozen=True)
class UltimaCorrida:
    """La ultima fila `precio_corrida` de la plataforma (abierta o cerrada)."""

    corrida_id: int
    fecha: date
    estado: str
    decisiones: int | None
    movidas: int | None
    retenidas: int | None
    errores: int | None


@dataclass(frozen=True)
class EstadoSeguridad:
    """Lo que `/precios` muestra arriba (bosquejo T, S.3).

    `retenciones_abiertas`: las del dia sin soltar, una por canal (la de la
    corrida mas reciente). `liberable_corrida_id`: la corrida que el boton
    Soltar suelta (`POST /api/precios/compuerta/{id}/liberar`, ruta de G.2),
    `None` si no hay que soltar. `cohorte` la llena S.7 (siempre `None`).
    Los modos son `None` con config vieja (sin claves del apagador): la
    pantalla observa sin exigir (B9), solo el motor fail-closea.
    """

    modo_global: str | None
    modo_universo: Mapping[str, str] | None
    ultima_corrida: UltimaCorrida | None
    retenciones_abiertas: Mapping[str, Retenida]
    liberable_corrida_id: int | None
    cohorte: None


_SQL_SEG_ULTIMA = (
    "SELECT id, fecha, estado, decisiones, movidas, retenidas, errores"
    " FROM precio_corrida WHERE platform = %s ORDER BY id DESC LIMIT 1"
)

_SEG_ABIERTA_WHERE = (
    " FROM precio_compuerta_medicion m JOIN precio_corrida c ON c.id = m.corrida_id"
    " WHERE c.platform = %s AND c.fecha = %s AND m.causa IS NOT NULL"
    " AND NOT EXISTS (SELECT 1 FROM precio_compuerta_liberacion l"
    " WHERE l.corrida_id = m.corrida_id)"
)

_SQL_SEG_ABIERTAS = (
    "SELECT m.canal::text, m.causa, m.medidas, m.nuevas, m.insumo, m.detalle"
    + _SEG_ABIERTA_WHERE
    + " ORDER BY m.corrida_id DESC"
)

_SQL_SEG_LIBERABLE = "SELECT max(m.corrida_id)" + _SEG_ABIERTA_WHERE


def leer_seguridad(conn: psycopg.Connection, plataforma: str, *, hoy: date) -> EstadoSeguridad:
    """Modos, ultima corrida y retenciones abiertas del dia sin soltar.

    Fail-open en los modos (como `universos_encendidos`): config sin
    claves del apagador = modos `None`, pero la ultima corrida y las
    retenciones se leen igual. Clave presente pero invalida = `ValueError`
    que la nombra.
    """
    from app.precio.config import MODOS

    settings = config_vigente_settings(conn)
    modo_global = settings.get("precio_modo_global")
    if modo_global is not None and modo_global not in MODOS:
        raise ValueError(f"setting precio_modo_global: modo invalido: {modo_global!r}")
    crudo_universo = settings.get("precio_modo_universo")
    if modo_global is None or crudo_universo is None:
        modo_universo = None
    else:
        modo_universo = config_modo_universo(settings, "precio_modo_universo")
    fila = conn.execute(_SQL_SEG_ULTIMA, (plataforma,)).fetchone()
    ultima = (
        UltimaCorrida(
            corrida_id=fila[0],
            fecha=fila[1],
            estado=fila[2],
            decisiones=fila[3],
            movidas=fila[4],
            retenidas=fila[5],
            errores=fila[6],
        )
        if fila is not None
        else None
    )
    abiertas: dict[str, Retenida] = {}
    for canal, causa, medidas, nuevas, insumo, detalle in conn.execute(
        _SQL_SEG_ABIERTAS, (plataforma, hoy)
    ).fetchall():
        abiertas.setdefault(
            canal,
            Retenida(
                causa=causa,
                medidas=medidas,
                nuevas=nuevas,
                insumo=insumo,
                detalle=detalle or "",
            ),
        )
    liberable = conn.execute(_SQL_SEG_LIBERABLE, (plataforma, hoy)).fetchone()[0]
    return EstadoSeguridad(
        modo_global=modo_global,
        modo_universo=dict(modo_universo) if modo_universo is not None else None,
        ultima_corrida=ultima,
        retenciones_abiertas=abiertas,
        liberable_corrida_id=liberable,
        cohorte=None,
    )


# ---------------------------------------------------------------------------
# Lectores de historia de unidad (M.4: mudados de `app/spapi/precio_mercado`;
# son por plataforma-parametro, los dos adaptadores los usan).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VentasUnidad:
    """Ventas atribuidas de la unidad + cobertura, para armar `HistoriaUnidad`."""

    dias: Mapping[date, int]
    primera_venta: date | None
    sin_atribuir: int
    ledger_cubierto_hasta: date | None


def ventas_unidad(
    conn: psycopg.Connection,
    *,
    listing_id: int,
    producto_ancla: int,
    platform: str,
    desde: date,
    hoy: date,
) -> VentasUnidad:
    """Ventas de `v_precio_venta_unidad` + cobertura del ledger (por unidad)."""
    dias: dict[date, int] = {}
    for dia, qty in conn.execute(
        "SELECT event_date, quantity FROM v_precio_venta_unidad"
        " WHERE listing_id = %s AND platform = %s AND quantity IS NOT NULL",
        (listing_id, platform),
    ).fetchall():
        if desde <= dia <= hoy:
            dias[dia] = dias.get(dia, 0) + qty
    primera = conn.execute(
        "SELECT min(event_date) FROM v_precio_venta_unidad"
        " WHERE listing_id = %s AND platform = %s AND quantity IS NOT NULL",
        (listing_id, platform),
    ).fetchone()[0]
    hasta = hoy - timedelta(days=1)
    sin_atribuir = sum(
        fila[0]
        for fila in conn.execute(
            "SELECT quantity FROM v_precio_venta_unidad"
            " WHERE product_id = %s AND platform = %s AND listing_id IS NULL"
            " AND quantity IS NOT NULL AND event_date BETWEEN %s AND %s",
            (producto_ancla, platform, desde, hasta),
        ).fetchall()
    )
    cubierto = conn.execute(
        "SELECT max(e.event_date) FROM ledger_event e"
        " JOIN ingest_run r ON r.id = e.ingest_run_id"
        " WHERE e.platform = %s AND e.kind = 'sale' AND r.ok"
        " AND r.source = 'accounting_ledger_events'",
        (platform,),
    ).fetchone()[0]
    return VentasUnidad(
        dias=dias,
        primera_venta=primera,
        sin_atribuir=sin_atribuir,
        ledger_cubierto_hasta=cubierto,
    )


def _direccion(antes: Decimal, despues: Decimal) -> str | None:
    if despues > antes:
        return "subir"
    if despues < antes:
        return "bajar"
    return None


def cambios_previos(
    conn: psycopg.Connection, listing_id: int, platform: str
) -> tuple[CambioPrevio, ...]:
    """Cambios no-reversa del par para #6, #9 y #10 (reales y virtuales).

    La direccion sale de comparar importes (la fila no la guarda). Un
    cambio con `antes == despues` no tiene direccion y se salta (no deberia
    existir: el motor solo mueve precio). `sin_efecto` (REPRICING 02 S.4,
    `datos.sql` 8) se deriva al leer: `error` con codigo
    `huerfana_sin_efecto` o con readback igual a `precio_antes` (importe
    Y moneda); sin readback no hay prueba y cuenta normal.
    """
    filas = conn.execute(
        "SELECT (enviado_at AT TIME ZONE 'UTC')::date, precio_antes, precio_antes_currency,"
        " precio_despues, estado, es_reversa, aplicado, error_code,"
        " readback_precio, readback_precio_currency"
        " FROM precio_cambio"
        " WHERE listing_id = %s AND platform = %s AND NOT es_reversa"
        " ORDER BY id",
        (listing_id, platform),
    ).fetchall()
    cambios: list[CambioPrevio] = []
    for (
        enviado_en,
        antes,
        antes_moneda,
        despues,
        estado,
        _reversa,
        aplicado,
        error_code,
        readback,
        readback_moneda,
    ) in filas:
        direccion = _direccion(antes, despues)
        if direccion is None or enviado_en is None:
            continue
        sin_efecto = estado == "error" and (
            error_code == "huerfana_sin_efecto"
            or (
                readback is not None
                and readback_moneda is not None
                and (readback, readback_moneda) == (antes, antes_moneda)
            )
        )
        cambios.append(
            CambioPrevio(
                enviado_en=enviado_en,
                direccion=direccion,
                estado=estado,
                es_reversa=False,
                aplicado=bool(aplicado),
                sin_efecto=sin_efecto,
            )
        )
    return tuple(cambios)


def ultima_subida(cambios: Sequence[CambioPrevio]) -> date | None:
    """Dia de la ultima subida propia que movio precio (None si no hay).

    Solo cuentan `subir` ejecutadas (`enviado`/`confirmado`); un `error`,
    un `pendiente` o un `sin_efecto` (el readback probo que el precio no
    se movio) no abren antes/despues para el trafico. El virtual de
    shadow (`confirmado` sin `aplicado`) tampoco: cero escrituras.
    """
    dias = [
        c.enviado_en
        for c in cambios
        if c.direccion == "subir"
        and c.estado in ("enviado", "confirmado")
        and c.aplicado
        and not c.sin_efecto
    ]
    return max(dias) if dias else None


def historial_margenes(
    conn: psycopg.Connection, listing_id: int, platform: str
) -> tuple[HistorialMargen, ...]:
    """Distancias `|m - goal|` al decidir cada cambio (#10)."""
    filas = conn.execute(
        "SELECT d.m_actual, d.goal, d.decision_date, c.precio_antes, c.precio_despues,"
        " c.aplicado"
        " FROM precio_cambio c JOIN precio_decision d ON d.id = c.decision_id"
        " WHERE c.listing_id = %s AND c.platform = %s AND NOT c.es_reversa"
        " AND d.m_actual IS NOT NULL AND d.goal IS NOT NULL"
        " ORDER BY c.id",
        (listing_id, platform),
    ).fetchall()
    puntos: list[HistorialMargen] = []
    for m_actual, goal, fecha, antes, despues, aplicado in filas:
        direccion = _direccion(antes, despues)
        if direccion is None or fecha is None:
            continue
        puntos.append(
            HistorialMargen(
                direccion=direccion,
                distancia=abs(Decimal(m_actual) - Decimal(goal)),
                fecha=fecha,
                aplicado=bool(aplicado),
            )
        )
    return tuple(puntos)
