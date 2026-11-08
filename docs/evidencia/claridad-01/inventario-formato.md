# Inventario de formato (2026-11-08, origen/master 7cf7c9a)

Casi todo se arma en el servidor con Jinja; JS solo pinta gráficas y partes interactivas.

| Problema | Pantalla | Dónde se imprime | Quién entrega el dato |
|---|---|---|---|
| Meta con 26 decimales | Decisiones «Target usado» | `decisiones.html:21` | `api_dashboard.decisiones_pagina` |
| Meta con 26 decimales | Settings «Target vigente» | `settings.html:28` | `api_dashboard` (settings) |
| `producto_sin_ventas` | Decisiones «Motivo» | `decisiones.html:20` | `MOTIVOS_ES_DECISIONES.get(m, m)`, `api_dashboard.py:214` |
| Procedencias | Campañas | `campanas.html:82`, `:154` | `ui_campanas.presenta_campanas`; `_ETIQUETA_PISA` (`api_dashboard.py:1820`) no se usa ahí |
| Procedencias y plataforma | Settings | `settings.html:24`, `:29` | cascada de settings |
| Procedencias y motivos | Productos sin clics | `productos_sin_clics.html:176` | `pantalla_productos.py` |
| `con_ventas_previas`, `gasto_sin_ventas`, `peso_muerto` | Inertes | `inertes.html:43,79` | vista `v_entidad_inerte` (0013) |
| `automatica`, `product_targeting` | Dónde poner el dinero | `donde_poner_el_dinero.html:24` | `pantalla_dinero.TipoCampana` |
| `sin_pedidos` | Productos sin clics, Retiros | `productos_sin_clics.html:65,72` | `retiro_anuncios.MotivoRetiro` |
| `amazon_us` como título | Salud, Familias, Propuestas, Reputación | `salud.html:7` y otros | `_NOMBRE_PLATAFORMA` existe en `ui_campanas.py:32`, solo lo usa Campañas |
| `oferta_desactualizada`, `ventas_sin_dato:historia_corta` | Precios | `_precios_goals.html:148`, `api_precios.py:324` | `motivo_precio_es` (`notifica.py:1147`), sin la clave ni soporte de `a:b` |
| «(catalogo_desactualizado)» | Precios, cobertura | — | `precio/cobertura.py:162-163` mete el código en la frase |
| Horas UTC sin aviso | Decisiones, Propuestas, Ruido, Keywords dañadas, Gasto sin venta | filtro `ts_ui` (`ui.py:134`) | — |
| Microsegundos | Salud (40), Inertes, Productos sin clics, Propuestas | `salud.html:53,122,123`, `cortes.html:66,68` | `api_dashboard.salud` |
| Sin separador de miles | Contribución, Dónde poner el dinero, Precios, Resumen, Ruido, Keywords dañadas, Búsquedas | `dinero_ui` (`ui.py:81`, 34 usos); `resumen.html`, `busquedas_sin_venta.html:35,66`, `precios.js:45,52` | — |
| Subtítulos técnicos | todas | `base.html:21-41` | — |
| Párrafos técnicos | Settings, Contribución, Campañas, Salud | `settings.html:5-7`, `contribucion.html:4`, `campanas.html:132-133`, `salud.html:98` | — |
| Skips: etiquetas saltadas y orden alfabético | Salud | `dashboard.js:119,296`; `tojson` ordena claves | `_skips_traducidos`, `api_dashboard.py:1070` |
| Keyword regresada sigue como tarjeta | Keywords dañadas | `keywords_danadas.html:37` | `pantalla_danadas.lee_danadas` |
| «Hoja 3861» sin nombre; ciclos ascendentes; 26 códigos | Ruido | `ruido.html:14,20,29,43` | `pantalla_ruido` (`_SQL_CICLOS :55`, `_encogimiento :189`) |
| Estado y modo crudos, hora UTC | Menú «Último ciclo» | `shell.js:53-77` | `/api/dashboard/salud` |
| JSON crudo como nombre de hoja | Productos sin clics | — | falta `etiqueta_entidad` |

Diccionarios sueltos (unos 20), todos con `.get(codigo, codigo)`: `MOTIVOS_ES_DECISIONES`,
`MOTIVOS_ES_SALUD`, `_ETIQUETA_PISA`, `ETIQUETA_POR_KIND`, `EFECTO_RECHAZO_POR_KIND`,
`MOTIVO_PRECIO_ES`, `ESTADO_PRECIO_ES`, `CAUSA_COMPUERTA_ES`, `_ESTADO_CAMBIO_ES`,
`_NOMBRE_PLATAFORMA`, `TIPOS_TARGET`, `MODOS_TEXTO`, `ETIQUETAS_CANAL`, `NOMBRES_UBICACION`,
`_NOMBRE_UBICACION`, `_TEXTO_CLASE_AJUSTE`, `ETIQUETA_ABSTENCION`, mapas de `jev_vista`,
`resumen.html:4,17`, 7 mapas en `fabrica.js:8-37` y 2 en `productos.js:229,240`.
