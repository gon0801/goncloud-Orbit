# Cierre de D.6

Despliegue del remate del impulso ya mergeado con el SHA
c52fedc564d9405700dccd3b53d9ffdb6a8df3a8, con sello
20261013-0707. Aplico las migraciones 0071 y 0072. Las salidas
reales guardadas en esta carpeta traen su linea exit al final,
como pide R13-M.

- ensayo-0707.txt con ENSAYO OK y exit cero. El intento previo de
  ensayo-0705.txt salio con exit dos porque se corrio sin la
  variable del DSN de pruebas y cayo al puerto 5432, que no tiene
  servicio. No toco produccion y queda guardado como parte de la
  corrida.
- desplegar-0707.txt con exit cero.
- checklist-0709.txt con exit cero a los dos minutos del
  despliegue, con cero fallas y cero sin medir. Ese checklist no
  mide ciclos ni configuracion.
- aplicadas-20261013-0707.txt con las migraciones que este
  despliegue aplico. Vivia sin commit en el worktree de despliegue
  y aqui queda copiada al repo, como pide R13-I. La reversa de este
  sello la necesita y la lee de esta misma carpeta.

El checklist de este despliegue pide el JSON de busquedas por
mercado y exige que su moneda sea la del mercado, y su
desplegar imprime la huella del crontab antes y despues y aborta
si cambia sin instalar linea, como pide R13-E.

La reversa vigente tras este despliegue fue la de D.6 hasta que
D.7 la reemplazo. Hoy manda la de D.7, como dice docs/DEPLOY.md.
