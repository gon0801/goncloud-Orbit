#!/bin/sh
# TEMPORADA 01 H.3: chequeo de los ciclos de la temporada en vivo. SOLO LECTURA.
# Lo corre `at` un trabajo por fecha (ver transiciones.txt del repo).
# El .sql abre BEGIN READ ONLY y cierra con ROLLBACK, y psql entra con orbit_read
# (ese rol solo puede SELECT). Si el chequeo falla, el aviso NO lleva el
# diagnostico crudo: se queda en primer-ciclo.out, en el servidor.
D=/mnt/data/appdata/orbit/recordatorios
if docker exec -i orbit-db-1 psql -U orbit_read -d orbit -v ON_ERROR_STOP=1 -q -tA -F " | " \
     < $D/primer-ciclo.sql > $D/primer-ciclo.out 2>&1; then
  estado=$(tail -n 1 $D/primer-ciclo.out)
  {
    echo "<b>[Orbit] Temporada en vivo - chequeo del ciclo ($(date -u '+%Y-%m-%d %H:%M') UTC)</b>"
    echo "<b>$estado</b>"
    echo "<pre>"
    sed -e "s/&/\&amp;/g" -e "s/</\&lt;/g" -e "s/>/\&gt;/g" $D/primer-ciclo.out
    echo "</pre>"
  } > $D/primer-ciclo.txt
else
  {
    echo "<b>[Orbit] Temporada en vivo - chequeo del ciclo ($(date -u '+%Y-%m-%d %H:%M') UTC)</b>"
    echo "<b>ERROR: el chequeo no corrio; el detalle queda en $D/primer-ciclo.out</b>"
  } > $D/primer-ciclo.txt
fi
python3 $D/envia_recordatorio.py $D/primer-ciclo.txt >> $D/envios.log 2>&1
