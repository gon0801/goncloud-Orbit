# ORBIT 19 A.1 -- plan versionado y migracion expansiva

Fecha: 2026-09-06. Alcance: solo A.1; no hubo HTTP hacia Amazon ni alta de
campanas.

## Contrato aplicado

- El formato v1 queda en `plan_como_json`, `huella_plan` y
  `plan_desde_json` sin campos nuevos ni rehash de lotes existentes.
- El formato v2 usa `schema_version: 2`, lista de publicaciones, margen
  nullable real y objetivo con procedencia. La huella ordena por `listing_id`.
- `fabrica.creacion` es fail-closed: ausente o invalido equivale a `v1`.
  La lectura v2 no consulta ese interruptor; A.2 conecta el guard de altas y
  A.3 conecta el lector a registrar, reconciliar y pausar.
- `0019_fabrica_grupo_publicacion_v2.sql` solo expande: la pertenencia pasa a
  ser `(grupo_id, listing_id)`, permite margen NULL y evita repetir
  `seller_sku` dentro del grupo. No hay DROP de tablas.

## TDD

La prueba roja se ejecuta en el mismo caso de migracion con el esquema 0018
antes de aplicar 0019: un segundo listing del mismo producto produce
`UniqueViolation` por la PK vieja `(grupo_id, product_id)`. Despues de aplicar
0019, esa misma insercion pasa y el margen puede permanecer NULL.

Antes de crear `0019` y las funciones v2, la primera ejecucion focal tambien
fallo durante coleccion:

```text
FileNotFoundError: .../migrations/0019_fabrica_grupo_publicacion_v2.sql
```

Despues de la implementacion, el comando focal fue:

```text
PYTHONPATH=. /Users/dn/dev/goncloud-Orbit/.venv/bin/python -m pytest -q \
  tests/test_fabrica_plan.py tests/test_fabrica_migracion.py
31 passed, 16 skipped
```

Esa fue la ejecucion anterior a la regresion SQL final. La ejecucion focal
definitiva, despues de la revision, fue `31 passed, 17 skipped`.

Los 17 casos que ejercitan PostgreSQL real quedaron skip porque no hay
`ORBIT_TEST_DSN` local. CI de la PR provee PostgreSQL 16 y ejecuta la bateria
completa una sola vez; ahi se comprobara la migracion y sus rechazos reales.

Ruff focal:

```text
All checks passed!
3 files already formatted
```

## Fuente y limites

El contrato base es `E/0.2/contrato-api-cli-v2.md` y la reversa acordada esta
en `E/0.2/migracion-rollback.md`. La consulta productiva que confirma que
margen ausente es un dato real, no cero, sigue archivada en
`E/0.2/select-margen-signo.out.txt`. No se altero `0018` ni se ejecuto una
migracion fuera de la base temporal de CI.
