# Mapa de verificacion de Orbit

Este directorio es la fuente mantenida del comportamiento que un usuario ve en el dashboard. Lee el indice, luego el archivo de la feature. No conduzcas produccion (`10.13.13.1:8010`).

## Precondiciones baseline

- Instancia levantada por `.cursor/skills/verify-orbit/helpers/orbit-verify launch` en `http://127.0.0.1:<puerto>` (default 18010).
- Base desechable `orbit_verify_<run_id>` sembrada: campana `Campana A` (amazon_us, ENABLED), metrica D-15 cost `12.3400` / revenue `45.6700` USD, goal de plataforma 25% (`goal_plataforma`), decision bid, corte `pending_veto` con search_term `zapato blanco`.
- `orbit-verify doctor` en verde (proceso uvicorn `app.main:app`, puerto nuestro, `/health` ok, `GET /` con `data-pantalla="resumen"`).
- Nunca conduzcas una instancia que este run no haya lanzado.

## Convenciones

- Parte siempre de `GET /` (Resumen) salvo que la feature diga otra cosa.
- Handles estables: `data-pantalla`, `href` del nav, `aria-current="page"`, ids de canvas, `data-vetar` / `data-veto`.
- Comandos literales. El harness es curl (y `orbit-verify drive-*` para cada pantalla).
- Tras una mutacion habria que restaurar la semilla. El baseline de este mapa es de lectura: no POSTees veto.
- Cleanup no borra `.cursor/skills/verify-orbit/evidence/<run_id>/`.

## Prueba y skips

- Captura la accion y el estado resultante, no solo la pantalla final.
- Prueba UI: HTML con `data-pantalla` + screenshot si hay canvas.
- Prueba de lado: `GET /api/dashboard/<recurso>` debe coincidir con lo visible.
- Anota feature id y entry point en cada artefacto.
- Un camino inalcanzable se reporta con el comando intentado y la precondicion que falto. No lo des por verificado por otro path.

## Features

- [Resumen](./resumen.md) — series spend/revenue/ACoS por plataforma.
- [Campanas](./campanas.md) — tabla 30d, target efectivo, procedencia, filtro y sort.
- [Decisiones](./decisiones.md) — feed por cursor de entidades que decidieron.
- [Salud](./salud.md) — ultimo ciclo, historico 14d, skips y quota.
- [Contribucion](./contribucion.md) — rango pre-cargos por campana (90d maduros).
- [Cortes](./cortes.md) — cola de veto (lectura). El POST Vetar queda fuera del baseline.
