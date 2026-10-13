# M.0: sonda de Mercado Libre (solo lectura, dentro de `orbit-app-1`)

Fila **M.0** de `plans/repricing-02.md` (`[stage:verificacion] [lane:gate]
[tdd:skip:sonda]`), sección 1, rama `rp02/sec1-lecturas`. Corrida el
**2026-10-13** UTC. Evidencia: `salidas/sonda-meli.txt` (salida saneada:
sin tokens, sin id de vendedor, sin datos de comprador; cada publicación,
SKU y nombre redactado — `<item>`, `<id>`, `<fuera>` — porque el repo es
público).

Comando:

```
ssh goncloud 'docker exec -i orbit-app-1 python -' \
    < docs/evidencia/repricing-02/ejecucion/M.0/sonda_meli.py
```

`sonda_meli.py` arma las credenciales desde `meli_tokens.json` sin exigir
`client_id`/`client_secret` (el archivo de producción no los trae; con el
token vivo los GET bastan y un 401 fallaría antes de reescribir el
archivo). Antecedente: esta sonda se frenó una vez (LISTO-R01-r1) porque el
token estaba vencido y sin cadena de refresco; el dueño reparó las
credenciales el 2026-10-13 (`users/me` → 200) y esta es la corrida con el
token vivo.

Rutas usadas: `/users/me`, `/users/{vendedor}/items/search`,
`/items/{id}` (×30), `/pricing/llm/listing-charges`,
`/pricing/llm/listing-charges/tables`,
`/categories/{cat}/listing_types/{tipo}/sale_fee`, `/sale_fee`,
`/listing_prices`, `/sites/MLM/listing_types/{tipo}`,
`/categories/{cat}/listing_types/{tipo}`,
`/items/{id}/visits/time-window`, `/items/{id}/visits`,
`/visits/items?ids={id}`, `/users/{vendedor}/applications`. Todas GET; no
se escribió nada.

## Las cuatro conclusiones

**1. El SKU de cada variante NO sale de la publicación.** Sobre 30
publicaciones del vendedor (24 con variantes, 143 variaciones): **ninguna**
variación trae `seller_sku` ni `seller_custom_field`, y ninguna raíz trae
un `seller_custom_field` con la forma de `odoo_sku`
(`XX-XXXX-XXXX`); solo 6 de 30 raíces traen `seller_custom_field` (18
caracteres, otra forma). El catálogo (M.1) no puede armar el cruce
publicación→producto desde la API del item: hace falta el mapeo del bridge
(`meli_sku_mapping`, que el diseño ya declaró tabla del bridge) u otra
fuente. Así se cierra la incógnita declarada de G22 («de dónde sale el SKU
lo dice la sonda»): no sale del item.

**2. No hay consulta de cargos por precio; hay estructura porcentual por
categoría y tipo que separa porcentaje y fijos.** Todas las rutas de
cotización a un precio dado dieron 404 (`/pricing/llm/listing-charges`,
`.../tables`, `/categories/{cat}/listing_types/{tipo}/sale_fee`,
`/sale_fee`, `/listing_prices`). Lo que sí existe y responde:
`/sites/MLM/listing_types/gold_pro` y
`/categories/MLM1440/listing_types/gold_pro`, con `sale_fee_criteria` = porcentaje + montos fijos + moneda
(site: 22.5 %; categoría MLM1440: **19.5 %**, `min_fee_amount` 0,
`max_fee_amount` 283350 MXN) y `listing_fee_criteria` (0 en ambos).
**Conclusión para la sección 2: `meli/meli` se registra sin cotizador**
(no hay consulta de cargos, dice la guía), con cierre `lineal`; la tabla de
`sale_fee_criteria` queda documentada aquí por si el dueño prefiere una
fórmula declarada en vez de `lineal` — esa decisión no es de esta fila.

**3. El precio va en la raíz y repetido en cada variación.** La publicación
con variantes trae `price` en la raíz (1989 MXN) y cada una de sus 6
variaciones trae su propio `price` (todas 1989), además de su
`available_quantity` propio. Para escribir (M.4) el precio de una
publicación con variantes habrá que tratar el precio por variación: la
forma candidata de `MeliWriteClient` nace sabiendo que el item replica el
precio en cada variación.

**4. El token permite escribir.** Leído de los permisos declarados (nunca
se escribió para averiguarlo): el `scope` del token incluye `write` y
`urn:ml:mktp:publish-sync:/read-write`, entre otros `read-write`
(`orders-shipments`, `offers`, `invoices`…). El detalle por aplicación
(`/users/{vendedor}/applications`) responde una lista, que
`ClienteMeli` no acepta (solo objetos), así que la fuente es el scope del
propio token, que es lo que la app declaró al autorizar.

## De paso: visitas

`/visits/items?ids={id}` responde el total de visitas por publicación
(1164 en la inspeccionada); las variantes de ventana
(`/items/{id}/visits/time-window` → 404, `/items/{id}/visits` → 400) no
respondieron. Para la señal de tráfico (D10) el total por publicación ya es
un dato; la ventana por día queda por resolver en el carril M si se
necesita.
