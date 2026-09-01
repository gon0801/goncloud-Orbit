# Salud

Salud muestra, por plataforma, el ultimo ciclo (status, mode, decisiones, applies), skips del ciclo, quota del dia, watermark/sync y el historico de 14 dias. Un ciclo `done` con Telegram caido debe verse en rojo, no "perfecto".

## Sub-features

- `salud-nav` abre `/salud` y marca `aria-current="page"`.
- `salud-ciclo` muestra `#<id>`, started_at, `decisiones:` y `applies:` (el 0 de shadow es dato).
- `salud-skips` dibuja `canvas#skips-<plataforma>` desde `script#datos-skips-<plataforma>`.
- `salud-telegram` si `notes.telegram` existe, aparece `p.alerta` `telegram: fallo del canal`.
- `salud-api` `GET /api/dashboard/salud` es el mismo snapshot.

## How to get to it (user POV)

- Elegir `Salud` en el nav (`<a href="/salud">`).

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: ciclo `done` / `shadow` en amazon_us con `decisiones` 2, `applies` 0 y skip `estado_no_enabled`.

- **Abrir Salud.** Corre `curl -sS "$BASE/salud"`. Status 200. El HTML contiene `data-pantalla="salud"` y `h2` `Salud — ultimo ciclo, historico 14d y skips`.
- **Leer el ultimo ciclo.** Hay una tarjeta `amazon_us` con chips `done` y `shadow`, texto `decisiones: 2` y `applies: 0` (no `—`).
- **Ver skips.** Existe `id="skips-amazon_us"` y `id="datos-skips-amazon_us"`.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/salud"`. Status 200. `plataformas.amazon_us.ultimo_ciclo.status` es `done`, `decisions_count` es `2` y `applied_count` es `0`.
- **Proof.** Guarda HTML y JSON bajo `evidence/<run_id>/salud/`.

## Gotchas

- `applied_count=0` en shadow es el caso normal. Pintarlo como `—` fue un bug de presentacion.
- Quota con `cap` null se muestra `— (sin clave)` o `— (config rota)`, nunca un tope 0 inventado.
- La nota `telegram` es la unica visibilidad del canal caido. Si el JSON trae `notes.telegram` y el HTML no la muestra, es regresion.
- Watermark (`v_metric_latest`) y `synced_at` pueden ser `—` si no hay estado: eso es dato faltante, no fallo.
