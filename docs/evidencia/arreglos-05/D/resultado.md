# Resultado del bloque D de ARREGLOS 05

Bloque de implementacion, `lane: release`. Base: `master` en
`7ef47542f5ab421359f049583b1bc7db95662079` (con BASE, A, B y C integrados y
desplegados). Rama `arreglos-05/d`. Codigo verificado: `9acd2646` y las
correcciones de las rondas 1 y 2 `d4e613b8`/`72c4ee29`/`db5e5485`; pruebas
y candados: `f946d329`, `d4e613b8` y `db5e5485`. Fecha: 2026-11-24 (America/Vancouver). Contrato: seccion
«D-E. Escritor unico y practicas vencidas» de [DISENO.md](../DISENO.md), solo
la parte D.

## Para el dueno

- La fabrica (terminal, pantalla y API de impulso) ya no escribe con su propio
  cliente y token: sus creaciones y las pausas o activaciones de campana salen
  por el mismo cliente protegido que el motor. Un cliente que se intentara
  construir con un modo distinto de `live` revienta antes de tocar la red.
- El archivo manual de inertes, la reposicion de keywords de C y su reversa
  usan ese mismo transporte: perfil sellado, ruta de la allowlist y ACK
  canonico. El protocolo de C (intencion, ACK antes del readback, lectura
  estricta y sello) no cambia.
- `preparar_escritor` dejo de escribir `live` en nombre de quien lo llama: sin
  modo no compila y un modo distinto revienta antes de construir nada.
- Nada cambia para las decisiones del motor ni para los precios y la pausa por
  producto: este bloque solo mueve el transporte.

## Para quien mantiene

- `app/ads/write.py` es la UNICA superficie que escribe en Amazon. Suma
  `crear_campana`, `crear_ad_group`, `crear_target_asin`, `cambiar_estado_campana`
  y `crear_keyword` (antes `crear_keyword_exacta`, ahora con `match_type` y
  `estado` validados). La allowlist suma tres pares `POST` y el contenedor de
  ad groups. No hay `request` generico ni cambio en el cliente de lectura.
- `app/ads/sp_wire.py` gana `ack_de` (ACK canonico `status/cuerpo/texto`) y
  `ack_de_error` (rechazo con status o incierto). El juicio por item sigue en
  `errores_de_ack`/`errores_put`/`id_de_ack`.
- `app/fabrica_motor.py` cambia su contexto: `_Ctx` lleva el escritor sellado y
  el lector, no `httpx` ni token. El juicio del ledger (`ack_ok`, `errores_207`,
  `id_creado`) se muda aqui desde `fabrica_plan`, que queda puro como pide
  `test_fabrica_plan_es_puro`, y delega en `sp_wire`.
- `app/ads/archivar_inertes.py` traduce el payload durable de C a
  `crear_keyword`/`borrar_keyword`; `app/ads/archivar.py` conserva
  `preparar_escritor` como unico constructor con perfil resuelto y ahora exige
  `modo_confirmado`.
- Sin migracion: D no agrega tablas, columnas ni indices.

## Por fila

| Fila | Comando de Acepta | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| D.1 | `D/escritores.tsv` + `rg -n -e '_client[.]request' -e 'def _post' app tools` | `9acd2646` | Inventario con 22 callers; 5 nombres `_post*` revisados por semantica y solo dos eran transporte propio | Cada caller declara metodo, ruta, perfil, modo, ledger, confirmacion y reversa | Es documental |
| D.2 | `N d` | `db5e5485` | 23 passed, salida 0 | Perfil, metodo o ruta ajenos se deniegan antes del HTTP; cada mutacion tiene intencion previa; un error por item no sella ok | `rollback.sh` con el `MOTOR_SHA` desplegado; sin migracion que deshacer |
| D.2 | `T writes test_11` | `db5e5485` | 1 passed, salida 0 | El contrato de C sigue intacto por el transporte nuevo | Igual que D.2 |
| D.3 | `N d` + suites de los duenos | `db5e5485` | 23 passed; fabrica 192, API 113, impulso 114, archivo 47, product ads 136, aplicador 168 | Fabrica parcial, retiro, reactivacion, sonda (operaciones del cliente) y reposicion/reversa verificadas por su dueno | Igual que D.2 |
| D.3 | `test_architecture.py` | `db5e5485` | 99 passed, 2 skipped, salida 0 | El mapa de importadores del cliente de escritura quedo con dos entradas nuevas y su razon; presupuesto de 900 lineas intacto | Igual que D.2 |

Comandos y salidas completos en [acepta.txt](acepta.txt) (partes 1 a 4 en
`acepta-1.txt` a `acepta-4.txt`), que genera [acepta.sh](acepta.sh). El SHA del
encabezado de cada parte es el arbol verificado; este archivo y los demas de
esta carpeta entran despues, en un commit que solo toca
`docs/evidencia/arreglos-05/D/`.

## D.1. Inventario de escritores

[escritores.tsv](escritores.tsv) lista cada caller con archivo, funcion,
metodo, ruta, perfil, modo, ledger o intento durable, confirmacion por item y
reversa. El contraste con el `rg` que pide la fila esta al pie del archivo:

- Cinco nombres `def _post*`: `fabrica_motor._post` y
  `archivar_inertes._post` eran transporte propio (migrados);
  `apply_harvest_hermanas._post_hermana`, `bloqueo_dueno_efecto._postea_y_relee`
  y `apply_harvest_reversa._post_delete_reversa` ya llamaban al cliente
  protegido y se conservan.
- `meli/client.py`, `ads/client.py` y `ads/descarga.py` son lectura.
- No queda ningun `.post(`/`.put(` de Amazon fuera de routers, meli, spapi,
  notifica y jev_juicios.
- `tools/` no muta Amazon: sus CLI despachan a `app`.
- No existe modulo sonda mutante de Amazon en `app/` ni en `tools/`; las sondas
  historicas corrieron por este mismo cliente y su evidencia vive en
  `docs/evidencia/bids-02` y en los comentarios sellados de `app/ads/write.py`.

## D.2. Cliente protegido y callers migrados

Caminos completos en [caminos.md](caminos.md). Resumen:

- **Allowlist**: `POST /sp/campaigns`, `POST /sp/adGroups` y `POST /sp/targets`
  entran con el vendor v3 ya sellado por las sondas del 2026-08-26 y
  2026-09-09; el contenedor `adGroups` se suma. `DELETE /sp/campaigns` y
  `PUT /sp/adGroups` siguen denegados.
- **Fabrica**: `mutar` construye UN escritor tras la ceremonia; `_ejecuta_paso`
  inserta `fabrica_lote_paso` con commit antes del HTTP, guarda ID+ACK antes
  del LIST y sella `applied`/`failed`. El cuerpo enviado es EXACTAMENTE el
  payload del ledger (probado paso por paso).
- **API, impulso y vigia**: `PausadorFabrica` y `api_fabrica` piden el escritor
  por plataforma; `desarmar_lote`, `reactiva_lote`, `pausar_lote` y
  `reanudar_lote` conservan su fila de ledger antes del PUT.
- **Archivo manual**: `_archivar` conserva `keyword_archivo_manual` y el
  readback `ARCHIVED`; `--reponer` y `--revertir-reposicion` conservan el
  protocolo de C y solo cambian de donde sale el `enviar`/`eliminar`.
- **Product ads**: el retiro, su regreso y la reactivacion ya usaban el cliente;
  lo unico que cambia es que el modo lo pasa el dueno.

## D.3. Verificacion por sus duenos

- Fabrica: `test_fabrica_campanas` 117 y `test_fabrica_plan` 75.
- API de fabrica: 113. Impulso y vigia: 114.
- Cliente de escritura, lectura y product ads: 136.
- Archivo manual de inertes: 47. Aplicador, cosecha y reversa: 168.
- API de escritura, dueno del efecto y retiro: 163. Reposicion de C: 20.
- Arquitectura: 99 (2 skips del propio candado).

## Rojo y verde

[rojo.txt](rojo.txt): la regresion del bloque contra el padre real
`7ef47542` (solo con `tests/test_arreglos05_d_write.py` copiado) da **13
failed, 6 passed**. Las aserciones visibles del impacto:

- `test_arreglos05_d_preparar_escritor_exige_el_modo_autorizado` falla con
  `Failed: DID NOT RAISE TypeError`: el padre escribe `live` en nombre del
  caller.
- `test_arreglos05_d_sin_transporte_mutante_propio` falla con
  `AssertionError: app.fabrica_motor` y `assert not True where True =
  hasattr(..., '_token_lwa')`: el padre conserva el transporte propio.
- Las operaciones nuevas (`crear_campana`, `crear_ad_group`, `crear_target_asin`,
  `cambiar_estado_campana`, `crear_keyword` con match/estado) no existen en el
  padre: `AttributeError`.
- Los 6 que pasan en el padre son los que describen el contrato que el bypass ya
  cumplia (cuerpos, 207 con error por item, 5xx, archivo manual); su valor es
  impedir que la migracion los rompa.

En el SHA de pruebas `f946d329`: `N d` 19 passed, `T writes test_11` 1 passed,
modelo de estados 181 passed, arquitectura 99 passed y 2 skipped.

## Ronda 1 de revision (P1 corregidos)

Dos P1 de Astra y dos hallazgos de Devin que comparten raiz con ellos; los
cuatro se corrigieron con su regresion en rojo contra `acaf7da7`
([rojo-ronda1.txt](rojo-ronda1.txt), 3 failed):

- **P1, un 5xx al pausar frenaba el resto.** `_put_estado_campana` levantaba
  ante 5xx o red, asi que `PausadorFabrica.pausa_huerfanas` moria antes de
  sellar el paso y antes de pausar las demas campanas ENABLED del lote: las
  otras seguian gastando y el paso quedaba sin `external_id`. Ahora devuelve
  siempre el ACK canonico; el paso se sella `failed` con su ID y su ACK
  incierto (la conciliacion lo lee) y el bucle sigue hasta informar el
  residuo. `_motivo_put_fallido` distingue INCERTO de rechazo.
  Regresion: `test_arreglos05_d_fabrica_pausa_huerfanas_sigue_tras_un_503`
  (dos campanas, 503 en la primera y pausa confirmada en la segunda) y el
  contrato de `test_r09r5_f05_put_timeout_declara_incierto`, que ahora lo
  declara el dueno del ledger.
- **P1, lote bloqueado sin haber escrito.** El token se pedia dentro de la
  primera mutacion, despues de registrar el lote: un rechazo de LWA dejaba un
  lote `failed` y la misma huella ya no se podia repetir. `mutar` ahora llama
  `autenticar()` (preflight sin mutacion) despues de la ceremonia y antes del
  `INSERT` del lote. Regresion:
  `test_arreglos05_d_fabrica_lwa_rechazado_no_registra_lote` (sin fila de
  lote; la misma orden pasa cuando LWA vuelve).
- **P1, sin token la reposicion abria intentos y seguia.** `AdsAuthError` no
  hereda de `AdsApiError`, asi que caia en el `except` generico: el intento
  quedaba incierto y la fase seguia abriendo intentos de filas que jamas
  salieron. Ahora el intento se sella `ok:no_aplicado` (cuenta cupo y admite
  reintento) y la fase se detiene antes de la fila siguiente, en reposicion y
  en reversa. Regresion:
  `test_arreglos05_d_reposicion_lwa_rechazado_detiene_la_fase` (dos filas y
  un rechazo de LWA: solo la primera abre intento).

Recibo rehecho sobre `db5e5485`: `N d` 23 y las cuatro partes en verde.

## Ronda 2 de revision (P1 corregido)

**P1, LWA caido en el camino del vigia.** `PausadorFabrica.pausa_huerfanas` no
pasa por el preflight de `mutar`. Si el primer token del escritor fallaba,
`AdsAuthError` (hermano, no subclase, de `AdsApiError`) escapaba de
`_put_estado_campana` **despues** del `INSERT` del paso: la fila quedaba
`planeado` sin `external_id` ni ACK y las demas campanas del lote no se
pausaban. Ahora ese fallo se reporta como `ACK_SIN_ENVIO` (nada salio, sin
ambiguedad), el paso se sella `failed` con su ID y el motivo de LWA, y la
siguiente campana se intenta con el token que ya funciona.
Regresion: `test_arreglos05_d_fabrica_pausa_huerfanas_sigue_sin_token` (dos
campanas, LWA que agota sus reintentos en la primera y token valido en la
segunda; rojo contra `0965b1ce` en [rojo-ronda2.txt](rojo-ronda2.txt)).

## Como se regresa

1. No hay migracion: el esquema no cambia, asi que no hay reversa de datos que
   correr. Los ledgers (`fabrica_lote_paso`, `keyword_archivo_manual`,
   `keyword_reposicion_intento`, `apply_attempt`) conservan su forma.
2. `MOTOR_SHA=<sha del merge> bash tools/despliegue/rollback.sh` restaura el
   codigo del respaldo `predeploy-*`, recrea el contenedor, comprueba `/health`
   y vuelve a fijar `despliegue/sha` al `SHA_PREVIO`. Con un `MOTOR_SHA` que no
   es el desplegado, el script sale 0 sin deshacer nada.
3. Los pasos que este bloque dejo a medias (una fila `planeado` de la fabrica o
   un intento abierto de C) se concilian con la version anterior del motor o
   con `--reconciliar`/`--reconciliar` de reposicion; el rollback no los borra.
4. La reversa de efectos (volver a crear o pausar algo en Amazon) es otra
   operacion y no la hace el rollback.

## Fallos previos de esta PC (no de este bloque)

Confirmados contra `7ef47542` con el mismo entorno y el mismo DSN:

- `test_impulso_io.py::test_r09r5_b1a_conexion_propia_lleva_credencial` exige
  password en el DSN del laboratorio y el PG desechable usa `trust`
  (`assert conn.info.password`). Falla igual en el padre.
- `test_ui_fabrica.py::test_flujo_js_*` (13 casos) revientan con
  `FileNotFoundError: [WinError 206] The filename or extension is too long` al
  lanzar node con el guion como argumento. Falla igual en el padre.
- `test_fabrica_f2_hermanas` (24) y `test_fabrica_f2_biblioteca` (16), ya
  documentados en el brief del bloque.
- `test_d3_adversary::test_revalidacion_harvest_respeta_target_del_goal`
  (2 parametrizaciones), `test_harvest_destino` (3), `test_notifica` (3),
  `test_orbit07_m1` (1) y
  `test_precio02_escenario_meli::test_ingesta_meli_sin_cliente_salta_meli_y_sigue`
  (1): rojos previos de la lista del brief.

Ningun otro fallo aparecio en las suites de los duenos de este bloque.

## Decisiones y limites

- **La ceremonia es el modo de la fabrica.** La fabrica crea campanas reales
  para goals en `shadow`: su autorizacion no es el modo del goal sino
  `--acepto-mutacion-real` + `--go` + `--huella` + `--esperado`. El escritor se
  construye despues de esa puerta y el cliente revalida el literal.
- **`_revierte_parcial` no tiene fila propia de ledger.** Es la compensacion de
  un lote `failed`, queda en `fabrica_lote.detalle` y ahora pasa por el guard
  del cliente. No se le agrego ledger para no cambiar su semantica best-effort.
- **El adaptador de reposicion ignora el `profile` que recibe.** El scope vive
  sellado en el escritor de esa plataforma y el mapa se indexa por la
  plataforma de la fila: no hay segundo scope que pueda ganar.
- **`crear_keyword` es la unica puerta de creacion de keywords.** El default
  `EXACT/ENABLED` conserva el harvest; la fabrica y la reposicion pasan sus
  valores y se validan antes del HTTP.
- **Sin migracion.** No se midio ningun invariante nuevo en produccion porque D
  no define datos nuevos.

## Seguimiento (P2)

Ninguno bloquea; requieren dos o mas fallos o una carrera.

1. El mapa `fabrica_plan.VENDOR_POR_PATH` sigue existiendo como vocabulario del
   plan; una sola prueba lo pinea contra el cliente. Se podria borrar cuando el
   plan deje de necesitar los paths.
2. La lectura de perfiles de `preparar_escritor` hace su propio GET por llamada;
   el retiro y el vigia podrian compartir el escritor por plataforma igual que
   la fabrica (hoy cada llamada construye uno).
3. `app/ads/archivar_inertes.py` construye un escritor por plataforma aceptada
   aunque el lote no la use; el token LWA solo se pide al primer request, pero
   el objeto se crea igual.
