# Mutantes de F.2

Un mutante por prueba nueva (16 de `tests/test_precio_envio.py` + 11 de
`tests/test_envio_muestras.py`), mas el obligatorio "familia sin donantes".
Cada uno se aplico sobre `app/precio/envio.py` o `app/envio_muestras.py`,
se corrio su prueba con

	ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres" \
		PYTHONPATH=. uv run --frozen python -m pytest -q <prueba>

(purgando `__pycache__` antes de cada corrida, leccion de F.1) y se restauro
el archivo. 28/28 en rojo.

## Puras (`app/precio/envio.py`)

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_clasifica_las_seis_identidades_de_f0 | `"ShippingHB": "shipping_hb"` → `"otros"` | ROJO |
| test_clasifica_lo_desconocido_y_lo_malformado_como_otros | default del mapa `"otros"` → `"shipping_hb"` | ROJO |
| test_costo_cuenta_la_etiqueta_una_vez_mas_hb | `max(...)` → `sum(...)` de las tres etiquetas (obligatorio) | ROJO |
| test_costo_con_solo_mfn_postage | `_ETIQUETAS` sin `"mfn_postage"` (obligatorio) | ROJO |
| test_costo_ignora_otros_y_sin_etiqueta_no_hay_costo | `_ETIQUETAS` con `"otros"` | ROJO |
| test_costo_solo_hb_no_es_etiqueta | `_ETIQUETAS` con `"shipping_hb"` | ROJO |
| test_costo_etiquetas_moneda_distinta_revienta | `raise MonedasMezcladas` → `continue` | ROJO |
| test_costo_hb_moneda_distinta_revienta | `total + extra` → suma manual sin checar moneda | ROJO |
| test_escalera_con_envios_propios_da_propio | `if propias:` → `if False:` | ROJO |
| test_escalera_con_un_solo_envio_propio_basta | `if propias:` → `if len(propias) > 1:` | ROJO |
| test_escalera_sin_propios_con_familia_da_mediana_de_medianas | medianas de hermanos → costos directos (mediana de ordenes) | ROJO |
| test_escalera_sin_familia_da_marketplace | medianas del pool → costos directos | ROJO |
| test_escalera_sin_ningun_envio_no_da_muestra | `if marketplace:` → `if True:` | ROJO |
| test_escalera_con_cero_propios_no_da_propio | `if propias:` → `if True:` (obligatorio) | ROJO |
| test_escalera_hermanas_sin_familia_cae_a_marketplace | `if hermanas and familia_id is not None:` → `if hermanas:` | ROJO |
| test_exclusiones_fija_el_orden_posicional | campo `ordenes_multiproducto` renombrado | ROJO |

## Job (`app/envio_muestras.py`)

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_orden_propia_da_muestra_propia | `abs(monto)` → `monto` (el ledger guarda negativo) | ROJO |
| test_orden_multiproducto_no_entra_y_se_cuenta | `len(unicos) > 1` → `> 2` | ROJO |
| test_orden_sin_producto_se_cuenta | `any(pid is None ...)` → `all(pid is not None ...)` | ROJO |
| test_cargo_otros_se_cuenta_y_no_entra_al_costo | `if fuente == "otros":` → `if False:` | ROJO |
| test_sin_propios_con_familia_da_familia | `familia_id=fid` → `familia_id=None` | ROJO |
| test_sin_propios_con_familia_da_familia (obligatorio extra) | `donantes=len(medianas)` → `donantes=None` en la rama familia (la base lo rechaza por CHECK) | ROJO |
| test_sin_familia_da_marketplace | `marketplace=todas` → `marketplace=[]` | ROJO |
| test_sin_envios_en_el_universo_no_hay_muestra | `continue` → `pass` con muestra None | ROJO |
| test_segunda_corrida_no_escribe | guarda de igualdad → `if False:` | ROJO |
| test_muestra_cambiada_agrega_fila | `_igual` → `return True` | ROJO |
| test_fuera_de_ventana_no_entra | ventana + 365 dias | ROJO |
| test_main_sin_dsn_sale_2 | `if not dsn:` → `if dsn:` | ROJO |
