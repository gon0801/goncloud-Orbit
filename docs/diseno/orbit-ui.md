# ORBIT 18 — Rediseño del dashboard

Referencia visual: `Orbit app UI mockups.zip`, handoff del dueño del 2026-09-06.
La dirección aprobada está en la
[especificación del rediseño](../superpowers/specs/2026-11-25-rediseno-ui-orbit-design.md);
el mapa funcional y verificable de las 18 rutas vive en
[`.cursor/skills/verify-orbit/features/README.md`](../../.cursor/skills/verify-orbit/features/README.md).
La implementación usa Jinja2, CSS local y Chart.js vendoreado; no incorpora el
runtime `support.js` del prototipo.

## Sistema A2

Los tokens `--o-bg`, `--o-card`, `--o-ac`, `--o-ac2`, `--o-sombra` y
`--o-radio` definen los temas día/noche. `.superficie`, `.seccion-encabezado`,
`.grupo-metricas` y `.accion-primaria` comparten la jerarquía visual sin
reemplazar ids, names, `data-*`, formularios, APIs ni handlers funcionales.

El sidebar presenta Resumen, Campañas y los grupos Oportunidades, Decisiones,
Rendimiento y Catálogo, seguidos de Salud y Configuración. Cada grupo usa
`nav.subnavegacion`: el padre lleva `aria-current="true"` y el destino exacto
`aria-current="page"`. La tab bar móvil tiene cinco controles —Resumen,
Campañas, Oportunidades, Decisiones y Más—. `button#nav-mas-toggle` abre
`nav#nav-mas`; Escape lo cierra y devuelve el foco. Crear campañas sigue siendo
una acción de Campañas, no una sección adicional.

## Mapa por tareas

- **Resumen y Campañas:** `/`, `/campanas` y `/campanas/nuevas`. Resumen
  prioriza pendientes, monedas y tendencia; Campañas conserva filtros, orden y
  procedencia del target; la fábrica conserva su flujo de cinco campañas.
- **Oportunidades:** `/cortes`, `/gasto-sin-venta`, `/busquedas-sin-venta`,
  `/keywords-danadas`, `/productos-sin-clics` e `/inertes`. Las superficies
  separan explicación, contenido y detalle sin ocultar acciones ni datos.
- **Decisiones y Rendimiento:** `/decisiones`, `/ruido`, `/contribucion` y
  `/donde-poner-el-dinero`. Historial, ciclos, contribución y presupuestos
  mantienen sus consultas y operaciones actuales.
- **Catálogo:** `/precios`, `/reputacion` y `/familias`. Compuerta, cobertura,
  reputación y clasificación de productos usan `.catalogo-resumen` sin tarjetas
  vacías cuando la configuración es ilegible.
- **Sistema:** `/salud` y `/settings`. Las tarjetas por plataforma y la sesión
  de configuración usan `.sistema-resumen`; goals, caps, tabs, gráficas y
  autenticación permanecen intactos.

## Reglas de datos y operaciones

- Datos monetarios separados por plataforma/moneda. KPI de Resumen calcula
  sumas Decimal y ACoS ponderado por ingreso; los días sin observación se
  declaran por métrica y no se reemplazan por cero. ACoS exige coberturas
  compatibles de gasto e ingreso. Sparklines conservan huecos.
- El prototipo suma monedas en una tarjeta de ejemplo; Orbit mantiene MXN y USD
  separados, sin conversión implícita. El gasto de Inertes es suma de importes
  observados, con la moneda visible.
- Sidebar consulta las APIs existentes de Salud, Propuestas y Reputación. Fallo
  de lectura se muestra como no disponible; un contador cero es un dato válido.
- No se dibuja un target histórico inventado: las series actuales no traen el
  target efectivo por fecha. El target por campaña conserva su fuente actual en
  Campañas y Configuración. El rediseño no agrega APIs ni consultas SQL.
- Inertes conserva resumen de clasificación y lotes. El KPI dice
  «Antigüedad cumplida»: eso solo no asegura que una entidad sea archivable.
- Total de cinco presupuestos con centavos BigInt. Campo incompleto no se
  convierte en cero. Cambiar plataforma limpia los importes existentes.
- Formularios de rechazo, configuración, creación, familias y ajustes conservan
  autenticación, confirmaciones y avisos existentes. Rechazar sigue siendo
  irreversible. Ninguno se envía en el baseline de verificación.
- Tablas con scroll propio y foco de teclado; navegación compacta en móvil.

## Validación integral

`orbit-verify launch`, `doctor`, los drives específicos y `drive-secundarias`
recorren las 18 rutas sobre PostgreSQL desechable. Cada ruta debe responder 200,
mostrar su `data-pantalla` y navegación activa, conservar sus datos y no ejecutar
POST. Un 503 fail-closed no cuenta como verificación exitosa. La semilla de
Precios replica la configuración válida de `test_precio_pantalla.py` y deja la
cobertura vacía sin inventar listings ni goals.

Las pruebas cubren templates, XSS, paginación, paleta, KPI, fábrica y el harness.
El pase real guarda HTML, JSON cuando existe, capturas de escritorio, 390 px y
tema noche, y comprueba foco, apertura/cierre de Más y Escape en navegador real.
Ruff, pre-commit, la suite completa y `graphify update` cierran el bloque.

## CSP y caché

La CSP es `default-src 'self'`; CSS y JS viven en `/static`, sin handlers inline.
Los únicos bloques inline permitidos son datos inertes `application/json`.
Chart.js se sirve localmente, nunca desde CDN. Las imágenes remotas de
publicaciones pasan por el proxy de Orbit descrito abajo.

Un navegador con CSS/JS previos guardados mostraba el HTML nuevo con la piel
anterior. Todos los CSS/JS locales llevan `?v=<huella>` calculada desde sus
contenidos al arrancar la app. Un cambio de archivo cambia la URL sin actualizar
una versión manual. `/static/` exige revalidación mediante
`Cache-Control: no-cache`, incluso en respuestas 304; el HTML sigue `no-store`.
La prueba incluye contenido distinto con igual tamaño/mtime y navegador con CSS
anterior en caché. Los deploys deben reiniciar la app tras copiar código.

## Fotos de publicaciones al crear campañas

Cada ASIN del selector muestra su imagen MAIN del marketplace elegido. Fuente:
SP-API Catalog Items 2022-04-01, GET por ASIN con `includedData=images`; contrato
y credenciales `amazon_credentials.json` existentes verificados en producción.
Referencia: https://developer-docs.amazon/sp-api/lang-US/docs/catalog-items-api-rate-limits

El navegador carga miniaturas bajo demanda mediante Orbit (misma CSP). El servidor
solo descarga raster de hosts de imagen Amazon permitidos, sin redirecciones, con
límite de 256 KiB. Caché en memoria: máximo 128 fotos durante 24 horas; ausencias
15 minutos y fallos 60 segundos. Consultas serializadas, separadas por al menos
0.6 segundos; las fotos faltantes muestran Sin foto sin afectar la elegibilidad
ni la selección. No se escribe en Amazon ni se altera su cliente Ads.
