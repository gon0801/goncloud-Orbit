# Clave privada: mantenibilidad

- Rejuega sin regresion todos los vectores congelados de `codigo`.
- `goal_enabled=false` pisa cualquier evidencia y explica `goal_deshabilitado`.
- `goal_enabled=true` conserva la decision normal.
- La ausencia del campo conserva compatibilidad historica.
- Integra el opt-out en un punto claro, sin duplicar toda la funcion ni cambiar
  el protocolo o los motivos anteriores.
