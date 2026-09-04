# CORTES UI 01 — la pantalla de propuestas dejó de mentir

> **Origen (incidente real, 2026-09-04)**: el dueño **vetó por error** el
> primer cosechado de la historia. No quería vetarlo: la pantalla se llama
> «Cortes», el único botón dice «vetar», y de ahí dedujo que la fila era un
> recorte. Su frase literal: *«no no queria vetarlo queria mandarlo a
> harvest, osea es confuso»*. El término era **«arras para boda cristiana»**:
> 63 clics, 28.22 USD de gasto, **2 órdenes y 203.20 USD de ingreso**
> (ACoS 13.9 %) — una propuesta de CRECIMIENTO, no un recorte.
>
> **Costo del incidente**: el veto es TERMINAL por diseño (la máquina de
> estados de 0002 no tiene salida de `vetoed`, y un veto vigente bloquea
> re-decidir esa clave hasta `vence_el` = 2026-10-04). El lead creó la
> keyword a mano por el camino sellado (`keywordId 260446590936405`, EXACT,
> 0.68 USD, readback ENABLED) para no perder un mes. **El sistema funcionó;
> la interfaz falló.**
>
> Precedencia: `docs/CONTEXTO.md` > este plan. Implementa **Spark**
> (prompt autocontenido abajo; sin ssh, sin push
> a producción, sin AppFlowy). Un PR. Cross-review: 1 ronda (lead +
> CodeRabbit). Reasignado de DeepSeek a Spark por el dueño 2026-09-04.

## La causa, en una línea

La cola `apply_queue` tiene TRES familias — `pause`, `negative` y
`harvest` — y las dos primeras recortan mientras la tercera **crea**. La
pantalla las presenta idénticas bajo un nombre que solo describe a dos.

## Decisiones selladas (el header manda)

- **D1 · El nombre.** «Cortes» pasa a **«Propuestas»** en el título, el menú
  y cualquier texto de la UI. La ruta `/cortes` **NO cambia** (hay enlaces y
  documentación vivos); se añade `/propuestas` como alias que sirve la misma
  vista. El vocabulario interno (`kind`, `familia`, motivos) **NO se toca**:
  esto es superficie, no dominio.
- **D2 · Cada fila declara su tipo, en español y sin jerga**, con el efecto
  primero: `pause` → **«Apagar palabra»**; `negative` → **«Bloquear
  búsqueda»**; `harvest` → **«Capturar término que vende»**. La etiqueta del
  harvest lleva además el indicador que lo justifica (órdenes e ingreso de
  la ventana, ya presentes en `decision.inputs.termino`).
- **D3 · Separación visual por dirección.** Las propuestas que RECORTAN y
  las que CRECEN se distinguen a simple vista (agrupadas o con un distintivo
  claro), sin depender del color solo (accesibilidad).
- **D4 · El botón dice la verdad.** Hoy solo existe «vetar», lo que sugiere
  que es la única acción y que no hacer nada es pasivo. Pasa a: **texto
  explícito de lo que ocurre si no se actúa** («se aplica solo el <fecha>»)
  y el botón etiquetado **«Rechazar»** con confirmación que nombra el efecto
  concreto de esa fila (p. ej. «Rechazar: la palabra NO se creará»).
- **D5 · El rechazo es irreversible y hay que decirlo.** La confirmación
  declara que un rechazo **no se puede deshacer** y que bloquea esa misma
  propuesta hasta la fecha de vencimiento. Es la verdad de la máquina de
  estados; ocultarla fue parte del incidente.
- **D6 · Sin cambios de comportamiento.** Ni el motor, ni la cola, ni el
  endpoint de veto cambian. Es UI y textos. El endpoint sigue exigiendo su
  token.

## Phase 1 [lane:gate]

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 1.1 | **Spark — la pantalla no miente**: D1-D5 en `app/ui.py` + plantillas Jinja (y `app/api_dashboard.py` solo si el feed necesita exponer el tipo legible; reusar `MOTIVOS_ES_SALUD`/vocabulario existente, jamás reimplementar). Alias `/propuestas`. Textos en español sin jerga. `docs/DASHBOARD.md` al día y una línea en `docs/CHAT-CONTEXT.md`. `[tdd:required]` | Rojo antes del código: test que falla porque una fila `harvest` NO trae su etiqueta legible ni el aviso de «se aplica solo el <fecha>». Verde: las tres familias con su etiqueta exacta de D2; `/cortes` y `/propuestas` sirven lo mismo (200); la confirmación de rechazo nombra el efecto y declara la irreversibilidad; sin JS nuevo (la CSP es `default-src 'self'`); suites de UI y dashboard completas verdes en CI | - | cc:完了 2026-09-04: rojo `KeyError: 'etiqueta'` + 6 rojos UI en codigo intacto; verde 88 (test_ui + test_api_dashboard) y suite completa 1161+29; endpoint expone etiqueta/direccion/efecto_rechazo/indicador desde decision.inputs->'termino'; etiqueta por KIND (familia es entity_cut/term_cut y no da tres etiquetas); docs/DASHBOARD.md §7.5 + linea CHAT-CONTEXT en el mismo PR |
| 1.2 | **Lead — review, merge y verificación con el dueño**: review contra `origin/master` + CodeRabbit (1 ronda); deploy; el dueño confirma que la pantalla ya se entiende sin explicación. `[tdd:skip:ops]` | El dueño lo confirma con sus palabras; AppFlowy anotado | 1.1 | cc:TODO |

## Reject (con razón)

- **Cambiar la ruta `/cortes`**: rompería enlaces y documentación vivos por
  una mejora cosmética; el alias cubre el caso.
- **Renombrar `kind`/`familia` en la base o el motor**: es vocabulario de
  dominio sellado y viaja en el ledger, el freeze y el replay. El problema
  es de presentación.
- **Añadir un botón «aprobar»**: no existe tal transición — no actuar YA es
  aprobar. Un botón que no hace nada nuevo confunde más.
- **Permitir deshacer el veto**: la irreversibilidad es deliberada (una
  decisión humana no se anula en silencio). Se DECLARA, no se cambia.

## 事前確認

- 事項: external-send — `git push` + PR (Spark) y merge (lead)
  理由: patrón del repo; batería completa en CI
  scope: 1.1
- 事項: destructive — deploy al contenedor (lead)
  理由: 1.2; solo UI, sin migración ni cambio de motor
  scope: 1.2

## Prompt de implementación (Spark, tarea 1.1)

> Copia tal cual. Es autocontenido: no requiere haber visto esta conversación.

```
Trabajas en el repo goncloud-Orbit (Python 3.12, FastAPI, psycopg3, dashboard
server-rendered con Jinja2; sin npm). Rama nueva `cortes-ui-01-1-1` desde
origin/master. UN PR al final. Lee primero AGENTS.md: español sin acentos en
el codigo, ruff line-length 100, TDD con rojo demostrado, dato faltante =
None (jamas constante inventada), dinero siempre (valor, moneda).

# El incidente que estas arreglando

La pantalla /cortes muestra la cola apply_queue, que tiene TRES familias:
`pause` y `negative` RECORTAN, pero `harvest` CREA una keyword nueva (una
propuesta de crecimiento). El 2026-09-04 el dueno veto POR ERROR el primer
harvest de la historia (un termino con 2 ordenes y 203.20 USD de ingreso)
porque la pantalla se llama «Cortes» y el unico boton dice «Vetar»: todo
parece recorte. El veto es irreversible por diseno. Tu trabajo es que la
pantalla deje de mentir. SOLO UI y textos: el motor, la cola, el endpoint
POST /api/ads-optimizer/veto y las migraciones NO se tocan.

# Decisiones selladas (del plan, no se negocian)

- D1 · «Cortes» pasa a «Propuestas» en titulo, menu y textos. La ruta
  /cortes NO cambia (hay enlaces vivos); ANADE /propuestas como alias que
  sirve la misma vista (mismo handler o uno que llame al mismo, 200 ambas).
  El vocabulario interno (familia, kind, motivos) NO se renombra.
- D2 · Cada fila declara su tipo por `familia`, en español llano, con el
  efecto primero, etiquetas EXACTAS:
    pause    -> «Apagar palabra»
    negative -> «Bloquear busqueda»
    harvest  -> «Capturar termino que vende»
  La fila harvest lleva ademas el indicador que la justifica: ordenes e
  ingreso de la ventana (ver abajo como obtenerlo).
- D3 · Separacion visual por direccion: las que CRECEN (harvest) y las que
  RECORTAN (pause/negative) se distinguen a simple vista. Recomendado: dos
  tarjetas/secciones («Crecen» / «Recortan») o una columna «Direccion» con
  chip + texto. NUNCA solo color (accesibilidad).
- D4 · El boton dice la verdad: junto a cada fila, texto explicito de lo que
  pasa si no se actua («se aplica solo el <vence_el>»), y el boton pasa de
  «Vetar» a «Rechazar». La confirmacion nombra el efecto concreto de ESA
  fila: harvest -> «Rechazar: la palabra NO se creara»; pause -> «Rechazar:
  la palabra NO se apagara (seguira gastando)»; negative -> «Rechazar: la
  busqueda NO se bloqueara».
- D5 · La confirmacion declara que el rechazo NO se puede deshacer y que
  bloquea esa misma propuesta hasta la fecha de vencimiento (vence_el).
- D6 · Sin cambios de comportamiento. El endpoint de veto sigue exigiendo su
  token x-orbit-token y recibiendo {queue_id, actor, dias}.

# Donde esta cada cosa (verificado)

- app/ui.py:~379 `pagina_cortes` (GET /cortes, HTMLResponse): llama
  `dash.cortes(conn=conn)` y renderiza cortes.html. Anade aqui el alias
  /propuestas.
- app/templates/base.html:~24: el <nav> tiene «Cortes» -> cambia la etiqueta
  a «Propuestas» (href se queda /cortes; la variable `pantalla` puede seguir
  siendo "cortes" para no tocar el aria-current).
- app/templates/cortes.html: la tabla actual (columnas #, plataforma, kind,
  entidad, search term, estado, vence, encolado, accion con boton «Vetar» +
  mini-form dias/actor/token). Aqui va D2-D5.
- app/static/js/cortes.js: cablea el click del boton y el submit del form
  (fetch POST /api/ads-optimizer/veto). La CSP es `default-src 'self'`:
  PROHIBIDO onclick inline o <script> inline; todo JS vive en /static.
  Ajusta textos aqui (el form de confirmacion es donde van D4/D5: puedes
  pasar el efecto por data-* en el boton/form y pintarlo en el <span
  data-estado> o en un <p> del form; el fetch y el payload NO cambian).
- app/api_dashboard.py:~869 `_SQL_CORTES_PENDIENTES` + endpoint `cortes`
  (~:930): hoy NO expone el indicador del harvest. El indicador vive en
  decision.inputs->'termino' de la decision que encolo la fila:
  JOIN decision d ON d.id = q.decision_id y extrae orders, ad_revenue,
  clicks y moneda (shape real en app/cycle.py:~787: termino = {search_term,
  cost, ad_revenue, clicks, orders, fechas_distintas, moneda, ...}).
  Expone por item: `etiqueta` (el string exacto de D2 por familia) y, solo
  para harvest, `indicador` = {ordenes, ingreso, clics, moneda} (ingreso con
  su moneda, NULL como null). Regla 22: la UI consume el endpoint; NO
  reimplementes queries en la plantilla ni en ui.py.

# TDD (regla 9: el rojo se demuestra, no se presume)

1. Escribe PRIMERO los tests y correlos contra el codigo intacto; captura el
   fallo (lo pedira la review):
   - tests/test_ui.py (hay tests de la pagina de cortes con fixtures _ctx*:
     buscalos): una fila harvest muestra «Capturar termino que vende» + el
     indicador; pause -> «Apagar palabra»; negative -> «Bloquear busqueda»;
     el texto «se aplica solo el» con la fecha; el boton dice «Rechazar»; la
     confirmacion nombra el efecto y declara la irreversibilidad; /propuestas
     responde 200 con el mismo contenido que /cortes.
   - tests/test_api_dashboard.py (patron _db_temporal + seeds; mira como
     siembran cortes los tests existentes de /api/dashboard/cortes): el
     endpoint expone etiqueta por familia y el indicador del harvest leido
     de decision.inputs->'termino' (siembra una decision con ese JSON).
2. Implementa hasta verde. Suite: `PYTHONPATH=. ./.venv/bin/python -m pytest
   tests/test_ui.py tests/test_api_dashboard.py -q` y luego la suite
   completa. `ruff check --fix app tests && ruff format app tests`.

# Cierre del PR

- Actualiza docs/DASHBOARD.md (la pantalla se llama Propuestas y agrupa por
  direccion) y agrega una entrada fechada arriba de docs/CHAT-CONTEXT.md en
  voz para el dueno (español llano, que paso y por que).
- Marca la tarea 1.1 del plan como cc:完了 con evidencia (hay un candado de
  CI que EXIGE tocar docs/CHAT-CONTEXT.md en el mismo PR que marca cc:完了).
- Push de la rama y `gh pr create` con cuerpo que cite el incidente, D1-D6 y
  el rojo demostrado. NO uses --no-verify jamas.

# Limites

Sin migraciones, sin ssh, sin deploy, sin AppFlowy, sin tocar motor/cola/
endpoint de veto/vocabulario de dominio. Sin boton «aprobar» (rechazado en
el plan: no actuar YA es aprobar). Textos visibles en español mexicano
llano; el dueno no es tecnico.
```
