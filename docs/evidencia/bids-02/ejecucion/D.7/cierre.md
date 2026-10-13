# Cierre de D.7

Despliegue del remate de ajustes, rejuego y busquedas con el SHA
ea3405859fe6fcee7984bf77ff87dd914ca9c256 y sello 20261013-1436.
Las salidas reales guardadas en esta carpeta traen su linea exit
al final, como pide R13-M.

- ensayo-1436.txt con ENSAYO OK y exit cero. Antes hubo dos
  intentos en simulacion que no tocaron produccion y quedan
  guardados. ensayo-1435.txt salio con exit uno porque se corrio
  sin el SHA que el script pide. ensayo-1438.txt salio con exit
  uno porque la base local de partida no traia la 0071 aplicada y
  la guarda aborto como debe. El nombre de ese archivo no
  corresponde a su hora interior, que es la de las 1435. Se
  conserva con su nombre de origen para no romper la trazabilidad
  de la corrida y aqui queda dicho.
- desplegar-1436.txt con exit cero. Este despliegue solo movio
  codigo y no aplico migraciones.
- checklist-1437.txt con exit cero y resultado de cero fallas y
  cero sin medir.
- aplicadas-20261013-1436.txt vacio a proposito, porque D.7 no
  despliega migraciones. Vivia sin commit en el worktree de
  despliegue y aqui queda copiado al repo, como pide R13-I. La
  reversa de este sello lo necesita y lo lee de esta misma
  carpeta.

La reversa vigente es rollback.sh 20261013-1436 de esta carpeta.
Lee su configuracion sin depender de que el contenedor de la app
este arriba, como pide R13-L.
