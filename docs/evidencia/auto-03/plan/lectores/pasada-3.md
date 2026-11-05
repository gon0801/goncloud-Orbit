# Lector fresco, pasada 3 (solo el diff de f0ac1822)

## Bloqueante
1. Una corrida cortada que se reanuda podía dejar las claves sin aplicar con el checklist en verde, o un rollback que no revierte el código: el texto "conserva sus líneas y agrega" no decía qué pasa con `SHA_PREVIO`, `CODIGO=` y `CLAVE=`. Modelado por el lector con un repo git falso E → Gm → Fm → F2 (escenarios corte5, corte5_rb, corte6_rb).

## No bloqueantes
2. El checklist exigía `CLAVE=` a un despliegue fuera de orden que la monotonía deja sin tocar claves.
3. Una clave que ya tenía el valor nuevo podía quedar sin anotar y el checklist revertía un despliegue bueno.
4. B.1 (6) todavía decía "frente al SHA desplegado".
5. `--ensayo` usaba `SHA_PREVIO` sin definirlo.
6. El `flock` no decía dónde vive, el rollback no lo tomaba, y las dos esperas no cabían en el tope de despliegue del motor (900 s); salir por tope da 124, no 75.
7. F.2 y F.4 describían mal lo que devuelve `_SQL_TARGETS_PREVIOS` (último target live de cada hoja, no el ciclo anterior).

## Qué se hizo
Tres pasadas seguidas trajeron un bloqueante en la misma pieza, la receta de despliegue escrita como prosa. En vez de una cuarta pasada, la receta corregida quedó como un modelo ejecutable: `modelo-despliegue/modelo.py`, con nueve escenarios con aserciones. Al reintroducir cada falla encontrada por los lectores (rollback sin `sha`, `SHA_PREVIO` nuevo al reanudar, respaldo repetido con la última `CODIGO`, claves reanotadas con rollback sin depurar), el modelo sale 1. B.5 exige que la prueba de los scripts reales reproduzca esos escenarios.
