# Mutantes M.4 (36/36 muertos, 0 anclas rotas)

Applier: /tmp/m4_aplica_mutante.py + /tmp/m4_corre_mutantes.py
(scratch, no se commitean). Cada mutante debe hacer FALLAR su
prueba; se restaura con `cp` del respaldo (jamas `git checkout`).
Obligatorio `PYTHONDONTWRITEBYTECODE=1 -B -p no:cacheprovider`.

Huecos de numeracion (m19, m26): mutantes descartados por
equivalentes durante el diseno, no se renumero.

| # | Archivo | Mutacion | Test que lo mata |
|---|---|---|---|
| m1 | write_client.py | `!= _MODO_LIVE` -> `==` | test_escritor_exige_modo_live |
| m2 | write_client.py | quita `and not sonda` | test_poner_precio_sonda...red_una_vez |
| m3 | write_client.py | sello -> `if False` | test_poner_precio_sin_sellar_no_toca_red |
| m4 | write_client.py | regex item `^MLM\d+$` -> `.*` | test_poner_precio_valida_sin_red |
| m5 | write_client.py | quita redondeo a centavo | test_poner_precio_valida_sin_red |
| m6 | write_client.py | quita `moneda != MXN` | test_poner_precio_valida_sin_red |
| m7 | write_client.py | quita duplicado de variantes | test_poner_precio_valida_sin_red |
| m8 | write_client.py | 401 `forzados < 1` -> `< 2` | test_put_401_doble...sin_loop |
| m9 | write_client.py | 429 -> `if False` | test_put_429_reintenta_una_vez_y_5xx_no |
| m10 | write_client.py | 200 -> 201 | test_poner_precio_sonda...red_una_vez |
| m11 | write_client.py | `"price"` -> `"precio"` | test_poner_precio_sonda...red_una_vez |
| m12 | write_client.py | quita llamada a refresco | test_put_401_refresca_y_reintenta_una_vez |
| m13 | precio_mercado.py | `clave != meli` -> `==` | test_vitrina_sin_clientes_solo_lee |
| m14 | precio_mercado.py | vitrina `if ...None` -> `if False` | test_vitrina_sin_clientes_solo_lee |
| m15 | precio_mercado.py | `estado active` -> `closed` | test_catalogo_activas_g27 |
| m16 | precio_mercado.py | `platform meli` -> `amazon_mx` | test_catalogo_activas_g27 |
| m17 | precio_mercado.py | `is None` -> `is not None` | test_insumos_variante_sin_mapear |
| m18 | precio_mercado.py | `if not miembros` -> `if miembros` | test_insumos_sin_miembros_da_costo_ausente |
| m20 | precio_mercado.py | precio obs `+ 1` | test_insumos_disponible...que_manda |
| m21 | precio_mercado.py | `exposiciones=visitas` -> `None` | test_historia_meli_disponibilidad_y_visitas |
| m22 | precio_mercado.py | `== active` -> `!= active` | test_historia_meli_disponibilidad_y_visitas |
| m23 | precio_mercado.py | `variantes = []` | test_escribir_sonda_pasa_y_valida_destino |
| m24 | precio_mercado.py | quita `if not unidad.externo` | test_escribir_sonda_pasa_y_valida_destino |
| m25 | precio_mercado.py | quita `precio.moneda != oferta...` | test_cotizar_error_y_moneda_distinta |
| m27 | precio_mercado.py | `price` -> `cost` | test_precio_vivo_ok_y_ausente |
| m28 | reputacion_clientes.py | quita refresco en 401 | test_cliente_meli_401... (reputacion) |
| m29 | precio_mercado.py | `fee_type` -> `fee_tipe` | test_cotizar_parte_fees_meli |
| m30 | precio_mercado.py | `fila is None` -> `is not None` | test_cotizacion_guardada...no_concilia |
| m31 | precio_mercado.py | `precio_q !=` -> `==` | test_cotizacion_guardada...no_concilia |
| m32 | precio_mercado.py | `suma != total` -> `if False` | test_cotizacion_guardada...no_concilia |
| m33 | precio_mercado.py | wrapper solo `ValueError` | test_cotizacion_guardada_corrupta... |
| m34 | precio_mercado.py | stock `<= 0` -> `< 0` | test_historia_meli_disponibilidad_y_visitas |
| m35 | precio_mercado.py | reraise DB -> `pass` | test_insumos_error_db_relevanta |
| m36 | precio_mercado.py | B1 `Exception` -> `ValueError` | test_insumos_fallo_interno_no_tumba_lote |
| m37 | fuentes.py | `despues >` -> `>=` | test_direccion_sin_movimiento_es_none |
| m38 | insumos.py | `asin=fila[3]` -> `fila[2]` | test_oferta_para_cotizar_devuelve_campos |
| m39 | precio_mercado.py | `if huerfanas` -> `if False` | test_escribir_bloquea_variaciones_vivas_sin_mapear |
| m40 | precio_mercado.py | `vivas - miembros` -> `vivas` | test_escribir_acepta_vivo_igual_a_miembros |

Adenda CodeRabbit (CR-2b): m2/m3 re-anclados a `exigir_sello`
(el `if ... and not sonda` se partio en dos). Resultado: 38/38
muertos, 0 anclas rotas.
