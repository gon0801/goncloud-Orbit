#!/usr/bin/env bash
# G.1: el dry-run del tool viejo (commit S.6) y el del nuevo (arbol) planean
# lo mismo sobre la misma base sembrada: mismo plan, misma huella.
#
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/G.1/comparar.sh [--romper-b]
#
# Lado A: worktree pristino en HEAD (S.6, tool fila por fila).
# Lado B: arbol de trabajo (G.1, siembra pura + aplicar todo o nada).
# --romper-b: corre B desde una COPIA con UMBRAL_SALTO 0.25 -> 0.50 y debe
# salir en rojo (si saliera verde, la comparacion no prueba nada).
# Al terminar borra las bases A y B y el worktree (trap).
set -u

ROMPER_B=0
if [[ "${1-}" == "--romper-b" ]]; then ROMPER_B=1; fi
: "${ORBIT_TEST_DSN:?exporta ORBIT_TEST_DSN (DSN base de VERIFY)}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)"
cd "$REPO"
TMP="$(mktemp -d)"
WT_A="$TMP/wt-a"
TMP_B="$TMP/lado-b"
DB_A="orbit_cmp_g1_a"
DB_B="orbit_cmp_g1_b"
DSN_A="$(echo "$ORBIT_TEST_DSN" | sed "s|/[^/]*$|/$DB_A|")"
DSN_B="$(echo "$ORBIT_TEST_DSN" | sed "s|/[^/]*$|/$DB_B|")"

limpiar() {
  psql "$ORBIT_TEST_DSN" -qc "DROP DATABASE IF EXISTS $DB_A" 2>/dev/null || true
  psql "$ORBIT_TEST_DSN" -qc "DROP DATABASE IF EXISTS $DB_B" 2>/dev/null || true
  git -C "$REPO" worktree remove --force "$WT_A" 2>/dev/null || true
  rm -rf "$TMP"
}
trap limpiar EXIT

sembrar() { # $1 = DSN destino; imprime L1=<lid> L2=<lid>
  ORBIT_SEED_DSN="$1" PYTHONPATH="$REPO" "$REPO/.venv/bin/python" - <<'EOF'
import os
import sys

import psycopg

sys.path.insert(0, "tests")
from test_precio_goals import ORDEN54, RAIZ, _cadena

conn = psycopg.connect(os.environ["ORBIT_SEED_DSN"], autocommit=True)
conn.execute("SET TIME ZONE 'UTC'")
for nombre in ORDEN54:
    conn.execute((RAIZ / "migrations" / nombre).read_text(encoding="utf-8"))
a = _cadena(conn, sku="SKU-CMP-A", ext="ASIN-CMP-A", m="24.00")
b = _cadena(conn, sku="SKU-CMP-B", ext="ASIN-CMP-B", m="41.00")
print(f"L1={a['listing']} L2={b['listing']}")
EOF
}

conteo() { # $1 = DSN, $2 = SQL escalar
  psql "$1" -qtAX -c "$2"
}

# --- lado A: HEAD pristino -------------------------------------------------
git -C "$REPO" worktree add --detach "$WT_A" HEAD >/dev/null
ln -s "$REPO/.venv" "$WT_A/.venv"
if ! grep -q "_guardas_del_plan" "$WT_A/tools/precio_goal.py"; then
  echo "ABORTA: el lado A no trae el tool viejo (_guardas_del_plan)" >&2
  exit 2
fi
if [[ -f "$WT_A/app/precio/siembra.py" ]]; then
  echo "ABORTA: el lado A trae siembra.py (no es S.6 pristino)" >&2
  exit 2
fi

# --- lado B: arbol (o copia rota) ------------------------------------------
LADO_B="$REPO"
if [[ "$ROMPER_B" == "1" ]]; then
  mkdir -p "$TMP_B"
  tar -cf - --exclude=.git --exclude=.venv -C "$REPO" . | tar -xf - -C "$TMP_B"
  ln -s "$REPO/.venv" "$TMP_B/.venv"
  sed -i.bak 's/Decimal("0.25")/Decimal("0.50")/' "$TMP_B/app/precio/siembra.py"
  rm -f "$TMP_B/app/precio/siembra.py.bak"
  if ! grep -q 'UMBRAL_SALTO = Decimal("0.50")' "$TMP_B/app/precio/siembra.py"; then
    echo "ABORTA: --romper-b no rompio la copia" >&2
    exit 2
  fi
  LADO_B="$TMP_B"
fi
if grep -q "_guardas_del_plan" "$LADO_B/tools/precio_goal.py"; then
  echo "ABORTA: el lado B trae el tool viejo (G.1 no aplicado)" >&2
  exit 2
fi

# --- bases ------------------------------------------------------------------
psql "$ORBIT_TEST_DSN" -qc "DROP DATABASE IF EXISTS $DB_A" >/dev/null
psql "$ORBIT_TEST_DSN" -qc "DROP DATABASE IF EXISTS $DB_B" >/dev/null
psql "$ORBIT_TEST_DSN" -qc "CREATE DATABASE $DB_A" >/dev/null
psql "$ORBIT_TEST_DSN" -qc "CREATE DATABASE $DB_B" >/dev/null
IDS_A="$(sembrar "$DSN_A")"
IDS_B="$(sembrar "$DSN_B")"
if [[ "$IDS_A" != "$IDS_B" ]]; then
  echo "ABORTA: las siembras difieren (A: $IDS_A, B: $IDS_B)" >&2
  exit 2
fi
eval "$IDS_A"
for lado in A B; do
  dsn_var="DSN_$lado"
  n_list="$(conteo "${!dsn_var}" 'SELECT count(*) FROM listing')"
  n_esc="$(conteo "${!dsn_var}" "SELECT count(*) FROM estimacion_escenario WHERE estado = 'disponible'")"
  if [[ "$n_list" != "2" || "$n_esc" != "2" ]]; then
    echo "ABORTA: base $lado sin las 2 cadenas ($n_list listings, $n_esc escenarios)" >&2
    exit 2
  fi
done

# --- corridas -----------------------------------------------------------------
tool() { # $1 = lado (REPO|WT_A|TMP_B), resto = args del tool; ORBIT_DSN_ADMIN afuera
  local raiz="$1"; shift
  ORBIT_DSN_ADMIN="$TOOL_DSN" PYTHONPATH="$raiz" "$raiz/.venv/bin/python" "$raiz/tools/precio_goal.py" "$@"
}

CSV2="$TMP/lote.csv"
printf 'listing_id,platform,goal_pct\n%s,amazon_mx,30.00\n%s,amazon_mx,40.00\n' "$L1" "$L2" >"$CSV2"
CSV1="$TMP/uno.csv"
printf 'listing_id,platform,goal_pct\n%s,amazon_mx,30.00\n' "$L1" >"$CSV1"

TOOL_DSN="$DSN_A"; LADO_A="$WT_A"
tool "$LADO_A" --listing-id "$L1" --platform amazon_mx --goal-pct 30.00 --mode shadow >"$TMP/a1.out" 2>"$TMP/a1.err"; RC_A1=$?
tool "$LADO_A" --csv "$CSV2" --mode shadow >"$TMP/a2.out" 2>"$TMP/a2.err"; RC_A2=$?
tool "$LADO_A" --listing-id "$L1" --platform amazon_mx --goal-pct 55.00 --mode shadow >"$TMP/a3.out" 2>"$TMP/a3.err"; RC_A3=$?
tool "$LADO_A" --csv "$CSV1" --mode shadow >"$TMP/a4.out" 2>"$TMP/a4.err"; RC_A4=$?

TOOL_DSN="$DSN_B"
tool "$LADO_B" --listing-id "$L1" --platform amazon_mx --goal-pct 30.00 --mode shadow >"$TMP/b1.out" 2>"$TMP/b1.err"; RC_B1=$?
tool "$LADO_B" --csv "$CSV2" --mode shadow >"$TMP/b2.out" 2>"$TMP/b2.err"; RC_B2=$?
tool "$LADO_B" --listing-id "$L1" --platform amazon_mx --goal-pct 55.00 --mode shadow >"$TMP/b3.out" 2>"$TMP/b3.err"; RC_B3=$?
tool "$LADO_B" --csv "$CSV1" --mode shadow >"$TMP/b4.out" 2>"$TMP/b4.err"; RC_B4=$?

# --- guardas: la comparacion prueba algo --------------------------------------
[[ "$RC_A1" == "0" && "$RC_B1" == "0" ]] || { echo "ABORTA: caso 1 no salio 0 (A=$RC_A1 B=$RC_B1)" >&2; exit 2; }
[[ "$RC_A2" == "0" && "$RC_B2" == "0" ]] || { echo "ABORTA: caso 2 no salio 0 (A=$RC_A2 B=$RC_B2)" >&2; exit 2; }
[[ "$RC_A4" == "0" && "$RC_B4" == "0" ]] || { echo "ABORTA: caso 4 no salio 0 (A=$RC_A4 B=$RC_B4)" >&2; exit 2; }
grep -q "huella: " "$TMP/a1.out" || { echo "ABORTA: A caso 1 sin huella" >&2; exit 2; }
grep -q "huella: " "$TMP/a2.out" || { echo "ABORTA: A caso 2 sin huella" >&2; exit 2; }
grep -q "\[plan\] siembra" "$TMP/a1.out" || { echo "ABORTA: A caso 1 sin plan" >&2; exit 2; }
if [[ "$ROMPER_B" == "0" ]]; then
  [[ "$RC_A3" == "2" && "$RC_B3" == "2" ]] || { echo "ABORTA: caso 3 no aborto en 2 (A=$RC_A3 B=$RC_B3)" >&2; exit 2; }
  grep -q "salto" "$TMP/a3.err" || { echo "ABORTA: A caso 3 sin salto en stderr" >&2; exit 2; }
  grep -q "huella: " "$TMP/a3.out" && { echo "ABORTA: A caso 3 imprimio huella" >&2; exit 2; } || true
fi

# --- diffs ----------------------------------------------------------------------
FALLOS=0
compara() { # $1 = titulo, $2/$3 = archivos
  if ! diff -u "$2" "$3"; then
    echo "DIFIERE: $1"
    FALLOS=$((FALLOS + 1))
  fi
}
compara "caso 1 (goal 30.00, A vs B)" "$TMP/a1.out" "$TMP/b1.out"
compara "caso 2 (CSV 30.00+40.00, A vs B)" "$TMP/a2.out" "$TMP/b2.out"
compara "caso 3 stdout (salto 55.00, A vs B)" "$TMP/a3.out" "$TMP/b3.out"
compara "caso 3 stderr (salto 55.00, A vs B)" "$TMP/a3.err" "$TMP/b3.err"
if [[ "$RC_A3" != "$RC_B3" ]]; then echo "DIFIERE: caso 3 exit (A=$RC_A3 B=$RC_B3)"; FALLOS=$((FALLOS + 1)); fi
compara "goal+CSV iguales (lado A)" "$TMP/a1.out" "$TMP/a4.out"
compara "goal+CSV iguales (lado B)" "$TMP/b1.out" "$TMP/b4.out"

if [[ "$FALLOS" == "0" ]]; then
  echo "COMPARACION VERDE: A y B planean lo mismo (lids $L1,$L2)"
  exit 0
fi
echo "COMPARACION ROJA: $FALLOS diferencia(s)"
exit 1
