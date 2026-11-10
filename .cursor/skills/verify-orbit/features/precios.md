# Precios (`/precios`)

Precios muestra, por plataforma de Amazon (amazon_us, amazon_mx), la compuerta del motor de precios, los goals por unidad, la Cobertura del catalogo y lo que el motor decidio hoy. En el baseline de esta skill solo se verifica la lectura: sembrar goals y Soltar escriben con `x-orbit-token`.

## Sub-features

- `precios-nav` abre `/precios` y marca `aria-current="page"`. Los h2 por plataforma dicen «Amazon MX» / «Amazon US» (el `id` de los bloques sigue con `amazon_mx` / `amazon_us`).
- `precios-seguridad` `#bloque-seguridad-<plataforma>` (h3 `Amazon US: compuerta`): modos en español («Modo global: Práctica · US: Práctica»), `Última corrida #<id> (<fecha>, <estado en español: «Completo», «Con fallas»…>): N decisiones nuevas en esta corrida · M en el día · R retenidas, E errores`, la medicion de la compuerta de esa corrida (`[data-compuerta-medicion]`: `Compuerta del día: fba 124 medidas, 0 nuevas · fbm …`; mide el dia completo; las causas e insumos de las retenciones van en español, p. ej. «movimiento masivo», «tipo de cambio»), retenciones abiertas y el boton `data-liberar-corrida`.
- `precios-goals` `#bloque-goals-<plataforma>`: el TACoS va una vez arriba de la tabla (`[data-tacos]`: `TACoS Amazon MX, octubre 2026: 8.83 %`; el `aria-label` de la sección es `Objetivos de Amazon MX`). Columnas de la tabla: Sel, SKU, Canal, Margen hoy, Goal, Modo (en español: «Práctica», «En vivo»), Precio objetivo, Origen envío (sin TACoS por fila).
- `precios-cobertura` `#bloque-cobertura-<plataforma>`: `N activas = evaluadas + no evaluadas + sin objetivo + fuera de alcance` (cifras con miles; la fila de evaluadas dice `N evaluadas con objetivo hoy`). Las activas son el catalogo BUYABLE de SP-API.
- `precios-no-evaluados` `#bloque-no-evaluados-<plataforma>`: una fila por cada no evaluada de la Cobertura (mismo largo y mismos motivos), con el motivo en palabras. Las decisiones `no_a_la_venta` de hoy (goal sobre una publicacion fuera del catalogo) van en una linea aparte `[data-no-a-la-venta]`: `Con objetivo pero sin estar a la venta: N. El motor no evalúa ni cambia el precio de estas publicaciones.`
- `precios-vista-previa` (`precios.js`, tras Planear): columnas SKU, Acción («Poner meta», «Cambiar meta», «Quitar meta», «Sin cambio»), Margen hoy, Meta, Precio objetivo, Bloqueo (en palabras, con su motivo entre paréntesis) y Confirmar; cifras con `Orbit.pct` y `Orbit.dinero`. Aplicado dice `N metas puestas, M quitadas (lote …)`.
- `precios-api` `GET /api/dashboard/precios`: por plataforma, `cobertura.no_evaluadas` (motivo, count), `no_evaluados_filas` (sku, motivo, motivo_es), `no_a_la_venta`, `tacos` (`plataforma`, `mes`, `mes_es`, `pct`, o null), `seguridad.ultima_corrida.decisiones_dia` y `.medicion`. Ninguna fila de `unidades` trae `tacos`.

## How to get to it (user POV)

- Elegir `Precios` en el sidebar (`<a href="/precios">`).

## Driving it with curl

Preconditions:

- Doctor en verde. La semilla baseline no trae config de precios: `/api/dashboard/precios` da 503 y `/precios` dice `Precios ilegibles: config sin precio_aviso_dias_sin_evaluar`.
- Para ver las listas, sembrar en la base desechable una `config_version` con `precio_modo_global`/`precio_modo_universo`, listings con `spapi_listing_estado_observation` BUYABLE y oferta, goals y decisiones del dia.

- **Abrir Precios.** Corre `curl -sS "$BASE/precios"`. Status 200. El HTML trae `Seguridad — compuerta del motor de precios` y `Precios — cobertura, decisiones y divergencias del dia`.
- **Cuadre de la lista.** Corre `curl -sS "$BASE/api/dashboard/precios" -o /tmp/precios.json && python3 docs/evidencia/claridad-01/E/cuadra.py /tmp/precios.json`. Sale 0 si `no_evaluados_filas` cuadra con la Cobertura y ninguna unidad trae `tacos`.
- **Proof.** Guarda HTML y JSON bajo `evidence/<run_id>/precios/`.

## Gotchas

- El motor (`app/precio/corrida.py`) decide y aplica en Amazon solo sobre el catalogo activo (`MercadoAmazon.catalogo`, BUYABLE); un goal fuera de el queda `no_evaluado(no_a_la_venta)`. Mercado Libre no filtra.
- La lista de No evaluados sale de la Cobertura en vivo: antes de la corrida del dia (13:10 UTC) casi todo sale `sin decision`.
- El JSON de `/salud` reusa la misma `cobertura`; no trae `no_evaluados_filas`.
