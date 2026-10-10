# REPRICING 02: el motor de precios en todas las plataformas

Estado: plan del 2026-10-08 UTC. El diseño se terminó el mismo día. Nada está
construido. Base de planificación: commit `d83bb28`. Dos lectores frescos
ejecutaron este plan y su guía. Sus hallazgos están en
`docs/evidencia/repricing-02/planificacion/lectores-frescos.md`. El 2026-10-09
las tareas se agruparon en seis secciones de entrega, sin cambiar ninguna
tarea: están en "Secciones de entrega".

**Resultado:** cada publicación activa de Amazon México (FBA y FBM), Amazon
Estados Unidos y Mercado Libre tiene un goal de margen y el motor la lleva a ese
goal moviendo el precio. El dueño fija los goals en pantalla. Un apagador, un
fusible y un resumen diario reemplazan al cupo.

**Diseño:** [diseño de REPRICING 02](../docs/superpowers/specs/2026-10-08-repricing-02-design.md),
con su [bosquejo de tipos](../docs/evidencia/repricing-02/diseno/bosquejo.py) y
su [modelo de datos](../docs/evidencia/repricing-02/diseno/datos.sql). El diseño
dice qué se construye. Este plan dice en qué orden, quién toca qué archivo y cómo
se comprueba cada pieza. El paso a paso está en
[la guía de construcción](repricing-02-construccion.md).

**Precedencia:** `docs/CONTEXTO.md` > diseño de REPRICING 02 > este plan > guía de
construcción. Hay una excepción: el orden de construcción lo fija este plan. El
diseño describe la forma final de la corrida, y el plan llega a ella en dos
pasos (0.b y S.1). Si dos documentos se contradicen en otra cosa, sigue el de
mayor precedencia y avisa al lead.

**Sustituye** a las filas abiertas de [REPRICING 01](repricing-01.md): D.2, D.3 y
las fases E, 0, B y M. Lo que REPRICING 01 cerró sigue en pie: las reglas puras,
la escritura y la reversa en Amazon, la migración 0039, la sonda A.4 y la sombra
de MX FBA.

**Fuera de alcance:** promociones, kits, perseguir la Buy Box, goals por variante
en Mercado Libre y la reversa automática.

## Decisiones que gobiernan este plan

Las diez decisiones del dueño del 2026-10-08 (D1 a D10) están en la sección
"Decisiones del dueño" del diseño. Este plan no las repite.

El diseño dejó preguntas abiertas. Ninguna frena la construcción. El plan usa
estos valores hasta que el dueño diga otra cosa:

| Tema | Valor que usa el plan | Dónde se cambia |
| --- | --- | --- |
| Tope de la banda de goals | 60 %. Un producto con margen de hoy fuera de 10 a 60 % se lista aparte y no se siembra solo | CHECK `precio_goal_banda` y `precio_goal_max_pct` |
| Regla de envío por orden | La etiqueta una vez, por cualquiera de sus tres caminos, más `ShippingHB` | `app/precio/envio.py::costo_de_orden` |
| Soltar el fusible | Los precios se mueven en el siguiente repaso, hasta dos horas después | Cron de repasos |
| Publicación de Mercado Libre con variantes | Manda la variante que necesita el precio más alto | `puerto.miembro_que_manda` |
| Variante de Mercado Libre sin producto | La publicación completa queda sin evaluar, con el SKU a la vista | `MercadoMeli.insumos` |
| Reversa en lote | Aborta si algún universo del lote está en `live` | `cambios.revertir_lote` |
| Envío imputado en `live` | Permitido, con el origen visible | Sin interruptor |
| Señal por tráfico | Solo después de una subida propia | `ventas.evaluar_senal` |
| Goal vigente al sembrar con la herramienta | Se reemplaza, igual que en pantalla | `siembra.planear` |
| Pasar un universo a `live` | No pide go literal propio. El go literal vive en cada goal | `/settings` |

## Políticas por universo

Cada universo nuevo necesita una fila en `estimacion_politica_version`. La
inserta el script del despliegue, con el rol `app_admin` y los valores de esta
tabla. Este plan es la confirmación del dueño.

| Universo | `iva_divisor` | `precio_incluye_iva` | `isr_tasa` | Envío | Edad máxima del tipo de cambio |
| --- | --- | --- | --- | --- | --- |
| `amazon_mx/fba` (existe) | 1.16 | sí | 0.025 | 0, va dentro de los fees | no aplica |
| `amazon_mx/fbm` | 1.16 | sí | 0.025 | de la muestra | no aplica |
| `amazon_us/fbm` | 1 | no | 0.0207 | de la muestra, convertido a USD | 3 días |
| `meli/meli` | 1.16 | sí | la mide M.2 del ledger | de la muestra | no aplica |

Los valores de `amazon_mx/fbm` copian la política vigente de FBA. El 0.0207 de
Estados Unidos es la retención medida en REPRICING 01. Los demás campos de
`settings` copian los de la política `amazon_mx/fba`.

## Tamaño medido

Lecturas de producción con `orbit_read`. Fuentes: `docs/evidencia/repricing-01/`
(A.7, E.1 y D.2), `docs/evidencia/reputacion-01/0.1/` para Mercado Libre, y
`precio --reporte` del 2026-10-01 al 2026-10-07.

| Dato | Amazon MX | Amazon US | Mercado Libre |
| --- | ---: | ---: | ---: |
| Publicaciones activas | 260 | 100 | 65 en la API |
| De esas, FBA | 158 | 0 | no aplica |
| De esas, sin canal conocido. Se asumen FBM | 102 | 100 | no aplica |
| Con goal hoy | 6, todos `shadow` | 0 | 0 |
| Productos con al menos 6 envíos propios en 180 días | 14 | 17 | sin medir |
| Productos con 1 a 5 envíos propios | 94 | 36 | sin medir |
| Publicaciones con varias variantes | no aplica | no aplica | 48 de 65 |

El motor corre una vez al día para `amazon_mx`. Del 2026-10-01 al 2026-10-07
decidió 6 productos por día. No ha escrito ningún precio fuera de la sonda A.4.

## Contratos

El revisor comprueba estas reglas en cada fila. Vienen del diseño.

- **La 0054 convive con el código que ya corre.** No exige ninguna columna
  nueva. Cada CHECK que exige una columna llega en la migración que acompaña al
  código que la escribe: la 0058 con S.1 y la 0055 con F.3.
- **Un camino de escritura por plataforma.** `app/spapi/write_client.py` y
  `app/meli/write_client.py` son las únicas puertas. Los clientes de lectura no
  ganan verbos de escritura.
- **Un escritor por tabla.** `goals_write.py` escribe `precio_goal`.
  `liberaciones.py` escribe `precio_compuerta_liberacion`. La corrida escribe
  `precio_corrida`, `precio_compuerta_medicion` y `precio_retencion`.
- **El protocolo de `precio_cambio` se escribe una vez**, en
  `app/precio/cambios.py`. El orden no cambia: INSERT, COMMIT, escritura, sello.
- **El motor no conoce plataformas.** `corrida.py`, `cambios.py` y `fuentes.py` no
  importan `app.spapi`, `app.meli`, `app.estimacion_fees` ni `httpx`, y no leen
  tablas `spapi_*`.
- **Un margen por producto.** El escenario lo produce la estimación. El puerto lo
  lee.
- **Un envío imputado no se confunde con uno medido.** Todo `L` nuevo dice su
  origen en la muestra, en el escenario y en la decisión.
- **Una sola moneda por cuenta.** `Cuenta` no se construye con monedas mezcladas.
  `Convertido` es el único tipo que cruza monedas.
- **Sin insumo, sin decisión.** `no_evaluado(motivo)` es una fila.
- **La decisión guarda el goal que leyó.** La base valida `goal_id`.
- **Retener no reescribe la decisión.** Una decisión `subir` retenida sigue siendo
  `subir`. `precio_retencion` dice qué corrida no la aplicó.
- **Reversa antes y nunca automática**, por plataforma.
- **Cobertura que cuadra exacta** por plataforma, en cada corrida.
- **Telegram sin costo, margen ni goal.**

## Interruptores y reversa

| Qué quieres parar | Cómo | Efecto |
| --- | --- | --- |
| Todo el motor | `precio_modo_global` a `off` en `/settings` | La corrida limpia y sale sin decidir |
| Un universo | Su entrada de `precio_modo_universo` a `off` o `shadow` | Sus decisiones no se aplican, o se aplican como virtuales |
| Un producto | Goal a `shadow`, o cerrar el goal | Deja de escribir ese día, también lo que estaba retenido |
| Una corrida en curso | Cualquiera de los tres de arriba | La corrida relee la config y el goal antes de cada escritura |
| Precios ya movidos | `PYTHONPATH=. python tools/precio_reversa.py --platform <p> --fecha <día>` | Revierte el lote, el mismo día |

Los tres primeros existen desde S.2. Antes de S.2 el único interruptor es el
modo de cada goal. Cada despliegue trae `rollback.sh`. Cada migración trae su
`00NN_reversa_<nombre>.sql`.

## Tareas y dependencias

La evidencia de cada fila va en `docs/evidencia/repricing-02/ejecucion/<id>/`.
`cc:TODO` significa que no ha empezado. Muse implementa, el lead revisa y claw
mergea.

**Este plan es la autorización completa del dueño (2026-10-08).** Cubre los
merges, las migraciones, los cuatro despliegues, las filas de política, la
siembra de goals, las sondas de escritura de un centavo con su reversa y el paso
de cada universo y de sus goals a `live`. Nadie le pide permiso ni un go al
dueño: se le avisa de cada evento.

En producción solo escriben los scripts de un PR aprobado y mergeado. Los corre
Muse cuando claw se lo encarga, y el lead comprueba el resultado en solo
lectura. El go literal de cada goal lo escribe el script de su fila:
`plan repricing-02 X.1`, `plan repricing-02 X.2`, `plan repricing-02 X.3` o
`plan repricing-02 X.4`.

### Corte 0: separar lo compartido

Los dos pasos van en fila. Después de 0.a arrancan F, M y G. Después de 0.b
arranca S.

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 0.a | `[stage:implementacion] [lane:gate] [tdd:required]` Contratos, sin cambio de comportamiento: migraciones 0053 y 0054, `puerto.py`, tipos nuevos, ayudantes únicos de config, los dos registros con módulos vacíos, candado de imports, `/precios` partido en parciales y los candados por carril en el job `rapido`. | La batería con base pasa sin saltos y sin tocar aserciones. Con la 0054 aplicada, el INSERT de decisión y el de escenario que hace el código de hoy siguen entrando. `ensayo.sh` aplica la 0053 y la 0054 sobre una copia con filas de producción. `tests/test_precio_corrida.py` y `tests/test_precio_pantalla.py` pasan sin cambios. | - | cc:TODO |
| 0.b | `[stage:implementacion] [lane:gate] [tdd:required]` Poner la corrida detrás del `Mercado`, con el orden de hoy: `MercadoAmazon`, `cambios.py`, `leer_cuenta`, cobertura sobre `Vitrina`. La decisión empieza a guardar `goal_id`, `l_origen` y `verificacion`. | `comparar.sh` da `diff` vacío entre el commit de 0.a y el de 0.b sobre la lista de columnas de la guía, con una fila por goal vigente decidida por esa corrida y al menos una con cuenta. El mismo script sale en rojo con el lado nuevo roto a propósito. `tests/test_precio_corrida.py`, `tests/test_precio_write.py` y `tests/test_precio_cobertura.py` conservan sus aserciones y cambian solo armado, imports y nombres. La compuerta de la guía sobre imports y lecturas `spapi_*` sale vacía. | 0.a | cc:TODO |

### Carril S: seguridad sin cupo y avisos

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| S.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Borrar el cupo y la prioridad. La corrida pasa a decidir, guardar y aplicar, con una fila de `precio_corrida` por ejecución. Migración 0058. | Con 8 decisiones `live` que mueven precio, las 8 se aplican. Una segunda corrida el mismo día no decide y aplica solo lo pendiente. `app/precio/cuota.py` no existe y la compuerta de la guía sale vacía. La 0058 rechaza una decisión con `l_valor` y sin `l_origen`. Las cuotas de Ads no cambian. | 0.b | cc:TODO |
| S.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Apagador: modo global, modo por universo, modo efectivo y su edición en `/settings`. | Con el global en `shadow` y un goal `live`, la decisión entra como `shadow` y deja un cambio virtual. Con el global en `off`, la corrida limpia y no decide. Apagar a media corrida corta en la siguiente escritura. Un universo sin entrada vale `off`. Una decisión `live` retenida no se aplica si su goal pasó a `shadow` o se cerró: deja una retención `goal_cambiado`. Guardar un modo desde `/settings` inserta una `config_version` que conserva las demás claves. Un valor fuera de `off`, `shadow` y `live` responde 422. Sin token, 401. | S.1 | cc:TODO |
| S.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Compuerta: fusible por universo, insumo sistémico, corte por errores, liberación, repasos y el bloque de seguridad de `/precios`. | 80 intenciones nuevas sobre 158 medidas retienen las 80 y no escriben nada. 79 no retienen. Un costo 20 % arriba en 60 de 158 retiene y nombra `costo`. Tras `liberar`, la siguiente corrida aplica lo retenido de hoy. Una segunda ola hacia el mismo goal no cuenta como nueva. Cinco errores seguidos cortan la corrida. Un repaso sin nada pendiente no llama a la plataforma. | S.2 | cc:TODO |
| S.4 | `[stage:implementacion] [lane:gate] [tdd:required]` Cambios en `error` sin efecto, huérfanas por lectura viva y cierre por plataforma. | Un `error` con readback igual a `precio_antes` no consume cooldown. Uno con readback igual a `precio_despues` sí. Una huérfana con el precio ya movido queda `confirmado` por `lectura_viva`. La corrida de `amazon_mx` no cierra cambios de `amazon_us`. Tras tres días seguidos en `error`, la cuarta corrida da `frenado(api_error)`. | S.1 | cc:TODO |
| S.5 | `[stage:implementacion] [lane:gate] [tdd:required]` Reversa en lote por fecha, por corrida o por ids. | El plan no gasta cuota de la plataforma. Un precio que cambió entre el plan y el go salta esa fila y el lote sigue. Un original `enviado` con el precio ya aplicado se cierra y se revierte el mismo día. Si algún universo del lote está en `live`, el lote aborta. La reversa y la corrida no corren a la vez. | S.4 | cc:TODO |
| S.6 | `[stage:implementacion] [lane:gate] [tdd:required]` Avisos: resumen diario, compuerta, cambio en error, corte, reversa y aviso de corrida ausente en `/salud`. | Un resumen por plataforma y día, también si nada se movió. Dos corridas el mismo día mandan un resumen. El aviso de compuerta sale una vez por universo, causa y día, aunque haya repasos. Ningún texto trae costo, margen ni goal. Un fallo del envío no tumba la corrida. | S.3 | cc:TODO |
| S.7 | `[stage:implementacion] [lane:gate] [tdd:required]` Señal de ventas por unidad con evidencia de tráfico, lectura de cohorte como aviso y cierre `lineal`. | Los casos de `u15` y `u60` de `tests/test_precio_reglas.py` dan lo mismo con ventas por unidad. Sin subida propia, el tráfico da `sin_dato`. Con ventas sin atribuir, la señal da `sin_dato(venta_sin_publicacion)`. Con `lineal=True`, `decidir` no pide cotización. | 0.b, S.6, F.1, F.4 | cc:TODO |

### Carril G: goals en pantalla

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| G.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Plan de goals puro y su escritura todo o nada. | "Margen de hoy" propone el margen de cada unidad. Fuera de banda, sin escenario y salto mayor a 25 % salen como bloqueo con motivo. Una huella distinta no escribe nada. Un fallo a mitad no deja ninguna fila. Reeditar el mismo día deja un solo goal vigente. `tools/precio_goal.py` conserva su ceremonia, con tres cambios declarados: el go es todo o nada, resembrar el mismo día se permite, y un goal vigente se reemplaza. | 0.a. La prueba contra la base real espera a 0.b | cc:TODO |
| G.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Rutas de lectura y de escritura de `/api/precios`. | Sin token, 401 antes de abrir la conexión admin. `plan` no escribe. `aplicar` con huella vieja responde 409 con el plan nuevo. `live` sin go literal responde 422. `liberar` dos veces devuelve la misma fila. | G.1, S.3 para `liberar` | cc:TODO |
| G.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Pantalla `/precios` con edición y selección en bloque. Incluye el bloque de seguridad que construye S.3. | El servidor entrega, por unidad, margen de hoy, goal, precio objetivo, origen del envío y TACoS. La prueba manual de la guía siembra un goal, cambia otro y siembra "margen de hoy" a todos los sin goal. | G.2 | cc:TODO |

### Carril F: FBM de México y Estados Unidos

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| F.0 | `[stage:verificacion] [lane:gate] [tdd:skip:sonda]` Tres lecturas de producción: las identidades de los cargos de envío, Product Fees para una oferta FBM de MX y una de US, y si un listing FBM sin existencias deja de ser `BUYABLE`. | `ejecucion/F.0/` con cada script o consulta, su salida literal y la conclusión. Si Product Fees no responde `Success`, el universo se registra sin cotizador. | - | cc:TODO |
| F.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Atribución de ventas de Amazon a publicación. | Una venta con SKU único se atribuye por `sku`. Un producto con FBA y FBM se desempata por `fulfillment_channel`. Lo que hoy no se puede decidir no se escribe, se cuenta y se reintenta en la siguiente ingesta. La primera corrida llena toda la historia. Un evento atribuido no se vuelve a atribuir. | 0.a | cc:TODO |
| F.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Envío con origen: regla por orden, escalera de origen y job de muestras. | `costo_de_orden` con etiqueta 450, Labman 449 y HB 80 da 530. Una orden de MX con solo `MFNPostageFee` 91 da 91. Un mutante que suma las tres etiquetas muere. Con un envío propio el origen es `propio`. Sin propios y con familia, `familia`. Sin familia, `marketplace`. Sin ningún envío en el universo, no hay muestra. El job corrido dos veces no escribe la segunda. | 0.a, F.0 | cc:TODO |
| F.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Registro de universos en la estimación: FBM de MX y Estados Unidos. Migración 0055. | Una oferta FBM de MX produce escenario `disponible` con `logistica_origen` y fees partidos. La cotización FBM no trae comisión de logística. Una oferta de US sin tasa de cambio da `fx_ausente`. Una tasa de más de 3 días da `fx_desactualizado`. La batería de MX FBA pasa sin tocar aserciones. La 0055 rechaza un escenario `disponible` sin origen. | F.1, F.2 | cc:TODO |
| F.4 | `[stage:implementacion] [lane:gate] [tdd:required]` Adaptador de Amazon para FBM y US, y la sonda genérica de escritura. | La disponibilidad de una unidad FBM sale del estado del listing. Las ventas salen de `v_precio_venta_unidad`. `precio --platform amazon_mx --platform amazon_us` corre las dos en serie. `tools/precio_sonda.py` sin `--acepto-mutacion-real` no escribe. | F.3, 0.b | cc:TODO |

### Carril M: Mercado Libre

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| M.0 | `[stage:verificacion] [lane:gate] [tdd:skip:sonda]` Sonda de lectura dentro de `orbit-app-1`: forma de una publicación, SKU por variante, consulta de cargos, visitas y permisos del token. | `ejecucion/M.0/` con el script, cada respuesta saneada y cuatro conclusiones: de dónde sale el SKU, si hay consulta de cargos que separe porcentaje y fijo, si el precio va por publicación o por variante, y si el token permite escribir. | - | cc:TODO |
| M.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Catálogo diario: publicaciones, miembros y observación. | Una publicación con tres variantes mapeadas da una fila en `listing` y tres en `listing_miembro`. Una variante sin producto queda con `product_id` nulo. Una publicación sin ningún miembro mapeado no entra a `listing` y la cobertura la cuenta. Una publicación cerrada no es activa. | 0.a, M.0 | cc:TODO |
| M.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Ledger de Mercado Libre, con la auditoría previa de quién lee `ledger_event`. | `ejecucion/M.2/auditoria.md` lista cada vista y reporte que agrupa por plataforma y dice qué verá. El conteo "plataforma meli excluida" baja a cero. Un cargo de tipo desconocido cae en `otros`, contado. Amazon no cambia. La evidencia trae la retención medida de Mercado Libre. | M.1 | cc:TODO |
| M.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Estimación del universo `meli/meli`. | Una publicación con todo produce escenario `disponible` reproducible a mano. Cada componente ausente da su motivo. Con varios miembros, la cuenta de la fila es la del miembro que manda. | M.2, F.2 | cc:TODO |
| M.4 | `[stage:implementacion] [lane:gate] [tdd:required]` `MercadoMeli` y el cliente de escritura, con la forma candidata sin sellar. | Sin `modo_confirmado="live"` el cliente no se construye. Con la forma sin sellar, `escribir` desde la corrida levanta `EscrituraNoDisponible` sin tocar la red. `ClienteMeli` sigue rechazando todo método que no sea GET. El candado falla con un `.put(` sembrado fuera de `app/meli/write_client.py`. | M.3, 0.b | cc:TODO |

### Despliegues y encendido

Cada universo recorre su fila por su cuenta. Ninguno espera a otro.

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| D.1 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar el corte 0 (0053, 0054 y el código de 0.b). Lo corre Muse. | El checklist sale 0. `precio --reporte` del día siguiente muestra 6 decisiones sobre los mismos listings. `/precios` responde 200. | 0.b | cc:TODO |
| D.2 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar S.1 a S.6 y el carril G, con la 0058 y los repasos de MX en el cron. | El checklist sale 0. `/precios` muestra los modos y el bloque de seguridad. El resumen diario llega. | S.1 a S.6, G.3, D.1 | cc:TODO |
| X.1 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Encender MX FBA. | Criterios de encendido de abajo. | D.2 | cc:TODO |
| D.3 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar el carril F y S.7, con la 0055, las políticas `amazon_mx/fbm` y `amazon_us/fbm`, el job de muestras y US en el cron. | El checklist sale 0 después de la primera corrida del job de muestras y de la estimación. Hay escenarios `disponible` de FBM y de US. La cobertura de MX y de US cuadra. | F.4, S.7, D.2 | cc:TODO |
| X.2 | `[stage:cierre-pr] [lane:release] [tdd:skip:sonda]` Encender MX FBM. | Sonda de escritura de un centavo con su reversa, y criterios de encendido. | D.3 | cc:TODO |
| X.3 | `[stage:cierre-pr] [lane:release] [tdd:skip:sonda]` Encender Estados Unidos. | Sonda de escritura con su reversa, y criterios de encendido. | D.3 | cc:TODO |
| D.4 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar el carril M, con la política `meli/meli`, el catálogo diario y Mercado Libre en el cron. | El checklist sale 0. La cobertura de Mercado Libre cuadra con las activas de la API. | M.4, D.2 | cc:TODO |
| X.4 | `[stage:cierre-pr] [lane:release] [tdd:skip:sonda]` Encender Mercado Libre. | Sonda de escritura con su reversa, tres intentos como máximo. Si pasa, un PR cambia solo la constante de la forma y D.4 se vuelve a desplegar. Después, criterios de encendido. | D.4 | cc:TODO |
| C.1 | `[stage:cierre-pr] [lane:fast] [tdd:skip:docs]` Cierre: filas, `plans/ROADMAP.md`, `docs/CHAT-CONTEXT.md` y `docs/DEPLOY.md`. | Los cuatro universos encendidos, o cerrados con el motivo escrito. | X.1 a X.4 | cc:TODO |

## Criterios de encendido

Un universo se enciende con uno o dos días de prueba (D2). No hay pilotos ni
ventanas de 30 días.

1. **Sembrar en sombra.** Muse siembra "margen de hoy" en `shadow` a todo el
   universo, con las mismas rutas que usa `/precios`. Las unidades con margen de
   hoy fuera de la banda no se siembran: se le listan al dueño en el aviso. Si
   el universo no tiene entrada de modo (X.2, X.3 y X.4), la siembra lo pone
   antes en `shadow`: sin entrada vale `off` y la corrida no decide nada para
   él. Esos tres se siembran con el checklist de su despliegue en 0.
2. **Leer la primera corrida.** Pasa si se cumplen las cuatro:
   - La cobertura cuadra exacta.
   - Al menos 95 % de las unidades sembradas quedan en `mantener(en_tolerancia)`.
     Esta lectura detecta un insumo que cambió entre la siembra y la corrida. No
     prueba que la cuenta sea correcta: la siembra y el motor leen el mismo
     margen.
   - Cada unidad que no quedó en tolerancia tiene explicación escrita.
   - El lead reproduce a mano la cuenta de tres unidades, desde el costo, los
     fees y el envío de sus fuentes. Elige una por cada origen de envío que haya
     en el universo. Esta es la comprobación que prueba la cuenta.
3. **Probar la escritura**, solo en un universo con camino de escritura nuevo
   (X.2, X.3 y X.4). Muse corre la sonda de un centavo en una publicación y su
   reversa. Pasa si Amazon o Mercado Libre aceptan las dos, la lectura
   posterior muestra el precio y una publicación de control no cambia.
4. **Encender.** Con los pasos anteriores cumplidos y el visto bueno del lead
   sobre el paso 2, Muse pone el universo en `live` y pasa los goals a `live`
   en bloque con el go literal de la fila. El primer universo que se enciende
   (X.1, o el siguiente si X.1 queda cerrada) sube además `precio_modo_global`
   a `live`: la migración lo siembra en `shadow` y
   el modo efectivo es el menor de los tres. Sin ese paso ningún precio se
   mueve y nada lo marca como error.

Si un criterio no se cumple, ese universo no se enciende. Muse entrega el motivo
medido, la fila X queda cerrada con ese motivo, claw avisa al dueño y los demás
universos siguen.

Después del encendido el lead lee el resumen diario los primeros días. Esa
lectura no frena a ningún otro universo.

## Pruebas que deben discriminar

Cada fila registra un mutante por prueba nueva: el cambio de una línea que la
pone en rojo. Estos mutantes son obligatorios y deben morir:

- La 0054 exige `l_origen` o `logistica_origen` y rechaza el INSERT del código
  de hoy.
- `costo_de_orden` suma las tres etiquetas en vez de contar una.
- `costo_de_orden` deja fuera `MFNPostageFee`.
- `resolver_envio` devuelve `propio` con cero envíos propios.
- Una muestra `familia` entra a la base sin donantes.
- `Cuenta` se construye con el envío en MXN y el precio en USD.
- La cotización FBM se pide como FBA.
- El fusible usa mayor o igual en vez de mayor estricto.
- El fusible cuenta los `no_evaluado` en el denominador.
- Un segundo escalón hacia el mismo goal cuenta como intención nueva.
- El cooldown ignora un `error` cuyo readback muestra el precio nuevo.
- La corrida no relee la config antes de escribir.
- La corrida aplica en el repaso una decisión `live` cuyo goal ya pasó a
  `shadow`.
- Una venta que hoy no se puede atribuir se guarda como fila fija.
- El aviso de compuerta sale en cada repaso del mismo día.
- El trigger de modo vuelve a exigir `g.mode = NEW.mode`.
- La decisión entra con un `goal_id` ya cerrado.
- `aplicar_plan` escribe la mitad del lote cuando una fila falla.
- El resumen diario sale dos veces el mismo día.
- `MeliWriteClient` se construye sin `modo_confirmado="live"`.
- La corrida escribe en Mercado Libre con la forma sin sellar.

## Secciones de entrega

Las tareas se entregan en seis secciones, en este orden. Cada sección es una
rama y un PR, con un commit por tarea en el orden de la tabla. En las secciones
2 a 5 el último commit es el paquete de despliegue de su fila D, con los scripts
de sus filas X. Una sola revisión cubre el PR entero. Con el PR mergeado corre
su despliegue y después el encendido de sus universos.

| Sección | Tareas, en orden | Rama | Después del merge |
| --- | --- | --- | --- |
| 1. Lecturas de producción | F.0 y M.0 | `rp02/sec1-lecturas` | - |
| 2. Corte 0 | 0.a, 0.b y el paquete de D.1 | `rp02/sec2-corte-0` | D.1 |
| 3. Seguridad y goals | S.1, S.2, S.3, S.4, S.5, S.6, G.1, G.2, G.3 y el paquete de D.2 con los scripts de X.1 | `rp02/sec3-seguridad-goals` | D.2 y X.1 |
| 4. FBM de México y Estados Unidos | F.1, F.2, F.3, F.4, S.7 y el paquete de D.3 con los scripts de X.2 y X.3 | `rp02/sec4-fbm-us` | D.3, X.2 y X.3 |
| 5. Mercado Libre | M.1, M.2, M.3, M.4 y el paquete de D.4 con los scripts de X.4 | `rp02/sec5-meli` | D.4 y X.4 |
| 6. Cierre | C.1 | `rp02/sec6-cierre` | - |

Reglas de una sección:

- Una tarea no empieza con la anterior en rojo. El orden de la tabla cumple la
  columna Depends de cada fila.
- La sección 1 solo trae evidencia. Sus conclusiones entran al encargo de la
  sección 2.
- Un despliegue lleva todo lo que está en `master`. Por eso una sección no se
  mergea mientras la anterior esté mergeada y sin desplegar.
- El encendido de un universo no frena la sección siguiente: sus pasos se
  intercalan con la construcción.
- Las filas se marcan y la entrada de `docs/CHAT-CONTEXT.md` se agrega en el PR
  de cierre, C.1.

## Orden y salida

El orden es el de "Secciones de entrega". Las dependencias que lo explican: 0.a
va antes que 0.b. Los carriles F, M y G necesitan 0.a, y el carril S necesita
0.b. F.0 y M.0 son lecturas y no dependen de nada.

`tests/test_architecture.py` lo editan 0.a, 0.b y el carril S. Los carriles F, M
y G no lo tocan: escriben sus candados en `tests/test_arq_precio_<carril>.py`,
que 0.a agrega al job `rapido`.

El plan cierra cuando los cuatro universos están encendidos, o cuando el que
falte quedó cerrado con el motivo escrito. Un universo pasa a `live` solo con
sus criterios de encendido cumplidos. Ese paso lo da Muse con el script de su
fila X, sin esperar al dueño.
