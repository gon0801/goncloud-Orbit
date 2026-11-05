# PEZ: fuente primaria y ruta para completar costos

**Estado posterior:** [PEZ 01](../pez-correccion/identidad/README.md) acredita nueve SET activos con cofre como correspondencias comerciales, incluido Blanco con Broche. Los NH-SOLO citados abajo se descartan para las publicaciones de este lote; esta investigacion inicial se conserva como historia.

**Rectificacion 5-nov-2026:** [la lectura de BOM/componentes y referencias activas](../pez-bom/README.md) sustituye la conclusion inicial de receta/costo fuente faltante. Si hay BOM activos PEZ bajo otros codigos. Por ejemplo, NH-SOLO-CAR-BLC-PEZ-DOR (producto 8513, BOM 2133) suma 77.47 MXN; la receta archivada del codigo NH-CAR-BLA-PEZ-DOR suma 194.65 MXN y contiene otro componente. No son el mismo desglose. El problema pendiente es resolver la correspondencia entre SKU comercial y referencia/BOM vigente, y despues la sincronizacion. El chatter/tracking consultado no acredita el motivo del archivo.

**Decision del usuario:** «pez si se vende». PEZ se conserva como producto comercial vigente; no se retiran publicaciones ni variantes por obsoletas o por stock cero. Esta decision corrige la alternativa planteada en el informe anterior; no demuestra un importe ni selecciona un ID de Odoo.

Lectura Odoo: 2026-11-05T01:46:52.949734+00:00. Las fechas de captura estan en UTC; la carpeta conserva el dia local de inicio (4-nov).

## Resultado

MeLi confirma por SELLER_SKU y variante_ext los nueve SKU exactos, con modelo «Sello cristiano». Odoo tiene tres registros por SKU: una variante con atributo «Sello Cristiano» y dos productos historicos. Los 27 registros estan archivados y su standard_price es cero MXN. Ese cero observado no demuestra que falten recetas o costos de componentes. La lectura posterior calcula materiales de los BOM y encuentra referencias PEZ con BOM activos. Se conserva el cero de esta captura separado de esos calculos.

La busqueda incluyo productos archivados (active_test=false), aliases NH/PEZ paginados, las 27 plantillas, todos sus 219 BOM y las lineas/componentes de los 29 BOM aplicables a PEZ o genericos. Los 29 estan archivados. Ninguna de las 64 variantes activas de esas plantillas contiene el atributo PEZ. No se encontro venta confirmada (sale/done) para esos 27 IDs; esto describe esa fuente y no contradice que PEZ se vende por otros caminos.

Odoo SI contiene PEZ activos: la busqueda encontro 28 referencias NH-SOLO-* / NH-ARR-* activas. Ocho familias tienen candidatos «Solo Arras» con BOM activos, costos positivos y composicion distinta de las recetas archivadas. La [comparacion por componente](../pez-bom/README.md) acredita que la receta activa existe. Esa diferencia no prueba por si sola que una referencia sea sustituta comercial de otra: resta verificar el producto que corresponde a cada variante MeLi.

## Matriz de identidad

| Publicacion / variante | SKU exacto | IDs Odoo (todos archivados, costo 0 MXN) | Variante con atributo / plantilla |
|---|---|---|---|
| MLM2163407464 / 194936687129 | NH-CAR-BLA-PEZ-DOR | 1608, 5307, 6442 | 6442 / 6186 |
| MLM1890653227 / 194936530433 | NH-CAR-BLA-PEZ-PLA | 1609, 5308, 6452 | 6452 / 6187 |
| MLM4735255462 / 190065698608 | NH-BLA-BRO-PEZ-DOR | 1572, 5247, 5900 | 5900 / 5911 |
| MLM2724304859 / 190063357552 | NH-ITA-PEZ-PLA | 1705, 5468, 6692 | 6692 / 6211 |
| MLM2727437671 / 196650442795 | NH-GAM-AZU-PEZ-PLA | 1681, 5428, 6652 | 6652 / 6207 |
| MLM2727363007 / 196650403797 | NH-GAM-NEG-PEZ-DOR | 1692, 5447, 6662 | 6662 / 6208 |
| MLM2727328203 / 196650403671 | NH-GAM-AZU-PEZ-DOR | 1680, 5427, 6642 | 6642 / 6206 |
| MLM4749600210 / 196764932943 | NH-GAM-NEG-PEZ-PLA | 1693, 5448, 6672 | 6672 / 6209 |
| MLM4763716652 / 190333223922 | NH-PLA-CH-PEZ-PLA | 1729, 5508, 6732 | 6732 / 6215 |

La consulta por referencia exacta no basta para seleccionar el producto vigente. La investigacion posterior aporta ocho candidatos con BOM activos; Blanco con Broche no tiene candidato NH-SOLO identificado. No se necesita pedir al dueño una receta por el mero hecho de que el costo final observado sea cero, ni reactivar automaticamente las versiones archivadas.

## Ruta existente para completar

1. Usar la tabla de referencias y BOM activos de la lectura posterior para comprobar la correspondencia con lo que se entrega en cada variante MeLi. Las recetas existen; falta resolver esa relacion, incluyendo Blanco con Broche.
2. Publicar el mapeo y costo de la referencia/BOM que corresponda, con su fecha efectiva y reversa antes de cualquier escritura. No elegir una receta por ser la mas reciente ni reactivar todas las archivadas. Esta investigacion es de solo lectura.
3. El camino ya existente de accounting/scripts/sync_cogs_odoo.py consulta product.product con referencia, active=true y standard_price>0. Por eso omite los 27 registros exactos. sync_bom_odoo.py y los snapshots posteriores constituyen el camino existente hacia accounting y Orbit; no se ejecutaron. Al resolver la fuente, comprobar costo y moneda en accounting, ingesta global OK sin skips y product_id/costo de cada miembro en Orbit antes de recalcular escenarios.
4. Una eventual mutacion requiere lote con ID anterior/nuevo, costo, moneda, fecha y respaldo/reversa. operaciones_preparadas=[] describe el alcance de la captura inicial, no ausencia de recetas o imposibilidad de costear. No se preparo ni ejecuto una escritura productiva.

## Evidencia y limites

- `meli_identidad.py` / `meli-identidad.json`: nueve GET actuales con atributos de variante; confirma referencia y modelo, sin refresh de tokens.
- `odoo_detalle.py` / `odoo-detalle.json`: XML-RPC existente de accounting; authenticate y allowlist fields_get/search_read/read. Configuracion solo en memoria, nunca en salida ni argv. Busqueda de alias y BOM paginada hasta pagina incompleta; sin truncar esos dominios.
- `resumir.py` / `matriz.json`: matriz historica reproducible sin red. Sus campos de bloqueo y costo utilizable=null pertenecen a la conclusion inicial, sustituida por `../pez-bom/`; no evalua el costo desde BOM. Las cifras de candidatos mantienen la representacion de la fuente, incluido ruido binario.
- La evidencia previa `../costos-meli/fuentes.json` ya demuestra la ausencia de los nueve SKU exactos en accounting.sku_costs/bom_headers y bridge.meli_sku_mapping. `../costos-meli/orbit.json` muestra product_id NULL para los miembros PEZ. Se reutiliza y no se sobrescribe.
- La captura inicial no publica costo/vigencia en Orbit. La lectura posterior separa costo material calculado, costo final publicado en Odoo y referencia comercial. write_date no acredita fecha efectiva del costo ni fecha/motivo de archivo.
- No se modificaron Odoo, accounting, bridge, Orbit, publicaciones, metas ni planes. No se equipara similitud de nombre con equivalencia de producto. Las lecturas de distintas fuentes no son un snapshot atomico.

## Reproduccion

```bash
ssh goncloud python3 - < docs/evidencia/quick-wins-2026-11-04/pez-fuente/odoo_detalle.py
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/pez-fuente/meli_identidad.py
python3 docs/evidencia/quick-wins-2026-11-04/pez-fuente/resumir.py
```
