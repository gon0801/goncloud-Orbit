# F.2: envio con origen y job de muestras

Fila **F.2** de `plans/repricing-02.md`, seccion 4. `app/precio/envio.py`
(puro, seccion C) + `app/envio_muestras.py` (job diario, seccion O, rol
`app_decide`, unico escritor de `precio_envio_muestra`).

## Reglas selladas (medidas en prod el 2026-10-15 por `orbit_read`)

- La etiqueta se cuenta UNA vez: `max(labman, shipping_label, mfn_postage)
  + shipping_hb`. HB solo (38 ordenes, 2.7 %) no es etiqueta: sin etiqueta
  no hay costo, por regla del plan (E.0: HB es "otro componente").
- Todo `shipping_fee` es negativo y MXN (2036/2036, tambien US: la
  conversion a USD la hace F.3). El job guarda `abs()`.
- Las seis identidades de F.0, ninguna mas (ahora con historia completa:
  MX trae las seis, US tres). Chargebacks y lo desconocido → `otros`,
  contado, fuera del costo.
- Mapeo orden→producto estricto: TODAS las ventas de la orden con el MISMO
  producto no nulo. Una venta sin producto contamina a `sin_producto`
  (podria ser otro producto compartiendo el envio). En prod: 1740 ordenes
  de un producto, 1 de dos, 242 sin producto mapeable.
- Ventana dias cerrados `[hoy-ventana, hoy-1]` (el dia en curso se
  descarta). `precio_envio_ventana_dias` = 180 (semilla 0054).
- `main` valida el universo contra el registro: sin `envio == "muestra"`
  sale 2 (fba usa politica, no job).

## Comprueba

`comprobar.sh`: copia prod → corre el `main` real dos veces por plataforma
→ distribuye por origen. Salida en `comprobar-salida.txt`:

- MX: 249 productos → 249 muestras (107 `propio`, 142 `marketplace`, 0
  `familia`); exclusiones multiproducto=1 sin_producto=6 otros=4.
- US: 119 → 119 (47 `propio`, 72 `marketplace`); exclusiones 0/2/0.
- Segunda corrida: 0 filas nuevas en las dos plataformas.
- Cero `familia` porque `producto_familia` esta vacia en prod (medido: 0
  filas): sin familia asignada la escalera cae a `marketplace`, como manda
  el DoD. El camino `familia` queda cubierto por fixtures.
- `MonedasMezcladas` no salto: todo el universo mide MXN.

## Pruebas

`tests/test_precio_envio.py` (16 puras, sin base) y
`tests/test_envio_muestras.py` (11 con base, ORDEN02 de 0.a reutilizado).
Ver `mutantes.md` (28/28 mueren: 16 + 11 + el obligatorio extra
"familia sin donantes", que la base rechaza por CHECK).
