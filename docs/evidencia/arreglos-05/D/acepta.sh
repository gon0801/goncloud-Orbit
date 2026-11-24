#!/usr/bin/env bash
# Corre, en serie sobre el PG desechable, los comandos de Acepta de las filas
# D.1 a D.3 de ARREGLOS 05 y los controles que el bloque debe conservar, y
# registra la salida de cada uno.
# Uso desde la raiz (el argumento opcional elige una de cuatro partes; sin
# argumento corre todo):
#   bash docs/evidencia/arreglos-05/D/acepta.sh            > .../D/acepta.txt
#   bash docs/evidencia/arreglos-05/D/acepta.sh 1          > .../D/acepta-1.txt
# Sale 0 solo si todos los comandos ACEPTA salen 0 con casos ejecutados, sin
# omisiones ni errores. Los CONTROL tambien cuentan: el bloque D debe dejarlos
# en verde. Cuatro partes porque en esta PC cada corrida debe quedar por debajo
# de ocho minutos.
#
# Exclusiones explicitas, con su razon en resultado.md:
# - `test_impulso_io::test_r09r5_b1a_conexion_propia_lleva_credencial` exige
#   password en el DSN del laboratorio y el PG desechable usa trust: falla
#   igual en el padre 7ef47542.
# - `test_ui_fabrica::test_flujo_js_*` revienta por limite de linea de comandos
#   de Windows (WinError 206) al lanzar node; falla igual en el padre.
# - `test_fabrica_f2_hermanas` y `test_fabrica_f2_biblioteca` son los rojos
#   previos documentados de esta PC (24 y 16) y el motor de fabrica no los toca
#   en este bloque; no entran al recibo.
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
: "${UV_PROJECT_ENVIRONMENT:?Definir el venv del checkout principal en esta PC}"

parte=${1:-0}
[[ $parte =~ ^[0-4]$ ]] || { echo "parte invalida: $parte (0, 1, 2, 3 o 4)" >&2; exit 2; }

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
# El CLI de la fabrica se ejecuta por stdin en sus pruebas de punta a punta: el
# hijo decodifica el guion con la locale, asi que el recibo fija UTF-8.
export PYTHONUTF8=1

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

echo "# Acepta de ARREGLOS 05 D. SHA $(git rev-parse HEAD)$( ((parte)) && echo " (parte $parte de 4)")"
echo "# arbol con seguimiento limpio (sin contar los archivos de salida de esta carpeta): $([[ -z $(git status --porcelain --untracked-files=no -- . ':(exclude)docs/evidencia/arreglos-05/D/acepta.txt' ':(exclude)docs/evidencia/arreglos-05/D/acepta-?.txt') ]] && echo si || echo NO)"
echo "# variables PG* de destino o de servicio quitadas del entorno: $pg_quitadas"
echo "# herramientas en el PATH para recolectar con CI=1: psql=$(command -v psql >/dev/null && echo si || echo no) pg_dump=$(command -v pg_dump >/dev/null && echo si || echo no) flock=$(command -v flock >/dev/null && echo si || echo no)"
echo "# PG: $(uv run --frozen --python 3.12 python -c 'import os,psycopg; c=psycopg.connect(os.environ["ORBIT_TEST_DSN"]); print(c.execute("select version()").fetchone()[0].split(",")[0], "|", c.execute("show server_version_num").fetchone()[0])' 2>&1)"

# --- Parte 1: lo que pide la columna Acepta y el modelo ---
en_parte 1 && corre ACEPTA "D.1 a D.3: N d, regresiones del bloque" 22 \
  uv run --frozen --python 3.12 bash "$comprobar" N d
en_parte 1 && corre ACEPTA "D.2: contrato de reposicion de C (T writes test_11)" 1 \
  uv run --frozen --python 3.12 bash "$comprobar" T writes test_11
en_parte 1 && corre ACEPTA "D.2: modelo de estados completo (sin casillas nuevas)" 181 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_arreglos05_a_estados.py
en_parte 1 && corre CONTROL "arquitectura: presupuesto de tamano e imports del cliente de escritura" 99:2 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_architecture.py

# --- Parte 2: creaciones de la fabrica y sus consumidores ---
en_parte 2 && corre CONTROL "fabrica: motor, plan y ledger pre-HTTP" 192 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_fabrica_plan.py tests/test_fabrica_campanas.py
en_parte 2 && corre CONTROL "API de la fabrica (retiro, impulso y post-lotes)" 113 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_api_fabrica.py
en_parte 2 && corre CONTROL "pausador y vigia del impulso (sin el caso de password del entorno)" 114 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_impulso_io.py \
  -k "not b1a_conexion_propia_lleva_credencial"

# --- Parte 3: archivo manual, product ads, aplicador y cosecha ---
en_parte 3 && corre CONTROL "cliente de escritura, cliente de lectura y product ads" 136 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_ads_write.py tests/test_ads_client.py tests/test_ads_archivar.py
en_parte 3 && corre CONTROL "archivo manual de inertes y reposicion" 47 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider tests/test_archiva_inertes.py
en_parte 3 && corre CONTROL "aplicador, cosecha y reversa" 168 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_apply.py tests/test_apply_harvest.py tests/test_reversa_harvest.py

# --- Parte 4: API de escritura, reposicion de C, esquema, instalador y secretos ---
en_parte 4 && corre CONTROL "API de escritura, dueno del efecto y retiro" 163 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_write.py tests/test_bloqueo_dueno_efecto.py tests/test_retiro_anuncios.py
en_parte 4 && corre CONTROL "reposicion durable del bloque C" 20 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_arreglos05_c_reposicion.py
en_parte 4 && corre CONTROL "esquema y ledger del aplicador" 117 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_schema.py tests/test_apply_schema.py
en_parte 4 && corre CONTROL "selector del instalador: migraciones de ARREGLOS 05" 3 \
  uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider \
  tests/test_despliegue_motor.py -k "arreglos05"
en_parte 4 && corre ACEPTA "revision de secretos de los commits del bloque" - \
  gitleaks detect --redact --no-banner --log-opts=7ef47542f5ab421359f049583b1bc7db95662079..HEAD

echo
echo "# comandos que no pasaron: $fallos"
[[ $fallos == 0 ]]
