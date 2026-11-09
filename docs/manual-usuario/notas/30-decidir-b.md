# Fuentes de 30-decidir-b.html

Código leído en `origin/master` (7cf7c9a), que es lo que corre en producción. Para Dónde poner el dinero e Inertes, la rama local y origin/master son iguales (`git diff HEAD origin/master` no muestra cambios en esos archivos). Producción se consultó solo con GET el 8 de noviembre de 2026.

## Dónde poner el dinero (hojas 1 y 2)
- Título, subtítulo «Solo cuenta lo que hoy está encendido… no coincide con el Resumen», enlaces Amazon MX/US: `app/templates/donde_poner_el_dinero.html:3-12`.
- Línea gris con la ventana por tipo y «ubicación y campaña, últimos 30 días»: `:13`. Ventana de 90 días hasta ayer UTC y 30 días para ubicación/campaña: `app/pantalla_dinero.py:501-509` (lee_dinero), `:553`.
- Columnas Tipo/Gasto/Parte del gasto/Pedidos/Venta/ACoS y «(target X %)»: plantilla `:17-53`. El target viene del último ciclo terminado: `pantalla_dinero.py:507-509`.
- Tipos exact/phrase/broad/automatica/product_targeting: `pantalla_dinero.py:27`. Se agrupan por keyword o target activo según el tipo de su campaña: `pantalla_dinero.py:49-65`.
- Nombres de ubicación y columnas CPC/Conversión: plantilla `:54-95`. Frase «X: N MXN en 30 días, N clics, ningún pedido.» cuando el gasto llega al umbral sin pedidos: plantilla `:88-92`, `pantalla_dinero.py:202-205`.
- Columnas Por campaña: plantilla `:96-115`. Gasto medio solo con días con dato: `pantalla_dinero.py:394-400, 459-461`. Uso = medio/presupuesto: `:487`.
- Estrategias: plantilla `:66-71`. Ajustes «+N % en …»: `:114`.
- Avisos «se queda sin presupuesto» (90 % o más en 5 o más de 7 días) y «este presupuesto no limita» (presupuesto > 10 × gasto medio): `app/avisos_campana.py:84-111`.
- Aviso «Orbit movió el precio hace N días; espera al día 7 para juzgarlo.»: `avisos_campana.py:125-134`, cableado en `pantalla_dinero.py:158-163, 444-446`.
- Botones Cambiar presupuesto / Cambiar ajuste de ubicación / Limitar fuera de Amazon, Ver plan, Actor, Token, «Escribe APLICAR AJUSTE», Aplicar, Regresar: plantilla `:97-154`. Ver plan pide el plan sin escribir y Aplicar exige la frase exacta: `app/static/js/dinero.js:23-58`. Limitar fuera de Amazon pide el plan al abrir: `dinero.js:155-163`. «Ajuste aplicado.»: `dinero.js:97`.
- Frases del plan («Orbit cambia el presupuesto diario de … a … en campaña …»): `app/campana_ajustes.py:161-190`. Porcentaje 0 a 900: `:28, :178`.
- Regresar no existe para fuera de Amazon: `pantalla_dinero.py:135` (`a.clase <> 'fuera_de_amazon'`). Texto del botón «Regresar el presupuesto del AAAA-MM-DD»: `pantalla_dinero.py:152-155, 245-247`.
- Datos reales: `http://10.13.13.1:8010/donde-poner-el-dinero?plataforma=amazon_mx` (Total 54484.67 MXN, ACoS 13.8 %, target 20.0 %, exact 40.5 % con ACoS 10.6 %, fuera de Amazon 3611.23 MXN, 3 pedidos, ACoS 157.6 %, AC - Category Exact - MX 300.00 / 300.53 / 100.2 % con aviso de 6 de 7 días, Arras Auto 2 293.94 / 2.47). Ventana 2026-08-09 a 2026-11-07.

## Productos sin clics (hojas 3 a 6)
Plantilla en `git show origin/master:app/templates/productos_sin_clics.html` (la local está atrasada y no trae meta ni pausa).
- Secciones y textos: líneas 15-116 (Candidatos, Impulsos, Retiros abiertos, Sin un pedido), 118-200 (Meta por producto), 202-253 (Pausa por producto), 255-330 (Límites, Encendido).
- Criterio de candidato 2 piezas en 90 días y menos de 20 clics en 60: `origin/master:app/pantalla_productos.py:15-18`. Pedidos 90d suma piezas (`sum(quantity)`): mismo archivo, `_SQL_CANDIDATOS` (~línea 210). 330 días para Sin un pedido: `:19`.
- Subfamilia con `placeholder="aros"` (texto gris, casilla vacía): plantilla líneas 29 y 105. En producción aparece 107 veces (21 + 86 casillas) y ninguna con `value`. Sin subfamilia, «Falta la subfamilia de N marcados.» y no se pide la vista: `origin/master:app/static/js/productos.js:35-39, 139`.
- Vista previa (tope, «al día», keywords, negatives, «Sale de N anuncios viejos.»): `productos.js:86-125`. Frase de confirmación `LANZAR IMPULSO DE {n} PRODUCTO{S si n≠1}`: `origin/master:app/impulso_io.py:442`. «Lanzados N.»: `productos.js:222`.
- Dos campañas (exact y automática), tope y plazo de 14 días: `origin/master:app/impulso.py:1-23`. Los anuncios viejos se retiran cuando el impulso ya tiene impresiones: `impulso_io.py:1034-1064`.
- Frases de Impulsos («Aún sin lectura.», «Día N de 14 · …», «Vende. Sigue encendido…», «Recibió clics y no vendió. Orbit lo pausó.»): `pantalla_productos.py:175-183, 373`.
- Retiros: «REGRESAR N ANUNCIOS», «Regresarlo a sus grupos de antes»: plantilla 48-55, 77-84. «Regresados N de M»: `productos.js:330`. Motivos sin_pedidos/subfamilia: plantilla 65; `subfamilia` exige lote de Crear campañas: `origin/master:app/api_fabrica.py:582-588`.
- Sacar: «Nada que sacar: sin anuncios en bolsas viejas.» `productos.js:456`; `SACAR {n} ANUNCIOS` `api_fabrica.py:717`; «Retirados N de M» `productos.js:515`.
- Meta: textos de la plantilla 118-200 (Margen, Meta, «sin meta», «ningún grupo exclusivo», Por qué, Mostrar, Target hoy / Target con meta, «cambia», «manda el goal»).
- Pausa: plantilla 202-253 (ventana de 30 días que terminó hace 30 días, Gasto observado / actuable, «N anuncios propuestos · N confirmados»). Encendido, Guardar límites, «Guarda tus límites primero», Volver a práctica: plantilla 255-330; textos de confirmación `productos.js:820-870` (48 horas, propuestas de práctica se descartan, lo ya pausado sigue pausado).
- Datos reales: `/productos-sin-clics?plataforma=amazon_mx` y `?plataforma=amazon_us` (21 y 6 candidatos; SET-CAR-AZU-SAN-PLA con 7 y 4; 0 impulsos; 0 retiros; 86 y 34 sin pedido; NH-ARR-COF-22-CEN-DOR; «1 de 249 con meta», NH-PERS-CAR-AZU-CEN-DOR margen 41.27 % meta 20.64 %, ningún grupo exclusivo; meta «en vivo» ciclo 177; pausa en práctica con 0 de 249 y 0 de 119; límites 100 clics / 500.00 MXN y 100 clics / 40.00 USD «por omisión, todavía sin guardar»; Encender deshabilitado en los dos).

## Inertes (hoja 7)
- Plantilla `app/templates/inertes.html:4-123` (título «Entidades sin trafico», KPIs, columnas, chip «en espera», Resumen, Lotes de archivado). Sin botones ni JS.
- 14 días sin impresiones desde el watermark y clasificación (con_ventas_previas si órdenes 90d > 0, gasto_sin_ventas si gasto > 0, si no peso_muerto): `migrations/0013_entidad_inerte.sql:36-79` (única definición de la vista en origin/master).
- en_espera y Archivable desde = primera vista + 30 días: `app/api_dashboard.py:1424-1436`.
- Archivar es manual por lotes, con go del dueño; Amazon no des-archiva y la reversa crea otra keyword pausada sin historia: `tools/archiva_inertes.py:1-40`.
- Datos reales: `http://10.13.13.1:8010/inertes` (tardó 234 s en responder). 363 hojas, 360 antigüedad cumplida, 3 en espera, 1165.35 MXN y 5.21 USD; MX 3/16/203, US 1/8/132; primera fila «arras boda», AGMX - Category Phrase - MX, 26 días, 168.5500 MXN, 1 orden; «sin lotes de archivado todavia.»

## Crear campañas (hojas 8 a 10)
- Plantilla `origin/master:app/templates/fabrica.html` (igual a la local): aviso ACTIVAS / shadow / live 6-8; campos 15-54; Publicaciones 56-77; tabla de cinco campañas y etiquetas 78-108; Revisar plan 109; Comparar publicaciones 112-149; paso 2 153-157; Token y «Escribe CREAR 5 CAMPAÑAS», «Crear las cinco campañas», «Creando… no cierres esta página.» 159-176; Historial 179-185; Detalle y acciones PAUSAR/RECONCILIAR/REGISTRAR GRUPO 187-213; «Sacar estos productos de sus bolsas viejas» 214-228.
- Revisar plan y Usar pujas sugeridas no piden token (conexión de lectura); crear y acciones de lote sí: `origin/master:app/api_fabrica.py:303-357`.
- Botón Crear se activa solo con un plan revisado: `origin/master:app/static/js/fabrica.js:196`. Estados del lote y del paso («Creado y registrado», «Interrumpido», «Pendiente de verificación», «Pausado», «Verificado»): `fabrica.js:16-20`. Columnas del detalle: `fabrica.js:1083-1086`. Retiro usa el Token de arriba: `fabrica.js:1280`.
- Contenido de cada campaña (exact = términos que cumplen HARVEST; phrase/broad = biblioteca + términos con pedido; product targeting = ASIN; auto = negatives de la biblioteca): `origin/master:app/fabrica_plan.py:425-500`. Nombre «tipo | nombre | rol | fecha»: `fabrica_plan.py:413-418`. Tipos sugeridos salen de las bibliotecas de keywords y negatives: `origin/master:app/fabrica_web.py:63-64`.
- Datos reales: `GET /api/fabrica/lotes?plataforma=amazon_mx` (1 lote, 2026-09-09, kit_arras / Personalizado, shadow, 20 MXN por campaña, objetivo derivado 21.08 % = 0.5 × 42.157 %), `?plataforma=amazon_us` (0 lotes), `GET /api/fabrica/lotes/<lote>` (87 pasos, todos applied con readback ENABLED), `GET /api/fabrica/catalogo?plataforma=amazon_mx` (249 productos, tipos ["kit_arras"]). Las cinco campañas «kit_arras | Personalizado | … | 2026-09-09» aparecen en `/donde-poner-el-dinero?plataforma=amazon_mx`.

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
- Subfamilia de Candidatos e Impulsar aparte = tipo de producto con patrón `^[a-z0-9_]+$`: `app/api_fabrica.py:366, 384-387` (`Subfamilia = Annotated[str, Field(..., pattern=r"^[a-z0-9_]+$")]`). Se pasa como tipo a la fábrica y elige la biblioteca de keywords/negatives: `app/impulso_io.py:333` → `tools/fabrica_campanas.py:635-680` (`_biblioteca(conn_read, tipo, …)`). El texto anterior («arras para boda», con espacios) lo rechazaría la validación.
- Motivo de retiro «subfamilia» lo pone el botón Sacar de Crear campañas: `app/static/js/fabrica.js` (`pedirRetiro`, `motivo: "subfamilia"`), `app/api_fabrica.py:582-588`.
- Bolsa compartida / grupo exclusivo: plantilla `productos_sin_clics.html:111, 124-125, 190, 339`.
- Halo: `app/optimizer/productos.py:15-17, 120-122` («ventas atribuidas de otro SKU: su margen no es el del producto»; «el tramo del halo de menos de 30 dias todavia puede cambiar»).
- Meta por producto la pisa un goal con target: cascada `app/optimizer/goals.py:17-35` (el target del goal resuelto va antes que margen_producto; un goal de campaña con target vacío NO deja entrar el target de plataforma). Texto de pantalla: plantilla `:125` («Un goal de campaña con target manda sobre ella»). La guía dice «un Target ACoS escrito en un goal» porque, sin goal de campaña, también lo pisa un goal de plataforma con target.
- Ancla `productos-sin-clics-pausa` en la hoja de la pausa.
- La sección Pausa solo lee el último ciclo terminado: `app/pantalla_decisiones_producto.py:53-58` (`ORDER BY id DESC LIMIT 1`). Anuncio con pausa ya en cola se excluye: `app/productos_io.py:445` (`clave_en_vuelo`), texto `app/optimizer/productos.py:141`. Frase «N anuncios propuestos · N confirmados»: plantilla `:219`.
- Datos: `GET /api/dashboard/productos-sin-clics?plataforma=amazon_us` → cycle_id 176, propuestas 0, `anuncios_excluidos.clave_en_vuelo = 31`, con_propuestas 0 de 119. `GET /api/dashboard/cortes` → 31 filas `pause` + 1 `negative`, todas `pending_veto`, amazon_us.

### Inertes
- «hasta 4 minutos»: la consulta del 8-nov tardó 234 s (ver arriba).

### Crear campañas (ahora 4 hojas; nueva «el lote hoy», ancla `fabrica-modo`)
- Lote: `GET /api/fabrica/lotes?plataforma=amazon_mx` → `plan.modo = "shadow"`, kit_arras / Personalizado, creado 2026-09-09T03:35 UTC.
- Goals #8 a #12 («kit_arras | Personalizado | … | 2026-09-09») con `mode: live`, `target: null`, `enabled: true`: `GET /api/dashboard/settings`. Settings los muestra como «mode (solo lectura)»: `app/templates/settings.html:134, 169`.
- Los goals nacen con el modo del plan y con target solo si el objetivo es manual: `tools/fabrica_campanas.py:1240, 1304, 1315`; `app/fabrica_plan.py:252-258` (`target_para_goals`: manual → target; derivado → NULL, «el goal resuelve por margen_plataforma cada ciclo»).
- Modo efectivo = el menor entre el modo global de publicidad y el del goal: `app/optimizer/goals.py:522-532` (`modo_efectivo`); modo global = `ads_optimizer_mode`: `goals.py:118, 477-480`; `app/api_dashboard.py:1906`. Hoy «Modo global (solo lectura): live» (`GET /settings`).
- Meta de hoy del lote = margen_plataforma 20.02 (`GET /settings`, Target vigente amazon_mx).
- Detalle del lote: ficha Plataforma/Estado/Detalle/Creado/Finalizado, tabla de pasos y «Ver plan persistido del lote» con Grupo, Tipo, Modo del optimizador, Target ACoS aplicado: `app/static/js/fabrica.js:297-330, 1064-1100`. Comprobado en navegador (solo abrir, sin pulsar nada que escriba): `/campanas/nuevas?lote=web-923cb…` muestra «Estado actualizado.», Detalle «Sin dato», 87 pasos Verificado/ENABLED, plan persistido con Modo del optimizador shadow y Target ACoS aplicado 21.08 %.
- Acciones grises: fieldset `disabled` en la plantilla (`app/templates/fabrica.html:197`) y se activa al terminar de pintar el detalle (`fabrica.js:197, 1052, 1098`; `mutando` lo vuelve a apagar durante una acción). En producción, tras cargar, `#fabrica-recuperacion.disabled = false`.
- Botones de solo lectura: Actualizar productos, Actualizar comparación, Actualizar historial, Consultar estado del lote (`fabrica.html:65, 145, 182, 192`; todos GET en `fabrica.js`).
- «342 seleccionables · 249 productos» = publicaciones elegibles de 249 productos: `fabrica.js:144-145, 918`; `GET /api/fabrica/catalogo?plataforma=amazon_mx` → 249 productos, 342 elegibles, tipos ["kit_arras"]; amazon_us → 119 productos, tipos [].
- «El objetivo manual no acredita rentabilidad» / «Sin ACoS manual, target: margen de la plataforma (se ajusta cada ciclo)»: `fabrica.html:44-47`.
