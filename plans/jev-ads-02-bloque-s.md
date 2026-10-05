# Cómo construir el bloque S de JEV ADS 02

Esta guía te lleva de `master` a la señal por búsqueda-en-grupo desplegada y
apagada. Cubre los pasos S.1 a S.8 de [JEV ADS 02](jev-ads-02.md). No construye
ningún efecto sobre Ads: el bloque E queda fuera.

Qué construir está en el
[diseño](../docs/superpowers/specs/2026-10-04-jev-ads-02-design.md) y en su
[bosquejo de tipos](../docs/evidencia/jev-ads-02/diseno/bosquejo.py). Esta guía
dice dónde cae cada pieza en el código de hoy, qué prueba se escribe primero y
qué candados del repo vas a encontrar. Si la guía y el diseño se contradicen,
sigue el diseño y avisa al lead.

Los números de línea son del commit `25cebe7`. Si el archivo ya cambió, busca
el símbolo por nombre.

## Antes de empezar

1. Comprueba que el PR #402 está en `master`. Trae el diseño, el bosquejo y
   esta guía.
2. Lee el diseño completo y las secciones A a E del bosquejo.
3. Levanta un Postgres local. Las pruebas con base no corren en un PR.
4. Trabaja cada paso en su rama, con un PR por paso.

## Reglas que valen en todos los pasos

**Prueba primero.** Escribe la prueba, mírala fallar por la razón correcta y
después escribe el código. Para cada prueba que esta guía nombra, registra en
la evidencia un mutante: el cambio de una línea que la pone en rojo.

**Lo que CI no ve en un PR.** En un PR solo corre el job de CI `rapido`: pre-commit,
`tests/test_architecture.py`, `tests/test_precommit_hooks.py` y
`tests/test_chat_context_guard.py`, sin Postgres. La batería con base corre en
el push a `master`. Antes de abrir el PR, corre en local los archivos de prueba
que tocaste y los que cada paso nombra en "Qué vas a encontrar" y en
"Comprueba". Anota el comando y el conteo en la evidencia del paso.

**Migraciones.**

- Toma el siguiente número libre al escribirla. Hoy S.1 sería `0051` y S.3
  `0052`. Si otra rama tomó el número, usa el siguiente.
- Escribe `BEGIN;` y `COMMIT;` dentro del archivo. No la hagas idempotente: el
  despliegue corre cada archivo una sola vez.
- Escribe la reversa en un archivo hermano `NNNN_reversa_<nombre>.sql`.
- Pon a cada tabla nueva sus dos triggers `prohibir_mutacion()`: uno
  `BEFORE UPDATE OR DELETE ... FOR EACH ROW` y otro `BEFORE TRUNCATE ... FOR
  EACH STATEMENT`. El modelo está en `migrations/0040_ads_report_result.sql`.
- Pon un índice por cada clave foránea que la clave primaria no encabece.
- Declara cada permiso con un `GRANT` explícito. Cierra la migración con un
  bloque `DO` que falle si falta o sobra un privilegio. Si el bloque pregunta
  por una tabla que una base de prueba puede no tener, usa antes `to_regclass`.
- No escribas en ninguna migración los textos `motivo IN (`, `procedencia IN (`
  ni `CREATE VIEW v_decision_huerfana`. `tests/test_apply_schema.py` busca la
  última migración que los contenga y tomaría la tuya.

**El conteo de tablas.** `tests/test_schema_docs.py` aplica todas las
migraciones y exige que el número de tablas sea el que declaran
`verify/Launch.md` y `verify/Doctor.md`. Hoy es 63. S.1 lo sube a 65 y S.3 a
70. Actualiza los dos archivos en el mismo PR, con los números de sus
comentarios: hoy dicen "14 vistas", "daría 77" y "64 con headers". Tras S.1 son
14, 79 y 66. Tras S.3, que agrega una vista, son 15, 85 y 71.

**Tamaño y complejidad.** Ningún módulo de `app/` pasa de 900 líneas. Ninguna
función pasa de complejidad 22, 25 ramas ni 80 sentencias. Si una función
llega al límite, pártela por lo que cada parte sabe. No uses `noqa` para esquivar el
límite.

**Repo público.** Usa búsquedas y productos inventados en pruebas, fixtures y
documentos. Ningún nombre de producto ni SKU real.

**Sin TypeSafe real.** Todas las pruebas usan un `pedir` falso o
`httpx.MockTransport`. Ningún paso de construcción llama al proveedor.

**Evidencia.** Guarda comandos, salidas, mutantes y el SHA de cada paso en
`docs/evidencia/jev-ads-02/ejecucion/<paso>/`.

**Scripts de despliegue.** Cada paso que despliega trae `desplegar.sh`,
`rollback.sh` y `checklist.sh` en su carpeta de evidencia. Pártelos de los de
`docs/evidencia/jev-ads-01/ejecucion/2.3/`. Un paso con migración trae además
`ensayo.sh` y `permisos.sql`.

- Conserva las salidas del checklist: 0 es comprobado, 1 es una falla y 3 es
  sin fallas pero todavía sin la prueba posterior.
- Agrega una cuarta salida: 4 es "no pude medir". El checklist empieza
  comprobando que el servidor contesta, con
  `docker inspect -f '{{.State.Running}}' orbit-app-1`. Si no contesta, sale
  con 4. Un valor leído vacío lleva a la salida 4, no a la 1. El checklist de
  2.3 cuenta una conexión caída como falla, y una falla puede acabar en una
  reversa.
- Cambia otra regla del checklist de 2.3, que cuenta un ciclo en `running`
  como falla. Un ciclo en curso todavía no terminó: el checklist sale con 3.
- Cuenta "posterior" desde el arranque del contenedor nuevo
  (`docker inspect -f '{{.State.StartedAt}}' orbit-app-1`), no desde el sello.
  El sello se imprime al empezar `desplegar.sh`. Un ciclo que corrió con el
  contenedor viejo no prueba nada.
- Quita del checklist de 2.3 las líneas que ya no son ciertas: hoy producción
  tiene fichas, revisiones y eventos de Jev. Antes de pedir la revisión, corre
  el checklist contra producción, que solo lee, y guarda la salida como
  `checklist-antes-del-deploy.txt`. Cada `FALLA` de esa corrida debe ser algo
  que el despliegue cambia.
- El preflight exige que ningún comando `app.cli` esté corriendo dentro del
  contenedor: `docker top orbit-app-1` no lista ninguno. Eso cubre las
  ingestas, el ciclo y la corrida de precios. Repite las guardas justo antes
  del `docker compose up`. No pongas guardas por hora.
- Pon en `rollback.sh` las guardas del preflight de `desplegar.sh`. El de 2.3
  recrea el contenedor sin mirar si hay un ciclo corriendo.
- Corre `ensayo.sh` antes de pedir la revisión y guarda su salida. Se repite
  sobre el squash antes de desplegar.
- Ningún script imprime una contraseña ni un valor `ORBIT_DSN_*`. El bloque
  que crea un login manda su salida a `/dev/null` y reporta solo su código.
  Antes de guardar una salida en el repo, cambia `/Users/dn` por `~`.

**Revisión y cierre.** Cada paso con código pasa una revisión cruzada con un
revisor distinto del autor (`cross-review.ps1`) antes del merge. Solo un
hallazgo bloqueante y reproducible abre otra ronda. Haz el merge con
`gh pr merge <n> --squash --match-head-commit <sha>`. Antes de empezar el paso
siguiente, comprueba que el job de CI `completa` del push a `master` terminó
en `success`. Marca la fila del paso en `plans/jev-ads-02.md` y agrega una
entrada a `docs/CHAT-CONTEXT.md` en el mismo PR.

## Restricciones del diseño

Las reglas del diseño están en el diseño. Antes de cada paso, relee la sección
que le toca: "La regla" y "Tipos que fijan invariantes" para S.2,
"Persistencia" para S.1 y S.3, "Roster probado" para S.1 y S.4, "El trabajo
fuera del ciclo" para S.4 y "Superficies" para S.5 y S.8.

El diseño no dice estas cuatro cosas. Son interpretación del lead a partir del
código. Si alguna no te cuadra, pregunta antes de cambiarla.

- No toques `componer`, `_solo_no_activo` ni el trigger
  `jev_par_evento_encadenado`, ni las pruebas que los fijan. El diseño se
  apoya en que se comportan como hoy.
- Calcula `satisfacen`, `evaluados`, `miembros` y `productos_ok` desde los
  pares. `componer` no devuelve conteos cuando el resultado es
  `Indeterminado`.
- `jev_lectura.py` no puede importar `app.optimizer.windows`. Por eso
  `MAX_EDAD_SYNC` le llega como parámetro.
- El CLI manual de JEV ADS 01 no cambia de comportamiento, salvo el arreglo de
  reanudación de S.2.

## S.1: registra el acta de listado en la ingesta

Al terminar, cada corrida de `ingest structure` deja escrito qué listó Amazon
por plataforma y por ad group. Nada lee todavía esas filas.

### Qué vas a encontrar

- `_plan_items` (`app/ads/structure_plan.py:149`) está en el tope exacto:
  complejidad 22 y 80 sentencias. No le cabe una línea.
- `_sellar_run` y `_SQL_ABRIR_RUN` los usa también `app/ads/reports.py`. El
  acta no puede vivir dentro de `_sellar_run`.
- Cinco pruebas corren `sync_structure` sobre bases que solo aplican unas
  pocas migraciones. Si la ingesta escribe el acta y la base no tiene las
  tablas, las cinco fallan con `UndefinedTable`.
- `tools/fabrica_campanas.py` también llama a `sync_structure`. Por código
  lista las dos plataformas completas, igual que el cron, y dejará su acta.

### Pruebas primero

Escríbelas en `tests/test_structure_sync.py`, junto a las que nombro.

1. Junto a `test_paginacion_incompleta_segun_totalresults_da_error`: la página
   1 declara `totalResults` 3 y trae `nextToken`, y la página 2 no declara
   total y trae un item. Hoy pasa en silencio. Debe fallar con "paginacion
   incompleta".
2. `listar_con_prueba` devuelve el total declarado cuando Amazon lo manda y
   `None` cuando no lo manda. `None` no es cero.
3. Junto a `test_plan_items_product_ads_filtra_estado_padre_y_asin`: la
   función pura del acta, sobre un payload armado a mano. Cubre un anuncio vivo
   escrito, un anuncio no archivado descartado por un filtro, un anuncio
   archivado (no cuenta), un anuncio sin `adGroupId` (suma a
   `product_ads_sin_grupo`) y un ad group sin anuncios (tiene fila, con cero
   vivos).
4. `huella_anuncios` cambia si cambia un solo `adId` y no cambia con el orden
   del payload.
5. Junto a `test_product_ad_archivado_en_amazon_deja_de_figurar_vivo_en_el_cache`:
   una corrida ok deja una fila por plataforma y una por ad group escrito, en
   la misma transacción que sella `ingest_run`.
6. Una corrida que falla a media escritura no deja acta. Usa el patrón que ya
   existe: `monkeypatch.setattr(estructura_modulo, "_SQL_UPSERT_STATE", ...)`
   con una tabla inexistente. Agrega una variante que parchea
   `estructura_modulo._sellar_run` para que falle solo con `ok=True`: el acta
   debe revertirse con todo lo demás.
7. La ingesta no cambia lo que escribe en `ad_entity` y `ad_entity_state`.
   `test_sync_y_resync_estructura_en_vivo` fija `rows_written == 9`: el acta no
   suma a ese conteo.

### Cambios

1. Escribe la migración A con las dos tablas del diseño, sección
   "Persistencia". Agrega lo que el diseño no trae: CHECK de conteos no
   negativos, CHECK de formato de `huella_vivos` (`^[0-9a-f]{64}$`) e índice
   sobre `ads_listado_grupo (ad_group_id)`. Da `INSERT` solo a `app_ingest`,
   que es el rol con que corre toda llamada a `sync_structure`. No nombres a
   `app_jev` en el bloque `DO`: las bases de prueba de la ingesta no aplican
   0049 y ese rol no existe ahí.
2. Escribe la reversa. Borra las dos tablas aunque tengan filas. El acta se
   vuelve a generar en la siguiente corrida.
3. En `app/ads/structure_api.py`, crea `listar_con_prueba` con el cuerpo de
   `listar_todo`. Toma el total de la primera página que lo declare y compáralo
   con el acumulado al final. Si la última página también lo declara, compara
   ese también, como hoy. Deja `listar_todo` con su firma, devolviendo
   `listar_con_prueba(...).items`.
4. Agrega a `EstructuraPerfil` los totales declarados de ad groups y de product
   ads, con default `None` y después de `product_ads`. Las pruebas construyen
   `EstructuraPerfil` por nombre y sin esos campos.
5. En `fetch_structure`, usa `listar_con_prueba` para `PATH_AD_GROUPS` y
   `PATH_PRODUCT_ADS`. Los campos `ad_groups` y `product_ads` siguen siendo
   listas planas.
6. En `app/ads/structure_plan.py`, agrega `huella_anuncios` y una función pura
   que arma el acta a partir de la estructura y de `refs`. Ponla junto a
   `_archivados_por_plataforma`, que ya relee el payload crudo. No cambies la
   firma de `_plan_items`. La regla por cada product ad del payload:
   - `state == "ARCHIVED"`: no cuenta.
   - Sin `adGroupId`: suma a `product_ads_sin_grupo` de su plataforma.
   - Su ad group no está en `refs`: no suma nada. Ese grupo queda sin fila.
   - Su `(platform, "product_ad", adId)` está en `refs`: es un anuncio vivo
     escrito y su `adId` entra a la huella de su grupo.
   - En otro caso: suma a `descartados` de su grupo.

   Mira `payload["state"]` para saber si está archivado. El motivo de skip de
   `_item_product_ad` no sirve: un archivado sin `asin` sale como "sin asin".
7. En `sync_structure` (`app/ads/structure.py:275`), inserta el acta dentro del
   `with conn.transaction()` que sella la corrida, después de marcar los
   archivados y antes de `_sellar_run`. Inserta primero `ads_listado_plataforma`
   y después `ads_listado_grupo`. Pon los INSERT en un helper. Escribe una fila
   por cada ad group de `refs`, también los que no tienen anuncios.
8. Amplía las tres bases de prueba que corren la ingesta para que apliquen la
   migración A: `tests/test_structure_sync.py` (constante junto a `_SQL17`),
   `tests/test_product_ads_vinculo.py` (`_conectar_base_con_0004`) y
   `tests/test_archiva_inertes.py` (`_db_21`).
9. Sube el conteo de tablas a 65 en `verify/Launch.md` y `verify/Doctor.md`,
   con los números de sus comentarios.

### Comprueba

Corre en local, con Postgres:

	uv run pytest tests/test_structure_sync.py tests/test_product_ads_vinculo.py tests/test_snapshot_listas.py tests/test_schema_docs.py tests/test_architecture.py tests/test_archiva_inertes.py tests/test_fabrica_campanas.py -q

Deben pasar todas y ninguna debe saltarse.

### Despliega

1. Copia el patrón de `docs/evidencia/jev-ads-01/ejecucion/2.3/` a
   `docs/evidencia/jev-ads-02/ejecucion/S.1/`. Cambia la lista de migraciones y
   de reversas, la tabla testigo del preflight, el prefijo del respaldo, los
   archivos del md5 y `permisos.sql`.
2. No reutilices `ensayo.sh` tal cual. Ese script aborta si producción ya
   tiene tablas de Jev, y hoy las tiene. El volcado de producción trae además
   permisos de `app_jev`: crea ese rol en la base desechable antes de cargarlo.
3. La guarda de "ningún `app.cli` corriendo" importa aquí más que en ningún
   otro paso: no recrees el contenedor mientras corre la ingesta de estructura
   de las 06:45 UTC.
4. Aplica la migración antes que el código. Si el código llega sin las tablas,
   la ingesta diaria falla, y a las 48 horas el motor empieza a saltar grupos
   por `MAX_EDAD_SYNC`.
5. La prueba posterior del checklist de S.1 es la corrida de estructura, además
   del ciclo. La última corrida de `amazon_ads_structure_v2` que empezó después
   del arranque del contenedor terminó con `ok` y dejó filas en
   `ads_listado_plataforma`. Sin corrida todavía: salida 3. Con una corrida
   fallida o sin acta: salida 1.

### Mide

Después de la primera corrida real, corre una consulta de solo lectura y
guarda la salida en la evidencia. Debe decir, por plataforma: si los totales
declarados igualan a los recibidos, cuántos anuncios no archivados se
descartaron, y cuántos grupos con gasto de búsquedas cumplen las
condiciones de roster del diseño. El día del diseño cumplían 22 en MX y 11 en
US en lo que se podía medir sin el acta.

## S.2: escribe el núcleo puro y traslada dos piezas del asesor

Al terminar existen `app/jev_lectura.py` y `app/jev_libro.py`, y el asesor de
JEV ADS 01 se comporta igual que antes. Este paso no depende de S.1.

### Qué vas a encontrar

- La guarda de pureza vive en `tests/test_jev_ads.py:358-453`. Usa una lista
  blanca de imports (`_PERMITIDOS_PUROS`) y prohíbe hasta una variable llamada
  `conn`.
- Ninguna prueba nombra los métodos privados del asesor. Puedes moverlos sin
  tocar pruebas.
- `_mismo_origen` (`app/jev_ads.py:431`) compara `_censo_crudo`, que incluye
  `synced_at`. La ingesta diaria reescribe ese campo. Por eso retomar una
  revisión al día siguiente falla con "misma solicitud con otro payload".

### Pruebas primero

1. `tests/test_jev_lectura.py`, puro y sin base:
   - `leer` como tabla, una fila por regla del diseño. Incluye: todos
     `no_satisface` con órdenes en el historial da `vendio_aqui`. Todos
     `no_satisface` con venta en otro grupo da `vende_en_otro`. Un `orders`
     NULL en otro grupo da `sin_lectura`. Una orden en un día y `orders` NULL
     en otro día del historial da `vendio_aqui`. `NoEvaluada` sin ventas da
     `sin_lectura` con motivo `jev_no_evaluada`.
   - `probar_roster`: cada motivo de `MotivoSinProbar` sale por separado, y
     el caso sin ninguno da `RosterProbado`. Una corrida de 48
     horas exactas todavía vale.
   - `anunciados_hoy`: quita al miembro con todos sus anuncios archivados.
     Conserva al miembro con un estado ausente. No cambia `exhaustivo`.
   - `ajustes_desde_settings`: `Apagado` con la clave ausente, con `"true"`
     como texto, con `1`, con tope negativo y con `jev.min_clics` ausente. Solo
     el JSON `true` enciende. Un `bool` no cuenta como entero. `jev.avisos`
     ausente o distinto de `true` deja `avisos` en `False` sin apagar el job.
   - `leer_destino`: una fila por cada valor de relevancia guardada.
   - `planear`: la misma entrada da el mismo plan. Ninguna clave se repite. Las
     propuestas van primero, por vencimiento.
2. Agrega `"jev_lectura.py"` al `parametrize` de
   `test_modulo_puro_sin_red_ni_db_en_top_level`.
3. Antes de mover `_enriquecer`, escribe una prueba de caracterización de la
   regla de fichas. Hoy nadie fija dos de sus comportamientos: un miembro que
   no acredita conserva el `ficha_version_id` que traía, y un miembro archivado
   también recibe ficha.
4. La prueba roja del arreglo de reanudación, en `tests/test_jev_cli.py`:
   evalúa con `presupuesto=1` y dos términos, mueve `synced_at` con el `UPDATE`
   que ya usa ese archivo, relee el censo y evalúa con la misma solicitud. Hoy
   da `ValueError`. Debe retomar y llamar solo al término pendiente. Agrega la
   variante pura en `tests/test_jev_ads.py`.

### Cambios

1. Crea `app/jev_lectura.py` con los tipos y las firmas de la sección A del
   bosquejo. Define ahí también `SenalVista` y `SenalPropuesta`: `jev_vista`
   las va a recibir y no puede importar un módulo con IO.
2. Agrega `"app.jev_lectura"` a `_PERMITIDOS_PUROS`.
3. Crea `app/jev_libro.py` con la clase `Libro` y solo su método `pagar` en
   este paso. Mueve ahí el bloque de `app/jev_asesor.py:160-166`: intención,
   commit, llamada y resultado. Tres cosas deben quedar iguales:
   - `Libro` recibe la misma conexión del asesor. El commit de la intención
     confirma también la fila de la revisión.
   - `FalloProveedor.producto_id` sale de `ficha.producto_id`.
   - Sin clave de API, `pagar` sigue escribiendo la intención y el resultado
     con error. Así se comporta hoy el CLI manual.

   El orden de `pares_de`, el contador de llamadas, `_exito_previo` y
   `_reutilizar` se quedan en el asesor. `_siguiente_ordinal` se va a `Libro`,
   que lo expone: `_reutilizar` lo sigue necesitando y lo llama desde ahí.
   No consultes la base en el `__init__` de `Libro`. `AsesorAds(conn)` se
   construye en cada GET de `/cortes`, y construye su `Libro` ahí mismo.
4. Mueve la regla de fichas de `_enriquecer` a `resolver_fichas` en
   `app/jev_catalogo.py`. Importa `replace` de `dataclasses`.
5. Arregla `_mismo_origen`: compara `_identidad_del_censo` y, aparte,
   `exhaustivo`. Deja después la cláusula de las fichas que el llamador ya
   traía: su `zip(strict=True)` falla si los largos difieren. Borra
   `_censo_crudo` y el import que queda sin uso.
6. Corrige los dos docstrings que quedan falsos: el de `app/jev_asesor.py`
   ("la UNICA capa con IO") y el de `app/jev_ads.py`.

### Comprueba

Corre en local, con Postgres:

	uv run pytest tests/test_jev_lectura.py tests/test_jev_ads.py tests/test_jev_cli.py tests/test_jev_catalogo.py tests/test_api_dashboard.py tests/test_api_fabrica.py -q

Las pruebas que ya existían pasan sin haberlas editado. Si una necesita
cambio, para y avisa. Las que fijan el traslado son
`test_revision_e_intencion_confirmadas_antes_del_http`, las cuatro de
reanudación de `tests/test_jev_cli.py` y
`test_asesoria_muestra_el_exito_de_la_reanudacion`.

### Despliega

Este paso no se despliega por separado. No cambia el esquema, y su código sale
con el despliegue de S.3.

## S.3: crea las tablas de señales, el login y el quinto DSN

Al terminar, producción tiene las tablas nuevas vacías, el login `orbit_jev` y
la variable `ORBIT_DSN_JEV`. El ciclo ya no puede leer tablas de Jev. Este paso
va después de S.2.

### Qué vas a encontrar

- El permiso por omisión de `migrations/0001_initial.sql:1521` da `SELECT` a
  `app_read`, `app_ingest`, `app_decide` y `app_admin` sobre toda tabla y toda
  vista nueva. Por eso hoy `app_decide` lee las cuatro tablas de 0049.
- `test_roles_de_minimo_privilegio` se salta si la conexión no es de
  superusuario.
- `tests/test_compose_deploy.py` exige que estén los cuatro DSN, pero no
  prohíbe un quinto.
- El script de logins de `docs/DEPLOY.md` empieza borrando todas las líneas
  `ORBIT_DSN_*` y rota las cuatro contraseñas.

### Pruebas primero

1. El perímetro, con un login real y no con `SET ROLE`. Usa el patrón de
   `tests/test_jev_cli.py:1163`: `CREATE ROLE ... LOGIN NOSUPERUSER`, darle el
   grupo y limpiar al final. Como `app_jev`, leer `decision`, `apply_queue` y
   `search_term_observation` falla. Como `app_decide` y como `app_ingest`, leer
   cualquier tabla `jev_*` y la vista falla. Esta prueba no se salta.
2. Los dos CHECK de `ajena` rechazan un INSERT directo con
   `roster_probado = false`, y otro con `ordenes_otros` NULL.
3. El CHECK de moneda rechaza `amazon_mx` con `USD`.
4. Una fila `lote` entra a `jev_revision`, y una intención y su resultado
   colgados de ella pasan el trigger `jev_par_evento_encadenado` sin tocarlo.
   Usa el ayudante `_revision` de `tests/test_jev_catalogo.py`.
5. Las cinco tablas nuevas rechazan `UPDATE`, `DELETE` y `TRUNCATE`.
6. La vista `jev_senal_vigente` marca no vigente una señal vencida y una señal
   cuyo roster cita una ficha revocada.
7. En `tests/test_compose_deploy.py`, convierte la tupla de DSN en un conjunto
   exacto de cinco. Debe fallar antes de tocar `docker-compose.yml`.

### Cambios

1. Escribe la migración B con el DDL del diseño. Detalles que el diseño da por
   entendidos:
   - El CHECK de `sujeto_tipo` es inline y PostgreSQL lo llama
     `jev_revision_sujeto_tipo_check`. Confírmalo en tu base con
     `SELECT conname FROM pg_constraint WHERE conrelid = 'jev_revision'::regclass`.
   - Al soltar `jev_revision_sujeto_coherente` se pierde su
     `COMMENT ON CONSTRAINT`. Vuelve a escribirlo.
   - Escribe el `REVOKE` después de cada `CREATE`, la vista incluida, y también
     sobre las cuatro tablas de 0049.
   - Agrega el índice parcial sobre `jev_par_evento (created_at) WHERE tipo =
     'intencion'`.
   - La columna de `jev_corrida` se llama `cierre`. Si le pones un CHECK,
     recuerda el texto prohibido de las reglas generales.
2. Escribe la reversa. Tiene tres obligaciones que `ensayo.sh` detecta al
   comparar el esquema: devolver `SELECT` a `app_decide` y `app_ingest` sobre
   las tablas de 0049, restaurar el `COMMENT ON CONSTRAINT` y recrear los dos
   CHECK con sus nombres. La reversa debe abortar si ya existe una fila `lote`.
3. Agrega la migración B a las listas escritas a mano que la van a necesitar:
   `ORDEN` en `tests/test_jev_catalogo.py` y `SQL_JEV` en
   `tests/test_api_dashboard.py`. Al sumarla a `ORDEN`, corrige en el mismo
   cambio `test_reversa_deja_la_base_como_0001`: aplica primero la reversa de
   B. Sin eso falla, porque la reversa de 0049 borra `jev_revision` sin
   `CASCADE` y las tablas nuevas la referencian. Agrega las cinco tablas nuevas
   a `TABLAS_JEV`.
4. Agrega `ORBIT_DSN_JEV` al bloque `environment` del servicio `app` en
   `docker-compose.yml`, y corrige el comentario que dice "SOLO los 4 DSN".
5. En `docs/DEPLOY.md`, documenta un bloque aparte para crear `orbit_jev` sin
   rotar las otras contraseñas. El bloque hace esto, en orden:
   1. Borra solo la línea `^ORBIT_DSN_JEV=` del `.env`.
   2. Crea o altera el rol `orbit_jev`.
   3. Ejecuta `GRANT app_jev TO orbit_jev`.
   4. Agrega la línea nueva al `.env`.
   5. Deja el `.env` en modo 600.

   Suma también `jev` al bucle del script completo, para instalaciones nuevas.
   Actualiza el chequeo de roles de la restauración: agrega `app_jev` y
   `orbit_jev` a su lista de nombres. Los valores que espera pasan de `N=9`,
   `ATTR=5` y `MEM=4` a `N=11`, `ATTR=6` y `MEM=5`. `ATTR` ya debía ser 6 desde
   0049, que creó `app_jev`. Corrige también el texto vecino, que dice
   "9 roles" y "4 usuarios": pasan a 11 y 5.
6. Sube el conteo de tablas a 70 en `verify/Launch.md` y `verify/Doctor.md`,
   con los números de sus comentarios.

### Comprueba

Corre en local, con Postgres y con el DSN de superusuario:

	uv run pytest tests/test_jev_catalogo.py tests/test_jev_cli.py tests/test_api_dashboard.py tests/test_compose_deploy.py tests/test_schema_docs.py tests/test_apply_schema.py -q

Agrega el archivo donde pusiste las pruebas nuevas, si es otro. Deben pasar
todas y ninguna debe saltarse: `test_roles_de_minimo_privilegio` se salta sin
superusuario.

### Despliega

1. Escribe los scripts en `docs/evidencia/jev-ads-02/ejecucion/S.3/`.
2. `desplegar.sh` crea el login y agrega la línea al `.env` antes del
   `docker compose up`, con el bloque nuevo de `docs/DEPLOY.md`. Nadie corre
   ese bloque a mano. El contenedor tiene que recrearse para ver la variable.
3. `desplegar.sh` de 2.3 no copia `docker-compose.yml`. S.3 sí necesita
   copiarlo, porque cambia el `environment` del servicio `app`.
4. En el checklist, comprueba los permisos de la prueba 1 con
   `has_table_privilege`. Comprueba que existen los CHECK de las pruebas 2 y 3
   con una consulta a `pg_constraint`. Comprueba que `/cortes` y `/salud`
   responden 200.
5. La reversa. `desplegar.sh` respalda `docker-compose.yml` en
   `predeploy-<sello>/` antes de copiarlo. El `.env` no se copia a ningún lado.
   `rollback.sh` restaura `docker-compose.yml` con el código, corre la reversa
   de B y deja el login `orbit_jev` y la línea `ORBIT_DSN_JEV`: sin las tablas
   no hacen nada. Su comprobación final es que `app_decide` vuelve a tener
   `SELECT` sobre las cuatro tablas de 0049.

Nota para los pasos siguientes: el job de CI nocturno `pesada` no aplica las
migraciones que tienen reversa hermana. Esa base no tendrá las tablas nuevas.
Las pantallas de S.5 deben degradar sin fallar cuando falten.

## S.4: construye el job `jev-senales`

Al terminar, `python -m app.cli jev-senales` existe, tiene su línea de cron y
su bloque en `/salud`. Con el interruptor ausente imprime que está apagado y
sale con 0. Este paso va después de S.1 y de S.3.

### Qué vas a encontrar

- `app/cli.py` tiene 878 líneas de 900, y su función `main` tiene 79
  sentencias de 80. El registro del comando y su despacho suman 3 sentencias:
  no caben.
- `app/optimizer/windows.py` tiene 874 de 900. No cabe una consulta nueva.
- `shell.js` pide `/api/dashboard/cortes` y `/api/dashboard/salud` en cada
  carga de cualquier pantalla. Lo que cuelgues ahí corre en cada vista.
- `decision_a_revisar` falla con `ValueError` si un harvest no trae destino
  legible. El job no debe caerse por eso.
- La cola tiene filas con `modo = 'shadow'`, que nunca se aplican.

### Pruebas primero

1. No se paga dos veces. Con un `pedir` que cuenta llamadas: la misma búsqueda
   y la misma ficha en dos grupos cuesta 1 llamada. Una segunda corrida cuesta
   0. Mover `synced_at` de todos los anuncios entre corridas cuesta 0. Agregar
   un producto al roster cuesta exactamente 1.
2. El tope aguanta una caída. El `pedir` falla en la llamada k. Corre otra
   vez. Las intenciones del día nunca pasan de `jev.tope_diario`.
3. Apagado es cero. Con `jev.senales` ausente, con `"true"` como texto o con
   tope negativo: ninguna tabla `jev_*` cambia y el transporte no se toca.
4. Sin clave de API el job no abre ninguna intención y sella las señales que
   las ventas ya deciden.
5. El universo de punta a punta. Un grupo con un producto solo archivado,
   roster probado y todos los activos en `no_satisface`: la señal es `ajena` y
   `miembros` es el número de activos.
6. La madurez. Una venta de la misma búsqueda en otro grupo, de hace 5 días,
   no cuenta como `vende_en_otro`. En el historial del propio grupo sí cuenta
   como `vendio_aqui`. El diseño explica la diferencia en "La regla".
7. Un harvest sin destino legible produce su señal de origen y marca
   `destino_ilegible`. El job termina.
8. Seco por omisión. Sin `--aplicar` el job imprime el plan y cuántas llamadas
   pagaría, y no escribe nada. El seco necesita solo `ORBIT_DSN_READ`.
9. Dos procesos a la vez. Otra conexión toma el candado y el job sale con 0
   sin escribir. El modelo es `test_advisory_ajeno_no_espera`.
10. El subquery compartido de `windows` da el mismo `terminos_cortes` que
    antes. Agrega la constante nueva a la lista de
    `test_sql_del_modulo_parsea_como_postgres` de
    `tests/test_optimizer_windows.py`. Formatea su marcador antes de parsear.
11. La guarda de imports, en `tests/test_architecture.py`. Recorre todo `app/`
    y `tools/`. Solo pueden importar un módulo `jev_*` los propios módulos de
    Jev, `app/cli.py`, `app/api_dashboard.py`, `app/fabrica_web.py`,
    `tools/jev_ads.py` y `tools/jev_fichas.py`. Antes de fijar la lista, busca
    con `grep` quién importa Jev hoy y agrega al que falte, con su razón.
    Agrega una fuga sembrada que ponga la guarda en rojo. Borra la prueba vieja
    de `tests/test_jev_cli.py`.
12. El despacho del CLI. Parchea `app.jev_senales.main` y comprueba que recibe
    los argumentos restantes y que su código de salida se propaga. El modelo
    está en `tests/test_cli.py:248`.
13. La línea de cron. Una constante en la prueba, y la afirmación de que
    `docs/DEPLOY.md` la contiene. Comprueba además que el filtro del instalador
    de crons no la borra. El modelo es `tests/test_ads_salud_cron.py`.

### Cambios

1. En `app/optimizer/windows.py`, saca la subconsulta de colapso de
   `_SQL_TERMINOS_CORTES` a una constante compartida con un marcador para su
   `WHERE`. El historial no lleva filtro de fechas. El texto final de
   `_SQL_TERMINOS_CORTES` queda igual salvo espacios. No muevas el `BETWEEN` a
   la consulta de afuera: cambia el plan de ejecución del motor. El archivo
   termina en menos de 880 líneas. Actualiza el docstring que enumera sus SQL.
2. Completa `Libro` con `juicio` y `llamadas_de_hoy`, según el bosquejo. El
   conteo del día filtra por rango: `created_at` desde el inicio del día UTC y
   antes del inicio del día siguiente. Una igualdad sobre
   `(created_at AT TIME ZONE 'UTC')::date` no usa el índice parcial de S.3. La
   reserva va dentro de `with escritor.transaction():`, con
   `pg_advisory_xact_lock(hashtext('jev:cupo'))` como primera sentencia. Un
   candado de transacción tomado fuera de ese bloque se libera al instante en
   una conexión autocommit.
3. Crea `app/jev_senales.py` con `correr` y `main`.
   - Abre `lector` con `ORBIT_DSN_READ` y `escritor` con `ORBIT_DSN_JEV`, las
     dos con `app.db.connect`. Esa función cambia el host del DSN por el del
     contenedor. Con `psycopg.connect` directo el job no conecta en
     producción. El escritor va en autocommit.
   - Toma `pg_try_advisory_lock(hashtext('jev:senales'))` dentro del `try` y
     suéltalo en el `finally`. El modelo es `app/precio/corrida.py:138-159`.
   - Lee todo con el lector dentro de una sola transacción `REPEATABLE READ`.
     El modelo es `app/cycle.py:2669`.
   - Lee la cola con una consulta propia: `id`, `decision_id`, `kind`, `modo`,
     `vence_el`, `ad_entity_id`, `search_term` y `decision.inputs`, con `kind`
     en `negative` o `harvest` y `estado` en `pending_veto` o `released`.
     Resuelve el destino de un harvest desde `inputs.goal.harvest.ad_group_id`,
     que es el id externo. Si falta o no existe, marca `destino_ilegible`.
   - Lee aparte los cortes que Orbit ya aplicó: las filas de `apply_queue` con
     `estado = 'applied'`, por ad group y búsqueda. `planear` los recibe como
     `cortes_aplicados` y los deja fuera de las candidatas.
   - Saca los ad groups con observaciones de términos con una consulta propia
     sobre `search_term_observation` por plataforma. No importes `app.cycle`.
   - Llama a `windows.terminos_cortes` por ad group para la ventana madura.
     Arma el historial con tu propio agregado sobre la subconsulta compartida:
     la suma de órdenes de los días con dato y el número de días sin dato.
   - Toma la moneda de `PLATAFORMAS_MONEDA` (`app/optimizer/bid.py`). No
     escribas un diccionario con `"MXN"` y `"USD"`: rompe
     `test_una_sola_fuente_de_moneda_por_plataforma`.
   - Arma el roster en este orden: `censo_grupo`, `resolver_fichas`,
     `anunciados_hoy`, `probar_roster`. Si la prueba es `RosterProbado`, pon
     `exhaustivo=True` en el censo con `dataclasses.replace`. Ningún otro
     código lo pone.
   - Para `probar_roster` necesitas dos lecturas que `censo_grupo` no da. La
     primera es el acta: la última corrida ok de `amazon_ads_structure_v2` que
     tenga fila en `ads_listado_plataforma` para esa plataforma, con su fila de
     `ads_listado_grupo` para ese ad group. La segunda es la huella en base:
     los `external_id` de los product ads del grupo con estado ENABLED o
     PAUSED, pasados por `huella_anuncios` de `app.ads.structure_plan`.
   - `Roster.sha256` es el sha256 del JSON canónico de `_identidad_del_censo`
     más la ficha de cada miembro. No incluye `synced_at` ni la prueba. Ese
     mismo JSON es lo que guarda `jev_roster.miembros`.
   - Las conexiones del dashboard usan `dict_row`. Las funciones del catálogo
     desempacan por posición. Usa un cursor con `tuple_row` donde haga falta.
   - Calcula `satisfacen`, `evaluados`, `miembros` y `productos_ok` desde los
     pares.
   - Deja fuera el aviso de Telegram. Entra en S.8.
   - Imprime una línea de resumen con `print`. En `app/` no hay
     `logging.basicConfig`, y solo los `logging.warning` llegan al log del cron.
   - `correr` va a llegar al límite de complejidad. Pártela por lo que cada
     parte sabe: leer el mundo, armar el roster, sellar una unidad.
4. En `app/cli.py`, saca antes la construcción del parser de `main` a una
   función aparte, en un commit que no cambie comportamiento. Después registra
   el comando junto al de `ads-salud` y despáchalo antes del
   `return _ingest(args, rest)` final. Importa
   `app.jev_senales` dentro del `if`, no arriba: `python -m app.cli cycle` no
   debe cargar Jev.
5. En `app/api_dashboard.py`, agrega el bloque de salud como clave hermana de
   `plataformas`: `{"plataformas": ..., "jev": _jev_de(conn)}`. No lo metas
   dentro de `plataformas`: `shell.js` recorre ese diccionario y pintaría el
   bloque como si fuera un mercado. `_jev_de` sigue el patrón de `_spapi_de`,
   con import tardío dentro del `try`, y devuelve `None` si algo falla. Dale a
   `SaludJev` un `como_dict()` de datos planos. Mantén sus consultas baratas.
6. En `app/ui.py`, pasa `datos.get("jev")` a la plantilla. Usa `.get`: hay
   pruebas que sustituyen `dash.salud` por una función que no trae esa clave.
7. En `app/templates/salud.html`, pinta el bloque fuera del `for` de
   plataformas, en una tarjeta propia bajo `{% if jev %}`.
8. Documenta la línea de cron en `docs/DEPLOY.md` como línea suelta, con sus
   prerrequisitos, su instalación y su reversa. Usa `30 9,21 * * *`, con
   `flock -n` y log propio.

### Comprueba

Corre los archivos de prueba que tocaste y además:

	uv run pytest tests/test_optimizer_windows.py tests/test_optimizer_hygiene.py tests/test_cycle.py tests/test_precio_pantalla.py tests/test_spapi_salud.py tests/test_ui.py tests/test_cli.py -q

### Despliega

Este paso no trae migración. Sus scripts van en
`docs/evidencia/jev-ads-02/ejecucion/S.4/`.

1. `desplegar.sh` copia el código y recrea el contenedor. `rollback.sh` quita
   primero la línea de cron y después restaura el respaldo del código.
2. Con el contenedor arriba y antes del checklist, corre
   `python -m app.cli jev-senales` dentro del contenedor. Debe decir que el
   job está apagado y salir con 0. Si no, no instales nada. Después respalda
   el crontab de `gon`, instala la línea como dice `docs/DEPLOY.md` y
   comprueba que solo se agregó esa línea.
3. El checklist comprueba:
   - `/health`, `/cortes` y `/salud` responden 200.
   - `python -m app.cli jev-senales`, dentro del contenedor y sin `--aplicar`,
     dice que el job está apagado y sale con 0.
   - El bloque `jev` de `/api/dashboard/salud` dice apagado.
   - Las cinco tablas de S.3 tienen cero filas.
   - Importar `app.cycle`, `app.apply_cola` y `app.apply_harvest` no carga
     ningún módulo `app.jev_*`. El checklist de 2.3 ya trae esa comprobación.
   - El crontab de `gon` tiene una sola línea del job.
4. Lee el log de la primera corrida por cron. Debe decir que el job está
   apagado.

## S.5: muestra la señal en `/cortes` y crea `/gasto-sin-venta`

Al terminar, las dos pantallas existen y salen vacías, porque el job sigue
apagado.

### Qué vas a encontrar

- Dos pruebas exigen que la palabra "error" no aparezca en el HTML de
  `/cortes`.
- La barra móvil está fijada en 7 pestañas.
- Los contextos de prueba de `/cortes` no traen las claves nuevas.

### Pruebas primero

1. GET puro. Las dos pantallas con un transporte que falla si alguien lo usa,
   y con el conteo de filas de todas las tablas `jev_*` igual antes y después.
   Amplía `_conteos_jev` de `tests/test_api_dashboard.py` con las cinco tablas
   nuevas.
2. La fila es la verdad. Para toda fila de `jev_senal` sembrada, `leer` con
   sus insumos y su `regla_version` da la `lectura` guardada.
3. Vigencia viva. Tras revocar una ficha citada, la señal sale
   "desactualizada".
4. La lectura falla y la pantalla sigue. Parchea la función de lectura para
   que falle: `/cortes` responde 200 con los mismos items y el aviso "Señal no
   disponible". El modelo es
   `test_cortes_asesoria_ilegible_se_avisa_y_la_pantalla_sigue`.
5. Nunca "ninguno" sin roster probado, ni en la API ni en el HTML.
6. La pantalla nueva: una prueba de forma y totales en la API, una de escape
   de texto en el HTML y una de la ruta. Los modelos son las tres pruebas de
   `/inertes`.
7. Un harvest pinta dos bloques en `/cortes`, origen y destino. Un destino
   ilegible se pinta como "sin lectura".
8. `banda_de_proporcion`, como prueba pura: 0 de n es `ninguno`, hasta 15% es
   `pocos`, 95% o más es `todos`, lo demás es `una_parte`, y sin evaluados es
   `sin_dato`.

### Cambios

1. En `app/jev_senales.py`, escribe las dos lecturas de pantalla del
   bosquejo: `de_propuestas` y `gasto_sin_venta`, con `PantallaGasto`. Solo
   hacen `SELECT` sobre `jev_senal_vigente` y las tablas de Jev. Dale
   `como_dict()` a `SenalVista` y a `SenalPropuesta`.
2. En `cortes()` (`app/api_dashboard.py:1339`), agrega el bloque de señal
   justo después del de asesoría y con su misma forma: bandera en `True`,
   `try`, import tardío, lectura por `decision_id`, y en el `except` un
   `warning` al log, un diccionario vacío y la bandera en `False`. Cada item gana
   `senal`, y la respuesta gana `senal_disponible`. En `pagina_cortes`
   (`app/ui.py`), pasa esa bandera a la plantilla con `.get`.
3. En `app/templates/cortes.html`, pinta la fila "Señal" antes de la fila de
   asesoría. Usa `{% if item.senal %}` y `is defined`, como hace la plantilla
   con `asesoria_disponible`.
4. Agrega la pantalla nueva siguiendo `/inertes` de punta a punta: una función
   delgada en `api_dashboard.py` que delega a `jev_senales.gasto_sin_venta`,
   la ruta en `ui.py`, la plantilla, y los tres sitios de `base.html`
   (`paginas`, la lista de navegación y `tab_por_pantalla`). Ponla en el grupo
   "Decidir" de la barra lateral. No agregues pestaña a la barra móvil.
5. Cambia de mercado con `?plataforma=` y `_vocab_o_422`. No existe componente
   de pestañas.
6. Muestra los productos que dijeron "sí" dentro de un `<details><summary>`
   plegado. No escribas JavaScript nuevo ni atributos `on*=`: una prueba recorre todas las
   plantillas y los prohíbe.
7. Pon las bandas de proporción en `jev_vista`, como función pura. No las
   guardes.

### Comprueba

Corre en local, con Postgres:

	uv run pytest tests/test_api_dashboard.py tests/test_ui.py tests/test_ui_tema.py tests/test_jev_ads.py tests/test_jev_lectura.py tests/test_architecture.py -q

Deben pasar todas y ninguna debe saltarse.

### Despliega

Este paso tampoco trae migración. Sus scripts van en
`docs/evidencia/jev-ads-02/ejecucion/S.5/`, con la misma forma que los de S.4.
El checklist comprueba:

- `/cortes`, `/gasto-sin-venta` y `/salud` responden 200.
- `/api/dashboard/cortes` trae `senal_disponible` en `true` y `senal` en `null`
  en todos sus items, porque el job sigue apagado.
- La pantalla nueva sale sin filas en las dos plataformas.

## S.6: enciende con tope 0

Este paso no tiene código. Lo ejecuta el dueño o quien él indique.

1. Comprueba que S.5 está desplegado y que el bloque de `/salud` dice apagado.
2. Con el go del dueño, inserta una versión de config que agregue
   `jev.senales = true`, `jev.tope_diario = 0` y `jev.min_clics = 3`. Usa la
   receta de `docs/DEPLOY.md` para sembrar claves: un `INSERT INTO
   config_version` que copia la última fila y le suma las claves, con el
   literal del go en el `label`. Corre antes el `SELECT` de ensayo.
3. Espera una semana. Las señales salen solo con ventas: `vendio_aqui`,
   `vende_en_otro` y `sin_lectura`.
4. Comprueba con una consulta de solo lectura que el ciclo y la cola no
   cambiaron: mismos conteos de decisiones, cola y ledger por día que la
   semana anterior, dentro de lo que explica el tráfico.

Para apagar, inserta otra versión sin la clave `jev.senales`.

## S.7: sube el tope y mide con el dueño

1. Cierra antes las tareas 0.3 (tarifa) y 0.5 (orden de las opciones).
2. Con el go del dueño, sube `jev.tope_diario` a 5,000. La primera pasada
   tarda 4 días.
3. Arma la muestra: al menos 50 búsquedas distintas por mercado, que no sean
   las que se usaron para corregir fichas.
4. El dueño etiqueta cada una sin ver la señal: si es ajena, y si bloquearla
   sería sano.
5. Reporta por mercado y por búsqueda distinta: el acuerdo con cada lectura,
   los falsos `ajena`, los falsos "sí" causados por una ficha, cuántas veces
   una abstención impidió `ajena` y cuántas de las señaladas vendieron después.
6. Corrige las fichas que el reporte señale, registrando versiones nuevas.

## S.8: avisa por Telegram

Constrúyelo solo si S.7 cumple su criterio: el dueño coincide con la lectura
en 90% o más de al menos 50 búsquedas.

### Pruebas primero

1. `notifica.envia_aviso` devuelve `False` con el canal inactivo y no hace
   ninguna llamada. Es lo contrario de los demás envíos de `notifica`, que
   devuelven `True` en ese caso.
2. El aviso es honesto. Telegram falla: hay fila en `jev_aviso` y no hay fila
   en `jev_aviso_entrega`. La corrida siguiente lo envía una sola vez, con el
   mismo texto guardado.
3. Dos corridas el mismo día producen un solo aviso por propuesta y lectura.
4. `texto_aviso`, como prueba pura por lectura: lleva la fecha y la hora UTC
   en que se aplica, y no "faltan n horas". Para una propuesta en `shadow`
   dice que no se aplica. Nunca dice "ninguno" sin roster probado.

### Cambios

1. En `app/notifica.py`, agrega `envia_aviso(texto, *, transport=None) ->
   bool` junto a los demás envíos. `notifica` no importa nada de Jev.
2. En `app/jev_vista.py`, agrega `texto_aviso`. Recibe los tipos de
   `jev_lectura`.
3. En `jev_senales.correr`, agrega el paso del aviso: inserta `jev_aviso`
   antes de enviar y `jev_aviso_entrega` solo si el envío devolvió `True`.
   `main` inyecta `notifica.envia_aviso`.
4. Usa `Ajustes.avisos`, que existe desde S.2: sin `jev.avisos` en `true`,
   `correr` no envía ni inserta avisos.
5. Agrega "avisos sin entrega" al bloque de `/salud`.

### Comprueba

Corre los archivos de prueba que tocaste y además:

	uv run pytest tests/test_notifica.py tests/test_jev_ads.py tests/test_jev_lectura.py tests/test_api_dashboard.py tests/test_architecture.py -q

### Despliega

Este paso no trae migración: las tablas de avisos existen desde S.3. Sus
scripts van en `docs/evidencia/jev-ads-02/ejecucion/S.8/`, con la misma forma
que los de S.4. El checklist comprueba que, con `jev.avisos` ausente,
`jev_aviso` y `jev_aviso_entrega` siguen con cero filas después de una corrida
del job.

Encender `jev.avisos` es otro cambio de config, con su go.

## Preguntas que siguen abiertas

Esta guía ya resolvió las preguntas que quedaron abiertas al leer el código.
Estas tres siguen abiertas. No las resuelvas tú: las decide el lead con el
dueño.

- Si el CLI manual debe pasar también a la reutilización global. Hoy conserva
  su regla.
- Si el aviso debe llegar también por las búsquedas `ajena` que no son
  propuestas del motor. El diseño avisa solo propuestas.
- Los valores de 36 horas de vigencia y 5 fallos seguidos. Son del autor del
  diseño, sin medición detrás.
