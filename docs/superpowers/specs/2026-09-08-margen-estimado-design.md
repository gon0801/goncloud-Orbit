# MARGEN ESTIMADO 01 — Estimacion por venta antes de Ads

> Spec, insumo de `plans/margen-estimado-01.md` (plan propuesto,
> no iniciado). El índice de planes/pendientes se escribe solo
> en `plans/ROADMAP.md`.

Estado: propuesta formal para ejecucion por bloques, 2026-09-08 UTC.
Autorizacion: el dueno respondio «ok» a formalizar el margen estimado para
productos nuevos. Esta entrega documenta el plan; no implementa ni autoriza gasto.
Base inspeccionada: origin/master `80b6403`.

## Objetivo y precedencia

Ayudar a comparar publicaciones Amazon MX/US incluso con cero ventas mediante
una estimacion de una venta al precio y condiciones identificados. No requiere
30 fechas con venta. No predice si habra ventas ni cuanto venderan los anuncios.

Spec delta: desarrollar la ampliacion de margen prospectivo declarada en
`2026-09-06-catalogo-campanas-abierto-design.md`, conservando D1–D4. No existe
spec.md raiz en la base inspeccionada; se sigue la convencion del repositorio.
Precedencia: `docs/CONTEXTO.md` y diseno del optimizador → contratos observados
ORBIT 19 → este sub-spec para estimaciones → `plans/margen-estimado-01.md`.
Un cambio fiscal o de target no puede introducirse bajo esta ampliacion.

## S1. Tres medidas, tres significados

| Medida | Grano y tiempo | Uso |
|---|---|---|
| Margen neto observado antes de Ads | Producto + mercado; ventana historica y madurez vigentes | Mantiene reglas actuales |
| Margen observado · muestra limitada | Mismo grano; 1–29 fechas con venta y guardas de integridad | Informacion; no deriva target ni entra al orden maduro |
| Contribucion estimada por venta · antes de Ads | Publicacion + oferta/canal + escenario de precio fechado | Comparacion informativa; no gobierna el motor |

La tercera etiqueta es deliberada: una venta estimada no incluye por defecto
reembolsos futuros, costos periodicos o gastos generales. «Neto estimado» solo
podria usarse tras una ampliacion que acredite ese alcance. El titulo de este
plan conserva «margen estimado» como nombre de la necesidad del usuario.

La v1 modela una unidad vendible al precio documentado, con entrega completada
sin devolucion, con costos directos y retenciones aplicables identificados.
No se usa tasa historica promedio para inventar devoluciones, cargos o impuestos
de un producto nuevo. Almacenamiento, inbound, importacion, embalaje y descuentos
se clasifican en 0.2: incluido en COGS/fee, costo directo adicional, no aplicable
con evidencia, o excluido del escenario con justificacion visible. Una clase
directa aplicable sin importe no puede declararse excluida para obtener un total.
Devoluciones, costos periodicos y gastos generales excluidos se enumeran siempre;
no se contabilizan como ceros. La comparacion historica no promete misma base.

## S2. Fuentes y contrato previo obligatorio

Antes de implementar, cada componente debe tener un contrato verificable:
fuente unica, clave, unidad vendible, moneda original, signo, base fiscal,
impuestos incluidos, pertenencia a totales, fecha del hecho, fecha de captura,
vigencia, politica de frescura, regla de ausencia y evidencia de conciliacion.

| Componente | Fuente existente o candidata | Lo que falta verificar en 0.1/0.2 |
|---|---|---|
| Identidad/oferta | `listing` y puente `amazon_sku_mapping` | Multiples seller SKU por ASIN, canal y unidad/BOM; no elegir una oferta por orden |
| Precio | Bridge `amazon_listing_prices.price` | Significado/frescura de `fetched_at`, impuestos, promociones, shipping cobrado, monedas y cardinalidad |
| Costo | `sku_cost` desde accounting/Odoo | Vigencia actual y cobertura de costos directos; unidad igual a la venta |
| FX | `fx_resolve` y `fx_rate` existentes | Cobertura a fecha del escenario; conservar direccion USD→MXN y conversion inversa por division |
| Comisiones | SP-API Product Fees, candidato oficial | Acceso real por mercado, respuesta por item, alcance fiscal/logistico y correspondencia exacta del precio |
| Logistica | Cotizacion FBA o fuente FBM verificable | Que cubre Amazon; envio/embalaje no cubiertos; stock no es tarifa |
| Retenciones y tratamiento fiscal | Contrato financiero del negocio, fuentes oficiales y evidencia de accounting | Base/tasa/vigencia/aplicabilidad por mercado; no inferir de filas ausentes ni del nombre del mercado |

No reusar `product_economics_expected` ni codigo del sistema cancelado.
El ledger observado sirve para contrastar componentes, no como tarifa actual.
El snapshot mutable `listing_price` no es prueba de frescura. `created_at`,
hora de consulta e `ingest_run` no sustituyen fecha fuente del precio.

0.3 debe fijar valores concretos de frescura y periodicidad para cada fuente,
justificados por su cadencia real. No se introduce un TTL financiero arbitrario.
Si una fuente no proporciona una fecha fiable, ese estado es desconocido.
Una corrida fallida o repetir un snapshot viejo no rejuvenece los insumos.

Fuentes oficiales consultadas el 2026-09-08 UTC:

- [Amazon, estimacion por SKU](https://developer-docs.amazon.com/sp-api/reference/getmyfeesestimateforsku): cotiza para SKU, marketplace y precio propuesto; los cargos reales pueden variar. El SKU puede reflejar medidas reales una vez recibido por Amazon.
- [Amazon, acceso a estimaciones por SKU](https://developer-docs.amazon.com/sp-api/docs/get-product-fee-estimates-sku): verificar autorizacion del vendedor y roles antes de sondear.
- [Modelo oficial Product Fees v0](https://github.com/amzn/selling-partner-api-models/blob/main/models/product-fees-api-model/productFeesV0.json): contrato de identificador, canal, precio, moneda, total y desglose; fijar revision exacta en 0.2 antes de implementar.
- [Amazon, estimaciones en lote](https://developer-docs.amazon.com/sp-api/reference/getmyfeesestimates): existe operacion batch; cuota efectiva y errores se verifican en la sonda. No se compromete batch como requisito de v1.
- [SAT, referencia de tratamiento de IVA en plataformas](https://wwwmat.sat.gob.mx/articulo/80052/regla-12.2.11): la pagina consultada referencia RMF 2024; solo acredita que hay tratamientos distintos. No prueba tasa, regimen ni regla vigente aplicable a esta cuenta en 2026. 0.2 debe verificar documentos vigentes y evidencia del negocio.

Esta consulta documental no demuestra permisos reales, tarifas de la cuenta,
cobertura actual ni montos productivos. No se hicieron sondas financieras en esta
planificacion. Deben registrarse resultados por mercado, sin sumar MX+US.

## S3. Formula y fiscalidad sin doble conteo

Para un escenario de una unidad, todos los terminos en la misma moneda:

```text
I = ingreso de la venta normalizado a la base economica documentada
C = costo vigente de esa unidad, normalizado a la misma base
F = comisiones aplicables, incluyendo una sola vez impuestos no recuperables
L = logistica y costos directos aplicables no incluidos ya en C ni F
R = retenciones/cargos fiscales que el contrato del negocio trata como costo
contribucion_estimada = I - C - F - L - R
contribucion_estimada_pct = 100 * contribucion_estimada / I  (solo I > 0)
```

ISR sigue siendo costo de primer orden segun el contrato de Orbit. El IVA
incluido en el precio, IVA sobre comisiones y retencion de IVA son conceptos
distintos: no restarlos dos veces ni confundir liquidacion de caja con resultado
economico. No dividir por 1.16 ni aplicar tasas universales por intuicion.
No cambiar como se calcula el margen historico. Si su base difiere, explicarlo.

0.2 debe documentar con ejemplos el paso de precio bruto a I y la construccion
de R para MX y US; tasas, bases y vigencias proceden de evidencia, no de esta
formula simbolica. Si no puede cerrarse, el resultado numerico queda bloqueado.

Un total de fees y sus detalles representan el mismo cargo: utilizar una sola
representacion para sumar y reconciliar la otra. Incluir impuestos, descuentos
de fees y fulfillment segun semantica de la fuente. Errores por item, moneda
inesperada, importes no finitos o componentes que no concilian invalidan el
resultado; una respuesta HTTP exitosa no basta.

Todos los obligatorios deben estar presentes y vigentes. `None` no se omite
de la suma ni se convierte en cero. Costo cero conserva el rechazo actual de
`sku_cost`. Fee/retencion cero requiere evidencia de cero o no aplicabilidad.
Resultado cero o negativo es valido y visible. Precio/ingreso no positivo no
produce ratio. Dinero Decimal/NUMERIC(14,4), porcentajes decimales y serializacion
como cadenas; redondear solo en frontera declarada, no en pasos intermedios.

El porcentaje usa I, no `sales30d` de Ads, que puede incluir halo. No mostrar
«ACoS de equilibrio», no restar ACoS al estimado ni sugerir un target a partir
de el. Tampoco es pronostico de utilidad mensual o garantia de rentabilidad.

## S4. Identidad, persistencia y reproducibilidad

- Escenario por listing_id, plataforma, seller SKU y canal verificados. Dos ASIN
  del mismo producto pueden producir estimaciones diferentes; no promediar ni
  tomar el menor precio para ocultar multiples ofertas incompatibles.
- La v1 no crea selector nuevo de ofertas: si el listing no resuelve una oferta
  unica y coherente, `identidad_ambigua`; mostrar el faltante. Abrir ese selector
  requiere ampliar el contrato en 0.3.
- Migracion expansiva NUEVA, numero elegido contra master fresco al implementar;
  no editar migraciones selladas ni reservar el numero que usa Reputacion.
- Observaciones de precio/cotizacion y contexto calculado append-only: identidad,
  fecha fuente, observed_at, entrada canonica y huella de precio/canal, IDs de
  costos/FX usados, version de politica y formula. Correccion = nueva observacion.
- Precio o cotizacion repetidos se deduplican por evento fuente; un cambio real
  se conserva. Estado de intento fallido se registra aparte del ultimo exito.
- Recalcular un snapshot usa sus insumos congelados, no el costo/FX mas reciente.
  Consultar un as-of no puede usar observaciones capturadas despues del corte.
  Las tablas existentes de costos/FX no se presentan como bitemporales si no
  conservan esa dimension: guardar los valores y referencias usados.
- El contexto nuevo selecciona costo vigente en su fecha, sin prolongar vigencias
  sobre huecos. La conversion MXN→USD divide por USD→MXN resuelto por el camino
  existente; conserva valor/moneda originales, tasa, fecha y procedencia.
- Una cotizacion solo sirve al precio, marketplace, moneda, SKU y canal para los
  que fue obtenida. Cualquier cambio invalida su uso actual, aunque siga reciente.
- Tiempos UTC conscientes; invariantes temporales en trigger UTC, no CHECK
  dependiente del reloj. Rechazar fechas fuente futuras fuera del contrato.

## S5. API y limites de escritura

Nuevo bloque aditivo `estimacion` en GET `/api/fabrica/catalogo` y
`/api/fabrica/evaluacion`, sin renombrar campos actuales. Lectura por lotes,
sin una consulta o llamada externa por cada tarjeta. Una proyeccion comun
resuelve los mismos snapshots para ambas rutas.

Contrato propuesto a cerrar en 0.3:

```text
estimacion:
  estado: disponible | incompleta | desactualizada | identidad_ambigua
  motivos: lista de codigos legibles
  snapshot_id: identificador o null
  escenario: unidad, canal, fecha_valoracion, version_formula, version_politica
  moneda: MXN | USD | null
  contribucion: cadena decimal o null
  contribucion_pct: cadena decimal o null
  base_porcentaje: ingreso_normalizado
  componentes: nombre, importe original/moneda, importe normalizado/moneda,
               fuente, fecha_fuente, observed_at, vigencia, estado, pertenencia
  exclusiones: clases expresamente fuera del escenario
```

`disponible` exige componentes completos para el escenario; no afirma confianza
estadistica. En cualquier otro estado, los valores principales son null. El
ultimo valor vencido puede verse en detalle con su fecha, nunca como valor actual.
Sin politica fiscal aprobada corresponde `incompleta` y motivo concreto.

GET solo DB con rol de lectura: sin OAuth, sin cotizacion externa ni escrituras
de snapshots. El boton Actualizar solo relee datos guardados. La ingesta separada
usa rutas SP-API de consulta/cotizacion expresamente permitidas, origen fijo,
timeouts y retries acotados; no modificar el guard read-only de Amazon Ads.
No crear una segunda integracion si ya existe una fuente equivalente verificable.
Snapshots bridge/accounting con `.backup()` y lectura `mode=ro`; no modificar
sus bases, servicios o scheduler. No registrar secretos ni payloads sensibles.

## S6. Diseno de pantalla y aceptacion de producto

La tarjeta conserva foto, checkbox y enlace Amazon independiente. Agrega la
contribucion estimada junto a las medidas observadas con sus etiquetas propias.
El comparador conserva los datos actuales y muestra estimacion/desglose mediante
detalle accesible para evitar otra tabla excesivamente ancha. Importe por unidad,
porcentaje con denominador explicado, moneda, fecha y escenario siempre accesibles.
Costos excluidos se resumen junto al numero y se enumeran en el detalle.

Estados necesarios: sin ventas con estimacion, muestra limitada con estimacion,
maduro con estimacion distinta, incompleta con faltantes, vencida, oferta ambigua,
cero y negativo. No mostrar «no disponible para anunciar» por estos motivos.
No crear una nota 0–100 ni etiquetas de exito publicitario a partir de la estimacion.

Orden inicial por margen maduro intacto; no completar huecos con estimaciones.
Orden adicional por estimado es Recommended, fuera de la v1 requerida; exige
comparabilidad de escenario, politica y moneda y desempate listing_id.
Seleccion preservada al filtrar, actualizar y cambiar orden. Dia/noche y movil
390px; teclado, etiquetas y contraste; ausencia de desbordamiento de pagina.

## S7. Aislamiento del motor y cierre

Cambiar exclusivamente una estimacion no cambia elegibilidad, objetivo,
presupuesto, bid, modo, plan canonico, huella ni recuperacion v1/v2. No incorpora
`estimacion` a `margen_neto_pct`, goals, ledger o decisiones. Sin margen maduro
fiable, manual sigue obligatorio; no relajar madurez ni clamp actuales.

Las pruebas demuestran estos invariantes con snapshots distintos y mismo plan.
Primero SELECT real sobre forma de datos; luego regresion roja sobre codigo
anterior, verde focal; bateria completa una vez por entrega en PR CI (PostgreSQL
activo), Ruff/pre-commit, revision independiente y smoke navegador.

No basta publicar un catalogo entero en estado incompleto para cerrar el plan.
Se requieren estimaciones conciliadas de publicaciones sin ventas en cada
mercado/canal comprometido en 0.3, mas casos negativos y ausentes reproducibles.
0.3 fija universo, conteos por motivo y casos representativos de aceptacion; las
limitaciones materiales o un mercado no soportado requieren enmienda visible.
Comparar contra precio fuente y cotizacion externa del mismo contexto, no contra
el propio total guardado. Contrastar cargos posteriores solo cuando haya ventas
comparables; diferencia estimado/real no demuestra por si misma error de formula.

Despliegue solo Orbit con backup y migracion aditiva; ensayo antes de publicar.
Reversa desactiva la ingesta nueva y retira la proyeccion UI/API manteniendo
observaciones y la recuperacion v1/v2. No DROP ni rollback del codigo de otra
sesion. Reputacion, bridge y accounting permanecen fuera del alcance.

## S8. Resultado del bloque 0 (2026-09-08 UTC)

0.1 y 0.2 confirmaron precio/canal fechado en bridge, costo y FX reutilizables,
y Product Fees accesible para FBM MX/US y FBA MX/US. No completaron el contrato:
bridge no conserva base fiscal de `price` ni RFC por API. El dueño confirmó RFC PF válido y 16% IVA para las publicaciones MX vigentes; FBA MX queda liberado bajo esa política. El precio
expira a las seis horas y no hay kits: el dueño confirma unidad uno por listing con `product_id` y
que el COGS incluye importación, transporte de entrada y embalaje. FBM queda sin
principal antes de vender porque la guía depende del pedido; US queda sin política
prospectiva compatible. Acta y evidencia:
`docs/evidencia/margen-estimado-01/0.1/`, `0.2/`, `0.3/acta.md`.

Por ello A.1 queda liberada únicamente para FBA MX. FBM y US siguen fuera del alcance. El estado correcto de FBM/US
ante esos huecos sigue siendo `incompleta` o `desactualizada`, con principal
`null`; no se degrada a precio menos costo ni se convierte la tarifa histórica en
default.
