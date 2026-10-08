# REPRICING 02: el motor de precios en todas las plataformas

Estado: plan del 2026-10-08 UTC. El diseño se terminó el mismo día. Nada está
construido. Base de planificación: commit `d83bb28`.

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
construcción. Si dos documentos se contradicen, sigue el de mayor precedencia y
avisa al lead.

**Sustituye** a las filas abiertas de [REPRICING 01](repricing-01.md): D.2, D.3 y
las fases E, 0, B y M. Lo que REPRICING 01 cerró sigue en pie: las reglas puras,
la escritura y la reversa en Amazon, la migración 0039, la sonda A.4 y la sombra
de MX FBA.

**Fuera de alcance:** promociones, kits, perseguir la Buy Box, goals por variante
en Mercado Libre y la reversa automática.

## Decisiones que gobiernan este plan

Las diez decisiones del dueño del 2026-10-08 (D1 a D10) están en la sección
"Decisiones del dueño" del diseño. Este plan no las repite.

El diseño dejó nueve preguntas abiertas. Ninguna frena la construcción. El plan
usa estos valores hasta que el dueño diga otra cosa:

| Tema | Valor que usa el plan | Dónde se cambia |
| --- | --- | --- |
| Tope de la banda de goals | 60 %. Un producto con margen de hoy fuera de 10 a 60 % se lista aparte y no se siembra solo | CHECK `precio_goal_banda` y `precio_goal_max_pct` |
| Retención en Estados Unidos | 2.07 % | Fila `amazon_us/fbm` de `estimacion_politica_version` |
| Envío de Estados Unidos | Etiqueta más `ShippingHB`, con el cargo repetido contado una vez | `app/precio/envio.py::costo_de_orden` |
| Soltar el fusible | Los precios se mueven en el siguiente repaso, hasta dos horas después | Cron de repasos |
| Publicación de Mercado Libre con variantes | Manda la variante que necesita el precio más alto | `puerto.miembro_que_manda` |
| Variante de Mercado Libre sin producto | La publicación completa queda sin evaluar, con el SKU a la vista | `MercadoMeli.insumos` |
| Reversa en lote | Exige apagar antes el universo | `cambios.revertir_lote` |
| Envío imputado en `live` | Permitido, con el origen visible | Sin interruptor |
| Señal por tráfico | Solo después de una subida propia | `ventas.evaluar_senal` |

## Tamaño medido

Lecturas de producción con `orbit_read`. Las fuentes están en
`docs/evidencia/repricing-01/` (A.7, E.1 y D.2) y en la lectura del 2026-10-08.

| Dato | Amazon MX | Amazon US | Mercado Libre |
| --- | ---: | ---: | ---: |
| Publicaciones activas | 260 | 100 | 65 en la API |
| De esas, FBA | 158 | 0 | no aplica |
| De esas, FBM | 102 | 100 | no aplica |
| Con goal hoy | 6, todos `shadow` | 0 | 0 |
| Productos con al menos 6 envíos propios en 180 días | 14 | 17 | sin medir |
| Productos con 1 a 5 envíos propios | 94 | 36 | sin medir |
| Publicaciones con varias variantes | no aplica | no aplica | 48 de 65 |

El motor corre una vez al día para `amazon_mx`. Del 2026-10-01 al 2026-10-07
decidió 6 productos por día. No ha escrito ningún precio fuera de la sonda A.4.

## Contratos

El revisor comprueba estas reglas en cada fila. Vienen del diseño.

- **Un camino de escritura por plataforma.** `app/spapi/write_client.py` y
  `app/meli/write_client.py` son las únicas puertas. Los clientes de lectura no
  ganan verbos de escritura.
- **Un escritor por tabla.** `goals_write.py` escribe `precio_goal`.
  `liberaciones.py` escribe `precio_compuerta_liberacion`. La corrida escribe
  `precio_corrida`, `precio_compuerta_medicion` y `precio_retencion`.
- **El protocolo de `precio_cambio` se escribe una vez**, en
  `app/precio/cambios.py`. El orden no cambia: INSERT, COMMIT, escritura, sello.
- **El motor no conoce plataformas.** `corrida.py`, `cambios.py` y `fuentes.py` no
  importan `app.spapi`, `app.meli`, `app.estimacion_fees` ni `httpx`.
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
- **Migraciones aditivas.** Todo CHECK nuevo sobre `precio_decision` o
  `estimacion_escenario` lleva `NOT VALID`.
- **Telegram sin costo, margen ni goal.**

## Interruptores y reversa

| Qué quieres parar | Cómo | Efecto |
| --- | --- | --- |
| Todo el motor | `precio_modo_global` a `off` en `/settings` | La corrida limpia y sale sin decidir |
| Un universo | Su entrada de `precio_modo_universo` a `off` o `shadow` | Sus decisiones no se aplican, o se aplican como virtuales |
| Un producto | Goal a `shadow`, o cerrar el goal | Deja de escribir ese día, también lo que estaba retenido |
| Una corrida en curso | Cualquiera de los tres de arriba | La corrida relee la config antes de cada escritura |
| Precios ya movidos | `tools/precio_reversa.py --platform <p> --fecha <día>` | Revierte el lote, el mismo día |

Cada despliegue trae `rollback.sh`. Cada migración trae su
`00NN_reversa_<nombre>.sql`.

## Tareas y dependencias

La evidencia de cada fila va en `docs/evidencia/repricing-02/ejecucion/<id>/`.
`cc:TODO` significa que no ha empezado. Muse implementa, el lead revisa y el
dueño corre lo que toca producción.

### Corte 0: separar lo compartido

Los dos pasos van en fila. Después de 0.a arrancan F, M y G. Después de 0.b
arranca S.

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 0.a | `[stage:implementacion] [lane:gate] [tdd:required]` Contratos, sin cambio de comportamiento: migraciones 0053 y 0054, `puerto.py`, tipos nuevos, lector único de config, los dos registros con módulos vacíos, candado de imports, y `/precios` partido en parciales. | La batería pasa sin tocar aserciones. El ensayo de la 0054 pasa sobre una copia con las filas de producción. El candado de la migración rechaza los casos de `datos.sql` §11. `precio --platform amazon_mx` da las mismas líneas antes y después. | - | cc:TODO |
| 0.b | `[stage:implementacion] [lane:gate] [tdd:required]` Voltear la corrida: `MercadoAmazon`, `cambios.py`, corrida en tres pasos con la compuerta abierta, `leer_cuenta`, cobertura sobre `Vitrina`. | Las decisiones de un día en sombra son idénticas fila por fila antes y después. La lista de activas del adaptador coincide con `_SQL_CANO` sobre datos reales. Las pruebas de `tests/test_precio_corrida.py` pasan cambiando solo su armado. `corrida.py` ya no importa `app.spapi`. | 0.a | cc:TODO |

### Carril S: seguridad sin cupo y avisos

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| S.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Borrar el cupo y la prioridad. | Con 8 decisiones `live` que mueven precio, las 8 se aplican. `app/precio/cuota.py` no existe. `git grep -n "repartir_cupo\|precio_cap_" -- app` no devuelve código. Las cuotas de Ads no cambian. | 0.b | cc:TODO |
| S.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Apagador: modo global, modo por universo y modo efectivo. | Con el global en `shadow` y un goal `live`, la decisión entra como `shadow` y deja un cambio virtual. Con el global en `off`, la corrida limpia y no decide. Apagar a media corrida corta en la siguiente escritura. Un universo sin entrada vale `off`. Una decisión `live` retenida no se aplica si su goal pasó a `shadow` o se cerró: deja una retención `goal_cambiado`. | S.1 | cc:TODO |
| S.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Compuerta: fusible por universo, insumo sistémico, corte por errores, liberación y repasos. | 80 intenciones nuevas sobre 158 medidas retienen las 80 y no escriben nada. 79 no retienen. Un costo 20 % arriba en 60 de 158 retiene y nombra `costo`. Tras `liberar`, la siguiente corrida aplica lo retenido de hoy. Una segunda ola hacia el mismo goal no cuenta como nueva. Cinco errores seguidos cortan la corrida. | S.2 | cc:TODO |
| S.4 | `[stage:implementacion] [lane:gate] [tdd:required]` Cambios en `error` sin efecto, huérfanas por lectura viva y cierre por plataforma. | Un `error` con readback igual a `precio_antes` no consume cooldown. Uno con readback igual a `precio_despues` sí. Una huérfana con el precio ya movido queda `confirmado` por `lectura_viva`. La corrida de `amazon_mx` no cierra cambios de `amazon_us`. `frenado(api_error)` se alcanza en tres días. | 0.b | cc:TODO |
| S.5 | `[stage:implementacion] [lane:gate] [tdd:required]` Reversa en lote por fecha, por corrida o por ids. | El plan no gasta cuota de la plataforma. Un precio que cambió entre el plan y el go salta esa fila y el lote sigue. Un original `enviado` con el precio ya aplicado se cierra y se revierte el mismo día. Con el universo en `live`, el lote aborta. La reversa y la corrida no corren a la vez. | S.4 | cc:TODO |
| S.6 | `[stage:implementacion] [lane:gate] [tdd:required]` Avisos: resumen diario, compuerta, cambio en error, corte, reversa y aviso de corrida ausente en `/salud`. | Un resumen por plataforma y día, también si nada se movió. Dos corridas el mismo día mandan un resumen. El aviso de compuerta sale una vez por universo, causa y día, aunque haya repasos. Ningún texto trae costo, margen ni goal. Un fallo del envío no tumba la corrida. | S.3 | cc:TODO |
| S.7 | `[stage:implementacion] [lane:gate] [tdd:required]` Señal de ventas por unidad, con disponibilidad por canal y evidencia de tráfico. Lectura de cohorte como aviso. | Los casos de `tests/test_precio_reglas.py` sobre `u15` y `u60` dan lo mismo con ventas por unidad. Una unidad FBM con estado activo evalúa sin inventario FBA. Sin subida propia, el tráfico da `sin_dato`. Con ventas sin atribuir, la señal da `sin_dato(venta_sin_publicacion)`. | 0.b | cc:TODO |

### Carril G: goals en pantalla

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| G.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Plan de goals puro y su escritura todo o nada. | "Margen de hoy" propone el margen de cada unidad. Fuera de banda, sin escenario y salto mayor a 25 % salen como bloqueo con motivo. Una huella distinta no escribe nada. Un fallo a mitad no deja ninguna fila. Reeditar el mismo día deja un solo goal vigente. `tools/precio_goal.py` da los mismos resultados que antes. | 0.a. La prueba contra la base real espera a 0.b | cc:TODO |
| G.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Rutas de lectura y de escritura de `/api/precios`. | Sin token, 401 antes de abrir la conexión admin. `plan` no escribe. `aplicar` con huella vieja responde 409 con el plan nuevo. `live` sin go literal responde 422. `liberar` dos veces devuelve la misma fila. | G.1, S.3 para `liberar` | cc:TODO |
| G.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Pantalla `/precios` con edición, selección en bloque y el bloque de seguridad. | La pantalla siembra un goal, cambia uno y siembra "margen de hoy" a todos los sin goal. Cada fila muestra margen de hoy, goal, precio objetivo, origen del envío y TACoS. El bloque de seguridad muestra los modos, la última corrida y el botón Soltar cuando hay retención. | G.2 | cc:TODO |

### Carril F: FBM de México y Estados Unidos

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| F.0 | `[stage:verificacion] [lane:gate] [tdd:skip:sonda]` Tres lecturas de producción: prefijos de `source_event_id`, Product Fees para una oferta FBM de MX y una de US, y si un listing FBM sin existencias deja de ser `BUYABLE`. | `ejecucion/F.0/` con cada consulta, su salida literal y la conclusión. Si Product Fees no responde `Success`, el universo se registra sin cotizador. | - | cc:TODO |
| F.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Atribución de ventas de Amazon a publicación. | Una venta con SKU único se atribuye por `sku`. Un producto con FBA y FBM se desempata por `fulfillment_channel`. Lo que hoy no se puede decidir no se escribe, se cuenta y se reintenta en la siguiente ingesta. La primera corrida llena toda la historia. Un evento atribuido no se vuelve a atribuir. | 0.a | cc:TODO |
| F.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Envío con origen: regla por orden, escalera de origen y job de muestras. | `costo_de_orden` da `max(labman, etiqueta) + HB`. Un mutante que suma las tres muere. Con un envío propio el origen es `propio`. Sin propios y con familia, `familia`. Sin familia, `marketplace`. Sin ningún envío en el universo, no hay muestra. El job corrido dos veces no escribe la segunda. | 0.a, F.0 | cc:TODO |
| F.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Registro de universos en la estimación: FBM de MX y Estados Unidos. | Una oferta FBM de MX produce escenario `disponible` con `logistica_origen` y fees partidos. La cotización FBM no trae comisión de logística. Una oferta de US sin tasa de cambio da `fx_ausente`. Una tasa más vieja que `fx_max_dias` da `fx_desactualizado`. MX FBA no cambia: su batería pasa sin tocar. | F.1, F.2 | cc:TODO |
| F.4 | `[stage:implementacion] [lane:gate] [tdd:required]` Adaptador de Amazon para FBM y US, y la sonda genérica de escritura. | La disponibilidad de una unidad FBM sale del estado del listing. `precio --platform amazon_mx --platform amazon_us` corre las dos en serie. `tools/precio_sonda.py` en simulación no escribe. | F.3, 0.b | cc:TODO |

### Carril M: Mercado Libre

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| M.0 | `[stage:verificacion] [lane:gate] [tdd:skip:sonda]` Sonda de lectura con `ClienteMeli`: forma de una publicación, SKU por variante, consulta de cargos, visitas y permisos del token. | `ejecucion/M.0/` con cada respuesta saneada y cuatro conclusiones: de dónde sale el SKU, si hay consulta de cargos que separe porcentaje y fijo, si el precio va por publicación o por variante, y si el token permite escribir. | - | cc:TODO |
| M.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Catálogo diario: publicaciones, miembros y observación. | Una publicación con tres variantes mapeadas da una fila en `listing` y tres en `listing_miembro`. Una variante sin producto queda con `product_id` nulo. Una publicación sin ningún miembro mapeado no entra a `listing` y la cobertura la cuenta. Una publicación cerrada no es activa. | 0.a, M.0 | cc:TODO |
| M.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Ledger de Mercado Libre, con la auditoría previa de quién lee `ledger_event`. | `ejecucion/M.2/auditoria.md` lista cada vista y reporte que agrupa por plataforma y dice qué verá. El conteo "plataforma meli excluida" baja a cero. Un cargo de tipo desconocido cae en `otros`, contado. Amazon no cambia. | M.1 | cc:TODO |
| M.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Estimación del universo `meli/meli`. | Una publicación con todo produce escenario `disponible` reproducible a mano. Cada componente ausente da su motivo. Con varios miembros, la cuenta de la fila es la del miembro que manda. | M.2, F.2 | cc:TODO |
| M.4 | `[stage:implementacion] [lane:gate] [tdd:required]` `MercadoMeli` y el cliente de escritura, con la forma sin sellar. | Sin `modo_confirmado="live"` el cliente no se construye. `escribir` levanta `EscrituraNoDisponible` sin tocar la red. `ClienteMeli` sigue rechazando todo método que no sea GET. El candado falla con un `.put(` sembrado fuera de `app/meli/write_client.py`. | M.3, 0.b | cc:TODO |

### Despliegues y encendido

Cada universo recorre su fila por su cuenta. Ninguno espera a otro.

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| D.1 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar el corte 0 (0053, 0054 y el código de 0.b). Lo corre el dueño. | El checklist sale 0. La corrida automática del día siguiente decide los 6 goals igual que hoy. `/precios` responde 200. | 0.b | cc:TODO |
| D.2 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar los carriles S y G, con los repasos en el cron. | El checklist sale 0. `/precios` muestra los modos y el bloque de seguridad. El resumen diario llega. | S.1 a S.7, G.3, D.1 | cc:TODO |
| X.1 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Encender MX FBA. | Criterios de encendido de abajo. | D.2 | cc:TODO |
| D.3 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar el carril F, con las políticas `amazon_mx/fbm` y `amazon_us/fbm`, el job de muestras y el cron de US. | El checklist sale 0. Hay escenarios `disponible` de FBM y de US. La cobertura de MX y de US cuadra. | F.4, D.2 | cc:TODO |
| X.2 | `[stage:cierre-pr] [lane:release] [tdd:skip:sonda]` Encender MX FBM. | Sonda de escritura de un centavo con su reversa, y criterios de encendido. | D.3 | cc:TODO |
| X.3 | `[stage:cierre-pr] [lane:release] [tdd:skip:sonda]` Encender Estados Unidos. | Sonda de escritura con su reversa, y criterios de encendido. | D.3 | cc:TODO |
| D.4 | `[stage:cierre-pr] [lane:release] [tdd:skip:ops]` Desplegar el carril M, con la política `meli/meli` y el cron de Mercado Libre. | El checklist sale 0. La cobertura de Mercado Libre cuadra con las activas de la API. | M.4, D.2 | cc:TODO |
| X.4 | `[stage:cierre-pr] [lane:release] [tdd:skip:sonda]` Encender Mercado Libre. | Sonda de escritura con su reversa, que sella la forma del cuerpo en un PR propio, y criterios de encendido. | D.4 | cc:TODO |
| C.1 | `[stage:cierre-pr] [lane:fast] [tdd:skip:docs]` Cierre: filas, `plans/ROADMAP.md`, `docs/CHAT-CONTEXT.md` y `docs/DEPLOY.md`. | Los cuatro universos encendidos, o cerrados con el motivo escrito. | X.1 a X.4 | cc:TODO |

## Criterios de encendido

Un universo se enciende con uno o dos días de prueba (D2). No hay pilotos ni
ventanas de 30 días.

1. **Sembrar en sombra.** El dueño siembra "margen de hoy" en `shadow` a todo el
   universo desde `/precios`.
2. **Leer la primera corrida.** Pasa si se cumplen las cuatro:
   - La cobertura cuadra exacta.
   - Al menos 95 % de las unidades sembradas quedan en `mantener(en_tolerancia)`.
     Sembradas en su propio margen, casi ninguna debe querer moverse.
   - Cada unidad que no quedó en tolerancia tiene explicación escrita.
   - El lead reproduce a mano la cuenta de tres unidades.
3. **Probar la escritura**, solo en un universo con camino de escritura nuevo
   (X.2, X.3 y X.4). El dueño corre la sonda de un centavo en una publicación y
   su reversa. Pasa si Amazon o Mercado Libre aceptan las dos, la lectura
   posterior muestra el precio y una publicación de control no cambia.
4. **Encender.** El dueño pone el universo en `live` en `/settings` y pasa los
   goals a `live` en bloque con su go literal. En el primer encendido (X.1)
   sube además `precio_modo_global` a `live`: la migración lo siembra en
   `shadow` y el modo efectivo es el menor de los tres. Sin ese paso ningún
   precio se mueve y nada lo marca como error.

Después del encendido el lead lee el resumen diario los primeros días. Esa
lectura no frena a ningún otro universo.

## Pruebas que deben discriminar

Cada fila registra un mutante por prueba nueva: el cambio de una línea que la
pone en rojo. Estos mutantes son obligatorios y deben morir:

- `costo_de_orden` suma las tres fuentes en vez de `max(labman, etiqueta) + HB`.
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
- Una decisión `live` entra bajo un goal `shadow`.
- La decisión entra con un `goal_id` ya cerrado.
- `aplicar_plan` escribe la mitad del lote cuando una fila falla.
- El resumen diario sale dos veces el mismo día.
- `MeliWriteClient` se construye sin `modo_confirmado="live"`.

## Orden y salida

Orden: 0.a, después 0.b. Con 0.a cerrado arrancan F, M y G. Con 0.b cerrado
arranca S. F.0 y M.0 son lecturas y arrancan hoy.

Un solo archivo sigue compartido después del corte 0: `tests/test_architecture.py`,
que solo se edita en 0.a. Cada carril escribe sus candados en
`tests/test_arq_precio_<carril>.py`.

El plan cierra cuando los cuatro universos están encendidos, o cuando el que
falte quedó cerrado con el motivo escrito. Ninguna fila autoriza por sí sola
pasar un universo a `live`. Ese paso lo da el dueño en `/settings`.
