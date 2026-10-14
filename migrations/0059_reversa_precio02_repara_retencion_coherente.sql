-- Restaura el cuerpo roto de la 0054 a proposito: es la reversa, no el fix.
BEGIN;
CREATE OR REPLACE FUNCTION precio_retencion_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_resultado TEXT;
    v_canal estimacion_canal;
    v_platform platform;
    v_decision_date DATE;
BEGIN
    SELECT d.resultado, d.canal, d.platform, d.decision_date
      INTO v_resultado, v_canal, v_platform, v_decision_date
      FROM precio_decision d WHERE d.id = NEW.decision_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'precio_retencion: la decision % no existe', NEW.decision_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_resultado NOT IN ('subir', 'bajar') THEN
        RAISE EXCEPTION
            'precio_retencion: la decision % es % y solo una subir/bajar se '
            'retiene (retener no reescribe la decision).', NEW.decision_id,
            v_resultado USING ERRCODE = 'check_violation';
    END IF;
    IF (v_platform, v_decision_date) IS DISTINCT FROM (
        SELECT c.platform, c.fecha FROM precio_corrida c WHERE c.id = NEW.corrida_id
    ) THEN
        RAISE EXCEPTION
            'precio_retencion: la decision % y la corrida % no son de la '
            'misma plataforma y el mismo dia.', NEW.decision_id, NEW.corrida_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.causa IN ('movimiento_masivo', 'insumo_sistemico') THEN
        IF NOT EXISTS (
            SELECT 1 FROM precio_compuerta_medicion m
             WHERE m.corrida_id = NEW.corrida_id
               AND m.canal = v_canal
               AND m.causa = NEW.causa
        ) THEN
            RAISE EXCEPTION
                'precio_retencion: la causa % exige la medicion de esa corrida '
                'y ese canal.', NEW.causa USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
COMMIT;
