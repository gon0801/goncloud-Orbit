-- 0065 (BIDS 02 I.3, seccion 5): impulso, impulso_lectura y el estado
-- 'pausado' de fabrica_lote.
--
-- Un impulso = un producto = un grupo de DOS roles. La fila impulso es
-- inmutable; el estado se DERIVA de la ultima lectura y del estado del
-- lote (v_impulso_estado). impulso_lectura es append-only con clave
-- (impulso_id, corte): la misma corrida del vigia dos veces no duplica.
-- (Bloque "0062" de docs/evidencia/bids-02/diseno/datos.sql; el numero
-- real es 0065.)
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, con su
-- reversa en 0065_reversa_bids02_impulso.sql. El CHECK de estado de
-- fabrica_lote se reescribe con 'pausado' (confirmado con SELECT a
-- pg_constraint: fabrica_lote_estado_check).

BEGIN;

CREATE TABLE impulso (
    id                 BIGSERIAL PRIMARY KEY,
    platform           platform    NOT NULL,
    listing_id         BIGINT      NOT NULL REFERENCES listing (id),
    product_id         BIGINT      NOT NULL REFERENCES product (id),
    grupo_id           BIGINT      NOT NULL UNIQUE REFERENCES campana_grupo (id),
    lote               TEXT        NOT NULL UNIQUE REFERENCES fabrica_lote (lote),
    subfamilia         TEXT        NOT NULL,
    tope_monto         money_amount NOT NULL,
    tope_moneda        currency    NOT NULL,
    presupuesto_diario money_amount NOT NULL,
    plazo_dias         INTEGER     NOT NULL,
    lanzado_el         TIMESTAMPTZ NOT NULL,
    actor              TEXT        NOT NULL,
    go_literal         TEXT        NOT NULL,
    CONSTRAINT impulso_tope_positivo  CHECK (tope_monto > 0 AND presupuesto_diario > 0 AND plazo_dias > 0),
    CONSTRAINT impulso_tope_cabe      CHECK (presupuesto_diario <= tope_monto)
);

CREATE TABLE impulso_lectura (
    impulso_id      BIGINT      NOT NULL REFERENCES impulso (id),
    corte           DATE        NOT NULL,
    leido_el        TIMESTAMPTZ NOT NULL,
    dias_observados INTEGER     NOT NULL,
    impresiones     BIGINT,
    clics           BIGINT,
    gasto           money_amount,
    pedidos         BIGINT,
    veredicto       TEXT        NOT NULL,
    es_final        BOOLEAN     NOT NULL,
    hecho           TEXT        NOT NULL,
    CONSTRAINT lectura_veredicto CHECK (veredicto IN
        ('en_curso', 'vende', 'clics_sin_ventas', 'impresiones_sin_clics', 'sin_impresiones')),
    CONSTRAINT lectura_hecho CHECK (hecho IN ('nada', 'pausado', 'graduado', 'retiro_de_bolsas')),
    CONSTRAINT lectura_en_curso_no_final CHECK (NOT (veredicto = 'en_curso' AND es_final)),
    PRIMARY KEY (impulso_id, corte)
);

CREATE VIEW v_impulso_estado AS
SELECT i.*,
       l.corte, l.dias_observados, l.impresiones, l.clics, l.gasto, l.pedidos,
       l.veredicto, l.es_final,
       f.estado AS estado_lote
  FROM impulso i
  JOIN fabrica_lote f ON f.lote = i.lote
  LEFT JOIN LATERAL (
        SELECT * FROM impulso_lectura x WHERE x.impulso_id = i.id ORDER BY x.corte DESC LIMIT 1
  ) l ON TRUE;

-- Trigger (no CHECK): la moneda del tope es la de la plataforma, y el
-- listing es de ese producto y plataforma.
CREATE FUNCTION impulso_coherente() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.platform = 'amazon_mx' AND NEW.tope_moneda <> 'MXN')
       OR (NEW.platform = 'amazon_us' AND NEW.tope_moneda <> 'USD') THEN
        RAISE EXCEPTION 'impulso %: tope en % no es la moneda de %',
            NEW.id, NEW.tope_moneda, NEW.platform;
    END IF;
    PERFORM 1 FROM listing l
     WHERE l.id = NEW.listing_id
       AND l.product_id = NEW.product_id
       AND l.platform = NEW.platform;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'impulso: listing % no es del producto % en %',
            NEW.listing_id, NEW.product_id, NEW.platform;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER impulso_coherente
    BEFORE INSERT OR UPDATE ON impulso
    FOR EACH ROW EXECUTE FUNCTION impulso_coherente();

-- La fabrica pausa y reanuda las campanas de un lote sin darlo por
-- desarmado: 'pausado' es reversible, 'desarmado' sigue terminal.
ALTER TABLE fabrica_lote DROP CONSTRAINT fabrica_lote_estado_check;
ALTER TABLE fabrica_lote ADD CONSTRAINT fabrica_lote_estado_check
    CHECK (estado IN ('planeado', 'applied', 'failed', 'pausado', 'desarmado'));

COMMENT ON TABLE impulso IS
    'BIDS 02 I.3: un producto, un grupo de dos, un tope. Fila inmutable; el estado se deriva.';
COMMENT ON TABLE impulso_lectura IS
    'BIDS 02 I.3: lectura diaria del vigia, append-only, clave (impulso_id, corte).';

-- La fila impulso es inmutable y la lectura es append-only: nadie hace
-- UPDATE ni DELETE sobre las dos tablas (trigger, no solo GRANT).
CREATE FUNCTION impulso_inmutable() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'impulso es inmutable: % no permitido en %', TG_OP, TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER impulso_sin_update_delete
    BEFORE UPDATE OR DELETE ON impulso
    FOR EACH ROW EXECUTE FUNCTION impulso_inmutable();

CREATE TRIGGER impulso_lectura_sin_update_delete
    BEFORE UPDATE OR DELETE ON impulso_lectura
    FOR EACH ROW EXECUTE FUNCTION impulso_inmutable();

GRANT SELECT, INSERT ON impulso, impulso_lectura TO app_admin;
GRANT SELECT ON impulso, impulso_lectura TO app_read;
REVOKE SELECT ON impulso, impulso_lectura FROM app_ingest, app_decide;
GRANT USAGE, SELECT ON SEQUENCE impulso_id_seq TO app_admin;

DO $$
BEGIN
    IF NOT has_table_privilege('app_admin', 'impulso', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'impulso', 'INSERT')
       OR NOT has_table_privilege('app_admin', 'impulso_lectura', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'impulso_lectura', 'INSERT')
       OR NOT has_table_privilege('app_read', 'impulso', 'SELECT')
       OR NOT has_table_privilege('app_read', 'impulso_lectura', 'SELECT')
       OR has_table_privilege('app_read', 'impulso', 'INSERT')
       OR has_table_privilege('app_read', 'impulso_lectura', 'INSERT')
       OR has_table_privilege('app_decide', 'impulso', 'SELECT')
       OR has_table_privilege('app_ingest', 'impulso_lectura', 'SELECT')
       OR has_table_privilege('app_admin', 'impulso', 'UPDATE')
       OR has_table_privilege('app_admin', 'impulso_lectura', 'DELETE') THEN
        RAISE EXCEPTION '0065: privilegios de impulso(_lectura) distintos de los declarados';
    END IF;
END;
$$;

COMMIT;
