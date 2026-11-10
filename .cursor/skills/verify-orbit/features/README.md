# Mapa de verificacion de Orbit

Este directorio es la fuente mantenida del comportamiento que un usuario ve en el dashboard. Lee el indice, luego el archivo de la feature. No conduzcas produccion (`10.13.13.1:8010`).

## Precondiciones baseline

- Instancia levantada por `.cursor/skills/verify-orbit/helpers/orbit-verify launch` en `http://127.0.0.1:<puerto>` (default 18010).
- Base desechable `orbit_verify_<run_id>` sembrada: campana `Campana A` (amazon_us, ENABLED), metrica D-15 cost `12.3400` / revenue `45.6700` USD, goal de plataforma 25% (`goal_plataforma`), decision bid, corte `pending_veto` con search_term `zapato blanco`.
- `orbit-verify doctor` en verde (proceso uvicorn `app.main:app`, puerto nuestro, `/health` ok, `GET /` con `data-pantalla="resumen"`).
- Nunca conduzcas una instancia que este run no haya lanzado.

## Convenciones

- Parte siempre de `GET /` (Resumen) salvo que la feature diga otra cosa.
- Handles estables: `data-pantalla`, `href` del nav, `aria-current="page"`, ids de canvas, `data-vetar` / `data-veto`, `#btn-tema`.
- Toda pantalla muestra en el header la leyenda «Horas del centro de México» (`span.leyenda-zona`, de `formato.LEYENDA_ZONA`). Las horas se ven como «8 nov, 11:58» (centro de México, sin la palabra UTC); las cifras llevan miles (`1,234.50`) y los porcentajes `25.00 %`, con espacio.
- Los códigos internos se ven en español (registro `app/etiquetas.py`): `amazon_us` es «Amazon US», `shadow` «Práctica», `goal_plataforma` «Meta de la plataforma». Los `id`, `data-*`, `value=` y el JSON de `/api/dashboard/*` siguen con el código crudo; un código sin etiqueta sale humanizado (sin guiones bajos, «Motivo futuro»). Nombres de campaña, keywords, búsquedas y subfamilias llevan `data-dato-libre` y no se traducen. Los campos de token dicen «Token de escritura» (el header sigue siendo `x-orbit-token`).
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
- [Campanas](./campanas.md) — tabla 30d, target efectivo/procedencia, filtros GET y sort por headers.
- [Decisiones](./decisiones.md) — pagina numerada de entidades que decidieron.
- [Salud](./salud.md) — ultimo ciclo, los 14 ciclos anteriores, lo que se saltó y cambios permitidos hoy.

Todas las pantallas cargan `app/static/js/formato.js` antes que sus scripts: `base.html` emite el registro de `app/etiquetas.py` en `script#orbit-etiquetas[type=application/json]` y los scripts traducen y dan formato con `Orbit.etiqueta`, `Orbit.dinero`, `Orbit.numero`, `Orbit.pct` y `Orbit.fechaHora` (CLARIDAD 01 I.1), igual que los filtros de las plantillas.
- [Contribucion](./contribucion.md) — rango pre-cargos por campana (90d maduros).
- [Propuestas](./cortes.md) — cola de decisiones pendientes (lectura). El POST Rechazar queda fuera del baseline.
- [Inertes](./inertes.md) — keywords y productos sin visitas 14d (lectura). La semilla baseline llega vacia.
- [Crear campanas](./fabrica.md) — formulario de un grupo de cinco campanas (`/campanas/nuevas`). POST crear/plan/acciones queda fuera del baseline.
- [Settings](./settings.md) — cascada de targets, caps y goals (lectura). POST de escritura queda fuera del baseline.
- [Productos sin clics](./productos.md) — candidatos, impulsos, retiros y la meta por producto del ultimo ciclo (lectura). Los POST de impulso, retiro y reposicion quedan fuera del baseline.
- [Precios](./precios.md) — compuerta, goals, Cobertura y no evaluados del motor de precios de Amazon (lectura). Sembrar goals y Soltar quedan fuera del baseline.
