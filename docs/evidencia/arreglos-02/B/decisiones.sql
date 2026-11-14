SELECT id, kind, new_value, inputs::text FROM decision WHERE inputs->>'politica'='niveles_v3' ORDER BY id
