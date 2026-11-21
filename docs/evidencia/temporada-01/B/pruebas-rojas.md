# TEMPORADA 01 B: pruebas rojas (regla 9) y notas de comprobacion

SHA previo aplicable: b9b325cfda3187f1614b342532dcda2977dde27b (master con el
bloque A integrado y desplegado). Las tres pruebas nombradas por la fila B.2
se copiaron SIN cambios a un worktree de ese SHA y la coleccion entera falla
porque las firmas que usan no existen todavia:

```
$ PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q \
    tests/test_temporada01_b.py -k "temporada01_b_pendiente or \
    temporada01_b_episodio_reparado_tras_caida or temporada01_b_fin_de_reduccion"
E   ImportError: cannot import name 'CambioFechado' from 'app.optimizer.caso'
E       (...\sha-previo\app\optimizer\caso.py)
ERROR tests/test_temporada01_b.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 1.59s
```

Las pruebas DB (episodio_reparado_tras_caida y fin_de_reduccion) fallan en el
SHA previo por la misma raiz y ademas porque las cuatro tablas de temporada no
existen (su fixture usa la cadena canonica del propio SHA, que no trae 0093).
La prueba de migracion usa `migrations/0093_temporada01_episodio.sql`, que no
existe en el SHA previo (FileNotFoundError al leer el archivo).

## Notas de comprobacion

- `tests/test_replay_ads_economico.py` (citado en el Acepta de B.3 y D.2) no
  existe ni en `tests/` ni en `tools/` en el SHA entregado: el replay del motor
  economico vive en `tests/test_optimizer_politica.py` y los candados de replay
  del ciclo en `tests/test_cycle.py` (test_replay_*, test_golden_replay_* y
  test_constantes_historicas_de_replay_son_inmutables). El Acepta de B.3 se
  corrio con ese equivalente (acepta-b3.txt: los 4 archivos del plan mas
  test_optimizer_politica.py) y queda registrada la diferencia con el texto de
  la fila.
- Windows: pytest corre con `--basetemp` propio (el cleanup de symlinks de
  pytest falla en este usuario), misma nota de la evidencia del bloque A.
- gitleaks no esta instalado en esta PC (pre-commit trueno: command not
  found). El escaneo de secretos del diff fue manual con patrones
  (password/token/secret/AKIA/PEM/ghp_/sk-): sin coincidencias en app/, tests/,
  verify/ ni en los archivos nuevos (migraciones, temporada_io, recuperacion,
  test_temporada01_b). Las credenciales de los mocks son falsas (patron del
  repo).
- La cadena de migraciones de las pruebas que ejercitan el aplicador
  (test_apply, test_apply_cola, test_apply_harvest, test_cycle,
  test_cycle_apply, test_api_write, test_apply_schema) paso a incluir
  0060/0061/0067/0093: la cadena de prueba ES la de produccion (patron de
  0081). El aplicador ahora cierra temporada_efecto en registra_sin_aplicar y
  en _confirma_resumen.
- _db_ajustes de test_api_write ya no aplica 0061/0067 a mano: la cadena base
  las trae.

## Ronda de revision (fix del bloqueo persistente)

Review de PR #697: si `reconcilia_bids` sella un intento sin resumen
(fallo:readback_ambiguo, fallo:reconciliado_sin_identidad,
fallo:ancestro_no_enabled), el efecto de temporada quedaba abierto y TX3
reventaba contra `temporada_efecto_uno_pendiente` en cada corrida.

Fix en dos partes: (1) los tres sellos sin resumen de `reconcilia_bids`
registran ahora el desenlace terminal (`registra_sin_aplicar` con
entidad_no_decisora o sin_respuesta y el sello en el detalle); (2) TX3
cierra como cinturon los efectos con TODOS los intentos sellados sin
desenlace ('sin_aplicar'), conservando abiertos los de intento sin sello.

Prueba nueva: `test_temporada01_b_efecto_con_intento_sellado_se_cierra`
(entrada 8->5.60 sin confirmar, intento sellado ambiguo, Amazon sigue en
8.00; el ciclo siguiente cierra el efecto viejo, decide la entrada otra vez
y la confirma). Demostrada fallando contra el SHA entregado del PR:

```
$ git worktree add ... e307da170df66badf30ce97f4d6d3fa88a81997b
$ pytest -q tests/test_temporada01_b.py::test_temporada01_b_efecto_con_intento_sellado_se_cierra
E           psycopg.errors.UniqueViolation: duplicate key value violates unique constraint "temporada_efecto_uno_pendiente"
1 failed in 7.60s
```
