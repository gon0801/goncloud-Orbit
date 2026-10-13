# Cierre de D.6

Despliegue del bloque de ajustes de campana ya mergeado, con sello
20261013-0707. Las salidas reales guardadas en esta carpeta traen su
linea exit al final, como pide R13-M.

- ensayo-0707.txt con ENSAYO OK y exit cero. El intento previo de
  ensayo-0705.txt salio con exit dos en simulacion y sin tocar
  produccion. Queda guardado como parte de la corrida.
- desplegar-0707.txt con exit cero.
- checklist-0709.txt con exit cero tras el primer ciclo, con la
  configuracion vigente ya observada.
- aplicadas-20261013-0707.txt con las migraciones que este
  despliegue aplico. Vivia sin commit en el worktree de despliegue
  y aqui queda copiada al repo, como pide R13-I. La reversa de este
  sello la necesita y la lee de esta misma carpeta.

La reversa vigente tras este despliegue fue la de D.6 hasta que
D.7 la reemplazo. Hoy manda la de D.7, como dice docs/DEPLOY.md.
