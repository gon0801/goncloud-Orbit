# ORBIT 19 / 0.2 — Contrato API/CLI v2 (PROPUESTO)

No vigente. Depende de D2/D3. Un solo normalizador produce el plan
canonico; no hay segundo motor de creacion.

## Compatibilidad v1 (intacto)

API actual `POST /api/fabrica/plan` y `POST /api/fabrica/crear` siguen
aceptando `productos: list[int]` con la semantica de hoy: un producto =
un listing, aborta si hay varios listings o margen NULL.

CLI `--productos` igual. Huella v1 no se rehashea. Lotes existentes
(hoy: cero filas) se leen con el lector v1.

Rechazar cuerpo que mezcle `productos` y `listing_ids`.

## API v2 (nueva, misma ruta o campo discriminado)

```
{
  "plataforma": "amazon_mx"|"amazon_us",
  "tipo_producto": "...",
  "nombre_base": "...",
  "listing_ids": [int, ...],   # unicos, min 1, max 100
  "modo": "shadow"|"live",
  "objetivo": {
    "origen": "margen_medido"
  } | {
    "origen": "manual_lanzamiento",
    "acos_pct": "25.00"        # fixture, NUNCA default
  },
  "parametros": { ...cinco roles budget/bid string... }
}
```

Reglas:

- `listing_ids` se resuelven a listing de la plataforma. Mercado cruzado,
  ID inexistente, seller_sku vacio o SKU duplicado en el mismo grupo:
  422 **antes** del primer POST.
- Varios listings del mismo `product_id` son legales.
- `origen=margen_medido` solo si **todos** tienen margen maduro de
  `v_margen_producto`. Si falta uno: 422 pidiendo `manual_lanzamiento`.
- `acos_pct` decimal positivo, mismo rango/precision que
  `ads_optimizer_goal.target_acos_pct` (NUMERIC(6,2) > 0). Sin clamp
  silencioso a 10%.
- Preview invalida si cambia listing, objetivo, budget, bid, modo o fecha.

CLI propuesto: `--listing-ids 1190,1206` y `--target-acos 25.00`.
`--productos` no se convierte a listing en silencio.

## Snapshot persistido

`fabrica_lote.plan` v2:

```
schema_version: 2
publicaciones: [{listing_id, product_id, asin, seller_sku, platform, margen_neto_pct|null}]
objetivo: {origen, acos_pct, procedencia, fraccion|null, derivado|null}
```

Campo medido ausente = JSON `null`, no `"None"` ni 0. Huella v2 ordena
por `listing_id` antes del hash. Cambiar objetivo, publicacion o gasto
cambia la huella. Clasificacion asesora (Por probar, ACoS vs target) no
entra en la huella.

v1 sin `schema_version` conserva el JSON actual (`margen_neto_pct` string
obligatorio, un listing por producto).

## GET /catalogo

Hoy: `elegible=false` si multilisting o sin margen.

Propuesto (fase A, no 0.2 codigo): cada publicacion es seleccionable si
tiene identidad (listing_id, ASIN, seller_sku, plataforma). `elegible` de
producto desaparece o pasa a ser por listing. Motivos se vuelven
informativos (sin margen, multilisting, sin ventas), no bloqueo.

GET sigue sin HTTP Amazon.
