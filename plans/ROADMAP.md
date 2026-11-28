# ROADMAP maestro — Orbit

Actualización vigente del **25-nov-2026**. La cola completa **ARREGLOS 03 → ARREGLOS 04 → TEMPORADA 01 → ARREGLOS 05** quedó **cerrada, integrada y desplegada** (`c16c8c55`, aceptación integrada 31/31, regresiones N a–k 438/438, migración `0100` en producción). Ver [ORDEN-IMPLEMENTACION](ORDEN-IMPLEMENTACION.md); el manifiesto registra `arreglos-05` cerrado.

Foto histórica conservada: **2026-11-05 09:00 UTC** (cierre de P01; base del 2026-11-04, America/Vancouver, con consultas de producción del
2026-11-05 UTC), contra `master` **6bce3589** y los cierres de cada plan. Actualización incremental: la fila RESUMEN ADS 04 se comprobó contra `master` **9fb5a63f** (5-nov 03:22 UTC). Cierre de P01 registrado el **2026-11-05 09:00 UTC** con el resultado del job 10.
Este registro conserva los seguimientos y la historia; `docs/PENDIENTES-AUTOMATIZACION.md`
redirige aquí. El orden vigente se mantiene únicamente en [ORDEN-IMPLEMENTACION](ORDEN-IMPLEMENTACION.md); los planes detallados conservan sus contratos.

[Evidencia de la conciliación](../docs/evidencia/estado-planes-2026-11-04/README.md):
GitHub, consultas con `ORBIT_DSN_READ` en transacciones `READ ONLY`, hashes del
contenedor y recordatorios del servidor. Integración, despliegue y validación
operativa son estados distintos. Los datos históricos llevan fecha. Esta
revisión no modificó producción. Los [quick wins](../docs/evidencia/quick-wins-2026-11-04/README.md)
añaden el arreglo del checker y los cierres operativos A.4/B.4; su integración
se registra por separado de las lecturas productivas. PEZ 01 / PR #526 agrega
el cierre productivo P04 el 5-nov-2026 UTC, con evidencia antes/despues.
PEZ 02 agrega ocho descripciones corregidas; P13 conserva una baja por categoria.
El job 10 cerró P01 el 5-nov-2026 09:00 UTC con `estado=OK` (lectura RO).

En la foto del 5-nov el registro apuntaba a `resumen-ads-multicanal`, cuyo P01 cerró ese día.
Al 15-nov, ARREGLOS 01/02, ORBIT 07, CLARIDAD 01 y DISEÑOS 01 están integrados: **no reejecutarlos**.
Las [huellas local](../docs/evidencia/arreglos-05/base/huella-local.json) y [productiva](../docs/evidencia/arreglos-05/base/huella-prod.json) coinciden en 202 archivos de runtime del snapshot `ff03ed61`. Esto no acredita por sí solo todas las migraciones, configuración ni actas operativas.
Fuentes del censo: [inventario](../docs/evidencia/arreglos-05/inventario/inventario.md), [JSON](../docs/evidencia/arreglos-05/inventario/inventario.json) y [cobertura final](../docs/evidencia/arreglos-05/COBERTURA.md). Las asignaciones de cobertura finales prevalecen sobre la propuesta original del inventario.
Antes de implementar un frente nuevo se necesita un plan con alcance, fuentes
verificadas, dependencias, pruebas proporcionales, revisión y despliegue.

## Pendientes de operación y cierre

Los IDs P01–P13 identifican seguimientos, no planes de implementación.
«Por comprobar» significa que falta evidencia de cierre, no que el sistema falle.
Salvo actualización expresa, cifras, modos y conteos de esta tabla conservan la fecha del 4/5-nov; no son consultas nuevas del 15-nov.

| ID | Frente | Estado comprobado / impacto | Siguiente paso y criterio de cierre | Responsable |
|---|---|---|---|---|
| P01 | Resumen Ads multicanal | **CERRADO, 5-nov-2026 09:00 UTC — job 10 `estado=OK`.** Corrida 1374 (cron 08:15 UTC) exitosa con la misma huella/as-of del snapshot actual; conciliación 812/812 filas y 203/203 días sin diferencias, T+0 4/4; la diferencia de +13.47 MXN del 4-nov quedó absorbida. | Sin siguiente paso: la ingesta diaria y la conciliación quedaron comprobadas en lectura RO (`orbit_read`, READ ONLY). [Resultado](../docs/evidencia/quick-wins-2026-11-04/seguimiento-meli/resultado-job10-20261105T090000Z.json), [seguimiento](../docs/evidencia/quick-wins-2026-11-04/seguimiento-meli/LEEME.md). | Operación |
| P02 | FABRICA 02 D.3 / cierre ORBIT 17 | **CERRADO, 27-nov-2026 UTC.** Prerrequisitos 04 B-C y 05 B desplegados (`c16c8c55`). Primer harvest natural por grupo verificado en producción (`harvest_job` 12, `decision` 2943, grupo 3 `AGM2M`, `"arras matrimoniales"`, 26-nov 16:42 UTC): keyword `EXACT` `24714489976544` (`ENABLED`), negativo origen `101174754610536`, 2/2 hermanas (`category_phrase` `73580000101126`, `product_targeting` `262872540833841`), `keyword_biblioteca` id 4 sin dinero (0 en `negative_biblioteca`) y ledger `581–584` (1 unidad de cuota). Dry-run de reversa verificado (`pendientes: 4`, huella `ae7b4f25732e5016`); dueño aprobó no ejecutar borrado destructivo sobre un término ganador (5.69 % ACoS), acreditando la reversa con sonda 0.1, reversa real del `job=2` y pruebas 04 B-C / 05 B. Paso 4 de Fábrica 01 anexado (77 decisiones en grupo 1 desde 19-sep). | Sin siguiente paso: cierre operativo completo de FABRICA 01, FABRICA 02 D.3 (`AUTO-02`) y ORBIT 17. [Acta de cierre](../docs/evidencia/fabrica-02/D.3/cierre-p02-2026-11-27.md). | Operación + dueño |
| P03 | ADS PROTECCION 01: C.6 | **CERRADO, 27-nov-2026 UTC.** `ads_propuestas_campana` descartado/apagado por decisión del dueño. Regla económica corregida a `economic_pause_v2` por mercado (`ads_pause_economica_amazon_mx`/`_us`) con guarda de ventana reciente y recuperación rentable (backtest 46d reales: `0/47` FP `+10d` frente a `12/59` de `v1`), tabla append-only `pausa_economica_evaluacion` (`0105` + reversa) y cortacircuito de 5/ciclo (PR plan `#800`, PR código `#801` `56671091`, P2 en `#802`). Desplegado y verificado en `goncloud` (`orbit_decide` permisos OK, fail-closed OK, replay `2/2` en US `4924`/`5891`, `0` en MX) y encendido en **`live`** (`config_version 69/70`, con veto de 48h, revalidación y reversa). | Sin siguiente paso: cierre operativo completo de ADS PROTECCION 01 (P2 menores en `#802`). [Acta de cierre](../docs/evidencia/ads-proteccion-01/cierre-p03-2026-11-27.md). | Operación + dueño |
| P04 | Producto y costo PEZ en MeLi | **CERRADO, 5-nov-2026 UTC — PEZ 01 / PR #526.** Nueve variantes vinculadas a SET activos con cofre, incluido Blanco. Costos externos coinciden 9/9; siete miembros activos y dos inactivos conservados. | Aliases persistentes desplegados, lote reversible aplicado y lectura posterior sin cambios fuera del alcance. Las diferencias de valoracion/medidas se siguen en P13. [Cierre](../docs/evidencia/quick-wins-2026-11-04/pez-correccion/README.md). | Operacion |
| P05 | REP-FOLLOW-2: reseñas Amazon | Foto histórica: cero ingestas exitosas/reseñas Amazon; último bloqueo documentado, crédito Apify. **Actualización 15-nov: ARREGLOS 03 D-B retira ese camino.** | Ejecutar y acreditar la retirada prevista en D-B; no recargar crédito ni activar el cron antiguo. Una fuente Amazon para Reputación 02 requiere brief propio. No se declara retirada ejecutada. | Datos / operación |
| P06 | ORBIT 02, 3.4: cloudflared | Rotación diferida por falta de inventario de rutas en Cloudflare Zero Trust. No se volvió a inspeccionar proceso ni token. | Inventario y ventana acordada; luego rotación y comprobación de dominios según 3.4. | Dueño + operación |
| P07 | BIDS 01: primer lote de archivo | **CERRADO, 27-nov-2026 UTC.** Prerrequisitos 05 C y 05 D desplegados (`c16c8c55`). Simulación actual ejecutada (MX `peso_muerto`: 188, US: 1) y primer lote piloto autorizado por el dueño (`inertes-2026-11-27`, MX `peso_muerto`, `--limite 10`, huella `b68f37b82cfefcdbb0476bb01f0b7162eca12c71d325863c7498f6558b4ef8a4`, `go_literal = "de acuerdo ejecutalo"`): 10/10 archivadas en Amazon con readback `ARCHIVED`, ledger `keyword_archivo_manual` `id=1..10` en `applied` y visible en `/api/dashboard/inertes` (`applied: 10, failed: 0`). | Sin siguiente paso: cierre operativo completo de BIDS 01; lotes adicionales quedan a discreción del dueño con la misma ceremonia. [Acta de cierre](../docs/evidencia/bids-01/cierre-p07-2026-11-27.md). | Dueño + operación |
| P08 | JEV ADS 03: medición de 30 días | **CERRADO, 27-nov-2026 UTC.** Informe del 17-nov (`at` job 8, `jev-ads03-d3.out`) recuperado y contrastado a 14d post-bloqueo (`datos_hasta = 2026-11-26`): 25/25 aprobados en MX (`100 %`, ahorro equivalente `488.97 MXN/mes`, 2.93× sobre el umbral de `167 MXN/mes`) y 23/23 en US (`100 %`, `59.13 USD/mes`), 48/48 aplicados y 0 quitados; gasto post-bloqueo `2.95 MXN` / `0.00 USD` mientras los 13 grupos vendieron `32,333.86 MXN` (29 pedidos) y `996.20 USD` (10 pedidos). Dueño decidió mantener `jev.bloqueo = true`. | Sin siguiente paso: `jev.bloqueo` continúa encendido (`config_version` 66). [Acta de cierre](../docs/evidencia/jev-ads-03/ejecucion/D.3/cierre-p08-2026-11-27.md). | Operación + dueño |
| P09 | ORBIT 19: conciliación y cobertura | **CERRADO, 28-nov-2026 UTC.** Causa de `0.3` (`2026-09-03` MX) probada al centavo como desfase de `observed_at` (conciliada en `2026-09-07 08:08 UTC`: `3,427.58 MXN` / `2` compras). Conciliación actual madura `[D-39..D-10]`: MX **30/30 días exactos** (`178,013.54 MXN`, `173` compras) y US **30/30 exactos** (`3,301.50 USD`, `31` compras). Cobertura solo-actividad: MX `252/342` (`73.7 %` en 30d; `289/342` = `84.5 %` en 111d) y US `96/176` (`54.5 %` en 30d; `105/176` = `59.7 %` en 111d), conservando ausencias como `Sin datos`. Oferta destacada (`spapi_pricing`) y stock FBA vendible fresco (`spapi_inventario` con `details=true`, solo `fulfillable_quantity`) conectados en Fábrica (PR `#804`, `41958d1e`), desplegados y verificados en vivo (`342/342` MX: `262` propia / `80` sin ofertas, `105` FBA vendibles + `3` en tránsito filtrados a 0; `176/176` US: `134` propia / `42` sin ofertas; `fbm` conservado aparte). | Sin siguiente paso: cierre operativo completo de ORBIT 19 (3 notas P2 menores en el acta). [Acta de cierre](../docs/evidencia/orbit-19/cierre-p09-2026-11-27.md). | Datos / operación |
| P10 | REP-FOLLOW-1: reseñas compartidas | Mejora opcional: atribución multi-item limitada por deduplicación de reseñas compartidas. | Medir impacto; si se prioriza, definir identidad y atribución con fuente real. No bloquea v1. | Datos |
| P11 | REP-FOLLOW-3: lotes Apify | Propuesta histórica condicional sobre 518 URL/300 s, sin medición concluyente. **Superada como siguiente acción por la retirada de Apify de ARREGLOS 03 D-B.** | No construir lotes, recargar ni activar Apify por este pendiente. Conservar historia; cualquier fuente futura se define en otro brief. | Datos / operación |
| P12 | Transversales | Deuda declarada: secretos con permisos 600/uid, sin cifrado en reposo, y observabilidad parcial. No se auditó toda la infraestructura. | Delimitar y priorizar revisión específica. Mantener gitleaks y secretos fuera del repositorio. | Dueño + operación |
| P13 | Datos PEZ: medidas | **PARCIAL 8/9**, 5-nov UTC. Usuario confirmo 22 mm; ocho descripciones publicadas y verificadas. Precios, existencias, SKU y estados conservados. Costos ya conciliados 9/9, sin recosteo. | Solo queda MLM2727437671 / NH-GAM-AZU-PEZ-PLA: MeLi la inhabilito el 31-oct UTC por categoria incorrecta; rechazo la edicion HTTP 400, sin REMEDY. Resolver ese bloqueo con alcance propio; texto corregido y respaldo guardados. No falta confirmar medidas. [PEZ 02](pez-02.md), [publicacion y evidencia](../docs/evidencia/quick-wins-2026-11-04/pez-p13/publicacion/README.md). | Catalogo / dueño |

## Implementación pendiente

Ningún plan pendiente en la cola autorizada: **ARREGLOS 04 → TEMPORADA 01 → ARREGLOS 05** quedaron cerrados, integrados y desplegados al **25-nov-2026** (`c16c8c55`). Ver cierre completo en [ORDEN-IMPLEMENTACION](ORDEN-IMPLEMENTACION.md) y [docs/evidencia/arreglos-05/Z/resultado.md](../docs/evidencia/arreglos-05/Z/resultado.md).

## Futuros sin implementación autorizada

Esta tabla no es una cola de ejecución ni autorización. BIDS 02 ya entregó herramientas manuales de AUTO-05 y AUTO-06; faltan contratos y planes del automatismo. AUTO-03 ya fue entregado en DECISIONES PRODUCTO 01 y continuado en ORBIT 07. AUTO-04 (ubicaciones automáticas) está implementado en práctica: [plan](../docs/superpowers/plans/2026-11-26-auto-04-ubicaciones.md), PR #785, #786 y el PR de pantalla; el paso a `live` exige la compuerta de la sección 9 de su [spec](../docs/superpowers/specs/2026-11-26-auto-04-ubicaciones-design.md).

| ID / futuro plan | Qué falta construir | Preparación y dependencias |
|---|---|---|
| AUTO-05 — presupuestos automáticos | Redistribución entre campañas/grupos, ritmo de gasto e intradía; posible AMS/Stream. | Ajuste manual existente. Falta política, autoridad única de escritura, límites por moneda y reversa. Cuotas de operaciones no son presupuestos publicitarios. |
| AUTO-06 — reactivación y limpieza | Decidir qué reactivar/limpiar automáticamente. | BIDS 01/02 aportan herramientas manuales, retiro/reposición y regreso de keywords dañadas. Falta evaluación y plan; no automatizar el archivo irreversible por defecto. |
| meli-ads-01 — campañas MeLi | Propuestas de campañas, sin escritura publicitaria. | Sin plan formal. Restricción de escritura a nivel cuenta como contrato; revalidarla al planificar. La ingesta MeLi del Resumen no implementa gestión de campañas. |
| AUTO-08 / promociones-01 | Descuentos, simulación de margen y autorizaciones. | Sin plan formal. Márgenes y Repricing disponibles; definir fuentes/APIs y alcance. |
| AUTO-09 / reputacion-02 | Respuestas asistidas, Account Health, Buy Box, reseñas sospechosas, sentimiento y acciones por reputación. | [Stub](reputacion-02.md), sin brief ejecutable ni autorización de implementación; fuente Amazon por redefinir tras ARREGLOS 03 D-B, sin reactivar Apify por P05. Cada acción comercial necesita contrato y reversa. |
| Integraciones cruzadas | Reputación → campañas/promociones; envíos → reputación. | Sin plan formal; dependen del consumidor y de una fuente demostrada. |

## Entregas integradas, cerradas o sustituidas

**AUTO-03 entregado:** [DECISIONES PRODUCTO 01](decisiones-producto-01.md), A-I, `f86ed7dc`; [ORBIT 07](orbit-07-motor-decisiones.md), `7cf7c9ab`. La pausa por producto queda lista para vivo y en práctica por decisión documentada; el encendido del dueño no es implementación pendiente. [Spec](../docs/superpowers/specs/2026-10-24-decisiones-producto-design.md), [síntesis](../docs/evidencia/auto-03/plan/sintesis.md). No se autoriza por arrastre pausa económica de productos con ventas en grupos compartidos.

**Base de noviembre integrada:** ARREGLOS 01 A-Z, ARREGLOS 02 A/D/B/C, ARREGLOS 03, CLARIDAD 01 y DISEÑOS 01 A/B/C, además de ORBIT 07. Runtime coincidente según huellas; los recibos específicos de migraciones/operación se consolidan sin reimplementar. SHA y límites en [la base integrada](ORDEN-IMPLEMENTACION.md#base-integrada-no-reejecutar).

**Otros seguimientos documentales:** SP-API 02 conserva acta de nocturna previa al despliegue y requiere recuperar evidencia posterior; RESUMEN ADS 04 conserva revisión visual productiva pendiente. No reabrir su código. P10/P12 son opcionales sin plan; P11 deja de proponer lotes por la retirada de Apify. Briefs, guías, validaciones y `jev-ads-03.md-E` no son nuevas implementaciones.

«Cerrado» remite al cierre documentado; no es una nueva auditoría completa de
cada módulo. Los residuales siguen arriba, separados del desarrollo.

| Planes | Estado / fuente |
|---|---|
| ORBIT 01, 03, 04; GUARDRAILS 01; DASHBOARD 01; CORTES 01; CAMPANAS 01; CAMPANA ACTIVA 01; ESTRUCTURA 01 | Tareas registradas completas en sus planes. ORBIT 02 conserva P06. |
| ORBIT 05 preflight y ORBIT 05 / AUTO-01 | Cerrados; 2.3/2.5 cerraron el **1-oct**, con harvest automático verificado. [Acta](../docs/evidencia/orbit-05/cierre-2026-10-01.md). |
| ORBIT 06 | Cerrado 4-sep: consolidación y target por margen en vivo. |
| BIDS 01; CORTES UI 01 | **Cerrados** (15-sep; P07 cerrado 27-nov-2026 UTC con primer lote `inertes-2026-11-27`, 10/10 `ARCHIVED` en MX `peso_muerto`). [Acta](../docs/evidencia/bids-01/cierre-p07-2026-11-27.md). |
| FABRICA 01 / FABRICA UI 01 / ORBIT 17; FABRICA 02 / AUTO-02 | **Cerrados 27-nov-2026 UTC (P02).** Primer harvest natural por grupo verificado (`job=12`, exacta + 2/2 hermanas + biblioteca + ledger), dry-run de reversa verificado (dueño optó por no archivar un término ganador) y paso 4 de Fábrica 01 anexado (77 decisiones en grupo 1). [Acta](../docs/evidencia/fabrica-02/D.3/cierre-p02-2026-11-27.md). |
| ORBIT UI 01 / ORBIT 18; CATALOGO CAMPANAS 01 / ORBIT 19 | **Cerrados** (núcleos entregados 8-sep; P09 cerrado 28-nov-2026 UTC: conciliación `30/30` MX y US, cobertura solo-actividad registrada, y Oferta destacada + stock FBA vendible SP-API conectados y verificados en producción en `41958d1e`, PR `#804`). [Acta](../docs/evidencia/orbit-19/cierre-p09-2026-11-27.md). |
| MARGEN ESTIMADO 01 / AUTO-10 | Cerrado 8-sep para su alcance original. Ampliaciones FBM/US/MeLi entregadas después en REPRICING 02. |
| REPUTACION 01 | v1 cerrada 8-sep; MeLi operativo. Amazon y mejoras en P05/P10/P11; v2 por construir. |
| SP-API 01 y 02 | Cerrados 10-sep y 22-oct: lecturas y resistencia a 429 entregadas. |
| JEV ADS 01, 02 y 03 | **Cerrados** (JEV 03 encendido y P08 cerrado 27-nov-2026 UTC: `488.97 MXN/mes` + `59.13 USD/mes`, 100 % aprobación, dueño decidió mantener encendido). [Acta](../docs/evidencia/jev-ads-03/ejecucion/D.3/cierre-p08-2026-11-27.md). Avisos JEV 02 apagados por decisión; abstención/ruteo/fábrica no se construyen por aquel cierre. |
| REPRICING 01 | **Sustituido desde el 8-oct** por REPRICING 02 para D.2/D.3/E/0/B/M. Sus 22 filas históricas no son trabajo pendiente. |
| REPRICING 02 / AUTO-07 | Cerrado 17-oct; foto histórica de goals: MX 231, US 99, MeLi 45 en `live`. P04 cerrado mediante PEZ 01; nueve correspondencias verificadas. Datos PEZ pendientes en P13. Incidente MeLi de octubre corregido y desplegado (#504). |
| BIDS 02 | Construido, desplegado y encendido; remate cerrado 13-oct. Sus guías no son planes pendientes adicionales. |
| INGESTA 01 | Cerrada 22-oct. Informe del **28-oct recuperado**: 21/21 corridas exitosas, 14/14 ciclos `done`, cero días sin éxito por fuente. Guardas históricas comprobadas para ambas plataformas. [Evidencia](../docs/evidencia/estado-planes-2026-11-04/README.md). |
| RESUMEN ADS 01/02/03/04 | Integrados. Archivos principales de 01/02/03 comprobados en producción el 4-nov. ADS 04 (rediseño del Resumen y ACoS objetivo en settings, #524, integrado 5-nov 02:01 UTC): 10/10 archivos de `app/` iguales entre master `9fb5a63f` y `orbit-app-1` (SHA256, 5-nov 03:22 UTC; contenedor iniciado 02:10 UTC; [hashes](../docs/evidencia/resumen-ads-multicanal/04-despliegue/hashes.json)); sin revisión visual en producción. P01 cerrado el 5-nov-2026 09:00 UTC (job 10 OK); no están por construir. |
| ADS PROTECCION 01 / Fase D | **Cerrados** (P03 cerrado 27-nov-2026 UTC: `economic_pause_v2` desplegado en `56671091` con migración `0105` y encendido en `live` MX/US en `config_version 69/70`; `ads_propuestas_campana` descartado/apagado; P2 menores en `#802`). [Acta](../docs/evidencia/ads-proteccion-01/cierre-p03-2026-11-27.md). |
| ENVIOS 01 | Seguimiento de envíos Amazon y MeLi integrado y desplegado con migración `0102` (#780 `52cb4b02`, #782 `3c5e53fb`; [acta](../docs/evidencia/envios-01/cierre-produccion.md)). Primera corrida automática del cron verificada en verde (26-nov 06:25–06:40 UTC, runs `1908`–`1910`). Rama `feat/envios-01-vencimiento` (#783, **pendiente de despliegue**): mantiene `in_hub`/`in_packing_list` en `en_camino` (transición real `in_hub` → `in_packing_list` → `shipped`), tabla única de estados, cierre por vencimiento configurable (`0103`, `envios_meli_dias_cierre = 90`, `--releer`), enlace en barra móvil, y respuestas ilegibles fallan corrida salvo envíos >30 días fuera de búsqueda. Abierto: desplegar #783 con migración `0103`; la vista de 30 días deja salir problemas sin resolver. |

## Cómo mantener este registro

Al cerrar un pendiente, actualizar la cola en `ORDEN-IMPLEMENTACION.md`, su fila, el plan detallado y la descripción
de `manifest.json`, con evidencia fechada. Distinguir lo comprobado en producción
de lo integrado. Conservar historia en Git y actas: una fecha vencida o un PR
integrado no bastan para cerrar una operación. Enlazar aquí los borradores fuera
del manifest y declarar su estado.
