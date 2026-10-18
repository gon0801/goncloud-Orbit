#!/bin/sh
# jev-ads-03 D.3: reporte a 30 dias de jev.bloqueo. SOLO LECTURA
# (la consulta abre BEGIN READ ONLY y cierra con ROLLBACK) y aviso por Telegram.
D=/mnt/data/appdata/orbit/recordatorios
docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -tA -F " | " < $D/jev-ads03-d3.sql > $D/jev-ads03-d3.out 2>&1
{
  echo "<b>[Orbit] jev-ads-03 — bloqueo a 30 dias</b>"
  echo "Criterio: si MX ahorra menos de 167 MXN al mes, proponer apagar jev.bloqueo."
  echo "<pre>"
  sed -e "s/&/\&amp;/g" -e "s/</\&lt;/g" -e "s/>/\&gt;/g" $D/jev-ads03-d3.out
  echo "</pre>"
} > $D/jev-ads03-d3.txt
python3 $D/envia_recordatorio.py $D/jev-ads03-d3.txt >> $D/envios.log 2>&1
