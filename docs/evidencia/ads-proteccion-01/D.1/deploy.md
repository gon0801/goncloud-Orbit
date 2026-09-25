# D.1 — decision_sin_aplicar: merge y deploy

## Merge

- PR #347 (`fix(test)`: sellar la ruta de descarte de C.5 en `SUPERFICIE_ADS_OPTIMIZER`), merge `cd6e63d`. Master estaba rojo en la bateria completa desde `ad79eeb` (run 36167946686, `1 failed, 3432 passed`).
- PR #345 (D.1), merge `03faa24`. Implemento Kimi; review del lead en dos rondas (r1 `REQUEST_CHANGES`: corte shadow en ciclo live huerfano para siempre, reproducido; r2 `APPROVE`). Renombre de la migracion 0043 → 0044 por choque con `0043_ads_propuesta_descarte_admin.sql` (C.5). Revision IA (DeepSeek) sobre `44ac54c`: M1 y L2 arreglados en `1c15e1e`; M2, L1, L4 y el test de kinds van a D.1b.
- Bateria completa `success` sobre `7c80eab` (run 36202158897); el arbol de `03faa24` es identico al de `7c80eab`.

## Deploy (2026-09-25 23:53 UTC, dueno con `!`)

```text
APROBADO=03faa24bc5da47b29b1129e664e22f2949115ec3 (arbol = 7c80eab, bateria completa success)
-rw------- 1 root root 420948 Sep 25 23:54 /mnt/data/appdata/orbit/backups/pre0044_schema_20260925-2353.sql
CREATE TABLE / CREATE INDEX / COMMENT x4 / GRANT x2 / DO / COMMENT / GRANT / DO / COMMIT
tabla|t  vista|t  decide_insert|t  decide_update|f  read_vista|t
respaldo predeploy-20260925-2353
md5 OK (apply.py, apply_cola.py, cycle.py = 03faa24)
DIGEST antes=sha256:126332dc... despues=sha256:b8133eca...
{"status":"ok"}  orbit-app-1 Up
v_decision_huerfana: sin_registro|197
```

Los dos `WARNING` de transaccion son el `BEGIN`/`COMMIT` propios de la 0044 dentro del `-1` de psql (mismo caso que la 0039 en D.0 de repricing): todo quedo en una transaccion.

`sin_registro = 197` es el hueco historico de ciclos live cerrados antes de la 0044 (incluye los bids del 2-3 sep del analisis Kimi). Cero `huerfana` al desplegar.

Reversa: codigo `predeploy-20260925-2353/` + rebuild; esquema `backups/pre0044_schema_20260925-2353.sql`.

## Pendiente

D.1b (no bloqueantes de la revision IA) y D.2 (anti-inversion de bids, decision del dueno).
