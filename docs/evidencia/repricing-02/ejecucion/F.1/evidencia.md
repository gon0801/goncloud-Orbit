# F.1: atribuidor de Amazon

Fila **F.1** de `plans/repricing-02.md`, seccion 4, rama `rp02/sec4-fbm-us`.
Prueba primero, despues codigo (`app/ledger_atribucion.py`; `app/ledger.py`
no se toca: desde 0.a ya inserta lo que el atribuidor devuelve y cuenta
`venta_sin_atribuir`).

## Diseno

El protocolo `Atribuidor.atribuir(fila)` recibe solo el evento, sin
conexion, y `app/ledger.py` es contrato del carril M: el atribuidor abre su
propia conexion de SOLO LECTURA (`autocommit`, `read_only`) desde
`ORBIT_DSN_INGEST` al primer evento que pide base. Solo hace SELECT sobre
tablas que la ingesta del ledger no escribe (`listing`,
`spapi_order_observation`, `spapi_inventario_observation`), asi que no ve
datos a medias. Sin DSN o sin conexion devuelve None y la venta se cuenta
para reintento. En pruebas recibe la conexion por constructor.

Escalera, con datos de produccion medidos el 2026-10-15 (SELECT por
`orbit_read`, solo lectura):

- Un candidato (`listing` con ese `product_id` en la plataforma) →
  `resuelto_por="sku"`, sin mirar nada mas. En prod, 178 de 249 productos
  de MX y 75 de 119 de US tienen un solo listing.
- Varios candidatos → el canal de la orden elige (`canal_orden`). Canal
  del listing: `spapi_inventario_observation` es inventario FBA por
  definicion (su COMMENT lo dice), asi que fila presente = FBA, ausente =
  FBM; medido: separa perfecto los 518 listings de Amazon (314 con fila
  traen fuente fba en disponibilidad, 204 sin fila traen solo fbm).
  Canal de la orden: `fulfillment_channel` (`fulfilledBy`) con vocabulario
  `AMAZON/AFN → fba`, `MERCHANT/MFN → fbm`; se toma el ultimo no nulo.
  Exactamente un candidato del canal de la orden atribuye; empate o
  canal desconocido → None.
- `externo_unico` y `producto_unico` quedan para el atribuidor de MeLi
  (M.2): el evento de Amazon solo trae `product_id` y `order_id`, sin SKU
  ni ASIN, asi que este atribuidor no los distingue.

Riesgo conocido: un listing FBA nuevo, antes de su primera fila de
inventario (el sync es diario), se lee como FBM. Solo afecta a productos
multi-listing en esa ventana; documentado aqui, no silenciado.

## Comprueba

`comprobar.sh` vuelca prod (esquema + 6 tablas), carga la copia
`orbit_f1_comp` en el 5432 del loop, corre `atribuir_copia.py` (en seco,
sin escribir atribuciones) y borra la base. Salida literal en
`comprobar-salida.txt` (ids de orden redactados, repo publico):

- ventas=1985, sku=1028, canal_orden=82, sin_atribuir=875
- motivos: empate=46, sin_canal=586, sin_producto=243 (sin_orden=0,
  sin_listing=0)
- reintento: OK (borra la observacion de una orden `canal_orden` en la
  copia → None; la restaura con los mismos datos → atribuye igual)

El 44 % sin atribuir es honesto: 243 ventas de ASIN que ya no estan en
`listing` (se cuentan y se reintentan; si el ASIN no vuelve, quedan
fuera) y 586 de productos multi-listing cuya orden no trae canal (el
sync de ordenes solo cubre ventana reciente: 225 de 1400 ordenes de MX
traen canal). S.7 vera `sin_atribuir > 0` en esas unidades.

## Pruebas

`tests/test_precio02_atribucion.py` (9 pruebas, DoD 1:1) con ORDEN
`0001, 0028, 0030, 0035, 0039, 0047, 0053, 0054` (0.a + ordenes e
inventario). Candado en `tests/test_arq_precio_f.py`: el atribuidor no
trae verbos de escritura, con fuga sembrada que si detecta. Ver
`mutantes.md` (9/9 mueren, incluido el obligatorio "venta indecidible
guardada como fila fija").
