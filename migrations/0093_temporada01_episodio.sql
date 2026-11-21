-- sonda-aplicada: SELECT CASE WHEN to_regclass('public.temporada_episodio') IS NULL THEN false ELSE has_table_privilege('app_decide', 'public.temporada_episodio', 'INSERT') AND has_table_privilege('app_decide', 'public.temporada_fin_reduccion', 'INSERT') AND pg_get_viewdef('public.v_cambio_bid'::regclass) LIKE '%temporada%' AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('public.decision_sin_aplicar') AND conname = 'decision_sin_aplicar_motivo_check' AND pg_get_constraintdef(oid) LIKE '%bid_cambiado_fuera%') END
-- 0093 (TEMPORADA 01 B.1, diseno «Migracion»): la identidad durable de cada
-- temporada baja. Cuatro tablas nuevas y el origen `temporada` en v_cambio_bid:
--
-- 1. v_cambio_bid se redefine desde la definicion de 0067 (tres ramas) y
--    AGREGA AL FINAL (CREATE OR REPLACE VIEW solo deja agregar columnas al
--    final) motivo_temporada (entra/ajusta/sale) y episodio_id; las ramas 2
--    (regreso_del_dueno) y 3 (ajuste_de_campana) dan NULL, NULL. R1 no ve los
--    movimientos de temporada (origen propio), piso_aprendido no los cuenta,
--    «Keywords danadas» no los mete en su racha y los frenos no los juzgan.
--    La consulta previa (diseño, comentario abajo) da 0 filas antes de
--    desplegar: los motivos nacen en este plan, ninguna trayectoria historica
--    cambia (regla de replay).
-- 2. temporada_episodio: una fila por baja de una plataforma; solo se cierra.
--    El indice unico deja UNA abierta por plataforma.
-- 3. temporada_efecto: la decision de temporada de una hoja mientras no tiene
--    desenlace. El indice unico deja UNA abierta por hoja.
-- 3b. temporada_fin_reduccion: el fin de una reduccion sin enviar nada
--     (ya_arriba o mano del dueno). Append-only: un fin no revive.
-- 4. temporada_exclusion: la mano de David vista por el aplicador o por TX3,
--     con su bid visto y moneda cuando los hay. Append-only.
-- 5. decision_sin_aplicar admite `bid_cambiado_fuera` (el mismo DROP/ADD
--    CONSTRAINT de 0081); MOTIVOS_SIN_APLICAR lo suma en el mismo bloque y
--    test_apply_schema exige que coincidan.
-- 6. Permisos minimos: app_decide inserta y cierra (UPDATE acotado a las
--    columnas de cierre); app_read/app_admin leen.
--
-- Consulta previa (0 filas antes de desplegar; los motivos nacen en este plan):
--   SELECT count(*) FROM decision
--    WHERE inputs->>'motivo' IN ('entra_temporada_baja','ajusta_temporada_baja','sale_temporada_baja');
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0093_reversa_temporada01_episodio.sql (aborta si ya hubo episodios o
-- decisiones de temporada).

-- 1. Origen 'temporada'. Se redefine la vista de 0067 (0067:65-100); las dos
--    columnas nuevas van al final (CREATE OR REPLACE VIEW solo deja agregar
--    columnas al final); las ramas 2 y 3 agregan NULL, NULL.
CREATE OR REPLACE VIEW v_cambio_bid AS
SELECT d.ad_entity_id AS hoja_id,
       da.confirmed_at AS confirmado_el,
       d.old_value AS bid_antes,
       d.new_value AS bid_despues,
       d.value_currency AS moneda,
       CASE WHEN d.inputs->>'motivo' = 'regreso_por_desplome'
            THEN 'regreso_por_desplome'
            WHEN d.inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja',
                                         'sale_temporada_baja')
            THEN 'temporada'
            ELSE 'motor' END AS origen,
       d.id AS decision_id,
       CASE d.inputs->>'motivo' WHEN 'entra_temporada_baja' THEN 'entra'
                                WHEN 'ajusta_temporada_baja' THEN 'ajusta'
                                WHEN 'sale_temporada_baja' THEN 'sale' END AS motivo_temporada,
       (d.inputs->>'temporada_episodio_id')::bigint AS episodio_id
  FROM decision_application da
  JOIN decision d         ON d.id = da.decision_id AND d.kind = 'bid'
  JOIN optimizer_cycle oc ON oc.id = da.applied_cycle_id AND oc.mode = 'live'
 WHERE da.verify_ok IS TRUE
   AND d.old_value IS NOT NULL AND d.new_value IS NOT NULL AND d.new_value <> d.old_value
UNION ALL
SELECT d.ad_entity_id,
       a.finished_at,
       NULL::numeric,
       d.old_value,
       d.value_currency,
       'regreso_del_dueno',
       d.id,
       NULL::text,
       NULL::bigint
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id AND d.kind = 'bid'
 WHERE a.tipo = 'reversa' AND a.resultado = 'ok'
UNION ALL
SELECT h.hoja_id,
       a.confirmado_el,
       h.current_bid,
       h.current_bid,
       h.bid_currency,
       'ajuste_de_campana',
       NULL::bigint,
       NULL::text,
       NULL::bigint
  FROM campana_ajuste a
  JOIN v_hoja_activa h ON h.campana_id = a.campana_id AND h.current_bid IS NOT NULL
 WHERE a.confirmado_el IS NOT NULL AND a.clase = 'ajuste_ubicacion';

-- 2. Episodio: uno abierto por plataforma; solo se cierra.
CREATE TABLE temporada_episodio (
    id                 BIGSERIAL PRIMARY KEY,
    platform           platform    NOT NULL,
    abierto_el         TIMESTAMPTZ NOT NULL,
    abierto_en_ciclo   BIGINT      NOT NULL REFERENCES optimizer_cycle(id),
    config_version_id  BIGINT      NOT NULL REFERENCES config_version(id),
    cerrado_el         TIMESTAMPTZ NULL,
    cerrado_en_ciclo   BIGINT      NULL REFERENCES optimizer_cycle(id),
    CHECK ((cerrado_el IS NULL) = (cerrado_en_ciclo IS NULL))
);
CREATE UNIQUE INDEX temporada_episodio_uno_abierto ON temporada_episodio (platform)
    WHERE cerrado_el IS NULL;

-- 3. Efecto: la decision de temporada de una hoja mientras no tiene desenlace.
--    Uno abierto por hoja (indice unico; un choque es bug y el ciclo falla cerrado).
CREATE TABLE temporada_efecto (
    id            BIGSERIAL PRIMARY KEY,
    episodio_id   BIGINT      NOT NULL REFERENCES temporada_episodio(id),
    ad_entity_id  BIGINT      NOT NULL REFERENCES ad_entity(id),
    decision_id   BIGINT      NOT NULL UNIQUE REFERENCES decision(id),
    tipo          TEXT        NOT NULL CHECK (tipo IN ('entra', 'ajusta', 'sale')),
    abierto_el    TIMESTAMPTZ NOT NULL DEFAULT now(),
    cerrado_el    TIMESTAMPTZ NULL,
    desenlace     TEXT        NULL CHECK (desenlace IN
                      ('confirmado', 'divergencia', 'sin_aplicar', 'mano_del_dueno', 'abandonado')),
    CHECK ((cerrado_el IS NULL) = (desenlace IS NULL))
);
CREATE UNIQUE INDEX temporada_efecto_uno_pendiente ON temporada_efecto (ad_entity_id)
    WHERE cerrado_el IS NULL;

-- 3b. Fin de una reduccion sin enviar nada (ya_arriba o mano del dueno).
--     Append-only: un fin no revive (cadena.reduccion omite las reducciones
--     con fila aqui via HistoriaBid.fines congelado).
CREATE TABLE temporada_fin_reduccion (
    episodio_id   BIGINT      NOT NULL REFERENCES temporada_episodio(id),
    ad_entity_id  BIGINT      NOT NULL REFERENCES ad_entity(id),
    motivo        TEXT        NOT NULL CHECK (motivo IN ('ya_arriba', 'mano_del_dueno')),
    cycle_id      BIGINT      NOT NULL,
    cerrado_el    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (episodio_id, ad_entity_id)
);

-- 4. Exclusion durable: la mano de David vista por el aplicador (bid leido
--    distinto) o por TX3 (mano sin fecha vista hoy en baja). Append-only.
CREATE TABLE temporada_exclusion (
    episodio_id   BIGINT      NOT NULL REFERENCES temporada_episodio(id),
    ad_entity_id  BIGINT      NOT NULL REFERENCES ad_entity(id),
    motivo        TEXT        NOT NULL CHECK (motivo IN ('mano_del_dueno', 'divergencia')),
    bid_visto     NUMERIC(14,4) NULL,
    bid_visto_moneda currency   NULL,
    decision_id   BIGINT      NULL REFERENCES decision(id),
    detectada_el  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (episodio_id, ad_entity_id),
    CHECK ((bid_visto IS NULL) = (bid_visto_moneda IS NULL))
);

-- Triggers solo-cierre en episodio y efecto (patron de 0069/0078: el unico
-- UPDATE valido pasa cerrado_el de NULL a un valor; search_path fijo).
CREATE FUNCTION temporada_episodio_0093_solo_cierre() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.platform IS DISTINCT FROM NEW.platform
       OR OLD.abierto_el IS DISTINCT FROM NEW.abierto_el
       OR OLD.abierto_en_ciclo IS DISTINCT FROM NEW.abierto_en_ciclo
       OR OLD.config_version_id IS DISTINCT FROM NEW.config_version_id
       OR OLD.cerrado_el IS NOT NULL
       OR NEW.cerrado_el IS NULL
       OR NEW.cerrado_en_ciclo IS NULL THEN
        RAISE EXCEPTION
            'temporada_episodio: SOLO-CIERRE: el unico UPDATE valido cierra '
            'cerrado_el y cerrado_en_ciclo de un episodio abierto, sin tocar lo demas'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER temporada_episodio_update_solo_cierre
    BEFORE UPDATE ON temporada_episodio
    FOR EACH ROW EXECUTE FUNCTION temporada_episodio_0093_solo_cierre();

CREATE FUNCTION temporada_efecto_0093_solo_cierre() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.episodio_id IS DISTINCT FROM NEW.episodio_id
       OR OLD.ad_entity_id IS DISTINCT FROM NEW.ad_entity_id
       OR OLD.decision_id IS DISTINCT FROM NEW.decision_id
       OR OLD.tipo IS DISTINCT FROM NEW.tipo
       OR OLD.abierto_el IS DISTINCT FROM NEW.abierto_el
       OR OLD.cerrado_el IS NOT DISTINCT FROM NEW.cerrado_el
       OR OLD.desenlace IS NOT DISTINCT FROM NEW.desenlace
       OR OLD.cerrado_el IS NOT NULL THEN
        RAISE EXCEPTION
            'temporada_efecto: SOLO-CIERRE: el unico UPDATE valido cierra '
            'cerrado_el y desenlace de un efecto abierto, sin tocar lo demas'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER temporada_efecto_update_solo_cierre
    BEFORE UPDATE ON temporada_efecto
    FOR EACH ROW EXECUTE FUNCTION temporada_efecto_0093_solo_cierre();

-- Las dos tablas de fin y exclusion son append-only (patron 0081).
CREATE TRIGGER temporada_fin_reduccion_append_only
    BEFORE UPDATE OR DELETE ON temporada_fin_reduccion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER temporada_fin_reduccion_append_only_truncate
    BEFORE TRUNCATE ON temporada_fin_reduccion
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER temporada_exclusion_append_only
    BEFORE UPDATE OR DELETE ON temporada_exclusion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER temporada_exclusion_append_only_truncate
    BEFORE TRUNCATE ON temporada_exclusion
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- 5. decision_sin_aplicar admite 'bid_cambiado_fuera' al final del vocabulario
--    (espejo de app.apply_cuota.MOTIVOS_SIN_APLICAR, mismo orden; el mismo
--    DROP/ADD CONSTRAINT de 0081).
DO $$
DECLARE
    candidatos TEXT[];
BEGIN
    SELECT array_agg(conname) INTO candidatos
      FROM pg_constraint
     WHERE conrelid = 'decision_sin_aplicar'::regclass
       AND contype = 'c'
       AND conname = 'decision_sin_aplicar_motivo_check';
    IF candidatos IS NULL OR array_length(candidatos, 1) <> 1 THEN
        RAISE EXCEPTION
            '0093: se esperaba el CHECK nombrado decision_sin_aplicar_motivo_check';
    END IF;
    EXECUTE $check$
ALTER TABLE decision_sin_aplicar DROP CONSTRAINT decision_sin_aplicar_motivo_check
$check$;
    EXECUTE $check$
ALTER TABLE decision_sin_aplicar ADD CONSTRAINT decision_sin_aplicar_motivo_check
    CHECK (motivo IN (
        'modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decisora',
        'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota',
        'espera_target', 'sin_respuesta', 'perdida', 'choque_clave',
        'espera_proteccion', 'bid_cambiado_fuera'
    ))
$check$;
END $$;

-- 6. Permisos minimos.
GRANT SELECT, INSERT ON temporada_episodio, temporada_efecto, temporada_exclusion, temporada_fin_reduccion TO app_decide;
GRANT UPDATE (cerrado_el, cerrado_en_ciclo) ON temporada_episodio TO app_decide;
GRANT UPDATE (cerrado_el, desenlace) ON temporada_efecto TO app_decide;
GRANT USAGE ON SEQUENCE temporada_episodio_id_seq, temporada_efecto_id_seq TO app_decide;
GRANT SELECT ON temporada_episodio, temporada_efecto, temporada_exclusion, temporada_fin_reduccion TO app_read, app_admin;

COMMENT ON TABLE temporada_episodio IS
    'TEMPORADA 01 B (0093): una fila por baja de una plataforma. Se abre en '
    'TX3 el primer dia de baja (temporada_io.registra_ciclo, UNICO escritor) '
    'y se cierra el primer dia que la fase deja de ser baja, o cuando el '
    'episodio abierto es de otra baja. Solo-cierre: una baja no se reabre.';
COMMENT ON TABLE temporada_efecto IS
    'TEMPORADA 01 B (0093): la decision de temporada (entra/ajusta/sale) de '
    'una hoja mientras no tiene desenlace. El cierre vive en la MISMA '
    'transaccion que decision_application (_confirma_resumen) o que '
    'decision_sin_aplicar; la reconciliacion de temporada_io.registra_ciclo '
    'cierra como abandonado lo que un crash dejo sin intento. Indice unico '
    'temporada_efecto_uno_pendiente: un choque es bug y el ciclo falla cerrado.';
COMMENT ON TABLE temporada_fin_reduccion IS
    'TEMPORADA 01 B (0093): el fin de una reduccion de temporada SIN enviar '
    'nada: ya_arriba (el bid vigente ya llego a su nivel de regreso) o '
    'mano_del_dueno (la cadena o el bid vigente dicen que el dueño movio). '
    'Append-only: un fin guardado no revive (HistoriaBid.fines lo omite).';
COMMENT ON TABLE temporada_exclusion IS
    'TEMPORADA 01 B (0093): la mano de David en un episodio, vista por el '
    'aplicador (divergencia con bid leido, o mano_del_dueno con '
    'bid_cambiado_fuera) o por TX3 (mano sin fecha vista hoy en baja, con el '
    'bid visto). Con bid_visto la mano queda FECHADA: en la siguiente baja ya '
    'es vieja y la entrada si sale (plan B.2, casos 2 y 7). Append-only.';
COMMENT ON CONSTRAINT decision_sin_aplicar_motivo_check ON decision_sin_aplicar IS
    'Vocabulario cerrado, espejo de app.apply_cuota.MOTIVOS_SIN_APLICAR (mismo '
    'orden). 0093 agrega bid_cambiado_fuera: la lectura fresca antes del PUT '
    '(bloque E) encontro en Amazon un bid distinto del que Orbit vio al '
    'decidir; no se envia nada y la hoja queda excluida del episodio.';

DO $$
BEGIN
    IF NOT has_table_privilege('app_decide', 'temporada_episodio', 'INSERT')
       OR NOT has_table_privilege('app_decide', 'temporada_efecto', 'INSERT')
       OR NOT has_table_privilege('app_decide', 'temporada_exclusion', 'INSERT')
       OR NOT has_table_privilege('app_decide', 'temporada_fin_reduccion', 'INSERT')
       OR NOT has_column_privilege('app_decide', 'temporada_episodio', 'cerrado_el', 'UPDATE')
       OR NOT has_column_privilege('app_decide', 'temporada_episodio', 'cerrado_en_ciclo', 'UPDATE')
       OR NOT has_column_privilege('app_decide', 'temporada_efecto', 'cerrado_el', 'UPDATE')
       OR NOT has_column_privilege('app_decide', 'temporada_efecto', 'desenlace', 'UPDATE')
       OR NOT has_sequence_privilege('app_decide', 'temporada_episodio_id_seq', 'USAGE')
       OR NOT has_sequence_privilege('app_decide', 'temporada_efecto_id_seq', 'USAGE')
       OR NOT has_table_privilege('app_read', 'temporada_episodio', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'temporada_efecto', 'SELECT')
       OR has_table_privilege('app_read', 'temporada_episodio', 'INSERT')
       OR has_table_privilege('app_decide', 'temporada_exclusion', 'UPDATE')
       OR has_table_privilege('app_decide', 'temporada_fin_reduccion', 'UPDATE')
       OR has_table_privilege('app_ingest', 'temporada_episodio', 'INSERT') THEN
        RAISE EXCEPTION '0093: privilegios de temporada invalidos';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'temporada_episodio'::regclass
                   AND tgname = 'temporada_episodio_update_solo_cierre' AND NOT tgisinternal)
       OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'temporada_efecto'::regclass
                      AND tgname = 'temporada_efecto_update_solo_cierre' AND NOT tgisinternal)
       OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'temporada_exclusion'::regclass
                      AND tgname = 'temporada_exclusion_append_only' AND NOT tgisinternal)
       OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'temporada_fin_reduccion'::regclass
                      AND tgname = 'temporada_fin_reduccion_append_only_truncate' AND NOT tgisinternal) THEN
        RAISE EXCEPTION '0093: triggers de temporada invalidos';
    END IF;
    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_name = 'v_cambio_bid'
           AND column_name IN ('motivo_temporada', 'episodio_id')) <> 2 THEN
        RAISE EXCEPTION '0093: v_cambio_bid sin las columnas de temporada';
    END IF;
END
$$;
