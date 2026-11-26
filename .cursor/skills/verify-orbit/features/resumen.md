# Resumen

Resumen presenta el contrato multicanal de publicidad. Por omisión entran todos
los canales (Amazon MX, Amazon US y Mercado Libre) y 30 días, sin el día en
curso. Cada canal conserva su moneda. None se ve como raya. El cero se ve como
0. Mercado Libre no hereda la ventana D-8 de Amazon.

## Sub-features

- `resumen-nav` abre `/` desde el sidebar (`aside.sidebar`) y deja `aria-current="page"` en Resumen. Los destinos agrupados usan `nav.subnavegacion`; Resumen, como destino de primer nivel, no muestra una subnavegación propia. En móvil, `nav.tab-bar` tiene cinco controles: Resumen, Campañas, Oportunidades, Decisiones y el botón `Más` (`#nav-mas-toggle`), que abre `nav#nav-mas`.
- `resumen-leyenda` el header (`header.pagina-header`) trae `span.leyenda-zona` «Horas del centro de México», igual que toda pantalla del dashboard. Las horas se ven «8 nov, 11:58», sin la palabra UTC. La acción `.accion-primaria` abre `/campanas/nuevas`.
- `resumen-filtros` es el primer bloque de contenido y muestra píldoras de canal (`Todos`, `Amazon MX`, `Amazon US`, `Mercado Libre`), píldoras de periodo (`7 dias`, `30 dias`, `90 dias`), y `<details class="custom-range">` plegado con `form.filtros` (`desde`/`hasta`). Al enviar, la elección queda en la URL y en los controles.
- `resumen-que-revisar` (`.resumen-pendientes.superficie`, `Pendientes de revisión`) sigue inmediatamente después de los filtros y lista diagnósticos priorizados calculados desde las cifras del periodo elegido con enlaces a `/gasto-sin-venta`, `/busquedas-sin-venta`, `/campanas` y `/decisiones`.
- `resumen-monedas-principales` (`.resumen-monedas-principales`) presenta los mismos `contrato.totales_por_moneda` antes de la gráfica, con cuatro columnas de KPI cuando hay espacio y dos en tableta y móvil. No suma monedas ni crea métricas.
- `resumen-tendencia` (`Tendencia · ventas vs gasto`) abre `.resumen-principal` y presenta una tarjeta de gráfica con pestañas por canal (`button[data-tab-canal]`) y `canvas#tendencia-<canal>` dentro de `.lienzo`. Lee `script#datos-resumen`. El día va fino y `ma7` es la línea gruesa. `null` es hueco.
- `resumen-tarjetas` (`aside.resumen-canales`, `Canales`) ocupa la segunda columna de `.resumen-principal` a más de 1050 px y muestra 1 tarjeta por canal visible con etiqueta `Bien` / `Atención` / `Sin dato`, ACoS grande con variación y valor previo, `Meta ACoS` con enlace `Configurar` a `/settings`, mini-sparkline SVG de `ma7.ad_revenue`, 3 mini-KPIs (`Ventas`, `Gasto`, `ROAS`) y frase resumen. Cuando el canal publica madurez (Amazon, `comparison.observado_hasta` no es `null`), bajo la frase va `p.ch-comparacion` con «La comparación usa el periodo anterior tal como se veía hace <dias> días, para que los dos lleven el mismo retraso en las ventas que todavía llegan.».
- `resumen-monedas` (`Totales por moneda`) conserva dentro del detalle las tarjetas oscuras `.curr-card` para `MXN` y `USD`, con su cobertura y sin mezclar divisas.
- `resumen-detalle` (`Tablas detalladas y series diarias`) contiene los totales con cobertura y pliega con `<details class="detail-box">` la `Tabla por canal`, `Periodo anterior por canal` y las tablas `#serie-<canal>` (`Datos diarios · <canal>`). El antiguo `footer.footer-links` no se muestra porque duplica el shell.
- `resumen-api` el Resumen consume `dash.resumen_ads` (`GET /api/dashboard/resumen?canal=amazon_us`), donde `canales.amazon_us.moneda` es `USD` y `canales.amazon_us.series` entrega la serie diaria con huecos en `null`.

## How to get to it (user POV)

- Abrir `http://127.0.0.1:<puerto>/`.
- Elegir el enlace `Resumen` del sidebar (`<a href="/">` dentro de `aside.sidebar`).
- Cambiar canal, periodo o rango y pulsar `Filtrar`.

## Driving it with curl

Preconditions:

- Doctor en verde sobre la instancia de este run.
- Semilla presente (amazon_us tiene al menos un dia con cost/revenue).

- **Abrir Resumen.** Corre `curl -sS -D - "$BASE/"`. Status 200. El HTML contiene `data-pantalla="resumen"`, `<title>` `Orbit — Dashboard`, `<h1>` `Resumen`, `aside.sidebar`, `nav a[href="/"]` con `aria-current="page"`, los destinos padre `Oportunidades`, `Decisiones`, `Rendimiento`, `Catálogo`, `Salud` y `Configuración`, `html[data-tema="dia"]`, `button#btn-tema` y `class="leyenda-zona"` con `Horas del centro de México`. En móvil, `nav.tab-bar` tiene Resumen, Campañas, Oportunidades, Decisiones y el botón `Más`; `nav#nav-mas` contiene Rendimiento, Catálogo, Salud y Configuración. También la acción `.accion-primaria[href="/campanas/nuevas"]`, `Resumen de publicidad`, `value="" selected`, `value="30" selected`, `.resumen-pendientes`, `.resumen-monedas-principales`, `.resumen-principal`, `aside.resumen-canales`, `id="datos-resumen"`, `Tabla por canal`, `Periodo anterior`, `id="tendencia-amazon_us"`, `id="serie-amazon_us"`, `media de 7 dias` y `Amazon MX · Amazon US · Mercado Libre`. Pendientes aparece antes que las métricas principales y estas antes que `.chart-card`; no existe `footer.footer-links`. La línea de la tarjeta trae `en las ventas que todavía llegan` (solo ahí; «tal como se veía hace» sale también del caption y no prueba nada) y, aparte, el caption nuevo `En Amazon, el periodo anterior se mide tal como se veía hace los mismos días`.
- **Cambiar canal y periodo.** Corre `curl -sS "$BASE/?canal=meli&periodo=7"`. Status 200. El HTML contiene `value="meli" selected` y `value="7" selected`. El tramo de `<h2>Mercado Libre` no contiene `Cifras inmaduras`.
- **Periodo 90.** Corre `curl -sS "$BASE/?periodo=90"`. Status 200 y `value="90" selected`.
- **Rango.** Corre `curl -sS "$BASE/?desde=2026-10-01&hasta=2026-10-07"`. Status 200, las dos fechas en los inputs y el texto `rango personalizado`.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/resumen?canal=amazon_us"`. Status 200. En `canales.amazon_us`, `plataforma` es `amazon_us`, `moneda` es `USD`, y en `canales.amazon_us.series` el día sembrado tiene `cost` `12.3400` y `ad_revenue` `45.6700`. Un hueco sigue en `null`.
- **Proof.** Guarda el HTML de `/`, los filtros, el JSON de la serie y las capturas (1280, 7, 90, rango, Mercado Libre, 390 px y tema noche) bajo `evidence/<run_id>/resumen/`. A 390 px el body no se desplaza y `.tabla-scroll` sí. El foco de teclado en esa tabla deja el outline visible.

## Gotchas

- Sin `ORBIT_DSN_READ` esta ruta es 503: no es un fallo de template, es fail-closed de conexion.
- `amazon_mx` puede no tener filas de metrica: igual debe existir el bloque. Hueco = `null` en el contrato y raya en la tabla, no un 0 pintado.
- ACoS con revenue 0 conocido sigue `null` en el contrato. No se pinta 0%.
- Chart.js vive en `/static/vendor/chart.umd.min.js`. La CSP `default-src 'self'` bloquea CDN.
- La gráfica no mezcla MXN y USD: hay un canvas por canal.
- A 390 px solo `.tabla-scroll` se desplaza en horizontal.
