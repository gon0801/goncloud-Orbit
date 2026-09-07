#!/usr/bin/env python3
"""Carga un snapshot SQLite del bridge a disponibilidad_observation (ORBIT 19 B.3).

QUE HACE: lee SOLO `amazon_fba_inventory` (FBA) y `amazon_listing_prices`
con fulfillment_channel=DEFAULT (FBM) de un snapshot LOCAL del bridge, y
escribe filas append-only en `disponibilidad_observation` de Orbit (rol de
ingesta). `amazon_inventory_cache` esta PROHIBIDO y no se lee (stale, todo
cero; candado en tests/test_disponibilidad.py). FBA y FBM no se suman.
SKU sin listing en Orbit = fila no mapeada, contada y reportada.

Idempotente por (fecha, fuente, sku, observed_at): re-correr con el mismo
observed_at no duplica filas (ON CONFLICT DO NOTHING).

EL SNAPSHOT NO LO TOMA ESTE TOOL (no hay SSH desde aqui): lo copia el lead
desde el servidor goncloud, en modo ro, con el mismo metodo que la sonda 0.3
(sqlite3 backup, docs/evidencia/orbit-19/0.3/bridge-observado.txt):

  ssh goncloud 'python3 -c "import sqlite3; \\
    sqlite3.connect(\\"file:/mnt/data/appdata/bridge/data/bridge.db?mode=ro\\", \\
    uri=True).backup(sqlite3.connect(\\"/tmp/bridge-snapshot-orbit19-b3.db\\"))"'
  scp goncloud:/tmp/bridge-snapshot-orbit19-b3.db out/bridge-snapshot-orbit19-b3.db
  ssh goncloud 'rm -f /tmp/bridge-snapshot-orbit19-b3.db'

Luego, desde el repo (venv activa):

  PYTHONPATH=. .venv/bin/python tools/disponibilidad_snapshot.py \\
    --snapshot out/bridge-snapshot-orbit19-b3.db --dsn "$ORBIT_DSN_INGEST"

USO:
  --snapshot RUTA   (obligatorio) SQLite del bridge, local y read-only
  --dsn DSN         (default: env ORBIT_DSN_INGEST; obligatorio si falta)
  --observed-at ISO (opcional) UTC explicito para re-corridas reproducibles

Salida: resumen de filas insertadas / idempotentes / skips por motivo.
Errores -> stderr y exit != 0 (jamas un exito con la base sin tocar).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import connect  # noqa: E402
from app.disponibilidad import DisponibilidadError, sync_disponibilidad  # noqa: E402
from app.redaction import install_scrub_filter, scrub  # noqa: E402

logger = logging.getLogger(__name__)
install_scrub_filter(logger)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Carga el snapshot de disponibilidad del bridge a Orbit (B.3)."
    )
    parser.add_argument("--snapshot", required=True, help="ruta local de la SQLite del bridge")
    parser.add_argument(
        "--dsn", default=os.environ.get("ORBIT_DSN_INGEST"), help="DSN de ingesta de Orbit"
    )
    parser.add_argument(
        "--observed-at",
        default=None,
        help="UTC ISO explicito (re-corrida reproducible); default: ahora UTC",
    )
    args = parser.parse_args(argv)

    if not args.dsn:
        print("falta --dsn u ORBIT_DSN_INGEST", file=sys.stderr)
        return 2
    observed_at = None
    if args.observed_at is not None:
        try:
            observed_at = dt.datetime.fromisoformat(args.observed_at)
        except ValueError as exc:
            print(scrub(f"--observed-at ilegible: {exc}"), file=sys.stderr)
            return 2
        if observed_at.tzinfo is None:
            print("--observed-at debe traer zona horaria (UTC)", file=sys.stderr)
            return 2

    try:
        conn = connect(args.dsn)
    except Exception as exc:  # OrbitDbError ya redactado
        print(f"no se pudo conectar a Orbit: {exc}", file=sys.stderr)
        return 1
    try:
        resultado = sync_disponibilidad(conn, args.snapshot, observed_at=observed_at)
        conn.commit()
    except DisponibilidadError as exc:
        print(scrub(f"fallo la ingesta de disponibilidad: {exc}"), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 (tool: cualquier fallo -> exit != 0)
        print(scrub(f"fallo la ingesta de disponibilidad: {exc}"), file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(f"run_id={resultado.run_id} ok={resultado.ok}")
    print(f"filas_insertadas={resultado.filas_insertadas}")
    print(f"filas_idempotentes={resultado.filas_idempotentes}")
    print(f"rows_skipped={resultado.rows_skipped}")
    if resultado.skip_reason:
        print(f"skips: {resultado.skip_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
