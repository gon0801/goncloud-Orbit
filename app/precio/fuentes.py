r"""Lectura de la cobertura del catalogo (REPRICING 01 A.7).

Solo SELECT (candado propio en `tests/test_architecture.py`): fuente
canonica `spapi_listing_estado_observation` («activa» = la ultima
observacion por `(seller_sku, platform)` cuyo `status` contiene `BUYABLE`,
con `LEFT JOIN` a `listing` por `seller_sku` y `platform`; dos listings
con el mismo SKU cuentan uno, y una activa sin listing cuenta como
`sin_listing`), canal de la ultima `estimacion_oferta_observation` por
listing, goals vigentes (`valid_from <= hoy AND (valid_to IS NULL OR
valid_to > hoy)`), decisiones del dia y cuenta de identidad (`listing`
por plataforma: identidad, no activas).

Las consultas viven tambien en `docs/evidencia/repricing-01/A.7/consultas/`
(las mismas, con `\set` explicitos para el readback del lead).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from app.precio.cobertura import FilaPublicacion

__all__ = [
    "max_dias_desde_settings",
    "config_vigente_settings",
    "hoy_base",
    "leer_publicaciones",
    "contar_listing_identidad",
]

_SQL_CANO = (
    "SELECT listing_id, seller_sku, platform, asin, status, observed_at FROM ("
    " SELECT DISTINCT ON (e.seller_sku, e.platform)"
    " l.id AS listing_id, e.seller_sku, e.platform, e.asin, e.status, e.observed_at"
    " FROM spapi_listing_estado_observation e"
    " LEFT JOIN listing l ON l.seller_sku = e.seller_sku AND l.platform = e.platform"
    " WHERE e.platform = %s"
    " ORDER BY e.seller_sku, e.platform, e.observed_at DESC, l.id NULLS LAST"
    ") u WHERE u.status LIKE '%%BUYABLE%%'"
)
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
_SQL_PUENTE = "SELECT count(*) FROM listing WHERE platform = %s"
_SQL_HOY = "SELECT (now() AT TIME ZONE 'UTC')::date"


def _numero(settings: Mapping, clave: str) -> Decimal:
    """Con la forma de `app/precio/config.py::_numero` (A.7 no edita `config.py`)."""
    if clave not in settings or settings[clave] is None:
        raise ValueError(f"config sin {clave}")
    valor = settings[clave]
    if isinstance(valor, bool):
        raise ValueError(f"setting {clave}: valor no numerico: {valor!r}")
    try:
        numero = Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"setting {clave}: valor no numerico: {valor!r}") from exc
    if not numero.is_finite():
        raise ValueError(f"setting {clave}: valor no finito: {valor!r}")
    return numero


def _entero(settings: Mapping, clave: str, *, minimo: int, maximo: int) -> int:
    """Con la forma de `app/precio/config.py::_entero` (l.64-72)."""
    numero = _numero(settings, clave)
    if numero != numero.to_integral_value():
        raise ValueError(f"setting {clave}: debe ser entero: {numero}")
    entero = int(numero)
    if not minimo <= entero <= maximo:
        raise ValueError(f"setting {clave}: fuera de cota [{minimo}, {maximo}]: {entero}")
    return entero


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


def leer_publicaciones(
    conn: psycopg.Connection, *, platform: str, hoy: date
) -> list[FilaPublicacion]:
    """Activas canonicas de `platform` con canal, goal y decision del dia."""
    cano = conn.execute(_SQL_CANO, (platform,)).fetchall()
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
    for listing_id, sku, _plat, _asin, _status, observed in cano:
        canal, precio, moneda = (
            canales.get(listing_id, (None, None, None))
            if listing_id is not None
            else (None, None, None)
        )
        resultado, motivo = decisiones.get(listing_id, (None, None))
        filas.append(
            FilaPublicacion(
                listing_id=listing_id,
                seller_sku=sku,
                platform=platform,
                canal=str(canal) if canal is not None else None,
                precio=precio,
                moneda=str(moneda) if moneda is not None else None,
                dias_sin_reportar=(hoy - observed.astimezone(UTC).date()).days,
                tiene_goal=listing_id in goals,
                resultado_hoy=resultado,
                motivo_hoy=motivo,
            )
        )
    return filas


def contar_listing_identidad(conn: psycopg.Connection, *, platform: str) -> int:
    """Filas de `listing` por plataforma: identidad, no activas (el estado
    del puente no esta en Orbit)."""
    return int(conn.execute(_SQL_PUENTE, (platform,)).fetchone()[0])
