# Modelo explícito de estados de un cambio enviado a Amazon (ARREGLOS 05 A)

Dueño único del modelo: [`app/apply_estados.py`](../../../../app/apply_estados.py).
Prueba exhaustiva de la tabla y chequeos `/correct`: [`tests/test_arreglos05_a_estados.py`](../../../../tests/test_arreglos05_a_estados.py).
Regresiones de extremo a extremo: [`tests/test_arreglos05_a_ciclo.py`](../../../../tests/test_arreglos05_a_ciclo.py).

## Premisa atacada

Seis pasadas de revisión de Astra y siete de Devin convergieron sobre la misma familia de fallos: decidir qué hacer ante un resultado de Amazon (4xx determinista, 5xx o red, 401/403, lectura sin estado, pausa manual posterior, tope de intentos, relevo de claim o concurrencia) de forma local donde aparecía cada bug, mediante un contador, un flag o una consulta `HAVING` nueva. Cada parche puntual dejaba abierto el caso vecino.

Se atacó la premisa (`Attack the Premise` + `Model the Domain` + `Migrate Callers Then Delete Legacy APIs`):

1. Un solo módulo puro (`app/apply_estados.py`) define los estados, los eventos, las acciones y la tabla completa `TABLA_TRANSICIONES` como datos, junto con `decidir(estado, evento)` y `estado_inicial_desde_hechos(...)`.
2. Todos los ejecutores y reconciliadores del bloque A migraron en una sola ola a consultar la tabla (`apply.py`, `apply_reconcilia.py`, `apply_cola.py`, `apply_cola_ejecuta.py`, `apply_reversa.py`, `apply_producto.py`, `retiro_anuncios.py`, `apply_harvest.py`, `apply_harvest_hermanas.py`, `apply_harvest_reversa.py`, `apply_harvest_reconciliacion.py`, `apply_store.py`, `temporada_io.py`).
3. Lo que la tabla sustituye se borró sin dejar API paralela: `_SQL_REVERSA_TERMINAL` (`HAVING` ad hoc), `RESULTADO_PAUSADA_TRAS_HABILITAR`, y las definiciones duplicadas de `STATUS_IDENTIDAD_RECHAZADA`, `TOPE_INTENTOS` y `es_rechazo_de_identidad` en `apply_store.py`.

## Tipos del dominio

### Estados (`EstadoCambio`, 9)

| Estado | Terminal | Significado |
|---|---|---|
| `pendiente` | No | Cambio listo para evaluar elegibilidad y enviar (sin intento abierto ni terminal y bajo tope). |
| `en_vuelo` | No | Otra ruta (p. ej. ciclo vs reversa manual) mantiene la exclusión activa (`candado_reversa_pause`). |
| `incierto` | No | Existe un intento abierto sin sello (`finished_at IS NULL`); Amazon pudo haber aplicado el cambio. |
| `aplicado` | Sí | Confirmado en Amazon (`resultado = 'ok'`). |
| `rechazado` | Sí | Rechazo determinista de Amazon (`4xx` con cuerpo); Amazon no aplicó el cambio. |
| `pausado_por_dueno` | Sí | Tras un intento incierto, la caché registró `ENABLED` posterior y Amazon ahora muestra `PAUSED` (`omitida:pausada_tras_habilitar`). |
| `archivado` | Sí | Entidad `ARCHIVED` (u otro estado terminal) en Amazon. |
| `sin_identidad` | Sí | Entidad sin identidad válida en `ad_entity`. |
| `tope_intentos` | Sí | Alcanzó `TOPE_INTENTOS_CAMBIO` (3, misma fuente que `apply.TOPE_INTENTOS`) sin intento abierto. |

Prioridad fija de derivación en `estado_inicial_desde_hechos(...)`:
`en_vuelo_ajeno` (`EN_VUELO`) > `tiene_intento_abierto` (`INCIERTO`) > `resultado_terminal` (`APLICADO` / `PAUSADO_POR_DUENO` / `ARCHIVADO`) > `intentos_totales >= tope_intentos` (`TOPE_INTENTOS`) > `PENDIENTE`.

### Eventos (`EventoCambio`, 12)

`nuevo_horario`, `ok_confirmado`, `no_aplicado_en_amazon`, `error_4xx_determinista`, `error_5xx_o_red`, `rechazo_identidad`, `pausa_manual_dueno`, `entidad_archivada`, `entidad_sin_identidad`, `dueno_desplazado`, `intentos_agotados`, `operacion_en_curso`.

### Acciones (`AccionCambio`, 10)

`enviar`, `leer_resultado`, `sellar_aplicado`, `sellar_rechazado`, `contar_intento`, `mantener_incierto`, `sellar_pausado_dueno`, `sellar_terminal`, `no_tocar`, `abortar_fase`.

## Tabla completa `estado x evento` (108 casillas)

Cada casilla devuelve `Transicion(accion, estado_siguiente, origen_incompleto, sella_intento, propaga_aborto)`.
Banderas resumidas entre paréntesis: `I` = `origen_incompleto`, `S` = `sella_intento`, `A` = `propaga_aborto`.

| Evento | `pendiente` | `incierto` | `en_vuelo` | Terminales (`aplicado`, `rechazado`, `pausado_por_dueno`, `archivado`, `sin_identidad`, `tope_intentos`) |
|---|---|---|---|---|
| `nuevo_horario` | `enviar` -> `incierto` | `leer_resultado` -> `incierto` (`I`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> mismo estado |
| `ok_confirmado` | `sellar_aplicado` -> `aplicado` (`S`) | `sellar_aplicado` -> `aplicado` (`S`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> mismo estado |
| `no_aplicado_en_amazon` | `no_tocar` -> `pendiente` | `contar_intento` -> `pendiente` (`S`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> mismo estado |
| `error_4xx_determinista` | `sellar_rechazado` -> `rechazado` (`S`) | `sellar_rechazado` -> `rechazado` (`S`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> mismo estado |
| `error_5xx_o_red` | `mantener_incierto` -> `incierto` (`I`) | `mantener_incierto` -> `incierto` (`I`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> mismo estado |
| `rechazo_identidad` | `abortar_fase` -> `rechazado` (`I,S,A`) | `abortar_fase` -> `incierto` (`I,A`) | `abortar_fase` -> `en_vuelo` (`I,A`) | `abortar_fase` -> mismo estado (`I,A`) |
| `pausa_manual_dueno` | `sellar_pausado_dueno` -> `pausado_por_dueno` (`S`) | `sellar_pausado_dueno` -> `pausado_por_dueno` (`S`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> mismo estado |
| `entidad_archivada` | `sellar_terminal` -> `archivado` (`S`) | `sellar_terminal` -> `archivado` (`S`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> mismo estado |
| `entidad_sin_identidad` | `sellar_terminal` -> `sin_identidad` (`S`) | `sellar_terminal` -> `sin_identidad` (`S`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> mismo estado |
| `dueno_desplazado` | `abortar_fase` -> `pendiente` (`I,A`) | `abortar_fase` -> `incierto` (`I,A`) | `abortar_fase` -> `en_vuelo` (`I,A`) | `abortar_fase` -> mismo estado (`I,A`) |
| `intentos_agotados` | `no_tocar` -> `tope_intentos` | `leer_resultado` -> `incierto` (`I`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> mismo estado |
| `operacion_en_curso` | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> `en_vuelo` (`I`) | `no_tocar` -> mismo estado |

## Invariantes demostrados sobre toda la tabla

Comprobados automáticamente en `test_arreglos05_a_invariantes_globales_de_la_tabla`:

1. **Lo incierto se lee antes de reenviar:** ninguna casilla de `INCIERTO` devuelve `ENVIAR`; `(INCIERTO, NUEVO_HORARIO)` ordena `LEER_RESULTADO` y `(INCIERTO, ERROR_5XX_O_RED)` mantiene `INCIERTO` con `origen_incompleto=True` y `sella_intento=False`.
2. **Nunca más de 3 intentos por pausa:** `TOPE_INTENTOS_CAMBIO == 3` (re-exportado como `apply.TOPE_INTENTOS` y consumido en `temporada_io.sql_pausas_de_baja`); ninguna casilla de `TOPE_INTENTOS` emite `ENVIAR`; si el 3.er intento quedó abierto, `(INCIERTO, INTENTOS_AGOTADOS)` primero lee su resultado antes de pasar a `TOPE_INTENTOS`.
3. **Lo pausado por el dueño no se reactiva:** `PAUSADO_POR_DUENO` es absorbente y jamás emite `ENVIAR` ni vuelve a `APLICADO`; `(INCIERTO, PAUSA_MANUAL_DUENO)` sella `RESULTADO_PAUSADA_POR_DUENO` sin atribuir `ok`.
4. **Un rechazo de identidad (401/403) no deja salir otra mutación:** en los 9 estados, `RECHAZO_IDENTIDAD` ordena `ABORTAR_FASE` con `propaga_aborto=True`; en `INCIERTO`, `sella_intento=False`.
5. **Un dueño desplazado jamás sella:** en los 9 estados, `DUENO_DESPLAZADO` ordena `ABORTAR_FASE` con `sella_intento=False` y `propaga_aborto=True`.
6. **Una operación en vuelo ajena jamás se concilia ni reenvía:** en `EN_VUELO`, todo evento ordinario devuelve `NO_TOCAR` con `sella_intento=False` y `origen_incompleto=True`.

## Mapa completo `hallazgo -> casilla` (seis pasadas de Astra y siete de Devin)

Todo hallazgo registrado en [resultado.md](resultado.md) cae en una casilla probada de `TABLA_TRANSICIONES` y conserva su prueba de regresión en `tests/test_arreglos05_a_ciclo.py`.

| Hallazgo | Commit | Casilla(s) `estado x evento -> accion` | Prueba de regresión |
|---|---|---|---|
| Devin 1 | `914424e5` | `(PENDIENTE, NUEVO_HORARIO) -> ENVIAR` (tras revalidar target del `ciclo_origen`) | `test_arreglos05_a_ejecutor_libera_pausa_economica_con_el_target_del_origen` |
| Devin 2 | `06fde246` | `(APLICADO / RECHAZADO, NUEVO_HORARIO) -> NO_TOCAR` | `test_arreglos05_a_ejecutor_no_repite_decisiones_que_ya_tienen_desenlace` |
| Devin 3 | `21a23178` | `(INCIERTO, NUEVO_HORARIO) -> LEER_RESULTADO (I)` y `(PENDIENTE, NUEVO_HORARIO)` con origen incompleto | `test_arreglos05_a_interrumpido_tras_tx3_abre_ejecutor_sin_huerfanas` |
| Devin 4 | `f6333519` | `(INCIERTO, DUENO_DESPLAZADO) -> ABORTAR_FASE (sella=False)` | `test_arreglos05_a_dueno_desplazado_durante_la_lectura_de_vuelta_no_sella` |
| Devin 5 | `3c7693bb` | `(PENDIENTE, NUEVO_HORARIO)` (frontera diaria del plan con reloj de decisión) | `test_arreglos05_a_origen_decidido_con_reloj_de_ayer_no_cuenta_como_el_de_hoy` |
| Devin 6 | `160612ce` | `(PENDIENTE, NUEVO_HORARIO)` (guarda pre-envío no emite `ENVIAR` con evaluación rancia) | `test_arreglos05_a_ejecutor_no_aplica_pausa_con_la_evaluacion_del_origen` |
| Devin 7 | `fd811eee` | `(PENDIENTE, RECHAZO_IDENTIDAD) -> ABORTAR_FASE (S, A, estado=RECHAZADO)` | `test_arreglos05_a_rechazo_de_identidad_detiene_todas_las_mutaciones` |
| Astra 1 | `616c61a0` | `(INCIERTO, DUENO_DESPLAZADO) -> ABORTAR_FASE (sella=False)` (sin fila del claim) | `test_arreglos05_a_dueno_desplazado_durante_la_lectura_de_vuelta_no_sella[sin_fila]` |
| Astra 2 | `a7d7df0e` | `(*, RECHAZO_IDENTIDAD) -> ABORTAR_FASE (A)` | `test_arreglos05_a_rechazo_de_identidad_detiene_todas_las_mutaciones`, `test_arreglos05_a_rechazo_de_identidad_no_se_aisla_por_fila` |
| Astra 3 | `8de0d66f` | `(INCIERTO, NUEVO_HORARIO) -> LEER_RESULTADO`; `(INCIERTO, OK_CONFIRMADO) -> SELLAR_APLICADO` | `test_arreglos05_a_reversa_incierta_se_resuelve_por_lectura_antes_de_reenviar` |
| Astra 4 | `9b178290` | `(PENDIENTE, DUENO_DESPLAZADO) -> ABORTAR_FASE (sella=False)` (cerca en TX3) | `test_arreglos05_a_tx3_no_se_compromete_si_el_claim_cambio_de_dueno` |
| Coord. lock_timeout | `a8e10145` | `(PENDIENTE / INCIERTO, DUENO_DESPLAZADO) -> ABORTAR_FASE (sella=False)` | `test_arreglos05_a_ninguna_espera_del_claim_es_infinita` |
| Astra 5 | `6ee6a5fd` | `(PENDIENTE / INCIERTO, RECHAZO_IDENTIDAD) -> ABORTAR_FASE (A, sella=False en INCIERTO)` | `test_arreglos05_a_rechazo_de_identidad_en_una_lectura_detiene_el_lote` |
| Astra 6 / Devin 10 | `7e3ae4a8` | `(INCIERTO, ERROR_5XX_O_RED) -> MANTENER_INCIERTO (I)`; `(INCIERTO, ENTIDAD_ARCHIVADA) -> SELLAR_TERMINAL` | `test_arreglos05_a_reversa_incierta_se_resuelve_por_lectura_antes_de_reenviar` |
| Devin 8 | `7e3ae4a8` | `(INCIERTO, OK_CONFIRMADO) -> SELLAR_APLICADO` (sella `RESULTADO_OK = "ok"`) | `test_arreglos05_a_reversa_incierta_se_resuelve_por_lectura_antes_de_reenviar` |
| Devin 9 | `213c3ad9` | `(INCIERTO, RECHAZO_IDENTIDAD) -> ABORTAR_FASE (A)` | `test_arreglos05_a_rechazo_de_identidad_en_la_recuperacion_de_cosecha` |
| Devin 11 | `5b42dba8` | `(PENDIENTE, RECHAZO_IDENTIDAD) -> ABORTAR_FASE` (frontera manual traduce a `confirmada=False`) | `test_arreglos05_a_reversa_manual_con_rechazo_de_identidad_no_es_un_500` |
| Devin 12 | `1cce20d4` | `(PENDIENTE, DUENO_DESPLAZADO) -> ABORTAR_FASE (sella=False)` (cerca antes de cerrar 401/403) | `test_arreglos05_a_la_puja_rechazada_no_se_cierra_sin_el_claim` |
| Devin 13 | `a99097d3` | `(INCIERTO, ERROR_5XX_O_RED) -> MANTENER_INCIERTO (I)` | `test_arreglos05_a_reactivacion_pendiente_de_lectura_no_cierra_el_origen` |
| Astra 10 | `9665191e` | `(PENDIENTE, ERROR_4XX_DETERMINISTA) -> SELLAR_RECHAZADO (S, propaga=False)` | `test_arreglos05_a_el_rechazo_de_una_puja_se_cierra_con_su_sello` |
| Astra 8 | `80978ff4`, `a3fc579e` | `(INCIERTO, RECHAZO_IDENTIDAD) -> ABORTAR_FASE (I, A)` | `test_arreglos05_a_la_pausa_de_producto_propaga_el_rechazo_de_identidad`, `test_arreglos05_a_barrido_pausa_de_producto_403_detiene_el_corte_siguiente` |
| Astra 9 | `2c0c33d8`, `a3fc579e` | `(PENDIENTE / INCIERTO, RECHAZO_IDENTIDAD) -> ABORTAR_FASE (A)` | `test_arreglos05_a_la_reversa_de_cosecha_propaga_el_rechazo_de_identidad` |
| Devin 14 | `a48c14cc` | `(INCIERTO, RECHAZO_IDENTIDAD) -> ABORTAR_FASE (A)` (cierra job fallido antes de propagar) | `test_arreglos05_a_reversa_con_rechazo_de_identidad_cierra_el_job_y_no_repite_el_post` |
| Devin 15 | `cda75f72` | `(INCIERTO, ERROR_5XX_O_RED) -> MANTENER_INCIERTO (I, sella=False)` | `test_arreglos05_a_el_barrido_de_reactivaciones_lee_antes_de_reenviar` |
| Astra 11 | `d6c707a9` | `(INCIERTO, RECHAZO_IDENTIDAD) -> ABORTAR_FASE (I, A)` via `transicion_desde_status` / `transicion_desde_error` | `test_arreglos05_a_barrido_pausa_de_producto_recorrido_real_403_detiene_el_corte_siguiente` |
| Astra 12 | `3ef8cf29` | `(INCIERTO, ERROR_5XX_O_RED) -> MANTENER_INCIERTO (I)` | `test_arreglos05_a_unico_resume_con_put_500_abre_ejecutor_y_lee_antes_de_reenviar` |
| Devin 16 | `05ef26d3` | `(PENDIENTE, ERROR_4XX_DETERMINISTA) -> SELLAR_RECHAZADO` (frontera humana `decision_id=None` no propaga) | `test_arreglos05_a_retiro_humano_put_403_cierra_y_vigia_no_reenvia` |
| Astra 13 | `e771227a` | `tiene_intento_abierto -> INCIERTO` antes de filtros; `(INCIERTO, NUEVO_HORARIO) -> LEER_RESULTADO` -> `(INCIERTO, OK_CONFIRMADO) -> SELLAR_APLICADO` | `test_arreglos05_a_reversa_abierta_se_sella_aunque_cache_ya_este_enabled` |
| Astra 14 | `f004c2e5` | `(INCIERTO, DUENO_DESPLAZADO) -> ABORTAR_FASE (sella=False)` | `test_arreglos05_a_reversa_pause_desplazada_durante_el_list_no_sella_ni_toca_cache` |
| Astra 15 | `c8658f38` | `(INCIERTO, NUEVO_HORARIO) -> LEER_RESULTADO` compartido por `reversa_manual` y `aplica_reactivaciones` | `test_arreglos05_a_reversa_manual_resuelve_intento_abierto_sin_reenviar_put` |
| Devin 17 | `4ce2211a` | `(INCIERTO, PAUSA_MANUAL_DUENO) -> SELLAR_PAUSADO_DUENO (PAUSADO_POR_DUENO)` | `test_arreglos05_a_reversa_incierta_no_deshace_pausa_manual_posterior` |
| Devin 18 | `e34817ea` | `(EN_VUELO, NUEVO_HORARIO) -> NO_TOCAR (I, sella=False)` bajo `candado_reversa_pause` | `test_arreglos05_a_reversa_manual_concurrente_no_cierra_put_automatico_en_vuelo` |
| Astra 16 | `35859807` | `(PAUSADO_POR_DUENO, NUEVO_HORARIO) -> NO_TOCAR (PAUSADO_POR_DUENO)` | `test_arreglos05_a_tercer_horario_preserva_pausa_manual_cuando_otra_hoja_sigue_pendiente` |
| Astra 17 | `61d77db4` | `(INCIERTO, INTENTOS_AGOTADOS) -> LEER_RESULTADO`; `(INCIERTO, NO_APLICADO_EN_AMAZON) -> CONTAR_INTENTO`; `(TOPE_INTENTOS, NUEVO_HORARIO) -> NO_TOCAR` | `test_arreglos05_a_reversa_respeta_tope_de_tres_y_cuarto_horario_lee_sin_reenviar` |
| Astra 18 | `de4fce45` | `(ARCHIVADO, NUEVO_HORARIO) -> NO_TOCAR`; `(INCIERTO, NO_APLICADO_EN_AMAZON) -> CONTAR_INTENTO` + reevaluación `(TOPE_INTENTOS, NUEVO_HORARIO) -> NO_TOCAR` en `reversa_manual` (solo `ENVIAR` llama `reversa_pause`; hechos reales obligatorios) | `test_arreglos05_a_reversa_manual_respeta_archivado_sin_otro_put_ni_intento`, `test_arreglos05_a_reversa_manual_respeta_tope_de_tres_en_abierto_y_cerrados` |
| Astra 19 | `de4fce45` | `(APLICADO, NUEVO_HORARIO) -> NO_TOCAR` (omitida, 0 confirmadas) vs `(INCIERTO, OK_CONFIRMADO) -> SELLAR_APLICADO` (1 confirmada atribuida al ejecutor); los llamadores actúan solo sobre `accion`, nunca sobre `estado_siguiente` | `test_arreglos05_a_segundo_ejecutor_solo_cuenta_confirmadas_de_esa_corrida` |
| Astra 20 | `649fbfc9` | `(INCIERTO, INTENTOS_AGOTADOS / NUEVO_HORARIO) -> LEER_RESULTADO` en `_fase_reconciliacion` (`reconcilia_reversas_pause`) al cambiar de día, antes de TX2 y sin depender del `HAVING` de `temporada_io` ni emitir 4.º PUT ni cobrar cuota | `test_arreglos05_a_tercer_put_incierto_al_cambio_de_dia_se_concilia_sin_cuarto_put` |
| Astra 21 | `649fbfc9` | `(INCIERTO, ENTIDAD_ARCHIVADA) -> SELLAR_TERMINAL` y `(ARCHIVADO, NUEVO_HORARIO) -> NO_TOCAR`: un solo camino `_aplica_lectura_reversa` para lectura inmediata y conciliación persiste `fallo:entidad_archived` en el primer LIST | `test_arreglos05_a_reversa_manual_archivado_en_primer_list_un_solo_intento` |
| Astra 22 | `fdea6b3d`, `f32f03ef` | `(INCIERTO, NUEVO_HORARIO) -> LEER_RESULTADO` corre de forma incondicional en `_fase_reconciliacion` (sin `excluir_queue_ids` y con cualquier modo de temporada o global `live`/`shadow`/`off`) y antes del apagador de temporada en `aplica_reactivaciones`; los apagadores solo bloquean `ENVIAR` | `test_arreglos05_a_reversa_abierta_se_concilia_aunque_temporada_pase_a_shadow`, `test_arreglos05_a_reversa_abierta_se_concilia_aunque_global_pase_a_shadow` |

Ningún hallazgo quedó fuera de la tabla.

## Demostración por nivel (`/correct`: arquitectura > tipos > chequeo > prueba > docs)

Para cada nivel se demuestra cómo atrapa o impide una clase real de error ya resuelta en las pasadas de revisión, y por qué la clase se corrigió en el nivel más alto viable:

### 1. Arquitectura (nivel elegido para la clase de transiciones, concurrencia e identidad)

- **Hallazgos reales:** Astra 13 (intento abierto saltado cuando la caché ya decía `ENABLED`), Devin 18 (reversa manual concurrente sellando un PUT en vuelo del ciclo), Astra 17 (tope de 3 reversas superado al recargar `resume`) y Astra 11 (403 en PUT/readback de pausa de producto absorbido en un módulo intermedio).
- **Cómo lo impide este nivel:** `aplica_reactivaciones` y `reversa_manual` ya no evalúan filtros ni consultas `HAVING` antes de saber en qué estado está la decisión. Ambas entran primero al candado de sesión `candado_reversa_pause`, leen los hechos puros (`_hechos_reversa`) y piden el estado único a `estado_inicial_desde_hechos(...)`. Como `en_vuelo_ajeno` tiene prioridad 1 y `tiene_intento_abierto` tiene prioridad 2 (antes de terminales, tope y filtros pre-PUT), es estructuralmente imposible conciliar un envío concurrente, saltar un intento abierto o reenviar sobre el tope. Asimismo, en `retiro_anuncios.py`, `apply.py`, `apply_cola.py`, `apply_producto.py` y `apply_harvest*.py`, toda clasificación de error o status HTTP pasa por `transicion_desde_error` / `transicion_desde_status`.
- **Por qué en arquitectura:** un test puntual solo protege el escenario que escribe; un único dueño de estado y de orden de evaluación elimina la clase entera de desincronización entre ramas.

### 2. Tipos (nivel elegido para eliminar strings mágicos, desenlaces ambiguos y hechos omitidos)

- **Hallazgos reales:** Devin 7 / Astra 3 (puja rechazada sin desenlace o reactivación incierta tratada con strings sueltos), Astra 12 (5xx en reactivación sumado a `fallidas` en vez de dejar `origen_incompleto`), Devin 17 / Astra 16 (pausa manual posterior mezclada con `ok:no_aplicado`) y Astra 18 (`intentos_totales=0` descartando el conteo del ledger en `reversa_manual`).
- **Cómo lo impide este nivel:** `EstadoCambio`, `EventoCambio` y `AccionCambio` son `StrEnum` cerrados y `Transicion` es un `dataclass(frozen=True)` con campos explícitos (`accion`, `estado_siguiente`, `origen_incompleto`, `sella_intento`, `propaga_aborto`). `resuelve_reversa_abierta` devuelve `Transicion | None` (cero strings ad hoc), y `estado_inicial_desde_hechos(...)` exige como argumentos obligatorios sin valor por omisión los cuatro hechos observables (`en_vuelo_ajeno`, `tiene_intento_abierto`, `resultado_terminal`, `intentos_totales`): omitir un hecho lanza `TypeError`.
- **Por qué en tipos:** el vocabulario cerrado y los parámetros obligatorios hacen imposible confundir `PAUSADO_POR_DUENO` con `PENDIENTE` o descartar el conteo de intentos por omisión.

### 3. Chequeo estructural (nivel elegido para evitar regresión de la premisa rota y errores de integración)

- **Hallazgos reales:** Astra 16 y Astra 17 (literales duplicados `'omitida:pausada_tras_habilitar'`, constante `TOPE_INTENTOS` y `STATUS_IDENTIDAD_RECHAZADA` redefinidas en varios archivos, consulta `HAVING` ad hoc en `apply_cola_ejecuta.py`), Astra 18 (pasar `intentos_totales=0` literal a `estado_inicial_desde_hechos`), Astra 19 (ramificar por `transicion.estado_siguiente` en vez de `transicion.accion`), Astra 21 (comparar `estado == ESTADO_WIRE_ENABLED` fuera de `evento_desde_lectura_estado` + `decidir`) y Astra 22 (excluir filas en `_fase_reconciliacion` o retornar temprano en `aplica_reactivaciones` por apagador de temporada antes de leer intentos abiertos).
- **Cómo lo atrapa este nivel:** `_violaciones_chequeo_dueno_unico` en `tests/test_arreglos05_a_estados.py` inspecciona el AST y texto de los 14 módulos del aplicador y falla si aparece fuera de `app/apply_estados.py`: el literal `'omitida:pausada_tras_habilitar'`, `_SQL_REVERSA_TERMINAL`, cualquier `HAVING` en `apply_cola_ejecuta.py`, `excluir_queue_ids` en `apply_cola_ejecuta.py`/`cycle_apply.py`, la redefinición de `STATUS_IDENTIDAD_RECHAZADA` / `RESULTADO_PAUSADA_TRAS_HABILITAR` / `es_rechazo_de_identidad`, cualquier acceso a `.estado_siguiente` en un llamador, pasar literal `intentos_totales=0` a `estado_inicial_desde_hechos`, comparar directamente con `ESTADO_WIRE_ENABLED` en `app/apply_cola_ejecuta.py`, o retornar temprano en `aplica_reactivaciones` por apagador de temporada antes del bucle de lectura. Además `test_arreglos05_a_invariantes_globales_de_la_tabla` afirma que ninguna casilla de `INCIERTO` con `sella_intento=False` puede dejar `origen_incompleto=False`.
- **Por qué en chequeo:** la arquitectura se degrada cuando un llamador mira el estado siguiente en vez de la acción o pasa un conteo falso; el chequeo automatizado bloquea ambas clases en CI.

### 4. Prueba exhaustiva de tabla + regresiones (nivel elegido para demostrar los invariantes)

- **Hallazgos reales:** toda la matriz de 108 casillas, con énfasis en Astra 12, Devin 17, Astra 16, Astra 17 y Devin 18.
- **Cómo lo atrapa este nivel:** `test_arreglos05_a_tabla_estados_completa_y_sin_ambiguedad` exige exactamente `9 x 12 = 108` casillas; `test_arreglos05_a_tabla_casilla_accion_y_estado_siguiente` parametriza las 108 casillas contra expectativas literales independientes de la implementación; `test_arreglos05_a_invariantes_globales_de_la_tabla` verifica los 6 invariantes sobre toda la tabla; y las 51 regresiones de `tests/test_arreglos05_a_ciclo.py` ejercitan el motor real sobre PostgreSQL 16.
- **Por qué en prueba:** si se añade un estado o evento nuevo, las pruebas de completitud e invariantes fallan de inmediato hasta que toda la fila o columna nueva quede definida y respete las reglas del dominio.

### 5. Documentación (nivel de contrato para quien mantenga y opere el sistema)

- **Hallazgos reales:** límites declarados que el código por sí solo no explica al operador ni a un mantenedor nuevo (Devin 16: diferencia entre frontera humana `decision_id=None` y corte automático; Devin 14: negativo del origen sin retirar cuando el DELETE compensatorio recibe 401/403; Devin 11: frontera manual devolviendo `confirmada=False` en vez de 500).
- **Cómo apoya este nivel:** [`docs/APPLY.md`](../../../APPLY.md) §9.2 fija los estados, eventos, acciones, tabla, invariantes y límites del modelo en el contrato permanente del aplicador, y este documento deja trazabilidad completa entre los 39 hallazgos de revisión, su casilla y su prueba.
- **Por qué solo como respaldo:** la documentación no previene bugs por sí sola; se usa únicamente para explicar las fronteras intencionales una vez que arquitectura, tipos, chequeo y pruebas ya impiden los fallos de la clase.
