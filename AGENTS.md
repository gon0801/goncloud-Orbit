# Orbit

Optimizador de **Amazon Ads** (Sponsored Products) con decisiones explícitas y
auditables (cuánto pujar, qué pausar, qué harvestear) para un negocio en **Amazon
MX/US y Mercado Libre** bajo el **régimen mexicano de plataformas** (las retenciones
de **ISR** son costo de primer orden: Amazon las manda sin `order_id` → se prorratean).
Roadmap (`docs/traspaso/MODULOS-AVANZADOS.md`): **Repricing** (inventory-aware),
**Campañas** (MeLi Ads *proposal-only*), **Reputación**, **Promociones**, **Envíos**.
Sistema **nuevo desde cero** (DB nueva); reemplaza a `goncloud-MCP-2` sin reutilizar
código viejo (solo se migran credenciales y datos verificados). **Leer primero:** `docs/CONTEXTO.md` y
`docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md`.

Muse y los agentes que leen `AGENTS.md` ignoran `CLAUDE.md` en este repo: toda regla que deba
valer para todos va aquí también.

## Reglas de comportamiento

- **Responde siempre en español**; directo al grano, sin relleno.
- **Código limpio y listo para usar**: completo (sin `...`), escrito a archivos con las herramientas.
- Entiende antes de cambiar; prefiere patrones existentes; cambios mínimos; nunca inventes APIs/credenciales.
- Verifica con las pruebas y `pre-commit` (gitleaks); jamás `--no-verify`.

## Stack

- Python ≥ 3.12, FastAPI (`app/main.py`); PostgreSQL 16 en Docker (bind `127.0.0.1:5432`, túnel SSH), `psycopg` 3; HTTP: `httpx`. **Sin Redis/colas**.
- Deploy: servidor `goncloud` (`ssh goncloud`) junto a `bridge` y `accounting` (**no se tocan**); runbook: `docs/DEPLOY.md`. Rama: `master`.

## Estructura

```
app/          # main.py (API), db.py (connect), redaction.py (secretos), ads/ (cliente Amazon READ-ONLY)
migrations/   # por numero (0001 = esquema inicial); las *_reversa_* y las que salta verify/Launch.md no van en el deploy. NO re-runnable
tests/        # pytest
tools/        # scripts operativos (fabrica_campanas.py, compara_target_margen.py, ...)
verify/       # mapa de la app + Drive e2e; receta de arranque en verify/Launch.md
docs/         # CONTEXTO.md, DATABASE.md, DEPLOY.md, traspaso/ (fuentes verbatim)
plans/        # manifest.json marca el plan ORBIT NN activo (sigue sus tasks y DoD)
```

## Archivos que más importan

- `docs/CONTEXTO.md` (leer primero) y `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md` (reglas y umbrales).
- `migrations/0001_initial.sql` (esquema sellado), `app/ads/client.py` (guard read-only), `plans/manifest.json` (plan activo).

## Comandos

```bash
uv sync                                   # deps (uv; venv en .venv/)
uv run --frozen python -m pytest -q       # suite (CI: python -m pytest -q -x); test en vivo skipea sin ORBIT_TEST_DSN
ruff check --fix . && ruff format .       # lint/format (line-length 100)
pre-commit run --all-files                # gitleaks y conflictos; CI (gate: gitleaks, compileall, pytest): .github/workflows/quality.yml
```
Windows: el venv es `.venv/Scripts/python.exe`; sin Postgres local, lo PG-backed
se skipea por diseno (corre en CI). Si un push se cuelga, es credencial:
`gh auth setup-git`.

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

- Español en comentarios, docstrings, tests y docs; **sin acentos en el código**. Estilo Ruff.
- Secretos: ninguno en el repo; se cargan vía `ORBIT_SECRETS_DIR`; errores/logs redactados por `app/redaction.py`.
- Cliente Amazon Ads: guard default-deny (GET siempre; POST solo a
  `/reporting/reports`, rutas literales v3 `/list` de lectura y
  `/sp/targets/bid/recommendations` de lectura); API exacta: `get`, `list_objects`,
  `recommend_bids`, `create_report`, `get_report`, `download`.
- Base: roles LOGIN por servicio (`orbit_ingest`/`_decide`/`_read`/`_admin`); invariante nuevo del esquema = con su test; ADRs en `COMMENT ON` y docstrings.
- Trabajo en planes `ORBIT NN` (PR a `master` con CI verde por fase).

## Pruebas y revisiones
- Pruebas nuevas SOLO en 4 casos: dinero (pujas, precios, presupuestos, cobros), escribir datos de produccion
  (base del servidor, Amazon, Mercado Libre, Shopify, Odoo), instalar/borrar/deshacer en las maquinas, y una
  falla que ya paso en produccion (una prueba, no una bateria). Lo demas: basta comprobar que corre. Nunca
  pruebas de texto, de las pruebas ni mutantes. Un revisor no pide pruebas fuera de eso.
- Un revisor solo bloquea por seguridad, datos, el comportamiento pedido roto o un caso de los 4 sin su
  prueba, siempre con el comando que lo reproduce. Lo demas va en notas y se corrige en la misma ronda.
- Nunca subir contrasenas ni llaves a un repo.
