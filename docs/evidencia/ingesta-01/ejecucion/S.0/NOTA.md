# S.0 — Nota de ejecucion de la sonda de latencia

`salida.txt` la produjo el script tal como esta en el commit **071df33**
(sha256 `512c79ad650a4e0491319ff04549eacb30410049214adebe07c6318a19452128`),
ejecutado en produccion (goncloud, contenedor `orbit-app-1`, Python 3.12.15)
con la forma del encargo:

```
docker exec -i orbit-app-1 python - < sonda.py
```

## Condiciones de la corrida

- Lanzamiento: 2026-10-21 19:00:58 UTC (primer POST 19:00:59), fin 19:09:20
  UTC, exit 0. Ventana exigida (>= 19:00 UTC) cumplida; la ingesta diaria de
  Orbit ya habia corrido (07:10/10:40/13:15) y la contabilidad (12:00/18:00)
  no estaba activa.
- Lock: `/tmp/orbit-ingesta-diaria.lock` libre. El archivo existia en el host
  (creado 18:22 por una corrida manual del lead) pero sin ningun proceso
  teniendolo abierto (fuser vacio; verificado tambien con flock sobre fd de
  solo lectura). Dentro del contenedor no existe.
- Rango pedido: 2026-09-20..2026-10-20 (D-31..D-1, mismo rango que el cron de
  metricas). Perfiles aceptados: MX `3850003733258937`, US `4387591165211712`.
- La cola de reportes de la cuenta estaba vacia al empezar (todo lo anterior
  habia completado mucho antes).

## Lectura de salida.txt

- **(a)** 12 POST, todos HTTP 200, en ~2 s en total. COMPLETED a los
  **0.8-1.6 min** (10 de 12 sondeados; ver el detalle de targets abajo).
- **(a) targets, id no extraido**: en los 2 POST de `targets` (uno por perfil)
  el script corto el cuerpo a 600 chars y el `reportId` de spTargeting cae
  despues del corte, asi que quedaron fuera del sondeo. Los reportes SI se
  crearon: sus ids reales quedaron expuestos por los 425 de (c):
  MX `b3bdeec5-ef1d-4461-8adc-344ccd230512`,
  US `f0bedaa2-fc84-4b94-bee7-983f4a49bc30`. (a) mide latencia de 10/12.
- **(b)** campanas MX identico y con `name` + `-r1`: ambos **HTTP 425**
  `The Request is a duplicate of : f9190bda-...`. Amazon dedupla por
  CONFIGURACION (startDate/endDate/configuration), **el nombre no importa**,
  mientras el reporte original no ha completado. El sufijo del repetido NO
  sirve para bypassear el dedup.
- **(c)** 24 POST en rafaga (sufijos `-rafaga1`/`-rafaga2`): **24x 425
  duplicate, 0 x 429**. A esta escala no aparece rate-limit; el mecanismo
  efectivo es el dedup por configuracion.
- **(d)** tras COMPLETED el mismo config SI crea reporte nuevo (200):
  MX secuencial campanas **2.0 min**, keywords **2.0 min**, search_terms
  **2.7 min**. `targets=NO_CREADO` significa "id no extraido por el corte de
  600 chars" (el POST fue 200), no un rechazo.

## Implicaciones para A.1 (lo que el lead confirma o ajusta)

- Latencia medida muy por debajo del tope actual de poll (reportes del
  21-23/09 tardaban 12-13 min; hoy 0.8-2.7 min: la cola varia por ordenes de
  magnitud, conviene mantener margen).
- Re-pedir el mismo config mientras no completo da 425 con el id del
  original: un reintento puede REUSAR ese id en vez de tratarlo como error.
- No se observo 429 en 24 POST seguidos.

## Script en HEAD

El `sonda.py` de HEAD (e45cb75) es la version corregida para futuras corridas
((c) incondicional, resumenes que distinguen COMPLETED de FAILURE, guardas
que dejan linea de evidencia); la `salida.txt` de este directorio la produjo
la version 071df33 descrita arriba.
