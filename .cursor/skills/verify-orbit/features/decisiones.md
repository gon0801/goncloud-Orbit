# Decisiones

Decisiones lista las decisiones que SI se tomaron, ahora agrupadas por dia. El motivo sale en espanol por el registro `app/etiquetas.py` (`MOTIVO_DECISION`, o por union `MOTIVO_SALUD`); un id sin etiqueta sale humanizado, sin guiones bajos y con mayuscula inicial, nunca crudo. Los motivos de pujas llevan `{que}`, que `motivo_decision_es` cambia por el sujeto segun el kind de la entidad. El search_term del comprador va escapado y marcado `data-dato-libre`. La pagina HTML pagina por `?page=` (PageWindow) y conserva `tipo` y `q`; el JSON `/api/dashboard/decisiones` sigue siendo feed por cursor y no cambia.

## Sub-features

- `decisiones-nav` abre `/decisiones` y marca `aria-current="page"`.
- `decisiones-resumen` `section.tarjeta[aria-labelledby="h-res"]` con el titulo «En esta página», el rango (`50 decisiones · 31 oct – 11 nov`) y cuatro `.kpi`: Pausas, Pujas subidas, Pujas bajadas y Negativos. Cada contador dice «de 50 en esta página»: cuenta SOLO la pagina.
- `decisiones-filtros` `form.decisiones-filtros` con `div.seg` de enlaces `a.seg-btn` (`?tipo=todas|pausa|puja_sube|puja_baja|negativo|captura`) e `input#f-buscar[name="q"]` (campaña, keyword o ASIN). Cada pestaña muestra el conteo de TODAS las decisiones de ese tipo que cumplen la busqueda; un `tipo` desconocido cae en `todas`.
- `decisiones-dia` `section.decisiones-dia` por fecha. El encabezado `div.decisiones-dia-cab` trae la fecha (`h2.num`, «9 oct») y el resumen («33 decisiones · 31 pausas · …»). La tarjeta `div.decisiones-lista` contiene las filas del dia.
- `decisiones-fila` `div.decisiones-fila`: chip de tipo, entidad en negritas + `· campaña` dentro de un `[data-dato-libre]`, motivo, linea gris `#ad_entity_id · decision #id` (con `search term: …` si existe), `Target usado X %` y, solo en pujas, `antes → despues MONEDA`. La hora del centro de Mexico va a la derecha.
- `decisiones-grupo` `details.decisiones-grupo` SOLO para pausas y negativos del mismo dia con el mismo tipo y motivo: resumen «31 pausas · 6 entidades en 10 campañas», motivo y «Ver las 31 decisiones»; al abrir lista entidad, campaña, ids, target, hora y la evidencia v2 de cada una. Las pujas nunca se agrupan.
- `decisiones-chip` clases por tipo: `chip-tipo-puja-sube` (teal), `chip-tipo-puja-baja` (ambar), `chip-tipo-pausa` (gris), `chip-tipo-negativo` (naranja) y `chip-tipo-captura` (azul, «Captura» para `harvest`).
- `decisiones-pagina` `nav.paginador` arriba (en `header.pagina-header .header-acciones`) y abajo: anterior / página N de M / siguiente / total. Los enlaces conservan `tipo` y `q` (`?page=2&tipo=negativo&q=wedding`). Sin pagina extra, anterior y siguiente son `<span class="mutado">`.
- `decisiones-api` `GET /api/dashboard/decisiones` es el feed JSON por cursor y su contrato no cambia.

## How to get to it (user POV)

- Elegir `Decisiones` en el nav (`<a href="/decisiones">`).
- Elegir una pestaña de tipo o escribir en `Buscar` y enviar el formulario; ambos filtran todas las decisiones, no solo la pagina.
- Si hay mas de una pagina, pulsar `siguiente` o `anterior`; el filtro y la busqueda se conservan.
- Abrir un `details` de pausas o negativos repetidos para ver cada decision.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: al menos una decision bid sobre `Campana A` con motivo `banda_menos_12` y `target_acos_pct_usado` 25.00; y dos pausas del mismo dia con el mismo motivo para ver el grupo plegable.

- **Abrir la pagina.** Corre `curl -sS "$BASE/decisiones"`. Status 200. El HTML contiene `data-pantalla="decisiones"`, `h1` `Decisiones` y `section.tarjeta` con `h2` `En esta página`.
- **Leer la fila sembrada.** El HTML contiene `Campana A`, chip `Puja`, `Target usado 25.00 %`, `1.00` → `0.88` USD y el motivo `ACoS 15 % arriba de la meta`. En un negative, `search term: zapato blanco` va dentro de un `[data-dato-libre]`.
- **Filtrar por tipo.** Corre `curl -sS "$BASE/decisiones?tipo=negativo"`. La pestaña `Negativos` queda `aria-pressed="true"` y el total del paginador baja al conteo de negativos. Un `tipo` inventado se comporta como `todas`.
- **Buscar en todas.** Corre `curl -sS "$BASE/decisiones?q=wedding"`. El total y los conteos de cada pestaña bajan a lo que cumple la busqueda; la paginacion trae `q=wedding` en `rel="next"`.
- **Pagina (si `a[rel=next]` existe).** Corre `curl -sS "$BASE/decisiones?page=2"`. El HTML dice `página 2 de` y trae `rel="prev"`. Si no hay `rel=next`, la vista cabe en una pagina: anotalo, no inventes una pagina.
- **Dia partido.** En los datos de produccion el dia `9 oct` termina la pagina 2 y empieza la 3: ambas deben traer su `div.decisiones-dia-cab`.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/decisiones"`. Status 200. `items` incluye la bid de `Campana A`; el contrato es el de siempre.
- **Proof.** Guarda HTML de `?page=1` y `?page=2` (si aplica) y el JSON bajo `evidence/<run_id>/decisiones/`. Corre `python tools/pantallas_claras.py --base "$BASE" --rutas /decisiones --tope 0`: debe dar 0.

## Gotchas

- Sin `?page=` es la pagina 1. Un `page` mayor que `pages` se clampa a la ultima.
- El search_term es texto libre: el HTML servido no puede contener `<script>` crudo. Si siembras un payload, afirma el escape `&lt;script&gt;`.
- Las pujas se pintan una por fila; solo pausas y negativos se pliegan, y solo si comparten dia, tipo y motivo.
- El chip de `harvest` dice `Captura` y usa `chip-tipo-captura`; el filtro `?tipo=captura` cuenta esas decisiones.
- Skips no son filas de este feed: viven agregados en Salud.
- El JSON sigue paginando por cursor (`has_more` / `next_cursor`). La pagina HTML no usa `#btn-mas`.
