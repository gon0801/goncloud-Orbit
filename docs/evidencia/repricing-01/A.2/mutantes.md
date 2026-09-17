# A.2 — Catálogo de mutantes (REPRICING 01, reglas puras)

Implementador: Muse. Base: rama `fase8/precio-reglas` sobre
`origin/master` `6127708` (que ya trae la migración 0039 del carril A).
Fecha: 2026-09-17. Plan: `plans/repricing-01.md` v1.2 (fila A.2).

Contrato de cada fila: diff mínimo (1 línea) sobre el HEAD commiteado,
focal del archivo relevante, rojo literal copiado de pytest, revertido
con `git checkout -- <archivo>` y `git status` limpio. Ninguna mutación
se commitea. Un mutante que sobrevive se cierra arreglando el test, no
escondiéndolo (M12).

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
