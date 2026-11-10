#!/usr/bin/env bash
# Lee la base de producción de Orbit. Solo lectura: rol orbit_read (solo SELECT) y transacción read_only.
# Uso: echo "select 1" | bash docs/evidencia/arreglos-01/lee_prod.sh
# Imprime una fila por línea, columnas separadas por |.
set -euo pipefail
ssh goncloud "docker exec -i orbit-app-1 python -c 'import os,sys; from app import db; c=db.connect(os.environ[\"ORBIT_DSN_READ\"]); c.read_only=True; [print(*r, sep=\"|\") for r in c.execute(sys.stdin.read())]'"
