# SP-API 01 / A.6 — Deploy A.1–A.3 a producción y primeras corridas reales

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
