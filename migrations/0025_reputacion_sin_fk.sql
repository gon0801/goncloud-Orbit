-- REPUTACION 01 A.2 (D-LEAD-A2-1) -- soltar FKs a listing.
--
-- 0.1 + E/A.2: un item MeLi mapea a N SKUs Odoo (48/65 items son
-- publicaciones con variaciones), asi que NO existe product_id unico
-- para crearle listing; con las FKs de 0024 la ingesta MeLi no podria
-- escribir ni una fila. Amazon sigue conciliando contra listing,
-- pero EN CODIGO (testeado en tests/test_reputacion.py), patron
-- 0022/disponibilidad: texto libre + plan filtra + skip contado.
-- Igual que el sistema viejo (own_items sin FK a productos).
--
-- Adenda al acta 0.5 §5 aprobada por el dueno 2026-09-08.
-- Solo DROP CONSTRAINT. No re-runnable.

BEGIN;

ALTER TABLE reputation_snapshot DROP CONSTRAINT reputation_snapshot_listing_existe;
ALTER TABLE review_event DROP CONSTRAINT review_event_listing_existe;
ALTER TABLE meli_question DROP CONSTRAINT meli_question_listing_existe;
ALTER TABLE reputation_alert DROP CONSTRAINT reputation_alert_listing_existe;

COMMENT ON COLUMN reputation_snapshot.external_id IS
    'D-LEAD-A2-1: sin FK (0025). Amazon = ASIN conciliado en codigo '
    'contra listing; MeLi = item MLM conciliado contra API + mapa '
    'bridge (un item tiene N SKUs: no hay listing unico). Lo ambiguo '
    'se omite y se cuenta, nunca se rellena.';
COMMENT ON COLUMN review_event.external_id IS
    'D-LEAD-A2-1: sin FK (0025). Misma conciliacion que '
    'reputation_snapshot.';
COMMENT ON COLUMN meli_question.external_id IS
    'D-LEAD-A2-1: sin FK (0025). Item MLM conciliado contra API + '
    'mapa bridge.';
COMMENT ON COLUMN reputation_alert.external_id IS
    'D-LEAD-A2-1: sin FK (0025). NULL = alerta de cuenta.';

COMMIT;
