# Qué se borra o se retira (restar antes de sumar)

El diseño deja **una** política de bid en el camino vivo. Todo lo que hoy existe para elegir entre dos
políticas, caer de una a la otra o compararlas en sombra se borra. Lo que el replay necesita para volver a
decidir filas viejas se muda a un módulo de historia que el ciclo no puede importar.

Las rutas son del repo `~/dev/goncloud-Orbit` en `master` d83bb28.

## 1. Se borra del camino vivo

| Qué | Dónde está hoy | Por qué sobra |
|---|---|---|
| Las dos políticas detrás del interruptor `ads_bid_politica_<platform>` | `app/optimizer/goals.py:870-903` (`motor_evidencia_desde_settings`, `POLITICA_BANDAS_*`); el selector de política en `settings.html` | Ya no hay dos políticas entre las que elegir. La clave se conserva con vocabulario nuevo: ausente = el motor no mueve bids; `niveles_v3` = encendido. Hoy la clave no existe en producción, así que no hay migración de datos. |
| El regreso a la regla vieja cuando la nueva se abstiene (`fallback_v1`) | `app/optimizer/bid.py:377-417`, `:604-629`; `app/cycle.py:1878-1889` | Es el defecto que hacía que "no opino" terminara en recorte. En la política nueva la abstención es `Mantener` y no hay a dónde caer. |
| Las bandas de ventana de 30 días como política viva | `app/optimizer/bid.py:235-282` (`_factor_banda`, `_factor_cero_ventas`), `:420-433` | Es la regla que recorta con un clic sin venta. Su lógica se muda tal cual a `app/optimizer/eras.py` para el replay (sección 2). |
| Cinco parámetros de `decide_bid`: `evidencia`, `politica_bandas`, `confianza_recorte`, `confianza_subida`, `fallback_v1`, y los campos `politica` y `abstencion_v2` de `ResultadoBid` | `app/optimizer/bid.py:201-227`, `:470-489` | `decide(caso)` recibe un solo valor. `bid.py` queda con lo que no cambia: `_decide_pause`, `exceso_economico`, los pisos de pausa y el mapa de monedas. |
| La compuerta de 20 clics al bid vigente para poder opinar, y lo que arma la evidencia de v2 | `app/optimizer/evidencia.py:45`, `:137-159` (`CostoPorClic`, `EvidenciaHoja`), `:356-382` (`clasifica`), `:422-548` (`parciales_evidencia`, `evidencia_hoja`, `estima_acos`, `factor_por_evidencia`) | Los 20 clics pasan a ser la condición para repetir dirección (R15). La probabilidad se calcula en `politica.py` con `gamma_p` y `gamma_q`, que sí se conservan. |
| El roll-up por familia y subfamilia de la conversión | `app/optimizer/evidencia.py:78-119`, `:176-219`, `:385-419`; `app/optimizer/windows.py:698-823` (`_SQL_CONVERSION_GRANO`, `_SQL_MAPEO_HOJAS`, `conversion_jerarquica`, `_familia_de_*`) | Hay 0 productos etiquetados: esos dos niveles nunca aportaron. La previa que queda es de dos niveles (cuenta y resto del ad group) y solo frena subidas. La lectura por hoja la da una consulta nueva con tramos. |
| El CPC "posterior al cambio" con su consulta por hoja | `app/optimizer/windows.py:826-873` (`_SQL_CPC_SLICE`, `cpc_vigente`) | Lo sustituye la escalera de precio, que lee los dos lados del cambio en una sola consulta y solo para hojas que cambiaron. |
| Cooldown de bids, D.2 y sus tipos | `app/optimizer/goals.py:140-149` (`DIAS_EVIDENCIA_INVERSION`, `POLITICA_INVERSION*`), `:585-682` (`ultimo_bid_aplicado`, `permite_reversa_bid`, `SinHistoriaBid`, `HistoriaBidRota`, `UltimoBidAplicado`); llamadas en `app/cycle.py:1806`, `:1914-1945` | Las tres cosas son una sola idea, "esperar a ver el efecto del último cambio", y viven en `Trayectoria` (R2, R14, R15). `en_cooldown` se queda solo para la pausa y para el ad group de términos, que no cambian. |
| La ventana de bids de 30 días por hoja y su exigencia de 7 fechas | `app/optimizer/windows.py:489-496` (`ventana_bids`), uso de `MIN_FECHAS_COMPLETITUD` para bids; `app/optimizer/bid.py:587-594` | La suficiencia se mide en pedidos y dinero, no en días con fila. Además esa ventana se anclaba en la última fila de la propia hoja y se quedaba vieja cuando la hoja perdía tráfico. La ventana de cortes de la pausa no se toca. |
| La consulta `_SQL_MAX_FECHA_TERMINOS` que cada hoja ejecuta y siempre devuelve vacío | `app/optimizer/windows.py:312-314`, llamada desde `ventanas_entidad` (`:586-599`) | Los términos cuelgan del ad group. Es una consulta por hoja sin uso. |
| Los dos contrafactuales por decisión y sus claves congeladas | `app/cycle.py:1652-1752` (`_contrafactual_v2_json`, `_contrafactual_v1_json`), `:823-893` (`_evidencia_v2_json`), `:1946-2035`; claves `evidencia_v2`, `bandas_v1`, `politica_bandas_usada`, `inversion_policy_version`, `cooldown_policy_version` | Con una política no hay contra qué comparar. El freeze nuevo es `inputs.caso`, que es el argumento de `decide`. |
| Los parámetros que `_recorre_plataforma` pasa a `_procesa_decisora` solo para alimentar a v1 y v2 | `app/cycle.py:2590-2643` (`conv_jerarquica`, `confianza_recorte`, `confianza_subida`, `motor_evidencia`, `pause_sin_cooldown_bid`) | Los sustituye un solo valor, `lecturas`. |
| Dos peldaños del target que nunca decidieron: `cache_estado` y `default` (55 %) | `app/optimizer/goals.py:111`, `:295-332`, `:409`; `ad_entity_state.acos_target`; CHECK de `target_acos_ciclo` | El target publicado en Amazon llega vacío en todas las corridas, y un target inventado de 55 % contradice la regla 3. Sin peldaño, la hoja se salta con `sin_target`. |
| La herramienta que mide el encendido en una sombra sin regreso | `tools/compara_evidencia.py` (340 líneas), `tools/evidencia_b3_recorrido_persistido.py` | Contaba cada abstención como recorte evitado, cuando en vivo esas abstenciones las decidía la regla vieja. La sustituye `tools/rejuega_niveles.py`. |
| El orden bajo cupo que descarta primero las subidas | `app/apply.py:524-558` | Lo sustituye `prioridad_bajo_cupo`: regresos, recortes con evidencia propia, subidas y recortes heredados. |
| La exigencia de que un grupo tenga los cuatro roles de descubrimiento para poder cosechar | `app/apply_harvest.py:1753-1779` (`_roster_hermanas` devuelve `entidad_incompleta` si falta un rol) | Con un grupo de dos roles cada harvest se creaba y se revertía. El roster pasa a ser "los roles de descubrimiento que el grupo sí tiene, menos el de origen", y puede quedar vacío. |
| El literal fijo `CREAR 5 CAMPAÑAS` y el `--esperado 5` | `app/api_fabrica.py:194-197`, `tools/fabrica_campanas.py:223`, `fabrica.js:1153` | El número sale de los roles del plan. Los planes de cinco roles siguen diciendo cinco. |

## 2. Se muda (no se borra) porque el replay lo necesita

| Qué | De dónde | A dónde | Candado |
|---|---|---|---|
| Bandas v1, regla de cero ventas y pisos históricos `REPLAY_*` | `app/optimizer/bid.py`, `app/optimizer/cortes.py` | `app/optimizer/eras.py` (`decide_bid_era_bandas`) | Una prueba de arquitectura prohíbe que `app/cycle.py` importe `eras`. La prueba dorada de replay sigue pasando sobre todas las filas históricas. |
| `_replay_bid`, `_args_replay_bid`, `_agregado_sintetico` | `app/optimizer/replay.py:39-133` | Siguen en `replay.py`, detrás del despacho por era de `reproduce` | Fila sin `inputs.politica` rejuega con su era. Ningún valor vigente entra. |

Lo que **no** se muda y deja de rejugarse: `reproduce_evidencia_v2`, `reproduce_bandas_v1` y
`verifica_politica` (`app/optimizer/replay.py:206-324`). La política `evidencia_v2` nunca decidió en vivo, así
que ninguna decisión aplicada depende de ella. Sus bloques congelados en filas del 2026-10-03 en adelante
quedan como dato histórico legible.

## 3. Pruebas que se retiran con su código

- `tests/test_evidencia.py` (988 líneas): se quedan las pruebas de `gamma_p`, `gamma_q`, `previa_jerarquica` y
  `resta_conteo`; se van las de `clasifica`, `estima_acos`, `factor_por_evidencia` y el roll-up por familia.
- En `tests/test_cycle.py`, el bloque A4 y A6 de contrafactuales (desde `:2995`). La prueba dorada
  `test_golden_replay_reproduce_todas_las_decisiones` se conserva y gana filas de la era nueva.
- `tests/test_optimizer_bid.py` (1,307 líneas): las pruebas de bandas se mudan con el código a las de `eras`; las de pausa se quedan.
- `tests/test_cycle_anti_inversion.py` (215 líneas): D.2 deja de existir como pieza aparte; sus casos pasan a ser casos de R14 y R15 de `politica`.

## 4. Decisiones escritas que este diseño deja sin efecto

| Decisión anterior | Dónde está escrita | Qué la sustituye |
|---|---|---|
| Encender `evidencia_v2` cuando la sombra muestre menos de un tercio de los recortes, tras 14 ciclos | `~/.claude/orchestrate/acos-evidencia/docs/plan.md:535` | Rejuego a un paso de los ciclos pasados, el mismo día (`tools/rejuega_niveles.py`). |
| Calibrar `K_PREVIA` y las confianzas en la sombra hacia el 2026-10-16 | `plan.md:518` | La previa ya no recorta ni protege: solo frena subidas con un pedido prestado. No queda nada que calibrar para recortar. |
| "Matriz nunca-peor": al abstenerse la política nueva, decide la vieja | PR #386 | Abstenerse es no mover. |
| Paso de 0.5 por ciclo también para las subidas del target | `docs/superpowers/specs/2026-09-03-target-margen-plataforma-design.md:176-183`; `MARGEN_PASO_MAX` en `app/optimizer/goals.py` | La razón escrita del paso solo protege contra bajadas bruscas y vaivén. Las subidas aplican de una vez; las bajadas siguen a 0.5 por ciclo. |
| Reversa de bid que "no limpia el cooldown" y es invisible para el motor | `app/apply.py:1494-1498` | La reversa sigue sin tocar `decision_application`, pero el motor la ve en `v_cambio_bid` y la respeta. |

## 5. Lo que se decidió no construir

Cada una es una idea de la base o del diagnóstico que este diseño descarta, con su razón.

- **Tope acumulado de recorte por hoja.** Sobra: R16 y R1 detienen la racha por lo que el recorte le hizo al
  tráfico, que es el daño real. Un tope fijo habría frenado también a la hoja 3861, donde cinco recortes no
  quitaron tráfico.
- **Bid calculado por valor del clic.** Con el target de US en 28.34 %, las hojas que hay que corregir están a
  uno o dos pasos de 12 %. No justifica un cálculo nuevo sobre conversiones de 3 pedidos.
- **CPC del ad group como sustituto.** Erra 26 % contra 6 % del CPC escalado (prototipo A4).
- **Regla por régimen de CPC contra bid.** Solo hay 7 hojas en MX y 4 en US con 20 clics desde su último
  cambio: no alcanza para fijar una frontera. La relación se muestra en el tablero de ruido y no decide.
- **N ad groups por campaña para el impulso.** Ver "Alternatives considered" en `design.md`. No se toca
  `campana_grupo_rol`, no se sonda `PUT /sp/adGroups` y no se cambia el dedupe del harvest.
- **Grupo de control.** El dueño lo descartó el 2026-10-09 (decisión D4): no se construye.
- **Tabla de estado del impulso.** El estado se deriva de la última lectura y del estado del lote.
- **Pausar product ads (`PUT /sp/productAds`).** Sería mejor que archivar. Se sondeó en V.0 (sonda 1 sellada
  2026-10-10): el retiro pausa el product ad si la sonda sella; si no, archiva y repone.
- **Persistir cada abstención como fila de `decision`.** Serían unas 600 filas por día. Las abstenciones
  siguen contadas por motivo en `notes.skips`.
