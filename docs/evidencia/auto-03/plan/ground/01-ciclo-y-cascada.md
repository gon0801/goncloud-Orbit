# Ground 1: ciclo y cascada de target (explorador, master 5adf1be0, 2026-11-04)

## Overview
- Hay tres transacciones con nombre: TX1 (claim + envelope), TX2 (`_fase_lecturas`, REPEATABLE READ, sin escrituras, recorre hojas y ad groups) y TX3 (`_corre_fases`: INSERT de decisiones + targets + cierre). Además hay una TX4 de apply.
- **El motor NO llama a `cascada_target_acos_con_procedencia`.** Llama dos veces por hoja/grupo: `g.cascada_target_acos(...)` para el valor y `g.peldano_target_acos(...)` para el nombre, ambas con firmas posicionales. La variante con procedencia la usan solo el dashboard /settings, la revalidación de pausas en `apply_cola` y `propuestas_campana`.
- La cascada tiene cinco peldaños: goal → margen_familia → margen_plataforma → setting. El CHECK de `target_acos_ciclo.procedencia` en la DB repite esa misma lista, así que un peldaño nuevo exige migración.
- El "sube de una vez / baja 0.5" NO vive en `cache_acos_target`. El ancla de plataforma sale de `optimizer_cycle.notes.target` y la de familia de `target_acos_ciclo` (por hoja). `cache_acos_target` es un argumento muerto que se conserva solo por compatibilidad posicional.
- El ciclo no menciona el impulso. Sus campañas entran como cualquier otra, con goal de campaña (target derivado del margen, banda [10,45]) en modo shadow hasta graduarse.

## 1. `app/cycle.py`: estructura, transacciones, recorrido, cascada

**Transacciones:**
- El docstring del módulo las describe: TX1 en `cycle.py:13`, TX2 en `:19`, TX3 en `:25`, TX4 en `:119`.
- TX1 abre en `corre_ciclo` `:2995`.
- TX2 es `_fase_lecturas` `:2421-2458`. Abre con `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` (`:2441`), corre `windows.guarda_plataforma`, después `apply_cola.claves_bloqueadas` y después `_recorre_plataforma`.
- TX3 está en `_corre_fases` `:2852-2866`: `_inserta_decisiones` + `_inserta_targets` + (si el flag está activo) `propuestas_campana.guarda_evaluaciones` + `_cierra_envelope`. Corre en READ COMMITTED (`:2857`).
- TX4 está en `_fase_apply` `:2552` y envuelve `encola_cortes`.

**Snapshot del ciclo.** `_recorre_plataforma` `:2296-2418` lee todo una sola vez por plataforma:
- `setting_target = g.target_desde_settings` (`:2309`).
- `_resuelve_target_ciclo` (`:2312`): el peldaño margen_plataforma (`:2011-2108`).
- `_lee_familias_ciclo` (`:2323`), con destino = plataforma ?? setting (`:2322`).
- `acos_campanas` vía `_SQL_CAMPANAS` y `goals = _lee_goals` (`:2337`).
- `propuestas_campana.lee_evaluaciones` (`:2341`, solo con flag). Es el precedente más cercano a "evaluar en TX2 y persistir en TX3".
- La evidencia por ad group (`:2348`), `inertes` (`:2352`), el flag `pause_economica` y `politica = g.politica_bid_desde_settings` (`:2370`).
- `lecturas = lee_plataforma(..., economia=EconomiaPlataforma(...))` (`:2376-2383`).
- El dict `comunes` (`:2384-2402`) agrupa lo que reciben los dos recorridos.

**Recorridos:**
- Hojas: `for fila in _SQL_DECISORAS` llama a `_procesa_decisora` (`:2403-2414`). `_SQL_DECISORAS` (`:457-468`) toma keyword y product_target con su ad_group y su campaña.
- Ad groups: `for fila in _SQL_GRUPOS` llama a `_procesa_grupo` (`:2415-2417`).
- Los product ads no se recorren. Solo aparecen en `_SQL_PAREJAS_CAMPANA_FAMILIA` (`:503-514`), que da familia por campaña (product_ad ENABLED/PAUSED → listing → product → producto_familia).

**Goal por hoja:**
- `_gates_entidad` (`:1589-1614`) llama a `_porta_goal_campana` (`:1442-1453`), que hace `g.resuelve_goal(por_campana.get(campaign_id), goal_plataforma)`. Salta con `sin_goal`, `goal_disabled` o `goal_mode_off`.
- No hay variables separadas `goal_campana`/`goal_plataforma` en el motor. Se pasa **un solo goal ya resuelto** (`goal.target_acos_pct`, `goal.scope`).

**Llamada real en la hoja** (`:1706-1728`):
```python
target_margen_familia, snapshot_fam = _margen_familia_de_hoja(familias_ciclo, campaign_id, entidad_id)
target = g.cascada_target_acos(goal.target_acos_pct, setting_target, margen_plataforma, goal.scope, target_margen_familia)
...
procedencia = g.peldano_target_acos(goal.target_acos_pct, goal.scope, margen_plataforma, setting_target, target_margen_familia)
contadores.targets[entidad_id] = (target, procedencia)
```
- Con `target is None`, la hoja cuenta como skip `sin_target` (`:1716-1721`).
- En el grupo es idéntico (`:1895-1915`), con `hoja_id=None` y `ancla_grupo`. Esa ruta no escribe en `contadores.targets`: solo las hojas congelan en `target_acos_ciclo`.

**Todos los consumidores de la cascada:**

| Consumidor | Ubicación | Qué llama | Notas |
|---|---|---|---|
| Motor, hojas | `app/cycle.py:1709,1722` | `cascada_target_acos` + `peldano_target_acos` | con familia |
| Motor, grupos/términos | `app/cycle.py:1898,1909` | ídem | con familia; ancla de grupo |
| Revalidación de pausa | `app/apply_cola.py:618-664` (`_target_pause_vigente`, usada en `:768`) | `cascada_target_acos_con_procedencia(campana, plataforma, settings, estado[1], platform, margen)` | **sin `target_margen_familia`**; el margen sale de `notes.target.target_aplicado` del ciclo ejecutor |
| Propuestas de campaña | `app/propuestas_campana.py:651` | `..._con_procedencia(campana, goal_plataforma, settings, cache_target, platform, target_margen)` | **sin familia**; atrapa ValueError → None |
| Dashboard /settings | `app/api_dashboard.py:1855` | `..._con_procedencia(None, goal, settings, None, plataforma, _target_margen_del_ciclo(...))` | solo plataforma |
| Dashboard /campanas | `app/api_dashboard.py:749-776` | no recalcula: lee `target_acos_ciclo` del último ciclo live+done | `"mixto"` si hay varias procedencias |
| Etiquetas del dashboard | `app/api_dashboard.py:1811-1815` `PISA_POR_PELDANO` | nombres de peldaño fijos en el código | KeyError si llega uno desconocido |
| UI | `app/ui.py:50,408,475` | `PELDANOS_CASCADA` como vocabulario y validación 422 | |
| Replay económico | `tools/replay_ads_economico.py:77-94` | `_FUENTES_COMPARTIDAS` fija 4 nombres (sin margen_familia) | lee `target_acos_ciclo` |
| Rejuego niveles | `tools/rejuega_niveles.py:4,126` | lee `target_acos_ciclo` + `notes.target` (equilibrio) | no recalcula la cascada |
| Fábrica/impulso | `app/fabrica_plan.py:326-345` `target_del_grupo` | fórmula propia: `clamp(fraccion × min(margenes), [10,45])`, cuantizada a 2 decimales | no pasa por la cascada; escribe el goal de campaña |
| DB | `migrations/0063_bids02_peldanos_target.sql:22-27` | CHECK de los 5 nombres | "espeja PELDANOS_CASCADA (el test estatico lo sella)" |
| Tests | `tests/test_optimizer_goals.py`, `tests/test_settings_escritura.py`, `tests/test_apply_schema.py:904,1022` | | |

## 2. `app/optimizer/goals.py`

**Firmas exactas:**
- `cascada_target_acos(target_goal, setting_plataforma, target_margen=None, scope_goal=None, target_margen_familia=None) -> Decimal | None` (`:232-256`).
- `peldano_target_acos(target_goal, scope_goal, target_margen, setting_plataforma, target_margen_familia=None) -> str | None` (`:315-338`). Ojo: el orden de argumentos difiere del de la anterior.
- `cascada_target_acos_con_procedencia(goal_campana, goal_plataforma, settings, cache_acos_target, platform, target_margen=None, target_margen_familia=None) -> tuple[Decimal|None, str|None]` (`:341-392`).

**Núcleo y peldaños:**
- Las tres variantes usan el núcleo único `_nucleo_target_acos` (`:279-303`), con orden goal → margen_familia → margen_plataforma → setting.
- `target_margen_familia` va al final por compatibilidad posicional (`:293`).
- `PELDANOS_CASCADA` (`:270-276`) = `goal_campana, goal_plataforma, margen_familia, margen_plataforma, setting_plataforma`.
- `_valida_target_peldano` (`:220-229`): None pasa al siguiente peldaño; un valor ≤0 o no finito lanza ValueError y no cae.
- En `_con_procedencia`, un goal de campaña con target None bloquea goal_plataforma pero deja pasar margen y setting (`:366-383`).
- `cache_acos_target` "se valida, no se usa" (`:360-364`, `:387-388`).
- Ojo: el docstring del módulo (`:17-27`) describe un orden viejo; el código vigente es el de `_nucleo_target_acos`.

**Constantes y lectores:**
- Fracción: `fraccion_desde_settings(settings, platform)` (`:659-680`), con clave `ads_target_fraccion_margen_{platform}`. Ausente devuelve None (abstención `sin_fraccion`, es el interruptor). Fuera de (0,1] lanza ValueError. `_valida_fraccion` en `:683-691`.
- `MARGEN_BANDA_MIN = 10` y `MARGEN_BANDA_MAX = 45` (`:569-570`).
- `MARGEN_PASO_MAX = 0.5` (`:571`, convergencia simétrica) y `MARGEN_PASO_MAX_BAJADA = 0.5` (`:576`).
- `PASO_POLITICA = "asimetrico_v1"` (`:579`), que se congela en notes.
- `MARGEN_COBERTURA_MIN = 0.95`, `MARGEN_DIAS_MIN = 60` (plataforma), `MARGEN_DIAS_MIN_FAMILIA = 30`, `MARGEN_RANCIO_DIAS = 3` (`:580-583`).
- Vocabulario cerrado `MOTIVOS_ABSTENCION` (`:588-595`): sin_margen, cobertura_baja, ventana_corta, sin_fraccion, ledger_rancio, sin_familia.

**`resuelve_target_margen(medicion, fraccion, hoy, ultimo, setting=None, dias_min=60)`** (`:880-932`):
1. Fracción None devuelve `sin_fraccion` en seco.
2. `ultimo` se clampa a la banda.
3. `_motivo_dato_invalido` (`:935-951`). Si hay motivo, `_converge_al_destino(ultimo, setting, motivo)` (`:860-877`): un paso de ≤0.5 hacia el setting; sin ancla devuelve None y el peldaño cae al siguiente.
4. `derivado = fraccion × margen_neto_pct`, `recortado = clamp(derivado,[10,45])`.
5. Sin ancla devuelve `recortado`. Si `recortado >= ultimo`, lo aplica de una vez. Si no, `max(recortado, ultimo-0.5)`.

**Familia.** `resuelve_target_margen_familia(etiqueta, padre, fraccion, hoy, ultimo, destino, *, tiene_previo)` (`:991-1050`) usa la misma banda, el mismo paso y el mínimo de 30 días. Si la subfamilia no mide, reintenta con el padre.

**Dónde vive el ancla del paso:**
- Plataforma: `_resuelve_target_ciclo` (`cycle.py:2030-2048`) recorre `_SQL_NOTAS_PREVIAS` (`:562-569`, hasta 200 ciclos done) y toma `notes.target.target_aplicado` del último ciclo live+done con procedencia `margen_plataforma`. Si no hay, usa el setting.
- Familia por hoja: `_SQL_TARGETS_PREVIOS` (`:524-533`) sobre `target_acos_ciclo` (último live+done, sin filtrar procedencia). `tiene_previo` exige que el previo sea margen_familia (`:2261-2262`).
- Familia por grupo: `_anclas_grupo` (`:2209-2223`).
- `ad_entity_state.acos_target` se lee en `_SQL_DECISORAS` pero se descarta como `_acos_cache` (`:1647`).

## 3. Del target a la política, el freeze y el replay

**Camino del target:**
- `lecturas.caso(conn, hoja_id, ad_group_id, bid=BidVigente(...floor, ceiling), target_acos_pct=target, pausa=InsumosPausa(...))` (`cycle.py:1792-1799`), luego `decide(caso)` (`:1800`).
- `Economia` = `EconomiaPlataforma` + `target_acos_pct` de esta hoja (`app/optimizer/caso.py:214-219`).
- `EconomiaPlataforma.equilibrio_acos_pct` viene de `target_ciclo.margen_neto_pct` (`cycle.py:2378`). Es el margen neto crudo de la **plataforma**, no-None solo si el peldaño de plataforma midió. Es igual para todas las hojas, aunque en una hoja gane familia o goal.
- `gasto_para_concluir` vale 350 MXN / 36 USD por defecto (`goals.py:761-764`, `caso.py:207-208`).

**Uso dentro de la política** (`app/optimizer/politica.py`):
- `decide` `:130-254` valida `target > 0` (`:189-191`).
- R0 pausa vía `_decide_pause(..., target, politica_economica)` (`:194-205`). La pausa económica usa `exceso_economico = cost - target×ad_revenue/100` (`app/optimizer/bid.py:188-204`).
- R3 usa equilibrio (`politica.py:303-304`). R4–R6 usan target × 1.15 / 1.35 / 0.85.
- Con la política apagada (`politica is None`) corre `bid.decide_pause(... target_acos_pct=target ...)` (`cycle.py:1746-1790`).

**Freeze en `inputs`:**
- `pendiente_bid` (`cycle.py:859-933`) congela: `motor:"bid"`, `politica:"niveles_v3"`, `platform`, `modo`, `motivo`, `factor`, `nivel`, `veredicto_kind`, `caso` (`caso.como_json()`, que incluye `economia.target_acos_pct` y `equilibrio_acos_pct`), `target_acos_pct_usado`, `target_procedencia`, `target_snapshot` (solo si gana margen_plataforma o margen_familia), `goal` (`_goal_json`, `:790-856`, con floor/ceiling efectivos), `bid_actual`, `bid_moneda` y `corte` (`_corte_json`, `:706-749`, con `cost_min_usado`).
- La pausa con política apagada usa `_pendiente_pause_apagada` (`:936-991`). Los términos usan `_pendiente_termino` (`:994-1063`), con el mismo par target/procedencia/snapshot.
- Además, `target_acos_ciclo(cycle_id, ad_entity_id, decided_at, target_acos_pct, procedencia)` congela el target de cada hoja que llegó a calcularlo, aunque no deje decisión (`:620-628`, `:1271-1282`).

**Replay** (`app/optimizer/replay.py`):
- `reproduce(inputs)` `:122-148`: si `inputs["politica"]=="niveles_v3"`, ejecuta `decide(CasoHoja.desde_json(inputs["caso"]))`. Si `motor=="bid"` sin esa clave, `decide_bid_era_bandas`. Si es hygiene, `_replay_hygiene`, que lee `target_acos_pct_usado` (`:99`).
- El replay nunca recalcula la cascada. Toma el target del caso ya congelado.
- `replay_bid_con_target` (`:110-119`) sirve solo para la era vieja (lo usa `tools/compara_target_margen.py`).
- Freeze y replay están partidos a propósito: toda clave nueva se agrega en los dos lados (`replay.py:12-16`).

## 4. Impulso (BIDS 02)

**Módulos:**
- `app/impulso.py` es puro. `ROLES_IMPULSO = ("category_exact","auto_discovery")` está en `app/fabrica_plan.py:43`. `PLAZO_VEREDICTO_DIAS=14`, `DIAS_DE_MARGEN_DEL_VIGIA=2` (`impulso.py:20-24`).
- `veredicto_impulso` `:168-187`: con ≥1 pedido gradúa. Pausa si gasto ≥ tope − 2×diario. Al vencer el plazo, pausa sin impresiones o sin clics.
- `grupo_v2_de_plan` construye el plan en **modo `"shadow"`** (`:210-223`).
- La IO vive en `app/impulso_io.py`. El vigía es `vigila`/`_vigila_uno` (`:929-1084`) y se invoca por CLI `impulso-vigia` (`app/cli_bids.py:98`).
- El tope por defecto es `gasto_para_concluir` (`impulso_io.py:24`; `goals.py:758-759`).

**Cómo se identifica:**
- Tabla `impulso`, con platform, listing_id, product_id, grupo_id, lote, subfamilia, tope (`_sella_impulso` `:487-509`).
- Campañas vía `campana_grupo_rol.grupo_id` (`_SQL_CAMPANAS_DE_IMPULSO` `:884-889`).
- Impulso "abierto" = sin `impulso_lectura.es_final` (`_SQL_VIVOS` `:870-877`).
- El lote se sigue en `fabrica_lote.estado/pausado_por='impulso-vigia'` (`:962-966`).

**Graduación y retiro:**
- Al graduar, `goals_write.edita_goal(..., mode="live")` para los goals de campaña del impulso (`:1004-1005`).
- Con la primera impresión, el vigía retira los anuncios del producto en otras bolsas: `retiro_anuncios.planea_retiro(..., motivo="impulso", conservar_grupo_id=grupo)` + `ejecuta_retiro(..., go=go, impulso_id=imp, actor="impulso-vigia")` (`:1034-1057`).

**Trato en el ciclo:**
- `cycle.py` no tiene ninguna referencia a impulso. Sus campañas tienen goal de campaña creado por la fábrica (`tools/fabrica_campanas.py:1299-1308`, `target_acos_pct=goal_target`, `mode=plan.modo`).
- El ciclo las decide normalmente: el gate solo excluye `enabled=false` y `mode='off'` (`cycle.py:1449-1452`). En la cascada gana `goal_campana`.
- El modo shadow se re-resuelve por decisión en el apply (`goals.py:56-59`).

## 5. Persistencia de `decision`

**DDL** (`migrations/0001_initial.sql:846-892`):
- Columnas: `id`, `cycle_id` FK `optimizer_cycle`, `ad_entity_id` FK `ad_entity`, `kind decision_kind`, `decided_at`, `config_version_id` FK, `data_observed_at`, `window_start`, `window_end`, `search_term`, `old_value`, `new_value`, `value_currency`, `inputs JSONB NOT NULL`.
- No hay FK a ninguna tabla de evaluación.
- Enum `decision_kind` = bid, budget, pause, resume, negative, harvest (`:40`). Ninguna migración posterior lo amplía.

**Restricciones e índices:**
- CHECK `decision_termino_coherente` (`:874-877`).
- CHECK de moneda solo en bid/budget/harvest (`:883-888`).
- CHECK `data_observed_at <= decided_at` (`:890-891`).
- Únicos parciales: una decisión por (ciclo, entidad) para bid/budget/pause/resume (`:898-900`) y por (ciclo, entidad, término) para negative/harvest.
- Trigger `decision_madurez_corte`: pause/negative/harvest exigen `window_end <= decided_at(UTC) - 10` (`:933-976`).
- Tabla append-only por trigger (COMMENT `:909`).

**Cola** (`migrations/0002_apply.sql:55-110`):
- `apply_queue` tiene `familia` GENERATED: pause → entity_cut, negative/harvest → term_cut.
- CHECK `kind IN ('pause','negative','harvest')`.
- Clave de efecto `(platform, ad_entity_id, familia, search_term) NULLS NOT DISTINCT` para filas no terminales.

**Desde el ciclo:**
- `_SQL_INSERT_DECISION` (`cycle.py:613-618`) + `_inserta_decisiones` (`:1241-1268`, executemany de `_Pendiente`, `:659-672`).
- `encola_cortes(conn, aplicador, cycle_id, *, modo_envelope, ahora)` (`apply_cola.py:478`) en TX4.

**Notes y motivos:**
- `_notas_cuerpo` (`cycle.py:1066-1098`) arma `{"skips":{"entidad":{motivo:n},"termino":{...}}, "decisiones":{kind:n}, "entidades","ad_groups","terminos","ciclos_muertos","degradacion_live", "target":snapshot, "harvest_destino", "motivo_skip","detalle"}`, más `propuestas_campana` en TX3.
- Las constantes de motivo están en `cycle.py:259-281` (sin_goal, goal_disabled, goal_mode_off, estado_no_enabled, campana/grupo_no_enabled, entidad_inerte, cooldown_7d, sin_target, politica_apagada, escalera_off, veto_pendiente). Los motivos `Mantener` de la política entran directo como skip (`:1802-1804`).

## Gotchas para AUTO-03 (lo que la spec asume y el código contradice o matiza)

1. **La llamada que propone la spec no es la del motor.** `cycle.py` usa `cascada_target_acos` + `peldano_target_acos` con un goal ya resuelto, no `_con_procedencia(goal_campana, goal_plataforma, settings, cache_acos_target, ...)`. Habría que cambiar tres firmas, que además tienen órdenes posicionales distintos, y sus llamadores.
2. **`cache_acos_target` es un argumento muerto** (`goals.py:360-364`). El estado del paso asimétrico está en `notes.target` (plataforma) y en `target_acos_ciclo` (familia), no en un cache.
3. **Hoy hay consumidores desalineados.** `apply_cola._target_pause_vigente` y `propuestas_campana` no pasan `target_margen_familia`, y `replay_ads_economico` fija 4 nombres sin margen_familia. "Migrar todas las variantes y consumidores en un solo cambio" incluye estas brechas que ya existen.
4. **Un peldaño nuevo exige migración** del CHECK `target_acos_ciclo_procedencia_check` (0063), más `PELDANOS_CASCADA`, `PISA_POR_PELDANO` (KeyError ante un nombre nuevo), `ui.py` (422) y el test estático que sella la paridad.
5. **El ciclo no conoce "grupo exclusivo".** Hoy la única topología de product ads que lee es campaña → familia (`_SQL_PAREJAS_CAMPANA_FAMILIA`, que agrega por campaña, no por ad group). La exclusividad por ad group sería una lectura nueva en TX2.
6. **No existe ancla de paso por producto.** Familia y plataforma suben de una vez y bajan 0.5 por ciclo. La spec no dice si el peldaño de producto lleva paso, convergencia al abstener o el pre-clamp del ancla. Sin paso, al pasar de producto a familia el target puede saltar en un ciclo, que es justo lo que `_converge_al_destino` evita hoy.
7. **El "sin piso de 10" choca con la banda actual en dos lugares.** `fabrica_plan.target_del_grupo` (`:340`) y `valida_banda_manual` (`:271`) fuerzan [10,45] en los goals de campaña de fábrica/impulso, y `_converge_al_destino` clampa el ancla a [10,45].
8. **El impulso no queda excluido por el ciclo.** Sus campañas llevan un goal de campaña explícito con target derivado del margen (con piso 10), así que con "goal explícito gana" el peldaño de producto nunca aplicaría ahí, ni siquiera después de graduarse (el goal sigue con su target). No hay marca de "vigía" en `ad_entity`. La pertenencia sale de `impulso` + `campana_grupo_rol`.
9. **El equilibrio de R3 es de plataforma** (`cycle.py:2378`). Aunque gane un target de producto, R3 sigue comparando contra el margen neto de la plataforma (o None si la plataforma no midió).
10. **`decision.kind` no tiene `pause_product_ad`, y `decision` no tiene FK a evaluación.** El índice único de entidad por ciclo y el trigger de madurez se escriben por lista de kinds (`kind IN (...)`), igual que `apply_queue_solo_cortes` y la `familia` GENERATED. Un kind nuevo los toca a todos. `_SQL_PAUSE_TARGET_STATE` en apply_cola filtra `e.kind IN ('keyword','product_target')`.
11. **El replay no recalcula targets.** Rejuega desde `inputs.caso.economia.target_acos_pct`. "El replay lee la evaluación referida" sería un camino nuevo. El actual (`reproduce`) basta si el target sigue congelado en el caso.
12. **Ya existe un precedente de "evaluación por lote en TX2, persistida en TX3".** Es `propuestas_campana.lee_evaluaciones` / `guarda_evaluaciones` (`cycle.py:2340-2347`, `2861-2864`; tabla `ads_campaign_proposal`), activado por flag `ads_propuestas_campana`.
13. **La fuente de margen por producto ya existe:** `v_margen_producto` (migración 0018), consumida por `fabrica_web.py:58`, `tools/fabrica_campanas.py:90` y `app/economia_observada.py` (que mezcla "muestra limitada"). No la consume el ciclo.
14. **Fracciones 0.5 (MX) y 0.8 (US):** son datos de config que no verifiqué en DB.
