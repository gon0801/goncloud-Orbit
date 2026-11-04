-- ---------------------------------------------------------------------------
-- SP-API 01 A.5 ronda review (dueno) — indice para /salud + candado del
-- INSERT de ingest_run.platform.
--
-- /salud hace 24 Seq Scans por carga (3 consultas x 4 fuentes x 2
-- plataformas) sobre ingest_run, append-only y sin purga (D7). Va en una
-- 0037 NUEVA y no dentro de 0036: 0036 ya estaba mergeada en origin/master
-- (PR #246) cuando se pidio el indice y su aplicacion en produccion no se
-- puede verificar desde aqui — editar una migracion mergeada romperia
-- cualquier entorno que ya la hubiera aplicado. No re-runnable.
--
-- Por esa MISMA razon el candado del INSERT vive aqui y no en 0036
-- (hallazgo CodeRabbit PR #247): un entorno que ya aplico 0036 jamas
-- volveria a correr su bloque DO, asi que el candado alli no protegeria
-- nada. Aqui si: 0037 no se ha aplicado en ningun entorno.
-- ---------------------------------------------------------------------------

BEGIN;

CREATE INDEX ingest_run_source_platform_id_idx ON ingest_run (source, platform, id DESC);

-- Candado del privilegio que las 4 ingestas REALMENTE ejercen: el INSERT de
-- platform al abrir el run. Hoy lo cubre el GRANT INSERT a nivel TABLA de
-- 0001 (l.1467), que alcanza a las columnas nuevas; el UPDATE de esa misma
-- tabla, en cambio, es POR COLUMNA — y esa asimetria fue el bug de
-- produccion 0033->0034 (corridas 145 y 147 abiertas). Si alguien acota el
-- INSERT por columna, esto truena aqui en vez de tumbar las 4 ingestas.
DO $$
BEGIN
    IF NOT has_column_privilege('app_ingest', 'ingest_run', 'platform', 'INSERT') THEN
        RAISE EXCEPTION '0037: app_ingest sin INSERT en ingest_run.platform';
    END IF;
END $$;

COMMIT;
