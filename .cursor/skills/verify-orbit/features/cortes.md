# Propuestas (`/cortes`)

Cortes lista la cola de pause/negative/harvest en `pending_veto` o `released`. El usuario puede abrir el mini-form Rechazar. En el baseline de esta skill solo se verifica la lectura: el POST autentica con `x-orbit-token` y escribe.

## Sub-features

- `cortes-nav` abre `/cortes` y marca `aria-current="page"`.
- `cortes-tabla` parte la cola en `Crecen` y `Recortan`. Columnas: Tipo (chip Crece/Recorta, chip `Práctica` si la fila es `modo` `shadow`, y etiqueta), Entidad (`linea_entidad` + ids + plataforma como «Amazon US»), Search term, Por que, Vence (`se aplica solo el <8 nov, 11:58>` en filas `live`, `No se aplica: es práctica` en filas `shadow`, + estado en español y `propuesta el <fecha>` en detalle), Accion.
- `cortes-etiqueta` la etiqueta de `pause` sale por la entidad: keyword `Apagar palabra`, product_target `Apagar segmentación de producto`, product_ad `Pausar anuncio del producto`. `negative` es `Bloquear busqueda` y `harvest` `Capturar termino que vende`.
- `cortes-vacio` sin filas muestra `Sin propuestas pendientes: no hay nada esperando tu decisión.`
- `cortes-form` el boton `data-vetar="<id>"` revela `form[data-veto="<id>"]` (efecto del rechazo, o en una fila `shadow` el aviso `Es de práctica: no se aplica aunque no la rechaces. El rechazo sí bloquea esta propuesta de verdad…`; la frase `Bloquea la propuesta durante los días que pongas abajo, contados desde hoy.`; `Días` (`name="dias"`), actor, token (placeholder «Token de escritura»), `Confirmar rechazo`). Tras el POST, `cortes.js` escribe `Rechazado hasta el <fecha larga> (hora del centro de México).` y un 422 sale como `Error: Días: <mensaje>.`.
- `cortes-api` `GET /api/dashboard/cortes` es la misma cola; cada item trae `modo` (`live` o `shadow`).
- `cortes-contador` el numero del menu (`#propuestas-contador`, lo pinta `shell.js`) cuenta solo los items `live`; su `aria-label` es `N pendientes (M de práctica)`.

## How to get to it (user POV)

- Elegir `Propuestas` en el sidebar (`<a href="/cortes">`). `/propuestas` es el mismo HTML.
- Pulsar `Rechazar` en una fila para ver el form (el submit real queda fuera del baseline).

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: una fila `pending_veto` kind `negative`, search_term `zapato blanco`, plataforma `amazon_us`.
- Migraciones de esquema de `migrations/` aplicadas, incluida 0002 (`apply_queue`). Los parches de datos con `_reversa_` (hoy 0011) no van en esta base vacia.
- Si falta 0002, `apply_queue` no existe y `/cortes` devuelve 500; ese run no es valido.

- **Abrir Cortes.** Corre `curl -sS "$BASE/cortes"`. Status 200. El HTML contiene `data-pantalla="cortes"` y `h2` `Propuestas pendientes de decisión`.
- **Leer la fila sembrada.** El HTML contiene bloques `Crecen` y `Recortan`, columnas `Tipo` / `Por que`, chip `Recorta`, `zapato blanco`, entidad `Campana A`, `se aplica solo el` (la semilla es `live`), `Esperando tu revisión` (el estado `pending_veto`, en español) y `propuesta el` en el detalle de Vence (no como chip), `button` con `data-vetar=`, y `form` con `data-veto=`, `durante los días que pongas abajo`, `Confirmar rechazo`, inputs `name="dias"` (value 30, label `Días`), `name="actor"`, `name="token"` (placeholder `Token de escritura`). Una fila sembrada con `modo='shadow'` muestra el chip `Práctica` y `No se aplica: es práctica`.
- **Estado vacio (solo si dropeas la cola).** El HTML entonces contiene el parrafo `Sin propuestas pendientes`. No lo afirmes contra la semilla baseline.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/cortes"`. Status 200. Un item tiene `kind` `negative`, `search_term` `zapato blanco`, `estado` `pending_veto`, `modo` `live`.
- **Proof.** Guarda HTML y JSON bajo `evidence/<run_id>/cortes/`. No POSTees `/api/ads-optimizer/veto` salvo que el run tenga `ORBIT_SECRETS_DIR` y `ORBIT_DSN_ADMIN` desechables y lo declares.

## Gotchas

- El click de Rechazar lo cablea `/static/js/cortes.js`. Un `onclick=` inline lo bloquea la CSP.
- El token va en el header `x-orbit-token`, nunca en query string.
- `search_term` es texto del comprador: debe ir escapado en el HTML.
- Una fila `shadow` nunca vence ni se aplica (trigger de `migrations/0002_apply.sql`), pero vetarla sí bloquea la clave: `apply_cola._SQL_CLAVES_BLOQUEADAS` no filtra por `modo`.
- Conducir el veto contra la base viva de goncloud esta fuera de esta skill.
