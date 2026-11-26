"""Comando `envios-ventana`: dias para cerrar por vencimiento un envio de
Mercado Libre sin estado final.

Despacha a `config_write.guarda_config`, el unico escritor de config_version,
con ORBIT_DSN_ADMIN. Si la config vigente ya trae el valor no escribe y sale 0.
Exit codes: 0 escrito o sin cambios, 1 fallo, 2 uso o valor invalido.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

from app import config_write
from app.db import connect
from app.redaction import scrub


def registra(sub) -> None:
    """Agrega el subparser `envios-ventana` al CLI principal."""
    p = sub.add_parser(
        "envios-ventana",
        help=(
            "dias desde la compra para cerrar un envio de Mercado Libre sin"
            " estado final (ORBIT_DSN_ADMIN)"
        ),
        allow_abbrev=False,
    )
    p.add_argument("--dias", type=int, required=True, help="entero de 30 a 365")


def corre(args: argparse.Namespace) -> int:
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
                platform="meli",
                base_config_version_id=base,
                ahora=dt.datetime.now(dt.UTC),
                actor="envios-ventana",
                envios_dias_cierre=args.dias,
            )
        finally:
            conn.close()
    except config_write.EdicionVacia:
        print("envios-ventana: sin cambios, la config vigente ya lo trae")
        return 0
    except config_write.SettingsInvalido as exc:
        print(f"edicion invalida (exit 2): {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"envios-ventana fallo: {scrub(str(exc))}", file=sys.stderr)
        return 1
    print(f"config_version {resultado['config_version_id']}: {resultado['label']}")
    return 0
