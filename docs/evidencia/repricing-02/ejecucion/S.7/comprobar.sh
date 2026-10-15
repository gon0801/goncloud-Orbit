#!/usr/bin/env bash
# Comprueba S.7 (REPRICING 02): senal por unidad, trafico, cohorte y lineal.
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/S.7/comprobar.sh
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
export PYTHONPATH="$REPO"
uv run --frozen python docs/evidencia/repricing-02/ejecucion/S.7/comprobar_s7.py
