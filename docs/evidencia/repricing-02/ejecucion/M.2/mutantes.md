# Mutantes M.2 (21/21 muertos, post-fixes panel)

Applier: /tmp/m2_aplica_mutante.py (scratch, no se commitea).
Comando por mutante: aplica, corre su test, restaura desde /tmp/m2ok/.

| # | Archivo | Mutacion | Test que lo mata |
|---|---|---|---|
| m1 | ledger.py | borra `"meli": "meli"` del mapa origen | test_meli_ya_no_se_excluye |
| m2 | ledger.py | `productos.get(sku)` -> `None` | test_venta_meli_fija_product_id_por_sku |
| m3 | ledger.py | `productos.get(sku)` -> `-1` | test_venta_meli_sin_sku_mapeado_queda_sin_producto |
| m4 | meli/ledger.py | `con_item = ...` -> `False` | test_atribuye_por_item_id_externo_unico |
| m5 | meli/ledger.py | `len(candidatos) == 1` -> `False` | test_atribuye_por_sku_con_un_solo_listing |
| m6 | meli/ledger.py | SQL sin `AND activo` | test_dos_listings_no_se_atribuye_y_se_reintenta |
| m7 | ledger.py | borra `"meli": atribuidor_meli` del registro | test_registro_por_defecto_atribuye_meli |
| m8 | ledger.py | guarda `viola signos` -> `if False` | test_reversa_positiva_se_salta_contada |
| m9 | ledger.py | `(refund,None)` -> `("fee","category")` | test_reembolso_meli_sin_categoria_guarda_fee_type_nulo |
| m10 | precio/envio.py | guarda platform -> `otros` | test_identidad_fee_es_meli_envio |
| m11 | precio/envio.py | `MFNPostageFee` -> `otros` | test_amazon_no_cambia |
| m12 | precio/envio.py | default `.get` -> `labman_label` | test_cargo_desconocido_cae_en_otros |
| m13 | envio_muestras.py | `otros += 1` -> `+= 0` | test_cargo_meli_desconocido_se_cuenta_en_otros |
| m14 | precio/envio.py | filtro `meli_envio` -> `otros` | test_cargo_meli_envio_da_muestra_propia |
| m15 | ledger.py | borra `(fee,isr_withheld)` del mapa | test_excluida_baja_a_cero_en_origen_realista |
| m16 | precio/envio.py | `total + extra` -> `extra` | test_meli_envio_suma_por_orden |
| m17 | precio/envio.py | suma -> `Importe(...)` moneda mezclada | test_meli_envio_moneda_mezclada_falla_fuerte |
| m18 | ledger.py | `if not sku: hueco` -> `if False` | test_venta_meli_sin_sku_cuenta_hueco |
| m19 | ledger.py | `elif qty None/<=0: hueco` -> `elif False` | test_venta_meli_mapeada_sin_cantidad |
| m20 | ledger.py | `elif productos None/get None` -> `elif False` | test_plan_sin_mapa_productos |
| m21 | ledger.py main | `meli_sin_producto={...}` -> `=0` | test_main_imprime_cobertura_meli |

| m22 | ledger.py | `if event_type == "refund"` -> `if False` | test_refund_meli_mismo_periodo_compensa_venta |

Resultado: 22/22 `1 failed` bajo mutacion; suite restaurada 22/22 passed.
