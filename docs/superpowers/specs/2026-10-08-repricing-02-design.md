# REPRICING 02 — el motor de precios en todas las plataformas (diseño)

Versión 1.0, 2026-10-08 UTC. Estado: **diseño, sin implementar**. Base de
código: `origin/master` `d83bb28`.

Este documento dice cómo queda el motor de precios para funcionar al 100 % en
Amazon México (FBA y FBM), Amazon Estados Unidos y Mercado Libre. Lo acompañan,
en `docs/evidencia/repricing-02/diseno/`:

- [`bosquejo.py`](../../evidencia/repricing-02/diseno/bosquejo.py): tipos y
  firmas, secciones A–V. Las letras entre paréntesis de este documento remiten
  a esas secciones.
- [`datos.sql`](../../evidencia/repricing-02/diseno/datos.sql): las migraciones
  0053 y 0054 como bosquejo.
- [`juicio.md`](../../evidencia/repricing-02/diseno/juicio.md): los tres
  diseños que compitieron, el veredicto del juez y lo que se injertó.

Sustituye, en lo que choque, al
[spec de REPRICING 01](2026-09-15-repricing-01-design.md) v1.3. Lo que ese spec
ya construyó y cerró (reglas puras, escritura y reversa en Amazon, migración
0039, sonda A.4, sombra de MX FBA) sigue en pie.

## Decisiones del dueño (2026-10-08 UTC, cerradas)

| # | Decisión | Literal |
|---|---|---|
| D1 | Sin cupo diario. En su lugar: apagador general, fusible y resumen diario por Telegram | «el cupo es estupido eso queda completamente fuera»; reemplazo aceptado: «si» |
| D2 | Sin mediciones de 30 días. Uno o dos días de prueba | «con uno o dos y pruebas de que funciona es suficiente» |
| D3 | Todos los productos califican. Envío propio; si no hay, un promedio | «todos los productos tienen que calificar» |
| D4 | El export de Seller Central deja de ser requisito | «los insumos de fbm quedan completamente fuera, no son bloqueantes» |
| D5 | Una etiqueta de envío de US cuesta ~530 MXN: el cargo repetido se cuenta una vez | «530» |
| D6 | Todos los goals arrancan en el margen de hoy y el dueño ajusta | «si» |
| D7 | Goals editables en pantalla, uno por uno y en bloque | «se pone en UI y se puede agregar manual, automatico uno por uno y todos juntos?» |
| D8 | Mercado Libre: goal por publicación, variantes después | «publicacion con opcion a variantes en el futuro» |
| D9 | Todo en paralelo; cada marketplace se enciende cuando esté listo | «120 dias es completamente inaceptable» |
| D10 | Mirar visitas o impresiones cuando no hay ventas suficientes | «ver si bajan impresiones u otras estadisticas?» |

Siguen vigentes del spec v1.3: proteger margen y no perseguir Buy Box (perderla
avisa, no frena); goal por producto fijado por el dueño; se compara el margen
estimado; baja solo si caen las ventas; ningún silencio; reversa manual.

**Lo que este diseño deroga del spec v1.3:** el cupo (S4 #12 y `precio_cap_*`);
los pilotos de 3–5 productos y la medición de 30 días; las fases en serie; el
mínimo de 6 envíos y «producto sin historia no se evalúa»; E.0a como bloqueo;
goals solo por herramienta; y los Reject «defaults de goal» y «usar el promedio
de envío».

## Problema

El motor de repricing funciona para un solo universo (Amazon MX FBA) y tiene que
funcionar para cuatro, encendidos en paralelo y sin ventanas de medición (D2,
D9). Lo que hace difícil la forma no es la cantidad de trabajo sino dónde está
cableado Amazon: en dos capas distintas. La estimación de margen solo sabe
calcular `amazon_mx/fba` (seis puntos fijos), y la corrida (`corrida.py`, 1 626
líneas) importa directo el cliente de fees, lee tres tablas `spapi_*` y delega
la escritura a un módulo de `app/spapi/` que además es dueño del protocolo de
`precio_cambio`. Encima, las cinco tablas de la 0039 cuelgan de `listing` con un
solo `product_id`, y 48 de las 65 publicaciones de Mercado Libre venden varios
productos. El diseño tiene que respetar lo que ya está sellado: reglas puras con
candado, un escritor por tabla, orden INSERT+COMMIT → escritura → sello, sombra
fiel, cobertura que cuadra exacta, migraciones aditivas sobre filas reales, y la
única excepción que el dueño abrió a «dato faltante = nada»: el envío imputado
(D3), que no puede parecerse a uno medido. Y tiene que decir qué archivo es de
quién, porque cuatro carriles van a construir a la vez.

## Uso (lo que ve quien llama)

Quien opera el motor ve tres cosas: un comando por plataforma, una pantalla y
una herramienta de reversa. Nada de eso menciona Amazon ni Mercado Libre más
que como el nombre de la plataforma.

**1. El cron.** Una línea por grupo de cuota: las dos de Amazon en serie dentro
del mismo proceso (comparten cuota de SP-API), Mercado Libre aparte y en
paralelo. Las mismas líneas se repiten cada dos horas como repaso; el repaso no
hace nada salvo que haya algo pendiente.

```
10 13 * * *      flock -n /tmp/precio-spapi.lock python -m app.cli precio --platform amazon_mx --platform amazon_us
10 13 * * *      flock -n /tmp/precio-meli.lock  python -m app.cli precio --platform meli
10 15-23/2 * * * (las mismas dos, como repaso)
```

Estas líneas muestran la forma. Las líneas reales del servidor, con
`docker exec` y su archivo de log, y el orden en que se instalan, están en la
sección "Despliegues" de la guía de construcción.

```python
# app/cli.py
for clave in args.platform:
    with precio_mercados.abrir(clave) as mercado:
        resumen = corrida.correr(conn, mercado, owner=owner, avisar=avisar_precio)
    print(resumen.linea())
```

**2. El dueño siembra todos los goals en «el margen de hoy» desde `/precios`.**
Primero mira, luego acepta. La vista previa y la escritura recalculan el mismo
plan; si algo cambió entre mirar y aceptar, no se escribe nada.

```python
# POST /api/precios/goals/plan  (no escribe)
plan = siembra.planear(
    goals_write.leer_candidatos(conn, precio_mercados.vitrina("amazon_mx"), filtro=SinGoal()),
    pedido=PedidoMargenDeHoy(mode="shadow"),
    banda=config.banda_goal,
)
# -> filas con margen de hoy, goal propuesto, precio objetivo, origen del envío
#    y bloqueos (sin_escenario, fuera_de_banda, salto_mayor_25), más plan.huella

# POST /api/precios/goals/aplicar  (token, todo o nada)
goals_write.aplicar_plan(conn, plan, huella=cuerpo.huella,
                         confirmados=cuerpo.confirmados, go_literal=None, actor="dueno")
```

El mismo par de llamadas sirve para un producto, para una selección con un valor
escrito a mano (`PedidoValor`) y para pasar a `live` (`PedidoModo`, con el go
literal tecleado una vez). `tools/precio_goal.py` despacha a estas dos funciones.

**3. Salta el fusible y el dueño lo suelta.** La corrida decide todo y mide cada
universo (plataforma y canal). Si más de la mitad de uno quiere moverse, no
aplica nada de ese universo y avisa una vez.

```python
veredicto = compuerta.evaluar(intenciones_fba, medidas=n, derivas=derivas_fba, config=config)
# Retenida("movimiento_masivo", medidas=158, nuevas=91, ...)
#   -> las 91 decisiones quedan como `subir`/`bajar` con su cuenta completa,
#      sin cambio de precio y con una fila en `precio_retencion`
#   -> Telegram: «91 de 158 productos FBA de México quieren moverse hoy; no se
#      aplicó nada.»

# El dueño aprieta «Soltar» en /precios:
liberaciones.liberar(conn, corrida_id=812, actor="dueno", nota="subí los goals a propósito")
# El repaso de las 15:10 aplica esas 91 (si su precio vivo sigue siendo el de la mañana).
```

**4. Reversa en lote por fecha.** Primero se apaga el universo; luego se revierte.

```
python tools/precio_reversa.py --platform amazon_mx --fecha 2026-10-12
python tools/precio_reversa.py --platform amazon_mx --fecha 2026-10-12 \
       --acepto-mutacion-real --huella H --go "revertir 12-oct"
```

```python
plan = cambios.planear_reversa(conn, mercado, SeleccionPorFecha("amazon_mx", date(2026, 10, 12)))
cambios.revertir_lote(conn, mercado, plan, huella=H, go_literal="revertir 12-oct", owner=owner)
```

## Forma

### Dos fronteras, no una

La idea de partida era meter todo lo específico de plataforma detrás de una
interfaz por plataforma. Se sostiene para cinco de seis cosas: el precio
observado, la cotización de fees, la lectura del precio vivo, la escritura y el
catálogo de activas. Para la sexta, la fuente del escenario de margen, es la
forma equivocada y este diseño la rechaza: el escenario lo consumen también `/margen` y las demás pantallas
(regla 2, un número una fuente), y cada decisión de la 0039 apunta por FK a
`estimacion_escenario`, `estimacion_fee_observation` y
`estimacion_oferta_observation`. Si cada plataforma calculara su margen detrás
del puerto habría dos márgenes del mismo producto y el rastro de auditoría se
partiría.

Por eso hay **dos fronteras, cada una con su registro**:

- **Abajo, el universo** (`platform/canal`, sección M): qué deja una venta.
  Política fiscal, quién cotiza fees y con qué cuerpo, de dónde sale el envío,
  en qué moneda. Vive en la estimación y la usa todo Orbit.
- **Arriba, el mercado** (`Mercado`, sección B): cómo se observa y cómo se mueve
  un precio. Vive en el motor y solo lo usa el motor.

El puerto **lee** el escenario (por `leer_cuenta`, sección N); no lo produce.

### Estructuras de datos

**La unidad de precio es la fila de `listing`; sus productos van aparte.** La
0001 ya describe `listing.external_id` como «ASIN o MLM id» y todas las tablas
`precio_*` exigen `(listing_id, platform)`. Una publicación de Mercado Libre es
una fila de `listing`: un goal, una decisión al día, un cambio abierto a la vez.
Esos son exactamente los invariantes correctos al grano del precio, y no se toca
ni una llave de la 0039. Lo que no cabía era `product_id` único.
`listing.product_id` sigue obligatorio y en Mercado Libre guarda un producto
**ancla** (el del primer miembro mapeado): es identidad, no costo, y así ningún
reporte que hoy cruza por esa columna pierde filas. Los productos de verdad van
en `listing_miembro` (publicación → variantes → producto), con una vista
`v_precio_unidad_miembro` que responde igual para Amazon (un miembro) y Mercado
Libre (varios). En el código es `UnidadPrecio(listing_id, miembros)`.

Con varios miembros a un solo precio, **manda el miembro cuyo precio objetivo es
el más alto** (`miembro_que_manda`): el precio que deja a todas las variantes en
el goal o arriba. Con un goal único es la variante de menor margen. Esa regla ya
recorre miembros con un goal por miembro, así que «variantes después» (D8) entra
sin reescribir en sus dos futuros posibles: si Mercado Libre separa las
variantes en publicaciones propias, son filas nuevas de `listing` y nada cambia;
si el dueño quiere goal distinto por variante bajo un mismo precio, es una tabla
hija `precio_goal_variante` (bosquejada al final de `datos.sql`, no se crea
ahora) que no toca ninguna unicidad.

**El envío es un tipo suma, no un número con etiqueta.** `Envio = EnvioIncluido
| EnvioPropio | EnvioImputado` (sección A). Un imputado es otra clase: tratarlo
como medido obliga a escribir el `isinstance`. En la base, la muestra lleva
`origen` con un CHECK que ata origen a forma (`propio` sin donantes; `familia`
con donantes y familia; `marketplace` con donantes y sin familia), el escenario
guarda `logistica_origen` y `envio_muestra_id`, y toda decisión nueva con `L`
exige `l_origen` (CHECK `NOT VALID`: vale para lo nuevo, no revisa la historia).
La escalera propio → familia → marketplace → nada vive en **una** función pura,
`resolver_envio`; el job diario escribe una muestra por producto y la estimación
solo lee la última. Si no hay un solo envío medido en ese marketplace y canal,
no hay muestra y sale `no_evaluado(envio_sin_historia)`: la excepción de D3
llega hasta el promedio del marketplace, no hasta una constante.

**Dinero en dos monedas.** `Importe` gana aritmética que revienta con monedas
distintas; `Cuenta` (reemplaza a `Componentes`) no se construye si sus seis
importes no están en la moneda de venta; y `Convertido` es el único tipo que
cruza monedas, guardando las dos puntas y la tasa. En US el costo y el envío se
miden en MXN y entran a la cuenta en USD por `convertir`, con la misma tasa del
día que ya resuelve la estimación; la muestra de envío se guarda en MXN, tal
como se midió. El chequeo tardío de monedas en `reglas._freno_entrada` se borra:
validar en el borde, confiar adentro (per boundary-discipline).

**Lo que persisten el apagador y el fusible.** El apagador es config
(`precio_modo_global` y `precio_modo_universo`, append-only en `config_version`,
editable en `/settings`): no hay estado nuevo. La migración siembra las claves
nuevas copiando la config vigente, así que no hay paso manual. El fusible no
tiene estado propio que sincronizar: cada ejecución es una fila de
`precio_corrida`, y su medición es una fila de `precio_compuerta_medicion` por
universo (`medidas`, `nuevas`, `causa`); una medición con causa **es** el
disparo. Lo retenido son filas de `precio_retencion` (decisión, corrida, causa)
y soltar es una fila de `precio_compuerta_liberacion` con la corrida como llave.
Cada tabla tiene un solo escritor y un solo rol; «soltado» se deriva de que la
fila exista (per separate-before-serializing-shared-state).

### El puerto

`Vitrina` es la cara de lectura (solo base, sin credenciales): `catalogo`,
`insumos`, `observado`. `insumos` lee por lote: una llamada por plataforma
devuelve la cuenta de todas las unidades pedidas, no una ronda de consultas por
unidad. `Mercado` la extiende con lo que cruza la red: `cotizar`, `precio_vivo`,
`escribir`. `cotizar` puede responder `SinCotizador` cuando el universo está
registrado sin cotización real; entonces la decisión se cierra con la fórmula y
lo declara (`verificacion = lineal`). Seis métodos de dominio y un `cerrar`. Se
partió en dos caras
porque hay tres llamadores que leen sin red y con roles distintos: la pantalla
de cobertura (`app_read`), la vista previa de goals (`app_admin`) y la corrida.

Lo que esconde: autenticación, cubos de tasa, reintentos, la forma del cuerpo de
escritura, el parseo del cable, y las dos reglas que hoy contaminan el núcleo
puro: qué es «disponible» (FBA: stock y BUYABLE; FBM: BUYABLE; Mercado Libre:
activa con stock) y cómo se parte un fee en variable y fijo (Amazon: el único
`ReferralFee`). Las reglas reciben `DiaUnidad.disponibilidad` y `Fees.variable /
Fees.fijo` y dejan de saber de inventario FBA y de `ReferralFee`.

Lo que **no** esconde, a propósito: el protocolo de `precio_cambio`. Hoy vive en
`app/spapi/precio_write.py` junto al PATCH; con una segunda plataforma eso
obligaría a Mercado Libre a reescribir la parte más peligrosa del sistema. Pasa
a `app/precio/cambios.py`, una sola vez, y el puerto solo aporta `precio_vivo` y
`escribir`. Reversa y cierre dejan de ser «de Amazon»: son el mismo protocolo
sobre otro mercado.

Ningún tipo de transporte cruza: `Cotizacion`, `Acuse`, `Insumos`,
`ObservacionPricing` son de dominio. `Insumos.cotizable` es un token opaco que el
adaptador se devuelve a sí mismo.

### La corrida: tres pasos sobre filas

```
correr(conn, mercado)
  limpieza        cerrar_huerfanas, cerrar_por_observacion     (siempre, también en off)
  decidir         una `precio_decision` por unidad que no tenga la de hoy
  compuerta       veredicto puro sobre lo persistido del día
  aplicar         cada decisión de HOY pendiente que la compuerta deje pasar
```

Hoy la fase 1 decide en memoria porque el cupo reescribía decisiones
(`mantener(cuota)`). Sin cupo nada reescribe una decisión, así que se persiste
al decidir y desaparecen el `plan` de tuplas, el orden por prioridad y el
reparto. El fusible queda donde el fundamento sugería (entre decidir y aplicar),
pero como función pura sobre filas, no sobre una lista en memoria.

`aplicar` es idempotente por comparación contra el precio vivo: si el vivo ya es
el precio nuevo, salta (`ya_aplicado`); si no es el que vio la decisión, salta
(`precio_vivo_distinto`). Por eso «pendiente» puede definirse sin estado: una
decisión de hoy `subir`/`bajar` sin cambio con efecto. Tracear el flujo completo
toma tres archivos: `corrida.py` → `cambios.py` → el adaptador.

### La compuerta

Una sola función, `compuerta.evaluar`, que se llama una vez por universo
(`plataforma/canal`). Se mide por universo y no por plataforma porque 102 de las
260 activas de México son FBM: un error en la política de FBM nunca pasaría de
39 % de la plataforma y no dispararía. Es más estricto que el literal del dueño
(«la mitad del catálogo»), nunca más laxo. Dos disparos:

- **`movimiento_masivo`** (el fusible de D1): intenciones **nuevas** del día
  sobre las unidades **medidas** del universo (las que hoy tienen cuenta; un
  `no_evaluado` es «no se sabe» y no diluye), mayor estricto a
  `precio_fusible_frac` (0.50). «Nueva» = la decisión anterior de esa unidad no
  venía ya en camino al mismo goal. Un segundo escalón de ±10 % no es intención
  nueva: así las olas de 7 días no vuelven a disparar.
- **`insumo_sistemico`**: una fracción grande de unidades vio saltar el **mismo**
  insumo (costo, envío, retención, FX, fee fijo) desde su decisión anterior. Se
  calcula comparando contra `c_valor`, `l_valor`, `r_valor`… ya guardados en
  `precio_decision`; no relee ninguna fuente. Atrapa el costo cargado 20 %
  arriba aunque no llegue a mover a la mitad, y el aviso dice cuál insumo.

Sombra y `live` cuentan juntas (la sombra es fiel: retenida, tampoco deja cambio
virtual). Debajo de `precio_fusible_min_movimientos` no dispara: con seis goals,
cuatro movimientos no son un evento sistémico.

Efecto de D6 sobre el primer encendido: si todos los goals arrancan en el margen
de hoy, el primer día **nadie quiere moverse**. El «todo el catálogo se mueve a
propósito» ya no ocurre al encender sino cuando el dueño ajusta goals en bloque,
y ahí el fusible hace lo que debe: pide una confirmación. De paso, eso da la
prueba de D2 sin esperar: sembrar a margen de hoy en `shadow` y ver casi todo en
`mantener(en_tolerancia)` demuestra que la cuenta del motor coincide con la de
la estimación; cada unidad que no lo esté señala un insumo que cambió.

`apagador`, `corte_errores` y `goal_cambiado` usan la misma tabla de retención
pero no son estado: se reevalúan en cada corrida. La config y el goal de la
decisión se releen antes de cada escritura real. Así, apagar a media corrida
corta, y pasar un goal a `shadow` o cerrarlo también: una decisión `live`
retenida en la mañana no se aplica en el repaso si su goal cambió entre tanto. El corte por errores seguidos
(`Cortacircuito`) detiene la corrida tras N errores de escritura consecutivos.

Y el cooldown deja de contar un cambio en `error` cuyo readback probó que el
precio no se movió (`sin_efecto`, derivado de columnas que ya existen). Con eso
un mal día de la plataforma no congela 7 días y `frenado(api_error)` se vuelve
alcanzable.

### Profundidad de interfaz

| Superficie | Tamaño | Qué esconde |
|---|---|---|
| `corrida.correr(conn, mercado, owner)` | 1 función | lock, limpieza, decisión con hasta dos cotizaciones, compuerta, aplicación, resumen |
| `Mercado` | 6 métodos + `cerrar` | todo el cable de una plataforma y sus dos reglas de dominio |
| `cambios.aplicar` / `revertir_lote` | 2 funciones | el ledger de cambios, huérfanas, cierre, corte |
| `siembra.planear` + `goals_write.aplicar_plan` | 2 funciones | banda, salto, huella, todo-o-nada, cierre+siembra |
| `compuerta.evaluar` | 1 función | fusible, deriva de insumos, liberaciones |

La corrida pasa de recibir cinco costuras sueltas (`lector`, `escritor`, `fees`,
`construir_cuerpo`, `limitador`) a recibir una. Lo que queda expuesto al
llamador es lo mínimo: qué plataforma y quién es el dueño del lock.

### Qué pasa si

- **La corrida se ejecuta dos veces el mismo día.** La segunda no decide lo ya
  decidido (UNIQUE por día), reusa las cotizaciones del día y solo aplica lo
  pendiente. Normalmente no hace nada.
- **Muere entre el COMMIT y la escritura.** Queda un cambio `pendiente`. La
  siguiente corrida lee el precio vivo: si no se movió, lo cierra como
  `error/huerfana_sin_efecto` (no gasta cooldown) y lo reintenta; si sí se
  movió, lo sella y lo confirma; si no puede saberlo, lo deja como hoy.
- **El fusible salta.** Salta antes de la primera escritura, sobre el día
  completo: no existe «a medias». El corte por errores sí corta a medias; lo
  que faltó se reintenta en el repaso.
- **Corren dos plataformas a la vez.** No comparten filas: lock, corrida,
  decisiones y cambios son por plataforma, y `cerrar_por_observacion` ahora
  filtra por plataforma.
- **La reversa en lote y la corrida.** Toman el mismo claim `precio:<platform>`;
  la segunda sale con «hay una corrida en curso».
- **La pantalla cambia un goal a media corrida.** Hoy el trigger de la 0039
  busca «el goal vigente» y lo escribe en la fila: si el dueño reemplazó el
  goal, la decisión quedaría anotada con un goal distinto del que el motor usó.
  Por eso la decisión guarda `goal_id`, el goal que el motor leyó, y la base
  valida **ese**. Si ya no está vigente el INSERT revienta, la corrida lo cuenta
  en `saltadas_por_edicion` y el repaso decide esa unidad con el goal nuevo.
- **El goal cambió y ya había una cotización de hoy a otro precio.** La unidad
  sale `no_evaluado(cotizacion_de_otro_goal)` hoy y se decide limpia mañana. La
  llave de la cotización (unidad, día, intento) no se toca.
- **El dueño suelta mientras la corrida corre.** La liberación es otra tabla; la
  ve la siguiente corrida.
- **La corrida muere sin cerrar.** Su fila queda `abierta`; la siguiente corrida
  de la plataforma la pasa a `abortada`. `/salud` avisa si a las 14:00 UTC no
  hay corrida cerrada del día ni resumen enviado.
- **El repaso vuelve a ver un fusible ya avisado.** Cada repaso es una corrida
  nueva con su propia medición. Un índice único deja sellar un solo aviso
  `compuerta` por universo, causa y día, y un solo resumen diario por
  plataforma y día. La corrida avisa solo si todavía no hay sello, y sella
  después de enviar.
- **El dueño pasa un goal a `shadow` con una decisión `live` retenida.** El
  repaso relee el goal antes de escribir, no aplica esa decisión y deja una
  retención `goal_cambiado`.
- **Una venta llega antes que el dato que la atribuye.** No se escribe como
  indeterminada: queda sin fila y la siguiente ingesta la reintenta.
- **Los repasos y la cuota de Amazon.** Un repaso no cotiza: las decisiones ya
  están guardadas con su cotización. Solo lee el precio vivo y escribe lo
  pendiente, así que no compite con la estimación de las 12:45 y 18:45 por la
  cuota de cotizaciones.

### Qué se borra

`app/precio/cuota.py` entero; `reglas.repartir_cupo` y `_cuota_reescrita`; el
motivo `mantener(cuota)`; las claves `precio_cap_*` (primero el código deja de
leerlas, después se quitan de la config: así no hay `exit 2`); «cupo X de Y» en
`/salud`; `prioridad` e `_ingreso_60d` (sin cupo nadie ordena, y con eso G14
desaparece en vez de arreglarse); el `plan` en memoria y la fase 2;
`moneda_contexto` (la moneda la da `Vitrina.moneda`); cinco copias de `_numero /
_entero / _fraccion` (un solo lector de config); dos copias del desarmado del
JSON `componentes` (`corrida.armar_entrada` y `tools/precio_goal._referencia`);
`construir_cuerpo` como parámetro y la rama `parche_sin_sellar` de la corrida;
`FASE_FBM`, `FASE_MELI` y `_fase_fuera_de_alcance` de la cobertura;
`EntradaDecision.motivo_estimacion` (siempre `None`); cuatro submotivos de
inventario FBA. `apply_quota_state` y sus triggers no se tocan: son de Ads.

### Candados

Los existentes quedan igual o más estrictos. La lista de excepciones de pureza
pasa de `(goals_write, fuentes, corrida, cuota)` a `(goals_write, fuentes,
corrida, cambios, liberaciones)`, y se agrega uno nuevo: **`corrida.py`,
`cambios.py` y `fuentes.py` no importan `app.spapi`, `app.meli`,
`app.estimacion_fees` ni `httpx`**. La excepción de pureza deja de ser «puede
todo» y pasa a ser «puede base, nada más». En paralelo: único `.put(` a Mercado
Libre en `app/meli/write_client.py`, único importador de ese cliente, y
`app/precio_mercados.py` como único módulo que conoce a los dos adaptadores.

### Lo que deliberadamente no hace

No revierte solo, no persigue Buy Box, no usa TACoS como regla (se muestra junto
al margen en `/precios`), no dispara la corrida por HTTP, no recorta en silencio
un margen fuera de banda, no neta el envío cobrado al cliente contra `L` y no
estima visitas de Amazon que Orbit no ingiere.

### Lo que el código dice distinto del fundamento

- **G2 choca con un trigger.** «Modo efectivo = el menor entre global y goal» no
  se puede persistir hoy: `precio_decision_coherente` exige un goal vigente del
  **mismo** modo que la decisión. Con el apagador en `shadow` y un goal `live`,
  cada INSERT revienta. El diseño cambia ese predicado (un goal `live` ampara
  una decisión `shadow`; nunca al revés).
- **G11 tiene salida sin ingesta nueva de Amazon, pero no por columna.**
  `ledger_event` es append-only por trigger: no se le puede agregar `listing_id`
  a la historia. Va en tabla aparte. Y `spapi_order_observation` ya guarda
  `fulfillment_channel` por orden, que desempata FBA de FBM.
- **G16: la fuente del cargo probablemente ya está en la base.** `ledger_event.
  source_event_id` es el `dedupe_key` de contabilidad, y los nombres
  `finance:LabmanLabelPurchase` / `shipping_label` tienen forma de prefijo de esa
  llave. Hay que confirmarlo con datos; si es así, no hace falta tocar la ingesta
  para separar Labman, etiqueta y HB.
- **Una decisión `subir` sin cambio ya es legal** (`precio_decision` no tiene
  columna `aplicado`; pasa hoy con `precio_vivo_distinto`). Por eso retener no
  necesita un resultado nuevo.
- **La banda del goal tiene dos fuentes**: el CHECK de la 0039 (0.10–0.60, fijo)
  y `precio_goal_min/max_pct` en la config. La config solo puede estrechar.
- **`SpapiWriteClient` ya admite `amazon_us`**: escribir en US no pide cliente
  nuevo, solo su sonda.
- **`meli_sku_mapping` no existe en Orbit.** Es una tabla del bridge, igual que
  `sku_mapping`. Orbit cruza el SKU de cada variante contra `product.odoo_sku`.
- **`cobertura.py` esconde FBM.** Clasifica `canal == 'fbm'` como
  `fuera_de_alcance` aunque el motor ya lo decida; se borra esa rama.
- **`estimacion_oferta_observation` y `estimacion_escenario` exigen `seller_sku`
  y `asin`.** En Mercado Libre `asin` lleva el id de la publicación y
  `seller_sku` el SKU del miembro que manda. Es deuda de nombre, declarada.
- **`precio_goal_solo_cierra_vigencia` compara columnas por nombre.** Las tres
  columnas nuevas de `precio_goal` (`origen`, `lote`, `m_referencia`) entran a
  esa lista como inmutables.

## Decisión de síntesis

Tres diseños independientes compitieron con el mismo fundamento, cada uno desde
un punto de partida distinto, y un juez en otro modelo los calificó. El detalle
está en [`juicio.md`](../../evidencia/repricing-02/diseno/juicio.md).

**Base: el candidato A** (frontera de plataforma dentro del motor), 25 de 30
contra 23 de C y 17 de B. Ganó porque pone la frontera donde un mantenedor
futuro la puede extender sin romper nada: la estimación sigue siendo la única
fuente del margen y el puerto solo observa y mueve; retener no reescribe la
decisión, así que soltar el fusible aplica el mismo día; el dinero es de una
sola moneda por construcción; y fue el único que vio que el apagador en `shadow`
revienta contra un trigger de la 0039.

**Los tres coincidieron** en lo siguiente, que se toma como acuerdo fuerte: un
puerto con tres verbos de efecto (cotizar, leer el precio vivo, escribir); el
protocolo de `precio_cambio` fuera de `app/spapi/` y escrito una vez; el envío
como tipo con origen y columna tipada, con mediana de medianas y nunca una
constante; cierre por lectura viva para revertir el mismo día; índice parcial
para resembrar un goal hoy; el plan de goals con huella dentro de `app/`; y
borrar el cupo, la prioridad y las fases de cobertura.

**Injertos de C:** `goal_id` en la decisión (cierra una carrera que A creyó
cerrada); producto ancla en `listing.product_id` en vez de permitir NULL; el
fusible por universo y sobre unidades medidas; el sello del resumen diario y el
aviso de hombre muerto; las claves de config sembradas en la migración; los
candados de arquitectura en un archivo por carril; el criterio de salida del
corte que voltea la corrida; y la auditoría de quién lee el ledger antes de
abrir Mercado Libre.

**Injertos de B:** los fees partidos como columnas del escenario; `SinCotizador`
y el cierre `lineal` declarado; la lectura por lote; y la corrección de que
`meli_sku_mapping` es del bridge.

**Rechazado:** de B, el cooldown que ignora todo cambio en `error` (un PATCH
aplicado con respuesta fallida se movería dos veces), dar goal solo a las
publicaciones de Mercado Libre con un único producto (deja fuera a 48 de 65) y
recortar a la banda al sembrar (sembrar 10 % a quien gana 8 % es decidir subirle
el precio). De C, las vistas SQL como frontera de lectura (la regla de «se podía
comprar» terminaría en migraciones que dos carriles editan), el árbol de fees de
Amazon como contrato del puerto, la reversa sin tomar el candado de la corrida y
el cron único que pone a Mercado Libre en fila detrás de Amazon. De C tampoco
se tomó la tabla de fuentes de envío ni la mediana de cambio de margen: la regla
de envío queda en código puro y probado, y la deriva por insumo ya dice cuál
insumo saltó. De A se quitó la Buy Box como evidencia de pérdida, porque
contradice una decisión vigente del dueño.

**Lo que ninguno resolvió y este documento sí:** la cotización guardada cuando
el goal cambia a media tarde, el cierre de una corrida que muere, el aviso del
fusible una sola vez, el token del escritor de Mercado Libre, y la banda de
goals frente a los márgenes de Estados Unidos (pregunta 9).

## Costos aceptados

- Aceptamos **dos ensanches de restricciones de la 0039**
  (`precio_goal_unico_por_fecha` como índice parcial; `confirmado_por` con un
  valor más) a cambio de poder reeditar un goal el mismo día y revertir sin
  esperar a mañana. Ninguna fila existente deja de ser válida.
- Aceptamos que **`listing.product_id` en Mercado Libre sea un producto ancla**
  que no dice el costo, a cambio de no tocar siete módulos y doce vistas que
  cruzan por esa columna. Una publicación sin ningún producto mapeado no tiene
  fila en `listing` y sale en cobertura como `sin_listing`.
- Aceptamos **medir el fusible por universo**, que dispara con menos productos
  que «la mitad del catálogo», a cambio de atrapar un error que solo afecta a un
  canal.
- Aceptamos **cerrar `lineal`, sin cotización real,** en un universo donde la
  sonda no encuentre cotizador, a cambio de que ese universo califique (D3). Se
  declara en la decisión y en la pantalla; el escalón de 10 % y la regla del
  doble acotan el error.
- Aceptamos que **Mercado Libre entre a las tablas de estimación** (con `asin`
  llevando el id de publicación y un valor nuevo de enum en dos pasos) a cambio
  de un solo margen por producto en todo Orbit y de conservar el rastro de
  auditoría de la 0039.
- Aceptamos **un corte inicial en dos partes antes de paralelizar del todo**
  (0a contratos, 0b voltear la corrida) a cambio de que ningún carril edite el
  archivo de otro después.
- Aceptamos que **soltar el fusible no aplique al instante**: aplica el
  siguiente repaso (hasta dos horas). A cambio, ninguna pantalla dispara
  escrituras a una plataforma (el Reject de `/run` sigue en pie).
- Aceptamos que **tras un movimiento en bloque los productos queden
  sincronizados en olas de 7 días** (G8). Sin cupo la ola no cuesta nada más que
  minutos de corrida, y el fusible no la cuenta como intención nueva.
- Aceptamos **`live` con envío imputado** (D3) a cambio de cobertura total, con
  el origen visible en decisión, pantalla y resumen. El imputado del marketplace
  en MX se equivoca ~2 puntos de margen en ~42 % de los productos, siempre hacia
  subir de más; se corrige solo con el primer envío propio.
- Aceptamos que **la publicación con variantes se gobierne por su variante de
  menor margen**: las demás quedan por encima del goal.
- Aceptamos **una fila de `precio_corrida` por ejecución, repasos incluidos**
  (unas 20 filas al día) a cambio de que resumen, salud, fusible y reversa por
  corrida salgan de una sola fuente.
- Aceptamos que **la señal por producto siga siendo débil**: con ~0.2 ventas por
  producto cada 15 días ninguna métrica por producto es concluyente. La
  evidencia por tráfico (D10) solo se evalúa después de una subida propia, y la
  lectura de cohorte es aviso, no acción. La Buy Box sigue siendo solo aviso.
- Aceptamos **`Vitrina` y `Mercado` como dos protocolos** en vez de uno, porque
  la pantalla no puede cargar credenciales.

## Alternativas consideradas

- **Un motor por plataforma** (`corrida_meli.py` junto a `corrida.py`,
  compartiendo solo las reglas puras). Es la que da carriles más independientes
  y por eso tentaba. Pierde en profundidad: el llamador ve N motores, y cada uno
  repite lo que más conviene tener una vez (protocolo de cambios, compuerta,
  huérfanas, reversa). El fusible y el apagador tendrían N implementaciones que
  mantener iguales a mano.
- **Todo es dato: normalizar en la base y no tener puerto de lectura.** Cada
  ingesta escribe en tablas o vistas comunes (`precio_observacion`,
  `precio_disponibilidad`), la corrida solo lee tablas genéricas y lo único por
  plataforma es una función de escritura. La superficie es más chica, pero
  esconde menos: la regla de disponibilidad por canal y el partido de fees
  terminarían en SQL dentro de migraciones (cada ajuste, una migración; los
  carriles F y M chocando en el mismo archivo), y cotizar sigue necesitando
  código por plataforma. Se adoptó en lo que sí es dato: `v_precio_goal_vigente`,
  `v_precio_venta_unidad`, `v_precio_unidad_miembro`.
- **El puerto produce el escenario** (la dirección de partida al pie de la
  letra). Interfaz más simple de enunciar, una sola frontera. Perdió por lo
  dicho en Forma: dos márgenes del mismo producto y FKs de auditoría sin destino.
- **Solo la frontera de abajo: extender la estimación y dejar la corrida con
  ramas por plataforma.** Es el cambio más chico. Deja `corrida.py` como el
  punto donde chocan los cuatro carriles y obliga a Mercado Libre a copiar
  `precio_write.py`. No esconde nada nuevo; reparte `if platform` por el archivo
  más delicado.
- **Grano de variante en `listing`** (una fila por variante, la publicación como
  agrupador). Encaja con «un listing, un producto» sin tocar `product_id`, pero
  rompe el grano del precio: N decisiones y N cambios por una sola escritura
  real, y el índice de un cambio abierto por listing deja de proteger nada.

## Preguntas abiertas y riesgos

Para el dueño:

1. En una publicación de Mercado Libre con varias variantes y un solo precio,
   ¿el margen que se cuida es el de la variante que menos deja (todas quedan en
   el goal o arriba) o el promedio según lo que se vende de cada una? El diseño
   asume la primera.
2. Al sembrar «goal = margen de hoy», ¿qué quieres con los productos cuyo margen
   de hoy está fuera de 10–60 %? El diseño los lista aparte y no los siembra
   hasta que confirmes ese grupo, porque ponerlos en 10 % ya es decidir subirles
   el precio. ¿O prefieres que se siembren en el borde sin preguntar?
3. ¿Confirmas que el envío de US es etiqueta más `ShippingHB` (~530 MXN), no
   solo la etiqueta? Así se asume.
4. ¿Qué retención se usa en US: 2.07 % (ISR medido) o 6.56 % (`tax_withheld`)?
   Es un número de la política y cambia el margen de todo US unos 4.5 puntos.
5. ¿Está bien que al soltar el fusible los precios se muevan en el siguiente
   repaso (hasta dos horas) y no en el momento?
6. ¿El apagador por universo basta (`amazon_mx/fba`, `amazon_mx/fbm`,
   `amazon_us/fbm`, `meli/meli`), o quieres también uno por familia?
7. Una variante de Mercado Libre sin producto mapeado (12 SKUs hoy) deja a toda
   su publicación sin evaluar hasta que la mapees. ¿De acuerdo, o prefieres que
   se evalúe con las variantes que sí están mapeadas?
8. Para la reversa en lote se exige apagar antes el universo. ¿De acuerdo?
9. La banda de goals es 10–60 %. En Estados Unidos el margen medido va de 33 %
   a 63 %: los productos arriba de 60 % no se pueden sembrar «al margen de hoy».
   ¿Subimos el tope de la banda (por ejemplo a 80 %)? El diseño los lista aparte
   mientras tanto.

Ninguna de estas preguntas frena la construcción: cada una tiene el valor por
omisión que se indica y se cambia sin rehacer el diseño.

Riesgos que resuelve una sonda, no el diseño:

- ¿Product Fees responde `Success` para una oferta FBM de US y de MX con
  `IsAmazonFulfilled: false`? Si no, ese universo no tiene cotización real y
  queda en `fee_ausente` hasta encontrar otra fuente.
- ¿El token de Mercado Libre permite escribir, y el precio se manda en la
  publicación o repetido por variante? `MeliWriteClient` nace con la forma sin
  sellar.
- ¿La consulta de cargos de Mercado Libre por precio, categoría y tipo de
  publicación devuelve el cargo porcentual y el fijo separados, y con o sin IVA?
- ¿El prefijo de `source_event_id` distingue Labman, etiqueta y HB?
- ¿El SKU de cada variante viene en la publicación o hay que mapearlo a mano?
- ¿Un listing FBM sin existencias deja de ser BUYABLE? De eso depende que
  «disponible» en FBM sea solo el estado.

Riesgo de construcción: el corte 0b voltea el archivo más delicado del motor.
Se mitiga exigiendo que sea de comportamiento idéntico para MX FBA: las pruebas
existentes de la corrida pasan sin cambiar sus aserciones, solo su armado.

## Siguiente paso de implementación

Escribir el corte 0a: las migraciones 0053 y 0054, `app/precio/puerto.py` y los
tipos nuevos de `tipos.py`, los dos registros con sus módulos vacíos
(`app/precio_mercados.py`, `app/estimacion_universo.py`) y el candado nuevo de
imports, sin cambiar una sola línea de comportamiento.

## Huecos cubiertos

| # | Qué lo cierra |
|---|---|
| G1 | Se borran `cuota.py`, `repartir_cupo`, `mantener(cuota)`, `prioridad`, la fase 2 y «cupo X de Y». `precio_cap_*`: el código deja de leerlas antes de quitarlas de la config. El punto de sabotaje entre decidir y aplicar pasa a `compuerta.evaluar` (E, J). |
| G2 | `precio_modo_global` y `precio_modo_universo` en `config_version`; `modo_efectivo` (E); el modo efectivo se guarda en la decisión; cambio del predicado de modo en `precio_decision_coherente` (datos §7). Se relee antes de cada escritura. |
| G3 | `compuerta.evaluar` sobre decisiones persistidas (E); `precio_compuerta_medicion` guarda la medición por universo; `precio_retencion` y `precio_compuerta_liberacion` (datos §5–6); `POST /api/precios/compuerta/{id}/liberar` (T). Se mide por `plataforma/canal`, sobre las unidades con cuenta del día. Las decisiones se guardan como son (`subir`/`bajar`), sin cambio. Primer encendido: resuelto por D6 más «intención nueva». |
| G4 | Disparo `insumo_sistemico` por deriva de costo, envío, retención, FX o fee fijo contra la decisión anterior (E); tope `fx_max_dias` por universo (M). |
| G5 | Cooldown ignora cambios `sin_efecto` (F, datos §8), con lo que `frenado(api_error)` es alcanzable; `Cortacircuito` por errores seguidos (E, J); huérfanas resueltas por lectura viva (I). |
| G6 | `Seleccion` por fecha, por corrida o por ids; huella sobre el conjunto y salto por fila; cierre del original por lectura viva en el momento; todo filtrado por plataforma (I, V). |
| G7 | Tipos `resumen_diario`, `compuerta`, `cambio_error`, `corte_errores`, `reversa`, `cohorte` en el mismo sender (U). El resumen y el aviso de compuerta llevan sello y salen una vez; `/salud` avisa si falta el resumen del día. |
| G8 | No se escalona. D6 hace que el primer día nadie se mueva; tras un ajuste en bloque quedan olas de 7 días que el fusible no cuenta. Costo aceptado. |
| G9 | Un `flock` por grupo de cuota; las plataformas del mismo grupo corren en serie en un proceso (`Mercado.grupo_tasa`, CLI con `--platform` repetible). |
| G10 | `MOTIVOS_NO_EVALUADO` se deriva del vocabulario que exporta la estimación más los propios; una prueba exige el superconjunto (A). |
| G11 | `ledger_event_atribucion` y `v_precio_venta_unidad` (datos §2); `HistoriaUnidad` trae ventas de la unidad y cuenta lo no atribuible (B, P). |
| G12 | `DiaUnidad.disponibilidad` normalizada por el adaptador según canal (B, Q); salen los submotivos de inventario FBA. |
| G13 | Señal con dos evidencias en orden (unidades, y tráfico tras una subida propia) y lectura de cohorte como aviso (D). La Buy Box sigue siendo solo aviso. Visitas de Mercado Libre en su observación diaria. Declarado: por producto sigue siendo débil. |
| G14 | Desaparece: `ingreso_60d` solo alimentaba la prioridad del cupo. |
| G15 | `Envio` como tipo suma; `resolver_envio`; columnas de origen en muestra, escenario y decisión con CHECK (A, C, datos §4). `L` entra a la estimación por `logistica_del_universo` (M). Todo sale del ledger: el export de Seller Central no se usa (D4). |
| G16 | `clasificar_cargo` y `costo_de_orden` (`max(labman_label, shipping_label, mfn_postage) + shipping_hb`), `Exclusiones` contadas, importes en positivo (C, O). Las 148 ventas sin producto se cuentan en `excl_sin_producto`. |
| G17 | Declarado, no resuelto: el envío cobrado al cliente se guarda como evidencia (`cobrado_n`, NULL en US) y no se resta de `L`. Razón: sin dato en US y parcial en MX; netearlo mezclaría medido con supuesto. |
| G18 | Universo `amazon_us/fbm` con su política (I = P, retención, costo y envío convertidos con `fx_resolve`). La política es una fila que inserta el dueño. Declarado: falta la sonda de Product Fees y la respuesta a la pregunta 4. |
| G19 | `Convertido`; `Cuenta` en una sola moneda por construcción; la muestra se queda en MXN (A, M). |
| G20 | TACoS junto al margen en `/precios`, solo lectura (T). Ninguna regla lo usa. |
| G21 | Publicación = fila de `listing` con producto ancla; `listing_miembro`; `miembro_que_manda`; puerta para variantes sin reescribir (B, datos §1). |
| G22 | La ingesta de catálogo de Mercado Libre llena `listing_miembro` cruzando SKU con `product.odoo_sku`; lo que no cruza queda con `product_id` nulo y visible (R). Declarado: de dónde sale el SKU lo dice la sonda. |
| G23 | `Atribuidor` por plataforma reemplaza la rama «meli excluida» de `ledger.py`; `app/meli/ledger.py` (P, R). |
| G24 | `meli_publicacion_observation` diaria con `ClienteMeli` (datos §3, R). |
| G25 | Comisión: `MercadoMeli.cotizar` con la consulta de cargos por precio. Envío: la misma muestra con origen, medida del ledger ya abierto. Declarado: forma exacta de la respuesta, por sonda; si no hay consulta de cargos, el universo se registra sin cotizador y cierra `lineal`. |
| G26 | `MeliWriteClient` aparte, default-deny, forma sin sellar hasta la sonda; reversa por el mismo `cambios.revertir_lote`; `ClienteMeli` sigue GET-only (R). |
| G27 | `MercadoMeli.catalogo`: última observación con estado activo (R). |
| G28 | `siembra.planear` y `goals_write.aplicar_plan` en `app/`; `DetalleSinGoal.listing_id`; `precios.js`; todo o nada en una transacción; reedición el mismo día por índice parcial (G, K, T, datos §9). |
| G29 | `PedidoMargenDeHoy`: fuera de banda se lista y pide confirmación del grupo; sin escenario no se siembra y se muestra con su motivo; la siembra es repetible (solo toca unidades sin goal). |

Además, sin número en el fundamento y cubierto aquí: la carrera entre la
pantalla y la corrida (`goal_id`), el choque del apagador con el trigger de
modo, la cobertura que escondía FBM, y los productos de Estados Unidos fuera de
la banda de goals (pregunta 9).

## Carriles y primer corte

**Corte 0a — contratos (un PR, sin cambio de comportamiento, destraba a todos).**
Migraciones 0053 y 0054 completas, salvo los cuatro CHECK que exigen una
columna nueva. Esos llegan con el código que escribe la columna: la 0058 con
S.1 y la 0055 con F.3. Un CHECK `NOT VALID` perdona las filas viejas, no los
INSERT nuevos, así que la 0054 no puede exigir lo que el código desplegado
todavía no escribe. `app/precio/puerto.py`. Tipos y vocabulario nuevos en
`tipos.py`. `config.py` como lector único. `app/precio_mercados.py` y
`app/estimacion_universo.py` con las cuatro entradas ya registradas, apuntando a
módulos que existen vacíos (`app/meli/…`, `app/estimacion_amazon.py`,
`app/ledger_atribucion.py` con sus dos atribuidores). `ledger.py` ya despacha
por plataforma (meli sigue devolviendo «excluida» hasta que M llene su módulo).
Candado nuevo de imports. Partir `precios.html` en `_precios_seguridad.html` y
`_precios_goals.html`, y sacar el bloque de precios de `api_dashboard.py` a
`app/api_precios.py`. El motor sigue corriendo por el camino viejo.

Después de 0a los registros ya nombran a todos: cada carril llena cuerpos en
archivos propios y nadie edita un registro.

**Corte 0b — voltear la corrida (un PR, lo hace el carril S; F revisa).**
Extraer `MercadoAmazon` de `corrida.py` y `precio_write.py`; `cambios.py`;
`corrida.py` detrás del `Mercado`, con su orden de hoy; `leer_cuenta`;
cobertura sobre `Vitrina`. Comportamiento idéntico para MX FBA. **Criterio de
salida:** las decisiones de un día en sombra son idénticas fila por fila antes
y después, y la lista de activas del adaptador coincide con la consulta actual
(`_SQL_CANO`) sobre datos reales.

| Carril | Archivos propios | Empieza tras | Necesita de otro |
|---|---|---|---|
| **F** FBM MX + US | `app/estimacion_amazon.py`, `estimacion_{insumos,fees,ingest,repository,venta}.py`, `app/precio/envio.py`, `app/envio_muestras.py`, atribuidor de Amazon, `app/spapi/precio_mercado.py` (tras 0b), `tools/precio_sonda.py` | 0a | 0b solo para disponibilidad FBM y ventas por unidad en el adaptador |
| **M** Mercado Libre | todo `app/meli/` (catálogo, mercado, escritor, estimación, ledger) | 0a | nada: prueba su `Mercado` contra el protocolo |
| **G** goals en pantalla | `app/precio/siembra.py`, `goals_write.py`, `app/api_precios.py`, `app/api_precios_write.py` (rutas de goals), `_precios_goals.html`, `static/js/precios.js`, `tools/precio_goal.py` | 0a | `leer_cuenta` (0b) para la vista previa |
| **S** seguridad y avisos | `corrida.py`, `cambios.py`, `compuerta.py`, `liberaciones.py`, `reglas.py`, `objetivo.py`, `ventas.py`, `fuentes.py`, `notifica.py` (bloque de precios), `_precios_seguridad.html`, `tools/precio_reversa.py` | 0a (0b es suyo) | — |

Los tres pasos sobre filas guardadas (decidir, compuerta, aplicar) llegan en
S.1, junto con el borrado del cupo. No caben antes: hoy el cupo reescribe la
decisión a `mantener(cuota)` antes de guardarla, y `precio_decision` no admite
UPDATE.

S.7 (la señal por unidad) es del carril S pero espera a F.1 y F.4, que
atribuyen las ventas y arman la historia de cada unidad. Se despliega con el
carril F. Hasta entonces el motor usa la señal por producto de hoy.

Archivos de contrato, completos desde 0.a: `tipos.py`, `puerto.py`,
`config.py`, los dos registros y las migraciones 0053 y 0054. Después solo se
editan para borrar lo que un paso deja de usar. La tabla "Quién edita cada
archivo compartido" de la guía dice quién toca cada archivo que usa más de un
paso. Si un carril necesita agregar algo a un contrato, es un PR aparte.

Dos choques que quedan y cómo se evitan: los candados de arquitectura (cada
carril escribe los suyos en un archivo propio, `tests/test_arq_precio_{s,g,f,m}.py`;
`tests/test_architecture.py` lo editan 0.a, 0.b y el carril S) y el router de escritura
(`/compuerta/liberar` es de S pero vive en el archivo de G: S entrega
`liberaciones.liberar` y G cablea la ruta de cinco líneas).

**Lo que sí va en fila:**

1. 0a → 0b.
2. Por universo: política sellada por el dueño → sonda de cotización → sonda de
   escritura de ±0.01 con su reversa → siembra a margen de hoy en `shadow` → uno
   o dos días viendo que casi todo queda en tolerancia → `live` en
   `precio_modo_universo`. El primer universo que se enciende sube además
   `precio_modo_global` a `live`: la migración lo siembra en `shadow` y el
   modo efectivo es el menor. Cada universo recorre esta fila por su cuenta; no
   esperan entre sí (D9).
3. La migración 0054 siembra las claves nuevas de config (copia la vigente y
   suma las nuevas), así que no hay paso manual. Las `precio_cap_*` se quedan
   inertes: borrarlas antes de desplegar haría fallar a la versión vieja.
4. La primera corrida de la ingesta del ledger con atribución llena toda la
   historia; la señal por unidad y las muestras de envío dependen de ella.
5. Antes del primer INSERT de Mercado Libre en `ledger_event`, el carril M
   audita cada vista y reporte que agrupa por plataforma (margen, TACoS,
   contribución): desde esa fila verán a `meli`. Es la forma más probable de
   romper otro motor sin querer.
6. Apagador y fusible (carril S) en producción antes del primer goal `live` de
   un universo nuevo.
