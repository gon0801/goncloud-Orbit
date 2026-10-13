# R14-r3: cambios pedidos por el veredicto de R14-r2

El veredicto de la segunda ronda dejo dos bloqueantes y cuatro no
bloqueantes de una linea. Esta ronda los cierra con commits locales
sobre el PR #443, sin push.

## B1: el paso 8 del checklist no podia salir del otro lado

El paso 8 leia archivos `busquedas-*.json` que nadie guardaba y
comparaba las claves `grupos` o `resultados`, que el JSON real de
`/api/dashboard/busquedas-sin-venta` no trae. Daba OK con cualquier
moneda y la corrida real terminaba siempre en `exit=4`.

Ahora el script pide el JSON de cada mercado con su funcion `cuerpo`
y compara los campos `moneda` y `plataforma` de arriba contra el
mercado pedido. El cuerpo no se imprime ni se escribe en el repo.
Respuesta vacia, JSON invalido o sin `moneda` queda NO MEDIDO, nunca
OK. La evidencia de D.6 dejo de decir que los JSON son insumos del
dueno.

La simulacion repetida trae los dos mercados en OK y el resto igual
(0 fallas, 1 sin medir por el candidato del dashboard). La corrida
forzada con la moneda cruzada esta en `checklist-forzada.txt`, con su
script en `b1_checklist_moneda.sh` adaptado del repro del revisor:
da FALLA en los dos mercados y `exit=1`.

## B2: dos sellos del ledger sin prueba que los distinguiera

`_pausa_una` sella `failed` cuando el PUT levanta `Abortar` y cuando
el readback no vuelve PAUSED, y ninguna prueba lo atrapaba. Esta
ronda suma tres pruebas con Postgres real. El PUT con timeout deja el
paso `failed` y el lote `applied`. El readback en ENABLED deja el
paso `failed` con ese `readback_estado`. El readback que levanta ya
no deja la fila en `planeado`, porque `_pausa_una` ahora tambien
sella ese camino y levanta `Abortar`.

Los mutantes del revisor M9 y M11 murieron contra la copia del SHA
con el comando de su script `m01_mutantes.py`. M4 y M17, que el
veredicto dejo como no bloqueantes, tambien murieron con las pruebas
nuevas de esta ronda.

## No bloqueantes de una linea

Las rutas del equipo en la evidencia nueva se reemplazaron por rutas
relativas y el diff contra la base ya no trae ninguna aparicion de
la ruta del equipo. Las pruebas de impulso arman su base con la 0072
incluida, asi el lanzamiento corre con el esquema completo.

## Lo que queda para R16

Lo que el veredicto marco como fila del plan y esta ronda no toca. El
mutante M12 (la carrera que cierra el `FOR UPDATE` de la 0072) sin
prueba de concurrencia. La vista previa que no marca el vivo del
mismo listing con otro plan. El 500 de `lanza` si `_sella_impulso`
revienta en la base y la perdida de la lista de lanzados. La
aclaracion de R09-I (el desarme no escribia en el ledger y ahora si)
para el cierre de la fila. El doble `psql` del rollback al reversar
la 0071. El conflicto de producto como fallo del parcial 502 quedo
aceptado sin cambio.
