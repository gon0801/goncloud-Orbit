# Lectores frescos del plan AUTO-03: hallazgos y correcciones

Pasada 1, 2026-11-05, sobre master `ff28eb33`. Dos lectores (opus) en paralelo que ejecutaron los comandos: implementador del motor (`implementador.md`) y escéptico (`escéptico.md`). Encargo común en `encargo.md`. Solo lectura en producción.

## Bloqueantes y su corrección

| Hallazgo | Quién | Corrección |
|---|---|---|
| Una guarda que aborta lanza un rollback que deshace el despliegue anterior | ambos | B.1 (0): `ultimo.env` nuevo antes de las guardas, cada paso anota antes de cambiar; guardas que esperan hasta 600 s y salen 75 sin cambios. B.2: el rollback solo deshace lo anotado por este `MOTOR_SHA` y no hace nada con `abortado_sin_cambios`. |
| El bloque B pasaba con scripts `exit 0` | escéptico | B.5 exige aplicar, comprobar, revertir y no tocar nada ajeno, con `docker` y `ssh` falsos; la `prueba-real` de B es `desplegar.sh --ensayo` contra producción en solo lectura. |
| `sonda.sh` daba verde con SQL roto o ssh caído | escéptico | B.4: `set -euo pipefail`, `ON_ERROR_STOP`, falla sin filas o con una fila que no empiece con `t\|`; formato `t\|nota`. |
| `meta_live` fallaba recién desplegado o pasaba en vacío | escéptico | F.5: revisa la config y solo ciclos con la meta en vivo; sin ciclo así devuelve `t\|sin ciclo con meta live todavía`. |
| Una falla de productos se escondía en sombra y apagaba el ciclo en vivo | escéptico | D.4: lectura en SAVEPOINT dentro de TX2. D.5: `replay` falla con error en notas, conteo distinto o sin evaluaciones. F.2: en vivo solo las hojas de grupos aptos quedan sin decisión. |
| `concilia` pedía más de 31 días | escéptico | E.3: un reporte de la ventana madura `[D-60, D-30)`; sale 2 si no termina. |
| La evidencia previa a encender la escribía el implementador | ambos | F.1 e I.2: el `Acepta` corre en producción `evalua`, `replay` y `concilia` por mercado. |
| El checklist revertía un apagado manual | ambos | B.2: solo compara las claves que este despliegue convergió. |
| H.7 pedía una prueba que necesita código de I | implementador | El caso pasa a I.6. |
| La `prueba-real` de E dependía de un ciclo natural; `concilia` sin `--json`; `replay` sin argumentos sin definir | implementador | E: `evalua --json` y `replay`/`concilia --json` por mercado dentro del contenedor; D.5 define `replay` sin argumentos. |

## No bloqueantes corregidos en la misma ronda

C.1 fixture `_db_temporal_c2a` con la 0076. C.3 etiquetas de `PISA_POR_PELDANO`. C.4 `hoy` y `piso=0`. C.6 `Acepta` que distingue y prueba de la clave. C.7 formato de la clave de gasto y del cuerpo. D.1 regla del trigger y conteo 88 a 89 de `verify/`. D.6 error de SQL real. D prueba-real con sonda `meta_sombra`. E.2 y H.9 corren `orbit-verify launch` y `drive-productos`. E.4 comando para sacar el JSON. F aviso honesto sobre cobertura. H.5, I.8 sondas con la config. H.8 causa `sin_cobertura_d7`. I.1 cita corregida. I.2 sin código no desplegado. I.8 `Acepta` que distingue por el manifest. `Acepta` con Python 3.12. Migraciones de otros planes ya no traban el despliegue. S7: el halo de la meta suma solo fechas con fila, porque sin fila no hubo clics ni ventas atribuidas; S6 queda solo para la pausa. A.3 rotula el sustituto previo a D.7 y trae su comando.

## Comprobado después de corregir

- `motor valida`: ok, 9 bloques, 54 filas.
- Con `ssh`, `docker` y `python` falsos: F.1, I.2 y la `prueba-real` de E salen 0 con todo bien y 1 si `concilia` falla; el mercado se expande dentro del contenedor.
- I.8: sale 1 con el manifest que dice "no entregado" y 0 con el manifest actualizado.
- `bash -n` sin errores en C.6, E.2, H.9, I.8, B.2, F.5 y la `prueba-real` de B.

## Pasada 2 (solo el diff)

Un bloqueante, introducido por la corrección anterior: el rollback no devolvía `$SRV/despliegue/sha`, así que tras revertir un despliegue los interruptores no se volvían a aplicar. Corregido con `SHA_PREVIO` anotado en (0) bajo el `flock`, contra el que comparan la monotonía y la convergencia, y que el rollback restaura; B.5 (h) lo prueba. Los nueve no bloqueantes también se corrigieron: monotonía que tampoco toca claves, `productos-modo` idempotente, qué hacer con la salida 75, `--ensayo` sin escrituras, `flock` antes de `ultimo.env` y reanudación de una corrida cortada, respaldo y restauración de todos los archivos desplegados, grupos aptos cuando falla la lectura, `CASCADE` en D.6 y E.4 alineado con S6. Detalle en `pasada-2.md`.

## Pasada 3 (solo el diff) y cierre

Un bloqueante más en la misma pieza: al reanudar un despliegue cortado, la receta no decía qué `SHA_PREVIO`, respaldo y claves valían, y podía dejar las claves sin aplicar con el checklist en verde, o un rollback que no revertía el código. Fueron tres pasadas seguidas con un bloqueante en la receta de despliegue, señal de que esa parte no convergía corrigiendo prosa. Se cerró así: la receta corregida quedó como modelo ejecutable (`modelo-despliegue/modelo.py`, nueve escenarios con aserciones, sale 0), que detecta cada falla encontrada al reintroducirla, y B.5 exige que la prueba de los scripts reales reproduzca esos escenarios y corre el modelo en su `Acepta`. Los seis no bloqueantes también se corrigieron. No se corrió una cuarta pasada; detalle en `pasada-3.md`.

## Hechos medidos por los lectores (solo lectura, 2026-11-05)

Ningún goal tiene target en producción. En MX hay 5 ad groups exclusivos de productos con margen positivo, con 76 hojas ENABLED. Los ciclos corren 08:40 y 08:41 UTC. Hay 18 a 19 crons con `app.cli` y la ingesta de las 07:10 dura 9 a 17 minutos. `ad_ids` es un arreglo jsonb. El tope de verificación del motor en la Mac es 3600 s y el de despliegue 900 s.
