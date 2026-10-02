# E/D.4 — deploy y remediación del job 2 (2026-10-02 UTC)

## Deploy del código (dueño con `!`, `deploy-codigo-8d4890a.sh`)

- SHA `8d4890a` = `edd154e` (#378) + #379 (solo `plans/manifest.json`, que
  devolvió master a verde: batería completa 3485 passed). CI success.
- Preflight con `orbit_read`: `ok|0|0|0|0` (sin ciclo `running`, sin ingesta a
  medias, sin harvest `released`/`applying`, sin job en vuelo). Las filas
  `pending_veto` 17 y 18 se dejaron: el ciclo las aplica con el código nuevo.
- Producción antes = `2d29ef2` (md5 de los 5 archivos que cambian; lectura previa
  del lead: 122/122 archivos de `app/` iguales a master pre-#378).
- Respaldo `/mnt/data/appdata/orbit/predeploy-20261002-0355/`.
- md5 OK: `app/apply_harvest.py`, `app/apply_harvest_reconciliacion.py`,
  `app/cycle.py`, `app/optimizer/harvest_destino.py`, `app/api_dashboard.py`.
- Imagen `sha256:f84b0a35…` → `sha256:01e55fb1…`; `/health` ok, `/salud` 200,
  `/cortes` 200; import en el contenedor `True True` (firma con
  `origen_ad_group_external` y `plan_reversa_origen_harvest`).

## Remediación (go literal del dueño: «D.4 archivar negativo origen job 2»)

Dry-run del lead tras el deploy (cero HTTP):

```text
job: 2 platform: amazon_mx termino: arras matrimoniales de oro decision: 2311
alcance: solo negativo de origen (la keyword destino queda intacta)
[pendiente] negative negative ad_group=272585315669297 id=92333897493675
pendientes: 1 huella: afc9ef3591ca6200
```

Corrida real del dueño con `!` (`--job 2 --solo-origen --acepto-mutacion-real
--esperado 1 --huella afc9ef3591ca6200 --go 'D.4 archivar negativo origen job 2'`):
`reversa: ok`.

Ledger (`orbit_read`): `apply_attempt` 384, `seq` 3, tipo `reversa`,
`quota_cobrada = false`, `resultado = ok`, 2026-10-02 04:10:54 UTC, request
`negativeKeywordIdFilter` `92333897493675`, ack `success`. 208/209 intactas.
Re-dry-run: `pendientes: 0` (idempotente).

LIST de Amazon (dueño, `docs/evidencia/orbit-05/list-negativos.py` y
`list-readback.py`):

```text
{"keywordId": "92333897493675", "keywordText": "arras matrimoniales de oro", "matchType": "NEGATIVE_EXACT", "state": "ARCHIVED", "campaignId": "97835222467967", "adGroupId": "272585315669297"}
{"keywordId": "197174507964917", "keywordText": "arras matrimoniales de oro", "matchType": "EXACT", "state": "ENABLED", "bid": 2.5, "campaignId": "97835222467967", "adGroupId": "272585315669297"}
```

Los negativos de los jobs 3 y 4 (`238992858651508`, `123271341601901`) siguen
ENABLED en sus propios ad groups, como corresponde. La EXACT de «arras
matrimoniales de oro» ya puede servir en su búsqueda exacta.
