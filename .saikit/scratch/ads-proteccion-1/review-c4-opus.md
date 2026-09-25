# Re-review C.4, ronda 3: draft PR 342 @ eaf45fb (opus, independiente)

Revisor: claude opus, NO autor del cambio. Fecha: 2026-09-25.
Objeto: `origin/feat/ads-proteccion-c4` @ `eaf45fb` (copia /tmp/rv-c4) vs
`origin/master` `f31058d` (copia /tmp/rv-c3-master). Fix de esta ronda:
`d778b8b..eaf45fb` (9 archivos; código: propuestas_campana.py, api.py,
cortes.html, salud.html, 0042; más tests).
Método: `diff -rq` de las copias; `git diff d778b8b eaf45fb` en el repo
(solo lectura); `cmp` de los 20 archivos de la rama contra `eaf45fb`
(idénticos); lectura del código y los tests nuevos; pytest con el `.venv`
del repo contra Postgres local 127.0.0.1:5432; repros en /tmp/rv-c4-repro
(ronda 2) y /tmp/rv-c4-repro3 (nuevos); mutantes sobre la copia
/tmp/rv-c4-mut3, restaurada tras cada uno (`diff -rq` final = igual a
/tmp/rv-c4); `gh` en solo lectura para el PR y el CI.

## Bloqueantes

**BN2 — La batería completa está roja sobre `eaf45fb` (CI por dispatch).**
Corrida 36094417038 (Quality, workflow_dispatch, eaf45fb):
`rapido=success`, `pesada=success`, `completa=failure`
(`2 failed, 3319 passed`) y `gate=failure` ("algun job aplicable NO quedo
en success"). Se reproduce en local:
`pytest -q tests/test_precio_pantalla.py::test_precio_pantalla_salud_agrega_precios_sin_cambiar_claves tests/test_ui_copy_campana.py`
→ `2 failed, 1 passed`.
- (a) tests/test_precio_pantalla.py:806 compara el conjunto EXACTO de claves
  de /salud. La rama agrega `avisos_propuesta` ("Extra items in the left set:
  'avisos_propuesta'"). El cambio es legítimo: basta sumar la clave a
  `_CLAVES_SALUD_PREVIAS` (:544).
- (b) tests/test_ui_copy_campana.py:29, candado del repo (#178, "campaña con
  ñ en la copia visible"): cortes.html:100 `Campanas`, :101 `no toca
  campanas` y :106 `<th>Campana</th>`. El arreglo es `Campañas`/`campañas`/
  `Campaña`.

Ambas fallas entraron en `d778b8b` (`git show d778b8b:app/templates/cortes.html
| grep -c Campana` → 2; `avisos_propuesta` en api_dashboard → 3) y siguen en
`eaf45fb`. En la ronda 2 no las vi: el CI no corrió y yo solo corrí la suite
focalizada. Es omisión mía. Aun así bloquean: un candado roto sobre el SHA
final choca con AGENTS.md ("PR a master con CI verde por fase"; regla de
hierro 1). El arreglo es de 1 línea en el test y 3 palabras en la plantilla.
La ronda siguiente solo necesita ese diff y el `gate` en verde. No es el
mismo bloqueante que en la ronda 2 (BN1 quedó levantado), así que el ciclo
sigue normal.

## Estado de lo que había que levantar

- **BN1: levantado.** El SELECT va en su propia tx
  (propuestas_campana.py:617-618). Llega desde IDLE: `_fase_apply` hace
  commit o rollback (cycle.py:2028 y los `except`), y `tick()` late por una
  conexión aparte en autocommit (cycle.py:961 y :973-981), así que no reabre
  la tx. Cada marca (:657-658) y el sello (cycle.py:2359) commitean de
  verdad.
  - Mi repro de la ronda 2 (desde un cwd neutro), sin cambios:
    `FILAS [('open','sent',1)]`, `AVISOS_PROPUESTA_ENVIADOS 1`,
    `ENVELOPE 1/2 done True` (notes['apply'] persiste), `CONTROL_ENVELOPE
    done True`, `ADV01C flag=on … persistido degraded`, `flag=off …
    degraded` → `4 passed`.
  - Repro nuevo del camino de fallo, con conexión de prod: canal 500 →
    `('open','pending',1)` persistido + nota `telegram.aviso_propuesta`
    persistida. Luego 2 ciclos con el canal OK → `('open','sent',2)`,
    1 aviso y 0 sesiones `idle in transaction`. Live abortado + flag +
    canal caído → `degraded` persistido. Resultado: `2 passed`.
  - La prueba del autor discrimina: el mutante MBN1 (quitar la tx del
    SELECT) muere en `test_bn1_aviso_y_sello_persisten_con_conexion_de_produccion`
    (test_propuestas_campana.py:690). La de ADV-01c (:761) cubre el flag
    prendido y apagado.
- **Obs1: levantada.** M8 muere (`test_m8_apagar_flag_no_envia_pendientes`,
  :829), M10 muere (`test_m10_envio_ignora_paused_observed`, :858) y M12
  muere (`test_salud_muestra_avisos_propuesta_pendientes_y_fallo`, ahora con
  una fila paused_observed pending).
- **Obs2: levantada.** salud.html:41 pinta los pendientes y el fallo; lo
  prueba `test_ui_salud_muestra_avisos_propuesta_pendientes_y_fallo`
  (MS1 y MS2 mueren).
- **Obs3: levantada.** api.py:363-367 expone `riesgo_ignorando_estado`;
  cortes.html pinta el chip de riesgo y "—" en el aviso de las filas que no
  son open. MA1, MU1 y MU2 mueren (MU3 sobrevive, ver obs 2).
- **Obs4: juicio abajo (obs 1).** No bloquea; decide el operador.
- **Obs5: levantada.** Es un comentario en 0042 y en el código;
  `profile_id` sigue NULL hasta C.5, como está diseñado.
- **Obs6: informativa, sigue.** `git merge-file` de 14 archivos de
  master+rama: goals.py con 2 conflictos; los otros 13, limpios. Hay que
  re-revisar sobre el SHA mergeado.
- **Obs7: el CI ya corre (por dispatch), pero en rojo** → BN2.
- **Obs8: levantada a medias** (obs 3).

## Observaciones (no bloquean)

1. **Obs4 (juicio de la decisión del autor).** El gate
   (propuestas_campana.py:362-367) compara contra el ÚLTIMO snapshot
   pausado, no contra el momento de la pausa: `actualizar_pausado` refresca
   `window_end` y `observed_at` en cada ciclo PAUSED. Con la ingesta diaria
   (el vintage avanza), solo frena la reapertura si no hubo ingesta entre el
   último ciclo pausado y el primero ENABLED.
   Repro (`/tmp/rv-c4-repro3/test_repro_obs4.py`): pausa, luego PAUSED con
   vintage+1, luego ENABLED con vintage+1, siempre con el mismo 200/0 →
   `[('paused_observed',200,0,False,'pending'), ('open',200,0,False,'pending')]`.
   Resultado: reabre y avisa sin gasto nuevo y sin reset. La prueba del
   autor "con evidencia nueva" (:475) también reabre con el mismo 200/0, y
   el comentario de :361 dice "(gasto nuevo, episodio nuevo)", lo cual es
   inexacto.
   El spec (docs/superpowers/specs/2026-09-24-ads-proteccion-design.md:88-91)
   dice: "un episodio nuevo solo abre despues de una ventana observada sin
   riesgo". Tanto d778b8b como eaf45fb reabren `paused_*` sin `reset_at`
   (:138).
   Juicio: la decisión resuelve "cron repetido" (los mismos datos no
   reabren; MG1 muere) pero no "aviso sin gasto nuevo". No es regresión
   (es estrictamente más estrecha que la ronda 2), no muta nada y el flag
   viene apagado, así que no bloquea. Declaro que mi obs5 de la ronda 1
   ("reactivada con riesgo no se repropone") empujó hacia reabrir.
   Pide decisión del operador, con fila en el plan:
   - (a) spec literal: `paused_*` exige `reset_at`, igual que
     resolved/dismissed. La reactivada con riesgo queda visible con chip y
     sin aviso.
   - (b) reactivación = episodio nuevo, y se corrige el spec.
   De una línea: corregir el comentario de :361 y el docstring del test.
   MG4 y MG5 (gate por un solo campo) sobreviven; solo importa si se queda
   con (b).
2. MU3 sobrevive (chip de riesgo pintado siempre): el test solo verifica
   que `riesgo</span>` aparezca. De una línea: `html.count("riesgo</span>") == 1`.
3. Obs8: el DO (0042:109-110) usa `has_column_privilege(...,'status','UPDATE')`,
   que solo ve la columna `status`. Verificado en tx con rollback:
   `GRANT UPDATE (aviso_estado) … TO app_read` → `has_column_privilege(status)
   False` y `has_any_column_privilege True`. De una línea: usar
   `has_any_column_privilege`. MD1 (DO que nunca dispara) sobrevive, como es
   normal en un assert de migración; el test con SET ROLE cubre el caso real.
4. MG6 (el gate sin `paused_external`) sobrevive: ningún test cubre
   `paused_external` con la misma tupla. Riesgo bajo porque C.5 todavía no
   escribe esas filas.
5. Higiene del revisor: un `python -` mío sin `PYTHONDONTWRITEBYTECODE`
   dejó 40 `.pyc` (21:38) en `/tmp/rv-c4/**/__pycache__`. Las fuentes
   están intactas (`cmp` de los 20 archivos contra eaf45fb = iguales). El
   harness bloqueó el borrado; se pueden borrar sin riesgo.

## Lo verificado OK

- Suite pedida: `pytest -q tests/test_propuestas_campana.py tests/test_api_dashboard.py tests/test_api.py tests/test_notifica.py tests/test_ui.py tests/test_cycle.py tests/test_cycle_apply.py`
  → `1 failed, 273 passed`. La única falla es
  `test_uv_lock_commiteado_y_jinja2_pinneada` (`git ls-files` sin .git en la
  copia; es del entorno y también fallaba en la ronda 2).
- `ruff check` → `All checks passed!`. `ruff format --check` → 2 archivos,
  `plans/brief-sp-api-01-a5-ronda-review.md` y `plans/fabrica-01.md`, igual
  que en master (no son de la rama).
- Mutantes (`/tmp/rv-c4-r3-mutantes.log`):
  - Mueren: M1, M2, M4, M5, M6+M7, M8, M9, M10, M11, M12, M13, MBN1,
    MG1 (sin gate), MG2 (gate invertido), MG3 (mantener en vez de
    actualizar_pausado), MU1, MU2, MS1, MS2 y MA1.
  - Sobreviven: MG4, MG5, MG6, MU3 y MD1 (obs 1, 2, 3 y 4).
  - MU3, MS1 y MS2 los re-corrí con `--deselect` del test de entorno,
    porque con `-x` "morían" por él.
- `gh pr view 342` → draft, head `eaf45fb`; el único check en el PR es
  CodeRabbit. El CI completo solo existe como dispatch 36094417038 (BN2).

VEREDICTO: NO APROBADO
