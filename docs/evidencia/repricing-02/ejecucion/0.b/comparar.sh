#!/usr/bin/env bash
# Compara lo que decide el commit de 0.a con lo que decide el de 0.b sobre
# los mismos datos reales (REPRICING 02, guia 0.b "Comprueba").
#
# El volcado es el del ensayo de 0.a mas las tablas que la corrida lee:
# spapi_listing_estado_observation, spapi_price_observation,
# spapi_inventario_observation, estimacion_oferta_observation,
# estimacion_fee_observation, sku_cost, ingest_run, ledger_event.
# Corre el mismo dia UTC, antes de seis horas de la ultima estimacion
# (00:45, 06:45, 12:45 y 18:45 UTC): pasada la ventana la oferta se
# considera vieja y todo sale `no_evaluado`.
#
# Carga el volcado en dos bases locales. La base A lleva todas las de
# `master` menos la 0053 y la 0054 (de la 0001 a la 0052 y de la 0060 a la
# 0072); B lleva todas. Cada lado corre en su worktree (el commit de 0.a
# y este checkout) para no tocar el arbol de trabajo. Solo LEE produccion
# (orbit_read por ssh); jamas escribe ahi. Simula contra el 5432 del loop;
# borra sus bases y worktrees al terminar.
# Uso: cd <repo> && bash docs/evidencia/repricing-02/ejecucion/0.b/comparar.sh [--romper-b]
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
DSN_LOCAL=${ORBIT_TEST_DSN:-postgresql://orbit:orbit@localhost:5432/postgres}
DB_A=orbit_cmp_r02_a
DB_B=orbit_cmp_r02_b
TMP=$(mktemp -d)
WT_A="$TMP/wt-a"
ROMPER_B=0
[ "${1:-}" = "--romper-b" ] && ROMPER_B=1
limpiar() {
  git -C "$REPO" worktree remove --force "$WT_A" >/dev/null 2>&1 || true
  psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB_A WITH (FORCE)" >/dev/null
  psql "$DSN_LOCAL" -qc "DROP DATABASE IF EXISTS $DB_B WITH (FORCE)" >/dev/null
  rm -rf "$TMP"
}
trap limpiar EXIT

COMMIT_A=$(git -C "$REPO" log --format=%H --grep='REPRICING 02 0.a' -1)
[ -n "$COMMIT_A" ] || { echo "ABORTA: no hay commit de 0.a"; exit 1; }
echo "commit A (0.a): $COMMIT_A"
echo "lado B: arbol de trabajo ($(git -C "$REPO" rev-parse --short HEAD))"

echo "== esquema y datos de prod (orbit_read, solo lectura)"
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 pg_dump "$DSN" --schema-only' \
  > "$TMP/prod.sql"
grep -q 'CREATE TABLE public.precio_decision ' "$TMP/prod.sql" || { echo "ABORTA: dump de prod invalido"; exit 1; }
ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 pg_dump "$DSN" --data-only --exclude-table-data="*_seq" -t product -t listing -t config_version -t estimacion_politica_version -t estimacion_escenario -t "precio_*" -t spapi_listing_estado_observation -t spapi_price_observation -t spapi_inventario_observation -t estimacion_oferta_observation -t estimacion_fee_observation -t sku_cost -t ingest_run -t ledger_event' \
  > "$TMP/datos.sql"
grep -q '^COPY public.product ' "$TMP/datos.sql" || { echo "ABORTA: volcado de datos invalido"; exit 1; }

DIA=$(date -u +%F)
for DB in "$DB_A" "$DB_B"; do
  psql "$DSN_LOCAL" -qc "CREATE DATABASE $DB" >/dev/null
  psql "${DSN_LOCAL%/*}/$DB" -q -v ON_ERROR_STOP=1 -f "$TMP/prod.sql" >/dev/null
  { echo "SET session_replication_role = replica;"; cat "$TMP/datos.sql"; } | psql "${DSN_LOCAL%/*}/$DB" -X -q -v ON_ERROR_STOP=1 >/dev/null
  for tabla in product listing config_version estimacion_politica_version estimacion_escenario precio_goal precio_decision precio_cotizacion precio_envio_muestra precio_cambio estimacion_oferta_observation estimacion_fee_observation ingest_run sku_cost ledger_event; do
    psql "${DSN_LOCAL%/*}/$DB" -q -c "select setval(pg_get_serial_sequence('$tabla', 'id'), (select coalesce(max(id), 1) from $tabla));" >/dev/null
  done
done

echo "== la base A es prod tal cual (sin 0053 ni 0054); la B suma las dos"
psql "${DSN_LOCAL%/*}/$DB_B" -X -q -v ON_ERROR_STOP=1 -f "$REPO/migrations/0053_precio02_canal_meli.sql" >/dev/null
psql "${DSN_LOCAL%/*}/$DB_B" -X -q -v ON_ERROR_STOP=1 -f "$REPO/migrations/0054_precio02_base.sql" >/dev/null

echo "== borra lo que dejo la corrida de hoy en las dos bases"
for DB in "$DB_A" "$DB_B"; do
  psql "${DSN_LOCAL%/*}/$DB" -X -q -v ON_ERROR_STOP=1 -v dia="$DIA" <<'SQL' >/dev/null
SET session_replication_role = replica;
DELETE FROM precio_cambio WHERE decision_id IN
  (SELECT id FROM precio_decision WHERE decision_date = :'dia');
DELETE FROM precio_decision WHERE decision_date = :'dia';
DELETE FROM precio_cotizacion
  WHERE source_event_id LIKE 'precio-cotiz-%-' || :'dia' || '-%';
SET session_replication_role = origin;
SQL
  QUEDAN=$(psql "${DSN_LOCAL%/*}/$DB" -tAc "SELECT count(*) FROM precio_decision WHERE decision_date = '$DIA'")
  [ "$QUEDAN" = "0" ] || { echo "ABORTA: $DB aun trae $QUEDAN decisiones de hoy"; exit 1; }
done

export GOALS
GOALS=$(psql "${DSN_LOCAL%/*}/$DB_A" -tAc "SELECT count(*) FROM precio_goal g WHERE g.platform = 'amazon_mx' AND g.valid_from <= '$DIA' AND (g.valid_to IS NULL OR g.valid_to > '$DIA')")
[ "$GOALS" -gt 0 ] || { echo "ABORTA: sin goals vigentes de amazon_mx (la comparacion no probaria nada)"; exit 1; }
echo "goals vigentes amazon_mx: $GOALS"
export DIA ROMPER_B

git -C "$REPO" worktree add --detach "$WT_A" "$COMMIT_A" >/dev/null 2>&1

corre_lado() {
  wt=$1; db=$2; txt=$3
  ( cd "$wt" && PATH="/opt/homebrew/bin:/usr/bin:/bin" PYTHONPATH=.:tests \
    ORBIT_TEST_DSN="${DSN_LOCAL%/*}/$db" CMP_DB="$db" CMP_TXT="$txt" CMP_DIA="$DIA" \
    CMP_GOALS="$GOALS" CMP_ROMPER="$ROMPER_B" CMP_DSN_BASE="$DSN_LOCAL" .venv/bin/python - <<'PY'
import os
import psycopg
from test_precio_corrida import _RedFalsa

db, txt, dia, goals = os.environ["CMP_DB"], os.environ["CMP_TXT"], os.environ["CMP_DIA"], int(os.environ["CMP_GOALS"])
romper = os.environ["CMP_ROMPER"] == "1"
conn = psycopg.connect(os.environ["CMP_DSN_BASE"], dbname=db, autocommit=True)
mapa = {
    f[0]: f[1]
    for f in conn.execute(
        "SELECT source_event_id, seller_sku FROM estimacion_oferta_observation"
    ).fetchall()
}
red = _RedFalsa(skus=mapa)
if romper and db.endswith("_b"):
    import app.precio.corrida as corrida_mod

    def _rota(*a, **k):
        raise RuntimeError("lado B roto a proposito")

    corrida_mod.cotizar_y_decidir = _rota
from app.precio.corrida import correr
from app.spapi.client import SpapiClient
from app.spapi.precio_write import construir_escritor
from app.estimacion_fees import ProductFeesClient

cred = {"lwa_app_id": "x", "lwa_client_secret": "y", "refresh_token": "z"}
lector = SpapiClient(credentials=cred, transport=red.transport, sleep=lambda s: None)
escritor = construir_escritor(lector, "amazon_mx", transport=red.transport, sleep=lambda s: None)
fees = ProductFeesClient(credentials=cred, transport=red.transport, sleep=lambda s: None)
try:
    from app.spapi.precio_mercado import MercadoAmazon

    mercado = MercadoAmazon(
        "amazon_mx",
        lector=lector,
        escritor=escritor,
        fees=fees,
        construir_cuerpo=lambda **kw: kw,
    )
    res = correr(conn, mercado, owner="comparar-0b")
except ImportError:
    import time as _time

    from app.spapi.client import CuboTasa

    res = correr(
        conn,
        "amazon_mx",
        lector=lector,
        escritor=escritor,
        fees=fees,
        construir_cuerpo=lambda **kw: kw,
        limitador=CuboTasa(
            sleep=lambda s: None, clock=_time.monotonic, capacidad=100, tasa=1000.0
        ),
        owner="comparar-0b",
    )
assert res.decisiones == goals, f"decisiones={res.decisiones} goals={goals}"
filas = conn.execute(
    "select listing_id, platform, resultado, motivo, canal, m_actual, goal,"
    " p_actual, p_objetivo, p_aplicado, i_valor, c_valor, f_valor, l_valor,"
    " r_valor, u15, u60, n15, n60, racha_senal, perdiendo, mode"
    f" from precio_decision where decision_date = '{dia}' order by listing_id"
).fetchall()
assert len(filas) == goals, f"filas={len(filas)}"
assert any(f[5] is not None for f in filas), "ninguna fila trae m_actual"
assert not any((f[3] or "").startswith("fee_error:") for f in filas), (
    "hay fee_error: red falsa mal armada"
)
with open(txt, "w", encoding="utf-8") as fh:
    for f in filas:
        fh.write("|".join("" if v is None else str(v) for v in f) + "\n")
print(f"{db}: decisiones={res.decisiones} filas={len(filas)}")
PY
  )
}

echo "== lado A (commit 0.a) contra $DB_A"
ln -s "$REPO/.venv" "$WT_A/.venv"
corre_lado "$WT_A" "$DB_A" "$TMP/a.txt"
echo "== lado B (arbol de trabajo) contra $DB_B"
corre_lado "$REPO" "$DB_B" "$TMP/b.txt"

echo "== diff"
if diff -u "$TMP/a.txt" "$TMP/b.txt"; then
  echo "COMPARAR OK: diff vacio"
else
  echo "FALLA: el diff no esta vacio"
  exit 1
fi
