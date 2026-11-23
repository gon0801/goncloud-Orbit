#!/usr/bin/env bash
# Corre, en serie sobre el PG desechable, los comandos de Acepta de las filas
# BASE.0 y BASE.3 y los controles TEMPORADA B/E/F, y registra la salida de cada uno.
# Uso desde la raiz: bash docs/evidencia/arreglos-05/BASE/acepta.sh > acepta.txt
# Sale 0 solo si todos los comandos marcados ACEPTA salen 0 sin omisiones. Los
# marcados CONTROL se registran y no cuentan: hoy deben salir distinto de 0 y su
# bloque los atiende.
# El shell de algunas sesiones trae MSYS_NO_PATHCONV=1, que rompe comprobar.sh.
set -uo pipefail
cd "$(dirname -- "$0")/../../../.." || exit 2
unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL

# Guarda antes de abrir cualquier conexion: solo el PG desechable del laboratorio.
# Los comandos de pytest directo no pasan por la guarda de comprobar.sh, y la suite
# solo entiende el DSN en forma de URL (tests/helpers/pg.py lo parte con urlsplit).
uv run --frozen --python 3.12 python - <<'PY' || exit 2
import os
from urllib.parse import urlsplit

url = urlsplit(os.environ.get("ORBIT_TEST_DSN", ""))
autorizados = {("127.0.0.1", 55449), ("orbit-review-current-pg", 5432)}
if url.scheme != "postgresql" or url.path != "/postgres" or (url.hostname, url.port) not in autorizados:
    raise SystemExit(
        "ORBIT_TEST_DSN no es el del laboratorio (postgresql://...@127.0.0.1:55449/postgres);"
        " no se abrio conexion"
    )
PY

# Varias sesiones comparten el temporal del sistema y pytest se cae al cerrar si
# otra tiene tomado su pytest-of-<usuario>/pytest-current. Temporal propio.
TMPDIR=$(cygpath -m "$(mktemp -d)")
trap 'rm -rf "$TMPDIR"' EXIT
export TMPDIR
comprobar=docs/evidencia/arreglos-05/comprobar.sh
fallos=0

# Un skip, un error de fixture o cero casos no son aceptacion aunque pytest salga 0.
# Solo mira la linea de resumen de pytest; una salida sin ella pasa.
resumen_limpio() {
  local linea
  linea=$(printf '%s\n' "$1" | grep -E ' in [0-9.]+s( \([0-9:]+\))?( =+)?$' | tail -n 1)
  [[ -z $linea ]] && return 0
  [[ $linea =~ [0-9]+\ passed && ! $linea =~ (skipped|error|failed|no\ tests\ ran) ]]
}

corre() {
  local tipo=$1 nombre=$2
  shift 2
  echo
  echo "### [$tipo] $nombre"
  echo "\$ $*"
  local salida codigo veredicto
  salida=$("$@" 2>&1)
  codigo=$?
  printf '%s\n' "$salida"
  if [[ $codigo != 0 ]]; then
    veredicto="salida=$codigo"
  elif ! resumen_limpio "$salida"; then
    veredicto="FALLO: omision o error en el resumen de pytest"
  else
    veredicto=ok
  fi
  echo "resultado=$veredicto"
  [[ $tipo == CONTROL || $veredicto == ok ]] || fallos=$((fallos + 1))
}

echo "# Acepta de ARREGLOS 05 BASE. SHA $(git rev-parse HEAD)"
echo "# PG: $(uv run --frozen --python 3.12 python -c 'import os,psycopg; c=psycopg.connect(os.environ["ORBIT_TEST_DSN"]); print(c.execute("select version()").fetchone()[0].split(",")[0], "|", c.execute("show server_version_num").fetchone()[0])' 2>&1)"

corre ACEPTA "BASE.0 plan y diseno en origin/master" bash -c \
  'git merge-base --is-ancestor origin/master HEAD && (for f in plans/arreglos-05.md docs/evidencia/arreglos-05/{DISENO.md,COBERTURA.md,ACEPTACION.md,comprobar.sh}; do git cat-file -e "origin/master:$f" || exit 1; done) && echo "origin/master=$(git rev-parse origin/master) HEAD=$(git rev-parse HEAD)"'
corre ACEPTA "BASE.3 control 01" uv run --frozen --python 3.12 bash "$comprobar" T writes test_01
corre ACEPTA "BASE.3 controles 08 y D2" uv run --frozen --python 3.12 bash "$comprobar" T pricing 'test_08 or test_D2'
corre ACEPTA "BASE.3 control 09" uv run --frozen --python 3.12 bash "$comprobar" T ingest test_09
corre ACEPTA "BASE.3 banda manual 01X" env CI=1 PYTHONPATH=.:tests uv run --frozen --python 3.12 python -m pytest -q -p no:cacheprovider tests/test_config_write.py -k arreglos01_x
corre ACEPTA "BASE.3 controles TEMPORADA B.2, E y F" env PYTHONPATH=.:tests uv run --frozen --python 3.12 python -m pytest -q -p no:cacheprovider tests -k 'temporada01_b_ or temporada01_e_ or temporada01_f_'
corre CONTROL "Ciclo 4 legado: conciliacion antes de decidir" env PYTHONPATH=.:tests uv run --frozen --python 3.12 python -m pytest -q -rs -p no:cacheprovider docs/evidencia/arreglos-2026-11-12/pruebas/ciclo/test_revision_ciclo.py -k reconciliacion_corre_despues_de_decidir

echo
echo "# comandos Acepta que no pasaron: $fallos"
[[ $fallos == 0 ]]
