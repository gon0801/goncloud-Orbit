# Resultado de ARREGLOS 05 A: completar un ciclo sin duplicar sus decisiones

Bloque `A` de `plans/arreglos-05.md`. Base: `master` en
`149bf454f9dbd2bca90c23f84c2ad1ea7537b34c` (merge de #745). Rama `arreglos-05/a`.
Fecha: 2026-11-23 (America/Vancouver).

El SHA del codigo aceptado es el de la cabecera de [acepta.txt](acepta.txt); la
salida del rojo, en [rojo.txt](rojo.txt). Corridas sobre PostgreSQL 16.15
desechable, con la API de Ads simulada (`httpx.MockTransport`) y secretos falsos.

## Para el dueno y para quien mantendra el codigo

- Un fallo de Amazon en una hoja ya no deja sin aplicar el resto del ciclo:
  las otras pujas se intentan, los cortes vencidos se liberan y las
  reactivaciones corren. La puja incierta no se repite: una lectura la
  resuelve en el ciclo siguiente.
- Si el apply no termina (fallo, proceso muerto despues de decidir), el
  horario siguiente completa lo pendiente con un **ejecutor de recuperacion**:
  no vuelve a decidir, no repite envios y no abre otro cupo.
- El ciclo ya no decide a ciegas sobre una hoja cuya puja Amazon ya aplico:
  la conciliacion corre antes de decidir.
- Se retira la regla de 3 horas: un ciclo colgado no bloquea el horario
  siguiente; manda el candado (claim).
- Al cambiar el dia, lo que nunca se intento deja de ser elegible con un
  desenlace auditable; no se borra nada.
- Para mantenimiento: `atender_plataforma` (`app/cycle.py`) es la unica
  operacion de un horario. `_fase_reconciliacion` (nueva) y `_fase_apply`
  viven en `app/cycle_apply.py`. La migracion es la `0094`.

## Fila por fila

| Fila | Comando (salida completa en acepta.txt) | Resultado | Impacto | Reversa |
|---|---|---|---|---|
| A.1 | `uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N a` | 0, 26 passed | Otros objetos y fases progresan ante un fallo individual; ningun envio nuevo ni sello del owner vencido; cero decisiones y HTTP duplicados. | No aplica (pruebas). |
| A.2 | `N a`; `pytest tests/test_cycle.py tests/test_cycle_apply.py tests/test_cli.py tests/test_metricas_listas.py`; Ciclo 4 legado (`-k reconciliacion_corre_despues_de_decidir`) | 0: 26 passed; 0: 150 passed; 0: 1 passed | Una segunda conexion ve la TX3 comprometida incluso con lote valido vacio. La reproduccion que BASE dejo en rojo queda verde. | `0094_reversa_arreglos05_ciclo_origen.sql`. |
| A.3 | `N a`; `T writes test_01` | 0: 26 passed; 0: 4 passed | Cero PUT de una puja incierta, sin un segundo cupo por recuperacion. La reconciliacion de bids sigue siendo solo lectura. | Codigo del bloque anterior. |
| A.4 | `N a` (migracion ida, reversa e ida con el rol real); `gitleaks detect --log-opts=149bf454..HEAD` | 0: 26 passed; 0: no leaks found | El rollback conserva intentos y no vuelve a un binario incompatible. | `MOTOR_SHA=<sha> bash tools/despliegue/rollback.sh`. |

Controles adicionales que el bloque conserva en verde: `T optimizer
test_control` (1 passed) y las reproducciones legadas F1 a F4 (`4 passed, 1
failed`: la que falla es F5, que pertenece a ARREGLOS 02 y no corre desde 02 B).

## Rojo y verde de cada regresion

Rojo en `ef93166d3de84620a233390c03ef21c366dfa0ff` (`app/`, `migrations/` y
`tools/` identicos a `149bf454`): 10 fallidas por asercion y 1 pasada (el
control de identidad, verde por diseno). Verde en el SHA de `acepta.txt`.

| Prueba (`tests/test_arreglos05_a_ciclo.py`) | Rojo: lo que hacia el codigo anterior | Verde |
|---|---|---|
| `5xx_en_el_primer_bid_no_frena_el_segundo` | un 500 abortaba la fase (`apply_error`); la segunda puja nunca salia | las dos salen; la segunda se aplica; la incierta queda sin sello y sin reenviar |
| `5xx_en_un_bid_no_frena_la_liberacion_de_cortes` | la pausa vencida seguia en `pending_veto` | se libera y se aplica en el mismo ciclo |
| `degraded_por_apply_abre_un_ejecutor_en_el_siguiente_horario` | «ya hay un ciclo degraded hoy» | el horario siguiente abre un ejecutor (`resume_of`), aplica lo pendiente y no decide |
| `lock_expirado_con_running_reciente_no_bloquea_el_horario` | «running iniciado hace menos de 3 h» | corre, decide de nuevo y el huerfano queda con rastro |
| `caida_despues_de_tx3_abre_ejecutor_sin_decidir_de_nuevo` | no habia sello de TX3 | ejecutor del origen; encola los cortes que la caida dejo sin fila |
| `concilia_antes_de_decidir` | el mismo ciclo saltaba la hoja como «bid en vuelo» y la confirmaba despues | concilia antes; la hoja se juzga desde el bid real |
| `lote_valido_vacio_deja_tx3_comprometida` | sin sello | segunda conexion ve `decision_committed_at` |
| `owner_vencido_con_respuesta_retenerida_no_sella_ni_repite` | el sucesor no podia completar lo pendiente | el owner vencido no sella ni manda mas; el sucesor concilia leyendo; cupo total 2 |
| `al_dia_siguiente_concilia_y_vence_lo_no_intentado` | nada vencia | el ambiguo se concilia; lo no intentado queda `vigencia_vencida`; la historia sin sello no se toca |
| `migracion_sello_y_origen_ida_reversa_e_ida` | faltaba la 0094 | sello de una transicion, origen solo en el INSERT, permiso por columna |
| `fallo_de_identidad_detiene_todas_las_mutaciones` | control (verde antes) | sigue verde |

El selector del instalador se cubre en
`tests/test_despliegue_motor.py::test_arreglos05_a_la_migracion_del_bloque_entra_en_el_despliegue`.

## Revision automatica del PR #746

El revisor automatico abrio cinco hilos. Los cinco eran de grado bloqueante
para lo que A promete y se corrigieron, cada uno con su prueba en rojo
(`92371a22`, registro en [review-rojo.txt](review-rojo.txt)) y en verde, y su
commit.

| Hilo | Que fallaba | Correccion | Prueba nueva | Commit |
|---|---|---|---|---|
| 1 | Un ejecutor no decide, asi que sus notas no traen `target`: la revalidacion de una pausa economica quedaba en `espera_target` y el horario siguiente no la completaba. | El aplicador lleva `ciclo_origen` (el ciclo que decidio) y la revalidacion de pausas resuelve el target ahi. Intentos y confirmacion siguen del ejecutor. | `test_arreglos05_a_ejecutor_libera_pausa_economica_con_el_target_del_origen` | `914424e5` |
| 2 | El ejecutor cargaba todas las pujas del origen y reencolaba todo corte sin fila: reevaluaba decisiones ya cerradas sin intento (`modo_no_live`, `choque_clave`). | `bids_recuperables` y el encolado usan el mismo criterio de desenlace que el plan; se filtra al aplicar, despues de conciliar. | `test_arreglos05_a_ejecutor_no_repite_decisiones_que_ya_tienen_desenlace` | `06fde246` |
| 3 | El plan solo abria ejecutor con alguna decision huerfana; la vista no ve reactivaciones ni cortes en cola, y esas fases quedaban sin correr. | El origen guarda `notes.apply.completo` cuando sus fases terminan; el plan abre ejecutor si falta o hay decisiones sin desenlace; el ejecutor marca el origen al terminar bien. | `test_arreglos05_a_interrumpido_tras_tx3_abre_ejecutor_sin_huerfanas` (en el rojo aparece con su nombre anterior) | `21a23178` |
| 4 | El dueno se validaba antes de cada HTTP y no al volver: con el LIST de verificacion en vuelo, el dueno desplazado sellaba la puja. | Toda confirmacion pasa por `Aplicador.confirma_cercada`: la fila del claim se lee con `FOR UPDATE` dentro de la transaccion que sella; un dueno desplazado no sella. | `test_arreglos05_a_dueno_desplazado_durante_la_lectura_de_vuelta_no_sella`, con y sin aviso pre-HTTP | `f6333519` |
| 5 | El sello iba con la hora de la base: un ciclo corrido con `--decided-at` de ayer y terminado hoy contaba como la decision de hoy y suprimia el origen de hoy. | El sello lleva el reloj de la decision (acotado al ahora de la base); el plan compara con el dia del reloj de la corrida. | `test_arreglos05_a_origen_decidido_con_reloj_de_ayer_no_cuenta_como_el_de_hoy` | `3c7693bb` |
| 6 | La revalidacion tomaba del origen, ademas del target, su evaluacion de producto, que tiene horas: si el producto perdia su costo vigente, la recuperacion aplicaba la pausa al 4 % del origen. | Notas y economia separadas: en un ejecutor la evaluacion de producto no cuenta como fresca y una pausa por margen de producto queda en `espera_target`, fila `released` en cola. | `test_arreglos05_a_ejecutor_no_aplica_pausa_con_la_evaluacion_del_origen` | `160612ce` |

Pruebas existentes tocadas por estas correcciones: la del reconciliador que
espiaba `_confirma_resumen` ahora cuenta las confirmaciones cercadas del
aplicador (sigue exigiendo dos confirmaciones y un solo cliente).

### Revision de Astra (gpt-6.2-astra)

Cuatro comentarios P1 sobre `b76fff40`, corregidos cada uno con su prueba en
rojo (registro en [review-rojo.txt](review-rojo.txt)) y en verde, y su commit.

| Comentario | Que fallaba | Correccion | Prueba nueva | Commit |
|---|---|---|---|---|
| 1 | Sin fila del claim la cerca del sello dejaba sellar: si el sucesor tomaba y soltaba el claim, el dueno desplazado sellaba con una lectura vieja. | Sin fila no se es dueno: no se sella. | Variante `sin_fila` de `test_arreglos05_a_dueno_desplazado_durante_la_lectura_de_vuelta_no_sella` | `616c61a0` |
| 2 | Un 401 o 403 de identidad o permisos se trataba como el rechazo de ESA hoja y el lote segui con la siguiente. | Fallo global en pujas, cortes, harvest, hermanas y reversas: el intento se sella con su rechazo y se propaga; los aisladores por fila dejan de tragarselo. | `test_arreglos05_a_rechazo_de_identidad_detiene_todas_las_mutaciones`, `test_arreglos05_a_rechazo_de_identidad_no_se_aisla_por_fila` | `a7d7df0e` |
| 3 | Una reactivacion con la respuesta perdida cobraba cuota y reenviaba el PUT aunque Amazon ya habilito la hoja. | La reversa sin sello se resuelve por lectura antes de cobrar cuota y reenviar; el sello y el cache pasan por la cerca del claim. | `test_arreglos05_a_reversa_incierta_se_resuelve_por_lectura_antes_de_reenviar` | `8de0d66f` |
| 4 | La TX3 podia cerrar despues de que el sucesor tomo el claim y leyo el plan: dos origenes decidian el mismo dia. | Dentro de la TX3 se comprueba y bloquea la fila del claim antes de sellar. | `test_arreglos05_a_tx3_no_se_compromete_si_el_claim_cambio_de_dueno` (dos conexiones) | `9b178290` |

Montaje de pruebas ajustado por estas correcciones: las de los hilos 1 y 6 toman
el claim del ejecutor antes de su fase, porque la cerca exige la fila del claim.

## Vecinos

Lote de 28 archivos de ciclo, cron, apply, cola, harvest, temporada,
notificaciones, CLI, goals, fabrica y dashboard: 960 pasan y 7 fallan. Los 7
fallan igual en `149bf454` en este entorno (comprobado ejecutandolos sobre un
`git archive` del padre): `test_harvest_destino` x3 (su banco no aplica la
0093 y `temporada_efecto` no existe), `test_notifica::test_avisar_campanas_*`
x3 y `test_orbit07_m1::test_sombra_sin_target_aplica_la_proteccion_en_el_juicio_hipotetico`.
No son de este bloque y no se corrigieron aqui.

Pruebas existentes adaptadas al orden nuevo, sin quitar aserciones: el CLI y
el cron espiaban `corre_ciclo` y ahora espian `atender_plataforma`; la regla de
las 3 h se sustituye por la del claim; la prueba de la heuristica con conexion
falsa se retira (su lugar lo toman las regresiones sobre base real); las de
temporada B comprueban que la hoja se juzga desde el valor real una vez
conciliada; la de harvest comprueba el rastro durable sin esperar que el error
aborte el barrido.

## Decisiones de diseno y diferencias con el contrato

- **La puja incierta conserva el rastro de siempre.** Su fila del ledger queda
  SIN sello (APPLY.md 6.1). Una version intermedia la sellaba como lectura
  ambigua y rompia el control 01 del arnes de aceptacion, que esta fijado por
  manifiesto. Lo unico que cambia es que ya no aborta el lote.
- **`_fase_apply` conserva su firma** (`cycle_id`, `decided_at`, `guard`,
  `aplicador_factory`): sin una conciliacion ya hecha la corre el mismo, como
  siempre. El arnes la llama suelta. El ciclo le pasa la que corrio antes de
  decidir (`origen`, `reconciliacion`).
- **`corre_ciclo` se conserva**: siempre abre un origen nuevo y no consulta el
  plan del dia. Lo usa el arnes de aceptacion y la corrida manual.
- **D-1 cerrada.** La conciliacion ya corre antes de TX2 (diferencia D-1 de BASE).
- **D-4 cerrada.** La primera migracion de 05 es la `0094`; `0093` sigue tomada
  dos veces.
- Firmas nuevas: `atender_plataforma`, `ResultadoAtencion`, `_PlanHoy`,
  `_fase_reconciliacion`, `_Reconciliacion`, `_compromete_tx3`,
  `expira_no_intentadas`, `DesenlaceBid`; `ResultadoCiclo.resume_of`;
  `_fase_apply(origen, reconciliacion)`; `_fase_notifica(resume_of)`;
  `notifica_digest(con_contribucion)`; `_abre_envelope(resume_of)`. Firmas
  de BASE que no cambian: `aplica_bids`, `_confirma_resumen`,
  `registra_sin_aplicar`, `aplica_reactivaciones`, `cadena_bid`.

## Invariantes medidos en produccion antes de fijarlos

Solo lectura (`previa.sh` sobre `previa.sql`, rol de lectura, 22 bloques, 0
erroes; resultado en [previa.txt](previa.txt)):

- 207 envelopes: 205 con evidencia de TX3 (175 live, 30 shadow), 2 `skipped`,
  0 abiertos, 0 con conteo distinto. Por eso la migracion solo sella los
  ciclos cerrados el dia del despliegue y no la historia.
- 197 pujas de 2026-09-02 a 2026-09-25 estan sin desenlace y sin intento. Por
  eso `vigencia_vencida` solo alcanza origenes con sello de TX3.
- En las 2,923 decisiones `decided_at` cae el mismo dia UTC que el cierre.
- `app_decide` no tiene UPDATE general sobre `optimizer_cycle`; solo cinco
  columnas. La 0094 agrega una y nada mas.

## Como se regresa

1. Reversa de la migracion: `0094_reversa_arreglos05_ciclo_origen.sql`
   aborta si ya hay desenlaces `vigencia_vencida`; conserva ciclos,
   ejecutores y todas las filas de `apply_attempt`.
2. `MOTOR_SHA=<sha del merge> bash tools/despliegue/rollback.sh` restaura el
   codigo antes de revertir las migraciones. El binario anterior corre contra
   el esquema nuevo: cierra sus ciclos sin sello y no conoce `resume_of`.
3. El rollback conserva los intentos. Limites con el binario anterior: no
   completa lo que un ejecutor dejo pendiente (visible en
   `v_decision_huerfana`) y no expira lo no intentado al cambiar de dia.

## Limites declarados

- Una tormenta de 5xx en los PUT deja cada puja del lote incierta; todas se
  concilian con lecturas en el ciclo siguiente.
- Las pantallas de «ultimo ciclo» no distinguen ejecutores: mientras uno sea el
  mas reciente lo muestran con 0 decisiones, y Salud no muestra el target de
  ese dia. Pendiente de un bloque que toque esas pantallas y sus bancos.
- `tests/test_despliegue_motor.py` (B.5) no es ejecutable en esta PC: los
  accesos a `psql`, `pg_dump` y `flock` solo satisfacen su comprobacion de
  presencia. En este entorno sus 35 pruebas dan error al armar el repositorio
  de prueba; la del bloque (`test_arreglos05_a_la_migracion_del_bloque_entran_en_el_despliegue`)
  si corre y pasa porque no usa esas herramientas.
