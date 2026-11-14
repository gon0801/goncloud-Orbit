SELECT count(*) FROM decision WHERE inputs->>'politica'='niveles_v3' AND inputs->'caso'->'trayectoria'->'cambios' @> '[{"origen":"ajuste_de_campana"}]'
