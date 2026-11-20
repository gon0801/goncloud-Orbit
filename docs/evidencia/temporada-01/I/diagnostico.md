# TEMPORADA 01 I — Diagnostico, hallazgo del guard y demostraciones (regla 9)

Fecha: 2026-11-20. Base: `a18308f2c44ca1adca5c709687a709f01bfa6f93` (origin/master).
Candidato recuperado: `rescate/temporada01-bloque-i` = `1885400838127739509b3dfe259f5c5224c0d8ff`
(fast-forward desde la base; 7 commits). PR del bloque: #684 (borrador de David desde
`ao/bloque-i-implementacion/temporada01-bloque-i` en `0191c68`, actualizado aqui).

Arnes de esta PC: `ORBIT_TEST_DSN=postgresql://orbit@127.0.0.1:55433/postgres` (PG16 local),
`PYTHONPATH=.`, `uv run --frozen --python 3.12 python -m pytest`. Para el motor de despliegue,
PATH con `Git\usr\bin` (md5sum real) y stubs que salen 0 (`psql`, `pg_dump`, `flock`) segun
`docs/evidencia/arreglos-03/BASE/rebase.md` punto 3.

## 1. Los 9 fallos: causa raiz y correccion (commit `b2959fd`)

Sintoma (informe `C:/Users/ehven/diagnostico-bloque-i.txt`): `ResumenReconciliacion` toda en
ceros; la reconciliacion no cuenta nada.

Verificacion contra la base: **los mismos 9 tests fallan sobre `a18308f`** (con `-k` y con el
archivo completo: `9 failed, 63 passed`). La regresion NO la introduce el bloque I: entra a la
base con la consolidacion del lector estricto de listas
(`fix(harvest): consolidar lector estricto _lista_completa en B-C/TN02`, commit `3e7164b`,
2026-11-19, con `9d2745f` y `546265f`), ya integrada en `origin/master`.

Causa raiz exacta (traza con `_continua_job` directo, sin el `except`):

    app.apply_harvest.ListaIncompleta: ambiguo: contenedor o item invalido
      en _paso_keyword -> _lista_completa(cliente, "/sp/negativeKeywords/list")

El lector `_lista_completa` (codigo de produccion, fail-closed) valida cada fila de la pagina
contra el vocabulario cerrado del contenedor (`_elemento_list_valido`): `matchType` de
`negativeKeywords` ∈ {NEGATIVE_EXACT, NEGATIVE_PHRASE}, `matchType` de `keywords` ∈
{EXACT, PHRASE, BROAD}, `state` ∈ {ENABLED, PAUSED, ARCHIVED} (UPPER). El Amazon simulado de
los 9 tests sembraba la forma que el lector viejo toleraba: negativos con
`"matchType": "EXACT"/"exact"` y estados en minusculas (`"enabled"`). `reconcilia_harvest`
traga esa `ListaIncompleta` como lectura ambigua (`except (AdsApiError, AdsAuthError):
continue`) y el resumen queda en ceros: exactamente el sintoma del informe.

El wire REAL habla el enum completo (probe 2.5, citado en `_identidad` y `_payload_negative`
del propio test). Correccion: el mock de los 9 tests habla el enum real (`NEGATIVE_EXACT` +
estados UPPER en negativos; `EXACT` + `ENABLED` en keywords). `_identidad` normaliza el enum
al comparar: las aserciones quedan intactas, el lector de produccion no se toca, nada se
debilita.

Demostracion (regla de diseno 9), mismo arnes:

| estado | resultado |
| --- | --- |
| base `a18308f` | 9 fallan (igual que el sintoma del informe) |
| candidato `1885400` sin corregir | 9 fallan |
| candidato + `b2959fd` (corregido) | 9 pasan |

## 2bis. Hallazgo de review (Devin): guardas del margen vigente sobre su propio conjunto

Comentario en `migrations/0092_temporada01_margen_vigente.sql` (thread
`PRRT_kwDOUANqbs66MYH_`, kind bug): los cargos que entran al numerador del
`margen_vigente_pct` provienen de `ordenes_vigentes` (`cargos_vigentes`),
pero las guardas de moneda y clasificacion (`cargos_ag`) se calculaban solo
sobre el conjunto historico (`cargos`/`ordenes_cubiertas`). Con ventas sin
costo historico y costo de hoy vivo, un cargo de una orden solo-vigente en
OTRA moneda o con `fee_type NULL` entraba al numerador sin activar ninguna
guarda: el vigente publicaba 40 % mezclando USD con la venta MXN (regla 4).
El mismo desfase existia en producto (`cargos_producto_vigente` sin
indicadores; guardas desde `cp`) y familia (`prod` media integridad desde
`co`/`o`, no desde `cov`/`ov`).

Correccion (espejo mecanico de guardas): `cargos_vigentes` pasa a ser el
conjunto completo (con orden de `ordenes_vigentes` UNION ALL sin orden
compartido) y `cargos_ag_vigentes` trae monto + `n_monedas_cargos` +
`moneda_cargos` + `fees_sin_tipo`; el CASE del vigente usa esos indicadores.
En producto, `orden_cubierta_vigente` trae `n_monedas`, `cargos_orden_vigente`
espeja `cargos_orden` (monto, n_monedas, moneda, fees_sin_tipo) y
`cargos_producto_vigente` trae los mismos indicadores que `cargos_producto`;
el CASE vigente usa `cpv.*`. En familia, `prod` agrega `fees_sin_tipo_vigente`,
`n_monedas_orden_vigente` y `moneda_vigente` (el CASE de moneda espejado sobre
`cov`/`ov`); `ag` los agrega y el CASE vigente los usa. Los indicadores
historicos quedan separados para el margen historico.

Pruebas (casos del ejemplo del review, las tres vistas): con el arnes del
caso sin costo historico, (a) cargos con orden en USD: `margen_vigente_pct`
NULL en plataforma, producto y familia; (b) cargos con orden con `fee_type
NULL`: NULL por fees_sin_tipo vigente.

Demostracion (regla 9): contra la migracion de `ddf1919` el caso (a) falla
con `assert Decimal('40.000...') is None` (el vigente publicado sin las
guardas propias); con la correccion pasan los 3 tests del archivo.

## 2ter. Hallazgo del dueño: la guarda descartaba el margen vigente (commit `a8218b4`)

Hallazgo reproducible en `0191c68`: con 90 dias de ventas sin costo historico y costo de hoy
vivo, la vista devuelve `margen_vigente_pct` 40 y `cobertura_vigente` 1, pero
`medicion_desde_fila` conservaba `venta_cubierta` HISTORICA (NULL porque ninguna venta tuvo
costo en su fecha): `motivo_dato_invalido` devolvia `sin_margen` y descartaba el 40 % donde
corresponde meta 20 con fraccion 0.5. I.2 exige que las ventas cubiertas del margen
`margen_vigente_pct` sean las que tienen costo de hoy, aunque el dia de la venta no lo
tuvieran. El dueño verifico la regresion con 70 dias sembrados en PostgreSQL 16 (la vista da
40 y cobertura 1; el motor deriva None donde debe dar 20).

Correccion: `goals.importe_cubierto_vigente(venta_total, cobertura_vigente)` — UN solo
calculo, con las magnitudes que YA publica la vista (contrato I.2 intacto: 0092 conserva sus
tres columnas al final y su reversa restaura 0086; los SELECT del motor solo suman
`venta_total` a la lectura). Los cuatro lectores lo usan: plataforma
(`medicion_desde_fila`), familia del ciclo (`_lee_familias_ciclo`), familia de revalidacion
(`economia_revalidacion_io._medicion`) y producto (`productos_io`). La guarda solo usa el
signo del importe; dato faltante sigue siendo None (regla 3).

Prueba de comportamiento (caso nuevo en `test_temporada01_i_equilibrio_con_costo_vigente`):
90 ventas de 1,000 MXN con cargos de -300 (venta_total 90000, dias 90 >= 60), costo historico
cerrado en D-110 (cubre 0 ventas) y costo de hoy 300 publicado en D-14 (cubre TODAS las
ventas vigentes). La vista da `venta_cubierta` NULL y `margen_neto_pct` NULL (historico),
`cobertura_vigente` 1 y `margen_vigente_pct` 40; producto y familia miden igual. El lector
proyecta `venta_cubierta` 90000 y `resuelve_target_margen` da meta derivada 20 sin motivo.
Demostracion (regla 9): en `0191c68` el caso falla con
`assert medicion.venta_cubierta == Decimal("90000")` → `None == Decimal('90000')`; con la
correccion pasa.

## 3. Demostraciones de las pruebas nuevas del bloque

- `test_temporada01_i_migracion_en_migraciones_de` (I.1, instalar): sobre el
  `tools/despliegue` de la base falla — falta `0092_temporada01_prueba.sql` en el stdout de
  `migraciones_de` (el patron sin `temporada01`); con el `comun.sh` del bloque pasa
  (`acepta-i1-despliegue.txt`).
- `test_temporada01_i_equilibrio_con_costo_vigente` (I.3, dinero): con el `app/` de la base
  (y la migracion 0092 presente, deliverable del propio bloque) falla en la primera asercion
  que exige el mapeo nuevo: `medicion.margen_neto_pct` da 30 (historico) donde el test exige
  40 (vigente): el `medicion_desde_fila` de la base gobierna con el costo de la fecha de
  venta. Con el `app/` del bloque pasa (`acepta-i-temporada01.txt`).

## 4. Estado de las corridas amplias (descartadas por el dueño)

- La corrida completa inicial quedo INTERRUMPIDA (el proceso pytest murio sin resumen ni
  identidad de fallos, con las marcas F visibles sin nombre). Se reconocen esas marcas sin
  identidad; el contraste dirigido POSIX con PostgreSQL real lo hizo el dueño sobre
  `a18308f` y `0191c68`: mismos resultados y el mismo fallo previo de `ledger.py`, sin
  skips, anotado en el PR. No se repite aqui.
- Las corridas por trozos (chunk 0: 1 fallo; chunk 1: 41 fallos) quedan descartadas como
  evidencia: los fallos del trozo 1 eran el candado del motor de despliegue sin `flock`
  real para bash en Windows y dos pruebas POSIX-only (`os.fchmod`, permisos 0o700), todas
  ambientales y ajenas al bloque; la comparacion dirigida del dueño las cubre.
- Fallo PREVIO de master (no del bloque): `test_presupuesto_de_tamano_por_modulo` rojo por
  `app/ledger.py` en 1000 lineas desde ARREGLOS 03 D-H/D-I (2026-11-18; demostrado igual en
  `a18308f`; el repo no corre pytest en CI). La reparacion con allowlist vivia en el commit
  `ad767f0`, ajeno a este bloque: el dueño pidió NO incluirlo y sale del PR (queda reservado
  en la rama `ao/bloque-i-implementacion/suite-trozos`).

## 5. Exclusiones ambientales de Windows (documentadas, sin cambios en el repo)

- `tests/test_corrige_descripciones_pez.py`: importa `fcntl` (solo-Unix) y no puede
  coleccionarse en Windows; el `-k temporada01_i_` corre con `--ignore` de ese modulo
  (tool del servidor sin cambios desde 2026-11-04, `b509acb`). Ningun `temporada01_i_` vive
  ahi.
- 2 tests de `test_corrige_meli_skus.py` (`os.fchmod`) y 1 de
  `test_dossier_adversarial.py` (permisos 0o700 en directorio): POSIX-only, no forman parte
  de ningun Acepta de este bloque.

## 6. Salidas de los Acepta del bloque (todas en este directorio)

| comando | salida | resultado |
| --- | --- | --- |
| `pytest -q tests/test_despliegue_motor.py -k temporada01_i_` | `acepta-i1-despliegue.txt` | 1 passed |
| `pytest -q tests -k temporada01_i_` | `acepta-i-temporada01.txt` | 4 passed |
| `pytest -q tests/test_optimizer_goals.py tests/test_cycle_target_ciclo.py tests/test_cycle.py tests/test_lecturas_caso.py` | `acepta-i3-afines.txt` | 153 passed |
| `ruff check` (app, tests, tools) | `ruff-check.txt` | All checks passed |
| `ruff format` (archivos del bloque) | `ruff-format.txt` | formato aplicado |
| `pre-commit run --all-files` (gitleaks, conflictos) | `pre-commit.txt` / `pre-commit-exit.txt` | Passed, exit 0 |

## 7. Precondicion I.0

`git merge-base --is-ancestor origin/master HEAD` sale 0; `git cat-file -e
origin/master:plans/temporada-01.md` y `git cat-file -e
origin/master:docs/evidencia/temporada-01/DISENO.md` salen 0. Verificado con
`origin/master = a18308f2c44ca1adca5c709687a709f01bfa6f93`.

## 8. Numeros de migracion

`0092` libre en master al abrir el PR (master llega hasta `0091_arreglos03_sin_precio_cap`).
El candidato ya lo uso; sin colisiones.
