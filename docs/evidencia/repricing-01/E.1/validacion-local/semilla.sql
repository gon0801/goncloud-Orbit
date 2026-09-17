-- DATOS SINTETICOS, NO PRODUCCION.
-- Semilla de validación local para las consultas de E.1 (repricing-01).
-- Corre sobre la base desechable e1_validacion, con el esquema de
-- migrations/0001_initial.sql + 0030_spapi_orders.sql +
-- 0031_spapi_orders_bitemporal.sql ya aplicado. No se corre contra
-- producción.
--
-- FORMATO REAL de source_event_id (ronda r3, medido en producción por el
-- lead el 2026-09-17 — ver medicion.md "Formato real medido por el
-- lead"). DOS formas, NINGÚN fixture usa el formato inventado de las
-- rondas anteriores (`ord_x|plataforma|x|x|x|Subtipo`), que estaba mal:
--
--   finance, 6 partes:        <plataforma>|finance|fee|<order_id>|<sku o
--                              vacío>|<subtipo>
--   shipping_label, 4 partes: <plataforma>|shipping_label|<order_id>|
--                              <fecha YYYY-MM-DD>
--
-- El campo "sku" (parte 5 de la forma finance) se usa aquí solo para
-- diferenciar dos filas de la MISMA orden y el MISMO subtipo (p.ej. dos
-- finance:LabmanLabelPurchase), porque el índice ledger_dedupe_source es
-- único por (platform, kind, source_event_id) completo.

begin;

insert into ingest_run (source, started_at, finished_at, rows_written, ok)
values ('e1_validacion_semilla', now(), now(), 0, true);

insert into product (odoo_sku, name) values
    ('E1-P6', 'Producto con 6 órdenes en 90 días (alcanza mínimo)'),
    ('E1-P5', 'Producto con 5 órdenes en 90 días (no alcanza mínimo)'),
    ('E1-PA', 'Producto A de orden multi-producto'),
    ('E1-PB', 'Producto B de orden multi-producto'),
    ('E1-PM', 'Producto de orden multi-unidad'),
    ('E1-PDUP', 'Producto de orden con finance:LabmanLabelPurchase + shipping_label'),
    ('E1-P3F', 'Producto de orden con tres cargos de fuentes distintas'),
    ('E1-P8', 'Producto con 8 órdenes a 90 días y 12 a 180 días (ronda r1)'),
    ('E1-PCOST', 'Producto con cambio de sku_cost entre la venta y el cargo (ronda r1, hallazgo 2)'),
    ('E1-PNOCOST', 'Producto sin sku_cost vigente (ronda r1, hallazgo 3)'),
    ('E1-PMULTIFILA', 'Producto con dos filas de la misma fuente de etiqueta (ronda r1, hallazgo 7)'),
    ('E1-PNOREC', 'Producto con fuente no reconocida (ronda r1, hallazgo 8)'),
    ('E1-PHOY', 'Producto con cargo de hoy, fuera de la ventana semiabierta (ronda r1, hallazgo 6)'),
    ('E1-PSHIP', 'Producto con observación Shipped (ronda r1, hallazgo 5)'),
    ('E1-PSINSHIP', 'Producto sin observación Shipped (ronda r1, hallazgo 5)'),
    ('E1-PHIST', 'Producto con flapeo GENUINO: entra, sale con <3, reentra (ronda r1, hallazgo 12)'),
    ('E1-PMIXTO', 'Producto con costos MIXTOS en el mismo grupo (ronda r2, hallazgo 3 original)'),
    ('E1-PMFN', 'Producto con finance:MFNPostageFee + shipping_label, MX (ronda r3, hecho nuevo)'),
    ('E1-PCHARGE', 'Producto con chargebacks (ShippingChargeback y MFNShippingChargeback) (ronda r3)'),
    ('E1-PSHIPLABEL', 'Producto con rezago de shipping_label positivo y negativo (ronda r3, hallazgo 21)'),
    ('E1-PLUT', 'Producto con last_updated_time temprano y observed_at tardío (ronda r3, hallazgo 21)'),
    ('E1-PVIEJO', 'Producto del top 10 sin órdenes en 90 días (ronda r3, hallazgo 20)'),
    ('E1-PPARCASI', 'Producto con par de etiquetas casi igual (ronda r4a, cargos-por-orden-y-fuente.sql)'),
    ('E1-PPARLEJOS', 'Producto con par de etiquetas muy distinto (ronda r4a, cargos-por-orden-y-fuente.sql)'),
    ('E1-PCARGA', 'Producto con filas de carga inicial y de ingesta incremental (ronda r4a, rezago-ingesta.sql)'),
    ('E1-PDOSFECHAS', 'Producto con dos cargos de envío en fechas distintas (ronda r4c, rezago-emision.sql medida a)');

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
    'amazon_us|finance|fee|ord_p6_' || n || '||ShippingHB',
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
    'amazon_mx|finance|fee|ord_p5_' || n || '||ShippingHB',
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
     null, null, -95.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_multi_producto||ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- Orden multi-unidad: NO se descarta. L_unidad = L_orden / unidades (3).
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '10 days', 'ord_multi_unidad',
     (select id from product where odoo_sku = 'E1-PM'), 3, 900.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '10 days', 'ord_multi_unidad',
     null, null, -300.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_multi_unidad||ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- Cargo sin venta ligada: solo el cargo, ninguna fila 'sale' con ese
-- order_id. descartes.sql lo cuenta como 'sin_venta_ligada'.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'fee', current_date - interval '10 days', 'ord_sin_venta',
     null, null, -100.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_sin_venta||ShippingHB', (select max(id) from ingest_run));

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
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '8 days', 'ord_fuente_desconocida',
     (select id from product where odoo_sku = 'E1-P6'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '8 days', 'ord_fuente_desconocida',
     null, null, -120.00, 'MXN', 'shipping_fee', null, (select max(id) from ingest_run));

-- =====================================================================
-- Orden con DOS cargos de fuentes distintas: finance:LabmanLabelPurchase
-- (150) y shipping_label (100). componentes = 250; duplicado_etiqueta =
-- greatest(150, 100) = 150 (solo la mayor de las dos fuentes de
-- etiqueta).
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '15 days', 'ord_dos_fuentes',
     (select id from product where odoo_sku = 'E1-PDUP'), 1, 500.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '15 days', 'ord_dos_fuentes',
     null, null, -150.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_dos_fuentes||LabmanLabelPurchase', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '15 days', 'ord_dos_fuentes',
     null, null, -100.00, 'MXN', 'shipping_fee', 'amazon_us|shipping_label|ord_dos_fuentes|' || to_char(current_date - interval '15 days', 'YYYY-MM-DD'), (select max(id) from ingest_run));

-- =====================================================================
-- Orden con TRES cargos de fuentes distintas (finance:ShippingHB + las
-- dos de etiqueta): componentes = 50+150+100 = 300;
-- duplicado_etiqueta = 50 + max(150,100) = 200.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '20 days', 'ord_tres_fuentes',
     (select id from product where odoo_sku = 'E1-P3F'), 1, 700.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '20 days', 'ord_tres_fuentes',
     null, null, -50.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_tres_fuentes||ShippingHB', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '20 days', 'ord_tres_fuentes',
     null, null, -150.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_tres_fuentes||LabmanLabelPurchase', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '20 days', 'ord_tres_fuentes',
     null, null, -100.00, 'MXN', 'shipping_fee', 'amazon_us|shipping_label|ord_tres_fuentes|' || to_char(current_date - interval '20 days', 'YYYY-MM-DD'), (select max(id) from ingest_run));

-- =====================================================================
-- Envío de hace 91 días: cae FUERA de la ventana de 90 días, DENTRO de
-- 180 y 365. Prueba el corte de ventana.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '91 days', 'ord_91_dias',
     (select id from product where odoo_sku = 'E1-P6'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '91 days', 'ord_91_dias',
     null, null, -95.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_91_dias||ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- Envío de hace 181 días: cae FUERA de 90 y 180, DENTRO de 365.
-- =====================================================================
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '181 days', 'ord_181_dias',
     (select id from product where odoo_sku = 'E1-P6'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '181 days', 'ord_181_dias',
     null, null, -95.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_181_dias||ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- Rezago de ingesta: fila con observed_at bien posterior a event_date
-- (simula que la corrida se enteró 7 días después del cargo).
-- =====================================================================
insert into ledger_event (platform, kind, event_date, observed_at, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'fee', current_date - interval '30 days', (current_date - interval '23 days')::timestamptz, 'ord_rezago',
     null, null, -80.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_rezago||ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- RONDA r1: bordes pedidos por el revisor.
-- =====================================================================

-- E1-P8: 8 órdenes en 90 días + 4 órdenes más entre 90 y 180 días atrás.
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
    'amazon_us|finance|fee|ord_p8_' || n || '||ShippingHB',
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
    'amazon_us|finance|fee|ord_p8_' || n || '||ShippingHB',
    (select max(id) from ingest_run)
from unnest(array[95, 110, 125, 140]) n;

-- E1-PCOST: venta hace 40 días, cargo hace 10 días, dos vigencias de
-- sku_cost (50 antes de hace 30 días, 80 después).
insert into sku_cost (product_id, cost_amount, cost_currency, includes_tax, valid_from, valid_to) values
    ((select id from product where odoo_sku = 'E1-PCOST'), 50.00, 'MXN', false, current_date - interval '100 days', current_date - interval '30 days'),
    ((select id from product where odoo_sku = 'E1-PCOST'), 80.00, 'MXN', false, current_date - interval '30 days', null);

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '40 days', 'ord_cost_cambio',
     (select id from product where odoo_sku = 'E1-PCOST'), 1, 500.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '10 days', 'ord_cost_cambio',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_cost_cambio||ShippingHB', (select max(id) from ingest_run));

-- E1-PNOCOST: orden usable, sin ninguna fila en sku_cost.
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '5 days', 'ord_sin_costo',
     (select id from product where odoo_sku = 'E1-PNOCOST'), 1, 400.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '5 days', 'ord_sin_costo',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_sin_costo||ShippingHB', (select max(id) from ingest_run));

-- E1-PMULTIFILA: DOS filas de finance:LabmanLabelPurchase (60 y 40,
-- diferenciadas por el campo "sku" que aquí solo sirve para no chocar en
-- el índice) + una fila de shipping_label (70). componentes = 170;
-- duplicado_etiqueta = greatest(60+40, 70) = 100.
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '12 days', 'ord_multi_fila_misma_fuente',
     (select id from product where odoo_sku = 'E1-PMULTIFILA'), 1, 400.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '12 days', 'ord_multi_fila_misma_fuente',
     null, null, -60.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_multi_fila_misma_fuente|f1|LabmanLabelPurchase', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '12 days', 'ord_multi_fila_misma_fuente',
     null, null, -40.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_multi_fila_misma_fuente|f2|LabmanLabelPurchase', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '12 days', 'ord_multi_fila_misma_fuente',
     null, null, -70.00, 'MXN', 'shipping_fee', 'amazon_us|shipping_label|ord_multi_fila_misma_fuente|' || to_char(current_date - interval '12 days', 'YYYY-MM-DD'), (select max(id) from ingest_run));

-- E1-PNOREC: identidad finance:OtraFuenteRara, fuera del vocabulario de
-- seis fuentes conocidas. descartes.sql debe contarla en
-- 'fuente_no_reconocida'.
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '6 days', 'ord_fuente_no_reconocida',
     (select id from product where odoo_sku = 'E1-PNOREC'), 1, 350.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '6 days', 'ord_fuente_no_reconocida',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_fuente_no_reconocida||OtraFuenteRara', (select max(id) from ingest_run));

-- E1-PHOY: cargo con event_date = HOY. La ventana semiabierta debe
-- excluirlo.
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date, 'ord_hoy',
     (select id from product where odoo_sku = 'E1-PHOY'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date, 'ord_hoy',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_hoy||ShippingHB', (select max(id) from ingest_run));

-- E1-PSHIP / E1-PSINSHIP: rezago de emisión (contraste con
-- fulfillment_status='Shipped').
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '14 days', 'ord_con_shipped',
     (select id from product where odoo_sku = 'E1-PSHIP'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '14 days', 'ord_con_shipped',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_con_shipped||ShippingHB', (select max(id) from ingest_run)),
    ('amazon_us', 'sale', current_date - interval '14 days', 'ord_sin_shipped',
     (select id from product where odoo_sku = 'E1-PSINSHIP'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '14 days', 'ord_sin_shipped',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_sin_shipped||ShippingHB', (select max(id) from ingest_run));

-- E1-PHIST: flapeo GENUINO bajo histéresis (hallazgo 12). Tres racimos
-- (140-145, 95-96, 5-10 días atrás) diseñados para que las seis ventanas
-- móviles den [0,6,8,8,2,6] de la más vieja a la más nueva: entra, se
-- mantiene, SALE de verdad (2<3), reentra.
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
    'amazon_mx|finance|fee|ord_phist_' || n || '||ShippingHB',
    (select max(id) from ingest_run)
from unnest(array[140,141,142,143,144,145, 95,96, 5,6,7,8,9,10]) n;

-- =====================================================================
-- RONDA r2: E1-PMIXTO (hallazgo 3 original, costos mixtos).
-- =====================================================================
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
    'amazon_mx|finance|fee|ord_pmixto_' || n || '||ShippingHB',
    (select max(id) from ingest_run)
from (values (1,20), (2,21), (3,22), (4,23), (5,24), (6,25)) as t(n, cargo_d);

insert into spapi_order_observation (amazon_order_id, platform, marketplace_id, purchase_date, last_updated_time, fulfillment_status, observed_at) values
    ('ord_con_shipped', 'amazon_us', 'ATVPDKIKX0DER', (current_date - interval '20 days')::timestamptz, (current_date - interval '20 days')::timestamptz, 'Unshipped', (current_date - interval '20 days')::timestamptz),
    ('ord_con_shipped', 'amazon_us', 'ATVPDKIKX0DER', (current_date - interval '20 days')::timestamptz, (current_date - interval '19 days')::timestamptz, 'Shipped', (current_date - interval '19 days')::timestamptz);

-- =====================================================================
-- RONDA r3: hecho nuevo (formato real) + hallazgos 20/21/22.
-- =====================================================================

-- E1-PMFN (MX): finance:MFNPostageFee (200) + shipping_label (120).
-- componentes = 320; duplicado_etiqueta = greatest(200,120) = 200 — las
-- DOS lecturas difieren, y usa la fuente MFNPostageFee (distinta de
-- LabmanLabelPurchase) que reveló la sonda de producción.
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '13 days', 'ord_mfn_shipping_label',
     (select id from product where odoo_sku = 'E1-PMFN'), 1, 600.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '13 days', 'ord_mfn_shipping_label',
     null, null, -200.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_mfn_shipping_label||MFNPostageFee', (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '13 days', 'ord_mfn_shipping_label',
     null, null, -120.00, 'MXN', 'shipping_fee', 'amazon_mx|shipping_label|ord_mfn_shipping_label|' || to_char(current_date - interval '13 days', 'YYYY-MM-DD'), (select max(id) from ingest_run));

-- E1-PCHARGE: finance:ShippingHB (80) + finance:ShippingChargeback (20)
-- en una orden, y finance:MFNShippingChargeback (15) sola en otra — ambos
-- chargebacks van al "resto" (se suman completos en las dos lecturas,
-- porque no se sabe qué representan: eso lo resuelve E.0).
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '17 days', 'ord_chargeback',
     (select id from product where odoo_sku = 'E1-PCHARGE'), 1, 450.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '17 days', 'ord_chargeback',
     null, null, -80.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_chargeback||ShippingHB', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '17 days', 'ord_chargeback',
     null, null, -20.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_chargeback||ShippingChargeback', (select max(id) from ingest_run)),
    ('amazon_us', 'sale', current_date - interval '18 days', 'ord_mfn_chargeback',
     (select id from product where odoo_sku = 'E1-PCHARGE'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '18 days', 'ord_mfn_chargeback',
     null, null, -15.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_mfn_chargeback||MFNShippingChargeback', (select max(id) from ingest_run));

-- E1-PSHIPLABEL: rezago de la fila shipping_label (hallazgo 21).
--   ord_reznotivo: cargo hace 10 días, fecha de parte 4 hace 15 días
--     (ANTERIOR al cargo) -> rezago = (hoy-10) - (hoy-15) = 5, positivo.
--   ord_reznegativo: cargo hace 10 días, fecha de parte 4 hace 5 días
--     (POSTERIOR al cargo) -> rezago = (hoy-10) - (hoy-5) = -5, negativo:
--     debe contarse en ordenes_con_rezago_negativo y quedar fuera del
--     percentil.
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '10 days', 'ord_reznotivo',
     (select id from product where odoo_sku = 'E1-PSHIPLABEL'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '10 days', 'ord_reznotivo',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_us|shipping_label|ord_reznotivo|' || to_char(current_date - interval '15 days', 'YYYY-MM-DD'), (select max(id) from ingest_run)),
    ('amazon_us', 'sale', current_date - interval '10 days', 'ord_reznegativo',
     (select id from product where odoo_sku = 'E1-PSHIPLABEL'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '10 days', 'ord_reznegativo',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_us|shipping_label|ord_reznegativo|' || to_char(current_date - interval '5 days', 'YYYY-MM-DD'), (select max(id) from ingest_run));

-- E1-PLUT: last_updated_time temprano (hace 25 días) pero observed_at
-- tardío (hace 2 días, simulando un backfill). Cargo hace 10 días.
--   Con last_updated_time (correcto, hallazgo 21): rezago = (hoy-10) -
--     (hoy-25) = 15, positivo y razonable.
--   Con observed_at (el bug de la ronda r1): rezago = (hoy-10) - (hoy-2)
--     = -8, negativo y sin sentido — exactamente el sesgo que el hallazgo
--     21 describe (sin tope, hacia abajo).
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '10 days', 'ord_lut',
     (select id from product where odoo_sku = 'E1-PLUT'), 1, 300.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '10 days', 'ord_lut',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_lut||ShippingHB', (select max(id) from ingest_run));

insert into spapi_order_observation (amazon_order_id, platform, marketplace_id, last_updated_time, fulfillment_status, observed_at) values
    ('ord_lut', 'amazon_us', 'ATVPDKIKX0DER', (current_date - interval '25 days')::timestamptz, 'Shipped', (current_date - interval '2 days')::timestamptz);

-- E1-PVIEJO: 6 órdenes, TODAS a 100-105 días atrás (dentro de 180/365,
-- FUERA de 90). Entra al top10 (el conjunto de productos es pequeño) pero
-- debe salir con ordenes=0 y alcanza_minimo=false en la ventana de 90
-- días, en vez de desaparecer de la tabla (hallazgo 20).
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'sale', current_date - (n || ' days')::interval, 'ord_pviejo_' || n,
    (select id from product where odoo_sku = 'E1-PVIEJO'), 1, 260.00, 'MXN', null, null,
    (select max(id) from ingest_run)
from unnest(array[100,101,102,103,104,105]) n;

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'fee', current_date - (n || ' days')::interval, 'ord_pviejo_' || n,
    null, null, -90.00, 'MXN', 'shipping_fee',
    'amazon_mx|finance|fee|ord_pviejo_' || n || '||ShippingHB',
    (select max(id) from ingest_run)
from unnest(array[100,101,102,103,104,105]) n;

-- Fila shipping_fee con kind='refund': visible SOLO en 00-sonda-formato.sql
-- (que no filtra por kind), invisible en las demás consultas (todas
-- filtran kind='fee'). Confirma que 00-sonda-formato.sql audita el ledger
-- completo, no solo lo que las consultas de costo usan.
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'refund', current_date - interval '9 days', 'ord_refund_shipping_fee',
     null, null, -30.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_refund_shipping_fee||ShippingHB', (select max(id) from ingest_run));

-- =====================================================================
-- RONDA r4a: cuatro cosas que la producción mostró y la semilla sintética
-- de r3 no podía mostrar.
-- =====================================================================

-- Venta con product_id NULL (medido por el lead: las 148 órdenes que
-- descartes.sql r3 rotulaba 'sin_venta_ligada' SÍ tenían fila de venta;
-- lo que faltaba era product_id). descartes.sql debe contarla en
-- 'venta_sin_producto', NO en 'sin_fila_de_venta'.
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '11 days', 'ord_venta_sin_producto',
     null, null, 320.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '11 days', 'ord_venta_sin_producto',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_venta_sin_producto||ShippingHB', (select max(id) from ingest_run));

-- E1-PPARCASI: par de etiquetas CASI IGUAL, como el caso medido por el
-- lead en producción (finance:LabmanLabelPurchase 452.69 vs
-- shipping_label 449.69, diferencia ~0.7%).
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '16 days', 'ord_par_casi_igual',
     (select id from product where odoo_sku = 'E1-PPARCASI'), 1, 900.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '16 days', 'ord_par_casi_igual',
     null, null, -452.69, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_par_casi_igual||LabmanLabelPurchase', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '16 days', 'ord_par_casi_igual',
     null, null, -449.69, 'MXN', 'shipping_fee', 'amazon_us|shipping_label|ord_par_casi_igual|' || to_char(current_date - interval '16 days', 'YYYY-MM-DD'), (select max(id) from ingest_run));

-- E1-PPARLEJOS: par de etiquetas MUY DISTINTO (finance:MFNPostageFee 500
-- vs shipping_label 100, cociente 5x) — el contraste que demuestra que
-- 'cargos-por-orden-y-fuente.sql' (d)/(e) discriminan casi-igual de
-- muy-distinto, no solo que la consulta corre.
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_us', 'sale', current_date - interval '16 days', 'ord_par_lejos',
     (select id from product where odoo_sku = 'E1-PPARLEJOS'), 1, 700.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '16 days', 'ord_par_lejos',
     null, null, -500.00, 'MXN', 'shipping_fee', 'amazon_us|finance|fee|ord_par_lejos||MFNPostageFee', (select max(id) from ingest_run)),
    ('amazon_us', 'fee', current_date - interval '16 days', 'ord_par_lejos',
     null, null, -100.00, 'MXN', 'shipping_fee', 'amazon_us|shipping_label|ord_par_lejos|' || to_char(current_date - interval '16 days', 'YYYY-MM-DD'), (select max(id) from ingest_run));

-- E1-PCARGA: carga inicial (observed_at hace 30 días, la más vieja de
-- toda la semilla, así que se convierte en el primer_dia_de_ingesta
-- global) vs incremental (observed_at hace 1 día). rezago-ingesta.sql
-- debe mostrar 'todas' con más filas que 'solo_incremental', y las filas
-- de carga inicial deben quedar excluidas de 'solo_incremental'.
insert into ledger_event (platform, kind, event_date, observed_at, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'fee', current_date - (n || ' days')::interval, (current_date - interval '30 days')::timestamptz,
    'ord_carga_inicial_' || n,
    null, null, -90.00, 'MXN', 'shipping_fee',
    'amazon_mx|finance|fee|ord_carga_inicial_' || n || '||ShippingHB',
    (select max(id) from ingest_run)
from unnest(array[60, 61]) n;

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'sale', current_date - (n || ' days')::interval, 'ord_carga_inicial_' || n,
    (select id from product where odoo_sku = 'E1-PCARGA'), 1, 300.00, 'MXN', null, null,
    (select max(id) from ingest_run)
from unnest(array[60, 61]) n;

insert into ledger_event (platform, kind, event_date, observed_at, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'fee', current_date - (n || ' days')::interval, (current_date - interval '1 days')::timestamptz,
    'ord_incremental_' || n,
    null, null, -90.00, 'MXN', 'shipping_fee',
    'amazon_mx|finance|fee|ord_incremental_' || n || '||ShippingHB',
    (select max(id) from ingest_run)
from unnest(array[2, 3]) n;

insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id)
select
    'amazon_mx', 'sale', current_date - (n || ' days')::interval, 'ord_incremental_' || n,
    (select id from product where odoo_sku = 'E1-PCARGA'), 1, 300.00, 'MXN', null, null,
    (select max(id) from ingest_run)
from unnest(array[2, 3]) n;

-- =====================================================================
-- RONDA r4c: hallazgo medio de la validación local — ninguna orden tenía
-- dos cargos de envío en FECHAS DISTINTAS, así que en rezago-emision.sql
-- medida (a) intercambiar primer_cargo/ultimo_cargo no cambiaba ninguna
-- salida local.
-- =====================================================================

-- E1-PDOSFECHAS: venta el día D (hace 10 días), cargo finance:ShippingHB
-- el día D-1 (hace 11 días) y cargo shipping_label el día D+2 (hace 8
-- días; fecha de parte 4 coherente con su propio event_date, hace 8
-- días). Las dos fuentes son reconocidas. Todo dentro de la ventana de
-- 90 días.
--   primer_cargo (D-1) - venta (D) = -1 día -> NEGATIVO, contado aparte
--     y fuera del percentil.
--   ultimo_cargo (D+2) - venta (D) = +2 días -> POSITIVO, entra al
--     percentil.
insert into ledger_event (platform, kind, event_date, order_id, product_id, quantity, amount, amount_currency, fee_type, source_event_id, ingest_run_id) values
    ('amazon_mx', 'sale', current_date - interval '10 days', 'ord_dos_fechas',
     (select id from product where odoo_sku = 'E1-PDOSFECHAS'), 1, 400.00, 'MXN', null, null, (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '11 days', 'ord_dos_fechas',
     null, null, -90.00, 'MXN', 'shipping_fee', 'amazon_mx|finance|fee|ord_dos_fechas||ShippingHB', (select max(id) from ingest_run)),
    ('amazon_mx', 'fee', current_date - interval '8 days', 'ord_dos_fechas',
     null, null, -60.00, 'MXN', 'shipping_fee', 'amazon_mx|shipping_label|ord_dos_fechas|' || to_char(current_date - interval '8 days', 'YYYY-MM-DD'), (select max(id) from ingest_run));

commit;
