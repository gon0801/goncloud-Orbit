# Mutantes de F.1

Un mutante por prueba nueva de `tests/test_precio02_atribucion.py`. Cada
uno se aplico sobre `app/ledger_atribucion.py`, se corrio su prueba con

	ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres" \
		PYTHONPATH=. uv run --frozen python -m pytest -q <prueba>

y se restauro el archivo. Los 9 en rojo (9/9 mueren).

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_venta_con_sku_unico_se_atribuye_por_sku | `len(candidatos) == 1` → `== 2` | ROJO |
| test_producto_fba_fbm_se_desempata_por_canal | `"AMAZON": "fba"` → `"AMAZON": "fbm"` | ROJO |
| test_lo_indecidible_no_se_escribe_se_cuenta_y_se_reintenta | `_SQL_CANAL_ORDEN` con `LIMIT 0` (canal jamas llega) | ROJO |
| test_primera_corrida_con_atribuidor_llena_la_historia | guarda `!= "sale"` → `== "sale"` | ROJO |
| test_evento_atribuido_no_se_vuelve_a_atribuir | `_SQL_LISTINGS` con `product_id + 1` | ROJO |
| test_dos_listings_del_mismo_canal_no_se_deciden | `len(ganadores) != 1` → `< 1` (el empate atribuye al primero) | ROJO |
| test_venta_sin_producto_no_se_atribuye | la guarda devuelve `Atribucion(listing_id=9, ...)` fija en vez de None (obligatorio del plan: "venta indecidible guardada como fila fija") | ROJO |
| test_atribuye_en_us_con_vocabulario_afn_mfn | `"AFN": "fba"` → `"AFN": "fbm"` | ROJO |
| test_mapea_canales_de_orden_conocidos | `"MFN": "fbm"` → `"MFN": "fba"` | ROJO |

Notas:

- El mutante de `test_venta_sin_producto` usa `listing_id=9` porque las
  migraciones siembran producto/listing antes del fixture (medido: el
  producto ajeno queda en id 3, su listing en 9). Un primer intento con
  `or 1` no enrojecio por eso mismo y se descarto.
- El harness borra `app/__pycache__` antes de cada corrida: dos intentos
  salieron "vivos" por bytecode rancio (mutante del mismo tamano y mismo
  segundo de mtime que el original) y enrojecieron al purgar. La fuente
  restaurada se verifico con la bateria focalizada en verde despues.
