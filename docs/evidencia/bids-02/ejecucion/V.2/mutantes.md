# Mutantes de V.2 (migracion 0062)

Un mutante por prueba nueva de `tests/test_migracion_0062.py`: cada cambio es
de una linea y pone su prueba en rojo. Verificados el 2026-10-10 corriendo
cada prueba contra su mutante (los cuatro dieron `1 failed`) y restaurando
despues (la suite quedo en `4 passed`).

| Prueba | Mutante (1 linea) | Efecto |
| --- | --- | --- |
| `test_0062_rechaza_placement_fuera_de_ubicacion` | En `migrations/0062_bids02_placement.sql`: `'arriba_de_busqueda', 'resto_de_busqueda', 'paginas_de_producto', 'fuera_de_amazon'` por `'top_de_busqueda', 'resto_de_busqueda', 'pagina_de_producto', 'fuera_de_amazon'` (los textos de `datos.sql`) | `'top_de_busqueda'` entra y `'arriba_de_busqueda'` revienta: `1 failed`. |
| `test_0062_columnas_sin_top_of_search_is` | En `migrations/0062_bids02_placement.sql`: agregar la linea `top_of_search_is NUMERIC,` antes de `source_report_id` | La lista de columnas trae 12 con la extra: `assert [...] == [...]` falla, `1 failed`. |
| `test_0062_dedupe_absorbe_reingesta` | En `migrations/0062_bids02_placement.sql`: `CREATE UNIQUE INDEX apo_dedupe_reporte` por `CREATE INDEX apo_dedupe_reporte` | El `ON CONFLICT` no encuentra constraint unico: `psycopg.errors.InvalidColumnReference: there is no unique or exclusion constraint matching the ON CONFLICT specification`, `1 failed`. |
| `test_0062_reversa_deja_esquema_como_estaba` | En `migrations/0062_reversa_bids02_placement.sql`: `DROP TABLE ads_placement_observation;` por `-- DROP TABLE ads_placement_observation;` | La tabla sobrevive a la reversa: la foto del catalogo difiere (`Differing items`), `1 failed`. |

## Modulo y wiring (verificados 2026-10-10, todos MUEREN con `1 failed`)
- `PLACEMENTS_CFG` pide `topOfSearchImpressionShare` -> test_body_reporte_placements: `1 failed`.
  (Mutante nombrado por la guia.)
- `ingest_placements` inserta sin `ON CONFLICT` -> test_mismo_reporte_dos_veces_no_agrega_filas:
  `1 failed` (la reingesta choca con el indice dedupe). (Mutante nombrado por la guia.)
- `_UBICACION` mapea "Off Amazon" a `resto_de_busqueda` -> test_clasificaciones_mapean_a_ubicacion:
  `1 failed`.
- `_planea_filas_placements` sin el gate de metrica negativa -> test_filas_malas_saltan_con_motivo:
  `1 failed`.
- `main` sin la exclusion (`--placements` + `--productos`) ->
  test_main_placements_excluyente_con_productos: `1 failed` (sale 2 por DSN, sin el mensaje).
- `SOURCE_PLACEMENTS` con el valor del pipeline principal ->
  test_fallo_de_transporte_sella_run_propia_y_no_toca_principal: `1 failed`.
- `sync_placements` con `observed_at` fijo (medianoche) ->
  test_dos_reportes_mismo_dia_dejan_dos_observaciones_y_distinct_on_una: `1 failed` (la segunda
  ingesta choca en la PK).

## Panel V.2 (verificados 2026-10-10, todos MUEREN con `1 failed`)
- Preflight con el `source` principal (`SOURCE_PLACEMENTS` sin usar) ->
  test_main_placements_preflight_roto_sella_run_propia: `1 failed`.
- Cero perfiles con el raise dentro del `try` ->
  test_cero_perfiles_sella_una_sola_vez_y_avisa_fallando: `1 failed` (el `except` de fase API
  reintentaba el sello y moria con el warning `sello tambien fallo`; el segundo sello no dejaba
  fila, asi que el test caza el warning via `caplog`, no el conteo).
