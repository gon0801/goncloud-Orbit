# R13 — Costo de la vigencia en GET /cortes

**Regla.** Al leer la asesoria de un corte, la vigencia hace una consulta
(`ficha_vigente`) por cada listing con ficha de la revision, mas la relectura
del destino en harvest (R3).

**Medicion en prod (orbit_read, 2026-10-04 22:55 UTC, tras el deploy de 2.3).**

| Medida | Valor |
| --- | ---: |
| Cortes pendientes (`pending_veto` o `released`) | 0 |
| Revisiones Jev | 0 |
| Consultas de vigencia por GET /cortes hoy | 0 |

**Denominadores para cuando haya revisiones** (0.2 y la consulta de 3.1): un
harvest de MX cubre un grupo de origen de 208 productos; una revision suya
costaria hasta unas 208 consultas por GET mientras el corte este pendiente.
Los negativos de US cubren 91 productos.

**Umbral declarado.** Si la suma de listings con ficha de los cortes
pendientes que tienen revision pasa de 500 en un GET, se agrupa la vigencia
en una consulta por revision (`ficha_vigente` sobre `ANY(listings)`). Hasta
entonces la consulta por listing es la mas simple y su costo es acotado.

Consulta usada:

    WITH pendientes AS (
      SELECT q.id, q.decision_id, d.ad_entity_id AS grupo
        FROM apply_queue q JOIN decision d ON d.id = q.decision_id
       WHERE q.estado IN ('pending_veto', 'released')
    )
    SELECT count(*), count(DISTINCT grupo),
           coalesce(sum((SELECT count(DISTINCT pa.listing_id) FROM ad_entity pa
                          WHERE pa.parent_id = p.grupo AND pa.kind = 'product_ad')), 0),
           (SELECT count(*) FROM jev_revision)
      FROM pendientes p;

Resultado: `0 | 0 | 0 | 0`.
