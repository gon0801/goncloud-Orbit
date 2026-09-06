# FABRICA 01 — fábrica de campañas Amazon SP por grupo (Phase F1) — Implementation Plan

> **v2 tras ronda 1 de cross-review (grok 2026-09-05: 5 ALTA + 9 MEDIA + 6 BAJA — todo
> incorporado o declarado).** Altas: desarmar desde `fabrica_lote_paso` (cubre lote a
> medias), multi-listing fail-loud, `--registrar` idempotente, `_registrar` en try con
> rollback previo al sello, reconciliar aborta también con `ausentes`.
>
> **v3 tras ronda 2 de cross-review (glm 2026-09-05: 1 ALTA + 2 MEDIA + 2 BAJA — todo
> incorporado).** Alta: el guard `if __name__ == "__main__":` es SIEMPRE lo último del
> archivo (los stubs y sus reemplazos van antes de `main()`; con el layout anterior toda
> corrida real por stdin reventaba con NameError y la suite — que importa el módulo — quedaba
> verde; candado `test_fabrica_guard_main_es_lo_ultimo_del_archivo`). Medias: `ORBIT_DSN_ADMIN`
> seteada en el test de doble corrida; `--registrar` rechaza lotes `desarmado` (no resucita
> un grupo pausado). Bajas: el readback exige también `defaultBid` y `budget.budget`;
> divergencia conservadora de `_cumple_harvest` en (cost=0, revenue=0) declarada.
>
> **v4 tras ronda 3 de cross-review (codex 2026-09-05: 2 ALTA + 4 MEDIA — todo incorporado).**
> Altas: desarmar cubre campañas creadas con readback fallido (paso failed CON external);
> guards de moneda en los denominadores del prorrateo de v_margen_producto (orden y
> plataforma). Medias: desempate source_report_id DESC NULLS LAST del colapso bitemporal;
> reconciliar por plataforma con su propio perfil; row_factory tuple_row defensivo en
> --registrar; test ledger pre-HTTP con secuencia compartida sql/http.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** una herramienta CLI proposal-only que, con go literal del dueño, crea en Amazon SP un grupo de 5 campañas (auto, phrase, broad, product targeting, exact) para un `tipo_producto`, con target derivado del margen mínimo del grupo, product ads por producto, semillas de la biblioteca, ledger pre-HTTP, readback, sync de estructura, goals por campaña y reversa por pausa.

**Architecture:** patrón sellado de `tools/archiva_inertes.py` (plan desde la base → dry-run con huella → mutación con `--acepto-mutacion-real --esperado --huella --go` → ledger `planeado` antes de cada POST → HTTP propio con vendor v3 → readback por LIST → sello). La lógica pura (target, nombres, payloads, huella, semillas) vive en `app/fabrica_plan.py` porque el tool entra por stdin al contenedor y debe ser UN archivo; la escritura de `ad_entity` la hace `sync_structure` (único escritor) y la de goals `app/goals_write.py` (camino único, con `crea_goal` nuevo). F2 (reruteo del harvest, negative cruzado, biblioteca escrita por el motor) es OTRO plan: depende de la sonda de esta phase.

**Tech Stack:** Python 3.12, psycopg 3, httpx, PostgreSQL 16, pytest, ruff, pglast (tests estáticos de migraciones).

**Spec:** `docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md` (decisiones 1-14, §2-§10). Precedencia: `docs/CONTEXTO.md` (reglas 1-10) > `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md` > spec > este plan.

## Global Constraints

- **Regla 3**: dato faltante = `None` y la fila no se escribe; jamás constantes inventadas. Producto sin margen medible o sin `seller_sku` NO entra al grupo: el tool aborta nombrándolo.
- **Regla 4**: todo dinero con moneda. Moneda por plataforma: `amazon_mx → MXN`, `amazon_us → USD` (mismo mapa que `app/ads/write.py::PLATAFORMA_MONEDA`; test que los pinea). Piso/techo de bids POR MONEDA: `DEFAULTS_POR_MONEDA` de `app/optimizer/goals.py` (USD 0.10/2.50, MXN 1.00/45.00). El bid viaja al wire como número cuantizado a 2 decimales (`quantize(Decimal("0.01"), ROUND_HALF_EVEN)`), igual que `_bid_wire` de `archiva_inertes`.
- **Regla 7**: ninguna mutación sin su reversa en el MISMO PR: `--desarmar` (pausa las 5 + `enabled=false` en goals) entra con la mutación (tarea 7 y 9 se mergean juntas si el reviewer lo exige; ver tarea 9).
- **Regla 8**: antes de cada test de invariante, el `SELECT` contra producción (tarea 1). Los tests de SQL corren contra Postgres REAL (precedente `%s::platform` IndeterminateDatatype).
- **Regla 9**: toda prueba de regresión se demuestra fallando primero (TDD estricto; los pasos "Run test, expected FAIL" no se saltan).
- **Target del grupo**: `aplicado = clamp(fraccion × min(margen_neto_pct de los productos), [10, 45])`, con `fraccion` del setting vigente `ads_target_fraccion_margen_<platform>` (`config_version` más reciente; ausente → aborta; inválida → `ValueError` de `fraccion_desde_settings`). Banda: `MARGEN_BANDA_MIN/MAX` de `app/optimizer/goals.py`, NO copiar los números.
- **Terna de harvest completa en F1** (decisión 12): cada goal nace con `harvest_campaign_id`/`harvest_ad_group_id` = externos de la `category_exact` del grupo y `harvest_default_bid = --bid-exact`.
- **`--modo` explícito** (decisión 13): `shadow|live`, sin default. Los 5 goals nacen `enabled=true, mode=--modo`.
- **Campañas nacen ENABLED** (decisión 8). `--desarmar` PAUSA, nunca archiva.
- **Orden de creación fijo**: `category_exact → category_phrase → category_broad → product_targeting → auto_discovery`.
- **HTTP propio**: `httpx` directo con `Content-Type` Y `Accept` = vendor v3 exacto del path; el objeto viaja envuelto en su clave de lista; ids como STRING. Readback SOLO por `AdsClient.list_objects` (POST `/sp/*/list`). PROHIBIDO importar `app.ads.write` (candado `test_imports_del_cliente_de_escritura_acotados` ya escanea `tools/`).
- **Shapes sin sellar en vivo** (POST `/sp/campaigns`, `/sp/adGroups`, `/sp/targets`, camino feliz de `/sp/productAds`): son hipótesis documentadas hasta la sonda de la tarea 11; se marcan `# HIPOTESIS hasta la sonda` en el código y se sellan (o corrigen) con el log de la sonda.
- **Ventanas (dos, no una)**: margen, biblioteca y términos phrase/broad/product usan `[CURRENT_DATE - 105, CURRENT_DATE - 15)` UTC (la misma de `v_target_margen_plataforma`); los **candidatos a semilla exact** se evalúan con la **ventana de CORTES del motor** (`VENTANA_CORTES_DIAS`, agregado separado con `window_end <= hoy - 10d`, regla 6 — pineada contra `app/optimizer/windows.py` por test).
- **Módulos de `app/` ≤ 900 líneas** (`test_presupuesto_de_tamano_por_modulo`); complejidad bajo los topes de ruff (C901 22, PLR0912 25, PLR0915 80). `tools/` no tiene tope de líneas pero sí ruff.
- **Proceso**: rama por tarea desde `origin/master` (`git fetch` primero); un PR por tarea; CI corre la batería completa (no correr la suite entera local, solo el archivo de test que se toca); `pre-commit run --all-files` verde; JAMÁS `--no-verify`. Cross-review: 1 ronda por PR de código; 2ª SOLO si la 1ª halla severidad alta; jamás 3ª. Prohibido tocar el contenedor de producción: la corrida real (tarea 11) es del lead.
- **Commits**: Conventional Commits en español (`feat(fabrica): ...`, `test(fabrica): ...`, `docs: ...`) con los trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` y `Claude-Session: https://claude.ai/code/session_016wRNF2Uuu7fJoQAJ5tPZU5`.
- **Registro**: fila `ORBIT NN — Fábrica de campañas por grupo (FABRICA 01)` en EHV Tasks (NN = el siguiente libre en el grid; el último visto en el repo es ORBIT 16): `In progress` al arrancar la tarea 1, `Done` con notas completas al cerrar la 11 (skill `appflowy-ehv-task`). Cada tarea cerrada agrega su marker `cc:完了` en este plan y actualiza `docs/CHAT-CONTEXT.md` (candado `tools/check_chat_context_fresh.py`).

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `migrations/0018_fabrica_campanas.sql` (crear) | ENUM `campana_rol`; tablas `fabrica_lote`, `fabrica_lote_paso`, `campana_grupo`, `campana_grupo_rol`, `campana_grupo_producto`, `keyword_biblioteca`, `negative_biblioteca`, `harvest_excepcion`; vista `v_margen_producto`; triggers `campana_grupo_rol_kinds`, `campana_grupo_producto_listing`, `harvest_excepcion_kind`; COMMENTs; GRANTs. |
| `app/goals_write.py` (modificar) | `crea_goal`: INSERT único de goals de campaña (camino único, sellado 26 de ORBIT 04 — `docs/APPLY.md` §10.3), reusando `_valida_pre_editar` y `resuelve_floor_ceiling`. |
| `app/fabrica_plan.py` (crear) | Núcleo PURO (sin psycopg/httpx): dataclasses del plan, target del grupo, validación de montos, nombres, semillas, payloads por rol, huella, parseo de acks 207, líneas del dry-run. |
| `tools/fabrica_campanas.py` (crear) | IO: SQL de plan (`ORBIT_DSN_READ`), ledger (`ORBIT_DSN_ADMIN`), HTTP v3, readback, `sync_structure` (`ORBIT_DSN_INGEST`), registro interno, `--desarmar`, `--reconciliar`, argparse. Archivo ÚNICO (entra por stdin). |
| `tests/test_fabrica_migracion.py` (crear) | pglast + Postgres real: DDL, CHECKs, trigger, GRANTs, `v_margen_producto` con ledger sembrado. |
| `tests/test_goals_write.py` (modificar) | `crea_goal`: unit (validaciones) + PG (INSERT, UNIQUE, trigger kind). |
| `tests/test_fabrica_plan.py` (crear) | Núcleo puro: target/clamp/procedencia, montos por moneda, nombres, huella, semillas, payloads, acks. |
| `tests/test_fabrica_campanas.py` (crear) | Tool: SQL contra Postgres real; dry-run sin HTTP; mutación con MockTransport (ledger pre-HTTP, orden, detención, readback); sync+registro; desarmar; reconciliar. |
| `tests/test_architecture.py` (modificar) | Allowlist POSITIVA de imports de `tools/fabrica_campanas.py`. |
| `docs/DATABASE.md`, `docs/CHAT-CONTEXT.md`, `plans/manifest.json` (modificar) | Documentación de 0018 y registro del plan. |

---

### Task 1: Regla 8 — SELECTs contra producción (lead, sin código)

**Files:**
- Modify: `plans/fabrica-01.md` (sección "Decisiones y evidencia" al final)

**Interfaces:**
- Consumes: `orbit_read` en el server (`ssh goncloud`, `docker exec -i orbit-postgres-1 psql ...` o el DSN de lectura del contenedor app).
- Produces: números que fijan dos decisiones de la tarea 3 (guard `dias_con_venta >= 60` por producto y cobertura por producto) y confirman el grano del ledger.

- [x] **Step 1: Marcar la tarea en AppFlowy**

```bash
ssh goncloud "python3 /mnt/data/appdata/appflowy/_migrate/add_ehv_task.py --name 'ORBIT NN — Fábrica de campañas por grupo (FABRICA 01)' --status 'In progress' --notes 'Arranque F1: SELECTs regla 8 contra produccion (tarea 1 del plan plans/fabrica-01.md). Spec: docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md'"
```

- [x] **Step 2: Correr los SELECTs (read-only) y pegar la salida en "Decisiones y evidencia"**

```sql
-- (a) ¿Cuantas ordenes de venta traen mas de un producto? (grano del prorrateo, spec §8)
SELECT platform,
       COUNT(*) FILTER (WHERE n_productos > 1) AS ordenes_multi,
       COUNT(*) AS ordenes
  FROM (SELECT platform, order_id, COUNT(DISTINCT product_id) AS n_productos
          FROM ledger_event
         WHERE kind = 'sale' AND order_id IS NOT NULL AND product_id IS NOT NULL
           AND event_date >= CURRENT_DATE - 105 AND event_date < CURRENT_DATE - 15
         GROUP BY platform, order_id) o
 GROUP BY platform;

-- (b) ¿Los cargos traen product_id? (esperado: NO; app/ledger.py solo resuelve product_id en ventas)
SELECT platform, kind,
       COUNT(*) FILTER (WHERE product_id IS NOT NULL) AS con_producto,
       COUNT(*) FILTER (WHERE order_id IS NOT NULL) AS con_orden,
       COUNT(*) AS total
  FROM ledger_event
 WHERE kind IN ('fee', 'refund', 'withholding')
   AND event_date >= CURRENT_DATE - 105 AND event_date < CURRENT_DATE - 15
 GROUP BY platform, kind ORDER BY 1, 2;

-- (c) Por producto: dias con venta, cobertura de costo y monedas en la ventana
--     (fija si el guard dias >= 60 por producto deja candidatos vivos).
SELECT l.platform, l.product_id, p.odoo_sku,
       COUNT(DISTINCT l.event_date) AS dias_con_venta,
       SUM(l.amount) AS venta_total,
       SUM(l.amount) FILTER (WHERE c.id IS NOT NULL AND c.cost_currency = l.amount_currency) AS venta_cubierta,
       COUNT(DISTINCT l.amount_currency) AS n_monedas
  FROM ledger_event l
  JOIN product p ON p.id = l.product_id
  LEFT JOIN sku_cost c ON c.product_id = l.product_id
       AND l.event_date >= c.valid_from AND (c.valid_to IS NULL OR l.event_date < c.valid_to)
 WHERE l.kind = 'sale' AND l.event_date >= CURRENT_DATE - 105 AND l.event_date < CURRENT_DATE - 15
 GROUP BY 1, 2, 3 ORDER BY 1, dias_con_venta DESC;

-- (d) Listings con seller_sku por plataforma (sin SKU no hay product ad, spec §3)
--     y productos MULTI-LISTING por plataforma (N > 1 listings: la fabrica
--     ABORTA nombrando el producto — regla 3, no elige; residual de F1).
SELECT platform, COUNT(*) AS listings, COUNT(seller_sku) AS con_sku FROM listing GROUP BY platform;
SELECT platform,
       COUNT(*) FILTER (WHERE n > 1) AS productos_multi_listing,
       COUNT(*) AS productos
  FROM (SELECT platform, product_id, COUNT(*) AS n FROM listing GROUP BY platform, product_id) t
 GROUP BY platform;

-- (e) Setting de fraccion vigente por plataforma
SELECT id, settings->>'ads_target_fraccion_margen_amazon_mx' AS mx,
       settings->>'ads_target_fraccion_margen_amazon_us' AS us
  FROM config_version ORDER BY id DESC LIMIT 1;

-- (f) search_term_observation: ¿el ad_entity_id origen es campana o ad group? (fija el JOIN de semillas)
--     CRITICO: si hay filas en AMBOS granos de la misma campana, el UNION de
--     la CTE `origenes` (tarea 6, _SQL_TERMINOS) DUPLICA orders/cost. La
--     decision sale de aqui: un solo grano -> ese; ambos -> SOLO ad_group.
SELECT e.kind, COUNT(*) FROM search_term_observation s JOIN ad_entity e ON e.id = s.ad_entity_id
 WHERE s.metric_date >= CURRENT_DATE - 105 GROUP BY e.kind;
```

- [x] **Step 3: Anotar las decisiones que salen de (c), (d) y (f)**

Si NINGÚN producto llega a 60 días con venta, el guard por producto de la tarea 3 se cambia a `MARGEN_DIAS_MIN_PRODUCTO = 30` **solo con decisión escrita del dueño** en "Decisiones y evidencia" (regla 2: un número, una fuente; la constante vive en la vista y en `app/fabrica_plan.py` con un test que las pinea). Si sí hay candidatos, el guard queda en 60 (misma maquinaria que la plataforma).

De (d): si hay productos multi-listing en la plataforma de la sonda, el `--productos` de la sonda los EXCLUYE (el tool aborta nombrándolos si se piden; el residual `--listing` explícito queda fuera de F1).

De (f): registrar en "Decisiones y evidencia" el grano real de `search_term_observation` y aplicarlo a la CTE `origenes` de `_SQL_TERMINOS` (tarea 6): UN solo grano en producción → `origenes` se simplifica a ese SELECT; AMBOS granos → se usa SOLO el grano `ad_group` (más fino; la campaña se deduce por `parent_id`) y el UNION se elimina. El test `test_terminos_del_producto_colapsan_bitemporal_y_suman_en_ventana` se ajusta a la variante elegida (hoy ejercita ambos granos sumados, la hipótesis UNION).

- [x] **Step 4: Commit de la evidencia**

```bash
git checkout -b fabrica-01-1-evidencia origin/master
git add plans/fabrica-01.md plans/manifest.json
git commit -m "plan: fabrica-01 — evidencia regla 8 (tarea 1)"
```

(`plans/manifest.json` gana la entrada `{"name": "fabrica-01", "path": "plans/fabrica-01.md", "description": "FABRICA 01 — fábrica de campañas Amazon SP por grupo con target por margen (F1 proposal-only; F2 harvest por grupo es otro plan)"}` en el arreglo `plans`.)

---

### Task 2: Migración 0018 — tablas del grupo, biblioteca y ledger de creación

**Files:**
- Create: `migrations/0018_fabrica_campanas.sql`
- Create: `tests/test_fabrica_migracion.py`

**Interfaces:**
- Consumes: tipos `platform`, `currency`, dominio `money_amount`, tablas `ad_entity`, `product`, `listing` (0001); roles `app_read/app_ingest/app_decide/app_admin`.
- Produces: ENUM `campana_rol` (5 valores), tablas `fabrica_lote(lote PK)`, `fabrica_lote_paso(id, lote, orden, rol, recurso, request_payload, external_id, ack, readback_estado, estado)`, `campana_grupo(id, platform, tipo_producto, nombre_base, lote, target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal, created_at)`, `campana_grupo_rol(grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)`, `campana_grupo_producto(grupo_id, product_id, listing_id, seller_sku, margen_neto_pct)`, `keyword_biblioteca`, `negative_biblioteca`, `harvest_excepcion`; triggers `campana_grupo_rol_kinds`, `campana_grupo_producto_listing` (listing↔producto y plataforma), `harvest_excepcion_kind` (kind='campaign'). La vista `v_margen_producto` se agrega al MISMO archivo en la tarea 3.

- [ ] **Step 1: Escribir el test estático (pglast) y el de Postgres real, que fallan porque la migración no existe**

```python
# tests/test_fabrica_migracion.py
"""Migracion 0018 (FABRICA 01, spec §8): tablas del grupo, biblioteca por
tipo_producto, ledger de creacion y v_margen_producto (tarea 3).

(a) ESTATICO (pglast): el DDL parsea y trae los invariantes del spec.
(b) POSTGRES REAL: CHECKs, UNIQUEs, trigger de kinds y GRANTs muerden;
    la vista mide contra un ledger sembrado (tarea 3). Skip fail-closed
    sin Postgres (misma condicion que test_schema)."""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pglast
import psycopg
import pytest
from psycopg import sql as pgsql
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]
MIGRACIONES = ROOT / "migrations"
# Orden minimo para que 0018 aplique: enum product_ad (0004), first_seen_at
# (0017) y la vista de plataforma (0015/0016) cuya maquinaria copia 0018.
ORDEN = (
    "0001_initial.sql",
    "0002_apply.sql",
    "0003_goal_bounds_explicit.sql",
    "0004_ad_entity_kind_product_ad.sql",
    "0013_entidad_inerte.sql",
    "0014_keyword_archivo_manual.sql",
    "0015_target_margen_plataforma.sql",
    "0016_target_margen_correcciones.sql",
    "0017_first_seen_at.sql",
    "0018_fabrica_campanas.sql",
)
SQL18 = (MIGRACIONES / "0018_fabrica_campanas.sql").read_text(encoding="utf-8")

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


@contextmanager
def db_fabrica(prefijo: str = "orbit_fabrica"):
    """DB temporal con las migraciones de ORDEN; yields conn autocommit."""
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for nombre in ORDEN:
            conn.execute((MIGRACIONES / nombre).read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _entidad(conn, platform, kind, external, parent=None, listing_id=None) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, listing_id)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, listing_id),
    ).fetchone()[0]


def _producto(conn, sku="SKU-1", asin="B0FABRICA01", platform="amazon_mx", seller_sku="SS-1"):
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES (%s, %s) RETURNING id", (sku, sku)
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pid, platform, asin, seller_sku),
    ).fetchone()[0]
    return pid, lid


def _lote(conn, lote="fabrica-amazon_mx-collar_perro-20260905-120000"):
    conn.execute(
        "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
        " huella, plan, modo_goal, estado) VALUES (%s, 'amazon_mx', 'collar_perro', 'Collar',"
        " 'go', 'h', '{}'::jsonb, 'shadow', 'planeado')",
        (lote,),
    )
    return lote


def _grupo(conn, lote) -> int:
    return conn.execute(
        "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
        " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal)"
        " VALUES ('amazon_mx', 'collar_perro', 'Collar', %s, 19.10, 19.1040, 0.5,"
        " 'margen_minimo_grupo', 'go') RETURNING id",
        (lote,),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# (a) Estatico
# ---------------------------------------------------------------------------


def test_0018_parsea_y_trae_el_ddl_del_spec():
    pglast.parse_sql(SQL18)  # revienta si no parsea
    for tabla in (
        "fabrica_lote",
        "fabrica_lote_paso",
        "campana_grupo",
        "campana_grupo_rol",
        "campana_grupo_producto",
        "keyword_biblioteca",
        "negative_biblioteca",
        "harvest_excepcion",
    ):
        assert f"CREATE TABLE {tabla}" in SQL18, tabla
        assert f"COMMENT ON TABLE {tabla}" in SQL18, f"{tabla} sin COMMENT"
    assert "CREATE TYPE campana_rol AS ENUM" in SQL18
    for rol in (
        "auto_discovery",
        "category_phrase",
        "product_targeting",
        "category_broad",
        "category_exact",
    ):
        assert f"'{rol}'" in SQL18
    assert "paso_evidencia_applied" in SQL18, "applied exige external_id+ack+readback"
    assert "grupo_rol_kinds" in SQL18, "trigger: campana y ad group con kind correcto"
    assert "campana_grupo_producto_listing" in SQL18, "trigger: listing DEL producto y plataforma"
    assert "harvest_excepcion_kind" in SQL18, "trigger: excepcion solo sobre kind=campaign"
    for rol in ("app_read", "app_ingest", "app_decide", "app_admin"):
        assert rol in SQL18
    assert "GRANT INSERT, UPDATE ON" in SQL18 and "TO app_admin" in SQL18


# ---------------------------------------------------------------------------
# (b) Postgres real
# ---------------------------------------------------------------------------


@_skip_db
def test_0018_aplica_sobre_el_esquema_vivo():
    with db_fabrica() as conn:
        n = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name IN"
            " ('fabrica_lote','fabrica_lote_paso','campana_grupo','campana_grupo_rol',"
            " 'campana_grupo_producto','keyword_biblioteca','negative_biblioteca',"
            " 'harvest_excepcion')"
        ).fetchone()[0]
        assert n == 8


@_skip_db
def test_paso_applied_exige_evidencia_completa():
    """Espejo de archivo_evidencia_applied: applied sin external/ack/readback
    revienta; con los tres, pasa."""
    with db_fabrica() as conn:
        lote = _lote(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                " estado) VALUES (%s, 1, 'category_exact', 'campaign', '{}'::jsonb, 'applied')",
                (lote,),
            )
        conn.execute(
            "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
            " external_id, ack, readback_estado, estado) VALUES (%s, 1, 'category_exact',"
            " 'campaign', '{}'::jsonb, '111', '{}'::jsonb, 'ENABLED', 'applied')",
            (lote,),
        )


@_skip_db
def test_grupo_rol_exige_campana_y_ad_group_reales():
    """Trigger campana_grupo_rol_kinds (patron goal_scope_campana_real): la
    campana debe ser kind='campaign' y el ad group kind='ad_group' hijo de
    ESA campana; cualquier otra combinacion revienta."""
    with db_fabrica() as conn:
        lote = _lote(conn)
        grupo = _grupo(conn, lote)
        camp = _entidad(conn, "amazon_mx", "campaign", "c1")
        ag = _entidad(conn, "amazon_mx", "ad_group", "ag1", parent=camp)
        otra = _entidad(conn, "amazon_mx", "campaign", "c2")
        ag_otra = _entidad(conn, "amazon_mx", "ad_group", "ag2", parent=otra)
        with pytest.raises(psycopg.errors.CheckViolation):  # ad_group como campana
            conn.execute(
                "INSERT INTO campana_grupo_rol VALUES (%s, 'category_exact', %s, %s)",
                (grupo, ag, ag),
            )
        with pytest.raises(psycopg.errors.CheckViolation):  # ad group de OTRA campana
            conn.execute(
                "INSERT INTO campana_grupo_rol VALUES (%s, 'category_exact', %s, %s)",
                (grupo, camp, ag_otra),
            )
        conn.execute(
            "INSERT INTO campana_grupo_rol VALUES (%s, 'category_exact', %s, %s)",
            (grupo, camp, ag),
        )
        # UNIQUE(ad_entity_id): una campana pertenece a lo sumo a un grupo
        grupo2 = _grupo(conn, _lote(conn, "fabrica-amazon_mx-collar_perro-20260905-130000"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO campana_grupo_rol VALUES (%s, 'category_exact', %s, %s)",
                (grupo2, camp, ag),
            )


@_skip_db
def test_grupo_producto_snapshot_y_biblioteca_unica():
    with db_fabrica() as conn:
        lote = _lote(conn)
        grupo = _grupo(conn, lote)
        pid, lid = _producto(conn)
        conn.execute(
            "INSERT INTO campana_grupo_producto VALUES (%s, %s, %s, 'SS-1', 38.2000)",
            (grupo, pid, lid),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO campana_grupo_producto VALUES (%s, %s, %s, 'SS-1', 38.2000)",
                (grupo, pid, lid),
            )
        conn.execute(
            "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen, orders,"
            " cost, revenue, moneda) VALUES ('collar_perro', 'amazon_mx', 'collar perro',"
            " 'campana:1', 3, 10.5, 90, 'MXN')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen)"
                " VALUES ('collar_perro', 'amazon_mx', 'collar perro', 'campana:2')"
            )
        with pytest.raises(psycopg.errors.CheckViolation):  # dinero sin moneda (regla 4)
            conn.execute(
                "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen, cost)"
                " VALUES ('collar_perro', 'amazon_mx', 'otra', 'campana:1', 1)"
            )


@_skip_db
def test_grupo_producto_exige_listing_del_producto_y_plataforma():
    """Trigger campana_grupo_producto_listing (patron campana_grupo_rol_kinds):
    la FK sola admite un listing de OTRO producto o de OTRA plataforma; el
    trigger exige listing.product_id = NEW.product_id y listing.platform =
    plataforma del grupo."""
    with db_fabrica() as conn:
        grupo = _grupo(conn, _lote(conn))
        pid, lid = _producto(conn)
        _otro_pid, otro_lid = _producto(conn, sku="B", asin="B0BBBBBBBB", seller_sku="SB")
        lid_us = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_us', 'B0US000001', 'SS-US') RETURNING id",
            (pid,),
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):  # listing de OTRO producto
            conn.execute(
                "INSERT INTO campana_grupo_producto VALUES (%s, %s, %s, 'SB', 1)",
                (grupo, pid, otro_lid),
            )
        with pytest.raises(psycopg.errors.CheckViolation):  # listing de OTRA plataforma
            conn.execute(
                "INSERT INTO campana_grupo_producto VALUES (%s, %s, %s, 'SS-US', 1)",
                (grupo, pid, lid_us),
            )
        conn.execute(  # listing correcto: pasa
            "INSERT INTO campana_grupo_producto VALUES (%s, %s, %s, 'SS-1', 38.2)",
            (grupo, pid, lid),
        )


@_skip_db
def test_harvest_excepcion_exige_kind_campaign():
    """Trigger harvest_excepcion_kind (patron goal_scope_campana_real): la
    excepcion se congela sobre una CAMPANA; un ad_group (u otra kind) revienta."""
    with db_fabrica() as conn:
        camp = _entidad(conn, "amazon_mx", "campaign", "c-exc")
        ag = _entidad(conn, "amazon_mx", "ad_group", "ag-exc", parent=camp)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO harvest_excepcion (ad_entity_id, destino_campaign_external,"
                " destino_ad_group_external, go_literal) VALUES (%s, 'c1', 'ag1', 'go')",
                (ag,),
            )
        conn.execute(
            "INSERT INTO harvest_excepcion (ad_entity_id, destino_campaign_external,"
            " destino_ad_group_external, go_literal) VALUES (%s, 'c1', 'ag1', 'go')",
            (camp,),
        )


@_skip_db
def test_grants_0018():
    """app_admin escribe las tablas nuevas; app_decide y app_read solo leen
    (el ruteo de F2 corre como motor y lee campana_grupo_rol)."""
    with db_fabrica() as conn:
        filas = conn.execute(
            "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants"
            " WHERE table_name IN ('campana_grupo_rol', 'fabrica_lote_paso', 'keyword_biblioteca')"
        ).fetchall()
        privs = {(g, t, p) for g, t, p in filas}
        assert ("app_admin", "campana_grupo_rol", "INSERT") in privs
        assert ("app_admin", "fabrica_lote_paso", "UPDATE") in privs
        assert ("app_decide", "campana_grupo_rol", "SELECT") in privs
        assert ("app_read", "keyword_biblioteca", "SELECT") in privs
        assert ("app_decide", "campana_grupo_rol", "INSERT") not in privs
        assert ("app_read", "fabrica_lote_paso", "INSERT") not in privs
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `pytest tests/test_fabrica_migracion.py -v`
Expected: FAIL en la colección con `FileNotFoundError: .../0018_fabrica_campanas.sql`.

- [ ] **Step 3: Escribir la migración (tablas; la vista llega en la tarea 3)**

```sql
-- migrations/0018_fabrica_campanas.sql
-- =============================================================================
--  FABRICA 01 (spec docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md
--  §8) — PostgreSQL 16.
--
--  Grupos de 5 campanas SP creados por tools/fabrica_campanas.py con go del
--  dueno. El VINCULO campana<->grupo es la tabla campana_grupo_rol (nunca el
--  nombre en Amazon). Ledger de creacion fabrica_lote/fabrica_lote_paso con
--  el patron keyword_archivo_manual (0014): intencion durable ANTES del HTTP,
--  ack JSONB, readback, estados planeado/applied/failed. Biblioteca de
--  keywords/negativos por (tipo_producto, platform, texto): F1 la LEE al
--  sembrar; F2 la escribe desde harvest/negatives aplicados. harvest_excepcion
--  nace aqui (schema) y se puebla en F2 con go del dueno (decision 4).
--  v_margen_producto: misma maquinaria de v_target_margen_plataforma (0016)
--  con grano ledger_event.product_id (tarea 3 de plans/fabrica-01.md).
-- =============================================================================

CREATE TYPE campana_rol AS ENUM (
  'auto_discovery', 'category_phrase', 'product_targeting', 'category_broad', 'category_exact'
);
COMMENT ON TYPE campana_rol IS
  'FABRICA 01 §3: los 5 roles fijos de un grupo. La exact recibe el harvest.';

-- ---------------------------------------------------------------------------
-- Ledger de creacion
-- ---------------------------------------------------------------------------
CREATE TABLE fabrica_lote (
  lote          TEXT        PRIMARY KEY,
  platform      platform    NOT NULL,
  tipo_producto TEXT        NOT NULL CHECK (tipo_producto ~ '^[a-z0-9_]+$'),
  nombre_base   TEXT        NOT NULL CHECK (btrim(nombre_base) <> ''),
  go_literal    TEXT        NOT NULL CHECK (btrim(go_literal) <> ''),
  huella        TEXT        NOT NULL,
  plan          JSONB       NOT NULL,
  modo_goal     TEXT        NOT NULL CHECK (modo_goal IN ('shadow', 'live')),
  estado        TEXT        NOT NULL
                CHECK (estado IN ('planeado', 'applied', 'failed', 'desarmado')),
  detalle       TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ
);
COMMENT ON TABLE fabrica_lote IS
  'FABRICA 01 §5: un lote = una corrida real de la fabrica. `plan` congela el '
  'dry-run autorizado (huella, target, bids, budgets, semillas, productos); '
  'nace planeado ANTES del primer HTTP y se sella applied/failed al final; '
  'desarmado = las 5 pausadas por --desarmar.';

CREATE TABLE fabrica_lote_paso (
  id              BIGSERIAL   PRIMARY KEY,
  lote            TEXT        NOT NULL REFERENCES fabrica_lote(lote),
  orden           INT         NOT NULL,
  rol             campana_rol NOT NULL,
  recurso         TEXT        NOT NULL CHECK (recurso IN
                    ('campaign', 'ad_group', 'product_ad', 'keyword', 'target', 'negative_keyword')),
  request_payload JSONB       NOT NULL,
  external_id     TEXT,
  ack             JSONB,
  readback_estado TEXT,
  estado          TEXT        NOT NULL CHECK (estado IN ('planeado', 'applied', 'failed')),
  intentado_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (lote, orden),
  CONSTRAINT paso_evidencia_applied CHECK (
    estado <> 'applied'
    OR (external_id IS NOT NULL AND ack IS NOT NULL AND readback_estado IS NOT NULL)
  )
);
CREATE INDEX ON fabrica_lote_paso (estado) WHERE estado IN ('planeado', 'failed');
COMMENT ON TABLE fabrica_lote_paso IS
  'FABRICA 01 §5/§8: un paso por POST (campana, ad group, cada product ad, '
  'cada semilla). Fila planeado + commit ANTES del HTTP (regla 7); applied '
  'exige el id externo, el ack y el readback que lo confirmaron; failed puede '
  'no traer readback (el POST lanzo). --reconciliar cruza planeado/failed '
  'contra el LIST real.';

-- ---------------------------------------------------------------------------
-- Grupo
-- ---------------------------------------------------------------------------
CREATE TABLE campana_grupo (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  platform            platform     NOT NULL,
  tipo_producto       TEXT         NOT NULL CHECK (tipo_producto ~ '^[a-z0-9_]+$'),
  nombre_base         TEXT         NOT NULL CHECK (btrim(nombre_base) <> ''),
  lote                TEXT         NOT NULL UNIQUE REFERENCES fabrica_lote(lote),
  target_acos_pct     NUMERIC(6, 2) NOT NULL CHECK (target_acos_pct > 0),
  target_derivado_pct NUMERIC(10, 4) NOT NULL,
  fraccion            NUMERIC(6, 4) NOT NULL CHECK (fraccion > 0 AND fraccion <= 1),
  target_procedencia  TEXT         NOT NULL,
  go_literal          TEXT         NOT NULL,
  created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE campana_grupo IS
  'FABRICA 01 §4: target CONGELADO al crear = clamp(fraccion x margen minimo '
  'de los productos, [10, 45]); target_derivado_pct es el crudo pre-clamp y '
  'target_procedencia lo explica. El re-ajuste posterior queda FUERA '
  '(residual 3). tipo_producto = etiqueta del dueno (decision 7).';

CREATE TABLE campana_grupo_rol (
  grupo_id              BIGINT      NOT NULL REFERENCES campana_grupo(id),
  rol                   campana_rol NOT NULL,
  ad_entity_id          BIGINT      NOT NULL REFERENCES ad_entity(id),
  ad_group_ad_entity_id BIGINT      NOT NULL REFERENCES ad_entity(id),
  PRIMARY KEY (grupo_id, rol),
  UNIQUE (ad_entity_id),
  UNIQUE (ad_group_ad_entity_id)
);
COMMENT ON TABLE campana_grupo_rol IS
  'FABRICA 01 §7/§8: EL vinculo campana<->grupo (jamas por nombre). Una '
  'campana pertenece a lo sumo a un grupo. El ad group de cada rol va '
  'GUARDADO (no resuelto por parent_id): el destino del harvest de F2 es un '
  'SELECT directo sobre rol = category_exact.';

-- Patron goal_scope_campana_real: la FK sola no garantiza kinds.
CREATE FUNCTION campana_grupo_rol_kinds() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM 1 FROM ad_entity WHERE id = NEW.ad_entity_id AND kind = 'campaign';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'campana_grupo_rol: ad_entity_id % no es kind=campaign', NEW.ad_entity_id
            USING ERRCODE = 'check_violation';
    END IF;
    PERFORM 1 FROM ad_entity
     WHERE id = NEW.ad_group_ad_entity_id AND kind = 'ad_group' AND parent_id = NEW.ad_entity_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'campana_grupo_rol: ad_group_ad_entity_id % no es un ad_group hijo de %',
            NEW.ad_group_ad_entity_id, NEW.ad_entity_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER campana_grupo_rol_kinds
    BEFORE INSERT OR UPDATE ON campana_grupo_rol
    FOR EACH ROW EXECUTE FUNCTION campana_grupo_rol_kinds();
COMMENT ON FUNCTION campana_grupo_rol_kinds IS
  'FABRICA 01: la campana del rol es kind=campaign y su ad group es '
  'kind=ad_group con parent_id = esa campana (la FK sola no lo garantiza).';

CREATE TABLE campana_grupo_producto (
  grupo_id        BIGINT NOT NULL REFERENCES campana_grupo(id),
  product_id      BIGINT NOT NULL REFERENCES product(id),
  listing_id      BIGINT NOT NULL REFERENCES listing(id),
  seller_sku      TEXT   NOT NULL CHECK (btrim(seller_sku) <> ''),
  margen_neto_pct NUMERIC(10, 4) NOT NULL,
  PRIMARY KEY (grupo_id, product_id)
);
COMMENT ON TABLE campana_grupo_producto IS
  'FABRICA 01 §4/§8: snapshot al alta de cada producto del grupo con el '
  'margen que entro al minimo y el SKU del product ad (listing.seller_sku).';

-- Patron campana_grupo_rol_kinds: la FK sola admite un listing de OTRO
-- producto o de OTRA plataforma; el snapshot debe cuadrar.
CREATE FUNCTION campana_grupo_producto_listing() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM 1
      FROM listing l
      JOIN campana_grupo cg ON cg.id = NEW.grupo_id
     WHERE l.id = NEW.listing_id
       AND l.product_id = NEW.product_id
       AND l.platform = cg.platform;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'campana_grupo_producto: listing % no es del producto % en la '
            'plataforma del grupo %', NEW.listing_id, NEW.product_id, NEW.grupo_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER campana_grupo_producto_listing
    BEFORE INSERT OR UPDATE ON campana_grupo_producto
    FOR EACH ROW EXECUTE FUNCTION campana_grupo_producto_listing();
COMMENT ON FUNCTION campana_grupo_producto_listing IS
  'FABRICA 01: el listing del snapshot pertenece AL producto y a la '
  'plataforma del grupo (la FK sola no lo garantiza).';

-- ---------------------------------------------------------------------------
-- Biblioteca acumulativa por tipo_producto (decision 6)
-- ---------------------------------------------------------------------------
CREATE TABLE keyword_biblioteca (
  id            BIGSERIAL   PRIMARY KEY,
  tipo_producto TEXT        NOT NULL CHECK (tipo_producto ~ '^[a-z0-9_]+$'),
  platform      platform    NOT NULL,
  texto         TEXT        NOT NULL CHECK (btrim(texto) <> ''),
  origen        TEXT        NOT NULL,
  orders        INT         NOT NULL DEFAULT 0 CHECK (orders >= 0),
  cost          money_amount,
  revenue       money_amount,
  moneda        currency,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tipo_producto, platform, texto),
  CONSTRAINT biblioteca_dinero_con_moneda
    CHECK ((cost IS NULL AND revenue IS NULL) OR moneda IS NOT NULL)
);
COMMENT ON TABLE keyword_biblioteca IS
  'FABRICA 01 §6: terminos con historial por (tipo_producto, platform). F1 '
  'siembra phrase/broad con orders >= 1 y product targeting con los ASIN-like; '
  'F2 la alimenta desde cada harvest aplicado. Dinero con moneda (regla 4).';

CREATE TABLE negative_biblioteca (
  id            BIGSERIAL   PRIMARY KEY,
  tipo_producto TEXT        NOT NULL CHECK (tipo_producto ~ '^[a-z0-9_]+$'),
  platform      platform    NOT NULL,
  texto         TEXT        NOT NULL CHECK (btrim(texto) <> ''),
  origen        TEXT        NOT NULL,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tipo_producto, platform, texto)
);
COMMENT ON TABLE negative_biblioteca IS
  'FABRICA 01 §6: negativos por (tipo_producto, platform). F1 siembra la '
  'auto_discovery con ellos; F2 la alimenta desde cada negative aplicado.';

-- ---------------------------------------------------------------------------
-- Excepciones de harvest (schema en F1; se puebla en F2 con go, decision 4)
-- ---------------------------------------------------------------------------
CREATE TABLE harvest_excepcion (
  ad_entity_id              BIGINT      PRIMARY KEY REFERENCES ad_entity(id),
  destino_campaign_external TEXT        NOT NULL CHECK (btrim(destino_campaign_external) <> ''),
  destino_ad_group_external TEXT        NOT NULL CHECK (btrim(destino_ad_group_external) <> ''),
  go_literal                TEXT        NOT NULL,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE harvest_excepcion IS
  'FABRICA 01 §7 (F2): destino de harvest CONGELADO de una campana sin grupo, '
  'migrada una a una con go del dueno («que queden asi ya»). F1 solo crea la '
  'tabla; nadie la escribe hasta F2.';

-- Patron goal_scope_campana_real: la excepcion es de una CAMPANA sin grupo.
CREATE FUNCTION harvest_excepcion_kind() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM 1 FROM ad_entity WHERE id = NEW.ad_entity_id AND kind = 'campaign';
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'harvest_excepcion: ad_entity_id % no existe o no es kind=campaign',
            NEW.ad_entity_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER harvest_excepcion_kind
    BEFORE INSERT OR UPDATE ON harvest_excepcion
    FOR EACH ROW EXECUTE FUNCTION harvest_excepcion_kind();
COMMENT ON FUNCTION harvest_excepcion_kind IS
  'FABRICA 01: la excepcion de harvest se congela sobre kind=campaign (la FK '
  'sola admite cualquier entidad).';

-- ---------------------------------------------------------------------------
-- GRANTs: app_admin escribe (la fabrica corre con ORBIT_DSN_ADMIN); el motor
-- (app_decide) y la lectura solo leen. USAGE SOLO de las secuencias de las
-- tablas nuevas y SOLO para app_admin (el unico que inserta; app_ingest y
-- app_decide tienen SELECT puro, que no toca secuencias).
-- ---------------------------------------------------------------------------
GRANT SELECT ON fabrica_lote, fabrica_lote_paso, campana_grupo, campana_grupo_rol,
    campana_grupo_producto, keyword_biblioteca, negative_biblioteca, harvest_excepcion
    TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT, UPDATE ON fabrica_lote, fabrica_lote_paso, campana_grupo, campana_grupo_rol,
    campana_grupo_producto, keyword_biblioteca, negative_biblioteca, harvest_excepcion
    TO app_admin;
GRANT USAGE ON SEQUENCE fabrica_lote_paso_id_seq, campana_grupo_id_seq,
    keyword_biblioteca_id_seq, negative_biblioteca_id_seq TO app_admin;
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `pytest tests/test_fabrica_migracion.py -v`
Expected: PASS los 8 tests (el estático siempre; los de Postgres si hay servidor local o `ORBIT_TEST_DSN`).

- [ ] **Step 5: Commit**

```bash
git checkout -b fabrica-01-2-migracion origin/master
git add migrations/0018_fabrica_campanas.sql tests/test_fabrica_migracion.py
git commit -m "feat(fabrica): migracion 0018 — grupo, biblioteca y ledger de creacion (spec §8)"
```

---

### Task 3: `v_margen_producto` (misma migración 0018)

**Files:**
- Modify: `migrations/0018_fabrica_campanas.sql` (agregar la vista al final, antes de los GRANTs de vistas)
- Modify: `tests/test_fabrica_migracion.py`

**Interfaces:**
- Consumes: `ledger_event`, `sku_cost`, `ingest_run` (source `accounting_ledger_events`); constantes de guard de `app/optimizer/goals.py` (`MARGEN_COBERTURA_MIN = 0.95`, `MARGEN_DIAS_MIN = 60`).
- Produces: vista `v_margen_producto(platform, product_id, ventana_desde, ventana_hasta, venta_total, venta_cubierta, cargos_con_orden, cargos_sin_orden, cogs, cobertura, dias_con_venta, fees_sin_tipo, margen_neto_pct, ledger_fresco_at, moneda)`. `margen_neto_pct` NULL ante cualquier guard (regla 3); la fila existe solo si el producto vendió en ventana.

- [ ] **Step 1: Escribir los tests de la vista (fallan: la vista no existe)**

```python
# agregar a tests/test_fabrica_migracion.py


def _ledger_producto(conn, pid, *, hoy, platform="amazon_mx", ventas=70, precio=100, costo=50):
    """`ventas` dias consecutivos con UNA venta de `precio` y costo `costo`
    (misma moneda), todas con order_id propio; 7 cargos de plataforma sin
    orden (-100) y 1 cargo ads (-9999, EXCLUIDO). Cobertura 1, margen por
    producto = 100 x (ventas*precio + cargos_sin_orden - ventas*costo) / (ventas*precio)."""
    conn.execute(
        "INSERT INTO ingest_run (source, finished_at, ok) VALUES"
        " ('accounting_ledger_events', now(), true)"
    )
    run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
    conn.execute(
        "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax, valid_from)"
        " VALUES (%s, %s, 'MXN', true, %s)",
        (pid, costo, hoy - dt.timedelta(days=200)),
    )
    for i in range(ventas):
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id, quantity,"
            " amount, amount_currency, ingest_run_id)"
            " VALUES (%s, 'sale', %s, %s, %s, 1, %s, 'MXN', %s)",
            (platform, hoy - dt.timedelta(days=100 - i), f"o-{pid}-{i}", pid, precio, run),
        )
    for i in range(7):
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, amount, amount_currency,"
            " fee_type, ingest_run_id) VALUES (%s, 'fee', %s, -100, 'MXN', 'closing', %s)",
            (platform, hoy - dt.timedelta(days=90 - i), run),
        )
    conn.execute(
        "INSERT INTO ledger_event (platform, kind, event_date, amount, amount_currency,"
        " fee_type, ingest_run_id) VALUES (%s, 'fee', %s, -9999, 'MXN', 'ads', %s)",
        (platform, hoy - dt.timedelta(days=50), run),
    )
    return run


@_skip_db
def test_v_margen_producto_un_producto_reproduce_la_plataforma():
    """Con UN solo producto, el margen por producto es EXACTAMENTE el de
    v_target_margen_plataforma (misma maquinaria, regla 2): 70 ventas x100,
    costo 50, 7 cargos sin orden -100 prorrateados por cobertura 1 ->
    100 x (7000 - 700 - 3500) / 7000 = 40.0."""
    hoy = dt.date.today()
    with db_fabrica() as conn:
        pid, _ = _producto(conn)
        _ledger_producto(conn, pid, hoy=hoy)
        prod = conn.execute(
            "SELECT margen_neto_pct, cobertura, dias_con_venta, moneda, cargos_sin_orden"
            " FROM v_margen_producto WHERE platform = 'amazon_mx' AND product_id = %s",
            (pid,),
        ).fetchone()
        plat = conn.execute(
            "SELECT margen_neto_pct FROM v_target_margen_plataforma WHERE platform = 'amazon_mx'"
        ).fetchone()
        assert prod is not None and plat is not None
        assert prod[0] == Decimal("40") == plat[0]
        assert prod[1] == 1 and prod[2] == 70 and prod[3] == "MXN"
        assert prod[4] == Decimal("-700")


@_skip_db
def test_v_margen_producto_prorratea_cargos_con_orden_por_monto():
    """Orden multi-producto (spec §8): un cargo -30 con order_id de una orden
    donde A vendio 100 y B vendio 200 se reparte 1/3 a A y 2/3 a B. Solo una
    venta por producto -> dias < 60 -> margen NULL (guard), pero las columnas
    de cargos si se publican (la vista MIDE)."""
    hoy = dt.date.today()
    with db_fabrica() as conn:
        pa, _ = _producto(conn, sku="A", asin="B0AAAAAAAA", seller_sku="SA")
        pb, _ = _producto(conn, sku="B", asin="B0BBBBBBBB", seller_sku="SB")
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
        conn.execute(
            "INSERT INTO ingest_run (source, finished_at, ok) VALUES"
            " ('accounting_ledger_events', now(), true)"
        )
        fecha = hoy - dt.timedelta(days=40)
        for pid in (pa, pb):
            conn.execute(
                "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
                " valid_from) VALUES (%s, 10, 'MXN', true, %s)",
                (pid, hoy - dt.timedelta(days=200)),
            )
        for pid, monto in ((pa, 100), (pb, 200)):
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
                " quantity, amount, amount_currency, ingest_run_id)"
                " VALUES ('amazon_mx', 'sale', %s, 'o-multi', %s, 1, %s, 'MXN', %s)",
                (fecha, pid, monto, run),
            )
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, amount,"
            " amount_currency, fee_type, ingest_run_id)"
            " VALUES ('amazon_mx', 'fee', %s, 'o-multi', -30, 'MXN', 'closing', %s)",
            (fecha, run),
        )
        filas = dict(
            conn.execute(
                "SELECT product_id, cargos_con_orden FROM v_margen_producto"
                " WHERE platform = 'amazon_mx'"
            ).fetchall()
        )
        assert filas[pa] == Decimal("-10") and filas[pb] == Decimal("-20")
        margenes = dict(
            conn.execute("SELECT product_id, margen_neto_pct FROM v_margen_producto").fetchall()
        )
        assert margenes[pa] is None and margenes[pb] is None  # dias_con_venta = 1 < 60


@_skip_db
def test_v_margen_producto_sin_costo_no_cubre_y_sin_venta_no_existe():
    """Regla 3: venta sin sku_cost -> venta_cubierta 0 -> margen NULL; un
    producto sin ventas en ventana NO tiene fila (jamas cero)."""
    hoy = dt.date.today()
    with db_fabrica() as conn:
        pid, _ = _producto(conn)
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', %s, 'o1', %s, 1, 100, 'MXN', %s)",
            (hoy - dt.timedelta(days=40), pid, run),
        )
        fila = conn.execute(
            "SELECT margen_neto_pct, venta_cubierta FROM v_margen_producto WHERE product_id = %s",
            (pid,),
        ).fetchone()
        # NULL EXIGIDO en ambas (regla 3): venta_cubierta es SUM(...) FILTER de
        # lineas cubiertas -> NULL si ninguna; un COALESCE a 0 seria un cero inventado.
        assert fila == (None, None)
        otro, _ = _producto(conn, sku="X", asin="B0XXXXXXXX", seller_sku="SX")
        assert (
            conn.execute("SELECT count(*) FROM v_margen_producto WHERE product_id = %s", (otro,))
            .fetchone()[0]
            == 0
        )


@_skip_db
def test_v_margen_producto_mezcla_de_moneda_en_denominadores_es_null():
    """r3 codex 2 (regla 4): los DENOMINADORES del prorrateo tambien hacen
    guard. (i) una unica venta USD de OTRO producto en la plataforma NULLea
    el margen del producto puro MXN (venta_plataforma quedo en 2 monedas);
    (ii) una orden con lineas cubiertas en dos monedas NULLea por
    n_monedas_orden (la misma linea USD tambien enciende el guard de
    plataforma: ambos guards son fail-closed sobre el mismo hecho)."""
    hoy = dt.date.today()
    with db_fabrica() as conn:  # (i) denominador de la plataforma
        pa, _ = _producto(conn)
        _ledger_producto(conn, pa, hoy=hoy)  # puro MXN: margen 40 sin la mezcla
        pb, _ = _producto(conn, sku="USD", asin="B0USDUSDUS", seller_sku="SUSD")
        run = conn.execute(
            "INSERT INTO ingest_run (source) VALUES ('t') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
            " quantity, amount, amount_currency, ingest_run_id)"
            " VALUES ('amazon_mx', 'sale', %s, 'o-usd', %s, 1, 25, 'USD', %s)",
            (hoy - dt.timedelta(days=50), pb, run),
        )
        fila = conn.execute(
            "SELECT margen_neto_pct FROM v_margen_producto WHERE product_id = %s", (pa,)
        ).fetchone()
        assert fila[0] is None  # venta_plataforma en 2 monedas
    with db_fabrica() as conn:  # (ii) denominador de la orden
        pa, _ = _producto(conn)
        _ledger_producto(conn, pa, hoy=hoy)
        pb, _ = _producto(conn, sku="USD", asin="B0USDUSDUS", seller_sku="SUSD")
        run = conn.execute(
            "INSERT INTO ingest_run (source) VALUES ('t') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,"
            " valid_from) VALUES (%s, 10, 'USD', true, %s)",
            (pb, hoy - dt.timedelta(days=200)),
        )
        for pid, monto, moneda in ((pb, 25, "USD"), (pa, 100, "MXN")):
            conn.execute(
                "INSERT INTO ledger_event (platform, kind, event_date, order_id, product_id,"
                " quantity, amount, amount_currency, ingest_run_id)"
                " VALUES ('amazon_mx', 'sale', %s, 'o-mixta', %s, 1, %s, %s, %s)",
                (hoy - dt.timedelta(days=50), pid, monto, moneda, run),
            )
        fila = conn.execute(
            "SELECT margen_neto_pct FROM v_margen_producto WHERE product_id = %s", (pa,)
        ).fetchone()
        assert fila[0] is None  # orden cubierta en 2 monedas (n_monedas_orden)
```

- [ ] **Step 2: Correr y ver el rojo**

Run: `pytest tests/test_fabrica_migracion.py -k v_margen_producto -v`
Expected: FAIL con `UndefinedTable: relation "v_margen_producto" does not exist`.

- [ ] **Step 3: Agregar la vista a la migración (antes del bloque de GRANTs, y sumar la vista al GRANT SELECT)**

```sql
-- ---------------------------------------------------------------------------
-- v_margen_producto: v_target_margen_plataforma (0016) con grano product_id.
--   ventas: solo lineas con product_id; cobertura por MONTO; COGS vigente a
--           la fecha en la MISMA moneda (o la linea es NO cubierta).
--   cargos con order_id: pertenecen a su orden (sin filtro de fecha propio,
--           A5) y se PRORRATEAN a cada producto por su monto de venta dentro
--           de la orden (spec §8), contando solo lineas CUBIERTAS.
--   cargos sin order_id: de plataforma, en ventana, prorrateados por la
--           participacion de la venta cubierta del producto en la venta
--           total de la plataforma.
--   guards identicos a la plataforma (moneda unica, fees_sin_tipo = 0,
--           cobertura >= 0.95, dias >= 60, cubierta > 0) -> margen NULL.
--   ads (fee_type = 'ads') EXCLUIDO: es el numerador del ACoS del motor.
--   fees_sin_tipo es un COUNT de GUARD fail-loud, NO dinero: se infla cuando
--           una orden trae varias lineas de cargo del mismo producto (cuenta
--           lineas, no montos). Es intencional: cualquier fee sin tipo anula
--           el margen (NULL); no hay doble conteo de dinero.
--   cargos_con_orden/cargos_sin_orden/cogs publican 0 por COALESCE cuando no
--           hay cargos (igual que v_target_margen_plataforma): esas columnas
--           MIDEN y no son NULL-aware; el MARGEN si va NULL ante cada guard.
-- ---------------------------------------------------------------------------
CREATE VIEW v_margen_producto AS
WITH ventana AS (
    SELECT CURRENT_DATE - 105 AS desde, CURRENT_DATE - 15 AS hasta
),
ventas AS (
    SELECT l.platform, l.product_id, l.event_date, l.order_id,
           l.amount, l.amount_currency,
           CASE WHEN c.id IS NOT NULL AND c.cost_currency = l.amount_currency
                THEN c.cost_amount * l.quantity END AS cogs_linea
      FROM ledger_event l
      CROSS JOIN ventana v
      LEFT JOIN sku_cost c
        ON c.product_id = l.product_id
       AND l.event_date >= c.valid_from
       AND (c.valid_to IS NULL OR l.event_date < c.valid_to)
     WHERE l.kind = 'sale' AND l.product_id IS NOT NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
),
orden_cubierta AS (
    -- venta cubierta por orden: base del prorrateo de sus cargos. n_monedas:
    -- guard del DENOMINADOR (r3 codex 2): una orden con lineas cubiertas en
    -- dos monedas no puede prorratear (regla 4 — NULL, no suma a ciegas).
    SELECT platform, order_id, SUM(amount) AS venta_orden,
           COUNT(DISTINCT amount_currency) AS n_monedas
      FROM ventas
     WHERE order_id IS NOT NULL AND cogs_linea IS NOT NULL
     GROUP BY platform, order_id
),
cargos_orden AS (
    SELECT l.platform, l.order_id,
           SUM(l.amount) AS monto,
           COUNT(DISTINCT l.amount_currency) AS n_monedas,
           MAX(l.amount_currency::text) AS moneda,
           COUNT(*) FILTER (WHERE l.fee_type IS NULL) AS fees_sin_tipo
      FROM ledger_event l
      JOIN orden_cubierta o ON o.platform = l.platform AND o.order_id = l.order_id
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
     GROUP BY l.platform, l.order_id
),
cargos_producto AS (
    SELECT v.platform, v.product_id,
           SUM(co.monto * v.amount / o.venta_orden) AS cargos_con_orden,
           SUM(co.fees_sin_tipo) AS fees_sin_tipo,
           MAX(co.n_monedas) AS n_monedas_cargos,
           MAX(o.n_monedas) AS n_monedas_orden,
           MAX(co.moneda) AS moneda_cargos
      FROM ventas v
      JOIN orden_cubierta o ON o.platform = v.platform AND o.order_id = v.order_id
      JOIN cargos_orden co ON co.platform = v.platform AND co.order_id = v.order_id
     WHERE v.cogs_linea IS NOT NULL
     GROUP BY v.platform, v.product_id
),
plataforma AS (
    SELECT l.platform,
           SUM(l.amount) AS monto_sin_orden,
           COUNT(*) FILTER (WHERE l.fee_type IS NULL) AS fees_sin_tipo,
           COUNT(DISTINCT l.amount_currency) AS n_monedas,
           MAX(l.amount_currency::text) AS moneda
      FROM ledger_event l
      CROSS JOIN ventana v
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
       AND l.order_id IS NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
     GROUP BY l.platform
),
venta_plataforma AS (
    -- n_monedas: guard del DENOMINADOR (r3 codex 2): el sistema viejo
    -- reportaba MXN hasta en amazon_us; con ventas de la plataforma en dos
    -- monedas la fraccion no puede calcularse (regla 4).
    SELECT platform, SUM(amount) AS venta_total,
           COUNT(DISTINCT amount_currency) AS n_monedas
      FROM ventas GROUP BY platform
),
ag AS (
    SELECT platform, product_id,
           SUM(amount) AS venta_total,
           SUM(amount) FILTER (WHERE cogs_linea IS NOT NULL) AS venta_cubierta,
           SUM(cogs_linea) AS cogs_conocido,
           COUNT(DISTINCT event_date) AS dias_con_venta,
           COUNT(DISTINCT amount_currency) AS n_monedas,
           MAX(amount_currency) AS moneda_unica
      FROM ventas
     GROUP BY platform, product_id
),
fresco AS (
    SELECT MAX(started_at) AS ledger_fresco_at
      FROM ingest_run
     WHERE source = 'accounting_ledger_events' AND ok
)
SELECT a.platform,
       a.product_id,
       (SELECT desde FROM ventana) AS ventana_desde,
       (SELECT hasta FROM ventana) AS ventana_hasta,
       a.venta_total,
       a.venta_cubierta,
       COALESCE(cp.cargos_con_orden, 0) AS cargos_con_orden,
       COALESCE(p.monto_sin_orden, 0) * COALESCE(a.venta_cubierta, 0)
           / NULLIF(vp.venta_total, 0) AS cargos_sin_orden,
       COALESCE(a.cogs_conocido, 0) AS cogs,
       CASE WHEN a.venta_total > 0 THEN COALESCE(a.venta_cubierta, 0) / a.venta_total END
           AS cobertura,
       a.dias_con_venta,
       COALESCE(cp.fees_sin_tipo, 0) + COALESCE(p.fees_sin_tipo, 0) AS fees_sin_tipo,
       CASE
           WHEN a.n_monedas <> 1 THEN NULL
           WHEN COALESCE(cp.n_monedas_cargos, 0) > 1 OR COALESCE(p.n_monedas, 0) > 1 THEN NULL
           WHEN cp.moneda_cargos IS NOT NULL AND cp.moneda_cargos <> a.moneda_unica::text THEN NULL
           WHEN p.moneda IS NOT NULL AND p.moneda <> a.moneda_unica::text THEN NULL
           WHEN COALESCE(cp.fees_sin_tipo, 0) + COALESCE(p.fees_sin_tipo, 0) > 0 THEN NULL
           WHEN a.venta_cubierta IS NULL OR a.venta_cubierta <= 0 THEN NULL
           WHEN a.venta_cubierta / NULLIF(a.venta_total, 0) < 0.95 THEN NULL
           WHEN a.dias_con_venta < 60 THEN NULL
           WHEN COALESCE(cp.n_monedas_orden, 0) > 1 THEN NULL
           WHEN vp.n_monedas > 1 THEN NULL
           ELSE 100.0 * (a.venta_cubierta
                + COALESCE(cp.cargos_con_orden, 0)
                + COALESCE(p.monto_sin_orden, 0) * a.venta_cubierta / NULLIF(vp.venta_total, 0)
                - COALESCE(a.cogs_conocido, 0)) / a.venta_cubierta
       END AS margen_neto_pct,
       fr.ledger_fresco_at,
       CASE WHEN a.n_monedas = 1 THEN a.moneda_unica END AS moneda
  FROM ag a
  JOIN venta_plataforma vp ON vp.platform = a.platform
  CROSS JOIN fresco fr
  LEFT JOIN cargos_producto cp ON cp.platform = a.platform AND cp.product_id = a.product_id
  LEFT JOIN plataforma p ON p.platform = a.platform;

COMMENT ON VIEW v_margen_producto IS
  'FABRICA 01 §4/§8: margen neto % POR PRODUCTO con la maquinaria de '
  'v_target_margen_plataforma (0016): ventana [D-105, D-15) UTC, COGS a la '
  'fecha en la misma moneda, cobertura por monto, cargos no-ads con order_id '
  'prorrateados por el monto del producto dentro de su orden (solo lineas '
  'cubiertas), cargos sin order_id prorrateados por la participacion del '
  'producto en la venta de la plataforma. NULL ante mezcla de moneda '
  '(incluidos los DENOMINADORES del prorrateo: la orden cubierta o la venta '
  'total de la plataforma en mas de una moneda — regla 4), '
  'fees_sin_tipo > 0, cobertura < 0.95, dias < 60 o cubierta <= 0 (regla 3). '
  'fees_sin_tipo es un COUNT de guard fail-closed (se infla con varias '
  'lineas de cargo de una misma orden; intencional, no es dinero). Las '
  'columnas cargos_*/cogs publican 0 por COALESCE (miden); solo el margen '
  'usa NULL. Solo MIDE: el target del grupo lo deriva app/fabrica_plan.py y '
  'queda congelado en campana_grupo.';

GRANT SELECT ON v_margen_producto TO app_read, app_ingest, app_decide, app_admin;
```

Nota de regla 2: el guard `dias < 60` se queda en 60 salvo decisión escrita de la tarea 1 (si cambia, cambia AQUÍ y la constante `MARGEN_DIAS_MIN_PRODUCTO` de la tarea 5 con su test que pinea el número del SQL).

- [ ] **Step 4: Correr y ver el verde**

Run: `pytest tests/test_fabrica_migracion.py -v`
Expected: PASS (10 tests). Si `test_v_margen_producto_un_producto_reproduce_la_plataforma` difiere de la plataforma, el bug está en el prorrateo: con un producto y cobertura 1, `cargos_sin_orden` debe ser exactamente el total de plataforma.

- [ ] **Step 5: Commit (misma rama que la tarea 2, mismo PR)**

```bash
git add migrations/0018_fabrica_campanas.sql tests/test_fabrica_migracion.py
git commit -m "feat(fabrica): v_margen_producto — margen por producto con la maquinaria de plataforma (spec §4)"
```

---

### Task 4: `crea_goal` en `app/goals_write.py`

**Files:**
- Modify: `app/goals_write.py` (después de `edita_goal`)
- Modify: `tests/test_goals_write.py`

**Interfaces:**
- Consumes: `_valida_pre_editar`, `_fila_respuesta`, `_COLUMNAS`, `resuelve_floor_ceiling` (ya en el módulo); trigger `goal_scope_campana_real` y `goal_unico_campana` (0001).
- Produces: `crea_goal(conn, *, ad_entity_id: int, target_acos_pct: Decimal, bid_currency: str, mode: str, harvest_campaign_id: str, harvest_ad_group_id: str, harvest_default_bid: Decimal, created_at: dt.datetime, enabled: bool = True) -> dict` (shape de GET /goals). Excepciones: `GoalInvalido` (uso), `ValueError` (`created_at` None/naive).

- [ ] **Step 1: Tests (fallan: `crea_goal` no existe)**

```python
# agregar a tests/test_goals_write.py

T_CREADO = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.UTC)


def _kw_crea(**cambios):
    base = dict(
        ad_entity_id=1,
        target_acos_pct=Decimal("19.10"),
        bid_currency="MXN",
        mode="shadow",
        harvest_campaign_id="c-exact",
        harvest_ad_group_id="ag-exact",
        harvest_default_bid=Decimal("6.00"),
        created_at=T_CREADO,
    )
    base.update(cambios)
    return base


class _ConnMuda:
    """Fail-closed: la validacion pura de crea_goal NO abre SQL — cualquier
    execute revienta el test. (`_ConnFake` de test_api_write solo expone
    `close`: no tiene `execute` ni `ejecutadas`, asi que la guarda es este
    fake local.)"""

    row_factory = None

    def execute(self, *a):
        raise AssertionError("SQL prohibido en la validacion pura de crea_goal")


def test_crea_goal_valida_sin_io():
    """Validacion PURA antes de tocar la base: modo fuera de shadow/live,
    target <= 0, bid <= 0, ids vacios, moneda desconocida y created_at naive.
    Si cualquier rama abriera SQL, _ConnMuda.execute ya revento."""
    conn = _ConnMuda()
    for malo in (
        _kw_crea(mode="off"),
        _kw_crea(mode="LIVE"),
        _kw_crea(target_acos_pct=Decimal("0")),
        _kw_crea(harvest_default_bid=Decimal("-1")),
        _kw_crea(harvest_campaign_id="  "),
        _kw_crea(harvest_ad_group_id=""),
        _kw_crea(target_acos_pct=Decimal("Infinity")),
    ):
        with pytest.raises(goals_write.GoalInvalido):
            goals_write.crea_goal(conn, **malo)
    with pytest.raises(goals_write.GoalInvalido, match="moneda"):
        goals_write.crea_goal(conn, **_kw_crea(bid_currency="EUR"))
    with pytest.raises(ValueError, match="created_at"):
        goals_write.crea_goal(conn, **_kw_crea(created_at=dt.datetime(2026, 9, 5, 12, 0)))
    # nada que afirmar sobre la conexion: _ConnMuda.execute revienta solo


@_skip_db
def test_crea_goal_inserta_con_defaults_de_su_moneda_y_terna_completa():
    """PG real: INSERT de goal de campana MXN con floor/ceiling de
    DEFAULTS_POR_MONEDA (1.00/45.00, jamas 0.10/2.50), enabled, mode y la
    terna harvest completa; created_at/updated_at = el instante pasado."""
    from test_cycle import _db_temporal, _entidad

    with _db_temporal("orbit_goals_crea") as (conn, _):
        camp = _entidad(conn, "amazon_mx", "campaign", "c-1")
        fila = goals_write.crea_goal(conn, **_kw_crea(ad_entity_id=camp))
        assert Decimal(fila["bid_floor"]) == Decimal("1.00")
        assert Decimal(fila["bid_ceiling"]) == Decimal("45.00")
        assert fila["mode"] == "shadow" and fila["enabled"] is True
        assert fila["harvest_campaign_id"] == "c-exact"
        assert Decimal(fila["harvest_default_bid"]) == Decimal("6.00")
        assert fila["created_at"] == T_CREADO and fila["updated_at"] == T_CREADO
        # segundo goal para la MISMA campana: goal_unico_campana -> GoalInvalido
        with pytest.raises(goals_write.GoalInvalido, match="ya tiene goal"):
            goals_write.crea_goal(conn, **_kw_crea(ad_entity_id=camp))


@_skip_db
def test_crea_goal_rechaza_entidad_que_no_es_campana():
    """Trigger goal_scope_campana_real traducido a GoalInvalido (no un
    CheckViolation crudo)."""
    from test_cycle import _db_temporal, _entidad

    with _db_temporal("orbit_goals_crea_kind") as (conn, _):
        camp = _entidad(conn, "amazon_mx", "campaign", "c-1")
        ag = _entidad(conn, "amazon_mx", "ad_group", "ag-1", parent=camp)
        with pytest.raises(goals_write.GoalInvalido, match="kind=campaign"):
            goals_write.crea_goal(conn, **_kw_crea(ad_entity_id=ag))
```

(La conexion falsa es `_ConnMuda`, definida arriba: `_ConnFake` de `test_api_write` solo expone `close` — sin `execute` ni `ejecutadas` — asi que no sirve para esta guarda.)

- [ ] **Step 2: Rojo**

Run: `pytest tests/test_goals_write.py -k crea_goal -v`
Expected: FAIL con `AttributeError: module 'app.goals_write' has no attribute 'crea_goal'`.

- [ ] **Step 3: Implementar**

Además del código de abajo, actualizar el docstring del MÓDULO `app/goals_write.py`: la frase «`edita_goal` es la UNICA escritura de `ads_optimizer_goal`» deja de ser cierta con `crea_goal` — debe quedar «la escritura de `ads_optimizer_goal` vive SOLO en este módulo: `edita_goal` (UPDATE) y `crea_goal` (INSERT, FABRICA 01); cli/api_write/tools despachan, jamas duplican SQL».

```python
# app/goals_write.py — despues de edita_goal

MODOS_CREACION = ("shadow", "live")

_SQL_CREA = (
    "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct, bid_floor,"
    " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
    " harvest_default_bid, enabled, mode, created_at, updated_at)"
    " VALUES ('campaign', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    f" RETURNING {_COLUMNAS}"
)


def crea_goal(
    conn: psycopg.Connection,
    *,
    ad_entity_id: int,
    target_acos_pct: Decimal,
    bid_currency: str,
    mode: str,
    harvest_campaign_id: str,
    harvest_ad_group_id: str,
    harvest_default_bid: Decimal,
    created_at: dt.datetime,
    enabled: bool = True,
) -> dict:
    """Crea el goal de scope campana de una campana NUEVA (FABRICA 01, spec
    §4, decisiones 12 y 13): terna harvest COMPLETA obligatoria, `mode`
    explicito en {shadow, live} (nunca `off`: un goal apagado al nacer es
    una campana que el motor jamas toca), piso/techo de DEFAULTS_POR_MONEDA
    de SU moneda. Camino unico de escritura de goals (sellado 26 de ORBIT 04,
    docs/APPLY.md §10.3): la fabrica despacha aqui, jamas duplica el INSERT.

    Validacion PURA antes de I/O (mismo criterio que edita_goal); los
    rechazos de la base (goal_unico_campana, goal_scope_campana_real) se
    traducen a GoalInvalido con mensaje en espanol.
    """
    if created_at is None or created_at.tzinfo is None:
        raise ValueError("created_at es obligatorio y tz-aware")
    if mode not in MODOS_CREACION:
        raise GoalInvalido(f"mode debe ser uno de {MODOS_CREACION}, llego {mode!r}")
    for nombre, valor in (
        ("target_acos_pct", target_acos_pct),
        ("harvest_default_bid", harvest_default_bid),
    ):
        if not isinstance(valor, Decimal) or not valor.is_finite():
            raise GoalInvalido(f"{nombre} debe ser un Decimal finito, llego {valor!r}")
    for nombre, valor in (
        ("harvest_campaign_id", harvest_campaign_id),
        ("harvest_ad_group_id", harvest_ad_group_id),
    ):
        if not isinstance(valor, str) or not valor.strip():
            raise GoalInvalido(f"{nombre} no puede ser vacio: la terna harvest va completa")
    try:
        piso, techo = resuelve_floor_ceiling(None, bid_currency)
    except ValueError as exc:
        raise GoalInvalido(f"moneda sin defaults: {exc}") from exc
    fila_nueva = {
        "target_acos_pct": target_acos_pct,
        "bid_floor": piso,
        "bid_ceiling": techo,
        "harvest_campaign_id": harvest_campaign_id.strip(),
        "harvest_ad_group_id": harvest_ad_group_id.strip(),
        "harvest_default_bid": harvest_default_bid,
    }
    _valida_pre_editar(fila_nueva, {})  # mismos espejos de CHECK que la edicion

    conn.row_factory = dict_row
    try:
        fila = conn.execute(
            _SQL_CREA,
            (
                ad_entity_id,
                target_acos_pct,
                piso,
                techo,
                bid_currency,
                fila_nueva["harvest_campaign_id"],
                fila_nueva["harvest_ad_group_id"],
                harvest_default_bid,
                enabled,
                mode,
                created_at,
                created_at,
            ),
        ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        conn.rollback()
        raise GoalInvalido(f"la campana {ad_entity_id} ya tiene goal (goal_unico_campana)") from exc
    except psycopg.errors.CheckViolation as exc:
        conn.rollback()
        raise GoalInvalido(f"la base rechazo el goal: {exc.diag.message_primary}") from exc
    conn.commit()
    return _fila_respuesta(fila)
```

- [ ] **Step 4: Verde + candados**

El candado `test_escritura_de_goals_vive_solo_en_goals_write` (tests/test_architecture.py) solo vigilaba el `UPDATE`; con `crea_goal` el INSERT también es escritura y se extiende aquí (misma tarea, regla 2 del quality-kit):

```python
# tests/test_architecture.py — junto a _PATRON_UPDATE_GOAL
_SQL_INSERT_GOAL = r"INSERT\s+INTO\s+ads_optimizer_goal"
_PATRON_INSERT_GOAL = re.compile(_SQL_INSERT_GOAL, re.IGNORECASE)
```

```python
# y dentro de test_escritura_de_goals_vive_solo_en_goals_write, tras el assert de UPDATE:
    escritores_insert = [
        p.relative_to(RAIZ).as_posix()
        for p in APP.rglob("*.py")
        if _PATRON_INSERT_GOAL.search(p.read_text(encoding="utf-8"))
    ]
    assert escritores_insert == ["app/goals_write.py"], (
        f"INSERT de ads_optimizer_goal fuera de app/goals_write.py (crea_goal, "
        f"FABRICA 01): {escritores_insert}"
    )
```

(el docstring del candado se actualiza: «escribe `UPDATE` o `INSERT` de ads_optimizer_goal». La tarea 10 verifica que este candado extendido siga verde con el tool ya completo.)

Run: `pytest tests/test_goals_write.py tests/test_architecture.py -v`
Expected: PASS. `test_escritura_de_goals_vive_solo_en_goals_write` sigue verde (el INSERT vive en `goals_write.py`, el único dueño).

- [ ] **Step 5: Commit**

```bash
git checkout -b fabrica-01-4-crea-goal origin/master
git add app/goals_write.py tests/test_goals_write.py
git commit -m "feat(goals): crea_goal — alta de goal de campana por el camino unico (fabrica-01 tarea 4)"
```

---

### Task 5: `app/fabrica_plan.py` — núcleo puro del plan

**Files:**
- Create: `app/fabrica_plan.py`
- Create: `tests/test_fabrica_plan.py`

**Interfaces:**
- Consumes: `app.optimizer.goals` (`DEFAULTS_POR_MONEDA`, `MARGEN_BANDA_MIN`, `MARGEN_BANDA_MAX`, `_valida_fraccion`), `app.optimizer.hygiene` (`HARVEST_ORDERS_MIN`, `HARVEST_ACOS_TOPE_FIJO_PCT`). Sin psycopg ni httpx (es lógica pura; NO vive en `app/optimizer/`, así que el candado del motor no aplica, pero se mantiene pura a propósito).
- Produces (nombres EXACTOS que usan las tareas 6-9):
  - constantes `ROLES_ORDEN_CREACION`, `MATCH_POR_ROL`, `TARGETING_POR_ROL`, `MONEDA_POR_PLATAFORMA`, `MODOS_GOAL`, `ESTADO_NUEVO`, `PATH_CREATE`, `VENDOR_POR_PATH`, `ENVOLTURA_POR_PATH`, `CLAVE_ID_POR_PATH`, `LIST_POR_PATH`, `FILTRO_ID_POR_LIST`, `CONTENEDOR_POR_LIST`, `PATRON_ASIN`, `MARGEN_DIAS_MIN_PRODUCTO`, `VENTANA_DIAS = (105, 15)`, `VENTANA_CORTES_DIAS = (39, 9)`.
  - dataclasses `ProductoGrupo`, `ParametrosRol`, `TerminoProducto`, `Semillas`, `ResultadoTarget`, `PlanGrupo`, `Paso`.
  - `PlanInvalido(ValueError)`.
  - funciones `valida_tipo_producto`, `target_del_grupo`, `valida_parametros`, `nombre_campana`, `nombre_ad_group`, `semillas_desde_terminos`, `huella_plan`, `plan_como_json`, `plan_desde_json`, `monto_wire`, `pasos_del_rol`, `id_creado`, `errores_207`, `ack_ok`, `lineas_dry_run`.

- [ ] **Step 1: Tests del núcleo (fallan: el módulo no existe)**

```python
# tests/test_fabrica_plan.py
"""Nucleo PURO de la fabrica (FABRICA 01 tarea 5): target del grupo y su
clamp, montos por moneda, nombres, semillas, huella, payloads y acks."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal

import pytest

from app import fabrica_plan as fp
from app.optimizer import goals as g
from app.optimizer import hygiene

FECHA = dt.date(2026, 9, 5)


def _producto(pid=1, margen="38.20", sku="SS-1", asin="B0AAAAAAAA"):
    return fp.ProductoGrupo(
        product_id=pid, odoo_sku=f"ODOO-{pid}", listing_id=10 + pid, asin=asin,
        seller_sku=sku, margen_neto_pct=Decimal(margen),
    )


def _parametros(bid="5.00", budget="120"):
    return {
        rol: fp.ParametrosRol(rol=rol, budget=Decimal(budget), bid=Decimal(bid))
        for rol in fp.ROLES_ORDEN_CREACION
    }


def _plan(**cambios):
    base = dict(
        platform="amazon_mx", tipo_producto="collar_perro", nombre_base="Collar reflectante",
        fecha=FECHA, moneda="MXN", modo="shadow", productos=(_producto(),),
        parametros=_parametros(), target=fp.target_del_grupo([Decimal("38.20")], Decimal("0.5")),
        semillas=fp.Semillas(keywords=("collar perro",), asins=("B0BBBBBBBB",), negativos=("gato",), exact=()),
    )
    base.update(cambios)
    return fp.PlanGrupo(**base)


# --- target ----------------------------------------------------------------


def test_target_es_fraccion_por_margen_minimo_con_procedencia():
    r = fp.target_del_grupo([Decimal("38.20"), Decimal("52.00")], Decimal("0.5"))
    assert r.minimo == Decimal("38.20")
    assert r.derivado == Decimal("19.100")  # crudo, NUMERIC(10,4)
    assert r.aplicado == Decimal("19.10") and r.aplicado.as_tuple().exponent == -2
    assert "38.20" in r.procedencia and "0.5" in r.procedencia and "clamp" not in r.procedencia


def test_target_aplicado_cuantizado_a_numeric_6_2_una_sola_vez():
    """0.5 x 38.21 = 19.105 -> 19.10 (HALF_EVEN a 2 decimales, la precision de
    NUMERIC(6,2) de campana_grupo.target_acos_pct y ads_optimizer_goal): la
    huella, el JSON, la DB y el goal ven el MISMO numero; el derivado crudo
    queda y la procedencia declara el redondeo."""
    r = fp.target_del_grupo([Decimal("38.21")], Decimal("0.5"))
    assert r.derivado == Decimal("19.105")
    assert r.aplicado == Decimal("19.10") and str(r.aplicado) == "19.10"
    assert "clamp" not in r.procedencia and "redondeo NUMERIC(6,2)" in r.procedencia


def test_target_clampea_a_la_banda_del_motor_y_lo_declara():
    bajo = fp.target_del_grupo([Decimal("12")], Decimal("0.5"))  # 6 -> 10
    alto = fp.target_del_grupo([Decimal("100")], Decimal("1"))  # 100 -> 45
    assert bajo.aplicado == g.MARGEN_BANDA_MIN and bajo.derivado == Decimal("6.0")
    assert alto.aplicado == g.MARGEN_BANDA_MAX
    assert "clamp" in bajo.procedencia and "clamp" in alto.procedencia


def test_target_sin_fraccion_o_sin_margenes_aborta():
    with pytest.raises(fp.PlanInvalido, match="fraccion"):
        fp.target_del_grupo([Decimal("30")], None)
    with pytest.raises(ValueError):  # invalida = config corrupta (misma ley que el motor)
        fp.target_del_grupo([Decimal("30")], Decimal("1.5"))
    with pytest.raises(fp.PlanInvalido, match="margen"):
        fp.target_del_grupo([], Decimal("0.5"))
    with pytest.raises(fp.PlanInvalido, match="margen"):
        fp.target_del_grupo([Decimal("30"), None], Decimal("0.5"))


# --- montos ---------------------------------------------------------------


def test_valida_parametros_usa_piso_y_techo_de_la_moneda():
    fp.valida_parametros(_parametros(bid="45.00", budget="150"), "MXN")  # techo MXN inclusivo
    with pytest.raises(fp.PlanInvalido, match="bid"):
        fp.valida_parametros(_parametros(bid="45.01"), "MXN")
    with pytest.raises(fp.PlanInvalido, match="bid"):
        fp.valida_parametros(_parametros(bid="0.50"), "MXN")  # bajo el piso 1.00
    fp.valida_parametros(_parametros(bid="2.50", budget="10"), "USD")
    with pytest.raises(fp.PlanInvalido, match="budget"):
        fp.valida_parametros(_parametros(bid="2.00", budget="1.50"), "USD")  # budget < bid
    with pytest.raises(fp.PlanInvalido, match="moneda"):
        fp.valida_parametros(_parametros(), "EUR")
    faltante = _parametros()
    del faltante["auto_discovery"]
    with pytest.raises(fp.PlanInvalido, match="auto_discovery"):
        fp.valida_parametros(faltante, "MXN")


def test_moneda_por_plataforma_iguala_la_capa_http():
    from app.ads.write import PLATAFORMA_MONEDA

    assert dict(PLATAFORMA_MONEDA) == fp.MONEDA_POR_PLATAFORMA


def test_monto_wire_cuantiza_a_dos_decimales_como_el_write_client():
    assert fp.monto_wire(Decimal("4.5")) == 4.5
    assert fp.monto_wire(Decimal("4.005")) == 4.0  # HALF_EVEN
    with pytest.raises(TypeError):
        fp.monto_wire(4.5)


# --- nombres y tipo_producto ---------------------------------------------


def test_tipo_producto_es_etiqueta_ascii_minuscula():
    assert fp.valida_tipo_producto("collar_perro") == "collar_perro"
    for malo in ("Collar", "collar perro", "", "collar-perro", "ñu"):
        with pytest.raises(fp.PlanInvalido):
            fp.valida_tipo_producto(malo)


def test_nombres_llevan_tipo_base_rol_y_fecha():
    assert (
        fp.nombre_campana("collar_perro", "Collar reflectante", "category_exact", FECHA)
        == "collar_perro | Collar reflectante | category_exact | 2026-09-05"
    )
    assert fp.nombre_ad_group("collar_perro", "Collar reflectante", "category_exact", FECHA).endswith(
        "| ag"
    )


# --- semillas -------------------------------------------------------------


def _termino(texto, orders=1, cost="10", revenue="100", asin_like=False):
    return fp.TerminoProducto(
        texto=texto, is_asin_like=asin_like, orders=orders,
        cost=Decimal(cost), revenue=Decimal(revenue),
    )


def test_semillas_reparten_biblioteca_y_terminos_por_rol():
    s = fp.semillas_desde_terminos(
        terminos=[
            _termino("collar perro", orders=1),
            _termino("b0cccccccc", orders=2, asin_like=True),
            _termino("collar led", orders=3, cost="20", revenue="100"),  # acos 20 <= min(35, 19.1)? no
            _termino("collar noche", orders=2, cost="10", revenue="100"),  # acos 10: exact
            _termino("sin ventas", orders=0),
        ],
        biblioteca_keywords=["collar reflectante", "B0DDDDDDDD", "collar perro"],
        biblioteca_negativos=["gato", "gato"],
        target_acos_pct=Decimal("19.10"),
    )
    assert s.keywords == ("collar led", "collar noche", "collar perro", "collar reflectante")
    assert s.asins == ("B0CCCCCCCC", "B0DDDDDDDD")
    assert s.negativos == ("gato",)
    assert s.exact == ("collar noche",)


def test_semilla_exact_usa_el_criterio_harvest_del_motor():
    """orders >= HARVEST_ORDERS_MIN y cost*100 <= min(35, target)*revenue
    (comparacion cruzada SIN dividir, hygiene.py camino (6)): mismas
    constantes del motor, no copias."""
    t = _termino("x", orders=hygiene.HARVEST_ORDERS_MIN, cost="35", revenue="100")
    assert fp.semillas_desde_terminos([t], [], [], Decimal("40")).exact == ("x",)
    assert fp.semillas_desde_terminos([t], [], [], Decimal("30")).exact == ()
    t2 = _termino("y", orders=hygiene.HARVEST_ORDERS_MIN - 1, cost="1", revenue="100")
    assert fp.semillas_desde_terminos([t2], [], [], Decimal("40")).exact == ()
    t3 = _termino("z", orders=5, cost="5", revenue="0")  # revenue 0: ACoS no evaluable
    assert fp.semillas_desde_terminos([t3], [], [], Decimal("40")).exact == ()


def test_semilla_exact_se_evalua_sobre_la_ventana_de_cortes():
    """Spec §6 (ventana y madurez ya selladas, regla 6): la excepcion exact se
    evalua sobre `terminos_exact` (ventana de CORTES del motor), no sobre la
    ventana [D-105, D-15) del margen; keywords siguen saliendo de `terminos`."""
    t_margen = _termino("collar noche", orders=1, cost="90", revenue="100")
    t_cortes = _termino(
        "collar noche", orders=hygiene.HARVEST_ORDERS_MIN, cost="10", revenue="100"
    )
    s = fp.semillas_desde_terminos(
        [t_margen], [], [], Decimal("19.10"), terminos_exact=[t_cortes]
    )
    assert s.exact == ("collar noche",)  # cumple harvest en la ventana de cortes
    assert s.keywords == ("collar noche",)  # la keyword sale de la ventana del margen


def test_ventana_cortes_es_la_del_motor():
    """Regla 2 (un numero, una fuente): la ventana de los candidatos a exact
    ES la ventana de cortes del motor: 30 dias inclusive (DIAS_VENTANA) que
    terminan a mas tardar hoy - 10d (DIAS_MADUREZ_CORTES, regla 6)."""
    from app.optimizer import windows

    desde, hasta = fp.VENTANA_CORTES_DIAS
    assert hasta == windows.DIAS_MADUREZ_CORTES - 1  # "< hoy-9" == "<= hoy-10"
    assert desde - hasta == windows.DIAS_VENTANA  # [D-39, D-10] = 30 fechas


# --- huella y json --------------------------------------------------------


def test_huella_cambia_con_bids_productos_o_semillas():
    base = _plan()
    assert fp.huella_plan(base) == fp.huella_plan(_plan())
    assert fp.huella_plan(base) != fp.huella_plan(_plan(parametros=_parametros(bid="5.50")))
    assert fp.huella_plan(base) != fp.huella_plan(_plan(productos=(_producto(), _producto(2))))
    assert fp.huella_plan(base) != fp.huella_plan(_plan(modo="live"))
    canonico = json.dumps(fp.plan_como_json(base), sort_keys=True, separators=(",", ":"))
    assert fp.huella_plan(base) == hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def test_plan_como_json_lleva_dinero_como_string():
    j = fp.plan_como_json(_plan())
    assert j["target_acos_pct"] == "19.10"
    assert j["parametros"]["category_exact"]["bid"] == "5.00"
    assert j["productos"][0]["margen_neto_pct"] == "38.20"


def test_plan_json_ida_y_vuelta():
    """fabrica_lote.plan -> PlanGrupo -> misma huella (lo que --registrar
    reconstruye es EXACTAMENTE lo autorizado)."""
    plan = _plan()
    assert fp.huella_plan(fp.plan_desde_json(fp.plan_como_json(plan))) == fp.huella_plan(plan)


# --- pasos y payloads -----------------------------------------------------


def test_pasos_de_la_exact_son_campana_ad_group_product_ads_y_semillas_exact():
    plan = _plan(semillas=fp.Semillas(("kw",), ("B0X",), ("neg",), ("hv",)))
    pasos = fp.pasos_del_rol(plan, "category_exact")
    assert [p.recurso for p in pasos] == ["campaign", "ad_group", "product_ad", "keyword"]
    assert pasos[0].path == "/sp/campaigns" and pasos[0].payload["targetingType"] == "MANUAL"
    assert pasos[0].payload["budget"] == {"budgetType": "DAILY", "budget": 120.0}
    assert pasos[0].payload["state"] == "ENABLED" and pasos[0].payload["startDate"] == "2026-09-05"
    assert pasos[1].payload["defaultBid"] == 5.0
    assert pasos[2].payload["sku"] == "SS-1" and pasos[2].payload["state"] == "ENABLED"
    assert pasos[3].payload == {"keywordText": "hv", "matchType": "EXACT", "state": "ENABLED", "bid": 5.0}


def test_pasos_por_rol_semillas_correctas():
    plan = _plan(semillas=fp.Semillas(("kw",), ("B0X",), ("neg",), ("hv",)))
    phrase = fp.pasos_del_rol(plan, "category_phrase")
    assert phrase[-1].payload["matchType"] == "PHRASE" and phrase[-1].payload["keywordText"] == "kw"
    broad = fp.pasos_del_rol(plan, "category_broad")
    assert broad[-1].payload["matchType"] == "BROAD"
    prod = fp.pasos_del_rol(plan, "product_targeting")
    assert prod[-1].path == "/sp/targets"
    assert prod[-1].payload["expression"] == [{"type": "ASIN_SAME_AS", "value": "B0X"}]
    assert prod[-1].payload["expressionType"] == "MANUAL"
    auto = fp.pasos_del_rol(plan, "auto_discovery")
    assert auto[0].payload["targetingType"] == "AUTO"
    assert auto[-1].path == "/sp/negativeKeywords"
    assert auto[-1].payload["matchType"] == "NEGATIVE_EXACT"
    assert "bid" not in auto[-1].payload


def test_pasos_sin_semillas_son_solo_estructura():
    plan = _plan(semillas=fp.Semillas((), (), (), ()))
    assert [p.recurso for p in fp.pasos_del_rol(plan, "category_phrase")] == [
        "campaign", "ad_group", "product_ad",
    ]


def test_vendors_y_envolturas_por_path():
    assert fp.VENDOR_POR_PATH["/sp/campaigns"] == "application/vnd.spcampaign.v3+json"
    assert fp.VENDOR_POR_PATH["/sp/targets"] == "application/vnd.sptargetingclause.v3+json"
    assert fp.ENVOLTURA_POR_PATH["/sp/targets"] == "targetingClauses"
    assert fp.CLAVE_ID_POR_PATH["/sp/productAds"] == "adId"
    assert fp.LIST_POR_PATH["/sp/adGroups"] == "/sp/adGroups/list"
    assert fp.FILTRO_ID_POR_LIST["/sp/negativeKeywords/list"] == "negativeKeywordIdFilter"
    assert set(fp.PATH_CREATE.values()) == set(fp.VENDOR_POR_PATH) == set(fp.ENVOLTURA_POR_PATH)


# --- acks -----------------------------------------------------------------


def test_id_creado_lee_success_anidado_o_plano():
    plano = {"status": 207, "cuerpo": {"campaigns": {"success": [{"index": 0, "campaignId": "c1"}]}}}
    anidado = {"status": 207, "cuerpo": {"keywords": {"success": [{"keyword": {"keywordId": 9}}]}}}
    assert fp.id_creado(plano, "campaignId") == "c1"
    assert fp.id_creado(anidado, "keywordId") == "9"
    assert fp.id_creado({"status": 207, "cuerpo": {"keywords": {"success": []}}}, "keywordId") is None
    assert fp.id_creado({"status": 500, "cuerpo": {}}, "keywordId") is None


def test_ack_ok_exige_2xx_sin_errores_207():
    assert fp.ack_ok({"status": 207, "cuerpo": {"campaigns": {"success": [{}], "error": []}}})
    assert not fp.ack_ok({"status": 207, "cuerpo": {"campaigns": {"error": [{"index": 0}]}}})
    assert not fp.ack_ok({"status": 400, "cuerpo": {}})
    assert fp.errores_207({"status": 207, "cuerpo": "no json"}) == [{"no_json": None}]


def test_lineas_dry_run_declaran_semillas_cero():
    lineas = fp.lineas_dry_run(_plan(semillas=fp.Semillas((), (), (), ())))
    assert any("category_phrase" in linea and "semillas=0" in linea for linea in lineas)
    assert any("target=19.10" in linea for linea in lineas)
```

- [ ] **Step 2: Rojo**

Run: `pytest tests/test_fabrica_plan.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.fabrica_plan'`.

- [ ] **Step 3: Implementar el módulo**

```python
# app/fabrica_plan.py
"""Nucleo PURO de la fabrica de campanas por grupo (FABRICA 01, spec
docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md).

Sin psycopg ni httpx: recibe lo leido de la base, devuelve el plan, sus
payloads y su huella. tools/fabrica_campanas.py (que entra por stdin al
contenedor y debe ser UN archivo) hace todo el IO alrededor de esto.

Sellos que se REUSAN, jamas se copian (regla 2): banda [10, 45] y defaults
de bid por moneda de app.optimizer.goals; criterio HARVEST de
app.optimizer.hygiene; moneda por plataforma pineada contra
app.ads.write.PLATAFORMA_MONEDA por test.

HIPOTESIS hasta la sonda (tarea 11 del plan): los shapes de POST
/sp/campaigns, /sp/adGroups y /sp/targets nunca se ejercitaron en vivo en
este repo; el camino feliz de /sp/productAds tampoco. Los de keywords y
negativeKeywords si (probe 2.5, archiva_inertes). Cualquier campo que la
sonda corrija se sella aqui con su evidencia en out/.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from app.optimizer import goals as g
from app.optimizer import hygiene

# Orden FIJO de creacion (spec §5.3): la exact primero — si el lote muere a
# medias, jamas queda una discovery sin destino de harvest.
ROLES_ORDEN_CREACION = (
    "category_exact",
    "category_phrase",
    "category_broad",
    "product_targeting",
    "auto_discovery",
)
MATCH_POR_ROL = {"category_exact": "EXACT", "category_phrase": "PHRASE", "category_broad": "BROAD"}
TARGETING_POR_ROL = {rol: "MANUAL" for rol in ROLES_ORDEN_CREACION}
TARGETING_POR_ROL["auto_discovery"] = "AUTO"

# Mismo mapa que app/ads/write.py::PLATAFORMA_MONEDA (test lo pinea).
MONEDA_POR_PLATAFORMA = {"amazon_mx": "MXN", "amazon_us": "USD"}
MODOS_GOAL = ("shadow", "live")
ESTADO_NUEVO = "ENABLED"  # decision 8: nacen ENABLED
VENTANA_DIAS = (105, 15)  # [D-105, D-15), la misma de v_target_margen_plataforma
# Candidatos a semilla EXACT: la ventana de CORTES del motor (spec §6: "mismo
# criterio HARVEST... ventana y madurez ya selladas"), NO la del margen.
# [D-39, D-10] inclusive = 30 dias (DIAS_VENTANA) con madurez >= 10d
# (DIAS_MADUREZ_CORTES, regla 6) de app/optimizer/windows.py; en el SQL:
# metric_date >= CURRENT_DATE - 39 AND metric_date < CURRENT_DATE - 9.
VENTANA_CORTES_DIAS = (39, 9)  # pineada contra windows.py por test (regla 2)
MARGEN_DIAS_MIN_PRODUCTO = 60  # pineado contra el SQL de v_margen_producto por test

PATRON_TIPO_PRODUCTO = re.compile(r"^[a-z0-9_]+$")
PATRON_ASIN = re.compile(r"^b0[a-z0-9]{8}$", re.IGNORECASE)

PATH_CREATE = {
    "campaign": "/sp/campaigns",
    "ad_group": "/sp/adGroups",
    "product_ad": "/sp/productAds",
    "keyword": "/sp/keywords",
    "target": "/sp/targets",
    "negative_keyword": "/sp/negativeKeywords",
}
VENDOR_POR_PATH = {
    "/sp/campaigns": "application/vnd.spcampaign.v3+json",
    "/sp/adGroups": "application/vnd.spadgroup.v3+json",
    "/sp/productAds": "application/vnd.spproductad.v3+json",
    "/sp/keywords": "application/vnd.spkeyword.v3+json",
    "/sp/targets": "application/vnd.sptargetingclause.v3+json",
    "/sp/negativeKeywords": "application/vnd.spnegativekeyword.v3+json",
}
ENVOLTURA_POR_PATH = {
    "/sp/campaigns": "campaigns",
    "/sp/adGroups": "adGroups",
    "/sp/productAds": "productAds",
    "/sp/keywords": "keywords",
    "/sp/targets": "targetingClauses",
    "/sp/negativeKeywords": "negativeKeywords",
}
CLAVE_ID_POR_PATH = {
    "/sp/campaigns": "campaignId",
    "/sp/adGroups": "adGroupId",
    "/sp/productAds": "adId",
    "/sp/keywords": "keywordId",
    "/sp/targets": "targetId",
    "/sp/negativeKeywords": "keywordId",
}
LIST_POR_PATH = {path: f"{path}/list" for path in VENDOR_POR_PATH}
FILTRO_ID_POR_LIST = {
    "/sp/campaigns/list": "campaignIdFilter",
    "/sp/adGroups/list": "adGroupIdFilter",
    "/sp/productAds/list": "adIdFilter",
    "/sp/keywords/list": "keywordIdFilter",
    "/sp/targets/list": "targetIdFilter",
    "/sp/negativeKeywords/list": "negativeKeywordIdFilter",
}
CONTENEDOR_POR_LIST = {f"{path}/list": clave for path, clave in ENVOLTURA_POR_PATH.items()}


class PlanInvalido(ValueError):
    """Fail-closed del plan: falta un dato o un monto esta fuera de ley."""


@dataclass(frozen=True)
class ProductoGrupo:
    product_id: int
    odoo_sku: str
    listing_id: int
    asin: str
    seller_sku: str
    margen_neto_pct: Decimal


@dataclass(frozen=True)
class ParametrosRol:
    rol: str
    budget: Decimal
    bid: Decimal


@dataclass(frozen=True)
class TerminoProducto:
    texto: str
    is_asin_like: bool
    orders: int | None
    cost: Decimal | None
    revenue: Decimal | None


@dataclass(frozen=True)
class Semillas:
    keywords: tuple[str, ...]
    asins: tuple[str, ...]
    negativos: tuple[str, ...]
    exact: tuple[str, ...]


@dataclass(frozen=True)
class ResultadoTarget:
    aplicado: Decimal
    derivado: Decimal
    minimo: Decimal
    fraccion: Decimal
    procedencia: str


@dataclass(frozen=True)
class PlanGrupo:
    platform: str
    tipo_producto: str
    nombre_base: str
    fecha: Any  # datetime.date
    moneda: str
    modo: str
    productos: tuple[ProductoGrupo, ...]
    parametros: dict[str, ParametrosRol]
    target: ResultadoTarget
    semillas: Semillas
    existentes: tuple[dict, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Paso:
    """Un POST del lote: recurso + path + payload SIN los ids del padre
    (campaignId/adGroupId los completa el tool con los externos creados)."""

    rol: str
    recurso: str
    path: str
    payload: dict
    descripcion: str


# ---------------------------------------------------------------------------
# Validaciones puras
# ---------------------------------------------------------------------------


def valida_tipo_producto(tipo: str) -> str:
    if not isinstance(tipo, str) or not PATRON_TIPO_PRODUCTO.match(tipo):
        raise PlanInvalido(
            f"tipo_producto {tipo!r} invalido: etiqueta ascii minuscula [a-z0-9_]+ (decision 7)"
        )
    return tipo


def target_del_grupo(margenes: list[Decimal | None], fraccion: Decimal | None) -> ResultadoTarget:
    """target = clamp(fraccion x min(margenes), [MARGEN_BANDA_MIN, MARGEN_BANDA_MAX]).
    fraccion None = interruptor apagado -> PlanInvalido; invalida -> ValueError
    (config corrupta, misma ley que el motor: g._valida_fraccion). El aplicado
    se cuantiza a NUMERIC(6,2) UNA sola vez aqui (0.5 x 38.21 = 19.105 ->
    19.10 por HALF_EVEN): huella, JSON, DB y goal ven el MISMO numero; el
    derivado queda crudo (NUMERIC(10,4)) para la procedencia."""
    if fraccion is None:
        raise PlanInvalido("sin fraccion: setting ads_target_fraccion_margen_<platform> ausente")
    fraccion = g._valida_fraccion(fraccion)
    if not margenes or any(m is None for m in margenes):
        raise PlanInvalido("todo producto del grupo necesita margen medible (regla 3)")
    minimo = min(margenes)
    derivado = fraccion * minimo
    clampeado = min(max(derivado, g.MARGEN_BANDA_MIN), g.MARGEN_BANDA_MAX)
    aplicado = clampeado.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    procedencia = f"margen_minimo_grupo: {fraccion} x {minimo} = {derivado}"
    if clampeado != derivado:
        procedencia += f" -> clamp [{g.MARGEN_BANDA_MIN}, {g.MARGEN_BANDA_MAX}] = {clampeado}"
    if aplicado != clampeado:
        procedencia += f" -> redondeo NUMERIC(6,2) = {aplicado}"
    return ResultadoTarget(aplicado, derivado, minimo, fraccion, procedencia)


def valida_parametros(parametros: dict[str, ParametrosRol], moneda: str) -> None:
    """Bids dentro de [piso, techo] de SU moneda (DEFAULTS_POR_MONEDA, regla 4)
    y budgets > 0 y >= bid; los 5 roles presentes."""
    try:
        piso, techo = g.DEFAULTS_POR_MONEDA[moneda]
    except KeyError:
        raise PlanInvalido(f"moneda {moneda!r} sin piso/techo en DEFAULTS_POR_MONEDA") from None
    for rol in ROLES_ORDEN_CREACION:
        p = parametros.get(rol)
        if p is None:
            raise PlanInvalido(f"faltan --bid/--budget de {rol}")
        for nombre, valor in (("bid", p.bid), ("budget", p.budget)):
            if not isinstance(valor, Decimal) or not valor.is_finite() or valor <= 0:
                raise PlanInvalido(f"{nombre} de {rol} debe ser Decimal finito > 0: {valor!r}")
        if not piso <= p.bid <= techo:
            raise PlanInvalido(f"bid de {rol} = {p.bid} fuera de [{piso}, {techo}] {moneda}")
        if p.budget < p.bid:
            raise PlanInvalido(f"budget de {rol} = {p.budget} < bid {p.bid}: no compra ni un clic")


def monto_wire(monto: Decimal) -> float:
    """Encoding final del wire (2 decimales, HALF_EVEN), mismo criterio que
    _bid_wire del write client: float SOLO aqui, jamas para guardar o decidir."""
    if not isinstance(monto, Decimal):
        raise TypeError(f"monto debe ser Decimal, no {type(monto).__name__}")
    return float(monto.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


# ---------------------------------------------------------------------------
# Nombres (la API los exige; el vinculo es campana_grupo_rol, no el nombre)
# ---------------------------------------------------------------------------


def nombre_campana(tipo: str, base: str, rol: str, fecha) -> str:
    return f"{tipo} | {base} | {rol} | {fecha.isoformat()}"


def nombre_ad_group(tipo: str, base: str, rol: str, fecha) -> str:
    return f"{nombre_campana(tipo, base, rol, fecha)} | ag"


# ---------------------------------------------------------------------------
# Semillas (spec §6)
# ---------------------------------------------------------------------------


def _cumple_harvest(t: TerminoProducto, target: Decimal) -> bool:
    """Criterio HARVEST del motor (hygiene.py camino (6), SIN dividir):
    orders >= HARVEST_ORDERS_MIN y cost * 100 <= min(tope_fijo, target) *
    revenue (el motor salta cuando cost * _CIEN > tope * ad_revenue).
    orders/cost/revenue None o revenue <= 0 -> no (regla 3).
    Divergencia declarada y conservadora (r2 glm 5): el motor harvestea
    "ventas gratis" (cost=0, revenue=0 pasa su cruce); la fabrica NO siembra
    exact con revenue <= 0 — sembrar menos, jamas de mas."""
    if t.orders is None or t.orders < hygiene.HARVEST_ORDERS_MIN:
        return False
    if t.cost is None or t.revenue is None or t.revenue <= 0:
        return False
    tope = min(hygiene.HARVEST_ACOS_TOPE_FIJO_PCT, target)
    return t.cost * 100 <= tope * t.revenue


def semillas_desde_terminos(
    terminos: list[TerminoProducto],
    biblioteca_keywords: list[str],
    biblioteca_negativos: list[str],
    target_acos_pct: Decimal,
    *,
    terminos_exact: list[TerminoProducto] | None = None,
) -> Semillas:
    """phrase/broad = biblioteca + terminos con orders >= 1 (sin ASIN-like);
    product targeting = ASIN-like de terminos + entradas ASIN de biblioteca;
    exact = terminos_exact que YA cumplen HARVEST; auto = negativos de
    biblioteca. `terminos_exact` es el MISMO agregado pero sobre la ventana
    de CORTES del motor (VENTANA_CORTES_DIAS, regla 6: madurez >= 10d); None
    = evaluar exact sobre `terminos` (compat hacia atras en tests del nucleo).
    Todo normalizado (strip, keywords en minusculas, ASIN en mayusculas),
    deduplicado y ordenado (determinismo para la huella)."""
    keywords: set[str] = set()
    asins: set[str] = set()
    exact: set[str] = set()
    for texto in biblioteca_keywords:
        limpio = texto.strip()
        if PATRON_ASIN.match(limpio):
            asins.add(limpio.upper())
        elif limpio:
            keywords.add(limpio.lower())
    for t in terminos:
        limpio = t.texto.strip()
        if not limpio or not t.orders or t.orders < 1:
            continue
        if t.is_asin_like or PATRON_ASIN.match(limpio):
            asins.add(limpio.upper())
            continue
        keywords.add(limpio.lower())
    for t in terminos_exact if terminos_exact is not None else terminos:
        limpio = t.texto.strip()
        if not limpio or t.is_asin_like or PATRON_ASIN.match(limpio):
            continue
        if _cumple_harvest(t, target_acos_pct):
            exact.add(limpio.lower())
    negativos = sorted({n.strip().lower() for n in biblioteca_negativos if n.strip()})
    return Semillas(tuple(sorted(keywords)), tuple(sorted(asins)), tuple(negativos), tuple(sorted(exact)))


# ---------------------------------------------------------------------------
# Huella y JSON del plan (dinero como STRING, regla 4)
# ---------------------------------------------------------------------------


def plan_como_json(plan: PlanGrupo) -> dict:
    return {
        "platform": plan.platform,
        "tipo_producto": plan.tipo_producto,
        "nombre_base": plan.nombre_base,
        "fecha": plan.fecha.isoformat(),
        "moneda": plan.moneda,
        "modo": plan.modo,
        "productos": [
            {
                "product_id": p.product_id,
                "odoo_sku": p.odoo_sku,
                "listing_id": p.listing_id,
                "asin": p.asin,
                "seller_sku": p.seller_sku,
                "margen_neto_pct": str(p.margen_neto_pct),
            }
            for p in plan.productos
        ],
        "parametros": {
            rol: {"budget": str(p.budget), "bid": str(p.bid)} for rol, p in sorted(plan.parametros.items())
        },
        "target_acos_pct": str(plan.target.aplicado),
        "target_derivado_pct": str(plan.target.derivado),
        "fraccion": str(plan.target.fraccion),
        "target_procedencia": plan.target.procedencia,
        "semillas": {
            "keywords": list(plan.semillas.keywords),
            "asins": list(plan.semillas.asins),
            "negativos": list(plan.semillas.negativos),
            "exact": list(plan.semillas.exact),
        },
    }


def huella_plan(plan: PlanGrupo) -> str:
    """sha256 del JSON canonico del plan: cambia si cambia CUALQUIER cosa que
    se va a crear (productos, bids, budgets, semillas, target, modo)."""
    canonico = json.dumps(plan_como_json(plan), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def plan_desde_json(d: dict) -> PlanGrupo:
    """Inverso de plan_como_json (fabrica_lote.plan -> PlanGrupo) para el
    reintento del registro (--registrar): dinero vuelve a Decimal, fecha a
    date. `existentes` no se congela (solo informa)."""
    productos = tuple(
        ProductoGrupo(p["product_id"], p["odoo_sku"], p["listing_id"], p["asin"],
                      p["seller_sku"], Decimal(p["margen_neto_pct"]))
        for p in d["productos"]
    )
    parametros = {
        rol: ParametrosRol(rol, Decimal(v["budget"]), Decimal(v["bid"]))
        for rol, v in d["parametros"].items()
    }
    target = ResultadoTarget(
        Decimal(d["target_acos_pct"]), Decimal(d["target_derivado_pct"]),
        min(p.margen_neto_pct for p in productos), Decimal(d["fraccion"]), d["target_procedencia"],
    )
    s = d["semillas"]
    return PlanGrupo(
        d["platform"], d["tipo_producto"], d["nombre_base"], dt.date.fromisoformat(d["fecha"]),
        d["moneda"], d["modo"], productos, parametros, target,
        Semillas(tuple(s["keywords"]), tuple(s["asins"]), tuple(s["negativos"]), tuple(s["exact"])),
    )


# ---------------------------------------------------------------------------
# Pasos por rol (payloads SIN ids del padre)
# ---------------------------------------------------------------------------


def _payload_campana(plan: PlanGrupo, rol: str) -> dict:
    p = plan.parametros[rol]
    return {  # HIPOTESIS hasta la sonda: startDate ISO, budget anidado, dynamicBidding
        "name": nombre_campana(plan.tipo_producto, plan.nombre_base, rol, plan.fecha),
        "targetingType": TARGETING_POR_ROL[rol],
        "state": ESTADO_NUEVO,
        "budget": {"budgetType": "DAILY", "budget": monto_wire(p.budget)},
        "startDate": plan.fecha.isoformat(),
        "dynamicBidding": {"strategy": "LEGACY_FOR_SALES"},
    }


def _payload_ad_group(plan: PlanGrupo, rol: str) -> dict:
    return {  # HIPOTESIS hasta la sonda: defaultBid numero
        "name": nombre_ad_group(plan.tipo_producto, plan.nombre_base, rol, plan.fecha),
        "state": ESTADO_NUEVO,
        "defaultBid": monto_wire(plan.parametros[rol].bid),
    }


def _semillas_del_rol(plan: PlanGrupo, rol: str) -> list[Paso]:
    bid = monto_wire(plan.parametros[rol].bid)
    s = plan.semillas
    if rol in MATCH_POR_ROL:
        textos = s.exact if rol == "category_exact" else s.keywords
        return [
            Paso(rol, "keyword", PATH_CREATE["keyword"],
                 {"keywordText": kw, "matchType": MATCH_POR_ROL[rol], "state": ESTADO_NUEVO, "bid": bid},
                 f"keyword {MATCH_POR_ROL[rol]} {kw!r}")
            for kw in textos
        ]
    if rol == "product_targeting":
        return [
            Paso(rol, "target", PATH_CREATE["target"],
                 {  # HIPOTESIS hasta la sonda: expressionType MANUAL + ASIN_SAME_AS
                     "expressionType": "MANUAL",
                     "expression": [{"type": "ASIN_SAME_AS", "value": asin}],
                     "state": ESTADO_NUEVO,
                     "bid": bid,
                 },
                 f"target ASIN {asin}")
            for asin in s.asins
        ]
    return [  # auto_discovery: negativos (shape sellado por el probe 2.5)
        Paso(rol, "negative_keyword", PATH_CREATE["negative_keyword"],
             {"keywordText": neg, "matchType": "NEGATIVE_EXACT", "state": ESTADO_NUEVO},
             f"negative EXACT {neg!r}")
        for neg in s.negativos
    ]


def pasos_del_rol(plan: PlanGrupo, rol: str) -> list[Paso]:
    """campana -> ad group -> un product ad por producto -> semillas del rol."""
    pasos = [
        Paso(rol, "campaign", PATH_CREATE["campaign"], _payload_campana(plan, rol), f"campana {rol}"),
        Paso(rol, "ad_group", PATH_CREATE["ad_group"], _payload_ad_group(plan, rol), f"ad group {rol}"),
    ]
    pasos.extend(
        Paso(rol, "product_ad", PATH_CREATE["product_ad"],
             {"sku": p.seller_sku, "state": ESTADO_NUEVO}, f"product ad sku {p.seller_sku}")
        for p in plan.productos
    )
    pasos.extend(_semillas_del_rol(plan, rol))
    return pasos


# ---------------------------------------------------------------------------
# Acks 207 (mismo criterio que el aplicador y archiva_inertes)
# ---------------------------------------------------------------------------


def errores_207(ack: dict) -> list:
    cuerpo = ack.get("cuerpo")
    if not isinstance(cuerpo, dict):
        return [{"no_json": ack.get("texto")}]
    errores: list = []
    for valor in cuerpo.values():
        if isinstance(valor, dict):
            errores.extend(valor.get("error") or [])
    return errores


def ack_ok(ack: dict) -> bool:
    return ack.get("status") in (200, 207) and not errores_207(ack)


def id_creado(ack: dict, clave: str) -> str | None:
    """El id del objeto creado segun el ack (success plano o anidado por
    recurso). None = sin id legible (regla 3: jamas inventado)."""
    cuerpo = ack.get("cuerpo")
    if not isinstance(cuerpo, dict):
        return None
    for valor in cuerpo.values():
        if not isinstance(valor, dict) or not isinstance(valor.get("success"), list):
            continue
        for item in valor["success"]:
            if not isinstance(item, dict):
                continue
            if item.get(clave) is not None:
                return str(item[clave])
            for sub in item.values():
                if isinstance(sub, dict) and sub.get(clave) is not None:
                    return str(sub[clave])
    return None


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def lineas_dry_run(plan: PlanGrupo) -> list[str]:
    s = plan.semillas
    conteo = {
        "category_exact": len(s.exact),
        "category_phrase": len(s.keywords),
        "category_broad": len(s.keywords),
        "product_targeting": len(s.asins),
        "auto_discovery": len(s.negativos),
    }
    lineas = [
        f"grupo {plan.platform} | tipo={plan.tipo_producto} | base={plan.nombre_base!r} | "
        f"target={plan.target.aplicado} ({plan.target.procedencia}) | modo_goal={plan.modo} | "
        f"moneda={plan.moneda}",
    ]
    lineas.extend(
        f"producto {p.product_id} {p.odoo_sku} | asin={p.asin} | sku={p.seller_sku} | "
        f"margen={p.margen_neto_pct}"
        for p in plan.productos
    )
    for rol in ROLES_ORDEN_CREACION:
        p = plan.parametros[rol]
        lineas.append(
            f"{rol} | {nombre_campana(plan.tipo_producto, plan.nombre_base, rol, plan.fecha)} | "
            f"targeting={TARGETING_POR_ROL[rol]} | budget={p.budget} | bid={p.bid} | "
            f"product_ads={len(plan.productos)} | semillas={conteo[rol]}"
        )
    for e in plan.existentes:
        lineas.append(
            f"existente (solo se informa, decision 9): {e.get('external_id')} | "
            f"{e.get('name')} | status={e.get('status')}"
        )
    return lineas
```

- [ ] **Step 4: Verde + ruff**

Run: `pytest tests/test_fabrica_plan.py -v && ruff check app/fabrica_plan.py && ruff format --check app/fabrica_plan.py`
Expected: PASS; sin hallazgos de ruff (si `_semillas_del_rol` dispara C901, partir el `if` de keywords en `_pasos_keywords(plan, rol)`; jamás `noqa` sin razón).

- [ ] **Step 5: Agregar los tests que pinean las constantes contra el SQL de la vista**

```python
# agregar a tests/test_fabrica_plan.py
def test_dias_minimos_por_producto_pineados_contra_el_sql():
    """Regla 2 (un numero, una fuente): la constante del nucleo y el guard
    `dias_con_venta < N` de v_margen_producto son el MISMO numero."""
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "migrations" / "0018_fabrica_campanas.sql").read_text(
        encoding="utf-8"
    )
    assert f"a.dias_con_venta < {fp.MARGEN_DIAS_MIN_PRODUCTO} THEN NULL" in sql


def test_cobertura_minima_pineada_contra_el_sql():
    """Mismo trato que MARGEN_DIAS_MIN (regla 2): el guard `cobertura < X` de
    v_margen_producto y MARGEN_COBERTURA_MIN del motor son el MISMO numero."""
    from pathlib import Path

    ruta = Path(__file__).resolve().parents[1] / "migrations" / "0018_fabrica_campanas.sql"
    assert f"< {g.MARGEN_COBERTURA_MIN} THEN NULL" in ruta.read_text(encoding="utf-8")
```

Run: `pytest tests/test_fabrica_plan.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git checkout -b fabrica-01-5-nucleo origin/master
git add app/fabrica_plan.py tests/test_fabrica_plan.py
git commit -m "feat(fabrica): nucleo puro del plan — target por margen minimo, semillas, payloads y huella (spec §3-§6)"
```

---

### Task 6: `tools/fabrica_campanas.py` — plan desde la base y dry-run (cero HTTP)

**Files:**
- Create: `tools/fabrica_campanas.py`
- Create: `tests/test_fabrica_campanas.py`

**Interfaces:**
- Consumes: `app.fabrica_plan` (tarea 5), `app.db.connect`, `app.optimizer.goals.fraccion_desde_settings`, `app.redaction` (`install_scrub_filter`, `scrub`), `v_margen_producto` (tarea 3), `keyword_biblioteca`/`negative_biblioteca` (tarea 2), `search_term_observation`, `ad_entity`, `ad_entity_state`, `listing`, `config_version`.
- Produces: funciones del tool `_dsn_read()`, `_fraccion(conn, platform)`, `_productos(conn, platform, ids)`, `_terminos_producto(conn, platform, listing_ids, *, sql=_SQL_TERMINOS, ventana=fp.VENTANA_DIAS)`, `_biblioteca(conn, tipo, platform)`, `_existentes(conn, platform, listing_ids)`, `_arma_plan(args, conn_read) -> PlanGrupo`, `_lote_nuevo(plan) -> str`, `_log(evento, **campos)`, `Abortar`, `_parser() -> argparse.ArgumentParser`, `main()`; SQL `_SQL_TERMINOS` (ventana del margen) y `_SQL_TERMINOS_EXACT` (ventana de CORTES del motor para los candidatos a exact). El dry-run imprime `lineas_dry_run` + `huella del conjunto: <sha>` y un JSON `{"evento": "dry_run", ...}`.

- [ ] **Step 1: Tests de SQL contra Postgres real y de dry-run sin HTTP (fallan: el tool no existe)**

```python
# tests/test_fabrica_campanas.py
"""tools/fabrica_campanas.py (FABRICA 01, tareas 6-9).

SQL contra Postgres REAL (precedente: el %s::platform IndeterminateDatatype
solo lo vio una base de verdad); dry-run y guards sin HTTP; mutacion con
httpx.MockTransport y ledger falso; sync+registro; desarmar; reconciliar."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from psycopg.rows import tuple_row
from psycopg.types.json import Json
from test_fabrica_migracion import _entidad, _ledger_producto, _producto, db_fabrica
from test_schema import _postgres_obligatorio_ausente

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fabrica_campanas as fc  # noqa: E402

from app import fabrica_plan as fp  # noqa: E402
from app.ads.config import AdsCredentials  # noqa: E402
from app.ads.structure import PerfilAds  # noqa: E402

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")
HOY = dt.date.today()


def _config(conn, fraccion="0.5", platform="amazon_mx"):
    conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('t', %s)",
        (Json({f"ads_target_fraccion_margen_{platform}": fraccion}),),
    )


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]


def _campana_con_producto(conn, lid, external="c-old", status="ENABLED", platform="amazon_mx"):
    """Campana existente con ad group y product_ad ligado al listing: es
    'campana del producto' para semillas y para el reporte de existentes."""
    camp = _entidad(conn, platform, "campaign", external)
    conn.execute("UPDATE ad_entity SET name = %s WHERE id = %s", (f"vieja {external}", camp))
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES (%s, %s, now())",
        (camp, status),
    )
    ag = _entidad(conn, platform, "ad_group", f"ag-{external}", parent=camp)
    _entidad(conn, platform, "product_ad", f"pa-{external}", parent=ag, listing_id=lid)
    return camp, ag


def _termino(conn, run, origen, texto, fecha, orders, cost, revenue, asin_like=False, obs=1):
    conn.execute(
        "INSERT INTO search_term_observation (platform, ad_entity_id, search_term, metric_date,"
        " observed_at, metric_currency, cost, clicks, orders, ad_revenue, is_asin_like,"
        " ingest_run_id) VALUES ('amazon_mx', %s, %s, %s, %s, 'MXN', %s, 5, %s, %s, %s, %s)",
        (origen, texto, fecha, dt.datetime(2026, 9, 1, obs, tzinfo=dt.UTC), cost, orders,
         revenue, asin_like, run),
    )


# ---------------------------------------------------------------------------
# SQL contra Postgres real
# ---------------------------------------------------------------------------


@_skip_db
def test_fraccion_lee_el_setting_vigente_y_aborta_sin_el():
    with db_fabrica("orbit_fab_frac") as conn:
        with pytest.raises(fc.Abortar, match="fraccion"):
            fc._fraccion(conn, "amazon_mx")
        _config(conn, "0.25")
        assert fc._fraccion(conn, "amazon_mx") == Decimal("0.25")
        _config(conn, "abc")  # config CORRUPTA presente: ValueError ruidoso, no abstencion
        with pytest.raises(ValueError):
            fc._fraccion(conn, "amazon_mx")


@_skip_db
def test_productos_exige_margen_y_seller_sku():
    with db_fabrica("orbit_fab_prod") as conn:
        con_margen, lid = _producto(conn, sku="A", asin="B0AAAAAAAA", seller_sku="SA")
        _ledger_producto(conn, con_margen, hoy=HOY)
        sin_margen, _ = _producto(conn, sku="B", asin="B0BBBBBBBB", seller_sku="SB")
        sin_sku, _ = _producto(conn, sku="C", asin="B0CCCCCCCC", seller_sku=None)
        _ledger_producto(conn, sin_sku, hoy=HOY)
        filas = fc._productos(conn, "amazon_mx", [con_margen])
        assert len(filas) == 1 and filas[0].seller_sku == "SA" and filas[0].listing_id == lid
        assert filas[0].margen_neto_pct == Decimal("40")
        with pytest.raises(fc.Abortar, match="sin margen"):
            fc._productos(conn, "amazon_mx", [con_margen, sin_margen])
        with pytest.raises(fc.Abortar, match="seller_sku"):
            fc._productos(conn, "amazon_mx", [sin_sku])
        with pytest.raises(fc.Abortar, match="no existe"):
            fc._productos(conn, "amazon_mx", [999999])


@_skip_db
def test_productos_multi_listing_aborta_nombrando_el_producto():
    """Fail-loud (regla 3): un producto con 2 listings en la plataforma haria
    N product ads por campana y reventaria la PK de campana_grupo_producto
    DESPUES de los POST. El plan ABORTA nombrando el producto: la fabrica no
    elige listing (multi-listing queda fuera de F1, residual --listing)."""
    with db_fabrica("orbit_fab_multi") as conn:
        pid, _ = _producto(conn, sku="A", asin="B0AAAAAAAA", seller_sku="SA")
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'B0AAAAAAB2', 'SA-2')",
            (pid,),
        )
        _ledger_producto(conn, pid, hoy=HOY)
        with pytest.raises(fc.Abortar, match="listings") as exc:
            fc._productos(conn, "amazon_mx", [pid])
        assert str(pid) in str(exc.value) and "(A)" in str(exc.value)  # nombra al producto


@_skip_db
def test_terminos_del_producto_colapsan_bitemporal_y_suman_en_ventana():
    """Solo terminos de campanas con product_ad del listing; ultima
    observacion por (origen, termino, dia) (regla 5); ventana [D-105, D-15).
    NOTA (tarea 1 (f)): ejercita filas en AMBOS granos (campana + ad group)
    sumadas — la hipotesis del UNION. Si la evidencia de la tarea 1 muestra
    ambos granos en produccion, la CTE `origenes` queda SOLO con ad_group y
    este test se ajusta (el grano campana se elimina del sembrado)."""
    with db_fabrica("orbit_fab_term") as conn:
        pid, lid = _producto(conn)
        camp, ag = _campana_con_producto(conn, lid)
        otra, _ = _campana_con_producto(conn, None, external="c-ajena")
        run = _run(conn)
        d = HOY - dt.timedelta(days=40)
        _termino(conn, run, camp, "collar perro", d, 1, 10, 100, obs=1)
        _termino(conn, run, camp, "collar perro", d, 2, 12, 150, obs=2)  # re-observacion: manda
        _termino(conn, run, ag, "collar perro", d + dt.timedelta(days=1), 1, 5, 50)
        _termino(conn, run, camp, "b0zzzzzzzz", d, 1, 1, 10, asin_like=True)
        _termino(conn, run, otra, "ajeno", d, 5, 1, 10)
        _termino(conn, run, camp, "viejo", HOY - dt.timedelta(days=120), 5, 1, 10)
        _termino(conn, run, camp, "inmaduro", HOY - dt.timedelta(days=5), 5, 1, 10)
        terminos = {t.texto: t for t in fc._terminos_producto(conn, "amazon_mx", [lid])}
        assert set(terminos) == {"collar perro", "b0zzzzzzzz"}
        assert terminos["collar perro"].orders == 3
        assert terminos["collar perro"].cost == Decimal("17") and terminos["collar perro"].revenue == Decimal("200")
        assert terminos["b0zzzzzzzz"].is_asin_like is True


@_skip_db
def test_terminos_exact_usan_la_ventana_de_cortes():
    """Regla 6: los candidatos a semilla exact maduran con la ventana de
    CORTES del motor (hasta hoy - 10d), no con la del margen (hasta hoy -
    15d): un termino de hace 12 dias entra a exact pero no a la ventana del
    margen; uno de hace 5 dias no entra a ninguna (inmaduro)."""
    with db_fabrica("orbit_fab_term2") as conn:
        _pid, lid = _producto(conn)
        camp, _ag = _campana_con_producto(conn, lid)
        run = _run(conn)
        _termino(conn, run, camp, "doce dias", HOY - dt.timedelta(days=12), 2, 10, 100)
        _termino(conn, run, camp, "cinco dias", HOY - dt.timedelta(days=5), 2, 10, 100)
        margen = {t.texto for t in fc._terminos_producto(conn, "amazon_mx", [lid])}
        exact = {
            t.texto
            for t in fc._terminos_producto(
                conn, "amazon_mx", [lid],
                sql=fc._SQL_TERMINOS_EXACT, ventana=fp.VENTANA_CORTES_DIAS,
            )
        }
        assert margen == set()  # ambos mas jovenes que D-15
        assert exact == {"doce dias"}


@_skip_db
def test_biblioteca_y_existentes():
    with db_fabrica("orbit_fab_bib") as conn:
        pid, lid = _producto(conn)
        conn.execute(
            "INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen, orders)"
            " VALUES ('collar_perro', 'amazon_mx', 'con ventas', 'x', 2),"
            " ('collar_perro', 'amazon_mx', 'sin ventas', 'x', 0),"
            " ('otro_tipo', 'amazon_mx', 'ajena', 'x', 5)"
        )
        conn.execute(
            "INSERT INTO negative_biblioteca (tipo_producto, platform, texto, origen)"
            " VALUES ('collar_perro', 'amazon_mx', 'gato', 'x')"
        )
        kws, negs = fc._biblioteca(conn, "collar_perro", "amazon_mx")
        assert kws == ["con ventas"] and negs == ["gato"]
        _campana_con_producto(conn, lid, external="c-old", status="PAUSED")
        existentes = fc._existentes(conn, "amazon_mx", [lid])
        assert [(e["external_id"], e["status"]) for e in existentes] == [("c-old", "PAUSED")]


# ---------------------------------------------------------------------------
# Dry-run sin HTTP (conexion falsa)
# ---------------------------------------------------------------------------


class _ConnFalsa:
    """Sirve el plan enlatado por consulta y GRABA escrituras + commits."""

    def __init__(self, *, settings=None, productos=(), terminos=(), terminos_exact=None,
                 biblioteca=([], []), existentes=(), grupo=None, pendientes=(), lote_fila=None,
                 secuencia=None):
        # r3 codex 6: lista OPCIONAL compartida con _Amazon para afirmar el
        # orden global sql/http (ledger ANTES de cada POST)
        self.secuencia = secuencia if secuencia is not None else []
        self.settings = settings
        self.productos = list(productos)
        self.terminos = list(terminos)
        # candidatos a exact (ventana de cortes): por default los mismos terminos
        self.terminos_exact = list(terminos) if terminos_exact is None else list(terminos_exact)
        self.biblioteca = biblioteca
        self.existentes = list(existentes)
        self.grupo = grupo
        self.pendientes = list(pendientes)
        self.lote_fila = lote_fila  # (plan, estado) para SELECT ... FROM fabrica_lote (tarea 8)
        self.queries = []
        self.escrituras = []  # (sql plano, params)
        self.commits = 0
        self.rollbacks = 0
        self._seq = 100
        self.row_factory = None

    def execute(self, sql, params=None):
        plano = " ".join(str(sql).split())
        self.queries.append((plano, params))
        self.secuencia.append(("sql", plano))
        bajo = plano.lower()
        if bajo.startswith(("insert", "update")):
            self.escrituras.append((plano, params))
            self._seq += 1
            return _Cursor([(self._seq,)])
        if "from config_version" in bajo:
            return _Cursor([(1, self.settings)] if self.settings is not None else [])
        if "from v_margen_producto" in bajo or "join v_margen_producto" in bajo:
            return _Cursor(self.productos)
        if "ventana_cortes" in bajo:  # _SQL_TERMINOS_EXACT (ventana de cortes)
            return _Cursor(self.terminos_exact)
        if "search_term_observation" in bajo:
            return _Cursor(self.terminos)
        if "from keyword_biblioteca" in bajo:
            return _Cursor([(t,) for t in self.biblioteca[0]])
        if "from negative_biblioteca" in bajo:
            return _Cursor([(t,) for t in self.biblioteca[1]])
        if "ad_entity_state" in bajo and "product_ad" in bajo:
            return _Cursor(self.existentes)
        if "from fabrica_lote_paso" in bajo and "recurso = 'campaign'" in bajo:
            return _Cursor(self.grupo or [])  # _SQL_CAMPANAS_DEL_LOTE (desarmar, tarea 9)
        if "from fabrica_lote_paso" in bajo and "join fabrica_lote" in bajo:
            # _SQL_PENDIENTES (r3 codex 4): el fake aplica el WHERE de
            # plataforma igual que el SQL real (params: lote, lote, plat, plat)
            pend = self.pendientes
            if params is not None and len(params) > 2 and params[2] is not None:
                pend = [f for f in pend if f[3] == params[2]]
            return _Cursor(pend)
        if "join campana_grupo_rol" in bajo or "from campana_grupo_rol" in bajo:
            return _Cursor(self.grupo or [])
        if "from ads_optimizer_goal" in bajo:
            return _Cursor([])  # _SQL_GOAL_EXISTENTE: sin goal previo (tarea 8)
        if "plan, estado from fabrica_lote" in bajo:
            return _Cursor([self.lote_fila] if self.lote_fila is not None else [])
        if "from fabrica_lote where" in bajo:
            return _Cursor([("amazon_mx",)])
        if "from fabrica_lote_paso" in bajo:
            return _Cursor(self.pendientes)
        if "from ad_entity" in bajo:
            return _Cursor([(self._seq + 1000,)])
        raise AssertionError(f"SQL inesperado: {plano[:140]}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _Cursor:
    def __init__(self, filas):
        self._filas = list(filas)

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def fetchall(self):
        return list(self._filas)


# Fila de _SQL_PRODUCTOS: (product_id, odoo_sku, listing_id, asin, seller_sku, margen)
FILA_PRODUCTO = (1, "ODOO-1", 11, "B0AAAAAAAA", "SS-1", Decimal("38.20"))
ARGS_BASE = [
    "--plataforma", "amazon_mx", "--tipo-producto", "collar_perro",
    "--nombre-base", "Collar reflectante", "--productos", "1", "--modo", "shadow",
    "--budget-auto", "150", "--budget-phrase", "120", "--budget-product", "120",
    "--budget-broad", "120", "--budget-exact", "150",
    "--bid-auto", "4.50", "--bid-phrase", "5.00", "--bid-product", "5.00",
    "--bid-broad", "4.00", "--bid-exact", "6.00",
]


def _eventos(capsys):
    return [json.loads(linea) for linea in capsys.readouterr().out.splitlines() if linea.startswith("{")]


def _frontera_lectura(monkeypatch, conn_read):
    monkeypatch.setenv("ORBIT_DSN_READ", "dsn-read")
    monkeypatch.setenv("ORBIT_DSN_ADMIN", "dsn-admin")
    monkeypatch.setenv("ORBIT_DSN_INGEST", "dsn-ingest")
    monkeypatch.setattr(fc, "connect", lambda dsn: {"dsn-read": conn_read}[dsn])


def test_dry_run_imprime_plan_huella_y_no_abre_http(monkeypatch, capsys):
    conn = _ConnFalsa(
        settings={"ads_target_fraccion_margen_amazon_mx": "0.5"},
        productos=[FILA_PRODUCTO],
        terminos=[("collar perro", False, 3, Decimal("10"), Decimal("100"))],
        biblioteca=(["collar led"], ["gato"]),
        existentes=[("c-old", "vieja", "PAUSED")],
    )
    _frontera_lectura(monkeypatch, conn)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *ARGS_BASE])
    monkeypatch.setattr(fc, "httpx", None)  # cualquier HTTP revienta
    assert fc.main() == 0
    salida = capsys.readouterr().out
    assert "target=19.10" in salida and "semillas=" in salida
    assert "huella del conjunto:" in salida
    assert "existente (solo se informa" in salida and "c-old" in salida
    assert conn.escrituras == [] and conn.commits >= 1  # la lectura cierra su txn


def test_dry_run_dice_semillas_cero_explicito(monkeypatch, capsys):
    conn = _ConnFalsa(settings={"ads_target_fraccion_margen_amazon_mx": "0.5"}, productos=[FILA_PRODUCTO])
    _frontera_lectura(monkeypatch, conn)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *ARGS_BASE])
    assert fc.main() == 0
    salida = capsys.readouterr().out
    assert "category_phrase" in salida and "semillas=0" in salida


def test_sin_fraccion_o_bid_fuera_de_banda_aborta_sin_http(monkeypatch):
    conn = _ConnFalsa(settings={}, productos=[FILA_PRODUCTO])
    _frontera_lectura(monkeypatch, conn)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *ARGS_BASE])
    with pytest.raises(fc.Abortar, match="fraccion"):
        fc.main()
    conn = _ConnFalsa(settings={"ads_target_fraccion_margen_amazon_mx": "0.5"}, productos=[FILA_PRODUCTO])
    _frontera_lectura(monkeypatch, conn)
    args = [a if a != "6.00" else "45.01" for a in ARGS_BASE]  # bid-exact sobre el techo MXN
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *args])
    with pytest.raises(fc.Abortar, match="bid"):
        fc.main()


def test_modo_es_obligatorio_y_cerrado(monkeypatch):
    sin_modo = [a for i, a in enumerate(ARGS_BASE) if a not in ("--modo", "shadow")]
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *sin_modo])
    with pytest.raises(SystemExit):
        fc.main()
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *[a if a != "shadow" else "off" for a in ARGS_BASE]])
    with pytest.raises(SystemExit):
        fc.main()
```

- [ ] **Step 2: Rojo**

Run: `pytest tests/test_fabrica_campanas.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'fabrica_campanas'`.

- [ ] **Step 3: Implementar el tool (primera mitad: plan + dry-run)**

```python
#!/usr/bin/env python3
"""Fabrica de campanas Amazon SP por grupo con estructura fija (FABRICA 01).

Spec: docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md.
Operacion de NEGOCIO con go del dueno (no pasa por apply_queue ni por la
escalera): crea 5 campanas (exact, phrase, broad, product targeting, auto)
para un tipo_producto, con target = fraccion x margen MINIMO de los productos
(v_margen_producto), product ads por producto, semillas de la biblioteca y
del historial propio, ledger fabrica_lote/fabrica_lote_paso ANTES de cada
HTTP, readback por LIST, sync de estructura y goals por campana.

QUE HACE, EN ORDEN:

 1. Plan desde la BASE (ORBIT_DSN_READ): fraccion del setting vigente,
    productos con margen y seller_sku, semillas, campanas existentes de los
    mismos productos (SOLO se reportan, decision 9). Validacion SIN HTTP.
 2. Dry-run por defecto: tabla del plan + huella del conjunto; cero HTTP.
 3. Mutacion (--acepto-mutacion-real --esperado 5 --huella H --go "<literal>"):
    fila fabrica_lote planeado + commit -> por rol en orden exact -> phrase
    -> broad -> product -> auto: por cada POST fila fabrica_lote_paso
    planeado + commit -> POST con vendor v3 -> readback LIST -> applied.
    Rechazo o readback que no cuadra -> failed y el lote SE DETIENE
    declarando lo creado.
 4. sync_structure (ORBIT_DSN_INGEST) para que ad_entity/ad_entity_state
    tengan las campanas nuevas -> registro campana_grupo/_rol/_producto y
    5 goals via app.goals_write.crea_goal (ORBIT_DSN_ADMIN).
 5. Reversa: --desarmar <lote> --acepto-mutacion-real --go "<literal>":
    PUT /sp/campaigns state PAUSED a las campanas applied del LEDGER DE
    PASOS (durable desde antes del HTTP: cubre lotes muertos a medias, sin
    grupo registrado) + enabled=false en sus goals si existen.
 6. --reconciliar [--lote X]: cruza pasos planeado/failed contra el LIST
    real y promueve solo lo verificado.

CORRIDA (dentro del contenedor app, por stdin; la imagen solo trae app/):

    docker exec -i orbit-app-1 python - --plataforma amazon_mx \
      --tipo-producto collar_perro --nombre-base "Collar reflectante" \
      --productos 12,15 --modo shadow \
      --budget-auto 150 --budget-phrase 120 --budget-product 120 \
      --budget-broad 120 --budget-exact 150 \
      --bid-auto 4.50 --bid-phrase 5.00 --bid-product 5.00 \
      --bid-broad 4.00 --bid-exact 6.00 < tools/fabrica_campanas.py
    ... --acepto-mutacion-real --esperado 5 --huella <sha> --go "<literal>" \
      < tools/fabrica_campanas.py

HTTP propio con el sello v3 (patron archiva_inertes/reactiva_campanas): NO
importa app.ads.write (candado en tests/test_architecture.py). Shapes de
campanas/adGroups/targets y el camino feliz de productAds son HIPOTESIS
hasta la sonda (plans/fabrica-01.md tarea 11).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import logging
import os
import sys
import time
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

import httpx
import psycopg
from psycopg.rows import tuple_row

from app import fabrica_plan as fp
from app import goals_write
from app.ads.client import DEFAULT_BASE_URL, AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import evaluar_perfiles, fetch_structure, sync_structure
from app.db import connect
from app.optimizer.goals import fraccion_desde_settings
from app.redaction import install_scrub_filter, register_secret, scrub

install_scrub_filter(logging.getLogger())

API = DEFAULT_BASE_URL
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
VENDOR_CAMPANAS = fp.VENDOR_POR_PATH["/sp/campaigns"]
_PAUSADA = "PAUSED"
_ESPERADO_ROLES = len(fp.ROLES_ORDEN_CREACION)

# --- SQL de plan (ORBIT_DSN_READ) ------------------------------------------
# Primer parametro CASTEADO (%s::platform): sin el cast Postgres no infiere el
# tipo (IndeterminateDatatype, lección de archiva_inertes).
_SQL_SETTINGS = "SELECT id, settings FROM config_version ORDER BY id DESC LIMIT 1"

# Un producto con N listings en la plataforma produce N filas: _productos
# ABORTA nombrandolo (fail-loud, regla 3 — la fabrica no elige listing; sin
# el guard serian N product ads por campana y UniqueViolation en
# campana_grupo_producto DESPUES de los POST). Residual F1: --listing.
_SQL_PRODUCTOS = """
SELECT p.id, p.odoo_sku, l.id, l.external_id, l.seller_sku, m.margen_neto_pct
  FROM product p
  LEFT JOIN listing l ON l.product_id = p.id AND l.platform = %s::platform
  LEFT JOIN v_margen_producto m ON m.product_id = p.id AND m.platform = %s::platform
 WHERE p.id = ANY(%s)
 ORDER BY p.id
"""

# Terminos de las campanas del producto: campanas con un product_ad ligado
# al listing. GRANO DEL ORIGEN (depende de la evidencia de la tarea 1 (f),
# registrada en "Decisiones y evidencia"): el UNION campana + ad group de la
# CTE `origenes` DUPLICA orders/cost si search_term_observation tiene filas
# en AMBOS granos de la misma campana. Variantes:
#   (a) UN solo grano en produccion -> `origenes` se simplifica a ese SELECT;
#   (b) AMBOS granos -> SOLO el grano ad_group (mas fino): `origenes` queda
#       SELECT ag.id FROM ad_entity ag JOIN campanas c
#         ON c.campaign_id = ag.parent_id WHERE ag.kind = 'ad_group'
# Hasta que la tarea 1 cierre (f) se mantiene el UNION (peor caso: infla
# orders/cost de las semillas; el target no lo toca).
_SQL_TERMINOS = """
WITH ventana AS (
    SELECT CURRENT_DATE - %s AS desde, CURRENT_DATE - %s AS hasta
),
campanas AS (
    SELECT DISTINCT ag.parent_id AS campaign_id
      FROM ad_entity pa
      JOIN ad_entity ag ON ag.id = pa.parent_id
     WHERE pa.kind = 'product_ad' AND pa.platform = %s::platform
       AND pa.listing_id = ANY(%s)
),
origenes AS (
    SELECT campaign_id AS id FROM campanas
    UNION
    SELECT ag.id FROM ad_entity ag JOIN campanas c ON c.campaign_id = ag.parent_id
     WHERE ag.kind = 'ad_group'
),
ultimas AS (
    SELECT DISTINCT ON (s.ad_entity_id, s.search_term, s.metric_date)
           s.search_term, s.is_asin_like, s.orders, s.cost, s.ad_revenue
      FROM search_term_observation s
      JOIN origenes o ON o.id = s.ad_entity_id
      CROSS JOIN ventana v
     WHERE s.platform = %s::platform
       AND s.metric_date >= v.desde AND s.metric_date < v.hasta
     ORDER BY s.ad_entity_id, s.search_term, s.metric_date, s.observed_at DESC,
              -- desempate bitemporal sellado (r3 codex 3): mismo observed_at
              -- en dos reportes = gana el reporte mas reciente (patron de
              -- app/optimizer/windows.py, colapso de observaciones)
              s.source_report_id DESC NULLS LAST
)
SELECT search_term, bool_or(is_asin_like), SUM(orders), SUM(cost), SUM(ad_revenue)
  FROM ultimas
 GROUP BY search_term
HAVING COALESCE(SUM(orders), 0) >= 1
 ORDER BY SUM(orders) DESC, search_term
"""

# MISMO agregado pero sobre la ventana de CORTES del motor (VENTANA_CORTES_
# DIAS = [D-39, D-10] inclusive; regla 6: madurez >= 10d, spec §6): alimenta
# SOLO los candidatos a semilla exact. El CTE ventana_cortes es el marcador
# que distingue esta consulta en los tests.
_SQL_TERMINOS_EXACT = _SQL_TERMINOS.replace("ventana", "ventana_cortes")

_SQL_BIBLIOTECA_KW = """
SELECT texto FROM keyword_biblioteca
 WHERE tipo_producto = %s AND platform = %s::platform AND orders >= 1
 ORDER BY orders DESC, texto
"""
_SQL_BIBLIOTECA_NEG = """
SELECT texto FROM negative_biblioteca
 WHERE tipo_producto = %s AND platform = %s::platform
 ORDER BY texto
"""

_SQL_EXISTENTES = """
SELECT DISTINCT c.external_id, c.name, s.status
  FROM ad_entity pa
  JOIN ad_entity ag ON ag.id = pa.parent_id
  JOIN ad_entity c ON c.id = ag.parent_id
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = c.id
 WHERE pa.kind = 'product_ad' AND pa.platform = %s::platform
   AND pa.listing_id = ANY(%s)
 ORDER BY c.external_id
"""


class Abortar(RuntimeError):
    """Fail-closed: la realidad difiere del plan o la API rechaza."""


def _log(evento: str, **campos: Any) -> None:
    print(scrub(json.dumps({"evento": evento, **campos}, default=str)), flush=True)


def _dsn(nombre: str) -> str:
    dsn = os.environ.get(nombre)
    if not dsn:
        raise Abortar(f"{nombre} no esta en el entorno (corre dentro del contenedor app)")
    return dsn


def _dsn_read() -> str:
    return _dsn("ORBIT_DSN_READ")


def _dsn_admin() -> str:
    return _dsn("ORBIT_DSN_ADMIN")


def _dsn_ingest() -> str:
    return _dsn("ORBIT_DSN_INGEST")


# --- lecturas ----------------------------------------------------------------


def _fraccion(conn: psycopg.Connection, platform: str) -> Decimal:
    fila = conn.execute(_SQL_SETTINGS).fetchone()
    settings = fila[1] if fila else {}
    fraccion = fraccion_desde_settings(settings or {}, platform)  # invalida -> ValueError
    if fraccion is None:
        raise Abortar(
            f"sin fraccion: ads_target_fraccion_margen_{platform} ausente en la config vigente"
        )
    return fraccion


def _productos(conn: psycopg.Connection, platform: str, ids: list[int]) -> list[fp.ProductoGrupo]:
    filas = conn.execute(_SQL_PRODUCTOS, (platform, platform, ids)).fetchall()
    vistos = {f[0] for f in filas}
    faltan = sorted(set(ids) - vistos)
    if faltan:
        raise Abortar(f"producto(s) {faltan} no existe(n) en product")
    n_listings: dict[int, int] = {}
    for f in filas:
        n_listings[f[0]] = n_listings.get(f[0], 0) + 1
    out = []
    for pid, odoo, lid, asin, sku, margen in filas:
        if n_listings[pid] > 1:
            raise Abortar(
                f"producto {pid} ({odoo}) tiene {n_listings[pid]} listings en {platform}:"
                " la fabrica no elige (regla 3); multi-listing queda fuera de F1"
                " hasta --listing explicito (residual)"
            )
        if lid is None:
            raise Abortar(f"producto {pid} ({odoo}) sin listing en {platform}")
        if margen is None:
            raise Abortar(f"producto {pid} ({odoo}) sin margen medible en v_margen_producto (regla 3)")
        if not sku or not str(sku).strip():
            raise Abortar(f"producto {pid} ({odoo}) sin seller_sku en {platform}: no hay product ad")
        out.append(fp.ProductoGrupo(pid, odoo, lid, asin, str(sku).strip(), Decimal(margen)))
    return out


def _terminos_producto(
    conn: psycopg.Connection,
    platform: str,
    listing_ids: list[int],
    *,
    sql: str = _SQL_TERMINOS,
    ventana: tuple[int, int] = fp.VENTANA_DIAS,
) -> list[fp.TerminoProducto]:
    """Agregado bitemporal de search_term_observation en la ventana pedida.
    Default: ventana del margen [D-105, D-15); con sql=_SQL_TERMINOS_EXACT y
    ventana=fp.VENTANA_CORTES_DIAS: candidatos a exact (regla 6)."""
    desde, hasta = ventana
    filas = conn.execute(sql, (desde, hasta, platform, listing_ids, platform)).fetchall()
    return [
        fp.TerminoProducto(
            texto=f[0],
            is_asin_like=bool(f[1]),
            orders=int(f[2]) if f[2] is not None else None,
            cost=Decimal(f[3]) if f[3] is not None else None,
            revenue=Decimal(f[4]) if f[4] is not None else None,
        )
        for f in filas
    ]


def _biblioteca(conn: psycopg.Connection, tipo: str, platform: str) -> tuple[list[str], list[str]]:
    kws = [f[0] for f in conn.execute(_SQL_BIBLIOTECA_KW, (tipo, platform)).fetchall()]
    negs = [f[0] for f in conn.execute(_SQL_BIBLIOTECA_NEG, (tipo, platform)).fetchall()]
    return kws, negs


def _existentes(conn: psycopg.Connection, platform: str, listing_ids: list[int]) -> list[dict]:
    return [
        {"external_id": f[0], "name": f[1], "status": f[2]}
        for f in conn.execute(_SQL_EXISTENTES, (platform, listing_ids)).fetchall()
    ]


def _decimal(texto: str, nombre: str) -> Decimal:
    try:
        valor = Decimal(str(texto).strip())
    except InvalidOperation:
        raise Abortar(f"{nombre} no es un numero: {texto!r}") from None
    if not valor.is_finite():
        raise Abortar(f"{nombre} debe ser finito: {texto!r}")
    return valor


def _parametros(args) -> dict[str, fp.ParametrosRol]:
    sufijo = {
        "auto_discovery": "auto",
        "category_phrase": "phrase",
        "product_targeting": "product",
        "category_broad": "broad",
        "category_exact": "exact",
    }
    return {
        rol: fp.ParametrosRol(
            rol=rol,
            budget=_decimal(getattr(args, f"budget_{s}"), f"--budget-{s}"),
            bid=_decimal(getattr(args, f"bid_{s}"), f"--bid-{s}"),
        )
        for rol, s in sufijo.items()
    }


def _ids_productos(texto: str) -> list[int]:
    try:
        ids = sorted({int(p) for p in texto.split(",") if p.strip()})
    except ValueError:
        raise Abortar(f"--productos debe ser ids enteros separados por coma: {texto!r}") from None
    if not ids:
        raise Abortar("--productos no puede ser vacio")
    return ids


def _arma_plan(args, conn_read: psycopg.Connection) -> fp.PlanGrupo:
    """Plan completo desde la base + validacion SIN HTTP (spec §5.1)."""
    try:
        tipo = fp.valida_tipo_producto(args.tipo_producto)
        moneda = fp.MONEDA_POR_PLATAFORMA[args.plataforma]
        parametros = _parametros(args)
        fp.valida_parametros(parametros, moneda)
        ids = _ids_productos(args.productos)
        fraccion = _fraccion(conn_read, args.plataforma)
        productos = _productos(conn_read, args.plataforma, ids)
        target = fp.target_del_grupo([p.margen_neto_pct for p in productos], fraccion)
        listings = [p.listing_id for p in productos]
        terminos = _terminos_producto(conn_read, args.plataforma, listings)
        terminos_exact = _terminos_producto(
            conn_read, args.plataforma, listings,
            sql=_SQL_TERMINOS_EXACT, ventana=fp.VENTANA_CORTES_DIAS,
        )
        kws, negs = _biblioteca(conn_read, tipo, args.plataforma)
        semillas = fp.semillas_desde_terminos(
            terminos, kws, negs, target.aplicado, terminos_exact=terminos_exact
        )
        existentes = tuple(_existentes(conn_read, args.plataforma, listings))
    except fp.PlanInvalido as exc:
        raise Abortar(str(exc)) from exc
    finally:
        conn_read.commit()  # la lectura cierra su txn ANTES de cualquier red
    if not args.nombre_base or not args.nombre_base.strip():
        raise Abortar("--nombre-base no puede ser vacio")
    return fp.PlanGrupo(
        platform=args.plataforma,
        tipo_producto=tipo,
        nombre_base=args.nombre_base.strip(),
        fecha=datetime.datetime.now(datetime.UTC).date(),
        moneda=moneda,
        modo=args.modo,
        productos=tuple(productos),
        parametros=parametros,
        target=target,
        semillas=semillas,
        existentes=existentes,
    )


def _lote_nuevo(plan: fp.PlanGrupo) -> str:
    marca = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    return f"fabrica-{plan.platform}-{plan.tipo_producto}-{marca}"


def _imprime_dry_run(plan: fp.PlanGrupo, huella: str) -> None:
    for linea in fp.lineas_dry_run(plan):
        print(linea, flush=True)
    print(f"huella del conjunto: {huella}", flush=True)


def _crear(args) -> int:
    conn_read = connect(_dsn_read())
    plan = _arma_plan(args, conn_read)
    huella = fp.huella_plan(plan)
    _imprime_dry_run(plan, huella)
    _log(
        "plan",
        platform=plan.platform,
        tipo_producto=plan.tipo_producto,
        target=str(plan.target.aplicado),
        productos=[p.product_id for p in plan.productos],
        existentes=len(plan.existentes),
        huella=huella,
    )
    if not args.acepto_mutacion_real:
        _log("dry_run", huella=huella, nota="sin --acepto-mutacion-real no se toca Amazon")
        return 0
    return _mutar(args, plan, huella)  # tarea 7


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plataforma", choices=tuple(fp.MONEDA_POR_PLATAFORMA), default=None)
    ap.add_argument("--tipo-producto", default=None)
    ap.add_argument("--nombre-base", default=None)
    ap.add_argument("--productos", default=None, help="ids de product separados por coma")
    ap.add_argument("--modo", choices=fp.MODOS_GOAL, default=None,
                    help="modo de los 5 goals (decision 13: explicito, sin default)")
    for s in ("auto", "phrase", "product", "broad", "exact"):
        ap.add_argument(f"--budget-{s}", default=None)
        ap.add_argument(f"--bid-{s}", default=None)
    ap.add_argument("--acepto-mutacion-real", action="store_true")
    ap.add_argument("--esperado", type=int, default=None, help="campanas autorizadas (5)")
    ap.add_argument("--huella", default=None, help="huella del dry-run")
    ap.add_argument("--go", default=None, help="literal del dueno (va al ledger)")
    ap.add_argument("--desarmar", default=None, help="lote a pausar (reversa, tarea 9)")
    ap.add_argument("--reconciliar", action="store_true")
    ap.add_argument("--lote", default=None)
    return ap


def _valida_args_creacion(args) -> None:
    faltan = [
        nombre for nombre in ("plataforma", "tipo_producto", "nombre_base", "productos", "modo")
        if getattr(args, nombre) is None
    ]
    faltan += [
        f"{k}_{s}" for k in ("budget", "bid") for s in ("auto", "phrase", "product", "broad", "exact")
        if getattr(args, f"{k}_{s}") is None
    ]
    if faltan:
        raise SystemExit(f"faltan argumentos obligatorios: {', '.join(faltan)}")


def main() -> int:
    args = _parser().parse_args()
    if args.reconciliar:
        return _reconciliar_cmd(args)  # tarea 9
    if args.desarmar is not None:
        return _desarmar(args)  # tarea 9
    _valida_args_creacion(args)
    return _crear(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abortar as exc:
        _log("ABORTAR_FAIL_CLOSED", motivo=str(exc))
        sys.exit(2)
```

Para que el archivo importe en esta tarea, agregar stubs explícitos que las tareas 7 y 9 reemplazan. **Layout innegociable del archivo: los stubs (y sus reemplazos de las tareas 7/8/9) van ANTES de `def main()`; el bloque `if __name__ == "__main__":` es SIEMPRE lo último del archivo.** `main()` despacha `_mutar`/`_desarmar`/`_reconciliar_cmd`/`_registrar_cmd`: si esas funciones quedan texturalmente DESPUÉS del guard, toda corrida real por stdin (`python - < tools/fabrica_campanas.py ...`) revienta con `NameError` (el guard ejecuta `main()` antes de que existan los nombres) mientras la suite — que importa el módulo completo — queda verde (cross-review r2 glm, hallazgo 1):

```python
def _mutar(args, plan, huella) -> int:  # tarea 7
    raise Abortar("mutacion real: pendiente de la tarea 7 del plan")


def _desarmar(args) -> int:  # tarea 9
    raise Abortar("--desarmar: pendiente de la tarea 9 del plan")


def _reconciliar_cmd(args) -> int:  # tarea 9
    raise Abortar("--reconciliar: pendiente de la tarea 9 del plan")
```

El candado `test_fabrica_guard_main_es_lo_ultimo_del_archivo` de la tarea 10 pinea este layout.

- [ ] **Step 4: Verde**

Run: `pytest tests/test_fabrica_campanas.py -v && ruff check tools/fabrica_campanas.py`
Expected: PASS (4 de Postgres + 4 sin HTTP). Los unused imports (`httpx`, `contextlib`, `time`, `register_secret`, `AdsClient`, `evaluar_perfiles`, `fetch_structure`, `sync_structure`, `goals_write`) disparan F401 en esta tarea: dejarlos con `# noqa: F401  # tarea 7/8` SOLO hasta la tarea 7, que los usa y quita el noqa.

- [ ] **Step 5: Commit**

```bash
git checkout -b fabrica-01-6-dry-run origin/master
git add tools/fabrica_campanas.py tests/test_fabrica_campanas.py
git commit -m "feat(fabrica): tools/fabrica_campanas.py — plan desde la base y dry-run con huella (spec §5.1-5.2)"
```

---

### Task 7: mutación real — ledger pre-HTTP, orden fijo, readback y detención

**Files:**
- Modify: `tools/fabrica_campanas.py` (reemplazar el stub `_mutar`)
- Modify: `tests/test_fabrica_campanas.py`

**Interfaces:**
- Consumes: `fp.pasos_del_rol`, `fp.VENDOR_POR_PATH`, `fp.ENVOLTURA_POR_PATH`, `fp.CLAVE_ID_POR_PATH`, `fp.LIST_POR_PATH`, `fp.FILTRO_ID_POR_LIST`, `fp.CONTENEDOR_POR_LIST`, `fp.ack_ok`, `fp.id_creado`, `fp.plan_como_json`; `AdsCredentials.from_secrets_dir`, `AdsClient.list_objects`, `evaluar_perfiles`; tablas `fabrica_lote`, `fabrica_lote_paso`.
- Produces: `_mutar(args, plan, huella) -> int`, `_token_lwa(cred, http)`, `_perfiles(cliente_lectura)`, `_post(http, token, cred, profile, path, payload)`, `_readback(cliente_lectura, profile, path_create, external) -> dict | None`, `_inserta_lote`, `_inserta_paso`, `_sella_paso`, `_sella_lote`, `_ejecuta_rol(ctx, plan, rol) -> dict` (externos del rol: `{"rol": rol, "campaign": id, "ad_group": id, "product_ads": [...], "semillas": [...]}`), `_readback_cuadra(leido, payload) -> bool`, `_expresion_normalizada`, `_monto_wire_cuadra`, dataclass `_Ctx(http, token, cred, cliente_lectura, profile, conn_admin, lote)`. `_mutar` queda en su forma FINAL: token LWA ANTES de `_inserta_lote` (la intención durable se exige antes del primer POST de MUTACIÓN, no antes del token) y `_registrar(ctx, plan, creadas)` dentro de un `try` propio con `conn_admin.rollback()` ANTES del sello `failed` (la tarea 8 solo completa `_registrar`; en esta tarea es un stub que solo loguea).

- [ ] **Step 1: Tests de mutación con MockTransport (fallan: `_mutar` es stub)**

```python
# agregar a tests/test_fabrica_campanas.py

_CREDS = AdsCredentials(client_id="cid", client_secret="sec", refresh_token="rt")


def _perfil(platform="amazon_mx", profile_id=101):
    return PerfilAds(profile_id=profile_id, country="MX", currency_code="MXN", account_type="seller",
                     valid_payment_method=True, account_name="t", aceptado=True, platform=platform,
                     moneda="MXN", motivo=None)


class _Amazon:
    """MockTransport: LWA + creates por path (cola) + readbacks por LIST.

    NOTA (residual cubierto por la sonda de la tarea 11): el 207 con
    success/error anidado por envoltura lo INVENTA este mock siguiendo el
    sello del probe 2.5; los shapes reales se sellan (o corrigen) con el log
    de la sonda."""

    def __init__(self, fallar_en=None, readback_malo=None, secuencia=None):
        self.pedidos = []
        self.creados = {}  # path -> contador
        self.fallar_en = fallar_en  # (path, n-esimo POST de ese path) que responde 400
        self.readback_malo = readback_malo  # external que el LIST devuelve PAUSED
        self.objetos = {}  # external -> payload creado (el LIST lo devuelve tal cual)
        # r3 codex 6: orden global sql/http cuando el test comparte la lista
        self.secuencia = secuencia if secuencia is not None else []

    def __call__(self, request):
        self.pedidos.append(request)
        url = str(request.url)
        if "auth/o2/token" in url:
            self.secuencia.append(("http", "lwa"))
            return httpx.Response(200, json={"access_token": "tok"})
        path = url.replace(fc.API, "")
        self.secuencia.append(("http", path))
        if path.endswith("/list"):
            return self._list(path, json.loads(request.content))
        assert path in fp.VENDOR_POR_PATH, path
        assert request.headers["Content-Type"] == fp.VENDOR_POR_PATH[path]
        assert request.headers["Accept"] == fp.VENDOR_POR_PATH[path]
        cuerpo = json.loads(request.content)
        assert list(cuerpo) == [fp.ENVOLTURA_POR_PATH[path]] and len(cuerpo[fp.ENVOLTURA_POR_PATH[path]]) == 1
        n = self.creados[path] = self.creados.get(path, 0) + 1
        if self.fallar_en == (path, n):
            return httpx.Response(400, json={"code": "400", "details": "rechazado"})
        clave = fp.CLAVE_ID_POR_PATH[path]
        ext = f"{fp.ENVOLTURA_POR_PATH[path]}-{n}"
        self.objetos[ext] = cuerpo[fp.ENVOLTURA_POR_PATH[path]][0]
        return httpx.Response(207, json={fp.ENVOLTURA_POR_PATH[path]: {"success": [{"index": 0, clave: ext}], "error": []}})

    def _list(self, path, body):
        filtro = fp.FILTRO_ID_POR_LIST[path]
        ext = body[filtro]["include"][0]
        contenedor = fp.CONTENEDOR_POR_LIST[path]
        clave = fp.CLAVE_ID_POR_PATH[path.removesuffix("/list")]
        estado = "PAUSED" if ext == self.readback_malo else "ENABLED"
        return httpx.Response(200, json={contenedor: [{**self.objetos.get(ext, {}), clave: ext, "state": estado}]})

    def posts(self, path):
        return [p for p in self.pedidos if str(p.url).endswith(path) and not str(p.url).endswith("/list")]


class _ClienteLectura:
    """AdsClient de lectura sobre el mismo MockTransport (readback)."""

    def __init__(self, transporte):
        self._c = httpx.Client(transport=transporte)

    def list_objects(self, path, body, *, profile_id):
        return self._c.post(f"{fc.API}{path}", json=body)


def _frontera_mutacion(monkeypatch, conn_read, conn_admin, amazon, perfiles=None):
    _frontera_lectura(monkeypatch, conn_read)
    monkeypatch.setattr(fc, "connect", lambda dsn: {"dsn-read": conn_read, "dsn-admin": conn_admin}[dsn])
    monkeypatch.setattr(AdsCredentials, "from_secrets_dir", classmethod(lambda cls: _CREDS))
    transporte = httpx.MockTransport(amazon)
    monkeypatch.setattr(fc, "AdsClient", lambda cred: _ClienteLectura(transporte))
    monkeypatch.setattr(fc, "evaluar_perfiles", lambda c: perfiles if perfiles is not None else [_perfil()])
    monkeypatch.setattr(fc, "httpx", SimpleNamespace(Client=lambda **kw: httpx.Client(transport=transporte), Timeout=lambda **kw: None))
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)
    monkeypatch.setattr(fc, "_registrar", lambda ctx, plan, externos: None)  # tarea 8


def _conn_plan(**extra):
    base = dict(settings={"ads_target_fraccion_margen_amazon_mx": "0.5"}, productos=[FILA_PRODUCTO],
                terminos=[("collar noche", False, 2, Decimal("10"), Decimal("100"))],
                biblioteca=(["collar led", "B0DDDDDDDD"], ["gato"]))
    base.update(extra)
    return _ConnFalsa(**base)


def _args_go(huella, esperado=5, go="go"):
    return ["fabrica_campanas.py", *ARGS_BASE, "--acepto-mutacion-real", "--esperado", str(esperado),
            "--huella", huella, "--go", go]


def _huella_de(monkeypatch):
    conn = _conn_plan()
    _frontera_lectura(monkeypatch, conn)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", *ARGS_BASE])
    fc.main()
    return conn


def _pasos(conn_admin):
    return [p for s, p in conn_admin.escrituras if s.lower().startswith("insert into fabrica_lote_paso")]


def _sellos(conn_admin, estado):
    return [p for s, p in conn_admin.escrituras if s.lower().startswith("update fabrica_lote_paso") and f"'{estado}'" in s.lower()]


def test_go_exige_esperado_huella_y_literal(monkeypatch, capsys):
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(": ")[1]
    for argv, patron in (
        (["fabrica_campanas.py", *ARGS_BASE, "--acepto-mutacion-real"], "esperado"),
        (_args_go(huella, esperado=4), "esperado 4"),
        (_args_go("deadbeef"), "huella"),
        (_args_go(huella, go=""), "go"),
    ):
        conn_admin = _ConnFalsa()
        _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, _Amazon())
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(fc.Abortar, match=patron):
            fc.main()
        assert conn_admin.escrituras == [], "ningun guard debe escribir el ledger"


def test_mutacion_orden_fijo_ledger_pre_http_y_readback(monkeypatch, capsys):
    """exact -> phrase -> broad -> product -> auto; por campana: campaign,
    ad group, product ad, semillas; cada POST tiene su fila planeado ANTES
    (commit) y su sello applied DESPUES del readback. r3 codex 6: la lista
    `secuencia` compartida entre _ConnFalsa (sql) y _Amazon (http) AFIRMA el
    orden — contar inserts/sellos al final no discriminaba un POST antes del
    INSERT del paso."""
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(": ")[1]
    secuencia = []
    conn_admin = _ConnFalsa(secuencia=secuencia)
    amazon = _Amazon(secuencia=secuencia)
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    assert fc.main() == 0
    campanas = [json.loads(p.content)["campaigns"][0]["name"] for p in amazon.posts("/sp/campaigns")]
    assert [n.split(" | ")[2] for n in campanas] == list(fp.ROLES_ORDEN_CREACION)
    assert len(amazon.posts("/sp/adGroups")) == 5 and len(amazon.posts("/sp/productAds")) == 5
    # semillas: exact 1 (collar noche cumple harvest), phrase/broad 2 (led + noche), product 1 ASIN, auto 1 negativo
    assert len(amazon.posts("/sp/keywords")) == 1 + 2 + 2
    assert len(amazon.posts("/sp/targets")) == 1 and len(amazon.posts("/sp/negativeKeywords")) == 1
    kw_exact = json.loads(amazon.posts("/sp/keywords")[0].content)["keywords"][0]
    assert kw_exact["matchType"] == "EXACT" and kw_exact["campaignId"] == "campaigns-1" and kw_exact["adGroupId"] == "adGroups-1"
    pa = json.loads(amazon.posts("/sp/productAds")[0].content)["productAds"][0]
    assert pa == {"campaignId": "campaigns-1", "adGroupId": "adGroups-1", "sku": "SS-1", "state": "ENABLED"}
    total_posts = 5 + 5 + 5 + 5 + 1 + 1
    assert len(_pasos(conn_admin)) == total_posts and len(_sellos(conn_admin, "applied")) == total_posts
    lote = [p for s, p in conn_admin.escrituras if s.lower().startswith("insert into fabrica_lote ")]
    assert len(lote) == 1 and lote[0][4] == "go" and lote[0][5] == huella
    # el lote y cada paso commitean ANTES de su POST: mas commits que POSTs
    assert conn_admin.commits >= 1 + 2 * total_posts
    # orden global (r3 codex 6): el INSERT del lote precede a TODO POST de
    # creacion y el primer INSERT de paso precede al primer POST /sp/campaigns
    i_lote = next(
        i for i, (t, x) in enumerate(secuencia)
        if t == "sql" and x.lower().startswith("insert into fabrica_lote ")
    )
    i_paso = next(
        i for i, (t, x) in enumerate(secuencia)
        if t == "sql" and x.lower().startswith("insert into fabrica_lote_paso")
    )
    i_campana = next(i for i, (t, x) in enumerate(secuencia) if (t, x) == ("http", "/sp/campaigns"))
    posts_creacion = [
        i for i, (t, x) in enumerate(secuencia)
        if t == "http" and x != "lwa" and not x.endswith("/list")
    ]
    assert posts_creacion, "no hubo POSTs de creacion"
    assert all(i_lote < i for i in posts_creacion), "un POST corrio antes del INSERT del lote"
    assert i_paso < i_campana, "el POST /sp/campaigns corrio antes del INSERT del paso"
    eventos = _eventos(capsys)
    assert eventos[-1]["evento"] == "reconciliacion_final" and eventos[-1]["ok"] is True


def test_rechazo_sella_failed_y_detiene_declarando_lo_creado(monkeypatch, capsys):
    """El 3er POST a /sp/campaigns (broad) responde 400: exact y phrase quedan
    applied, el paso broad failed, NINGUN POST posterior, el lote failed con
    detalle y Abortar."""
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(": ")[1]
    conn_admin = _ConnFalsa()
    amazon = _Amazon(fallar_en=("/sp/campaigns", 3))
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(fc.Abortar, match="category_broad"):
        fc.main()
    assert len(amazon.posts("/sp/campaigns")) == 3 and len(amazon.posts("/sp/adGroups")) == 2
    assert len(_sellos(conn_admin, "failed")) == 1
    lote_failed = [p for s, p in conn_admin.escrituras if s.lower().startswith("update fabrica_lote ") and p[0] == "failed"]
    assert len(lote_failed) == 1
    detenido = [e for e in _eventos(capsys) if e["evento"] == "lote_detenido"][0]
    assert detenido["creadas"] == ["category_exact", "category_phrase"]


def test_readback_que_no_cuadra_detiene(monkeypatch, capsys):
    """La campana exact nace pero el LIST la trae PAUSED: failed + detencion
    (fail-closed: solo se sella lo verificado)."""
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(": ")[1]
    conn_admin = _ConnFalsa()
    amazon = _Amazon(readback_malo="campaigns-1")
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(fc.Abortar, match="readback"):
        fc.main()
    assert len(amazon.posts("/sp/campaigns")) == 1 and amazon.posts("/sp/adGroups") == []
    assert len(_sellos(conn_admin, "failed")) == 1


def test_sin_perfil_aceptado_no_muta(monkeypatch, capsys):
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(": ")[1]
    conn_admin = _ConnFalsa()
    amazon = _Amazon()
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, amazon, perfiles=[_perfil("amazon_us", 102)])
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(fc.Abortar, match="perfil"):
        fc.main()
    assert amazon.posts("/sp/campaigns") == [] and conn_admin.escrituras == []


def test_readback_cuadra_exige_expresion_y_bid():
    """El readback verifica TAMBIEN la expresion del target (un ASIN distinto
    no puede pasar) y TODO monto pedido (bid/defaultBid/budget.budget;
    patron _bid_readback_cuadra de archiva_inertes: quantize 2, HALF_EVEN,
    via str); monto ausente en el LIST cuando el payload lo pedia = no cuadra
    (fail-closed)."""
    payload_t = {
        "expressionType": "MANUAL",
        "expression": [{"type": "ASIN_SAME_AS", "value": "B0X"}],
        "state": "ENABLED",
        "bid": 5.0,
    }
    exp = [{"type": "ASIN_SAME_AS", "value": "B0X"}]
    base = {"state": "ENABLED", "expression": exp, "bid": 5.0}
    assert fc._readback_cuadra(base, payload_t)
    assert not fc._readback_cuadra(
        {**base, "expression": [{"type": "ASIN_SAME_AS", "value": "B0OTRO"}]}, payload_t
    )
    assert not fc._readback_cuadra({**base, "expression": []}, payload_t)
    assert not fc._readback_cuadra({**base, "bid": 4.0}, payload_t)
    assert not fc._readback_cuadra({k: v for k, v in base.items() if k != "bid"}, payload_t)
    assert fc._readback_cuadra({**base, "bid": "5.00"}, payload_t)  # otra escala, mismo monto
    payload_kw = {"keywordText": "k", "matchType": "PHRASE", "state": "ENABLED", "bid": 6.0}
    leido_kw = {"keywordText": "k", "matchType": "PHRASE", "state": "ENABLED", "bid": 6.0}
    assert fc._readback_cuadra(leido_kw, payload_kw)
    assert not fc._readback_cuadra({**leido_kw, "matchType": "EXACT"}, payload_kw)
    # r2 glm 4: defaultBid del ad group y budget.budget de la campana tambien se exigen
    payload_ag = {"name": "g", "state": "ENABLED", "defaultBid": 4.5}
    leido_ag = {"name": "g", "state": "ENABLED", "defaultBid": 4.5}
    assert fc._readback_cuadra(leido_ag, payload_ag)
    assert not fc._readback_cuadra({**leido_ag, "defaultBid": 4.0}, payload_ag)
    assert not fc._readback_cuadra({"name": "g", "state": "ENABLED"}, payload_ag)
    payload_c = {"name": "c", "state": "ENABLED", "budget": {"budgetType": "DAILY", "budget": 150.0}}
    leido_c = {"name": "c", "state": "ENABLED", "budget": {"budgetType": "DAILY", "budget": 150.0}}
    assert fc._readback_cuadra(leido_c, payload_c)
    assert not fc._readback_cuadra(
        {**leido_c, "budget": {"budgetType": "DAILY", "budget": 100.0}}, payload_c
    )


def test_fallo_de_registro_sella_failed_con_rollback_previo(monkeypatch, capsys):
    """_registrar revienta (sync caido, entidad ausente, GoalInvalido): las 5
    quedan creadas en Amazon; rollback ANTES del sello (la excepcion pudo
    abortar la txn) y el lote queda failed con 'registro interno incompleto'
    — el reintento es --registrar <lote> (tarea 8)."""
    _huella_de(monkeypatch)
    huella = [x for x in capsys.readouterr().out.splitlines() if x.startswith("huella")][0].split(": ")[1]
    conn_admin = _ConnFalsa()
    _frontera_mutacion(monkeypatch, _conn_plan(), conn_admin, _Amazon())

    def _boom(ctx, plan, creadas):
        raise RuntimeError("sync caido")

    monkeypatch.setattr(fc, "_registrar", _boom)
    monkeypatch.setattr(sys, "argv", _args_go(huella))
    with pytest.raises(fc.Abortar, match="registro interno incompleto"):
        fc.main()
    sellos = [
        p for s, p in conn_admin.escrituras
        if s.lower().startswith("update fabrica_lote ") and p[0] == "failed"
    ]
    assert len(sellos) == 1 and "registro interno incompleto" in sellos[0][1]
    assert conn_admin.rollbacks >= 1, "rollback antes de sellar (la txn pudo quedar abortada)"
```

- [ ] **Step 2: Rojo**

Run: `pytest tests/test_fabrica_campanas.py -k "go_exige or mutacion or rechazo or readback or perfil" -v`
Expected: FAIL (`Abortar: mutacion real: pendiente de la tarea 7`).

- [ ] **Step 3: Implementar `_mutar` y sus piezas (reemplaza el stub)**

```python
# tools/fabrica_campanas.py — bloque de mutacion (tarea 7)
from dataclasses import dataclass  # arriba, con los imports

_SQL_INSERTA_LOTE = """
INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal, huella,
                          plan, modo_goal, estado)
VALUES (%s, %s::platform, %s, %s, %s, %s, %s::jsonb, %s, 'planeado')
"""
_SQL_SELLA_LOTE = """
UPDATE fabrica_lote SET estado = %s, detalle = %s, finished_at = now() WHERE lote = %s
"""
_SQL_INSERTA_PASO = """
INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload, estado)
VALUES (%s, %s, %s::campana_rol, %s, %s::jsonb, 'planeado')
RETURNING id
"""
_SQL_SELLA_PASO_APPLIED = """
UPDATE fabrica_lote_paso
   SET estado = 'applied', external_id = %s, ack = %s::jsonb, readback_estado = %s
 WHERE id = %s
"""
_SQL_SELLA_PASO_FAILED = """
UPDATE fabrica_lote_paso
   SET estado = 'failed', external_id = %s, ack = %s::jsonb, readback_estado = %s
 WHERE id = %s
"""


@dataclass
class _Ctx:
    http: Any
    token: str
    cred: AdsCredentials
    cliente_lectura: Any
    profile: int
    conn_admin: psycopg.Connection
    lote: str
    orden: int = 0


def _token_lwa(cred: AdsCredentials, client) -> str:
    resp = client.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": cred.refresh_token,
            "client_id": cred.client_id,
            "client_secret": cred.client_secret,
        },
    )
    if resp.status_code != 200:
        raise Abortar(f"LWA {resp.status_code}: {scrub(resp.text[:300])}")
    token = resp.json()["access_token"]
    register_secret(token)
    return token


def _perfiles(cliente_lectura) -> dict[str, int]:
    return {
        p.platform: p.profile_id
        for p in evaluar_perfiles(cliente_lectura)
        if p.aceptado and p.platform and p.profile_id is not None
    }


def _post(client, token: str, cred: AdsCredentials, profile: int, path: str, payload: dict) -> dict:
    """POST de creacion: vendor v3 EXACTO del path en Content-Type Y Accept,
    objeto envuelto como unica entrada de su lista (sellos probe 2.5)."""
    vendor = fp.VENDOR_POR_PATH[path]
    resp = client.post(
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Amazon-Advertising-API-ClientId": cred.client_id,
            "Amazon-Advertising-API-Scope": str(profile),
            "Content-Type": vendor,
            "Accept": vendor,
        },
        json={fp.ENVOLTURA_POR_PATH[path]: [payload]},
    )
    cuerpo: dict = {}
    with contextlib.suppress(ValueError):
        cuerpo = resp.json()
    return {"status": resp.status_code, "cuerpo": cuerpo, "texto": scrub(resp.text[:400])}


def _readback(cliente_lectura, profile: int, path_create: str, external: str) -> dict | None:
    """Objeto vivo por POST /sp/<recurso>/list con filtro de id. None = la
    API no respondio o no lo trae (fail-closed: no se sella)."""
    path_list = fp.LIST_POR_PATH[path_create]
    clave = fp.CLAVE_ID_POR_PATH[path_create]
    try:
        resp = cliente_lectura.list_objects(
            path_list, {fp.FILTRO_ID_POR_LIST[path_list]: {"include": [str(external)]}},
            profile_id=profile,
        )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    for fila in resp.json().get(fp.CONTENEDOR_POR_LIST[path_list]) or []:
        if str(fila.get(clave)) == str(external):
            return fila
    return None


def _expresion_normalizada(exp: Any) -> tuple:
    """(type, value) de cada clausula, ordenado: el ASIN pedido es parte del
    objeto creado; un target con OTRO ASIN no puede pasar el readback."""
    if not isinstance(exp, list):
        return ()
    return tuple(
        sorted((str(e.get("type")), str(e.get("value"))) for e in exp if isinstance(e, dict))
    )


def _monto_wire_cuadra(crudo_leido: Any, crudo_pedido: Any) -> bool:
    """Monto del LIST contra el pedido (patron _bid_readback_cuadra de
    archiva_inertes: cuantizado a 2, HALF_EVEN, via str — el wire viaja
    cuantizado por monto_wire). Ausente o ilegible = NO cuadra (fail-closed;
    la sonda de la tarea 11 confirma que el LIST devuelve los montos con la
    misma escala)."""
    if crudo_leido is None:
        return False
    try:
        pedido = Decimal(str(crudo_pedido)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        leido = Decimal(str(crudo_leido)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError):
        return False
    return leido == pedido


def _readback_cuadra(leido: dict | None, payload: dict) -> bool:
    """Vivo, ENABLED y con el texto/match/sku/nombre/targeting que se pidio;
    en targets tambien la expresion (el ASIN), y TODO monto pedido: `bid`,
    `defaultBid` del ad group y `budget.budget` de la campana (r2 glm 4: un
    objeto creado con otro monto no puede sellarse applied)."""
    if not isinstance(leido, dict) or leido.get("state") != fp.ESTADO_NUEVO:
        return False
    for clave in ("keywordText", "matchType", "sku", "name", "targetingType"):
        if clave in payload and str(leido.get(clave)) != str(payload[clave]):
            return False
    if "expression" in payload and (
        _expresion_normalizada(leido.get("expression"))
        != _expresion_normalizada(payload["expression"])
    ):
        return False
    for clave in ("bid", "defaultBid"):
        if clave in payload and not _monto_wire_cuadra(leido.get(clave), payload[clave]):
            return False
    pedido_budget = payload.get("budget")
    if isinstance(pedido_budget, dict) and "budget" in pedido_budget:
        leido_budget = leido.get("budget")
        if not isinstance(leido_budget, dict) or not _monto_wire_cuadra(
            leido_budget.get("budget"), pedido_budget["budget"]
        ):
            return False
    return True


def _inserta_lote(conn, lote: str, plan: fp.PlanGrupo, huella: str, go: str) -> None:
    conn.execute(
        _SQL_INSERTA_LOTE,
        (lote, plan.platform, plan.tipo_producto, plan.nombre_base, go, huella,
         json.dumps(fp.plan_como_json(plan)), plan.modo),
    )
    conn.commit()


def _sella_lote(conn, lote: str, estado: str, detalle: str | None) -> None:
    conn.execute(_SQL_SELLA_LOTE, (estado, detalle, lote))
    conn.commit()


def _inserta_paso(ctx: _Ctx, paso: fp.Paso, payload: dict) -> int:
    ctx.orden += 1
    fila = ctx.conn_admin.execute(
        _SQL_INSERTA_PASO, (ctx.lote, ctx.orden, paso.rol, paso.recurso, json.dumps(payload))
    ).fetchone()
    ctx.conn_admin.commit()
    return fila[0]


def _sella_paso(ctx: _Ctx, paso_id: int, ok: bool, external, ack: dict, readback) -> None:
    sql = _SQL_SELLA_PASO_APPLIED if ok else _SQL_SELLA_PASO_FAILED
    ctx.conn_admin.execute(sql, (external, json.dumps(ack, default=str), readback, paso_id))
    ctx.conn_admin.commit()


def _ejecuta_paso(ctx: _Ctx, paso: fp.Paso, padres: dict) -> str:
    """Fila planeado + commit -> POST -> id del ack -> readback -> sello.
    Devuelve el external creado o levanta Abortar con el paso failed."""
    payload = {**padres, **paso.payload}
    paso_id = _inserta_paso(ctx, paso, payload)
    try:
        ack = _post(ctx.http, ctx.token, ctx.cred, ctx.profile, paso.path, payload)
    except Exception as exc:  # red caida a mitad: la intencion ya es durable
        ack = {"status": "excepcion", "cuerpo": {}, "texto": scrub(str(exc))[:300]}
    if not fp.ack_ok(ack):
        _sella_paso(ctx, paso_id, False, None, ack, None)
        raise Abortar(f"{paso.descripcion} ({paso.rol}) rechazado (status {ack.get('status')})")
    external = fp.id_creado(ack, fp.CLAVE_ID_POR_PATH[paso.path])
    if external is None:
        _sella_paso(ctx, paso_id, False, None, ack, None)
        raise Abortar(f"{paso.descripcion} ({paso.rol}): el ack no trae {fp.CLAVE_ID_POR_PATH[paso.path]}")
    time.sleep(0.3)  # cortesia de rate limit
    leido = _readback(ctx.cliente_lectura, ctx.profile, paso.path, external)
    readback = leido.get("state") if isinstance(leido, dict) else None
    cuadra = _readback_cuadra(leido, payload)
    _log("paso", lote=ctx.lote, rol=paso.rol, recurso=paso.recurso, external=external,
         ack=ack["cuerpo"], readback=readback, ok=cuadra)
    _sella_paso(ctx, paso_id, cuadra, external, ack, readback)
    if not cuadra:
        raise Abortar(f"readback de {paso.descripcion} ({paso.rol}) no cuadra ({readback})")
    return external


def _ejecuta_rol(ctx: _Ctx, plan: fp.PlanGrupo, rol: str) -> dict:
    """campana -> ad group -> product ads -> semillas, con los externos de
    los padres inyectados en cada payload hijo."""
    pasos = fp.pasos_del_rol(plan, rol)
    externos: dict = {"rol": rol, "product_ads": [], "semillas": []}
    externos["campaign"] = _ejecuta_paso(ctx, pasos[0], {})
    padres = {"campaignId": externos["campaign"]}
    externos["ad_group"] = _ejecuta_paso(ctx, pasos[1], padres)
    padres["adGroupId"] = externos["ad_group"]
    for paso in pasos[2:]:
        ext = _ejecuta_paso(ctx, paso, padres)
        (externos["product_ads"] if paso.recurso == "product_ad" else externos["semillas"]).append(
            {"recurso": paso.recurso, "external": ext}
        )
    return externos


def _valida_go(args, huella: str) -> None:
    if args.esperado is None:
        raise Abortar("mutacion real exige --esperado 5 (las 5 campanas del grupo)")
    if args.esperado != _ESPERADO_ROLES:
        raise Abortar(f"--esperado {args.esperado} != {_ESPERADO_ROLES} campanas del grupo")
    if not args.huella:
        raise Abortar("mutacion real exige --huella del dry-run (autorizacion por conjunto)")
    if args.huella != huella:
        raise Abortar(f"--huella {args.huella} != huella del plan {huella}: el plan cambio, se re-autoriza")
    if not args.go or not args.go.strip():
        raise Abortar("mutacion real exige --go con el literal del dueno (no vacio)")


def _mutar(args, plan: fp.PlanGrupo, huella: str) -> int:
    _valida_go(args, huella)
    cred = AdsCredentials.from_secrets_dir()
    cliente_lectura = AdsClient(cred)
    perfiles = _perfiles(cliente_lectura)
    _log("perfiles", perfiles=perfiles)
    if plan.platform not in perfiles:
        raise Abortar(f"sin perfil aceptado para {plan.platform}: no se muta")
    conn_admin = connect(_dsn_admin())
    http = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0))
    # el token NO muta: va ANTES del lote (si LWA falla, no queda lote huerfano)
    token = _token_lwa(cred, http)
    lote = _lote_nuevo(plan)
    # intencion durable ANTES del primer POST de MUTACION (no antes del token)
    _inserta_lote(conn_admin, lote, plan, huella, args.go)
    ctx = _Ctx(http, token, cred, cliente_lectura, perfiles[plan.platform], conn_admin, lote)
    creadas: list[dict] = []
    try:
        for rol in fp.ROLES_ORDEN_CREACION:
            creadas.append(_ejecuta_rol(ctx, plan, rol))
            _log("campana_creada", lote=lote, rol=rol, external=creadas[-1]["campaign"])
    except Abortar as exc:
        detalle = f"{exc} | creadas: {[c['rol'] for c in creadas]}"
        _sella_lote(conn_admin, lote, "failed", detalle)
        _log("lote_detenido", lote=lote, motivo=str(exc), creadas=[c["rol"] for c in creadas],
             nota="las creadas quedan ENABLED en Amazon; --desarmar <lote> las pausa")
        raise
    try:
        _registrar(ctx, plan, creadas)  # tarea 8: sync + campana_grupo + goals
    except Exception as exc:
        # La excepcion pudo abortar la txn de conn_admin: SIN rollback el
        # sello reventaria y el lote quedaria planeado para siempre.
        conn_admin.rollback()
        _sella_lote(
            conn_admin, lote, "failed",
            f"campanas creadas en Amazon, registro interno incompleto: {exc}",
        )
        _log("lote_detenido", lote=lote, motivo=f"registro: {exc}",
             creadas=[c["rol"] for c in creadas],
             nota="--registrar <lote> reintenta SOLO el registro (no toca Amazon)")
        raise Abortar(f"registro interno incompleto: {exc}") from exc
    _sella_lote(conn_admin, lote, "applied", None)
    _log("reconciliacion_final", lote=lote, campanas=[c["campaign"] for c in creadas], ok=True)
    return 0


def _registrar(ctx: _Ctx, plan: fp.PlanGrupo, creadas: list[dict]) -> None:  # tarea 8
    _log("registro_pendiente", lote=ctx.lote, nota="tarea 8 del plan")
```

- [ ] **Step 4: Verde + ruff (quitar los `noqa: F401` de la tarea 6 que ya no aplican)**

Run: `pytest tests/test_fabrica_campanas.py -v && ruff check tools/fabrica_campanas.py && ruff format --check tools/fabrica_campanas.py`
Expected: PASS. Si `_ejecuta_paso` pasa PLR0915/C901, extraer `_falla_paso(ctx, paso_id, ack, motivo)` que sella y levanta.

- [ ] **Step 5: Commit**

```bash
git checkout -b fabrica-01-7-mutacion origin/master
git add tools/fabrica_campanas.py tests/test_fabrica_campanas.py
git commit -m "feat(fabrica): mutacion real — ledger pre-HTTP, orden fijo, readback y detencion (spec §5.3)"
```

Nota de regla 7 para el PR: este PR NO se mergea solo — se mergea junto con el de la tarea 9 (`--desarmar`) o después de él; mientras tanto el `--acepto-mutacion-real` no se corre en producción (prohibido para implementadores; el lead decide en la tarea 11).

---

### Task 8: sync de estructura + registro interno (grupo, roles, productos, goals)

**Files:**
- Modify: `tools/fabrica_campanas.py` (reemplazar el stub `_registrar`)
- Modify: `tests/test_fabrica_campanas.py`

**Interfaces:**
- Consumes: `fetch_structure(AdsClient)`, `sync_structure(conn_ingest, estructura)` (`app/ads/structure.py`, rol `app_ingest`), `goals_write.crea_goal` (tarea 4), tablas `campana_grupo`, `campana_grupo_rol`, `campana_grupo_producto`, `ad_entity`.
- Produces: `_registrar(ctx, plan, creadas) -> int` (grupo_id, IDEMPOTENTE: re-correrlo sobre el mismo lote no duplica ni revienta), `_sync(cliente_lectura)`, `_id_entidad(conn, platform, kind, external) -> int`, `_SQL_INSERTA_GRUPO` (ON CONFLICT por lote), `_SQL_INSERTA_ROL`/`_SQL_INSERTA_PRODUCTO` (ON CONFLICT DO NOTHING), `_SQL_GOAL_EXISTENTE`, `_SQL_ID_ENTIDAD`, `_SQL_CAMPANAS_APPLIED` (reintento `--registrar`), `_registrar_cmd(args) -> int`.

- [ ] **Step 1: Tests (Postgres real para el registro; fake para el orden sync→registro)**

```python
# agregar a tests/test_fabrica_campanas.py

_CREADAS = [
    {"rol": rol, "campaign": f"c-{rol}", "ad_group": f"ag-{rol}", "product_ads": [], "semillas": []}
    for rol in fp.ROLES_ORDEN_CREACION
]


def _plan_min():
    return fp.PlanGrupo(
        platform="amazon_mx", tipo_producto="collar_perro", nombre_base="Collar", fecha=HOY,
        moneda="MXN", modo="shadow",
        productos=(fp.ProductoGrupo(1, "ODOO-1", 11, "B0AAAAAAAA", "SS-1", Decimal("38.20")),),
        parametros={rol: fp.ParametrosRol(rol, Decimal("120"), Decimal("6.00")) for rol in fp.ROLES_ORDEN_CREACION},
        target=fp.target_del_grupo([Decimal("38.20")], Decimal("0.5")),
        semillas=fp.Semillas((), (), (), ()),
    )


@_skip_db
def test_registrar_escribe_grupo_roles_productos_y_goals(monkeypatch):
    """Con ad_entity ya sincronizado (aqui sembrado a mano), _registrar deja
    campana_grupo (target congelado), 5 roles (campana + ad group), el
    producto y 5 goals via crea_goal con la terna apuntando a la exact."""
    with db_fabrica("orbit_fab_reg") as conn:
        pid, lid = _producto(conn)
        for c in _CREADAS:
            camp = _entidad(conn, "amazon_mx", "campaign", c["campaign"])
            _entidad(conn, "amazon_mx", "ad_group", c["ad_group"], parent=camp)
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal, huella,"
            " plan, modo_goal, estado) VALUES ('L1', 'amazon_mx', 'collar_perro', 'Collar', 'go', 'h',"
            " '{}', 'shadow', 'planeado')"
        )
        monkeypatch.setattr(fc, "_sync", lambda cliente: None)  # el sync real se prueba aparte
        plan = dataclasses.replace(
            _plan_min(),
            productos=(fp.ProductoGrupo(pid, "ODOO-1", lid, "B0AAAAAAAA", "SS-1", Decimal("38.20")),),
        )
        ctx = fc._Ctx(None, "tok", None, None, 101, conn, "L1")
        grupo = fc._registrar(ctx, plan, _CREADAS)
        conn.row_factory = tuple_row  # crea_goal deja dict_row en la conexion
        fila = conn.execute(
            "SELECT target_acos_pct, target_derivado_pct, fraccion, lote FROM campana_grupo WHERE id = %s", (grupo,)
        ).fetchone()
        assert fila == (Decimal("19.10"), Decimal("19.1000"), Decimal("0.5000"), "L1")
        roles = conn.execute(
            "SELECT r.rol, c.external_id, g.external_id FROM campana_grupo_rol r"
            " JOIN ad_entity c ON c.id = r.ad_entity_id JOIN ad_entity g ON g.id = r.ad_group_ad_entity_id"
            " WHERE r.grupo_id = %s ORDER BY r.rol", (grupo,),
        ).fetchall()
        assert len(roles) == 5 and ("category_exact", "c-category_exact", "ag-category_exact") in roles
        assert conn.execute("SELECT seller_sku, margen_neto_pct FROM campana_grupo_producto WHERE grupo_id = %s", (grupo,)).fetchone() == ("SS-1", Decimal("38.2000"))
        goals = conn.execute(
            "SELECT g.mode, g.enabled, g.target_acos_pct, g.harvest_campaign_id, g.harvest_ad_group_id,"
            " g.harvest_default_bid, g.bid_floor FROM ads_optimizer_goal g"
            " JOIN campana_grupo_rol r ON r.ad_entity_id = g.ad_entity_id WHERE r.grupo_id = %s", (grupo,),
        ).fetchall()
        assert len(goals) == 5
        assert all(g == ("shadow", True, Decimal("19.10"), "c-category_exact", "ag-category_exact", Decimal("6.0000"), Decimal("1.0000")) for g in goals)


@_skip_db
def test_registrar_aborta_si_el_sync_no_trajo_la_campana(monkeypatch):
    with db_fabrica("orbit_fab_reg2") as conn:
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal, huella,"
            " plan, modo_goal, estado) VALUES ('L1', 'amazon_mx', 'collar_perro', 'Collar', 'go', 'h',"
            " '{}', 'shadow', 'planeado')"
        )
        monkeypatch.setattr(fc, "_sync", lambda cliente: None)  # el sync corrio pero no trajo nada
        ctx = fc._Ctx(None, "tok", None, None, 101, conn, "L1")
        with pytest.raises(fc.Abortar, match="ad_entity"):
            fc._registrar(ctx, _plan_min(), _CREADAS)
        assert conn.execute("SELECT count(*) FROM campana_grupo").fetchone()[0] == 0


def test_registrar_sincroniza_antes_de_escribir(monkeypatch):
    """Orden sellado: _sync corre ANTES del primer INSERT de registro (las FKs
    a ad_entity no existen hasta el sync)."""
    llamadas = []
    conn = _ConnFalsa()
    monkeypatch.setattr(fc, "_sync", lambda cliente: llamadas.append("sync"))
    monkeypatch.setattr(fc, "_id_entidad", lambda c, p, k, e: llamadas.append(f"id:{k}:{e}") or 7)
    monkeypatch.setattr(fc.goals_write, "crea_goal", lambda c, **kw: llamadas.append("goal") or {"id": 1})
    ctx = fc._Ctx(None, "tok", None, "cliente", 101, conn, "L1")
    fc._registrar(ctx, _plan_min(), _CREADAS)
    assert llamadas[0] == "sync" and llamadas.count("goal") == 5
    assert any(s.lower().startswith("insert into campana_grupo ") for s, _ in conn.escrituras)


def test_sync_usa_dsn_ingest_y_el_escritor_unico(monkeypatch):
    llamadas = {}
    monkeypatch.setenv("ORBIT_DSN_INGEST", "dsn-ingest")
    monkeypatch.setattr(fc, "connect", lambda dsn: llamadas.setdefault("dsn", dsn) and "conn-ingest")
    monkeypatch.setattr(fc, "fetch_structure", lambda cliente: "estructura")
    monkeypatch.setattr(fc, "sync_structure", lambda conn, est: llamadas.setdefault("sync", (conn, est)))
    fc._sync("cliente")
    assert llamadas == {"dsn": "dsn-ingest", "sync": ("conn-ingest", "estructura")}
```

- [ ] **Step 2: Rojo**

Run: `pytest tests/test_fabrica_campanas.py -k "registrar or sync_usa" -v`
Expected: FAIL (`AttributeError: module 'fabrica_campanas' has no attribute '_sync'` y el registro no escribe nada).

- [ ] **Step 3: Implementar (reemplaza el stub `_registrar`)**

```python
# tools/fabrica_campanas.py — registro (tarea 8)
# IDEMPOTENCIA (el registro corre DESPUES de crear en Amazon: re-correr
# --registrar sobre el mismo lote no puede reventar con UniqueViolation):
# grupo con ON CONFLICT (lote) no-op que devuelve el id existente SIN pisar
# target/go_literal; roles y productos con ON CONFLICT DO NOTHING; goals con
# SELECT previo (goal_ya_existe, no se pisa).
_SQL_ID_ENTIDAD = """
SELECT id FROM ad_entity WHERE platform = %s::platform AND kind = %s AND external_id = %s
"""
_SQL_INSERTA_GRUPO = """
INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote, target_acos_pct,
                           target_derivado_pct, fraccion, target_procedencia, go_literal)
SELECT %s::platform, %s, %s, %s, %s, %s, %s, %s, go_literal
  FROM fabrica_lote WHERE lote = %s
ON CONFLICT (lote) DO UPDATE SET lote = EXCLUDED.lote
RETURNING id
"""
_SQL_INSERTA_ROL = """
INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)
VALUES (%s, %s::campana_rol, %s, %s)
ON CONFLICT DO NOTHING
"""
_SQL_INSERTA_PRODUCTO = """
INSERT INTO campana_grupo_producto (grupo_id, product_id, listing_id, seller_sku, margen_neto_pct)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""
_SQL_GOAL_EXISTENTE = """
SELECT id FROM ads_optimizer_goal WHERE ad_entity_id = %s AND scope = 'campaign'
"""


def _sync(cliente_lectura) -> None:
    """ad_entity/ad_entity_state de las campanas nuevas las escribe SOLO el
    sync de estructura (escritor unico, rol app_ingest)."""
    conn_ingest = connect(_dsn_ingest())
    try:
        sync_structure(conn_ingest, fetch_structure(cliente_lectura))
    finally:
        with contextlib.suppress(Exception):
            conn_ingest.close()


def _id_entidad(conn, platform: str, kind: str, external: str) -> int:
    fila = conn.execute(_SQL_ID_ENTIDAD, (platform, kind, external)).fetchone()
    if fila is None:
        raise Abortar(f"{kind} {external} no esta en ad_entity tras el sync: registro detenido")
    return fila[0]


def _registrar(ctx: _Ctx, plan: fp.PlanGrupo, creadas: list[dict]) -> int:
    """sync -> campana_grupo -> roles -> productos -> 5 goals (crea_goal).
    Todo en la conexion admin; commit al final de cada bloque para que un
    fallo deje rastro legible (el lote queda failed con el motivo).
    IDEMPOTENTE: re-correr sobre el mismo lote devuelve el grupo existente,
    no duplica roles/productos y no pisa goals ya creados (goal_ya_existe)."""
    _sync(ctx.cliente_lectura)
    conn = ctx.conn_admin
    exact = next(c for c in creadas if c["rol"] == "category_exact")
    ids = {
        c["rol"]: (
            _id_entidad(conn, plan.platform, "campaign", c["campaign"]),
            _id_entidad(conn, plan.platform, "ad_group", c["ad_group"]),
        )
        for c in creadas
    }
    grupo = conn.execute(
        _SQL_INSERTA_GRUPO,
        (plan.platform, plan.tipo_producto, plan.nombre_base, ctx.lote, plan.target.aplicado,
         plan.target.derivado, plan.target.fraccion, plan.target.procedencia, ctx.lote),
    ).fetchone()[0]
    for rol, (camp_id, ag_id) in ids.items():
        conn.execute(_SQL_INSERTA_ROL, (grupo, rol, camp_id, ag_id))
    for p in plan.productos:
        conn.execute(_SQL_INSERTA_PRODUCTO, (grupo, p.product_id, p.listing_id, p.seller_sku, p.margen_neto_pct))
    conn.commit()
    ahora = datetime.datetime.now(datetime.UTC)
    bid_exact = plan.parametros["category_exact"].bid
    for rol, (camp_id, _ag_id) in ids.items():
        previo = conn.execute(_SQL_GOAL_EXISTENTE, (camp_id,)).fetchone()
        if previo is not None:
            # crea_goal deja row_factory=dict_row en la conexion: la fila
            # puede ser tupla (primera pasada) o dict (reintento parcial)
            goal_previo = previo["id"] if isinstance(previo, dict) else previo[0]
            _log("goal_ya_existe", lote=ctx.lote, rol=rol, goal_id=goal_previo)
            continue
        goal = goals_write.crea_goal(
            conn,
            ad_entity_id=camp_id,
            target_acos_pct=plan.target.aplicado,
            bid_currency=plan.moneda,
            mode=plan.modo,
            harvest_campaign_id=exact["campaign"],
            harvest_ad_group_id=exact["ad_group"],
            harvest_default_bid=bid_exact,
            created_at=ahora,
        )
        _log("goal_creado", lote=ctx.lote, rol=rol, goal_id=goal["id"], mode=plan.modo)
    _log("grupo_registrado", lote=ctx.lote, grupo_id=grupo, target=str(plan.target.aplicado))
    return grupo
```

La forma final del `try` de `_registrar` en `_mutar` (atrapa Exception, `conn_admin.rollback()` ANTES de `_sella_lote(..., "failed", "campanas creadas en Amazon, registro interno incompleto: <motivo>")` y sube Abortar) **ya quedó escrita en la tarea 7** — esta tarea no la toca, solo completa `_registrar`. Para reintentar SOLO el registro existe `--registrar <lote>` (no toca Amazon; solo el LIST del sync):

```python
# tools/fabrica_campanas.py — reintento del registro (tarea 8)
_SQL_CAMPANAS_APPLIED = """
SELECT rol::text, recurso, external_id
  FROM fabrica_lote_paso
 WHERE lote = %s AND estado = 'applied' AND recurso IN ('campaign', 'ad_group')
 ORDER BY orden
"""


def _registrar_cmd(args) -> int:
    """`--registrar <lote>`: sync + registro para un lote cuyas 5 campanas y
    ad groups YA estan applied en el ledger (el registro interno fallo
    despues del HTTP)."""
    conn_admin = connect(_dsn_admin())
    # r3 codex 5: crea_goal/edita_goal mutan la row_factory de la conexion a
    # dict_row; los SELECTs de este tool desestructuran tuplas -> la fijamos
    # defensivamente tras el connect (_desarmar/_reconciliar_cmd no la
    # necesitan: sus SELECTs corren antes de cualquier goals_write)
    conn_admin.row_factory = tuple_row
    fila = conn_admin.execute(
        "SELECT plan, estado FROM fabrica_lote WHERE lote = %s", (args.registrar,)
    ).fetchone()
    if fila is None:
        raise Abortar(f"lote {args.registrar} no existe")
    plan_json, estado = fila
    if estado == "applied":
        raise Abortar(f"lote {args.registrar} ya esta applied: nada que registrar")
    if estado == "desarmado":
        # r2 glm: registrar un desarmado crearia goals enabled=true sobre
        # campanas PAUSED y sellaria 'applied' un grupo pausado (fail-open).
        raise Abortar(
            f"lote {args.registrar} esta desarmado (campanas PAUSED): registrarlo "
            "lo resucitaria a medias; si se quiere vivo, es decision nueva del dueno"
        )
    pasos = conn_admin.execute(_SQL_CAMPANAS_APPLIED, (args.registrar,)).fetchall()
    conn_admin.commit()
    creadas: dict[str, dict] = {}
    for rol, recurso, external in pasos:
        creadas.setdefault(rol, {"rol": rol, "product_ads": [], "semillas": []})[recurso] = external
    faltan = [r for r in fp.ROLES_ORDEN_CREACION if "ad_group" not in creadas.get(r, {})]
    if faltan:
        raise Abortar(f"lote {args.registrar} sin campana+ad group applied para {faltan}: --reconciliar primero")
    plan = fp.plan_desde_json(plan_json)
    cred = AdsCredentials.from_secrets_dir()
    cliente_lectura = AdsClient(cred)
    perfiles = _perfiles(cliente_lectura)
    if plan.platform not in perfiles:
        raise Abortar(f"sin perfil aceptado para {plan.platform}")
    ctx = _Ctx(None, "", cred, cliente_lectura, perfiles[plan.platform], conn_admin, args.registrar)
    _registrar(ctx, plan, [creadas[r] for r in fp.ROLES_ORDEN_CREACION])
    _sella_lote(conn_admin, args.registrar, "applied", "registro reintentado")
    return 0
```

`_parser` gana `ap.add_argument("--registrar", default=None, help="lote failed cuyas campanas ya existen: reintenta solo sync + registro")` y `main` lo despacha con `if args.registrar is not None: return _registrar_cmd(args)` antes de `--desarmar`. El test del reintento:

```python
# agregar a tests/test_fabrica_campanas.py


def test_registrar_cmd_reintenta_solo_el_registro(monkeypatch):
    """`--registrar L1` con las 5 campanas y ad groups applied en el ledger:
    _sync corre ANTES del registro, el lote se sella applied y NO hay ningun
    POST a Amazon (el reintento no toca la API, solo el LIST del sync — aqui
    _sync esta monkeypatcheado)."""
    plan_json = fp.plan_como_json(_plan_min())
    pasos = [
        (rol, recurso, f"{prefijo}-{rol}")
        for rol in fp.ROLES_ORDEN_CREACION
        for recurso, prefijo in (("campaign", "c"), ("ad_group", "ag"))
    ]
    conn_admin = _ConnFalsa(lote_fila=(plan_json, "failed"), pendientes=pasos)
    amazon = _Amazon()
    llamadas = []
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(fc, "_sync", lambda cliente: llamadas.append("sync"))
    monkeypatch.setattr(fc, "_id_entidad", lambda c, p, k, e: llamadas.append(f"id:{k}:{e}") or 7)
    monkeypatch.setattr(fc.goals_write, "crea_goal", lambda c, **kw: llamadas.append("goal") or {"id": 1})
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--registrar", "L1"])
    assert fc.main() == 0
    assert llamadas[0] == "sync" and llamadas.count("goal") == 5
    sellos = [
        p for s, p in conn_admin.escrituras
        if s.lower().startswith("update fabrica_lote ") and p[0] == "applied"
    ]
    assert len(sellos) == 1
    assert amazon.pedidos == [], "el reintento del registro no abre HTTP"
```


```python
# agregar a tests/test_fabrica_campanas.py (Postgres real: los ON CONFLICT y
# el goal_ya_existe no los puede ejercitar _ConnFalsa)


@_skip_db
def test_registrar_cmd_doble_corrida_es_idempotente(monkeypatch):
    """`--registrar L1` DOS veces sobre el mismo lote (entre ambas, el lote
    vuelve a failed: simula el choque entre el registro y el sello). Sale 0
    las dos veces con el MISMO grupo (ON CONFLICT (lote) devuelve el id sin
    pisar target/go_literal), sin duplicar roles/productos (DO NOTHING) y sin
    pisar goals (goal_ya_existe)."""
    with db_fabrica("orbit_fab_reg3") as conn:
        pid, lid = _producto(conn)
        for c in _CREADAS:
            camp = _entidad(conn, "amazon_mx", "campaign", c["campaign"])
            _entidad(conn, "amazon_mx", "ad_group", c["ad_group"], parent=camp)
        plan = dataclasses.replace(
            _plan_min(),
            productos=(fp.ProductoGrupo(pid, "ODOO-1", lid, "B0AAAAAAAA", "SS-1",
                                        Decimal("38.20")),),
        )
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
            " huella, plan, modo_goal, estado) VALUES ('L1', 'amazon_mx', 'collar_perro',"
            " 'Collar', 'go', 'h', %s, 'shadow', 'failed')",
            (Json(fp.plan_como_json(plan)),),
        )
        orden = 0
        for c in _CREADAS:
            for recurso, ext in (("campaign", c["campaign"]), ("ad_group", c["ad_group"])):
                orden += 1
                conn.execute(
                    "INSERT INTO fabrica_lote_paso (lote, orden, rol, recurso, request_payload,"
                    " external_id, ack, readback_estado, estado) VALUES ('L1', %s,"
                    " %s::campana_rol, %s, '{}'::jsonb, %s, '{}'::jsonb, 'ENABLED', 'applied')",
                    (orden, c["rol"], recurso, ext),
                )
        monkeypatch.setattr(fc, "connect", lambda dsn: conn)
        monkeypatch.setenv("ORBIT_DSN_ADMIN", "postgresql://fake")  # _dsn_admin aborta sin ella
        monkeypatch.setattr(fc.AdsCredentials, "from_secrets_dir", classmethod(lambda cls: _CREDS))
        monkeypatch.setattr(fc, "AdsClient", lambda cred: None)
        monkeypatch.setattr(fc, "evaluar_perfiles", lambda c: [_perfil()])
        monkeypatch.setattr(fc, "_sync", lambda cliente: None)
        monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--registrar", "L1"])
        assert fc.main() == 0
        conn.execute("UPDATE fabrica_lote SET estado = 'failed' WHERE lote = 'L1'")
        # r3 codex 5: la PRIMERA corrida dejo conn.row_factory=dict_row (la
        # muta crea_goal); sin reset, los SELECTs de la segunda desestructuran
        # filas dict y el desempaquetado calla o revienta
        conn.row_factory = tuple_row
        assert fc.main() == 0  # segunda corrida: idempotente, no duplica
        conn.row_factory = tuple_row  # crea_goal deja dict_row en la conexion
        filas = conn.execute(
            "SELECT id, target_procedencia, go_literal FROM campana_grupo WHERE lote = 'L1'"
        ).fetchall()
        assert len(filas) == 1 and filas[0][2] == "go"
        grupo = filas[0][0]
        assert "margen_minimo_grupo" in filas[0][1]  # ON CONFLICT no piso la procedencia
        n_roles = conn.execute(
            "SELECT count(*) FROM campana_grupo_rol WHERE grupo_id = %s", (grupo,)
        ).fetchone()[0]
        n_prods = conn.execute(
            "SELECT count(*) FROM campana_grupo_producto WHERE grupo_id = %s", (grupo,)
        ).fetchone()[0]
        n_goals = conn.execute(
            "SELECT count(*) FROM ads_optimizer_goal g JOIN campana_grupo_rol r"
            " ON r.ad_entity_id = g.ad_entity_id WHERE r.grupo_id = %s",
            (grupo,),
        ).fetchone()[0]
        assert (n_roles, n_prods, n_goals) == (5, 1, 5)


def test_registrar_rechaza_lote_desarmado(monkeypatch):
    """r2 glm 3: `--registrar` sobre un lote 'desarmado' crearia goals
    enabled=true sobre campanas PAUSED y sellaria 'applied' un grupo pausado
    (fail-open). El guard lo rechaza antes de tocar nada."""
    conn_admin = _ConnFalsa(lote_fila=({"x": 1}, "desarmado"))
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, _Amazon())
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--registrar", "L9"])
    with pytest.raises(fc.Abortar, match="desarmado"):
        fc.main()
```

- [ ] **Step 4: Verde**

Run: `pytest tests/test_fabrica_campanas.py -v && ruff check tools/fabrica_campanas.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git checkout -b fabrica-01-8-registro origin/master
git add tools/fabrica_campanas.py tests/test_fabrica_campanas.py
git commit -m "feat(fabrica): sync de estructura y registro interno del grupo con goals por campana (spec §5.4)"
```

---

### Task 9: reversa `--desarmar` y `--reconciliar`

**Files:**
- Modify: `tools/fabrica_campanas.py` (reemplazar los stubs `_desarmar` y `_reconciliar_cmd`)
- Modify: `tests/test_fabrica_campanas.py`

**Interfaces:**
- Consumes: `fabrica_lote_paso` applied (externos de las campañas — durable desde ANTES del HTTP: cubre lotes muertos a medias, sin grupo), `campana_grupo_rol` + `ads_optimizer_goal` solo para enriquecer (goal_id), `goals_write.edita_goal(conn, goal_id, enabled=False, updated_at=...)`, PUT `/sp/campaigns` con vendor `spcampaign` (sello de `reactiva_campanas`: `{"campaigns": [{"campaignId": "<str>", "state": "PAUSED"}]}`), `fabrica_lote_paso` pendientes.
- Produces: `_desarmar(args) -> int`, `_put_estado_campana(ctx, external, estado) -> dict`, `_reconciliar_cmd(args) -> int`, `_reconciliar(conn_admin, cliente_lectura, perfiles, lote, plataforma) -> dict`, `_SQL_CAMPANAS_DEL_LOTE` (base `fabrica_lote_paso` con `recurso='campaign' AND estado IN ('applied','failed') AND external_id IS NOT NULL` — failed con external = creada en Amazon con readback fallido, se pausa; failed sin external = POST rechazado, no existe; r3 codex 1; LEFT JOIN a `campana_grupo`/`campana_grupo_rol`/`ads_optimizer_goal` solo para el `goal_id`), `_SQL_PENDIENTES` (JOIN a `fabrica_lote` por la plataforma del paso; filtro opcional `--plataforma`, r3 codex 4), `_SQL_PROMUEVE_APPLIED`.

- [ ] **Step 1: Tests (fallan: stubs)**

```python
# agregar a tests/test_fabrica_campanas.py


class _AmazonDesarme(_Amazon):
    def __init__(self, estado_readback="PAUSED"):
        super().__init__()
        self.puts = []
        self.estado_readback = estado_readback

    def __call__(self, request):
        if request.method == "PUT":
            self.puts.append(request)
            assert request.headers["Content-Type"] == fp.VENDOR_POR_PATH["/sp/campaigns"]
            cuerpo = json.loads(request.content)["campaigns"][0]
            assert cuerpo["state"] == "PAUSED" and isinstance(cuerpo["campaignId"], str)
            return httpx.Response(207, json={"campaigns": {"success": [{"index": 0, "campaignId": cuerpo["campaignId"]}], "error": []}})
        if str(request.url).endswith("/sp/campaigns/list"):
            ext = json.loads(request.content)["campaignIdFilter"]["include"][0]
            return httpx.Response(200, json={"campaigns": [{"campaignId": ext, "state": self.estado_readback}]})
        return super().__call__(request)


# Filas de _SQL_CAMPANAS_DEL_LOTE: (rol, campaign_external, goal_id)
_FILAS_LOTE = [(rol, f"c-{rol}", 50 + i) for i, rol in enumerate(fp.ROLES_ORDEN_CREACION)]


def test_desarmar_pausa_las_5_y_apaga_sus_goals(monkeypatch, capsys):
    conn_admin = _ConnFalsa(grupo=_FILAS_LOTE)
    amazon = _AmazonDesarme()
    apagados = []
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(fc.goals_write, "edita_goal", lambda c, gid, **kw: apagados.append((gid, kw["enabled"])) or {})
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--desarmar", "L1", "--acepto-mutacion-real", "--go", "pausa"])
    assert fc.main() == 0
    assert len(amazon.puts) == 5
    assert apagados == [(gid, False) for _, _, gid in _FILAS_LOTE]
    sellos = [p for s, p in conn_admin.escrituras if s.lower().startswith("update fabrica_lote ") and p[0] == "desarmado"]
    assert len(sellos) == 1
    assert _eventos(capsys)[-1]["evento"] == "reconciliacion_final"


def test_desarmar_sin_go_es_dry_run_y_readback_malo_detiene(monkeypatch, capsys):
    conn_admin = _ConnFalsa(grupo=_FILAS_LOTE)
    amazon = _AmazonDesarme()
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--desarmar", "L1"])
    assert fc.main() == 0 and amazon.puts == [] and conn_admin.escrituras == []
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--desarmar", "L1", "--acepto-mutacion-real"])
    with pytest.raises(fc.Abortar, match="go"):
        fc.main()
    amazon = _AmazonDesarme(estado_readback="ENABLED")
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--desarmar", "L1", "--acepto-mutacion-real", "--go", "pausa"])
    with pytest.raises(fc.Abortar, match="readback"):
        fc.main()
    assert len(amazon.puts) == 1  # se detiene en la primera


def test_desarmar_lote_sin_pasos_applied_aborta(monkeypatch):
    """Lote sin NINGUN paso campaign applied (nunca llego a crear nada en
    Amazon): 'nada que desarmar' es correcto — no hay externos que pausar."""
    conn_admin = _ConnFalsa(grupo=[])
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, _AmazonDesarme())
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--desarmar", "L9", "--acepto-mutacion-real", "--go", "x"])
    with pytest.raises(fc.Abortar, match="L9"):
        fc.main()


def test_desarmar_lote_failed_a_medias_pausa_lo_applied_y_lo_failed_con_external(
    monkeypatch, capsys
):
    """Regla 7 (lote muerto ANTES del registro): 3 campanas applied + 1 failed
    CON external (creada en Amazon, readback no cuadro — r3 codex 1: sigue
    ENABLED gastando y el desarmar la cubre) y NINGUNA fila en campana_grupo
    — el viejo JOIN abortaba 'sin grupo registrado' mientras las campanas
    seguian ENABLED gastando. La fuente es el ledger de pasos: se pausan las
    4 con readback (un paso failed SIN external NO devuelve fila: el POST fue
    rechazado, la campana no existe en Amazon); goal_id None (nunca hubo
    goals) y el lote se sella desarmado."""
    filas = [(rol, f"c-{rol}", None) for rol in fp.ROLES_ORDEN_CREACION[:3]]
    rol_broad = fp.ROLES_ORDEN_CREACION[3]
    filas.append((rol_broad, f"c-{rol_broad}", None))  # failed CON external
    conn_admin = _ConnFalsa(grupo=filas)
    amazon = _AmazonDesarme()
    apagados = []
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)

    def _edita(c, gid, **kw):
        apagados.append((gid, kw))
        return {}

    monkeypatch.setattr(fc.goals_write, "edita_goal", _edita)
    monkeypatch.setattr(
        sys, "argv",
        ["fabrica_campanas.py", "--desarmar", "L7", "--acepto-mutacion-real", "--go", "pausa"],
    )
    assert fc.main() == 0
    assert len(amazon.puts) == 4  # las 3 applied + la failed con external
    assert apagados == []  # goal_id None: el registro nunca corrio, no hay goals
    sellos = [
        p for s, p in conn_admin.escrituras
        if s.lower().startswith("update fabrica_lote ") and p[0] == "desarmado"
    ]
    assert len(sellos) == 1
    assert _eventos(capsys)[-1]["evento"] == "reconciliacion_final"


# Filas de _SQL_PENDIENTES: (id, lote, rol, platform, recurso, path_create,
# external_id, request_payload) — platform viene del JOIN a fabrica_lote
# (r3 codex 4)
_PENDIENTES = [
    (1, "L1", "category_exact", "amazon_mx", "campaign", "/sp/campaigns", "campaigns-1",
     {"name": "x"}),
    (2, "L1", "category_exact", "amazon_mx", "ad_group", "/sp/adGroups", None, {"name": "y"}),
    (3, "L1", "category_phrase", "amazon_mx", "keyword", "/sp/keywords", "keywords-9",
     {"keywordText": "k", "matchType": "PHRASE"}),
]


def test_reconciliar_promueve_solo_lo_verificado_y_aborta_si_queda_sin_verificar(monkeypatch, capsys):
    """campaigns-1 vive ENABLED -> applied; el paso 2 no tiene external (el
    POST lanzo): queda intacto y cuenta como sin_verificar -> Abortar al final,
    DESPUES de procesar todos."""
    conn_admin = _ConnFalsa(pendientes=_PENDIENTES)
    amazon = _Amazon()
    amazon.objetos = {"campaigns-1": {"name": "x"}, "keywords-9": {"keywordText": "k", "matchType": "PHRASE"}}
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--reconciliar", "--lote", "L1"])
    with pytest.raises(fc.Abortar, match="sin verificar"):
        fc.main()
    promovidos = [p for s, p in conn_admin.escrituras if "estado = 'applied'" in s.lower()]
    assert [p[-1] for p in promovidos] == [1, 3]
    resumen = [e for e in _eventos(capsys) if e["evento"] == "reconciliacion"][0]
    assert resumen["recuperadas"] == 2 and resumen["sin_verificar"] == 1


def test_reconciliar_aborta_si_vive_pero_no_cuadra(monkeypatch, capsys):
    """Fail-closed (antes salia 0): el LIST respondio pero el objeto NO
    cuadra con el payload del ledger (keywordText distinto) -> va a ausentes
    y el comando ABORTA con mensaje de verificacion manual."""
    conn_admin = _ConnFalsa(pendientes=_PENDIENTES)
    amazon = _Amazon()
    amazon.objetos = {
        "campaigns-1": {"name": "x"},
        "keywords-9": {"keywordText": "OTRA", "matchType": "PHRASE"},
    }
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(sys, "argv", ["fabrica_campanas.py", "--reconciliar", "--lote", "L1"])
    with pytest.raises(fc.Abortar, match="no cuadran"):
        fc.main()
    promovidos = [p for s, p in conn_admin.escrituras if "estado = 'applied'" in s.lower()]
    assert [p[-1] for p in promovidos] == [1]  # solo la campana que cuadra
    resumen = [e for e in _eventos(capsys) if e["evento"] == "reconciliacion"][0]
    assert resumen["ausentes"] == 1 and resumen["sin_verificar"] == 1


def test_reconciliar_con_plataforma_excluye_la_otra_y_no_la_cuenta(monkeypatch, capsys):
    """r3 codex 4: `--reconciliar --plataforma amazon_mx` con pasos pendientes
    de DOS plataformas solo LISTea/promueve los MX; los US quedan intactos y
    NO cuentan como sin_verificar aunque no haya perfil US (el filtro SQL los
    excluye; sin el filtro se LISTeaban con el perfil equivocado)."""
    pendientes = _PENDIENTES + [
        (4, "L2", "category_exact", "amazon_us", "campaign", "/sp/campaigns",
         "campaigns-9", {"name": "us"}),
    ]
    conn_admin = _ConnFalsa(pendientes=pendientes)
    amazon = _Amazon()
    amazon.objetos = {
        "campaigns-1": {"name": "x"},
        "keywords-9": {"keywordText": "k", "matchType": "PHRASE"},
        "campaigns-9": {"name": "us"},
    }
    _frontera_mutacion(monkeypatch, _ConnFalsa(), conn_admin, amazon)
    monkeypatch.setattr(
        sys, "argv",
        ["fabrica_campanas.py", "--reconciliar", "--plataforma", "amazon_mx"],
    )
    with pytest.raises(fc.Abortar, match="sin verificar"):
        fc.main()  # el paso 2 (sin external) sigue disparando el abort
    assert amazon.posts("/sp/campaigns") == []  # ningun POST de creacion
    lists = [p for p in amazon.pedidos if str(p.url).endswith("/list")]
    assert all(b"campaigns-9" not in p.content for p in lists), "el paso US no se LISTea"
    promovidos = [p for s, p in conn_admin.escrituras if "estado = 'applied'" in s.lower()]
    assert [p[-1] for p in promovidos] == [1, 3]  # solo los MX
    resumen = [e for e in _eventos(capsys) if e["evento"] == "reconciliacion"][0]
    assert resumen["pendientes"] == 3 and resumen["recuperadas"] == 2
    assert resumen["sin_verificar"] == 1  # el paso sin external, no los US


@_skip_db
def test_sql_del_ledger_contra_postgres_real():
    """Los INSERT/UPDATE/SELECT de mutacion del ledger contra Postgres REAL
    (el _ConnFalsa no caza un IndeterminateDatatype ni el ORDER BY del ENUM):
    lote, pasos planeado/applied/failed, campanas del lote (desde pasos:
    applied + failed CON external, r3 codex 1), pendientes (con la platform
    del paso por el JOIN al lote, r3 codex 4), promocion y sello."""
    with db_fabrica("orbit_fab_sql") as conn:
        fc._inserta_lote(conn, "L1", _plan_min(), "h", "go")
        ctx = fc._Ctx(None, "tok", None, None, 101, conn, "L1")
        paso_c = fp.Paso("category_exact", "campaign", "/sp/campaigns", {"name": "x"}, "c")
        paso_a = fp.Paso("category_phrase", "ad_group", "/sp/adGroups", {"name": "y"}, "a")
        pid1 = fc._inserta_paso(ctx, paso_c, {"name": "x"})
        fc._sella_paso(ctx, pid1, True, "c-1", {"ok": 1}, "ENABLED")
        pid2 = fc._inserta_paso(ctx, paso_a, {"name": "y"})
        # failed CON external (el readback no cuadro): promoverlo luego no
        # viola paso_evidencia_applied (applied exige external+ack+readback)
        fc._sella_paso(ctx, pid2, False, "ag-2", {"status": 207}, "PAUSED")
        paso_c2 = fp.Paso("category_broad", "campaign", "/sp/campaigns", {"name": "z"}, "c")
        pid3 = fc._inserta_paso(ctx, paso_c2, {"name": "z"})
        fc._sella_paso(ctx, pid3, False, "c-3", {"status": 207}, "PAUSED")
        paso_c3 = fp.Paso("auto_discovery", "campaign", "/sp/campaigns", {"name": "w"}, "c")
        pid4 = fc._inserta_paso(ctx, paso_c3, {"name": "w"})
        fc._sella_paso(ctx, pid4, False, None, {"status": 400}, None)  # POST rechazado
        filas = conn.execute(fc._SQL_CAMPANAS_DEL_LOTE, ("L1",)).fetchall()
        # r3 codex 1: applied + failed CON external; el failed SIN external no existe
        assert [(f[0], f[1], f[2]) for f in filas] == [
            ("category_exact", "c-1", None), ("category_broad", "c-3", None)
        ]
        pend = conn.execute(fc._SQL_PENDIENTES, ("L1", "L1", None, None)).fetchall()
        esperado = [
            (pid2, "ad_group", "/sp/adGroups", "ag-2"),
            (pid3, "campaign", "/sp/campaigns", "c-3"),
            (pid4, "campaign", "/sp/campaigns", None),
        ]
        assert [(p[0], p[4], p[5], p[6]) for p in pend] == esperado
        assert {p[3] for p in pend} == {"amazon_mx"}  # platform viene del JOIN al lote
        conn.execute(fc._SQL_PROMUEVE_APPLIED, (json.dumps({"fuente": "t"}), "ENABLED", pid2))
        conn.execute(fc._SQL_PROMUEVE_APPLIED, (json.dumps({"fuente": "t"}), "ENABLED", pid3))
        conn.commit()
        pend_final = conn.execute(fc._SQL_PENDIENTES, ("L1", "L1", None, None)).fetchall()
        assert [(p[0], p[6]) for p in pend_final] == [(pid4, None)]
        fc._sella_lote(conn, "L1", "failed", "detalle")
        fila = conn.execute("SELECT estado, detalle FROM fabrica_lote WHERE lote = 'L1'").fetchone()
        assert fila == ("failed", "detalle")
```

- [ ] **Step 2: Rojo**

Run: `pytest tests/test_fabrica_campanas.py -k "desarmar or reconciliar" -v`
Expected: FAIL (`Abortar: --desarmar: pendiente de la tarea 9`).

- [ ] **Step 3: Implementar (reemplaza los stubs)**

```python
# tools/fabrica_campanas.py — reversa y reconciliacion (tarea 9)
# La fuente de las campanas a pausar es el LEDGER DE PASOS (recurso=
# 'campaign', estado applied O failed CON external_id — r3 codex 1: failed
# con external = la campana se creo en Amazon pero el readback no cuadro y
# sigue ENABLED gastando; dejarla fuera era un hueco de la regla 7. failed
# SIN external = POST rechazado: no existe en Amazon, no se toca): es durable
# desde ANTES del HTTP, asi un lote muerto a medias — antes del registro en
# campana_grupo — se puede desarmar (regla 7). campana_grupo/_rol/goal solo
# enriquecen (goal_id; el rol ya esta en la fila del paso). ORDER BY p.orden
# = orden de CREACION (exact primero): no depende del orden de declaracion
# del ENUM campana_rol (que no coincide con ROLES_ORDEN_CREACION; para
# desarmar el orden no es critico, pero el de creacion es el mas legible).
_SQL_CAMPANAS_DEL_LOTE = """
SELECT p.rol::text, p.external_id, g.id
  FROM fabrica_lote_paso p
  LEFT JOIN campana_grupo cg ON cg.lote = p.lote
  LEFT JOIN campana_grupo_rol r ON r.grupo_id = cg.id AND r.rol = p.rol
  LEFT JOIN ads_optimizer_goal g ON g.ad_entity_id = r.ad_entity_id AND g.scope = 'campaign'
 WHERE p.lote = %s AND p.recurso = 'campaign'
   AND p.estado IN ('applied', 'failed') AND p.external_id IS NOT NULL
 ORDER BY p.orden
"""
_SQL_PENDIENTES = """
SELECT p.id, p.lote, p.rol::text, l.platform::text, p.recurso, CASE p.recurso
         WHEN 'campaign' THEN '/sp/campaigns' WHEN 'ad_group' THEN '/sp/adGroups'
         WHEN 'product_ad' THEN '/sp/productAds' WHEN 'keyword' THEN '/sp/keywords'
         WHEN 'target' THEN '/sp/targets' ELSE '/sp/negativeKeywords' END,
       p.external_id, p.request_payload
  FROM fabrica_lote_paso p
  JOIN fabrica_lote l ON l.lote = p.lote
 WHERE p.estado IN ('planeado', 'failed')
   AND (%s::text IS NULL OR p.lote = %s)
   AND (%s::platform IS NULL OR l.platform = %s::platform)
 ORDER BY p.id
"""
_SQL_PROMUEVE_APPLIED = """
UPDATE fabrica_lote_paso
   SET estado = 'applied', ack = %s::jsonb, readback_estado = %s
 WHERE id = %s AND estado IN ('planeado', 'failed')
"""


def _put_estado_campana(ctx: _Ctx, external: str, estado: str) -> dict:
    """PUT /sp/campaigns state (sello de reactiva_campanas: vendor
    spcampaign en Content-Type y Accept, id como STRING, enum UPPER)."""
    resp = ctx.http.put(
        f"{API}/sp/campaigns",
        headers={
            "Authorization": f"Bearer {ctx.token}",
            "Amazon-Advertising-API-ClientId": ctx.cred.client_id,
            "Amazon-Advertising-API-Scope": str(ctx.profile),
            "Content-Type": VENDOR_CAMPANAS,
            "Accept": VENDOR_CAMPANAS,
        },
        json={"campaigns": [{"campaignId": str(external), "state": estado}]},
    )
    cuerpo: dict = {}
    with contextlib.suppress(ValueError):
        cuerpo = resp.json()
    return {"status": resp.status_code, "cuerpo": cuerpo, "texto": scrub(resp.text[:400])}


def _desarmar(args) -> int:
    """Reversa (regla 7): PAUSA las campanas del lote que EXISTEN en Amazon —
    pasos applied y tambien failed CON external (la campana se creo pero el
    readback no cuadro; sigue ENABLED gastando; r3 codex 1)— y pone
    enabled=false en sus goals (failed SIN external = POST rechazado, no
    existe, no se toca). La fuente es fabrica_lote_paso (durable pre-HTTP):
    un lote failed a medias, SIN grupo registrado, se desarma igual.
    Dry-run sin --acepto-mutacion-real."""
    conn_admin = connect(_dsn_admin())
    filas = conn_admin.execute(_SQL_CAMPANAS_DEL_LOTE, (args.desarmar,)).fetchall()
    conn_admin.commit()
    if not filas:
        raise Abortar(
            f"lote {args.desarmar} sin campanas que pausar en fabrica_lote_paso"
            " (applied, o failed con external): nada que desarmar"
        )
    for rol, external, goal_id in filas:
        print(f"{rol} | {external} | goal={goal_id} -> PAUSED + enabled=false", flush=True)
    _log("plan_desarmar", lote=args.desarmar, campanas=len(filas))
    if not args.acepto_mutacion_real:
        _log("dry_run", modo="desarmar", lote=args.desarmar, nota="sin --acepto-mutacion-real no se pausa nada")
        return 0
    if not args.go or not args.go.strip():
        raise Abortar("desarmar real exige --go con el literal del dueno")
    cred = AdsCredentials.from_secrets_dir()
    cliente_lectura = AdsClient(cred)
    perfiles = _perfiles(cliente_lectura)
    platform = conn_admin.execute("SELECT platform::text FROM fabrica_lote WHERE lote = %s", (args.desarmar,)).fetchone()
    if platform is None or platform[0] not in perfiles:
        raise Abortar(f"sin perfil aceptado para el lote {args.desarmar}")
    http = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0))
    ctx = _Ctx(http, _token_lwa(cred, http), cred, cliente_lectura, perfiles[platform[0]], conn_admin, args.desarmar)
    pausadas = 0
    for rol, external, goal_id in filas:
        ack = _put_estado_campana(ctx, external, _PAUSADA)
        if not fp.ack_ok(ack):
            _log("lote_detenido", lote=args.desarmar, motivo=f"PUT PAUSED de {rol} rechazado", pausadas=pausadas)
            raise Abortar(f"PUT PAUSED de {external} ({rol}) rechazado (status {ack.get('status')})")
        time.sleep(0.3)
        leido = _readback(cliente_lectura, ctx.profile, "/sp/campaigns", external)
        estado = leido.get("state") if isinstance(leido, dict) else None
        _log("desarmar", lote=args.desarmar, rol=rol, external=external, ack=ack["cuerpo"], readback=estado, ok=estado == _PAUSADA)
        if estado != _PAUSADA:
            raise Abortar(f"readback de {external} ({rol}) != PAUSED ({estado}): se detiene")
        if goal_id is not None:
            goals_write.edita_goal(conn_admin, goal_id, enabled=False, updated_at=datetime.datetime.now(datetime.UTC))
        pausadas += 1
    _sella_lote(conn_admin, args.desarmar, "desarmado", f"go: {args.go}")
    _log("reconciliacion_final", lote=args.desarmar, pausadas=pausadas, ok=True)
    return 0


def _reconciliar(conn_admin, cliente_lectura, perfiles: dict, lote: str | None,
                 plataforma: str | None) -> dict:
    """Cruza pasos planeado/failed contra el LIST real: promueve a applied lo
    que vive con el texto pedido; sin external (el POST lanzo) o LIST caido
    = sin_verificar (intacto, se reporta); el objeto vive pero NO cuadra con
    el payload del ledger = ausentes. Cada paso se LISTea con el perfil de SU
    plataforma (r3 codex 4: reconciliar todo con el unico perfil MX listaba
    pasos de US con el perfil equivocado); plataforma sin perfil aceptado =
    sin_verificar (patron archiva_inertes). Aborta al final si quedo alguno
    de AMBOS (fail-closed: salir 0 solo si todo quedo verificado)."""
    filas = conn_admin.execute(_SQL_PENDIENTES, (lote, lote, plataforma, plataforma)).fetchall()
    conn_admin.commit()
    resumen = {"pendientes": len(filas), "recuperadas": 0, "sin_verificar": 0, "ausentes": 0}
    por_plataforma: dict[str, list] = {}
    for fila in filas:
        por_plataforma.setdefault(fila[3], []).append(fila)
    for plataforma_fila, pasos in por_plataforma.items():
        profile = perfiles.get(plataforma_fila)
        if profile is None:
            _log("reconciliar_sin_perfil", plataforma=plataforma_fila, pasos=len(pasos),
                 nota="sin perfil aceptado: quedan sin verificar")
            resumen["sin_verificar"] += len(pasos)
            continue
        for fid, lote_fila, rol, _plat, recurso, path, external, payload in pasos:
            if external is None:
                _log("reconciliar_sin_external", paso=fid, lote=lote_fila, rol=rol, recurso=recurso,
                     nota="el POST no dejo id: verificar a mano por nombre en la consola")
                resumen["sin_verificar"] += 1
                continue
            leido = _readback(cliente_lectura, profile, path, external)
            if leido is None:
                resumen["sin_verificar"] += 1
                _log("reconciliar_sin_verificar", paso=fid, external=external,
                     nota="el LIST no respondio o no lo trae")
                continue
            if not _readback_cuadra(leido, payload if isinstance(payload, dict) else {}):
                resumen["ausentes"] += 1
                _log("reconciliar_no_cuadra", paso=fid, external=external,
                     estado=leido.get("state"))
                continue
            ack = {"fuente": "reconciliar", "external": external, "lote": lote_fila}
            conn_admin.execute(_SQL_PROMUEVE_APPLIED, (json.dumps(ack), leido.get("state"), fid))
            conn_admin.commit()
            resumen["recuperadas"] += 1
    _log("reconciliacion", lote=lote, **resumen)
    if resumen["ausentes"]:
        raise Abortar(
            f"reconciliacion con {resumen['ausentes']} paso(s) que viven pero no cuadran"
            " con el payload del ledger: verificacion manual (no se promueve a ciegas)"
        )
    if resumen["sin_verificar"]:
        raise Abortar(f"reconciliacion con {resumen['sin_verificar']} paso(s) sin verificar: nada se promovio a ciegas")
    return resumen


def _reconciliar_cmd(args) -> int:
    conn_admin = connect(_dsn_admin())
    cred = AdsCredentials.from_secrets_dir()
    cliente_lectura = AdsClient(cred)
    perfiles = _perfiles(cliente_lectura)
    if args.plataforma is None and args.lote is None:
        raise Abortar("--reconciliar exige --lote X o --plataforma (para elegir el perfil)")
    platform = args.plataforma
    if platform is None:
        # solo --lote: la plataforma del lote ES el filtro (r3 codex 4: sin
        # el filtro se LISTeaban pasos de la otra plataforma con este perfil)
        fila = conn_admin.execute("SELECT platform::text FROM fabrica_lote WHERE lote = %s", (args.lote,)).fetchone()
        if fila is None:
            raise Abortar(f"lote {args.lote} no existe")
        platform = fila[0]
    if platform not in perfiles:
        raise Abortar(f"sin perfil aceptado para {platform}")
    _reconciliar(conn_admin, cliente_lectura, perfiles, args.lote, platform)
    return 0
```

- [ ] **Step 4: Verde + ruff**

Run: `pytest tests/test_fabrica_campanas.py -v && ruff check tools/fabrica_campanas.py && ruff format --check tools/fabrica_campanas.py`
Expected: PASS. `_desarmar` roza PLR0915: si dispara, extraer `_pausa_una(ctx, rol, external, goal_id)`.

- [ ] **Step 5: Commit**

```bash
git checkout -b fabrica-01-9-reversa origin/master
git add tools/fabrica_campanas.py tests/test_fabrica_campanas.py
git commit -m "feat(fabrica): --desarmar (pausa + goals off) y --reconciliar (spec §5.6, regla 7)"
```

---

### Task 10: candado de arquitectura + documentación

**Files:**
- Modify: `tests/test_architecture.py` (después de `test_allowlist_snapshot_caza_import_de_escritura`)
- Modify: `docs/DATABASE.md` (sección de vistas/tablas: entrada de 0018)
- Modify: `docs/CHAT-CONTEXT.md` (una línea de estado, en el estilo de las líneas BIDS 01)

**Interfaces:**
- Consumes: `_imports_runtime`, `_violaciones`, `PERMITIDOS_IMPORTAR_ADS_WRITE`, `RAIZ` (ya en el módulo).
- Produces: `ALLOWLIST_IMPORTS_FABRICA_CAMPANAS`, `test_fabrica_campanas_solo_importa_lo_declarado`, `test_allowlist_fabrica_caza_import_de_escritura`, `test_fabrica_plan_es_puro`.

- [ ] **Step 1: Tests del candado (el de allowlist falla hasta que la lista coincida con los imports reales del tool)**

```python
# agregar a tests/test_architecture.py

# FABRICA 01 (plans/fabrica-01.md tarea 10): allowlist POSITIVA de los imports
# de runtime de tools/fabrica_campanas.py (mismo trato que snapshot_listas).
# El tool MUTA Amazon con HTTP propio: jamas app.ads.write (candado
# test_imports_del_cliente_de_escritura_acotados) y sus escrituras internas
# van SOLO por los caminos unicos: app.goals_write (goals) y
# app.ads.structure.sync_structure (ad_entity). Ampliarla = editar este
# archivo a proposito.
ALLOWLIST_IMPORTS_FABRICA_CAMPANAS = frozenset(
    {
        "__future__",
        "__future__.annotations",
        "argparse",
        "contextlib",
        "dataclasses",
        "dataclasses.dataclass",
        "datetime",
        "decimal",
        "decimal.Decimal",
        "decimal.InvalidOperation",
        "decimal.ROUND_HALF_EVEN",
        "json",
        "logging",
        "os",
        "sys",
        "time",
        "typing",
        "typing.Any",
        "httpx",
        "psycopg",
        "psycopg.rows",
        "psycopg.rows.tuple_row",
        "app",
        "app.fabrica_plan",
        "app.goals_write",
        "app.ads.client",
        "app.ads.client.DEFAULT_BASE_URL",
        "app.ads.client.AdsClient",
        "app.ads.config",
        "app.ads.config.AdsCredentials",
        "app.ads.structure",
        "app.ads.structure.evaluar_perfiles",
        "app.ads.structure.fetch_structure",
        "app.ads.structure.sync_structure",
        "app.db",
        "app.db.connect",
        "app.optimizer.goals",
        "app.optimizer.goals.fraccion_desde_settings",
        "app.redaction",
        "app.redaction.install_scrub_filter",
        "app.redaction.register_secret",
        "app.redaction.scrub",
    }
)


def test_fabrica_campanas_solo_importa_lo_declarado():
    extras = (
        _imports_runtime(RAIZ / "tools" / "fabrica_campanas.py") - ALLOWLIST_IMPORTS_FABRICA_CAMPANAS
    )
    assert not extras, (
        f"tools/fabrica_campanas.py importa por fuera de su allowlist: {sorted(extras)} — "
        "ampliar ALLOWLIST_IMPORTS_FABRICA_CAMPANAS exige editar tests/test_architecture.py"
    )
    assert "tools/fabrica_campanas.py" not in PERMITIDOS_IMPORTAR_ADS_WRITE
    fuente = (RAIZ / "tools" / "fabrica_campanas.py").read_text(encoding="utf-8")
    for patron in ("__import__(", "import_module(", "app.apply"):
        assert patron not in fuente, f"tools/fabrica_campanas.py usa {patron!r}"


def test_fabrica_guard_main_es_lo_ultimo_del_archivo():
    """FABRICA 01 (r2 glm 1): el tool entra por stdin (`python - < file`) y
    `main()` despacha funciones definidas mas abajo; si el bloque
    `if __name__ == "__main__":` no es LO ULTIMO del archivo, toda corrida
    real revienta con NameError mientras la suite (que importa el modulo
    completo) queda verde. El guard va al final, siempre."""
    lineas = [
        linea
        for linea in (RAIZ / "tools" / "fabrica_campanas.py")
        .read_text(encoding="utf-8")
        .splitlines()
        if linea.strip() and not linea.strip().startswith("#")
    ]
    idx = next(
        i for i, linea in enumerate(lineas) if linea.startswith('if __name__ == "__main__":')
    )
    resto = lineas[idx + 1 :]
    assert all(linea.startswith((" ", "\t")) for linea in resto), (
        f"codigo top-level despues del guard __main__ (linea {idx}): {resto}"
    )


def test_allowlist_fabrica_caza_import_de_escritura(tmp_path):
    """Regla 9: la copia del tool con `from app.ads.write import AdsWriteClient`
    queda fuera de la allowlist Y dispara el candado general."""
    fuente = (RAIZ / "tools" / "fabrica_campanas.py").read_text(encoding="utf-8")
    fuga = tmp_path / "fabrica_fuga.py"
    fuga.write_text(fuente + "from app.ads.write import AdsWriteClient\n", encoding="utf-8")
    imp = _imports_runtime(fuga)
    assert "app.ads.write" in _violaciones(imp, ("app.ads.write",))
    assert "app.ads.write" in imp - ALLOWLIST_IMPORTS_FABRICA_CAMPANAS


def test_fabrica_plan_es_puro():
    """app/fabrica_plan.py no importa IO (misma frontera que el motor: sin
    httpx/psycopg/app.ads/app.db)."""
    fugas = _violaciones(_imports_runtime(RAIZ / "app" / "fabrica_plan.py"), PROHIBIDOS_MOTOR)
    assert not fugas, f"app/fabrica_plan.py debe ser puro: {fugas}"
```

- [ ] **Step 2: Correr y ajustar la allowlist a los imports REALES del tool**

Run: `pytest tests/test_architecture.py -k fabrica -v`
Expected: PASS si la allowlist coincide; si `test_fabrica_campanas_solo_importa_lo_declarado` lista extras, son imports que el tool agregó en 7-9 (p.ej. `dataclasses`): agregarlos a la allowlist SOLO si son stdlib o caminos únicos; un `app.ads.write` o `app.apply` es un error del tool, no de la lista.

Verificar también que el candado extendido de la tarea 4 siga verde con el tool completo: `pytest tests/test_architecture.py -k escritura_de_goals -v` — el INSERT de `ads_optimizer_goal` existe SOLO en `app/goals_write.py` (`_SQL_CREA`); el tool nunca lo contiene (despacha `crea_goal`).

- [ ] **Step 3: Documentar 0018 en `docs/DATABASE.md`**

Agregar, junto a la entrada de `v_target_margen_plataforma` (línea ~555), un bloque con este contenido:

```markdown
- **Migración `0018` (FABRICA 01)** — grupos de campañas creados por
  `tools/fabrica_campanas.py`: `campana_grupo` (target CONGELADO al crear =
  clamp(fracción × margen mínimo, [10, 45]) con `target_procedencia`),
  `campana_grupo_rol` (EL vínculo campaña↔grupo; `UNIQUE(ad_entity_id)`;
  guarda también el ad group; trigger `campana_grupo_rol_kinds`),
  `campana_grupo_producto` (snapshot de margen y `seller_sku`),
  `keyword_biblioteca`/`negative_biblioteca` por `(tipo_producto, platform,
  texto)` (F1 lee; F2 escribe), `harvest_excepcion` (schema; se puebla en
  F2), ledger `fabrica_lote`/`fabrica_lote_paso` (patrón
  `keyword_archivo_manual`: `planeado` antes del HTTP, `applied` exige
  external+ack+readback). **`v_margen_producto`**: la maquinaria de
  `v_target_margen_plataforma` con grano `ledger_event.product_id`; cargos
  con `order_id` prorrateados por el monto del producto dentro de su orden,
  sin `order_id` por participación en la venta de la plataforma; mismos
  guards → `margen_neto_pct` NULL (regla 3). La escribe `app_admin`;
  `app_decide` solo lee (`campana_grupo_rol` es el destino de harvest de F2).
```

- [ ] **Step 4: Línea de estado en `docs/CHAT-CONTEXT.md`** (arriba, estilo de las líneas BIDS 01, en español sencillo):

```markdown
**2026-09-XX — FABRICA 01 (F1) lista para revisión: ya existe la herramienta que crea un grupo de 5 campañas nuevas (auto, phrase, broad, productos, exact) para un tipo de producto, con el target sacado del margen real más bajo del grupo y con anuncios por producto.** No crea nada sola: ensaya, muestra la huella, y solo con tu literal crea, verifica cada pieza contra Amazon y registra el grupo con sus metas. Si algo sale mal, se detiene y dice qué quedó creado; `--desarmar` pausa las 5. Nada se ha creado todavía en producción: el primer grupo real es la sonda que corre el lead con un producto y presupuestos mínimos.
```

- [ ] **Step 5: Commit**

```bash
git checkout -b fabrica-01-10-candados origin/master
git add tests/test_architecture.py docs/DATABASE.md docs/CHAT-CONTEXT.md plans/fabrica-01.md
git commit -m "test(architecture): allowlist de imports de tools/fabrica_campanas.py + docs 0018 (fabrica-01 tarea 10)"
```

---

### Task 11: despliegue, dry-run en producción y sonda (lead)

**Files:**
- Modify: `plans/fabrica-01.md` ("Decisiones y evidencia"), `docs/CHAT-CONTEXT.md`
- Create: `out/fabrica-sonda-<fecha>.log` (evidencia; `out/` no se commitea si está en `.gitignore`: se cita el nombre y se pega el extracto en el plan)

**Interfaces:**
- Consumes: todo lo anterior mergeado en `master`; contenedor `orbit-app-1` y Postgres de producción; `ORBIT_DSN_ADMIN`, `ORBIT_DSN_READ`, `ORBIT_DSN_INGEST` dentro del contenedor.
- Produces: migración 0018 aplicada en producción; un dry-run real; la sonda = primer grupo real con UN producto y budgets mínimos; vendors/shapes sellados (o corregidos con PR de fix + test regla 9); tarea AppFlowy `Done`.

- [ ] **Step 1: Backup y migración (patrón de las migraciones anteriores, disciplina aditiva)**

```bash
ssh goncloud 'docker exec orbit-postgres-1 pg_dump -U orbit -Fc orbit > /mnt/data/backups/orbit-pre-0018-$(date +%Y%m%d).dump'
ssh goncloud 'docker exec -i orbit-postgres-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1' < migrations/0018_fabrica_campanas.sql
ssh goncloud 'docker exec -i orbit-postgres-1 psql -U orbit -d orbit -c "SELECT platform, product_id, margen_neto_pct, dias_con_venta, cobertura FROM v_margen_producto ORDER BY 1, 3 DESC NULLS LAST LIMIT 20"'
```

Pegar la salida en "Decisiones y evidencia". Si NINGÚN producto trae `margen_neto_pct` no nulo, F1 se detiene aquí (residual 6 del spec) y se reporta al dueño: no se siembra target inventado.

- [ ] **Step 2: Dry-run real (cero HTTP) con el producto de la sonda**

```bash
ssh goncloud 'docker exec -i orbit-app-1 python - --plataforma amazon_mx \
  --tipo-producto <etiqueta> --nombre-base "<nombre>" --productos <product_id> --modo shadow \
  --budget-auto <min> --budget-phrase <min> --budget-product <min> --budget-broad <min> --budget-exact <min> \
  --bid-auto <b> --bid-phrase <b> --bid-product <b> --bid-broad <b> --bid-exact <b>' \
  < tools/fabrica_campanas.py | tee out/fabrica-dryrun-$(date +%Y%m%d).log
```

El dueño elige etiqueta, producto, budgets y bids viendo esta salida (decisión 5: cero defaults) y da el literal. `--modo shadow` para la sonda: el motor decide sin escribir; el flip a `live` es por `/goals` después.

- [ ] **Step 3: Sonda = mutación real del primer grupo**

```bash
ssh goncloud 'docker exec -i orbit-app-1 python - ... --acepto-mutacion-real --esperado 5 --huella <sha> --go "<literal>"' \
  < tools/fabrica_campanas.py | tee out/fabrica-sonda-$(date +%Y%m%d).log
```

Resultados posibles y qué hacer:
- **Todo `applied`, grupo registrado, 5 goals**: verificar en la consola de Amazon que las 5 existen ENABLED con sus anuncios; correr `--reconciliar --lote <lote>` (debe reportar 0 pendientes); pegar el extracto del log (scrubbed) en el plan; quitar los `# HIPOTESIS hasta la sonda` con un commit `docs(fabrica): shapes sellados por la sonda <fecha>`.
- **Rechazo de un shape** (400 en campaigns/adGroups/targets/productAds): el lote queda `failed` declarando lo creado. `--desarmar <lote>` pausa lo creado. Se abre un PR de fix con el shape corregido y un test regla 9 (el test viejo con el shape rechazado debe fallar contra el fix). Se repite la sonda.
- **Ack sin id o readback que no cuadra**: mismo trato; ANTES de repetir, `--reconciliar` para no duplicar.

- [ ] **Step 4: Verificar el ciclo siguiente del motor**

Con `--modo shadow`, el siguiente ciclo debe listar las 5 campañas nuevas como elegibles (goal propio, `enabled`, `mode=shadow`) y sus decisiones en shadow. Evidencia: `SELECT` sobre `decision` del ciclo filtrando `ad_entity_id` en `campana_grupo_rol` del lote.

- [ ] **Step 5: Cerrar**

```bash
ssh goncloud "python3 /mnt/data/appdata/appflowy/_migrate/add_ehv_task.py --name 'ORBIT NN — Fábrica de campañas por grupo (FABRICA 01)' --status 'Done' --notes '<qué se hizo, comandos y resultados de la sonda, decisiones (fracción, producto, budgets, bids, modo), PRs #..., pendiente: F2 (harvest por grupo, negative cruzado, biblioteca escrita por el motor) con su propio spec/plan>'"
```

Marker `cc:完了` en las 11 tareas de este plan + línea final en `docs/CHAT-CONTEXT.md` + commit `plan: fabrica-01 cerrada — sonda verificada`.

---

## Decisiones y evidencia (se llena durante la ejecución; el implementador escribe AQUÍ antes del código)

### Tarea 1 — SELECTs regla 8 (lead)

Corridos el 2026-09-05 contra produccion (`ssh goncloud`, `docker exec -i orbit-db-1 psql -U orbit_read -d orbit`; el contenedor se llama `orbit-db-1`, no `orbit-postgres-1`). AppFlowy: fila creada con status `In progress` (row_id `b414bf4b-613e-4eae-9fb3-dde7b2262baa`).

**(a) Ordenes multi-producto (grano del prorrateo):** ninguna orden trae mas de un producto.

```
 platform  | ordenes_multi | ordenes
-----------+---------------+---------
 amazon_mx |             0 |     261
 amazon_us |             0 |     154
(2 rows)
```

**(b) Cargos sin product_id (esperado: NO traen; solo ventas lo resuelven):** confirmado — `con_producto = 0` en fee/refund/withholding; withholding casi siempre con `order_id` (prorrateo por orden aplica).

```
 platform  |    kind     | con_producto | con_orden | total
-----------+-------------+--------------+-----------+-------
 amazon_mx | fee         |            0 |       566 |   658
 amazon_mx | refund      |            0 |         6 |     6
 amazon_mx | withholding |            0 |       272 |   276
 amazon_us | fee         |            0 |       478 |   567
 amazon_us | refund      |            0 |        29 |    29
 amazon_us | withholding |            0 |       270 |   274
(6 rows)
```

**(c) dias_con_venta por producto (ventana de 90 dias, sin el curso):** maximo 27 dias (amazon_mx, `PERS-CAR-AZU-SAN-DOR`) y 17 dias (amazon_us, `NH-PERS-ITA-CEN-DOR`). **NINGUN producto llega a 60 EN LA VENTANA DE 90 DIAS** → ver decision pendiente abajo. Venta 100% cubierta por `sku_cost` en moneda y una sola moneda por plataforma (MX en MXN, US en USD; `n_monedas = 1` en todas las filas); denominador: ventas con `product_id` — quedan fuera 3 ventas amazon_mx sin `product_id` por MXN 5,664.00 en la ventana (residuo fuera del prorrateo por producto). Extracto (top 10 por plataforma; salida completa: 132 filas):

```
 platform  | product_id |           odoo_sku            | dias_con_venta | venta_total | venta_cubierta | n_monedas
-----------+------------+-------------------------------+----------------+-------------+----------------+-----------
 amazon_mx |       1621 | PERS-CAR-AZU-SAN-DOR          |             27 |  35069.6700 |     35069.6700 |         1
 amazon_mx |        185 | NH-CAR-ROJ-CEN-DOR            |             14 |  14841.0000 |     14841.0000 |         1
 amazon_mx |        207 | NH-CAR-ROJ-VCO-DOR            |             12 |  14327.2000 |     14327.2000 |         1
 amazon_mx |       1625 | PERS-CAR-AZU-VCO-DOR          |             10 |  13728.0000 |     13728.0000 |         1
 amazon_mx |        203 | NH-CAR-ROJ-SAN-DOR            |              9 |  10686.0000 |     10686.0000 |         1
 amazon_mx |        335 | NH-PERS-CAR-AZU-COR-DOR       |              7 |   9984.0000 |      9984.0000 |         1
 amazon_mx |        187 | NH-CAR-ROJ-COR-DOR            |              7 |   7520.0000 |      7520.0000 |         1
 amazon_mx |        616 | SET-ARR-22-DOR-MAX-COF-22-CHA-RED-DOR |      6 |   5263.7900 |      5263.7900 |         1
 amazon_mx |       1740 | SET-CAR-AZU-SAN-PLA           |              6 |   5934.0000 |      5934.0000 |         1
 amazon_mx |        371 | NH-PERS-NOG-SIN-VCO-DOR       |              6 |  10367.5800 |     10367.5800 |         1
 amazon_us |        345 | NH-PERS-ITA-CEN-DOR           |             17 |  49312.1100 |     49312.1100 |         1
 amazon_us |        369 | NH-PERS-NOG-SIN-VBU-DOR       |             17 |  47763.5800 |     47763.5800 |         1
 amazon_us |        263 | NH-EUR-VIN-M-REP-CEN-DOR      |             15 |  49711.2900 |     49711.2900 |         1
 amazon_us |        355 | NH-PERS-ITA-VBU-DOR           |             11 |  27941.4300 |     27941.4300 |         1
 amazon_us |        359 | NH-PERS-NOG-SIN-CEN-DOR       |             10 |  29237.1100 |     29237.1100 |         1
 amazon_us |        273 | NH-EUR-VIN-M-REP-VBU-DOR      |              8 |  23454.6100 |     23454.6100 |         1
 amazon_us |        367 | NH-PERS-NOG-SIN-SAN-DOR       |              8 |  20874.2200 |     20874.2200 |         1
 amazon_us |        354 | NH-PERS-ITA-SAN-PLA           |              5 |  12465.1800 |     12465.1800 |         1
 amazon_us |        356 | NH-PERS-ITA-VBU-PLA           |              4 |   9905.3100 |      9905.3100 |         1
 amazon_us |        335 | NH-PERS-CAR-AZU-COR-DOR       |              4 |   9949.2200 |      9949.2200 |         1
```

> **PENDIENTE DECISION DEL DUENO (60 vs 30, Y LA VENTANA):** en la ventana de 90 dias del plan ningun producto alcanza `dias_con_venta >= 60` (max 27). El umbral NO es inalcanzable por negocio: es la ventana la que lo mata. Con ventana de 365 dias (siempre sin el curso), 8 productos llegan a >= 30 y uno cruza 60:
>
> ```
>  platform  | product_id |        odoo_sku         | dias_con_venta
> -----------+------------+-------------------------+----------------
>  amazon_mx |       1621 | PERS-CAR-AZU-SAN-DOR    |             62
>  amazon_mx |        207 | NH-CAR-ROJ-VCO-DOR      |             56
>  amazon_mx |       333 | NH-PERS-CAR-AZU-CEN-DOR |             53
>  amazon_mx |       185 | NH-CAR-ROJ-CEN-DOR      |             50
>  amazon_us |        359 | NH-PERS-NOG-SIN-CEN-DOR |             40
>  amazon_us |        369 | NH-PERS-NOG-SIN-VBU-DOR |             38
>  amazon_mx |        335 | NH-PERS-CAR-AZU-COR-DOR |             36
>  amazon_mx |        203 | NH-CAR-ROJ-SAN-DOR      |             35
> (8 rows)
> ```
>
> La decision del dueno es DOBLE: el valor de `MARGEN_DIAS_MIN_PRODUCTO` (60 o 30) Y la ventana sobre la que se cuenta (90 o 365 dias, siempre sin el curso). Cambiar cualquiera requiere decision escrita del dueno aqui (regla 2). Hasta entonces NO se decide.

**(d) Listings y productos multi-listing:** todos los listings tienen `seller_sku` en ambas plataformas. La columna `productos` cuenta solo productos CON listing en esa plataforma (universo relevante para la fabrica), no el catalogo completo (1,087 productos en `product`).

```
 platform  | listings | con_sku
-----------+----------+---------
 amazon_us |      176 |     176
 amazon_mx |      342 |     342
(2 rows)

 platform  | productos_multi_listing | productos
-----------+-------------------------+-----------
 amazon_us |                      44 |       119
 amazon_mx |                      71 |       249
(2 rows)
```

> **Decision (d):** hay multi-listing en AMBAS plataformas (71/249 en amazon_mx; 44/119 en amazon_us) → el `--productos` de la sonda (tarea 11) los EXCLUYE; el tool aborta nombrandolos si se piden; residual `--listing` explicito queda fuera de F1.

**(e) Setting de fraccion vigente (config_version id 14):**

```
 id | mx  | us
----+-----+-----
 14 | 0.5 | 0.5
(1 row)
```

**(f) Grano de `search_term_observation`:** UN solo grano — `ad_group` (34,045 filas en la ventana de 105 dias; cero filas con `kind = 'campaign'`).

```
    kind    | count
------------+-------
 ad_group   | 34045
(1 row)
```

> **Decision (f):** produccion tiene UN solo grano (`ad_group`) → en la tarea 6 la CTE `origenes` de `_SQL_TERMINOS` se simplifica a ese SELECT (grano ad_group; la campana se deduce por `parent_id`) y el UNION se elimina. `test_terminos_del_producto_colapsan_bitemporal_y_suman_en_ventana` se ajusta a esa variante (deja de ejercitar la hipotesis UNION de ambos granos).

### Tarea 11 — sonda (lead)

_(pendiente; extracto scrubbed del log, shapes confirmados o corregidos, lote y grupo_id)_

## Fuera de este plan (F2, spec §7)

Reruteo del harvest por `campana_grupo_rol`, negative cruzado en las hermanas con fase `hermanas_negadas` (migración 0019), relajación del CHECK `goal_harvest_completo`, biblioteca escrita desde harvest/negatives aplicados, migración de las existentes a `harvest_excepcion`. Depende de la sonda de la tarea 11 (si Amazon rechaza negative keywords en el ad group de product targeting, la decisión 10 queda en 3 hermanas).

**Residual declarado de F1**: un producto multi-listing (más de un `listing` en la plataforma del grupo) NO puede entrar a un grupo — `_productos` aborta nombrándolo (regla 3: la fábrica no elige listing). Queda fuera hasta que exista `--listing <id>` explícito (flag nuevo, su propia tarea fuera de F1).
