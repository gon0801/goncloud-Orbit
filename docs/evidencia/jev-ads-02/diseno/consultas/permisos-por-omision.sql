SELECT r AS rol, t AS tabla, has_table_privilege(r, t, 'SELECT') AS puede_leer
  FROM unnest(ARRAY['app_decide','app_ingest','app_jev']) r,
       unnest(ARRAY['jev_revision','jev_par_evento','jev_ficha_version','decision','apply_queue','search_term_observation']) t
 ORDER BY 1,2;
