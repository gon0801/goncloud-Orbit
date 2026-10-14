"""Comando `precio` (REPRICING 02, 0.b). Registry para no engordar `app/cli.py`.

Mueve `_precio` fuera de `app/cli.py` (precedente de forma:
`app/cli_bids.py`). `--platform` acepta repetido (una corrida por
plataforma, cada una con su mercado); `--reporte` sigue de una plataforma.
`_conexion_decide` y `_clientes_precio` se quedan en `app/cli.py` (los
tests los sabotean ahi).
"""

from __future__ import annotations

import datetime as dt
import os
import socket
import sys

from app import precio_mercados as mercados
from app.db import connect
from app.redaction import scrub

PLATAFORMAS = ("amazon_mx", "amazon_us", "meli")


def registra(sub) -> None:
    """Agrega el subparser `precio` al CLI principal."""
    p_precio = sub.add_parser(
        "precio",
        help="corrida diaria del motor de precios (--platform) o resumen (--reporte)",
    )
    p_precio.add_argument(
        "--platform",
        default=None,
        action="append",
        choices=PLATAFORMAS,
        help="plataforma de la corrida (repetible; el cron solo agenda amazon_mx)",
    )
    p_precio.add_argument("--reporte", action="store_true", help="resumen de solo lectura")
    p_precio.add_argument("--desde", default=None, help="YYYY-MM-DD (con --reporte)")
    p_precio.add_argument("--hasta", default=None, help="YYYY-MM-DD (con --reporte)")


def _abrir_plataforma(cli_mod, platform):
    """El mercado abierto de `platform`, 2 si no hay mercado, 1 si falla.

    `None` = la plataforma no tiene mercado (meli hasta M): exit 2.
    """
    try:
        lector, escritor, fees, limitador = cli_mod._clientes_precio(platform)
        return mercados.abrir(
            platform,
            lector=lector,
            escritor=escritor,
            fees=fees,
            limitador=limitador,
        )
    except mercados.ConfigInvalida as exc:
        print(f"precio: {exc}", file=sys.stderr)
        return None
    except Exception as exc:  # noqa: BLE001 - el CLI solo reporta, sin secretos
        print(f"precio: {scrub(str(exc)) or exc.__class__.__name__}", file=sys.stderr)
        return 1


def _ventana_reporte(args):
    """`(desde, hasta)` o exit code si el uso esta mal."""
    if not args.desde or not args.hasta:
        print("precio --reporte exige --desde y --hasta (YYYY-MM-DD)", file=sys.stderr)
        return 2
    try:
        desde = dt.date.fromisoformat(args.desde)
        hasta = dt.date.fromisoformat(args.hasta)
    except ValueError:
        print("precio --reporte: fechas invalidas (YYYY-MM-DD)", file=sys.stderr)
        return 2
    if desde > hasta:
        print(
            f"precio --reporte con ventana invertida: --desde ({desde}) posterior a"
            f" --hasta ({hasta})",
            file=sys.stderr,
        )
        return 2
    return desde, hasta


def main_precio(args) -> int:
    """Corrida diaria del motor de precios o su reporte (REPRICING 01 A.5)."""
    from app import cli as cli_mod
    from app.precio import corrida as corrida_precios

    if args.reporte:
        ventana = _ventana_reporte(args)
        if isinstance(ventana, int):
            return ventana
        desde, hasta = ventana
        dsn = os.environ.get("ORBIT_DSN_READ", "")
        if not dsn:
            print("precio --reporte exige ORBIT_DSN_READ", file=sys.stderr)
            return 2
        try:
            conn = connect(dsn, autocommit=True)
        except Exception as exc:  # noqa: BLE001 - como `_cycle`: scrubbado y != 0
            print(f"precio: {scrub(str(exc)) or exc.__class__.__name__}", file=sys.stderr)
            return 1
        plats = args.platform or []
        if len(plats) != 1:
            print("precio --reporte exige una sola --platform", file=sys.stderr)
            return 2
        try:
            for linea in corrida_precios.reporte(conn, desde=desde, hasta=hasta, platform=plats[0]):
                print(linea)
        except Exception as exc:  # noqa: BLE001 - D7: como la corrida, scrubbado y != 0
            print(f"precio: {scrub(str(exc)) or exc.__class__.__name__}", file=sys.stderr)
            return 1
        finally:
            conn.close()
        return 0
    if not args.reporte and (args.desde or args.hasta):
        print("precio: --desde/--hasta solo con --reporte", file=sys.stderr)
        return 2
    plataformas = args.platform or []
    if not plataformas:
        print("precio exige --platform (amazon_mx|amazon_us|meli)", file=sys.stderr)
        return 2
    try:
        conn, cerrar = cli_mod._conexion_decide()
    except cli_mod._FaltaDsn as exc:
        print(f"precio exige {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - K6: como `_cycle`, scrubbado y != 0
        print(f"precio: {scrub(str(exc)) or exc.__class__.__name__}", file=sys.stderr)
        return 1
    rc = 0
    try:
        for platform in plataformas:
            mercado_ctx = _abrir_plataforma(cli_mod, platform)
            if mercado_ctx is None:
                return 2
            if isinstance(mercado_ctx, int):
                rc = mercado_ctx
                continue
            with mercado_ctx as mercado:
                try:
                    from app.notifica import avisar_precio

                    resumen = corrida_precios.correr(
                        conn,
                        mercado,
                        owner=f"{socket.gethostname()}:{os.getpid()}",
                        avisar=avisar_precio,
                    )
                except corrida_precios.ConfigInvalida as exc:
                    print(f"precio: {exc}", file=sys.stderr)
                    return 2
                except corrida_precios.LockOcupado as exc:
                    print(f"precio: {exc}", file=sys.stderr)
                    continue
                except Exception as exc:  # noqa: BLE001 - el CLI solo reporta, sin secretos
                    print(f"precio: {scrub(str(exc)) or exc.__class__.__name__}", file=sys.stderr)
                    rc = 1
                    continue
            for linea in resumen.lineas:
                print(linea)
            print(f"decisiones={resumen.decisiones} escritas={resumen.escritas}")
            for error in resumen.errores:
                print(f"precio: {scrub(error)}", file=sys.stderr)
            if resumen.errores:
                rc = 1
    finally:
        cerrar()
    return rc
