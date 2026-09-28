# Fase D — deploy conjunto de D.2, D.1b y C.2a

Deploy del 2026-09-28 a las 00:31 UTC. Lo ejecutó el dueño con `!` usando
[`deploy-fase-d.sh`](deploy-fase-d.sh), que sigue el patrón de `docs/DEPLOY.md`
y del deploy de D.1.

## Por qué fue conjunto

`master` ya traía las tres tareas juntas en los mismos archivos. El código de
D.1b escribe en la columna que amplía la `0045`, y el de C.2a escribe en la
tabla que crea la `0046`. Sin esas migraciones, el ciclo falla en TX4 (D.1b,
R-D1b-5) o en TX3 (C.2a). Por eso D.2, que era solo código, se desplegó en el
mismo paso, después de las dos migraciones.

## Salida

```text
APROBADO=2aa70cc6fa583fe673fde512ae823141ca8efe18  (origin/master, CI Quality success)
preflight: ok|0|f|f   (sin ciclo running; 0045 y 0046 sin aplicar)
-rw------- 1 root root 427334 Sep 28 00:31 /mnt/data/appdata/orbit/backups/pre0045_0046_schema_20260928-0031.sql
0045: BEGIN / DO / COMMENT / COMMIT
0046: BEGIN / CREATE TABLE / COMMENT x4 / GRANT x2 / DO / COMMIT
esquema: t|t|t|f|f|t
respaldo de codigo: predeploy-20260928-0031/ (app, Dockerfile, pyproject.toml, tools, uv.lock)
md5 OK app/cycle.py, app/apply.py, app/apply_cola.py, app/optimizer/goals.py (= 2aa70cc)
DIGEST antes=sha256:b8133eca8c1eadcb52bff34092ca518e90ddf18607e1e7d67c8aa039344d90ac
DIGEST despues=sha256:0a113111b921eb951f9cffb5342ad9b032bc9c0a0011f964d0fbe33e709d4ab2
COPY app ./app  DONE (no CACHED); orbit-app-1 Recreated
{"status":"ok"}  orbit-app-1 Up
```

La verificación del esquema `t|t|t|f|f|t` confirma seis cosas:

- El CHECK de `decision_sin_aplicar` acepta `choque_clave`.
- La tabla `target_acos_ciclo` existe.
- `app_decide` puede hacer INSERT en esa tabla.
- `app_decide` no puede hacer UPDATE.
- `app_decide` no puede hacer DELETE.
- `app_read` puede hacer SELECT.

Las advertencias `there is already a transaction in progress` y `there is no
transaction in progress` salen del `BEGIN`/`COMMIT` propio de cada migración
dentro del `-1` de psql, igual que en D.1. Cada migración quedó en una sola
transacción.

## Primer intento abortado

El primer intento se detuvo en el preflight (`ok|0|t|f`) antes de tocar nada.
El preflight buscaba el CHECK por su nombre, pero Postgres ya había nombrado
así el CHECK anónimo de columna de la `0044`
(`<tabla>_<columna>_check` = `decision_sin_aplicar_motivo_check`). Se confirmó
en Postgres local. El preflight se corrigió para buscar `choque_clave` en la
definición del CHECK, y el segundo intento dio `ok|0|f|f`. La `0045` no depende
de ese nombre: localiza el único CHECK de la tabla, lo borra y lo vuelve a
crear con la lista ampliada.

## Desviaciones (revisión IA de #364)

- **Fuera de ventana.** El deploy corrió a las 00:31 UTC. La ventana de
  `docs/DEPLOY.md` (D.1.0-1) y del runbook (0.2) es 09:30–15:00 UTC o después
  de las 16:00 UTC, lejos de 05:00–07:20 y 08:40. Las 00:31 quedan lejos de
  los crons (backup 03:30, ingestas 05:00–07:20, ciclo 08:40) y no había ciclo
  corriendo, pero no hubo dispensa previa. Queda declarada como desviación en
  el runbook.
- **Harvest en vuelo sin comprobar.** El preflight que corrió solo revisó
  ciclos en `running`. No contó los harvests no terminales en `apply_queue` ni
  los `harvest_job` a medias que exige D.1.0-2 antes de recrear el contenedor.
  El conteo después del hecho queda pendiente (abajo). El script de este
  directorio ya incluye los dos conteos para el siguiente deploy.
- **Respaldo sin `.dockerignore`.** El paso 6 no respaldó `.dockerignore`, que
  el paso 7 sí sobrescribe. Ese archivo no cambió entre el código anterior y
  `2aa70cc`, así que la reversa no lo necesita. El script corregido ya lo
  respalda.

Conteo posterior de harvest en vuelo (solo lectura; ambos deben dar `0`). No
prueba la precondición D.1.0-2 a las 00:31: un `0` hoy no demuestra que no
hubiera harvest a medias entonces. Solo acota el riesgo posterior. La fase
`hermanas_negadas` cuenta como en vuelo desde la `0038`, igual que en
`app/apply_harvest.py`:

```bash
ssh goncloud "docker exec -i orbit-db-1 psql -U orbit -d orbit -tA -c \"SELECT count(*) FROM apply_queue WHERE kind = 'harvest' AND estado NOT IN ('applied','failed','vetoed','discarded');\" -c \"SELECT count(*) FROM harvest_job WHERE fase IN ('pending','negative_created','exact_created','hermanas_negadas');\""
```

Resultado (2026-09-28 ~00:55 UTC, corrido por el dueño; sin ciclo entre el
deploy y el conteo):

```text
apply_queue harvest no terminal: 0
harvest_job pending/negative_created/exact_created: 0
harvest_job hermanas_negadas: 0
```

## Reversa

- Código: `/mnt/data/appdata/orbit/predeploy-20260928-0031/` + rebuild.
- Esquema: `/mnt/data/appdata/orbit/backups/pre0045_0046_schema_20260928-0031.sql`.

## Primer ciclo después del deploy

Pendiente: el ciclo corre a las 08:40 UTC del 28-sep. La consulta es solo
lectura (`BEGIN READ ONLY` ... `ROLLBACK`) y ya se probó contra el esquema
completo 0001–0046 en local:

```bash
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1' \
  < docs/evidencia/ads-proteccion-01/fase-d/primer-ciclo.sql
```

La consulta revisa siete cosas:

1. Ciclos terminados desde el deploy.
2. Filas de `target_acos_ciclo` por ciclo (C.2a).
3. Decisiones de hoja sin fila de target o con un target distinto al de
   `inputs`. Se esperan 0 y 0.
4. Saltos `inversion_sin_evidencia` (D.2).
5. Bids con `inversion_n10_v1`.
6. `decision_sin_aplicar` por motivo (D.1b).
7. `v_decision_huerfana` por origen. Se espera `huerfana` = 0.
