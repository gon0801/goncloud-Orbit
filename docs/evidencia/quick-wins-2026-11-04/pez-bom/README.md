# PEZ: si hay BOM activos; el problema es la referencia que integra Orbit

La conclusion anterior fue demasiado fuerte: un producto archivado o con standard_price=0 no demuestra que falten componentes para costearlo. Las recetas se pueden leer y calcular. Ademas, Odoo SI tiene productos PEZ con BOM activos bajo referencias NH-SOLO-* y NH-ARR-*; no debe confundirse eso con los nueve SKU antiguos que todavia devuelve MeLi.

**Ejemplo concreto:** NH-SOLO-CAR-BLC-PEZ-DOR, producto 8513, tiene el BOM 2133 activo y atributo «Sello Cristiano». Contiene una unidad de EST-CAR-BLC (39.65 MXN) y una de ARR-22-DOR-PEZ (37.82 MXN): **77.47 MXN por unidad**. El SKU de MeLi NH-CAR-BLA-PEZ-DOR apunta a otra referencia: su variante Odoo 6442 tiene BOM archivados 1042/1326 con estuche 39.65 + cofre 155.00 = 194.65 MXN. La receta activa no agrega algo a 194.65: conserva el estuche, sustituye el cofre por las arras PEZ y usa otra referencia y otra composicion registrada. Esa diferencia permite investigar un mapeo desactualizado; no prueba por si sola que ambos SKU sean intercambiables.

## BOM activos PEZ relacionados por familia

Los ocho candidatos siguientes tienen producto y BOM activos y atributo «Sello Cristiano» corroborado en product.template.attribute.value. Las cantidades son 1 por componente y unidad final. La columna de costo es la suma reproducible de materiales actuales del BOM activo, sin copiar el costo final del candidato. Todos los componentes estan en Units; su costo esta en MXN.

| SKU MeLi | Producto activo / SKU Odoo | BOM activo | Componentes por unidad (MXN) | Costo material MXN |
|---|---|---|---|---|
| NH-BLA-BRO-PEZ-DOR | Sin candidato NH-SOLO para esta familia en la busqueda | — | — | — |
| NH-CAR-BLA-PEZ-DOR | 8513 / NH-SOLO-CAR-BLC-PEZ-DOR | 2133 | EST-CAR-BLC 39.65 + ARR-22-DOR-PEZ 37.82 | 77.47 |
| NH-CAR-BLA-PEZ-PLA | 8520 / NH-SOLO-CAR-BLC-PEZ-PLA | 2140 | EST-CAR-BLC 39.65 + ARR-22-PLA-PEZ 37.82 | 77.47 |
| NH-GAM-AZU-PEZ-DOR | 8681 / NH-SOLO-GAM-AZU-PEZ-DOR | 2203 | EST-GAM-AZU 60.0 + ARR-22-DOR-PEZ 37.82 | 97.82 |
| NH-GAM-AZU-PEZ-PLA | 8688 / NH-SOLO-GAM-AZU-PEZ-PLA | 2210 | EST-GAM-AZU 60.0 + ARR-22-PLA-PEZ 37.82 | 97.82 |
| NH-GAM-NEG-PEZ-DOR | 8709 / NH-SOLO-GAM-NEG-PEZ-DOR | 2217 | EST-GAM-NEG 60.0 + ARR-22-DOR-PEZ 37.82 | 97.82 |
| NH-GAM-NEG-PEZ-PLA | 8716 / NH-SOLO-GAM-NEG-PEZ-PLA | 2224 | EST-GAM-NEG 60.0 + ARR-22-PLA-PEZ 37.82 | 97.82 |
| NH-ITA-PEZ-PLA | 8576 / NH-SOLO-ITA-PEZ-PLA | 2238 | 4405-BG 76.0 + ARR-22-PLA-PEZ 37.82 | 113.82 |
| NH-PLA-CH-PEZ-PLA | 8744 / NH-SOLO-PLA-CH-PEZ-PLA | 2683 | 4587-BK 107.0 + ARR-22-PLA-PEZ 37.82 | 144.82 |

Para NH-BLA-BRO-PEZ-DOR no se encontro un NH-SOLO PEZ correspondiente entre las referencias NH/PEZ activas examinadas. Eso no equivale a demostrar que el producto no se vende o que no pueda existir otra referencia. Los BOM activos de la familia que comparten base incluyen componentes/modelos distintos.

## Desglose de las nueve referencias exactas de MeLi

La siguiente tabla calcula una receta archivada especifica por SKU: la mas reciente por create_date de la variante que tiene el mismo SKU y atributo de modelo corroborado en MeLi. Es una regla explicita para presentar las alternativas; no significa que Odoo la seleccione hoy o que su costo sea historicamente vigente. Los BOM repetidos de ocho variantes dan el mismo desglose. No se usa costo de otra familia.

| SKU exacto | Variante / BOM de referencia | Componentes por unidad (MXN) | Costo material MXN |
|---|---|---|---|
| NH-BLA-BRO-PEZ-DOR | 5900 / 1284 | 1 x 4608 88.69 + 1 x COF-19-DOR 165.0 | 253.69 |
| NH-CAR-BLA-PEZ-DOR | 6442 / 1326 | 1 x EST-CAR-BLC 39.65 + 1 x COF-16-DOR 155.0 | 194.65 |
| NH-CAR-BLA-PEZ-PLA | 6452 / 1327 | 1 x EST-CAR-BLC 39.65 + 1 x COF-16-PLA 155.0 | 194.65 |
| NH-GAM-AZU-PEZ-DOR | 6642 / 1404 | 1 x EST-GAM-AZU 60.0 + 1 x COF-19-DOR 165.0 | 225.0 |
| NH-GAM-AZU-PEZ-PLA | 6652 / 1405 | 1 x EST-GAM-AZU 60.0 + 1 x COF-19-PLA 165.0 | 225.0 |
| NH-GAM-NEG-PEZ-DOR | 6662 / 1418 | 1 x EST-GAM-NEG 60.0 + 1 x COF-19-DOR 165.0 | 225.0 |
| NH-GAM-NEG-PEZ-PLA | 6672 / 1419 | 1 x EST-GAM-NEG 60.0 + 1 x COF-19-PLA 165.0 | 225.0 |
| NH-ITA-PEZ-PLA | 6692 / 1433 | 1 x 4405-BG 76.0 + 1 x COF-16-PLA 155.0 | 231.0 |
| NH-PLA-CH-PEZ-PLA | 6732 / 1461 | 1 x 4587-BK 107.0 + 1 x COF-19-PLA 165.0 | 272.0 |

La excepcion historica es Blanco con broche: la misma variante 5900 tambien tiene BOM 328 (solo componente 4608, 88.69 MXN), BOM 994/1284 (4608 + COF-19-DOR, 253.69 MXN) y el generico 985 sin lineas. El BOM vacio queda con costo null, no cero. Es una diferencia concreta entre recetas, no falta de costos de los componentes. Los dos BOM mas recientes coinciden; reactivar todo sin distinguirlos seria incorrecto.

## Unidades, rendimiento, variantes y sub-BOM

- Se leyeron los 27 productos exactos, sus 29 BOM exactos/genericos, 46 lineas y los 10 componentes. Los 29 BOM producen 1 Units por lote. Todas las cantidades observadas son 1 Units; factor y factor_inv son 1. No hay redondeos ni conversiones monetarias en estos calculos.
- Ninguna de las 46 lineas de esos BOM tiene restricciones bom_product_template_attribute_value_ids. Tampoco las 1678 lineas de los 574 BOM activos examinados tienen restricciones. Las comprobaciones se hacen contra los atributos del producto, no contra nombres.
- Se consulto cada plantilla/producto de los 10 componentes con active_test=False: no tienen sub-BOM, ni activos ni archivados. Los componentes nuevos ARR-22-DOR-PEZ (6) y ARR-22-PLA-PEZ (156) se comprobaron aparte: tampoco tienen sub-BOM. Por tanto sus standard_price actuales son el costo hoja observado; no hay una explosion anidada que falte aplicar.
- Las recetas exactas no tienen operaciones ni subproductos. Los candidatos NH-SOLO tampoco agregan operaciones/subproductos a la cuenta. El rendimiento es product_qty=1 de la receta; no se agrega una merma inventada.
- Formula: cantidad_linea convertida a la unidad del componente × costo vigente del componente / salida_BOM convertida a la unidad del producto final. La conversion de cantidades usa cantidad / factor_origen × factor_destino, cotejada con el codigo instalado de Odoo. Todo el calculo local usa Decimal y conserva moneda.

## Que esta archivado y por que

Los 29 BOM asociados a los tres registros exactos por SKU estan archivados; por esos mismos IDs, la consulta con contexto por defecto devuelve cero filas y con active_test=False devuelve 29. Eso NO significa que todos los BOM PEZ de Odoo esten archivados: la tabla de candidatos demuestra BOM PEZ activos con otras referencias.

No se encontro un BOM activo con composicion directa exactamente igual a las nueve recetas historicas elegidas. La comparacion cubrio 574 BOM activos que contienen cualquier componente historico o pertenecen a los 28 productos NH/PEZ activos descubiertos; reviso componentes, cantidades y unidades. La falta de igualdad no invalida los BOM vigentes: pueden representar una composicion corregida o distinta. En Carte, por ejemplo, cambia cofre por arras; los actuales Centenario de Carte/Italia usan ademas cofre de 19 mm donde la receta PEZ antigua usa 16 mm.

**No sabemos por que ni quien archivo los BOM antiguos.** Los 22 mensajes de chatter de las nueve variantes con atributo, sus plantillas y los BOM examinados son mensajes de creacion; no tienen tracking values. El campo active tiene tracking=0 en product.product, product.template y mrp.bom. write_date no identifica un evento de archivo ni su motivo. El autor Administrator de los mensajes de creacion no se atribuye como autor del archivo.

## Problema exacto de integracion y siguiente paso concreto

El costo por componentes existe y es calculable. El pipeline actual no lo deriva al consumir los SKU de MeLi: accounting sincroniza costos finales de productos activos con standard_price>0; su sincronizacion de BOM omite archivados por el contexto por defecto. Orbit consume accounting.sku_costs; bom_headers solo aporta nombres. Por eso los nueve SKU antiguos no llegan como productos con costo y listing_miembro.product_id sigue null. Los hashes/fragmentos del camino real estan en sincronizacion.json.

El trabajo pendiente es reconciliar la referencia comercial actual de cada variante MeLi con su producto/receta vigente de Odoo y conectar ese costo por el camino autorizado. Ya hay ocho candidatos activos concretos y recetas exactas calculadas; no corresponde pedir nuevamente si PEZ se vende ni descartar variantes por stock cero. Elegir una receta/refactorizar el mapeo o publicar un costo son mutaciones separadas: esta evidencia no las ejecuta y no les inventa fecha efectiva.

## Evidencia y reproduccion

- capturar.py -> odoo.json: productos/BOM exactos, cantidades/unidades, restricciones, costos hoja y ausencia de sub-BOM.
- calcular.py -> desglose.json: calculo local de cada receta por separado, incluidos registros historicos y la variante moderna.
- activos_auditoria.py -> activos-auditoria.json: 574 BOM activos y su composicion; auditoria de los registros exactos y configuracion del tracking de active.
- comparar.py -> comparacion-activos.json: comparacion material por ID/cantidad/unidad, con componentes adicionales explicitos.
- candidatos.py -> candidatos-activos.json: tabla de candidatos NH-SOLO y diferencias de componentes frente a cada receta historica.
- atributos_activos.py -> atributos-activos.json: atributos Sello Cristiano y ausencia de sub-BOM de las dos hojas nuevas de arras PEZ.
- reglas-odoo.json: hash y ubicacion del codigo instalado de conversion de unidades/aplicabilidad. sincronizacion.json: evidencia de los filtros de ingesta, leida por el agente principal.

Scripts remotos: ssh goncloud python3 - < SCRIPT, salvo los GET MeLi ya capturados en ../pez-fuente/meli-identidad.json. Scripts de calculo/comparacion son locales. Credenciales solo en memoria; no refresh ni escrituras productivas. Las cantidades y costos son los observados a las horas UTC de cada JSON, no un snapshot atomico entre llamadas ni una reconstruccion historica del costo.
