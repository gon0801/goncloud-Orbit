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

-- Revision PR #240: fulfillment.fulfillmentStatus (dominio de ENVIO) NO se
-- mezcla en order_status (dominio del CICLO del pedido); vive en su propia
-- columna para que cada una conserve un vocabulario.
ALTER TABLE spapi_order_observation
    ADD COLUMN fulfillment_status TEXT;

COMMENT ON COLUMN spapi_order_observation.order_status IS
    'A.2b: SOLO la clave plana orderStatus/OrderStatus del resumen (dominio '
    'del CICLO del pedido, v0). El estado de envio va en fulfillment_status; '
    'nunca se mezclan.';

COMMENT ON COLUMN spapi_order_observation.fulfillment_status IS
    'A.2b revision: SOLO fulfillment.fulfillmentStatus de la seccion '
    'FULFILLMENT (dominio de ENVIO). NULL si la seccion no vino.';

COMMENT ON COLUMN spapi_order_observation.fulfillment_channel IS
    'A.2b: la seccion FULFILLMENT manda (fulfillment.fulfilledBy, quien '
    'cumple, analogo AFN/MFN de v0); de respaldo, la clave plana '
    'fulfillmentChannel/FulfillmentChannel del resumen.';

-- Revision PR #240: el COMMENT ON TABLE de 0030 describe la clave vieja
-- de 3 columnas; 0030 esta sellada, asi que aqui se refresca a la
-- bitemporal vigente.
COMMENT ON TABLE spapi_order_observation IS
    'SP-API 01 A.2/A.2b: resumenes searchOrders 2026-01-01 append-only por '
    '(platform, amazon_order_id, last_updated_time, observed_at) (clave '
    'bitemporal desde 0031; la de 3 columnas quedo en la historia). La '
    'ventana diaria usa lastUpdatedAfter = max(last_updated_time) - 1 dia '
    'de solape; la primera corrida usa createdAfter = ahora - 30 dias; el '
    'backfill usa --desde. Sin PII: sin columnas de comprador ni direccion.';

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
