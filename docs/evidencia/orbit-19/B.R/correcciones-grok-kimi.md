# ORBIT 19: correcciones del cross review Grok/Kimi

Base revisada: `87fd9d6`, PR #195. Grok emitio APPROVE con observaciones;
Kimi emitio REQUEST_CHANGES. El lead reprodujo los dos P2 y los acepto:
auditoria de descartes y error atrasado del comparador. Tambien se corrige
el P3 compartido de consultas duplicadas.

## Dato real antes de las pruebas

SELECT de solo lectura en produccion: ultima ingesta #116, 351 filas escritas
y 828 descartadas por agregacion. No se afirmo que esa corrida tuviera
filas corruptas; los casos de error se reproducen con fixtures sin red ni DB.

## Regresiones RED contra el codigo anterior

Se agregaron las pruebas ANTES de modificar las funciones:

- `test_descartes_de_clave_invalida_cuentan_todas_las_filas`: 8 fallos y
  4 controles correctos. Ejemplo literal: `assert (1 + 1) == 3`.
  Cubre filas vacias, no numericas, ventas/compras inconsistentes, ambos
  ordenes, varias filas fusionadas, reintentos y otra clave independiente.
- `test_flujo_js_comparador_etiquetas_orden_filtro_y_cero_escrituras`:
  `AssertionError: El fallo antiguo no borra la comparacion al filtrar`;
  la tabla resultaba vacia tras una respuesta nueva correcta seguida del
  fallo de la consulta antigua.
- `test_flujo_js_comparador_recibe_objetivo_manual_y_derivado`:
  `AssertionError: El cambio de origen hace un solo GET`, `6 !== 5`.
  La prueba tambien simula el input manual y su burbujeo al formulario.

## Cambios

- Al retirar una fila ya planeada, se cuenta un descarte. Los aportes
  absorbidos previamente por fusion ya estaban contados y no se duplican.
  Se mantiene `filas = rows_written + rows_skipped`.
- Una respuesta fallida atrasada sale antes de tocar el estado o los
  datos del comparador. Un fallo vigente sigue borrando los datos y
  mostrando su error.
- Invalidacion y cambios de objetivo comparten un temporizador. Una
  consulta inmediata cancela la recarga pendiente.

## GREEN local

- `pytest -q tests/test_ads_producto_ingesta.py`: **27 passed**.
- `pytest -q tests/test_ui_fabrica.py`: **13 passed**, una advertencia
  preexistente de Starlette/httpx.
- Ruff y `node --check app/static/js/fabrica.js`: correctos.

La bateria completa se ejecuta en CI del nuevo SHA del PR #195; no se
duplica localmente. Esta correccion no modifica esquema, configuracion
productiva ni campanas reales. Stock manual y Featured Offer sin verificar
siguen siendo limitaciones declaradas.
