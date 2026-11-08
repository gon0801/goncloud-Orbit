# ORBIT 07. Continuar las mejoras del motor después de AUTO-03

Continuación de `decisiones-producto-01` (AUTO-03), acordada con el dueño el 6 de noviembre de 2026.
El dueño autorizó iniciar ORBIT 07 en la PC con DeepSeek Flash por su API propia y Opus como
revisor, aplicando poteto-mode explícitamente. La construcción parte de AUTO-03 integrado y
desplegado en `f86ed7dcd77f0af7b540217c3df9422ebe5c7154`. El encargo y su conexión se registran
en la evidencia de arranque; este archivo por sí solo no los ejecuta. La base original de la investigación fue
`5adf1be0bb8bdd84d46c7b7e8588b66daeea6a99`, rama `resumen-ads-04-diseno`; no es la base de ejecución.
Se conservan los identificadores M0-M6 para enlazar la [revisión cruzada](../docs/evidencia/orbit-07-plan/cross-review/README.md).
M1, las diferencias de M2 y M5 más M6 son las entregas previstas. M3 más M4 se construye
solo por el comportamiento que AUTO-03 no cubra. M3 se separa si demuestra efecto propio.

## Recibir AUTO-03 antes de modificar el motor

El [registro consultado](../docs/evidencia/orbit-07-plan/auto03-estado.json) identifica el encargo
`enc_07844b614147c266` en la PC. Esa captura histórica lo mostraba ejecutándose en E. La
consulta de arranque del 7 de noviembre ya lo encontró terminado, con la entrega I desplegada,
PC y Mac sin encargos activos y servidor en el mismo SHA verificado. Se conserva la captura
anterior como historia; manda la evidencia de [arranque](../docs/evidencia/orbit-07/preparacion-2026-11-07.json).
El [registro de continuación](../docs/evidencia/orbit-07-plan/continuacion-auto03.md) conserva
la fuente del plan, su hash y las diferencias que justifican este trabajo.

| Resultado | Dueño y base que se reutiliza | Trabajo restante de ORBIT 07 |
|---|---|---|
| Cálculo de meta, identidad y evaluación persistida | AUTO-03 C-F, `productos.py`, `productos_io.py` y `evaluacion_producto_ciclo` | M2 amplía la economía compatible. No crea otro resolutor, tabla ni peldaño. |
| Economía al decidir y liberar una pausa | AUTO-03 S3 y F.3/S12 conservan equilibrio de plataforma y meta de producto congelada | M2 comparte resolución económica fresca entre ciclo y cola, conservando la historia. |
| Aplicación de la meta | AUTO-03 C/F aplica la primera meta sin ancla de producto y limita las bajadas posteriores a 0.5 puntos | M2 aplica también los cambios posteriores de margen directamente y resuelve el respaldo completo. No atribuirse la primera entrada ya cubierta. |
| Agregado y pausa por producto | AUTO-03 D/H agrega y propone con límites por mercado | M3 añade solo el episodio o freno que cambie una decisión respecto de AUTO-03. Reutiliza agregado y límites compatibles. |
| Retiro, cola y reversa por anuncio | AUTO-03 G-I, `pause` sobre `product_ad`, `apply_producto` y `retiro_anuncios` | M4 modifica solo la condición de aprendizaje pendiente. No crea otra acción ni otro escritor. |
| Pantalla y encendido de la pausa | AUTO-03 E/I, con límites guardados y encendido por el dueño | Conservar su flujo y su autorización. No encender la pausa al desplegar ORBIT 07. |
| Protección de una venta reciente | Diferencia propuesta en M1 | Añadir protección con vencimiento fijo sin congelar pujas después del veto. |
| Evaluación económica de cambios de puja | Diferencia propuesta en M5 más M6 | Usar episodios maduros aplicables al último cambio, sin una espera global nueva. |

- [ ] Antes de iniciar construcción, consultar de nuevo `motor director ver enc_07844b614147c266 --base C:/Users/ehven/.motor-encargos` en la PC. Registrar el resultado y leer `plans/decisiones-producto-01.md` desde `origin/master` actualizado. No deducir ejecución desde `manifest.active` ni desde esta captura histórica.
- [ ] Exigir que AUTO-03 haya cerrado sus entregas B-I, con sus commits integrados en `master`, CI verde y recibos de los despliegues que le corresponden. El encargo debe haber terminado y liberado los archivos comunes. Una pausa o fallo del ejecutor no transfiere su trabajo a ORBIT 07. Si falta esta condición o no se puede consultar, avanzar solo preparación de lectura y registrar el bloqueo de construcción.
- [ ] Al encargar ORBIT 07, registrar AUTO-03 como dependencia de ejecución antes de arrancar el constructor. Este documento no crea esa dependencia en el motor por sí solo. Usar un checkout y una rama propios desde el `master` que contiene la entrega final de AUTO-03; no apilar cambios sobre su rama activa ni modificar su plan mientras corre.
- [ ] Completar `docs/evidencia/orbit-07/arranque.md` con una fila por M1-M6 que incluya comportamiento requerido, implementación heredada, SHA, evidencia reutilizable y diferencia pendiente. Si el resultado ya está cubierto, registrar esa evidencia y omitir su construcción y su PR. Las listas posteriores describen resultados finales; no ordenan reconstruir lo heredado.
- [ ] Conservar la decisión de AUTO-03 I. La pausa puede quedar en práctica hasta que el dueño guarde sus límites y la encienda. Ese estado, o la falta de cobertura madura para una pausa concreta, no bloquea el comienzo de ORBIT 07 ni sus otras mejoras una vez entregado AUTO-03. No exigir treinta días de operación o un piloto para iniciar esta continuación.

La preparación paralela se limita a lecturas, casos y documentación propios. AUTO-03 conserva
la construcción de su alcance. Después del traspaso, ORBIT 07 modifica el motor compartido
en entregas secuenciales. Las ramas aisladas evitan pisar archivos, pero no sustituyen esa dependencia.

## Cómo ejecutar este plan

Cada casilla representa trabajo con evidencia nombrada. Márcala cuando exista el resultado,
el registro de ejecución o el commit que lo demuestra. Conserva los resultados inconclusos.
No conviertas una ausencia de datos en una verificación de la política. Una casilla heredada
puede cerrarse con evidencia válida de AUTO-03; una diferencia nueva necesita su propia evidencia.

La instrucción del dueño es implementar para optimizar, sin períodos largos de prueba ni una
operación excesivamente conservadora. Cada entrega termina con la mejora activa dentro del
alcance autorizado, conservando el encendido de pausa reservado al dueño por AUTO-03 I,
después de comprobar su funcionamiento con datos ya disponibles, pruebas
focalizadas, recorrido operativo y CI. No exige semanas de simulación, cohortes experimentales,
un tamaño de muestra futuro ni demostrar antes una mejora estadística del negocio.

Medir resultados sirve para corregir la operación después de activar. No es una fase previa que
mantenga apagada una mejora lista. La madurez de una fuente y la falta de un insumo concreto
siguen condicionando esa decisión individual, sin detener el resto del motor ni añadir esperas
de calendario a la entrega. El arranque posterior está autorizado con los modelos y la vía
indicados en el encabezado y en el pedido del ejecutor.

La ejecución sigue [Autopilot-stack](/Users/dn/.agents/skills/poteto-mode/playbooks/autopilot-stack.md),
adaptado a `AGENTS.md`. Entrega PRs verificadas para que el dueño las integre. El arranque
autoriza implementación y PRs; no autoriza merge, despliegue ni cambios en Amazon. Se conserva
el `active` recibido del manifest de master (`resumen-ads-multicanal`) y todas sus entradas.
El encargo selecciona explícitamente este plan; `active` no representa el estado del ejecutor
ni habilita a saltar la dependencia de AUTO-03.

Aplica comprobaciones proporcionales al comportamiento y al riesgo. Reutiliza la evidencia válida.
Estas políticas mueven dinero y pueden escribir en Amazon. Comprueba sus cuentas, alcance,
historia y reversa. Para cada PR, combina pruebas focalizadas, recorrido operativo y medición
cuando cambie el trabajo del ciclo. No impongas diez revisores, diez escenarios o capturas de una
interfaz que este plan no modifica. CI verde y revisión de secretos siguen siendo obligatorias.

Usa `master` como destino. Resuelve las dudas observables con consultas y sondas. Las elecciones
de negocio que la evidencia no resuelve deben quedar concretas en la ficha de activación.
No elijas en secreto cuánto dinero adicional está dispuesto a arriesgar el dueño.

## Preparar y coordinar el trabajo

### Fijar el alcance antes de construir

- [ ] Leer `AGENTS.md`, `docs/CONTEXTO.md`, el diseño del optimizador y este plan desde la base actual. Registrar SHA y diferencias relevantes en `docs/evidencia/orbit-07/arranque.md`.
- [ ] Conservar las mejoras pendientes de M1, M2 y M5 más M6. Construir M3 más M4 solo por una diferencia funcional acreditada contra AUTO-03. Excluir rediseño del dashboard, inventario, precios, presupuestos de campaña, placements, Mercado Libre Ads y nuevos servicios. Registrar cualquier ampliación como un cambio de alcance.
- [ ] Recibir el plan ejecutado AUTO-03 y sus entregas conforme a la sección anterior. Reutilizar sus módulos, migraciones, límites, pantalla y evidencia. Registrar como diferencias las reglas de M2 frente a S2, S3 y S12, sin modificar retrospectivamente el contrato ni los resultados históricos de AUTO-03. Stock y presupuestos siguen fuera.
- [ ] Conservar inicialmente los pasos de puja, los siete días entre ajustes ordinarios, la madurez mínima de cortes y el veto de 48 horas. Documentar en la ficha cualquier excepción que una fase proponga.
- [ ] Mantener una sola política ejecutora por entidad. Usar comparaciones históricas durante la construcción sin crear decisiones ejecutables, cola ni cuota para la variante comparada. No construir una operación paralela de simulación como requisito de entrega.
- [ ] Tratar el gasto agregado como un umbral para intervenir. Registrar que los reportes tardíos y el veto permiten gasto posterior al cruce. No prometer un tope instantáneo ni confundir cuota de operaciones con presupuesto publicitario.

### Asignar responsables y dependencias

- [ ] Asignar un responsable por PR y un revisor independiente del cambio monetario. Entregar a ambos el resultado esperado, archivos propios y criterios de aceptación.
- [ ] Resolver las fuentes de M1, M2 y M5 en paralelo con lecturas de M0. Después del traspaso de AUTO-03, secuenciar las ediciones de `cycle.py`, `caso.py`, `apply_cola.py`, `replay.py`, módulos de producto y migraciones. Si M4 espera un insumo, avanzar M5 y preparar su entrega conjunta con M6. Compartir archivos no impide preparar casos o lecturas independientes.
- [ ] Crear un checkout aislado por implementación. Reutilizar el existente si ya está aislado. No modificar los borradores ni evidencias ajenas presentes al arrancar.
- [ ] Ejecutar `graphify query` antes de explorar código en cada agente. Actualizar el grafo con `graphify update .` después de modificar código.
- [ ] Mantener una tabla de estado con SHA, evidencia, decisión y siguiente dependencia en `docs/evidencia/orbit-07/estado.md`. No activar un bucle horario para redactar o revisar este plan.

Punto de control de capacidad. M1, M2 y M5 más M6 son tres entregas previstas, sujetas al
contraste con la base recibida. M3 más M4 solo añade una entrega si queda una diferencia útil;
M3 se separa únicamente con efecto propio. M0 es preparación integrada. Mantener un solo
propietario de cambios monetarios y una integración y despliegue a la vez sobre archivos comunes.
Paralelizar lecturas y revisiones, sin abrir períodos de observación futuros para poder avanzar.

### Preparar cada PR

- [ ] Basar la primera entrega en `origin/master` actualizado que contiene AUTO-03 terminado, registrar el SHA recibido e incluir su preparación M0. Apilar cada PR sobre su dependencia real mientras el dueño no la haya integrado. No basar M5 más M6 en un M4 bloqueado si no necesita sus cambios. Registrar base y cabeza en la evidencia.
- [ ] Extender los puntos recibidos de AUTO-03 antes de añadir módulos. Comprobar en esa base las firmas y nombres de este plan. Un nombre propuesto no prueba que falte su función; no crear un equivalente de algo ya entregado.
- [ ] Reproducir primero cada regresión con una prueba que falle en el código anterior. No exigir que una función nueva inexistente tenga un resultado ficticio en la base.
- [ ] Actualizar `docs/CONTEXTO.md` y el diseño pertinente cuando cambie una regla. Especificar su versión y distinguir lo preparado de lo activado.
- [ ] Ejecutar Ruff sobre los archivos Python cambiados y `pre-commit run --all-files`. Incluir los archivos nuevos en la revisión de secretos. Guardar los resultados.
- [ ] Abrir la PR contra la base correcta. Ejecutar CI con PostgreSQL y resolver los fallos del commit final. No usar `--no-verify` ni quitar candados.

### Revisar y entregar cada PR

- [ ] Revisar dinero, fuentes, permisos, alcance de escritura y fidelidad histórica. Cada hallazgo incluye su impacto y el comando o escenario que lo reproduce.
- [ ] Usar una revisión proporcional después de cada corrección. Repetir una revisión completa solo cuando el cambio o la evidencia invalidada lo justifiquen.
- [ ] Entregar la PR con CI verde en su SHA final. Distinguir código verificado de política activada. El dueño integra las PRs.
- [ ] Preparar activación y reversa dentro de la misma entrega conforme a `docs/DEPLOY.md`. Una vez autorizada e integrada, activar directamente en las entidades elegibles, comprobar el primer ciclo y mantener seguimiento operativo. No añadir un piloto ni otra espera de maduración global al despliegue.

### Ejecutar recorridos operativos aislados

- [ ] Crear PostgreSQL 16 desechable según la receta vigente de `verify/Launch.md` y las exclusiones de migraciones de CI. Comprobar esquema aplicado; no copiar un conteo histórico de tablas como expectativa vigente.
- [ ] Cargar `ORBIT_TEST_DSN` y secretos vacíos para el recorrido local. No imprimir DSNs o credenciales. Usar `TestClient` y el ciclo real según los tests existentes.
- [ ] Simular solo el transporte externo de Amazon. Ejecutar realmente lectura, decisión, persistencia, cola, cuota y reconciliación en la base desechable. Guardar JSON y logs, no capturas sin relación con el comportamiento.
- [ ] Para verificar fuentes vivas, usar exclusivamente lectura y un rol sin escritura. Una prueba local no sustituye la conciliación de una muestra contra Amazon.
- [ ] Limpiar únicamente la base, procesos y directorios creados por ese recorrido. Guardar la evidencia fuera de los directorios de limpieza.

## Resolver datos y contratos con la evidencia disponible (M0)

**Depende de.** Las lecturas y el inventario pueden avanzar mientras AUTO-03 corre. La comprobación final de contratos usa su entrega integrada. M0 se incorpora a cada entrega, sin una PR ni espera experimental obligatoria. Reutilizar primero las sondas, conciliación, cobertura, replay y mediciones de AUTO-03 A/E/F que sigan siendo válidas; repetir solo lo que la nueva base o regla cambie.

**Archivos.**

- [ ] Leer `app/lecturas_caso.py`, `app/ads/reports.py`, `app/evaluacion_catalogo.py`, `app/economia_observada.py`, `app/impulso_io.py`, `app/retiro_anuncios.py` y las migraciones de sus fuentes. Registrar el mapa en `docs/evidencia/orbit-07/m0/fuentes.md`.
- [ ] Crear las consultas y sondas necesarias bajo `docs/evidencia/orbit-07/m0/`. Conservar resultados agregados y redactados. No copiar secretos o volcados completos al repositorio.

**Construir.**

- [ ] Ejecutar SELECT de producción antes de sellar invariantes dependientes de los datos. Medir pausas candidatas con pedidos recientes, fechas y correcciones. Distinguir `metric_date` de fecha de compra.
- [ ] Fijar por separado el contrato temporal de hojas y de producto. Guardar ventana reciente, fecha ancla, madurez por fuente, reobservación necesaria y condición de liberación en `m0/contratos-temporales.md`. Para producto, una señal con más de diez días pero sin la madurez exigida por su fuente no permite liberar la pausa por vencimiento de la regla de hoja.
- [ ] Medir productos con identidad histórica, margen apto, atribución compatible, cobertura por fecha y anuncios vinculados. Separar grupos exclusivos, compartidos, desconocidos e impulsos abiertos. Guardar denominadores, exclusiones y fecha de consulta.
- [ ] Conciliar una muestra de gasto y ventas por producto contra reportes de Amazon. Confirmar que varias campañas y varias reobservaciones no duplican el total. Guardar discrepancias y su resolución.
- [ ] Consultar episodios históricos de puja con datos maduros antes y después, así como cambios intermedios. Resolver con ellos la comparación disponible. No esperar episodios nuevos para comenzar ni extrapolar la cobertura observada en octubre.
- [ ] Comparar con sondas desechables las alternativas de protección temporal, alcance de aprendizaje y medición económica. Guardar entradas, salidas y SHA. El prototipo de pausa ya existente acredita precedencia, no frecuencia ni pérdidas en producción.
- [ ] Escribir `docs/evidencia/orbit-07/m0/politica-implementacion.md` con fórmula y unidad del aprendizaje, inicio y fin de episodio, tratamiento de ventas, renovación, margen de gasto adicional y regla económica elegida. Resolver parámetros con datos y límites existentes. Presentar una recomendación concreta cuando haga falta elegir una exposición monetaria nueva.
- [ ] Comprobar qué decisión cambia M3 con esa regla. Si solo cubre cero pedidos en el mismo ámbito y ventana, verificar si ya están impedidas las subidas. Si no añade un freno efectivo, entregar sus datos con M4. No ampliar la regla ni esperar una muestra futura para justificar una entrega separada.
- [ ] Incluir un episodio con subidas sucesivas antes de D+17 y otro con ventana económica madura. Identificar cuál puede usar M6 para una nueva decisión. Registrar la cobertura del freno sin prometer que detiene subidas anteriores a la madurez ni alargar la espera ordinaria de todas las hojas.
- [ ] Inventariar qué insumos históricos se congelaron realmente. Reutilizar `replay.reproduce` para fidelidad. No usar `tools/rejuega_niveles.py` como comparación exacta sin corregir sus aproximaciones de goals, settings, cortes y ausencia de pausas en el informe.

**Resultado observable.**

- [ ] Publicar `m0/cobertura.json`, `m0/politica-implementacion.md` y ejemplos afectados. Si falta un insumo, señalar qué fuente debe completarse y continuar las entidades o mejoras que sí tienen datos. No esperar a cubrir todo el catálogo.

**Verificar con pruebas focalizadas.**

- [ ] Reutilizar los casos de `tests/test_ads_producto_ingesta.py`, `tests/test_evaluacion_catalogo.py`, `tests/test_product_ads_vinculo.py`, `tests/test_economia_observada.py` y `tests/test_lecturas_caso.py`. Añadir una prueba solo si la sonda incorpora lógica monetaria nueva o descubre una regresión relevante. Guardar el comando y resultado con los casos seleccionados.

**Verificar el recorrido operativo.**

- [ ] Ejecutar las consultas con el rol de lectura. Comparar la muestra externa con el resultado de la misma consulta sobre una copia aislada. Aprobar cuando identidades, fechas, monedas y sumas coincidan, o las exclusiones expliquen cada diferencia.

**Verificar rendimiento.**

- [ ] Medir de forma focalizada el trabajo añadido al ciclo sobre una copia fija. Registrar filas, hojas, productos, consultas y tiempos. Reutilizar mediciones válidas y ampliar solo si la variación o un fallo impiden comprobar el impacto. No afirmar percentiles que la muestra no permita.
- [ ] Fijar antes de comparar candidatos un presupuesto numérico para latencia y consultas, basado en esa medición y en el horario operativo. Guardarlo en `m0/presupuesto-rendimiento.json`. No reutilizar el valor histórico de BIDS 02 como base actual.

**Revisión.** Revisar contratos de datos y elecciones monetarias. No requiere revisión visual de interfaz.

**Entrega.**

- [ ] Adjuntar fuentes, cobertura y regla concreta a la entrega correspondiente. M1 avanza cuando sus insumos están disponibles aunque la economía de otros productos siga incompleta. M0 no mantiene bloqueadas todas las mejoras por una fuente ajena.

## Proteger una venta reciente sin renovar indefinidamente la espera (M1)

**Depende de.** Traspaso de AUTO-03 y M0 para datos temporales, contrato de episodio y muestra histórica. La preparación de lectura no necesita esperar el traspaso.

**Archivos.**

- [ ] Modificar `app/optimizer/bid.py`, `app/optimizer/caso.py`, `app/optimizer/politica.py`, `app/lecturas_caso.py`, `app/cycle.py`, `app/apply.py`, `app/apply_cola.py`, `app/optimizer/replay.py` y `app/optimizer/eras.py` según sus responsabilidades actuales.
- [ ] Crear las migraciones numeradas necesarias para persistir el episodio y representar su espera. Incluir el motivo permitido de `decision_sin_aplicar` y los contratos de cola afectados. Extender los tests existentes de política, caso, lecturas, ciclo, cola y esquema.

**Construir.**

- [ ] Extender los insumos de pausa con fechas de datos recientes con pedido y su cobertura. Hacer la lectura común accesible incluso cuando la política de bids esté apagada.
- [ ] Implementar una subpolítica de pausa explícita. Para una pausa por cero pedidos, un pedido reciente conocido abre una protección por episodio. Anclarla a la fecha más antigua del dato que la motivó más diez días UTC. No usar la fecha de ejecución ni la de reobservación.
- [ ] Conservar ancla y vencimiento durante el episodio. Otra corrida, reobservación, propuesta o venta posterior no extiende ese plazo. Representar protección ausente, activa, consumida o indeterminada con datos explícitos y transiciones append-only.
- [ ] Al vencer, reevaluar. Un pedido maduro conocido elimina la condición de cero ventas y cierra el episodio. Si el pedido ancla se corrigió a cero, la protección queda consumida y la pausa puede proceder si siguen cumpliéndose sus requisitos. Otro pedido todavía inmaduro no renueva el episodio consumido. Un descenso pasajero de gasto no abre otro episodio.
- [ ] Tratar una fuente incompleta como desconocida. No convertirla en cero ni en una nueva gracia. Evaluar ambos motivos de pausa antes de proteger por cero ventas. Si también califica la pausa económica habilitada con sus datos completos, esa pausa conserva precedencia y la venta reciente no la oculta.
- [ ] Usar el mismo evaluador puro al decidir y al revalidar. Distinguir explícitamente aplicar, esperar y descartar. Una espera de protección no cobra cuota, no entra en `applying` y no reinicia el veto de 48 horas.
- [ ] Después del veto original, la espera por protección bloquea solo la pausa por cero ventas. Permitir que el mismo evaluador juzgue las pujas y que una puja autorizada siga su aplicación habitual. Distinguir esta causa en `_SQL_CLAVES_BLOQUEADAS`, `_procesa_decisora` y sus consumidores; no heredar el bloqueo de toda la hoja por una fila `released`.
- [ ] Conservar una sola fila pendiente y su reserva de corte, ancla y vencimiento originales. Registrar la espera sin dejar una decisión huérfana. La pausa económica conserva precedencia. Mantener los bloqueos existentes de veto pendiente, veto del dueño y aplicación en curso. No descartar y reencolar para liberar las pujas.
- [ ] Preservar historia antes de modificar la política viva. Versionar insumos y subpolítica también en pausas con bids apagados. Ausencia del marcador conserva la semántica antigua. Evitar que `eras.py` o el replay de `niveles_v3` consuman por defecto la regla nueva.
- [ ] Revalidar pendientes bajo la política activa registrada para ese intento. Conservar intactos los inputs originales de la decisión. Activar la nueva regla después de las comprobaciones de la entrega, sin una etapa separada de observación.

**Resultado observable.**

- [ ] Mostrar para el caso sintético de 500 MXN, 120 clics, cero pedidos maduros y un pedido reciente una protección con fecha fija bajo la versión nueva. Mostrar la pausa original bajo la versión histórica. Guardar ambos resultados en `m1/antes-despues.json`.

**Verificar con pruebas focalizadas.**

- [ ] Demostrar roja contra la base la regresión central de venta reciente. Cubrir con pruebas existentes o ampliadas vencimiento, corrección a cero, ausencia de datos, pausa económica, bids apagados y replay antiguo. No crear una prueba por cada casilla del plan.
- [ ] Ejecutar `uv run --frozen python -m pytest -q tests/test_optimizer_politica.py tests/test_optimizer_caso.py tests/test_lecturas_caso.py tests/test_apply_cola.py tests/test_cycle.py` con PostgreSQL para los casos que lo requieran. Registrar skips como no verificados.

**Verificar el recorrido operativo.**

- [ ] Ejecutar dos ciclos y una liberación de cola sobre PostgreSQL desechable. Introducir una venta durante el veto. Comprobar que la espera posterior permite evaluar y aplicar una puja autorizada, sin forzar un movimiento si sus reglas indican mantener. Aprobar cuando no se duplique la pausa, no se cobre cuota por esperar y se conserven ancla, vencimiento y veto originales. Verificar también la precedencia económica y los bloqueos del dueño y de aplicación.

**Verificar rendimiento.**

- [ ] Alternar base y candidato sobre la misma copia de M0. Comparar lectura, resolución y persistencia de episodios contra el presupuesto registrado. Evitar una consulta nueva por hoja si una lectura por lote resuelve el dato.

**Revisión.** Revisar temporalidad, datos desconocidos, cola y fidelidad de replay sobre casos existentes. La revisión no exige un piloto ni datos futuros.

**Entrega.**

- [ ] Entregar y activar M1 dentro del alcance autorizado con CI verde y reversa de selección de política. Comprobar el primer ciclo real. Volver a la versión anterior no borra episodios ni convierte decisiones históricas a otra versión.

## Resolver la meta y el límite económico del mismo producto (M2)

**Depende de.** Traspaso de AUTO-03, M0 para identidad y economía y M1 por contrato de casos e historia compartidos. Esta entrega amplía C-F de AUTO-03; no vuelve a construir su meta por producto.

**Archivos.**

- [ ] Extender `app/optimizer/productos.py` y `app/productos_io.py` recibidos de AUTO-03. Mantener su resolución pura y lectura por lote; no crear un resolutor paralelo.
- [ ] Modificar los contratos necesarios de `app/optimizer/goals.py`, `app/optimizer/caso.py`, `app/cycle.py`, `app/apply_cola.py` y replay. Reutilizar `evaluacion_producto_ciclo` y sus referencias desde decisiones. Añadir una migración solo por datos, permisos o invariantes nuevos de la economía compatible, con reversa y pruebas monetarias.

**Construir.**

- [ ] Resolver el producto anunciado mediante `product_ad → listing → product_id`. No tratar un `product_target` como un producto propio ni asignar el producto de un anuncio a toda su campaña.
- [ ] Construir una economía efectiva con meta, procedencia, punto de equilibrio y referencias compatibles con las ventas evaluadas. Mantener moneda y mercado. Separar la elección de la meta de la disponibilidad de un margen válido.
- [ ] Considerar exclusivo un grupo solo si todos sus anuncios no archivados, incluidos los pausados, tienen identidad conocida del mismo producto. Comprobar también el alcance histórico de la ventana que consume cada hoja.
- [ ] Aplicar el peldaño `min(fraccion_configurada × margen_producto, MARGEN_BANDA_MAX)` solo con datos aptos. Reutilizar las fuentes de configuración existentes. Mantener precedencia de goals explícitos. Documentar la regla de esta entrega con el caso 8 % por 0.5 igual a 4 %, sin elevarlo al piso de plataforma.
- [ ] Cuando gane el peldaño de producto, aplicar la meta calculada desde el primer ciclo elegible y ante cambios posteriores de margen. Desde una meta previa de 25 %, el caso anterior aplica 4 %, no 24.5 %. Esta es una excepción explícita a la rampa de 0.5 puntos de la meta. Conservar los límites y pasos vigentes de cambio de puja.
- [ ] Exigir atribución compatible con el margen durante la ventana de las hojas. Si hay halo sin margen atribuible, identidad desconocida o muestra financiera insuficiente, conservar la economía vigente completa. No combinar target individual con un equilibrio promedio disfrazado de individual.
- [ ] Al perder elegibilidad, resolver la economía completa de respaldo en ese ciclo. Cada peldaño usa su propio historial; el target de producto no se convierte en ancla del margen familiar o de plataforma. Al recuperar elegibilidad, aplicar la meta individual calculada entonces. No interpolar entre economías incompatibles.
- [ ] Extender la lectura por lote, evaluación append-only y referencia desde decisiones de AUTO-03. Versionar los datos económicos nuevos; el replay usa la evaluación congelada y conserva la semántica anterior. La pantalla no resuelve política y esta fase no modifica la interfaz.
- [ ] Usar ese mismo resolutor en `_target_pause_vigente` y `_revalida_pause` con la evaluación fresca del ciclo ejecutor. Conservar las mismas reglas de goal, identidad, atribución, moneda y respaldo. La meta puede cambiar por insumos nuevos, pero no por usar otro camino de resolución. Registrar la evaluación de cada intento sin sobrescribir los inputs originales de la decisión.
- [ ] Comprobar las cuentas sobre datos históricos disponibles y activar la economía individual en los productos elegibles al entregar. Mantener `politica.decide` como único dueño de la puja. No añadir una fase de simulación, otro motor, cron o cliente de Amazon.

**Resultado observable.**

- [ ] Mostrar meta calculada y aplicada, equilibrio, procedencia y motivo de elegibilidad por producto. Dos productos con distinta economía reciben referencias distintas solo donde su identidad y atribución lo permiten. Guardar cobertura, transiciones y referencias de decisión y revalidación en `m2/economias.json`.

**Verificar con pruebas focalizadas.**

- [ ] Comprobar cuentas monetarias, goal explícito, grupo compartido, halo incompatible, dato faltante y replay. Reutilizar la evidencia de primera entrada ya cubierta por AUTO-03. Añadir casos para cambios posteriores de margen, respaldo completo y economía fresca de la cola. Reutilizar `tests/test_productos_meta.py`, `tests/test_productos_meta_ciclo.py`, `tests/test_optimizer_goals.py`, `tests/test_optimizer_caso.py`, `tests/test_cycle_target_ciclo.py`, `tests/test_apply_cola.py`, `tests/test_economia_observada.py` y los casos de vínculo de anuncios.

**Verificar el recorrido operativo.**

- [ ] Ejecutar un ciclo con productos exclusivos, compartidos y sin margen en PostgreSQL. Comparar decisión guardada y replay. Cambiar después la fuente económica y comprobar que la decisión histórica sigue reproduciéndose igual.
- [ ] Recorrer decisión, veto y liberación con el caso de 5000 MXN de gasto, 10000 MXN de ventas, un pedido y 120 clics. Si la economía elegible no cambia, la cola debe conservar la meta individual del 4 % y la pausa económica, sin sustituirla por el 20 % general. Repetir con un cambio legítimo de goal o elegibilidad durante el veto. Registrar su efecto fresco y conservar el replay original.

**Verificar rendimiento.**

- [ ] Medir resolución por lote y ciclo completo con el mismo conjunto de M0. Aprobar solo dentro del presupuesto y sin multiplicar consultas por cada combinación de producto y keyword.

**Revisión.** Revisar atribución, doble conteo, transición de metas y consistencia económica entre decisión y revalidación. Comprobar que la meta entra directamente y la puja conserva sus límites.

**Entrega.**

- [ ] Entregar y activar M2 con CI verde en los productos cuya economía esté resuelta. No exigir una cohorte experimental ni un tamaño mínimo de muestra futuro. La selección de la versión anterior restaura la cascada previa sin borrar evidencia.

## Compartir el aprendizaje entre anuncios del mismo producto (M3)

**Omitido en esta entrega.** M0 no acredita una diferencia funcional pendiente:
el agregado de AUTO-03 ya colapsa por fecha y suma por producto sin doble
conteo, y el freno de subidas solo aplica a grupos exclusivos, donde la
intersección con los candidatos a pausa es cero en MX y US; con la pausa live
en vuelo, I.5 ya omite la subida. Evidencia:
[`docs/evidencia/orbit-07/m3m4/comparacion-auto03.md`](../docs/evidencia/orbit-07/m3m4/comparacion-auto03.md).

**Depende de.** M2 y la política de aprendizaje resuelta con los datos disponibles en M0.
Se entrega con M4 si su freno no cambia decisiones por sí mismo. M0 resuelve esa agrupación
antes de abrir una PR de M3, sin esperar resultados comerciales futuros. Comparar también
contra AUTO-03 H, que ya agrega gasto y propone pausas por producto. Si cubre el resultado,
conservar su evidencia y omitir la construcción correspondiente de M3 y M4.

**Archivos.**

- [ ] Extender el resolutor y lector de AUTO-03 ampliados en M2, el ciclo y el contrato de replay solo para la diferencia pendiente. Si M0 acredita un freno de subidas con efecto propio, extender también el caso y `app/optimizer/politica.py`. Reutilizar el colapso y la madurez por fecha de `app/evaluacion_catalogo.py` dentro de cada identidad. Añadir almacenamiento del episodio solo si no está cubierto por una fuente existente apta.

**Construir.**

- [ ] Reutilizar el agregado de AUTO-03 y comprobar que colapsa `ads_product_metric_observation` por identidad y fecha a la observación disponible al decidir antes de sumar por producto y mercado. Corregir solo una diferencia demostrada; no crear otro agregado ni sumar keywords, grupos o campañas sobre el total consolidado.
- [ ] Resolver un único episodio de aprendizaje por producto y mercado. Congelar inicio, ámbito, gasto conocido, política y umbral. Crear otra keyword, campaña o listing del mismo producto no reinicia ni multiplica ese episodio.
- [ ] Partir de los límites por mercado guardados en AUTO-03. Si el episodio requiere otra regla, justificar en M0 la diferencia, su fórmula, unidad y parámetros. No sustituir en silencio los límites elegidos por el dueño ni abrir otro cupo de gasto. Resolver cualquier parámetro nuevo antes de activar ese alcance, sin una fase indefinida de observación.
- [ ] Definir y registrar cuándo termina o se renueva un episodio. Una renovación que autoriza gasto adicional exige la política de negocio acordada. No reiniciarlo por un cambio de ciclo, un hueco de datos o una reobservación.
- [ ] Si M0 acredita el freno de subidas, transportar el episodio congelado en el caso. Cuando esté agotado con evidencia suficiente, `politica.decide` impide subidas ordinarias en grupos exclusivos y devuelve un motivo específico. Conservar recortes, pausas y recuperación del camino existente. No filtrar después un `Mover` en el ciclo ni tocar pujas compartidas. Si M3 solo prepara M4, no añadir una regla redundante a la política de pujas.
- [ ] Excluir impulsos abiertos, que conservan su tope y su vigía. Ninguna evaluación de efecto de M5 o M6 puede conceder gasto que esta política haya bloqueado.

**Resultado observable.**

- [ ] Con la misma evidencia y fecha de corte, mostrar el mismo gasto aunque cambien joins o se añadan entidades sin actividad. Una reobservación posterior reemplaza el valor de su fecha; no se suma al anterior. Actividad nueva sí aumenta el gasto. Ninguno de esos cambios reinicia el episodio ni multiplica su umbral. Guardar los casos en `m3/aprendizaje.json`.
- [ ] Si M3 se entrega sola, mostrar una subida que la política previa permitía y que el aprendizaje agotado bloquea bajo la regla de M0. Si ese caso no existe en el alcance elegido, presentar el agregado como insumo del retiro de M4. No declarar una reducción de exposición por añadir datos que no cambian decisiones.

**Verificar con pruebas focalizadas.**

- [ ] Reutilizar `tests/test_ads_producto_ingesta.py` para consolidar campañas y `tests/test_evaluacion_catalogo.py` para colapso y madurez por fecha. Si hay un freno nuevo, extender los casos de política y replay que lo comprueben. Cubrir correcciones legítimas, episodios que no se reinician, monedas y propiedad de impulso. Comprobar que un grupo compartido conserva su puja y que recortes y recuperaciones siguen disponibles.

**Verificar el recorrido operativo.**

- [ ] Ejecutar ciclos antes y después del cruce y tras ampliar la estructura publicitaria. Aprobar cuando no aparezca otro cupo de aprendizaje por agregar entidades y las decisiones sigan saliendo de un solo evaluador.

**Verificar rendimiento.**

- [ ] Comparar agregación y ciclo completo con M0. Mantener una resolución por producto y mercado, sin repetirla por hoja.

**Revisión.** Revisar definición del episodio, dinero autorizado, doble conteo y propiedad del impulso.

**Entrega.**

- [ ] Entregar M3 con M4 cuando su resultado sea preparar datos para el retiro. Si tiene efecto propio, activar su freno con CI verde y parámetros explícitos. Comprobar gasto agregado y decisiones en el primer ciclo. Describir únicamente la exposición que realmente impide; M4 añade el retiro individual cuando corresponda.

## Extender la pausa existente con el aprendizaje pendiente (M4)

**Entregado en esta entrega (política v3).** AUTO-03 H/I solo veía
`[D-60, D-30)` y pausaba productos que vendieron después de esa ventana. La
regla nueva espera una venta inmadura en `[D-30, D-1]` con ancla fija,
madurez de 30 días y liberación al día 31 (cuando la venta entra a la ventana
madura), lee el reporte de productos hasta D-1 al decidir y al revalidar, sin
otro escritor, cola, reversa ni `kind`. Evidencia:
[`docs/evidencia/orbit-07/m3m4/comparacion-auto03.md`](../docs/evidencia/orbit-07/m3m4/comparacion-auto03.md).

**Depende de.** Camino de retiro de AUTO-03 G-I recibido, M1, M2 y la diferencia de M3. Si AUTO-03 ya cubre el resultado, omitir esta entrega y registrar la evidencia heredada. Requiere cobertura de ventas y alcance por anuncio demostrada en M0, además de su contrato temporal específico de producto.

**Archivos.**

- [ ] Extender solo los puntos necesarios del resolutor, `app/cycle.py`, `app/apply_cola.py`, `app/apply_producto.py` y `app/retiro_anuncios.py` recibidos de AUTO-03. Conservar `kind='pause'` sobre `product_ad` (S11); no introducir `pause_product_ad`. Reutilizar permisos, ledger y reversa. Añadir esquema únicamente para una diferencia de estado necesaria y comprobada.

**Construir.**

- [ ] Proponer pausa solo con aprendizaje agotado, cero compras y cero ventas totales explícitas, datos maduros completos y alcance comprobado. Una venta de otro SKU impide afirmar cero ventas. Aplicar el contrato temporal de producto de M0, con episodio de ancla fija y liberación según esa fuente. No reutilizar el vencimiento de diez días de M1 para una fuente que exige treinta.
- [ ] Usar la ventana por producto que M0 haya acreditado. AUTO-03 H/I usa `[D-60, D-30)` y reobservación por fecha desde treinta días de edad. Reutilizar la evidencia de cobertura D.7/S6 y declarar los huecos que sigan existiendo. No declarar cobertura a partir de un máximo global ni rellenar ausencias con cero.
- [ ] Encolar únicamente anuncios que contribuyeron al alcance comprobado. En grupos compartidos, retirar esos anuncios individuales sin bajar la puja común ni pausar los productos vecinos. No desactivar la publicación.
- [ ] Mantener cola, veto, revalidación, claim y cuota como dueños del permiso para ejecutar. `retiro_anuncios` conserva el único PUT y la reconciliación. Vincular decisión e intención antes del HTTP mediante autorización verificable, sin simular autorización humana con una cadena.
- [ ] Revalidar ventas, composición, identidad, modo, goal, propiedad de impulso y estado vivo antes de escribir. Descartar un alcance obsoleto. Un resultado ambiguo conserva su intención y se concilia antes de otro intento.
- [ ] Reutilizar y comprobar la reversa de AUTO-03 ante las condiciones nuevas, antes de activar. Restaurar solo anuncios existentes retirados y confirmados por esta política. No reactivar pausas ajenas ni recrear anuncios archivados bajo autorización de simple reposición.
- [ ] Al apagar, impedir nuevos efectos y descartar pendientes de esta política. Conciliar las intenciones ya enviadas. No reponer automáticamente anuncios ni borrar auditoría.

**Resultado observable.**

- [ ] Demostrar que se retira solo el producto elegible de un grupo compartido. Mostrar confirmaciones parciales por anuncio, nunca un éxito total si faltan anuncios. Guardar evidencia y reversa en `m4/recorrido.json`.

**Verificar con pruebas focalizadas.**

- [ ] Extender `tests/test_retiro_anuncios.py`, `tests/test_apply_cola.py`, `tests/test_cycle_apply.py` y las pruebas de esquema pertinentes. Comprobar una cuota e intención por anuncio, cambio de composición, fallo después del PUT y reversa sin afectar anuncios ajenos.
- [ ] Comprobar un pedido del producto con más de diez y menos de treinta días. Si la fuente exige treinta días, aprobar solo cuando la pausa siga esperando hasta cumplir el contrato de madurez y reobservación. No ampliar el plazo del episodio al repetir la lectura.

**Verificar el recorrido operativo.**

- [ ] Recorrer decisión, veto, revalidación, retiro, interrupción y conciliación en PostgreSQL aislado con transporte de Amazon simulado en el borde. Aprobar cuando no se duplique escritura ni cuota y la reversa restaure exactamente el alcance propio.
- [ ] Preparar para la activación autorizada una lectura de Amazon antes y después del primer retiro y de su reversa cuando sea necesaria. Esa comprobación operativa no inicia un período de prueba comercial. No ejecutar escrituras al redactar el plan.

**Verificar rendimiento.**

- [ ] Medir tamaño de lote, consultas y llamadas por anuncio. Comparar el costo añadido con el camino de retiro existente. Durante la operación activa, registrar el gasto adicional desde el cruce del umbral hasta la confirmación para corregir la política si hace falta.

**Revisión.** Revisar autoría, idempotencia, reversa y alcance compartido. Bloquear activación si estos resultados no están demostrados.

**Entrega.**

- [ ] Entregar la diferencia M4 con CI verde y reversa comprobada, sin piloto previo. Conservar el modo de pausa y sus límites vigentes; si está en práctica, el encendido sigue correspondiendo al dueño desde la pantalla de AUTO-03 I. Con pausa autorizada en vivo, confirmar los retiros contra Amazon. Registrar que el umbral opera con retraso y medir el gasto posterior como resultado operativo.

## Preparar los datos económicos que utilizará la decisión (M5)

**Depende de.** M0 para ventanas y comparabilidad y M2 para economía compatible. Puede construirse antes de las diferencias M3 o M4 una vez recibido AUTO-03 y con los archivos comunes libres. Se entrega junto con M6, sin despliegue intermedio de observación.

**Archivos.**

- [ ] Extender `app/optimizer/caso.py`, `app/lecturas_caso.py` y la evidencia persistida del ciclo. Reutilizar `v_cambio_bid` y las métricas point-in-time. Mantener separados los cambios de lectura y regla dentro de la misma entrega M5 más M6 para verificarlos con claridad.

**Construir.**

- [ ] Definir un episodio desde un cambio confirmado. Registrar identidad, ámbito, moneda, fecha, ventanas y otros cambios. Representar comparable y no comparable con motivos explícitos.
- [ ] Leer gasto, ventas y pedidos maduros antes y después, excluyendo el día del cambio. Un cambio adicional de puja, presupuesto, placement, precio o alcance dentro de las ventanas invalida la comparación. La ausencia de una fuente necesaria no demuestra estabilidad.
- [ ] Separar comparabilidad histórica y uso en una decisión nueva. Un cambio posterior al cierre del post no altera retroactivamente las métricas de ese episodio. Si modifica la puja o sus condiciones, el episodio anterior queda histórico y no gobierna la repetición del nuevo cambio. M6 solo consume el episodio del último cambio confirmado cuyas condiciones sigan vigentes.
- [ ] Usar solo observaciones disponibles en el instante evaluado. No reconstruir el pasado con margen actual, estado actual de identidad o datos corregidos después.
- [ ] Calcular una estimación de contribución solo con ventas y margen compatibles. Si faltan, conservar gasto, ventas y pedidos como observaciones y declarar la contribución desconocida. No llamar utilidad real a `ventas × margen − gasto`.
- [ ] Resolver las ventanas con episodios históricos disponibles. Siete días posteriores completos con diez días de madurez llegan como pronto en D+17. Usar episodios que ya cumplen esa condición y siguen siendo aplicables. Para el último cambio todavía inmaduro, conservar la evaluación de trayectoria vigente. No esperar D+17 para desplegar ni frenar a las demás hojas.
- [ ] Preparar el resultado económico que consumirá M6. Distinguir inmadurez, información insuficiente, cambio posterior y datos ausentes. No exigir un período de producción dedicado solo a observarlo.

**Resultado observable.**

- [ ] Entregar `m5/episodios.json` con comparaciones reproducibles, exclusiones y cobertura por mercado. Mostrar un caso útil y un caso no comparable sin fabricar un valor para el segundo. Distinguir un episodio histórico comparable de otro aplicable a la decisión actual.

**Verificar con pruebas focalizadas.**

- [ ] Ampliar los casos necesarios en `tests/test_lecturas_caso.py`, `tests/test_optimizer_caso.py` y `tests/test_rejuega_niveles.py` para fronteras temporales, datos futuros, cambio dentro de las ventanas, cambio después del cierre y freeze. Reutilizar los casos de trayectoria existentes.

**Verificar el recorrido operativo.**

- [ ] Reproducir episodios sobre una copia histórica con la misma fecha de corte. Comprobar que añadir la lectura por sí sola no altera decisiones y que la regla de M6 modifica únicamente los casos previstos. Es una comprobación local de la entrega conjunta, no una etapa adicional de producción.

**Verificar rendimiento.**

- [ ] Medir lectura, armado de todos los casos y persistencia adicional, no solo `lee_plataforma`. Comparar base y candidato de forma alternada contra el presupuesto de M0 con igual número de casos.

**Revisión.** Revisar comparabilidad, madurez y atribución. Comprobar que los cambios de decisión pertenecen a la regla de M6 y no a efectos secundarios de la lectura.

**Entrega.**

- [ ] Incluir M5 en la entrega M6 con CI verde y cobertura por entidad. Activar la regla nueva donde tenga insumos; conservar la evaluación vigente solo en las entidades que aún no los tienen. Si una fuente necesaria no existe, resolverla o declarar ese alcance pendiente sin bloquear las otras mejoras.

## Aplicar la evaluación económica y dar seguimiento a su resultado (M6)

**Depende de.** M5, economía compatible de M2 y el freno de M3 cuando tenga efecto propio.
Si M3 solo prepara datos para M4, M5 más M6 puede entregarse después de M2 sin esperar ese
retiro. Respetar las restricciones de aprendizaje vigentes. M4 conserva su precedencia cuando esté activo.

**Archivos.**

- [ ] Modificar `app/optimizer/politica.py`, el contrato del caso, el despacho histórico y la selección de política. Actualizar el diseño y los casos monetarios afectados.

**Construir.**

- [ ] Escribir en `m6/politica-implementacion.md` la regla y parámetros elegidos, su razón económica y los casos existentes que los sustentan. Permitir repetir un recorte cuando la evidencia compatible muestra ventas conservadas y mejor economía, o frenar repetir una subida cuyo tráfico adicional no compensa el gasto. Comprobar también los casos donde la nueva regla sería incorrecta.
- [ ] Usar únicamente episodios comparables y aplicables al último cambio según M5. Con evidencia insuficiente, conservar la evaluación de trayectoria vigente. No imponer un bloqueo adicional a todas las hojas ni transformar falta de datos en permiso para aumentar gasto.
- [ ] Limitar el freno económico a las repeticiones cuyo episodio ya está maduro. Una subida permitida por la trayectoria vigente en D+8 puede ocurrir antes de conocer su efecto económico en D+17. No afirmar que M6 la impide. Si hubo otra puja, evaluar su episodio cuando corresponda, sin usar el anterior como si describiera la puja actual ni imponer una espera adicional de 17 días.
- [ ] Mantener inicialmente regreso por desplome, espera ordinaria, mínimos de evidencia, pisos y pasos de puja. Cambiar la condición de repetición definida en esta entrega. Alterar otra regla requiere una razón concreta y alcance explícito, sin agregar un período de prueba como requisito general.
- [ ] Versionar política e insumos. Reproducir exactamente todas las eras anteriores sin consultar configuración vigente. El camino vivo debe tener un único dueño y no combinar dos veredictos de bid.
- [ ] Activar la mejora verificada en las entidades elegibles dentro del alcance autorizado. No exigir un piloto, grupo de control, duración mínima o mejora estadística previa para utilizarla.
- [ ] Definir el seguimiento operativo y la respuesta ante un fallo o deterioro. Usar gasto, ventas, cobertura y contribución estimada compatible, separados por mercado y moneda. Ante un defecto comprobado, corregir o volver a la versión anterior; no esperar a completar un experimento.
- [ ] Registrar versiones y fechas de activación para interpretar los resultados posteriores. No atribuir causalidad a una simple comparación antes y después ni prometer más utilidad por haber pasado las pruebas. Esa limitación del informe no bloquea la activación.

**Resultado observable.**

- [ ] Mostrar las decisiones que cambió la política activa y sus motivos desde el primer ciclo. Añadir resultados económicos al seguimiento cuando los datos estén disponibles. Un rejuego comprueba decisiones; no fabrica ventas que nunca ocurrieron.

**Verificar con pruebas focalizadas.**

- [ ] Reutilizar `tests/test_optimizer_politica.py`, `tests/test_optimizer_caso.py`, `tests/test_lecturas_caso.py` y `tests/test_rejuega_niveles.py`. Añadir solo casos que detecten una decisión monetaria incorrecta, contaminación temporal o cambio indebido del replay.
- [ ] Comprobar una repetición permitida antes de D+17, un último cambio maduro donde M6 sí modifica el veredicto y una puja posterior que vuelve histórico el episodio anterior. El primer caso conserva la regla vigente; los otros usan únicamente la evidencia que les corresponde.

**Verificar el recorrido operativo.**

- [ ] Recorrer selección autorizada, aplicación de la nueva regla y vuelta a la versión anterior. Comprobar que apagar no inventa reversas, no borra decisiones y no afecta entidades fuera de alcance. Verificar el primer ciclo tras activar, sin dejar la mejora en simulación.

**Verificar rendimiento.**

- [ ] Comparar ciclo completo y llamadas externas sobre la misma carga de M0. Aprobar dentro del presupuesto registrado. No confundir menor duración causada por omitir entidades con una mejora de rendimiento.

**Revisión.** Revisar que la regla implementa la mejora prevista, usa datos compatibles y conserva una reversa operativa. El resultado comercial posterior se sigue durante el uso.

**Entrega.**

- [ ] Entregar y activar M5 más M6 con CI verde, decisiones comprobadas, alcance resuelto y reversa. Mantener seguimiento desde el primer ciclo. La falta de una medición estadística posterior no deja apagada la mejora ni retrasa las otras entregas.

## Cerrar el programa con evidencia de cada resultado

- [ ] Informar qué mejora está activa y qué decisiones modifica. Separar comprobación funcional de resultados comerciales observados. Enumerar alcances pendientes por insumos o autorización, sin introducir períodos experimentales como dependencia.
- [ ] Conservar replay de decisiones antiguas, configuración de cada entrega y referencias a las fuentes. Confirmar que todos los cambios externos tienen reversa disponible y evidencia de autoría.
- [ ] Actualizar el estado de este plan y su entrada del manifest. Distinguir resultados heredados de AUTO-03, diferencias entregadas y pendientes. No atribuir a ORBIT 07 la construcción de AUTO-03 ni cerrar stock, presupuestos o reactivación automática por haber completado esta continuación.
- [ ] Entregar las PRs y evidencias al dueño. No presentar el plan terminado como implementación terminada ni una simulación como mejora de utilidad demostrada.

## Apéndice A. Evidencia de planificación y límites

El caso sintético ejecutado en la base revisada produjo estos resultados con 500 MXN de gasto,
cero pedidos maduros y un pedido reciente. Con 99 clics produjo `Mantener(venta_reciente)`.
Con 120 clics y umbral de 100 produjo `Pausar(pause_umbral)`. El resultado completo está en
[la evidencia del plan](../docs/evidencia/orbit-07-plan/pausa-venta-reciente.json).
El prototipo fue desechable y no cambió código de producto ni creó decisiones en producción.

La evidencia demuestra el orden de evaluación. No demuestra incidencia real, daño comercial,
elegibilidad financiera por producto, beneficio de un umbral nuevo ni efecto causal de una puja.
M0 resuelve las fuentes y los casos disponibles. Los resultados comerciales se siguen durante
la operación activa, sin exigirlos como condición previa de entrega.

Para reproducir la sonda sin escribir bytecode ni acceder a producción, ejecutar desde la raíz
con el entorno sincronizado. El SHA de referencia figura al inicio del plan.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests uv run --frozen python - <<'PY'
import datetime as dt
from test_optimizer_caso import fabrica_caso, cortes, nivel, tramo
from app.optimizer.caso import InsumosPausa
from app.optimizer.bid import PAUSE_COST_MIN
from app.optimizer.politica import decide

for clics in (99, 120):
    caso = fabrica_caso(
        propia=nivel(tramo(clics=clics, pedidos=0, venta="0", gasto="500")),
        pedidos_inmaduros=1,
        pausa=InsumosPausa(
            cortes(clicks=clics, cost="500"),
            100,
            PAUSE_COST_MIN["amazon_mx"],
            None,
            None,
        ),
        observado_al=dt.datetime(2026, 10, 9, 8, 40, tzinfo=dt.UTC),
    )
    print(clics, decide(caso))
PY
```

## Apéndice B. Alternativas descartadas

- Cambiar solo el target por producto dejaría el punto de equilibrio promedio dentro del juicio de pérdida. M2 mueve juntos los datos compatibles.
- Reiniciar una gracia de diez días en cada corrida puede aplazar indefinidamente una pausa. M1 conserva un episodio con vencimiento fijo.
- Reemplazar tráfico por utilidad aún inmadura inventaría certeza. M5 y M6 se entregan juntos y usan datos compatibles donde ya estén disponibles.
- Bajar una puja compartida para detener un solo producto afectaría a sus vecinos. M3 no lo hace y M4 retira anuncios individuales comprobados.
- Sumar campañas y keywords sobre el agregado por producto duplicaría gasto. M3 utiliza una sola fuente y un solo grano.
- Llamar presupuesto estricto al umbral de aprendizaje ocultaría el rezago. M4 mide el gasto posterior y declara el alcance real.
- Añadir otro escritor, cola o cron competiría por las mismas entidades. Todas las fases se integran en el ciclo y escritores actuales.
- Cambiar números globales solo para producir más acciones no explica una mejora. Cada regla debe tener una razón económica y casos que comprueben el comportamiento antes de activarla.
- Exigir semanas de simulación o pilotos comerciales antes de activar contradice la instrucción del dueño. Se comprueba la implementación con datos disponibles y se da seguimiento durante el uso.

## Apéndice C. Riesgos y decisiones de negocio

| Riesgo | Fase y tratamiento |
|---|---|
| Faltan fechas de pedidos recientes | M0 y M1 amplían lectura; el total agregado actual no acredita un vencimiento. |
| Una protección nueva permite gasto durante la espera | M1 mide ese gasto y conserva la pausa económica independiente. |
| Una espera de protección congela pujas por el bloqueo de cola | M1 permite evaluar pujas después del veto original y conserva una sola pausa pendiente. |
| La cola usa otra economía al liberar una pausa | M2 comparte el resolutor y registra datos frescos por intento sin alterar la decisión histórica. |
| La meta individual queda retrasada por la rampa anterior | M2 aplica la meta elegible desde ese ciclo y conserva los límites de cambio de puja. |
| Cambiar el código reescribe el pasado | M1 a M6 congelan versión e insumos; ausencia de marcador significa semántica histórica. |
| Cero productos cumplen identidad, halo y cobertura | M0 declara cobertura cero. No inventar ceros ni relajar elegibilidad para activar. |
| El margen histórico no describe ventas atribuidas actuales | M2 conserva procedencia y compatibilidad; sin ellas, no publica economía individual decisoria. |
| El umbral de aprendizaje no está elegido | M0 presenta fórmula y dinero permitido. El dueño elige el riesgo de negocio antes de activar M3 o M4. |
| El freno de M3 no cambia ninguna decisión en el alcance elegido | M0 lo comprueba con la regla acordada. En ese caso, M3 se entrega como preparación de M4. |
| Datos consolidados pierden reparto entre campañas | M0 acredita alcance. M4 excluye anuncios cuya contribución e identidad no puedan demostrarse. |
| Dos actores retiran el mismo anuncio | M4 usa intención, llave, claim y escritor existentes; no duplica cuota ni PUT. |
| La comparación posterior no permite atribuir el resultado a una sola regla | M6 registra versiones y límites de interpretación. Eso no impide usar una mejora funcionalmente verificada. |
| Una subida se repite antes de que madure su episodio económico | M5 distingue historia y aplicabilidad. M6 conserva la trayectoria vigente mientras falten datos del último cambio. |
| El programa crece hasta reemplazar todo el motor | Cada PR cambia una decisión concreta; budgets, inventario y UI siguen fuera. |

El usuario ha pedido el plan y ha descartado períodos largos de prueba. Los importes nuevos de
aprendizaje se resuelven con datos y una recomendación concreta durante la implementación.
La activación utiliza la autorización vigente para ese alcance. Esta revisión del plan no inventa
un importe de riesgo ni inicia por sí misma escrituras en producción.

## Apéndice D. Fuentes, habilidades y adaptación a Orbit

- [Contexto vigente](../docs/CONTEXTO.md) y [diseño original con deltas](../docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md).
- [Plan BIDS 02](bids-02.md) y [diseño BIDS 02](../docs/superpowers/specs/2026-10-09-bids-02-design.md).
- [Diseño AUTO-03 de la copia local](../docs/superpowers/specs/2026-10-24-decisiones-producto-design.md). Para la base recibida manda `plans/decisiones-producto-01.md` de `origin/master` y sus deltas aprobados, no este borrador local anterior. La [consulta del ejecutor](../docs/evidencia/orbit-07-plan/auto03-estado.json) y el [mapa de continuación](../docs/evidencia/orbit-07-plan/continuacion-auto03.md) documentan la diferencia.
- [Política vigente](../app/optimizer/politica.py), [caso](../app/optimizer/caso.py), [lecturas](../app/lecturas_caso.py) y [replay](../app/optimizer/replay.py).
- [Cola](../app/apply_cola.py), [retiro de anuncios](../app/retiro_anuncios.py), [economía observada](../app/economia_observada.py) y [rejuego con aproximaciones declaradas](../tools/rejuega_niveles.py).
- [Colapso y madurez por fecha existentes](../app/evaluacion_catalogo.py) y [sus pruebas](../tests/test_evaluacion_catalogo.py).
- [Revisión cruzada con Grok, Kimi, Argon y Fable](../docs/evidencia/orbit-07-plan/cross-review/README.md). Los informes corresponden a la versión anterior al presente ajuste. El [registro del ajuste](../docs/evidencia/orbit-07-plan/ajuste-post-review.md) relaciona sus conclusiones con este plan.
- [Poteto-mode](/Users/dn/.agents/skills/poteto-mode/SKILL.md) y [guía de planes por fases](/Users/dn/.agents/skills/poteto-mode/playbooks/multi-phase-plan.md).
- [Sequence Work into Verifiable Units](/Users/dn/.agents/skills/principle-sequence-verifiable-units/SKILL.md) determina las PRs separadas. [Model the Domain](/Users/dn/.agents/skills/principle-model-the-domain/SKILL.md) determina episodios y resultados explícitos.
- [Laziness Protocol](/Users/dn/.agents/skills/principle-laziness-protocol/SKILL.md) mantiene el ciclo y sus escritores. [Explain the Number](/Users/dn/.agents/skills/principle-explain-the-number/SKILL.md) separa comprobación de decisiones y resultados observados durante el uso.
- [Prove It Works](/Users/dn/.agents/skills/principle-prove-it-works/SKILL.md) exige comprobar el resultado operativo. [Guard the Context Window](/Users/dn/.agents/skills/principle-guard-the-context-window/SKILL.md) permite investigación acotada en paralelo.
- [Separate Before Serializing Shared State](/Users/dn/.agents/skills/principle-separate-before-serializing-shared-state/SKILL.md) separa la preparación de lectura y hace secuencial la construcción sobre el motor compartido.
- Usar `how` para los contratos que cambien y revisión adversarial focalizada de dinero y autoría antes de activar. Usar `technical-writing` y `unslop` para actualizar este documento.

La plantilla genérica de poteto exige títulos en inglés, `origin/main`, un bucle horario y diez
recorridos por PR. Aquí prevalecen las instrucciones del usuario y `AGENTS.md`. Usar español, `master`,
alcance solicitado y comprobaciones proporcionales. Se conserva la secuencia de preparación,
dependencias, construcción, evidencia, revisión y entrega. No se agregan casillas ficticias para
hacer pasar su validador literal. Ejecutar el validador original y conservar su salida como
limitación de compatibilidad, junto con la revisión sustantiva del plan y `pre-commit`.
