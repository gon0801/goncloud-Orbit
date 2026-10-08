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
lead.

Los nombres de símbolos son del commit `d83bb28`. Si un archivo ya cambió, busca
el símbolo por nombre.

## Antes de empezar

1. Comprueba que el PR que trae el diseño, el plan y esta guía está en `master`.
2. Lee el diseño completo. Lee del bosquejo las secciones de tu paso.
3. Levanta un Postgres local. Las pruebas con base no corren en un PR.
4. Trabaja cada paso en su rama, con un PR por paso.

## Reglas que valen en todos los pasos

**Prueba primero.** Escribe la prueba, mírala fallar por la razón correcta y
después escribe el código. Registra en la evidencia un mutante por cada prueba
nueva: el cambio de una línea que la pone en rojo. Los mutantes obligatorios
están en la sección "Pruebas que deben discriminar" del plan.

**Lo que CI no ve en un PR.** En un PR solo corre el job `rapido`. La batería con
base corre en el push a `master`, en el job `completa`. Antes de abrir el PR,
corre en local los archivos de prueba que tocaste y anota el comando y el conteo
en la evidencia.

**Migraciones.** Toma el siguiente número libre al escribirla. Hoy la última es
la 0052. Escribe `BEGIN;` y `COMMIT;` dentro del archivo y su reversa en
`NNNN_reversa_<nombre>.sql`. Todo CHECK nuevo sobre `precio_decision` o
`estimacion_escenario` lleva `NOT VALID`: esas tablas tienen filas en producción.

**Tamaño.** Ningún módulo de `app/` pasa de 900 líneas. Si una función llega al
límite de complejidad, pártela por lo que cada parte sabe. No uses `noqa`.

**Candados de arquitectura.** Solo el paso 0.a edita
`tests/test_architecture.py`. Cada carril escribe los suyos en
`tests/test_arq_precio_<carril>.py`. Cada candado nuevo trae una prueba con una
fuga sembrada que demuestra que sí la detecta.

**Repo público.** Usa productos, SKUs y publicaciones inventados en pruebas,
fixtures y documentos.

**Sin red en las pruebas.** Usa clientes falsos o `httpx.MockTransport`. Ningún
paso de construcción llama a Amazon ni a Mercado Libre. Las sondas F.0 y M.0 son
la excepción y solo leen.

**Evidencia.** Guarda comandos, salidas, mutantes y el SHA de cada paso en
`docs/evidencia/repricing-02/ejecucion/<id>/`.

**Producción.** Solo el dueño escribe en producción, con un script que tú dejas
listo. Las lecturas con `ORBIT_DSN_READ` sí las corres tú. Cada paso que
despliega trae `desplegar.sh`, `rollback.sh` y `checklist.sh`, y un paso con
migración trae además `ensayo.sh`. Sigue las reglas de esos scripts que están en
la sección "Reglas que valen en todos los pasos" de
[la guía del bloque S de JEV ADS 02](jev-ads-02-bloque-s.md).

**Revisión y cierre.** Cada paso con código pasa una revisión cruzada con un
revisor distinto del autor antes del merge. Solo un hallazgo bloqueante y
reproducible abre otra ronda. Marca la fila del paso en `plans/repricing-02.md`
y agrega una entrada a `docs/CHAT-CONTEXT.md` en el mismo PR.

## 0.a: escribe los contratos sin cambiar el comportamiento

Este paso crea todo lo que los carriles comparten. Después de él, ningún carril
vuelve a editar `tipos.py`, `puerto.py`, `config.py`, los registros ni las
migraciones.

### Qué vas a encontrar

- `app/precio/tipos.py` importa `DetalleFee` de `app.estimacion_venta` y define
  `MOTIVOS_NO_EVALUADO` a mano.
- La lectura de config está repetida: `_numero` y `_fraccion` en
  `goals_write.py`, `_numero` y `_entero` en `fuentes.py`, `_numero_entero` en
  `corrida.py` y `validar_cap` en `cuota.py`.
- `tests/test_architecture.py` fija `EXCEPCIONES_PURAS_PRECIO` con una aserción
  de igualdad y lista `corrida.py` en `ALLOWLIST_TAMANO`.
- `app/ledger.py` descarta las filas de Mercado Libre con el motivo
  "plataforma meli excluida". `_PLATAFORMA_ORIGEN` no trae `meli`.
- `/precios` vive en `app/templates/precios.html` y en `app/api_dashboard.py`
  (`precios`, `_bloque_precios`, `_precios_de`).

### Pruebas primero

- La migración rechaza cada caso de `datos.sql` §11: una muestra `familia` sin
  donantes, una decisión nueva con `l_valor` y sin `l_origen`, una decisión
  `live` bajo un goal `shadow`, una decisión con un `goal_id` ya cerrado y una
  liberación para una corrida que no retuvo.
- La migración acepta lo que hoy revienta: una decisión `shadow` bajo un goal
  `live`, y un goal reeditado el mismo día.
- La migración pasa sobre una base con las filas de producción. Las decisiones
  viejas, sin `l_origen`, no la hacen fallar.
- Sumar dos `Importe` de moneda distinta levanta `MonedasMezcladas`. `Cuenta` no
  se construye con el envío en MXN y el precio en USD.
- `EnvioImputado` no se construye con una muestra de origen `propio`.
- `MOTIVOS_NO_EVALUADO` contiene todos los motivos que exporta la estimación.
- `leer_config` falla y nombra la clave que falta. `modo_de` da `off` para un
  universo sin entrada.
- `precio_mercados.abrir("meli")` levanta `ConfigInvalida` mientras el adaptador
  de Mercado Libre esté vacío.

### Cambios

1. Escribe `migrations/0053_precio02_canal_meli.sql` solo con el valor nuevo del
   enum `estimacion_canal`. Un valor nuevo de enum no se puede usar en la
   transacción que lo crea.
2. Escribe `migrations/0054_precio02_base.sql` con todo `datos.sql`, incluidas la
   siembra de las claves nuevas de config (§12) y el bloque que comprueba la
   migración (§11). Escribe las dos reversas.
3. Agrega a `precio_goal_solo_cierra_vigencia` las columnas nuevas de
   `precio_goal` (`origen`, `lote`, `m_referencia`) como inmutables.
4. Crea `app/precio/puerto.py` con la sección B del bosquejo.
5. Agrega a `app/precio/tipos.py` la sección A. Arma `MOTIVOS_NO_EVALUADO` a
   partir de un export nuevo de `app/estimacion_venta.py` más los motivos propios
   del motor.
6. Deja `app/precio/config.py` como el único lector de claves `precio_*` (H). Las
   copias de `goals_write.py`, `fuentes.py` y `corrida.py` lo llaman. No toques
   `validar_cap`: se borra en S.1.
7. Crea `app/precio_mercados.py` (S) y `app/estimacion_universo.py` (M) con las
   cuatro entradas registradas. Cada entrada apunta a un módulo que ya existe,
   aunque esté vacío: `app/spapi/precio_mercado.py`, `app/estimacion_amazon.py`,
   `app/ledger_atribucion.py` y, en `app/meli/`, `precio_mercado.py`,
   `write_client.py`, `catalogo.py`, `estimacion.py` y `ledger.py`.
8. Haz que `app/ledger.py` despache la atribución por plataforma con el
   protocolo `Atribuidor` (P). Mercado Libre sigue saliendo como excluida.
9. Parte `precios.html` en `_precios_seguridad.html` y `_precios_goals.html`.
   Mueve el bloque de precios de `app/api_dashboard.py` a `app/api_precios.py`.
10. En `tests/test_architecture.py`, agrega el candado que deja a
    `app/precio_mercados.py` como el único módulo que importa los dos
    adaptadores. El candado de imports de `corrida.py` llega en 0.b.

### Comprueba

- La batería completa pasa en local sin cambiar ninguna aserción existente.
- `ensayo.sh` aplica la 0053 y la 0054 en dos corridas de `psql` separadas sobre
  una copia con las filas de producción.
- `python -m app.cli precio --platform amazon_mx` imprime las mismas líneas antes
  y después, contra la misma base de prueba.

## 0.b: voltea la corrida

Este paso es el de mayor riesgo del plan. Cambia el archivo más delicado del
motor sin cambiar lo que hace para MX FBA.

### Qué vas a encontrar

- `app/precio/corrida.py` importa `ProductFeesClient`, `cotizar_a_precio`,
  `leer_escenarios` y `app.spapi.precio_write`. Lee `spapi_price_observation`,
  `spapi_inventario_observation` y `spapi_listing_estado_observation` dentro de
  `armar_entrada` y de `_insumos_ventas`.
- `app/spapi/precio_write.py` mezcla el protocolo de `precio_cambio`
  (`cambiar_precio`, `cerrar_por_observacion`, `revertir`) con lo que es de
  Amazon (`leer_precio_vivo`, `construir_cuerpo_parche`, `construir_escritor`).
- El JSON `componentes` del escenario se desarma a mano en dos lugares:
  `corrida.armar_entrada` y `tools/precio_goal.py::_referencia`.
- `app/precio/fuentes.py::_SQL_CANO` define la publicación activa de Amazon.

### Pruebas primero

- Un `Mercado` falso basta para correr `correr` de punta a punta, sin `httpx` ni
  SP-API.
- `cambios.aplicar` llamada dos veces sobre la misma decisión escribe una vez. La
  segunda salta con `ya_aplicado`.
- `MercadoAmazon.catalogo` devuelve la misma lista que `_SQL_CANO` sobre el mismo
  fixture.
- `leer_cuenta` da los mismos importes que `armar_entrada` da hoy sobre el mismo
  escenario.
- El candado falla con un `import app.spapi` sembrado en `corrida.py`, en
  `cambios.py` o en `fuentes.py`.

### Cambios

1. Crea `app/spapi/precio_mercado.py` con `MercadoAmazon` (Q). Mueve ahí las
   lecturas de `spapi_*`, `_oferta_para_cotizar`, `_verificada` y `_detalle_fee`
   de `corrida.py`, y `_SQL_CANO` de `fuentes.py`. `Vitrina.insumos` lee por
   lote: una llamada por plataforma.
2. Crea `app/precio/cambios.py` (I) con el protocolo que hoy está en
   `precio_write.py`. `precio_write.py` se queda con lo de Amazon y sigue siendo
   el único importador de `write_client`.
3. Agrega `leer_cuenta` a `app/estimacion_reader.py` (N). `armar_entrada` y
   `_referencia` la llaman.
4. Reescribe `correr` en tres pasos sobre filas guardadas: decidir, compuerta y
   aplicar (J). En este paso la compuerta siempre deja pasar y el cupo sigue
   vivo. `correr` recibe un `Mercado` en vez de `lector`, `escritor`, `fees`,
   `construir_cuerpo` y `limitador`.
5. Cambia `app/cli.py::_precio` para que acepte `--platform` repetido y abra cada
   mercado con `precio_mercados.abrir`.
6. Haz que `fuentes.leer_publicaciones` reciba una `Vitrina` (L). Borra
   `FASE_FBM`, `FASE_MELI` y `_fase_fuera_de_alcance` de `cobertura.py`. Agrega
   `listing_id` a `DetalleSinGoal`.
7. Extiende el candado de imports a `corrida.py`, `cambios.py` y `fuentes.py`.
   Actualiza `EXCEPCIONES_PURAS_PRECIO` y la lista de imports permitidos de
   `tools/precio_reversa.py`.

### Comprueba

- Guarda las decisiones de un día en sombra con el código viejo y con el nuevo,
  contra la misma base. Son idénticas fila por fila.
- `tests/test_precio_corrida.py` pasa cambiando solo cómo arma sus clientes.
- `git grep -n "app.spapi\|estimacion_fees" -- app/precio/corrida.py` no devuelve
  nada.

## Carril S: seguridad sin cupo y avisos

Archivos del carril: `app/precio/{corrida,cambios,compuerta,liberaciones,reglas,objetivo,ventas,fuentes}.py`,
el bloque de precios de `app/notifica.py`, `_precios_seguridad.html` y
`tools/precio_reversa.py`.

### S.1: borra el cupo y la prioridad

**Qué vas a encontrar.** `cuota.reservar` se llama en `_fase3_uno`.
`repartir_cupo` separa la fase 1 de la fase 3, y cuatro pruebas de
`tests/test_precio_corrida.py` lo usan como punto de sabotaje. `validar_cap`
exige `precio_cap_<platform>` al arrancar. `/salud` pinta "cupo X de Y".

**Pruebas primero.**

- Con 8 decisiones `live` que mueven precio, las 8 se escriben.
- Una config sin `precio_cap_*` no detiene la corrida.
- Las cuotas de Ads en `apply_quota_state` no cambian durante una corrida.

**Cambios.** Borra `app/precio/cuota.py`, `reglas.repartir_cupo`,
`corrida._cuota_reescrita`, `_ingreso_60d`, la prioridad y el motivo `cuota` de
`MOTIVOS_MANTENER`. Quita "cupo" de `salud.html` y de `app/api_precios.py`. Mueve
el punto de sabotaje de las cuatro pruebas al paso de la compuerta. No toques
`apply_quota_state`, sus triggers ni `apply_cap_de_config`: son de Ads. No borres
`precio_cap_*` de la config: se quedan sin lector.

**Comprueba.** `git grep -n "repartir_cupo\|precio_cap_\|cuota_mod" -- app` no
devuelve código.

### S.2: construye el apagador

**Qué vas a encontrar.** El único modo de hoy es `precio_goal.mode`. Desde la
0054, `precio_decision_coherente` acepta una decisión `shadow` bajo un goal
`live`. El precedente está en `app/optimizer/goals.py`. Copia la regla, no la
importes.

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

**Cambios.** Escribe `compuerta.modo_efectivo` (E). En `_decidir_pendientes`,
calcula el modo de cada unidad y guárdalo en la decisión. En
`_aplicar_pendientes`, relee la config y el goal de la decisión antes de cada
escritura real. Agrega
`precio_modo_global` y `precio_modo_universo` a la pantalla `/settings`, con el
mismo camino que usa hoy para editar config.

**Comprueba.** El DoD de la fila S.2.

### S.3: construye la compuerta

**Qué vas a encontrar.** Desde 0.b las decisiones se guardan antes de aplicarse.
Una decisión `subir` sin cambio ya es legal: pasa hoy con `precio_vivo_distinto`.

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

**Cambios.** Escribe `app/precio/compuerta.py` (E) como módulo puro. Escribe
`_medir_compuerta`, que arma `Intencion` y `Deriva` desde lo guardado hoy y
escribe una fila de `precio_compuerta_medicion` por universo. Escribe
`app/precio/liberaciones.py`, el único escritor de
`precio_compuerta_liberacion`, con rol `app_admin`. Entrega `liberar` al carril
G, que cablea la ruta. Agrega al cron los repasos de cada dos horas y un `flock`
por grupo de cuota: uno para las dos plataformas de Amazon y otro para Mercado
Libre.

**Comprueba.** El DoD de la fila S.3. Además, un repaso sin nada pendiente no
llama a la plataforma.

### S.4: deja de congelar por errores sin efecto

**Qué vas a encontrar.** `cambios_previos` cuenta un cambio en `error` para el
cooldown, y por eso `frenado(api_error)` no se alcanza. `cerrar_huerfanas` sella
`error` sin leer el precio. `cerrar_por_observacion` no filtra por plataforma.

**Pruebas primero.** Las cinco del DoD de la fila S.4, cada una con su fixture de
`readback_precio`.

**Cambios.** Agrega `sin_efecto` a `CambioPrevio`, derivado de columnas que ya
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
`precio:<platform>` que la corrida. Deja `tools/precio_reversa.py` como
despachador, con `--cambio-id`, `--fecha` con `--platform`, y `--corrida`.

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

**Qué vas a encontrar.** `ventas.evaluar_senal` recibe ventas por producto y
exige inventario FBA todos los días. `reglas._freno_entrada` revisa las monedas
tarde, y `objetivo.derivar_ref_fijo` busca el `ReferralFee` dentro de las reglas.

**Pruebas primero.** Las cuatro del DoD de la fila S.7. Corre además los casos
existentes de `u15` contra `u60` de `tests/test_precio_reglas.py`: deben dar lo
mismo.

**Cambios.** `evaluar_senal` recibe `HistoriaUnidad` y usa
`DiaUnidad.disponibilidad` (D). Agrega la evidencia `trafico`, que solo se
evalúa después de una subida propia. Escribe `evaluar_cohorte`, que solo avisa.
`reglas.decidir` usa `Fees.variable` y `Fees.fijo`, acepta `lineal=True` y deja
de revisar monedas (F). Mueve `derivar_ref_fijo` al adaptador de Amazon.

**Comprueba.** El DoD de la fila S.7.

## Carril G: goals en pantalla

Archivos del carril: `app/precio/siembra.py`, `app/precio/goals_write.py`,
`app/api_precios.py`, `app/api_precios_write.py`, `_precios_goals.html`,
`app/static/js/precios.js` y `tools/precio_goal.py`.

### G.1: mueve el plan de goals a `app/`

**Qué vas a encontrar.** Las protecciones viven en `tools/precio_goal.py`:
`_referencia`, `_guardas_del_plan`, `_huella` y `UMBRAL_SALTO`. La herramienta
siembra fila por fila sin transacción global. `goals_write.py` es el único
escritor de `precio_goal` y editar es cerrar y sembrar.

**Pruebas primero.**

- `planear` con `PedidoMargenDeHoy` propone el margen de hoy de cada unidad.
- Una unidad con margen de hoy fuera de la banda sale con el bloqueo
  `fuera_de_banda`, confirmable. Una sin escenario sale `sin_escenario`, no
  confirmable.
- Un precio objetivo a más de 25 % del actual sale `salto_mayor_25`.
- `aplicar_plan` con una huella distinta levanta `HuellaDistinta` y no escribe.
- Si la tercera fila de cinco falla, no queda ninguna.
- Sembrar y volver a sembrar el mismo día deja un goal vigente y uno anulado.
- `live` sin go literal no escribe.

**Cambios.** Escribe `app/precio/siembra.py` (G) como módulo puro. Agrega
`leer_candidatos` y `aplicar_plan` a `goals_write.py` (K). `aplicar_plan`
recalcula el plan adentro y escribe todo en una transacción. Deja
`tools/precio_goal.py` como despachador y borra de ahí `_referencia`,
`_componente`, `_detalle_fee`, `_guardas_del_plan` y `UMBRAL_SALTO`.

**Comprueba.** `tests/test_precio_goals.py` pasa. La prueba contra la base real
necesita `leer_cuenta`, que llega con 0.b.

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
scripts de `/static`.

**Pruebas primero.**

- `/precios` muestra, por unidad, margen de hoy, goal, precio objetivo, origen
  del envío y TACoS.
- La acción "margen de hoy" pide el plan, muestra los bloqueos y solo entonces
  habilita Aplicar.
- El botón **Soltar** aparece solo con una retención sin soltar.

**Cambios.** Escribe `app/static/js/precios.js` y `_precios_goals.html`. Agrega
casillas, la acción en bloque y el campo del go literal para pasar a `live`.
Muestra el origen del envío con una marca distinta cuando es `familia` o
`marketplace`.

**Comprueba.** Con la base de prueba, siembra desde la pantalla un goal, cambia
otro y siembra "margen de hoy" a todos los sin goal.

## Carril F: FBM de México y Estados Unidos

Archivos del carril: `app/estimacion_amazon.py`,
`app/estimacion_{insumos,fees,ingest,repository,venta}.py`,
`app/precio/envio.py`, `app/envio_muestras.py`, el atribuidor de Amazon de
`app/ledger_atribucion.py`, `app/spapi/precio_mercado.py` después de 0.b, y
`tools/precio_sonda.py`.

### F.0: lee tres cosas de producción

Corre las tres lecturas con `ORBIT_DSN_READ` y guarda consulta, salida y
conclusión en `ejecucion/F.0/`.

1. Agrupa los cargos `shipping_fee` de `ledger_event` por el prefijo de
   `source_event_id`. Parte de la consulta
   `docs/evidencia/repricing-01/E.0/consultas/06-par-labman-shippinghb.sql`.
   Confirma que el prefijo distingue `LabmanLabelPurchase`, `shipping_label` y
   `ShippingHB`.
2. Cotiza con Product Fees una oferta FBM de MX y una de US, con
   `IsAmazonFulfilled` en falso. Anota el estado, los tipos de fee y si trae
   impuesto. Si no responde `Success`, registra ese universo sin cotizador.
3. Busca un listing FBM sin existencias y mira si su estado sigue trayendo
   `BUYABLE`. De eso depende que "disponible" en FBM sea solo el estado.

### F.1: atribuye las ventas de Amazon a su publicación

**Qué vas a encontrar.** `ledger_event` es append-only y sus ventas traen
`product_id` y `order_id`, no `listing_id`. `spapi_order_observation` guarda
`fulfillment_channel` por orden.

**Pruebas primero.** Las cinco del DoD de la fila F.1.

**Cambios.** Escribe el atribuidor de Amazon en `app/ledger_atribucion.py` (P).
La ingesta inserta en `ledger_event_atribucion` con `ON CONFLICT DO NOTHING`. Si
el atribuidor devuelve `None`, la ingesta no inserta nada para ese evento y lo
cuenta en `ingest_run`.

**Comprueba.** En una copia de producción, cuenta las ventas por `resuelto_por`.
Anota cuántas quedan sin atribuir. Corre la ingesta otra vez después de cargar
el dato que faltaba y comprueba que esas ventas ya se atribuyen.

### F.2: mide el envío con su origen

**Qué vas a encontrar.** Los tres cargos de envío caen como
`fee_type='shipping_fee'`, en MXN y con signo negativo. `precio_envio_muestra`
existe desde la 0039 y está vacía.

**Pruebas primero.**

- `costo_de_orden` con etiqueta 450, Labman 449 y HB 80 da 530.
- Una orden con dos productos no entra a ninguna muestra y se cuenta.
- Una fuente desconocida cae en `otros` y se cuenta.
- La escalera: un envío propio da `propio`. Sin propios y con hermanos de
  familia, `familia`, con la mediana de las medianas. Sin familia,
  `marketplace`. Sin un solo envío en el universo, no hay muestra.

**Cambios.** Escribe `app/precio/envio.py` (C) como módulo puro. Escribe
`app/envio_muestras.py::refrescar_muestras` (O), que inserta solo si la muestra
cambió. Agrega su línea de cron a las 12:15 UTC, antes de la estimación de las
12:45.

**Comprueba.** El job corrido dos veces el mismo día no escribe la segunda vez.

### F.3: abre los universos en la estimación

**Qué vas a encontrar.** MX FBA está fijo en seis puntos:
`estimacion_insumos._motivo_universo`, `estimacion_fees._UNIVERSO_SOPORTADO` con
`MARKETPLACE_MX` e `IsAmazonFulfilled` en el cuerpo, el
`WHERE platform = 'amazon_mx'` de `estimacion_ingest.py`, la única política de
`estimacion_politica_version`, y `logistica` leída como constante en
`estimacion_venta.py`.

**Pruebas primero.** Las cinco del DoD de la fila F.3. Agrega una: la batería de
MX FBA pasa sin tocar ninguna aserción.

**Cambios.** Llena `universo_de` y `logistica_del_universo` en
`app/estimacion_universo.py` (M) y el cotizador en `app/estimacion_amazon.py`.
Reemplaza los seis puntos por llamadas al registro. El escenario guarda
`logistica_origen`, `envio_muestra_id`, `fee_variable`, `fee_fijo` y
`fee_cotizable`. En Estados Unidos, convierte costo y envío de MXN a USD con
`convertir` y la tasa del día. Deja listo el script del dueño que inserta las
políticas `amazon_mx/fbm` y `amazon_us/fbm`.

**Comprueba.** En local, una oferta FBM de MX y una de US producen escenario
`disponible`.

### F.4: completa el adaptador de Amazon y escribe la sonda

**Pruebas primero.** Las tres del DoD de la fila F.4.

**Cambios.** En `MercadoAmazon.insumos`, calcula la disponibilidad según el canal
de la unidad y lee las ventas de `v_precio_venta_unidad`. Escribe
`tools/precio_sonda.py`, genérica sobre `Mercado`: simula por omisión y, con
`--acepto-mutacion-real` y `--go`, mueve una publicación un centavo y la
revierte.

**Comprueba.** `tools/precio_sonda.py --platform amazon_us --listing-id <id>`
imprime el plan y no escribe.

## Carril M: Mercado Libre

Archivos del carril: todo `app/meli/`.

### M.0: sondea Mercado Libre sin escribir

Usa `ClienteMeli`, que solo hace GET. Guarda cada respuesta saneada en
`ejecucion/M.0/` y escribe cuatro conclusiones:

1. De dónde sale el SKU de cada variante.
2. Si hay una consulta de cargos que separe el porcentaje del cargo fijo.
3. Si el precio de una publicación con variantes va una vez o por variante.
4. Si el token actual permite escribir.

Si no hay consulta de cargos, registra `meli/meli` sin cotizador.

### M.1: ingiere el catálogo

**Qué vas a encontrar.** `fetch_meli` en `app/reputacion.py` ya pide cada
publicación a diario y solo guarda `health`. No hay filas `meli` en `listing`.

**Pruebas primero.** Las cuatro del DoD de la fila M.1.

**Cambios.** Escribe `app/meli/catalogo.py`. Por publicación activa, inserta o
actualiza `listing` con el producto ancla, sus filas de `listing_miembro` y una
fila de `meli_publicacion_observation`. Cruza el SKU de cada variante con
`product.odoo_sku`.

**Comprueba.** Cuenta en la evidencia las publicaciones con todos sus miembros
mapeados, con alguno sin mapear y sin ninguno.

### M.2: abre el ledger de Mercado Libre

Haz la auditoría antes de escribir código.

1. Lista cada vista, consulta y reporte que lee `ledger_event` agrupando por
   plataforma. Empieza por `git grep -n "ledger_event" -- app migrations`.
2. Escribe en `ejecucion/M.2/auditoria.md` qué verá cada uno cuando existan filas
   `meli`, y cuál necesita un filtro.
3. Corrige lo que haga falta en el mismo PR.

**Pruebas primero.** Las del DoD de la fila M.2.

**Cambios.** Agrega `meli` a `_PLATAFORMA_ORIGEN`. Escribe el atribuidor de
Mercado Libre en `app/meli/ledger.py` y el mapeo de sus tipos de cargo.

### M.3: estima el margen de `meli/meli`

**Pruebas primero.** Las tres del DoD de la fila M.3.

**Cambios.** Escribe `app/meli/estimacion.py`: el escenario por publicación, con
una cuenta por miembro y la del miembro que manda en la fila. El envío sale de
`precio_envio_muestra` con la escalera de F.2. Deja listo el script del dueño
que inserta la política `meli/meli`.

### M.4: construye el adaptador y el cliente de escritura

**Pruebas primero.** Las cuatro del DoD de la fila M.4.

**Cambios.** Escribe `app/meli/write_client.py` con `MeliWriteClient` (R). Recibe
el refresco de token que ya vive en `app/reputacion_clientes.py` y nunca lee
credenciales. Deja `FORMA_ESCRITURA_MELI = "pendiente_sonda"`. Escribe
`MercadoMeli` en `app/meli/precio_mercado.py`. Agrega en
`tests/test_arq_precio_m.py` los candados: ningún `.put(` fuera de
`write_client.py`, un solo importador del cliente, y `ClienteMeli` solo con GET.

## Despliegues

Cada despliegue lo corre el dueño con `! bash <script>`. Tú dejas los scripts y
lees el resultado.

- **D.1** lleva la 0053, la 0054 y el código de 0.b. Aplica las dos migraciones
  en dos corridas de `psql` separadas. Al día siguiente, compara la corrida
  automática con la de hoy: las mismas 6 decisiones.
- **D.2** lleva los carriles S y G. Cambia la línea de cron por las dos de la
  sección "Uso" del diseño y agrega los repasos.
- **D.3** lleva el carril F. Corre el script de políticas, agrega el job de
  muestras al cron y agrega `--platform amazon_us` a la línea de Amazon.
- **D.4** lleva el carril M. Corre el script de la política `meli/meli` y agrega
  la línea de cron de Mercado Libre.

## Encendido

Sigue los "Criterios de encendido" del plan en cada universo.

- **X.1, MX FBA.** No necesita sonda de escritura: el camino ya se probó en A.4.
  Es el primer encendido: el dueño sube también `precio_modo_global` a `live`.
- **X.2, MX FBM** y **X.3, Estados Unidos.** Corre `tools/precio_sonda.py` en una
  publicación de cada universo antes de pasar a `live`.
- **X.4, Mercado Libre.** La sonda sella la forma del cuerpo. Escribe la forma
  real en `MeliWriteClient`, cambia `FORMA_ESCRITURA_MELI` y abre un PR solo con
  ese cambio.

## Preguntas que siguen abiertas

Las nueve preguntas para el dueño están en la sección "Preguntas abiertas y
riesgos" del diseño. El plan lista el valor que usa para cada una. Si el dueño
cambia un valor, cambia la fila de esa tabla y la pieza que la tabla nombra.
