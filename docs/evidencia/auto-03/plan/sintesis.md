# Síntesis del plan AUTO-03 (`plans/decisiones-producto-01.md`)

Fecha: 2026-11-04. Skill: pstack **architect** (Ground → Sketch con **arena** → síntesis), sin implementar. Base de lectura: `origin/master` `e6bab315` (el checkout local `resumen-ads-04-diseno` en `5adf1be0` solo difiere en `app/meli/catalogo.py` y `0075_meli_sku_alias.sql`). El plan está validado: `motor valida` → `ok  decisiones-producto-01: 9 bloques A(fast), B(gate), C(gate), D(release), E(release), F(release), G(release), H(release), I(release)`, 54 filas, todas con `Acepta`: 48 de la síntesis, 2 del ajuste por el modelo de estados, 3 de los límites editables con simulación y 1 de la revisión del PR (secciones al final).

## Problem

El diseño de AUTO-03 ya estaba sintetizado el 24-oct (`docs/superpowers/specs/2026-10-24-decisiones-producto-design.md`, con dos candidatos y comparación en `docs/evidencia/auto-03/diseno/`). Faltaba el plan de construcción para el motor y, sobre todo, contrastar la spec con el código vigente. El ground (`ground/00-explicacion.md`, 28 restricciones, 13 de política) encontró que la spec choca con el código en puntos que cambian la forma: el motor no llama a la variante de cascada que la spec muestra; un peldaño nuevo exige migración y seis puntos de paridad; los goals de fábrica e impulso ya traen target y pisarían al peldaño de producto casi siempre; el reporte de Amazon omite las fechas sin actividad, así que "fecha ausente = desconocido" excluiría justo a los productos que habría que pausar; el ciclo corre como `app_decide` con REVOKE deliberado sobre `impulso*` y `anuncio_retiro*`; la cola despacha un kind desconocido como harvest o negative; `ejecuta_retiro` ya acepta un literal humano reusado (el vigía); Orbit no tiene receta de despliegue por omisión para el motor.

## Usage (vista del implementador del motor)

Cada bloque es un PR que un agente implementa viendo solo ese bloque: nombra archivos y líneas vigentes, la firma nueva y un `Acepta` que sale 0. El preámbulo del plan fija S1..S18 (los supuestos de política) y A.1 los copia a la spec, así que el implementador aplica un supuesto en vez de decidirlo. Del lado de David: `productos-modo --platform P --meta|--pausa off|shadow|live` (único escritor de las claves, vía `config_write.guarda_config`), la pantalla de productos y `POST /retiro/reponer` con resultado por anuncio.

## Shape

Escalera por capacidad con reversa antes de cada efecto: A (spec corregida + medición SQL de elegibilidad) y B (receta genérica de despliegue en `tools/despliegue/` con prueba de instalar/deshacer) → C (peldaño `margen_producto` puro + CHECK 0076 + CLI de interruptores) → D (evaluación en sombra en TX2/TX3, tabla 0077, `tools.auto03_verifica evalua|replay`) → E (pantalla, conciliación contra Amazon y medición real con el código nuevo, sin esperar ciclo) → F (meta en vivo) ∥ G (contrato de retiro: autorización tipada, resultado por anuncio, `adoptada`, sin recreación silenciosa, 0078) → H (pausa en sombra en la cola, vista de impulso 0079) → I (pausa en vivo desde el ciclo con grants mínimos 0080). Decisiones de forma que ambos candidatos compartieron y el plan adopta: kind `pause` sobre entidad `product_ad` (no `pause_product_ad`); ejecución en el ciclo como `app_decide` con grants acotados y una vista estrecha de impulso; sin piso de 10 (función propia que reutiliza fracción, banda máxima y `motivo_dato_invalido`); goal explícito gana (S1) y se mide cuánto deja fuera; pisos fijos de PAUSE; interruptores por plataforma apagados por omisión; sin segundo encolador ni segundo escritor; colapso y madurez reutilizados de `evaluacion_catalogo`; una sola fuente de margen.

## Synthesis decision

Runners: A en `opus` (semilla "rebanadas por capacidad") y B en `sonnet` (semilla "sombra completa primero"), ambos con `motor valida` en verde. **Juez cruzado omitido**: el pool `arena cross-judge pool: opus, sonnet` es toda familia Claude, igual que la sesión que orquesta, y el skill prohíbe un juez de la misma familia; el lead eligió base solo con la rúbrica del frame.

| Criterio | A | B | Lectura |
|---|---|---|---|
| R1 fidelidad al código | 2 | 2 | Ambos citan líneas vigentes; A mapea las 28 restricciones una por una. |
| R2 secuencia y riesgo | 2 | 1 | A pone la pausa en sombra en la cola (práctica de veto con candidatos reales, como ORBIT 04) antes de vivo; B va de evaluación persistida a vivo. A exigía esperar un ciclo natural antes de F: se corrige con un injerto de B. |
| R3 un dueño, un camino | 1 | 2 | A escribía los interruptores con un `interruptor.sh` en bash que duplicaba `config_write.guarda_config` (dos caminos); B usa el escritor existente vía CLI. |
| R4 pruebas proporcionales | 2 | 2 | Ambos dentro de los 4 casos; A concentra las reversas de esquema en una sola prueba de instalar/deshacer que cubre todas las migraciones. |
| R5 válido y autónomo | 2 | 2 | Ambos pasan `motor valida` sin bloques humanos. |
| R6 carga del lector | 2 | 1 | A trae la tabla S1..S16 en el plan y migraciones por bloque; B deja `lib.sh` bajo `docs/evidencia/`, mal lugar para scripts que el motor corre repetidamente. |

**Base: A.** Injertos de B, incorporados a mano:

1. Interruptores escritos solo por `config_write.guarda_config` a través del CLI `productos-modo` (C.6); `desplegar.sh` converge `interruptores.env` llamando ese CLI dentro del contenedor (B.1, B.2). Se eliminó `interruptor.sh`.
2. Regla de fecha ausente (S6) anclada en el ledger de ingesta (`ads_report_result` `written` + `ingest_run.ok` que cubren la fecha), no en "otros productos tienen filas"; se conserva de A la condición de que el producto tenga al menos una fila en la ventana.
3. Ningún bloque espera a un ciclo natural: la evidencia previa a cada `live` sale de `tools.auto03_verifica evalua --json` + `replay` + `concilia` corriendo en lectura contra producción (E.4, F.1, I.2). Evita que el motor marque ARREGLAR un bloque sin nada roto.
4. `resumen_cobertura` como única función de conteo para `notes.productos`, la herramienta y la medición (D.3, D.5).
5. `_revalida` y `libera_vencidos` lanzan ante un kind o entidad sin rama propia en vez de caer a harvest/negative (H.4, H.6).
6. Precondición A.0: spec, ground, arena y síntesis versionados en master antes de entregar el plan.
7. `guarda_evaluaciones(estricto=...)`: tolerante en sombra (el error va a `notes.productos.error` y no cambia decisiones), falla cerrado en vivo.
8. Vista de impulso compartida por `retiro_anuncios._SQL_ANUNCIOS` y la lectura de productos (un solo lugar para "impulso abierto").

Rechazado de B: pasar de evaluación persistida a pausa en vivo sin la sombra en la cola; `lib.sh` bajo `docs/evidencia/`; "adoptado" solo para el motivo nuevo. Rechazado de A: `interruptor.sh`; esperar el ciclo de las 08:40 UTC antes de F; "adoptada" bloqueando también la reposición humana. Síntesis propia en S14: `adoptada` para cualquier motivo, `repone` la excluye por omisión y una `AutorizacionHumana` puede incluirla explícitamente; una `AutorizacionDeDecision` nunca repone adoptados.

Convergencias (señal fuerte, se adoptan sin injerto): kind `pause` sobre `product_ad`; ejecución como `app_decide` con grants acotados; sin piso de 10; S1; pisos fijos; interruptores por plataforma; vista estrecha de impulso; autorización tipada con el literal del vigía como humana; tabla de evaluación append-only con FK nullable desde `decision`; reutilizar `evaluacion_catalogo`.

## Tradeoffs accepted

- Aceptamos construir una receta genérica de despliegue (B) antes de la primera línea de AUTO-03 a cambio de que seis releases compartan una receta probada en simulación y cada migración se derive por cabecera `-- sonda-aplicada:` sin lista a mano.
- Aceptamos que la meta pueda quedar casi inerte mientras S1 mande (fábrica e impulso nacen con target) a cambio de respetar la autoridad vigente del dueño; A.3 y E.4 miden cuánto queda fuera y la palanca es `limpia_target`.
- Aceptamos ampliar grants de `app_decide` sobre el ledger de retiro (0080) a cambio de un solo liberador, una sola conexión y un solo protocolo intento-intención-cierre.
- Aceptamos que F e I enciendan `live` aunque la cobertura medida sea cero: sin grupos aptos no hay efecto y se activa solo cuando la historia alcance la ventana de 90 días; la pantalla y `notes.productos` lo muestran cada ciclo.
- Aceptamos que `cycle.py`, `apply_cola.py` y `goals.py` crezcan unas líneas de despacho (están en la allowlist); la lógica nueva va a `productos.py`, `productos_io.py`, `apply_producto.py` y `pantalla_decisiones_producto.py`.
- Aceptamos nueve bloques y seis releases a cambio de que cada escalón sea comprobable con datos reales y reversible por interruptor o por reversa de migración antes del siguiente.

## Alternatives considered

- **Sombra completa primero (semilla B).** Un primer bloque de sombra para meta y pausa toca a la vez IO, ciclo, cola, vista de impulso y pantalla: un PR no revisable, y retrasa la meta detrás del trabajo pesado de la pausa. Su ventaja (medir antes) se conservó en A.3 y E.4.
- **Kind nuevo `pause_product_ad` (lo que eligió la spec).** Toca enum, cuatro CHECK, dos triggers, `familia` GENERATED, `KINDS_QUOTA` y el SQL de cuota, `_SQL_CORTES_CICLO` y `KINDS_DECISION`; con los CHECK migrados y el código no, la cola lo ejecutaría como negative. Con `pause` sobre `product_ad` pasan todos los candados y la cuota de pausas es literalmente "una sola reserva".
- **Ejecutor `app_admin` aparte (como el vigía).** Exige cron nuevo, otro liberador de la misma cola, cambiar `apply_queue_sella_transiciones` y partir un efecto entre dos roles.
- **Reutilizar `resuelve_target_margen`.** Trae el clamp a [10,45] y rompe el caso 8% × 0.5 → 4%.

## Open questions and risks (para David; el plan fija supuestos reversibles, no espera respuesta)

1. **S1.** ¿Los goals de fábrica e impulso cuentan como "explícitos"? Si A.3/E.4 muestran que casi ningún grupo exclusivo queda sin target, la meta por producto estará construida y encendida pero inerte hasta que se limpien targets.
2. **S6, S8.** ¿Basta "fecha sin fila = cero si una corrida ok del reporte la cubre y el producto tiene filas en la ventana", para pausar product ads automáticamente? La regla literal de la spec nunca pausaría a nadie. Los límites de gasto y clics ya no se fijan aquí: el dueño los elige con la simulación de H.8 (ver la sección de límites editables).
3. **S9, S10.** ¿Se acepta ampliar el perímetro escrito de la cola (`docs/APPLY.md`) a pausas de product ad y dar a `app_decide` INSERT/SELECT acotados sobre el ledger de retiro?
4. **S14.** ¿Está bien que un anuncio confirmado tras una respuesta incierta quede "adoptado" también para fábrica e impulso, y que regresarlo exija marcar `incluye_adoptados`?
5. Riesgo: si más adelante se enciende `ads_pause_economica` (hoy ausente), una meta baja (4%) dispara antes la pausa económica de hojas; la sombra de D lo deja visible.
6. Riesgo: la conciliación `concilia` depende de que el reporte asíncrono de Amazon termine dentro del tope de `prueba-real`; ventas de atribución tardía pueden mover las cifras frente a lo colapsado a D+30.
7. Riesgo: S4 depende de la forma real del JSONB `ad_ids`; A.3 (8) la comprueba y, si no es lista de ids, el alcance pasa a "todos los ENABLED con identidad".
8. No verificado en producción en esta sesión: fracciones 0.5/0.8 (solo la lectura del 25-oct) y el número 0076 libre.

## Ajuste por el modelo de estados (2026-11-05)

Un modelo TLA+ de la pausa de un product ad con el plan construido (bloques H e I), revisado con pstack **statechart**, encontró dos reglas rotas en 384 estados. Las dos se confirmaron contra el código y el plan:

1. **Una propuesta de práctica tapa a la real.** Camino: interruptor en práctica, el ciclo propone, se enciende en vivo. Una fila `shadow` jamás se libera, solo `app_admin` puede descartarla (trigger de 0002) y mientras siga abierta bloquea la clave del anuncio (`_SQL_CLAVES_BLOQUEADAS`), así que el ciclo lo salta con `veto_pendiente` para siempre. El plan encendía sin cerrarlas. Corrección: fila **I.7** y supuesto **S18** (encender cierra en bloque las prácticas de product ad, como el cambio a vivo de ORBIT 05).
2. **Vuelve a pausar lo que el dueño regresó.** Camino de 9 pasos: pausa, el dueño lo regresa, el ciclo lo vuelve a proponer porque la ventana sigue sin ventas, y solo el veto de 48 h lo detiene. Las keywords tienen 7 días de gracia (`docs/APPLY.md` §3.3); los product ads no la tenían. Corrección: fila **H.7** y supuesto **S17** (gracia de 7 días desde `repuesto_el`, o desde `reactivacion_manual.detectada_en` si se reactivó en Amazon).

Con ambas correcciones el modelo corregido cumple las seis reglas en 304 estados: solo en vivo, nunca con venta, nunca con otra mezcla, un solo cobro, la práctica no tapa a la real y no re-pausa en la gracia. La regla 2 quedó acotada a la gracia: pasados los 7 días, Orbit puede volver a proponer y el dueño tiene sus 48 h de veto. Modelos, vistas, diagramas y reportes en `statechart/antes/` y `statechart/despues/`. El modelo deja fuera varios anuncios a la vez, la cuota agotada, los tiempos reales y la meta por producto; un dato desconocido cuenta como venta.

## Límites de la pausa editables con simulación (2026-11-05)

Una lectura de producción del 5-nov (solo lectura, ventana `[D-60, D-30)`, conteo aproximado antes de los demás filtros del plan) mostró que los límites heredados de la pausa de keywords no atrapan casi nada a grano producto. En Amazon MX 143 productos gastaron 6,077.60 MXN sin vender, con mediana de 29 MXN y máximo de 251.73 MXN, y ninguno llega a 500 MXN ni a 100 clics. En Amazon US 56 productos gastaron 603.02 USD sin vender y 3 llegan a 40 USD y 100 clics. La pausa no depende del margen: los productos sin margen medible también entran.

Por eso S8 deja de ser un número fijo. **C.7** vuelve editables los límites por mercado, con los valores actuales por omisión y un mínimo que no se puede bajar (el piso de los negativos). **H.8** agrega una simulación pura que corre la misma regla con otros límites y muestra, lado a lado, el gasto que se habría evitado (gasto observado, no ahorro prometido) y las ventas que se habrían perdido (ventas atribuidas del mes siguiente a los productos que se habrían pausado el mes anterior), sin ver observaciones posteriores a la fecha simulada (regla 5). **H.9** pone los campos y la simulación en la pantalla de productos y guarda por el escritor único de settings. Todo va antes de encender la pausa en vivo, para que el dueño elija el límite con datos; I.2 deja constancia de los límites con que se enciende. Con datos de productos desde el 2026-08-08, al principio la ventana anterior alcanza para uno o dos meses.

## Correcciones por la revisión del PR #531 (2026-11-05)

Los dos revisores automáticos del PR (DeepSeek por ai-review y CodeRabbit) encontraron fallas reales del plan. Se confirmaron contra el código y se corrigieron en el mismo PR:

- **La cobertura de fechas no se podía saber.** `ads_report_result` (0040) no guarda qué fechas pidió cada reporte y `diario.py` acepta `--fecha`/`--fecha-fin`, así que una corrida manual de un día habría hecho pasar 30 días ausentes por ceros y autorizado pausas (regla 3). Nueva fila **D.7**: la ingesta guarda el rango pedido; un rango desconocido no cubre. Costo aceptado: la pausa puede actuar unos 30 días después de desplegar D.
- **Escritura en la fase de lectura.** H.7 insertaba en `reactivacion_manual` dentro de TX2, que es de solo lectura, y rompía `evalua` con el rol de lectura. Ahora la lectura excluye en memoria y el INSERT va en TX3.
- **Doble PUT.** `reconcilia_intenciones` reenvía la pausa cuando lee el anuncio ENABLED. Ahora deja fuera las intenciones con `decision_id` y solo `apply_producto` las resuelve, sin reenviar.
- **Ventana vieja al liberar.** La revalidación de I.3 corre la regla con la ventana al reloj de liberación, como las pausas de keyword, y además exige cero ventas en la ventana congelada.
- **Carrera al encender.** Un ciclo podía meter una fila de práctica entre el descarte de I.7 y el cambio de config. Ahora ambos toman el mismo candado de base de datos, exclusivo al encender y compartido al encolar.
- **Rollback trabado.** Si una reversa abortaba por tener filas, el código no se restauraba. Ahora se restaura primero el código y las reversas van al final.
- **Evidencia que podía mentir.** Las pruebas previas a encender contaban dos líneas `diferencias: 0` sin distinguir replay de conciliación, y la `prueba-real` de E usaba `;`. Ahora cada línea va etiquetada por herramienta y mercado, y se exige cada una.
- **Goal en sombra.** H.3 aceptaba goals en `shadow`; ahora exige `mode = 'live'`, como dice S4.
- Menores: conteos y S1..S18 en síntesis y ROADMAP, barras sin escapar en `frame.md` y un bloque de código partido en `00-explicacion.md`.
- Hallado por el lead al revisar: el bloque C usaba en su `prueba-real` la sonda que crea B sin depender de B. Ahora depende de A y B.
- Segunda ronda de DeepSeek sobre `00efe6e` (resolvió los cuatro anteriores). **I.7 no era atómico ni repetible**: `guarda_config` hace su propio `commit` y rechaza una edición vacía, así que un fallo entre la config y el descarte dejaba la pausa en vivo con prácticas abiertas y el reintento abortaba. Ahora `guarda_config` acepta `confirma=False` y el CLI confirma después del descarte; con la pausa ya en vivo solo descarta. Menores: D.5 agrega `tools/auto03_verifica.py` al conjunto exacto que sella `tests/test_compose_deploy.py` y lo corre en su `Acepta`; la cita del permiso de admin pasa a 0002:251-261.

## Next implementation step

Versionar en master la spec, `docs/evidencia/auto-03/**` y `plans/decisiones-producto-01.md` (A.0), y entregar el plan al motor; el primer código es `app/optimizer/productos.py` con `resolver_producto` y el caso de dinero 8% × 0.5 = 4% (C.4, C.5), en paralelo con la medición `elegibilidad.sql` (A.3).

## Verificación del artefacto

- `motor valida` en verde (salida arriba); 9 bloques, 54 filas, 0 filas sin `Acepta`, 6 releases con `despliegue:` completo, dependencias sin ciclo.
- Pipes dentro de filas escapados (`Decimal \| None` se parsea como `Decimal | None`).
- Archivos y símbolos citados existen en master: `docs/evidencia/bids-02/ejecucion/D.1/desplegar.sh`, `tests/test_compose_deploy.py`, `tests/test_evaluacion_catalogo.py`, `tests/test_settings_escritura.py`, `tests/test_retiro_anuncios.py`, `tests/test_impulso_io.py`, `tests/test_api_fabrica.py`, `tests/test_apply_cola.py`, `tests/test_cycle.py`, `tests/test_cycle_target_ciclo.py`, `tests/test_apply_schema.py`, `tests/test_optimizer_goals.py`, `tests/test_architecture.py`; `config_write.proxima_config`/`guarda_config`, `apply_cola._meet_goals`, `goals_write.edita_goal(limpia_target=...)`, `apply_harvest.aplica_harvest`, `apply.ESTADO_PUT_PAUSED` (re-export de `write.py:157`), `archivar.preparar_escritor`/`leer_anuncios`/`reponer_anuncios`.
- No se ejecutó código de producto, ninguna consulta de producción ni ninguna escritura; los candidatos tampoco (citaron la lectura del 25-oct).

## Fases del skill

Ground: `how` complejo (3 exploradores + 1 explicador) → `ground/00-explicacion.md`. Sketch: arena con 2 runners, frame y brief en `arena/`. Agree: sin checkpoint (no se pidió), pero las preguntas de política quedan arriba. Implement y Scrap: fuera de la petición ("diseñar, no implementar").
