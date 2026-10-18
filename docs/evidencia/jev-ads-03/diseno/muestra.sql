-- JEV ADS 03 diseno: SOLO LECTURA (orbit_read).
BEGIN READ ONLY;
WITH base AS (
  SELECT s.*, ag.name AS grupo, cp.name AS campana, r.miembros AS rmiembros
  FROM jev_senal_vigente v JOIN jev_senal s ON s.id=v.senal_id
  JOIN ad_entity ag ON ag.id=s.ad_group_id LEFT JOIN ad_entity cp ON cp.id=ag.parent_id
  JOIN jev_roster r ON r.sha256=s.roster_sha256
  WHERE v.vigente AND v.lectura IN ('ajena','relevante_sin_venta','vende_en_otro'))
SELECT json_build_object(
  'id', b.id, 'plataforma', b.plataforma, 'lectura', b.lectura, 'termino', b.termino,
  'grupo', b.grupo, 'campana', b.campana, 'n_productos', b.miembros, 'clics', b.clics,
  'gasto', b.gasto::text, 'moneda', b.moneda, 'creada', b.created_at,
  'productos', (SELECT coalesce(json_agg(DISTINCT p.name), '[]') FROM jsonb_array_elements(b.rmiembros->'censo') c
                JOIN product p ON p.id=(c->>1)::bigint),
  'otros', (SELECT coalesce(json_agg(json_build_object('grupo', g.name, 'campana', gc.name, 'ordenes', (o->>'ordenes')::int) ORDER BY (o->>'ordenes')::int DESC), '[]')
            FROM jsonb_array_elements(b.otros_que_venden) o JOIN ad_entity g ON g.id=(o->>'ad_group_id')::bigint LEFT JOIN ad_entity gc ON gc.id=g.parent_id)
)::text FROM base b;
ROLLBACK;
