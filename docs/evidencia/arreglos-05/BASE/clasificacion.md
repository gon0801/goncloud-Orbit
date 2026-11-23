# Laboratorio, corrida completa y clasificacion de los 31 casos (BASE.1)

Bloque `BASE` de `plans/arreglos-05.md`, fila `BASE.1`.

## Laboratorio

- Python 3.12.14 por `uv sync --frozen --python 3.12`.
- PostgreSQL 16.15 desechable en `127.0.0.1:55449`, el contenedor `orbit-review-current-pg`. Sin bases `thermo_*_current` al empezar y sin otras conexiones.
- Edge como Chromium por `ORBIT_CHROMIUM`.
- Sin credenciales reales. El transporte es `httpx.MockTransport`, los tokens son ficticios y la prueba de precios apunta `ORBIT_SECRETS_DIR` a una carpeta que no existe.

## Corrida completa

```bash
bash docs/evidencia/arreglos-05/BASE/acepta.sh ALL resultados.xml > resultados.log 2>&1
```

La entrada aplica las guardas de la aceptacion y pasa a `comprobar.sh` el Python 3.12 del proyecto; llamando a `comprobar.sh ALL` a secas esas guardas no corren.

- SHA: `c98ad4bd`, con el arbol limpio. El codigo ejecutable es el mismo que en `2639ff8f`.
- Fecha: 2026-11-23, de 07:44 a 07:46 UTC. Duracion 120 s.
- Resultado: 31 casos, 5 fallan, 26 pasan, 0 errores, 0 omisiones. Salida 1 por los cinco rojos de producto.
- Archivos: [resultados.xml](resultados.xml) y [resultados.log](resultados.log).

## Clasificacion

La tabla la genera [clasifica.py](clasifica.py), que compara esta corrida con la original de la revision (`revision/acceptance.xml`, `ff03ed61`). Un rojo es de producto solo si repite la excepcion y la sentencia que ya fallaban en la revision.

```bash
uv run --frozen --python 3.12 python docs/evidencia/arreglos-05/BASE/clasifica.py \
  docs/evidencia/arreglos-05/revision/acceptance.xml docs/evidencia/arreglos-05/BASE/resultados.xml
```

| Caso | Grupo | Hallazgo | Dueno vigente | En ff03ed61 | Ahora | Clase | Exigido |
|---|---|---|---|---|---|---|---|
| test_02_lista_incompleta_no_autoriza_repost[missing] | writes | TN02 | 04 B-C | falla | pasa | verde | verde |
| test_02_lista_incompleta_no_autoriza_repost[truncated] | writes | TN02 | 04 B-C | falla | pasa | verde | verde |
| test_03_harvest_y_reversa_no_archivan_keyword_ajena | writes | TN03 | 05 B | falla | falla | producto | producto |
| test_04_retiro_no_envia_plan_borrado_tras_fallar_nueva_previa | ui | TN04 | 04 W-P | falla | pasa | verde | verde |
| test_04_vecino_impulso_no_envia_plan_borrado_tras_fallar_nueva_previa | ui | TN04 | 04 W-P | falla | pasa | verde | verde |
| test_05_fuente_corrupta_no_publica_subtotal_ni_recrea_clave | ingest | TN05 | 03 D-G | falla | pasa | verde | verde |
| test_06_scan_incompleto_no_desactiva_ausentes | ingest | TN06 | 03 D-M | falla | pasa | verde | verde |
| test_07_corrida_meli_conserva_fecha_y_detalle[corriente] | pricing | TN07 | 03 P-G | falla | pasa | verde | verde |
| test_07_corrida_meli_conserva_fecha_y_detalle[persistida] | pricing | TN07 | 03 P-G | falla | pasa | verde | verde |
| test_10_negative_reconciliado_conserva_reversa | writes | TN10 | 05 B | falla | falla | producto | producto |
| test_11_reponer_no_recrea_tras_ack_y_readback_ambiguo | writes | TN11 | 05 C | falla | falla | producto | producto |
| test_12_la_meta_avanza_en_ciclos_live_con_ledger_rancio | optimizer | TN12 | 05 F | falla | falla | producto | producto |
| test_13_reobservar_un_termino_maduro_no_aborta_el_ciclo | optimizer | TN13 | 05 G | falla | falla | producto | producto |
| test_14_contencion_no_declara_escrituras_revertidas | ingest | TN14 | 03 D-M | falla | pasa | verde | verde |
| test_15_ruido_no_publica_acos_con_gasto_desconocido | ui | TN15 | 04 W-E | falla | pasa | verde | verde |
| test_16_resumen_no_afirma_ausencia_de_ventas_que_api_reporta | ui | TN16 | 04 W-D | falla | pasa | verde | verde |
| test_S1_error_de_proveedor_redactado_antes_de_persistir | ingest | TNS1 | 03 D-N | falla | pasa | verde | verde |
| test_control_arreglos01_b_no_harvestea_keyword_exact_propia | optimizer | control | revision | pasa | pasa | verde | verde |
| test_control_auth_query_y_form_no_sustituyen_header | ui | control | revision | pasa | pasa | verde | verde |
| test_control_contrato_resumen_conserva_desconocido_y_csp | ui | control | revision | pasa | pasa | verde | verde |
| test_control_productos_no_publica_subtotal_en_ningun_orden | ingest | control | revision | pasa | pasa | verde | verde |
| test_01_reconciliacion_no_reenvia_bid_anterior[live-False] | writes | control 01 | 01K | pasa | pasa | verde | verde |
| test_01_reconciliacion_no_reenvia_bid_anterior[live-True] | writes | control 01 | 01K | pasa | pasa | verde | verde |
| test_01_reconciliacion_no_reenvia_bid_anterior[off-True] | writes | control 01 | 01K | pasa | pasa | verde | verde |
| test_01_reconciliacion_no_reenvia_bid_anterior[shadow-True] | writes | control 01 | 01K | pasa | pasa | verde | verde |
| test_08_aplicar_y_guardas_reversa_con_unidades_reales | pricing | control 08 | 01R | pasa | pasa | verde | verde |
| test_09_jev_cron_cierra_corrida_sin_forzar_y_preserva_tx_ajena | ingest | control 09 | 01 (de895d18) | pasa | pasa | verde | verde |
| test_arreglos02_cargos_y_reversa_existentes[test_arreglos02_d_el_cargo_sin_source_event_id_no_se_descarta] | pricing | control ARREGLOS 02 | 02 D | pasa | pasa | verde | verde |
| test_arreglos02_cargos_y_reversa_existentes[test_arreglos02_d_la_estimada_no_se_suma_a_la_comision_real] | pricing | control ARREGLOS 02 | 02 D | pasa | pasa | verde | verde |
| test_arreglos02_cargos_y_reversa_existentes[test_arreglos02_d_la_reversa_regresa_las_vistas] | pricing | control ARREGLOS 02 | 02 D | pasa | pasa | verde | verde |
| test_D2_retencion_ayer_sin_liberar_no_abre_hoy | pricing | control D2 | 01E | pasa | pasa | verde | verde |

- Casos recogidos: 31; la base trae 31.
- Por clase: producto 5, verde 26.
- Cerrados antes de 05 y controles (deben estar verdes): 26; de 05 (hoy rojos de producto): 5.
- Casos fuera de lo exigido: 0.
- Casos de la base ausentes: 0.
- Casos nuevos que la base no trae: 0.

Ningun caso esta fuera de lo exigido ni ausente. Los cerrados por 03 y 04 y los controles estan verdes. Los cinco rojos son de producto y pertenecen a 05.

## Los cinco rojos de producto

| Caso | Dueno | Sentencia que falla | Lo que hace hoy el codigo |
|---|---|---|---|
| `test_03_harvest_y_reversa_no_archivan_keyword_ajena` | 05 B | `assert not any("ajena" in b["keywordIdFilter"]["include"] for b in result["deletes_keyword"]), result` | La reversa manda un DELETE que incluye la keyword ajena. |
| `test_10_negative_reconciliado_conserva_reversa` | 05 B | `assert result["status"] == 200, result` | La reversa responde 422 porque no resuelve el `negative_id` desde el ledger. |
| `test_11_reponer_no_recrea_tras_ack_y_readback_ambiguo` | 05 C | `assert result["posts"] == 1, result` | Hace 2 POST a `/sp/keywords`. |
| `test_12_la_meta_avanza_en_ciclos_live_con_ledger_rancio` | 05 F | `assert actual == expected, result` | La meta se queda en `[29.0, 29.0, 29.0]` en lugar de `[29.0, 28.5, 28.0]`. |
| `test_13_reobservar_un_termino_maduro_no_aborta_el_ciclo` | 05 G | `assert result["status"] == "done", result` | El ciclo termina `failed` por `decision_dato_no_del_futuro`. |

## Como llego la base a este estado

| Corrida | Salida | Que paso |
|---|---|---|
| 1 | 4 | Cero casos. El shell de sesion trae `MSYS_NO_PATHCONV=1` y `comprobar.sh` le paso a Python rutas `/c/...`. Entorno. |
| 2 | 2 | Error de recoleccion. `revision/ingest.py` importaba `app.reputacion_clientes`, que ya no existe. Fixture. |
| 3 | 1 | 7 rojos y 24 verdes. Tres eran de fixture: TN07 en sus dos variantes y TN11. Los otros cuatro, de producto. |
| 4 a 6 | 1 | Comprobaciones sueltas de cada porte con `T pricing test_07` y `T writes test_11`, hasta que TN07 paso y TN11 fallo por su asercion. |
| 7 | 1 | 5 rojos y 26 verdes, con los tres fixtures portados. |
| 8 | 1 | 5 rojos y 26 verdes sobre `2639ff8f`, con el lanzador directo. |
| Definitiva | 1 | La misma cuenta sobre `c98ad4bd`, por `acepta.sh ALL`. Es la guardada. |

Los portes estan descritos en [diferencias.md](diferencias.md). Ninguna asercion cambio.
