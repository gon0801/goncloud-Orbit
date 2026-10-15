"""Sonda generica de escritura de precios (REPRICING 02, F.4).

Mueve UNA publicacion un centavo sobre su precio vivo y la revierte al
vivo. Dry-run por omision (imprime el plan, cero PATCH). La mutacion
real exige juntos `--acepto-mutacion-real` y `--go` no vacio. Usa
`ORBIT_DSN_DECIDE` (jamas DSN admin) y recibe el mercado de
`precio_mercados.abrir`: este tool no importa `app.spapi.*` ni
`app.meli.*` (generico sobre `Mercado`).

Si la sonda no es aceptada no hay nada que revertir (exit 1). Si la
reversa no es aceptada el precio QUEDA movido: se grita en stdout y
exit 1 para que el dueno lo componga.
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal

import app.precio_mercados as mercados
from app.db import OrbitDbError, connect
from app.precio.puerto import Importe, Miembro, UnidadPrecio

CENTAVO = Decimal("0.01")


class Abortar(RuntimeError):
    """Fallo fail-closed del tool: mensaje al dueno, exit 2."""


def _dsn_decide() -> str:
    dsn = os.environ.get("ORBIT_DSN_DECIDE")
    if not dsn:
        raise Abortar("ORBIT_DSN_DECIDE no esta en el entorno (jamas DSN admin)")
    return dsn


def _unidad(conn, *, listing_id: int, platform: str) -> UnidadPrecio:
    fila = conn.execute(
        "SELECT id, product_id, platform, external_id, seller_sku FROM listing WHERE id = %s",
        (listing_id,),
    ).fetchone()
    if fila is None:
        raise Abortar(f"listing {listing_id} inexistente")
    _lid, producto, plat, externo, sku = fila
    if plat != platform:
        raise Abortar(f"listing {listing_id} es de otra plataforma ({plat}, se pidio {platform})")
    if not sku:
        raise Abortar(f"listing {listing_id} sin seller_sku: no hay ruta que escribir")
    return UnidadPrecio(
        listing_id=listing_id,
        plataforma=platform,
        externo=externo,
        canal=None,
        producto_ancla=producto,
        miembros=(Miembro(producto, sku, None),),
    )


def _corre(conn, mercado, unidad: UnidadPrecio, args) -> int:
    try:
        vivo = mercado.precio_vivo(unidad)
    except Exception as exc:  # noqa: BLE001 - el tool solo reporta, sin secretos
        raise Abortar(f"sin precio vivo para listing {unidad.listing_id}: {exc}") from None
    if not isinstance(vivo, Importe):
        raise Abortar(f"precio vivo invalido para listing {unidad.listing_id}: {vivo!r}")
    sonda = Importe(vivo.valor + CENTAVO, vivo.moneda)
    sku = unidad.miembros[0].seller_sku
    print(
        f"[plan] {unidad.plataforma} listing={unidad.listing_id} sku={sku}"
        f" vivo={vivo.valor:.2f} {vivo.moneda} -> {sonda.valor:.2f} {sonda.moneda}"
        f" -> {vivo.valor:.2f} {vivo.moneda}"
    )
    if not args.acepto_mutacion_real:
        if args.go:
            raise Abortar(
                "falto --acepto-mutacion-real: --go sin --acepto-mutacion-real"
                " no muta (dry-run enganoso)"
            )
        return 0
    if not args.go:
        raise Abortar("falta --go: --acepto-mutacion-real sin --go no muta")
    try:
        ida = mercado.escribir(unidad, sonda, sonda=True)
    except Exception as exc:  # noqa: BLE001 - el tool solo reporta, sin secretos
        raise Abortar(f"sonda de listing {unidad.listing_id} fallo: {exc}") from None
    if not ida.aceptada:
        print(f"[sonda] listing={unidad.listing_id} NO aceptada ({ida.codigo}): sin revertir")
        return 1
    print(f"[sonda] listing={unidad.listing_id} aceptada a {sonda.valor:.2f} {sonda.moneda}")
    try:
        vuelta = mercado.escribir(unidad, vivo, sonda=True)
    except Exception as exc:  # noqa: BLE001 - el tool solo reporta, sin secretos
        raise Abortar(
            f"REVERSA de listing {unidad.listing_id} fallo ({exc}):"
            f" el precio QUEDO en {sonda.valor:.2f} {sonda.moneda}"
        ) from None
    if not vuelta.aceptada:
        print(
            f"[revierte] listing={unidad.listing_id} NO aceptada ({vuelta.codigo}):"
            f" el precio QUEDO en {sonda.valor:.2f} {sonda.moneda}"
        )
        return 1
    print(f"[revierte] listing={unidad.listing_id} aceptada a {vivo.valor:.2f} {vivo.moneda}")
    return 0


def main(argv=None, *, mercado=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--platform", required=True, choices=("amazon_mx", "amazon_us", "meli"))
    ap.add_argument("--listing-id", type=int, required=True)
    ap.add_argument("--acepto-mutacion-real", action="store_true")
    ap.add_argument("--go", default=None)
    args = ap.parse_args(argv)
    try:
        conn = connect(_dsn_decide(), autocommit=True)
    except OrbitDbError as exc:
        raise Abortar(str(exc)) from None
    try:
        unidad = _unidad(conn, listing_id=args.listing_id, platform=args.platform)
        if mercado is None:
            from app.cli import _clientes_precio

            try:
                lector, escritor, _fees, limitador = _clientes_precio(args.platform)
            except Exception as exc:  # noqa: BLE001 - sin clientes no hay go real
                raise Abortar(f"sin clientes para {args.platform}: {exc}") from None
            with mercados.abrir(
                args.platform, lector=lector, escritor=escritor, limitador=limitador
            ) as abierto:
                return _corre(conn, abierto, unidad, args)
        return _corre(conn, mercado, unidad, args)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        print(f"precio_sonda: {exc}", file=sys.stderr)
        sys.exit(2)
