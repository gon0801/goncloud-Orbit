# M.0: sonda de Mercado Libre (solo lectura, dentro de `orbit-app-1`)

Fila **M.0** de `plans/repricing-02.md` (`[stage:verificacion] [lane:gate]
[tdd:skip:sonda]`), sección 1, rama `rp02/sec1-lecturas`. Corrida el
**2026-10-13** UTC (ronda r3, sobre el árbol del commit `eaa2545` con los
cambios de esta ronda sin commitear, como sella la corrida; **código de
salida 0**). Evidencia: `salidas/sonda-meli.txt` (salida saneada: sin
tokens, sin id de vendedor, sin datos de comprador; cada publicación, SKU y
nombre redactado — `<item>`, `<id>`, `<fuera>` — porque el repo es público;
los SKU del cruce con `product` nunca se imprimen, solo conteos).

Comando:

```
ssh goncloud 'docker exec -i orbit-app-1 python -' \
    < docs/evidencia/repricing-02/ejecucion/M.0/sonda_meli.py
```

`sonda_meli.py` arma las credenciales desde `meli_tokens.json` sin exigir
`client_id`/`client_secret` (el archivo de producción no los trae; con el
token vivo los GET bastan y un 401 fallaría antes de reescribir el
archivo). Antecedentes: la sonda se frenó una vez (LISTO-R01-r1) por un
token vencido sin cadena de refresco; el dueño reparó las credenciales el
2026-10-13. En la ronda r2 la conclusión 1 fue falsa (pedía `/items/{id}`
sin `include_attributes`, y sin ese parámetro las variaciones no traen
`attributes`); la revisión R01-r2 lo corrigió y esta es la corrida buena.

Rutas usadas: `/users/me`, `/users/{vendedor}/items/search`,
`/items/{id}?include_attributes=all` (×30), `/items/{id}` (una, sin el
parámetro, para mostrar la diferencia), `/sites/MLM/listing_prices`
(×3 precios), `/sites/MLM/listing_types/{tipo}`,
`/categories/{cat}/listing_types/{tipo}`,
`/items/{id}/visits/time_window`, `/visits/items?ids={id}`,
`/users/{vendedor}/applications`; y un SELECT en `BEGIN READ ONLY` por el
rol lector para el cruce con `product.odoo_sku`. Todas GET; no se escribió
nada.

## Las cuatro conclusiones

**1. El SKU de cada variante sale del atributo `SELLER_SKU` del item,
pidiendo `include_attributes=all`.** Con ese parámetro, en 30 publicaciones
del vendedor (24 con variantes, 143 variaciones): 143 de 143 variaciones
traen `attributes` con `SELLER_SKU` (143 SKU distintos), las 6 publicaciones
sin variantes lo traen en la raíz, y las 24 con variantes tienen TODAS sus
variaciones con SKU. Sin el parámetro (probado con la misma publicación),
0 de 6 variaciones traen `attributes` y la raíz tampoco trae `SELLER_SKU`:
así se explica la falsa conclusión de la ronda r2. Los campos `seller_sku`
y `seller_custom_field` de variación vienen vacíos siempre (0 de 143); 6
raíces traen `seller_custom_field`. El cruce real contra
`product.odoo_sku` (por conteo, rol lector): **149 miembros, 149 con SKU,
143 mapean a un producto (143 productos distintos); de 30 publicaciones, 24
con todos sus miembros mapeados, 6 con alguno y 0 sin ninguno**. M.1
construye el catálogo cruzando ese atributo contra `product.odoo_sku`,
sin necesidad de tablas del bridge.

**2. SÍ hay consulta de cargos por precio, categoría y tipo de publicación,
y separa porcentaje y fijo: `/sites/{sitio}/listing_prices`.** La ronda r2
probó `/listing_prices` en la raíz (404) y no encontró esta ruta. Con
`price`, `category_id` y `listing_type_id` de la publicación de muestra
(MLM1440 / gold_pro): a 1989 responde `sale_fee_amount` 387.86 con
`sale_fee_details {percentage_fee: 19.5, fixed_fee: 0, gross_amount:
387.86}`; a 979.11 da 190.93 y a 150 da 29.25 (19.5 % en los tres, fijo
en 0 y `listing_fee` también separado y en 0). **Conclusión para la
sección 2: `meli/meli` va CON cotizador** — la condición de la guía para
registrarlo sin cotizador («si no hay consulta de cargos») no se cumple.
Como dato de apoyo quedaron las estructuras de cargo del sitio
(`sale_fee_criteria` 22.5 %) y de la categoría (19.5 %, min 0, max 283350
MXN), con sus `exceptions_by_category`.

**3. El precio va en la raíz y repetido en cada variación.** La publicación
con variantes trae `price` en la raíz (1989 MXN) y cada una de sus 6
variaciones trae su propio `price` (todas 1989), además de su
`available_quantity` propio. En las 24 publicaciones con variantes de la
muestra, NINGUNA tiene un precio distinto entre raíz y variaciones. Para
escribir (M.4) el precio de una publicación con variantes habrá que
tratar el precio por variación: la forma candidata de `MeliWriteClient`
nace sabiendo que el item replica el precio en cada variación.

**4. El token permite escribir.** Leído de los permisos declarados (nunca
se escribió para averiguarlo): el `scope` del token incluye `write` y
`urn:ml:mktp:publish-sync:/read-write`, entre otros `read-write`
(`orders-shipments`, `offers`, `invoices`…). El detalle por aplicación
(`/users/{vendedor}/applications`) responde una lista, que `ClienteMeli`
no acepta (solo objetos), así que la fuente es el scope del propio token,
que es lo que la app declaró al autorizar.

## De paso: visitas

- `/items/{id}/visits/time_window?last=7&unit=day` (con guion bajo; la
  ronda r2 probó `time-window` con guion y dio 404): responde 200 con
  `total_visits` y `results` por día (10 visitas en los 7 días de la
  publicación de muestra).
- `/visits/items?ids={id}`: total histórico por publicación (1164 en la
  inspeccionada).

Para la señal de tráfico (D10) ambas sirven; la ventana por día ya está
disponible con `time_window`.
