# Crear campanas - Rediseño del handoff UI (1)

2026-09-08 UTC. Referencia: `Orbit app UI mockups (1).zip`, README seccion
"Crear campanas - detalle del rediseño" y prototipo HTML actualizado el 8 sep.

## Alcance

- Tarjetas agrupadas por producto con foto 72px, checkbox, ASIN, SKU,
  margen neto antes de Ads, muestra, avisos y enlace externo fuera del label.
- Contador de seleccion y recarga en barra fija sobre el catalogo de 420px.
- Comparador: 14 columnas que conservan los 16 datos anteriores; seleccion
  y aviso se integran en la identidad. Cabeceras agrupadas, identidad fija,
  seis metadatos, chips de Ads/disponibilidad, margen limitado separado.
- Scroll contenido y cabeceras fijas; importes a dos decimales con redondeo
  decimal (BigInt), valor original y moneda disponibles en title.
- Sin cambios en endpoints, calculo del margen, elegibilidad, objetivos,
  creacion, confirmacion, recuperacion, DB o Reputacion.

## Comprobaciones

- Regresiones de UI ejecutadas primero sobre el codigo anterior: dos fallos
  esperados (contador/tarjeta y cabecera de dos niveles). El formato monetario
  tambien fallo antes de implementarse. GREEN: **13 pruebas de test_ui_fabrica.py**.
- JS real con DOM/transporte controlados: fotos/fallback, enlaces seguros fuera
  del label, contador, conservacion de 14 columnas y sus datos, cero vs ausencia,
  precision original del CPC, seleccion al ordenar/filtrar, objetivo manual y
  derivado, respuesta vieja fallida, creacion sin duplicados y recuperacion.
- Ruff 0.15.20, node --check y pre-commit --all-files: verdes.
- Chromium (agent-browser), plantillas/CSS/JS reales en servidor local de lectura
  con GET a Orbit por tunel. El proxy local rechaza toda escritura. No se hizo
  ninguna creacion ni se envio token. MX: 342 publicaciones; US: 176.
- Navegador: foto real, seleccion del listing 1461 preservada al filtrar y
  ordenar; objetivo manual consultado sin crear; temas dia/noche; ancho movil
  390px con document.scrollWidth=390px, sin overflow de pagina. Desktop 1440px,
  tabla con 14 columnas, encabezados e identidad sticky, enlaces fuera de label.
- Capturas locales de datos reales: selector-dia.png, comparador-dia.png,
  selector-movil.png y comparador-noche.png. El 25% visible es una entrada de
  formulario de la comprobacion local; no se guardo ni se lanzo una campana.
- La bateria completa se ejecuta una vez en la CI del PR; no se duplico localmente.
