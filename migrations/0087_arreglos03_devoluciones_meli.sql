-- sonda-aplicada: SELECT to_regclass('public.arreglos03_devoluciones_meli_ids') IS NOT NULL
-- 0087 (ARREGLOS 03 D-H.4, ingesta.md F5): las devoluciones de MeLi traen su
-- etiqueta.
--
-- MeLi no manda fee_category en sus devoluciones: quedaban con fee_type NULL,
-- la guarda fees_sin_tipo de las vistas de margen las contaba y el margen de
-- MeLi salia vacio siempre. Una devolucion sin categoria es una devolucion:
-- la publicidad nunca llega como refund (entra como fee con categoria ads).
-- Se corrige el dato una sola vez, con reversa, y la lista de IDs tocados
-- queda en una tabla privada de la migracion que nace en la MISMA transaccion
-- que el UPDATE: su existencia permite comprobar el despliegue incluso si
-- habia cero filas pendientes. La correccion solo toca filas de MeLi: Amazon
-- ya no tiene ninguna sin etiqueta.
--
-- El trigger ledger_solo_reatribuye (0085) bloquea todo UPDATE de ledger_event
-- salvo la reatribucion de product_id: se apaga solo durante esta correccion y
-- se reenciende dentro de la misma transaccion.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0087_reversa_arreglos03_devoluciones_meli.sql.

CREATE TABLE public.arreglos03_devoluciones_meli_ids (id bigint PRIMARY KEY);
REVOKE ALL ON public.arreglos03_devoluciones_meli_ids FROM PUBLIC, app_read, app_ingest, app_decide, app_admin;
ALTER TABLE ledger_event DISABLE TRIGGER ledger_solo_reatribuye;
WITH corregidas AS (
    UPDATE ledger_event SET fee_type = 'refund'
    WHERE platform = 'meli' AND kind = 'refund' AND fee_type IS NULL
    RETURNING id
)
INSERT INTO public.arreglos03_devoluciones_meli_ids (id) SELECT id FROM corregidas;
ALTER TABLE ledger_event ENABLE TRIGGER ledger_solo_reatribuye;
