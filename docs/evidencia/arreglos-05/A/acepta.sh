#!/usr/bin/env bash
# Corre, en serie sobre el PG desechable, los comandos de Acepta de las filas
# A.1 a A.4 de ARREGLOS 05 y los controles que el bloque debe conservar, y
# registra la salida de cada uno.
# Uso desde la raiz: bash docs/evidencia/arreglos-05/A/acepta.sh > docs/evidencia/arreglos-05/A/acepta.txt
# Sale 0 solo si todos los comandos ACEPTA salen 0 con casos ejecutados, sin
# omisiones ni errores. Los CONTROL tambien cuentan: el bloque A debe dejarlos
# en verde. Preambulo de esta PC antes de llamarlo:
#   unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL
#   export PATH="/c/Users/ehven/.local/share/orbit-lab/bin:$PATH"
# (B.5 exige psql, pg_dump y flock para recolectar con CI=1; ver README.md).
set -uo pipefail
cd "$(dirname -- "$0")/../../../.." || exit 2
unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

pg_quitadas=0
for variable in PGHOST PGHOSTADDR PGPORT PGDATABASE PGSERVICE PGSERVICEFILE PGLOADBALANCEHOSTS PGOPTIONS; do
  if [[ -n ${!variable:-} ]]; then
    unset "$variable"
    pg_quitadas=$((pg_quitadas + 1))
  fi
done

# Guarda antes de abrir cualquier conexion: solo el PG desechable del laboratorio.
uv run --frozen --python 3.12 python - <<'PY' || exit 2
import os
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict

dsn = os.environ.get("ORBIT_TEST_DSN", "")
url = urlsplit(dsn)
try:
    cfg = conninfo_to_dict(dsn, use_env=False)
    destino = (cfg.get("host"), str(cfg.get("port")))
    redirige = cfg.get("hostaddr") or cfg.get("service")
except psycopg.Error:
    destino, redirige = None, True
laboratorio = {("127.0.0.1", "55449"), ("orbit-review-current-pg", "5432")}
if (url.scheme != "postgresql" or url.path != "/postgres" or url.query or url.fragment
        or destino not in laboratorio or redirige):
    raise SystemExit(
        "ORBIT_TEST_DSN no es el del laboratorio"
        " (postgresql://...@127.0.0.1:55449/postgres, sin opciones); no se abrio conexion"
    )
PY

ruta() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "${1//\\//}"; fi
}

# Temporal propio: varias sesiones comparten el del sistema y pytest se cae al cerrar.
TMPDIR=$(ruta "$(mktemp -d)")
if [[ -z $TMPDIR || ! -d $TMPDIR ]]; then
  echo "No se pudo crear el temporal propio de pytest" >&2
  exit 2
fi
trap 'rm -rf "$TMPDIR"' EXIT
export TMPDIR
comprobar=docs/evidencia/arreglos-05/comprobar.sh
export PYTHONPATH="$PWD:$PWD/tests"

fallos=0

# Regla N: resumen de pytest con al menos N passed y sin skipped, error ni
# "no tests ran". Regla "legado": resumen exactamente "4 passed, 1 failed"
# (F5 pertenece a ARREGLOS 02 y no corre desde 02 B). Regla "-": sin resumen.
pytest_paso() {
  local linea
  [[ $2 != - ]] || return 0
  linea=$(printf '%s\n' "$1" | grep -E ' in [0-9.]+s( \([0-9:]+\))?( =+)?$' | tail -n 1)
  [[ -n $linea ]] || { echo "sin resumen de pytest"; return 1; }
  if [[ $2 == legado ]]; then
    [[ $linea =~ ^(4\ passed,\ 1\ failed|1\ failed,\ 4\ passed)\ in ]] || { echo "se esperaba 4 passed, 1 failed y el resumen es: $linea"; return 1; }
    return 0
  fi
  [[ $linea =~ ([0-9]+)\ passed ]] || { echo "resumen sin passed: $linea"; return 1; }
  local pasaron=${BASH_REMATCH[1]}
  [[ $linea =~ (skipped|error|failed|no\ tests\ ran) ]] && { echo "resumen con omision o error: $linea"; return 1; }
  ((pasaron >= $2)) || { echo "pasaron $pasaron y se esperaban al menos $2: $linea"; return 1; }
  return 0
}

corre() { # <ACEPTA|CONTROL> <nombre> <regla> <comando...>
  local tipo=$1 nombre=$2 regla=$3
  shift 3
  echo
  echo "### [$tipo] $nombre"
  echo "\$ $*"
  local salida codigo veredicto="" motivo
  salida=$("$@" 2>&1)
  codigo=$?
  printf '%s\n' "$salida"
  # La regla "legado" espera "4 passed, 1 failed": pytest sale 1 y aun asi paso.
  if [[ $codigo != 0 && ! ( $regla == legado && $codigo == 1 ) ]]; then
    veredicto="salida=$codigo"
  else
    motivo=$(pytest_paso "$salida" "$regla") || veredicto="FALLO: $motivo"
    [[ -n $veredicto ]] || veredicto=ok
  fi
  echo "resultado=$veredicto"
  [[ -z $veredicto || $veredicto == ok ]] || fallos=$((fallos + 1))
}

echo "# Acepta de ARREGLOS 05 A. SHA $(git rev-parse HEAD)"
echo "# arbol con seguimiento limpio (sin contar este archivo de salida): $([[ -z $(git status --porcelain --untracked-files=no -- . ':(exclude)docs/evidencia/arreglos-05/A/acepta.txt') ]] && echo si || echo NO)"
echo "# variables PG* de destino o de servicio quitadas del entorno: $pg_quitadas"
echo "# herramientas de B.5 en el PATH: psql=$(command -v psql >/dev/null && echo si || echo no) pg_dump=$(command -v pg_dump >/dev/null && echo si || echo no) flock=$(command -v flock >/dev/null && echo si || echo no)"
echo "# PG: $(uv run --frozen --python 3.12 python -c 'import os,psycopg; c=psycopg.connect(os.environ["ORBIT_TEST_DSN"]); print(c.execute("select version()").fetchone()[0].split(",")[0], "|", c.execute("show server_version_num").fetchone()[0])' 2>&1)"

corre ACEPTA "A.1 y A.2 y A.3 y A.4: regresiones del bloque" 12 \
  uv run --frozen --python 3.12 bash "$comprobar" N a
corre ACEPTA "A.2: suite existente de ciclo, cron y locks" 150 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_cycle.py tests/test_cycle_apply.py tests/test_cli.py tests/test_metricas_listas.py
corre ACEPTA "A.2: Ciclo 4 legado, conciliacion antes de decidir (CONTROL de BASE)" 1 \
  uv run --frozen --python 3.12 python -B -m pytest -q -rs -p no:cacheprovider \
  docs/evidencia/arreglos-2026-11-12/pruebas/ciclo/test_revision_ciclo.py -k reconciliacion_corre_despues_de_decidir
corre CONTROL "A.1: reproducciones legadas F1 a F4 promovidas (F5 es de ARREGLOS 02)" legado \
  uv run --frozen --python 3.12 python -B -m pytest -q -rs -p no:cacheprovider \
  docs/evidencia/arreglos-2026-11-12/pruebas/ciclo/test_revision_ciclo.py
corre CONTROL "A.3: control 01, la reconciliacion solo lee" 4 \
  uv run --frozen --python 3.12 bash "$comprobar" T writes test_01
corre CONTROL "A.2: control del arnes de optimizer (corre_ciclo con la firma sellada)" 1 \
  uv run --frozen --python 3.12 bash "$comprobar" T optimizer test_control
corre ACEPTA "A.4: revision de secretos de los commits del bloque" - \
  gitleaks detect --redact --no-banner --log-opts=149bf454..HEAD

echo
echo "# comandos que no pasaron: $fallos"
[[ $fallos == 0 ]]
