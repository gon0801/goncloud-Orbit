
## Ronda de revision 1 (Devin sobre 32e7308: 6 hallazgos, todos corregidos)

1. **La practica altera cortes reales** (cycle.py): el mult del umbral de
   pause por hoja y el de negatives se aplicaban aunque ads_temporada estuviera
   en shadow. Fix: el mult REAL solo con modo live (la sombra arma los suyos).
2. **Pausas con gasto insuficiente** (apply_cola.py): al liberar, decide_pause
   recibia el umbral x mult pero el piso NORMAL — pausas que ya no calificaban
   segui­an aplicando. Fix: cost_min = piso_pausa x mult en la revalidacion.
3. **Propuestas de pausa tras el regreso** (cycle.py): la cerca solo se
   aplicaba en baja; en fase normal/alta la ventana de pausa seguia mirando
   dias anteriores al regreso. Fix: la cerca vive en cualquier fase mientras
   exista; el mult solo en baja.
4. **Cortes de terminos con evidencia anterior** (apply_cola.py +
   apply_harvest_reconciliacion.py): _revalida_negative y la cosecha leian
   terminos_cortes SIN cerca. Fix: cerca_plataforma (nuevo lector de
   temporada_io) en ambas liberaciones.
5. **Pujas con evidencia anterior al regreso** (lecturas_caso.py): una hoja
   con cerca cuya ventana individual quedaba vacia conservaba el agregado SIN
   cerca (evidencia anterior). Fix: la hoja con cerca usa SOLO su corrida; si
   su ventana es vacia queda cero medido (propia None).
6. **Pausas con limites de otra meta** (apply_cola.py, amarillo): mi parche
   leia el goal con el ID de la keyword en el campo de campana y las
   posiciones 9/10 (harvest, no floor/ceiling). Fix: campana = parent del
   grupo de la hoja; goal campaign enabled live; floor/ceiling en 4/5.

Verificacion: test_pause_economica (34), temporada01_d (8), cycle,
apply_cola, lecturas, apply_harvest/harvest_excepcion, optimizer_politica —
297 passed.
