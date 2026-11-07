-- Antes de D/F: ninguna clave ads_producto_* en la ultima config_version y ninguna puja con la
-- meta por producto. Vale antes y despues de la 0076 (C.1), que solo amplia el CHECK de
-- procedencia: la prueba-real de C corre despues de desplegarla.
SELECT CASE WHEN EXISTS (
         SELECT 1 FROM jsonb_object_keys(coalesce(
           (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1), '{}'::jsonb)) AS k
         WHERE k LIKE 'ads\_producto\_%')
       THEN 'f' ELSE 't' END
       || '|ultima config_version sin claves ads_producto_*';
SELECT CASE WHEN EXISTS (SELECT 1 FROM target_acos_ciclo WHERE procedencia = 'margen_producto')
       THEN 'f' ELSE 't' END
       || '|ninguna fila de target_acos_ciclo con procedencia margen_producto';
