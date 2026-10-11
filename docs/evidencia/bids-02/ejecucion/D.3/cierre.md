# D.3: despliegue de la seccion 4 (2026-10-11)

SHA desplegado: `a6bf7d5c5fa0a585090a8d00666c395574478754` (bateria
completa=success, run 38136136472).
Sello: `20261011-1147`.

Salidas (literales, `$HOME` ya es `~`):
- `ensayo-1147Z.txt`: exit=0, ENSAYO OK (0067 aplicada, reversa
  deja el esquema identico, reaplicar igual, huella 0067 t).
- `desplegar-1147Z.txt`: exit=0, LISTO. Preflight limpio (0 ciclos
  running, 0 harvest en vuelo); 0060/0061/0062/0063 ya aplicadas,
  aplicada solo la 0067; cron intacto (D.3 no lo cambia); md5 OK en
  los 7 archivos; app recreada (digest bb74e7a2, health ok).
- `checklist-1148Z.txt`: exit=0. 0 fallas, 0 sin medir; sin
  diferidas. Tabla `campana_ajuste` existe y vacia (0); vista
  previa `campana-ajuste/plan` 200 sin escrituras; pantalla de
  dinero 200 en MX (34 botones por clase) y US (9 por clase);
  `PUT /sp/campaigns` en allowlist.

Respaldo (en servidor): `predeploy-20261011-1147/` (codigo + SHA)
y `backups/pred3_schema_20261011-1147.sql` (esquema).

Reversa: `bash docs/evidencia/bids-02/ejecucion/D.3/rollback.sh 20261011-1147`
(restaura codigo, aplica la reversa 0067).
