"""Economia observada por producto (ORBIT 19 B.2, politica 0.4 §§5/7).

Lectura de `v_economia_producto` (migrations/0021_economia_observada.sql):
proyeccion madura de `v_margen_producto` (0018, INTACTA) + muestra limitada
(D4: visible, jamas input de sort ni de target automatico).

Reglas respetadas:
- Dato faltante = `None` (regla 3): un producto/listing sin fila en la vista
  llega con todas sus metricas en None, NUNCA en 0.
- Dinero = Decimal (NUMERIC de Postgres); jamas float.
- Grano (platform, product_id): dos listings del mismo producto COMPARTEN
  la economia del producto; el total financiero no se duplica (0.4 §5).
- MX/US no se mezclan: la moneda viaja por fila.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from psycopg import Connection
from psycopg.rows import dict_row


@dataclass(frozen=True)
class EconomiaProducto:
    """Economia observada de UN producto en UNA plataforma.

    Toda metrica es None cuando el dato falta (sin ventas en ventana,
    guard de integridad caido, o producto/listing sin fila).
    """

    platform: str
    product_id: int
    ventana_desde: dt.date | None = None
    ventana_hasta: dt.date | None = None
    moneda: str | None = None
    venta_total: Decimal | None = None
    venta_cubierta: Decimal | None = None
    cobertura: Decimal | None = None
    dias_con_venta: int | None = None
    margen_neto_pct: Decimal | None = None
    integridad_ok: bool | None = None
    muestra_limitada: bool | None = None
    muestra_venta: Decimal | None = None
    muestra_margen_neto_pct: Decimal | None = None
    ledger_fresco_at: dt.datetime | None = None

    @staticmethod
    def vacio(platform: str, product_id: int) -> EconomiaProducto:
        """Producto sin fila en la vista: todo None (regla 3, jamas 0)."""
        return EconomiaProducto(platform=platform, product_id=product_id)


_COLUMNAS = (
    "ventana_desde, ventana_hasta, moneda, venta_total, venta_cubierta, cobertura,"
    " dias_con_venta, margen_neto_pct, integridad_ok, muestra_limitada, muestra_venta,"
    " muestra_margen_neto_pct, ledger_fresco_at"
)


_PROPIAS = frozenset(EconomiaProducto.__dataclass_fields__) - {"platform", "product_id"}


def _a_economia(platform: str, product_id: int, fila) -> EconomiaProducto:
    if fila is None:
        return EconomiaProducto.vacio(platform, product_id)
    return EconomiaProducto(
        platform=platform,
        product_id=product_id,
        **{k: v for k, v in fila.items() if k in _PROPIAS},
    )


def por_producto(
    conn: Connection, platform: str | None = None
) -> dict[tuple[str, int], EconomiaProducto]:
    """Economia por (platform, product_id); solo productos CON fila en la vista
    (con ventas en la ventana madura)."""
    with conn.cursor(row_factory=dict_row) as cur:
        sql = f"SELECT platform, product_id, {_COLUMNAS} FROM v_economia_producto"
        params: tuple = ()
        if platform is not None:
            sql += " WHERE platform = %s"
            params = (platform,)
        filas = cur.execute(sql, params).fetchall()
    return {
        (r["platform"], r["product_id"]): _a_economia(r["platform"], r["product_id"], r)
        for r in filas
    }


def por_listing(conn: Connection, platform: str | None = None) -> dict[int, EconomiaProducto]:
    """Economia por listing_id: cada listing lleva la economia de SU producto
    (grano compartido, 0.4 §5 — dos listings del mismo producto ven el mismo
    total; sumar por listing DUPLICARIA el financiero y esta prohibido).

    Listings sin fila en la vista (sin ventas en ventana) llegan con todo en
    None (regla 3)."""
    with conn.cursor(row_factory=dict_row) as cur:
        sql = (
            f"SELECT l.id AS listing_id, l.platform, l.product_id, {_COLUMNAS}"
            " FROM listing l"
            " LEFT JOIN v_economia_producto e"
            "   ON e.platform = l.platform AND e.product_id = l.product_id"
        )
        params: tuple = ()
        if platform is not None:
            sql += " WHERE l.platform = %s"
            params = (platform,)
        filas = cur.execute(sql, params).fetchall()
    return {r["listing_id"]: _a_economia(r["platform"], r["product_id"], r) for r in filas}
