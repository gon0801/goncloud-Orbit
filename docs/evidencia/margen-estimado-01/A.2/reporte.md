# MARGEN ESTIMADO 01 · A.2 — muestra real y conciliacion

Fecha: 2026-09-08 UTC. Todas las consultas fueron de solo lectura. El bridge
se copio con la API `sqlite3.Connection.backup()` y el snapshot se abrio con
`mode=ro`; Orbit se consulto con `SELECT`. No se modificaron bridge,
accounting ni produccion Orbit.

La muestra contiene 809 ofertas bridge y 518 listings Amazon de Orbit. El
resolver A.2 encontro 221 ofertas FBA MX frescas, 121 listings MX FBM fuera del
universo por `logistica_fbm_pendiente` y 176 listings US fuera por
`us_sin_politica_prospectiva`. No hubo oferta ausente, vencida o identidad
ambigua en este corte.

Los 221 precios resueltos conciliaron exactamente como `Decimal` con el precio
del mismo `(marketplace, seller_sku, ASIN, canal, fetched_at)` del snapshot.
La fuente mas reciente era 2026-09-08 06:36:47 UTC, dentro de las seis horas.

La ultima corrida real de costos al corte era la 109, terminada correctamente
el 2026-09-07 08:15:02 UTC con cero skips. Para una valoracion del 2026-09-08
antes del job de las 08:15 UTC, A.2 debe devolver `costo_desactualizado`; no
extiende la acreditacion del dia anterior. Habia 1,089 vigencias activas, pero
su existencia no sustituye la validacion diaria.

`fx_resolve(2026-09-08, USD, MXN, 7)` devolvio `16.89910000`, fecha
2026-09-04 y procedencia `nearest_prior`. La tasa conserva ocho decimales y no
extiende el maximo sellado de siete dias.

Los conteos y valores sin identificadores de publicacion estan en
`muestra-real-2026-09-08.json`.
