# Re-review independiente — PR 341 (ADS PROTECCION C.3), ronda 3

- **Objeto:** `origin/feat/ads-proteccion-c3` @ `f83897e` (copia `/tmp/rv-c3`) vs `origin/master` @ `f31058d` (copia `/tmp/rv-c3-master`). Se ignora el brief de C.2b.
- **Ronda anterior:** @ `8ba025b`, NO APROBADO (B1' + 8 obs). Respaldo del reporte R2: `/tmp/rvc3_repro_r3/veredicto-r2-backup.md`.
- **Fecha:** 2026-09-25
- **Método:**
  1. `diff -rq` / `diff -u`: cambian `app/{apply_cola,cycle}.py`, `app/optimizer/{bid,goals,replay}.py` y `tests/{test_apply_cola,test_cycle,test_cycle_apply,test_cycle_pause_cooldown,test_optimizer_goals}.py`; hay un test nuevo, `tests/test_pause_economica.py`.
  2. Lectura del código tocado (merge pre-sello, flag, gate orders/clicks, evidencia, `_hay_goal`) y de los tests nuevos.
  3. Tests focalizados contra Postgres local 127.0.0.1:5432 (DSN default, 0 skips):
     `.venv/bin/python -m pytest -q tests/test_apply_cola.py tests/test_cycle_pause_cooldown.py tests/test_pause_economica.py tests/test_optimizer_goals.py tests/test_cycle_apply.py tests/test_cycle.py` → **177 passed in 9.11s**
     `ruff check app tests` → **All checks passed!**; `ruff format --check app tests` → **198 files already formatted**
  4. Mutantes **en memoria**: el plugin `/tmp/rvc3_repro/mutplug.py` re-ejecuta el módulo con una sola sustitución y no toca disco. Driver: `/tmp/rvc3_repro_r3/run_mutants_r3.py`, suite = los 6 archivos, `-x`. La línea base (no-op por módulo en `apply_cola`, `cycle` y `bid`) dio **177 passed** en los tres.
  5. Test de repro fuera del objeto: `/tmp/rvc3_repro_r3/test_repro_r3_exito.py`. `/tmp/rv-c3` no se modificó: `find . -type f -newer <inicio>` sin caches → vacío.

## Bloqueantes

Ninguno.

## Observaciones (no bloqueantes; si no se corrigen, van a una fila del plan y se nombran en el PR)

1. **Nuevo en R3: el camino de ÉXITO del merge pre-sello no tiene test, y el dedupe sobrevive como mutante.** Pasan por `_mezcla_evidencias_persistidas` (`app/cycle.py:1152-1184`) todos los ciclos live con evidencia económica. En éxito, `destino` es la **tupla** `res_cola.revalidaciones_economicas` (`cycle.py:2106`), así que no se truena solo porque el dedupe (`cycle.py:1182`) descarta todo lo persistido. El único test del merge es el de aborto, donde `destino` es una lista nueva, y ese test no puede discriminar esto.
   - Mutante `cycle.py:1182` `... and evidencia.get("decision_id") not in vistos:` → `if isinstance(evidencia, dict):` **SOBREVIVE** (177 passed).
   - Repro del camino de éxito: el mismo escenario que `test_evidencia_economica_sobrevive_aborto_despues_de_liberar`, pero sin perder el lease.
     ```
     PYTHONPATH=/tmp/rv-c3:/tmp/rv-c3/tests:/tmp/rvc3_repro .venv/bin/python -m pytest -q -s /tmp/rvc3_repro_r3/test_repro_r3_exito.py
       → STATUS done; evidencia 1 vez en apply.*, sin top-level → 1 passed
     MUT_MOD=app.cycle MUT_OLD='if isinstance(evidencia, dict) and evidencia.get("decision_id") not in vistos:' \
     MUT_NEW='if isinstance(evidencia, dict):' ... -p mutplug /tmp/rvc3_repro_r3/test_repro_r3_exito.py
       → E AttributeError: 'tuple' object has no attribute 'append' (app/cycle.py:1183) → 1 failed
     ```
   - El código actual es correcto. Además, hoy lo persistido ⊆ lo que está en memoria, porque `_registra_evidencia` solo se llama desde `libera_vencidos` con lista. No bloquea. Pero si esto regresiona, el ciclo truena **después** de mutar en Amazon, y la suite sigue en verde.
   - Arreglo: una línea, `notas["revalidaciones_economicas"] = list(res_cola.revalidaciones_economicas)` en `cycle.py:2106`, más adoptar el repro como test de éxito (sin duplicado, sin clave top-level).
2. **Nit:** en `tests/test_cycle_pause_cooldown.py:130-132`, el comentario "Obs4r2 ... 127.94" está entre el caso `cost="79"` y el caso `orders=None`, así que se lee como si hablara del primero. En realidad describe el segundo (`:133`).
3. **Informativa:** con el flag apagado, una fila **económica** ya encolada que no califica por umbral se descarta `ya_no_califica` (`apply_cola.py:802-821`). Funciona como kill switch coherente con "flag off = camino pre-C.3", y la evidencia registra `version: None`. Conviene dejarlo dicho en el runbook de rollback.
4. **Obs8 (proceso), según el reporte del autor y `gh`:** el PR sigue **draft**. El cuerpo dice "pasa a ready solo con C.2b aceptado ... y merges #334/#335". Corridas sobre `f83897e`: `pull_request` 36094121286 → rapido SUCCESS, gate SUCCESS, completa/pesada SKIPPED. `workflow_dispatch` 36094414829 → rapido SUCCESS, pesada SUCCESS, **completa SUCCESS** (04:26→04:33Z), gate SUCCESS; la batería completa corrió en verde sobre este SHA.

## Lo verificado OK

Mutantes (driver `/tmp/rvc3_repro_r3/run_mutants_r3.py`):

| Ítem | Mutante | Resultado / test que lo mata |
|---|---|---|
| B1' | `apply_cola.py:782` `if target is not None and _flag_pause_economica(conn)` → `if target is not None` | **MUERTO** · `test_pause_economica.py:399` `test_flag_apagado_revalidacion_legada_no_adopta_regla_economica` |
| B1' | `apply_cola.py:588-601` `_flag_pause_economica` → `return True` | **MUERTO** · `test_apply_cola.py:2054` `test_flag_pausa_economica_fail_closed_en_db` (PG real) |
| B1' | `cycle.py:1896` `pause_economica = ...settings` → `= True` | **MUERTO** · `test_cycle.py:2817` `test_ciclo_lee_flag_economico_de_settings` (PG real) |
| Obs1 | `cycle.py:2417` sin la llamada al merge | **MUERTO** · `test_cycle_apply.py:925` (aborto REAL: PUT PAUSED salió, lease perdido en el LIST de readback, `degraded`, `apply_abortado_owner`, evidencia `califica`) |
| Obs1 | merge lee `apply.revalidaciones_economicas` en vez del top-level | **MUERTO** · `test_cycle_apply.py:925` |
| Obs2 | `califica` exige motivo económico en fila económica | **MUERTO** · `test_pause_economica.py:438` |
| Obs3 | evidencia `"version": version_efectiva` → versión nominal v1 | **MUERTO** · `test_pause_economica.py:483` |
| Obs4 | `bid.py:317` sin `cortes.orders is not None` | **MUERTO** · `test_cycle_pause_cooldown.py:133` (`orders=None`, cost 127.94 → ya discrimina) |
| Obs4 | `bid.py:318` sin `cortes.clicks is not None` | **MUERTO** · `test_pause_economica.py:526` |
| Obs5 | `apply_cola.py:669` `_hay_goal` → `return True` | **MUERTO** · `test_apply_cola.py:2083` (PG real: plataforma, disabled/off, sin goal, sin estado) |
| Obs6 | `bid.py:267` sin guarda `cost < 0` | **MUERTO** · `test_pause_economica.py:258` (pinea `pause_economica_dato_faltante`) |
| Obs6 | sin guarda `ad_revenue < 0` | **MUERTO** · `test_pause_economica.py:258` |
| R3 | merge sin dedupe | **SOBREVIVE** → obs 1 |

- **Obs7 levantada:** `_flag_pause_economica` (`apply_cola.py:588-601`) ya no tiene `try/except`. Hace fail-closed solo en datos (sin fila, o settings no-dict → False) y un SELECT roto falla ruidoso.
- **Obs4, caso 79:** `cost="79"` quedó solo en el caso `clicks=156` (`test_cycle_pause_cooldown.py:129`). Es legítimo, porque con 127.94 y revenue 0 la regla económica sí calificaría. El caso `orders=None` usa 127.94, y la abstención es lo único que impide la PAUSE (lo prueba el mutante muerto).
- **Transacciones del merge:** el SELECT va en su propia `conn.transaction()`, y la conexión está IDLE tras el `commit()`/`rollback()` de `_fase_apply` (`cycle.py:2121-2128`). El sello conserva la guarda de status (`_SQL_SELLA_APPLY`, `cycle.py:353-358`), así que no pisa el rastro del sucesor. La evidencia de un descarte que luego hace rollback por aborto se va junto con el descarte: coherente, la misma TX.
- Ruff check y format limpios. 177/177 focalizados con DB real.

VEREDICTO: APROBADO
