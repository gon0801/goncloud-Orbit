# A.4: lectura posterior de cambios y reversas

Consulta de solo lectura en producción, 2026-09-22 UTC, con `ORBIT_DSN_READ`
dentro de `orbit-app-1`. `app.db.connect` aplicó `ORBIT_PG_HOST=db`. No se
ejecutó ingesta ni escritura durante esta comprobación.

```sql
SELECT id, listing_id, estado, precio_antes, precio_despues,
       enviado_at, confirmado_por
FROM precio_cambio
WHERE id BETWEEN 2 AND 7
ORDER BY id;

SELECT o.metric_date, o.observed_at, l.id AS listing_id,
       o.own_listing_price, o.own_listing_currency
FROM spapi_price_observation o
JOIN listing l ON l.external_id = o.asin AND l.platform = o.platform
WHERE l.id IN (1213, 1284, 1295, 1253)
  AND o.metric_date >= DATE '2026-09-20'
ORDER BY o.metric_date DESC, o.observed_at DESC, l.id;
```

| Cambio | Listing | Estado | Antes MXN | Después MXN | Enviado UTC | Confirmación |
|---:|---:|---|---:|---:|---|---|
| 2 | 1213 | confirmado | 988.00 | 988.01 | 2026-09-19 20:09:02 | observacion |
| 3 | 1284 | confirmado | 1288.00 | 1288.01 | 2026-09-19 20:09:42 | observacion |
| 4 | 1295 | confirmado | 699.00 | 699.01 | 2026-09-19 20:09:43 | observacion |
| 5 | 1213 | confirmado | 988.01 | 988.00 | 2026-09-20 02:30:40 | observacion |
| 6 | 1284 | confirmado | 1288.01 | 1288.00 | 2026-09-20 02:30:44 | observacion |
| 7 | 1295 | confirmado | 699.01 | 699.00 | 2026-09-20 02:30:48 | observacion |

Pricing capturó `988.01 / 1288.01 / 699.01 / 848.00 MXN` el
2026-09-20 02:06:30 UTC, después de los cambios y antes de las reversas.
Capturó `988.00 / 1288.00 / 699.00 / 848.00 MXN` el
2026-09-21 05:02:45 UTC y otra vez el 2026-09-22 05:02:55 UTC. El cuarto
importe es el listing 1253, control negativo.

La lectura de Pricing tiene `observed_at` posterior a los envíos y un precio
explícito en MXN. `summaries.lastUpdatedDate` de Listings Items permaneció
anterior al envío, por lo que no sirve como reloj del cambio de oferta.
