# Revisión: fábrica de campañas y catálogo de productos

Código revisado: worktree en master `a4644aad`, que es lo que corre hoy en producción. La revisión fue de solo lectura: no cambié ningún archivo rastreado.

Mis pruebas de reproducción están en `docs/evidencia/arreglos-2026-11-12/pruebas/fabrica/` (carpeta nueva, sin rastrear). Se corren así:

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q docs/evidencia/arreglos-2026-11-12/pruebas/fabrica/
→ 3 failed  (cada una falla justo por la falla que describe)
```

**Cómo se ha usado la fábrica en producción (hoy):**
- Ha creado un solo lote en toda su historia: `web-923cb2…`, del 9-sep, con 87 pasos y ninguno fallido.
- No hay impulsos (0).
- No hay retiros de anuncios (0).

Casi todo lo que sigue es riesgo que todavía no ha ocurrido, no daño que ya haya pasado.

---

## Cómo funciona

1. **Elegir.** En «Campañas nuevas» eliges publicaciones, un nombre, el modo del optimizador (práctica o en vivo), el objetivo de ACoS y, para cada una de las 5 campañas (exact, phrase, broad, product targeting y auto), su presupuesto diario y su bid. El bid lo pones a mano o lo toma de la sugerencia de Amazon.
2. **Revisar.** El servidor arma el plan con datos de la base:
   - las semillas, que son los search terms con ventas en las campañas donde ya se anuncian esas publicaciones, más la biblioteca de la familia;
   - los bids, acotados a piso y techo según la moneda;
   - una «huella», que es la firma del plan.
3. **Confirmar.** Escribes «CREAR 5 CAMPAÑAS» y tu token. El servidor:
   - vuelve a armar el plan desde cero y, si la huella ya no coincide, se niega;
   - bloquea ese lote (`web-<huella>`) para que nadie más lo toque a la vez;
   - crea en Amazon, una por una, en este orden: campaña → ad group → product ads → keywords/targets/negatives.
4. **Cada paso deja rastro.** Antes de cada envío a Amazon se escribe una fila en `fabrica_lote_paso`. Después se guarda el id que devuelve Amazon y se lee de vuelta para comprobar que quedó como se pidió.
5. **Al terminar.** Se sincroniza la estructura y se registra el grupo con sus 5 goals: con el objetivo manual si lo diste o, si no, vacíos para que la meta se tome del margen.
6. **Si algo falla.** La creación se detiene y el lote queda «Interrumpido».
   - Las campañas ya creadas siguen **activas** (decisión 8).
   - Desde el detalle del lote puedes «Pausar», «Reconciliar» o «Completar registro».
   - Nada se vuelve a crear solo.
7. **Impulso** (otra ruta: 2 campañas por producto). Las campañas nacen **pausadas**, se activan al final y, si algo falla, se pausan solas.
8. **Retiro.** Saca de sus campañas viejas los anuncios de un producto. Tiene su propia reversa: regresar o recrear.

Archivos:
- `app/api_fabrica.py`: la entrada web.
- `app/fabrica_web.py`: el adaptador.
- `tools/fabrica_campanas.py`: el motor, que en realidad es un CLI.
- `app/fabrica_plan.py`: el núcleo puro.
- `app/static/js/fabrica.js`: la página.

---

## Fallas (con reproducción)

### 1. El servidor saca anuncios por «sin pedidos» sin comprobar que el producto de verdad no vendió

- **Qué te pasa:** la lista «Sin un pedido en 11 meses» se arma en la página. El servidor saca los anuncios de cualquier producto que la página le mande. Con una pestaña abierta desde hace días, o una petición armada a mano, un producto que vendió ayer se queda sin anuncios en sus campañas compartidas. Se puede regresar, pero mientras tanto pierde ventas por anuncios.
- **Dónde:**
  - `app/retiro_anuncios.py:245-272`: `planea_retiro` no lee ventas cuando el motivo es `sin_pedidos`.
  - `app/api_fabrica.py:766-790`: solo compara la huella de los anuncios y el texto «SACAR N ANUNCIOS».
  - `app/static/js/productos.js:425-523`: de aquí salen los productos.
- **Prueba:** `docs/evidencia/arreglos-2026-11-12/pruebas/fabrica/test_cat_productos.py::test_cat_retiro_sin_pedidos_no_verifica_que_no_haya_pedidos`. El producto vendió 4 veces hace 10 días: no aparece en la lista, pero el plan sí incluye su anuncio.
  ```
  AssertionError: el plan sin_pedidos incluye el anuncio de un producto que vendio hace 10 dias
  ```
- **Producción:** todavía no ha pasado (`anuncio_retiro` está vacía).
- **Qué debería pasar:** con motivo `sin_pedidos`, el servidor vuelve a calcular el criterio (cero ventas en 330 días, la misma consulta de `pantalla_productos._SQL_SIN_PEDIDO`), rechaza lo que no cumple y mete ese criterio en la huella.

### 2. Las semillas de un producto salen de las ventas de OTROS productos (NH- y SET- incluidos)

- **Qué te pasa:** al crear el grupo de un producto NH-, la fábrica le siembra los search terms que vendieron en todas las campañas donde ese producto se anuncia, sin importar qué producto se vendió. En tus campañas viejas casi todos los ad groups anuncian NH- y SET- juntos. Resultado: el grupo nuevo de un NH- nace con las keywords que probó el SET- (16 mm contra 22 mm) y viceversa. Eso incluye las semillas exact, que exigen 2 pedidos o más: esos 2 pedidos pueden ser de otro producto. El plan de la fábrica dice lo contrario: «terminos con ventas del MISMO producto» (spec `2026-09-05-fabrica-campanas-grupos-design.md` §6).
- **Dónde:** `tools/fabrica_campanas.py:112-164` (`_SQL_TERMINOS`). Toma la campaña del product ad y de ahí todos sus ad groups y todos sus términos; el search term no dice qué producto se vendió.
- **Sonda de producción:**
  - Ad groups que anuncian un NH- en MX: **3,755 de 3,756** también anuncian un SET-. En US: 2,620 de 2,740.
  - Cada uno de esos ad groups anuncia 11 productos.
  - Caso concreto en MX: con la consulta de la fábrica, `NH-ARR-COF-22-CEN-DOR` (listing 1245) y `SET-ARR-22-DOR-MAX-COF-22-CHA-RED-DOR` (listing 1246) heredan **los mismos 35 términos, 35 de 35**:
    ```
    nh_terminos 35 | set_terminos 35 | en_comun 35
    ```
- **Qué debería pasar:** sembrar solo términos de ad groups donde el producto se anuncia solo. Si no hay ninguno, arrancar sin semillas y decirlo («phrase: 0 semillas»), como ya prevé el spec. Así se respeta «NH- y SET- son productos distintos».

### 3. La exact nace con keywords que también viven en phrase y broad, sin negative cruzado

- **Qué te pasa:** los términos que ya probaste (la exact) se siembran también, sin negative, en phrase y broad del mismo grupo, y la auto arranca sin negativos. Una misma búsqueda puede caer en cualquiera de las tres campañas, que pujan entre sí. Lo aprendido se reparte y la exact no concentra lo probado. Para el harvest esto ya está resuelto con el negative cruzado (decisión 10: «el origen y las hermanas quedan negados»). La siembra hace el mismo trabajo que un harvest («mismo criterio del motor, aplicado al sembrar», §6), pero se salta esa mitad.
- **Dónde:** `app/fabrica_plan.py:442-494` (`semillas_desde_terminos`: `keywords` no excluye a `exact`) y `app/fabrica_plan.py:869-897` (los negatives solo salen de la biblioteca).
- **Sonda de producción** sobre el único lote real (`web-923cb2…`):
  ```
  exact 4 | keywords 31 | exact_tambien_en_phrase_broad 4 | negativos 0
  ```
  Las 4 exact («arras», «arras matrimoniales»…) se pujan también como phrase y broad, y la auto no tiene un solo negativo.
- **Qué debería pasar:** cada término sembrado en exact nace también como negative exact en phrase, broad, product targeting y auto, igual que hace un harvest. Si no, se quita de phrase y broad.

### 4. «Pausar» un lote interrumpido deja viva una campaña cuyo id sí se conoce, y el lote dice «Pausado» (impacto bajo)

- **Qué te pasa:** si el servidor se reinicia a media creación (los deploys son 24/7), la última campaña puede quedar con su id de Amazon guardado pero sin la comprobación final, es decir, en estado «planeado». Pausar solo mira los pasos «applied» o «failed», así que esa campaña sigue activa. El lote queda marcado «Pausado» («Las campañas conocidas del lote están pausadas») y desde ahí ya no deja reconciliar.
  - **Dinero:** hoy no corre, porque esa campaña nació sin ad group y no puede anunciar.
  - **Lo que sí pasa:** queda basura activa en Amazon y la pantalla dice algo falso.
- **Dónde:**
  - `tools/fabrica_campanas.py:317-330` (`_SQL_CAMPANAS_DEL_LOTE`, filtro `estado IN ('applied','failed')`), que contrasta con `_SQL_EXTERNALS_CAMPANA_DEL_LOTE` (líneas 237-240), la que usa la reversa del impulso: esa toma cualquier paso con id.
  - Bloqueo posterior: `app/fabrica_web.py:725-728`.
- **Prueba:** `docs/evidencia/arreglos-2026-11-12/pruebas/fabrica/test_fab_desarme.py::test_desarmar_pausa_campana_planeada_con_external`.
  ```
  AssertionError: solo se pauso ['c-1']; c-phrase (id conocido) sigue ENABLED gastando
  assert ['c-1'] == ['c-1', 'c-phrase']
  ```
  El lote queda en `desarmado`.
- **Qué debería pasar:** pausar toma toda campaña del lote que tenga id, sin importar el estado del paso. Es la misma consulta que ya usa la reversa del impulso (ver Estructura, movimiento 2).

### 5. La lista de candidatos a impulso suma clics de otro SKU del mismo ASIN (latente)

- **Qué te pasa:** un producto que vende y casi no tiene clics puede desaparecer de los candidatos porque se le cuentan los clics de otro SKU del mismo ASIN (por ejemplo NH- contra SET-). El motor sí lo hace bien: solo cuenta el SKU del listing. Es la misma regla escrita dos veces y una copia está mal.
- **Dónde:** `app/pantalla_productos.py:212-219`. Une `listing` solo por `external_id = advertised_asin`.
- **Prueba:** `docs/evidencia/arreglos-2026-11-12/pruebas/fabrica/test_cat_productos.py::test_cat_candidatos_cuentan_clics_de_otro_sku_del_mismo_asin`.
  ```
  AssertionError: los 30 clics del SKU SET-X se sumaron al producto NH-X — assert [] == [1]
  ```
- **Producción:** 0 casos hoy. Ningún ASIN tiene dos listings y en 60 días no hay filas de anuncios con otro SKU.
- **Qué debería pasar:** unir también por `advertised_sku = seller_sku`, reutilizando `productos_io.claves_por_producto`.

---

## Sospechas (no reproducidas o de diseño)

- **El presupuesto no tiene techo.**
  - Cada campaña acepta hasta 9,999,999,999.99 al día. La única guarda es «presupuesto ≥ bid» (`app/api_fabrica.py:114-125`, `app/fabrica_plan.py:360-397`).
  - Escribir 1500 en vez de 150 pasa. El texto de confirmación es fijo («CREAR 5 CAMPAÑAS») y no menciona el monto.
  - La pantalla sí muestra el total diario. Sugerencia: un techo por moneda, o que la confirmación incluya el total.
- **Si la creación de 5 campañas falla a medias, deja campañas activas gastando.**
  - Esto es lo que dice la decisión 8, pero el impulso ya resolvió lo mismo: nace pausado y se activa al final.
  - Además, si lo que falla es el registro de goals, esas campañas quedan gobernadas por el goal de plataforma (**en vivo**) aunque hayas elegido «práctica» (`tools/fabrica_campanas.py:1563-1587`).
- **«Pausar» un lote de 5 campañas no tiene vuelta desde Orbit.**
  - Deja el lote «desarmado: nunca revive» (`tools/fabrica_campanas.py:1832-1838`) y apaga los 5 goals.
  - Si después las reactivas a mano en Amazon, el goal de campaña apagado manda sobre el de plataforma: el motor **nunca** las vuelve a optimizar ni a proteger.
  - La pantalla lo llama igual que la pausa reversible del impulso («Pausado», `app/etiquetas.py:680-686`).
  - Esto choca con la regla 7: no hay reversa implementada desde Orbit.
- **Solicitudes largas.**
  - Un plan de hasta 100 publicaciones son cientos de pasos dentro de una sola petición web, cada uno con 0.3 s de espera y una lectura de comprobación.
  - Si Amazon frena las lecturas, la comprobación sale vacía y el lote se detiene a medias con campañas activas.
- **Con un lote ya interrumpido, la página dice «Grupo creado: Interrumpido».** Pasa si se reenvía la misma huella desde otra pestaña o tras recargar. El servidor devuelve el lote viejo con respuesta 200 (`app/fabrica_web.py:687-689` y `app/static/js/fabrica.js:1159`).
- **«Resultado incierto» aparece también cuando Amazon rechazó en firme un paso** (error 400, sin id; `app/fabrica_web.py:566-572`). Y «Reconciliar» siempre termina en error si hubo cualquier rechazo, porque cuenta ese paso como «sin verificar» (`tools/fabrica_campanas.py:1916-1926`).
- **El plan v1 (por producto) sigue accesible por la API** aunque la página ya no lo usa. Acepta productos con margen negativo y les pone un objetivo de 10 % (`app/fabrica_plan.py:326-347` no rechaza margen ≤ 0, a diferencia de v2).
- **El lote real dice «Práctica» y sus goals están en vivo desde el 2-oct.** El detalle muestra el plan persistido (`modo: shadow`) y la pantalla no muestra el modo actual de los goals. Puede confundir.
- **Del catálogo** (las encontró el subrevisor; sin reproducción):
  - En `productos.js`, «Lanzar», «Sacar», «Regresar» y «Recrear» no bloquean el botón mientras envían. El retiro y el impulso se protegen en el servidor; esto último lo comprobé: hay candado por producto y listing dentro de una transacción (`app/impulso_io.py:765-777`).
  - «Sin pedido» no revisa que las ventas estén al día. Si la carga se detiene, productos que sí venden se ofrecen para retiro.
  - Las fotos se piden una por una, con un candado global (`app/publicacion_fotos.py:136`). Con 342 publicaciones se forma cola y salen errores 503.
  - `familias.asigna` reasigna la familia sin avisar. Si la familia nueva mide margen, la meta cambia sin confirmación. Hoy hay 0 productos con familia.
- **Pequeño, regla 4:** `monto_wire` promete «float solo en el envío, jamás para guardar», pero el ledger guarda esos floats en `request_payload` (`tools/fabrica_campanas.py:1066-1072`).

---

## Estructura: movimientos que borran complejidad

1. **Un solo camino de creación: el lote de 5 nace como el impulso.**
   - **Problema:** `_mutar` tiene dos banderas (`crear_pausado`, `revierte_al_fallar`) que se ramifican en 4 sitios, dos notas de falla distintas, y la rama «las creadas quedan ENABLED» que solo existe para el lote de 5.
   - **Movimiento:** todo lote nace pausado, se activa al final y, si falla, se pausa lo creado.
   - **Qué desaparece:** las dos banderas, la rama de campañas activas a medias y la sospecha de gasto a medias.
   - **Compatibilidad:** las campañas terminan activas, que es lo que pide la decisión 8.
   - **Riesgo:** 5 envíos más por lote, y hay que reescribir las pruebas del camino de 5.
2. **Una sola pieza para cambiar el estado de las campañas de un lote.**
   - **Problema:** hay 5 bucles que hacen «enviar estado → esperar → leer → sellar»: `_pausa_una`, `_revierte_parcial`, `_activa_campanas`, `_cambia_estado_lote` y `reactiva_lote` (`tools/fabrica_campanas.py:1624-1830`). Además hay 4 consultas de «campañas del lote» con filtros distintos (líneas 237, 241, 317 y 331). Esa diferencia de filtros **es** la falla 4. También hay dos `try/except` muertos alrededor de `_readback`, que ya traga toda excepción (líneas 1138-1149 y 1640-1644).
   - **Movimiento:** una consulta (campañas con id, la última por rol, sin importar el estado) y una función `fija_estado(ctx, lote, estado, apaga_goals)`.
   - **Qué desaparece:** unas 150 líneas, 3 consultas y la falla 4.
   - **Riesgo:** medio, porque toca la pausa y la reanudación del impulso; las pruebas actuales lo cubren.
3. **Borrar el plan v1.**
   - **Evidencia de que no se usa:** producción tiene 1 lote, y es v2; la página solo manda `listing_ids`; `fabrica.creacion` está en v2.
   - **Problema:** v1 vive en `PlanGrupo`, `ProductoGrupo`, `plan_como_json`, `huella_plan`, `plan_desde_json`, `lineas_dry_run`, `_productos`, `_SQL_PRODUCTOS`, `_ids_productos`, la rama v1 de `_arma_plan`, el «o uno o el otro» de `SolicitudPlan`, **16** ramas `isinstance(plan, PlanGrupoV2)`, los envoltorios `_huella_plan`/`_plan_como_json` copiados en dos archivos y el propio interruptor `fabrica.creacion`.
   - **Qué desaparece:** unas 300 líneas más sus pruebas, y `fabrica_plan.py` (1,014) baja de 900 y sale de la lista de excepciones de tamaño.
   - **Riesgo:** se pierde `--productos` en el CLI. Leer lotes viejos no se ve afectado (hay 0 lotes v1).
4. **Que la web no hable con un CLI.**
   - **Problema:**
     - `fabrica_web.argumentos` convierte el JSON en un `SimpleNamespace` con unos 20 atributos planos (`budget_exact`, `fuente_bid_exact`, `recomendaciones_exact`…) para que `fc._parametros` lo vuelva a juntar en un diccionario.
     - La web usa 15 piezas privadas de `tools/fabrica_campanas.py` (2,010 líneas) y `impulso_io.py` usa 9.
     - El mapa rol → sufijo está escrito 4 veces (`SUFIJOS`, `_parametros`, `_parser`, `_valida_args_creacion`).
     - Los errores se clasifican buscando texto (`"fraccion" in str(exc)`, `startswith("ORBIT_DSN_", "LWA ")`), y los mensajes del CLI llegan a tu pantalla.
   - **Movimiento:** pasar el plan y la mutación a un módulo de `app/` con una solicitud tipada y excepciones tipadas. El CLI queda como un `argparse` delgado que arma la misma solicitud.
   - **Qué desaparece:** `argumentos`, `SUFIJOS`, el baile de `getattr` y la clasificación por texto.
   - **Riesgo:** medio; requiere mover la lista de importaciones permitidas de la prueba de arquitectura.
5. **La página solo pinta: procedencia y textos como datos.**
   - **Problema:** la procedencia del objetivo se arma como texto en `target_del_grupo` (3 ramas). Luego `fabrica_web._procedencia_dos_dec` la reformatea con una expresión regular, y `fabrica.js:252-261` (`textoProcedencia`) vuelve a desarmarla con más expresiones regulares (`-> clamp`, `NUMERIC(6,2)`). Además, `fabrica.js` (1,335 líneas, 70 funciones en un solo bloque) repite reglas del servidor: el literal «CREAR 5 CAMPAÑAS», las frases de confirmación, la validación del presupuesto y el reparto de semillas por campaña (`semillasDeCampana`).
   - **Movimiento:** el plan ya trae `fraccion`, `minimo`, `derivado` y `aplicado` (`ResultadoTarget`). Se mandan como campos, junto con las semillas por campaña (ya calculadas en `pasos_del_rol`) y la confirmación esperada.
   - **Qué desaparece:** dos capas de expresiones regulares, `semillasDeCampana` y 4 reglas duplicadas.
   - **Riesgo:** la huella incluye el texto de procedencia, así que cambia la identidad de los lotes **nuevos**; los viejos conservan su texto.
6. **Del catálogo** (subrevisor):
   - Una sola regla «producto ↔ métricas de anuncios»: hoy hay 3 versiones y el bloque `activos` está copiado 3 veces en `pantalla_productos.py`. Esto borra la falla 5.
   - Un solo lector de la «config vigente», que hoy está escrito 18 veces en `app/`.

---

## Qué comprobé que está bien

- **Doble clic, dos pestañas o dos envíos del mismo plan:** no crean dos veces.
  - El lote se llama `web-<huella>` y hay un candado de sesión en Postgres durante toda la creación (`app/fabrica_web.py:657-666, 684-689`). El segundo envío recibe «otra operación en curso» o el lote ya existente.
  - La página además bloquea el botón y recuerda las huellas ya intentadas.
- **Los envíos de creación no se repiten nunca:**
  - `_post` no reintenta.
  - Un timeout o un error 5xx se declara «incierto» y detiene todo (`tools/fabrica_campanas.py:1092-1133`).
  - La fila del ledger se escribe **antes** de cada envío, y el id de Amazon se guarda **antes** de la comprobación.
  - Reintentar la misma huella nunca vuelve a crear.
- **Nombres:** llevan familia, nombre y fecha. Amazon no deja dos campañas activas con el mismo nombre: en producción solo hay duplicados entre campañas archivadas. Así, dos lotes paralelos del mismo grupo el mismo día no pueden duplicarse.
- **El servidor no confía en la página:**
  - Vuelve a armar el plan desde la base al confirmar y lo compara por huella.
  - Exige que las sugerencias de Amazon cubran exactamente las semillas vigentes y que el bid del grupo sea su mediana.
  - Cada bid queda dentro de piso y techo de su moneda (MXN 1–45, USD 0.10–2.50), así que una sugerencia inventada no pasa del techo.
  - El token es obligatorio para crear, pausar, reconciliar, registrar, lanzar y retirar.
- **Moneda:** sale de un solo mapa por plataforma. Los montos se manejan como `Decimal` y se redondean a 2 decimales solo al enviar. El presupuesto debe ser mayor o igual que cada bid.
- **Orden de creación:** la exact va primero, así que nunca queda una discovery sin destino de harvest.
- **Impulso:** nace pausado, se activa al final y se revierte si falla, con candado por producto y listing dentro de una transacción.
- **Hipótesis refutada:** sospeché que las keywords de más de 10 palabras serían rechazadas. El único lote real creó «arras de boda matrimoniales set con baño de oro de 24 k» (12 palabras) y Amazon la aceptó.
- **Texto de fuera en la página:** `fabrica.js` y `productos.js` no usan `innerHTML`; todo pasa por `textContent`. Las plantillas escapan el texto y no usan `|safe`.
- **NH-/SET-:** no hay emparejamientos por parecido de nombre ni de SKU. Producto ↔ publicación sale de un mapa exacto de SKU, y anuncio ↔ publicación del ASIN exacto. El problema NH/SET de la falla 2 viene de compartir ad groups, no de parecido.
- **Modo por producto:** la pausa por producto solo se enciende por un camino, con límites guardados (en producción no están, así que es coherente que esté en práctica). La meta en vivo muestra el target que congeló el ciclo. Hoy casi no actúa: en MX, 1 de 249 productos tiene meta; en US, 0.
- **Etiquetas:** son solo texto de pantalla; no cambian targets ni presupuestos.

---

## Textos visibles con jerga (lista corta)

- **Mensajes del CLI que llegan a la pantalla:**
  - «--tipo-producto es obligatorio sin familia unica en los productos»
  - «--listing-ids no puede ser vacio…»
  - «objetivo manual v2 … fuera de la banda [10, 45]»
  - «… fuera de NUMERIC(6,2)»

  Salen de `fabrica_web.planificar` (`error(422, scrub(str(exc)))`).
- **Nombre del lote:** «web-» seguido de 64 caracteres hexadecimales, en el historial y en el aviso de resultado.
- **Detalle del lote:**
  - «Resultado incierto: Amazon pudo crear recursos sin devolver su identificador… La pausa solo alcanza campañas con identificador conocido».
  - Encabezados «ID externo» y «Verificación de creación».
- **Ficha del plan:**
  - «Procedencia del target», «Fracción del margen», «Target derivado del margen».
  - «target: margen de la plataforma (se ajusta cada ciclo)» (`fabrica.js:271`).
- **Acciones del lote:** «Reconciliar… registro local de un lote reconciliado» (`fabrica.html:198-199`) y «Token de Orbit».
- **Otros textos de la fábrica:** «conserva su procedencia en el plan» (`fabrica.html:80`) y «Puja predeterminada del grupo».
- **Productos** (detectados por el subrevisor):
  - «Un goal de campaña con target manda sobre ella».
  - «la config cambió mientras editabas».
  - «no tiene fila ni cobertura demostrada del reporte».
  - «No lanzable <número interno>: <código sin traducir>».
  - Motivos sin acentos: «metrica ausente», «reobservacion», «el tramo del halo…».
