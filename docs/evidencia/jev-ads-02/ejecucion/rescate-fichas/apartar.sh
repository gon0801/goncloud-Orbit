#!/usr/bin/env bash
# apartar.sh — aparta el respaldo orbit_2026-10-06 para que no lo toque la rotacion.
# Corre en la Mac; todo pasa en el servidor por ssh goncloud.
# Ultima linea: "APARTADO OK <sha256>" o "APARTADO MAL" (codigo != 0).
set -euo pipefail

ssh goncloud bash -s <<'EOS'
set -euo pipefail
B=/mnt/data/appdata/orbit/backups
SHA=e77a3195f1636552974f0766eb63013e094ace7a1b98b6f300b160f58e457d21
DUMP=orbit_2026-10-06.dump

if [ ! -d "$B/apartado_jev_2026-10-06" ]; then
  cp -a -T "$B/orbit_2026-10-06" "$B/apartado_jev_2026-10-06.parcial"
  s=$(sha256sum "$B/apartado_jev_2026-10-06.parcial/$DUMP" | awk '{print $1}')
  if [ "$s" = "$SHA" ]; then
    mv -T "$B/apartado_jev_2026-10-06.parcial" "$B/apartado_jev_2026-10-06"
  fi
fi

if [ -f "$B/apartado_jev_2026-10-06/$DUMP" ]; then
  s=$(sha256sum "$B/apartado_jev_2026-10-06/$DUMP" | awk '{print $1}')
else
  s=ausente
fi

if [ "$s" != "$SHA" ]; then
  echo "APARTADO MAL"
  exit 1
fi
echo "APARTADO OK $s"
EOS
