#!/bin/sh
# ingesta-01 D.2: medicion de 7 dias (SOLO LECTURA) y aviso por Telegram. Patron de
# /mnt/data/appdata/orbit/recordatorios/jev-ads03-d3.sh. Se programa con `at`.
D=/mnt/data/appdata/orbit/recordatorios
L=/mnt/data/appdata/orbit/logs
if docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -A -F " | " < $D/ingesta01-d2.sql > $D/ingesta01-d2.out 2>&1; then
  T="<b>[Orbit] ingesta-01 — 7 dias del ingest diario</b>"
else
  T="<b>[Orbit] ingesta-01 — la medicion de 7 dias FALLO (psql)</b>"
fi
# Horarios que la guarda salto, por trabajo: los logs no llevan fecha, asi que es el total
# desde el despliegue (el 21-oct los logs de estos jobs estaban vacios o sin esas frases).
cuenta() { c=$(grep -c "$2" "$L/$1" 2>/dev/null); echo "$1: ${c:-0}"; }
{
  echo "$T"
  echo "<pre>"
  sed -e "s/&/\&amp;/g" -e "s/</\&lt;/g" -e "s/>/\&gt;/g" $D/ingesta01-d2.out
  echo
  echo "(4) horarios saltados por la guarda (total desde el despliegue)"
  cuenta avisos-campana.log "sin métricas de ayer"
  cuenta impulso-vigia.log "sin métricas de ayer"
  cuenta jev-senales.log "sin métricas de ayer"
  cuenta optimizer.log "sin métricas de ayer"
  cuenta optimizer.log "ya hay un ciclo"
  cuenta optimizer.log "running iniciado hace menos de 3 h"
  echo "</pre>"
} > $D/ingesta01-d2.txt
python3 $D/envia_recordatorio.py $D/ingesta01-d2.txt >> $D/envios.log 2>&1
