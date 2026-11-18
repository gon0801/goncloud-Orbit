#!/usr/bin/env bash
# Lanzador de evidencia de laboratorio; no instala servicios ni usa secretos reales.
set -euo pipefail

uso() {
  echo 'Uso: comprobar.sh T <writes|ingest|optimizer|pricing|ui> <filtro> [args pytest]'
  echo '     comprobar.sh N <a..k> [args pytest]'
  echo '     comprobar.sh ALL'
  echo 'Requiere Python3.12 con dependencias y ORBIT_TEST_DSN de PG16 desechable.'
}

if [[ ${1:-} == --help ]]; then
  uso
  exit 0
fi

evidencia_dir=$(cd -- "$(dirname -- "$0")/revision" && pwd)
checkout_dir=$(cd -- "$evidencia_dir/../../../.." && pwd)
python_revision=${ORBIT_ACCEPT_PYTHON:-python}
: "${ORBIT_TEST_DSN:?Falta DSN del PG16 desechable; ver ACEPTACION.md}"

export ORBIT_REVIEW_REPO="$checkout_dir" ORBIT_REVIEW_ROOT="$checkout_dir"
export ORBIT_REVIEW_DSN="$ORBIT_TEST_DSN" ORBIT_REVIEW_PG_DSN="$ORBIT_TEST_DSN"
export ORBIT_OPTIMIZER_TEST_DSN="$ORBIT_TEST_DSN" ORBIT_UI_DSN="$ORBIT_TEST_DSN"
export PYTHONPATH="$checkout_dir:$checkout_dir/tests:$evidencia_dir"
export PYTHONDONTWRITEBYTECODE=1 CI=1
cd "$checkout_dir"

"$python_revision" -B - <<'PY'
import os
import sys
from psycopg.conninfo import conninfo_to_dict

if sys.version_info[:2] != (3, 12):
    raise SystemExit("Se requiere Python 3.12")
cfg = conninfo_to_dict(os.environ["ORBIT_TEST_DSN"])
if (cfg.get("host"), cfg.get("port"), cfg.get("dbname")) not in {
    ("127.0.0.1", "55449", "postgres"),
    ("orbit-review-current-pg", "5432", "postgres"),
}:
    raise SystemExit("DSN fuera del laboratorio autorizado; no se abrio conexion")
PY

modo=${1:-}
case "$modo" in
  T)
    [[ $# -ge 3 ]] || { uso; exit 2; }
    grupo=$2
    filtro=$3
    case "$grupo" in writes|ingest|optimizer|pricing|ui) ;; *) uso; exit 2;; esac
    shift 3
    exec "$python_revision" -B -m pytest -q -rs -p no:cacheprovider \
      "$evidencia_dir/test_accept_${grupo}.py" -k "$filtro" "$@"
    ;;
  N)
    [[ $# -ge 2 && $2 =~ ^[a-k]$ ]] || { uso; exit 2; }
    bloque=$2
    shift 2
    exec "$python_revision" -B -m pytest -q -rs -p no:cacheprovider \
      tests -k "arreglos05_${bloque}_" "$@"
    ;;
  ALL)
    [[ $# == 1 ]] || { uso; exit 2; }
    : "${ORBIT_ACCEPTANCE_XML:?Definir destino nuevo; no sobrescribir revision/acceptance.xml}"
    exec "$python_revision" -B -m pytest -q -rs -s -p no:cacheprovider \
      "$evidencia_dir/test_accept_writes.py" \
      "$evidencia_dir/test_accept_ingest.py" \
      "$evidencia_dir/test_accept_optimizer.py" \
      "$evidencia_dir/test_accept_pricing.py" \
      "$evidencia_dir/test_accept_ui.py" --junitxml="$ORBIT_ACCEPTANCE_XML"
    ;;
  *) uso; exit 2 ;;
esac
