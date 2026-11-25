# Resultado del bloque H: 150 pujas ordinarias por ciclo

- Base: `c9d31d87fd8ad89fc0492df9bd15a5f68c6bc2f7` (master con BASE, A, B, C, D, E, F
  y G integrados y desplegados).
- Rama: `arreglos-05/h`.
- Cabeza de la implementacion: `e48a1075df56d7d92371adde0a16e7349fe607d0`
  (modelo y foto en `8716b563`).
- Acepta: partes 1 a 4 en `e48a1075` y parte 5 en `932eb679` (tras corregir el
  conteo de la receta), todas con arbol limpio; el codigo de H no cambia entre
  ambos. Salida completa en `acepta.txt`, partes en `acepta-1..5.txt`.

- gitleaks de `c9d31d87..HEAD`: sin fugas (dentro de `acepta-5.txt`).
- Contrato: `docs/evidencia/arreglos-05/DISENO.md`, seccion "H. Tope de 150
  pujas ordinarias" e "Integracion con TEMPORADA y migraciones".
- Aviso del bloque: cada ciclo puede intentar hasta 150 pujas normales; una
  recuperacion no vuelve a abrir el cupo.
- Modelo por camino: `H/estados.md` (escrito antes del primer commit de codigo).

## Que cambia para el dueno y para quien mantiene

Para el dueno: ningun ciclo puede mandar mas de 150 pujas ordinarias a Amazon.
Si el lote trae mas, las ultimas quedan sin enviar con su motivo visible
(`tope_ciclo`), el cupo diario deja de gastarse en ellas y los cortes y la
temporada no se frenan. La entrada y el regreso de temporada nunca compiten por
ese cupo, y una recuperacion del mismo dia no reabre otros 150.

Para quien mantiene:

- **Fuente unica.** `TOPE_PUJAS_ORDINARIAS_CICLO = 150` y
  `reserva_cupo_ciclo` en `app/apply_cuota.py`. El conteo es un SELECT sobre el
  ledger `apply_attempt` (`tipo='normal'`) unido a las decisiones `kind='bid'`
  por `decision.cycle_id` (el ORIGEN, no el ejecutor) y la plataforma. No hay
  contador nuevo ni tabla nueva.
- **Reserva e intencion en la misma transaccion.** En `Aplicador.aplica_bids`,
  `_reserva_del_ciclo` corre despues de la lectura fresca y antes del cupo
  diario, del ledger y del PUT: bloquea la fila del ciclo de origen
  (`SELECT … FOR UPDATE` con espera acotada), cuenta y, si hay lugar, deja que
  la fila del ledger (la intencion) se comprometa en el mismo `commit` que el
  aplicador hace antes del HTTP. El cerrojo serializa a dos procesos del mismo
  origen y el intento ya es visible para el segundo.
- **La decision sale de la tabla.** Evento `cupo_ciclo_agotado` y estado
  terminal `cupo_ciclo_agotado` en `app/apply_estados.py`; el clasificador
  `transicion_de_reserva_cupo` consulta la tabla y solo `ENVIAR` deja seguir.
  La tabla pasa a 204 casillas (12 estados por 17 eventos).
- **Consume lo intentado.** Cuentan el confirmado, el rechazado, el ambiguo
  (sin sello) y el de una caida antes o despues del HTTP. Las reversas
  (`tipo='reversa'`) no cuentan ni devuelven cupo.
- **Exencion por marcador congelado.** `puja_exenta_del_cupo` solo exime
  `inputs.motivo in (entra_temporada_baja, sale_temporada_baja)` con
  `inputs.temporada_episodio_id` presente. `ajusta_temporada_baja` y los
  `Regresar` ordinarios (Recupera) cuentan; sin marcador, la puja cuenta como
  ordinaria. El cupo diario y la prioridad de TEMPORADA siguen vigentes.
- **Motivo auditable y sin elusion.** La rechazada deja
  `decision_sin_aplicar.motivo = 'tope_ciclo'` con `detalle = {usados, tope}` y
  `cycle_id` del ejecutor en curso. Un ejecutor de recuperacion cuenta contra
  el origen y el plan del dia no abre un segundo origen con TX3 comprometida.

## Filas H.1 y H.2

| Fila | Comando | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| H.1 | `uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N h` | `e48a1075` | 21 passed (`prueba-real.txt`); 151 elegibles -> 150 intentos y la 151 con `tope_ciclo`; 149 + ambigua = 150; 149 permiten el 150; rechazado y ambiguo sellado cuentan | reserva e intencion en la misma TX bajo cerrojo del origen; conteo del ledger; reversas fuera | migracion `0099_reversa_arreglos05_tope_ciclo.sql` (aborta con desenlaces `tope_ciclo`) y `tools/despliegue/rollback.sh` con el `MOTOR_SHA` desplegado |
| H.2 | mismos comandos mas las pruebas de temporada, recuperacion, dos procesos y caidas | `e48a1075` | 21 passed (ver abajo el caso de cada una) | entrada/regreso exentos por marcador; recuperacion no concede otro cupo; dos procesos y caida pre/post HTTP nunca pasan de 150; cupo diario y prioridad TEMP intactos | igual que H.1 |

Casos de `N h`:

`tests/test_arreglos05_h_tope_ciclo.py` (14):

1. `test_arreglos05_h_151_ordinarias_intentan_150_y_la_151_lleva_el_motivo`:
   150 PUT, 150 filas del ledger, la 151 con `tope_ciclo`, `detalle` con
   `{usados: 150, tope: 150}` y sin abrir otro ciclo.
2. `test_arreglos05_h_149_confirmadas_mas_una_ambigua_agotan_el_cupo`: 149
   selladas + 1 sin sello = 150; cero HTTP para la siguiente.
3. `test_arreglos05_h_149_intentos_permiten_el_intento_150`: con 149 entra la
   numero 150 y su fila nace antes del PUT.
4. `test_arreglos05_h_intento_rechazado_y_ambiguo_sellado_tambien_cuentan`:
   `fallo http 400` y `fallo:readback_ambiguo` consumen igual que el `ok`.
5. `test_arreglos05_h_entrada_y_regreso_de_temporada_quedan_fuera_del_cupo`:
   con el cupo agotado, entrada y regreso se envian; la ordinaria del lote no.
6. `test_arreglos05_h_ajuste_y_recupera_ordinaria_cuentan`: ajuste a media baja
   y `Regresar` por desplome no se envian con el cupo agotado.
7. `test_arreglos05_h_recuperacion_no_concede_otro_cupo`: dos origenes, uno con
   150 (el ejecutor no envia y su desenlace lleva el `cycle_id` del ejecutor) y
   otro con 149 (el ejecutor completa el 150 contra el origen).
8. `test_arreglos05_h_reversas_no_consumen_cupo`: 149 normales + 3 reversas
   siguen dejando entrar la 150.
9. `test_arreglos05_h_dos_procesos_no_superan_150`: dos conexiones, dos
   aplicadores del mismo origen; un solo envio, un solo intento 150 y el otro
   con `tope_ciclo`.
10. `test_arreglos05_h_caida_antes_del_http_consume_cupo`: el intento queda
    abierto tras la caida, cuenta, y la siguiente puja no entra.
11. `test_arreglos05_h_caida_despues_del_http_tambien_consume_cupo`: con el PUT
    ya salido y la lectura de vuelta caida, el 150 cuenta y la siguiente no.
12. `test_arreglos05_h_cupo_diario_y_prioridad_de_temporada_siguen_vigentes`:
    con cupo diario 1 entra la entrada de TEMPORADA y la ordinaria queda en
    `fuera_de_cap` (no en el tope del ciclo).
13. `test_arreglos05_h_cupo_diario_apagado_tambien_frena_la_exenta`: con cap 0
    ni la entrada exenta se envia.
14. `test_arreglos05_h_fuente_unica_del_tope_y_del_motivo`: constantes,
    vocabulario y marcador de exencion.

`tests/test_arreglos05_h_estados.py` (3): estado y evento nuevos, 204 casillas,
ninguna casilla del cupo agotado envia y el clasificador consulta la tabla.
`tests/test_arreglos05_h_migracion.py` (4): sonda e ida, reversa sin datos y
re-ida, reversa que rehusa con desenlaces `tope_ciclo`, y roles reales
(`app_decide` registra, `app_read` no).

## Rojo y verde

Rojo contra el padre real `c9d31d87`, en un worktree temporal con las pruebas
finales copiadas (`H/rojo.txt`): **14 failed, 4 passed**. No hubo error de
fixture, skip ni cero casos. Los cuatro verdes son controles que ya pasaban en
el padre (149 permiten el 150, reversas no consumen, cupo diario con prioridad
de temporada, cap 0). Los fallos son de comportamiento o de contrato:

- `151 ... lleva_el_motivo`: `aplicadas=151` (no habia tope).
- `149 ... ambigua`: `aplicadas=1` y `[] == ['tope_ciclo']`.
- `entrada_y_regreso`: `3 == 2` (la ordinaria tambien se enviaba).
- `dos_procesos` y caidas: `DuenoDelClaimCambio` / intentos de mas.
- `fuente_unica`: `AttributeError` de las constantes nuevas.
- migracion: falta `0099_...` y `CheckViolation` al insertar `tope_ciclo`.

Verde en la cabeza `e48a1075`: `N h` = 21 passed; modelo completo = 209 passed.

## Conteo de tablas de la receta de arranque

Al correr los vecinos aparecio `tests/test_schema_docs.py` en rojo (`97 == 98`).
Se clasifico antes de tocar nada:

- `H/conteo-tablas.txt` (script `H/cuenta_tablas.py`, PG desechable): con todas
  las migraciones hay **98 tablas BASE y 27 vistas**; **sin 0099 tambien hay
  98** y sin 0097 ni 0099 hay 97. `0099` no trae `CREATE TABLE`; `0097`
  (bloque C) crea `keyword_reposicion_intento`. Es decir, el conteo de la
  receta quedo viejo con el bloque C, no con H.
- Contra el padre real `c9d31d87`, el mismo test ya fallaba (`97 == 98`); el
  registro de esa corrida se reemplazo por la evidencia de la correccion.

Aun asi el test es vecino de este bloque y estaba rojo, asi que se corrigio el
numero en `verify/Launch.md` y `verify/Doctor.md` (97 -> 98; el comentario de
Doctor pasa de 124 a 125 vistas incluidas, 98 tablas + 27 vistas) y se corrio
en verde: `H/schema-docs.txt`, 1 passed. No cambia ningun comportamiento: es la
receta de arranque la que contaba una tabla de menos desde `0097`.

## Foto de produccion (solo lectura)

Comando: `bash docs/evidencia/arreglos-05/H/lee_previa.sh > H/previa.txt`
(rol `orbit_read`, transaccion `read_only`, via `lee_prod.sh`). Salida en
`H/previa.txt` (2026-11-25 04:34 UTC, SHA del padre). Lo medido:

- 211 ciclos: 179 live+done, 30 shadow+done y 2 off+skipped.
- 100 ciclos con intentos bid normales; 503 intentos; **maximo 45 por ciclo**
  (mediana 2, p90 10) y cero ciclos por encima de 50: hoy ningun ciclo se
  acerca a 150.
- 0 intentos ambiguos (ni sin sello ni `fallo:readback_ambiguo`).
- 0 decisiones de entrada/regreso de temporada con intento (ni ajustes): la
  exencion no cambia nada hoy.
- 11 reversas en decisiones bid (no cuentan).
- Motivos de `decision_sin_aplicar` en uso: `modo_no_live` (190) y
  `fuera_de_cap` (3); ningun `tope_ciclo` previo.
- El CHECK vivo es el de 0094 (15 motivos); `app_decide` puede INSERT en el
  ledger, SELECT en el ciclo y UPDATE de `decision_committed_at`.

Consecuencias: no hay backfill ni reescritura de historia; la migracion 0099
solo amplia el vocabulario. El primer ciclo que supere 150 dejaria filas
`tope_ciclo`; hasta hoy no hay ninguna.

## Acepta (salida completa en `acepta.txt`, partes en `acepta-1..5.txt`)

- Parte 1: `N h` = 21 passed; modelo de estados = 209 passed; bloque A ciclo =
  57 passed; arquitectura = 99 passed, 2 skipped (la omision declarada 99:2).
- Parte 2: aplicador de bids = 65 passed; cola y producto = 75 passed.
- Parte 3: cosecha, reversas y reposicion = 162 passed; cliente protegido,
  practica vencida, API y bloqueo del dueno = 123 passed.
- Parte 4: ciclo y temporada A-E = 155 passed; temporada F-I, meta, sello,
  target y cooldown = 90 passed.
- Parte 5: esquema y ledger = 117 passed; receta de arranque
  (`test_schema_docs`) = 1 passed; selector del instalador = 4 passed;
  gitleaks de `c9d31d87..HEAD` = sin fugas.
- Total: 14 comandos, 0 que no pasaron.

## Migracion

`migrations/0099_arreglos05_tope_ciclo.sql` (numero libre en master; la ultima
era `0098`). Es aditiva: solo re-crea el CHECK nombrado
`decision_sin_aplicar_motivo_check` con `tope_ciclo` al final, deja comentarios
y un DO que verifica que el CHECK conserva los motivos previos y que
`app_decide` puede insertar. Cabecera `-- sonda-aplicada: <SELECT>` en la
primera linea, sin BEGIN/COMMIT (el instalador la envuelve). No hay tabla,
columna, indice ni grant nuevos: la fuente del cupo es el ledger existente. No
se toca `0001` ni `0002`.

Reversa `0099_reversa_arreglos05_tope_ciclo.sql`: aborta con
`restrict_violation` si ya hay desenlaces `tope_ciclo` (no reescribe historia) y
restaura la lista de 0094. Pruebas de ida, reversa, re-ida y roles reales en
`tests/test_arreglos05_h_migracion.py`; el selector del instalador la descubre
solo (`git ls-tree migrations/`) y el lote `-k arreglos05` sigue en 4 passed.

## Reversa

1. **Reversa de datos:** no hace falta; no se reescribe nada.
2. **Reversa de migracion:** `0099_reversa_arreglos05_tope_ciclo.sql` (arriba).
   Antes de revertir, el codigo nuevo debe dejar de escribir `tope_ciclo`; si
   hay desenlaces, la reversa rehusa y se resuelven con el dueno.
3. **Reversa de despliegue:** `MOTOR_SHA=<sha desplegado> bash
   tools/despliegue/rollback.sh`. Solo deshace lo anotado en `ultimo.env` para
   ese `MOTOR_SHA` y sale 0 sin deshacer con otro SHA. El binario anterior a H
   no conoce `tope_ciclo`: contra el esquema con 0099 sigue corriendo (solo
   enviaria mas de 150 hasta que se revierta el codigo).

## Archivos cambiados

- `app/apply_cuota.py`: `TOPE_PUJAS_ORDINARIAS_CICLO`, `MOTIVO_TOPE_CICLO`,
  `MOTIVOS_TEMPORADA_EXENTOS`, `ReservaCiclo`, `CupoDelCicloNoReservable`,
  `puja_exenta_del_cupo`, `reserva_cupo_ciclo`.
- `app/apply.py`: `_reserva_del_ciclo`, `_descarta`; la reserva antes del cupo
  diario y del ledger; commit del desenlace cuando una puja no se envia tras
  tomar el cerrojo.
- `app/apply_estados.py`: estado y evento `cupo_ciclo_agotado`,
  `transicion_de_reserva_cupo`, casillas de la tabla.
- `migrations/0099_arreglos05_tope_ciclo.sql` y su reversa.
- `tests/test_arreglos05_h_tope_ciclo.py`, `tests/test_arreglos05_h_estados.py`,
  `tests/test_arreglos05_h_migracion.py`; el conteo de casillas en
  `tests/test_arreglos05_a_estados.py`; la cadena de `_db_temporal_d1` en
  `tests/test_apply_schema.py`.
- `docs/APPLY.md` (9.2 y nueva 9.3), `docs/DATABASE.md`,
  `docs/evidencia/arreglos-05/A/estados.md` (ampliacion H).
- `verify/Launch.md` y `verify/Doctor.md`: el conteo estricto de tablas BASE
  pasa de 97 a 98 (venia viejo desde `0097`; 0099 no crea tablas) y el
  comentario de Doctor del conteo sin `table_type` pasa de 124 a 125.
- Evidencia en `docs/evidencia/arreglos-05/H/`.

## Fallos previos de `master` en esta PC (no de este bloque)

`test_despliegue_motor.py::test_a_despliegue_completo_y_checklist_verde`
corrido en el padre real `c9d31d87` con el mismo entorno y DSN
(`H/previos-despliegue.txt`, pytest sale 1): falla igual con
`cp: cannot stat 'app'` dentro del directorio temporal del servidor simulado,
en el paso (3) respaldo de `despliegue/desplegar.sh`. No depende del bloque H:
el resto del archivo falla por la misma causa en esta PC y el selector
`-k arreglos05` pasa. Tambien son previos y ya conocidos de la PC:
`test_fabrica_f2_hermanas` x24, `test_fabrica_f2_biblioteca` x16,
`test_harvest_destino` x3, `test_d3_adversary` x2, `test_notifica` x3,
`test_orbit07_m1` x1 y
`test_precio02_escenario_meli.py::test_ingesta_meli_sin_cliente_salta_meli_y_sigue` x1.

## Seguimiento (P2)

- El cerrojo del origen se toma con `lock_timeout` de 5 s: si una conexion
  queda con la transaccion abierta reteniendola, el lote de pujas de esa
  corrida falla cerrado (`CupoDelCicloNoReservable` -> `apply_error`) y el
  horario siguiente lo reintenta. No hay camino que envíe sin contar.
- Los ciclos historicos anteriores a H siguen con su conteo real (maximo 45);
  no se reclasifica nada ni se escribe `tope_ciclo` retroactivo.
- La pantalla de Salud no muestra el motivo `tope_ciclo` todavia; el dato vive
  en `decision_sin_aplicar` y lo puede pintar un bloque que toque Salud.

## Fase 2: despliegue y recibo

- PR #767 integrado. `MOTOR_SHA=889fe5cac5a54178eff8f0562f6e418ecf6d4ef8`
  (merge de master) y `SHA_PREVIO=d0cdf1c110a965542239ae9bc1ae7f1e97d05d48`.
- Fecha del despliegue: 2026-11-25 06:08-06:20 UTC.
- Ensayo de solo lectura: guardas libres (`true|0|0|0|0`), 24 migraciones
  aplicadas y **0099 pendiente**, `claves: ninguna cambia`, bloque Orbit igual
  al canonico, `== ENSAYO OK`.
- **Primer intento real: fallo del entorno local, no del servidor.** Salio 1 en
  el paso (5) con `xargs: environment is too large for exec` (el md5 del paso 5
  corre en el Git Bash local y el entorno de esta sesion excede el limite de
  exec). Estado remoto tras ese intento, leido antes de seguir: diario
  `MOTOR_SHA=889fe5ca ... CODIGO=predeploy-20261125-060839
  MIGRACION=0099_arreglos05_tope_ciclo.sql ESTADO=guardas`,
  `despliegue/sha = d0cdf1c1` (el codigo no se copio), `orbit-app-1` arriba,
  `/health` (8010) 200 y sonda de 0099 `True`.
- **Reanudacion de la MISMA corrida** con el entorno limpio que usan los
  recibos de C y F (`env -i PATH=/ucrt64/bin:/usr/bin:/bin HOME=$HOME`):
  `reanudo la corrida de 889fe5ca (ESTADO=guardas)`, 0099 ya aplicada,
  `md5 OK: 304 archivos`, contenedor recreado, `/health 200`,
  `despliegue/sha = 889fe5ca`, cron canonico, `ninguna clave cambia` y
  `== DESPLEGADO 889fe5cac5a54178eff8f0562f6e418ecf6d4ef8`.
- Checklist: **VERDE** `889fe5ca`; la linea
  `OK migracion 0099_arreglos05_tope_ciclo.sql aplicada` esta en
  `checklist.txt`, con bloque Orbit canonico, `refresh_estimacion.sh` del SHA
  y `/health 200`. El diario remoto termina en `ESTADO=verificado`.
- `N h` sobre el SHA integrado: **21 passed** (`prueba-real.txt`, que tambien
  trae los vecinos: 357 passed en modelo de estados, bloque A ciclo, aplicador
  y esquema de apply). La corrida de la fase 1 queda en
  `prueba-real-fase1.txt`.
- Lectura RO de produccion (`previa-fase2.txt`, rol `orbit_read`, `read_only`):
  sonda de 0099 `True`; el CHECK vivo lista los 16 motivos con `tope_ciclo` al
  final; **98 tablas BASE y 27 vistas**; 0 desenlaces `tope_ciclo` (el primer
  ciclo despues del despliegue aun no corrio); 100 ciclos con intentos bid,
  maximo 45 por ciclo (p90 10) y ninguno sobre 150; 179 live+done, 30
  shadow+done y 2 off+skipped; `app_decide` puede INSERT el desenlace y
  `app_read` no, con SELECT.
- **Recibo de esquema y huella (`esquema-ro.txt`).** Consulta propia con
  `lee_prod.sh` (mas `lee_esquema_ro.sh` y `esquema-ro.sql`): la 0099 queda
  como CHECK `decision_sin_aplicar_motivo_check` validado (`convalidated=True`)
  con los 16 motivos y `tope_ciclo` al final; el conteo del esquema publico es
  **98 tablas y 27 vistas** por `pg_class` (y 97 por `information_schema`
  bajo `orbit_read`, que oculta la tabla privada de 0087); la historia no se
  toco: 211 ciclos, **503** intentos bid normales, **11** reversas bid y **193**
  desenlaces `sin_aplicar`, identicos a la foto previa (`previa.txt`), **cero**
  filas nuevas en ciclos, decisiones, intentos, desenlaces y aplicaciones desde
  el inicio del despliegue (06:08 UTC), maximos de id y de fecha anteriores a
  ese momento, y `app_decide` sin DELETE/UPDATE sobre las tablas de historia.
  La huella del codigo desplegado va en el mismo archivo: `despliegue/sha =
  889fe5ca`, `/health` 200, md5 identico de `app/apply.py`,
  `app/apply_cuota.py` y `app/apply_estados.py` entre servidor y arbol local, y
  los simbolos de H dentro del contenedor (`tope=150`, `motivo=tope_ciclo`,
  evento/estado `cupo_ciclo_agotado`, clasificador y helper de exencion).
- **Conteo y privilegios.** `information_schema.tables` bajo `orbit_read`
  devuelve 97 porque la tabla privada de 0087
  (`arreglos03_devoluciones_meli_ids`) no tiene privilegios para ese rol;
  contada por `pg_class` da 98, igual que el esquema canonico
  (`tablas-produccion-vs-canonico.txt`: 0 diferencias). El 98 corregido en
  Launch/Doctor es el conteo real; 0099 no suma tablas, suma el estado del
  CHECK.
- Huella del codigo (`huella-codigo.txt`): `despliegue/sha = 889fe5ca`;
  md5 de `app/apply.py`, `app/apply_cuota.py` y `app/apply_estados.py`
  identico entre servidor y arbol local; dentro del contenedor
  `TOPE_PUJAS_ORDINARIAS_CICLO=150`, `MOTIVO_TOPE_CICLO=tope_ciclo`,
  `EventoCambio.CUPO_CICLO_AGOTADO=cupo_ciclo_agotado`,
  `EstadoCambio.CUPO_CICLO_AGOTADO=cupo_ciclo_agotado` y los helpers
  `transicion_de_reserva_cupo`/`puja_exenta_del_cupo` presentes.
- Reversa: `MOTOR_SHA=889fe5cac5a54178eff8f0562f6e418ecf6d4ef8 bash
  tools/despliegue/rollback.sh` deshace lo anotado en `ultimo.env` para ese SHA
  (la 0099 y el codigo; las claves y el cron no cambiaron) y sale 0 sin deshacer
  con otro SHA. La reversa de 0099 solo restaura el CHECK: **no borra intentos
  ni ciclos**, y aborta con `restrict_violation` si ya hay desenlaces
  `tope_ciclo` para no reescribir historia.
- Recibo completo: `despliegue.txt`, `checklist.txt`, `prueba-real.txt`,
  `prueba-real-fase1.txt`, `previa-fase2.txt`, `previa-fase2.sql`,
  `lee_previa_fase2.sh`, `esquema-ro.txt`, `esquema-ro.sql`,
  `lee_esquema_ro.sh`, `tablas-produccion-vs-canonico.txt` y
  `huella-codigo.txt`.
- Higiene de la evidencia: `sanea_evidencia.py` deja las rutas como
  `<checkout>`/`<venv>`, el usuario como `<usuario>` y el host como `<host>`;
  despues de pasar por el, en `H/` no queda ninguna ruta local ni nombre de
  usuario (solo el propio script contiene los patrones que sustituye).
