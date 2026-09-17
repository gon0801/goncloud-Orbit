# A.3 — Catálogo de mutantes (REPRICING 01, escritura y reversa)

Implementador: Muse. Base: rama `fase8/precio-escritura` sobre
`origin/master` `6127708`. Fecha: 2026-09-18. Plan:
`plans/repricing-01.md` v1.2 (fila A.3).

Contrato de cada fila: diff mínimo sobre el HEAD commiteado, focal del
archivo relevante, rojo literal copiado de pytest con caché nueva
(`PYTHONPYCACHEPREFIX=$(mktemp -d) -p no:cacheprovider`), revertido por
edición y `git status` limpio. Ninguna mutación se commitea. Un mutante
que sobrevive se cierra arreglando el test, no escondiéndolo.

## Baseline (sin base: cliente falso; DSN solo por los tests vecinos)

```bash
ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres" \
  ./.venv/bin/python -m pytest tests/test_spapi_write_client.py \
  tests/test_precio_write.py tests/test_architecture.py -q
```

## Inventario DoD → tests (antes de mutar)

| Caso DoD | Test |
|---|---|
| ack ACCEPTED + GET viejo → enviado | `test_cambiar_ack_aceptado_y_get_viejo_da_enviado` |
| ack error + GET nuevo → error | `test_cambiar_ack_error_y_get_nuevo_da_error` |
| ack ok + GET distinto → enviado + readback ok | `test_cambiar_ack_ok_y_get_distinto_da_enviado_con_readback_ok` |
| 429 agotado → readback fallido, cero PATCH extra | `test_cambiar_429_agotado_readback_fallido_sin_escritura_extra` |
| obs D+1 igual → confirmado; distinta → no_confirmado; sin obs → intacto | `test_cerrar_observacion_igual_confirma_distinta_no` |
| reversa se cierra por observación | `test_cerrar_reversa_se_cierra_por_observacion` |
| reversa por lote salta vivo distinto | `test_revertir_salta_si_el_vivo_ya_no_coincide` |
| `MutationNotAllowedError` != live | `test_constructor_sin_live_no_construye` |
| 401/429/5xx/red | `test_401_*`, `test_429_*`, `test_5xx_sin_reintento`, `test_error_de_red_sin_reintento` |

## Mutantes (15; 14 muertos, 1 sobreviviente cerrado con test)

### W1 — sin fail-closed de modo (`write_client.py`)

Mutante: `if modo_confirmado != "live"` → `... and False` (el
constructor abre en cualquier modo).

```text
E       Failed: DID NOT RAISE MutationNotAllowedError
FAILED tests/test_spapi_write_client.py::test_constructor_sin_live_no_construye
1 failed, 23 deselected in 0.20s
```

MUERTO.

### W2 — 401 sin refresh (`write_client.py`)

Mutante: `forzados < 1` → `forzados < 0` (el 401 se devuelve sin
reintentar con token fresco).

```text
E       assert 401 == 202
E       AssertionError: assert 1 == 2
```

MUERTO (`test_401_un_refresh_y_reintento`).

### W3 — reintento ciego en 5xx (`write_client.py`)

Mutante: `== 429` → `in (429, 500)` (el 500 se reintenta y el segundo
intento lo convierte en 202: una escritura repetida a ciegas es peor
que un `error`).

```text
E       assert 202 == 500
FAILED tests/test_spapi_write_client.py::test_5xx_sin_reintento - assert 202 ...
1 failed, 23 deselected in 0.24s
```

MUERTO.

### ORDEN — PATCH antes del INSERT (`precio_write.py:revertir`)

Mutante: PATCH primero y sello con la respuesta temprana (el orden S5
al revés). El PATCH encuentra cero filas pendientes y el flujo sella
`error`.

```text
E           AssertionError: assert 'error' == 'enviado'
1 failed, 29 deselected in 0.34s
```

MUERTO (`test_revertir_patch_exige_fila_pendiente_primero`, que además
pinza `n_patch == 1`).

### ACK — aceptar cualquier 2xx (`precio_write.py:_estado_aceptado`)

Mutante: `aceptado = 200 <= status < 300` (sin pedir `submissionId` ni
`ACCEPTED`). **Sobrevivió** a la suite (30 passed): ningún test cubría
el 2xx con estado explícito distinto. Se cerró con test nuevo
(`test_cambiar_200_sin_accepted_es_error`, PATCH 200 con
`status: "ERROR"` → `error` con `error_code ... 200`), rojo contra el
mutante:

```text
E           AssertionError: assert 'enviado' == 'error'
```

MUERTO (con test nuevo).

### SKIP — la reversa siempre procede (`precio_write.py:revertir`)

Mutante: `if vivo != despues` → `... and False` (el precio movido por
otro proceso se pisa igual).

```text
FAILED tests/test_precio_write.py::test_revertir_salta_si_el_vivo_ya_no_coincide
1 failed, 2 passed, 28 deselected in 0.59s
```

MUERTO.

### C-IGUAL — confirmar al revés (`precio_write.py:cerrar_por_observacion`)

Mutante: `==` → `!=` (lo igual sale `no_confirmado` y lo distinto
`confirmado`).

```text
FAILED tests/test_precio_write.py::test_cerrar_observacion_igual_confirma_distinta_no
FAILED tests/test_precio_write.py::test_cerrar_reversa_se_cierra_por_observacion
```

MUERTO.

### C-DIA — cerrar el mismo día (`precio_write.py:cerrar_por_observacion`)

Mutante: `dia_envio < hoy` → `dia_envio <= hoy` (el enviado de hoy
entra al lote; sin observación posterior cuenta como intacto de más).

```text
E           AssertionError: assert {'confirmado'...'intactos': 2} == {'confirmado'...'intactos': 1}
1 failed, 1 passed, 29 deselected in 0.53s
```

MUERTO.

### C-MONEDA — comparar solo importe (`precio_write.py:cerrar_por_observacion`)

Mutante: `(precio, moneda) == (despues, moneda)` → `precio == despues`
(110 USD confirmaría 110 MXN).

```text
1 failed, 1 passed, 29 deselected in 0.46s
```

MUERTO.

### HUELLA — go sin verificar (`tools/precio_reversa.py`)

Mutante: `if args.huella != huella` → `... and False` (el go con
huella mala avanza hasta chocar con la forma sin sellar en vez de
abortar antes de tocar nada).

```text
E           AssertionError: Regex pattern did not match.
E             Expected regex: 'huella'
E             Actual message: 'forma del parche sin sellar (A.4 la sella): pendiente_sonda'
FAILED tests/test_precio_write.py::test_tool_go_huella_mala_aborta_sin_tocar
```

MUERTO.

### DRYRUN — el dry-run construye escritor (`tools/precio_reversa.py`)

Mutante: el dry-run llama a `construir_escritor` antes de volver (el
camino que no debe tocar Amazon lo toca).

```text
E       AssertionError: el dry-run no construye el escritor
FAILED tests/test_precio_write.py::test_tool_dry_run_no_construye_escritor
1 failed, 30 deselected in 0.34s
```

MUERTO.

### LOCK-a — detector de PATCH crudo roto (`test_architecture.py`)

Mutante: `PATCH` fuera de la alternancia de verbos (el prefijo junto a
PATCH en la misma línea ya no dispara).

```text
E       assert False
FAILED tests/test_architecture.py::test_precio_write_frontera_caza_prefijo_con_verbo
1 failed, 32 deselected in 0.21s
```

MUERTO.

### LOCK-b — allowlist del write client vacía (`test_architecture.py`)

Mutante: `PERMITIDOS_IMPORTAR_SPAPI_WRITE = {}` (el dueño legítimo
pasa por ilegal).

```text
E       AssertionError: modulos que importan app.spapi.write_client sin estar en la allowlist: ['app/spapi/precio_write.py']
FAILED tests/test_architecture.py::test_imports_del_spapi_write_client_acotados
1 failed, 32 deselected in 0.47s
```

MUERTO.

### LOCK-c — allowlist del tool sin `httpx` (`test_architecture.py`)

Mutante: `ALLOWLIST_IMPORTS_PRECIO_REVERSA` sin `"httpx"` (cualquier
import de más, aunque sea stdlib de red, revienta el candado).

```text
E       AssertionError: tools/precio_reversa.py importa por fuera de la allowlist: ['httpx']
FAILED tests/test_architecture.py::test_tool_precio_reversa_solo_importa_lectura
1 failed, 32 deselected in 0.22s
```

MUERTO.

## Residuales

- `escritor._seller_id` (privado, mismo paquete `app.spapi`).
- `seller_id` real: `VENDEDORES_PROPIOS[MERCADOS[platform]]` (la llave
  es marketplace id; el BRIEF lo abrevia como `[platform]`).
- Aceptación del ack: 2xx + `submissionId` + `status` ausente o
  `ACCEPTED`; A.4 confirma la forma exacta (puede endurecerse a exigir
  siempre `ACCEPTED`).
- `revertir` exige el original cerrado (el índice único
  `precio_cambio_abierto_unico` impide la reversa con un abierto en el
  mismo listing): revertir un `enviado` aún abierto revienta en la base.
- El tool aborta (exit 2) ante error inesperado en el lote; solo los
  saltados continúan. La huella del go exige igualdad exacta con el
  dry-run (patrón `reversa_harvest`).
