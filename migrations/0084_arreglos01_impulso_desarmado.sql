-- sonda-aplicada: SELECT coalesce((SELECT pg_get_constraintdef(oid) LIKE '%''desarmado''%' FROM pg_constraint WHERE conname = 'lectura_veredicto' AND conrelid = 'public.impulso_lectura'::regclass), false)
-- 0084 (ARREGLOS 01 Q.2, hallazgo I-H2): impulso_lectura admite el veredicto
-- 'desarmado'.
--
-- Un impulso cuyo grupo se pauso desde la fabrica queda con el lote
-- 'desarmado' (terminal: nunca revive). El vigia tiene que cerrarlo con una
-- lectura final 'desarmado' y hecho 'pausado', no reintentar la pausa cada dia
-- (tools/fabrica_campanas.py aborta con "desarmado: nunca revive" y el vigia
-- respondia 409 sin guardar lectura). El resto del vocabulario no cambia:
-- lectura_en_curso_no_final sigue igual ('desarmado' no es 'en_curso').
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0084_reversa_arreglos01_impulso_desarmado.sql.

ALTER TABLE impulso_lectura DROP CONSTRAINT lectura_veredicto;
ALTER TABLE impulso_lectura ADD CONSTRAINT lectura_veredicto CHECK (veredicto IN
    ('en_curso', 'vende', 'clics_sin_ventas', 'impresiones_sin_clics', 'sin_impresiones',
     'desarmado'));
