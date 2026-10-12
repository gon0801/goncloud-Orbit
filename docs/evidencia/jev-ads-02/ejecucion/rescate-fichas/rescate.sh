#!/usr/bin/env bash
# rescate.sh <ensayo|prod> [--acepto-escribir-en-produccion]
#
# Repone las 868 fichas, 24 revisiones y 11272 juicios del respaldo apartado
# (apartado_jev_2026-10-06) en la base destino:
#   ensayo -> orbit_rescate_ensayo (se borra y se recrea con el respaldo
#             diario mas nuevo)
#   prod   -> orbit (exige --acepto-escribir-en-produccion y un ensayo OK
#             de menos de 2 horas en $ENSAYO_SALIDA)
#
# Todo pasa en el servidor goncloud; esta script solo orquesta por ssh.
# La carga es UNA transaccion: o entra todo o no entra nada.
# Ultima linea posible:
#   RESCATE <modo> OK                          (codigo 0)
#   RESCATE <modo> OK (ya estaba cargado)      (codigo 0)
#   RESCATE <modo> FALLA: <causa>              (nada quedo escrito, codigo 1)
#   RESCATE <modo> FALLA: JOB CORRIENDO        (codigo 75, unico reintentable)
#   RESCATE <modo> CARGADO CON DIFERENCIA: <tabla>  (la carga quedo escrita)
set -euo pipefail
cd "$(dirname "$0")"

MODO="${1:-}"
FLAG="${2:-}"
ESPERADO_ORIGEN="868,0,24,0,11272"

if [ "$MODO" != "ensayo" ] && [ "$MODO" != "prod" ]; then
  echo "uso: bash rescate.sh <ensayo|prod> [--acepto-escribir-en-produccion]" >&2
  exit 2
fi
if [ "$MODO" = "prod" ] && [ "$FLAG" != "--acepto-escribir-en-produccion" ]; then
  echo "RESCATE prod FALLA: falta la bandera --acepto-escribir-en-produccion"
  exit 1
fi

falla() {
  echo "RESCATE $MODO FALLA: $1"
  exit 1
}

diferencia() {
  echo "RESCATE $MODO CARGADO CON DIFERENCIA: $1"
  exit 1
}

# ---------- paso 1: origen ----------
paso1() {
  ssh goncloud bash -s <<'EOS'
set -euo pipefail
B=/mnt/data/appdata/orbit/backups
DUMP="$B/apartado_jev_2026-10-06/orbit_2026-10-06.dump"
SHA=e77a3195f1636552974f0766eb63013e094ace7a1b98b6f300b160f58e457d21
if [ ! -f "$DUMP" ]; then
  echo "ORIGEN sin dump apartado"
  exit 10
fi
s=$(sha256sum "$DUMP" | awk '{print $1}')
if [ "$s" != "$SHA" ]; then
  echo "ORIGEN sha256 distinto: $s"
  exit 10
fi
echo "ORIGEN sha256 del apartado ok ($s)"
existe=$(docker exec orbit-db-1 psql -U orbit -d postgres -X -q -tA \
  -c "SELECT count(*) FROM pg_catalog.pg_database WHERE datname = 'orbit_rescate_jev'")
if [ "$existe" = "0" ]; then
  ERR=$(mktemp /tmp/rescate-origen-err.XXXXXX)
  if ! docker exec orbit-db-1 psql -U orbit -d postgres -X -q -v ON_ERROR_STOP=1 \
       -c "CREATE DATABASE orbit_rescate_jev" 2>"$ERR"; then
    echo "ORIGEN falla create: $(head -n1 "$ERR")"
  elif ! docker exec -i orbit-db-1 pg_restore -U orbit -d orbit_rescate_jev \
       --exit-on-error < "$DUMP" 2>"$ERR"; then
    echo "ORIGEN falla restore: $(head -n1 "$ERR")"
  else
    echo "ORIGEN restaurado desde el apartado"
  fi
else
  echo "ORIGEN ya existia"
fi
c=$(docker exec orbit-db-1 psql -U orbit -d orbit_rescate_jev -X -q -tA \
    -c "SELECT (SELECT count(*) FROM public.jev_ficha_version) || ',' || (SELECT count(*) FROM public.jev_ficha_revocacion) || ',' || (SELECT count(*) FROM public.jev_revision) || ',' || (SELECT count(*) FROM public.jev_revision WHERE sujeto_tipo = 'lote') || ',' || (SELECT count(*) FROM public.jev_par_evento)" \
    2>/dev/null) || c="error"
echo "ORIGEN conteos: $c"
EOS
}

# ---------- pasos 4 y 7: hashes md5 de una base ----------
hashes_md5() {
  ssh goncloud bash -s -- "$1" "$2" <<'EOS'
set -euo pipefail
DB="$1"
ETQ="$2"
OPTS='-c timezone=UTC -c datestyle=ISO,MDY'
h1=$(docker exec -e PGOPTIONS="$OPTS" orbit-db-1 psql -U orbit -d "$DB" -X -q -tA \
  -c "SELECT md5(string_agg(md5(t::text), '' ORDER BY t.id)) FROM public.jev_ficha_version t")
h2=$(docker exec -e PGOPTIONS="$OPTS" orbit-db-1 psql -U orbit -d "$DB" -X -q -tA \
  -c "SELECT md5(string_agg(md5(t::text), '' ORDER BY t.id)) FROM public.jev_par_evento t")
h3=$(docker exec -e PGOPTIONS="$OPTS" orbit-db-1 psql -U orbit -d "$DB" -X -q -tA \
  -c "SELECT md5(string_agg(md5(t::text), '' ORDER BY t.solicitud)) FROM public.jev_revision t WHERE t.sujeto_tipo <> 'lote'")
echo "HASH $ETQ jev_ficha_version $h1"
echo "HASH $ETQ jev_par_evento $h2"
echo "HASH $ETQ jev_revision $h3"
EOS
}

# ---------- paso 1 ----------
echo "== paso 1: origen (orbit_rescate_jev) =="
s1=$(paso1) || { echo "$s1"; falla "origen: $(echo "$s1" | tail -n1)"; }
echo "$s1"
conteos=$(echo "$s1" | sed -n 's/^ORIGEN conteos: //p' | tail -n1)
if [ "$conteos" != "$ESPERADO_ORIGEN" ]; then
  echo "conteos del origen no dan ($conteos): limpiar.sh y un solo reintento"
  bash limpiar.sh || falla "limpiar.sh fallo en el reintento del origen"
  s1=$(paso1) || { echo "$s1"; falla "origen tras reintento: $(echo "$s1" | tail -n1)"; }
  echo "$s1"
  conteos=$(echo "$s1" | sed -n 's/^ORIGEN conteos: //p' | tail -n1)
  [ "$conteos" = "$ESPERADO_ORIGEN" ] || falla "conteos del origen tras reintento: $conteos"
fi

# ---------- paso 2: destino ----------
echo "== paso 2: destino =="
if [ "$MODO" = "ensayo" ]; then
  DESTINO=orbit_rescate_ensayo
  s2=$(ssh goncloud bash -s <<'EOS'
set -euo pipefail
N=$(ls -1dt /mnt/data/appdata/orbit/backups/orbit_[0-9]*[0-9]/ | head -1)
DUMP="$N$(basename "$N").dump"
ERR=$(mktemp /tmp/rescate-ensayo-err.XXXXXX)
if ! docker exec orbit-db-1 psql -U orbit -d postgres -X -q -v ON_ERROR_STOP=1 \
     -c "DROP DATABASE IF EXISTS orbit_rescate_ensayo" 2>"$ERR"; then
  echo "DESTINO falla drop: $(head -n1 "$ERR")"
  exit 1
fi
if ! docker exec orbit-db-1 psql -U orbit -d postgres -X -q -v ON_ERROR_STOP=1 \
     -c "CREATE DATABASE orbit_rescate_ensayo" 2>"$ERR"; then
  echo "DESTINO falla create: $(head -n1 "$ERR")"
  exit 1
fi
if ! docker exec -i orbit-db-1 pg_restore -U orbit -d orbit_rescate_ensayo \
     --exit-on-error < "$DUMP" 2>"$ERR"; then
  echo "DESTINO falla restore: $(head -n1 "$ERR")"
  exit 1
fi
echo "DESTINO ensayo recreado desde $(basename "$N")"
EOS
) || { echo "$s2"; falla "destino ensayo: $(echo "$s2" | tail -n1)"; }
  echo "$s2"
else
  DESTINO=orbit
  [ -n "${ENSAYO_SALIDA:-}" ] || falla "falta la variable ENSAYO_SALIDA"
  [ -f "$ENSAYO_SALIDA" ] || falla "no existe el archivo ENSAYO_SALIDA"
  if ! find "$ENSAYO_SALIDA" -mmin -120 | grep -q .; then
    falla "ENSAYO_SALIDA tiene 2 horas o mas"
  fi
  ult=$(tail -n1 "$ENSAYO_SALIDA")
  case "$ult" in
    "RESCATE ensayo OK"*) ;;
    *) falla "ENSAYO_SALIDA no termina en RESCATE ensayo OK" ;;
  esac
  echo "DESTINO prod autorizado (ensayo vigente: $ult)"
fi

# ---------- paso 3: guardas del destino ----------
echo "== paso 3: guardas del destino ($DESTINO) =="
s3=$(ssh goncloud bash -s -- "$DESTINO" <<'EOS'
set -euo pipefail
DESTINO="$1"
q() { docker exec orbit-db-1 psql -U orbit -d "$DESTINO" -X -q -tA -c "$1"; }
c=$(q "SELECT (SELECT count(*) FROM public.jev_ficha_version) || ',' || (SELECT count(*) FROM public.jev_ficha_revocacion) || ',' || (SELECT count(*) FROM public.jev_revision) || ',' || (SELECT count(*) FROM public.jev_revision WHERE sujeto_tipo <> 'lote') || ',' || (SELECT count(*) FROM public.jev_par_evento)")
echo "GUARDA conteos (fichas,revocaciones,revisiones,no_lote,juicios): $c"
f=$(echo "$c" | cut -d, -f1)
r=$(echo "$c" | cut -d, -f2)
nl=$(echo "$c" | cut -d, -f4)
j=$(echo "$c" | cut -d, -f5)
estado=""
if [ "$f" = "868" ] && [ "$r" = "0" ] && [ "$nl" = "24" ] && [ "$j" = "11272" ]; then
  estado="ya_cargado"
elif [ "$f" = "0" ] && [ "$r" = "0" ] && [ "$nl" = "0" ] && [ "$j" = "0" ]; then
  estado="cargar"
else
  echo "GUARDA FALLA conteos del destino: $c"
  exit 1
fi
t=$(q "SELECT count(*) = 11 AND bool_and(tgenabled = 'O') FROM pg_catalog.pg_trigger WHERE NOT tgisinternal AND tgrelid IN ('public.jev_ficha_version'::regclass, 'public.jev_ficha_revocacion'::regclass, 'public.jev_revision'::regclass, 'public.jev_par_evento'::regclass)")
echo "GUARDA triggers (11 en O): $t"
if [ "$t" != "t" ]; then
  echo "GUARDA FALLA triggers: $t"
  exit 1
fi
cols() {
  docker exec orbit-db-1 psql -U orbit -d "$1" -X -q -tA \
    -c "SELECT string_agg(column_name, ', ' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema = 'public' AND table_name = '$2'"
}
check_cols() {
  v=$(cols "$1" "$2")
  if [ "$v" != "$3" ]; then
    echo "GUARDA FALLA columnas $1.$2"
    exit 1
  fi
  echo "GUARDA columnas $1.$2 ok"
}
for db in "$DESTINO" orbit_rescate_jev; do
  check_cols "$db" jev_ficha_version "id, producto_id, plataforma, listings, hechos, desconocidos, sha256, aprobador, observado_at, revisar_antes_de, created_at"
  check_cols "$db" jev_ficha_revocacion "id, ficha_version_id, autor, motivo, fecha"
  check_cols "$db" jev_revision "solicitud, sujeto_tipo, decision_id, plan_canonico, plan_sha256, fuentes_semillas, censos, ficha_version_ids, contrato, decided_at, captured_at, created_at"
  check_cols "$db" jev_par_evento "id, revision_id, termino_sha256, ficha_version_id, contrato_sha256, ordinal, tipo, request_sha256, respuesta, error, intencion_id, reutiliza_id, duracion_ms, usage, created_at"
done
if [ ! -e /tmp/jev-senales.lock ]; then
  echo "GUARDA FALLA candado: no existe /tmp/jev-senales.lock"
  exit 1
fi
if ( flock -n 9; true ) 9</tmp/jev-senales.lock; then
  echo "GUARDA candado ok"
else
  echo "GUARDA FALLA candado: no se pudo abrir para lectura"
  exit 1
fi
echo "GUARDA estado: $estado"
EOS
) || {
  echo "$s3"
  causa=$(echo "$s3" | sed -n 's/^GUARDA FALLA //p' | tail -n1)
  [ -n "$causa" ] || causa=$(echo "$s3" | tail -n1)
  [ -n "$causa" ] || causa="guardas sin salida clara"
  falla "$causa"
}
echo "$s3"
estado=$(echo "$s3" | sed -n 's/^GUARDA estado: //p' | tail -n1)
[ -n "$estado" ] || falla "guardas sin estado"

# ---------- paso 4: hashes esperados ----------
echo "== paso 4: hashes esperados (orbit_rescate_jev) =="
s4=$(hashes_md5 orbit_rescate_jev origen) || { echo "$s4"; falla "hashes del origen: $(echo "$s4" | tail -n1)"; }
echo "$s4"
H_FICHAS=$(echo "$s4" | awk '$3=="jev_ficha_version"{print $4}')
H_JUICIOS=$(echo "$s4" | awk '$3=="jev_par_evento"{print $4}')
H_REV=$(echo "$s4" | awk '$3=="jev_revision"{print $4}')
for h in "$H_FICHAS" "$H_JUICIOS" "$H_REV"; do
  [[ "$h" =~ ^[0-9a-f]{32}$ ]] || falla "hash con formato inesperado: $h"
done

# ---------- paso 7 compartido (segunda lectura y linea final) ----------
segunda_lectura_y_final() {
  mensaje_ok="$1"
  s7=$(hashes_md5 "$DESTINO" destino) && rc7=0 || rc7=$?
  if [ "$rc7" != "0" ]; then
    echo "segunda lectura fallo ($s7); un solo reintento en 5s"
    sleep 5
    s7=$(hashes_md5 "$DESTINO" destino) && rc7=0 || rc7=$?
  fi
  echo "$s7"
  if [ "$rc7" != "0" ]; then
    echo "RESCATE $MODO CARGADO CON DIFERENCIA: segunda lectura fallo"
    exit 1
  fi
  d1=$(echo "$s7" | awk '$3=="jev_ficha_version"{print $4}')
  d2=$(echo "$s7" | awk '$3=="jev_par_evento"{print $4}')
  d3=$(echo "$s7" | awk '$3=="jev_revision"{print $4}')
  [ "$d1" = "$H_FICHAS" ] || diferencia jev_ficha_version
  [ "$d2" = "$H_JUICIOS" ] || diferencia jev_par_evento
  [ "$d3" = "$H_REV" ] || diferencia jev_revision
  echo "$mensaje_ok"
  exit 0
}

if [ "$estado" = "ya_cargado" ]; then
  echo "== el destino ya tenia la carga: no se escribe, solo se comparan hashes =="
  segunda_lectura_y_final "RESCATE $MODO OK (ya estaba cargado)"
fi

# ---------- pasos 5 y 6: datos y carga en una transaccion ----------
echo "== pasos 5 y 6: datos y carga en una transaccion ($DESTINO) =="
s56=$(ssh goncloud bash -s -- "$DESTINO" "$MODO" "$H_FICHAS" "$H_JUICIOS" "$H_REV" <<'EOS'
set -euo pipefail
DESTINO="$1"
MODO="$2"
H_FICHAS="$3"
H_JUICIOS="$4"
H_REV="$5"

ERR=$(mktemp /tmp/rescate-carga-err.XXXXXX)
OUT=$(mktemp /tmp/rescate-carga-out.XXXXXX)
DATOS=$(mktemp /tmp/rescate-jev-datos.XXXXXX)
chmod 600 "$DATOS"
trap 'rm -f "$DATOS"' EXIT

# paso 5: archivo de datos (no sale del servidor; se borra con rm -f al final)
if ! docker exec orbit-db-1 pg_dump -U orbit -d orbit_rescate_jev --data-only \
     -t public.jev_ficha_version -t public.jev_revision > "$DATOS" 2>"$ERR"; then
  echo "CARGA FALLA: datos pg_dump: $(head -n1 "$ERR")"
  exit 1
fi
printf '%s\n' "COPY public.jev_par_evento (id, revision_id, termino_sha256, ficha_version_id, contrato_sha256, ordinal, tipo, request_sha256, respuesta, error, intencion_id, reutiliza_id, duracion_ms, usage, created_at) FROM stdin;" >> "$DATOS"
if ! docker exec orbit-db-1 psql -U orbit -d orbit_rescate_jev -X -q -v ON_ERROR_STOP=1 \
     -c "COPY (SELECT id, revision_id, termino_sha256, ficha_version_id, contrato_sha256, ordinal, tipo, request_sha256, respuesta, error, intencion_id, reutiliza_id, duracion_ms, usage, created_at FROM public.jev_par_evento ORDER BY CASE tipo WHEN 'intencion' THEN 1 WHEN 'resultado' THEN 2 ELSE 3 END, created_at, id) TO STDOUT" \
     >> "$DATOS" 2>"$ERR"; then
  echo "CARGA FALLA: datos copy: $(head -n1 "$ERR")"
  exit 1
fi
printf '%s\n' '\.' >> "$DATOS"
echo "DATOS listos ($(wc -l < "$DATOS") lineas)"

# paso 6: carga, en UNA transaccion
carga() {
  {
    cat <<'SQL'
BEGIN;
SET LOCAL TimeZone = 'UTC'; SET LOCAL DateStyle = 'ISO, MDY'; SET LOCAL lock_timeout = '5s';
LOCK TABLE public.jev_ficha_version, public.jev_revision, public.jev_par_evento IN SHARE ROW EXCLUSIVE MODE;
CREATE TEMP TABLE rescate_antes ON COMMIT DROP AS SELECT count(*) AS n, md5(coalesce(string_agg(md5(t::text), '' ORDER BY t.solicitud), '')) AS h FROM public.jev_revision t WHERE t.sujeto_tipo = 'lote';
ALTER TABLE public.jev_revision DISABLE TRIGGER jev_revision_tiempos;
SQL
    cat "$DATOS"
    cat <<'SQL'
SET LOCAL lock_timeout = '5s';
ALTER TABLE public.jev_revision ENABLE TRIGGER jev_revision_tiempos;
SQL
    cat <<DO
DO \$do\$
DECLARE
  n1 bigint; n2 bigint; n3 bigint; n4 bigint;
  h1 text; h2 text; h3 text;
  na bigint; ha text; nb bigint; hb text;
  tok boolean;
BEGIN
  SELECT count(*) INTO n1 FROM public.jev_ficha_version;
  IF n1 <> 868 THEN RAISE EXCEPTION 'rescate fichas: % en vez de 868', n1; END IF;
  SELECT count(*) INTO n2 FROM public.jev_ficha_revocacion;
  IF n2 <> 0 THEN RAISE EXCEPTION 'rescate revocaciones: % en vez de 0', n2; END IF;
  SELECT count(*) INTO n3 FROM public.jev_par_evento;
  IF n3 <> 11272 THEN RAISE EXCEPTION 'rescate juicios: % en vez de 11272', n3; END IF;
  SELECT count(*) INTO n4 FROM public.jev_revision t WHERE t.sujeto_tipo <> 'lote';
  IF n4 <> 24 THEN RAISE EXCEPTION 'rescate revisiones no-lote: % en vez de 24', n4; END IF;
  SELECT md5(string_agg(md5(t::text), '' ORDER BY t.id)) INTO h1 FROM public.jev_ficha_version t;
  IF h1 <> '$H_FICHAS' THEN RAISE EXCEPTION 'rescate hash fichas: %', h1; END IF;
  SELECT md5(string_agg(md5(t::text), '' ORDER BY t.id)) INTO h2 FROM public.jev_par_evento t;
  IF h2 <> '$H_JUICIOS' THEN RAISE EXCEPTION 'rescate hash juicios: %', h2; END IF;
  SELECT md5(string_agg(md5(t::text), '' ORDER BY t.solicitud)) INTO h3 FROM public.jev_revision t WHERE t.sujeto_tipo <> 'lote';
  IF h3 <> '$H_REV' THEN RAISE EXCEPTION 'rescate hash revisiones: %', h3; END IF;
  SELECT n, h INTO na, ha FROM pg_temp.rescate_antes;
  SELECT count(*) INTO nb FROM public.jev_revision t WHERE t.sujeto_tipo = 'lote';
  IF nb <> na THEN RAISE EXCEPTION 'rescate lote: % filas en vez de %', nb, na; END IF;
  SELECT md5(coalesce(string_agg(md5(t::text), '' ORDER BY t.solicitud), '')) INTO hb FROM public.jev_revision t WHERE t.sujeto_tipo = 'lote';
  IF hb <> ha THEN RAISE EXCEPTION 'rescate hash lote distinto'; END IF;
  SELECT count(*) = 11 AND bool_and(tgenabled = 'O') INTO tok FROM pg_catalog.pg_trigger WHERE NOT tgisinternal AND tgrelid IN ('public.jev_ficha_version'::regclass, 'public.jev_ficha_revocacion'::regclass, 'public.jev_revision'::regclass, 'public.jev_par_evento'::regclass);
  IF NOT tok THEN RAISE EXCEPTION 'rescate triggers: no estan los 11 en O'; END IF;
END;
\$do\$;
DO
    cat <<'SQL'
COMMIT;
\echo CARGA CONFIRMADA
SQL
  } | docker exec -i orbit-db-1 psql -U orbit -d "$DESTINO" -X -q -v ON_ERROR_STOP=1
}

rc=0
if [ "$MODO" = "prod" ]; then
  (
    flock -n 9 || { echo "JOB CORRIENDO"; exit 75; }
    carga > "$OUT" 2>&1
  ) 9</tmp/jev-senales.lock || rc=$?
else
  carga > "$OUT" 2>&1 || rc=$?
fi

if [ "$rc" = "75" ]; then
  echo "CARGA FALLA: JOB CORRIENDO"
  exit 75
fi
if [ "$rc" != "0" ]; then
  linea=$(grep -m1 -E '^(ERROR|FATAL|psql:)' "$OUT" || true)
  if [ -z "$linea" ]; then linea=$(head -n1 "$OUT" || true); fi
  if [ -z "$linea" ]; then linea="psql salio $rc sin mensaje"; fi
  echo "CARGA FALLA: $linea"
  exit 1
fi
if ! grep -q 'CARGA CONFIRMADA' "$OUT"; then
  echo "CARGA FALLA: sin la linea CARGA CONFIRMADA"
  exit 1
fi
echo "CARGA CONFIRMADA en $DESTINO"
EOS
) && rc56=0 || rc56=$?
echo "$s56"
if [ "$rc56" = "75" ]; then
  echo "RESCATE $MODO FALLA: JOB CORRIENDO"
  exit 75
fi
if [ "$rc56" != "0" ]; then
  causa=$(echo "$s56" | sed -n 's/^CARGA FALLA: //p' | tail -n1)
  [ -n "$causa" ] || causa=$(echo "$s56" | tail -n1)
  [ -n "$causa" ] || causa="falla remota sin mensaje"
  falla "$causa"
fi

# ---------- paso 7: segunda lectura, despues del COMMIT ----------
echo "== paso 7: segunda lectura ($DESTINO) =="
segunda_lectura_y_final "RESCATE $MODO OK"
