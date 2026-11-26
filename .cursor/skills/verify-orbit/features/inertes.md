# Inertes

Inertes lista hojas (`keyword` / `product_target`) ENABLED cuya campana y ad group tambien estan ENABLED y que Amazon no sirve desde hace 14 dias o mas, contados desde el watermark de su plataforma (`v_entidad_inerte`). El motor ya no las ajusta. Revivirlas es decision humana. Sin JS: tabla server-rendered.

## Sub-features

- `inertes-jerarquia` agrupa clasificación y antigüedad en `section.oportunidad-resumen.superficie`, los lotes en `section.oportunidad-contenido`, y las filas con sus criterios de archivado en `section.oportunidad-detalle`. «Antigüedad cumplida» sigue siendo solo una señal para revisar la clasificación, no una equivalencia con archivable.
- `inertes-nav` abre `/inertes` desde `Oportunidades`; el padre del sidebar usa `aria-current="true"` y `Inertes` en `nav.subnavegacion` usa `aria-current="page"`. En móvil, `Oportunidades` en la tab bar marca `aria-current="page"`.
- `inertes-vacio` sin filas muestra `Sin keywords ni productos inertes: todo lo activo tiene visitas recientes.` (el parrafo se pinta dos veces: resumen y tabla).
- `inertes-kpis` pinta Sin visitas, Antigüedad cumplida, En espera y Gasto de 90 días sin visitas (cifras con miles).
- `inertes-fila` si hay hoja, muestra plataforma, campana, ad group, texto de keyword/target (`etiqueta_entidad.hoja`), clasificacion («Vendió antes» / «Gastó sin vender» / «Sin gasto ni ventas»; el JSON sigue con `con_ventas_previas` / `gasto_sin_ventas` / `peso_muerto`), chip `en espera` si aplica, y las columnas Grupo de anuncios, Keyword o producto, Días sin impresiones, Gasto 90 días, Órdenes 90 días, Última impresión y `Archivable desde`. La plataforma se lee «Amazon US».
- `inertes-lotes` seccion `Lotes de archivado` (columnas Archivadas y Fallidas). Sin lotes muestra `Sin lotes de archivado todavía.`
- `inertes-api` `GET /api/dashboard/inertes` es el mismo snapshot (`totales` + `items` + `lotes`).

## How to get to it (user POV)

- Elegir `Oportunidades` en el sidebar o la tab bar y después `Inertes` en la subnavegación (`<a href="/inertes">`).
- Abrir `http://127.0.0.1:<puerto>/inertes`.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla baseline: campana + ad_group, **sin** hoja keyword/product_target ENABLED. La vista no devuelve filas.

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Seguir el nav.** Corre `curl -sS "$BASE/inertes"`. Status 200. El HTML contiene `data-pantalla="inertes"`, `h2` `Keywords y productos sin visitas`, `href="/inertes"` junto a `aria-current="page"`.
- **Leer el vacio de la semilla.** El HTML contiene `Sin keywords ni productos inertes: todo lo activo tiene visitas recientes.`, KPIs `Sin visitas` y `h3` `Lotes de archivado` con `Sin lotes de archivado todavía.` No afirma una clasificacion contra esta semilla.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/inertes"`. Status 200. `items` es `[]` y `totales` es `{}`.
- **Proof.** Guarda HTML de `/` y `/inertes` mas el JSON bajo `evidence/<run_id>/inertes/`. O corre `.cursor/skills/verify-orbit/helpers/orbit-verify drive-inertes`.

## Gotchas

- Una fila con numeros exige hoja ENABLED + 14d sin impresiones desde el watermark. Eso no va en este fixture (regla 3). Reportalo `verified-unreachable` si el run no siembra esa hoja.
- El texto de la hoja es libre (`keyword_text`): el HTML servido no puede contener `<script>` crudo.
- `gasto_90d` NULL (mezcla de moneda) se pinta `—`, nunca un 0 inventado.
- Ads modularization (`app/ads/structure_*.py`) no cambia esta pantalla: la fuente es `v_entidad_inerte`.
