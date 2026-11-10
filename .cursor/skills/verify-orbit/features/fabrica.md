# Crear campanas (`/campanas/nuevas`)

Crear campanas es el formulario de un grupo de cinco campanas de Sponsored Products. El HTML llega server-rendered; el catalogo, el plan y el historial se cargan con JS contra `/api/fabrica/*`. En el baseline de esta skill solo se verifica la lectura. POST `/plan`, `/crear` y acciones de lote piden token y escriben.

## Sub-features

- `fabrica-nav` abre `/campanas/nuevas` desde el grupo Decidir del sidebar y marca `aria-current="page"`. `data-pantalla` es `fabrica`.
- `fabrica-aviso` declara que las campanas se crean ACTIVAS y pueden gastar en shadow y en live.
- `fabrica-form` `form#fabrica-plan` pide plataforma, tipo, nombre, modo, origen del objetivo ACoS, presupuestos y pujas de los cinco roles.
- `fabrica-confirmar` pide token y la frase `CREAR 5 CAMPAÑAS`. El boton nace deshabilitado.
- `fabrica-api` `GET /api/fabrica/catalogo?plataforma=amazon_us` y `GET /api/fabrica/lotes?plataforma=amazon_us` son el lado JSON. La semilla baseline llega sin listings ni lotes.
- `fabrica-textos` (CLARIDAD 01 I.1): roles, estados de lote y paso, etiqueta Ads, disponibilidad y estimación salen del registro con `Orbit.etiqueta` (dominios `rol_campana`, `estado_lote`, `estado_paso`, `etiqueta_ads`, `estado_disponibilidad`, `estado_estimacion`, `motivo_estimacion`, `exclusion_estimacion`, `componente_estimacion`, `origen_objetivo`, `recurso_fabrica`, `objetivo_puja`); los pasos de un lote dicen «Grupo de anuncios · Verificado · Activa» y las pujas sugeridas «Búsquedas muy parecidas» o «Keyword exacta: <keyword>»; p. ej. «Exacta de la categoría», «Creado y registrado», «Oferta destacada: sin verificar». Montos con miles y 2 decimales (`66,185.18 MXN`; el `title` de la celda trae 4), porcentajes con 2 (`48.02 %`), cobertura del margen como porcentaje (`100.00 %`) y horas del centro de México (`8 nov, 02:15`). El historial dice `<lote> · Creado y registrado · 8 sep, 21:35`.

## How to get to it (user POV)

- Desde cualquier pantalla, elegir `Crear campañas` en el sidebar (`<a href="/campanas/nuevas">`).
- Desde Campanas, el enlace `← Volver a campañas` no crea nada: solo vuelve a `/campanas`.

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla baseline: sin `listing` ni lotes de fabrica. El catalogo y el historial llegan vacios.

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Seguir el nav.** Corre `curl -sS "$BASE/campanas/nuevas"`. Status 200. El HTML contiene `data-pantalla="fabrica"`, `h1` `Crear campañas` (sin titulo duplicado), selector `#fabrica-productos` dentro de `.fabrica-catalogo`, comparador `#fabrica-comparador` con `#fabrica-comparador-datos`, `href="/campanas/nuevas"` junto a `aria-current="page"`, `form#fabrica-plan`, `#fabrica-plataforma`, `#fabrica-token` y la frase `CREAR 5 CAMPAÑAS`.
- **Leer el vacio de la semilla.** Corre `curl -sS "$BASE/api/fabrica/catalogo?plataforma=amazon_us"`. Status 200. `productos` y `tipos_producto` son `[]`. Corre `curl -sS "$BASE/api/fabrica/lotes?plataforma=amazon_us"`. Status 200. `items` es `[]`.
- **Proof.** Guarda HTML de `/` y `/campanas/nuevas` mas los JSON bajo `evidence/<run_id>/fabrica/`. O corre `.cursor/skills/verify-orbit/helpers/orbit-verify drive-fabrica`.

## Gotchas

- El path de usuario es `/campanas/nuevas`. `data-pantalla` es `fabrica`. No afirmes `href="/fabrica"`.
- Sin JS el HTML sigue siendo 200 y el `<noscript>` lo dice. No es un 503.
- No POSTees `/api/fabrica/plan`, `/api/fabrica/crear` ni `/api/fabrica/lotes/{lote}/{accion}` en el baseline.
- Un catalogo con filas exige listings vinculados. Eso no va en este fixture (regla 3).
