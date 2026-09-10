# SP-API 01 / A.6 — Despliegue de la Fase A a producción

Dos partes, porque el despliegue fue en dos tiempos: primero A.1–A.3
(2026-09-09/10), y el final con A.4 + A.5 + cron (2026-09-10). La segunda
parte está más abajo.

## A.6 (parcial) — Deploy A.1–A.3 y primeras corridas reales

Fecha: 2026-09-09/10 UTC. Ejecuta: el lead, con go del dueño. Alcance: A.1
(cliente), A.2/A.2b (Orders + estado/total) y A.3 (Pricing). A.4/A.5 siguen
pendientes (sus migraciones y crons NO están en este deploy).

## Secuencia (runbook docs/DEPLOY.md)

1. **Estado previo**: 0030 ya aplicada (224 filas, primeras corridas de
   orders del dueño); 0031/0032/0033 ausentes; contenedor con código pre-A.3.
2. **Backup** (staging + verificación de marcadores, patrón 4.1):
   `backups/pre0031_spapi_order_observation_20260910-003302.sql` (42,695 B,
   `CREATE TABLE` + marcador de cierre presentes, 600). El backup diario
   mas reciente (`orbit_2026-09-09`, 03:30 UTC) cubre el resto del esquema.
3. **Migraciones** (cada una con `-1 -v ON_ERROR_STOP=1`, como `orbit`):
   0031 → 0032 → 0033, en orden. Post-verificación: 224 filas intactas,
   vista `v_spapi_order_ultima` (224 filas, 1:1 sin re-observaciones),
   `fulfillment_status` presente, triggers `spapi_price_tiempo_coherente` +
   append-only, `ingest_run.llamadas`, GRANTs (`app_ingest` INSERT=t,
   `app_read` SELECT=t, `app_decide` INSERT=f).
4. **Deploy app**: backup `app.bak-predeploy-20260910`; `git archive
   origin/master` (SHA `1291bb0`) por ssh; md5 de todos los `.py` idénticos
   server vs `origin/master`; `docker compose up -d --no-deps --build app`
   → `Recreated` (no CACHED); `orbit-db-1` intacto (mismo container id);
   `/health` ok; binds `127.0.0.1:8010` y `10.13.13.1:8010` (nunca 0.0.0.0);
   CLI ya con `spapi_pricing`.

## Primeras corridas reales

- `ingest spapi_orders --platform amazon_mx|amazon_us`: runs 143/144,
  ok=true, 15 + 6 filas (ventana incremental con solape de 1 d sobre las
  224 previas). La clave bitemporal de 0031 ya gobierna.
- `ingest spapi_pricing --platform amazon_mx|amazon_us`: **el primer pase
  destapó un bug de esquema** — ver siguiente sección.

## Bug de producción y fix (mismo deploy)

Síntoma: ambos pases de pricing escribieron el universo completo
(342/342 ASINs MX, 176/176 US en `spapi_price_observation`) pero el sello
del run reventó con `permission denied for table ingest_run`; runs 145/147
quedaron abiertos.

Causa raíz: 0001 otorga `UPDATE` de `ingest_run` a `app_ingest` **por
columna** (`finished_at, rows_written, rows_skipped, skip_reason, ok`);
las columnas nuevas no heredan grants por columna y 0033 agregó `llamadas`
sin su GRANT. Orders no lo notó: su sello no toca `llamadas`. CI tampoco:
el e2e corre como dueño de la base desechable, no como `app_ingest`
(brecha declarada en el PR).

Fix: **PR #242** — migración `0034_ingest_run_llamadas_grant.sql`
(`GRANT UPDATE (llamadas)` + verificación DO) y test
`test_grant_update_llamadas_app_ingest` (pinnea las seis columnas del
sello con `has_column_privilege`, demostrado en rojo sin la migración,
regla 9). CI verde, mergeado (`3a9a57f`), aplicada en producción y
verificada (`has_column_privilege = t`).

Cierre de las corridas huérfanas: 145/147 selladas a mano como `orbit`
(`ok=false`, `rows_written` conciliado contra `spapi_price_observation`
por `ingest_run_id` — 342 y 176, universos completos, cero skips — y
`skip_reason` con la causa). `ingest_run` es la excepción mutable
deliberada del esquema (COMMENT ON TABLE de 0001); los datos de precio
son append-only y no se tocaron.

## Re-corridas tras el fix

Con 0034 aplicada (sin redeploy: el cambio era solo de base):

- `ingest spapi_pricing --platform amazon_mx`: run 148, **ok=true**,
  342/342 ASINs escritas, 0 skips, 684 llamadas (2 por ASIN), 1,376.7 s.
- `ingest spapi_pricing --platform amazon_us`: run 149, **ok=true**,
  176/176 ASINs, 0 skips, 352 llamadas, 702.7 s.

Verificación (regla 10, contra los datos reales): con Buy Box ganador la
box es propia en el 100% de los casos (MX 254/342 con box, US 105/176;
las demás con `offers_count=0` y precios NULL — ausencia declarada);
precios propios sanos por moneda (MXN 699.00–2,689.00; USD 43.20–188.00;
una sola moneda por plataforma). Segunda observacion del mismo ASIN el
mismo dia: permitida por diseno (append-only por `observed_at`; las filas
de las corridas 145/147 se conservan).

Smoke: `/health` ok, `/salud` 200 (el bloque spapi en `/salud` es A.5).

## Reversa

Solo lectura: no hay nada que deshacer en Amazon. La reversa operativa es
apagar la ingesta y conservar los datos (los crons `spapi_*` aún no
existen — la propuesta de cron es de A.5 — así que hoy la ingesta es 100%
manual: nada que apagar). La reversa de esquema de 0031
(`0031_reversa_spapi_orders_bitemporal.sql`) quedó ensayada en CI con
`test_reversa_0031` (guarda doble: aborta con re-observaciones o con
`fulfillment_status` poblado). 0032–0034 son expansivas (tablas/columna/
grant nuevos): reversa = conservarlas sin uso.

---

# A.6 (final) — Deploy de A.4 + A.5, cron diario y primeras corridas

Fecha: 2026-09-10 UTC. SHA desplegado: **`aee0221`** (`origin/master` tras
el PR #249, con A.R cerrada). Ejecuta: el dueño, con el script
`out/migrar-a6-final.sh` (idempotente; aborta si `origin/master` no trae
las tres migraciones y el informe A.R).

## 1. Respaldo y migraciones

Respaldo del esquema ANTES de cualquier DDL:
`/mnt/data/appdata/orbit/backups/schema-pre-a6final-20260910-0759.sql`
(323 381 bytes, 45 `CREATE TABLE`).

`0035` → `0036` → `0037`, cada una en UNA transacción con `ON_ERROR_STOP`,
servidas desde `origin/master` por stdin. Las tres aplicaron sin error.

> Nota: cada archivo trae su propio `BEGIN/COMMIT` **y** el script pasó
> `-1`, así que psql avisó `there is already a transaction in progress` /
> `there is no transaction in progress` por cada una. Son benignos —todo
> corrió en una sola transacción y commiteó—, pero el `-1` sobra cuando el
> SQL ya trae la transacción, como `docs/DEPLOY.md` ya advierte para 0011.

## 2. Verificación de esquema (como `orbit_read`, sin mutaciones)

| Comprobación | Resultado |
|---|---|
| Triggers en las 2 tablas nuevas | 5, los que declara 0035 (solo `spapi_inventario_observation` lleva `tiempo_coherente`) |
| `app_ingest` INSERT / `app_read` INSERT / `app_ingest` UPDATE en observación | `t` / `f` / `f` |
| `app_ingest` INSERT / UPDATE en `ingest_run.platform` | `t` / `f` |
| Índice de `/salud` | `CREATE INDEX ingest_run_source_platform_id_idx ON public.ingest_run USING btree (source, platform, id DESC)` |

El `t` de INSERT sobre `ingest_run.platform` es la comprobación que importa:
ese es el privilegio cuya ausencia por columna causó el bug 0033→0034
(corridas 145 y 147 abiertas). El candado de 0037 lo verifica al migrar.

## 3. Deploy del código

`md5 OK: 99 archivos idénticos a origin/master`; `orbit-app-1 Recreated`;
`/health` → `{"status":"ok"}`; puertos 8010 escuchando. Respaldo del código
previo en `app.bak-predeploy-20260910-0059` (la hora es local del server;
el respaldo de esquema del mismo run es UTC — mismo run, dos husos).

## 4. Cron diario

Wrapper `/mnt/data/appdata/orbit/spapi-diario.sh` (0755, 606 bytes) con las
8 corridas EN SERIE en un solo proceso, y **una** línea de crontab con
`flock -n` y log en `logs/spapi-diario.log`. Crontab: 47 → 49 líneas
(aditivo, ninguna otra tocada).

Convivencia verificada (hallazgo H1 de A.R): la línea no calza ninguno de
los cinco filtros `grep -v` del instalador de ORBIT 03 (`DEPLOY.md:496`),
así que re-aplicar el bloque de Ads ya no la borra. La versión anterior de
la propuesta —ocho líneas con `app.cli ingest`— sí se borraba sola.

## 5. Primeras corridas reales (MX)

| run | source | platform | ok | escritas | omitidas | llamadas | seg |
|---|---|---|---|---|---|---|---|
| 154 | `spapi_orders` | `amazon_mx` | t | 13 | 0 | — | 1 |
| 155 | `spapi_listings` | `amazon_mx` | t | 342 | 0 | 342 | 85 |
| 156 | `spapi_inventario` | `amazon_mx` | t | 1071 | 0 | 22 | 18 |

Conteos de tabla tras las corridas: `spapi_listing_estado_observation` 342,
`spapi_inventario_observation` 1071 (1071 SKUs distintos, `metric_date`
2026-09-10), `spapi_order_observation` 258. Los conteos coinciden **exacto**
con lo que reportó cada run: ni escrituras dobles ni pérdidas.

`platform` quedó escrita en las tres; las corridas de Ads y del bridge de la
misma base siguen con `platform` NULL, que es el diseño (solo las 4 ingestas
SP-API la escriben, y agregar la columna no rompió ningún pipeline viejo).

Las 342 filas de listings y las 1071 de inventario son la **primera** vez
que 0035 recibe datos en producción. El 1071 concilia con la sonda de Fase 0
(1071/1071 contra el bridge).

## 6. Smoke de candados (transacciones revertidas, `DEPLOY.md:1271`)

Con datos dentro —antes no se podía: los triggers append-only son row-level
y con la tabla vacía no disparan—, un `UPDATE` sobre cada tabla nueva:

```
ERROR: La tabla spapi_inventario_observation es APPEND-ONLY: corregir es
INSERTAR una observación nueva, no pisar la anterior.
ERROR: La tabla spapi_listing_estado_observation es APPEND-ONLY: ...
```

Ambos `ROLLBACK`; conteo posterior 1071, intacto.

## 7. Reversa ensayada

Apagar la ingesta = quitar la línea del crontab. Ensayado punta a punta:
`APAGADA, lineas spapi: 0` → `RESTAURADA, lineas spapi: 2` → 49 líneas
totales, las mismas de antes. Ninguna otra línea del crontab se tocó.

Reversa de esquema: `0035`/`0036`/`0037` son expansivas (tablas, columna e
índice nuevos) → reversa = conservarlas sin uso, con la ingesta apagada.
El respaldo del esquema previo y el del crontab quedan en `backups/`.

## 8. Qué NO se ejercitó todavía

- **`spapi_pricing` no corrió en este deploy** (≈23 min en MX): lo hará el
  cron de las 05:00.
- **Ninguna corrida de `amazon_us`**: también queda para el cron.
- El wrapper **no se ha ejecutado como wrapper**: las tres corridas fueron
  invocaciones directas del CLI. La primera prueba real del `flock`, del
  encadenado en serie y del log es la de mañana 05:00 UTC.
- Las **alertas de A.5 no se han disparado en producción**: no ha habido
  ninguna corrida fallida. El camino de Telegram sigue probado solo por
  tests.
