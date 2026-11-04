# ORBIT 19 / B.6 — Validacion final y despliegue del bloque B

2026-09-07 (lead). PR #190 (8b4f7fe + 8b407cc + docs) y PR #191 (fix de poll),
ambos con CI quality verde (runs 34145272233 y 34147994585) y mergeados a
master (4aa1912 -> 7331740).

## Despliegue

- Backup de esquema pre-migracion: `backups/pre002x_orbit19_b_20260907-165805.sql`
  (validado: CREATE TABLE + marcador de cierre; 227,017 B).
- Migraciones aplicadas en transaccion unica sobre la base viva: 0020, 0021,
  0022. Verificado: las 3 tablas/vistas existen, `v_margen_producto` intacta,
  trigger `apm_metric_moneda_sellada` armado. 0 lotes/grupos antes y despues.
- Codigo desde origin/master (git archive, LF); respaldo previo
  `app.bak-predeploy-b-20260907-165920`; `docker compose up -d --no-deps --build
  app` -> Recreated; orbit-db-1 sin recrear (Up 10 days durante todo el
  despliegue). SHA desplegado: 7331740 (post PR #191).
- /health ok; binds solo 127.0.0.1:8010 y 10.13.13.1:8010; secrets/ 700/600
  intactos.

## Smoke productivo (solo GET; cero creacion de campanas)

- GET /campanas/nuevas -> 200; GET /api/fabrica/catalogo?plataforma=amazon_mx -> 200.
- GET /api/fabrica/evaluacion?plataforma=amazon_mx -> 200 con datos reales:
  economia madura (ej. margen 43.31 %, cobertura 1.0), disponibilidad real en
  las 342 publicaciones (snapshot bridge del dia), Ads con etiquetas reales
  tras la primera ingesta (161 con datos: sin_datos=84, gasto_sin_ventas=77,
  ACoS presente; sin "por_probar").

## Conciliacion externa

- B.1: `docs/evidencia/orbit-19/B.1/conciliacion-viva.md` (US exacto en
  clicks/cost/sales; MX ventas exactas, clicks -5 / cost -33.08 MXN por el
  hueco solo-actividad, nunca por encima).
- B.3: `docs/evidencia/orbit-19/B.3/reporte-pruebas.md` seccion "Resultado en
  vivo" (546 filas reales, 2046 SKUs sin listing contados y excluidos).

## Incidente y fix

Primeras corridas --productos abortaron fail-closed (reporte PENDING ~25 min >
poll 10 min; reintento 425 por duplicado). Fix PR #191 (poll 300x5s solo para
spAdvertisedProduct, con regresion). Cron diario instalado a las 07:20 UTC
(`ingest:metrics:productos`, respaldo del crontab previo en archive/); entrada
documentada en docs/DEPLOY.md.

## Pendientes declarados (fuentes opcionales / ampliaciones)

- Featured Offer: Sin verificar (ampliacion abierta de B.3; no bloquea).
- Residual MX solo-actividad (clicks -5, cost -33.08 del 2026-09-06): los
  ausentes siguen siendo Sin datos hasta cobertura demostrada.
- "Por probar" sigue sin asignarse (regla 0.4).
- Sonda de gasto real (crear campanas): sigue diferida, NO autorizada aqui.

AC1-AC5/AC7/AC9 quedaron cubiertos por la Fase A (E/A.x); AC6/AC8/AC10 por
E/B.5. Cero mutaciones comerciales a Amazon en todo el bloque B.
