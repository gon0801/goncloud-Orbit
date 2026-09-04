# Inertes

Inertes lista hojas (`keyword` / `product_target`) ENABLED cuya campana y ad group tambien estan ENABLED y que Amazon no sirve desde hace 14 dias o mas, contados desde el watermark de su plataforma (`v_entidad_inerte`). El motor ya no las ajusta. Revivirlas es decision humana. Sin JS: tabla server-rendered.

## Sub-features

- `inertes-nav` abre `/inertes` y marca `aria-current="page"`.
- `inertes-vacio` sin filas muestra `sin entidades inertes: todo lo activo tiene trafico reciente.` (el parrafo se pinta dos veces: resumen y tabla).
- `inertes-fila` si hay hoja, muestra plataforma, campana, ad group, texto de keyword/target (`etiqueta_entidad.hoja`), clasificacion (`con_ventas_previas` / `gasto_sin_ventas` / `peso_muerto`), dias sin impresiones, gasto 90d, ordenes 90d, ultima impresion.
- `inertes-api` `GET /api/dashboard/inertes` es el mismo snapshot (`totales` + `items`).

## How to get to it (user POV)

- Elegir `Inertes` en el nav (`<a href="/inertes">`).
- Abrir `http://127.0.0.1:<puerto>/inertes`.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla baseline: campana + ad_group, **sin** hoja keyword/product_target ENABLED. La vista no devuelve filas.

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Seguir el nav.** Corre `curl -sS "$BASE/inertes"`. Status 200. El HTML contiene `data-pantalla="inertes"`, `h2` `Entidades sin trafico`, `href="/inertes"` junto a `aria-current="page"`.
- **Leer el vacio de la semilla.** El HTML contiene `sin entidades inertes: todo lo activo tiene trafico reciente.` No afirma una clasificacion contra esta semilla.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/inertes"`. Status 200. `items` es `[]` y `totales` es `{}`.
- **Proof.** Guarda HTML de `/` y `/inertes` mas el JSON bajo `evidence/<run_id>/inertes/`. O corre `.cursor/skills/verify-orbit/helpers/orbit-verify drive-inertes`.

## Gotchas

- Una fila con numeros exige hoja ENABLED + 14d sin impresiones desde el watermark. Eso no va en este fixture (regla 3). Reportalo `verified-unreachable` si el run no siembra esa hoja.
- El texto de la hoja es libre (`keyword_text`): el HTML servido no puede contener `<script>` crudo.
- `gasto_90d` NULL (mezcla de moneda) se pinta `—`, nunca un 0 inventado.
- Ads modularization (`app/ads/structure_*.py`) no cambia esta pantalla: la fuente es `v_entidad_inerte`.
