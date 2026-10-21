#!/usr/bin/env bash
# Checklist post-deploy de D.1 (ingesta-01): SOLO LECTURA. Nunca corre `cycle`.
# Salidas: 0 = todo comprobado; 1 = alguna FALLA medida; 4 = no pude medir.
# Uso: cd <repo> && bash docs/evidencia/ingesta-01/ejecucion/D.1/checklist.sh [STAMP] > .../checklist-<STAMP>.txt
set -uo pipefail

H=${ORBIT_SSH:-gonserver}
REPO=$(git rev-parse --show-toplevel)
SRV=/mnt/data/appdata/orbit
DIR=docs/evidencia/ingesta-01/ejecucion/D.1
# ORBIT_DEPLOY_SHA: costura SOLO para tests (tests/test_ingesta01_d1_checklist.py):
# SHA alternativo que existe en cualquier checkout (el shallow de CI no tiene el
# objeto del SHA desplegado real). Sin la variable, el camino de produccion es el
# de siempre: sha-desplegado.txt, y si falta, el predeploy mas reciente del server.
SHA=${ORBIT_DEPLOY_SHA:-}
FALLAS=0
NOMEDIDO=0
cd "$REPO"

revisa() {  # revisa <nombre> <esperado> <obtenido>
  if [ -z "$3" ]; then echo "NO MEDIDO $1 (lectura vacia)"; NOMEDIDO=$((NOMEDIDO + 1));
  elif [ "$2" = "$3" ]; then echo "OK    $1 = $3";
  else echo "FALLA $1: esperado '$2', obtenido '$3'"; FALLAS=$((FALLAS + 1)); fi
}

echo "== 0) El servidor contesta"
RUNNING=$(ssh "$H" "sudo docker inspect -f '{{.State.Running}}' orbit-app-1" 2>/dev/null || true)
[ -n "$RUNNING" ] || { echo "NO MEDIDO el servidor no contesta"; exit 4; }
revisa "orbit-app-1 corriendo" true "$RUNNING"
[ -n "$SHA" ] || SHA=$(cat "$DIR/sha-desplegado.txt" 2>/dev/null || true)
[ -n "$SHA" ] || SHA=$(ssh "$H" "ls -d $SRV/predeploy-2* | sort | tail -1 | xargs -I{} sudo cat {}/SHA" 2>/dev/null || true)
echo "== Checklist D.1 ingesta-01 (SHA ${SHA:-sin-dato})"

echo "== 1) md5 server vs SHA desplegado"
if [ -z "$SHA" ]; then echo "NO MEDIDO md5 (sin SHA desplegado)"; NOMEDIDO=$((NOMEDIDO + 1));
else
  N=$(wc -l < "$DIR/archivos.txt" | tr -d ' ')
  ssh "$H" "cd $SRV && sudo xargs md5sum" < "$DIR/archivos.txt" 2>/tmp/ck-md5-err.$$ \
    | sed 's/  / /' | sort -k2 > /tmp/ck-md5-srv.$$ || true
  NSRV=$(grep -c . /tmp/ck-md5-srv.$$ || true)
  if [ "${NSRV:-0}" = "0" ]; then
    echo "NO MEDIDO md5 (lectura del servidor vacia o ssh fallo); stderr:"; NOMEDIDO=$((NOMEDIDO + 1))
    sed -n '1,2p' /tmp/ck-md5-err.$$
  elif [ "$NSRV" != "$N" ]; then
    echo "FALLA md5: el servidor reporto $NSRV de $N archivos (md5sum manda el faltante a stderr, sin linea); stderr:"
    FALLAS=$((FALLAS + 1)); sed -n '1,3p' /tmp/ck-md5-err.$$
  else
    python3 - "$SHA" "$DIR/archivos.txt" <<'PY2' | sort -k2 > /tmp/ck-md5-loc.$$
import hashlib, subprocess, sys
for f in open(sys.argv[2]).read().split():
    b = subprocess.run(["git", "show", f"{sys.argv[1]}:{f}"], capture_output=True, check=True).stdout
    print(hashlib.md5(b).hexdigest(), f)
PY2
    FALLAN=$(diff /tmp/ck-md5-loc.$$ /tmp/ck-md5-srv.$$ | grep -c '^[<>]' || true)
    revisa "md5 de $N archivos: distintos" 0 "$FALLAN"
    diff /tmp/ck-md5-loc.$$ /tmp/ck-md5-srv.$$ | grep '^[<>]' | sed -n '1,10p'
  fi
  rm -f /tmp/ck-md5-srv.$$ /tmp/ck-md5-err.$$ /tmp/ck-md5-loc.$$
fi

echo "== 2) crontab de gon: lineas Orbit = tabla de DEPLOY.md (una vez cada una); ajenas = crontab-antes.txt"
python3 - "$SHA" "$DIR/crontab-antes.txt" > /tmp/ck-ingesta01.out 2>&1 <<'PY'
import re, subprocess, sys
sha, antes = sys.argv[1], sys.argv[2]
doc = subprocess.run(["git", "show", f"{sha}:docs/DEPLOY.md"], capture_output=True, text=True, check=True).stdout
m = re.search(r"ORBIT_BLOCK=\$\(cat <<'CRON'\n(.*?)\nCRON\n", doc, re.S)
bloque = m.group(1).splitlines()
# patrones que el instalador borra (los mismos grep -v de su linea)
pats = ["Orbit (ORBIT 03", "job_key=ingest:", "job_key=ads-salud", "job_key=ads_optimizer", "job_key=avisos-campana",
        "job_key=impulso-vigia", "job_key=jev-senales", "app.cli ingest", "app.cli ads-salud", "app.cli cycle",
        "app.cli avisos-campana", "app.cli impulso-vigia", "app.cli jev-senales"]
orbit = lambda l: any(p in l for p in pats)
ahora = subprocess.run(["ssh", "gonserver", "sudo crontab -u gon -l"], capture_output=True, text=True, check=True).stdout.splitlines()
previo = open(antes, encoding="utf-8").read().splitlines()
lin_bloque = [l for l in bloque if not l.startswith("#")]
ahora_orbit = [l for l in ahora if orbit(l) and not l.startswith("#")]
print("bloque_lineas", len(lin_bloque))
print("orbit_igual_tabla", sorted(ahora_orbit) == sorted(lin_bloque))
print("cada_una_una_vez", all(ahora.count(l) == 1 for l in lin_bloque))
print("ajenas_iguales", [l for l in ahora if not orbit(l)] == [l for l in previo if not orbit(l)])
PY
cat /tmp/ck-ingesta01.out
revisa "crontab: lineas Orbit iguales a la tabla" True "$(sed -n 's/^orbit_igual_tabla //p' /tmp/ck-ingesta01.out)"
revisa "crontab: cada linea Orbit una sola vez" True "$(sed -n 's/^cada_una_una_vez //p' /tmp/ck-ingesta01.out)"
revisa "crontab: ajenas iguales a crontab-antes.txt" True "$(sed -n 's/^ajenas_iguales //p' /tmp/ck-ingesta01.out)"
rm -f /tmp/ck-ingesta01.out

echo "== 3) Ayudas de los comandos nuevos (no corre nada)"
tiene() { grep -q -- "$1" && echo 1 || echo 0; }
revisa "descarga --help muestra --solo-faltantes" 1 \
  "$(ssh "$H" 'sudo docker exec orbit-app-1 python -m app.ads.descarga --help' 2>&1 | tiene --solo-faltantes)"
revisa "cycle --help muestra --forzar" 1 \
  "$(ssh "$H" 'sudo docker exec orbit-app-1 python -m app.cli cycle --help' 2>&1 | tiene --forzar)"

echo "== 4) metricas_de_ayer_listas (solo lectura, ORBIT_DSN_READ) MX y US"
LISTAS=$(ssh "$H" "sudo docker exec -i orbit-app-1 python -" <<'PY' 2>&1
import os
from app.db import connect
from app.metricas_listas import metricas_de_ayer_listas
with connect(os.environ["ORBIT_DSN_READ"], options="-c default_transaction_read_only=on") as conn:
    for p in ("amazon_mx", "amazon_us"):
        print(f"metricas_de_ayer_listas {p}={metricas_de_ayer_listas(conn, p)}")
PY
)
echo "$LISTAS"
revisa "metricas_de_ayer_listas imprime MX y US" 2 "$(printf '%s\n' "$LISTAS" | grep -c '^metricas_de_ayer_listas amazon_\(mx\|us\)=\(True\|False\)$')"

echo "== RESULTADO: $FALLAS falla(s), $NOMEDIDO sin medir"
[ "$FALLAS" -eq 0 ] || exit 1
[ "$NOMEDIDO" = 0 ] || exit 4
