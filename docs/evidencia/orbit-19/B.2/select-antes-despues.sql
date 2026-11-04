-- ORBIT 19 / B.2 — SELECT antes/despues de 0021 sobre v_margen_producto.
-- Corre contra la DB temporal que siembra tests/test_economia_observada.py
-- (_semilla: maduro 70d, muestra 5d, rota 5d fee-sin-tipo, negativo 40d,
-- un_dia 1 venta). El mismo SELECT corre ANTES (migraciones hasta 0019) y
-- DESPUES de aplicar 0021; la regla dura exige filas identicas.

-- Columnas comparadas (identidad fila por fila, ORDER BY product_id):
SELECT product_id,
       dias_con_venta,
       margen_neto_pct,
       venta_total,
       cobertura,
       moneda
  FROM v_margen_producto
 ORDER BY product_id;

-- Valores esperados del fixture (ambas corridas):
--   maduro : 70 dias, margen 50.0, venta 7000, cobertura 1, MXN
--   muestra: 5 dias,  margen NULL (< 30), venta 500
--   rota   : 5 dias,  margen NULL (fee sin tipo)
--   neg    : 40 dias, margen -20.0 (negativo conservado)
--   un_dia : 1 dia,   margen NULL (< 30), venta 100

-- Despues de 0021, la vista nueva se verifica con:
SELECT platform, product_id, margen_neto_pct, integridad_ok, muestra_limitada,
       muestra_venta, muestra_margen_neto_pct, moneda
  FROM v_economia_producto
 ORDER BY product_id;
