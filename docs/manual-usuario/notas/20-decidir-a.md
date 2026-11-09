# Fuentes de 20-decidir-a.html

Código leído de `origin/master` (7cf7c9a, lo que corre en producción), extraído a
`scratchpad/master-src/`. El checkout local está en la rama `resumen-ads-04-diseno`, que va
atrás de master. Las plantillas de estas cinco pantallas son iguales en las dos ramas, pero
`api_dashboard.py`, `apply_cola.py`, `api_write.py` y `ui.py` sí cambian. Rutas relativas al repo.
Producción: `http://10.13.13.1:8010`, solo GET, el 8-nov-2026.

## Menú y contador
- Ids y subtítulos: `app/templates/base.html:27-31` y `:53-57`. El contador va en `:66` (`propuestas-contador`), y la barra del celular repite el badge en `:176-182`.
- Número junto a Propuestas = `datos.items.length` de `GET /api/dashboard/cortes`: `app/static/js/shell.js:26-35`. Si la lectura falla, muestra «—». Con 0 propuestas muestra «0» (pintarBadge se llama siempre).
- `items` = filas de `apply_queue` en `pending_veto` o `released`, de los dos mercados, sin filtrar por modo: `app/api_dashboard.py` `_SQL_CORTES_PENDIENTES` (master ~l.1289-1310). No cuenta `propuestas_campana` ni las búsquedas ajenas de Jev.
- Prod: `/api/dashboard/cortes` → 32 items (31 pause + 1 negative, todos amazon_us, todos pending_veto, vence 2026-11-10T08:40Z, encolados 2026-11-08T08:40Z), 0 propuestas_campana, bloqueos_jev con interruptor true, propuestas [] en MX y US, bloqueos [].

## Propuestas (/cortes)
- Plantilla: `app/templates/cortes.html`. Crecen/Recortan `:95`, `:115`. Columnas Tipo, Entidad, Search term, Por que, Vence, Accion `:100-105`. «se aplica solo el …» `:32`. Botón Rechazar `:35`. Formulario con efecto, «Este rechazo NO se puede deshacer y bloquea la propuesta hasta el {{ item.vence_el }}», Dias 30 (1-365), Actor, Token, Confirmar rechazo `:72-90`. Mercado en detalle de Entidad `:22`.
- Etiquetas por tipo y efecto del rechazo: `app/api_dashboard.py` `ETIQUETA_POR_KIND`, `DIRECCION_POR_KIND`, `EFECTO_RECHAZO_POR_KIND` (~l.1325-1340).
- La hora se pinta sin zona (`ts_ui`, `app/ui.py:135-140`), y el valor es UTC (`vence_el` ISO +00:00 en la API). La diferencia de 6 h con CDMX está en `paginas/90-cierre.html`.
- Ventana de 48 h: `app/apply_cola.py:108` (`VENTANA_VETO = timedelta(hours=48)`), y `vence = ahora + VENTANA_VETO` al encolar (~l.530 en master). Docs: `docs/APPLY.md` §1.4.
- Solo pause/negative/harvest pasan por la cola. Los bids se aplican en su ciclo: `docs/APPLY.md` §1.1.
- Al vencer: `libera_vencidos` vuelve a validar antes de aplicar y descarta si ya no procede (docstring en `app/apply_cola.py`, master ~l.1514+). Solo selecciona `modo = 'live'`: `_SQL_VENCIDAS` en master l.198-204. Las filas shadow nunca se aplican.
- Harvest = negative en el grupo de origen + keyword nueva: `_payload_term` en `app/apply_cola.py` (primer paso de la cadena).
- Pausa por producto: el anuncio de producto es live solo si la config trae la pausa en live (`_modo_pausa_producto`, master `app/apply_cola.py:518-537`). Si se apaga, los pendientes se descartan.
- Estado vigente: `GET /api/dashboard/productos/pausa?platform=amazon_us` → `{"modo":"shadow","modo_texto":"Práctica"}`, y lo mismo en amazon_mx. Salud (`/api/dashboard/salud`), ciclo 176 US: `productos.modo_pausa = "shadow"`, `anuncios_excluidos.clave_en_vuelo = 31`. Las 31 pause son decisiones `motivo_es = producto_sin_ventas` del ciclo 174 (`/api/dashboard/decisiones?plataforma=amazon_us`).
- Inferencia: las 31 filas son shadow porque la config está en shadow y apagarla habría descartado las live. La API no expone el modo por fila.
- El aviso de Telegram sí distingue la sombra («No se aplica: la propuesta está en sombra»): `app/jev_vista.py` `_linea_aplica`. La pantalla no.
- Veto: `app/api_write.py` `_SQL_VETO` (vence_el = now() + N días, estados pending_veto/released) y `veto()`, que devuelve 409 «en vuelo» si está applying. Mensaje «Rechazado hasta …»: `app/static/js/cortes.js:30-31`.
- Re-propone al vencer el bloqueo: `docs/APPLY.md` §1.4 (fin).
- Campañas: `cortes.html:139-206` («Pausar a mano en Amazon; Orbit no toca campañas.», Descartar → Actor + Token, «NO se puede deshacer y NO toca Amazon»). Endpoint `app/api_write.py` `descartar_propuesta`, docstring.
- Búsquedas ajenas de Jev: `cortes.html:210-277` (Bloquear / Rechazar con Token solo). Tus bloqueos / Quitar `:278-329`. Efectos: `app/bloqueo_dueno_efecto.py` (docstring: «Efecto del bloqueo que el dueño aprueba: ledger, Amazon…»). `rechazar` inserta un veredicto 'rechazado', `quitar` hace la reversa en Amazon (`_quitar_con_candado`).
- Señal «desactualizada»: `app/templates/_senal.html:8`.

## Gasto sin venta (/gasto-sin-venta)
- Plantilla: `app/templates/gasto_sin_venta.html`. Mercado `:11-12`, «Calculado el» `:15`, aviso de 36 h `:18`, «Productos que dijeron que sí» `:40`, vacío «Sin filas en este mercado…» `:70`. Sin botones.
- Filas: señales vigentes con `ordenes = 0 AND gasto > 0`: `app/jev_salud.py:257-279`. Aviso de más de 36 h: `app/api_dashboard.py` `_calculo_viejo`.
- Títulos, bandas y frases: `app/jev_vista.py:133-216` (`TITULO_POR_LECTURA`, `TITULO_POR_BANDA`, `FRASE_POR_LECTURA`, frase de ajena «Bloquear, y pronto», sin_lectura «Un dato faltante no es cero»). «Jev: k de n»: `_senal.html:20`.
- Prod: `/api/dashboard/gasto-sin-venta?plataforma=amazon_mx|amazon_us` → `calculado_el 2026-10-21T09:34:20Z`, `aviso_desactualizado true`, `total_filas 0` en los dos.

## Búsquedas que gastan sin vender (/busquedas-sin-venta)
- Plantilla: `app/templates/busquedas_sin_venta.html` (frase de azar `:13`, Páginas de producto ajenas `:18`, «es un producto tuyo» `:33`, Palabras y frases `:47`, A medio camino `:78-83`). Solo lectura: docstring en `app/ui.py` `pagina_busquedas_sin_venta`.
- Lógica: `app/pantalla_busquedas.py` (ventana de 90 días que termina hoy-3 en `lee_busquedas`, solo campañas ENABLED en `_SQL_BUSQUEDAS`, mínimo = gasto para concluir, medio camino = mitad del mínimo, palabras de una o dos con todas sus búsquedas en 0 pedidos en `_resumenes_palabra`, azar en `_referencia`).
- Mínimo por omisión 350 MXN / 36 USD: `app/optimizer/goals.py:812-830` (`GASTO_PARA_CONCLUIR_DEFAULT`, l.800). No se pudo leer si prod tiene otro valor guardado (`/api/dashboard/settings` no lo expone). Los datos cuadran con el default: hay filas de 407.68 MXN y 36.26 USD dentro de la lista.
- Prod MX: referencia visto 39 / azar 29. ASIN B0C11QJPKV 120 clics 655.79 MXN. B0CJT6KW6M con es_propio true. «de arras» 30 búsquedas 957.44 MXN. «estuche para» 9 búsquedas 735.35 MXN. US: 62 / 59.

## Keywords dañadas (/keywords-danadas)
- Plantilla: `app/templates/keywords_danadas.html` (orden «con más venta previa arriba» `:5`, Regresar todas con frase REGRESAR N KEYWORDS + Actor + Token `:18-34`, tarjeta `:39-41`, «Regresada el» `:43`, botón «Regresar el bid a …» `:46`, texto «Orbit cambia ahora el bid en Amazon … espera 7 días … 20 clics … 14 días … nunca la baja del bid que la dañó» `:49`, Confirmar regreso `:52`).
- Definición (vendía ≥1 pedido en los 90 días previos, clics de los últimos 14 días < 30 % de los 14 previos): `app/pantalla_danadas.py:1-27` y `:161`.
- Mensajes de resultado «Regresado a … » y «Regresadas N de M»: `app/static/js/danadas.js:32-33` y `:79-80`. Endpoints `app/api_write.py:439-470`.
- Prod MX: 3 hojas. sustituto (ASEM - Auto Discovery - MX, 5 recortes, 3.95 → 2.09, clics 8 → 0, 3 pedidos, 2193.09). «mismo ASIN B086TVLJ43» ya_regresada 2026-10-11. charola para arras pendiente. Son 2 pendientes, de ahí REGRESAR 2 KEYWORDS. US: «arras de boda cristiana» hoja 5338, 0.68 → 0.23, 5 recortes.

## Ruido (/ruido)
- Plantilla: `app/templates/ruido.html` (Por ciclo `:13-39`, «Shadow, no aplicado» `:34`, Encogimiento `:40-48`, «Quedó en» con porcentaje_ui `:44`, Antes y después «No es causal» `:49-69`).
- Lógica: `app/pantalla_ruido.py` (solo decisiones de bid con caso, shadow aparte, abstenciones = `notes.skips.entidad`, ciclos en orden ascendente `_SQL_CICLOS ORDER BY c.id`, encogimiento = bid de hoy / bid antes del cambio más antiguo en 90 días, comparación de 30 días antes del encendido contra los días posteriores).
- «azar_lo_explica» = el motor no tiene confianza suficiente para subir ni bajar: `app/optimizer/politica.py:312-340` (`_juzga_vendedora`, último `return Mantener(MOTIVO_AZAR_LO_EXPLICA)`).
- Traducciones de motivos: `MOTIVOS_ES_DECISIONES` (bajo_target +15 %, grupo_sangra_vendedora -12 %, regreso_por_desplome) y `MOTIVOS_ES_SALUD` (azar_lo_explica, grupo_cumple, sin_evidencia, entidad_inerte = sin impresiones en 14 días, campana_no_enabled) en `app/api_dashboard.py`.
- Prod: `/api/dashboard/ruido?plataforma=amazon_mx` → 89 ciclos (del 2 al 177). Ciclo 177 (2026-11-08 17:58Z) sin decisiones, azar_lo_explica 22, grupo_cumple 23, sin_evidencia 33. Encogimiento: hoja 3861 13.64 → 3.71 (0.27199 → «27.20 %»). Antes y después: encendido 2026-10-11, antes 30 días ACoS 14.42, después 28 días ACoS 13.57. US: ACoS 33.70 → 50.93, hoja 5338 razón 0.33823. Desde el 25-oct (ciclos con started_at ≥ 2026-10-25): MX 8 decisiones (7 bajo_target, 1 grupo_sangra_vendedora), US 16.

## Ronda de correcciones (lectores novato y escéptico), 8-nov-2026
Código de `origin/master` 7cf7c9a (copias en `scratchpad/fixa-src/`). Producción solo con GET.

- Hoja nueva «¿Esta pausa es de práctica o real?» (`data-ancla="cortes-practica"`). Ya no afirma que las 31 sean de práctica; dice que la tabla no distingue.
  - Si la pausa está en Práctica al vencer, no se ejecuta: `_SQL_VENCIDAS` solo toma `modo = 'live'` (`app/apply_cola.py:198-204`); al vencer, `_modo_efectivo_corte` ≠ live descarta con `modo_no_live` (`:1597-1607`); el modo de la pausa por producto es live solo con la config en live (`_modo_pausa_producto`, `:518-537`, docstring «apagar la pausa descarta sus pendientes … en sombra la fila nace shadow»). Una fila nacida en sombra no se vuelve live si después se enciende, por eso «En vivo» se trata como «puede ser real».
  - Textos «Práctica» / «En vivo» / «Apagada»: `app/pantalla_decisiones_producto.py:96`. Recuadro «Encendido de la pausa»: `app/templates/productos_sin_clics.html:307-316`.
  - «0 de N con propuesta»: Pausa por producto lee solo el último ciclo done (`app/pantalla_decisiones_producto.py:53-59`, `ORDER BY id DESC LIMIT 1`); un anuncio con pausa en cola se excluye con `clave_en_vuelo` («el anuncio ya tiene una pausa en la cola o un veto vigente», `app/optimizer/productos.py:141`; `app/productos_io.py:445`).
  - Prod: `GET /api/dashboard/productos/pausa?platform=amazon_us|amazon_mx` → `modo_texto "Práctica"`. `GET /api/dashboard/productos-sin-clics?plataforma=amazon_us` → `cycle_id 176`, `propuestas_pausa 0`, `anuncios_excluidos.clave_en_vuelo 31`. `GET /productos-sin-clics?plataforma=amazon_us` → «Pausa por producto 0 de 119 con propuesta», «Ciclo 176 · la pausa está en práctica: las propuestas quedan registradas y no pausan ningún anuncio». `GET /api/dashboard/cortes` → 31 pause + 1 negative, pending_veto, encolados 08:40.
  - Referencia a la hoja de la pausa por producto: `{{pag:productos-sin-clics-pausa}}` (ancla del otro ayudante).
- «pending_veto · encolado …» bajo Vence: `app/templates/cortes.html:32`; pending_veto es el estado que todavía acepta veto (`_SQL_VETO` en `app/api_write.py`).
- «Por que» con «—»: solo harvest trae indicador (`cortes.html:26-31`, `_indicador_harvest` en `app/api_dashboard.py:1351+`).
- Jev definido en la hoja de Propuestas antes de usarse: «Jev: k de n» = satisfacen de evaluados (`app/templates/_senal.html:20`), «desactualizada» = señal no vigente (`_senal.html:8`).
- Texto del rechazo para cualquier pause, también de anuncio: `EFECTO_RECHAZO_POR_KIND["pause"]` en `app/api_dashboard.py:1344-1348`.
- Ancla `data-ancla="cortes-rechazar"` en «Rechaza una propuesta». La hoja de las dos pantallas apunta ahí para Bloquear.
- «a mano en Amazon» = consola de Amazon Ads, dentro de la campaña: `cortes.html` sección Campañas («Pausar a mano en Amazon; Orbit no toca campañas»). Orbit bloquea con negative exacto: `app/ads/write.py:378-393` (`matchType NEGATIVE_EXACT`). Que un negative de frase sobre una palabra suelta bloquee toda búsqueda que la contiene es comportamiento de Amazon, no de Orbit (no verificable en el repo).
- Gasto sin venta: el mismo nombre en Resumen (días, `app/ui_metricas.py:407-447`) y en Campañas (campañas con gasto y venta 0, `app/ui_campanas.py` `estado_de`). Mensaje vacío «Sin filas en este mercado: el job sigue apagado o nada gasta sin vender»: prod `GET /gasto-sin-venta?plataforma=amazon_mx`. Ya no afirma «Jev no corría desde el 21 de octubre»; dice lo que se comprueba hoy, «Calculado el 2026-10-21 09:34». Salud «Encendido» = interruptor (`app/templates/salud.html:183-186`).
- Keywords dañadas: la racha puede ser de 1 recorte (prod US «wedding coins gold … tras 1 recortes»); «mismo ASIN B086TVLJ43» Regresada el 2026-10-11 con «Bid: 36.99 antes, 36.99 hoy» y 0 clics (prod `GET /keywords-danadas?plataforma=amazon_mx`). Consejo de avisar si sigue sin clics: criterio de la guía, no regla de Orbit.
- Ruido: 89 tarjetas en MX y la última va justo antes de «Encogimiento por hoja» (`app/templates/ruido.html:13-40`, ciclos en orden ascendente en `app/pantalla_ruido.py`). Hoja 5338 = «arras de boda cristiana»: `GET /api/dashboard/keywords-danadas?plataforma=amazon_us` (`hoja_id 5338`, 0.68 → 0.23) y `GET /ruido?plataforma=amazon_us` («Hoja 5338 · Bid: 0.68 antes, 0.23 hoy (USD)»). Motivos nuevos en la tabla con su traducción de `MOTIVOS_ES_SALUD` (`app/api_dashboard.py:272-330`): esperando_efecto, sin_clics_nuevos, sin_gasto, venta_reciente, dato_faltante, estado_no_enabled. Salud traduce los skips del último ciclo con ese mismo diccionario.
- «Antes y después del encendido» pasó a la primera hoja de Ruido por espacio; el texto no cambió.
