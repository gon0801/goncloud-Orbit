# D.1 — verificacion del redeploy 2026-09-25 23:53-23:54 UTC (master ee50332)

Fecha de verificacion: 2026-09-26 ~00:40 UTC. Verificador: ingenieria (subagente).
Metodo: solo lectura (`SELECT`, `md5sum`, `curl` GET, `ls`). Sin escrituras en prod.

## Lo verificado OK

1. Backup previo: `-rw------- 1 root root 420948 Sep 25 23:54
   /mnt/data/appdata/orbit/backups/pre0044_schema_20260925-2353.sql`
   (tamano y fecha coinciden con `D.1/deploy.md`). Respaldo de codigo previo
   presente: `/mnt/data/appdata/orbit/predeploy-20260925-2353/` (`app`,
   `Dockerfile`, `pyproject.toml`, `tools`, `uv.lock`).
2. Codigo prod = APROBADO `03faa24` (== codigo de master tip `ee50332`: el
   rango `03faa24..ee50332` trae solo docs — H5/H6/D.1/C.2b + scratch):
   ```text
   5ce5c3be0b068744e55766f53cb66be4  /mnt/data/appdata/orbit/app/apply.py
   4a3ca2e597e78ee047132ede86994ba6  /mnt/data/appdata/orbit/app/apply_cola.py
   a6d08d74d115a58c879f086c8b69398b  /mnt/data/appdata/orbit/app/cycle.py
   ```
   identicos a los blobs `git show 03faa24:app/<f> | md5sum` (verificado este turno).
3. Smoke ahora: `curl http://127.0.0.1:8010/health` -> `{"status":"ok"}`;
   `/cortes` -> 200; `/propuestas` -> 200. `orbit-app-1 Up 43 minutes`
   (reinicio ~23:54 UTC, consistente con el redeploy).
4. Migracion 0044 presente: `to_regclass('public.decision_sin_aplicar')` OK;
   0041/0042 (`ads_ingest_incident`, `ads_campaign_proposal`) presentes.
5. Sombra intacta tras el redeploy: 9 goals en `shadow` (0 en otro modo);
   predicado canonico (runbook H5.3, `H5/paso5.txt:12`) `applied_at > '2026-09-25T06:13:41Z'` = 0; filas encoladas antes y aplicadas despues = 0;
   `ads_campaign_proposal` con `status='open'` = 0 (C.5 pausa manual aun sin objeto).

## Que cambio vs deploy C.5 19:50 UTC (ad79eeb)

Rango `ad79eeb..03faa24`: D.1 `decision_sin_aplicar` (`app/apply.py` +83,
`app/apply_cola.py`, `app/cycle.py` 1 linea, migracion
`0044_decision_sin_aplicar.sql`, tests) + #347 `fix(test)` (sella la ruta de
descarte de C.5 en `SUPERFICIE_ADS_OPTIMIZER`, `cd6e63d`). Migracion aplicada
en este redeploy: solo la 0044 (0041-0043 ya estaban desde las 19:50 UTC).

## Protocolo

Siguio protocolo: APROBADO=`03faa24` (arbol = `7c80eab`, bateria completa
`success` run 36202158897), backup schema previo + respaldo de codigo previo,
md5 de testigos, digest antes/despues (`126332dc...` -> `b8133eca...`), smoke
y reversa lista (`predeploy-20260925-2353/` + `backups/pre0044_schema_...sql`).
Ejecuto el dueno con `!` (23:53 UTC); este turno solo autentica en lectura.
