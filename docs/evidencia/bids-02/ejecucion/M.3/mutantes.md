# Mutantes de M.3: los 3 obligatorios, todos muertos

Método: aplicar el mutante (una línea) al código, correr su prueba
focalizada, observar el rojo con el diff predicho, revertir. 3/3 muertos,
0 sobrevivientes.

## Tabla

| # | Mutante | Prueba que lo mata | Rojo observado |
|---|---|---|---|
| 1 | clave ausente enciende (`return None` → `return POLITICA_BID_VIGENTE` en `politica_bid_desde_settings`) | `test_ciclo_clave_ausente_no_emite_bids_cuenta_politica_apagada` | `{'dato_faltante': 1}` en vez de `{'politica_apagada': 1}` (la hoja entra al motor sin clave) |
| 2 | valor desconocido sigue (`raise ValueError` → `return None`) | `test_ciclo_politica_desconocida_falla_cerrado` | `Failed: DID NOT RAISE ValueError` |
| 3 | regreso al final (`return 0` → `return 9` en `prioridad_bajo_cupo`) | `test_aplica_bids_cupo_tres_descarta_heredado_aunque_mas_gasto` | orden `[3, 2, 1, 4]` (heredado antes que regreso) en vez de `[4, 3, 2, 1]` |

Comando por mutante (ejemplo 3): aplicar el cambio, correr
`pytest -q tests/test_apply.py::test_aplica_bids_cupo_tres_descarta_heredado_aunque_mas_gasto`,
observar `1 failed`, revertir. Todos revertidos; el árbol queda con el
código sano y la suite en verde (salvo el pre-existente `test_jev_catalogo`,
que falla igual sin M.3).
