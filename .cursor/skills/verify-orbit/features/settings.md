# Settings (`/settings`)

Settings muestra la cascada vigente por plataforma (target, procedencia, caps, interruptor de margen) y los goals editables. Mode global y harvest son solo lectura. Arriba de las tarjetas por plataforma vive la tarjeta «Temporadas de venta» (TEMPORADA 01 A). En el baseline de esta skill solo se verifica la lectura. POST `/api/ads-optimizer/settings/{platform}`, «Guardar temporadas» y «Terminar la baja hoy» piden `x-orbit-token` y escriben.

## Sub-features

- `settings-nav` abre `/settings` desde el pie del sidebar y marca `aria-current="page"`.
- `settings-sesion`: `section.sistema-resumen.superficie.settings-sesion` muestra el alcance, `config_version_id`, modo global («Modo global (solo lectura): Práctica») y el campo `#settings-token` (placeholder «Token de escritura»). Cada formulario conserva un solo `button.accion-primaria[type="submit"]`.
- `settings-temporada` (TEMPORADA 01 A) tarjeta `[data-temporada]` arriba de las de plataforma: 12 selects `select[data-mes]` (Baja/Normal/Alta, prellenados con lo guardado o con el calendario por defecto), los cuatro números `input[data-temporada-campo]` (reducción 5-30, preparación 7-42, multiplicador 1-3, aumento 0-100), el criterio de campaña `input[data-topada-campo]` (pct 50-100, días 1-7), por plataforma `div[data-temporada-plataforma]` con meta de alta `input[data-meta-alta]` (10-45), radios de modo (Apagado/Práctica/En vivo) y las líneas de equilibrio (`data-linea-equilibrio`, `data-razon-equilibrio`, `data-aviso-rojo` con el aviso ROJO cuando la meta vigente o la de alta iguala o pasa el equilibrio; siempre existen en el HTML, ocultos cuando no aplican). Durante una baja, cada plataforma muestra el bloque de estado `[data-temporada-estado]`: «Temporada baja desde el …», conteos (`[data-estado-conteo]`: confirmados, pendientes y sin tocar con su razón en llano) y la lista de keywords pausadas a reactivar con el botón «Mantener pausada» (`[data-mantener-pausada]`). «Así queda tu año» (`[data-vista-previa]`) abre con la fase de hoy (`[data-frase-hoy]`), se recalcula el JS con GET `/api/ads-optimizer/temporada/vista-previa` (sin token) y una edición que cambia la fase de hoy muestra el aviso amarillo `[data-aviso-cambio]`. Botón «Guardar temporadas» (`[data-guardar-temporada]`; sin calendario guardado guarda los valores sugeridos tal cual) y, si hay baja guardada hoy, el bloque `[data-temporada-baja]` con «Terminar la baja hoy» (`[data-terminar-baja]`; fuera de baja responde 422 «Solo puedes terminar la baja mientras estás en temporada baja.»). Los errores de validación se pintan en «Error: <mensaje del servidor>».
- `settings-plataforma` una tarjeta `data-plataforma` por `amazon_us` / `amazon_mx` (h3 «Amazon US (USD)») con «Meta vigente: `25.00 %` · sale de: <procedencia en español>», target manual, margen, confianzas, motor de bids, subida rápida de bids (fieldset `[data-recuperacion]`, radios Apagada/Práctica/En vivo y memoria del bid más alto 90-730), motor de precios, `ACoS objetivo del Resumen (opcional, %)` (`input[data-resumen-acos]` para `amazon_mx`, `amazon_us` y `meli`) y caps. Los caps (`input[name="cap_<kind>"]`) son `type="text"` con `inputmode="numeric"`: conservan el texto crudo para poder rechazar `1,000`. El botón dice «Guardar configuración de Amazon US». Los goals muestran «Esta meta no cambia en temporada alta.»
- `settings-cuotas` una cuota modificada que no sea un entero no negativo (`1,000`, `1.5`, `-1`, `abc`) pinta en `[data-estado]` «Error: la cuota de <kind> debe ser un numero entero sin comas ni decimales (llego <texto>).» y no hace POST; una cuota omitida conserva su valor. El backend decide con `apply_cuota.cap_desde_settings`: `null` explícito, booleanos y fracciones ilegibles dan 422 sin crear `config_version`; un entero válido guarda una fila nueva.
- `settings-goals` goals de plataforma en su tarjeta; goals de campana en seccion propia. Sin goals de campana muestra `sin goals de campaña`.
- `settings-api` `GET /api/dashboard/settings` es el mismo snapshot; trae `temporada_form` (prefill), `campana_topada`, `temporada_estado` (fase de hoy de la config guardada) y por plataforma `meta_alta`, `modo_temporada`, `recuperacion_modo`, `recuperacion_memoria_dias`, `campanas_con_meta_propia` y `temporada` (equilibrio/linea/razon/aviso/frases).

## How to get to it (user POV)

- Elegir `Settings` al pie del sidebar (`<a href="/settings">`).

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: `config_version` con `ads_optimizer_mode=shadow` y un goal de plataforma amazon_us a 25%. Para ver el equilibrio con margen vigente hace falta la cadena de vistas de margen (0015+0016+0086+0092, como `test_api_dashboard._db_temporal`) y ventas con costo (`test_temporada01_i._siembra`); sin ellas la línea de equilibrio muestra la razón en llano («sin margen medido en la ventana»).

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Seguir el nav.** Corre `curl -sS "$BASE/settings"`. Status 200. El HTML contiene `data-pantalla="settings"`, `h1` `Configuración`, `h2` `Settings`, `href="/settings"` junto a `aria-current="page"`, `#settings-token`, `data-plataforma="amazon_us"`, `data-temporada` con `Temporadas de venta`, `Así queda tu año` y `Guardar temporadas`, `Meta de la plataforma` (la procedencia `goal_plataforma`), `25.00 %` y `sin goals de campaña`.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/settings"`. Status 200. `modo_global` es `shadow`. `temporada_form` trae los 12 meses prellenados y los defaults del formulario (30/21/2/25 y topada 90/5). `plataformas` trae amazon_us con `target_vigente.valor` `25.00`, `peldano` `goal_plataforma`, `meta_alta` `25` y `temporada.linea` (o `temporada.razon` sin dato). Hay un goal `scope=platform` de amazon_us. No hay goal `scope=campaign`.
- **Vista previa (sin token).** Corre `curl -sS "$BASE/api/ads-optimizer/temporada/vista-previa?meses=baja,baja,alta,alta,baja,baja,normal,normal,normal,alta,alta,normal&reduccion_baja_pct=30&dias_preparacion=21&mult_cortes_baja=2&aumento_presupuesto_alta_pct=25"`. Status 200 con `resumen.proximos` (1 dic sale_alta, 1 ene entra_baja, 8 feb restaura-preparación...) y `mensaje` «Guardado. Próximo cambio: ...». Un número con coma (`reduccion_baja_pct=30,5`) da 422 «Escribe solo el número, por ejemplo 30.»
- **Proof.** Guarda HTML de `/` y `/settings` mas el JSON bajo `evidence/<run_id>/settings/`. O corre `.cursor/skills/verify-orbit/helpers/orbit-verify drive-settings`.

## Gotchas

- El submit lo cablea `/static/js/settings.js` con header `x-orbit-token`. No POSTees escritura en el baseline.
- La tarjeta de temporadas es GLOBAL: «Guardar temporadas» hace POST a `/api/ads-optimizer/settings/amazon_mx` desde cualquier plataforma (el path solo nombra el label).
- Los textos de error de la tarjeta son los de `temporada.valida` y llegan tal cual en el `detail` del 422 («Error: Escribe un número, no sí o no.» con true/false en un campo de número).
- amazon_mx puede mostrar un peldano `default` si no hay goal sembrado (sin etiqueta propia: sale humanizado, «Default»). Eso es la cascada del motor, no un hueco de template.
- `margen_habilitado` es "clave presente", no un booleano inventado. La semilla no prende margen.
- Mode global ausente se pinta `—`. La semilla trae `shadow`.
- El equilibrio de la tarjeta sale de `v_target_margen_plataforma` (bloque I): sin migraciones de margen ni ventas sembradas muestra la razón, no la línea.
- Los caps del formulario son `input[type="text"][inputmode="numeric"]`, no `type=number`: `1,000` se ve tal cual, pinta el error de `settings-cuotas` y no hay POST. En el wire, `caps` con `null` explícito, booleano o fracción ilegible responde 422 sin crear `config_version`; omitir la llave conserva la cuota.
