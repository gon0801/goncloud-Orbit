# E/R.1 — Catálogo de mutantes (FABRICA 02, Fase A)

Implementador: Muse. Base leída: `origin/master` `41bc6aa`
(cierre documental de Fase A; código `7a32ec3`, sin diff de código entre
ambos: solo `docs/CHAT-CONTEXT.md`, `plans/fabrica-02.md` y
`plans/manifest.json`). Rama: `feat/fabrica-02-r1` cortada desde
`origin/master`. Fecha: 2026-09-15. Plan: `plans/fabrica-02.md` v1.9
(fila R.1, AC1a–AC12, residuales A.3–A.6).

Contrato de cada fila: diff mínimo (1–5 líneas) aplicado con
`git apply` o a mano sobre el SHA base, focales del archivo relevante,
rojo literal copiado de pytest, revertido con `git checkout --` y
`git status` limpio. Ninguna mutación se commitea.

## Baseline (DSN real, antes del primer mutante)

```bash
ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres uv run --frozen python -m pytest -q \
  tests/test_fabrica_f2_hermanas.py tests/test_fabrica_f2_biblioteca.py \
  tests/test_fabrica_f2_visibilidad.py tests/test_harvest_destino.py \
  tests/test_harvest_excepcion.py tests/test_apply_harvest.py \
  tests/test_fabrica_0038.py tests/test_reversa_harvest.py
```

```text
302 passed, 1 warning in 43.58s
```

El warning es preexistente (`StarletteDeprecationWarning` por
`httpx` en `starlette.testclient`, ajeno a F2). **0 skipped**: condición
de partida cumplida.

## Inventario AC → tests existentes (antes de mutar)

Ningún AC está sin test. Cobertura por AC sobre el SHA base:

| AC | Tests que lo cubren |
|---|---|
| AC1a | `test_harvest_destino.py::test_a1_grupo_con_terna_null_postea_en_exacta_del_grupo`, `::test_a1_ciclo_tras_limpiar_terna_congela_destino`, `::test_a1_grupo_gana_a_excepcion`; `test_fabrica_f2_hermanas.py::test_roster_tres_hermanas_por_cada_rol_origen`, `::test_sello_durable_visible_desde_segunda_conexion_antes_del_primer_post` |
| AC1b | `test_harvest_destino.py::test_a1_terna_campaign_distinta_es_inconsistente_y_cero_http`; `test_fabrica_f2_visibilidad.py::test_a6_terna_distinta_deja_salto_de_grupo_en_notes`, `::test_a6_flanco_avisa_una_vez_por_racha`; `test_notifica.py::test_notifica_destino_grupo_envia_y_tumba`, `::test_fase_notifica_mapea_salto_destino_a_nota_y_acumula` |
| AC2 | `test_harvest_destino.py::test_a1_exact_como_origen_es_skip_declarado` |
| AC3 | `test_fabrica_f2_hermanas.py::test_previo_truncado_cero_post_y_pendientes`, `::test_reanudacion_retoma_indice_y_huerfanas_no_cierra`, `::test_posterior_truncado_en_ciclo_3_cierra_por_tope`, `::test_ack_durable_con_posterior_unknown_y_prueba_tardia`, `test_apply_harvest.py::test_barrido_cierra_fila_harvest_applying_sin_job_vivo`, `test_lista_filtrada_*` (fail-closed) |
| AC4 | `test_fabrica_f2_hermanas.py::test_fallo_400_deja_pendiente_y_reintenta_sin_quota`, `::test_pt_rechazada_queda_pendiente_con_motivo_propio`, `::test_5xx_y_ack_sin_id_quedan_pendientes_con_motivo`, `::test_ack_sin_id_queda_pendiente`, `::test_ciclo_3_cierra_done_con_pendientes_y_alerta_veraz`, `::test_quota_no_se_recobra_en_reintentos_de_higiene` |
| AC5 | `test_apply_harvest.py::test_harvest_vetado_jamas_crea_harvest_job`; `test_fabrica_f2_hermanas.py::test_grupo_en_shadow_cero_jobs`; `test_fabrica_f2_biblioteca.py::test_failed_antes_de_readback_cero_filas`, `::test_shadow_cero_filas_biblioteca`, `::test_vetado_cero_filas_biblioteca`, `::test_terna_sin_grupo_cero_filas_biblioteca`, `::test_excepcion_sin_grupo_cero_filas_biblioteca`, `::test_perdida_claim_cero_filas_biblioteca` |
| AC6 | `test_fabrica_f2_biblioteca.py::test_fallo_inyectado_keyword_sello_intacto`, `::test_fallo_inyectado_negative_veredicto_intacto` |
| AC7 | `test_harvest_destino.py::test_a1_grupo_mutado_antes_de_liberar_no_postea`, `::test_a1_terna_limpiada_tras_decidir_apply_y_replay_usan_congelado`, `::test_a1_bid_congelado_viaja_al_post_tras_limpiar_terna`, `::test_a1_excepcion_ajena_no_pertenece_es_desincronizado`, `::test_a1_terna_ajena_no_pertenece_es_desincronizado`, `::test_a1_termino_en_exacta_del_grupo_es_duplicado`; `test_fabrica_f2_hermanas.py::test_membresia_cambiada_no_falla_harvest_confirmado` |
| AC8 | `test_fabrica_0038.py::test_0038_app_decide_bibliotecas_statement_literal`, `::test_0038_canon_biblioteca_en_do`, `::test_0038_canon_biblioteca_statement_literals_cruza_con_do`; `test_fabrica_f2_biblioteca.py::test_rol_app_decide_escribe_y_prohibe` |
| AC9 | `test_harvest_destino.py::test_a1_sin_grupo_con_terna_propia_resuelve_terna`, `::test_a1_sin_goal_propio_resuelve_terna_de_plataforma`, `::test_a1_sin_grupo_con_excepcion_resuelve_excepcion`; `test_fabrica_f2_visibilidad.py::test_a6_campana_suelta_con_termino_salta_sin_aviso_de_grupo`, `::test_a6_notes_trae_resueltos_por_grupo_y_sin_saltos` |
| AC10 | `test_fabrica_f2_biblioteca.py::test_harvest_grupo_aprende_keyword_en_sello`, `::test_grupo_negativos_de_harvest_no_entran_a_negativa`, `::test_precedencia_termino_ya_keyword_no_entra_como_exclusion`, `::test_mismo_termino_otro_job_actualiza_updated_at_sin_duplicar` |
| AC11 | `test_harvest_destino.py::test_a1_ciclo_tras_limpiar_terna_congela_destino`, `::test_a1_bid_congelado_viaja_al_post_tras_limpiar_terna`, `::test_a1_grupo_sin_bid_negative_reproduce_sin_excepcion`; `test_apply_harvest.py::test_bid_sugerido_403_deja_el_default_del_goal_clampeado`, `::test_clampeo_sugerido_sobre_ceiling_y_bajo_floor` |
| AC12 | `test_fabrica_0038.py::test_0038_hermana_en_ledger_sin_quota_y_sin_decision_truena`; `test_fabrica_f2_hermanas.py::test_seq_global_monotonica_en_flujo_de_hermanas`, `::test_quota_no_se_recobra_en_reintentos_de_higiene`, `::test_cap_tres_intentos_por_adgroup`; `test_apply_harvest.py::test_harvest_completo_1_quota_2_https`, `::test_reconcilia_cobra_quota_la_primera_vez_y_respeta_el_veto_en_released`, `::test_reintento_released_sin_quota_reusa_el_job_sin_abortar_la_tx` |

## Tabla de mutantes — obligatorios (M01–M14)

| # | AC | Sitio | Mutación (diff mínimo) | Test asesino | Rojo literal | Estado |
|---|---|---|---|---|---|---|
| M01 | OBL | `app/apply_harvest.py:_avanza:899` | `- ext.update({k: v for k, v in ids.items() if v is not None})`<br>`+ ext = {k: v for k, v in ids.items() if v is not None}` | `tests/test_fabrica_f2_hermanas.py::test_merge_conserva_exitos_previos` | `E KeyError: 'auto_discovery'` (`tests/test_fabrica_f2_hermanas.py:1593`) | muere |
| M02 | OBL | `app/apply_harvest.py:_lista_filtrada:571-574` | `- body: dict = {"adGroupIdFilter": {"include": [str(x) for x in ad_group_ids]}}`<br>`+ body: dict = {}`<br>`+ if token is None:`<br>`+     body["adGroupIdFilter"] = {"include": [str(x) for x in ad_group_ids]}` | `tests/test_fabrica_f2_hermanas.py::test_lista_filtrada_filtro_en_cada_pagina_y_senales_de_ambiguedad` | `E AssertionError: filtro en cada pagina` | muere |
| M03 | OBL | `app/apply_harvest.py:_identidad:607-608` | `- if str(item.get("state", "")).upper() == apply.ESTADO_WIRE_ARCHIVED:`<br>`-     continue  # delete-archiva: operativamente AUSENTE (probe 2.5)` | `tests/test_apply_harvest.py::test_identidad_ignora_archived_el_delete_archiva` | `E AssertionError: ARCHIVED es operativamente AUSENTE: la identidad devuelve la fila VIVA` | muere |
| M04 | OBL | `app/apply_harvest.py:_paso_readback:1597` | `- encontrado = _identidad(kws, ctx.destino_grupo, job.search_term)`<br>`+ encontrado = _identidad(kws, ctx.destino_grupo, job.search_term) or {"keywordId": "fantasma"}` | `tests/test_fabrica_f2_biblioteca.py::test_keyword_ausente_en_readback_cero_filas` | `E AssertionError: ResumenReconciliacion(jobs_done=1, jobs_failed=0, ...)` sobre `assert resumen.jobs_failed == 1` | muere |
| M05 | OBL | `app/apply_harvest.py:plan_reversa_harvest:1220` | `- return (job.plataforma, job.search_term, job.decision_id, pasos)`<br>`+ return (job.plataforma, job.search_term, job.decision_id, list(reversed(pasos)))` | `tests/test_reversa_harvest.py::test_plan_orden_canonical_excluye_adoptadas` | `E AssertionError: adoptada fuera, propias en orden canonico, origen ultimo` | muere |
| M06 | OBL | `app/apply_harvest.py:_post_hermana:1789` | `- id_attempt = apply._ledger(conn, job.decision_id, "hermana", payload, quota_cobrada=False)`<br>`+ id_attempt = apply._ledger(conn, job.decision_id, "hermana", payload, quota_cobrada=True)` | `tests/test_fabrica_f2_hermanas.py::test_seq_global_monotonica_en_flujo_de_hermanas` | `E At index 2 diff: True != False` sobre `assert [f[2] for f in filas] == [True, False, False, False, False]` | muere |
| M07 | OBL | `app/biblioteca.py:SQL_BIBLIOTECA_KEYWORD:88-90` | `- "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen)"`<br>`- " VALUES (%s, %s, %s, %s)"`<br>`+ "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen, cost, moneda)"`<br>`+ " VALUES (%s, %s, %s, %s, 0, 'MXN')"` | `tests/test_fabrica_f2_biblioteca.py::test_statements_sin_dinero` | `E AssertionError: assert 'cost' not in 'INSERT INTO...RETURNING id'` | muere |
| M08 | OBL | `app/optimizer/harvest_destino.py:_SQL_GRUPO:96-100` | `- ON re.grupo_id = r.grupo_id AND re.rol = 'category_exact'`<br>`+ ON re.grupo_id = r.grupo_id`<br>`- AND ce.platform = %s::platform`<br>`+ AND ce.platform = %s::platform AND ce.external_id LIKE '%%exact%%'` | `tests/test_harvest_destino.py::test_a1_grupo_con_terna_null_postea_en_exacta_del_grupo` | `E AssertionError: assert '8001' == '6104'` (resuelve terna de plataforma en vez de exacta del grupo) | muere |
| M09 | OBL | `app/optimizer/harvest_destino.py:decide:181-183` | `- return SaltoHarvest(motivo=hygiene.MOTIVO_DESTINO_INCONSISTENTE)`<br>`+ _g = lectura.goal_campana`<br>`+ return DestinoHarvest(_g.harvest_campaign_id, _g.harvest_ad_group_id, RESUELTO_TERNA, grupo_id, None, bid, moneda, floor, ceiling)` | `tests/test_harvest_destino.py::test_a1_terna_campaign_distinta_es_inconsistente_y_cero_http` | `E AssertionError: assert DestinoHarvest(campaign_external='8001', ...) == SaltoHarvest(motivo='destino_inconsistente')` | muere |
| M10 | OBL | `migrations/0038_fabrica_hermanas_biblioteca.sql:246-247` | `- GRANT USAGE ON SEQUENCE keyword_biblioteca_id_seq, negative_biblioteca_id_seq`<br>`-     TO app_decide;` | `tests/test_fabrica_0038.py::test_0038_app_decide_bibliotecas_statement_literal` | `E psycopg.errors.InsufficientPrivilege: permission denied for sequence keyword_biblioteca_id_seq` | muere |
| M11 | OBL | `migrations/0038_fabrica_hermanas_biblioteca.sql:81` | `- WHERE fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas');`<br>`+ WHERE fase IN ('pending', 'negative_created', 'exact_created');` | `tests/test_fabrica_0038.py::test_0038_en_vuelo_bloquea_en_hermanas_y_libera_en_done` | `E Failed: DID NOT RAISE UniqueViolation` | muere |
| M12 | OBL | `app/apply_harvest_reconciliacion.py:_SQL_JOBS_EN_VUELO:54` | `- AND fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas')`<br>`+ AND fase IN ('pending', 'negative_created', 'exact_created')` | `tests/test_fabrica_f2_hermanas.py::test_reanudacion_retoma_indice_y_huerfanas_no_cierra` | `E assert 0 == 1` sobre `assert resumen.jobs_done == 1` (el job en higiene queda invisible) | muere |
| M13 | OBL | `app/cycle.py:_goal_json:708` | `- if destino.bid is None or destino.moneda is None:`<br>`+ if goal.harvest_campaign_id is None or goal.harvest_ad_group_id is None:` | `tests/test_harvest_destino.py::test_a1_ciclo_tras_limpiar_terna_congela_destino` | `E AssertionError: post-D.2 el harvest sale del destino, no de la terna` | muere |
| M14 | OBL | `app/apply_harvest_reconciliacion.py:revalida_harvest:191` | `- keywords = hygiene.keywords_campana_destino(conn, platform, destino.campaign_external)`<br>`+ _fresco = _ejecucion._goal_del_grupo(conn, platform, padre[0]) if padre is not None else None`<br>`+ keywords = hygiene.keywords_campana_destino(conn, platform, (_fresco.harvest_campaign_id if _fresco and _fresco.harvest_campaign_id else destino.campaign_external))` | `tests/test_harvest_destino.py::test_r1_revalida_duplicado_en_exacta_resuelta_descarta` (nuevo, R.1) | `E AssertionError: el duplicado no se aplica` / `assert 1 == 0` (con el mutante; sin él: verde). Sobrevivía: `184 passed` sin el test nuevo | sobrevive → test nuevo `tests/test_harvest_destino.py::test_r1_revalida_duplicado_en_exacta_resuelta_descarta` |

## Notas por mutante (M01–M14)

- M01: también mueren `test_sello_durable_visible_desde_segunda_conexion_antes_del_primer_post`
  (`el flujo de grupo debe POSTear a hermanas (hay roster)`) y
  `test_roster_tres_hermanas_por_cada_rol_origen` en los 4 orígenes
  (`assert [] == [...]`, roster perdido). Cita: PR #267 (A.3, roster
  congelado; el docstring del test dice "un reemplazo del dict perdería
  los éxitos").
- M02: `test_dos_barridos_batched_con_filtro_en_cada_pagina` PASA con este
  mutante (su fixture es de 1 página); el discriminante es el test puro de
  2 páginas. Cita: PR #267 (A.3, filtro preservado en cada página).
- M03: `test_identidad_adopta_enabled_exact_e_ignora_archived_y_phrase`
  PASA con este mutante porque el previo de hermanas usa `_coincidencias`
  (filtro propio intacto, línea 628): dos capas, como notó kimi. Cita:
  PR #267 (A.3, identidad ad group + NEGATIVE_EXACT + vivo).
- M04: la primera forma del mutante (borrar la rama `encontrado is None`)
  moría por crash (`AttributeError` en línea 1625), no por semántica; se
  afinó a 1 línea para que el rojo demuestre el gate. También mueren
  `test_matriz_senuelo_en_otro_ad_group_no_es_ya_aplicada` y
  `test_reversa_automatica_borra_la_keyword_aun_sin_negative_id` (ambos en
  `test_apply_harvest.py`). Cita: PR #269 (A.4, matriz de cero filas).
- M05: también mueren otros 11 de `test_reversa_harvest.py` (ejecución,
  readbacks, discordantes, reanudación, CLI ambiguo, provisional). Cita:
  PR #267 (A.3, reversa por ID keyword → hermanas → origen).
- M06: `test_quota_no_se_recobra_en_reintentos_de_higiene` PASA con este
  mutante porque lee `apply_quota_state.used` (el contador, que las
  hermanas jamás tocan), no el flag del ledger; el único candado del flag
  es el test de seq (el esquema no tiene CHECK, como notó kimi). Cita:
  PR #267 (A.3, ledger `tipo='hermana'` con `quota_cobrada=false`).
- M07: segunda capa dinámica:
  `test_harvest_grupo_aprende_keyword_en_sello` muere con
  `AssertionError: sin dinero` / `assert (Decimal('0.0000') is None)` — el
  dinero sí aterriza en la fila. Con solo `cost` (sin moneda) el CHECK
  `biblioteca_dinero_con_moneda` rechaza el INSERT y el SAVEPOINT lo
  absorbe (los funcionales mueren por fila ausente); con cost+moneda el
  INSERT pasa y el NULL dinámico discrimina. 12 muertos en total (10 de
  biblioteca + 2 de 0038, incl. `cruza_con_do`). Cita: PR #269 (A.4
  DoD g, sin dinero).
- M08: también mueren otros 7 de grupo (origen exact, inconsistente,
  limpiada, duplicado, bid congelado, ciclo-tras-limpiar, grupo-gana); los
  15 sin grupo intactos. El LIKE lleva `%%` escapado (psycopg); sin escape
  el SQL truena con `ProgrammingError` y el mutante sería inválido. Cita:
  PR #258 (A.1 a, señuelo `category_exact` fuera del grupo).
- M09: también mueren `test_a6_terna_distinta_deja_salto_de_grupo_en_notes`
  y `test_a6_flanco_avisa_una_vez_por_racha` (la tríada AC1b). Cita:
  PR #258 (A.1 e, terna campaign distinta).
- M10: sin USAGE ni siquiera aplica la migración: el propio DO de 0038
  (que INSERTa como `app_decide` con SET ROLE real) truena en el fixture
  y 10/13 tests de 0038 caen en setup. Pasan solo los 3 que no levantan
  fixture con 0038 (canon estático, sin_usage que muta en memoria y sigue
  tronando, cruza_con_do estático). Cita: PR #261 (A.2, mutante 8).
- M11: también muere `test_0038_fases_en_vuelo_iguales_al_indice_real`
  (cruce contra `pg_index.indpred`). Cita: PR #261 (A.2, índice parcial).
- M12: también mueren otros 18 que reanudan higiene por reconciliación
  (crash, cap, 400, PT, ack_sin_id, merge, gate, ciclo3, posterior,
  membresía, dup, posterior-otro-id, ack_tardío, dos_abiertas,
  ack_distinto, ack_durable, biblio-no-reescribe ×2). Cita: PR #267
  (A.3 h, job en `hermanas_negadas` se retoma).
- M13: mata SOLO este test (1/23):
  `test_a1_bid_congelado_viaja_al_post_tras_limpiar_terna` pasa porque
  limpia DESPUÉS de decidir (el congelado ya trae harvest). Discriminación
  quirúrgica del bid-solo (terna NULL) que cosecha por grupo (AC11). Cita:
  PR #258 (A.1, ronda del lead: el congelado refleja lo usado).
- M14: SOBREVIVÍA (único obligatorio sin test). Causa: ningún test
  liberaba con el término plantado en la exacta RESUELTA y terna fresca
  en otra parte; el dedupe de revalida jamás discriminaba goal-fresco vs
  resuelto (`184 passed` en destino+apply+hermanas con el mutante).
  Cerrado rojo-primero con
  `test_r1_revalida_duplicado_en_exacta_resuelta_descarta`: verde sin el
  mutante, rojo con él. Cita: PR #258 (A.1 g — pero ese pin es del lado
  decide; este es el lado revalida).

## Tabla de mutantes — por AC (M15–M27)

Un mutante por cada AC, distinto del obligatorio que pisa la misma zona
(ver notas).

| # | AC | Sitio | Mutación (diff mínimo) | Test asesino | Rojo literal | Estado |
|---|---|---|---|---|---|---|
| M15 | AC1a | `app/optimizer/harvest_destino.py:_SQL_GRUPO:97` | `- ON re.grupo_id = r.grupo_id AND re.rol = 'category_exact'`<br>`+ ON re.grupo_id = r.grupo_id AND re.rol = 'auto_discovery'` | `tests/test_harvest_destino.py::test_a1_grupo_con_terna_null_postea_en_exacta_del_grupo` | `E AssertionError: assert '6100' == '6104'` (resuelve la campaña auto_discovery en vez de la exacta) | muere |
| M16 | AC1b | `app/cycle.py:2242` (flanco `avisos_destino`) | `- if saltos_previos is None or saltos_previos.get(str(camp)) != salto.motivo`<br>`+ if saltos_previos is None or saltos_previos.get(str(camp)) == salto.motivo` | `tests/test_fabrica_f2_visibilidad.py::test_a6_flanco_avisa_una_vez_por_racha` | `E AssertionError: la misma racha no re-avisa` / `assert 2 == 1` | muere |
| M17 | AC2 | `app/optimizer/harvest_destino.py:decide:175` | `- if rol_origen == "category_exact":`<br>`+ if rol_origen != "category_exact":` | `tests/test_harvest_destino.py::test_a1_exact_como_origen_es_skip_declarado` | `E AssertionError: la exacta no se harvestea a si misma` | muere |
| M18 | AC3 | `app/apply_harvest.py:_lista_filtrada:595` | `- return (items, "truncado")  # nextToken vivo al tope: fail-closed`<br>`+ return (items, "ok")  # nextToken vivo al tope: fail-closed` | `tests/test_fabrica_f2_hermanas.py::test_previo_truncado_cero_post_y_pendientes` | `E assert 'done' == 'hermanas_negadas'` (cierra en vez de quedar pendiente) | muere |
| M19 | AC4 | `app/apply_harvest.py:TOPE_CICLOS_HERMANAS:148` | `- TOPE_CICLOS_HERMANAS = 3`<br>`+ TOPE_CICLOS_HERMANAS = 30` | `tests/test_fabrica_f2_hermanas.py::test_ciclo_3_cierra_done_con_pendientes_y_alerta_veraz` | `E assert 'hermanas_negadas' == 'done'` (no cierra al tercer ciclo) | muere |
| M20 | AC5 | `app/apply_harvest_reconciliacion.py:507` (jobs de filas muertas) | `- if queue_estado in ("vetoed", "discarded"):`<br>`+ if queue_estado in ("discarded",):` | `tests/test_apply_harvest.py::test_reconcilia_cierra_job_de_fila_vetoada_la_cola_manda` | `E psycopg.errors.CheckViolation: apply_queue 1: transicion vetoed -> applied fuera de la maquina de estados (docs/APPLY.md seccion 1.2). ...` | muere |
| M21 | AC6 | `app/biblioteca.py:registra_keyword:276` (`except`) | `  except Exception as exc:  # noqa: BLE001 - el sello jamas aborta por la contabilidad derivada`<br>`+     raise`<br>`      return _rastro_fallo(` | `tests/test_fabrica_f2_biblioteca.py::test_fallo_inyectado_keyword_sello_intacto` | `E psycopg.errors.UndefinedTable: relation "no_existe" does not exist` (el fallo inyectado tumba el sello en vez de dejar rastro+alerta) | muere |
| M22 | AC7 | `app/apply_harvest.py:_contexto_congelado:863` | `- and vigente.campaign_external == destino_campana`<br>`+ and vigente.campaign_external != destino_campana` | `tests/test_harvest_destino.py::test_a1_terna_limpiada_tras_decidir_apply_y_replay_usan_congelado` | `E AssertionError: apply usa el congelado aunque la terna ya sea NULL` / `assert 0 == 1` | muere |
| M23 | AC8 | `migrations/0038_fabrica_hermanas_biblioteca.sql:248` | `- GRANT UPDATE (updated_at) ON keyword_biblioteca TO app_decide;`<br>`+ GRANT UPDATE ON keyword_biblioteca TO app_decide;` | `tests/test_fabrica_0038.py::test_0038_app_decide_bibliotecas_statement_literal` | `E psycopg.errors.RaiseException: 0038: UPDATE(origen) en keyword_biblioteca NO fue rechazado` | muere |
| M24 | AC9 | `app/optimizer/harvest_destino.py:decide:218` | `- motivo=hygiene.MOTIVO_MIGRACION_PENDIENTE,`<br>`+ motivo=None,` | `tests/test_harvest_destino.py::test_a1_sin_grupo_con_terna_propia_resuelve_terna` | `E AssertionError: assert None == 'migracion_pendiente'` | muere |
| M25 | AC10 | `app/biblioteca.py:registra_negative:309` | `- if ya_keyword is not None:`<br>`+ if ya_keyword is not None and False:` | `tests/test_fabrica_f2_biblioteca.py::test_precedencia_termino_ya_keyword_no_entra_como_exclusion` | `E AssertionError: no se ensena como exclusion` | muere |
| M26 | AC11 | `app/apply_harvest.py:1515` (paso keyword) | `- bid = bid_efectivo(sugerido, ctx.default_bid, ctx.floor, ctx.ceiling)`<br>`+ bid = bid_efectivo(sugerido, ctx.floor, ctx.floor, ctx.ceiling)` | `tests/test_harvest_destino.py::test_a1_bid_congelado_viaja_al_post_tras_limpiar_terna` | `E AssertionError: el bid congelado viaja clampeado` / `assert 1.0 == 11.62` | muere |
| M27 | AC12 | `app/apply_harvest_reconciliacion.py:530` (cobro pre-claim) | `- if queue_estado == "released":`<br>`+ if True:` | `tests/test_fabrica_f2_hermanas.py::test_quota_no_se_recobra_en_reintentos_de_higiene` | `E assert 2 == 1` sobre `assert used == 1` (tres ciclos recobran) | muere |

## Notas por mutante (M15–M27)

- M15: también mueren otros 6 de grupo (mutado, limpiada, duplicado,
  bid-congelado, ciclo-tras-limpiar, grupo-gana). Distinto de M08 (aquel
  quita el rol y filtra por nombre; este apunta a otro rol).
- M16: también muere
  `test_a6_flanco_grupo_sin_exacta_cuatro_avisos_una_vez`. Distinto de
  M09 (aquel ataca el skip inconsistente; este el aviso una-vez-por-racha
  de AC1b). Cita: PR #274 (A.6, sender en flanco por campaña).
- M17: también mueren otros 8 de grupo (el gate invertido salta los
  discovery y cosecha la exacta). Cita: PR #258 (A.1 b).
- M18: también mueren `test_posterior_truncado_en_ciclo_3_cierra_por_tope`
  y `test_ack_durable_con_posterior_unknown_y_prueba_tardia`. Distinto de
  M12 (aquel invisibiliza la fase en reconciliación; este rompe el
  fail-closed del LIST). Cita: PR #267 (A.3 d).
- M19: también mueren otros 5 de cierre por tope (crash_sin_ack,
  posterior_truncado_ciclo3, dup_tras_repost, ack_distinto, biblio
  done-por-tope). Distinto de M06 (aquel cobra quota; este rompe el
  cierre done+pendientes+alerta de AC4). Cita: PR #267 (A.3 e).
- M20: mata SOLO este test (1/48), y lo mata el TRIGGER
  `apply_queue_sella_transiciones` (defensa en profundidad del esquema:
  el job que la app dejó seguir revienta al intentar vetoed→applied), no
  el assert del test. Evidencia de capas: app + esquema.
- M21: mata SOLO este test (1/28): el gemelo de negative
  (`registra_negative` intacto) pasa. Quirúrgico al SAVEPOINT+catch de
  keyword. Distinto de M07 (aquel escribe dinero; este rompe el contrato
  jamás-aborta de AC6). Cita: PR #269 (A.4 e).
- M22: también mueren `test_a1_grupo_con_terna_null_postea_en_exacta_del_grupo`
  y `test_a1_bid_congelado_viaja_al_post_tras_limpiar_terna` (los 3
  liberan-OK).
  `test_a1_grupo_mutado_antes_de_liberar_no_postea` PASA con este mutante
  porque la comparación de ad_group (intacta) sigue pescando el cambio; el
  mutante solo rompe el lado "grupo intacto procede". Distinto de M13/M14
  (aquellos varían completa/dedupe; este la revalidación pre-POST del
  congelado). Cita: PR #258 (A.1 f).
- M23: el negativo del DO (UPDATE de origen/texto que debe tronar) ahora
  pasa y el DO levanta: 37/41 caen en setup de 0038+biblioteca (pasan solo
  4 estáticos/sin-fixture). Distinto de M10 (aquel quita USAGE; este
  amplía UPDATE por columna — el pin de GRANTs por columna de AC8). Cita:
  PR #261 (A.2 e).
- M24: también muere `test_a1_sin_goal_propio_resuelve_terna_de_plataforma`
  (la pareja c′/c″). Cita: PR #258 (A.1 c′/c″).
- M25: también muere `test_registra_negative_devuelve_motivo_sin_alertar`.
  Distinto de M07/M21 (aquellos dinero/catch; este el invariante de
  intersección vacía de AC10). Cita: PR #269 (A.4 d).
- M26: también mueren `test_harvest_completo_1_quota_2_https` y
  `test_bid_sugerido_403_deja_el_default_del_goal_clampeado`. Distinto de
  M13 (aquel varía completa; este el valor del bid del POST de AC11).
  Cita: PR #258 (A.1 h).
- M27: 30 muertos en hermanas+apply (el re-cobro en cada reconciliación
  satura caps y mueve conteos). Distinto de M06 (aquel el flag del
  ledger; este el contador `used` de AC12). Cita: PR #267 (A.3 f).

## Tabla de mutantes — residuales (M28–M29)

| # | AC | Sitio | Mutación (diff mínimo) | Test asesino | Rojo literal | Estado |
|---|---|---|---|---|---|---|
| M28 | RES | `app/cycle.py:2283-2300` (llamada `_fase_notifica`) | `+ with conn.transaction():  # MUTANTE R.1: enviar DENTRO de tx` envolviendo la llamada `_fase_notifica(...)` (mismo contenido, dentro de tx; verificado por diff) | — (los 3 citados por kimi: `tests/test_notifica.py::test_fase_notifica_mapea_salto_destino_a_nota_y_acumula`, `::test_fase_notifica_salto_destino_canal_caido_deja_nota`, `::test_notifica_destino_grupo_envia_y_tumba`) | — (no discriminable: `80 passed` en `test_notifica.py` + `test_fabrica_f2_visibilidad.py` con el mutante aplicado) | no discriminable |
| M29 | RES | `tools/harvest_excepcion.py` (resolución del par destino) | (estructural, sin diff: el tool jamás lee `ad_entity_state`) | — | — (no discriminable por diseño: `grep ad_entity_state/status/state` vacío en el tool y en `tests/test_harvest_excepcion.py`; no hay código que mutar ni fixture que discrimine) | no discriminable |

Notas:

- M28 (residual de A.6, verificación pedida por el lead): kimi sostiene
  que los 3 tests de `test_notifica.py` cubren el envío en
  `_fase_notifica`; ejecutado el mutante «enviar dentro de la
  transacción», los 3 pasan y las suites completas también (80 passed).
  Kimi demuestra contenido (mapeo/acumulación/nota), no timing: ningún
  test observa estado de transacción en el envío (`grep
  transaction_status` en tests: solo los espías H4 de biblioteca y
  módulos ajenos). Se enumera como `RES` con estado `no discriminable`,
  no como muerto — tal cual anticipaba el brief.
- M29 (residual de A.5, el noveno que kimi no mencionó): el tool
  `--migrar` no verifica el `state` vivo del ad group destino. Aceptado
  por contrato: es operativo, no de código — el LIST del flujo de harvest
  ve lo vivo, y D.2 lo verifica con el readback del propio tool. El lead
  lo declaró aceptado y pidió enumerarlo como `RES`.
- H4 (espía de `_envia_texto`, `tests/test_fabrica_f2_biblioteca.py`)
  NO va como `RES`: se encontró cómo asertar el timing sin tocar
  `app/notifica.py` (registrar en vez de asertar dentro del espía) y se
  cerró con test — ver «Sobrevivientes cerrados».

## Comandos exactos

Un mutante a la vez, sobre el árbol limpio (`git status` vacío salvo el
catálogo/tests commiteados):

```bash
# 1. Aplicar a mano (el diff de la fila) con el editor.
# 2. Correr los focales del archivo relevante, p. ej.:
ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
  uv run --frozen python -m pytest -q tests/test_fabrica_f2_hermanas.py
# 3. Copiar el rojo (primera línea E/assert).
# 4. Revertir y comprobar limpieza:
git checkout -- <archivo-mutado> && git status --short --branch
```

Excepciones documentadas: M10/M11/M23 mutan la migración 0038 (el
fixture `db_f2` la aplica desde disco en cada corrida, así que el
mutante rige sin más); M14/H2/H4 se re-aplicaron una segunda vez para
demostrar el rojo del test nuevo/fijado. Cierre: `git status` limpio
tras cada mutante; `git diff origin/master --stat` solo muestra
evidencia y tests (verificado antes del push).

## Sobrevivientes cerrados y tests nuevos

Un solo sobreviviente de catálogo (M14) + los tres hallazgos bajos del
pre-pase de kimi que el brief manda cerrar aquí (H2, H3, H5) + H4
(cerrado al encontrar assert sin tocar `app/`):

| Hallazgo | Tests | Rojo con mutante (verde sin él) |
|---|---|---|
| M14 (dedupe de revalida al goal fresco) | `tests/test_harvest_destino.py::test_r1_revalida_duplicado_en_exacta_resuelta_descarta` (nuevo) | `E AssertionError: el duplicado no se aplica` / `assert 1 == 0` |
| H2 (guardas cola/ids sin test) | `tests/test_reversa_harvest.py::test_r1_plan_cola_no_applied_falla_cerrado`, `::test_r1_plan_sin_ids_falla_cerrado` (nuevos) | ambos `E Failed: DID NOT RAISE ValueError` sin las guardas `app/apply_harvest.py:1150-1157`; el viejo `test_plan_precondiciones_fallan_cerrado` pasa con el mutante (era ciego). Ronda: el 2o caso heredaba la 1a ausencia (hueco); independizado desde el ext original — mutante `if keyword_id is None:` (`:1156`) → `E Failed: DID NOT RAISE ValueError` en el 2o caso (`:324`); verde sin él |
| H3 (suelta sin siembra, asserts vacíos) | `tests/test_fabrica_f2_visibilidad.py::test_a6_campana_suelta_salta_en_skips_pero_no_en_saltos_grupo` (fortalecido, no borrado) | siembra + `skips.termino == 1` + `saltos_grupo == {}` + cero avisos de grupo (conjunción que ni el gemelo ni el flanco-suelta pinean) |
| H5 (DSN fijo en 5 CLI) | helper `tests/test_reversa_harvest.py::_dsn_decide_de(conn)` + 5 sitios | deriva de `_test_dsn()` como el resto de la suite; 0 fijos restantes; CLI verdes. Ronda: vía `make_conninfo(_test_dsn(), dbname=...)` (respeta `?sslmode=...` y forma `keyword=value`; jamás `rsplit('/')`) |
| H4 (espía de timing tragado) | 2 espías fijados en `tests/test_fabrica_f2_biblioteca.py` (registran, asertan fuera) | mutante «avisar dentro del sello» (harvest y negative): `E AssertionError: A.4r1: el aviso sale DESPUES del commit del sello` / `assert <TransactionStatus.INTRANS: 2> == <TransactionStatus.IDLE: 0>`. Ronda: refuerzo con 2a conexión (IDLE no prueba persistencia con autocommit) — el espía registra rastro/veredicto visibles al avisar; mutante-timing → `E AssertionError: ronda H4: el rastro del sello ya es visible al avisar` (`(False,)`) y `E AssertionError: ronda H4: el veredicto del sello ya es visible al avisar` (`'applying' == 'applied'`) |

Cero filas en estado `sobrevive` sin `→ test nuevo`. Ningún test
existente borrado, relajado, `skip` ni `xfail` (H3 se fortaleció; H4/H5
son ediciones mandadas por el brief para fijar cobertura/portabilidad,
verdes antes y después). Ningún mutante reveló bug real en `app/`
(todos los rojos pinean comportamiento esperado): no se tocó `app/`,
`tools/`, `migrations/` ni `ops/` en ningún commit.

## Lo que NO cubre el catálogo y por qué

- M28/M29 (`no discriminable`, arriba): timing del envío en
  `_fase_notifica` y `state` del destino en `--migrar`. Razón: sin
  observador (timing) y sin código que mute (state, por contrato
  operativo + D.2).
- ADV-10 (keyword exacta adoptada que la reversa automática podría
  confundir): preexistente, excluido por el brief de A.3; la reversa
  manual F2 está protegida (plan exige `keyword_id` + `creada=true`).
  No hay mutante F2 que lo toque.
- Duplicados propios múltiples (se prueba la primera): los tests pinean
  `probadas[0]`/`halladas[0]`; el duplicado vivo restante es mismo
  texto/mismo ad group, sin riesgo de dinero ni ruteo. Mutar el índice
  ([0]→[1]) cambiaría ids pineados, no comportamiento — ruido, no señal.
- Paginado >20 en reversa manual: fail-safe por construcción
  (`TOPE_PAGINAS_LIST` → stop); liveness solo verificable en vivo
  (regla 8, D.3). Ningún mutante local discrimina liveness.
- Conflicto keyword/negative preexistente (`conflicto_negative: true`):
  comportamiento elegido y documentado (keyword gana para la siembra de
  F1; `app_decide` sin DELETE por diseño de 0038). No es hueco.
- Candados de arquitectura vs literales adyacentes e imports dinámicos
  (`tools/`): los tests muerden la forma canónica; el candado real de
  escritura es el rol de Postgres, no el regex. Anti-deriva aceptada.
- H1 (ledger desactualizado en el plan): ya no aplica (cerrado en
  `41bc6aa`, posterior al SHA del pre-pase).
- Cobertura del catálogo: 27 filas AC+OBL (13+14) + 2 RES. Los 8
  archivos de evidencia citada (PRs #258, #261, #264, #267, #269, #272,
  #274, #275) se reutilizan citando PR y se re-ejecutaron sobre
  `7a32ec3`/`41bc6aa` (código idéntico): evidencia fresca.
