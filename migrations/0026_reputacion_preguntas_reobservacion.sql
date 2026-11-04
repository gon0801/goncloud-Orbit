-- REPUTACION 01 A.4 (D-LEAD-A4-1) -- re-observacion de preguntas.
--
-- El UNIQUE de 0024 (external_id, question_external_id) congelaba el
-- estado de la primera escritura: una pregunta respondida seguiria
-- UNANSWERED para siempre (DO NOTHING ignora el cambio). Con
-- observed_at en la llave, cada corrida deja una fila por pregunta
-- con su estado actual (append-only real); "pendientes" = DISTINCT ON
-- mas reciente por pregunta WHERE estado = 'UNANSWERED'.
-- 51 preguntas/dia es volumen trivial.
--
-- Solo DROP/ADD CONSTRAINT. No re-runnable.

BEGIN;

ALTER TABLE meli_question DROP CONSTRAINT meli_question_anti_duplicado;
ALTER TABLE meli_question ADD CONSTRAINT meli_question_anti_duplicado
    UNIQUE (external_id, question_external_id, observed_at);

COMMENT ON CONSTRAINT meli_question_anti_duplicado ON meli_question IS
    'D-LEAD-A4-1: re-observacion diaria por pregunta (append-only); el '
    'estado actual es la fila mas reciente, no la primera.';

COMMIT;
