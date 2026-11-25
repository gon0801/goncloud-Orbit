#!/usr/bin/env bash
# Corre, en serie sobre el PG desechable, los comandos de Acepta de las filas
# F.1 y F.2 de ARREGLOS 05 y los controles que el bloque debe conservar, y
# registra la salida de cada uno.
# Uso desde la raiz (el argumento opcional elige una de tres partes; sin
# argumento corre todo):
#   bash docs/evidencia/arreglos-05/F/acepta.sh            > .../F/acepta.txt
#   bash docs/evidencia/arreglos-05/F/acepta.sh 1          > .../F/acepta-1.txt
# Sale 0 solo si todos los comandos ACEPTA salen 0 con casos ejecutados, sin
# omisiones ni errores. Los CONTROL tambien cuentan: el bloque F debe dejarlos
# en verde. La unica omision admitida es la de arquitectura (regla 99:2).
# Tres partes porque en esta PC cada corrida debe quedar por debajo de ocho
# minutos. `tests/test_notifica.py` no entra: sus 3 fallos de avisos de
# campana son previos de master en esta PC (F/resultado.md).
#
# Despues de capturar cada parte, sanear la salida (quita la ruta local del aviso
# de FastAPI) con:
#   uv run --frozen --python 3.12 python docs/evidencia/arreglos-05/F/sanea_evidencia.py
#
# Prerrequisitos de esta PC:
# - ORBIT_TEST_DSN apunta al PG16 desechable del laboratorio.
# - UV_PROJECT_ENVIRONMENT apunta al venv del checkout principal: el .venv del
#   worktree queda bloqueado por la politica de Application Control.
# - PATH incluye $HOME/.local/share/orbit-lab/bin (psql, pg_dump y flock).
set -uo pipefail
cd "$(dirname -- "$0")/../../../.." || exit 2
unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL
export PATH="$HOME/.local/share/orbit-lab/bin:$PATH"
export PYTHONUTF8=1
# UV_PROJECT_ENVIRONMENT solo hace falta si el .venv del worktree esta bloqueado
# por la politica de Application Control; en esta PC `uv run --frozen` usa el
# .venv del checkout.

parte=${1:-0}
[[ $parte =~ ^[0-3]$ ]] || { echo "parte invalida: $parte (0, 1, 2 o 3)" >&2; exit 2; }

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
export TMPDIR TEMP="$TMPDIR" TMP="$TMPDIR"
comprobar=docs/evidencia/arreglos-05/comprobar.sh
export PYTHONPATH="$PWD:$PWD/tests"

fallos=0

# Regla N: resumen de pytest con al menos N passed y sin skipped, error ni
# "no tests ran". Regla "N:M": lo mismo admitiendo exactamente M skipped.
# Regla "-": sin resumen.
pytest_paso() {
  local linea
  [[ $2 != - ]] || return 0
  linea=$(printf '%s\n' "$1" | grep -aE ' in [0-9.]+s( \([0-9:]+\))?( =+)?$' | tail -n 1)
  [[ -n $linea ]] || { echo "sin resumen de pytest"; return 1; }
  [[ $linea =~ ([0-9]+)\ passed ]] || { echo "resumen sin passed: $linea"; return 1; }
  local pasaron=${BASH_REMATCH[1]}
  ((pasaron >= ${2%%:*})) || { echo "pasaron $pasaron y se esperaban al menos ${2%%:*}: $linea"; return 1; }
  if [[ $2 == *:* ]]; then
    local omitidos=${2##*:}
    if [[ $linea =~ ([0-9]+)\ skipped ]]; then
      [[ ${BASH_REMATCH[1]} == "$omitidos" ]] || { echo "se omitieron ${BASH_REMATCH[1]} y solo se admiten $omitidos: $linea"; return 1; }
    else
      [[ $omitidos == 0 ]] || { echo "se esperaban $omitidos omitidas y el resumen no las trae: $linea"; return 1; }
    fi
  fi
  [[ $linea =~ (error|failed|no\ tests\ ran) ]] && { echo "resumen con fallo o error: $linea"; return 1; }
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
  if [[ $codigo != 0 ]]; then
    veredicto="salida=$codigo"
  else
    motivo=$(pytest_paso "$salida" "$regla") || veredicto="FALLO: $motivo"
    [[ -n $veredicto ]] || veredicto=ok
  fi
  echo "resultado=$veredicto"
  [[ -z $veredicto || $veredicto == ok ]] || fallos=$((fallos + 1))
}

en_parte() { [[ $parte == 0 || $parte == "$1" ]]; }

echo "# Acepta de ARREGLOS 05 F. SHA $(git rev-parse HEAD)$( ((parte)) && echo " (parte $parte de 3)")"
echo "# base del bloque: 8a094228cd9704b35195000b83e37157527f887c"
echo "# arbol con seguimiento limpio (sin contar los archivos de salida de esta carpeta): $([[ -z $(git status --porcelain --untracked-files=no -- . ':(exclude)docs/evidencia/arreglos-05/F/acepta.txt' ':(exclude)docs/evidencia/arreglos-05/F/acepta-?.txt') ]] && echo si || echo NO)"
echo "# variables PG* de destino o de servicio quitadas del entorno: $pg_quitadas"
echo "# herramientas en el PATH para recolectar con CI=1: psql=$(command -v psql >/dev/null && echo si || echo no) pg_dump=$(command -v pg_dump >/dev/null && echo si || echo no) flock=$(command -v flock >/dev/null && echo si || echo no)"
echo "# PG: $(uv run --frozen --python 3.12 python -c 'import os,psycopg; c=psycopg.connect(os.environ["ORBIT_TEST_DSN"]); print(c.execute("select version()").fetchone()[0].split(",")[0], "|", c.execute("show server_version_num").fetchone()[0])' 2>&1)"

# --- Parte 1: lo que pide la columna Acepta y el modelo ---
en_parte 1 && corre ACEPTA "F.1: T optimizer test_12, base 29/28.5/28 con ledger rancio" 1 \
  uv run --frozen --python 3.12 bash "$comprobar" T optimizer test_12
en_parte 1 && corre ACEPTA "F.1 y F.2: N f, regresiones del bloque (base, overlay, practica, snapshot viejo, fraccion apagada, replay, recuperacion)" 9 \
  uv run --frozen --python 3.12 bash "$comprobar" N f
en_parte 1 && corre CONTROL "bloque A: modelo de estados completo, invariantes y clasificadores (181 casillas)" 181 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_arreglos05_a_estados.py
en_parte 1 && corre CONTROL "arquitectura: presupuesto de tamano por modulo e imports" 99:2 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_architecture.py

# --- Parte 2: los duenos que F toca (ciclo, target y apply) ---
en_parte 2 && corre CONTROL "ciclo y target: notes.target, ancla, cascada y congelado por hoja" 102 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_cycle.py tests/test_cycle_target_ciclo.py tests/test_target_margen.py
en_parte 2 && corre CONTROL "aplicador: ledger, cola, cosecha, producto, esquema y API de escritura" 291 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_apply.py tests/test_apply_cola.py tests/test_apply_harvest.py \
  tests/test_apply_producto.py tests/test_apply_schema.py tests/test_api_write.py

# --- Parte 3: temporada, tablero, ciclo de apply, esquema y secretos ---
en_parte 3 && corre CONTROL "temporada: configuracion, calendario, practica, alta, baja y presupuesto" 128 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_temporada01_a.py tests/test_temporada01_b.py tests/test_temporada01_c.py \
  tests/test_temporada01_d.py tests/test_temporada01_e.py tests/test_temporada01_f.py \
  tests/test_temporada01_g.py tests/test_temporada01_h.py tests/test_temporada01_i.py
en_parte 3 && corre CONTROL "tablero y ciclo de apply: lectores de la meta congelada" 130 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_dashboard.py tests/test_dashboard_resumen.py tests/test_cycle_apply.py \
  tests/test_cycle_pause_cooldown.py
en_parte 3 && corre CONTROL "esquema: invariantes del ledger del ciclo" 88 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_schema.py
en_parte 3 && corre CONTROL "selector del instalador: migraciones de ARREGLOS 05 (F no agrega DDL)" 4 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_despliegue_motor.py -k "arreglos05"
en_parte 3 && corre ACEPTA "revision de secretos de los commits del bloque" - \
  gitleaks detect --redact --no-banner --log-opts=8a094228cd9704b35195000b83e37157527f887c..HEAD

echo
echo "# comandos que no pasaron: $fallos"
[[ $fallos == 0 ]]
