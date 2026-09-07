# ORBIT 19 / B.1 — Conciliacion viva de la tabla persistida

2026-09-07, productivo. Primera corrida `ingest metrics --productos` (D-1,
ambos perfiles; CA rechazado por gate de pais como en los 4 reportes estandar):

- MX: reporte fbe0457a COMPLETED, 862 filas gzip -> 258 filas (asin,sku,fecha)
  escritas; 604 filas absorbidas por agregacion de clave (suma, no reparto).
- US: reporte 7bc12fc6 COMPLETED, 317 filas -> 93 escritas; 224 absorbidas.
  **Primera ingesta US exitosa**: US pasa de no_verificada a verificada en
  su primera ingesta (regla 0.4 §1).
- ingest_run 116 ok=True rows_written=351 rows_skipped=828.

## Sumas: tabla producto vs agregado de campana (v_metric_latest, misma fecha/moneda)

| plataforma | producto (clicks/cost/sales30d) | campana (clicks/cost/ad_revenue) | delta |
|---|---|---|---|
| amazon_us | 227 / 128.70 / 115.20 | 227 / 128.70 / 115.20 | 0 / 0 / 0 |
| amazon_mx | 155 / 472.79 / 1703.44 | 160 / 505.87 / 1703.44 | -5 / -33.08 / 0 |

Lectura honesta:
- US cuadra EXACTO en las tres columnas.
- MX cuadra exacto en ventas; clics y costo quedan POR DEBAJO del agregado de
  campana (nunca por encima: no hay doble conteo ni reparto). Es el hueco de
  cobertura solo-actividad ya sellado en 0.3/0.4: el gzip spAdvertisedProduct
  no trae fila para todo ad con gasto reportado a nivel campana ese dia;
  la ausencia se trata como Sin datos, no como cero.
- Residual declarado (mismo espíritu que delta_impressions=-1 de 0.3):
  MX clicks -5, cost -33.08 MXN.

## Incidente operativo y fix

Las dos primeras corridas abortaron fail-closed: el reporte quedo PENDING
~25 min y el poll estandar (120x5s) corto antes; el reintento recibio 425
(reporte duplicado pendiente). Fix en master (PR #191): presupuesto de poll
propio 300x5s SOLO para spAdvertisedProduct, con test de regresion.
