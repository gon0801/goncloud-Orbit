-- Antes de C/D: ninguna clave ads_producto_* en la ultima config_version y el CHECK de
-- procedencia de target_acos_ciclo sin margen_producto.
SELECT CASE WHEN EXISTS (
         SELECT 1 FROM jsonb_object_keys(coalesce(
           (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1), '{}'::jsonb)) AS k
         WHERE k LIKE 'ads\_producto\_%')
       THEN 'f' ELSE 't' END
       || '|ultima config_version sin claves ads_producto_*';
SELECT coalesce(
         (SELECT CASE WHEN pg_get_constraintdef(oid) LIKE '%margen_producto%' THEN 'f' ELSE 't' END
            FROM pg_constraint WHERE conname = 'target_acos_ciclo_procedencia_check'),
         'f') || '|target_acos_ciclo_procedencia_check existe y no trae margen_producto';
