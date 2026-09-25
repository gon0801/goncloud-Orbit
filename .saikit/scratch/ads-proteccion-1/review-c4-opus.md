# Re-review C.4 — draft PR 342 @ d778b8b (opus, independiente)

Revisor: claude opus, NO autor del cambio. Fecha: 2026-09-25.
Objeto: `origin/feat/ads-proteccion-c4` @ `d778b8b` (copia /tmp/rv-c4) vs
`origin/master` `f31058d` (copia /tmp/rv-c3-master). Diff propio de la rama:
merge-base `f98b30ab`..`d778b8b` (3 commits; el fix es `6f6838b..d778b8b`,
14 archivos, +1103/-76). Método: `diff -rq` de copias + `git diff` en el repo;
lectura de propuestas_campana/notifica/cycle/api/api_dashboard/ui/cortes.html/
0042 y tests; pytest con el `.venv` del repo contra Postgres local
127.0.0.1:5432; repros y mutantes FUERA del objeto (/tmp/rv-c4-repro,
copia /tmp/rv-c4-mut restaurada tras cada mutante; /tmp/rv-c4 intacto).

## Bloqueantes

**BN1 (nuevo, lo introduce el fix de B1) — Con el flag prendido, en producción
se pierden las marcas de aviso Y el sello del ciclo.**
`envia_avisos_pendientes` corre tras `_fase_apply`, que dejó la conexión IDLE
(cycle.py:2028). Su SELECT (propuestas_campana.py:594) abre una tx implícita.
La conexión de producción no es autocommit (`app.db.connect`; cli.py:102-108
cierra sin commit), así que cada `with conn.transaction()` posterior es un
SAVEPOINT que nunca commitea: la marca (propuestas_campana.py:633) y
`_sella_apply` (cycle.py:2359). Es exactamente la trampa ADV-01 que documenta
el docstring de `_fase_apply`. Efectos medidos con conexión como la de
producción, más `close()` y lectura desde otra conexión:
- (a) El aviso se reenvía en cada ciclo: 2 ciclos → **2 Telegram de la misma
  propuesta**; la fila queda `('open','pending',0)`. Rompe el contrato
  "sent sin duplicar".
- (b) `notes['apply']` y `notes['telegram']` de ambos envelopes se pierden,
  así que el fallo del canal nunca llega a /salud.
- (c) **Regresión ADV-01c**: un ciclo live con el apply abortado persiste
  `done` en vez de `degraded`.

Control: con el flag apagado, el sello y `degraded` sí persisten. Las pruebas
no lo detectan: usan conexión autocommit (tests/test_cycle.py:138) y
test_propuestas_campana.py:299 lee `sent` con la misma sesión. Es una prueba
que no discrimina: la suite pasa con el defecto.

Repro:
`PYTHONPATH=/tmp/rv-c4:/tmp/rv-c4/tests .venv/bin/python -m pytest -q -s -p no:cacheprovider --rootdir /tmp/rv-c4-repro /tmp/rv-c4-repro/test_repro_c4.py`
```
FILAS [('open', 'pending', 0)]
AVISOS_PROPUESTA_ENVIADOS 2
ENVELOPE 1 done False False []        # notes sin 'apply' ni 'telegram'
ENVELOPE 2 done False False []
CONTROL_ENVELOPE done True            # flag off: sello persiste
ADV01C flag=on memoria degraded persistido done
ADV01C flag=off memoria degraded persistido degraded
2 failed, 2 passed
```
Arreglo esperado: que el SELECT entre desde IDLE dentro de su propio
`with conn.transaction()` (o hacer `conn.commit()` tras el fetch), y que cada
marca y el sello posterior commiteen de verdad. Agregar una prueba con
`conectar(autocommit=False)` + `close()` + lectura desde otra conexión
(patrón de test_cycle_apply.py:814/852) con el flag prendido:
2 ciclos → 1 aviso, fila `sent`, `notes['apply']` persistido y `degraded`
persistido en el aborto live.

## Estado de los bloqueantes originales

- **B1 — NO levantado.** Están las columnas de entrega en 0042 (enmienda), el
  envío en el ciclo, el bloque `avisos_propuesta` en `/api/dashboard/salud` y
  los tests de aviso/reintento (M1/M2 mueren), pero en producción el contrato
  se rompe (BN1).
- **B2 — Levantado.** La fila `paused_observed` es visible y no accionable
  (M4 muere). La sección Campanas está en `/cortes` = `/propuestas`
  (ui.py:537). El JSON trae `motivo`, `sales30d`, `close_reason` y
  `profile_id`, probado con PG real (M11 y M13 mueren).
- **B3 — Levantado.** Flag fail-closed (goals.py:422) en lectura
  (cycle.py:1789), guarda (:2274) y avisos (:2305). `test_flag_apagado` corre
  el ciclo real: M6+M7 juntos mueren (cada uno solo sobrevive porque la otra
  puerta lo tapa).

## Observaciones (no bloquean; varias son de una línea y caben en la ronda de BN1)

1. Pruebas faltantes (mutantes que sobreviven): M8 (puerta de avisos siempre
   prendida; importa en el rollback on→off), M10 (avisar también
   `paused_observed`: A1U/AU2 recibirían "pausar a mano", y el DoD dice que no)
   y M12 (conteo de /salud sin filtro `open`).
2. /salud (pantalla): `avisos_propuesta.pendientes` solo aparece en el JSON;
   salud.html:29-33 solo pinta la línea genérica de telegram del último ciclo.
3. `paused_observed` nace con `aviso_estado='pending'` y nunca se envía; la
   pantalla muestra "pending" en la columna Aviso. El motivo visible de
   A1U/AU2 es `campana_no_enabled`; la razón económica solo está en
   `evidence.riesgo_ignorando_estado`.
4. Cruce con C.5 (obs5 original, parcial): C.4 cierra open→`paused_observed`
   ante cualquier PAUSED del sync (propuestas_campana.py:122-124),
   adelantándose al cierre por readback de C.5 (`paused_external`). Además,
   `paused_*` + ENABLED con riesgo reabre sin reset (:138-139), con una ventana
   de 30d que aún contiene el gasto previo a la pausa: al reactivar llega un
   aviso nuevo sin gasto nuevo. Es decisión de diseño para C.5 o el dueño.
5. `profile_id` (obs6 original, parcial): la columna y la clave de evidencia
   existen, pero siempre valen NULL. Tras el merge, 0040 `ads_report_result`
   de master ya guarda `profile_id` por plataforma.
6. El PR está `CONFLICTING` con master: goals.py tiene 2 conflictos aditivos
   (B.2a vs C.4, hay que conservar ambos); cycle.py mezcla limpio
   (`git merge-file`). Hay que re-revisar sobre el SHA mergeado (runbook,
   atorón #3).
7. CI (obs8 original, sigue): sobre d778b8b solo corrió CodeRabbit, que se
   saltó por draft.
8. En el DO de 0042, `has_table_privilege('app_read',…,'UPDATE')` no ve grants
   por columna (el test con SET ROLE sí lo cubre).

## Observaciones originales: estado

1. TX3 REPEATABLE READ: levantada (se quitó; cycle.py:2266 queda en READ COMMITTED).
2. Endpoint con PG real: levantada.
3. Cascada: levantada (usa `cascada_target_acos_con_procedencia`, :539; tiene test).
4. `acos_pct` redondeado a 2 decimales: levantada (M9 muere).
5. Parcial (reactivada y ARCHIVED probadas, M5 muere; lo que sigue abierto está en la obs 4).
6. Parcial (obs 5).
7. DO de candado: levantada.
8. CI: sigue (obs 7).

## Lo verificado OK

Comandos corridos (salidas resumidas):
- `pytest -q tests/test_propuestas_campana.py tests/test_api_dashboard.py tests/test_api.py tests/test_notifica.py tests/test_ui.py`
  → `1 failed, 207 passed`. La única falla es
  `test_uv_lock_commiteado_y_jinja2_pinneada` (`git ls-files` sin .git en la
  copia); también falla en la copia de master, así que es del entorno.
- `pytest -q tests/test_cycle.py tests/test_cycle_apply.py` → `60 passed`.
- `ruff check` → `All checks passed!`. `ruff format --check` marca 2 archivos
  `plans/*.md` que ya estaban en master; ninguno es de la rama.
- Mutantes: M1, M2, M4, M5, M9, M11, M13 y M6+M7 mueren; M6, M7 (cada uno
  solo), M8, M10 y M12 sobreviven (obs 1).
- `gh pr view 342` → draft, head d778b8b, `CONFLICTING`.

El texto del aviso es plano (sin parse_mode), exige `ok=true` y dice "pausar a
mano" (no sugiere pausa automática). Cero HTTP a Amazon y cero `apply_queue`
en el código nuevo. Los CHECKs de 0042 están cubiertos por `evalua` antes de
persistir. La conexión de lectura es autocommit, así que el `except` de
/salud y /cortes no envenena la tx. `corre_ciclo` corre por plataforma
(contadores no compartidos). A.3 tampoco pone tope a los reintentos
(`pending` = `sent_at NULL`), así que "reintento acotado" no bloquea.

VEREDICTO: NO APROBADO
