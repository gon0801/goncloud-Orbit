# E/R.1 — Pre-pase independiente de kimi sobre `7a32ec3` (noche del 2026-09-15)

**Qué es esto.** La mitad del revisor de la fila R.1, corrida de noche por el
lead como pre-pase: kimi (`kimi-code` 0.39.1, prompt no interactivo `-p`)
leyó la Fase A completa en un worktree aparte, en modo solo lectura, sobre el
SHA de código `7a32ec3`, con la lista de la fila R.1 como checklist (los
catorce mutantes obligatorios, guard, dinero, append-only, quota, reversa,
GRANTs por columna, replay, fail-closed en LIST), los residuales declarados en
A.3–A.6 y el poder discriminante de los tests. **No ejecutó tests** (sin
Postgres en el worktree): sus veredictos son análisis estático con citas
`archivo:línea`. `git status` del worktree quedó vacío al terminar: no tocó
nada.

**Qué NO es.** No es el APPROVE de R.1. La fila exige el catálogo enumerado
del implementador (`mutantes.md`, lo entrega Muse en el bloque 1 del día), la
re-mutación de kimi contra ese catálogo y la auditoría del lead; el loop
cierra solo con el APPROVE del lead sobre el SHA del PR de R.1.

El prompt exacto vive en el PR de la noche (descripción) y el informe de
kimi va tal cual abajo, sin editar. La auditoría del lead está al final.

---

# Revisión independiente R.1 — FABRICA 02 Fase A — SHA 7a32ec3

## 1. Veredicto preliminar

**APPROVE candidato (sin hallazgos altos ni medios).** Los catorce mutantes obligatorios tienen el código correcto hoy y un test existente que los mata; los ocho invariantes transversales cumplen con evidencia estática; los residuales declarados son eso, residuales — ninguno esconde un bug de dinero, Amazon, datos o quota.

## 2. Hallazgos

Sin hallazgos **altos**. Sin hallazgos **medios**. Cinco **bajas** (calidad de tests y ledger), ninguna reabre código:

- **H1 — Severidad: baja. Dónde:** `plans/fabrica-02.md:397-398` y `:600`. **Qué:** en el SHA bajo revisión las filas A.5 y A.6 dicen `cc:TODO` y la sección "Estado para la siguiente sesión" dice "Sigue A.5", pero ambas fases están mergeadas (18997de, 959e742, 7a32ec3). El ledger del plan está desactualizado; lo cierra el propio PR de R.1. **Evidencia:** `| A.5 | ... | cc:TODO |` vs `git log` con los tres squashes. **Cómo demostrarlo:** lectura directa del archivo contra `git log --oneline -3`.
- **H2 — Severidad: baja. Dónde:** `tests/test_reversa_harvest.py:217-246`. **Qué:** `test_plan_precondiciones_fallan_cerrado` anuncia cuatro precondiciones y solo ejercita dos (`solo acepta done`, `sin verify_ok`); las guardas "cola no applied" (`app/apply_harvest.py:1150-1152`) y "sin ids" (`:1156-1157`) podrían borrarse con la suite verde. Residual CodeRabbit declarado, confirmado. **Cómo demostrarlo:** borrar esas dos guardas; ningún test cae.
- **H3 — Severidad: baja. Dónde:** `tests/test_fabrica_f2_visibilidad.py:104-118`. **Qué:** `test_a6_campana_suelta_salta_en_skips_pero_no_en_saltos_grupo` nunca siembra términos, así que ningún skip se genera: el camino que el nombre anuncia no se ejercita y los asserts se cumplen vacíos. El test gemelo (`:121-135`, con término sembrado) sí discrimina, así que la cobertura real existe; este es ruido. **Cómo demostrarlo:** el cuerpo del test no llama `_siembra_terminos`; `saltos_grupo == {}` vale con y sin el comportamiento.
- **H4 — Severidad: baja. Dónde:** `tests/test_fabrica_f2_biblioteca.py:1015-1106`. **Qué:** el assert `transaction_status == IDLE` del espía `_envia_texto` es letra muerta: si el código avisara dentro de la transacción, el `AssertionError` sería tragado por el `try/except` fail-silent del sender (`app/notifica.py:749-767`) y el test fallaría por el conteo de envíos con mensaje engañoso. Residual declarado, confirmado. **Cómo demostrarlo:** mutante que mueve `avisa_si_fallo` dentro del sello: el test cae, pero por `"el sender nuevo se llamo una vez"`, no por el timing.
- **H5 — Severidad: baja. Dónde:** `tests/test_reversa_harvest.py:677-960` (cinco tests del CLI). **Qué:** arman `ORBIT_DSN_DECIDE` con `orbit:orbit@localhost:5432` fijo en vez de derivarlo de `_test_dsn()`; solo son verdes en ese entorno concreto. Residual declarado, confirmado.

## 3. Tabla de los catorce obligatorios

| # | ¿Vulnerable hoy? | Test que lo mata (archivo::test) | Nota |
|---|---|---|---|
| 1 | No: los callers reconstruyen el dict completo (`apply_harvest.py:1842` lo copia de `external_ids` y `:1868/1882/1937/1965` lo pasan entero a `_avanza`) | tests/test_fabrica_f2_hermanas.py::test_merge_conserva_exitos_previos (verificado: ciclo 2 conserva h1/h3, `:1597-1600`) | Cubre "perder"; "resucitar" no es alcanzable (nunca se borran claves) |
| 2 | No: `adGroupIdFilter` se construye en cada página (`apply_harvest.py:572-574`) | tests/test_fabrica_f2_hermanas.py::test_lista_filtrada_filtro_en_cada_pagina_y_senales_de_ambiguedad (puro, captura bodies de 2 páginas) | Test puro, corre sin DB |
| 3 | No: `_identidad`/`_coincidencias` saltan `ARCHIVED` (`apply_harvest.py:607-608`, `:628`) | tests/test_fabrica_f2_hermanas.py::test_identidad_adopta_enabled_exact_e_ignora_archived_y_phrase; puro: tests/test_apply_harvest.py::test_identidad_ignora_archived_el_delete_archiva | Dos capas |
| 4 | No: la cascada exige readback previo (`apply_harvest.py:1993-1997`); el sello va tras `_identidad` (`:1596-1633`) | tests/test_fabrica_f2_hermanas.py::test_sello_durable_visible_desde_segunda_conexion_antes_del_primer_post; tests/test_fabrica_f2_biblioteca.py::test_keyword_ausente_en_readback_cero_filas | `verify_ok` solo nace del readback |
| 5 | No: keyword → hermanas por ROLES_DISCOVERY → origen último (`apply_harvest.py:1185-1219`) | tests/test_reversa_harvest.py::test_plan_orden_canonical_excluye_adoptadas; ::test_ejecuta_orden_filas_y_readback; ::test_fallo_en_hermana_detiene_y_origen_intacto | Orden del plan y de los DELETE asertados |
| 6 | No: `quota_cobrada=False` literal en `_post_hermana` (`apply_harvest.py:1789`) | tests/test_fabrica_f2_hermanas.py::test_seq_global_monotonica_en_flujo_de_hermanas (verificado: `[True, False, False, False, False]` y `used == 1`, `:1454-1459`) | Único candado (el esquema no tiene CHECK) |
| 7 | No: statements sin columnas de dinero (`app/biblioteca.py:88-98`); sin COALESCE en `_monto`/`_contexto_congelado` | tests/test_fabrica_f2_biblioteca.py::test_statements_sin_dinero (estático) + ::test_harvest_grupo_aprende_keyword_en_sello (NULL reales) | El GRANT no acota dinero: lo garantiza la app (declarado en 0038:256-259) |
| 8 | No: resolución por `campana_grupo_rol.rol` (`harvest_destino.py:93-105`) | tests/test_harvest_destino.py::test_a1_grupo_con_terna_null_postea_en_exacta_del_grupo (verificado: señuelo externo llamado `category_exact`, POST al ad group del grupo, `:280`, `:311`) | Señuelo adversario explícito |
| 9 | No: terna de scope campaign contradictoria → `SaltoHarvest` antes que destino (`harvest_destino.py:177-183`) | tests/test_harvest_destino.py::test_a1_terna_campaign_distinta_es_inconsistente_y_cero_http | Aserta cero HTTP |
| 10 | No: `GRANT USAGE ON SEQUENCE` presente (0038:246-247) | tests/test_fabrica_0038.py::test_0038_sin_usage_en_secuencia_la_migracion_truena (verificado: aplica la migración mutada y exige `InsufficientPrivilege`, `:840-849`) | Mutante ejecutado en el propio test |
| 11 | No: en el predicado del índice (0038:79-81) y en `_SQL_JOB_EXISTENTE` (`apply_harvest.py:165`) | tests/test_fabrica_0038.py::test_0038_fases_en_vuelo_iguales_al_indice_real (cruzado contra `pg_index.indpred`) + ::test_0038_en_vuelo_bloquea_en_hermanas_y_libera_en_done | |
| 12 | No: presente en `_SQL_JOBS_EN_VUELO` (`apply_harvest_reconciliacion.py:54`) y `_SQL_JOB_EN_VUELO_DE` (`:92`) | tests/test_fabrica_f2_hermanas.py::test_reanudacion_retoma_indice_y_huerfanas_no_cierra | Sin la fase, el job no se retoma y la fila applying se cierra huérfana |
| 13 | No: `completa` se deriva del destino resuelto (`cycle.py:707-712`) | tests/test_harvest_destino.py::test_a1_ciclo_tras_limpiar_terna_congela_destino | Bid-solo con terna NULL cosecha por grupo |
| 14 | No: dedupe contra `destino.campaign_external` (`cycle.py:1270-1273`) | tests/test_harvest_destino.py::test_a1_termino_en_exacta_del_grupo_es_duplicado | Goal de plataforma apunta a otra campaña sin el término |

Cero SIN TEST. Verifiqué personalmente los tests de los mutantes 1, 6, 8 y 10 (cuerpo y aserciones); los demás descansan en la lectura del explorador con citas, no en ejecución.

## 4. Invariantes transversales

| Invariante | Veredicto | Evidencia |
|---|---|---|
| GUARD (ningún verbo nuevo a Amazon) | cumple | `git diff 2090edf..HEAD -- app/ads/write.py app/ads/client.py` vacío; allowlists intactas (`write.py:103-117`, `client.py:61-79`); F2 solo reusa `list_sellado`, `crear_negative_exacto`, `crear_keyword_exacta`, `borrar_keyword/negative`; `tools/reversa_harvest.py:112` usa `apply._cliente_reversa`; `tools/harvest_excepcion.py` y `app/biblioteca.py` cero HTTP |
| DINERO | cumple | statements de biblioteca sin `cost`/`moneda`/`revenue` (`biblioteca.py:88-98`); bid del POST = `decision.new_value` clampeado con floor/ceiling congelados (`apply_harvest.py:1513-1528`); moneda cruzada contra el congelado, nunca inventada (`:856-857`) |
| APPEND-ONLY | cumple | ledger: INSERT pre-HTTP + sello NULL→valor una vez (`apply.py:783-807`, `:851-881`; `_guarda_ack` solo con `finished_at IS NULL`, `:859-874`); bibliotecas: solo INSERT/upsert de `updated_at` (0038:298-339); nada borra historia en código de app |
| QUOTA | cumple | 1 unidad por harvest (`apply_harvest.py:2014`); hermanas con `quota_cobrada=False` y tope propio por (decisión, adGroupId) (`apply.py:799-802`, `:815-823`); reintentos en reconciliación solo cobran si la cola sigue `released` (`apply_harvest_reconciliacion.py:530-531`); las hermanas jamás llaman `consume_quota` de negative |
| REVERSA | cumple | orden canónico (`apply_harvest.py:1185-1219`); readback por ID entre deletes (`:1305-1328`); stop al primer fallo (`:1293-1304`); reanudación por `_reversa_confirmada` (`:1223-1233`); adoptadas (`creada=false`) fuera del plan (`:1190`) |
| GRANTS POR COLUMNA | cumple | 0038:245-248: INSERT en ambas, USAGE de secuencias, `UPDATE (updated_at)` solo en keyword; el DO prueba DELETE/UPDATE(origen/texto/first_seen_at/cost)/escritura en `harvest_excepcion` rechazados bajo `SET ROLE app_decide` (0038:346-395) |
| REPLAY | cumple | congelado con `resuelto_por`/`grupo_id` (`cycle.py:713-721`, `:870`); replay lee las 4 claves congeladas (`optimizer/replay.py:129-137`); apply re-valida contra la exacta vigente y jamás re-rutea (`apply_harvest.py:858-872`) |
| FAIL-CLOSED en LIST | cumple | `_lista_filtrada`: truncado/ambiguo/unknown → cero POST (`apply_harvest.py:551-595`, `:1874-1883`, `:1932-1940`); página malformada o enum desconocido → inválida (`:497-511`); token repetido → ambiguo (`:592-593`) |

## 5. Residuales declarados

- **ADV-10** (keyword exacta adoptada que la reversa automática podría borrar por identidad) → sigue aceptable: preexistente, y la reversa manual F2 está protegida (plan exige `keyword_id` de `external_ids` y hermanas solo con `creada=true`, `apply_harvest.py:1154-1157`, `:1190`).
- **Duplicados múltiples → se prueba la primera** → aceptable: `probadas[0]`/`halladas[0]` (`apply_harvest.py:1898`, `:1920`, `:1959`); el duplicado vivo restante es mismo texto/mismo ad group, sin riesgo de dinero ni de ruteo.
- **Paginado >20 en reversa manual** → aceptable: `TOPE_PAGINAS_LIST=20` devuelve no-completa → stop fail-safe (`apply_harvest.py:548`); liveness queda para D.3, como se declaró.
- **Hilos CodeRabbit menores de #267** → aceptables, confirmados reales: son H2 y H5 arriba (cobertura, no bugs de producción).
- **Conflicto keyword/negative preexistente** → aceptable: es comportamiento elegido y documentado (`biblioteca.py:36-40`, `:271-274`); `app_decide` no tiene DELETE por diseño y keyword gana para la siembra de F1.
- **Espía de tests de fallo inyectado** → aceptable como residual, confirmado (H4): el código de producción sí avisa fuera del commit (`apply_harvest.py:1651-1661`); lo débil es el test.
- **Candados de arquitectura vs literales adyacentes/imports dinámicos** → aceptables como anti-deriva: `test_escritura_de_goals_vive_solo_en_goals_write` y `test_harvest_excepcion_solo_importa_lo_declarado` muerden la forma canónica, no evasiones; el candado real de escritura es el rol de Postgres, no el regex.
- **Envío en `_fase_notifica` sin test conductual** → residual superado: existen tests conductuales del sender dentro de `_fase_notifica` (tests/test_notifica.py::test_fase_notifica_mapea_salto_destino_a_nota_y_acumula, ::test_fase_notifica_salto_destino_canal_caido_deja_nota, ::test_notifica_destino_grupo_envia_y_tumba). Si la nota de A.6 lo declaraba, está cerrado de facto.

## 6. Tests con poco poder discriminante

1. **tests/test_fabrica_f2_visibilidad.py::test_a6_campana_suelta_salta_en_skips_pero_no_en_saltos_grupo** (`:104-118`) — el peor: sin `_siembra_terminos`, ningún término se evalúa; `saltos_grupo == {}` pasa con y sin el comportamiento, el segundo assert está implicado por el primero y el tercero aserta el propio fixture. Lo salva su gemelo con término (`:121-135`).
2. **tests/test_reversa_harvest.py::test_plan_precondiciones_fallan_cerrado** (`:217-246`) — anuncia cuatro precondiciones y ejercita dos; las guardas "cola no applied" y "sin ids" de `plan_reversa_harvest` no tienen cobertura en ningún test.
3. **tests/test_fabrica_f2_biblioteca.py::test_fallo_inyectado_keyword_sello_intacto / test_fallo_inyectado_negative_veredicto_intacto** (`:1015-1106`) — el assert de timing (IDLE post-commit) es inalcanzable como aserción: el `try/except` fail-silent del sender se traga el `AssertionError` y el test reportaría la causa equivocada. El resto de ambos tests (rastro, cola, verify) sí es fuerte.

Mención: `tests/test_fabrica_f2.py::test_f2_encola_y_aplicador_reutilizados` (`assert aplicador is not None`) y los cinco tests del CLI con DSN fijo (H5) son débiles pero de humo declarado.

## 7. Lo que no pude verificar

- **No ejecuté nada**: sin Postgres en este worktree, ningún test corrió; los veredictos "el test mata al mutante" son análisis estático del fixture contra las aserciones (con cuatro verificados por mí en detalle: mutantes 1, 6, 8, 10), no re-mutación ejecutada.
- **`E/R.1/mutantes.md` no existe** en este SHA (`docs/evidencia/fabrica-02/` solo tiene `0.1/` y `0.2/`): el catálogo enumerado que el DoD de R.1 exige al implementador aún no se entregó — esperable si viene con el PR de R.1, pero su auditoría queda pendiente por definición.
- **Las notas de cierre de A.5/A.6 no están en `plans/fabrica-02.md`** (H1): tomé sus residuales del enunciado de la revisión y de los títulos de commit, no de notas `cc:完了` en el plan.
- **El comportamiento del trigger `apply_attempt_solo_sella_resultado` de 0002** (que admite el sello columna a columna que usa `_guarda_ack`) lo di por sentado desde los COMMENTs de 0038; no lo re-derivé de 0002.
- **El set de columnas de `keyword_biblioteca`/`negative_biblioteca` en 0018** (p. ej. que negative no tiene `updated_at`) lo tomé de los comentarios de 0038 sin releer 0018.
- **Nada en vivo**: la efectividad del negativo por texto en product targeting (residual de 0.1: aceptado ≠ efectivo) y el ensayo real de la reversa quedan para D.3, fuera del alcance de esta revisión.


---

# Auditoría del lead sobre el pre-pase (2026-09-15/16, noche)

Alcance de esta auditoría: contrastar la salida de kimi contra el código en
`7a32ec3` y contra el mapa propio del lead de los catorce obligatorios. No
sustituye la re-mutación del bloque 1 del día: kimi vuelve a mutar contra el
catálogo que entregue Muse, y el loop cierra solo con el APPROVE del lead.

## Mapa del lead: obligatorio → test que debería matarlo (por nombre, sin ejecutar)

| # | Mutante obligatorio | Test candidato (a confirmar ejecutándolo en R.1) |
|---|---|---|
| 1 | `_avanza` merge superficial de `hermanas` | `tests/test_fabrica_f2_hermanas.py::test_merge_conserva_exitos_previos`, `::test_roster_tres_hermanas_por_cada_rol_origen` |
| 2 | LIST sin `adGroupIdFilter` | `tests/test_fabrica_f2_hermanas.py::test_dos_barridos_batched_con_filtro_en_cada_pagina`, `tests/test_fabrica_f2.py::test_f2_list_honra_adgroupidfilter` |
| 3 | LIST que cuenta `ARCHIVED` | `tests/test_fabrica_f2_hermanas.py::test_identidad_adopta_enabled_exact_e_ignora_archived_y_phrase`, `tests/test_apply_harvest.py::test_identidad_ignora_archived_el_delete_archiva` |
| 4 | Hermanas antes del readback de keyword | `tests/test_fabrica_f2_hermanas.py::test_sello_durable_visible_desde_segunda_conexion_antes_del_primer_post`, `tests/test_fabrica_f2_biblioteca.py::test_keyword_visible_desde_segunda_conexion_antes_del_primer_post` |
| 5 | Reversa origen-antes-que-hermanos | `tests/test_apply_harvest.py::test_orden_reversa_completa_keyword_primero_negativo_despues`, `tests/test_reversa_harvest.py::test_readback_vivo_detiene_antes_de_seguir` (orden con hermanas: confirmar en `test_reversa_harvest.py`) |
| 6 | `quota_cobrada=True` en filas `hermana` | `tests/test_fabrica_0038.py::test_0038_hermana_en_ledger_sin_quota_y_sin_decision_truena`, `tests/test_fabrica_f2_hermanas.py::test_quota_no_se_recobra_en_reintentos_de_higiene` |
| 7 | `COALESCE` de moneda | `tests/test_fabrica_f2_biblioteca.py::test_statements_sin_dinero`, `tests/test_apply_harvest.py::test_bid_sugerido_403_deja_el_default_del_goal_clampeado` |
| 8 | Resolutor por nombre | `tests/test_harvest_destino.py::test_a1_grupo_con_terna_null_postea_en_exacta_del_grupo` (fixture con señuelo `category_exact` fuera del grupo, A.1 a) |
| 9 | Fallback al goal con terna presente | `tests/test_harvest_destino.py::test_a1_terna_campaign_distinta_es_inconsistente_y_cero_http` |
| 10 | `USAGE` de secuencia ausente | `tests/test_fabrica_0038.py::test_0038_sin_usage_en_secuencia_la_migracion_truena`, `tests/test_fabrica_f2_biblioteca.py::test_rol_app_decide_escribe_y_prohibe` |
| 11 | Fase nueva fuera del índice parcial | `tests/test_fabrica_0038.py::test_0038_en_vuelo_bloquea_en_hermanas_y_libera_en_done`, `::test_0038_fases_en_vuelo_iguales_al_indice_real` |
| 12 | Fase nueva fuera de los SELECT de reconciliación | `tests/test_fabrica_f2_biblioteca.py::test_reconciliacion_de_job_en_hermanas_negadas_no_reescribe`, `tests/test_fabrica_f2_hermanas.py::test_reanudacion_retoma_indice_y_huerfanas_no_cierra` |
| 13 | `completa` derivada de la terna | `tests/test_harvest_destino.py::test_a1_grupo_con_terna_null_postea_en_exacta_del_grupo` (bid-solo cosecha por grupo) |
| 14 | Dedupe apuntado al goal | `tests/test_harvest_destino.py::test_a1_terna_limpiada_tras_decidir_apply_y_replay_usan_congelado`, `::test_a1_bid_congelado_viaja_al_post_tras_limpiar_terna` |

Lo que este mapa NO prueba: que esos tests discriminen de verdad. Eso es lo
que el catálogo de Muse demuestra con el rojo literal por mutante, y lo que
kimi re-muta.

## Contraste con la salida de kimi

Verificado por el lead contra `7a32ec3`, a mano, después de leer el informe:

- **Los diez tests que kimi cita y que no estaban en el mapa del lead existen**
  con ese nombre exacto: `test_a1_ciclo_tras_limpiar_terna_congela_destino`,
  `test_a1_termino_en_exacta_del_grupo_es_duplicado`,
  `test_plan_orden_canonical_excluye_adoptadas`,
  `test_ejecuta_orden_filas_y_readback`,
  `test_fallo_en_hermana_detiene_y_origen_intacto`,
  `test_seq_global_monotonica_en_flujo_de_hermanas`,
  `test_keyword_ausente_en_readback_cero_filas`, y los tres de
  `tests/test_notifica.py` sobre `_fase_notifica`.
- **Mutante 13**: `app/cycle.py:707-721` deriva `completa` del destino
  resuelto (`bid`/`moneda` en `None` → `completa = False`), no de la terna.
  Confirmado.
- **GUARD**: `git diff 2090edf..7a32ec3 -- app/ads/write.py app/ads/client.py`
  vacío. Confirmado: cero verbos nuevos.
- **H2**: las guardas «cola no applied» y «sin keyword_id o negative_id» de
  `plan_reversa_harvest` (`app/apply_harvest.py:1150-1157`) existen y
  `test_plan_precondiciones_fallan_cerrado` solo aserta `solo acepta done` y
  `sin verify_ok`. Confirmado: dos guardas sin test.
- **H3**: el cuerpo de `test_a6_campana_suelta_salta_en_skips_pero_no_en_saltos_grupo`
  no siembra términos; los asserts se cumplen vacíos. Confirmado.
- **H1** ya no aplica: las filas A.5 y A.6 se cerraron en `41bc6aa` (PR #276),
  posterior al SHA revisado.

Mapa del lead vs tabla de kimi: coinciden en los obligatorios 1, 2, 3, 4, 6,
7, 8, 9, 10, 11 y 12; para 5, 13 y 14 kimi señaló tests más específicos que
los del mapa (los tres de `test_reversa_harvest.py`, y los dos `test_a1_*` de
`test_harvest_destino.py`). Ninguna discrepancia de fondo.

## Decisiones del lead

1. **Veredicto del pre-pase: coincido con «APPROVE candidato».** Sin
   hallazgos altos ni medios; los cinco bajos son de tests, no de producción.
2. **H2, H3 y H5 se cierran en el PR de R.1**, no se declaran: son
   exactamente lo que la fila pide («un sobreviviente se cierra con test en el
   mismo PR»). Van como sección obligatoria en
   `plans/brief-fabrica-02-r1-muse.md`.
3. **H4 se declara como residual `RES`** en el catálogo: el código de
   producción avisa fuera del commit; lo débil es el espía del test, y
   arreglarlo bien exige tocar el contrato fail-silent del sender, fuera del
   alcance de R.1. Muse lo anota con estado `no discriminable`; si encuentra
   una forma de asertar el timing sin cambiar `app/notifica.py`, mejor.
4. **El residual «envío en `_fase_notifica` sin test conductual» pasa a
   verificación de Muse** en el catálogo: kimi dice que los tres tests de
   `test_notifica.py` ya lo cubren; Muse lo demuestra con el mutante «enviar
   dentro de la transacción» y su rojo, o lo deja como `RES`.
5. **Pendiente para el bloque 1 del día**: kimi re-muta ejecutando contra
   el catálogo de Muse (esta noche no corrió nada), y el lead muta por su
   cuenta antes del APPROVE.
