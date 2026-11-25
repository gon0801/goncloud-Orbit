# Modelo del bloque E: la practica vencida no bloquea el trabajo real

Base de este bloque: `5223bfae1e85fee2d2d956962563ee030113e333` (master con BASE,
A, B, C y D integrados y desplegados). SHA de la rama `arreglos-05/e`.

## Donde vive la decision

La pregunta de E no es "que hago con el resultado de Amazon", sino "puedo
encolar una decision nueva para esta clave de efecto `(platform, ad_entity_id,
familia, search_term)`". Por eso NO la decide `app/apply_estados.py`: su
`TABLA_TRANSICIONES` modela un cambio ya enviado a Amazon (puja, corte,
reversa, cosecha) y su ciclo de vida frente a la API. E es una precondicion de
envio, no una transicion de un cambio. La tabla de estados no cambia, no suma
estados ni eventos, y sus 176 casillas siguen intactas.

La decision de E tiene tres duenos y todos son codigo o esquema, no un chequeo
previo suelto:

1. **Indice unico parcial de efectos live** (`apply_queue_live_efecto_unico`):
   como maximo UNA fila live no terminal por clave de efecto. Es la garantia
   de la base contra dos workers que quieran producir el mismo efecto live.
2. **Trigger de INSERT** (`apply_queue_clave_vigente`): bloquea una fila nueva
   cuando ya hay una fila live no terminal de la clave (cubre el INSERT shadow
   contra un live), una practica shadow no terminal VIGENTE
   (`vence_el > now()`, reloj de la base) o un VETO vigente (`estado='vetoed'`
   con `vence_el > now()`). Una practica shadow VENCIDA no bloquea. El trigger
   levanta `unique_violation`, el mismo error que el motor ya captura como
   `choque_clave`, y toma un candado asesor por clave (`hashtextextended`) que
   comparte con el endpoint de veto: el alta y el veto no se cruzan.
3. **SELECT de claves bloqueadas** (`app/apply_cola._SQL_CLAVES_BLOQUEADAS` y
   su espejo `app/productos_io._SQL_CLAVES_EN_VUELO`): al decidir el ciclo, una
   fila live no terminal siempre bloquea; una shadow solo bloquea mientras es
   vigente; un veto vigente bloquea en ambos modos.

La vigencia sale siempre del reloj de la base (`now()` sobre `TIMESTAMPTZ`), no
del proceso ni de la zona de la sesion. La espera de 48 h (`vence_el =
encolado_at + 48h`) y los triggers de transicion de 0002 no se tocan. Las
practicas no se promocionan a live, no se borran y no las descarta el motor; su
historial queda como estaba.

## Caminos nuevos y como los decide la tabla `apply_queue`

Para cada camino: estado(s) de la fila implicada, evento de la base, accion
unica que toma el esquema o el SELECT, y el papel del indice.

### 1. Decision nueva con una shadow vencida de la misma clave

- Estado de la practica: `modo='shadow'`, `estado='pending_veto'`,
  `vence_el <= now()` (vencida por reloj de la base).
- Evento: `INSERT` de la nueva decision live.
- Accion: **entra**. El trigger no la ve porque solo cuenta shadow
  `vence_el > now()`; el indice live no incluye filas shadow; el SELECT no la
  lista como bloqueada. La fila shadow conserva su fila e historial.
- Indice: `apply_queue_live_efecto_unico` solo cubre `modo='live'`; la shadow
  vencida queda fuera y no choca.

### 2. Decision nueva con una shadow vigente

- Estado de la practica: `modo='shadow'`, no terminal, `vence_el > now()`.
- Evento: `INSERT` de la nueva decision (live o shadow).
- Accion: **bloqueada** con `unique_violation`. La razon es visible en el
  resumen (`choque_clave` en ciclo live) y el SELECT la reporta como bloqueada.
- Indice: no interviene (es shadow); decide el trigger por vigencia. Al vencer
  pasa al camino 1 sin que nadie borre la fila.

### 3. Decision nueva con un live no terminal o un veto vigente

- Live: `modo='live'`, estado en `pending_veto|released|applying` (cualquier
  `vence_el`).
- Veto: `estado='vetoed'` con `vence_el > now()` (bloqueo durable del dueño).
- Evento: `INSERT` de la nueva decision.
- Accion: **bloqueada**. El live no terminal hace saltar el indice y el
  trigger; el veto vigente lo filtra el SELECT al decidir (el veto no se
  reinserta, protege la clave). Un veto vencido no bloquea y el motor
  re-propone con fila nueva.
- Indice: `apply_queue_live_efecto_unico` es el candado del live no terminal.

### 4. Dos workers a la vez sobre la misma clave live

- Estado inicial: sin fila no terminal de la clave.
- Evento: dos `INSERT` live concurrentes.
- Accion: **como maximo una fila live no terminal**. El indice unico parcial
  resuelve la carrera a nivel de base: el segundo `INSERT` recibe
  `unique_violation` y el motor lo trata como carrera/choque. Cero doble
  efecto live.
- Indice: `apply_queue_live_efecto_unico` (no el trigger) es la garantia
  concurrente; el trigger opera antes y da el mismo error en el caso
  secuencial.

### 5. Down con colisiones reales

- Estado: una clave con mas de una fila no terminal (p. ej. la shadow vencida
  del camino 1 y la live que entro despues).
- Evento: aplicar la reversa de 0098, que quiere restaurar el indice mixto.
- Accion: **rehusa** con `restrict_violation`, sin borrar filas y sin cambiar
  el esquema: el `DO` cuenta las claves que chocarian antes de cualquier
  `DROP`/`CREATE`, y aborta la transaccion. Si no hay colisiones, restaura el
  indice mixto de 0002 y deja la sonda de la migracion en `f`.
- Indice: el indice mixto de 0002 es el que se restauraria; la reversa no lo
  fuerza borrando historia.

### 6. Cambio de dia en medio del lote y reloj de la base en UTC

- Estado: la misma fila shadow, con la frontera de 48 h cruzada durante la
  corrida.
- Evento: `INSERT` y `SELECT` evaluados con `now()` de PostgreSQL sobre
  `TIMESTAMPTZ`.
- Accion: la vigencia se recalcula en cada sentencia con el reloj de la base;
  no depende de la hora del proceso, de la zona de la sesion ni de un valor
  cacheado. Un cambio de dia del proceso no reanima ni bloquea por si mismo.
- Indice: el indice no usa `now()` (una restriccion parcial no puede); la
  vigencia la aplica el trigger y el SELECT, ambos con el reloj de la base.

### 7. Decision calculada antes del veto que se encola despues

- Estado: el dueno ya veto la practica (fila `estado='vetoed'` con
  `vence_el > now()`), pero el ciclo calculo una decision live de la misma
  clave antes del veto y todavia no la inserto.
- Evento: `INSERT` del corte live.
- Accion: **bloqueada** con `unique_violation`; el motor la registra como
  `choque_clave` y no entra a la cola. El candado asesor por clave serializa el
  alta con el veto: si el INSERT gana la carrera, el veto espera y luego veta la
  fila insertada; si el veto gana, el INSERT ve el veto vigente y no entra.
- Indice: no interviene; deciden el trigger por veto vigente y el candado
  asesor compartido con `app/apply_veto.veta_efecto`.

## Invariantes del bloque

1. Una fila shadow vencida nunca bloquea ni el SELECT ni el INSERT de la
   decision real, y conserva su historial sin cambios.
2. Una fila shadow vigente sigue deduplicando propuestas shadow y sigue
   bloqueando una decision nueva hasta que vence.
3. Un efecto live no terminal y un veto vigente siguen bloqueando la clave.
4. Como maximo una fila live no terminal por clave, incluso con dos workers.
5. El down rehusa si restaurar el indice mixto chocaria y nunca borra filas.
6. La vigencia es del reloj de la base en UTC y no del proceso.
7. Un veto vigente bloquea tambien el alta de una decision ya calculada: el
   INSERT y el veto de la misma clave se serializan por candado asesor.
