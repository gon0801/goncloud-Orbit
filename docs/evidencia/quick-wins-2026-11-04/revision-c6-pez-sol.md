# Revision C6-P1/P2 y PEZ — GPT-6.1 Sol

Dictamen independiente: **APPROVE de la evidencia**, con gate C6 incompleto.
Fecha: 5-nov-2026 UTC, 4-nov America/Vancouver. Se revisaron codigo de lectura,
resultados, limites y fuentes; no hubo escrituras productivas del revisor.

- P1: inventario verificable. SQL READ ONLY / REPEATABLE READ, timeout 15s,
  rol orbit_read y redactor. Once hashes no acreditan SHA global ni aislamiento.
- P2: fuente del replay embebida identica a la version controlada. Formula y
  ventanas congeladas, target y moneda coherentes. Confirmados 48 cruces;
  33 cohortes con horizonte cumplido, 368/912 pares fecha/cohorte revisados y
  ninguna cohorte con revision completa. No acredita FP=0.
- Contraste Amazon: 62/62 pares de dinero coincidentes con el reporte externo
  existente. Su rango solo cubre 22/30 fechas de una cohorte; no es una
  conciliacion de todas las ventanas ni del vintage +10d.
- La ventana por entidad es el contrato vigente; antiguedad individual no
  equivale a fallo de frescura global. El flag historico OFF explica lo real,
  no demuestra elegibilidad con ON. Solo dos cruces tienen evidencia individual
  del camino de decision; los otros 46 mantienen desconocidos historicos.
- PEZ: identidad comercial confirmada; tres IDs exactos archivados por SKU
  y costo observado cero. Costo utilizable sin respaldo = None. Candidatos
  activos de otras referencias quedan separados; no existe lote financiero
  seguro. XML-RPC limitado a lectura, configuracion en memoria y sin secretos
  en artefactos. Venta confirmada no necesita nueva confirmacion.

La nota menor que confundia cero observado con dato ausente fue corregida.
No hay bloqueantes de seguridad o datos en esta entrega. Se aprueba publicar
la investigacion y sus limites, no iniciar sombra, activar proteccion ni
modificar productos/costos de Odoo. El resultado CI corresponde al head final
del PR; no se repitio la suite desde el revisor.
