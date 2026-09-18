#!/usr/bin/env bash
# D.0 (REPRICING 01): base de producción lista para la sonda.
#
# Lo corre el DUEÑO, desde la raíz del repo en la Mac, con el go literal como
# único argumento (en la sesión de Claude: `! bash <este archivo> '<go>'`):
#
#   bash docs/evidencia/repricing-01/D.0/correr.sh 'D.0 aplicada: <go literal>'
#
# Orden (fila D.0 del plan v1.3): preflight de solo lectura → backup del
# schema → 0039 en una transacción → siembra de las 20 claves `precio_*`
# como config_version nueva copiando la vigente → readback como lector →
# config validada con el código de origin/master → cuota `precio:amazon_mx`
# con cap 5 en BEGIN … ROLLBACK. Ningún goal, ninguna decisión, ningún precio.
#
# Re-correrlo es seguro: si la 0039 ya está, no la repite (ni el backup); si
# la vigente ya trae las 20 claves, no siembra; el readback corre siempre.
# Todas las salidas quedan en docs/evidencia/repricing-01/D.0/salidas/<stamp>/,
# con CORRIDA.txt de resumen; la última línea es D0-VERDE o D0-ROJO.
#
# Ensayo sin producción (lo que hizo el lead antes de entregarlo):
#   DESTINO=local ORBIT_D0_DSN_LOCAL=postgresql://orbit:orbit@localhost:5432/<base> \
#   ORBIT_D0_SALIDAS=<dir> bash docs/evidencia/repricing-01/D.0/correr.sh '<go>'
# En local los roles de la app se imitan con SET ROLE (app_read, app_admin).
set -euo pipefail

GO=${1:-}
if [ -z "$GO" ]; then
  echo "ATORADO: falta el go literal (primer argumento; queda como label de la config)"
  exit 2
fi
case "$GO" in
  *$'\n'*) echo "ATORADO: el go literal va en una sola linea"; exit 2 ;;
esac

RAIZ=$(git rev-parse --show-toplevel)
AQUI="$RAIZ/docs/evidencia/repricing-01/D.0"
DESTINO=${DESTINO:-produccion}
STAMP=$(date -u +%Y%m%d-%H%M%S)
case "$DESTINO" in
  produccion) SAL="$AQUI/salidas/$STAMP" ;;
  local)
    : "${ORBIT_D0_DSN_LOCAL:?DESTINO=local exige ORBIT_D0_DSN_LOCAL}"
    SAL="${ORBIT_D0_SALIDAS:-$AQUI/ensayo-local}/$STAMP"
    ;;
  *) echo "ATORADO: DESTINO=$DESTINO (produccion|local)"; exit 2 ;;
esac
mkdir -p "$SAL"
PY="$RAIZ/.venv/bin/python"
[ -x "$PY" ] || { echo "ATORADO: falta $PY (el .venv del repo)"; exit 2; }

RESUMEN="$SAL/CORRIDA.txt"
ROJOS=0
anota() { echo "$*" | tee -a "$RESUMEN"; }
verde() { anota "VERDE $*"; }
rojo() { anota "ROJO  $*"; ROJOS=$((ROJOS + 1)); }
corta() { anota "ROJO  $*"; anota "D0-ROJO: se detuvo en: $*"; exit 1; }

# --- psql por papel -----------------------------------------------------------
# Salida `clave|valor` para lo que el guion lee (lector y admin de la app);
# salida de texto para lo que solo se guarda (migración y siembra).
PSQL_TABLA=(-X -q -tA -F '|' -P pager=off -v ON_ERROR_STOP=1)
PSQL_TEXTO=(-X -P pager=off -v ON_ERROR_STOP=1)

sql_lector() {  # stdin: SQL de solo lectura
  if [ "$DESTINO" = produccion ]; then
    { echo 'BEGIN READ ONLY;'; cat; echo 'COMMIT;'; } | ssh goncloud 'set -eu
      DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)
      test -n "$DSN" || { echo "ATORADO: ORBIT_DSN_READ vacio" >&2; exit 1; }
      docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -F "|" -P pager=off -v ON_ERROR_STOP=1'
  else
    { echo 'SET ROLE app_read;'; echo 'BEGIN READ ONLY;'; cat; echo 'COMMIT;'; } \
      | psql "$ORBIT_D0_DSN_LOCAL" "${PSQL_TABLA[@]}"
  fi
}

sql_admin_app() {  # stdin: SQL con el rol admin de la app
  if [ "$DESTINO" = produccion ]; then
    ssh goncloud 'set -eu
      DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_ADMIN)
      test -n "$DSN" || { echo "ATORADO: ORBIT_DSN_ADMIN vacio" >&2; exit 1; }
      docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -F "|" -P pager=off -v ON_ERROR_STOP=1'
  else
    { echo 'SET ROLE app_admin;'; cat; } | psql "$ORBIT_D0_DSN_LOCAL" "${PSQL_TABLA[@]}"
  fi
}

sql_super() {  # stdin: SQL como superusuario; $1 opcional: -1 (una transacción)
  local una=${1:-}
  if [ -n "$una" ] && [ "$una" != "-1" ]; then echo "sql_super: $una" >&2; return 2; fi
  if [ "$DESTINO" = produccion ]; then
    ssh goncloud "docker exec -i orbit-db-1 psql -U orbit -d orbit -X -P pager=off -v ON_ERROR_STOP=1 $una"
  else
    psql "$ORBIT_D0_DSN_LOCAL" "${PSQL_TEXTO[@]}" $una
  fi
}

val() {  # val <clave> <archivo>: el valor de la primera línea `clave|valor`
  grep -m1 "^$1|" "$2" | cut -d'|' -f2- || true
}

anota "D.0 REPRICING 01 — destino=$DESTINO — inicio_utc=$(date -u +%FT%TZ)"
anota "go_literal: $GO"
anota "commit_del_repo: $(git -C "$RAIZ" rev-parse HEAD)"

# --- 0. preflight (solo lectura) --------------------------------------------
git -C "$RAIZ" fetch -q origin
if [ "$(git -C "$RAIZ" rev-parse origin/master:migrations/0039_precio.sql)" \
     = "$(git -C "$RAIZ" hash-object "$RAIZ/migrations/0039_precio.sql")" ]; then
  verde "0039 del arbol = origin/master"
else
  corta "migrations/0039_precio.sql del arbol no es la de origin/master"
fi

PRE="$SAL/00-preflight.txt"
sql_lector < "$AQUI/preflight.sql" > "$PRE" || corta "preflight: la lectura fallo (ver $PRE)"
anota "pg_version: $(val pg_version "$PRE")"
[ "$(val btree_gist "$PRE")" = 1 ] && verde "btree_gist instalada" || corta "btree_gist ausente"
[ "$(val listing_dup_id_platform "$PRE")" = 0 ] && verde "listing sin (id, platform) duplicado" \
  || corta "listing con (id, platform) duplicado: la 0039 no puede crear listing_id_platform_key"
grep '^cap:ads' "$PRE" > "$SAL/caps-ads-antes.txt" || true
if grep -q '|NULL$' "$SAL/caps-ads-antes.txt" || [ "$(wc -l < "$SAL/caps-ads-antes.txt")" -ne 8 ]; then
  corta "caps de Ads incompletos ANTES de tocar nada (ver $SAL/caps-ads-antes.txt)"
fi
verde "8 caps de Ads vivos antes"
SIN_0039=$(val sin_0039 "$PRE")
CLAVES_ANTES=$(val claves_precio "$PRE")
anota "config vigente antes: id=$(val config_id "$PRE") label=$(val config_label "$PRE") claves_precio=$CLAVES_ANTES"
case "$CLAVES_ANTES" in
  0 | 20) ;;
  *) corta "la config vigente trae $CLAVES_ANTES claves precio_* (ni 0 ni 20): siembra parcial de otro lado" ;;
esac

# --- 1 y 2. backup y migración (solo si falta la 0039) -------------------------
if [ "$SIN_0039" = true ]; then
  if [ "$DESTINO" = produccion ]; then
    # Bloque de docs/DEPLOY.md §«Migración 0039», tal cual.
    ssh goncloud 'set -eu; D=/mnt/data/appdata/orbit/backups; \
      STAMP=$(date -u +%Y%m%d-%H%M%S); TMP="$D/.pre0039_$STAMP.sql.tmp"; \
      OUT="$D/pre0039_precio_$STAMP.sql"; \
      docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > "$TMP"; \
      [ -s "$TMP" ] \
        && grep -q "CREATE TABLE public.listing" "$TMP" \
        && grep -q "CREATE FUNCTION public.apply_cap_de_config" "$TMP" \
        && tail -5 "$TMP" | grep -q "PostgreSQL database dump complete" \
        || { echo "DUMP INVALIDO"; rm -f "$TMP"; exit 1; }; \
      chmod 600 "$TMP"; mv "$TMP" "$OUT"; ls -l "$OUT"' > "$SAL/01-backup.txt" 2>&1 \
      || corta "backup del schema (ver $SAL/01-backup.txt)"
  else
    # El dump local va fuera del repo: jamás se commitea un respaldo.
    DUMPS=$(mktemp -d)
    TMP="$DUMPS/.pre0039.sql.tmp"
    pg_dump --schema-only "$ORBIT_D0_DSN_LOCAL" > "$TMP"
    if [ -s "$TMP" ] && grep -q "CREATE TABLE public.listing" "$TMP" \
       && grep -q "CREATE FUNCTION public.apply_cap_de_config" "$TMP" \
       && tail -5 "$TMP" | grep -q "PostgreSQL database dump complete"; then
      mv "$TMP" "$DUMPS/pre0039_schema.sql"; ls -l "$DUMPS/pre0039_schema.sql" > "$SAL/01-backup.txt"
    else
      rm -f "$TMP"; corta "backup del schema local"
    fi
  fi
  verde "backup del schema: $(tail -1 "$SAL/01-backup.txt")"
  sql_super -1 < "$RAIZ/migrations/0039_precio.sql" > "$SAL/02-migracion.txt" 2>&1 \
    || corta "migracion 0039 (revertida entera por la transaccion; ver $SAL/02-migracion.txt)"
  verde "0039 aplicada en una transaccion (el EXCLUDE con enum + daterange se creo)"
else
  anota "SALTA 0039 ya aplicada: sin backup ni migracion"
fi

# --- 3. siembra (solo si la vigente no trae las claves) ------------------------
if [ "$CLAVES_ANTES" = 0 ]; then
  GO_PSQL=${GO//\\/\\\\}
  GO_PSQL=${GO_PSQL//\'/\'\'}
  { printf "\\set go '%s'\n" "$GO_PSQL"; cat "$AQUI/siembra.sql"; } \
    | sql_super > "$SAL/03-siembra.txt" 2>&1 || corta "siembra (ver $SAL/03-siembra.txt)"
  verde "siembra: config_version nueva ($(grep -m1 -E '^ *[0-9]+ \|' "$SAL/03-siembra.txt" | sed 's/^ *//; s/ *$//'))"
else
  anota "SALTA la vigente ya trae las 20 claves: no se siembra"
fi

# --- 4. readback como lector ----------------------------------------------------
RB="$SAL/04-readback.txt"
sql_lector < "$AQUI/readback.sql" > "$RB" || corta "readback: la lectura fallo (ver $RB)"
TABLAS=precio_cambio,precio_cotizacion,precio_decision,precio_envio_muestra,precio_goal
[ "$(val tablas "$RB")" = "$TABLAS" ] && verde "las cinco tablas precio_*" || rojo "tablas: $(val tablas "$RB")"
for t in precio_goal precio_decision precio_cotizacion precio_envio_muestra precio_cambio; do
  [ "$(val "filas:$t" "$RB")" = 0 ] && verde "$t con cero filas" || rojo "$t con $(val "filas:$t" "$RB") filas"
done
[ "$(val listing_id_platform_key "$RB")" = 1 ] && verde "listing_id_platform_key" || rojo "falta listing_id_platform_key"
[ "$(val exclude:precio_goal_sin_solape "$RB")" = 1 ] && verde "EXCLUDE precio_goal_sin_solape" || rojo "falta el EXCLUDE"
[ "$(val indices_parciales "$RB")" = 2 ] && verde "dos indices parciales" || rojo "indices parciales: $(val indices_parciales "$RB")"
[ "$(grep -c '^triggers:' "$RB")" = 5 ] && verde "triggers habilitados en las cinco tablas" \
  || rojo "triggers en $(grep -c '^triggers:' "$RB") de 5 tablas"
for m in precio:amazon_mx precio:amazon_us precio:meli; do
  [ "$(val "cap:$m" "$RB")" = 5 ] && verde "cap $m = 5" || rojo "cap $m = $(val "cap:$m" "$RB")"
done
grep '^cap:ads' "$RB" > "$SAL/caps-ads-despues.txt" || true
if diff -u "$SAL/caps-ads-antes.txt" "$SAL/caps-ads-despues.txt" > "$SAL/caps-ads-diff.txt"; then
  verde "caps de Ads identicos antes y despues"
else
  rojo "caps de Ads cambiaron (ver $SAL/caps-ads-diff.txt)"
fi
anota "config vigente despues: id=$(val config_id "$RB") label=$(val config_label "$RB")"
if [ "$CLAVES_ANTES" = 0 ]; then
  [ "$(val config_label "$RB")" = "$GO" ] && verde "label = go literal" || rojo "label distinto del go literal"
fi
[ "$(grep -c '^clave:' "$RB")" = 20 ] && verde "20 claves precio_* en la vigente" \
  || rojo "$(grep -c '^clave:' "$RB") claves precio_* en la vigente"

# --- 5. config validada con el código de origin/master ----------------------------
echo 'SELECT settings FROM config_version ORDER BY id DESC LIMIT 1;' | sql_lector > "$SAL/05-settings.json" \
  || corta "no se pudo leer settings"
if "$PY" "$AQUI/verificar_config.py" < "$SAL/05-settings.json" > "$SAL/05-verificar.txt" 2>&1; then
  verde "$(tail -1 "$SAL/05-verificar.txt")"
else
  rojo "config: $(tr '\n' ';' < "$SAL/05-verificar.txt")"
fi

# --- 6. cuota precio:amazon_mx nace con cap 5 (BEGIN … ROLLBACK) -------------------
if sql_admin_app < "$AQUI/cuota.sql" > "$SAL/06-cuota.txt" 2>&1; then
  [ "$(val cuota:precio:amazon_mx "$SAL/06-cuota.txt")" = 5 ] && verde "apply_quota_state precio:amazon_mx nace con cap 5" \
    || rojo "cuota: $(tr '\n' ';' < "$SAL/06-cuota.txt")"
  [ "$(val cuota_filas_despues "$SAL/06-cuota.txt")" = 0 ] && verde "ROLLBACK: cero filas de cuota" \
    || rojo "quedaron filas de cuota"
else
  rojo "cuota: $(tr '\n' ';' < "$SAL/06-cuota.txt")"
fi

anota "fin_utc: $(date -u +%FT%TZ)"
if [ "$ROJOS" -eq 0 ]; then
  anota "D0-VERDE salidas=$SAL"
else
  anota "D0-ROJO: $ROJOS comprobacion(es) en rojo; salidas=$SAL"
  exit 1
fi
