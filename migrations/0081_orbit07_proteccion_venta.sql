-- ORBIT 07 M1: proteccion de una venta reciente con vencimiento fijo.
--
-- Un episodio por (plataforma, hoja, version, ancla). El ancla es la fecha
-- `metric_date` mas antigua con pedido en D-9..D-1 y el vencimiento es
-- ancla + 10 dias UTC. La tabla es APPEND-ONLY: cerrar un episodio no escribe,
-- se deriva de `vence_el` y de la ventana madura. El trigger fija la relacion
-- temporal en la base (nunca un CHECK dependiente de la TimeZone de sesion).
--
-- `decision_sin_aplicar` recibe el motivo `espera_proteccion` al final del
-- vocabulario (espejo de app.apply.MOTIVOS_SIN_APLICAR, mismo orden).
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0081_reversa_orbit07_proteccion_venta.sql (aborta si ya hay episodios).

CREATE TABLE proteccion_venta_reciente (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform     platform    NOT NULL,
    ad_entity_id BIGINT      NOT NULL REFERENCES ad_entity(id),
    version      TEXT        NOT NULL,
    ancla        DATE        NOT NULL,
    vence_el     DATE        NOT NULL,
    abierta_el   TIMESTAMPTZ NOT NULL,
    observado_el TIMESTAMPTZ NOT NULL,
    CONSTRAINT proteccion_venta_unica
        UNIQUE (platform, ad_entity_id, version, ancla),
    CONSTRAINT proteccion_venta_no_futuro
        CHECK (observado_el <= abierta_el)
);

CREATE INDEX proteccion_venta_entidad_idx
    ON proteccion_venta_reciente (platform, ad_entity_id, version, ancla DESC);

COMMENT ON TABLE proteccion_venta_reciente IS
    'ORBIT 07 M1: episodio de proteccion por venta reciente. Append-only. '
    'ancla = metric_date mas antigua con pedido en D-9..D-1; vence_el = '
    'ancla + 10 dias UTC. El codigo anterior ignora esta tabla; la semantica '
    'historica se conserva porque la ausencia del marcador en los insumos '
    'congelados no activa la subpolitica.';

COMMENT ON COLUMN proteccion_venta_reciente.abierta_el IS
    'Instante de la decision que abrio el episodio (reloj decidido_at).';

COMMENT ON COLUMN proteccion_venta_reciente.observado_el IS
    'Observacion mas reciente del dato que motivo el episodio, clampeada a '
    'abierta_el (mismo sello bitemporal que data_observed_at).';

CREATE FUNCTION proteccion_venta_vence_fijo() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.vence_el <> NEW.ancla + 10 THEN
        RAISE EXCEPTION
            'proteccion_venta_reciente: vence_el % no es ancla + 10 dias (%)',
            NEW.vence_el, NEW.ancla + 10
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION proteccion_venta_vence_fijo IS
    'El vencimiento es DATO del episodio, no se recalcula al leer: si la '
    'reobservacion moviera el ancla, la proteccion se renovaria sin fin. '
    'Aritmetica de DATE (inmutable), sin depender de la TimeZone de sesion.';

CREATE TRIGGER proteccion_venta_vence_fijo
    BEFORE INSERT ON proteccion_venta_reciente
    FOR EACH ROW EXECUTE FUNCTION proteccion_venta_vence_fijo();

CREATE TRIGGER proteccion_venta_append_only
    BEFORE UPDATE OR DELETE ON proteccion_venta_reciente
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER proteccion_venta_append_only_truncate
    BEFORE TRUNCATE ON proteccion_venta_reciente
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

GRANT SELECT ON proteccion_venta_reciente TO app_read, app_admin, app_decide;
GRANT INSERT ON proteccion_venta_reciente TO app_decide;
GRANT USAGE ON SEQUENCE proteccion_venta_reciente_id_seq TO app_decide;

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
            '0081: se esperaba el CHECK nombrado decision_sin_aplicar_motivo_check';
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
        'espera_proteccion'
    ))
$check$;
END $$;

COMMENT ON CONSTRAINT decision_sin_aplicar_motivo_check ON decision_sin_aplicar IS
    'Vocabulario cerrado, espejo de app.apply.MOTIVOS_SIN_APLICAR (mismo '
    'orden). 0081 agrega espera_proteccion: la fila de pausa por cero ventas '
    'espera el vencimiento del episodio de venta reciente sin cobrar cuota, '
    'sin entrar a applying y sin reiniciar el veto de 48 horas.';
