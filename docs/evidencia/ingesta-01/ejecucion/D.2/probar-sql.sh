#!/usr/bin/env bash
# Prueba de solo lectura de medicion-7d.sql contra la DB de produccion.
# El archivo abre con BEGIN READ ONLY y cierra con ROLLBACK: no escribe ni envia.
# psql alineado deja un espacio al final de las cabeceras. Ese espacio tumbo
# el candado trailing-whitespace (Quality run 39701678564, lineas 3, 8 y 13 de
# sql-prueba-20261022-004326.txt). Toda salida que se guarde como sql-prueba-*.txt
# tiene que pasar por este filtro.
# Uso: bash docs/evidencia/ingesta-01/ejecucion/D.2/probar-sql.sh > sql-prueba-<stamp>.txt
set -euo pipefail
REPO=$(git rev-parse --show-toplevel)
SQL=$REPO/docs/evidencia/ingesta-01/ejecucion/D.2/medicion-7d.sql
H=${ORBIT_SSH:-gonserver}
ssh "$H" 'sudo docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1' < "$SQL" \
  | sed 's/ *$//'
