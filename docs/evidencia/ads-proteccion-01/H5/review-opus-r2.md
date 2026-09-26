# Cross-review H5 ronda 2 (revisor independiente)

- **Objeto:** el diff `67d2c06..0223c6f` de
  `docs/evidencia/ads-proteccion-01/H5/plan-deploy.md` (rama
  `evidencia/ads-proteccion-h5`, worktree `/tmp/wt-h5`). Es la respuesta al
  Bloqueante 1 de la ronda 1 (0040 ausente en prod) y a las obs 2 y 3 de R1.
  Contexto: `H5/review-opus.md` (R1, NO APROBADO) y el go del operador
  `/tmp/mig0040/go-h5r2.txt` ("GO P3-H5 RONDA 2").
- **Revisor:** claude opus, independiente (no es autor del plan, ni del
  codigo, ni de la aplicacion de 0040).
- **Fecha:** 2026-09-25. Lecturas de prod entre 05:47:06 y 05:51:51 UTC
  (`ssh goncloud date -u` al inicio y al final; `now()` de la DB = 05:47:34
  y 05:49:43 UTC en las consultas).
- **Metodo:** solo lectura en prod. Todo el SQL corrio como `orbit_read`
  (`$PSQL_READ` del runbook 0.1) dentro de `BEGIN READ ONLY; ... ROLLBACK;`,
  mas `curl /health` y `date`. **En prod no se escribio nada.** El repo
  `/Users/dn/dev/goncloud-Orbit` se uso solo para leer (`git show`,
  `git grep`, `git rev-parse`, `git ls-remote`, `git archive | tar -t`; no hubo
  fetch). `gh` solo en lectura. Arbol `/tmp/rv-h5-sha` sin cambios de mi
  parte: corri python con `-B`, no aparecieron `__pycache__` nuevos desde R1
  y ningun archivo nuevo salvo `.claude/state|sessions`, que es estado del
  harness de sesion y ya existia desde las 05:35 UTC. Postgres local: base
  desechable `rv_h5_r2`, borrada al final (0 bases `rv_h5*` y
  `orbit_reports_test*` colgadas).

Comandos ejecutados (verbatim breve; `PSQL_READ` es el literal del runbook
0.1 y `PSQL_READ -v ON_ERROR_STOP=1 <<SQL BEGIN READ ONLY; ...; ROLLBACK; SQL`
envuelve todo el SQL de prod):

```bash
# Contexto
cat H5/review-opus.md; git -C /tmp/wt-h5 diff 67d2c06 0223c6f -- .../plan-deploy.md
git -C /tmp/wt-h5 log -2 --format='%h %aI %cI'   # 0223c6f 2026-09-24T22:45:15-07:00 (= 05:45:15 UTC)
cat /tmp/mig0040/go-h5r2.txt /tmp/mig0040/focal.log; stat -f '%Sm %N' /tmp/mig0040/*   # mtimes 22:41-22:44 PDT
ssh goncloud date -u                                  # Fri Sep 25 05:47:06 AM UTC 2026
# 1. sha256 de 0040
shasum -a 256 /tmp/rv-h5-sha/migrations/0040_ads_report_result.sql /tmp/mig0040/0040_ads_report_result.sql
for r in 25bded0 origin/master 8cfe7b0; do git show $r:migrations/0040_ads_report_result.sql | shasum -a 256; done
#   los 5 = ae2970ef9c3e9a6e126e0b14aed49c7cf89f1704686931803dd456d665084b15
# 1. 0040 en prod (lectura)
SELECT current_user, now();                                              # orbit_read | 05:47:34
SELECT to_regclass('public.ads_report_result'), (SELECT count(*) FROM ads_report_result);   # ads_report_result | 0
SELECT relname, owner, relacl FROM pg_class WHERE relname IN ('ads_report_result','ads_report_result_id_seq','ingest_run');
SELECT indexname, indexdef FROM pg_indexes WHERE tablename='ads_report_result';            # 3
SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid='ads_report_result'::regclass AND NOT tgisinternal;  # 2
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='ads_report_result'::regclass;             # 5
SELECT ... FROM information_schema.columns WHERE table_name='ads_report_result';            # 9 columnas
SELECT r, has_table_privilege(r,'ads_report_result','SELECT'|'INSERT'|'UPDATE'|'DELETE'|'TRUNCATE'),
       has_sequence_privilege(r,'ads_report_result_id_seq','USAGE') FROM unnest(ARRAY['app_read','app_ingest','app_decide','app_admin']) r;
SELECT g.rolname, string_agg(u.rolname...) FROM pg_auth_members ...   # app_ingest = orbit_ingest(login), orbit_test(login)
SELECT current_setting('track_commit_timestamp'), xmin FROM pg_class WHERE relname='ads_report_result';   # off | 295391
# 1. equivalencia catalogo prod vs 0040 verbatim (misma consulta en los dos lados)
ssh goncloud "$PSQL_READ -v ON_ERROR_STOP=1 -At" < /tmp/rv_h5_r2_catalogo.sql > /tmp/rv_h5_r2_cat_prod.txt
psql .../rv_h5_r2 -At < /tmp/rv_h5_r2_catalogo.sql > /tmp/rv_h5_r2_cat_local.txt
diff /tmp/rv_h5_r2_cat_prod.txt /tmp/rv_h5_r2_cat_local.txt && echo IDENTICO   # IDENTICO (26 lineas c/u)
# 2. Bloqueante 1 levantado
cd /tmp/rv-h5-sha && PYTHONPATH=. .venv/bin/python -B -m pytest -p no:cacheprovider -q tests/test_reports_pipeline.py -k en_vivo   # 2 passed, 25 deselected, EXIT:0
psql .../postgres -c "CREATE DATABASE rv_h5_r2"; for f in migrations/*.sql (sin reversa, sin 0040); do psql -v ON_ERROR_STOP=1 -1 -f $f; done
#   38 aplicadas; OMITIDA 0011 (migracion de datos: "no existe el producto NH-GAM-NEG-PESETA-PLA"; no toca ingest_run/ads)
#   ACL de ingest_run local = prod
PYTHONPATH=/tmp/rv-h5-sha .venv/bin/python -B /tmp/rv_h5_r2_repro.py      # A) sin 0040, SET ROLE app_ingest
psql .../rv_h5_r2 -X -v ON_ERROR_STOP=1 -1 < migrations/0040_ads_report_result.sql   # misma forma que el plan; EXIT:0
PYTHONPATH=/tmp/rv-h5-sha .venv/bin/python -B /tmp/rv_h5_r2_repro.py      # B) con 0040, SET ROLE app_ingest
git grep -c ads_report_result 8cfe7b0^1 -- app/ tools/                     # 0 (el codigo pre-A.2 no la toca)
git grep -nE "DELETE FROM ingest_run|UPDATE ingest_run SET id" {8cfe7b0^1,25bded0} -- app/ tools/   # 0 y 0
# 3. SHA
git ls-remote origin refs/heads/master     # 25bded042cd7600f4161f25b42b2d22d0b6c783b
APROBADO=25bded042cd7600f4161f25b42b2d22d0b6c783b; [ "$(git rev-parse origin/master)" = "$APROBADO" ] && echo ok   # ok
[ "$(git ls-remote origin refs/heads/master | cut -f1)" = "$APROBADO" ] && echo ok                             # ok
APROBADO=25bded0; [ ... ] || echo FALLO    # FALLO (control: el corto de R1 sigue fallando, como debe)
gh run list --commit 25bded042cd7600f4161f25b42b2d22d0b6c783b   # 36095114172 push master completed success
git archive 25bded042cd7600f4161f25b42b2d22d0b6c783b app | tar -t | wc -l   # 127 (el SHA completo sirve a D.1.4)
# 4. obs-2: literal de paso 1 contra prod + comparacion como conjunto
IDS_LIVE=$(ssh goncloud "$PSQL_READ -tA -c \"SELECT string_agg(id::text, ',') FROM ads_optimizer_goal WHERE mode='live';...\"")   # 6,7,4,5,11,9,10,8,12
[ "$(printf '%s' "$IDS_LIVE" | tr ',' '\n' | sort -n | paste -sd, -)" = "4,5,6,7,8,9,10,11,12" ] && echo ok   # ok
#   controles negativos: con 13 extra -> FALLO; sin 8 -> FALLO
# 5. diff no toca lo aprobado
git diff -U0 67d2c06 0223c6f      # 4 hunks: SHA cabecera, seccion 0040 + titulo bloqueante, APROBADO=, esperado paso 1
diff <(show 67d2c06 | sed -n '/^### Paso 2/,$p') <(show 0223c6f | sed -n '/^### Paso 2/,$p')   # IDENTICOS
diff <(Precondiciones 67d2c06) <(Precondiciones 0223c6f)                                        # IDENTICAS
# 6. D.3 y estado (lectura, 05:49:43 UTC)
SELECT count(*) FROM harvest_job WHERE fase NOT IN ('done','failed');                                            # 0
SELECT count(*) FROM apply_queue WHERE kind='harvest' AND estado NOT IN ('applied','failed','vetoed','discarded');  # 0
SELECT kind, modo, estado, count(*) FROM apply_queue WHERE estado NOT IN (...terminales...) GROUP BY 1,2,3;       # 0 filas
SELECT max(id), max(created_at) FROM harvest_job;           # 4 | 2026-09-20 08:41:38
SELECT id, mode, enabled, updated_at FROM ads_optimizer_goal ORDER BY id;   # 4-12 live/enabled
SELECT id, label, flag FROM config_version ORDER BY id DESC LIMIT 1;        # 20 | D.0: <tu go literal> | (null)
SELECT id, source, started_at, finished_at, ok FROM ingest_run WHERE started_at > now()-'8h';   # 430-438 todas ok=t
ssh goncloud 'curl -fsS http://127.0.0.1:8010/health'       # {"status":"ok"}
psql .../postgres -c "DROP DATABASE rv_h5_r2"               # borrada
```

## Bloqueantes

Ninguno.

El Bloqueante 1 de R1 queda levantado. Lo comprobe de forma independiente:
en el catalogo de prod, con un repro que discrimina y con el SHA revalidado
(ver "Lo verificado OK", puntos 1 y 2). En el diff no hay ningun bloqueante
nuevo.

## Observaciones (no bloqueantes)

1. **Las horas de 0040 en el plan son imposibles (evidencia, regla 4).**
   El plan dice "0040 aplicada ~06:05 UTC" y "re-verificacion FASE 1 ~06:10
   UTC". Pero el commit `0223c6f`, que ya da la migracion por aplicada, es
   de las 05:45:15 UTC. Yo encontre la tabla presente a las 05:47:34 UTC
   (`now()` de la DB), y el reloj de prod marcaba 05:47:06 al empezar. Las
   dos horas quedan en el futuro respecto de su propio commit. El "~06:05"
   viene del texto del go del operador, que era una estimacion. La hora real
   no se puede sacar de la base (`track_commit_timestamp=off`). Las cotas que
   tengo: despues de la lectura de R1 que la vio ausente (R1 declara lecturas
   de 05:26-05:40 UTC) y antes de las 05:45:15 UTC. Los mtimes de
   `/tmp/mig0040/` (05:41-05:44 UTC) caen dentro de ese rango. **Arreglo de
   una linea en esta misma ronda:** poner la hora real del shell del
   ingeniero o, si no la tiene, la cota "entre la lectura R1 y 05:45 UTC".
2. **0040 se aplico dentro de la zona excluida por el runbook 0.2**
   ("lejos de 05:00-07:20"). Pasa tanto con la hora real como con la
   declarada. Si fue antes de las 05:37:14, se empalmo con la ingesta
   `spapi_pricing` (run 438, 05:25:32-05:37:14). La FK
   `REFERENCES ingest_run(id)` toma un lock `SHARE ROW EXCLUSIVE` sobre
   `ingest_run`, que choca con los INSERT/UPDATE de la ingesta. En el peor
   caso uno espera al otro; ninguno falla. **No hubo daño:** las runs 431-438
   del dia estan todas `ok=t` y `/health` responde ok. El go "P3-H5 RONDA 2"
   del operador lo ratifica. Como ya no tiene reversa, no bloquea; pero el
   plan deberia declarar la desviacion en lugar de callarla.
3. **El plan no cita el go que ratifica 0040.** La cabecera cita solo el GO
   original, que dice literalmente "SIN migraciones nuevas", y la seccion
   0040 no nombra ninguna autorizacion. Asi el registro queda contradictorio.
   El go literal existe (`/tmp/mig0040/go-h5r2.txt`: nombra el archivo, el
   sha256 y #333, asi que cumple la regla 1), pero vive en `/tmp`, que es
   efimero. Copiar su primer parrafo al plan son 2-3 lineas.
4. **El arreglo de obs-2 es correcto pero quedo en prosa.** "Comparar
   ordenando ambos lados" alcanza para no parar en falso, pero la FASE 3 pide
   comando verbatim. Propongo agregar el literal, que probe contra prod
   (ok) y con dos controles negativos (FALLO):
   `[ "$(printf '%s' "$IDS_LIVE" | tr ',' '\n' | sort -n | paste -sd, -)" = "4,5,6,7,8,9,10,11,12" ] && echo ok || { echo "FALLO: IDS_LIVE difiere"; false; }`
5. **Ahora 0040 es precondicion dura del deploy, pero no esta en
   Precondiciones.** Agregar, en ventana y antes del paso 2:
   `SELECT to_regclass('public.ads_report_result') IS NOT NULL;` (esperado
   `t`). Opcional: confirmar que las ingestas 07:10/07:20 de hoy sellaron
   `ok=t` con la tabla ya presente. Eso prueba en vivo que el codigo viejo y
   0040 conviven.
6. **Rollback: falta una linea sobre 0040** (sigue abierto R1 obs-8). Lo
   verifique: el codigo pre-A.2 tiene 0 referencias a la tabla
   (`git grep 8cfe7b0^1`), y ningun codigo borra ni reescribe
   `ingest_run.id`. Por lo tanto la tabla es inerte si se revierte el codigo,
   y la regla 3 no necesita revertir 0040. Un `DROP TABLE` solo con go y
   sabiendo que tira auditoria append-only. Tampoco se documenta si hubo
   backup de schema antes de 0040 (0038 y 0039 si lo tuvieron). Como 0040 es
   aditiva y el backup de las 03:30 existe, no bloquea, pero hay que dejar
   dicho si se tomo o no.
7. **La "Salida verbatim" viene resumida** ("x2", "x3"). Al aplicar el mismo
   archivo en local salieron exactamente esas 10 lineas en ese orden
   (`CREATE TABLE`, `CREATE INDEX` x2, `CREATE TRIGGER` x2, `COMMENT`,
   `GRANT` x3, `DO`), asi que es consistente. Aun asi conviene pegar las 10
   lineas literales.
8. **El texto viejo de "BLOQUEANTE DECLARADO RONDA 1" sigue ahi** ("Este
   plan NO incluye aplicar 0040. Pregunta al review...") y conserva la
   imprecision de R1 obs-1 (06:45 y :1949/:1966). Lleva la marca RESUELTO,
   asi que es cosmetico.
9. **Del plan, este diff solo atiende R1 obs 2 y 3.** Siguen abiertas las
   obs 1 y 4-11 de R1. Segun la regla, cada una va a una fila del plan o
   tracker y se nombra en el PR. Dos pesan en la evidencia de FASE 3:
   obs-4 (el conteo de `apply_queue.applied_at` no ve los bids, que se
   aplican sin cola; sumar `apply_attempt ... started_at > '$INICIO_SHADOW'`)
   y obs-7 (digest antes/despues del build). La obs-11 sigue vigente:
   `config_version` max id = 20, asi que el `RETURNING id` esperado es 21.
10. **El test focalizado solo prueba compatibilidad de esquema, no
    privilegios.** `test_pipeline_metricas_en_vivo` aplica solo
    0001 + 0040 y corre como superusuario `orbit`. Por eso agregue el repro
    como `app_ingest` sobre 0001-0039 (punto 2 de abajo), que si cubre los
    privilegios reales.

## Lo verificado OK

- **(1) 0040 en prod es exactamente el archivo de master.**
  - sha256 `ae2970ef9c3e9a6e126e0b14aed49c7cf89f1704686931803dd456d665084b15`
    en los 5 lugares: `origin/master`, `25bded0`, el merge #333 (`8cfe7b0`),
    `/tmp/rv-h5-sha` y `/tmp/mig0040/`.
  - En prod (como `orbit_read`, read-only, 05:47 UTC): la tabla existe con
    0 filas y dueño `orbit`.
  - Indices (3): `ads_report_result_pkey`, `ads_report_result_run_idx
    (ingest_run_id)` y `ads_report_result_salud_idx (platform, report_name,
    observed_at DESC) WHERE status='written'`.
  - Triggers (2): `ads_report_result_append_only` (BEFORE UPDATE OR DELETE,
    FOR EACH ROW) y `ads_report_result_append_only_truncate` (BEFORE
    TRUNCATE), los dos con `prohibir_mutacion()`, que existe.
  - Constraints (5): FK a `ingest_run`, PK, `scope`, `reason` y
    `status_check`. Hay 9 columnas; `id` es `IDENTITY ALWAYS`.
  - Privilegios:

    | rol | SELECT | INSERT | UPDATE | DELETE | TRUNCATE | USAGE secuencia |
    | --- | --- | --- | --- | --- | --- | --- |
    | `app_read` | t | f | f | f | f | f |
    | `app_ingest` | t | t | f | f | f | t |
    | `app_decide` | t | f | f | f | f | f |
    | `app_admin` | t | f | f | f | f | f |

    ACL: `{orbit=arwdDxt,app_ingest=ar,app_decide=r,app_read=r,app_admin=r}`;
    secuencia `{orbit=rwU,app_ingest=rU}`.
  - `orbit_ingest` (login) es miembro de `app_ingest`. El INSERT de
    `_registrar_resultado` (`app/ads/reports.py:253`) no usa `RETURNING`, asi
    que `INSERT` + `USAGE` de la secuencia alcanzan.
  - **Equivalencia fuerte:** la misma consulta de catalogo de 26 lineas (ACL
    de tabla y secuencia, attacl, indices, triggers con `tgenabled`,
    constraints, columnas, md5 del COMMENT, md5 de `prohibir_mutacion`, RLS)
    da salida **identica** (`diff` vacio) en prod y en una base local donde
    apliqué 0040 verbatim.
- **(2) El Bloqueante 1 de R1 esta levantado.** Repro que discrimina, con el
  codigo real de `25bded0`, sobre una base local con 0001-0039 (el estado de
  prod antes de 0040; se omitio 0011, que es de datos). Corre como
  `SET ROLE app_ingest`, con `evaluar_perfiles` devolviendo un perfil
  aceptado y `solicitar_reporte` stubeado para fallar:
  - A) Sin 0040: `EXC: UndefinedTable relation "ads_report_result" does not
    exist`, el log `fallo de fase API sin run auditable (sello tambien
    fallo)` y `ingest_run (2, amazon_ads_reports_v3, sellada=False,
    ok=None)`. Es decir, la run queda ABIERTA, igual que en R1.
  - Se aplica 0040 con la misma forma del plan (`-v ON_ERROR_STOP=1 -1 <
    archivo`): EXIT 0.
  - B) Con 0040: pasa la fase pending, con eventos `[(3,'pending',4),
    (3,'failed',1)]` escritos **como app_ingest**. La run 3 queda sellada
    (`finished_at` puesto, `ok=false` por el stub).
  - Focalizado `tests/test_reports_pipeline.py -k en_vivo` en
    `/tmp/rv-h5-sha`: 2 passed, 25 deselected, EXIT 0 (lo re-corri; coincide
    con `/tmp/mig0040/focal.log`).
  - Convivencia con el codigo que hoy corre en prod: la version pre-A.2 no
    referencia la tabla, y la FK solo agrega triggers RI sobre DELETE/UPDATE
    de `ingest_run.id`, que ningun codigo ejecuta. Las ingestas de hoy
    06:45/07:10/07:20 no se ven afectadas.
- **(3) SHA revalidado y fila 3 en verde.** `git ls-remote` da
  `25bded042cd7600f4161f25b42b2d22d0b6c783b`. El comparador de D.1.0 paso 1
  con el `APROBADO` completo del plan da `ok`, tanto contra `origin/master`
  local como contra el remoto vivo. El control con el corto sigue dando
  `FALLO`, como documento R1. CI push `36095114172` sobre ese SHA:
  `completed/success` (misma evidencia de R1; el SHA no cambio).
  `git archive` con el SHA completo funciona, que es lo que usa D.1.4.
- **(4) Obs-2 y obs-3 de R1 bien aplicadas.**
  - Obs-3: el SHA completo aparece en la cabecera y en `APROBADO=`, y ya no
    queda ningun literal con el corto (el "25bded0" de Precondiciones es
    prosa).
  - Obs-2: el esperado pasa a ser un conjunto. El literal del paso 1 contra
    prod devuelve hoy `6,7,4,5,11,9,10,8,12`, que como conjunto es igual a
    {4..12}, asi que no hay parada en falso. El orden no afecta al flip
    (`WHERE id IN (...)`) ni al flip de vuelta de H5.4.
  - Las dos bastan (ver obs 4 para dejar el literal).
- **(5) El diff no rompe nada de lo APROBADO en R1.** Son 4 hunks: SHA de
  cabecera, seccion 0040 + retitulo del bloqueante, `APROBADO=` y el esperado
  del paso 1. Desde `### Paso 2` hasta el final (pasos 2-6, literales del
  flag, rollback) el texto es identico byte a byte; Precondiciones y la
  ventana declarada tambien. No encontre comandos nuevos de escritura en el
  plan: el unico comando nuevo es el de 0040, y queda como registro de algo
  ya hecho.
- **(6) D.3 sin cosecha en vuelo (05:49:43 UTC, read-only).**
  - `harvest_job` no terminal = **0**. `apply_queue` harvest no terminal =
    **0**, y no hay ninguna fila no terminal de ningun kind.
  - El ultimo `harvest_job` sigue siendo el id 4 (2026-09-20 08:41).
  - Goals 4-12: `live`/enabled, con 8-12 en `updated_at 2026-09-24
    04:03:55`. El ciclo de las 08:40 todavia no corre.
  - Lo que dice el plan en "Estado D.3" sigue siendo cierto. Los dos conteos
    se repiten en ventana, como exige el plan.

VEREDICTO: APROBADO
