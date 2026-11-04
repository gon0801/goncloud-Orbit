# M.3: comandos, salidas y SHA

SHA base: `1767a25426b3bb1d4ab4a4aa363d85d1399b3b0e` (rama
`bids-02/s2-motor`, arbol limpio antes de empezar; los cambios quedan sin
commitear, por encargo). `$HOME` aparece como `~`. Produccion solo se leyo
(consulta `evidencia_v2` = 0, ver `prod-evidencia-v2/`); jamas se escribio
ahi. `mide_ciclo.sh` se escribio pero NO se corrio (lo corre el lead).

## 1. Prueba primero: rojo por la razon correcta

Pruebas nuevas escritas antes que el codigo y vistas en rojo (ejemplos):

```
$ .venv/bin/python -m pytest -q tests/test_cycle.py::test_ciclo_clave_ausente_no_emite_bids_cuenta_politica_apagada
E  AttributeError: module 'app.optimizer.goals' has no attribute 'politica_bid_desde_settings'
1 failed

$ .venv/bin/python -m pytest -q tests/test_apply.py -k cupo
E  ImportError: cannot import name 'prioridad_bajo_cupo' from 'app.apply'
6 failed
```

La siembra de cupo se resembro en inverso para que el orden estable no
diera verde falso (`assert [1,2]==[2,1]` en rojo antes del cambio 14).

## 2. Comprueba del paso (todos en verde)

```
$ git grep -nE "orden_bids|_PRIORIDAD_BANDA" -- app tests | wc -l
0
$ git grep -nE "fallback_v1|politica_bandas" -- app/optimizer/bid.py | wc -l
0
$ git grep -nE "decide_bid|motor_evidencia|conv_jerarquica|_contrafactual_v|_evidencia_v2_json|permite_reversa_bid|ultimo_bid_aplicado|cpc_vigente" -- app/cycle.py | wc -l
0
$ .venv/bin/python -m pytest -q -rs tests/test_apply.py -k cupo
6 passed, 48 deselected
$ .venv/bin/python -m pytest -q --collect-only
4084 tests collected (sin errores)
```

## 3. Mutantes obligatorios (3/3 muertos, ver `mutantes.md`)

| # | Mutante | Rojo observado |
|---|---|---|
| 1 | clave ausente enciende | `{'dato_faltante': 1}` en vez de `{'politica_apagada': 1}` |
| 2 | valor desconocido sigue | `Failed: DID NOT RAISE ValueError` |
| 3 | regreso al final del cupo | orden `[3, 2, 1, 4]` en vez de `[4, 3, 2, 1]` |

Nota: el revert del mutante 3 (`0`→`9`, mismo tamano de archivo, mismo
segundo) dejo un `.pyc` rancio que ejecuto el mutante con el disco sano;
se purgo `__pycache__` y la bateria volvio a verde.

## 4. Bateria completa

```
$ .venv/bin/python -m pytest -q -p no:cacheprovider
1 failed, 4070 passed, 13 skipped in 223.48s
```

El unico fallo es `tests/test_jev_catalogo.py::test_reversa_deja_la_base_como_0001`
(`role "app_jev" cannot be dropped...`, pre-existente: falla igual en la
base limpia con M.3 en stash, verificado). 21 fallos que M.3 introdujo en
`test_api_dashboard` (2), `test_cycle_apply` (8), `test_cycle_target_ciclo`
(9), `test_goals_write` (1) y `test_preflight_1_4` (1) se migraron y estan
en verde (siembras a niveles_v3 con relleno de calendario; 2 pruebas de
previa familiar retiradas con su codigo, cambio 7).

## 5. Ruff a tocados

```
$ .venv/bin/ruff check <31 archivos py tocados>
All checks passed!
$ .venv/bin/ruff format --check <16 archivos>
(ojo: 4 reformateados, solo lineas de M.3; base ya formateada)
```

## 6. Archivos

- App (13): `app/cycle.py`, `app/apply.py`, `app/apply_cola.py`,
  `app/config_write.py`, `app/api_write.py`, `app/api_dashboard.py`,
  `app/templates/settings.html`, `app/optimizer/goals.py`,
  `app/optimizer/bid.py`, `app/optimizer/cortes.py`,
  `app/optimizer/evidencia.py`, `app/optimizer/politica.py`,
  `app/optimizer/replay.py`, `app/optimizer/windows.py`, nuevo
  `app/optimizer/eras.py`.
- Borrados (5): `tools/compara_evidencia.py`,
  `tools/evidencia_b3_recorrido_persistido.py`,
  `tests/test_cycle_anti_inversion.py`, `tests/test_optimizer_ultimo_bid.py`,
  `tests/test_repro_a6_d2_lead.py`.
- Docs: `AGENTS.md:34`, `docs/DATABASE.md:231`, `0.b/copia.sh` (+14 tablas),
  `ejecucion/M.3/` (`mide_ciclo.sh`, `mutantes.md`, este archivo,
  `prod-evidencia-v2/`).
