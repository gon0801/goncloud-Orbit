# D.5: despliegue de la seccion 6 (2026-10-13)

SHA desplegado: `6a154bdf4478facc3da9df88e6a5e07fb3e7fa9f` (bateria
completa=success, run 38327612174).
Sello: `20261013-0051`.

Salidas (literales, `$HOME` ya es `~`):
- `ensayo-0046Z.txt`: exit=0, ENSAYO OK. Esquema de prod en solo
  lectura; 0060 a 0070 ya aplicadas, 0 pendientes; guardas propias
  presentes; `lee_busquedas` contra el esquema de prod en MX y US
  con listas vacias.
- `desplegar-0046Z.txt`: exit=1, frenado por la guarda del preflight
  ("ABORTA: hay app.cli corriendo") tras pasar el paso 0 (SHA y CI
  en success). No toco codigo, base ni crontab. El intento anterior
  (`desplegar-0021Z`, en las salidas del loop) freno igual en el
  paso 0 porque la bateria de la punta aun corria: los dos intentos
  frenados fueron las guardas haciendo su trabajo.
- `desplegar-0051Z.txt`: exit=0, LISTO. Primer reintento, 5 minutos
  despues. Preflight limpio; guardas propias OK; backup de esquema
  `pred5_schema_20261013-0051.sql` (550043 bytes); md5 OK en 5
  archivos; app recreada (digest 188b4657, health ok). Sin
  migraciones aplicadas (D.5 no trae) y sin instalar cron.
- `checklist-0057Z.txt`: exit=0. 0 fallas, 0 sin medir; sin
  diferidas (salio 0, no 3: no hay chequeo posterior que pedir).
  `/busquedas-sin-venta` 200 en MX y US con su frase de referencia
  (con porcentajes); `/productos-sin-clics` 200 en MX y US;
  `search_term_observation` sin escrituras (164152 antes y despues).

D.5 no aplica migraciones ni instala linea de cron. El crontab
quedo igual: `crontab -u gon -l` con md5
`7c5a5fbcf3cef38dc5af843bcc635eab` y 60 lineas antes y despues (los
crontab completos no se copiaron al repo: basta la huella).

Respaldos (en servidor): `predeploy-20261013-0051/` (codigo + SHA)
y `backups/pred5_schema_20261013-0051.sql` (esquema).

Reversa: `bash docs/evidencia/bids-02/ejecucion/D.5/rollback.sh 20261013-0051`
(restaura solo el codigo, reconstruye y comprueba health). No se uso.
