# Resultado del bloque C de ARREGLOS 05

Bloque de implementacion, `lane: release`. Base: `master` en
`d0ed286e6048d38db45abe49006a20f25d2b3cdf` (con BASE, A y B). Rama
`arreglos-05/c`. Codigo verificado: `40eb2bb9`. Fecha: 2026-11-24
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
| C.1 | `N c` | `40eb2bb9` | 16 passed, 0 omitidas, salida 0 | La intencion es visible antes del HTTP; el ACK/ID se guarda antes del readback; dos intentos abiertos de la misma operacion no coexisten; la migracion ida/reversa/ida corre con los roles reales. | `rollback.sh` con el `MOTOR_SHA` anterior; la reversa `0097` aborta si hay intentos y nunca borra el historial. |
| C.2 | `T writes test_11` | `40eb2bb9` | 1 passed, salida 0 | Un solo POST tras ACK y readback fallido; dos CLI no crean dos objetos; el objeto repuesto nace PAUSED. | Igual que C.1. |
| C.2 | `N c` | `40eb2bb9` | 16 passed, salida 0 | Cobertura de crash antes/despues de POST, ACK y readback, rechazo 4xx, 5xx, 401/403, lectura truncada/omitida/duplicada y repeticion sin HTTP. | Igual que C.1. |
| C.3 | `N c` | `40eb2bb9` | 16 passed, salida 0 | La reversa archiva solo `repuesto_external`; una caida no repite el DELETE; un rechazo definitivo permite otro intento registrado; el historial y la keyword original no se tocan. | Igual que C.1. |

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
`posts: 2` y la misma corrida en `40eb2bb9` da `1 passed`. El padre no tiene
`reponer_keyword` ni `revertir_reposicion`, no acepta
`--revertir-reposicion` y no tiene la tabla `0097`.

En `40eb2bb9`: `N c` 16 passed; `T writes test_11` 1 passed; modelo de estados
170 passed.

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

1. `tools/despliegue/rollback.sh` con `MOTOR_SHA=<sha anterior>`: restaura el
   codigo antes de tocar el esquema.
2. La reversa `0097_reversa_arreglos05_reposicion.sql` aborta si la tabla
   conserva intentos: el down jamas borra el historial. Con intentos abiertos,
   primero se concilian con el binario de este bloque.
3. Con el binario anterior, la tabla `0097` queda ignorada; sus funciones de
   reposicion vuelven al camino viejo (un segundo POST tras un ACK sin
   readback). No se borran intentos para forzar el down.

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

Ronda 1 (Devin, informativo; `40eb2bb9`): cuatro hallazgos, dos P1 reales
corregidos con su regresion.

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
- **P2, 401/403 en la reversa.** El bloque aborta la fase y deja el intento
  abierto; tras corregir credenciales, la lectura del objetivo vivo es
  incertidumbre y no reenvia. Es la politica pedida para un resultado
  desconocido y el modelo de A no sella un rechazo de identidad en `incierto`
  (`sella_intento=False`); queda para reconciliacion manual. No se corrige en
  esta ronda.
- **P2, el lote sigue tras un rechazo.** El motor aisla filas: un 400 en una
  fila no impide atender las siguientes (cada una PAUSED). El mensaje de la
  CLI decia «lote detenido»; se cambio a «lote con fallos» para no mentir. No
  es un fallo de datos ni de dinero.

Pendiente la revision asignada (Astra u otro modelo distinto al
implementador). `ai-review` es informativo y Devin no cuenta como compuerta.
