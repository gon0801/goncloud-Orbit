# 0.5 — Sensibilidad al orden de las opciones (2026-10-13)

## Contratos

El contrato vigente es `0fe322c8aa063997c65e101a8835e616cf00275dd022f0061e30ef227c7443e6`. Sus opciones van en el orden satisface, no_satisface, informacion_insuficiente, y los criterios van en ese mismo orden. El contrato invertido es `9a155982ddacdb4445fa2f3444a56a3fc6b814e9a23bef520da55753e6b62700`: las mismas opciones y los mismos criterios, al revés. El sha cambia, así que un lado no reutiliza los juicios del otro.

La solicitud de la medición es `952333c1-0cdd-53c4-8a88-aea65124c2d2`. La muestra quedó congelada en su censo.

## Denominadores

Consulta y salida: `comparacion.sql` y `comparacion.salida.txt`, lectura con `orbit_read` el 2026-10-13. La corrida del script está en `corrida.txt`.

Pares con respuesta decisiva (satisface o no_satisface) del contrato vigente, sobre la ficha que gana en todos sus listings: 2,434. De esos, 1,419 satisface y 1,015 no_satisface. La muestra tomó 100, 50 y 50, repartidos entre términos.

| | pares |
| --- | ---: |
| Muestra | 100 |
| Respuesta decisiva en los dos lados | 99 |
| Iguales | 99 |
| Cambiaron entre satisface y no_satisface | 0 |
| Abstención en el lado invertido | 1 |
| Fallos del lado invertido | 0 |

El cruce: 50 siguieron en satisface, 49 siguieron en no_satisface, y 1 pasó de no_satisface a informacion_insuficiente. En los 100, la relación guardada al armar la muestra coincide con el primer éxito del contrato vigente.

De los 99 pares con respuesta decisiva en los dos lados, ninguno cambió al invertir el orden.

## Costo

100 intenciones y 100 resultados, del 2026-10-13 01:37:47 al 01:38:12 UTC. Las 100 respuestas traen `input_tokens` y `output_tokens`. Entrada 125,826. Salida 5,253.

La tarifa es la de `ejecucion/0.3/NOTAS.md`: 0.042 USD por millón de tokens de entrada, salida en cero. Con `Decimal`:

```text
125826 * Decimal("0.042") / Decimal("1000000") = 0.005284692
```

## Límites

La muestra es 100 de 2,434, no el censo. Se corrió un solo proceso. Esta corrida pagó los 100 de una vez; no hubo reanudación. El denominador de arriba es la consulta, no el conteo de pantalla.

El tope diario del job no bloqueó estas llamadas. El 2026-10-13 el dueño lo subió de 50 a 5,000 (`config_version` id 28, señales encendidas, mínimo de clics 3) para que la medición no dejara al cron sin cupo. Ese cambio no cierra S.7. S.7 sigue pendiente de S.6 y de la nota J6-31.

Corregir o no el contrato, con este dato, es decisión del dueño.
