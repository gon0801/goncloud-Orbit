# Orbit — pendientes de automatizacion

Registrados por solicitud del dueno el 2026-09-06, tras revisar el alcance real
del optimizador. Estado: pendientes para desarrollar sus planes y despues
implementarlos. Este registro no inicia ejecucion ni cambia el trabajo de Grok.

Spec skip reason: inventario documental de pendientes; no se fijan reglas,
umbrales, APIs ni cambios de comportamiento. Los planes futuros deben definirlos.
team_validation_mode: not_required_lightweight (registro de la conversacion).

## Pendientes

| ID | Pendiente | Alcance para el futuro plan o seguimiento | Base y dependencia | Estado |
|---|---|---|---|---|
| AUTO-01 | Verificar harvest automatico completo | Seguir una propuesta real que cumpla las reglas: creacion exacta, negativo de origen, readbacks, registro y recuperacion. No forzar una propuesta ni modificar vetos para obtener evidencia. | Retomar ORBIT 05 tareas 2.3/2.5; el mecanismo existe. No crear otro motor. | Pendiente de verificacion operativa |
| AUTO-02 | Harvest por grupo de campanas | Enrutar hacia la hermana exacta, negativos cruzados y actualizacion de bibliotecas desde resultados aplicados; definir compatibilidad con campanas existentes y reversa. | FABRICA 01 F2, fuera de F1. Coordinar con publicaciones/lotes v2 de ORBIT 19 y la sonda pendiente de la fabrica. | Pendiente de plan formal |
| AUTO-03 | Decisiones publicitarias por producto | Usar rendimiento Ads atribuible, margen, muestra, stock y disponibilidad para definir acciones y limites por producto. Distinguir pausar un product ad de desactivar la publicacion de venta; decidir el alcance antes de escribir. | ORBIT 19 entrega selector/comparacion, no este motor. Fuentes por mercado conciliadas y disponibilidad desconocida explicita. | Pendiente de plan formal |
| AUTO-04 | Ajustes de placements | Evaluar y definir ajustes de Top of Search, Product Pages y otros placements soportados; datos por ubicacion, efecto sobre la puja efectiva, limites y reversa. | No hay motor de placements en Orbit. Confirmar fuente y API vigentes al planificar; no reutilizar el sistema viejo. | Pendiente de plan formal |
| AUTO-05 | Gestion de presupuestos | Definir redistribucion entre campanas/grupos, ritmo de gasto y limites por moneda, con una sola autoridad de escritura y reversa. | Hoy se capturan budgets al crear. Las cuotas de operaciones no son presupuestos de publicidad. Requiere datos comparables y objetivo explicito. | Pendiente de plan formal |
| AUTO-06 | Reactivacion y limpieza del catalogo publicitario | Evaluar cuando conviene reactivar keywords/targets/campanas y si hace falta automatizar la limpieza. Separar pausa reversible de archivado irreversible; reponer crea identidad nueva sin historia. | Ya hay herramientas manuales y BIDS 01. No convertirlas en automatismo por defecto ni sustituir su autorizacion. | Pendiente de evaluacion y plan |
| AUTO-07 | Repricing | Desarrollar el plan del motor de precios, con costos, margen, inventario, limites y reversa. | Modulos avanzados; no incluido en el selector de campanas. | Pendiente de plan formal |
| AUTO-08 | Promociones | Desarrollar el plan de promociones y su efecto economico, con datos y autorizaciones explicitas. | Modulos avanzados; no incluido en ORBIT 19. | Pendiente de plan formal |
| AUTO-09 | Reputacion | Desarrollar el plan de seguimiento y acciones de reputacion. Cualquier pausa derivada necesita contrato, evidencia y reversa. | Modulos avanzados; no incluido en ORBIT 19. | Pendiente de plan formal |
| AUTO-10 | Estimacion por venta antes de Ads | Precio, costo, comisiones, logistica y retenciones verificables por publicacion; desglose y ausencias explicitas. Comparacion informativa, sin cambiar targets ni motor. | [MARGEN ESTIMADO 01](../plans/margen-estimado-01.md), formalizado 2026-09-08 UTC: 12 tareas, bloque 0 de fuentes antes de A/B. | Plan formal propuesto; implementacion no iniciada |

AUTO-01 a AUTO-05 son los gaps principales de la revision. AUTO-06 a AUTO-09
conservan los pendientes adicionales mencionados. La prioridad final y el orden
entre planes se decidiran al planificar; no se asignan fechas ni presupuestos aqui.

## Estado comprobado al registrar

Consulta de produccion del 2026-09-06, aproximadamente 23:49 UTC, con
`orbit_read` y transacciones READ ONLY:

- Configuracion 15: optimizador live; goals habilitados en live para MX y US.
- Desde 2026-09-02: 78 ajustes de bid con `decision_application.verify_ok=true`
  (47 MX, 31 US), sobre keywords y product targets.
- En ese periodo: ninguna pausa ni negativo automatico aplicado registrado;
  una propuesta harvest live, rechazada; `harvest_job` sin filas.
- `fabrica_lote`: cero filas. Las herramientas manuales no equivalen a un
  motor automatico y estos conteos no incluyen todos sus actos historicos.
- Los ciclos del 2026-09-06 terminaron done con cero acciones nuevas.
  Falta de accion no demuestra averia: se registraron cooldown, evidencia
  insuficiente y condiciones de ajuste no cumplidas.

Son observaciones historicas, no un estado vivo. Actualizar la evidencia antes
de planificar o ejecutar. Gasto/clics de la sonda Ads MX estan conciliados;
ventas/compras entre reportes siguen sin conciliar, con causa no demostrada.

## Fuentes y limites

- [ORBIT 05](../plans/orbit-05.md): seguimiento del primer harvest automatico
  completo y cierre formal. AUTO-01 referencia ese pendiente, no lo duplica.
- [FABRICA 01](../plans/fabrica-01.md), seccion F2 fuera de este plan:
  harvest por grupos y bibliotecas. AUTO-02 solicita el plan futuro de F2.
- [BIDS 01](../plans/bids-01.md): herramientas manuales y guardas de archivado.
- [Modulos avanzados](traspaso/MODULOS-AVANZADOS.md): base de precios,
  promociones y reputacion. Traducir al stack Orbit, sin copiar codigo viejo.
- [Plan ORBIT 19, PR185](https://github.com/gon0801/goncloud-Orbit/pull/185):
  catalogo abierto y comparacion. Su bloque 2 / fase A en curso es distinto
  de FABRICA F2. Estos pendientes no se agregan a sus 17 tareas.
- [Contexto del proyecto](CONTEXTO.md): una fuente por numero, datos ausentes
  explicitos, dinero con moneda, madurez, conciliacion externa y reversa.

Antes de implementar cada funcion nueva: plan formal con alcance, fuente
verificada, contrato de decision, dependencias, pruebas, revision y despliegue.
No marcar estos items completados por escribir el plan: enlazar despues el plan
y conservar el seguimiento hasta evidencia de implementacion y validacion.
