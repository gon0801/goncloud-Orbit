# Cierre del plan JEV ADS 02

El dueño, el 2026-10-12, ordenó terminar el plan con una sola prueba. No pidió
prender ni apagar nada.

## S.6

La semana con tope 0 no se corrió. El job ya estaba encendido con tope 5,000
(`config_version` 28: `jev.senales` true, `jev.tope_diario` 5000,
`jev.min_clics` 3). El dueño rechazó esperar esa semana. La fila se cierra
por esa orden, no porque la semana haya ocurrido.

El tope sí valió 0 del 2026-10-07 al 2026-10-12 (ver "El job antes del
2026-10-12"). Fueron cinco días y no siete, sin clave de TypeSafe, y no hay
evidencia de la comparación del ciclo y la cola que pide el DoD. Por eso no
cuenta como la semana de S.6.

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
sello. Las salidas de ese despliegue están en el historial de git
(`2ce27ab`); los archivos de esta carpeta son los del remate.

El checklist dio 0 mal: el contenedor corre el código del SHA, las cuatro
pantallas responden 200, la única señal ajena trae `listado_de` y su frase
cita ese día, `jev.avisos` sigue ausente y `jev_aviso` tiene 0 filas.

Las dos fechas de esa señal («baño») caen el 2026-10-12, así que la frase
no cambió a la vista. Lo que cambió es que la API ya devuelve `listado_de`.

## El job antes del 2026-10-12

El tope sale de `config_version`. De la 23 (2026-10-07) a la 26 valió 0.
La 27 (2026-10-12 23:34 UTC) lo puso en 50. La 28 (2026-10-13 01:37 UTC) lo
puso en 5,000.

Las once corridas del cron del 2026-10-07 al 2026-10-12 21:30 UTC, y una
manual del 12 a las 23:18 UTC, cerraron con `sin_api_key`: `typesafe.json`
llegó al servidor ese día a las 23:29 UTC.
Con tope 0 tampoco habrían pagado. El job pagó sus primeras 50 llamadas el
2026-10-12 a las 23:34 UTC, en una corrida manual con el tope en 50.

Las 100 llamadas que el seco del checklist cuenta para el 2026-10-13
(`llamadas_hoy=100`) son de la medición 0.5, no del job
(`ejecucion/0.5/NOTAS.md`). El seco anticipaba 4,900 llamadas para el cron
de las 09:30 UTC del 2026-10-13. Esa corrida se adelantó a las 05:08 UTC
(sección "Primera corrida con el tope en 5,000"); con el día en 5,000 de
5,000, los cron de las 09:30 y 21:30 UTC del 13 no pagan.

## Notas J6

J6-31 se arregló. J6-1 a J6-30 y J6-32 a J6-45 se aceptan sin arreglo de
código: no bloqueaban el cierre y el dueño ordenó no esperarlas. J6-21 ya
tiene decisión del dueño (sección "J6-21", abajo).

## Observación de la revisión del cierre

No bloquea con el aviso apagado. En `_aplicar` el paso de avisos corre antes
de escribir el evento `fin`. Si ese paso levanta una excepción, la corrida
queda abierta en `jev_corrida` aunque sus señales ya estén escritas. Está
anotado en "Huecos conocidos" del plan.

Resuelto en #444: la corrida escribe su `fin` con `avisos_error` y las
entregas que sí salieron, y después falla con salida 1.
`/salud` muestra ese error en la última corrida.

## Primera corrida con el tope en 5,000

El dueño ordenó no esperar al cron. La corrida se lanzó a mano el
2026-10-13 a las 05:08 UTC como `gon`, con la línea del cron, y cerró a las
05:30 UTC: motivo `tope`, 370 unidades, 4,900 llamadas, 16 fallos, 370
señales, 0 avisos. Con las 100 llamadas de la medición 0.5 el día quedó en
5,000 de 5,000.

Lecturas del lote anterior (2026-10-12 23:34 UTC) y de este:

| Lectura | Antes | Después |
| --- | ---: | ---: |
| `sin_lectura` | 194 | 153 |
| `relevante_sin_venta` | 61 | 93 |
| `vende_en_otro` | 60 | 62 |
| `vendio_aqui` | 43 | 43 |
| `ajena` | 1 | 19 |

De las 153 sin lectura, 125 traen `jev_no_evaluada` (se acabó el cupo), 19
`sin_observaciones` y 9 `jev_sin_veredicto`. Las 19 ajenas son 16 de
amazon_mx y 3 de amazon_us.

Entrada: 6,238,281 tokens. Con la tarifa de `ejecucion/0.3/NOTAS.md`:

```text
6238281 * Decimal("0.042") / Decimal("1000000") = 0.262007802
```

Mediana de 253 ms por llamada.

Dos intentos anteriores, lanzados como `root`, no corrieron: `flock` no
pudo abrir `/tmp/jev-senales.lock`, que es de `gon`. Dejaron dos líneas de
error en `jev-senales.log` y nada más.

## Los 16 fallos

Los 16 son `respuesta_invalida: las probabilidades suman 0.99`. Las otras
4,884 respuestas suman 1.00. El proveedor redondea cada probabilidad a dos
decimales. #444 cambia la validación: admite medio centésimo de desvío por
opción, 0.015 con las tres del contrato.

## Despliegue del remate

#444 (`6c6486d`) se desplegó el 2026-10-13 a las 06:33 UTC con el mismo
`desplegar.sh`: solo código. Respaldo en `predeploy-20261013-0633`. Las
salidas de esta carpeta son las de ese despliegue. El checklist dio 0 mal,
con 19 señales ajenas, todas con `listado_de`.

El seco del checklist corrió con el cupo del día agotado (`pagaria=0`,
`llamadas_hoy=5000`). Con cupo en cero el seco no recorre pares: esa
sección prueba que el job arranca con el código nuevo y lee la config, no
la validación nueva ni el cierre con `avisos_error`. Esas dos las cubren
las pruebas de #444; en producción la validación corre en la siguiente
corrida que pague.

## J6-21

El dueño decidió el 2026-10-13 UTC dejarlo como está. Un producto sin ficha
sigue dando `Indeterminado`, como selló S.2, aunque además falte cupo. En
el lote del 2026-10-13 ninguna señal trae ficha ausente.

## Tope en 10,000

El dueño ordenó el 2026-10-13 UTC duplicar el cupo ("el cupo del dia hazlo
el doble"). La `config_version` 29 (2026-10-13 07:05 UTC) copia
la 28 y solo cambia `jev.tope_diario` de 5000 a 10000; el resto de los
ajustes es idéntico y `jev.avisos` sigue ausente. Con el día en 5,000
llamadas, el seco posterior anticipa 5,000 más (`pagaria=5000 tope=10000
llamadas_hoy=5000`): el cron de las 09:30 UTC del 13 sí paga. A la tarifa
de 0.3, el tope completo cuesta cerca de 0.52 USD al día.
