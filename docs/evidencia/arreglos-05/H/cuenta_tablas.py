"""Cuenta tablas BASE del esquema canonico con y sin 0099 y sin 0097+0099.

Solo laboratorio: crea bases temporales en ORBIT_TEST_DSN y las borra.
"""

from __future__ import annotations

import os
import socket
import uuid
from pathlib import Path

import psycopg
from psycopg import sql

RAIZ = Path(__file__).resolve().parents[4]
DESTINO = Path(__file__).resolve().parent / "conteo-tablas.txt"


def _migraciones() -> list[Path]:
    return [
        p
        for p in sorted((RAIZ / "migrations").glob("*.sql"))
        if "0011_" not in p.name and "_reversa_" not in p.name
    ]


def _cuenta(dsn: str, rutas: list[Path]) -> tuple[int, int]:
    db = f"orbit_h_conteo_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))
    try:
        with psycopg.connect(
            dsn.replace("/postgres", f"/{db}", 1), autocommit=True
        ) as conn:
            for ruta in rutas:
                conn.execute(ruta.read_text(encoding="utf-8"))
            tablas = conn.execute(
                "select count(*) from information_schema.tables"
                " where table_schema = 'public' and table_type = 'BASE TABLE'"
            ).fetchone()[0]
            vistas = conn.execute(
                "select count(*) from information_schema.tables"
                " where table_schema = 'public' and table_type = 'VIEW'"
            ).fetchone()[0]
            return tablas, vistas
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))


def main() -> int:
    dsn = os.environ["ORBIT_TEST_DSN"]
    todas = _migraciones()
    sin_0099 = [p for p in todas if not p.name.startswith("0099_")]
    sin_0097_0099 = [
        p for p in sin_0099 if not p.name.startswith("0097_")
    ]
    tablas_todas, vistas_todas = _cuenta(dsn, todas)
    tablas_sin_0099, _ = _cuenta(dsn, sin_0099)
    tablas_sin_0097, _ = _cuenta(dsn, sin_0097_0099)
    lineas = [
        f"host={socket.gethostname()} dsn_laboratorio=si",
        f"migraciones canonicas: {len(todas)} (ultima {todas[-1].name})",
        f"tablas BASE con TODAS las migraciones: {tablas_todas}",
        f"vistas con TODAS las migraciones: {vistas_todas}",
        f"tablas BASE SIN 0099: {tablas_sin_0099}",
        f"tablas BASE SIN 0097 ni 0099: {tablas_sin_0097}",
        "0099 no trae CREATE TABLE: "
        + ("si" if "CREATE TABLE" not in (RAIZ / "migrations/0099_arreglos05_tope_ciclo.sql").read_text() else "NO"),
        "0097 crea keyword_reposicion_intento: "
        + ("si" if "keyword_reposicion_intento" in (RAIZ / "migrations/0097_arreglos05_reposicion.sql").read_text() else "NO"),
    ]
    DESTINO.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
