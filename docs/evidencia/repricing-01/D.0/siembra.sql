-- D.0 (REPRICING 01): siembra de las 20 claves `precio_*` de la tabla de
-- umbrales del plan v1.3, con su valor inicial, como una `config_version`
-- NUEVA que copia la vigente (append-only; patrón de `docs/DEPLOY.md`
-- §D.1.1). Copiar es obligatorio: una fila con solo las claves `precio_*`
-- dejaría sin caps a Ads y su `apply_quota_state` reventaría fail-closed.
--
-- Son las 16 filas de la tabla menos las cinco `precio_envio_*` (las siembra
-- E.3 cuando E.2 las selle); las filas con barra cuentan cada clave.
--
-- Espera la variable de psql `go` (el go literal del dueño, que queda como
-- `label`). Falla ruidoso, sin escribir, si la vigente ya trae alguna clave
-- `precio_*`: no se siembra dos veces.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1) v,
               jsonb_object_keys(v.settings) AS k
         WHERE k LIKE 'precio\_%'
    ) THEN
        RAISE EXCEPTION 'D.0: la config vigente ya trae claves precio_*; no se siembra dos veces';
    END IF;
END
$$;

INSERT INTO config_version (label, settings)
SELECT :'go',
       settings || jsonb_build_object(
           'precio_caida_ventas_pct',               0.40,
           'precio_senal_dias',                     3,
           'precio_u60_min',                        20,
           'precio_fechas_excluidas',               '[]'::jsonb,
           'precio_escalon_max_pct',                0.10,
           'precio_movimiento_min_pct',             0.01,
           'precio_movimiento_min_abs_mxn',         1.00,
           'precio_movimiento_min_abs_usd',         0.10,
           'precio_tolerancia',                     0.005,
           'precio_dias_entre_cambios',             7,
           'precio_cap_amazon_mx',                  5,
           'precio_cap_amazon_us',                  5,
           'precio_cap_meli',                       5,
           'precio_goal_min_pct',                   0.10,
           'precio_goal_max_pct',                   0.60,
           'precio_freno_cambios',                  3,
           'precio_aviso_dias_sin_evaluar',         3,
           'precio_freno_dias_error',               3,
           'precio_divergencia_max_pct',            0.01,
           'precio_catalogo_max_dias_sin_reportar', 3
       )
  FROM config_version
 ORDER BY id DESC
 LIMIT 1
RETURNING id, label;

COMMIT;
