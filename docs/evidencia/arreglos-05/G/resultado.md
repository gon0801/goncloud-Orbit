# Resultado del bloque G: un termino reobservado no revierte todo el lote

- Base: `2825fb036be82d118acfeb4e53e07e490cdd4557` (master con BASE, A, B, C, D,
  E y F integrados y desplegados).
- Rama: `arreglos-05/g`.
- Modelo (antes del codigo): `90d9c645`.
- Cabeza de la implementacion: `d0b831ef8bfe4ba1dbd7ec9b2ca0027da7c49a0c`
  (arreglo de codigo + regresiones + rojo/verde).
- Evidencia: el commit que contiene este archivo.
- Acepta corrida en `d0b831ef` con arbol con seguimiento limpio.

- gitleaks de `2825fb03..d0b831ef` (commits del bloque): sin fugas.
- Contrato: `docs/evidencia/arreglos-05/DISENO.md`, seccion "F-G. Meta base y
  sello temporal" (parte G) e "Integracion con TEMPORADA y migraciones".
- Aviso del bloque: el ciclo conserva decisiones validas cuando recibe una
  observacion nueva de un termino maduro.
- Modelo por camino: `G/estados.md`.

## Que cambia para el dueno y para quien mantiene

`_pendiente_termino` aplicaba `_sello_bitemporal` solo dentro de
`if corte is not None`, es decir, solo a la rama `negative`. El `harvest` sin
corte copiaba `resultado.data_observed_at` (= `termino.observed_at_max`) crudo.
Cuando un termino maduro del lote se reobservaba con un `observed_at` posterior
al reloj de la decision, la fila del harvest quedaba con `data_observed_at` en el
futuro; `decision_dato_no_del_futuro` la rechazaba y, como las decisiones se
insertan juntas con `executemany` en la TX3, el `CheckViolation` revertia TODO el
lote mixto, incluidas las decisiones `pause`, `negative` y `bid` que ya eran
validas.

Para el dueno: una reobservacion de un termino maduro ya no tumba el lote del
dia; las decisiones validas se conservan y el harvest se decide con el mismo
dato maduro.

Para quien mantiene:

- **Un solo sello, aplicado a todo termino.** El sello
  `_sello_bitemporal(decided_at, data_observed, evidencia)` se mueve fuera del
  `if corte is not None` y corre siempre. Para `negative` el resultado es
  identico (evidencia no nula: mismo max y mismo clamp). Para `harvest`
  (evidencia nula) queda `min(decided_at, termino.observed_at_max)`.
- **El insumo real no se toca.** `inputs.termino.observed_at_max` conserva la
  fecha original, aunque sea posterior; las metricas siguen append-only y la
  ventana de madurez (`window_start`/`window_end`) y el trigger
  `decision_madurez_corte` no cambian.
- **Sin API paralela ni estado nuevo.** No se crea otro sello ni otra rama; G no
  toca `app/apply_estados.py` (el sello es un campo de la fila de `decision`, no
  una transicion aplicada).

## Fila G.1

| Fila | Comando | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| G.1 | `uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh T optimizer test_13` y `... comprobar.sh N g` | `d0b831ef` | `test_13`: 1 passed. `N g`: 4 passed. Lote mixto confirmado con `pause`/`negative`/`harvest`; `data_observed_at <= decided_at`; `observed_at` real intacto en inputs y metricas; ventana madura sin mover y trigger vigente; replay igual que el control | el sello bitemporal comun cubre harvest sin corte; una reobservacion de termino maduro no revierte el lote | no hay DDL que revertir; `tools/despliegue/rollback.sh` con el `MOTOR_SHA` desplegado devuelve el binario anterior, que solo difiere en la linea del sello (lee y escribe las mismas columnas) |

Casos de `N g` (`tests/test_arreglos05_g_sello.py`):

1. `test_arreglos05_g_lote_mixto_reobservado_se_confirma`: el lote mixto con un
   termino maduro reobservado termina `done`, con `pause`/`negative`/`harvest`
   persistidas, `decisions_count` igual al numero de filas y sello de TX3.
2. `test_arreglos05_g_sello_clampa_y_conserva_el_insumo`: toda decision cumple
   `data_observed_at <= decided_at`; el harvest clampa a `decided_at`;
   `inputs.termino.observed_at_max` guarda la fecha posterior real; la fila nueva
   de `search_term_observation` existe y las originales siguen ahi.
3. `test_arreglos05_g_ventana_madura_intacta_y_trigger_vigente`: todo corte tiene
   `window_end <= decided_at - 10 dias` y el trigger sigue rechazando un corte
   inmaduro (sqlstate 23514, sin persistir).
4. `test_arreglos05_g_replay_igual_y_decisiones_conservadas`: el replay reproduce
   `(kind, old_value, new_value)` en control y reobservado, y el lote reobservado
   decide exactamente lo mismo que el control.

## Rojo y verde

Rojo contra el padre real `2825fb03`, en `G/rojo.txt`: `T optimizer test_13`
= 1 failed (`status failed`, `constraint decision_dato_no_del_futuro`,
`persisted 0`); `N g` = 4 failed, cada uno terminando en la asercion nombrada
"el lote mixto aborto por decision_dato_no_del_futuro". No hubo error de import,
skip ni cero casos. El detalle del CHECK muestra la fila del harvest con
`data_observed_at 12:00:01 > decided_at 12:00:00`.

Verde en la cabeza `d0b831ef`, en `G/verde.txt`: `N g` = 4 passed;
`T optimizer test_13` = 1 passed.

## Acepta (salida completa en `G/acepta.txt`, partes en `G/acepta-1..3.txt`)

- Parte 1: `T optimizer test_13` = 1 passed; `N g` = 4 passed; bloque A estados
  = 181 passed; bloque A ciclo = 57 passed; arquitectura = 99 passed, 2 skipped
  (la omision declarada de la regla 99:2).
- Parte 2: ciclo completo = 54 passed; aplicador, cola, cosecha y producto = 276
  passed.
- Parte 3: temporada = 128 passed; optimizer = 254 passed; esquema y esquema del
  aplicador = 117 passed; bloques B a E = 94 passed; selector del instalador
  (G no agrega DDL) = 4 passed; gitleaks de `2825fb03..d0b831ef` = sin fugas.
- Vecinos extra de ciclo verificados aparte en `G/vecinos-ciclo.txt`:
  `test_cycle_apply`, `test_cycle_pause_cooldown` y `test_cycle_target_ciclo`
  = 49 passed (codigo identico a `d0b831ef`).
- Total: 0 comandos que no pasaron.

## Foto de produccion (solo lectura)

Comando: `bash docs/evidencia/arreglos-05/G/lee_previa.sh > G/previa.txt` (rol
`orbit_read`, transaccion `read_only`, por `docs/evidencia/arreglos-01/lee_prod.sh`;
2026-11-25T03:18Z, SHA `2825fb03`). Lo medido:

- Lotes: 179 live+done, 30 shadow+done y 2 off+skipped.
- 21 lotes mixtos (termino y entidad a la vez); 23 lotes con termino, 141 con
  entidad, 143 con decisiones.
- Decisiones: 2788 bid, 69 pause, 54 negative, 16 harvest.
- 70 decisiones (45244 pares) con una observacion del mismo termino posterior a
  `decided_at`; hoy todas son `negative`, protegidas por el sello de su rama.
- `harvest`: 16 en total, 0 con insumo posterior y 0 con sello en el futuro: el
  defecto era latente (la primera reobservacion en un lote harvest lo abortaria).
- `decision_dato_no_del_futuro`: 0 violaciones historicas; sin backfill.

Consecuencias del invariante: existe la forma del dato (reobservaciones y lotes
mixtos) y no hay deuda historica; no se reescribe ninguna fila.

## Migracion

G no crea migracion. El sello es codigo; `decision.data_observed_at` y el CHECK
`decision_dato_no_del_futuro` ya existen en `0001_initial.sql` (no se tocan ni
`0001` ni `0002`). El selector del instalador sigue en verde con las mismas
migraciones de ARREGLOS 05.

## Reversa

1. **Reversa de datos:** no hace falta. No se reescribe historia; el cambio solo
   clampea el valor calculado al insertar. Un binario anterior lee y escribe las
   mismas columnas.
2. **Reversa de despliegue:** `MOTOR_SHA=<sha desplegado> bash
   tools/despliegue/rollback.sh`. Solo deshace lo anotado en `ultimo.env` para ese
   `MOTOR_SHA` y sale 0 sin deshacer con otro SHA. Antes de degradar, detener
   nuevas mutaciones y conciliar.

## Archivos cambiados

- `app/cycle_serializacion.py`: `_pendiente_termino` aplica
  `_sello_bitemporal` fuera de `if corte is not None`, de modo que el sello
  bitemporal es comun a negative y harvest. Sin cambios de firma ni de tipos.
- `tests/test_arreglos05_g_sello.py`: las 4 regresiones de G.
- Evidencia en `docs/evidencia/arreglos-05/G/`.

No se toco `app/cycle.py` (techo del ratchet intacto), ni `app/apply_estados.py`,
ni `app/optimizer/*`, ni migraciones, ni `0001`/`0002`.

## Fallos previos de `master` en esta PC (no de este bloque)

Se confirmo `test_harvest_destino` x3 contra el arbol con la cabeza del bloque:

- `test_harvest_destino.py::test_a1_grupo_con_terna_null_postea_en_exacta_del_grupo`
- `test_harvest_destino.py::test_a1_terna_limpiada_tras_decidir_apply_y_replay_usan_congelado`
- `test_harvest_destino.py::test_a1_bid_congelado_viaja_al_post_tras_limpiar_terna`

Resultado: 3 failed, 29 passed, los mismos tres de la lista de rojos previos de
esta PC. Por eso `G/acepta.sh` no incluye `tests/test_harvest_destino.py`. Los
otros previos (`test_fabrica_f2_hermanas`, `test_fabrica_f2_biblioteca`,
`test_d3_adversary`, `test_notifica`, `test_orbit07_m1`, `test_precio02_...`) no
se ejecutan en el acepta del bloque por ser de otros modulos.

## Seguimiento (P2)

- El sello clamp no cambia `window_end`; la madurez sigue siendo del trigger. Si
  un lector futuro necesita distinguir "dato observado real" de "sello del lote",
  lo correcto es leer `inputs.termino.observed_at_max` (insumo) y
  `decision.data_observed_at` (sello), no reconstruirlo.
- El defecto era latente en produccion (0 harvest con insumo posterior): el
  primer lote harvest con un termino reobservado despues del despliegue es el que
  ejercita el camino nuevo en vivo; la evidencia de laboratorio lo cubre con PG16
  desechable.

## Fase 2: despliegue y recibo

- PR #765 integrado. `MOTOR_SHA=d0cdf1c110a965542239ae9bc1ae7f1e97d05d48`
  (master), `SHA_PREVIO=96eddb992295616d183f7fac1707574e6682a5ad`.
- Fecha: 2026-11-25 04:16-04:24 UTC.
- Entorno: `env -i PATH=/ucrt64/bin:/usr/bin:/bin HOME=$HOME MOTOR_SHA=<sha>`.

### Ensayo de solo lectura

`MOTOR_SHA=d0cdf1c1... bash tools/despliegue/desplegar.sh --ensayo` (ver
`despliegue.txt`):

- Guardas libres (`alias|ciclos running|harvest en cola|harvest jobs|app.cli` =
  `true|0|0|0|0`).
- Migraciones 0076-0098 aplicadas, **ninguna pendiente**: G no trae DDL y la
  `0098_arreglos05_indice_shadow.sql` sigue siendo la ultima.
- `claves: ninguna cambia`; bloque de cron igual al canonico;
  `refresh_estimacion.sh` ya es el de `d0cdf1c1`.
- `== ENSAYO OK`.

### Despliegue real

`MOTOR_SHA=d0cdf1c1... bash tools/despliegue/desplegar.sh`:

- Respaldo `predeploy-20261125-041646` completo.
- Migraciones 0076-0098 `ya aplicada` (ninguna se ejecuto; G sin migracion).
- `md5 OK: 304 archivos`; imagen construida aparte y contenedor recreado;
  `/health 200`; `despliegue/sha = d0cdf1c110a965542239ae9bc1ae7f1e97d05d48`.
- Cron igual al canonico; ninguna clave cambia.
- `== DESPLEGADO d0cdf1c1... (checklist.sh lo pasa a verificado)`.

### Checklist

`MOTOR_SHA=d0cdf1c1... bash tools/despliegue/checklist.sh` (ver `checklist.txt`):

- `== CHECKLIST VERDE d0cdf1c1...`: el SHA desplegado contiene `d0cdf1c1`; las
  migraciones 0076-0098 aplicadas (la ultima, `0098_indice_shadow`); bloque de
  cron canonico; `refresh_estimacion.sh` del SHA; `/health 200`.

### Prueba real sobre el SHA integrado

- `N g`: **4 passed** (6443 deselected).
- `T optimizer test_13`: **1 passed** (2 deselected).
- Huella (`huella-codigo.txt`): `md5_local = md5_servidor = md5_contenedor =
  4a43e4d24a6484c24c034b757b837fc0` del archivo que cambia G
  (`app/cycle_serializacion.py`); `sha_desplegado = d0cdf1c1`; el marcador
  "comun a TODO termino" aparece en el contenedor.

### Lectura RO de produccion (`previa-fase2.txt`)

Rol `orbit_read`, transaccion `read_only`, via `lee_prod.sh`; 2026-11-25T04:22Z:

- Esquema de ARREGLOS 05 (0094-0098) y del sello: las ocho comprobaciones en
  `True`; `decision_dato_no_del_futuro`, `decision.data_observed_at` y el trigger
  `decision_madurez_corte` presentes.
- 179 live+done, 30 shadow+done y 2 off+skipped; 21 lotes mixtos.
- `harvest`: 16 en total, **0 con insumo posterior**, **0 con sello en el futuro**,
  0 con sello invalido. Esta es la medida correcta del sello.
- El bloque `observaciones_ingeridas_posteriores` (45244 pares, 70 decisiones) es
  contexto: cuenta observaciones **ingeridas** despues de la decision, no insumos
  **leidos** con `observed_at > decided_at`; no prueba que el sello de `negative`
  protegiera algo (nota P2 (a) de la revision).
- `decision_dato_no_del_futuro`: 0 violaciones historicas.

### Reversa

G no agrega migracion, asi que no hay DDL que deshacer: la 0098 sigue siendo la
ultima. `MOTOR_SHA=d0cdf1c110a965542239ae9bc1ae7f1e97d05d48 bash
tools/despliegue/rollback.sh` deshace solo lo anotado en `ultimo.env` para ese
SHA (codigo; las claves y el cron no cambiaron) y sale 0 sin deshacer con otro
SHA. Antes de degradar, detener nuevas mutaciones y conciliar; la reversa de
efectos y la de despliegue son operaciones distintas.

### Seguimiento (P2) de la fase 2

- (a) El bloque de reobservaciones de `previa.sql`/`estados.md` es contexto de
  ingesta; la medida del sello es `harvest_insumo_posterior = 0` (ya anotado en el
  cuerpo del PR #765). Ajuste de redaccion, sin cambio de codigo.
- (b) `test_arreglos05_g_ventana_madura_intacta_y_trigger_vigente` podria afirmar
  ademas que el mensaje del trigger contiene `maduracion`, no solo el sqlstate
  `23514`. Mejora de prueba, fuera de este bloque.
- (c) La raiz de fondo seria una lectura as-of (`observed_at <= decided_at`) en
  `windows`; queda como diseno de seguimiento, fuera de este bloque.

### Recibo

`despliegue.txt`, `checklist.txt`, `prueba-real.txt`, `previa-fase2.txt` y
`huella-codigo.txt` en esta carpeta.
