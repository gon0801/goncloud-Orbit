# ORBIT 19 — Catalogo abierto y comparacion para campanas

Estado: ESPECIFICACION FORMAL v1, 2026-09-06; decisiones de negocio pendientes.
Formalizada con harness-plan; solo planificacion, no aprobacion de ejecucion.
Solicitud: todos los articulos deben poder anadirse a campanas, con metricas
que ayuden a distinguir cuales conviene anunciar. No autoriza crear anuncios.

## Contrato propuesto

Separar seleccion, condiciones tecnicas, evaluacion y evidencia. Un margen
bajo/negativo, pocas ventas o falta de historial no deshabilitan la seleccion.
Una recomendacion asesora no crea, pausa, cambia pujas ni decide por el dueno.

Alcance base propuesto: publicaciones Amazon MX/US ya vinculadas al catalogo de
Orbit y creacion de las cinco campanas existentes. No confundir con todo el
catalogo de Amazon: listings sin mapa a producto pueden faltar en la ingesta.
Inventariar esas omisiones y mostrarlas como problemas de vinculacion, sin
asignarles un producto automaticamente. Anadidos a campanas existentes quedan
como ampliacion pendiente de respuesta, no incluidos silenciosamente.

### Seleccion por publicacion

- Un checkbox por listing_id, agrupado bajo producto; foto, ASIN, seller SKU,
  mercado y moneda visibles. Se pueden elegir varios ASIN del mismo producto.
- No elegir la primera publicacion ni todas automaticamente. Evitar anuncios
  duplicados por seller SKU dentro del mismo mercado y grupo.
- Sin ventas, con 1–29 fechas de venta o con mal rendimiento: seleccionables.
- Identidad inexistente, mercado equivocado, seller SKU ausente o perfil no
  aceptado: problema tecnico concreto que debe resolverse antes del POST.
- Existencia de anuncio, inventario y capacidad de servir son datos distintos.
  Stock/Featured Offer desconocidos se muestran Sin verificar. Un cero observado
  no equivale a dato ausente. No prometer que Amazon servira todos los anuncios.
- Seleccion y objetivo/bids/presupuestos/modo se revalidan en el servidor antes
  de escribir; cambios materiales invalidan el preview. El ranking no modifica
  en segundo plano una seleccion ya hecha.

### Objetivo de campana para lanzamientos

Propuesta pendiente de confirmacion: permitir un objetivo ACoS manual explicito
para cualquier grupo, obligatorio si falta margen fiable en alguno de sus
productos. Es intencion del dueno, nunca una medicion de margen.

- Mantener margen=None donde no se puede medir; guardar objetivo, procedencia,
  fecha y contexto de confirmacion. No inventar identidad de usuario: el sistema
  usa token compartido, no cuentas personales.
- Grupo mixto: no obtener el minimo omitiendo los productos sin dato. Ofrecer
  objetivo manual del grupo y mostrar los margenes conocidos como informacion.
- Derivacion actual desde margen solo si todos tienen margen fiable. No presentar
  el piso del clamp de 10% como rentabilidad: si el margen no es positivo o el
  objetivo aplicado supera el margen conocido, pedir objetivo manual y explicar
  la comparacion. No modificar los objetivos ya congelados.
- Objetivo manual decimal positivo, con precision/rango compatibles con goals;
  validacion comun en API, CLI, preview y registro. Sin porcentaje predeterminado.
- Presupuestos diarios y bids siguen siendo entradas explicitas por moneda.
  Lanzamiento NO significa gasto cero ni tope total. Shadow sigue creando
  campanas activas y solo observa los ajustes del optimizador.
- Reusar los goals existentes con el objetivo confirmado. No introducir nuevas
  reglas de bids, cortes ni harvest. Revisar y mostrar las campanas sin semillas;
  los terminos de campanas compartidas no son evidencia individual del producto.

### Evaluacion interpretable

Cada publicacion muestra tres bloques con fuente, grano, ventana y actualizacion:

| Bloque | Metricas | Regla de interpretacion |
|---|---|---|
| Economia observada | Margen antes de Ads, ventas totales, cobertura de costos, fechas con venta | Producto + plataforma; compartido entre sus ASIN. No representa margen de vender hoy. |
| Rendimiento Ads | Gasto, Revenue Ads, compras, clicks, CPC, CVR y ACoS | Reporte de producto anunciado; no repartir datos de campana/ad group entre ASIN. |
| Evidencia y disponibilidad | Dias observados, muestra, madurez, frescura; stock/estado cuando se verifiquen | Desconocido no es cero, pocas observaciones no equivalen a mal producto. |

La economia observada puede mostrar muestras menores de 30 fechas con venta si
pasan las guardas de integridad (moneda, cobertura de costo, cargos clasificados).
Debe llamarse muestra limitada. La vista madura actual conserva su semantica:
no borrar el guard global para desbloquear anuncios ni duplicar formulas. Una
fuente de calculo comun debe distinguir medicion disponible de evidencia madura.

MVP sin nota opaca 0–100 ni pesos inventados:

- Por probar: sin actividad Ads observada en la ventana completa verificada.
  No implica que el articulo sea nuevo o nunca haya tenido anuncios.
- Sin datos / datos incompletos: reporte faltante o sin cobertura; no afirmar cero.
- Dentro del objetivo / por encima del objetivo: comparar ACoS con un objetivo
  explicito identificado; sin objetivo solo mostrar metricas, no calificacion. No promediar los
  objetivos de varias campanas para fabricar uno por ASIN.
- Gasto sin ventas: gasto observado con ventas cero, ACoS indefinido, no cero.
- Margen observado positivo/negativo/no calculable como eje separado.
- Muestra y antiguedad siempre junto al resultado; no afirmar probabilidad de
  exito ni rentabilidad neta a partir de una venta o del ACoS aislado.

Ordenes explicitos por margen observado, ventas, rendimiento Ads y cantidad de
evidencia. Seccion Por probar visible y seleccionable; ausencia de dato no se
convierte en una mala nota. Propuesta inicial: priorizar rentabilidad y conservar
espacio para exploracion, pendiente de preferencia del dueno.

Reglas de datos: ratios desde sumas compatibles, no promedios de porcentajes;
monedas separadas; no sumar grano producto repetido por cada ASIN. Revenue Ads
atribuido puede incluir halo: identificar total/promovido cuando la fuente lo
permita. No restarlo a ventas contables para inventar ventas organicas. Ventana
publicitaria y madurez segun contrato exacto del reporte, sin asumir que 10 dias
cierran una atribucion de 30. Evidencia inmadura visible como tal, sin inferir
rendimiento definitivo. Estas etiquetas no disparan acciones automaticas.

### Fuentes nuevas necesarias

1. Reporte Amazon Ads de producto anunciado: contrato exacto, permisos, campos,
   clave, atribucion, cobertura y reconciliacion por verificar antes de construir.
   Reusar el cliente y scheduler de reporting, append-only con observed_at.
2. Disponibilidad comercial: verificar fuentes existentes bridge/SP-API para
   stock vendible por marketplace y su fecha. No sumar FBA/FBM sin semantica
   comprobada. Elegibilidad/Featured Offer solo si su fuente y frescura se prueban;
   ausencia de permisos deja Sin verificar, no bloquea por inferencia.
3. Margen proyectado con comisiones/precio/costos actuales, reviews y nota global:
   ampliaciones opcionales; no prerequisitos para permitir lanzamientos.

### Persistencia y recuperacion

Migracion NUEVA: conservar 0018 sellada. Snapshot de grupo admite objetivo manual
sin fraccion/derivado ni margen ficticios. Relacion de grupo-publicacion debe
permitir varias publicaciones del mismo producto conservando pertenencia y SKU.

Formato del plan versionado: lector de v1 intacto para lotes existentes, v2 para
seleccion por listing y procedencia de objetivo. Huella determinista incluye todo
lo autorizado para crear, independiente del orden visual. La clasificacion asesora
no entra en la huella salvo que cambie un parametro material autorizado.

Probar crear/registrar/reconciliar/pausar y errores parciales, tanto v1 como v2.
Sin doble POST ante doble click, cambio de orden o reintento. No recalcular targets
historicos. Reversa antes de nuevas escrituras. Rollback no puede abandonar lotes
v2 ni restaurar un lector v1 incompatible con los datos nuevos.

## Spec delta propuesto y decisiones pendientes

Enmendar expresamente FABRICA 01 (exclusion por margen y multilisting, decisiones
3/14 y residual de target manual) y FABRICA UI 01. Este borrador no cambia aun el
contrato vigente de produccion ni los invariantes de dinero/maduracion del motor.
No hay spec.md raiz en esta base; se usan los specs existentes del proyecto.

Pendientes consultados al dueno: objetivo prioritario; target manual de prueba
frente a margen proyectado previo; nuevas campanas solamente o tambien existentes.
Hasta resolverlos, las alternativas figuran como propuestas, no como aprobaciones.
La solicitud de formalizar el plan no cuenta como respuesta a esas decisiones.

## Contratos verificables de la formalizacion

### API, CLI y snapshots v2 (propuesta tecnica)

- API nueva: `listing_ids: list[int]`, sin duplicados; objetivo discriminado como
  `objetivo: {origen: "margen_medido"}` o
  `objetivo: {origen: "manual_lanzamiento", acos_pct: "25.00"}`. El porcentaje
  del ejemplo es un fixture, nunca default. Rechazar entradas que mezclen
  `productos` con `listing_ids` o margen derivado con un objetivo manual.
- Compatibilidad: aceptar el contrato antiguo `productos` y CLI `--productos`
  con su semantica anterior, sin convertir silenciosamente productos ambiguos.
  CLI nuevo propone `--listing-ids` y `--target-acos` explicito. Un unico
  normalizador produce el plan canonico, no dos motores de creacion.
- Persistido v2: `schema_version=2`, publicaciones resueltas con listing_id,
  product_id, ASIN, seller SKU y plataforma; snapshot del objetivo y evidencia
  financiera realmente usada. Campo medido ausente se serializa como null.
- Persistido sin version: lector v1 conserva exactamente su comportamiento y
  huella. Las nuevas solicitudes v2 ordenan por listing_id antes del hash;
  cambiar objetivo, publicacion o gasto cambia la huella. No rehashear lotes v1.
- Migracion expansiva y lector v1/v2 antes de habilitar nuevas creaciones. La
  version de reversa debe deshabilitar creaciones v2 y conservar lectura,
  registro, reconciliacion y pausa de v2; no volver al binario viejo incompatible.
  El mecanismo concreto de deshabilitacion y su prueba se cierran en 0.2.

### Comparacion y evidencia

Un objetivo de comparacion puede ser el del grupo que el dueno esta preparando,
identificado como tal. Antes de capturarlo, ACoS se muestra sin etiqueta de
cumplimiento. No se deriva un target comun mezclando los objetivos de campanas.
La comparacion describe resultados observados, no una probabilidad de exito.

Orden de evaluacion, con cobertura/madurez como ejes separados:

1. Reporte faltante o cobertura no verificada: datos incompletos; sin etiqueta
   dentro/fuera. Los subtotales disponibles se identifican como parciales.
2. Cobertura completa y sin actividad observada: Por probar en esta ventana.
3. Gasto positivo y Revenue Ads cero observado: Gasto sin ventas; ACoS=null.
4. Revenue Ads positivo: ACoS=100*suma(gasto)/suma(Revenue Ads). Con objetivo
   explicito, igualdad cuenta Dentro del objetivo y mayor cuenta Por encima.
   Sin objetivo, solo ACoS. Evidencia inmadura se etiqueta provisional.
5. Combinaciones restantes conservan sus datos, sin forzar una etiqueta. Una
   fila inexistente solo permite inferir cero si el contrato del reporte y su
   cobertura lo demuestran; de otro modo es desconocido.

CPC=suma(gasto)/suma(clicks), CVR=100*suma(compras)/suma(clicks), con denominador
positivo y cobertura compatible; en otro caso null. Los campos de compras y
ventas, su componente promovido/halo y la ventana exacta de atribucion quedan
sellados en 0.4 tras la muestra de fuente0.3. No construir B con ese contrato
sin resolver ni combinar columnas de atribucion incompatible.

Ordenes soportados: margen observado, ventas totales, Revenue Ads, gasto,
ACoS, CPC, CVR y compras; ascendente/descendente visible. Solo filas comparables
por mercado, moneda, grano y ventana. Null al final en ambas direcciones,
desempate estable por listing_id. No usar el orden como nota global de calidad.
Orden inicial pendiente de preferencia; fallback tecnico neutral por SKU/ASIN.

Evidencia siempre separa: (a) madurez de atribucion, (b) tamano de muestra y
cobertura, (c) frescura de ingesta. No calificar confianza alta/media/baja sin
una politica adicional; no reutilizar 30 fechas financieras como umbral Ads.
Disponibilidad Sin verificar no impide comparar economia/Ads ni seleccionar.

Fixtures de aceptacion con cifras ilustrativas, nunca valores sembrados:

| Caso | Entrada | Resultado esperado |
|---|---|---|
| Ratios desde sumas | Gasto10/ventas100 y gasto90/ventas300, misma moneda/ventana | ACoS25%, no promedio20%; muestras y cobertura visibles |
| Igualdad | ACoS25%, objetivo explicito25% | Dentro del objetivo |
| Distintos objetivos de campana | Una publicacion aparece en dos campanas con targets distintos; sin objetivo de comparacion | ACoS sin etiqueta dentro/fuera; no target promedio |
| Cero y ausencia | Gasto10, ventas0 observadas vs reporte ausente | Primero Gasto sin ventas/ACoSnull; segundo Sin datos |
| Muestra | Dos ASIN con ACoS25%, compras1 y100 | Mismo resultado frente al target; conteos diferentes visibles |
| Mismo producto | Dos listings comparten venta financiera100 y margen20% del producto | Se muestra el grano compartido; total financiero100, no200 |
| Historia fuera de ventana | Reporte completo sin actividad actual, actividad antigua conocida | Por probar en esta ventana; no llamarlo producto nuevo |
| Orden estable | Igual metrica o null | Desempate listing_id; null al final; seleccion preservada |

### Resolucion de fuentes

0.3 puede cerrar la investigacion con estado verificada o no_verificada y motivo
por fuente. La implementacion Ads exige fuente verificada: si no hay acceso,
B.1 y la entrega B siguen pendientes. Mostrar null no completa el reporte Ads.
Disponibilidad es recomendada: su ausencia admite el estado Sin verificar, sin
bloquear B.4/B.5. La entrega completa debe declarar si integra disponibilidad
real o si esa ampliacion queda pendiente. Margen con muestra limitada es
propuesta de negocio en 0.2, no relajacion tacita del calculo del motor.

## Fuentes y evidencia

- Base origin/master aa92611; inspeccion de app/fabrica_plan.py, fabrica_web.py,
  api_fabrica.py, tools/fabrica_campanas.py, migracion0018, app/ads/reports.py,
  docs/MARGEN-ENTIDAD.md y specs de fabrica 2026-09-05/06.
- Consulta productiva 2026-09-06: MX 249 productos, 3 elegibles; 84 sin ventas
  vinculadas, 91 con menos de30 fechas, 71 multilisting. US119, 2 elegibles;
  48 sin ventas vinculadas,25 con menos de30 fechas,44 multilisting. Causas
  excluyentes segun el orden del selector; los multilisting pueden ademas carecer
  de margen. Ventana financiera [2026-02-20,2026-08-22).
- [Amazon: guia de Sponsored Products](https://advertising.amazon.com/library/guides/new-advertiser-success-guide): stock y Featured Offer afectan la publicacion de anuncios.
- [Amazon: reporte de producto anunciado](https://advertising.amazon.com/help/GYNVHW8R4QPYUS9H): grano y ventas promovidas/halo.
- [Amazon: atribucion](https://advertising.amazon.com/help/G22MA5YPN9KKT7TM): datos incompletos durante la ventana y fecha de interaccion.
