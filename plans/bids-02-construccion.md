# Cómo construir BIDS 02

Esta guía es para Muse, que implementa cada fila de [BIDS 02](bids-02.md). El lead revisa cada PR y claw hace el merge.
Al dueño no se le pregunta nada: el plan aprobado es su autorización completa y de cada evento solo recibe un aviso.

Si esta guía y el plan `bids-02.md` difieren, sigue el plan y avisa al lead. La precedencia completa es
`docs/CONTEXTO.md`, después el diseño, después el plan y al final esta guía. Los números de línea son de
`origin/master` en el commit `45b7de3`. Si un archivo ya cambió, busca el símbolo por nombre.

Cada paso es una fila del plan y tiene cuatro partes: qué vas a encontrar, pruebas primero, cambios y comprueba.
"Comprueba" revisa cada cláusula del DoD de su fila. La guía no repite el diseño. Nombra la sección de `design.md`,
`tabla-decision.md`, `bosquejo.py`, `datos.sql` o `borrado.md` que tienes que leer.

Los pasos están agrupados en las siete secciones de entrega del plan. Recibes una sección completa y la construyes
completa. El lead la revisa una vez y después se despliega. Cada sección abre con cinco bloques: qué entrega, sus
tareas en orden, su rama y sus PR, sus archivos y cómo se comprueba completa.

## Antes de empezar

1. Para 0.a, lee los documentos en `~/.claude/orchestrate/motor-poca-data/docs/`. Desde 0.b, léelos en el repo.
2. Lee en este orden: `docs/CONTEXTO.md`, el diseño completo, `tabla-decision.md`, el bloque de tu paso en
   `bosquejo.py`, `borrado.md` y la fila de tu tarea en el plan. Antes del primer paso de una sección, lee los cinco
   bloques que la abren.
3. Prepara un árbol de trabajo por sección. El bloque "Rama y PR" de cada sección da el nombre de su rama. No cambies
   de rama en `~/dev/goncloud-Orbit`: otro agente puede tener ahí trabajo sin commit.

		git -C ~/dev/goncloud-Orbit fetch origin
		git -C ~/dev/goncloud-Orbit worktree add ~/dev/wt-bids-02-s<N> -b bids-02/s<N>-<nombre> origin/master
		cd ~/dev/wt-bids-02-s<N> && uv sync

4. Levanta un Postgres 16 local con el paso 1 de `verify/Launch.md`, que lo deja en el puerto 5433. Exporta
   `ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres`. Los comandos de esta guía lo dan por exportado.
5. Corre las pruebas de tu paso con `uv run --frozen python -m pytest -q -rs <archivos>`. Cada paso lista sus
   archivos en "Comprueba". El resultado esperado es siempre `passed`. Revisa en la salida de `-rs` que ningún
   salto diga "sin Postgres utilizable". Con ese salto la corrida sale verde sin probar nada.
6. Antes de cada commit corre `ruff check --fix . && ruff format .` y después `pre-commit run --all-files`. Si
   `pre-commit` no encuentra `python`, corre antes `source .venv/bin/activate`.
7. Corre las herramientas de `tools/` con `PYTHONPATH=.` delante. Sin él fallan con `No module named 'app'`.

## Reglas que valen en todos los pasos

1. **Un PR de código por sección.** Usa una rama y un árbol de trabajo por sección. Haz un commit por tarea, con el
   mensaje `BIDS 02 <id>: <qué hace>`. P.1, P.2a, P.4, P.5 y P.6 llevan dos commits cada una: primero el contrato y
   después la pantalla. Un arreglo posterior va en un commit nuevo con el id de su tarea. La sección 1 es la
   excepción: 0.a es un PR de documentos aparte y se mergea primero, porque todas las demás ramas parten de él. Cada
   sección termina con un PR de cierre corto, por el carril `fast`, en la rama `bids-02/s<N>-cierre`. El PR de cierre
   trae la evidencia de producción de la sección, marca sus filas en `plans/bids-02.md` con `cc:完了` y agrega una
   entrada al inicio de `docs/CHAT-CONTEXT.md`. Sin esa entrada falla `tools/check_chat_context_fresh.py`. Una fila
   que no se hizo lleva su motivo en vez del marcador.
2. **Prueba primero.** Escribe la prueba, mírala fallar por la razón correcta y después escribe el código.
3. **Un mutante por prueba nueva.** Registra en `mutantes.md` el cambio de una línea que pone la prueba en rojo. Los
   obligatorios están en "Pruebas que deben discriminar" del plan. Cada paso nombra los suyos.
4. **Evidencia.** Guarda comandos, salidas, mutantes y el SHA en `docs/evidencia/bids-02/ejecucion/<id>/`. Los scripts
   que esta guía nombra como `ejecucion/<id>/<x>` viven ahí. Antes de guardar una salida, cambia el valor de `$HOME`
   por `~`.
5. **Lo que CI ve en un PR.** En un PR solo corre el job `rapido`: pre-commit y el paso de guardas, sin Postgres
   (`.github/workflows/quality.yml:75-78`). Desde 0.b ese paso corre también `tests/test_arq_bids_*.py`. Las pruebas
   con base corren en el push a `master`. Corre en local las de cada paso. Para correr la batería completa sobre el
   commit final del PR de una sección, lanza el workflow a mano con `gh workflow run quality.yml --ref <rama>` y
   espera con `gh run watch`. Esa corrida incluye el job `completa`, que corre `pytest -q` con Postgres
   (`quality.yml:83` y `:137`). Esperado: `completa` termina en `success`.
6. **Una migración por tarea.** BIDS 02 usa de la 0060 a la 0067, como dice la tabla de "Convivencia con REPRICING
   02" del plan. REPRICING 02 reserva de la 0053 a la 0058 y la 0059 queda libre. Antes de abrir el PR corre
   `git ls-tree --name-only origin/master migrations/`. Si tu número ya existe ahí, toma el siguiente libre, cambia
   el nombre del archivo y de su reversa, y avisa al lead. No edites una migración mergeada. Si tu tarea cambia un
   objeto de otra migración, escribe el cambio en la tuya.
7. **Procedimiento sellado de una migración.** Nombra el archivo `migrations/NNNN_bids02_<nombre>.sql` y su reversa
   `migrations/NNNN_reversa_bids02_<nombre>.sql`. Escribe `BEGIN` y `COMMIT` dentro del archivo. No la hagas
   idempotente. Pon a cada tabla nueva sus dos triggers `prohibir_mutacion()`, con
   `migrations/0040_ads_report_result.sql` como modelo. Declara cada permiso con un `GRANT` y cierra con un bloque
   `DO` que falle si falta o sobra un privilegio. No escribas los textos `motivo IN (` ni
   `CREATE VIEW v_decision_huerfana`: `tests/test_apply_schema.py` toma la última migración que los contenga. El
   texto `procedencia IN (` solo va en la 0063. Escribe `ensayo.sh` y `permisos.sql` a partir de los de
   `docs/evidencia/jev-ads-02/ejecucion/S.3/`. El ensayo aplica la migración sobre el esquema real, aplica la
   reversa y exige el esquema idéntico al de producción. Córrelo cuando claw te lo encarga, antes de pedir el merge.
   En producción la aplica solo `desplegar.sh` de una fila D.
8. **Conteo de tablas.** `tests/test_schema_docs.py` exige que `verify/Launch.md:42` y `verify/Doctor.md:23` digan el
   número real de tablas base. Hoy es 70. Si tu migración crea una tabla, sube ese número. Si crea una vista, sube
   el conteo de vistas de los comentarios (`verify/Launch.md:38` y `verify/Doctor.md:25-26`). Al rebasar, recalcula
   los dos números: otra tarea y REPRICING 02 también los suben.
9. **El código nunca corre sin sus tablas.** Cada despliegue aplica todas las migraciones de BIDS 02 que trae su
   commit, como dice "Cómo se despliega una sección". No escribas código que tolere una tabla o una columna ausente.
10. **Producción.** En producción solo escriben los scripts de un PR aprobado y mergeado. Los scripts que leen
    producción usan `ORBIT_DSN_READ`. Corre unos y otros solo cuando claw te lo encarga, y guarda cada salida con su
    código. Ningún script imprime una contraseña, un token ni un DSN.
11. **Copia de producción y consulta de control.** Un DoD que compara contra producción no usa los números del
    2026-10-09. Compara contra la consulta de control del paso, corrida sobre la misma copia. La copia la arma
    `ejecucion/0.b/copia.sh`. Cada paso que la usa le agrega sus tablas con `-t`. Si la lectura y la consulta de
    control difieren, no cambies una de las dos para cuadrar. Busca la fila que difiere y avisa al lead.
12. **Candados.** Si un candado de pre-commit o de pre-push falla, arregla la causa. `--no-verify` está prohibido.
13. **Tamaño.** Ningún módulo de `app/` pasa de 900 líneas. Ninguna función pasa de complejidad 22, 25 ramas ni
    80 sentencias. No uses `noqa` para pasar un límite. `app/cli.py` tiene 891 líneas: un comando nuevo vive en
    `app/cli_bids.py` y `app/cli.py` solo gana su despacho.
14. **Candados de arquitectura.** Escribe los candados nuevos en `tests/test_arq_bids_<carril>.py`. `<carril>` es la
    letra del id de la tarea, en minúscula. Esa letra nombra el tema de la tarea, no su sección. Solo M.3 edita
    `tests/test_architecture.py`. Cada candado trae una prueba con una fuga sembrada. Un candado corre sin Postgres y
    con los paquetes que instala el paso de guardas (`.github/workflows/quality.yml:77`).
15. **Cliente de escritura.** Ningún módulo nuevo construye `AdsWriteClient`. Hoy lo construyen `app/apply.py`
    (`:1304` y `:1609`) y `app/ads/archivar.py` (`:126`), y la lista `PERMITIDOS_IMPORTAR_ADS_WRITE`
    (`tests/test_architecture.py:290`) no crece. El regreso del dueño y los ajustes de campaña escriben por
    `app/apply.py`. El retiro de anuncios escribe por `app/ads/archivar.py`.
16. **Repo público y sin red.** Ningún volcado de producción entra al repo. Un fixture lleva números e ids internos,
    nunca el texto de una keyword, un SKU ni el nombre de un producto. Las pruebas usan clientes falsos.
17. **Archivos compartidos.** Rebasa sobre `origin/master` antes de abrir un PR que toque un archivo de la tabla. La
    segunda sección en mergear rebasa y resuelve.
18. **Verificación una vez por sección.** Mientras construyes, corre solo las pruebas focalizadas de cada tarea. El
    lead hace una ronda de revisión sobre el PR completo de la sección. La batería completa corre una vez, en CI,
    sobre el commit final de ese PR, como dice la regla 5. Los hallazgos se agrupan y los corriges en una sola ronda.
    Solo un hallazgo bloqueante, con el comando que lo reproduce, abre otra ronda. El texto de estas reglas está en
    "Calidad (quality-kit)" de `AGENTS.md`, en `origin/master`. El bloque del que hablan esas reglas es la sección.
19. **Mergear, desplegar y rebasar.** El despliegue de una sección corre en cuanto claw mergea su PR de código. Un
    despliegue lleva todo lo mergeado. Por eso ninguna sección se mergea mientras otra está mergeada y sin desplegar.
    Después de cada merge, rebasa tu rama sobre `origin/master` si tu sección sigue abierta.
20. **"Avisa al lead".** Significa escribir el aviso en dos sitios: en la descripción del PR, bajo el encabezado
    `Desviaciones`, y en la línea de entrega de la sección. El canal entre claw, el lead y Muse es del loop de claw,
    no de estos documentos.

## Quién edita cada archivo compartido

Esta tabla lista los archivos que toca más de una sección. Las secciones 2, 3, 5 y 6 se construyen al mismo tiempo: la
segunda en mergear rebasa sobre `origin/master` y resuelve. Dentro de una sección, el orden es el de sus tareas. Los
archivos que toca una sola sección están en el bloque "Archivos de la sección" de esa sección.

| Archivo | Qué agrega cada sección |
| --- | --- |
| `app/optimizer/goals.py` y `tests/test_optimizer_goals.py` | Sección 1: 0.b agrega `gasto_para_concluir_desde_settings`. Sección 2: T.1 cambia el paso y quita los dos peldaños. M.3 agrega `politica_bid_desde_settings` y borra D.2 y `motor_evidencia_desde_settings` |
| `app/cycle.py` | Sección 2: T.1 salta la hoja y el ad group sin target y guarda `paso_politica`. M.3 cablea `lecturas_caso` y `decide` y borra los contrafactuales. Sección 5: I.4 filtra el estado en `_SQL_PAREJAS_CAMPANA_FAMILIA` |
| `app/apply.py` | Sección 2: M.3 cambia `orden_bids` por `prioridad_bajo_cupo`. M.5 agrega el regreso del dueño. Sección 4: V.3 agrega `aplica_ajuste_campana` y `regresa_ajuste_campana` |
| `app/api_write.py`, `tests/test_api.py` y `tests/test_api_write.py` | Sección 2: M.3 cambia el vocabulario de `motor_bid` en `CuerpoSettings`. M.5 agrega sus dos rutas y las suma a `SUPERFICIE_ADS_OPTIMIZER`. Sección 4: V.3 agrega sus tres rutas y las suma a la misma lista |
| `app/api_dashboard.py`, `tests/test_api_dashboard.py` y `tests/test_ui.py` | Sección 2: T.1 quita las etiquetas de los dos peldaños y trata `/settings` sin target. M.3 cambia `MOTIVOS_ES_*` y `motor_bid`. P.3b y P.5 agregan la ruta delgada de su pantalla. Sección 3: P.1 agrega la ruta delgada de su pantalla, P.2a extiende las dos pruebas con sus tablas y V.4 agrega su bloque a `salud`. Sección 4: P.2b extiende las dos pruebas con sus botones. Sección 5: P.4 agrega la ruta delgada de su pantalla. Sección 6: P.6 agrega la ruta delgada de su pantalla. REPRICING 02 también edita `app/api_dashboard.py` |
| `app/ui.py` y `app/templates/base.html` | Cada pantalla agrega su ruta y sus tres entradas de menú. Sección 2: P.3b y P.5. Sección 3: P.1. Sección 5: P.4. Sección 6: P.6 |
| `app/pantalla_dinero.py` | Sección 3: P.1 lo crea con la tabla por tipo. P.2a agrega `FilaUbicacion`, `FilaCampana` y sus lecturas. V.4 llena `FilaCampana.avisos`. Sección 4: P.2b agrega `ajustes_regresables` |
| `app/ads/campana_config.py` | Sección 3: V.1 lo crea. Sección 4: V.3 agrega el cuerpo del PUT y la lista de llaves |
| `app/ads/write.py` y `tests/test_ads_write.py` | Sección 4: V.3 agrega `PUT /sp/campaigns`. Sección 5: I.4 agrega `PUT /sp/productAds` si V.0 selló la pausa. Cada entrada va con la evidencia de V.0 en el mismo PR |
| `app/cli.py`, `app/cli_bids.py` y `tests/test_cli.py` | Sección 3: V.4 registra `avisos-campana`. Sección 5: I.3 registra `impulso-vigia`. Cada una crea `app/cli_bids.py` y el despacho en `app/cli.py` si no están en su rama. La segunda en mergear deja un solo despacho y agrega su comando. V.2 no los toca |
| `tests/test_arq_bids_*.py` | 0.b crea `_ci` en la sección 1. M.1 crea `_m` en la sección 2. V.1 crea `_v` en la sección 3 y V.3 lo extiende en la sección 4. I.2 crea `_i` en la sección 5. `_p` lo crea la primera tarea P de cada rama si no está: P.3a en la sección 2, P.1 en la sección 3, P.4 en la sección 5 y P.6 en la sección 6. La segunda sección en mergear une los archivos `_p`. Las demás tareas agregan al archivo de su letra |
| `verify/Launch.md` y `verify/Doctor.md` | Sección 1: 0.b sube 2 vistas. Sección 3: V.1 sube 1 tabla y 1 vista, y V.2 sube 1 tabla. Sección 4: V.3 sube 1 tabla. Sección 5: I.3 e I.4 suben 2 tablas y 1 vista cada una. Al rebasar, recalcula los dos números |
| `docs/DEPLOY.md` | Sección 2: D.1 agrega su sección. Sección 3: V.2 y V.4 agregan su línea de cron con su prueba, y D.2 agrega su sección. Sección 4: D.3 agrega su sección. Sección 5: I.3 agrega su línea de cron con su prueba, y D.4 agrega su sección. Sección 6: D.5 agrega su sección. Sección 7: C.1 cierra |
| `docs/evidencia/bids-02/ejecucion/0.b/copia.sh` | Sección 1: 0.b lo crea. Secciones 2, 3, 5 y 6: cada paso que usa la copia le agrega sus tablas con `-t` |
| `plans/bids-02.md` y `docs/CHAT-CONTEXT.md` | El PR de cierre de cada sección marca sus filas y agrega su entrada |
| `migrations/` | Una migración por tarea, sin choque entre secciones: 0060 de 0.b en la sección 1, 0063 de T.1 en la sección 2, 0061 de V.1 y 0062 de V.2 en la sección 3, 0067 de V.3 en la sección 4, y 0064 de I.1, 0065 de I.3 y 0066 de I.4 en la sección 5 |

## Cómo se despliega una sección

Cada despliegue lo corres tú cuando claw te lo encarga, con los scripts de su paquete en
`docs/evidencia/bids-02/ejecucion/<D.n>/` y en este orden: `ensayo.sh`, `desplegar.sh <sha>` y `checklist.sh <sello>`.
Parte de los scripts de `docs/evidencia/jev-ads-02/ejecucion/S.3/`. El procedimiento real está en `docs/DEPLOY.md`:
copia del código (`:119-153`), migraciones con `psql -v ON_ERROR_STOP=1 -1` (`:831-851`) y crons (`:372-430`). El lead
comprueba cada despliegue en solo lectura, con la salida de `checklist.sh` y con una copia de
`docs/evidencia/repricing-01/E.0/correr.sh`.

**Mergear, desplegar y rebasar.** El despliegue de una sección corre en cuanto su PR de código se mergea. Un
despliegue lleva todo lo que está en `origin/master`. Por eso ninguna sección se mergea mientras otra está mergeada y
sin desplegar. Ningún despliegue espera a otro, salvo D.3, que corre después de D.2. La sección 1 no tiene despliegue:
su código y la 0060 viajan con el primer despliegue que corra después. Los scripts de cada despliegue van en el commit
de su fila D, dentro del PR de código de su sección. Sus salidas van en el PR de cierre.

**Migraciones.** Cada despliegue aplica todas las migraciones de BIDS 02 que trae su SHA y que no estén aplicadas,
en orden de número, aunque sean de una sección que la fila no nombra. `desplegar.sh` las lista con
`git ls-tree --name-only <sha> migrations/ | grep _bids02_ | grep -v _reversa_`. Producción no tiene tabla de
versiones: el script decide si una migración está aplicada con esta consulta. Aplica cada una en su propia corrida
de `psql` y anota su nombre en `aplicadas-<sello>.txt`.

| Migración | Está aplicada si esta consulta da verdadero |
| --- | --- |
| 0060 | `SELECT to_regclass('public.v_hoja_activa') IS NOT NULL` |
| 0061 | `SELECT to_regclass('public.ads_campana_config_observation') IS NOT NULL` |
| 0062 | `SELECT to_regclass('public.ads_placement_observation') IS NOT NULL` |
| 0063 | `SELECT pg_get_constraintdef(oid) NOT LIKE '%cache_estado%' FROM pg_constraint WHERE conname = 'target_acos_ciclo_procedencia_check'` |
| 0064 | `SELECT count(*) = 1 FROM information_schema.columns WHERE table_name = 'campana_grupo' AND column_name = 'tipo'` |
| 0065 | `SELECT to_regclass('public.impulso') IS NOT NULL` |
| 0066 | `SELECT to_regclass('public.anuncio_retiro') IS NOT NULL` |
| 0067 | `SELECT to_regclass('public.campana_ajuste') IS NOT NULL` |

Si T.1 conservó los dos peldaños en el CHECK (su cambio 8), la consulta de la 0063 no sirve. T.1 deja otra en la
descripción del PR de la sección 2.

`ensayo.sh` aplica esa misma lista sobre una copia del esquema de producción, aplica las reversas en orden inverso y
compara el esquema con el de partida. Si falla, no despliegues.

Todo `desplegar.sh` de este plan trae estas guardas y aborta si una falla:
1. `origin/master` es el SHA aprobado y su corrida `push` de Quality terminó en `success`.
2. Ningún ciclo está corriendo: `select count(*) from optimizer_cycle where status = 'running'` da 0 y
   `docker top orbit-app-1` no lista `app.cli`. Conserva el reintento de 30 minutos de `guardas_proceso`.
3. Ningún harvest está en vuelo: las dos consultas de `docs/DEPLOY.md:1930-1933` dan 0.
4. Hay respaldo del código en `predeploy-<sello>` y volcado del esquema antes de la migración. El código se copia con
   `git archive` del SHA y las rutas de `docs/DEPLOY.md:130-133`, con `md5` por archivo.
5. Las líneas de cron las instala el script: respalda el crontab de `gon`, agrega las líneas y comprueba que el
   `diff` trae solo las previstas. Si trae otra cosa, restaura el respaldo y aborta.

Corre `rollback.sh <sello>` en tres casos: `/health` distinto de 200 en dos lecturas separadas 60 segundos,
`desplegar.sh` abortó después de tocar el código, la base o el crontab, o `checklist.sh` sale 1 por algo que este
despliegue cambió. `rollback.sh` quita primero las líneas de cron que instaló, restaura el código de
`predeploy-<sello>` y aplica las reversas de `aplicadas-<sello>.txt` en orden inverso. Una `FALLA` ajena al
despliegue se reporta y no se revierte. `checklist.sh` usa las salidas del de S.3: 0, 1 con una falla medida, 3 si
falta el primer ciclo o el primer sync, y 4 si no pudo medir. El DoD de cada fila pide la salida 0.

Las cinco filas D son `[tdd:skip:ops]`. En cada una, "Pruebas primero" es el ensayo.

## Cómo se construye una pantalla

Los tipos son de la sección `Pantallas "puro contrato"` de `bosquejo.py`. Las frases son de "Lo que ve y hace el
dueño" de `design.md`. Cada pantalla son dos commits, como los dos PR de la última pantalla que se construyó: primero
el contrato y después la pantalla. P.3a es el commit de contrato de su pantalla y P.3b es el commit de pantalla. P.1,
P.2a, P.4, P.5 y P.6 llevan los dos commits en su fila. P.2b es un solo commit: agrega los botones y los avisos a las
tablas que pintó P.2a. Esta lista vale para todos los pasos P:

- **Commit de contrato.** El modelo es el PR #407 (`80da876`), que tocó tres módulos `app/jev_*.py` y creó
  `tests/test_jev_pantallas.py`, sin plantilla, ruta ni migración. El tuyo trae `app/pantalla_<x>.py` con
  sus dataclasses congeladas, `como_dict()`, `lee_<x>` de solo SELECT y `tests/test_pantalla_<x>.py`. Prueba
  con una conexión falsa y con Postgres, como `_ConnFalsa` y `ORDEN_S5` de ese archivo (`:351-373`, `:38-49`).
- **Consulta de control.** El commit de contrato trae además `ejecucion/<id>/numeros.py`, que imprime
  `lee_<x>(...).como_dict()`, y `ejecucion/<id>/control.sql`, con la consulta de control del paso. Córrelos sobre la
  misma copia de producción cuando claw te lo encarga (regla 11). Las variables de `control.sql` se pasan con
  `psql -v plataforma=amazon_mx`.
- **Commit de pantalla.** El modelo es el PR #408 (`15e4327`), que trajo la función de `app/api_dashboard.py`, la ruta
  de `app/ui.py`, `app/templates/gasto_sin_venta.html`, la entrada de `base.html` y la evidencia de
  `docs/evidencia/jev-ads-02/ejecucion/S.5/`. Extendió `tests/test_api_dashboard.py` y `tests/test_ui.py`: el tuyo
  también.
- La función de `app/api_dashboard.py` delega, como `contribucion_campanas` (`:1162-1165`). El router solo
  admite GET (`tests/test_api_dashboard.py:489`). Su prueba de SQL no cubre módulos nuevos (`:476-486`).
- `app/ui.py` llama a la función del JSON con la misma conexión (`:565-583`) y valida el mercado con
  `_vocab_o_422` (`:205-212`). El mercado se elige con dos enlaces GET (`gasto_sin_venta.html:9-13`).
- `base.html` pide tres entradas por pantalla: `paginas` (`:21-35`), el menú (`:44-52`) y
  `tab_por_pantalla` (`:96-104`). Pon `/ruido` en el grupo "Panel" y las otras cuatro en "Decidir".
- Una plantilla no lleva `<style>`, `on*=` ni `<script>` ejecutable (`tests/test_ui.py:318`). Una acción
  es un botón `data-*`, una fila oculta con actor y token, y un script de `/static` cargado en
  `{% block scripts %}` (`app/templates/cortes.html:35`, `:72-90`, `app/templates/fabrica.html:217`).
- El dinero viaja como texto (`_dec_str`, `app/api_common.py:23`). Un `None` viaja como `null` y se pinta
  con el guion de dato faltante (`app/templates/salud.html:29`). Una venta de 0 se pinta "sin ventas"
  (`app/api_dashboard.py:373-389`). La moneda sale de `PLATAFORMAS_MONEDA` (`app/optimizer/bid.py:106`).
- **Pruebas comunes.** Cada paso corre su `tests/test_pantalla_<x>.py` y además `tests/test_arq_bids_p.py
  tests/test_api_dashboard.py tests/test_ui.py tests/test_ui_copy_campana.py tests/test_architecture.py`. La primera
  tarea P de cada rama crea `tests/test_arq_bids_p.py` si no está: P.3a en la sección 2, P.1 en la sección 3, P.4 en
  la sección 5 y P.6 en la sección 6.

## Sección 1: preparación

### Qué entrega
El repo queda con el diseño, el plan y esta guía. Queda también la base de lectura que usan las demás secciones: las
vistas `v_hoja_activa` y `v_cambio_bid` y el lector `gasto_para_concluir_desde_settings`. Las cuatro sondas de
escritura quedan corridas, cada una con su conclusión. El dueño no ve nada nuevo.

### Tareas, en orden
| Tarea | Qué hace | Qué necesita |
| --- | --- | --- |
| 0.a | Incorpora el diseño, el plan y la guía al repo | Nada |
| 0.b | Crea las dos vistas con la migración 0060, agrega los candados de BIDS 02 al job `rapido` y escribe el lector del gasto para concluir | 0.a |
| V.0 | Escribe y corre las cuatro sondas de escritura | 0.a |

### Rama y PR
- 0.a va solo, en un PR de documentos por el carril `fast`. Usa la rama `bids-02/s1-0a` y el árbol
  `~/dev/wt-bids-02-s1-0a`, creados desde `origin/master`. Se mergea primero, porque todas las demás ramas
  parten de él.
- 0.b y V.0 van en el PR de código de la sección. Usa la rama `bids-02/s1-preparacion` y el árbol
  `~/dev/wt-bids-02-s1`, creados desde `origin/master` con 0.a ya mergeado. Haz un commit por tarea: `BIDS 02
  0.b: <qué hace>` y `BIDS 02 V.0: <qué hace>`.
- Esta sección no tiene despliegue. La 0060 la aplica el primer despliegue que corra después.
- Con el PR de código mergeado, corre las cuatro sondas cuando claw te lo encarga.
- El PR de cierre usa la rama `bids-02/s1-cierre`. Trae `docs/evidencia/bids-02/ejecucion/V.0/`, con las salidas de
  las sondas y `conclusion.md`, y marca las filas 0.a, 0.b y V.0. Las secciones 4 y 5 leen ese `conclusion.md`.

### Archivos de la sección
- Propios: los documentos que copia 0.a, `migrations/0060_bids02_base_lectura.sql` y su reversa,
  `tests/test_bids02_vistas.py`, `tests/test_arq_bids_ci.py`, los scripts de `ejecucion/0.b/` y las cuatro sondas de
  `tools/`.
- Compartidos con secciones posteriores: `app/optimizer/goals.py`, `tests/test_optimizer_goals.py`,
  `verify/Launch.md`, `verify/Doctor.md`, `docs/CHAT-CONTEXT.md` y `ejecucion/0.b/copia.sh`. Ninguna otra sección de
  BIDS 02 corre al mismo tiempo que esta.
- Solo esta sección edita `.github/workflows/quality.yml`, `plans/manifest.json` y la viñeta nueva de
  `plans/ROADMAP.md`.

### Comprueba la sección completa
Corre esto una vez, sobre el último commit del PR de código:
- Las pruebas focalizadas de la sección, juntas.

		uv run --frozen python -m pytest -q -rs tests/test_bids02_vistas.py tests/test_schema_docs.py \
			tests/test_arq_bids_ci.py tests/test_optimizer_goals.py tests/test_architecture.py

  Esperado: todas terminan en `passed` y ningún salto dice "sin Postgres utilizable".
- `pre-commit run --all-files` sale 0.
- La batería completa, en CI: `gh workflow run quality.yml --ref bids-02/s1-preparacion`. Esperado: el job `completa`
  termina en `success`.
- Sobre la copia de producción, cuando claw te lo encarga: `bash docs/evidencia/bids-02/ejecucion/0.b/ensayo.sh`.
  Esperado: `difieren` da 0, `control` es igual a `vista` y el esquema queda idéntico al de producción después de la
  reversa.
- Con el PR de código mergeado, cada sonda corre primero sin `--acepto-mutacion-real` y después con ella. Esperado:
  `"iguales": true` y un `resultado` que empieza con `OK`. Una sonda que falla queda "sin sellar" en `conclusion.md`.
- Esta sección no tiene checklist de despliegue.

### 0.a: incorpora el diseño y el plan al repo

Este paso solo mueve documentos. No cambia nada de `app/`, `migrations/`, `tests/` ni `tools/`.

#### Qué vas a encontrar
- La última entrada de la lista `plans` de `plans/manifest.json` es `repricing-02-construccion`. Después de la lista
  viene `"active": "fabrica-01"`.
- `plans/ROADMAP.md` trata Ads en "M2 Campañas por API". Su viñeta "MeLi Ads" empieza en la línea 61 y las filas
  AUTO-03 a AUTO-06 están en las líneas 147 a 150.
- `design.md` y los encabezados de bloque de `datos.sql` agrupan las migraciones en cuatro temas, de la 0060 a la
  0063. La tabla de "Convivencia con REPRICING 02" del plan manda sobre esos números. No los cambies en la copia.
- Ruff revisa todo `.py` del repo, también bajo `docs/`. Los scripts del diseño dan 536 errores y 16 archivos por
  reformatear. Lo mide este comando, corrido en `diseno/sintesis/`:
  `ruff check --no-cache --config ~/dev/goncloud-Orbit/pyproject.toml --statistics bosquejo.py prototipos/*.py pruebas/*.py`.

#### Pruebas primero
Este paso no lleva pruebas nuevas: la fila es `[tdd:skip:docs]`.

#### Cambios
1. Copia cada fuente a su destino. Esta es la tabla "Qué incorpora 0.a":

   | Fuente, bajo `~/.claude/orchestrate/motor-poca-data/docs/` | Destino en el repo |
   | --- | --- |
   | `diseno/sintesis/design.md` | `docs/superpowers/specs/2026-10-09-bids-02-design.md` |
   | `diseno/sintesis/bosquejo.py`, `datos.sql`, `tabla-decision.md`, `borrado.md`, `PRUEBAS.md` y `SINTESIS-NOTA.md` | `docs/evidencia/bids-02/diseno/`, con el mismo nombre |
   | `diseno/sintesis/prototipos/*.py`, `*.txt` y `RESULTADOS.md` | `docs/evidencia/bids-02/diseno/prototipos/` |
   | `diseno/sintesis/prototipos-base/*.py` y `RESULTADOS.md` | `docs/evidencia/bids-02/diseno/prototipos-base/` |
   | `diseno/sintesis/pruebas/*.py`, `*.sql`, `*.json` y `p4_salida.txt` | `docs/evidencia/bids-02/diseno/pruebas/` |
   | `plan/bids-02.md` | `plans/bids-02.md` |
   | `plan/bids-02-construccion.md` | `plans/bids-02-construccion.md` |

2. No copies `prototipos/datos/`, que es un enlace a los CSV crudos de producción, ni `pruebas/campana_dia.csv` ni
   ningún otro CSV. Tampoco copies los `.err` vacíos, `__pycache__/`, `plan/guia/`, `diseno/runner-*/`,
   `diseno/grounding*`, `diseno/TAREA.md` ni `diseno/RUBRICA.md`. Los `pruebas/*.json` sí entran, aunque traen
   nombres de campaña.
3. No dejes entrar ninguna credencial, token ni DSN. Antes del commit corre este comando y exige salida vacía:
   `grep -rlE -i 'bearer|refresh_token|client_secret|password|postgresql://|amzn1\.' docs/evidencia/bids-02 docs/superpowers/specs/2026-10-09-bids-02-design.md`.
4. En la copia del diseño, apunta los enlaces a los archivos del cambio 1 hacia `../../evidencia/bids-02/diseno/` y
   deja como texto las menciones a `../runner-c/`. En cada copia, cambia el valor de `$HOME` por `~`.
5. Corre `ruff format docs/evidencia/bids-02/`. Después pon `# ruff: noqa` como primera línea de cada `.py` de
   `prototipos/` y de `pruebas/`, y `# ruff: noqa: E501` en `bosquejo.py`. Escribe la razón en la misma línea: es
   evidencia congelada del diseño. Después del formato, corre esto donde existe el enlace `datos`. Esperado: `diff`
   vacío.

		cd ~/.claude/orchestrate/motor-poca-data/docs/diseno/sintesis/prototipos
		python3 ~/dev/wt-bids-02-s1-0a/docs/evidencia/bids-02/diseno/prototipos/a1_rejuego.py | diff - s1_salida.txt

6. Agrega al final de la lista `plans` de `plans/manifest.json` estas dos entradas, con la sangría del archivo. No
   cambies `active`.

		{"name": "bids-02", "path": "plans/bids-02.md", "description": "BIDS 02 - una sola politica de bid, niveles_v3, para una cuenta con poca data. Decide cada hoja con sus datos de 90 dias o con los de su ad group y regresa el bid que desplomo el trafico. Cinco pantallas, impulso de dos campanas por producto y ajustes de campana que aprueba el dueno. Sustituye lo abierto de acos-evidencia. Plan del 2026-10-09. Nada construido."},
		{"name": "bids-02-construccion", "path": "plans/bids-02-construccion.md", "description": "BIDS 02, guia de construccion: donde cae cada pieza del diseno en el codigo, que prueba se escribe primero y como se comprueba cada paso. Escrita el 2026-10-09. Nada construido."}

7. En `plans/ROADMAP.md`, agrega esta viñeta antes de la viñeta "MeLi Ads". No toques las filas AUTO-03 a AUTO-06:
   las cierra C.1.

		- Motor de bids: [BIDS 02](bids-02.md), plan y diseño del 2026-10-09. Nada construido. Una sola política de
		  bid para una cuenta con poca data, cinco pantallas, impulso por producto y ajustes de campaña. Cubre en
		  parte AUTO-03 a AUTO-06.

8. Agrega la entrada del 2026-10-09 al inicio de `docs/CHAT-CONTEXT.md`, con la forma de la entrada de REPRICING 02.
   Sin ella falla `tools/check_chat_context_fresh.py`, porque el plan y la guía nombran el marcador `cc:完了`. No
   marques la fila 0.a: la marca el PR de cierre de la sección 1.

#### Comprueba
- Cada destino de la tabla "Qué incorpora 0.a" existe en `origin/master` después del merge. Este comando lista
  archivos de los seis destinos:
  `git ls-tree -r --name-only origin/master docs/superpowers/specs docs/evidencia/bids-02/diseno plans | grep bids-02`.
- Los datos crudos no entran: `git diff --cached --name-only | grep -E '^(app|migrations|tests|tools)/|\.csv$'` sale
  vacío, y el `grep` de credenciales del cambio 3 sale vacío.
- Los scripts de evidencia entran formateados y con `# ruff: noqa`: `ruff check docs/evidencia/bids-02/` y
  `ruff format --check docs/evidencia/bids-02/` salen 0.
- `pre-commit run --all-files` sale 0. Los hooks que arreglan archivos fallan la primera vez: agrega de nuevo los
  archivos y corre otra vez. El hook `check-added-large-files` rechaza un archivo de más de 500 KB.

### 0.b: crea la base de lectura

#### Qué vas a encontrar
- La definición de hoja activa está escrita dos veces: `_SQL_DECISORAS` en `app/cycle.py:424`, que no filtra el
  estado, y la CTE `hojas` de `migrations/0013_entidad_inerte.sql:24`. `ad_entity_state.targeting_type` está en
  `migrations/0001_initial.sql:649` y `ad_entity.match_type` en `:261`.
- La historia de bids aplicados la leen hoy `_SQL_EN_COOLDOWN` (`app/optimizer/goals.py:547`) y
  `_SQL_ULTIMO_BID_APLICADO` (`:591`). Las dos exigen `verify_ok IS TRUE` y un ciclo ejecutor `live`.
- La reversa que pide el dueño no toca `decision_application` (`app/apply.py:1496`). Solo queda en `apply_attempt`,
  con `tipo = 'reversa'` y `resultado = 'ok'` (`_SQL_REVERSA_OK`, `app/apply.py:1582`).
- `datos.sql` es un bosquejo: le faltan `BEGIN`, `COMMIT`, los `GRANT` y el bloque `DO`. El modelo de permisos de una
  vista es `migrations/0048_margen_familia.sql:223`.
- El paso de guardas del job `rapido` corre tres archivos por nombre (`.github/workflows/quality.yml:78`). Un patrón
  `tests/test_arq_bids_*.py` que no encuentra ningún archivo hace fallar a pytest con código 4.

#### Pruebas primero
Crea `tests/test_bids02_vistas.py`. Parte de `_db_inerte` (`tests/test_entidad_inerte.py:35`) y arma la base con
todas las migraciones en orden, sin la 0011 y sin las reversas, como `tests/test_schema_docs.py:41`.

- `v_hoja_activa` trae una hoja con hoja, ad group y campaña `ENABLED`. No la trae si uno de los tres está `PAUSED`.
- `tipo_campana` da `automatica` para un product target de una campaña `AUTO` y `product_targeting` para uno de una
  campaña manual. Da `exact`, `phrase` y `broad` según el match type.
- `v_cambio_bid` trae un bid confirmado por un ciclo `live`. No trae uno de un ciclo `shadow`, uno con `verify_ok`
  falso ni uno con `old_value` igual a `new_value`. Da el origen `regreso_por_desplome` cuando `inputs.motivo` es
  ese texto.
- `v_cambio_bid` trae una fila de origen `regreso_del_dueno` por cada reversa `ok` de una decisión de bid. Su
  `bid_despues` es el `old_value` de la decisión revertida. No trae la reversa de una pausa.
- `app_decide`, `app_read` y `app_admin` pueden leer las dos vistas.
- En `tests/test_optimizer_goals.py`: `gasto_para_concluir_desde_settings` da `Decimal("350")` para `amazon_mx` y
  `Decimal("36")` para `amazon_us` con la clave ausente. Con `"abc"`, con 0 o con un negativo levanta `ValueError`.

Crea `tests/test_arq_bids_ci.py`, sin base: el paso de guardas del job `rapido` nombra `tests/test_arq_bids_*.py`.
Lee el workflow como `tests/test_precommit_hooks.py:104`.

Mutante obligatorio: `v_cambio_bid` ignora la reversa del dueño. Bórrale la segunda rama.

#### Cambios
1. Escribe `migrations/0060_bids02_base_lectura.sql` con las dos vistas del bloque 0060 de `datos.sql`. Sigue la
   regla 7. Da `SELECT` de las dos vistas a `app_decide`, `app_read` y `app_admin`.
2. Escribe `migrations/0060_reversa_bids02_base_lectura.sql` con los dos `DROP VIEW`.
3. Mide el número de vistas en una base migrada con
   `select count(*) from information_schema.views where table_schema = 'public'`. Pon ese número y su suma con 70
   en los comentarios de `verify/Launch.md` y `verify/Doctor.md`.
4. En `.github/workflows/quality.yml:78`, agrega `tests/test_arq_bids_*.py` al final del comando de pytest.
5. Escribe `ejecucion/0.b/copia.sh`. Parte de `docs/evidencia/jev-ads-02/ejecucion/S.3/ensayo.sh`, que carga en una
   base local desechable el esquema real leído con `ORBIT_DSN_READ`. Agrégale la carga de datos:

		pg_dump "$DSN" --data-only --exclude-table-data="*_seq" -t ad_entity -t ad_entity_state -t decision \
			-t decision_application -t optimizer_cycle -t apply_attempt > "$TMP/datos.sql"

   Carga ese archivo con `SET session_replication_role = replica`. El archivo se queda en `$TMP`. Esta base es la
   copia de producción de la regla 11.
6. Escribe `ejecucion/0.b/control.sql` con la consulta de control de "Comprueba" y `ejecucion/0.b/ensayo.sh`. El
   ensayo aplica la 0060 sobre la copia, corre `control.sql`, aplica la reversa y compara el esquema con el de
   producción.
7. No cambies `_SQL_DECISORAS`, `v_entidad_inerte` ni ningún lector. M.2 y M.3 pasan a las vistas.
8. Agrega `gasto_para_concluir_desde_settings` a `app/optimizer/goals.py`, del bloque `app/optimizer/goals.py` del
   bosquejo. Lee la clave `ads_gasto_para_concluir_<plataforma>`. M.3, I.3, V.4 y P.2a la importan.

#### Comprueba
- Pruebas del paso: `tests/test_bids02_vistas.py tests/test_schema_docs.py tests/test_arq_bids_ci.py
  tests/test_optimizer_goals.py`. La prueba del regreso del dueño cubre la cláusula "devuelve el regreso del dueño
  como un cambio". La del lector cubre sus dos valores y su falla cerrada.
- Cuando claw te lo encarga, corre `bash docs/evidencia/bids-02/ejecucion/0.b/ensayo.sh`. Solo lee producción. La
  consulta de control arma la hoja activa desde las tablas base, sin la vista. Esperado: `difieren` da 0 y `control`
  es igual a `vista`. Como referencia, el 2026-10-09 había 355 hojas activas en `amazon_mx` y 233 en `amazon_us`.

		WITH control AS (
			SELECT k.id FROM ad_entity k
			  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group'
			  JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
			 WHERE k.kind IN ('keyword', 'product_target')
			   AND (SELECT status FROM ad_entity_state WHERE ad_entity_id = k.id) = 'ENABLED'
			   AND (SELECT status FROM ad_entity_state WHERE ad_entity_id = ag.id) = 'ENABLED'
			   AND (SELECT status FROM ad_entity_state WHERE ad_entity_id = c.id) = 'ENABLED')
		SELECT (SELECT count(*) FROM control) AS control, (SELECT count(*) FROM v_hoja_activa) AS vista,
		       (SELECT count(*) FROM control x FULL JOIN v_hoja_activa v ON v.hoja_id = x.id
		         WHERE x.id IS NULL OR v.hoja_id IS NULL) AS difieren

- El mismo ensayo imprime que el esquema es idéntico al de producción después de la reversa.
- Candado sembrado: sube al PR un commit que agrega `tests/test_arq_bids_prueba.py` con una prueba que falla. Corre
  `gh pr checks <pr> --watch`. Esperado: `rapido` termina en `fail` y su log nombra ese archivo. Guarda la URL de la
  corrida en la evidencia. Quita el archivo con otro commit y espera a que `rapido` pase.

### V.0: sella las cuatro sondas de escritura
#### Qué vas a encontrar
- `MUTATION_REQUEST_TYPES` no trae `PUT /sp/campaigns` ni `PUT /sp/productAds` (`app/ads/write.py:103-117`).
  El cliente de lectura rechaza todo método que no sea GET o POST (`app/ads/client.py:248-270`). Una sonda
  escribe con `cliente._client.request`, por fuera de ese guard, como `pruebas/sonda_pausa_product_ad.py`.
- El precedente de `PUT /sp/campaigns` es `tools/reactiva_campanas.py`. El vendor
  `application/vnd.spcampaign.v3+json` (`:200-203`) va en `Content-Type` y en `Accept` (`:394-395`). El id
  viaja como string dentro de `{"campaigns": [{...}]}` (`:449-460`). Un 207 puede traer errores anidados
  (`:405-415`). El readback usa `campaignIdFilter` (`:418-435`). Solo cambió `state`.
- `pruebas/sonda_campanas.json` solo guarda campañas `ENABLED`: la configuración de `A1U - Auto Discovery - US` es
  `unknown` hasta la lectura inicial de cada sonda. Los valores de `offAmazonSettings` y el presupuesto diario
  mínimo de cada país son `unknown` hasta correr las sondas 4 y 2.
- Las sondas van en el PR de código de la sección 1 y su evidencia va en el PR de cierre, porque en producción solo
  escribe un script mergeado (regla 10).
#### Pruebas primero
La fila es `[tdd:skip:sonda]`: no escribas pruebas unitarias. La prueba de cada sonda es su corrida sin
`--acepto-mutacion-real`, que lee, imprime lo que mandaría y no escribe.
#### Cambios
1. Copia `docs/evidencia/bids-02/diseno/pruebas/sonda_pausa_product_ad.py` a `tools/`. Es la sonda 1. Conserva sus
   seguros: aborta si el anuncio `284583606382521` no está `ENABLED`, si la campaña no está `PAUSED` o si el anuncio
   no es de esa campaña. Pausa con `PUT /sp/productAds`, lee, reactiva y lee. Agrégale el modo sin escritura.
   Extiéndela para que cumpla el contrato del cambio 2: si falla entre sus dos PUT, reactiva el anuncio dentro de un
   `finally`, e imprime `iguales` con la comparación de `antes` y la lectura final.
2. Escribe las otras tres con este contrato. Cada una corre por la entrada estándar, sin otro archivo:
   - Lee el perfil de `amazon_us` y la campaña por nombre, con `perfiles_aceptados` y `listar_todo`
     (`app/ads/structure_api.py:317`, `:219`). Aborta si no hay exactamente una o si no está `PAUSED`.
   - Guarda el objeto completo como `antes`. Sin la bandera, imprime `antes` y el cuerpo, y sale 0.
   - Con la bandera, manda el PUT del cambio, espera 3 segundos y lee. Trata un 207 con errores como rechazo.
   - Dentro de un `finally`, manda el PUT de regreso, lee y compara con `antes`, llave por llave.
   - Imprime un solo JSON pasado por `scrub`, con cada cuerpo enviado, cada respuesta, cada lectura, `iguales` y
     `resultado`. Sale 0 con `OK`, 2 con `ABORTADO` y 1 con `REVISAR`.
3. Sonda 2, `tools/sonda_presupuesto_campana.py`. Aborta si `budget.budgetType` no es `DAILY`. Manda solo
   `campaignId` y `budget`, con `budget.budget` más 1. Anota si las demás llaves quedaron iguales.
4. Dale a la sonda 2 la bandera `--platform`. Con `amazon_mx`, lista las campañas `PAUSED` del perfil de México,
   elige la de `campaignId` menor con `budgetType` `DAILY` e imprime cuál eligió. Corre la sonda una vez por país.
5. Para medir el presupuesto diario mínimo, la sonda 2 manda antes un PUT con `budget.budget` en 0.01. Si Amazon lo
   rechaza, guarda literal el cuerpo del rechazo y el mínimo que nombra. Si Amazon lo acepta, anota 0.01 como mínimo
   aceptado. El `finally` regresa el presupuesto en los dos casos.
6. Sonda 3, `tools/sonda_ajuste_placement.py`. En vivo solo se vieron `PLACEMENT_TOP` y `PLACEMENT_PRODUCT_PAGE`. Si
   la campaña no trae `strategy`, aborta sin escribir. Elige el primero si la campaña no lo tiene, y si lo tiene, el
   segundo. Manda la `strategy` leída y una lista de una sola entrada: ese placement con su porcentaje más 1. Si
   `antes` traía otra entrada, la lectura dice si sigue. Si no, manda un segundo PUT con solo el otro placement en 1 y
   lee si el primero sigue. Regresa con la lista de `antes`. Manda en 0 lo que sobre y anota si el 0 borra la entrada.
   Al comparar `antes` con la lectura final, una entrada con 0 % cuenta como ausente.
7. Sonda 4, `tools/sonda_fuera_de_amazon.py`. Los valores permitidos de `offAmazonSettings` no están verificados. La
   sonda prueba a lo más dos valores candidatos, en este orden: `MINIMIZE_SPEND` y después `MAXIMIZE_REACH`, en el
   campo `offAmazonBudgetControlStrategy` de `offAmazonSettings`. El nombre del campo sale de la página
   `sponsored-products/3-0/openapi/prod` de la documentación de Amazon Ads: guarda ese esquema en la evidencia. Sin la
   bandera, la sonda imprime el `offAmazonSettings` de hoy y los dos cuerpos. Con un 400, guarda literal el cuerpo de
   la respuesta y prueba el siguiente candidato una vez. Amazon lista en ese cuerpo los valores que acepta
   (`tools/reactiva_campanas.py:190-192`). Regresa a lo que leíste. Si Amazon no deja regresar a `{}`, deja el valor
   que minimiza el gasto, anótalo y repórtalo bajo `Desviaciones`. La campaña está pausada, así que ese valor no
   cambia ninguna entrega.
8. Pasa `ruff format` y `ruff check` a las cuatro y haz el commit de V.0. Después de correrlas, lleva al PR de cierre
   de la sección `docs/evidencia/bids-02/ejecucion/V.0/`: la ruta y el SHA de cada sonda, sus dos salidas literales y
   `conclusion.md`.
9. Escribe en `conclusion.md`, por sonda: el cuerpo que Amazon acepta, lo que responde y si la entidad quedó como
   estaba. Escribe además si una lista parcial de `placementBidding` reemplaza la entera, qué valor candidato de
   `offAmazonSettings` aceptó Amazon y el presupuesto diario mínimo de `amazon_us` y de `amazon_mx`.
10. Si una sonda aborta o Amazon rechaza el PUT, escribe "sin sellar" en su conclusión y sigue con las otras. No
    elijas otra campaña de Estados Unidos. Los sustitutos están en la tabla de valores del plan: con la sonda 1 sin
    sellar, I.4 archiva y repone. Con la 2, la 3 o la 4 sin sellar, V.3 no construye ese ajuste y la pantalla muestra el
    dato sin botón.
11. Si la lectura final no es igual a `antes`, no repitas la sonda. Guarda la salida literal y avisa al lead. La única
    excepción es el valor que la sonda 4 deja cuando no puede regresar a `{}`.
#### Comprueba
- Corre cada sonda solo cuando claw te lo encarga, con el PR de código de la sección 1 mergeado y una sonda a la vez.
  La primera línea no escribe. La segunda termina con `"iguales": true` y un `resultado` que empieza con `OK`.

		ssh goncloud 'docker exec -i orbit-app-1 python -' < tools/sonda_presupuesto_campana.py
		ssh goncloud 'docker exec -i orbit-app-1 python - --acepto-mutacion-real' < tools/sonda_presupuesto_campana.py

- `ejecucion/V.0/` trae las dos salidas literales de cada sonda y `conclusion.md` con los cinco datos del cambio 9.
  Una sonda que falló dice "sin sellar".
- El lead repite cada sonda sin la bandera: su `antes` es igual al de la primera corrida.

## Sección 2: el motor

### Qué entrega
Una sola política de bid, `niveles_v3`, decide cada hoja, y el motor queda encendido en MX y en US. El dueño ve que el
motor deja de recortar sin evidencia y que las keywords dañadas vuelven a su bid. Tiene además dos pantallas nuevas:
keywords dañadas y ruido.

### Tareas, en orden
| Tarea | Qué hace | Qué necesita |
| --- | --- | --- |
| T.1 | Frena solo las bajadas del target y retira dos peldaños con la migración 0063 | 0.a |
| P.3a | Escribe el contrato de keywords dañadas | 0.b |
| M.1 | Escribe el caso y la política, puros | 0.b |
| M.2 | Lee el caso de la base | M.1 |
| M.3 | Cablea el ciclo y borra las dos políticas viejas | M.2 y T.1 |
| M.4 | Escribe el rejuego | M.3 y P.3a |
| M.5 | Construye el regreso del dueño | 0.b y P.3a |
| P.3b | Construye la pantalla de keywords dañadas | P.3a y M.5 |
| P.5 | Construye la pantalla de ruido | M.3 |
| D.1 | Despliega la sección antes de las 08:40 UTC y escribe la fracción de Estados Unidos | Todas las anteriores |
| X.1 | Enciende el motor el mismo día, después del ciclo de las 08:40 UTC | D.1 |

### Rama y PR
- Usa la rama `bids-02/s2-motor` y el árbol `~/dev/wt-bids-02-s2`, creados desde `origin/master` con el PR de
  código de la sección 1 mergeado.
- Haz un commit por tarea, en el orden de la tabla. P.5 lleva dos: contrato y pantalla. El commit de D.1 trae sus
  scripts y la sección D.1 de `docs/DEPLOY.md`. El commit de X.1 trae los suyos.
- claw mergea el PR de código a una hora que deja correr D.1 antes de las 08:40 UTC. X.1 corre ese mismo día, después
  del ciclo.
- El PR de cierre usa la rama `bids-02/s2-cierre`. Trae las salidas de D.1 y de X.1 y las de los scripts que leen
  producción, y marca las once filas.

### Archivos de la sección
- Propios: `app/optimizer/caso.py`, `app/optimizer/politica.py`, `app/optimizer/eras.py`, `app/lecturas_caso.py`,
  `tools/rejuega_niveles.py`, `app/pantalla_danadas.py`, `app/pantalla_ruido.py`, sus plantillas y scripts,
  `migrations/0063_bids02_peldanos_target.sql` y su reversa, y `tests/test_arq_bids_m.py`.
- Solo esta sección edita `app/optimizer/bid.py`, `app/optimizer/replay.py` y `app/apply_cola.py`: los edita M.3. M.1
  importa `_decide_pause` sin editar `bid.py`. M.4 solo llama a `reproduce`. T.1 no cambia `apply_cola.py`.
- Solo esta sección edita `app/config_write.py`, `app/templates/settings.html` y `app/static/js/settings.js`. T.1
  pinta "sin target" en `settings.html`. M.3 cambia el selector de política y la validación final de `proxima_config`.
  REPRICING 02 agrega sus claves a los mismos archivos.
- Solo M.3 edita `tests/test_architecture.py`, `AGENTS.md` y `docs/DATABASE.md`. REPRICING 02 también edita
  `tests/test_architecture.py`.
- `app/pantalla_danadas.py`: P.3a lo crea. M.4 y M.5 llaman a `lee_danadas` sin editarlo. P.3b construye la pantalla.
- Compartidos con las secciones 3, 5 y 6, que se construyen al mismo tiempo: `app/cycle.py`, `app/api_dashboard.py`,
  `app/ui.py`, `app/templates/base.html`, `tests/test_api_dashboard.py`, `tests/test_ui.py`,
  `tests/test_arq_bids_p.py`, `docs/DEPLOY.md` y `ejecucion/0.b/copia.sh`. La sección 4 edita después `app/apply.py`,
  `app/api_write.py`, `tests/test_api.py` y `tests/test_api_write.py`.

### Comprueba la sección completa
Corre esto una vez, sobre el último commit del PR de código:
- Las pruebas focalizadas de la sección, juntas. Agrega los archivos de la lista de M.3 que siguen en el repo.

		uv run --frozen python -m pytest -q -rs tests/test_target_margen.py tests/test_optimizer_goals.py \
			tests/test_cycle_target_ciclo.py tests/test_apply_cola.py tests/test_apply_schema.py tests/test_schema_docs.py \
			tests/test_optimizer_caso.py tests/test_optimizer_politica.py tests/test_lecturas_caso.py tests/test_cycle.py \
			tests/test_cycle_pause_cooldown.py tests/test_pause_economica.py tests/test_config_write.py \
			tests/test_rejuega_niveles.py tests/test_apply.py tests/test_api_write.py tests/test_api.py \
			tests/test_pantalla_danadas.py tests/test_pantalla_ruido.py tests/test_api_dashboard.py tests/test_ui.py \
			tests/test_ui_copy_campana.py tests/test_arq_bids_m.py tests/test_arq_bids_p.py tests/test_architecture.py

  Esperado: todas terminan en `passed` y ningún salto dice "sin Postgres utilizable".
- `pre-commit run --all-files` sale 0.
- La batería completa, en CI: `gh workflow run quality.yml --ref bids-02/s2-motor`. Esperado: el job `completa`
  termina en `success`.
- Sobre producción en solo lectura y sobre la copia, cuando claw te lo encarga. La consulta de T.1 da solo
  `margen_plataforma`. La consulta de M.3 sobre `evidencia_v2` da 0. `mide_lectura.py` de M.2 da menos de 1 s por
  plataforma. `mide_ciclo.sh` de M.3, con la política en `niveles_v3`, da una mediana que no pasa de la de
  `origin/master` más 30 %. `tools/rejuega_niveles.py` de M.4 imprime el informe de 30 ciclos. `numeros.py` de P.3a
  lista las mismas hojas que su consulta de control.
- El despliegue, con los scripts de `ejecucion/D.1/`: `ensayo.sh` deja el esquema idéntico, `fraccion.sh` deja la
  fracción de Estados Unidos en 0.8 y `checklist.sh <sello>` sale 0 después del ciclo de las 08:40 UTC.
- El encendido: la consulta de los criterios 4 y 5 de X.1 da `0.8|niveles_v3|niveles_v3`.

### T.1: frena solo las bajadas del target
#### Qué vas a encontrar
- `resuelve_target_margen` está en `app/optimizer/goals.py:946`. El paso es la línea `:994`. El ancla se llama
  `ultimo`, no `ancla_aplicado_pct` como en el bosquejo.
- `_converge_al_destino` (`:926`) usa el mismo `MARGEN_PASO_MAX` (`:691`) en `:942` para caminar al setting cuando
  el dato es inválido. `resuelve_target_margen_familia` (`:1054`) llama a `resuelve_target_margen`.
- La escalera es `PELDANOS_CASCADA` (`:295`) y `_nucleo_target_acos` (`:306`), que termina en el peldaño `default`
  (`:332`). `DEFAULT_TARGET_PCT` está en `:111`.
- Cinco sitios llaman a la escalera: `app/cycle.py:1850` para la hoja, `app/cycle.py:2131` para el ad group de
  términos, `app/api_dashboard.py:1719` para `/settings`, `app/apply_cola.py:666` y `app/propuestas_campana.py:651`.
  Los dos últimos ya convierten una falla en `(None, None)`.
- `/campanas` no llama a la escalera. Lee `target_acos_ciclo` (`_SQL_CONGELADO_CAMPANAS`, `app/api_dashboard.py:668`),
  publica `null` cuando el último ciclo no congeló target y filtra por `PELDANOS_CASCADA` (`app/ui.py:399`). El DoD
  del plan pide tratar las dos pantallas, `/settings` y `/campanas`.
- `_resuelve_target_ciclo` (`app/cycle.py:2238`) lee el ancla de `notes.target` del último ciclo `live` y `done`, y
  arma el snapshot en `:2305-2327`.
- El CHECK es `target_acos_ciclo_procedencia_check` (`migrations/0046_target_acos_ciclo.sql:17`, ampliado en
  `migrations/0048_margen_familia.sql:230`). `tests/test_apply_schema.py:991` exige que su lista sea igual a
  `PELDANOS_CASCADA`, en el mismo orden.
- `tests/test_target_margen.py:467` fija hoy la subida de 19 a 19.5 y `:454` la de 30 a 30.5. El motivo `sin_target`
  no existe hoy en `app/`.
#### Pruebas primero
- En `tests/test_target_margen.py`: con derivado 20 y ancla 19, el aplicado es 20. Con derivado 20 y ancla 21, es
  20.5. Con derivado 45.2 y ancla 30, es 45. `test_abstencion_converge_al_setting_sin_precipicio` (`:823`) pasa sin
  cambios.
- En `tests/test_optimizer_goals.py`: sin goal, margen ni setting, la escalera devuelve `None`. Un `acos_target` del
  estado ya no decide.
- En `tests/test_cycle_target_ciclo.py`: una hoja sin target se cuenta con `sin_target` y no deja decisión ni fila
  en `target_acos_ciclo`. El ad group de términos sin target se cuenta con `sin_target`.
  `notes.target.paso_politica` vale `asimetrico_v1`.
- En `tests/test_apply_cola.py`: sin target, la revalidación de una fila en cola da el mismo resultado que hoy da
  con `(None, None)`.
- En `tests/test_api_dashboard.py` y `tests/test_ui.py`: sin target, `/settings` trae `target_vigente` con valor
  `null` y su página dice "sin target". Una campaña sin target congelado dice "sin target" en `/campanas`. El texto
  de una propuesta de campaña sin target dice "sin target".
- En `tests/test_apply_schema.py`: con la 0063, el CHECK rechaza `cache_estado` y `default`.

Mutantes obligatorios: una subida del target camina 0.5 en vez de aplicar de una vez. Una bajada del target aplica
de una vez.
#### Cambios
1. Cambia la línea `:994` por el paso del bloque `app/optimizer/goals.py` del bosquejo. Agrega
   `MARGEN_PASO_MAX_BAJADA` y `PASO_POLITICA`. No cambies `_converge_al_destino`: la convergencia al setting sigue a
   0.5 en los dos sentidos.
2. Quita `cache_estado` y `default` de `PELDANOS_CASCADA` y de `_nucleo_target_acos`. Borra `DEFAULT_TARGET_PCT` y
   el parámetro `cache_acos_target` de las tres variantes de la escalera. Sin peldaño, las tres devuelven `None`.
   Al terminar, `git grep -nE "cache_estado|DEFAULT_TARGET_PCT" -- app | wc -l` da `0`. Hoy da 7.
3. En `app/cycle.py:1850`, si la escalera no da target, cuenta la hoja con `sin_target` y regresa antes de
   `contadores.targets` (`:1869`). En `:2131`, cuenta el ad group con `sin_target` y sáltalo.
4. No cambies `app/apply_cola.py:666`: su `(None, None)` conserva la revalidación de hoy. En
   `app/propuestas_campana.py:651`, escribe "sin target" donde el texto pinta el target.
5. En `app/api_dashboard.py:1719`, publica `target_vigente` con valor `null` y sin peldaño, y no busques ese peldaño
   en `PISA_POR_PELDANO` (`:1764`). Quita las dos etiquetas de `:1677`. En las plantillas de `/settings` y de
   `/campanas`, pinta "sin target" donde el target es `null`.
6. Agrega `paso_politica` al snapshot de `_resuelve_target_ciclo`.
7. Escribe `migrations/0063_bids02_peldanos_target.sql` y su reversa con el bloque 0063 de `datos.sql`.
8. Si la consulta previa de "Comprueba" trae filas con `cache_estado` o `default`, conserva esos dos valores en el
   CHECK y ajusta `tests/test_apply_schema.py:991` para que acepte los históricos. Avisa al lead.
#### Comprueba
- Cuando claw te lo encarga, corre en producción esta consulta con una copia de
  `docs/evidencia/repricing-01/E.0/correr.sh`, antes de pedir el merge. Esperado: solo `margen_plataforma`.
  `desplegar.sh` la repite antes de aplicar la 0063.

		select procedencia, count(*) from target_acos_ciclo group by 1

- Pruebas del paso: `tests/test_target_margen.py tests/test_optimizer_goals.py tests/test_cycle_target_ciclo.py
  tests/test_apply_cola.py tests/test_api_dashboard.py tests/test_ui.py tests/test_apply_schema.py
  tests/test_schema_docs.py`. Cubren la subida de una vez, la bajada de 0.5 y los cinco llamadores sin target.

### P.3a: escribe el contrato de keywords dañadas
#### Qué vas a encontrar
- `v_cambio_bid` trae los bids del motor y el regreso del dueño. El nombre de una hoja sale de
  `linea_entidad` (`app/etiqueta_entidad.py:67`), con `_JOINS_ANCESTROS` (`app/dashboard_pagina.py:14-20`).
- `HojaDanada` trae `ya_regresada` y no trae la fecha del regreso, que pide P.3b.
- La definición medida está en `prototipos/a6_pantallas.py`. M.4 y M.5 esperan este módulo.
#### Pruebas primero
Crea `tests/test_pantalla_danadas.py`. Crea `tests/test_arq_bids_p.py` si no está en tu rama, con el candado de
P.1: ningún `app/pantalla_*.py` importa `app.ads.write` ni `app.apply`.
- Una hoja activa con racha de recortes, 1 pedido en los 90 días previos y clics de 14 días por debajo de
  30 % de los previos entra a la lista. Con 30 % exacto, sin pedidos previos o con su campaña `PAUSED`, no.
- El orden es por venta previa, de mayor a menor. `bid_antes` es el bid anterior al primer recorte y `bid_hoy` es
  el bid vigente. Cada fila trae `pedidos_antes_90d` y `venta_antes_90d`.
- Con un `regreso_del_dueno` después de la racha, la fila trae `ya_regresada` y `regresada_el`.
#### Cambios
1. Crea `app/pantalla_danadas.py` con `HojaDanada`, `PantallaDanadas` y `lee_danadas`. Agrega a `HojaDanada` el
   campo `regresada_el: dt.date | None`.
2. Lee `v_hoja_activa`, `v_cambio_bid` y `v_metric_latest`. La racha sigue vigente cuando su último cambio es un
   `regreso_del_dueno`. Una subida del motor la cierra.
3. Usa las ventanas del prototipo. Los 90 y los 14 días previos terminan el día anterior al primer recorte. Los
   últimos 14 días van del día 15 al día 2 antes de la última fecha con métricas de la plataforma.
#### Comprueba
- Las pruebas comunes y `tests/test_pantalla_danadas.py` pasan. Cubren lo que vendía, el bid de antes y el de hoy.
- Sobre la copia, `numeros.py` de `ejecucion/P.3a/` lista las mismas hojas que esta consulta de control, con los
  mismos `recortes`, `pedidos_antes` y clics. Agrega `-t ads_metric_observation` a `copia.sh` si no está.

		WITH hoy AS (
			SELECT max(m.metric_date) AS d FROM v_metric_latest m JOIN v_hoja_activa h ON h.hoja_id = m.ad_entity_id
			 WHERE h.platform = :'plataforma'
		), cambios AS (
			SELECT c.hoja_id, c.confirmado_el, c.bid_antes, c.bid_despues
			  FROM v_cambio_bid c JOIN v_hoja_activa h USING (hoja_id)
			 WHERE h.platform = :'plataforma' AND c.bid_antes IS NOT NULL AND c.bid_despues <> c.bid_antes
		), racha AS (
			SELECT c.hoja_id, min(c.confirmado_el)::date AS inicio, count(*) AS recortes FROM cambios c
			 WHERE c.bid_despues < c.bid_antes
			   AND NOT EXISTS (SELECT 1 FROM cambios s WHERE s.hoja_id = c.hoja_id
			                    AND s.bid_despues > s.bid_antes AND s.confirmado_el > c.confirmado_el)
			 GROUP BY 1
		), medida AS (
			SELECT r.hoja_id, r.recortes,
			       sum(m.orders) FILTER (WHERE m.metric_date >= r.inicio - 90 AND m.metric_date < r.inicio) AS pedidos_antes,
			       sum(m.clicks) FILTER (WHERE m.metric_date >= r.inicio - 14 AND m.metric_date < r.inicio) AS clics_antes,
			       coalesce(sum(m.clicks) FILTER (WHERE m.metric_date BETWEEN hoy.d - 15 AND hoy.d - 2), 0) AS clics_ahora
			  FROM racha r CROSS JOIN hoy LEFT JOIN v_metric_latest m ON m.ad_entity_id = r.hoja_id GROUP BY 1, 2)
		SELECT * FROM medida WHERE pedidos_antes >= 1 AND clics_antes > 0 AND clics_ahora * 10 < clics_antes * 3 ORDER BY 1

- Como referencia, el 2026-10-09 eran 6 hojas en MX (2963, 2871, 2880, 3857, 2917 y 2969) y 3 en US (5894, 4919 y
  4924), las de `prototipos/a6_salida.txt`. La 2963 decía 31 pedidos, 28,672 MXN y bid de 9.74 a 3.73.

### M.1: escribe el caso y la política, puros
#### Qué vas a encontrar
- `test_motor_puro_sin_io` (`tests/test_architecture.py:195`) prohíbe `httpx`, `psycopg`, `app.ads` y `app.db` en
  todo módulo de `app/optimizer/` salvo `windows.py`. Tus dos módulos caen bajo ese candado sin editarlo.
- `gamma_p` y `gamma_q` están en `app/optimizer/evidencia.py:551` y `:567`. Exigen pedidos enteros. Las bandas, los
  pasos y los clamps de hoy están en `app/optimizer/bid.py:133-144` y la cola de clamps empieza en `:650`.
  `_decide_pause` está en `:304` y su motivo `pause_umbral` en `:147`.
- `prototipos/politica.py` es la misma política en `float`. `prototipos/carga.py` lee `datos/` relativo al
  directorio actual, así que solo corre donde existe ese enlace.
#### Pruebas primero
Crea `tests/test_optimizer_caso.py`, `tests/test_optimizer_politica.py` y `tests/test_arq_bids_m.py`.
- `CasoHoja.desde_json(caso.como_json()) == caso` para cada caso de las pruebas y de los fixtures. `desde_json`
  levanta `ValueError` si falta una clave o un tipo es ajeno.
- Un caso por regla, de R0 a R19 y R11b, que la dispara y da el motivo de `tabla-decision.md`, con su mutante.
- Un cambio de origen `regreso_del_dueno` tiene dirección +1. Un recorte posterior va en dirección contraria: aplica
  R14, no R15. Cuatro casos con ese último cambio. R2 antes de 7 días. R14 al día 8 con 19 clics al bid nuevo, con
  motivo `esperando_precio_medido`. El recorte con 20 clics al bid nuevo, y también al día 14 con menos de 20. R17 con
  un recorte que deja el bid en el bid que dañó a la hoja o por debajo. Ninguno da `sin_clics_nuevos`.
- Un caso de origen `ajuste_de_campana`: R2 espera y la escalera de precio no proyecta.
- `estado_grupo` da `sin_veredicto` con cualquier dato `None`. `estima` no levanta con un caso sin datos.
- La prueba dorada decide cada caso de su fixture y exige el veredicto que dio el prototipo para ese caso. El fixture
  es una muestra de a lo más 300 casos y 500 KB. Trae todos los casos que la política recorta, sube o regresa, y una
  muestra estratificada del resto. Estos casos usan la historia real de bids aplicados, como `a1_rejuego.py`.
- Los casos de la hoja 2963, uno por día del 2026-09-02 al 2026-10-09, dan todos `Mantener`. Estos casos usan la
  historia simulada: la que deja la propia política, como `a2_simulacion.py`. Con la historia real, la política da
  `Regresar` el 2026-09-26 (`design.md`).
- `typing.get_args(OrigenCambio)` contiene `ajuste_de_campana`.
- En `tests/test_arq_bids_m.py`: `caso.py` y `politica.py` no importan reloj, entorno ni azar. Reusa el detector que
  usa `test_precio_sin_reloj_ni_entorno` (`tests/test_architecture.py:1195`). Siembra un `datetime.now()` y exige que
  lo detecte.

Mutantes obligatorios: R9 recorta con un gasto menor al gasto para concluir. Un `Mantener` se convierte en recorte.
R4 recorta 25 %. R11 recorta con menos de un cuarto del gasto para concluir. R1 regresa sin el filtro de volumen. R1
regresa con una razón de 0.30 o más. R15 deja repetir sin 20 clics nuevos. R16 deja un segundo recorte con menos de
0.70 del tráfico. R17 recorta bajo el piso aprendido. `decide` lee un valor que no está en el caso. `desde_json`
acepta un JSON sin una clave.
#### Cambios
1. Crea `app/optimizer/caso.py` con el bloque `app/optimizer/caso.py` del bosquejo. Conserva los cuatro valores de
   `OrigenCambio`, con `ajuste_de_campana`: V.3 lo usa.
2. Crea `app/optimizer/politica.py` con el bloque `app/optimizer/politica.py` del bosquejo. El orden de las reglas
   es el del docstring de `decide`.
3. Importa `gamma_p` y `gamma_q` de `evidencia.py` y `_decide_pause` de `bid.py`. No los copies. Usa `Decimal` en
   toda la aritmética y compara por multiplicación, nunca por división.
4. Escribe `ejecucion/M.1/exporta_casos.py` a partir de `prototipos/politica.py` y `prototipos/a1_rejuego.py`. Importa
   `arma_caso` del prototipo, convierte cada caso a `CasoHoja` y escribe `como_json()` junto al veredicto del
   prototipo. Exporta solo números e ids, sin el texto de ninguna keyword. Para la hoja 2963, exporta los casos del
   camino simulado de `a2_simulacion.py`.
5. Guarda en `tests/fixtures/` dos archivos, cada uno de menos de 500 KB: la muestra de la prueba dorada y los casos
   de la hoja 2963. Arma la muestra con todos los casos de `a1_rejuego.py` que la política recorta, sube o regresa.
   Completa hasta 300 casos con una muestra del resto, estratificada por plataforma y motivo.
6. Dale a `exporta_casos.py` la bandera `--completo`. Con ella, decide con `app/optimizer/politica.py` todos los casos
   de `a1_rejuego.py` e imprime la tabla con el formato de `prototipos/s1_salida.txt`.
7. No toques `app/cycle.py`, `bid.py`, `evidencia.py` ni `goals.py`.
   `git diff --name-only origin/master -- app/cycle.py` sale vacío.
#### Comprueba
- Genera los fixtures donde existe el enlace `datos`. Los CSV no entran al repo:

		cd ~/.claude/orchestrate/motor-poca-data/docs/diseno/sintesis/prototipos
		W=~/dev/wt-bids-02-s2
		PYTHONPATH=$W:. $W/.venv/bin/python $W/docs/evidencia/bids-02/ejecucion/M.1/exporta_casos.py

- Pruebas del paso: `tests/test_optimizer_caso.py tests/test_optimizer_politica.py tests/test_arq_bids_m.py
  tests/test_architecture.py`. Cubren las cláusulas del DoD que son pruebas de CI. `mutantes.md` lista un mutante
  muerto por regla, de R1 a R19.
- Corre una vez la reproducción completa de `prototipos/s1_salida.txt`, contra la carpeta de datos del prototipo,
  `~/.claude/orchestrate/motor-poca-data/docs/diseno/sintesis/prototipos/datos/`. Usa los mismos comandos de arriba
  con `--completo` al final. Esperado para `amazon_mx`: 6 recortes de 405, en 4 hojas, y la tabla del bloque
  `amazon_mx | recortes`. Guarda la salida en `ejecucion/M.1/`. No es una prueba de CI.
- Si un conteo de la reproducción completa no coincide con `s1_salida.txt`, no cambies una constante para cuadrarlo.
  Busca el caso que difiere, anota la causa en la evidencia y avisa al lead.

### M.2: lee el caso de la base
#### Qué vas a encontrar
- `app/lecturas_caso.py` no existe. Hoy cada hoja cuesta de 5 a 7 consultas: `windows.ventanas_entidad`
  (`app/optimizer/windows.py:586`), `g.en_cooldown` (`app/optimizer/goals.py:562`), `g.ultimo_bid_aplicado` (`:628`)
  y `windows.cpc_vigente` (`windows.py:841`).
- La forma de una lectura por plataforma está en `_SQL_EVIDENCIA_AD_GROUP` (`windows.py:392`): una métrica con
  alguna fila `NULL` sale `None`. Esa consulta suma todas las hojas del ad group, también las apagadas.
  `_SQL_CONVERSION_GRANO` (`:706`) trata además un valor negativo como desconocido.
- `v_metric_latest` está en `migrations/0001_initial.sql:1159` y `metrics_as_of(p_as_of)` en `:1185`.
  `windows._fecha_utc` (`windows.py:450`) rechaza un `datetime` sin zona.
- El diseño no da la consulta del calendario de ingesta. Hoy la plataforma solo tiene su watermark (`windows.py:368`).
#### Pruebas primero
Crea `tests/test_lecturas_caso.py`, con Postgres y la 0060 aplicada.
- `lee_plataforma` ejecuta cuatro consultas. `caso` ejecuta cero para una hoja sin cambio de bid en 90 días y una
  para una hoja con cambio. Cuéntalas con una conexión que envuelve `execute`.
- Con dos observaciones de la misma hoja y fecha, `visto_el` entre las dos devuelve la primera. Un cambio de bid
  confirmado después de `visto_el` no entra a la trayectoria.
- Un día sin fila de la hoja y con ingesta de la plataforma cuenta como cero. Un día sin ingesta deja el tramo en
  `None`. El mutante es contar todo día sin fila como cero.
- El ad group y la cuenta suman solo hojas de `v_hoja_activa`. Una hoja sin impresiones después de su último cambio
  da `impresiones_post == 0`, no `None`.
- `caso` devuelve, para un fixture pequeño, un `CasoHoja` igual a un valor literal.
- En `tests/test_arq_bids_m.py`: `app/lecturas_caso.py` solo hace `SELECT` y no importa `app.apply` ni `app.ads`.
#### Cambios
1. Crea `app/lecturas_caso.py` con el bloque `app/lecturas_caso.py` del bosquejo. Deja privados los diccionarios de
   `LecturasPlataforma`: el único acceso es `caso`.
2. Lee las métricas de `v_metric_latest` cuando `visto_el` es `None` y de `metrics_as_of(visto_el)` cuando trae
   fecha. Lee las hojas activas de `v_hoja_activa` y los cambios de bid de `v_cambio_bid`.
3. Para el calendario de ingesta, cuenta como día con ingesta toda fecha con al menos una fila de alguna hoja de la
   plataforma en la misma lectura. Anota esta decisión en el PR: el diseño no la fija.
4. No llames a `lee_plataforma` desde `app/cycle.py`. Eso es M.3.
#### Comprueba
- Pruebas del paso: `tests/test_lecturas_caso.py tests/test_arq_bids_m.py`. Cubren el número de consultas, `visto_el`,
  el día sin fila con ingesta y el día sin ingesta.
- Escribe `ejecucion/M.2/mide_lectura.py`. Llama a `lee_plataforma` para cada plataforma sobre la copia de
  producción y mide el tiempo. Agrega `-t ads_metric_observation -t ingest_run` a `copia.sh`. Córrelo cuando claw te
  lo encarga. Esperado: menos de 1 s por plataforma.

### M.3: cablea el ciclo y borra las dos políticas viejas

Este paso es el de mayor riesgo de la sección. Cambia el camino por hoja de `app/cycle.py`.
#### Qué vas a encontrar
- `_procesa_decisora` está en `app/cycle.py:1755`. Llama a `windows.ventanas_entidad` en `:1842`, resuelve el target
  en `:1850`, llama a `bid.decide_bid` en `:1890`, aplica el cooldown en `:1916` y D.2 en `:1937`. `_pendiente_bid`
  está en `:896`. `_recorre_plataforma` (`:2516`) lee el roll-up en `:2597` y el interruptor en `:2609`.
  `_TargetCiclo` (`:2213`) no trae el margen neto: está solo en su snapshot. T.1 ya cambió la línea `:1850`.
- `app/apply_cola.py:803` llama a `decide_bid` para revalidar una PAUSE en cola. `borrado.md` no nombra esa llamada.
- `app/optimizer/replay.py:121` rejuega con `evidencia.clasifica` las filas con `evidencia_v2.via` igual a `decide`.
  `borrado.md` dice que esa política nunca decidió en vivo.
- `/settings` no puede quitar la clave de política: lo dice `_aplica_motor_bid` (`app/config_write.py:90`). El literal
  está en `CuerpoSettings` (`app/api_write.py:228`) y el selector en `app/templates/settings.html:75`. La validación
  final de `proxima_config` llama a `motor_evidencia_desde_settings` (`app/config_write.py:199`).
- El cooldown de 7 días tiene dos usos en `_procesa_decisora`: el gate de `:1806` y el bloque de `:1914-1919`. Los dos
  dependen del flag `ads_pause_sin_cooldown_bid`, que en producción está encendido. Con el flag encendido, la PAUSE se
  decide primero y `g.en_cooldown(..., kind="pause")` la salta con `cooldown_7d`. Un bid previo no enfría una PAUSE.
  `tests/test_cycle_pause_cooldown.py` fija ese comportamiento.
- `borrado.md` nombra cuatro archivos de prueba. Estos también usan lo que se borra:
  `tests/test_optimizer_ultimo_bid.py`, `tests/test_repro_a6_d2_lead.py`, `tests/test_optimizer_windows.py`,
  `tests/test_cycle_pause_cooldown.py`, `tests/test_config_write.py`, `tests/test_optimizer_goals.py`,
  `tests/test_ui.py`, `tests/test_api_dashboard.py`, `tests/test_apply.py` y `tests/test_dossier_adversarial.py`.
- `tools/dossier_adversarial.py` importa las constantes `REPLAY_*`. `tools/compara_target_margen.py` usa
  `replay_bid_con_target`. `AGENTS.md:34` y `docs/DATABASE.md:231` nombran lo que se borra. La prueba dorada es
  `tests/test_cycle.py:998` y el bloque de contrafactuales empieza en `:2995`.
- El orden de aplicación bajo cupo es `orden_bids` (`app/apply.py:553-558`), con la tabla `_PRIORIDAD_BANDA`
  (`:529-534`) y la clave `_clave_orden` (`:540-550`). `aplica_bids` lo llama en `:1326`. Hoy van primero los recortes
  de 25 %, después los de 12 % y al final las subidas. Dentro de cada banda va primero el de más gasto, leído de
  `inputs.ventanas.cortes.cost`. `tests/test_apply.py:871` fija ese orden.
- El cupo es el setting `ads_apply_cap_<plataforma>_bid`, que en producción vale 45. Un bid que no cabe se descarta
  con `fuera_de_cap` y no se reintenta (`app/apply.py:1372-1379`). Por eso hoy las subidas se descartan primero.
  `app/apply.py` escribe los motivos como literales, porque no importa el motor de bids (`:524-528`).
#### Pruebas primero
- En `tests/test_cycle.py`: con la clave ausente el ciclo no guarda ninguna decisión `bid`, cuenta cada hoja en
  `notes.skips` con `politica_apagada` y sigue guardando PAUSE, negative y harvest.
- Con `niveles_v3`, cada decisión `bid` guarda `inputs.politica` e `inputs.caso`, y `reproduce(inputs)` da el `kind`,
  el valor y la moneda guardados. Con otro valor en la clave, el ciclo termina `failed`.
- Con `niveles_v3`, una hoja con una reversa `ok` del dueño de hace 3 días y evidencia para recortar no recibe
  decisión y se cuenta con `esperando_efecto`. Al día 8 con 19 clics al bid nuevo se cuenta con
  `esperando_precio_medido`. Con 20 clics al bid nuevo, o al día 14, recibe el recorte. Un recorte que deja el bid en
  el bid que la dañó o por debajo se cuenta con `piso_aprendido`.
- `test_golden_replay_reproduce_todas_las_decisiones` conserva sus filas históricas y gana filas de la era nueva. Una
  fila sin `inputs.politica` se rejuega con `eras`.
- En `tests/test_apply.py`: `prioridad_bajo_cupo` ordena regresos, recortes con evidencia propia, subidas y recortes
  heredados. Dentro de cada nivel va primero el de más gasto.
- En `tests/test_apply.py`, con el cupo en 2 y cuatro decisiones del mismo ciclo, una por nivel: `aplica_bids` aplica
  el regreso y el recorte con evidencia propia, y descarta la subida y el recorte heredado con `fuera_de_cap`. Con el
  cupo en 1, un regreso de poco gasto se aplica antes que un recorte de mucho gasto. Con el cupo en 3, el descartado es
  el recorte heredado, aunque sea el de más gasto. Sustituye `test_orden_bids_prioridad_de_hemorragia_sellada`
  (`:871`) por estas pruebas. Pon `cupo` en el nombre de cada una.
- En `tests/test_apply.py`: cada motivo literal de `prioridad_bajo_cupo` existe en `app/optimizer/politica.py`.
- En `tests/test_config_write.py` y `tests/test_api_write.py`: `/settings` guarda `niveles_v3` con `"motor_bid":
  "niveles_v3"`, quita la clave con `"motor_bid": "apagado"` y responde 422 a `bandas_v1`. Con una config vigente que
  trae `bandas_v1`, una edición que no toca la clave levanta `SettingsInvalido`.
- En `tests/test_apply_cola.py`: una PAUSE en cola se revalida con el bloque de PAUSE de `bid.py`, sin `decide_bid`.
- En `tests/test_architecture.py`: `app/cycle.py` no importa `app.optimizer.eras`. Siembra el import y exige que el
  candado lo detecte.
- En `tests/test_cycle_pause_cooldown.py`: una hoja con una PAUSE aplicada hace 3 días no recibe otra PAUSE y se
  cuenta con `cooldown_7d`. Una hoja con un bid aplicado hace 3 días y datos maduros para pausar sí recibe la PAUSE.
- Retira o muda las pruebas de la sección 3 de `borrado.md` y las de la lista de arriba.
  `tests/test_cycle_pause_cooldown.py` se muda al camino nuevo. No lo retires.

Mutantes obligatorios: con la clave ausente, el ciclo mueve un bid. Con un valor desconocido, el ciclo sigue en vez
de fallar. Con el cupo lleno, un recorte heredado del ad group pasa antes que un regreso.
#### Cambios
1. Agrega a `app/optimizer/goals.py` `politica_bid_desde_settings`, del bloque `app/optimizer/goals.py` del bosquejo.
   `gasto_para_concluir_desde_settings` ya está ahí: la construyó 0.b.
2. Agrega `margen_neto_pct` a `_TargetCiclo`. Vale `None` cuando la procedencia no es `margen_plataforma`. En
   `_recorre_plataforma`, arma `EconomiaPlataforma` y llama a `lee_plataforma` una vez, como en "Llamadas reales".
3. En `_procesa_decisora`, sustituye el cuerpo desde `windows.ventanas_entidad` hasta el freeze por `lecturas.caso`,
   `decide` y `pendiente_bid`. No cambies los filtros de goal, ancestros, veto e inerte, ni el salto `sin_target`.
4. Con la política apagada, decide solo la PAUSE y cuenta la hoja con `politica_apagada`.
5. Crea `app/optimizer/eras.py` con `decide_bid_era_bandas`. Muda ahí, sin cambios, lo que lista la sección 2 de
   `borrado.md`: `_factor_banda`, `_factor_cero_ventas`, `_factor_ventana_v1`, los motivos de banda y
   `REPLAY_PAUSE_COST_PRE_CORTES03` de `bid.py`, y `REPLAY_PAUSE_CLICKS_PRE_CORTES01` de `cortes.py`.
6. Deja en `replay.py` `_replay_bid`, `_args_replay_bid` y `_agregado_sintetico`, detrás del despacho por era de
   `reproduce`. Borra `reproduce_evidencia_v2`, `reproduce_bandas_v1`, `verifica_politica` y sus reexportaciones de
   `app/cycle.py:205`. Antes de borrar el rejuego de `evidencia_v2`, confirma el dato en producción con una copia de
   `docs/evidencia/repricing-01/E.0/correr.sh`, cuando claw te lo encarga. Esta consulta da 0. Si da otro número,
   detente y avisa al lead.

		select count(*) from decision where inputs -> 'evidencia_v2' ->> 'via' = 'decide'

7. Borra lo que lista la sección 1 de `borrado.md`, salvo las filas del cambio 8 y lo que conserva el cambio 9:
   - De `goals.py`: `POLITICA_BANDAS_V1`, `POLITICA_BANDAS_EVIDENCIA` y `motor_evidencia_desde_settings` (`:872-903`).
     `DIAS_EVIDENCIA_INVERSION` y `POLITICA_INVERSION*` (`:145-149`). `ultimo_bid_aplicado`, `permite_reversa_bid` y
     sus tres tipos (`:585-682`). Conserva `COOLDOWN` y `en_cooldown`.
   - De `bid.py`: `fallback_v1` (`:377-417`, `:604-629`), las bandas vivas (`:235-282`, `:420-433`), los parámetros
     `evidencia`, `politica_bandas`, `confianza_recorte`, `confianza_subida` y `fallback_v1` de `decide_bid`, los
     campos `politica` y `abstencion_v2` de `ResultadoBid`, y la exigencia de 7 fechas para bids (`:587-594`).
   - De `evidencia.py`: `CostoPorClic` y `EvidenciaHoja` (`:137-159`), `clasifica` (`:356-382`),
     `parciales_evidencia`, `evidencia_hoja`, `estima_acos` y `factor_por_evidencia` (`:422-548`), y el roll-up por
     familia (`:78-119`, `:176-219`, `:385-419`). Conserva `gamma_p`, `gamma_q`, `previa_jerarquica` y `resta_conteo`.
   - De `windows.py`: `_SQL_CONVERSION_GRANO`, `_SQL_MAPEO_HOJAS`, `conversion_jerarquica` y `_familia_de_*`
     (`:698-823`). `_SQL_CPC_SLICE` y `cpc_vigente` (`:826-873`). `ventana_bids` (`:489-496`).
     `_SQL_MAX_FECHA_TERMINOS` (`:312-314`) y su llamada desde `ventanas_entidad`. No toques la ventana de cortes.
   - De `cycle.py`: `_contrafactual_v2_json` y `_contrafactual_v1_json` (`:1652-1752`), `_evidencia_v2_json`
     (`:823-893`), el bloque `:1946-2035`, el bloque de D.2 (`:1924-1945`) y los parámetros `conv_jerarquica`,
     `confianza_recorte`, `confianza_subida`, `motor_evidencia` y `pause_sin_cooldown_bid` (`:2590-2643`). Del
     gate de `:1806` y del bloque de `:1914-1919` borra solo lo que dice el cambio 9. Deja de congelar
     `evidencia_v2`, `bandas_v1`, `politica_bandas_usada`, `inversion_policy_version` y
     `cooldown_policy_version`.
   - De `apply.py`: `_PRIORIDAD_BANDA` y `orden_bids` (`:524-558`). Los sustituye `prioridad_bajo_cupo`.
   - `tools/compara_evidencia.py` y `tools/evidencia_b3_recorrido_persistido.py`.

   Al terminar, este conteo da `0`, comentarios incluidos. Hoy da 54:

		git grep -nE "decide_bid|motor_evidencia|conv_jerarquica|_contrafactual_v|_evidencia_v2_json|permite_reversa_bid|ultimo_bid_aplicado|cpc_vigente" -- app/cycle.py | wc -l

8. No borres tres filas de esa sección. La de los dos peldaños del target es de T.1. La del roster de hermanas y
   la del literal `CREAR 5 CAMPAÑAS` con su `--esperado 5` son de I.1.
9. No borres el cooldown de la PAUSE. Deja fijo el camino que producción corre hoy, con el flag
   `ads_pause_sin_cooldown_bid` encendido. El gate de `:1806` queda con `comprobar_cooldown=False`. Después de decidir
   una PAUSE, `g.en_cooldown(conn, entidad_id, ahora=decided_at, kind="pause")` salta la hoja con `cooldown_7d`. Borra
   solo la rama de bids de ese bloque, la de `kind=None`: la espera de un bid la decide R2. El parámetro
   `pause_sin_cooldown_bid` sale, como dice `borrado.md`. No borres su lector de `goals.py`: `borrado.md` no lo lista.
10. Conserva en `bid.py` una entrada que decida solo la PAUSE con `_decide_pause`. Haz que `app/apply_cola.py:803` la
    llame. `tests/test_pause_economica.py` pasa sin cambiar aserciones.
11. En `CuerpoSettings`, cambia el literal de `motor_bid` por `"niveles_v3"` y `"apagado"`. En `_aplica_motor_bid`,
    `niveles_v3` escribe la clave `ads_bid_politica_<plataforma>` y `apagado` la quita. En la validación final de
    `proxima_config` (`app/config_write.py:199`), llama a `politica_bid_desde_settings` en vez de
    `motor_evidencia_desde_settings`. En `settings.html`, deja el selector con esas dos opciones. Esta guía fija el
    literal `apagado`: el plan solo dice que la ruta quita la clave.
12. Cambia el import de `goals.py:102` y el de `bid.py:99`, que traen símbolos borrados. Actualiza `AGENTS.md:34` y
    `docs/DATABASE.md:231`. En `app/api_dashboard.py`, pon los motivos de `politica.py` en `MOTIVOS_ES_DECISIONES` y
    `MOTIVOS_ES_SALUD`, y cambia `motor_bid` (`:1747`).
13. En `app/apply.py`, escribe `prioridad_bajo_cupo(veredicto_kind, motivo)`, de la sección `app/apply.py` de
    `bosquejo.py`. Devuelve 0 para un regreso por desplome, 1 para un recorte con evidencia propia, 2 para una subida y
    3 para un recorte heredado del ad group. Los motivos de cada nivel salen de `tabla-decision.md`: R1 da el 0, R3 y
    R9 dan el 1, R6 da el 2, y R4 y R11 dan el 3. Un motivo fuera de esa tabla va al final, como hoy (`:542`). Escribe
    los motivos como literales, como hoy.
14. Haz que `aplica_bids` ordene con `prioridad_bajo_cupo` en vez de `orden_bids` (`app/apply.py:1326`). Dentro de cada
    nivel, ordena de mayor a menor por el gasto crudo de la hoja, el que R9 compara con el gasto para concluir. Léelo
    de `inputs.caso`. Un gasto `None` va al final de su nivel, como hoy (`:535-537`). No cambies el cobro del cupo ni
    el descarte con `fuera_de_cap`.
#### Comprueba
- Pruebas del paso: los archivos que nombra "Pruebas primero", `tests/test_pause_economica.py` y los de la lista de
  "Qué vas a encontrar" que siguen en el repo. Cubren la clave ausente, `niveles_v3` con su replay, el valor
  desconocido, la prueba dorada con `eras.py`, la revalidación de la pausa, el cooldown de la PAUSE, la espera después
  de un regreso del dueño, las tres respuestas de `/settings` y el orden de aplicación con el cupo lleno.
- El orden de hoy ya no existe. Este conteo da `0`. Hoy da 10:
  `git grep -nE "orden_bids|_PRIORIDAD_BANDA" -- app tests | wc -l`.
- `uv run --frozen python -m pytest -q -rs tests/test_apply.py -k cupo` termina en `passed`: un regreso se aplica antes
  que un recorte y un recorte heredado queda al final.
- `uv run --frozen python -m pytest -q --collect-only` termina sin errores: ninguna prueba importa un símbolo borrado.
  La batería completa corre una vez, al final de la sección.
- `bid.py` ya no exporta `fallback_v1` ni `politica_bandas`. Este conteo da `0`, comentarios incluidos. Hoy da 19:
  `git grep -nE "fallback_v1|politica_bandas" -- app/optimizer/bid.py | wc -l`.
- Escribe `ejecucion/M.3/mide_ciclo.sh`. Agrega a `copia.sh` las tablas que lee el ciclo. La lista es `unknown`.
  Sácala con `grep -ohE "(FROM|JOIN) [a-z_0-9]+" app/cycle.py app/optimizer/windows.py app/lecturas_caso.py | sort -u`.
  El script fuerza `ads_optimizer_mode` a `shadow` en la copia con una fila nueva de `config_version`. Corre
  `ORBIT_DSN_DECIDE=<copia> uv run --frozen python -m app.cli cycle --platform <plataforma>` cinco veces con el commit
  de `origin/master` y cinco con el tuyo, el mismo día del volcado. Antes de las cinco corridas de tu commit, escribe
  `ads_bid_politica_<plataforma> = niveles_v3` en la copia, con otra fila de `config_version`. Sin esa clave la
  medición pasa con un motor que no decide ningún bid. Las cinco corridas de `origin/master` van sin la clave: ese
  código falla cerrado con `niveles_v3`. Aborta si un ciclo no termina `done`. Lee la duración de `finished_at -
  started_at` en `optimizer_cycle`. Esperado: tu mediana no pasa de la de `origin/master` más 30 %. Córrelo cuando
  claw te lo encarga.

### M.4: escribe el rejuego
#### Qué vas a encontrar
- `tools/compara_evidencia.py` se borró en M.3. No partas de él: contaba cada abstención como recorte evitado.
- `optimizer_cycle` trae `started_at`, `mode` y `status` (`migrations/0001_initial.sql:793`).
- El cuarto criterio de `cumple()` necesita la lista de keywords dañadas. Esa lectura es `lee_danadas`, de
  `app/pantalla_danadas.py`, que construyó P.3a.
- El bosquejo firma `rejuega(conn, *, plataforma, desde, hasta)`. El plan fija la línea de comandos con `--ciclos`.
- Para cada ciclo pasado, el rejuego usa cuatro insumos. El target que ese ciclo congeló en `target_acos_ciclo`. El
  bid de ese día, que reconstruyes al caminar `v_cambio_bid` hacia atrás desde el bid de hoy. El piso y el techo de
  hoy del goal. `InsumosPausa`, leído con `visto_el`.
- Antes del encendido no hay ningún caso guardado. "Volver a decidir cada caso guardado" significa esto: arma cada
  caso, serialízalo con `como_json`, reconstrúyelo con `desde_json`, decide los dos y exige veredictos iguales.
- Una hoja elegible es una hoja que pasa los filtros que no cambian: goal habilitado, campaña, ad group y hoja
  `ENABLED`, sin veto pendiente y no inerte.
#### Pruebas primero
Crea `tests/test_rejuega_niveles.py`.
- `cumple()` es verdadero con los cuatro criterios cumplidos. Cuatro pruebas, una por criterio roto, dan `cumple()`
  falso: una fila que no se reproduce, un recorte con una regla prohibida, una cobertura de 94 % y un recorte sobre
  una hoja dañada.
- `vendedoras_que_recortaria` lista cada hoja con pedidos que recibe un recorte, con su motivo.
- `rejuega` no lee nada observado después de `started_at` de cada ciclo. Siembra una observación posterior y exige
  que el informe no cambie.
- El caso de un ciclo pasado trae el target que ese ciclo congeló y el bid de ese día. Siembra dos cambios en
  `v_cambio_bid` posteriores al ciclo y exige el bid anterior a los dos.
- Con `--ciclos 30` y 31 ciclos `live` sembrados, el informe cubre los 30 más recientes y no escribe ninguna fila.
- En `tests/test_arq_bids_m.py`: la herramienta no importa `app.apply`, `app.cycle` ni `app.ads.write`.
#### Cambios
1. Crea `tools/rejuega_niveles.py` con el bloque `tools/rejuega_niveles.py` del bosquejo. Lee con `ORBIT_DSN_READ`.
2. Haz que acepte `--platform` y `--ciclos`. Con `--ciclos N`, toma `desde` y `hasta` de los `started_at` de los
   últimos N ciclos `live` de la plataforma y llama a `rejuega`.
3. Haz que imprima el informe, con la lista `vendedoras_que_recortaria`, y salga 0 si `cumple()` es verdadero y 1 si
   no. La última línea dice `cumple: true` o `cumple: false`.
#### Comprueba
- Pruebas del paso: `tests/test_rejuega_niveles.py tests/test_arq_bids_m.py`. Cubren los cuatro criterios de
  `cumple()` y la lista de hojas con ventas.
- Cuando claw te lo encarga, córrela sobre la copia de producción. Esperado: el informe de los 30 ciclos sale en la
  misma corrida, sin esperar un ciclo nuevo, con su lista de hojas con ventas. En producción la corre X.1.

		ORBIT_DSN_READ=<copia> PYTHONPATH=. python tools/rejuega_niveles.py --platform amazon_mx --ciclos 30

### M.5: construye el regreso del dueño
#### Qué vas a encontrar
- `reversa_manual` está en `app/apply.py:1636`. Con `tipo="bid"` exige una decisión aplicada y levanta
  `ReversaYaHecha` (`:146`) si ya tiene una reversa `ok`. `reversa_bid` (`:1487`) escribe el `old_value`, con ledger,
  readback y sin cobrar cupo.
- La ruta `POST /reversa/bid` está en `app/api_write.py:341`. Los errores de reversa van a 409 por `_ERRORES_REVERSA`
  (`:328`). El cuerpo con `actor` del descarte de una propuesta es `CuerpoDescartePropuesta` (`:156`). La lista
  sellada de rutas es `SUPERFICIE_ADS_OPTIMIZER` (`tests/test_api.py:594`).
- `regreso_del_dueno_todas` relee la lista de keywords dañadas con `lee_danadas`, que construyó P.3a.
- La espera después de un regreso no es de este paso. La deciden R2, R14 y R17 de `politica.py` y la comprueba M.3.
#### Pruebas primero
Extiende `tests/test_apply.py`, `tests/test_api_write.py` y `tests/test_api.py`.
- `regreso_del_dueno` revierte la primera decisión de la racha vigente de recortes. Amazon recibe el bid anterior a
  la racha. Después, `v_cambio_bid` trae la fila de origen `regreso_del_dueno` de esa hoja.
- Una hoja sin racha levanta `SinRachaDeRecortes` y la ruta responde 409. Un segundo regreso de la misma racha
  responde 409 y el transporte falso no recibe otra llamada.
- `regreso_del_dueno_todas` con `REGRESAR 5 KEYWORDS` y 6 hojas pendientes responde 409 y no escribe. Con una hoja
  que falla, las demás se regresan y el resultado trae el motivo de la que falló.
- Las dos rutas responden 401 sin token y están en `SUPERFICIE_ADS_OPTIMIZER`.
#### Cambios
1. Agrega a `app/apply.py` `RegresoHecho`, `SinRachaDeRecortes`, `regreso_del_dueno` y `regreso_del_dueno_todas`, del
   bloque `app/apply.py` del bosquejo. Encuentra la racha en `v_cambio_bid`, no por otro camino.
2. En `regreso_del_dueno_todas`, N es el número de hojas de `lee_danadas` con `ya_regresada` en falso. Compara la
   confirmación con `REGRESAR <N> KEYWORDS` antes de escribir.
3. Agrega a `app/api_write.py` la ruta `POST /api/ads-optimizer/bid/regresar` con `CuerpoRegresarBid`, como en
   "Llamadas reales" del diseño.
4. Agrega la ruta `POST /api/ads-optimizer/bid/regresar-todas`. Su cuerpo trae `plataforma`, `confirmacion` y
   `actor`, que son los parámetros de `regreso_del_dueno_todas`. Suma las dos rutas a `SUPERFICIE_ADS_OPTIMIZER`.
5. No importes `app.ads.write` en `app/api_write.py`. Este paso no escribe en Amazon: en producción, "Regresar
   todas" la corre X.1.
#### Comprueba
- Pruebas del paso: `tests/test_apply.py tests/test_api_write.py tests/test_api.py tests/test_architecture.py`.
  Cubren el bid que recibe Amazon y su fila en `v_cambio_bid`, el segundo regreso con 409, el N que no es el
  vigente, la falla que no detiene a las demás y la lista sellada.

### P.3b: construye la pantalla de keywords dañadas
#### Qué vas a encontrar
- P.3a dejó `lee_danadas` con `ya_regresada` y `regresada_el`. M.5 dejó `POST /api/ads-optimizer/bid/regresar` y
  `POST /api/ads-optimizer/bid/regresar-todas`, con la confirmación `REGRESAR <N> KEYWORDS`.
- N es el número de filas con `ya_regresada` en falso, como en M.5.
#### Pruebas primero
Extiende `tests/test_pantalla_danadas.py`, `tests/test_api_dashboard.py` y `tests/test_ui.py`.
- Una fila con `ya_regresada` muestra la fecha de `regresada_el` y no pinta el botón.
- La página pinta "Regresar todas" con un solo campo de confirmación. Con N en 0, no lo pinta.
- En `tests/test_ui.py`, con Node: sin el literal `REGRESAR <N> KEYWORDS` exacto, la página no manda
  `bid/regresar-todas`.
#### Cambios
1. Agrega `GET /api/dashboard/keywords-danadas` como en "Llamadas reales" de `design.md`, la
   ruta `/keywords-danadas`, `app/templates/keywords_danadas.html` y `app/static/js/danadas.js`.
2. Pinta cada fila con las tres líneas del ejemplo de la hoja 2963 y el botón "Regresar el bid a 9.74".
   Pon en el formulario esta frase, con los números de la fila: "Orbit cambia ahora el bid en Amazon de 3.73 a 9.74
   MXN. El motor espera 7 días. Después no la recorta hasta que junte 20 clics al bid nuevo o pasen 14 días, y nunca
   la baja del bid que la dañó."
3. Arriba de la lista, pinta "Regresar todas". Muestra el bid al que vuelve cada una y pide el literal
   `REGRESAR <N> KEYWORDS`. Si una falla, di cuál quedó pendiente.
#### Comprueba
- Las pruebas comunes y `tests/test_pantalla_danadas.py` pasan. Cubren la fila regresada, con fecha y sin botón, y
  la confirmación única de "Regresar todas".

### P.5: construye la pantalla de ruido
#### Qué vas a encontrar
- M.3 guarda `inputs.caso` en cada decisión de bid. Su forma vive solo en `app/optimizer/caso.py`.
- Las abstenciones no son filas. Están contadas por motivo en `notes` del ciclo, que es texto de formato
  mixto: se lee con `_parse_notes` (`app/api_common.py:38`), como `_skips_de` (`app/api_dashboard.py:989`).
- `config_version` guarda `created_at` y `settings` (`migrations/0001_initial.sql:624`). La fecha del
  encendido es la de la primera fila con `ads_bid_politica_<plataforma>` en `niveles_v3`.
#### Pruebas primero
Crea `tests/test_pantalla_ruido.py`.
- Por ciclo, las decisiones salen contadas por motivo y nivel, y las abstenciones por motivo. Un ciclo con
  `notes` ilegible trae sus abstenciones en `None`, no en 0.
- Una hoja con bid de 10 hace 90 días y 4 hoy trae un encogimiento de 0.4. Una hoja sin cambios no sale.
- Sin fila de encendido, `antes_y_despues` es `None` y la plantilla lo dice. MX no trae filas de US.
- La página trae la frase "No es causal" encima de la comparación.
- Candado: ningún `_SQL_*` de `app/pantalla_ruido.py` nombra una llave de `inputs.caso`.
#### Cambios
1. Commit de contrato: crea `app/pantalla_ruido.py` con `PantallaRuido` y `lee_ruido`.
2. Trae las filas de `decision` y pasa cada `inputs["caso"]` por `CasoHoja.desde_json`, sin leerlo en SQL.
3. Calcula el encogimiento con `v_cambio_bid`: el bid de hoy entre el bid más antiguo de 90 días.
4. Compara 30 días previos al encendido con los posteriores, en `v_metric_latest`, solo campañas `ENABLED`.
5. Commit de pantalla: agrega `GET /api/dashboard/ruido`, la ruta `/ruido` y `app/templates/ruido.html`.
6. Encima de la comparación, pinta: "Es una comparación simple de antes y después. No es causal."
#### Comprueba
- Las pruebas comunes y `tests/test_pantalla_ruido.py` pasan. Cubren las decisiones y abstenciones por motivo, el
  encogimiento por hoja y la comparación por mercado con su frase.

### D.1: despliega el motor y escribe la fracción de Estados Unidos
#### Qué vas a encontrar
- Lleva el PR de la sección 2: T.1, P.3a, M.1 a M.5, P.3b y P.5. Su migración es la 0063. Aplica también la 0060, de
  la sección 1, si ningún despliegue anterior la aplicó. No cambia el cron.
- D.1 corre antes de las 08:40 UTC, la hora del ciclo de `amazon_us`. El de `amazon_mx` corre a las 08:41. Ese ciclo
  corre con la clave de política ausente y no mueve ningún bid: es el ciclo que observa el DoD de esta fila. X.1 corre
  el mismo día, después de ese ciclo.
- La clave `ads_bid_politica_<plataforma>` no existe hoy en producción. Si la última `config_version` trae un valor en
  `ads_bid_politica_amazon_mx` o `ads_bid_politica_amazon_us`, el ciclo nuevo falla cerrado: el código nuevo solo
  acepta `niveles_v3`.
- Con el código de T.1 y la fracción de hoy, el target de Estados Unidos salta a 28.34 % en su primer ciclo, y bajar
  camina 0.5 por ciclo. Por eso D.1 escribe la fracción 0.8 antes del primer ciclo posterior al despliegue. La ruta de
  settings de hoy ya acepta `margen.fraccion`.
- Un setting se escribe con `POST /api/ads-optimizer/settings/{platform}` (`app/api_write.py:430-457`). El cuerpo
  exige `base_config_version_id`: léelo con `SELECT id FROM config_version ORDER BY id DESC LIMIT 1`. Con un 409,
  relee el id y repite una vez. Un 422 con "edicion vacia" significa que el valor ya estaba.
#### Pruebas primero
Corre `ensayo.sh`. Esperado: el esquema queda idéntico después de las reversas.
#### Cambios
1. Escribe `ensayo.sh`, `desplegar.sh`, `checklist.sh` y `rollback.sh` de `ejecucion/D.1/`, y la sección D.1 de
   `docs/DEPLOY.md`.
2. Agrega dos guardas propias a `desplegar.sh`. La última `config_version` no trae ninguna de las dos claves de
   política. La consulta de "Comprueba" de T.1 no trae `cache_estado` ni `default`.
3. Escribe `ejecucion/D.1/fraccion.sh`. Simula por omisión y escribe solo con `--acepto-mutacion-real`. Manda esta
   llamada. La respuesta trae `"created": true` y un `label` con `margen encendido (fraccion <antes> -> 0.8)`. El
   token de escritura no sale del servidor ni se imprime.

		ssh goncloud 'curl -sS -X POST http://127.0.0.1:8010/api/ads-optimizer/settings/amazon_us \
			-H "Content-Type: application/json" \
			-H "x-orbit-token: $(cat /mnt/data/appdata/orbit/secrets/api_write_token)" \
			-d "{\"base_config_version_id\": <id>, \"margen\": {\"habilitado\": true, \"fraccion\": 0.8}}"'

4. Corre `fraccion.sh` antes de `desplegar.sh`. El código anterior camina hacia el target nuevo a 0.5 por ciclo, así
   que la fracción escrita antes del despliegue no produce ningún salto. Agrega a `desplegar.sh` una tercera guarda
   propia: la fracción de `amazon_us` es 0.8.
5. Haz que `checklist.sh` imprima los conteos de `pause`, `negative` y `harvest` del primer ciclo de cada plataforma
   posterior al arranque, junto a los del ciclo anterior. Haz que pida `/keywords-danadas` y `/ruido`.
6. Deja escrita la consecuencia en la sección D.1 de `docs/DEPLOY.md` y en la línea de entrega de la sección: después
   de D.1 el motor no mueve ningún bid hasta X.1. `rollback.sh` regresa el motor anterior, que vuelve a recortar.
   `rollback.sh` no regresa la fracción: 0.8 es la decisión D1 y vale también con el código anterior.
7. Haz que `rollback.sh` lea la clave de política antes de restaurar el código. Si una plataforma la trae, X.1 ya
   corrió: `rollback.sh` corre primero `ejecucion/X.1/apagar.sh`, que quita la clave. El código anterior falla cerrado
   con `niveles_v3`.
#### Comprueba
- `checklist.sh` sale 0, con `/health` en 200 y con `v_hoja_activa` y `v_cambio_bid` creadas.
- La clave de política está ausente: `SELECT settings ? 'ads_bid_politica_amazon_mx', settings ?
  'ads_bid_politica_amazon_us' FROM config_version ORDER BY id DESC LIMIT 1` da `f|f`.
- La fracción de Estados Unidos es 0.8 antes de las 08:40 UTC: `SELECT settings ->>
  'ads_target_fraccion_margen_amazon_us' FROM config_version ORDER BY id DESC LIMIT 1` da `0.8`.
- El primer ciclo de cada plataforma posterior al arranque es el de las 08:40 UTC del día de D.1. Terminó sin
  `failed`, no dejó ninguna `decision` con `kind = 'bid'` y su `notes` contiene `politica_apagada`. Sus conteos de
  `pause`, `negative` y `harvest` salen junto a los del ciclo anterior.
- `/keywords-danadas` y `/ruido` responden 200.

### X.1: enciende el motor en México y en Estados Unidos

Sigue los "Criterios de encendido" del plan, en orden y el mismo día que D.1, después del ciclo de las 08:40 UTC.
El primer ciclo con la política encendida es el del día siguiente.
#### Qué vas a encontrar
- Un setting se escribe con `POST /api/ads-optimizer/settings/{platform}` (`app/api_write.py:430-457`), que llama a
  `config_write.guarda_config` (`app/config_write.py:209-240`), el único escritor de `config_version`. El cuerpo es
  `CuerpoSettings` (`app/api_write.py:209-228`) y exige `base_config_version_id`: léelo con `SELECT id FROM
  config_version ORDER BY id DESC LIMIT 1`. Con un 409, relee el id y repite una vez. Un 422 con "edicion vacia"
  significa que el valor ya estaba.
- M.3 dejó en esa ruta `"motor_bid": "niveles_v3"`, que enciende, y `"motor_bid": "apagado"`, que quita la clave.
- `tools/rejuega_niveles.py` no está en la imagen (`Dockerfile:21-24`). Córrelo por la entrada estándar.
- La pantalla de dañadas llega con D.1. `regresar.sh` no la usa: N sale de `lee_danadas`.
#### Pruebas primero
La fila es `[tdd:skip:ops]`. Cada script que escribe simula por omisión y escribe solo con `--acepto-mutacion-real`.
Corre cada uno sin la bandera y guarda su salida antes de la corrida real.
#### Cambios
Escribe en el commit de X.1, dentro del PR de código de la sección 2, los scripts de
`docs/evidencia/bids-02/ejecucion/X.1/`: `rejuego.sh`, `encender.sh`, `regresar.sh`, `apagar.sh` y `lectura.sql`.
Guarda cada salida con su código. El token de escritura no sale del servidor ni se imprime. Después sigue estos pasos:
1. **Desplegar ya detiene los recortes.** Comprueba que `checklist.sh` de D.1 sale 0 y que la clave de política da
   `f|f`. El ciclo de las 08:40 UTC ya corrió con la clave ausente.
2. **Rejuega el último mes.** Corre `rejuego.sh`, una vez por plataforma. Sale 0 y su última línea dice
   `cumple: true` cuando se cumplen los cuatro criterios.

		ssh goncloud 'docker exec -i orbit-app-1 python - --platform amazon_mx --ciclos 30' \
			< tools/rejuega_niveles.py > docs/evidencia/bids-02/ejecucion/X.1/rejuego-amazon_mx.txt

   Si `cumple()` es falso en una plataforma, no corras el paso 5 para ella. Escribe en
   `ejecucion/X.1/no-encendida-<plataforma>.md` cuál de los cuatro criterios falló y su número medido. claw avisa al
   dueño y la otra plataforma sigue. La que falló queda con la clave ausente y sin mover bids.
3. **Prepara el aviso al dueño.** Escribe `ejecucion/X.1/aviso.md` con los recortes y las subidas del mes rejugado
   por plataforma, y con la lista `vendedoras_que_recortaria` del `InformeRejuego`. claw lo manda. No esperes respuesta.
4. **Comprueba la fracción de Estados Unidos.** D.1 ya la escribió. Este paso solo lee: `SELECT settings ->>
   'ads_target_fraccion_margen_amazon_us' FROM config_version ORDER BY id DESC LIMIT 1` da `0.8`. Si da otro valor,
   detente y avisa al lead.
5. **Enciende.** Corre `encender.sh`. Manda esta llamada una vez para `amazon_mx` y otra para `amazon_us`. No toques
   los goals ni `ads_optimizer_mode`. `apagar.sh` manda `"motor_bid": "apagado"` y queda listo sin correr.

		ssh goncloud 'curl -sS -X POST http://127.0.0.1:8010/api/ads-optimizer/settings/amazon_mx \
			-H "Content-Type: application/json" \
			-H "x-orbit-token: $(cat /mnt/data/appdata/orbit/secrets/api_write_token)" \
			-d "{\"base_config_version_id\": <id>, \"motor_bid\": \"niveles_v3\"}"'

6. **Regresa las keywords dañadas.** `regresar.sh` llama a `POST /api/ads-optimizer/bid/regresar-todas` una vez por
   plataforma, también en una que no se encendió. Antes lee N: corre `lee_danadas` por la entrada estándar del
   contenedor, con `ORBIT_DSN_READ`, y cuenta las hojas con `ya_regresada` en falso. Manda
   `confirmacion: "REGRESAR <N> KEYWORDS"` y `actor: "plan bids-02 X.1"`. Un 409 significa que la lista cambió:
   relee N y repite una vez. Guarda la respuesta completa. Una keyword que falló queda anotada y no detiene a las
   demás. Como referencia, el 2026-10-09 eran 6 en MX y 3 en US.
7. **Lee el primer ciclo.** A la mañana siguiente el lead corre `lectura.sql` en solo lectura. No frena nada.

		SELECT c.platform, c.id, c.status, count(d.id) FILTER (WHERE d.kind = 'bid') AS bids,
			count(d.id) FILTER (WHERE d.kind = 'bid' AND d.inputs ? 'caso') AS con_caso
		FROM optimizer_cycle c LEFT JOIN decision d ON d.cycle_id = c.id
		WHERE c.started_at >= current_date AND c.platform IN ('amazon_mx', 'amazon_us') GROUP BY 1, 2, 3

		SELECT t.procedencia, t.target_acos_pct, count(*) FROM target_acos_ciclo t JOIN optimizer_cycle c
			ON c.id = t.cycle_id WHERE c.platform = 'amazon_us' AND c.started_at >= current_date GROUP BY 1, 2

		SELECT c.platform, d.inputs ->> 'motivo' AS motivo, count(*) FROM decision d JOIN optimizer_cycle c
			ON c.id = d.cycle_id WHERE d.kind = 'bid' AND d.new_value < d.old_value
			AND c.started_at >= current_date GROUP BY 1, 2

#### Comprueba
- Criterios 1 y 3: `checklist.sh` de D.1 sale 0, y `aviso.md` existe antes del paso 5.
- Criterio 2: `rejuego-<plataforma>.txt` termina en `cumple: true`, o existe `no-encendida-<plataforma>.md`.
- Criterios 4 y 5: `SELECT settings ->> 'ads_target_fraccion_margen_amazon_us', settings ->>
  'ads_bid_politica_amazon_mx', settings ->> 'ads_bid_politica_amazon_us' FROM config_version ORDER BY id DESC
  LIMIT 1` da `0.8|niveles_v3|niveles_v3`. Una plataforma que no se encendió sale vacía.
- Criterio 6: la respuesta de cada llamada trae un `RegresoHecho` o un motivo por hoja, y una segunda lectura de N
  da 0 más las que fallaron.
- Criterio 7: `bids = con_caso` en cada ciclo. La fila `margen_plataforma` de Estados Unidos dice 25.19. Los motivos
  de recorte son solo los de R3, R4, R9 y R11 en `tabla-decision.md`: `pierde_dinero`, `pierde_dinero_fuerte`,
  `grupo_sangra_vendedora`, `gasto_sin_venta`, `gasto_sin_venta_doble` y `grupo_sangra`.

## Sección 3: ver la campaña

### Qué entrega
El sync guarda la configuración de cada campaña y un reporte diario trae el gasto por placement. El dueño ve la
pantalla "Dónde poner el dinero", con el gasto por tipo de campaña, por placement y por campaña, y recibe los tres
avisos diarios de campaña. Las tablas por placement y por campaña son de solo lectura. Sus botones los agrega P.2b, en
la sección 4.

### Tareas, en orden
| Tarea | Qué hace | Qué necesita |
| --- | --- | --- |
| V.1 | Guarda la configuración de campaña en el sync, con la migración 0061 | 0.a |
| V.2 | Pide el reporte por placement, con la migración 0062 | 0.a |
| P.1 | Construye la tabla por tipo de campaña y la pantalla de dinero | 0.b |
| P.2a | Escribe el contrato de las tablas por placement y por campaña y las pinta, sin botones | P.1, V.1, V.2 y 0.b |
| V.4 | Manda los tres avisos diarios | P.2a y 0.b |
| D.2 | Despliega la sección e instala dos líneas de cron | Todas las anteriores |

### Rama y PR
- Usa la rama `bids-02/s3-ver` y el árbol `~/dev/wt-bids-02-s3`, creados desde `origin/master` con el PR de
  código de la sección 1 mergeado.
- Haz un commit por tarea, en el orden de la tabla. P.1 y P.2a llevan dos cada una: contrato y pantalla. El commit de
  D.2 trae sus scripts y la sección D.2 de `docs/DEPLOY.md`.
- D.2 corre en cuanto claw mergea el PR de código.
- El PR de cierre usa la rama `bids-02/s3-cierre`. Trae las salidas de D.2 y marca las seis filas.

### Archivos de la sección
- Propios: `app/ads/campana_config.py`, `app/ads/placements.py`, `app/avisos_campana.py`, `app/pantalla_dinero.py`,
  `app/templates/donde_poner_el_dinero.html`, las migraciones 0061 y 0062 con sus reversas, y
  `tests/test_arq_bids_v.py`.
- Qué construir está en la sección "Ver y ajustar la campaña" de `design.md` y en las secciones de `bosquejo.py` que
  nombra cada paso. La pantalla dice "ubicación". Aquí es placement.
- `app/templates/donde_poner_el_dinero.html`: P.1 la crea con la tabla por tipo. P.2a le agrega las tablas por
  placement y por campaña, sin botones. La sección 4 le agrega después los botones y los avisos de cada campaña.
- Solo esta sección edita `app/ads/structure.py` y `app/ads/structure_plan.py`: los edita V.1.
- Solo esta sección edita `app/ads/reports.py`: V.2 agrega la bandera `--placements` a `reports.main`. El reporte vive
  en `app/ads/placements.py`.
- Solo esta sección edita `app/notifica.py` y `app/templates/salud.html`: los edita V.4. REPRICING 02 también edita
  `notifica.py`.
- Compartidos con las secciones 2, 5 y 6, que se construyen al mismo tiempo: `app/api_dashboard.py`, `app/ui.py`,
  `app/templates/base.html`, `tests/test_api_dashboard.py`, `tests/test_ui.py`, `tests/test_arq_bids_p.py`,
  `app/cli.py`, `app/cli_bids.py`, `tests/test_cli.py`, `verify/Launch.md`, `verify/Doctor.md`, `docs/DEPLOY.md` y
  `ejecucion/0.b/copia.sh`. La sección 4 edita después `app/pantalla_dinero.py`,
  `app/templates/donde_poner_el_dinero.html`, `app/ads/campana_config.py` y `tests/test_arq_bids_v.py`.

### Comprueba la sección completa
Corre esto una vez, sobre el último commit del PR de código:
- Las pruebas focalizadas de la sección, juntas.

		uv run --frozen python -m pytest -q -rs tests/test_campana_config.py tests/test_migracion_0061.py \
			tests/test_structure_sync.py tests/test_placements.py tests/test_migracion_0062.py tests/test_placements_cron.py \
			tests/test_reports_pipeline.py tests/test_ads_salud.py tests/test_ads_producto_ingesta.py tests/test_cli.py \
			tests/test_pantalla_dinero.py tests/test_avisos_campana.py tests/test_avisos_campana_cron.py \
			tests/test_notifica.py tests/test_api_dashboard.py tests/test_ui.py tests/test_ui_copy_campana.py \
			tests/test_schema_docs.py tests/test_arq_bids_v.py tests/test_arq_bids_p.py tests/test_architecture.py

  Esperado: todas terminan en `passed` y ningún salto dice "sin Postgres utilizable".
- `pre-commit run --all-files` sale 0.
- La batería completa, en CI: `gh workflow run quality.yml --ref bids-02/s3-ver`. Esperado: el job `completa` termina
  en `success`.
- Sobre la copia de producción, cuando claw te lo encarga. `ensayo.sh` de V.1 deja el esquema idéntico. `numeros.py`
  de P.1 da por tipo el mismo gasto, pedidos y venta que su consulta de control.
- La pantalla de dinero pinta las tablas por placement y por campaña, sin ningún botón de ajuste: lo prueban
  `tests/test_api_dashboard.py` y `tests/test_ui.py`, que extiende P.2a.
- El despliegue, con los scripts de `ejecucion/D.2/`: `ensayo.sh` deja el esquema idéntico y `checklist.sh <sello>`
  sale 0 después del sync de las 06:45 UTC y del reporte de las 07:25 UTC. En esa salida, las tablas por placement y
  por campaña traen filas en los dos mercados.

### V.1: guarda la configuración de campaña
#### Qué vas a encontrar
- `_plan_items` lee cuatro llaves de cada campaña y tira el resto (`app/ads/structure_plan.py:281-301`).
  Su comentario de `:297` y el docstring de `app/ads/structure.py:28-30` dicen que el presupuesto no se
  guarda porque llega sin moneda. Los bids tampoco la traen y usan la del perfil (`structure_plan.py:274`).
- `sync_structure` trabaja en una sola transacción (`structure.py:335`). `_insertar_acta` es el precedente
  de una escritura más dentro de ella (`:433`). Los payloads crudos están en `EstructuraPerfil.campanas`.
- El bloque "0061 VER LO INVISIBLE" de `datos.sql` trae tres tablas. La 0061 lleva solo la primera y su vista.
  `ads_placement_observation` es de V.2 y `campana_ajuste` es de V.3.
#### Pruebas primero
Crea `tests/test_campana_config.py`, `tests/test_migracion_0061.py` y `tests/test_arq_bids_v.py`. Extiende
`tests/test_structure_sync.py`.
- Con una campaña como las de `pruebas/sonda_campanas.json`: `budget` 10.08 da `Decimal("10.08")` con la
  moneda del perfil, la estrategia sale tal cual y `PLACEMENT_PRODUCT_PAGE` 40 da 40 y 0 en los otros dos.
- Sin `dynamicBidding`, los tres ajustes y la estrategia son `None`. Un presupuesto 0 o de texto, un
  placement desconocido y un porcentaje fuera de 0 a 900 dan `None` para la campaña entera.
- `guarda_config` con la misma configuración dos veces deja una fila. Con un campo distinto deja dos. El
  mutante es insertar siempre. Una campaña ilegible se cuenta en `skip_reason` y su `ad_entity` se escribe.
- Después de `sync_structure` con dos campañas, `v_campana_config_vigente` trae para cada una presupuesto, moneda,
  estrategia, los tres ajustes y `fuera_de_amazon`.
- La 0061 rechaza un presupuesto sin moneda, un ajuste de 901 y un `ad_entity` que no es campaña. La reversa deja
  el esquema como estaba.
- Candado con fuga sembrada: en `app/`, solo `app/ads/campana_config.py` nombra `placementBidding` y
  `offAmazonSettings`. Es el DoD "ningún payload de Amazon sale de `config_de_payload`".
#### Cambios
1. Escribe `migrations/0061_bids02_campana_config.sql` con `ads_campana_config_observation` y
   `v_campana_config_vigente`, del bloque "0061 VER LO INVISIBLE" de `datos.sql`. Sigue la regla 7.
2. Agrega a la tabla la columna `fuera_de_amazon TEXT`, que `ConfigCampana` trae y `datos.sql` no. Agrega lo que
   `datos.sql` declara que le falta, con la forma de `migrations/0051_ads_acta_listado.sql`, y el trigger que exige
   `kind = 'campaign'`.
3. Da INSERT sobre la tabla a `app_ingest` y SELECT de la tabla y de la vista a `app_read` y `app_admin`.
4. Crea `app/ads/campana_config.py` con `ConfigCampana`, `config_de_payload` y `guarda_config`, de la
   sección de `bosquejo.py` con ese nombre. Agrega `como_json`, `desde_json` y `config_vigente(conn, id)`.
5. Convierte el presupuesto como `_bid_decimal` (`structure_plan.py:57-78`). Guarda un `offAmazonSettings`
   no vacío como texto JSON con las llaves ordenadas, hasta que V.3 use el vocabulario de la sonda 4.
6. En `sync_structure`, después de `_insertar_acta`, convierte `est.campanas` de cada perfil y llama a
   `guarda_config`. Suma las filas insertadas a `written` y las campañas ilegibles a `skips`.
7. Corrige el comentario de `structure_plan.py:297` y el docstring de `structure.py:28-30`. Sube en 1 el conteo
   de tablas y en 1 el de vistas de `verify/Launch.md` y `verify/Doctor.md`.
8. Escribe `docs/evidencia/bids-02/ejecucion/V.1/ensayo.sh` a partir de
   `docs/evidencia/jev-ads-02/ejecucion/S.3/ensayo.sh`.
#### Comprueba
- Pruebas del paso: `tests/test_campana_config.py tests/test_migracion_0061.py tests/test_structure_sync.py
  tests/test_schema_docs.py tests/test_arq_bids_v.py tests/test_architecture.py`. Cubren la configuración completa
  tras un sync, la fila nueva solo con cambio y el conteo de tablas.
- Ningún payload sale de `config_de_payload`:
  `git grep -n -e placementBidding -e offAmazonSettings -- app | grep -vc '^app/ads/campana_config.py'` da `0`.

### V.2: pide el reporte por placement
#### Qué vas a encontrar
- Amazon aceptó en vivo `spCampaigns` con `groupBy ["campaign", "campaignPlacement"]` y datos diarios
  (`PRUEBAS.md`, prueba 2). Rechazó `topOfSearchImpressionShare` con 400 (`pruebas/sonda_placement2.json`).
  El reporte trae `campaignId` y `cost` como números. `ad_entity.external_id` es texto.
- `solicitar_reporte` manda siempre `"timeUnit": "DAILY"` (`app/ads/reports.py:625`). El precedente es el
  reporte de productos anunciados: `PRODUCTOS_CFG` fuera de `REPORTES_CFG` (`:390-410`), `source` propio
  (`:250`, `:1778`) y la bandera `--productos` (`:2053-2060`). Un fallo de un reporte aborta su corrida
  entera (`sync_metrics`, `:1748`). `ads-salud` solo mira el `source` principal (`app/ads/salud.py:20`).
- `ingest metrics` pasa sus argumentos a `reports.main` (`app/cli.py:175-176`). La bandera nueva no toca `app/cli.py`.
- El instalador del cron borra toda línea suelta con `app.cli ingest` (`docs/DEPLOY.md:643`). La línea de
  productos vive dentro de `ORBIT_BLOCK` (`:627-641`, línea `:635`).
- `datos.sql` escribe el CHECK de `placement` con cuatro textos que no son los del tipo `Ubicacion` del bosquejo, y
  trae la columna `top_of_search_is`, que Amazon no entrega.
#### Pruebas primero
Crea `tests/test_placements.py`, `tests/test_migracion_0062.py` y `tests/test_placements_cron.py`.
- El cuerpo del reporte trae ese `groupBy` y `DAILY`, sin `topOfSearchImpressionShare`. Agrégala de mutante.
- Los cuatro valores de `placementClassification` de `PRUEBAS.md` dan los cuatro de `Ubicacion`. Un valor
  desconocido, una campaña que no está en `ad_entity` y una métrica negativa saltan la fila y se cuentan.
- El mismo reporte ingerido dos veces no agrega filas. El mutante es quitar `ON CONFLICT`. Dos reportes
  distintos del mismo día dejan dos observaciones, y la suma leída con `DISTINCT ON` cuenta una.
- Con un transporte que falla, queda un `ingest_run` del `source` nuevo con `ok` falso y ninguno de
  `amazon_ads_reports_v3`. `reports.main(["--placements"])` devuelve 1 y no llama a `sync_metrics`.
- La 0062 rechaza un `placement` fuera de los cuatro de `Ubicacion`. La reversa deja el esquema como estaba.
- La línea de cron queda una sola vez después de reinstalar el bloque (`tests/test_ads_salud_cron.py`).
#### Cambios
1. Escribe `migrations/0062_bids02_placement.sql` con `ads_placement_observation`, del mismo bloque de `datos.sql`.
   Usa en el CHECK de `placement` los cuatro valores de `Ubicacion`: `arriba_de_busqueda`, `resto_de_busqueda`,
   `paginas_de_producto` y `fuera_de_amazon`. No crees `top_of_search_is`.
2. Agrega el índice de dedupe de `migrations/0020_ads_producto_metrica.sql:65-68`. Da INSERT a `app_ingest` y SELECT
   a `app_read` y `app_admin`. Sube en 1 el conteo de tablas de `verify/`.
3. Crea `app/ads/placements.py` con `sync_placements` de `bosquejo.py`, la configuración del reporte, el
   planificador puro de filas y `SOURCE_PLACEMENTS = "amazon_ads_placements_v3"`.
4. Pide solo `date`, `campaignId`, `placementClassification`, `impressions`, `clicks`, `cost`,
   `purchases30d` y `sales30d`. Llama a `solicitar_reporte`, `esperar_reporte` y `descargar_filas`.
5. Termina la fase de API antes de abrir la transacción. Abre y sella tu `ingest_run` como `sync_metrics`.
6. Guarda `metric_currency` con la moneda del perfil. Inserta con `ON CONFLICT DO NOTHING` y cuenta las
   filas absorbidas como saltadas.
7. En `reports.main`, agrega `--placements`, excluyente con `--productos`. Con la bandera, importa
   `app.ads.placements` dentro de la función y llama a `sync_placements` con el mismo rango de fechas. Sin
   `ORBIT_DSN_INGEST`, el comando imprime `ORBIT_DSN_INGEST no esta definido` y sale 2.
8. En `docs/DEPLOY.md`, agrega la fila a la tabla de crons (`:380-387`) y estas dos líneas dentro de
   `ORBIT_BLOCK`, después de `:635`. Cambia "seis líneas" por "siete" en `:649`.

		# job_key=ingest:metrics:placements  BIDS 02 V.2 (spCampaigns por campaignPlacement)
		25 7 * * * FECHA=$(date -u -d "31 days ago" +\%F) FECHA_FIN=$(date -u -d "1 day ago" +\%F) && docker exec orbit-app-1 python -m app.cli ingest metrics --fecha "$FECHA" --fecha-fin "$FECHA_FIN" --placements >> /mnt/data/appdata/orbit/logs/ingest-placements.log 2>&1

#### Comprueba
- Pruebas del paso: `tests/test_placements.py tests/test_migracion_0062.py tests/test_placements_cron.py
  tests/test_reports_pipeline.py tests/test_ads_salud.py tests/test_ads_producto_ingesta.py tests/test_cli.py
  tests/test_schema_docs.py`. Cubren el cuerpo del reporte, el fallo aislado, los cuatro valores y la segunda
  corrida. El primer reporte real lo mide `checklist.sh` de D.2.

### P.1: construye la tabla por tipo de campaña
#### Qué vas a encontrar
- `v_hoja_activa` trae `tipo_campana` desde la 0060. Puede ser `NULL`. Ninguna vista ni ruta agrupa hoy
  por tipo de campaña. Sumar filas de campaña y filas de hoja duplica el gasto: esta tabla suma hojas.
- `v_metric_latest` es un `DISTINCT ON` sobre toda la tabla (`migrations/0001_initial.sql:1159-1162`). El
  veneno por métrica con `bool_and` está en `_SQL_CAMPANAS_30D` (`app/api_dashboard.py:165-178`). El target
  sale de `_target_margen_del_ciclo` (`:601`).
#### Pruebas primero
Crea `tests/test_pantalla_dinero.py`. Crea `tests/test_arq_bids_p.py` si no está en tu rama.
- Con hojas de los cinco tipos, salen cinco renglones y un total. La parte del gasto suma 100.
- La tabla de MX no trae el gasto de una hoja de US. Es el mutante obligatorio "suma MX con US": quita el
  filtro de plataforma. La hoja de una campaña `PAUSED` no cuenta. Es el mutante obligatorio "cuenta una
  campaña apagada": cambia `v_hoja_activa` por `ad_entity`.
- Un tipo con venta 0 da `acos_pct` `None` y la plantilla dice "sin ventas". Una métrica `NULL` da su suma
  en `None` y la plantilla pinta el guion. Es el mutante obligatorio "una métrica `None` se pinta como 0".
- Una hoja con `tipo_campana` `NULL` entra al total, no a los renglones, y se cuenta aparte. Con
  `hasta=2026-10-04` y `dias=90`, `desde` es 2026-07-06.
- Candado: ningún `app/pantalla_*.py` importa `app.ads.write` ni `app.apply`.
#### Cambios
1. Commit de contrato: crea `app/pantalla_dinero.py` con `TipoCampana`, `FilaTipo`, `PantallaDinero` y `lee_dinero`. No
   crees `por_ubicacion` ni `por_campana`: los agrega P.2a.
2. Agrega a `lee_dinero` el parámetro `hasta`, con ayer UTC por omisión. La ventana va de `hasta - dias` a
   `hasta`. Agrega a `PantallaDinero` el campo `hojas_sin_clasificar: int`.
3. Suma con un SELECT: `v_hoja_activa` unida a `v_metric_latest`, por plataforma, fechas y `tipo_campana`.
4. Commit de pantalla: agrega `GET /api/dashboard/donde-poner-el-dinero`, la ruta `/donde-poner-el-dinero` y
   `app/templates/donde_poner_el_dinero.html`. Pinta cinco renglones con gasto, parte del gasto, pedidos y
   ACoS, y el target al lado. Arriba pinta: "Solo cuenta lo que hoy está encendido. Lo que apagaste no
   aparece, así que el total no coincide con el Resumen."
#### Comprueba
- Las pruebas comunes y `tests/test_pantalla_dinero.py` pasan. Cubren lo activo, MX sin US y "sin ventas".
- Sobre la copia, `numeros.py` de `ejecucion/P.1/` da por tipo el mismo gasto, pedidos y venta que esta consulta de
  control, que parte de las tablas base. La fila con `tipo` vacío es el total. Si `sin_dato` es mayor que 0, el
  contrato da `None` en esa suma. Un `tipo` fuera de los cinco es una hoja sin clasificar.

		WITH hojas AS (
			SELECT k.id, CASE WHEN sc.targeting_type = 'AUTO' THEN 'automatica'
			                  WHEN k.kind = 'product_target' THEN 'product_targeting'
			                  ELSE lower(k.match_type) END AS tipo
			  FROM ad_entity k JOIN ad_entity ag ON ag.id = k.parent_id JOIN ad_entity c ON c.id = ag.parent_id
			  JOIN ad_entity_state sk ON sk.ad_entity_id = k.id AND sk.status = 'ENABLED'
			  JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id AND sg.status = 'ENABLED'
			  JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
			 WHERE k.platform = :'plataforma' AND k.kind IN ('keyword', 'product_target')
		), ultima AS (
			SELECT DISTINCT ON (m.ad_entity_id, m.metric_date) m.ad_entity_id, m.cost, m.orders, m.ad_revenue
			  FROM ads_metric_observation m JOIN hojas h ON h.id = m.ad_entity_id
			 WHERE m.metric_date BETWEEN :'desde' AND :'hasta'
			 ORDER BY m.ad_entity_id, m.metric_date, m.observed_at DESC)
		SELECT h.tipo, sum(u.cost) AS gasto, sum(u.orders) AS pedidos, sum(u.ad_revenue) AS venta,
		       count(*) - count(u.cost) AS sin_dato
		  FROM hojas h JOIN ultima u ON u.ad_entity_id = h.id GROUP BY ROLLUP (h.tipo) ORDER BY 1

- Como referencia, el 2026-10-09 y con `--hasta 2026-10-04`, el total de MX era 38,201 MXN, 274 pedidos y 13.3 % de
  ACoS. El de US era 3,286 USD, 96 pedidos y 31.6 % (`prototipos/RESULTADOS.md`).

### P.2a: escribe el contrato de las tablas por placement y por campaña y píntalas
#### Qué vas a encontrar
- V.1 dejó `v_campana_config_vigente` y V.2 dejó `ads_placement_observation`, con los cuatro valores de `Ubicacion`.
- `ads_placement_observation` no tiene vista de última observación: colapsa como `_SQL_ADS_VENTANA`
  (`app/fabrica_web.py:360-361`). El gasto diario de una campaña sale de sus filas en `v_metric_latest`,
  como en `_SQL_CAMPANAS_30D` (`app/api_dashboard.py:165-178`).
- La estrategia se guarda con el texto de Amazon. En vivo hay tres: `LEGACY_FOR_SALES`, `AUTO_FOR_SALES` y
  `MANUAL` (`pruebas/sonda_campanas.json`).
- La marca de "Fuera de Amazon" usa `gasto_para_concluir_desde_settings`, que construyó 0.b.
- Producción no tiene todavía las dos tablas: las crea D.2. Por eso este paso compara sobre la base de pruebas.
- P.1 dejó la ruta `/donde-poner-el-dinero`, `GET /api/dashboard/donde-poner-el-dinero`,
  `app/templates/donde_poner_el_dinero.html` y las tres entradas de `base.html`. Las dos tablas van en esa misma
  pantalla. Este paso no agrega ruta ni entrada de menú.
- Este paso no pinta botones ni avisos de campaña. Los botones dependen de V.3 y los avisos dependen de V.4: los
  agrega P.2b, en la sección 4.
#### Pruebas primero
Para el commit de contrato, extiende `tests/test_pantalla_dinero.py`.
- `lee_dinero` llena `por_ubicacion` y `por_campana`, solo con campañas `ENABLED` de ese mercado.
- "Fuera de Amazon" con gasto igual al gasto para concluir y 0 pedidos trae `gasta_sin_vender`. Con 1, no.
- `LEGACY_FOR_SALES` da `solo_hacia_abajo`, `AUTO_FOR_SALES` da `arriba_y_abajo`, `MANUAL` da `fija` y otro texto da
  `otra`. Una campaña sin configuración sale con presupuesto, uso y estrategia en `None`.
- La fila de campaña trae presupuesto diario, gasto medio diario, uso del presupuesto y sus ajustes por placement.
  Con 5 días de gasto en 90 % o más del presupuesto, `dias_al_tope_7d` es 5.
- Sobre la base sembrada de la prueba, `lee_ubicaciones` y `lee_campanas` dan lo mismo que las dos consultas de
  control de "Comprueba", escritas como literales en la prueba.

Para el commit de pantalla, extiende `tests/test_api_dashboard.py` y `tests/test_ui.py`.
- `GET /api/dashboard/donde-poner-el-dinero` trae `por_ubicacion` y `por_campana`. Con `plataforma=amazon_mx` no trae
  ninguna campaña de US.
- La página pinta `solo_hacia_abajo` como "solo hacia abajo", `arriba_y_abajo` como "hacia arriba y hacia abajo" y
  `fija` como "fija". Un `None` se pinta con el guion.
- La página pinta los cuatro placements en palabras, la frase de la fila que gasta sin vender y un ajuste de 40 en
  páginas de producto como "+40 % en páginas de producto". Una fila que no gasta sin vender no lleva la frase.
- Las dos tablas no traen ningún `<button>` ni `<form>`, y la página no pinta `FilaCampana.avisos`.
#### Cambios
1. Commit de contrato: agrega a `app/pantalla_dinero.py` `Ubicacion`, `EstrategiaPuja`, `FilaUbicacion` y
   `FilaCampana`, y a `PantallaDinero` los campos `por_ubicacion` y `por_campana`.
2. Agrega a `FilaCampana` el campo `dias_al_tope_7d: int | None`, que usa V.4. Un día cuenta al tope si su gasto es
   90 % o más del presupuesto vigente ese día. Un día anterior a la primera observación no cuenta.
3. Escribe los lectores `lee_ubicaciones` y `lee_campanas`, solo SELECT. El primero suma 30 días de
   `ads_placement_observation` por placement, sobre campañas `ENABLED` y con el colapso por `DISTINCT ON`. El
   segundo une `v_campana_config_vigente` con el gasto diario de la campaña.
4. Conecta los dos lectores a `lee_dinero`. Deja `FilaCampana.avisos` vacío: lo llena V.4. Incluye los dos campos
   nuevos en `PantallaDinero.como_dict()`: así la ruta de P.1 los entrega sin otro cambio.
5. Commit de pantalla: agrega dos tablas a `app/templates/donde_poner_el_dinero.html`, debajo de la tabla por tipo. No
   agregues ruta ni entrada de menú.
6. Pinta la tabla por placement con gasto, parte del gasto, pedidos, CPC, conversión y ACoS de 30 días, y
   marca la fila que gasta sin vender con su frase. La frase es la de `design.md`: "Fuera de Amazon: 1,735 MXN en
   30 días, 1,127 clics, ningún pedido." Escribe los cuatro placements como "arriba de búsqueda", "resto de búsqueda",
   "páginas de producto" y "fuera de Amazon".
7. Pinta la tabla por campaña con presupuesto diario, gasto medio diario, uso, estrategia en palabras,
   ajustes como "+40 % en páginas de producto" y gasto fuera de Amazon. No pintes las frases de `FilaCampana.avisos`:
   las pinta P.2b.
8. No pongas ningún `<button>` ni `<form>` en las dos tablas. Guarda la evidencia de la pantalla en `ejecucion/P.2a/`.
#### Comprueba
- Las pruebas comunes y `tests/test_pantalla_dinero.py` pasan. Cubren la marca de "Fuera de Amazon" y la fila de
  campaña con presupuesto, uso, estrategia y ajustes. Cubren también las dos tablas pintadas con sus frases, el guion
  de un dato `None` y la ausencia de botones.
- Las dos consultas de control son estas. La prueba las compara con las lecturas sobre la misma base:

		WITH ultima AS (
			SELECT DISTINCT ON (o.ad_entity_id, o.placement, o.metric_date) o.placement, o.cost, o.clicks, o.orders
			  FROM ads_placement_observation o
			  JOIN ad_entity_state s ON s.ad_entity_id = o.ad_entity_id AND s.status = 'ENABLED'
			 WHERE o.platform = :'plataforma' AND o.metric_date BETWEEN :'desde' AND :'hasta'
			 ORDER BY o.ad_entity_id, o.placement, o.metric_date, o.observed_at DESC)
		SELECT placement, sum(cost) AS gasto, sum(clicks) AS clics, sum(orders) AS pedidos FROM ultima GROUP BY 1

		SELECT c.id, v.presupuesto_diario, v.presupuesto_moneda, v.estrategia_puja,
		       v.ajuste_top_pct, v.ajuste_resto_pct, v.ajuste_producto_pct
		  FROM ad_entity c JOIN ad_entity_state s ON s.ad_entity_id = c.id AND s.status = 'ENABLED'
		  LEFT JOIN LATERAL (SELECT * FROM ads_campana_config_observation o WHERE o.ad_entity_id = c.id
		                      ORDER BY o.observed_at DESC LIMIT 1) v ON true
		 WHERE c.platform = :'plataforma' AND c.kind = 'campaign' ORDER BY 1

- Como referencia de `PRUEBAS.md`, del 2026-09-06 al 2026-10-06 MX gastó 1,735 MXN fuera de Amazon sin un pedido.

### V.4: manda los avisos diarios
#### Qué vas a encontrar
- Todo `notifica_*` devuelve un booleano y nunca levanta (`app/notifica.py:1-23`). `_envia_texto` devuelve
  `True` con el canal apagado (`:204-231`). `avisar_precio` lee, decide y envía (`:1521`).
- `/salud` arma un diccionario por plataforma. Un bloque roto da `None` (`app/api_dashboard.py:1055-1083`).
- P.2a dejó en `app/pantalla_dinero.py` `FilaUbicacion`, `FilaCampana` con `dias_al_tope_7d`, y los lectores
  `lee_ubicaciones` y `lee_campanas`. 0.b dejó `gasto_para_concluir_desde_settings`.
- El plan no le da migración a este paso. La marca de "ya salió hoy" usa `ingest_run`, que guarda `source`,
  `platform`, `started_at` y `ok` (`migrations/0001_initial.sql:62-73`, `migrations/0036_ingest_run_platform.sql`).
- El comando `jev-senales` es el modelo de un `main` propio con sus DSN (`app/jev_senales.py:747`), y su línea de
  cron suelta es el modelo de la tuya (`docs/DEPLOY.md:421`, `tests/test_jev_senales_cron.py`).
#### Pruebas primero
Crea `tests/test_avisos_campana.py` y `tests/test_avisos_campana_cron.py`. Extiende `tests/test_notifica.py`,
`tests/test_api_dashboard.py`, `tests/test_ui.py` y `tests/test_cli.py`.
- Un placement con gasto igual al gasto para concluir y 0 pedidos da `ubicacion_gasta_sin_vender`. Con 1
  pedido o con gasto `None`, no. Una campaña con 5 días al tope da `campana_sin_presupuesto`. Con 4, no. Un
  presupuesto mayor a 10 veces el gasto medio diario da `presupuesto_expuesto`. Con gasto `None` o 0, no.
- Ningún texto contiene `costo`, `margen` ni `target`. Los avisos de MX no traen filas de US.
- El comando corrido dos veces el mismo día manda cada mensaje una vez. Es el mutante obligatorio "sale dos veces
  el mismo día". Un mensaje sale por plataforma y clase.
- Con un Telegram que falla, el comando sigue con las demás clases y sale 0. La clase que falló sale en la corrida
  siguiente del mismo día.
- `/api/dashboard/salud` trae `avisos_campana` por plataforma. Si el bloque falla, trae `None`.
- La línea de cron de `docs/DEPLOY.md` es igual a la constante de `tests/test_avisos_campana_cron.py`.
#### Cambios
1. Crea `app/avisos_campana.py` con `Aviso` y `avisos_del_dia` de `bosquejo.py`. Agrega el parámetro
   `plataforma`. Es puro: agrega el candado de que no importa `psycopg`, `httpx` ni `app.notifica`.
2. Arma un mensaje por plataforma y clase, con una línea por caso y estas frases:
   - "Fuera de Amazon: 1,735 MXN en 30 días, 1,127 clics, ningún pedido."
   - "<campaña>: se queda sin presupuesto. Usó 90 % o más en 5 de los últimos 7 días."
   - "<campaña>: este presupuesto no limita. 5,354 MXN al día y gasta 43."
3. Escribe en `app/notifica.py` la función que llama a los dos lectores y a `avisos_del_dia`, y envía con
   `_envia_texto`.
4. Antes de enviar una clase, busca un `ingest_run` con `source = 'avisos_campana:<clase>'`, esa `platform`, `ok`
   verdadero y `started_at` del mismo día UTC. Si existe, no envíes. Después de un envío que devolvió `True`,
   inserta esa fila ya sellada. Lee con `ORBIT_DSN_READ` y marca con `ORBIT_DSN_INGEST`.
5. Si `app/cli_bids.py` no está en `origin/master`, créalo con `COMANDOS`, `registra(sub)` y
   `despacha(comando, rest)`. En `app/cli.py`, agrega solo el import, la llamada a `registra` y el despacho de
   `COMANDOS`: cuatro líneas. Registra `avisos-campana` en `app/cli_bids.py`, con su `main` ahí mismo.
   `wc -l app/cli.py` da 900 o menos.
6. En `lee_dinero`, llena `FilaCampana.avisos` con las frases de `avisos_del_dia`. Importa `avisos_del_dia` dentro
   de la función: `app/avisos_campana.py` importa los tipos de `app/pantalla_dinero.py`.
7. Agrega `avisos_campana` a cada plataforma en `salud`, con `None` si falla, y píntalo en `salud.html`.
8. En `docs/DEPLOY.md`, agrega la fila a la tabla de crons (`:380-387`) y esta línea suelta, con su constante en
   `tests/test_avisos_campana_cron.py`. Corre después del reporte por placement de las 07:25 UTC:

		5 8 * * * /usr/bin/flock -n /tmp/avisos-campana.lock docker exec orbit-app-1 python -m app.cli avisos-campana >> /mnt/data/appdata/orbit/logs/avisos-campana.log 2>&1

#### Comprueba
- Pruebas del paso: `tests/test_avisos_campana.py tests/test_avisos_campana_cron.py tests/test_notifica.py
  tests/test_pantalla_dinero.py tests/test_api_dashboard.py tests/test_ui.py tests/test_cli.py
  tests/test_arq_bids_v.py tests/test_architecture.py`. Cubren los tres avisos una vez por plataforma, clase y día,
  los textos sin costo, margen ni target, y el fallo del envío.

### D.2: despliega la configuración de campaña, los avisos y la pantalla de dinero
#### Qué vas a encontrar
- Lleva el PR de la sección 3: V.1, V.2, P.1, P.2a y V.4. Sus migraciones son la 0061 y la 0062. Aplica también la
  0060, de la sección 1, si ningún despliegue anterior la aplicó.
- Instala dos líneas de cron. La del reporte por placement va dentro del bloque de `docs/DEPLOY.md:628-640`, porque
  el instalador de ORBIT 03 borra toda línea con `app.cli ingest` y vuelve a escribir su bloque (`:643`). La de
  `avisos-campana` va suelta. Las dos están en `docs/DEPLOY.md` del SHA aprobado: las escribieron V.2 y V.4.
#### Pruebas primero
Corre `ensayo.sh`. Esperado: el esquema queda idéntico después de las reversas.
#### Cambios
1. Escribe los cuatro scripts de `ejecucion/D.2/` y la sección D.2 de `docs/DEPLOY.md`.
2. Haz que `desplegar.sh` lea las dos líneas de cron con `git show <sha>:docs/DEPLOY.md | grep -E
   '^[0-9].*(--placements|avisos-campana)'` y las instale como dice la guarda 5. El ancla `^[0-9]` deja fuera la fila
   de la tabla de crons de `docs/DEPLOY.md`, que también nombra los dos comandos.
3. Haz que `checklist.sh` salga 3 hasta que corren el sync de las 06:45 UTC y el reporte de las 07:25 UTC.
4. Haz que `checklist.sh` pida, para cada mercado, `GET /api/dashboard/donde-poner-el-dinero?plataforma=<mercado>` y
   la página `/donde-poner-el-dinero?plataforma=<mercado>`. El modelo es
   `docs/evidencia/jev-ads-02/ejecucion/S.5/checklist.sh:104-111`. Exige que `por_ubicacion` y `por_campana` traigan
   filas y que la página responda 200.
#### Comprueba
- `checklist.sh` sale 0, con `/health` en 200.
- Después del sync de las 06:45 UTC, ninguna campaña `ENABLED` queda sin fila en `v_campana_config_vigente`.
- Después de las 07:25 UTC, `ads_placement_observation` tiene filas.
- `/donde-poner-el-dinero` responde 200 en MX y en US.
- Después de las 07:25 UTC, `por_ubicacion` y `por_campana` traen filas en MX y en US: la pantalla muestra las tablas
  por placement y por campaña.

## Sección 4: ajustar la campaña

### Qué entrega
El dueño aplica y regresa ajustes de campaña desde la pantalla de dinero. La tabla por campaña, que la sección 3 ya
pinta, gana un botón por cada ajuste cuya sonda quedó sellada: limitar el gasto fuera de Amazon, cambiar un ajuste por
placement y cambiar un presupuesto diario. Cada fila de campaña muestra además sus avisos.

### Tareas, en orden
| Tarea | Qué hace | Qué necesita |
| --- | --- | --- |
| V.3 | Construye los ajustes del dueño, con la migración 0067 | V.0, V.1 y M.2 |
| P.2b | Agrega los botones de ajuste y los avisos a la tabla por campaña | P.2a, V.3 y V.4 |
| D.3 | Despliega la sección | V.3, P.2b y D.2 |

### Rama y PR
- Usa la rama `bids-02/s4-ajustar` y el árbol `~/dev/wt-bids-02-s4`. Créalos desde `origin/master` cuando la
  sección 3 está mergeada y desplegada y M.2, de la sección 2, está en `origin/master`. El PR de cierre de la sección
  1 también tiene que estar mergeado: V.3 lee `conclusion.md` de V.0.
- Haz un commit por tarea, en el orden de la tabla. El commit de D.3 trae sus scripts y la sección D.3 de
  `docs/DEPLOY.md`.
- D.3 corre en cuanto claw mergea el PR de código.
- El PR de cierre usa la rama `bids-02/s4-cierre`. Trae las salidas de D.3 y marca las tres filas.

### Archivos de la sección
- Propios: `app/campana_ajustes.py`, `app/static/js/dinero.js`, la migración 0067 con su reversa y
  `ejecucion/V.3/conclusion.md`.
- Qué construir está en la sección "Ver y ajustar la campaña" de `design.md` y en las secciones de `bosquejo.py` que
  nombra cada paso.
- Archivos que ya editaron las secciones 2 y 3: `app/apply.py`, `app/api_write.py`, `tests/test_api.py`,
  `tests/test_api_write.py`, `app/pantalla_dinero.py`, `app/ads/campana_config.py`,
  `app/templates/donde_poner_el_dinero.html` y `tests/test_arq_bids_v.py`.
- `app/templates/donde_poner_el_dinero.html` ya pinta las tablas por placement y por campaña: las dejó P.2a, en la
  sección 3. P.2b no las vuelve a pintar. Solo agrega los botones y los avisos de cada campaña.
- Compartidos con la sección 5, que puede seguir abierta: `app/ads/write.py`, `tests/test_ads_write.py`,
  `verify/Launch.md`, `verify/Doctor.md` y `docs/DEPLOY.md`. Compartido con la sección 6, si sigue abierta:
  `docs/DEPLOY.md`.

### Comprueba la sección completa
Corre esto una vez, sobre el último commit del PR de código:
- Las pruebas focalizadas de la sección, juntas.

		uv run --frozen python -m pytest -q -rs tests/test_campana_ajustes.py tests/test_migracion_0067.py \
			tests/test_ads_write.py tests/test_api.py tests/test_api_write.py tests/test_apply.py tests/test_lecturas_caso.py \
			tests/test_pantalla_dinero.py tests/test_api_dashboard.py tests/test_ui.py tests/test_ui_copy_campana.py \
			tests/test_schema_docs.py tests/test_arq_bids_v.py tests/test_arq_bids_p.py tests/test_architecture.py

  Esperado: todas terminan en `passed` y ningún salto dice "sin Postgres utilizable".
- `pre-commit run --all-files` sale 0.
- La batería completa, en CI: `gh workflow run quality.yml --ref bids-02/s4-ajustar`. Esperado: el job `completa`
  termina en `success`.
- `git diff --name-only origin/master | grep -E 'app/ads/write.py|ejecucion/V.3/conclusion.md'` lista los dos
  archivos.
- El despliegue, con los scripts de `ejecucion/D.3/`: `ensayo.sh` deja el esquema idéntico, con `v_cambio_bid` de dos
  ramas, y `checklist.sh <sello>` sale 0. En esa salida, la pantalla de dinero trae un botón por cada clase de
  `CLASES_SELLADAS`.

### V.3: construye los ajustes del dueño
#### Qué vas a encontrar
- `reversa_manual` (`app/apply.py:1636`) es el precedente de una escritura del dueño por `app/apply.py`, que ya
  construye el cliente de escritura (regla 15).
- `_mutate` rechaza un par que no esté en `MUTATION_REQUEST_TYPES` y envuelve el payload en una lista bajo
  `MUTATION_CONTAINERS[path]` (`app/ads/write.py:544-600`, `:126-135`). `list_sellado` es la puerta de
  readback (`:508`). Dos pruebas fijan la superficie y el allowlist (`tests/test_ads_write.py:156`, `:428`).
- Toda ruta POST de `app/api_write.py` declara `exige_token` antes de `ConexionEscritura`
  (`tests/test_api_write.py:255`). `SUPERFICIE_ADS_OPTIMIZER` lista las rutas exactas
  (`tests/test_api.py:594-606`). En la fábrica, la vista previa lee sin token
  (`app/api_fabrica.py:301-302`) y crear exige `huella` y un literal (`:194-197`, `:329-331`).
- La configuración vigente solo cambia con el sync de las 06:45 UTC. Sin una fila nueva, un segundo
  ajuste del mismo día se planearía sobre una lectura vieja.
- `conclusion.md` de V.0 dice qué sondas quedaron selladas. Construye solo los ajustes de una sonda sellada. Si
  ninguna de las sondas 2, 3 y 4 quedó sellada, detente y avisa al lead.
- La 0060 creó `v_cambio_bid` con dos ramas. `LecturasPlataforma.caso` (M.2) arma la `Trayectoria` desde esa vista.
#### Pruebas primero
Crea `tests/test_campana_ajustes.py` y `tests/test_migracion_0067.py`. Extiende `tests/test_ads_write.py`,
`tests/test_api.py`, `tests/test_api_write.py` y `tests/test_arq_bids_v.py`.
- `planea_ajuste` levanta `ValueError` con un ajuste que no cambia nada, con un porcentaje de 901 y con un
  presupuesto de 0. La huella cambia al cambiar cada campo del plan, uno por uno.
- Con una huella vieja, `aplica_ajuste_campana` levanta su error y el transporte no recibe nada. Es el
  mutante obligatorio "escribe con una huella vieja". Con un transporte que falla o un readback distinto de
  `plan.despues`, `confirmado_el` queda `NULL`. Es el mutante obligatorio "confirma la fila sin readback".
- La fila de `campana_ajuste` ya existe cuando el transporte recibe el PUT. El transporte recibe un solo
  `PUT /sp/campaigns`, con una sola campaña y sin la llave `state`. La misma huella aplicada dos veces devuelve el
  mismo `AjusteHecho` y no manda un segundo PUT.
- Con un `campana_ajuste` confirmado de clase `ajuste_ubicacion`, `v_cambio_bid` devuelve una fila por hoja de
  esa campaña con origen `ajuste_de_campana`, y `lee_plataforma` la pone en la `Trayectoria` de cada hoja. Con
  clase `presupuesto`, ninguna. Es el mutante obligatorio "no llega a la `Trayectoria`".
- `regresa_ajuste_campana` aplica la configuración completa de `antes` y deja una fila con `regresa_a`.
  La segunda vez levanta su error y no escribe. Una clase que V.0 dejó sin sellar se rechaza antes del
  HTTP. `ajustar_campana` rechaza toda llave fuera de la lista de `app/ads/campana_config.py`.
- Las dos rutas POST responden 401 sin token y no abren la conexión admin. La vista previa deja igual el
  conteo de `campana_ajuste`. Una huella vieja responde 409 con el plan nuevo. Otro literal responde 422.
- La 0067 rechaza un UPDATE de `campana_ajuste` fuera de `confirmado_el`. La reversa deja `v_cambio_bid` con las
  dos ramas de la 0060 y el esquema como estaba.
#### Cambios
1. Escribe `migrations/0067_bids02_campana_ajuste.sql` con la tabla `campana_ajuste` de `datos.sql` y la tercera
   rama de `v_cambio_bid`, con `CREATE OR REPLACE VIEW` y `decision_id` en `NULL`. No edites la 0060. La reversa
   restaura la vista de la 0060 antes de borrar la tabla.
2. Da a `app_admin` INSERT sobre `campana_ajuste`, UPDATE solo de `confirmado_el` e INSERT sobre
   `ads_campana_config_observation`. Sube en 1 el conteo de tablas de `verify/`.
3. Crea `app/campana_ajustes.py` con la sección `app/campana_ajustes.py` de `bosquejo.py`. Es puro: agrega
   el candado de que no importa `psycopg`, `httpx` ni `app.ads.write`. Agrega `CLASES_SELLADAS`, con las clases de
   ajuste cuya sonda selló V.0. `planea_ajuste` rechaza las demás.
4. En `app/ads/write.py`, agrega `("PUT", "/sp/campaigns")` a `MUTATION_REQUEST_TYPES` con el vendor de
   V.0, y `"/sp/campaigns": "campaigns"` a `MUTATION_CONTAINERS`. El comentario de la entrada cita la ruta y
   el SHA de la evidencia de V.0. Copia `conclusion.md` de V.0 a `ejecucion/V.3/`. Agrega un solo método público,
   `ajustar_campana`.
5. En `app/ads/campana_config.py`, escribe la función que arma el cuerpo del PUT desde `plan.despues`, con
   la forma que selló V.0, y la lista de llaves que un ajuste puede mandar: `budget`, `dynamicBidding` y
   `offAmazonSettings`. Cambia el texto JSON de `fuera_de_amazon` por el vocabulario de la sonda 4.
6. En `app/apply.py`, escribe `aplica_ajuste_campana`, `regresa_ajuste_campana` y `AjusteHecho`, en este
   orden: recalcula el plan con `config_vigente`, compara la huella y el literal, inserta la fila y haz
   commit, manda el PUT, lee con `list_sellado`, convierte con `config_de_payload` y compara con
   `plan.despues`. Si coincide, sella `confirmado_el` e inserta esa lectura en la tabla de configuración.
7. Usa `APLICAR AJUSTE` como literal de aplicar y `REGRESAR AJUSTE` como `go_literal` de la fila de regreso.
8. Agrega a `app/api_write.py` estas tres rutas y súmalas a `SUPERFICIE_ADS_OPTIMIZER`:
   - `GET /api/ads-optimizer/campana-ajuste/plan`, sin token y con `ConexionLectura`: `planea_ajuste`.
   - `POST /api/ads-optimizer/campana-ajuste/aplicar`, con token: `apply.aplica_ajuste_campana`.
   - `POST /api/ads-optimizer/campana-ajuste/{ajuste_id}/regresar`, con token: `regresa_ajuste_campana`.
9. No escribas ningún script que aplique un ajuste en Amazon: el primer ajuste lo aplica el dueño en la pantalla.
#### Comprueba
- Pruebas del paso: `tests/test_campana_ajustes.py tests/test_migracion_0067.py tests/test_ads_write.py
  tests/test_api.py tests/test_api_write.py tests/test_apply.py tests/test_lecturas_caso.py
  tests/test_schema_docs.py tests/test_arq_bids_v.py tests/test_architecture.py`. Cubren el rechazo del ajuste que
  no cambia nada, la fila antes del HTTP, el readback, el 409, la `Trayectoria` y el regreso completo.
- `git diff --name-only origin/master | grep -E 'app/ads/write.py|ejecucion/V.3/conclusion.md'` lista los dos
  archivos: la entrada del allowlist y su evidencia van en el mismo PR.

### P.2b: agrega los botones de ajuste y los avisos a la tabla por campaña
#### Qué vas a encontrar
- V.3 dejó las tres rutas `campana-ajuste`, el literal `APLICAR AJUSTE` y `CLASES_SELLADAS`. V.4 llena
  `FilaCampana.avisos` con `avisos_del_dia`. P.2b depende de V.4.
- `FilaCampana` no trae los ajustes que se pueden regresar.
- P.2a dejó pintadas las tablas por placement y por campaña en `app/templates/donde_poner_el_dinero.html`, sin botones
  y sin avisos. Este paso no las vuelve a pintar. Agrega a la tabla por campaña los botones y las frases de
  `FilaCampana.avisos`.
#### Pruebas primero
Extiende `tests/test_pantalla_dinero.py`, `tests/test_api_dashboard.py` y `tests/test_ui.py`.
- La página trae un botón por campaña por cada clase de `CLASES_SELLADAS`. Una clase fuera de esa lista no trae
  botón y su dato sí se pinta. Un ajuste confirmado y sin regreso trae su botón "Regresar".
- La fila de una campaña con avisos pinta cada frase de `FilaCampana.avisos`. La fila de una campaña sin avisos no
  pinta ninguna.
- En `tests/test_ui.py`, con Node: tocar un botón pide `campana-ajuste/plan` y no manda ningún POST. Sin el literal
  `APLICAR AJUSTE` escrito, el formulario no manda `campana-ajuste/aplicar`.
#### Cambios
1. Agrega a `FilaCampana` el campo `ajustes_regresables`: id y frase de cada ajuste confirmado sin regreso.
2. En la tabla por campaña que pintó P.2a, agrega a cada fila un botón por cada clase de `CLASES_SELLADAS`. Cada botón
   lleva su clase en `data-ajuste-clase`. No pongas botón a una clase fuera de esa lista. No cambies la tabla por
   placement ni las columnas que pintó P.2a.
3. Agrega a cada fila un botón "Regresar" por cada elemento de `ajustes_regresables`.
4. Pinta en cada fila de campaña las frases de `FilaCampana.avisos`.
5. Escribe `app/static/js/dinero.js`. Cada botón pide la vista previa y muestra `PlanAjuste.frase`. Solo
   entonces habilita el formulario con actor, token y el literal. No calcules el plan en JavaScript. El botón
   "Regresar" manda su POST a `campana-ajuste/{ajuste_id}/regresar`, con actor y token.
#### Comprueba
- Las pruebas comunes y `tests/test_pantalla_dinero.py` pasan. Cubren el botón que abre la vista previa sin
  escribir, la confirmación obligatoria y la clase sin sonda sellada, que no tiene botón. Cubren también el botón
  "Regresar" de un ajuste confirmado y los avisos de la fila de campaña.

### D.3: despliega los ajustes del dueño
#### Qué vas a encontrar
- Lleva el PR de la sección 4: V.3 y P.2b. Su migración es la 0067. No cambia el cron. Corre después de D.2,
  porque la sección 4 se construye sobre la sección 3.
- La vista previa es `GET /api/ads-optimizer/campana-ajuste/plan` y no pide token.
- La pantalla de dinero ya muestra las tablas por placement y por campaña desde D.2. Este despliegue le agrega los
  botones de ajuste, que P.2b marca con `data-ajuste-clase`.
#### Pruebas primero
Corre `ensayo.sh`. Esperado: el esquema queda idéntico después de las reversas, con `v_cambio_bid` de dos ramas.
#### Cambios
1. Escribe los cuatro scripts de `ejecucion/D.3/` y la sección D.3 de `docs/DEPLOY.md`.
2. Haz que `checklist.sh` pida la vista previa de un ajuste de una clase de `CLASES_SELLADAS` sobre una campaña
   `ENABLED`, y que imprima si `MUTATION_REQUEST_TYPES` trae `PUT /sp/campaigns`.
3. Haz que `checklist.sh` pida la página `/donde-poner-el-dinero?plataforma=<mercado>` de cada mercado. Exige en cada
   una al menos un botón `data-ajuste-clase` de cada clase de `CLASES_SELLADAS`.
#### Comprueba
- `checklist.sh` sale 0, con `/health` en 200.
- `/donde-poner-el-dinero` responde 200 en MX y en US, y trae un botón por cada clase de `CLASES_SELLADAS`.
- La vista previa responde 200 y `SELECT count(*) FROM campana_ajuste` da 0 antes y después de pedirla.
- `to_regclass('public.campana_ajuste')` no es NULL: la tabla existe y está vacía.

## Sección 5: impulso y estructura

### Qué entrega
El dueño impulsa un producto con dos campañas propias, una exact y una automática, con tope y veredicto. Ve la
pantalla de productos que venden y casi no reciben clics, con los impulsos en curso y su veredicto. Puede sacar un
producto de sus bolsas compartidas y regresarlo.

### Tareas, en orden
| Tarea | Qué hace | Qué necesita |
| --- | --- | --- |
| I.1 | Declara los roles del grupo y deja cosechar a un grupo de dos, con la migración 0064 | 0.a |
| I.2 | Escribe el plan, el tope y el veredicto, puros | I.1 |
| I.3 | Lanza y vigila el impulso, con la migración 0065 | I.1, I.2, 0.b y V.0 |
| I.4 | Saca un producto de sus bolsas compartidas y lo regresa, con la migración 0066 | I.3 y V.0 |
| I.5 | Abre las rutas del impulso | I.3 e I.4 |
| P.4 | Construye la pantalla de productos sin clics | I.5 y V.2 |
| E.1 | Agrega los dos botones de retiro | I.4 y P.4 |
| D.4 | Despliega la sección e instala la línea de cron de `impulso-vigia` | Todas las anteriores |

### Rama y PR
- Usa la rama `bids-02/s5-impulso` y el árbol `~/dev/wt-bids-02-s5`, creados desde `origin/master` con el PR
  de código de la sección 1 mergeado.
- Antes de I.3, rebasa sobre `origin/master` con el PR de cierre de la sección 1 mergeado: I.3 e I.4 leen
  `conclusion.md` de V.0.
- Antes de P.4, rebasa sobre `origin/master` con la sección 3 mergeada: P.4 lee la tabla de V.2.
- Haz un commit por tarea, en el orden de la tabla. P.4 lleva dos: contrato y pantalla. El commit de D.4 trae sus
  scripts y la sección D.4 de `docs/DEPLOY.md`.
- D.4 corre en cuanto claw mergea el PR de código.
- El PR de cierre usa la rama `bids-02/s5-cierre`. Trae las salidas de D.4 y marca las ocho filas.

### Archivos de la sección
- Propios: `app/impulso.py`, `app/impulso_io.py`, `app/retiro_anuncios.py`, `app/pantalla_productos.py` con su
  plantilla y su script, las migraciones 0064, 0065 y 0066 con sus reversas, y `tests/test_arq_bids_i.py`.
- Qué construir está en la decisión 4 de `design.md` y en las secciones `app/impulso.py`, `app/impulso_io.py` y
  `app/retiro_anuncios.py` de `bosquejo.py`. El bloque "0062 IMPULSO DE PRODUCTOS Y RETIRO DE ANUNCIOS" y la nota "LO
  QUE NO CAMBIA" de `datos.sql` traen el esquema de las tres migraciones.
- Solo esta sección edita `app/fabrica_plan.py`, `app/fabrica_web.py` y `app/apply_harvest.py`: los edita I.1. I.2 e
  I.3 llaman a `fabrica_plan` sin editarlo.
- `tools/fabrica_campanas.py`: I.1 recorre los roles del plan. I.3 agrega la pausa y la reanudación de un lote.
- `app/api_fabrica.py`: I.5 agrega las seis rutas del impulso y del retiro. I.1 y E.1 no lo editan.
- `app/impulso_io.py`: I.3 lo crea. I.4 llena `PlanImpulso.retiros` y agrega el paso 4 de `vigila`.
- `app/ads/archivar.py`: I.4 agrega la pausa y la reactivación de un product ad, si V.0 las selló.
- `app/pantalla_productos.py`, su plantilla y su script: P.4 los crea. E.1 agrega una acción a la plantilla y al
  script. E.1 edita también `app/templates/fabrica.html` y `app/static/js/fabrica.js`.
- Compartidos con las secciones 2, 3 y 6, que se construyen al mismo tiempo: `app/cycle.py`, `app/api_dashboard.py`,
  `app/ui.py`, `app/templates/base.html`, `tests/test_api_dashboard.py`, `tests/test_ui.py`,
  `tests/test_arq_bids_p.py`, `app/cli.py`, `app/cli_bids.py`, `tests/test_cli.py`, `verify/Launch.md`,
  `verify/Doctor.md`, `docs/DEPLOY.md` y `ejecucion/0.b/copia.sh`. Compartidos con la sección 4: `app/ads/write.py` y
  `tests/test_ads_write.py`.

### Comprueba la sección completa
Corre esto una vez, sobre el último commit del PR de código:
- Las pruebas focalizadas de la sección, juntas, con la prueba de Node corrida y no saltada.

		uv run --frozen python -m pytest -q -rs tests/test_fabrica_*.py tests/test_api_fabrica.py tests/test_ui_fabrica.py \
			tests/test_apply_harvest.py tests/test_migracion_0064.py tests/test_impulso.py tests/test_impulso_io.py \
			tests/test_migracion_0065.py tests/test_impulso_vigia_cron.py tests/test_retiro_anuncios.py \
			tests/test_migracion_0066.py tests/test_ads_archivar.py tests/test_ads_write.py tests/test_apply_schema.py \
			tests/test_api.py tests/test_cli.py tests/test_pantalla_productos.py tests/test_api_dashboard.py tests/test_ui.py \
			tests/test_ui_copy_campana.py tests/test_schema_docs.py tests/test_arq_bids_i.py tests/test_arq_bids_p.py \
			tests/test_architecture.py

  Esperado: todas terminan en `passed` y ningún salto dice "sin Postgres utilizable".
- Las pruebas de la fábrica pasan sin tocar aserciones: el conteo de líneas borradas de "Comprueba" de I.1 da `0`.
- `pre-commit run --all-files` sale 0.
- La batería completa, en CI: `gh workflow run quality.yml --ref bids-02/s5-impulso`. Esperado: el job `completa`
  termina en `success`.
- Sobre la copia de producción, cuando claw te lo encarga: los candidatos de `numeros.py` de P.4 son los `product_id`
  de su consulta de control.
- El despliegue, con los scripts de `ejecucion/D.4/`: `ensayo.sh` deja el esquema idéntico, con todo grupo existente
  en `fabrica5`, y `checklist.sh <sello>` sale 0.

### I.1: declara los roles del grupo y deja cosechar a un grupo de dos
#### Qué vas a encontrar
La base no exige cinco roles: `campana_grupo_rol` solo tiene la llave `(grupo_id, rol)`
(`migrations/0018_fabrica_campanas.sql:96-104`). Los cinco están fijos en estos sitios:

| Dónde | Qué fija | Qué hace I.1 |
| --- | --- | --- |
| `app/fabrica_plan.py:33-39` | `ROLES_ORDEN_CREACION`, los cinco en orden de creación | No cambia. Es el valor por omisión de `roles` |
| `app/fabrica_plan.py:354-389` | `valida_parametros` recorre los cinco (`:363`) | Recorre los roles del plan |
| `app/fabrica_plan.py:654-690` y `:699-702` | El JSON v2 no trae `roles`. El lector exige `schema_version == 2` | Escribe y lee `roles` solo cuando difiere de los cinco |
| `tools/fabrica_campanas.py:223` y `:1140-1144` | `_ESPERADO_ROLES = 5` y `_valida_go` | Compara `--esperado` con el número de roles del plan |
| `tools/fabrica_campanas.py:702`, `:1330`, `:1394`, `:1408` y `:1443` | Dry-run v2, ledger completo, `--registrar` y `_mutar` recorren los cinco | Recorren los roles del plan |
| `app/fabrica_web.py:162` | `_bids_como_json` recorre los cinco. Lo llama `detalle_lote` (`:599`) | Recorre los roles del plan, para que el detalle de un lote de dos roles se pueda leer |
| `app/apply_harvest.py:142-147` y `:1753-1779` | `_roster_hermanas` devuelve `entidad_incompleta` si falta un rol de `ROLES_DISCOVERY` (`:1775-1776`) | Devuelve un roster vacío para un grupo `impulso` |
| `app/fabrica_plan.py:952-984`. `tools/fabrica_campanas.py:467-493`, `:790-792` y `:816-821`. `app/api_fabrica.py:182-183` y `:197`. `app/fabrica_web.py:31-37`, `:84-88`, `:185`, `:222` y `:691`. `app/static/js/fabrica.js:7`, `:208`, `:363`, `:1153-1154`, `:1171` y `:1195`. `app/templates/fabrica.html:83-108`, `:154`, `:167` y `:173`. `docs/DEPLOY.md:168` | Dry-run v1, los cinco sufijos del CLI, la validación de la API, `args.esperado = 5`, las cinco filas de la pantalla y el literal `CREAR 5 CAMPAÑAS` | No cambia. La fábrica sigue creando grupos de cinco y `/crear` solo recibe planes de cinco roles |
| `tests/test_api_fabrica.py:106`, `:196` y `:411`. `tests/test_fabrica_campanas.py:749`, `:843`, `:1184`, `:1212`, `:1638` y `:2222`. `tests/test_fabrica_f2.py:289`. `tests/test_ui_fabrica.py:60` | Cinco campañas, cinco goals, cinco PUT y el literal | No las edites |

- Cuando el roster falla, `_paso_readback` revierte la keyword recién creada y cierra el job en `failed`
  (`app/apply_harvest.py:1814-1817`). Con un roster vacío, `_paso_hermanas` cierra en `done` sin ningún POST (`:2044-2060`).
- El destino del harvest es el ad group de la fila `category_exact` (`app/optimizer/harvest_destino.py:100-112`).
  `_registrar` busca esa fila con `next(...)` (`tools/fabrica_campanas.py:1173`): un plan sin exact revienta ahí.
- `app/fabrica_plan.py` y `app/apply_harvest.py` están en `ALLOWLIST_TAMANO` (`tests/test_architecture.py:65-81`). No
  los partas en este paso. El tool tiene una lista cerrada de imports (`:605-652`).
#### Pruebas primero
- En `tests/test_fabrica_plan.py`, al final: calcula en `origin/master` la huella de un plan v2 de cinco roles y
  fíjala como literal. Después del cambio, ese plan da la misma huella y su JSON no trae la llave `roles`.
- Un plan v2 con `roles=("category_exact", "auto_discovery")` valida con dos parámetros, trae `roles` en su JSON,
  sobrevive a `plan_v2_desde_json` y da pasos con `pasos_del_rol`. `_valida_plan_v2` rechaza un `roles` vacío, con un
  rol repetido o ajeno, sin `category_exact` o en otro orden que el de `ROLES_ORDEN_CREACION`.
- En `tests/test_fabrica_campanas.py`, al final: con un plan de dos roles y `--esperado 2`, `_mutar` crea 2 campañas,
  2 ad groups, 2 filas de rol y 2 goals. Con `--esperado 5` aborta sin ningún POST.
- Crea `tests/test_migracion_0064.py` con la lista `ORDEN` de `tests/test_fabrica_migracion.py:28-43` más la 0064. Un
  grupo que ya existía queda en `fabrica5`. Un grupo `impulso` rechaza `category_phrase`. Un `tipo` NULL o fuera de
  vocabulario se rechaza. La reversa deja el esquema como estaba.
- En `tests/test_fabrica_f2_hermanas.py`, con una variante de `_grupo_listo` (`:98`) y con `_corre_harvest_grupo`
  (`:111`): un harvest desde la automática de un grupo `impulso` termina en `done`, deja la keyword en el ad group de la
  exact, guarda `hermanas_objetivo == {}` y no manda ningún POST de negative de hermana ni a `/sp/keywords/delete`.
- Un grupo `fabrica5` sin uno de sus roles de descubrimiento sigue cerrando en `failed` con `entidad_incompleta`. Hoy
  ninguna prueba lo fija.
- Mutantes: el roster de hermanas de un grupo `impulso` trae campañas (obligatorio del plan), `valida_parametros`
  ignora `roles`, y el trigger admite un tercer rol.
#### Cambios
1. Crea `migrations/0064_bids02_grupo_tipo.sql` y su reversa. Lleva la columna `campana_grupo.tipo` y el trigger
   `campana_grupo_rol_roles_de_impulso` de la nota "LO QUE NO CAMBIA" de `datos.sql`. Sigue la regla 7.
2. En `app/fabrica_plan.py`, agrega al final de `PlanGrupoV2` (`:218-235`) el campo
   `roles: tuple[str, ...] = ROLES_ORDEN_CREACION`. Valida `roles` en `_valida_plan_v2`. Dale a `valida_parametros` el
   parámetro `roles`, con los cinco por omisión.
3. En `plan_v2_como_json`, escribe `"roles"` solo cuando `plan.roles != ROLES_ORDEN_CREACION`. En `plan_v2_desde_json`,
   lee `datos.get("roles", ROLES_ORDEN_CREACION)`. Conserva `schema_version: 2`.
4. En las cinco líneas del tool y en `app/fabrica_web.py:162`, recorre `plan.roles` cuando el plan es `PlanGrupoV2` y
   los cinco cuando es `PlanGrupo`.
5. En `_valida_go`, compara `args.esperado` con el número de roles del plan. Conserva los dos mensajes de hoy para un
   plan de cinco: la prueba de `tests/test_fabrica_campanas.py:1184` busca el texto `esperado 4`.
6. En `_registrar`, conserva el INSERT de hoy (`_SQL_INSERTA_GRUPO`, `:261-269`) para un plan de cinco roles. Para un
   plan con `roles == ("category_exact", "auto_discovery")`, usa un segundo INSERT que escribe `tipo = 'impulso'`.
7. En `_roster_hermanas`, lee `campana_grupo.tipo` del grupo. Si es `impulso`, devuelve `{}`. Con `fabrica5`, deja el
   camino de hoy sin cambios.
8. No toques `_paso_readback`, `ROLES_DISCOVERY`, `campana_grupo_rol` ni el literal `CREAR 5 CAMPAÑAS`. No agregues
   imports al tool.
#### Comprueba
- Pruebas del paso: `tests/test_fabrica_*.py tests/test_api_fabrica.py tests/test_ui_fabrica.py
  tests/test_apply_harvest.py tests/test_migracion_0064.py tests/test_schema_docs.py tests/test_architecture.py`.
  Cubren el harvest de un grupo `impulso` y el tercer rol que la base rechaza.
- Las pruebas de la fábrica pasan sin tocar aserciones. Este comando cuenta las líneas borradas en ellas y da `0`:

		git diff origin/master -- 'tests/test_fabrica_*.py' tests/test_api_fabrica.py tests/test_ui_fabrica.py \
			| grep -c '^-[^-]'

### I.2: escribe el plan, el tope y el veredicto puros
#### Qué vas a encontrar
- `fabrica_plan.pasos_del_rol` (`app/fabrica_plan.py:870-904`) arma campaña, ad group, un product ad por publicación y
  las semillas del rol. Antes valida el plan entero con `_valida_plan_v2` (`:872-873`), que exige un `objetivo`
  (`:617-636`). `PlanImpulso` no trae objetivo en el bosquejo.
- Un objetivo de origen `margen_medido` deja los goals con target NULL, y el motor lo resuelve por margen
  (`app/fabrica_plan.py:240-251`). Es lo que pide el plan: un impulso no lleva target propio.
- `_payload_campana` (`:789-798`) ya manda `dynamicBidding: {"strategy": "LEGACY_FOR_SALES"}`, que es "solo hacia
  abajo", y no manda `placementBidding`. El presupuesto sale de `plan.parametros[rol].budget` (`:795`).
- `valida_parametros` rechaza un presupuesto menor que el bid (`:372-373`). La mitad del presupuesto diario del
  impulso va a cada campaña: con el tope por omisión, cada campaña lleva 12.50 MXN o 1.29 USD al día. Un bid sugerido
  puede ser mayor que ese presupuesto. El mínimo diario que Amazon acepta es `unknown` hasta que V.0 lo mida.
- I.1 dejó `roles` en `PlanGrupoV2` y en `valida_parametros`. Este paso los usa: por eso depende de I.1.
#### Pruebas primero
Crea `tests/test_impulso.py`, sin base:
- Un tope de 350 MXN da `presupuesto_diario == Decimal("25")`. Uno de 36 USD da `Decimal("36") / 14`.
- Con un mínimo de 20 MXN por campaña y un tope de 350, el presupuesto diario es 40 y el veredicto pausa con un
  gasto de 270. Con el mínimo en `None`, el presupuesto diario es el tope entre 14.
- Una prueba por rama de `veredicto_impulso`, en el orden del bosquejo y con valores literales. Los cuatro veredictos
  finales tienen su caso. Un pedido gana al tope agotado.
- Con tope 350 y presupuesto diario 25, un gasto de 299.99 sigue `en_curso` y uno de 300 da `clics_sin_ventas`, final,
  `pausar`. Mata al mutante obligatorio "el vigía pausa cuando falta un solo presupuesto diario".
- Con `None` en impresiones, clics, gasto o pedidos, el veredicto es `en_curso`, no final y `seguir`, aunque el gasto
  pase del tope y el plazo haya vencido. Mata al mutante obligatorio "un veredicto final con un dato `None`".
- La huella de `PlanImpulso` cambia al cambiar cada campo, uno por uno. `como_json` lleva el dinero como string.
- `pasos_del_impulso` devuelve primero los pasos de `category_exact`. La campaña de cada rol lleva la mitad del
  presupuesto diario, la estrategia `LEGACY_FOR_SALES` y ninguna llave `placementBidding`.
- Con un presupuesto diario de 12.50 MXN por campaña, un bid sugerido de 15 queda en 12.50 y su rol sale en
  `bids_acotados`. Un bid de 10 queda en 10 y su rol no sale. `pasos_del_impulso` no levanta `PlanInvalido` por un
  presupuesto menor que el bid.
- En `tests/test_arq_bids_i.py`: `app/impulso.py` no importa nada de `PROHIBIDOS_MOTOR`, con una fuga sembrada. Copia
  `test_fabrica_plan_es_puro` (`tests/test_architecture.py:831-834`).
#### Cambios
1. Crea `app/impulso.py` con la sección `app/impulso.py` de `bosquejo.py`, completa.
2. Agrega a `PlanImpulso` el campo `objetivo: fp.ObjetivoPlanV2`, siempre de origen `margen_medido`. Inclúyelo en
   `como_json` y en la huella.
3. Agrega `PRESUPUESTO_MINIMO_CAMPANA`, un mapa de moneda a presupuesto diario mínimo por campaña, con `None` en
   las dos monedas. Con un mínimo, el presupuesto diario del impulso es el mayor entre el tope entre 14 y dos veces
   el mínimo. El valor medido lo escribe I.3 o I.4.
4. En `pasos_del_impulso`, arma un `fp.PlanGrupoV2` con `roles=ROLES_IMPULSO`, una publicación, el objetivo del plan y
   un `ParametrosRol` por rol con la mitad de `tope.presupuesto_diario`. Llama a `fp.pasos_del_rol` por cada rol.
5. Escribe la función pura que acota cada bid sugerido al presupuesto diario de su campaña. Agrega a `PlanImpulso` el
   campo `bids_acotados: tuple[str, ...]`, con los roles cuyo bid bajó. Inclúyelo en `como_json` y en la huella.
6. No redondees `presupuesto_diario` en `TopeAprendizaje`. El redondeo a centavos lo hace `monto_wire`
   (`app/fabrica_plan.py:391-396`) al armar el payload. No leas el reloj: la fecha llega en `PlanImpulso.fecha`.
#### Comprueba
- Pruebas del paso, sin Postgres: `tests/test_impulso.py tests/test_arq_bids_i.py tests/test_architecture.py`. Cubren
  el tope entre 14, la pausa a dos presupuestos diarios del tope, el dato `None`, los cuatro veredictos finales,
  la huella y el bid acotado al presupuesto diario de su campaña.

### I.3: lanza y vigila el impulso
#### Qué vas a encontrar
- `app/fabrica_web.py` importa el tool (`:29`) y llama `fc._mutar` (`:695`), `fc._inserta_lote` y `fc._sella_lote`
  (`:700-701`). `impulso_io` entra por la misma puerta. `crear` bloquea el lote (`:646-655`), devuelve el lote si ya
  existe (`:676-678`) y responde 409 si la huella cambió (`:687-688`).
- `fc._datos_plan_v2` (`tools/fabrica_campanas.py:601-654`) devuelve publicaciones, objetivo, semillas y campañas
  existentes. Su objetivo `margen_medido` exige margen positivo. Si falta, levanta `PlanInvalido`
  (`app/fabrica_plan.py:344-351`).
- `_registrar` crea los goals con `mode=plan.modo` (`tools/fabrica_campanas.py:1239-1249`). Para pasar un goal a
  `live` existe `goals_write.edita_goal(conn, goal_id, mode="live", updated_at=...)` (`app/goals_write.py:329-345`),
  que exige el rol `category_exact` en el grupo (`:143-182`).
- `_put_estado_campana` acepta cualquier estado (`tools/fabrica_campanas.py:1491-1506`). `_pausa_una` apaga además el
  goal (`:1509-1534`). `_desarmar` sella el lote en `desarmado`, que es terminal (`:1584` y
  `app/fabrica_web.py:714-717`), y no deja fila en `fabrica_lote_paso` antes del PUT.
- Las métricas con grano de campaña están en `v_metric_latest` (`app/api_dashboard.py:139-173`). El último día con
  ingesta de una plataforma es `max(metric_date)` de esa vista (`migrations/0013_entidad_inerte.sql:21-22`).
- I.3 depende de V.0 por un solo resultado: el presupuesto diario mínimo de cada país. Tómalo de
  `ejecucion/V.0/conclusion.md`, que llega con el PR de cierre de la sección 1. Si la sonda 2 quedó "sin sellar", usa
  el sustituto de la tabla de decisiones del plan.
- Un impulso nace con la configuración de gasto fuera de Amazon por defecto de Amazon. No mandes `offAmazonSettings`
  en el payload de la campaña.
#### Pruebas primero
Crea `tests/test_impulso_io.py`, con base y con `httpx.MockTransport`:
- `previsualiza` deja iguales los conteos de `fabrica_lote`, `impulso` e `impulso_lectura`, y no manda ninguna
  mutación a Amazon. `lanza` con una huella vieja, o sin la `confirmacion_esperada`, no crea ningún lote.
- Con tres productos y el segundo fallando, el primero y el tercero quedan con su fila en `impulso` y su lote en
  `applied`. `lanza` dos veces con la misma huella crea una sola vez. Un producto con un impulso vivo no recibe otro.
- Los goals del grupo nacen en `shadow`. El `request_payload` de cada campaña trae `LEGACY_FOR_SALES` y no trae
  `placementBidding` ni `offAmazonSettings`.
- La vista previa nombra cada rol cuyo bid bajó al presupuesto diario de su campaña.
- `vigila` dos veces con el mismo `hoy` deja una sola fila por impulso y llama al pausador una sola vez. Sin impulsos
  devuelve una lista vacía, y el comando sale 0.
- Con veredicto `pausar`, `vigila` llama `pausador.pausa(lote)`, guarda `hecho = 'pausado'` y deja el lote `pausado`.
  Cada PUT deja antes su fila en `fabrica_lote_paso`. Con `vende`, pasa los goals a `live` y guarda `graduado`.
- Un día con ingesta y sin fila de la campaña cuenta como cero. Una métrica NULL llega como `None`: `en_curso`.
- Crea `tests/test_migracion_0065.py`: la moneda del tope es la de la plataforma, `en_curso` no puede ser final,
  `fabrica_lote` acepta `pausado` y nadie puede hacer UPDATE ni DELETE sobre las dos tablas.
- Crea `tests/test_impulso_vigia_cron.py` como copia de `tests/test_jev_senales_cron.py`, con la línea del cambio 10.
#### Cambios
1. Escribe `migrations/0065_bids02_impulso.sql` y su reversa con `impulso`, `impulso_lectura`, `v_impulso_estado`, el
   trigger de moneda y de listing, y el CHECK de `fabrica_lote.estado` con `pausado`. Hoy ese CHECK va en línea
   (`migrations/0018_fabrica_campanas.sql:35-36`). Su nombre es `unknown`: léelo con `\d fabrica_lote`. La reversa
   aborta si `impulso` tiene una fila.
2. Sigue la regla 7. Da INSERT y SELECT a `app_admin`, que es con quien escribe la fábrica
   (`0018_fabrica_campanas.sql:441-452`), y SELECT a `app_read`. Sube en 2 las tablas y en 1 las vistas de `verify/`.
3. Crea `app/impulso_io.py` con la sección `app/impulso_io.py` de `bosquejo.py`.
4. En `previsualiza`, arma un `PlanImpulso` por producto. Toma publicaciones, objetivo y semillas de
   `fc._datos_plan_v2`, los bids de `app/fabrica_bids.py` y el tope de `gasto_para_concluir_desde_settings`. Pasa los
   bids por la función de I.2 que los acota, y haz que `VistaPreviaImpulso.como_dict` traiga `bids_acotados` de cada
   plan.
5. Si `_datos_plan_v2` levanta `PlanInvalido` para un producto, déjalo en la vista previa como no lanzable, con ese
   motivo. No inventes un objetivo. Deja `PlanImpulso.retiros` vacío en este paso: lo llena I.4.
6. En `lanza`, sigue el orden de `fabrica_web.crear`: bloquea, relee, compara la huella y crea un lote por producto
   con `fc._mutar`, con `modo="shadow"` y `esperado=2`. Toma el candado por listing que describe `datos.sql`. Inserta
   la fila de `impulso` después de que su lote queda `applied`.
7. Si `conclusion.md` trae el presupuesto diario mínimo, escríbelo en `PRESUPUESTO_MINIMO_CAMPANA`. Si no, déjalo en
   `None` y anótalo en el PR.
8. Escribe en `tools/fabrica_campanas.py` la pausa y la reanudación de un lote sin desarmarlo: fila en
   `fabrica_lote_paso` antes del PUT, `_put_estado_campana`, readback y estado `pausado` o `applied`. No reuses
   `_pausa_una`: apagaría el goal. `impulso_io` envuelve esas dos funciones en un `PausadorDeCampanas`.
9. En `vigila`, haz los pasos 1 a 3 del bosquejo. El paso 4 es de I.4. Gradúa con `goals_write.edita_goal`.
10. Registra el comando `impulso-vigia` en `app/cli_bids.py`, con su `main` ahí mismo. Si `app/cli_bids.py` no está
    en `origin/master`, créalo como dice el cambio 5 de V.4. `wc -l app/cli.py app/cli_bids.py app/impulso_io.py`
    da 900 o menos en cada archivo. Agrega a `docs/DEPLOY.md` la fila de la tabla de crons
    (`:380-387`) y esta línea suelta, como la de `jev-senales` (`:421`):

		20 8 * * * /usr/bin/flock -n /tmp/impulso-vigia.lock docker exec orbit-app-1 python -m app.cli impulso-vigia >> /mnt/data/appdata/orbit/logs/impulso-vigia.log 2>&1

#### Comprueba
- Pruebas del paso: `tests/test_impulso_io.py tests/test_migracion_0065.py tests/test_impulso_vigia_cron.py
  tests/test_fabrica_campanas.py tests/test_schema_docs.py tests/test_cli.py tests/test_architecture.py`. Cubren la
  vista previa sin escritura, el producto que falla, la estrategia y los ajustes de nacimiento, el payload sin
  `offAmazonSettings`, el vigía corrido dos veces y los goals de `shadow` a `live`.

### I.4: saca un producto de sus bolsas compartidas y regrésalo
#### Qué vas a encontrar
- `archivar.archivar_anuncios(escritor, ad_ids, *, ejecutar)` lee, archiva uno por uno y relee
  (`app/ads/archivar.py:319-327`). Cada resultado trae su línea de reversa (`:59-73` y `:184`). Un anuncio ya archivado
  sale como `ya_estaba` (`:76-85`). `archivar.reponer_anuncios(escritor, lineas, *, ejecutar)` recrea el anuncio desde
  esas líneas, con otro `adId` (`:236-244`).
- `archivar.preparar_escritor(platform)` construye el cliente de escritura (`:88-131`). El retiro escribe por
  `app/ads/archivar.py` (regla 15).
- `tests/test_apply_schema.py:949` toma la última migración que contiene el texto `motivo IN (` y exige que su lista
  sea `MOTIVOS_SIN_APLICAR`. El bloque de `datos.sql` escribe el CHECK de `anuncio_retiro` con ese texto.
- El encabezado de `datos.sql` pide en `anuncio_retiro` la columna `modo`, con `pausa` o `archivo`. El cuerpo del
  bloque no la trae, y `anuncio_reposicion.ad_id_nuevo` es obligatorio.
- El bloque de `datos.sql` declara `UNIQUE (ad_entity_id)` en `anuncio_retiro`. La unicidad que pide el plan es otra:
  un retiro abierto por anuncio. Un retiro está abierto mientras no tiene fila en `anuncio_reposicion`.
- `tests/test_ads_write.py` sella la superficie pública del cliente de escritura y el allowlist (`:156`, `:428`).
#### Pruebas primero
Crea `tests/test_retiro_anuncios.py` y `tests/test_migracion_0066.py`. Extiende `tests/test_ads_write.py`:
- `planea_retiro` lista cada anuncio `ENABLED` de los listings pedidos y deja iguales los conteos de `anuncio_retiro`
  y `anuncio_reposicion`. Con `conservar_grupo_id`, no lista los anuncios de los ad groups de ese grupo. La huella
  cambia si cambia un anuncio.
- `ejecuta_retiro` dos veces deja una fila por anuncio y manda una sola mutación por anuncio.
- La 0066 rechaza un segundo retiro de un anuncio que tiene un retiro abierto. Después de su fila en
  `anuncio_reposicion`, acepta un retiro nuevo del mismo anuncio.
- En `tests/test_ads_write.py`, con la sonda 1 sellada: la superficie pública gana el método de `PUT /sp/productAds` y
  el allowlist gana esa entrada, y nada más. Sin sellar, las dos pruebas pasan sin cambios.
- `repone` deja al producto con un anuncio en cada ad group del que salió, con el estado que tenía, y deja una fila
  en `anuncio_reposicion`. Una segunda llamada no repone otra vez.
- Con la sonda sellada, el retiro pausa, guarda `modo = 'pausa'` y la reposición reactiva el mismo `adId`. Sin
  sellar, archiva, guarda `modo = 'archivo'` y repone. La 0066 rechaza otro `modo`.
- El vigía no retira nada mientras la lectura del impulso tiene 0 impresiones o `None`. Con más de 0, retira una vez
  y guarda `retiro_de_bolsas`. Mata al mutante obligatorio "el retiro saca un anuncio antes de las impresiones".
- Una campaña cuyo único product ad de otra familia está `ARCHIVED` conserva su familia.
- En `tests/test_arq_bids_i.py`: `app/retiro_anuncios.py` no importa `app.ads.write`.
#### Cambios
1. Escribe `migrations/0066_bids02_retiro_anuncios.sql` y su reversa con `anuncio_retiro`, `anuncio_reposicion` y
   `v_anuncio_retirado`. Agrega a `anuncio_retiro` la columna `modo TEXT NOT NULL`, con `pausa` o `archivo`. Escribe
   el CHECK del motivo como `motivo = ANY (ARRAY['impulso', 'sin_pedidos', 'subfamilia'])`. No escribas `motivo IN (`
   en la migración, ni en un comentario: `git grep -n 'motivo IN (' -- migrations/0066_bids02_retiro_anuncios.sql`
   sale vacío. No copies el `UNIQUE (ad_entity_id)` de `datos.sql`. Escribe un trigger que rechaza un retiro nuevo
   de un anuncio que tiene un retiro sin fila en `anuncio_reposicion`. La reversa aborta si `anuncio_retiro` tiene
   una fila. Sube en 2 el conteo de tablas de `verify/` y en 1 el de vistas.
2. Crea `app/retiro_anuncios.py` con `planea_retiro`, `ejecuta_retiro` y `repone` del bosquejo. Obtén el escritor con
   `archivar.preparar_escritor`.
3. Lee en `docs/evidencia/bids-02/ejecucion/V.0/conclusion.md` la conclusión de la sonda 1. Si quedó "sin sellar",
   `ejecuta_retiro` llama a `archivar_anuncios` y `repone` llama a `reponer_anuncios` con la línea guardada.
4. Si la sonda quedó sellada, agrega `("PUT", "/sp/productAds")` a `MUTATION_REQUEST_TYPES`, donde hoy no está
   (`app/ads/write.py:103-117`), con el tipo de contenido y el cuerpo que la sonda registró. Escribe el método en
   `AdsWriteClient` y la pausa y la reactivación en `app/ads/archivar.py`, con lectura previa y readback. Copia a
   `ejecucion/I.4/` la salida literal de la sonda.
5. En `repone`, lee el estado actual del anuncio en Amazon. Si está `PAUSED`, reactívalo y guarda su mismo `adId` en
   `ad_id_nuevo`. Si está `ARCHIVED`, repón con `reponer_anuncios`.
6. En `impulso_io.previsualiza`, llena `PlanImpulso.retiros` con `planea_retiro`. En `vigila`, agrega el paso 4.
7. `_SQL_PAREJAS_CAMPANA_FAMILIA` cuenta product ads sin filtrar estado (`app/cycle.py:468-478`). Cuenta solo los que
   están en `ENABLED` o `PAUSED`, como pide la nota de `datos.sql`.
8. Deja fuera del plan de retiro los anuncios sin `listing_id`. No los busques por ASIN en este paso.
9. Si I.3 dejó sin tomar de V.0 el presupuesto diario mínimo, tómalo ahora.
#### Comprueba
- Pruebas del paso: `tests/test_retiro_anuncios.py tests/test_impulso_io.py tests/test_migracion_0066.py
  tests/test_ads_archivar.py tests/test_ads_write.py tests/test_apply_schema.py tests/test_schema_docs.py
  tests/test_architecture.py`. Cubren el plan que no escribe, el retiro después de las impresiones, la columna `modo`,
  el regreso a los mismos ad groups, el retiro abierto que no se repite y el retiro nuevo de un anuncio ya repuesto.

### I.5: abre las rutas del impulso
#### Qué vas a encontrar
- El router es `APIRouter(prefix="/api/fabrica")` con la clase `_RutaFabrica` (`app/api_fabrica.py:60`). Los cuerpos
  heredan de `_Cuerpo`, que prohíbe campos extra (`:67-68`).
- `/crear` recibe el token y no recibe conexión (`:329-331`). La conexión admin se abre dentro de
  `fabrica_web.crear`. `test_auth_antes_de_conectar_admin` lo fija (`tests/test_api_fabrica.py:394-413`).
- `exige_token` lee solo el header `x-orbit-token` y responde 401 (`app/api_write.py:109-120`). `/plan` usa
  `ConexionLectura` (`app/api_fabrica.py:301-303`). `/lotes/{lote}/{accion}` compara un literal y responde 422 (`:347-356`).
- El bosquejo solo nombra `POST /api/fabrica/impulso/lanzar`, y el diseño solo fija el literal
  `LANZAR IMPULSO DE <N> PRODUCTOS`. Las demás rutas y literales los fija esta tabla:

| Ruta | Llama a | Token | Confirmación literal |
| --- | --- | --- | --- |
| `POST /api/fabrica/impulso/plan` | `impulso_io.previsualiza` | No | No |
| `POST /api/fabrica/impulso/lanzar` | `impulso_io.lanza` | Sí | `LANZAR IMPULSO DE <N> PRODUCTOS` |
| `POST /api/fabrica/impulso/{impulso_id}/pausar` | `PausadorDeCampanas.pausa` | Sí | `PAUSAR IMPULSO` |
| `POST /api/fabrica/retiro/plan` | `retiro_anuncios.planea_retiro` | No | No |
| `POST /api/fabrica/retiro/ejecutar` | `retiro_anuncios.ejecuta_retiro` | Sí | `SACAR <N> ANUNCIOS` |
| `POST /api/fabrica/retiro/reponer` | `retiro_anuncios.repone` | Sí | `REGRESAR <N> ANUNCIOS` |

#### Pruebas primero
Extiende `tests/test_api_fabrica.py` con casos nuevos al final:
- Las cuatro rutas con token responden 401 sin el header y con el token en la query. La conexión admin no se abre:
  sustituye `connect` por una función que falla, como en la prueba de `:394-413`.
- Las dos rutas `plan` responden 200 y dejan iguales los conteos de `fabrica_lote`, `impulso` y `anuncio_retiro`.
- `lanzar` con una huella vieja responde 409 y el detalle trae la vista previa nueva. No crea ningún lote.
- `lanzar`, `ejecutar` y `reponer` responden 422 con un literal distinto, también cuando solo cambia N. Un campo extra
  en el cuerpo responde 422 y la respuesta no repite el valor recibido.
#### Cambios
1. Agrega las seis rutas a `app/api_fabrica.py`, con sus cuerpos como modelos `_Cuerpo`. `CuerpoLanzarImpulso` está en
   la sección `app/api_write.py y app/api_fabrica.py` de `bosquejo.py`.
2. En cada ruta con token, pon `exige_token` como primera dependencia y abre la conexión admin dentro de la función
   de `impulso_io` o de `retiro_anuncios`.
3. Compara el literal antes de escribir. Calcula N con el plan releído en el servidor. No aceptes N del cliente.
4. `retiro/plan` y `retiro/ejecutar` reciben `listing_ids`, `motivo` y, opcional, `lote`. Con `lote`, resuelve
   `conservar_grupo_id` en el servidor: `campana_grupo.lote` es único (`migrations/0018_fabrica_campanas.sql:82`).
   `retiro/ejecutar` recibe la huella del plan y responde 409 si cambió.
#### Comprueba
- Pruebas del paso: `tests/test_api_fabrica.py tests/test_api.py tests/test_architecture.py`. Cubren el 401 antes de
  abrir la conexión admin, la vista previa sin escritura, el 409 con el plan nuevo y el 422 sin el literal.

### P.4: construye la pantalla de productos sin clics
#### Qué vas a encontrar
- Un producto tiene de 2 a 4 publicaciones: sumar por publicación duplica. El ledger de `amazon_us` está
  en MXN: cuenta los pedidos en unidades, como `_SQL_PRODUCTOS` (`app/familias.py:237-257`).
- `ads_product_metric_observation` se colapsa a mano (`app/fabrica_web.py:360-361`). Sin filas no hay cero.
- I.2 dejó `LecturaImpulso`, `VeredictoImpulso` y `TopeAprendizaje`. I.3 dejó `v_impulso_estado`. I.5 dejó
  `impulso/plan`, `impulso/lanzar` y `retiro/reponer` bajo `/api/fabrica`. Nadie construye "Darle otro tope".
- Un impulso nace con la configuración de gasto fuera de Amazon por defecto de Amazon, así que su veredicto puede
  incluir clics de fuera de Amazon. La pantalla dice cuántos. Ese dato vive en `ads_placement_observation`, de V.2,
  que es de la sección 3. Antes de este paso, rebasa sobre `origin/master` con la sección 3 mergeada.
#### Pruebas primero
Crea `tests/test_pantalla_productos.py`. Crea `tests/test_arq_bids_p.py` si no está en tu rama, con el candado de
P.1.
- Un producto con anuncio activo, 2 pedidos en 90 días y 19 clics en 60 días es candidato. Con 1 pedido,
  con 20 clics o sin anuncio activo, no. Un producto con dos publicaciones cuenta una vez.
- Un producto sin filas de ads trae `clics_ads_60d` `None` y la plantilla pinta el guion.
- Un impulso en curso trae día, impresiones, clics, gasto y veredicto. Sin lectura, `lectura` es `None`.
- Un impulso con 3 clics de placement `fuera_de_amazon` en sus dos campañas desde su lanzamiento trae
  `clics_fuera_de_amazon` en 3. Sin filas de placement de sus campañas trae `None`, y la plantilla pinta el guion.
- La sección de 11 meses trae dos acciones. "Impulsar aparte" pide `impulso/plan` con los productos marcados y exige
  el literal `LANZAR IMPULSO DE <N> PRODUCTOS`.
- `anuncios_sin_producto` cuenta los product ads activos sin publicación. No sale "Darle otro tope".
#### Cambios
1. Commit de contrato: crea `app/pantalla_productos.py` con `ProductoCandidato`, `ImpulsoVisto`,
   `PantallaProductos` y `lee_productos`. Tres meses son 90 días. Un anuncio activo es un product ad `ENABLED` en
   un ad group y una campaña `ENABLED`. Agrega a `ImpulsoVisto` el campo `clics_fuera_de_amazon: int | None`: la
   suma de clics de placement `fuera_de_amazon` de las dos campañas del impulso desde su lanzamiento, leída de
   `ads_placement_observation` con el colapso por `DISTINCT ON`.
2. Commit de pantalla: agrega `GET /api/dashboard/productos-sin-clics`, la ruta `/productos-sin-clics`,
   `app/templates/productos_sin_clics.html` y `app/static/js/productos.js`.
3. En candidatos, pon una casilla y el campo de subfamilia por producto, y el botón "Ver lanzamiento". La
   vista previa sale de `impulso/plan`. El literal es `LANZAR IMPULSO DE <N> PRODUCTOS`.
4. En impulsos, pinta "Día 6 de 14 · 1,840 impresiones · 14 clics · 48 de 350 MXN · sin pedidos · sigue aprendiendo" y
   las cuatro frases finales. Debajo pinta cuántos clics vinieron de fuera de Amazon, con esta frase: "3 de sus 14
   clics vinieron de fuera de Amazon." "Regresarlo a sus grupos de antes" llama a `retiro/reponer`.
5. En la sección de 11 meses, pinta la lista con una casilla por producto y debajo "Hay N anuncios sin producto ligado
   que no puedo juzgar." La sección ofrece dos acciones: "Sacar de las bolsas compartidas" e "Impulsar aparte".
   "Impulsar aparte" abre el mismo flujo de impulso del cambio 3, con esos productos marcados. "Sacar de las bolsas
   compartidas" la agrega E.1.
#### Comprueba
- Las pruebas comunes y `tests/test_pantalla_productos.py` pasan. Cubren el impulso en curso con día, impresiones,
  clics, gasto y veredicto, sus clics de fuera de Amazon, las dos acciones de la sección de 11 meses y el conteo de
  anuncios sin producto ligado.
- Sobre la copia, los candidatos de `numeros.py` de `ejecucion/P.4/` son los `product_id` de esta consulta de
  control. Agrega a `copia.sh` las tablas `product`, `listing`, `ledger_event` y `ads_product_metric_observation`.

		WITH activos AS (
			SELECT DISTINCT li.product_id FROM ad_entity pa
			  JOIN ad_entity ag ON ag.id = pa.parent_id JOIN ad_entity c ON c.id = ag.parent_id
			  JOIN ad_entity_state sp ON sp.ad_entity_id = pa.id AND sp.status = 'ENABLED'
			  JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id AND sg.status = 'ENABLED'
			  JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
			  JOIN listing li ON li.id = pa.listing_id
			 WHERE pa.platform = :'plataforma' AND pa.kind = 'product_ad'
		), ventas AS (
			SELECT product_id, sum(quantity) AS unidades FROM ledger_event
			 WHERE platform = :'plataforma' AND kind = 'sale' AND event_date >= :'hoy'::date - 90 GROUP BY 1
		), clics AS (
			SELECT li.product_id, sum(u.clicks) AS clics
			  FROM (SELECT DISTINCT ON (advertised_asin, advertised_sku, metric_date) advertised_asin, clicks
			          FROM ads_product_metric_observation
			         WHERE platform = :'plataforma' AND metric_date >= :'hoy'::date - 60
			         ORDER BY advertised_asin, advertised_sku, metric_date, observed_at DESC) u
			  JOIN listing li ON li.platform = :'plataforma' AND li.external_id = u.advertised_asin GROUP BY 1)
		SELECT a.product_id, v.unidades, c.clics FROM activos a JOIN ventas v USING (product_id)
		  LEFT JOIN clics c USING (product_id) WHERE v.unidades >= 2 AND coalesce(c.clics, 0) < 20 ORDER BY 1

- Como referencia, el 2026-10-09 eran 10 candidatos en MX y 5 en US, y la tercera sección traía 90 productos en MX
  y 34 en US.

### E.1: agrega los dos botones de retiro
#### Qué vas a encontrar
- La pantalla de la fábrica es `/campanas/nuevas` (`app/ui.py:424-427`). Su último bloque de creación es la sección
  "Confirmar una operación" (`app/templates/fabrica.html:159-177`).
- `detalle_lote` devuelve estado, plan y pasos, sin el id del grupo (`app/fabrica_web.py:584-618`). Los `listing_id`
  están en `plan.publicaciones`.
- La pantalla compara el literal antes de enviar (`app/static/js/fabrica.js:1153-1155`). `tests/test_ui_fabrica.py`
  corre el JavaScript con Node y falla en CI si Node falta (`:23-30`).
- P.4 dejó `app/templates/productos_sin_clics.html` y `app/static/js/productos.js`. I.5 dejó `retiro/plan` y
  `retiro/ejecutar`, con el literal `SACAR <N> ANUNCIOS`.
#### Pruebas primero
- En `tests/test_ui_fabrica.py`: la página trae el botón "Sacar estos productos de sus bolsas viejas". El botón solo
  se activa con un lote en `applied`. Al tocarlo pide `retiro/plan` con los `listing_ids` del lote, el motivo
  `subfamilia` y el `lote`. Sin el literal exacto no manda `retiro/ejecutar`.
- En `tests/test_retiro_anuncios.py`: con el lote de un grupo nuevo, el plan no trae ningún anuncio de los ad groups
  de ese grupo. El mutante es quitar ese filtro.
- En `tests/test_pantalla_productos.py`: "Sacar de las bolsas compartidas" pide `retiro/plan` con el motivo
  `sin_pedidos` y sin `lote`, muestra cada anuncio y exige el mismo literal `SACAR <N> ANUNCIOS`.
#### Cambios
1. Agrega el botón al final de la sección de creación de `fabrica.html`, y su flujo a `fabrica.js`: vista previa del
   retiro, lista de anuncios, literal y resultado.
2. Agrega la acción "Sacar de las bolsas compartidas" a la sección de 11 meses de `productos_sin_clics.html` y a
   `productos.js`, con ese flujo.
3. No escribas ninguna regla de retiro en JavaScript: las dos pantallas solo llaman a las rutas de I.5. No cambies el
   literal `CREAR 5 CAMPAÑAS` ni las cinco filas del formulario.
#### Comprueba
- Pruebas del paso, con la prueba de Node corrida y no saltada: `tests/test_ui_fabrica.py
  tests/test_retiro_anuncios.py tests/test_pantalla_productos.py tests/test_api_fabrica.py`. Cubren el uso de
  `retiro_anuncios` con el mismo literal y el retiro solo de los ad groups que no son del grupo nuevo.

### D.4: despliega el impulso, la pantalla de productos y el retiro
#### Qué vas a encontrar
- Lleva el PR de la sección 5: I.1 a I.5, P.4 y E.1. Sus migraciones son la 0064, la 0065 y la 0066. Aplica
  también las migraciones de otras secciones que su commit traiga y que no estén aplicadas.
- Instala la línea suelta de `impulso-vigia` que I.3 dejó en `docs/DEPLOY.md`. Corre a las 08:20 UTC, después de
  las ingestas de las 07:10, 07:20 y 07:25, y antes del ciclo de las 08:40.
- La reversa de la 0065 y la de la 0066 abortan si `impulso` o `anuncio_retiro` ya tienen una fila. Después del
  primer lanzamiento, un problema se corrige hacia adelante.
#### Pruebas primero
Corre `ensayo.sh`. Esperado: el esquema queda idéntico después de las reversas y todo grupo existente queda en
`fabrica5` con la 0064 aplicada.
#### Cambios
1. Escribe los cuatro scripts de `ejecucion/D.4/` y la sección D.4 de `docs/DEPLOY.md`.
2. Haz que `desplegar.sh` corra una vez `docker exec orbit-app-1 python -m app.cli impulso-vigia` antes de instalar
   el cron. Si no sale 0, o si `impulso_lectura` tiene alguna fila después, aborta sin instalar la línea.
3. Haz que `checklist.sh` tome un producto candidato de `GET /api/dashboard/productos-sin-clics` y pida
   `POST /api/fabrica/impulso/plan` con él.
#### Comprueba
- `checklist.sh` sale 0, con `/health` y `/productos-sin-clics` en 200 y `crontab -u gon -l | grep -c impulso-vigia`
  en 1.
- La vista previa del impulso responde 200, y los conteos de `fabrica_lote` e `impulso` son iguales antes y después.
- La corrida de `impulso-vigia` del cambio 2 salió 0 sin impulsos. `impulso`, `impulso_lectura`, `anuncio_retiro` y
  `anuncio_reposicion` existen y están vacías.

## Sección 6: búsquedas que gastan sin vender

### Qué entrega
El dueño ve la pantalla "Búsquedas que gastan sin vender": la lista de páginas de producto ajenas y de palabras que ya
gastaron el mínimo sin vender, junto a lo que daría el azar. El mínimo es el gasto para concluir. La pantalla es de
solo lectura: no escribe nada en Amazon y no propone nada a la cola de veto. El dueño agrega los negatives a mano en
Amazon (decisión D11). Esta sección no construye ningún motor de negatives.

### Tareas, en orden
| Tarea | Qué hace | Qué necesita |
| --- | --- | --- |
| P.6 | Construye la pantalla de búsquedas que gastan sin vender | 0.b |
| D.5 | Despliega la sección, sin migración propia ni línea de cron | P.6 |

### Rama y PR
- Usa la rama `bids-02/s6-busquedas` y el árbol `~/dev/wt-bids-02-s6`, creados desde `origin/master` con el PR
  de código de la sección 1 mergeado.
- Esta sección depende solo de la sección 1. Se construye al mismo tiempo que las secciones 2, 3 y 5.
- Haz un commit por tarea, en el orden de la tabla. P.6 lleva dos: contrato y pantalla. El commit de D.5 trae sus
  scripts y la sección D.5 de `docs/DEPLOY.md`.
- D.5 corre en cuanto claw mergea el PR de código.
- El PR de cierre usa la rama `bids-02/s6-cierre`. Trae las salidas de D.5 y marca las dos filas.

### Archivos de la sección
- Propios: `app/pantalla_busquedas.py`, `app/templates/busquedas_sin_venta.html`, `tests/test_pantalla_busquedas.py`
  y los scripts de `ejecucion/P.6/` y de `ejecucion/D.5/`.
- Qué construir está en la sección "Búsquedas que gastan sin vender" de `design.md` y en el bloque
  `app/pantalla_busquedas.py` de `bosquejo.py`. La definición medida está en `prototipos/p7_busquedas.py`.
- Esta sección no trae migración, comando ni línea de cron. No edita `app/optimizer/`, `app/cycle.py`, `app/apply.py`,
  `app/apply_cola.py`, `app/jev_salud.py` ni `app/templates/gasto_sin_venta.html`.
- Compartidos con las secciones 2, 3 y 5, que se construyen al mismo tiempo: `app/api_dashboard.py`, `app/ui.py`,
  `app/templates/base.html`, `tests/test_api_dashboard.py`, `tests/test_ui.py`, `tests/test_arq_bids_p.py`,
  `docs/DEPLOY.md` y `ejecucion/0.b/copia.sh`.

### Comprueba la sección completa
Corre esto una vez, sobre el último commit del PR de código:
- Las pruebas focalizadas de la sección, juntas.

		uv run --frozen python -m pytest -q -rs tests/test_pantalla_busquedas.py tests/test_arq_bids_p.py \
			tests/test_api_dashboard.py tests/test_ui.py tests/test_ui_copy_campana.py tests/test_architecture.py

  Esperado: todas terminan en `passed` y ningún salto dice "sin Postgres utilizable".
- `pre-commit run --all-files` sale 0.
- La batería completa, en CI: `gh workflow run quality.yml --ref bids-02/s6-busquedas`. Esperado: el job `completa`
  termina en `success`.
- Sobre la copia de producción, cuando claw te lo encarga: `numeros.py` de P.6 da, para cada mercado, los mismos ASIN,
  las mismas palabras y la misma referencia que las tres consultas de control de P.6.
- La página no trae ningún botón ni formulario: lo prueba `tests/test_ui.py`, que extiende P.6.
- El despliegue, con los scripts de `ejecucion/D.5/`: `ensayo.sh` deja el esquema idéntico y `checklist.sh <sello>`
  sale 0, con la página en 200 en los dos mercados.

### P.6: construye la pantalla de búsquedas que gastan sin vender
#### Qué vas a encontrar
- `search_term_observation` guarda una fila por plataforma, ad group, búsqueda, fecha y observación
  (`migrations/0001_initial.sql:365-388`). Sus columnas son `platform`, `ad_entity_id`, `search_term`, `metric_date`,
  `observed_at`, `metric_currency`, `cost`, `clicks`, `orders`, `ad_revenue` e `is_asin_like` (`:366-376`). `cost`,
  `clicks` y `orders` pueden ser `NULL`.
- `ad_entity_id` es siempre un ad group: la ingesta liga el reporte con `"kind": "ad_group"`
  (`app/ads/reports.py:368-376`). `orders` guarda `purchases7d` (`:137`).
- La tabla no tiene vista de última observación. El motor la colapsa con `_SQL_COLAPSO_TERMINOS`
  (`app/optimizer/windows.py:321-330`): `DISTINCT ON (ad_entity_id, search_term, metric_date)`, con `observed_at DESC`
  y `source_report_id DESC NULLS LAST` como desempate.
- `_SQL_TERMINOS_CORTES` suma ese colapso por ad group y búsqueda (`windows.py:332-354`). Deja en `NULL` la suma de
  una métrica que algún día trae en `NULL` (`:337-340`). Esa consulta filtra un solo ad group. No la importes: escribe
  la tuya con el mismo colapso.
- La ingesta llena `is_asin_like` con `es_asin_like` (`app/ads/reports.py:890-910`): 10 caracteres alfanuméricos que
  empiezan con `B0`, sin distinguir mayúsculas. Un ASIN puede llegar en minúsculas. El motor nunca propone un negative
  para una búsqueda ASIN-like (`app/optimizer/hygiene.py:301-303`).
- El estado de una campaña es `ad_entity_state.status`, con el texto de Amazon: `ENABLED`, `PAUSED` o `ARCHIVED`
  (`migrations/0001_initial.sql:644-648`). La campaña de un ad group es su `ad_entity.parent_id` (`:255`) y el nombre
  de la campaña es `ad_entity.name` (`:256`). El modelo del filtro por estado es
  `migrations/0013_entidad_inerte.sql:31-33`.
- Los ASIN propios están en `listing.external_id`, con una fila por plataforma y ASIN
  (`migrations/0001_initial.sql:106-114`).
- El gasto para concluir sale de `gasto_para_concluir_desde_settings`, que construyó 0.b. Los settings vigentes son la
  fila de mayor `id` de `config_version` (`app/api_dashboard.py:189-191` y `:561-567`). Sin fila, los settings son `{}`.
- La pantalla "Gasto sin venta" (`/gasto-sin-venta`) ya existe y es otra: lista señales de JEV por búsqueda. No la
  cambies. Copia su estructura: la lectura de solo SELECT (`app/jev_salud.py:256-278`), la función delgada
  (`app/api_dashboard.py:1494-1508`), la ruta (`app/ui.py:565-583`), los dos enlaces de mercado
  (`app/templates/gasto_sin_venta.html:9-13`) y la prueba del texto escapado (`tests/test_ui.py:1708`).
- Los tipos de este paso no están en la sección `Pantallas "puro contrato"` de `bosquejo.py`. Están en el bloque
  `app/pantalla_busquedas.py`.
- La medición del 2026-10-09 cubrió 100 días (`prototipos/p7_busquedas.py`). La pantalla cubre 90. Por eso sus números
  pueden diferir de los de `prototipos/p7_salida.txt`.
#### Pruebas primero
Crea `tests/test_pantalla_busquedas.py`. Crea `tests/test_arq_bids_p.py` si no está en tu rama, con el candado de
P.1: ningún `app/pantalla_*.py` importa `app.ads.write` ni `app.apply`.

Para el commit de contrato, prueba primero la función pura `resume_busquedas`. Arma cada caso a mano con
`FilaBusqueda`. Usa búsquedas y ASIN inventados, nunca un texto de producción (regla 16). Todos los casos usan
`gasto_para_concluir=Decimal("350")`. Cada fila va aquí como (ad group, campaña, búsqueda, clics, gasto, pedidos).
`es_asin` es verdadero solo en las búsquedas con forma de ASIN.
- **Referencia.** Con (1, "C1", "alfa", 2, 60, 0) y (1, "C1", "beta", 2, 40, 2), la referencia es
  `ReferenciaAzar(Decimal("60"), Decimal("25"))`. La conversión es 2 pedidos entre 4 clics. El azar es 60 por 0.25
  más 40 por 0.25, entre 100.
- **Sin referencia.** Solo con la fila "alfa", la cuenta no tiene pedidos y la referencia es
  `ReferenciaAzar(None, None)`. Con una sola fila de 0 clics, 0 de gasto y 0 pedidos, la referencia también es
  `ReferenciaAzar(None, None)`.
- **ASIN sumado en toda la cuenta.** Con (1, "C1", "b0ajeno001", 30, 200, 0) y (2, "C2", "B0AJENO001", 20, 150, 0),
  `asins` es `(FilaAsinAjeno("B0AJENO001", 50, Decimal("350"), ("C1", "C2"), False),)`. Con 149.99 de gasto en la
  segunda fila, `asins` es `()` y `asins_a_medio_camino` es 1. Con 1 pedido en la segunda fila, `asins` es `()` y
  `asins_a_medio_camino` es 0.
- **ASIN propio.** Con las dos filas del caso anterior y `asins_propios=frozenset({"B0AJENO001"})`, la misma fila sale
  con `es_propio` verdadero. Es el mutante obligatorio "un ASIN propio se esconde en vez de marcarse": quita de la
  lista los ASIN propios.
- **Palabra sin pedido.** Con (1, "C1", "gama uno", 10, 200, 0) y (1, "C1", "gama dos", 10, 200, 0), `palabras` es
  `(FilaPalabra("gama", 2, 20, Decimal("400"), ("gama dos", "gama uno")),)` y `palabras_a_medio_camino` es 4. Con la
  fila (2, "C2", "gama tres", 3, 50, 1) agregada, `palabras` es `()`. Es el mutante obligatorio "una palabra entra
  aunque una búsqueda que la contiene tuvo un pedido": suma solo las búsquedas sin pedido.
- **La misma búsqueda en dos ad groups.** Con las dos primeras filas del caso anterior y (2, "C2", "gama uno", 1, 10,
  0), la fila "gama" trae 2 búsquedas, 21 clics y 410 de gasto.
- **Palabras vacías.** Con (1, "C1", "delta para omega", 5, 400, 0), las palabras son, en orden, "delta", "delta
  para", "omega" y "para omega". "para" no sale sola. El mutante es quitar las palabras vacías antes de armar las
  entradas de dos palabras.
- **Palabra corta.** Con (1, "C1", "xy omega", 5, 400, 0), las palabras son "omega" y "xy omega". "xy" no sale sola.
- **Métrica `None`.** Con las dos filas de la referencia y (1, "C1", "nulo", 3, `None`, 0), la referencia sigue en 60
  y 25. Con la fila (1, "C1", "b0sindato1", 3, 400, `None`) sola, `asins` es `()`. El mutante es cambiar un `None`
  por 0: la referencia pasa a 60 y 51, y el ASIN entra.

Prueba después la lectura `lee_busquedas`, con Postgres. Arma la base con `migrations/0001_initial.sql`, como `db_s5`
(`tests/test_jev_pantallas.py:481-499`). Siembra cada observación con un ayudante como `_termino`
(`tests/test_optimizer_windows.py:310`). Todos los casos usan `plataforma="amazon_mx"` y `hoy=date(2026, 10, 7)`.
- `desde` es 2026-07-06 y `hasta` es 2026-10-04. Una observación del 2026-07-06 cuenta. Una del 2026-07-05 y una del
  2026-10-05 no cuentan.
- Con dos observaciones del mismo ad group, búsqueda y fecha, cuenta solo la de `observed_at` más reciente. El mutante
  es quitar el `DISTINCT ON`.
- Un ASIN con 900 de gasto y 0 pedidos en una campaña `PAUSED` no sale. Es el mutante obligatorio "la pantalla de
  búsquedas cuenta una campaña apagada": quita el JOIN del estado de la campaña.
- Un ad group `PAUSED` dentro de una campaña `ENABLED` sí cuenta.
- El gasto del mismo ASIN en `amazon_us` no entra a la suma, y `moneda` es `MXN`. El mutante es quitar el filtro de
  plataforma.
- Una búsqueda con dos fechas, una de ellas con `cost` en `NULL`, da una `FilaBusqueda` con `gasto` en `None` y no
  suma a ninguna lista.
- Un ASIN que está en `listing` sale con `es_propio` verdadero. Vale también si su fila de `listing` es de
  `amazon_us` y si la búsqueda llega en minúsculas.
- Sin fila en `config_version`, un ASIN con 349 de gasto no entra y uno con 350 entra. Con
  `{"ads_gasto_para_concluir_amazon_mx": "100"}`, un ASIN con 100 de gasto y 0 pedidos entra.
- Con una conexión falsa como `_ConnFalsa` (`tests/test_jev_pantallas.py:362-371`), `lee_busquedas` hace tres
  consultas y cada una empieza con `SELECT` o con `WITH`.
- `como_dict()` da cada gasto como texto y cada `None` como `None`.

Agrega este candado a `tests/test_arq_bids_p.py`, con su fuga sembrada: el texto de `app/pantalla_busquedas.py` no
contiene `apply_queue`, `INSERT`, `UPDATE` ni `DELETE`. Con el candado de P.1, cubre la cláusula "no escribe nada en
Amazon y no propone nada a la cola de veto".

Para el commit de pantalla, extiende `tests/test_api_dashboard.py` y `tests/test_ui.py`.
- `GET /api/dashboard/busquedas-sin-venta` responde 200 con `referencia`, `asins`, `palabras`, `asins_a_medio_camino`
  y `palabras_a_medio_camino`. Con `plataforma=amazon_mx`, `moneda` es `MXN`. Con una plataforma fuera del
  vocabulario, responde 422.
- La página pinta la frase de referencia con los dos porcentajes. Con la referencia en `None`, la página no pinta
  ningún porcentaje y pinta la frase del cambio 28.
- Un ASIN propio lleva la marca "es un producto tuyo". Un ASIN ajeno no la lleva.
- Una fila de palabra pinta la palabra, el número de búsquedas, los clics, el gasto y hasta tres búsquedas de ejemplo.
- La página pinta los dos conteos de "A medio camino" y ninguna lista debajo.
- Con una lista vacía, la página pinta la frase del cambio 31 que le toca.
- Una búsqueda de ejemplo con el texto `PAYLOAD_XSS` sale escapada, como en `tests/test_ui.py:1708`.
- La página no trae ningún `<button>` ni `<form>`.
- `base.html` trae las tres entradas de `busquedas-sin-venta`.
#### Cambios
1. Commit de contrato: crea `app/pantalla_busquedas.py` con `PALABRAS_VACIAS`, `FilaBusqueda`, `FilaAsinAjeno`,
   `FilaPalabra`, `ReferenciaAzar`, `PantallaBusquedas`, `resume_busquedas` y `lee_busquedas`, del bloque
   `app/pantalla_busquedas.py` de `bosquejo.py`.
2. En `resume_busquedas`, descarta primero toda fila con `clics`, `gasto` o `pedidos` en `None`. Esa fila no suma y
   no cuenta como cero.
3. Calcula la referencia con `Decimal` y con todas las filas que quedan, ASIN incluidos. `p` es la suma de pedidos
   entre la suma de clics. `visto_pct` es 100 por el gasto de las filas con 0 pedidos, entre el gasto total.
   `azar_pct` es 100 por la suma de `gasto * (1 - p) ** clics` de cada fila, entre el gasto total.
4. Redondea `visto_pct` y `azar_pct` a un entero con `ROUND_HALF_UP`.
5. Si los pedidos, los clics o el gasto suman 0, devuelve `ReferenciaAzar(None, None)`.
6. Suma los ASIN por `termino.strip().upper()`, en toda la cuenta. Un ASIN entra con 0 pedidos y con un gasto igual o
   mayor a `gasto_para_concluir`.
7. Pon `es_propio` en verdadero si el ASIN está en `asins_propios`. No lo quites de la lista.
8. Llena `campanas` con los nombres distintos de campaña, en orden alfabético.
9. Arma las palabras solo con las filas que no son ASIN. Pasa cada búsqueda a minúsculas y sepárala con la expresión
   `[a-záéíóúñü0-9]+`.
10. Arma las entradas de una palabra sin las `PALABRAS_VACIAS` y sin las palabras de una o dos letras.
11. Arma las entradas de dos palabras con cada par de palabras seguidas de la búsqueda completa, sin quitar ninguna.
12. Suma cada búsqueda una sola vez a cada entrada. Una entrada entra con 0 pedidos sumados y con un gasto igual o
    mayor a `gasto_para_concluir`.
13. Llena `busquedas` con el número de textos de búsqueda distintos. Llena `ejemplos` con hasta tres búsquedas, con la
    de más gasto primero.
14. Cuenta "a medio camino" los ASIN y las entradas con 0 pedidos y con un gasto desde la mitad de
    `gasto_para_concluir` hasta menos que `gasto_para_concluir`. Son dos conteos: uno de ASIN y uno de palabras.
15. Ordena cada lista con más gasto primero. En empate, ordena por texto.
16. Escribe `lee_busquedas` con tres consultas de solo SELECT, en este orden: los settings vigentes de
    `config_version`, las búsquedas y los ASIN propios.
17. Calcula la ventana con `hasta = hoy - 3` y `desde = hasta - 90`. Las dos fechas entran a la ventana.
18. En la consulta de búsquedas, colapsa como `_SQL_COLAPSO_TERMINOS` y filtra la plataforma y las fechas.
19. En la misma consulta, deja solo los ad groups cuya campaña está `ENABLED`. No filtres el estado del ad group.
20. En la misma consulta, suma por ad group y búsqueda. Deja en `NULL` la suma de una métrica que algún día trae en
    `NULL`, como `_SQL_TERMINOS_CORTES`. Trae también `bool_or(is_asin_like)` y el nombre de la campaña.
21. Lee los ASIN propios con `SELECT upper(external_id) FROM listing WHERE platform IN ('amazon_mx', 'amazon_us')`.
22. Toma la moneda de `PLATAFORMAS_MONEDA` y el mínimo de `gasto_para_concluir_desde_settings`. No escribas 350 ni 36
    en este módulo.
23. Escribe `ejecucion/P.6/numeros.py`, con los argumentos `--plataforma` y `--hoy`, y `ejecucion/P.6/control.sql`, con
    las tres consultas de "Comprueba".
24. Commit de pantalla: agrega `GET /api/dashboard/busquedas-sin-venta`, la ruta `/busquedas-sin-venta` y
    `app/templates/busquedas_sin_venta.html`.
25. Haz que la función de `app/api_dashboard.py` llame a `lee_busquedas` con el día de hoy en UTC.
26. En `base.html`, agrega las tres entradas con la llave `busquedas-sin-venta`. El título es "Búsquedas que gastan sin
    vender" y el subtítulo es "Solo campañas encendidas · últimos 90 días". El enlace va en el grupo "Decidir", después
    de "Gasto sin venta", y la llave va en la pestaña `revision`.
27. Pinta arriba la frase de referencia de `design.md`, con los dos números de la lectura: "40 % de tu gasto cayó en
    búsquedas que no vendieron. Con la conversión de tu cuenta, el azar daría 33 %. La diferencia es lo único que un
    negative podría recuperar."
28. Si la referencia llega en `None`, pinta en su lugar: "Sin pedidos o sin clics en estos 90 días: no hay referencia
    de azar."
29. Pinta la tabla "Páginas de producto ajenas" con el ASIN, los clics, el gasto y las campañas. Pon la marca "es un
    producto tuyo" en cada ASIN propio.
30. Pinta la tabla "Palabras y frases" con la palabra, el número de búsquedas, los clics, el gasto y los ejemplos.
31. Si una lista llega vacía, pinta en su lugar "Ninguna página de producto pasó el mínimo sin vender." o "Ninguna
    palabra pasó el mínimo sin vender."
32. Pinta "A medio camino" como dos conteos, sin lista.
33. No pongas ningún `<button>` ni `<form>` en la página. Guarda la evidencia de la pantalla en `ejecucion/P.6/`.
#### Comprueba
- Las pruebas comunes y `tests/test_pantalla_busquedas.py` pasan:

		uv run --frozen python -m pytest -q -rs tests/test_pantalla_busquedas.py tests/test_arq_bids_p.py \
			tests/test_api_dashboard.py tests/test_ui.py tests/test_ui_copy_campana.py tests/test_architecture.py

  Cubren la frase de referencia con sus dos partes y sin ellas, el ASIN sumado en toda la cuenta, la marca del ASIN
  propio, la palabra con un pedido, las palabras vacías, la métrica `None`, la campaña apagada y MX sin US.
- Sobre la copia, cuando claw te lo encarga, compara `numeros.py` con `control.sql`. Agrega antes a `copia.sh` las
  tablas `search_term_observation`, `listing` y `config_version`, si no están. Corre los dos para `amazon_mx` y para
  `amazon_us`.
- Pasa a `control.sql` las mismas fechas que imprime `numeros.py`. Con `--hoy 2026-10-07`, las variables son
  `psql -v plataforma=amazon_mx -v desde=2026-07-06 -v hasta=2026-10-04 -v minimo=350`.
- `minimo` es el gasto para concluir de la plataforma. Léelo con
  `SELECT settings->>'ads_gasto_para_concluir_amazon_mx' FROM config_version ORDER BY id DESC LIMIT 1`. Si da `NULL`,
  usa 350 para `amazon_mx` y 36 para `amazon_us`.
- Cada consulta de `control.sql` empieza con estas dos CTE. Parten de las tablas base y no usan el código del paso:

		WITH ultima AS (
			SELECT DISTINCT ON (o.ad_entity_id, o.search_term, o.metric_date)
			       o.ad_entity_id, o.search_term, o.is_asin_like, o.cost, o.clicks, o.orders, c.id AS campana_id
			  FROM search_term_observation o
			  JOIN ad_entity ag ON ag.id = o.ad_entity_id
			  JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
			  JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
			 WHERE o.platform = :'plataforma' AND o.metric_date BETWEEN :'desde' AND :'hasta'
			 ORDER BY o.ad_entity_id, o.search_term, o.metric_date, o.observed_at DESC, o.source_report_id DESC NULLS LAST
		), busqueda AS (
			SELECT ad_entity_id, search_term, campana_id, bool_or(is_asin_like) AS es_asin,
			       sum(cost) AS gasto, sum(clicks) AS clics, sum(orders) AS pedidos
			  FROM ultima GROUP BY 1, 2, 3
			HAVING bool_and(cost IS NOT NULL AND clicks IS NOT NULL AND orders IS NOT NULL))

- La primera consulta lista por ASIN el gasto y los pedidos. Los ASIN de `numeros.py` son las filas con `pedidos` en 0
  y `gasto` igual o mayor a `minimo`, con el mismo gasto, los mismos clics y el mismo `propio`.
  `asins_a_medio_camino` es el número de filas con `pedidos` en 0 y `gasto` desde la mitad de `minimo` hasta menos que
  `minimo`.

		SELECT upper(btrim(search_term)) AS asin, sum(gasto) AS gasto, sum(clics) AS clics, sum(pedidos) AS pedidos,
		       count(DISTINCT campana_id) AS campanas,
		       bool_or(upper(btrim(search_term)) IN (SELECT upper(external_id) FROM listing
		                                              WHERE platform IN ('amazon_mx', 'amazon_us'))) AS propio
		  FROM busqueda WHERE es_asin GROUP BY 1 ORDER BY 2 DESC, 1

- La segunda consulta da los insumos de la referencia de azar, sumados por búsqueda. `visto_pct` y `azar_pct` son los
  de la referencia de `numeros.py`. Si `pedidos` o `clics` es 0, el contrato da `None` en las dos partes.

		, cuenta AS (
			SELECT sum(gasto) AS gasto, sum(clics) AS clics, sum(pedidos) AS pedidos,
			       sum(pedidos) / NULLIF(sum(clics), 0) AS p FROM busqueda)
		SELECT c.gasto, c.clics, c.pedidos, sum(b.gasto) FILTER (WHERE b.pedidos = 0) AS gasto_sin_pedido,
		       round(100 * sum(b.gasto) FILTER (WHERE b.pedidos = 0) / NULLIF(c.gasto, 0)) AS visto_pct,
		       round(100 * sum(b.gasto * power(1 - c.p, b.clics)) / NULLIF(c.gasto, 0)) AS azar_pct
		  FROM busqueda b CROSS JOIN cuenta c GROUP BY c.gasto, c.clics, c.pedidos, c.p

- La tercera consulta lista las palabras sin pedido que llevan al menos la mitad de `minimo`. Las palabras de
  `numeros.py` son las filas con `gasto` igual o mayor a `minimo`, con las mismas búsquedas, los mismos clics y el
  mismo gasto. `palabras_a_medio_camino` es el número de las demás filas.

		, ficha AS (
			SELECT b.ad_entity_id, b.search_term, b.gasto, b.clics, b.pedidos, t.n, t.m[1] AS palabra
			  FROM busqueda b
			 CROSS JOIN LATERAL regexp_matches(lower(b.search_term), '[a-záéíóúñü0-9]+', 'g') WITH ORDINALITY AS t(m, n)
			 WHERE NOT b.es_asin
		), entrada AS (
			SELECT DISTINCT ad_entity_id, search_term, gasto, clics, pedidos, palabra FROM ficha
			 WHERE length(palabra) > 2 AND palabra <> ALL (string_to_array(
			       'de la el los las para y con en un una por del a al que the of for and to in on with', ' '))
			UNION ALL
			SELECT DISTINCT a.ad_entity_id, a.search_term, a.gasto, a.clics, a.pedidos, a.palabra || ' ' || s.palabra
			  FROM ficha a JOIN ficha s ON s.ad_entity_id = a.ad_entity_id AND s.search_term = a.search_term AND s.n = a.n + 1)
		SELECT palabra, count(DISTINCT search_term) AS busquedas, sum(clics) AS clics, sum(gasto) AS gasto
		  FROM entrada GROUP BY 1 HAVING sum(pedidos) = 0 AND sum(gasto) >= :'minimo'::numeric / 2 ORDER BY 4 DESC, 1

- Como referencia, el 2026-10-09 y con 100 días (`prototipos/p7_salida.txt`): MX no traía ningún ASIN, y traía la
  palabra «sin» y las frases «cofre para», «estuche para» y «boda sin», con 40 % visto contra 33 % por azar. US traía
  2 ASIN, «box» y «coins catholic», con 63 % contra 62 %.

### D.5: despliega la pantalla de búsquedas
#### Qué vas a encontrar
- Lleva el PR de la sección 6: P.6. No trae migración propia ni línea de cron. Aplica la 0060, de la sección 1, si
  ningún despliegue anterior la aplicó. Aplica también las migraciones de otras secciones que su commit traiga y que
  no estén aplicadas.
- La pantalla lee tablas que producción ya tiene: `search_term_observation`, `ad_entity`, `ad_entity_state`, `listing`
  y `config_version`. No espera un ciclo ni un sync.
- El modelo de un despliegue sin migración propia ni cron es
  `docs/evidencia/jev-ads-02/ejecucion/S.5/desplegar.sh` (`:3` y `:102`).
#### Pruebas primero
Corre `ensayo.sh`. Esperado: el esquema queda idéntico después de las reversas. Si el commit no trae ninguna migración
sin aplicar, `ensayo.sh` lo imprime y sale 0.
#### Cambios
1. Escribe los cuatro scripts de `ejecucion/D.5/` y la sección D.5 de `docs/DEPLOY.md`.
2. Haz que `desplegar.sh` no cambie el crontab y que lo imprima.
3. Haz que `checklist.sh` pida, para cada mercado, `GET /api/dashboard/busquedas-sin-venta?plataforma=<mercado>` y la
   página `/busquedas-sin-venta?plataforma=<mercado>`. El modelo es
   `docs/evidencia/jev-ads-02/ejecucion/S.5/checklist.sh:104-111`. Exige que el JSON traiga `referencia`, `asins` y
   `palabras`, que `moneda` sea la del mercado y que la página responda 200.
4. Haz que `checklist.sh` nunca salga 3: este despliegue no espera un ciclo ni un sync.
#### Comprueba
- `checklist.sh` sale 0, con `/health` en 200.
- `/busquedas-sin-venta` responde 200 en MX y en US.
- `crontab -u gon -l` da lo mismo antes y después del despliegue.

## Sección 7: cierre

### Qué entrega
Las filas del plan y los documentos del repo quedan cerrados. El dueño no ve nada nuevo. Esta sección corre al final,
después de todas las demás.

### Tareas, en orden
| Tarea | Qué hace | Qué necesita |
| --- | --- | --- |
| C.1 | Cierra las filas y los documentos | X.1, D.3, D.4 y D.5 |

### Rama y PR
- Usa la rama `bids-02/s7-cierre` y el árbol `~/dev/wt-bids-02-s7`, creados desde `origin/master` con las
  secciones 2, 4, 5 y 6 mergeadas, desplegadas y con su PR de cierre mergeado.
- Es un solo PR de documentos, por el carril `fast`, con un commit: `BIDS 02 C.1: <qué hace>`. Este PR es también el
  PR de cierre de la sección.

### Archivos de la sección
- Edita `plans/bids-02.md`, `plans/ROADMAP.md`, `docs/CHAT-CONTEXT.md`, `docs/CONTEXTO.md` y `docs/DEPLOY.md`. Ninguna
  otra sección está abierta.

### Comprueba la sección completa
Corre esto una vez, sobre el último commit del PR:
- `pre-commit run --all-files` sale 0.
- La consulta de los criterios 4 y 5 de X.1 dice `niveles_v3` en las dos plataformas, o la fila X.1 lleva su motivo.
- Las cinco pantallas responden 200: `/keywords-danadas`, `/donde-poner-el-dinero`, `/productos-sin-clics`,
  `/busquedas-sin-venta` y `/ruido`.
- Esta sección no tiene pruebas nuevas ni despliegue.

### C.1: cierra las filas y los documentos

Es un solo PR de documentos, por el carril `fast`. Depende de X.1, D.3, D.4 y D.5.
#### Qué vas a encontrar
- La ruta del plan la fijó 0.a: `plans/bids-02.md`. El marcador de una fila cerrada es `cc:完了`. El PR de cierre
  de cada sección ya marcó sus filas.
- Las filas AUTO-03 a AUTO-06 de `plans/ROADMAP.md` están en `:147-150`.
#### Pruebas primero
Este paso no lleva pruebas nuevas: la fila es `[tdd:skip:docs]`.
#### Cambios
1. Comprueba que cada fila de las secciones 1 a 6 lleva `cc:完了` o su motivo, y marca la fila C.1.
2. En `plans/ROADMAP.md`, escribe en las filas AUTO-03 a AUTO-06 qué cubrió este plan y qué no.
3. En `docs/CONTEXTO.md`, cambia lo que describe las bandas, `evidencia_v2` y el regreso por la política `niveles_v3`.
   Deja una regla por línea, sin relato de fechas.
4. En `docs/DEPLOY.md`, deja las líneas de cron instaladas, la tabla de crons y el interruptor de política. En
   `docs/CHAT-CONTEXT.md`, agrega la entrada del cierre. Corre `pre-commit run --all-files`. Debe pasar.
#### Comprueba
- El motor está encendido: la consulta de los criterios 4 y 5 de X.1 dice `niveles_v3` en las dos plataformas. Si
  una quedó sin encender, la fila X.1 lleva el motivo de `ejecucion/X.1/no-encendida-<plataforma>.md`.
- Las cinco pantallas responden 200: `/keywords-danadas`, `/donde-poner-el-dinero`, `/productos-sin-clics`,
  `/busquedas-sin-venta` y `/ruido`.
- Las filas AUTO-03 a AUTO-06 de `plans/ROADMAP.md` dicen qué cubrió este plan y qué no.

## Preguntas que siguen abiertas

Ninguna frena la construcción. Usa el valor de la tabla "Decisiones que gobiernan este plan" del plan.

| Qué no se sabe | Cómo se resuelve | Valor que usas mientras tanto |
| --- | --- | --- |
| Configuración de `A1U - Auto Discovery - US` | Corre cada sonda de V.0 sin `--acepto-mutacion-real` | Ninguno: la sonda aborta si la campaña no está `PAUSED` |
| Si `PUT /sp/productAds` pausa un product ad | Sonda 1 de V.0 | Sin sellar, I.4 archiva y repone |
| Presupuesto diario mínimo de cada país | Sonda 2 de V.0, una corrida por país | `PRESUPUESTO_MINIMO_CAMPANA` en `None`: el presupuesto es el tope entre 14. Con el dato, el impulso usa el mínimo medido |
| Si una lista parcial de `placementBidding` reemplaza la entera | Sonda 3 de V.0 | `PlanAjuste.despues` manda siempre la configuración completa |
| Valores permitidos de `offAmazonSettings` | Sonda 4 de V.0, con dos valores candidatos | Sin sellar, ese ajuste no se construye y la pantalla muestra el dato sin botón. El impulso nace siempre con la configuración por defecto de Amazon |
| Tablas que lee el ciclo, para `copia.sh` | El `grep` de "Comprueba" de M.3 | Ninguno |
| Nombre del CHECK de `fabrica_lote.estado` | `\d fabrica_lote` | `fabrica_lote_estado_check`, el que escribe `datos.sql` |
| Días para invertir dirección sin clics nuevos (R14) | Se mira en la pantalla de ruido | 14, en `DIAS_SALIDA` |
| Anuncios sin producto ligado | Decisión del dueño | Fuera del impulso y del retiro. La pantalla dice cuántos son |
| Impulso que agota su tope | Decisión del dueño | Se pausa sin veto de 2 días |
| Subidas del primer día | Paso 7 de X.1 | Sin tope propio. Las acota el cupo diario de 45 |
| Sin grupo de control | Decisión D4 | La pantalla de ruido compara antes y después y dice que no es causal |

Esta guía fija lo que ni el diseño ni el plan traen. Si el lead cambia una de estas cosas, cambia la pieza nombrada:
- El literal `"motor_bid": "apagado"` (M.3) y el N de "Regresar todas", que cuenta las filas sin regresar (M.5).
- El calendario de ingesta de M.2 y las ventanas de `lee_danadas` (P.3a) y de `lee_productos` (P.4).
- Las rutas `campana-ajuste`, los literales `APLICAR AJUSTE` y `REGRESAR AJUSTE` y `CLASES_SELLADAS` (V.3).
- `SOURCE_PLACEMENTS` (V.2), la marca diaria en `ingest_run` y la hora del cron de avisos (V.4).
- El vocabulario de placement de la 0062, que es el de `Ubicacion` y no el de `datos.sql` (V.2).
- `PlanImpulso.objetivo`, `PlanImpulso.bids_acotados` y `PRESUPUESTO_MINIMO_CAMPANA` (I.2), y el camino de la
  reposición, que sale del estado del anuncio leído en Amazon (I.4).
- El trigger de la 0066 que deja un solo retiro abierto por anuncio (I.4).
- `ImpulsoVisto.clics_fuera_de_amazon`, su frase y la dependencia de P.4 con V.2 (P.4).
- El redondeo de la referencia a enteros, el desempate por texto y las dos fechas dentro de la ventana (P.6).
- El filtro por el estado de la campaña y no del ad group, y los ASIN propios de las dos plataformas de Amazon (P.6).
- Las frases de referencia ausente y de lista vacía, el subtítulo del menú, la pestaña `revision` y la tercera
  consulta de control, la de palabras (P.6).
- `fraccion.sh` y su orden, antes de `desplegar.sh` (D.1).
- La bandera `--completo` de `exporta_casos.py` y los dos archivos de fixture (M.1).
- El camino de la PAUSE fijo en el del flag `ads_pause_sin_cooldown_bid` encendido, con su lector sin borrar (M.3).
- La rama `bids-02/s1-0a`, los árboles `wt-bids-02-s<N>` y `gh workflow run quality.yml --ref <rama>` como la forma de
  correr la batería sobre el commit final de una sección.
- Las rutas y los literales de la tabla de I.5, salvo `impulso/lanzar` y su literal.
- El literal `CREAR 5 CAMPAÑAS` no cambia, aunque `borrado.md` lo lista: `/crear` solo recibe planes de cinco roles.

"Darle otro tope" a un impulso terminado no se construye: `impulso.lote` es único y ninguna fila del plan lo pide.
