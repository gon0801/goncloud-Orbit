-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0084 (ARREGLOS 01 Q.2).
--
-- Devuelve el CHECK lectura_veredicto de impulso_lectura al vocabulario de la
-- 0065 y no bloquea el rollback. Las lecturas con veredicto 'desarmado' solo
-- las escribe el codigo de la 0084 y el codigo anterior no sabe mapearlas
-- (frase_final lanzaria KeyError), asi que se sacan de la tabla viva; antes de
-- borrarlas se copian integras a impulso_lectura_desarmado_archivo, para no
-- perder auditoria.
--
-- Vuelta atras: despues de volver a aplicar la 0084 (o sea, al desplegar Q de
-- nuevo), SELECT restaura_impulso_lectura_desarmado(); repone las lecturas
-- archivadas en impulso_lectura sin duplicar (ON CONFLICT DO NOTHING) y
-- devuelve cuantas inserto. El archivo se conserva.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). Si algo falla, no se borra nada. No
-- re-runnable.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS impulso_lectura_desarmado_archivo (
    LIKE impulso_lectura INCLUDING DEFAULTS,
    archivado_el TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (impulso_id, corte)
);

COMMENT ON TABLE impulso_lectura_desarmado_archivo IS
    'ARREGLOS 01 Q.2: archivo de las lecturas con veredicto desarmado que la '
    'reversa de la 0084 saca de impulso_lectura para que el codigo anterior no '
    'las lea. restaura_impulso_lectura_desarmado() las repone tras reaplicar la 0084.';

INSERT INTO impulso_lectura_desarmado_archivo
    (impulso_id, corte, leido_el, dias_observados, impresiones, clics, gasto,
     gasto_moneda, pedidos, veredicto, es_final, hecho)
SELECT impulso_id, corte, leido_el, dias_observados, impresiones, clics, gasto,
       gasto_moneda, pedidos, veredicto, es_final, hecho
  FROM impulso_lectura
 WHERE veredicto = 'desarmado'
ON CONFLICT (impulso_id, corte) DO NOTHING;

ALTER TABLE impulso_lectura DISABLE TRIGGER impulso_lectura_sin_update_delete;

DELETE FROM impulso_lectura WHERE veredicto = 'desarmado';

ALTER TABLE impulso_lectura ENABLE TRIGGER impulso_lectura_sin_update_delete;

ALTER TABLE impulso_lectura DROP CONSTRAINT lectura_veredicto;
ALTER TABLE impulso_lectura ADD CONSTRAINT lectura_veredicto CHECK (veredicto IN
    ('en_curso', 'vende', 'clics_sin_ventas', 'impresiones_sin_clics', 'sin_impresiones'));

CREATE OR REPLACE FUNCTION public.restaura_impulso_lectura_desarmado() RETURNS bigint
LANGUAGE sql
SET search_path = pg_catalog, public, pg_temp
AS $$
    WITH ins AS (
        INSERT INTO public.impulso_lectura
            (impulso_id, corte, leido_el, dias_observados, impresiones, clics, gasto,
             gasto_moneda, pedidos, veredicto, es_final, hecho)
        SELECT impulso_id, corte, leido_el, dias_observados, impresiones, clics, gasto,
               gasto_moneda, pedidos, veredicto, es_final, hecho
          FROM public.impulso_lectura_desarmado_archivo
        ON CONFLICT (impulso_id, corte) DO NOTHING
        RETURNING 1
    )
    SELECT count(*) FROM ins
$$;

COMMENT ON FUNCTION public.restaura_impulso_lectura_desarmado() IS
    'ARREGLOS 01 Q.2: vuelta atras de la reversa de la 0084. Repone en '
    'impulso_lectura las lecturas archivadas con veredicto desarmado y devuelve '
    'cuantas inserto (idempotente). Exige el CHECK con desarmado: reaplica la 0084 antes.';
