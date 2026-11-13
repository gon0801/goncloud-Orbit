# Bloque en espera de decisión del dueño

Este bloque no está en `plans/arreglos-04.md`. Con la regla estricta del spec (semillas solo de anuncios del mismo producto), 248 de 249 productos de México y los 119 de Estados Unidos arrancarían sin semillas. Entra al plan cuando el dueño decida entre la regla estricta y conservar las semillas del grupo. Diseño: sección `## W-K` de `docs/evidencia/arreglos-04/DISENO.md`.

## Bloque W-K — Las campañas nuevas de un producto solo se siembran con sus propias búsquedas
lane: release
depende: W-J
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Las campañas nuevas de un producto solo se siembran con búsquedas que vendieron en anuncios de ese mismo producto; un NH- ya no hereda las del SET-. Casi ningún producto tiene anuncios propios, así que la mayoría arrancará sin semillas y la campaña automática las irá encontrando.
prueba-real: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-04/W-K/prueba.py
| Fila | Contenido | Acepta |
| --- | --- | --- |
| W-K.1 | Prueba (dinero: las semillas nacen como keywords con bid en Amazon), en `tests/test_fabrica_campanas.py`, `test_arreglos04_w_k_semillas_solo_de_ad_groups_del_producto`: un ad group con un NH- y un SET- y otro solo con el NH-; `_terminos_producto` del NH- trae solo las búsquedas del segundo, y el del SET- no trae ninguna. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_fabrica_campanas.py -k arreglos04_w_k_ |
| W-K.2 | `tools/fabrica_campanas.py`: `_SQL_TERMINOS` con las CTE `productos` y `origenes` de la sección W-K, `_terminos_producto` con los parámetros `(hoy, desde, hoy, hasta, listing_ids, platform, platform)`, y el comentario de `:112-126` con la regla nueva. `_SQL_TERMINOS_EXACT` sigue saliendo del `replace`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_fabrica_campanas.py tests/test_api_fabrica.py tests/test_fabrica_plan.py tests/test_impulso_io.py |
| W-K.3 | `app/static/js/fabrica.js:341`: el texto de «0 semillas» de la sección W-K cuando el plan no trae keywords ni ASINs; ficha `.cursor/skills/verify-orbit/features/fabrica.md` con ese texto; correr `orbit-verify` sobre la fábrica. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_ui_fabrica.py tests/test_verify_orbit.py |
| W-K.4 | Antes y después del desplegar, con `lee_prod.sh`, la consulta del anexo B del diseño. Guardar las dos salidas en `docs/evidencia/arreglos-04/W-K/semillas.txt`; la última columna debe coincidir con lo que da el `_SQL_TERMINOS` nuevo. | test -s docs/evidencia/arreglos-04/W-K/semillas.txt |
| W-K.5 | `docs/evidencia/arreglos-04/W-K/prueba.py` para `en_prod.sh`: `tools.fabrica_campanas._SQL_TERMINOS` no contiene «campanas AS» y contiene «origenes AS» con `bool_and`. | test -s docs/evidencia/arreglos-04/W-K/prueba.py |
