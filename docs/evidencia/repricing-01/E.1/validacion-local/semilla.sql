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
    ('E1-P3F', 'Producto de orden con tres cargos de fuentes distintas'),
    ('E1-P8', 'Producto con 8 órdenes a 90 días y 12 a 180 días (ronda r1)'),
    ('E1-PCOST', 'Producto con cambio de sku_cost entre la venta y el cargo (ronda r1, hallazgo 2)'),
    ('E1-PNOCOST', 'Producto sin sku_cost vigente (ronda r1, hallazgo 3)'),
    ('E1-PMULTIFILA', 'Producto con dos filas de la misma fuente de etiqueta (ronda r1, hallazgo 7)'),
    ('E1-PNOREC', 'Producto con fuente_tipo no reconocido (ronda r1, hallazgo 8)'),
    ('E1-PHOY', 'Producto con cargo de hoy, fuera de la ventana semiabierta (ronda r1, hallazgo 6)'),
    ('E1-PSHIP', 'Producto con observación Shipped (ronda r1, hallazgo 5)'),
    ('E1-PSINSHIP', 'Producto sin observación Shipped (ronda r1, hallazgo 5)'),
    ('E1-PHIST', 'Producto con flapeo GENUINO: entra, sale con <3, reentra (ronda r1, hallazgo 12)'),
    ('E1-PMIXTO', 'Producto con costos MIXTOS en el mismo grupo: 4 órdenes con sku_cost, 2 sin (ronda r2, hallazgo 3 original)');

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

-- =====================================================================
-- RONDA r1 (corrección): bordes nuevos pedidos por el revisor.
-- =====================================================================

-- ---------------------------------------------------------------------
-- E1-P8: 8 órdenes en 90 días + 4 órdenes más entre 90 y 180 días atrás
-- (12 en total a 180 días). Prueba efecto-margen.sql: alcanza_minimo=true
-- en 90/180/365, y que las tres ventanas se emiten siempre (hallazgo 1),
-- no solo cuando hay >=10 órdenes en ESA ventana particular.
-- ---------------------------------------------------------------------
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_us', 'sale', current_date - (n || ' days')::interval, 'ord_p8_' || n,
    (select id from product where odoo_sku = 'E1-P8'), 1, 280.00, 'MXN', null, null,
    (select max(id) from ingest_run)
from generate_series(1, 8) n;

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_us', 'fee', current_date - (n || ' days')::interval, 'ord_p8_' || n,
    null, null, -90.00, 'MXN', 'shipping_fee',
    'ord_p8_' || n || '|amazon_us|x|x|x|ShippingHB',
    (select max(id) from ingest_run)
from generate_series(1, 8) n;

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_us', 'sale', current_date - (n || ' days')::interval, 'ord_p8_' || n,
    (select id from product where odoo_sku = 'E1-P8'), 1, 280.00, 'MXN', null, null,
    (select max(id) from ingest_run)
from unnest(array[95, 110, 125, 140]) n;

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_us', 'fee', current_date - (n || ' days')::interval, 'ord_p8_' || n,
    null, null, -90.00, 'MXN', 'shipping_fee',
    'ord_p8_' || n || '|amazon_us|x|x|x|ShippingHB',
    (select max(id) from ingest_run)
from unnest(array[95, 110, 125, 140]) n;

-- ---------------------------------------------------------------------
-- E1-PCOST: venta hace 40 días, cargo (envío) hace 10 días, con DOS
-- vigencias de sku_cost que cambian el 1-jun-en-relativo (hoy - 30):
-- la vigencia vieja (50 MXN) cubre la fecha de la VENTA (hoy-40); la
-- nueva (80 MXN) cubre la fecha del CARGO (hoy-10). Si la consulta
-- resolviera el costo a la fecha del cargo (bug del hallazgo 2), saldría
-- 80; a la fecha de la venta (correcto) debe salir 50.
-- ---------------------------------------------------------------------
insert into sku_cost (product_id, cost_amount, cost_currency, includes_tax, valid_from, valid_to) values
    ((select id from product where odoo_sku = 'E1-PCOST'), 50.00, 'MXN', false, current_date - interval '100 days', current_date - interval '30 days'),
    ((select id from product where odoo_sku = 'E1-PCOST'), 80.00, 'MXN', false, current_date - interval '30 days', null);

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '40 days', 'ord_cost_cambio',
     (select id from product where odoo_sku = 'E1-PCOST'), 1, 500.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '10 days', 'ord_cost_cambio',
     null, null, -90.00, 'MXN', 'shipping_fee', 'ord_cost_cambio|amazon_mx|x|x|x|ShippingHB', (select max(id) from ingest_run));

-- ---------------------------------------------------------------------
-- E1-PNOCOST: orden usable, sin NINGUNA fila en sku_cost para el
-- producto. costo_unidad_mxn debe salir NULL, contarse en
-- ordenes_sin_costo, y el margen del grupo debe salir NULL (no un
-- promedio que ignora esta orden).
-- ---------------------------------------------------------------------
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '5 days', 'ord_sin_costo',
     (select id from product where odoo_sku = 'E1-PNOCOST'), 1, 400.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '5 days', 'ord_sin_costo',
     null, null, -90.00, 'MXN', 'shipping_fee', 'ord_sin_costo|amazon_mx|x|x|x|ShippingHB', (select max(id) from ingest_run));

-- ---------------------------------------------------------------------
-- E1-PMULTIFILA: DOS filas de LabmanLabelPurchase (60 y 40) + una fila
-- de shipping_label (70). componentes = 60+40+70 = 170.
-- duplicado_etiqueta correcto = suma DENTRO de LabmanLabelPurchase
-- (60+40=100) vs shipping_label (70), toma la mayor = 100. El bug viejo
-- (max() por fila sin agrupar antes) habría dado max(60,40)=60 vs 70 = 70.
-- ---------------------------------------------------------------------
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '12 days', 'ord_multi_fila_misma_fuente',
     (select id from product where odoo_sku = 'E1-PMULTIFILA'), 1, 400.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '12 days', 'ord_multi_fila_misma_fuente',
     null, null, -60.00, 'MXN', 'shipping_fee', 'ord_multi_fila_misma_fuente|amazon_us|x|x|1|LabmanLabelPurchase', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '12 days', 'ord_multi_fila_misma_fuente',
     null, null, -40.00, 'MXN', 'shipping_fee', 'ord_multi_fila_misma_fuente|amazon_us|x|x|2|LabmanLabelPurchase', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '12 days', 'ord_multi_fila_misma_fuente',
     null, null, -70.00, 'MXN', 'shipping_fee', 'ord_multi_fila_misma_fuente|amazon_us|x|x|x|shipping_label', (select max(id) from ingest_run));

-- ---------------------------------------------------------------------
-- E1-PNOREC: fuente_tipo presente pero fuera del vocabulario conocido
-- ('OtraFuenteRara'). descartes.sql debe contarla en 'fuente_no_reconocida',
-- NO en 'usable' ni en 'fuente_desconocida'.
-- ---------------------------------------------------------------------
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '6 days', 'ord_fuente_no_reconocida',
     (select id from product where odoo_sku = 'E1-PNOREC'), 1, 350.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '6 days', 'ord_fuente_no_reconocida',
     null, null, -90.00, 'MXN', 'shipping_fee', 'ord_fuente_no_reconocida|amazon_us|x|x|x|OtraFuenteRara', (select max(id) from ingest_run));

-- ---------------------------------------------------------------------
-- E1-PHOY: cargo con event_date = HOY. La ventana semiabierta
-- [hoy-N, hoy) debe EXCLUIRLO de las tres ventanas (90/180/365) — el día
-- en curso está incompleto y no cuenta (hallazgo 6).
-- ---------------------------------------------------------------------
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date, 'ord_hoy',
     (select id from product where odoo_sku = 'E1-PHOY'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date, 'ord_hoy',
     null, null, -90.00, 'MXN', 'shipping_fee', 'ord_hoy|amazon_mx|x|x|x|ShippingHB', (select max(id) from ingest_run));

-- ---------------------------------------------------------------------
-- E1-PSHIP / E1-PSINSHIP: rezago de emisión. ord_con_shipped tiene una
-- observación spapi 'Shipped' 5 días antes del cargo (rezago = 5 días).
-- ord_sin_shipped tiene cargo pero NINGUNA observación 'Shipped' (debe
-- contarse en ordenes_sin_observacion_shipped, no en el percentil).
-- ---------------------------------------------------------------------
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '14 days', 'ord_con_shipped',
     (select id from product where odoo_sku = 'E1-PSHIP'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '14 days', 'ord_con_shipped',
     null, null, -90.00, 'MXN', 'shipping_fee', 'ord_con_shipped|amazon_us|x|x|x|ShippingHB', (select max(id) from ingest_run)),
    ('amazon_us', 'sale', current_date - interval '14 days', 'ord_sin_shipped',
     (select id from product where odoo_sku = 'E1-PSINSHIP'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '14 days', 'ord_sin_shipped',
     null, null, -90.00, 'MXN', 'shipping_fee', 'ord_sin_shipped|amazon_us|x|x|x|ShippingHB', (select max(id) from ingest_run));

-- ---------------------------------------------------------------------
-- E1-PHIST: flapeo GENUINO bajo histéresis (hallazgo 12). Tres racimos de
-- 6/2/6 órdenes en días distintos, elegidos para que las seis ventanas
-- móviles (offsets 150,120,90,60,30,0 días, de la más vieja a la más
-- nueva) den los conteos [0,6,8,8,2,6]:
--   ventana 1 (0)  -> sin historia (false)
--   ventana 2 (6)  -> ENTRA (true)
--   ventana 3 (8)  -> sigue evaluado
--   ventana 4 (8)  -> sigue evaluado
--   ventana 5 (2)  -> SALE de verdad (<3, real, justo después de haber
--                     estado evaluado) — esto es lo que
--                     parpadea_con_histeresis debe capturar
--   ventana 6 (6)  -> REENTRA
-- Corte seco también marca parpadeo aquí (correcto: es un flapeo real),
-- a diferencia de E1-P8 (corte seco lo marca, histéresis NO debería,
-- porque nunca cae por debajo de 3).
-- ---------------------------------------------------------------------
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'sale', current_date - (n || ' days')::interval, 'ord_phist_' || n,
    (select id from product where odoo_sku = 'E1-PHIST'), 1, 260.00, 'MXN', null, null,
    (select max(id) from ingest_run)
from unnest(array[140,141,142,143,144,145, 95,96, 5,6,7,8,9,10]) n;

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'fee', current_date - (n || ' days')::interval, 'ord_phist_' || n,
    null, null, -90.00, 'MXN', 'shipping_fee',
    'ord_phist_' || n || '|amazon_mx|x|x|x|ShippingHB',
    (select max(id) from ingest_run)
from unnest(array[140,141,142,143,144,145, 95,96, 5,6,7,8,9,10]) n;

-- ---------------------------------------------------------------------
-- E1-PMIXTO (ronda r2, hallazgo 3 original): costos MIXTOS dentro del
-- MISMO grupo producto/plataforma/ventana/lectura, para que la anulación
-- explícita del margen (ordenes_sin_costo > 0 => NULL) discrimine de
-- verdad. Con la semilla de la ronda r1, todo grupo con ordenes_sin_costo
-- > 0 tenía TODAS sus órdenes sin costo (avg() ya daba NULL solo, con o
-- sin la anulación explícita) — este producto rompe eso a propósito:
--
--   4 órdenes (ord_pmixto_1..4): venta hace 20-23 días, DENTRO de la
--   vigencia de sku_cost (60 MXN, vigente desde hace 200 días). Tienen
--   costo.
--   2 órdenes (ord_pmixto_5..6): venta hace 250-251 días, ANTES de que
--   empezara la vigencia de sku_cost (hace 200 días). Sin costo a su
--   fecha.
--
-- Las SEIS comparten cargo de envío reciente (20-25 días atrás), así que
-- las seis caen en el MISMO grupo de ventana (90/180/365 idénticas) y
-- lectura — el promedio ingenuo de las 4 con costo (60 MXN) sería un
-- número calculable si no se anulara explícitamente; con el arreglo,
-- ordenes_sin_costo=2 fuerza el margen a NULL.
-- ---------------------------------------------------------------------
insert into sku_cost (product_id, cost_amount, cost_currency, includes_tax, valid_from, valid_to) values
    ((select id from product where odoo_sku = 'E1-PMIXTO'), 60.00, 'MXN', false, current_date - interval '200 days', null);

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'sale', current_date - (venta_d || ' days')::interval, 'ord_pmixto_' || n,
    (select id from product where odoo_sku = 'E1-PMIXTO'), 1, 500.00, 'MXN', null, null,
    (select max(id) from ingest_run)
from (values (1,20), (2,21), (3,22), (4,23), (5,250), (6,251)) as t(n, venta_d);

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'fee', current_date - (cargo_d || ' days')::interval, 'ord_pmixto_' || n,
    null, null, -90.00, 'MXN', 'shipping_fee',
    'ord_pmixto_' || n || '|amazon_mx|x|x|x|ShippingHB',
    (select max(id) from ingest_run)
from (values (1,20), (2,21), (3,22), (4,23), (5,24), (6,25)) as t(n, cargo_d);

insert into spapi_order_observation (amazon_order_id, platform, marketplace_id, last_updated_time, fulfillment_status, observed_at) values
    ('ord_con_shipped', 'amazon_us', 'ATVPDKIKX0DER', (current_date - interval '20 days')::timestamptz, 'Unshipped', (current_date - interval '20 days')::timestamptz),
    ('ord_con_shipped', 'amazon_us', 'ATVPDKIKX0DER', (current_date - interval '19 days')::timestamptz, 'Shipped', (current_date - interval '19 days')::timestamptz);

commit;
