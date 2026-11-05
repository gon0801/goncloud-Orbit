# PEZ 01: cierre operativo de nueve correspondencias

Estado: implementacion y lote preparados, pendientes de CI, despliegue y
aplicacion. Autorizacion del usuario: «ok hazlo» a corregir las nueve variantes
y verificar producto/costo en Orbit. [Plan](../../../../plans/pez-01.md).

## Resultado comprobado antes de aplicar

- Las nueve variantes MeLi corresponden a SET con cofre de Odoo, incluido
  Blanco con Broche. Los NH-SOLO de la investigacion anterior tienen otra
  composicion. [Fotos, atributos, BOM y matriz](identidad/README.md).
- Los nueve costos finales publicados coinciden entre Odoo, accounting y
  Orbit; ya existian las vigencias canonicas. El costo material de componentes
  se conserva aparte y no se usa para sobrescribir la valoracion de Odoo.
- Los nueve miembros de Orbit tienen producto desconocido; siete estan
  activos y dos inactivos. El lote conserva ambos estados y el SELLER_SKU real.
- El catalogo anterior resuelve exclusivamente por SKU exacto y sobreescribe
  el producto al reingerir. Un UPDATE manual aislado no persiste.

## Correccion

La migracion 0075 crea `meli_sku_alias` para correspondencias verificadas hacia
el producto canonico. El catalogo usa el mismo camino para resolverlas y
rechaza conflictos con un SKU canonico exacto antes de escribir. No cambia
el identificador de la variante ni el SKU externo de MeLi.

[lote.json](lote.json) identifica las nueve filas y sus valores esperados.
`tools/corrige_meli_skus.py` ensaya por defecto y aplica todo en una transaccion.
Guarda un respaldo 0600 con hash y fsync antes del COMMIT. La reversa comprueba
el estado aplicado antes de restaurarlo; si una ingesta u otra operacion lo
cambio, aborta para no pisar esa modificacion. No altera los importes, las
vigencias, los precios, las metas ni el estado activo/inactivo.

La cuenta administradora recibe UPDATE solo de `listing_miembro.product_id`.
Una funcion sin parametros ni DML toma locks sobre tablas fijas, sin conceder
permisos de cambiar productos o costos. La migracion tiene reversa independiente;
antes de retirarla se revierte el lote y se repone el catalogo anterior.

## Comprobaciones

- [P2](p2-validacion.txt): dos regresiones fallan contra el codigo anterior;
  24 pruebas PostgreSQL comprueban reingesta, costo, desconocidos, conflicto,
  permisos y reversa del esquema.
- [P3](p3-validacion/README.md): siete pruebas PostgreSQL con `app_admin`
  comprueban ensayo, aplicacion, respaldo, reversa, reintentos y deriva. El
  caso con siete miembros activos y dos inactivos fallo antes de corregirlo.
- [Estado previo](orbit-antes.json), [permisos reales](permisos-antes.json) y
  [version productiva del catalogo](predeploy.json). La version del catalogo
  del servidor coincide con la base revisada.

## Alcance y seguimientos

No se modifican Odoo, bridge, accounting, publicaciones, precios ni goals.
La actualizacion de costos, si se ejecuta, usa solo el importador normal y
snapshot SQLite de lectura; no el wrapper que tambien ingiere metricas.

Las descripciones genericas MeLi indican medidas que difieren de la variante
SET en Odoo; las fotos/atributos especificos sustentan la identidad del lote,
sin afirmar una medicion fisica. La diferencia entre costo final y suma actual
de componentes (4.68 MXN dorado / 4.20 MXN plateado) queda como seguimiento
separado de valoracion. Ninguna de esas diferencias se corrige silenciosamente
durante esta vinculacion.
