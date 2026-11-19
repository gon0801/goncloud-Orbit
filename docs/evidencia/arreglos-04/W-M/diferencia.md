# W-M: diferencias entre el diseño y lo entregado

## Aviso de productos que vendieron

El diseño (`DISENO.md`, sección W-M) cuenta `r.data.vendieron.length` como `n` de
«productos marcados». `vendieron` son ids de publicación (`listing`) y una casilla
marcada trae todas las publicaciones del producto en `data-listings`, así que la
cifra contaba publicaciones y las llamaba productos.

La revisión del dueño en el PR #673 (2026-11-19) pidió separar las dos cifras.
`verRetiro` cuenta ahora `P`, las casillas marcadas con alguna publicación en
`vendieron`, y `K`, las publicaciones de `vendieron`:
«{P} de los productos marcados vendieron en los últimos 11 meses: se conservan los
anuncios de {K} publicaciones.», con «1 de los productos marcados vendió…» y «…de 1
publicación.» en singular. El diseño conserva su texto; esta nota registra la
diferencia.

## Publicaciones sin seller_sku

`listing.seller_sku` admite nulo (`migrations/0001_initial.sql`) y la ingesta
descarta los vacíos (`app/spapi/listings.py`). El JOIN por SKU de `_SQL_CANDIDATOS`
no cruza esas publicaciones y el JOIN anterior por ASIN sí les contaba clics, así
que entraban a candidatos con cero clics por falta de dato.

`activos` exige ahora una publicación activa con `seller_sku` no vacío: un producto
sin SKU atribuible sale de candidatos en vez de entrar con cero. La cobertura del
dato faltante queda en la prueba de dinero de W-M.1.
