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
> Precedencia: `docs/CONTEXTO.md` > este plan. Implementa **DeepSeek**
> (superficie de lectura server-rendered, su especialidad; sin ssh, sin push
> a producción, sin AppFlowy). Un PR. Cross-review: 1 ronda (lead +
> CodeRabbit).

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
| 1.1 | **DeepSeek — la pantalla no miente**: D1-D5 en `app/ui.py` + plantillas Jinja (y `app/api_dashboard.py` solo si el feed necesita exponer el tipo legible; reusar `MOTIVOS_ES_SALUD`/vocabulario existente, jamás reimplementar). Alias `/propuestas`. Textos en español sin jerga. `docs/DASHBOARD.md` al día y una línea en `docs/CHAT-CONTEXT.md`. `[tdd:required]` | Rojo antes del código: test que falla porque una fila `harvest` NO trae su etiqueta legible ni el aviso de «se aplica solo el <fecha>». Verde: las tres familias con su etiqueta exacta de D2; `/cortes` y `/propuestas` sirven lo mismo (200); la confirmación de rechazo nombra el efecto y declara la irreversibilidad; sin JS nuevo (la CSP es `default-src 'self'`); suites de UI y dashboard completas verdes en CI | - | cc:TODO |
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

- 事項: external-send — `git push` + PR (DeepSeek) y merge (lead)
  理由: patrón del repo; batería completa en CI
  scope: 1.1
- 事項: destructive — deploy al contenedor (lead)
  理由: 1.2; solo UI, sin migración ni cambio de motor
  scope: 1.2
