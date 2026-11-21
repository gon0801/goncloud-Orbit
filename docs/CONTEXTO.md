# Contexto del proyecto Orbit

El estado de entregas y pendientes vive en [ROADMAP actualizado](../plans/ROADMAP.md). Este documento reúne las reglas vigentes y las decisiones de arquitectura. Las secciones de migración documentan la transición inicial desde el sistema viejo.

Stack decidido el 2026-08-22: Python, FastAPI y PostgreSQL en Docker dentro del servidor propio junto a `bridge` y `accounting`, sin Redis ni colas mientras la carga real no lo justifique. Se descartó Supabase por dependencia externa, latencia en crons y costo, dado que un sistema interno de motores no necesita su capa de auth ni realtime.

Orbit es un sistema nuevo construido desde cero sobre una base de datos nueva. Reemplaza al stack de motores de Amazon Ads (`goncloud-MCP-2`, cancelado el 2026-08-21) sin reutilizar código viejo. Solo migra credenciales, datos verificados como limpios y las lecciones del incidente anterior.

Documentos fuente en copia verbatim, obligatorios antes de diseñar cambios de fondo:

- `docs/traspaso/TRASPASO-1-ACCESOS-E-INFRAESTRUCTURA.md` lista qué credenciales existen, dónde viven, qué rotar y qué datos son migrables.
- `docs/traspaso/TRASPASO-2-AUTOPSIA-Y-CIMIENTOS.md` documenta por qué falló el sistema viejo, las trampas del dominio y las reglas de diseño. La parte 3 sobre trampas del dominio es lectura obligatoria porque afecta a cualquier implementación nueva por igual.
- `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md` especifica las reglas y umbrales del optimizador.
- `docs/traspaso/MODULOS-AVANZADOS.md` especifica los cinco módulos avanzados: Repricing, Campañas, Reputación, Promociones y Envíos.

## Qué es Orbit

Orbit optimiza Amazon Ads (Sponsored Products) con decisiones explícitas y auditables sobre cuánto pujar, qué pausar y qué cosechar. Emite a lo sumo una decisión por entidad y ciclo, registra cada entrada en la tabla de auditoría y opera en modo live automático con topes diarios. Evita gates evidenciales apilados y cooldowns implícitos, que fueron la causa de fallo del stack viejo, donde el 94% de las decisiones corría con menos evidencia de la exigida y un gate `live` nunca disparó en 220,494 decisiones.

## Qué se migra del sistema viejo y qué no

Se migra y conserva:

- Credenciales e integraciones (Traspaso 1 §1 y §5) de Amazon Ads con la misma app LWA para no rehacer OAuth, además de MeLi, Odoo, SP-API, Keepa, Apify, Anthropic y Telegram. El sistema anterior leía credenciales en tres lugares por orden: variables de entorno, archivo JSON y fila en base de datos. Ningún secreto entra a este repositorio.
- Datos verificados como limpios (Traspaso 2 §4): las cuatro tablas de métricas de ads sin duplicados ni huecos, `currency_rates` diaria desde 2025-10-31, `ledger_events` con moneda verificada al 100%, precios de `product_economics_expected` y `own_items.current_qty` de Amazon.
- Los servicios `bridge` y `accounting`, que siguen independientes, activos y sin cambios.
- El mapa de cadencias reales de cada API externa.

No se migra:

- El código de los motores viejos, que sumaba 62 módulos, 206 flags y 147 jobs para tomar tres decisiones. Las primitivas de escritura que el diseño v2 marcaba como reutilizables se reescriben en Orbit con las mismas defensas: reintentos en 429/5xx y POSTs de creación no idempotentes en modo fail-closed.
- `sales_history.cogs` de MeLi porque el 49% carece de costo y guarda cero como si fuera dato real, además de `snapshots`, tablas de backup, `decision_audit` detenido desde 2026-05-19 y `own_items.current_qty` de MeLi.

## Cómo leer el diseño v2 (ADS_OPTIMIZER_V2_DESIGN.md)

Este documento fija las reglas y umbrales base para bids, hygiene, harvest, guardas de ciclo, cascada de target ACoS y escalera de modos `off`, `shadow` y `live`. Se adopta como especificación con dos adaptaciones:

- La tabla de componentes reutilizables del documento apuntaba al repositorio viejo. En Orbit esas piezas se implementan desde cero con ingesta por fecha, reintentos HTTP y envelope de ciclo.
- Los nombres de módulos como `engines/ads_optimizer_*.py` sirven como referencia de arquitectura y no obligan a ese layout de archivos.

Política ADS PROTECCION 01, aprobada el 2026-09-24 en [`spec de protección`](superpowers/specs/2026-09-24-ads-proteccion-design.md). Prevalece sobre el diseño v2 en tres puntos: precedencia de PAUSE frente al cooldown de bids, límite económico de hojas y propuestas de campaña. El cooldown de un bid no bloquea PAUSE, mientras que el enfriamiento tras PAUSE o reversa sigue vigente. El corte económico de hojas usa datos maduros y exige `cost > 3 * target * revenue` y un exceso `cost - target * revenue >= 80 USD` o `1000 MXN` con `target` en fracción. La misma regla cubre `revenue = 0` medido incluso antes del umbral adaptativo de clics, y se abstiene ante `None`. Para hojas aplica PAUSE automática con veto, revalidación y reversa; para campañas genera una propuesta que el dueño ejecuta en Amazon y Orbit verifica después. La vigilancia de la ingesta principal avisa por Telegram al fallar, a las 10:30 UTC si sigue sin éxito del día y al recuperarse.

Deltas sellados en ORBIT 03 respecto al diseño v2:

1. Doble ventana. Los bids usan una ventana de 30 días terminada en `max(metric_date) - 3d`. Los cortes de pausa, negativo y cosecha usan un agregado separado terminado en `min(max(metric_date) - 3d, decided_at - 10d)`, con madurez mínima de 10 días (`window_end <= decided_at - 10d`, garantizado por el trigger `decision_madurez_corte`). No se permite reutilizar el agregado de bids bajando solo `window_end`.
2. Opt-out por campaña. En lugar de `campaign_optimization_state`, un goal de campaña con `enabled = false` anula a un goal de plataforma habilitado.
3. Validación en shadow. Como el sistema viejo se apagó en ORBIT 02, el modo shadow se valida contra datos reales y recálculo manual, asumiendo que ninguna campaña se optimiza durante un freeze.

### Reglas numéricas vigentes

- PAUSE. Exige `orders = 0`, `clicks >= umbral_corte` y `cost >= 40 USD` en US o `500 MXN` en MX (CORTES 03, decidido el 2026-08-28; antes 12 USD y 200 MXN). El umbral de clics es adaptativo por producto (`umbral_corte(evidencia, 'pause')`, elegibilidad 3 pedidos, 60 clics y 14 fechas) con respaldo en 100 y piso `max(100, bruto)` desde CORTES 03 (antes 50 y 25). El motor de bids congela `inputs.corte` en toda decisión, incluidas las de tipo `bid`, porque PAUSE se evalúa antes que las bandas. El replay lee `umbral_clicks_usado` y `cost_min_usado` del freeze, y en filas históricas sin esas claves aplica los valores históricos de su era (25 clics y 12 USD o 200 MXN), nunca los vigentes.
- Política de bid `niveles_v3` en `app/optimizer/politica.py`, con `decide(caso)` como función pura compartida con el replay. Reemplaza a las bandas con previa jerárquica:
  - Cada hoja se juzga con sus propios datos. Si no concluyen, hereda el veredicto de su ad group siempre que el grupo tenga 3 pedidos o más. Un resultado sin opinión nunca recorta.
  - La previa jerárquica solo frena subidas y ya no devuelve la decisión a la regla anterior.
  - Los pasos son -25%, -12% y +15% sobre las bandas 1.35x, 1.15x y 0.85x del target, evaluados con la posterior gamma según la confianza de la plataforma y no con el ACoS crudo. El clamp por decisión es [-30%, +20%] y el resultado queda dentro de `[floor, ceiling]` con defaults por moneda.
  - Un gasto alto sin ventas no deriva a PAUSE; reduce puja por gasto para concluir (`gasto_sin_venta`). PAUSE al 1.5x se evalúa antes con su propio reloj.
  - Repetir la misma dirección pide 20 clics al bid nuevo; invertirla pide 20 clics o 14 días. Ante desplome de tráfico, el bid vuelve al anterior de inmediato y ese piso aprendido no vuelve a cruzarse hacia abajo.
  - Tras un cambio de bid, el motor espera 7 días (`DIAS_EFECTO` en `app/optimizer/politica.py`) antes de subir o bajar esa hoja otra vez. El regreso por desplome y la pausa no esperan ese plazo.
  - El interruptor `ads_bid_politica_<plataforma>` apaga los movimientos de bid cuando está ausente y los activa con `niveles_v3`, sin afectar PAUSE, negativos ni cosecha.
- NEGATIVE_EXACT. Exige `orders = 0`, `clicks >= umbral_corte` y `cost >= 8 USD` en US o `130 MXN` en MX. Los términos con formato ASIN siempre se saltan. El umbral de clics es adaptativo usando la evidencia del ad group en la ventana `D-90..D-10` con elegibilidad de 3 pedidos, 60 clics y 14 fechas: calcula `ceil(expected_clicks * 1.5)`, cae a 40 sin elegibilidad y aplica piso `max(20, bruto)`, de modo que solo sube umbrales. El piso de costo también es adaptativo (CORTES 01 1.4): con la misma elegibilidad y `ad_revenue` sano usa `max(legacy, AOV * 1.0)` con `AOV = ad_revenue_total / orders_total`; sin elegibilidad o con ingreso incompleto usa `max(legacy, 45 USD)` en US o `600 MXN` en MX. El freeze `inputs.corte` guarda `umbral_clicks_usado`, `piso_cost_usado` y `aov`; en filas históricas sin esas claves el replay usa 20 clics y 8 USD o 130 MXN.
- HARVEST. Exige `orders >= 2` y `ACoS <= min(35%, target)`, con destino de campaña manual configurado sin placeholders.
- Guardas generales: solo entran campañas con goal habilitado, frescura en `max(metric_date) - 3d`, completitud mínima de 7 fechas por entidad, claim con TTL y cooldown de 7 días que solo cuenta applies verificados.

## Reglas de diseño innegociables (Traspaso 2 §5)

Cada regla responde a un fallo previo con impacto económico y aplica a todo el código de Orbit:

1. Una decisión tiene un solo camino y un solo dueño. Lo que no pertenece a una decisión útil no se construye.
2. Un número tiene una sola fuente. El motor viejo calculaba 10 márgenes distintos en un día desde tres fuentes.
3. Un dato faltante se representa como `None` y la fila no se escribe. Nunca se usa una constante inventada; un tipo de cambio fijo de 20.5 infló el ingreso en 28,549 MXN en el sistema viejo.
4. Todo monto lleva `(valor, moneda, fecha_fx)` por esquema y conserva el importe original además del convertido. Un `SUM()` que mezcle monedas es inválido por esquema. Los defaults de piso y techo de goals van por moneda: 0.10 y 2.50 en USD, 1.00 y 45.00 en MXN. Sin moneda no hay default.
5. Toda métrica es append-only por `(entidad, metric_date, observed_at)`. El upsert in-place del sistema viejo introducía lookahead en los backtests.
6. La edad mínima del dato depende de la decisión. Cortar o pausar exige al menos 10 días de maduración, porque a día 0 el corte produce 8.7% de falsos positivos frente a 0.0% a día 10. El día en curso se descarta porque trae alrededor de 20% del costo y 12% del ingreso final.
7. Ninguna acción irreversible se enciende sin tener su reversa implementada antes.
8. Antes de escribir la prueba de un invariante, corre el `SELECT` que confirma la forma real del dato en producción.
9. Toda prueba de regresión debe fallar contra el código anterior antes de aplicarse el arreglo.
10. Se concilia contra la fuente externa (reportes de liquidación de Amazon), no contra la consistencia interna.

## Trampas del dominio que Orbit hereda

Estas restricciones vienen de las plataformas externas y no dependen del código (detalle completo en Traspaso 2 §3):

- Relojes desalineados. La venta atribuida madura en 5 a 8 días, el costo madura hacia abajo por clawback hasta el día 15 y los fees del P&L tardan de 15 a 30 días. Un ACoS medido al día 1 en MX resulta cerca de 1.5x peor que el real.
- Efecto halo. Entre 56% y 58.5% del ingreso atribuido en Amazon proviene de otros SKUs, mientras MeLi reporta cero. Es atribución de plataforma, no causalidad. El numerador de ROAS y POAS usa `ad_revenue` completo y `sales_same_sku` solo sirve para atribuir.
- Tablas point-in-time. Nunca se consultan sin filtro explícito de vintage porque inflan cifras de 13x a 17x e invierten el signo de las tendencias.
- Monedas. En el sistema viejo `sales_history` reportaba MXN incluso para `amazon_us`, generando un error de 18.66x.
- Impuestos retenidos. El dueño no paga impuestos adicionales a los que retienen las plataformas (confirmado el 2026-11-13). La venta del libro incluye IVA y el margen descuenta solo el IVA y el ISR retenidos; no falta restar otra mitad del IVA.
- ISR de Amazon sin `order_id`. Llega en bultos quincenales sin referencia a pedido. Los costos sin `order_id` se prorratean de forma explícita o se excluyen por escrito.
- Mercado Libre no es comparable con Amazon. Tiene cero halo, lag de un día, ISR con `order_id` y escritura de ads bloqueada a nivel cuenta, por lo que MeLi Ads opera solo con propuestas.
- Signo económico de US bajo halo. En 91 días la cuenta de US oscila entre +1,671 USD y -2,238 USD según se incluya o no el halo. Antes de automatizar decisiones sensibles a atribución conviene acotar con ambos supuestos, correr un holdout o decidir por TACoS.

## Integraciones externas

- Amazon Ads API. Usa reporting v3 asíncrono para métricas y campaign management v3 para escrituras. Los endpoints v4 unificados rechazan Bearer LWA.
- Amazon Marketing Stream y SQS. Son la única fuente intra-día y sirven para alertas de gasto, no para decisiones porque la atribución tarda días.
- SP-API. Orders v0 está deprecado y se migra a la versión `2026-01-01`. Finances `2024-06-19` es la fuente de fees e ISR.
- Mercado Libre. Usa un único refrescador de token y rutas de ads con `site`, como `/advertising/MLM/...`.
- Odoo 17. Cada creación debe enviar `tax_ids` para evitar IVA duplicado.
- Shopify, Google Ads y Meta no tienen cuenta activa en este negocio; no existen credenciales que buscar.

## Estado histórico del servidor viejo (referencia de migración)

El apagado y la migración cerraron en ORBIT 02. El registro siguiente corresponde a la foto previa al 2026-08-22. Solo la rotación del token de `cloudflared` (tarea 3.4) sigue pendiente en [ROADMAP P06](../plans/ROADMAP.md#pendientes-de-operación-y-cierre).

- El scheduling del sistema viejo corría en 147 jobs de APScheduler dentro del contenedor, no en cron. El apagado siguió el orden: flags a `off`, `systemctl disable --now competitive-intel.service`, `docker compose stop` y limpieza de cron (Traspaso 1 §0).
- El respaldo final de `competitive.db` se tomó con la API `.backup()` de SQLite para incluir el WAL.
- Elementos marcados para rotación al cerrar el stack viejo: `API_AUTH_SECRET`, deploy key `mcp2_deploy`, token de `cloudflared` en systemd y cuatro respaldos de `accounting.env`.

## Módulos avanzados (docs/traspaso/MODULOS-AVANZADOS.md)

Orbit incluye cinco módulos adicionales al optimizador: Repricing, Creación de Campañas por API, Reputación, Promociones y Envíos. La especificación fija el alcance y los modelos; en cualquier conflicto con las reglas de la autopsia, manda la autopsia.

- Se mantiene el stack Python, FastAPI y PostgreSQL sin Redis ni BullMQ hasta que la carga real lo justifique.
- Repricing y Promociones dependen del margen y cumplen las reglas 2, 3 y 4. Como los fees maduran en 15 a 30 días, cualquier margen proyectado declara su edad de datos.
- El optimizador actúa solo dentro de sus topes diarios. Los cambios masivos de precio (más de 50 productos), el borrado de campañas y las promociones bajo el margen mínimo exigen confirmación humana (spec §10.4).
- En Mercado Libre el módulo de Campañas genera propuestas en lugar de crear por API mientras la cuenta mantenga bloqueada la escritura de MeLi Ads.
- Pausar campañas desde Reputación es una acción de corte y cumple la regla 7 y la madurez mínima de datos.
- La trazabilidad completa usa tablas append-only por `(entidad, fecha, observed_at)` conforme a la regla 5.

## Módulo dashboard (ORBIT 16 y DASHBOARD 01)

El dashboard muestra series de gasto, ingreso y ACoS, campañas con target efectivo y procedencia, feed de decisiones y salud del optimizador. El dueño entra por túnel SSH o por VPN WireGuard. El contrato detallado de pantallas, endpoints, JSON y gráficas vive en `docs/DASHBOARD.md`.

- Las rutas de lectura son solo GET bajo el rol `orbit_read`. La edición de settings y goals pasa por los endpoints de escritura autenticados de ORBIT 04.
- Las reglas 1 a 10 aplican igual: series filtradas a `kind = 'campaign'`, colapso bitemporal mediante `v_metric_latest`, `NULL` mostrado como dato ausente y nunca como cero, montos serializados como texto sin mezclar monedas y exclusión del día en curso con marca de inmadurez en `D-8..D-1`.
- Cascada de target ACoS en `app/optimizer/goals.py`: tiene cinco peldaños (`goal_campana`, `goal_plataforma`, `margen_familia`, `margen_plataforma` y `setting_plataforma`) compartidos con el motor. BIDS 02 T.1 eliminó `cache_estado` y el valor por defecto 55 en la migración 0063; sin peldaño resuelto, la entidad se salta con `sin_target` y las pantallas muestran "sin target".
- Target derivado del margen (ORBIT 06 Fase 2, 2026-09-03). El orden de precedencia es goal resuelto, `margen_familia`, `margen_plataforma` y `setting_plataforma`. `margen_plataforma` multiplica `fraccion` por el margen neto antes de publicidad sobre 90 días maduros `[D-105, D-15)`, excluyendo `fee_type = ads`, con COGS a la fecha, misma moneda y cobertura por monto de al menos 95%. La fracción sale de `ads_target_fraccion_margen_<platform>` (0.5 en MX y 0.8 en US por decisión D1 de BIDS 02); ausente apaga el peldaño y presente con valor inválido lanza `ValueError`. Si la señal falta, supera 3 días de antigüedad, tiene menos de 60 días con venta, cobertura baja, moneda mezclada o cargos sin clasificar, el peldaño se abstiene y pasa al setting. Un derivado fuera de `[10, 45]` se recorta al extremo. Las subidas aplican de una vez y las bajadas avanzan como máximo 0.5 puntos por ciclo. `v_contribucion_entidad` sigue siendo solo informativa. Ver `docs/superpowers/specs/2026-09-03-target-margen-plataforma-design.md`.
- Meta por producto (AUTO-03 C-F y ORBIT 07 M2). En un grupo exclusivo y elegible, `margen_producto` calcula `min(fracción * margen_del_producto, 45)` sin piso de 10. Con `ads_producto_economia_<plat> = producto_directo_v1` (M2), la meta aplica de una vez sin rampa de 0.5 puntos y al perder elegibilidad cae directo a la cascada de respaldo; ausente o `auto03` conserva el comportamiento previo. `ads_producto_meta_<plat>` sigue controlando el apagado de la meta individual y cambiar versión no borra historial.
- Pausa por producto y venta reciente (ORBIT 07 M4). Además de la ventana madura `[D-60, D-30)` en cero, la política v3 espera si detecta compra o venta en `[D-30, D-1]` leyendo el reporte de productos hasta `D-1`. El ancla es la fecha más antigua de esa venta, vence a los 30 días y libera al día 31. Al revalidar en cola descarta con `venta_reciente_producto` sin PUT ni cuota. Desde A-H3 también espera con `venta_reciente_sin_medir` si una fecha de `[D-30, D-1]` fue descartada por falta de métrica y pudo tener venta; un día con compra y venta conocidas en cero no bloquea.
- Episodio económico del último cambio (ORBIT 07 M5 y M6). Con `ads_bid_economia_<plat>` en `economia_cambio_v1` o `economia_cambio_v2`, el ciclo congela las ventanas `[x-7, x-1]` y `[x+1, x+7]` del último cambio de bid y exige madurez en `D+17`. Un recorte que conserva ventas y mejora ACoS salta el freno de tráfico de R16; una subida cuyo tráfico adicional no compensa el gasto se frena con `subida_sin_economia`. En v2 la ventana posterior pide además el mínimo de clics `CLICS_NUEVOS` y frena subidas con `post.venta <= pre.venta` aunque antes no hubiera ventas. Si el ajuste está ausente o en `off`, el ciclo no lee ni congela episodios.
- Las rutas de dashboard viven en `app/api_dashboard.py` y los helpers compartidos con `app/api.py` viven en `app/api_common.py`.

## Módulo apply (ORBIT 04)

El módulo apply ejecuta mutaciones en Amazon Ads con topes diarios y ventana de veto. El contrato detallado de `apply_queue`, `apply_attempt`, reconciliación, reversas y cuotas vive en `docs/APPLY.md`.

- Esquema híbrido con ventana de veto (2026-08-24). Los bids aplican en su propio ciclo. Los cortes de pausa, negativo y cosecha entran a `apply_queue` con 48 horas de veto y se aplican al vencer si nadie los veta. Un veto dura 30 días por clave de efecto `(platform, ad_entity_id, familia, search_term)`. Desde AUTO-03 (S9, 2026-11-05), las pausas sobre `product_ad` entran en la misma cola con familia `entity_cut` y cuota de pausas.
- Práctica de veto en shadow. En modo shadow los cortes se encolan con `modo = 'shadow'` para practicar el veto con candidatos reales y nunca se liberan.
- Orden sellado al liberar un corte: revalidación previa al claim con ventana fresca (recalculando `umbral_corte` al reloj de liberación), cobro de cuota, claim atómico, escritura de ledger pre-HTTP y envío HTTP. Ningún descarte ocurre después de cobrar cuota.
- Cuota fail-closed. La fila diaria solo nace si existe `ads_apply_cap_<platform>_<kind>` en la configuración vigente (mapeada a `ads_optimizer:<platform>:<kind>`). Sin clave no se aplica nada. Cada operación lógica cobra una unidad; los reintentos por 429 no recobran y las reversas están exentas.
- Ledger pre-HTTP (`apply_attempt`, append-only) obligatorio en toda mutación. `decision_application` resume el resultado final y sella `applied_cycle_id` al confirmar, de modo que el cooldown se mide sobre el ciclo ejecutor.
- Cliente de escritura default-deny en `app/ads/write.py`, limitado a `app/apply.py`, `app/ads/archivar.py` y `app/bloqueo_dueno_efecto.py` por `tests/test_architecture.py`. El cliente de lectura sigue bloqueando PUT, PATCH y DELETE.
- Toda mutación tiene reversa probada antes de encenderse conforme a la regla 7. En harvest la reversa borra primero la keyword y después el negativo. Una reversa no limpia el cooldown.
- Al inicio de cada ciclo se reconcilian contra Amazon en vivo los harvests pendientes, negativos, bids sin sello y filas `applying` huérfanas.

## Fases de evolución (diseño v2)

1. PR 1 en shadow completo: decisiones, auditoría, envelope, router, cron y flag, sin escrituras a Amazon.
2. PR 2 de apply: topes diarios, reserva de PAUSE, secuencia ledger pre-HTTP, HTTP, readback y sello, harvest con fases, reconciliación y checklist de cutover.
3. Fase 3 de consolidación: digest diario por Telegram, vistas de lectura y targets derivados del margen.
4. Fase 4 de señales sin gates: cada señal nueva debe demostrar en shadow que aumenta la tasa de acción útil, y su efecto máximo es abstenerse.
5. Fase 5 de palancas nuevas: una a la vez, cada una apagable sin romper el resto.

Los cinco módulos avanzados dependen de la base de márgenes y la autenticación unificada de Amazon y Mercado Libre construidas en las primeras fases.
