#!/bin/sh
# ARREGLOS 03 D-I.5: dice por Telegram si ya entraron ventas de Amazon US con su importe en
# dolares. SOLO LECTURA (ventas-usd.sql abre BEGIN READ ONLY y cierra con ROLLBACK).
# Va con ventas-usd.sql en /mnt/data/appdata/orbit/recordatorios/ y se programa con `at` en
# goncloud para las 08:45 UTC siguientes al despliegue (el libro corre a las 08:15).
D=/mnt/data/appdata/orbit/recordatorios
if docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -A -t < $D/ventas-usd.sql > $D/ventas-usd.out 2>&1; then
  if grep -qx t $D/ventas-usd.out; then
    T="<b>[Orbit] Ya hay ventas de Estados Unidos con su importe en dólares</b>"
    M="El libro de las 08:15 UTC ya guardó al menos una venta de Amazon Estados Unidos con su importe en dólares. Desde ahora cada venta nueva de US lo guarda."
  elif grep -qx f $D/ventas-usd.out; then
    T="<b>[Orbit] Todavía no hay ventas de Estados Unidos con su importe en dólares</b>"
    M="Ese día no entró ninguna venta de Amazon Estados Unidos nueva, así que todavía no hay ninguna con dólares. Las ventas de antes del cambio se quedan sin dólares a propósito; los dólares aparecen con la próxima venta de US."
  else
    T="<b>[Orbit] La lectura de ventas de Estados Unidos en dólares dio algo inesperado</b>"
    M="Revisa la salida de abajo."
  fi
else
  T="<b>[Orbit] La lectura de ventas de Estados Unidos en dólares FALLÓ (psql salió con $?)</b>"
  M="Revisa la salida de abajo."
fi
{
  echo "$T"
  echo "$M"
  echo "<pre>"
  sed -e "s/&/\&amp;/g" -e "s/</\&lt;/g" -e "s/>/\&gt;/g" $D/ventas-usd.out
  echo "</pre>"
} > $D/ventas-usd.txt
python3 $D/envia_recordatorio.py $D/ventas-usd.txt >> $D/envios.log 2>&1
