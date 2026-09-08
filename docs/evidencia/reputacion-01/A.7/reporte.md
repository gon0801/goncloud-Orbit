# REPUTACION 01 / A.7 — Despliegue (lead, 2026-09-08)

## DoD

- ruff + suite (1683 passed local) + CI master success: cumplido.
- Backup: diario `orbit_2026-09-08/` (03:30) + `preA7_schema_<STAMP>.sql`
  (staging + verificado: no-vacio + testigo + marcador). Cumplido.
- Migracion: 0024-0027 aplicadas en vivo (`-1`, warnings doble-BEGIN
  benignos); 5 tablas + 8 triggers + GRANTs (incl. UPDATE por columna
  solo app_decide) + UNIQUEs 0026/0027 verificados. Cumplido.
- Codigo: master copiado (md5 idénticos), `Recreated`, smoke health +
  /reputacion + API 200, bind loopback. Cumplido.
- Cron en DEPLOY.md: seccion reputacion (migraciones, secretos, mount
  rw, crons, reversa, pendiente). Cumplido.
- Smoke GET con datos: 65 listings + cuenta + 1 pendiente + 4 alertas
  + 20 reviews. Cumplido.

## Hallazgos del deploy (operativos)

- H-A7-1 (major, CORREGIDO PR #212): SELECT desnudo + prod sin
  autocommit = tx implicita que revertia sello+hechos en close()
  (run 119 huerfano: ok/finished/rows NULL). Tests usaban
  autocommit=True y no lo atraparon. Fix: tx propia y corta +
  2 tests prod-like con discriminancia (2 failed sin fix).
  Validado en prod: run 120 (402) sellado failed. 119 queda como
  evidencia historica.
- H-A7-2 (BLOQUEO EXTERNO, dueno): Apify FREE agotada ($0.000017,
  `not-enough-usage-to-run-paid-actor`). Manual Amazon + cron
  semanal pendientes de subir plan (autorizado). Timeout 300s vs
  518 URLs sin medir: con credito, si excede, lotes (no
  implementado a ciegas).
- H-A7-3 (diseno, go dueno): secrets/ `:ro` -> `:rw` (refresh
  OAuth MeLi rota y reescribe; sin rw el cron muere al 2o dia).
  Respaldo compose; reversa documentada en DEPLOY.md.
- H-A7-4 (observacion): 100 idempotentes/corrida MeLi (determinista,
  ambas corridas): reviews compartidas entre items con variaciones
  colisionan en UNIQUE (platform, review_external_id,
  observed_at) — dedupe funcionando, atribucion = primer item.
  Inferido de 48/65 items con variaciones (0.1); candidato a
  follow-up (UNIQUE + external_id), fuera de release.
- `meli_tokens.json` sin client (formato OAuth crudo): merge de
  MELI_CLIENT_ID/SECRET desde accounting.env con go dueno
  (backup .bak). `apify_token.json` creado desde APIFY_TOKEN.
- MeLi estreno: runs 117/118 ok (236 filas), refresh+guardar
  verificado (mtime), 4 alertas abiertas reales.
- Crons: meli 09:30 + alertas 10:30 instalados (respaldo crontab,
  accounting byte-igual); amazon comentado pendiente credito.

## Reversa

Acta §8.4 + DEPLOY.md (deshabilitar jobs, conservar datos/pantalla).
