#!/usr/bin/env bash
# Comprueba M.1 en local (REPRICING 02): catalogo MeLi con cliente falso.
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/M.1/comprobar.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
export ORBIT_TEST_DSN=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
export PYTHONPATH="$REPO"
exec "$REPO/.venv/bin/python" -m pytest tests/test_precio02_meli_catalogo.py -q
