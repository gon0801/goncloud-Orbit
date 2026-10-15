# S.5: reversa en lote — evidencia

Rama: `rp02/sec3-seguridad-goals`, sobre S.4 (`e362e99`). Fuentes (en
orden): fila S.5 de `plans/repricing-02.md` (DoD de 5 cláusulas),
`plans/repricing-02-construccion.md` S.5 (líneas 592-609), bosquejo V
(`docs/evidencia/repricing-02/diseno/bosquejo.py` 1872-1875) y diseño
`Interruptores` + `failure mode` + `datos.sql` §8 (`sin_efecto`) e I
(`Seleccion`/`planear_reversa`/`revertir_lote`, 1118-1252).
TDD: las 9 pruebas nuevas se escribieron primero y se miraron fallar
por la razón correcta antes de implementar (ver §2).

Comando base (se omite por brevedad):

```console
$ PATH=/opt/homebrew/bin:$PATH ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
  PYTHONPATH=. .venv/bin/python -m pytest -q -rs -p no:cacheprovider
```

## 1. Archivos

Tocados (4):
`app/precio/cambios.py` (`SeleccionPorCambios`/`PorFecha`/`PorCorrida`,
`FilaReversa`/`PlanReversa`, `planear_reversa` solo-base con huella de
selección + (cambio_id, volver_a), `revertir_lote` con claim
`precio:<platform>` compartido con la corrida, aborto live por universo
con goal fijo `live`, cierre `confirmado`/`lectura_viva` del `enviado`
con vivo aplicado, salto por fila con vivo movido, `lote_reversa` =
huella en cada reversa; `revertir` adelgazado sobre `_revertir_fila`
compartido, sin cambio de conducta),
`tools/precio_reversa.py` (reescrito como despachador: `--cambio-id` /
`--fecha` + `--platform` / `--corrida`, mercado de
`precio_mercados.abrir`, sin `app.spapi.*`),
`tests/test_precio_write.py` (9 pruebas S.5 + ayudantes
`ORDEN59S5`/`_db59`/`_config59`/`_goal59`/`_decision59`/`_cambio59`/
`_semilla59`/`_MercadoLote`; archivo migrado de ORDEN39C a ORDEN59S5;
sección vieja del tool reescrita: 12 adaptadas, 10 retiradas por
premisa eliminada, ver §4),
`tests/test_architecture.py` (allowlist de imports del tool reescrita,
`_ESCRIBE_CAMBIOS` + `ads_optimizer_lock`, entrada de tamaño para
`app/precio/cambios.py` con razón).
Contratos intactos: `app/precio/tipos.py`, `puerto.py`, `config.py`,
`app/precio_mercados.py`, `app/estimacion_universo.py`, migraciones
(cero ediciones). Sin red real en ninguna prueba (falsos y
`_MercadoLote` / `httpx.MockTransport`).

## 2. Rojo TDD (antes de implementar)

```console
$ pytest tests/test_precio_write.py -k "test_s5_"
8 failed, 90 deselected in 0.77s
# Rojas (razón correcta):
# - 5 × ImportError: cannot import name 'SeleccionPorCambios' /
#   'CorridaEnCurso' from 'app.precio.cambios' (API inexistente)
# - 3 × TypeError: main() got an unexpected keyword argument 'mercado'
#   (tool sin despachador)
```

## 3. Verde final (árbol limpio, mutantes revertidos)

```console
$ pytest tests/test_precio_write.py tests/test_architecture.py
184 passed in 14.08s
$ ruff check app/precio/cambios.py tools/precio_reversa.py \
  tests/test_precio_write.py tests/test_architecture.py
All checks passed!
$ ruff format --check (mismos 4 archivos)
4 files already formatted
```

DoD S.5, cláusula por cláusula (prueba que la cubre):
1. El plan no gasta cuota → `test_s5_plan_no_gasta_cuota`
   (`lecturas == 0`, `escrituras == []`, contenido literal del plan).
2. Precio cambiado plan→go salta la fila y el lote sigue →
   `test_s5_precio_cambiado_salta_fila_y_lote_sigue`.
3. Enviado con precio aplicado se cierra y se revierte el mismo día →
   `test_s5_enviado_con_precio_aplicado_cierra_y_revierte_mismo_dia`
   (`confirmado`/`lectura_viva`, reversa con `lote_reversa` = huella y
   `enviado_at` del mismo día UTC).
4. Universo del lote en live aborta → `test_s5_universo_en_live_aborta`
   (cero toques, cero filas; con global o universo fuera de live pasa).
5. Reversa y corrida no corren a la vez →
   `test_s5_reversa_y_corrida_no_corren_a_la_vez` (ambos sentidos sobre
   el mismo claim + advisory).
Selectores: `test_s5_tool_fecha_revierte_enviados_del_dia`,
`test_s5_tool_corrida_revierte_lote_de_corrida`,
`test_s5_tool_ids_validan_existencia_y_plataforma`.
Robustez del lote: `test_s5_fallo_al_escribir_sella_error_y_lote_sigue`.

## 4. Pruebas viejas del tool (premisa A.3 → S.5)

Adaptadas (12, mismo nombre salvo indicación): dry-run imprime plan
(ahora también `lecturas == 0`), r1-A7c formato, r4-G8 ×2, huella mala,
forma sellada (mercado real inyectado, sin monkeypatch del cuerpo),
r2-B1 pendiente durable (mercado real inyectado con `_cuerpo_falso`),
r3-K7 rc 1 (`patch="rechazo"`), r4-G3 (renombrada a
`test_r4_g3_sin_sku_salta_en_el_go`: el plan lista, el go salta),
r4-G6 solo saltados, r5-L3.
Retiradas (10): abierto-salta-en-plan (premisa invertida: el enviado
ahora se cierra y revierte, DoD 3), A7b un-escritor-por-operación (el
tool ya no construye escritores), plan-salta-sin-abortar y
plan-no-construye-escritor (el plan ya no lee el vivo), r1-M T1 (dup
de r4-G8), r1-M T4 y r3-K1-tool (salto-por-fila cubierto en DoD 2 y
núcleo compartido), r3-K6 (el tool ya no lee el vivo; la explosión al
escribir se fija en `test_s5_fallo_al_escribir_*`), r4-G3-lote `[error]`
(línea `[error]` reemplazada por `[hecho] estado=error`), r6-C5 cubo
(el tool ya no trae cubo; el límite vive en los clientes inyectados),
`test_tool_cambio_inexistente_aborta` (cubierto por
`test_s5_tool_ids_*`), fixture `_go_sin_dormir` (sin `sleep` en `main`).

## 5. Desviaciones y decisiones (con razón)

1. Slot de goal = `live` en el aborto (encargo; guía muda): la reversa
   es manual y el dueño ya autorizó, el goal no restringe. Fijado en
   `test_s5_universo_en_live_aborta`: con global+universo live aborta
   aunque… (el esquema impide cambios reales bajo decisión shadow, así
   que el pin es: si el slot fuera shadow/off el efectivo nunca sería
   live y no habría aborto que probar; el mutante M4 lo demuestra).
2. `--corrida` resuelve (platform, fecha) de la corrida: ni la corrida
   ni `aplicar` estampan `corrida_id` en cambios/decisiones, y
   estamparlo es fuera del alcance S.5. Fijado en
   `test_s5_tool_corrida_revierte_lote_de_corrida`.
3. Sin canal conocido no hay universo que medir (precedente corrida:
   el universo no restringe). Sin config legible, `ValueError`
   fail-closed (no se actúa a ciegas).
4. `--cambio-id` multi-plataforma aborta con mensaje (un lote, una
   plataforma: un candado y un mercado por lote). Las fuentes no lo
   fijan; separar por plataforma es lo honesto.
5. Explosión al escribir (no-infra, forma sellada) sella su fila en
   error y el lote sigue (precedente: el tool A.3 continuaba con
   `[error]`); solo `psycopg.Error` y `EscrituraNoDisponible` abortan
   el lote. Fijado en `test_s5_fallo_al_escribir_*`.
6. `app/precio/cambios.py` (1029) entra a `ALLOWLIST_TAMANO` con razón
   (protocolo cohesivo, sección I; partirlo separaría `revertir` de
   `_revertir_fila`). Sin `con_motor_encendido` del bosquejo (no está
   en plan ni guía: el aborto live es incondicional).
7. Archivo de pruebas migrado de ORDEN39C a ORDEN59S5: el INSERT
   compartido sella `lote_reversa` (columna 0054); mantener dos
   variantes SQL por esquema viejo habría sido partir por partir.
