# R.1 bis-r2 — 11 tests que matan los 17 mutantes del lead (r1-cierre-r2)

Solo tests (cero cambios en `app/**`: `git status` final trae solo los dos
archivos de tests + este doc). Cada mutante se sembro con el script del lead
(`/Users/dn/dev/orbit-insumos/R.1-lead/mutantes_lead.py`) sobre el arbol de
trabajo, con base real
(`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`) y
`PYTHONPYCACHEPREFIX` fresco por mutante; el script restaura cada archivo al
terminar. Los 11 tests pasan en limpio
(`177 passed` en `test_precio_corrida.py` + `test_precio_pantalla.py`).
Cero sobrevivientes: 17/17 MUERE.

| Id | Prueba que lo mata | Linea FAILED |
|---|---|---|
| M1-aviso-ventana-umbral | `test_r1b2_m1_racha_que_sigue_no_reavisa` | `FAILED tests/test_precio_pantalla.py::test_r1b2_m1_racha_que_sigue_no_reavisa` |
| M9-ventas-sin-kind | `test_r1b2_m9_refund_y_fee_no_inflan_u15` | `FAILED tests/test_precio_corrida.py::test_r1b2_m9_refund_y_fee_no_inflan_u15` |
| M9b-primera-sin-kind | `test_r1b2_m9b_primera_venta_sale_solo_de_kind_sale` | `FAILED tests/test_precio_corrida.py::test_r1b2_m9b_primera_venta_sale_solo_de_kind_sale` |
| M10b-ingreso-sin-moneda | `test_r1b2_m10b_ingreso_60d_suma_solo_su_moneda` | `FAILED tests/test_precio_corrida.py::test_r1b2_m10b_ingreso_60d_suma_solo_su_moneda` |
| M17-salud-huerf-reversa | `test_r1b2_m17_salud_huerfana_reversa_no_cuenta` | `FAILED tests/test_precio_pantalla.py::test_r1b2_m17_salud_huerfana_reversa_no_cuenta` |
| M18-pricing-primera-del-dia | `test_r1b2_m18_pricing_del_dia_usa_la_mas_reciente` | `FAILED tests/test_precio_corrida.py::test_r1b2_m18_pricing_del_dia_usa_la_mas_reciente` |
| M20-freno-error-con-reversas | `test_r1b2_m20_reversas_en_error_no_frenan` | `FAILED tests/test_precio_corrida.py::test_r1b2_m20_reversas_en_error_no_frenan` |
| M32-freno-dias-15 | caso `r1b2_m32-freno-15` de `test_umbral_fuera_de_cota_aborta_al_arrancar` | `FAILED tests/test_precio_corrida.py::test_umbral_fuera_de_cota_aborta_al_arrancar[r1b2_m32-freno-15]` |
| M33-entero-acepta-bool | caso `r1b2_m33-freno-True` de `test_umbral_fuera_de_cota_aborta_al_arrancar` | `FAILED tests/test_precio_corrida.py::test_umbral_fuera_de_cota_aborta_al_arrancar[r1b2_m33-freno-True]` |
| M46-prioridad-no-persiste | `test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids` | `FAILED tests/test_precio_corrida.py::test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids` |
| M47-canal-no-persiste | `test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids` | `FAILED tests/test_precio_corrida.py::test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids` |
| M48-config-version-no-persiste | `test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids` | `FAILED tests/test_precio_corrida.py::test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids` |
| M49-cotizacion-no-persiste | `test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids` | `FAILED tests/test_precio_corrida.py::test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids` |
| M50-fee-obs-no-persiste | `test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids` | `FAILED tests/test_precio_corrida.py::test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids` |
| M54-listing-siempre-activo | `test_r1b2_m54_dia_inactivo_no_deja_perdiendo` | `FAILED tests/test_precio_corrida.py::test_r1b2_m54_dia_inactivo_no_deja_perdiendo` |
| M58-goal-anulado-vigente-pantalla | `test_r1b2_m58_goal_anulado_hoy_fuera_de_con_goal` | `FAILED tests/test_precio_pantalla.py::test_r1b2_m58_goal_anulado_hoy_fuera_de_con_goal` |
| M60-reversas-historicas | `test_r1b2_m60_reversa_de_ayer_no_consume_cupo` | `FAILED tests/test_precio_corrida.py::test_r1b2_m60_reversa_de_ayer_no_consume_cupo` |

Notas:

- M32/M33 no son pruebas separadas: son dos casos agregados a la
  parametrizacion existente de
  `test_umbral_fuera_de_cota_aborta_al_arrancar`, con ids explicitos
  (`r1b2_m32-freno-15`, `r1b2_m33-freno-True`) para que el FAILED los
  identifique.
- M46-M50 los mata UNA sola prueba (`..._m46_m50_...`), con un assert por
  columna persistida (prioridad, canal, config_version_id, cotizacion_id,
  fee_observation_id).
- M49: la corrida deja 2 cotizaciones en este fixture; la decision
  referencia la ultima (`max(id)`), que es lo que afirma el test.

## Salida completa del script de mutantes

Comando (rama `fase11/r1-cierre-r2`, arbol con solo los tests nuevos):

```bash
WT=/Users/dn/dev/goncloud-Orbit TESTS="tests/test_precio_corrida.py tests/test_precio_pantalla.py" ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres" ./.venv/bin/python /Users/dn/dev/orbit-insumos/R.1-lead/mutantes_lead.py M1-aviso-ventana-umbral M9-ventas-sin-kind M9b-primera-sin-kind M10b-ingreso-sin-moneda M17-salud-huerf-reversa M18-pricing-primera-del-dia M20-freno-error-con-reversas M32-freno-dias-15 M33-entero-acepta-bool M46-prioridad-no-persiste M47-canal-no-persiste M48-config-version-no-persiste M49-cotizacion-no-persiste M50-fee-obs-no-persiste M54-listing-siempre-activo M58-goal-anulado-vigente-pantalla M60-reversas-historicas
```

```text
M1-aviso-ventana-umbral MUERE | 1 failed, 174 passed, 1 warning in 21.90s | ['FAILED tests/test_precio_pantalla.py::test_r1b2_m1_racha_que_sigue_no_reavisa']
M9-ventas-sin-kind MUERE | 1 failed, 92 passed, 1 warning in 12.29s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m9_refund_y_fee_no_inflan_u15']
M9b-primera-sin-kind MUERE | 1 failed, 93 passed, 1 warning in 12.63s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m9b_primera_venta_sale_solo_de_kind_sale']
M17-salud-huerf-reversa MUERE | 1 failed, 175 passed, 1 warning in 20.88s | ['FAILED tests/test_precio_pantalla.py::test_r1b2_m17_salud_huerfana_reversa_no_cuenta']
M18-pricing-primera-del-dia MUERE | 1 failed, 99 passed, 1 warning in 13.65s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m18_pricing_del_dia_usa_la_mas_reciente']
M20-freno-error-con-reversas MUERE | 1 failed, 98 passed, 1 warning in 13.40s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m20_reversas_en_error_no_frenan']
M32-freno-dias-15 MUERE | 1 failed, 20 passed, 1 warning in 3.74s | ['FAILED tests/test_precio_corrida.py::test_umbral_fuera_de_cota_aborta_al_arrancar[r1b2_m32-freno-15]']
M33-entero-acepta-bool MUERE | 1 failed, 21 passed, 1 warning in 4.18s | ['FAILED tests/test_precio_corrida.py::test_umbral_fuera_de_cota_aborta_al_arrancar[r1b2_m33-freno-True]']
M46-prioridad-no-persiste MUERE | 1 failed, 97 passed, 1 warning in 12.94s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids']
M48-config-version-no-persiste MUERE | 1 failed, 97 passed, 1 warning in 13.67s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids']
M49-cotizacion-no-persiste MUERE | 1 failed, 97 passed, 1 warning in 13.34s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids']
M50-fee-obs-no-persiste MUERE | 1 failed, 97 passed, 1 warning in 13.11s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids']
M47-canal-no-persiste MUERE | 1 failed, 97 passed, 1 warning in 13.40s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m46_m50_fila_persiste_prioridad_canal_e_ids']
M54-listing-siempre-activo MUERE | 1 failed, 94 passed, 1 warning in 12.55s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m54_dia_inactivo_no_deja_perdiendo']
M10b-ingreso-sin-moneda MUERE | 1 failed, 95 passed, 1 warning in 13.10s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m10b_ingreso_60d_suma_solo_su_moneda']
M58-goal-anulado-vigente-pantalla MUERE | 1 failed, 176 passed, 1 warning in 20.98s | ['FAILED tests/test_precio_pantalla.py::test_r1b2_m58_goal_anulado_hoy_fuera_de_con_goal']
M60-reversas-historicas MUERE | 1 failed, 96 passed, 1 warning in 13.17s | ['FAILED tests/test_precio_corrida.py::test_r1b2_m60_reversa_de_ayer_no_consume_cupo']
 M tests/test_precio_corrida.py
 M tests/test_precio_pantalla.py
```
