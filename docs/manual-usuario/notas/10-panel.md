# Fuentes · 10-panel (Resumen, Campañas, Decisiones, Salud, Contribución)

Producción va adelante de la rama local `resumen-ads-04-diseno`. Las fuentes de código son de
`origin/master` (7cf7c9a), que coincide con lo que sirve producción. Producción se consultó solo con GET
el 8-nov-2026 (`http://10.13.13.1:8010/...`). Copias en el scratchpad `panel/`.

## Resumen (`/`)
- Filtros Canal (Todos / Amazon MX / Amazon US / Mercado Libre), Periodo 7/30/90, línea Ventana, «el dia en curso queda fuera», Rango personalizado con Desde/Hasta/Filtrar/Limpiar, aviso «Hacen falta las dos fechas»: `app/templates/resumen.html:41-101`. Limpiar aparece si hay canal, fechas o periodo distinto de 30 (l. 95-97) y lleva a `/`.
- Cinco secciones numeradas: `resumen.html:104-465`.
- Bien / Atención / Sin dato: `app/ui_metricas.py:199-221` (con meta, ACoS ≤ meta = Bien; sin meta, ventas sube y ACoS baja = Bien; sin cost ni revenue = Sin dato).
- «Meta ACoS: sin configurar» y enlace Configurar a `/settings`: `resumen.html:132-135`.
- Cifras inmaduras y asterisco en serie diaria: `resumen.html:172-176`, `:443`.
- Línea fina = día, gruesa = media 7 días, hueco = dato desconocido: `resumen.html:228`; `app/static/js/dashboard.js:186-200` (grosor 2.5 vs 1.2).
- Totales por moneda sin mezclar divisas: `resumen.html:266-312`.
- Datos reales: `GET /` y `GET /api/dashboard/resumen`. Ventana 2026-10-09..2026-11-07. MX Bien, ACoS 13.50 % (antes 14.33 %), ventas $172,354.68, gasto $23,270.77, ROAS 7.41×. US Atención, ACoS 43.36 %, «12 de 30 dias tuvieron gasto y $0.00 en ventas atribuidas ($620.53 USD…)». Inmaduros 2026-10-31..2026-11-07 (8 días).
- Captura `img/resumen.png` (agent-browser, solo abrir).

## Campañas (`/campanas`)
- Plantilla: `git show origin/master:app/templates/campanas.html`. Franja de inmaduros l. 9. Tarjetas «Campañas contra su target» l. 11-43. Pestañas l. 45-53. Filtros Plataforma/Estado/Campaña/Procedencia, Filtros por métricas, Filtrar/Limpiar l. 55-107. Columnas en orden l. 111-123. Nota de estados l. 163.
- Clasificación En target / Sobre target (+N pp) / Gasto sin venta / Sin target / Sin actividad: `origin/master:app/ui_campanas.py` `estado_de` (aprox. l. 85-110).
- Orden por omisión = orden del API (US luego MX por `PLATAFORMAS_MONEDA`, y dentro por id): `origin/master:app/api_dashboard.py:181-193` (`ORDER BY e.id`) y `:832-866`. Sin `ordenar` no se reordena: `origin/master:app/ui.py:352-366`. Primer clic desc en numéricas, asc en texto; segundo invierte; ausentes al final: `app/ui.py:390-399` (rama) y `:360-366`.
- Target efectivo = lo que congeló el último ciclo live+done para las hojas; rango si difieren; `mixto` si varias procedencias; null = «sin target»: `origin/master:app/api_dashboard.py:732-752` (`display`), `:772-790`, docstring `:832-846`.
- Peldaños y su orden: `origin/master:app/optimizer/goals.py:286-293` (`PELDANOS_CASCADA`), `:300-320` (núcleo, primero que tenga valor), `:20` (margen_producto solo en grupo exclusivo), banda [10, 45] del margen de plataforma `:959`.
- Línea scope · mode · floor · ceiling / «sin goal»: `campanas.html` (master) l. 128-133.
- Datos reales: `GET /campanas`, `GET /api/dashboard/campanas`. 36 campañas. AC - Category Exact - MX 9,015.78 / 95,514.51 / 9.44 % En target / 20.02 % / margen_plataforma. USPerNog - Category Exact - US 71.57 %, +52.20 pp, target 19.37 %. Pestañas Todas 36, Sobre 13, Gasto 7, En 9, Sin actividad 7.
- Captura `img/campanas.png`.

## Decisiones (`/decisiones`)
- Columnas Entidad, Kind, Search term, Motivo, Target usado, Bid, Decidida y paginador: `app/templates/decisiones.html:5-28`.
- 50 por página (`LIMITE_FEED_DEFAULT`): `origin/master:app/ui.py:534-546`; producción mostraba «pagina 1 de 58 · 2900 decisiones».
- Más nueva arriba: el feed ordena por id descendente (`app/api_dashboard.py` cabecera l. 38).
- Decidida en UTC: `app/ui.py:133-138` (`ts_ui` recorta el ISO); JSON `decided_at` termina en Z y la tabla muestra 08:40.
- Kinds posibles bid, budget, pause, resume, negative, harvest: `migrations/0001_initial.sql:40`; search_term solo en negative/harvest `:874-877`.
- Motivos en español y % de ajuste: `origin/master:app/api_dashboard.py:214-245`. `producto_sin_ventas` no está en el diccionario y sale crudo; es la pausa por producto que pasa por la cola de 48 h: `docs/superpowers/specs/2026-10-24-decisiones-producto-design.md:16,282,344,361`.
- Datos reales: `GET /decisiones`, `GET /api/dashboard/decisiones`. #2868 arras · AU2 - Category Phrase - US, bid 0.22 → 0.30 USD, «Regreso: el bid nuevo desplomo el trafico», 2026-11-08 08:40. En los 50 más nuevos, 31 son pause producto_sin_ventas.

## Salud (`/salud`)
- Tarjeta por plataforma con chips status y mode, Watermark, Sync estructura, Ingesta Ads principal, Skips (canvas), Ultimo ciclo con decisiones/applies, línea roja telegram, Quota, Avisos de campaña, Historico (14d): `app/templates/salud.html:5-178`.
- Estados posibles running/done/degraded/skipped/failed: `migrations/0001_initial.sql:802-803`; rojo para degraded/failed: `salud.html:9`. Modos off/shadow/live: `0001_initial.sql:796`.
- Histórico = últimos 14 ciclos (`ORDER BY id DESC LIMIT 14`): `origin/master:app/api_dashboard.py:254-264`.
- Gráfica de skips en barras horizontales con tooltip por defecto: `app/static/js/dashboard.js:118-135`, `:290-303`.
- Datos reales: `GET /salud`, `GET /api/dashboard/salud`. US #174 08:40 UTC 33 decisiones; #176 17:58 UTC 0 decisiones (fuera de horario); los 14 ciclos done. Skip más común «Campaña no habilitada (pausada/archivada o sin estado)» 1,620 (US). Reportes `written`, edad 1 día.
- Captura `img/salud.png` (no se usó en la hoja por espacio).

## Contribución (`/contribucion`)
- Plantilla y rango «sin halo .. con halo», «—(motivo)», chips: `app/templates/contribucion.html:3-62`.
- Etiqueta «contribucion pre-cargos · no decisoria» y motivos: `app/dashboard_contribucion.py:19-29`. Ventana D-15-89 .. D-15 UTC: `:39-42`.
- Fórmula (ventas − costo proxy − gasto ads; sin halo = revenue_same_sku, con halo = ad_revenue); fee, refund y withholding no entran: `docs/MARGEN-ENTIDAD.md:24-40`, `:94-95`, `:158-174`. No decisoria: `:126-137`. Precio menor en multilisting: `migrations/0008_precio_multilisting_us.sql:536-545`.
- Datos reales: `GET /contribucion`, `GET /api/dashboard/contribucion`. Ventana 2026-07-27..2026-10-24. AC - Category Exact - MX 30454.53 .. 114986.89. A1U - Category Exact - US -540.73 .. -264.56. Exact_arras_de_boda «— (catalogo parcial)».

## Verificación de 01-inicio.html
- (a) Programación real: `crontab -l` en goncloud (lectura por ssh) `40 8 * * *` cycle amazon_us y `41 8 * * *` cycle amazon_mx. También `docs/DEPLOY.md:687-700` (tabla UTC, 08:40). Histórico de Salud confirma un ciclo diario por mercado a las 08:40/08:41 UTC.
- (b) Watermark = `max(metric_date)` de `v_metric_latest` por plataforma: `app/optimizer/windows.py:351-356`; chip del encabezado en `app/static/js/shell.js:53-73`.
- (c) Estados: `migrations/0001_initial.sql:802-803`; chip en `shell.js:64-65`.
- (d) Botones Amazon MX/US: `busquedas_sin_venta.html:9`, `donde_poner_el_dinero.html:10`, `keywords_danadas.html:10`, `gasto_sin_venta.html:11`, `productos_sin_clics.html:11`, `ruido.html:10`, y Resumen con Todos y Mercado Libre. Campañas usa un desplegable Plataforma; Decisiones, Salud y Contribución muestran los dos mercados juntos.

## Ronda de correcciones (lectores novato y escéptico), 8-nov-2026
Código de `origin/master` 7cf7c9a (copias en `scratchpad/fixa-src/`). Producción solo con GET.

- Resumen, «Ordenado por impacto» (antes decía «del más caro al más barato»): `app/templates/resumen.html:188`. El orden lo da `pct_sv` y equivalentes en `app/ui_metricas.py:400-447`.
- Rango personalizado trae su propio Canal y Periodo; «Con desde y hasta, el rango manda y el periodo de arriba se ignora»; Limpiar solo con canal, fechas o periodo distinto de 30: `resumen.html:69-101` (Limpiar `:95-97`, nota `:99`).
- «Gasto sin venta en Amazon US» de Qué revisar cuenta días y enlaza a /gasto-sin-venta: `app/ui_metricas.py:407-411`, `:434-447`.
- Nota de Mercado Libre («meli no publica madurez … su D-1 aun puede cambiar»): `app/dashboard_resumen.py:136-142`; se pinta en `resumen.html:174-175`.
- «cobertura incompleta» = faltan días con dato: `resumen.html:281-283`, `app/dashboard_resumen.py:636` (`completo = dias_con_dato == dias_rango`).
- «la media de 7 dias que ya trae el contrato»: `resumen.html:228`. «contrato» explicado como el paquete de cifras que Orbit arma (inferido de la plantilla, que usa `contrato` como fuente de todo).
- Campañas solo lista campañas con filas en la ventana de 30 días: `app/api_dashboard.py:181-194` (`_SQL_CAMPANAS_30D … v.metric_date BETWEEN`). Prod: `GET /api/dashboard/campanas` = 36 items, todos ENABLED, sin «A1U - Category Exact - US».
- Pestaña «Gasto sin venta» de Campañas = cost > 0 y revenue = 0: `app/ui_campanas.py` `estado_de`.
- Filtros por métricas: Cost/Revenue/ACoS/Target buscan texto contenido (casefold) en la cifra cruda; Clicks con número entero = igualdad exacta; ACoS acepta «sin ventas»: `app/ui.py:301-349`. Cifra cruda sin comas: prod `cost: "45.5200"`.
- Orden por Cost mezcla monedas (clave decimal sin moneda): `app/ui.py:286-287`. Prod: AC - Category Exact - MX 9015.78 MXN, AU2 - Auto Discovery - US 466.14 USD.
- Target efectivo = último ciclo live+done aunque no decida nada: docstring `app/api_dashboard.py:836-840`. Prod: USPerNog `target_efectivo.ciclo.id_max = 176`; Salud #176 con 0 decisiones.
- Decimales largos en Target usado: prod `GET /decisiones` muestra «19.36610636923199718608899984%».
- Bid «— → —» en pausas: prod `GET /decisiones` (filas pause producto_sin_ventas).
- Motivos: traducciones `app/api_dashboard.py:214-245`. «hoja» = keyword o target (nivel "hoja" en `app/optimizer/politica.py:326-356`). «sangra» = gasto > 1.15 × target × venta, o 0 pedidos con gasto ≥ gasto para concluir: `politica.py:492-519`. «equilibrio» = margen neto del producto: `politica.py:158-161`, `app/cycle.py:749`, `:788`. «concluir» = gasto_para_concluir, doble = −25 %: `politica.py:349-356`; default 350 MXN / 36 USD en `app/optimizer/goals.py:800-830`.
- «sustituto», «mismo ASIN», «query amplia», «query cercana» = tipos de target automático de Amazon: `app/etiqueta_entidad.py:8-27`. La hoja no escribe «query» porque `lint.py` la marca como jerga; se cita como «las que terminan en amplia o cercana».
- Salud, hora larga con «+00:00»: `salud.html` pinta `started_at` crudo (`:53`).
- Etiquetas de la gráfica de skips cortadas: reporte del lector novato; tooltip en `app/static/js/dashboard.js:118-135`.
- Skip más común en US sigue siendo campana_no_enabled 1,620: `GET /api/dashboard/salud` (8-nov, ciclo 176).
- Segunda hoja de Salud: Quota (`salud.html:89-106`, «saturada» si used ≥ cap), tabla de spapi_* con ok/fallo, Motivo, Ultimo 429 y LWA (`:110-131`), Destino de harvest y «Campañas de grupo sin destino» (`:71-84`; «por terna» = destino escrito en el goal, `app/api_dashboard.py:368`, `:1099-1115`, `app/optimizer/hygiene.py:195-205`), Precios (`:131-145`), Avisos de campaña (`:146-160`), Jev (`:180-193`, «Encendido» = `jev.interruptor.encendido`; «Última corrida» sin fecha). Prod: US spapi_listings ok «49x http_404, 1x red»; harvest terna 8; Jev encendido, llamadas_hoy 0, `ultima_inicio` 2026-10-21T09:30 (la pantalla no lo muestra).
- «Sin consultar» del recuadro Último ciclo es el texto inicial antes de que el menú lea Salud; si la lectura falla dice «No se pudo consultar»: `app/templates/base.html:84`, `app/static/js/shell.js:53-77`. Medido: `/api/dashboard/salud` responde en 1.5 s; `/inertes` tardó más de 60 s (curl con tope de 60 s), por eso el texto inicial dura más ahí.
- Contribución: rango que cruza cero = sin halo negativo, con halo positivo (`docs/MARGEN-ENTIDAD.md:24-40`). Prod `GET /api/dashboard/contribucion`: A1U - Category Phrase - US -187.54 .. 93.99. «vintage D-15 UTC» y «El rollup suma las entidades hijas … (D2)»: `contribucion.html:3-5`, ventana `app/dashboard_contribucion.py:39-42`. «FX aproximado · nearest_prior»: `contribucion.html:52-54`.
- Nombre del menú «Contribucion» sin acento: `base.html:26`, `:52`.
- Se quitó la captura `img/campanas.png` de la hoja de Campañas para hacer espacio a los filtros y al aviso de monedas.

## CLARIDAD 01 I
- Recuadro Último ciclo: «US · Completo · En vivo» y «8 nov, 11:58 · N decisiones» con `Orbit.etiqueta` (`estado_ciclo`, `modo`) y `Orbit.fechaHora`; chip del encabezado «Cifras hasta · US: …»: `app/static/js/shell.js`, `app/templates/base.html`.
- Gráfica de skips de Salud: del más contado al menos contado, autoSkip falso, 24 px por barra, motivo en hasta dos renglones con el texto completo en el tooltip y «(búsquedas)» para un motivo repetido: `app/static/js/dashboard.js` (`graficarBarras`, `renglones`), `app/api_dashboard.py` (`_skips_traducidos`).
- «Últimos datos de métricas» y «Estructura sincronizada» en la tarjeta: `app/templates/salud.html:13-14`.
