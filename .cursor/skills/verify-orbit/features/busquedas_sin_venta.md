# Búsquedas que gastan sin vender (`/busquedas-sin-venta`)

Resume páginas de producto y palabras que superan el gasto mínimo sin vender en campañas encendidas. Usa 90 días maduros y deja fuera los últimos 10; no crea negatives en Amazon.

## Sub-features

- `busquedas-nav` entra desde Resumen por `Oportunidades` y después `Búsquedas sin vender` en `nav.subnavegacion`. El padre del sidebar usa `aria-current="true"`; `/busquedas-sin-venta` en la subnavegación y `Oportunidades` en la tab bar usan `aria-current="page"`.
- `busquedas-mercado` conserva los enlaces GET `?plataforma=amazon_mx|amazon_us` y su `aria-current="page"`.
- `busquedas-contenido` usa `section.oportunidad-resumen.superficie`, `section.oportunidad-contenido` y `section.oportunidad-detalle`. Las tablas de ASIN y palabras viven en `.tabla-scroll[role="region"][tabindex="0"]`; la referencia de azar y los casos `A medio camino` conservan sus textos.
- `busquedas-vacio` sin referencia dice `Sin pedidos o sin clics en estos 90 días: no hay referencia de azar.` Las listas vacías dicen `Ninguna página de producto pasó el mínimo sin vender.` y `Ninguna palabra pasó el mínimo sin vender.` Son los estados de la semilla baseline.

## Driving it with curl

```bash
curl -sS "$BASE/"
curl -sS "$BASE/busquedas-sin-venta?plataforma=amazon_us"
curl -sS "$BASE/api/dashboard/busquedas-sin-venta?plataforma=amazon_us"
```

Con `doctor` verde, las tres rutas responden 200. El JSON gemelo `GET /api/dashboard/busquedas-sin-venta` trae la referencia, ASIN, palabras y conteos a medio camino del mismo mercado.

## Operaciones prohibidas en baseline

No hay operación POST en esta pantalla. No agregues negatives ni ejecutes escrituras a partir de sus ejemplos: el baseline solo lee.
