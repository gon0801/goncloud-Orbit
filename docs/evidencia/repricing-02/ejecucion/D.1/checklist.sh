#!/usr/bin/env bash
# Checklist post-deploy de D.1 (REPRICING 02 corte 0): SOLO LECTURA. Corre
# UNA vez despues de desplegar.sh y deja su salida en un txt con su
# linea exit= al final. Patron: docs/evidencia/bids-02/ejecucion/D.6/checklist.sh.
# 0 = todo comprobado; 1 = alguna FALLA medida; 4 = no pudo medir (el
# servidor no contesta o una lectura salio vacia, sin ninguna FALLA
# medida). Una FALLA medida sale con 1 aunque otra lectura quede vacia.
# En simulacion (ORBIT_SIMULACION=1) la base es ORBIT_SIM_DSN y el HTTP es
# ORBIT_SIM_API: no hay ssh.
# Comprueba: columnas goal_id/l_origen/verificacion en precio_decision;
# valor meli en el enum canal; precio_modo_global en la config vigente;
# la corrida de precios sigue programada (linea precio en el crontab, sin
# cambiarla); y GET /precios en 200 (0.a reescribio esa pantalla).
# Uso: bash docs/evidencia/repricing-02/ejecucion/D.1/checklist.sh <STAMP> > checklist-<sello>.txt 2>&1; echo "exit=$?" >> checklist-<sello>.txt
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
SIM=${ORBIT_SIMULACION:-0}
DSN_SIM=${ORBIT_SIM_DSN:-postgresql://orbit:orbit@localhost:5432/orbit_sim_d1}
API_SIM=${ORBIT_SIM_API:-http://127.0.0.1:8012}
FALLAS=0
NOMEDIDO=0
cd "$REPO"

revisa() {  # revisa <nombre> <esperado> <obtenido>
  if [ -z "$3" ]; then echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1));
  elif [ "$2" = "$3" ]; then echo "OK    $1 = $3";
  else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}
lee() {
  if [ "$SIM" = 1 ]; then
    psql "$DSN_SIM" -X -q -tA -v ON_ERROR_STOP=1
  else
    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
  fi
}

echo "== Checklist D.1 (solo lectura; sello $SELLO)"

echo "== 1) Esquema: columnas del corte y enum canal"
revisa "columnas goal_id/l_origen/verificacion en precio_decision" 3 \
  "$(echo "SELECT count(*) FROM information_schema.columns WHERE table_name = 'precio_decision' AND column_name IN ('goal_id', 'l_origen', 'verificacion');" | lee || true)"
revisa "valor meli en enum canal" t \
  "$(echo "SELECT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'estimacion_canal' AND e.enumlabel = 'meli');" | lee || true)"
revisa "precio_modo_global en config vigente" shadow \
  "$(echo "SELECT settings ->> 'precio_modo_global' FROM config_version ORDER BY id DESC LIMIT 1;" | lee || true)"

echo "== 2) El crontab trae la linea precio (sin cambiarla este despliegue)"
if [ "$SIM" = 1 ]; then
  revisa "linea precio en crontab sim" 1 \
    "$(grep -c 'python -m app.cli precio' "${TMPDIR:-/tmp}/orbit-d1-sim/crontab-gon" 2>/dev/null || true)"
else
  revisa "linea precio en crontab" 1 \
    "$(ssh goncloud 'crontab -l -u gon 2>/dev/null' | grep -c 'python -m app.cli precio' || true)"
fi

echo "== 3) GET /precios en 200"
if [ "$SIM" = 1 ]; then
  revisa "GET /precios" 200 "$(curl -s -o /dev/null -w '%{http_code}' "$API_SIM/precios" || true)"
else
  revisa "GET /precios" 200 "$(ssh goncloud "curl -s -o /dev/null -w %{http_code} 'http://127.0.0.1:8010/precios'" || true)"
fi

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
