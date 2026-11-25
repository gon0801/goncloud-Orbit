-- K.1: politicas de estimacion vigentes e historicas (sin credenciales).

-- == A. Politicas por universo [universo|formula|label|settings|valid_from|valid_to]
SELECT universo, formula_version, label, settings::text, valid_from, valid_to
  FROM estimacion_politica_version
 ORDER BY universo, valid_from;
