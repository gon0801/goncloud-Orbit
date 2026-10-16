#!/usr/bin/env bash
# Reproduce la evidencia M.2. Requiere: Postgres del loop en 5432.
set -euo pipefail
cd "$(dirname "$0")/../../../../.."
export ORBIT_TEST_DSN="${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}"
export PYTHONPATH=.
.venv/bin/ruff check app/ledger.py app/meli/ledger.py app/precio/envio.py \
  app/envio_muestras.py tests/test_precio02_meli_ledger.py
.venv/bin/ruff format --check app/ledger.py app/meli/ledger.py app/precio/envio.py \
  app/envio_muestras.py tests/test_precio02_meli_ledger.py
.venv/bin/python -m pytest tests/test_precio02_meli_ledger.py tests/test_ledger_sync.py \
  tests/test_precio_envio.py tests/test_envio_muestras.py \
  tests/test_ledger_atribucion.py tests/test_precio02_atribucion.py -q
