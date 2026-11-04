# Settings (`/settings`)

Settings muestra la cascada vigente por plataforma (target, procedencia, caps, interruptor de margen) y los goals editables. Mode global y harvest son solo lectura. En el baseline de esta skill solo se verifica la lectura. POST `/api/ads-optimizer/settings/{platform}` y el guardado de goals piden `x-orbit-token` y escriben.

## Sub-features

- `settings-nav` abre `/settings` desde el pie del sidebar y marca `aria-current="page"`.
- `settings-sesion` muestra `config_version_id`, modo global y el campo `#settings-token`.
- `settings-plataforma` una tarjeta `data-plataforma` por `amazon_us` / `amazon_mx` con target vigente + peldano, target manual, margen y caps.
- `settings-goals` goals de plataforma en su tarjeta; goals de campana en seccion propia. Sin goals de campana muestra `sin goals de campaña`.
- `settings-api` `GET /api/dashboard/settings` es el mismo snapshot.

## How to get to it (user POV)

- Elegir `Settings` al pie del sidebar (`<a href="/settings">`).

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: `config_version` con `ads_optimizer_mode=shadow` y un goal de plataforma amazon_us a 25%.

- **Partir de Resumen.** Corre `curl -sS "$BASE/"`. Status 200 y `data-pantalla="resumen"`.
- **Seguir el nav.** Corre `curl -sS "$BASE/settings"`. Status 200. El HTML contiene `data-pantalla="settings"`, `h1` `Settings`, `h2` `Settings`, `href="/settings"` junto a `aria-current="page"`, `#settings-token`, `data-plataforma="amazon_us"`, `goal_plataforma`, `25.00` y `sin goals de campaña`.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/settings"`. Status 200. `modo_global` es `shadow`. `plataformas` trae amazon_us con `target_vigente.valor` `25.00` y `peldano` `goal_plataforma`. Hay un goal `scope=platform` de amazon_us. No hay goal `scope=campaign`.
- **Proof.** Guarda HTML de `/` y `/settings` mas el JSON bajo `evidence/<run_id>/settings/`. O corre `.cursor/skills/verify-orbit/helpers/orbit-verify drive-settings`.

## Gotchas

- El submit lo cablea `/static/js/settings.js` con header `x-orbit-token`. No POSTees escritura en el baseline.
- amazon_mx puede mostrar un peldano `default` si no hay goal sembrado. Eso es la cascada del motor, no un hueco de template.
- `margen_habilitado` es "clave presente", no un booleano inventado. La semilla no prende margen.
- Mode global ausente se pinta `—`. La semilla trae `shadow`.
