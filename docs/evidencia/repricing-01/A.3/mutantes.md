# A.3 — Catálogo de mutantes (REPRICING 01, escritura y reversa)

## Ronda r4 (revisión grok + mutante del lead, sobre `8e99ca0`)

Caché de bytecode nueva por corrida (`PYTHONPYCACHEPREFIX=$(mktemp -d)`,
`-p no:cacheprovider`). Todos MUERTOS.

### G1 — PATCH sin `marketplaceIds` (`write_client.py:patch_listing`, ALTO)

El `platform` nunca llegaba al cable y, como el mismo seller sirve MX y
US, el mercado quedaba indefinido (campo obligatorio en Listings Items
2021-08-01; el GET hermano sí lo manda). Arreglo: `params=
{"marketplaceIds": MERCADOS[self._platform]}` (la ruta sigue sin `?`).

```text
2 failed, 29 deselected in 0.27s   # request.url sin query marketplaceIds
```

MUERTO (`test_r4_g1_patch_lleva_marketplace_ids_del_platform`, uno por
platform MX/US; el cable r1 se actualizó al contrato ruta + query).

### G2 — `cambiar` con abierto del par (`precio_write.py:cambiar_precio`)

Mutante: el `INSERT` explotaba con `UniqueViolation` cruda
(`precio_cambio_abierto_unico`) cuando el par ya tenía un abierto (el
docstring además prometía una rama `original_abierto` que no existe en
`cambiar`; ahora documenta `listing_con_cambio_abierto`).

```text
E           psycopg.errors.UniqueViolation: duplicate key value violates unique constraint "precio_cambio_abierto_unico"
1 failed, 73 deselected in 0.60s
```

MUERTO (`test_r4_g2_cambiar_con_abierto_del_par_salta`: saltado, sin
fila ni PATCH).

### G3 — el go muere a mitad del lote (`tools/precio_reversa.py`)

Mutante: sin el `try` por `revertir`, un previsible a mitad del lote
mataba la corrida con traceback y el resto no se ejecutaba.

```text
E           app.spapi.client.SpapiNoPermitida: seller SKU invalido para ruta listings
1 failed, 75 deselected in 0.60s
```

MUERTO. El plan salta `sin_sku` (test `test_r4_g3_plan_sin_sku_salta`);
el go atrapa `CambioNoReversible`, `SpapiNoPermitida`,
`PublicacionSinSku` y `UniqueViolation` por revertir (`[error]`, el
lote sigue, rc 1; test `test_r4_g3_lote_sigue_tras_previsible_y_devuelve_1`
con SKU inválido no vacío — el nulo/vacío lo salta el plan, así que el
`[error]` en go exige un caso que el plan marque revertir).

### G4 — competitivo caído bloquea con oferta propia (`precio_write.py`)

Mutante: exigir ambos 200 aunque las ofertas traigan la propia (un
5xx/429 del respaldo tumbaba la lectura).

```text
E           app.spapi.precio_write.PrecioVivoAusente: pricing B0TESTC001 status=200/500
1 failed, 1 passed, 76 deselected in 0.57s
```

MUERTO (`test_r4_g4_competitivo_caido_no_bloquea_con_oferta_propia`;
sin propia sigue ausente).

### G5 — cuerpo con `Decimal`/`float` a la red (`write_client.py`)

Mutante: sin validación, el encoder estándar deforma o revienta los
importes (van como `str`, patrón `_decimal_a_json_number`).

```text
2 failed, 31 deselected in 0.27s   # DID NOT RAISE ValueError
```

MUERTO (`test_r4_g5_cuerpo_con_numeros_binarios_es_mal_uso`, anidados,
antes de la red: cero pedidos, cero tokens).

### P25 — lote con solo saltados (`tools/precio_reversa.py`, mutante del lead)

Mutante: contar los saltados como error (rc 1 en un lote donde nada
falló).

```text
1 failed, 78 deselected in 0.55s
```

MUERTO (`test_r4_g6_solo_saltados_devuelve_0`: rc 0, cero PATCH).

## Ronda r3 (revisión cruzada kimi + mutante del lead, sobre `8e99ca0`)

Caché de bytecode nueva por corrida (`PYTHONPYCACHEPREFIX=$(mktemp -d)`,
`-p no:cacheprovider`). Todos MUERTOS.

### K1 — otro abierto del par revienta el INSERT (`precio_write.py:revertir` + tool)

Sin la consulta previa, el `INSERT` de la reversa explota con
`UniqueViolation` cruda (`precio_cambio_abierto_unico`), el go muere con
traceback y el dry-run había prometido `[revertir]`.

```text
E           psycopg.errors.UniqueViolation: duplicate key value violates unique constraint "precio_cambio_abierto_unico"
1 failed, 65 deselected in 0.42s
```

MUERTO (`test_r3_k1_otro_abierto_mismo_listing_salta` en la función y
`test_r3_k1_tool_afectado_salta_otro_revierte` en el tool: el afectado
salta `listing_con_cambio_abierto`, el otro se revierte).

### K3 — estado fuera de vocabulario (`precio_write.py`)

Mutante: construir `ResultadoCambio`/`ResultadoReversion` con un estado
inventado (el comentario documentaba `"enviado" | "error"` y `cambiar`
devuelve `"saltado"`).

```text
E       Failed: DID NOT RAISE <class 'ValueError'>
1 failed, 68 deselected in 0.26s
```

MUERTO (`test_r3_k3_estado_fuera_de_vocabulario_es_mal_uso`;
`__post_init__` valida en ambas).

### K7 — el go devuelve 0 con reversas en error (`tools/precio_reversa.py`)

Mutante: sin el rastreo de `hubo_error` (el lote con una reversa en
`error` reportaba éxito).

```text
E       AssertionError: assert 0 == 1
1 failed, 70 deselected in 0.46s
```

MUERTO (`test_r3_k7_reversa_en_error_devuelve_1`).

### K8 — sin guarda de autocommit en `revertir`/`cerrar` (`precio_write.py`)

Mutante del lead: quitar la guarda de `revertir` (solo estaba probada
en `cambiar_precio`; nada lo notaba).

```text
2 failed, 71 deselected in 0.54s   # con ambas guardas quitadas
```

MUERTO (`test_r3_k8_revertir_sin_autocommit_es_mal_uso` y
`test_r3_k8_cerrar_sin_autocommit_es_mal_uso`).

### K13 — import dinámico invisible al candado (`tests/test_architecture.py`)

Mutante: el escáner sin la condición dinámica (o sin el helper: un
`__import__("app.spapi.write_client")` pasaba el candado AST).

```text
1 failed, 1 passed, 53 deselected in 0.23s
```

MUERTO (`test_imports_spapi_write_sin_import_dinamico` + fuga sembrada
`test_imports_spapi_write_frontera_caza_import_dinamico`).

## Ronda r2 (re-auditoría del lead sobre `1428f69`; B1 + 3 mutantes)

Caché de bytecode nueva por corrida (`PYTHONPYCACHEPREFIX=$(mktemp -d)`,
`-p no:cacheprovider`). Todos MUERTOS.

### B1 — PATCH antes de que la pendiente sea durable (`precio_write.py` + `tools/precio_reversa.py`)

El arreglo de r1 (`conn.commit()` al final del go) estaba al revés:
con la conexión sin autocommit los `with conn.transaction()` interiores
anidan como savepoints y el PATCH sale antes de que la fila `pendiente`
sea durable (0 filas visibles desde otra conexión al momento del PATCH).
Arreglo: `cambiar_precio`, `revertir` y `cerrar_por_observacion` exigen
`conn.autocommit` antes de hacer nada (`ValueError`, función mal usada);
el tool conecta con `autocommit=True` y se quita el `conn.commit()`.

```text
FAILED tests/test_precio_write.py::test_r2_b1_go_con_pendiente_durable - asse...
FAILED tests/test_precio_write.py::test_r2_b1_cambiar_con_pendiente_durable
FAILED tests/test_precio_write.py::test_r2_b1_sin_autocommit_es_mal_uso - Ind...
3 failed, 59 deselected in 0.76s
```

MUERTO (los tres en rojo sin el arreglo; en verde con él). La red falsa
del PATCH cuenta desde otra conexión las `pendiente` confirmadas y
contesta `500` si no ve exactamente 1.

### P9 — aceptar `INVALID` (`precio_write.py:_estado_aceptado`)

Mutante: `status == "ACCEPTED"` → `status in ("ACCEPTED", "INVALID")`
(202 con el rechazo real de Listings Items sellaría `enviado`).

```text
FAILED tests/test_precio_write.py::test_r2_b2_p9_invalid_es_error
1 failed, 62 deselected in 0.42s
```

MUERTO.

### P20 (r2) — `confirmado_por` exacta en ambos estados (`precio_write.py:cerrar_por_observacion`)

El testigo de r1 ya exigía el valor exacto; se suma el test dedicado
que lo afirma en `confirmado` Y en `no_confirmado`.

```text
FAILED tests/test_precio_write.py::test_r2_b2_p20_confirmado_por_exacta_en_ambos
```

MUERTO.

### P22 — loguear el cuerpo en la rama `enviado` (`precio_write.py:_publicar`)

Mutante: `logger.info("... estado=enviado cuerpo=%s", ..., cuerpo)`.
El test viejo miraba `"110.00"` pero el cuerpo llevaba `100.0000` (no
lo veía). El nuevo pone un marcador único (`ZZ9X8`) en el cuerpo que de
verdad sale por el cable (`request.content`) y afirma su ausencia en
`caplog`, en `cambiar_precio` y en `revertir`.

```text
E           AssertionError: assert 'ZZ9X8' not in 'INFO     ap...eadback=ok\n'
FAILED tests/test_precio_write.py::test_r2_b2_p22_cuerpo_real_no_se_loguea
```

MUERTO.

### T1 — equivalente (no mutante)

Sin `--huella`, el chequeo siguiente (`args.huella != huella`: `None`
contra la huella del plan) aborta igual antes de tocar nada. No hay
camino por el que el go mute sin huella: quitar la primera condición
no cambia el comportamiento observable. Se declara equivalente con
esta razón; el test `test_r1_m_t1_go_sin_huella_aborta` fija el aborto
por falta de huella.

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

## Ronda r1 (auditoria del lead sobre `a9c49f7`; 14 mutantes + cable)

Caché de bytecode nueva por corrida (`PYTHONPYCACHEPREFIX=$(mktemp -d)`,
`-p no:cacheprovider`). Todos MUERTOS.

### P5 — `subir` en `shadow` mueve precio (`precio_write.py:cambiar_precio`)

Mutante: quitar `or mode != "live"` (la sombra escribiría en Amazon).

```text
FAILED tests/test_precio_write.py::test_r1_m_p5_shadow_no_mueve_precio
1 failed, 49 deselected in 0.42s
```

MUERTO.

### P7 — aceptar sin `submissionId` (`precio_write.py:_estado_aceptado`)

Mutante: quitar `and cuerpo.get("submissionId") is not None` (un
202 `ACCEPTED` sin id de sumisión sellaría `enviado`).

```text
FAILED tests/test_precio_write.py::test_r1_m_p7_accepted_sin_submission_id_es_error
```

MUERTO.

### P8 — aceptar con 4xx (`precio_write.py:_estado_aceptado`)

Mutante: quitar `200 <= resp.status_code < 300` (un 400 con cuerpo
`ACCEPTED` sellaría `enviado`).

```text
FAILED tests/test_precio_write.py::test_r1_m_p8_4xx_con_accepted_es_error
```

MUERTO.

### P11 — `error_code` con el SKU (`precio_write.py:_publicar`)

Mutante: `ruta = _ruta_sin_sku(...)` → con `/{sku}` (el SKU llegaría
al log/sello). Ya lo mataban 5 testigos de formato exacto; se sumó el
test de valor exacto que exige la ausencia.

```text
FAILED tests/test_precio_write.py::test_revertir_patch_500_sella_error_con_codigo
FAILED tests/test_precio_write.py::test_cambiar_ack_error_y_get_nuevo_da_error
FAILED tests/test_precio_write.py::test_r1_a2_401_y_lwa_400_sella_lwa_sin_huerfanas
FAILED tests/test_precio_write.py::test_r1_a2_error_de_red_sella_sin_relanzar
FAILED tests/test_precio_write.py::test_r1_a2_excepcion_rara_sella_y_relanza
FAILED tests/test_precio_write.py::test_r1_m_p11_error_code_sin_sku_valor_exacto
```

MUERTO.

### P13 — `revertir` acepta un virtual (`precio_write.py:revertir`)

Mutante: `if es_reversa or not aplicado` → `if es_reversa` (un cambio
virtual, que nunca tocó Amazon, generaría una reversa real).

```text
FAILED tests/test_precio_write.py::test_r1_m_p13_virtual_no_se_revierte
```

MUERTO.

### P15 — `revertir` compara solo el importe (`precio_write.py:revertir`)

Mutante: `(vivo.precio, vivo.moneda) != (despues, despues_moneda)` →
`vivo.precio != despues` (110 USD revertiría 110 MXN).

```text
FAILED tests/test_precio_write.py::test_r1_m_p15_otra_moneda_salta
```

MUERTO.

### P17 — la observación del mismo día cierra (`precio_write.py:cerrar_por_observacion`)

Mutante: `metric_date > %s` → `>=` (cerraría con la observación del
mismo día del envío; el día en curso se descarta por regla 6).

```text
FAILED tests/test_precio_write.py::test_r1_m_p17_observacion_mismo_dia_no_cierra
```

MUERTO.

### P19 — manda la más vieja (`precio_write.py:cerrar_por_observacion`)

Mutante: `ORDER BY metric_date DESC` → `ASC` (con dos observaciones
posteriores distintas decidiría la vieja).

```text
FAILED tests/test_precio_write.py::test_r1_m_p19_manda_la_mas_reciente
```

MUERTO.

### P20 — `confirmado_por = 'virtual'` (`precio_write.py:cerrar_por_observacion`)

Mutante: `'observacion'` → `'virtual'` en el UPDATE del cierre.

```text
FAILED tests/test_precio_write.py::test_cerrar_observacion_igual_confirma_distinta_no
```

MUERTO (testigo existente, valor exacto `{"observacion"}`).

### W3 — seller ajeno con ruta coincidente (`write_client.py:validar_patch_listings`)

Mutante: quitar `if seller_id not in VENDEDORES_PROPIOS.values()`
(el caso viejo `(RUTA, "A000...", SKU)` no lo mataba: lo tapaba el
`path != esperada` final).

```text
FAILED tests/test_spapi_write_client.py::test_r1_m_w3_seller_ajeno_aunque_la_ruta_coincida
```

MUERTO.

### W10 — sin defensa de traversal (`write_client.py:validar_patch_listings`)

Mutante: quitar el chequeo de `..` (lo tapaba el `path != esperada`
final; ahora el mensaje de SU defensa está fijado).

```text
FAILED tests/test_spapi_write_client.py::test_r1_m_w10_traversal_falla_con_su_mensaje
```

MUERTO.

### W11 — sin defensa de query/fragment (`write_client.py:validar_patch_listings`)

Mutante: quitar el chequeo de `?` y `#` (idem W10).

```text
FAILED tests/test_spapi_write_client.py::test_r1_m_w11_query_y_fragment_fallan_con_su_mensaje
```

MUERTO.

### T1 — el go no exige `--huella` (`tools/precio_reversa.py`)

Mutante: quitar `not args.huella or` y la comparación
`args.huella != huella` (el go mutaría sin el visto-bueno del dry-run).

```text
FAILED tests/test_precio_write.py::test_r1_m_t1_go_sin_huella_aborta
```

MUERTO.

### T4 — un saltado aborta el lote (`tools/precio_reversa.py`)

Mutante: `if accion != "revertir": continue` → `raise Abortar`
(el primer saltado impediría revertir el segundo).

```text
FAILED tests/test_precio_write.py::test_r1_m_t4_saltado_no_aborta_el_lote
```

MUERTO.

### Cable del `patch_listing` (`test_spapi_write_client.py`)

Sin mutante: lo no probado en el cable que ahora está fijado.

- `test_r1_m_cable_patch_ruta_header`: método `PATCH`, `raw_path`
  exacto con seller sellado + SKU percent-encoded (`SKU P1` →
  `SKU%20P1`, sin espacios crudos), header `x-amz-access-token`
  con el token vigente (`tok-1`).
- Token nuevo tras `401`: testigo existente
  `test_401_un_refresh_y_reintento` (`tok-2` en el reintento).

### COMMIT — el go perdía sus escrituras (`tools/precio_reversa.py`)

Hallazgo del test T4 (no estaba en la tabla del lead): la conexión
del tool es sin autocommit y `main` nunca hacía `commit`; el primer
SELECT abría una transacción implícita, los `with conn.transaction()`
interiores anidaban como savepoints y el `close()` final hacía
rollback de todo. El go reportaba éxito y el PATCH sí salía a la red
falsa, pero la fila de la reversa y los sellos se perdían. Fix:
`conn.commit()` en el camino de éxito del go (patrón `app/cycle.py`).
Sin el fix, `test_r1_m_t4_saltado_no_aborta_el_lote` falla en
`assert reversas == 1` (0 filas); con el fix pasa.

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
  Un cambio en `error` (nace con `enviado_at`, no pasa por el cierre) sí
  es reversible el mismo día si el vivo coincide (r3-K5).
- El tool aborta (exit 2) ante error inesperado en el lote; solo los
  saltados continúan. La huella del go exige igualdad exacta con el
  dry-run (patrón `reversa_harvest`).
