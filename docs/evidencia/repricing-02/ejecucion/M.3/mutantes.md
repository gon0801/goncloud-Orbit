# Mutantes M.3 (39/39 muertos)

Applier: /tmp/m3_aplica_mutante.py + /tmp/m3_corre_mutantes.py (scratch,
no se commitean). Cada mutante debe hacer FALLAR su prueba; se restaura
con `cp` del respaldo (el arbol no estaba commiteado; jamas `git
checkout`). Obligatorio `PYTHONDONTWRITEBYTECODE=1 -B -p
no:cacheprovider`: sin esto, `__pycache__` valida un mutante del mismo
tamano contra el .pyc previo y el test pasa en falso (visto con
m13/m26).

| # | Archivo | Mutacion | Test que lo mata |
|---|---|---|---|
| m1 | meli/estimacion.py | quita `platform != meli` del guarda universo | test_universo_sin_cotizador_no_toca_red |
| m2 | meli/estimacion.py | `if not categoria or not tipo` -> `if False` | test_categoria_ausente |
| m3 | meli/estimacion.py | `venta - fijo` -> `venta + fijo` | test_cotiza_con_fijo_y_publicacion |
| m4 | meli/estimacion.py | `fee_contrato_incompatible` -> `fee_error` | test_respuesta_ilegible |
| m5 | meli/estimacion.py | `precio <= 0` -> `precio < 0` | test_precio_invalido_falla_fuerte |
| m6 | meli/estimacion.py | `fees_estimated_at=capturada` -> `None` | test_cotiza_por_listing_prices |
| m7 | meli/estimacion.py | SaleFee `> 0` -> `>= 0` | test_partir_fees_meli |
| m8 | meli/estimacion.py | `len != 1` -> `len == 0` | test_partir_fees_meli |
| m9 | meli/estimacion.py | `categoria_id` canonica -> `None` | test_canonica_fee_meli |
| m10 | meli/estimacion.py | prefijo `meli-fee:` -> `x:` | test_canonica_fee_meli |
| m11 | meli/estimacion.py | `estado != active` -> `== active` | test_oferta_cerrada_o_vieja |
| m12 | meli/estimacion.py | quita `or precio is None` | test_oferta_sin_precio |
| m13 | repository.py | rama meli `==` -> `!=` (cae a legacy) | test_tres_miembros |
| m14 | repository.py | manda `min(-carga)` -> `min(carga)` | test_empate_cercano_manda_carga_exacta |
| m15 | repository.py | quita rama desactualizada de union | test_fila_desactualizada |
| m16 | repository.py | quita `if info is manda: continue` | test_disponible (sin auto-entrada) |
| m17 | repository.py | `pertenece_a_total` False -> True | test_tres_miembros |
| m18 | repository.py | `miembros=canonica` -> `None` | test_cambio_miembro_resiembra |
| m19 | ingest.py | quita fail-closed sin meli_client | test_ingesta_meli_sin_cliente |
| m20 | ingest.py | `if not es_meli` -> `if True` | test_ingesta_meli_no_toca_secretos |
| m21 | ingest.py | quita despacho canonica fee meli | test_ingesta_meli_punta_a_punta |
| m22 | repository.py | `seller_sku` ancla -> que manda | test_tres_miembros (+ trigger) |
| m23 | politica_meli.sql | isr `0.025` -> `0.0206` | test_politica_meli_sql |
| m24 | 0073.sql | `IF plataforma meli` -> `IF FALSE` | test_0073_meli_acepta_costo |
| m25 | repository.py | `min(` -> `max(` del que manda | test_tres_miembros |
| m26 | repository.py | despacho partido meli `==` -> `!=` | test_disponible |
| m27 | repository.py | `_carga_de` -> `Decimal("0")` | test_empate_cercano |
| m28 | repository.py | colapso vocab -> `return codigo` | test_fee_error_colapsa |
| m29 | ingest.py | fail-closed DESPUES de abrir run | test_ingesta_meli_sin_cliente (count==0) |
| m30 | ingest.py | `except Exception` -> `except ValueError` en fee | test_fee_malformado_se_cuenta |
| m31 | ingest.py | conteo negativa -> `(1, 0, 1)` fijo | test_negativa_meli_reutiliza |
| m32 | meli/estimacion.py | `listing_fee_amount` -> `listing_fee` | test_cotiza_con_fijo_y_publicacion |
| m33 | meli/estimacion.py | quita check `currency_id` | test_listing_fee_ausente_o_moneda... |
| m34 | meli/estimacion.py | quita rechazo `> 4 decimales` | test_monto_con_mas_de_4_decimales |
| m35 | ingest.py | sello crash `ok=False` -> `ok=True` | test_ctor_amazon_falla_sella |
| m36 | 0073_reversa.sql | `<>` -> `=` en costo del listing | test_0073_reversa_restaura_0028 |
| m37 | repository.py | union `motivos` -> `motivos[:1]` | test_miembro_sin_costo_ni_muestra... |
| m38 | meli/estimacion.py | quita check moneda universo | test_oferta_usd_da_universo... |
| m39 | repository.py | quita `mot_in.append(res_envio)` | test_miembro_sin_costo_ni_muestra... |

Resultado: 39/39 muertos (corrida final sin cache).
Nota: m39-original (`motivos=[]` en costo) salio equivalente —
`calcular` re-deriva costo/fx (backstop); se redisenó a logistica,
unica via sin backstop. Ver `panel.md`.

## Adenda CodeRabbit (CR-1 + CR-2a, seccion 5 cierre)

| # | Archivo | Mutacion | Test que lo mata |
|---|---|---|---|
| m40 | meli/estimacion.py | `if _variantes_sin_mapear` -> `if False` | test_oferta_con_variante_sin_mapear_da_motivo |
| m41 | meli/estimacion.py | `if _variantes_sin_mapear` -> `if True` | test_oferta_con_variantes_todas_mapeadas_resuelve |
| m42 | ingest.py | filtro `!= meli` -> sin filtro | test_ingesta_meli_sin_cliente_salta_meli_y_sigue |
| m43 | ingest.py | filtro `!= meli` -> `== meli` | test_ingesta_meli_sin_cliente_llega_a_amazon |

Retirados m19, m29: probaban el fail-closed global jubilado por CR-1
(sustituidos por m42, m43). Resultado: 41/41 muertos, 0 anclas rotas.
