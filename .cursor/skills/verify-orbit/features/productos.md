# Productos sin clics (`/productos-sin-clics`)

Productos que venden y casi no reciben clics, sus impulsos, los retiros abiertos, los que no venden en 11 meses y, desde AUTO-03, la sección **Meta por producto**: lo que el último ciclo terminado calculó para cada producto (margen y ventana, meta, grupos donde aplicaría con su target y veredicto, y por qué los demás quedan fuera). Todo es lectura salvo los formularios de impulso, retiro y reposición, que piden `x-orbit-token` y quedan fuera del baseline.

## Sub-features

- `productos-nav` abre `/productos-sin-clics` desde el sidebar (`<a href="/productos-sin-clics">`) y marca `aria-current="page"`. Mercado con `?plataforma=amazon_mx|amazon_us` (enlaces GET, sin pestañas).
- `productos-meta` es la tarjeta `div#decisiones-producto`. Lee la última evaluación del último ciclo done del mercado (`app/pantalla_decisiones_producto.py`, solo SELECT, sin llamar al resolutor).
  - Cabecera: chip `<N> de <M> con meta`, línea `Ciclo <id> · <fecha> UTC · la meta está <strong>en práctica|en vivo|apagada</strong>`, y conteos del ciclo (grupos aptos, keywords bajo ellos, cuántas recibirían la meta, cuántas manda un goal con target).
  - Tabla `table#decisiones-tabla`, una fila `tr[data-decision-producto="<product_id>"]` por producto, primero los que tienen meta. Columnas: Producto (nombre y SKU de Odoo), Margen (`<pct> %` y ventana `desde a hasta`, o `—`), Meta (`<strong><pct> %</strong>` o `sin meta`), Grupos donde aplicaría (un `details[data-grupo]` por grupo exclusivo con campaña, número de keywords y chip `manda el goal` si un goal con target pisa la meta; dentro, `tr[data-hoja]` con target de hoy y su procedencia de `target_acos_ciclo`, veredicto de hoy, target con meta y veredicto con meta, chip `cambia`), Por qué (`li[data-motivo="<codigo>"]` con la etiqueta de `productos.MOTIVOS_PRODUCTO`).
  - Un dato ausente se ve `—`, nunca `0`.
  - Estados vacíos (`[data-decisiones-vacio]`): `Todavía no hay un ciclo terminado en este mercado.` o `Este ciclo no evaluó productos: la meta está apagada.` Un error del ciclo sale en `p.aviso[data-decisiones-error]`.
- `productos-meta-filtro` es `select#decisiones-filtro`: `todos`, `con meta`, `sin meta` y un `motivo:<codigo>` por motivo presente. Lo cablea `/static/js/productos.js` (oculta filas con `hidden`); no pide nada al servidor.
- `productos-api` `GET /api/dashboard/productos-sin-clics?plataforma=P` trae lo mismo en `decisiones_producto` (`cycle_id`, `modo_meta`, `con_meta`, `motivos_presentes`, `productos[].grupos[].hojas[]`).

## How to get to it (user POV)

- Elegir `Productos sin clics` en el sidebar. Cambiar de mercado con los enlaces `Amazon MX` / `Amazon US`.
- Bajar a `Meta por producto`. Abrir el grupo de un producto para ver sus keywords. Elegir un motivo en `Mostrar` para ver solo esos productos.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla de `launch` (`_sembrar_productos`): ciclo done de `amazon_mx` con meta en práctica y dos evaluaciones guardadas con `productos_io.guarda_evaluaciones`. `Collar demo` (`PV-COLLAR`) con margen 8 %, fracción 0.5 y meta `4.00 %` en `Grupo collar` de `Campana P`, cuya keyword `collar plata` tiene target de hoy `10.00 %` `margen_plataforma` y pasa de `Mantener · dentro_de_banda` a `Mover -12.00 % a 0.88 · acos_sobre_target`. `Aretes demo` (`PV-ARETES`) sin meta por `halo_sin_margen`. `amazon_us` tiene ciclos done sin evaluaciones.
- La cadena entera de migraciones sin `0011` ni reversas (incluye 0077, `evaluacion_producto_ciclo`). Sin ella la página da 500 y el run no vale.

Receta empaquetada:

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify drive-productos [--run-id ID]
```

- **Abrir la pantalla.** `curl -sS "$BASE/productos-sin-clics?plataforma=amazon_mx"`. Status 200, `data-pantalla="productos-sin-clics"`, tarjeta `id="decisiones-producto"` con `Meta por producto` y chip `1 de 2 con meta`, `Ciclo <id>` y `<strong>en práctica</strong>`.
- **Leer el producto con meta.** La fila de `Collar demo` trae `<strong>4.00 %</strong>`, `8.00 %`, `Grupo collar`, `Campana P`, `collar plata`, `10.00 %`, `margen_plataforma`, `Mantener · dentro_de_banda`, `Mover -12.00 % a 0.88 · acos_sobre_target` y el chip `cambia`.
- **Leer por qué otro queda fuera.** La fila de `Aretes demo` trae `sin meta`, `ventas atribuidas de otro SKU` y `ningún grupo exclusivo`. El filtro tiene `value="motivo:halo_sin_margen"`.
- **Mercado sin evaluaciones.** `curl -sS "$BASE/productos-sin-clics?plataforma=amazon_us"` trae `Este ciclo no evaluó productos: la meta está apagada`.
- **Lado JSON.** `curl -sS "$BASE/api/dashboard/productos-sin-clics?plataforma=amazon_mx"`: `decisiones_producto.cycle_id` es el de la semilla, `con_meta` 1, Collar con `meta_pct` `"4.00"` y su hoja `cambia: true`, Aretes con `meta_pct` null y motivo `halo_sin_margen`.
- **Filtro cableado.** `/static/js/productos.js` contiene `getElementById("decisiones-filtro")`.
- **Proof.** `evidence/<run_id>/productos/` (HTML mx y us, JSON, `PROOF.json`).

## Gotchas

- El filtro vive en `/static/js/productos.js`: la CSP bloquea scripts inline.
- La sección muestra el último ciclo done aunque la meta esté apagada: entonces dice que no evaluó productos.
- Las cifras salen de la evaluación congelada del ciclo. Para recalcular con los datos de hoy está `python -m tools.auto03_verifica evalua`, no la pantalla.
- Nombres de producto, grupo y keyword son texto libre: van escapados.
