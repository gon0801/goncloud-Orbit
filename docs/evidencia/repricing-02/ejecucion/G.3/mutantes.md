# Mutantes G.3 (uno por prueba nueva; el plan no lista obligatorios)

## M1 — margen sin pasar a tanto por uno

- Prueba: `test_g3_precios_unidades_traen_margen_goal_objetivo_origen_y_tacos`.
- Mutante (`app/api_precios.py`, `_unidades_precio`): `"margen_hoy":
  _dec_str(m_pct)` en vez de `_dec_str(m_pct / 100)`.
- Rojo: `AssertionError: assert '24.0000' == '0.2400'`
  (1 failed, 86 deselected).
- Revertido; la prueba vuelve a verde.

## M2 — TACoS sin signo de por ciento

- Prueba: `test_g3_precios_html_tabla_goals_marca_origen_y_seguridad`.
- Mutante (`app/templates/_precios_goals.html`, columna TACoS):
  `{{ u.tacos | dinero_ui }}` en vez de
  `{{ u.tacos | dinero_ui }} %`.
- Rojo: `assert '10.00 %' in '<!DOCTYPE html>...'` (1 failed).
- Revertido; la prueba vuelve a verde.
