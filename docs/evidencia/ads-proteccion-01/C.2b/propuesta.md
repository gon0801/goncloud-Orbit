# C.2b — parametros prefijados (2026-09-25 UTC, definiciones previas)

El dueno fijo por adelantado, sobre propuesta del lead (no sobre el reporte
C.2, que aun no existe):

| Parametro | Valor |
| --- | --- |
| Horizonte de maduracion (falso positivo = candidato que deja de cruzar el limite con atribucion madurada al horizonte) | 10 dias |
| Tolerancia (falsos positivos que frenan el live) | 0: 1+ FP -> la regla se revisa antes de live |
| Piso propio del target | NO: se usa el target de la cascada tal cual |

Cita: "10 dias (Recomendado)", "Cero (Recomendado)", "No, sin piso
(Recomendado)", 25-sep-2026.

Pendiente para cerrar C.2b: aceptar o cambiar la regla SOBRE el reporte C.2
medido. Sin ese literal no hay merge C.3/C.4 ni live de C.
