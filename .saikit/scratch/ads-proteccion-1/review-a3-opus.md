# Cross-review A.3 — draft 340 @ 2872567 (opus, independiente)

Revisor: `claude -p --model opus`, NO autor del cambio. Fecha: 2026-09-24.
Objeto: `origin/feat/ads-proteccion-a3` @ `2872567` contra `origin/master`
`f40270d` (trae #333, #335, #339). Metodo del revisor: copia via `git archive`
en /tmp, `.venv` del repo, Postgres local; no toco el checkout.
Nota: al revisar, master ya iba en `f31058d` (#334 y #338 encima);
`git merge-tree` contra ese master sale limpio.

## (a) Simetria cancelar-antes-de-abrir en comprobar_atraso

- Scoped: OK (`salud.py:177-179`, igual que `procesar_run:136-138`).
- Global: presente (`salud.py:180-182`) pero inalcanzable en la secuencia
  A.3d (con un `written` en `ads_report_result` append-only, `ultimos` nunca
  vuelve a vacio). El mutante que la borra sobrevive. No es bug; va a
  observaciones.

## (b) Tests rojo->verde: SI discriminan

6 tests en verde con la rama. Mutantes: M1 (borrar cancel scoped) mata el
test de atraso; M2 (borrar cancel en procesar_run) mata fallo + episodio;
M4 (tipo equivocado) mata atraso; M3 (borrar cancel global) sobrevive
(camino inalcanzable, ver (a)). Asserts sobre episodio viejo
(`recovery_cancelled_at`, `closed_at`), conteo y texto del aviso nuevo.

## (c) DoD H4.2/A.3: incompleto (ver bloqueante 2)

Estados terminal/superseded y cadencia implicitos en codigo/0041 pero no
definidos por escrito (ni PR, ni DEPLOY.md, ni plan). Falta definir: recovery
que agota sus 6 intentos queda abierta sin limite hasta reemplazo.
Chequeo 10:30 no observable: `main()` calla en exito, sin heartbeat.

## (d) Merge de master: OK salvo 1 rojo

Sin duplicados, ruff limpio, sin ciclo de imports (`salud` no importa
`reports`), migraciones 0040 (master) + 0041 (nueva) coherentes, B.2a verde.
Pero 397 passed + 2 failed en 9 archivos vecinos: 1 es artefacto de /tmp
(sin .git); el otro es real: `test_precio_pantalla_salud_agrega_
precios_sin_cambiar_claves` (`Extra items in the left set: 'ads_ingest'`).
CI del draft no lo vio (completa/pesada skipping).

## Bloqueantes

1. Bateria completa roja: `tests/test_precio_pantalla.py::
   test_precio_pantalla_salud_agrega_precios_sin_cambiar_claves` por la clave
   nueva `ads_ingest` en /salud. Repro: `git archive
   origin/feat/ads-proteccion-a3 | tar -x -C <tmp>` + pytest del test.
   Fix: agregar `"ads_ingest"` al conjunto esperado.
2. Falta DoD explicito A.3/H4.2: ejecucion observable del chequeo 10:30
   (stdout o heartbeat persistido + test) y definicion escrita de estados
   (terminal = `closed_at`, superseded = `recovery_cancelled_at`), cadencia
   (1 episodio por scope/tipo, <=6 intentos) y final de recovery agotada.

## Observaciones (no bloqueantes)

1. Cancelacion global inalcanzable: documentar mutante equivalente o quitar
   con comentario.
2. `A.3d/caso.md` dice "25-sep-2026" pero hoy es 24-sep-2026: corregir fecha.
3. Body del PR desactualizado ("#333 no mergeado", "A.3d no decidido").
4. La 0041 no trae el bloque `DO $$` de verificacion de privilegios de 0040.
5. El cron `*/10 10-12` dispara 10:00/10:10/10:20 sin rastro (liga con
   bloqueante 2).

VEREDICTO: NO APROBADO
