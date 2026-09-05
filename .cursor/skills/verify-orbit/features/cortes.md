# Cortes

Cortes lista la cola de pause/negative/harvest en `pending_veto` o `released`. El usuario puede abrir el mini-form Vetar. En el baseline de esta skill solo se verifica la lectura: el POST autentica con `x-orbit-token` y escribe.

## Sub-features

- `cortes-nav` abre `/cortes` y marca `aria-current="page"`.
- `cortes-tabla` lista id, plataforma, kind (+ familia, p.ej. `term_cut`), entidad (`linea_entidad` + `#id`, no el JSON de targeting), search_term, estado, vence, encolado, boton Vetar.
- `cortes-vacio` sin filas muestra `sin cortes pendientes: la cola no tiene filas esperando ventana de veto.`
- `cortes-form` el boton `data-vetar="<id>"` revela `form[data-veto="<id>"]` (dias, actor, token).
- `cortes-api` `GET /api/dashboard/cortes` es la misma cola.

## How to get to it (user POV)

- Elegir `Cortes` en el nav (`<a href="/cortes">`).
- Pulsar `Vetar` en una fila para ver el form (el submit real queda fuera del baseline).

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: una fila `pending_veto` kind `negative`, search_term `zapato blanco`, plataforma `amazon_us`.
- Migraciones de esquema de `migrations/` aplicadas, incluida 0002 (`apply_queue`). Los parches de datos con `_reversa_` (hoy 0011) no van en esta base vacia.
- Si falta 0002, `apply_queue` no existe y `/cortes` devuelve 500; ese run no es valido.

- **Abrir Cortes.** Corre `curl -sS "$BASE/cortes"`. Status 200. El HTML contiene `data-pantalla="cortes"` y `h2` `Cortes pendientes de veto`.
- **Leer la fila sembrada.** El HTML contiene chip `negative`, `zapato blanco`, chip `pending_veto`, entidad `Campana A` (etiqueta del ad_group sembrado), `button` con `data-vetar=`, y `form` con `data-veto=`, inputs `name="dias"` (value 30), `name="actor"`, `name="token"`.
- **Estado vacio (solo si dropeas la cola).** El HTML entonces contiene el parrafo `sin cortes pendientes`. No lo afirmes contra la semilla baseline.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/cortes"`. Status 200. Un item tiene `kind` `negative`, `search_term` `zapato blanco`, `estado` `pending_veto`.
- **Proof.** Guarda HTML y JSON bajo `evidence/<run_id>/cortes/`. No POSTees `/api/ads-optimizer/veto` salvo que el run tenga `ORBIT_SECRETS_DIR` y `ORBIT_DSN_ADMIN` desechables y lo declares.

## Gotchas

- El click de Vetar lo cablea `/static/js/cortes.js`. Un `onclick=` inline lo bloquea la CSP.
- El token va en el header `x-orbit-token`, nunca en query string.
- `search_term` es texto del comprador: debe ir escapado en el HTML.
- Conducir el veto contra la base viva de goncloud esta fuera de esta skill.
