# ORBIT 19 / 0.2 — D1–D4 (PROPUESTAS, no aprobadas)

Estado: **pendiente de respuesta del dueno**. Este archivo no cierra 0.2.
La solicitud de formalizar el plan y estas recomendaciones **no cuentan**
como aprobacion. Silencio o tiempo tampoco.

Evidencia de catalogo: `docs/evidencia/orbit-19/0.1/reporte.md`
(2026-09-06 21:00:01 UTC). 3+2 productos elegibles hoy; 115 multilisting;
175 sin margen maduro (84+48 sin ventas + 91+25 con &lt;30 fechas).

## D1 — Prioridad de comparacion

Propuesta del plan: rentabilidad con espacio para explorar.

**Recomendacion:** orden inicial por **margen observado descendente**
(solo filas con margen calculable), seccion **Por probar** visible y
seleccionable aparte, desempate `listing_id`. No hay nota 0–100 ni pesos.
Sin objetivo de comparacion, no etiquetar dentro/fuera de ACoS.

Motivo: el dueno pidio distinguir conveniencia, no un score. El catalogo
tiene 175 productos sin margen maduro; si el orden inicial es solo Ads o
solo ventas, esos quedan al fondo y se confunden con malos. Por probar
tiene que verse, no esconderse.

**No aprobado.** Si el dueno prefiere ventas, ACoS o evidencia primero,
se cambia el orden inicial en 0.4 sin tocar el motor.

## D2 — Lanzamientos sin margen

Propuesta del plan: ACoS manual del grupo, presupuestos/bids explicitos.

**Recomendacion:** aceptar. Obligatorio `objetivo.origen=manual_lanzamiento`
si **algun** producto del grupo no tiene margen maduro. No derivar el
minimo omitiendo nulls. No inventar margen proyectado como prerequisito
(eso replanifica fuentes: precio actual, comisiones, fees).

El target manual es intencion, no medicion. `margen_neto_pct` en el
snapshot puede ser null. `v_margen_producto` y el motor no se relajan.

Hoy `target_del_grupo` aborta si hay un None. Eso es correcto para v1;
v2 lo sustituye por objetivo manual explicito, no por un default 25%.

**No aprobado.** Si el dueno exige margen proyectado previo, se para A
hasta replanificar.

## D3 — Nuevas o existentes

Propuesta del plan: crear nuevas primero.

**Recomendacion:** **solo campanas nuevas** en este plan (igual que
FABRICA 01 decision 1). Anadir a existentes queda como ampliacion con
reversa propia; no se mete en A.1–A.5.

Motivo: no hay lote fabrica todavia (`fabrica_lote` vacia). Mezclar
adopcion de campanas viejas con el cambio de PK por listing duplica
el riesgo de rollback. El selector actual ya reporta existentes y no
las toca.

**No aprobado.** Si el dueno incluye existentes, 0.2 se replanifica
antes de implementar.

## D4 — Detalle economico con poca muestra

Propuesta del plan: mostrar margen observado con guardas de integridad,
separado del maduro.

**Recomendacion:** aceptar como contrato de UI/comparacion, **sin**
cambiar `v_margen_producto` (sigue exigiendo >=30 fechas para el
numero que gobierna target por margen).

- Muestra limitada: integridad OK (moneda unica, cobertura >=0.95,
  fees_sin_tipo=0, cubierta>0) y `dias_con_venta` 1–29. Se muestra
  el % con etiqueta "muestra limitada" y el conteo de fechas.
- Maduro: el de la vista actual (>=30).
- No calculable: el resto (sin ventas, mezcla de moneda, cobertura baja).

91+25 productos caen hoy en 1–29 fechas. Mostrarlos como null esconde
informacion; usarlos para derivar target automatico mentiria.

**No ratificado.** 0.2 no cierra D4 sin esa ratificacion.

## Como se cierra 0.2

Hace falta respuesta atribuible al dueno para D1, D2 y D3, y ratificacion
de D4. Hasta entonces los contratos de API/CLI/migracion de esta carpeta
son **propuestos**, no vigentes.
