# Plan de Resumen Ads multicanal

> **Estado al 5-nov-2026: fases 01/02/03/04 integradas y archivos principales desplegados.** Ingesta inicial exitosa y cron/script correctos. Hasta 3-nov: 808/808 filas iguales; 4-nov tiene dos diferencias por fuente posterior (+13.47 MXN). **P01 CERRADO, 5-nov-2026 09:00 UTC:** el job 10 (lectura RO) dio `estado=OK`: corrida 1374 del cron 08:15 exitosa con la misma huella/as-of del snapshot, conciliación 812/812 filas y 203/203 días sin diferencias, T+0 4/4; la diferencia del 4-nov quedó absorbida. [Resultado](../docs/evidencia/quick-wins-2026-11-04/seguimiento-meli/resultado-job10-20261105T090000Z.json), [seguimiento](../docs/evidencia/quick-wins-2026-11-04/seguimiento-meli/LEEME.md).

**Objetivo.** Convertir `/` en un resumen de publicidad con periodos configurables, comparación y tendencias legibles para Amazon MX, Amazon US y Mercado Libre.

**Alcance.** Solo publicidad. No incluye ventas orgánicas, rentabilidad general, Shopify, bids, pausas, precios, accounting ni bridge.

**Arquitectura.** Amazon ya está en Orbit. MeLi Ads ya está en accounting. La fase 1 copia MeLi al esquema existente de Orbit. La fase 2 concentra los cálculos en la API. La fase 3 presenta esos resultados en la página.

**Verificación.** Sigue `AGENTS.md`. Las pruebas nuevas se limitan a dinero, escritura productiva, operaciones en máquinas y fallas reales de producción. Durante el desarrollo verifica de forma proporcional al riesgo y no repitas evidencia válida del mismo SHA. Antes de integrar cada fase, el gate de CI del commit final debe estar verde: gitleaks, compileall y suite completa con PostgreSQL. Los candados existentes se conservan; cualquier excepción debe justificarse en su allowlist. No hay carriles live, cantidad de pruebas nuevas ni gates de rendimiento obligatorios. La revisión cruzada solo se ejecuta si el operador la pide.

## Ingestar Mercado Libre Ads (RESUMEN-ADS-01)

**Archivos.**

- `app/meli/ads_ingest.py` y `app/meli/ads_source.py`.
- `app/cli.py` para `ingest meli-ads --sqlite <snapshot>`.
- `tools/refresh_costos.sh` para invocar el comando sin cambiar los otros pipelines.
- `docs/DEPLOY.md` y `docs/DATABASE.md` para origen, operación, conciliación y rollback.
- Evidencia en `docs/evidencia/resumen-ads-multicanal/01-ingesta/`.

**Trabajo.**

1. Usar `meli_ads_campaigns_daily` del snapshot de accounting. Su grano es campaña/día. `meli_ads_metrics_daily` queda solo como control diario de conciliación.
2. Reutilizar `ad_entity`, `ads_metric_observation`, `v_metric_latest`, el índice de deduplicación e `ingest_run`. No crear una migración.
3. Abrir SQLite en `mode=ro`. Validar identidad, fechas, moneda y métricas antes de escribir. Un dato desconocido queda `None`. Un cero conocido queda `0`.
4. Hacer cada snapshot atómico. Un reintento no duplica. Una corrección agrega una observación y no reescribe la historia.
5. Derivar una versión determinista de la fila cuando el origen no tenga revisión. Usar `observed_at` en UTC y no usar `ingest_run_id` como llave de deduplicación.
6. Actualizar solo entidades `kind='campaign'`. Un nombre ausente no borra el publicado ni restaura un nombre viejo.
7. Conciliar campaña/día y totales diarios contra accounting. La ventana cerrada termina ayer. Tratar T+0 de forma simétrica en origen y Orbit, sin ocultar huecos históricos, NULL ni diferencias.
8. Mantener el snapshot, el DSN de solo lectura y la limpieza de archivos en un procedimiento ejecutable. No pasar secretos por argumentos ni imprimirlos.

**Resultado visible.**

- Orbit contiene las mismas métricas MeLi que accounting, sin duplicados.
- Repetir un snapshot no cambia los totales.
- Una corrección aparece en `v_metric_latest`.
- Un snapshot inválido no deja datos parciales.
- Amazon conserva su comportamiento anterior.

**Verificación proporcional.**

Esta fase escribe datos de producción. Cubre los comportamientos anteriores con pruebas reales de SQLite y PostgreSQL cuando sean necesarias. Ejecuta la carga, el reintento, la corrección y la conciliación contra un snapshot desechable. Compara un smoke de Amazon solo si el cambio toca su camino. La selección de pruebas locales no sustituye el gate completo de CI antes de integrar.

## Exponer el contrato del Resumen (RESUMEN-ADS-02)

**Cierre técnico (2026-11-01).** Contrato implementado en PR #508 y cobertura
por métrica corregida para que un día con clics pero dinero desconocido no
publique ratios completos. Regresión demostrada fallando contra el código
anterior, 124 pruebas focalizadas aprobadas y 12 consultas de periodos/filtros
comprobadas con API y PostgreSQL desechable. Revisión independiente aprobada.
Evidencia: `docs/evidencia/resumen-ads-multicanal/02-contrato/15-cierre-cobertura-por-moneda.txt`.
PR #509 tambien restaura el presupuesto de tamano con una excepcion justificada
para el contrato, alinea las reglas con el gate real de CI y confirma la
regresion F1; 126 pruebas de arquitectura y calculos aprobadas. Evidencia:
`docs/evidencia/resumen-ads-multicanal/02-contrato/16-cierre-mediums-508.txt`.
PR #509 integrado el 1-nov; esta condición de integración pertenece a su cierre histórico.
La fase 03 debe partir del contrato con esta corrección integrada y presentar
su cobertura, sin volver a calcular métricas.

**Archivos.**

- `app/dashboard_resumen.py` para catálogo, agregación, comparación y medias móviles.
- `app/api_dashboard.py` para delegar el Resumen multicanal.
- `app/ui_metricas.py` solo para reutilizar la misma agregación en llamadas existentes.
- `docs/DASHBOARD.md` para parámetros, monedas, nulos, cobertura y frescura.

**Trabajo.**

1. Definir un catálogo propio del Resumen para `amazon_mx`, `amazon_us` y `meli`. No ampliar el vocabulario del optimizador. MeLi no hereda la madurez D-8 de Amazon.
2. Aceptar periodos de 7, 30 y 90 días y rangos personalizados de hasta 366 días. El periodo visible termina en D-1.
3. Entregar por canal `totals`, `comparison`, `series`, `coverage`, `max_metric_date` y `last_ingest_at`.
4. Mantener MXN y USD separados. No emitir un total global que mezcle monedas.
5. Calcular ACoS y ROAS desde sumas, no promediando ratios diarios. Un denominador cero o un valor necesario desconocido produce `None`.
6. Consultar seis días de calentamiento para MA7 y devolver solo el rango pedido. No convertir días desconocidos en cero.
7. Conservar la compatibilidad de `/api/dashboard/series/plataforma` para consumidores actuales. Agregar la ruta o los campos nuevos sin romper los campos existentes.
8. Consultar solo el grano campaña para evitar doble conteo con entidades hijas.

**Resultado visible.**

- La respuesta incluye los tres canales con su moneda nativa.
- Un periodo de 30 días tiene 30 puntos visibles y una comparación de igual longitud.
- Las tendencias usan la media diaria de siete días (la suma de la ventana dividida entre 7) y dejan brechas cuando falta información.
- La ausencia de frescura o madurez MeLi queda explícita.

**Verificación proporcional.**

Las pruebas monetarias están permitidas. Comprueba mediante la API real los tres canales, los periodos, la comparación, MA7, los ceros, los nulos, la moneda y el grano campaña. No agregues pruebas para cada campo. Antes de integrar exige el gate completo de CI sobre el commit final.

## Rediseñar la interfaz del Resumen (RESUMEN-ADS-03)

**Archivos.**

- `app/ui.py` para filtros GET y llamada al contrato único.
- `app/templates/resumen.html` para filtros, KPI, comparación, gráficas y resúmenes textuales.
- `app/static/js/dashboard.js` para tendencias, comparación, temas y reemplazo de gráficas.
- `app/static/css/dashboard.css` para diseño responsivo, tablas y foco visible.
- `app/templates/base.html` y `app/static/js/shell.js` solo si el texto actual contradice el Resumen multicanal.
- `.cursor/skills/verify-orbit/helpers/orbit-verify` para manejar filtros y capturar evidencia.
- `docs/DASHBOARD.md` para el flujo del operador.

**Trabajo.**

1. Usar todos los canales y 30 días por omisión. Conservar filtros en la URL y después de enviar el formulario.
2. Mostrar gasto, ventas atribuidas, ACoS, ROAS, clics y órdenes atribuidas cuando el canal los soporte. La página presenta los cálculos de la API y no los repite.
3. Mostrar el valor diario con poco énfasis y MA7 como tendencia principal. Mantener brechas por datos desconocidos y ceros reales como ceros.
4. Incluir comparación con el periodo anterior y una tabla por canal. Nunca mezclar monedas en una gráfica o total.
5. Agregar equivalentes textuales, foco visible y navegación por teclado. A 390 px solo la tabla puede desplazarse horizontalmente.
6. Mantener Chart.js local, cargas JSON inertes y animación desactivada. No marcar MeLi con la madurez D-8 de Amazon.
7. No crear pruebas nuevas de interfaz, texto, JavaScript ni del verificador. Comprueba el comportamiento en el navegador real.

**Resultado visible.**

- El operador puede cambiar canal y periodo sin editar URLs manualmente.
- La tendencia se entiende aunque los valores diarios tengan picos.
- MeLi aparece con datos reales junto a Amazon MX y Amazon US.
- Los valores desconocidos se ven como brechas o `—`. Los ceros se ven como `0`.
- La página funciona en escritorio, móvil, teclado y temas claro y oscuro.

**Verificación proporcional.**

Verifica en el navegador los periodos, el rango personalizado, MA7, la comparación, los canales, los nulos, los temas y el ancho de 390 px. Captura las pantallas principales y un video corto del flujo. El operador revisa la interfaz antes de integrarla. No hay lanes en VM ni medición de rendimiento obligatorios.

## Rediseñar el Resumen y ACoS objetivo (RESUMEN-ADS-04)

**Estado.** Integrado en #524 (merge `9934f9ef`, 5-nov 02:01 UTC) y desplegado: los 10 archivos de `app/` que cambió tienen el mismo SHA256 en master `9fb5a63f` y en `orbit-app-1` (5-nov 03:22 UTC). [Hashes](../docs/evidencia/resumen-ads-multicanal/04-despliegue/hashes.json). Falta la revisión visual en producción.

**Alcance.** La vista `/` pasa a siete bloques jerárquicos: filtros por canal y periodo conservados en la URL, tarjetas con métricas y tendencias, alertas priorizadas, totales por moneda y tablas plegables con cobertura. `/settings` agrega un ACoS objetivo opcional por canal; dejar el campo vacío elimina la meta. Las fichas `resumen.md` y `settings.md` de `.cursor/skills/verify-orbit/features/` describen el flujo.

**Archivos.** `app/api_dashboard.py`, `app/api_write.py`, `app/config_write.py`, `app/ui.py`, `app/ui_metricas.py`, `app/templates/resumen.html`, `app/templates/settings.html`, `app/static/js/dashboard.js`, `app/static/js/settings.js` y `app/static/css/dashboard.css`.

**Resultado visible.** El operador ve los siete bloques en `/` con filtros que sobreviven a recargar la página, y puede guardar o borrar el ACoS objetivo de cada canal en `/settings`.

**Verificación proporcional y cierre.** La escritura del ACoS objetivo tiene su prueba (`tests/test_settings_escritura.py`). Falta la revisión visual en producción: recorrer `/` y `/settings` con `.cursor/skills/verify-orbit/helpers/orbit-verify` según sus fichas, a 390 px y en escritorio, y guardar las capturas en `docs/evidencia/resumen-ads-multicanal/04-despliegue/`. La fase cierra cuando esas capturas muestran los siete bloques con datos reales y una meta de ACoS guardada y luego borrada.

## Decisiones de diseño

- Se reutiliza `ads_metric_observation` para tener una sola fuente de hechos campaña/día.
- El catálogo del Resumen no modifica `PLATAFORMAS_MONEDA` ni las reglas del optimizador.
- MA7 acompaña al dato diario. No lo sustituye.
- No hay conversión de moneda sin una política FX explícita.
- Shopify queda fuera. El contrato por canal permite agregarlo después sin ramas específicas en la interfaz.

## Referencias

Lee `docs/CONTEXTO.md`, `docs/DATABASE.md`, `docs/DEPLOY.md` y `docs/DASHBOARD.md` antes de editar. Revisa `app/api_dashboard.py`, `app/ui_metricas.py`, `app/ui.py`, `app/templates/resumen.html` y `app/static/js/dashboard.js` para conservar el comportamiento existente. El esquema de observaciones y deduplicación está en `migrations/0001_initial.sql`.
