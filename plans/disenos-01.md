# DISEÑOS 01: Decisiones, Salud y Contribución con el diseño nuevo

Base: rama desde `origin/master` al día. Sin migraciones.
Precondición: este plan y `docs/disenos/disenos-01/` llegan juntos a master antes de entregar el encargo.
Para quién: el dueño, que usa Orbit sin conocimientos técnicos. Las tres pantallas cambian cómo se ven, no qué datos muestran.
Fuente del diseño: cada pantalla tiene su carpeta `docs/disenos/disenos-01/<pantalla>/` con `INSTRUCCIONES-<pantalla>.md` (manda), `<pantalla>-diseno.png` (cómo se ve a 1440 px) y `<pantalla>-diseno.dc.html`. Ese HTML es el formato de un editor de diseño (`<x-dc>`, `{{holes}}`, `<sc-for>`): no se pega, es la referencia exacta de textos, colores, tamaños y espaciados. Las instrucciones citan un `INSTRUCCIONES-resumen.md` que no viene: el sistema visual es el Resumen ya desplegado (RESUMEN ADS 04, PR #524). Se reutilizan sus clases, colores, sidebar y comportamiento en móvil; no se duplican.

Decisiones del dueño (2026-11-11). Cierran las preguntas que dejan abiertas las instrucciones:
1. El filtro por tipo y el buscador de Decisiones buscan en todas las decisiones, no solo en la página. Son parámetros de consulta en el servidor, de solo lectura, y la paginación los conserva.
2. El tipo `harvest` (16 decisiones, la última del 2026-10-29) se muestra con el chip «Captura» en azul: fondo `#DBEAFE`, texto `#1E40AF`.
3. Las dos columnas de «Lo que se saltó» en Salud se titulan «Keywords y productos» (clave `entidad`) y «Búsquedas» (clave `termino`).
4. En Salud, un estado que no sea «Escrito», «ok» o «Completo» se marca en ámbar.
5. Contribución usa la escala recortada con «▸ valor real» en la barra que se sale. Una escala logarítmica no puede dibujar negativos ni el 0.

Reglas para los tres bloques:
- Solo cambia la presentación. Mismos datos, mismos textos y ningún campo perdido respecto a la página de hoy.
- Las tablas se hacen con `<table>`, no con `div` en grid.
- Cifras, fechas y códigos pasan por `app/formato.py` y `app/etiquetas.py`. `tools/pantallas_claras.py` sobre la ruta del bloque sigue en 0.
- La ficha `.cursor/skills/verify-orbit/features/<pantalla>.md` se actualiza con los selectores y flujos nuevos. La pantalla se comprueba con `.cursor/skills/verify-orbit/helpers/orbit-verify`, con una captura a 1440 px y otra a 390 px.
- Pruebas nuevas: ninguna, porque ningún bloque cae en los 4 casos del repo. Las pruebas que hoy fijan el HTML viejo de la pantalla se ajustan al nuevo y conservan lo que afirman de los datos.
- Evidencia en `docs/evidencia/disenos-01/<bloque>/`: la página de producción antes del cambio y la comparación de conteos y valores después del despliegue.

## Bloque A — Decisiones por día, con filtro por tipo y buscador
lane: release
depende: -
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: La página Decisiones agrupa las decisiones por día, junta en una fila las pausas y negativos repetidos y permite filtrar por tipo y buscar por campaña, keyword o ASIN en todas las decisiones.
prueba-real: python3 tools/pantallas_claras.py --base http://10.13.13.1:8010 --rutas /decisiones --tope 0
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | Antes de tocar código, guardar de producción el HTML de `/decisiones` y `/decisiones?page=2` y el total de decisiones. | Los tres archivos existen en `docs/evidencia/disenos-01/A/antes/` |
| A.2 | Pantalla según `INSTRUCCIONES-decisiones.md` y el PNG: encabezado con paginación arriba y abajo, «En esta página» con 4 contadores, línea de tiempo por día, filas plegables solo para pausas y negativos del mismo día con el mismo tipo y motivo, pujas siempre una por fila con bid antes → después. Chip por tipo con los colores de las instrucciones y «Captura» para `harvest` (decisión 2). | `orbit-verify` maneja `/decisiones` y la ficha nueva pasa |
| A.3 | Filtro y buscador sobre todas las decisiones (decisión 1): `tipo` en `todas`, `pausa`, `puja_sube`, `puja_baja`, `negativo` y `captura`, con puja que sube o baja según bid después contra bid antes; `q` busca en campaña, entidad, search term y ASIN. La paginación conserva `tipo` y `q`. Cada pestaña muestra cuántas decisiones de ese tipo cumplen la búsqueda; «En esta página» cuenta solo la página. Un `tipo` desconocido se trata como `todas`. El JSON `GET /api/dashboard/decisiones` no cambia. | `curl` de `/decisiones?tipo=negativo` da el mismo total que `select count(*) from decision where kind = 'negative'` por `lee_prod.sh` |
| A.4 | Después del despliegue, comparar contra A.1: mismo total, las mismas 50 decisiones de la página 1 con los mismos valores, y un día partido entre dos páginas muestra su encabezado en ambas. | `docs/evidencia/disenos-01/A/comparacion.md` sin diferencias |

## Bloque B — Salud con resumen por plataforma y detalle en pestañas
lane: release
depende: A
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: La página Salud abre con una tarjeta por plataforma que dice de un vistazo si algo necesita atención, y el detalle de cada plataforma va en su propia pestaña.
prueba-real: python3 tools/pantallas_claras.py --base http://10.13.13.1:8010 --rutas /salud --tope 0
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | Antes de tocar código, guardar de producción el HTML de `/salud` y el JSON de `/api/dashboard/salud`. | Los dos archivos existen en `docs/evidencia/disenos-01/B/antes/` |
| B.2 | Tarjeta por plataforma según `INSTRUCCIONES-salud.md`, con los valores calculados de los datos que ya trae la página. Punto ámbar solo si hay filas saltadas, avisos de «se queda sin presupuesto» o un estado distinto de «Escrito», «ok» o «Completo» (decisión 4). Una plataforma nueva en los datos saca su tarjeta y su pestaña sin tocar código. | `orbit-verify` maneja `/salud` y la ficha nueva pasa |
| B.3 | Detalle en pestañas por plataforma: barras de los 14 ciclos con la tabla plegada, avisos de campaña agrupados, «Lo que se saltó» con barras horizontales desde el mismo `datos-skips-<plataforma>` y columnas «Keywords y productos» y «Búsquedas» (decisión 3), tablas completas de Ingesta Ads y SP-API, y las tarjetas de Último ciclo, Cambios permitidos hoy y Precios. La tarjeta de Jev va al final con sus 4 líneas. El aviso de Telegram caído (`p.alerta`) sigue apareciendo. | El HTML nuevo contiene cada valor del JSON de B.1 para las dos plataformas |
| B.4 | Después del despliegue, comparar cada valor contra la página de antes en las dos plataformas. | `docs/evidencia/disenos-01/B/comparacion.md` sin diferencias |

## Bloque C — Contribución con barra de rango por campaña
lane: release
depende: B
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: La página Contribución muestra cuántas campañas ganan, pierden o pueden ir para cualquier lado, y una barra por campaña con su rango frente al cero. Sigue sin decidir nada.
prueba-real: python3 tools/pantallas_claras.py --base http://10.13.13.1:8010 --rutas /contribucion --tope 0
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | Antes de tocar código, guardar de producción el HTML de `/contribucion` y el JSON de `/api/dashboard/contribucion`. | Los dos archivos existen en `docs/evidencia/disenos-01/C/antes/` |
| C.2 | Tarjeta por plataforma y tabla con barra de rango según `INSTRUCCIONES-contribucion.md`: clase por signo, línea en 0, eje por plataforma y la escala recortada con «▸ valor real» (decisión 5). Una fila con mínimo igual al máximo se ve como punto y una fila sin dato no dibuja barra. «no decisoria» sigue visible. En móvil la tabla se desplaza dentro de su caja. | `orbit-verify` maneja `/contribucion` y la ficha nueva pasa |
| C.3 | Después del despliegue, comparar cada fila contra la página de antes. | `docs/evidencia/disenos-01/C/comparacion.md` sin diferencias |
