#!/usr/bin/env bash
# Checklist post-deploy de S.1 (DoD de la fila): SOLO LECTURA. Corre UNA vez
# despues de desplegar.sh, de la siguiente corrida de estructura y del siguiente
# ciclo del optimizador, y deja su salida en checklist-salida.txt.
# Exit 0 = todo comprobado; 1 = alguna falla; 3 = sin fallas pero todavia sin
# la prueba posterior (corrida de estructura o ciclo); 4 = nada fallo pero algo
# no se pudo medir. Posterior se cuenta desde el arranque del contenedor nuevo,
# no desde el sello. Un ciclo en curso no es falla: es pendiente.
# Uso: cd ~/dev/goncloud-Orbit && bash docs/evidencia/jev-ads-02/ejecucion/S.1/checklist.sh <STAMP> > docs/evidencia/jev-ads-02/ejecucion/S.1/checklist-salida.txt; echo "exit=$?"
set -uo pipefail

STAMP=${1:?uso: checklist.sh <STAMP impreso por desplegar.sh>}
DESDE=$(date -u -j -f '%Y%m%d-%H%M' "$STAMP" '+%Y-%m-%dT%H:%M:00Z' 2>/dev/null || date -u -d "${STAMP:0:8} ${STAMP:9:2}:${STAMP:11:2}" '+%Y-%m-%dT%H:%M:00Z')
REPO=$(git rev-parse --show-toplevel)
DIR=docs/evidencia/jev-ads-02/ejecucion/S.1
FALLAS=0
PENDIENTE=0
NOMEDIDO=0
cd "$REPO"

revisa() {  # revisa <nombre> <esperado> <obtenido> (obtenido ya medido)
  if [ "$2" = "$3" ]; then echo "OK    $1 = $3"; else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}
mide() {  # mide <nombre> <esperado> <obtenido>: lectura vacia = no se pudo medir (salida 4), no falla
  if [ -z "$3" ]; then echo "SIN MEDIR $1 (lectura vacia)"; NOMEDIDO=1; else revisa "$1" "$2" "$3"; fi
}
lee() { ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'; }

echo "== 0) El servidor contesta (posterior se cuenta desde el arranque, sello $STAMP solo para los logs)"
RUNNING=$(ssh goncloud "docker inspect -f '{{.State.Running}}' orbit-app-1" 2>/dev/null || true)
if [ -z "$RUNNING" ]; then
  echo "SIN MEDIR el servidor no contesta (docker inspect vacio)"
  echo "== RESULTADO: 0 falla(s), prueba posterior sin medir, con lecturas sin medir"
  exit 4
fi
revisa "contenedor orbit-app-1 corriendo" true "$RUNNING"
ARRANQUE=$(ssh goncloud "docker inspect -f '{{.State.StartedAt}}' orbit-app-1" 2>/dev/null || true)
if [ -z "$ARRANQUE" ]; then
  echo "SIN MEDIR arranque del contenedor (se usa el sello $DESDE como ventana)"
  NOMEDIDO=1
  ARRANQUE="$DESDE"
else
  echo "arranque del contenedor: $ARRANQUE"
fi

echo "== 1) HTTP: health, /cortes, asesoria inexistente"
mide "GET /health" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/health' 2>/dev/null || true)"
mide "GET /cortes" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/cortes' 2>/dev/null || true)"
mide "GET /api/dashboard/cortes" 200 "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/api/dashboard/cortes' 2>/dev/null || true)"
mide "GET /api/fabrica/asesoria/<64 ceros>" 404 \
  "$(ssh goncloud 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8010/api/fabrica/asesoria/0000000000000000000000000000000000000000000000000000000000000000' 2>/dev/null || true)"
if LOGS=$(ssh goncloud "docker logs orbit-app-1 --since $DESDE 2>&1"); then
  mide "logs 'asesoria Jev ilegible' desde el sello" 0 "$(printf '%s\n' "$LOGS" | grep -c 'asesoria Jev ilegible')"
else
  echo "SIN MEDIR logs del contenedor (docker logs fallo)"
  NOMEDIDO=1
fi

echo "== 2) CLI dentro del contenedor, sin credenciales; Jev sigue apagado"
mide "tools.jev_ads sin ORBIT_DSN_ADMIN (exit)" 2 \
  "$(ssh goncloud 'docker exec orbit-app-1 env -u ORBIT_DSN_ADMIN python -m tools.jev_ads evaluar --plataforma amazon_mx --grupo-id 1 --termino t --solicitud 00000000-0000-0000-0000-000000000001 --aplicar >/dev/null 2>&1; echo $?' 2>/dev/null || true)"
mide "tools.jev_fichas --help (exit)" 0 "$(ssh goncloud 'docker exec orbit-app-1 python -m tools.jev_fichas --help >/dev/null 2>&1; echo $?' 2>/dev/null || true)"
mide "cycle/apply_cola/apply_harvest importan app.jev_*" False \
  "$(ssh goncloud 'docker exec orbit-app-1 python -c "import sys, app.cycle, app.apply_cola, app.apply_harvest; print(any(m.startswith(\"app.jev\") for m in sys.modules))"' 2>/dev/null || true)"
JEV=$(echo "SELECT (SELECT count(*) FROM jev_ficha_version) || '|' || (SELECT count(*) FROM jev_revision) || '|' || (SELECT count(*) FROM jev_par_evento);" | lee 2>/dev/null || true)
if [ -n "$JEV" ]; then echo "filas Jev (fichas|revisiones|eventos, informativo): $JEV"; else echo "filas Jev (informativo): no se pudo leer"; fi

echo "== 3) Permisos y esquema S.1 (consulta como orbit_read)"
mide "permisos.sql" "permisos OK" "$(lee < "$DIR/permisos.sql" 2>/dev/null || true)"
mide "orbit_admin miembro de app_jev (CLI del asesor)" t \
  "$(echo "SELECT pg_has_role('orbit_admin', 'app_jev', 'MEMBER');" | lee 2>/dev/null || true)"
mide "tablas del acta (plataforma|grupo)" "t|t" \
  "$(echo "SELECT to_regclass('public.ads_listado_plataforma') IS NOT NULL, to_regclass('public.ads_listado_grupo') IS NOT NULL;" | lee 2>/dev/null || true)"
mide "triggers append-only del acta" 4 \
  "$(echo "SELECT count(*) FROM pg_trigger WHERE tgrelid IN (to_regclass('public.ads_listado_plataforma'), to_regclass('public.ads_listado_grupo')) AND NOT tgisinternal;" | lee 2>/dev/null || true)"

echo "== 4) Corrida de estructura posterior al arranque ($ARRANQUE)"
if ! CORRIDAS=$(echo "SELECT id || ' ok=' || coalesce(ok::text, 'NULL') || ' ' || started_at FROM ingest_run WHERE source = 'amazon_ads_structure_v2' AND started_at > '$ARRANQUE' ORDER BY id;" | lee); then
  echo "SIN MEDIR corridas de estructura posteriores"
  NOMEDIDO=1
elif [ -z "$CORRIDAS" ]; then
  echo "INCOMPLETO: todavia no corre estructura despues de $ARRANQUE; vuelve a correr el checklist despues de la siguiente corrida"
  PENDIENTE=1
else
  echo "$CORRIDAS"
  ULTIMA=$(echo "SELECT id FROM ingest_run WHERE source = 'amazon_ads_structure_v2' AND started_at > '$ARRANQUE' ORDER BY id DESC LIMIT 1;" | lee 2>/dev/null || true)
  if [ -z "$ULTIMA" ]; then
    echo "SIN MEDIR ultima corrida de estructura"
    NOMEDIDO=1
  else
    mide "ultima corrida posterior ok ($ULTIMA)" t "$(echo "SELECT ok FROM ingest_run WHERE id = $ULTIMA;" | lee 2>/dev/null || true)"
    HAY_ACTA=$(echo "SELECT to_regclass('public.ads_listado_plataforma') IS NOT NULL;" | lee 2>/dev/null || true)
    if [ "$HAY_ACTA" = "f" ]; then
      echo "FALLA acta de la corrida $ULTIMA: tabla ausente (el despliegue la crea)"
      FALLAS=$((FALLAS + 1))
    elif [ -z "$HAY_ACTA" ]; then
      echo "SIN MEDIR acta de la corrida $ULTIMA"
      NOMEDIDO=1
    else
      ACTA=$(echo "SELECT count(*) FROM ads_listado_plataforma WHERE ingest_run_id = $ULTIMA;" | lee 2>/dev/null || true)
      if [ -z "$ACTA" ]; then
        echo "SIN MEDIR acta de la corrida $ULTIMA"
        NOMEDIDO=1
      elif [ "$ACTA" = "0" ]; then
        echo "FALLA acta de la corrida $ULTIMA: 0 filas (corrida sin acta)"
        FALLAS=$((FALLAS + 1))
      else
        echo "OK    acta de la corrida $ULTIMA = $ACTA fila(s)"
      fi
    fi
  fi
fi

echo "== 5) Primer ciclo del optimizador despues del arranque"
if ! CICLOS=$(echo "SELECT id || ' ' || coalesce(platform::text, '-') || ' ' || status || ' ' || coalesce(decisions_count, 0) FROM optimizer_cycle WHERE started_at > '$ARRANQUE' ORDER BY id;" | lee); then
  echo "SIN MEDIR ciclos posteriores"
  NOMEDIDO=1
elif [ -z "$CICLOS" ]; then
  echo "INCOMPLETO: todavia no corre un ciclo despues de $ARRANQUE; vuelve a correr el checklist despues del siguiente ciclo"
  PENDIENTE=1
else
  echo "$CICLOS"
  revisa "ciclos posteriores fallidos" 0 "$(echo "$CICLOS" | grep -c -E ' failed ')"
  if echo "$CICLOS" | grep -q -E ' running '; then
    echo "INCOMPLETO: hay un ciclo en curso; vuelve a correr el checklist cuando termine"
    PENDIENTE=1
  fi
fi

echo "== RESULTADO: $FALLAS falla(s), prueba posterior $([ "$PENDIENTE" = 1 ] && echo PENDIENTE || echo comprobada), $([ "$NOMEDIDO" = 1 ] && echo con lecturas sin medir || echo todo medido)"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
[ "$PENDIENTE" = 0 ] || exit 3
