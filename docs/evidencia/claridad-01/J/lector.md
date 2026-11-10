# Lectura fresca de la guía contra producción (lector J)

9 de noviembre de 2026, de las 22:04 a las 22:21 hora del centro de México. Leí las 42 hojas de `docs/manual-usuario/manual.html` y recorrí cada pantalla de http://10.13.13.1:8010 con `curl -s -m 60` (solo GET, incluidas las variantes `?plataforma=amazon_us`) y con `agent-browser --session lector-j` (open, wait, snapshot, eval de solo lectura y una captura). No pulsé ningún botón que guarde, lance, rechace, regrese, encienda, aplique, bloquee, quite, planee ni consulte el estado de un lote. No abrí ningún lote.

Resultado: 11 hallazgos. 0 ALTA, 3 MEDIA, 8 BAJA. Los recuadros «Tu cuenta» coinciden con la pantalla en todas las hojas que pude comprobar (3, 6, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 27, 28, 29, 30, 31, 35, 37, 38, 39 y 40). Las referencias cruzadas están bien salvo una.

## Hallazgos

1. **MEDIA · Hojas 22 / 42, contra 8, 33 y 39.** La guía dice en la hoja 22: «Junto a cada ACoS aparece entre paréntesis la meta de ACoS del mercado, por ejemplo «(meta 20.00 %)». Es el target.» Las hojas 8, 33 y 39 dicen que la meta de Amazon MX es 20.02 % y sale de Margen de la plataforma. En pantalla, Dónde poner el dinero muestra «(meta 20.00 %)» y Settings y Campañas muestran 20.02 %. Un lector puede creer que en esa pantalla manda otra regla (el Target manual de 20) o que la meta cambió. Es la misma meta redondeada a un decimal. Evidencia: `curl http://10.13.13.1:8010/donde-poner-el-dinero?plataforma=amazon_mx` y `curl http://10.13.13.1:8010/settings`. Código: `app/pantalla_dinero.py:328` (`quantize(Decimal("0.1"))`) sobre el valor de `app/api_dashboard.py:542` (`_target_margen_del_ciclo`). La guía debería decir que ahí la meta sale con un decimal menos.

2. **MEDIA · Hoja 13 (y 15).** La guía pone en la tabla «Lo que ves» de Propuestas: «Señal con «Jev: 16 de 16» y desactualizada». Hoy ninguna de las 32 propuestas muestra esa fila ni ese texto. La única columna Señal visible está en «Búsquedas que Jev marca como ajenas» (hoja 15) y dice otra cosa: «No corresponde a ninguno de los 208 productos; lista comprobada con el listado de Amazon del 2026-11-09. Bloquear, y pronto.» La hoja 15 no explica esa frase. El lector busca «Jev: n de n» y no lo encuentra; lo que sí ve, nadie se lo traduce. Evidencia: `curl http://10.13.13.1:8010/cortes` (el texto «Jev:» y «desactualizada» no aparecen en lo visible). Código: `app/templates/cortes.html:51-57` pinta la fila Señal solo cuando `item.senal` existe; `app/templates/_senal.html:8,20` arma «Jev: k de n» y el chip «desactualizada».

3. **MEDIA · Hoja 19.** Keywords dañadas abre con la línea «Calculado el 8 nov.» (sin hora, con fecha de ayer) en los dos mercados. La guía no menciona esa línea. Un lector que acaba de leer en la hoja 16 que «Calculado el» avisa si la lista está vieja puede pensar que esta pantalla lleva un día de atraso y esperar antes de regresar una puja. Evidencia: `curl http://10.13.13.1:8010/keywords-danadas` y `?plataforma=amazon_us`. Código: `app/pantalla_danadas.py:25` («medianoche UTC de HOY») y `app/templates/keywords_danadas.html:15` (solo imprime la fecha). Es la medianoche UTC, que en el centro de México cae el día anterior a las 18:00.

4. **BAJA · Hoja 11.** La guía dice de Cambios permitidos hoy: «saturada, en rojo, quiere decir que llegó al tope». En pantalla la columna Estado dice en las cuatro filas «Tope de la configuración», y la guía no explica ese texto. Evidencia: `curl http://10.13.13.1:8010/salud`.

5. **BAJA · Hoja 11.** La guía explica «49x http 404» y «red». La pantalla también muestra «9x http 400» (Precios de Amazon, US), «4x http 400» y «14x sin summary» (MX), el título de la tabla «SP-API», las columnas «Último límite de Amazon» y «Última falla de acceso», y en MX Inventario de Amazon trae «#1005 · 21 oct, 23:02» en Última falla de acceso aunque dice ok. Nada de eso se explica. Evidencia: `curl http://10.13.13.1:8010/salud`.

6. **BAJA · Hojas 2 y 41, contra Salud.** La hoja 2 dice que el ciclo «Corre solo, una vez al día en cada mercado, de madrugada». Salud muestra los ciclos #176 y #177 del «8 nov, 11:58» además de los de las 02:40. La hoja 41 sí dice que el recuadro «puede ser una extra de la tarde». Las dos hojas no cuadran entre sí. Evidencia: `curl http://10.13.13.1:8010/salud`, tabla Últimos 14 ciclos.

7. **BAJA · Hoja 39.** La regla «3 · Meta por producto» remite a la «página 25». La hoja 25 solo la lista en una tabla; la explicación (Margen y Meta, grupo exclusivo, «sin meta») está en la hoja 27. Evidencia: manual.html, hojas 25 y 27.

8. **BAJA · Hoja 6.** La tabla «Lo que ves» pone «▼ -0.83 pp y ROAS 7.41×» y el recuadro «Tu cuenta» pone «Amazon MX decía Bien con ACoS 13.32 %». Con ACoS 13.32 % el ROAS es 7.51×, que es lo que muestra la pantalla («▼ -1.20 pp», «antes 14.52 %», «ROAS 7.51×»). La fila parece una lectura real y no lo es; no está marcada como ejemplo. Evidencia: `agent-browser snapshot` de http://10.13.13.1:8010/.

9. **BAJA · Hoja 6.** La guía dice del punto 1 de Qué revisar: «Su enlace Ver gasto sin venta abre otra pantalla». En pantalla ese punto trae dos enlaces, «Ver gasto sin venta →» y «Busquedas que gastan sin vender →». El segundo no se menciona, y la hoja 18 es justo la que explica cuándo abrir cada uno. Evidencia: `agent-browser snapshot` de http://10.13.13.1:8010/.

10. **BAJA · Hojas 36 y 37.** En Goals, siembra y cambio hay una casilla «todos los sin goal» junto a Planear y Aplicar que la guía no explica. La hoja 37 dice «marca Confirmar» pero esa columna solo existe en la vista previa que abre Planear (no está en la página al cargar). En No evaluados de Estados Unidos aparece el motivo «catalogo desactualizado» (SKU 0I-M6SQ-YYO7), que tampoco se explica. Evidencia: `curl http://10.13.13.1:8010/precios`. Código: `app/static/js/precios.js:96-110` arma la casilla Confirmar solo si la fila es `confirmable`; `app/precio/siembra.py:61` fija el 25 %.

11. **BAJA · Hoja 22, contra hoja 5.** La hoja 5 dice «El día de hoy siempre queda fuera porque aún no termina» y el chip dice «Cifras hasta · MX: 2026-11-08». En Dónde poner el dinero la línea gris dice «Ventana por tipo 2026-08-11 a 2026-11-09», e incluye hoy. La hoja 22 repite «del 11 de agosto al 9 de noviembre» sin avisar que esta pantalla sí cuenta el día en curso. Evidencia: `curl http://10.13.13.1:8010/donde-poner-el-dinero?plataforma=amazon_mx`.

## Lo que no pude comprobar sin cambiar nada

Las hojas 32 y 33 describen el detalle del lote («87 pasos en Verificado con Activa», «Ver plan persistido del lote», «Estado actualizado.»). Abrir el lote consulta su estado en el servidor, así que no lo abrí. Tampoco pulsé Ver plan, Ver lanzamiento, Simular sin guardar ni Planear.

## Las preguntas que todavía le haría a un humano

1. ¿Por qué Dónde poner el dinero muestra la meta con un decimal menos (20.00 %) que Settings y Campañas (20.02 %)? ¿Se puede mostrar igual en las tres?
2. ¿Cuándo aparece la fila Señal «Jev: n de n» en Propuestas? Hoy no se ve en ninguna propuesta y la guía la presenta como algo que verás.
3. En Keywords dañadas, «Calculado el 8 nov.» ¿quiere decir que faltan datos de hoy, o es solo la medianoche UTC? ¿Conviene que la pantalla lo diga en hora del centro?

## Qué se corrigió en esta misma ronda

Los 11 hallazgos se corrigieron en las hojas, con la fuente en `docs/manual-usuario/notas/`.

1. Hoja de Dónde poner el dinero: la meta entre paréntesis lleva un decimal menos que en Settings.
2. Hoja de Propuestas: la fila Señal sale solo en propuestas que Jev revisó; la hoja de Rechazar explica la Señal de las búsquedas ajenas.
3. Keywords dañadas: «Calculado el 8 nov.» es el último día con cifras, no un atraso.
4. Salud: «Tope de la configuración» quiere decir que el tope sale de Settings.
5. Salud: otros motivos de la tabla y las dos columnas de fechas de fallas pasadas.
6. Glosario: además del ciclo de madrugada hay revisiones extra.
7. Settings regla 3: apunta a la hoja que explica la meta por producto.
8. Resumen: la fila de la tarjeta usa las cifras de hoy (▼ -1.20 pp, ROAS 7.51×).
9. Resumen: el punto de Qué revisar trae dos enlaces.
10. Precios: la casilla «todos los sin goal», Confirmar solo en la vista previa y el motivo «catalogo desactualizado».
11. Dónde poner el dinero: desde las 18:00 la ventana ya incluye el día de hoy.

Después de corregir, `python3 docs/manual-usuario/lint.py` dio 0 errores y `python3 docs/manual-usuario/build.py` armó las 42 hojas sin DESBORDA.
