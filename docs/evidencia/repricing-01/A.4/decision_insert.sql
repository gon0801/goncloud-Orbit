-- Ejecutado 2026-09-19 como app_decide. El trigger fijó decision_date y goal
-- desde el reloj UTC de la base y el precio_goal live vigente.
INSERT INTO precio_decision (
  listing_id, platform, resultado, motivo, product_id, canal, m_actual,
  p_actual, p_actual_currency, p_objetivo, p_objetivo_currency,
  p_aplicado, p_aplicado_currency,
  i_valor, i_currency, c_valor, c_currency, f_valor, f_currency,
  l_valor, l_currency, r_valor, r_currency,
  escenario_id, fee_observation_id, buy_box_is_own, mode, config_version_id
) VALUES
  (1213, 'amazon_mx', 'subir', NULL, 308, 'fba', 0.3495,
   988.0000, 'MXN', 988.0100, 'MXN', 988.0100, 'MXN',
   851.7241, 'MXN', 341.0000, 'MXN', 191.7600, 'MXN',
   0.0000, 'MXN', 21.2931, 'MXN', 9795, 9795, true, 'live', 20),
  (1284, 'amazon_mx', 'subir', NULL, 288, 'fba', 0.5289,
   1288.0000, 'MXN', 1288.0100, 'MXN', 1288.0100, 'MXN',
   1110.3448, 'MXN', 262.8200, 'MXN', 232.5500, 'MXN',
   0.0000, 'MXN', 27.7586, 'MXN', 9866, 9866, true, 'live', 20),
  (1295, 'amazon_mx', 'subir', NULL, 416, 'fba', 0.5919,
   699.0000, 'MXN', 699.0100, 'MXN', 699.0100, 'MXN',
   602.5862, 'MXN', 77.4700, 'MXN', 153.3900, 'MXN',
   0.0000, 'MXN', 15.0647, 'MXN', 9877, 9877, true, 'live', 20)
RETURNING id, listing_id, decision_date, goal, p_actual, p_aplicado, mode;
