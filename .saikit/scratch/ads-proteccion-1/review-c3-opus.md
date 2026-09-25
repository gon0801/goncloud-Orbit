# Re-review independiente — PR 341 (ADS PROTECCION C.3), ronda 2

- **Objeto:** `origin/feat/ads-proteccion-c3` @ `8ba025b` (copia `/tmp/rv-c3`) vs `origin/master` @ `f31058d` (copia `/tmp/rv-c3-master`). Se ignora el brief de C.2b.
- **Ronda anterior:** @ `a7d036a`, NO APROBADO (B1/B2/B3 + 6 obs).
- **Fecha:** 2026-09-25
- **Método:**
  1. `diff -rq` / `diff -u`: cambian `app/{apply_cola,cycle}.py`, `app/optimizer/{bid,goals,replay}.py` y `tests/{test_apply_cola,test_cycle_pause_cooldown,test_optimizer_goals}.py`; hay un test nuevo, `tests/test_pause_economica.py`.
  2. Lectura del código tocado, de los tests y del spec `docs/superpowers/specs/2026-09-24-ads-proteccion-design.md` (§Datos y §Precedencia), más `plans/ads-proteccion-01.md` (C.2b, C.3).
  3. Tests focalizados contra Postgres local (los DB tests corrieron, ninguno en skip):
     `.venv/bin/python -m pytest -q tests/test_apply_cola.py tests/test_cycle_pause_cooldown.py tests/test_pause_economica.py tests/test_optimizer_goals.py` → **109 passed in 3.45s**
     `ruff check app tests` → **All checks passed!**; `ruff format --check app tests` → **198 files already formatted**
     Otros archivos que llaman el código cambiado (`test_apply_harvest, test_cycle, test_d3_adversary, test_fabrica_f2_*, test_harvest_destino, test_notifica, test_optimizer_bid, test_target_margen`) → **416 passed in 40.03s**
  4. Mutantes **en memoria**: el plugin `/tmp/rvc3_repro/mutplug.py` re-ejecuta el módulo con una sola sustitución y no toca disco. Driver: `/tmp/rvc3_repro/run_mutants.py`. La línea base (mutante no-op sobre los 4 módulos) dio 110 passed.
  5. Tests de repro fuera del objeto, en `/tmp/rvc3_repro/`. `/tmp/rv-c3` no se modificó: `find -newer` no devuelve ningún fuente; solo quedan caches y el `.claude/` que crean los hooks de la sesión.

## Bloqueantes

### B1' — B1 levantado solo en parte: nada prueba el aislamiento off-by-default en la revalidación
El código está bien en las tres piernas: decisión (`cycle.py:1527`), freeze (`cycle.py:807`) y revalidación (`apply_cola.py:787`). Pero la pierna de revalidación y el cableado del flag no tienen ninguna prueba que discrimine. En todos los tests de revalidación el flag se fuerza a `True` con monkeypatch (`test_pause_economica.py:128,211`) o viene prendido en la semilla (`test_apply_cola.py:1889,1954`). Ningún caso ejercita el flag apagado en `apply_cola`.

Mutantes que **sobreviven** a los 109 focalizados:

| Mutante | Resultado |
|---|---|
| `apply_cola.py:787` `if target is not None and _flag_pause_economica(conn)` → `if target is not None` | SOBREVIVE (109 passed) |
| `apply_cola.py:588` `_flag_pause_economica` → `return True` | SOBREVIVE (109 passed) |
| `cycle.py:1858` `pause_economica = g.pause_economica_desde_settings(settings)` → `pause_economica = True` | SOBREVIVE (109 passed) |

Efecto del primer mutante, que es justo lo que B1 busca impedir: con el flag **apagado**, una fila **legada** `pause_umbral` cuya hoja vendió después (orders>0) y aún cruza el límite económico **se aplica** (PAUSE en Amazon) en vez de descartarse `vendio_en_ventana`. Así, el deploy activa la PAUSE económica.

Reproducción (test discriminante que falta; pasa con el código real y mata al mutante):
```
PYTHONPATH=/tmp/rv-c3:/tmp/rv-c3/tests:/tmp/rvc3_repro .venv/bin/python -m pytest -q /tmp/rvc3_repro/test_repro_b1_revalida.py
  → 1 passed
MUT_MOD=app.apply_cola MUT_OLD="if target is not None and _flag_pause_economica(conn)" MUT_NEW="if target is not None" \
  ... -p mutplug /tmp/rvc3_repro/test_repro_b1_revalida.py
  → E  AssertionError: assert None == 'vendio_en_ventana'   (1 failed)
```
Por qué bloquea: el DoD de C.3 exige "aislamiento off por defecto probado antes de merge" (`plans/ads-proteccion-01.md:102`), y la regla 9 de AGENTS.md pide que la regresión se demuestre fallando. Hoy un cambio que reactive la PAUSE económica con el flag apagado pasa la suite completa en verde.
Arreglo mínimo:
- Agregar a `tests/test_pause_economica.py` el caso de `test_repro_b1_revalida.py`.
- Agregar un test de `_flag_pause_economica` con DB sin la clave y otro con la clave en `false`; ambos deben dar `False`.
- De preferencia, agregar un test que lea el flag desde `settings` en el ciclo (`_recorre_plataforma`/`corre_ciclo` sin la clave → decisión con la regla vieja y `economic_policy.version = None`).

## Observaciones (no bloqueantes; si no se corrigen, van a una fila del plan y se nombran en el PR)

1. **Obs3 NO levantada: la evidencia de revalidación todavía se pierde con `ApplyAbortado`.** `_registra_evidencia` escribe en `notes`, y esa escritura se commitea con la intención pre-HTTP (`apply_cola.py:1223`). Luego, en el camino de aborto, `_fase_apply` solo deja `{"apply_abortado_owner": True}` (`cycle.py:2086`) y `_corre_fases` sella `cuerpo` con `_sella_apply` (`cycle.py:2342,2380`, `notes = %s`), lo que **pisa** `notes` entero. Repro `/tmp/rvc3_repro/test_repro_obs3.py`:
   `ANTES del sello: [..., 'revalidaciones_economicas', ...]` → `DESPUES del sello: {'target': {...}, 'apply': {'apply_abortado_owner': True}}` → **1 failed**.
   Son falsos el docstring `apply_cola.py:676-677` ("misma suerte ante abortos; el cierre ... la reescribe idéntica") y el comentario `test_apply_cola.py:1926` ("sobrevive un ApplyAbortado"). Incluso en el camino de éxito la clave se mueve a `apply.revalidaciones_economicas`; no se reescribe "idéntica". El spec pide que "el ledger conserva los valores de decisión y de liberación". Arreglo: mezclar las `revalidaciones_economicas` ya persistidas antes del sello (o hacer merge jsonb en `_SQL_SELLA_APPLY`) y agregar un test que corra el aborto de verdad.
2. **Obs1 arreglada en código, pero sin test que la discrimine.** El mutante `califica = kind=="pause" and (not economica or motivo==pause_economica)` en `apply_cola.py:794` **SOBREVIVE** (109 passed). Repro: `/tmp/rvc3_repro/test_repro_obs1.py` (fila económica; hoja fresca con 0 pedidos, 300 clicks, cost 50, revenue 0 → solo califica por umbral → se espera `None`). Con el código real: 1 passed; con el mutante: 1 failed. Esto incumple la regla de hierro 2 (cada arreglo lleva su prueba).
3. **Nuevo: la evidencia registra la versión equivocada.** `apply_cola.py:804` fija `"version": economic_pause_v1` aunque, con el flag apagado, la revalidación corrió con `policy_version=None`. La auditoría dice v1 cuando la política aplicada fue la anterior. Debe registrar la versión efectiva.
4. **Nuevo e implícito: la PAUSE económica se dispara con `orders=None` o `clicks=None`.** `_decide(_corte('127.94','0',orders=None)).motivo == 'pause_economica'`, y `clicks=None` da lo mismo. El spec solo pide confiabilidad de cost/revenue/moneda/target, pero la PAUSE vieja abstenía con `pause_orders_desconocido`. El arreglo de B2 (`test_cycle_pause_cooldown.py:129-130`, `cost="79"`) conserva la intención del test y a la vez esconde este caso. Hay que decidirlo explícitamente y fijarlo con un test.
5. **Obs4 levantada, con un hueco.** La espera está implementada y probada (mutantes muertos, abajo). Pero el SQL de `_hay_goal` (`apply_cola.py:652-666`) no se prueba: los tests lo mockean y el mutante `return True` SOBREVIVE. Además, una fila en espera hace el LIST fresco (HTTP) en cada ciclo antes de llegar al chequeo de target; es solo costo, sin mutación.
6. **Obs2, nit:** la guarda `cortes.cost < 0` (`bid.py:267`) sobrevive como mutante. Con revenue ≥ 0, un costo negativo nunca cruza el límite, así que la guarda solo cambia el motivo (`dato_faltante` vs None), y el test solo afirma `.kind`. Si se quiere abstención auditable, hay que afirmar también `.motivo`.
7. **Nit:** `_flag_pause_economica` (`apply_cola.py:588-598`) atrapa `Exception` alrededor de un SELECT. En producción la conexión no es autocommit: si el SELECT falla, la TX queda abortada y el siguiente statement truena con `InFailedSqlTransaction`. El "fail-closed" es ilusorio, aunque la tabla siempre existe.
8. **Obs6, proceso (informativa):** C.2b sigue `cc:TODO; decision pendiente` (`plans/ads-proteccion-01.md:101`). El PR es draft. CI sobre `8ba025b` (`gh pr view 341`): `rapido` SUCCESS, `gate` SUCCESS, **`completa` SKIPPED**, `pesada` SKIPPED, así que la batería completa no ha corrido sobre este SHA.

## Lo verificado OK

- **B2 levantado:** 109/109 focalizados verdes con DB real y 416/416 en los archivos que llaman el código cambiado. El ajuste `cost="79"` en `test_cycle_pause_cooldown.py:129-130` es legítimo (ninguna de las dos reglas califica), con la salvedad de la obs 4.
- **B3 levantado:** el mutante `bid.py:313` `>` → `>=` **MUERE** en `test_regla_economica_us[120-200-None]`.
- **B1, piernas de decisión y freeze:** los mutantes `cycle.py:1527` y `cycle.py:807` (ignorar flag) **MUEREN** en `test_flag_economico_apagado_decide_regla_vieja_y_congela_none`. El mutante `goals.py` `is True` → `bool()` **MUERE** en `test_pause_economica_fail_closed`, que cubre `"true"` y `1`.
- **Obs2:** los mutantes goal disabled/off ignorado, peldaño margen → None y guarda `revenue<0` eliminada **MUEREN**.
- **Obs4:** los mutantes espera → descarte y `libera_vencidos` sin rama de espera **MUEREN**. La fila queda `released`, sin cobrar cuota ni mutar.
- **Obs5 levantada:** `"multiplicador"` sale de `MULT_PAUSE_ECONOMICA` en la regla (`bid.py:313`), en el freeze (`cycle.py`) y en la evidencia (`apply_cola.py`).
- **Precedencia umbral → económica:** el mutante que etiqueta la PAUSE vieja como económica **MUERE** en `test_4925_...`.
- **Persistencia inmediata en notes:** el mutante que no escribe **MUERE** en `test_pause_economica_venta_pasa_cola_veto_apply_y_readback`. Lo que falla es que sobreviva al sello (obs 1).
- **Replay:** `replay.py` pasa la versión congelada, y `test_2423_...` reproduce `pause` con `economic_policy` y no-pause sin ella.
- **Callers intactos:** `decide_bid(policy_version=None)` por defecto conserva el comportamiento para quien no lo pasa. `decision.inputs` es `JSONB NOT NULL` (`migrations/0001_initial.sql:871`), así que el nuevo `isinstance(dict)` no descarta filas legadas.
- Ruff check y format limpios.

VEREDICTO: NO APROBADO
