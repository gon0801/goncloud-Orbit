# S.2: construye el apagador — evidencia

Rama: `rp02/sec3-seguridad-goals`. Guía: `plans/repricing-02-construccion.md` S.2
(líneas 495-531). TDD: las pruebas nuevas se escribieron primero y se miraron
fallar por la razón correcta antes de implementar.

Comando base (se omite por brevedad):

```console
$ PATH=/opt/homebrew/bin:$PATH ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
  PYTHONPATH=. .venv/bin/python -m pytest -q -rs -p no:cacheprovider
```

## 1. Archivos

Nuevos: `app/precio/compuerta.py` (solo `modo_efectivo`; S.3 lo extiende),
`tests/test_precio_compuerta.py`. Tocados: `app/precio/corrida.py` (decidir
guarda el modo efectivo, off salta decidir, aplicar relee config y goal por
escritura, `retenidas` en fila y resumen), `app/config_write.py`
(`precio_modo_global` + `precio_modo_universo` con fusión por llave),
`app/api_write.py` (campos en `CuerpoSettings` y cableado),
`app/api_dashboard.py` (GET expone los modos + lista de universos),
`app/templates/settings.html`, `app/static/js/settings.js`,
`tests/test_precio_corrida.py` (8 pruebas + `SETTINGS` con universo +
caso universo-ausente), `tests/test_config_write.py` (7 pruebas),
`tests/test_architecture.py` (allowlist `_ESCRIBE_CORRIDA` suma
`precio_retencion`; precedente: S.1 actualizó sus candados en su commit).
Contratos intactos: `app/precio/tipos.py`, `puerto.py`, `config.py`,
`app/precio_mercados.py`, `app/estimacion_universo.py`, `0053/0054`.

## 2. Rojo TDD (antes de implementar)

```console
$ pytest tests/test_precio_compuerta.py
E   ModuleNotFoundError: No module named 'app.precio.compuerta'
1 error

$ pytest tests/test_precio_corrida.py -k "test_s2_ or test_umbral_ausente"
FAILED test_umbral_ausente_aborta_al_arrancar[precio_modo_universo]
FAILED test_s2_global_shadow_goal_live_decide_shadow_y_virtual
FAILED test_s2_global_off_limpia_y_no_decide
FAILED test_s2_apagado_a_media_corrida_retiene_apagador
FAILED test_s2_repaso_no_aplica_live_con_goal_pasado_a_shadow
FAILED test_s2_repaso_no_aplica_live_con_goal_cerrado
FAILED test_s2_repaso_no_aplica_live_con_goal_ya_no_live
FAILED test_s2_repaso_no_aplica_live_sin_goal_id
FAILED test_s2_universo_sin_entrada_vale_off
9 failed, 2 passed (los 2 casos viejos de umbral)

$ pytest tests/test_config_write.py -k test_s2_
7 failed, 18 deselected
```

## 3. Verde focalizado (después de implementar)

```console
$ pytest tests/test_precio_corrida.py tests/test_precio_compuerta.py \
  tests/test_config_write.py tests/test_api_write.py \
  tests/test_settings_escritura.py tests/test_architecture.py
310 passed, 1 warning in 22.88s
```

Corrido por el lead en r3 con la 0059 en ORDEN59 (ver §6). El warning es
preexistente (`starlette.testclient` deprecado). No se corrió la batería
completa (orden del plan: corre una vez al cierre de la sección).

Vecinos settings: `test_ui_settings_sin_target_dice_sin_target` (1 passed),
`test_settings_sin_target_publica_null_y_no_pisa` (1 passed),
`-k settings` en `test_ui.py` (2 passed).
`tests/test_architecture.py` + `tests/test_arq_precio_s.py`: 96 passed
(pureza del nuevo `compuerta.py`, allowlist con `precio_retencion`).
Render real de `settings.html`
con y sin claves precio: selects correctos y sin `None` (script inline,
OK). `settings.js` sin runtime JS disponible: verificado por lectura
(replica los patrones `cambioSelect`/`querySelectorAll` existentes).

## 4. DoD de la fila S.2 (plan l.172)

- Global `shadow` + goal `live` → decisión `shadow` + virtual: verde.
- Global `off` → limpia y no decide (con espía de que decidir no corre): verde.
- Apagar a media corrida corta en la siguiente escritura: verde.
- Universo sin entrada vale `off`: verde.
- Live retenida con goal a `shadow`/cerrado → `goal_cambiado`, no aplica: verde.
- Guardar modo desde `/settings` inserta `config_version` que conserva
  claves: verde. Fuera de vocabulario/registro → 422; sin token → 401: verde.
- `ruff check` + `ruff format --check` sobre lo tocado: verde.

## 5. Bloqueador RESUELTO: bug en el trigger `precio_retencion_coherente` (0054)

Resuelto por el bloque R03k (PR #453, migración
`0059_precio02_repara_retencion_coherente.sql`): `SELECT ... INTO`
escalares y comparación fila contra fila. Diagnóstico original (r2):

`migrations/0054_precio02_base.sql` l.426-428:

```sql
IF (v_platform, v_decision_date) IS DISTINCT FROM (
    SELECT c.platform, c.fecha FROM precio_corrida c WHERE c.id = NEW.corrida_id
) THEN
```

Postgres no acepta fila-vs-subquery con `IS DISTINCT FROM`: todo INSERT
en `precio_retencion` muere con `SyntaxError: subquery must return only
one column` (probado aislado en `psql`: falla igual). Ningún test cubre
ese trigger (solo lo tocan las pruebas S.2 nuevas). NO se tocó: la 0054
es contrato. El R03k aplicó el fix en migración nueva con su reversa.

## 6. Revisión del lead (r3)

Veredictos sobre las 6 banderas de LISTO-R03-r2: los 2 `AssertionError`
de decidir se volvieron gracia (`return 0, 0` / `continue`: el bosquejo J
dice "modo == off -> no hay fila", no excepción); el test off ganó un
espía de que `_decidir_pendientes` no corre. Se quedan: bypass de
`modo_de` (lee las 2 claves con lectores del contrato, probado), aplicar
corriendo con global off (solo deja retenciones `apagador`, va a
Desviaciones del PR), GET de settings exponiendo modos (lo exigen los
campos), canal `None` como neutro `live` (solo en decisiones que nunca
se aplican; subir/bajar sin canal cierra en `off`), 4 universos fijos en
la UI (citados al registro, sin enumeración pública).
`tests/test_precio_corrida.py::ORDEN59` suma la 0059 (la heredan mercado
y demás importadores). Los 7 mutantes `[probe]` + M4 se re-demostraron
en el entorno real con la 0059 (ver `mutantes.md`); M1, M2 y M11-M18
siguen valiendo (su código no cambió).
