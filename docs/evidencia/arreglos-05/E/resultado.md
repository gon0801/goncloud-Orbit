# Resultado del bloque E: la practica vencida no bloquea el trabajo real

- Base: `5223bfae1e85fee2d2d956962563ee030113e333` (master con BASE, A, B, C y D
  integrados y desplegados).
- Rama: `arreglos-05/e`.
- Cabeza de la implementacion: `64c29136a95c5be354d745c6199000a70b501499`.
- Contrato: `docs/evidencia/arreglos-05/DISENO.md` seccion "D-E. Escritor unico
  y practicas vencidas" (parte E) e "Integracion con TEMPORADA y migraciones".
- Aviso del bloque: una propuesta de practica vencida deja de congelar la
  keyword, conservando su historial.

## Que cambia para el dueno y para quien mantiene

Hasta 0002, el indice mixto `apply_queue_clave_efecto_en_vuelo` trataba igual
una fila `live` y una propuesta `shadow`: una practica que ya vencio su ventana
de 48 h seguia bloqueando la clave para siempre, porque la fila shadow no se
descarta ni se borra. La foto de produccion tenia 31 practicas shadow
`pending_veto`, las 31 vencidas, congelando sus claves.

El bloque separa los dos invariantes:

- **Unicidad de efectos live:** indice unico parcial
  `apply_queue_live_efecto_unico`, solo `modo='live'` no terminal. Como maximo
  un efecto live no terminal por clave, incluso con dos workers.
- **Deduplicacion de practicas shadow vigentes:** trigger
  `apply_queue_clave_vigente` en el INSERT. Una shadow no terminal deduplica
  mientras `vence_el > now()` (reloj de la base) y bloquea un `shadow` contra un
  efecto live de la misma clave. Una shadow vencida no bloquea y conserva su
  fila e historial; no se promociona a live ni la descarta el motor.

Para quien mantiene: el SELECT de claves bloqueadas
(`apply_cola._SQL_CLAVES_BLOQUEADAS` y su espejo en `productos_io`) ya no trata
como bloqueo a una shadow vencida. Los tres duenos de la decision son el indice
(live), el trigger (shadow vigente) y el SELECT (lectura del ciclo). La tabla de
estados de `apply_estados.py` NO cambia: la disponibilidad de una clave es una
precondicion de encolar, no un cambio enviado a Amazon. El modelo por camino
esta en `E/estados.md`.

## Filas E.1 y E.2

| Fila | Comando | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| E.1 | `uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N e` | `64c29136` | 13 passed | shadow vencida no bloquea SELECT ni INSERT; live/veto vigente protegidos; migracion 0098 y su reversa | `0098_reversa_arreglos05_indice_shadow.sql` restaura el indice mixto de 0002 si no hay colisiones; con colisiones rehusa sin borrar filas |
| E.2 | mismos `N e` mas vecinos: `tests/test_apply_cola.py`, `tests/test_apply_producto.py`, `tests/test_apply_harvest.py`, `tests/test_arreglos05_a_ciclo.py`, `tests/test_productos_io.py`, `tests/test_productos_pausa.py` | `64c29136` | verde (ver abajo) | mismo efecto con shadow vieja, vigente y live; dos workers con maximo un live no terminal; historial identico antes/despues | igual que E.1; el rollback de despliegue usa `tools/despliegue/rollback.sh` con el `MOTOR_SHA` desplegado |

## Ronda 1 de revision (Devin): el veto es del efecto

Devin reporto un P1 real: tras 0098 una practica vencida y un corte real de la
misma clave conviven, y el endpoint de veto solo cambiaba la fila seleccionada.
Vetar la practica daba confirmacion al dueno pero el corte real seguia su
camino y podia aplicarse.

Arreglo en `app/api_write.py` (el veto es del efecto, no de una fila):

- identifica la clave `(platform, ad_entity_id, search_term)` de la fila
  seleccionada (0002 garantiza que `search_term` determina la familia);
- comprueba primero que no haya un corte equivalente en `applying` (punto de no
  retorno): si lo hay, responde 409 y no toca nada;
- veta TODAS las filas vetables (`pending_veto`/`released`) de la clave en una
  sola sentencia, con el mismo `vence_el` y actor, y devuelve `filas_vetadas`;
- vuelve a comprobar `applying` tras el UPDATE para cerrar la carrera con el
  claim; si aparecio, revierte el veto entero (la dependency cierra sin commit).

Pruebas nuevas en `tests/test_api_write.py` (rojo contra el endpoint anterior en
`E/rojo.txt`): la practica vencida + corte real released → el veto deja ambas
`vetoed` y `libera_vencidos` no envia nada; con el equivalente `applying` → 409
y ninguna fila cambia.

## Foto de produccion (solo lectura)

Comando: `bash docs/evidencia/arreglos-05/E/lee_previa.sh > E/previa.txt`
(rol `orbit_read`, transaccion `read_only`, via `lee_prod.sh`). Salida en
`E/previa.txt` (2026-11-24, SHA del padre). Lo medido:

- `apply_queue`: 58 filas. `live`: 17 applied, 3 discarded, 2 vetoed (0 live no
  terminal). `shadow`: 31 `pending_veto`, 3 discarded, 2 vetoed.
- Practicas shadow: 31 no terminales, **31 vencidas** (`vence_el <= now()`), 0
  vigentes. Son las que hoy congelan sus claves.
- Claves con mas de una fila live no terminal: **0** (el indice live de 0098 se
  crea sin backfill).
- Claves con mas de una fila no terminal (lo que el down debe respetar):
  **0** hoy. El down rehusaria si apareciera alguna.
- Claves con practica vencida y efecto live no terminal: **0** hoy.

No se invento backfill: no hay datos que corregir para aplicar 0098.

## Rojo y verde

Rojo contra el padre real (sin 0098), en `E/rojo.txt`: 2 failed, 3 passed. Los
dos fallos son de comportamiento: la shadow vencida aparece en
`claves_bloqueadas` y su INSERT live choca el indice mixto
`apply_queue_clave_efecto_en_vuelo` (`UniqueViolation`). No hubo error de
fixture, skip ni cero casos.

Verde en la cabeza del bloque: 15 passed de `N e` (12 regresiones de los
modulos E, 2 del veto por clave y la prueba del selector del instalador).

## Acepta (salida completa en `E/acepta.txt`, partes en `E/acepta-1..3.txt`)

- Parte 1: `N e` = 15 passed; bloque A estados = 181 passed; arquitectura =
  99 passed, 2 skipped (la omision declarada de la regla 99:2).
- Parte 2: cola = 44 passed; producto y cosecha = 104 passed; ciclo A = 57
  passed; API de escritura y veto = 51 passed; productos = 43 passed.
- Parte 3: esquema y ledger = 117 passed; selector del instalador (0098) = 1
  passed; temporada = 128 passed; gitleaks de `5223bfae..HEAD` = sin fugas.
- Total: 0 comandos que no pasaron.

## Reversa

1. **Reversa de esquema:** `migrations/0098_reversa_arreglos05_indice_shadow.sql`
   cuenta las claves con mas de una fila no terminal. Si hay alguna, aborta con
   `restrict_violation` ANTES de tocar el esquema y no borra filas ni
   promociona practicas; se conserva 0098. Sin colisiones, retira el indice live
   y el trigger y restaura `apply_queue_clave_efecto_en_vuelo` de 0002; la sonda
   de 0098 vuelve a `f` y la ida es aplicable otra vez (probado en
   `test_arreglos05_e_migracion_reversa_restaura_el_mixto_y_la_ida_vuelve`).
2. **Reversa de despliegue:** `MOTOR_SHA=<sha desplegado> bash
   tools/despliegue/rollback.sh`. `rollback.sh` solo deshace lo anotado en
   `ultimo.env` para ese `MOTOR_SHA` y sale 0 sin deshacer con otro SHA. Antes
   de degradar, detener nuevas mutaciones y conciliar; la reversa de efectos es
   distinta de la reversa de despliegue.

## Archivos cambiados

- `migrations/0098_arreglos05_indice_shadow.sql` y
  `migrations/0098_reversa_arreglos05_indice_shadow.sql`.
- `app/apply_cola.py` (SELECT de claves bloqueadas), `app/productos_io.py`
  (espejo del mismo predicado) y `app/api_write.py` (el veto es del efecto).
- `tests/test_arreglos05_e_shadow.py`, `tests/test_arreglos05_e_migracion.py`,
  `tests/test_apply_cola.py` (suma 0098 a su cadena),
  `tests/test_api_write.py` (suma 0098 y las pruebas del veto por clave) y
  `tests/test_despliegue_motor.py` (selector de 0098).
- `docs/APPLY.md` (1.5, 2.2, 4.3, 9.2), `docs/DATABASE.md` y
  `docs/evidencia/arreglos-05/A/estados.md`.
- Evidencia en `docs/evidencia/arreglos-05/E/`.

## Riesgos y seguimiento (P2)

- La deduplicacion de practicas shadow es un trigger, no un indice: dos INSERT
  shadow concurrentes de la misma clave podrian crear dos filas shadow (no hay
  doble efecto live; el live sigue garantizado por el indice). El ciclo corre
  bajo su claim por plataforma, asi que la carrera es improbable. Se anota como
  P2, no bloquea.
- `now()` en el trigger es el instante de la transaccion; una transaccion muy
  larga puede ver como vigente una practica que vence durante ella. Es
  conservador (bloquea, no adelanta) y no afecta al cambio de dia.
- Se actualizo el espejo `productos_io._SQL_CLAVES_EN_VUELO` para no divergir de
  `apply_cola`; su prueba cubre shadow vencida, vigente e `ignora_practicas`.
- Carrera veto vs. decision nueva: si un ciclo inserta una decision live de la
  misma clave justo mientras el veto de la practica aun no ha comprometido, esa
  fila nueva no queda vetada. El `SELECT` de claves la habria saltado con el
  veto ya comprometido; para el instante intermedio el trigger no consulta
  vetos. Es una carrera de dos eventos (P2); cerrarla del todo exigiria que el
  trigger de INSERT tambien mirara vetos vigentes.

## Fallos previos de `master` en esta PC (no de este bloque)

`test_fabrica_f2_hermanas` x24, `test_fabrica_f2_biblioteca` x16,
`test_harvest_destino` x3, `test_d3_adversary` x2, `test_notifica` x3,
`test_orbit07_m1` x1,
`test_precio02_escenario_meli.py::test_ingesta_meli_sin_cliente_salta_meli_y_sigue`
x1. Ninguno se ejecuto en la aceptacion de este bloque salvo los que comparte
`test_apply_cola`/`ciclo`/`temporada`, que quedaron verdes.
