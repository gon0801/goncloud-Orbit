# A.2 — Catálogo de mutantes (REPRICING 01, reglas puras)

Implementador: Muse. Base: rama `fase8/precio-reglas` sobre
`origin/master` `6127708` (que ya trae la migración 0039 del carril A).
Fecha: 2026-09-17. Plan: `plans/repricing-01.md` v1.2 (fila A.2).

Contrato de cada fila: diff mínimo (1 línea) sobre el HEAD commiteado,
focal del archivo relevante, rojo literal copiado de pytest, revertido
con `git checkout -- <archivo>` y `git status` limpio. Ninguna mutación
se commitea. Un mutante que sobrevive se cierra arreglando el test, no
escondiéndolo (M12).

## Ronda r5 (revisión de kimi, 2026-09-17)

Sembrados sobre el HEAD commiteado, con caché nueva
`PYTHONPYCACHEPREFIX=$(mktemp -d) -p no:cacheprovider`, revertidos por
edición y `git status` limpio. J4–J7 son candados e higiene sin cambio
de comportamiento y no llevan mutante.

### J1a — sin filtro `aplicado`: la sombra compite (`reglas.py`)

Mutante: `if resultado in (...) and aplicado` → `if resultado in
(...)`. El `subir` de sombra le quita el cupo al `live`.
Test: `test_r5_j1_sombra_no_consume_cupo`.

```text
E       AssertionError: assert Decision(resultado='mantener', motivo='cuota', ...) is Decision(resultado='subir', ..., aplicado=True, ...)
FAILED tests/test_precio_reglas.py::test_r5_j1_sombra_no_consume_cupo
1 failed, 6 passed, 161 deselected in 0.32s
```

MUERTO.

### J1b — sin filtro de resultado: todo lo aplicado compite (`reglas.py`)

Mutante: `if resultado in (...) and aplicado` → `if aplicado`. Con
datos del motor hoy es indistinguible (solo `_subir`/`_bajar` nacen con
`aplicado=True`), así que sobrevivió al subconjunto; se mató con
`test_r5_j1_mantener_con_aplicado_no_compite` (un `mantener(*)` con
`aplicado=True` pasa idéntico por contrato, aunque el motor no lo
produzca hoy):

```text
E       AssertionError: assert Decision(resultado='mantener', motivo='cuota', ...) is Decision(resultado='subir', ..., aplicado=True, ...)
FAILED tests/test_precio_reglas.py::test_r5_j1_mantener_con_aplicado_no_compite
1 failed, 168 deselected in 0.30s
```

MUERTO (con test nuevo; doctrina M12).

### J1c — las que no compiten se reescriben a cuota (`reglas.py`)

Mutante: la rama `else` final reescribe a `mantener(cuota)` en vez de
pasar idéntico. El `no_evaluado` pierde su motivo y la sombra su
resultado. Tests: los cuatro `test_r5_j1_*`.

```text
FAILED tests/test_precio_reglas.py::test_r5_j1_frenado_no_consume_cupo_ni_reescribe_no_evaluado
FAILED tests/test_precio_reglas.py::test_r5_j1_sombra_no_consume_cupo
FAILED tests/test_precio_reglas.py::test_r5_j1_mantener_con_aplicado_no_compite
FAILED tests/test_precio_reglas.py::test_r5_j1_cupo_cero_solo_accion_live_sale_cuota
4 failed, 165 deselected in 0.34s
```

MUERTO.

### J2 — sin guarda de monedas (`reglas.py`)

Mutante: quitar el `ValueError` de monedas distintas. Test:
`test_r5_j2_monedas_distintas_entre_compiten_es_valueerror` (rojo
original del TDD, con caché nueva):

```text
E       Failed: DID NOT RAISE ValueError
1 failed, 168 deselected in 0.32s
```

MUERTO.

### J3 — sin exigir centavos (`estimacion_fees.py`)

Mutante: quitar el `ValueError` de `precio != precio.quantize(0.01)`.
Test: `test_r5_j3_cotizar_a_precio_exige_centavos` (rojo original del
TDD, con caché nueva):

```text
E       Failed: DID NOT RAISE ValueError
1 failed, 169 deselected in 0.31s
```

MUERTO.

## Baseline (sin base: son reglas puras; DSN solo por los tests vecinos)

```bash
ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres" \
  ./.venv/bin/python -m pytest tests/test_precio_reglas.py \
  tests/test_architecture.py tests/test_estimacion_venta.py -q
```

```text
166 passed in 3.49s
```

**0 skipped**: condición de partida cumplida.

## Inventario regla → tests (antes de mutar)

| Regla S4 | Tests que la cubren |
|---|---|
| #1 insumos | `test_reglas_passthrough_del_motivo_de_estimacion`, `test_reglas_sin_pricing_es_precio_sin_observar` |
| #2 margen actual | `test_reglas_borde_tolerancia_mantener_y_subir` |
| #3 subir + cotizaciones | `test_objetivo_paso_cero_cotizaciones_pide_estrella`, `test_objetivo_paso_una_lineal_verifica`, `test_objetivo_paso_dos_no_lineales_es_fee_no_lineal`, `test_objetivo_paso_nunca_pide_tercera`, `test_objetivo_paso_tax_y_error_y_no_concilia`, `test_reglas_tope_del_escalon_hacia_abajo` |
| #4 bajar | `test_reglas_bajar_con_senal_y_escalon` |
| #5 perdiendo ventas | `test_ventas_borde_90_no_dispara_89_si`, `test_ventas_ventanas_inclusive_ayer_y_anteayer`, `test_ventas_hueco_ledger_sin_cobertura`, `test_ventas_u60_bajo_minimo`, `test_ventas_n15_insuficiente_con_excluidas`, `test_ventas_historia_corta_74_dias`, `test_ventas_dia_sin_stock_y_sin_observacion`, `test_ventas_listing_inactivo_un_dia`, `test_ventas_racha_dos_de_tres_es_sin_dato`, `test_ventas_excluidas_escalan_el_promedio` |
| #6 freno tras subida | `test_reglas_freno_tras_subida_antes_que_todo`, `test_reglas_freno_antes_que_cooldown` |
| #7 buy box | `test_reglas_buy_box_solo_se_registra` |
| #8 movimiento mínimo | `test_reglas_movimiento_minimo_no_consume` |
| #9 cooldown | `test_reglas_cooldown_seis_si_siete_no` |
| #10 no converge | `test_reglas_no_converge_tres_sin_acercarse`, `test_reglas_convergiendo_o_goal_nuevo_no_frena` |
| #11 goal inalcanzable | `test_objetivo_denominador_no_positivo_es_margen_imposible`, `test_reglas_p_estrella_mayor_al_doble_no_cotiza`, `test_reglas_regla11_cubre_costo_predicado` |
| #12 cuota y prioridad | `test_reglas_prioridad_registrada`, `test_reglas_cupo_por_prioridad_con_desempate` |
| #13 sombra | `test_reglas_sombra_igual_con_aplicado_falso` |
| config con cotas | `test_config_*` (bordes a los dos lados de cada cota) |
| pureza + Decimal | `test_precio_puro_sin_io`, `test_precio_sin_reloj_ni_entorno`, `test_precio_frontera_caza_fuga_en_subpaquete`, `test_reglas_sin_decimal_no_hay_float` |

## Mutantes (14; 13 muertos, 1 sobreviviente cerrado con test)

### M1 — S4 #1: se ignora el motivo de la estimación

Diff (`app/precio/reglas.py`): `if entrada.motivo_estimacion is not None:` → `if False:`.
Test: `test_reglas_passthrough_del_motivo_de_estimacion`.

```text
E       AssertionError: assert ('mantener', ..._sin_perdida') == ('no_evaluado', 'fee_ausente')
E         At index 0 diff: 'mantener' != 'no_evaluado'
1 failed, 77 deselected in 0.09s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### M2 — S4 #2: `m_actual` sin `R`

Diff (`app/precio/reglas.py`): se quita `- comp.isr.valor` de la cuenta.
Test: `test_reglas_borde_tolerancia_mantener_y_subir`.

```text
E       AssertionError: assert ('mantener', ..._sin_perdida') == ('mantener', 'en_tolerancia')
E         At index 1 diff: 'sobre_goal_sin_perdida' != 'en_tolerancia'
1 failed, 77 deselected in 0.08s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### M3 — S4 #3: la primera cotización siempre verifica

Diff (`app/precio/objetivo.py`): `if abs(m1 - goal) <= tolerancia:` → `if True:`.
Test: `test_objetivo_paso_dos_no_lineales_es_fee_no_lineal`.

```text
E       AssertionError: assert (False)
E        +  where False = isinstance(ResultadoObjetivo(resultado='verificado', ...), <class 'app.precio.tipos.PideCotizacion'>)
1 failed, 1 passed, 76 deselected in 0.09s
```

MUERTO. Revertido con `git checkout -- app/precio/objetivo.py`.

### M4 — S4 #4: `max` por `min` en la bajada

Diff (`app/precio/reglas.py`): `p_aplicado = max(p_goal, piso)` → `min(p_goal, piso)`.
Test: `test_reglas_bajar_con_senal_y_escalon`.

```text
E       AssertionError: assert Decimal('89.88') == Decimal('104.40')
1 failed, 77 deselected in 0.08s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### M5 — S4 #5: `<` estricto por `<=`

Diff (`app/precio/ventas.py`): `if not Decimal(u15) < esperado:` → `<=`.
Test: `test_ventas_borde_90_no_dispara_89_si` (el borde 90 exacto).

```text
E       AssertionError: assert 'sin_dato' == 'no_perdiendo'
E         - no_perdiendo
E         + sin_dato
1 failed, 77 deselected in 0.07s
```

MUERTO. Revertido con `git checkout -- app/precio/ventas.py`.

### M6 — S4 #6: ventana del freno recortada a 9 días

Diff (`app/precio/reglas.py`): `_DIAS_FRENO_SUBIDA = 22` → `= 9`.
Test: `test_reglas_freno_tras_subida_antes_que_todo` (subida hace 10 días).

```text
E       AssertionError: assert ('bajar', None) == ('frenado', '..._tras_subida')
E         At index 0 diff: 'bajar' != 'frenado'
1 failed, 77 deselected in 0.08s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### M7 — S4 #7: Buy Box inventada

Diff (`app/precio/reglas.py`): `buy_box_is_own=entrada.buy_box_is_own` → `=True`.
Test: `test_reglas_buy_box_solo_se_registra`.

```text
E       AssertionError: assert ('subir', True) == ('subir', False)
E         At index 1 diff: True != False
1 failed, 77 deselected in 0.07s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### M8 — S4 #8: el movimiento mínimo nunca dispara

Diff (`app/precio/reglas.py`): `< umbral` → `< -umbral`.
Test: `test_reglas_movimiento_minimo_no_consume`.

```text
E       AssertionError: assert ('subir', None) == ('mantener', ...iento_minimo')
E         At index 0 diff: 'subir' != 'mantener'
1 failed, 77 deselected in 0.08s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### M9 — S4 #9: cooldown inclusivo (7 días frena)

Diff (`app/precio/reglas.py`): `0 <= dias < config.dias_entre_cambios` → `<=`.
Test: `test_reglas_cooldown_seis_si_siete_no`.

```text
E       AssertionError: assert False
E        +  where False = isinstance(Decision(resultado='mantener', motivo='cooldown', ... diagnostico='ultimo cambio hace 7 dias'), <class 'app.precio.tipos.PideCotizacion'>)
1 failed, 1 passed, 76 deselected in 0.08s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### M10 — S4 #10: el freno exige mejora estricta

Diff (`app/precio/reglas.py`): `posterior >= anterior` → `posterior > anterior`.
Test: `test_reglas_no_converge_tres_sin_acercarse` (cadena plana 0.05).

```text
E       AttributeError: 'PideCotizacion' object has no attribute 'resultado'
1 failed, 77 deselected in 0.08s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### M11 — S4 #11: el doble ampliado a 200×

Diff (`app/precio/reglas.py`): `p_estrella > 2 * p_actual` → `> 200 * p_actual`.
Test: `test_reglas_p_estrella_mayor_al_doble_no_cotiza`.

```text
E       AssertionError: assert not True
E        +  where True = isinstance(PideCotizacion(precio=Importe(valor=Decimal('746.44'), moneda='MXN'), intento=1), <class 'app.precio.tipos.PideCotizacion'>)
1 failed, 77 deselected in 0.09s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### M12 — S4 #12: desempate por `listing_id` descendente

Diff (`app/precio/reglas.py`): clave de desempate `par[0]` → `-par[0]`.

Primera corrida: **SOBREVIVIÓ** (`1 passed, 77 deselected`) — el test solo
pedía que el primero fuera `subir`, sin distinguir QUÉ listing pasaba.
Se arregló el test (no se escondió el mutante): ahora exige que con
empate pase el `listing_id` menor, distinguido por `p_aplicado` (cada
costo da un `P*` distinto). Segunda corrida:

```text
E       AssertionError: assert Decimal('127.60') == Decimal('121.23')
1 failed, 77 deselected in 0.09s
```

MUERTO con el test endurecido (commiteado en el mismo encargo).
Revertido con `git checkout -- app/precio/reglas.py`.

### M13 — S4 #13: la sombra también aplicaría

Diff (`app/precio/reglas.py`): `aplicado=entrada.mode == "live"` → `aplicado=True`.
Test: `test_reglas_sombra_igual_con_aplicado_falso`.

```text
E       AssertionError: assert True is False
E        +  where True = Decision(..., mode='shadow', ...).aplicado
1 failed, 77 deselected in 0.08s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### M14 — config: cota de tolerancia recortada

Diff (`app/precio/config.py`): cota máxima `0.05` → `0.004`.
Test: `test_config_bordes_en_cota_pasan` (el valor inicial `0.005` del plan
revienta con la cota mutada).

```text
E           ValueError: setting precio_tolerancia: fuera de cota [0, 0.004]: 0.005
1 failed, 77 deselected in 0.08s (4 parametrizaciones del borde)
```

MUERTO. Revertido con `git checkout -- app/precio/config.py`.

## Ronda r4 (revisión de grok, 2026-09-17)

G1–G4 con mutante propio (sembrados sobre el HEAD commiteado, con
caché nueva `PYTHONPYCACHEPREFIX=$(mktemp -d) -p no:cacheprovider`,
revertidos por edición y `git status` limpio). G5–G10 son
documentación, higiene de tests o candados sin cambio de
comportamiento y no llevan mutante.

### G1 — historial sin ordenar por fecha (`reglas.py`)

Mutante: `sorted(..., key=lambda h: h.fecha)` → `list(...)` (orden de
llegada). El freno mira los últimos N por fecha; sin ordenar, el
barajado decide. Test: `test_r4_g1_orden_de_llegada_no_decide_el_freno`.

```text
E       AssertionError: assert False
E        +  where False = isinstance(Decision(resultado='frenado', motivo='no_converge', m_actual=Decimal('0.225'), goal=Decimal('0.30'), p_actual=Importe(...d=Decimal('1125.000'), aplicado=False, mode='live', buy_box_is_own=None, diagnostico='3 cambios sin acercarse al goal'), <class 'app.precio.tipos.PideCotizacion'>)
1 failed, 1 passed, 162 deselected in 0.36s
```

MUERTO.

### G2 — `repartir_cupo` devuelve en orden de prioridad (`reglas.py`)

Mutante: el bucle de salida itera `ordenados` en vez de `candidatos`.
El `zip` con la entrada cruzaría publicaciones. Test:
`test_r4_g2_cupo_devuelve_pares_en_orden_de_entrada`.

```text
E       assert [1, 2] == [2, 1]
E         At index 0 diff: 1 != 2
1 failed, 163 deselected in 0.34s
```

MUERTO.

### G3 — escalón igual al mínimo revienta (`config.py`)

Mutante: `if escalon < minimo` → `if escalon <= minimo`. El igual es
válido (el escalón aplicado queda sobre el mínimo, no bajo él).
Test: `test_r4_g3_escalon_bajo_minimo_revienta_igual_pasa`.

```text
E           ValueError: setting precio_escalon_max_pct por debajo de precio_movimiento_min_pct: 0.10 < 0.10 (el escalón aplicado quedaría bajo el mínimo y consumiría cooldown y cuota)
1 failed, 163 deselected in 0.32s
```

MUERTO.

### G4 — moneda sin mínimo gasta cotización (`reglas.py`)

Mutante: la guarda acepta `"EUR"` (`not in ("MXN", "USD", "EUR")`).
La moneda mala pasa la coherencia y `decidir` pide cotización en vez
de `no_evaluado(escenario_incoherente)`. Test:
`test_r4_g4_moneda_sin_minimo_no_gasta_cotizacion`.

```text
E       AssertionError: assert not True
E        +  where True = isinstance(PideCotizacion(precio=Importe(valor=Decimal('131.68'), moneda='EUR'), intento=1), <class 'app.precio.tipos.PideCotizacion'>)
1 failed, 163 deselected in 0.28s
```

MUERTO.

## Ronda r4b (auditoría, 2026-09-17)

### H1 — `repartir_cupo` emparejaba por `id(decision)` (`reglas.py`)

Mutante implícito: el mismo objeto `Decision` en dos publicaciones con
cupo suficiente salía la segunda `mantener(cuota)`. El arreglo decide
quién pasa por posición (`enumerate`). Test:
`test_r4b_h1_mismo_objeto_en_dos_publicaciones_pasa_con_cupo`.

```text
E       AssertionError: assert [(1, 'subir')..., 'mantener')] == [(1, 'subir'), (2, 'subir')]
E         At index 1 diff: (2, 'mantener') != (2, 'subir')
1 failed, 164 deselected in 0.34s
```

MUERTO.

### H2 — rama redundante del `Call` en `_usos_reloj` (`test_architecture.py`)

`ast.walk` visita el `Call` Y su `Attribute` interno: quitar `"utcnow"`
o `"today"` de la tupla del `Call` no rompía ningún test (mutante
equivalente por redundancia). Se quitó la rama del `Call`; queda una
sola tupla en la rama del `Attribute`. Sensibilidad verificada con
caché nueva sobre la tupla que queda:

```text
E       Failed: DID NOT RAISE AssertionError
FAILED tests/test_architecture.py::test_precio_frontera_caza_reloj_en_todas_sus_formas[from datetime import datetime as dt\nx = dt.utcnow()\n]
1 failed, 10 passed, 29 deselected in 0.24s
```

```text
E       Failed: DID NOT RAISE AssertionError
FAILED tests/test_architecture.py::test_precio_frontera_caza_reloj_en_todas_sus_formas[from datetime import date as d\nx = d.today()\n]
1 failed, 10 passed, 29 deselected in 0.24s
```

La tupla discrimina elemento por elemento. De paso se corrigió el
docstring: `time.time()` cae SOLO por el import prohibido de `time`.

## Ronda r3 (revisión de kimi, 2026-09-17)

Nacieron de K: monedas divergentes (`test_r3_k1_*`), cupo que limpia
`p_aplicado` + invariante (`test_r3_k2_*`, 2 tests), duplicados
conservadores (`test_r3_k5_*`, 6 tests). K3 (parámetro muerto) y K4
(imports) son higiene sin cambio de comportamiento.

(Los cinco hallazgos de kimi son K1–K5; nada más en este catálogo se le
atribuye.)

### Ayuda fantasma de tests (r3, NO es L2 del brief; r3b la sacó de `app/`)

En la r3 se puso una fábrica en `tipos.py`; en la r3b se quitó
(`tipos.py` queda como en `e2628da` en esa parte) y la ayuda vivió en
`tests/test_precio_reglas.py` (`_fantasma`, `test_r3b_fantasma_*`).
Su mutante («el fantasma que no verifica pasa») murió con caché nueva:

```text
E       Failed: DID NOT RAISE ValueError
1 failed, 155 deselected in 0.31s
```

R5-J6: ayuda y tests borrados (probaban la ayuda, no el motor); la
entrada queda como historia.

## Ronda r3b (corrección, 2026-09-17)

### L1 — la reversa no frena por ventas (real)

Mutante: quitar `cambio.es_reversa` del filtro de la regla #6.
Test: `test_r3b_l1_reversa_no_frena_por_ventas` (reversa `confirmado`
que sube, hace 10 días, + `perdiendo`). Con caché nueva:

```text
E       AssertionError: assert False
E        +  where False = isinstance(Decision(resultado='frenado', motivo='perdiendo_tras_subida', ...), <class 'app.precio.tipos.PideCotizacion'>)
FAILED tests/test_precio_reglas.py::test_r3b_l1_reversa_no_frena_por_ventas
1 failed, 157 deselected in 0.32s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

### L2 — la bajada no frena por ventas (real)

Mutante: quitar `cambio.direccion == "subir"` de la regla #6.
Test: `test_r3b_l2_bajada_no_frena_por_ventas` (bajada `confirmado` hace
10 días + `perdiendo`). Con caché nueva:

```text
E       AssertionError: assert False
E        +  where False = isinstance(Decision(resultado='frenado', motivo='perdiendo_tras_subida', ...), <class 'app.precio.tipos.PideCotizacion'>)
FAILED tests/test_precio_reglas.py::test_r3b_l2_bajada_no_frena_por_ventas
1 failed, 157 deselected in 0.32s
```

MUERTO. Revertido con `git checkout -- app/precio/reglas.py`.

## Ronda r2 (re-auditoría del lead, 2026-09-17)

El lead corrió 65 mutantes con caché de bytecode nueva por corrida; 12
quedaron verdes. Cada uno recibió su test (`test_r2_b_*`), verificado en
rojo con el diff aplicado **con caché nueva**
(`PYTHONPYCACHEPREFIX=$(mktemp -d) ... -p no:cacheprovider`) y revertido
con `git checkout -- <archivo>`.

Además nacieron de A: 22 motivos + fallback (23 tests r2-A1), precio no
positivo (r2-A2), futuro que cuenta (2 tests r2-A3) y fugas de imports y
reloj (8 tests r2-A4).

Re-verificación de los 14 del catálogo + R15 con caché nueva (2026-09-17):
todos muertos (`1 failed` cada uno; M11 `2 failed`; M14 `9 failed`;
R15 `1 failed` con solo el filtro de historial quitado). Sin falsos por
`.pyc` viejo.

### R2 — `m_actual > goal + tol` → `>=` (`reglas.py`)

Test: `test_r2_b_r2_borde_3050_tolerancia_3051_pide` (`m = 30.50 %`
exacto con señal `perdiendo` → `en_tolerancia`).

```text
E       AttributeError: 'PideCotizacion' object has no attribute 'resultado'
1 failed, 143 deselected in 0.33s
```

MUERTO.

### R6 — `divergencia > max` → `>=` (`reglas.py`)

Test: `test_r2_b_r6_divergencia_exacta_100_no_diverge` (`1.00 %` exacto).

```text
E       AssertionError: assert ('no_evaluado...o_divergente') == ('mantener', ..._sin_perdida')
E         At index 0 diff: 'no_evaluado' != 'mantener'
```

MUERTO.

### N1b — no mirar la moneda de `precio_cotizado` (`reglas.py`)

Test: `test_r2_b_n1b_cotizado_otra_moneda_es_incoherente`.

```text
E       AttributeError: 'PideCotizacion' object has no attribute 'resultado'
1 failed, 143 deselected in 0.32s
```

MUERTO.

### N2 — no mirar `Σ final_fee == F` (`reglas.py`)

Test: `test_r2_b_n2_fees_que_no_suman_f_es_incoherente`.

```text
E       AttributeError: 'PideCotizacion' object has no attribute 'resultado'
1 failed, 143 deselected in 0.32s
```

MUERTO.

### N3 — tolerancia de `I` en `1` (`reglas.py`)

Test: `test_r2_b_n3_i_borde_001_pasa_0011_no` (el caso `0.011` pasa).

```text
E       AttributeError: 'PideCotizacion' object has no attribute 'resultado'
1 failed, 143 deselected in 0.30s
```

MUERTO.

### N3b — tolerancia de `I` con `>=` (`reglas.py`)

Mismo test (el caso `0.01` exacto ya no pasa).

```text
E       AssertionError: assert False
E        +  where False = isinstance(Decision(resultado='no_evaluado', motivo='escenario_incoherente', ...), <class 'app.precio.tipos.PideCotizacion'>)
1 failed, 143 deselected in 0.30s
```

MUERTO.

### N4 — tolerancia de `R` en `1` (`reglas.py`)

Test: `test_r2_b_n4_r_borde_001_pasa_0011_no`.

```text
E       AttributeError: 'PideCotizacion' object has no attribute 'resultado'
1 failed, 143 deselected in 0.28s
```

MUERTO.

### N8 — quitar la regla 11 sobre el pedido de `bajar` (`reglas.py`)

Test: `test_r2_b_n8_bajar_pide_doble_no_pide_segunda` (pediría `326.13`).

```text
E       AssertionError: assert not True
E        +  where True = isinstance(PideCotizacion(precio=Importe(valor=Decimal('326.13'), moneda='MXN'), intento=2), <class 'app.precio.tipos.PideCotizacion'>)
1 failed, 143 deselected in 0.31s
```

MUERTO.

### N9 / N9b — quitar la regla 11 sobre el verificado (`reglas.py`)

Test: `test_r2_b_n9_verificado_sobre_doble_no_sube_ni_baja`
(cotización a `250` que verifica en los dos caminos).

```text
E       AssertionError: assert ('subir', None) == ('goal_inalca...yor_al_doble')
E         At index 0 diff: 'subir' != 'goal_inalcanzable'
```

MUERTO en subir (sale `subir` recortado por el escalón) y en bajar
(`1 failed` con el mutante de cada camino).

### N15 — historial `fecha >= desde` → `>` (`reglas.py`)

Test: `test_r2_b_n15_punto_mismo_dia_del_goal_cuenta`.

```text
E       AttributeError: 'PideCotizacion' object has no attribute 'resultado'
1 failed, 143 deselected in 0.30s
```

MUERTO.

### N16 — freno `enviado_en < desde` → `<=` (`reglas.py`)

Test: `test_r2_b_n16_subida_mismo_dia_del_goal_frena` (cae a `cooldown`,
que sí cuenta ese cambio).

```text
E       AssertionError: assert 'cooldown' == 'perdiendo_tras_subida'
E         - perdiendo_tras_subida
```

MUERTO.

### Equivalentes declarados

- El `bool` de `config._numero` (ronda anterior): `Decimal(str(True))`
  revienta con `InvalidOperation`; verificado en intérprete el 2026-09-17.
- `d <= cubierto` en `contados60`: al llegar ahí `cubierto ≥ hoy−3`
  siempre, y la ventana de 60 termina en `hoy−16`; **se quitó** de
  `ventas.py` en esta ronda en vez de declararse (cero cambio de
  comportamiento, suite verde).

## Ronda r1 (auditoría del lead, 2026-09-17)

El lead corrió 39 mutantes; 13 quedaron verdes. Cada uno recibió su test
(`test_r1_c_*`), verificado en rojo con el diff aplicado y revertido con
`git checkout -- <archivo>`. El 14.º (quitar el rechazo de `bool` en
`config._numero`) es **equivalente**: `Decimal(str(True))` =
`Decimal("True")` revienta con `InvalidOperation` igual que el rechazo
explícito — el `ValueError` sale por la misma rama. Se declara y no se
cambia código.

Además nacieron de A y B: coherencia del escenario (4 tests r1-A1),
regla 11 en pedido/verificado (r1-A2), máquina de bajada (3 tests r1-A3),
cobertura a 3 días (2 tests r1-B1), virtuales (3 tests r1-B2),
`goal_vigente_desde` (3 tests r1-B3, rojo vía mutante R15), submotivos
(2 tests r1-B4) y reloj endurecido (3 tests r1-B5).

### V4 — `u60 < u60_min` → `<=` (`ventas.py`)

Test: `test_r1_c_v4_u60_en_el_minimo_si_evalua` (`u60 = 20`, mínimo 20).

```text
E       AssertionError: assert ('sin_dato', 20) == ('no_perdiendo', 20)
E         At index 0 diff: 'sin_dato' != 'no_perdiendo'
```

MUERTO.

### V9 — `n15 / n60` → `15 / 60` fijo (`ventas.py`)

Test: `test_r1_c_v9_exclusiones_asimetricas_cambian_veredicto` (5 días
fuera solo en la ventana de 15 con `racha_previa=2`).

```text
E       AssertionError: assert ('perdiendo', 10, 60) == ('no_perdiendo', 10, 60)
E         At index 0 diff: 'perdiendo' != 'no_perdiendo'
```

MUERTO.

### R4 — freno `dias <= 22` → `<` (`reglas.py`)

Test: `test_r1_c_r4_freno_22_si_23_no`.

```text
E       AttributeError: 'PideCotizacion' object has no attribute 'motivo'
1 failed, 107 deselected in 0.09s
```

MUERTO (a 22 días ya no frena y sigue a la máquina).

### R7 — quitar `estado != "confirmado"` en #6 (`reglas.py`)

Test: `test_r1_c_r7_no_confirmado_no_frena`.

```text
E       AssertionError: assert False
E        +  where False = isinstance(Decision(resultado='frenado', motivo='perdiendo_tras_subida', ...), <class 'app.precio.tipos.PideCotizacion'>)
1 failed, 107 deselected in 0.08s
```

MUERTO.

### R8 — piso de `bajar` con `piso_centavo` (`reglas.py`)

Test: `test_r1_c_r8_piso_sube_al_centavo` (`P = 116.01` → piso `104.41`).

```text
E       AssertionError: assert Decimal('104.40') == Decimal('104.41')
```

MUERTO.

### R14 — la reversa cuenta para cooldown (`reglas.py`)

Test: `test_r1_c_r14_reversa_no_es_cooldown`.

```text
E       AssertionError: assert False
E        +  where False = isinstance(Decision(resultado='mantener', motivo='cooldown', ...), <class 'app.precio.tipos.PideCotizacion'>)
1 failed, 107 deselected in 0.08s
```

MUERTO.

### R15 — el goal nuevo no reinicia #6

Cubierto por r1-B3: con el filtro de fecha quitado,
`test_r1_b3_subida_anterior_al_goal_no_frena_posterior_si` y
`test_r1_b3_historial_viejo_no_frena_nuevo_si` se ponen rojos
(`2 failed, 1 passed`, rojo pegado en `.saikit/scratch/B/tdd.md`).

MUERTO.

### O3 — `tax_amount` anidado sin mirar (`objetivo.py`)

Test: `test_r1_c_o3_tax_anidado_frena` (`TaxAmount` en `included_fee_details`).

```text
E       AttributeError: 'PideCotizacion' object has no attribute 'motivo'
1 failed, 107 deselected in 0.09s
```

MUERTO.

### O4 — convergencia `<=` → `<` (`objetivo.py`)

Test: `test_r1_c_o4_igual_a_tol_verifica` (`|m(P₁) − goal|` exactamente `tol`).

```text
E       AssertionError: assert (False)
E        +  where False = isinstance(PideCotizacion(precio=Importe(valor=Decimal('131.71'), moneda='MXN'), intento=2), ResultadoObjetivo)
1 failed, 107 deselected in 0.09s
```

MUERTO.

### O5 — el 2.º `P*` reusa `ref`/`fijo` viejos (`objetivo.py`)

Test: `test_r1_c_o5_segundo_p_sale_de_ref1` (valor exacto, distinto del
que saldría con el `ref` viejo).

```text
E       AssertionError: assert Decimal('131.68') == Decimal('131.71')
1 failed, 107 deselected in 0.08s
```

MUERTO.

### O7 — denominador `<= 0` → `< 0` (`objetivo.py`)

Test: `test_r1_c_o7_denominador_cero_es_imposible`.

```text
E       decimal.DivisionByZero: [<class 'decimal.DivisionByZero'>]
1 failed, 107 deselected in 0.09s
```

MUERTO (sin el `<=`, el cero divide).

### O8 — `ReferralFee` con `final_fee = 0` cuenta (`objetivo.py`)

Test: `test_r1_c_o8_referral_en_cero_no_cuenta`.

```text
E       Failed: DID NOT RAISE ErrorObjetivo
1 failed, 107 deselected in 0.08s
```

MUERTO.

### O9 — `precio_incluye_iva=False` usa el divisor igual (`objetivo.py`)

Test: `test_r1_c_o9_sin_iva_no_usa_divisor` (`43 / 0.54` exacto).

```text
E       AssertionError: assert Decimal('96.66666666666666666666666667') == (Decimal('43') / Decimal('0.54'))
1 failed, 107 deselected in 0.08s
```

MUERTO.

## Residuales de la mutación

- `precio_no_cubre_costo` (#11) no se ejercita de punta a punta: con
  denominador < 1 (todos los casos reales) `P* > C + L` siempre; es una
  guarda y se prueba por predicado (`motivo_regla11`). Declarado también
  en el docstring de `motivo_regla11`.
- `cotizar_a_precio` no lleva mutante propio: su rojo es el de la máquina
  de `objetivo.py` (sin sustitución no hay `P₁` que verificar); sus tests
  fijan copia-sin-mutar, `ValueError` y universo intacto.
- La pureza lleva su propia fuga sembrada
  (`test_precio_frontera_caza_fuga_en_subpaquete`); no se mutó aparte.
