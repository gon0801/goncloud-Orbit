-- Sonda base de AUTO-03 (B.4): los dos hechos que deben seguir valiendo
-- antes de que el bloque C instale el peldano margen_producto. Cada SELECT
-- devuelve una fila t|<nota> o f|<nota>; la tabla vacia tambien da t.
SELECT CASE WHEN NOT COALESCE((
         SELECT (settings ? 'ads_producto_meta_amazon_mx')
             OR (settings ? 'ads_producto_meta_amazon_us')
             OR (settings ? 'ads_producto_pausa_amazon_mx')
             OR (settings ? 'ads_producto_pausa_amazon_us')
           FROM config_version
          ORDER BY id DESC
          LIMIT 1), false)
       THEN 't' ELSE 'f' END
  || '|ninguna clave ads_producto_* en la config vigente';

SELECT CASE WHEN NOT EXISTS (
         SELECT 1
           FROM pg_constraint
          WHERE conname = 'target_acos_ciclo_procedencia_check'
            AND pg_get_constraintdef(oid) LIKE '%margen_producto%')
       THEN 't' ELSE 'f' END
  || '|el CHECK de procedencia aun no trae margen_producto';
