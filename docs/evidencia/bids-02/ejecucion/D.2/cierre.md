# D.2: despliegue de la seccion 3 (2026-10-11)

SHA desplegado: `d62544fdb005b1fcdcb73d0f9a2ffb9dca2cb366` (PR #425 squash-merged,
completa=success run 38108887879).
Sello: `20261011-0351`.

Salidas (literales, `$HOME` ya es `~`):
- `ensayo-20261011-0351.txt`: exit=0, ENSAYO OK (huellas 0061/0062 t/t).
- `desplegar-20261011-0351.txt`: exit=0, LISTO. Aplico 0061+0062 (0060 y 0063
  ya estaban); instalo las 2 lineas de cron (--placements tras --productos,
  avisos-campana suelta al final).
- `checklist-20261011-0351.txt`: exit=3. 0 fallas, 0 sin medir; app arriba
  (health 200, HTML MX/US 200, esquema t|t|t). Falta el primer sync de las
  06:45 UTC y el primer reporte de las 07:25 UTC: el despliegue esta hecho,
  lo pendiente se comprueba en el chequeo posterior.

Respaldo (en servidor): `backups/crontab-gon-pre-d2-20261011-0351.txt`,
`backups/pred2_schema_20261011-0351.sql` y `predeploy-20261011-0351/`.

Reversa: `bash docs/evidencia/bids-02/ejecucion/D.2/rollback.sh 20261011-0351`
(quita solo las 2 lineas D.2 del crontab actual, restaura codigo, aplica
las reversas 0062/0061 en orden inverso).

Chequeo posterior: `bash docs/evidencia/bids-02/ejecucion/D.2/checklist.sh
20261011-0351` despues de las 07:40 UTC (tras sync 06:45 + reporte 07:25).
