# PEZ 01: correspondencias MeLi con productos vigentes de Odoo

Estado: EN CURSO. Autorizacion: el usuario acepto «ok hazlo» al cierre operativo
de las nueve variantes PEZ: verificar identidad, corregir correspondencias,
sincronizar costos y comprobar el resultado en Orbit.

## Contrato

- La variante MeLi conserva su SELLER_SKU y su identidad. No se cambian
  publicaciones, precios, goals, Odoo, bridge ni accounting.
- El producto canonico es `product.odoo_sku`; su costo procede de
  `accounting.sku_costs`, como el resto de Orbit. No se copia un costo a un
  producto ficticio ni se recalcula la valoracion publicada de Odoo.
- Las fotos/atributos actuales acreditan SET con cofre para las nueve variantes.
  NH-SOLO tiene otra composicion y no debe usarse. La matriz comercial queda en
  `docs/evidencia/quick-wins-2026-11-04/pez-correccion/identidad/`.
- Una correspondencia explicita MeLi se conserva en `meli_sku_alias`:
  `seller_sku` unico/no vacio, `product_id` FK al canonico, `evidencia` no vacia,
  `created_at`. Solo el administrador escribe; los lectores y la ingesta leen. Una funcion
  sin argumentos ni DML toma los locks fijos con privilegios del propietario;
  el administrador solo recibe UPDATE(product_id) en listing_miembro, sin
  derechos de cambiar productos o importes.
- El catalogo carga esas correspondencias en su resolucion normal. Un SKU
  canonico exacto que contradiga un alias provoca error antes de escribir;
  ningun orden de filas decide el producto. Los SKU sin correspondencia siguen
  con producto desconocido.
- La reparacion inicial modifica unicamente las nueve filas verificadas de
  `listing_miembro` y crea sus aliases en una transaccion. Conserva el ancla
  de listing, el SKU MeLi, fechas de observacion y todo el resto de las filas.
  La siguiente ingesta usa la misma resolucion y no deshace la correccion.
- Antes de aplicar: fotografia de valores anteriores, comprobacion de
  cardinalidad/identidad/costo/moneda, ensayo sin escritura y reversa probada.
  Deriva respecto al lote esperado aborta todo, sin arreglos parciales ocultos.
- La ingesta de costos usa un snapshot SQLite por backup desde lectura;
  no ejecuta el wrapper que tambien ingiere FX/ledger/Ads. Reutiliza las
  vigencias existentes cuando coinciden, sin retroactividad inventada.
- El costo publicado en Odoo difiere de la suma actual de algunos componentes.
  Se registran ambos y se mantiene la fuente de valoracion del contrato actual;
  investigar esa diferencia no autoriza recalcular Odoo en este arreglo.

## Tareas

| Tarea | Estado | Depende | Criterio de cierre |
|---|---|---|---|
| P1. Identidad y estado previo | DONE | — | Nueve pares MeLi/SET comprobados con fotos, atributos y BOM; costos Odoo/accounting/Orbit conciliados; alcance actual medido. |
| P2. Resolucion persistente | DONE | P1 | Migracion 0075 y reversa; catalogo conserva alias al reingerir; prueba falla con resolver anterior y pasa con correccion; conflicto y permisos comprobados. |
| P3. Lote y reversa | DONE | P1, P2 | Herramienta operativa acotada con ensayo, precondiciones, respaldo y reversa; pruebas PG de aplicacion, deriva y restauracion. |
| P4. Integracion y aplicacion | WIP | P2, P3 | Revision independiente, gitleaks y CI verdes; migracion/app desplegadas; nueve miembros corregidos con costos externos coincidentes y cero cambios fuera del lote. |
| P5. Cierre | TODO | P4 | Persistencia de ingesta comprobada, conteos antes/despues, costo fresco y documentacion actualizada. |

## Evidencia previa

- `docs/evidencia/quick-wins-2026-11-04/pez-bom/`: distingue recetas archivadas
  y candidatos NH-SOLO; la identidad comercial posterior los descarta para
  este lote y encuentra SET para las nueve, incluido Blanco con Broche.
- `docs/evidencia/quick-wins-2026-11-04/pez-correccion/orbit-antes.json`:
  nueve miembros sin producto; los nueve productos SET y costos ya existen
  en Orbit. Las metas consultadas no incluyen esas nueve publicaciones.
- `app/meli/catalogo.py`: la resolucion anterior usa coincidencia exacta de
  SKU y sobreescribe el producto de miembros en cada ingesta. Un UPDATE aislado
  no es una reparacion duradera.
