# RESUMEN-ADS-01 — Cierre de fase (listo para revisión de Limi 3.1)

Fecha: 2026-10-29. Rama: `resumen-ads-01-ingesta-meli`.
Base: `31ba12a16f12b90e2f7ea6e86a0ff102f3b8465f` (master al iniciar).
Commit de implementación: `79da5251b59051cc340b054ad010c1da10fbeec2`.
Corrección del bloqueante de revisión: `f11e2434f432f231892dc1bc20d4352640dc901b`.
Segunda revisión (concurrencia y nombre): `f55600e7c49b858b8417bba4f740ee68264d1e60`.
Tercera revisión (marca de as-of en recopias): `47f83e72a7419dc7e27b6f2a570804330239dce1`.

## Alcance entregado

Ingesta de solo lectura de Mercado Libre Ads desde el snapshot de contabilidad
hacia `ad_entity` (`kind='campaign'`) y `ads_metric_observation`, sin migración
y sin tocar API, UI, Shopify, bids, pausas, precios, accounting ni bridge.
No se inició RESUMEN-ADS-02.

## Puerta de fuente (detuvo y se reportó)

`meli_ads_metrics_daily` (la tabla que nombraba el plan) es total del
advertiser por día (PK `date`): no tiene campaña. La tabla hermana
`meli_ads_campaigns_daily`, escrita por el mismo `sync_meli_ads.py` en el
mismo snapshot, sí es campaña/día (PK `(date, campaign_id)`), con 4
`campaign_id` estables y 196 días contiguos. Se reportó al operador y se
autorizó explícitamente leer la tabla hermana el 2026-10-29. Evidencia y
consultas: `00-puerta-fuente.md`, `01-fuente-esquema.txt`,
`02-fuente-campanas-perfil.txt`, `03-fuente-correcciones-y-sync.txt`.

## Contrato comprobado

- Grano campaña/día; 4 campañas, 784 filas, 2026-04-16..2026-10-28.
- Moneda MXN (site MLM, cargos de ads MXN, trigger `meli→MXN`).
- `spend` = costo de ads; `ad_sales` = revenue atribuido. `orders` no existe
  en el origen: NULL. No se derivan del ledger ni de ventas totales.
- Ceros reales conservados; NULL conservado; día ausente = hueco.
- Sin revisión en el origen y `updated_at` se reescribe cada 2 h: la versión
  es el hash canónico (sin nombre). `source_report_id` =
  `meli-ads:<campaign_id>:<fecha>:<version>`.
- Corrección in-place comprobada contra un backup de 2026-08-21 (3 filas
  corregidas de 512 comparables).
- Sin madurez D-8 para MeLi; se ingesta T+0 parcial.

## Corrección del bloqueante de la revisión (Limi 3.1)

Revisión: un snapshot viejo ingerido después de una corrección recibía un
`observed_at` posterior y `v_metric_latest` mostraba el valor viejo. Corregido
con una política de orden por el as-of del origen:

- `as_of` = `max(updated_at)` del snapshot (UTC). `updated_at` no es la
  versión (cambia en cada sync), pero sí es la marca de cuándo escribió el
  origen.
- `source_report_id` =
  `meli-ads|<campaign_id>|<fecha>|<version>|<as_of_epoch>`.
- Una observación nueva solo se acepta si su as-of es **estrictamente
  posterior** al as-of de la observación vigente de esa campaña/fecha. Un
  snapshot atrasado, o uno con el mismo as-of y contenido distinto, se rechaza
  completo con `ok=false`: no escribe, no toca el catálogo y no reemplaza la
  corrección vigente.
- El mismo hecho re-observado con un as-of nuevo (recopia sin cambios) no
  agrega observaciones: manda la versión (hash).
- Un advisory lock por transacción serializa solo las ingestas de MeLi, para
  que dos corridas concurrentes no lean el mismo "último".

Pruebas nuevas: `test_snapshot_atrasado_no_reemplaza_la_correccion_vigente`
(reproduce la secuencia del bloqueante), `test_snapshot_con_el_mismo_as_of_no_reemplaza`
y `test_recopia_con_as_of_nuevo_y_mismo_contenido_no_agrega`. Reproducción
guardada en `17-bloqueante-snapshot-atrasado.txt`.

Residual declarado: si un snapshot afirma un `updated_at` **más nuevo** que el
de la observación vigente pero trae contenido viejo, el origen está mintiendo
sobre su orden y ninguna política puede distinguirlo de una corrección. La
recuperación operativa es re-copiar después del último sync de accounting.

Observaciones no bloqueantes atendidas: los conteos del docstring ya no se
presentan como invariantes; el orden de lectura/validación/apertura de la
corrida quedó descrito como es; el runbook usa `mktemp` en vez de una ruta
fija; y se quitaron los espacios finales de la evidencia.

## Segunda revisión (Limi 3.1)

Dos bloqueantes más, ambos corregidos con su regresión:

1. **Concurrencia y `observed_at`.** El reloj de ingesta se calculaba antes del
   advisory lock: una corrida que esperaba podía terminar con un
   `observed_at` anterior al de la que ya había cerrado, y `v_metric_latest`
   elegía la vieja. Ahora `observed_at` se toma con `clock_timestamp()`
   **después** del candado. Regresión con dos conexiones
   (`test_concurrencia_observed_at_se_toma_bajo_el_candado`): la nueva espera
   el candado, la vieja inserta y cierra primero, y aun así la vigente es
   6.5000.
2. **Un snapshot atrasado restauraba el nombre viejo.** Con métricas idénticas
   se omitía la validación temporal y `_entidad` actualizaba el nombre. Ahora
   un as-of anterior se rechaza **aunque `source_version` coincida**; el
   nombre solo se aplica desde un snapshot estrictamente posterior al as-of
   aceptado, y el catálogo se toca después de la validación. Regresiones:
   `test_snapshot_atrasado_no_restaura_nombre` y
   `test_mismo_as_of_no_restaura_nombre` (copia editada con el mismo as-of).

Evidencia: `18-regresion-bloqueantes-revision-2.txt` (los tres casos fallan en
`f11e2434` y pasan en el head). Focales: `86 passed` (23 del módulo).

## Tercera revisión (Limi 3.1)

Bloqueante: una recopia deduplicada no avanzaba la marca temporal. La marca
se derivaba solo de las observaciones, así que la recopia de 12:00 salía por
dedupe y el as-of aceptado seguía en 08:00; un snapshot de 11:00 entraba y
restauraba el nombre viejo.

Ahora la corrida exitosa sella `skip_reason = "as_of=<epoch>; <duplicados>"`.
Ese prefijo es el **máximo as-of aceptado** y se conserva aunque la recopia no
inserte métricas. En cada corrida se lee la última marca exitosa y se combina
con el máximo as-of de las observaciones. Un snapshot anterior a la marca se
rechaza completo, aunque sus métricas coincidan; con el mismo as-of solo se
admite el reintento exacto. El nombre solo se aplica desde un snapshot
estrictamente posterior a la marca.

Regresión: `test_recopia_deduplicada_avanza_la_marca_y_bloquea_el_snapshot_anterior`
con la secuencia de tres snapshots. Comprueba la marca persistida, que el
nombre no vuelve atrás y que un snapshot intermedio con métricas distintas
tampoco cambia el valor vigente. Contra `f55600e7` falla en la marca
(`'1x snapshot ya ingerido...'` en vez de `as_of=<epoch>`).

Evidencia: `19-regresion-marca-recpia.txt`. Focales: `87 passed` (24 del
módulo).

## Archivos

- `app/meli/ads_ingest.py` (nuevo): adaptador SQLite `mode=ro`, forma
  canónica, versión, entidades, observaciones append-only, `ingest_run`.
- `app/cli.py`: `ingest meli-ads --sqlite <snapshot>`.
- `tools/refresh_costos.sh`: corre `meli-ads` al final, aislado, sin cambiar
  los pipelines existentes ni la línea del cron.
- `tests/test_meli_ads_ingest.py` (nuevo) y `tests/test_cli.py`.
- `docs/DEPLOY.md` y `docs/DATABASE.md`: contrato, programación (08:15 UTC
  después del sync de accounting cada 2 h, ~20 s), conciliación y rollback.
- `docs/evidencia/resumen-ads-multicanal/01-ingesta/`: esta evidencia.

## Pruebas

- Focales: `87 passed` (`tests/test_meli_ads_ingest.py` + `tests/test_cli.py`;
  24 casos del módulo). Detalle: `07-pruebas-foco.txt`.
- Regresión contra el padre: la ingesta no colecciona (`ModuleNotFoundError`)
  y el CLI falla por el comando/choice ausente. Detalle:
  `06-pruebas-regresion-parent.txt`.
- Suite completa: 10 fallos preexistentes idénticos en el SHA padre, ajenos a
  esta fase (bloqueo_dueño, estimación MeLi, JEV bloqueo). Detalle:
  `16-suite-completa.txt`.

## Verificación operativa real

| Lane | Resultado | Archivo |
|---|---|---|
| 2 Carga CLI real (784 filas, 4 entidades) | OK | `09-operativo-carga-y-reintento.txt` |
| 3 Reintento idéntico (0 escrituras, 784 duplicados) | OK | `09-operativo-carga-y-reintento.txt` |
| 4 Corrección (1 observación nueva, `v_metric_latest` con 87.60) | OK | `10-operativo-correccion.txt` |
| 5 Moneda e identidad inválidas (falla cerrada, 0 filas) | OK | `12-operativo-rechazos.txt` |
| Snapshot atrasado sobre datos reales (no pisa la vigente) | OK | `20-operativo-snapshot-atrasado.txt` |
| Reproducción del bloqueante de Limi | OK | `17-bloqueante-snapshot-atrasado.txt` |
| Regresión de los dos bloqueantes de la 2a revisión | OK | `18-regresion-bloqueantes-revision-2.txt` |
| Regresión de la marca de recopia (3 snapshots) | OK | `19-regresion-marca-recpia.txt` |
| 6/7 Sin snapshot, sin DSN y DSN con password redactado | OK | `13-operativo-sin-snapshot-dsn.txt` |
| 8 Conciliación 784/784 filas y 196/196 días | OK | `11-conciliacion-orbit-vs-accounting.txt` |
| 9 Aislamiento de `refresh_costos.sh` (fx falla, meli-ads corre) | OK | `15-operativo-aislamiento.txt` |
| 10 Reinicio: misma historia y latest estables | OK | `14-operativo-reinicio.txt` |

Los lanes corrieron contra un Postgres desechable local (`orbit_meli_ads_*`)
con la migración 0001 aplicada y snapshots reales copiados de accounting con
`.backup()` en modo read-only. No se escribió en accounting, bridge, MeLi ni
en la base de producción de Orbit.

## Calidad

Ruff limpio y `pre-commit run --all-files` verde (gitleaks incluido), sin
`--no-verify`: `08-calidad.txt`.

## Pendiente

Revisión de Limi 3.1 antes de integrar. No se desplegó ni se mergeó. No se
inició RESUMEN-ADS-02.
