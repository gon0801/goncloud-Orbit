-- ADS D.1b: el vocabulario de decision_sin_aplicar se amplia con
-- 'choque_clave' (ADS D.1b): la decision de corte live cuyo INSERT choco la
-- clave de efecto en vuelo no deja fila en la cola; si su goal quedo en
-- shadow dentro de un ciclo live y no se registra, v_decision_huerfana la
-- listaria como 'huerfana' sin serlo.
--
-- El CHECK de 0044 nacio ANONIMO (columna inline, sin nombre): se localiza
-- en pg_constraint (contype 'c'), se ABORTA si no hay exactamente uno y se
-- re-crea NOMBRADO `decision_sin_aplicar_motivo_check` con la lista
-- ampliada. La lista es el ESPEJO de app.apply.MOTIVOS_SIN_APLICAR en el
-- MISMO orden (el test estatico y el de la constraint viva de
-- test_apply_schema la mantienen sincronizada): los 11 motivos de 0044 y
-- 'choque_clave' AL FINAL. No cambia GRANTs: la tabla ya existe y un
-- constraint no altera privilegios.
BEGIN;

DO $$
DECLARE
    candidatos TEXT[];
BEGIN
    SELECT array_agg(conname) INTO candidatos
      FROM pg_constraint
     WHERE conrelid = 'decision_sin_aplicar'::regclass AND contype = 'c';
    IF candidatos IS NULL OR array_length(candidatos, 1) <> 1 THEN
        RAISE EXCEPTION
            '0045: se esperaba EXACTAMENTE un CHECK en decision_sin_aplicar, hay %',
            COALESCE(array_length(candidatos, 1), 0);
    END IF;
    EXECUTE format('ALTER TABLE decision_sin_aplicar DROP CONSTRAINT %I', candidatos[1]);
    EXECUTE $check$
ALTER TABLE decision_sin_aplicar ADD CONSTRAINT decision_sin_aplicar_motivo_check
    CHECK (motivo IN (
        'modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decisora',
        'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota',
        'espera_target', 'sin_respuesta', 'perdida', 'choque_clave'
    ))
$check$;
END $$;

COMMENT ON CONSTRAINT decision_sin_aplicar_motivo_check ON decision_sin_aplicar IS
  'Vocabulario cerrado, espejo de app.apply.MOTIVOS_SIN_APLICAR (mismo '
  'orden). 0045 re-creo con nombre el CHECK anonimo de 0044 para agregar '
  'choque_clave: la decision de corte live cuyo INSERT choco la clave de '
  'efecto en vuelo (sin fila en la cola; sin registro, '
  'v_decision_huerfana la listaria como huerfana). perdida sigue aceptandose '
  'por las filas historicas, pero libera_vencidos ya no la escribe. '
  'ya_aplicada existe para el espejo pero nunca se escribe (esa decision ya '
  'tiene decision_application).';

COMMIT;
