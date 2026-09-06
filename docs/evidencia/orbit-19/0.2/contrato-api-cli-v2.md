# ORBIT 19 / 0.2 — Contrato API/CLI v2

Cerrado 2026-09-06 21:31 UTC (`confirmacion.md`). No hay codigo v2 en
produccion hasta A. Un solo normalizador produce el plan canonico.

## Compatibilidad v1 (intacto)

API actual `POST /api/fabrica/plan` y `POST /api/fabrica/crear` siguen
aceptando `productos: list[int]` con la semantica de hoy: un producto =
un listing, aborta si hay varios listings o margen NULL.

CLI `--productos` igual. Huella v1 no se rehashea. Lotes existentes
(hoy: cero filas) se leen con el lector v1.

Rechazar cuerpo que mezcle `productos` y `listing_ids`.

## API v2 (mismas rutas; discriminacion por campos)

Mismos `POST /api/fabrica/plan` y `POST /api/fabrica/crear`.
v1: cuerpo con `productos`, sin `listing_ids` ni `objetivo`.
v2: cuerpo con `listing_ids` y `objetivo`, sin `productos`.
Mezclar ambos → 422. Un normalizador, no una ruta nueva.

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
- `origen=margen_medido` solo si **todos** tienen margen maduro **y
  positivo** de `v_margen_producto`, y el objetivo derivado no supera
  ese margen. Si alguno esta ausente, es 0, es negativo, o el clamp
  superaria el margen: 422 pidiendo `manual_lanzamiento`.
- `acos_pct` decimal positivo, mismo rango/precision que
  `ads_optimizer_goal.target_acos_pct` (NUMERIC(6,2) > 0). Sin clamp
  silencioso a 10%. El objetivo manual **no acredita rentabilidad**.
- Preview y revision declaran por publicacion: margen maduro / muestra
  limitada / ausente / cero / negativo, y si el margen conocido es
  **inferior al objetivo**. Cero, negativo o inferior al objetivo no
  deshabilitan la seleccion. No se presenta el grupo como rentable
  por tener target manual.
- Preview invalida si cambia listing, objetivo, budget, bid, modo o fecha.

CLI v2: `--listing-ids 1190,1206` y `--target-acos 25.00`.
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

Misma ruta `GET /catalogo?plataforma=`. F1 vigente hasta A.5: `elegible`
por producto (false si multilisting o sin margen).

Fase A (un solo shape): cada publicacion es seleccionable si tiene
identidad (`listing_id`, ASIN, seller_sku, plataforma). `elegible` pasa
a ser **por listing** (true si la identidad es valida). Motivos
(sin margen, multilisting, sin ventas, cero, negativo) son informativos,
no bloqueo. El producto agrupa publicaciones; no hay `elegible` de
producto que tape un listing valido.

GET sigue sin HTTP Amazon.
