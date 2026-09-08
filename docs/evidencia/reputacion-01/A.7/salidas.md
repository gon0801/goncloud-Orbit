# E/A.7 salidas — deploy 2026-09-08 (lead, goncloud; sin secretos)

## 1-5. Esquema (verificacion.sql)

Tablas (5): `meli_question, reputation_alert, reputation_snapshot,
review_event, seller_reputation_snapshot`.

Triggers: 8. GRANTs tabla: app_admin SELECT; app_decide INSERT+SELECT;
app_ingest SELECT; app_read SELECT. UPDATE columna: app_decide sobre
resolved/resolved_at (+ orbit = dueno, normal). UNIQUEs:
`meli_question_anti_duplicado`, `review_event_anti_duplicado`.

## 6. Runs

```text
117|t|236
118|t|236
119|||            <- huerfano pre-fix tx (H-A7-1; inerte, ver reporte)
120|f|0|fallo la corrida   <- 402 sellado tras fix (validacion prod)
```

Filtro 119: ningun lector fuera de `app/reputacion*.py` lee runs con
`source LIKE 'reputacion_%'` (grep: solo `costs.py` con su propio flujo).
Inerte para dashboards y conteos.

## 7. Conteos MeLi

```text
snapshot|65
review|119
seller|1
question|51
```

## Alertas abiertas tras estreno (4)

```text
rating_bajo/aviso/meli/MLM1890586139: rating 3.8 < 4.2
resena_1/critica/meli/MLM2154113682: 1 estrella nueva: review 2643084807
resena_1/critica/meli/MLM1890605671: 1 estrella nueva: review 2996270093
resena_1/critica/meli/MLM1890586139: 1 estrella nueva: review 742283124
```

## Smoke

```text
health=200 reputacion=200 api=200
{"listings":[],"...,"amazon_texto":"sin-verificar"}            <- pre-ingesta
listings: 65 cuenta: True pend: 1 alertas: 4 reviews: 20       <- post-MeLi
```

Bind: `127.0.0.1:8010` + `10.13.13.1:8010`, nunca `*:8010`.
App: `Recreated` (deploy real). db: 11 dias up (no recreada).

## Codigo master == server (md5)

```text
763a96f2 reputacion.py | 479b7b0b api_reputacion.py | e5c1f47a reputacion.html
f438c047 ui.py (server pre-A.6: db28437d)
```

`api_fabrica.py` server == master (deploy solo activo reputacion).

## CI y suite

- CI master post-#211: success (run 34184878502).
- CI PR #212 (fix tx): success (run 34187366311).
- CI PR #213 (docs A.7): success (run 34187759854).
- Suite local: 1683 passed, 1 skipped. ruff limpio.

## Backup y crons

- Diario `orbit_2026-09-08/` 03:30 (dump 1.8MB + globals).
- `preA7_schema_20260908-035509.sql` (246KB, testigo + marcador OK).
- Respaldo app: `app.bak-predeploy-A7-20260908-035653`.
- Respaldo compose: `docker-compose.yml.bak-A7-<STAMP>`; crontab:
  `archive/crontab-gon.<STAMP>`; accounting byte-igual verificado.
- Mejora MeLi: `{"fuente": "meli", "ok": true, "run_ids": {"meli": 117},
  "filas_insertadas": 236, "filas_idempotentes": 100, "skips": {},
  "costo_usd": null}`; re-corrida 118 identica (bitemporal por diseno).
- Amazon: `junglee run-sync -> 402` (`not-enough-usage-to-run-paid-actor`,
  remaining $0.000017) + `The read operation timed out` en corrida con
  credito residual (timeout 300s vs 518 URLs sin medir a escala).

## Follow-ups abiertos (no bloquean cierre)

- REP-FOLLOW-1 (H-A7-4): UNIQUE review_event + external_id
  (atribucion multi-item; hoy = primer item).
- REP-FOLLOW-2: manual Amazon + cron semanal tras subir plan Apify
  (linea comentada en crontab; ~$1.30/corrida).
- REP-FOLLOW-3: lotes junglee si 518 URLs exceden 300s (medir con
  credito; no implementar a ciegas).
