# PausaProducto

TLC reached 384 states and 1398 steps. The chart draws 14 states and 29 arrows.
The chart does not draw 718 steps that stay in one chart state.
The view hides 246 steps, so the chart does not draw them.

## PracticaNoTapaLaReal breaks
The shortest path has 3 steps:
0. Activo, sin propuesta
1. lo bajas a práctica o lo apagas: Activo, sin propuesta
2. el ciclo ve gasto sin ventas y propone pausar [interruptor en práctica]: Propuesta de práctica, nunca se ejecuta
3. enciendes el interruptor: Propuesta de práctica atorada, interruptor ya en vivo

## NoRepausaLoQueRegresaste breaks
The shortest path has 9 steps:
0. Activo, sin propuesta
1. enciendes el interruptor: Activo, sin propuesta
2. el ciclo ve gasto sin ventas y propone pausar [interruptor en vivo]: Propuesta real, esperando tus 48 h
3. pasan 48 h sin veto: Revisando datos frescos
4. la revisión confirma y cobra la cuota: Mandando la pausa a Amazon
5. Amazon confirma: Pausado por Orbit
6. lo regresas desde la pantalla: Lo regresaste, activo
7. el ciclo ve gasto sin ventas y propone pausar [interruptor en vivo]: Propuesta real otra vez, esperando 48 h
8. pasan 48 h sin veto: Revisando otra vez
9. la revisión confirma y cobra la cuota: Mandando la pausa otra vez

## Rules that hold
SoloEnVivo, NuncaConVenta, NuncaConOtraMezcla, UnSoloCobro, NoDeadlock
