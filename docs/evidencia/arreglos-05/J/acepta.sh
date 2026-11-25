#!/usr/bin/env bash
# Acepta del bloque J de ARREGLOS 05. Corre en serie, sobre el PG desechable,
# las regresiones del bloque y los vecinos que toca el retiro.
# Uso desde la raiz:
#   bash docs/evidencia/arreglos-05/J/acepta.sh 1 > docs/evidencia/arreglos-05/J/acepta-1.txt
#   bash docs/evidencia/arreglos-05/J/acepta.sh 2 > .../acepta-2.txt
#   bash docs/evidencia/arreglos-05/J/acepta.sh 3 > .../acepta-3.txt
# Sale 0 solo si todos los comandos salen 0 con casos ejecutados.
set -uo pipefail
cd "$(dirname -- "$0")/../../../.." || exit 2
unset MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL
export PATH="$HOME/.local/share/orbit-lab/bin:$PATH"
: "${ORBIT_TEST_DSN:?Falta DSN del PG16 desechable; ver ACEPTACION.md}"

parte=${1:-0}
[[ $parte =~ ^[1-3]$ ]] || { echo "parte invalida: $parte (1, 2 o 3)" >&2; exit 2; }

TMPDIR=$(cygpath -m "$(mktemp -d)")
trap 'rm -rf "$TMPDIR"' EXIT
export TMPDIR TEMP="$TMPDIR" TMP="$TMPDIR"
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD:$PWD/tests"
export CI=1
comprobar=docs/evidencia/arreglos-05/comprobar.sh
pytest="uv run --frozen --python 3.12 python -B -m pytest -q -p no:cacheprovider"

fallos=0
corre() {
  local nombre=$1
  shift
  echo
  echo "### $nombre"
  echo "\$ $*"
  local salida codigo
  salida=$("$@" 2>&1)
  codigo=$?
  printf '%s\n' "$salida"
  if [[ $codigo != 0 ]]; then
    echo "resultado=salida=$codigo"
    fallos=$((fallos + 1))
  elif ! printf '%s\n' "$salida" | grep -qE '[0-9]+ passed'; then
    echo "resultado=sin casos ejecutados"
    fallos=$((fallos + 1))
  else
    echo "resultado=ok"
  fi
}

echo "# Acepta de ARREGLOS 05 J. SHA $(git rev-parse HEAD) (parte $parte de 3)"
echo "# arbol con seguimiento limpio: $([[ -z $(git status --porcelain --untracked-files=no -- . ':(exclude)docs/evidencia/arreglos-05/J/acepta-?.txt') ]] && echo si || echo NO)"
echo "# PG: $(uv run --frozen --python 3.12 python -c 'import os,psycopg; c=psycopg.connect(os.environ["ORBIT_TEST_DSN"]); print(c.execute("select version()").fetchone()[0].split(",")[0])' 2>&1)"

if [[ $parte == 1 ]]; then
  corre "J.1/J.2: N j, regresiones del bloque" \
    uv run --frozen --python 3.12 bash "$comprobar" N j
  corre "arquitectura: presupuesto por modulo" \
    $pytest tests/test_architecture.py
  corre "modelo de estados del bloque A" \
    $pytest tests/test_arreglos05_a_estados.py
  corre "ciclo: golden, gates, replay, politica apagada" \
    $pytest tests/test_cycle.py
fi

if [[ $parte == 2 ]]; then
  corre "politica, eras, cooldown y config" \
    $pytest tests/test_optimizer_goals.py tests/test_eras.py \
    tests/test_cycle_pause_cooldown.py tests/test_config_write.py
  corre "escritura de settings y API" \
    $pytest tests/test_settings_escritura.py tests/test_api_write.py
  corre "economia conservada (auto03 y cambio v2)" \
    $pytest tests/test_economia_observada.py tests/test_orbit07_m2.py
fi

if [[ $parte == 3 ]]; then
  corre "productos: lectura, meta live y pausa shadow" \
    $pytest tests/test_productos_io.py tests/test_productos_pausa.py \
    tests/test_productos_meta_ciclo.py tests/test_productos_meta.py \
    tests/test_pantalla_productos.py tests/test_ads_producto_ingesta.py
  corre "proteccion de venta: ciclo y cola" \
    $pytest tests/test_orbit07_m1.py
  corre "aplicador, target y bloques F/G" \
    $pytest tests/test_apply_producto.py tests/test_cycle_target_ciclo.py \
    tests/test_arreglos05_f_meta.py tests/test_arreglos05_g_sello.py
  corre "UI de productos (render puro)" \
    $pytest tests/test_ui.py -k productos \
    tests/test_ui_productos.py::test_r09r4_t01_t07_retiros_abiertos_form_ids_escape \
    tests/test_ui_productos.py::test_encender_boton_deshabilitado_sin_limites_y_volver_en_vivo
  corre "selector del instalador: migraciones de ARREGLOS 05" \
    $pytest tests/test_despliegue_motor.py -k arreglos05
fi

echo
echo "# comandos que no pasaron: $fallos"
[[ $fallos == 0 ]]
