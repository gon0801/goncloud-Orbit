-- ORBIT · fase 8 · repricing-01 · E.1
-- Rezago de EMISIÓN: entre la fecha real del envío y el event_date con que
-- Amazon cobra el cargo. Distinto del rezago de INGESTA (ver
-- rezago-ingesta.sql).
--
-- NO IMPLEMENTABLE tal como está escrito: no existe en el esquema de Orbit
-- ninguna columna de "fecha de envío" / fecha de despacho del paquete.
-- Se buscó en migrations/*.sql y app/*.py (grep -rniE
-- 'ship_date|shipped_at|fecha_envio|fecha_de_envio|shipment_date') y no
-- hay match. `ledger_event.event_date` es la fecha del CARGO (lo que ya
-- mide rezago-ingesta.sql junto con observed_at), no la fecha del envío
-- físico; `spapi_order_observation.purchase_date` es la fecha de COMPRA,
-- no de envío. Inventar una de estas columnas como sustituto de "fecha de
-- envío" produciría un número falso, así que esta consulta NO lo hace:
-- deja la razón documentada en su salida, para que medicion.md la cite
-- como `unknown (columna inexistente)` en vez de using otra fecha
-- silenciosamente.

select
    'sin_columna_fecha_de_envio' as motivo,
    'no existe en el esquema de Orbit una columna de fecha de despacho/envío; '
    || 'event_date es la fecha del cargo (ver rezago-ingesta.sql) y '
    || 'spapi_order_observation.purchase_date es la fecha de compra, no de envío'
        as detalle;
