#!/usr/bin/env bash
# Ejecutar dentro del entorno desechable de revision; nunca con un DSN real.
set -euo pipefail
REVIEW_REPO=${1:?Uso: bash run-acceptance.sh /ruta/checkout}
REVIEW_EVIDENCE=$(cd -- "$(dirname -- "$0")" && pwd)
: "${ORBIT_TEST_DSN:?Falta DSN del PostgreSQL 16 desechable}"
export ORBIT_REVIEW_REPO="$REVIEW_REPO"
export ORBIT_REVIEW_ROOT="$REVIEW_REPO"
export ORBIT_REVIEW_DSN="$ORBIT_TEST_DSN"
export ORBIT_REVIEW_PG_DSN="$ORBIT_TEST_DSN"
export ORBIT_OPTIMIZER_TEST_DSN="$ORBIT_TEST_DSN"
export ORBIT_UI_DSN="$ORBIT_TEST_DSN"
export PYTHONPATH="$REVIEW_REPO:$REVIEW_REPO/tests:$REVIEW_EVIDENCE"
export PYTHONDONTWRITEBYTECODE=1
cd "$REVIEW_REPO"
git rev-parse HEAD
python --version
python -m pytest -q -s -p no:cacheprovider \
  "$REVIEW_EVIDENCE/test_accept_writes.py" \
  "$REVIEW_EVIDENCE/test_accept_ingest.py" \
  "$REVIEW_EVIDENCE/test_accept_optimizer.py" \
  "$REVIEW_EVIDENCE/test_accept_pricing.py" \
  "$REVIEW_EVIDENCE/test_accept_ui.py" \
  --junitxml="${ORBIT_ACCEPTANCE_XML:-$REVIEW_EVIDENCE/acceptance.xml}"
