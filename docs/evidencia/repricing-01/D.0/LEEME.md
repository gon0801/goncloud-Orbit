# D.0 — base de producción lista para la sonda (REPRICING 01)

Paquete para la fila **D.0** del plan v1.3: aplicar `migrations/0039_precio.sql`
y sembrar las 20 claves `precio_*` como `config_version` nueva que copia la
vigente. **Lo corre el dueño**; ningún goal, ninguna decisión, ningún precio.

## Cómo se corre

Desde la raíz del repo en la Mac, con `origin/master` al día y en una ventana
tranquila (**no** en la misma ventana de despliegue que D.3 de `fabrica-02`):

```
bash docs/evidencia/repricing-01/D.0/correr.sh 'D.0: <go literal del dueño>'
```

En la sesión de Claude va con `!` delante. El go literal queda como `label` de
la `config_version` nueva. La última línea dice `D0-VERDE` o `D0-ROJO`, y todo
queda en `salidas/<stamp>/` con `CORRIDA.txt` de resumen.

## Qué hace, en orden

| Paso | Archivo | Con qué rol |
|---|---|---|
| 0. Preflight: 0039 del árbol = `origin/master`, `btree_gist`, `listing` sin duplicados, 8 caps de Ads vivos, estado de la 0039 y de las claves | `preflight.sql` | lector, `BEGIN READ ONLY` |
| 1. Backup `--schema-only` completo (bloque de `docs/DEPLOY.md` §0039) | en `correr.sh` | superusuario, en el server |
| 2. 0039 en una transacción (si el `EXCLUDE` no se crea, se revierte entera) | `migrations/0039_precio.sql` | superusuario |
| 3. Siembra de las 20 claves (se niega si ya hay claves `precio_*`) | `siembra.sql` | superusuario |
| 4. Readback: cinco tablas en cero filas, constraint, índices, `EXCLUDE`, triggers, caps `precio:*` = 5, caps de Ads **idénticos** a los de antes, 20 claves | `readback.sql` | lector, `BEGIN READ ONLY` |
| 5. La config vigente pasa `leer_config`, la banda de goals y los días de catálogo con el código de `origin/master` | `verificar_config.py` | local, sobre el JSON leído como lector |
| 6. `apply_quota_state` de `precio:amazon_mx` nace con cap 5, en `BEGIN … ROLLBACK` | `cuota.sql` | `ORBIT_DSN_ADMIN` de la app |

Re-correrlo es seguro: con la 0039 puesta no repite backup ni migración, y con
las 20 claves no siembra; el readback corre siempre.

**Desviación declarada**: la fila pide los pasos 5 y 6 «desde el contenedor
app». El código del motor todavía no está desplegado (eso es D.1), así que el
paso 5 corre local con el código de `origin/master` sobre la config leída de
producción, y el paso 6 usa el DSN de admin del contenedor app desde el
contenedor de la base.

## Ensayo del lead

`ensayo-local/` tiene dos corridas contra una base local con las migraciones
de `master` (sin la 0039 ni las de datos o reversa) y una config con los caps
de Ads: la primera aplica y siembra (`D0-VERDE`), la segunda salta las dos
cosas (`D0-VERDE`). El ensayo cazó que el `grep` del backup de `DEPLOY.md`
§0039 buscaba `CREATE OR REPLACE FUNCTION`, que `pg_dump` nunca escribe: el
backup habría abortado con «DUMP INVALIDO». `tests/test_precio_d0.py` lo
prueba ahora contra un `pg_dump` real, junto con la siembra, la cuota, el
readback y el verificador.

## Después de la corrida (lead)

Commitear `salidas/<stamp>/`, marcar en `docs/DEPLOY.md` §0039 que la 0039
**ya está aplicada** (con la fecha, y sin atribuírsela a D.1), y cerrar la
celda de D.0 en el plan con el SHA.
