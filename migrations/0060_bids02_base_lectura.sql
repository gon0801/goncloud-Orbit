-- 0060: base de lectura de BIDS 02 (0.b): v_hoja_activa y v_cambio_bid.
--
-- v_hoja_activa = "hoja activa" (hoja, ad group y campana ENABLED) con su
-- tipo de campana. Hoy ese triple JOIN vive en _SQL_DECISORAS (cycle.py),
-- en v_entidad_inerte y en cada pantalla: una definicion, una fuente. El
-- tipo se decide primero por la campana (AUTO) y despues por la hoja, porque
-- las campanas automaticas tambien tienen product targets.
-- v_cambio_bid = historial de bids aplicados por hoja. Fuente unica de la
-- trayectoria del motor, la pantalla de keywords danadas y el tablero de
-- encogimiento. Dos origenes: bids del motor confirmados por un ciclo live
-- (los mismos filtros del cooldown de hoy) y reversas de bid confirmadas en
-- el ledger (regreso pedido por el dueno). `origen` distingue el regreso por
-- desplome (lo decide el motor, motivo congelado) del recorte o subida.
-- (Bloque 0060 de docs/evidencia/bids-02/diseno/datos.sql.)
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente (CREATE VIEW
-- pela, sin OR REPLACE), GRANT declarados y bloque DO que falla si falta o
-- sobra un privilegio. Sin tablas nuevas: no hay triggers append-only.

BEGIN;

-- "Hoja activa" (hoja, ad group y campana ENABLED) con su tipo de campana. Hoy ese triple JOIN vive en
-- _SQL_DECISORAS (cycle.py), en v_entidad_inerte y en cada pantalla. Una definicion, una fuente.
-- El tipo se decide primero por la campana (AUTO) y despues por la hoja, porque las campanas automaticas
-- tambien tienen product targets (las clausulas automaticas).
CREATE VIEW v_hoja_activa AS
SELECT k.id                AS hoja_id,
       k.platform,
       k.kind,
       k.parent_id         AS ad_group_id,
       ag.parent_id        AS campana_id,
       CASE
           WHEN sc.targeting_type = 'AUTO'      THEN 'automatica'
           WHEN k.kind = 'product_target'       THEN 'product_targeting'
           WHEN k.match_type = 'EXACT'          THEN 'exact'
           WHEN k.match_type = 'PHRASE'         THEN 'phrase'
           WHEN k.match_type = 'BROAD'          THEN 'broad'
       END                 AS tipo_campana,          -- NULL = no clasificable; la pantalla lo cuenta aparte
       sk.current_bid,
       sk.bid_currency
  FROM ad_entity k
  JOIN ad_entity ag       ON ag.id = k.parent_id AND ag.kind = 'ad_group'
  JOIN ad_entity c        ON c.id = ag.parent_id AND c.kind = 'campaign'
  JOIN ad_entity_state sk ON sk.ad_entity_id = k.id  AND sk.status = 'ENABLED'
  JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id AND sg.status = 'ENABLED'
  JOIN ad_entity_state sc ON sc.ad_entity_id = c.id  AND sc.status = 'ENABLED'
 WHERE k.kind IN ('keyword', 'product_target');

-- Historial de bids aplicados por hoja. Fuente unica de: trayectoria del motor (sustituye a
-- _SQL_EN_COOLDOWN para bids y a _SQL_ULTIMO_BID_APLICADO), pantalla de keywords danadas y tablero de
-- encogimiento. Dos origenes de filas:
--   (a) bids del motor confirmados por un ciclo live (los mismos filtros del cooldown de hoy);
--   (b) reversas de bid confirmadas en el ledger (regreso pedido por el dueno): el bid vuelve al old_value
--       de la decision revertida. Hoy estas reversas son invisibles para el cooldown y para D.2, y por eso
--       el motor vuelve a recortar al dia siguiente.
-- `origen` distingue el regreso por desplome (lo decide el motor, motivo congelado) del recorte o subida.
CREATE VIEW v_cambio_bid AS
SELECT d.ad_entity_id                         AS hoja_id,
       da.confirmed_at                        AS confirmado_el,
       d.old_value                            AS bid_antes,
       d.new_value                            AS bid_despues,
       d.value_currency                       AS moneda,
       CASE WHEN d.inputs->>'motivo' = 'regreso_por_desplome'
            THEN 'regreso_por_desplome' ELSE 'motor' END AS origen,
       d.id                                   AS decision_id
  FROM decision_application da
  JOIN decision d         ON d.id = da.decision_id AND d.kind = 'bid'
  JOIN optimizer_cycle oc ON oc.id = da.applied_cycle_id AND oc.mode = 'live'
 WHERE da.verify_ok IS TRUE
   AND d.old_value IS NOT NULL AND d.new_value IS NOT NULL AND d.new_value <> d.old_value
UNION ALL
SELECT d.ad_entity_id,
       a.finished_at,                         -- sello del resultado en el ledger (0002_apply.sql:305)
       NULL::numeric,                         -- el bid previo al regreso lo deriva el lector: es el
                                              -- bid_despues del cambio anterior de esa hoja
       d.old_value,
       d.value_currency,
       'regreso_del_dueno',
       d.id
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id AND d.kind = 'bid'
 WHERE a.tipo = 'reversa' AND a.resultado = 'ok';
-- Nota de replay: esta vista alimenta `caso.trayectoria`, que se CONGELA entera en decision.inputs. El
-- replay nunca vuelve a leer la vista.

COMMENT ON VIEW v_hoja_activa IS
  'BIDS 02 0.b (0060): una fila por hoja (keyword/product_target) con hoja, '
  'ad group y campana ENABLED, mas tipo_campana. UNICA fuente de "hoja '
  'activa": sustituye al triple JOIN de _SQL_DECISORAS y de v_entidad_inerte. '
  'tipo_campana NULL = hoja no clasificable (la pantalla la cuenta aparte).';

COMMENT ON VIEW v_cambio_bid IS
  'BIDS 02 0.b (0060): historial UNION de bids por hoja. Rama motor: bids '
  'confirmados por un ciclo live (filtros del cooldown). Rama regreso_del_'
  'dueno: reversas ok del ledger, con bid_despues = old_value revertido (el '
  'bid previo al regreso lo deriva el lector del cambio anterior).';

GRANT SELECT ON v_hoja_activa TO app_decide, app_read, app_admin;
GRANT SELECT ON v_cambio_bid TO app_decide, app_read, app_admin;

DO $$
BEGIN
    IF NOT has_table_privilege('app_decide', 'v_hoja_activa', 'SELECT')
       OR NOT has_table_privilege('app_read', 'v_hoja_activa', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'v_hoja_activa', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'v_cambio_bid', 'SELECT')
       OR NOT has_table_privilege('app_read', 'v_cambio_bid', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'v_cambio_bid', 'SELECT')
       OR has_table_privilege('app_decide', 'v_hoja_activa', 'INSERT')
       OR has_table_privilege('app_decide', 'v_hoja_activa', 'UPDATE')
       OR has_table_privilege('app_decide', 'v_hoja_activa', 'DELETE')
       OR has_table_privilege('app_read', 'v_hoja_activa', 'INSERT')
       OR has_table_privilege('app_read', 'v_hoja_activa', 'UPDATE')
       OR has_table_privilege('app_read', 'v_hoja_activa', 'DELETE')
       OR has_table_privilege('app_admin', 'v_hoja_activa', 'INSERT')
       OR has_table_privilege('app_admin', 'v_hoja_activa', 'UPDATE')
       OR has_table_privilege('app_admin', 'v_hoja_activa', 'DELETE')
       OR has_table_privilege('app_decide', 'v_cambio_bid', 'INSERT')
       OR has_table_privilege('app_decide', 'v_cambio_bid', 'UPDATE')
       OR has_table_privilege('app_decide', 'v_cambio_bid', 'DELETE')
       OR has_table_privilege('app_read', 'v_cambio_bid', 'INSERT')
       OR has_table_privilege('app_read', 'v_cambio_bid', 'UPDATE')
       OR has_table_privilege('app_read', 'v_cambio_bid', 'DELETE')
       OR has_table_privilege('app_admin', 'v_cambio_bid', 'INSERT')
       OR has_table_privilege('app_admin', 'v_cambio_bid', 'UPDATE')
       OR has_table_privilege('app_admin', 'v_cambio_bid', 'DELETE') THEN
        RAISE EXCEPTION '0060: privilegios de v_hoja_activa/v_cambio_bid invalidos';
    END IF;
END
$$;

COMMIT;
