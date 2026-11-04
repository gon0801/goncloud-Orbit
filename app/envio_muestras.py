"""Job diario de muestras de envio (REPRICING 02 F.2, seccion O).

Rol `app_decide`, unico escritor de `precio_envio_muestra`. Se corre asi:

    python -m app.envio_muestras --platform amazon_mx --canal fbm

Lee los cargos `shipping_fee` de la ventana (`precio_envio_ventana_dias`,
dias cerrados `[hoy-ventana, hoy-1]`), mapea cada orden a su unico producto
por las ventas de la misma orden y escribe una muestra por producto con
publicacion en la plataforma (en MeLi, tambien por miembro activo). Solo
inserta si la muestra cambio respecto de su ultima fila: append-only sin
filas repetidas.

Mapeo de orden estricto: todas las ventas de la orden con el MISMO producto
no nulo. Una venta sin producto contamina a `sin_producto` (podria ser otro
producto compartiendo el envio); dos productos distintos a
`multiproducto`. Las ordenes sin etiqueta (HB solo, chargebacks) no entran
por regla y no se cuentan en `Exclusiones`: esos contadores son resultados
de mapeo, no de regla.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta

import psycopg

from app.db import connect
from app.estimacion_universo import universo_de
from app.precio.config import leer_config_motor
from app.precio.envio import (
    CargoEnvio,
    Exclusiones,
    MuestraNueva,
    OrdenMedida,
    clasificar_cargo,
    costo_de_orden,
    resolver_envio,
)
from app.precio.tipos import Importe
from app.redaction import scrub

logger = logging.getLogger(__name__)

__all__ = ["refrescar_muestras", "main"]

_SQL_CARGOS = (
    "SELECT order_id, source_event_id, fee_type, amount, amount_currency, event_date"
    " FROM ledger_event"
    " WHERE platform = %s AND kind = 'fee' AND fee_type = 'shipping_fee'"
    " AND event_date BETWEEN %s AND %s"
)
_SQL_VENTAS = (
    "SELECT order_id, product_id FROM ledger_event"
    " WHERE platform = %s AND kind = 'sale' AND event_date BETWEEN %s AND %s"
    " AND order_id IS NOT NULL"
)
# MeLi cuenta el escenario por miembro activo (v_precio_unidad_miembro): sin
# su muestra, la publicacion con variantes sale `envio_sin_historia`.
_SQL_PRODUCTOS = (
    "SELECT product_id FROM listing WHERE platform = %s AND product_id IS NOT NULL"
    " UNION"
    " SELECT product_id FROM v_precio_unidad_miembro"
    " WHERE platform = %s AND product_id IS NOT NULL"
)
_SQL_FAMILIAS = "SELECT product_id, familia_id FROM producto_familia WHERE platform = %s"
_SQL_ULTIMA = (
    "SELECT origen, valor, valor_currency, envios, donantes, familia_id,"
    " ventana_desde, ventana_hasta, mediana, mediana_currency, p90, p90_currency,"
    " maximo, maximo_currency, regla_version"
    " FROM precio_envio_muestra"
    " WHERE product_id = %s AND platform = %s AND canal = %s"
    " ORDER BY calculada_at DESC, id DESC LIMIT 1"
)
_SQL_INSERT = (
    "INSERT INTO precio_envio_muestra (product_id, platform, canal, ventana_desde,"
    " ventana_hasta, envios, percentil, valor, valor_currency, mediana,"
    " mediana_currency, p90, p90_currency, maximo, maximo_currency, origen,"
    " familia_id, donantes, regla_version, excl_multiproducto, excl_sin_producto,"
    " cargos_otros)"
    " VALUES (%s, %s, %s, %s, %s, %s, 'mediana', %s, %s, %s, %s, %s, %s, %s, %s,"
    " %s, %s, %s, %s, %s, %s, %s)"
)
_SQL_CONFIG = "SELECT settings FROM config_version ORDER BY id DESC LIMIT 1"


def _medidas(
    conn: psycopg.Connection, plataforma: str, desde: date, hasta: date
) -> tuple[dict[str, OrdenMedida], Exclusiones]:
    cargos_por_orden: dict[str, list[CargoEnvio]] = {}
    otros = 0
    sin_producto = 0
    for order_id, source, fee_type, monto, moneda, dia in conn.execute(
        _SQL_CARGOS, (plataforma, desde, hasta)
    ):
        fuente = clasificar_cargo(source, fee_type, plataforma)
        if fuente == "otros":
            otros += 1
            continue
        if order_id is None:
            sin_producto += 1
            continue
        cargos_por_orden.setdefault(order_id, []).append(
            CargoEnvio(
                orden=order_id,
                fuente=fuente,
                importe=Importe(abs(monto), moneda),
                dia=dia,
            )
        )
    productos_por_orden: dict[str, list[int | None]] = {}
    for order_id, product_id in conn.execute(_SQL_VENTAS, (plataforma, desde, hasta)):
        productos_por_orden.setdefault(order_id, []).append(product_id)
    medidas: dict[str, OrdenMedida] = {}
    multiproducto = 0
    for orden, cargos in cargos_por_orden.items():
        costo = costo_de_orden(cargos)
        ventas = productos_por_orden.get(orden, [])
        if not ventas or any(pid is None for pid in ventas):
            sin_producto += 1
            continue
        unicos = set(ventas)
        if len(unicos) > 1:
            multiproducto += 1
            continue
        if costo is None:
            continue
        medidas[orden] = OrdenMedida(
            orden=orden, product_id=unicos.pop(), costo=costo, dia=cargos[0].dia
        )
    return medidas, Exclusiones(multiproducto, sin_producto, otros)


def _igual(ultima: tuple, muestra: MuestraNueva) -> bool:
    p90 = None if muestra.p90 is None else muestra.p90.valor
    p90_moneda = None if muestra.p90 is None else muestra.p90.moneda
    return ultima == (
        muestra.origen,
        muestra.valor.valor,
        muestra.valor.moneda,
        muestra.envios,
        muestra.donantes,
        muestra.familia_id,
        muestra.ventana[0],
        muestra.ventana[1],
        muestra.mediana.valor,
        muestra.mediana.moneda,
        p90,
        p90_moneda,
        muestra.maximo.valor,
        muestra.maximo.moneda,
        muestra.regla_version,
    )


def refrescar_muestras(
    conn: psycopg.Connection,
    plataforma: str,
    canal: str,
    *,
    hoy: date,
    ventana_dias: int,
) -> Exclusiones:
    """Todo o nada por corrida; devuelve lo que no entro, contado."""
    desde = hoy - timedelta(days=ventana_dias)
    hasta = hoy - timedelta(days=1)
    with conn.transaction():
        medidas, excl = _medidas(conn, plataforma, desde, hasta)
        por_producto: dict[int, list[OrdenMedida]] = {}
        for medida in medidas.values():
            por_producto.setdefault(medida.product_id, []).append(medida)
        productos = [
            fila[0] for fila in conn.execute(_SQL_PRODUCTOS, (plataforma, plataforma)).fetchall()
        ]
        familias = {pid: fid for pid, fid in conn.execute(_SQL_FAMILIAS, (plataforma,)).fetchall()}
        por_familia: dict[int, list[int]] = {}
        for pid, fid in familias.items():
            por_familia.setdefault(fid, []).append(pid)
        todas = list(medidas.values())
        for pid in sorted(productos):
            fid = familias.get(pid)
            hermanas = [
                m
                for hermano in por_familia.get(fid, ())
                if hermano != pid
                for m in por_producto.get(hermano, [])
            ]
            muestra = resolver_envio(
                pid,
                propias=por_producto.get(pid, []),
                hermanas=hermanas,
                familia_id=fid,
                marketplace=todas,
                plataforma=plataforma,
                canal=canal,
                ventana=(desde, hasta),
            )
            if muestra is None:
                continue
            ultima = conn.execute(_SQL_ULTIMA, (pid, plataforma, canal)).fetchone()
            if ultima is not None and _igual(ultima, muestra):
                continue
            conn.execute(
                _SQL_INSERT,
                (
                    muestra.product_id,
                    muestra.plataforma,
                    muestra.canal,
                    muestra.ventana[0],
                    muestra.ventana[1],
                    muestra.envios,
                    muestra.valor.valor,
                    muestra.valor.moneda,
                    muestra.mediana.valor,
                    muestra.mediana.moneda,
                    None if muestra.p90 is None else muestra.p90.valor,
                    None if muestra.p90 is None else muestra.p90.moneda,
                    muestra.maximo.valor,
                    muestra.maximo.moneda,
                    muestra.origen,
                    muestra.familia_id,
                    muestra.donantes,
                    muestra.regla_version,
                    excl.ordenes_multiproducto,
                    excl.ordenes_sin_producto,
                    excl.cargos_otros,
                ),
            )
    return excl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.envio_muestras",
        description="Muestras diarias de costo de envio por producto (REPRICING 02 F.2).",
    )
    parser.add_argument("--platform", required=True)
    parser.add_argument("--canal", required=True)
    parser.add_argument("--ventana-dias", type=int, default=None)
    args = parser.parse_args(argv)

    universo = universo_de(args.platform, args.canal)
    if universo is None or universo.envio != "muestra":
        print(
            f"universo {args.platform}/{args.canal} sin muestras (fba usa politica, no job)",
            file=sys.stderr,
        )
        return 2

    dsn = os.environ.get("ORBIT_DSN_DECIDE")
    if not dsn:
        print(
            "ORBIT_DSN_DECIDE no esta definido: no se puede muestrear (fail-closed)",
            file=sys.stderr,
        )
        return 2

    try:
        conn = connect(dsn)
        try:
            fila = conn.execute(_SQL_CONFIG).fetchone()
            if fila is None:
                print("sin config_version: no se puede muestrear", file=sys.stderr)
                return 1
            ventana = args.ventana_dias
            if ventana is None:
                ventana = leer_config_motor(fila[0]).envio_ventana_dias
            elif not 30 <= ventana <= 365:
                print(f"ventana fuera de cota 30-365: {ventana}", file=sys.stderr)
                return 2
            hoy = datetime.now(UTC).date()
            excl = refrescar_muestras(
                conn, args.platform, args.canal, hoy=hoy, ventana_dias=ventana
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"muestras fallaron: {scrub(str(exc))}", file=sys.stderr)
        return 1

    print(
        f"== Muestras de envio {args.platform}/{args.canal} =="
        f" multiproducto={excl.ordenes_multiproducto}"
        f" sin_producto={excl.ordenes_sin_producto} otros={excl.cargos_otros}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
