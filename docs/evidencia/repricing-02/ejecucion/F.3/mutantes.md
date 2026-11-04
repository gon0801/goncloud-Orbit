# Mutantes de F.3

Un mutante por prueba nueva (26), cada uno corrido solo contra su prueba
con `ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres"`
y `PYTHONPATH=.`, purgando `app/__pycache__` antes de cada corrida.
Script: `/tmp/mutantes_f3.py` (no se commitea). 26/26 en rojo.

## Insumos (`app/estimacion_insumos.py`)

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_fbm_mx_resuelve_oferta | `universo is None` → `is not None` | ROJO |
| test_us_fbm_resuelve_oferta | moneda del mapa → `"MXN"` | ROJO |
| test_combinacion_fuera_del_registro | `return "universo_no_soportado"` → `return None` | ROJO |
| test_resolver_fx_tasa_vieja | guarda de `max_dias` → `if False:` | ROJO |
| test_us_sin_tasa_da_fx_ausente | motivo `fx_ausente` → fx falso con motivo None | ROJO |
| test_us_tasa_vieja_da_fx_desactualizado | `> max_dias` → `> max_dias + 100` | ROJO |

## Fees (`app/estimacion_fees.py`)

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_universo_us_fbm_cotiza_con_marketplace_us | `MARKETPLACE_US` → `MARKETPLACE_MX` | ROJO |
| test_cotizar_fbm_mx_sin_comision_logistica | `is not (canal == "fba")` → `is not True` | ROJO |
| test_cotizador_fbm_mx_pide_mfn | `cumplido = ... if fba else ...` → `"true"` (camino real a red) | ROJO |
| test_cotizar_a_precio_no_amplia_universo | matriz += `("amazon_us", "fba", "MXN")` | ROJO |

## Cotizador y partido (`app/estimacion_amazon.py`, registro en `universo.py`)

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_cotizador_us_pide_marketplace_us | cotizador US → `None` | ROJO |
| test_cotizador_fuera_del_registro_no_sale_a_red | `estado="error"` → `"success"` | ROJO |
| test_partir_referral_unico | fijo `total - variable` → `total` | ROJO |
| test_partir_solo_referral_fijo_cero | `!= 1` → `!= 2` | ROJO |
| test_partir_arbol_us | `(variable, fijo)` → `(fijo, variable)` | ROJO |
| test_partir_sin_referral_da_motivo | `!= 1` → `== 1` | ROJO |
| test_partir_dos_referrals_da_motivo | `!= 1` → `> 2` | ROJO |
| test_partir_referral_cero_no_cuenta | `> 0` → `>= 0` | ROJO |
| test_partir_y_derivar_coinciden | fijo `total - variable` → `total + variable` | ROJO |

## Escenarios (`app/estimacion_repository.py`, `app/estimacion_universo.py`, `0055`)

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_fbm_mx_disponible_con_origen_y_fees_partidos | `if fee_total is not None:` → `if False:` | ROJO |
| test_fbm_mx_sin_muestra_da_envio_sin_historia | motivo → `"fx_ausente"` | ROJO |
| test_us_disponible_convierte_muestra_y_parte_fees | muestra `/ fx` → `* fx` | ROJO |
| test_sin_referral_unico_da_fee_incompatible | `motivos.append(partido)` → `pass` | ROJO |
| test_fba_sigue_con_origen_politica_y_partidos | rama `politica` → `"envio_sin_historia"` | ROJO |
| test_0055_rechaza_disponible_sin_origen | CHECK origen `IS NOT NULL` → `IS NULL` | ROJO |
| test_0055_rechaza_disponible_sin_fees_partidos | CHECK partidos += `OR TRUE` | ROJO |
