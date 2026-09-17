-- DATOS SINTETICOS, NO PRODUCCION.
-- Semilla de validación local para las consultas de E.1 (repricing-01).
-- Corre sobre la base desechable e1_validacion, con el esquema de
-- migrations/0001_initial.sql ya aplicado. No se corre contra producción.
--
-- source_event_id siempre incluye el order_id en la parte 1: el índice
-- ledger_dedupe_source es único por (platform, kind, source_event_id) SIN
-- order_id, así que dos cargos de órdenes distintas con el mismo texto
-- literal en source_event_id chocarían entre sí.

begin;

insert into ingest_run (source, started_at, finished_at, rows_written, ok)
values ('e1_validacion_semilla', now(), now(), 0, true);

insert into product (odoo_sku, name) values
    ('E1-P6', 'Producto con 6 órdenes en 90 días (alcanza mínimo)'),
    ('E1-P5', 'Producto con 5 órdenes en 90 días (no alcanza mínimo)'),
    ('E1-PA', 'Producto A de orden multi-producto'),
    ('E1-PB', 'Producto B de orden multi-producto'),
    ('E1-PM', 'Producto de orden multi-unidad'),
    ('E1-PDUP', 'Producto de orden con dos fuentes de etiqueta (duplicado_etiqueta)'),
    ('E1-P3F', 'Producto de orden con tres cargos de fuentes distintas');

-- =====================================================================
-- P6: 6 órdenes de un solo producto y una sola unidad, dentro de 90 días.
-- Un cargo shipping_fee cada una, montos NEGATIVOS (convención de signos),
-- para probar que el percentil se calcula sobre abs(amount) y no elige
-- el "más barato" si se dejara el signo crudo.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_us', 'sale', current_date - (n || ' days')::interval, 'ord_p6_' || n,
    (select id from product where odoo_sku = 'E1-P6'), 1, 300.00, 'MXN', null, null,
    (select max(id) from ingest_run)
from generate_series(1, 6) n;

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_us', 'fee', current_date - (n || ' days')::interval, 'ord_p6_' || n,
    null, null, -(90 + n)::numeric, 'MXN', 'shipping_fee',
    'ord_p6_' || n || '|amazon_us|x|x|x|ShippingHB',
    (select max(id) from ingest_run)
from generate_series(1, 6) n;

-- =====================================================================
-- P5: 5 órdenes en 90 días (no llega a 6). Mismo patrón que P6.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'sale', current_date - (n || ' days')::interval, 'ord_p5_' || n,
    (select id from product where odoo_sku = 'E1-P5'), 1, 250.00, 'MXN', null, null,
    (select max(id) from ingest_run)
from generate_series(1, 5) n;

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'fee', current_date - (n || ' days')::interval, 'ord_p5_' || n,
    null, null, -95.00, 'MXN', 'shipping_fee',
    'ord_p5_' || n || '|amazon_mx|x|x|x|ShippingHB',
    (select max(id) from ingest_run)
from generate_series(1, 5) n;

-- =====================================================================
-- Orden multi-producto: se DESCARTA de envio-por-producto, se cuenta en
-- descartes.sql como 'multi_producto'.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '10 days', 'ord_multi_producto',
     (select id from product where odoo_sku = 'E1-PA'), 1, 200.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'sale', current_date - interval '10 days', 'ord_multi_producto',
     (select id from product where odoo_sku = 'E1-PB'), 1, 150.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '10 days', 'ord_multi_producto',
     null, null, -95.00, 'MXN', 'shipping_fee', 'ord_multi_producto|amazon_mx|x|x|x|ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- Orden multi-unidad: NO se descarta. L_unidad = L_orden / unidades (3).
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '10 days', 'ord_multi_unidad',
     (select id from product where odoo_sku = 'E1-PM'), 3, 900.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '10 days', 'ord_multi_unidad',
     null, null, -300.00, 'MXN', 'shipping_fee', 'ord_multi_unidad|amazon_mx|x|x|x|ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- Cargo sin venta ligada: solo el cargo, ninguna fila 'sale' con ese
-- order_id. descartes.sql lo cuenta como 'sin_venta_ligada'.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'fee', current_date - interval '10 days', 'ord_sin_venta',
     null, null, -100.00, 'MXN', 'shipping_fee', 'ord_sin_venta|amazon_us|x|x|x|ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- Cargo sin order_id: descartes.sql lo cuenta como 'sin_order_id' (FILA,
-- no orden, porque no hay orden que agrupar). source_event_id NULL cae al
-- índice ledger_dedupe_sin_orden (platform,kind,fee_type,event_date,amount,
-- amount_currency) — sin colisión porque nada más comparte esos 6 valores.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'fee', current_date - interval '5 days', null,
     null, null, -50.00, 'MXN', 'shipping_fee', null, (select max(id) from ingest_run));

-- =====================================================================
-- source_event_id NULO con order_id presente: fuente_desconocida.
-- descartes.sql cuenta la ORDEN completa como 'fuente_desconocida' (regla
-- de E.0: un grupo con fuente desconocida no cierra hasta resolverse, no
-- se cuela como 'usable' ni se pierde en 'sin duplicados'). Con order_id
-- presente el dedupe cae en ledger_dedupe_sin_orden solo si además
-- order_id es NULL, así que aquí no aplica ningún índice de dedupe por
-- source_event_id (ambos son NULL y el índice filtra
-- "WHERE source_event_id IS NULL AND order_id IS NULL").
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '8 days', 'ord_fuente_desconocida',
     (select id from product where odoo_sku = 'E1-P6'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '8 days', 'ord_fuente_desconocida',
     null, null, -120.00, 'MXN', 'shipping_fee', null, (select max(id) from ingest_run));

-- =====================================================================
-- Orden con DOS cargos de fuentes distintas: LabmanLabelPurchase (150) y
-- shipping_label (100). componentes = 250; duplicado_etiqueta = 150
-- (solo la mayor de las dos fuentes de etiqueta).
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '15 days', 'ord_dos_fuentes',
     (select id from product where odoo_sku = 'E1-PDUP'), 1, 500.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '15 days', 'ord_dos_fuentes',
     null, null, -150.00, 'MXN', 'shipping_fee', 'ord_dos_fuentes|amazon_us|x|x|x|LabmanLabelPurchase', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '15 days', 'ord_dos_fuentes',
     null, null, -100.00, 'MXN', 'shipping_fee', 'ord_dos_fuentes|amazon_us|x|x|x|shipping_label', (select max(id) from ingest_run));

-- =====================================================================
-- Orden con TRES cargos de fuentes distintas (ShippingHB + las dos de
-- etiqueta): componentes = 50+150+100 = 300;
-- duplicado_etiqueta = 50 + max(150,100) = 200.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '20 days', 'ord_tres_fuentes',
     (select id from product where odoo_sku = 'E1-P3F'), 1, 700.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '20 days', 'ord_tres_fuentes',
     null, null, -50.00, 'MXN', 'shipping_fee', 'ord_tres_fuentes|amazon_us|x|x|x|ShippingHB', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '20 days', 'ord_tres_fuentes',
     null, null, -150.00, 'MXN', 'shipping_fee', 'ord_tres_fuentes|amazon_us|x|x|x|LabmanLabelPurchase', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '20 days', 'ord_tres_fuentes',
     null, null, -100.00, 'MXN', 'shipping_fee', 'ord_tres_fuentes|amazon_us|x|x|x|shipping_label', (select max(id) from ingest_run));

-- =====================================================================
-- Envío de hace 91 días: cae FUERA de la ventana de 90 días, DENTRO de
-- 180 y 365. Prueba el corte de ventana.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '91 days', 'ord_91_dias',
     (select id from product where odoo_sku = 'E1-P6'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '91 days', 'ord_91_dias',
     null, null, -95.00, 'MXN', 'shipping_fee', 'ord_91_dias|amazon_mx|x|x|x|ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- Envío de hace 181 días: cae FUERA de 90 y 180, DENTRO de 365.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '181 days', 'ord_181_dias',
     (select id from product where odoo_sku = 'E1-P6'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '181 days', 'ord_181_dias',
     null, null, -95.00, 'MXN', 'shipping_fee', 'ord_181_dias|amazon_mx|x|x|x|ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- Rezago de ingesta: fila con observed_at bien posterior a event_date
-- (simula que la corrida se enteró 7 días después del cargo).
-- =====================================================================
insert into ledger_event (platform, kind, event_date, observed_at, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'fee', current_date - interval '30 days', (current_date - interval '23 days')::timestamptz, 'ord_rezago',
     null, null, -80.00, 'MXN', 'shipping_fee', 'ord_rezago|amazon_us|x|x|x|ShippingHB', (select max(id) from ingest_run));

commit;
