# PEZ: fuente primaria y ruta para completar costos

**Decision del usuario:** «pez si se vende». PEZ se conserva como producto comercial vigente; no se retiran publicaciones ni variantes por obsoletas o por stock cero. Esta decision corrige la alternativa planteada en el informe anterior; no demuestra un importe ni selecciona un ID de Odoo.

Lectura Odoo: 2026-11-05T01:46:52.949734+00:00. Las fechas de captura estan en UTC; la carpeta conserva el dia local de inicio (4-nov).

## Resultado

MeLi confirma por SELLER_SKU y variante_ext los nueve SKU exactos, con modelo «Sello cristiano». Odoo tiene tres registros por SKU: una variante con atributo «Sello Cristiano» y dos productos historicos. Los 27 registros estan archivados y su standard_price es cero MXN. El standard_price observado es 0 MXN; el costo utilizable permanece None porque falta respaldo vigente para estos SKU exactos. Se conserva el cero observado en la evidencia.

La busqueda incluyo productos archivados (active_test=false), aliases NH/PEZ paginados, las 27 plantillas, todos sus 219 BOM y las lineas/componentes de los 29 BOM aplicables a PEZ o genericos. Los 29 estan archivados. Ninguna de las 64 variantes activas de esas plantillas contiene el atributo PEZ. No se encontro venta confirmada (sale/done) para esos 27 IDs; esto describe esa fuente y no contradice que PEZ se vende por otros caminos.

Odoo SI contiene PEZ activos: la busqueda encontro 28 referencias NH-SOLO-* / NH-ARR-* activas. Ocho de las familias tienen candidatos «Solo Arras» con costo positivo; su referencia y composicion difieren. No hay mapping probado que autorice sustituir NH-* de MeLi por NH-SOLO-*. Sus importes se conservan solo como datos de candidatos, nunca como costo del SKU investigado.

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

Cada fila queda bloqueada por el mismo dato puntual: el ID Odoo vigente que representa lo que se entrega con esa variante, o la confirmacion de que la variante identificada debe reactivarse con una receta completa vigente, un costo positivo y una fecha efectiva. Confirmar que se vende ya esta resuelto; no se vuelve a pedir esa confirmacion.

## Ruta existente para completar

1. El responsable de producto/Odoo debe resolver la discrepancia entre los SKU comerciales vigentes de MeLi y sus productos archivados/sin costo en Odoo. La matriz incluye los IDs exactos y candidatos para que la decision sea puntual. No hace falta inventar un SKU nuevo ni buscar por nombre otra vez.
2. Si corresponde un alias de producto activo, debe quedar respaldado el mapeo exacto y la composicion. Si corresponde el SKU exacto archivado, falta su receta/costo vigente. No se ejecuta ninguna de esas opciones desde esta investigacion.
3. El camino ya existente de accounting/scripts/sync_cogs_odoo.py consulta product.product con referencia, active=true y standard_price>0. Por eso omite los 27 registros exactos. sync_bom_odoo.py y los snapshots posteriores constituyen el camino existente hacia accounting y Orbit; no se ejecutaron. Al resolver la fuente, comprobar costo y moneda en accounting, ingesta global OK sin skips y product_id/costo de cada miembro en Orbit antes de recalcular escenarios.
4. Una eventual mutacion requiere lote con ID anterior/nuevo, costo, moneda, fecha y respaldo/reversa. Hoy operaciones_preparadas=[]: con cero como unico costo exacto y tres IDs archivados por SKU no existe un lote financiero seguro. No se preparo un script que complete datos faltantes con suposiciones.

## Evidencia y limites

- `meli_identidad.py` / `meli-identidad.json`: nueve GET actuales con atributos de variante; confirma referencia y modelo, sin refresh de tokens.
- `odoo_detalle.py` / `odoo-detalle.json`: XML-RPC existente de accounting; authenticate y allowlist fields_get/search_read/read. Configuracion solo en memoria, nunca en salida ni argv. Busqueda de alias y BOM paginada hasta pagina incompleta; sin truncar esos dominios.
- `resumir.py` / `matriz.json`: matriz reproducible sin red, costos exactos no utilizables como null y candidatos separados. Las cifras de candidatos mantienen la representacion de la fuente, incluido ruido binario; no se redondean ni se adoptan.
- La evidencia previa `../costos-meli/fuentes.json` ya demuestra la ausencia de los nueve SKU exactos en accounting.sku_costs/bom_headers y bridge.meli_sku_mapping. `../costos-meli/orbit.json` muestra product_id NULL para los miembros PEZ. Se reutiliza y no se sobrescribe.
- Costo, moneda y vigencia utilizable de PEZ: costo=null, vigencia=null; la moneda MXN solo describe el standard_price cero observado. write_date de Odoo no es fecha efectiva de costo.
- No se modificaron Odoo, accounting, bridge, Orbit, publicaciones, metas ni planes. No se equipara similitud de nombre con equivalencia de producto. Las lecturas de distintas fuentes no son un snapshot atomico.

## Reproduccion

```bash
ssh goncloud python3 - < docs/evidencia/quick-wins-2026-11-04/pez-fuente/odoo_detalle.py
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/pez-fuente/meli_identidad.py
python3 docs/evidencia/quick-wins-2026-11-04/pez-fuente/resumir.py
```
