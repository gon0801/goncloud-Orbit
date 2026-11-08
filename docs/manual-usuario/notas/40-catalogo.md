# Fuentes · 40-catalogo (Reputación, Precios, Familias, Settings)

Repo: `/Users/dn/dev/goncloud-Orbit` (rama resumen-ads-04-diseno, HEAD 5adf1be).
Producción: solo GET a `http://10.13.13.1:8010` el 8-nov-2026.

## Reputación

| Afirmación | Fuente |
|---|---|
| 4 tarjetas Rating MeLi / Reviews verificadas / Preguntas pendientes / Alertas abiertas | app/templates/reputacion.html:8-29 |
| Alertas críticas primero | app/templates/reputacion.html:34; app/api_reputacion.py:159 |
| Tipos, severidad y umbrales: rating_bajo < 4.2, caida_rating ≥ 0.3, reclamos_suben +2 en 7 días, salud_cuenta, resena_1 crítica | app/reputacion_alertas.py:1-20, 33-36, 59-157 |
| salud_cuenta «info» si el level no está mapeado | app/reputacion_alertas.py:127-140 |
| Se cierran solas rating_bajo, caida_rating, reclamos_suben, salud_cuenta; resena_1 no | app/reputacion_alertas.py:156-159, 302-307 |
| Ventana MeLi 7 días, Amazon lectura anterior | app/api_reputacion.py:75-90; reputacion.html:66 |
| No hay botón para cerrar alertas ni responder (sin POST en /api/reputacion; «Responder en MeLi» es span con title «v1 es solo lectura») | app/api_reputacion.py:1-12, 29; app/templates/reputacion.html:151 |
| Tabla Publicación/Rating/Reviews/Tendencia/Fuente/Texto/Ver | app/templates/reputacion.html:72-106 |
| Fuente OK = hay rating; Texto verificado/sin-reviews/sin-verificar; Amazon siempre sin verificar | app/api_reputacion.py:102-118; reputacion.html:108 |
| URL solo para Amazon; MeLi «— sin URL» | app/api_reputacion.py:37-42 |
| Reviews recientes 20, solo publicadas MeLi; preguntas tope 50 | app/api_reputacion.py:31-33; reputacion.html:115 |
| Datos de la cuenta (65 listings, 4.8 de 30, 573 reviews, 5 alertas 4+1, MLM1890605671 review 3188730888 del 7-oct, MLM1890586139 rating 3.8, 4 preguntas de MLM2736115339 la más vieja hace 114 d, 5_green/silver/0 de 24) | GET /reputacion |

Nota: el brief decía que Reputación tiene pestañas; en la plantilla y en producción no hay pestañas, son recuadros en una sola página.

## Precios

| Afirmación | Fuente |
|---|---|
| Una decisión por publicación por día; sin goal vigente no hay decisión | docs/DATABASE.md:852-890 |
| Goal = margen en fracción, banda 0.10-0.60 | docs/DATABASE.md:861-864 |
| Modos off/shadow/live; universos amazon_mx/fba, amazon_mx/fbm, amazon_us/fbm | app/templates/settings.html (Motor de precios); app/templates/_precios_seguridad.html:15-20; GET /precios |
| Compuerta retiene por fusible (muchos cambios a la vez), insumo sistémico o errores | app/precio/compuerta.py:1-4, 196-245 |
| Soltar pide Actor, Nota, Token; aparece solo con corrida liberable | app/templates/_precios_seguridad.html:36-38; app/static/js/precios.js:238-283 |
| Cobertura «activas = evaluadas + no evaluadas + sin goal + fuera de alcance» | app/templates/_precios_goals.html:96-103 |
| Puente del bridge / aviso | app/templates/_precios_goals.html:104; app/ui.py:542-566 |
| Con goal, Último cambio, «confirmado» | app/templates/_precios_goals.html:128-142; app/notifica.py:1179-1187 |
| Motivos en español (sin observacion de precio del dia, perdiendo tras la subida, precio observado distinto del publicado) | app/notifica.py:1068-1098 |
| Precio divergente = N días seguidos no_evaluado(precio_divergente); N = setting precio_aviso_dias_sin_evaluar (1-14) | app/api_precios.py:499-536, 553, 585; app/notifica.py:1467-1471 |
| Planear = vista previa sin escribir; Aplicar escribe; live exige go literal no vacío | app/api_precios_write.py:1-24; app/precio/goals_write.py:517-530; app/static/js/precios.js:136-232 |
| Salto > 25 % exige confirmación por fila | app/precio/siembra.py:15-20, 59-60; precios.js:109-113 |
| Goal vale desde el día UTC en que se aplica (valid_from = hoy) y la segunda corrida del día no decide → efecto en la siguiente revisión diaria | app/precio/goals_write.py:250-256; docs/DATABASE.md (UNIQUE por día) |
| TACoS = del mes maduro, mismo número por fila | app/api_precios.py:436-477 |
| Datos de la cuenta (MX 206=169+4+33+0; 0X-MX5B-0D61 58.62 % 2689.00 mantenido; VH-YA15-JRHD frenado; US 0 evaluadas; 1B-J83K-7VFL 91.13→92.23 31-oct confirmado; No evaluados MX 59 filas) | GET /precios |

## Familias

| Afirmación | Fuente |
|---|---|
| Dos niveles, una familia por producto y plataforma, slug = tipo_producto | app/familias.py:1-11 |
| Origen «usa la meta del país» / «margen de la familia» / «margen de la familia padre» | app/familias.py:30-36, 329-372 |
| Meta = lo que midió el último ciclo (la web no recalcula) | app/familias.py:309-327 |
| Familia necesita 30 días con ventas | app/optimizer/goals.py:581-582 |
| Ventas 90d = unidades (quantity) de ventas en 90 días | app/familias.py:150-166 |
| Fábrica toma el tipo de producto del slug con familia única; aviso de mezcla | app/fabrica_plan.py:186-190, 316-323; app/api_fabrica.py:149, 204 |
| Botones Crear familia y Asignar a los seleccionados, Token arriba; asignar reemplaza | app/templates/familias.html:10-14, 49-66, 85-99; app/static/js/familias.js:30-76; app/familias.py:125-148 |
| Sin botón de borrar/renombrar | app/templates/familias.html (solo esos dos formularios) |
| Datos de la cuenta (Arras matrimoniales + Arras MX + Arras US, 0 productos; US sin familias; 249 y 119 sin familia) | GET /familias y GET /familias?plataforma=amazon_us |

## Settings

| Afirmación | Fuente |
|---|---|
| Cascada de 6 peldaños en este orden (corregido en la ronda de correcciones) | origin/master:app/optimizer/goals.py:17-35, 286-292 |
| Cada peldaño decide solo si el anterior es None | app/optimizer/goals.py:17-18, 239-246 |
| Goal de campaña pisa siempre, incluso con target vacío y enabled=false | app/optimizer/goals.py:30-33, 178-189; app/templates/settings.html:146-150 |
| enabled=false deja la campaña fuera del ciclo | app/optimizer/goals.py:186-189 |
| Margen × fracción, banda 10-45, 60 días, baja ≤ 0.5 por ciclo, sube de una vez | app/optimizer/goals.py:569-582, 880-933 |
| Floor/Ceiling defaults MXN 1/45, USD 0.10/2.50 | app/optimizer/goals.py:36-40, 107 |
| Caps diarios bid/pause/negative/harvest | app/apply.py:307; app/config_write.py:77-80 |
| El cupo del día se fija al nacer la fila del día (cambio de cap → día siguiente si ya hubo aplicación) | migrations/0002*.sql:455-505 |
| Guardar config = versión nueva; el ciclo lee la última config | app/config_write.py:1-35; app/cycle.py:425-428 |
| Target manual con margen encendido exige el «Entiendo» | app/config_write.py:23-26; app/static/js/settings.js:55-61 |
| ACoS objetivo del Resumen solo alimenta las tarjetas del Resumen | app/api_dashboard.py:583; app/templates/settings.html:112-120 |
| pisa_a texto | app/api_dashboard.py:1808-1815, 1928-1940 |
| Datos de la cuenta (config 50, live, MX 20.02 margen_plataforma fracción 0.5, US 19.37 fracción 0.8, manual 20, caps 1000000, floor/ceiling, 7 goals de campaña sin target, ACoS objetivo vacío) | GET /settings y GET /api/dashboard/settings |

## Ronda de correcciones (lectores novato y escéptico, 8-nov)
Código leído con `git show origin/master:<ruta>` (7cf7c9a), no el árbol local. Producción solo con GET.

### Reputación
- Menú: etiqueta «Catalogo» y enlace «Reputacion», sin acento: `app/templates/base.html:71, 74`.
- Tabla «Listings»: `app/templates/reputacion.html` (sección `aria-label="Listings"`, `<h3>Listings</h3>`).
- resena_1 muestra solo el id de la review («1 estrella nueva: review 2643084807 en MLM2154113682»): `GET /reputacion`. No se autorresuelve («la atiende el operador»): `app/reputacion_alertas.py` (`_AUTO_RESUELVEN` sin resena_1). No hay botón ni ruta para cerrarlas: el único `UPDATE reputation_alert` en app/ es el autorresolver de `reputacion_alertas.py:325`.

### Precios
- Motor también en Mercado Libre: `GET /api/dashboard/settings` → `precio_modo_universo["meli/meli"] = "live"`; rama MeLi en `app/precio/corrida.py:580-600`. La pantalla solo itera amazon_us y amazon_mx: `app/api_precios.py:681` (`for plataforma in PLATAFORMAS_MONEDA`).
- Precio cambia solo si los tres modos (global, grupo, goal) están en live: `app/precio/corrida.py:650-668` (`modo_efectivo(modo_global, universo, mode)`, el mínimo). Columna Modo de cada goal: `app/templates/_precios_goals.html:16-19`.
- Pedido: «margen de hoy» = goal igual al margen de hoy de cada unidad; «valor» = un goal para toda la selección; «modo» = pasar a live o volver a shadow conservando el goal: `git show origin/master:app/precio/siembra.py` líneas 126-193.
- Go literal: texto libre de hasta 500 caracteres; live sin go es rechazado; shadow con go es rechazado; se guarda en el goal: `app/api_precios_write.py:124-128`; `app/precio/goals_write.py:180-199, 517-531, 554`; `app/static/js/precios.js:195-199`. No hay frase esperada en ningún lado: Orbit no lo compara.
- TACoS = gasto en anuncios / ventas totales (`ledger_event kind='sale'`) del mercado y mes, mes de (hoy − 15 días), mismo valor en cada fila: `app/api_precios.py:436-477`; `migrations/0012_tacos_residuo_fail_loud.sql:70-140`.
- SKU de Precios = `listing.seller_sku`: `api_precios.py:445-462`. «Buscar publicaciones» de Crear campañas filtra por SKU de Amazon y muestra «SKU de Odoo»: `app/templates/fabrica.html:66-69`; `app/static/js/fabrica.js:862-905`. `GET /api/fabrica/catalogo?plataforma=amazon_mx`: 0X-MX5B-0D61 → SET-CAO-SAN-PLA «SET - Caoba 22mm Plateado»; VH-YA15-JRHD → NH-PERS-ITA-CEN-PLA.
- oferta_desactualizada = la oferta (precio leído) no pasa la frescura: `app/estimacion_insumos.py:375-379`. `GET /precios`: amazon_us «94 activas = 0 evaluadas + 92 no evaluadas + 2 sin goal», todas las filas de No evaluados con oferta_desactualizada (99 filas).
- El formulario Goals va antes que Cobertura: `_precios_goals.html:12-60` antes de `:62`.

### Familias
- Tipo de producto desde la familia: con una familia única en todos los productos, el tipo sale del slug y «el dado se corrige»: `tools/fabrica_campanas.py:550-574`. Slug = nombre en minúsculas, sin acentos, lo demás a guion bajo: `app/familias.py:53-63`. Bibliotecas existentes: solo kit_arras (catálogo amazon_mx), ninguna en amazon_us.
- Escalón: margen_familia es el 4.º de 6: `app/optimizer/goals.py:286-292`.
- «Auth: header x-orbit-token (campo Token)»: `app/templates/familias.html:5-7`.

### Settings
- Seis peldaños en este orden: `app/optimizer/goals.py:17-35, 286-292` (`PELDANOS_CASCADA`). Con goal de campaña de target vacío, el target de plataforma NO entra: `goals.py:28-32`.
- Target vigente con todos los decimales: `GET /settings` («Target vigente: 20.02023058311879485576605025», «19.36610636923199718608899984»).
- Dos «Modo global»: el de arriba, solo lectura = `ads_optimizer_mode` (publicidad): `app/templates/settings.html:13`, `app/api_dashboard.py:1906`. El de Motor de precios = `precio_modo_global`, editable: `settings.html:84-93`.
- Guardar config de una tarjeta manda también precio_modo_global, precio_modo_universo (todos los grupos, meli incluido) y resumen_acos_objetivo de los tres canales, solo si cambiaron: `app/static/js/settings.js:73-89`; plantilla «Se guarda igual desde cualquier plataforma»: `settings.html:104, 115`.
