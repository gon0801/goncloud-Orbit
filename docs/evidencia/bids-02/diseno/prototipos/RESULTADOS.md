# Prototipos del runner A (2026-10-09)

Datos: `datos/` es un enlace a `prototipos/datos` de la base (extracto de producción de solo lectura). Todo
corre con `python3 <script>` desde esta carpeta, con biblioteca estándar. Las salidas completas están en los
archivos `a*_salida.txt`.

`politica.py` es la política `niveles_v3` en miniatura (en float; el motor real va en Decimal). Tiene la misma
forma que `bosquejo.py`: `arma_caso(hoja, día, bid)` reconstruye lo que se veía ese día y `decide(caso)` es
una función pura. Todos los prototipos pasan por esas dos funciones.

Límites que valen para todo lo de abajo:

- Antes del 2026-08-01 solo hay datos semanales. El total de cada semana se puso en su lunes. Solo afecta al
  tramo antiguo de la ventana de 90 días.
- "Hoja activa" y "ad group" usan el estado de hoy, no el del día rejugado.
- El target de US es 28.34 % (el que da el margen) salvo donde se diga otra cosa. El de MX es 20.72 %.
- El equilibrio es el margen medido: 41.43 % en MX y 31.49 % en US.

## A1. Rejuego a un paso de los recortes decididos en vivo (`a1_rejuego.py`)

Cada recorte que el motor viejo decidió entre el 2026-09-02 y el 2026-10-09 se vuelve a decidir con la
política nueva, viendo la historia real de bids hasta ese día.

| | MX | US (target 28.34) | US (target 20.79) |
|---|---|---|---|
| Recortes decididos por el motor viejo | 405 | 346 | 346 |
| La política nueva también recortaría | 15 (4 %) | 26 (8 %) | 57 (16 %) |
| Hojas distintas | 7 | 15 | 28 |

A dónde van los otros recortes (MX y US con 28.34): el ad group cumple (136 y 46), no hay evidencia (104 y
106), el azar lo explica (63 y 43), no hay gasto maduro (35 y 57), no hay clics nuevos desde el recorte
anterior (11 y 34), regreso por desplome (6 y 5). Y 15 recortes de MX y 4 de US habrían sido **subidas**: la
ventana de 90 días decía que la hoja iba bien y la de 30 días la castigó.

Contar decisiones engaña: el motor viejo repitió hasta cinco recortes sobre la misma hoja. La medida que
importa es A2.

## A2. El mes entero con la política nueva y su propia historia (`a2_simulacion.py`)

Cada día de ciclo decide todas las hojas activas que no están inertes, y cada cambio se da por aplicado. Las
métricas son las reales, así que después del primer cambio distinto el mundo simulado ya no es el real. Sirve
para ver el tamaño y la dirección, no el efecto.

| Del 2026-09-02 al 2026-10-09 | MX nueva | MX real | US nueva | US real |
|---|---|---|---|---|
| Recortes | 17 en 15 hojas | 220 en 76 hojas | 22 en 18 hojas | 138 en 44 hojas |
| Parte del gasto de 30 días en hojas recortadas | 21 % | 39 % | 72 % | 86 % |
| Subidas | 33 en 22 hojas (68 % del gasto) | 30 aplicadas | 9 en 5 hojas (24 % del gasto) | 9 aplicadas |
| Regresos por desplome | 1 | no existía | 1 | no existía |
| Recortes de 25 % | 0 | 15 | 0 | 48 |

Lectura: en US la corrección no se congela. La política nueva pone un recorte de 12 % sobre las hojas que
cargan 72 % del gasto, en vez de cinco recortes de 25 % sobre las que cargan 86 %.

Lo que sostiene ese 72 % es la regla R4 (la vendedora cuyo dato no concluye hereda el veredicto de su ad
group). Sin R4 (`python3 a2_simulacion.py 28.34 sin_herencia`) la parte del gasto recortada en US baja a 28 %.

## A3. Las hojas 2963 y 4924, paso a paso (`a3_hojas.py`, salida en `a3_salida.txt`)

**Hoja 2963 (MX, exact, vendedora).** Con la política nueva desde el principio: ningún cambio en todo el mes.

| Fecha | Motor viejo | Política nueva, viendo la historia real |
|---|---|---|
| 09-05 | −12 %, de 9.74 a 8.57 (30 días: ACoS 26.3 %) | No mover. 90 días pesados: 20 pedidos, ACoS 14.7 %; al bid de hoy, 19.4 %; su ad group va en 11.5 %. |
| 09-13 | −12 %, a 7.54 | No mover. ACoS al bid de hoy 17.5 %. |
| 09-21 | −25 %, a 5.66 | No mover. ACoS al bid de hoy 18.4 %. |
| 09-26 | (nada) | **Regresa a 7.54.** Día 5 tras el recorte: 12,844 impresiones en los 7 días previos, 1,150 en 4 días después (razón 0.16). |
| 09-30 | −25 %, a 4.25 | Regresar (la guarda sigue viendo el desplome). |
| 10-08 | −12 %, a 3.73 | Regresar. |

**Hoja 4924 (US, phrase, vendedora en un ad group que sangra).** Con la política nueva desde el principio: un
recorte de 12 % el 09-02 (de 0.40 a 0.352) y nada más en el mes.

| Fecha | Motor viejo | Política nueva, viendo la historia real |
|---|---|---|
| 09-02 | −25 %, de 0.40 a 0.30 | Recorte de 12 % (R4). 90 días pesados: 5.5 pedidos, ACoS 30.9 %; al bid de hoy 33.1 %; su ad group va en 41.3 %. |
| 09-10 | −25 %, a 0.225 | No mover: el recorte anterior costó tráfico (razón 0.42). |
| 09-15 | (nada) | **Regresa a 0.30.** Día 5 tras el segundo recorte: 8,336 impresiones antes, 500 en 4 días después (razón 0.10). |
| 09-18 | −25 %, a 0.165 | Regresar. |
| 09-26 a 10-08 | Cuatro decisiones de −25 % y una de −12 % | No mover: espera un precio medido. A los 14 días puede volver a subir. |

Aviso: el día del encendido hay hojas que ya están dañadas y sin volumen. A esas la guarda no las alcanza,
porque ya no tienen tráfico previo que comparar. Para ellas es la pantalla de keywords dañadas.

## A4. Cuatro números que sostienen decisiones (`a4_dudas.py`)

**(c) Qué sustituye a los 20 clics al bid vigente.** En 56 recortes con 15 clics o más antes y después:

| Sustituto del CPC tras el recorte | Sesgo | Error absoluto mediano | p90 |
|---|---|---|---|
| El CPC de antes, sin tocar (ventana entera) | +26 % | 27 % | 38 % |
| CPC de antes × bid nuevo ÷ bid anterior (elegido) | −3 % | 6 % | 24 % |
| Dos regímenes según CPC entre bid (descartado) | +1 % | 5 % | 33 % |
| CPC del resto del ad group | −1 % | 26 % | 58 % |

**(e) Guarda de desplome**, en hojas que vendían y tenían volumen:

| | Agosto sin motor | Tras un recorte | Tras −12 % | Tras −25 % |
|---|---|---|---|---|
| Razón menor a 0.30 con 4 días | 7 de 127 (6 %) | 10 de 51 (20 %) | 1 de 19 (5 %) | 9 de 32 (28 %) |
| Razón menor a 0.30 con 7 días | 6 de 127 (5 %) | 9 de 49 (18 %) | 1 de 19 | 8 de 30 |
| Razón menor a 0.70 con 4 días | 33 de 127 (26 %) | 31 de 51 (61 %) | 8 de 19 | 23 de 32 |
| Razón mediana con 4 días | | | 0.80 | 0.50 |

Dos lecturas. Con 4 días la guarda ya separa igual que con 7, así que puede regresar al quinto día. Y un paso
de 12 % no desploma más que el azar; uno de 25 % sí. Por eso una vendedora nunca recibe 25 %.

**(a) Dónde está el gasto de los grupos que sangran** (ventana madura al 2026-10-09, hojas activas): en MX,
2,459 MXN, todo en hojas con evidencia propia. En US, 2,193 USD: 2,094 (95 %) en hojas con evidencia propia y
99 en 5 hojas sin ella. Recortar hojas de pocos clics porque su grupo sangra mueve 5 % del gasto o menos.

**(b) CPC entre bid:** solo 7 hojas en MX (mediana 0.36) y 4 en US (mediana 1.05) juntan 20 clics desde su
último cambio. No alcanza para una regla; se descartó.

## A5. Ventana de 90 días con más peso a lo reciente (`a5_ventana.py`)

Qué ventana predice mejor la conversión de las 4 semanas maduras más recientes, por ad group (n = 21):

| Ventana | Correlación de rangos | Error medio (puntos de conversión) |
|---|---|---|
| 90 días planos | 0.39 | 1.04 |
| 60 días planos | 0.59 | 1.01 |
| Dos tramos: 41 días con peso 1 y 40 días con peso 1/2 | 0.57 | 0.91 |
| Exponencial, vida media de 30 días | 0.47 | 0.98 |

Los dos tramos dan el menor error y casi la mejor correlación, y se pueden leer en enteros por tramo. Con 21
grupos se lee la dirección, no el decimal. De 121 decisiones del mes con pedidos en medios, 4 cambian de
acción según cómo se redondee.

## A6. Pantallas y veredicto del impulso (`a6_pantallas.py`)

- Keywords dañadas hoy: 6 en MX (vendieron 50,039 MXN en los 90 días previos a su racha) y 3 en US (2,987 USD).
  Están 2963, 2871, 2880, 4924, 4919 y 5894.
- CTR de la cuenta: 0.88 % en MX y 1.31 % en US. El tope de 350 MXN compra cerca de 97 clics y el de 36 USD
  cerca de 88. Veinte clics piden cerca de 2,266 impresiones en MX y 1,532 en US. De ahí el piso de 2,000
  impresiones para decir "sin impresiones".

## A7. ¿Una subida trae tráfico? (`a7_subidas.py`)

| | n | Razón de impresiones mediana | Con razón de 1.10 o más |
|---|---|---|---|
| Subidas aplicadas en MX | 18 | 1.23 | 67 % |
| Subidas aplicadas en US | 8 | 1.29 | 75 % |
| Agosto sin motor | 139 | 0.91 | 30 % |

En MX la mediana de CPC entre bid antes de subir era 0.20. Aun así la subida trajo impresiones. Junto con A4:
en MX el bid mueve volumen en las dos direcciones y casi no mueve precio.

## Lo que no se pudo probar con estos datos

- El efecto real de la política nueva sobre ventas: no hay grupo de control en el pasado.
- Cuánto se excede Amazon del presupuesto diario de una campaña (se supone un día de presupuesto).
- Si `PUT /sp/productAds` permite pausar un anuncio, y la forma de `dynamicBidding` en la lista de campañas.
- El costo del ciclo en producción con las lecturas nuevas (no hay duración de ciclo registrada).
