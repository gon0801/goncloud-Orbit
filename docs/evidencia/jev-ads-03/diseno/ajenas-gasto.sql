-- JEV ADS 03 diseno: SOLO LECTURA (orbit_read). Gasto de las senales vigentes sin venta, por lectura.
-- Cada senal trae el gasto de su ventana de cortes (ventana_inicio a ventana_fin).
BEGIN READ ONLY;
SELECT s.plataforma, s.lectura, count(*) n, round(sum(s.gasto),2) gasto, min(s.ventana_inicio) desde, max(s.ventana_fin) hasta
FROM jev_senal_vigente v JOIN jev_senal s ON s.id=v.senal_id
WHERE v.vigente AND s.ordenes = 0 GROUP BY 1,2 ORDER BY 1,2;
ROLLBACK;
