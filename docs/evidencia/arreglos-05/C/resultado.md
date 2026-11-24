# Resultado del bloque C de ARREGLOS 05

Bloque de implementacion, `lane: release`. Base: `master` en
`d0ed286e6048d38db45abe49006a20f25d2b3cdf` (con BASE, A y B). Rama
`arreglos-05/c`. Codigo verificado: `3f8d1005`. Fecha: 2026-11-24
(America/Vancouver). Contrato: seccion «C. Reposicion durable por fila» de
[DISENO.md](../DISENO.md).

## Para el dueno

- Repetir `--reponer` ya no crea otra keyword cuando falla la lectura de
  confirmacion: el ACK y su ID quedan guardados antes del readback y la
  siguiente corrida lee por ese ID.
- Dos CLI a la vez sobre la misma fila no crean dos objetos: la intencion
  durable con su lease deja pasar a una sola y la otra solo concilia.
- La reversa archiva solo el ID repuesto propio, con intencion y readback. No
  recrea ni devuelve identidad o historia a la keyword archivada original.
- Un rechazo definitivo permite otro intento registrado; una lectura parcial,
  una omision o un duplicado dejan el intento pendiente y no disparan otro
  POST.

## Para quien mantiene

- `app/apply_estados.py` sigue siendo el unico dueno de las transiciones: no
  se agregan estados, eventos ni casillas. C suma dos adaptadores de hechos:
  `resultado_terminal_reposicion` y `evento_reposicion_desde_lectura`.
- `app/ads/archivar.py` expone `reponer_keyword` y `revertir_reposicion`. La
  implementacion vive en `app/ads/reposicion.py` por el presupuesto de 900
  lineas; la persistencia, en `app/ads/reposicion_store.py`.
- `app/ads/archivar_inertes.py` conserva la CLI `--reponer` y suma
  `--revertir-reposicion`. El protocolo no esta duplicado: `_reponer` y
  `_revertir` solo construyen clientes, perfiles y el POST/DELETE.
- Migracion `0097` con su `_reversa_`; detalle en `docs/DATABASE.md`. La
  tabla madre y la nueva se midieron en produccion antes de escribir el
  invariante: 0 filas y tabla nueva ausente.

## Por fila

| Fila | Comando de Acepta | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| C.1 | `N c` | `3f8d1005` | 21 passed, 0 omitidas, salida 0 | La intencion es visible antes del HTTP; el ACK/ID se guarda antes del readback; dos intentos abiertos de la misma operacion no coexisten; la migracion ida/reversa/ida corre con los roles reales. | `rollback.sh` con el SHA desplegado `73ea8f90`; la reversa `0097` aborta si la tabla conserva cualquier fila y nunca borra el historial. |
| C.2 | `T writes test_11` | `3f8d1005` | 1 passed, salida 0 | Un solo POST tras ACK y readback fallido; dos CLI no crean dos objetos; el objeto repuesto nace PAUSED. | Igual que C.1. |
| C.2 | `N c` | `3f8d1005` | 21 passed, salida 0 | Cobertura de crash antes/despues de POST, ACK y readback, rechazo 4xx, 5xx, 401/403, lectura truncada/omitida/duplicada y repeticion sin HTTP. | Igual que C.1. |
| C.3 | `N c` | `3f8d1005` | 21 passed, salida 0 | La reversa archiva solo `repuesto_external`; una caida no repite el DELETE; un rechazo definitivo permite otro intento registrado; el historial y la keyword original no se tocan. | Igual que C.1. |

Comandos y salidas completos en [acepta.txt](acepta.txt), que genera
[acepta.sh](acepta.sh). El SHA del encabezado de cada parte es el arbol
verificado; este archivo y los demas de esta carpeta entran despues, en un
commit que solo toca `docs/evidencia/arreglos-05/C/`.

### C.1. Migracion y ledger

`keyword_reposicion_intento` cuelga de `keyword_archivo_manual` y guarda
`archivo_id`, `seq`, `tipo` (`reponer`/`reversa`), `owner`, `lease_hasta`,
`request_payload` exacto, `ack`, `external_id`, `resultado`, `started_at`,
`heartbeat_at` y `finished_at`.

- `UNIQUE (archivo_id, seq)` ordena la historia; el indice unico parcial
  `(archivo_id, tipo) WHERE finished_at IS NULL` excluye dos intentos abiertos
  de la misma operacion.
- El trigger `keyword_reposicion_intento_invariantes` impide reescribir
  identidad o payload, des-sellar ACK/ID/resultado/tiempo, borrar filas y
  fijar tiempos fuera de `[started_at, now()]`. No son CHECK.
- Grants: `app_admin` INSERT y UPDATE por columna; `app_read` SELECT;
  `app_ingest` y `app_decide` sin SELECT. La prueba de ida/reversa/ida usa los
  roles reales y comprueba la sonda del instalador.

### C.2. Reponer

Camino por fila: `_estado` desde los hechos del ledger -> `decidir` ->
`ENVIAR` -> `abre_intento` (intencion con payload y lease) y `commit` ->
POST -> ACK e ID durables antes del readback -> lectura completa estricta de
04 B-C -> `evento_reposicion_desde_lectura` -> `decidir` -> sello del intento
y del archivo en la misma transaccion.

- Un 4xx sella `fallo:rechazo`; no es terminal para el archivo y la siguiente
  corrida registra el intento `seq+1`, con el tope de 3.
- Un 5xx, red o timeout deja el intento abierto (`incierto`). La lectura
  siguiente es obligatoria antes de cualquier envio.
- Un 401/403 en POST o en lectura aborta la fase en todas las rutas.
- Una lista truncada o ambigua (`ListaIncompleta`), una lectura que omite el
  ID, un duplicado/preexistente, o campana/bid/estado que no cuadran dejan
  `identidad_dudosa`: no se sella ni se reenvia.
- La repuesta nace PAUSED y el readback lo exige.

### C.3. Reversa

`--revertir-reposicion LOTE` toma solo filas `estado='repuesto'` con
`repuesto_external`. La lectura previa por ID decide:

- ID propio ya ARCHIVED: confirma por lectura, sin DELETE.
- ID propio vivo: DELETE con ACK e ID durables antes del readback.
- ID ausente, ajeno o lectura no concluyente: pendiente, cero DELETE.
- Un DELETE con respuesta perdida queda abierto; la corrida siguiente solo
  lee y no repite el envio. Un rechazo 4xx si permite otro intento registrado.
- La keyword original archivada, su identidad y su historia no se tocan.

## Rojo y verde

[rojo.txt](rojo.txt): contra `d0ed286e`, `T writes test_11` falla con
`posts: 2` y la misma corrida en `3f8d1005` da `1 passed`. El padre no tiene
`reponer_keyword` ni `revertir_reposicion`, no acepta
`--revertir-reposicion` y no tiene la tabla `0097`.

En `3f8d1005`: `N c` 21 passed; `T writes test_11` 1 passed; modelo de estados
181 passed.

## Lectura previa de produccion

[previa.txt](previa.txt), solo `SELECT`, rol `orbit_read`, transaccion
`read_only`, 2026-11-24 16:41 UTC:

- `keyword_archivo_manual` con 0 filas: `planeado|applied|failed|repuesto` =
  `0|0|0|0`, sin IDs y secuencia en 0.
- `keyword_reposicion_intento` ausente, sin FK apuntando a la tabla madre.
- `app_admin` con INSERT/UPDATE; `app_decide`, `app_ingest` y `app_read` solo
  SELECT; `orbit_read` es miembro de `app_read` y `orbit_admin` de
  `app_admin`.

No hay backfill. La migracion no asume filas.

## Como se regresa

1. `MOTOR_SHA=73ea8f901a3951dd1a74ac8aae1c47fb476e6e77 bash tools/despliegue/rollback.sh`.
   Ese es el SHA desplegado y el que tiene diario en `ultimo.env`; con el SHA
   anterior el script detecta que el diario es de otro `MOTOR_SHA` y sale 0 sin
   deshacer nada.
2. El script restaura primero interruptores, cron y el codigo del respaldo
   `CODIGO=predeploy-*` que dejo este despliegue, recrea `orbit-app-1`,
   comprueba `/health` y actualiza `despliegue/sha` a `SHA_PREVIO`
   (`ae7626ba4c22ad5d09979b9fb5d7a115b0cc990f`). Solo despues intenta la
   reversa de la migracion `0097`.
3. La reversa `0097_reversa_arreglos05_reposicion.sql` aborta si la tabla
   conserva CUALQUIER fila, incluida una sellada `ok`: su `IF EXISTS` no
   distingue abiertos de sellados. En ese caso el esquema y el historial se
   conservan; el codigo ya se restauro y el motor informa
   `rollback_incompleto` (sale 2). Es una reversa PARCIAL del despliegue:
   registrar que el codigo quedo restaurado y que la migracion no se retiro.
   No se repite el rollback para "arreglarla": volveria a recrear el
   contenedor y saldria otra vez 2. Si quedan intentos abiertos, se concilian
   con una version compatible del motor como operacion aparte; aun asi la
   tabla conserva filas y el down no procede.
4. Con el codigo restaurado (`ae7626ba`) la tabla `0097` queda ignorada y la
   reposicion vuelve al camino viejo (un segundo POST tras un ACK sin
   readback). No se borran intentos.

La reversa de efectos (archivar la keyword repuesta) y la reversa de
despliegue son operaciones distintas.

## Decisiones y limites

- **El lease es de 300 s.** Un sucesor no toca un intento con lease vivo; si
  el proceso murio, la corrida siguiente al vencer lo toma y solo CONCILIA.
  Antes de vencer, la corrida reporta `pendiente` sin HTTP: es el precio de no
  duplicar objetos.
- **Sin ACK y con una identidad viva, el intento queda pendiente.** No se
  adopta un objeto que pudo preexistir. Con la lista completa y sin identidad
  viva, se sella `ok:no_aplicado` y el siguiente intento es posible.
- **Una lectura que omite el ID no prueba ausencia.** Se queda en
  `identidad_dudosa`, aunque el ACK y el ID ya esten guardados.
- **Un duplicado/preexistente no se borra solo.** Si la lectura confirma el ID
  propio y ademas hay otro vivo con la misma identidad, no se sella; queda
  pendiente para que una persona decida.
- **La reversa abierta no reenvia.** Si el DELETE pudo salir y el objetivo
  sigue vivo, el intento queda incierto. No se repite el envio; se resuelve
  por lectura o por una persona.
- **El intento de archivo `failed` sigue recuperandose.** `--reponer` con
  mutacion conserva la reconciliacion previa del ledger madre; una fila
  archivada en Amazon se promueve a `applied` antes de leer las reponibles.

## Seguimiento (P2)

Ninguno bloquea; requieren dos o mas fallos o una carrera.

1. Dos reposiciones sobre filas distintas del mismo lote comparten la lectura
   completa de la cuenta por fila; es costoso en lotes grandes. Se puede
   reutilizar la lectura mientras no haya una mutacion intermedia.
2. Un `reponer` que crea un duplicado porque ya existia un gemelo vivo y el
   readback no lo ve a tiempo queda pendiente, pero el objeto creado no se
   borra automaticamente. Es la politica de "preexistente: pendiente".
3. La recuperacion de un intento con lease vencido se hace por corrida, no por
   un proceso de fondo: un lote sin nueva corrida espera a la siguiente.
4. El trabajo pendiente en `docs/APPLY.md` 9.1 sobre las pantallas de «ultimo
   ciclo» no cambia con este bloque.

## Revision

Ronda 1 (Devin, informativo; `3f8d1005`): cuatro hallazgos, tres corregidos con
su regresion y uno de aislamiento anotado.

- **P1, sonda del instalador.** La sonda usaba
  `'public.keyword_reposicion_intento'::regclass` dentro del `EXISTS`; en una
  base sin la tabla PostgreSQL resuelve el literal antes del `AND` y el
  instalador aborta antes de aplicar. Se cambio a
  `to_regclass('public.keyword_reposicion_intento')` y la prueba
  `test_arreglos05_c_migracion_ida_reversa_e_ida_y_roles_reales` evalua la
  sonda sobre una base sin la tabla: `False`, sin error, y luego aplica.
- **P1, dos claims tras el sello.** Dos CLI podian decidir enviar antes de que
  el primero sellara; el segundo reservaba despues del sello y creaba otro
  objeto. `store.abre_intento` ahora toma un cerrojo transaccional por
  `(archivo, tipo)` y revalida el estado del archivo, el ultimo `ok` y la
  ausencia de intentos abiertos; el sello toma el mismo cerrojo. Regresion:
  `test_arreglos05_c_claim_revalida_el_sello_bajo_cerrojo`, con una segunda
  conexion.
- **P1, 401/403 en la reversa (corregido).** El 401/403 de un DELETE o POST
  queda ahora en el ACK durable del intento abierto. Al vencer el lease, la
  conciliacion clasifica ese ACK con el evento nuevo
  `rechazo_identidad_definitivo`: `(INCIERTO, RECHAZO_IDENTIDAD_DEFINITIVO) ->
  SELLAR_RECHAZADO -> RECHAZADO` (`sella_intento=True`), y la misma corrida
  registra un intento nuevo. El 5xx o la respuesta perdida no llevan ese ACK y
  siguen inciertos: la prueba de no reenviar se conserva. Regresion:
  `test_arreglos05_c_403_en_reversa_se_sella_y_permite_reintentar`. La tabla
  pasa de 165 a 176 casillas (11 estados por 16 eventos); los 181 casos de
  `test_arreglos05_a_estados.py` quedan verdes.
- **P2, el lote sigue tras un rechazo.** El motor aisla filas: un 400 en una
  fila no impide atender las siguientes (cada una PAUSED). El mensaje de la
  CLI decia «lote detenido»; se cambio a «lote con fallos» para no mentir. No
  es un fallo de datos ni de dinero.

Pendiente la revision asignada (Astra u otro modelo distinto al
implementador). `ai-review` es informativo y Devin no cuenta como compuerta.

## Ronda 2 (Astra, revisor asignado, pasada 1; `3f8d1005`)

Tres P1, corregidos con su prueba en rojo contra `6577a5eb`
([rojo-ronda2.txt](rojo-ronda2.txt)):

- **Total declarado.** `app/apply_harvest_list._lista_completa` ahora exige que
  `totalResults` cuadre con lo acumulado antes de aceptar el fin de la lista,
  igual que `structure_api`. Una pagina `keywords: []` con `totalResults: 1`
  lanza `ListaIncompleta` y no autoriza ausencia. Regresion:
  `test_arreglos05_c_total_declarado_mayor_no_autoriza_ausencia` (el intento
  sigue abierto y no hay POST).
- **ACK de forma inesperada.** Un 200/207 con cuerpo JSON null (o lista) ya no
  se sella como `fallo:rechazo`: el intento queda abierto con el ACK durable.
  Solo un 4xx o un 207 con errores por item cuentan como rechazo explicito.
  Regresion: `test_arreglos05_c_ack_nulo_no_sella_rechazo` (dos corridas, un
  solo POST).
- **401/403 durable.** El rechazo de identidad del DELETE (y del POST) se
  guarda en el ACK antes de abortar la fase. La conciliacion lo clasifica con
  `rechazo_identidad_definitivo` y lo sella; la misma corrida registra el
  intento siguiente. Regresion:
  `test_arreglos05_c_403_en_reversa_se_sella_y_permite_reintentar` (403,
  credenciales corregidas, DELETE exitoso). Un 5xx sigue sin reenviar.

## Ronda 3 (Devin, seguimientos; `3f8d1005`)

- **P1: `totalResults` en listas filtradas.** La validacion de total agregada
  en la ronda 2 se aplicaba tambien con `adGroupIdFilter`; Amazon puede contar
  filas de otros grupos y la aprobacion de bloqueos rechazaba una lista
  completa. Ahora el total solo se exige sin filtro; con filtro se conservan
  token y pertenencia. Regresion: `test_arreglos05_c_total_solo_se_exige_sin_filtro`
  (mismo total, sin filtro falla; con filtro se acepta). Rojo contra
  `64e06ec8` en [rojo-ronda2.txt](rojo-ronda2.txt).
- **P2 corregido: reversa con ACK ambiguo.** Un 207/200 con cuerpo null ya no
  se queda en `pendiente`: el ACK queda durable y la lectura por el ID propio
  confirma el archivado; si no concluye, sigue abierto. Regresion:
  `test_arreglos05_c_reversa_con_ack_nulo_confirma_por_el_id_conocido` (rojo
  contra `afeecf11`).

## Fase 2

Hecha el 2026-11-24. La integracion es `73ea8f901a3951dd1a74ac8aae1c47fb476e6e77` (merge del PR #755 en
`master`); se desplego con `tools/despliegue/desplegar.sh` (ensayo de solo
lectura OK, migracion `0097_arreglos05_reposicion.sql` aplicada) y el
`checklist.sh` quedo verde. `N c` paso 21 en el SHA integrado y la lectura de
solo lectura de produccion (`lee_prod.sh`, rol `orbit_read`) confirmo la tabla
nueva con sus columnas, indice unico parcial, triggers y privilegios, con 0
filas y 0 intentos abiertos. Recibos: [despliegue.txt](despliegue.txt),
[checklist.txt](checklist.txt) y [prueba-real.txt](prueba-real.txt).

**Reversa.** `MOTOR_SHA=73ea8f901a3951dd1a74ac8aae1c47fb476e6e77 bash
tools/despliegue/rollback.sh`, con el SHA desplegado y su diario `ultimo.env`.
Restaura el codigo del respaldo `CODIGO=predeploy-*`, recrea el contenedor,
comprueba `/health` y despues intenta la reversa de la `0097`. Si la tabla
conserva cualquier fila (sellada o abierta), la reversa de la migracion aborta:
el esquema y el historial se conservan y el motor informa
`rollback_incompleto` aunque el codigo ya este restaurado. Es una reversa
parcial; no se repite el rollback para forzarla. Los intentos abiertos se
concilian con una version compatible como operacion aparte. Con el SHA anterior
el script sale 0 sin deshacer nada.
