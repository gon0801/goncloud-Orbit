
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

## Ronda de revision 2 (Devin sobre db6028f: techo heredado en la liberacion)

`_revalida_pause` inventaba piso 0.01 / techo 999999 para campanas SIN goal
propio: `nivel_de_regreso` acota al techo, asi que con 999999 la cola veia un
regreso pendiente hacia 12.00 (y conservaba los umbrales x2) para una puja que
YA HABIA ALCANZADO el techo real de 8.00 heredado de la plataforma.

Fix: el piso y el techo salen del GOAL EFECTIVO de la hoja con la precedencia
del ciclo — `g.resuelve_goal` (campana > plataforma) + `g.resuelve_floor_
ceiling` con los defaults de la moneda.

Demostracion empirica contra aace2673c97f600b3dd1a2b711fcf0a79597b3c0 (misma
siembra, dos lecturas): con el techo inventado 999999 la frontera daba
`baja | mult 2 | cerca 2027-01-05` (re-tenia los umbrales x2 para una puja ya
en el techo); con el techo real 8.00 daba `normal | fin ya_arriba`.

Prueba `test_temporada01_d_liberacion_techo_heredado` (dinero): campana sin
goal propio, entrada 12 -> 8 por el techo heredado, episodio cerrado: la
frontera de liberacion da `fin ya_arriba` (sin regreso pendiente) y la
aritmetica muestra 8.00 con el techo real contra 12.00 con el inventado.

## Ronda 3 (revision nativa sobre e32c3e2): ciclo con terminos, cerca en shadow

Prueba `test_temporada01_d_ciclo_terminos_shadow_igual_a_hoy` (regla 9,
 Revision nativa sobre e32c3e2): dos corridas de `_procesa_grupo` sobre el
mismo grupo sembrado — 8 fechas del termino (11-10..11-24, todas ANTES de la
cerca fijada 12-01) + ancla 12-10; ventana natural 11-08..12-07, entidad
completa (8 fechas >= 7), cost 800 MXN x fecha, clicks 60 x fecha, orders 0.

- corrida SHADOW (plat con cerca 12-01 y mult_cortes_baja 2, modo shadow):
  la guarda en_live deja cerca_plat=None y mult_neg=None -> negative
  `negative_umbral` emitido, ventana congelada COMPLETA (11-08..12-07, 8
  fechas) y veredictos IDENTICOS a los del flag ausente.
- corrida SIN flag (contadores sin temporada): misma negative, byte a byte.

ROJA demostrada contra e798f7f (el commit ANTERIOR a la guarda): sin la
guarda, cerca_plat=12-01 levantaba el INICIO de la ventana por encima de
todas las fechas sembradas y la corrida shadow perdia el negative:

    E   AssertionError: []
    E   assert [] == [('tortugas n...tive_umbral')]
    FAILED tests/test_temporada01_d.py::test_temporada01_d_ciclo_terminos_shadow_igual_a_hoy

(pre-guarda el multiplicador x2 tambien aplicaba en shadow: umbral 40 -> 80
clicks y piso 600 -> 1200 MXN; con esta siembra la cerca es el mecanismo que
vacia la evidencia). En head la prueba pasa: 1 passed contra el guard
`en_live` de `_procesa_grupo` (cerca_plat/mult_neg SOLO con temporada live).
