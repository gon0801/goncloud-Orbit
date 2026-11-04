"""Reversa manual de precios en lote (REPRICING 02, S.5).

Nunca automatica: el dueno corre dry-run, revisa el plan y su huella, y
solo entonces el go con la misma huella. El plan sale solo de la base
(cero cuota de plataforma); la huella cubre la seleccion y el precio al
que vuelve cada fila, no el vivo. En el go cada fila se revalida contra
el vivo: un precio movido entre el plan y el go salta esa fila y el lote
sigue. Un original `enviado` con el precio ya aplicado se cierra y se
revierte el mismo dia.

Selectores (uno por corrida): `--cambio-id` (repetible), `--fecha` con
`--platform`, `--corrida`. Un lote, una plataforma. El go toma el mismo
candado `precio:<platform>` que la corrida (no corren a la vez) y aborta
si algun universo del lote esta en `live`: primero se apaga, luego se
revierte.

Dry-run por omision (imprime plan + huella, cero PATCH). La mutacion
real exige juntos `--acepto-mutacion-real`, `--huella H` (la del
dry-run) y `--go` no vacio. Usa `ORBIT_DSN_DECIDE` (jamas DSN admin) y
recibe el mercado de `precio_mercados.abrir`: este tool no importa
`app.spapi.*`. El go devuelve 1 si alguna reversa termina en `error`
(los saltados no cuentan); 0 si ninguna falla.

Conexion en autocommit; cada bloque confirma al salir: la fila
`pendiente` ya es durable cuando sale la escritura (S5).
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from datetime import date

import app.precio_mercados as mercados
from app.db import OrbitDbError, connect
from app.precio import cambios as cambios_mod


class Abortar(RuntimeError):
    """Fallo fail-closed del tool: mensaje al dueno, exit 2."""


def _dsn_decide() -> str:
    dsn = os.environ.get("ORBIT_DSN_DECIDE")
    if not dsn:
        raise Abortar("ORBIT_DSN_DECIDE no esta en el entorno (jamas DSN admin)")
    return dsn


def _seleccion(args):
    n = sum([bool(args.cambios), args.fecha is not None, args.corrida is not None])
    if n != 1:
        raise Abortar("elige un selector: --cambio-id, --fecha con --platform, o --corrida")
    if args.fecha is not None:
        if not args.platform:
            raise Abortar("--fecha exige --platform")
        try:
            dia = date.fromisoformat(args.fecha)
        except ValueError:
            raise Abortar(f"--fecha invalida (AAAA-MM-DD): {args.fecha}") from None
        return cambios_mod.SeleccionPorFecha(args.platform, dia), args.platform
    if args.platform:
        raise Abortar("--platform solo vale con --fecha")
    if args.corrida is not None:
        return cambios_mod.SeleccionPorCorrida(args.corrida), None
    return cambios_mod.SeleccionPorCambios(ids=tuple(args.cambios)), None


def _platform_de_corrida(conn, corrida_id: int) -> str:
    fila = conn.execute(
        "SELECT platform FROM precio_corrida WHERE id = %s", (corrida_id,)
    ).fetchone()
    if fila is None:
        raise Abortar(f"corrida {corrida_id} inexistente")
    return fila[0]


def _platform_de_ids(conn, ids: tuple[int, ...]) -> str:
    filas = conn.execute(
        "SELECT id, platform FROM precio_cambio WHERE id = ANY(%s)", (list(ids),)
    ).fetchall()
    vistos = {f[0] for f in filas}
    for pedido in ids:
        if pedido not in vistos:
            raise Abortar(f"cambio {pedido} inexistente")
    plats = sorted({f[1] for f in filas})
    if len(plats) > 1:
        raise Abortar(f"lote multi-plataforma: {plats} (corre una vez por plataforma)")
    return plats[0]


def _linea_plan(fila, platform: str) -> str:
    if fila.estado_plan == "revertible":
        return (
            f"[revertir] cambio={fila.cambio_id} {fila.seller_sku} {platform}"
            f" {fila.desde.valor:.2f} {fila.desde.moneda}"
            f" -> {fila.volver_a.valor:.2f} {fila.volver_a.moneda}"
        )
    return (
        f"[saltar] cambio={fila.cambio_id} {fila.seller_sku} {platform} motivo={fila.estado_plan}"
    )


def _linea_go(cid: int, res) -> str:
    if res.estado == "saltado":
        return f"[saltar] cambio={cid} motivo={res.motivo}"
    base = f"[hecho] cambio={cid} estado={res.estado}"
    return base if res.motivo is None else f"{base} motivo={res.motivo}"


def _corre(conn, mercado, platform: str, seleccion, args) -> int:
    try:
        plan = cambios_mod.planear_reversa(conn, mercado, seleccion)
    except ValueError as exc:
        raise Abortar(str(exc)) from None
    for fila in plan.filas:
        print(_linea_plan(fila, platform))
    print(f"huella: {plan.huella}")
    if not args.acepto_mutacion_real:
        if args.go:
            raise Abortar(
                "falto --acepto-mutacion-real: --go --huella sin"
                " --acepto-mutacion-real no muta (dry-run enganoso)"
            )
        return 0
    if not args.go:
        raise Abortar("falta --go: --acepto-mutacion-real sin --go no muta")
    if not args.huella:
        raise Abortar("falta --huella: el go exige la huella del dry-run")
    if args.huella != plan.huella:
        raise Abortar(
            f"--huella {args.huella} != huella del plan {plan.huella}: re-corre el dry-run"
        )
    try:
        resultados = cambios_mod.revertir_lote(
            conn,
            mercado,
            plan,
            huella=args.huella,
            go_literal=args.go,
            owner=f"{socket.gethostname()}:{os.getpid()}",
        )
    except (
        cambios_mod.CorridaEnCurso,
        cambios_mod.UniversoEnLive,
        ValueError,
    ) as exc:
        raise Abortar(str(exc)) from None
    for cid, res in resultados:
        print(_linea_go(cid, res))
    return 1 if any(res.estado == "error" for _cid, res in resultados) else 0


def main(argv=None, *, mercado=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cambio-id", dest="cambios", type=int, action="append", default=None)
    ap.add_argument("--fecha", default=None)
    ap.add_argument("--platform", default=None, choices=("amazon_mx", "amazon_us", "meli"))
    ap.add_argument("--corrida", type=int, default=None)
    ap.add_argument("--acepto-mutacion-real", action="store_true")
    ap.add_argument("--huella", default=None)
    ap.add_argument("--go", default=None)
    args = ap.parse_args(argv)
    seleccion, platform = _seleccion(args)

    try:
        conn = connect(_dsn_decide(), autocommit=True)
    except OrbitDbError as exc:
        raise Abortar(str(exc)) from None
    try:
        if platform is None:
            if isinstance(seleccion, cambios_mod.SeleccionPorCorrida):
                platform = _platform_de_corrida(conn, seleccion.corrida_id)
            else:
                platform = _platform_de_ids(conn, seleccion.ids)
        if mercado is None:
            from app.cli import _clientes_precio

            try:
                lector, escritor, fees, limitador = _clientes_precio(platform)
            except Exception as exc:  # noqa: BLE001 - sin clientes no hay go real
                raise Abortar(f"sin clientes para {platform}: {exc}") from None
            # MeLi exige el cotizador (`fees`) ademas de lector y escritor.
            with mercados.abrir(
                platform, lector=lector, escritor=escritor, fees=fees, limitador=limitador
            ) as abierto:
                return _corre(conn, abierto, platform, seleccion, args)
        return _corre(conn, mercado, platform, seleccion, args)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        print(f"precio_reversa: {exc}", file=sys.stderr)
        sys.exit(2)
