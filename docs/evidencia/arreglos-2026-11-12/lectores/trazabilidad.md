# Lector de trazabilidad y choques: ARREGLOS 02, 03 y 04 (2026-11-12)

Qué leí: el árbol de trabajo de `la copia de trabajo del plan` (rama `plan/arreglos-02`, código de master `a4644aad`). Incluye `HALLAZGOS.md`, la sección «Fallas» de los ocho reportes, los tres planes nuevos con sus diseños, ARREGLOS 01 T-X y TEMPORADA 01 con el diff de este PR.
Resultado: **5 bloqueantes y 26 no bloqueantes**. Todos los comandos se corren desde la raíz de esa copia.

---

## Bloqueantes

### 1. Los siete parches de centavos que el diseño de ARREGLOS 02 B escribe para TEMPORADA 01 no se aplicaron

- **Qué pasa.** `docs/evidencia/arreglos-02/DISENO.md:123-130` («Cruces con TEMPORADA 01, con parche») trae siete cambios para el diseño de TEMPORADA. Ninguno está en el diff de este PR. La cabecera de ARREGLOS 02 (`plans/arreglos-02.md:20`) dice que el PR «también corrige» TEMPORADA, pero solo nombra el bloque D, y solo el parche de D se aplicó.
- **Qué pasa al seguir los dos diseños.** B crea `app/optimizer/centavos.py` (`CENTAVO`, `centavo_hacia`), `_objeto_con_opcionales` con la opcional `revision`, y `_paso(..., *, centavos)`; además borra R17 de `_freno_trayectoria`. Después, el diseño de TEMPORADA manda:
  - definir otra vez `CENTAVO` en `temporada.py`;
  - redondear con su propio `quantize(CENTAVO, ROUND_CEILING)`;
  - crear `_objeto_con_opcionales` con opcionales sin `"revision"`;
  - llamar `_paso` sin `centavos`;
  - aplicar «solo R17» de `_freno_trayectoria`, donde R17 ya no existe.
- **Consecuencias.** El ejecutor de TEMPORADA encuentra un código que contradice su diseño y, por la regla de su cabecera, para el bloque. Si no para, quedan dos reglas de centavo. Peor: si su `desde_json` usa ese conjunto sin `revision`, ningún caso nuevo se puede rejugar.
- **Evidencia:**
  ```
  $ git diff -- docs/evidencia/temporada-01/DISENO.md | grep -ci centavo
  0
  $ grep -n 'CENTAVO = Decimal\|quantize(CENTAVO, ROUND_CEILING)\|_objeto_con_opcionales\|solo R17\|Con 0.15 USD, `_paso` da 0.1050' docs/evidencia/temporada-01/DISENO.md
  39:   ... Con 0.15 USD, `_paso` da 0.1050 y
  499:CENTAVO = Decimal("0.01")
  693:    # desde_json: _objeto_con_opcionales(datos, _CLAVES_CASO,
  786:    # d = (bid * (1 - reduccion)).quantize(CENTAVO, ROUND_CEILING)
  1091:    #   ultimo.origen == "temporada"                      -> solo R17 (R2 ya pasó)
  1467:| `app/optimizer/caso.py` | ... `_objeto_con_opcionales` | el formato congelado |
  1566:- Si el último cambio fue de temporada, solo R17: ...
  ```
  Los números de línea que cita el diseño de 02 (`:500`, `:787`, `:694`, `:1468`, `:1092`, `:1745`) ya están una línea corridos, porque el parche de D quitó una línea de la maqueta.
- **Corrección pegable.** Aplicar en `docs/evidencia/temporada-01/DISENO.md`, anclado por contenido:
  1. Línea `CENTAVO = Decimal("0.01")` → `from app.optimizer.centavos import CENTAVO, centavo_hacia  # ARREGLOS 02 B`
  2. En `destino_entrada`: `# d = (bid * (1 - reduccion)).quantize(CENTAVO, ROUND_CEILING)` → `# d = centavo_hacia(bid * (1 - reduccion), desde=bid)   # mismo centavo hacia arriba (ARREGLOS 02 B)`
  3. Al final del punto 2 de «Lo que hace que la forma no sea obvia» (el que termina en «El destino de temporada se resuelve en centavos antes de congelarlo.»): «Desde ARREGLOS 02 B, `_paso` ya decide en centavos con `centavo_hacia` (0.15 USD con −25 % da 0.12, no 0.11); el destino de temporada usa la misma función.»
  4. `#                                    {"temporada", "historia", "recuperacion", "presupuesto"})` → `#                                    {"revision", "temporada", "historia", "recuperacion", "presupuesto"})   # _objeto_con_opcionales ya existe desde ARREGLOS 02 B`. En el mapa de módulos, quitar `; _objeto_con_opcionales` de la fila de `app/optimizer/caso.py`.
  5. `-> solo R17 (R2 ya pasó)` → `-> ningún freno de _freno_trayectoria; el piso aprendido (R17) lo aplica _paso (ARREGLOS 02 B lo dejó solo ahí)`. El mismo cambio en «Si el último cambio fue de temporada, solo R17» y en «solo aplican R2 y R17».
  6. Después de `#   (si techo_extra deja el delta bajo un centavo -> Mantener(MOTIVO_TECHO_DE_BAJA))`: `#   techo_extra se aplica antes de centavo_hacia, junto con piso y techo; _paso recibe centavos=caso.revision >= 1 (ARREGLOS 02 B)`
  7. En `plans/arreglos-02.md:20`, después de «Los parches están en el diseño, sección «D», «Cruce con TEMPORADA 01».», agregar: «Los de centavos, en la sección «B», «Cruces con TEMPORADA 01, con parche», también van en este PR.»

### 2. Las pruebas de B, escritas con `REVISION_POLITICA`, se rompen cuando C la sube a 2: el `Acepta` de C sale rojo siguiendo el plan al pie de la letra

- **Dónde:** `plans/arreglos-02.md:57` (B.1) y `:71-73` (C.1, C.2, C.3).
- **Qué pasa.** B.1 manda que `test_arreglos02_b_piso_aprendido_en_centavos[2865]` use `replace(caso, revision=REVISION_POLITICA)` y espere `Mover` a 0.30. C.2 sube `REVISION_POLITICA` a 2. Con revisión 2, la 2865 da `Mantener("vende_dentro_del_margen")`: lo afirma C.1, y está en la tabla de `DISENO.md:192`. Los `Acepta` de C.2 y C.3 corren todo `tests/test_optimizer_politica.py`, así que salen rojos. El ejecutor tiene que tocar una prueba de dinero de otro bloque sin instrucción, y puede borrar la cobertura de la falla 1 justo en el caso real 2865.
- **Evidencia:**
  ```
  $ python3 -c "import json;print(json.load(open('docs/evidencia/arreglos-2026-11-12/pruebas/politica/datos/decision_2865.json'))['motivo'])"
  grupo_sangra_vendedora
  $ grep -n '| 2865 | 4934 | 0.98 | 0 | sin veredicto |' docs/evidencia/arreglos-02/DISENO.md
  192:| 2865 | 4934 | 0.98 | 0 | sin veredicto |
  $ grep -n 'revision=REVISION_POLITICA' plans/arreglos-02.md | cut -c1-12
  57:| B.1 | D
  59:| B.3 | M
  71:| C.1 | D
  ```
- **Corrección pegable, en B.1:** cambiar «y con `replace(caso, revision=REVISION_POLITICA)` `decide` da `Mover`» por «y con `replace(caso, revision=1)` `decide` da `Mover`», y en la segunda prueba «`revision=REVISION_POLITICA`» por «`revision=1`». Agregar al final de B.1: «Las dos fijan `revision=1`, no `REVISION_POLITICA`: C la sube a 2 y cambia el veredicto de la 2865.»

### 3. El parche de `comun.sh` del diseño de ARREGLOS 03 borra la etiqueta `arreglos02` (lo encontró el lector de 03; lo comprobé)

- **Dónde:** `docs/evidencia/arreglos-03/DISENO-precios.md:500-501`. Lo manda usar la fila BASE.1 (`plans/arreglos-03.md:30`).
- **Qué pasa.** La línea `+` es `_(auto03|orbit07|claridad01|arreglos01|temporada01|arreglos03)_`: no trae `arreglos02`. Cuando 03 arranque, `arreglos02` ya estará en master, porque ARREGLOS 02 A.2 lo pone. El `Acepta` de BASE.1 (`-k arreglos03_base_`) y su `prueba-real` no lo detectan. Si se pega la línea, se rompe `test_arreglos02_a_...` y el despliegue deja de listar las migraciones de ARREGLOS 02. El error recién sale en D-H.0, que dice «si ya lo reconoce, no cambia nada».
- **Evidencia:**
  ```
  $ sed -n 501p docs/evidencia/arreglos-03/DISENO-precios.md
  +    | grep -E '_(auto03|orbit07|claridad01|arreglos01|temporada01|arreglos03)_' | grep -v '_reversa_' | LC_ALL=C sort); do
  ```
- **Corrección pegable.**
  - En el diseño, reemplazar el bloque `diff` y la frase siguiente por: «Al grupo de etiquetas de `migraciones_de` se le suma `|arreglos03` al final, sin quitar ninguna de las que ya estén (`arreglos02`, y `temporada01` si ya entró). Con las dos en master queda `| grep -E '_(auto03|orbit07|claridad01|arreglos01|arreglos02|temporada01|arreglos03)_' | grep -v '_reversa_' | LC_ALL=C sort); do`.»
  - `Acepta` de BASE.1: `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py && grep -q arreglos02 tools/despliegue/comun.sh && grep -q arreglos03 tools/despliegue/comun.sh`

### 4. El `Acepta` de B-A.4 nunca sale 0: `docs/DEPLOY.md` nombra los dos comandos fuera de la sección que la fila manda reemplazar (lo encontró el lector de 04; lo comprobé)

- **Dónde:** `plans/arreglos-04.md:190`.
- **Evidencia:**
  ```
  $ rg -n "archivar-anuncios|reponer-anuncios" docs/DEPLOY.md | cut -c1-60
  1556:atrás YA implementada — la misma que obligó a `reponer-anuncios` antes de
  1557:`archivar-anuncios` (hallazgo de CodeRabbit, PR #116). La reversa borra por
  2318: ...   (2318 a 2359: la sección «Limpieza de product ads muertos», 2314-2368)
  ```
- **Corrección pegable, al final de B-A.4:** «En `docs/DEPLOY.md`, la frase "la misma que obligó a `reponer-anuncios` antes de `archivar-anuncios` (hallazgo de CodeRabbit, PR #116)" (párrafo de la reversa de 0011) pasa a "la misma que obligó a guardar la reversa del archivado de anuncios antes de archivar (hallazgo de CodeRabbit, PR #116)".»

### 5. La falla 3 de `web.md` («las ventas bajaron» con días que aún maduran) no tiene ninguna comprobación que falle si el arreglo no se hace (lo encontró el lector de 04; lo comprobé)

- **Dónde:** `plans/arreglos-04.md:66-70`.
- **Qué pasa:**
  - W-D.4 solo mira que `bloque_canal` tenga el parámetro `instante` (y la frase de la falla 10).
  - W-D.5 compara contra producción, pero su `Acepta` es `test -s`.
  - La reproducción `test_resumen_7d_no_concluye_caida_por_dias_inmaduros` responde por el texto `"GROUP BY v.metric_date"`, que la consulta nueva también trae, así que no distingue el corte.
  - Resultado: se puede agregar el parámetro sin cambiar la lectura y todo sale verde.
- **Evidencia:**
  ```
  $ rg -n '"GROUP BY v.metric_date"' docs/evidencia/arreglos-2026-11-12/pruebas/web/test_rev_numeros.py
  99:            ("GROUP BY v.metric_date", filas),
  ```
- **Corrección pegable.**
  - Al final de W-D.4: «y, con una conexión falsa que responde a `_SQL_DIAS` con la venta final (100 por día) y a la consulta que contiene el corte de `observed_at` con la venta a la misma edad (76 por día en el periodo anterior), la frase de `_tarjeta_canal("amazon_mx", ...)` para 7 días no contiene «bajaron».»
  - En W-D.5: «Escribe al final la línea `cuadra si` o `cuadra no`.» `Acepta`: `test -s docs/evidencia/arreglos-04/W-D/comparacion.txt && grep -qx 'cuadra si' docs/evidencia/arreglos-04/W-D/comparacion.txt`

---

## No bloqueantes

### Choques entre planes (E, G, H)

**6. ARREGLOS 03 y ARREGLOS 04 no se nombran entre sí en ningún cruce, y comparten 12 archivos.**
- Las dos cabeceras dicen «corre en paralelo con …», y ningún diseño tiene una fila del otro plan.
- `grep -n "ARREGLOS 04" docs/evidencia/arreglos-03/*.md` da 0. `grep -n "ARREGLOS 03" docs/evidencia/arreglos-04/*.md` también da 0.
- Hay tres sitios donde los cambios caen pegados:
  - `app/cli.py`: B-A borra los subparsers de `archivar-anuncios` (`:679-700`) y `reponer-anuncios` (`:726-745`), que rodean al de `reputacion` (`:702-725`). Ese lo cambian D-B (`--fuente`) y D-K (`reputacion avisa`). B-A borra también la rama de `main` en `:819-823`, y D-K agrega la suya justo después, en `:824-832`.
  - `docs/DEPLOY.md`: B-A.4 reemplaza la sección `:2314-2368`. D-B.3 y D-K.5 editan `:2392-2418`, la sección siguiente, y citan números de línea que B-A.4 corre unas 50 líneas.
  - `app/dashboard_resumen.py`: D-F.3 y W-D cambian la misma `resumen()`.
- No se rompe nada en lo semántico, pero el plan no dice cómo integrar.
- Corrección pegable para las dos cabeceras, en «Cruces»: «Con ARREGLOS 03/04 (en paralelo) se comparten `app/cli.py` (`_construye_parser` y `main`: D-A, D-B y D-K contra W-H y B-A), `app/notifica.py`, `app/api_dashboard.py`, `app/dashboard_resumen.py` (`resumen()`: D-F.3 y W-D), `app/ui.py`, `docs/DEPLOY.md` (B-A.4 junto a D-B.3 y D-K.5), `.cursor/skills/verify-orbit/features/cortes.md` y `resumen.md`, y `tests/test_cli.py`, `test_notifica.py`, `test_api_dashboard.py` y `test_ui.py`. Son funciones distintas salvo `resumen()`, `_construye_parser` y `main`. Todo cambio en esos archivos se hace por contenido, no por número de línea; al rebasar sobre el otro plan se conserva lo suyo.»

**7. La cabecera de ARREGLOS 03 dice que los bloques D y P no comparten archivos de código, y comparten `app/notifica.py`.**
- **Dónde:** `plans/arreglos-03.md:18`.
- D-K.3 y D-K.4 tocan `digest_ciclo`, `_lineas_reputacion` y `notifica_digest`, y agregan `aviso_reputacion`. P-B.3 toca `FRASEO_TIPO_PRECIO` (`:1067`) y `notifica_precio` (`:1452`). Las dos cadenas corren en paralelo.
- Corrección: «Los bloques D y P comparten solo `app/notifica.py` (D-K: reputación; P-B: aviso `corrida_fallida`), en funciones distintas, que se integran por contenido. Los P van en cadena porque comparten `app/precio/`.»

**8. Números de migración: dos planes, o dos cadenas del mismo plan, pueden tomar el mismo `<n>`.**
- Hoy la última es `0085`; ARREGLOS 02 toma `0086`.
- ARREGLOS 03 (D-H, D-I y D-K en una cadena; P-C y P-D en otra) y TEMPORADA (I, B) toman «el siguiente libre al abrir el PR», en paralelo. Los dos pueden tomar `0087`.
- No rompe el despliegue. `migraciones_de` ordena por nombre completo y `reversa_de` arma la reversa con el nombre, como muestra `rg -n "reversa_de\(\)" tools/despliegue/comun.sh` (`216: printf '%s_reversa_%s' "${1%%_*}" "${1#*_}"`). Ninguna prueba exige números únicos. Pero deja dos `0087_*` y el plan no dice qué hacer.
- Corrección pegable para las cabeceras de ARREGLOS 03 y TEMPORADA 01, después de «el siguiente libre en master al abrir el PR»: «Si al rebasar otro PR ya tomó ese número, la migración y su reversa pasan al siguiente libre antes de integrar, y se corrigen las menciones del número en las filas.»

**9. TEMPORADA I.1 no dice que conserve las etiquetas de los otros planes, y choca en el mismo renglón con 03 BASE.1.**
- Las dos editan la misma línea `:202` de `comun.sh` y agregan una prueba junto a `test_arreglos01_a_` (`tests/test_despliegue_motor.py:1119`).
- I.1 además mete `_temporada01_` en las dos listas de `:279` y `:365`. 02 y 03 no lo hacen, y no hace falta: sus migraciones entran a la base plantilla.
- Corrección para I.1, después de «el patrón suma `temporada01`»: «y conserva las que ya estén (`arreglos02` y, si ya entró, `arreglos03`). Los textos de `comun.sh` y `desplegar.sh` que listan las etiquetas se actualizan igual.»

**10. ARREGLOS 04 no exige ARREGLOS 02, y los dos tocan `app/ads/write.py`, `tests/test_ads_write.py` y `tests/test_architecture.py`.**
- La base de 04 es «ARREGLOS 01 terminado». Puede arrancar al mismo tiempo que 02.
- B.2 cambia `_bid_payload` (`:198`). B-A.3 borra `archivar_product_ad` y un par de `MUTATION_REQUEST_TYPES`. B.2 corre `tests/test_architecture.py`, que B-A.3 edita.
- Son funciones distintas, pero ninguna cabecera lo dice.
- Corrección para la cabecera de 04, en «Cruces»: «Si ARREGLOS 02 corre a la vez, B-A y su B.2 comparten `app/ads/write.py` (02 cambia solo `_bid_payload`), `tests/test_ads_write.py` y `tests/test_architecture.py`: funciones distintas, por contenido.»

**11. W-F traduce solo los errores de validación del esquema, y TEMPORADA A agrega campos que se validan a mano** (del lector de 04).
- Si el 422 de `temporada.valida` sale con `detail` en lista, Ajustes vuelve a mostrar «[object Object]».
- Si TEMPORADA renombra `target_manual_pct`, la reproducción `-k error_422` de W-F.2 deja de servir.
- Corrección para TEMPORADA A.2: «Los errores de `temporada.valida` responden 422 con `detail` en texto, igual que `mensaje_validacion` de ARREGLOS 04 W-F, si ya está en master.»

**12. PR #584 sigue abierto.** El diseño de 04 (`DISENO.md:59-65`) pide cerrarlo sin mergear antes del plan, pero la cabecera no lo dice.
- Evidencia: `gh pr view 584 --json state` da `OPEN`.
- Corrección en la cabecera de 04: «Antes de W-A, el PR #584 se cierra sin mergear (diseño, pregunta e).»

### Una regla, un lugar (F)

**13. Las zonas horarias quedan en una sola tabla en cualquier orden, pero el diseño de 04 manda lo contrario de 03.**
- Al terminar D-F.3, la única tabla es `ZONAS` de `app/dia_cerrado.py`. `hoy_por_canal` usa `dia_local`, y `hoy_resumen`, de donde W-C toma el reloj, no cambia de firma.
- Funciona en los dos órdenes. Si W-C entra primero, D-F.3 solo cambia lo de adentro. Si entra D-F.3 primero, W-C llama `hoy_resumen`, que ya lee `ZONAS`.
- Nada de 04 toca `Canal.zona`, `CANALES` ni `_fin_del_dia`; lo comprueba `rg -n "\.zona\b|_fin_del_dia|CANALES\[" docs/evidencia/arreglos-04/DISENO.md`, que solo da la línea 34 (texto) y la 241 (`CANALES[n]`, que D-F.3 conserva).
- Pero `docs/evidencia/arreglos-04/DISENO.md:34-35` dice que el «último día cerrado» de la ingesta «debe leer `CANALES[plataforma].zona`», y D-F.3 borra ese campo.
- Corrección para esas líneas: «La función de «último día cerrado» la crea ARREGLOS 03 D-E en `app/dia_cerrado.py` (`ZONAS`, `dia_local`, `ultimo_dia_cerrado`), y D-F.3 hace que `hoy_por_canal` la use. W-C solo llama `hoy_resumen`, que sigue igual.» En el docstring de `_hoy_pantalla` (`:180-190`), «(dashboard_resumen.CANALES)» pasa a «(zonas de `app.dia_cerrado`, vía `hoy_resumen`)».

**14. Hay otro par de definiciones duplicadas: «último día cerrado» de las pantallas.**
- W-C calcula `hasta = _hoy_pantalla(mercado) - 1 día`; D-E crea `ultimo_dia_cerrado(plataforma, instante)` = `dia_local - 1`. Es la misma regla en dos sitios, aunque hoy den lo mismo.
- (El otro duplicado, el centavo de 02 B contra el de TEMPORADA, es el bloqueante 1.)
- Corrección para W-C.1: «Si `app/dia_cerrado.py` ya está en master, `hasta` es `min(ultimo_dia_cerrado(p, instante) for p in plataformas)`; si no, `_hoy_pantalla(...) - 1 día`, y ARREGLOS 03 D-F.3 la cambia al llegar.»

### Trazabilidad que se perdió en el camino (A, B, D)

**15. Del parche de D a TEMPORADA se aplicó solo una parte.**
- El cuarto punto del parche 3 de `docs/evidencia/arreglos-02/DISENO.md:345` pide que las líneas «`:1835-1840`» dejen de presentar 23.8 % como el estado de hoy. No se aplicó, y tampoco los comentarios «hoy» de `meta_del_juicio` y `equilibrio_del_juicio`.
- Evidencia: `grep -n "23.81\|23.8 %\|40.26" docs/evidencia/temporada-01/DISENO.md` da `902: ... (arriba de su equilibrio 23.81: Ajustes avisa)`, `913: # US hoy en alta: max(23.81, 25) = 25; MX: max(40.26, 25) = 40.26`, `1836:> Punto de equilibrio con el costo de hoy: 23.8 %` y `1841: ... 23.8 %, y 25 %.`
- Corrección:
  - En 902, «(arriba de su equilibrio 23.81: Ajustes avisa)» pasa a «(ejemplo con un equilibrio de 23.81: Ajustes avisaría)».
  - En 913, «US hoy en alta» pasa a «ejemplo, US con equilibrio 23.81, en alta».
  - Antes de la cita de 1836, agregar «Ejemplo con un equilibrio de 23.8 % (no es el de hoy: con los datos del 2026-11-12, sin la comisión doble, es 29.0 % en US):».

**16. La cabecera de ARREGLOS 02 trae el alcance viejo de 03 y 04** (del lector de 04).
- `plans/arreglos-02.md:5` dice «ARREGLOS 03 (datos, precios y bloqueos), ARREGLOS 04 (pantallas y fábrica)». Es el único rastro del renombre: `rg 'arreglos03|ARREGLOS 03'` en el plan y los diseños de 04 solo da la línea 3 de su cabecera, que está bien.
- Corrección: «ARREGLOS 03 (datos y precios), ARREGLOS 04 (pantallas, fábrica y bloqueos)».

**17. D.4 compara con una tolerancia de 0.1 puntos una vista que se mueve sola durante el día.**
- El diseño lo midió: 23.81 en el ciclo contra 23.64 en la tarde (`DISENO.md:277`), 0.17 puntos el mismo día.
- Si D.3 y D.4 caen en horas o días distintos, el ejecutor para aunque el arreglo esté bien.
- Corrección para D.4: «Después de desplegar, correr otra vez `q_margen.sql` con `lee_prod.sh` y, en la misma sesión, `SELECT platform, margen_neto_pct FROM v_target_margen_plataforma`. La vista nueva debe coincidir con el `margen_sin_dup` de esa misma corrida, con diferencia menor a 0.1 punto.»

**18. Falla 7 de `estimacion.md` (una alerta crítica nunca llega): D-K no tiene ninguna comprobación que falle sin el arreglo** (del lector de 03).
- El diseño dice «F7 no lleva prueba» (`DISENO-datos.md:562`).
- Corrección, al final del `prueba-real` de D-K: ` && bash docs/evidencia/arreglos-01/desplegado.sh --no "def carga_reputacion_digest" && bash docs/evidencia/arreglos-01/desplegado.sh "def ejecuta_aviso" "def aviso_reputacion"`

**19. Falla 9 («0 reclamos» cuando falta la lista): cubierta a medias** (del lector de 03).
- D-L.5 solo prueba `plan_seller(None)`. La falla está en `_pagina` y `fetch_seller` (`app/reputacion.py:442-443`).
- Corrección para D-L.5: «El guion también arma el `_ClienteFalso` de `test_reputacion_revision.py` (claims sin `data`) y afirma `fetch_seller(cliente)[0].disputas_total is None`.»

**20. Falla 6 (la caída de nivel se pierde): nada comprueba que `evalua_y_persiste` use `mejor_nivel_previo`** (del lector de 03).
- Evidencia: `grep -n "evalua_salud_cuenta(seller" app/reputacion_alertas.py` da `273: ... evalua_salud_cuenta(seller[0][1], seller[1][1])`.
- Corrección, al final del `Acepta` de D-L.3: ` && grep -q "mejor_nivel_previo(seller, hoy)" app/reputacion_alertas.py && ! grep -q "seller\[1\]\[1\]" app/reputacion_alertas.py`

**21. Falla 5 de `precio.md` (hora falsa en el historial): P-C.4 no tiene ninguna comprobación que pueda fallar** (del lector de 03).
- Evidencia: `grep -c "now()" app/precio/cambios.py` da 3, y las tres son del candado.
- Corrección, al final del `Acepta` de P-C.4: ` && PYTHONPATH=. uv run --frozen --python 3.12 python -c "import inspect, app.precio.cambios as c; assert 'ahora' not in inspect.signature(c.insertar_virtual).parameters" && [ "$(grep -c "now()" app/precio/cambios.py)" -ge 6 ]`

**22. Falla 2 de `precio.md`: el aviso de P-B promete más de lo que cubre** (del lector de 03).
- Cuando `_abrir_plataforma` (`app/cli_precio.py:49-51`) devuelve 1, la plataforma se salta sin aviso, y las líneas de precio del cron no llevan `cron-avisa`.
- Corrección, al final de P-B.3: «… y cuando `_abrir_plataforma` devuelve 1 (falla al armar los clientes), una vez para esa plataforma.»

**23. `HALLAZGOS.md:5` dice que cada bloque promueve su reproducción, y cuatro de `pruebas/estimacion/` no se promueven, a propósito** (del lector de 03).
- Corrección: «… cada bloque promueve la suya, salvo `test_vigia_calla_si_jev_corre_sin_juzgar`, `test_caida_de_nivel_de_cuenta_se_pierde_si_el_job_de_alertas_salta_un_dia`, `test_alerta_nunca_llega_a_telegram_si_el_ciclo_no_corre_en_24h` y `test_respuesta_de_disputas_sin_la_clave_data_se_guarda_como_cero_disputas`, que ARREGLOS 03 cubre con `cron-avisa` y con los guiones de `prueba-real` y que siguen fallando aquí.»

**24. W-C (falla 4 de `web.md`): la comprobación no obliga a productos ni a búsquedas a usar el reloj nuevo** (del lector de 04).
- Las dos usan `dt.datetime.now(dt.UTC).date()`, no `_hoy_utc`.
- Evidencia: `rg -n "now\(dt.UTC\)\.date\(\)" app/api_dashboard.py` da 240, 1993 y 2072.
- Corrección: al `Acepta` de W-C.1 se le antepone `! rg -q "_hoy_utc|now\(dt\.UTC\)\.date\(\)" app/api_dashboard.py && `.

**25. El `Acepta` de W-I.0 puede salir 5 («no se recogió ninguna prueba») aunque X ya esté en master** (del lector de 04).
- X.2 no obliga a que haya una prueba `arreglos01_x_` en `tests/test_optimizer_goals.py`.
- Corrección, `Acepta` de W-I.0: `PYTHONPATH=. uv run --frozen --python 3.12 python -c "from app.optimizer.goals import exige_banda_meta_manual, exige_bajo_techo, TECHOS_HUMANOS_POR_MONEDA"`

**26. B-D no revisa los bloqueos atorados en `reversa_fallida`, que la falla 8 también cuenta como vivos** (del lector de 04).
- Hoy hay 0, pero pueden aparecer antes de B-B.
- Corrección, al final de B-D.1: «Además imprime `reversa_fallida <n>`, con los veredictos aprobados cuyo último paso es `reversa_fallida`. Si n > 0, se anotan en `antes.txt` y el bloque para con `docs/evidencia/arreglos-04/B-D/diferencia.md`.»

**27. B-B.6 cita un texto del diseño (`## F4`) que no existe** (del lector de 04).
- Evidencia: `rg -c 'baño' docs/evidencia/arreglos-04/DISENO-bloqueos.md` da 0.
- Corrección para B-B.6: «Ítems: `{"keywordId": "k1", "adGroupId": "9", "keywordText": "baño", "matchType": "NEGATIVE_EXACT", "state": "PAUSED"}` y uno igual con `"keywordId": "k2", "state": "ENABLED"`.»

**28. Referencias rotas y alcance viejo en los diseños.**
- `DISENO-datos.md:5` cita `BLOQUES-datos.md` y `arreglos-04/DISENO.md:4` cita `BLOQUES-pantallas.md`. Ninguno existe: `ls docs/evidencia/arreglos-03 docs/evidencia/arreglos-04` solo muestra los `DISENO*.md` y `PENDIENTE-W-K.md`.
- `DISENO-bloqueos.md:110-111` todavía borra `tools/archiva_inertes.py` y el rango de DEPLOY `:2246-:2368`, que incluye la sección de inertes que se queda. B-A.3 manda seguir esa tabla, y solo lo salva la nota de `:90`.
- `arreglos-04/DISENO.md:23` cuenta W-K como «Dentro».
- Corrección:
  - Las dos primeras líneas pasan a «Bloques: `plans/arreglos-03.md`» y «Bloques: `plans/arreglos-04.md`».
  - En `DISENO-bloqueos.md`, tachar la fila `:110`, y la `:111` pasa a «la sección «Limpieza de product ads muertos (ORBIT 06)» (`:2314-2368`) y la frase de `:1555-1557`».
  - En `arreglos-04/DISENO.md:23`, quitar W-K, y el título `## W-K` pasa a «(fuera de ARREGLOS 04: espera decisión del dueño, ver `PENDIENTE-W-K.md`)».

### Plan contra diseño (C)

**29. P-F.2 dice «`_SQL_FEE` deja `fee_details`»; el diseño (`DISENO-precios.md:450`) dice «deja de leer»** (del lector de 03).
- Al quitar la columna se corren los índices de `goals_write.py:437-440`.
- Corrección: «`_SQL_FEE` (`:370-373`) deja de leer `fee_details` (quedan `total_fees, quoted_price_amount, estado`) y el armado de `LecturaHoy` (`:432-441`) ajusta sus índices.»

**30. D-E.2 pide `ValueError` con un instante sin zona o una plataforma desconocida, y el seudocódigo del diseño no lo levanta** (del lector de 03).
- `ZONAS[plataforma]` da `KeyError`, y `astimezone` sobre un instante sin zona lo toma como hora local.
- Corrección para `DISENO-datos.md:97`: «`if instante.tzinfo is None: raise ValueError("instante sin zona")`; `if plataforma not in ZONAS: raise ValueError(f"plataforma sin zona: {plataforma}")`».

**31. Las pruebas con base que se saltan sin Postgres dejan salir 0 un `Acepta` con `-k`** (del lector de 04).
- Pasa, por ejemplo, con las de W-M.1, que siguen los moldes de `test_cat_productos`.
- Corrección para las tres cabeceras: «Los `Acepta` con pruebas de base se corren con `-rs` y se dan por fallidos si alguna sale `skipped`.»

---

## Verificado y correcto

- Las 54 fallas tienen fila en `HALLAZGOS.md`. Las de «ARREGLOS 05» (ciclo 1-4; apply 2, 5, 6, 7 y 11; web 9; la mitad de Ajustes de web 1; la reversa de apply 1) y la de «en espera» (fábrica 2, con `PENDIENTE-W-K.md`) lo dicen.
- Las fallas que van a ARREGLOS 02 llegan a su bloque y a una comprobación que falla en `a4644aad`:
  - política 1, ciclo 5 y la primera parte de apply 12: B (falla porque `CasoHoja.revision` no existe);
  - política 2: A (`grep 'N = 10'` hoy da la línea 84);
  - política 3 y 4: C;
  - estimación 1: D (`v_ledger_cargo` no existe).
- D.1 cuadra: −150 − 125 − 100 = −375 hoy y −150 − 100 = −250 con la vista. Las cuatro vistas exponen `cargos_con_orden`. Las ocho líneas `FROM ledger_event l` que cita D.2 (`0016:65,72`, `0018:321,351`, `0021:67,92`, `0048:60,152`) son esas. Ninguna migración posterior redefine las cuatro vistas.
- A.1: `N = 10` e `inversion_sin_evidencia` solo aparecen en las líneas 84 y 89 de `docs/CONTEXTO.md`, y las frases que A.1 conserva (líneas 89 y 149) existen.
- El diff de TEMPORADA aplica los parches de D: I.2 recrea desde `0086`, la decisión 4 trae 45.5 y 29.0, y están cambiados la maqueta de Ajustes, `DISENO.md:34`, `:1717` y `:1811`. También aplica el de 03 D-E a H.3 (10:05 UTC).
- `depende:` de los tres planes: cada bloque nombrado existe y coincide con la línea «Orden» de su cabecera.
- Los nombres de prueba siguen la regla de su cabecera (`test_arreglos02_<letra>_`, `test_arreglos03_<letras>_`, `test_arreglos04_w_<letra>_` y `test_arreglos04_b<letra>_`).
- Cada ruta `docs/evidencia/arreglos-0N/<bloque>/...` que una fila da por existente la crea una fila anterior del mismo bloque.
- Las secciones de diseño citadas existen, en los tres planes.
- Las cifras, nombres de función y textos de aviso de 02 B, C y D coinciden con su diseño: 0.30/0.43/0.27, 8 de 49, 45.50 y 28.99, `centavo_hacia`, `_estado_heredado` y `REVISION_POLITICA` 1→2. Los de 03 y 04 los revisaron los lectores de cada plan; lo que no coincide está arriba.
- ARREGLOS 01 T-X contra estos planes: T comparte `_freno_trayectoria` con 02 B y C (la cabecera de 02 dice que se edita por contenido). X va antes de W-B y W-I, que lo declaran. S ya está en master y su prueba de consumidores sigue pasando con los horarios de D-E (cada uno conserva las 16). Y (`ledger_solo_reatribuye`, 0085) ya está en master para D-H.4.
- El aviso nuevo de D-K suma una línea con `cron-avisa`. `cron_orbit.sh` solo aborta si la cuenta baja (`:112`, `:145`), así que no aborta.
- Orden real: 03 declara que necesita 02, y su único uso real es la etiqueta de `comun.sh`. TEMPORADA declara 02. Ningún bloque de 03 o 04 necesita algo de TEMPORADA. B-B sigue los nombres de `apply.py` y `apply_cola.py` si TEMPORADA los cambia, y lo dice.
- Decisiones del dueño:
  - La 1 está en la cabecera de 02, en A y en A.1.
  - La 2 no la toca nadie y lo dicen las cabeceras de 02 y 03.
  - La 3 está en la cabecera de 03, en P-D y en la prueba de dinero P-D.1 (91 se retienen, 50 pasan); la parte de 150 pujas va en HALLAZGOS como ARREGLOS 05.
  - La 4 está en la cabecera de 03, en P-E y en la prueba P-E.1.
  - La 5 está en las cabeceras de 03 (D-A a D-D y P-A) y 04 (W-A, W-B y B-A), con los modos del ciclo en ARREGLOS 05.
  - Ningún bloque contradice una decisión. `archiva_inertes` se queda porque tiene un lote pendiente, y la cabecera de 04 lo explica.

## Archivos que tocan dos planes

Leído de las filas. «Aviso» dice si alguna cabecera lo menciona.

| Archivo | Planes y bloques (qué toca cada uno) | ¿Misma función? | Aviso |
|---|---|---|---|
| `tools/despliegue/comun.sh` `migraciones_de` | 02 A.2; 03 BASE.1, D-H.0 y D-K.0; TEMPORADA I.1 (los tres suman una etiqueta) | Sí, la misma línea | 03 sí; 02 en su diseño; TEMPORADA no (punto 9) |
| `tools/despliegue/desplegar.sh` (textos `:4`, `:50`, `:183`) | 02 A.2 y 03 BASE.1 | Sí, mismas líneas de texto | 02 en su diseño |
| `tests/test_despliegue_motor.py` | 02 A.2 (prueba nueva), 03 BASE.1 (prueba nueva), TEMPORADA I.1 (listas `:279` y `:365` y prueba nueva) | No; tres pruebas junto a `:1119` | No (punto 9) |
| `tools/despliegue/crontab-orbit.txt` | Solo 03 (D-E.5 horarios, D-K.5 línea nueva). 04 B-A.2 solo lo lee; S y N de ARREGLOS 01 ya están en master | Sin choque entre planes | — |
| `app/cli.py` | 03 D-A (`_ingest :192`, choices `:557`), D-B (subparser `reputacion :712`), D-K (`reputacion avisa` y `main :824-832`); 04 W-H (`_parse_decided_at :68-86`), B-A (`:65`, `:240-345`, subparsers `:679-700` y `:726-745`, `main :819-823`) | Sí en `_construye_parser` y `main`, con cambios pegados | Solo W-H contra B-A (punto 6) |
| `app/notifica.py` | 03 D-K (`digest_ciclo`, `_lineas_reputacion`, `notifica_digest`, nueva `aviso_reputacion`), 03 P-B (`FRASEO_TIPO_PRECIO`, `notifica_precio`, clases nuevas); 04 W-G (`aviso_corte_encolado :251`, `notifica_encola :659`, funciones nuevas); TEMPORADA posible (su mapa no lo nombra) | No | 04 contra TEMPORADA en su diseño; 03 contra 04 no; D contra P dice lo contrario (punto 7) |
| `app/api_dashboard.py` | 03 D-C (`cortes :1479-1578`); 04 W-A (series `:130-411`), W-C (`:47`, `:238`, `:703`, `:1183`, `:1973`, `:1993`, `:2030`, `:2072`), W-E (`ruido :1960`); TEMPORADA A (`settings :1834` y vista previa) | No | 04 contra TEMPORADA en su diseño; 03 contra 04 no |
| `app/dashboard_resumen.py` | 03 D-F.3 (`Canal`, `CANALES`, `hoy_por_canal`, `_fin_del_dia`, `_cerrado`, `resumen() :955`); 04 W-D (`bloque_canal`, `resumen()`, `_SQL_DIAS_A_LA_EDAD`); W-C y W-E solo leen `hoy_resumen` y `DIAS_INMADUROS` | Sí, `resumen()` | No (puntos 6 y 13) |
| `app/ui.py` | 03 D-C (`:674`); 04 W-A (`:58`, `:115-116`), W-C (`:25`), W-E (ruta de búsquedas); TEMPORADA A (`:805-813`) | No | No |
| `docs/DEPLOY.md` | 03 D-A.3 (`:348`), D-D.3 (`:452`, `:467`), D-E.5 (tabla `:864-867`), D-B.3 (`:2392-2418`), D-K.5 (`:2407-2409`); 04 W-B.2 (`:1735-1746`), B-A.4 (`:1556-1557` y `:2314-2368`) | No, pero B-A.4 cae pegado a D-B.3 y D-K.5, que citan números de línea | No (punto 6) |
| `docs/DASHBOARD.md` | Solo 04 (W-A.3, W-C.1, W-D.3, en cadena) | — | — |
| `docs/DATABASE.md` | 02 D.2 (`:774`) y 03 P-D.4 (`:991-994`) | No | No hace falta |
| `.cursor/skills/verify-orbit/features/cortes.md` | 03 D-C.4 (quita la asesoría) y 04 B-B.3 (mensajes nuevos) | Mismo archivo | No (punto 6) |
| `.cursor/skills/verify-orbit/features/resumen.md` | 03 D-F.3 (si cambia texto), 04 W-A.3 y W-D.3 | Mismo archivo | No |
| `app/ads/write.py` | 02 B.2 (`_bid_payload`) y 04 B-A.3 (`archivar_product_ad`, `MUTATION_REQUEST_TYPES`) | No | No (punto 10) |
| `tests/test_ads_write.py`, `tests/test_architecture.py` | 02 B.2 los corre; 04 B-A.3 los edita; 03 D-C.2 y P-A.4 corren `test_architecture.py` | — | No (punto 10) |
| `app/cycle.py` | 04 W-G (`_fase_notifica :3645-3654`) y TEMPORADA (varias fases) | Posible, en `_fase_notifica` | Sí (cabecera de 04) |
| `app/optimizer/politica.py`, `caso.py`, `app/lecturas_caso.py` | ARREGLOS 01 T, luego 02 B y C, luego TEMPORADA | En secuencia | 02 avisa T; TEMPORADA exige 02, pero su diseño no trae los parches de B (bloqueante 1) |
| `tests/test_cli.py`, `test_notifica.py`, `test_api_dashboard.py`, `test_ui.py` | 03 (D-A, D-B, D-K, P-B, D-C) y 04 (W-H, B-A, W-G, W-A, W-C, W-D, W-E) | Pruebas distintas | No |
| `tests/test_api_write.py` | 04 W-F (`:1705`) y TEMPORADA A | No | Sí (diseño de 04) |
| `tests/test_goals_write.py` | 04 W-I la edita; 03 P-F solo la corre | — | — |
| `tools/fabrica_campanas.py`, `app/api_fabrica.py` | ARREGLOS 01 X, luego 04 W-B, W-F, W-I y W-J | En secuencia (04 exige X) | Sí |
| `app/ledger.py` | ARREGLOS 01 Y (en master), luego 03 D-H y D-I | En secuencia | Sí |
