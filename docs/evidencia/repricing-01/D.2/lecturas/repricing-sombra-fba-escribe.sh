#!/usr/bin/env bash
# REPRICING 01 — sombra FBA MX: ESCRIBE 3 goals shadow (sin go literal; no mueve precios ni llama a Amazon).
# Huellas verificadas por el dry-run del 2026-10-01 06:56 UTC.
set -uo pipefail
P=tools/precio_goal.py  # correr desde la raiz del repo en origin/master
date -u +%FT%T
echo "== 1256"; ssh goncloud "docker exec -i orbit-app-1 python - --listing-id 1256 --platform amazon_mx --goal-pct 52.00 --mode shadow --acepto-mutacion-real --huella 603e659968cc2575" < "$P" 2>&1 || echo "(salida $?)"
echo "== 1260"; ssh goncloud "docker exec -i orbit-app-1 python - --listing-id 1260 --platform amazon_mx --goal-pct 55.50 --mode shadow --acepto-mutacion-real --huella 2fd673f02db39230" < "$P" 2>&1 || echo "(salida $?)"
echo "== 1265"; ssh goncloud "docker exec -i orbit-app-1 python - --listing-id 1265 --platform amazon_mx --goal-pct 49.50 --mode shadow --acepto-mutacion-real --huella a3fc6d0a616953cf" < "$P" 2>&1 || echo "(salida $?)"
