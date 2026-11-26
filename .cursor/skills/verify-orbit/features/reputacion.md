# Reputación (`/reputacion`)

Presenta rating y reviews verificadas de MeLi, preguntas pendientes, alertas, listings y cuenta MeLi. Amazon aparece como `sin verificar`; la versión no responde preguntas ni importa reviews de Amazon.

## Sub-features

- `reputacion-nav` entra desde Resumen por `Catálogo` (`href="/precios"`) y después `Reputación` en `nav.subnavegacion`. El padre del sidebar usa `aria-current="true"`; `/reputacion` en la subnavegación y `Catálogo` dentro de `nav#nav-mas` usan `aria-current="page"`.
- `reputacion-resumen` conserva `section.catalogo-resumen.superficie`, los cuatro KPI, `.rep-estrellas`, alertas y severidades.
- `reputacion-filtros` es `form.rep-toolbar[action="/reputacion"][method="get"]` con `input[name="q"]`, `select[name="plataforma"]`, `select[name="tendencia"]` y Filtrar. La tabla usa `.tabla-reputacion` dentro de `.tabla-scroll[tabindex="0"]`.
- `reputacion-listas` mantiene `.tarjeta-lista`, `.rep-fila`, `.rep-vacio`, reviews recientes, preguntas y cuenta MeLi. Sin observaciones, las regiones muestran `Sin datos`; es el estado baseline.

## Driving it with curl

```bash
curl -sS "$BASE/"
curl -sS "$BASE/reputacion"
curl -sS "$BASE/reputacion?q=MLM&plataforma=meli&tendencia=estable"
curl -sS "$BASE/api/reputacion/resumen"
curl -sS "$BASE/api/reputacion/contador"
```

Con `doctor` verde responden 200. `GET /api/reputacion/resumen` es el JSON gemelo sin los filtros locales de la tabla; `/contador` alimenta el contador del shell. No afirmes que el JSON aplica `q`, `plataforma` o `tendencia`.

## Operaciones prohibidas en baseline

La pantalla es de lectura y no ofrece POST. No respondas preguntas ni escribas reviews, alertas o cuenta mediante APIs externas; `Responder en MeLi` es solo una indicación.
