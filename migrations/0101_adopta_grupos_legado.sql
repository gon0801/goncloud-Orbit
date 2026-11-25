-- sonda-aplicada: SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'campana_grupo'::regclass AND conname = 'campana_grupo_lote_segun_tipo')
-- 0101: adopcion unica de grupos legado de campanas existentes (AC, AGM2M, AGMX, ASEM y USPerNog).
--
-- Los cinco grupos fueron creados antes de FABRICA 01 y operan activos en
-- produccion (todos de tipo_producto = 'kit_arras'). Sus campanas pausadas
-- (AU2 Exact con 75.6% ACoS, USPerNog Phrase con 153.3% ACoS, USPerNog
-- Broad/PT vacias y ASEM PT sin ventas con pujas de 25 MXN) quedan fuera.
--
-- Esquema:
--   - Amplia campana_grupo.tipo a ('fabrica5', 'impulso', 'legado').
--   - Permite campana_grupo.lote IS NULL unicamente cuando tipo = 'legado'
--     (fabrica5 e impulso siguen exigiendo lote NOT NULL que referencia fabrica_lote).
--   - Conserva sin cambios campana_grupo_objetivo_v2, triggers de 0018/0038/0064/0072
--     y privilegios de los cuatro roles.
--
-- Adopcion de datos:
--   - Resuelve cada campana y ad group por (platform, kind, external_id), nunca
--     por ad_entity.id interno.
--   - Si 0 roles del grupo existen en ad_entity (base de pruebas vacia), omite el
--     grupo sin error; si existen solo parte de los roles declarados, aborta
--     fail-closed en lugar de crear un grupo a medias.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh corre en una transaccion (-1).
-- Reversa: 0101_reversa_adopta_grupos_legado.sql.

ALTER TABLE campana_grupo
    DROP CONSTRAINT campana_grupo_tipo_check,
    ADD CONSTRAINT campana_grupo_tipo_check
        CHECK (tipo IN ('fabrica5', 'impulso', 'legado')),
    ALTER COLUMN lote DROP NOT NULL,
    ADD CONSTRAINT campana_grupo_lote_segun_tipo
        CHECK (
            (tipo = 'legado' AND lote IS NULL)
            OR (tipo <> 'legado' AND lote IS NOT NULL)
        );

COMMENT ON COLUMN campana_grupo.tipo IS
    'fabrica5 (grupo de cinco de la fabrica), impulso (grupo de dos del dueno) o legado (grupo existente adoptado una unica vez sin fabrica_lote).';

COMMENT ON CONSTRAINT campana_grupo_lote_segun_tipo ON campana_grupo IS
    'Solo los grupos tipo legado carecen de fabrica_lote; fabrica5 e impulso siguen exigiendo lote NOT NULL.';

DO $$
DECLARE
    g RECORD;
    v_esperados INT;
    v_encontrados INT;
    v_ya_en_grupo INT;
    v_grupo_id BIGINT;
BEGIN
    CREATE TEMP TABLE _grupos_legado_def (
        grupo_clave TEXT NOT NULL,
        platform    platform NOT NULL,
        rol         campana_rol NOT NULL,
        camp_ext    TEXT NOT NULL,
        ag_ext      TEXT NOT NULL
    ) ON COMMIT DROP;

    INSERT INTO _grupos_legado_def (grupo_clave, platform, rol, camp_ext, ag_ext) VALUES
        -- AC (amazon_mx): 4 roles activos (sin category_broad)
        ('AC',       'amazon_mx', 'category_exact',    '36419758709592',  '553629449717842'),
        ('AC',       'amazon_mx', 'auto_discovery',    '6725297929896',   '449528234398547'),
        ('AC',       'amazon_mx', 'category_phrase',   '1928166522818',   '422648909272683'),
        ('AC',       'amazon_mx', 'product_targeting', '170680510319588', '292842646731052'),
        -- AGM2M (amazon_mx): 4 roles activos (sin category_broad)
        ('AGM2M',    'amazon_mx', 'category_exact',    '150524793905747', '231642806323471'),
        ('AGM2M',    'amazon_mx', 'auto_discovery',    '186754400178437', '478068950298503'),
        ('AGM2M',    'amazon_mx', 'category_phrase',   '95461755558026',  '100861701218603'),
        ('AGM2M',    'amazon_mx', 'product_targeting', '104269918219765', '168634033266847'),
        -- AGMX (amazon_mx): 4 roles activos (sin category_broad)
        ('AGMX',     'amazon_mx', 'category_exact',    '77497572464498',  '420478206173588'),
        ('AGMX',     'amazon_mx', 'auto_discovery',    '138540322850772', '476942043430531'),
        ('AGMX',     'amazon_mx', 'category_phrase',   '79296609147301',  '353798003957642'),
        ('AGMX',     'amazon_mx', 'product_targeting', '103896791234989', '509300461895675'),
        -- ASEM (amazon_mx): 3 roles activos (product_targeting pausada queda fuera)
        ('ASEM',     'amazon_mx', 'category_exact',    '240839688795909', '436018660372463'),
        ('ASEM',     'amazon_mx', 'auto_discovery',    '26722751353851',  '561594597563774'),
        ('ASEM',     'amazon_mx', 'category_phrase',   '183722893302577', '398439659915475'),
        -- USPerNog (amazon_us): 2 roles activos (phrase/broad/pt pausadas quedan fuera)
        ('USPerNog', 'amazon_us', 'category_exact',    '251723662158466', '522582072501798'),
        ('USPerNog', 'amazon_us', 'auto_discovery',    '140602818838686', '529927551250133');

    FOR g IN
        SELECT DISTINCT grupo_clave, platform
          FROM _grupos_legado_def
         ORDER BY platform, grupo_clave
    LOOP
        SELECT count(*)
          INTO v_esperados
          FROM _grupos_legado_def d
         WHERE d.grupo_clave = g.grupo_clave AND d.platform = g.platform;

        SELECT count(*)
          INTO v_encontrados
          FROM _grupos_legado_def d
          JOIN ad_entity c
            ON c.platform = d.platform AND c.kind = 'campaign' AND c.external_id = d.camp_ext
          JOIN ad_entity ag
            ON ag.platform = d.platform AND ag.kind = 'ad_group'
           AND ag.external_id = d.ag_ext AND ag.parent_id = c.id
         WHERE d.grupo_clave = g.grupo_clave AND d.platform = g.platform;

        IF v_encontrados = 0 THEN
            CONTINUE;
        END IF;

        IF v_encontrados <> v_esperados THEN
            RAISE EXCEPTION
                '0101: grupo legado % (%) incompleto en ad_entity (% de % pares campana/ad_group)',
                g.grupo_clave, g.platform, v_encontrados, v_esperados;
        END IF;

        SELECT count(*)
          INTO v_ya_en_grupo
          FROM _grupos_legado_def d
          JOIN ad_entity c
            ON c.platform = d.platform AND c.kind = 'campaign' AND c.external_id = d.camp_ext
          JOIN campana_grupo_rol r
            ON r.ad_entity_id = c.id
         WHERE d.grupo_clave = g.grupo_clave AND d.platform = g.platform;

        IF v_ya_en_grupo > 0 THEN
            RAISE EXCEPTION
                '0101: grupo legado % (%) ya tiene % campanas en campana_grupo_rol',
                g.grupo_clave, g.platform, v_ya_en_grupo;
        END IF;

        INSERT INTO campana_grupo (
            platform,
            tipo_producto,
            nombre_base,
            lote,
            target_acos_pct,
            target_derivado_pct,
            fraccion,
            target_origen,
            target_procedencia,
            go_literal,
            tipo
        ) VALUES (
            g.platform,
            'kit_arras',
            g.grupo_clave,
            NULL,
            20.00,
            NULL,
            NULL,
            'manual_lanzamiento',
            'adopcion unica de grupo legado pre-fabrica (2026-11-25)',
            'adopcion unica grupos legado 2026-11-25',
            'legado'
        )
        RETURNING id INTO v_grupo_id;

        INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)
        SELECT v_grupo_id, d.rol, c.id, ag.id
          FROM _grupos_legado_def d
          JOIN ad_entity c
            ON c.platform = d.platform AND c.kind = 'campaign' AND c.external_id = d.camp_ext
          JOIN ad_entity ag
            ON ag.platform = d.platform AND ag.kind = 'ad_group'
           AND ag.external_id = d.ag_ext AND ag.parent_id = c.id
         WHERE d.grupo_clave = g.grupo_clave AND d.platform = g.platform
         ORDER BY d.rol;
    END LOOP;

    IF NOT has_table_privilege('app_read', 'campana_grupo', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'campana_grupo', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'campana_grupo', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'campana_grupo', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'campana_grupo', 'INSERT')
       OR NOT has_table_privilege('app_admin', 'campana_grupo', 'UPDATE')
       OR has_table_privilege('app_read', 'campana_grupo', 'INSERT')
       OR has_table_privilege('app_decide', 'campana_grupo', 'INSERT') THEN
        RAISE EXCEPTION '0101: privilegios de campana_grupo alterados';
    END IF;
END;
$$;
