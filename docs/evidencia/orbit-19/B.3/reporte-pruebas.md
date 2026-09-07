# ORBIT 19 / B.3 — Reporte de pruebas

Fecha: 2026-09-06. Rama `feat/orbit19-fase-b`. Los tests de integracion
corrieron contra Postgres local real (migraciones 0001 + 0022 en una DB
temporal); ningun test toca red ni el servidor bridge/accounting.

## Comandos y salida

```
$ .venv/bin/python -m ruff check app/disponibilidad.py tools/disponibilidad_snapshot.py tests/test_disponibilidad.py
All checks passed!
$ .venv/bin/python -m ruff format --check app/disponibilidad.py tools/disponibilidad_snapshot.py tests/test_disponibilidad.py
3 files already formatted
$ PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_disponibilidad.py
20 passed in 0.43s
```

Los 20 tests (unitarios + integracion) PASSED, sin skips:

- cero observado (`test_fba_cero_observado_no_es_null`, `test_estado_cero_observado`)
- desconocido NULL / fila ausente, jamas 0 (`test_fba_null_o_fila_ausente_es_desconocido_jamas_cero`, `test_estado_desconocido_sin_filas_o_solo_null`)
- tres estados distinguibles AC8 (`test_estado_tres_estados_distinguibles`, `test_sync_idempotente_y_tres_estados_en_db`)
- frescura vieja propagada (`test_frescura_vieja_se_propaga_no_se_descarta`, `test_estado_frescura_por_fuente`)
- FBM solo DEFAULT; AMAZON_NA NULL no es FBM (`test_fbm_solo_canal_default_amazon_na_no_es_fbm`)
- FBA+FBM no se suman (`test_fba_fbm_no_se_suman`, `test_estado_positivo_no_suma_canales`)
- cache prohibido (`test_cache_prohibido_nunca_se_lee`)
- SKU sin match en Orbit reportado (`test_sku_sin_match_en_orbit_se_reporta`)
- idempotencia (mismo observed_at -> 0 insertadas) y append-only
  (re-observacion anade) (`test_sync_idempotente_y_tres_estados_en_db`)
- esquema muerde: CHECK quantity>=0 y UNIQUE anti-duplicado
  (`test_check_rechaza_negativo_y_unique_muerde`, `test_migracion_parsea_y_trae_invariantes`)
- Featured Offer sin_verificar constante, no integracion
  (`test_featured_offer_sin_verificar_no_toca_la_base`,
  `test_migracion_declara_featured_offer_como_ampliacion`)

## Regresiones demostradas durante el desarrollo

- Cero del cache: el fixture siembra `amazon_inventory_cache` con 806-style
  ceros; si el adaptador lo leyera, los estados positivos fallarian.
- El test de UNIQUE con `now()` no mordia (autocommit avanza el reloj entre
  INSERTs): se fijaron timestamps, y el UNIQUE ya muerde.

## Resultado en vivo: PENDIENTE (lo corre el lead)

El resultado real contra el snapshot del servidor NO esta inventado aqui.
Comandos exactos (metodo 0.3, `docs/evidencia/orbit-19/0.3/bridge-observado.txt`):

```bash
# 1) snapshot en el servidor, modo ro (no toca accounting)
ssh goncloud 'python3 -c "import sqlite3; \
  sqlite3.connect(\"file:/mnt/data/appdata/bridge/data/bridge.db?mode=ro\", \
  uri=True).backup(sqlite3.connect(\"/tmp/bridge-snapshot-orbit19-b3.db\"))"'

# 2) traerlo al repo
scp goncloud:/tmp/bridge-snapshot-orbit19-b3.db out/bridge-snapshot-orbit19-b3.db

# 3) limpieza en el servidor
ssh goncloud 'rm -f /tmp/bridge-snapshot-orbit19-b3.db'

# 4) cargarlo a Orbit (requiere ORBIT_DSN_INGEST; correr tras aplicar 0022)
PYTHONPATH=. .venv/bin/python tools/disponibilidad_snapshot.py \
  --snapshot out/bridge-snapshot-orbit19-b3.db --dsn "$ORBIT_DSN_INGEST"
```

Salida esperada del paso 4: `run_id=... ok=True`, `filas_insertadas=N`,
`rows_skipped=M` con motivos (`seller_sku sin listing en Orbit` esperado por
los 291 listings sin mapa de 0.1). Pegar la salida real aqui cuando corra.

## Resultado en vivo: productivo, 2026-09-07 (cerrado por el lead)

Snapshot real del bridge (`sqlite3 .backup()` en modo ro, patron de
`docs/DEPLOY.md`), copiado al contenedor y corrido con el tool:

```
$ cat tools/disponibilidad_snapshot.py | ssh goncloud \
    'docker exec -i orbit-app-1 python - --snapshot /tmp/bridge-snapshot-b3.db'
run_id=115 ok=True
filas_insertadas=546
filas_idempotentes=0
rows_skipped=2046
skips: 1828x fba: seller_sku sin listing en Orbit, 218x fbm: seller_sku sin listing en Orbit
```

- 2046 SKUs del bridge sin listing en Orbit quedaron FUERA, contados, sin
  inventar filas (regla 3). El snapshot temporal se borro del contenedor.
- Verificacion via API productiva `GET /api/fabrica/evaluacion?plataforma=amazon_mx`:
  las 342 publicaciones MX traen disponibilidad con dato real; ejemplo
  B0BXHVT1MG estado=positivo, cantidad fbm=46, freshness 2026-09-07T12:40Z
  (hoy). Featured Offer sigue Sin verificar (ampliacion abierta).
