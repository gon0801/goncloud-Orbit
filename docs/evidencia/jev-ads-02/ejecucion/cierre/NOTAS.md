# Cierre del plan JEV ADS 02

El dueño, el 2026-10-12, ordenó terminar el plan con una sola prueba. No pidió
prender ni apagar nada.

## S.6

La semana con tope 0 no se corrió. El job ya estaba encendido con tope 5,000
(`config_version` 28: `jev.senales` true, `jev.tope_diario` 5000,
`jev.min_clics` 3). El dueño rechazó esperar esa semana. La fila se cierra
por esa orden, no porque la semana haya ocurrido.

## S.7

No hubo muestra de 50 búsquedas por mercado ni etiquetas a ciegas. El dueño
confirmó cuatro lecturas reales de amazon_mx y dijo que son ciertas:

- «baño»: ajena. Cero órdenes aquí y en otros grupos, 6 clics.
- «arras de plata»: corresponde y no vende.
- «arras matrimoniales», un grupo: vende en otro (41 órdenes en otros grupos).
- «arras matrimoniales», otro grupo: ya vendió aquí, porque el historial
  tiene ventas aunque la ventana traiga cero.

J6-31 quedó arreglado en el mismo cambio: la frase de ajena usa el día del
listado de Amazon (`listado_de`), no el día de las ventas (`datos_hasta`).

## S.8

El aviso de Telegram está escrito y probado. `jev.avisos` sigue ausente: sin
esa clave en true el job no inserta ni envía. Encenderlo es otro cambio de
config.

## Lo que no se construye

E.1, E.2 y E.3 no se construyen. La medición no llegó a 50 búsquedas ni a los
porcentajes del criterio, y el dueño no pidió tocar Amazon.

5.1, 5.2 y 5.3 no se construyen. Las bibliotecas de fábrica siguen vacías y
no hubo piloto.

## R y D

R no se corre: `cross-review.ps1` quedó fuera por regla permanente.

D: S.1 a S.5 ya estaban desplegados. S.8 entra en este cambio con el aviso
apagado.

## Despliegue del cierre

El cambio de cierre (#440, `30cc62f`) se unió a master sin desplegar. Se
desplegó el 2026-10-13 a las 03:57 UTC con `desplegar.sh` de esta carpeta:
solo código, sin migración ni cron. Respaldo en `predeploy-20261013-0357`;
la reversa es `docs/evidencia/bids-02/ejecucion/D.5/rollback.sh` con ese
sello. Salidas en `desplegar-salida.txt` y `checklist-salida.txt`.

El checklist dio 0 mal: el contenedor corre el código del SHA, las cuatro
pantallas responden 200, la única señal ajena trae `listado_de` y su frase
cita ese día, `jev.avisos` sigue ausente y `jev_aviso` tiene 0 filas.

Las dos fechas de esa señal («baño») caen el 2026-10-12, así que la frase
no cambió a la vista. Lo que cambió es que la API ya devuelve `listado_de`.

## El job antes del 2026-10-12

Del 2026-10-07 al 2026-10-12 las corridas del cron cerraron con
`sin_api_key` y tope 0: `typesafe.json` llegó al servidor el 2026-10-12 a
las 23:29 UTC. El job pagó 50 llamadas ese día, en una corrida manual con
tope 50. El tope 5,000 es de la `config_version` 28 (2026-10-13 01:37 UTC).
La corrida en seco del checklist anticipa 4,900 llamadas para la del cron
de las 09:30 UTC del 2026-10-13, la primera con ese tope.

## Notas J6

J6-31 se arregló. J6-1 a J6-30 y J6-32 a J6-45 se aceptan sin arreglo de
código: no bloqueaban el cierre y el dueño ordenó no esperarlas. J6-21 sigue
sin decisión del dueño; no se inventó.

## Observación de la revisión del cierre

No bloquea con el aviso apagado. En `_aplicar` el paso de avisos corre antes
de escribir el evento `fin`. Si ese paso levanta una excepción, la corrida
queda abierta en `jev_corrida` aunque sus señales ya estén escritas. Está
anotado en "Huecos conocidos" del plan y se arregla antes de encender
`jev.avisos`.
