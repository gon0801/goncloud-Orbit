#!/bin/sh
# jev-ads-03 D.3: reporte a 30 dias de jev.bloqueo. SOLO LECTURA
# (la consulta abre BEGIN READ ONLY y cierra con ROLLBACK) y aviso por Telegram.
D=/mnt/data/appdata/orbit/recordatorios
if docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -A -F " | " < $D/jev-ads03-d3.sql > $D/jev-ads03-d3.out 2>&1; then
  T="<b>[Orbit] jev-ads-03 — bloqueo a 30 dias</b>"
else
  T="<b>[Orbit] jev-ads-03 — el reporte a 30 dias FALLO (psql exit $?)</b>"
fi
{
  echo "$T"
  echo "Criterio: si MX ahorra menos de 167 MXN al mes (gasto_30d_equivalente de amazon_mx en (4)), proponer apagar jev.bloqueo."
  echo "<pre>"
  sed -e "s/&/\&amp;/g" -e "s/</\&lt;/g" -e "s/>/\&gt;/g" $D/jev-ads03-d3.out
  echo "</pre>"
} > $D/jev-ads03-d3.txt
python3 $D/envia_recordatorio.py $D/jev-ads03-d3.txt >> $D/envios.log 2>&1
