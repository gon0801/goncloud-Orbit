# S.4: deja de congelar por errores sin efecto — evidencia

Rama: `rp02/sec3-seguridad-goals`, sobre S.3 (`2594a0e`). Guía:
`plans/repricing-02-construccion.md` S.4 (líneas 575-590), fila S.4 del
plan (`plans/repricing-02.md`), bosquejo I (`cerrar_huerfanas` /
`cerrar_por_observacion`, 1140-1156) y F (cooldown, 945-947),
`datos.sql` §8 (derivación `sin_efecto`, 504-509).
TDD: las 10 pruebas nuevas se escribieron primero; 6 se miraron fallar
por la razón correcta antes de implementar y 4 son candados
(verdes antes y después, ver §2).

Comando base (se omite por brevedad):

```console
$ PATH=/opt/homebrew/bin:$PATH ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres \
  PYTHONPATH=. .venv/bin/python -m pytest -q -rs -p no:cacheprovider
```

## 1. Archivos

Tocados: `app/spapi/precio_mercado.py` (`cambios_previos` deriva
`sin_efecto` al leer: `error` con `error_code = 'huerfana_sin_efecto'`
o con `(readback_precio, readback_precio_currency) = (precio_antes,
precio_antes_currency)`), `app/precio/reglas.py` (el cooldown de
`decidir` salta el cambio `sin_efecto`), `app/precio/cambios.py`
(`cerrar_huerfanas` lee el precio vivo y resuelve: quieto →
`error/huerfana_sin_efecto`, movido al destino → `enviado` con ack
`{"origen": "huerfana_recuperada"}` y luego
`confirmado/lectura_viva`, otro o ilegible →
`error/huerfana_sin_patch`; reversas intactas, devuelve conteo),
`tests/test_precio_corrida.py` (9 pruebas DB + ayudantes
`_error_con_readback`, `_VivoFijo`, `_pendiente_huerfana`; retirado
`test_huerfana_se_cierra_sin_get_y_sale_en_resumen`, ver §4.6),
`tests/test_precio_reglas.py` (1 prueba pura).
Contratos intactos: `app/precio/tipos.py` (`sin_efecto` ya existía),
`puerto.py`, `config.py`, `app/precio_mercados.py`,
`app/estimacion_universo.py`, migraciones. `freno_por_error` intacto
(orden explícita). `cerrar_por_observacion` sin cambio (ver §4.1).

## 2. Rojo TDD (antes de implementar)

```console
$ pytest tests/test_precio_reglas.py -k s4_cooldown
E  AssertionError: assert False
E   +  where False = isinstance(Decision(resultado='mantener',
     motivo='cooldown', ...), PideCotizacion)
1 failed, 174 deselected

$ pytest tests/test_precio_corrida.py -k "test_s4_"
F.FF.F.F.
5 failed, 4 passed, 110 deselected
# Rojas (razón correcta):
# - test_s4_error_sin_efecto_no_consume_cooldown:
#   assert (1, 0) == (1, 1) (el cooldown bloqueó el reintento)
# - test_s4_huerfana_movida_en_corrida_confirma_y_cuenta:
#   assert ('error', None, None) == ('confirmado', ...)
# - test_s4_huerfana_quieta_es_error_sin_efecto:
#   'huerfana_sin_patch' != 'huerfana_sin_efecto'
# - test_s4_huerfana_ilegible_sella_sin_patch:
#   assert 0 == 1 (lecturas: no leía el vivo)
# - test_s4_dos_dias_sin_efecto_reintentan_y_arman_racha:
#   assert (1, 0) == (1, 1)
# Candados verdes antes y después: test_s4_error_con_efecto...,
# test_s4_huerfana_otro_precio..., test_s4_corrida_mx...,
# test_s4_racha_sin_efecto... (cada uno con su mutante en
# mutantes.md, todos rojos y revertidos).
```

## 3. Verde focalizado (después de implementar)

```console
$ pytest tests/test_precio_corrida.py tests/test_precio_reglas.py \
  tests/test_precio_write.py tests/test_precio02_mercado.py
392 passed in 27.91s

$ ruff check <5 tocados> && ruff format --check <5 tocados>
All checks passed! / 5 files already formatted
# (una E501 en el ayudante nuevo se arregló con ruff format)
```

No se corrió la batería completa (orden del brief).

## 4. Desviaciones con razón

1. `cerrar_por_observacion` sin cambio de código: ya recibía la
   `Vitrina`, ya filtraba por `c.platform` y ya leía
   `vitrina.observado` (viene de antes de S.4). El DoD 4 quedó como
   candado, con fixture endurecida (obs MX del ASIN americano que
   también cerraría) para que discrimine la capa SQL.
2. Las reversas `pendiente` siguen intactas: la guía textual no pide
   tocarlas y `test_huerfana_no_toca_reversas` lo fija. El bosquejo
   sugiere resolverlas "aquí" pero sin ramas definidas: fuera de
   alcance.
3. `cerrar_huerfanas` sigue devolviendo conteo (`int`), no
   `Mapping`: `Resumen.huerfanas` es conteo y la guía no pide
   cambiar el contrato.
4. Sin escrituras de readback en `cerrar_huerfanas`: la derivación
   usa `error_code`; la guía fija estados, códigos y ack exactos.
5. `freno_por_error` intacto (orden explícita del encargo). M10
   demuestra que filtrarlo rompería el DoD 5 literal.
6. Retirado `test_huerfana_se_cierra_sin_get_y_sale_en_resumen`:
   su contrato ("cero red", "SIN GET previo") es lo que S.4 cambia
   ("lee el precio vivo"). Reemplazado por 4 pruebas de ramas más
   1 por corrida (resumen incluido).
7. DoD 5 como alcance, no 4 corridas literales: `correr` usa el
   `now()` real y la base fija `decision_date` a hoy, así que los
   días se siembran y el reintento de hoy se corre de verdad; el
   cableado freno→`frenado(api_error)` ya lo cubre el test con
   freno espiado más el DoD 5 literal.
8. M10 salió rojo vía `no_converge`, no vía `subir`: al cegar el
   freno, `decidir` corre y el #10 dispara por el historial
   sembrado. Sigue rojo por la razón correcta (no `api_error`).
9. Arreglo del lead (r3): la rama movida pisaba `ack` y el sello
   trigger lo rechaza; `_sellar` ahora hace `COALESCE(ack, nuevo)`
   (nunca des-sella, calza con "el sello es UNA vez"). La semilla de
   `test_r1_a2_huerfana_es_evento_en_corrida_real` ganó `enviado_at`
   (producción siempre lo pone al insertar; sin él el CHECK
   `precio_cambio_envio_exige_ack` rechaza el `enviado`). Solo
   armado, cero aserciones tocadas. 802 passed con vecinas.
