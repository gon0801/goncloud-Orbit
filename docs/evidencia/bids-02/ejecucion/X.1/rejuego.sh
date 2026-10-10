#!/usr/bin/env bash
# Rejuego del ultimo mes para X.1 (BIDS 02 seccion 2), paso 2: corre
# tools/rejuega_niveles.py UNA vez por plataforma y guarda su salida en
# docs/evidencia/bids-02/ejecucion/X.1/rejuego-<plataforma>.txt. SOLO LEE
# (el rejuego es SOLO SELECT): no lleva --acepto-mutacion-real, igual que
# checklist.sh de D.1. Su unico escrito es el txt local de evidencia.
# Sale con el exit del rejuego (0 si cumple, 1 si no) y lo imprime.
# Si cumple() es falso, este script NO escribe no-encendida-<plataforma>.md:
# lo escribe el OPERADOR a mano en ejecucion/X.1/ (paso 2 del plan), con
# cual de los cuatro criterios fallo y su numero medido, y esa plataforma
# no se enciende. El aviso.md del paso 3 lo escribe claw, no este script:
# para armarlo, el script imprime a stdout los recortes y subidas por
# motivo (por_motivo), la lista vendedoras_que_recortaria y la linea cumple.
# Modo real: el literal del paso (ssh + stdin, porque rejuega_niveles.py no
# esta en la imagen). Modo simulacion (ORBIT_SIMULACION=1): python local
# (.venv) con ORBIT_DSN_READ a ORBIT_SIM_DSN y PYTHONPATH al repo.
# Uso real: bash docs/evidencia/bids-02/ejecucion/X.1/rejuego.sh amazon_mx
# Uso sim:  ORBIT_SIMULACION=1 ORBIT_SIM_DSN=<dsn> bash docs/evidencia/bids-02/ejecucion/X.1/rejuego.sh amazon_mx
set -euo pipefail

[ $# -eq 1 ] || { echo "uso: rejuego.sh <amazon_mx|amazon_us>"; exit 2; }
PLAT=$1
[ "$PLAT" = amazon_mx ] || [ "$PLAT" = amazon_us ] || { echo "uso: rejuego.sh <amazon_mx|amazon_us>"; exit 2; }
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/bids-02/ejecucion/X.1
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@127.0.0.1:5433/orbit_sim_d1}
PY=${ORBIT_SIM_PYTHON:-$REPO/.venv/bin/python}
TXT=$DIR/rejuego-$PLAT.txt
cd "$REPO"

CODIGO=0
if [ "$SIM" = 1 ]; then
  echo "== MODO SIMULACION: rejuego local contra $DSN_SIM"
  ORBIT_DSN_READ="$DSN_SIM" PYTHONPATH="$REPO" "$PY" tools/rejuega_niveles.py \
    --platform "$PLAT" --ciclos 30 > "$TXT" || CODIGO=$?
else
  ssh goncloud "docker exec -i orbit-app-1 python - --platform $PLAT --ciclos 30" \
    < tools/rejuega_niveles.py > "$TXT" || CODIGO=$?
fi
echo "rejuego $PLAT -> $TXT (exit=$CODIGO)"
echo "== para el aviso (paso 3): recortes/subidas y vendedoras"
head -n 1 "$TXT"
sed -n '/^por_motivo:/,$p' "$TXT"
exit "$CODIGO"
