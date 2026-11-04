# S.6: mutantes (obligatorio del plan + uno por prueba nueva)

Cada mutante: rojo con mutante, verde restaurado, revertido al terminar.
Comando base: el de `evidencia.md`. Todos se demostraron contra su prueba
(linea `::test`) y se revirtieron; el verde final (§3 de `evidencia.md`)
corre sobre el arbol limpio (verificado: `grep` de `and False`,
`"costo": "costo"`, `motivo: {aviso.motivo}`, `is not None` en
`_corrida_ausente` y el `WHERE` sin causa sin hits; literales correctos
presentes).

## M0 — obligatorio del plan: el aviso de compuerta sale en cada repaso

Mutante (`app/notifica.py`, `_avisar_compuerta_precio`):
`if ya is not None:` → `if ya is not None and False:`
(la guarda del sello nunca salta).

- Rojo: `1 failed` (`assert 2 == 1`: el repaso reenvio).
- Prueba: `test_s6_compuerta_una_vez_por_universo_causa_y_dia_con_repasos`.
- Verde restaurado: `1 passed` (en el verde final §3).

## M-T1 — `test_s6_resumen_diario_uno_por_plataforma_y_dia_aunque_nada_se_mueva`

Mutante (`app/notifica.py`, `aviso_precio_resumen`):
`if resumen.subieron == 0 and resumen.bajaron == 0:` →
`... and False:` (nunca headline de dia quieto).

- Rojo: `1 failed` (`'hoy no se movio ningun precio'` ausente).
- Verde restaurado: `1 passed` (en el verde final §3).

## M-T2 — `test_s6_dos_corridas_mismo_dia_mandan_un_resumen`

Mutante (`app/notifica.py`, `_avisar_resumen_precio`):
`...fetchone() is not None:` → `...fetchone() is None:`
(guarda del sello invertida: solo "manda" si ya hay sello).

- Rojo: `1 failed` (`assert 0 == 1` en la primera corrida).
- Verde restaurado: `1 passed` (en el verde final §3).

## M-T3 — `test_s6_compuerta_una_vez_por_universo_causa_y_dia_con_repasos`

Mutante (`app/notifica.py`, `_SQL_COMPUERTA_YA_AVISADA` + llamada):
`... AND canal = %s AND causa = %s` → `... AND canal = %s`
(chequeo ciego a la causa; llamada con 3 params).

- Rojo: `1 failed` (`assert 2 == 3`: la causa nueva no aviso).
- Verde restaurado: `1 passed` (en el verde final §3).

## M-T4 — `test_s6_textos_enviados_sin_costo_margen_ni_goal`

Mutante (`app/notifica.py`, `INSUMO_COMPUERTA_ES`):
`"costo": "coste"` → `"costo": "costo"` (sin traduccion).

- Rojo: `1 failed` (`assert 'costo' not in` el texto de compuerta).
- Verde restaurado: `1 passed` (en el verde final §3).

## M-T5 — `test_s6_fallo_de_envio_no_tumba_la_corrida_y_el_repaso_reintenta`

Mutante (`app/notifica.py`, `_avisar_resumen_precio`):
sello movido antes del envio (sella aunque falle).

- Rojo: `1 failed` (`assert 1 == 0`: sello con Telegram caido).
- Verde restaurado: `1 passed` (en el verde final §3).

## M-T6 — `test_s6_builders_nuevos_sin_costo_margen_ni_goal`

Mutante (`app/notifica.py`, `aviso_precio_cambio_error`):
`motivo_precio_es(aviso.motivo)` → `aviso.motivo` (crudo).

- Rojo: `1 failed` (`precio_no_cubre_costo` trae "costo").
- Verde restaurado: `1 passed` (en el verde final §3).

## M-T7 — `test_s6_salud_corrida_ausente_dentro_del_bloque_precios`

Mutante (`app/api_precios.py`, `_corrida_ausente`):
`...fetchone() is None` → `...fetchone() is not None` (invertido).

- Rojo: `1 failed` (True/False cruzados).
- Verde restaurado: `1 passed` (en el verde final §3).
