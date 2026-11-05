# P13: la diferencia proviene de valor publicado retenido frente a materiales actuales

La diferencia numerica de 4.68 MXN dorado y4.20 MXN plateado concilia exactamente con la bajada del costo del cofre del 24-ago. Los nueve SET conservan su costo publicado del 18-ago, compatible exactamente con cofre 48.74 MXN. Despues de una recepcion, el cofre pasa a 44.06/44.54 MXN bajo AVCO. Odoo no define standard_price del SET como un campo dependiente de su BOM; sumar materiales actuales calcula otra magnitud.

No se demostro un error de ingesta ni un monto arbitrario. No procede restar automaticamente la diferencia ni revalorizar los nueve SET para forzar igualdad. Se conserva la fuente publicada del contrato actual; el material actual se mantiene como conciliacion informativa.

## Cadena causal comprobada

1. ir.property registra los costos de los nueve SET el 18-ago entre 06:25:00 y06:25:10 UTC. Esa es la fecha de escritura del campo de costo, distinta del write_date de producto que cambia por otras operaciones. El historial externo de accounting confirma la vigencia de esos importes desde 18-ago07:00:06.
2. La propiedad de precio de COF-22-DOR (producto 7) y COF-22-PLA (producto 8) cambia el 24-ago03:23:06 UTC a 44.06 y44.54 MXN. Las categorias Cofres y Arras Sets tienen metodo average (AVCO) y valoracion manual_periodic; solo hay una compania accesible y su moneda es MXN.
3. A esa misma hora se crean las capas de valoracion6026/6027 de la recepcion EHV/IN/00009: entran1600 cofres dorados y400 plateados a unit_cost43.09, movimientos 2267/2268 Vendors>Stock. La fuente instalada de stock.move.product_price_update_before_done actualiza el promedio ponderado del producto recibido. La fecha/objeto de esa recepcion coincide con la escritura de sus propiedades de costo.
4. El historial de accounting comprobado por el agente principal muestra48.74 MXN para ambos cofres entre 18-ago07:00:06 y24-ago04:00:14, despues44.06/44.54. Restar el nuevo cofre al anterior explica9/9 diferencias. La conciliacion historica reproducible esta en [../accounting/conciliacion.json](../accounting/conciliacion.json).
5. Los nueve BOM SET son phantom; no se encontraron capas de valoracion propias para los nueve productos SET. Su configuracion AVCO no demuestra que sus valores provengan de un promedio de movimientos propios. El dato probado es que retienen el valor publicado del 18-ago; no se atribuye quien ni con que herramienta se asigno originalmente.

## Cifras por SET, MXN por unidad

| SET | BOM | Publicado Odoo | Material actual | Diferencia |
|---|---|---|---|---|
| SET-CAR-BLA-PEZ-DOR | 1824 | 126.2100 | 121.530 | 4.6800 |
| SET-CAR-BLA-PEZ-PLA | 1825 | 126.2100 | 122.010 | 4.2000 |
| SET-BLA-BRO-PEZ-DOR | 1788 | 175.2500 | 170.570 | 4.6800 |
| SET-ITA-PEZ-PLA | 1921 | 162.5600 | 158.360 | 4.2000 |
| SET-GAM-AZU-PEZ-PLA | 1897 | 146.5600 | 142.360 | 4.2000 |
| SET-GAM-NEG-PEZ-DOR | 1908 | 146.5600 | 141.880 | 4.6800 |
| SET-GAM-AZU-PEZ-DOR | 1896 | 146.5600 | 141.880 | 4.6800 |
| SET-GAM-NEG-PEZ-PLA | 1909 | 146.5600 | 142.360 | 4.2000 |
| SET-PLA-CH-PEZ-PLA | 1945 | 193.5600 | 189.360 | 4.2000 |

La conciliacion usa el BOM observado hoy valorado con precios historicos, no una version historica demostrada de la receta. Los BOM tienen write_date del 12-sept; no se ha probado que tuvieran exactamente las mismas lineas el 18-ago. La coincidencia numerica no identifica el metodo ni el autor de la asignacion inicial del costo SET.

La conciliacion usa Decimal, misma moneda y cantidades/unidades observadas. Todo rendimiento es1 Units. No hay restricciones de atributo, operaciones, subproductos ni sub-BOM que sumen estos importes. Las dos diferencias son48.74-44.06 y48.74-44.54. No se aplico FX ni se sumaron cantidades de modelos incompatibles.

## Por que no se ajustaron automaticamente los SET

En el codigo instalado, product.standard_price es Float company_dependent, con ayuda que lo describe como valor del producto calculado en AVCO y usado para valoracion y margenes. No tiene una dependencia computada de bom_line_ids. El promedio de recepcion escribe el costo del producto recibido, no recorre los productos que lo usan en sus recetas.

El recosteo por receta es otro camino: button_bom_cost llama _set_price_from_bom, que calcula _compute_bom_price y asigna standard_price. Ese camino puede escribir; no se invoco. El calculador instalado toma costo actual de cada componente, cantidad, UOM, operaciones/subproductos si existen y salida del BOM; no usa un importe oculto de 48.74 ni un recargo4.68/4.20.

No se encontraron acciones de servidor que mencionen button_bom_cost, _set_price_from_bom, standard_price o compute_price, ni crons ligados a esas acciones. base_automation no esta instalado. Esto acota la configuracion revisada; no es una prueba de inexistencia de cualquier script externo. La fecha de ir.property si demuestra que los valores SET observados no fueron escritos nuevamente despues del 18-ago.

El addon bom_total_cost tiene cost de linea y bom_cost/total_bom_cost propios; en estas recetas valen0. Su codigo calcula esos campos aparte mediante onchange. El camino de recosteo de mrp_account lee el standard_price del componente, no esos ceros. El addon no explica las diferencias concretas y no se modifico.

## Tratamiento propuesto

Para esta tarea, conservar standard_price publicado de Odoo, su moneda y sus vigencias en accounting/Orbit, conforme al contrato existente; esto no es una recomendacion contable general sobre AVCO o manual_periodic. No modificar costos, metodo de valoracion ni categorias y no ejecutar compute/button_bom_cost como si se tratara de una reparacion de sincronizacion. La suma de materiales actuales puede mostrarse como costo material de reposicion, claramente diferenciada del valor publicado.

Si el negocio decide que una proyeccion debe consumir costo de reposicion, eso requiere definir esa fuente y fecha de calculo como magnitud explicita; no sobrescribir un valor AVCO/retenido por una suma distinta sin cambiar el contrato. No hay una correccion productiva justificada por esta diferencia aislada. La revision queda resuelta con causa y conciliacion; el origen exacto de la primera asignacion de los SET sigue sin auditoria demostrada.

## Archivos y reproduccion

- odoo.py / odoo.json: propiedades del costo, companias, componentes y BOM/lineas; diferencia entre write_date de producto y fecha del costo.
- politica.py / politica.json: metodo/categoria, modulos y configuracion de acciones/automatizaciones. Codigo arbitrario de acciones no se imprime; solo hash/nombres de llamadas.
- valoracion.py / valoracion.json: consultas acotadas de capas SET y recepcion de cofres23-24ago; tipo phantom y rutaBuy.
- fuente.py / fuente-instalada.json: hashes y AST de funciones del Odoo instalado, sin importar el servidor ORM ni invocar negocio. Incluye definicion del campo, AVCO de recepciones y recosteo por BOM.
- resumir.py / resumen.json: conciliacion local Decimal 9/9 y marcas temporales con evidencia de recepcion.
- Historial y conciliacion independientes de accounting: carpeta ../accounting, mantenida por el agente principal y no modificada por este agente.

Los tres scripts XML-RPC se ejecutan por ssh goncloud python3 - < SCRIPT, con configuracion existente solo en memoria. fuente.py corre con ssh goncloud docker exec -i odoo-odoo-1 python3 -. Todos usan lecturas; no se invocaron metodos compute ni se escribieron Odoo, accounting, bridge u Orbit. Los scripts locales solo escriben evidencia dentro de esta carpeta.
