# TEMPORADA 01 H.1 — La práctica con el código corregido (ciclos 206 y 207)

Lectura de solo lectura sobre producción (`docs/evidencia/arreglos-01/lee_prod.sh`, rol `orbit_read`,
transacción `READ ONLY`). Son los dos primeros ciclos que corren con el arreglo de la práctica
(`49f8734`, `ff2c1e3`, `2a8a6b0`, `57b47dd`) y con las cuatro claves en `shadow` (config **61**). Este
documento **cierra H.1**: trae el dato, no una promesa.

Los dos ciclos se forzaron con `--forzar` por autorización del dueño, porque el ciclo automático ya
había corrido ese día (204 y 205). Las marcas de agua, el antes y el después y el resultado de cero
escrituras están en `ciclo-forzado.txt`, al lado de este archivo.

## Los ciclos

| Ciclo | Plataforma | Terminó (UTC) | Decisiones | `notes.temporada` |
| --- | --- | --- | --- | --- |
| 206 | amazon_us | 2026-11-22 20:15:26 | 0 | `comparadas` 82, `coinciden` 82, `diferencias` 0, `reactivaciones` 0 |
| 207 | amazon_mx | 2026-11-22 20:15:54 | 0 | `comparadas` 112, `coinciden` 110, `diferencias` 2, `reactivaciones` 0 |

**US: 82 comparadas, 82 coinciden, 0 diferencias, 0 reactivaciones.** La sombra y el juicio real coinciden
en las 82 hojas de Amazon US.

**MX: 112 comparadas, 110 coinciden, 2 diferencias, 0 reactivaciones.** Las dos diferencias están
abajo, con su caso reproducible.

Los dos ciclos corrieron con la config **61** vigente: se creó el 2026-11-22 17:17:15 UTC y no hubo
ninguna config posterior hasta después de los ciclos, así que era la vigente a las 20:15.

`recupera_nivel` es **0**: ninguna de las dos diferencias tiene `motivo_sombra = "recupera_nivel"`. Y
ahora el 0 está respaldado con un número, no con un hueco de cobertura: ver «R6r: el 0 tiene número».

## La fase en la que cayó el ciclo

El calendario guardado tiene **noviembre en alta** (`temporada.estacion.fase = "alta"`, día
`2026-11-22`, `inicio_bloque` `2026-10-01`, `previa_alta` `false`). El ciclo cayó en **temporada alta**,
con 9 días por delante antes del `2026-12-01` (`sale_alta`).

Eso fija qué podía aparecer y qué no:

- **Entradas a la baja: 0, por la fase.** En un mes alto no hay entrada que decidir; esa línea solo se
  puede juzgar en enero, febrero, mayo o junio.
- **Regresos: 0 por diseño.** La práctica no abre episodios ni confirma nada.
- **Retenidos por presupuesto: 0 por diseño.** `_anota_regreso_retenido` solo cuenta con la temporada en
  `live`; en práctica ni se evalúa.
- **Reactivaciones: 0.** Sin episodio no hay pausa de la baja que reactivar.
- **Metas de alta: sí actuaron.** Es lo único que esta práctica sí pudo ejercer.

Un 0 en esta tabla no es «todo bien». Los tres ceros de en medio son estructurales (no había nada que
contar) y no dicen nada sobre si esas rutas funcionan. Lo único que la práctica ejerció de verdad son
las metas de alta, y de ahí salen las dos diferencias.

## Las dos diferencias de MX, con el caso rejugado

```
hoja|real|sombra|destino_sombra|replay_sombra|m_estrella|m_estrella_sin_tope|gamma_q_base|gamma_q_juicio|confianza|techo_efectivo|gasto_ventana|gasto_extra
2457|Mantener(azar_lo_explica)|Mover(bajo_target)|23.48|Mover(bajo_target)|R6r no llego a m*|1.047300919852685259527551050|0.6634869036566673150705835614|0.7537874196951052446201276572|0.70|23.195500|2306.8300|345.69
2969|Mantener(azar_lo_explica)|Mover(bajo_target)|7.70|Mover(bajo_target)|R6r no llego a m*|1.001592509809653414288664064|0.6605252144095406147383533060|0.7008219810804959065148415665|0.70|6.7000|153.8000|22.96
total| | | | | | | | | | | |368.64
```

Sale de `docs/evidencia/temporada-01/H/replay_sombra.py`, que lee el `notes.temporada` del ciclo y
rejuega el `caso_sombra` con `CasoHoja.desde_json` y `decide` del repo. El comando, reproducible:

```
$ echo "select (notes::jsonb->'temporada')::text from optimizer_cycle where id = 207" \
    | bash docs/evidencia/arreglos-01/lee_prod.sh > temporada-207.json
$ PYTHONPATH=. python docs/evidencia/temporada-01/H/replay_sombra.py temporada-207.json
```

El replay de la sombra devuelve `Mover(bajo_target)` con el mismo destino que guardó el ciclo (23.48 y
7.70), así que el caso está completo: se puede volver a decidir sin la base.

Las dos son la meta de alta en acción, y la cuenta exacta está en las columnas `gamma_q_base` y
`gamma_q_juicio` de la salida de arriba. R6 no dispara por un ACoS debajo de un tope: dispara cuando
`gamma_q(a_subida, arg_subida)` alcanza la confianza de subida (0.70). El argumento es
`clics_sub × cpc / (0.85 × meta/100 × ticket_sub)`, así que subir la meta del juicio de 23.1920 % a 25 %
baja el argumento y sube la probabilidad:

| Hoja | `gamma_q` con meta 23.1920 % | `gamma_q` con meta 25 % | Confianza |
| --- | --- | --- | --- |
| 2457 | 0.663487 | 0.753787 | 0.70 |
| 2969 | 0.660525 | 0.700822 | 0.70 |

Con la meta base las dos quedan **por debajo de 0.70** y el veredicto real es `Mantener`
(`azar_lo_explica`). Con la meta de alta las dos la alcanzan y la sombra da `Mover` (`bajo_target`).
Conviene no contarlo como «el ACoS baja del tope»: el ACoS de hoy de esas hojas es 15.98 % y 14.73 %, por
debajo de los dos límites (19.72 % y 21.25 %) y por eso ninguno de los dos explica nada. Lo que cambia es
la probabilidad, no el ACoS.

El destino de las dos es exactamente el paso de +15 % de R6, truncado a centavos: 20.42 × 1.15 = 23.483 →
23.48, y 6.70 × 1.15 = 7.705 → 7.70.

## R6r: el 0 tiene número

En los ciclos 204 y 205 `recupera_nivel` también daba 0, pero por una razón que no valía: la práctica no
construía los insumos de la subida rápida en `shadow`, así que no había `caso_sombra`, `m*` ni gasto que
medir. Eso fue el hallazgo de la revisión y se corrigió en `2a8a6b0`. En 206 y 207 el `caso_sombra` ya
viene con `recuperacion` e `historia`.

Ahora R6r se puede juzgar, y el 0 es un 0 con causa:

- **`recupera_nivel`: 0 de 2 diferencias.** Las dos son `bajo_target` (R6, el paso de +15 %), no R6r.
- **`m*` máximo observable: 1.0473** (hoja 2457), y 1.0016 en la 2969.
- **R6r no llegó a calcular `m*` por su cuenta** en ninguno de los dos casos: la última compuerta,
  `techo_conocido`, corta antes. En 2457 el techo es 23.1955 y el salto mínimo de R6r exige superar
  20.42 × 1.15 = 23.4830; en 2969 el techo es 6.70, igual al bid vigente. Sin hueco bajo el techo, R6r no
  actúa. Por eso la columna `m_estrella` dice «R6r no llegó a m*».
- **La columna `m_estrella_sin_tope`** pide el mismo `m*` con la misma cadena de R6r pero sin la compuerta
  del techo, y es la que dice cuánto sostenía la evidencia: **1.0473** y **1.0016**. Las dos quedan por
  debajo de 1 + 0.15, que es el mínimo para que R6r se mueva (`app/optimizer/recuperacion.py:297`). Es
  decir: aunque el techo hubiera dejado hueco, R6r tampoco habría actuado. Las dos compuertas coinciden y
  ninguna de las dos escondía un salto que valiera la pena.

Las dos `m*` se calcularon con la meta del juicio (25 %), que es la que usa la sombra, no con la base
23.192 %. Con la base el número saldría por debajo de 1 y contradiría que R6 haya dado +15 %; ese fue el
primer error del script y está corregido: `_caso_del_juicio` aplica la misma sustitución que `decide`.

**Gasto extra proyectado.** Por R6r: **0.00 MXN**, porque no hubo ni un caso. El gasto de las dos
diferencias es el del paso de +15 % de R6 que la sombra propuso y el juicio real no aplicó, con el mismo
CPC (supuesto del diseño: los clics no cambian):

| Hoja | Bid hoy | Destino sombra | Factor | Gasto de la ventana madura | Gasto extra |
| --- | --- | --- | --- | --- | --- |
| 2457 | 20.42 MXN | 23.48 | 1.149853 | 2,306.83 MXN | +345.69 MXN |
| 2969 | 6.70 MXN | 7.70 | 1.149254 | 153.80 MXN | +22.96 MXN |
| Total | | | | 2,460.63 MXN | **+368.64 MXN** |

La suma exacta es 368.640816 MXN; los dos renglones redondeados por separado dan 368.65 y la diferencia
es el redondeo a centavos. Son 368.64 MXN más de gasto en las dos ventanas maduras si se aplicara la
meta de alta. Eso es lo que la temporada en `live` va a gastar de más en estas dos hojas cuando la meta
de alta mande, y es el número que el dueño necesita para decidir el encendido.

## Lo que la práctica no toca (y por eso no se puede dar por bueno)

En los ciclos 206 y 207: **0 decisiones**, **0 episodios abiertos**, **0 filas** en `temporada_episodio`,
`temporada_efecto`, `temporada_fin_reduccion` y `temporada_exclusion`, **0** filas nuevas en
`decision_application` y `decision_sin_aplicar`, **0** en `campana_ajuste` y **0** intentos nuevos en
`apply_attempt`. Las marcas completas están en `ciclo-forzado.txt`. La práctica cumplió su contrato:
anotó y no escribió.

Quedan sin ejercitar, por diseño, las rutas que solo existen en vivo (diseño, «Riesgos y límites
honestos», punto 16): la lectura fresca antes del PUT, la exclusión por la mano del dueño, la
reactivación de pausas de la baja, la retención del regreso por presupuesto y el candado de las
propuestas. Lo vigila H.3 desde el primer ciclo en vivo, con el chequeo programado en `transiciones.txt`.

## Casos de paro de H.3 en la práctica

Las cinco condiciones de `docs/evidencia/temporada-01/H/primer-ciclo.sql`, aplicadas a los ciclos 206 y
207:

| Caso de paro | Resultado en práctica |
| --- | --- |
| bid enviado que baja más que la reducción | 0 (0 entradas enviadas ni confirmadas) |
| regreso que baja un bid | 0 (0 regresos enviados ni confirmados) |
| PUT después de una lectura fresca distinta | 0 (`decision_sin_aplicar` con `bid_cambiado_fuera`: 0) |
| dos entradas para una keyword en un episodio | 0 (0 episodios, 0 entradas) |
| presupuesto escrito sin `APLICAR AJUSTE` | 0 (`campana_ajuste`: 0 filas) |

Ninguno aparece. Y conviene decirlo otra vez: **que los cinco den 0 en práctica no significa que los
cinco estén bien**; tres de ellos solo pueden fallar cuando hay escrituras, y la práctica no escribe.
