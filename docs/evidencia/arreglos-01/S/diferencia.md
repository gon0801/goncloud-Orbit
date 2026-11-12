# Bloque S - diferencia entre el diseno y el codigo real

## Lo que mandan el plan y el diseno

- `plans/arreglos-01.md`, fila S.3: `fin_de_intentos(hoy)` es el ultimo
  `HORARIOS_INGESTA` mas `TOPE_PARED_SEGUNDOS` (`app/ads/diario.py:56,60`).
- `docs/evidencia/arreglos-2026-11-09/operacion/opus/DISENO.md`, seccion
  `## H-H2 = I-H3 = J-H2`: "Momento UTC en que ya no puede llegar D-1 hoy:
  inicio del ultimo HORARIOS_INGESTA mas TOPE_PARED_SEGUNDOS (13:15 + 2 h 45
  = 16:00)".

El codigo implementa esa formula en `app/metricas_listas.py` y la prueba
`docs/evidencia/arreglos-01/S/prueba.py` fija las 16:00 UTC.

## El hueco que levanto DeepSeek F1

`TOPE_PARED_SEGUNDOS` (9900 s) no es un tope duro: en `app/ads/diario.py:408`
solo se usa para dejar un aviso despues de que la corrida termino. El tope
duro real del sondeo de reportes es `PLAZO_SONDEO_SEGUNDOS` (9000 s, 2 h 30)
en `app/ads/descarga.py:66`, comprobado en el bucle de sondeo (`:580`, `:697`).
Los 15 min de diferencia son la holgura de escrituras y avisos que fija el
diseno, no un segundo plazo de sondeo.

## Forma final elegida (correccion de F1)

La formula y el comportamiento no cambian: `fin_de_intentos` sigue siendo el
inicio del ultimo `HORARIOS_INGESTA` mas `TOPE_PARED_SEGUNDOS` (16:00 UTC), y
`salida_sin_metricas` sigue saliendo 75 antes de esa hora y 1 desde ella, como
mandan S.3 y la «Forma final» de `operacion/SINTESIS.md`. Lo que cambia es lo
que el texto promete.

F1 tenia razon en el texto. El ultimo intento de la ingesta empieza a las
13:15 y su sondeo se corta solo a las 15:45 por `PLAZO_SONDEO_SEGUNDOS`; los
15 minutos hasta las 16:00 son la holgura de escrituras y avisos. Decir que
"el ultimo intento programado termina a las 16:00" prometia un corte que la
ingesta no hace.

Por eso el mensaje ya no habla de un plazo de la ingesta. Dice lo que pasa: el
ultimo intento empieza a las 13:15 UTC, su sondeo se corta solo a las 15:45
UTC y desde las 16:00 UTC la guarda da el dia por perdido. El docstring,
`S/prueba.py` y este documento dicen lo mismo, asi que no prometen un corte
que no existe.

## El aviso del dia perdido nombra lo que falta (F1 de la revision)

`avisos-campana` es el unico consumidor cuya guarda exige ademas la corrida de
placements (`exigir_placements=True`). Su aviso de dia perdido no puede decir
que faltaron "las metricas" cuando lo que falto pudo ser la corrida de
placements. `salida_sin_metricas(..., incluye_placements=True)` nombra "las
metricas o los placements de ayer"; los otros tres consumidores conservan "las
metricas de ayer". El codigo 75/1 y las horas no cambian.

## Lo medido en produccion (solo lectura, 2026-11-12)

- 180 dias: 189 corridas de los tres sources del diario; la mas larga tardo
  8288 s (2 h 18) y termino a las 09:28 UTC (2026-10-14).
- Ultimos 21 dias: todas las corridas del diario terminan a mas tardar a las
  07:31 UTC; el primer intento de las 07:10 basta y no hay corridas a las
  10:40 ni a las 13:15.
- Consultas: `ingest_run` por `started_at`, `source IN
  ('amazon_ads_reports_v3','amazon_ads_products_v3','amazon_ads_placements_v3')`.

## Por que no se cambia la formula

- La formula es la decision del dueno en el plan y el diseno; cambiarla por
  otra (matar la ingesta a las 2 h 45 o mover el plazo) es una forma nueva que
  el plan no autoriza.
- El plazo de las 16:00 es conservador: el sondeo no puede pasar de las 15:45
  por su tope duro y la corrida mas larga medida termino a las 09:28. El
  mensaje nombra ese tope y la holgura en vez de prometer un corte de la
  ingesta a las 16:00.
- Si el dueno quiere un corte duro a las 2 h 45, es un cambio de la ingesta
  fuera del bloque S y necesita su plan.
