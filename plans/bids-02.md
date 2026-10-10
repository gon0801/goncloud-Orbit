# BIDS 02: el motor de pujas para una cuenta con poca data

Estado: plan del 2026-10-09 UTC. El diseño se terminó el mismo día. Nada está construido. Base de
planificación: commit `45b7de3` de `origin/master`.

**Resultado:** una sola política de bid, `niveles_v3`, decide cada keyword y cada product target con sus
propios datos de 90 días, o con los de su ad group cuando los propios no alcanzan. El motor deja de recortar
sin evidencia y regresa solo un bid que desplomó el tráfico. El dueño ve cinco pantallas nuevas: keywords
dañadas, dónde poner el dinero, productos que venden y casi no reciben clics, búsquedas que gastan sin vender,
y ruido. También impulsa productos con dos campañas por producto y aprueba ajustes de campaña que Orbit le
propone.

**Diseño:** `docs/superpowers/specs/2026-10-09-bids-02-design.md`, con su bosquejo de tipos, su modelo de
datos, su tabla de decisión, su lista de borrado y sus pruebas en `docs/evidencia/bids-02/diseno/`. La tarea
0.a los incorpora al repo. Hasta entonces viven en
`~/.claude/orchestrate/motor-poca-data/docs/diseno/sintesis/`. El diseño dice qué se construye. Este plan
dice en qué orden, quién toca qué archivo y cómo se comprueba cada pieza. El paso a paso está en
[la guía de construcción](bids-02-construccion.md).

**Precedencia:** `docs/CONTEXTO.md` > diseño de BIDS 02 > este plan > guía de construcción. Si dos documentos
se contradicen, sigue el de mayor precedencia y avisa al lead.

**Sustituye** al programa `acos-evidencia` (bloques A1 a A7, PRs #381 a #388) en lo que quedó abierto: el
encendido de `evidencia_v2`, la calibración de `K_PREVIA` y la espera de 14 ciclos de sombra. Lo que ese
programa cerró sigue en pie: el target por margen, las familias en dos niveles y las confianzas por
plataforma. Cubre también, en parte, las filas AUTO-03 a AUTO-06 de `plans/ROADMAP.md`.

**Fuera de alcance:** Sponsored Brands y Sponsored Display, horarios, portfolios, target por producto,
cambios a PAUSE, a negative y a harvest (salvo permitir que un grupo de dos campañas coseche), y que el motor
cambie por su cuenta el presupuesto, la estrategia de puja o los ajustes por ubicación. Tampoco se construye
"darle otro tope" a un impulso terminado: el dueño lanza un impulso nuevo del mismo producto. Tampoco se
construye un motor de negative ASIN ni de negative phrase (decisión D11). De ese tema el plan construye solo
la pantalla "Búsquedas que gastan sin vender", y el dueño agrega esos negatives a mano en Amazon.

## Palabras de este plan

| Palabra | Significado |
| --- | --- |
| Hoja | Una keyword o un product target. Es la unidad sobre la que el motor decide un bid. |
| Gasto para concluir | 350 MXN o 36 USD. El gasto sin venta que hace falta para concluir algo, y el tope de aprendizaje de un impulso. |
| Impulso | Un producto con su par de campañas propias, una exact y una automática, con tope y veredicto. |
| Bolsa compartida | Un ad group viejo que anuncia decenas de productos. |
| Ajuste de campaña | Un cambio que el dueño aprueba desde la pantalla: limitar el gasto fuera de Amazon, cambiar el ajuste por ubicación o cambiar el presupuesto diario. |
| Rejuego | Volver a decidir ciclos pasados con la política nueva, sobre los datos como se veían cada día. |
| Sección | Un grupo de tareas que Muse recibe completo, construye completo, se revisa una vez y se despliega. |

## Decisiones que gobiernan este plan

Las once decisiones del dueño del 2026-10-09 (D1 a D11) están en la sección "Decisiones del dueño" del
diseño. Este plan no las repite. Tres de ellas fijan valores que el plan usa:

| Tema | Valor | Dónde se cambia |
| --- | --- | --- |
| Fracción del margen de US (D1) | 0.8. Target de 25.2 %. La escribe D.1 | Setting `ads_target_fraccion_margen_amazon_us` |
| Gasto para concluir (D2) | 350 MXN y 36 USD, fijos | Setting `ads_gasto_para_concluir_<plataforma>` |
| Encendido (D5) | Directo a vivo, el mismo día del despliegue | Setting `ads_bid_politica_<plataforma>` |

**Por qué no hay motor de negatives (D11).** La medición cubrió 100 días de campañas activas. Una búsqueda
típica tiene 1 clic. El gasto en búsquedas sin pedido es casi el que da el azar: 40 % visto contra 33 % por
azar en MX, y 63 % contra 62 % en US. Con el gasto para concluir como mínimo, solo aparecen 4 candidatos en MX
y 4 en US. Un motor de negatives actuaría sobre ruido. Por eso el plan construye una pantalla de solo lectura.

El diseño dejó preguntas abiertas. Ninguna frena la construcción. El plan usa estos valores hasta que el
dueño diga otra cosa:

| Tema | Valor que usa el plan | Dónde se cambia |
| --- | --- | --- |
| Días para invertir dirección sin clics nuevos | 14 | `DIAS_SALIDA` en `app/optimizer/politica.py` |
| Anuncios sin producto ligado | Quedan fuera del impulso y del retiro. La pantalla dice cuántos son | `lee_productos` |
| Sacar un producto de una bolsa compartida | Pausa del product ad si la sonda V.0 la sella. Si no, archivar y reponer | `app/retiro_anuncios.py` |
| Regreso por desplome | Vuelve al bid anterior de una vez | Regla R1 |
| Impulso que agota su tope | Se pausa sin veto de 2 días | `impulso_io.vigila` |
| Hoja sin pedidos con menos de 87.50 MXN o 9 USD de gasto | No se recorta aunque su ad group sangre | `MATERIALIDAD_GRUPO` |
| Target de un impulso | No lleva target propio. Resuelve por margen, igual que un grupo de la fábrica desde A1 | `PlanImpulso` |
| Presupuesto diario de un impulso menor al mínimo de Amazon | El impulso usa el mínimo que V.0 midió, y el vigía sigue pausando dos presupuestos diarios antes del tope | `TopeAprendizaje` |
| Una sonda de V.0 que no sella su verbo | Ese ajuste de campaña no se construye: la pantalla muestra el dato sin botón | Tareas V.3 y P.2b |
| Gasto fuera de Amazon de un impulso | Un impulso nace con la configuración por defecto de Amazon. Lo cubren el aviso diario "una ubicación que gasta sin vender" y el ajuste del dueño. El veredicto de un impulso puede incluir clics de fuera de Amazon, y la pantalla dice cuántos | Tareas I.3, V.4, V.3 y P.4 |
| Bid sugerido mayor que el presupuesto diario de su campaña | El plan del impulso baja ese bid al presupuesto diario de su campaña y la vista previa lo dice | Tareas I.2 e I.3 |
| Sin target resuelto en ningún peldaño | La hoja y su ad group se saltan con motivo `sin_target`. Las pantallas dicen "sin target" | Tarea T.1 |

## Tamaño medido

Lecturas de producción con `orbit_read` y con el cliente de solo lectura de Amazon, del 2026-10-09. Fuentes:
`docs/evidencia/bids-02/diseno/PRUEBAS.md` y los prototipos de la misma carpeta.

| Dato | Amazon MX | Amazon US |
| --- | ---: | ---: |
| Hojas activas | 355 | 233 |
| Campañas activas | 34 | 9 |
| Pedidos por ads en 30 días | 120 | 36 |
| Recortes aplicados por el motor de hoy en 30 días | 220 | 138 |
| Recortes del diseño nuevo en el mes simulado | 11, en 9 hojas | 22, en 17 hojas con 72.5 % del gasto |
| Subidas del diseño nuevo en el mes simulado | 33 | 7 |
| Keywords dañadas hoy | 6 | 3 |
| Productos que venden y casi no reciben clics | 10 | 5 |
| Productos anunciados sin un pedido en 11 meses | 90 | 34 |
| Gasto en búsquedas sin pedido en 100 días: visto y por azar | 40 % y 33 % | 63 % y 62 % |
| Candidatos que gastaron el gasto para concluir sin un pedido | 4: ningún ASIN, 1 palabra y 3 frases | 4: 2 ASIN, 1 palabra y 1 frase |
| Duración de un ciclo, mediana | 0.9 s | 0.9 s |
| Lectura nueva de 90 días de toda la plataforma | 0.11 s | 0.09 s |

El mes simulado de US usa el target de 25.19 %. El de MX usa 20.72 %.

## Contratos

El revisor comprueba estas reglas en cada fila. Vienen del diseño y de `docs/CONTEXTO.md`.

- **Una sola política de bid.** `app/cycle.py` arma un `CasoHoja`, llama a `politica.decide` y guarda. No
  conoce reglas, niveles ni previas.
- **El replay es la misma función.** `decide(CasoHoja.desde_json(inputs["caso"]))` da el veredicto guardado.
  Una señal nueva que decide es un campo de `CasoHoja`. Las filas anteriores se rejuegan con
  `app/optimizer/eras.py`.
- **"No sé" es no mover.** Ningún `Mantener` termina en recorte. No existe regreso a otra política.
- **Un dato ausente es `None`, nunca cero.** Un día sin fila es cero medido solo si la plataforma tuvo ingesta
  ese día.
- **`app/optimizer/` no hace IO.** Las lecturas del caso viven en `app/lecturas_caso.py`.
- **Ningún módulo nuevo construye el cliente de escritura a Amazon.** Hoy lo construyen `app/apply.py` y
  `app/ads/archivar.py`. El regreso del dueño y los ajustes de campaña escriben por `app/apply.py`. El retiro
  de anuncios escribe por `app/ads/archivar.py`.
- **Cada verbo de escritura nuevo entra al allowlist después de su sonda.** `PUT /sp/productAds` y
  `PUT /sp/campaigns` entran a `MUTATION_REQUEST_TYPES` solo con la evidencia de V.0 en el mismo PR.
- **El motor no cambia la campaña.** Presupuesto, estrategia y ajustes por ubicación los cambia el dueño,
  uno por uno, desde la pantalla.
- **Un ajuste por ubicación entra a la `Trayectoria`** de las hojas de esa campaña.
- **La fábrica de cinco campañas no cambia de comportamiento.** Sus pruebas pasan sin tocar aserciones.
- **MX y US nunca se suman.** Un solo mapa de plataforma a moneda.
- **Pantallas en dos commits:** primero el contrato puro con sus pruebas, después la ruta, la plantilla y la
  evidencia.
- **Cada módulo de `app/` tiene 900 líneas como máximo.** Las pantallas nuevas van en módulo propio, no en
  `app/api_dashboard.py`.
- **Una migración por tarea.** Ninguna migración se edita después de mergeada.
- **Cada despliegue aplica todas las migraciones de BIDS 02 que trae su commit** y que no estén aplicadas, en
  orden de número. Así el código de una sección nunca corre en producción sin sus tablas. Producción no tiene
  tabla de versiones: cada migración trae la consulta que dice si ya está aplicada.
- **Telegram sin costo, margen ni target.**

## Convivencia con REPRICING 02

REPRICING 02 está planeado y sin arrancar. Puede correr al mismo tiempo. Dos reglas evitan el choque:

- **Migraciones.** REPRICING 02 reserva de la 0053 a la 0058. BIDS 02 usa de la 0060 a la 0067, una por
  tarea. La 0059 queda libre. Si al aplicar una migración su número ya existe en `origin/master`, toma el
  siguiente libre, cambia el nombre del archivo y de su reversa, y avisa al lead.

  | Migración | Tarea | Sección | Contenido |
  | --- | --- | --- | --- |
  | 0060 | 0.b | 1 | Vistas `v_hoja_activa` y `v_cambio_bid` |
  | 0061 | V.1 | 3 | `ads_campana_config_observation` y su vista vigente |
  | 0062 | V.2 | 3 | `ads_placement_observation` |
  | 0063 | T.1 | 2 | Retiro de los peldaños `cache_estado` y `default` |
  | 0064 | I.1 | 5 | `campana_grupo.tipo` y su candado de roles |
  | 0065 | I.3 | 5 | `impulso`, `impulso_lectura` y los estados nuevos de `fabrica_lote` |
  | 0066 | I.4 | 5 | `anuncio_retiro` y `anuncio_reposicion` |
  | 0067 | V.3 | 4 | `campana_ajuste` y la tercera rama de `v_cambio_bid` |

  `datos.sql` agrupa estas migraciones en cuatro bloques por tema. Esta tabla manda sobre sus números.
- **Archivos compartidos.** `tests/test_architecture.py`, `app/config_write.py`, la ruta de config de
  `app/api_write.py`, `app/templates/settings.html`, `app/static/js/settings.js`, `app/api_dashboard.py`,
  `app/notifica.py`, `verify/Launch.md`, `verify/Doctor.md` y `docs/DEPLOY.md` los editan los dos planes.
  Antes de abrir un PR que toque uno de ellos, rebasa sobre `origin/master`. Los candados nuevos de BIDS 02
  van en `tests/test_arq_bids_<carril>.py`, no en `tests/test_architecture.py`, salvo en la tarea M.3.
  `<carril>` es la letra del id de la tarea, en minúscula.
- **`app/cli.py` tiene 891 líneas y el tope es 900.** Los comandos nuevos viven en `app/cli_bids.py`.
  `app/cli.py` solo gana su despacho.

## Secciones de entrega

Una sección es un grupo de tareas que Muse recibe completo, construye completo, el lead revisa una vez y se
despliega. Los ids de las tareas no cambian. La letra de un id nombra el tema de la tarea, no su sección.

| Sección | Tareas, en orden | Termina con | Depende de | Qué ve el dueño al terminar |
| --- | --- | --- | --- | --- |
| 1. Preparación | 0.a, 0.b, V.0 | Las cuatro sondas con su conclusión | Nada | Nada |
| 2. El motor | T.1, P.3a, M.1, M.2, M.3, M.4, M.5, P.3b, P.5, D.1, X.1 | El motor encendido en MX y US | Sección 1 | El motor deja de recortar sin evidencia y las keywords dañadas vuelven a su bid. Tras D.1 y hasta X.1, el motor no mueve ningún bid; la fracción US 0.8 queda aunque haya rollback (D.1) |
| 3. Ver la campaña | V.1, V.2, P.1, P.2a, V.4, D.2 | Pantalla de dinero y avisos en producción | Sección 1 | El gasto por tipo de campaña, por ubicación y por campaña, y los avisos diarios |
| 4. Ajustar la campaña | V.3, P.2b, D.3 | Los ajustes del dueño en producción | Secciones 1 y 3, y M.2 de la sección 2 | Botones para limitar el gasto fuera de Amazon, cambiar un ajuste por ubicación y cambiar un presupuesto |
| 5. Impulso y estructura | I.1, I.2, I.3, I.4, I.5, P.4, E.1, D.4 | El impulso en producción | Sección 1. P.4 necesita además V.2, de la sección 3 | Puede impulsar productos y leer su veredicto |
| 6. Búsquedas que gastan sin vender | P.6, D.5 | La pantalla de búsquedas en producción | Sección 1 | La lista de páginas de producto ajenas y de palabras que ya gastaron el mínimo sin vender, junto a lo que daría el azar |
| 7. Cierre | C.1 | Filas y documentos cerrados | Todas las demás. Espera a las secciones 2, 4, 5 y 6, que ya incluyen la 1 y la 3 | Nada |

- **Una rama, un árbol de trabajo y un PR de código por sección**, con un commit por tarea: `BIDS 02 <id>:
  <qué hace>`. Las ramas son `bids-02/s1-preparacion`, `bids-02/s2-motor`, `bids-02/s3-ver`,
  `bids-02/s4-ajustar`, `bids-02/s5-impulso`, `bids-02/s6-busquedas` y `bids-02/s7-cierre`.
- **La sección 1 es la excepción.** 0.a es un PR de documentos aparte y se mergea primero, porque todas las
  demás ramas parten de él.
- **Cada sección termina con un PR de cierre**, corto y por el carril `fast`, en la rama
  `bids-02/s<N>-cierre`. Trae la evidencia de producción de la sección y marca sus filas como cerradas.
- **Orden.** Después de la sección 1, las secciones 2, 3, 5 y 6 se construyen en paralelo. La sección 4
  arranca cuando la sección 3 está mergeada, y necesita también M.2, de la sección 2. La sección 5 construye
  P.4 con la sección 3 mergeada, porque P.4 lee la tabla de V.2. La sección 6 depende solo de la sección 1. La
  sección 7 va al final, después de todas las demás.
- **Verificación una vez por sección.** Pruebas focalizadas por tarea mientras se construye. Una ronda de
  revisión sobre el PR completo de la sección. La batería completa una vez, en CI, sobre el commit final de
  ese PR. Los hallazgos se agrupan y se corrigen en una sola ronda, y solo un hallazgo bloqueante con el
  comando que lo reproduce abre otra. El texto de estas reglas está en "Calidad (quality-kit)" de `AGENTS.md`,
  en `origin/master`.
- **Mergear, desplegar y después rebasar.** El despliegue de una sección corre en cuanto su PR se mergea. Un
  despliegue lleva todo lo mergeado. Por eso ninguna sección se mergea mientras otra está mergeada y sin
  desplegar. Ningún despliegue espera a otro, salvo D.3, que va después de D.2 porque la sección 4 se
  construye sobre la sección 3. La sección 1 no tiene despliegue: su código viaja con el primer despliegue que
  corra después.
- **Archivos compartidos entre las secciones 2, 3, 5 y 6.** La guía dice, por archivo, qué agrega cada sección.
  La segunda sección en mergear rebasa y resuelve. Dentro de una sección, el orden es el de sus tareas.
- **"Avisa al lead"** significa escribir el aviso en la descripción del PR, bajo el encabezado `Desviaciones`,
  y en la línea de entrega de la sección. El canal entre claw, el lead y Muse es del loop de claw, no de estos
  documentos.

## Interruptores y reversa

| Qué quieres parar | Cómo | Efecto |
| --- | --- | --- |
| Los bids de una plataforma | Quitar `ads_bid_politica_<plataforma>` en `/settings` | El motor no mueve bids ahí. PAUSE, negative y harvest siguen |
| Todo el motor | `ads_optimizer_mode` a `off` o a `shadow`, como hoy | Sin cambios respecto a hoy |
| El salto del target | Fijar el target en el goal de plataforma, como hoy | El target queda fijo |
| Un impulso | Pausar su lote desde la pantalla | Amazon deja de mostrar sus dos campañas |
| Un producto retirado de una bolsa | "Regresarlo a sus grupos de antes" en la pantalla | Reactiva o repone sus anuncios |
| Un ajuste de campaña | Botón "Regresar" de ese ajuste | Vuelve a la configuración anterior |
| Un bid regresado por error | El motor lo corrige con evidencia nueva | Sin acción |

No hay regreso a `bandas_v1` ni a `evidencia_v2`. Cada despliegue trae `rollback.sh`. Cada migración trae su
`00NN_reversa_<nombre>.sql`. Un `rollback.sh` de D.1 posterior a X.1 corre primero `apagar.sh`, que quita la
clave de política: el código anterior falla cerrado con `niveles_v3`.

## Tareas y dependencias

La evidencia de cada fila va en `docs/evidencia/bids-02/ejecucion/<id>/`. `cc:TODO` significa que no ha
empezado. Una fila cerrada lleva `cc:完了`: lo escribe el PR de cierre de su sección. Muse implementa, el lead
revisa y claw mergea.

Las tablas siguen el orden de las secciones de entrega. Cada pantalla son dos commits: el contrato y la
pantalla. La pantalla de keywords dañadas lleva su contrato y su pantalla como filas separadas, porque otras
tareas dependen de su contrato. Las tablas por ubicación y por campaña llevan sus botones en una fila aparte,
P.2b, porque los botones dependen de V.3.

**Al aprobar este plan, el dueño autoriza:**

- Los merges.
- Las migraciones 0060 a 0067, por el procedimiento sellado.
- Los cinco despliegues, uno por sección de la 2 a la 6.
- Los settings de la tabla de decisiones.
- El regreso de las keywords dañadas al encender (decisión D6).
- Las líneas nuevas de cron.
- Cuatro sondas de escritura en Amazon, cada una sobre una sola entidad dentro de una campaña ya pausada y con
  su regreso: pausar y reactivar un product ad (decisión D8), cambiar y regresar un presupuesto diario,
  cambiar y regresar un ajuste por ubicación, y limitar y regresar el gasto fuera de Amazon. La sonda de
  presupuesto corre una vez en cada país, para medir el presupuesto diario mínimo que Amazon acepta.

Nadie le pide permiso ni un go al dueño: se le avisa de cada evento.

**No autoriza, porque son acciones del dueño en la pantalla:** lanzar un impulso, sacar productos de una
bolsa compartida y aplicar un ajuste de campaña.

En producción solo escriben los scripts de un PR aprobado y mergeado. Los corre Muse cuando claw se lo
encarga, y el lead comprueba el resultado en solo lectura.

### Sección 1: preparación

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 0.a | `[stage:cierre-pr] [lane:fast] [tdd:skip:docs]` Incorporar al repo el diseño, su evidencia, este plan y su guía. Fila en `plans/manifest.json` y en `plans/ROADMAP.md`. | Los archivos de la tabla "Qué incorpora 0.a" de la guía existen en `origin/master`. Los datos crudos de producción no entran al repo. Los scripts de evidencia entran formateados y con `# ruff: noqa`. `pre-commit run --all-files` pasa. | - | cc:完了 |
| 0.b | `[stage:implementacion] [lane:gate] [tdd:required]` Migración 0060: vistas `v_hoja_activa` y `v_cambio_bid`. Agregar `tests/test_arq_bids_*.py` al paso de guardas del job `rapido`. El lector `gasto_para_concluir_desde_settings`. | `v_hoja_activa` coincide con la consulta de control de la guía sobre la misma copia de producción. `v_cambio_bid` devuelve el regreso del dueño como un cambio. `tests/test_schema_docs.py` pasa. La reversa deja el esquema como estaba. Un candado sembrado en `tests/test_arq_bids_prueba.py` falla en el job `rapido`. El lector da 350 MXN y 36 USD con la clave ausente y falla cerrado con un valor inválido. | 0.a | cc:完了 |
| V.0 | `[stage:verificacion] [lane:gate] [tdd:skip:sonda]` Cuatro sondas de escritura. Cada script vive en `tools/` y toca una sola entidad de la campaña pausada `A1U - Auto Discovery - US`. La de presupuesto corre además sobre una campaña pausada de MX que el script elige por lectura. | `ejecucion/V.0/` con la salida literal de cada script y su conclusión: el cuerpo que Amazon acepta, lo que responde, si un cambio parcial de `placementBidding` reemplaza la lista entera, qué valor candidato de `offAmazonSettings` aceptó Amazon y el presupuesto diario mínimo de cada país. Cada entidad queda como estaba, comprobado por lectura. La única excepción es la sonda de gasto fuera de Amazon: si Amazon no deja regresar a `{}`, queda el valor que minimiza el gasto y se reporta como desviación. Si una sonda falla, su conclusión es "sin sellar". | 0.a | cc:完了 |

### Sección 2: el motor

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| T.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Paso asimétrico en `resuelve_target_margen` y migración 0063 que retira los peldaños `cache_estado` y `default`. | Un derivado mayor que el ancla aplica de una vez. Uno menor baja 0.5 por ciclo. Sin target resuelto, cada llamador de la cascada trata el `None`: la hoja y su ad group se saltan con motivo `sin_target`, `app/apply_cola.py` conserva su revalidación, y `/settings`, `/campanas` y las propuestas de campaña dicen "sin target". Antes de aplicar la 0063, la consulta de la guía confirma que ninguna fila usa los dos peldaños que salen. `tests/test_cycle_target_ciclo.py` pasa. | 0.a | cc:TODO |
| P.3a | `[stage:implementacion] [lane:gate] [tdd:required]` Contrato de "Keywords dañadas": `app/pantalla_danadas.py` con `lee_danadas`. | La lista coincide con la consulta de control de la guía sobre la misma copia de producción. Cada fila trae lo que vendía, el bid de antes y el de hoy. | 0.b | cc:TODO |
| M.1 | `[stage:implementacion] [lane:gate] [tdd:required]` `app/optimizer/caso.py` y `app/optimizer/politica.py`, puros. No tocan `app/cycle.py`. | La prueba dorada decide una muestra de a lo más 300 casos y 500 KB, exportada a JSON con `como_json`, y da el veredicto del prototipo en cada caso. La muestra trae todos los casos que la política recorta, sube o regresa. Muse corre una vez la reproducción completa de `prototipos/s1_salida.txt`, 6 recortes de 405 en MX, contra los datos del prototipo, y guarda la salida en la evidencia. Esa corrida no es una prueba de CI. `desde_json(como_json(caso)) == caso` para cada caso. Cada regla R1 a R19 tiene un caso que la dispara y un mutante que muere. Con la historia simulada, la hoja 2963 no recibe ningún cambio. `OrigenCambio` incluye `ajuste_de_campana`. | 0.b | cc:TODO |
| M.2 | `[stage:implementacion] [lane:gate] [tdd:required]` `app/lecturas_caso.py`: `lee_plataforma` y `LecturasPlataforma.caso`. | Cuatro consultas por plataforma y una más por hoja con cambio de bid en 90 días. Con `visto_el`, la lectura reproduce lo que el ciclo veía ese día. Un día sin fila y con ingesta es cero. Un día sin ingesta es `None`. La lectura de toda la plataforma tarda menos de 1 s sobre la copia de producción. | M.1 | cc:TODO |
| M.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Cablear `app/cycle.py`, el interruptor, `app/optimizer/eras.py` y el despacho de `app/optimizer/replay.py`. Sustituir en `app/apply.py` el orden de aplicación bajo cupo por `prioridad_bajo_cupo`. Borrar lo que lista la sección 1 de `borrado.md`, salvo las filas que son de T.1 y de I.1. Conservar el cooldown de la PAUSE. Dejar que `/settings` escriba `niveles_v3` y quite la clave. | Con la clave ausente, el ciclo no emite ningún bid y cuenta cada hoja con motivo `politica_apagada`. Con `niveles_v3`, cada decisión guarda `inputs.caso` y el replay la reproduce. Con otro valor, el ciclo falla cerrado. La prueba dorada de replay de las filas históricas pasa con `eras.py`. `bid.py` ya no exporta `fallback_v1` ni `politica_bandas`. `app/apply_cola.py` revalida una pausa con el bloque de PAUSE que queda en `bid.py`. Después de que el dueño regresa una keyword, el motor espera 7 días, después pide 20 clics al bid nuevo o 14 días antes de un recorte, y nunca recorta hasta el bid que la dañó ni por debajo de él. Una hoja con una PAUSE aplicada hace menos de 7 días se salta con `cooldown_7d`, como hoy. `/settings` guarda `niveles_v3`, quita la clave y rechaza otro valor con 422. Con el cupo diario de bids lleno, el orden de aplicación es este: regresos por desplome, recortes con evidencia propia, subidas y recortes heredados del ad group. Dentro de cada nivel va primero el de más gasto. Una prueba demuestra que un regreso pasa antes que un recorte y que un recorte heredado pasa al final. La mediana del ciclo sobre la copia de producción, medida con la política en `niveles_v3`, no pasa de la de `origin/master` más 30 %. | M.2, T.1 | cc:TODO |
| M.4 | `[stage:implementacion] [lane:gate] [tdd:required]` `tools/rejuega_niveles.py` y su `InformeRejuego`. Se corre con `PYTHONPATH=. python tools/rejuega_niveles.py --platform <plataforma> --ciclos 30`. | El rejuego de 30 ciclos corre el mismo día, sin esperar ciclos nuevos. `cumple()` es falso si se rompe cualquiera de sus cuatro criterios. El informe lista las hojas con ventas que recortaría. | M.3, P.3a | cc:TODO |
| M.5 | `[stage:implementacion] [lane:gate] [tdd:required]` Regreso del dueño: `apply.regreso_del_dueno`, `regreso_del_dueno_todas` y sus rutas `POST /api/ads-optimizer/bid/regresar` y `POST /api/ads-optimizer/bid/regresar-todas`. | Regresar una hoja escribe en Amazon el bid anterior a su racha de recortes y aparece en `v_cambio_bid` con origen `regreso_del_dueno`. Regresar dos veces la misma racha responde 409 y no escribe. "Regresar todas" con una confirmación `REGRESAR <N> KEYWORDS` cuyo N no es el vigente responde 409. Una falla no detiene a las demás. Las dos rutas están en la lista sellada de `tests/test_api.py`. | 0.b, P.3a | cc:TODO |
| P.3b | `[stage:implementacion] [lane:gate] [tdd:required]` Pantalla de "Keywords dañadas", con el botón de una hoja y el botón "Regresar todas". | Una fila ya regresada muestra la fecha y no ofrece el botón. "Regresar todas" pide una sola confirmación escrita. | P.3a, M.5 | cc:TODO |
| P.5 | `[stage:implementacion] [lane:gate] [tdd:required]` "Ruido": `app/pantalla_ruido.py`. | Por ciclo, decisiones y abstenciones por motivo. Por hoja, cuánto se encogió su bid en 90 días. La comparación de antes y después del encendido separa MX de US y dice que no es causal. | M.3 | cc:TODO |
| D.1 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar la sección 2 antes de las 08:40 UTC y escribir la fracción de US en 0.8. Lo corre Muse. | El checklist sale 0. La clave de política está ausente en las dos plataformas. La fracción de US es 0.8 antes del ciclo de las 08:40 UTC. Ese ciclo no emite ningún bid y sí decide PAUSE, negative y harvest como antes. Las pantallas de dañadas y de ruido responden 200. | T.1, P.3a, M.1 a M.5, P.3b, P.5 | cc:TODO |
| X.1 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Encender el motor en MX y en US, el mismo día que D.1 y después del ciclo de las 08:40 UTC. | Criterios de encendido de abajo. | D.1 | cc:TODO |

### Sección 3: ver la campaña

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| V.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Guardar la configuración de campaña en el sync: `app/ads/campana_config.py` y migración 0061. | Tras un sync, cada campaña tiene presupuesto con moneda, estrategia, sus tres ajustes por ubicación y su configuración de gasto fuera de Amazon. Una fila nueva solo si algo cambió. Ningún payload de Amazon sale de `config_de_payload`. El conteo de tablas de `verify/Launch.md` y `verify/Doctor.md` cuadra. | 0.a | cc:TODO |
| V.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Reporte por placement en corrida propia: `app/ads/placements.py`, migración 0062 y la bandera `--placements` de `ingest metrics`. | El reporte se pide con `timeUnit DAILY` y sin `topOfSearchImpressionShare`. Un fallo suyo no afecta a los otros reportes. Los cuatro valores de `placementClassification` se guardan como dominio. Una segunda corrida del mismo día no duplica. | 0.a | cc:TODO |
| P.1 | `[stage:implementacion] [lane:gate] [tdd:required]` "Dónde poner el dinero", tabla por tipo de campaña: `app/pantalla_dinero.py`. | La tabla coincide con la consulta de control de la guía sobre la misma copia de producción. Solo cuenta lo activo. MX y US no se suman. Una fila sin venta dice "sin ventas", no 0 %. | 0.b | cc:TODO |
| P.2a | `[stage:implementacion] [lane:gate] [tdd:required]` Contrato y pantalla de las tablas por ubicación y por campaña, de solo lectura: `FilaUbicacion`, `FilaCampana`, sus lecturas y sus dos tablas en la pantalla de dinero. Sin botones. | La fila "Fuera de Amazon" se marca cuando gastó el gasto para concluir sin un pedido. La fila de campaña trae presupuesto, uso, estrategia en palabras y ajustes. Las lecturas coinciden con la consulta de control de la guía. La pantalla de dinero pinta las dos tablas con sus frases y no trae ningún botón de ajuste. Un dato `None` se pinta con el guion. | P.1, V.1, V.2, 0.b | cc:TODO |
| V.4 | `[stage:implementacion] [lane:gate] [tdd:required]` Avisos diarios: `app/avisos_campana.py`, su comando `avisos-campana` y su bloque en `/salud`. | Los tres avisos de la guía salen una vez por plataforma, clase y día, también si el comando corre dos veces. Ningún texto trae costo, margen ni target. Un fallo del envío no tumba la corrida. | P.2a, 0.b | cc:TODO |
| D.2 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar la sección 3, con las líneas de cron del reporte por placement y de los avisos. | El checklist sale 0. Tras el siguiente sync cada campaña activa tiene configuración. El primer reporte por placement trae filas. La pantalla de dinero responde 200 y muestra las tablas por ubicación y por campaña. | V.1, V.2, P.1, P.2a, V.4 | cc:TODO |

### Sección 4: ajustar la campaña

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| V.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Ajustes del dueño: `app/campana_ajustes.py`, `apply.aplica_ajuste_campana`, `regresa_ajuste_campana`, migración 0067 y sus rutas. Solo los ajustes cuya sonda quedó sellada en V.0. | `planea_ajuste` es pura y rechaza un ajuste que no cambia nada. La fila se inserta antes del HTTP y se confirma solo si el readback coincide. Una huella vieja responde 409. Un ajuste por ubicación confirmado aparece en `v_cambio_bid` y en la `Trayectoria` de cada hoja de la campaña. Regresar aplica la configuración anterior completa. `PUT /sp/campaigns` entra al allowlist con la evidencia de V.0 en el mismo PR. | V.0, V.1, M.2 | cc:TODO |
| P.2b | `[stage:implementacion] [lane:gate] [tdd:required]` Botones de la tabla por campaña: un botón por cada ajuste sellado, con su vista previa y su confirmación, el botón "Regresar" de un ajuste confirmado y los avisos de cada campaña. | Cada botón abre su vista previa y no escribe sin la confirmación. Un ajuste sin sonda sellada no tiene botón. Un ajuste confirmado y sin regreso muestra su botón "Regresar". La fila de campaña muestra sus avisos. | P.2a, V.3, V.4 | cc:TODO |
| D.3 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar la sección 4. | El checklist sale 0. La pantalla de dinero muestra un botón por cada ajuste sellado. La vista previa de un ajuste responde sin escribir. La tabla `campana_ajuste` existe y está vacía. | V.3, P.2b, D.2 | cc:TODO |

### Sección 5: impulso y estructura

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| I.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Grupo de dos campañas en la fábrica: `roles` en el plan canónico, migración 0064 con `campana_grupo.tipo` y su candado de roles, y el roster de hermanas vacío. | Las pruebas de la fábrica de cinco campañas pasan sin tocar aserciones. Un harvest en un grupo `impulso` crea la keyword en la exact del mismo producto y no la revierte. Un tercer rol en un grupo `impulso` lo rechaza la base. | 0.a | cc:TODO |
| I.2 | `[stage:implementacion] [lane:gate] [tdd:required]` `app/impulso.py`, puro: plan, tope y veredicto. | El presupuesto diario es el tope entre 14, y la mitad va a cada campaña: 12.50 MXN o 1.29 USD con los números de hoy. Un bid sugerido mayor que el presupuesto diario de su campaña baja a ese presupuesto. `veredicto_impulso` pausa cuando faltan dos presupuestos diarios para el tope. Con algún dato `None` sigue en curso. Los cuatro veredictos finales tienen su caso. La huella cambia si cambia cualquier campo del plan. | I.1 | cc:TODO |
| I.3 | `[stage:implementacion] [lane:gate] [tdd:required]` `app/impulso_io.py`: vista previa, lanzamiento y vigía, con la migración 0065 y el comando `impulso-vigia`. | La vista previa no escribe. Un producto que falla al lanzar no detiene a los demás. Las campañas nacen con estrategia solo hacia abajo y sin ajustes por ubicación. Nacen con la configuración de gasto fuera de Amazon por defecto de Amazon: el payload no trae `offAmazonSettings`. La vista previa dice qué bid bajó al presupuesto diario de su campaña. El vigía corrido dos veces el mismo día no duplica la lectura. Los goals nacen en `shadow` y pasan a `live` al graduar. | I.1, I.2, 0.b, V.0 | cc:TODO |
| I.4 | `[stage:implementacion] [lane:gate] [tdd:required]` `app/retiro_anuncios.py`: sacar un producto de sus bolsas compartidas y regresarlo, con la migración 0066. | El plan de retiro lista cada anuncio y no escribe. El retiro ocurre solo después de que el impulso recibe impresiones. `anuncio_retiro` guarda si el retiro fue una pausa o un archivado. Regresar deja al producto anunciado en los mismos ad groups. Un anuncio con un retiro abierto, sin reponer todavía, no se retira otra vez. Un anuncio ya repuesto se puede retirar de nuevo. `tests/test_ads_write.py` pasa con la superficie de escritura y el allowlist que deja este paso. | I.3, V.0 | cc:TODO |
| I.5 | `[stage:implementacion] [lane:gate] [tdd:required]` Rutas del impulso en `app/api_fabrica.py`. | Sin token, 401 antes de abrir la conexión admin. La vista previa no escribe. Lanzar con una huella vieja responde 409 con el plan nuevo. Sin la confirmación literal, 422. | I.3, I.4 | cc:TODO |
| P.4 | `[stage:implementacion] [lane:gate] [tdd:required]` "Productos que venden y casi no reciben clics": `app/pantalla_productos.py`, con candidatos, impulsos en curso y la sección de 11 meses sin pedido, que ofrece "Sacar de las bolsas compartidas" e "Impulsar aparte". | Los candidatos coinciden con la consulta de control de la guía. Un impulso en curso muestra día, impresiones, clics, gasto y veredicto, y cuántos de sus clics vinieron de fuera de Amazon. "Impulsar aparte" abre el mismo flujo de impulso con esos productos marcados. La pantalla dice cuántos anuncios no tienen producto ligado. | I.5, V.2 | cc:TODO |
| E.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Botón "Sacar estos productos de sus bolsas viejas" al final de la pantalla de la fábrica, y la acción "Sacar de las bolsas compartidas" de la sección de 11 meses. | El botón usa `retiro_anuncios` y la misma confirmación literal. Los productos elegidos salen solo de los ad groups que no son del grupo nuevo. | I.4, P.4 | cc:TODO |
| D.4 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar la sección 5, con la línea de cron de `impulso-vigia`. | El checklist sale 0. La vista previa de un impulso de un producto candidato responde sin escribir. El vigía corre sin impulsos y sale 0. | I.1 a I.5, P.4, E.1 | cc:TODO |

### Sección 6: búsquedas que gastan sin vender

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| P.6 | `[stage:implementacion] [lane:gate] [tdd:required]` "Búsquedas que gastan sin vender": `app/pantalla_busquedas.py` y su pantalla de solo lectura. No escribe nada en Amazon y no propone nada a la cola de veto. | La frase de referencia muestra la parte del gasto vista y la parte que daría el azar. No muestra ninguna de las dos si la cuenta no tiene pedidos o no tiene clics en la ventana. Un ASIN entra solo con cero pedidos y con un gasto igual o mayor al gasto para concluir, sumado en toda la cuenta. Un ASIN propio se marca, no se esconde. Una palabra entra solo si ninguna búsqueda que la contiene tuvo un pedido. Las palabras vacías se quitan solo de las entradas de una palabra. Una métrica `None` no cuenta como cero. Solo cuentan las campañas activas. MX y US no se suman. Las listas coinciden con la consulta de control de la guía sobre la misma copia de producción. Referencia del 2026-10-09, de `prototipos/p7_salida.txt`: en MX, ningún ASIN, la palabra «sin» y las frases «cofre para», «estuche para» y «boda sin». En US, 2 ASIN, «box» y «coins catholic». | 0.b | cc:TODO |
| D.5 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar la sección 6. No trae migración propia ni línea de cron. | El checklist sale 0. La página de búsquedas responde 200 en MX y en US. | P.6 | cc:TODO |

### Sección 7: cierre

Esta sección corre al final, después de todas las demás.

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| C.1 | `[stage:cierre-pr] [lane:fast] [tdd:skip:docs]` Cierre: filas, `plans/ROADMAP.md`, `docs/CHAT-CONTEXT.md`, `docs/CONTEXTO.md` y `docs/DEPLOY.md`. | El motor está encendido en las dos plataformas, o cerrado con el motivo escrito. Las cinco pantallas responden. Las filas AUTO-03 a AUTO-06 del roadmap dicen qué cubrió este plan y qué no. | X.1, D.3, D.4, D.5 | cc:TODO |

Los números de control del 2026-10-09 están en `PRUEBAS.md` y en `prototipos/RESULTADOS.md`: 355 y 233 hojas
activas, 6 y 3 keywords dañadas, 10 y 5 candidatos. Sirven de referencia. Una copia de producción posterior da
otros números, y por eso cada DoD compara contra una consulta de control sobre la misma copia.

## Criterios de encendido

El motor se enciende el mismo día que se despliega (D5). No hay ciclo en sombra ni ventana de medición.

La clave `ads_bid_politica_<plataforma>` no existe hoy en producción. D.1 corre antes de las 08:40 UTC. El
ciclo de las 08:40 corre con la clave ausente y no mueve ningún bid: ese ciclo es el que observa el DoD de
D.1. X.1 corre el mismo día, después de ese ciclo. El primer ciclo con la política encendida es el del día
siguiente.

1. **Desplegar ya detiene los recortes.** Con la clave ausente, el motor no mueve bids. Si algo de lo que
   sigue falla, el motor se queda así y nada se daña.
2. **Rejugar el último mes.** Muse corre `tools/rejuega_niveles.py` para cada plataforma sobre los últimos
   30 ciclos. Pasa si `cumple()` es verdadero:
   - Volver a decidir cada caso da el mismo veredicto, en todas las filas. Antes del encendido no hay casos
     guardados: el rejuego arma cada caso, lo pasa por `como_json` y `desde_json`, decide los dos y exige
     veredictos iguales.
   - Ningún recorte sale de una regla que no sea R3, R4, R9 o R11, y ninguno viola R2, R15, R16 ni R17.
   - Al menos 95 % de las hojas elegibles tienen su caso completo. Una hoja elegible pasa los filtros que no
     cambian: goal habilitado, campaña, ad group y hoja `ENABLED`, sin veto pendiente y no inerte.
   - Ninguna keyword de la pantalla de dañadas vuelve a recortarse.

   Para cada ciclo pasado, el rejuego usa el target que ese ciclo congeló en `target_acos_ciclo`, el bid de
   ese día, el piso y el techo de hoy del goal, y los insumos de pausa leídos como se veían ese día.
3. **Avisar al dueño lo que haría.** claw le manda el número de recortes y de subidas del mes rejugado por
   plataforma, y la lista de hojas con ventas que el motor recortaría. Es un aviso. No espera respuesta.
4. **Comprobar la fracción de US.** D.1 escribió `ads_target_fraccion_margen_amazon_us = 0.8` antes del primer
   ciclo posterior al despliegue. Con el código de T.1 y la fracción anterior, el target de US saltaría a
   28.34 % en su primer ciclo, y bajar camina 0.5 por ciclo. Muse comprueba en solo lectura que la fracción es
   0.8.
5. **Encender.** Muse escribe `ads_bid_politica_amazon_mx = niveles_v3` y `ads_bid_politica_amazon_us =
   niveles_v3` por la ruta de config de `/settings`. Los goals conservan su modo.
6. **Regresar las keywords dañadas.** Muse llama a `POST /api/ads-optimizer/bid/regresar-todas` para cada
   plataforma, con la confirmación `REGRESAR <N> KEYWORDS` del N vigente y el actor `plan bids-02 X.1`. El
   2026-10-09 eran 6 en MX y 3 en US.
7. **Leer el primer ciclo.** A la mañana siguiente el lead comprueba en solo lectura que el ciclo guardó
   `inputs.caso`, que el target de US es 25.19 % y que ningún recorte salió de una regla prohibida. Esta
   lectura no frena nada.

Si el paso 2 falla en una plataforma, esa plataforma no se enciende. Muse entrega el motivo medido, claw
avisa al dueño y la otra plataforma sigue.

## Pruebas que deben discriminar

Cada fila registra un mutante por prueba nueva: el cambio de una línea que la pone en rojo. Estos mutantes
son obligatorios y deben morir:

- R9 recorta con un gasto menor al gasto para concluir.
- Un `Mantener` cualquiera se convierte en un recorte.
- R4 recorta 25 % en vez de 12 %.
- R11 recorta una hoja con gasto menor a un cuarto del gasto para concluir.
- R1 regresa sin el filtro de volumen.
- R1 regresa con una razón de tráfico de 0.30 o más.
- R15 deja repetir un recorte sin 20 clics nuevos.
- R16 deja un segundo recorte cuando el primero dejó menos de 0.70 del tráfico.
- R17 recorta por debajo del piso aprendido.
- `decide` lee un valor que no está en el caso.
- `CasoHoja.desde_json` acepta un JSON sin una clave.
- Con la clave de política ausente, el ciclo mueve un bid.
- Con un valor de política desconocido, el ciclo sigue en vez de fallar.
- Con el cupo lleno, un recorte heredado del ad group pasa antes que un regreso.
- `v_cambio_bid` ignora la reversa del dueño.
- Un ajuste por ubicación confirmado no llega a la `Trayectoria`.
- Una subida del target camina 0.5 en vez de aplicar de una vez.
- Una bajada del target aplica de una vez.
- El vigía pausa cuando falta un solo presupuesto diario.
- `veredicto_impulso` da un veredicto final con un dato `None`.
- El roster de hermanas de un grupo `impulso` trae campañas.
- `aplica_ajuste_campana` escribe con una huella vieja.
- `aplica_ajuste_campana` confirma la fila sin readback.
- El retiro saca un anuncio antes de que el impulso reciba impresiones.
- La pantalla de dinero suma MX con US.
- La pantalla de dinero cuenta una campaña apagada.
- Una métrica `None` se pinta como 0.
- Un aviso de campaña sale dos veces el mismo día.
- Una palabra entra aunque una búsqueda que la contiene tuvo un pedido.
- Un ASIN propio se esconde en vez de marcarse.
- La pantalla de búsquedas cuenta una campaña apagada.

## Orden y salida

Orden: la sección 1 va primero, con 0.a antes que 0.b y V.0. Después se construyen en paralelo las secciones
2, 3, 5 y 6. La sección 4 arranca con la sección 3 mergeada y con M.2 en `origin/master`. La sección 7 cierra,
después de todas las demás.

El camino más corto al encendido son las secciones 1 y 2. Las secciones 3, 4, 5 y 6 no lo frenan.

El plan cierra cuando el motor está encendido en las dos plataformas, las cinco pantallas responden y el
impulso y los ajustes de campaña están desplegados, o cuando lo que falte quedó cerrado con el motivo
escrito. Lanzar el primer impulso y aplicar el primer ajuste son acciones del dueño y no son condición de
cierre.
