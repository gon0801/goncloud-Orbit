#!/usr/bin/env bash
# Comprueba F.3 en local (REPRICING 02): una oferta FBM de MX y una de US
# producen escenario disponible con origen y fees partidos.
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/F.3/comprobar.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
export ORBIT_TEST_DSN=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
export PYTHONPATH="$REPO"
uv run --frozen python docs/evidencia/repricing-02/ejecucion/F.3/comprobar_f3.py
