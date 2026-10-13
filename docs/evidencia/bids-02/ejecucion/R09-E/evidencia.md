# R09-E: GRANT SELECT explicito sobre v_impulso_estado (migracion 0071)

## Dato real antes de escribir (regla 8)

Lectura de solo lectura en produccion (`orbit_read`), 2026-10-13:

- `v_impulso_estado` se lee hoy por default privileges: su ACL trae
  `app_ingest=r`, `app_decide=r`, `app_read=r` y `app_admin=r`. La
  0065, ya aplicada, no se edita; la 0071 declara el GRANT.

## Prueba primero

`tests/test_migracion_0071.py::test_0071_grant_explicito_sobre_la_vista_y_su_reversa`

- Con la migracion ausente, la prueba no corre (archivo inexistente).
- Con la migracion: 1 passed. La base se prepara sin el GRANT
  explicito (REVOKE previo), la 0071 lo otorga a `app_read` y
  `app_admin`, y la reversa lo quita. Vecinos
  (`tests/test_migracion_0065.py`, `tests/test_schema_docs.py`):
  8 passed, 0 saltadas.

## Ensayo

```bash
ORBIT_SIMULACION=1 ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
  ORBIT_ENSAYO_DSN=postgresql://orbit:orbit@127.0.0.1:5433/orbit_copia_r09e \
  bash docs/evidencia/bids-02/ejecucion/R09-E/ensayo.sh
```

La base simulada `orbit_copia_r09e` es el esquema completo del repo
(78 tablas). Ultima linea de la corrida: `ENSAYO OK`. El ensayo
comprueba que `permisos.sql` dice `permisos OK` con el GRANT, que la
reversa devuelve el esquema identico y que reaplicar lo reproduce.

## Mutante

Quitar la linea `GRANT SELECT ...` de la 0071: la propia migracion
aborta en su bloque DO y la prueba pasa a rojo (1 failed). Revertido:
1 passed.

## Consulta de aplicada (para desplegar.sh)

`SELECT has_table_privilege('app_read', 'public.v_impulso_estado', 'SELECT') AND has_table_privilege('app_admin', 'public.v_impulso_estado', 'SELECT')`
