-- ---------------------------------------------------------------------------
-- SP-API 01 A.2b — pedidos con estado y total: clave bitemporal + vista.
--
-- La ventana diaria con solape vuelve a observar los pedidos recientes y
-- ahora trae mas datos (FULFILLMENT + PROCEEDS): la llave sin observed_at
-- congelaba la primera observacion (los 224 pedidos de las runs 140/141
-- quedarian en NULL para siempre, mismo bug que 0026/0027). Con
-- observed_at en la llave, cada corrida deja su fila; la ultima manda.
--
-- Solo DROP/ADD CONSTRAINT + CREATE VIEW. No toca ni borra filas.
-- No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

ALTER TABLE spapi_order_observation
    DROP CONSTRAINT spapi_order_clave_unica;
ALTER TABLE spapi_order_observation
    ADD CONSTRAINT spapi_order_clave_unica
    UNIQUE (platform, amazon_order_id, last_updated_time, observed_at);

COMMENT ON CONSTRAINT spapi_order_clave_unica ON spapi_order_observation IS
    'SP-API 01 A.2b: re-observacion diaria por pedido (append-only, patron '
    '0026/0027); el estado actual es la fila mas reciente, no la primera.';

-- Ultima observacion por (plataforma, pedido): lo que consume Fase B.
CREATE VIEW v_spapi_order_ultima AS
SELECT DISTINCT ON (platform, amazon_order_id) *
  FROM spapi_order_observation
 ORDER BY platform, amazon_order_id, observed_at DESC, id DESC;

COMMENT ON VIEW v_spapi_order_ultima IS
    'SP-API 01 A.2b: estado y total actuales por pedido (ultima '
    'observacion). Desempate por id para observed_at iguales.';

GRANT SELECT ON v_spapi_order_ultima TO app_read, app_ingest, app_decide, app_admin;

COMMIT;
