# REPRICING 01 E.2, parte 1 — propuesta para el dueno

Estado: **propuesta; decision del dueno pendiente**. No habilita FBM ni cambia
precios. La parte 2 espera el veredicto E.0a y la medicion E.1 sobre el ledger
corregido. Fuente de los numeros: `E.1/medicion.md`, corrida del 17-sep-2026;
hechos 20–22 de `plans/repricing-01.md`. Antes de activar, refrescar la medicion.

## Decision propuesta

1. **Ventana: 180 dias.** En la foto medida, alcanzan seis ordenes con envio
   14 productos MX y 17 US. A 90 dias son 7 y 9; a 365 dias, 17 y 22, pero
   esa ultima ventana solo tenia 287 dias reales de historia. Los 180 dias
   duplican aproximadamente la cobertura de 90 sin usar toda la historia
   disponible como si fuera una tarifa vigente.
2. **Entrada con seis ordenes; salida al caer a tres.** Un producto ya
   admitido permanece con cuatro o cinco ordenes en la ventana. La medicion
   de seis ventanas mostro que esta histeresis redujo las salidas de 9 a 2
   productos MX y de 10 a 3 US frente a un corte seco.
3. **Sin historia suficiente: abstencion con `envio_sin_historia`.** No se
   reemplaza el costo del envio por cero ni por una constante. La publicacion
   permanece visible en el recuadro de cobertura con su motivo.

La ventana efectiva termina antes de hoy para permitir el rezago de ingesta.
En la medicion, su p90 fue 4.2 dias MX y 13 dias US; **propuesta** de corte:
5 dias MX y 13 dias US. Esta cifra tambien requiere la decision del dueno.

| Ventana medida | MX con >=6 ordenes | US con >=6 ordenes | Dias reales de historia |
| --- | ---: | ---: | ---: |
| 90 dias | 7 | 9 | 90 |
| 180 dias | 14 | 17 | 180 |
| 365 dias | 17 | 22 | 287 |

## Pendiente para cerrar el acta

- Registrar el literal del dueno para ventana, umbrales, abstencion y rezago.
- Actualizar S10 del spec: su afirmacion de que el rezago de **emision** es
  p90 de 57–59 dias no se reprodujo. E.1 midio p90 de **ingesta** de 4.2 dias
  MX y 13 US; son relojes distintos.
- Dejar `E.2/acta.md` y la fila E.2 en el plan como **parte 1 cerrada** solo
  despues del literal. La mediana del costo y el ingreso por envio pertenecen
  a la parte 2, despues de E.0a/E.0b/E.1.
