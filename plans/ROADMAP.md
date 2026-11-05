# ROADMAP maestro — Orbit

Actualizado el **2026-11-04, America/Vancouver** (consultas de producción:
2026-11-05 UTC), contra `master` **6bce3589** y los cierres de cada plan.
Esta es la fuente única de pendientes; `docs/PENDIENTES-AUTOMATIZACION.md`
redirige aquí. Los planes detallados conservan contratos e historia.

[Evidencia de la conciliación](../docs/evidencia/estado-planes-2026-11-04/README.md):
GitHub, consultas con `ORBIT_DSN_READ` en transacciones `READ ONLY`, hashes del
contenedor y recordatorios del servidor. Integración, despliegue y validación
operativa son estados distintos. Los datos históricos llevan fecha. Esta
revisión no modificó producción. Los [quick wins](../docs/evidencia/quick-wins-2026-11-04/README.md)
añaden el arreglo del checker y los cierres operativos A.4/B.4; su integración
se registra por separado de las lecturas productivas.

El registro apunta a `resumen-ads-multicanal`, la última entrega integrada y
desplegada, con seguimiento P01. No selecciona ni autoriza el siguiente desarrollo.
Antes de implementar un frente nuevo se necesita un plan con alcance, fuentes
verificadas, dependencias, pruebas proporcionales, revisión y despliegue.

## Pendientes de operación y cierre

Los IDs P01–P12 identifican seguimientos, no planes de implementación.
«Por comprobar» significa que falta evidencia de cierre, no que el sistema falle.

| ID | Frente | Estado comprobado / impacto | Siguiente paso y criterio de cierre | Responsable |
|---|---|---|---|---|
| P01 | Resumen Ads multicanal | Ingesta inicial y cron 08:15 UTC verificados. Hasta 3-nov: 808/808 filas iguales; 4-nov difiere +13.47 MXN por una versión posterior de accounting. Comprobación RO instalada y programada: **job 10, 5-nov 09:00 UTC**. | Leer el resultado del job 10 y exigir nueva corrida exitosa, conciliación y misma huella/as-of de fuente. Sigue abierto hasta ese resultado; el snapshot inicial no se conservó. [Seguimiento](../docs/evidencia/quick-wins-2026-11-04/seguimiento-meli/LEEME.md). | Operación |
| P02 | FABRICA 02 D.3 / cierre ORBIT 17 | 11 harvests `done`, ninguno con hermanas ni resuelto por grupo. La sonda de creación de Fábrica 01 ya se hizo el 9-sep; su tarea 11 conserva pendiente el cierre del paso 4. | Seguir el primer harvest natural de grupo: exacta, hermanas, biblioteca y ledger; ensayar su reversa con el go previsto en D.3. Anexar la evidencia del paso 4 de Fábrica 01, sin repetir la creación. | Operación + dueño para reversa |
| P03 | ADS PROTECCION 01: C.6 | P1 ejecutado: config 42 económica apagada, nueve goals live, caps de 1M y una PAUSE tradicional pendiente. P2: 48 cruces numéricos en 7 entidades; 62/62 filas externas coinciden, cobertura parcial. Ninguna cohorte acredita revisión completa a +10d; elegibilidad contrafactual incompleta. | Completar evidencia de maduración, estado/goal histórico y ventanas externas antes del gate P3. Preparar captura prospectiva si el histórico no es recuperable. La medición no demuestra FP=0 ni habilita sombra/encendido. [P1](../docs/evidencia/quick-wins-2026-11-04/c6-p1/README.md), [P2](../docs/evidencia/quick-wins-2026-11-04/c6-p2/README.md). | Operación; dueño para sombra/activación |
| P04 | Producto y costo PEZ en MeLi | **PEZ sí se vende y sí tiene BOM y componentes con costo.** Los nueve SKU exactos de MeLi llevan a 27 registros y 29 BOM archivados; también existen referencias PEZ activas bajo otros códigos. El sync exige producto activo y costo final positivo; Orbit no calcula desde BOM. El chatter/tracking consultado no explica el archivo. | Resolver la correspondencia con la referencia/BOM vigente y publicar su costo por el camino existente, con fecha y verificación. Una receta histórica calculable no acredita que sea la vigente. Se corrige el diagnóstico anterior de «falta receta»: es un problema de correspondencia/sincronización. [Trazabilidad BOM](../docs/evidencia/quick-wins-2026-11-04/pez-bom/README.md). | Operación; dueño si queda ambigüedad de composición |
| P05 | REP-FOLLOW-2: reseñas Amazon | Cero ingestas Amazon exitosas y cero reseñas Amazon; MeLi sí sigue entrando. Último bloqueo documentado: crédito Apify agotado; no se consultó hoy su saldo. | Resolver crédito/ejecución autorizada; comprobar ingesta y cron semanal dentro del presupuesto. Desbloquea Reputación 02. | Dueño + operación |
| P06 | ORBIT 02, 3.4: cloudflared | Rotación diferida por falta de inventario de rutas en Cloudflare Zero Trust. No se volvió a inspeccionar proceso ni token. | Inventario y ventana acordada; luego rotación y comprobación de dominios según 3.4. | Dueño + operación |
| P07 | BIDS 01: primer lote de archivo | Ledger `keyword_archivo_manual` vacío. El 4-oct era la primera fecha elegible, no una orden de ejecución. | Repetir ensayo con datos actuales y decidir lote con el go requerido. Archivar pierde identidad/historia; reponer no equivale a desarchivar. | Dueño + operación |
| P08 | JEV ADS 03: medición de 30 días | Bloqueo encendido; `atq` conserva job 8 para **17-nov-2026 09:00 UTC**. | Revisar aprobación, ahorro y ventas posteriores; decidir continuidad con el criterio del plan. | Operación + dueño |
| P09 | ORBIT 19: conciliación y cobertura | Núcleo cerrado. Residuales: causa de ventas/compras de 0.3 no demostrada, cobertura solo-actividad y Featured Offer en catálogo sin cierre específico. Las lecturas SP-API posteriores no prueban ese cierre. | Comparar fuentes externas con misma ventana/vintage; registrar cobertura y verificar Featured Offer en este consumidor. No repartir agregados a productos ni convertir ausencias en cero. | Datos / operación |
| P10 | REP-FOLLOW-1: reseñas compartidas | Mejora opcional: atribución multi-item limitada por deduplicación de reseñas compartidas. | Medir impacto; si se prioriza, definir identidad y atribución con fuente real. No bloquea v1. | Datos |
| P11 | REP-FOLLOW-3: lotes Apify | Condicional: sin medir si las 518 URL del inventario histórico caben en 300 s con crédito. | Medir tras P05; dividir en lotes solo si el volumen vigente y el tiempo lo justifican. | Operación |
| P12 | Transversales | Deuda declarada: secretos con permisos 600/uid, sin cifrado en reposo, y observabilidad parcial. No se auditó toda la infraestructura. | Delimitar y priorizar revisión específica. Mantener gitleaks y secretos fuera del repositorio. | Dueño + operación |

## Planes por construir

El orden no fija prioridad ni fecha. BIDS 02 ya cubre partes manuales de
AUTO-03 a AUTO-06; esta tabla recoge el alcance restante.

| ID / futuro plan | Qué falta construir | Preparación y dependencias |
|---|---|---|
| AUTO-03 — decisiones Ads por producto | Metas y acciones según rendimiento atribuible, margen y disponibilidad; distinguir product ad de publicación. | [Diseño propuesto del 24-oct](../docs/superpowers/specs/2026-10-24-decisiones-producto-design.md) y [alternativas](../docs/evidencia/auto-03/diseno/sintesis.md) guardados. No es plan ejecutable ni aprobación. Revalidar fuentes/topología y sellar alcance; la propuesta inicial no cubre todas las pausas económicas en grupos compartidos. |
| AUTO-04 — placements automáticos | Ajustes autónomos por ubicación, límites y reversa. | BIDS 02 ya tiene datos, pantalla y ajustes del dueño. Falta plan del automatismo y confirmar fuente/API al diseñarlo. |
| AUTO-05 — presupuestos automáticos | Redistribución entre campañas/grupos, ritmo de gasto e intradía; posible AMS/Stream. | Ajuste manual existente. Falta política, autoridad única de escritura, límites por moneda y reversa. Cuotas de operaciones no son presupuestos publicitarios. |
| AUTO-06 — reactivación y limpieza | Decidir qué reactivar/limpiar automáticamente. | BIDS 01/02 aportan herramientas manuales, retiro/reposición y regreso de keywords dañadas. Falta evaluación y plan; no automatizar el archivo irreversible por defecto. |
| meli-ads-01 — campañas MeLi | Propuestas de campañas, sin escritura publicitaria. | Sin plan formal. Restricción de escritura a nivel cuenta como contrato; revalidarla al planificar. La ingesta MeLi del Resumen no implementa gestión de campañas. |
| AUTO-08 / promociones-01 | Descuentos, simulación de margen y autorizaciones. | Sin plan formal. Márgenes y Repricing disponibles; definir fuentes/APIs y alcance. |
| envios-01 | Seguimiento de envíos Amazon y MeLi. | Sin plan formal. SP-API Orders disponible; validar MeLi Orders/Shipments y su integración. |
| AUTO-09 / reputacion-02 | Respuestas asistidas, Account Health, Buy Box, reseñas sospechosas, sentimiento y acciones por reputación. | [Stub](reputacion-02.md), pendiente de brief y P05. Cada acción comercial necesita contrato y reversa. |
| Integraciones cruzadas | Reputación → campañas/promociones; envíos → reputación. | Sin plan formal; dependen del consumidor y de una fuente demostrada. |

## Entregas cerradas o sustituidas

«Cerrado» remite al cierre documentado; no es una nueva auditoría completa de
cada módulo. Los residuales siguen arriba, separados del desarrollo.

| Planes | Estado / fuente |
|---|---|
| ORBIT 01, 03, 04; GUARDRAILS 01; DASHBOARD 01; CORTES 01; CAMPANAS 01; CAMPANA ACTIVA 01; ESTRUCTURA 01 | Tareas registradas completas en sus planes. ORBIT 02 conserva P06. |
| ORBIT 05 preflight y ORBIT 05 / AUTO-01 | Cerrados; 2.3/2.5 cerraron el **1-oct**, con harvest automático verificado. [Acta](../docs/evidencia/orbit-05/cierre-2026-10-01.md). |
| ORBIT 06 | Cerrado 4-sep: consolidación y target por margen en vivo. |
| BIDS 01; CORTES UI 01 | Cerrados 15-sep. BIDS 01 conserva P07; la confirmación de Cortes UI ya ocurrió. |
| FABRICA 01 / FABRICA UI 01 / ORBIT 17; FABRICA 02 / AUTO-02 | Implementados/desplegados; cierre operativo P02. AUTO-02 ya tiene plan y código. D.4 corregida y desplegada. |
| ORBIT UI 01 / ORBIT 18; CATALOGO CAMPANAS 01 / ORBIT 19 | Núcleos entregados; ORBIT 19 cerrado 8-sep, residuales P09. |
| MARGEN ESTIMADO 01 / AUTO-10 | Cerrado 8-sep para su alcance original. Ampliaciones FBM/US/MeLi entregadas después en REPRICING 02. |
| REPUTACION 01 | v1 cerrada 8-sep; MeLi operativo. Amazon y mejoras en P05/P10/P11; v2 por construir. |
| SP-API 01 y 02 | Cerrados 10-sep y 22-oct: lecturas y resistencia a 429 entregadas. |
| JEV ADS 01, 02 y 03 | Cerrados. JEV 03 encendido, P08 programado. Avisos JEV 02 apagados por decisión; abstención/ruteo/fábrica no se construyen por aquel cierre. |
| REPRICING 01 | **Sustituido desde el 8-oct** por REPRICING 02 para D.2/D.3/E/0/B/M. Sus 22 filas históricas no son trabajo pendiente. |
| REPRICING 02 / AUTO-07 | Cerrado 17-oct; goals vigentes: MX 231, US 99, MeLi 45 en `live`. P04 conserva ampliación de cobertura. Incidente MeLi de octubre corregido y desplegado (#504). |
| BIDS 02 | Construido, desplegado y encendido; remate cerrado 13-oct. Sus guías no son planes pendientes adicionales. |
| INGESTA 01 | Cerrada 22-oct. Informe del **28-oct recuperado**: 21/21 corridas exitosas, 14/14 ciclos `done`, cero días sin éxito por fuente. Guardas históricas comprobadas para ambas plataformas. [Evidencia](../docs/evidencia/estado-planes-2026-11-04/README.md). |
| RESUMEN ADS 01/02/03 | Integrados y archivos principales comprobados en producción el 4-nov. Queda P01; no están por construir. |
| ADS PROTECCION 01 Fase D | Tareas completadas; el plan padre conserva P03. |

## Cómo mantener este registro

Al cerrar un pendiente, actualizar su fila, el plan detallado y la descripción
de `manifest.json`, con evidencia fechada. Distinguir lo comprobado en producción
de lo integrado. Conservar historia en Git y actas: una fecha vencida o un PR
integrado no bastan para cerrar una operación. Enlazar aquí los borradores fuera
del manifest y declarar su estado.
