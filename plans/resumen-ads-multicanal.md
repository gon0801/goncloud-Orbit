# Plan de Resumen Ads multicanal

**Objetivo.** Convertir `/` en un resumen de publicidad con periodos configurables, comparación y tendencias legibles para Amazon MX, Amazon US y Mercado Libre.

**Alcance.** Solo publicidad. No incluye ventas orgánicas, rentabilidad general, Shopify, bids, pausas, precios, accounting ni bridge.

**Arquitectura.** Amazon ya está en Orbit. MeLi Ads ya está en accounting. La fase 1 copia MeLi al esquema existente de Orbit. La fase 2 concentra los cálculos en la API. La fase 3 presenta esos resultados en la página.

**Verificación.** Sigue `AGENTS.md`. Las pruebas nuevas se limitan a dinero, escritura productiva, operaciones en máquinas y fallas reales de producción. No hay baterías, carriles live, cantidad de pruebas ni gates de rendimiento obligatorios. Verifica el comportamiento pedido de forma proporcional al riesgo y no repitas evidencia válida del mismo SHA. La revisión cruzada solo se ejecuta si el operador la pide.

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

Esta fase escribe datos de producción. Cubre los comportamientos anteriores con pruebas reales de SQLite y PostgreSQL cuando sean necesarias. Ejecuta la carga, el reintento, la corrección y la conciliación contra un snapshot desechable. Compara un smoke de Amazon solo si el cambio toca su camino. No ejecutes la suite completa por obligación.

## Exponer el contrato del Resumen (RESUMEN-ADS-02)

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
- Las tendencias usan sumas de siete días y dejan brechas cuando falta información.
- La ausencia de frescura o madurez MeLi queda explícita.

**Verificación proporcional.**

Las pruebas monetarias están permitidas. Comprueba mediante la API real los tres canales, los periodos, la comparación, MA7, los ceros, los nulos, la moneda y el grano campaña. No agregues pruebas para cada campo ni ejecutes la suite completa por obligación.

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

## Decisiones de diseño

- Se reutiliza `ads_metric_observation` para tener una sola fuente de hechos campaña/día.
- El catálogo del Resumen no modifica `PLATAFORMAS_MONEDA` ni las reglas del optimizador.
- MA7 acompaña al dato diario. No lo sustituye.
- No hay conversión de moneda sin una política FX explícita.
- Shopify queda fuera. El contrato por canal permite agregarlo después sin ramas específicas en la interfaz.

## Referencias

Lee `docs/CONTEXTO.md`, `docs/DATABASE.md`, `docs/DEPLOY.md` y `docs/DASHBOARD.md` antes de editar. Revisa `app/api_dashboard.py`, `app/ui_metricas.py`, `app/ui.py`, `app/templates/resumen.html` y `app/static/js/dashboard.js` para conservar el comportamiento existente. El esquema de observaciones y deduplicación está en `migrations/0001_initial.sql`.
