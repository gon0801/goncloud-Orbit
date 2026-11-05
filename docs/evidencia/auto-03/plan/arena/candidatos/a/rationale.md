# Rationale del candidato A (rebanadas por capacidad), plan `decisiones-producto-01`

Runner A de la arena AUTO-03, 2026-11-04, base `origin/master` `e6bab315`. Plan: `candidatos/a/plan.md`, con 9 bloques y 45 filas.

## Problem

AUTO-03 conecta tres subsistemas que hoy no se hablan. El primero es el ciclo con su cascada de target (`cycle.py` y `goals.py`). El segundo es la cola de cortes con el retiro de product ads (`apply_cola.py` y `retiro_anuncios.py`). El tercero son los datos por producto (`ads_product_metric_observation` y `v_margen_producto`). La forma no es obvia porque el código contradice a la spec en 28 puntos (`ground/00-explicacion.md`). Los que más pesan:

- El motor no llama a la variante `_con_procedencia` que la spec muestra.
- Un peldaño nuevo exige migrar un CHECK y actualizar seis puntos de paridad.
- `app_decide` tiene REVOKE sobre `impulso` y sobre el ledger de retiro.
- La cola despacha cualquier kind desconocido como harvest o como negative.
- El reporte de productos omite las fechas sin actividad, así que la regla "fecha ausente = desconocido" impediría pausar siempre.
- Orbit no tiene receta de despliegue por omisión.
- Hay decisiones de política abiertas (g3, g5, g8, g10, g13, g15, g16, g20 y g22) que el motor no puede preguntarle a David.

El plan tiene que corregir la spec donde el código manda, fijar esas decisiones como supuestos reversibles y ordenar la construcción para que nada toque dinero ni Amazon sin reversa probada antes.

## Usage (caller's view)

### Cómo lo lee el agente del motor que implementa un bloque

El agente ve solo su bloque y los archivos que abre. Por eso cada fila nombra el archivo, la línea vigente y la firma nueva, y su `Acepta` es un comando que sale 0. El preámbulo trae la tabla S1..S16: el agente aplica un supuesto, no lo vuelve a decidir.

| Bloque | Archivos que toca | Firma o contrato que cambia | Comando que lo acepta |
|---|---|---|---|
| A | spec, `APPLY.md`, `CONTEXTO.md`, `elegibilidad.sql` | solo documentos y una consulta de lectura | greps y conteo de etapas en `elegibilidad.txt` |
| B | `tools/despliegue/*.sh`, `tools/auto03/sonda.sh`, `DEPLOY.md` | receta sin argumentos que lee `MOTOR_SHA`; cabecera `-- sonda-aplicada:` en cada migración | `pytest tests/test_despliegue_motor.py` |
| C | `goals.py:232/279/315/341`, `productos.py` nuevo, 0076 | `target_margen_producto` keyword-only; `resolver_producto(entrada)`; `meta_de_hoja(...)` | `pytest tests/test_productos_meta.py tests/test_optimizer_goals.py` |
| D | `productos_io.py` nuevo, `cycle.py:2296/2852`, `evaluacion_catalogo.py`, 0077 | `lee_entradas_productos(...)`, `guarda_evaluaciones(...)`, FK `decision.evaluacion_producto_id` | `pytest tests/test_productos_io.py tests/test_despliegue_motor.py` |
| E | `pantalla_productos.py`, `productos.js`, ficha verify-orbit, `auto03_verifica concilia` | sección nueva del payload de `/productos-sin-clics` | `compileall` y la ficha presente |
| F | `cycle.py:1706-1728`, `apply_cola.py:618-664` | la cascada recibe la meta; la revalidación usa peldaños congelados | `pytest tests/test_productos_meta_ciclo.py` |
| G | `retiro_anuncios.py`, `api_fabrica.py:684-745`, `impulso_io.py:1034-1057`, 0078 | `ejecuta_retiro(..., autorizacion)` devuelve `tuple[ResultadoAnuncio, ...]`; `repone` sin recreación; `recrea` | `pytest tests/test_retiro_contrato.py` y las pruebas de retiro e impulso |
| H | `productos.py`, `productos_io.py`, `cycle.py`, `apply_cola.py:422/478`, `replay.py:122`, 0079 | `_Pendiente` kind `pause` sobre `product_ad`; payload `{adId, state}` | `pytest tests/test_productos_pausa.py` |
| I | `apply_producto.py` nuevo, despacho en `apply_cola` y en `apply_harvest_reconciliacion`, 0080 | `revalida_/ejecuta_/reconcilia_pause_producto(conn, fila...)` | `pytest tests/test_apply_producto.py tests/test_apply_cola.py` |

### Llamadas resultantes en el código (derivadas de lo anterior)

```python
# TX2, cycle._recorre_plataforma: una lectura por lote bajo el snapshot del ciclo
modo_meta = productos.modo_meta_desde_settings(settings, plataforma)
if modo_meta != "off":
    entradas = productos_io.lee_entradas_productos(
        conn, plataforma, decidido_el=ahora, settings=settings,
        goals_por_campana=por_campana, goal_plataforma=goal_plataforma,
        claves_bloqueadas=claves,                    # desde H
    )
    evaluaciones = tuple(productos.resolver_producto(e) for e in entradas)

# _procesa_decisora, con modo_meta == "live" (F)
meta = productos.meta_de_hoja(ev.meta, ultimo=previo, destino=sin_producto, tiene_previo=previo_es_producto)
target = g.cascada_target_acos(goal.target_acos_pct, setting_target, margen_plataforma, goal.scope,
                               target_margen_familia, target_margen_producto=meta.aplicado)
procedencia = g.peldano_target_acos(goal.target_acos_pct, goal.scope, margen_plataforma, setting_target,
                                    target_margen_familia, target_margen_producto=meta.aplicado)

# TX3: las evaluaciones van antes que las decisiones que las referencian
ids = productos_io.guarda_evaluaciones(conn, cycle_id, evaluaciones)

# TX4, apply_cola.libera_vencidos, pausa sobre product_ad (I)
motivo = apply_producto.revalida_pause_producto(conn, fila, ahora)      # antes del claim
usada, _ = consume_quota_y_sello(conn, platform, "pause")               # la misma reserva de pausas
estado = apply_producto.ejecuta_pause_producto(conn, fila)              # ledger antes del HTTP y retiro
#   adentro: retiro_anuncios.ejecuta_retiro(conn, retiro_anuncios.plan_de_decision(conn, fila.decision_id),
#            autorizacion=AutorizacionDeDecision(fila.decision_id, fila.id))

# Reversa manual desde la pantalla de productos (G)
retiro_anuncios.repone(conn, retiro_ids=ids,
                       autorizacion=AutorizacionHumana("REGRESAR 3 ANUNCIOS", actor="fabrica-ui"))
```

## Shape

**Datos primero.** Cada migración llega en el bloque cuyo código la usa, como su primera fila, con `_reversa_` y cabecera `-- sonda-aplicada:`. La receta (B.1) aplica las pendientes antes de reconstruir la app.

| Migración | Bloque | Qué hace |
|---|---|---|
| 0076 | C | CHECK de procedencia con seis nombres; la reversa lo deja `NOT VALID` para conservar la historia |
| 0077 | D | `evaluacion_producto_ciclo` append-only y FK nullable en `decision` |
| 0078 | G | `autoria`, el motivo `producto_sin_ventas` y `decision_id` UNIQUE en la intención |
| 0079 | H | vista estrecha `v_impulso_abierto` y COMMENT del perímetro de la cola |
| 0080 | I | grants mínimos de `app_decide` sobre el ledger de retiro |

**Tipos.** `app/optimizer/productos.py` es puro. Reutiliza `goals.MedicionMargen`, `ResolucionMargen`, `motivo_dato_invalido`, `converge_al_destino(piso=...)` y las constantes de banda y paso, sin copiarlas. Los importes van en `Dinero(valor, moneda)`. La entrada congelada se serializa con `como_json`/`desde_json`, y el replay corre la misma función sobre ella.

**Dueños.**

| Qué | Dueño único |
|---|---|
| Encolar | `apply_cola.encola_cortes`; con kind `pause` y entidad `product_ad` pasan todos los CHECK, `familia=entity_cut`, el trigger de madurez y la cuota de pausas |
| PUT, intención y candados `retiro-ad:<id>` | `retiro_anuncios` |
| Cerrar filas de cola de `product_ad` | `apply_producto` |
| Fuente de margen | `v_margen_producto`; la evaluación es evidencia, no fuente |
| Claves `ads_producto_*` | `interruptor.sh` |
| Receta de despliegue | `tools/despliegue/` |

**Profundidad.** `productos_io` esconde SQL, identidad ASIN→listing→producto, colapso, cobertura y huella detrás de una lectura y una escritura. El resolutor esconde la política detrás de una entrada y una salida. `apply_producto` esconde el puente cola→retiro detrás de tres funciones con la forma del hook `apply_harvest.aplica_harvest`. `retiro_anuncios` sigue escondiendo PUT, readback, intención y reconciliación.

**Escalera por capacidad.** La meta pasa por C (puro), D (sombra con doble veredicto), E (pantalla, replay y conciliación) y F (vivo). La pausa pasa por G (reversa y autorización), H (sombra en la cola) e I (vivo). Cada `live` exige `replay` en verde dentro de `desplegar.sh` y evidencia de `concilia` contra Amazon (F.1, I.2).

**Lo que no hace.**

- Ningún cron, servicio, caché por producto ni goal por producto.
- Ninguna pausa económica de productos con venta.
- Ningún cambio por stock.
- Ninguna reactivación automática.
- Ningún cambio en `_procesa_grupo` (S16), en R3 (S3), en `propuestas_campana` ni en `/settings`, que tienen otro grano.

## Synthesis decision

La llena el lead.

## Tradeoffs accepted

- Aceptamos construir una receta de despliegue genérica (B) antes de la primera línea de AUTO-03. A cambio, seis bloques `release` comparten una receta probada en simulación, y sus reversas de esquema se ejercitan en cada bloque con `test_despliegue_motor.py`.
- Aceptamos ampliar los permisos de `app_decide` sobre `anuncio_retiro` y `anuncio_retiro_intencion` (S10). A cambio no hay cron nuevo, que la spec prohíbe, ni un segundo liberador de la cola con otro rol y otro trigger de transiciones. `go_literal` sigue oculto: la vista de impulso no lo expone.
- Aceptamos que la meta pueda quedar casi inerte por S1, porque fábrica e impulso nacen con target. A cambio se respeta el goal explícito. La palanca es `limpia_target`, y A.3 mide el efecto antes de construir.
- Aceptamos que `adoptada` aplique a todos los motivos (S14). La fábrica pierde la reposición automática en el caso raro de una respuesta incierta. A cambio, una pausa sin autoría demostrada nunca se reactiva sola.
- Aceptamos guardar la evidencia congelada en JSONB y no en una tabla normalizada por fecha. El ciclo corre una vez al día por plataforma (`DEPLOY.md:1042-1044`), unos 211 productos, así que el volumen es chico.
- Aceptamos esperar al menos un ciclo de sombra (08:40 UTC) antes de F. El motor no tiene un estado de espera de despliegue, así que el guard puede abortar y revertir una vez.
- Aceptamos que `cycle.py` crezca unas 110 líneas: está en la allowlist con la nota de que no tiene frontera coherente. `apply_cola.py` y `goals.py` crecen poco, y su lógica nueva va en módulos nuevos.

## Alternatives considered

- **Semilla B, sombra completa primero (meta y pausa sin efectos, luego encender una por una).** Perdió.
  - Su primer bloque de sombra tendría que tocar a la vez IO, ciclo, cola (payload y modo de `product_ad`), vista de impulso y pantalla: un PR no revisable.
  - Retrasa la meta, que es la capacidad reversible en cada ciclo, detrás del trabajo pesado de la pausa (contrato de retiro, grants, puente con la cola).
  - Su ventaja real es medir la elegibilidad de la pausa antes de comprometerse. A la neutraliza con A.3, que mide el embudo de meta y de pausa con SQL de lectura antes de escribir código.
  - Desvío de la semilla A: G (reversa y autorización) no espera a la meta en vivo. Va en paralelo a F, porque es infraestructura de reversa (regla 7) y no una capacidad de pausa.
- **Kind `pause_product_ad` (lo que eligió la spec).** Perdió.
  - Exige `ALTER TYPE ... ADD VALUE` en dos transacciones y tocar `decision_termino_coherente`, `decision_unica_entidad_ciclo`, el trigger de madurez, `apply_queue_solo_cortes`, `familia` GENERATED, `KINDS_QUOTA`, el SQL de cuota, `_SQL_CORTES_CICLO` y `KINDS_DECISION`.
  - Mientras los CHECK estén migrados y el código no, `_revalida` lo trata como harvest y `libera_vencidos` lo ejecuta como negative.
  - Con `pause` sobre `product_ad` el modo de falla es seguro: fila `shadow`, payload `{}` y descarte `entidad_no_viva` (`apply_cola.py:523-526`).
- **Ejecutor `app_admin` aparte, como el vigía.** Perdió: requiere cron nuevo, otro liberador de la misma cola, cambiar el trigger `apply_queue_sella_transiciones` y dar a `app_admin` las transiciones y el ledger que hoy solo tiene `app_decide` (`0002:690-707`).
- **Scripts por bloque en `docs/evidencia/` (estilo BIDS 02).** Perdió frente a la receta genérica. La lista `aplicada()` del script viejo queda reemplazada por la sonda en la cabecera de cada migración, así que no hay lista que sincronizar a mano. El estado deseado de los interruptores vive en un archivo versionado.
- **Usar el escritor de la fábrica dentro del ciclo con un `go` fijo.** Rechazado por la spec y por `AutorizacionDeDecision`, que verifica decisión, fila `applying`, modo live, veto vencido y cuota cobrada.

## Correcciones a la spec que propone el plan

1. El snippet de llamada usa `cascada_target_acos` y `peldano_target_acos` con `target_margen_producto` keyword-only. Desaparece `cache_acos_target`, que es un argumento muerto (g1).
2. `MargenMedido` pasa a ser `goals.MedicionMargen`. La meta lleva el paso asimétrico sellado en CONTEXTO para los peldaños de margen (S2).
3. El kind es `pause` sobre `product_ad`, no `pause_product_ad`. La lista de enums, CHECK y triggers a migrar desaparece (S11).
4. "Fecha ausente = desconocido" se reemplaza por la regla de cobertura S6 (g10).
5. El halo sobre el periodo de las hojas ya no exige madurez +30; esa madurez queda solo para la pausa (S7, g11).
6. Se elimina "al graduarse entra por la misma evaluación": el goal del impulso conserva su target (S1, g3).
7. La pausa se ejecuta dentro del ciclo con grants mínimos y una vista estrecha (S10). La spec no lo decía y chocaba con los REVOKE.
8. `lee_entradas_productos` recibe además los goals ya leídos y `claves_bloqueadas`. El replay de la pausa usa la evaluación referida; `reproduce` solo devuelve lo congelado.
9. La meta aplica solo a hojas (S16). `propuestas_campana` y `/settings` no reciben el peldaño, porque su grano es campaña o plataforma.
10. La "pantalla de productos" es una sección nueva de `/productos-sin-clics` que no reutiliza `coalesce(c.clics,0)`.

## Decisiones del dueño fijadas como supuestos

Todas son reversibles y están visibles en la tabla del plan.

| Gotcha | Supuesto | Por qué este valor | Cómo se revierte |
|---|---|---|---|
| g3 | S1: gana el goal con target no nulo | Conserva la autoridad vigente del dueño; A.3 mide cuántos goals vivos tienen target | `limpia_target` por goal de campaña en /settings (`goals_write.edita_goal`) |
| g5 | S2: el paso es el de los otros márgenes | Es la regla sellada en CONTEXTO ("sube de una vez, baja 0.5") | Interruptor meta en `off` |
| g8 | S4: pertenencia por `ad_ids` | Cumple "solo anuncios que contribuyeron" sin repartir dinero | Interruptor pausa en `off` |
| g10 | S6: cero con cobertura de plataforma | El reporte SP atribuye por clic: sin fila no hay impresiones, ni clics, ni compras atribuidas. La cobertura de plataforma madura prueba que el reporte de la fecha llegó | Constante y medición en A.3 y D |
| g13 | S8: `F_PAUSE` y `PAUSE_COST_MIN` fijos | Son los pisos de su fuente; el adaptativo depende de evidencia de hojas | Interruptor pausa |
| g15 | S9: el plan sella el perímetro | Un plan equivale a autorización completa | Interruptor pausa |
| g16 | S10: ejecución en el ciclo | No hay cron nuevo y hay un solo liberador | Reversa de la 0080 |
| g20 | S13: el literal del vigía cuenta como humano | Lo tecleó David al lanzar ese impulso; así no hay regresión del vigía | Plan aparte |
| g22 | S14: `adoptada` para todos los motivos | Una sola regla segura | Reversa de la 0078 |

## Mapa de las 28 restricciones

| g | Dónde se resuelve |
|---|---|
| g1 | A.1, C.2 |
| g2 | C.1, C.2, C.3 (CHECK, tupla, `_FUENTES_COMPARTIDAS` derivada, `PISA_POR_PELDANO` inalcanzable y declarado, `ui.py` ya deriva, tupla de la prueba; la paridad estática de `test_apply_schema.py:994` se cumple sola) |
| g3 | S1, A.1, A.3 etapa 7 |
| g4 | C.4, C.5 (8% × 0.5 = 4, falla contra `resuelve_target_margen`) |
| g5 | S2, C.2, C.4, F.2 |
| g6 | S3 |
| g7 | D.3 |
| g8 | S4, H.3 |
| g9 | S5, D.3 |
| g10 | S6, D.3, D.6 |
| g11 | S7 |
| g12 | D.1, D.2 |
| g13 | S8, H.2 |
| g14 | S11, H.4 |
| g15 | S9, A.2, H.1 |
| g16 | S10, H.1, I.1 |
| g17 | I.5 |
| g18 | S12, F.3, F.4 |
| g19 | I.3 |
| g20 | S13, G.2, G.4 |
| g21 | G.1, G.2 |
| g22 | S14, G.1-G.3 |
| g23 | S15, G.3, G.4 |
| g24 | G.2, I.3, I.6 |
| g25 | Módulos nuevos `productos.py`, `productos_io.py` y `apply_producto.py`; `retiro_anuncios.py` queda en unas 810 líneas, bajo 900 |
| g26 | D.1, F.2, H.3, H.4 |
| g27 | E.1, E.2 |
| g28 | Preámbulo (`origin/master`, migraciones 0076 o siguiente libre); la fracción 0.5/0.8 está verificada en la lectura del 25-oct |

## Chequeo de banderas rojas

| Bandera | Resultado |
|---|---|
| Módulo superficial | No. Cada módulo nuevo tiene una o tres entradas detrás de mucha política. `tools/auto03_verifica` es herramienta, no API. |
| Fuga de información | Controlada. Las claves de interruptor viven en `productos.py`, con paridad contra `interruptores.env` sellada por prueba (D.5). Los motivos son una tupla con etiquetas al lado. El grano de los peldaños vive en `PELDANOS_POR_ENTIDAD`, de donde el replay económico deriva. |
| Descomposición temporal | No. `productos_io` agrupa carga, colapso e identidad alrededor del conocimiento "evidencia del producto". |
| Pass-through | No. `ejecuta_pause_producto` agrega ledger antes del HTTP y cierre desde la intención; no reenvía tal cual a `ejecuta_retiro`. |
| Dueño partido | Revisado. La intención la pueden sellar el paso diario del vigía y la reconciliación del ciclo, pero siempre a través de `retiro_anuncios` y bajo el candado `retiro-ad:<id>`. La fila de cola solo la cierra `apply_producto`. |
| Dos caminos | No. Hay un encolador y una cascada. `repone` y `recrea` quedan como dos tareas distintas y explícitas. La receta vieja por bloque queda como historia. |
| Internals importables | Se publican antes de usarse fuera: `_motivo_dato_invalido`, `_converge_al_destino`, `_fecha_madura`, `_suma`. Residual declarado: `apply_producto` usa `apply._ledger` y `_confirma_resumen`, como ya lo hacen `apply_cola` y `apply_harvest` dentro de la familia apply. |
| Lista sincronizada a mano | Se eliminan o se sellan: la validación de motivo de `planea_retiro` pasa a `get_args` (G.2); `_FUENTES_COMPARTIDAS` se deriva (C.3); la sonda de "aplicada" vive en cada migración, sin lista paralela en el script. |

## Open questions and risks

- ¿David acepta S1 si A.3 muestra que casi ningún grupo exclusivo tiene goal sin target? La meta quedaría construida y apagada en la práctica hasta que limpie targets de fábrica o impulso.
- ¿Qué pasa si F se despliega antes del primer ciclo en sombra? El guard de `replay` aborta y `rollback.sh` revierte. El motor manda el bloque a ARREGLAR aunque no haya nada que arreglar. ¿Conviene un estado de espera en el motor?
- S6 asume que una clave envenenada en la ingesta no desaparece en todas sus observaciones de una fecha suelta. ¿Se agrega un contador de claves envenenadas por fecha antes de encender la pausa?
- Cuando se encienda `ads_pause_economica` (ausente en la config 42), una meta baja (4%) dispara antes la pausa económica de hojas. La sombra de D lo deja visible; ¿lo revisa la fase C.6 de protección?
- La conciliación (`concilia`) depende de que el reporte asíncrono de Amazon termine dentro del tope de 1800 s de `prueba-real`.
- El ambiente del motor necesita `uv` y el Postgres desechable de `.motor.toml` para los `Acepta`. Se omitió la guarda de CI con `gh` porque el daemon puede no tener credenciales; el motor solo mergea PRs en verde.

## Next implementation step

Correr `elegibilidad.sql` de A.3 contra producción en modo lectura y, en paralelo, escribir `app/optimizer/productos.py` con `resolver_producto` y el caso de dinero 8% × 0.5 = 4% (C.4 y C.5).

## Salida literal de `motor valida`

```
$ cd ~/dev/goncloud-motor && python3 -m motor valida /Users/dn/dev/goncloud-Orbit/docs/evidencia/auto-03/plan/arena/candidatos/a/plan.md --home ~/.motor
ok  decisiones-producto-01: 9 bloques A(fast), B(gate), C(gate), D(release), E(release), F(release), G(release), H(release), I(release)
```
