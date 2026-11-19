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

`activos` marca cada producto con `bool_and`: si alguna publicación activa de esa
plataforma no tiene `seller_sku`, el producto entero sale de candidatos. Así no se
confirma «menos de 20 clics» con clics que no se pueden atribuir, ni siquiera cuando
otra publicación del mismo producto sí tiene SKU. La cobertura del caso puro y del
mixto queda en la prueba de dinero de W-M.1.
