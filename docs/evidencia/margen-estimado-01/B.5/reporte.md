# MARGEN ESTIMADO 01 · B.5 — despliegue y cierre

Fecha: 2026-09-08 UTC. Alcance: solo Orbit. No se crearon campanas ni se
modificaron Reputacion, bridge o accounting.

## Integracion

- PR de entrega: `#222`.
- Head revisado: `dc1e17e1e9a7e50ea9fc62dd01d6c26a1e46d096`.
- Merge en `master`: `9382fcb672a3df11a260692d1cf01b49cb085c6f`.
- CI completa: run `34274641944`, verde sobre el head exacto.
- Reviews: lead `APPROVE` y cross-review Grok `APPROVE`, sin hallazgos
  bloqueantes.

## Backup y migraciones

- Backup completo publicado a las `2026-09-08T21:01:05+00:00` en
  `backups/orbit_2026-09-08/`: dump `2,101,241` bytes y globals `3,236` bytes.
- `0028_estimacion_venta.sql` aplicada completa.
- `0029_estimacion_politica_vigencia.sql` aplicada completa; una politica
  `amazon_mx_pf_rfc_valid_2026_01`, universo `amazon_mx/fba`, formula `S3`.
- Tablas y permisos verificados para `app_read`, `app_ingest`, `app_decide` y
  `app_admin`.

## Aplicacion e ingesta

- Codigo copiado desde `origin/master` y verificado por checksum antes del
  build. Respaldo del binario anterior: `app.bak-predeploy-margen-20260908-2101`.
- `orbit-app-1` recreado; imagen anterior `030ace7...`, imagen nueva
  `2fbdb42...`.
- Primera ingesta: run `128`, `ok=true`, `rows_written=663`,
  `rows_skipped=121`; 221 ofertas, 221 cotizaciones Product Fees y 221
  escenarios nuevos; cero errores de fee. Los 121 skips son
  `logistica_fbm_pendiente`.
- Cron instalado para `refresh_estimacion.sh` cada seis horas a `:45`.
- EHV Tasks actualizado a `Done`: fila
  `38c7ea55-6b99-4d53-807e-321066b557d0`.

## Smoke y reversa

- `/health` 200 y `/campanas/nuevas` 200.
- Catalogo MX: 342 publicaciones; 221 estimaciones `disponible` y 121
  `incompleta`.
- 219 publicaciones con estimacion disponible no tienen margen observado;
  la estimacion no rellena ese campo.
- Catalogo y evaluacion devolvieron el mismo `as_of` y el mismo `snapshot_id`
  para las 342 publicaciones.
- Sin excepciones en logs y binds conservados en `127.0.0.1:8010` y
  `10.13.13.1:8010`.
- `estimacion_venta_reversa()` se ejecuto dentro de una transaccion y se hizo
  `ROLLBACK`; los permisos quedaron restaurados y no persistio fila de reversa.
- Permisos de secretos conservados: directorio `700`, archivos `600`, uid
  `10001`.

## Limites declarados

FBA MX es el unico universo numerico de esta version. FBM queda incompleto por
logistica dependiente del pedido y US queda incompleto por politica prospectiva
pendiente. La estimacion es informativa y no gobierna target, budget, bid,
placements, elegibilidad ni recuperacion.
