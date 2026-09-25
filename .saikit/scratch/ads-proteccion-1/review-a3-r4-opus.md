# Review A.3-R4: cierre del draft PR 340 @ 7accc9e (opus, independiente)

Revisor: `claude -p --model opus`, NO autor del cambio. Fecha: 2026-09-24.
Objeto doble: delta `5b8058b...7accc9e` + diff total
`f31058d...7accc9e`. Metodo: copia via `git archive` en /tmp, `.venv`
del repo, Postgres 16 local; repo intacto.

## (1) Delta: obs1 y obs2 de R3 cerradas

- obs1: `definiciones.md:56` cita los 2 nombres nuevos (existen en
  test_ads_salud.py:419 y :441); el nombre viejo ya no aparece.
- obs2: `len(textos) == 1` distingue bueno de malo. Mutante MA (quitar
  `entregar_pendientes` de comprobar_atraso): sobrevive en 5b8058b,
  muere en 7accc9e justo en el assert. Segundo envio tambien romperia
  (cola `[True]`): el test fija exactamente un envio. MB (apagar
  entrega) mata 4 tests.

## (2) Total: nada nuevo que bloquee

- Tests focalizados: 135 passed, 0 skipped (PG real). 1 rojo solo en
  /tmp sin .git (`test_uv_lock...`, conocido de R1; en CI pasa).
- Ruff check + format limpios (199 archivos).
- Migraciones: 0040 en master, 0041 nueva con DO $$ (cierra obs R1);
  sin choque (342 usa 0042); NULLS NOT DISTINCT OK en PG 16.
  `app/` y `migrations/` identicos entre 6aed71b y 7accc9e.
- Merge: CLEAN + merge-tree limpio sobre base f31058d actual.
- DoD: estados/cadencia/recovery/agotado/heartbeat definidos y
  coincidentes con tests; frase imposible fuera; M6 en fila A.3.
- Extras: `intentar_procesar_run` sin autocommit no pierde/duplica;
  `_ads_ingest_de` degrada a None sin 0041.
- CI 7accc9e: rapido/gate/CodeRabbit verde; completa/pesada skipping.
  Ultima bateria completa: 6aed71b (dispatch 36086066116).

## Observaciones (no bloqueantes)

1. Bateria completa corrio en 6aed71b, no en 7accc9e (cambio posterior:
   docs + 1 assert). Antes de ready: dispatch Quality sobre 7accc9e o
   dejar escrito que se reutiliza evidencia de 6aed71b y por que.
2. Body del PR dice "SHA final 6aed71b" y solo menciona R2: actualizar
   a 7accc9e + R3/R4.
3. M3 abierto desde R1 (cancel global inalcanzable, mutante sobrevive):
   anotarlo como equivalente o fila en plan como M6 (1 linea en A.3).

VEREDICTO: APROBADO CON OBSERVACIONES
