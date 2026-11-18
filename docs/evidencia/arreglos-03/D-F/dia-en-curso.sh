#!/bin/sh
# ARREGLOS 03 D-F.5: dice por Telegram si la primera copia de Mercado Libre Ads despues del
# despliegue ya no guardo el dia de Mexico que iba en curso. SOLO LECTURA (dia-en-curso.sql abre
# BEGIN READ ONLY y cierra con ROLLBACK).
# Va con dia-en-curso.sql en /mnt/data/appdata/orbit/recordatorios/ y se programa con `at` en
# goncloud para las 08:45 UTC siguientes al despliegue (la copia de MeLi Ads corre a las 08:15).
D=/mnt/data/appdata/orbit/recordatorios
if docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -A -t < $D/dia-en-curso.sql > $D/dia-en-curso.out 2>&1; then
  if grep -qx t $D/dia-en-curso.out; then
    T="<b>[Orbit] Mercado Libre Ads ya no guarda el día en curso</b>"
    M="La copia de Mercado Libre Ads de las 08:15 UTC guardó solo días que ya terminaron en México. El día que va en curso se descarta y entra completo en la copia del día siguiente."
  elif grep -qx f $D/dia-en-curso.out; then
    T="<b>[Orbit] Mercado Libre Ads todavía guardó el día en curso</b>"
    M="La última copia de Mercado Libre Ads que escribió datos guardó un día de México que aún no terminaba, con unas horas de gasto. Revisa que el cambio ya esté desplegado y que la copia de las 08:15 UTC haya corrido después."
  else
    T="<b>[Orbit] La lectura del día en curso de Mercado Libre Ads dio algo inesperado</b>"
    M="Revisa la salida de abajo."
  fi
else
  T="<b>[Orbit] La lectura del día en curso de Mercado Libre Ads FALLÓ (psql salió con $?)</b>"
  M="Revisa la salida de abajo."
fi
{
  echo "$T"
  echo "$M"
  echo "<pre>"
  sed -e "s/&/\&amp;/g" -e "s/</\&lt;/g" -e "s/>/\&gt;/g" $D/dia-en-curso.out
  echo "</pre>"
} > $D/dia-en-curso.txt
python3 $D/envia_recordatorio.py $D/dia-en-curso.txt >> $D/envios.log 2>&1
