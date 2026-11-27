-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0105 (ADS PROTECCION P03, pausa economica v2).
--
-- Guarda Regla 5: la reversa JAMAS borra historia util.
--   - Aborta si existe alguna fila `modo = 'live'` en
--     `pausa_economica_evaluacion`: hay decisiones live auditadas que se
--     perderian al borrar la tabla.
--   - Aborta si alguna fila de `decision_sin_aplicar` quedo esperando por
--     `espera_reciente_economica`: hay trabajo real en vuelo.
--
-- Si no hay datos de ninguno de los dos tipos:
--   1) restaura `decision_sin_aplicar_motivo_check` EXACTA de la 0099
--      (16 motivos, sin `espera_reciente_economica`);
--   2) `DROP TABLE IF EXISTS pausa_economica_evaluacion` (triggers e indice
--      caen con ella).
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). Re-runnable (IF EXISTS / to_regclass).
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF to_regclass('public.pausa_economica_evaluacion') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM pausa_economica_evaluacion WHERE modo = 'live') THEN
            RAISE EXCEPTION
                '0105 reversa: hay filas live en pausa_economica_evaluacion; '
                'borrarlas perderia la auditoria de decisiones (regla 5)';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM decision_sin_aplicar WHERE motivo = 'espera_reciente_economica'
    ) THEN
        RAISE EXCEPTION
            '0105 reversa: hay pausas economicas esperando por su ventana '
            'reciente; resolverlas antes de revertir (regla 5)';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'decision_sin_aplicar'::regclass
           AND conname = 'decision_sin_aplicar_motivo_check'
    ) THEN
        EXECUTE $check$
ALTER TABLE decision_sin_aplicar DROP CONSTRAINT decision_sin_aplicar_motivo_check
$check$;
    END IF;
    EXECUTE $check$
ALTER TABLE decision_sin_aplicar ADD CONSTRAINT decision_sin_aplicar_motivo_check
    CHECK (motivo IN (
        'modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decidora',
        'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota',
        'espera_target', 'sin_respuesta', 'perdida', 'choque_clave',
        'espera_proteccion', 'bid_cambiado_fuera', 'vigencia_vencida',
        'tope_ciclo'
    ))
$check$;
END $$;

COMMENT ON CONSTRAINT decision_sin_aplicar_motivo_check ON decision_sin_aplicar IS
    'Vocabulario cerrado, espejo de app.apply_cuota.MOTIVOS_SIN_APLICAR (mismo '
    'orden). 0099 agrega tope_ciclo: el ciclo de origen ya intento sus 150 '
    'pujas ordinarias (count del ledger apply_attempt, tipo normal y kind bid, '
    'excluidas solo la entrada y el regreso de TEMPORADA por su marcador '
    'congelado). La decision no se envia, no gasta cupo diario y no abre otro '
    'ciclo para eludir el tope. Las reversas no cuentan.';

DROP TABLE IF EXISTS pausa_economica_evaluacion;
