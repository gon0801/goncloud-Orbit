"""Comando administrativo de fichas Jev (JEV ADS 01, 1.2).

Registrar y revocar fichas aprobadas: solo Postgres y solo `app_admin`
(unica credencial: `ORBIT_DSN_ADMIN`). Cero Amazon, cero TypeSafe: la
ficha es config humana revisada a mano, con hechos y fuente.

Ceremonia: `registrar` y `revocar` son dry-run por omision (validan contra
la base e imprimen que harian SIN escribir); la escritura exige `--aplicar`.
La idempotencia de `registrar` es del hash: repetir el mismo contenido
devuelve la fila existente. `revocar` inserta el evento append-only e
irreversible (una sola revocacion por ficha; una ficha inexistente o ya
revocada sale con 1, en seco igual que al aplicar).

Ejemplos:

  tools/jev_fichas.py registrar --producto-id 12 --plataforma amazon_mx \\
      --listings 34,35 --hechos ficha.json --aprobador ana \\
      --observado-at 2026-10-03T00:00:00+00:00 \\
      --revisar-antes-de 2026-11-02T00:00:00+00:00
  tools/jev_fichas.py registrar ... --aplicar
  tools/jev_fichas.py revocar --ficha-version-id <uuid> --autor ana \\
      --motivo 'el proveedor cambio el material'
  tools/jev_fichas.py revocar ... --aplicar
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg

from app.db import connect
from app.jev_catalogo import hash_ficha, registrar_ficha, revocar_ficha
from app.redaction import scrub


def _fecha(valor: str) -> datetime:
    leida = datetime.fromisoformat(valor)
    if leida.tzinfo is None:
        raise ValueError(f"la fecha {valor} debe venir con zona (UTC)")
    return leida


def _listings(valor: str) -> tuple[int, ...]:
    partes = [pedazo.strip() for pedazo in valor.split(",") if pedazo.strip()]
    if not partes:
        raise ValueError("--listings vacio")
    return tuple(int(pedazo) for pedazo in partes)


def _hechos_de_archivo(ruta: str) -> tuple[tuple[str, str], ...]:
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    if not isinstance(datos, list):
        raise ValueError("el archivo de hechos debe ser una lista de {texto, fuente}")
    hechos = []
    for item in datos:
        if not isinstance(item, dict) or set(item) != {"texto", "fuente"}:
            raise ValueError("cada hecho debe ser exactamente {texto, fuente}")
        hechos.append((str(item["texto"]), str(item["fuente"])))
    return tuple(hechos)


def _desconocidos(valor: str) -> tuple[str, ...]:
    return tuple(pedazo.strip() for pedazo in valor.split(",") if pedazo.strip())


def _validar_listings(
    conn: psycopg.Connection, producto_id: int, plataforma: str, listings: tuple[int, ...]
) -> list[int]:
    faltan: list[int] = []
    for listing_id in listings:
        existe = conn.execute(
            "SELECT 1 FROM listing WHERE id = %s AND product_id = %s AND platform = %s",
            (listing_id, producto_id, plataforma),
        ).fetchone()
        if existe is None:
            faltan.append(listing_id)
    return faltan


def _registrar(conn: psycopg.Connection, args: argparse.Namespace) -> int:
    listings = _listings(args.listings)
    hechos = _hechos_de_archivo(args.hechos)
    observado = _fecha(args.observado_at)
    vence = _fecha(args.revisar_antes_de)
    faltan = _validar_listings(conn, args.producto_id, args.plataforma, listings)
    if faltan:
        print(
            f"listings {faltan} no son del producto {args.producto_id} en {args.plataforma}",
            file=sys.stderr,
        )
        return 1
    sha = hash_ficha(
        producto_id=args.producto_id,
        plataforma=args.plataforma,
        listings=listings,
        hechos=hechos,
        desconocidos=_desconocidos(args.desconocidos),
        aprobador=args.aprobador,
        observado_at=observado,
        revisar_antes_de=vence,
    )
    if not args.aplicar:
        print(f"sha256 {sha}")
        print("seco: nada escrito; agrega --aplicar para registrar")
        return 0
    registro = registrar_ficha(
        conn,
        producto_id=args.producto_id,
        plataforma=args.plataforma,
        listings=listings,
        hechos=hechos,
        desconocidos=_desconocidos(args.desconocidos),
        aprobador=args.aprobador,
        observado_at=observado,
        revisar_antes_de=vence,
    )
    print(f"id {registro.ficha.id}")
    print(f"sha256 {registro.ficha.sha256}")
    if registro.ya_existia:
        print("ya existia: idempotente por hash, no se inserto version nueva")
    return 0


def _revocar(conn: psycopg.Connection, args: argparse.Namespace) -> int:
    existe = conn.execute(
        "SELECT 1 FROM jev_ficha_version WHERE id = %s", (args.ficha_version_id,)
    ).fetchone()
    if existe is None:
        print(f"ficha {args.ficha_version_id} no existe", file=sys.stderr)
        return 1
    fecha = _fecha(args.fecha) if args.fecha else None
    # En seco se ejecuta la MISMA insercion y se revierte: valida lo mismo que
    # --aplicar (ya revocada, autor/motivo vacios, fecha) sin escribir.
    with conn.transaction(force_rollback=not args.aplicar):
        revocar_ficha(
            conn,
            ficha_version_id=args.ficha_version_id,
            autor=args.autor,
            motivo=args.motivo,
            fecha=fecha,
        )
    if not args.aplicar:
        print(f"revocaria {args.ficha_version_id}")
        print("seco: validado contra la base y revertido; agrega --aplicar para revocar")
        return 0
    print(f"revocada {args.ficha_version_id}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jev_fichas", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    registrar = sub.add_parser(
        "registrar", help="valida en seco (default) o registra (--aplicar) una ficha"
    )
    registrar.add_argument("--producto-id", type=int, required=True)
    registrar.add_argument("--plataforma", required=True, choices=("amazon_mx", "amazon_us"))
    registrar.add_argument("--listings", required=True, help="IDs de listing separados por coma")
    registrar.add_argument("--hechos", required=True, help="ruta JSON: [{texto, fuente}, ...]")
    registrar.add_argument(
        "--desconocidos", default="", help="campos que la ficha no afirma, separados por coma"
    )
    registrar.add_argument("--aprobador", required=True)
    registrar.add_argument("--observado-at", required=True)
    registrar.add_argument("--revisar-antes-de", required=True)
    registrar.add_argument("--aplicar", action="store_true", help="sin esta bandera es dry-run")

    revocar = sub.add_parser(
        "revocar", help="valida en seco (default) o revoca (--aplicar) una ficha; es irreversible"
    )
    revocar.add_argument("--ficha-version-id", required=True)
    revocar.add_argument("--autor", required=True)
    revocar.add_argument("--motivo", required=True)
    revocar.add_argument("--fecha", default=None, help="default: now() en la base")
    revocar.add_argument("--aplicar", action="store_true", help="sin esta bandera es dry-run")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dsn = os.environ.get("ORBIT_DSN_ADMIN")
    if not dsn:
        print("falta ORBIT_DSN_ADMIN", file=sys.stderr)
        return 2
    try:
        with connect(dsn) as conn:
            if args.comando == "registrar":
                return _registrar(conn, args)
            return _revocar(conn, args)
    except (ValueError, psycopg.Error) as error:
        print(scrub(str(error)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
