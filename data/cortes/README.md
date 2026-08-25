# Lista curada de negativos (CORTES 02)

`negativos-curados.csv` es **dato, no código**: términos de búsqueda que el
dueño aprobó cortar por **relevancia** (producto diferente), la dimensión que
la regla estadística de CORTES 01 deliberadamente no toca.

Reglas de esta lista:

- **Cada fila entra solo con aprobación explícita del dueño** (columna
  `aprobado_por` + `fecha_aprobacion`). La AI propone; jamás agrega sola.
- **Append-only**: las rondas de curaduría (cadencia 2-3×/semana) agregan
  filas nuevas; no se editan ni borran las existentes. Si un término se
  aprobó por error, se registra la reversa como decisión del dueño.
- `tipo`: `negative_exact` (keywords). Los ASIN aprobados entrarán como
  `negative_product_target` cuando se curen (pendiente: 475 candidatos).
- Se aplica a **todas las campañas activas de ambos marketplaces** (un
  negativo que nunca matchea no estorba).
- `gasto_hist_*` y `clicks_hist` son la evidencia al momento de aprobar
  (ventana D-90..D-10 del bootstrap), no se actualizan.

Origen: bootstrap del 2026-08-25 — clasificación AI de 2,713 términos
históricos, palomeada por el dueño (88 producto_diferente + "baño";
"souvenirs" quedó corriendo bajo la regla estadística).
