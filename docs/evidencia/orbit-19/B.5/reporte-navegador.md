# ORBIT 19 / B.5 — Verificacion en navegador (lead)

Fecha: 2026-09-07. DB sembrada `orbit_b5_comparador` (tools/semilla_comparador.py),
app local en puerto 8765 (uvicorn). Dos navegadores: IAB de ZCode (interaccion,
snapshot ARIA) y agent-browser/Chromium headless (capturas).

Correcciones del lead durante la verificacion (regresiones reales del navegador):

1. `app/static/js/fabrica.js`: `NodeList.map` no existe en el navegador; el
   comparador reventaba con "porId(...).querySelectorAll(...).map is not a
   function" y la tabla no se renderizaba. Envuelto en `Array.from(...)`.
   El test de Node no lo atrapo porque su mock de NodeList tiene `.map`.
2. `tools/semilla_comparador.py`: los ASIN sembrados tenian 11 caracteres
   (B0AAAAAAA01...); el normalizador de Fase A los rechazaba como invalidos y
   dejaba 0 publicaciones seleccionables (AC10 no verificable). Corregidos a
   10 caracteres (B0AAAAAA01...).

## Resultados por caso

- AC6 (igual ACoS, muestras distintas): B0BBBBBB01 ACoS 25 % "Dentro del
  objetivo", muestra 1 fecha; B0BBBBBB02 ACoS 25 % "Dentro del objetivo
  (provisional)", muestra 10 fechas. Misma etiqueta, conteos distintos
  visibles. Ausencia (B0AAAAAA02) = "Sin datos", no 0 ni Por probar.
- AC8 (stock 0 / viejo / desconocido): "Stock en 0: FBA 0", "Con stock: FBA 12
  (desde 2026-08-08)" (viejo, frescura visible) y "Desconocido" en tres filas
  distintas. Featured Offer: "Sin verificar" en todas. Ninguno bloqueo la
  seleccion.
- AC10 (orden/filtros no pierden seleccion): con listing-3 marcado, se cambio
  orden a ACoS asc, direccion asc y filtro "Con datos de Ads". La casilla del
  paso 1 siguio checked y la columna Seleccion muestra "Seleccionada". La fila
  "Sin datos" quedo fuera de la tabla filtrada y al final del orden. Cero POST
  comerciales (el comparador solo hace GET /api/fabrica/evaluacion).
- Etiquetas observadas: "Dentro del objetivo", "Por encima del objetivo",
  "Gasto sin ventas" (con ACoS "Sin dato"), "Datos sin etiqueta (provisional)",
  "Sin datos". "Por probar" no aparece (regla 0.4).
- Ventana/grano/objetivo/muestra limitada visibles en el resumen dl: ventana
  2026-08-07 a 2026-09-06, grano publicacion (ASIN+SKU), objetivo 25.00 %,
  "Muestra limitada: visible aparte; no entra al orden (D4)".

## Capturas

- `capturas/ac10-comparador-orden-filtro.png` — pagina completa con orden
  ACoS asc + filtro aplicados y seleccion preservada.
- `capturas/ac6-ac8-comparador-tabla.png` — tabla del comparador con AC6 y AC8.

## Nota de accesibilidad

El clic programatico de Playwright sobre la casilla (input real dentro de
label) expiro en el IAB, pero el input es un checkbox nativo visible y
habilitado (verificado por computed style y click via DOM); el camino de
teclado nativo sigue disponible. Region con aria-labelledby, tabla semantica
y status con role=status presentes en el snapshot ARIA.
