# Gasto sin venta (`/gasto-sin-venta`)

Agrupa búsquedas que gastan sin vender por lectura y, cuando aplica, por bandas. Las bandas solo ordenan la presentación; no se guardan ni alimentan decisiones.

## Sub-features

- `gasto-nav` entra desde Resumen por `Oportunidades` (`href="/cortes"`) y luego `Gasto sin venta` en `nav.subnavegacion`. El padre del sidebar usa `aria-current="true"`; el enlace `/gasto-sin-venta` de la subnavegación y `Oportunidades` en la tab bar móvil usan `aria-current="page"`.
- `gasto-mercado` cambia por GET entre `?plataforma=amazon_mx` y `?plataforma=amazon_us`; el mercado elegido marca `aria-current="page"`.
- `gasto-contenido` conserva `section.oportunidad-resumen.superficie`, `section.oportunidad-contenido`, `section.oportunidad-detalle`, las tarjetas por lectura y `.tabla-scroll[role="region"][tabindex="0"]` con Búsqueda, Gasto, Señal y Productos. En Señal, `.action-body` ocupa la columna disponible de `.senal-bloque .action-row`; la medición CDP compara su ancho con el espacio útil de la fila.
- `gasto-texto-extensible` la evidencia de señales usa `details.texto-extensible`, limitada a tres líneas en móvil con expansión nativa y sin JavaScript.
- `gasto-vacio` sin cálculo vigente avisa `No hay cálculo vigente` y explica que la revisión no ha corrido. Con cálculo vigente y sin filas dice `Sin filas en este mercado: la revisión está apagada o nada gasta sin vender.` La semilla baseline usa el primer caso.

## Driving it with curl

Precondición: `doctor` en verde sobre la instancia desechable.

```bash
curl -sS "$BASE/"
curl -sS "$BASE/gasto-sin-venta?plataforma=amazon_us"
curl -sS "$BASE/api/dashboard/gasto-sin-venta?plataforma=amazon_us"
```

Las tres rutas responden 200. El JSON gemelo es `GET /api/dashboard/gasto-sin-venta`; contiene el mismo cálculo, secciones y bandas del mercado. Guarda HTML y JSON bajo `evidence/<run_id>/gasto-sin-venta/` cuando amplíes el drive específico.

## Operaciones prohibidas en baseline

La pantalla no tiene formularios de escritura. No conviertas sus filas en negatives ni ejecutes POST: la creación de bloqueos queda fuera del baseline de lectura.
