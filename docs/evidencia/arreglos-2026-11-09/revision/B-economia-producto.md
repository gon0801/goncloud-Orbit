# B. Economía directa de producto (M2): resultado

Copia revisada: `orbit07-wt` en `3c841f4` (master). Ningún commit posterior a `7cf7c9a`
toca `productos.py`, `goals.py`, `politica.py`, `productos_io.py`,
`economia_revalidacion_io.py`, `cycle.py` ni `apply_cola.py`.

Sondas (puras, sin base), con su salida guardada al lado:
- `sondas/B_meta_directa.py` → `B_meta_directa.txt`
- `sondas/B_respaldo_familia.py` → `B_respaldo_familia.txt`
- `sondas/B_veredicto_respaldo.py` → `B_veredicto_respaldo.txt`

Se corren con:
`cd orbit07-wt && PYTHONPATH=. /Users/dn/dev/goncloud-Orbit/.venv/bin/python ../orbit07-review/sondas/<sonda>.py`

## Hallazgos confirmados

### H1. Al perder elegibilidad, la keyword salta a la economía de la plataforma y tarda semanas en llegar a la de su familia
- Severidad: MEDIO
- Frente: dinero
- Dónde:
  - `app/cycle.py:3136-3141`: con `producto_directo_v1`, si el último target de la hoja fue
    `margen_producto`, el ancla de la familia pasa a ser `familias.destino` (plataforma o
    setting), no el target vigente de la hoja.
  - `app/optimizer/goals.py:996-1000`: desde esa ancla, la familia solo puede bajar 0.5 por ciclo.
  - `app/optimizer/productos.py:656-660`: en M2 el peldaño de producto devuelve `aplicado=None`
    en el mismo ciclo. Ya no converge como en AUTO-03.
  - `app/cycle.py:2143-2149`: R3 deja de usar el punto de equilibrio del producto y vuelve al de
    plataforma. Si el ledger está rancio, no usa ninguno (`cycle.py:2975` deja
    `margen_neto_pct=None`).
  - La cola copia la misma regla en `app/economia_revalidacion_io.py:151-152`.
- Escenario A (el producto pierde elegibilidad por un motivo propio):
  - Datos: producto con margen 8 % (meta 4 %), familia con margen 24 % (target 12 %),
    plataforma en 25.5 %, setting 30 %. La keyword venía en 4 % con procedencia
    `margen_producto`.
  - Disparo: el producto pierde elegibilidad por un motivo suyo, por ejemplo una venta atribuida
    de otro SKU (`halo_sin_margen`), un día con métrica ausente en el halo
    (`ads_con_metrica_ausente`) o una línea de venta sin costo (`cobertura_baja`).
  - Qué pasa: M2 resuelve la familia "fresca" anclada en 25.5. El target salta de 4 % a 25.0 % y
    luego baja 0.5 por ciclo. Tarda 26 ciclos (26 días, con un ciclo diario por mercado) en
    llegar al 12 % de la familia.
  - En AUTO-03 el mismo caso subía 4 → 4.5 → 5 … y llegaba a 12 sin pasarse nunca.
  - Efecto en la puja, con la misma keyword (ACoS 15 %, 20 pedidos):
    - con el 12 % de su familia: −12 % (`grupo_sangra_vendedora`);
    - con el 25.0 % de M2: +15 % (`bajo_target`).

  Mientras dure el exceso, R2 y R15 permiten hasta una subida de +15 % por semana, con 20 clics
  nuevos, en keywords de un producto que su propia familia diría que pierde dinero.
- Escenario B (falla de ingesta común a todos):
  - Disparo: la ingesta contable no corre en 4 días (`ledger_rancio`).
  - Qué pasa: producto, familia y plataforma comparten la misma frescura. Todas las keywords con
    meta de producto pasan en un ciclo de 4 % a 25.5 % (el valor de plataforma que converge al
    setting), con R3 apagado.
  - Misma keyword vendedora con ACoS 10 %:
    - con el 4 % de su producto: −25 % (`pierde_dinero_fuerte`);
    - con M2 tras la falla: +15 % (`bajo_target`);
    - con AUTO-03 en la misma falla: 4.5 % y −12 %.
  - Al volver el ledger, el target cae de golpe a 4 %.
- Escenario C (apagar la meta sin cambiar la versión): con `ads_producto_meta_<plat>` en `off`
  o `shadow` y la versión todavía en `producto_directo_v1`, pasa lo mismo que en el escenario A.
  `cycle.py:3136` solo mira la versión, no el modo de la meta.
- Qué debería pasar: el respaldo no debería dejar a la keyword por encima de la economía de su
  familia durante semanas por una falla de datos. Hay dos caminos:
  - anclar la familia en el target vigente de la hoja cuando este es menor, como hacía AUTO-03;
  - converger desde el target vigente cuando la causa es un dato faltante. Es el freno kimi-H1
    que la plataforma ya tiene, `goals.py:979-991`.
- Nota honesta: el código cumple la letra del plan. `plans/orbit-07-motor-decisiones.md:237`
  dice que el target de producto no es ancla de la familia, y
  `tests/test_orbit07_m2.py:402-438` fija la caminata 29.5 → 29.0 → 28.5. Lo que el plan no
  prevé es el exceso sobre la economía de la familia ni el disparo por fallas de ingesta. Es
  decisión del dueño si se corrige.
- Alcance con la config actual de producción: M2 está encendido en MX y US con la meta en
  `live`. Hoy no se dispara: la evaluación del 2026-11-07 encontró cero keywords bajo meta de
  producto (`docs/evidencia/orbit-07-plan/prearranque-2026-11-07/README.md:26-28`). En MX, el
  producto 1621 tiene 104 keywords en grupos exclusivos y está bloqueado por halo. Se vuelve
  real el día en que un producto de margen bajo quede con keywords bajo su meta y luego pierda
  elegibilidad, o cuando falle la ingesta contable 4 días. Es live, no shadow.
- Evidencia (sondas ejecutadas):

  `B_respaldo_familia.txt` (funciones reales `cycle._margen_familia_de_hoja`,
  `productos.meta_de_hoja` y `goals.cascada_target_acos`):
  ```
  == auto03 ==
    ciclo  1: target=4.5 (margen_producto)
    ciclo 16: target=12.0 (margen_producto)
    ciclos con target por encima de la economia de la familia (12): 0
  == producto_directo_v1 ==
    ciclo  1: target=25.0 (margen_familia)
    ciclo 16: target=17.5 (margen_familia)
    ciclo 27: target=12.0 (margen_familia)
    ciclos con target por encima de la economia de la familia (12): 26
  ```
  `B_veredicto_respaldo.txt` (`politica.decide`, ACoS 15 %):
  ```
  AUTO-03 tras perder elegibilidad (target 4.5, eq. 51):   Mover(factor=-0.12 ... grupo_sangra_vendedora)
  M2 tras perder elegibilidad (target 25.0, eq. 51):       Mover(factor=0.15 ... bajo_target)
  economia de la familia (target 12, eq. 51):              Mover(factor=-0.12 ... grupo_sangra_vendedora)
  ```
  `B_meta_directa.txt` §3-4 (ledger de hace 4 días):
  ```
  plataforma: aplicado=25.5 motivo=ledger_rancio (margen_neto_pct al caso = None)
  auto03               peldano producto=4.5 -> target de la hoja=4.5 (margen_producto)
  producto_directo_v1  peldano producto=None -> target de la hoja=25.5 (margen_plataforma)
  M2 elegible (target 4, equilibrio 8):        Mover(factor=-0.25 ... pierde_dinero_fuerte)
  M2 ledger rancio (target 25.5, equilibrio -): Mover(factor=0.15 ... bajo_target)
  AUTO-03 ledger rancio (target 4.5, eq. -):   Mover(factor=-0.12 ... grupo_sangra_vendedora)
  ```
  Leí línea por línea que familia, padre y plataforma usan la misma frescura del ledger:
  `migrations/0048_margen_familia.sql:166-168`, `migrations/0016_target_margen_correcciones.sql:116-118`
  y `migrations/0018_fabrica_campanas.sql` (CTE `fresco`).
- Cómo reproducir:
  `cd orbit07-wt && PYTHONPATH=. /Users/dn/dev/goncloud-Orbit/.venv/bin/python ../orbit07-review/sondas/B_respaldo_familia.py`
  (y `B_veredicto_respaldo.py`, `B_meta_directa.py`).

## Sospechas sin confirmar

1. **La cola resuelve el respaldo donde el ciclo congela la keyword.**
   - Qué pasa: si en el ciclo ejecutor el guardado de evaluaciones falla (`cycle.py:2653-2654`),
     el ciclo descarta las decisiones de esas keywords (`_descarta_sin_evidencia`). La cola, en
     cambio, revalida las pausas `margen_producto` con `sin_evaluacion_fresca` y cae a
     familia/plataforma (`apply_cola.py:783-786` y `941-951`).
   - Por qué es sospecha: confirmado por lectura, pero hoy no mueve dinero. Con
     `ads_pause_economica` ausente (última lectura: config 42, 2026-11-04), la pausa por umbral no
     usa el target y la cola solo lo registra como evidencia.
   - Para confirmarla: encender la pausa económica y forzar un error en `guarda_evaluaciones`.
     Requiere base; no ejecutado.
2. **La pausa económica se movería de golpe con la meta directa.**
   - Qué pasa: si el dueño enciende `ads_pause_economica`, el umbral de esa pausa
     (`bid.py:258`, gasto > 3 × target × ventas) sigue a la meta sin rampa. Una keyword que
     ayer pausaba con ACoS > 75 % pausaría hoy con ACoS > 12 % si su meta pasa de 25 % a 4 %.
   - Por qué es sospecha: no es un defecto de código, es el efecto de M2 sobre una palanca hoy
     apagada.
   - Para confirmarla: leer la config vigente de producción.

## Zonas revisadas sin hallazgos

- **Entradas absurdas → meta.** Con margen 0, negativo, NULL, cobertura 0.90, 10 días con
  venta, ledger de 4 días, venta cubierta 0 o producto sin fila, la meta queda en `SinDato`
  (nunca 0 ni negativa). Un margen de 150 % queda topado en 45. Un margen de 0.0001 % da meta
  0.00005, válida a propósito (S2, sin piso). La columna `target_acos_ciclo` es NUMERIC sin
  escala, así que no hay redondeo a 0 que viole un CHECK. Sonda `B_meta_directa` §1;
  `migrations/0046_target_acos_ciclo.sql:15`.
- **Topes después de la meta directa.** La meta solo entra como target. La puja sale de
  `_paso`: factor entre −30 % y +20 %, piso y techo del goal, piso aprendido y delta mínimo
  (`politica.py:601-624`), o de un regreso con clamp (`politica.py:244`). Con target 0.00005 %
  la puja baja −25 % y se detiene en el piso (`Mantener delta_bajo_umbral`), sonda §5. Las
  pujas solo se aplican en su propio ciclo (`apply.py:516-529`).
- **Moneda.** El margen es una razón. La vista anula cualquier mezcla de monedas
  (`0018_fabrica_campanas.sql`, columna `margen_neto_pct`). Los `DiaProducto` se construyen
  siempre en la moneda de la plataforma (`productos_io.py:567-590`).
- **Selector de versión.** `auto03` reproduce el código anterior: diff de `meta_de_hoja` y de
  `_margen_familia_de_hoja`, sonda §2 (24.5 contra 4.0). Un valor ajeno lanza `ValueError` en
  TX2 y el ciclo de esa plataforma queda `failed`; es cierre a propósito y `config_write`
  rechaza guardarlo. La versión se lee una sola vez por plataforma y ciclo
  (`cycle.py:3201-3205`), así que no hay mezcla dentro de un ciclo. Entre ciclo y cola solo
  habría mezcla si alguien guarda config a mitad del ciclo.
- **Sombra contra live (hermanos de F14).**
  - El equilibrio del producto solo entra con procedencia `margen_producto`, tanto en el caso
    vivo (`cycle.py:2143-2149`) como en la sombra (`cycle.py:2542-2548`).
  - `replace` no muta la economía compartida (`lecturas_caso.py:481-485`).
  - Con la meta en live, la sombra solo anota (`cycle.py:2496-2511`).
  - `_juicio_hoja` no registra episodios.
  - El freno I.5 filtra `modo='live'`.
- **Productos compartidos.**
  - Exclusivo significa que todos los anuncios no archivados son del mismo producto, con la
    misma regla en ciclo y cola (`productos_io.py:240-244` y `economia_revalidacion_io.py:65-74`).
  - Un grupo pertenece a un solo producto, y un producto en varias campañas da la misma meta a
    cada grupo apto.
  - El orden es determinista.
- **Cola M2 y `None` (hermanos de F10).**
  - `_producto_fresco` y `familia_fresca` corren dentro del `try` que captura KeyError,
    TypeError, ValueError y ArithmeticError (`apply_cola.py:885-974`).
  - El exceso solo se calcula con target (`apply_cola.py:1184-1188`).
  - La pausa por umbral usa `_TARGET_REVALIDA`.
  - `meta.motivos[0]` nunca recibe un `SinDato` vacío.
  - Los SQL nuevos tienen permisos (`0077:106`, `0048:223`, `0047:143`).
- **Lectura de productos que falla a medio ciclo.** Corre en un SAVEPOINT. Las keywords con
  previo `margen_producto` se saltan sin decidir y las demás caen a la cascada; no queda una
  meta vieja pegada (`cycle.py:2354-2367` y `2406-2414`).
- **Idempotencia.** M2 solo agrega el snapshot en TX3 (atómico, una vez por ciclo y con la PK
  de `target_acos_ciclo`) y evidencia en notas. El lock del ciclo evita corridas paralelas, y
  una repetición o un fallo parcial no duplican ni pierden filas.
