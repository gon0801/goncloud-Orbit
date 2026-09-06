-- ORBIT 19 A.1 -- v2 por publicacion. Es expansiva: los lotes v1 permanecen legibles.

ALTER TABLE campana_grupo
    ADD COLUMN target_origen TEXT NOT NULL DEFAULT 'margen_medido'
        CHECK (target_origen IN ('margen_medido', 'manual_lanzamiento'));

ALTER TABLE campana_grupo
    ALTER COLUMN target_derivado_pct DROP NOT NULL,
    ALTER COLUMN fraccion DROP NOT NULL;

ALTER TABLE campana_grupo
    ADD CONSTRAINT campana_grupo_objetivo_v2
    CHECK (
        (target_origen = 'margen_medido' AND target_derivado_pct IS NOT NULL AND fraccion IS NOT NULL)
        OR
        (target_origen = 'manual_lanzamiento' AND target_derivado_pct IS NULL AND fraccion IS NULL)
    );

ALTER TABLE campana_grupo_producto
    DROP CONSTRAINT campana_grupo_producto_pkey,
    ALTER COLUMN margen_neto_pct DROP NOT NULL,
    ADD PRIMARY KEY (grupo_id, listing_id),
    ADD CONSTRAINT campana_grupo_producto_seller_sku_unico UNIQUE (grupo_id, seller_sku);

COMMENT ON COLUMN campana_grupo.target_origen IS
    'ORBIT 19 A.1: margen_medido conserva fraccion+derivado; manual_lanzamiento los deja NULL.';
COMMENT ON COLUMN campana_grupo_producto.margen_neto_pct IS
    'ORBIT 19 A.1: NULL significa margen no medido, nunca cero inventado.';
COMMENT ON CONSTRAINT campana_grupo_producto_seller_sku_unico ON campana_grupo_producto IS
    'ORBIT 19 A.1: un seller_sku solo puede generar un product ad por grupo.';
