# S.5: revision (autorrevison + candados automaticos)

Sin herramientas de delegacion en este contexto no hubo hijos Muse ni
comment-sicko ni panel GLM por `opencode run`: deslop/no-comments se
aplicaron a mano sobre el diff, igual que en el panel de S.4. La revision
cruzada del loop (revisor + cross-review) corre sobre el PR, fuera de este
bloque.

Autorevision del diff (tres archivos de app, +436/-4):

Aplicados:

- Plazas por clave en `_vistas_de_claves` (psycopg no expande `IN %s`;
  cazado por la prueba de punta a punta antes del verde).
- `PropuestaEnVeto` se importa en `jev_salud` solo por su anotacion de
  `elegidas`; verificado que ruff no lo marca sin uso.
- `_ilegibles` de `_propuestas` se descarta con nombre: la pantalla no
  necesita la lista del job.
- `s.ordenes = 0` (no `IS NOT DISTINCT`): los NULL quedan fuera, que es lo
  correcto: dato faltante no es cero ventas.
- `date`/`datetime` en `_como_fecha`/`_como_instante`: psycopg devuelve
  DATE como `date` e INSTANT como `datetime`; el `str` cubre dicts
  escritos a mano en pruebas.

No se tocan (fuera del alcance "puro contrato sin plantillas"):

- `app/api_dashboard.py` y `app/ui.py`: ningun endpoint ni plantilla
  cambia en S.5. El cableado de `/cortes` (fila Senal) y
  `/gasto-sin-venta` es trabajo posterior con sus propias pruebas.
- `app/jev_senales.py`: el job no se mueve; `jev_salud` reutiliza su
  `_propuestas` (la MISMA consulta, una sola definicion).
- Sin migracion: la fila ya trae todo lo que la pantalla pinta, salvo
  `otros_sin_venta`, que ninguna pantalla lee y `leer` ignora (fijado en
  prueba). No se agrega columna por un dato que no decide.

Candados automaticos (todos en verde en el SHA del PR):

- `ruff check app/ tests/`: limpio.
- `ruff format --check` en los 4 archivos del paso: limpio.
- Guarda de pureza (`test_modulo_puro_sin_red_ni_db_en_top_level` para
  `jev_lectura.py`/`jev_vista.py`): verde con `json` y `typing` nuevos
  (ambos en la lista blanca).
- Guarda de imports Jev (`test_jev_solo_importa_lo_declarado`): verde;
  nada fuera de `jev_*` importa lo nuevo.
- Presupuesto de tamano: `jev_lectura.py` en 796 de 900, sin allowlist.
