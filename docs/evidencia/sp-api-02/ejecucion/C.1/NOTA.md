# C.1 — nota de cierre de sp-api-02

## Prueba real del bloque B (checklist en el servidor, 2026-10-22)

Desplegado el 2026-10-22 07:30 UTC (respaldo `predeploy-20261022-073010`) con el SHA `f273213`.
Salida de `B.3/checklist.sh` contra el servidor real (`checklist-B.txt`):

```
== Checklist B.3 sp-api-02 (SHA f2732131d2284b5155a79eaa8855b480b70d8503)
OK    orbit-app-1 corriendo = true
== 1) client.py del contenedor
OK    md5 app/spapi/client.py (contenedor) = f722213fe1a6b60cee4c76816a706ac4
== 2) guion nocturno del servidor
OK    md5 spapi-diario.sh (servidor) = b33a79556171c539861b5b143453851b
== 3) crontab de gon conserva job_key=spapi:diario
OK    job_key=spapi:diario presente
== Resumen: fallas=0 no-medido=0
```

## Primera corrida nocturna con el código nuevo

**Todavía no ocurrió.** La corrida de las 05:00 UTC del 2026-10-22 salió antes del despliegue (07:30 UTC) y
corrió con el código viejo; por eso `spapi_inventario amazon_mx` falló con `status=429` y no se repitió.
La primera corrida con el código nuevo es la de las 05:00 UTC del **2026-10-23**: se revisa en esa corrida
(`logs/spapi-diario.log` del servidor: líneas `spapi 429 ...` con el intento y la espera, y `repito ...` o
`no repito ...` al final). Cola del log al cierre, aún con el código viejo (`log-ultima-corrida.txt`):

```
spapi_orders amazon_us: run=1002 escritas=8 omitidas=0 paginas=1 aviso=None
=== 2026-10-22T05:00:05Z spapi_listings amazon_mx ===
spapi_listings amazon_mx: run=1003 escritas=342 omitidas=0 skus=342 llamadas=342 segundos=83.2
=== 2026-10-22T05:01:29Z spapi_listings amazon_us ===
spapi_listings amazon_us: run=1004 escritas=176 omitidas=0 skus=176 llamadas=176 segundos=43.8
=== 2026-10-22T05:02:13Z spapi_inventario amazon_mx ===
ingesta spapi_inventario fallo: inventario status=429
=== 2026-10-22T05:02:21Z spapi_inventario amazon_us ===
spapi_inventario amazon_us: run=1006 escritas=1071 omitidas=0 paginas=22 aviso=None llamadas=22 segundos=15.5
=== 2026-10-22T05:02:38Z spapi_pricing amazon_mx ===
spapi_pricing amazon_mx: run=1007 escritas=342 omitidas=0 asins=342 llamadas=684 segundos=1372.6
=== 2026-10-22T05:25:31Z spapi_pricing amazon_us ===
spapi_pricing amazon_us: run=1008 escritas=176 omitidas=0 asins=176 llamadas=352 segundos=702.6
=== 2026-10-22T05:37:15Z fin ===
Thu Oct 22 07:33:34 AM UTC 2026
```
