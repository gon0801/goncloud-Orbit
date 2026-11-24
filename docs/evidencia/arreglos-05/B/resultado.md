# Resultado del bloque B de ARREGLOS 05

Bloque de implementacion, `lane: release`. Base: `master` en
`23ba2385c7394ef5f9693e2802dbe9bb640ac791` (el codigo de `app/` y
`migrations/` es el de `17fa9a17`). Rama `arreglos-05/b`. Codigo verificado:
`5fbb78ef`. Fecha: 2026-11-24 (America/Vancouver). Contrato: seccion
«B. Identidad, confirmacion y reversas» de [DISENO.md](../DISENO.md).

## Para el dueño

- Una keyword que alguien crea o archiva a mano mientras Orbit cosecha ya no
  puede borrarse en una reversa. Si la keyword propia fue reemplazada por otra
  con el mismo texto, la reversa se detiene y avisa; no toca la ajena.
- Un negative que Amazon acepto sin devolver respuesta ya no queda sin
  reversa: el siguiente ciclo lo confirma leyendo y el dueno puede revertirlo
  desde la API, una sola vez.
- Revertir una puja vuelve al valor que Orbit leyo en Amazon antes de
  aplicarla. Si despues el dueno movio esa puja a mano, la reversa no la pisa.
- Si una reversa se queda a medias (respuesta perdida, proceso caido), el
  intento siguiente la resuelve leyendo, sin repetir el envio.

## Para quien mantiene

- `app/apply_estados.py` sigue siendo el unico dueno de las transiciones: 11
  estados, 15 eventos, 165 casillas. Lo nuevo son dos estados (`ajeno`,
  `movido_por_dueno`), tres eventos y dos clasificadores puros. La ampliacion
  esta en [../A/estados.md](../A/estados.md) y en `docs/APPLY.md` 9.2.
- `evento_desde_lectura_objeto` es el unico resolver de identidad. Lo usan la
  creacion, la lectura de vuelta, la conciliacion y las tres reversas; no
  hay que escribir otra regla de «propio frente a ajeno».
- `app/apply_negative.py` es el dueno del camino del negative de corte
  (creacion con evidencia previa y su reversa). `app/apply_harvest_contexto.py`
  salio de `app/apply_harvest.py` para respetar el presupuesto de 900 lineas.
- `app/apply_store.py` gano `hechos_reversa`, `estado_de_reversa`,
  `bid_previo_comprobado`, `bids_escritos_por_orbit` e
  `intento_normal_abierto`. Las dos primeras vivian en
  `app/apply_cola_ejecuta.py`; el nombre viejo se reexporta.
- Migracion `0095` con su `_reversa_`; detalle en `docs/DATABASE.md`.

## Por fila

| Fila | Comando de Acepta | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| B.1 | `T writes test_03` | `5fbb78ef` | 1 passed, salida 0 | La cosecha conserva el ID del ACK, no adopta una keyword sustituida y su reversa no borra la ajena. | Codigo: `rollback.sh` con el SHA anterior. Datos: ninguno cambia en Amazon salvo lo que la reversa propia ya hacia. |
| B.1 | `N b` | `5fbb78ef` | 26 passed, 0 omitidas, salida 0 | Regresiones propias del bloque, mas la del selector del instalador. | Igual que arriba. |
| B.2 | `T writes test_10` | `5fbb78ef` | 1 passed, salida 0 | Un negative aceptado sin respuesta se confirma leyendo y su reversa manual lo retira una vez. | Migracion `0095_reversa_...` (aborta si hay un intento abierto con evidencia). |
| B.3 | `N b` | `5fbb78ef` | 26 passed, salida 0 | La reversa de bid vuelve al valor leido, respeta un cambio manual y no repite el PUT tras una caida. | Igual que B.2. |

Comandos y salidas completos en [acepta.txt](acepta.txt), que genera
[acepta.sh](acepta.sh). El SHA del encabezado de cada parte es el arbol
verificado; este archivo y `acepta.txt` entran despues, en un commit que
solo toca esta carpeta.

### B.1. Harvest y reconciliacion

Que cambia:

- El ID del ACK queda en `harvest_job.external_ids.keyword_id` y el ACK en el
  ledger ANTES de la lectura de vuelta. El intento sigue abierto hasta que esa
  lectura verifique el ID con grupo, texto y match; recien ahi se sella `ok`.
- Un ID distinto con el mismo texto es ajeno: no se adopta como propio ni se
  borra. La lectura de vuelta sella el intento como `ajeno` (o `archivado`),
  el job cierra fallido con alerta y la reversa automatica retira solo lo
  propio que sigue vivo.
- Una lectura que no permite concluir (dos o mas vivas con el texto, o el ID
  propio vivo con otro texto) deja el job pendiente en su fase; el ciclo
  siguiente la repite.
- Reversa manual, automatica y de hermanas usan el mismo resolver y solo
  borran un objeto si lo ven vivo con su identidad bajo el ID del ACK. Lo
  propio que ya no vive se confirma por lectura, sin HTTP. Lo sustituido o
  dudoso queda pendiente.

Pruebas (en `tests/test_arreglos05_b_reversas.py`):
`...cosecha_no_adopta_keyword_sustituida_ni_la_borra`,
`...cosecha_con_identidad_dudosa_queda_pendiente_sin_borrar`,
`...cosecha_cuya_keyword_propia_fue_archivada_cierra_sin_borrar_ajenos`,
`...cosecha_guarda_el_ack_antes_del_readback_y_el_ciclo_siguiente_lo_sella`,
`...reversa_manual_no_borra_keyword_sustituida_y_queda_pendiente`,
`...reversa_manual_confirma_por_lectura_lo_propio_ya_retirado`,
`...reversa_manual_no_borra_hermana_sustituida`.

### B.2. Negative conciliado

Que cambia:

- Antes del POST, una lectura completa demuestra que la identidad no esta viva
  y esa evidencia nace con la fila del ledger (`ausencia_previa`). Sin lectura
  concluyente no nace el intento y no sale el envio. Si ya vive algo con esa
  identidad, no se envia ni se adopta: la fila queda fallida.
- La conciliacion confirma por LIST solo si el intento abierto trae la
  evidencia de esa misma identidad. El resumen guarda `platform_ack` con
  `fuente: list` y `procedencia: efecto_reconciliado`; no se fabrica un ACK
  remoto. Sin evidencia, con varios candidatos o con el ID sustituido, queda
  pendiente.
- La reversa manual toma el ID de la confirmacion durable de la decision (su
  resumen y, sin este, el ACK del ultimo intento aplicado), lo relee vivo,
  registra su intento y confirma una vez. La segunda llamada responde 409 sin
  abrir HTTP. Un DELETE con respuesta perdida queda abierto y la llamada
  siguiente lo resuelve leyendo.

Pruebas: `...negative_guarda_la_ausencia_previa_antes_del_post`,
`...lectura_previa_incompleta_no_autoriza_el_post`,
`...negative_preexistente_no_se_envia_ni_se_borra`,
`...negative_reconciliado_tiene_reversa_ejecutable_y_se_confirma_una_vez`,
`...sin_evidencia_previa_el_negative_no_se_confirma_por_conciliacion`,
`...con_varios_candidatos_el_negative_reconciliado_queda_pendiente`,
`...reversa_del_negative_respeta_lo_que_no_es_el_id_acreditado`,
`...reversa_del_negative_con_delete_perdido_se_resuelve_leyendo`.

### B.3. Reversa de bid

Que cambia:

- Cada PUT de puja guarda en el ledger el bid leido justo antes
  (`bid_previo`). La reversa vuelve a ese valor. Solo una decision aplicada
  antes de la `0095` vuelve a `decision.old_value`, el unico valor registrado
  entonces.
- Antes de enviar, la reversa relee la hoja. Si el valor vivo no lo escribio
  Orbit, es un cambio manual y no se envia. Si ya es el valor previo, se
  confirma por lectura. Un intento abierto se resuelve leyendo antes de otro
  envio.
- Un 401 o 403 en la lectura previa o en el PUT es un resultado de la
  frontera (`confirmada: false`), no un 500.

Pruebas: `...la_puja_guarda_el_bid_leido_antes_del_put`,
`...reversa_de_bid_vuelve_al_valor_leido_antes_del_put`,
`...reversa_de_bid_respeta_un_cambio_manual_posterior`,
`...reversa_de_bid_con_respuesta_perdida_no_duplica_el_put`,
`...reversa_de_bid_incierta_que_no_aplico_se_lee_y_se_reenvia_una_vez`,
`...rechazo_de_identidad_en_la_reversa_de_bid_es_un_resultado`.
Las del regreso del dueno (`tests/test_apply.py::test_regreso_del_dueno_*`)
siguen verdes con sus aserciones intactas.

### Migracion 0095

Aditiva, con `0095_reversa_arreglos05_evidencia_previa.sql`, cabecera de sonda
en la linea 1, sin BEGIN/COMMIT, y sin permisos nuevos: el INSERT que ya
tenia `app_decide` cubre las columnas y su UPDATE sigue por columna. El
instalador la toma por nombre (`migraciones_de`). Pruebas de ida, reversa e
ida con el rol real del motor y del selector del instalador:
`...migracion_evidencia_previa_ida_reversa_e_ida` y
`tests/test_despliegue_motor.py::test_arreglos05_b_la_migracion_del_bloque_entra_en_el_despliegue`.

## Rojo y verde

[rojo.txt](rojo.txt): contra `23ba2385`, 22 de las 25 regresiones del archivo
fallan por asercion de comportamiento, mas la del selector del instalador. Las
tres que pasan en el padre son controles declarados en su docstring. La prueba
de la tabla no puede importarse en el padre: nombra tipos que no existen; la
tabla del padre tiene 9 estados, 12 eventos y 108 casillas.

En `5fbb78ef`: `N b` 26 passed; tabla 170 passed.

## Vecinos

[vecinos.md](vecinos.md). Ninguna suite empeora: todas las que fallan en la
rama fallan igual y en los mismos casos en `23ba2385`, medido en un worktree
aparte. Once archivos de pruebas se portaron al contrato nuevo conservando sus
aserciones.

## Lectura previa de produccion

[previa.txt](previa.txt), solo conteos y nombres de objetos, rol `orbit_read`,
transaccion `read_only`, 2026-11-24 12:02 UTC:

- 576 intentos, ninguno abierto; la `0094` aplicada y la `0095` todavia no.
- Ningun negative confirmado por conciliacion. Las 3 aplicaciones de negative
  respaldan su ID con un ACK del ledger.
- 503 decisiones de puja aplicadas, ninguna sin `old_value`. 11 reversas de
  puja, todas `ok`. El regreso del dueno se uso 11 veces.

Esa ultima cifra importo: el regreso revierte la primera decision de una
racha, cuando Orbit ya movio esa puja despues. Una primera version de este
bloque exigia que la hoja tuviera el valor de esa decision y lo habria roto
en su primer uso tras el despliegue. Las pruebas del regreso lo atraparon
antes de empujar.

## Decisiones y limites

- **Que es un cambio manual.** No un valor distinto del de esta decision: un
  valor vivo que Orbit no escribio. Cada PUT propio que quedo `ok` y el destino
  de cada decision de puja aplicada cuentan como suyos. Fuente: el ledger,
  nunca el cache, que la ingesta tambien sincroniza.
- **Historia anterior a la 0095.** Sin lectura previa guardada la reversa de
  bid vuelve a `decision.old_value`. La relectura y el respeto por un cambio
  manual se aplican igual. Se resuelve solo a medida que las decisiones nuevas
  traen su `bid_previo`.
- **Autoria de la reconciliacion.** Ausencia previa mas aparicion posterior no
  prueba autoria absoluta frente a un creador externo concurrente. Por eso
  `EfectoReconciliado` es distinto de `CreacionPropia`: permite la reversa
  manual del efecto inequivoco con su procedencia visible y nunca autoriza la
  limpieza automatica de harvest ni adopta un objeto preexistente.
- **Un booleano no alcanza.** `keyword_creada` o `negative_creada` sin ID no
  autorizan a borrar nada. En la cosecha, una keyword que aparece tras un
  intento propio sin ACK se adopta como `incierta` y no entra en el plan de
  reversa.
- **Un apply que divergio.** Si Amazon quedo en otro valor, ese valor no es
  atribuible y la reversa no se envia a ciegas. La frontera manual ya se
  negaba por otra razon: esa decision no esta confirmada como aplicada.
- **Lo confirmado por lectura.** Nace una fila `reversa` sellada `ok` con
  `ack.conciliado` y su `fuente`. Declara su fuente; no es un ACK remoto.

## Recorrido previo al empuje

| Caso | Donde lo decide la tabla | Prueba |
|---|---|---|
| Respuesta ok | `(INCIERTO, OK_CONFIRMADO) -> SELLAR_APLICADO` | lectura de vuelta de cosecha, negative reconciliado, reversa de bid |
| 4xx determinista | `(PENDIENTE, ERROR_4XX_DETERMINISTA) -> SELLAR_RECHAZADO` | `test_regreso_del_dueno_sin_confirmar_levanta_con_el_motivo` |
| 5xx o red sin saber si aplico | `(PENDIENTE, ERROR_5XX_O_RED) -> MANTENER_INCIERTO`; se lee antes de otro envio | respuesta perdida en negative, en el DELETE y en el PUT de reversa |
| 401 o 403, lecturas incluidas | `(*, RECHAZO_IDENTIDAD) -> ABORTAR_FASE`; la frontera manual lo traduce | rechazo de identidad en la reversa de bid; las del bloque A |
| Lectura que omite la hoja o su estado | `ListaIncompleta`, nada se escribe | lectura previa incompleta; lector del 04 B-C |
| ID sustituido, preexistente o duplicado | `IDENTIDAD_SUSTITUIDA` y `IDENTIDAD_DUDOSA` | las pruebas de cosecha y de negative |
| Caida antes y despues de cada escritura | cada desenlace se sella en la misma transaccion | ACK antes del readback; DELETE y PUT perdidos |
| Cambio de dia y de modo | la lectura va antes de la guarda de modo | `test_apagar_goal_confirma_negative_aplicado_sin_reintentar_post` |
| Segundo intento de reversa ya confirmada | `(APLICADO, NUEVO_HORARIO) -> NO_TOCAR` | 409 sin HTTP; reanudacion sin repetir |

## Como se regresa

1. `tools/despliegue/rollback.sh` con `MOTOR_SHA=<sha anterior>`: restaura el
   codigo antes de revertir migraciones. El binario anterior corre contra el
   esquema con la `0095`.
2. La reversa de la migracion aborta si hay un intento ABIERTO con evidencia
   previa: hay que dejarlo conciliar con el binario de este bloque y repetir.
3. Con el binario anterior, limites conocidos: confirma un negative por
   identidad sin evidencia, sobrescribe el ID del ACK con el de la lectura de
   vuelta y revierte una puja al valor del cache.

La reversa de efectos (borrar lo creado) y la reversa de despliegue son
operaciones distintas.

## Seguimiento (P2)

Ninguno bloquea; requieren dos o mas fallos o una carrera.

1. Dos reversas manuales a la vez sobre la misma decision (dos CLI, o CLI y
   API) pueden abrir dos intentos. El efecto en Amazon es uno, el mismo ID. Ya
   ocurria antes de este bloque.
2. La reversa automatica de cosecha no revalida el claim antes de cada DELETE;
   solo importa si el claim cambia de dueno en medio de esa reversa.
3. La lectura previa del corte negative agrega un LIST filtrado por ad group a
   cada POST (cinco al dia por el cap). La reversa manual de cosecha hace dos
   lecturas por paso en vez de una.
4. El trabajo pendiente en `docs/APPLY.md` 9.1 sobre las pantallas de «ultimo
   ciclo» no cambia con este bloque.

## Fase 2

Pendiente hasta el aviso del coordinador: despliegue, recibo y segundo PR de
evidencia.
