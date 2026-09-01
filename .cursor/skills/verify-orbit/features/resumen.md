# Resumen

Resumen muestra las series diarias de spend, revenue y ACoS de `amazon_us` (USD) y `amazon_mx` (MXN) en la ventana D-30..D-1. El dia en curso no aparece. Los dias inmaduros (D-8..D-1) van marcados.

## Sub-features

- `resumen-nav` abre `/` desde el header y deja `aria-current="page"` en Resumen.
- `resumen-series` renderiza un bloque por plataforma con ventana efectiva e inmaduros.
- `resumen-canvas` declara `canvas#serie-<plataforma>` y `canvas#serie-acos-<plataforma>` leyendo `script#datos-serie-<plataforma>`.
- `resumen-api` el JSON `GET /api/dashboard/series/plataforma?platform=amazon_us` describe la misma serie.

## How to get to it (user POV)

- Abrir `http://127.0.0.1:<puerto>/`.
- Elegir el enlace `Resumen` del nav (`<a href="/">`).

## Driving it with curl

Preconditions:

- Doctor en verde sobre la instancia de este run.
- Semilla presente (amazon_us tiene al menos un dia con cost/revenue).

- **Abrir Resumen.** Corre `curl -sS -D - "$BASE/"`. Status 200. El HTML contiene `data-pantalla="resumen"`, `h1` Orbit — Dashboard, y `nav a[href="/"]` con `aria-current="page"`.
- **Ver series.** El HTML contiene `h2` `amazon_us (USD) — spend y revenue` y `amazon_mx (MXN) — spend y revenue`, mas `Ventana efectiva:` y `inmaduros D-8..D-1`.
- **Ver handles de grafica.** El HTML contiene `id="serie-amazon_us"`, `id="serie-acos-amazon_us"`, `data-serie="datos-serie-amazon_us"` y `<script type="application/json" id="datos-serie-amazon_us">`.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/series/plataforma?platform=amazon_us"`. Status 200. `plataforma` es `amazon_us`, `moneda` es `USD`, `series` es un spine de fechas, y el dia sembrado (`seed.metric_date`) tiene `cost` `12.3400` y `ad_revenue` `45.6700`.
- **Proof.** Guarda el HTML de `/` y el JSON de la serie bajo `evidence/<run_id>/resumen/`. El body sigue en `data-pantalla="resumen"` y el JSON no inventa 0 donde hay hueco (`null`).

## Gotchas

- Sin `ORBIT_DSN_READ` esta ruta es 503: no es un fallo de template, es fail-closed de conexion.
- `amazon_mx` puede no tener filas de metrica: igual debe existir el bloque de plataforma (el template itera `PLATAFORMAS_MONEDA`). Hueco = spine con null, no un 0 pintado.
- ACoS con revenue 0 conocido se ve como `sin ventas` / `acos` null, nunca 0%.
- Chart.js vive en `/static/vendor/chart.umd.min.js`. La CSP `default-src 'self'` bloquea CDN: un HTML con `https://` en el dashboard es regresion.
