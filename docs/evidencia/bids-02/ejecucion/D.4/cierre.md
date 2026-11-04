# D.4: despliegue de la seccion 5 (2026-10-12)

SHA desplegado: `540a5e53d09387697193d735c6e059d7e789b22a` (squash del
PR 432; bateria completa=success, run 38310300658).
Sello: `20261012-2146`.

Salidas (literales, `$HOME` ya es `~`):
- `ensayo-2146Z.txt`: exit=0, ENSAYO OK. Esquema de prod solo lectura
  (73 tablas); ya aplicadas 0060-0063 y 0067; aplicadas 0064, 0065,
  0066, 0068, 0069 y 0070; las reversas en orden inverso dejan el
  esquema identico al base y reaplicar da el mismo; huellas todo t;
  0 grupos fuera de `fabrica5`.
- `desplegar-2146Z.txt`: exit=0, LISTO. Preflight limpio (0 ciclos
  running, 0 harvest en vuelo); aplicadas las seis migraciones
  pendientes; guardas propias OK; md5 OK en 14 archivos; app
  recreada (digest b76e3d39, health ok); el vigia de 7v salio 0 sin
  impulsos y sin guardar; el crontab de 7b quedo instalado con solo
  la linea prevista de `impulso-vigia` (08:20 UTC).
- `checklist-2149Z.txt`: exit=0. 0 fallas, 0 sin medir; sin
  diferidas (salio 0, no 3: no hay chequeo posterior que pedir). Las
  cinco tablas (`impulso`, `impulso_lectura`, `anuncio_retiro`,
  `anuncio_reposicion`, `anuncio_retiro_intencion`) existen y estan
  vacias; columna `pausado_por` (0068) y el DELETE de `app_admin`
  en `fabrica_lote` (0070) en t; la pantalla responde 200 en MX y
  US; el crontab trae 1 linea `impulso-vigia`; la vista previa
  `impulso/plan` 200 sin escrituras; `PUT /sp/productAds` en
  allowlist. El primer `impulso-vigia` corre solo por cron.

Respaldos (en servidor): `backups/pred4_schema_20261012-2146.sql`
(esquema) y `backups/crontab-gon-pre-d4-20261012-2146.txt`
(crontab previo); el paso 2 dejo respaldo del codigo (sin `.env`).

Reversa: `bash docs/evidencia/bids-02/ejecucion/D.4/rollback.sh 20261012-2146`
(restaura codigo, revierte las migraciones de
`aplicadas-20261012-2146.txt` y restaura el crontab). No se uso.
