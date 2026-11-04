# Cross-review H5 plan de deploy (revisor independiente)

- **Objeto:** `/tmp/wt-h5/docs/evidencia/ads-proteccion-01/H5/plan-deploy.md`
  (H5 = ADS PROTECCION B.4: deploy de `25bded0` + inicio del shadow
  PAUSE-sin-cooldown). Contrastado contra `docs/runbooks/ads-proteccion-01.md`
  (0.1, 0.2, reglas 1-4, H4, H5, filas 3/6/7/9), `plans/ads-proteccion-01.md`
  (master = `25bded0`), `docs/DEPLOY.md` (Crons, Aplicar migraciones, D.1.0,
  D.1.4, D.3) y `docs/evidencia/ads-proteccion-01/B.2a/flag.md`.
- **Revisor:** claude opus, independiente (no es autor del plan ni del codigo).
- **Fecha:** 2026-09-25 (lecturas de prod entre 05:26 y 05:40 UTC, `ssh goncloud date -u` = `Fri Sep 25 05:26:37 AM UTC 2026`).
- **Metodo:** solo lectura. Repo `/Users/dn/dev/goncloud-Orbit`, arbol
  `/tmp/rv-h5-sha` (`git archive 25bded0`), venv del repo, Postgres local
  (bases desechables `rv_h5_scratch` y `rv_h5_0040`, borradas al final),
  `gh` en modo lectura, y `ssh goncloud` solo con SELECT, `md5sum`, `ls`,
  `tail` y `date`. En prod no se escribio nada.
  Efecto colateral: las corridas de pytest dejaron 8 directorios
  `__pycache__` en `/tmp/rv-h5-sha` (cache de bytecode; `git archive` no los
  trae). No los borre porque el harness pidio confirmacion. Se pueden borrar
  sin riesgo.

Comandos ejecutados (verbatim breve; `PSQL_READ` es el de runbook 0.1):

```bash
# 1. SHA
git merge-base --is-ancestor {beae9ec,93bcdab,f40270d} 25bded0     # los 3 OK
git merge-base --is-ancestor 8cfe7b0 f40270d                        # A.2 (#333) ya esta en f40270d
git diff --quiet f40270d 25bded0 -- app/ migrations/ && echo VACIO  # VACIO
git diff --stat f40270d 25bded0 -- Dockerfile .dockerignore pyproject.toml uv.lock tools/{fabrica_campanas,harvest_excepcion,reversa_harvest}.py  # vacio
git ls-remote origin refs/heads/master        # 25bded042cd7600f4161f25b42b2d22d0b6c783b
gh run list --commit 25bded042cd7...          # 36095114172 push master completed/success
gh run view 36095114172 --json jobs,...       # completa=success, gate=success, rapido/pesada=skipped (por diseno en push)
# 2. Aislamiento B.2 (en /tmp/rv-h5-sha)
PYTHONPATH=. .venv/bin/pytest -p no:cacheprovider -q tests/test_optimizer_goals.py tests/test_cycle_pause_cooldown.py   # 47 passed
PYTHONPATH=. .venv/bin/pytest -p no:cacheprovider -q tests/ -k test_flag_apagado_bloquea_pause_nueva_como_antes_de_b2    # 1 passed
.venv/bin/ruff check --no-cache .                                   # All checks passed!
.venv/bin/ruff format --no-cache --check app/optimizer/goals.py app/ads/reports.py   # 2 files already formatted
# 3. Gap 0040
grep -n "_registrar_resultado\|ads_report_result" app/ads/reports.py   # 253 (SQL), 1724, 1803, 1814, 1826, 1853, 1932, 1949
grep -rn ads_report_result app/ tools/        # solo app/ads/reports.py
ssh goncloud "$PSQL_READ -c \"SELECT to_regclass('public.ads_report_result'), to_regclass('public.harvest_excepcion'), to_regclass('public.precio_goal');\""  # NULL | harvest_excepcion | precio_goal
ssh goncloud "$PSQL_READ -c \"SELECT count(*) FROM pg_class WHERE relname='ads_report_result';\""   # 0
ssh goncloud "$PSQL_READ -c \"SELECT to_regclass('public.spapi_inventario_observation'), ..., to_regclass('public.ads_report_result');\""  # 0033-0039 presentes, 0040 ausente
for f in app/ads/reports.py app/cycle.py app/optimizer/goals.py; do ssh goncloud "md5sum /mnt/data/appdata/orbit/$f"; git show 25bded0:$f | md5sum; git show 8cfe7b0^1:$f | md5sum; done
#   reports.py prod = fd2ebe23... = pre-A.2 (8cfe7b0^1) != 25bded0 (73a7b392...); grep -c ads_report_result en prod = 0
ssh goncloud "$PSQL_READ -c \"SELECT id, source, started_at, finished_at, ok, rows_written FROM ingest_run WHERE source LIKE 'amazon_ads%' ORDER BY id DESC LIMIT 8;\""  # 24-sep ok=t (hoy sana)
python /tmp/rv_h5_repro_0040.py  (repro local; ver Bloqueante 1)
# 4. D.3
ssh goncloud "$PSQL_READ -c \"SELECT id, scope, platform, ad_entity_id, mode, enabled, updated_at FROM ads_optimizer_goal ORDER BY id;\""
ssh goncloud "$PSQL_READ -tA -c \"SELECT string_agg(id::text, ',') FROM ads_optimizer_goal WHERE mode='live';\""   # 6,7,4,5,11,9,10,8,12
ssh goncloud "$PSQL_READ -c \"SELECT count(*) FROM harvest_job WHERE fase NOT IN ('done','failed');\""               # 0
ssh goncloud "$PSQL_READ -c \"SELECT count(*) FROM apply_queue WHERE kind='harvest' AND estado NOT IN ('applied','failed','vetoed','discarded');\""  # 0
ssh goncloud "$PSQL_READ -c \"SELECT kind, modo, estado, count(*) FROM apply_queue WHERE estado NOT IN (...terminales...) GROUP BY 1,2,3;\""  # 0 filas (ningun kind)
ssh goncloud "$PSQL_READ -c \"SELECT ... jobs_grupo1_directo, jobs_con_hermanas, jobs_grupo1_por_ancestro ...\""   # 0 | 0 | 0
# 5/6. Pasos, config, applies, backup
ssh goncloud "$PSQL_READ -c \"SELECT id, label, created_at, settings->'ads_optimizer_mode', settings->'ads_pause_sin_cooldown_bid' FROM config_version ORDER BY id DESC LIMIT 3;\""  # max id 20, escalera "live", flag ausente
ssh goncloud "$PSQL_READ -c \"SELECT tgname, pg_get_triggerdef(...) FROM pg_trigger WHERE tgrelid='ads_optimizer_goal'::regclass ...\""  # goal_scope_campana_real, ads_optimizer_goal_harvest_coherente
ssh goncloud "$PSQL_READ -c \"SELECT d.kind, a.tipo, count(*), max(a.started_at) FROM apply_attempt a JOIN decision d ... 10 days ...\""  # bid 95 (ultimo 24-sep 08:41), pause 1, negative 2, harvest 6
ssh goncloud "$PSQL_READ -c \"SELECT count(*) ... d.kind='bid' ... AND NOT EXISTS (SELECT 1 FROM apply_queue q WHERE q.decision_id=a.decision_id);\""  # 95
ssh goncloud 'ls -la /mnt/data/appdata/orbit/backups | tail -4; df -h /mnt/data | tail -1; tail -3 /mnt/data/appdata/orbit/backup.log'
APROBADO=25bded0; [ "$(git rev-parse origin/master)" = "$APROBADO" ] && echo ok || echo FALLO     # FALLO (falso)
APROBADO=$(git rev-parse 25bded0); [ ... ] && echo ok                                             # ok
# simulacion de quoting de pasos 1/3/4: sh -c "<cmd>" con PSQL/PSQL_READ -> psql local a rv_h5_scratch
```

## Bloqueantes

1. **Desplegar `25bded0` sin la migracion 0040 rompe la ingesta diaria de
   metricas Ads (categoria: datos), y ni el runbook ni el plan autorizan
   aplicar 0040 dentro de H5.**

   - **Codigo (25bded0):** `sync_metrics` hace el INSERT en
     `ads_report_result` sin guarda de existencia (`app/ads/reports.py:253`
     SQL, `_registrar_resultado` :259). En la ruta feliz inserta en :1803
     (rechazados), **:1814 (`pending`, antes de pedir reportes)**, :1853
     (`downloaded`) y :1932 (`written`). En el manejo de fallo inserta en
     `_run_de_fallo_de_api` :1724 (fallos de la fase API) y en :1949 (fase DB).
     Ninguna ruta verifica antes que la tabla exista.
   - **Prod (solo lectura):** `to_regclass('public.ads_report_result')` = NULL
     y `pg_class` = 0 filas. 0033-0039 estan aplicadas; 0040 es la unica
     pendiente. Hoy `app/ads/reports.py` de prod tiene el md5 pre-A.2
     (`fd2ebe23…` = `8cfe7b0^1`) y 0 menciones de la tabla, y la ingesta
     esta sana (`ingest_run` 426/422 del 24-sep, `ok=t`). **El deploy
     introduce la regresion.**
   - **Reproduccion discriminante (local, codigo real de 25bded0):** creé la
     base desechable con solo `ingest_run` (DDL de 0001) y corri
     `PYTHONPATH=/tmp/rv-h5-sha .venv/bin/python -B repro.py` (monkeypatch de
     `reports.evaluar_perfiles` para que devuelva un perfil aceptado, y de
     `reports.solicitar_reporte`; despues `reports.sync_metrics(conn, object(),
     fecha_ini=2026-09-01, fecha_fin=2026-09-24)`). Resultados:
     - Sin la tabla: `UndefinedTable relation "ads_report_result" does not exist`,
       el log `fallo de fase API sin run auditable (sello tambien fallo)` y
       `ingest_run: [(1, 'amazon_ads_reports_v3', None, None)]`. La corrida
       queda ABIERTA y nunca se llega a pedir un reporte.
     - Control positivo (misma base + tabla creada): pasa la fase `pending`
       (4 eventos) y la run queda sellada (`finished_at` puesto, `ok=false`
       por el stub).
   - **Efecto:** desde el 26-sep 07:10/07:20 UTC, cada ingesta de metricas y
     de productos aborta sin escribir y deja una run abierta. Durante los 5
     ciclos de shadow el ciclo decidiria sobre metricas congeladas, asi que la
     comparacion contra el replay B.3 (H5.3) pierde validez. Los datos que se
     dejan de ingerir solo se recuperan mientras sigan dentro de D-31.
   - **Adjudicacion (¿hay camino en runbook o plan?): no hay ninguno.**
     - Runbook, tabla "Gos requeridos", fila Deploy: el alcance de B.4 es
       *"cubre deploy + flip todos-a-shadow + encendido flag, con efecto
       declarado"*. No incluye migracion.
     - Runbook, regla 1: *"el go nombra la operacion y el SHA/PR exacto. Sin
       literal, no hay avance"*. El GO citado no nombra ninguna migracion.
     - Runbook, regla 2: Deploy = DEPLOY.md paso 1 (copia, md5, backup,
       build, `Recreated`) + D.1.0. Ninguno de los dos aplica migraciones.
     - Runbook, H5 pasos 1-4: no tienen paso de migracion.
     - Plan, fila A.2: *"migracion solo si fuente actual insuficiente"*. Fila
       A.4: *"Preparar release de A.2/A.3 … Runbook posterior fija metodo
       seguro"*. Es decir, el release de A.2 (y por lo tanto de 0040) es A.4
       (runbook H4.3), y H4.1 dice *"Sin go, A.3/A.4/C.6 siguen pausadas"*
       (A.3d sigue pendiente).
     - Plan, "Operaciones que requieren go": el deploy necesita *"go
       especifico de deploy"*.
     - DEPLOY.md, "Aplicar migraciones": cada migracion la aplica el lead con
       GO del dueño y con backup de schema (precedente 0003).
     - El brief dice *"SIN migraciones nuevas"*.

     El runbook se escribio con H4 (A.4) antes de H5. Como #333 ya esta
     mergeado antes que #335 y #339, no hay ningun SHA de master que tenga
     B.2 + B.3 + B.2a y no tenga A.2. H5 terminaria liberando A.2 "por
     arrastre", el mismo tipo de exposicion que B.2a resolvio para B.2.
   - **Resolucion (decide el operador, fila 7/9 del runbook):**
     - (a) Un go literal nuevo que nombre "aplicar
       `migrations/0040_ads_report_result.sql` antes del build" (con eso se
       levanta el "SIN migraciones nuevas" solo para 0040) y acepte que A.2
       se libera antes de A.4. El plan tendria que agregar el paso con sus
       literales: backup de schema previo;
       `ssh goncloud "$PSQL -v ON_ERROR_STOP=1 -1" < migrations/0040_ads_report_result.sql`;
       readback `to_regclass` + `has_table_privilege('app_ingest', …,'INSERT')`;
       y rollback (`DROP TABLE ads_report_result`, es una tabla nueva, + la
       reversa de codigo de D.1.4). La ronda 2 revisaria solo ese diff.
     - (b) Posponer H5 hasta despues de A.4, respetando el orden H4 → H5.
     - (c) Desplegar otro SHA sin A.2. Requiere un PR/merge nuevo y choca con
       el "SIN merges".

     Recomiendo (a): 0040 solo agrega objetos (CREATE TABLE, indices,
     triggers, GRANTs), se verifica sola con su bloque `DO` y corre dentro de
     `-1`. Pero exige ese go literal.

## Observaciones (no bloqueantes)

1. **La causa citada en el Bloqueante declarado es imprecisa.** La primera
   escritura que falla es :1814 (`pending`, fase API), asi que el handler que
   corre es `_run_de_fallo_de_api` (:1706-1744, log `fallo de fase API sin
   run auditable`), no el de :1949/:1966-1973. Ademas la ingesta de las 06:45
   (`ingest structure`, `app/ads/structure.py`) no toca la tabla: las
   afectadas son solo las de 07:10 y 07:20. La conclusion del plan no
   cambia.
2. **El valor esperado de `IDS_LIVE` va a provocar una parada falsa.** Con
   el literal del runbook (sin `ORDER BY`), prod devuelve hoy
   `6,7,4,5,11,9,10,8,12`. Lo corri contra prod y lo reproduje en local. El
   plan espera `IDS_LIVE=4,5,6,7,8,9,10,11,12 (si difiere, parar)`. Hay que
   comparar como conjunto {4..12}, o cambiar la cadena esperada. Se arregla
   en una linea.
3. **`APROBADO=25bded0` (SHA corto) contradice D.1.0 paso 1 ("SHA
   completo").** El literal `[ "$(git rev-parse origin/master)" = "$APROBADO" ]`
   imprime `FALLO: origin/master avanzó` aunque master no se haya movido. Lo
   verifique: con el corto da FALLO y con
   `25bded042cd7600f4161f25b42b2d22d0b6c783b` da ok. Falla hacia el lado
   seguro, pero se arregla en una linea.
4. **La verificacion de cero applies no ve los bids.** El paso 5 y la parada
   H5.3(c) solo cuentan `apply_queue.applied_at`, pero los bids se aplican
   dentro del ciclo sin pasar por la cola (sellado 1 de
   `app/apply_cola.py`). En prod hubo 95 `apply_attempt` de bid en 10 dias
   sin fila en `apply_queue` (el ultimo el 24-sep a las 08:41). Propongo
   agregar al paso 5 y a la evidencia de cada ciclo:
   `SELECT count(*) FROM apply_attempt WHERE tipo='normal' AND started_at > '$INICIO_SHADOW';`
   (esperado 0). El hueco viene del runbook, asi que tambien va una fila de
   correccion al runbook.
5. **El literal de backup de D.1.0 paso 5 no muestra el backup de hoy.**
   `ls -la backups | tail -4` ordena alfabeticamente y deja fuera
   `orbit_2026-09-25` (salen `schema-*`, `sku_*`, `ui-*`, `v_tacos-*`). Hay
   que usar `tail -1 /mnt/data/appdata/orbit/backup.log` o
   `ls -d backups/orbit_$(date -u +%F)`. Hoy si esta:
   `2026-09-25T03:30:03+00:00 backup OK: …/orbit_2026-09-25`.
6. **La precondicion de D.3 no trae literal.** Si se reusa D.1.0 paso 2,
   usa `$PSQL` (superusuario) para una lectura y
   `fase IN ('pending','negative_created','exact_created')`, que omite
   `hermanas_negadas` (en vuelo desde 0038). Hay que poner los dos conteos
   con `$PSQL_READ` y `fase NOT IN ('done','failed')`, que es exactamente lo
   que corri.
7. **La regla 2 pide "digest distinto", pero D.1.4 no tiene comando para
   capturarlo.** Agregar
   `ssh goncloud 'docker inspect orbit-app-1 --format "{{.Image}}"'` antes y
   despues del build, y guardar la linea `Recreated` de compose en
   `paso2.txt`.
8. **Rollback de codigo.** Ademas de la regla 3 (revert + PR + CI + go, que
   es lento), conviene citar la reversa inmediata de D.1.4 (restaurar
   `predeploy-$STAMP/` y reconstruir, con go) para una emergencia como la
   del Bloqueante 1. Si se aplica 0040, falta escribir su rollback.
9. **El estado de D.3 se puede precisar.** D.3 esta en su paso 2 (esperando
   el primer harvest natural del grupo 1): hay 0 jobs del grupo y 0 jobs con
   `hermanas_objetivo`. "Go 2 pendiente" es cierto, pero hoy no tiene sobre
   que actuar. El shadow congela el paso 2 de D.3 durante al menos 5 ciclos,
   y eso es lo que significa "sombreamiento coordinado".
   Ademas, el ciclo de hoy (08:40 UTC) corre con el grupo 1 en live antes de
   que abra la ventana. Puede dejar un harvest `live` en `pending_veto`
   (`harvest_job` recien nace al liberar, sellado 13). El plan ya obliga a
   repetir los conteos en ventana y a parar si dan >0; bien.
10. **La cita del GO esta recortada con "[...]" y no muestra SHA ni PR**
    (regla 1). Como el Bloqueante 1 pide un go nuevo de todas formas, ese go
    deberia nombrar el SHA completo, el paso de 0040 y la modalidad D.3
    ("sombreamiento coordinado; D.3 paso 2 congelado durante el shadow").
11. **Cuidar la etiqueta de `config_version`.** En prod, `config_version`
    id 20 tiene la etiqueta literal `D.0: <tu go literal>`: alguien dejo el
    placeholder sin llenar y la tabla es append-only, asi que quedo asi para
    siempre. El paso 4 del plan si llena la cita
    (`'B.2a flag on (H5, go PUNTO P3-H5 2026-09-25)'`). Revisar la etiqueta
    antes de ejecutar. El `RETURNING id` esperado hoy es 21.

## Lo verificado OK

- **(1) SHA.** `beae9ec`, `93bcdab` y `f40270d` son ancestros de `25bded0`,
  y A.2 (`8cfe7b0`) ya esta en `f40270d`. `git diff f40270d 25bded0 -- app/
  migrations/` sale vacio. Tampoco hay diff en
  Dockerfile/.dockerignore/pyproject/uv.lock ni en los 3 tools de D.1.4. Lo
  que hay encima es: docs de evidencia, `plans/`, `tools/replay_ads_economico.py`
  + su test (no entra a la imagen: el `Dockerfile` solo hace COPY de `app` y
  `tools/fabrica_campanas.py`) y `.github/` (#343). CI push `36095114172`
  sobre `25bded042cd7…`: `completa` (bateria entera) success, `gate`
  success; `rapido`/`pesada` quedan skipped en push por diseño del
  workflow. `origin/master` sigue en `25bded0` (ls-remote).
- **(2) Aislamiento B.2.** 47 passed; el test nominal
  `test_flag_apagado_bloquea_pause_nueva_como_antes_de_b2`
  (`tests/test_cycle_pause_cooldown.py:112`) existe y pasa, y discrimina:
  con `flag=False` da `pendientes == []`, `skips_entidad == {"cooldown_7d": 1}`
  y consulta sin kind. Su gemelo con `flag=True` propone la PAUSE.
  `ruff check` limpio; `ruff format --check` limpio en los 2 archivos. La
  lectura del flag es fail-closed (`settings.get(...) is True`).
- **(3) Gap 0040.** Confirmado en codigo, en prod y con repro local
  (Bloqueante 1). El plan lo declaro con honestidad y no se lo salto.
- **(4) D.3.** Confirmado a las 05:27 UTC: 9 goals (4-12) `live`; los goals
  8-12 tienen `updated_at 2026-09-24 04:03:55`, que es el reencendido.
  `harvest_job` no terminal = 0 (el ultimo es el id 4, `done`, del 20-sep).
  `apply_queue` harvest no terminal = 0, y de hecho no hay ninguna fila no
  terminal de ningun kind. No hay job del grupo 1. D.3 sigue en curso (con
  la precision de la Observacion 9).
- **(5) Pasos 1-6 contra el runbook.**
  - Los pasos 1 y 3 copian verbatim H5.1/H5.2: disciplina de IDs, flip
    `WHERE id IN ($IDS_LIVE)`, prohibicion explicita de
    `WHERE mode='shadow'`, `inicio.txt` + `source`.
  - El paso 4 es el literal de `flag.md`: append-only, `settings || …`,
    readback, y repetir el readback despues de cada cambio de config.
  - Simulacion de quoting (`sh -c` + psql local): el flip actualiza solo los
    9 IDs y deja intacto un goal que ya estaba en shadow. El INSERT del flag
    conserva los settings previos y agrega `"ads_pause_sin_cooldown_bid": true`.
    El readback devuelve `true`. Con `IDS_LIVE` vacio, el UPDATE falla con
    `syntax error` y no escribe (falla hacia el lado seguro).
  - La app lee la config como `ORDER BY id DESC LIMIT 1` (`app/cycle.py:388`,
    `app/apply.py:254`), consistente con el INSERT.
  - Triggers de `ads_optimizer_goal`: `harvest_coherente` +
    `scope_campana_real`. Los goals 8-12 estan en estado bid-solo y el mismo
    tipo de UPDATE de `mode` ya paso el 24-sep 04:03.
  - La ventana 0.2 esta declarada verbatim. Precondicion de fila 3,
    rollback por regla 3 y fila 6: presentes.
  - No encontre comandos inseguros. Todas las escrituras van con `$PSQL`,
    acotadas por IDs o append-only, y el paso 2 va encadenado con `FALLO`.
- **(6) Cero applies y acuerdo D.3.**
  - El GO citado dice literalmente "efecto cero-applies" y "acuerdo
    FABRICA 02 D.3 si sigue en curso". Condiciona la ejecucion a "D.3 sin
    cosecha en vuelo" y ordena el flip de todas las metas live, lo que solo
    es compatible con el sombreamiento coordinado (no con esperar el go 2).
    En sustancia cubre el "el go nombra el acuerdo con D.3" de H5; la
    modalidad deberia quedar explicita en el go nuevo (Observacion 10).
  - El efecto cero-applies se sostiene en el codigo: la liberacion vuelve a
    resolver el modo por decision y descarta con `modo_no_live` cualquier
    fila `live` que haya quedado encolada antes del flip
    (`app/apply_cola.py:894-903`). Las filas shadow nunca se seleccionan.
    La unica brecha esta en la medicion (Observacion 4), no en el efecto.
- Backup nocturno del 25-sep OK (03:30 UTC) y disco al 53%.

VEREDICTO: NO APROBADO
