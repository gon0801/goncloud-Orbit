# S.0 — Nota de ejecucion de la sonda de latencia

`salida.txt` es la corrida de **2026-10-21 19:17:36 -> 19:23:43 UTC** (exit 0)
con el `sonda.py` de este directorio (sha256
`0da88cb180fa3b6967391c48eb1d7da16e4cd894c3baa8ea257314671df0f402`, el de
a211438 y de HEAD), en produccion (contenedor `orbit-app-1`) con la forma
del encargo: `docker exec -i orbit-app-1 python - < sonda.py`.

## Condiciones

- Hora >= 19:00 UTC (ventana exigida), lejos de las descargas de contabilidad
  (12:00/18:00) y de la ingesta diaria de Orbit.
- `/tmp/orbit-ingesta-diaria.lock` libre (sin proceso que lo tuviera abierto).
- Cola: la corrida de 19:00 (ver Antecedente) habia terminado antes y sus
  reportes estaban completados; los 12 de (a) de esta corrida arrancaron sobre
  cola propia vacia, y (b)/(c) corrieron con esos 12 aun en cola.
- Rango D-31..D-1 = 2026-09-20..2026-10-20. Perfiles: MX `3850003733258937`,
  US `4387591165211712`.

## Resultados (de salida.txt)

- **(a)** 12/12 POST HTTP 200 (0.2 s c/u), 12/12 COMPLETED, **1.6-2.7 min**:
  MX campanas 2.4, keywords 2.4, targets 2.7, search_terms 2.0, productos 1.6,
  placements 2.0; US campanas 2.4, keywords 2.4, targets 2.0, search_terms
  2.3, productos 2.3, placements 2.7.
- **(b)** con (a) aun en cola: campanas MX identico -> HTTP **200**; con
  `name` + `-r1` -> HTTP **200**. Ambos con reportId nuevo (distinto del
  original `16c8a84c...`; ver el cuerpo en salida.txt). En esta corrida
  Amazon **no** deduplico contra el original todavia en cola.
- **(c)** 24 POST en rafaga: **24x HTTP 200, 0x 429**. Ojo: en HEAD cada POST
  de la rafaga usa un rango distinto (corrido n dias), o sea son reportes
  distintos y no ejercen el dedup; solo miden el rate-limit (ninguno a 24).
- **(d)** MX uno tras otro tras (a): campanas **1.0**, keywords **0.7**,
  targets **0.7**, search_terms **1.0** min (4/4 COMPLETED).

## Antecedente: corrida de 19:00:58-19:09:20 UTC (salida en 3df0c9e)

Corrida con el script anterior (071df33), antes de arreglar el truncamiento a
600 chars (el reportId de targets quedo sin extraer) y con la rafaga sobre
el mismo rango. Resultados distintos: (b) identico y `-r1` dieron HTTP 425
`duplicate of f9190bda-...`; (c) 24x425, 0x429; (a) 10/12 medidos en
0.8-1.6 min; (d) 3/4 (targets sin id). **Los dos resultados de dedup no
coinciden**: a las 19:00 el repetido (con y sin `-r1`) fue 425 contra el
original en cola; a las 19:17 fue 200 con id nuevo. No hay evidencia para
explicar la diferencia (la 19:00 repitio el mismo rango/config; la de 19:17
tambien en (b)); solo se dispone de las dos salidas.

## Para A.1 (lo confirma o ajusta el lead)

- Latencia: 0.7-2.7 min en ambas corridas, muy por debajo del tope de poll
  (en septiembre se vieron 12-13 min: la cola varia por ordenes de magnitud,
  conviene mantener margen).
- Dedup del repetido: no concluyente; dos corridas dieron 425 y 200. No dar
  por hecho ni que el sufijo `-r1` salte el dedup ni que no lo salte; el
  reintento debe tolerar ambos (425 con id del original -> reusar ese id;
  200 -> id nuevo).
- Rate-limit: 0x 429 en 24 POST seguidos (ambas corridas), con la salvedad de
  que solo la de 19:17 mando 24 reportes distintos.

## Sobrante

`reposicion.py` quedo SIN correr: se escribio para reponer las medidas que
la corrida de 19:00 perdio (targets sin id), pero la re-corrida de 19:17 con
el `sonda.py` arreglado las midio todas y lo volvio innecesario. Queda solo
como antecedente de la tecnica (ventana corrida un dia por POST para esquivar
el dedup) y de los reportId que los 425 de la corrida de 19:00 revelaron.
