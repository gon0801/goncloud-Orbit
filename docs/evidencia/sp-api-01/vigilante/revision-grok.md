# Vigilante SP-API — revisión cruzada de grok (PR #280)

Revisor de infra: grok, headless, worktree propio, Postgres local, mutantes revertidos, `git status` vacío en ambas pasadas. Lead: 160 focales verdes, 5 mutantes ejecutados sobre `a97a893` (uno sobrevivía: borde superior de la ventana en la función pura) y re-ejecutado sobre `b7a7428` (cae). APPROVE del lead publicado como comentario en el PR #280.

---

Voy a leer el brief, el PR y el skill de review, y luego cruzo crontab, contenedor y el código del vigilante contra el SHA a97a893.El SHA coincide con `a97a893`. Sigo con el brief, el cuerpo del PR y el código de crontab/vigilante; no voy a postear review en GitHub.El diff contra `origin/master` trae más archivos de los del PR; voy a separar commits propios vs lo que master avanzó, y leer crontab, compose y el vigilante.El PR está un commit atrás de master; ahora leo el instalador de crons, compose, Python del contenedor y los tests.Simulo el filtro del instalador, verifico `fromisoformat` con `Z` y arranco la batería de tests.Los tests base pasaron (158). Ahora re-muto 3 del catálogo y 2 propios de ops, revirtiendo cada uno.# Revisión infra — grok — PR #280 — SHA a97a893

Base leída: brief `plans/brief-vigilante-cron-muse.md`, cuerpo del PR, `git diff origin/master..HEAD` y el delta contra merge-base `41bc6aa`. SHA verificado `a97a893`. Suite pedida: **158 passed** (`test_spapi_vigilante` + `test_notifica` + `test_architecture` + `test_cli`). Sin SSH, sin tocar producción. Mutantes revertidos; `git status --short` vacío.

## 1. Crontab e instalador

La línea nueva **sobrevive** al instalador de ORBIT 03. El filtro es:

```bash
grep -v "Orbit (ORBIT 03" | grep -v "job_key=ingest:" | grep -v "job_key=ads_optimizer" | grep -v "app.cli ingest" | grep -v "app.cli cycle"
```

Simulado contra un crontab mezclado: se caen `ingest`/`cycle`; **quedan** el comentario `job_key=spapi:vigilante`, la línea de las 07:30 y `spapi:diario`. No contiene `app.cli ingest` (es top-level `app.cli spapi_vigilante`); `job_key=spapi:` no calza `job_key=ingest:`. El test pinza la línea exacta (H1 de A.R).

`flock -n /tmp/spapi-vigilante.lock` es el **mismo patrón** que `spapi:diario` (A.6 ya instalado). Correcto en este server: lock en el host (el cron de `gon` envuelve el `docker exec`), no dentro del contenedor; `/tmp` se limpia al reboot y el flock se suelta con el proceso. Lock **distinto** al de diario: el vigilante no se queda mudo si el wrapper de las 05:00 sigue vivo —eso es lo que tiene que vigilar.

Si el wrapper de las 05:00 **sigue corriendo a las 07:30**: el vigilante corre igual. Las corridas con `finished_at IS NULL` cuentan ausentes → Telegram de silencio. Presupuesto documentado: ~47 min (baratas ~12 + pricing MX ~23 + US ~12), arranque 05:00 → fin ~05:47. La ventana 04:30–07:30 deja ~1 h 43 min de colchón. Un 429 con `Retry-After` 60 s no la come. Aviso falso solo si pricing se cuelga **horas**; eso es el contrato («colgada = silencio»), no un bug de ops.

Log: `>> /mnt/data/appdata/orbit/logs/spapi-vigilante.log 2>&1`. El instalador de ORBIT 03 ya hace `mkdir -p` + `chown gon:gon` de `logs/`; A.6 ya escribe `spapi-diario.log` ahí. El redirect **crea** el archivo en la primera corrida (dueño `gon` en el host; el python va como 10001 *dentro* del contenedor). En el repo **no hay logrotate** para ningún cron de Orbit; este log es ~8 líneas/día. Residual preexistente, no específico del PR.

No pude ver el crontab vivo ni `logrotate.d` del server (prohibido SSH).

## 2. Contenedor, env y logs

`docker-compose.yml` del servicio `app`:

- `ORBIT_DSN_READ: ${ORBIT_DSN_READ}` interpolado del `.env`
- `ORBIT_SECRETS_DIR: /mnt/data/appdata/orbit/secrets` + volume `:ro`
- `ORBIT_PG_HOST: db` → `app/db.py` reescribe `@127.0.0.1:` / `@localhost:` a `@db:` dentro del compose

Telegram sale por el mismo `telegram.json` que el resto de `notifica_*`. El DSN de lectura del host no se queda apuntando al loopback del contenedor.

Stdout/stderr del `docker exec` van al log del host (`>> … 2>&1`). `PYTHONUNBUFFERED=1` en el Dockerfile; al salir el proceso igual flushea. El resumen `faltan N de 8` y el `vigilante ciego: …` por stderr **sí** quedan en `spapi-vigilante.log`.

Exit 1 (faltantes) y 2 (ciego / ventana inválida): Vixie/Debian cron manda mail **si hay output**, no por el código de salida. Con redirect, el fd de cron queda vacío → **no hay mail**. Mismo contrato que `ingest structure` / `cycle`. El canal de aviso es Telegram + el log. `MAILTO` no aparece en el runbook.

Hueco de diseño (el brief clava `docker exec`): si `orbit-app-1` está caído, Python no arranca, `avisa_ciego` no corre, solo cae el error de docker al log. DEPLOY lista «contenedor caído» como caso que avisa; por Telegram **no** avisa. Ver hallazgo 1.

## 3. Ventana y tiempo

Default: `desde = hoy 04:30 UTC` (`datetime.now(UTC).replace(hour=4, minute=30, …)`), `hasta = ahora` UTC. El cron `30 7 * * *` en un server documentado UTC dispara a las 07:30 UTC; a esa hora la ventana es válida.

Si corre **antes** de las 04:30 UTC: `desde >= hasta` → exit 2, stderr `ventana invalida`, **sin SELECT y sin Telegram**. No hay aviso falso. El paso (a) del runbook (`--dry-run` sin flags) a las 03:00 UTC saldría 2, no `faltan 0 de 8`.

Si el server **no** está en UTC: las horas del crontab se corren (preexistente; DEPLOY dice «estas horas SON UTC»). El python **sí** usa `datetime.now(UTC)` y no depende de `TZ` del contenedor (compose no setea `TZ`).

`started_at` es `timestamptz`. psycopg3 lo entrega **aware** (en este Postgres de test, sesión `America/Vancouver`; el instante compara bien con `desde`/`hasta` UTC). `desde <= started_at < hasta` es comparación de instantes, no de wall-clock. SQL `started_at >= %s AND started_at < %s` (hasta exclusivo) alineado con `faltantes`. Una naive vs aware levantaría `TypeError` → camino ciego (exit 2 + aviso); las ingestas escriben timestamptz, no aplica en prod.

`_fecha_utc` / `datetime.fromisoformat('2026-01-01T04:30:00Z')`: el contenedor es `python:3.12-slim` (`requires-python >=3.12`). `Z` entra desde 3.11. Verificado aquí (CPython 3.14 del venv + los CLI tests del PR pasan con `…Z`). Naive sin `Z` se asume UTC. El paso (b) del runbook es seguro.

El builder pone el literal `UTC` sobre `%Y-%m-%d %H:%M` **sin** `.astimezone(UTC)`. El cron y el runbook van en Z/UTC; un `--desde` con offset distinto etiquetaría mal. No es el camino de las 07:30.

## 4. Fail-silent

| Camino | ¿Levanta? | Aviso | Exit |
|---|---|---|---|
| DB caída / DSN roto / `ORBIT_DSN_READ` ausente | no (`except Exception`) | `avisa_ciego` (fail-silent, scrub) | 2 |
| Canal Telegram inactivo | no | sender → `True`, sin POST | 1 si hay faltantes; 0 si verde |
| Canal caído / `ok=false` / excepción | no | `False` + warning con scrub | 1 (faltantes) o 2 (ciego) |
| Ventana invertida/vacía | no | **nada** (antes de abrir la base) | 2 |
| Verde | no | **nada** | 0 |
| `--dry-run` | no | cero envíos | mismos 0/1/2 |

Ningún camino del `main` propaga excepción al cron. `argparse` con ISO basura sí hace `SystemExit(2)` —no es el cron.

Sin flanco / idempotencia: cada corrida que ve ausentes avisa. El cron es una vez al día → un Telegram por día de silencio. Si el dueño lo corre a mano (paso (b) del runbook, o un replay), **avisa otra vez**. Aceptable: no hay tabla que escribir; el brief lo pide explícito en (b). Residual: dos `--dry-run` no avisan; dos corridas live sí.

`connect()` no pone `connect_timeout`. Un host blackholeado cuelga ~2 min (SYN retries de Linux), luego ciego; el flock se sostiene ese rato. No un día.

## 5. Mutantes (tabla: mutante | resultado | test)

Aplicados sobre a97a893, pytest, `git checkout -- <archivo>`, sin pycache residual.

| Mutante | resultado | test |
|---|---|---|
| `ok=false` como ausente (catálogo) | **MUERE** | `test_faltantes_ok_false_cuenta_como_presente` (`assert [('spapi_orders','amazon_mx')] == []`) |
| `finished_at NULL` como presente (catálogo) | **MUERE** | `test_faltantes_finished_none_es_ausencia` (`assert [] == [('spapi_orders','amazon_mx')]`) |
| crontab sin `flock` (catálogo, ops) | **MUERE** | `test_linea_crontab_en_deploy_y_sobrevive_instalador` (constante exacta ya no está en DEPLOY) |
| quitar `platform is None` del filtro (propio, ops) | **SOBREVIVE** | `test_spapi_vigilante` + `test_notifica` + `test_architecture` = 111 passed. `None not in PLATAFORMAS_SPAPI` ya ignora NULL; el check es redundante, no un hueco |
| `--dry-run` no imprime `faltan N de 8` (propio, ops) | **MUERE** | `test_cli_dry_run_no_envia_pero_mismo_codigo` y `test_cli_registrado_en_app_cli_top_level` |

## 6. Diff

**Delta real del PR** (merge-base `41bc6aa`..`HEAD`): 7 archivos, **solo aditivo**, dentro de lo permitido:

- `app/spapi/vigilante.py` (nuevo, +184)
- `app/notifica.py` (+57 / 0)
- `app/cli.py` (+18 / 0)
- `docs/DEPLOY.md` (+41 / 0)
- `tests/test_spapi_vigilante.py` (nuevo, +425)
- `tests/test_notifica.py` (+124 / 0)
- `tests/test_architecture.py` (+9 / 0)

`notifica_*` existentes intactos. Candado AST de Ads ampliado a `vigilante.py`. Cero migraciones, cero wrapper `spapi-diario.sh`, cero `app/spapi/salud.py`.

**`git diff origin/master..HEAD` (two-dot, master hoy `d7c0bf9`) NO es solo aditivo:** master avanzó 2 commits (#277/#278: runbook F2, briefs, evidencia R.1, ajustes de tests F2). El two-dot **borra** esos archivos en el parche. GitHub merge es 3-way: el vigilante inserta ~L448 (tras diario) y F2 vive al final de DEPLOY; **no se pisan**. No aplicar el two-dot a mano. La rama está 2 commits detrás; el merge UI las conserva.

## 7. Hallazgos numerados con severidad (alta = puede dejar de avisar, avisar falso, romper otro cron o el contenedor; media; baja)

1. **Alta (residual inherente al brief, no un miss de Muse).** Contenedor `orbit-app-1` caído → `docker exec` falla en el host, Python no arranca, no hay Telegram ciego. DEPLOY §vigilante lista «contenedor caído» como caso cubierto. El artefacto clava exactamente la línea del brief. Mitigación real: wrapper host (como `spapi-diario.sh`) que si `docker inspect` no da `Running` dispare el canal; queda fuera de los archivos permitidos. Mínimo honesto: no afirmar que ese caso avisa por Telegram (el error de docker sí queda en el log). No rompe otros crons ni el contenedor.

2. **Media.** Rama no rebaseada sobre `origin/master` actual. Merge 3-way de GitHub está limpio; un apply del two-dot **borraría** el runbook F2 de #277. Disciplina: mergear por GitHub, no por parche two-dot.

3. **Baja.** Sin flanco: un replay manual live re-avisa el mismo día. El paso (b) del runbook lo necesita. No hay fila que escribir.

4. **Baja.** Sin `logrotate` para `spapi-vigilante.log` (tampoco lo hay para el resto de Orbit). Volumen despreciable (~8 líneas/día).

5. **Baja.** Mutante `platform is None` sobrevive: el filtro `not in PLATAFORMAS_SPAPI` ya cubre NULL. Comportamiento correcto; el test no discrimina el check explícito.

6. **Baja.** `connect()` sin `connect_timeout` en el DSN: un blackhole cuelga el flock ~2 min y luego ciego. No un día.

7. **Baja.** `--dry-run` del runbook (a) sin `--desde/--hasta` antes de las 04:30 UTC sale 2 (`ventana invalida`), no `faltan 0 de 8`. El cron de las 07:30 no pisa este caso.

No encontré camino que rompa el instalador de ORBIT 03, el wrapper de las 05:00, ni el contenedor.

## 8. Veredicto: APPROVE sobre a97a893

La línea de crontab sobrevive al instalador, el `flock -n` en `/tmp` es el patrón ya vivo de `spapi:diario`, la ventana 04:30–07:30 cubre el presupuesto de pricing, el contenedor trae `ORBIT_DSN_READ` + `ORBIT_SECRETS_DIR`, fail-silent no levanta, y los mutantes de catálogo mueren. El hueco «contenedor caído → Telegram» es del diseño `docker exec` que el brief clavó; no bloqueo el SHA por eso (hallazgo 1, residual a declarar en el merge/runbook si el lead quiere).

`git status --short` final: *(vacío)*


---

Voy a verificar solo la ronda `a97a893..b7a7428`: primero el diff completo y los archivos citados, luego tests y mutantes (y los reverso).Diff acotado a 5 archivos. Ahora leo el código de `faltantes`, `connect` y los tests de la ronda.Baseline en verde, conexión real con `connect_timeout=10` y después los mutantes (revirtiendo cada uno).160 tests en verde y la conexión real con timeout respondió. Ahora aplico los dos mutantes y los reverso.# Ronda #280 — grok — SHA b7a7428

## 1–5

**1. Borde superior — verde, mutante cae.** `faltantes` sigue en `[desde, hasta)` (`desde <= started_at < hasta`, línea 63; SQL ya era `started_at < %s`). `test_faltantes_started_en_hasta_exacto_es_ausencia` pasa en b7a7428. Mutante `<= hasta` → **FAILED**: `assert [] == [('spapi_orders', 'amazon_mx')]`. Revertido.

**2. `connect_timeout=10` — verde limpio.** `app.db.connect(dsn, **kw)` reenvía a `psycopg.connect` (firma `(dsn: 'str', **kw)`). El test **no oculta** el kwarg: espiá `vigilante.connect` y pinza `llamadas == [{"connect_timeout": 10}]`. Conexión REAL `connect(..., connect_timeout=10)` contra `localhost:5432` → `SELECT 1` como `orbit`, `status=0`, cerró sin error.

**3. Builder UTC — verde, mutante cae.** `test_aviso_spapi_silencio_texto_exacto` pasa `desde` en CDMX (−05:00) y exige el mismo texto que UTC. Mutante «quitar `astimezone`» → **FAILED**: etiqueta `2026-09-15 23:30 UTC` vs `2026-09-16 04:30 UTC`. Revertido. Observación menor (no bloquea): el test no convierte `hasta`; un `astimezone` solo en `desde` seguiría verde.

**4. `docs/DEPLOY.md` — verde, honesto y suficiente.** El límite va en negritas; ya no se lista «contenedor caído» como caso que avise. Explica `docker exec` → Python no arranca → no Telegram, solo error en `spapi-vigilante.log`, y por qué un aviso de host queda fuera (secretos `700 root`). El dueño que instala sabe qué no esperar y dónde mirar. Observación menor: «fuera de este PR» es meta de ticket; el hecho operativo ya está.

**5. Scope — verde.** Diff `a97a893..b7a7428`: 5 archivos, +88/−14. Aditivo en código/tests (`connect_timeout`, `astimezone`, 2 tests). No aditivo solo docstring de `vigilante.py` y el runbook (el recorte de «contenedor caído avisa»). Nada fuera de `app/notifica.py`, `app/spapi/vigilante.py`, `docs/DEPLOY.md`, `tests/test_notifica.py`, `tests/test_spapi_vigilante.py`. Batería: **160 passed** en 2.15s.

## Veredicto: APPROVE sobre b7a7428

Hallazgos de a97a893 cubiertos y discriminantes. Sin CAMBIOS REQUERIDOS.

`git status --short`: *(vacío)*
