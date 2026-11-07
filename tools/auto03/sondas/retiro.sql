-- Bloque G: la 0078 quedo aplicada y no reescribio la historia del retiro. Las columnas nuevas
-- existen, los dos CHECK de motivo admiten producto_sin_ventas, la intencion liga una decision
-- solo con ese motivo, y todo retiro historico sigue 'confirmada': un retiro 'adoptada' solo
-- puede venir del codigo de G, que cierra su intencion con el detalle 'adoptado: ...'
-- (retiro_anuncios._DETALLE_ADOPTADO); uno adoptado sin ese cierre seria historia reescrita.
SELECT CASE WHEN count(*) = 2 THEN 't' ELSE 'f' END
       || '|existen anuncio_retiro.autoria y anuncio_retiro_intencion.decision_id'
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND ((table_name = 'anuncio_retiro' AND column_name = 'autoria')
     OR (table_name = 'anuncio_retiro_intencion' AND column_name = 'decision_id'));
SELECT CASE WHEN count(*) = 2 THEN 't' ELSE 'f' END
       || '|retiro_motivo e intencion_motivo admiten producto_sin_ventas'
  FROM pg_constraint
 WHERE conname IN ('retiro_motivo', 'intencion_motivo')
   AND pg_get_constraintdef(oid) LIKE '%producto_sin_ventas%';
SELECT CASE WHEN EXISTS (
         SELECT 1 FROM pg_constraint
          WHERE conname = 'intencion_decision_coherente'
            AND conrelid = 'public.anuncio_retiro_intencion'::regclass)
       THEN 't' ELSE 'f' END
       || '|la intencion trae decision_id si y solo si el motivo es producto_sin_ventas';
SELECT CASE WHEN count(*) FILTER (
                  WHERE r.autoria = 'adoptada'
                    AND NOT EXISTS (SELECT 1 FROM anuncio_retiro_intencion i
                                     WHERE i.retiro_id = r.id AND i.detalle LIKE 'adoptado:%'))
                = 0
       THEN 't' ELSE 'f' END
       || '|todo retiro historico es confirmada ('
       || count(*) FILTER (WHERE r.autoria = 'confirmada') || ' confirmados, '
       || count(*) FILTER (WHERE r.autoria = 'adoptada') || ' adoptados, de '
       || count(*) || ')'
  FROM anuncio_retiro r;
