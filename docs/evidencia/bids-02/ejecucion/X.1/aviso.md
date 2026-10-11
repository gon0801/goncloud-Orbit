# Aviso X.1: lo que haria el motor (rejuego del ultimo mes, 2026-10-11)

Sale de `rejuego-amazon_mx.txt` y `rejuego-amazon_us.txt` (cumple: true en
ambas). `por_motivo` cuenta Mover sin distinguir direccion (recortes y
subidas mezclados); `hojas_recortadas` y `vendedoras_que_recortaria` son
las hojas con ventas que el motor recortaria.

## amazon_mx (2026-09-15..2026-10-11: 31 ciclos live, 15 aportaron, 2028 casos)

- por_motivo: bajo_target 58, grupo_sangra_vendedora 2
- hojas_recortadas: 1
- recortes_que_se_repetirian: 2 (de 149 del motor viejo)
- parte_del_gasto_recortada_pct: 10.81
- regresos: 13
- vendedoras_que_recortaria (1): hoja 3861 (grupo_sangra_vendedora)

## amazon_us (2026-09-13..2026-10-11: 30 ciclos live, 15 aportaron, 1629 casos)

- por_motivo: grupo_sangra_vendedora 10, bajo_target 2
- hojas_recortadas: 4
- recortes_que_se_repetirian: 3 (de 110 del motor viejo)
- parte_del_gasto_recortada_pct: 50.66
- regresos: 6
- vendedoras_que_recortaria (4): hojas 4934, 5336, 5565, 363926 (grupo_sangra_vendedora)

cumple: true en ambas plataformas.
