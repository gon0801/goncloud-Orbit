# P01: conciliación productiva MeLi Ads contra accounting

> **Superado el 5-nov-2026 09:00 UTC.** El job 10, tras el cron 08:15, concilió 812/812 filas y 203/203 días sin
> diferencias contra la misma huella/as-of de fuente: P01 cerrado. Este reporte conserva el estado histórico del 4-nov.
> [Resultado](../seguimiento-meli/resultado-job10-20261105T090000Z.json).

**Cierre parcial.** La comparación estricta vigente falla por dos campañas del
4 de noviembre: accounting corrigió sus métricas después de la única ingesta
de Orbit. No se ejecutaron ingestas ni se cambiaron datos, servicios o crons.
El primer cron posterior a la activación todavía no ocurrió.

## Alcance y límite temporal

Lectura capturada el **2026-11-05 00:52 UTC**. Ventana contractual UTC
**2026-04-16..2026-11-04**, cerrada en D-1. No se usó la fecha local del operador.
Grano de Orbit: `platform='meli' AND kind='campaign'`, `v_metric_latest`.
Fuente externa: `meli_ads_campaigns_daily` y control `meli_ads_metrics_daily`
de accounting. Copia consistente mediante SQLite `.backup()` desde URI `mode=ro`;
Postgres: rol `orbit_read`, transacción `REPEATABLE READ READ ONLY` comprobada.

El snapshot exacto de la carga inicial ya no estaba disponible. Se capturó uno
nuevo y se compararon versiones, sin presentarlo como el mismo snapshot:

| Evidencia | Orbit ingerido | Fuente actual |
|---|---|---|
| as-of UTC | 2026-11-04 23:21:17 | 2026-11-05 00:00:29 |
| Huella canónica | `8002541838d135dcc8dca609cbb197d45328449dc996ea9fb0764decffbda227` | `18dc8cd7f09b06cf2f3086b9202ac729891f5f58b6513087a73f997d47b6df55` |

La ingesta `1351` empezó `2026-11-04 23:22:25.424029 UTC`, terminó correctamente,
escribió 812 filas y no omitió ninguna. Todas las versiones guardadas coinciden
con el contenido actual de Orbit; 810/812 versiones de fuente actual coinciden
con ellas. Las dos distintas son precisamente las diferencias siguientes.
Esto es compatible con una actualización posterior del origen y el refresco
diario pendiente; no demuestra que la carga inicial haya importado mal.

## Resultado fila/día y totales

812 filas, 4 campañas y 203 días en ambos lados. Sin claves faltantes/sobrantes,
sin huecos de calendario y sin NULL en costo, revenue, impresiones o clics.
Los 812 `orders=NULL` son esperados: el origen no publica pedidos, según contrato.
No se sustituyó ese dato por cero. La moneda de Orbit es MXN; accounting no
incluye columna de moneda, por lo que se conserva el contrato documentado MLM/MXN.

| Métrica | Accounting actual | Orbit | Diferencia fuente − Orbit |
|---|---:|---:|---:|
| Costo MXN | 111626.7700 | 111613.3000 | 13.4700 |
| Revenue atribuido MXN | 396653.6000 | 396653.6000 | 0.0000 |
| Impresiones | 9237508 | 9235818 | 1690 |
| Clics | 37377 | 37366 | 11 |

| Campaña / fecha | Costo fuente / Orbit MXN | Impresiones fuente / Orbit | Clics fuente / Orbit |
|---|---:|---:|---:|
| 356150383 / 2026-11-04 | 231.6600 / 225.4600 | 14961 / 14261 | 64 / 60 |
| 356150418 / 2026-11-04 | 175.3600 / 168.0900 | 21623 / 20633 | 57 / 50 |

En ambas filas el revenue es cero medido en los dos lados. El control externo
del 4-nov trae 407.0200 MXN, 36584 impresiones y 121 clics; Orbit agrega
393.5500 MXN, 34894 impresiones y 110 clics. De los 203 días, 202 coinciden
completamente contra ese control. Hasta el **3-nov: 808/808 filas coinciden**.
Las 59 fechas de actividad del control anteriores al inicio declarado se
excluyen explícitamente. El control T+0 de 5-nov se excluye porque ninguno de
los lados publica campañas de ese día; no se declara conciliado.

Impacto: la vista de MeLi en Orbit subestima 13.47 MXN de gasto y 11 clics del
último día cerrado respecto del origen actualizado. No hay pérdida de filas
ni diferencia en revenue en esta captura. No corresponde cerrar P01 totalmente
con una conciliación estricta fallida.

## Cron posterior

`cron.txt` prueba que el script del host incluye `corre meli-ads meli_ads.log`
y tiene el mismo SHA-256 que `tools/refresh_costos.sh`:
`fee5bf977129598a459d2fba64cacc76561635c511c8004c1264d8554a7f08bb`.
El crontab de **gon** lo llama a las 08:15 UTC, envuelto por `cron-avisa`.
El último log de costos corresponde a 4-nov 08:15, antes de la carga manual;
`meli_ads.log` todavía no existe. A las 00:52 UTC del 5-nov, el primer cron
posterior sigue pendiente hasta **2026-11-05 08:15 UTC** (**5-nov 00:15 America/Vancouver**). No se esperó horas.
No hace falta reinstalar el script ni cambiar el cron.

## Reproducción y criterio de cierre

Desde la raíz del repo, con SSH `goncloud` disponible:

```bash
uv run --frozen python docs/evidencia/quick-wins-2026-11-04/conciliacion/resumir.py
uv run --frozen python docs/evidencia/quick-wins-2026-11-04/conciliacion/ejecutar.py
```

El primer comando recalcula totales y hashes de la evidencia guardada sin red.
El segundo toma una captura nueva, reemplaza sus resultados locales y reutiliza
las funciones del conciliador vigente de `01-ingesta/30-revision-10266d91`;
sale 1 si diverge, 0 solo si concilia. El DSN se captura en memoria desde
`ORBIT_DSN_READ`, jamás se pasa por argumentos ni se persiste. Solo crea y
limpia temporales propios, y cierra su túnel. El snapshot SQLite completo se
borra; se conserva su SHA-256, filas de ambas tablas relevantes y versiones en
`datos.json`. Los valores monetarios de esta evidencia son cadenas decimales.

Después de 08:15, comprobar una nueva `ingest_run` exitosa, `meli_ads.log` y
salida `CONCILIACION OK` contra **el mismo snapshot de esa corrida** antes de
su limpieza. Una captura posterior diferente puede divergir por correcciones
de accounting: cotejar siempre as-of y huella antes de atribuirlo a corrupción.
La regla estricta de activación del runbook exige ese snapshot compartido;
esta captura posterior no reemplaza aquella evidencia ni acredita el cron.
Si se ejecuta de nuevo este script después de medianoche UTC, D-1 avanza;
no interpretar la ausencia de un nuevo día aún no publicado como cero.

Readback concreto después de esa hora (no ejecuta la ingesta):

```bash
ssh goncloud 'date -u +%FT%TZ; tail -n 20 /mnt/data/appdata/orbit/logs/meli_ads.log; tail -n 8 /mnt/data/appdata/orbit/logs/costos.log'
uv run --frozen python docs/evidencia/quick-wins-2026-11-04/conciliacion/ejecutar.py
```

El script guarda `ingest_runs` con as-of y huella; verificar que apareció una
corrida posterior a 08:15. Si su huella y as-of coinciden con la fuente nueva,
la comparación usa la misma versión canónica ingerida; si no coinciden, sigue
sin resolverse el requisito temporal y se debe preservar un snapshot común
en una operación autorizada posterior. El cron vigente elimina su snapshot
al terminar: no se modificó ese comportamiento para retener datos contables.
