-- BIDS 02 M.3 cambio 6: filas que el vivo decidio con evidencia_v2.
-- Esperado: 0 (esa politica nunca decidio en vivo). Si da otro numero,
-- DETENERSE y avisar al lead antes de borrar el rejuego de evidencia_v2.
select count(*) from decision where inputs -> 'evidencia_v2' ->> 'via' = 'decide';
