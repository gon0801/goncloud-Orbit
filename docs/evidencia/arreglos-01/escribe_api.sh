#!/usr/bin/env bash
# Llama a un endpoint de escritura de Orbit (POST) desde dentro del contenedor de producción.
# El token se lee ahí (<ORBIT_SECRETS_DIR>/api_write_token): nunca sale del servidor ni se imprime.
# Uso: bash docs/evidencia/arreglos-01/escribe_api.sh /api/ads-optimizer/reversa/negative '{"queue_id": 10}'
# Imprime el código HTTP y el cuerpo de la respuesta. Sale 0 solo con respuesta 2xx.
set -euo pipefail
[ "$#" -eq 2 ] || { echo "uso: escribe_api.sh <ruta> <json>" >&2; exit 2; }
ruta=$1
printf '%s' "$ruta" | grep -Eq '^/api/[a-z0-9/_-]+$' || { echo "ruta invalida: $ruta" >&2; exit 2; }
cuerpo=$(printf '%s' "$2" | base64 | tr -d '\n')
ssh goncloud "docker exec -i orbit-app-1 python - '$ruta' '$cuerpo'" <<'PY'
import base64, os, pathlib, sys, urllib.error, urllib.request
ruta, cuerpo = sys.argv[1], base64.b64decode(sys.argv[2])
token = pathlib.Path(os.environ["ORBIT_SECRETS_DIR"], "api_write_token").read_text(encoding="utf-8").strip()
pet = urllib.request.Request("http://127.0.0.1:8000" + ruta, data=cuerpo, method="POST",
                             headers={"x-orbit-token": token, "content-type": "application/json"})
try:
    with urllib.request.urlopen(pet, timeout=180) as r:
        print(r.status, r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())
    sys.exit(1)
PY
