# D.1: despliegue de la seccion 2 (2026-10-10)

SHA desplegado: `1798affebd3b87f0d653bec5d5d300fe53c0b975` (bateria
completa=success).
Sello: `20261010-2022`.

Salidas (literales, `$HOME` ya es `~`):
- `ensayo-20261010-2021.txt`: exit=0, ENSAYO OK (0060+0063, esquema
  identico tras reversa y re-aplicacion, t|t|t).
- `fraccion-sim-20261010-2021.txt`: exit=0, SIMULACRO OK (nada escrito).
- `fraccion-real-20261010-2021.txt`: exit=0. Config 24: amazon_us
  margen encendido (fraccion 0.9 -> 0.8).
- `desplegar-20261010-2022.txt`: exit=0, LISTO. Preflight limpio
  (0 ciclos running, 0 harvest en vuelo); guardas propias OK (claves
  f|f, procedencias T.1 solo margen_plataforma|goal_campana, fraccion
  0.8); cron sin cambios; aplicadas 0060+0063; md5 OK en los 6
  archivos; app recreada (health ok).
- `checklist-20261010-2023.txt`: exit=3. 0 fallas, 0 sin medir; app
  arriba (health 200, HTML MX/US 200, esquema t|t, claves f|f,
  fraccion 0.8). Faltaba el primer ciclo posterior a la recreacion:
  X.1 lo cubrio al dia siguiente (`X.1/checklist-0903Z.txt`).

Respaldo (en servidor): `predeploy-20261010-2022/` (codigo) y
`backups/pred1_schema_20261010-2022.sql` (esquema).

Reversa: `bash docs/evidencia/bids-02/ejecucion/D.1/rollback.sh 20261010-2022`
(restaura codigo, aplica las reversas 0063/0060 en orden inverso).
