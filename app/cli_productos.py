"""Comando `productos-modo` (AUTO-03 C.6, C.7): interruptores y limites de la
pausa por producto de una plataforma.

Despacha a `config_write.guarda_config`, el unico escritor de config_version,
con ORBIT_DSN_ADMIN como POST /settings. Lo usan el operador y
tools/despliegue/desplegar.sh, que converge tools/despliegue/interruptores.env
dentro del contenedor. Si la config vigente ya trae lo pedido no escribe y
sale 0. Exit codes: 0 escrito o sin cambios, 1 fallo, 2 uso o edicion
invalida (incluida la pausa en live, que todavia no se puede encender).
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from decimal import Decimal, InvalidOperation

from app import config_write
from app.db import connect
from app.optimizer import productos
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.redaction import scrub


def registra(sub) -> None:
    """Agrega el subparser `productos-modo` al CLI principal."""
    p = sub.add_parser(
        "productos-modo",
        help="interruptores de meta y pausa por producto y limites de la pausa (ORBIT_DSN_ADMIN)",
    )
    p.add_argument("--platform", required=True, choices=productos.PLATAFORMAS)
    p.add_argument("--meta", choices=("off", "shadow", "live"), default=None)
    p.add_argument("--pausa", choices=("off", "shadow"), default=None)
    p.add_argument("--clics", type=int, default=None, help="clics minimos de la pausa")
    p.add_argument(
        "--gasto", default=None, help="gasto minimo de la pausa en la moneda de la plataforma"
    )


def corre(args) -> int:
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
    if not modo and limites is None:
        print("productos-modo necesita --meta, --pausa o --clics/--gasto", file=sys.stderr)
        return 2
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
            resultado = config_write.guarda_config(
                conn,
                platform=args.platform,
                base_config_version_id=base,
                ahora=dt.datetime.now(dt.UTC),
                actor="productos-modo",
                producto_modo=modo or None,
                limites_pausa=limites,
            )
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
    return 0
