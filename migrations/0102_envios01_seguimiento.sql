-- sonda-aplicada: SELECT to_regclass('public.envio_observation') IS NOT NULL AND to_regclass('public.envio_paquete_observation') IS NOT NULL AND to_regclass('public.v_envio_actual') IS NOT NULL AND to_regclass('public.envio_observation_actual_idx') IS NOT NULL AND to_regprocedure('public.envio_tiempo_coherente()') IS NOT NULL
-- =============================================================================
--  ENVIOS 01 - seguimiento de envios (Amazon MX/US y Mercado Libre)
--
--  Dos tablas append-only con hechos de la fuente: sin campos derivados (el
--  estado unificado lo calcula app/envios/estado.py), sin montos y sin PII. El
--  unico escritor es app/envios/store.py, que inserta solo cuando cambia la
--  huella del envio.
--
--  Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
--  transaccion (psql -1). No re-ejecutable. Reversa:
--  0102_reversa_envios01_seguimiento.sql.
-- =============================================================================

CREATE TABLE envio_observation (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform              platform    NOT NULL,
    envio_ref             TEXT        NOT NULL,
    pedidos               TEXT[]      NOT NULL,
    pack_ref              TEXT,
    logistica_fuente      TEXT,
    estado_fuente         TEXT,
    subestado_fuente      TEXT,
    pedido_creado_el      TIMESTAMPTZ,
    fuente_actualizado_el TIMESTAMPTZ,
    despachar_antes_de    TIMESTAMPTZ,
    entrega_prometida_el  TIMESTAMPTZ,
    entrega_limite_el     TIMESTAMPTZ,
    entregado_el          TIMESTAMPTZ,
    devuelto_el           TIMESTAMPTZ,
    articulos             JSONB       NOT NULL,
    huella                TEXT        NOT NULL,
    observed_at           TIMESTAMPTZ NOT NULL,
    ingest_run_id         BIGINT      NOT NULL REFERENCES ingest_run (id),
    UNIQUE (platform, envio_ref, observed_at),
    CONSTRAINT envio_con_pedidos
        CHECK (cardinality(pedidos) >= 1 AND array_position(pedidos, NULL) IS NULL),
    CONSTRAINT envio_articulos_arreglo
        CHECK (jsonb_typeof(articulos) = 'array'),
    CONSTRAINT envio_huella_sha256
        CHECK (huella ~ '^[0-9a-f]{64}$')
);

CREATE INDEX envio_observation_actual_idx
    ON envio_observation (platform, envio_ref, observed_at DESC, id DESC);

CREATE TABLE envio_paquete_observation (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    envio_obs_id   BIGINT NOT NULL REFERENCES envio_observation (id),
    paquete_ref    TEXT   NOT NULL CHECK (paquete_ref <> ''),
    transportista  TEXT,
    servicio       TEXT,
    guia           TEXT,
    despachado_el  TIMESTAMPTZ,
    estado_fuente  TEXT,
    detalle_fuente TEXT,
    UNIQUE (envio_obs_id, paquete_ref)
);

CREATE INDEX envio_paquete_por_envio ON envio_paquete_observation (envio_obs_id);

-- Invariante de tiempo en trigger con comparacion absoluta, sin now(). MeLi
-- queda fuera por spec: sus dos fechas vienen de recursos distintos.
CREATE FUNCTION envio_tiempo_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.platform IN ('amazon_mx', 'amazon_us')
        AND NEW.pedido_creado_el > NEW.fuente_actualizado_el THEN
        RAISE EXCEPTION
            'envio_observation: pedido_creado_el (%) posterior a fuente_actualizado_el (%)',
            NEW.pedido_creado_el, NEW.fuente_actualizado_el
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER envio_tiempo_coherente
    BEFORE INSERT ON envio_observation
    FOR EACH ROW EXECUTE FUNCTION envio_tiempo_coherente();

CREATE TRIGGER envio_observation_append_only
    BEFORE UPDATE OR DELETE ON envio_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER envio_observation_sin_truncate
    BEFORE TRUNCATE ON envio_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER envio_paquete_append_only
    BEFORE UPDATE OR DELETE ON envio_paquete_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER envio_paquete_sin_truncate
    BEFORE TRUNCATE ON envio_paquete_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE VIEW v_envio_actual AS
SELECT DISTINCT ON (platform, envio_ref) *
  FROM envio_observation
 ORDER BY platform, envio_ref, observed_at DESC, id DESC;

COMMENT ON TABLE envio_observation IS
    'ENVIOS 01: hechos de un envio segun la fuente, append-only. Una fila nueva '
    'solo cuando cambia la huella (escritor unico app/envios/store.py). Sin '
    'PII, sin montos y sin estado derivado.';
COMMENT ON COLUMN envio_observation.envio_ref IS
    'Amazon: orderId. Mercado Libre: shipment.id como texto.';
COMMENT ON COLUMN envio_observation.fuente_actualizado_el IS
    'Fecha de actualizacion de la fuente al escribir la fila; no entra en la '
    'huella, asi que no avanza si solo ella cambia.';
COMMENT ON COLUMN envio_observation.articulos IS
    'Arreglo de objetos {pedido_ref, sku, titulo, cantidad, producto_ref}.';
COMMENT ON COLUMN envio_observation.huella IS
    'sha256 de los hechos del envio y sus paquetes (app/envios/modelo.py).';
COMMENT ON TABLE envio_paquete_observation IS
    'ENVIOS 01: paquetes de una observacion de envio, append-only.';
COMMENT ON VIEW v_envio_actual IS
    'ENVIOS 01: ultima observacion por (platform, envio_ref).';

GRANT SELECT ON envio_observation, envio_paquete_observation, v_envio_actual
    TO app_read, app_ingest, app_admin;
GRANT INSERT ON envio_observation, envio_paquete_observation TO app_ingest;
GRANT USAGE ON SEQUENCE envio_observation_id_seq, envio_paquete_observation_id_seq
    TO app_ingest;
-- El ALTER DEFAULT PRIVILEGES de 0001 da SELECT a app_decide; ninguna decision
-- lee envios.
REVOKE ALL ON envio_observation, envio_paquete_observation, v_envio_actual
    FROM app_decide;
