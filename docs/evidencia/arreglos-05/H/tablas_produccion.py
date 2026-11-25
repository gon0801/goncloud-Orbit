"""Diferencias entre las tablas BASE del esquema canonico y las de produccion (RO).

El esquema canonico se crea en una base temporal de laboratorio (todas las
migraciones menos 0011 y reversas). Produccion se lee con lee_prod.sh (orbit_read).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import psycopg
from psycopg import sql

RAIZ = Path(__file__).resolve().parents[4]
DESTINO = Path(__file__).resolve().parent / "tablas-produccion-vs-canonico.txt"


def _canonicas() -> set[str]:
    dsn = os.environ["ORBIT_TEST_DSN"]
    db = f"orbit_h_tablas_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))
    try:
        with psycopg.connect(dsn.replace("/postgres", f"/{db}", 1), autocommit=True) as conn:
            for ruta in sorted((RAIZ / "migrations").glob("*.sql")):
                if "0011_" in ruta.name or "_reversa_" in ruta.name:
                    continue
                conn.execute(ruta.read_text(encoding="utf-8"))
            filas = conn.execute(
                "select table_name from information_schema.tables"
                " where table_schema = 'public' and table_type = 'BASE TABLE'"
            ).fetchall()
            return {f[0] for f in filas}
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db))
            )


def _produccion() -> set[str]:
    # pg_class, no information_schema: la tabla privada de 0087 no tiene
    # privilegios para orbit_read y information_schema la esconde.
    salida = subprocess.run(
        ["bash", str(RAIZ / "docs/evidencia/arreglos-01/lee_prod.sh")],
        input=(
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace"
            " where n.nspname = 'public' and c.relkind in ('r','p')"
        ),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {linea.strip() for linea in salida.splitlines() if linea.strip()}


def main() -> int:
    canonicas = _canonicas()
    produccion = _produccion()
    solo_canonico = sorted(canonicas - produccion)
    solo_prod = sorted(produccion - canonicas)
    lineas = [
        f"tablas BASE canonicas (desde cero): {len(canonicas)}",
        f"tablas BASE en produccion: {len(produccion)}",
        f"solo en el canonico ({len(solo_canonico)}): {', '.join(solo_canonico) or '-'}",
        f"solo en produccion ({len(solo_prod)}): {', '.join(solo_prod) or '-'}",
    ]
    DESTINO.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
