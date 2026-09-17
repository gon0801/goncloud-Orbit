-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r4a
-- Envío por producto: órdenes, mediana, p75, p90, máximo en ventanas de
-- 90/180/365 días, agrupando por ORDEN (no por fila) y sobre abs(amount)
-- (los fees se guardan negativos, ledger_convencion_signos). p75 se
-- conserva porque la DoD v1.1 lo pedía, aunque el hallazgo 5 de
-- plan-validacion-ebm.md midió que no aporta dispersión real.
--
-- `dias_con_datos` (r4a): medido por el lead 2026-09-17 que la historia
-- real de `shipping_fee` es de ~287 días, no 365 (ver
-- 01-historia-disponible.sql) — esta columna dice cuántos días de la
-- ventana de esta fila están realmente cubiertos por datos, para que una
-- ventana truncada se vea en la tabla en vez de leerse como "365 días
-- completos" cuando no lo son.
--
-- Ventana SEMIABIERTA [hoy-N, hoy) en UTC: deja fuera el día en curso
-- (todavía incompleto) y usa (now() AT TIME ZONE 'UTC')::date como "hoy",
-- no current_date (que depende de la zona horaria de la sesión).
--
-- IDENTIDAD DE FUENTE (hallazgo alta, ronda r3 — reemplaza el supuesto
-- de la ronda r1, que estaba MAL): el lead midió en producción el
-- 2026-09-17 (ver medicion.md, "Formato real medido por el lead") que
-- source_event_id tiene DOS formas reales, no la asumida:
--   finance, 6 partes:        <plataforma>|finance|fee|<order_id>|<sku o
--                              vacío>|<subtipo>  → identidad =
--                              'finance:' || <subtipo>
--   shipping_label, 4 partes: <plataforma>|shipping_label|<order_id>|
--                              <fecha>  → identidad = 'shipping_label'
-- `fuente_desconocida` = source_event_id nulo, o la parte 2 viene vacía,
-- o la parte 2 es 'finance' pero la parte 6 (subtipo) viene vacía —
-- ninguno de estos casos permite construir una identidad.
-- `fuente_no_reconocida` = identidad construida pero fuera del
-- vocabulario de seis conocido (ver abajo).
-- Vocabulario de las seis fuentes reales medidas en producción:
--   shipping_label, finance:LabmanLabelPurchase, finance:MFNPostageFee,
--   finance:ShippingHB, finance:ShippingChargeback,
--   finance:MFNShippingChargeback.
--
-- LECTURA DUAL (disputa abierta de E.0, sin veredicto):
--   componentes        = suma de TODAS las fuentes reconocidas de la orden.
--   duplicado_etiqueta = HIPÓTESIS DEL LEAD, NO el veredicto de E.0: las
--                        fuentes de COMPRA DE ETIQUETA por `finance` son
--                        `finance:LabmanLabelPurchase` y
--                        `finance:MFNPostageFee`; la otra fuente de
--                        etiqueta es el reporte `shipping_label`. Costo =
--                        greatest(suma de las dos de finance-etiqueta,
--                        suma de shipping_label) + suma del resto
--                        (`finance:ShippingHB`,
--                        `finance:ShippingChargeback`,
--                        `finance:MFNShippingChargeback`). NO SE SABE qué
--                        representan `ShippingHB` ni los dos chargebacks
--                        — eso es exactamente lo que E.0 tiene que
--                        resolver contra el documento contable de origen;
--                        aquí se suman completos porque no hay evidencia
--                        de que dupliquen nada.
-- Ninguna de las dos lecturas se sella aquí: eso lo hace E.0/E.2.
--
-- COBERTURA (hallazgo 23, ronda r3): el bloque de cobertura por fuente se
-- publica UNA SOLA VEZ, aquí (segundo SELECT de este archivo), y vale
-- para el resto de las consultas de E.1 porque todas parten de la MISMA
-- población (poblacion/cargos, filtro único del hecho 13). No se repite
-- en productos-que-alcanzan-minimo.sql, parpadeo.sql ni efecto-margen.sql.
--
-- Una orden con más de un producto se EXCLUYE de esta tabla (se cuenta en
-- descartes.sql). Multi-unidad NO se excluye: L_unidad = L_orden / unidades
-- se calcula en efecto-margen.sql; aquí el costo es por ORDEN.

with poblacion as (
    -- FILTRO UNICO de E.1 (hecho 13), repetido literal en cada consulta:
    -- cargos de envio (kind='fee', fee_type='shipping_fee') en la ventana
    -- maxima de 365 dias, dia de corte en UTC.
    select
        le.order_id,
        le.platform,
        le.event_date,
        le.observed_at,
        abs(le.amount) as monto_abs,
        le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_forma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_subtipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
cargos as (
    select
        *,
        case
            when source_event_id is null or fuente_forma is null then null
            when fuente_forma = 'shipping_label' then 'shipping_label'
            when fuente_forma = 'finance' and fuente_subtipo is not null then 'finance:' || fuente_subtipo
            when fuente_forma = 'finance' then null
            else fuente_forma
        end as identidad_fuente
    from poblacion
    where order_id is not null
),
sum_por_fuente as (
    select
        order_id,
        platform,
        identidad_fuente,
        sum(monto_abs) as monto_fuente,
        count(*) as filas
    from cargos
    group by order_id, platform, identidad_fuente
),
orden_meta as (
    select
        order_id,
        platform,
        max(event_date) as event_date,
        bool_or(identidad_fuente is null) as tiene_fuente_desconocida,
        bool_or(
            identidad_fuente is not null
            and identidad_fuente not in (
                'shipping_label', 'finance:LabmanLabelPurchase', 'finance:MFNPostageFee',
                'finance:ShippingHB', 'finance:ShippingChargeback', 'finance:MFNShippingChargeback'
            )
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
        greatest(
            coalesce(sum(sf.monto_fuente) filter (
                where sf.identidad_fuente in ('finance:LabmanLabelPurchase', 'finance:MFNPostageFee')
            ), 0),
            coalesce(sum(sf.monto_fuente) filter (where sf.identidad_fuente = 'shipping_label'), 0)
        )
        + coalesce(sum(sf.monto_fuente) filter (
            where sf.identidad_fuente in ('finance:ShippingHB', 'finance:ShippingChargeback', 'finance:MFNShippingChargeback')
          ), 0) as costo_duplicado_etiqueta,
        bool_or(sf.identidad_fuente = 'shipping_label') as tiene_shipping_label,
        bool_or(sf.identidad_fuente = 'finance:LabmanLabelPurchase') as tiene_labmanlabelpurchase,
        bool_or(sf.identidad_fuente = 'finance:MFNPostageFee') as tiene_mfnpostagefee,
        bool_or(sf.identidad_fuente = 'finance:ShippingHB') as tiene_shippinghb,
        bool_or(sf.identidad_fuente = 'finance:ShippingChargeback') as tiene_shippingchargeback,
        bool_or(sf.identidad_fuente = 'finance:MFNShippingChargeback') as tiene_mfnshippingchargeback,
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
),
-- dias_con_datos (r4a): una ventana de N días puede estar TRUNCADA si la
-- historia de shipping_fee de la plataforma empieza dentro de esa
-- ventana (medido por el lead: "365 días" son en realidad ~287 días de
-- datos, ver 01-historia-disponible.sql). primer_event_date se calcula
-- SIN el filtro de 365 días de la población, sobre TODO el historial.
historia_platform as (
    select platform, min(event_date) as primer_event_date
    from ledger_event
    where fee_type = 'shipping_fee' and kind = 'fee'
    group by platform
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
    max(b.costo) as maximo,
    (((now() at time zone 'UTC')::date)
        - greatest(
            ((((now() at time zone 'UTC')::date)) - (v.ventana_dias || ' days')::interval)::date,
            hp.primer_event_date
          )) as dias_con_datos
from base_lectura b
join ventanas v
  on b.event_date >= (((now() at time zone 'UTC')::date)) - (v.ventana_dias || ' days')::interval
 and b.event_date <  (((now() at time zone 'UTC')::date))
left join historia_platform hp on hp.platform = b.platform
group by b.product_id, b.platform, v.ventana_dias, b.lectura, hp.primer_event_date
order by b.product_id, b.platform, v.ventana_dias, b.lectura;

-- cobertura de fuentes (hallazgo 4b/23): ordenes con cada una de las seis
-- fuentes reconocidas, por plataforma. Publicada UNA SOLA VEZ (hallazgo
-- 23): vale para todas las consultas de E.1 porque comparten la misma
-- población.
with poblacion as (
    select
        le.order_id,
        le.platform,
        le.event_date,
        le.observed_at,
        abs(le.amount) as monto_abs,
        le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_forma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_subtipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
cargos as (
    select
        *,
        case
            when source_event_id is null or fuente_forma is null then null
            when fuente_forma = 'shipping_label' then 'shipping_label'
            when fuente_forma = 'finance' and fuente_subtipo is not null then 'finance:' || fuente_subtipo
            when fuente_forma = 'finance' then null
            else fuente_forma
        end as identidad_fuente
    from poblacion
    where order_id is not null
),
sum_por_fuente as (
    select
        order_id,
        platform,
        identidad_fuente,
        sum(monto_abs) as monto_fuente,
        count(*) as filas
    from cargos
    group by order_id, platform, identidad_fuente
),
orden_meta as (
    select
        order_id,
        platform,
        bool_or(identidad_fuente is null) as tiene_fuente_desconocida,
        bool_or(
            identidad_fuente is not null
            and identidad_fuente not in (
                'shipping_label', 'finance:LabmanLabelPurchase', 'finance:MFNPostageFee',
                'finance:ShippingHB', 'finance:ShippingChargeback', 'finance:MFNShippingChargeback'
            )
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
        bool_or(sf.identidad_fuente = 'shipping_label') as tiene_shipping_label,
        bool_or(sf.identidad_fuente = 'finance:LabmanLabelPurchase') as tiene_labmanlabelpurchase,
        bool_or(sf.identidad_fuente = 'finance:MFNPostageFee') as tiene_mfnpostagefee,
        bool_or(sf.identidad_fuente = 'finance:ShippingHB') as tiene_shippinghb,
        bool_or(sf.identidad_fuente = 'finance:ShippingChargeback') as tiene_shippingchargeback,
        bool_or(sf.identidad_fuente = 'finance:MFNShippingChargeback') as tiene_mfnshippingchargeback,
        bool_or(sf.filas > 1) as tiene_multi_fila_misma_fuente
    from sum_por_fuente sf
    join orden_meta om using (order_id, platform)
    group by sf.order_id, sf.platform, om.tiene_fuente_desconocida, om.tiene_fuente_no_reconocida
)
select
    platform,
    count(*) filter (where tiene_shipping_label) as ordenes_con_shipping_label,
    count(*) filter (where tiene_labmanlabelpurchase) as ordenes_con_finance_labmanlabelpurchase,
    count(*) filter (where tiene_mfnpostagefee) as ordenes_con_finance_mfnpostagefee,
    count(*) filter (where tiene_shippinghb) as ordenes_con_finance_shippinghb,
    count(*) filter (where tiene_shippingchargeback) as ordenes_con_finance_shippingchargeback,
    count(*) filter (where tiene_mfnshippingchargeback) as ordenes_con_finance_mfnshippingchargeback,
    count(*) filter (where tiene_fuente_desconocida) as ordenes_con_fuente_desconocida,
    count(*) filter (where tiene_fuente_no_reconocida) as ordenes_con_fuente_no_reconocida,
    count(*) filter (where tiene_multi_fila_misma_fuente) as ordenes_con_multi_fila_misma_fuente
from orden_costo
group by platform
order by platform;
