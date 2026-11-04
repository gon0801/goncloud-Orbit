#!/usr/bin/env bash
# REPRICING 01 — sombra FBA MX con ventas: DRY-RUN de precio_goal.py (no escribe: sin --acepto-mutacion-real).
# Correr despues de las 12:00 UTC (escenarios 'disponible'). Goals = margen actual + ~3 pts.
set -uo pipefail
S=. # correr desde la raiz del repo en origin/master
for par in "1256 52.00" "1260 55.50" "1265 49.50"; do
  set -- $par
  echo "== listing $1 goal $2 % shadow"
  ssh goncloud "docker exec -i orbit-app-1 python - --listing-id $1 --platform amazon_mx --goal-pct $2 --mode shadow" < tools/precio_goal.py 2>&1 || echo "(salida $?)"
done
