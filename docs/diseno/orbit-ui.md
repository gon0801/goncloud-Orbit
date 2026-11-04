# ORBIT 18 — Rediseno del dashboard

Referencia: `Orbit app UI mockups.zip`, handoff del dueno del 2026-09-06.
La implementacion usa Jinja2, CSS local y Chart.js vendoreado. No incorpora
el runtime `support.js` del prototipo.

## Entrega

- Nueve pantallas existentes con sidebar agrupado, encabezado propio, paleta
  morada, tipografia sans, tarjetas, tablas y temas dia/noche.
- Datos monetarios separados por plataforma/moneda. KPI de resumen calcula
  sumas Decimal y ACoS ponderado por ingreso; los dias sin observacion se
  declaran por metrica y no se reemplazan por cero. ACoS exige coberturas
  compatibles de gasto e ingreso. Sparklines conservan huecos.
- Sidebar consulta las APIs existentes de Salud y Propuestas. Fallo de
  lectura se muestra como no disponible; un contador cero es un dato valido.
- Filtros adicionales de campanas plegables, sin perder sort ni filtros GET.
  Decisiones presenta anterior → nuevo con moneda en una celda.
- Inertes conserva resumen de clasificacion y lotes. El KPI dice
  "Antiguedad cumplida": eso solo no asegura que una entidad sea archivable.
- Total de cinco presupuestos con centavos BigInt. Campo incompleto no se
  convierte en cero. Cambiar plataforma limpia los importes existentes.
- Formularios de rechazo, settings y creacion conservan la autenticacion,
  confirmaciones y avisos existentes. Rechazar sigue siendo irreversible.
- Tablas con scroll propio y foco de teclado; navegacion compacta en movil.

## Limites respecto al prototipo

Repricing requiere modelo, sincronizacion y reversas, fuera del cambio de piel;
se muestra como proximo modulo, al igual que Reviews y Reputacion. Amazon Ads
es el unico canal actual: el alcance MX + US es una etiqueta, no un selector
que simule filtrar canales inexistentes.

No se dibuja un target historico inventado: las series actuales no traen el
target efectivo por fecha. El target por campana conserva su fuente actual en
Campanas y Settings. No se agregan APIs ni consultas SQL para este rediseno.

El prototipo suma monedas en una tarjeta de ejemplo; Orbit mantiene MXN y USD
separados, sin conversion implicita. El gasto de Inertes es suma de importes
observados, con la moneda visible.

## Validacion

Pruebas focalizadas de templates, XSS, paginacion, paleta, KPI y flujo de fabrica.
Regresiones de la nueva presentacion demostradas en rojo antes del cambio.
Ruff y pre-commit locales. Bateria completa y harness con PostgreSQL en Quality
CI del PR. Comprobacion visual local con Edge sobre base desechable del harness.

## Correccion de cache tras el despliegue

Un navegador con CSS/JS previos guardados mostraba el HTML nuevo con la piel
anterior. Todos los CSS/JS locales llevan ahora `?v=<huella>` calculada desde
sus contenidos al arrancar la app. Un cambio de archivo cambia la URL sin
actualizar una version manual. `/static/` exige revalidacion mediante
`Cache-Control: no-cache`, incluso en respuestas 304; el HTML sigue no-store.
La prueba incluye contenido distinto con igual tamano/mtime y navegador con
CSS anterior en cache. Los deploys deben reiniciar la app tras copiar codigo.

## Fotos de publicaciones al crear campanas

Cada ASIN del selector muestra su imagen MAIN del marketplace elegido.
Fuente: SP-API Catalog Items 2022-04-01, GET por ASIN con includedData=images;
contrato y credenciales amazon_credentials.json existentes verificados en produccion.
Referencia: https://developer-docs.amazon/sp-api/lang-US/docs/catalog-items-api-rate-limits

El navegador carga miniaturas bajo demanda mediante Orbit (misma CSP). El servidor
solo descarga raster de hosts de imagen Amazon permitidos, sin redirecciones,
con limite de 256 KiB. Cache en memoria: maximo 128 fotos durante 24 horas;
ausencias 15 minutos y fallos 60 segundos. Consultas serializadas, separadas
por al menos 0.6 segundos; las fotos faltantes muestran Sin foto sin afectar
la elegibilidad ni la seleccion. No se escribe en Amazon ni se altera su cliente Ads.
