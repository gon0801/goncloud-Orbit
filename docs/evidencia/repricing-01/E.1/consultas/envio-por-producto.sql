-- ORBIT · fase 8 · repricing-01 · E.1
-- Envío por producto: órdenes, mediana, p75, p90, máximo en ventanas de
-- 90/180/365 días, agrupando por ORDEN (no por fila) y sobre abs(amount)
-- (los fees se guardan negativos, ledger_convencion_signos). p75 se
-- conserva porque la DoD v1.1 lo pedía, aunque el hallazgo 5 de
-- plan-validacion-ebm.md midió que no aporta dispersión real.
--
-- LECTURA DUAL (disputa abierta de E.0, sin veredicto — ver
-- docs/evidencia/repricing-01/plan-validacion-ebm.md "Disputa abierta" y
-- spec S10 "Tres fuentes que se solapan"):
--   componentes        = suma de TODOS los cargos shipping_fee de la orden.
--   duplicado_etiqueta = HIPÓTESIS DEL LEAD, NO el veredicto del revisor
--                        escéptico: cuando la orden trae las DOS fuentes de
--                        etiqueta (LabmanLabelPurchase y shipping_label),
--                        cuenta solo la MAYOR de las dos; cualquier otra
--                        fuente (p.ej. ShippingHB) se suma completa.
-- Ninguna de las dos lecturas se sella aquí: eso lo hace E.0/E.2.
--
-- Supuesto declarado: la fuente del cargo se identifica con la parte 6 de
-- source_event_id (ver plan-validacion-ebm.md, consulta de la disputa) y
-- se asume que esa parte trae literalmente 'ShippingHB',
-- 'LabmanLabelPurchase' o 'shipping_label'. No verificado contra
-- producción por esta tarea (ver medicion.md).
--
-- Una orden con más de un producto se EXCLUYE de esta tabla (se cuenta en
-- descartes.sql). Multi-unidad NO se excluye: L_unidad = L_orden / unidades
-- se calcula en efecto-margen.sql; aquí el costo es por ORDEN.

with poblacion as (
    -- FILTRO ÚNICO de E.1 (hecho 13), repetido literal en cada consulta:
    -- cargos de envío en la ventana máxima de 365 días. Las ventanas
    -- menores (90/180) se recortan después vía event_date, sin repetir
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
      and le.event_date >= current_date - interval '365 days'
),
cargos as (
    select *
    from poblacion
    where order_id is not null
),
orden_costo as (
    select
        order_id,
        platform,
        max(event_date) as event_date,
        count(*) as n_cargos,
        bool_or(fuente_plataforma is null or fuente_tipo is null) as tiene_fuente_desconocida,
        sum(monto_abs) as costo_componentes,
        coalesce(sum(monto_abs) filter (
            where fuente_tipo is distinct from 'LabmanLabelPurchase'
              and fuente_tipo is distinct from 'shipping_label'
        ), 0)
        + greatest(
            coalesce(max(monto_abs) filter (where fuente_tipo = 'LabmanLabelPurchase'), 0),
            coalesce(max(monto_abs) filter (where fuente_tipo = 'shipping_label'), 0)
          ) as costo_duplicado_etiqueta
    from cargos
    group by order_id, platform
),
ventas_orden as (
    select order_id, product_id, sum(quantity) as unidades
    from ledger_event
    where kind = 'sale' and order_id is not null and product_id is not null
    group by order_id, product_id
),
orden_producto as (
    select
        order_id,
        count(distinct product_id) as n_productos,
        max(product_id) as product_id,
        sum(unidades) as unidades
    from ventas_orden
    group by order_id
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
    join orden_producto op using (order_id)
    where op.n_productos = 1
      and not oc.tiene_fuente_desconocida
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
join ventanas v on b.event_date >= current_date - (v.ventana_dias || ' days')::interval
group by b.product_id, b.platform, v.ventana_dias, b.lectura
order by b.product_id, b.platform, v.ventana_dias, b.lectura;
