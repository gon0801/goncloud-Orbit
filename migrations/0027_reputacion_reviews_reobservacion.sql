-- REPUTACION 01 XR-1 (hallazgo 4 Kimi, ronda 1) -- re-observacion de reviews.
--
-- Mismo defecto que 0026 corrigio en meli_question: el UNIQUE
-- (platform, review_external_id) congelaba `publicada` en la primera
-- escritura; si MeLi modera una review, la tabla seguiria diciendo
-- publicada=TRUE y alimentaria `resena_1` (acta §2 exige rate==1 AND
-- published). Con observed_at en la llave, cada corrida deja la
-- observacion actual; lectores con DISTINCT ON mas reciente.
--
-- Solo DROP/ADD CONSTRAINT. No re-runnable.

BEGIN;

ALTER TABLE review_event DROP CONSTRAINT review_event_anti_duplicado;
ALTER TABLE review_event ADD CONSTRAINT review_event_anti_duplicado
    UNIQUE (platform, review_external_id, observed_at);

COMMENT ON CONSTRAINT review_event_anti_duplicado ON review_event IS
    'XR-1.4: re-observacion por review (append-only); publicada actual '
    'es la fila mas reciente, no la primera. Igual que 0026.';

COMMIT;
