#!/usr/bin/env bash
# Checklist post-deploy de S.3 (JEV ADS 02): SOLO LECTURA. Corre UNA vez
# despues de desplegar.sh y del siguiente arranque del contenedor, y deja
# su salida en checklist-salida.txt.
# Salidas: 0 = todo comprobado; 1 = alguna FALLA medida; 3 = sin fallas
# pero todavia sin ciclo posterior al arranque (o con uno en curso: un
# ciclo en curso todavia no termino y no es falla); 4 = no pude medir
# (el servidor no contesta o una lectura salio vacia, sin ninguna FALLA
# medida). Una FALLA medida sale con 1 aunque otra lectura quede vacia.
# "Posterior" cuenta desde el arranque del contenedor nuevo
# (docker inspect StartedAt), no desde el sello: el sello que toma como
# argumento es solo informativo (sale en el encabezado de la corrida).
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.3/checklist.sh <STAMP> > docs/evidencia/jev-ads-02/ejecucion/S.3/checklist-salida.txt; echo "exit=$?"
set -uo pipefail

SELLO=${1:-sin-sello}
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/jev-ads-02/ejecucion/S.3
FALLAS=0
PENDIENTE=0
NOMEDIDO=0
cd "$REPO"

revisa() {  # revisa <nombre> <esperado> <obtenido>
  if [ -z "$3" ]; then echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1));
  elif [ "$2" = "$3" ]; then echo "OK    $1 = $3";
  else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}
lee() { ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'; }

echo "== 0) El servidor contesta"
RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1" || true)
if [ -z "$RUNNING" ]; then echo "NO MEDIDO el servidor no contesta (docker inspect vacio)"; exit 4; fi
revisa "orbit-app-1 corriendo" true "$RUNNING"
DESDE=$(ssh goncloud "docker inspect -f '{{.State.StartedAt}}' orbit-app-1" || true)
if [ -z "$DESDE" ]; then echo "NO MEDIDO arranque del contenedor (StartedAt vacio)"; NOMEDIDO=$((NOMEDIDO + 1)); fi

echo "== Checklist S.3 (solo lectura; sello $SELLO, contenedor desde ${DESDE:-sin-dato})"

echo "== 1) HTTP: health, /cortes, /salud"
revisa "GET /health" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/health')"
revisa "GET /cortes" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/cortes')"
revisa "GET /salud" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/salud')"

echo "== 2) CLI dentro del contenedor, sin credenciales"
ssh goncloud 'docker exec orbit-app-1 env -u ORBIT_DSN_ADMIN python -m tools.jev_ads evaluar --plataforma amazon_mx --grupo-id 1 --termino t --solicitud 00000000-0000-0000-0000-000000000001 --aplicar' 2>&1 | tail -1
revisa "tools.jev_ads sin ORBIT_DSN_ADMIN (exit)" 2 \
  "$(ssh goncloud 'docker exec orbit-app-1 env -u ORBIT_DSN_ADMIN python -m tools.jev_ads evaluar --plataforma amazon_mx --grupo-id 1 --termino t --solicitud 00000000-0000-0000-0000-000000000001 --aplicar >/dev/null 2>&1; echo $?')"
revisa "tools.jev_fichas --help (exit)" 0 "$(ssh goncloud 'docker exec orbit-app-1 python -m tools.jev_fichas --help >/dev/null 2>&1; echo $?')"

echo "== 3) Permisos y login del job (consulta como orbit_read)"
revisa "permisos.sql" "permisos OK" "$(lee < "$DIR/permisos.sql")"
revisa "orbit_jev miembro de app_jev" t \
  "$(echo "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orbit_jev') THEN pg_has_role('orbit_jev', 'app_jev', 'MEMBER') ELSE false END;" | lee)"
revisa "ORBIT_DSN_JEV en el contenedor (sin valor)" presente \
  "$(ssh goncloud 'docker exec orbit-app-1 printenv ORBIT_DSN_JEV | grep -q . && echo presente || echo ausente')"

echo "== 4) CHECKs de S.3 (pruebas 2 y 3, via pg_constraint)"
revisa "CHECKs ajena x2 + moneda" 3 \
  "$(echo "SELECT count(*) FROM pg_constraint WHERE conname IN ('jev_senal_ajena_exige_roster', 'jev_senal_lectura_ajena', 'jev_senal_moneda_de_plataforma');" | lee)"
revisa "CHECK sujeto_tipo admite lote" t \
  "$(echo "SELECT pg_get_constraintdef(oid) LIKE '%lote%' FROM pg_constraint WHERE conname = 'jev_revision_sujeto_tipo_check';" | lee)"

echo "== 5) Tablas nuevas vacias; el ciclo no importa Jev"
# En dos lecturas: un CASE con las cinco tablas en la rama ELSE no sirve,
# porque el planificador valida que existan antes de ejecutar el CASE.
NUEVAS_T=$(echo "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('jev_roster', 'jev_senal', 'jev_aviso', 'jev_aviso_entrega', 'jev_corrida');" | lee || true)
if [ -z "$NUEVAS_T" ]; then FILAS=""
elif [ "$NUEVAS_T" = "5" ]; then
  FILAS=$(echo "SELECT (SELECT count(*) FROM jev_roster) + (SELECT count(*) FROM jev_senal) + (SELECT count(*) FROM jev_aviso) + (SELECT count(*) FROM jev_aviso_entrega) + (SELECT count(*) FROM jev_corrida);" | lee || true)
else FILAS="sin-tablas"; fi
revisa "filas en las 5 tablas nuevas" 0 "$FILAS"
revisa "cycle/apply_cola/apply_harvest importan app.jev_*" False \
  "$(ssh goncloud 'docker exec orbit-app-1 python -c "import sys, app.cycle, app.apply_cola, app.apply_harvest; print(any(m.startswith(\"app.jev\") for m in sys.modules))"')"

echo "== 6) Primer ciclo del optimizador posterior al arranque"
if [ -z "$DESDE" ]; then
  echo "NO MEDIDO ciclos posteriores (sin arranque del contenedor)"
  NOMEDIDO=$((NOMEDIDO + 1))
elif [ -z "$(echo 'SELECT 1;' | lee || true)" ]; then
  echo "NO MEDIDO no se pudo leer optimizer_cycle"
  NOMEDIDO=$((NOMEDIDO + 1))
elif ! CICLOS=$(echo "SELECT id || ' ' || coalesce(platform::text, '-') || ' ' || status || ' ' || coalesce(decisions_count, 0) FROM optimizer_cycle WHERE started_at > '$DESDE' ORDER BY id;" | lee); then
  CICLOS=""
  echo "NO MEDIDO no se pudo leer optimizer_cycle"
  NOMEDIDO=$((NOMEDIDO + 1))
elif [ -z "$CICLOS" ]; then
  echo "INCOMPLETO: todavia no corre un ciclo despues de $DESDE; vuelve a correr el checklist despues del siguiente ciclo"
  PENDIENTE=1
else
  echo "$CICLOS"
  revisa "ciclos posteriores fallidos" 0 "$(echo "$CICLOS" | grep -c -E ' failed ' || true)"
  ENCURSO=$(echo "$CICLOS" | grep -c -E ' running ' || true)
  if [ "$ENCURSO" != "0" ]; then
    echo "INCOMPLETO: hay un ciclo en curso, todavia no termino; vuelve a correr el checklist cuando acabe"
    PENDIENTE=1
  fi
fi

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir, ciclo posterior $([ "$PENDIENTE" = 1 ] && echo PENDIENTE || echo comprobado)"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
[ "$PENDIENTE" = 0 ] || exit 3
