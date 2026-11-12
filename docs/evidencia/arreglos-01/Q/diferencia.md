# Bloque Q - diferencia entre el diseno y el codigo real

## Lo que mandan el plan y el diseno

- `plans/arreglos-01.md`, fila Q.2: "La reversa falla con mensaje claro si ya
  hay filas `desarmado` y lo dice en su cabecera."
- `docs/evidencia/arreglos-2026-11-09/ads-escritura/opus/DISENO.md`, seccion
  `## I-H2`, en Forma y en Cambio de comportamiento: la reversa es el mismo par
  sin `desarmado`; falla si ya hay filas `desarmado` (append-only) y "solo
  aplica si no se escribio ninguna fila desarmado".
- La reversa hermana de esta misma tabla, `migrations/0068_reversa_bids02_sellos.sql`,
  hace lo mismo: `RAISE` si hay lecturas que la reversa perderia.

Se implemento asi en `migrations/0084_reversa_arreglos01_impulso_desarmado.sql`
(forma de 002eeab). No se toca ninguna fila: `impulso_lectura` es append-only
(trigger `impulso_lectura_sin_update_delete`, 0065) y corregir sigue siendo
insertar una fila nueva.

## El hueco operativo que levanto la revision F1

`tools/despliegue/comun.sh` (`revierte`) restaura el CODIGO anterior antes de
aplicar las reversas de las migraciones: primero el bloque `== codigo`
(lineas 329-343) y despues el bloque `== migraciones (orden inverso)`
(lineas 354-366). Si el vigia ya escribio al menos una lectura final con
`veredicto='desarmado'`, la reversa de la 0084 aborta y el rollback queda
`rollback_incompleto`, con el codigo anterior ya recreado.

Ese codigo anterior no conoce `desarmado`: `app/pantalla_productos.py` pasa el
valor a `frase_final`, que lanza `KeyError` al pintar los impulsos del producto.
El impulso sigue cerrado para el vigia viejo (la lectura final existe y
`_SQL_VIVOS` la excluye), pero la pantalla de productos falla mientras exista
esa fila.

No se invento una forma nueva (archivar y borrar filas de la tabla append-only,
ni mapear el veredicto en el codigo viejo) porque el plan y el diseno deciden lo
contrario y la regla del encargo es parar y documentar la diferencia. La
decision queda en el dueno.

## Opciones para el dueno

1. Aceptar el riesgo: desplegar Q y, si hace falta revertir, asumir que el
   rollback queda incompleto mientras existan lecturas `desarmado`, y que la
   pantalla de productos falla con el codigo anterior.
2. Antes de un rollback, decidir explicitamente que hacer con esas lecturas
   (por ejemplo, una migracion aprobada que las archive o las saque, con su
   propia vuelta atras). No forma parte de esta fase.
3. No revertir Q: el cambio es aditivo y el codigo viejo solo se rompe con las
   filas `desarmado`, que nacen al usar el arreglo.

## Evidencia

- `git show 002eeab:migrations/0084_reversa_arreglos01_impulso_desarmado.sql`.
- `tools/despliegue/comun.sh:329-343` (codigo antes de las reversas) y
  `tools/despliegue/comun.sh:354-366` (reversas).
- `tests/test_migracion_0084.py::test_0084_reversa_rechaza_volver_con_filas_desarmado`:
  la reversa falla con `desarmado` y deja las filas intactas.
