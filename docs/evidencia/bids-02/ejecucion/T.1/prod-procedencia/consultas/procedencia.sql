-- BIDS 02 T.1 (Comprueba): procedencias vivas en target_acos_ciclo.
-- Esperado: solo margen_plataforma. Si aparece cache_estado o default,
-- la 0063 conserva ese valor en el CHECK (cambio 8) y se avisa al lead.
select procedencia, count(*) from target_acos_ciclo group by 1;
