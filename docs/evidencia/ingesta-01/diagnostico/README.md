# INGESTA 01 — diagnóstico (2026-10-19)

Lecturas de solo lectura en goncloud: `ingest_run` y `ads_report_result` como
`orbit_read` (con `default_transaction_read_only = on`), los logs de
`/mnt/data/appdata/orbit/logs/` y el log de contabilidad
`/mnt/data/appdata/accounting/logs/amazon_ads_sync.log`. Reloj del servidor: UTC.

## Corridas `amazon_ads_reports_v3` (07:10 UTC)

| Día | Duración (min) | ok | Reporte que agotó 25 min |
| --- | --- | --- | --- |
| 09-25 / 09-26 | 16 / 12 | sí | |
| 09-27 a 10-09 | 22 a 81 | sí | |
| 10-10 | 41 | no | MX keywords (MX campañas tardó 15) |
| 10-11 a 10-13 | 22 a 36 | sí | |
| 10-14 | 138 | no | US search_terms; los 7 anteriores tardaron 10 a 23 min cada uno |
| 10-15 | 118 | no | US targets |
| 10-16 | 125 | no | US keywords |
| 10-17 / 10-18 | 42 / 48 | sí | |
| 10-19 | 111 | no | US keywords; 5 de 8 ya descargados se tiraron |

Cinco fallas en quince días, del 5 al 19 de octubre de 2026 (10, 14, 15, 16 y
19). El último commit que tocó `app/ads/reports.py` antes del corte del
2026-10-19 es #425 (BIDS 02 S.3, del 2026-10-10, que agregó la corrida de
placements).

## Por qué

- **Disparador externo:** esos mismos días Amazon tardó más con todos. La corrida
  de productos tardó 26 a 40 min (normal 7 a 17) y la de placements 27 a 37
  (normal 8 a 11), y contabilidad esperó 46, 64 y 47 sondeos en su corrida de
  las 06:00 del 14, 16 y 19 (0 a 14 los días normales).
- **Diseño que convierte lentitud en falla** (`app/ads/reports.py`):
  `sync_metrics` pide los 8 reportes uno por uno (2 perfiles × 4); cada uno
  tiene 300 × 5 s = 25 min (`INTENTOS_POLL`); uno que se pasa aborta la
  corrida y descarta lo descargado; no hay segundo intento en el día.
- **Competencia propia:** 06:00 contabilidad, 07:10 métricas, 07:20 productos,
  07:25 placements piden reportes a la misma cuenta a la vez.
- **Consecuencia:** el ciclo de 08:40 corrió los cinco días sin las métricas
  del día: el 10 la corrida ya había fallado a las 07:50, y el 14, 15, 16 y 19
  seguía corriendo y falló entre las 09:01 y las 09:28. La única guarda del
  motor es un watermark de más de 7 días.
