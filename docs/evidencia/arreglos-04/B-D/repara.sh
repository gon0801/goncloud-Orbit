#!/usr/bin/env bash
# B-D.2: corre repara.py dentro del contenedor con el codigo desplegado.
# Lee antes.txt (solo lectura del inventario B-D.1) y reparte la salida:
#   R|...  -> docs/evidencia/arreglos-04/B-D/reparacion.txt
#   V|...  -> docs/evidencia/arreglos-04/B-D/revisar.txt
# Un reintento de este script no repite una escritura ambigua: repara.py
# concilia el ledger antes de cada envio y omite lo que ya no puede atribuir.
set -uo pipefail
base="docs/evidencia/arreglos-04/B-D"
[ -s "$base/antes.txt" ] || { echo "falta $base/antes.txt" >&2; exit 2; }
grep -q '^FIN|' "$base/antes.txt" || { echo "antes.txt sin recibo FIN" >&2; exit 2; }

b64=$(base64 < "$base/antes.txt" | tr -d '\n')
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

ssh goncloud "docker exec -i -e B_D_ANTES_B64=$b64 orbit-app-1 python -" < "$base/repara.py" \
  > "$tmp/salida.txt" 2> "$tmp/error.txt"
rc=$?

awk -F'|' '$1=="R"{sub(/^R\|/,""); print}' "$tmp/salida.txt" > "$base/reparacion.txt"
awk -F'|' '$1=="V"{sub(/^V\|/,""); print}' "$tmp/salida.txt" > "$base/revisar.txt"
grep '^E|' "$tmp/salida.txt" || true

if [ -s "$tmp/error.txt" ]; then
  cat "$tmp/error.txt" >&2
fi
if ! grep -q '^FIN_REPARACION|' "$base/reparacion.txt"; then
  echo "reparacion incompleta: falta FIN_REPARACION" >&2
  exit 2
fi
exit "$rc"
