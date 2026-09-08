# REPUTACION 01 / A.2 — Ingesta snapshots (implementado por el lead)

Fecha: 2026-09-08. El brief GLM (`plans/brief-reputacion-01-A2-glm.md`,
PR #201) quedo sin efecto: el dueno pidio implementacion del lead.

## Decisiones

- D-LEAD-A2-1 (adenda acta 0.5 §11, aprobada): migracion 0025 suelta
  las 4 FKs a `listing` (48/65 items MeLi multi-SKU: sin listing
  unico). Conciliacion en codigo testeado, patron 0022.
- D-LEAD-A2-2: "UNA transaccion" = hechos en una tx; `ingest_run` se
  abre/sella fuera para dejar evidencia del fallo (patron
  disponibilidad). El test de corte verifica cero filas de hechos +
  run failed.
- D-LEAD-A2-3: `review_event` MeLi entra en A.2 (mismo endpoint que
  el rating; separarlo seria artificial). A.4 = seller/preguntas.
- D-LEAD-A2-4: CLI `reputacion snapshot` con args en argparse +
  `ejecuta_snapshot()`; `transport` inyectable existe SOLO para
  tests (documentado en firma).
- D-LEAD-A2-5: junglee sin reintentos (cada intento cobra); MeLi con
  backoff (gratis). Topes junglee ANTES de gastar; costo estimado
  declarado, no verificado a escala.

## Evidencia roja/verde

- `tests/test_reputacion.py`: 22 passed (7 planes, 7 clientes, 8
  integracion incl. corte-a-mitad, redaction, CLI real).
- Rojos en el camino (tests, no prod): mensaje "agotados"
  inalcanzable -> mensajes especificos ("401 tras refresh",
  "agotados N (ultimo CODE)"); FK B0HIJO/MLM en fixtures.
- Suite completa + ruff + pre-commit: ver PR.

## Spec del cron (para A.7, del acta §7 sin cambios)

- `30 9 * * *` job `reputacion:meli`:
  `docker exec orbit-app-1 python -m app.cli reputacion snapshot
  --fuente meli` (log `logs/reputacion-meli.log`).
- `0 10 * * 1` job `reputacion:amazon`:
  `docker exec orbit-app-1 python -m app.cli reputacion snapshot
  --fuente amazon` (log `logs/reputacion-amazon.log`).
- Precondicion: lead cablea `meli_tokens.json` (+client_id/secret
  para refresh) y `apify_token.json` en ORBIT_SECRETS_DIR.
- Ingesta manual por CLI antes de encender cada cron (A.7).

## Residuales

- Tarifa junglee $0.0025 estimada con 2 corridas chicas; la primera
  corrida real (518) fija el numero (D6 se reabre si excede $10).
- `productPageReviews` ignorado (hallazgo no verificado E/0.2).
