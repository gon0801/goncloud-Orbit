# Lector ejecutor 02: ARREGLOS 02 (bloques A, D, B y C) y los parches a TEMPORADA 01

Copia revisada: `la copia de trabajo del plan`, rama `plan/arreglos-02`, código de master `a4644aad`, con el plan, el diseño y los parches sin commit.
Método: corrí cada `Acepta` hoy, leí cada `archivo:línea` citado y armé un prototipo fuera del repo: una copia de `app/` con B y C aplicados tal como los describe el diseño, y la migración de D construida con las reglas de la fila D.2 sobre una base temporal de Postgres. Las lecturas de producción se hicieron con `lee_prod.sh`, solo con SELECT.

Resumen: 3 bloqueantes y 10 no bloqueantes.

## Bloqueantes

### 1. Las pruebas de B usan `REVISION_POLITICA`, C sube esa constante a 2 y la prueba de la 2865 se rompe en el bloque C

- Dónde: plan, fila B.1 (`test_arreglos02_b_piso_aprendido_en_centavos` y `test_arreglos02_b_el_redondeo_no_pasa_del_paso` usan `replace(caso, revision=REVISION_POLITICA)`), y fila C.2 (`REVISION_POLITICA = 2`).
- Qué pasa: con la revisión 2, la decisión 2865 ya no da `Mover(..., 0.30)`. Da `Mantener("vende_dentro_del_margen")`, que es justo lo que C.1 quiere. La prueba de B para `[2865]` falla en cuanto C sube la constante. Los `Acepta` de C.2 y C.3 corren `tests/test_optimizer_politica.py` completo, así que C sale en rojo por una prueba de B. Además, el encabezado del plan dice «si el código real contradice el diseño, para el bloque», y el ejecutor puede parar ahí.
- Evidencia: prototipo fuera del repo con B y C del diseño, corrido sobre `decision_2865.json`:
  ```
  2865 rev0 reproduce ('bid', Decimal('0.290400'), 'USD')
  2865 rev1 Mover(factor=Decimal('-0.12'), bid_nuevo=Decimal('0.30'), motivo='grupo_sangra_vendedora', nivel='ad_group')
  2865 rev2 Mantener(motivo='vende_dentro_del_margen')
  ```
  (2866 y 2904 dan 0.43 y 0.27 con las dos revisiones; solo se rompe la 2865.)
- Corrección pegable (fila B.1): cambiar «y con `replace(caso, revision=REVISION_POLITICA)` `decide` da `Mover`» por «y con `replace(caso, revision=1)` `decide` da `Mover` (el 1 va escrito, no la constante: C sube `REVISION_POLITICA` a 2 y con 2 la 2865 da `Mantener`)». En `test_arreglos02_b_el_redondeo_no_pasa_del_paso`, cambiar «y `revision=REVISION_POLITICA`» por «y `revision=1`».

### 2. Tres pruebas de hoy fijan la falla de C.3, y el plan no dice que se cambien

- Dónde: plan, fila C.3. Su `Acepta` (y el de C.1 y C.2) corre `tests/test_optimizer_caso.py` y `tests/test_optimizer_politica.py` completos.
- Qué pasa: con `direccion` en 0 para un ajuste y el retorno temprano en `_freno_trayectoria`, tres pruebas de hoy fallan porque afirman el comportamiento viejo. Una de ellas afirma `Mantener("esperando_precio_medido")`, que C.1 describe como «lo que da hoy» y quiere quitar. El plan sí avisa de pruebas que hay que cambiar en B.3, pero no en C.3, así que el ejecutor puede tomar el rojo como una contradicción y parar el bloque.
- Evidencia: la suite de hoy contra el prototipo con B y C aplicados:
  ```
  FAILED tests/test_optimizer_politica.py::test_ajuste_escalera_no_proyecta
  FAILED tests/test_optimizer_politica.py::test_ajuste_no_dispara_r1
  FAILED tests/test_optimizer_caso.py::test_direccion_subida_bajada_y_ajuste   (assert 0 == 1)
  3 failed, 319 passed
  ```
  Con el prototipo, `_caso_ajuste(10)` da `Mover(-0.12, 8.80, "gasto_sin_venta", "hoja")` y `_precio_de_hoy` da `ventana_madura`.
- Corrección pegable (agregar al final del contenido de C.3): «Tres pruebas de hoy fijan la falla y se cambian en este bloque: `tests/test_optimizer_caso.py::test_direccion_subida_bajada_y_ajuste` (10.00 → 10.00 da 0); `tests/test_optimizer_politica.py::test_ajuste_no_dispara_r1` (conserva `not isinstance(veredicto, Regresar)` y ahora espera `Mover(Decimal("-0.12"), Decimal("8.80"), "gasto_sin_venta", "hoja")`); `::test_ajuste_escalera_no_proyecta` (afirma `_precio_de_hoy(caso)[1] == "ventana_madura"`, que es lo que su nombre promete, en vez del veredicto).»

### 3. Los 7 parches de B a TEMPORADA 01 no están aplicados y ninguna fila los aplica

- Dónde: diseño, sección `## B`, «Cruces con TEMPORADA 01, con parche», puntos 1 a 6. Plan, línea 20: solo menciona los parches de D. `git diff -- docs/evidencia/temporada-01/DISENO.md` trae solo los de D.
- Qué pasa: el diseño de TEMPORADA sigue pidiendo que `temporada.py` defina su propio `CENTAVO` y que `caso.py` cree `_objeto_con_opcionales` con opcionales `{"temporada", "historia", "recuperacion", "presupuesto"}`, sin `"revision"`. B ya habrá creado esa función. Si el ejecutor de TEMPORADA sigue su contrato, que según su plan es el único, el `desde_json` rechaza todos los casos que se guarden después de B, porque traen `"revision"`. El replay de esas decisiones se cae y las pruebas de ida y vuelta del caso salen en rojo. También sigue diciendo «solo R17» en `_freno_trayectoria`, cuando B quita R17 de ahí, y llama `_paso(..., techo_extra=...)` sin el `centavos=` que B vuelve obligatorio.
- Evidencia:
  ```
  $ rg -n 'CENTAVO = Decimal|"temporada", "historia", "recuperacion", "presupuesto"|solo R17|_paso\(caso.bid' docs/evidencia/temporada-01/DISENO.md
  499:CENTAVO = Decimal("0.01")
  694:    #                                    {"temporada", "historia", "recuperacion", "presupuesto"})
  1091:    #   ultimo.origen == "temporada"                      -> solo R17 (R2 ya pasó)
  1096:    # _paso(caso.bid, factor, motivo, nivel, piso_aprendido, techo_extra=techo_de_baja si factor > 0)
  1566:- Si el último cambio fue de temporada, solo R17: ...
  ```
  Y el conjunto de claves de TEMPORADA contra un caso de un ciclo posterior a B (prototipo con `_CLAVES_CASO` real y la 2865 con `"revision": 2`): `acepta ...: False`.
- Corrección pegable: aplicar en este mismo PR los puntos 1 a 6 del diseño. Las líneas del diseño de ARREGLOS 02 apuntan al archivo antes de los parches de D y ahora van una línea antes: `:500` es `:499`, `:694` es `:693-694`, `:787` es `:786`, `:1092` es `:1091`, `:1097` es `:1096`, `:1468` es `:1467` (la fila de `caso.py`) y `:1745` es `:1744-1746`. Agregar a la línea 20 del plan: «También aplica los parches de B al diseño de TEMPORADA (diseño, sección «B», «Cruces con TEMPORADA 01»).» Agregar al punto 6: «y la llamada a `_paso` pasa `centavos=caso.revision >= 1`».

## No bloqueantes

### 4. La prueba de D no detecta si se olvida cambiar `v_economia_producto`

- Dónde: fila D.1, `test_arreglos02_d_la_estimada_no_se_suma_a_la_comision_real` y `..._la_reversa_regresa_las_vistas`.
- Qué pasa: `v_economia_producto.cargos_con_orden` es `m.cargos_con_orden` de `v_margen_producto` (`0021:146,161`). Da −250 aunque `0021:67,92` siga leyendo `ledger_event`. La sonda de D.2 sí lo detectaría al desplegar, pero la prueba de dinero no.
- Evidencia: base temporal con todas las migraciones, la semilla de D.1 (costo 300 MXN, cantidad 1) y una 0086 que a propósito no cambia la 0021:
  ```
  v_economia_producto.cargos_con_orden=[-250]          <- pasa igual
  v_economia_producto.muestra_margen_neto_pct=[51.25]  <- sigue con el doble conteo
  ```
  Con la 0086 completa, `muestra_margen_neto_pct` da 57.5.
- Corrección pegable (fila D.1): agregar a la semilla «costo 300 MXN, cantidad 1 por venta» y, en las dos pruebas: «en `v_economia_producto` se mide `muestra_margen_neto_pct`: 57.5 con la migración (hoy 51.25) y 51.25 tras la reversa».

### 5. Los `\|` de las celdas, copiados tal cual, rompen comandos; uno da evidencia vacía sin avisar

- Dónde: D.3 (`split_part(source_event_id,'\|',2)`), B.4 y C.3 (`... \| bash docs/evidencia/arreglos-01/lee_prod.sh \| ...`), A.2 (el patrón `\|`), D.1 (los `source_event_id`).
- Evidencia: la consulta de D.3 tal como está en el archivo no devuelve nada y sale 0. Con `|` devuelve los pedidos:
  ```
  (con \|)  -> sin filas, exit=0
  (con |)   -> amazon_mx|265  amazon_us|97
  ```
  En B.4 y C.3 el `echo ... \| bash ...` imprime el texto en vez de encadenar. El `grep -qx` de los dos `Acepta` lo detecta, pero en C.3 el ejecutor podría leerlo como «no es 0» y parar el bloque.
- Corrección pegable (agregar a la línea 17 del plan): «En las celdas de las tablas, `\|` se escribe `|` al copiar un comando, un patrón de `grep -E` o un `source_event_id`.»

### 6. `reproduce` devuelve tres valores, no dos

- Dónde: B.4 y C.4 («`reproduce` da `("bid", Decimal("0.2904"))`»).
- Evidencia: `app/optimizer/replay.py:126`, que es `-> tuple[str | None, Decimal | None, str | None]`. Hoy da `('bid', Decimal('0.290400'), 'USD')`.
- Corrección pegable: en las dos filas, poner `("bid", Decimal("0.2904"), "USD")` en lugar de `("bid", Decimal("0.2904"))`.

### 7. El caso de 0.06 USD de B.1 no dice piso, techo ni economía

- Dónde: fila B.1, `test_arreglos02_b_el_redondeo_no_pasa_del_paso`.
- Qué pasa: `fabrica_caso` trae por omisión MXN con piso 0.50. Con un piso de 0.05 o más, el resultado cambia por el piso y no por el redondeo.
- Evidencia: con el prototipo, piso 0.02 da `Mover(-0.25, 0.05)` en las revisiones 1 y 2, y hoy da `0.0450`. Con piso 0.05, hoy ya da 0.05.
- Corrección pegable: «`fabrica_caso(plataforma="amazon_us", bid=BidVigente(Decimal("0.06"), "USD", Decimal("0.02"), Decimal("2.50")), economia=economia_us(), propia=nivel(tramo(clics=100, pedidos=0, venta="0", gasto="80", impresiones=3000)), revision=1)` da `Mover(Decimal("-0.25"), Decimal("0.05"), "gasto_sin_venta_doble", "hoja")`».

### 8. D.3 nombra dos consultas que no existen y una tercera que falla escrita como suena

- Dónde: fila D.3. No existe un archivo `q_margen.sql`: está en el diseño, líneas 249-269. La «de productos» no tiene SQL en ninguna parte (el diseño remite a `antes.txt`, que no existe todavía). La meta de `notes.target` necesita un cast: `optimizer_cycle.notes` es `text` y `notes->'target'` falla con `operator does not exist: text -> unknown`.
- Corrección pegable (fila D.3):
  - «`q_margen.sql` son las líneas 249-269 del diseño (`sed -n '249,269p' docs/evidencia/arreglos-02/DISENO.md | sed 's/^    //'`).»
  - «La de productos es `SELECT h.platform, h.product_id, round(h.margen_neto_pct,2) hoy, round(n.margen_neto_pct,2) sin_dup FROM v_margen_producto h JOIN (<cuerpo de 0018:284-419 con las líneas 321 y 351 leyendo de un CTE `v_ledger_cargo` con el SQL del diseño>) n USING (platform, product_id) WHERE h.margen_neto_pct IS NOT NULL`.»
  - «La meta: `SELECT id, platform, (notes::jsonb)->'target'->>'margen_neto_pct', (notes::jsonb)->'target'->>'target_aplicado' FROM optimizer_cycle WHERE id IN (SELECT max(id) FROM optimizer_cycle WHERE platform IN ('amazon_mx','amazon_us') GROUP BY platform)`.»

### 9. «Copiado tal cual» desde 0018, 0021 y 0048 empieza con `CREATE VIEW`

- Dónde: fila D.2. `0018:283`, `0021:33` y `0048:29` dicen `CREATE VIEW ...`; solo `0016:25` dice `CREATE OR REPLACE VIEW`. Copiado literal, falla con «ya existe». La prueba de D.1 lo atrapa, pero cuesta una vuelta.
- Corrección pegable: «el texto de cada vista se copia de esas líneas cambiando también `CREATE VIEW` por `CREATE OR REPLACE VIEW` en la primera».

### 10. Quedan en TEMPORADA frases de «hoy» con el equilibrio viejo

- Dónde: `docs/evidencia/temporada-01/DISENO.md:902` («US: max(19.05, 25) = 25 (arriba de su equilibrio 23.81: Ajustes avisa)»), `:913` («US hoy en alta: max(23.81, 25) = 25; MX: max(40.26, 25) = 40.26»), `:1836` y `:1841` (texto de Ajustes con «Punto de equilibrio con el costo de hoy: 23.8 %» y la línea roja de US). Contradicen la decisión 4 parchada y la maqueta de `:116-123`, que ya quitó la línea roja. El diseño de ARREGLOS 02 pedía cambiar `:1835-1840` a «ejemplo», y no se hizo. (`plans/temporada-01.md:92` también usa 23.81, pero ahí es un caso de prueba sintético y el diseño dice que se conserva; está bien.)
- Corrección pegable: en `:902`, cambiar «MX hoy:» por «Ejemplo:» y «(arriba de su equilibrio 23.81: Ajustes avisa)» por «(con un equilibrio de 23.81, Ajustes avisaría)». En `:913`, cambiar «US hoy en alta:» por «Ejemplo en alta:». En `:1836` y `:1841`, cambiar «23.8 %» por «<equilibrio>» y agregar antes de la cita: «Ejemplo con un equilibrio de 23.8 %, debajo de la meta de alta:».

### 11. El margen «antes» de US sale distinto en el aviso y en los parches

- Dónde: el aviso de D dice «de 23.6 % a 29.0 %». La línea 20 del plan y la decisión 4 de TEMPORADA dicen «antes 23.8 %». Las dos cifras son reales (23.64 en la lectura de la tarde, 23.81 en el ciclo 184), pero el dueño verá dos números para lo mismo.
- Corrección pegable (fila D.3, al final): «El aviso usa las cifras de `antes.txt`, las de hoy y las de sin duplicado, aunque no se alejen más de 1 punto del diseño.»

### 12. A.0 no comprueba toda la evidencia que el plan usa

- Dónde: fila A.0. Faltan `ciclo.md` y `apply.md`, que el diseño cita como fuentes, `HALLAZGOS.md` (línea 5 del plan) y `pruebas/conftest.py` con `pruebas/politica/test_revision_politica.py`, que la línea 17 manda correr.
- Corrección pegable: agregar al `git ls-files --error-unmatch` de A.0: `docs/evidencia/arreglos-2026-11-12/HALLAZGOS.md docs/evidencia/arreglos-2026-11-12/ciclo.md docs/evidencia/arreglos-2026-11-12/apply.md docs/evidencia/arreglos-2026-11-12/pruebas/conftest.py docs/evidencia/arreglos-2026-11-12/pruebas/politica/test_revision_politica.py`.

### 13. Riesgo con ARREGLOS 01 T (sin prueba: T no está en esta copia)

- T.2 puede dejar un marcador congelado en `caso.py`. Si es una clave opcional nueva del caso, el `_objeto_con_opcionales` de B tiene que aceptarla.
- Corrección pegable (fila B.3): «Si ARREGLOS 01 T dejó una clave opcional en el caso, va también en `opcionales`.»

## Verificado y correcto

- `Acepta` de A.0, A.1 y la `prueba-real` de A (sin `fetch`) salen 1 hoy: pueden fallar.
- `-k arreglos02_a_`, `-k arreglos02_b_`, `-k arreglos02_c_` y `-k arreglos02_d_` dan «no tests ran» hoy (exit 5 en `test_despliegue_motor.py`).
- Todos los archivos de prueba de los `Acepta` existen, salvo `tests/test_arreglos02_cargos.py`, que es nuevo. Las 15 suites de B, C y D pasan hoy (sin saltos), y `tests/test_despliegue_motor.py` también (46 passed).
- Las pruebas de reproducción de `pruebas/politica/` corren con el comando de la línea 17 y fallan hoy (9 failed).
- `docs/CONTEXTO.md:84-89` y `:148-149` dicen lo que A.1 afirma.
- `comun.sh:196-197,202` y `desplegar.sh:4,50,183` son las líneas de las etiquetas. `test_arreglos01_a_` está en `:1119`. `reversa_de` da `0086_reversa_arreglos02_cargos.sql`.
- Ninguna migración posterior recrea las cuatro vistas: solo `0015/0016`, `0018`, `0021` y `0048` las definen. Las líneas `0016:65,72`, `0018:321,351`, `0021:67,92` y `0048:60,152` son exactamente los `FROM ledger_event l` de los cargos con y sin orden, y los rangos 25-155, 283-419, 33-163 y 29-210 cubren cada vista completa.
- Prototipo de D en una base temporal (todas las migraciones, la 0086 de D.2 y la semilla de D.1): las cuatro vistas pasan de −375 a −250, y tras la reversa vuelven a −375 sin `v_ledger_cargo`.
- La sonda de D.2 da `False` en producción hoy. `migracion_aplicada.sh arreglos02_cargos` sale 1 hoy.
- `q_margen.sql` en producción: 40.33 → 45.50 (MX) y 23.64 → 28.99 (US), con 205/80 duplicadas y 4/2 estimadas sin real, igual que la tabla del diseño. El cuerpo real de `0016` con `v_ledger_cargo` en las líneas 65 y 72 también da 45.50 y 28.99.
- Por producto, en producción: 6 de 7 productos de MX suben de 2.07 a 4.25 puntos, y en US los 3 suben de 1.34 a 3.48. `v_margen_familia` está vacía. Las 362 estimadas son `fee`/`referral_fee`.
- Las metas `target_aplicado` de los ciclos 185 y 184 son 20.13 y 19.05, con fracciones 0.5 y 0.8. La subida de la meta se aplica de una vez (`goals.py:610-615`).
- Rejuego de las 49 `niveles_v3` de producción con el prototipo de B y C (incluido `_previa` con `grupo.sin`): congeladas, `distintas 0`. Con la revisión 1, 21 bids enviados distintos y 0 veredictos distintos. Con la revisión 2, cambian 8 (2835, 2842, 2854, 2856, 2863, 2865, 2902 y 2908), como dice el diseño. Si se fuerza la revisión 1 a todo, el rejuego da `distintas 35`: la compuerta de B.4 y C.4 sí puede fallar.
- Casos de C.1 con el prototipo: el de `test_f3` con revisión 2 da `Mantener("azar_lo_explica")`; el de `test_f4` da `Mover(0.15, 11.50, "bajo_target", "hoja")`; el de US con bid 10.00 tras un ajuste da `Mover(-0.25, 7.50, "gasto_sin_venta_doble", "hoja")`. El caso de `test_f1` con revisión 1 da `Mover(-0.12, 0.30)`.
- La consulta previa de C.3 da exactamente `0` en producción, y la misma consulta con `origen = motor` da 47: no es decorativa.
- `politica.py:262`, `:423-427`, `:497-503`, `caso.py:136-138`, `lecturas_caso.py:506` (único constructor), `write.py:198`, `replay.py:17-22`, `DATABASE.md:774` y `test_architecture.py:42` dicen lo que el plan cita.
- `09:05` ya no aparece en TEMPORADA. `10:05` cuadra con ARREGLOS 03 (ciclo a las 09:40, `plans/arreglos-03.md:11`).
