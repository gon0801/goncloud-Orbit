# Lector ejecutor · ARREGLOS 04 (plan + DISENO.md + DISENO-bloqueos.md)

Copia: `la copia de trabajo del plan`, HEAD `a4644aad`, archivos del plan sin commit.
Todo lo de abajo lo corrí. Las pruebas, con `python -m pytest -q -p no:cacheprovider` (y una con `uv run --frozen --python 3.12`, que sí funciona aquí). Producción, solo con `lee_prod.sh` (SELECT).

Resumen: **3 bloqueantes, 15 no bloqueantes.**

---

## Bloqueantes

### 1. B-A.4: su `Acepta` no puede salir 0 aunque se haga exactamente lo que pide la fila

- **Dónde:** plan, fila B-A.4.
- **Hallazgo:** la fila manda reemplazar solo la sección «Limpieza de product ads muertos (ORBIT 06)» y no tocar la de inertes. Pero `docs/DEPLOY.md` nombra `archivar-anuncios` y `reponer-anuncios` también en otra sección (la de la migración 0011, líneas 1556-1557). El `Acepta` busca esas palabras en todo el archivo, así que va a fallar. El ejecutor tendría que editar un párrafo que la fila no le autoriza, o dejar el bloque en rojo.
- **Evidencia** (simulé el reemplazo de la sección 2314-2368 en una copia y corrí el `Acepta` tal cual):
  ```
  $ awk 'NR<2314 || NR>=2369' docs/DEPLOY.md > $S/ba/DEPLOY.md && ! rg -q -e archivar-anuncios -e reponer-anuncios $S/ba/DEPLOY.md && test -e tools/archiva_inertes.py; echo rc=$?
  rc=1
  $ rg -n -e archivar-anuncios -e reponer-anuncios $S/ba/DEPLOY.md
  1556:atrás YA implementada — la misma que obligó a `reponer-anuncios` antes de
  1557:`archivar-anuncios` (hallazgo de CodeRabbit, PR #116). La reversa borra por
  ```
  Con la corrección de abajo aplicada a la misma copia, el `Acepta` da `rc=0`.
- **Corrección pegable** (al final del contenido de B-A.4):
  > En el párrafo de la migración 0011 (`docs/DEPLOY.md:1556-1557`), la frase «la misma que obligó a `reponer-anuncios` antes de `archivar-anuncios` (hallazgo de CodeRabbit, PR #116).» queda «la misma que se exigió al archivado de anuncios de ORBIT 06, ya retirado (hallazgo de CodeRabbit, PR #116).».

### 2. B-A.2: la búsqueda de «nada lo usa» todavía incluye `archiva_inertes`, y eso dispara la orden de parar el bloque

- **Dónde:** plan, fila B-A.2. Es lo que quedó de cuando el bloque también borraba `tools/archiva_inertes.py`.
- **Hallazgo:** la fila manda correr `rg ... -e archiva_inertes ...` y dice «Si aparece un llamador fuera de los que lista el diseño, para el bloque». Como `archiva_inertes` se queda, la búsqueda encuentra el archivo mismo y comentarios en otros tres archivos que no están en la tabla «Qué se borra» del diseño (`app/cli.py`, `app/ads/archivar.py`, `app/ads/write.py`). Un ejecutor que siga la regla al pie de la letra para B-A esa noche.
- **Evidencia:**
  ```
  $ rg -n "archiva_inertes" app tools
  tools/archiva_inertes.py:44:    docker exec -i orbit-app-1 python - < tools/archiva_inertes.py
  tools/archiva_inertes.py:46:      --esperado 12 --go "<literal del dueno>" < tools/archiva_inertes.py
  tools/archiva_inertes.py:50:      < tools/archiva_inertes.py
  tools/fabrica_campanas.py:39:La mutacion real tendra su propio sello v3 (patron archiva_inertes/
  tools/fabrica_campanas.py:79:# tipo (IndeterminateDatatype, leccion de archiva_inertes).
  app/api_dashboard.py:1423:# del predicado del tool (tools/archiva_inertes.py, _SQL_PLAN /
  app/fabrica_plan.py:938:# Acks 207 (mismo criterio que el aplicador y archiva_inertes)
  ```
  La consulta `keyword_archivo_manual` de esa misma fila también es de inertes; no estorba, pero no prueba nada de B-A.
- **Corrección pegable** (reemplaza la primera oración de B-A.2):
  > Evidencia de que nada lo usa, en `docs/evidencia/arreglos-04/B-A/sin-uso.txt`: salida de `rg -n -e archivar-anuncios -e reponer-anuncios -e archivar_anuncios -e archivar_product_ad -e productAds/delete app tools scripts verify .github docs/runbooks` antes del cambio (incluye `tools/despliegue/crontab-orbit.txt`). Los únicos archivos que pueden salir son `app/cli.py`, `app/ads/archivar.py` y `app/ads/write.py`; cualquier otro es un llamador nuevo.

### 3. B-D.2: la reparación en Amazon se dispara también cuando no se pudo leer el estado

- **Dónde:** plan, fila B-D.2, y diseño `DISENO-bloqueos.md:373`.
- **Hallazgo:** el script de lectura marca cada bloqueo con `ENABLED`, `PAUSED`, `ARCHIVED`, `AUSENTE` o `LECTURA_<estado>` (cuando la lista de Amazon no se pudo leer). La fila B-D.2 repara «cada línea con estado distinto de ENABLED», o sea que también entra `LECTURA_*`. En ese caso no se sabe si el negative bloquea. `quitar` lo archiva en Amazon, y eso **no se puede deshacer**. Si la clave no tiene señal vigente (hoy hay una así, `sin_lectura`), el bloqueo que pidió el dueño se queda quitado y ese término vuelve a gastar. Además, `antes.txt` también trae las líneas de conteo de páginas por lista, que no son bloqueos, y la fila deja marcadores `<veredicto>` / `<senal_vigente>` para que el ejecutor los arme a mano.
- **Evidencia:**
  ```
  $ rg -n -o "Por cada línea de .antes.txt. con estado distinto de ENABLED" plans/arreglos-04.md
  230:Por cada línea de `antes.txt` con estado distinto de ENABLED
  $ rg -n "LECTURA_<estado>" docs/evidencia/arreglos-04/DISENO-bloqueos.md
  373:# estado del id en Amazon: ENABLED, PAUSED, ARCHIVED, AUSENTE o LECTURA_<estado>.
  $ echo "select v.vigente, v.lectura, count(*) from bloqueo_veredicto b join jev_senal_vigente v on v.ad_group_id=b.ad_group_id join jev_senal s on s.id=v.senal_id and s.termino_sha256=b.termino_sha256 where b.tipo='aprobado' group by 1,2" | bash docs/evidencia/arreglos-01/lee_prod.sh
  True|ajena|47
  True|sin_lectura|1
  ```
  En `app/bloqueo_dueno_efecto.py:244-264`, `quitar` no vuelve a revisar el estado antes de mandar el borrado a Amazon.
- **Corrección pegable** (reemplaza el arranque de B-D.2 hasta «no reintentar a ciegas»):
  > Solo se reparan las líneas de bloqueo (las que empiezan con `amazon_mx|` o `amazon_us|`) cuyo estado es `PAUSED`, `ARCHIVED` o `AUSENTE`. Una línea `LECTURA_*` no se toca: volver a correr `lee_amazon.py`; si sigue sin lectura, anotarla en `reparacion.txt` como «sin lectura, no se reparó» y seguir con la siguiente. El bucle, con los valores tomados de la línea y sin escribir ningún marcador a mano:
  > ```
  > grep -E '^amazon_(mx|us)\|' docs/evidencia/arreglos-04/B-D/antes.txt | while IFS='|' read -r plat vid grupo kid estado senal; do
  >   case "$estado" in PAUSED|ARCHIVED|AUSENTE) ;; *) echo "$plat|$vid|$estado|no se reparó" >> docs/evidencia/arreglos-04/B-D/reparacion.txt; continue;; esac
  >   r=$(bash docs/evidencia/arreglos-01/escribe_api.sh /api/jev-bloqueo/quitar "{\"veredicto_id\": \"$vid\"}") || true
  >   echo "quitar $vid: $r" >> docs/evidencia/arreglos-04/B-D/reparacion.txt
  >   echo "$r" | grep -q '"paso": *"revertido"' || { echo "parar: $vid"; break; }
  >   if [ "$senal" != "-" ]; then
  >     r=$(bash docs/evidencia/arreglos-01/escribe_api.sh /api/jev-bloqueo/aprobar "{\"senal_id\": \"$senal\"}") || true
  >     echo "aprobar $senal: $r" >> docs/evidencia/arreglos-04/B-D/reparacion.txt
  >   else echo "$vid: sin señal vigente" >> docs/evidencia/arreglos-04/B-D/reparacion.txt; fi
  > done
  > ```
  > Si sale «parar», escribir en `reparacion.txt` el último paso de `bloqueo_efecto` de ese veredicto, leído con `lee_prod.sh`, y no reintentar. Archivar un negative PAUSED, ARCHIVED o AUSENTE no tiene vuelta atrás y no cambia lo que se gasta, porque ese negative no niega nada (diseño, «Reparación», paso 1).

---

## No bloqueantes (se corrigen en la misma ronda)

### 4. B-C se puede desplegar antes de medir el tamaño de página que B-D va a medir

- **Dónde:** plan, línea «Orden» (B-C no depende de nada) y B-D.3. Diseño, pregunta c.
- **Hallazgo:** si Amazon pagina de 100 en 100, la lista de keywords de MX (2,719, que medí hoy) ocupa 28 páginas y pasa el tope de 20. Con B-C, `_lista_todos` lanza `ListaIncompleta` en cada harvest de MX. El diseño dice que el primer ciclo que lo encuentra «corta su fase de apply». Hoy no se sabe el tamaño de página (diseño, pregunta c). La lectura de B-D.1 no escribe nada ni necesita B-B, y es justo la que lo mide.
- **Evidencia:** `select e.platform, count(*) from ad_entity e where e.kind='keyword' group by 1` → `amazon_us|1340`, `amazon_mx|2719`. `TOPE_PAGINAS_LIST = 20` (`app/apply_harvest.py:122`).
- **Corrección pegable** (nueva fila B-C.0):
  > | B-C.0 | Antes de desplegar B-C: escribir y correr `docs/evidencia/arreglos-04/B-D/lee_amazon.py` (fila B-D.1; solo lee) y guardar la salida en `docs/evidencia/arreglos-04/B-D/antes.txt`. Si en alguna plataforma `/sp/keywords/list` o `/sp/negativeKeywords/list` pasa de 15 páginas, parar B-C y escribirlo en `docs/evidencia/arreglos-04/B-C/diferencia.md`: el harvest de esa cuenta se detendría en cada ciclo. | test -s docs/evidencia/arreglos-04/B-D/antes.txt |

### 5. W-I.0: la compuerta busca las pruebas de X en un solo archivo

- **Dónde:** plan, fila W-I.0.
- **Hallazgo:** en ARREGLOS 01, la fila X.2 reparte sus pruebas `arreglos01_x_` entre `tests/test_api_fabrica.py`, `tests/test_fabrica_plan.py` y `tests/test_optimizer_goals.py`. Si X no pone ninguna en `test_optimizer_goals.py`, W-I.0 sale con 5 («no tests ran») aunque X ya esté en master, y W-I se queda esperando para siempre. Además, el diseño de X (`docs/evidencia/arreglos-2026-11-09/ads-escritura/opus/DISENO.md:486`) ya pone `exige_bajo_techo` en `goals_write._valida_pre_editar`. Si X lo implementó así, W-I lo duplica.
- **Evidencia:** hoy `-k arreglos01_x_` sale con rc 5. `python -c "from app.optimizer.goals import exige_banda_meta_manual, exige_bajo_techo, TECHOS_HUMANOS_POR_MONEDA"` da `ImportError` hoy, así que esa comprobación sí puede fallar.
- **Corrección pegable:** `Acepta` de W-I.0 → `PYTHONPATH=. uv run --frozen --python 3.12 python -c "from app.optimizer.goals import exige_banda_meta_manual, exige_bajo_techo, TECHOS_HUMANOS_POR_MONEDA"`. Al final de W-I.2: «Si X ya llama `exige_bajo_techo` en `_valida_pre_editar`, no se repite: se agregan solo los campos que falten.»

### 6. W-A.2: tres pruebas que dependen de lo borrado no están en la lista

- **Dónde:** plan, fila W-A.2.
- **Hallazgo:** la lista «`test_serie_*` y `test_arma_serie_*`» deja fuera:
  - `test_ventana_efectiva_default_recorte_y_validaciones` (`tests/test_api_dashboard.py:392`), que llama `_ventana_efectiva`.
  - `test_series_sql_filtran_kind_campaign_en_ad_entity` (`:451`), que lee `_SQL_SERIE_PLATAFORMA` y `_SQL_SERIE_CAMPANA`. También protege `_SQL_CAMPANAS_30D`, y esa garantía (que no se cuente el dinero dos veces) se tiene que conservar.
  - `test_sin_dsn_de_lectura_fail_closed_503` (`:530`), que pide `/series/plataforma`. Sin la ruta da 404 y no 503.
- **Evidencia:** `awk` sobre los nombres de las pruebas de `tests/test_api_dashboard.py` (líneas 392, 451 y 530) y el `rg` de cada símbolo.
- **Corrección pegable** (agregar en W-A.2): «Borrar `test_ventana_efectiva_default_recorte_y_validaciones`. En `test_series_sql_filtran_kind_campaign_en_ad_entity`, dejar solo `_SQL_CAMPANAS_30D` en el bucle y aplicar el mutante `OR TRUE` sobre `_SQL_CAMPANAS_30D`. `test_sin_dsn_de_lectura_fail_closed_503` pide `/api/dashboard/resumen`.»

### 7. W-B.2: una prueba que usa `plan_desde_json` no está en el `Acepta`

- **Dónde:** plan, fila W-B.2.
- **Evidencia:** `tests/test_estimacion_aislamiento_fabrica.py:105` llama `fp.plan_desde_json(plan_json)`, que W-B borra. Ese archivo no está en el `Acepta` de W-B.2, así que la falla solo saldría en CI.
- **Corrección pegable:** agregar a W-B.2: «`tests/test_estimacion_aislamiento_fabrica.py:100-104` queda `plan = fp.plan_v2_desde_json(plan_json)`.» Y sumar `tests/test_estimacion_aislamiento_fabrica.py` al `Acepta`.

### 8. W-F.1: otra prueba depende del texto viejo, y dos frases nuevas se leen raro

- **Dónde:** plan, fila W-F.1. Diseño, sección W-F.
- **Evidencia:** `tests/test_api_fabrica.py:1062` exige `"Los datos no son válidos" in respuesta.text`, y W-F cambia ese texto por «Revisa …». La fila solo nombra `tests/test_api_write.py:1705`.
- **Textos que lee el dueño:** con la regla del diseño, a `base_config_version_id` le sale «Revisa «la página (recárgala)» (falta).», y a un campo de más, «Revisa «un dato que no se esperaba» (no es válido).». Son confusas para alguien que no programa.
- **Corrección pegable:** en W-F.1, agregar «`tests/test_api_fabrica.py:1062` pasa a exigir «Revisa».» En el diseño, en `mensaje_validacion`: «Si algún error es de `base_config_version_id`, `hoja_id`, `queue_id`, `decision_id` o `campana_id`, el mensaje completo es «La página quedó vieja: recárgala y vuelve a intentar.». Un error `extra_forbidden` da «La pantalla mandó un dato que no se esperaba: recárgala y vuelve a intentar.».»

### 9. W-D.3: la revisión de la ficha no puede fallar

- **Dónde:** plan, fila W-D.3 (ficha `resumen.md`, paso «Abrir Resumen»).
- **Hallazgo:** la fila pide buscar «tal como se veía hace». Pero el `<caption>` nuevo también lleva esa frase, y sale siempre. Si la línea nueva de la tarjeta se rompe, la ficha pasa igual.
- **Evidencia:** `'tal como se veía hace' in caption` → `True`; `'en las ventas que todavía llegan' in caption` → `False`.
- **Corrección pegable:** «… el paso «Abrir Resumen» busca «en las ventas que todavía llegan» (solo está en la línea de la tarjeta) y, aparte, el caption nuevo.»

### 10. W-D.5: la consulta para comparar es ambigua y el `Acepta` no compara

- **Dónde:** plan, fila W-D.5. Anexo A del diseño.
- **Hallazgo:** la consulta `edad` del anexo junta MX y US y usa `now() - interval '7 days'`. Si se corre tal cual, no da el número de MX ni el mismo corte que dio la respuesta. El `Acepta` (`test -s`) pasa aunque los números no cuadren.
- **Corrección pegable** (contenido de W-D.5):
  > ```
  > r=$(curl -fsS "http://10.13.13.1:8010/api/dashboard/resumen?periodo=7&canal=amazon_mx")
  > read d h c v <<<"$(echo "$r" | python3 -c 'import json,sys;x=json.load(sys.stdin)["canales"]["amazon_mx"]["comparison"];print(x["desde"],x["hasta"],x["observado_hasta"],x["totals"]["ad_revenue"])')"
  > s=$(echo "SELECT sum(ad_revenue) FROM (SELECT DISTINCT ON (o.ad_entity_id, o.metric_date) o.ad_revenue FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id WHERE e.kind = 'campaign' AND e.platform = 'amazon_mx' AND o.metric_date BETWEEN date '$d' AND date '$h' AND o.observed_at <= timestamptz '$c' ORDER BY o.ad_entity_id, o.metric_date, o.observed_at DESC) t" | bash docs/evidencia/arreglos-01/lee_prod.sh)
  > printf 'comparison %s\nconsulta %s\n' "$v" "$s" > docs/evidencia/arreglos-04/W-D/comparacion.txt
  > ```
  > `Acepta`: `python3 -c "import sys;from decimal import Decimal as D;a,b=[l.split()[1] for l in open('docs/evidencia/arreglos-04/W-D/comparacion.txt')];sys.exit(D(a)!=D(b))"`

### 11. B-D.3: «debe salir 0» no lo revisa nadie, y en un caso no puede cumplirse

- **Dónde:** plan, fila B-D.3.
- **Hallazgo:** el `Acepta` es `test -s despues.txt`, así que pasa aunque el script salga con 1. Además, el script sale con 1 si no queda ningún bloqueo vivo («al menos un bloqueo»). Eso pasa si todos los reparados quedaron sin señal vigente y no había ninguno ENABLED.
- **Corrección pegable:** «Guardar al final de `despues.txt` la línea `salida=<código del script>`. `Acepta`: `grep -qx 'salida=0' docs/evidencia/arreglos-04/B-D/despues.txt || grep -q 'sin bloqueos vivos: todos quedaron sin señal vigente' docs/evidencia/arreglos-04/B-D/reparacion.txt`.»

### 12. B-B.3: la pantalla de Propuestas va a seguir mostrando códigos

- **Dónde:** plan, fila B-B.3. Diseño, sección F9.
- **Hallazgo:** después de B-B, `cortes.js` pasa el motivo por `Orbit.etiqueta`. Pero los motivos que salen de `_fallo_de` y de `_ejecuta` no tienen texto: `http_400` (y cualquier `http_<n>`), `sin_respuesta`, `lista_ambiguo`, `lista_truncado` y `sin_externos`. `formato.js:46-60` los muestra como «Http 400», «Lista ambiguo» o «Sin externos», que es jerga. La tabla de bloqueos de `cortes.html:306` ya los pinta igual.
- **Corrección pegable** (sumar a `MOTIVO_BLOQUEO` en la fila B-B.3): `"sin_respuesta": "Amazon no respondió"`, `"lista_ambiguo": "no se pudo leer la lista de Amazon"`, `"lista_truncado": "Amazon no entregó la lista completa"`, `"sin_externos": "faltan los datos del grupo en Amazon"`. Y para `http_<n>`: «en `cortes.js`, un motivo que empieza con `http_` se muestra como «Amazon respondió con error».»

### 13. W-C.1 manda editar líneas de `docs/DASHBOARD.md` que W-A ya borró

- **Dónde:** plan, filas W-A.3 y W-C.1.
- **Evidencia:** W-A.3 quita la fila 3 de la tabla (`:24`) y la sección 3.1 (`:103-134`, donde está `:112`). W-C.1 después pide «`docs/DASHBOARD.md:24,112` con el reloj nuevo».
- **Corrección pegable** (W-C.1): «En `docs/DASHBOARD.md`, la sección 3.3 (Resumen de campañas) dice: «La ventana es [D-30, D-1] con el hoy del Resumen: la fecha local mínima de los canales pedidos (`hoy_resumen`).»»

### 14. Faltan los cruces con ARREGLOS 03 y ARREGLOS 02

- **Dónde:** plan, línea «Cruces».
- **Evidencia:** archivos que nombran los dos planes: `app/api_dashboard.py`, `app/cli.py`, `app/dashboard_resumen.py`, `app/notifica.py` y `app/ui.py` (ARREGLOS 03), y `app/ads/write.py` (ARREGLOS 02, C). ARREGLOS 03 D-F.3 le quita `zona` a `Canal`, cambia `hoy_por_canal` y la línea `"zonas"` de `resumen()` (`:955`), justo en la función que edita W-D (`:951`). ARREGLOS 03 también edita `app/cli.py:702-720`, que está entre los subparsers que borra B-A (`:678-699` y `:725-745`).
- **Corrección pegable** (agregar a «Cruces»):
  > ARREGLOS 03 corre en paralelo y comparte `app/dashboard_resumen.py` (D-F.3: `Canal.zona`, `hoy_por_canal`, `"zonas"` de `resumen()`), `app/cli.py` (D-A, D-K), `app/notifica.py` (D-K, P-B), `app/api_dashboard.py` y `app/ui.py` (asesoría de cortes). W-C y W-D usan `hoy_resumen` tal como quede; si D-F.3 ya entró, `bloque_canal` y `resumen()` se editan sobre su versión. ARREGLOS 02 C toca `_bid_payload` de `app/ads/write.py`; B-A borra otras líneas de ese archivo. Los choques son de texto y se resuelven por contenido.

### 15. «Correr orbit-verify» sin el comando

- **Dónde:** plan, filas W-A.3, W-D.3, W-M.3 y B-B.3.
- **Corrección pegable:** «`.cursor/skills/verify-orbit/helpers/orbit-verify launch && .cursor/skills/verify-orbit/helpers/orbit-verify drive-resumen; .cursor/skills/verify-orbit/helpers/orbit-verify cleanup`». En W-M.3 va `drive-productos` y en B-B.3 `drive-cortes`. En W-A.3, agregar: «los chequeos `seed_*` y `mx_all_cost_none` del ayudante se leen de `canales.<canal>.series` de `/api/dashboard/resumen`.»

### 16. W-L: no hay ninguna prueba en producción de que Amazon acepte un negative keyword en la campaña por producto

- **Dónde:** plan, fila W-L.2. Diseño, W-L (por producto → `_pasos_negativos`).
- **Hallazgo:** el único lote real nunca creó un negative fuera de la automática, y ningún harvest ha negado hermanas (`external_ids ? 'hermanas'` da 0 de 11). Hasta donde sé, Amazon solo deja negar productos (no palabras) en un ad group de segmentación por producto. Si rechaza el paso, el lote queda a medias con campañas ya creadas.
- **Evidencia:** `select count(*) filter (where external_ids ? 'hermanas'), count(*) from harvest_job` → `0|11`. El `fabrica_lote_paso` del lote real no tiene ningún paso `negative_keyword` (`product_targeting|target|applied|6`).
- **Corrección pegable** (W-L.2): «En `product_targeting` no se agregan negatives mientras no haya un ack 207 sin error de un NEGATIVE_EXACT en un ad group de segmentación por producto (sonda de APPLY.md §11d). La prueba de W-L.1 deja de exigirlo en esa campaña.»

### 17. W-M: el día de la lista y el del retiro pueden diferir, y «vendieron» incluye publicaciones sin producto

- **Dónde:** plan, línea «Orden» (W-M no depende de nada) y fila W-M.2.
- **Hallazgo:** el diseño dice que la lista y el retiro usan el mismo día porque W-C ya cambió el reloj. Pero W-M no depende de W-C: antes de W-C, la lista sigue con el día UTC. Además, `vendieron = listing_ids - sin_pedido` también cuenta las publicaciones sin `product_id`, y el texto le diría al dueño que esas «vendieron».
- **Corrección pegable:** W-M → `depende: W-C`. En W-M.2: «`vendieron` son solo las publicaciones con producto conocido y alguna venta. Las que no tienen producto se quitan del plan y no se mencionan.»

### 18. Textos viejos en los diseños (del renombre y del recorte)

- **Dónde y qué dicen hoy:**
  - `DISENO.md:4`: cita `BLOQUES-pantallas.md`, que no existe.
  - `DISENO.md:9`: el «Para quién» habla de las semillas NH-/SET- (es W-K, que salió del plan).
  - `DISENO.md:23` y `:25`: dan W-K como «Dentro» («Va antes de W-J, W-K y W-L»).
  - `DISENO.md:35`: pide que la ingesta lea `CANALES[...].zona`, pero ARREGLOS 03 D-F.3 la quita.
  - `DISENO-bloqueos.md:3`, `:9-10` y `:22`: cuentan la falla 11 y «dos herramientas» como dentro.
  - `DISENO-bloqueos.md:127-128`: dice que B-A borra `tools/archiva_inertes.py:354`.
- **Corrección pegable:** al inicio de cada diseño, «Alcance vigente: el de `plans/arreglos-04.md`. W-K está en `PENDIENTE-W-K.md` y la falla 11 de `apply.md` va en ARREGLOS 05; donde este diseño diga otra cosa, manda el plan.» Y borrar la línea 4 de `DISENO.md`.

---

## Verificado y correcto

- Los 40 archivos de prueba y las 4 reproducciones que nombra el plan existen.
- Cada `-k` de prueba nueva (`arreglos04_w_e_`, `w_g_`, `w_h_`, `w_i_`, `w_j_`, `w_l_`, `w_m_`, `ba_`, `bb_`, `bc_`) da hoy «no tests ran» (rc 5); probé uno también con `uv run --frozen --python 3.12`.
- El `Acepta` de W-A.0 sale con 1 hoy (archivos sin commit): puede fallar. Las cuatro ayudas de `arreglos-01` ya están versionadas.
- Las reproducciones de web, fábrica y apply fallan hoy (33 fallan, 1 pasa), incluidas todas las que el plan promueve.
- Línea base verde de los `Acepta` existentes: 222 + 17 + 248 + 83 + 165 + 1,018 pruebas pasan.
- Las citas `archivo:línea` de `api_dashboard.py`, `dashboard_resumen.py`, `ui_metricas.py`, `ui.py`, `cli.py`, `notifica.py`, `cycle.py`, `pantalla_busquedas.py`, `pantalla_ruido.py`, `fabrica_campanas.py`, `fabrica_web.py`, `api_fabrica.py`, `fabrica.js`, `bloqueo_dueno_efecto.py`, `apply_harvest.py`, `smoke_apply.py`, `cortes.js`, `etiquetas.py` y `api_write.py` (rutas de `jev-bloqueo`) dicen lo que afirma el plan.
- La rama muerta de `_frase_tarjeta` de verdad no corre: `variacion` solo da `None` o `cobertura_distinta`.
- W-A: `/series` solo aparece en `app/api_dashboard.py` dentro de `app tools scripts verify .github docs/runbooks`; ninguna plantilla usa `data-serie`, `kpis_serie` ni `clase_cambio`.
- W-B: en producción, `1|0|1` lotes, 0 configuraciones `v1` y la configuración vigente (51) es `v2`; `rg -- "--productos" docs/runbooks` sale vacío.
- W-C.3: `_ventana_campanas(("amazon_mx",), 13-nov 03:00 UTC)` da (13-oct, 11-nov), igual que `ventana_resumen(periodo=30)`.
- W-D: `_SQL_DIAS_A_LA_EDAD` se puede leer como SQL válido de Postgres (pglast); `v_metric_latest` tiene el mismo colapso.
- W-E: `DIAS_MADUREZ_CORTES = 10` y `DIAS_INMADUROS = 8`; el filtro `fecha_corta` existe.
- W-F.3: la sonda que describe la fila falla hoy (el `detail` es una lista con `"20%"`), y la tabla de manejadores trae el de FastAPI.
- W-G: en producción, el ciclo 174 tuvo 31 cortes de práctica en amazon_us; `notifica_encola` y `aviso_corte_encolado` solo se usan en `cycle.py:3647` y en `tests/test_notifica.py`.
- W-H: solo las pruebas usan `--decided-at`, con fechas pasadas.
- W-J: `tools/fabrica_campanas.py` va dentro de la imagen (Dockerfile:22), así que la sonda de producción puede importarlo.
- W-L: `Semillas(keywords, asins, negativos, exact)` hace que `Semillas(("arras",), (), (), ("arras",))` signifique lo que dice la fila.
- W-L no depende de W-K (depende de W-J y no toca `_SQL_TERMINOS`). Ningún `Acepta` de B-A depende de borrar `archiva_inertes`; B-A.4 exige que siga existiendo.
- B-A: `archivar_product_ad`, `archivar-anuncios` y `/sp/productAds/delete` solo están en `cli.py`, `ads/archivar.py` y `ads/write.py`; `APPLY.md` no cita ese par; en producción, `0|0|0` en las tablas de archivo y la última sonda fue el 12-sep.
- B-B: `_coincidencias`, `_errores_de_ack`, `_json_seguro`, `ESTADO_WIRE_ENABLED`, `_cliente_ajuste` y `_SQL_EXTERNALES_GRUPO` existen; `rechazo_amazon`, `ListaIncompleta` y `_negative_que_bloquea` no están hoy en `app/`, así que las pruebas en producción fallan hasta desplegar.
- B-D: en producción hay 48 bloqueos con último paso `aplicado` (MX 25, US 23). `perfiles_aceptados`, `PerfilAds.platform`, `AdsClient.list_objects`, `AdsCredentials.from_secrets_dir`, `_lista_filtrada` (que llama `list_sellado(...).json()`) y `_externos` existen como los usa el diseño. Las rutas `/api/jev-bloqueo/quitar` y `/aprobar` piden `veredicto_id` y `senal_id` y responden `paso`. `escribe_api.sh` acepta esa ruta.
- Fichas: `resumen.md`, `productos.md` y `cortes.md` existen, y `orbit-verify` tiene `drive-resumen`, `drive-productos` y `drive-cortes`.
