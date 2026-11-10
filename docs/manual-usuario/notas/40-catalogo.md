# Fuentes · 40-catalogo (Reputación, Precios, Familias, Settings)

Repo: `/Users/dn/dev/goncloud-Orbit` (rama resumen-ads-04-diseno, HEAD 5adf1be).
Producción: solo GET a `http://10.13.13.1:8010` el 8-nov-2026.
Actualización J.1 (9-nov-2026): código en el checkout del encargo (base 4be5081) y producción leída con GET el
9-nov a las 21:52 hora del centro de México. Ver la sección «Actualización J.1» al final; manda sobre lo de arriba.

## Reputación

| Afirmación | Fuente |
|---|---|
| 4 tarjetas Rating MeLi / Reviews verificadas / Preguntas pendientes / Alertas abiertas | app/templates/reputacion.html:8-29 |
| Alertas críticas primero | app/templates/reputacion.html:34; app/api_reputacion.py:159 |
| Tipos, severidad y umbrales: rating_bajo < 4.2, caida_rating ≥ 0.3, reclamos_suben +2 en 7 días, salud_cuenta, resena_1 crítica | app/reputacion_alertas.py:1-20, 33-36, 59-157 (mensajes :68, 85, 104, 123, 149) |
| salud_cuenta «info» si el level no está mapeado | app/reputacion_alertas.py:127-140 |
| Se cierran solas rating_bajo, caida_rating, reclamos_suben, salud_cuenta; resena_1 no | app/reputacion_alertas.py:156-159, 302-307 |
| Ventana MeLi 7 días, Amazon lectura anterior | app/api_reputacion.py:75-90; reputacion.html:66 |
| No hay botón para cerrar alertas ni responder (sin POST en /api/reputacion; «Responder en MeLi» es span con title «v1 es solo lectura») | app/api_reputacion.py:1-12, 29; app/templates/reputacion.html:151 |
| Tabla Publicación/Rating/Reviews/Tendencia/Fuente/Texto/Ver | app/templates/reputacion.html:72-106 |
| Fuente OK = hay rating; Texto verificado/sin-reviews/sin-verificar; Amazon siempre sin verificar | app/api_reputacion.py:102-118; reputacion.html:108 |
| URL solo para Amazon; MeLi «— sin URL» | app/api_reputacion.py:37-42 |
| Reviews recientes 20, solo publicadas MeLi; preguntas tope 50 | app/api_reputacion.py:31-33; reputacion.html:115 |
| Datos de la cuenta: ver «Actualización J.1» | GET /reputacion (9-nov) |

Nota: el brief decía que Reputación tiene pestañas; en la plantilla y en producción no hay pestañas, son recuadros en una sola página.

## Precios

| Afirmación | Fuente |
|---|---|
| Una decisión por publicación por día; sin goal vigente no hay decisión | docs/DATABASE.md:852-890 |
| Goal = margen en fracción, banda 0.10-0.60 | docs/DATABASE.md:861-864 |
| Modos off/shadow/live; universos amazon_mx/fba, amazon_mx/fbm, amazon_us/fbm | app/templates/settings.html (Motor de precios); app/templates/_precios_seguridad.html:15-20; GET /precios |
| Compuerta retiene por fusible (muchos cambios a la vez), insumo sistémico o errores; medidas = unidades con decisión de hoy, nuevas = intenciones nuevas | app/precio/compuerta.py:1-4, 127-141, 192-247 |
| Soltar pide Actor, Nota, Token; aparece solo con corrida liberable | app/templates/_precios_seguridad.html:36-38; app/static/js/precios.js:238-283 |
| Cobertura «activas = evaluadas + no evaluadas + sin objetivo + fuera de alcance» | app/templates/_precios_goals.html:68-76; app/precio/cobertura.py:1-18, 137-189 |
| Puente del bridge / aviso «N publicaciones sin reportar hace mas de 3 dias» | app/templates/_precios_goals.html:77-84; app/precio/cobertura.py:174-180, 202-210 |
| Con goal, Último cambio, «confirmado», «sin decisión hoy» | app/templates/_precios_goals.html:106-123; app/etiquetas.py:533-539 |
| Motivos en español: registro único | app/etiquetas.py:477-520 (motivo_precio), 522-531 (estado_precio) |
| Frases de Hecho y habria hecho («se mantuvo en … (motivo)», «se frenó: …», «habría subido» en Práctica) | app/api_precios.py:321-356 |
| Precio divergente = N días seguidos no_evaluado(precio_divergente); N = setting precio_aviso_dias_sin_evaluar (1-14) | app/api_precios.py:499-536, 553, 585; app/notifica.py:1467-1471 |
| Planear = vista previa sin escribir; Aplicar escribe; live exige go literal no vacío | app/api_precios_write.py:1-24; app/precio/goals_write.py:517-530; app/static/js/precios.js:136-232 |
| Salto > 25 % exige confirmación por fila | app/precio/siembra.py:15-20, 59-60; precios.js:109-113 |
| Goal vale desde el día UTC en que se aplica (valid_from = hoy) y la segunda corrida del día no decide → efecto en la siguiente revisión diaria | app/precio/goals_write.py:250-256; docs/DATABASE.md (UNIQUE por día) |
| TACoS de la plataforma y del mes de (hoy − 15 días), una línea arriba de la lista de goals | app/templates/_precios_goals.html:14-16; app/api_precios.py:474-481, 524-536 |
| Datos de la cuenta: ver «Actualización J.1» | GET /precios (9-nov) |

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
| Datos de la cuenta: ver «Actualización J.1» | GET /familias y GET /familias?plataforma=amazon_us (9-nov) |

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
| Datos de la cuenta: ver «Actualización J.1» | GET /settings (9-nov) |

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
- TACoS = gasto en anuncios / ventas totales (`ledger_event kind='sale'`) de la plataforma y mes, mes de (hoy − 15 días), una sola línea arriba de la lista: `app/api_precios.py:474-536`; `migrations/0012_tacos_residuo_fail_loud.sql:70-140`.
- SKU de Precios = `listing.seller_sku`: `api_precios.py:445-462`. «Buscar publicaciones» de Crear campañas filtra por SKU de Amazon y muestra «SKU de Odoo»: `app/templates/fabrica.html:66-69`; `app/static/js/fabrica.js:862-905`. `GET /api/fabrica/catalogo?plataforma=amazon_mx`: 0X-MX5B-0D61 → SET-CAO-SAN-PLA «SET - Caoba 22mm Plateado»; VH-YA15-JRHD → NH-PERS-ITA-CEN-PLA.
- oferta_desactualizada (en pantalla «oferta sin observacion reciente», `app/etiquetas.py:496`) = la oferta (precio leído) no pasa la frescura: `app/estimacion_insumos.py:375-379`. Causa en US del 2 al 9-nov y su arreglo: `docs/evidencia/claridad-01/F/causa.md`.
- El formulario Goals va antes que Cobertura: `_precios_goals.html:12-60` antes de `:62`.

### Familias
- Tipo de producto desde la familia: con una familia única en todos los productos, el tipo sale del slug y «el dado se corrige»: `tools/fabrica_campanas.py:550-574`. Slug = nombre en minúsculas, sin acentos, lo demás a guion bajo: `app/familias.py:53-63`. Bibliotecas existentes: solo kit_arras (catálogo amazon_mx), ninguna en amazon_us.
- Escalón: margen_familia es el 4.º de 6: `app/optimizer/goals.py:286-292`.
- (Quitado en J.1: la línea con «x-orbit-token» ya no existe; ver Actualización J.1.)

### Settings
- Seis peldaños en este orden: `app/optimizer/goals.py:17-35, 286-292` (`PELDANOS_CASCADA`). Con goal de campaña de target vacío, el target de plataforma NO entra: `goals.py:28-32`.
- (Quitado en J.1: «Target vigente» con todos los decimales ya no sale; ahora «Meta vigente: 20.02 %».)
- Dos «Modo global»: el de arriba, solo lectura = `ads_optimizer_mode` (publicidad): `app/templates/settings.html:13`, `app/api_dashboard.py:1906`. El de Motor de precios = `precio_modo_global`, editable: `settings.html:84-93`.
- Guardar config de una tarjeta manda también precio_modo_global, precio_modo_universo (todos los grupos, meli incluido) y resumen_acos_objetivo de los tres canales, solo si cambiaron: `app/static/js/settings.js:73-89`; plantilla «Se guarda igual desde cualquier plataforma»: `settings.html:104, 115`.

## Actualización J.1 (9-nov-2026)
Código: checkout del encargo (base 4be5081). Producción: GET a `/reputacion`, `/precios`,
`/api/dashboard/precios`, `/familias`, `/familias?plataforma=amazon_us` y `/settings` el 9-nov a las 21:52
hora del centro de México (`TZ=America/Mexico_City date`). Nada de POST.

### Reputación
- Tipo de alerta traducido: «Reseña de 1 estrella», «Calificación baja», «Caída de calificación», «Suben los
  reclamos», «Salud de la cuenta»: `app/templates/reputacion.html:43`; `app/etiquetas.py:660-666`.
- Fecha de la alerta en hora del centro de México («7 sep, 22:21»): `reputacion.html:47`; `app/formato.py:77-85`.
- Mensaje corto de cada alerta («rating 3.8 < 4.2», «1 estrella nueva: review … en …», «level A -> B»):
  `reputacion.html:45`; `app/reputacion_alertas.py:68, 85, 104, 123, 149`.
- Columna Publicación con plataforma y «dato del 9 nov»: `reputacion.html:92`. La nota de la tabla sigue
  diciendo «snapshot anterior»: `reputacion.html:73`.
- Datos de hoy (`GET /reputacion`): 65 listings, todos «Mercado Libre · dato del 9 nov»; Rating MeLi 4.8
  «promedio de 30 listings con dato»; 574 reviews verificadas; 5 alertas «4 críticas · 1 aviso»; review
  MLM1890605671 · 3188730888 del «7 oct», 1 estrella, «Son mucho más pequeñas de lo que dicen las
  características, 13 mm aproximadamente.»; su alerta «17 oct, 04:30»; MLM1890586139 «rating 3.8 < 4.2»;
  4 preguntas de MLM2736115339, la más vieja «hace 116 d»; Cuenta MeLi 5_green, silver, 0 de 24.

### Precios
- Orden de la pantalla: «Seguridad — compuerta del motor de precios» primero, luego por mercado Goals,
  Cobertura, Con goal, Hecho y habria hecho, No evaluados, Precio divergente: `app/templates/precios.html:3-4`;
  `_precios_seguridad.html:6-45`; `_precios_goals.html`.
- Modos traducidos (Apagado, Práctica, En vivo) y grupos «FBA: En vivo, FBM: En vivo»:
  `_precios_seguridad.html:16-17`; `app/etiquetas.py:324`.
- Compuerta del día (total del día por canal, «fba 121 medidas, 0 nuevas»): `_precios_seguridad.html:21-26`.
- Cobertura cuenta solo lo que está a la venta (catálogo en vivo, `mercados.abrir`): `app/api_precios.py:220-238`.
  «sin objetivo» en el texto: `_precios_goals.html:75`.
- No evaluados = las mismas filas que la Cobertura, motivo en palabras: `app/api_precios.py:398-409`;
  `_precios_goals.html:143-161`. Línea aparte «Con objetivo pero sin estar a la venta: N»: `_precios_goals.html:145-147`;
  `app/api_precios.py:412-424`. Hoy cuadra: MX 164 = 164 filas (160 «sin decision», 4 «catalogo desactualizado»),
  US 92 = 92 filas (85 y 7).
- «sin decision» = publicación con goal sin decisión hoy: `app/precio/cobertura.py:121-122`; `app/etiquetas.py:513`.
- Día de la pantalla = día UTC de la base («Dia 2026-11-10 (reloj de la base)» a las 21:52 CST):
  `app/precio/fuentes.py:88-90`; `app/api_precios.py:48-49`; `_precios_goals.html:5`. La corrida diaria de
  Amazon es a las 13:10 UTC (7:10 hora del centro): `tests/test_precio_corrida.py:1452-1456`;
  `docs/evidencia/claridad-01/F/causa.md`. Los repasos (15:10 a 23:10 UTC) no vuelven a decidir.
- Estados Unidos: 99 de 99 `oferta_desactualizada` del 2 al 9-nov; arreglo desplegado el 9-nov, su primera
  corrida es la del 10-nov 13:10 UTC: `docs/evidencia/claridad-01/F/causa.md` (Evidencia y Arreglo).
- Mercado Libre aparece en Settings como «Mercado Libre · MELI»: `app/templates/settings.html:95-105`; GET /settings.
- Datos de hoy (`GET /precios`, `GET /api/dashboard/precios`): «Modo global: Práctica» en los dos mercados,
  US «FBM: En vivo», MX «FBA: En vivo, FBM: En vivo»; corrida #468 de MX (2026-11-09, Completa) «231 en el
  día», «Compuerta del día: fba 121 medidas, 0 nuevas · fbm 39 medidas, 0 nuevas»; corrida #470 de US «99 en
  el día», «Compuerta: sin publicaciones medidas en esta corrida»; «Sin retenciones abiertas hoy»; MX «197
  activas = 0 evaluadas + 164 no evaluadas + 33 sin objetivo + 0 fuera de alcance»; US «94 activas = 0
  evaluadas + 92 no evaluadas + 2 sin objetivo + 0 fuera de alcance»; «aviso: 4 publicaciones sin reportar
  hace mas de 3 dias» (MX) y 7 (US); «TACoS Amazon MX, octubre 2026: 8.69 %», «TACoS Amazon US, octubre 2026:
  22.12 %»; US 1B-J83K-7VFL «2026-10-31 · 91.13 → 92.23 confirmado».

### Familias
- La línea gris ahora dice «… Para guardar hace falta el token de escritura (campo Token).»:
  `app/templates/familias.html:4-8`; GET /familias.
- Meta con dos decimales (filtro `pct`): `familias.html:30, 39`; `app/formato.py:40-45`.
- Escalón «Margen de la familia» (texto de `margen_familia`): `app/etiquetas.py:269`.
- Datos de hoy: MX Arras matrimoniales / Arras MX / Arras US, 0 productos, Meta «—», «usa la meta del país»;
  «Productos · 249», todos «sin familia»; US «Sin familias en esta plataforma. Crea la primera abajo.»,
  «Productos · 119», todos «sin familia».

### Settings
- «Meta vigente: X % · sale de: <regla>»: `app/templates/settings.html:26-31`; textos de las reglas:
  `app/etiquetas.py:265-272`; «pisa a …»: `app/etiquetas.py:274-281`, `settings.html:170`.
- Párrafo gris reescrito: `settings.html:4-9`.
- Botón «Guardar configuración de Amazon US»: `settings.html:129`. Mensajes «Guardado: …», «Goal #N actualizado.»:
  `app/static/js/settings.js:122, 151`.
- Caps diarios Puja, Pausa, Negativo, Captura: `settings.html:121-127`; `app/etiquetas.py:283-290` (KIND).
- Grupos del motor de precios «Amazon MX · FBA», «Amazon MX · FBM», «Amazon US · FBM», «Mercado Libre · MELI»:
  `settings.html:95-105`.
- Metas de campaña «Meta #6 · Amazon US · …»: `settings.html:166-170`.
- Datos de hoy (`GET /settings`): «Config vigente: 51», «Modo global (solo lectura): En vivo»; Amazon US
  «19.22 %», «sale de: Margen de la plataforma», fracción 0.8; Amazon MX «20.02 %», Margen de la plataforma,
  fracción 0.5; Target manual 20 en los dos; caps 1000000; Floor/Ceiling 1.0000/45.0000 (MX) y
  0.1000/2.5000 (US); Motor de precios «Vive: Práctica»; ACoS objetivo del Resumen vacío en los tres;
  Meta #6 a #12 (2 US, 5 MX) sin Target ACoS, todas «pisa a la meta por margen de la plataforma».


## J.2, correcciones del lector fresco (`docs/evidencia/claridad-01/J/lector.md`)

- Casilla «todos los sin goal» de Goals, siembra y cambio: `app/templates/_precios_goals.html:54-57`; manda `sin_goal` en lugar de la lista marcada (`app/static/js/precios.js:80`, `app/api_precios_write.py:112-117`).
- Casilla Confirmar: solo en las filas `confirmable` de la vista previa de Planear (`app/static/js/precios.js:96-110`), umbral 25 % en `app/precio/siembra.py:61`.
- Motivo «catalogo desactualizado»: la publicación lleva más días sin reportar que el tope de la config (`app/precio/cobertura.py:112-114`, etiqueta en `app/etiquetas.py:515`). Prod `/precios`, No evaluados de Estados Unidos, SKU 0I-M6SQ-YYO7.
- Settings regla 3 apunta ahora a `{{pag:productos-sin-clics-meta}}`.
