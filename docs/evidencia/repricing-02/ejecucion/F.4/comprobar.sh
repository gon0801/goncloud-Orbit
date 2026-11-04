#!/usr/bin/env bash
# Comprueba F.4 en local (REPRICING 02): historia de una unidad FBM y sonda
# dry-run (imprime plan, no escribe) + go (mueve un centavo y revierte).
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/F.4/comprobar.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
export ORBIT_TEST_DSN=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
export PYTHONPATH="$REPO"
uv run --frozen python docs/evidencia/repricing-02/ejecucion/F.4/comprobar_f4.py
