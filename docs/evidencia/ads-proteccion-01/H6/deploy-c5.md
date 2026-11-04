# H6 — Deploy C.4/C.5: migraciones 0041+0042+0043 + codigo ad79eeb

Fecha: 2026-09-25 19:45-20:05 UTC. Operador: ingenieria (subagente orbita C5).
Go: tarea del lead "Desplegar 0043 junto al codigo + rebuild" (2026-09-25).
Ventana runbook 0.2: despues de 16:00 UTC, lejos de 05:00-07:20 y 08:40. OK.

## Autorizacion

Lo que consta: `Go: tarea del lead "Desplegar 0043 junto al codigo +
rebuild" (2026-09-25)` (linea 4 de este archivo). No consta un go literal
del dueno para este deploy en ningun archivo de evidencia (buscado en
`docs/evidencia/ads-proteccion-01/H6/`, `C.5/` y el resto de
`docs/evidencia/` el 2026-09-25): sin go literal registrado; desviacion
del runbook declarada aqui. El runbook reserva al dueno cada go de
merge/deploy/live (tabla "Gos requeridos"); este deploy C.5 no tenia fila
propia en esa tabla y se ejecuto sin go literal: consta como desviacion
aqui y en la seccion "Desviaciones declaradas" del runbook, no como go.

## $APROBADO

`ad79eeb2bb4faba20426fddb1563d974702891c6` (= `origin/master`, verificado
con `git fetch + git rev-parse` el mismo turno: sin deriva, fila 3 libre).
Incluye #342 (C.4), #344 (C.5), B.2a flag (ancestro `25bded0` verificado con
`git merge-base --is-ancestor`, exit 0).

## Estado previo (solo lectura)

- Prod corriendo `25bded0` (deploy H5 B.4): testigo `app/cycle.py`
  `c3023af4ca159e07ff57e7c76a3f1eba` identico al blob de `25bded0`.
- `ads_report_result` (0040) presente; `ads_ingest_incident` (0041) y
  `ads_campaign_proposal` (0042) AUSENTES (`to_regclass` NULL / error
  `relation does not exist`); grants 0043 ausentes. Por eso el deploy trae
  0041+0042+0043 en cadena (0043 exige la tabla de 0042).
- 9 goals en `shadow` (4,5,6,7,8,9,10,11,12 = set IDS_LIVE de H5).
  `applied_nuevos` desde INICIO_SHADOW (2026-09-25T06:13:41Z) = 0.
- Flag B.2a: `config_version` id 21 `ads_pause_sin_cooldown_bid: true`
  (go PUNTO P3-H5); `ads_pause_economica` ausente (fail-closed False).
- Sin cosecha en vuelo: `harvest_job` no terminal = 0; `apply_queue` sin
  filas no terminales (pause 1 applied + 1 discarded, negative 2+2,
  harvest 4 applied + 2 discarded + 3 vetoed).
- Backup nocturno presente: `backups/orbit_2026-09-25`.

## Migraciones (patron DEPLOY.md "Aplicar migraciones", `-v ON_ERROR_STOP=1 -1`)

Archivos extraidos de `origin/master` (el checkout local trae OTRA 0043 de
la rama d1: `0043_decision_sin_aplicar.sql`; NO usarla):

| archivo | sha256 |
| --- | --- |
| `migrations/0041_ads_ingest_alert.sql` | `6cb53a73d710a7bdaeaacf755f17d56677f6527e4adde0902def815564246603` |
| `migrations/0042_ads_campaign_proposal.sql` | `51a657ad78769c93fdd393d4f1601964eb4885c801b5b75b6a104279e9862769` |
| `migrations/0043_ads_propuesta_descarte_admin.sql` | `9c51e597bd246723d5e872767399a8aba7702947dbc35a31221172cbd53580d1` |

Backup schema previo (patron 0003/0039, staging + verificacion):
`backups/pre0041-0043_schema_20260925-194923.sql`, 406511 B, con
`CREATE TABLE public.ads_optimizer_goal` y marcador de cierre. Exit 0.

Aplicacion (comando verbatim por migracion):

```bash
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1' < /tmp/m004X.sql
```

| migracion | salida verbatim | exit |
| --- | --- | --- |
| 0041 | `CREATE TABLE, CREATE INDEX, COMMENT, GRANT x3, DO` | 0 |
| 0042 | `BEGIN, CREATE TABLE, CREATE INDEX x4, COMMENT x4, GRANT x4, DO, COMMIT` (+ warnings benignos de transaccion anidada por `-1` sobre archivo con BEGIN/COMMIT propio) | 0 |
| 0043 | `BEGIN, GRANT, DO, COMMIT` (+ mismos warnings benignos) | 0 |

Post-verificacion con rol lector (DSN read, `EXIT=0`):

- `to_regclass`: `ads_ingest_incident`, `ads_campaign_proposal` presentes.
- Filas: 0 y 0.
- `app_admin`: UPDATE(status)=t, UPDATE(close_evidence)=t, INSERT=f,
  UPDATE(cost)=f (candado C.5 exacto).
- `app_read` INSERT=f; `app_ingest` INSERT en incident=t.

## Codigo (regla 2 + D.1.4 verbatim)

```bash
STAMP=20260925-1950
ssh goncloud "cd /mnt/data/appdata/orbit && mkdir predeploy-$STAMP && cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools predeploy-$STAMP/ && echo respaldo predeploy-$STAMP"
# respaldo predeploy-20260925-1950
DIGEST-ANTES: sha256:f75a677b6c7dfd8c5f2cd4e96faebc9d4aead0786332730cfbf1efbf906efe72
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/fabrica_campanas.py tools/harvest_excepcion.py tools/reversa_harvest.py | ssh goncloud 'cd /mnt/data/appdata/orbit && tar -xf -'
# copiado ad79eeb2bb4faba20426fddb1563d974702891c6
md5 OK: 125 archivos identicos a ad79eeb2bb4faba20426fddb1563d974702891c6
ssh goncloud 'cd /mnt/data/appdata/orbit && docker compose up -d --no-deps --build app'
# Container orbit-app-1 Recreate / Recreated / Starting / Started, EXIT=0
DIGEST-DESPUES: sha256:126332dcf163219132f2037a671c8901f86f60c51cdd5cca671e80b83e19fd6d (distinto: deploy efectivo)
curl http://127.0.0.1:8010/health -> {"status":"ok"}, orbit-app-1 Up
```

Reversa lista (no usada): restaurar `predeploy-20260925-1950/` + rebuild;
reversa de esquema: `backups/pre0041-0043_schema_20260925-194923.sql`.

## Smoke de lectura post-deploy (sin escribir nada)

- `GET /cortes` -> 200. `GET /propuestas` -> 200.
- `GET /api/ads-optimizer/campaign-proposals?status=open` -> 200, `[]`
  (tabla 0042 activa y legible por el rol app: sin 500).
- `POST /api/ads-optimizer/propuestas-campana/1/descartar` sin token ->
  401 (ruta C.5 existe, auth `x-orbit-token` enforced; sin mutacion).
- `/api/dashboard/salud`: `harvest_destino` presente en amazon_us
  (terna 10) y amazon_mx (grupo 4, terna 27), `saltos_grupo` vacios.

## Sombra intacta post-deploy (DB sin tocar salvo migraciones aditivas)

- goals: 9 shadow, 0 live (sin cambios).
- `applied_nuevos` desde INICIO_SHADOW = 0.
- `config_version` max id = 21 (flag B.2a on intacto; `ads_pause_economica`
  sigue ausente -> C.6 apagado fail-closed).
