# Orbit

Optimizador de **Amazon Ads** (Sponsored Products) con decisiones explícitas y
auditables (cuánto pujar, qué pausar, qué harvestear) para un negocio en **Amazon
MX/US y Mercado Libre** bajo el **régimen mexicano de plataformas** (las retenciones
de **ISR** son costo de primer orden: Amazon las manda sin `order_id` → se prorratean).
Roadmap (`docs/traspaso/MODULOS-AVANZADOS.md`): **Repricing** (inventory-aware),
**Campañas** (MeLi Ads *proposal-only*), **Reputación**, **Promociones**, **Envíos**.
Sistema **nuevo desde cero** (DB nueva); reemplaza a `goncloud-MCP-2` sin reutilizar
código viejo. **Leer primero:** `docs/CONTEXTO.md` y `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md`.

## Reglas de comportamiento

- **Responde siempre en español**; directo al grano, sin relleno.
- **Código limpio y listo para usar**: completo (sin `...`), escrito a archivos con las herramientas.
- Entiende antes de cambiar; prefiere patrones existentes; cambios mínimos; nunca inventes APIs/credenciales.
- Antes de terminar: corre tests, ruff y pre-commit; jamás `--no-verify`.

## Stack

- Python ≥ 3.12, FastAPI (`app/main.py`); PostgreSQL 16 en Docker (bind `127.0.0.1:5432`, túnel SSH), `psycopg` 3; HTTP: `httpx`. **Sin Redis/colas**.
- Deploy: servidor `goncloud` (`ssh goncloud`) junto a `bridge` y `accounting` (**no se tocan**); runbook: `docs/DEPLOY.md`. Rama: `master`.

## Estructura

```
app/          # main.py (API), db.py (connect), redaction.py (secretos), ads/ (cliente Amazon READ-ONLY)
migrations/   # 0001_initial.sql: 19 tablas, roles, triggers. NO re-runnable
tests/        # pytest
docs/         # CONTEXTO.md, DATABASE.md, DEPLOY.md, traspaso/ (fuentes verbatim)
plans/        # manifest.json marca el plan ORBIT NN activo (sigue sus tasks y DoD)
```

## Archivos que más importan

- `docs/CONTEXTO.md` (leer primero) y `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md` (reglas y umbrales).
- `migrations/0001_initial.sql` (esquema sellado), `app/ads/client.py` (guard read-only), `plans/manifest.json` (plan activo).

## Comandos

```bash
uv sync                                   # deps (uv; venv en .venv/)
./.venv/Scripts/python.exe -m pytest -q   # suite (CI: PYTHONPATH=. pytest -q); test en vivo skipea sin ORBIT_TEST_DSN
ruff check --fix . && ruff format .       # lint/format (line-length 100)
pre-commit run --all-files                # candados (pytest corre en pre-push); CI: .github/workflows/quality.yml
```

## Reglas de diseño innegociables

Aplican a todo el código (detalle en `docs/CONTEXTO.md`; los invariantes de tiempo van en **trigger con UTC fijado**, nunca en CHECK):

1. Una decisión, un camino, un dueño — lo que no está en ese camino no se construye.
2. Un número, una fuente.
3. Dato faltante = `None` / fila no escrita; **nunca** una constante inventada.
4. Dinero = `(valor, moneda)` por schema (`NUMERIC(14,4)` + ENUM MXN/USD; prohibido float).
5. Métricas **append-only** `(entidad, metric_date, observed_at)`; corregir = insertar fila nueva.
6. Cortar/pausar exige dato con ≥10 días de maduración; el día en curso se descarta.
7. Ninguna acción irreversible sin su reversa implementada antes.
8. Antes del test de un invariante, correr el `SELECT` que confirma el dato real en producción.
9. Toda prueba de regresión se demuestra **fallando** contra el código anterior.
10. Conciliar contra la fuente externa, no contra la propia consistencia.

## Convenciones de código

- Español en comentarios, docstrings, tests y docs; **sin acentos en el código**. Estilo Ruff (`ruff-format`).
- Secretos: ninguno en el repo; se cargan vía `ORBIT_SECRETS_DIR`; errores/logs redactados por `app/redaction.py`.
- Cliente Amazon Ads: guard default-deny (GET siempre; POST solo a `/reporting/reports`); API exacta: `get`, `create_report`, `get_report`, `download`.
- Base: roles LOGIN por servicio (`orbit_ingest`/`_decide`/`_read`/`_admin`); invariante nuevo del esquema = con su test; ADRs en `COMMENT ON` y docstrings.
- Trabajo en planes `ORBIT NN` (PR a `master` con CI verde por fase); registro en AppFlowy (**EHV Tasks**, skill `appflowy-ehv-task`): `In progress` al empezar, `Done` con notas completas.

<!-- >>> QUALITY-KIT CALIDAD SECTION START -- managed by quality-kit's init-repo.ps1. Do not hand-edit between these markers; re-running init-repo.ps1 will refresh this block cleanly. -->
## Calidad (quality-kit)

Este archivo `.pre-commit-config.yaml` tiene agregados propios (detectados por quality-kit) -- no lo pisamos.
Para ver que candados tiene realmente: `pre-commit run --all-files` (o mira `.pre-commit-config.yaml`).

Reglas de hierro:
1. Si un candado falla, se arregla el problema real -- JAMAS se usa `--no-verify` ni se saltea un candado.
2. Cada bug arreglado incluye, en el mismo cambio, una prueba que lo habria atrapado.
<!-- >>> QUALITY-KIT CALIDAD SECTION END -->
