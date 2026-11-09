# Lector escéptico — «Orbit - Entrenamiento completo.pdf» (38 págs.)

Comparé la guía contra producción (http://10.13.13.1:8010, solo GET) y contra `origin/master` (7cf7c9a), el 2026-11-08.

**Resultado: ningún BLOQUEANTE.** Revisé cada frase de confirmación que la guía cita contra la plantilla o el JS: LANZAR IMPULSO DE 1 PRODUCTO / 2 PRODUCTOS (singular/plural en `app/impulso_io.py:442`), REGRESAR 2 KEYWORDS, REGRESAR 1 ANUNCIOS, SACAR N ANUNCIOS, APLICAR AJUSTE, CREAR 5 CAMPAÑAS y PAUSAR/RECONCILIAR/REGISTRAR GRUPO. Todas coinciden letra por letra. Los botones que la guía presenta como de solo consulta (Ver plan, Ver lanzamiento, Planear, Simular sin guardar, el primer «Sacar de las bolsas compartidas», Usar pujas sugeridas, Consultar estado del lote) no escriben nada. Las reglas de 48 h, 30 días por omisión, 1–365, 7/20/14 y 330 días coinciden con el código. Las cifras de «Tu cuenta» coinciden con lo que la pantalla muestra hoy.

Abajo van solo los errores, del más grave al más leve.

---

1. **ALTA. El lote de Crear campañas aparece «en modo shadow», pero hoy esas cinco campañas están en live.**
   - Pág. 27: «En Amazon MX había un lote, del 9 de septiembre, tipo kit_arras y nombre Personalizado, en modo shadow». En la misma página: «En shadow Orbit solo observa y no ajusta pujas».
   - Lo correcto: el lote *se creó* en shadow, pero los goals de sus 5 campañas (#8–#12) están hoy en `mode: live`. Orbit sí ajusta sus pujas. Quien lea la guía va a creer que Orbit no las toca.
   - Evidencia: `curl -s http://10.13.13.1:8010/api/dashboard/settings`. Los goals 8, 9, 10, 11 y 12 («kit_arras | Personalizado | …») salen con `"enabled": true, "mode": "live"`. En /campanas, debajo de cada «kit_arras | Personalizado | …» dice «campaign · live · floor 1.0000 · ceiling 45.0000». `GET /api/fabrica/lotes?plataforma=amazon_mx` da `plan.modo = "shadow"`, que es solo el modo con que se creó.

2. **MEDIA. La pág. 12 y la pág. 25 se contradicen y la guía no explica por qué.**
   - Pág. 12: «Las otras 31 eran Apagar palabra sobre anuncios de producto … de la pausa por producto en Práctica». Pág. 25: «La pausa estaba en Práctica en los dos mercados, con 0 productos con propuesta».
   - Lo correcto: la sección Pausa por producto solo lee el **último** ciclo terminado. En ese ciclo (US #176, el de las 17:58) los 31 anuncios quedaron excluidos porque ya tenían una pausa en la cola (`clave_en_vuelo`). Por eso marca 0 mientras Propuestas muestra 31 pausas de práctica del ciclo #174. Falta una línea que diga que «0 propuestos» no quiere decir que no haya propuestas en la cola. Ese mismo bloque dice «3 **anuncios** propuestos · 0 confirmados», no «3 propuestos».
   - Evidencia: `curl -s 'http://10.13.13.1:8010/api/dashboard/productos-sin-clics?plataforma=amazon_us'` da `cycle_id 176`, `propuestas_pausa: 0` y `anuncios_excluidos: {"clave_en_vuelo": 31}`. `/api/dashboard/cortes` trae 31 filas `kind: pause` con `estado: pending_veto`. En el código: `app/pantalla_decisiones_producto.py:53-58` (`ORDER BY id DESC LIMIT 1`) y `app/templates/productos_sin_clics.html:219`.

3. **MEDIA. La cascada de Settings dice que tiene cinco reglas, pero son seis. Falta margen_producto.**
   - Pág. 35: «Orbit revisa cinco reglas en orden … 3 · margen_familia … 4 · margen_plataforma … 5 · setting_plataforma». Pág. 34: «Es el tercer escalón de la cascada de Settings».
   - Lo correcto: después del goal va `margen_producto` (la meta por producto, pág. 24) y luego `margen_familia`. La propia pág. 8 sí la pone en ese lugar, así que la guía se contradice. La pág. 35 debería decir seis procedencias e incluir margen_producto. También debe corregirse «las reglas 3 a 5» en el párrafo del goal de campaña.
   - Evidencia: `git show origin/master:app/optimizer/goals.py`, líneas 17-26 (la cascada) y 286-292 (`PELDANOS_CASCADA = ("goal_campana","goal_plataforma","margen_producto","margen_familia","margen_plataforma","setting_plataforma")`). El filtro Procedencia de /campanas ofrece `margen_producto`.

4. **MEDIA. Campañas no muestra todas las campañas.**
   - Pág. 7: «Campañas muestra todas tus campañas de Amazon con su gasto…».
   - Lo correcto: solo aparecen las campañas que tienen cifras en los últimos 30 días. «A1U - Category Exact - US» y «AU2 - Category Exact - US» no salen en Campañas. Sin embargo, aparecen en Propuestas, en Decisiones, en Contribución y como goals #6 y #7 en Settings. Un usuario que las busque no las va a encontrar ahí.
   - Evidencia: `curl -s http://10.13.13.1:8010/api/dashboard/campanas` trae 36 items y ninguno es «A1U - Category Exact - US». `grep -c "A1U - Category Exact - US"` en /decisiones da 4. En el código: `app/api_dashboard.py:181-194` (`_SQL_CAMPANAS_30D … AND v.metric_date BETWEEN %s AND %s`).

5. **MEDIA-BAJA. «Target efectivo» no sale del último ciclo con cambios, sino del último ciclo en live.**
   - Pág. 8: «El Target efectivo es el ACoS meta que Orbit usó … en su último ciclo con cambios reales en Amazon».
   - Lo correcto: sale del último ciclo **en live que terminó**, aunque no haya cambiado nada. Hoy sale del ciclo #176, que tomó 0 decisiones.
   - Evidencia: `/api/dashboard/campanas` da `target_efectivo.ciclo.id_max = 176`, y en Salud el #176 aparece con `decisiones: 0 · applies: 0`. En el código: `app/api_dashboard.py:836-840` («lo que el ultimo ciclo live+done congelo»).

6. **BAJA. «Orbit movió el precio» se refiere a un ajuste de ubicación, no al precio del producto.**
   - Pág. 21: «“Orbit movió el precio hace 3 días; espera al día 7 para juzgarlo.” quiere decir que el precio cambió hace poco».
   - Lo correcto: el aviso solo sale después de un **Cambiar ajuste de ubicación** que Orbit aplicó (el precio por clic según la ubicación). No habla del precio del producto, y un usuario lo puede confundir con la pantalla Precios.
   - Evidencia: `git show origin/master:app/avisos_campana.py`, líneas 125-134 (`if clase != "ajuste_ubicacion": return None`).

7. **BAJA. La meta por producto la pisa un goal con target escrito, no cualquier goal.**
   - Pág. 24: «Un goal, la meta fijada a una campaña, manda sobre ella».
   - Lo correcto: solo la pisa un goal **con Target ACoS escrito**, sea de campaña o de plataforma. La pantalla lo dice así: «Un goal de campaña con target manda sobre ella». Los 7 goals de campaña de hoy no tienen target, así que no la pisan.
   - Evidencia: `app/templates/productos_sin_clics.html` (bloque Meta por producto, «Un goal de campaña con target manda sobre ella») y `app/optimizer/goals.py:17-21`.

8. **BAJA. Dos referencias cruzadas apuntan a la página equivocada.**
   - Pág. 12: «Las pausas por producto seguían en Práctica … (página 22)». La pausa por producto se explica en la **página 25**.
   - Pág. 16: «usas Bloquear en Propuestas si la búsqueda aparece como ajena (página 12)». «Búsquedas que Jev marca como ajenas» y Bloquear están en la **página 13**.
   - Evidencia: el índice del propio PDF (pág. 25 «La pausa por producto», pág. 13 «Otras secciones de Propuestas»).

9. **BAJA. El motor de precios también cubre Mercado Libre.**
   - Pág. 32: «live cambia precios reales en Amazon» y el diagrama «Amazon · Cambia el precio solo en live».
   - Lo correcto: la configuración del motor incluye `meli/meli: live`, y Settings muestra «meli/meli» en Motor de precios (global). No verifiqué si hoy mueve precios de MeLi, porque la pantalla Precios solo muestra amazon_us y amazon_mx. Conviene decirlo o aclarar el alcance.
   - Evidencia: `/api/dashboard/settings` da `"precio_modo_universo": {"meli/meli": "live", …}`. En el código: `app/precio/corrida.py:596` (rama MeLi).

10. **BAJA. «Qué revisar» no ordena por dinero, sino por impacto.**
    - Pág. 5: «Qué revisar lista los problemas del periodo, del más caro al más barato».
    - Lo correcto: la pantalla dice «Ordenado por impacto». El punto 2 de hoy es «Caida de gasto en Mercado Libre», que no es un costo.
    - Evidencia: snapshot de / con `agent-browser` y `app/templates/resumen.html:188`.

11. **BAJA. La guía no dice qué escribir en «Go literal».**
    - Pág. 33: «En live pide también Go literal, un texto que autoriza cambiar precios reales».
    - Lo correcto: el campo existe (placeholder «solo live»), pero la guía no dice qué texto va ni quién lo da. Sin eso, el usuario no puede completar Aplicar en live. Debería decir «pídelo al responsable», igual que con el Token.
    - Evidencia: `app/templates/_precios_goals.html:42` y `app/api_precios_write.py:128`.

12. **BAJA. Algunas etiquetas no se ven igual en pantalla.**
    - El menú dice «Contribucion», «CATALOGO» y «Reputacion», sin acento. La guía escribe «Contribución», «Catálogo» y «Reputación» (págs. 1, 3, 30: «En el menú Catálogo abre Reputación»).
    - Pág. 35, «amazon_mx mostraba Target vigente 20.02»: la pantalla muestra «20.02023058311879485576605025», y en US «19.36610636923199718608899984».
    - Evidencia: snapshot de /settings y de / con `agent-browser --session esceptico`.
