# Identidad comercial: nueve PEZ son SET con cofre

Las fotos especificas de las nueve variantes MeLi muestran estuche, cofre y arras; los productos SET activos de Odoo muestran esa presentacion y el mismo modelo Sello Cristiano, color y tipo de estuche. NH-SOLO no incluye cofre: los ocho candidatos anteriores no son la correspondencia respaldada por las fotos. Blanco con broche corresponde a SET-BLA-BRO-PEZ-DOR, producto7070 y BOM1788, ambos activos.

Se inspeccionaron las nueve parejas. En Carte, Blanco con broche, Gamuza y Platino coincide la presentacion fotografica. Italia muestra el mismo tipo de estuche/cofre/arras desde otro angulo. [comparacion.html](comparacion.html) muestra cada pareja original y un control NH-SOLO sin cofre; no se editaron las imagenes.

| Listing / variante MeLi | SKU MeLi | Producto / SKU SET Odoo | BOM activo | Costo final MXN | Material MXN |
|---|---|---|---|---|---|
| 1562 / 194936687129 | NH-CAR-BLA-PEZ-DOR | 7106 / SET-CAR-BLA-PEZ-DOR | 1824 | 126.2100 | 121.53 |
| 1563 / 194936530433 | NH-CAR-BLA-PEZ-PLA | 7112 / SET-CAR-BLA-PEZ-PLA | 1825 | 126.2100 | 122.01 |
| 1578 / 190065698608 | NH-BLA-BRO-PEZ-DOR | 7070 / SET-BLA-BRO-PEZ-DOR | 1788 | 175.2500 | 170.57 |
| 1579 / 190063357552 | NH-ITA-PEZ-PLA | 7208 / SET-ITA-PEZ-PLA | 1921 | 162.5600 | 158.36 |
| 1582 / 196650442795 | NH-GAM-AZU-PEZ-PLA | 7184 / SET-GAM-AZU-PEZ-PLA | 1897 | 146.5600 | 142.36 |
| 1583 / 196650403797 | NH-GAM-NEG-PEZ-DOR | 7190 / SET-GAM-NEG-PEZ-DOR | 1908 | 146.5600 | 141.88 |
| 1584 / 196650403671 | NH-GAM-AZU-PEZ-DOR | 7178 / SET-GAM-AZU-PEZ-DOR | 1896 | 146.5600 | 141.88 |
| 1585 / 196764932943 | NH-GAM-NEG-PEZ-PLA | 7196 / SET-GAM-NEG-PEZ-PLA | 1909 | 146.5600 | 142.36 |
| 1590 / 190333223922 | NH-PLA-CH-PEZ-PLA | 7232 / SET-PLA-CH-PEZ-PLA | 1945 | 193.5600 | 189.36 |

## Evidencia y limites de identidad

- MeLi GET de nueve items/descripciones: SELLER_SKU, variante_ext, modelo y picture_ids pertenecen a la variante especifica. fotos-meli.json conserva URL publica, picture_id y archivo.
- Odoo: nueve SET activos, un BOM activo por producto y atributo Sello Cristiano. Cada receta contiene el estuche correspondiente, cofre22mm del acabado y arras PEZ del acabado. Cantidades1 y unidades Units, moneda MXN; sin restricciones de aplicabilidad, operaciones, subproductos ni sub-BOM de componentes.
- Imagenes image_512 leidas por ID de producto, con hashes en fotos-odoo.json. No se usaron nombres parecidos como unica prueba. El modelo/color/estuche/cofre y las fotos sustentan la correspondencia; esto sigue siendo un cotejo comercial, no una medicion fisica.
- Las descripciones comunes MeLi conservan diametros16/19mm y algunas mencionan otro modelo/acabado. Las plantillas SET Odoo indican22mm. Esa discrepancia se conserva, sin modificar la publicacion ni declararla fisicamente resuelta. La variante especifica Sello cristiano y su foto con cofre distinguen SET de NH-SOLO.
- La consulta de sale.order.line sale/done para los nueve SET no devolvio filas. Las ventas no agregan una prueba de equivalencia; esa ausencia no significa que PEZ no se venda.

## Fuente monetaria y entrega operativa

Se conserva standard_price de cada SET como fuente del costo publicado, segun el contrato de app/costs.py. La suma material se presenta aparte: el final excede la suma en4.68MXN para dorados y4.20MXN para plateados. No se atribuye una causa ni se recalcula/escribe Odoo. Auditar la diferencia de valorizacion es un seguimiento separado; no sustituye la fuente en esta tarea.

matriz.json conserva el texto monetario crudo y su Decimal a cuatro decimales. El126.21000000000001 observado pasa a126.2100 por la precision y umbral de ruido binario existentes en app/costs.py::_costo_decimal; no se usa la suma material para reemplazarlo. Las fechas observadas/write_date no se convierten en vigencias inventadas para los SKU NH.

El lote para el agente principal contiene nueve correspondencias listing/variante/SKU origen → producto/SKU SET, BOM y costo final publicado. Este agente no cambio SELLER_SKU externo ni escribio Orbit, Odoo, accounting o bridge. El camino autorizado del host es mapping persistente en Orbit hacia el producto SET y sus vigencias ya publicadas, sin crear falsas vigencias para NH.

## Reproduccion y archivos

- meli.py → meli.json: GET actuales de atributos, descripciones y fotos; pase existente en memoria, sin refresh.
- odoo.py → odoo.json: busqueda por atributo Sello Cristiano y familia Blanco con broche, referencias activas y metadatos de imagen. Resuelve Blanco sin limitar la busqueda a NH-SOLO.
- sets.py → sets.json: captura enfocada de los nueve SET, BOM, lineas, costos hoja, atributos y ventas.
- odoo_imagenes.py: lectura image_512. Su stdout se captura en un temporal local para decodificar fotos; no se imprime el binario al contexto. fotos-odoo.json guarda los hashes.
- fotos-meli.json, fotos-odoo.json e imagenes/: originales leidos de ambas fuentes. Las fotos no contienen credenciales.
- resumir.py → matriz.json y comparacion.html: calculos Decimal y tabla visual locales, sin red.

Los scripts Odoo corren con `ssh goncloud python3 - < SCRIPT`; meli.py con `ssh goncloud 'docker exec -i orbit-app-1 python -' < SCRIPT`. No se exponen credenciales en argumentos o salidas. Las fuentes se observaron a las horas UTC de sus JSON y no constituyen una transaccion atomica entre sistemas.

## Conciliacion del tercer eslabon: accounting

accounting.py consulto los nueve SKU SET en una sola consulta de datos a SQLite (`mode=ro`, `query_only`, transaccion de lectura; sin snapshot ni ingesta). accounting.json conserva ID, costo original como texto, moneda, valid_from, valid_to, source y created_at de cada fila vigente.

Las nueve filas coinciden con standard_price observado en Odoo, usando la precision monetaria ya definida en Orbit. Todas estan en MXN, source=odoo_sync, valid_from=2026-08-18 07:00:06 y valid_to=null. matriz.json incluye la fila fuente de accounting por variante y el resultado del cotejo. Se reutiliza esa vigencia existente; no se crea ni se retrodata un costo para el SKU NH antiguo. La verificacion de Orbit queda en orbit-antes.json del lote principal.
