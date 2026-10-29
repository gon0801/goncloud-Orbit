# RESUMEN-ADS-01 — Puerta de fuente (inspección de solo lectura)

Fecha de inspección: 2026-10-29 01:49 UTC.
Origen: `accounting.db` del servidor `goncloud`, abierto con
`file:...?mode=ro`. Ninguna consulta escribió en accounting, bridge ni MeLi.

## Hallazgo que detiene la puerta

El plan (`plans/resumen-ads-multicanal.md`, sección RESUMEN-ADS-01) y la
asignación nombran como fuente `accounting.meli_ads_metrics_daily`. Esa tabla
**no tiene grano campaña/día ni identidad de campaña**:

```
PRAGMA table_info(meli_ads_metrics_daily)
date TEXT PRIMARY KEY, clicks, impressions, spend, ad_sales, acos, updated_at
```

Es el total del advertiser por día (una fila por fecha, PK `date`). No existe
`campaign_id` ni `campaign_name`, así que de ella no se puede derivar una
entidad `kind='campaign'` sin inventar el reparto.

La misma base, escrita por el **mismo** `sync_meli_ads.py` en la misma
transacción lógica, sí tiene el grano que el plan exige:

```
PRAGMA table_info(meli_ads_campaigns_daily)
date TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT,
clicks, impressions, spend, ad_sales, acos, updated_at,
PRIMARY KEY (date, campaign_id)
```

## Contrato comprobado en `meli_ads_campaigns_daily`

| Punto | Resultado | Evidencia |
|---|---|---|
| Grano | campaña/día | PK `(date, campaign_id)`; 4 filas por día en 196/196 días |
| Identidad | estable | 4 `campaign_id` numéricos, 196 días contiguos cada uno (2026-04-16..2026-10-28), un solo `campaign_name` por id |
| Moneda | MXN | la tabla no guarda moneda; site `MLM`; los 255 cargos de ads en `ledger_events` son MXN; el trigger de Orbit sella `meli→MXN` |
| `spend` | costo de ads | viene de `cost` de la API de MeLi Ads |
| `ad_sales` | revenue atribuido | viene de `total_amount` de la API de MeLi Ads; no se deriva del ledger ni de ventas totales |
| Ceros | reales | 257 filas con `spend=0`, 573 con `ad_sales=0`, 256 con `impressions=0`: filas escritas, no huecos |
| Nulos | no hay | 0 NULL en las 784 filas; el esquema tiene DEFAULT 0 |
| Días faltantes | fila ausente | el sync solo pide desglose de días con actividad del advertiser; un día sin campañas no tiene filas |
| Correcciones | in-place, sin revisión | backup 2026-08-21 vs base viva: 512 filas comparables, 3 corregidas (spend T+0 parcial 21.42→130.91, etc.); no existe columna de revisión |
| `updated_at` | no sirve de versión | se reescribe en cada sync (`ON CONFLICT DO UPDATE ... updated_at=datetime('now')`) aunque el valor no cambie |
| Retención | sin borrado | 196 días presentes; el sync refresca 90 días y deja congelado lo anterior |
| Última sincronización | OK | `sync_failures.json`: `sync_meli_ads.last_success = 2026-10-29T01:29:22Z`; 0 fallos consecutivos |
| Calendario del sync | cada 2 h | lo corre `goncloud-dashboard.service` in-process (`INTERVAL_ADS = 2*3600`), no cron |
| Duración | ~20 s | `min/max(updated_at)` del último loop por campaña: 01:29:02 → 01:29:22 (90 días) |
| Zona horaria | día UTC solicitado | el sync pide `date_from/date_to` con `datetime.now(timezone.utc)`; el día que devuelve la API se guarda tal cual |

## Conciliación (solo lectura)

Ventana 2026-10-01..2026-10-28, suma de campañas contra
`meli_ads_metrics_daily`: **28/28 días idénticos** en clicks, impressions,
spend y ad_sales.

- spend: 19,648.01 MXN en ambas fuentes.
- ad_sales: 85,950.09 MXN en ambas fuentes.

## Estado de Orbit (solo lectura)

- 0 entidades `meli`, 0 observaciones MeLi; 18,528 entidades y 241,435
  observaciones en total (todas Amazon).
- 0 duplicados por `(ad_entity_id, metric_date, source_report_id)`.
- `ads_metric_observation` ya tiene PK `(ad_entity_id, metric_date, observed_at)`,
  índice único parcial `metric_dedupe_reporte` y trigger de moneda
  `metric_moneda_sellada`; `v_metric_latest` expone la última `observed_at`.
- `app_ingest` tiene `INSERT` en `ingest_run`, `ad_entity` y
  `ads_metric_observation`, y `UPDATE` solo por columna (`ad_entity.name`,
  `ingest_run.finished_at/rows_*/ok/skip_reason/llamadas`).
- No hay tabla de migraciones aplicadas; la forma real de las tablas confirma
  las migraciones hasta 0074 (columnas `ingest_run.platform/llamadas`,
  `ad_entity.first_seen_at`, trigger y vista vigentes).

## Decisión del operador

La fuente nombrada no cumple el grano. La tabla hermana
`meli_ads_campaigns_daily` sí lo cumple. El plan dice: *"Stop and revise this
plan if the source is not campaign/day"*. No se adapta la fuente en silencio.

**2026-10-29, autorización explícita del operador:** la ingesta lee
`meli_ads_campaigns_daily` del mismo snapshot de accounting.
`meli_ads_metrics_daily` queda únicamente como control de conciliación
(campaña/día sumado contra total diario), nunca como fuente de entidades.
El resto del contrato de RESUMEN-ADS-01 no cambia.
