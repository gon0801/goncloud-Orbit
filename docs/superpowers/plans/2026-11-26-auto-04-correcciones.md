# AUTO-04 Correcciones: matriz de fallos, arquitectura y plan de pruebas

- **Fecha:** 2026-11-26
- **Rama base:** `master` (`032e6953`, desplegado en produccion en `shadow`, sin caps `ads_apply_cap_*_ubicacion`)
- **Alcance:** cerrar los 5 P1 de la auditoria thermo-nuclear de AUTO-04 (`P1-1` a `P1-5`), el P1 adicional descubierto por esta matriz (`P1-6`), y reemplazar la cobertura parcial de `tests/test_cycle_ubicacion.py` por una suite extremo a extremo real (`cycle.corre_ciclo` / `cycle.atender_plataforma` contra PostgreSQL 16 + `httpx.MockTransport` en la frontera HTTP).
- **Referencias:** `docs/superpowers/specs/2026-11-26-auto-04-ubicaciones-design.md`, `docs/APPLY.md` (§5, §6, §9.1, §9.4), `docs/CONTEXTO.md` (Reglas 1-10), `tests/test_architecture.py`.

---

## 1. Matriz de fallos por frontera de integracion

Cada fila analiza fallos simples y comunes en las 12 fronteras de AUTO-04 sobre `032e6953`, el comportamiento exigido por la spec y la prueba que lo demuestra.

| # | Frontera | Fallo simple comun | Estado actual en `032e6953` (`archivo:linea`) | Comportamiento correcto (spec / `APPLY.md`) | Severidad | Prueba que lo demuestra |
|---|---|---|---|---|---|---|
| 1a | Lectura de placements | Campana activa con 0 impresiones en el dia exacto `D-3` (plataforma fresca hasta `D-1`) | `app/ubicacion_io.py:386-392`: `max(info["max_fecha"]) >= hasta` por campana -> `placements_frescos = False` -> falso `datos_placements_incompletos` | Frescura medida a nivel plataforma (`D-3` presente en `ads_placement_observation` de `platform`); la conciliacion `gasto_placements_total` vs `gasto_campana` detecta huecos por campana | **P1-1** | `test_ubicacion_io.py::test_frescura_placements_es_por_plataforma_y_conciliacion_caza_huecos` + E2E `test_cycle_ubicacion.py::test_e2e_p1_1_campana_sin_impresiones_en_d3_recorta` |
| 1b | Lectura de placements | Ingesta de placements atrasada a nivel plataforma (sin filas de ningun anuncio en `D-3`) | `app/ubicacion_io.py:386-392` devuelve `False` | `placements_frescos = False` -> todas las campanas abstienen con `datos_placements_incompletos` | OK | `test_ubicacion_io.py::test_banda_de_metas_desde_hojas_y_conciliacion_de_gasto` + E2E |
| 1c | Lectura de placements | Hueco real en placements de UNA campana (faltan dias, `sum(placement.cost) != gasto_campana`) | `app/optimizer/ubicacion.py:327-329` rechaza con `descuadre_placements_campana` | Abstener con `descuadre_placements_campana` (Regla 10) | OK | `test_ubicacion_io.py::test_frescura_placements_es_por_plataforma_y_conciliacion_caza_huecos` |
| 1d | Lectura de placements | Campana sin ninguna fila de placements en ventana, o fila con `cost`/`orders` `NULL`, o `v_metric_latest` vacio | `total = None` o `_gasto_campana = None` -> `datos_placements_incompletos` | Abstener con `datos_placements_incompletos` (Regla 3, nunca 0 inventado) | OK | `test_ubicacion_io.py::test_suma_placements_presentes_y_ceros_medidos_sin_fila` |
| 1e | Lectura de placements | Falta `D-2` o `D-1` en ingesta de placements (inmaduros incompletos) | `cubre_inmaduros = False` -> `pedidos_inmaduros = None` -> abstiene recortes sin venta | No recortar por `gasto_sin_venta` sin cobertura de `D-2..D-1`; recortes por ACoS maduro si pueden evaluar | OK | `test_ubicacion_io.py::test_inmaduros_exige_cobertura_y_cero_sin_filas` |
| 1f | Lectura de placements | Excepcion de BD en TX2 o `ads_ubicacion_<plat>` ausente/`off` | Excepcion revierte TX2 y sella `failed`; setting ausente devuelve `()` | Fail-closed: `off` no lee ni escribe; error BD aborta TX2 sin comprometer TX3 | OK | `test_cycle_ubicacion.py::test_e2e_ubicacion_off_no_escribe_ni_toca_http` (`off`) + `test_cycle_ubicacion.py::test_e2e_error_bd_tx2_aborta_sin_tx3_y_libera_lock` (Postgres caido / `OperationalError` en TX2; `tests/test_cycle.py::test_sello_fail_closed_decide_explota` solo cubre error de logica) |
| 2a | Metas por hoja/producto (TX2) | Fallo de lectura de productos en TX2 (`lee_entradas_productos` lanza error en SAVEPOINT) en campana mixta (1 hoja previa `margen_producto`, 1 hoja `setting_plataforma`) | `app/cycle_recorrido.py:270-273` retorna antes de `hojas_aptas` y `targets`; `lee_casos` construye `MetaCampana` solo con la otra hoja y TX3 no ve la hoja perdida -> propone/aplica movimiento violando banda `[exigente, laxa]` | Registrar la hoja en `_ProductosCiclo.hojas_sin_evidencia` y sustituir la campana por `mantener('producto_sin_evidencia')` en TX3 | **P1-4** | `test_cycle_ubicacion.py::test_e2e_p1_4_fallo_lectura_productos_tx2_abstiene_campana_mixta` |
| 2b | Metas por hoja/producto (TX3) | Fallo al guardar `evaluacion_producto_ciclo` en TX3 (`guarda_evaluaciones` falla en SAVEPOINT) | `app/cycle_sombra.py:508-515` detecta `pid not in ids_evaluacion` y marca `producto_sin_evidencia` | Sustituir la campana afectada por `mantener('producto_sin_evidencia')` en TX3 | OK | `test_cycle_ubicacion.py::test_e2e_fallo_guardado_productos_tx3_abstiene_campana` |
| 2c | Metas por hoja/producto (TX2) | Campana sin ninguna hoja con target resuelto (`sin_target`) | `caso.meta = None` -> `sin_meta_campana` | Abstener con `sin_meta_campana` | OK | `test_cycle_ubicacion.py::test_e2e_sin_meta_campana_abstiene` |
| 2d | Metas por hoja/producto (TX2) | Motor vecino `ads_bid_politica_<plat>` apagado (`None`) | `app/cycle_recorrido.py:306-321` guarda `contadores.targets` antes del `if politica is None: return` | Las metas de hoja siguen congelandose y alimentan la banda `[exigente, laxa]` de ubicaciones | OK | `test_cycle_ubicacion.py::test_e2e_p1_3_temporada_baja_frena_subida_sin_bid_politica` |
| 3a | Temporada | `ads_temporada_<plat> = live` en mes `baja` con `ads_bid_politica_<plat>` apagado (ausente) | `app/cycle_recorrido.py:530-555,645`: `plat_t` solo se construye dentro de `if politica is not None:`, asi que `fase_temporada` llega `None` y permite **subir** multiplicadores en baja | Leer `fase_temporada` desde `target_ciclo.overlay.estacion.fase` (con `overlay.modo == 'live'`) sin depender de `politica` -> `temporada_baja_sin_subida` | **P1-3** | `test_cycle_ubicacion.py::test_e2e_p1_3_temporada_baja_frena_subida_sin_bid_politica` |
| 3b | Temporada | `ads_temporada_<plat> = shadow` (practica) o ausente (`off`) | Con `plat_t` no-None en `shadow` frenaba subidas (P2-6); ausente devuelve `None` | Solo `overlay.modo == 'live'` frena subidas; en `shadow`/`off` `fase_temporada` es `None` (mismo criterio `en_vivo` que bids/cortes) | P2-6 (cerrado con P1-3) | `test_cycle_ubicacion.py::test_e2e_temporada_shadow_no_frena_subida_ubicacion` |
| 3c | Temporada | `ads_temporada_<plat> = live` en `baja` con campana que califica para **recorte** | `_mejor_recorte` corre antes de `_compuerta_de_subida` | El recorte si procede en temporada baja; solo se frenan subidas | OK | `test_optimizer_ubicacion.py` + E2E |
| 4a | Guardado de evaluaciones (TX3) | Excepcion de BD en `guarda_evaluaciones` o perdida de claim en `_compromete_tx3` | TX3 entera corre en `with conn.transaction():` -> rollback atomico, envelope pasa a `failed` o `CicloOcupado` | Cero filas parciales en `ubicacion_evaluacion`, `decision` o `decision_committed_at`; siguiente horario reintenta | OK | `test_cycle_ubicacion.py::test_e2e_claim_perdido_en_tx3_no_deja_ubicacion_evaluacion` |
| 4b | Guardado de evaluaciones (TX3) | `config_version` vacia (`config_id is None`) | `settings = {}` -> `modo_ubicacion = 'off'` -> `contadores.ubicaciones = []` -> no inserta | Fail-closed, cero filas | OK | Cubierto por `modo_ubicacion_desde_settings` |
| 5a | Reconciliacion (`live`) | Error HTTP transitorio (500, 429, timeout, lectura ilegible) o `SinPerfilAjuste` al reconciliar ubicacion pendiente | `app/cycle_apply.py:294-306`: dentro del mismo `try/except` global de `_fase_reconciliacion`, hace `conn.rollback()` (perdiendo las escrituras aun sin commit de harvest/bids/reversas, salvo las commiteadas a mitad del lazo) y devuelve `aplicador = None`, cancelando pujas, `libera_vencidos` y reactivaciones (`encola_cortes` si corre) | Hacer `conn.commit()` de harvest/bids/reversas y aislar `reconcilia_automaticos` en su propio `try/except`: para fallos no-auth anota `ubicaciones_reconcilia_error`, conserva `aplicador` y no revierte nada previo | **P1-2** | `test_cycle_ubicacion.py::test_e2e_p1_2_error_reconcilia_ubicaciones_no_tira_apply_del_ciclo` |
| 5f | Reconciliacion (`live`) | Rechazo de identidad (`401`, `403`, `AdsAuthError`) al reconciliar ubicacion pendiente | Antes anulaba `aplicador = None` + `apply_error` (correcto para fallo global de auth), pero un `except` aislado demasiado amplio dejaria seguir pujas/cortes/reactivaciones con credenciales rechazadas | Con `es_identidad_rechazada(exc)`, conservar fallo global: `conn.rollback()` (sin tocar commits previos de harvest/bids/reversas), cerrar `aplicador`, anotar `apply_error` y devolver `aplicador = None` para que no salga ninguna mutacion | **P1-2 (ajuste Opus)** | `test_cycle_ubicacion.py::test_e2e_p1_2_401_reconcilia_ubicaciones_aborta_apply_global` |
| 5b | Reconciliacion (cliente y fabrica) | `reconcilia_automaticos` en `_fase_reconciliacion` llama `apply._cliente_ajuste(platform, transport=None)` | `app/cycle_apply.py:298` ignora `aplicador_factory`, repite `GET /v2/profiles`, no lleva `guard_http` y no cierra cliente | Reutilizar `aplicador._cliente()` (respeta `aplicador_factory`, `transport` y `guard_http=guard`) y cerrar cliente al salir | **P1-2 / P1-6** | Cubierto de forma indirecta por los E2E con `aplicador_factory` (`test_e2e_p1_2_error_reconcilia_ubicaciones_no_tira_apply_del_ciclo`, `test_e2e_p1_2_reconcilia_ubicaciones_en_shadow_y_off`, `test_e2e_claim_perdido_en_reconcilia_ubicacion_aborta_sin_sellar`) y `test_apply_ubicacion.py::test_transport_guardado_y_aplicador_close_liberan_pool` |
| 5c | Reconciliacion fuera de `live` o tras cierre del dia | Pendiente en `campana_ajuste` cuando `ads_optimizer_mode` baja a `shadow`/`off`, o en horario posterior tras `apply.completo = True` (`ciclo_id = None`) | `app/cycle_apply.py:240-277`: rama `modo.modo != 'live'` solo mira `hay_reversas_pause_abiertas` y nunca llama `reconcilia_automaticos`; campana atascada en `ajuste_en_vuelo` y sin boton **Regresar** | Correr `reconcilia_automaticos` (solo lectura + sello/cierre, cero PUTs) tambien en `modo.modo != 'live'` y cuando `ciclo_id is None`, en su bloque aislado y cerrando `aplicador_lectura` | **P1-2** | `test_cycle_ubicacion.py::test_e2e_p1_2_reconcilia_ubicaciones_en_shadow_y_off` + `test_cycle_ubicacion.py::test_e2e_readback_500_se_reconcilia_mismo_dia` |
| 5d | Reconciliacion (payload / estado) | `/sp/campaigns/list` devuelve lista vacia o payload sin `estrategia_puja` | `app/apply_ubicacion.py:222-223`: `continue` (deja pendiente) | No sella ni cierra en falso; espera siguiente horario | OK | `test_apply_ubicacion.py::test_reconcilia_cierra_la_intencion_que_amazon_no_refleja` |
| 5e | Reconciliacion (claim perdido / BD) | `ApplyAbortado` en `guard` o excepcion de BD durante `reconcilia_automaticos` | `ApplyAbortado` no esta en `except AdsClientError`; pero faltaba `tick()` tras `_config_leida` y antes del sello | Propagar `ApplyAbortado` -> `CicloOcupado` y errores de BD sin tragarselos; revalidar `tick()` antes del `tx_durable` que sella `confirmado_el` | Endurecimiento dentro de P1-2 | `test_cycle_ubicacion.py::test_e2e_claim_perdido_en_reconcilia_ubicacion_aborta_sin_sellar` |
| 6a | Lectura previa en consola (`_motivo_consola`) | `401`, `403` o `AdsAuthError` en `/sp/campaigns/list` antes del PUT | `app/apply_ubicacion.py:428-432`: escribe `ubicacion_sin_aplicar(eval_id, 'error_put')` **antes** de hacer `raise`, quemando irreversiblemente la evaluacion #1 del dia | Comprobar `if es_identidad_rechazada(exc): raise` **antes** de `if not vigilado.put_iniciado: descarta(...)`; evaluaciones #1..N quedan pendientes para el ejecutor de recuperacion | **P1-5** | `test_apply_ubicacion.py::test_rechazo_de_identidad_aborta_la_fase_sin_descartar_evaluaciones` + E2E `test_cycle_ubicacion.py::test_e2e_p1_5_401_403_no_quema_evaluacion_y_recuperacion_la_aplica` |
| 6b | Lectura previa en consola | `500`, `429`, timeout, lista vacia o payload incompleto en `/sp/campaigns/list` | Descarta esa evaluacion con `error_put` sin cobrar cuota ni PUT; continua con las demas | Aislamiento por campana, fail-closed, cero cuota, cero PUT | OK | `test_apply_ubicacion.py::test_lectura_previa_fallida_descarta_sin_cobrar_cuota_ni_escribir_intencion` + `test_lectura_viva_incompleta_descarta_sin_cuota_ni_put` |
| 6c | Lectura previa en consola | Valor de ajuste o `estrategia_puja` en Amazon difiere de `vigente` (cambio en consola) | Descarta con `cambio_externo` sin cobrar cuota ni PUT | Abstener sin pisar la mano del dueno, cero cuota, cero PUT | OK | `test_apply_ubicacion.py` + E2E |
| 6d | Lectura previa en consola | Excepcion de BD en `abrir()` o perdida de claim (`ApplyAbortado`) | Propaga sin escribir `ubicacion_sin_aplicar` ni cobrar cuota | Abortar fase (`fallo = True`), reintentable en recuperacion | OK | `test_apply_ubicacion.py::test_fallo_de_base_aborta_la_fase_y_no_marca_no_aplicado` + E2E |
| 7a | Cobro de cuota | `ads_apply_cap_<plat>_ubicacion` ausente o en `0` | `consume_quota` devuelve `False` -> descarta con `cuota_agotada`, 0 PUTs | Fail-closed (spec §5 y §7.4) | OK | `test_apply_ubicacion.py` + E2E `test_cycle_ubicacion.py::test_e2e_cuota_ausente_fail_closed` |
| 7b | Cobro de cuota | Valor de cap invalido en `config_version` (ej. `"abc"`, `-1`) | `cap_desde_settings` lanza `ValueError` -> `_fase_apply` marca `fallo = True`, 0 PUTs | Fail-closed ruidoso, cero PUTs | OK | Validacion en frontera de `app/config_write.py` y lector unico `cap_desde_settings` probados en `tests/test_arreglos05_i_cuotas.py::test_arreglos05_i_proxima_config_rechaza_cuota_que_no_puede_guardar`, `tests/test_settings_escritura.py` y `tests/test_cycle_ubicacion.py::test_config_write_valida_settings_y_cap_ubicacion` |
| 7c | Cobro de cuota | Excepcion de BD al cobrar cuota | Propaga sin descartar ni escribir `campana_ajuste` | Abortar fase, reintentable | OK | Cubierto en `aplica_automaticos` |
| 7d | Cobro de cuota | Tope de campanas en espera 7d alcanzado (`>= max_en_espera`) | Descarta con `tope_campanas_en_espera` antes de abrir conexion ni cobrar cuota | Cero cuota, cero PUT | OK | `test_apply_ubicacion.py` + E2E |
| 8a | PUT (`_fase_apply` -> `aplica_automaticos` -> `_aplica_y_confirma`) | Cliente HTTP de ubicaciones en `_fase_apply` sin `guard_http`, ignorando `aplicador_factory` y repitiendo `/v2/profiles` | `app/cycle_apply.py:498`: usa `apply._cliente_ajuste(platform, transport=None)` -> **no revalida el claim antes del PUT ni en reintentos 429**; repite `/v2/profiles`; no cierra `httpx.Client` | Reutilizar `aplicador._cliente()` (con `_TransportGuardado(base, guard)` y cierre en `finally`), igual que bids/cortes/reactivaciones | **P1-6 (nuevo)** | `test_cycle_ubicacion.py::test_e2e_p1_6_claim_perdido_antes_de_put_bloquea_envio_ubicacion` |
| 8b | PUT | `500`, `429` agotado, timeout, `400`, `207` con error durante `PUT /sp/campaigns` | `vigilado.put_iniciado = True` -> no descarta; deja fila pendiente en `campana_ajuste` (`confirmado_el IS NULL`) | Decidir por observacion en `reconcilia_automaticos` (nunca reenviar a ciegas) | OK | `test_apply_ubicacion.py::test_put_fallido_deja_la_intencion_pendiente_sin_importar_el_error` + E2E |
| 8c | PUT | `401`/`403` durante `PUT /sp/campaigns` | `vigilado.put_iniciado = True`, `es_identidad_rechazada = True` -> deja fila #1 pendiente en `campana_ajuste` y hace `raise` (aborta #2..N sin descartarlas) | Reconciliacion resuelve #1; ejecutor de recuperacion retoma #2..N | OK | `test_apply_ubicacion.py::test_rechazo_de_identidad_aborta_la_fase_sin_descartar_evaluaciones` + E2E |
| 8d | PUT | `401`/`403` en la segunda lectura `_config_leida` dentro de `_aplica_y_confirma` (post-`INSERT campana_ajuste`, pre-PUT) | `campana_ajuste` ya commiteo; con P1-5 hace `raise` sin escribir `ubicacion_sin_aplicar`; `evaluaciones_a_aplicar` no duplica (existe `campana_ajuste`), y `reconcilia_automaticos` la cierra con `error_put` al leer valor previo en Amazon | Converge sin doble PUT ni doble cuota y libera `ajuste_en_vuelo` | OK (traza documentada) | `test_apply_ubicacion.py::test_401_en_segunda_lectura_converge_por_reconciliacion` |
| 8e | PUT | Pujas del origen quedaron incompletas (`res_bids.inciertos > 0` o `bids_recuperables`) | `pujas_completas = False` -> descarta con `pujas_pendientes`, 0 cuota, 0 PUTs | No aplicar ubicaciones si pujas del ciclo no cerraron (spec §7.4) | OK | `test_cycle_ubicacion.py::test_e2e_pujas_inciertas_apartan_ubicaciones` |
| 9a | Readback post-PUT | `500`, `429`, timeout, lista vacia o valor distinto en readback | `vigilado.put_iniciado = True` -> no escribe `ubicacion_sin_aplicar`, deja `campana_ajuste` pendiente | `reconcilia_automaticos` confirma o cierra en siguiente horario (incluido el mismo dia tras P1-2) | OK | `test_cycle_ubicacion.py::test_e2e_readback_500_se_reconcilia_mismo_dia` |
| 9b | Readback post-PUT | `401`/`403` en readback | Deja #1 pendiente en `campana_ajuste` y hace `raise` abortando #2..N | #1 se reconcilia; #2..N quedan para recuperacion | OK | Cubierto en `apply_ubicacion.py` + E2E |
| 9c | Readback post-PUT | Excepcion de BD al ejecutar `_sella` (post-PUT exitoso) | `conn.rollback()` en `_aplica_y_confirma`, propaga error -> `fallo = True`, fila #1 sigue en `campana_ajuste` sin `confirmado_el` | `reconcilia_automaticos` la sella al volver BD | OK | `test_apply_ubicacion.py` + E2E |
| 10a | Cierre de dia | `aplica_automaticos` aborta con `401`/`403` o error BD -> `fallo = True` | `cycle.py:833` no marca `apply.completo = True` -> siguiente horario del mismo dia abre ejecutor (`resume_of = origen`) y retoma desde eval #1 (tras P1-5) | Recuperacion dentro del mismo dia UTC sin re-decidir ni duplicar | OK (depende de P1-5) | `test_cycle_ubicacion.py::test_e2e_p1_5_401_403_no_quema_evaluacion_y_recuperacion_la_aplica` |
| 10b | Cierre de dia | PUT/readback da 500 -> `aplica_automaticos` absorbe, `fallo = False`, `apply.completo = True` | Siguiente horario del mismo dia tiene `ciclo_id = None` -> entraba a `modo_rec = shadow` sin reconciliar ubicaciones | Reconciliar `hay_pendientes_automaticos` tambien con `ciclo_id = None` (P1-2) | **P1-2** | `test_cycle_ubicacion.py::test_e2e_readback_500_se_reconcilia_mismo_dia` |
| 10c | Cierre de dia | Cambio de dia UTC (`D -> D+1`) con evaluacion `live` no aplicada en `D` | En `D+1`, `_plan_de_hoy` abre origen nuevo `C2`; `evaluaciones_a_aplicar(C2)` solo mira `cycle_id = C2`; pendientes de `campana_ajuste` de `D` se reconcilian antes de TX2 de `C2` | No aplica evaluaciones vencidas de ayer; reconcilia antes de decidir hoy | OK | `test_cycle_ubicacion.py::test_e2e_cambio_de_dia_no_aplica_evaluacion_de_ayer` |
| 11a | Cambio de modo | `ads_optimizer_mode`: `live -> shadow` o `live -> off` con `campana_ajuste` pendiente | `_fase_reconciliacion` no corria `reconcilia_automaticos` fuera de `live` | Reconciliar pendientes en `shadow` y `off` (solo lectura + sello/cierre, cero PUTs) | **P1-2** | `test_cycle_ubicacion.py::test_e2e_p1_2_reconcilia_ubicaciones_en_shadow_y_off` |
| 11b | Cambio de modo | `ads_ubicacion_<plat>` o `goal`: `live -> shadow`/`off` entre TX3 y TX4 | `_sigue_vivo` relee `settings_vigentes` y goals frescos -> descarta con `modo_no_live`, 0 cuota, 0 PUTs | Abstener sin aplicar ni gastar cuota | OK | `test_cycle_ubicacion.py::test_e2e_cambio_de_modo_entre_tx3_y_tx4_y_vuelta_a_live` |
| 11c | Cambio de modo | Vuelta `shadow`/`off -> live` con evaluaciones `shadow` previas en `ubicacion_evaluacion` | `evaluaciones_a_aplicar` filtra `ue.modo = 'live'` y el trigger `0104` rechaza `shadow` | Jamas aplicar evaluaciones `shadow`; el ciclo `live` siguiente decide fresco y respeta 14d/7d | OK | `test_cycle_ubicacion.py::test_e2e_cambio_de_modo_entre_tx3_y_tx4_y_vuelta_a_live` |
| 12a | Reinicio a mitad de fase | Caida antes de COMMIT de TX3 | TX3 entera revierte; `decision_committed_at` sigue `NULL`; siguiente horario reclama lock vencido y corre origen completo | Sin duplicados ni evaluaciones parciales | OK | `test_cycle_ubicacion.py::test_e2e_reinicio_a_mitad_de_apply_retoma_sin_duplicar` |
| 12b | Reinicio a mitad de fase | Caida en TX4 entre eval #1 (confirmada) y eval #2 (sin intentar) | `apply.completo` sigue ausente -> ejecutor de recuperacion (`resume_of = origen`) salta eval #1 (`EXISTS campana_ajuste`) y aplica solo eval #2 | Convergencia exacta, 1 PUT por campana | OK | `test_cycle_ubicacion.py::test_e2e_reinicio_a_mitad_de_apply_retoma_sin_duplicar` |
| 12c | Reinicio a mitad de fase | Caida entre `INSERT campana_ajuste` (pre-HTTP) y PUT, o entre PUT y `_sella` | Fila pendiente en `campana_ajuste`; ejecutor del siguiente horario reconcilia antes de TX4 (sella si llego, cierra `error_put` si no) y continua con las demas evaluaciones | Convergencia idempotente por observacion en Amazon | OK | `test_cycle_ubicacion.py::test_e2e_reinicio_post_insert_pre_sello_converge_en_reconciliacion` |

---

## 2. Raiz de cada correccion y diseno elegido (`architect` + `blast-radius`)

### P1-1: Frescura de placements a nivel plataforma (`app/ubicacion_io.py`)
- **Raiz:** `lee_casos` media `fresco` sobre `max(info["max_fecha"]) >= hasta` de las filas de **cada campana** (`app/ubicacion_io.py:386-392`), contradiciendo el propio modelo de datos de Amazon (el reporte solo emite filas los dias con actividad) y contradiciendo `cubre_inmaduros` 40 lineas arriba (`ubicacion_io.py:343-349`), que si mide cobertura a nivel **plataforma**.
- **Alternativas evaluadas:**
  - *Opcion A (elegida):* consultar una sola vez por plataforma `_dias_con_placements(conn, platform, hasta_plat, hoy - 1d)` (3 fechas: `D-3`, `D-2`, `D-1`). De ahi salen ambos predicados de plataforma: `placements_frescos = (hasta_plat in dias_recientes)` y `cubre_inmaduros = _cubre_ingesta(dias_recientes, hoy - 2d, hoy - 1d)`. Se elimina la lectura de `max(metric_date)` por campana y se deja que `gasto_placements_total` vs `gasto_campana` detecte huecos reales por campana (y `gasto_placements_total is None` sigue absteniendo campanas sin ninguna fila de placements en la ventana).
  - *Opcion B (descartada):* `SELECT max(metric_date) >= hasta_plat FROM ads_placement_observation WHERE platform = %s`. Descartada porque si `D-3` faltara por fallo de ingesta pero `D-2` hubiera llegado, `max(metric_date) >= D-3` daria un falso positivo ocultando un hueco en `D-3`.
- **Radio de explosion comprobado:**
  - `tests/test_ubicacion_io.py:301-318`: siembra solo `atrasado = D-6` (ninguna fila en `D-3` en la plataforma) -> sigue devolviendo `placements_frescos is False`.
  - Campana sin filas en ventana (`placements == {}`): `total = None` -> `decide_ubicacion` sigue devolviendo `datos_placements_incompletos` en `app/optimizer/ubicacion.py:325`.
  - `app/optimizer/ubicacion.py` no cambia: `CasoCampana.placements_frescos` conserva su tipo `bool` y el replay determinista queda 100% compatible con evaluaciones historicas.
  - **Riesgo residual declarado:** si `D-3` llega parcial a la plataforma y una campana pierde sus filas de `D-3` con gasto por debajo de la tolerancia de conciliacion (`max(0.05, 1% * gasto_campana)`), decide sobre una ventana un dia mas corta. Impacto bajo, anotado en el PR.

### P1-2 y P1-6: Reconciliacion aislada, fuera de `live`, y cliente unico con `guard_http` (`app/cycle_apply.py`, `app/apply_ubicacion.py`, `app/apply.py`, `app/cycle.py`)
- **Raiz compartida:** En `app/cycle_apply.py` tanto `_fase_reconciliacion` (linea 298) como `_fase_apply` (linea 498) eludian el `Aplicador` del ciclo llamando directo a `apply._cliente_ajuste(platform, transport=None)`, perdiendo `aplicador_factory`, `transport`, `guard_http` (`_TransportGuardado`) y cierre de socket; ademas `reconcilia_automaticos` corria dentro del `try/except` global de `_fase_reconciliacion` (solo en `modo == "live"`), de modo que un fallo HTTP transitorio de ubicaciones hacia `conn.rollback()` y anulaba `aplicador`, cancelando pujas, `libera_vencidos` y reactivaciones.
- **Alternativas evaluadas:**
  - *Opcion A (elegida):*
    1. En `app/apply.py`: `_TransportGuardado` acepta `cerrar_base: bool = False` y delega `close()` a `self._base.close()` cuando `Aplicador._cliente()` creo el `httpx.HTTPTransport()` interno (sin cerrar transportes inyectados por callers/tests). Agregar `Aplicador.close()` para cerrar `self._write_client` si fue instanciado y dejarlo en `None`.
    2. En `app/cycle_apply.py`: extraer `cierra_aplicador(reconciliacion)` y `_reconcilia_ubicaciones_aislado(conn, aplicador, platform, guard, notas) -> bool`:
       - Corre solo si `hay_pendientes_automaticos(conn, platform)`.
       - En la rama `modo.modo == "live"`, corre **despues** de `conn.commit()` de `reconcilia_harvest` / `reconcilia_bids` / `reconcilia_reversas_pause`, dentro de su propio `try/except (AdsClientError, SinPerfilAplicar, apply.SinPerfilAjuste, AdsConfigError)`:
         - Si `es_identidad_rechazada(exc)` (`401`/`403`/`AdsAuthError`): fallo global de identidad/permisos -> hace `conn.rollback()` (sin perder harvest/bids/reversas ya commiteados), anota `apply_error`, cierra `aplicador` y devuelve `aplicador = None` para que no salga ninguna mutacion.
         - Para cualquier otro error transitorio (`5xx`/`429`/timeout/lectura ilegible/`SinPerfil*`): hace `conn.rollback()`, anota `notas["ubicaciones_reconcilia_error"]` y **conserva `aplicador` intacto**. `ApplyAbortado`, `DuenoDelClaimCambio` y errores de BD siguen propagando.
       - En la rama `modo.modo != "live"` (cubre `shadow`, `off` y `ciclo_id is None` tras cierre del dia), si `tiene_reversas or tiene_ubicaciones`, construye `aplicador_lectura` UNA sola vez con `fabrica = aplicador_factory or _aplicador_real`, reconcilia reversas si hay (commiteando) y llama a `_reconcilia_ubicaciones_aislado` usando `aplicador_lectura._cliente()`, cerrando `aplicador_lectura.close()` en `finally`. Si `fabrica` falla y solo habia ubicaciones (sin reversas) y no es rechazo de identidad, anota `ubicaciones_reconcilia_error`; si habia reversas o es auth, anota `apply_error`.
       - En `_fase_apply` (linea 498), pasa `cliente=aplicador._cliente()` a `aplica_automaticos` (en vez de `apply._cliente_ajuste`) y cierra `aplicador.close()` en `finally` de `_fase_apply`.
    3. En `app/cycle.py`: en `_atiende`, inicializar `reconciliacion = None` y llamar `cierra_aplicador(reconciliacion)` en `finally:` para cubrir tambien caidas en TX2/TX3 antes de llegar a `_fase_apply` (+2 lineas, queda en 1,143 <= 1,148).
    4. En `app/apply_ubicacion.py`: en `reconcilia_automaticos`, usar `tx_durable(conn)` al confirmar (coherente con `registra_sin_aplicar` y seguro con `autocommit=True/False`) y llamar `if tick is not None: tick()` tambien tras `_config_leida` y antes del sello `tx_durable`, evitando sellar si el claim se perdio durante el HTTP.
  - *Opcion B (descartada):* inyectar `guard_http` y `transport` en `apply._cliente_ajuste` y mantener dos clientes HTTP en paralelo. Descartada por duplicar `GET /v2/profiles`, duplicar token LWA y violar la Regla 1 (un solo dueno del cliente HTTP del ciclo: `Aplicador`).
- **Radio de explosion comprobado:**
  - `AdsWriteClient` (`app/ads/write.py:674,716,737`) ya implementa `ajustar_campana`, `list_sellado` y `close`, y `Aplicador._cliente()` envuelve su transporte en `_TransportGuardado(base, self._guard_http, cerrar_base=...)`.
  - `app/apply_ubicacion.py` sigue sin importar `app.ads.write` (`test_architecture.py` intacto).
  - Ningun test existente parcheaba `apply._cliente_ajuste` para el camino automatico (verificado por grep: solo `test_apply_ubicacion.py:784,838,867`, `test_api_write.py` y `test_jev_bloqueo_api.py` para el camino manual).
  - `app/cycle.py` queda en 1,143 <= 1,148 lineas (sin subir techos de `tests/test_architecture.py`).

### P1-3: `fase_temporada` desde `target_ciclo.overlay` independiente de `ads_bid_politica` (`app/cycle_recorrido.py`)
- **Raiz:** `app/cycle_recorrido.py:645` leía `plat_t.estacion.fase`, pero `plat_t` solo se construye dentro de `if politica is not None:` (`cycle_recorrido.py:533`). Apagar `ads_bid_politica_<platform>` anulaba el freno de temporada baja de AUTO-04, aunque `target_ciclo.overlay` ya estaba resuelto al inicio de `_recorre_plataforma` (`cycle_recorrido.py:466`).
- **Diseno elegido:**
  ```python
  overlay_t = target_ciclo.overlay
  fase_temporada = (
      overlay_t.estacion.fase
      if overlay_t is not None and overlay_t.modo == "live" and overlay_t.estacion is not None
      else None
  )
  ```
  - Desacopla completamente AUTO-04 de `ads_bid_politica_<platform>` (P1-3).
  - Aplica el freno solo cuando `ads_temporada_<platform> == "live"` (cerrando de paso P2-6 con la misma regla `en_vivo` que ya rige bids y cortes).
- **Radio de explosion comprobado:** `target_ciclo.overlay` siempre es calculado por `_resuelve_target_ciclo` (linea 466) independientemente de `politica`. En `shadow` u `off` de temporada, `fase_temporada` es `None` y no altera subidas.

### P1-4: Hojas `producto_sin_evidencia` en TX2 abstienen la campana en TX3 (`app/cycle_sombra.py`, `app/cycle_recorrido.py`)
- **Raiz:** Cuando falla `productos_io.lee_entradas_productos` en TX2 (`productos_ciclo.lectura_fallida = True`), `_procesa_decisora` (`app/cycle_recorrido.py:270-273`) descarta la hoja con `MOTIVO_PRODUCTO_SIN_EVIDENCIA` antes de registrar `hojas_aptas` y `contadores.targets`. En campanas mixtas (otra hoja si entro a `contadores.targets`), `lee_casos` armaba `MetaCampana` incompleta y `tx3_ubicaciones` no detectaba la hoja perdida.
- **Diseno elegido:**
  1. Agregar `hojas_sin_evidencia: set[int] = field(default_factory=set)` en `_ProductosCiclo` (`app/cycle_sombra.py:40`).
  2. En `_procesa_decisora` (`app/cycle_recorrido.py:270-273`), al entrar a `if meta_viva == MOTIVO_PRODUCTO_SIN_EVIDENCIA:`, registrar `productos_ciclo.hojas_sin_evidencia.add(entidad_id)` antes del `return`.
  3. En `tx3_ubicaciones` (`app/cycle_sombra.py:507-515`), calcular:
     ```python
     perdidas = set(getattr(contadores.productos, "hojas_sin_evidencia", ())) | {
         hoja
         for hoja, pid in contadores.productos.hojas_aptas.items()
         if pid not in ids_evaluacion
     }
     ```
     y pasar `perdidas` a `campanas_de_hojas` -> `marca_producto_sin_evidencia`.
- **Radio de explosion comprobado:**
  - `tools/auto04_verifica.py:81-82` ya clasifica `("mantener", "producto_sin_evidencia")` como `sustituidas` en el replay determinista.
  - El uso de `getattr(..., ())` mantiene compatibilidad con stubs `SimpleNamespace(hojas_aptas=...)` en pruebas existentes.
  - Cubre tanto campanas puras como mixtas, y funciona tambien cuando `ads_bid_politica_<platform>` esta apagada (porque `_meta_viva` corre antes de `if politica is None`).

### P1-5: Rechazo de identidad (401/403/`AdsAuthError`) aborta sin quemar la evaluacion (`app/apply_ubicacion.py`, `tests/test_apply_ubicacion.py`)
- **Raiz:** En `app/apply_ubicacion.py:428-432`, `if not vigilado.put_iniciado: descarta(...)` se ejecutaba **antes** de `if _identidad_rechazada(exc): raise`. Al recibir `401`, `403` o `AdsAuthError` en `_motivo_consola`, se escribia una fila inmutable en `ubicacion_sin_aplicar` antes de abortar, haciendo imposible que el ejecutor de recuperacion reintentara la evaluacion #1 (la mas prioritaria del dia).
- **Diseno elegido:** invertir el orden en `app/apply_ubicacion.py:428-432`:
  ```python
  except _ERRORES_DE_ENVIO as exc:
      if _identidad_rechazada(exc):
          raise
      if not vigilado.put_iniciado:
          descarta(eval_id, _motivo_sin_put(exc))
  ```
  - Si el 401/403 ocurre en `_motivo_consola` (pre-cuota, pre-insert, pre-PUT): no escribe `ubicacion_sin_aplicar`, no cobra cuota, no escribe `campana_ajuste`, y aborta (`fallo = True`, `apply.completo = False`) para que la recuperacion retome desde #1.
  - Si el 401/403 ocurre durante el PUT o el readback: `campana_ajuste` ya existia, no escribe `ubicacion_sin_aplicar`, aborta #2..N sin descartarlas; #1 se reconcilia y #2..N se retoman en recuperacion.
  - Corregir `tests/test_apply_ubicacion.py:689-705` que cristalizo el bug, verificando ahora que `ubicacion_sin_aplicar` queda vacia y `evaluaciones_a_aplicar` conserva `[eval_1, eval_2]` ante `401`, `403` y `AdsAuthError`.

---

## 3. Plan de pruebas y evidencia antes/despues (TDD)

### Orden de ejecucion (Fase 1)
1. **Regresiones que fallan primero en `032e6953`:** escribir las pruebas de los 5 P1 + P1-6 y correr `pytest` contra el codigo sin cambios para capturar la salida real de fallo de los 6 escenarios.
2. **Correccion minima por P1:** aplicar cada cambio en `app/ubicacion_io.py`, `app/apply.py`, `app/cycle_apply.py`, `app/apply_ubicacion.py`, `app/cycle_recorrido.py`, `app/cycle_sombra.py` y verificar verde en cada paso.
3. **Suite E2E completa en `tests/test_cycle_ubicacion.py`:** ejecutar el ciclo real (`cycle.corre_ciclo` y `cycle.atender_plataforma` -> `_fase_reconciliacion` -> `_fase_lecturas`/`_recorre_plataforma` -> TX3 -> `_fase_apply`) contra PostgreSQL 16 (`db_desde_template`) con rol `app_decide` real y `httpx.MockTransport` via `aplicador_factory` (parches acotados a `time.sleep`, lectura/guardado de productos y `_compromete_tx3`), alineada con la matriz de la seccion 1:
   - `test_e2e_p1_1_campana_sin_impresiones_en_d3_recorta`
   - `test_e2e_p1_2_error_reconcilia_ubicaciones_no_tira_apply_del_ciclo`
   - `test_e2e_p1_2_401_reconcilia_ubicaciones_aborta_apply_global`
   - `test_e2e_p1_2_reconcilia_ubicaciones_en_shadow_y_off` (parametrizada en `shadow` y `off`)
   - `test_e2e_readback_500_se_reconcilia_mismo_dia`
   - `test_e2e_p1_3_temporada_baja_frena_subida_sin_bid_politica`
   - `test_e2e_temporada_shadow_no_frena_subida_ubicacion`
   - `test_e2e_p1_4_fallo_lectura_productos_tx2_abstiene_campana_mixta`
   - `test_e2e_fallo_guardado_productos_tx3_abstiene_campana`
   - `test_e2e_p1_5_401_403_no_quema_evaluacion_y_recuperacion_la_aplica`
   - `test_e2e_p1_6_claim_perdido_antes_de_put_bloquea_envio_ubicacion`
   - `test_e2e_claim_perdido_en_reconcilia_ubicacion_aborta_sin_sellar`
   - `test_e2e_claim_perdido_en_tx3_no_deja_ubicacion_evaluacion`
   - `test_e2e_pujas_inciertas_apartan_ubicaciones`
   - `test_e2e_cuota_ausente_fail_closed`
   - `test_e2e_cambio_de_modo_entre_tx3_y_tx4_y_vuelta_a_live`
   - `test_e2e_cambio_de_dia_no_aplica_evaluacion_de_ayer`
   - `test_e2e_reinicio_a_mitad_de_apply_retoma_sin_duplicar`
   - `test_e2e_reinicio_post_insert_pre_sello_converge_en_reconciliacion`
   - `test_e2e_ubicacion_off_no_escribe_ni_toca_http`
   - `test_e2e_sin_meta_campana_abstiene`
   - *Filas reforzadas en cierre P2 (`#788`):* `1f` (error/caida de Postgres en TX2 cubierto en `tests/test_cycle_ubicacion.py::test_e2e_error_bd_tx2_aborta_sin_tx3_y_libera_lock`; en `tests/test_cycle.py` solo existia `test_sello_fail_closed_decide_explota` para error de logica) y `7b` (caps invalidos en frontera cubiertos en `tests/test_arreglos05_i_cuotas.py::test_arreglos05_i_proxima_config_rechaza_cuota_que_no_puede_guardar`, `tests/test_settings_escritura.py` y `tests/test_cycle_ubicacion.py::test_config_write_valida_settings_y_cap_ubicacion`).
   - *Unitarias nuevas complementarias:* `test_ubicacion_io.py::test_frescura_placements_es_por_plataforma_y_conciliacion_caza_huecos` y `test_apply_ubicacion.py::{test_rechazo_de_identidad_aborta_la_fase_sin_descartar_evaluaciones, test_401_en_segunda_lectura_converge_por_reconciliacion, test_transport_guardado_y_aplicador_close_liberan_pool}`.
4. **Verificacion de regresion y candados:**
   - `uv run --frozen python -m pytest tests/test_ubicacion_io.py tests/test_apply_ubicacion.py tests/test_cycle_ubicacion.py tests/test_optimizer_ubicacion.py tests/test_migracion_0104.py tests/test_ubicacion_ui.py tests/test_aplica_ajuste.py tests/test_cycle.py tests/test_cycle_apply.py tests/test_arreglos05_a_ciclo.py tests/test_architecture.py -q`
   - `ruff check app tests tools && ruff format --check app tests tools`
   - `pre-commit run --all-files`

---

## 4. P2 fuera de alcance (notas / issue #788)

Siguen fuera del alcance de este PR (no bloquean `shadow` ni rompen dinero/datos ante fallo simple comun; registrados en el issue `#788`):
- `P2-1`: restriccion de `OLD.evaluacion_id IS NOT NULL` para `app_decide` en `campana_ajuste_0067_solo_confirmado` (migracion futura).
- `P2-2`: camino manual `_exige_previa` cuando `_config_leida` devuelve `None`.
- `P2-3`: filtrar `ubicacion_sin_aplicar` en `ultimas_evaluaciones_por_campana` (UI).
- `P2-5`: emitir `CapSaturado` hacia Telegram desde `aplica_automaticos` (`#788`).
- `P2-7`: mostrar fecha/`cycle_id` en `resumen_ultimo_ciclo` de Salud.
- `P2-8`: exit code de `tools/auto04_verifica.py` con 0 comparaciones, `_ya_en_destino` solo al recuperar, y `notas.ubicaciones.aplicadas` de TX3 (`#788`).
- P2 de auditoria PR #789 (Opus rondas 2-3, anotados en `#788`):
  - `applied_count` no refleja confirmaciones de reconciliacion previas si la reconciliacion de ubicaciones falla con 401/403 (`aplicador=None`).
  - Actualizar docstring de `_fase_reconciliacion` (los errores transitorios de ubicaciones ya no anulan `aplicador`).
  - Retirar el alias sin uso `_identidad_rechazada = es_identidad_rechazada` en `app/apply_ubicacion.py`.
  - Mover o proteger `cierra_aplicador(reconciliacion)` en `finally` de `_atiende` respecto a `_libera_lock`.
  - En rama no-live, si fallan las reversas de pause se regresa antes de reconciliar ubicaciones pendientes (se resuelven en el siguiente horario).
  - Ventana estrecha `8d` (401 en la segunda lectura tras insertar `campana_ajuste`): la reconciliacion cierra con `error_put` y la evaluacion se pierde ese dia.
  - Correr la suite completa contra la linea base de fallas conocidas antes de pasar a `live`.
  - Informativos de Devin (no compuerta): validar `isinstance(items, list)` ante `campaigns: null` en `app/apply_ajustes.py:_config_leida` y usar contrasena efimera en `_como_decide` de `tests/test_cycle_ubicacion.py`.
- *Nota:* `P2-4` (`guard_http` y cierre HTTP en ubicaciones) y `P2-6` (temporada `shadow` no frena subidas) quedan cerrados de raiz al resolver `P1-2`/`P1-6` y `P1-3`.
