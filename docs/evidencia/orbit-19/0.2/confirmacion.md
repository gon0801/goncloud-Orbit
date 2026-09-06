# ORBIT 19 / 0.2 — Confirmacion del dueno y cierre de contrato

Fecha: **2026-09-06 21:31 UTC**. El dueno respondio `confirmado` a D1–D4
con las tres precisiones de revision. Esto cierra el negocio de 0.2.
No autoriza gasto, sonda Amazon ni implementar A/B en este acto.

## D1–D4 (cerradas)

| ID | Cierre |
|---|---|
| D1 | Orden inicial por `margen_neto_pct` maduro (porcentaje, ventana `[2026-02-20, D-15)` UTC). `dias_con_venta` visible. NULL al final, nunca 0%. MX/US no se mezclan. Ads ausente = Sin datos hasta cobertura **demostrada** (COMPLETED no basta). |
| D2 | ACoS manual obligatorio si algun producto tiene margen NULL, =0, <0, o si el clamp superaria el margen conocido. El manual **no acredita rentabilidad**. Preview/revision declaran cero, negativo o inferior al objetivo. Sigue seleccionable. Sin margen proyectado previo. |
| D3 | Solo campanas nuevas. Anadir a existentes queda fuera (otro plan con reversa). |
| D4 | Muestra limitada (1–29 fechas, integridad OK) visible en UI, separada del maduro. No gobierna target ni el sort D1. `v_margen_producto` no se relaja. |

## Precisiones de revision (parte del cierre)

1. Por probar exige evidencia de cobertura.
2. Objetivo manual no acredita rentabilidad.
3. Confirmar D1–D4 no bastaba: el contrato tecnico de abajo queda cerrado
   en esta misma tarea.

## Contrato tecnico cerrado (DoD 0.2)

| Pieza | Donde | Estado |
|---|---|---|
| API/CLI v2, rechazo de cuerpos mezclados, un normalizador | `contrato-api-cli-v2.md` | Cerrado |
| Compatibilidad v1 (`productos` / `--productos`, huella intacta) | idem | Cerrado |
| Preview declara margen 0 / neg / inferior al objetivo | idem | Cerrado |
| Reserva 0019 (grupo/listing, margen nullable, origen) | `migracion-rollback.md` | Cerrado; A.1. F2 ya no usa 0019 |
| Reserva 0020 (metricas producto anunciado) | idem | Cerrado; aplica B.1 (Ads MX verificada_parcial) |
| 0021 disponibilidad | idem | Opcional; Featured Offer no verificada |
| Reversa: `fabrica.creacion=v1` deshabilita altas v2; lector/registrar/reconciliar/pausar v2 siguen | idem | Cerrado; prueba en A.1/A.5 |
| Spec ORBIT19 + enmienda FABRICA 01 / UI 01 | `docs/superpowers/specs/` | Cerrado; codigo de produccion no cambia hasta A |

Produccion F1 sigue vigente hasta A.5. Este cierre no es go de creacion
real ni de deploy.
