# REPRICING 01 E.0a — aclaracion del dueno

En la sesion del 30-sep-2026 (America/Vancouver), el dueno aclaro:
«no no estan contando dos veces el envio sigue».

La lectura operativa para preparar E.0b es **componentes distintos** para
`finance:LabmanLabelPurchase`, `finance:ShippingHB` y `shipping_label`: una
coincidencia de orden o importe no autoriza a descartar ninguna de esas fuentes.
La ingesta actual conserva cada `source_event_id`; no deduplica entre estas
tres identidades por `order_id`.

Esta aclaracion es una decision del dueno, no el documento de origen de las
54 ordenes US de `veredicto.md` §7. El DoD de E.0a aun exige conciliar cada
par contra Seller Central o la contabilidad que alimenta el ledger. Quedan
pendientes tambien la clasificacion de descartes por signo y decidir si
`ShippingHB` entra en `L` o en otro componente del margen sin duplicar una
cotizacion FBM. Hasta entonces no se sella el valor de `L` ni se activa FBM.
