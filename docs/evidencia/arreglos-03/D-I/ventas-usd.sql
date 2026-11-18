-- ARREGLOS 03 D-I.5: ¿ya entro alguna venta de Amazon US con su importe en dolares? SOLO LECTURA.
-- Las ventas guardadas antes del despliegue quedan sin dolares (el libro es de solo agregar):
-- un `t` quiere decir que el libro ya guardo al menos una venta nueva de US con dolares.
-- Lo corre ventas-usd.sh, programado con `at` en goncloud para las 08:45 UTC siguientes al
-- despliegue (el libro corre a las 08:15), desde /mnt/data/appdata/orbit/recordatorios/.
BEGIN READ ONLY;
select count(*) > 0 from ledger_event where platform = 'amazon_us' and kind = 'sale' and amount_original_currency = 'USD' and amount_original > 0;
ROLLBACK;
