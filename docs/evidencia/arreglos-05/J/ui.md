# UI de Productos en fase 2

- `orbit-verify` no se ejecuto en esta sesion Windows: el helper exige
  `.venv/bin/python` POSIX y `/proc`, y el WSL disponible no tiene Postgres 16
  escuchando en `127.0.0.1:5432` ni sudo no interactivo para `start.sh`. No se
  inventa un pase.
- Equivalente ejecutado sobre `7cc88976`: la regresion de render
  `test_arreglos05_j_ui_productos_no_anuncia_la_meta_en_practica` y
  `tests/test_ui.py -k productos` dan 4 passed. La plantilla real se comprobo
  contra produccion en `produccion.txt`:
  - la frase retirada "se calcula y no cambia ninguna puja" aparece 0 veces;
  - `Meta por producto` y `Pausa por producto` siguen;
  - la meta esta `en vivo` y la pausa esta `en practica`;
  - Settings ofrece solo `niveles_v3` y `apagado`;
  - `productos-modo --help` ofrece `--meta {off,live}` y
    `--pausa {off,shadow,live}`.
