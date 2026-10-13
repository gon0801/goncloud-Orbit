# R09-J: candado de esquema impulso -> grupo tipo impulso (migracion 0072)

## Prueba primero

`tests/test_migracion_0072.py::test_0072_impulso_sobre_grupo_fabrica5_se_rechaza`

- Con la migracion ausente, la prueba no corre (archivo inexistente).
- Con la migracion: 1 passed. Un impulso sobre un grupo `fabrica5` se
  rechaza en la base; sobre un grupo `impulso` se acepta; la reversa
  quita el candado. Vecinos (`tests/test_migracion_0065.py`,
  `tests/test_migracion_0071.py`, `tests/test_schema_docs.py`):
  9 passed, 0 saltadas.

El camino real no se rompe: `_registrar` crea el grupo del impulso
con `_SQL_INSERTA_GRUPO_IMPULSO`, que sella `tipo = 'impulso'`.

## Ensayo

```bash
ORBIT_SIMULACION=1 ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
  ORBIT_ENSAYO_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_r09e \
  bash docs/evidencia/bids-02/ejecucion/R09-J/ensayo.sh
```

La base simulada es el esquema completo del repo antes de la 0072.
Ultima linea de la corrida: `ENSAYO OK`. El ensayo comprueba que
`permisos.sql` dice `permisos OK` con el trigger puesto, que la
reversa devuelve el esquema identico y que reaplicar lo reproduce.
La conducta la fija la prueba permanente.

## Ajuste de relectura

La 0072 suma un segundo candado: un grupo que ya tiene impulsos no
puede dejar de ser tipo impulso por un `UPDATE campana_grupo SET tipo`
posterior (trigger `grupo_impulso_tipo_guardia`). El test suma ese
caso y `permisos.sql` verifica las cuatro senales.

## FK o trigger

Una FK no puede condicionar a `campana_grupo.tipo`; el invariante va
en trigger `BEFORE INSERT` sobre `impulso`, mismo mecanismo que
`impulso_coherente` de la 0065. Solo INSERT: la fila `impulso` es
inmutable (trigger de la 0065), así que `grupo_id` no puede moverse
despues.

## Mutante

Cambiar la condicion de la funcion a `IF FALSE THEN`: el impulso
sobre el grupo fabrica5 ya no se rechaza y la prueba pasa a rojo
(1 failed). Revertido: 1 passed.

## Consulta de aplicada (para desplegar.sh)

`SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'impulso_grupo_tipo' AND NOT tgisinternal)`
