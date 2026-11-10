# Fuentes de 30-decidir-b.html

Primera versión: código leído en `origin/master` (7cf7c9a) y producción consultada solo con GET el 8 de noviembre de 2026. Recaptura del 9 de noviembre (CLARIDAD 01 J.1): código leído en el árbol de trabajo (HEAD 1e2a66b, que incluye 4be5081, lo desplegado) y producción consultada solo con GET el 9 de noviembre (ver la sección «Recaptura 9-nov» al final). Donde una línea de abajo choca con esa sección, manda la sección.

## Dónde poner el dinero (hojas 1 y 2)
- Título, subtítulo «Solo cuenta lo que hoy está encendido… no coincide con el Resumen», enlaces Amazon MX/US: `app/templates/donde_poner_el_dinero.html:3-12`.
- Línea gris con la ventana por tipo y «ubicación y campaña, últimos 30 días»: `:13`. Ventana de 90 días hasta ayer UTC y 30 días para ubicación/campaña: `app/pantalla_dinero.py:501-509` (lee_dinero), `:553`.
- Columnas Tipo/Gasto/Parte del gasto/Pedidos/Venta/ACoS y «(meta X %)»: plantilla `:17-53` (`:33`, `:49`). El target viene del último ciclo terminado: `pantalla_dinero.py:507-509`.
- Tipos exact/phrase/broad/automatica/product_targeting (`pantalla_dinero.py:27`) se muestran como Exacta, Frase, Amplia, Automática, Por producto: plantilla `:24` (`etiqueta("tipo_campana")`), registro `app/etiquetas.py:405-411`. Se agrupan por keyword o target activo según el tipo de su campaña: `pantalla_dinero.py:49-65`.
- Nombres de ubicación (Arriba de búsqueda, Resto de búsqueda, Páginas de producto, Fuera de Amazon; `app/etiquetas.py:437-442`, plantilla `:62`) y columnas CPC/Conversión: plantilla `:54-77`. En Ajustes van en minúscula: plantilla `:96`. Frase «X: N MXN en 30 días, N clics, ningún pedido.» cuando el gasto llega al umbral sin pedidos: plantilla `:88-92`, `pantalla_dinero.py:202-205`.
- Columnas Por campaña: plantilla `:96-115`. Gasto medio solo con días con dato: `pantalla_dinero.py:394-400, 459-461`. Uso = medio/presupuesto: `:487`.
- Estrategias: plantilla `:66-71`. Ajustes «+N % en …»: `:114`.
- Avisos «se queda sin presupuesto» (90 % o más en 5 o más de 7 días) y «este presupuesto no limita» (presupuesto > 10 × gasto medio): `app/avisos_campana.py:84-111`.
- Aviso «Orbit movió el precio hace N días; espera al día 7 para juzgarlo.»: `avisos_campana.py:125-134`, cableado en `pantalla_dinero.py:158-163, 444-446`.
- Botones Cambiar presupuesto / Cambiar ajuste de ubicación / Limitar fuera de Amazon, Ver plan, Actor, Token, «Escribe APLICAR AJUSTE», Aplicar, Regresar: plantilla `:97-154`. Ver plan pide el plan sin escribir y Aplicar exige la frase exacta: `app/static/js/dinero.js:23-58`. Limitar fuera de Amazon pide el plan al abrir: `dinero.js:155-163`. «Ajuste aplicado.»: `dinero.js:97`.
- Frases del plan («Orbit cambia el presupuesto diario de 200.00 MXN a 250.00 MXN en campaña …», con dos decimales por `formato.dinero`): `app/campana_ajustes.py:155-165`; `app/formato.py:32-37`. Porcentaje 0 a 900: `:28, :178`.
- Regresar no existe para fuera de Amazon: `pantalla_dinero.py:135` (`a.clase <> 'fuera_de_amazon'`). Texto del botón «Regresar el presupuesto del AAAA-MM-DD»: `pantalla_dinero.py:152-155, 245-247`.
- Datos reales: ver «Recaptura 9-nov».

## Productos sin clics (hojas 3 a 6)
Plantilla en `git show origin/master:app/templates/productos_sin_clics.html` (la local está atrasada y no trae meta ni pausa).
- Secciones y textos: líneas 15-116 (Candidatos, Impulsos, Retiros abiertos, Sin un pedido), 118-200 (Meta por producto), 202-253 (Pausa por producto), 255-330 (Límites, Encendido).
- Criterio de candidato 2 piezas en 90 días y menos de 20 clics en 60: `origin/master:app/pantalla_productos.py:15-18`. Pedidos 90d suma piezas (`sum(quantity)`): mismo archivo, `_SQL_CANDIDATOS` (~línea 210). 330 días para Sin un pedido: `:19`.
- Subfamilia: ver «Recaptura 9-nov» (sugerencias, normalización, aviso de subfamilia nueva).
- Vista previa (SKU · subfamilia, tope, «al día», keywords, negativos, «Sale de N anuncios viejos.»): `productos.js:86-125`. Frase de confirmación `LANZAR IMPULSO DE {n} PRODUCTO{S si n≠1}`: `origin/master:app/impulso_io.py:442`. «Lanzados N.»: `productos.js:222`.
- Dos campañas (exact y automática), tope y plazo de 14 días: `origin/master:app/impulso.py:1-23`. Los anuncios viejos se retiran cuando el impulso ya tiene impresiones: `impulso_io.py:1034-1064`.
- Frases de Impulsos («Aún sin lectura.», «Día N de 14 · …», «Vende. Sigue encendido…», «Recibió clics y no vendió. Orbit lo pausó.»): `pantalla_productos.py:175-183, 373`.
- Retiros: «REGRESAR N ANUNCIOS», «Regresarlo a sus grupos de antes»: plantilla 48-55, 77-84. «Regresados N de M»: `productos.js:332`. Motivo traducido «Sin pedidos» / «Su subfamilia se retira»: plantilla `app/templates/productos_sin_clics.html:79` (`etiqueta("motivo_retiro")`), registro `app/etiquetas.py:430-435`. `sin_pedidos` lo mandan Sacar de las bolsas compartidas (`productos.js:437, 499`); `subfamilia` exige lote de Crear campañas: `app/api_fabrica.py:628-634`. Sin retiros abiertos en producción el 9-nov: el texto traducido no se vio en pantalla, sale del código.
- Sacar: «Nada que sacar: sin anuncios en bolsas viejas.» `productos.js:458`; `SACAR {n} ANUNCIOS` (api_fabrica); «Retirados N de M» `productos.js:517`.
- Meta: textos de la plantilla 118-200 (Margen, Meta, «sin meta», «ningún grupo exclusivo», Por qué, Mostrar, Target hoy / Target con meta, «cambia», «manda el goal»).
- Pausa: plantilla 202-253 (ventana de 30 días que terminó hace 30 días, Gasto observado / actuable, «N anuncios propuestos · N confirmados»). Encendido, Guardar límites, «Guarda tus límites primero», Volver a práctica: plantilla 255-330; textos de confirmación `productos.js:820-870` (48 horas, propuestas de práctica se descartan, lo ya pausado sigue pausado).
- Datos reales: ver «Recaptura 9-nov».

## Inertes (hoja 7)
- Plantilla `app/templates/inertes.html:4-123` (título «Keywords y productos sin visitas» `:4`, subtítulo con «Revivirlos lo decides tú desde esta pantalla» `:5-9`, KPIs Sin visitas / Antigüedad cumplida / En espera / Gasto de 90 días sin visitas `:11-14`, columnas `:22-31`, chip de Clasificación traducido `:43`, chip «en espera» `:45`, Resumen `:62-90`, Lotes de archivado `:91-123`). Sin botones ni bloque de scripts.
- 14 días sin impresiones desde el watermark y clasificación (con_ventas_previas si órdenes 90d > 0, gasto_sin_ventas si gasto > 0, si no peso_muerto): hoy `migrations/0082_claridad01_entidad_inerte.sql:46-77` (misma lógica que la 0013, en una sola pasada). En pantalla: «Vendió antes», «Gastó sin vender», «Sin gasto ni ventas» (`app/etiquetas.py:399-403`).
- en_espera y Archivable desde = primera vista + 30 días: `app/api_dashboard.py:1424-1436`.
- Archivar es manual por lotes, con go del dueño; Amazon no des-archiva y la reversa crea otra keyword pausada sin historia: `tools/archiva_inertes.py:1-40`.
- Datos reales: ver «Recaptura 9-nov».

## Crear campañas (hojas 8 a 10)
- Plantilla `origin/master:app/templates/fabrica.html` (igual a la local): aviso ACTIVAS / shadow / live 6-8; campos 15-54; Publicaciones 56-77; tabla de cinco campañas y etiquetas 78-108; Revisar plan 109; Comparar publicaciones 112-149; paso 2 153-157; Token y «Escribe CREAR 5 CAMPAÑAS», «Crear las cinco campañas», «Creando… no cierres esta página.» 159-176; Historial 179-185; Detalle y acciones PAUSAR/RECONCILIAR/REGISTRAR GRUPO 187-213; «Sacar estos productos de sus bolsas viejas» 214-228.
- Revisar plan y Usar pujas sugeridas no piden token (conexión de lectura); crear y acciones de lote sí: `origin/master:app/api_fabrica.py:303-357`.
- Botón Crear se activa solo con un plan revisado (`fabrica.js`). Estados del lote y del paso («Creado y registrado», «Interrumpido», «Pendiente de verificación», «Pausado», «Verificado»): hoy en el registro, `app/etiquetas.py:674-680` (estado_lote) y `:705` (estado_paso), usados en `app/static/js/fabrica.js:1049-1054`. Verificación de creación ENABLED → «Activa»: `fabrica.js:1054`, `app/etiquetas.py:350-354`. Retiro usa el Token de arriba (`fabrica.js`).
- Contenido de cada campaña (exact = términos que cumplen HARVEST; phrase/broad = biblioteca + términos con pedido; product targeting = ASIN; auto = negatives de la biblioteca): `origin/master:app/fabrica_plan.py:425-500`. Nombre «tipo | nombre | rol | fecha»: `fabrica_plan.py:413-418`. Tipos sugeridos salen de las bibliotecas de keywords y negatives: `origin/master:app/fabrica_web.py:63-64`.
- Datos reales: ver «Recaptura 9-nov».

## Ronda de correcciones (lectores novato y escéptico, 8-nov)
Código leído con `git show origin/master:<ruta>` (7cf7c9a). Producción solo con GET.

### Dónde poner el dinero (ahora 3 hojas: tabla, «por campaña», «botones»)
- Aviso «Orbit movió el precio…» solo sale tras un ajuste de ubicación aplicado desde esta pantalla, días 1 a 7: `app/avisos_campana.py:123-134` (`if clase != "ajuste_ubicacion": return None`); fecha del último ajuste de ubicación confirmado: `app/pantalla_dinero.py:144-151` (`_SQL_ULTIMO_AJUSTE_PRECIO`, tabla de ajustes de esta pantalla).
- Columna «Fuera de Amazon» = gasto fuera de Amazon de la campaña en la ventana de 30 días: plantilla `app/templates/donde_poner_el_dinero.html:103,114`; `app/pantalla_dinero.py:261, 492`.
- Ajustes «+N % en …» = porcentaje extra de puja por ubicación: plantilla `:113`; campo del plan `app/campana_ajustes.py:183-190`.
- Botones y lo que piden: Cambiar presupuesto pide «Presupuesto diario»; ajuste de ubicación pide Ubicación (3 opciones) y Porcentaje (0 a 900, `campana_ajustes.py:27`); Limitar fuera de Amazon no pide cantidad: plantilla `:120-145`.
- Limitar fuera de Amazon fija `offAmazonBudgetControlStrategy = MINIMIZE_SPEND` (gastar lo mínimo fuera de Amazon), sin monto; si ya está limitado, el plan dice «el ajuste no cambia nada»: `app/campana_ajustes.py:28, 121-126, 192-198`. Sin Regresar: `pantalla_dinero.py:135` (`a.clase <> 'fuera_de_amazon'`).
- Ver plan no manda Token (GET del plan); Aplicar exige plan previo y «APLICAR AJUSTE»: `app/static/js/dinero.js:23-58`.
- «Orbit no cambia presupuestos en su revisión diaria»: inferido; `git grep budget origin/master -- app/cycle.py app/apply.py app/optimizer/` no da resultados. Los únicos escritores de presupuesto en app/ son esta pantalla, Crear campañas e impulsos.
- «Cuando el presupuesto se acaba, Amazon deja de mostrar sus anuncios hasta el día siguiente»: regla de Amazon Ads, no de Orbit (no está en el repo).
- Recomendación «abajo del target es candidata a más presupuesto»: criterio de lectura de la guía, no una regla de Orbit.

### Productos sin clics
- Subfamilia de Candidatos e Impulsar aparte = tipo de producto. Se pasa como tipo a la fábrica y elige la biblioteca de keywords/negativos: `app/impulso_io.py` → `tools/fabrica_campanas.py` (`_biblioteca(conn_read, tipo, …)`). Normalización y aviso: ver «Recaptura 9-nov».
- Motivo de retiro «subfamilia» lo pone el botón Sacar de Crear campañas: `app/static/js/fabrica.js` (`pedirRetiro`, `motivo: "subfamilia"`), `app/api_fabrica.py:582-588`.
- Bolsa compartida / grupo exclusivo: plantilla `productos_sin_clics.html:111, 124-125, 190, 339`.
- Halo: `app/optimizer/productos.py:15-17, 120-122` («ventas atribuidas de otro SKU: su margen no es el del producto»; «el tramo del halo de menos de 30 dias todavia puede cambiar»).
- Meta por producto la pisa un goal con target: cascada `app/optimizer/goals.py:17-35` (el target del goal resuelto va antes que margen_producto; un goal de campaña con target vacío NO deja entrar el target de plataforma). Texto de pantalla: plantilla `:125` («Un goal de campaña con target manda sobre ella»). La guía dice «un Target ACoS escrito en un goal» porque, sin goal de campaña, también lo pisa un goal de plataforma con target.
- Ancla `productos-sin-clics-pausa` en la hoja de la pausa.
- La sección Pausa solo lee el último ciclo terminado: `app/pantalla_decisiones_producto.py:53-58` (`ORDER BY id DESC LIMIT 1`). Anuncio con pausa ya en cola se excluye: `app/productos_io.py:445` (`clave_en_vuelo`), texto `app/optimizer/productos.py:141`. Frase «N anuncios propuestos · N confirmados»: plantilla `:219`.
- Datos: `GET /api/dashboard/productos-sin-clics?plataforma=amazon_us` → cycle_id 176, propuestas 0, `anuncios_excluidos.clave_en_vuelo = 31`, con_propuestas 0 de 119. `GET /api/dashboard/cortes` → 31 filas `pause` + 1 `negative`, todas `pending_veto`, amazon_us.

### Inertes
- (Borrado el 9-nov: «hasta 4 minutos». La pantalla ya abre en segundos.)

### Crear campañas (ahora 4 hojas; nueva «el lote hoy», ancla `fabrica-modo`)
- Lote: `GET /api/fabrica/lotes?plataforma=amazon_mx` → `plan.modo = "shadow"` (en pantalla «Práctica», `app/etiquetas.py:324`, `fabrica.js:275`), kit_arras / Personalizado, creado 2026-09-09T03:35 UTC = «8 sep, 21:35» en hora del centro.
- Goals #8 a #12 («kit_arras | Personalizado | … | 2026-09-09») con `mode: live`, `target: null`, `enabled: true`: `GET /api/dashboard/settings` (9-nov). Settings los muestra como «Meta #8 · Amazon MX» y «Modo (solo lectura): En vivo»: `app/templates/settings.html:167, 171`; `GET /settings` (9-nov).
- Los goals nacen con el modo del plan y con target solo si el objetivo es manual: `tools/fabrica_campanas.py:1240, 1304, 1315`; `app/fabrica_plan.py:252-258` (`target_para_goals`: manual → target; derivado → NULL, «el goal resuelve por margen_plataforma cada ciclo»).
- Modo efectivo = el menor entre el modo global de publicidad y el del goal: `app/optimizer/goals.py:522-532` (`modo_efectivo`); modo global = `ads_optimizer_mode`: `goals.py:118, 477-480`. El 9-nov «Modo global (solo lectura): En vivo» (`GET /settings`; `app/templates/settings.html:14`).
- Meta de hoy del lote: Amazon MX «Meta vigente: 20.02 % · sale de: Margen de la plataforma» (`GET /settings`, 9-nov; procedencia traducida en `app/etiquetas.py:265-272`).
- Detalle del lote: ficha Plataforma/Estado/Detalle/Creado/Finalizado con horas por `fechaHora`, tabla de pasos y «Ver plan persistido del lote» con Grupo, Tipo, Modo del optimizador, Target ACoS aplicado: `app/static/js/fabrica.js:263-280, 1046-1065`. Ver «Recaptura 9-nov» para lo visto en navegador.
- Acciones grises: fieldset `disabled` en la plantilla (`app/templates/fabrica.html:197`) y se activa al terminar de pintar el detalle (`fabrica.js:197, 1052, 1098`; `mutando` lo vuelve a apagar durante una acción). En producción, tras cargar, `#fabrica-recuperacion.disabled = false`.
- Botones de solo lectura: Actualizar productos, Actualizar comparación, Actualizar historial, Consultar estado del lote (`fabrica.html:65, 145, 182, 192`; todos GET en `fabrica.js`).
- «… de 342 seleccionables · 249 productos en el catálogo» = publicaciones elegibles de 249 productos: `fabrica.js:92-95, 886`; `GET /api/fabrica/catalogo?plataforma=amazon_mx` (9-nov) → 249 productos, 342 elegibles, `tipos_producto` ["kit_arras"]; amazon_us → 119 productos, 176 elegibles, `tipos_producto` [].
- «El objetivo manual no acredita rentabilidad» / «Sin ACoS manual, target: margen de la plataforma (se ajusta cada ciclo)»: `fabrica.html:44-47`.

## Recaptura 9-nov (CLARIDAD 01 J.1)
Código en el árbol de trabajo (HEAD 1e2a66b). Producción solo con GET el 9 de noviembre de 2026 (CDMX). No se pulsó nada que escriba ni que consulte Amazon. Para Crear campañas se abrió `/campanas/nuevas?lote=web-923cb…` en agent-browser: al abrir, la página hace solo `GET /api/fabrica/lotes/<lote>` (`app/static/js/fabrica.js:1071-1079`, `app/api_fabrica.py:381-384`, conexión de lectura). Se abrió el `<details>` «Ver plan persistido del lote» desde el DOM (no hace petición).

### Formato y textos comunes
- Dinero con dos decimales y miles, porcentajes con dos decimales: `app/formato.py:32-57`. Horas «8 sep, 21:35» en hora del centro: `app/formato.py:77-84`; leyenda «Horas del centro de México»: `app/formato.py:17`, presente en las cuatro pantallas (`<span class="leyenda-zona">`).

### Dónde poner el dinero (hojas 1 a 3)
- `GET /donde-poner-el-dinero?plataforma=amazon_mx`: «Ventana por tipo 2026-08-11 a 2026-11-09 (MXN)»; Exacta 22,078.54 / 40.30 % / ACoS 10.60 % (meta 20.00 %); Total 54,837.77, ACoS 13.70 % (meta 20.00 %); Fuera de Amazon 3,444.05, 15.20 %, 3 pedidos, ACoS 174.70 %; AC - Category Exact - MX 300.00 / 298.56 / 99.50 % con «se queda sin presupuesto. Usó 90 % o más en 5 de los últimos 7 días.»; Arras Auto 2 293.94 / 2.59 con «este presupuesto no limita. 294 MXN al día y gasta 3.»; Ajustes «+9 % en arriba de búsqueda; …». Las cinco campañas «kit_arras | Personalizado | … | 2026-09-09» siguen en la tabla Por campaña.
- `?plataforma=amazon_us`: meta 19.20 %, mismas etiquetas de tipo.
- Texto del aviso de presupuesto: `app/avisos_campana.py:84-111`.

### Productos sin clics (hojas 4 a 7)
- Sugerencias: `<datalist id="subfamilias-conocidas">` con las subfamilias conocidas del mercado y texto gris «ej. <primera>» o «ej. kit_arras» si no hay: `app/templates/productos_sin_clics.html:15-20, 36, 112`. Conocidas = con keywords, negativos o grupo: `app/fabrica_web.py:71-77` (`tipos_producto`), pasadas por `app/api_dashboard.py:1856-1862`. Producción: MX datalist con `kit_arras` y 107 casillas con `placeholder="ej. kit_arras"`; US datalist vacío y 39 casillas con el mismo texto gris.
- Normalización: `Subfamilia = Annotated[str, BeforeValidator(familias.slug), Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$")]` en `app/api_fabrica.py:405-411` (comentario: «Arras para boda» → arras_para_boda). `familias.slug`: minúsculas, sin acentos, lo que no es letra o número se vuelve `_`: `app/familias.py:53-63`.
- Aviso de subfamilia nueva: `subfamilia_nueva = subfamilia not in tipos_producto(...)` y `sin_negativos`: `app/api_fabrica.py:651-660`; textos «Subfamilia nueva: arranca sin negativos; Orbit los aprende con este impulso.» y «Esta subfamilia todavía no tiene negativos: …»: `app/static/js/productos.js:90-108`. Línea «SKU · subfamilia» de la vista: `productos.js:92`. En US `tipos_producto` = [] (`GET /api/dashboard/productos-sin-clics?plataforma=amazon_us` → `subfamilias: []`), así que toda subfamilia sale como nueva.
- Casilla vacía: un producto marcado sin subfamilia va a `faltan` y no aporta publicaciones (`productos.js:29-55`); si hay otras con subfamilia sale «Falta la subfamilia de N marcados.», y si era el único marcado sale «Marca al menos un producto.» (`productos.js:157-163`). En ningún caso se pide la vista. Por eso la hoja dice solo «no prepara nada».
- Origen en gris junto a Target hoy traducido (`etiqueta("procedencia")`): plantilla `:183`; producción MX: 53 celdas «Margen de la plataforma».
- Datos: `GET /productos-sin-clics?plataforma=amazon_mx` → 20 candidatos, SET - Carte Azul 22mm Plateado / SET-CAR-AZU-SAN-PLA con 7 y 4; Impulsos 0; Retiros abiertos 0; Sin un pedido en 11 meses 87 (NH-ARR-COF-22-CEN-DOR primero); «1 de 249 con meta», «Ciclo 179 · 9 nov, 02:41 · la meta está en vivo»; NH - PERS Carte Azul Dorado margen 41.28 %, meta 20.64 %, «ningún grupo exclusivo»; Pausa «0 de 249 con propuesta», «la pausa está en práctica», «0 anuncios propuestos · 0 confirmados»; «Vigentes: 100 clics y 500.00 MXN de gasto (por omisión, todavía sin guardar)»; «Guarda tus límites primero». `?plataforma=amazon_us` → 5 candidatos, 0 impulsos, 0 retiros, 34 sin pedido, «0 de 119 con meta», ciclo 178, «0 de 119 con propuesta», «0 anuncios propuestos · 0 confirmados», 100 clics y 40.00 USD.
- Propuestas: `GET /api/dashboard/cortes` → 32 items amazon_us: 31 `pause` «Pausar anuncio del producto» con `modo: shadow` encoladas el 2026-11-08 y 1 «Apagar palabra» `live` del 2026-11-09. `GET /api/dashboard/productos-sin-clics?plataforma=amazon_us` → `anuncios_excluidos.clave_en_vuelo = 31`.

### Inertes (hoja 8)
- `GET /inertes` respondió en 1.3 s (antes 234 s). Título «Keywords y productos sin visitas»; KPIs Sin visitas 363, Antigüedad cumplida 360, En espera 3 («0 sin dato de antigüedad»), Gasto de 90 días sin visitas 1,160.41 MXN y 8.85 USD. Primera fila: Amazon MX, AGMX - Category Phrase - MX, «arras boda», Vendió antes, 27 días, 168.55 MXN, 1 orden. Tercera fila «mismo ASIN B086TVLJ43» (Keyword o producto de un target). Resumen MX 3 / 15 / 203 y US 1 / 10 / 131. «Sin lotes de archivado todavía.»

### Crear campañas (hojas 9 a 12)
- Aviso de arriba y modos «Práctica: observar ajustes» / «En vivo: aplicar ajustes»: `app/templates/fabrica.html:6-8, 35-36`. Ayuda de Tipo de producto «Escribe un tipo o elige una sugerencia. Usa minúsculas, números y guion bajo.»: `fabrica.html:21-27`.
- Tabla del paso 1 con «Categoría · exacta», «Categoría · frase», «Categoría · amplia», «Segmentación por producto», «Descubrimiento automático»: `fabrica.html:88-92`. En la revisión del plan, el plan persistido y el detalle del lote se nombran con `rol_campana` («Exacta de la categoría», «Frase de la categoría», «Amplia de la categoría», «Por producto», «Automática»): `fabrica.js:14, 333, 339, 1052`; `app/etiquetas.py:413-419`. «Negativos» como título de semillas: `fabrica.js:295`.
- Navegador, `/campanas/nuevas?lote=web-923cb…`: historial «web-923cb… · Creado y registrado · 8 sep, 21:35»; «Estado actualizado.»; Plataforma Amazon MX, Estado Creado y registrado, Detalle Sin dato, Creado 8 sep, 21:35, Finalizado 8 sep, 21:37; pasos «Exacta de la categoría · Campaña · … · Verificado · Activa»; plan persistido: Modo del optimizador Práctica, Target ACoS aplicado 21.08 %, Origen del objetivo Margen medido, Procedencia del target «Margen más bajo del grupo: 0.5 x 42.16 = 21.08 → redondeado = 21.08», presupuestos 20.00 MXN por rol; «0 publicaciones seleccionadas de 342 seleccionables · 249 productos en el catálogo».
- `GET /api/fabrica/lotes/<lote>` → 87 pasos, todos `applied` con `readback_estado` ENABLED. `GET /api/fabrica/lotes?plataforma=amazon_us` → 0 lotes.
- `GET /campanas`: debajo de «kit_arras | Personalizado | category_exact | 2026-09-09» dice «Meta por campaña · En vivo» (`app/templates/campanas.html:132`).

## J.2, correcciones del lector fresco (`docs/evidencia/claridad-01/J/lector.md`)

- Dónde poner el dinero redondeaba meta y porcentajes a una décima (`app/pantalla_dinero.py`, `como_dict` y `_pct`). Desde h_26f91a68 los entrega sin redondear y solo los redondea `formato.pct` a dos decimales: «(meta 20.02 %)», como Settings (`app/api_dashboard.py:542`, `_target_margen_del_ciclo`). Cifras de la hoja 22 y 23 recalculadas con gasto y venta de `GET /api/dashboard/donde-poner-el-dinero?plataforma=amazon_mx` del 9-nov: Total 54,837.77 / 401,733.72 = 13.65 %; Exacta 22,078.54 / 208,609.47 = 10.58 % y 40.26 % del gasto; Fuera de Amazon 3,444.05 / 1,971.56 = 174.69 %; AC - Category Exact - MX 298.56 / 300.00 = 99.52 %.
- La línea gris de ventanas sigue el día de la base: a las 22:04 del 9-nov (hora del centro) decía «Ventana por tipo 2026-08-11 a 2026-11-09» (prod `/donde-poner-el-dinero?plataforma=amazon_mx`).
- Ancla `productos-sin-clics-meta` en la hoja «no venden y meta», para que Settings apunte a la explicación de la meta por producto.
