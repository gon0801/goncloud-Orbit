-- ---------------------------------------------------------------------------
-- SP-API 01 A.5 ronda review (dueno) — indice para /salud.
--
-- /salud hace 24 Seq Scans por carga (3 consultas x 4 fuentes x 2
-- plataformas) sobre ingest_run, append-only y sin purga (D7). Va en una
-- 0037 NUEVA y no dentro de 0036: 0036 ya estaba mergeada en origin/master
-- (PR #246) cuando se pidio el indice y su aplicacion en produccion no se
-- puede verificar desde aqui — editar una migracion mergeada romperia
-- cualquier entorno que ya la hubiera aplicado. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

CREATE INDEX ingest_run_source_platform_id_idx ON ingest_run (source, platform, id DESC);

COMMIT;
