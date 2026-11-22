#!/bin/sh
# TEMPORADA 01 H.3: chequeo de los ciclos de la temporada en vivo. SOLO LECTURA
# (primer-ciclo.sql abre BEGIN READ ONLY y cierra con ROLLBACK) y aviso por Telegram.
# Lo dispara `at` un trabajo por fecha (ver transiciones.txt del repo).
D=/mnt/data/appdata/orbit/recordatorios
if docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -q -tA -F " | " \
     < $D/primer-ciclo.sql > $D/primer-ciclo.out 2>&1; then
  estado=$(tail -n 1 $D/primer-ciclo.out)
else
  estado="ERROR: el chequeo de temporada no corrio (ver el detalle)"
fi
{
  echo "<b>[Orbit] Temporada en vivo - chequeo del ciclo ($(date -u '+%Y-%m-%d %H:%M') UTC)</b>"
  echo "<b>$estado</b>"
  echo "<pre>"
  sed -e "s/&/\&amp;/g" -e "s/</\&lt;/g" -e "s/>/\&gt;/g" $D/primer-ciclo.out
  echo "</pre>"
} > $D/primer-ciclo.txt
python3 $D/envia_recordatorio.py $D/primer-ciclo.txt >> $D/envios.log 2>&1
