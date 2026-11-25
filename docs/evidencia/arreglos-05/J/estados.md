# Modelo del bloque J: que desaparece y que queda como camino unico

- Base: `db9d2041c3250a145615b0249d8b809593c33a75` (master con BASE, A, B, C,
  D, E, F, G, H e I integrados).
- Rama: `arreglos-05/j`.
- Inventario medido: `J/modos.tsv` y `J/previa.txt` (solo lectura, rol
  `orbit_read`, 2026-11-25).
- Contrato: `docs/evidencia/arreglos-05/DISENO.md:243-248` y la decision 5 de
  `docs/evidencia/arreglos-02/DISENO.md:417-432`.

## Regla de decision

Un modo se retira solo si (a) su lector runtime existe, (b) la configuracion
viva no lo usa, (c) ningun ciclo historico lo uso como camino y (d) ninguna
decision historica lo necesita para replay o reversa. Si falta una condicion,
queda conservado y se dice. Nada se retira por grep=cero.

## 1. Politica de bids apagada (`politica is None`)

**Que la leia.** `_procesa_decisora` (`app/cycle_recorrido.py:347-428`) decidia
solo la pausa con `bid.decide_pause` y congelaba la fila con
`_pendiente_pause_apagada` (`app/cycle_serializacion.py:260-316`);
`_juicio_hoja` (`app/cycle_sombra.py:217-227`) repetia la rama para el juicio
hipotetico. `MOTIVO_POLITICA_APAGADA` contaba el salto.

**Por que se puede retirar.** La configuracion viva trae `niveles_v3` en MX y US
(config 63). La rama solo corria con la clave ausente o ilegible. El inventario
de decisiones da 36 pausas y 2723 bids sin `inputs.politica` (2026-08-24 a
2026-10-10): son historia, no camino vivo.

**Que camino desaparece y cual queda.** Desaparece la creacion de decisiones de
hoja por la era bandas cuando falta `ads_bid_politica_<plataforma>`. Queda un
solo camino vivo: `politica.decide(CasoHoja)` con `inputs.politica =
niveles_v3`. Con la clave ausente o ilegible la hoja no muta: se cuenta
`politica_apagada` y no se encola ninguna decision. Esto cumple la regla del
contrato: configuracion ausente o ilegible nunca se convierte en permiso live.

**Prueba de que la historia no depende de la rama.** El decoder historico vive
en `app/optimizer/eras.py` (`_decide_v1`, `decide_bid_era_bandas`) y en
`app/optimizer/replay.py`; no usa el writer retirado. La regresion
`test_arreglos05_j_replay_pausa_historica` reproduce una pausa real de
produccion (decision 2736, 2026-10-05, sin `inputs.politica`) y exige
`("pause", None, None)` antes y despues del retiro. El golden existente
`tests/test_cycle.py::test_replay_legacy_pause_sin_inputs_corte_usa_historicos`
sigue en verde.

## 2. Meta por producto en practica (rama shadow)

**Que la leia.** `_anota_sombra` (`app/cycle_sombra.py:367-458`) en su rama
`modo_meta == "shadow"`, `_juicio_hoja` y las tres funciones `decide_con`
armadas por hoja en `_procesa_decisora`.

**Por que se puede retirar.** La configuracion viva trae
`ads_producto_meta_amazon_mx/us = live` (config 63). Los 42 ciclos con
productos tienen `modo_meta = live`; ninguno tiene `shadow`. Las 2 versiones
historicas con `shadow` no llegaron a un ciclo con productos. La rama shadow no
decide dinero y no tiene uso actual.

**Que camino desaparece y cual queda.** Desaparece el calculo hipotetico de la
meta por producto y el modo `shadow` de la meta. Queda el camino live: la meta
por producto se resuelve con `_meta_viva` y se registra en `sombra_hojas` con
el veredicto real, como ya hacia la rama live. La pausa por producto en shadow
es otro interruptor y **se conserva** (41 decisiones `motor=producto`,
`modo=shadow`, 2026-11-08 a 2026-11-25).

**Historia.** Las evaluaciones viejas conservan su `sombra_hojas` en
`evaluacion_producto_ciclo.resultado`; la pantalla lo lee sin recalcular. El
retiro no reescribe ni borra esa columna. El replay de producto
(`tools/auto03_verifica replay`) usa `resolver_producto` sobre la entrada
congelada, no la rama shadow retirada.

## 3. Economia antigua `auto03`

**Que la lee.** `productos.economia_desde_settings`,
`productos.ancla_familia`, `productos.meta_de_hoja` y la revalidacion de
`apply_cola`/`economia_revalidacion_io`.

**Por que se conserva.** La configuracion viva usa `producto_directo_v1` en MX y
US, pero `auto03` es la reversa documentada de B-H1: el contrato de ARREGLOS 02
lo cita como la forma de deshacerse sin revertir el despliegue
(`docs/evidencia/arreglos-02/DISENO.md:427` y
`docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md:548`).
Ademas las versiones historicas lo usaban por ausencia (45/46 versiones sin la
clave) y la revalidacion de una decision vieja puede leerlo. Retirarlo quitaria
una reversa vigente, que el bloque debe conservar.

## 4. `ads_pause_sin_cooldown_bid`

**Que lo lee.** Solo `settings_lectura.pause_sin_cooldown_bid_desde_settings`,
re-exportado en `goals.py`. Cero llamadas en `app/`. La auditoria de
`docs/evidencia/arreglos-2026-11-12/ciclo.md:116` ya lo declara sin lector.

**Estado.** Retirable. Produccion lo trae `true` en 41 versiones, pero ninguna
decision lo congela ni depende de el. Se retiran el lector y su unica prueba.
La clave historica de `config_version` no se toca: la tabla es append-only y
dejarla no cambia ninguna decision.

## 5. Configuracion ausente o ilegible

- Ausente: `politica_bid_desde_settings` devuelve `None`. `_procesa_decisora`
  cuenta `politica_apagada` y retorna sin crear pendientes. No hay `live` por
  defecto.
- Ilegible: el mismo lector lanza `ValueError`; el error sube desde TX2, la
  transaccion se revierte y no queda ninguna decision. No se degrada a `live`
  ni a la era bandas.
- Producto meta ausente = `off`; producto pausa ausente = `off`. La pausa en
  live sigue exigiendo sus dos limites guardados.

## 6. Decision historica y replay

El replay sigue leyendo lo congelado y jamas recalcula. Los caminos de lectura
se conservan:

- `inputs.politica == "niveles_v3"` -> `politica.decide(CasoHoja.desde_json)`.
- `inputs.motor == "bid"` sin `politica` -> `decide_bid_era_bandas`
  (era bandas, incluida la pausa vieja).
- `inputs.motor == "hygiene"` -> `_replay_hygiene`.
- `inputs.motor == "temporada"` y `"producto"` -> sus ramas actuales.

Comandos de equivalencia antes/despues en la evidencia del bloque:
`tests/test_cycle.py -k "replay or era"`, `tests/test_eras.py` y
`N j`.

## 7. UI

- Ajustes ya ofrece `niveles_v3` y `apagado` para el motor de bids. Los dos
  siguen siendo validos: `niveles_v3` decide y `apagado` deja la hoja sin
  permiso de mutacion. No se agrega un modo nuevo.
- Productos deja de anunciar la meta en practica: se retira la frase
  "se calcula y no cambia ninguna puja" porque ese modo ya no existe.
- `productos-modo --meta` acepta solo `off` y `live`; la validacion de
  `config_write` rechaza `shadow` para meta. La pausa conserva `off`, `shadow`
  y `live`.
