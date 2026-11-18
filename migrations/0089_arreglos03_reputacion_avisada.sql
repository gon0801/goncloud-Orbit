-- sonda-aplicada: SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'reputation_alert' AND column_name = 'avisada_at')
-- 0089 (ARREGLOS 03 D-K, estimacion.md F7): las alertas de reputacion de
-- Mercado Libre salen solas y una vez.
--
-- Hasta hoy una alerta solo viajaba en el resumen del ciclo de Ads (las de
-- las ultimas 24 h, una vez por plataforma) y, si el ciclo no corria, nunca
-- llegaba. Ahora la revision de las 10:30 (`reputacion alertas`) manda por
-- Telegram las que tienen avisada_at vacio y lo sella solo si el envio salio
-- (app/reputacion_alertas.py, avisa_pendientes).
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0089_reversa_arreglos03_reputacion_avisada.sql.

ALTER TABLE reputation_alert ADD COLUMN avisada_at TIMESTAMPTZ;
ALTER TABLE reputation_alert ADD CONSTRAINT reputation_alert_avisada_despues
    CHECK (avisada_at IS NULL OR avisada_at >= created_at);

-- Las 5 alertas que ya existen salieron en el resumen del ciclo del dia
-- siguiente (ciclos del 9-sep y del 18-oct, medidos): no se reenvian.
UPDATE reputation_alert SET avisada_at = created_at;

COMMENT ON COLUMN reputation_alert.avisada_at IS
  'Cuando salio su aviso por Telegram (revision de las 10:30). NULL = pendiente:'
  ' la revision lo reintenta al dia siguiente si el envio fallo.';
COMMENT ON TABLE reputation_alert IS
    'REPUTACION 01 A.1: MUTABLE solo para sellar resolved/resolved_at '
    '(patron decision_application 0001) y, desde ARREGLOS 03 D-K, avisada_at. '
    'Tipos D3/acta §2. NULL platform/external_id = alerta de cuenta.';

GRANT UPDATE (avisada_at) ON reputation_alert TO app_decide;
