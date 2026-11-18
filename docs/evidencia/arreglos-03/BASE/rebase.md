# BASE.0 — Rebase, firmas y diferencias de contrato

Fecha: 2026-11-17. Bloque BASE de ARREGLOS 03 (plans/arreglos-03.md, filas BASE.0 y BASE.1).

## Rebase y firmas

- Rama de trabajo: `ao/arreglos-base/arreglos03-base` desde `ao/arreglos-base/root`.
- SHA base (`origin/master` tras `git fetch origin`): `0d6f92c`
  ("Merge pull request #626 from gon0801/chore/reglas-simples").
- HEAD de arranque: `0d6f92c` (rebase trivial: la rama salió de ese mismo commit).
  `git merge-base --is-ancestor origin/master HEAD` sale 0.
- SHA del código entregado (BASE.1): `4e221c6`.
- Precondición verificada: los nueve archivos del Acepta BASE.0 existen en
  `origin/master` (plan, comprobar-cron.sh, DISENO.md, DISENO-datos.md,
  DISENO-precios.md, ORDEN-IMPLEMENTACION.md, ACEPTACION.md,
  revision/test_accept_ingest.py, revision/test_accept_pricing.py).

## Lo ya resuelto en master (no se reimplementa)

- **ARREGLOS 01, bloque X (topes para cifras tecleadas a mano): INTEGRADO.**
  `TECHOS_HUMANOS_POR_MONEDA` y `exige_bajo_techo` viven en
  `app/optimizer/goals.py` (usadas por `app/api_fabrica.py` y
  `tools/fabrica_campanas.py`) y la evidencia
  `docs/evidencia/arreglos-01/X/{prueba.py,presupuestos.txt}` está versionada en
  master (`git cat-file -e origin/master:...` sale 0).
- **ARREGLOS 02, bloque C (un ajuste de campaña ya no cuenta como subida):
  INTEGRADO.** `CambioBid.direccion` es `Literal[-1, 0, 1]`
  (`app/optimizer/caso.py:149`), `_freno_trayectoria` empieza con
  `if ultimo.direccion == 0: return None` (`app/optimizer/politica.py:413`) y la
  evidencia `docs/evidencia/arreglos-02/C/{previa.txt,replay.txt}` está en master.

## Diferencias de contrato encontradas

1. **Arnés TN (55449) contra el DSN de pruebas de esta PC (55433).** Las guardas
   de `docs/evidencia/arreglos-05/revision/test_accept_pricing.py:34` solo aceptan
   `('127.0.0.1', '55449')` o `('orbit-review-current-pg', '5432')`; en esta PC el
   `ORBIT_TEST_DSN` de las pruebas normales es
   `postgresql://orbit@127.0.0.1:55433/postgres`. Hay un PG16 desechable publicado
   en 55449 únicamente para el arnés (compartido por varios workers, bajo el
   candado `~/.ao/data/locks/orbit-tn`). BASE no corre el arnés TN: sus dos Acepta
   no lo necesitan; los bloques que sí lo corren (p. ej. D-E.7, D-G.4) exportan el
   entorno TN del encargo.
2. **`temporada01` aún no entra.** El parche de `DISENO-precios.md`
   («Cruces con otros planes») muestra el estado final con `temporada01` ya en la
   línea; sobre el master actual la línea trae
   `_(auto03|orbit07|claridad01|arreglos01|arreglos02)_`, así que BASE aplicó la
   variante que el propio diseño prevé («Sin temporada01 si TEMPORADA 01 todavía
   no entró»): sumar solo `|arreglos03` al final. Cuando TEMPORADA 01 I.1 entre,
   suma su etiqueta en la misma línea sin tocar las demás.
3. **Herramientas de la guarda del módulo de despliegue.** Con `CI=1`, la guarda
   de `tests/test_despliegue_motor.py` exige `bash git psql pg_dump flock md5sum
   tar` en el PATH. En esta PC (Windows) solo están bash/git/tar (y md5sum en
   `Git\usr\bin`, fuera del PATH por omisión). Las corridas del Acepta anteponen
   al PATH `Git\usr\bin` (md5sum real) y tres stubs que siempre salen 0 (`psql`,
   `pg_dump`, `flock`): la prueba de BASE solo ejecuta `bash` y `git` reales y no
   invoca esas tres herramientas; los stubs solo satisfacen la guarda de
   colección. En el CI de GitHub (Linux) existen de verdad.
4. **Números de línea del plan (referencia `a4644aad`).** `migraciones_de` sigue
   en `tools/despliegue/comun.sh` con el grep en la línea 202 (el plan dice
   `:199-202`: sin cambio material). La prueba molde `arreglos01_a_` está en
   `tests/test_despliegue_motor.py:1119`. La nueva prueba se colocó junto a las de
   `arreglos01_a_`/`arreglos02_a_`.
5. **Números de migración.** `origin/master` llega a `0086_arreglos02_cargos`:
   las cinco migraciones de este plan saldrán del 0087 en adelante, salvo que
   otra cadena integrada antes tome el número (regla de la línea 4 del plan).
6. **Graphify.** No existe `graphify-out/graph.json` en el repo: la regla de
   graphify no aplica en este estado y `graphify update .` no genera grafo.
