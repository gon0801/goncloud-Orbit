# ORBIT 19 — Catalogo abierto y comparacion para campanas

Estado: BORRADOR para decision del dueno, 2026-09-06. Solo planificacion.
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

- Por probar: sin historial Ads observado en una cobertura verificada.
- Sin datos / datos incompletos: reporte faltante o sin cobertura; no afirmar cero.
- Dentro del objetivo / por encima del objetivo: comparar ACoS con un objetivo
  explicito identificado; sin objetivo solo mostrar metricas, no calificacion.
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
