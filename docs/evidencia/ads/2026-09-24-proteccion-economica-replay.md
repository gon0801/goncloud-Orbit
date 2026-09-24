# ADS PROTECCION 01 C.2 — replay economico sin lookahead

Medicion read-only del 24-sep-2026 sobre `orbit_read`. Periodo: ciclos Ads
11–24 sep, MX y US. Codigo reproducible: `tools/replay_ads_economico.py`; se
puede ejecutar dentro de `orbit-app-1` con `ORBIT_DSN_READ`. El calculo usa
`Decimal`, la ultima observacion de cada `(entidad, metric_date)` con
`observed_at <= optimizer_cycle.started_at`, y la ventana de cortes de 30
fechas calendario que termina en `min(max_metric_date - 3d, fecha UTC del
ciclo - 10d)`. Exige al menos siete fechas observadas, cost y revenue medidos,
moneda unica y target demostrable. Campaign consume solo filas `spCampaigns`;
keyword y product_target consumen sus propias filas. No se suman granos.

El target se toma de `decision.inputs` del mismo ciclo y campana cuando
existe. Para A1U/AU2, cuyo goal de campana tiene target `None` y no cambia
desde 2-sep, tambien se puede usar el target `margen_plataforma` congelado
en el mismo ciclo. Si falta un freeze o hay fuentes incompatibles, el resultado
es indeterminado. El replay mide **senales economicas**, no elegibilidad de
PAUSE: el estado historico de Amazon es cache mutable y no puede reconstruirse
para cada ciclo. Goal deshabilitado, entidad inerte, PAUSED, vetos y quota
requieren revalidacion antes de live.

## Resultado por grano

De 3.929 ventanas entidad-ciclo con >=7 fechas, hubo 79 senales positivas
(11 entidades distintas), 955 negativas con target y 2.895 indeterminadas
principalmente por falta de target historico. Las 79 son ciclos repetidos, no
79 acciones. La cobertura del replay es parcial, sobre todo en MX.

| Plataforma / grano | Senales positivas | Entidades distintas | Negativas | Target indeterminado |
| --- | ---: | ---: | ---: | ---: |
| US campaign | 29 | 4 | 28 | 136 |
| US keyword | 47 | 5 | 246 | 406 |
| US product_target | 1 | 1 | 209 | 610 |
| MX campaign | 0 | 0 | 58 | 354 |
| MX keyword | 0 | 0 | 258 | 795 |
| MX product_target | 2 | 1 | 156 | 594 |

A1U (3909) cruza en sus 12 ciclos evaluables desde el 12-sep; AU2 (3926)
en sus 13 desde el 11-sep. El 17-sep queda indeterminado para ambas porque
el ciclo US 66 no congeló ningun target. El 24-sep ambas ya estaban PAUSED en
Amazon, por lo que su senal economica de ese ciclo **no** es propuesta
aplicable. Los valores siguientes son de ventanas maduras conocidas en cada
instante, no los importes finales vistos hoy:

| Ciclo UTC | Campana | Cost USD | Revenue USD | Target | Exceso USD | Senal |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 11-sep / 51 | AU2 | 407.12 | 226.98 | 17.8558% | 366.59 | Si |
| 12-sep / 53 | A1U | 138.33 | 0 medido | 18.2214% | 138.33 | Si |
| 14-sep / 57 | A1U | 196.04 | 138.00 | 18.0912% | 171.07 | Si |
| 14-sep / 57 | AU2 | 405.00 | 115.20 | 18.0912% | 384.16 | Si |
| 23-sep / 78 | A1U | 462.90 | 253.20 | 17.5630% | 418.43 | Si |
| 23-sep / 78 | AU2 | 450.23 | 371.20 | 17.5630% | 385.04 | Si |

La keyword A1U 5347 muestra el caso con ventas que faltaba en el motor:
12-sep cost USD 103.35/revenue 0 medido, y 23-sep cost USD 246.03/revenue
USD 115.20, target 17.5630%, exceso USD 225.80. En ambos cruza, aunque el
segundo caso tiene una venta. AU2 keyword 4925 cruza con revenue cero desde
11-sep (USD 105.20), consistente con la PAUSE antigua que calificó el
14-sep pero fue bloqueada por cooldown. En US hay 22 señales de keyword y
dos de campaign con revenue cero medido; en MX dos de product_target.

## Veto de 48 horas y atribucion tardia

Una senal no predice apply: debe esperar el veto y recalcular con el target,
ventana y metricas vigentes. Hay un caso concreto en que **se cancela**:

| Grano | 11-sep / ciclo 51 | 13-sep / ciclo 55 | Resultado al liberar veto |
| --- | --- | --- | --- |
| Campaign 3920, AU2 Phrase | USD 638.32 / 1,046.07, target 17.8558%; cruza | USD 621.57 / 1,164.07, target 18.1098%; no cruza | Cancelar propuesta |
| Keyword 4919 de 3920 | USD 315.20 / 515.27; cruza | USD 313.86 / 633.27; no cruza | Cancelar PAUSE |

Una observacion posterior elevó revenue del hecho 18-ago de USD 111.78 a
229.78 (USD +118) tanto en campaign como en keyword. El cambio de ventana
tambien influye. Ambas señales del 11-sep dejan de cruzar antes de que venza
el veto; usar la foto inicial causaria un corte injustificado. Otro ejemplo:
la campaign 3919 cruza 18–19 sep con revenue USD 175.19, pero el 20-sep
la ventana incorpora una fecha madura con venta y llega a USD 360.59; deja
de cruzar con target del ciclo 72 congelado.

En A1U, la señal inicial del 12-sep sigue cruzando el 14-sep; en AU2, la del
11-sep sigue cruzando el 13-sep. Eso demuestra continuidad **economica**, no
que una escritura habria sido segura o que se habria aplicado: no hay
historial completo de estado, veto, quota y readback contrafactual. No se
estima ahorro. La campana rentable 3934 es contraejemplo negativo: el
19-sep USD 0.95 de cost y USD 118.00 de revenue, target 17.8352%; no cruza.

## Senal MX con ventana vieja

Product_target 3859 (MX, campaign 165) cruza con MXN 1,485.16 y revenue
cero en 14 y 22-sep, pero su ventana acaba 18-jun. El watermark MX conocido
en esos ciclos era 13 y 19-sep; la entidad no tiene observaciones posteriores
al 21-jun. `v_entidad_inerte` la excluye si estaba ENABLED entonces (cero
filas en 14 dias desde watermark); si estaba PAUSED, la excluye la guarda de
estado. El cache actual la muestra PAUSED, pero no prueba su estado pasado.
Conservar ambas guardas actuales en C.3 basta para este caso; no se propone
una nueva ventana maxima basandose en esta senal aislada.

## Puerta para live

El replay **no autoriza live** por si mismo. La politica muestra beneficio
potencial para A1U/AU2 y un falso positivo transitorio que el veto de 48 h
puede eliminar. Antes de activar C.3/C.4 deben pasar las pruebas de guardas,
revalidacion de venta tardia y cambio de target, shadow con datos nuevos,
reversa y review. El dueño debe aceptar que el limite es ACoS Ads, no utilidad
neta, y que una entidad con ventas puede ser pausada automaticamente si el
riesgo persiste. No hay evidencia aqui para bajar el limite ni cambiar los
goals.

Reproduccion focalizada: `uv run --frozen python -m pytest -q
tests/test_replay_ads_economico.py`. Lectura en contenedor:
`ssh goncloud 'docker exec -i orbit-app-1 python -' <
tools/replay_ads_economico.py > replay.json` (archivo local; no imprime DSN).
