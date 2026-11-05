# P04: costos MeLi, diagnostico de solo lectura

Consulta: 2026-11-05T01:17:53.271511+00:00. PG con ORBIT_DSN_READ, transaccion READ ONLY y timeout 15 s; SQLite mode=ro/query_only; nueve GET vivos, sin refresh de tokens ni escrituras.

Las nueve filas no requieren completar el costo del SKU ancla: ya existe en Orbit y coincide con accounting. El bloqueo es una variante PEZ por publicacion con product_id NULL. Ninguna de las nueve PEZ existe en sku_costs, bom_headers ni meli_sku_mapping. No hay importe externo verificable para completarlas.

Siete publicaciones estan active y con stock; dos son under_review/paused. Ninguna es catalog_listing. Las nueve variantes identificadas como PEZ en listing_miembro tienen available_quantity=0 en el GET vivo de esta consulta (5-nov UTC), cotejado por variante_ext contra el id de cada variante en readback.json. La identidad de siete miembros fue vista el 4-nov; la de MLM2727437671 y MLM4763716652 conserva visto_at del 30-oct y esta inactiva. El GET actual no devuelve seller_sku para esas variantes; confirma el stock por id, sin renovar la identidad del miembro. El calculo exige todos los miembros activos: stock cero no equivale a miembro inactivo. El escenario incompleto protege la publicacion completa, aunque otras variantes sean vendibles.

| Publicacion | SKU faltante | Estado vivo / stock | Ultimo escenario | Costo ancla confirmado (MXN) |
|---|---|---|---|---|
| MLM2163407464 | NH-CAR-BLA-PEZ-DOR | active / 131 | 2026-11-04 | 304.65 |
| MLM1890653227 | NH-CAR-BLA-PEZ-PLA | active / 11 | 2026-11-04 | 304.65 |
| MLM4735255462 | NH-BLA-BRO-PEZ-DOR | active / 18 | 2026-11-04 | 353.69 |
| MLM2724304859 | NH-ITA-PEZ-PLA | active / 18 | 2026-11-04 | 341.0 |
| MLM2727437671 | NH-GAM-AZU-PEZ-PLA | under_review / 50 | 2026-10-30 | 325.0 |
| MLM2727363007 | NH-GAM-NEG-PEZ-DOR | active / 94 | 2026-11-04 | 325.0 |
| MLM2727328203 | NH-GAM-AZU-PEZ-DOR | active / 117 | 2026-11-04 | 325.0 |
| MLM4749600210 | NH-GAM-NEG-PEZ-PLA | active / 11 | 2026-11-04 | 325.0 |
| MLM4763716652 | NH-PLA-CH-PEZ-PLA | paused / 0 | 2026-10-30 | 372.0 |

## Impacto y siguiente paso

No es un quick win de carga de costos: requiere una decision del dueno de catalogo/Odoo sobre las nueve variantes PEZ. Si son productos reales, falta alta/mapeo y costo verificado en su fuente; si son variantes obsoletas, debe corregirse el catalogo por el camino autorizado con reversa. No sustituir PEZ por CEN, no copiar su costo y no excluir una variante solo por stock cero.

La parte completada es la investigacion: identidad, causa, costo ancla externo, estado vivo y separacion de siete activas frente a dos no vendibles. Las metas P04 no se revisaron ni modificaron; 45 goals live no demuestran nueve publicaciones elegibles.

## Evidencia y reproduccion

- `consulta.py` -> `orbit.json`: ultimos escenarios, miembros, publicaciones y corridas de costo. La ultima corrida global es OK con cero skips; no indica costo faltante para el ancla.
- `fuentes.py` -> `fuentes.json`: consultas externas acotadas a SKUs ancla y PEZ; importes originales como texto, moneda y vigencia. La vigencia abierta refleja lo registrado en accounting, sin afirmar una comprobacion nueva en Odoo.
- `readback.py` -> `readback.json`: GET /items/{id}, estado, stock, catalogo y variantes, con hora de lectura.
- `matriz.json`: matriz por publicacion. El costo PEZ y su moneda quedan null.

Ejecutar scripts PG/API con `ssh goncloud docker exec -i orbit-app-1 python - < SCRIPT`; fuentes con `ssh goncloud python3 - < fuentes.py`. No se necesita ni se imprime el DSN. Las lecturas no son un snapshot atomico entre sistemas. El cache bridge solo conserva dos de estos IDs, con fecha mayo, y no se usa como estado actual.

Causa del software: `app/estimacion_repository.py::_sembrar_meli` agrega costo_ausente cuando un miembro no tiene product_id. `listing.product_id` identifica el ancla; no define el costo de toda la publicacion. Los dos escenarios del 30-oct son historicos: los miembros actuales estan inactivos y el readback vivo confirma under_review/paused.
