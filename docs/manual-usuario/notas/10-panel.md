# Fuentes · 10-panel (Resumen, Campañas, Decisiones, Salud, Contribución)

Producción va adelante de la rama local `resumen-ads-04-diseno`. Las fuentes de código son de
`origin/master` (7cf7c9a), que coincide con lo que sirve producción. Producción se consultó solo con GET
el 8-nov-2026 y otra vez el 9-nov-2026 (bloque J.1, SHA 4be5081) (`http://10.13.13.1:8010/...`). Copias en el scratchpad `panel/`.

## Resumen (`/`)
- Filtros Canal (Todos / Amazon MX / Amazon US / Mercado Libre), Periodo 7/30/90, línea Ventana, «el dia en curso queda fuera», Rango personalizado con Desde/Hasta/Filtrar/Limpiar, aviso «Hacen falta las dos fechas»: `app/templates/resumen.html:41-101`. Limpiar aparece si hay canal, fechas o periodo distinto de 30 (l. 95-97) y lleva a `/`.
- Cinco secciones numeradas: `resumen.html:104-465`.
- Bien / Atención / Sin dato: `app/ui_metricas.py:199-221` (con meta, ACoS ≤ meta = Bien; sin meta, ventas sube y ACoS baja = Bien; sin cost ni revenue = Sin dato).
- «Meta ACoS: sin configurar» y enlace Configurar a `/settings`: `resumen.html:132-135`.
- Cifras inmaduras y asterisco en serie diaria: `resumen.html:172-176`, `:443`.
- Línea fina = día, gruesa = media 7 días, hueco = dato desconocido: `resumen.html:228`; `app/static/js/dashboard.js:186-200` (grosor 2.5 vs 1.2).
- Totales por moneda sin mezclar divisas: `resumen.html:266-312`.
- Datos reales (9-nov, 21:51 hora del centro): `GET /`. Ventana 2026-10-10..2026-11-08. MX Bien, ACoS 13.32 % (antes 14.52 %), ROAS 7.51×. US Atención, ACoS 44.84 %; Qué revisar 1 «Gasto sin venta en Amazon US · 12 de 30 dias tuvieron gasto y $0.00 en ventas atribuidas ($616.71 USD…)». Los valores de la fila «▼ -0.83 pp y ROAS 7.41×» de la tabla son ilustrativos (del 8-nov) y se dejaron.
- Captura `img/resumen.png` recapturada el 9-nov (agent-browser, solo abrir): ya muestra «Horas del centro de México», «Cifras hasta · US: 2026-11-08 · MX: 2026-11-08» y el contador de Propuestas en 1.

## Campañas (`/campanas`)
- Plantilla: `git show origin/master:app/templates/campanas.html`. Franja de inmaduros l. 9. Tarjetas «Campañas contra su target» l. 11-43. Pestañas l. 45-53. Filtros Plataforma/Estado/Campaña/Procedencia, Filtros por métricas, Filtrar/Limpiar l. 55-107. Columnas en orden l. 111-123. Nota de estados l. 163.
- Clasificación En target / Sobre target (+N pp) / Gasto sin venta / Sin target / Sin actividad: `origin/master:app/ui_campanas.py` `estado_de` (aprox. l. 85-110).
- Orden por omisión = orden del API (US luego MX por `PLATAFORMAS_MONEDA`, y dentro por id): `origin/master:app/api_dashboard.py:181-193` (`ORDER BY e.id`) y `:832-866`. Sin `ordenar` no se reordena: `origin/master:app/ui.py:352-366`. Primer clic desc en numéricas, asc en texto; segundo invierte; ausentes al final: `app/ui.py:390-399` (rama) y `:360-366`.
- Target efectivo = lo que congeló el último ciclo live+done para las hojas; rango si difieren; `mixto` si varias procedencias; null = «sin target»: `origin/master:app/api_dashboard.py:732-752` (`display`), `:772-790`, docstring `:832-846`.
- Peldaños y su orden: `origin/master:app/optimizer/goals.py:286-293` (`PELDANOS_CASCADA`), `:300-320` (núcleo, primero que tenga valor), `:20` (margen_producto solo en grupo exclusivo), banda [10, 45] del margen de plataforma `:959`.
- Línea «Meta por plataforma · En vivo · puja mínima 0.10 · máxima 2.50» / «sin goal»: `app/templates/campanas.html:131-134` (`alcance_goal` en minúsculas, `modo`, `dinero`); registro `app/etiquetas.py:668-671` (ALCANCE_GOAL) y `:324` (MODO: Apagado / Práctica / En vivo).
- Filtro Plataforma con «Amazon US» / «Amazon MX» y columna Plataforma «Amazon US · USD»: `campanas.html:59-65`, `:143`; registro PLATAFORMA `app/etiquetas.py:259-263`. Procedencia en español en filtro y columna: `campanas.html:78-84`, `:154`; registro PROCEDENCIA `app/etiquetas.py:265-272` (Meta de la campaña, Meta de la plataforma, Meta por producto, Margen de la familia, Margen de la plataforma, Meta manual de la plataforma). «mixto» no está en el registro: `etiqueta()` lo humaniza como «Mixto» (`app/etiquetas.py:953-968`; peldaño «mixto» en `app/api_dashboard.py:596`).
- Rango del target sin «%»: el valor llega como texto «min–max» (`app/api_dashboard.py:588-592`) y `pct` deja igual lo que no es número (`app/formato.py:40-45`).
- Datos reales (9-nov): `GET /campanas`, `GET /api/dashboard/campanas`. 36 campañas. AC - Category Exact - MX 8,658.15 / 89,970.63 / 9.62 % En target / 20.02 % / «Margen de la plataforma». USPerNog - Category Exact - US 69.98 %, «+50.76 pp sobre target», 19.22 %; `target_efectivo.ciclo.id_max = 178`, decidido 2026-11-09T08:40Z = 9 nov, 02:40; Salud #178 con 1 decisión. AU2 - Auto Discovery - US 482.86 USD (ejemplo del aviso de monedas). Pestañas Todas 36, Sobre 8, Gasto 7, En 14, Sin actividad 7. Las 30 con target dicen «Margen de la plataforma»; 6 «sin target».

## Decisiones (`/decisiones`)
- Columnas Entidad, Kind, Search term, Motivo, Target usado, Bid, Decidida y paginador: `app/templates/decisiones.html:5-28`.
- 50 por página (`LIMITE_FEED_DEFAULT`): `origin/master:app/ui.py:534-546`; producción mostraba «pagina 1 de 58 · 2900 decisiones».
- Más nueva arriba: el feed ordena por id descendente (`app/api_dashboard.py` cabecera l. 38).
- Decidida en hora del centro: `app/templates/decisiones.html:28` (`fecha_hora`), `app/formato.py:77-84`. Prod: #2901 «9 nov, 02:40» (JSON 08:40Z).
- Kind traducido: `decisiones.html:18` (`etiqueta("kind")`); registro KIND `app/etiquetas.py:283-290` (Puja, Presupuesto, Pausa, Reactivación, Negativo, Captura). search_term solo en negative/harvest: `migrations/0001_initial.sql:874-877`.
- Motivos: `motivo_decision_es` (`app/etiquetas.py:981-990`) con MOTIVO_DECISION `:222-257` y sujeto «La keyword» / «El producto objetivo» / «El anuncio del producto» de SUJETO_POR_ENTIDAD `:972-978`. Ya no salen «hoja», «sangra», «equilibrio» ni «concluir» (grep vacío en el registro). «su grupo de anuncios pierde dinero» = códigos `grupo_sangra*` (`app/optimizer/politica.py:50`, `:58`; sangra = gasto > 1.15 × target × venta, o 0 pedidos con gasto ≥ gasto para concluir, `:494-495`). «pierde dinero» = ACoS sobre el equilibrio (`politica.py:325-330`). «gastó el doble de lo necesario para decidir» = gasto ≥ 2 × gasto_para_concluir (`politica.py:352-353`); 350 MXN / 36 USD en `app/optimizer/goals.py:789-793`.
- Target usado con dos decimales: `decisiones.html:21` (`pct`), `app/formato.py:40-45`. Prod «19.37 %».
- Blancos automáticos «mismo ASIN», «sustituto», «búsqueda amplia», «búsqueda cercana»: TIPO_TARGET `app/etiquetas.py:456-475`, usado por `app/etiqueta_entidad.py:104-118`.
- «Pausa del anuncio: el producto no vendió en la ventana de la pausa» (antes `producto_sin_ventas` crudo): `app/etiquetas.py:256`.
- Datos reales (9-nov): `GET /decisiones`. «pagina 1 de 59 · 2,901 decisiones». #2868 arras · AU2 - Category Phrase - US, Puja, 0.22 → 0.30 USD, «La keyword regresó a su puja anterior: la nueva hizo caer sus visitas», 19.37 %, «8 nov, 02:40». En los 50 más nuevos, 31 «Pausa del anuncio…». `GET /api/dashboard/cortes`: 31 propuestas `shadow` «Pausar anuncio del producto» y 1 `live` «Apagar palabra» (de ahí «las de práctica nunca se aplican»).

## Salud (`/salud`)
- Tarjeta por plataforma con chips status y mode, Watermark, Sync estructura, Ingesta Ads principal, Skips (canvas), Ultimo ciclo con decisiones/applies, línea roja telegram, Quota, Avisos de campaña, Historico (14d): `app/templates/salud.html:5-178`.
- Estados posibles running/done/degraded/skipped/failed: `migrations/0001_initial.sql:802-803`; rojo para degraded/failed: `salud.html:9`. Modos off/shadow/live: `0001_initial.sql:796`.
- Histórico = últimos 14 ciclos (`ORDER BY id DESC LIMIT 14`): `origin/master:app/api_dashboard.py:254-264`.
- Gráfica de skips en barras horizontales con tooltip por defecto: `app/static/js/dashboard.js:118-135`, `:290-303`.
- Datos reales (9-nov): `GET /salud`, `GET /api/dashboard/salud`. Los dos mercados «Completo» «En vivo». US «#178 · 9 nov, 02:40 · decisiones: 1 · aplicadas: 0»; MX #179 9 nov, 02:41, 0. Los 14 ciclos Completo. Skip más largo US «Campaña no habilitada (pausada, archivada o sin estado)» 1,620. Reportes «Escrito», edad «2 dias». SP-API US Publicaciones «ok #1461 · 8 nov, 23:02», motivo «49x http 404». Jev «Encendido», «Último cálculo terminado: 9 nov, 15:30 (hace 6 h)», `fin_viejo: false`.

## Contribución (`/contribucion`)
- Plantilla y rango «sin halo .. con halo», «—(motivo)», chips: `app/templates/contribucion.html:3-62`.
- Fórmula (ventas − costo proxy − gasto ads; sin halo = revenue_same_sku, con halo = ad_revenue); fee, refund y withholding no entran: `docs/MARGEN-ENTIDAD.md:24-40`, `:94-95`, `:158-174`. No decisoria: `:126-137`. Precio menor en multilisting: `migrations/0008_precio_multilisting_us.sql:536-545`.
- Textos de hoy (bloque H.5): título «Contribución por campaña antes de cargos (90 días cerrados)» y «Son 90 días que terminan hace 15, cuando las ventas ya no cambian. Cada campaña suma lo que dejan sus keywords y sus productos objetivo.»: `app/templates/contribucion.html:3-5`. Leyenda «contribucion pre-cargos · no decisoria»: `contribucion.html:15`, `app/dashboard_contribucion.py:20`. Chip «precio min multilisting»: `contribucion.html:35-37`. «Tipo de cambio aproximado: del último día con dato»: `contribucion.html:51-53`, METODO_FX `app/etiquetas.py:645-648`. «— (catálogo incompleto)»: MOTIVO_CONTRIBUCION `app/etiquetas.py:650-658`.
- Ventana por día UTC (`(now() AT TIME ZONE 'UTC')::date - 15`): `app/dashboard_contribucion.py:31-33`. Por eso desde las 18:00 del centro (UTC−6) las fechas avanzan un día: la lectura de las 21:51 del 9-nov dio 2026-07-29..2026-10-26.
- Datos reales (9-nov): `GET /contribucion`. A1U - Category Phrase - US -191.67 .. 182.47. A1U - Category Exact - US -540.76 .. -264.63 (no está entre las 36 de `/api/dashboard/campanas`). Exact_arras_de_boda «— (catálogo incompleto)».

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
- Orden por Cost mezcla monedas (clave decimal sin moneda): `app/ui.py:286-287`. Prod 9-nov: AC - Category Exact - MX 8,658.15 MXN, AU2 - Auto Discovery - US 482.86 USD.
- Target efectivo = último ciclo live+done aunque no decida nada: docstring `app/api_dashboard.py:836-840`.
- Bid «— → —» en pausas: prod `GET /decisiones` 9-nov (filas Pausa).
- Etiquetas de la gráfica de skips cortadas: reporte del lector novato; tooltip en `app/static/js/dashboard.js:118-135`.
- Skip más común en US sigue siendo campana_no_enabled 1,620: `GET /api/dashboard/salud` (9-nov, ciclo 178).
- Segunda hoja de Salud: Quota (`salud.html:89-106`, «saturada» si used ≥ cap), tabla de spapi_* con ok/fallo, Motivo, Ultimo 429 y LWA (`:110-131`), Destino de harvest y «Campañas de grupo sin destino» (`:71-84`; «por terna» = destino escrito en el goal, `app/api_dashboard.py:368`, `:1099-1115`, `app/optimizer/hygiene.py:195-205`), Precios (`:131-145`), Avisos de campaña (`:146-160`), Jev (`:180-193`, «Encendido» = `jev.interruptor.encendido`; «Última corrida» sin fecha). Prod 9-nov: US Publicaciones ok «49x http 404» (MX «43x http 404, 14x sin summary»); harvest terna 8; Jev Encendido, último cálculo 9 nov, 15:30. «red» como motivo posible se conserva de la lectura del 8-nov (`1x red`).
- «Sin consultar» del recuadro Último ciclo es el texto inicial antes de que el menú lea Salud; si la lectura falla dice «No se pudo consultar»: `app/templates/base.html:84`, `app/static/js/shell.js:53-77`. Medido: `/api/dashboard/salud` responde en 1.5 s; `/inertes` tardó más de 60 s (curl con tope de 60 s), por eso el texto inicial dura más ahí.
- Contribución: rango que cruza cero = sin halo negativo, con halo positivo (`docs/MARGEN-ENTIDAD.md:24-40`).
- Nombre del menú «Contribucion» sin acento: `base.html:26`, `:52`.
- Se quitó la captura `img/campanas.png` de la hoja de Campañas para hacer espacio a los filtros y al aviso de monedas.

## CLARIDAD 01 I
- Recuadro Último ciclo: «US · Completo · En vivo» y «8 nov, 11:58 · N decisiones» con `Orbit.etiqueta` (`estado_ciclo`, `modo`) y `Orbit.fechaHora`; chip del encabezado «Cifras hasta · US: …»: `app/static/js/shell.js`, `app/templates/base.html`.
- Gráfica de skips de Salud: del más contado al menos contado, autoSkip falso, 24 px por barra, motivo en hasta dos renglones con el texto completo en el tooltip y «(búsquedas)» para un motivo repetido: `app/static/js/dashboard.js` (`graficarBarras`, `renglones`), `app/api_dashboard.py` (`_skips_traducidos`).
- «Últimos datos de métricas» y «Estructura sincronizada» en la tarjeta: `app/templates/salud.html:13-14`.
- Hojas de Salud (DeepSeek F4): tarjeta «Amazon US»/«Amazon MX» con chips de estado y modo traducidos (`app/templates/salud.html:7-10`, registro `estado_ciclo`, `modo`); «Último ciclo» en hora del centro (ejemplo de la hoja actualizado el 9-nov a «#178 · 9 nov, 02:40 · decisiones: 1 · aplicadas: 0») (`salud.html:50-55`, `formato.fecha_hora`, leyenda «Horas del centro de México» de `base.html`); «Últimos 14 ciclos» (`salud.html:162`); «Escrito» (`estado_reporte`); «Cambios permitidos hoy» con Usados/Tope/«saturada» (`salud.html:89-99`); tabla de SP-API con fuentes traducidas (`fuente_ingesta`) y motivo sin guiones bajos (`salud.html:110-125`); «Último cálculo terminado» de Jev y alerta pasadas 13 horas (`salud.html:183-190`). Horas del 8-nov: ciclo #174 08:40 UTC = 02:40 y #176 17:58 UTC = 11:58 del centro.

## CLARIDAD 01 J.1 (9-nov-2026)
Producción solo con GET a las 21:51 hora del centro (`TZ=America/Mexico_City date`), copias en `/tmp/jpanel/`.
- 01-inicio, contador de Propuestas solo cuenta reales (`modo === "live"`), etiqueta «N pendientes (M de práctica)»: `app/static/js/shell.js:26-33`. Prod: `/api/dashboard/cortes` 1 live, 31 shadow; el menú mostraba 1 y aria-label «1 pendientes (31 de práctica)» (agent-browser, eval de solo lectura).
- 01-inicio, recuadro Último ciclo «US · Completo · En vivo» / «9 nov, 02:40 · 1 decisiones» y chip «Cifras hasta · US: 2026-11-08 · MX: 2026-11-08»: `shell.js:56-77`; leído en pantalla con agent-browser. «sin ciclo» sigue existiendo (`shell.js:64`), por eso 90-cierre lo conserva.
- 90-cierre, horas en hora del centro con leyenda «Horas del centro de México»: `app/formato.py:16-17`, `app/templates/base.html:93`. Fechas sin hora que siguen el día UTC: «Hoy» de Precios en Salud (`app/precio/fuentes.py:88-90`, prod «Hoy 2026-11-10» a las 21:51 del 9-nov) y ventana de Contribución (arriba).
- 00-portada, fecha de actualización al 9-nov.

## J.2, correcciones del lector fresco (`docs/evidencia/claridad-01/J/lector.md`)

- 01-inicio, glosario Ciclo: además de la de madrugada corren revisiones extra; prod `/salud`, «Últimos 14 ciclos» con #176 y #177 del «8 nov, 11:58» junto a los de las 02:40.
- 10-panel Resumen, «▼ -1.20 pp» y «ROAS 7.51×» de Amazon MX con 30 días: `img/resumen.png` recapturada el 9-nov. El punto 1 de Qué revisar trae los enlaces «Ver gasto sin venta →» y «Busquedas que gastan sin vender →» (agent-browser sobre `/`).
- 10-panel Salud, Estado «Tope de la configuración»: `app/etiquetas.py:634` (`fuente_quota.config_vigente`), `app/templates/salud.html:98`. Motivos «9x http 400», «14x sin summary» y columnas «Último límite de Amazon» / «Última falla de acceso» (último 429 y último fallo de acceso, con su fecha): `app/templates/salud.html:107-124`.
