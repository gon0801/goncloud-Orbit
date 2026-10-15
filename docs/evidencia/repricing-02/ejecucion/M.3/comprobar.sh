#!/usr/bin/env bash
# Reproduce la evidencia M.3. Requiere: Postgres del loop en 5432.
set -euo pipefail
cd "$(dirname "$0")/../../../../.."
export ORBIT_TEST_DSN="${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}"
export PYTHONPATH=.
.venv/bin/ruff check app/meli/estimacion.py app/estimacion_universo.py \
  app/estimacion_repository.py app/estimacion_ingest.py \
  tests/test_precio02_meli_cotizador.py tests/test_precio02_escenario_meli.py \
  tests/test_migracion_0073.py tests/test_precio02_contratos.py
.venv/bin/ruff format --check app/meli/estimacion.py app/estimacion_universo.py \
  app/estimacion_repository.py app/estimacion_ingest.py \
  tests/test_precio02_meli_cotizador.py tests/test_precio02_escenario_meli.py \
  tests/test_migracion_0073.py tests/test_precio02_contratos.py
.venv/bin/python -m pytest tests/test_precio02_escenario_meli.py \
  tests/test_migracion_0073.py tests/test_precio02_meli_cotizador.py \
  tests/test_precio02_contratos.py tests/test_estimacion_amazon.py \
  tests/test_estimacion_fees.py tests/test_estimacion_insumos.py \
  tests/test_estimacion_venta.py tests/test_estimacion_proyeccion.py \
  tests/test_estimacion_venta_schema.py tests/test_estimacion_aislamiento_fabrica.py \
  tests/test_precio02_escenario_fbm.py tests/test_arq_precio_f.py -q
