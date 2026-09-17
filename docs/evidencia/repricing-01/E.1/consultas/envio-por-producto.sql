-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r1
-- Envío por producto: órdenes, mediana, p75, p90, máximo en ventanas de
-- 90/180/365 días, agrupando por ORDEN (no por fila) y sobre abs(amount)
-- (los fees se guardan negativos, ledger_convencion_signos). p75 se
-- conserva porque la DoD v1.1 lo pedía, aunque el hallazgo 5 de
-- plan-validacion-ebm.md midió que no aporta dispersión real.
--
-- Ventana SEMIABIERTA [hoy-N, hoy) en UTC: deja fuera el día en curso
-- (todavía incompleto) y usa (now() AT TIME ZONE 'UTC')::date como "hoy",
-- no current_date (que depende de la zona horaria de la sesión).
--
-- LECTURA DUAL (disputa abierta de E.0, sin veredicto — ver
-- docs/evidencia/repricing-01/plan-validacion-ebm.md "Disputa abierta" y
-- spec S10 "Tres fuentes que se solapan"):
--   componentes        = suma de TODOS los cargos shipping_fee de la orden.
--   duplicado_etiqueta = HIPÓTESIS DEL LEAD, NO el veredicto del revisor
--                        escéptico: suma DENTRO de cada fuente, luego
--                        cuenta solo la MAYOR entre las dos fuentes de
--                        etiqueta (LabmanLabelPurchase, shipping_label);
--                        cualquier otra fuente (p.ej. ShippingHB) se suma
--                        completa.
-- Ninguna de las dos lecturas se sella aquí: eso lo hace E.0/E.2.
--
-- Segundo SELECT del archivo: cobertura por fuente (hallazgo 4b) — si
-- alguna de las tres fuentes conocidas sale en cero, o si
-- fuente_desconocida/fuente_no_reconocida/multi_fila no son cero, tiene
-- que hacer ruido antes de confiar en la tabla de arriba.
--
-- Supuesto declarado: la fuente del cargo se identifica con la parte 6 de
-- source_event_id (ver plan-validacion-ebm.md, consulta de la disputa) y
-- se asume que esa parte trae literalmente 'ShippingHB',
-- 'LabmanLabelPurchase' o 'shipping_label' — validado por
-- 00-sonda-formato.sql, que corre antes que esta consulta.
--
-- Una orden con más de un producto se EXCLUYE de esta tabla (se cuenta en
-- descartes.sql). Multi-unidad NO se excluye: L_unidad = L_orden / unidades
-- se calcula en efecto-margen.sql; aquí el costo es por ORDEN.

with poblacion as (
    -- FILTRO UNICO de E.1 (hecho 13), repetido literal en cada consulta:
    -- cargos de envio (kind='fee', fee_type='shipping_fee') en la ventana
    -- maxima de 365 dias, dia de corte en UTC ((now() AT TIME ZONE
    -- 'UTC')::date en vez de current_date: evita que la sesion que corre
    -- la consulta, en otra zona horaria, mueva el corte). Las ventanas
    -- menores (90/180) se recortan despues via event_date, sin repetir
    -- este WHERE.
    select
        le.order_id,
        le.platform,
        le.event_date,
        le.observed_at,
        abs(le.amount) as monto_abs,
        le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_plataforma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_tipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
cargos as (
    select *
    from poblacion
    where order_id is not null
),
sum_por_fuente as (
    select
        order_id,
        platform,
        fuente_tipo,
        sum(monto_abs) as monto_fuente,
        count(*) as filas
    from cargos
    group by order_id, platform, fuente_tipo
),
orden_meta as (
    select
        order_id,
        platform,
        max(event_date) as event_date,
        bool_or(fuente_plataforma is null or fuente_tipo is null) as tiene_fuente_desconocida,
        bool_or(
            fuente_tipo is not null
            and fuente_tipo not in ('ShippingHB', 'LabmanLabelPurchase', 'shipping_label')
        ) as tiene_fuente_no_reconocida
    from cargos
    group by order_id, platform
),
orden_costo as (
    select
        sf.order_id,
        sf.platform,
        om.event_date,
        om.tiene_fuente_desconocida,
        om.tiene_fuente_no_reconocida,
        sum(sf.monto_fuente) as costo_componentes,
        coalesce(sum(sf.monto_fuente) filter (
            where sf.fuente_tipo is distinct from 'LabmanLabelPurchase'
              and sf.fuente_tipo is distinct from 'shipping_label'
        ), 0)
        + greatest(
            coalesce(sum(sf.monto_fuente) filter (where sf.fuente_tipo = 'LabmanLabelPurchase'), 0),
            coalesce(sum(sf.monto_fuente) filter (where sf.fuente_tipo = 'shipping_label'), 0)
          ) as costo_duplicado_etiqueta,
        bool_or(sf.fuente_tipo = 'ShippingHB') as tiene_shippinghb,
        bool_or(sf.fuente_tipo = 'LabmanLabelPurchase') as tiene_labmanlabelpurchase,
        bool_or(sf.fuente_tipo = 'shipping_label') as tiene_shipping_label,
        bool_or(sf.filas > 1) as tiene_multi_fila_misma_fuente
    from sum_por_fuente sf
    join orden_meta om using (order_id, platform)
    group by sf.order_id, sf.platform, om.event_date, om.tiene_fuente_desconocida, om.tiene_fuente_no_reconocida
),
ventas_orden as (
    select order_id, platform, product_id, quantity, amount, amount_currency, event_date
    from ledger_event
    where kind = 'sale' and order_id is not null and product_id is not null
),
orden_producto as (
    select
        order_id,
        platform,
        count(distinct product_id) as n_productos,
        max(product_id) as product_id,
        sum(quantity) as unidades,
        sum(amount) as venta_amount,
        max(amount_currency) as venta_currency,
        max(event_date) as venta_event_date
    from ventas_orden
    group by order_id, platform
),
orden_final as (
    select
        oc.order_id,
        oc.platform,
        oc.event_date,
        op.product_id,
        oc.costo_componentes,
        oc.costo_duplicado_etiqueta
    from orden_costo oc
    join orden_producto op using (order_id, platform)
    where op.n_productos = 1
      and not oc.tiene_fuente_desconocida
      and not oc.tiene_fuente_no_reconocida
),
ventanas (ventana_dias) as (values (90), (180), (365)),
base_lectura as (
    select order_id, platform, product_id, event_date, 'componentes' as lectura, costo_componentes as costo
    from orden_final
    union all
    select order_id, platform, product_id, event_date, 'duplicado_etiqueta' as lectura, costo_duplicado_etiqueta as costo
    from orden_final
)
select
    b.product_id,
    b.platform,
    v.ventana_dias,
    b.lectura,
    count(*) as ordenes,
    percentile_cont(0.5) within group (order by b.costo) as mediana,
    percentile_cont(0.75) within group (order by b.costo) as p75,
    percentile_cont(0.9) within group (order by b.costo) as p90,
    max(b.costo) as maximo
from base_lectura b
join ventanas v
  on b.event_date >= (((now() at time zone 'UTC')::date)) - (v.ventana_dias || ' days')::interval
 and b.event_date <  (((now() at time zone 'UTC')::date))
group by b.product_id, b.platform, v.ventana_dias, b.lectura
order by b.product_id, b.platform, v.ventana_dias, b.lectura;

-- cobertura de fuentes (hallazgo 4b/8): ordenes con cada fuente
-- reconocida, para que un CERO haga ruido en vez de pasar inadvertido.
with poblacion as (
    select
        le.order_id,
        le.platform,
        le.event_date,
        le.observed_at,
        abs(le.amount) as monto_abs,
        le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_plataforma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_tipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
cargos as (
    select *
    from poblacion
    where order_id is not null
),
sum_por_fuente as (
    select
        order_id,
        platform,
        fuente_tipo,
        sum(monto_abs) as monto_fuente,
        count(*) as filas
    from cargos
    group by order_id, platform, fuente_tipo
),
orden_meta as (
    select
        order_id,
        platform,
        max(event_date) as event_date,
        bool_or(fuente_plataforma is null or fuente_tipo is null) as tiene_fuente_desconocida,
        bool_or(
            fuente_tipo is not null
            and fuente_tipo not in ('ShippingHB', 'LabmanLabelPurchase', 'shipping_label')
        ) as tiene_fuente_no_reconocida
    from cargos
    group by order_id, platform
),
orden_costo as (
    select
        sf.order_id,
        sf.platform,
        om.tiene_fuente_desconocida,
        om.tiene_fuente_no_reconocida,
        bool_or(sf.fuente_tipo = 'ShippingHB') as tiene_shippinghb,
        bool_or(sf.fuente_tipo = 'LabmanLabelPurchase') as tiene_labmanlabelpurchase,
        bool_or(sf.fuente_tipo = 'shipping_label') as tiene_shipping_label,
        bool_or(sf.filas > 1) as tiene_multi_fila_misma_fuente
    from sum_por_fuente sf
    join orden_meta om using (order_id, platform)
    group by sf.order_id, sf.platform, om.tiene_fuente_desconocida, om.tiene_fuente_no_reconocida
)
select
    platform,
    count(*) filter (where tiene_shippinghb) as ordenes_con_shippinghb,
    count(*) filter (where tiene_labmanlabelpurchase) as ordenes_con_labmanlabelpurchase,
    count(*) filter (where tiene_shipping_label) as ordenes_con_shipping_label,
    count(*) filter (where tiene_fuente_desconocida) as ordenes_con_fuente_desconocida,
    count(*) filter (where tiene_fuente_no_reconocida) as ordenes_con_fuente_no_reconocida,
    count(*) filter (where tiene_multi_fila_misma_fuente) as ordenes_con_multi_fila_misma_fuente
from orden_costo
group by platform
order by platform;
