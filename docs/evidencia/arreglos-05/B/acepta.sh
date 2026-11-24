#!/usr/bin/env bash
# Corre, en serie sobre el PG desechable, los comandos de Acepta de las filas
# B.1 a B.3 de ARREGLOS 05 y los controles que el bloque debe conservar, y
# registra la salida de cada uno.
# Uso desde la raiz (el argumento opcional elige una de tres partes; sin
# argumento corre todo):
#   bash docs/evidencia/arreglos-05/B/acepta.sh            > .../B/acepta.txt
#   bash docs/evidencia/arreglos-05/B/acepta.sh 1          > .../B/acepta-1.txt
# Sale 0 solo si todos los comandos ACEPTA salen 0 con casos ejecutados, sin
# omisiones ni errores. Los CONTROL tambien cuentan: el bloque B debe dejarlos
# en verde. La unica omision admitida es la de arquitectura (regla 99:2): dos
# pruebas que este sistema operativo no puede ejecutar.
# Tres partes porque en esta PC cada corrida debe quedar por debajo de ocho
# minutos. Preambulo de esta PC antes de llamarlo:
#   unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL
#   export PATH="/c/Users/ehven/.local/share/orbit-lab/bin:$PATH"
# (N b recolecta tests/test_despliegue_motor.py, que con CI=1 exige psql,
# pg_dump y flock en el PATH; ver README.md).
set -uo pipefail
cd "$(dirname -- "$0")/../../../.." || exit 2
unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

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
export TMPDIR
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

echo "# Acepta de ARREGLOS 05 B. SHA $(git rev-parse HEAD)$( ((parte)) && echo " (parte $parte de 3)")"
echo "# arbol con seguimiento limpio (sin contar los archivos de salida de esta carpeta): $([[ -z $(git status --porcelain --untracked-files=no -- . ':(exclude)docs/evidencia/arreglos-05/B/acepta.txt' ':(exclude)docs/evidencia/arreglos-05/B/acepta-?.txt') ]] && echo si || echo NO)"
echo "# variables PG* de destino o de servicio quitadas del entorno: $pg_quitadas"
echo "# herramientas en el PATH para recolectar con CI=1: psql=$(command -v psql >/dev/null && echo si || echo no) pg_dump=$(command -v pg_dump >/dev/null && echo si || echo no) flock=$(command -v flock >/dev/null && echo si || echo no)"
echo "# PG: $(uv run --frozen --python 3.12 python -c 'import os,psycopg; c=psycopg.connect(os.environ["ORBIT_TEST_DSN"]); print(c.execute("select version()").fetchone()[0].split(",")[0], "|", c.execute("show server_version_num").fetchone()[0])' 2>&1)"

# --- Parte 1: lo que pide la columna Acepta, el modelo y el bloque A ---
en_parte 1 && corre ACEPTA "B.1: T writes test_03, la cosecha y su reversa no archivan keyword ajena" 1 \
  uv run --frozen --python 3.12 bash "$comprobar" T writes test_03
en_parte 1 && corre ACEPTA "B.2: T writes test_10, el negative conciliado conserva reversa ejecutable" 1 \
  uv run --frozen --python 3.12 bash "$comprobar" T writes test_10
en_parte 1 && corre ACEPTA "B.1 a B.3: N b, regresiones del bloque" 38 \
  uv run --frozen --python 3.12 bash "$comprobar" N b
en_parte 1 && corre ACEPTA "B.1: tabla de estados completa (165 casillas), invariantes y clasificadores" 170 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_arreglos05_a_estados.py
en_parte 1 && corre CONTROL "bloque A: regresiones de ciclo" 57 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_arreglos05_a_ciclo.py
en_parte 1 && corre CONTROL "arquitectura: presupuesto de tamano por modulo e imports del cliente de escritura" 99:2 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_architecture.py

# --- Parte 2: los duenos que el bloque toca ---
en_parte 2 && corre CONTROL "apply (reversa de bid, regreso del dueno) y API de escritura" 114 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_apply.py tests/test_api_write.py
en_parte 2 && corre CONTROL "cola, cosecha y reversa manual de cosecha" 147 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_apply_cola.py tests/test_apply_harvest.py tests/test_reversa_harvest.py

# --- Parte 3: esquema, reproducciones y vecinos de temporada ---
en_parte 3 && corre CONTROL "esquema del ledger, migraciones y ciclo" 195 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_apply_schema.py tests/test_schema.py tests/test_migracion_0061.py \
  tests/test_migracion_0062.py tests/test_migracion_0064.py tests/test_migracion_0065.py \
  tests/test_migracion_0066.py tests/test_migracion_0067.py tests/test_migracion_0069.py \
  tests/test_migracion_0071.py tests/test_migracion_0072.py \
  tests/test_cycle.py tests/test_cycle_apply.py
en_parte 3 && corre CONTROL "reproducciones writes 01, 02, 03 y 10" 8 \
  uv run --frozen --python 3.12 bash "$comprobar" T writes "test_01 or test_02 or test_03 or test_10"
en_parte 3 && corre CONTROL "temporada A a I (E aporta la lectura previa al PUT)" 128 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_temporada01_a.py \
  tests/test_temporada01_b.py tests/test_temporada01_c.py tests/test_temporada01_d.py \
  tests/test_temporada01_e.py tests/test_temporada01_f.py tests/test_temporada01_g.py \
  tests/test_temporada01_h.py tests/test_temporada01_i.py
en_parte 3 && corre ACEPTA "revision de secretos de los commits del bloque" - \
  gitleaks detect --redact --no-banner --log-opts=23ba2385..HEAD

echo
echo "# comandos que no pasaron: $fallos"
[[ $fallos == 0 ]]
