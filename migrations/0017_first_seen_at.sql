-- =============================================================================
--  BIDS 01 · 2.1 — sellar la edad de las entidades (H1).
--
--  `ad_entity` no tenia ninguna columna de tiempo: la edad de una keyword
--  era INDERIVABLE (ad_entity_state.synced_at se sobrescribe en cada sync;
--  ingest_run de estructura solo llega al 2026-08-22; 133 de 166 candidatas
--  MX no tienen NI UNA metrica). Sin edad sellada, el harvest de ORBIT 05
--  crearia un EXACT que a las 24 h seria peso_muerto archivable: trampa viva.
--
--  `first_seen_at` se puebla en el INSERT del upsert de estructura y JAMAS
--  en el UPDATE (el DO UPDATE solo toca `name`). El DEFAULT now() permanente
--  cumple tres funciones: backfill de las existentes a la hora de ESTA
--  migracion (una sola txn = un solo valor), red para seeds de test que
--  insertan directo, y red para escritores futuros que olviden la columna.
--
--  PISO, NO VERDAD: toda fila anterior a esta migracion amanece con la hora
--  de la migracion aunque exista desde antes (las 133 sin metricas podrian
--  ser mas viejas). tools/archiva_inertes.py excluye del plan lo mas joven
--  que hoy - N con este valor: para lo viejo, pecar de joven es lo seguro
--  (una candidata vieja que espera N dias no pierde nada; una recien creada
--  archivada seria irreversible).
-- =============================================================================

ALTER TABLE ad_entity
  ADD COLUMN first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now();

COMMENT ON COLUMN ad_entity.first_seen_at IS
  'BIDS 01 2.1 (H1): primera vez que el sync vio la entidad. Se fija en el '
  'INSERT y JAMAS en el UPDATE. Las filas anteriores a la migracion 0017 '
  'traen la hora de la migracion: es un PISO, no la verdad (podrian ser mas '
  'viejas). tools/archiva_inertes.py excluye del plan lo mas joven que '
  'hoy - N dias con este valor.';
