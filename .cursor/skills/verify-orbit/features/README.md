# Mapa de verificación de Orbit

Este directorio es la fuente mantenida del comportamiento que un usuario ve en las 18 rutas del dashboard. Lee el índice, luego el archivo de la feature. No conduzcas producción (`10.13.13.1:8010`).

## Precondiciones baseline

- Instancia levantada por `.cursor/skills/verify-orbit/helpers/orbit-verify launch` en `http://127.0.0.1:<puerto>` (default 18010).
- Base desechable `orbit_verify_<run_id>` sembrada: campaña `Campana A` (amazon_us, ENABLED), métrica D-15 cost `12.3400` / revenue `45.6700` USD, goal de plataforma 25 % (`goal_plataforma`), decisión bid, corte `pending_veto` con search_term `zapato blanco`, evaluaciones de Productos y configuración válida de Precios sin publicaciones activas.
- La configuración de Precios replica los umbrales de `tests/test_precio_pantalla.py`: aviso tras 3 días, catálogo hasta 7 días, caps 7 y modos explícitos. No siembra goals, listings ni decisiones de precio.
- `orbit-verify doctor` en verde (proceso uvicorn `app.main:app`, puerto nuestro, `/health` ok, `GET /` con `data-pantalla="resumen"`).
- Nunca conduzcas una instancia que este run no haya lanzado.

## Convenciones

- Parte siempre de `GET /` (Resumen) salvo que la feature diga otra cosa.
- Handles estables: `data-pantalla`, `href` del nav, `aria-current="page"`, ids de canvas, `data-vetar` / `data-veto`, `#btn-tema`.
- El sidebar agrupa Oportunidades, Decisiones, Rendimiento y Catálogo; cada grupo despliega sus destinos en `nav.subnavegacion`. El padre activo usa `aria-current="true"` y el destino exacto `aria-current="page"`.
- La tab bar móvil tiene cinco controles: Resumen, Campañas, Oportunidades, Decisiones y `button#nav-mas-toggle`. Este abre `nav#nav-mas`, con Rendimiento, Catálogo, Salud y Configuración, y se cierra con Escape o al elegir un destino.
- El encabezado de toda pantalla trae solo la acción de la pantalla (o el paginador en Decisiones) y `button#btn-tema`: no muestra leyenda de zona, watermark de cifras ni chips de alcance. Las horas se ven como «8 nov, 11:58» (centro de México, sin la palabra UTC); las cifras llevan miles (`1,234.50`) y los porcentajes `25.00 %`, con espacio.
- Los códigos internos se ven en español (registro `app/etiquetas.py`): `amazon_us` es «Amazon US», `shadow` «Práctica», `goal_plataforma` «Meta de la plataforma». Los `id`, `data-*`, `value=` y el JSON de `/api/dashboard/*` siguen con el código crudo; un código sin etiqueta sale humanizado. Nombres de campaña, keywords, búsquedas y subfamilias llevan `data-dato-libre` y no se traducen. Los campos de token dicen «Token de escritura» (el header sigue siendo `x-orbit-token`).
- Comandos literales. El harness es curl y `orbit-verify drive-*`; `drive-secundarias` recorre las ocho rutas que no tenían drive específico.
- El baseline es de lectura: no ejecutes POST autenticados ni llamadas de escritura a Amazon o MeLi.
- Cleanup no borra `.cursor/skills/verify-orbit/evidence/<run_id>/`.

## Prueba y skips

- Captura la acción y el estado resultante, no solo la pantalla final.
- Prueba UI: HTML con `data-pantalla` + screenshot si hay canvas.
- Prueba de lado: cuando exista, el JSON gemelo debe coincidir con lo visible.
- Anota feature id y entry point en cada artefacto.
- Un camino inalcanzable se reporta con el comando intentado y la precondición que faltó. No lo des por verificado por otro path.
- Los 19 recorridos completos exigen HTTP 200 y navegación activa; un 503 fail-closed no se marca como `ok`.

## Features por tarea

### Resumen y Campañas

- [Resumen](./resumen.md) — `/`; periodos, pendientes, monedas, series y canales.
- [Campañas](./campanas.md) — `/campanas`; métricas 30d, target, filtros GET, vista rápida y orden.
- [Crear campañas](./fabrica.md) — `/campanas/nuevas`; acción de Campañas para un grupo de cinco campañas. POST crear/plan/acciones fuera del baseline.

### Oportunidades

- [Propuestas](./cortes.md) — `/cortes`; cola pendiente de revisión. POST Rechazar fuera del baseline.
- [Gasto sin venta](./gasto_sin_venta.md) — `/gasto-sin-venta`; búsquedas agrupadas por lectura y bandas.
- [Búsquedas sin vender](./busquedas_sin_venta.md) — `/busquedas-sin-venta`; ASIN y palabras en 90 días maduros.
- [Keywords dañadas](./keywords_danadas.md) — `/keywords-danadas`; pérdida de visitas tras recortes y regresos. POST fuera del baseline.
- [Productos sin clics](./productos.md) — `/productos-sin-clics`; candidatos, metas, pausas y límites. POST fuera del baseline.
- [Inertes](./inertes.md) — `/inertes`; keywords y productos sin visitas 14d. La semilla baseline llega vacía.

### Decisiones y Rendimiento

- [Historial de decisiones](./decisiones.md) — `/decisiones`; página numerada, filtros y grupos.
- [Ruido](./ruido.md) — `/ruido`; ciclos, abstenciones, encogimiento y antes/después.
- [Contribución](./contribucion.md) — `/contribucion`; rango pre-cargos por campaña (90d maduros).
- [Dónde poner el dinero](./dinero.md) — `/donde-poner-el-dinero`; gasto por tipo y campaña. POST aplicar/regresar fuera del baseline.

### Catálogo y Sistema

- [Precios](./precios.md) — `/precios`; compuerta, goals, cobertura y no evaluados. Sembrar goals y Soltar fuera del baseline.
- [Reputación](./reputacion.md) — `/reputacion`; alertas, rating, reviews, preguntas y listings.
- [Envíos](./envios.md) — `/envios`; pedidos y paquetes de Amazon y Mercado Libre, con lo que requiere atención.
- [Familias](./familias.md) — `/familias`; árbol de dos niveles y asignación de productos. POST fuera del baseline.
- [Salud](./salud.md) — `/salud`; último ciclo, 14 anteriores, skips y cambios permitidos.
- [Configuración](./settings.md) — `/settings`; cascada de targets, caps y goals. POST fuera del baseline.

Todas las pantallas cargan `app/static/js/formato.js` antes que sus scripts: `base.html` emite el registro de `app/etiquetas.py` en `script#orbit-etiquetas[type=application/json]` y los scripts traducen y dan formato con `Orbit.etiqueta`, `Orbit.dinero`, `Orbit.numero`, `Orbit.pct` y `Orbit.fechaHora`, igual que los filtros de las plantillas.
