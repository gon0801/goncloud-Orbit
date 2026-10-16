#!/usr/bin/env bash
# Reproduce la evidencia M.4. Requiere: Postgres del loop en 5432.
set -euo pipefail
cd "$(dirname "$0")/../../../../.."
export ORBIT_TEST_DSN="${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}"
export PYTHONPATH=.
.venv/bin/ruff check app/meli/precio_mercado.py app/meli/write_client.py \
  app/precio/puerto.py app/precio/fuentes.py app/estimacion_insumos.py \
  app/spapi/precio_mercado.py app/reputacion_clientes.py app/cli.py \
  tests/test_precio02_mercado_meli.py tests/test_arq_precio_m.py
.venv/bin/ruff format --check app/meli/precio_mercado.py app/meli/write_client.py \
  app/precio/puerto.py app/precio/fuentes.py app/estimacion_insumos.py \
  app/spapi/precio_mercado.py app/reputacion_clientes.py app/cli.py \
  tests/test_precio02_mercado_meli.py tests/test_arq_precio_m.py
.venv/bin/python -m pytest tests/test_precio02_mercado_meli.py \
  tests/test_arq_precio_m.py tests/test_precio02_meli_catalogo.py \
  tests/test_precio02_meli_ledger.py tests/test_precio02_meli_cotizador.py \
  tests/test_precio02_escenario_meli.py tests/test_migracion_0073.py \
  tests/test_precio02_mercado.py tests/test_precio_corrida.py \
  tests/test_architecture.py tests/test_reputacion.py \
  tests/test_reputacion_alertas.py tests/test_reputacion_migracion.py -q
