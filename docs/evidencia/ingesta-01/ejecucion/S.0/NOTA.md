# S.0 — Nota de ejecucion de la sonda de latencia

`salida.txt` es la corrida de **2026-10-21 23:36:49 -> 23:41:47 UTC** (exit 0)
con el `sonda.py` de este directorio (el de HEAD), en produccion (contenedor
`orbit-app-1`) con la forma del encargo: `docker exec -i orbit-app-1 python - <
sonda.py`. El intento previo de verificacion habia fallado con `docker:
command not found` porque se invoco docker en la Mac en vez de en el servidor
`goncloud` (ahi docker pide `sudo`); esta corrida se lanzo por
`ssh goncloud 'sudo docker exec -i orbit-app-1 python -' < sonda.py`.

## Condiciones

- Hora 23:36 UTC >= 19:00 UTC (ventana exigida), lejos de las descargas de
  contabilidad (12:00/18:00) y de la ingesta diaria de Orbit.
- `/tmp/orbit-ingesta-diaria.lock` libre (verificado antes de lanzar).
- Rango D-31..D-1 = 2026-09-20..2026-10-20. Perfiles: MX `3850003733258937`,
  US `4387591165211712`. (b)/(c) corrieron con los 12 de (a) aun en cola.

## Resultados (de salida.txt)

- **(a)** 12/12 POST HTTP 200 (0.2 s c/u), 12/12 COMPLETED, **0.5-1.2 min**:
  MX campanas 0.9, keywords 1.2, targets 0.5, search_terms 0.9, productos
  1.2, placements 0.9; US campanas 1.2, keywords 0.9, targets 0.9,
  search_terms 0.9, productos 0.9, placements 0.9.
- **(b)** con (a) aun en cola: campanas MX identico -> HTTP **425**
  `duplicate of 56cad96d-...` (el propio reporte de (a)); con `name` +
  `-r1` -> HTTP **425** `duplicate of 56cad96d-...` (mismo original).
  Es la tercera observacion del dedup: 425 con y sin sufijo cuando el
  original sigue en cola.
- **(c)** 24 POST en rafaga (rangos corridos, reportes distintos): **24x
  HTTP 200, 0x 429**.
- **(d)** MX uno tras otro tras (a): campanas **1.0**, keywords **0.7**,
  targets **1.0**, search_terms **1.0** min (4/4 COMPLETED).

## Antecedentes

- **2026-10-21 19:17:36 -> 19:23:43 UTC** (salida en 962238e): con el mismo
  `sonda.py`. (b) identico y `-r1` dieron HTTP **200** con reportId nuevo
  (Amazon no deduplico contra el original todavia en cola); (c) 24x200, 0x429;
  (a) 1.6-2.7 min; (d) 1.0/0.7/0.7/1.0 min.
- **2026-10-21 19:00:58 -> 19:09:20 UTC** (salida en 3df0c9e): con el script
  anterior (071df33), antes del arreglo del truncamiento. (b) 425 con y sin
  `-r1`; (c) 24x425, 0x429; (a) 10/12 medidos en 0.8-1.6 min; (d) 3/4.

## Para A.1 (lo confirma o ajusta el lead)

- **Latencia:** 0.5-2.7 min en las tres corridas, muy por debajo del tope de
  poll (en septiembre se vieron 12-13 min: la cola varia por ordenes de
  magnitud, conviene mantener margen).
- **Dedup del repetido:** no concluyente; 19:00 y 23:36 dieron 425 (con y sin
  sufijo) y 19:17 dio 200. No dar por hecho ni que el sufijo `-r1` salte el
  dedup ni que no lo salte; el reintento debe tolerar ambos (425 con id del
  original -> reusar ese id; 200 -> id nuevo).
- **Rate-limit:** 0x 429 en 24 POST seguidos (todas las corridas), con la
  salvedad de que solo 19:17 y 23:36 mandaron 24 reportes distintos.

## Sobrante

`reposicion.py` quedo SIN correr: se escribio para reponer las medidas que la
corrida de 19:00 perdio (targets sin id), pero la re-corrida de 19:17 con el
`sonda.py` arreglado las midio todas y lo volvio innecesario. Queda solo como
antecedente de la tecnica (ventana corrida un dia por POST para esquivar el
dedup) y de los reportId que los 425 de la corrida de 19:00 revelaron.
