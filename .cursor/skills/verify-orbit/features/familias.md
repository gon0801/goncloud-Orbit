# Familias (`/familias`)

Organiza productos en familias y subfamilias de dos niveles. La fábrica usa esa clasificación; crear y asignar exige token de escritura.

## Sub-features

- `familias-nav` entra desde Resumen por `Catálogo` y después `Familias` en `nav.subnavegacion`. El padre del sidebar usa `aria-current="true"`; `/familias` en la subnavegación y `Catálogo` dentro de `nav#nav-mas` usan `aria-current="page"`.
- `familias-resumen` conserva `section.catalogo-resumen.superficie[aria-labelledby="h-familias"]` y el conteo de productos.
- `familias-arbol` usa `table#familias-arbol` y `tr[data-familia-id]`. Sin filas dice `Sin familias en esta plataforma. Crea la primera abajo.`
- `familias-productos` conserva `form#familias-filtros` (plataforma, búsqueda y `sin_familia`), `table#familias-productos`, `tr[data-producto-id]` y `.familias-check`. Sin coincidencias dice `Sin productos para este filtro.`
- `familias-vacio` la semilla baseline presenta el árbol y los productos vacíos con los dos mensajes anteriores.
- `familias-escritura` mantiene `#familias-token`, `form#familias-crear`, `form#familias-asignar` y `/static/js/familias.js`; no se envían en el baseline.

## Driving it with curl

```bash
curl -sS "$BASE/"
curl -sS "$BASE/familias?plataforma=amazon_mx"
curl -sS "$BASE/familias?plataforma=amazon_mx&sin_familia=1&q=collar"
```

Con `doctor` verde responden 200. No existe un JSON gemelo de lectura: el HTML se alimenta directamente de `app/familias.py` (`arbol` y `productos`). Las rutas `/api/familias` son de escritura, no sustitutos de esa lectura.

## Operaciones prohibidas en baseline

No envíes `POST /api/familias` ni `POST /api/familias/asignar`: crean familias o cambian productos y exigen `x-orbit-token`. Los filtros GET sí forman parte del recorrido de lectura.
