# Modo de goals con ceremonia (PR #283) — revisión cruzada de kimi

Revisor: kimi, headless, worktree propio, Postgres local, mutantes revertidos, `git status` vacío en ambas pasadas. Lead: 136 → 144 focales verdes con base real; 6 mutantes ejecutados sobre `761a936` (todos mueren) y 3 sobre `8eb5395` (mutante sutil de la terna efectiva, apertura de bid-solo para sueltos, kill switch sin apertura: los tres caen). APPROVE del lead publicado como comentario en el PR #283.

---

# Revisión R.1-bis — kimi — PR #283 — SHA 761a936

## 1. Baseline

Literal: `136 passed, 1 warning in 5.18s` — **0 skipped** (el warning es el `StarletteDeprecationWarning` preexistente). Cierre tras todos los mutantes: `136 passed, 1 warning in 5.29s`.

## 2. Tabla de mutantes del PR

| mutante | veredicto | rojo observado |
|---|---|---|
| `mode` fuera del vocabulario | CONFIRMADO | `E AttributeError: 'object' object has no attribute 'execute'` (llega a I/O; idéntico al declarado) |
| `live` sin ceremonia que escriba (CLI) | CONFIRMADO | `E assert 2 == 0` (cae a ceremonia a medias; spy intacto) |
| `--go` vacío/ausente aceptado | CONFIRMADO | `E assert 0 == 2` ×2 (`go_vacio` + `acepto_sin_go`, ambos escriben) |
| bajar a `shadow`/`off` exigiendo ceremonia | CONFIRMADO | `E KeyError: 'mode'` ×2 (spy vacío: cayó al dry-run; mismo hecho que el «capturado vacío» declarado) |
| regla post-lectura ausente | CONFIRMADO | `E Failed: DID NOT RAISE GoalInvalido` |
| regla post-lectura invertida (exacta) | CONFIRMADO | 2 failed: `con_exacta_sin_terna_ok` (GoalInvalido inesperado) + `sin_exacta_ni_terna_rechaza` |
| tool edita otro grupo (`OR TRUE`) | CONFIRMADO | `Abortar: --esperado 5 != 10 candidatas` (lo frena la ceremonia, no el SQL) |
| tool incluye `scope=platform` | SOBREVIVE | 136 passed. Mutante equivalente: el CHECK `goal_scope_coherente` (0001) impone platform ⇒ `ad_entity_id IS NULL`, y NULL jamás matchea el JOIN — el filtro `scope='campaign'` es defensa en profundidad redundante. El rojo declarado en el PR (`--esperado 5 != 10`) **no se reproduce** con este mutante; la propiedad está garantizada de todos modos |
| tool por nombre | CONFIRMADO (inspección) | sin superficie: `_SQL_GOALS_GRUPO` y el readback joinean solo por `ad_entity_id`/`grupo_id`; cero referencia a nombre/tipo externo |
| huella ignorada | CONFIRMADO | `E Failed: DID NOT RAISE Abortar` (escribe con huella distinta) |
| `--esperado` cuenta «ya está» | CONFIRMADO | 5 failed (los 4 declarados + `fallo_a_mitad` como colateral) |
| seguir tras fallo a mitad | CONFIRMADO | `AssertionError: Regex pattern did not match` — aborta en readback («quedo shadow») y el match endurecido del loop no muerde, tal como declara el PR |
| UPDATE crudo en el tool | CONFIRMADO | `escritura cruda de ads_optimizer_goal en tools/ ... ['tools/goals_modo_grupo.py']` |
| endpoint que acepta `mode` | CONFIRMADO | 200 con `"mode":"live"` persistido vs 422 esperado (mutante temporal en `api_write.py`, revertido) |
| readback imprime lo pedido | CONFIRMADO | `E assert False` (imprime `live`, se leyó `shadow`) |

## 3. Mutantes propios

| mutante | resultado | test o SOBREVIVE | opinión |
|---|---|---|---|
| (a) `--mode live` con ceremonia sobre goal `enabled=false` | comportamiento actual: **escribe** (sonda PG: `mode=live, enabled=false`) | n/a — conducta, no mutante | Aceptable. El gate de gasto real es `enabled`, que jamás tuvo ceremonia por diseño (pause/resume rutinario preexistente); el `live` quedó autorizado por ceremonia al escribirse. Rechazarlo añadiría fricción al flujo normal sin tapar riesgo nuevo |
| (b) regla post-lectura acepta `live` bid-solo sin exacta | SOBREVIVE la suite (136 passed), **pero funcionalmente muerto**: la segunda barrera `_valida_pre_editar` rechaza con `config de harvest incompleta` (sonda) | SOBREVIVE (sin riesgo hoy) | Defensa en profundidad real y verificada. Brecha menor: la rama bid-solo del rechazo no tiene pin de regresión |
| (c) tool toca goals `scope=platform` del mismo platform (UNION) | muere | `test_go_escribe_solo_los_del_grupo`: `Abortar: --esperado 5 != 6 candidatas` | La barrera `--esperado`/huella lo frena antes de escribir nada; el goal de plataforma queda intacto |
| (d) readback desde otra conexión | verificado con sonda: una **segunda conexión** ve `live` tras `edita_goal` | n/a | Cada `edita_goal` hace `conn.commit()` por goal; en el readback no existe ventana «antes del commit». Lo que imprime es lo persistido y visible para terceros |
| (e) `edita_goal(mode="live", harvest_limpia_destino=True)` deja `live` bid-solo sin exacta | código actual: **RECHAZA** (sonda); mutante sutil (`cambios.get(col) or fila[col]`): **SOBREVIVE** y la sonda confirma que escribiría `live` + bid-solo + sin exacta | SOBREVIVE (el mutante grosero de ignorar cambios sí muere por `..._con_terna_efectiva_ok`) | Hoy el código es correcto, pero la interacción `mode` × `limpia_destino` en la misma llamada no tiene test que la pine → ver hallazgo 2 |

## 4. Invariantes (i)–(iv)

- **(i) Cero SQL de escritura fuera de `goals_write`** — VERIFICADO en ambas direcciones: el mutante M13 (UPDATE crudo sembrado en el tool real) hace caer `test_escritura_de_goals_vive_solo_en_goals_write` listando el archivo, y `test_candado_tools_caza_update_crudo_de_goals` (siembra en `tmp_path`, ambos tools) existe y está verde con aserción estricta de listado.
- **(ii) Endpoint sin `mode`** — `CuerpoGoal` es **extra=ignore** (default pydantic, documentado en su docstring). Sonda: body `{"mode":"live","target_acos_pct":33}` → **HTTP 200**, `mode` en DB intacto (`shadow`), `target` sí cambió. El test del PR solo cubre `mode` solo → 422 `edicion vacia`; el caso combinado **no está cubierto**. Riesgo de datos: ninguno (`mode` jamás viaja a `edita_goal`); riesgo de confusión del operador: sí → hallazgo 4.
- **(iii) Kill switch** — `goals set --mode shadow` escribe sin ceremonia (`test_cli_goals_set_mode_bajar_sin_ceremonia_escribe[shadow]` verde) y `_goals_set` solo lee `ORBIT_DSN_ADMIN` (cero DSN de lectura). **Con una excepción probada**: sobre un goal bid-solo el kill switch FALLA → hallazgo 1.
- **(iv) Trigger `ads_optimizer_goal_harvest_coherente` (0038)** — 0038 está en `ORDEN_F2` de los fixtures; los tests PG de `mode` (incluido el go del tool sobre terna completa) y la sonda (cambio de mode sobre goal sin terna) corren con 0038 aplicada y pasan: el trigger no bloquea el cambio de `mode`.

## 5. Diff de tests

`+920 −7` en 4 archivos. Las únicas 7 líneas borradas son la versión vieja de `test_candado_tools_caza_update_crudo_de_goals`, reemplazada por una **estrictamente más fuerte** (siembra la fuga en los dos tools y exige ambos en el listado). Todo lo demás es aditivo. Cero skip/xfail nuevo (el `_skip_db` del archivo nuevo es el gate estándar de `ORBIT_TEST_DSN`; con DSN: 0 skipped).

## 6. Hallazgos numerados con severidad

1. **ALTA (bloquea D.3 / kill switch trabado)** — `edita_goal` rechaza **cualquier** edición sobre un goal **bid-solo**, porque `_valida_pre_editar` corre con `permite_bid_solo=False` salvo que la misma llamada traiga `harvest_limpia_destino=True`. Bid-solo es exactamente el estado en que **D.2 deja al grupo 1** (`harvest_limpia_destino`: campaign/ad_group a NULL, bid intacto), y D.3 depende de D.2 (`plans/fabrica-02.md:413`). Probado con sondas PG (0038 aplicada): (1) `edita_goal(mode="live")` sobre goal bid-solo en grupo **con exacta** → `GoalInvalido: config de harvest incompleta` — o sea, `tools/goals_modo_grupo.py --grupo 1 --mode live` (paso 1 de D.3) aborta en el primer goal; (2) `edita_goal(mode="shadow")` sobre un goal **live bid-solo** → mismo rechazo: **el kill switch de emergencia no funciona** sobre el estado post-D.2. Los tests del go siembran solo terna completa; el caso bid-solo solo se prueba en dry-run (`test_dry_run_terna_bid_solo_y_sin_terna`), nunca en escritura. Dirección de fix: que `_valida_pre_editar` admita bid-solo efectivo cuando la campaña está en grupo (la misma condición que el trigger de 0038), resuelto post-lectura.
2. **MEDIA** — La combinación `mode="live"` + `harvest_limpia_destino=True` en la misma llamada no tiene pin de regresión: el mutante sutil (e) sobrevive a los 136 tests y dejaría un goal `live` bid-solo sin exacta (gasta sin cosechar). El código actual rechaza bien; falta el test que lo selle.
3. **BAJA** — El rechazo de `live` en bid-solo sin exacta tampoco está pineado (mutante (b) sobrevive la suite); hoy lo frena la segunda barrera (`_valida_pre_editar`), pero un test directo lo fijaría contra reordenamientos futuros.
4. **BAJA** — Endpoint: body con `mode` + campo válido pasa 200 ignorando `mode` en silencio (extra=ignore). Sin riesgo de datos; riesgo de confusión del operador. El test negativo solo cubre `mode` solo. Un test que pine «mode ignorado, fila conserva su mode» lo cierra.
5. **BAJA (nota de exactitud del PR)** — La fila «tool incluye `scope=platform`» de la tabla de mutantes declara un rojo (`--esperado 5 != 10`) que no se reproduce: el mutante natural es equivalente por el CHECK `goal_scope_coherente`. La propiedad está garantizada; la tabla del PR debería decirlo.

## 7. Veredicto

**CAMBIOS REQUERIDOS** sobre `761a936`:

1. Hallazgo 1 (ALTA): `edita_goal` debe poder editar (subir a `live` y, crítico, **bajar a `shadow`/`off`**) goals bid-solo en grupo — es el estado real del grupo 1 tras D.2 y el camino explícito de D.3; con test rojo-primero (bid-solo + exacta → `mode` escribe; kill switch bid-solo live → shadow escribe).
2. Hallazgo 2 (MEDIA): test que pine `mode="live"` + `harvest_limpia_destino=True` sin exacta → `GoalInvalido` (mutante (e) debe morir).

Recomendados sin bloquear: hallazgos 3 y 4 (pins de test de una línea cada uno) y la corrección de la fila del mutante scope=platform en la descripción del PR.

`git status --short` final: **vacío** (mutantes revertidos con `git checkout --`, sondas borradas, `__pycache__` purgado; baseline de cierre `136 passed`).



---

# Ronda #283 — kimi — SHA 8eb5395

## 1. Hallazgo 1 (ALTO) — bid-solo post-D.2 editable

Diff leído completo (`app/goals_write.py` +37, 2 archivos de tests +207). Batería limpia sobre 8eb5395: **144 passed**. Las cinco sondas corren como tests nuevos contra PG real y pasan en verde:

- (a) `edita_goal(mode="live")` sobre bid-solo en grupo con exacta → escribe: `test_edita_goal_mode_live_sobre_bid_solo_en_grupo_escribe` ✔
- (b) kill switch `mode="shadow"` sobre live bid-solo → escribe: `test_edita_goal_kill_switch_sobre_bid_solo_live_escribe` ✔
- (c) `edita_goal(target)` sobre bid-solo en grupo → escribe: `test_edita_goal_cualquier_edicion_sobre_bid_solo_en_grupo_escribe` ✔
- (d) suelto bid-solo → sigue rechazado («config de harvest incompleta»): `test_edita_goal_bid_solo_suelto_sigue_rechazado` ✔ (pincha suelto Y platform)
- (e) go del tool sobre los cinco goals en bid-solo → los cinco a live, readback live: `test_go_sobre_grupo_bid_solo_escribe_los_cinco` ✔

Mutante «quitar la apertura de bid-solo» (`permite_bid_solo = harvest_limpia_destino` solo): **4 failed — caen exactamente (a), (b), (c) y (e)**; (d) pasa en verde como debe (el mutante no toca sueltos). Revertido con `git checkout --`. El fix es el correcto: `_bid_solo_efectivo` sobre la terna EFECTIVA + apertura solo con `scope='campaign'` + `ad_entity_id` + EXISTS en `campana_grupo_rol` (espejo del estado 3 del trigger 0038).

## 2. Hallazgo 2 (MEDIO) — mutante sutil en la terna efectiva

Apliqué `cambios.get(col) or fila[col]` en la terna de `_valida_live_destino_post_lectura` (app/goals_write.py:166-170): el test nuevo `test_edita_goal_live_mas_limpia_destino_sin_exacta_rechaza` **cae** (`DID NOT RAISE` — el mutante resucita la terna vieja y dejaría un live bid-solo sin exacta). Revertido. El pin `mode=live + harvest_limpia_destino` del hallazgo queda cubierto.

## 3. Hallazgo 3 (MEDIO) — regla post-lectura pineada

Mutante «quitar la llamada a `_valida_live_destino_post_lectura`» (app/goals_write.py:412-413): `test_edita_goal_live_sobre_bid_solo_sin_exacta_lo_frena_primera_barrera` **cae** — desde el fix post-D.2 la segunda barrera (`_valida_pre_editar`) admite bid-solo en grupo, así que sin la primera barrera el live bid-solo sin exacta escribiría en silencio. El test discrimina correctamente cuál barrera frena. Revertido.

## 4. Hallazgo 4 (BAJO) — elección de Muse: pin de `mode` ignorado

Muse eligió **pin de mode ignorado** (no `extra=forbid`): `CuerpoGoal` sigue sin campo `mode` (app/api_write.py:172-187) y el endpoint no lo pasa a `edita_goal` (app/api_write.py:353-365). El test nuevo `test_endpoint_pg_mode_con_otro_campo_200_pero_mode_ignorado` sella: body con `mode` + `target_acos_pct` → 200, target sí cambia, mode queda en `shadow`. El mutante (agregar `mode` a `CuerpoGoal` y pasarlo en el dispatch) hace caer **ese test y además el viejo** `test_endpoint_pg_body_con_mode_422_y_fila_intacta` (2 failed). Revertido, `api_write.py` intacto.

## 5. Hallazgo 5 (BAJO) — fila scope=platform en el cuerpo del PR

`gh pr view 283 --json body`: la fila quedó corregida a **EQUIVALENTE** con la explicación honesta — por el CHECK `goal_scope_coherente` (0001), `platform ⇒ ad_entity_id NULL`, que jamás matchea el JOIN por `ad_entity_id`; el rojo documentado original venía de un `OR g.scope='platform'` más agresivo, no del mutante natural. Correcto y bien razonado.

## 6. Apertura acotada: ni platform ni sueltos, I/O solo si bid-solo

- Mutante «quitar la condición de grupo» (EXISTS de `campana_grupo_rol` fuera, scope/ad_entity intactos): `test_edita_goal_bid_solo_suelto_sigue_rechazado` **cae** (el suelto abriría). La condición `scope='campaign'` residual sigue frenando platform, como corresponde al espejo del trigger. Revertido.
- Sonda de I/O con conexión espía (script directo contra PG, fuera del harness de tests): edición sobre goal con **terna completa** → 0 queries al EXISTS extra; misma edición sobre goal **bid-solo en grupo** → exactamente 1. La query corre solo cuando la terna efectiva es bid-solo; el resto de ediciones no paga I/O extra.

## Veredicto: APPROVE sobre 8eb5395

Los cinco hallazgos quedan resueltos y pineados con tests que demuestran matar a su mutante (cada mutante aplicado → rojo → `git checkout --`). Batería final limpia sobre 8eb5395: 144 passed. `__pycache__` purgado tras cada ronda.

`git status --short` final: **vacío**.
