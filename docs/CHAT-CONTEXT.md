# Orbit — contexto para Claude Chat

> Archivo mantenido por la sesión lead de Claude Code: se actualiza al cierre
> de cada phase. Si la fecha de abajo se ve vieja, pide al dueño que haga
> "Sync now" en el Project o pregúntale el estado antes de asumir.

**2026-09-03 — BIDS 01 tarea 1.1 lista para revisión: la palabra que gasta sin vender ya puede bajar −25% cuando trae los clics que en su producto cuesta una venta y gasto sobre el piso (antes solo −12%); la pausa manda igual y lo viejo se sigue leyendo igual.**

**2026-09-03 — El dueño decidió sobre los dos huecos que la revisión adversarial vio en vivo; nace el plan BIDS 01.** (1) Una palabra que gasta y no vende podrá bajar −25% —no solo −12%— pero únicamente cuando haya acumulado los clics que en su producto cuesta una venta (el número adaptativo por grupo que ya calcula el motor; en las arras ~90-120) y un gasto de al menos 40 USD / 500 MXN; el dueño rechazó umbrales absolutos bajos («mis productos necesitan ~120 clics por venta»). Con los datos de ayer habría cambiado 1 de 103 decisiones. (2) Las palabras sin impresiones en 14 días dejan de recibir ajustes (hoy son ~65% de las activas: 172 MX / 167 US, ninguna con ventas en 90 días), se listan en una página nueva clasificadas por causa (gastó sin vender / peso muerto) y una herramienta permitirá archivarlas por lote con el go del dueño y con reversa (se pueden reponer). Revivir con puja más alta sigue siendo decisión humana. La propuesta del 26 de agosto queda aprobada con esos ajustes; implementan GLM (motor, vista, herramienta) y DeepSeek (página y digest); el lead migra, despliega y mide el antes/después. También quedó explicada la rampa de cupos: 10 pujas/día por país y duplicación manual cada 48 h sanas, primera posible el 4 de septiembre.

**2026-09-03 — ORBIT 05 tarea 2.1 CERRADA: tres revisores externos (codex, qwen, grok) recalcularon una por una las 20 primeras pujas reales y las tres coinciden: fieles a las reglas, cero divergencias.** El expediente saneado se generó con la herramienta nueva desde la máquina del lead (solo lectura, sin tocar el contenedor); cada revisor volvió a calcular ACoS, banda, puja esperada, redondeo a centavos, ids de Amazon, lectura de vuelta, madurez de datos y moneda. Ningún error del motor. Lo que sí salió a la luz son dos huecos de diseño que ya estaban documentados como propuesta desde el 26 de agosto y que ahora se vieron en vivo: (1) en México 8 de las 10 bajadas fueron de −12% a palabras que gastan pero no venden nada — la regla actual no puede bajarles más ni pausarlas; (2) dos de esas palabras llevan sin tráfico desde junio, así que bajarles la puja no sirve y gastó 2 de los 10 cupos del día. Ambas cosas quedan como decisión del dueño (aprobar o no las opciones A y C de esa propuesta). El lead cerró además, con la base completa, la duda que los revisores no podían resolver: las 10 pujas aplicadas por país son exactamente las 10 primeras del orden sellado (mayor sangrado primero).

**2026-09-02 — Ya existe la herramienta que arma el expediente saneado de las primeras pujas aplicadas para que tres revisores externos las recalculen.** Junta en un solo paquete (sin credenciales) la decisión, el gasto, la venta, la puja pedida y lo que Amazon confirmó; el lead corre eso contra los ciclos reales y se lo pasa a los tres revisores. La revisión en sí no está hecha: solo el expediente.

**2026-09-02 — ORBIT 05: el dueño firmó las 20 primeras pujas y confirmó los avisos de Telegram; el gate de campañas activas ya está desplegado.** Con un «si y si» el dueño cerró el spot-check (tarea 2.2: las 18 pujas en campañas activas firmadas, cero vetos; las 2 en campañas pausadas ya las había decidido conservar) y confirmó que le llegaron los dos avisos de «tope del día agotado» (US y MX), el único punto de la 1.5 que faltaba. Y con su go «deploy hoy» —enmienda consciente a la regla «un cambio a la vez» del día del flip— el lead desplegó CAMPAÑA ACTIVA 01 al contenedor a las 17:48 UTC (respaldo previo, código idéntico a master por md5, contenedor recreado, verificación dentro del contenedor, base sin cambio). Desde el ciclo del 2026-09-03 08:40 UTC el motor ya no gasta cupos en campañas ni ad groups pausados; la verificación de ese ciclo cierra la tarea 1.4.

**2026-09-02 — CAMPAÑA ACTIVA 01 revisada (CodeRabbit + codex, una ronda) y fusionada; queda el deploy.** El lead adjudicó los 10 comentarios: ninguno afecta el gate principal (el que mañana evita gastar cupos en campañas pausadas). Se corrigieron en este mismo PR los menores (nombre del plan sin Ñ en el código, el orden documentado de quota/claim en APPLY.md, formato del plan). Dos hallazgos de codex pasan a la tarea 1.6 (GLM): (1) la reconciliación que reintenta una mutación tras un crash a mitad de un apply no consulta campaña/ad group — camino rarísimo (exige crash + pausa en medio) pero real; (2) una entidad con veto pendiente dentro de una campaña pausada se cuenta como «veto pendiente» y no como «campaña no habilitada» (solo contadores, no muta nada). Descartados con razón: la carrera entre el sync de estructura y el claim (residual declarado; los crons van con 2 h de separación) y un caso imposible por llave foránea.

**2026-09-02 — CAMPAÑA ACTIVA 01 implementada: el motor ya no toca pujas ni cortes de campañas o ad groups pausados.** El 2026-09-02 el primer ciclo live aplicó 2 de 20 bids dentro de campañas PAUSED — el motor solo miraba el estado de la palabra clave, nunca el de su campaña ni el de su ad group (causa, medición y fix en `plans/campana-activa-01.md`). Gate nuevo en los dos momentos en que el motor toca Amazon: al decidir (el ciclo salta con contadores `campana_no_enabled`/`grupo_no_enabled` visibles en la pantalla de salud) y al liberar los cortes de la cola de 48h (descarte previo a cualquier cobro o llamada). Queda pendiente el deploy del lead y la verificación del ciclo siguiente (≈299 US / 10 MX skips esperados).

**2026-09-02 — ORBIT 05 Phase 1 CERRADA (1.1-1.5) con evidencia en `plans/orbit-05.md`; arranca la Phase 2 (48h live).** El día del flip quedó documentado paso a paso: go/no-go con los siete prerrequisitos por SELECT, backup real verificado por restauración, descarte de la cola shadow en una transacción, flip de config y goals a las 08:53 UTC y el primer ciclo real (corrido a mano por decisión del dueño, mismo comando del cron). Queda abierto en la Phase 2: la verificación adversarial triple de las 20 primeras pujas (el insumo ya existe, con 10 de México), la firma del dueño sobre las 18 pujas en campañas activas (las 2 en campañas pausadas ya las decidió: se conservan), confirmar que le llegaron por Telegram los dos avisos de "tope del día agotado", el primer harvest a mano (no antes del 2026-09-04) y dos días de monitoreo. Nada de esto toca Amazon por sí solo; el siguiente ciclo del reloj es el 2026-09-03 08:40 UTC.

**2026-09-02 — ORBIT 05 EN VIVO: primer ciclo real corrido con el go del dueño; hallazgo: el motor tocaba campañas pausadas → nace CAMPAÑA ACTIVA 01.** El dueño adelantó el flip al 2026-09-02 (renunció a los días restantes de shadow; flip a las 08:53 UTC, después del ciclo shadow de las 08:40) y pidió no esperar al cron del día siguiente: los ciclos 33 (US) y 34 (MX) corrieron a las 16:12/16:14 UTC en modo live por el mismo camino del cron (literal "corre hoy"). Resultado: 20 pujas bajadas (10 por país, el tope del día 1; −25% en US, −12%/−25% en MX), todas confirmadas por Amazon con lectura de vuelta, cero fallos, cero divergencias, cero cortes (la cola de veto quedó vacía; las otras 66 US / 37 MX quedaron fuera por el tope y se retoman en los ciclos siguientes). Hallazgo del lead al revisar la tabla de negocio: **2 de las 20 cayeron en campañas PAUSADAS** (USPerNog Category Phrase en US y AGM2M Auto Discovery en MX — justo la que el dueño dejó fuera del piloto). No gastan dinero (una campaña pausada no sirve), pero queman cupo del día y dejan pujas alteradas si un día se reactivan: el motor solo miraba el estado de la palabra, nunca el de su campaña ni el de su ad group (medido en vivo: 299 palabras en US y 10 en MX activas dentro de campañas pausadas). Decisiones del dueño: **conservar esas 2 pujas tal cual** ("dejalas asi") y **arreglar la causa antes de cerrar el papeleo de ORBIT 05**. Nace `plans/campana-activa-01.md`: GLM implementa que el motor salte todo lo que viva en una campaña o ad group no activo —al decidir y al liberar cortes—, con las etiquetas en la pantalla de salud; el lead revisa, despliega y verifica el ciclo siguiente. Hasta ese deploy, cada cron puede volver a gastar cupos en campañas pausadas.

**2026-09-01 — Higiene del registro de trabajo (sin cambios de producto).** En AppFlowy había seis tareas de ORBIT 06 que seguían «En progreso» aunque su trabajo ya estaba terminado y en producción (0.3, 0.4, 0.5, 0.6, 1.1 y 1.2): eran filas paralelas creadas por las sesiones implementadoras (Cursor/GLM) con su propio nombre. Su contenido útil se fusionó en las filas canónicas (que ya estaban Done) y las duplicadas quedaron en Blocked como evidencia, según la regla del dueño. En el repo, el plan de ORBIT 01 (cerrado desde el 22 de agosto) se movió de la raíz a `plans/orbit-01.md`, con `plans/manifest.json` apuntando ahí: todos los planes viven ahora bajo `plans/`, y el monitor del harness deja de avisar en cada sesión que «el plan lleva 244 horas sin moverse» por un archivo que era histórico. Nada de esto toca datos, motor ni dashboard.

**2026-09-02 — La palanca de mapeo quedó cerrada: las campañas de Arras en México ya muestran lo que dejan.** Faltaba mapear los anuncios de México a productos del catálogo. El dueño archivó el anuncio muerto (`B09QC3X991`, producto sin oferta en México) y decidió los 10 casos: 5 se ligaron a su producto y 5 —variantes que no existen en Odoo y con inventario en cero— se archivaron (35 anuncios en 7 campañas, con su reversa guardada). Al hacerlo apareció un último obstáculo: dos productos «Peseta» sólo tenían costo desde el 18 de agosto, y la regla de la vista exige costo de TODOS los productos del grupo en CADA día de la ventana de 90 días — dos huecos dejaban a los seis grupos de Arras callados. Se sembró su costo histórico (migración 0011, PR #116), tomado de sus hermanos de familia y **sellado por el dueño**, con la misma historia copiada a contabilidad; la prueba de que ambas fuentes cuadran es que la carga de costos siguiente salió **sin un solo cambio**. Resultado: México pasa de 107 a 144 anuncios publicando contribución, y de 64 a 27 entidades bloqueadas por catálogo incompleto. Queda declarado sin trabajar un residuo de 39.46 MXN de gasto en 90 días (tres grupos de ASINs de cola larga). Dato para el dueño: **Arras Productos** es la única campaña negativa en los dos extremos del rango — 33.99 MXN gastados sin una sola venta atribuida.

**Nota de método:** la migración 0011 lleva la primera excepción declarada al candado que impide reescribir costos publicados. Uno de los dos productos tenía el mismo costo antes y después, y dejarlo como dos tramos pegados habría hecho que el sistema rechazara ese producto en cada carga futura —congelándolo para siempre—, así que su historia quedó como un solo tramo, lo que obligó a borrar la fila vieja. Se hizo con el candado apagado y re-encendido dentro de la misma transacción, con su **reversa implementada y probada antes** (regla del repo: nada irreversible sin vuelta atrás), y el candado quedó verificado como armado en producción.

**2026-09-01 — Cross-review de la 1.5 (claude + codex + grok, una sola ronda) cerrada con tres correcciones ya en producción (PR #109, migración 0010).** Nada de lo publicado cambió —verificado en vivo: mismas 108 entidades MX y 273 US, mismas marcas, mismos números—; lo corregido era latente: (1) la marca «precio min multilisting» podía prenderse aunque el precio menor no hubiera participado del cálculo (producto sin ventas en la ventana); ahora solo se prende si de verdad se usó. (2) Los números publicados podían descuadrar en 0.0001 si alguien restaba columnas a mano; ahora cuadran exactos. (3) La guía de rollback de la migración 0008 no mencionaba que los permisos de lectura se pierden en un DROP y hay que re-crearlos; ya está corregida. Se declararon sin corrección: sin el candado de «precio único» ya nada frena un precio basura en el catálogo (regla sellada por el dueño; si se quiere, futura alerta de dispersión), y los dos pares de ASINs duplicados siguen siendo decisión de negocio abierta.

**2026-09-01 — ORBIT 06 tarea 1.5 CERRADA: Estados Unidos ya publica su contribución.** El motivo por el que US salía «sin dato» no era mapeo (todo lo activo está mapeado) sino que dos productos —NH Blanco con broche 19mm Plateado y NH PERS Italia Plateado— se venden en Amazon US bajo dos ASINs con precios distintos. La regla vieja exigía un solo precio por producto y prefería no publicar nada. El dueño selló la salida: **usar el precio menor y marcarlo**. Desde hoy las 15 campañas de US publican su rango, todas con la marca «precio min multilisting» en el dashboard y en el digest de Telegram; México quedó idéntico (verificado fila por fila). De pilón se corrigió que los números calculados salían con colas de ~40 decimales; ahora salen con 4, como todo el dinero del sistema. Decisión de negocio que queda abierta: si esos pares de ASINs duplicados deben seguir coexistiendo o conviene archivar uno.

**2026-09-01 — ORBIT 06 tarea 1.3 EN PR: el digest diario de Telegram ya incluye la contribución por plataforma como rango (nunca un número único), con la etiqueta «contribucion pre-cargos · no decisoria». Si una plataforma no publica filas —Estados Unidos hoy, por diseño—, el mensaje dice «sin dato» y cuenta cuántas entidades faltan y por qué motivo; no inventa cero ni se calla. Si el residual del TACoS del mes es mayor que cero, sale una línea corta (hoy 4.75 MXN en agosto). Lectura solo con ORBIT_DSN_READ; un fallo del canal sigue sin tumbar el ciclo. **CERRADA 2026-09-01 (PR #100, en producción) y el envío real quedó verificado el mismo día: el digest del ciclo #30 llegó al Telegram del dueño.**

**2026-09-01 — ORBIT 06 tarea 1.4: el dashboard ya muestra la contribución por campaña como rango.** Cada fila suma lo que publican sus anuncios hijos (palabra clave / producto): dos números (con y sin ventas de arrastre), la moneda, su propia edad de dato (fechas por campaña, además de la ventana de 90 días maduros) y la etiqueta «contribución pre-cargos · no decisoria». Si no hay dato, se ve un guion con el motivo dominante (serie incompleta, catálogo parcial, sin FX, etc.), jamás un cero; y si el rango es parcial (hay hijas ausentes), la fila lo declara («parcial: motivo») en vez de disfrazarse de completa. Estados Unidos puede quedar vacío o sin números a propósito mientras el catálogo no cubra al 100 %. Solo lectura; cero escritura nueva. **CERRADA 2026-09-01 y ya en producción.** Al probarla en vivo salieron dos bichos que las pruebas no veían: la consulta tardaba más de 100 segundos (se reescribió la vista para agregar por día y producto; ahora ~2.5 s, PR #101) y cada campaña sumaba su contribución una vez **por día** en vez de una sola (~65-90 veces de más; corregido y verificado contra la suma directa, PR #102). Hoy la pantalla responde en ~15 s: México muestra 23 campañas (13 con números) y Estados Unidos 15 sin números, por el catálogo parcial ya declarado.


**2026-09-01 — ORBIT 06 tarea 1.2 CERRADA: la contribución por anuncio ya se puede consultar en México.** 108 anuncios publican su rango (con y sin ventas de arrastre) sobre 90 días maduros; ningún número único, ningún dato inventado. Estados Unidos publica cero **a propósito**: la regla exige conocer el costo de TODOS los productos de un grupo, y los grupos de EE.UU. son tan grandes que siempre incluyen productos sin mapear — la palanca es mapear, no aflojar la regla. De pilón, el residuo del arreglo del TACoS quedó visible en la propia vista: 4.75 pesos exactos en agosto.

**2026-08-31 — ORBIT 06 tarea 1.2 EN PR: ya existe la vista que calcula lo que deja cada anuncio (palabra clave o producto). Publica siempre dos números —con y sin ventas de arrastre— nunca uno solo; si falta dato, la fila no sale. En México usa el precio realmente cobrado; en Estados Unidos el de vitrina (aún no decisoria). Incluye también el contador del gasto de campaña sin contraparte, la lista de anuncios excluidos con su motivo, y la comparación del gasto de ads contra el libro. Falta la corrida real del lead tras el deploy (`docs/SELECT-EVIDENCIA-1-2.md`).**

**2026-08-31 — ORBIT 06 tarea 1.1 CERRADA: ya está diseñado cómo medir lo que deja cada anuncio.** El diseño (de Cursor, sellado por el lead con una mejora) publica la contribución de cada anuncio siempre como rango —con y sin ventas de arrastre— y nunca como un número único. La mejora del sello: en México el costo se compara contra el precio realmente cobrado (sin IVA, de las ventas reales), no contra el precio de vitrina — así el número no sale inflado. Sigue la implementación (1.2).

**2026-08-31 — ORBIT 06 tarea 0.7: se encontró y corrigió que el gasto en anuncios se contaba DOS VECES.** Al medir contra los datos reales para aprobar el umbral de cobertura, apareció un patrón sospechoso: el gasto guardado a nivel de campaña era casi idéntico al gasto guardado a nivel de palabra clave/producto — en Estados Unidos, exacto centavo por centavo. Eso significa que el sistema estaba anotando el mismo peso gastado dos veces: una vez como "gasto de la campaña" y otra como "gasto de sus palabras clave/productos". La medida de TACoS (qué tan caro sale vender, comparando gasto de anuncios contra venta) tomaba ambos y sumaba doble, así que salía casi el doble de lo real. Se corrigió para que la medida solo cuente el gasto al nivel de detalle correcto (palabra clave/producto), el mismo nivel que ya usa el motor para decidir — el nivel de campaña queda fuera porque es un duplicado, no gasto adicional. Con el número correcto, la medición real de cobertura para aprobar la fase siguiente fue: México 89.3% y Estados Unidos 100% a nivel de grupo — ambos por arriba del umbral de 85% que aprobó el dueño, así que la fase siguiente queda desbloqueada.

**2026-08-31 — ORBIT 06 tarea 0.6 CERRADA y verificada de punta a punta por el lead (recuento independiente idéntico; deploy formal con corrida de control exacta): el libro de Amazon ya está en Orbit.** Se copiaron **8,041 hechos** (1,650 ventas, 4,221 fees, 2,034 retenciones, 136 refunds) desde contabilidad; ventana **2025-11-14 .. 2026-08-31**; MeLi quedó fuera; el ISR sin orden entró sin prorratear; **106 filas se descartaron** por `ledger_convencion_signos` (fee+/refund+/sale≤0) — **no se voltearon**. Huecos de producto: 229 ventas sin ASIN, 12 sin listing, 0 sin cantidad. Re-correrla (runs 54-55 post-review) no escribe nada. Residual declarado: ~6 ISR fee+ fuente no entran (CHECK D4); `amazon_us` viene 100 % MXN en la fuente (D8) para la 0.7.

**2026-08-31 — ORBIT 06 tarea 0.5 CERRADA: los tipos de cambio ya viven en Orbit.** 210 tasas diarias (oct 2025 → hoy) con las etiquetas corregidas; el convertidor las resuelve bien en vivo (día exacto, día anterior cuando hay hueco, y cero resultados cuando no hay tasa utilizable — jamás un número inventado). Con esto, lo vendido en dólares por fin puede compararse con lo vendido en pesos.

**2026-08-31 — ORBIT 06 tarea 0.4 EN CURSO: el vínculo anuncio→producto ya no vive en el grupo.** El dueño eligió atribuir margen al **anuncio de producto**, no al grupo: en Estados Unidos ningún grupo vende un solo ASIN, así que el modelo viejo dejaba esa cuenta entera sin margen. Se materializan anuncios ENABLED y PAUSED; los archivados no se guardan. El `listing_id` se escribe solo en esas filas nuevas, nunca en el grupo. **CERRADA el 2026-08-31**: el lead aplicó la migración (con respaldo verificado del esquema y chequeo previo), desplegó y corrió la ingesta real contra Amazon.

**Resultado**: se materializaron **12,527 anuncios** (7,709 en México, 4,818 en Estados Unidos) y se descartaron 25,454 archivados. **8,626 quedaron ligados a su producto (69 %)**, y ninguna otra entidad recibió el vínculo — el grupo quedó en nulo como se decidió.

**El dato que importa para la fase siguiente**: de los anuncios que resuelven producto, **el 100 % llega hasta un costo**. La cadena anuncio → producto → costo no pierde nada por el camino, así que el margen ya se puede calcular para ese 69 %. El hueco restante son 3,901 anuncios cuyo producto no está mapeado — el mismo hueco de la tarea anterior, ahora visible a nivel de anuncio.

Antes de aprobar, el lead verificó que meter 12 mil entidades nuevas **no contamina al motor** (todas sus consultas de decisión filtran por tipo) y que la descarga diaria entra con margen. Una objeción suya se disolvió al revisar: temía que la frescura de la estructura se falseara, pero la ingesta quedó integrada al sync existente en vez de correr aparte.

**2026-08-31 — Un hallazgo que cambia la tarea siguiente: casi ningún grupo de anuncios vende un solo producto.** Apenas quedó vivo el permiso de la 0.3, el lead midió lo que el plan tenía como desconocido. El plan proponía que, si un grupo anuncia varios productos, no se le atribuyera margen y se contara aparte — dando por hecho que era el caso raro. **Es al revés**: entre los grupos con anuncios activos, en México sólo 4 de 32 venden un producto único, y **en Estados Unidos ninguno de los 48**. El peor caso llega a 1,259 productos en un solo grupo.

Con esa política, la cuenta de Estados Unidos habría quedado **entera** sin margen atribuible. Queda descartada, y la tarea siguiente ya no arranca programando sino **eligiendo cómo atribuir el margen cuando un grupo vende muchos productos** — una decisión del dueño, porque cambia qué significa el número. Las tres salidas posibles quedan escritas en el plan con su costo.

De la misma medición: filtrar por estado es obligatorio (tres de cada cuatro anuncios en México están archivados) y falta una pieza técnica menor que la tarea anterior no cubría.

**Aparte, deuda de proceso saldada**: se sube al plan la exigencia de cerrar el marcador de la tarea y esta misma línea de contexto. En las tres entregas —dos de GLM, una de Cursor— las terminó cerrando el lead. Tres implementadores distintos fallando lo mismo no es descuido de ellos: era que vivía en un mensaje de chat en vez de en el contrato.

**2026-08-31 — ORBIT 06 tarea 0.3 CERRADA: Orbit ya puede leer qué producto anuncia cada anuncio.** Faltaba un permiso: la lista de anuncios de producto de Amazon no estaba habilitada en el candado de lectura del cliente, así que la llamada se rechazaba. Ahora sí, y con el ritual que el repo exige para tocar ese candado: **primero la prueba en vivo** —la hizo el lead porque pide credenciales que sólo viven en el servidor— y recién después el cambio de código. La prueba respondió correctamente en México y Estados Unidos, con 31,063 y 6,918 anuncios respectivamente.

Implementación de **Cursor**, su primera entrega en este repo: cinco líneas de código y once de prueba, sin tocar nada más. El lead verificó que la prueba **discrimina de verdad** (quitando la línea del permiso, falla exactamente ese test) y que el conteo del candado obliga a actualizarlo a propósito, que es justo lo que impide que crezca por accidente.

**Y la prueba en vivo trajo un hallazgo que simplifica la tarea siguiente**: cada anuncio viene con el ASIN y el código de producto **juntos**, así que el vínculo anuncio→producto es directo. Con el aviso de que esos totales incluyen todos los estados y hay que filtrar.

**2026-08-30 — ORBIT 06 tarea 0.2 CERRADA: el anuncio ya se puede conectar con el producto.** Existe el mapa producto ↔ mercado ↔ ASIN (GLM, PR #67): **513 listings** (337 MX / 176 US) que alcanzan **265 productos con costo**. La trampa que el lead había medido antes de asignar la tarea —los SKU de Amazon son autogenerados y NO son los de Odoo, así que unir por texto da 1 % de cobertura— quedó sellada: la unión va **solo** por la tabla puente. Verificado contra la base viva: 0 ASIN duplicados por plataforma, 0 violaciones del CHECK precio/moneda, monedas exactamente partidas por mercado, y `ad_entity.listing_id` **intacto** (eso es la 0.4). Recálculo independiente del lead desde el origen: idéntico. No-op confirmado tres veces.

GLM cazó un bug propio serio con la doble corrida: los SELECT previos abrían una transacción implícita y al cerrar la conexión **se revertía todo** — la corrida imprimía 513 escritas y la base quedaba vacía. **Cero daño verificado**: esos dos `ingest_run` ni existen. Quedó como regla en el código y un test de regresión.

**Corrección del lead**: la entrega definía su propio mapa de monedas, idéntico al que ya vive en el motor. Hoy coincidían, pero un tercer mercado actualizaría uno y no el otro — y este proyecto ya pagó ese error caro (un reporte que daba pesos por dólares: 18.66× siempre a favor de "todo es rentabilísimo"). Unificado, con **candado nuevo** que caza cualquier mapa de moneda no declarado. Al ponerlo aparecieron otras dos preexistentes y deliberadas —el cliente de escritura y el descubrimiento de perfiles—, las dos declaradas con su razón y sin tocarse.

**Corrección del propio lead (cross-review kimi, 2026-08-30)**: la primera versión de ese candado tenía un punto ciego —sólo veía una de las dos formas de escribir el mapa— y el cierre afirmó "una tercera" cuando eran cuatro los lugares que ataban mercado a moneda. **El comentario original de GLM señalaba justo la que faltaba, y el lead lo descartó por error: GLM tenía razón.** Corregido el detector para que decida por el valor y no por la forma, y corregido también un efecto colateral propio: la prueba del candado escribía un archivo dentro del repo en vez de en un directorio temporal.

**Y una segunda corrección, ésta de qwen**: aquella primera tapó un agujero y abrió otro sin decirlo — al exigir que todas las claves fueran constantes, dejaba escapar mapas escritos de otra forma. Ya está corregido, con la prueba cubriendo también los casos que el candado descarta a propósito. Nota de herramientas: qwen llevaba tres intentos fallidos como revisor por una bandera obsoleta en el kit de calidad; quedó arreglado y verificado como revisor de **solo lectura**, y ésta fue su primera revisión útil.

**2026-08-30 — ORBIT 06 arranca: la tarea 0.1 CERRADA, el margen ya tiene su primera pieza.** La ingesta de productos y costos existe y corrió contra la base viva (GLM, PR #63): **1,087 productos y 1,955 vigencias de costo** desde las 2,708 filas de contabilidad, con 753 rotaciones intradía colapsadas a una vigencia por día. El lead lo verificó **contra la base, no contra la evidencia**: una sola vigencia abierta por producto (1,087 de 1,087), 100 % MXN, cero costos ≤ 0, y un recálculo INDEPENDIENTE del origen que da los mismos 1,955. Re-correrla no escribe nada (**confirmado cuatro veces**). Los candados de solo-histórico sobrevivieron intactos a la remediación de una carga fallida, y el manejo de dinero **rechaza antes de redondear**, así que una precisión genuina nunca se redondea en silencio.

**La duda del IVA quedó resuelta con lectura directa de Odoo**, no con argumentos. El dueño la planteó bien: sus proveedores chinos no cobran IVA y los mexicanos sí, y suponía que Odoo guardaría el costo con el precio completo. **No se cumple**: en 227 de 227 líneas de compra el precio unitario es neto, 213 llevan el impuesto ENCIMA y 14 no (la mezcla existe pero viaja fuera del costo), y de 24 productos comparables **3 coinciden exactos con el precio sin impuesto y CERO con el precio con impuesto**. Importaba más de lo que parecía: no era un sesgo parejo sino **diferencial** —los productos de proveedor mexicano se habrían visto 16 % más caros que los importados y el motor habría movido presupuesto por una razón falsa—. Verificado, ese riesgo no existe.

**Dos residuales declarados antes de la Fase 1**: la fusión de días consecutivos de igual costo **nunca se ejercitó** (hay cero casos reales en el origen: cubierta por test, sin evidencia de producción), y la ingesta es **manual, sin cron** — los costos empiezan a envejecer hoy y la cadencia se decide antes de que la vista de margen lea datos viejos sin avisar.

**2026-08-30 — NADA SE ARRASTRA A ORBIT 05: inventario de cierre.** Decisión del dueño (2026-08-29): antes de empezar la fase siguiente, todo pendiente se cierra o se PARQUEA con dueño, disparador y tarea propia en el tracker — ninguno vive solo en prosa de un plan. Estado: **(a) accionable hoy — HECHO el 2026-08-30 02:12 UTC**: el deploy de master al contenedor. El contenedor corría todavía la imagen de 1.1, así que el código de 1.4 (quota + aviso de cap agotado), 1.5 (pantalla) y los defaults por moneda de 1.2 estaba mergeado pero **NO vivo** — y con esa imagen `tools/snapshot_listas.py` fallaba con `ImportError`, o sea **el backup del día del flip no habría corrido**. Imagen nueva `757c972b` (antes `a5d5d579`), con respaldo del `app/` anterior en `app.bak-predeploy-20260830` y los 28 `.py` verificados idénticos a master por md5 ANTES de construir. Verificado DENTRO del contenedor: CORTES 03 intacto (100/100/40/500), `KINDS_QUOTA`/`estado_quota` presentes, `DEFAULTS_POR_MONEDA` con MXN 1.00/45.00, `listar_todo` público; el endpoint de salud ya devuelve la quota (10/2/5/2, fuente `config_vigente`) y `/salud` la dibuja; **el snapshot vuelve a correr con los conteos exactos de 1.3** (MX 2,645/2,597/861 · US 1,336/1,536/549). Puerto solo en loopback y wg0, `secrets/` 700/600 intactos, cero errores de arranque, y los cuatro crons de Orbit (crontab de `gon`) sin tocar. **Nada se flipeó: la escalera y los cuatro goals siguen en `shadow`.** **(b) Espera por calendario** — 2 semanas de shadow (~2026-09-07) y, atados a eso, el día del flip y la verificación adversarial triple; no son trabajo pendiente sino candados que corren solos. **(c) Parqueado en el tracker con disparador** — revocación del ADMIN OPTION de `orbit_test` (hito, no fecha); **keywords duplicadas dentro de cada mercado** (medido 2026-08-29: 58 pares en MX y 60 en US; solo 13 y 12 tienen ganador claro por datos; el resto no vende en ninguna copia; dinero de la copia perdedora cuando la hermana sí vende = 452.78 MXN + 160.46 USD en 90 días — MX y US **jamás se comparan entre sí**, son mercados distintos; recomendación: decidirlo DESPUÉS del flip, porque el motor live puede cambiar cuál copia gana); **AGM2M (165)** diferida con disparador "48h de live"; **token de cloudflared** (rotación bloqueada hasta inventariar qué rutea el túnel); **partición de `app/ads/structure.py`** (916 líneas, excepción declarada en la allowlist). **(d) Otra fase** — edición de settings del dashboard, en `ORBIT 16`.

**2026-08-29 — ORBIT 05 PREFLIGHT CERRADO (1.1-1.8, todo en master).** Las ocho tareas están Done: CORTES 03 desplegada y verificada en vivo (1.1), defaults de piso/techo por moneda + migración 0003 aplicada en producción (1.2), `tools/snapshot_listas.py` con conciliación real contra el cache (1.3), quota `used/cap/fuente` en el endpoint + aviso Telegram de cap agotado (1.4), la pantalla `/salud` dibujándola (1.5), las cuatro decisiones del dueño registradas (1.6), destino de harvest US reactivado con dedup y terna del goal 5 sembrada (1.6a), y el hito de revocación de `orbit_test` ADMIN OPTION cerrado en DEPLOY.md con su tarea de tracker (1.7).

**Estado de la cola de apply hoy (SELECT en vivo, 2026-08-29): 5 filas, TODAS `shadow`** — 1 `discarded` (la 2, con motivo declarado por el dueño para probar CORTES 03 en vivo), 2 `vetoed` (la 3 por delegación, actor `gon`; la 4 veto PERSONAL del dueño, actor `gon-personal`) y 2 `pending_veto` (la 5, harvest MX; la 6, el primer harvest US nacido del ciclo 24). **Cero escrituras a Amazon**: la escalera sigue en `shadow` y los cuatro goals (4 MX; 5/6/7 US) están en `mode='shadow'` con la config vigente id 10. Base viva: 24 ciclos desde el 2026-08-23 y 1,475 decisiones (1,389 bid / 48 negative / 34 pause / 4 harvest).

**Lo que FALTA para el flip** — nada de esto es código; son candados humanos y de calendario (`docs/APPLY.md` §12 ítems 3-10): (a) **2 semanas de shadow cumplidas, llegan ~2026-09-07** (shadow desde 2026-08-24); es el único sub-ítem abierto del candado 3, porque la firma del spot-check (b) y CORTES 03 desplegada (c) ya están cumplidas; (b) el **backup pre-cutover REAL del mismo día** —el de `backups/precutover_orbit04_2026-08-28/` quedará obsoleto porque la base y las listas cambian a diario— y el discard masivo de las filas shadow; (c) la **verificación adversarial triple de las primeras decisiones APLICADAS en vivo**. **Ya NO falta la firma del dueño sobre `plans/orbit-05.md`: la dio el 2026-08-29** ("2 firmado"), registrada en el header del runbook — aprueba el PROCEDIMIENTO, no dispara el flip (el día del flip exige su propio go). Deudas declaradas abiertas: `orbit_test` conserva ADMIN OPTION sobre el cluster de prod hasta que la DB de test salga de ahí (hito y tarea de tracker en `docs/DEPLOY.md`), y los textos EXACT duplicados fuera del alcance de 1.6a — el GO "3. go" del 2026-08-29 cerró el de 'arras de boda cristiana' (de 6 entre las hermanas quedan 5), y el barrido completo mostró **más duplicados preexistentes en US y MX fuera de esas tres campañas**, declarados y sin tocar.

**2026-08-29 — ORBIT 05 preflight 1.6a CERRADA** (PR #54 → master `38693aa`; GO del dueño "go con la 1"): USPerNog Exact US (3919) **reactivada por API con dedup** — 9 pausas de keywords EXACT ANTES del resume por el camino sellado de PR #37 (cada PUT con readback, reconciliación 9/1 ok; `--solo-campana` + `--esperado-external` anti-typo + guard de ya-ENABLED, 17 tests). La re-derivación colapsada (v_metric_latest) cazó que la tabla del lead sumaba la bitemporalidad SIN colapsar (inflaba 2-4.6x) y volteaba 1 fila ('silver arras for wedding'); el dueño resolvió "A": lista tal cual. Quedan DECLARADOS 6 textos EXACT duplicados fuera del alcance (5 entre 3909↔3926, varios convirtiendo en ambas; + 'arras de boda cristiana' 3919↔3909, copia 3909 sin datos 90d que la tabla listó "—") — para decisión futura, nada se tocó fuera de lo autorizado. **Goal 5 con terna completa** (251723662158466 / 522582072501798 / **0.68 USD** = mediana de las EXACT ENABLED de las 3 hermanas n=21, decisión "A" del dueño; la mediana literal n=3 daba 1.25). **Ciclo shadow 24 US: desaparece `harvest_sin_config` y nace la primera decisión harvest** (1475, 'arras para boda cristiana': 58 cl / $24.76 / 2 órdenes / $203.20) hacia la terna sembrada — con EXTERNALES en inputs, encolada shadow pending_veto, cero escrituras. Evidencia `out/orbit-05-preflight-1-6a-20260829.md`.

**2026-08-29 — ORBIT 05 preflight 1.5: la pantalla /salud muestra la quota del dia** — la tarjeta de cada plataforma dibuja "Quota del dia" con una fila por forma (bid/pause/negative/harvest, recorriendo el dict que llega del endpoint de 1.4): used/cap y estado — `fila_del_dia` (chip ok: el cap INMUTABLE que rige hoy) vs `config_vigente` (chip neutro: sin consumo hoy); cap nulo se ve como "—" CON etiqueta (`sin_clave` = fail-closed, `config_rota` = estado de alarma), jamas como 0; `used >= cap` se ve SATURADA (el estado que dispara el aviso Telegram de 1.4). Server-rendered, sin JS ni CSS nuevo (CSP 'self'), cero escritura.

**2026-08-29 — ORBIT 05 preflight 1.4: la quota ya es visible antes del primer cobro real** — `/api/dashboard/salud` expone por plataforma y forma (bid/pause/negative/harvest) `used/cap` con su `fuente` (`fila_del_dia`: el cap INMUTABLE de la fila de hoy aunque la config cambie / `config_vigente` / `sin_clave` = fail-closed explícito), y el ciclo avisa por Telegram (fail-silent, UNA vez por cap y día, anclado en la transición `used == cap`) cuando una rampa se agota; si el canal falla queda la NOTA en Salud y el ciclo sigue 'done'. Detalle en `docs/APPLY.md` §5.6.

**2026-08-29 — ORBIT 05 preflight 1.3 CERRADA** (PR #48 → master `14ae6c0`): el snapshot de listas de Amazon del backup pre-cutover deja de ser codigo inline y es `tools/snapshot_listas.py` con tests — lee los tres `/sp/*/list` con la paginacion existente (guard de `totalResults` incluido), agrupa por campana, concilia contra `ad_entity` con diferencia CON SIGNO y no puede mutar nada (candado de arquitectura con allowlist positiva). Corrida real: MX 2,645 kw / 2,597 neg / 861 targets; US 1,336 / 1,536 / 549. **Diferencia 0 contra el cache en keywords y targets de ambas plataformas** (verificado por el lead con SELECT propio sobre `ad_entity` incl. ARCHIVED); los negativeKeywords NO tienen espejo en `ad_entity`, así que su conteo va declarado con `cache=None` y queda FUERA de esa conciliación. Escritura endurecida tras 6 hallazgos de bots (temporal exclusivo, dir 700, symlink rechazado, runbook que propaga el rc). Preflight: 1.1, 1.2 y 1.3 Done; siguen 1.4/1.5 (quota visible), 1.6a (destino harvest US), 1.7 y 1.8.

**2026-08-29 — ORBIT 05 preflight 1.2 CERRADA** (PR #46 → master `66d449e`): default de piso/techo de goals **POR MONEDA** (USD 0.10/2.50, MXN 1.00/45.00; otra moneda = error explícito) con `DEFAULTS_POR_MONEDA` como fuente única, y **migración 0003 aplicada en goncloud el 2026-08-29 04:10 UTC** (GO del dueño; chequeo previo: cero goals MXN con techo USD; backup del schema; verificado `column_default = NULL` con `NOT NULL` intacto y los 4 goals sin cambio): la DB ya no tiene DEFAULT, así que **un goal que nazca sin piso/techo revienta** en vez de heredar números pensados en USD. Preflight: 1.1 y 1.2 Done; siguen 1.3-1.8.**

**2026-08-28 — ORBIT 05 preflight 1.3: el snapshot de listas Amazon del backup pre-cutover es ahora el tool `tools/snapshot_listas.py` del repo con test (en 4.4 corrió inline); flags excluyentes `--out`/`--solo-conteos`, escribe `listas_por_plataforma.json` a 600.**

**2026-08-29 — CORTES 03 MERGEADA (#43 → master `5e5b16b`) y DESPLEGADA en
goncloud (preflight 1.1 de ORBIT 05, GO del dueño; imagen `a5d5d579`,
constantes 100/100/40/500 leídas dentro del contenedor). Verificación con
ciclos shadow 19 (US, 109) y 20 (MX, 47) por el mismo camino del cron:
cero pauses nuevas, 156/156 decisiones congelan `cost_min_usado`; **prueba
directa** en el ciclo 21 (tras descartar con motivo la fila shadow 2 que
bloqueaba su clave): la keyword de la 774 (72 clics / $25.21 / 0 ventas)
salió como bid 0.25 → 0.22 (banda −12 %, `umbral 100`, `cost_min_usado
40`) — **ya no pausa**. Efecto del techo MX
45.00: `rango_bloquea_ajuste` MX bajó de 43 (ciclo 18) a 2 (ciclo 20). Las
47 decisiones MX del ciclo 20 concilian así: 5 que ya decidían en el 18 +
41 desbloqueadas por el techo (43 → 2) + 1 que salió de
`pause_cortes_incompleto` (47 → 46); por banda: 37 −12 %, 6 +15 %, 4 −25 %;
máx old 42.63 → new 37.51, dentro del techo.
Checklist §12 ítem 3(c) marcado.** Detalle previo (PR #43):
umbral de PAUSE del dueño → **100 clics / 40 USD / 500 MXN** (origen
spot-check 4.4 fila 30 / decisión 774: 72 clics / 25.21 USD / 0 ventas
pausó prematuro); fallback y piso legacy de PAUSE también suben a 100;
NEGATIVE intacto. Replay hecho **fiel por construcción** (decisión del
lead 2026-08-28): el motor de bids congela `cost_min_usado` en su freeze y
las filas históricas sin la clave rejuegan con los históricos REPLAY_*
(25 clics / 12 USD, solo-replay) — 34/34 pauses medidas fieles.

**Última actualización: 2026-08-28 — ORBIT 04 CERRADA (4.1-4.4, todo en
`shadow`); el dueño FIRMÓ el spot-check el 2026-08-28 ("spot check
confirmado", revisado en lenguaje de negocio con el lead) → DoD de 4.4
cumplido, `ORBIT 04` Done en AppFlowy:**

- **4.1** deploy endurecido (env por servicio, non-root uid 10001, 0002
  aplicada, wiring admin→ledger).
- **4.2** seeds: goal 4 (platform MX) con terna harvest → Arras Manual
  (2.50 MXN, mediana de bids EXACT reales); goals 6/7 scope=campaign (A1U
  3909, AU2 3926) en shadow; caps día 1 en config id 7 (10/2/5/2 por
  plataforma); fail-closed de quota probado en vivo en ambos sentidos.
- **4.3** ensayo E2E: 4/4 formas neto-cero contra Amazon real (ledger probe
  22-29), SHAPES re-confirmados; veto real por endpoint (fila 3, actor
  'gon', delegado) + **veto PERSONAL del dueño (fila 4, actor
  'gon-personal', 06:54 UTC)**; neto-cero RE-VERIFICADO post-sync en 4.4.
- **4.4** backup pre-cutover VERIFY_OK en
  `backups/precutover_orbit04_2026-08-28/` (dump 762 KB + globals + CSV de
  ad_entity_state 5,899 filas + listas Amazon de 2 plataformas; restore real
  con conteos idénticos 4/29/9/977/5899); spot-check de 33 decisiones shadow
  recalculadas por el implementador (GLM, autor del motor: NO es una
  verificación independiente) + 11 re-calculadas por el lead desde
  `bid.py` (**0 divergencias** en ambas; tabla en AppFlowy "ORBIT 04 4.4 —
  spot-check" y en `out/orbit-04-4-4-cierre-20260828.md` §3 — **FIRMADA
  por el dueño 2026-08-28**, la única validación independiente; de su
  revisión salieron el techo MX y CORTES 03, ver abajo);
  escalera shadow verificada (config id 10 mode=shadow, attempts solo probe,
  quota 0 filas, cola 2 pending + 2 vetoed, `/api/ads-optimizer/status`
  cita shadow); corrección 1e41a1f: la verificación adversarial TRIPLE se
  movió al checklist §12 (ítem 9, ritual de ORBIT 05). La FIRMA del dueño
  del spot-check quedó como ítem 3 SIN MARCAR del checklist §12: candado
  pre-flip.

**Estado de la cola (2026-08-29, tras el preflight 1.1)**: fila 2 pause
**`discarded`** (descartada por el dueño con motivo declarado para liberar
la clave de efecto de la keyword de la 774 y probar en vivo CORTES 03),
fila 3 harvest `vetoed` (gon), fila 4 harvest `vetoed` (gon-personal),
fila 5 harvest `pending_veto` — cero released/applying. El día del cutover
queda **una** fila shadow pendiente (la 5, más las que nazcan hasta
entonces): se descartan en bloque con `app_admin`, en el orden sellado
**backup real → discard → flip → rampa** (checklist APPLY.md §12 ítems 4-6).

**Prerequisitos de ORBIT 05**: cumplidos — veto del dueño (fila 4, con su
mano), ensayo E2E (4/4 neto-cero), **runbook del backup pre-cutover
ensayado y verificado restaurable** (el snapshot del 28 NO es el punto de
restauración del flip), caps día 1 sembrados, spot-check preparado (33
decisiones, implementador + 11 del lead) **y FIRMADO por el dueño
2026-08-28** (AppFlowy "ORBIT 04 4.4 — spot-check shadow", Done).
**Pendientes** — 2 semanas de shadow (~2026-09-07);
`tools/snapshot_listas.py` con test; **backup pre-cutover REAL el día del
flip** (ítem 4); verificación adversarial TRIPLE de las primeras
decisiones live (ítem 9); resto del preflight (1.2-1.8 de
`plans/orbit-05-preflight.md`). **CUMPLIDOS post-#40**: techo de bids MX
1.00/45.00 MXN en el goal 4 (ver abajo) y **CORTES 03 mergeada, desplegada
y verificada en vivo el 2026-08-29** (preflight 1.1; ítem 3c del checklist
§12 marcado).

**Decisiones del dueño salidas del spot-check (2026-08-28, post-#40)**:
(1) **Techo de bids MX**: el default 2.50 del esquema (número pensado en
USD, `goals.py DEFAULT_CEILING`) estaba aplicado al goal 4 de México en
MXN — 144/233 keywords y 44/51 targets MX activos tienen bid > 2.50 MXN
(mediana 2.92 / 8.98, máx 42.63): en live los habría aplastado hacia 2.50.
Verificado que NO hay mezcla de monedas (`bid.py:89` fija MXN/USD por
plataforma; cache y decisiones 100% consistentes). El dueño aplicó desde su
terminal `goals set 4 --floor 1.00 --ceiling 45.00` (verificado por SELECT,
`updated_at` 18:05 UTC); US queda 0.10/2.50 (máx real 2.00). (2) **Umbrales
de pausa**: 72 clics / $25 / 0 ventas (fila 30 del spot-check) le pareció
poco → PAUSE exigirá **≥100 clics y ≥$40 USD** sin ventas (**MX: ≥500
MXN**, confirmado por el dueño) = tarea **CORTES 03** (CORTES 02 ya es la lista curada de términos producto-diferente; cambio de spec v3: `cortes.py
F_PAUSE/LEGACY_PAUSE`, `bid.py PAUSE_COST_MIN`, tests, docs/traspaso),
prerequisito de ORBIT 05, implementa GLM por PR con TDD. La firma del
spot-check se cumplió el 2026-08-28.

**Decisiones del dueño para ORBIT 05 (2026-08-28, respuesta literal "1. si
2. no se 3 acotar 4 todos" a las 4 preguntas de `plans/orbit-05-preflight.md`
1.6)**: (1) **destino harvest US = SÍ**: reactivar USPerNog Exact US
(ad_entity 3919, external 251723662158466, hoy PAUSED) y sembrar la terna
del goal 5 (preflight 1.6a; mutación real con autorización en el momento);
(2) **AGM2M (165) = DIFERIDO** ("no sé"): PAUSED, fuera del piloto, se
re-pregunta tras 48h live; (3) **halo US = ACOTAR con ambos supuestos**
(CONTEXTO "la pregunta sin respuesta"): la fase margin-aware reporta y
decide con el rango con-halo / sin-halo; ORBIT 05 sigue con revenue
completo; (4) **goals del día 1 = TODOS** (4 MX, 5/6/7 US); la rampa por
goal queda como mecanismo de rollback parcial. Con esto los planes
`orbit-05-preflight` y `orbit-05` quedan APROBADOS por el dueño — evidencia
de la aprobación: su mensaje literal en la sesión del lead (2026-08-28) y
la nota fechada en la fila `ORBIT 05` de AppFlowy (la PR #44 lleva la
misma cita; no hay review `APPROVED` de GitHub porque el dueño no revisa
por GitHub). El cutover no arranca antes de ~2026-09-07 ni sin el preflight
Done.

Previo (2026-08-28): ORBIT 04 4.3 CERRADA con DoD literal
(ensayo E2E + veto delegado en la fila 3 + VETO PERSONAL DEL DUEÑO en la
fila 4, actor `gon-personal`, 06:54 UTC, verificado en `apply_queue`;
prerequisito de ORBIT 05 cumplido). Review del lead post-cierre: pipefail
al shell local, token 600 por umask, residual del token en el historial
append-only de `config_version`, DoD de 4.4 con spot-check ≥20 +
adversarial triple, y el "neto-cero contra el cache" de la evidencia §6 era
ANTERIOR a la corrida (re-verificar post-sync en 4.4): re-corrida del smoke 2.5
contra el deploy real, mismas campañas sacrificables (A: USPerNog Category
Exact 251723662158466 — hoy PAUSED; B: USPerNog Auto Discovery
140602818838686), dentro del contenedor (tool a `/tmp` + `PYTHONPATH=/app`:
post-4.1 la imagen es non-root y sin tools; variante documentada en
APPLY.md §11d y en el docstring del tool). **4/4 formas ok/neto-cero**:
bid_keyword 0.51→0.52→0.51, negative crear+archivar, keyword crear+archivar
(bid 0.51 real leído), bid_target 0.32→0.33→0.32; ledger `apply_attempt`
probe ids 22-29 (quota_cobrada=false), config 8/9 de humo + **id 10 de
cierre limpia** (11 claves: mode, targets 20/20, caps 10/2/5/2). SHAPES
re-confirmados. **VETO REAL por el endpoint** (`POST /api/ads-optimizer/veto`):
fila 3 (harvest "arras matrimoniales cristianas") → `vetoed`,
`vetoed_by='gon'`, vence 2026-09-27. **Declarado: ejecutado por el lead por
delegación expresa del dueño** ("el veto el que consideres mejor"); la fila
2 (pause de una keyword con 62 clicks/$22.78/0 órdenes en 30d) se dejó
intacta a propósito — es un corte correcto. **Veto PERSONAL del dueño CUMPLIDO** el
2026-08-28 06:54 UTC: fila 4 (harvest "arras matrimoniales personalizadas")
→ `vetoed`, `vetoed_by='gon-personal'`, vence 2026-09-27 — ejecutado por
él desde su terminal contra el endpoint real, verificado en `apply_queue`.
La cola queda sana para el cutover: 2 shadow `pending_veto` (filas 2 y 5)
→ descarte en bloque en el flip; 2 `vetoed` terminales (3 y 4). Resto de la cola de fase: 4.4 cierre
(backup, CHAT-CONTEXT, PR final). Evidencia
`out/orbit-04-4-3-ensayo-e2e-20260828.md` + `out/smoke-apply-20260828.log`.

Previo (2026-08-27, noche): ORBIT 04 4.2 CERRADA (seeds de
configuración en vivo): goal 4 (platform amazon_mx) con terna harvest
completa → Arras Manual (external `97835222467967`, ad group
`272585315669297`, ambos ENABLED; `harvest_default_bid` 2.50 MXN = mediana
2.525 de los bids reales de 18 keywords EXACT ENABLED clampeada al techo
2.50 del goal), escrita por el camino único (`goals set` →
`goals_write.edita_goal`). Goals 6 y 7 scope=campaign: A1U Exact US (3909) y
AU2 Exact US (3926), USD, shadow, target NULL (la cascada da 20 desde
config). Caps día 1 en `config_version` id 7 (fila NUEVA append-only, la 6
intacta): `ads_apply_cap_*` = 10 bids / 2 pauses / 5 negatives / 2 harvests
por día y plataforma (rampa sellada: decisión 7 + APPLY.md §5.5). DoD en
vivo: `goal_harvest_completo` rechaza la terna a medias (CLI exit 2 y
`CheckViolation` con ROLLBACK); fail-closed de quota probado en ambos
sentidos contra el trigger vivo (sin clave no nace fila; con clave nace con
el cap de config; cap que no coincide también se rechaza). Divergencia con
la decisión sellada 15 ratificada por el dueño: manda el brief
(destino MX = Arras Manual; USPerNog 3919 sigue PAUSED — el destino US de
harvest queda como decisión abierta). `apply_quota_state` sigue en 0 filas
hasta el primer apply real; sistema en shadow. Evidencia
`out/orbit-04-4-2-seeds-20260827.md`. Pendiente Phase 4: 4.3 ensayo E2E +
veto real del dueño, 4.4 cierre.

Previo (2026-08-27, tarde): REACTIVACIÓN POR API EJECUTADA
(autorizada por el dueño, "hazlo tú"): 25 keywords pausadas (dedup de
CAMPANAS 01: 4 en Arras Manual 108, 18 broads en AD_READY 157-160, 3 phrases
en AU2 3920) y 5 campañas reactivadas (108 Arras Manual MX, 3934 Wedding
Coin ASIN US, 3911 A1U Category Phrase US, 3909 A1U Category Exact US, 3926
AU2 Category Exact US), cada una con readback verificado y cache sincronizado
(ingest_run 19, ok). AGM2M (165) quedó FUERA (veredicto reactivar-con-ajuste:
decisión aparte). Herramienta `tools/reactiva_campanas.py` (operación de
NEGOCIO por API, no del motor: no pasa por apply_queue; dry-run por defecto,
`--acepto-mutacion-real` obligatorio, fail-closed contra la base viva).
Evidencia `out/reactiva-campanas-20260827.log`. **Sellos NUEVOS de la API
v3 que refutaron hipótesis del repo:** el state del PUT de pause/resume es
UPPER (`PAUSED`/`ENABLED` — `'paused'` minúscula: 400 con el enum exacto;
`'userPaused'` de write.py REFUTADO y `ESTADO_PUT_*` corregidas con sus
tests); el id del body viaja como STRING (con número: 400 `NUMBER_VALUE...`);
los headers exigen el vendor v3 EXACTO en Content-Type **y** Accept (sin
Accept: 415; campañas `application/vnd.spcampaign.v3+json` — shape de resume
de campaña NUEVO, sellado con 3909 primero). Regla 8 atrapó un error del doc
de dedup: 'arras matrimoniales de oro' BROAD no existe en la 160 (decía
×4, eran 3) — la herramienta abortó fail-closed y la lista quedó en 25.
Pendiente Phase 4: 4.2 seeds (las Exact US YA están ENABLED — destrabado),
4.3 ensayo E2E, 4.4 cierre. CAMPANAS 01 1.1 完了.

Previo (2026-08-27): ORBIT 04 task 4.1 CERRADA (deploy endurecido, EN VIVO): env por servicio (db ya no hereda el .env completo —
llevaba hasta ORBIT_DSN_ADMIN; solo POSTGRES_* por interpolación), app
non-root como uid 10001 (secrets/ chown 10001:10001 con 600/700 intactos;
se retira el residual `user: "0:0"` de ORBIT 03), y wiring admin→ledger
resuelto (`GRANT app_decide TO orbit_admin` — las reversas ya no revientan
con InsufficientPrivilege). Backup previo `backups/pre-4.1-20260827.dump`.
Verificado en vivo: health ok, 8010 solo loopback+wg0, 5432 loopback, db
env con 0 ORBIT_DSN, datos intactos (apply_attempt=21), veto sin token 401 /
con token 422 (auth lee el secret como uid 10001), bridge y crons intactos.
Decisión documentada: la membresía cluster de orbit_test SE QUEDA mientras
la suite corra por túnel (revocación atada a sacar la base de test de prod).
0002 ya estaba aplicada (SELECT: apply_queue, apply_attempt, reactivacion_manual).
Candados re-sellados con rojo demostrado (sello bloque db, db sin DSNs, app
non-root, runbook con el uid). Rama orbit-04/4-1-deploy-endurecido, PR a
master. Pendiente Phase 4: 4.2 seeds (el dueño reactiva las Exact US —
lista de dedup en out/campanas-01-dedup-20260827.md), 4.3 ensayo E2E, 4.4 cierre.

Previo (2026-08-27): ORBIT 04 Phase 3 COMPLETA (3.1-3.3) y
task 2.5 MERGEADAS a master (squash: #28 3.1, #33 3.2, #34 3.3 y 2.5
probe-shapes; #29-32 quedaron cerrados por la cascada de bases). 3.3
(`app/notifica.py`, canal Telegram fail-silent): aviso por cada
corte NUEVO encolado (con vence_el 48h de la MISMA fuente de la fila),
digest único por ciclo (live Y shadow: el encabezado declara el modo para
que un digest de shadow no se confunda con uno live — cierre del hallazgo
medio de la review del lead) y alerta de harvest failed (en `_falla_job`,
junto a la reversa). Un fallo del canal JAMÁS tumba el ciclo ni degrada el
status: warning scrubbeado + NOTA `notes['telegram']` (solo claves de lo
que falló, regla 3) integrada antes del sello post-apply — estructuralmente
TX4 corre después del cierre del envelope, ahí estaba la única escritura de
notes restante — y VISIBLE en Salud (endpoint + pantalla). Canal
deshabilitado (sin secrets/telegram.json) = no es fallo: True, sin NOTA.
Avisos también en shadow (el mensaje declara modo — el dueño practica el
veto con candidatos reales, sellado 6). Ciclos skipped/failed sin digest
(estructura del ciclo; visibles por su propio status). tests/conftest.py
aísla el canal por defecto: cero HTTP real en tests es invariante
determinístico. Review: GO tras fixes (test directo del mapeo
harvest→NOTA, loop de alertas envuelto, acento). Rojo honesto del DoD
capturado (KeyError 'telegram' contra la base 3.2). 17 tests nuevos;
161+ focused en verde con PG16 real por túnel; batería completa en el CI
del PR.

Previo (2026-08-27): ORBIT 04 Phase 3 EN CURSO: 3.1 y 3.2
listas (PRs DRAFT #28 y apilado — el lead revisa). 3.2 (goals amigables,
rama orbit-04/3-2-goals-set): UNA implementación `app/goals_write.edita_goal`
(UPDATE solo-campos-pasados con `updated_at` EXPLICITO obligatorio — mutante
demostrado cazado; pre-validación de entrada pura SIN I/O que combina
nuevo+existente: floor<=ceiling, finitos, ids no vacíos, terna harvest
all-or-nothing con `harvest_limpia`, edición vacía rechazada) despachada por
DOS superficies: POST /api/ads-optimizer/goals/{goal_id} (misma auth de 3.1)
y CLI `python -m app.cli goals set` (ORBIT_DSN_ADMIN fail-closed exit 2,
allow_abbrev=False — el hueco de `--targe` abreviado cazado por test).
Candado de camino único en test_architecture (regex IGNORECASE+\s+; tools/
fuera, declarado) y superficie OpenAPI sellada +ruta. Test de punta a punta:
editar target 25→20 y correr UN ciclo REAL — la decisión congela
inputs.target_acos_pct_usado == "20.00" (rastro completo). Declaración
sellada: NINGÚN campo de goals set vive en config_version — la regla
config=fila-nueva queda para la pantalla de settings de ORBIT 16 Phase 3.
Review: GO tras fixes (ids vacíos, edición vacía que re-sellaba updated_at,
Decimal Infinity/NaN, docstrings stale); 100 tests focused en verde con
PG16 real por túnel; batería completa en el CI del PR.

Previo (2026-08-27): ORBIT 04 task 2.5 CERRADA: el probe
real se ejecutó (2026-08-26, autorización del dueño, campaña sacrificable
amazon_us) con 4/4 formas en neto cero (evidencia out/smoke-apply-20260826.log,
ledger probe ids 1-20) y los shapes quedaron fijados contra la API VIVA —
las hipótesis adivinadas estaban mal y se corrigieron (regla 8 cumplida a
contrapelo): el bid viaja como NÚMERO JSON (no string), los enums son UPPER
(matchType EXACT/NEGATIVE_EXACT, state ENABLED/PAUSED), los deletes v3 van
por POST /sp/{recurso}/delete con filtro de ids (DELETE con body da 403),
el readback es por LIST (el GET directo da 403) y los acks son 207 con
success/error. Tests de readback de 2.1-2.3 re-sellados contra esos shapes;
cross-review del dueño (codex+qwen) cerrada (readback paginado, 207
verificados campo por campo, constantes del pause a una fuente). Residuo
verificado y limpio: los 4 términos basura zzsmokeprobe* quedaron ARCHIVED
(en Amazon delete=archivar; ledger probe ids 1-20 — el id 21 es de la
limpieza del residuo del 2.5 del 2026-08-27, script
`out/limpia_residuo_probe_2_5.py`, verificado por payload).
Rama orbit-04/2-5-probe-shapes, PR DRAFT #31 apilado sobre #29 (3.2).

Previo (2026-08-26): ORBIT 04 Phase 3 EN CURSO: 3.1 lista
(rama orbit-04/3-1-auth-escritura, PR DRAFT — el lead revisa). Auth de
escritura: token estático SOLO-header (x-orbit-token, compare_digest,
register_secret, fail-closed 503 sin secret/DSN, query string no
autentica), ConexionEscritura (ORBIT_DSN_ADMIN), POST /veto (actor,
vence_el editable default 30d, 409 en applying/terminal, 404 inexistente)
y POST /reversa/{bid,pause,negative} vía apply.reversa_manual
(negative_id SIEMPRE del ledger; una reversa por decisión — 409 "ya
revertida"), pantalla /cortes (pendientes + vencimiento + botón vetar, JS
estático CSP-self, XSS testeado), candados OpenAPI = lista sellada 3 GET +
4 POST con auth-dependency introspectada, docstring api.py corregido
(/run Reject permanente), rotación de token en DEPLOY.md (verifica con
queue_id inexistente — jamás muta). Ciclo adversario por tocar auth: 6
hallazgos; ADV-2/3/4 ARREGLADOS con tests en rojo primero; ADV-1
DECLARADO (orbit_test quedó miembro ADMIN OPTION de app_* en el cluster
para tests locales por túnel — REVOKE documentado en DEPLOY.md, 4.1
resuelve); declarado también: reversas necesitan además membresía
app_decide (GRANT apply_attempt es solo decide, NOTA en DEPLOY.md, wiring
en 4.1). 65 tests nuevos/focused en verde contra PG16 real por túnel;
batería completa en el CI del PR. Siguiente: 3.2 goals amigables (CLI +
endpoint, una implementación), 3.3 app/notifica.py.

Previo (2026-08-26): ORBIT 04 Phase 2: implementación
COMPLETA (5/5 tareas construidas y mergeables, rama orbit-04/phase-2, PR
DRAFT sin mergear — el dueño revisa) pero ACEPTACIÓN PENDIENTE: la corrida
real del probe 2.5 es acto del dueño y los tests de readback se sellan
contra los shapes reales en esa corrida: el apply integrado. (2.1) app/apply.py: re-resolución por decisión (escalera + goal,
JAMÁS inputs.modo), quota atómica con cap de config, ledger PRE-HTTP con
tope-3, readback con GET sellado, cache con LO LEÍDO, reversa de bid.
(2.2) app/apply_cola.py: encola cortes (invariante corte↔cola), skip
veto_pendiente por clave de efecto, liberación FIFO con re-validación de
evidencia FRESCA al reloj de liberación (contrato cross-plan CORTES 01),
descarte pre-cobro, filas released reintentadas al día siguiente.
(2.3) app/apply_harvest.py: harvest_job nace AL LIBERAR, reconciliación
viva por identidad completa (señuelo en otro ad group no engaña), bid
sugerido clampeado con intención pre-POST (endpoint NO pineado: v2
retirado, v3 exige SigV4 — fail-open al default sellado; regla 8
documentada), reversas keyword-primero. (2.4) fase de apply DENTRO del
lock en corre_ciclo: heartbeat + ownership-check pre-HTTP con aborto
fail-closed, guard status=running en el cierre, HAY_MODULO_APPLY=True
(escalera shadow sigue = cero HTTP, testeado tras el flip). (2.5)
tools/smoke_apply.py CONSTRUIDO y NO ejecutado (doble autorización +
campaña solo por config; corrida real = dueño). Review de fase con
adversario: 11 hallazgos (1 crítico: TX4 sin commit invisible por
autocommit en tests — corregido con test SIN autocommit), 8 fixes
aplicados y aprobados por el reviewer. Cross-review ordenada por el dueño
(codex+grok+qwen en paralelo): 6 altas reales (tope-3 contando reversas,
UniqueViolation al reusar job, reversa rompiendo keyword-primero, fases
de harvest avanzando sin ids del ack, GET sin capturar abortando el
barrido, cierre job+cola sin transacción) + medias — 13 fixes aplicados.
Suite 550 passed con batería DB real (túnel). Previo (2026-08-25): ORBIT 04 Phase 1 CERRADA (3/3 tareas,
rama orbit-04/phase-1, PR de fase): (1.1) docs/APPLY.md, el contrato fino de
PR2 — máquina de estados de la cola de cortes con clave de efecto y ventana
de veto 48h, ledger de intentos, quota sellada con mapeo config↔motor,
matriz de reconciliación, tabla de reversas y checklist de cutover; spec
deltas en CONTEXTO.md y DATABASE.md. (1.2) migración 0002_apply.sql: tabla
apply_queue (nace pending_veto, transiciones exactas por trigger, veto exige
admin por schema, fila shadow JAMÁS sale de vetoed|discarded, único parcial
NULLS NOT DISTINCT por clave de efecto), ledger apply_attempt (sello una
sola vez), reactivacion_manual, sellos de apply_quota_state (fila del día
solo desde config, used creciente, día UTC de la base), fases de
harvest_job, applied_cycle_id y GRANTs por columna; test_schema parsea 0002
y test_apply_schema ejercita el DoD en DB (CI). (1.3) app/ads/write.py:
cliente de escritura allowlist default-deny (10 mutaciones exactas, scope
sellado por instancia, moneda verificada pre-HTTP, 429 reintenta sin
recobro / 5xx no reintenta, constructor fail-closed exige modo live, candado
de imports) + negativeKeywords/list verificado EN VIVO (regla 8, 200 en US y
MX). Suite local 359 passed / 78 DB-skips; batería DB completa en el CI del
PR. Siguiente: Phase 2 (el apply integrado: 2.1 núcleo, 2.2 cola, 2.3
harvest, 2.4 integración al ciclo, 2.5 probe autorizado). Previo (2026-08-24): Phases 1–3 en master; 4.1 y 4.2 hechas
en goncloud (servicio `app` en 127.0.0.1:8010 + 3 crons aditivos, profundidad
diaria D-31..D-1). 4.3 EJECUTADA por el dueño: escalera global en shadow,
targets ACoS 20 (mx) y 20 (us — presión máxima elegida a sabiendas), TODAS
las campañas activas vía goals de plataforma; harvest sin bid fijo por
decisión (el bid sugerido de Amazon llega con el apply de PR2 — regla 3:
jamás inventar el número). 4.4 VALIDADA por el dueño: primer shadow real corrido (133 decisiones,
us 124 / mx 9), spot-check completo y verificación adversarial TRIPLE
(codex, grok y qwen: 133/133 limpias, skips cuadrando al entero).
ORBIT 03 COMPLETO (17/17 tareas): el optimizador decide EN SOMBRA todos
los dias (crons 06:45/07:10/08:40-08:41 UTC) sobre todas las campanas
activas, con cero capacidad de escribir a Amazon. El reloj de las 2
semanas de shadow para el cutover (ORBIT 05) corre desde el 2026-08-24.
ORBIT 16 Phase 1 CERRADA (7/7 tareas): dashboard de LECTURA en master
y desplegado — 4 pantallas (Resumen con gráficas, Campañas con estado
y procedencia del target, Decisiones por cursor, Salud), acceso por
túnel ssh al 8010. El dueño validó el smoke 1.7 (gráficas OK bajo CSP
estricta; pidió y recibió la columna de estado de campaña; se le
explicó con datos vivos por qué el motor negativiza sus términos
"arras": 116 clicks / 0 ventas en ventana madura — la decisión
estratégica listing-vs-negative queda anotada para el apply). La
review en cadena (lead + reviewer fresco + kimi/codex/grok + bots)
atrapó y cerró bugs reales del bloque 2. Phase 2 CERRADA: el dashboard
también se ve por la VPN WireGuard del dueño (cel y compu, validado
por él) — bind adicional en la IP wg0, allowlist EXACTA de mapeos
sellada por candado, sign-off del dueño antes de aplicar. Queda solo
Phase 3 (settings de escritura, bloqueada por ORBIT 04). CORTES 01
CERRADO (5/5): los cortes NEGATIVE y PAUSE ahora usan umbral de clicks
ADAPTATIVO por producto (evidencia del ad group 90d: expected_clicks ×
1.5, piso max con legacy) y NEGATIVE además piso de COSTO adaptativo
(AOV del producto × 1.0, respaldos 45 USD/600 MXN) — nacido del caso
arras y calibrado con 57 anotaciones del dueño (regla nueva 21/22 con
su instinto vs 0/22 de la vieja); contrafactual final: 28 cortes
legacy → 1 con el paquete. Los términos PRODUCTO-DIFERENTE se cortan
por la otra vía: lista curada por AI con aprobación del dueño
(CORTES 02, sembrada). Shadow valida la regla nueva ~2 semanas antes
del cutover. Siguiente proyecto grande: ORBIT
04 (PR2: el apply con topes). Este archivo tiene candado de frescura:
el CI exige actualizarlo en cada PR que cierre tareas.

## Qué es Orbit

Sistema nuevo desde cero que optimiza **Amazon Ads (Sponsored Products)** para
un negocio que vende en **Amazon MX y US** (y opera también Mercado Libre)
bajo el régimen mexicano de plataformas tecnológicas. Decide, con reglas
explícitas y auditables: **cuánto pujar, qué pausar, qué términos negativizar
y qué harvestear**. Reemplaza a un sistema viejo (`goncloud-MCP-2`, apagado el
2026-08-22) que murió por monolito: 62 módulos, 206 flags y 147 jobs para 3
decisiones. Orbit arranca en modo **shadow**: decide y registra todo, pero NO
escribe nada a Amazon hasta pasar validación humana (el "apply" llega en PR2).

## Estado actual (se actualiza por phase)

- **Hecho y en master**: toda la ingesta (cliente HTTP read-only con redacción
  de secretos, sync de estructura, pipeline de métricas y search terms,
  backfill histórico completo: 95 días de métricas, ~65 de terms, us y mx),
  y todo lo siguiente (mergeado 2026-08-24): la capa de ventanas de datos, el
  motor de decisión puro (bids, hygiene, goals) con todos los umbrales
  sellados y testeados, 3.1 el orquestador del ciclo (claim atómico del
  lock con TTL y heartbeat, envelope que se sella en todos los caminos,
  skips estructurados en notes, decisiones con inputs congelados y golden
  replay que las reproduce exactas), 3.2 la API de solo lectura
  (`/api/ads-optimizer/{status,audit,goals}`, GET como `orbit_read`, con
  watermarks de la misma fuente del motor y notes de formato mixto
  tolerado) y 3.3 el CLI `python -m app.cli {ingest,cycle}` (envoltorio
  delgado: el ciclo usa el mismo claim/job_key del cron y `ingest` delega
  a los pipelines de `app/ads/`); candados anti-monolito activos
  (complejidad, fronteras de imports, tamaño de módulos, frescura de este
  archivo).
- **Phase 4 (PR #15, en cierre)**: 4.1 servicio `app` vivo en el server
  (`/health` OK, puerto solo loopback, `secrets/` 0600 intactos), 4.2
  tres crons diarios (accounting intacto), 4.3 seed del dueño ejecutado
  (shadow, targets 20/20) y 4.4 primer shadow real VALIDADO (133
  decisiones, triple verificación adversarial limpia). Falta solo el
  merge (4.5).
- **Datos reales ya en la base viva** (Postgres en el server `goncloud`):
  5,897 entidades, ~22,000 observaciones de métricas, ~6,900 de search terms.

## Cómo se construye (roles)

- **GLM** (otra sesión de IA) implementa las tareas del plan.
- **Claude Code (Fable, sesión lead)** revisa cada tarea con un reviewer de
  contexto fresco, verifica contra la base viva, y mantiene tracker y docs.
- **Cross-reviews** con otras IAs (Codex, Grok, Kimi, CodeRabbit, Greptile)
  por tarea; tope duro de 2 rondas.
- **Gon (el dueño)** decide QUÉ se construye, aprueba planes, y tiene dos
  checkpoints humanos en Phase 4: elegir campañas piloto (4.3) y el
  spot-check manual de ≥20 decisiones del primer shadow (4.4).
- Un PR por phase; nada llega a master sin CI verde y reviews atendidas.
- Registro de trabajo: fila `ORBIT 04` en AppFlowy (EHV Tasks).

## Arquitectura (mapa de carpetas)

```
app/
├── redaction.py   ← ningún secreto sale en logs/errores
├── db.py          ← conexión a Postgres (DSN redactado)
├── ads/           ← ESTACIÓN 1: hablar con Amazon (única capa con internet)
│   ├── client.py     cliente READ-ONLY (sin capacidad física de mutar campañas)
│   ├── structure.py  catálogo: campañas, ad groups, keywords, targets
│   └── reports.py    métricas y search terms → base de datos
└── optimizer/     ← ESTACIÓN 2: pensar (PURO, sin internet ni base — por test)
    ├── windows.py    única puerta a la base: ventanas de datos colapsadas
    ├── bid.py        decisiones de puja y pause
    ├── hygiene.py    negative exact y harvest
    └── goals.py      metas por campaña/plataforma, modo efectivo, cooldown
app/cycle.py ← orquestador del ciclo: ventanas → motor → tabla decision (auditoría)
```

Flujo de una vía: Amazon → `ads/` → Postgres → `windows.py` → motor →
tabla `decision` (auditoría) → API/CLI de lectura.

## Reglas de diseño selladas (resumen de docs/CONTEXTO.md)

1. Una decisión, un camino, un dueño — no se construye lo que no decide.
2. Un número, una fuente.
3. Dato faltante = None y la fila no se escribe; JAMÁS una constante inventada.
4. Todo dinero lleva (valor, moneda); mezclar monedas es imposible por schema.
5. Métricas append-only bitemporales; el motor colapsa a la última observación.
6. Cortes (pause/negative/harvest) exigen datos con ≥10 días de maduración.
7. Nada irreversible sin su reversa implementada antes.
8. Verificar la forma real del dato en producción antes de testear invariantes.
9. Toda prueba de regresión se demuestra fallando contra el código anterior.
10. Conciliar contra la fuente externa, no contra consistencia interna.

## Umbrales del optimizador (sellados; fuente: diseño v2)

| Decisión | Regla |
|---|---|
| PAUSE | orders=0 ∧ clicks ≥ `max(100, ceil(1.5×clicks/órdenes del ad group))` ∧ cost≥ {us: 40 USD, mx: 500 MXN} (piso y fallback 100 = CORTES 03; umbral adaptativo = CORTES 01) |
| Bajar puja −25% | ACoS > 1.35×target (con orders≥1) |
| Bajar puja −12% | ACoS > 1.15×target |
| Subir puja +15% | ACoS < 0.85×target ∧ orders≥3 |
| NEGATIVE_EXACT | orders=0 ∧ clicks ≥ `max(20, ceil(1.5×clicks/órdenes del ad group))` (fallback 40 si el grupo no califica) ∧ cost ≥ `max({us: 8, mx: 130}, AOV×1.0)` (respaldo 45/600); ASIN-like nunca — CORTES 01 |
| HARVEST | orders≥2 ∧ ACoS ≤ min(35%, target); exige config completa en el goal |

ACoS = cost / ad_revenue COMPLETO (halo incluido). Clamp por decisión
[−30%, +20%]; resultado dentro del [floor, ceiling] DEL GOAL (goal 4 MX:
1.00/45.00 MXN por decisión del dueño; goals US: 0.10/2.50 USD — el default
del esquema, pensado en USD, deja de aplicarse a ciegas en el preflight 1.2).
Target ACoS en cascada: goal → config global → cache del estado → default 55.
Precedencia: PAUSE gana a todo; −25 gana a −12. Modo: off→shadow→live, y en
PR1 'live' degrada a shadow (fail-closed).

## Trampas del dominio (nunca olvidarlas)

- **Tres relojes**: la venta atribuida madura en 5–8 días, el costo hasta el
  día 15, los fees a 15–30. Un ACoS de día 1 en MX sale ~1.5× peor que el real.
- **Halo**: 56–58% del ingreso atribuido es de OTROS SKUs. Es atribución de
  Amazon, no causalidad. Por eso el ACoS usa el revenue completo.
- **Monedas**: el sistema viejo mezcló MXN/USD (error de 18.66× a favor de
  "todo es rentabilísimo"). En Orbit es imposible por schema.
- **El día en curso** llega incompleto y etiquetado con su fecha: el cron
  re-tira D-31..D-1 (sello 4.2: tope de un request = 31d, cubre las
  columnas 30d y el mínimo D-8..D-1 de terms) y la bitemporalidad lo hace
  seguro.
- **Amazon ignora en silencio los filtros que no reconoce** (sonda en vivo
  2026-08-30): pedirle una lista con un filtro mal nombrado no da error —
  devuelve TODO. En una lectura eso es ruido; en un borrado significa que un
  nombre equivocado viaja SIN FILTRO, o sea "todos". Por eso el nombre del
  filtro de cada mutación está clavado con test propio.
- **La pregunta sin respuesta**: si la cuenta US gana o pierde depende del
  supuesto de halo (entre +1,671 y −2,238 USD en 91 días). Decisión pendiente
  antes de la fase margin-aware: acotar, holdout, o TACoS.

## Glosario rápido

- **Shadow**: el motor decide y registra, pero no toca Amazon.
- **Harvest**: promover un término de búsqueda ganador a keyword exact propia.
- **ASIN-like**: término que es un código de producto (B0 + 8 caracteres);
  jamás se negativiza.
- **Bitemporal**: cada métrica guarda el día del hecho Y el momento en que se
  observó; permite reconstruir "qué sabía el motor cuándo".
- **Watermark**: última fecha con datos por plataforma; si está vieja (>7d),
  el ciclo se salta esa plataforma.
- **Golden replay**: test que re-alimenta los inputs congelados de una
  decisión al motor y debe reproducirla idéntica — la garantía de que toda
  decisión es auditable.
- **Candados anti-monolito**: tests y linters que hacen imposible que el motor
  haga IO, que un módulo engorde sin decisión visible, o que la complejidad
  derive en silencio.

## Dónde vive todo

- **Código**: GitHub `gon0801/goncloud-Orbit` (master = lo aprobado; un PR
  abierto por phase en curso).
- **Base de datos viva**: Postgres en el server `goncloud` (Docker, solo
  localhost; acceso por túnel SSH).
- **Tracker**: AppFlowy (notion.goncloud.cc), grid EHV Tasks, fila
  `ORBIT 03 — PR1 optimizador: SHADOW completo (cero escrituras a Amazon)`.
- **Fuentes de verdad**: `docs/CONTEXTO.md` (reglas), `docs/traspaso/
  ADS_OPTIMIZER_V2_DESIGN.md` (umbrales), `docs/DATABASE.md` (schema),
  `plans/orbit-03.md` (plan y estado por tarea).

## Instrucciones para el asistente de chat

- Responde SIEMPRE en español (mexicano).
- Los umbrales y reglas de arriba están SELLADOS: cítalos tal cual, jamás
  inventes números ni "mejoras" de reglas — cambios de reglas son decisión
  del dueño y se hacen vía plan en el repo, no en el chat.
- Si el estado parece desfasado respecto a lo que el dueño cuenta, di que tu
  copia puede estar vieja y sugiérele hacer "Sync now" en el Project.
- Este Project es para PENSAR con el dueño (estrategia, dudas del negocio,
  entender decisiones del motor); la implementación vive en Claude Code.
