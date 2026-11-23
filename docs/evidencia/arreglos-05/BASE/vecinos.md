# Casos vecinos identificados (BASE.1)

Bloque `BASE` de `plans/arreglos-05.md`, fila `BASE.1`. Vecinos por causa, con las pruebas vigentes que ya los cubren en `33844bb3`. El numero es la cantidad de funciones `test_` del archivo; una funcion parametrizada trae mas de un caso. Son la linea verde sobre la que cada bloque debe mostrar su rojo y su verde. BASE no las corrio todas.

## En la propia base de 31 casos

- `test_04_vecino_impulso_no_envia_plan_borrado_tras_fallar_nueva_previa` es el vecino de TN04 para el flujo de impulso. Ambos pasan.
- TN07 tiene dos variantes, corriente y persistida. Cubren cotizacion fresca y reutilizada sin otro GET.
- TN01 tiene cuatro: `shadow`, `off` y `live` con y sin reenvio. Es el control de "la reconciliacion solo lee" que A, B y H deben conservar.
- TN02 tiene `missing` y `truncated`. Cubren contenedor ausente y lista cortada.

## Por bloque

| Bloque | Causa vecina | Pruebas vigentes |
|---|---|---|
| A | Un 5xx en una puja, ciclo degradado sin reintento, `running` huerfano, conciliacion antes de decidir | Legadas F1 a F4 en `docs/evidencia/arreglos-2026-11-12/pruebas/ciclo/test_revision_ciclo.py` (hoy rojas). `tests/test_cycle.py` (54), `tests/test_cycle_apply.py` (16), `tests/test_lecturas_caso.py` (19). |
| B | Keyword ajena o adoptada, negative con ID cambiado, reversa tras mano del dueno, crash durante la reversa | `tests/test_apply_harvest.py` (57), `tests/test_reversa_harvest.py` (30), `tests/test_apply.py` (62), `tests/test_temporada01_b.py` (21), `tests/test_temporada01_e.py` (8). Legadas en `pruebas/apply/`: `test_rev_apply.py`, `test_sub_a_reversa_pausa_producto.py`. |
| C | Dos CLI a la vez, crash antes y despues del POST, del ACK y del readback, rechazo definitivo | `tests/test_archiva_inertes.py` (47), `tests/test_ads_archivar.py` (19). Legada `pruebas/apply/test_sub_b_inertes_reponer_sin_intencion.py`. |
| D | Mutacion fuera del cliente protegido, 207 por item, sonda mutante | `tests/test_ads_write.py` (29), `tests/test_fabrica_f2_hermanas.py` (52), `tests/test_fabrica_f2_biblioteca.py` (28). Legadas `test_sub_a_bloqueo_207_error_ignorado.py` y `test_sub_b_smoke_207_rechazo_sella_ok.py`. |
| E | Practica vencida, practica vigente y live sobre la misma clave, dos workers | `tests/test_apply_cola.py` (44), `tests/test_bloqueo_dueno_efecto.py`, `tests/test_arq_bids_p.py`. En produccion hay 31 practicas shadow vencidas y 0 claves con historia live y practica en vuelo ([invariantes.md](invariantes.md)). |
| F | Overlay de temporada alto, snapshot viejo sin base, replay con inputs congelados | `tests/test_cycle_target_ciclo.py` (20), `tests/test_target_margen.py` (28), `tests/test_temporada01_d.py`, `tests/test_temporada01_i.py`. |
| G | Termino maduro reobservado, dias inmaduros, replay igual | `tests/test_cycle.py` (54), `tests/test_optimizer_windows.py` (16), `tests/test_temporada01_f.py` (13). |
| H | 151 ordinarias, 149 mas una ambigua, recuperacion, dos procesos, entrada y regreso exentos | `tests/test_apply.py` (62), `tests/test_apply_schema.py` (26), `tests/test_temporada01_b.py` (21), `tests/test_temporada01_e.py` (8). |
| I | Formulario con `1,000`, API con null, bool o fraccion, version vieja | `tests/test_config_write.py` (24), `tests/test_settings_escritura.py`, `tests/test_goals_write.py`. |
| J | Modo ausente o ilegible, pausa por producto en practica, replay de decisiones antiguas | `tests/test_optimizer_politica.py` (63), `tests/test_optimizer_goals.py` (61), `tests/test_temporada01_a.py`, `tests/test_productos_pausa.py`. |
| K | TaxAmount anidado, total inclusivo, impuesto separado | `tests/test_arreglos02_cargos.py`, `tests/test_estimacion_s4_caja_real.py`, `tests/test_precio02_herramientas_fees.py`. |

## Vecinos de entorno

Corrieron en BASE y deben seguir verdes en cada bloque que toque el ciclo o la configuracion:

- Controles TEMPORADA B, E y F: 34 pruebas (`tests -k 'temporada01_b_ or temporada01_e_ or temporada01_f_'`).
- Controles 01, 08, 09, D2, banda 01X y los tres de ARREGLOS 02, dentro de la corrida completa.
