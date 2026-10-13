#!/usr/bin/env bash
# Checklist posterior al despliegue del cierre de JEV ADS 02. Solo lectura:
# GET y SELECT, mas una corrida en seco del job (sin --aplicar no escribe
# ni llama a TypeSafe). Sale 1 si algo no coincide.
# Uso: bash docs/evidencia/jev-ads-02/ejecucion/cierre/checklist.sh <sha desplegado>
set -uo pipefail

SHA=${1:?uso: checklist.sh <sha desplegado>}
SRV=/mnt/data/appdata/orbit
MAL=0

revisa() {  # nombre esperado obtenido
  if [ "$2" = "$3" ]; then echo "OK   $1: $3"; else echo "MAL  $1: esperado '$2', obtenido '$3'"; MAL=$((MAL + 1)); fi
}
q() {
  ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1'
}
http() { ssh goncloud "curl -s -o /dev/null -w %{http_code} 'http://127.0.0.1:8010$1'"; }
md5_de() { if command -v md5 >/dev/null; then md5 -q; else md5sum | cut -d' ' -f1; fi; }

echo "== 1) El contenedor corre el codigo del SHA"
for f in app/jev_avisos.py app/jev_senales.py app/jev_vista.py app/api_dashboard.py; do
  revisa "md5 en el contenedor $f" "$(git show "$SHA:$f" | md5_de)" \
    "$(ssh goncloud "docker exec orbit-app-1 md5sum $f" | cut -d' ' -f1)"
done

echo "== 2) HTTP"
for ruta in /health /cortes /salud "/gasto-sin-venta?plataforma=amazon_mx" "/gasto-sin-venta?plataforma=amazon_us"; do
  revisa "GET $ruta" 200 "$(http "$ruta")"
done

echo "== 3) J6-31: toda senal ajena con roster probado trae listado_de y la frase cita ese dia"
ssh goncloud "curl -s 'http://127.0.0.1:8010/api/dashboard/gasto-sin-venta?plataforma=amazon_mx'; echo; curl -s 'http://127.0.0.1:8010/api/dashboard/gasto-sin-venta?plataforma=amazon_us'" \
  | python3 -c '
import json, sys

ajenas = sin_fecha = frase_mal = 0

def recorre(nodo):
    global ajenas, sin_fecha, frase_mal
    if isinstance(nodo, dict):
        if nodo.get("lectura") == "ajena" and nodo.get("frase"):
            ajenas += 1
            listado = nodo.get("listado_de")
            if not listado:
                sin_fecha += 1
            elif f"listado de Amazon del {listado[:10]}" not in nodo["frase"]:
                frase_mal += 1
        for valor in nodo.values():
            recorre(valor)
    elif isinstance(nodo, list):
        for valor in nodo:
            recorre(valor)

for linea in sys.stdin:
    if linea.strip():
        recorre(json.loads(linea))
print(f"ajenas={ajenas} sin_listado_de={sin_fecha} frase_con_otra_fecha={frase_mal}")
sys.exit(0 if ajenas and not sin_fecha and not frase_mal else 1)
' || { echo "MAL  J6-31 en pantalla"; MAL=$((MAL + 1)); }

echo "== 4) El aviso sigue apagado y no hay filas de aviso"
revisa "jev.avisos" ausente "$(echo "SELECT COALESCE(settings->>'jev.avisos', 'ausente') FROM config_version ORDER BY id DESC LIMIT 1;" | q)"
revisa "filas en jev_aviso" 0 "$(ssh goncloud "docker exec orbit-db-1 psql -U orbit -d orbit -X -q -tA -c 'SELECT count(*) FROM jev_aviso'")"

echo "== 5) El job corre en seco con el codigo nuevo"
SECO=$(ssh goncloud "docker exec orbit-app-1 python -m app.cli jev-senales" 2>&1 | tail -1)
echo "$SECO"
case "$SECO" in "jev-senales seco: unidades="*) echo "OK   seco";; *) echo "MAL  seco"; MAL=$((MAL + 1));; esac

echo "== 6) secrets/ sin tocar y puerto en loopback"
revisa "bind 8010" 1 "$(ssh goncloud "ss -lnt | grep -c '127.0.0.1:8010'")"
revisa "bind publico 8010" 0 "$(ssh goncloud "ss -lnt | grep -c -E '(\*|0\.0\.0\.0):8010' || true")"
ssh goncloud "stat -c '%a %U:%G %n' $SRV/secrets"

echo "== RESULTADO: $MAL mal"
[ "$MAL" = "0" ]
