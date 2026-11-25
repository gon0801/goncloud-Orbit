# Resultado del bloque F: la meta base avanza sin confundirse con temporada

- Base: `8a094228cd9704b35195000b83e37157527f887c` (master con BASE, A, B, C, D
  y E integrados y desplegados).
- Rama: `arreglos-05/f`.
- Cabeza de la implementacion: `142b1ba98c3e72e103922b3ce92012f78aed533e`
  (modelo en `4bf03729`; primera implementacion en `5eb549f4`; evidencia en
  `edd48fae`).
- Acepta corrida en `142b1ba98c3e72e103922b3ce92012f78aed533e` con arbol
  limpio. Los commits posteriores al acepta solo agregan
  evidencia y docstrings; el codigo es el mismo.
- gitleaks de `8a094228..HEAD`: sin fugas.
- Contrato: `docs/evidencia/arreglos-05/DISENO.md`, seccion "F-G. Meta base y
  sello temporal" (parte F) e "Integracion con TEMPORADA y migraciones".
- Aviso del bloque: la meta sigue acercandose a su destino cuando el libro esta
  vencido y conserva separada la meta de temporada.
- Modelo por camino: `F/estados.md` (escrito antes del primer commit de codigo).

## Que cambia para el dueno y para quien mantiene

Hasta E, el ancla de la meta base se reconocia por la procedencia de la
medicion: `notes.target.procedencia == 'margen_plataforma'`. Con el ledger
vencido el resolver se abstiene, la procedencia queda en `null` y ese ciclo
nunca puede ser el ancla del siguiente. La meta se congelaba en el ultimo
snapshot sano: 29, 29, 29, en vez de caminar 29, 28.5, 28. El hallazgo 12 lo
describe como "no se queda en 29,29,29 ni inventa margen fresco".

Para el dueno: con el libro vencido, la meta base sigue bajando medio punto por
ciclo hacia el destino, y la meta de alta de temporada (25 %) deja de poder
convertirse en la base del dia siguiente.

Para quien mantiene:

- **Evidencia durable.** El ancla se lee de los ciclos
  `motor='ads_optimizer' AND mode='live' AND decision_committed_at IS NOT NULL`
  (el sello de TX3 del bloque A), no de la procedencia ni del desenlace del
  apply. Un ciclo `degraded` con decisiones comprometidas es ancla; un ejecutor
  `resume_of` no lleva sello y no lo es.
- **Snapshot versionado.** `notes.target.version = "target_v2"` con dos
  subobjetos separados: `base = {valor, origen, cycle_id}` y
  `overlay = {version, modo, fase, estacion_dia, meta_alta_pct, aplica}`.
  `target_aplicado` sigue siendo el numero que entra a la cascada, asi que
  `api_common`, `cycle_notifica`, `apply_revalidacion`, `familias`, el tablero y
  el test externo `test_12` no cambian. `base.valor` nulo (peldano que no
  aplica, p. ej. `sin_fraccion`) queda visible pero no borra el ancla anterior:
  apagar la fraccion y reencenderla no hace saltar la meta.
- **Una lectura estacional por ciclo.** La configuracion de TEMPORADA se resuelve
  una vez en `cycle_target._overlay_temporada` y `cycle_recorrido` arma `plat_t`
  con ella (antes se leia dos veces). TEMPORADA conserva el dueno de sus
  umbrales, su fase y su prioridad: F solo la congela aparte.
- **Efectiva por hoja.** No se agrega un numero nuevo: la decision ya congela
  `target_acos_pct_usado` (base del juicio) y `caso.temporada.meta_alta_pct`
  (overlay), y `temporada.meta_del_juicio` deriva la efectiva en `decide`.

## Filas F.1 y F.2

| Fila | Comando | SHA | Resultado | Impacto | Reversa |
|---|---|---|---|---|---|
| F.1 | `uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh T optimizer test_12` y `... comprobar.sh N f` | `142b1ba9` | `test_12`: 1 passed. `N f`: 10 passed. Secuencia 29 -> 28.5 -> 28 tambien con overlay de alta 25; el overlay no es base; la practica no mueve el ancla live | ancla = ultimo ciclo live comprometido; snapshot con `base` y `overlay` separados; salida de alta retoma la base | no hay DDL que revertir; `tools/despliegue/rollback.sh` con el `MOTOR_SHA` desplegado devuelve el binario anterior, que lee `target_aplicado` y el JSON viejo sin problemas (claves aditivas) |
| F.2 | mismos comandos mas las pruebas de snapshot viejo, fraccion apagada, replay y recuperacion | `142b1ba9` | 10 passed (ver abajo el caso de cada una) | snapshot viejo sin base inequivoca conserva la semantica vieja y la ausencia explicita; una fase apagada no borra el ancla; replay con inputs congelados; lote nuevo tras recuperacion parcial ancla al origen | igual que F.1 |

Casos de `N f` (`tests/test_arreglos05_f_meta.py`):

1. `test_arreglos05_f_base_avanza_con_ledger_rancio`: 29 -> 28.5 -> 28, la hoja
   recibe la misma base que el peldano congelo.
2. `test_arreglos05_f_overlay_alta_no_se_vuelve_base`: overlay 25 separado; la
   siguiente base es 28.5, nunca 25; el caso congela base y `meta_alta_pct`.
3. `test_arreglos05_f_salida_de_alta_retoma_la_base`: al salir de alta
   `overlay.aplica` es falso y la base sigue su cadena.
4. `test_arreglos05_f_practica_no_cambia_el_ancla_live`: un ciclo shadow no es
   ancla del live siguiente.
5. `test_arreglos05_f_snapshot_viejo_sin_base_conserva_semantica`: v1 con
   `procedencia` nula no acredita base; manda el setting (28) y no el 27 del
   snapshot rancio.
6. `test_arreglos05_f_snapshot_viejo_con_base_inequivoca_es_ancla`: v1 con
   `procedencia=margen_plataforma` y `target_aplicado=29` si es ancla (28.5).
7. `test_arreglos05_f_fraccion_apagada_no_borra_el_ancla`: el ciclo
   `sin_fraccion` sella `base.valor` nulo y su hoja recibe el setting; al
   reencender, la meta camina desde la ultima base (28.5) y no salta al
   derivado.
8. `test_arreglos05_f_ledger_refrescado_tras_el_destino_camina_medio_punto`
   (revision de PR #763, Devin): llegar al destino deja `base.valor` nulo en ese
   ciclo; al recuperarse el ledger con un margen menor la meta camina 0.5 desde
   la ultima base explicita (27.5), no salta al derivado (20). Rojo propio en
   `F/rojo-devin.txt` contra `8bd59c9c`.
9. `test_arreglos05_f_replay_consume_su_version`: con la fraccion apagada, la
   meta de alta en 10 y la escalera en off, `reproduce` da el mismo veredicto; el
   freeze declara `target_v2`.
10. `test_arreglos05_f_recuperacion_parcial_retoma_la_base`: el ejecutor no decide
    ni se sella; el lote del dia siguiente ancla al origen (28.5, no 29).

## Foto de produccion (solo lectura)

Comando: `bash docs/evidencia/arreglos-05/F/lee_previa.sh > F/previa.txt`
(rol `orbit_read`, transaccion `read_only`, via `lee_prod.sh`). Salida en
`F/previa.txt` (2026-11-25T00:36Z, SHA del padre). Lo medido:

- 179 ciclos live+done, 30 shadow+done y 2 off+skipped.
- 175 snapshots con `notes.target`: **los 175** con
  `procedencia = margen_plataforma` y `target_aplicado` no nulo.
- `notes.target.version` en 0 snapshots: no hay versionado previo.
- Solo **2** ciclos live con `decision_committed_at`; el mas reciente es el 210,
  base `23.87387092262181318210778656`.
- Temporada en `live` en MX y US; noviembre es mes de `alta` y
  `ads_temporada_meta_alta_*` no esta configurada (25 % por defecto).

Consecuencias del invariante:

- El ancla del primer ciclo nuevo es el ciclo 210 (sellado): la base no salta.
  Los ciclos historicos sin sello pertenecen al binario anterior a 0094 y no son
  ancla (DISENO A: "no backfill por `status=done` a ciegas"); si no hubiera
  ninguno sellado, la inicializacion aprobada es el setting.
- La base de hoy (23.87) vive por debajo del overlay de alta (25). Antes del
  arreglo, el primer ciclo con ledger rancio habria congelado la base; ahora
  camina 23.87 -> 24.37 -> ... y el 25 queda solo como meta del juicio.
- No hay backfill: no se reescribio `notes` de ningun ciclo.

## Rojo y verde

Rojo contra el padre real, en `F/rojo.txt`: 9 failed. No hubo error de fixture,
skip ni cero casos. Los fallos son de comportamiento o del contrato nuevo:

- `test_arreglos05_f_base_avanza_con_ledger_rancio`: `[28.5, 28.5] !=
  [28.5, 28.0]` (la meta se congela).
- `test_arreglos05_f_recuperacion_parcial_retoma_la_base`: `29.0 != 28.5` (el
  ciclo `degraded` con TX3 comprometida no era ancla y manda el setting).
- Los otros siete: `KeyError` de `base`/`version`, el contrato versionado que
  aun no existia.

Verde en la cabeza: `N f` = 10 passed; `T optimizer test_12` = 1 passed.

La regresion que pidio Devin en el hilo del PR (#763) se demuestra en rojo
contra la cabeza `8bd59c9c`, donde `_base_previa` cortaba la busqueda con base
nula: `F/rojo-devin.txt`, 1 failed (`20` en vez de `27.5`). En la cabeza pasa.

## Acepta (salida completa en `F/acepta.txt`, partes en `F/acepta-1..3.txt`)

- Parte 1: `T optimizer test_12` = 1 passed; `N f` = 10 passed; bloque A estados
  = 181 passed; arquitectura = 99 passed, 2 skipped (la omision declarada de la
  regla 99:2).
- Parte 2: ciclo y target = 102 passed; aplicador (apply, cola, cosecha,
  producto, esquema de apply, API de escritura) = 291 passed.
- Parte 3: temporada = 128 passed; tablero y ciclo de apply = 133 passed;
  esquema = 91 passed; selector del instalador (0098, F no agrega DDL) = 4
  passed; gitleaks de `8a094228..HEAD` = sin fugas.
- Total: 0 comandos que no pasaron.

## Migracion

F no crea migracion. El versionado vive en el JSONB `optimizer_cycle.notes`, que
ya existe (columna TEXT con JSON); las claves nuevas son aditivas y los
snapshots historicos se leen con su semantica original sin reescribirse. No se
toca `0001` ni `0002` ni se abre un numero de migracion.

## Reversa

1. **Reversa de datos:** no hace falta. No se borra ni se reescribe historia: los
   snapshots viejos siguen ahi y el codigo nuevo los entiende. Un binario
   anterior lee `target_aplicado`, `procedencia` y las claves de medicion igual
   que antes e ignora `version`, `base` y `overlay`.
2. **Reversa de despliegue:** `MOTOR_SHA=<sha desplegado> bash
   tools/despliegue/rollback.sh`. Solo deshace lo anotado en `ultimo.env` para
   ese `MOTOR_SHA` y sale 0 sin deshacer con otro SHA. Antes de degradar,
   detener nuevas mutaciones y conciliar.

## Archivos cambiados

- `app/cycle_target.py`: `_SQL_NOTAS_PREVIAS` (ciclos previos done o con TX3
  comprometida; la base solo toma los live comprometidos);
  `_base_de_snapshot`, `_base_previa`, `_overlay_temporada`, `_BasePrevia`,
  `_OverlayTemporada`; snapshot `target_v2` con `base` y `overlay`.
- `app/cycle_recorrido.py`: `plat_t` se arma con el overlay congelado (una
  lectura estacional por ciclo).
- `tests/test_arreglos05_f_meta.py`: las 10 regresiones de F.
- Evidencia en `docs/evidencia/arreglos-05/F/`.

No se toco `app/cycle.py` (su techo del ratchet es 1148 y ya estaba en 1148), ni
`app/optimizer/replay.py`, ni la tabla de `app/apply_estados.py`, ni migraciones.

## Fallos previos de `master` en esta PC (no de este bloque)

`test_fabrica_f2_hermanas` x24, `test_fabrica_f2_biblioteca` x16,
`test_harvest_destino` x3, `test_d3_adversary` x2, `test_notifica` x3,
`test_orbit07_m1` x1,
`test_precio02_escenario_meli.py::test_ingesta_meli_sin_cliente_salta_meli_y_sigue`
x1.

`test_notifica` x3 se verifico contra el padre `8a094228` en un worktree aparte
(`test_avisar_campanas_envia_una_clase_y_devuelve_salio`,
`..._fallo_sigue_con_otras_clases`, `..._lectura_us_fallida_no_tumba_mx`):
`3 failed, 1 passed`, mismo fallo. Por eso el lanzador `F/acepta.sh` no incluye
`tests/test_notifica.py`.

## Seguimiento (P2)

- Los 173 ciclos live historicos sin sello no son ancla por diseno. Si algun dia
  el ciclo mas reciente de una plataforma quedara sin sello (binario anterior a
  0094), la base partiria del setting como inicializacion aprobada; no se
  backfillea `decision_committed_at` por `status=done`.
- `base.valor` es la base que ESTE ciclo entrega (puede ser `null` cuando el
  peldano no aplica) y `base.origen` dice de donde salio el ancla. Los lectores
  actuales no lo consumen; una pantalla que quiera mostrar la base debe leer
  `base.valor` y no `target_aplicado` cuando el peldano se abstiene.
- La meta efectiva por hoja sigue derivandose en `decide`; si un lector futuro
  necesita el numero efectivo debe leer `caso.temporada.meta_alta_pct` con
  `target_acos_pct_usado`, no reconstruirlo en el snapshot de ciclo.
