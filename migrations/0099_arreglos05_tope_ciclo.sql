-- sonda-aplicada: SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.decision_sin_aplicar'::regclass AND conname = 'decision_sin_aplicar_motivo_check') THEN false ELSE EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.decision_sin_aplicar'::regclass AND conname = 'decision_sin_aplicar_motivo_check' AND pg_get_constraintdef(oid) LIKE '%tope_ciclo%') END
-- 0099 (ARREGLOS 05 H.2, diseno seccion «H. Tope de 150 pujas ordinarias»):
-- el vocabulario cerrado de `decision_sin_aplicar` admite `tope_ciclo`.
--
-- Por que hace falta DDL: el tope de 150 pujas ordinarias por ciclo de origen
-- y plataforma se cuenta del ledger `apply_attempt` (no hay tabla ni contador
-- nuevo), pero la decision que no se envia necesita un desenlace AUDITABLE y
-- ese vocabulario es un CHECK cerrado (espejo de
-- app.apply_cuota.MOTIVOS_SIN_APLICAR). Sin este motivo, la 151 quedaria como
-- huerfana de un ciclo live cerrado o se reescribiria historia.
--
-- Que NO cambia:
--   1) No hay tabla, columna, indice ni contador nuevo: la fuente del cupo es
--      el join `apply_attempt -> decision -> optimizer_cycle`, con
--      `tipo='normal'` y `kind='bid'`, excluidas solo la entrada/regreso de
--      TEMPORADA por su marcador congelado.
--   2) No se toca `apply_attempt` ni `optimizer_cycle`: los intentos y los
--      ciclos quedan como estan.
--   3) Permisos: `app_decide` ya tiene INSERT en `decision_sin_aplicar`; no se
--      amplia ningun grant.
--
-- Consulta previa (solo lectura, 2026-11-25 04:34 UTC, rol orbit_read, via
-- docs/evidencia/arreglos-01/lee_prod.sh; salida en
-- docs/evidencia/arreglos-05/H/previa.txt): 211 ciclos (179 live+done, 30
-- shadow+done, 2 off+skipped); 100 ciclos con intentos bid normales, 503
-- intentos, maximo 45 por ciclo (mediana 2, p90 10) y CERO ciclos por encima de
-- 50; cero intentos ambiguos; cero decisiones de entrada/regreso de temporada
-- con intento; 11 reversas (no cuentan); motivos en uso `modo_no_live` (190) y
-- `fuera_de_cap` (3). Ningun dato historico cambia y no hay backfill.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0099_reversa_arreglos05_tope_ciclo.sql.

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
            '0099: se esperaba el CHECK nombrado decision_sin_aplicar_motivo_check';
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

DO $$
DECLARE
    definicion TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO definicion
      FROM pg_constraint
     WHERE conrelid = 'decision_sin_aplicar'::regclass
       AND conname = 'decision_sin_aplicar_motivo_check';
    IF definicion IS NULL OR definicion NOT LIKE '%tope_ciclo%' THEN
        RAISE EXCEPTION '0099: el CHECK de motivos no admite tope_ciclo';
    END IF;
    IF definicion NOT LIKE '%vigencia_vencida%'
       OR definicion NOT LIKE '%bid_cambiado_fuera%'
       OR definicion NOT LIKE '%espera_proteccion%' THEN
        RAISE EXCEPTION '0099: el CHECK perdio motivos previos';
    END IF;
    IF NOT has_table_privilege('app_decide', 'public.decision_sin_aplicar', 'INSERT') THEN
        RAISE EXCEPTION '0099: app_decide debe registrar el desenlace tope_ciclo';
    END IF;
END $$;
