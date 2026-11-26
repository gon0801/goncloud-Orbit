# Detalle de ciclos (`/ruido`)

El H1 y el H2 visibles dicen «Detalle de ciclos». Detalla decisiones y abstenciones por ciclo, la reducción de pujas en 90 días y una comparación simple antes/después del encendido. La comparación no se presenta como causal.

## Sub-features

- `ruido-nav` entra desde Resumen por `Decisiones` y después `Detalle de ciclos` en `nav.subnavegacion`. El padre del sidebar usa `aria-current="true"`; `/ruido` en la subnavegación y `Decisiones` en la tab bar usan `aria-current="page"`.
- `ruido-mercado` conserva los enlaces GET `?plataforma=amazon_mx|amazon_us` con `aria-current="page"` en el elegido.
- `ruido-contenido` agrupa los ciclos en `section.historial-resumen.superficie` y mantiene `.tarjeta`, motivos traducidos, abstenciones, prácticas, encogimiento y el antes/después.
- `ruido-vacio` puede mostrar `Sin ciclos del motor en este mercado.`, `Sin keywords ni productos con cambios de puja en los últimos 90 días.` y `Sin fecha de encendido: todavía no hay comparación de antes y después.` Son los estados baseline de Amazon MX.

## Driving it with curl

```bash
curl -sS "$BASE/"
curl -sS "$BASE/ruido?plataforma=amazon_mx"
curl -sS "$BASE/api/dashboard/ruido?plataforma=amazon_mx"
```

Con `doctor` verde responden 200. El JSON gemelo `GET /api/dashboard/ruido` entrega ciclos, encogimiento y `antes_y_despues` del mismo mercado; los huecos siguen siendo `null`.

## Operaciones prohibidas en baseline

Ruido no expone formularios POST. No ejecutes cambios de puja ni escrituras desde su historial; el recorrido común solo hace GET.
