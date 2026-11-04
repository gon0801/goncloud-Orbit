"""Sonda generica de escritura de precios (REPRICING 02, F.4).

Mueve UNA publicacion un centavo sobre su precio vivo y la revierte al
vivo. Dry-run por omision (imprime el plan, cero PATCH). La mutacion
real exige juntos `--acepto-mutacion-real` y `--go` no vacio. Usa
`ORBIT_DSN_DECIDE` (jamas DSN admin) y recibe el mercado de
`precio_mercados.abrir`: este tool no importa `app.spapi.*` ni
`app.meli.*` (generico sobre `Mercado`). Nunca imprime el seller_sku
(su salida se guarda en el repo publico: solo `listing_id`).

`--lee-vivo` solo lee e imprime el precio vivo (cero escritura;
rechaza `--go` y `--acepto-mutacion-real`). `ORBIT_SONDA_SIM` (ruta
a un JSON) mas `--mercado-sim` cambian el mercado real por uno de
archivo, solo para la simulacion X.2/X.3 de `sonda.sh`: la variable
sola no activa nada (una env colgada jamas finge aceptadas). Si la
sonda no es aceptada no hay nada que revertir (exit 1). Si la
reversa no es aceptada el precio QUEDA movido: se grita en stdout y
exit 1 para que el dueno lo componga.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal

import app.precio_mercados as mercados
from app.db import OrbitDbError, connect
from app.precio.puerto import Acuse, Importe, Miembro, PrecioVivoAusente, UnidadPrecio

CENTAVO = Decimal("0.01")


class Abortar(RuntimeError):
    """Fallo fail-closed del tool: mensaje al dueno, exit 2."""


class _MercadoSim:
    """Mercado de archivo, solo para la simulacion X.2/X.3 (`sonda.sh`).

    JSON `{"<listing_id>": {"valor": "116.00", "moneda": "MXN"}}`.
    `precio_vivo` lee; `escribir` guarda (acepta siempre) con
    escritura atomica (tmp + rename). Una corrida a la vez.
    """

    def __init__(self, ruta: str):
        self.ruta = ruta
        try:
            with open(ruta, encoding="utf-8") as f:
                self.precios = json.load(f)
        except (OSError, ValueError) as exc:
            raise Abortar(f"ORBIT_SONDA_SIM ilegible ({ruta}): {exc}") from None
        if not isinstance(self.precios, dict):
            raise Abortar(f"ORBIT_SONDA_SIM sin objeto ({ruta})")

    def precio_vivo(self, unidad: UnidadPrecio) -> Importe:
        fila = self.precios.get(str(unidad.listing_id))
        if not isinstance(fila, dict) or "valor" not in fila or "moneda" not in fila:
            raise PrecioVivoAusente(f"listing {unidad.listing_id} sin precio sim")
        return Importe(Decimal(str(fila["valor"])), str(fila["moneda"]))

    def escribir(self, unidad: UnidadPrecio, precio: Importe, *, sonda: bool = False) -> Acuse:
        self.precios[str(unidad.listing_id)] = {
            "valor": f"{precio.valor:.2f}",
            "moneda": precio.moneda,
        }
        tmp = self.ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.precios, f, sort_keys=True)
        os.replace(tmp, self.ruta)
        return Acuse(aceptada=True, codigo="sim", cuerpo={"sonda": sonda})


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
    if platform == "meli":
        # R05-r3 B1: igual que `MercadoMeli.catalogo` (misma consulta,
        # mismo orden): la unidad lleva las variantes mapeadas para que
        # el PUT pruebe la forma candidata. Sin miembros no hay ruta.
        miembros = tuple(
            Miembro(pid, str(msku), variante)
            for msku, pid, variante in conn.execute(
                "SELECT seller_sku, product_id, variante_ext FROM listing_miembro"
                " WHERE listing_id = %s AND platform = 'meli' AND activo"
                " ORDER BY seller_sku",
                (listing_id,),
            ).fetchall()
        )
        if not miembros:
            raise Abortar(f"listing {listing_id} sin miembros activos: no hay ruta que escribir")
    else:
        miembros = (Miembro(producto, sku, None),)
    return UnidadPrecio(
        listing_id=listing_id,
        plataforma=platform,
        externo=externo,
        canal=None,
        producto_ancla=producto,
        miembros=miembros,
    )


def _lee(conn, mercado, unidad: UnidadPrecio) -> int:
    try:
        vivo = mercado.precio_vivo(unidad)
    except Exception as exc:
        raise Abortar(f"sin precio vivo para listing {unidad.listing_id}: {exc}") from None
    if not isinstance(vivo, Importe):
        raise Abortar(f"precio vivo invalido para listing {unidad.listing_id}: {vivo!r}")
    print(f"vivo listing={unidad.listing_id} valor={vivo.valor:.2f} moneda={vivo.moneda}")
    return 0


def _corre(conn, mercado, unidad: UnidadPrecio, args) -> int:
    try:
        vivo = mercado.precio_vivo(unidad)
    except Exception as exc:
        raise Abortar(f"sin precio vivo para listing {unidad.listing_id}: {exc}") from None
    if not isinstance(vivo, Importe):
        raise Abortar(f"precio vivo invalido para listing {unidad.listing_id}: {vivo!r}")
    sonda = Importe(vivo.valor + CENTAVO, vivo.moneda)
    print(
        f"[plan] {unidad.plataforma} listing={unidad.listing_id}"
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
    except Exception as exc:
        raise Abortar(f"sonda de listing {unidad.listing_id} fallo: {exc}") from None
    if not ida.aceptada:
        print(f"[sonda] listing={unidad.listing_id} NO aceptada ({ida.codigo}): sin revertir")
        return 1
    print(f"[sonda] listing={unidad.listing_id} aceptada a {sonda.valor:.2f} {sonda.moneda}")
    try:
        vuelta = mercado.escribir(unidad, vivo, sonda=True)
    except Exception as exc:
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
    ap.add_argument("--lee-vivo", action="store_true")
    ap.add_argument("--mercado-sim", action="store_true")
    args = ap.parse_args(argv)
    sim = os.environ.get("ORBIT_SONDA_SIM")
    if sim and not args.mercado_sim:
        raise Abortar("ORBIT_SONDA_SIM exige --mercado-sim (solo sim X.2/X.3)")
    if args.mercado_sim and not sim:
        raise Abortar("--mercado-sim sin ORBIT_SONDA_SIM")
    if args.lee_vivo and (args.acepto_mutacion_real or args.go):
        raise Abortar("--lee-vivo no muta: sin --acepto-mutacion-real ni --go")
    try:
        conn = connect(_dsn_decide(), autocommit=True)
    except OrbitDbError as exc:
        raise Abortar(str(exc)) from None
    try:
        unidad = _unidad(conn, listing_id=args.listing_id, platform=args.platform)
        if mercado is None and sim:
            mercado = _MercadoSim(sim)
        if mercado is not None:
            if args.lee_vivo:
                return _lee(conn, mercado, unidad)
            return _corre(conn, mercado, unidad, args)
        from app.cli import _clientes_precio

        try:
            lector, escritor, fees, limitador = _clientes_precio(args.platform)
        except Exception as exc:
            raise Abortar(f"sin clientes para {args.platform}: {exc}") from None
        try:
            # MeLi exige el cotizador (`fees`) ademas de lector y escritor.
            with mercados.abrir(
                args.platform, lector=lector, escritor=escritor, fees=fees, limitador=limitador
            ) as abierto:
                if args.lee_vivo:
                    return _lee(conn, abierto, unidad)
                return _corre(conn, abierto, unidad, args)
        except Abortar:
            raise
        except mercados.ConfigInvalida as exc:
            raise Abortar(f"sin mercado para {args.platform}: {exc}") from None
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        print(f"precio_sonda: {exc}", file=sys.stderr)
        sys.exit(2)
