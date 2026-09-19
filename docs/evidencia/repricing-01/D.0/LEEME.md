# D.0 — base de producción lista para la sonda (REPRICING 01)

Paquete para la fila **D.0** del plan v1.3: aplicar `migrations/0039_precio.sql`
y sembrar las 20 claves `precio_*` como `config_version` nueva que copia la
vigente. **Lo corre el dueño**; ningún goal, ninguna decisión, ningún precio.

## Cómo se corre

Desde la raíz de un árbol del repo en la Mac **parado en `origin/master` y sin
cambios**, en una ventana tranquila (**no** en la misma ventana de despliegue
que D.3 de `fabrica-02`). Todo lo que corre sale del árbol (la 0039, la
siembra, el readback, el verificador y `app/`), así que en producción el guion
lo exige: si `HEAD` no es `origin/master` o hay cambios sin commitear en
archivos del repo, para en rojo antes de tocar el server. Los archivos nuevos
sin seguimiento no cuentan: no cambian lo que corre (todas las entradas son
rutas fijas del repo), y las `salidas/` de una corrida anterior, todavía sin
commitear, no deben impedir re-correrlo.

El checkout principal suele estar en una rama de Muse, así que lo seguro es un
worktree nuevo (el `.venv` se enlaza del checkout principal):

```
git -C /Users/dn/dev/goncloud-Orbit fetch origin
git -C /Users/dn/dev/goncloud-Orbit worktree add --detach /Users/dn/dev/wt-d0-corrida origin/master
ln -s /Users/dn/dev/goncloud-Orbit/.venv /Users/dn/dev/wt-d0-corrida/.venv
cd /Users/dn/dev/wt-d0-corrida && bash docs/evidencia/repricing-01/D.0/correr.sh 'D.0: <go literal del dueño>'
```

En la sesión de Claude cada línea va con `!` delante. El go literal queda como `label` de
la `config_version` nueva. La última línea dice `D0-VERDE` o `D0-ROJO`, y todo
queda en `salidas/<stamp>/` con `CORRIDA.txt` de resumen.

## Qué hace, en orden

| Paso | Archivo | Con qué rol |
|---|---|---|
| 0. Preflight: en producción el árbol es `origin/master` sin cambios; 0039 del árbol = `origin/master`, `btree_gist`, `listing` sin duplicados, 8 caps de Ads vivos, estado de la 0039 (`ausente`, `completa` o `parcial`: las cinco tablas más `listing_id_platform_key`; con `parcial` corta antes de migrar o sembrar) y de las claves | `preflight.sql` | lector, `BEGIN READ ONLY` |
| 1. Backup `--schema-only` completo (bloque de `docs/DEPLOY.md` §0039) | en `correr.sh` | superusuario, en el server |
| 2. 0039 en una transacción (si el `EXCLUDE` no se crea, se revierte entera) | `migrations/0039_precio.sql` | superusuario |
| 3. Siembra de las 20 claves (se niega si ya hay claves `precio_*`) | `siembra.sql` | superusuario |
| 4. Readback: cinco tablas en cero filas, constraint, índices, `EXCLUDE`, triggers, caps `precio:*` = 5, caps de Ads **idénticos** a los de antes, 20 claves | `readback.sql` | lector, `BEGIN READ ONLY` |
| 5. La config vigente pasa, con el código de `origin/master`, `leer_config`, la banda de goals, los días de catálogo y lo que la corrida (A.5) y la pantalla (A.6) validan al arrancar: el cap de `amazon_mx`, `amazon_us` y `meli`, `precio_freno_dias_error` y `precio_aviso_dias_sin_evaluar` | `verificar_config.py` | local, sobre el JSON leído como lector |
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

Las dos primeras corridas (`20260918-*`) son de antes del cierre de la Fase 11.
Las dos últimas (`20260919-070540` y `-070541`) repiten el ensayo con el guion
de este PR: el verificador con los validadores de la corrida y la pantalla y
el preflight con el estado de la 0039 en tres valores. La primera ve la 0039
`ausente`, la aplica y siembra; la segunda la ve `completa` y salta las dos
cosas; las dos dan `D0-VERDE`. La guarda de «árbol en `origin/master`» solo
corre en producción, y la prueban `tests/test_precio_d0.py` con un repo
desechable y un `ssh` falso, igual que el corte por una 0039 `parcial`.

## Después de la corrida (lead)

Commitear `salidas/<stamp>/`, marcar en `docs/DEPLOY.md` §0039 que la 0039
**ya está aplicada** (con la fecha, y sin atribuírsela a D.1), y cerrar la
celda de D.0 en el plan con el SHA.
