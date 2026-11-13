"""Comando `productos-modo` (AUTO-03 C.6, C.7): interruptores y limites de la
pausa por producto de una plataforma.

Despacha a `config_write.guarda_config`, el unico escritor de config_version,
con ORBIT_DSN_ADMIN como POST /settings. Lo usan el operador y
tools/despliegue/desplegar.sh, que converge tools/despliegue/interruptores.env
dentro del contenedor. Si la config vigente ya trae lo pedido no escribe y
sale 0. `--pausa` va solo y pasa por `productos_modo.cambia_pausa` (AUTO-03
I.7), como el boton de la pantalla: `live` exige los dos limites guardados y
descarta las practicas abiertas de product ads de la plataforma; imprime el
conteo antes y despues. Exit codes: 0 escrito o sin cambios, 1 fallo, 2 uso
o edicion invalida (incluida la pausa en live sin limites guardados).
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from decimal import Decimal, InvalidOperation

from app import apply_cola, config_write, productos_modo
from app.db import connect
from app.optimizer import productos
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import scrub


def registra(sub) -> None:
    """Agrega el subparser `productos-modo` al CLI principal."""
    p = sub.add_parser(
        "productos-modo",
        help=(
            "interruptores de meta y pausa por producto, limites de la pausa y"
            " selecciones ORBIT 07 (ORBIT_DSN_ADMIN)"
        ),
    )
    p.add_argument("--platform", required=True, choices=productos.PLATAFORMAS)
    p.add_argument("--meta", choices=("off", "shadow", "live"), default=None)
    p.add_argument("--pausa", choices=("off", "shadow", "live"), default=None)
    p.add_argument("--clics", type=int, default=None, help="clics minimos de la pausa")
    p.add_argument(
        "--gasto", default=None, help="gasto minimo de la pausa en la moneda de la plataforma"
    )
    p.add_argument(
        "--economia",
        choices=("auto03", "producto_directo_v1", "off"),
        default=None,
        help="seleccion ORBIT 07 M2 (off = ausente)",
    )
    p.add_argument(
        "--bid-economia",
        choices=("economia_cambio_v1", "economia_cambio_v2", "off"),
        default=None,
        help="seleccion ORBIT 07 M5/M6 (off = ausente)",
    )
    p.add_argument(
        "--proteccion",
        choices=("proteccion_venta_v1", "off"),
        default=None,
        help="seleccion ORBIT 07 M1, global (off = ausente)",
    )


def corre(args) -> int:
    if args.pausa is not None:
        if (
            args.meta is not None
            or args.clics is not None
            or args.gasto is not None
            or args.economia is not None
            or args.bid_economia is not None
            or args.proteccion is not None
        ):
            print("--pausa va solo: las demas claves en otra llamada", file=sys.stderr)
            return 2
        return _corre_pausa(args.platform, args.pausa)
    modo = {t: v for t, v in (("meta", args.meta), ("pausa", args.pausa)) if v is not None}
    if (args.clics is None) != (args.gasto is None):
        print("--clics y --gasto van juntos", file=sys.stderr)
        return 2
    limites = None
    if args.clics is not None:
        try:
            gasto = Decimal(args.gasto.strip())
        except InvalidOperation:
            print(f"--gasto no es un decimal: {args.gasto!r}", file=sys.stderr)
            return 2
        moneda = PLATAFORMAS_MONEDA[args.platform]
        limites = productos.LimitesPausa(args.clics, productos.Dinero(gasto, moneda))
    if (
        not modo
        and limites is None
        and args.economia is None
        and args.bid_economia is None
        and args.proteccion is None
    ):
        print(
            "productos-modo necesita --meta, --pausa, --clics/--gasto, --economia,"
            " --bid-economia o --proteccion",
            file=sys.stderr,
        )
        return 2
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        print("ORBIT_DSN_ADMIN no esta definido (fail-closed)", file=sys.stderr)
        return 2
    # D-H1: apagar la proteccion descarta en la MISMA transaccion las pausas
    # que esperaban por venta reciente (antes de que un rollback quede con el
    # codigo viejo, que las aplicaria de golpe).
    apaga_proteccion = args.proteccion == "off"
    descartadas_proteccion = 0
    try:
        conn = connect(dsn)
        try:
            base = config_write.config_vigente_id(conn)
            if base is None:
                print("no hay config_version vigente", file=sys.stderr)
                return 1
            resultado = config_write.guarda_config(
                conn,
                platform=args.platform,
                base_config_version_id=base,
                ahora=dt.datetime.now(dt.UTC),
                actor="productos-modo",
                confirma=not apaga_proteccion,
                producto_modo=modo or None,
                limites_pausa=limites,
                proteccion_venta=args.proteccion,
                economia_producto=args.economia,
                economia_cambio=args.bid_economia,
            )
            if apaga_proteccion:
                descartadas_proteccion = sum(
                    apply_cola.descarta_esperas_proteccion(conn, p) for p in PLATAFORMAS_MONEDA
                )
                conn.commit()
        finally:
            conn.close()
    except config_write.EdicionVacia:
        print(f"{args.platform}: sin cambios, la config vigente ya lo trae")
        return 0
    except config_write.SettingsInvalido as exc:
        print(f"edicion invalida (exit 2): {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"productos-modo fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    print(f"config_version {resultado['config_version_id']}: {resultado['label']}")
    if apaga_proteccion:
        print(
            f"{descartadas_proteccion} pausas en espera por venta reciente"
            " descartadas (proteccion_apagada)"
        )
    return 0


def _corre_pausa(platform: str, modo: str) -> int:
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        print("ORBIT_DSN_ADMIN no esta definido (fail-closed)", file=sys.stderr)
        return 2
    try:
        conn = connect(dsn)
        try:
            base = config_write.config_vigente_id(conn)
            if base is None:
                print("no hay config_version vigente", file=sys.stderr)
                return 1
            r = productos_modo.cambia_pausa(conn, platform, modo, base, dt.datetime.now(dt.UTC))
        finally:
            conn.close()
    except config_write.SettingsInvalido as exc:
        print(f"edicion invalida (exit 2): {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"productos-modo fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    if r["config_escrita"]:
        print(f"config_version {r['config_version_id']}: pausa {r['modo_anterior']} -> {modo}")
    else:
        print(f"{platform}: sin cambios de config, la pausa ya estaba en {modo}")
    print(
        f"{platform}: practicas abiertas de product ads antes {r['practicas_antes']},"
        f" descartadas {r['practicas_descartadas']}, despues {r['practicas_despues']}"
    )
    return 0
