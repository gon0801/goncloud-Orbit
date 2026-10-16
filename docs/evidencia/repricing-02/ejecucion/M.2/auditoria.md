# M.2: auditoria de quien lee `ledger_event`

Comando: `git grep -n "ledger_event" -- app migrations`.
Medido en esta punta: **108 lineas en 21 archivos** (el encargo cita
85/17 de un arbol anterior; la diferencia son las adiciones 0.a-0.b-F.4:
DDL 0054 y su reversa, atribucion en `app/ledger.py`, `app/ledger_atribucion.py`
nuevo y la lectura de historia en `app/spapi/precio_mercado.py`). Todas
cubiertas abajo. Solo importan los lectores runtime; el DDL base (0001)
y los comentarios no cambian.

## Lectores runtime

| Lector | Agrupa o filtra por plataforma | Que vera con filas `meli` | Filtro |
| --- | --- | --- | --- |
| `app/ledger.py` (ingesta) | Es el escritor; M.2 lo abre | 5,600 eventos historicos + flujo | — (es el cambio) |
| `app/envio_muestras.py` | `WHERE platform = %s` | El job `meli` ve sus cargos; `otros` contado | No |
| `app/familias.py` | `platform = %s` en ambas consultas | Solo si se le pide `meli`, correcto | No |
| `app/pantalla_productos.py` | `WHERE platform = %s` | Solo si se le pide `meli`, correcto | No |
| `app/spapi/precio_mercado.py` (historia) | `WHERE platform = %s` | Su plataforma, igual que hoy | No |
| `v_contribucion_entidad` | `WHERE platform IN ('amazon_mx','amazon_us')` explicito | Nada: `meli` invisible por diseno | No (sin cambio) |
| `v_tacos` | `FULL OUTER JOIN` por `(platform, mes)` | Fila `(meli, mes)` con venta y `tacos_pct` NULL (sin ads ingeridos). Sus consumidores (`api_precios`, `notifica`) filtran por plataforma; G.3 muestra `None` honesto | No |
| `v_target_margen_plataforma` | `GROUP BY platform` generico | Fila `meli` con su margen medido: inclusion correcta | No |
| `v_margen_familia` | `GROUP BY platform` generico | Igual: inclusion correcta | No |
| `v_economia_producto` | Generica por `(platform, product)` | Ventas `meli` con producto fluyen. 140/1,050 ventas sin SKU quedan fuera por `product_id IS NOT NULL` (simetrico con Amazon). `fees_sin_tipo` suma los 56 reembolsos `meli` sin subtipo: visible, es su trabajo | No |
| `v_margen_producto` (0018) | Generica por `(platform, product)` | Filas `meli` correctas; el guard `fees_sin_tipo = 0` da NULL donde aplique, como disenado | No |
| `v_precio_venta_unidad` (0054) | `LEFT JOIN` a atribucion por diseno | Ventas atribuidas por `item_id`, resto NULL contable | No |

## Correcciones en el mismo PR

Ninguna: ningun lector necesita filtro. Decisiones fijadas por esta
auditoria y pineadas con pruebas:

- Reembolso `meli` sin categoria guarda `fee_type` NULL (no se inventa
  subtipo); cae en los contadores `fees_sin_tipo`, a la vista.
- Venta `meli` fija `product_id` por SKU exacto contra `product.odoo_sku`;
  sin match queda NULL (140/1,050 historicas).
- Reversas (`fee_reversal`, montos positivos) se saltan por
  `ledger_convencion_signos`, contadas, igual que en Amazon (73/5,600).
