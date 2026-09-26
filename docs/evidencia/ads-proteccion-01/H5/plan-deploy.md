# H5 — Plan de deploy B.4 + inicio shadow (PUNTO P3-H5, 2026-09-25 UTC)

GO citado (brief del punto, mensaje literal del operador):

> PUNTO P3-H5 (OPENCLAW_WATCH=1 activo) — deploy B.4 + inicio shadow H5,
> operacion GO segun brief 4 ordenes (efecto cero-applies + acuerdo
> FABRICA 02 D.3 si sigue en curso). SIN merges de PRs, SIN ready de PRs,
> SIN migraciones nuevas. [...] FASE 3, SOLO si opus APROBADO y D.3 sin
> cosecha en vuelo: ejecuta flip de metas live a shadow + flag + inicio.txt
> H5 segun runbook H5 pasos 1-6, con comando verbatim + salida verbatim +
> exit code de cada paso.

Efecto declarado: cero applies en todo Ads durante 5 dias (todos los goals
a `shadow`: bids, negativos y harvest pausados). Acuerdo D.3: el brief lo
cubre ("acuerdo FABRICA 02 D.3 si sigue en curso"); D.3 sigue en curso
(grupo 1 live, go 2 pendiente) y el sombreamiento es coordinado via este
mismo GO — ver "Estado D.3" abajo.

## SHA de deploy (regla 2)

`$APROBADO = 25bded042cd7600f4161f25b42b2d22d0b6c783b` (`origin/master`;
SHA completo por D.1.0 paso 1 — obs 3 de ronda 1: el corto rompe el
comparador de fila 3).

Por que:

- Contiene B.2 (PR #332, `beae9ec`), B.3 (PR #335, `93bcdab`) y el flag
  B.2a (PR #339, `f40270d`) — verificado con `git merge-base --is-ancestor`
  de los tres merges contra `25bded0`.
- `git diff f40270d 25bded0 -- app/ migrations/` vacio: el codigo y esquema
  desplegable son identicos a los del merge B.2a. Encima solo hay docs
  (#338 evidencia), tools C.2 (`tools/replay_ads_economico.py`, no entra a
  la imagen), tests y CI (#343).
- CI push `36095114172` sobre `25bded0`: `completed/success`.
- Aislamiento B.2 re-verificado sobre este SHA (2026-09-25, `git archive`
  a /tmp + `.venv` del repo): `test_optimizer_goals.py` +
  `test_cycle_pause_cooldown.py` = 47 passed (incluye
  `test_flag_apagado_bloquea_pause_nueva_como_antes_de_b2`, 1 passed
  nominal); `ruff check` limpio; `ruff format --check` de
  `app/optimizer/goals.py` y `app/ads/reports.py` limpio.

## Estado D.3 (medido 2026-09-25 ~05:20 UTC, solo lectura, rol lector)

- Grupo 1 (`kit_arras`, goals 8-12): `live` efectivo desde el reencendido
  del 2026-09-24 04:03 UTC (go 1 reutilizado). Los 9 goals (4-12) en `live`.
- Cosecha en vuelo: NO. `harvest_job` con fase no terminal = 0;
  `apply_queue` kind `harvest` con estado no terminal = 0. Ultimo job: id 4
  `done` del 2026-09-20 (anterior al reencendido; el cron MX del 24-sep no
  produjo job).
- Go 2 (reversa del primer harvest): pendiente. D.3 SIGUE EN CURSO.

## Precondiciones (verificar en ventana, antes del paso 1)

- Ventana de deploy (runbook 0.2): 09:30-15:00 UTC o despues de 16:00 UTC,
  excepto 13:00-13:20 UTC; lejos de 05:00-07:20 y 08:40. Las escrituras de
  este plan SOLO corren en ventana.
- Backup nocturno presente (regla 2 / D.1.0).

## Excepcion de ventana (declarada a posteriori)

La 0040 (~06:05 UTC) y el deploy de `paso2.txt` (06:13-06:15 UTC) cayeron
dentro de la franja 05:00-07:20 UTC que las precondiciones excluyen. La
dispensa del operador consta en:

- `H5/paso1.txt:2-4`:
  `# NOTA: ventana de deploy 0.2 ELIMINADA por orden literal del operador`
  `# ("manda a la verga esa puta ventana ... jamas pares por una puta ventana",`
  `# 2026-09-25 ~06:10 UTC). Ejecucion fuera de la ex-ventana por orden expresa.`
- `H5/paso2.txt:2`:
  `# Fuera de ex-ventana por orden literal del operador (ver paso1.txt).`
- `origin/master` sigue en `25bded0` (fila 3: si avanzo, no desplegar;
  revalidar + go nuevo).
- D.3 sin cosecha en vuelo (repetir los dos conteos; si >0, parar).

## 0040 APLICADA EN PROD (ronda 2, 2026-09-25 ~06:05 UTC, ingeniero de turno)

Comando verbatim (patron DEPLOY.md Aplicar migraciones, una transaccion):

```bash
ssh goncloud "$PSQL -v ON_ERROR_STOP=1 -1" < migrations/0040_ads_report_result.sql
```

Salida verbatim: `CREATE TABLE`, `CREATE INDEX` x2, `CREATE TRIGGER` x2,
`COMMENT`, `GRANT` x3, `DO`. Exit code: 0.

Archivo aplicado: `migrations/0040_ads_report_result.sql`, sha256
`ae2970ef9c3e9a6e126e0b14aed49c7cf89f1704686931803dd456d665084b15`,
identico a `origin/master` y al merge #333 (verificado en lectura este
turno: sha256 del blob de master = mismo valor).

Post-verificacion del turno (lectura) + re-verificacion FASE 1 ronda 2
(2026-09-25 ~06:10 UTC, rol lector): `to_regclass` presente; 3 indices
(pkey + run_idx + salud_idx); 2 triggers append-only; 0 filas;
privilegios SELECT app_read=t, INSERT app_ingest=t, UPDATE=f, DELETE=f.

Test focalizado del turno sobre arbol `25bded0` (/tmp/rv-h5-sha, `.venv`
del repo, Postgres local): `pytest tests/test_reports_pipeline.py -k
en_vivo` = 2 passed (metricas + search_terms), 25 deselected, exit 0
(log /tmp/mig0040/focal.log).

SHA revalidado este turno: `git ls-remote origin refs/heads/master` =
`25bded042cd7600f4161f25b42b2d22d0b6c783b` (sin movimiento; fila 3 en
verde). Con 0040 aplicada, el Bloqueante 1 de ronda 1 queda RESUELTO (la
ingesta A.2 tiene su tabla antes del build).

## BLOQUEANTE DECLARADO RONDA 1 (RESUELTO — ver seccion 0040 arriba)

La migracion `0040_ads_report_result.sql` (A.2, PR #333, en master) NO esta
aplicada en prod (verificado 2026-09-25 por `to_regclass` e
`information_schema`: `ads_report_result` ausente; el metodo si detecta
`harvest_excepcion` y `precio_goal`). El codigo A.2 en `25bded0` llama
`_registrar_resultado` (INSERT a esa tabla, sin guarda de existencia) en el
camino principal de `sync_metrics` (`app/ads/reports.py:1814` y manejo de
fallo `:1949`): sin la tabla, cada ingesta revienta con UndefinedTable, el
sello best-effort tambien falla y la run queda ABIERTA (`:1966-1973`).
Desplegar `25bded0` sin 0040 rompe las ingestas 06:45/07:10/07:20.

El runbook H5 no trae paso de migracion y el brief prohibe migraciones
nuevas. Este plan NO incluye aplicar 0040. Pregunta al review: ¿BLOQUEANTE
para el deploy (NO APROBADO hasta go + paso de migracion), u otro camino?

## Pasos de ejecucion (solo si review APROBADO + precondiciones en verde)

Evidencia de la condicion: ronda 1 NO APROBADO (`H5/review-opus.md`,
bloqueante 0040) + ronda 2 APROBADO (`H5/review-opus-r2.md`, bloqueante
levantado con 0040 aplicada). La ronda 2 vivia en
`.saikit/scratch/ads-proteccion-1/` y se movio aqui sin cambiar su contenido.

Definir en cada terminal (runbook 0.1):

```bash
PSQL='docker exec -i orbit-db-1 psql -U orbit -d orbit -X -P pager=off'
PSQL_READ='docker exec -i orbit-db-1 psql "$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)" -X -P pager=off'
APROBADO=25bded042cd7600f4161f25b42b2d22d0b6c783b
```

### Paso 1 — Pre H5.1: inicio del shadow e IDs live

```bash
INICIO_SHADOW=$(ssh goncloud date -u +%Y-%m-%dT%H:%M:%SZ); echo $INICIO_SHADOW
IDS_LIVE=$(ssh goncloud "$PSQL_READ -tA -c \"SELECT string_agg(id::text, ',') FROM ads_optimizer_goal WHERE mode='live';\""); echo $IDS_LIVE
printf 'INICIO_SHADOW=%s\nIDS_LIVE=%s\n' "$INICIO_SHADOW" "$IDS_LIVE" > docs/evidencia/ads-proteccion-01/H5/inicio.txt
source docs/evidencia/ads-proteccion-01/H5/inicio.txt
```

Esperado: el CONJUNTO {4,5,6,7,8,9,10,11,12} (9 goals live medidos en
FASE 1; el orden de `string_agg` sin ORDER BY no es estable — obs 2 de
ronda 1: prod devuelve `6,7,4,5,11,9,10,8,12`). Comparar ordenando ambos
lados; si el conjunto difiere, parar y explicar. Guardar salida en
`H5/paso1.txt`.

### Paso 2 — Deploy regla 2 (DEPLOY.md D.1.4 adaptado, `$APROBADO`)

Conjunto de archivos: `app/`, `Dockerfile`, `.dockerignore`,
`pyproject.toml`, `uv.lock` + `tools/fabrica_campanas.py`,
`tools/harvest_excepcion.py`, `tools/reversa_harvest.py` (mismo conjunto
que D.1.4; el md5 lo cubre igual). Comandos literales de DEPLOY.md
D.1.4 (STAMP, respaldo `predeploy-$STAMP`, `git archive $APROBADO`,
comparacion md5 local-vs-server, `docker compose up -d --no-deps --build
app`, `Recreated` + digest distinto, health `/health` + `orbit-app-1 Up`).
Encadenados con `&&` / `|| { echo FALLO...; false; }`; ante FALLO no se
sigue. Guardar salida en `H5/paso2.txt`.

### Paso 3 — Flip de los IDs live a shadow (H5.2, acotado a IDs)

```bash
ssh goncloud "$PSQL_READ -c \"SELECT id, scope, mode, enabled FROM ads_optimizer_goal ORDER BY id;\""
ssh goncloud "$PSQL -c \"UPDATE ads_optimizer_goal SET mode='shadow', updated_at=now() WHERE id IN ($IDS_LIVE);\""
ssh goncloud "$PSQL_READ -c \"SELECT mode, count(*) FROM ads_optimizer_goal GROUP BY mode;\""
```

Esperado: antes 9 live; despues 9 shadow, 0 live. Guardar antes/despues en
`H5/paso3.txt`. Jamas `WHERE mode='shadow'` en el flip de vuelta (H5.1).

### Paso 4 — Encendido del flag B.2a (literal registrado, H5.2 caso a)

Literal de `docs/evidencia/ads-proteccion-01/B.2a/flag.md` con la cita del
GO de este plan:

```bash
ssh goncloud "$PSQL -c \"INSERT INTO config_version (label, settings)
SELECT 'B.2a flag on (H5, go PUNTO P3-H5 2026-09-25)',
       settings || '{\\\"ads_pause_sin_cooldown_bid\\\": true}'::jsonb
FROM config_version ORDER BY id DESC LIMIT 1 RETURNING id;\""
ssh goncloud "$PSQL_READ -c \"SELECT id, label, settings->'ads_pause_sin_cooldown_bid' AS flag
FROM config_version ORDER BY id DESC LIMIT 1;\""
```

Esperado: `RETURNING id` = max+1; readback `flag = true`. Guardar en
`H5/paso4.txt`. Repetir el readback tras cualquier cambio de config
posterior (flag.md).

### Paso 5 — Verificacion post (lectura + smoke)

```bash
ssh goncloud 'curl -fsS http://127.0.0.1:8010/health'
ssh goncloud "$PSQL_READ -c \"SELECT mode, count(*) FROM ads_optimizer_goal GROUP BY mode;\""
ssh goncloud "$PSQL_READ -c \"SELECT count(*) AS applied_nuevos FROM apply_queue
  WHERE applied_at > '$INICIO_SHADOW';\""
ssh goncloud 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8010/cortes'
```

Esperado: health ok; 9 shadow / 0 live; applied_nuevos = 0; /cortes 200.
Guardar en `H5/paso5.txt`.

### Paso 6 — Evidencia y registro

`H5/inicio.txt` + `H5/paso1.txt`...`H5/paso5.txt` (comando, salida, fecha
UTC, SHA — regla 4) commiteados a esta rama; AppFlowy EHV Tasks H5 a
`In progress` (shadow 5 ciclos en curso); el go de live y el flip de
vuelta (H5.4) quedan para operacion separada con su propio go.

## Rollback

- Codigo: regla 3 (revert del merge en rama `revert/<hito>`, PR + CI + go
  de merge, deploy regla 2, 1 ciclo observado).
- Flag: literal de apagado de flag.md (fila nueva con false, con go) +
  readback false + 1 ciclo sin PAUSE-nueva.
- Datos: solo el backup previo al deploy (regla 3).
- Parada en shadow (fila 6): congelar live; diagnosticar contra replay
  B.3; reanudar o revertir solo con go nuevo.
