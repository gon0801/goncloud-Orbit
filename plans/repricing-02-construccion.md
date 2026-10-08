# Cómo construir REPRICING 02

Esta guía te lleva de `master` al motor de precios encendido en Amazon México,
Amazon Estados Unidos y Mercado Libre. Cubre cada fila de
[REPRICING 02](repricing-02.md).

Qué construir está en el
[diseño](../docs/superpowers/specs/2026-10-08-repricing-02-design.md), en su
[bosquejo de tipos](../docs/evidencia/repricing-02/diseno/bosquejo.py) y en su
[modelo de datos](../docs/evidencia/repricing-02/diseno/datos.sql). Esta guía
dice dónde cae cada pieza en el código de hoy, qué prueba escribes primero y
cómo compruebas el paso. Las letras entre paréntesis son las secciones del
bosquejo. Si la guía y el diseño se contradicen, sigue el diseño y avisa al
lead. Hay una excepción: el orden de construcción lo fija el plan. El diseño
describe la forma final de la corrida y aquí se llega a ella en dos pasos, 0.b
y S.1.

Los nombres de símbolos son del commit `d83bb28`. Si un archivo ya cambió, busca
el símbolo por nombre.

## Antes de empezar

1. Comprueba que el PR que trae el diseño, el plan y esta guía está en `master`.
2. Lee el diseño completo. Lee del bosquejo las secciones de tu paso.
3. Levanta un Postgres local. Las pruebas con base no corren en un PR.
4. Trabaja cada paso en su rama, con un PR por paso.
5. Corre las herramientas de `tools/` como `PYTHONPATH=. python tools/<x>.py`.
   Sin `PYTHONPATH=.` fallan con `No module named 'app'`.

## Reglas que valen en todos los pasos

**Prueba primero.** Escribe la prueba, mírala fallar por la razón correcta y
después escribe el código. Registra en la evidencia un mutante por cada prueba
nueva: el cambio de una línea que la pone en rojo. Los mutantes obligatorios
están en la sección "Pruebas que deben discriminar" del plan.

**Lo que CI no ve en un PR.** En un PR solo corre el job `rapido`, sin Postgres.
La batería con base corre en el push a `master`, en el job `completa`. Antes de
abrir el PR, corre la batería en local con base:

	ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres PYTHONPATH=. python -m pytest -q -rs

Sin Postgres las pruebas con base se saltan y la corrida sale verde sin probar
nada. Revisa en la salida de `-rs` que no haya saltos con el motivo "sin
Postgres utilizable". Anota el comando y el conteo en la evidencia.

**Bases de prueba.** Las pruebas de precios arman su base con una lista fija de
migraciones (`ORDEN39`, `ORDEN59` y otras). Las pruebas nuevas usan una lista
que termina en `0047_*.sql`, `0053_*.sql` y `0054_*.sql`. La 0054 necesita la
0047 por la tabla `familia`.

**Migraciones.** Escribe `BEGIN;` y `COMMIT;` dentro del archivo y su reversa en
`NNNN_reversa_<nombre>.sql`. Los números ya están repartidos: 0053 y 0054 son del
paso 0.a, la 0055 es de F.3 y la 0058 es de S.1. Si un carril descubre que le
falta otra, usa 0056 para M y 0057 para G.

**Archivos de contrato.** `app/precio/tipos.py`, `app/precio/puerto.py`,
`app/precio/config.py`, `app/precio_mercados.py`, `app/estimacion_universo.py` y
las migraciones 0053 y 0054 quedan completos en 0.a. Después de 0.a solo se
editan para borrar lo que un paso deja de usar, y esta guía dice cuál paso borra
qué. Si necesitas agregar algo a uno de ellos, abre un PR aparte solo con ese
cambio y avisa al lead.

**Candados de arquitectura.** `tests/test_architecture.py` lo editan 0.a, 0.b y
el carril S. Los carriles F, M y G escriben los suyos en
`tests/test_arq_precio_<carril>.py`. Cada candado nuevo trae una prueba con una
fuga sembrada que demuestra que sí la detecta.

**Tamaño.** Ningún módulo de `app/` pasa de 900 líneas. `app/cli.py` tiene 891:
no le agregues subcomandos. Si una función llega al límite de complejidad,
pártela por lo que cada parte sabe. No uses `noqa`.

**Repo público.** Usa productos, SKUs y publicaciones inventados en pruebas,
fixtures y documentos. Ningún volcado de datos de producción entra al repo.

**Sin red en las pruebas.** Usa clientes falsos o `httpx.MockTransport`. Ningún
paso de construcción llama a Amazon ni a Mercado Libre. Las sondas F.0 y M.0 son
la excepción y solo leen.

**Evidencia.** Guarda comandos, salidas, mutantes y el SHA de cada paso en
`docs/evidencia/repricing-02/ejecucion/<id>/`.

**Producción.** El plan es la autorización completa del dueño: nadie le pide
permiso ni un go. En producción solo escriben los scripts de un PR aprobado y
mergeado, sobre una punta de `master` con el job `completa` en verde. Los
corres tú por `ssh goncloud` cuando claw te lo encarga, y guardas cada salida
con su código. El lead comprueba el resultado en solo lectura. Nadie escribe en
producción a mano, fuera de un script. Ningún script imprime una contraseña, un
token ni un DSN. Las consultas con `ORBIT_DSN_READ` las corres con una copia de
`docs/evidencia/repricing-01/E.0/correr.sh`. Cada paso que despliega trae
`desplegar.sh`, `rollback.sh` y `checklist.sh`, y un paso con migración trae
además `ensayo.sh`. Parte de los de `docs/evidencia/jev-ads-02/ejecucion/S.3/` y
sigue las reglas de la sección "Reglas que valen en todos los pasos" de
[la guía del bloque S de JEV ADS 02](jev-ads-02-bloque-s.md).

**Revisión y cierre.** Cada paso con código pasa una revisión cruzada con un
revisor distinto del autor antes del merge. Solo un hallazgo bloqueante y
reproducible abre otra ronda. Marca la fila del paso en `plans/repricing-02.md`
y agrega una entrada a `docs/CHAT-CONTEXT.md` en el mismo PR.

## Quién edita cada archivo compartido

Cada carril tiene sus archivos propios, listados al inicio de su sección. Estos
son los que tocan más de un paso, y en qué orden:

| Archivo | Quién lo edita |
| --- | --- |
| `tests/test_architecture.py` | 0.a, 0.b, S.1, S.3 y S.5 |
| `app/precio/tipos.py` | 0.a lo deja completo. S.1 borra el motivo `cuota`. S.7 borra los cuatro submotivos viejos de inventario |
| `app/ledger.py` | 0.a lo hace despachar por plataforma y cablea el INSERT de la atribución y su conteo. Después es del carril M |
| `app/estimacion_reader.py` | 0.b agrega `leer_cuenta`. Después es del carril F |
| `app/spapi/precio_mercado.py` | 0.b lo crea. Después es del carril F |
| `app/api_precios.py` | 0.a lo crea. S.1 le quita el cupo. Después es del carril G |
| `app/api_dashboard.py` (`salud`) y `salud.html` | Carril S |
| `app/config_write.py`, la ruta de config de `app/api_write.py`, `settings.html` y `settings.js` | S.2 |
| `tools/precio_goal.py` | Carril G. El paso 0.b no lo toca |
| `app/precio/objetivo.py` | Carril S. `derivar_ref_fijo` se queda ahí: la importan el adaptador de Amazon, `app/estimacion_reader.py` hasta F.3 y, hasta G.1, `tools/precio_goal.py` |
| `tools/precio_cobertura.py` y la llamada a `leer_publicaciones` de `app/api_precios.py` | 0.b, por el cambio de firma |
| `_precios_seguridad.html` | S.3 lo construye. G.3 solo lo incluye |
| `app/reputacion_clientes.py` | M.4, solo para exponer el refresco de token |
| `docs/DEPLOY.md` y la prueba de la línea de cron | S.3 para las líneas de D.2, S.7 para las de D.3 y M.4 para las de D.4 |

## 0.a: escribe los contratos sin cambiar el comportamiento

Este paso crea todo lo que los carriles comparten.

### Qué vas a encontrar

- `app/precio/tipos.py` importa `DetalleFee` de `app.estimacion_venta` y define
  `MOTIVOS_NO_EVALUADO` a mano. La estimación no exporta sus motivos: los emite
  como literales en `app/estimacion_{insumos,venta,ingest,repository,reader,fees}.py`.
- La lectura de config está repetida: `_numero` y `_fraccion` en
  `goals_write.py`, `_numero` y `_entero` en `fuentes.py`, `_numero_entero` y
  `validar_freno_dias_error` en `corrida.py`, `validar_cap` en `cuota.py` y
  `validar_precio_aviso_dias` en `notifica.py`.
- `tests/test_architecture.py` fija `EXCEPCIONES_PURAS_PRECIO` con una aserción
  de igualdad, exige que cada excepción exista como archivo y lista `corrida.py`
  en `ALLOWLIST_TAMANO`.
- `app/ledger.py` descarta las filas de Mercado Libre con el motivo
  "plataforma meli excluida". `_PLATAFORMA_ORIGEN` no trae `meli`.
- `/precios` vive en `app/templates/precios.html` y en `app/api_dashboard.py`.
  `tests/test_precio_pantalla.py` llama `dash.precios`, `dash._acciones_precio`,
  `dash._precios_de`, `dash._TOPE_RACHA_DIVERGENTE` y
  `dash.validar_precio_aviso_dias`.
- `datos.sql` solo es ejecutable en sus tablas, columnas, índices y permisos.
  Los triggers nuevos y los `CREATE OR REPLACE` están descritos en comentarios,
  y la siembra de config (§12) está comentada.
- `tests/test_schema_docs.py` compara los conteos de tablas y vistas de
  `verify/Launch.md` y `verify/Doctor.md` con el esquema.

### Pruebas primero

- **Convivencia.** Con la 0054 aplicada, el INSERT de `precio_decision` que hace
  hoy `corrida.persistir_decision` entra, y el INSERT de un escenario
  `disponible` que hace hoy `app/estimacion_repository.py` entra. Copia las
  columnas exactas de los dos INSERT de hoy.
- La migración rechaza: una muestra `familia` sin donantes, una decisión con un
  `goal_id` ya cerrado, una liberación para una corrida que no retuvo, y un
  UPDATE de `origen` sobre un goal ya sembrado.
- La migración sigue rechazando una decisión `live` bajo un goal `shadow`, que
  la 0039 ya rechazaba, y ahora acepta una decisión `shadow` bajo un goal
  `live`. El mutante es volver a `g.mode = NEW.mode`.
- La migración acepta un goal reeditado el mismo día.
- Después de la 0054, la última `config_version` trae `precio_modo_global` con
  valor `shadow` y conserva todas las claves que ya tenía.
- Sumar dos `Importe` de moneda distinta levanta `MonedasMezcladas`. `Cuenta` no
  se construye con el envío en MXN y el precio en USD.
- `EnvioImputado` no se construye con una muestra de origen `propio`.
- Una prueba recorre con `ast` los literales de motivo de los seis módulos de la
  estimación y exige que cada uno esté en `MOTIVOS_ESTIMACION`. El mutante es un
  motivo nuevo en `_motivo_universo`.
- El lector de config falla y nombra la clave que falta. `modo_de` da `off` para
  un universo sin entrada.
- `precio_mercados.abrir("meli")` levanta `ConfigInvalida` mientras el adaptador
  de Mercado Libre esté vacío.
- Con un atribuidor falso que devuelve una atribución, la ingesta deja una fila
  en `ledger_event_atribucion` para una venta nueva y para una venta ya
  ingerida. Corrida dos veces deja una sola fila por venta. Con un atribuidor
  que devuelve `None` no deja ninguna, `skip_reason` trae `venta_sin_atribuir`
  y `rows_skipped` no cambia.

### Cambios

1. Escribe `migrations/0053_precio02_canal_meli.sql` solo con el valor nuevo del
   enum `estimacion_canal`. Su reversa declara que el valor queda sin uso:
   Postgres no deja quitar un valor de enum.
2. Escribe `migrations/0054_precio02_base.sql` y su reversa. Lleva las tablas,
   columnas, índices, vistas y permisos de `datos.sql`, y además estas cuatro
   cosas que `datos.sql` solo describe:
   - Los tres `CREATE OR REPLACE`: `precio_decision_coherente` con los tres
     predicados de §7, `precio_cambio_sella_transicion` con `corrida_id` y
     `lote_reversa` como inmutables (§8), y `precio_goal_solo_cierra_vigencia`
     con `origen`, `lote` y `m_referencia` como inmutables (§9).
   - Los seis triggers nuevos de §5 y §6.
   - El INSERT de config de §12, sin comentar.
   - El bloque `DO` de §11, con todos sus casos.
3. No pongas en la 0054 los cuatro CHECK que exigen `l_origen`, `verificacion`,
   `logistica_origen` o los fees partidos. Van en la 0058 y en la 0055.
4. Crea `app/precio/puerto.py` con la sección B del bosquejo.
5. Agrega a `app/precio/tipos.py` la sección A completa. Conserva el motivo
   `cuota` y los cuatro submotivos viejos de inventario junto a los nuevos.
   `CambioPrevio` nace con `sin_efecto: bool = False`.
6. Crea en `app/estimacion_venta.py` el export `MOTIVOS_ESTIMACION` y arma
   `MOTIVOS_NO_EVALUADO` con él más los motivos propios del motor.
7. `app/precio/config.py` ya tiene los ayudantes `_numero`, `_fraccion` y
   `_entero`. Haz que las copias de los otros módulos los llamen, y agrega las
   claves nuevas de la sección H, con estas cotas: fracción del fusible de
   0.10 a 0.90, mínimo de movimientos de 1 a 100, salto de insumo de 0.01 a
   0.50, fracción de insumo de 0.05 a 0.90, errores seguidos de 2 a 20, tráfico
   mínimo de 20 a 5000, caída de cohorte de 0.05 a 0.90 y ventana de envío de 30
   a 365 días. `banda_desde_settings`, `max_dias_desde_settings`,
   `validar_freno_dias_error` y `validar_precio_aviso_dias` conservan su firma y
   llaman a esos ayudantes. No toques `validar_cap`: se borra en S.1.
8. Crea `app/precio_mercados.py` (S) y `app/estimacion_universo.py` (M) con las
   cuatro entradas registradas. Cada entrada apunta a un módulo que ya existe,
   aunque esté vacío: `app/spapi/precio_mercado.py`, `app/estimacion_amazon.py`,
   `app/ledger_atribucion.py` y, en `app/meli/`, `precio_mercado.py`,
   `write_client.py`, `catalogo.py`, `estimacion.py` y `ledger.py`.
9. Haz que `app/ledger.py` despache la atribución por plataforma con el
   protocolo `Atribuidor` (P). La ingesta inserta en `ledger_event_atribucion`
   con `ON CONFLICT DO NOTHING` cuando el atribuidor devuelve una atribución.
   Cuando devuelve `None`, no inserta y lo cuenta en `skip_reason` con el motivo
   `venta_sin_atribuir`. Ese conteo va en un contador aparte y no suma a
   `rows_skipped`, que sigue contando solo lo no escrito. La ingesta atribuye
   toda venta del plan, también la que ya estaba en `ledger_event`: busca su
   `id` por la llave de dedupe. Así la primera corrida con atribuidor llena la
   historia. En 0.a los dos atribuidores devuelven `None`. Mercado Libre sigue
   saliendo como excluida.
10. Parte `precios.html` en `_precios_seguridad.html` y `_precios_goals.html`.
    Mueve el bloque de precios de `app/api_dashboard.py` a `app/api_precios.py`.
    `api_dashboard.py` reexporta los cinco nombres que usa
    `tests/test_precio_pantalla.py`.
11. En `tests/test_architecture.py`, agrega el candado que deja a
    `app/precio_mercados.py` como el único módulo que importa los dos
    adaptadores.
12. Agrega `tests/test_arq_precio_*.py` al comando del job `rapido` de
    `.github/workflows/quality.yml`. Crea los cuatro archivos con una prueba
    vacía cada uno, para que el patrón tenga qué encontrar.
13. Actualiza los conteos de tablas y vistas de `verify/Launch.md` y
    `verify/Doctor.md` hasta que `tests/test_schema_docs.py` pase.

### Comprueba

- La batería con base pasa, sin saltos por falta de Postgres y sin cambiar
  ninguna aserción existente. `tests/test_precio_corrida.py` y
  `tests/test_precio_pantalla.py` pasan sin editarlos.
- `ejecucion/0.a/ensayo.sh` parte del de
  `docs/evidencia/jev-ads-02/ejecucion/S.3/`, que solo copia el esquema. Agrégale
  la carga de datos, también con `ORBIT_DSN_READ`:

		pg_dump "$DSN" --data-only --exclude-table-data="*_seq" \
			-t product -t listing -t config_version \
			-t estimacion_politica_version -t estimacion_escenario \
			-t "precio_*" > "$TMP/datos.sql"

  El rol de lectura no puede leer las secuencias. Sin `--exclude-table-data`,
  `pg_dump` falla con `permission denied for sequence product_id_seq`. El
  ensayo de referencia corre `pg_dump` dentro de `ssh goncloud '...'`, entre
  comillas simples. Por eso los patrones van entre comillas dobles.

  Carga ese archivo en la base local con `SET session_replication_role = replica`.
  El volcado no trae el valor de las secuencias. Después de cargar, ajusta la
  de cada tabla cargada, o el primer INSERT choca con un `id` que ya existe:

		select setval(pg_get_serial_sequence('<tabla>', 'id'),
			(select coalesce(max(id), 1) from <tabla>));

  Aplica la 0053 y la 0054 en dos corridas de `psql -v ON_ERROR_STOP=1`
  separadas. El archivo de datos se queda en `$TMP`.
- El ensayo exige tres cosas. Antes: `select count(*) from precio_envio_muestra`
  da 0. Después: `config_version` creció en una fila y
  `select settings ->> 'precio_modo_global' from config_version order by id desc limit 1`
  da `shadow`. Después: `precio_decision` conserva su conteo.

## 0.b: pon la corrida detrás del `Mercado`

Este paso es el de mayor riesgo del plan. Cambia el archivo más delicado del
motor sin cambiar lo que hace para MX FBA.

**El orden de la corrida no cambia en este paso.** La corrida sigue decidiendo
en memoria, reparte el cupo, guarda y aplica, como hoy. Guardar al decidir llega
en S.1, junto con el borrado del cupo. Las dos cosas no caben antes: el cupo
reescribe la decisión a `mantener(cuota)` antes de guardarla, y `precio_decision`
no admite UPDATE.

### Qué vas a encontrar

- `app/precio/corrida.py` importa `ProductFeesClient`, `cotizar_a_precio`,
  `leer_escenarios` y `app.spapi.precio_write`. Lee `spapi_price_observation`,
  `spapi_inventario_observation` y `spapi_listing_estado_observation` dentro de
  `armar_entrada` y de `_insumos_ventas`. `cerrar_huerfanas` también vive ahí.
- `app/spapi/precio_write.py` mezcla el protocolo de `precio_cambio`
  (`cambiar_precio`, `cerrar_por_observacion`, `revertir`) con lo que es de
  Amazon (`leer_precio_vivo`, `construir_cuerpo_parche`, `construir_escritor`).
- `app/precio/fuentes.py::_SQL_CANO` define la publicación activa de Amazon.
- Las pruebas importan lo que este paso mueve. `tests/test_precio_corrida.py`
  importa `armar_entrada`, `_insumos_ventas`, `_fase3_uno` e `_ingreso_60d`.
  `tests/test_precio_write.py` tiene 87 pruebas del protocolo.
  `tests/test_precio_pantalla.py` importa `cerrar_por_observacion`.
  `tests/test_precio_cobertura.py` usa `fuentes._SQL_CANO` y menciona
  `fase_E_envio_fbm` o `fase_M_meli` en cinco líneas.
- `objetivo.derivar_ref_fijo` la llaman `objetivo.paso` y, en `reglas.py`,
  `_subir` y `_bajar`. La importan `reglas.py`, `tools/precio_goal.py` y ocho
  líneas de `tests/test_precio_reglas.py`.
  `tests/test_precio_goals.py` corre la herramienta como subproceso.

### Pruebas primero

- Un `Mercado` falso basta para correr `correr` de punta a punta, sin `httpx` ni
  SP-API.
- `cambios.aplicar` llamada dos veces sobre la misma decisión escribe una vez. La
  segunda salta con `ya_aplicado`.
- `MercadoAmazon.catalogo` devuelve la misma lista que `_SQL_CANO` sobre el mismo
  fixture. `_SQL_CANO` toma la última observación de cada SKU y después filtra
  `BUYABLE`. Un fixture con un SKU cuya última observación no es `BUYABLE` y la
  anterior sí pone en rojo el orden invertido.
- `leer_cuenta` da los mismos importes que `armar_entrada` da hoy sobre el mismo
  escenario.
- Toda decisión guarda `goal_id` y `l_origen = 'politica'`. Una decisión `subir`
  o `bajar` guarda `verificacion = 'cotizada'`.
- El candado falla con un `import app.spapi` sembrado en `corrida.py`, en
  `cambios.py` o en `fuentes.py`.

### Cambios

1. Crea `app/spapi/precio_mercado.py` con `MercadoAmazon` (Q). Mueve ahí las
   lecturas de `spapi_*`, `_oferta_para_cotizar`, `_verificada` y `_detalle_fee`
   de `corrida.py`, y `_SQL_CANO` de `fuentes.py`. `Vitrina.insumos` lee por
   lote: una llamada por plataforma.
2. `MercadoAmazon.cotizar` entrega los fees ya partidos (`Fees.variable` y
   `Fees.fijo`). Para partirlos llama a `objetivo.derivar_ref_fijo`, que se
   queda en `objetivo.py` con sus pruebas. No la muevas ni la borres:
   `tools/precio_goal.py` la importa y este paso no toca esa herramienta.
   `reglas.decidir` y `objetivo.paso` dejan de llamarla, usan `Fees.variable` y
   `Fees.fijo`, y dejan de revisar monedas: `Cuenta` ya no se construye con
   monedas mezcladas. Hasta F.3 las columnas `fee_variable` y `fee_fijo` del
   escenario están vacías: en 0.b, `leer_cuenta` parte los fees del escenario
   con la misma `derivar_ref_fijo`. Agrega una prueba que fije lo de hoy: un
   escenario sin `ReferralFee` único y dentro de tolerancia sale
   `mantener(en_tolerancia)`, y el mismo escenario bajo el goal sale
   `no_evaluado(fee_error:referral_ausente)`.
3. Crea `app/precio/cambios.py` (I) con el protocolo que hoy está en
   `precio_write.py`, y mueve ahí `cerrar_huerfanas`. `precio_write.py` se queda
   con lo de Amazon y sigue siendo el único importador de `write_client`.
4. Agrega `leer_cuenta` a `app/estimacion_reader.py` (N). `armar_entrada` la
   llama. No toques `tools/precio_goal.py`: es del carril G.
5. `correr` recibe un `Mercado` en vez de `lector`, `escritor`, `fees`,
   `construir_cuerpo` y `limitador`, y conserva su orden de hoy.
   `persistir_decision` agrega `goal_id`, `l_origen` y `verificacion` a su INSERT.
6. Cambia `app/cli.py::_precio` para que acepte `--platform` repetido y abra cada
   mercado con `precio_mercados.abrir`. Para no pasar de 900 líneas, mueve
   `_precio` y `_clientes_precio` a un módulo nuevo `app/cli_precio.py`.
7. Haz que `fuentes.leer_publicaciones` reciba una `Vitrina` (L). Borra
   `FASE_FBM`, `FASE_MELI` y `_fase_fuera_de_alcance` de `cobertura.py`. Agrega
   `listing_id` a `DetalleSinGoal`.
8. En `tests/test_architecture.py`: extiende el candado de imports a
   `corrida.py`, `cambios.py` y `fuentes.py`. Agrega `cambios.py` a
   `EXCEPCIONES_PURAS_PRECIO`. Reescribe la lista de imports permitidos de
   `tools/precio_reversa.py`. Si `corrida.py` baja de 900 líneas, quítala de
   `ALLOWLIST_TAMANO`.
9. En las pruebas, cambia solo el armado de los clientes, los imports y los
   nombres de los puntos de sabotaje. Las aserciones `fase_*` de
   `tests/test_precio_cobertura.py` pasan a contar `universo_apagado`.

### Comprueba

- Escribe `ejecucion/0.b/comparar.sh`. Compara lo que decide el commit de 0.a
  con lo que decide el de 0.b sobre los mismos datos reales.

  El volcado es el del ensayo de 0.a más las tablas que la corrida lee. Tómalo
  y corre el script el mismo día UTC, antes de seis horas de la última
  estimación, que corre a las 00:45, 06:45, 12:45 y 18:45 UTC. Pasado ese
  tiempo la oferta se considera vieja y todo sale `no_evaluado`.

		-t spapi_listing_estado_observation -t spapi_price_observation \
			-t spapi_inventario_observation -t estimacion_oferta_observation \
			-t estimacion_fee_observation -t sku_cost -t ingest_run -t ledger_event

  Carga el volcado en dos bases locales. La base A lleva las migraciones hasta
  la 0052. La base B lleva además la 0053 y la 0054.

  La corrida de producción decide a las 13:10 UTC, y `correr` no vuelve a
  decidir un listing que ya tiene decisión del día. Un volcado posterior trae
  esas decisiones, y las dos bases devolverían las filas de producción sin que
  ningún commit decida nada. Por eso, antes de correr, el script borra en las
  dos bases lo que dejó la corrida de hoy:

		SET session_replication_role = replica;
		DELETE FROM precio_cambio WHERE decision_id IN
			(SELECT id FROM precio_decision WHERE decision_date = :'dia');
		DELETE FROM precio_decision WHERE decision_date = :'dia';
		DELETE FROM precio_cotizacion
			WHERE source_event_id LIKE 'precio-cotiz-%-' || :'dia' || '-%';
		SET session_replication_role = origin;

  El script aborta si después de borrar queda alguna decisión de hoy en
  cualquiera de las dos bases.

  Corre `correr` del commit de 0.a contra A y el del commit de 0.b contra B.
  Los dos usan la red falsa de las pruebas: `_RedFalsa` de
  `tests/test_precio_corrida.py`, importada con `PYTHONPATH=.:tests`. Ármala con
  el mapa de `source_event_id` a `seller_sku` de `estimacion_oferta_observation`.
  Sin ese mapa la cotización falla igual en los dos lados. Vuelca en cada base,
  a `a.txt` y `b.txt`:

		select listing_id, platform, resultado, motivo, canal, m_actual, goal,
			p_actual, p_objetivo, p_aplicado, i_valor, c_valor, f_valor, l_valor,
			r_valor, u15, u60, n15, n60, racha_senal, perdiendo, mode
		from precio_decision where decision_date = :dia order by listing_id

  Antes del `diff`, el script aborta si la comparación no prueba nada. Cuenta
  los goals vigentes de `amazon_mx` en la base A. Exige, en cada lado:

  - El `Resumen` que devuelve `correr` trae `decisiones` igual a ese conteo.
    Así las filas las decidió esta corrida y no venían en el volcado.
  - El conteo es mayor a cero y el archivo tiene esa cantidad de filas.
  - Al menos una fila trae `m_actual`.
  - Ninguna fila trae un motivo `fee_error:`. Si lo trae, la red falsa está
    mal armada.

  Esperado: 6 filas por lado con los goals de hoy, y `diff` vacío. Las columnas
  nuevas quedan fuera de la comparación a propósito.

  **El script tiene que probar que puede fallar.** Córrelo una vez más con el
  lado B roto a propósito: haz que `armar_entrada` levante una excepción.
  Esperado: el script sale en rojo. Guarda las dos salidas, la verde y la
  roja, en la evidencia. Un `comparar.sh` que sale verde con el lado B roto no
  cierra el paso.
- `tests/test_precio_corrida.py`, `tests/test_precio_write.py` y
  `tests/test_precio_cobertura.py` conservan todas sus aserciones.
- Esta compuerta sale vacía. Hoy, antes del paso, da 8 líneas:

		git grep --untracked -nE \
			"(from|import) app\.(spapi|meli|estimacion_fees)|import httpx|spapi_[a-z_]+_observation" \
			-- app/precio/corrida.py app/precio/cambios.py app/precio/fuentes.py

  `--untracked` hace que también revise `cambios.py`, que es nuevo. No dejes el
  nombre de una tabla `spapi_*` en un comentario de esos tres archivos.

## Carril S: seguridad sin cupo y avisos

Archivos del carril: los de `app/precio/` llamados `corrida`, `cambios`,
`compuerta`, `liberaciones`, `reglas`, `objetivo`, `ventas` y `fuentes`. Además,
el bloque de precios de `app/notifica.py`, `_precios_seguridad.html`,
`tools/precio_reversa.py` y los de la tabla "Quién edita cada archivo
compartido".

### S.1: borra el cupo y guarda al decidir

**Qué vas a encontrar.** `cuota.reservar` se llama al aplicar. `repartir_cupo`
separa decidir de aplicar. En `tests/test_precio_corrida.py`, cuatro pruebas lo
usan como punto de sabotaje, dos lo espían y cinco exigen filas
`mantener(cuota)`. Once pruebas de `tests/test_precio_reglas.py` lo llaman
directo. `tests/test_precio_d0.py` simula `validar_cap`. `app/api_precios.py`
usa `validar_cap` y `motor_cuota`. `/salud` pinta "cupo X de Y".
`tests/test_architecture.py` exige que `cuota.py` exista y fija las tablas que
escribe `corrida.py` en `_ESCRIBE_CORRIDA`.

**Pruebas primero.**

- Con 8 decisiones `live` que mueven precio, las 8 se escriben.
- Las decisiones se guardan antes de aplicarse. Una segunda corrida el mismo día
  no decide lo ya decidido y aplica solo lo pendiente.
- Cada ejecución abre y cierra una fila de `precio_corrida`. Una fila `abierta`
  de una corrida anterior pasa a `abortada`.
- Una config sin `precio_cap_*` no detiene la corrida.
- La 0058 rechaza una decisión con `l_valor` y sin `l_origen`, y una decisión
  `subir` sin `verificacion`.
- Las cuotas de Ads en `apply_quota_state` no cambian durante una corrida.

**Cambios.** Reescribe `correr` en tres pasos sobre filas guardadas: decidir,
compuerta y aplicar (J). En este paso la compuerta siempre deja pasar. Borra
`app/precio/cuota.py`, `reglas.repartir_cupo`, `corrida._cuota_reescrita`,
`_ingreso_60d`, la prioridad y el motivo `cuota` de `MOTIVOS_MANTENER`. Quita el
cupo de `app/api_precios.py` y de `salud.html`. Borra las pruebas del cupo, de
`_ingreso_60d` y de `validar_cap`. Mueve el punto de sabotaje de las cuatro
pruebas al paso de la compuerta. En `tests/test_architecture.py`, quita
`cuota.py` de las excepciones y agrega `precio_corrida` a `_ESCRIBE_CORRIDA`.
Escribe `migrations/0058_precio02_decision_exige.sql` con los dos CHECK del
final de `datos.sql`. No toques `apply_quota_state`, sus triggers ni
`apply_cap_de_config`: son de Ads. No borres `precio_cap_*` de la config.

**Comprueba.** Esta compuerta cuenta lo que queda del cupo, comentarios incluidos:

	{ test -e app/precio/cuota.py && echo "FALTA: app/precio/cuota.py sigue ahi"; \
		git grep --untracked -nE \
		"repartir_cupo|precio_cap_|cuota_mod|cuota_precio|validar_cap|motor_cuota|_cuota_reescrita|_ingreso_60d|pr\.cuota|\"cuota\"" \
		-- app tools; } | wc -l

Esperado: `0`. Hoy, antes del paso, da más de 30.

### S.2: construye el apagador

**Qué vas a encontrar.** El único modo de hoy es `precio_goal.mode`. Desde la
0054, `precio_decision_coherente` acepta una decisión `shadow` bajo un goal
`live`. El precedente está en `app/optimizer/goals.py`. Copia la regla, no la
importes. `/settings` hoy solo edita claves de Ads: `app/config_write.py` no
conoce ninguna clave `precio_*` y la ruta es
`POST /api/ads-optimizer/settings/{platform}`.

**Pruebas primero.**

- `modo_efectivo` da el menor de los tres en `off < shadow < live`.
- Con el global en `shadow` y el goal en `live`, la decisión se guarda `shadow` y
  deja un cambio virtual, sin escritura.
- Con el global en `off`, la corrida cierra huérfanas y cambios enviados, y no
  decide.
- Si la config cambia a `off` entre dos escrituras, la segunda no ocurre y deja
  una fila en `precio_retencion` con causa `apagador`.
- Una decisión `live` retenida cuyo goal pasó a `shadow`, o se cerró, no se
  escribe en el repaso y deja una retención `goal_cambiado`.
- Guardar `precio_modo_global = off` desde la ruta de settings inserta una
  `config_version` que conserva todas las demás claves.
- Un modo fuera de `off`, `shadow` y `live`, o un universo que no está en el
  registro, responde 422. Sin token responde 401.

**Cambios.** Escribe `compuerta.modo_efectivo` (E). Al decidir, calcula el modo
de cada unidad y guárdalo en la decisión. Al aplicar, relee la config y el goal
de la decisión antes de cada escritura real. Haz que
`app/config_write.py::proxima_config` acepte `precio_modo_global` y
`precio_modo_universo`, este último como un objeto de `plataforma/canal` a modo.
Agrega los dos al cuerpo de `POST /api/ads-optimizer/settings/{platform}`, la
ruta que ya existe. No abras otra. La config es global: los dos se guardan
igual desde cualquier plataforma, y la ruta sigue aceptando solo `amazon_us` y
`amazon_mx`. El modo de `meli/meli` se guarda con una de esas dos y su llave en
`precio_modo_universo`. Agrega sus campos a `settings.html` y a `settings.js`.

**Comprueba.** El DoD de la fila S.2.

### S.3: construye la compuerta

**Qué vas a encontrar.** Desde S.1 las decisiones se guardan antes de aplicarse.
Una decisión `subir` sin cambio ya es legal: pasa hoy con `precio_vivo_distinto`.
`tests/test_precio_corrida.py::LINEA_CRONTAB_PRECIO` fija la línea de cron que
debe aparecer en `docs/DEPLOY.md`.

**Pruebas primero.**

- 80 intenciones nuevas sobre 158 unidades medidas retienen. 79 no retienen.
- Una unidad `no_evaluado` no cuenta en las medidas.
- Una decisión `subir` cuya decisión anterior ya iba hacia el mismo goal no
  cuenta como nueva.
- Un costo que saltó en 60 de 158 unidades retiene con causa `insumo_sistemico`
  e insumo `costo`.
- Con FBA y FBM en la misma plataforma, un salto que solo afecta a FBM retiene
  FBM y deja pasar FBA.
- Tras `liberar`, la corrida siguiente aplica las decisiones de hoy retenidas. Si
  ya es otro día, no aplica las de ayer.
- `liberar` dos veces devuelve la misma fila. `liberar` una corrida que no retuvo
  levanta `CorridaSinRetencion`.
- `Cortacircuito` abre tras el número de errores seguidos de la config. Un éxito
  lo reinicia.
- Un repaso sin nada pendiente no llama al `Mercado`.

**Cambios.**

1. Escribe `app/precio/compuerta.py` (E) como módulo puro.
2. Escribe la medición de la compuerta: arma `Intencion` y `Deriva` desde lo
   guardado hoy y escribe una fila de `precio_compuerta_medicion` por universo.
3. Escribe `app/precio/liberaciones.py`, el único escritor de
   `precio_compuerta_liberacion`, con rol `app_admin`. Entrega `liberar` al
   carril G, que cablea la ruta.
4. Escribe `fuentes.EstadoSeguridad` (T) y `_precios_seguridad.html`: los modos,
   la última corrida, las retenciones del día y el botón **Soltar**.
5. En `tests/test_architecture.py`, agrega `liberaciones.py` a las excepciones y
   las tablas nuevas a `_ESCRIBE_CORRIDA`.
6. Actualiza `LINEA_CRONTAB_PRECIO` y `docs/DEPLOY.md` a las dos líneas de MX que
   instala D.2.

**Comprueba.** El DoD de la fila S.3.

### S.4: deja de congelar por errores sin efecto

**Qué vas a encontrar.** `cambios_previos` cuenta un cambio en `error` para el
cooldown, y por eso `frenado(api_error)` no se alcanza. `cerrar_huerfanas` sella
`error` sin leer el precio. `cerrar_por_observacion` no filtra por plataforma.

**Pruebas primero.** Las cinco del DoD de la fila S.4, cada una con su fixture de
`readback_precio`.

**Cambios.** Calcula `CambioPrevio.sin_efecto` al leer, con las columnas que ya
existen (`datos.sql` §8). El cooldown de `reglas.decidir` no cuenta un cambio
`sin_efecto`. `cambios.cerrar_huerfanas` lee el precio vivo y resuelve cada
huérfana. `cambios.cerrar_por_observacion` recibe la `Vitrina` y cierra solo lo
de esa plataforma.

**Comprueba.** El DoD de la fila S.4.

### S.5: construye la reversa en lote

**Qué vas a encontrar.** `tools/precio_reversa.py` solo acepta `--cambio-id`. Su
huella incluye la clasificación que sale de leer el precio vivo, así que un
precio que cambia invalida todo el lote. `revertir` exige que el original esté
cerrado.

**Pruebas primero.** Las cinco del DoD de la fila S.5.

**Cambios.** Escribe `Seleccion`, `planear_reversa` y `revertir_lote` en
`cambios.py` (I). La huella cubre la selección y el precio al que vuelve cada
fila, no el precio vivo. `revertir_lote` toma el mismo candado
`precio:<platform>` que la corrida. Aborta si el modo efectivo de algún universo
del lote es `live`. Deja `tools/precio_reversa.py` como despachador, con
`--cambio-id`, `--fecha` con `--platform`, y `--corrida`. Reescribe en
`tests/test_architecture.py` la lista de imports permitidos de la herramienta.

**Comprueba.** El DoD de la fila S.5.

### S.6: manda los avisos

**Qué vas a encontrar.** `notifica_precio` es el único canal y nunca levanta.
`TIPOS_PRECIO` no tiene aviso por precio movido, por cambio en `error` ni por
reversa.

**Pruebas primero.** Las cinco del DoD de la fila S.6. Agrega una más: el texto
de cada aviso nuevo no contiene costo, margen ni goal.

**Cambios.** Agrega a `TIPOS_PRECIO` los tipos `resumen_diario`, `compuerta`,
`cambio_error`, `corte_errores`, `reversa` y `cohorte` (U), cada uno con su
función pura que arma el texto. Antes de enviar, comprueba que no exista ya un
sello del mismo día: de la plataforma para el resumen, y del universo y la causa
para la compuerta. Sella `resumen_enviado_at` o `avisada_at` después de enviar.
Un envío que falla no deja sello y el repaso lo reintenta. Agrega a `/salud` el
aviso de que falta la corrida cerrada del día.

**Comprueba.** El DoD de la fila S.6.

### S.7: cambia la señal de ventas a la unidad

Este paso espera a F.1 y a F.4. Sin la atribución de ventas (F.1) y sin el
adaptador que arma la historia de cada unidad (F.4), la señal saldría `sin_dato`
para todo. Hasta S.7 el motor usa la señal por producto de hoy. Se despliega en
D.3.

**Qué vas a encontrar.** `ventas.evaluar_senal` recibe ventas por producto y
exige inventario FBA todos los días.

**Pruebas primero.** Las cuatro del DoD de la fila S.7.

**Cambios.** `evaluar_senal` recibe `HistoriaUnidad` y usa
`DiaUnidad.disponibilidad` (D). Agrega la evidencia `trafico`, que solo se
evalúa después de una subida propia. Escribe `evaluar_cohorte`, que solo avisa.
`reglas.decidir` acepta `lineal=True` (F). Borra de `tipos.py` los cuatro
submotivos viejos de inventario. No toques el adaptador: lo entrega F.4.
Actualiza `LINEA_CRONTAB_PRECIO` y `docs/DEPLOY.md` a las líneas que instala
D.3. Este paso va después de S.3, así que no las pisa.

**Comprueba.** El DoD de la fila S.7.

## Carril G: goals en pantalla

Archivos del carril: `app/precio/siembra.py`, `app/precio/goals_write.py`,
`app/api_precios.py` después de S.1, `app/api_precios_write.py`,
`_precios_goals.html`, `app/static/js/precios.js` y `tools/precio_goal.py`.

### G.1: mueve el plan de goals a `app/`

**Qué vas a encontrar.** Las protecciones viven en `tools/precio_goal.py`:
`_referencia`, `_guardas_del_plan`, `_huella` y `UMBRAL_SALTO`. La herramienta
siembra fila por fila sin transacción global. `goals_write.py` es el único
escritor de `precio_goal` y editar es cerrar y sembrar.
`tests/test_precio_goals.py` fija tres comportamientos que este paso cambia:
resembrar el mismo día falla, un listing con goal vigente aborta, y lo sembrado
antes de un fallo queda sembrado.

**Pruebas primero.**

- `planear` con `PedidoMargenDeHoy` propone el margen de hoy de cada unidad.
- Una unidad con margen de hoy fuera de la banda sale con el bloqueo
  `fuera_de_banda`, confirmable. Una sin escenario sale `sin_escenario`, no
  confirmable.
- Un precio objetivo a más de 25 % del actual sale `salto_mayor_25`.
- Un listing con goal vigente sale con la acción `reemplazar`.
- `aplicar_plan` con una huella distinta levanta `HuellaDistinta` y no escribe.
- Si la tercera fila de cinco falla, no queda ninguna.
- Sembrar y volver a sembrar el mismo día deja un goal vigente y uno anulado.
- `live` sin go literal no escribe.

**Cambios.** Escribe `app/precio/siembra.py` (G) como módulo puro. Agrega
`leer_candidatos` y `aplicar_plan` a `goals_write.py` (K). `aplicar_plan`
recalcula el plan adentro y escribe todo en una transacción. Deja
`tools/precio_goal.py` como despachador: borra de ahí `_componente`,
`_detalle_fee`, `_guardas_del_plan` y `UMBRAL_SALTO`, y reescribe `_referencia`
sobre `leer_cuenta`. Reescribe las tres pruebas de `tests/test_precio_goals.py`
que fijaban el comportamiento viejo y declara los tres cambios en el PR.

**Comprueba.** `tests/test_precio_goals.py` pasa con su lista de migraciones
terminada en la 0054. La prueba contra la base real necesita `leer_cuenta`, que
llega con 0.b.

### G.2: abre las rutas

**Qué vas a encontrar.** `app/api_write.py` trae `exige_token` y
`ConexionEscritura`. El token se valida antes de abrir la conexión admin. La
edición en bloque de `/familias` acepta hasta 500 ids.

**Pruebas primero.** Las cinco del DoD de la fila G.2.

**Cambios.** Escribe `app/api_precios_write.py` con `POST /api/precios/goals/plan`,
`POST /api/precios/goals/aplicar` y
`POST /api/precios/compuerta/{corrida_id}/liberar` (T). Las tres usan
`exige_token`. Los cuerpos son modelos del router y no salen de él.

**Comprueba.** El DoD de la fila G.2.

### G.3: construye la pantalla

**Qué vas a encontrar.** `/precios` no usa JavaScript. `app/static/js/settings.js`
y `app/static/js/familias.js` son el patrón: `fetch` con JSON, token tecleado en
un campo de contraseña y sin guardarlo. La política de contenido solo permite
scripts de `/static`. El repo no tiene con qué probar JavaScript.

**Pruebas primero.** Prueba del lado del servidor: la respuesta de `/precios`
trae, por unidad, margen de hoy, goal, precio objetivo, origen del envío y
TACoS, y trae el bloque de seguridad de S.3.

**Cambios.** Escribe `app/static/js/precios.js` y `_precios_goals.html`. Agrega
casillas, la acción en bloque y el campo del go literal para pasar a `live`.
Muestra el origen del envío con una marca distinta cuando es `familia` o
`marketplace`. Incluye `_precios_seguridad.html` sin cambiarlo.

**Comprueba.** Haz esta prueba manual contra la base de prueba y guarda una
captura de cada paso en la evidencia:

1. Siembra un goal a una unidad sin goal. Esperado: aparece con ese goal.
2. Cambia el goal de otra unidad. Esperado: un goal vigente y el anterior
   cerrado.
3. Elige "margen de hoy" para todos los sin goal. Esperado: la pantalla muestra
   el plan con sus bloqueos y el botón **Aplicar** se habilita solo después.
4. Aplica. Esperado: los sin bloqueo quedan sembrados y los bloqueados siguen
   listados con su motivo.

## Carril F: FBM de México y Estados Unidos

Archivos del carril: `app/estimacion_amazon.py`,
`app/estimacion_{insumos,fees,ingest,repository,venta}.py`,
`app/estimacion_reader.py` y `app/spapi/precio_mercado.py` después de 0.b,
`app/precio/envio.py`, `app/envio_muestras.py`, el atribuidor de Amazon de
`app/ledger_atribucion.py` y `tools/precio_sonda.py`.

### F.0: lee tres cosas de producción

Guarda cada script o consulta, su salida literal y la conclusión en
`ejecucion/F.0/`.

1. **Identidades de los cargos de envío.** Corre
   `docs/evidencia/repricing-01/E.0/consultas/00-sonda-formato-90d.sql` con una
   copia de `E.0/correr.sh`. La identidad de un cargo sale de `source_event_id`
   partido por `|`: el campo 6 si el campo 2 es `finance`, y si no, el campo 2.
   Esperado: las seis identidades de la tabla de fuentes de
   `docs/evidencia/repricing-01/E.0/veredicto.md` y ninguna más. Si aparece
   otra, anótala y avisa al lead antes de F.2.
2. **Product Fees para FBM.** `ProductFeesClient` no sirve para esto: fija
   `IsAmazonFulfilled` en verdadero y rechaza la respuesta si no lo es. Escribe
   `ejecucion/F.0/sonda_fees_fbm.py`, que arma el cuerpo con `IsAmazonFulfilled`
   en falso y el `MarketplaceId` de cada universo, y lo manda con
   `SpapiClient.post_fees`. Córrelo dentro del contenedor, que es donde viven
   las credenciales:

		ssh goncloud 'docker exec -i orbit-app-1 python - --sku <SKU FBM de MX> --sku-us <SKU de US>' \
			< docs/evidencia/repricing-02/ejecucion/F.0/sonda_fees_fbm.py

   Es una consulta a Amazon que no cambia nada. Esperado por SKU: el estado, la
   lista de tipos de fee y si trae impuesto. Si el estado no es `Success`,
   registra ese universo sin cotizador.
3. **FBM sin existencias.** Escribe `ejecucion/F.0/03-fbm-sin-stock.sql`. Cruza
   la última fila de `disponibilidad_observation` con cantidad 0 contra el
   último `status` de `spapi_listing_estado_observation` del mismo SKU.
   Esperado: una lista de SKUs con su estado. Si alguno sigue `BUYABLE` sin
   existencias, la disponibilidad de FBM debe mirar también la cantidad: avisa
   al lead antes de F.4.

### F.1: atribuye las ventas de Amazon a su publicación

**Qué vas a encontrar.** `ledger_event` es append-only y sus ventas traen
`product_id` y `order_id`, no `listing_id`. `spapi_order_observation` guarda
`fulfillment_channel` por orden. `ingest_run` solo tiene `rows_written`,
`rows_skipped`, `skip_reason` y `llamadas`.

**Pruebas primero.** Las cinco del DoD de la fila F.1.

**Cambios.** Escribe el atribuidor de Amazon en `app/ledger_atribucion.py` (P).
No toques `app/ledger.py`: desde 0.a la ingesta ya inserta lo que el atribuidor
devuelve y cuenta lo que devuelve como `None`.

**Comprueba.** En una copia de producción, cuenta las ventas por `resuelto_por`.
Anota cuántas quedan sin atribuir. Corre la ingesta otra vez después de cargar
el dato que faltaba y comprueba que esas ventas ya se atribuyen.

### F.2: mide el envío con su origen

**Qué vas a encontrar.** Todos los cargos de envío caen como
`fee_type='shipping_fee'`, en MXN y con signo negativo. En México casi todo el
envío FBM es `finance:MFNPostageFee`: 649 órdenes que no traen otra etiqueta.
`precio_envio_muestra` existe desde la 0039 y está vacía.

**Pruebas primero.**

- `costo_de_orden` con etiqueta 450, Labman 449 y HB 80 da 530.
- Una orden de MX con solo `MFNPostageFee` 91 da 91.
- `ShippingChargeback`, `MFNShippingChargeback` y una identidad desconocida caen
  en `otros`, no entran al costo y se cuentan.
- Una orden con dos productos no entra a ninguna muestra y se cuenta.
- La escalera: un envío propio da `propio`. Sin propios y con hermanos de
  familia, `familia`, con la mediana de las medianas. Sin familia,
  `marketplace`. Sin un solo envío en el universo, no hay muestra.

**Cambios.** Escribe `app/precio/envio.py` (C) como módulo puro.
`clasificar_cargo` usa la identidad del punto 1 de F.0. Escribe
`app/envio_muestras.py` (O) con su propio `main`, como `app/estimacion_ingest.py`.
Se corre así:

	python -m app.envio_muestras --platform amazon_mx --canal fbm

El job escribe una muestra por cada producto con publicación en esa plataforma.
No depende de la estimación para saber qué productos hay. Inserta solo si la
muestra cambió.

**Comprueba.** El job corrido dos veces el mismo día no escribe la segunda vez.

### F.3: abre los universos en la estimación

**Qué vas a encontrar.** MX FBA está fijo en estos lugares:

- `estimacion_insumos._motivo_universo`.
- En `estimacion_fees.py`: `_UNIVERSO_SOPORTADO`, `MARKETPLACE_MX`,
  `IsAmazonFulfilled` en el cuerpo, el mismo literal en la plantilla de bytes, y
  el validador que rechaza una respuesta sin `IsAmazonFulfilled` verdadero.
- El `WHERE platform = 'amazon_mx'` de `estimacion_ingest.py`.
- `_ultimo_contexto_fba_mx` en `estimacion_repository.py`.
- La única política de `estimacion_politica_version`.
- `logistica` leída como constante en `estimacion_venta.py`.

**Pruebas primero.** Las seis del DoD de la fila F.3.

**Cambios.** Llena `universo_de` y `logistica_del_universo` en
`app/estimacion_universo.py` (M) y el cotizador en `app/estimacion_amazon.py`.
Reemplaza los lugares de arriba por llamadas al registro. El escenario guarda
`logistica_origen`, `envio_muestra_id`, `fee_variable`, `fee_fijo` y
`fee_cotizable`. En Estados Unidos, convierte costo y envío de MXN a USD con
`convertir` y la tasa del día. Escribe
`migrations/0055_precio02_escenario_exige.sql` con los dos CHECK del final de
`datos.sql`. Deja listo el script que inserta las políticas
`amazon_mx/fbm` y `amazon_us/fbm` con los valores de la tabla "Políticas por
universo" del plan.

**Comprueba.** En local, una oferta FBM de MX y una de US producen escenario
`disponible`.

### F.4: completa el adaptador de Amazon y escribe la sonda

**Pruebas primero.** Las cuatro del DoD de la fila F.4.

**Cambios.** En `MercadoAmazon.insumos`, calcula la disponibilidad según el canal
de la unidad, lee las ventas de `v_precio_venta_unidad` y arma `HistoriaUnidad`
con las exposiciones y los clics de `ads_product_metric_observation`. Escribe
`tools/precio_sonda.py`, genérica sobre `Mercado`: simula por omisión y, con
`--acepto-mutacion-real` y `--go`, mueve una publicación un centavo y la
revierte.

**Comprueba.**
`PYTHONPATH=. python tools/precio_sonda.py --platform amazon_us --listing-id <id>`
imprime el plan y no escribe.

## Carril M: Mercado Libre

Archivos del carril: todo `app/meli/`, `app/ledger.py` después de 0.a y la
función de refresco de token de `app/reputacion_clientes.py`.

### M.0: sondea Mercado Libre sin escribir

Córrela dentro de `orbit-app-1`. Un 401 hace que `ClienteMeli` refresque el
token y reescriba el archivo de tokens. Si corres la sonda con una copia de los
secretos fuera del contenedor, ese refresco deja inservible el token de
producción.

Escribe `ejecucion/M.0/sonda_meli.py` sobre `ClienteMeli.get`, con una consulta
por pregunta, y córrela así:

	ssh goncloud 'docker exec -i orbit-app-1 python -' \
		< docs/evidencia/repricing-02/ejecucion/M.0/sonda_meli.py

Antes de guardar cada respuesta, quita tokens, el id del vendedor y datos de
comprador. Escribe cuatro conclusiones:

1. De dónde sale el SKU de cada variante. Consulta una publicación con variantes
   y una sin variantes.
2. Si hay una consulta de cargos por precio, categoría y tipo de publicación que
   separe el porcentaje del cargo fijo.
3. Si el precio de una publicación con variantes va una vez o por variante.
4. Si el token permite escribir. Léelo de los permisos que declara la aplicación.
   No intentes escribir para averiguarlo.

Los nombres de las consultas de la API los eliges tú. Anota en la evidencia cada
ruta que usaste. Si no hay consulta de cargos, registra `meli/meli` sin
cotizador.

### M.1: ingiere el catálogo

**Qué vas a encontrar.** `fetch_meli` en `app/reputacion.py` ya pide cada
publicación a diario y solo guarda `health`. No hay filas `meli` en `listing`.

**Pruebas primero.** Las cuatro del DoD de la fila M.1.

**Cambios.** Escribe `app/meli/catalogo.py` con su propio `main`. Se corre con
`python -m app.meli.catalogo`. Por publicación activa, inserta o actualiza
`listing` con el producto ancla, sus filas de `listing_miembro` y una fila de
`meli_publicacion_observation`. Cruza el SKU de cada variante con
`product.odoo_sku`.

**Comprueba.** Cuenta en la evidencia las publicaciones con todos sus miembros
mapeados, con alguno sin mapear y sin ninguno.

### M.2: abre el ledger de Mercado Libre

Haz la auditoría antes de escribir código.

1. Lista cada vista, consulta y reporte que lee `ledger_event` agrupando por
   plataforma. Empieza por `git grep -n "ledger_event" -- app migrations`, que
   hoy devuelve 83 líneas en 16 archivos.
2. Escribe en `ejecucion/M.2/auditoria.md` qué verá cada uno cuando existan filas
   `meli`, y cuál necesita un filtro.
3. Corrige lo que haga falta en el mismo PR.

**Pruebas primero.** Las del DoD de la fila M.2.

**Cambios.** Agrega `meli` a `_PLATAFORMA_ORIGEN` en `app/ledger.py`. Escribe el
atribuidor de Mercado Libre en `app/meli/ledger.py` y el mapeo de sus tipos de
cargo. Mide en la evidencia la retención que cobra Mercado Libre: es el
`isr_tasa` de la política `meli/meli`.

### M.3: estima el margen de `meli/meli`

**Pruebas primero.** Las tres del DoD de la fila M.3.

**Cambios.** Escribe `app/meli/estimacion.py`: el escenario por publicación, con
una cuenta por miembro y la del miembro que manda en la fila. El envío sale de
`precio_envio_muestra` con la escalera de F.2. Deja listo el script
que inserta la política `meli/meli` con los valores de la tabla "Políticas por
universo" del plan y la retención que midió M.2.

### M.4: construye el adaptador y el cliente de escritura

**Qué vas a encontrar.** En `app/reputacion_clientes.py`, el refresco de token es
el método privado `_refresh` de `ClienteMeli`.

**Pruebas primero.** Las cuatro del DoD de la fila M.4. Agrega una: con la forma
sin sellar, solo una llamada con `sonda=True` llega a la red.

**Cambios.** Expón el refresco de token de `app/reputacion_clientes.py` como una
función pública, sin darle a `ClienteMeli` ningún método que no sea GET. Escribe
`app/meli/write_client.py` con `MeliWriteClient` (R): recibe ese refresco y nunca
lee credenciales. Escribe en él la forma candidata que concluyó M.0 y deja
`FORMA_ESCRITURA_MELI = "pendiente_sonda"`. Con esa constante, `poner_precio`
levanta `EscrituraNoDisponible` salvo que reciba `sonda=True`. Escribe
`MercadoMeli` en `app/meli/precio_mercado.py`. Agrega en
`tests/test_arq_precio_m.py` los candados: ningún `.put(` fuera de
`write_client.py`, un solo importador del cliente, `ClienteMeli` solo con GET, y
`sonda=True` solo en `tools/precio_sonda.py`. Agrega a `docs/DEPLOY.md` las
líneas de cron que instala D.4.

## Despliegues

Cada despliegue lo corres tú cuando claw te lo encarga, con los scripts de su
paquete y en este orden: `ensayo.sh` si hay migración, `desplegar.sh <punta>` y
`checklist.sh <sello>`. Si `ensayo.sh` falla, no despliegues. No saltes ni
edites una guarda de `desplegar.sh`. El lead comprueba cada despliegue en solo
lectura.

Cuándo se corre `rollback.sh <sello>`:

- La aplicación quedó caída: `/health` distinto de 200 en dos lecturas
  separadas 60 segundos.
- `desplegar.sh` abortó después de tocar el código, la base o el crontab. Su
  mensaje de fallo dice qué reversa corre: síguelo.
- `checklist.sh` sale 1 y su línea `FALLA` es algo que este despliegue cambió.

Una `FALLA` de algo que este despliegue no tocó no se revierte: se reporta.
Después de una reversa no se despliega ni se enciende nada hasta que el dueño
decida.

Cada `desplegar.sh` instala sus líneas de cron él mismo. Parte del bloque de
respaldo y `diff` de `docs/evidencia/jev-ads-02/ejecucion/S.4/desplegar.sh`. El
paso manual del dueño de ese script ya no aplica: el script respalda el crontab,
cambia las líneas, comprueba que el `diff` trae solo las previstas y, si trae
otra cosa, restaura el respaldo y aborta.

**D.1** lleva la 0053, la 0054 y el código de 0.b. Aplica las dos migraciones en
dos corridas de `psql` separadas. No cambia el cron. Al día siguiente corre:

	ssh goncloud 'docker exec orbit-app-1 python -m app.cli precio --reporte --platform amazon_mx --desde <ayer> --hasta <hoy>'

Esperado: 6 decisiones cada día sobre los mismos listings. Anota el insumo que
explica cada diferencia de resultado.

**D.2** lleva S.1 a S.6, el carril G y la 0058. Reemplaza la línea de cron de
precios por estas dos:

	10 13 * * * /usr/bin/flock -n /tmp/precio-spapi.lock docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1
	10 15-23/2 * * * /usr/bin/flock -n /tmp/precio-spapi.lock docker exec orbit-app-1 python -m app.cli precio --platform amazon_mx >> /mnt/data/appdata/orbit/logs/precio-corrida.log 2>&1

**D.3** lleva el carril F, S.7 y la 0055. Corre el script de políticas. Agrega
`--platform amazon_us` a las dos líneas de D.2. Agrega el job de muestras antes
de la estimación de las 12:45 UTC:

	15 12 * * * docker exec orbit-app-1 python -m app.envio_muestras --platform amazon_mx --canal fbm >> /mnt/data/appdata/orbit/logs/precio-muestras.log 2>&1
	20 12 * * * docker exec orbit-app-1 python -m app.envio_muestras --platform amazon_us --canal fbm >> /mnt/data/appdata/orbit/logs/precio-muestras.log 2>&1

El día del despliegue no hay muestras hasta las 12:15 UTC. El checklist sale 3
hasta la primera corrida del job y de la estimación, y 0 después.

**D.4** lleva el carril M. Corre el script de la política `meli/meli`. Agrega el
catálogo diario, el job de muestras de `meli` y las dos líneas de precios de
Mercado Libre, con su propio archivo de candado:

	30 11 * * * docker exec orbit-app-1 python -m app.meli.catalogo >> /mnt/data/appdata/orbit/logs/meli-catalogo.log 2>&1
	25 12 * * * docker exec orbit-app-1 python -m app.envio_muestras --platform meli --canal meli >> /mnt/data/appdata/orbit/logs/precio-muestras.log 2>&1
	10 13 * * * /usr/bin/flock -n /tmp/precio-meli.lock docker exec orbit-app-1 python -m app.cli precio --platform meli >> /mnt/data/appdata/orbit/logs/precio-meli.log 2>&1
	10 15-23/2 * * * /usr/bin/flock -n /tmp/precio-meli.lock docker exec orbit-app-1 python -m app.cli precio --platform meli >> /mnt/data/appdata/orbit/logs/precio-meli.log 2>&1

## Encendido

Sigue los "Criterios de encendido" del plan en cada universo. Los corres tú
cuando claw te lo encarga.

Cada fila X trae sus scripts en `docs/evidencia/repricing-02/ejecucion/X.<n>/`.
Escríbelos en el PR del paquete de despliegue de su universo: X.1 con D.2, X.2
y X.3 con D.3, y X.4 con D.4.

- `sembrar.sh` siembra "margen de hoy" en `shadow` con
  `POST /api/precios/goals/plan` y `POST /api/precios/goals/aplicar`. Guarda el
  plan, con las unidades que quedaron bloqueadas y su motivo.
- `sonda.sh` (X.2, X.3 y X.4) corre `tools/precio_sonda.py` con
  `--acepto-mutacion-real` y `--go` en una publicación. Guarda la lectura
  posterior y la de una publicación de control.
- `encender.sh` pone el universo en `live` con
  `POST /api/ads-optimizer/settings/amazon_mx` y la llave `plataforma/canal` del
  universo en `precio_modo_universo`. La misma llamada sirve para `meli/meli`,
  porque la config es global. Después pasa los goals a `live` en bloque con el
  go literal de la fila.
- `apagar.sh` regresa el universo a `shadow`. Es la reversa de `encender.sh`.

Cada script simula por omisión y escribe solo con `--acepto-mutacion-real`. Los
cuatro corren dentro de `orbit-app-1`, con la misma forma que la sonda de M.0.
Fuera del contenedor, un refresco de token de Mercado Libre deja inservible el
de producción. El token de escritura no sale del servidor ni se imprime.

- **X.1, MX FBA.** No necesita sonda de escritura: el camino ya se probó en A.4.
  Es el primer encendido: `encender.sh` sube también `precio_modo_global` a
  `live`.
- **X.2, MX FBM** y **X.3, Estados Unidos.** Corre `sonda.sh` en una
  publicación de cada universo antes de pasar a `live`.
- **X.4, Mercado Libre.** `sonda.sh` es la única que puede escribir con la
  forma sin sellar. Si pasa, abre un PR que cambia solo
  `FORMA_ESCRITURA_MELI`. Si no pasa, corrige la forma candidata y repite,
  tres intentos como máximo. Al tercero sin pasar, la fila X.4 queda cerrada
  con el motivo.

## Preguntas que siguen abiertas

Las preguntas para el dueño están en la sección "Preguntas abiertas y riesgos"
del diseño. El plan lista el valor que usa para cada una. Si el dueño cambia un
valor, cambia la fila de esa tabla y la pieza que la tabla nombra.
