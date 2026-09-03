# ESTRUCTURA 01 — partir `app/ads/structure.py` (1,068 líneas) en API / plan puro / DB sin cambiar nada

> **Propósito**: `app/ads/structure.py` vive en la allowlist de tamaño de
> `tests/test_architecture.py` (tope 900 líneas) como «candidato DECLARADO a
> partirse la próxima vez que se toque en grande: IO de API (evaluar_perfiles
> + listar_todo + fetch_structure) de IO de DB (SQL sellada + _plan_items +
> sync_structure) — la frontera ya está marcada en el módulo». Hoy mide
> **1,068** líneas y sus cuatro secciones ya están delimitadas por
> encabezados. Este plan hace ese corte, **sin cambio de comportamiento**,
> con el módulo original como fachada para que ningún importador cambie.
>
> Decisión del dueño (2026-09-03): «si» al corte A (dos módulos nuevos +
> fachada). Implementa **Cursor**; el lead revisa contra `origin/master`,
> mergea y despliega junto con BIDS 01 (1.5), verificando en producción que
> un sync post-deploy deja exactamente los mismos conteos (el sync es
> idempotente).
>
> Precedencia: `docs/CONTEXTO.md` (reglas 1-10) > este plan. Un solo PR.
> Cross-review: 1 ronda (lead + CodeRabbit); sin codex salvo severidad alta.

## Reglas de proceso para Cursor (NO negociables)

1. **Prohibido tocar el contenedor de producción** ni la base viva: cero
   `ssh goncloud`, cero `docker exec`, cero SELECT, cero AppFlowy. La corrida
   real (sync post-deploy) es del lead.
2. **Rama desde `origin/master`** (`git fetch origin && git switch -c
   estructura-01 origin/master`); antes del PR, `git log origin/master..HEAD`
   lista SOLO tus commits. Sin commits de debug.
3. **Es un MOVE, no una reescritura**: los cuerpos de las funciones, las
   constantes y los SQL se mueven literal (copiar/pegar), sin «mejorar» nada
   de paso. El diff debe leerse como movimiento: `git diff --color-moved=zebra
   origin/master..HEAD` lo hace visible y el lead lo revisa así. Cualquier
   cambio de comportamiento que creas necesario se escribe en §Decisiones y
   se PARA a preguntar.
4. **La batería completa es el golden**: corre UNA vez en CI al abrir el PR
   (`.github/workflows/quality.yml` con Postgres). Local: solo
   `tests/test_structure_sync.py`, `tests/test_snapshot_listas.py` y
   `tests/test_architecture.py`. Ningún test existente se borra ni se
   debilita; los que importen nombres privados movidos se actualizan al
   módulo nuevo (ver §Fachada).
5. `ruff check --fix . && ruff format . && pre-commit run --all-files` antes
   de cada commit; jamás `--no-verify`. Sin acentos ni ñ en el código nuevo.
6. **Decisiones escritas ANTES del código** en §Decisiones (incluida la
   lista final de qué símbolo quedó en qué módulo). Si algo del plan no
   cuadra con el código real, se escribe ahí y se PARA a preguntar.
7. DoD: marker de la fila 1.1 a `cc:完了 [resumen]` + **una línea en
   `docs/CHAT-CONTEXT.md`** en lenguaje de negocio (el candado de frescura
   del CI la exige) + no correr cross-reviews por tu cuenta.

## Estado medido (lead, 2026-09-03, `origin/master` 47f6099)

| Pieza | Líneas | Contenido |
|---|---|---|
| Docstring de cabecera | 1–115 | ADR del módulo (se conserva; se le añade el mapa de módulos) |
| Constantes + SQL + dataclasses | 137–353 | `SOURCE`, `PATH_*`, `MAX_PAGINAS`, `_PAIS_PLATAFORMA_MONEDA`, `_ETIQUETA_*`, `_ESTADOS_PRODUCT_AD_VIVOS`, `ESTADO_ARCHIVED`, `_CLAVE_CONTENEDORA`, `_SQL_*`, `AdsStructureError`, `PerfilAds`, `EstructuraPerfil`, `EstructuraAds`, `_ItemEntidad`, `ResultadoSync` |
| «IO de API: fetch_structure» | 355–557 | `_json_de`, `_extraer_lista`, `listar_todo`, `_evaluar_perfil`, `evaluar_perfiles`, `perfiles_aceptados`, `fetch_structure` |
| «Planificacion pura (sin DB)» | 559–839 | `_bid_decimal`, `_nombre_target`, `_item_product_ad`, `_archivados_por_plataforma`, `_plan_items`, `_formato_skip_reason` |
| «IO de DB: sync_structure» | 841–1006 | `_sellar_run`, `sync_structure` |
| «__main__» | 1008–1068 | `_imprimir_resumen`, `main` |

Importadores (todos siguen importando de `app.ads.structure`, la fachada):
`app/apply.py` (`PerfilAds`, `evaluar_perfiles`), `app/ads/archivar.py`
(`PATH_PRODUCT_ADS`, `evaluar_perfiles`), `app/ads/reports.py`
(`_SQL_ABRIR_RUN`, `PerfilAds`, `_formato_skip_reason`, `_sellar_run`,
`evaluar_perfiles`), `tools/snapshot_listas.py` (`PATH_KEYWORDS`,
`PATH_NEGATIVE_KEYWORDS`, `PATH_TARGETS`, `listar_todo`, `perfiles_aceptados`,
`PerfilAds`), `tools/reactiva_campanas.py`, `tools/archiva_inertes.py`,
`tools/smoke_apply.py` (`evaluar_perfiles`), y los tests
`test_structure_sync`, `test_snapshot_listas`, `test_product_ads_vinculo`,
`test_reactiva_campanas`, `test_archiva_inertes`, `test_reports_pipeline`,
`test_cli` (parchea `app.ads.structure.main`), `test_architecture` (lista
`app.ads.structure.{listar_todo, perfiles_aceptados, PATH_KEYWORDS,
PATH_NEGATIVE_KEYWORDS, PATH_TARGETS}` como imports permitidos del snapshot y
`app/ads/structure.py` en `DEFINICIONES_MONEDA_DECLARADAS`).

Dos usos delicados en `tests/test_structure_sync.py`: (a) ~L910 recorre
`vars(app.ads.structure)` buscando `_SQL_*` para parsearlos con pglast; (b)
~L1413 parchea `app.ads.structure._SQL_UPSERT_STATE` para simular un fallo a
mitad del sync. **Los dos exigen que TODO el SQL y `sync_structure` se queden
en `structure.py`** (el módulo que los consume): así el parche sigue mordiendo.

## Decisiones selladas (diseño, corte A)

- **D1 · `app/ads/structure_api.py`** (IO de API, ~300 líneas): `PATH_*`,
  `MAX_PAGINAS`, `_PAIS_PLATAFORMA_MONEDA`, `AdsStructureError`, `PerfilAds`,
  `EstructuraPerfil`, `EstructuraAds`, `_json_de`, `_extraer_lista`,
  `listar_todo`, `_evaluar_perfil`, `evaluar_perfiles`, `perfiles_aceptados`,
  `fetch_structure`. Importa solo `app.ads.client` (+ stdlib).
- **D2 · `app/ads/structure_plan.py`** (puro, sin IO ni psycopg, ~330
  líneas): `_ETIQUETA_KIND`, `_ETIQUETA_PADRE`, `_ESTADOS_PRODUCT_AD_VIVOS`,
  `ESTADO_ARCHIVED`, `_CLAVE_CONTENEDORA`, `_ItemEntidad`, `_bid_decimal`,
  `_nombre_target`, `_item_product_ad`, `_archivados_por_plataforma`,
  `_plan_items`, `_formato_skip_reason`. Importa las dataclasses de
  `structure_api`. Candidato natural al candado de «motor puro» de
  `test_architecture` (sin `psycopg`, sin `httpx`): si el test de pureza
  admite añadirlo a su lista sin cambiar el candado, se añade; si no, se
  declara y no se fuerza.
- **D3 · `app/ads/structure.py`** (fachada + IO de DB, ~450 líneas):
  docstring de cabecera (conservada íntegra + un párrafo «Mapa de módulos»
  con quién vive dónde), `SOURCE`, TODOS los `_SQL_*`, `ResultadoSync`,
  `_sellar_run`, `sync_structure`, `_imprimir_resumen`, `main`, y las
  **re-exportaciones explícitas** (§Fachada). Los importadores NO cambian.
- **D4 · Candados de `tests/test_architecture.py`**: (a) quitar la entrada
  `app/ads/structure.py` de `ALLOWLIST_TAMANO` (el test es auto-limpiante:
  con el módulo bajo 900 la entrada sobrante FALLA); (b)
  `DEFINICIONES_MONEDA_DECLARADAS` pasa a `app/ads/structure_api.py` (el mapa
  `_PAIS_PLATAFORMA_MONEDA` se mueve ahí; el comentario de ~L489 que describe
  «forma DISTINTA» se actualiza al archivo nuevo); (c) la lista de imports
  permitidos del snapshot (`app.ads.structure.listar_todo`, …) NO cambia:
  la fachada los sigue exponiendo. (d) `test_imports_del_cliente_de_escritura`
  y el resto siguen verdes sin tocar.
- **D5 · Cero cambio de comportamiento.** Ningún `if`, valor, SQL, mensaje
  ni orden de operaciones cambia. El golden es la batería completa
  (`test_structure_sync` incluido, sin modificar asserts).
- **D6 · Deploy**: con BIDS 01 · 1.5 (mismo `git archive` de master). El
  lead corre después `python -m app.cli ingest structure` en el contenedor y
  compara `count(*)` de `ad_entity` y `ad_entity_state` por `platform`/`kind`
  antes y después: deben ser idénticos (regla 8; el sync es idempotente).

## Fachada (`app/ads/structure.py`) — nombres que DEBEN seguir resolviendo

```python
# API (structure_api): usados por app/, tools/ y tests/
from app.ads.structure_api import (  # noqa: F401 - fachada sellada (ESTRUCTURA 01)
    MAX_PAGINAS,
    PATH_AD_GROUPS,
    PATH_CAMPAIGNS,
    PATH_KEYWORDS,
    PATH_NEGATIVE_KEYWORDS,
    PATH_PRODUCT_ADS,
    PATH_PROFILES,
    PATH_TARGETS,
    AdsStructureError,
    EstructuraAds,
    EstructuraPerfil,
    PerfilAds,
    evaluar_perfiles,
    fetch_structure,
    listar_todo,
    perfiles_aceptados,
)

# Plan puro (structure_plan): usados por reports.py y tests
from app.ads.structure_plan import (  # noqa: F401 - fachada sellada (ESTRUCTURA 01)
    ESTADO_ARCHIVED,
    _CLAVE_CONTENEDORA,
    _formato_skip_reason,
    _plan_items,
)
```

Regla: TODO nombre que hoy alguien importe de `app.ads.structure` (lista de
§Estado medido) sigue importable de ahí. Los privados que solo usaban los
tests (`_plan_items`, `_CLAVE_CONTENEDORA`) se re-exportan igual (cambio
mínimo) — alternativa aceptada: actualizar esos dos imports en
`test_structure_sync.py` al módulo nuevo y NO re-exportarlos; decídelo en
§Decisiones y sé consistente.

## Phase 1 — El corte [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | **Cursor — partir `structure.py`** según D1-D4: crear `structure_api.py` y `structure_plan.py` moviendo literal los símbolos listados; dejar `structure.py` como fachada + IO de DB; actualizar `test_architecture.py` (allowlist de tamaño y moneda); docstring de cabecera con «Mapa de módulos»; `docs/DEPLOY.md`/`docs/CONTEXTO.md` solo si citan símbolos por ruta que cambie (grep antes). `[tdd:required]` | (1) `tests/test_architecture.py` ROJO antes del corte por la entrada sobrante de la allowlist SOLO tras quitarla (el rojo honesto de esta tarea es: quitar la entrada primero → el test de tamaño falla con 1,068 líneas → hacer el corte → verde); (2) batería completa verde en CI sin tocar asserts de `test_structure_sync`; (3) los 3 módulos ≤ 900 líneas y `structure_plan.py` sin `psycopg`/`httpx`; (4) diff revisable como MOVE (`--color-moved`); (5) §Decisiones con la tabla final símbolo → módulo | - | cc:TODO |
| 1.2 | **Lead — review, merge, deploy con BIDS 01 · 1.5 y verificación**: conteos de `ad_entity`/`ad_entity_state` por plataforma y kind antes y después de un `ingest structure` post-deploy (idénticos); `tools/snapshot_listas.py` sigue corriendo en el contenedor; AppFlowy. `[tdd:skip:ops]` | Conteos idénticos en la evidencia; snapshot ok; AppFlowy Done | 1.1 | cc:TODO |

## Decisiones y evidencia (Cursor escribe aquí ANTES del código)

Escritas 2026-09-03 ANTES de tocar `app/ads/structure.py`. MOVE literal. Cero
cambio de comportamiento.

**Privados `_plan_items` / `_CLAVE_CONTENEDORA`.** Se re-exportan desde la
fachada `app.ads.structure` (alternativa del plan, cambio minimo).
`tests/test_structure_sync.py` no cambia sus imports. Consistente con
«ningun importador de app/, tools/ ni tests/ cambia».

**Ciclo `_CLAVE_CONTENEDORA`.** D2 la deja en `structure_plan.py`; D1 deja
`_extraer_lista` / `listar_todo` en `structure_api.py`, que la leen.
`structure_plan` importa `PATH_*` de `structure_api`. Para no romper el
import circular, `structure_api` importa `_CLAVE_CONTENEDORA` DESPUES de
definir `PATH_*` y las dataclasses (el binding existe cuando
`structure_plan` carga). No es lazy import dentro de la funcion. Mismos
cuerpos.

**Candado de motor puro.** `test_architecture` solo exige pureza en
`app/optimizer/`. Meter `structure_plan.py` ahi exigiria cambiar el
candado. D2: se declara y no se fuerza. La prueba operativa es que
`structure_plan.py` no importa `psycopg` ni `httpx`.

**Tabla simbolo → modulo**

| Simbolo | Modulo |
|---|---|
| `PATH_*`, `MAX_PAGINAS`, `_PAIS_PLATAFORMA_MONEDA`, `_CLAVE_CONTENEDORA` | `structure_api` |
| `AdsStructureError`, `PerfilAds`, `EstructuraPerfil`, `EstructuraAds` | `structure_api` |
| `_json_de`, `_extraer_lista`, `listar_todo`, `_evaluar_perfil`, `evaluar_perfiles`, `perfiles_aceptados`, `fetch_structure` | `structure_api` |
| `_ETIQUETA_KIND`, `_ETIQUETA_PADRE`, `_ESTADOS_PRODUCT_AD_VIVOS`, `ESTADO_ARCHIVED`, `_ItemEntidad` | `structure_plan` |
| `_bid_decimal`, `_nombre_target`, `_item_product_ad`, `_archivados_por_plataforma`, `_plan_items`, `_formato_skip_reason` | `structure_plan` |
| docstring cabecera + «Mapa de modulos», `SOURCE`, todos los `_SQL_*`, `ResultadoSync`, `_sellar_run`, `sync_structure`, `_imprimir_resumen`, `main` | `structure` (fachada + DB) |
| re-export: `MAX_PAGINAS`, `PATH_*`, `_CLAVE_CONTENEDORA`, `AdsStructureError`, `EstructuraAds`, `EstructuraPerfil`, `PerfilAds`, `evaluar_perfiles`, `fetch_structure`, `listar_todo`, `perfiles_aceptados` | `structure` desde `structure_api` |
| re-export: `ESTADO_ARCHIVED`, `_formato_skip_reason`, `_plan_items` | `structure` desde `structure_plan` |

**Rojo honesto (allowlist).** Tras quitar `app/ads/structure.py` de
`ALLOWLIST_TAMANO` y correr
`pytest tests/test_architecture.py::test_presupuesto_de_tamano_por_modulo`
ANTES del corte:

```
F                                                                        [100%]
=================================== FAILURES ===================================
____________________ test_presupuesto_de_tamano_por_modulo _____________________
tests/test_architecture.py:279: in test_presupuesto_de_tamano_por_modulo
    assert not excedidos, (
E   AssertionError: modulos sobre el presupuesto de 900 lineas sin entrada en la allowlist (agregar entrada CON razon o partir el modulo — jamas partir por partir): {'app/ads/structure.py': 1068}
E   assert not {'app/ads/structure.py': 1068}
=========================== short test summary info ============================
FAILED tests/test_architecture.py::test_presupuesto_de_tamano_por_modulo
1 failed in 0.40s
```

**Desviacion `_CLAVE_CONTENEDORA`.** D2 la ponia en `structure_plan.py`, pero
el unico lector es `_extraer_lista` (API) y las claves son `PATH_*` (API).
Ponerla en plan crea un import circular API↔plan que ruff I (isort) sube
al tope y rompe el load. Queda en `structure_api.py` junto a `PATH_*`.
La fachada la re-exporta igual. `structure_plan` no la importa. No cambia
el valor ni los cuerpos.

## Reject (con razón)

- **Paquete `app/ads/structure/`**: mismo resultado con más rutas que tocar
  (candados y docs citan `app/ads/structure.py` por path).
- **Mover solo la API**: dejaría ~750 líneas, otra vez al borde del tope.
- **Aprovechar para «limpiar» funciones o SQL**: rompe el golden de
  comportamiento; cualquier mejora va en otra tarea.
- **Mover los `_SQL_*` fuera de `structure.py`**: rompería el parche de
  `test_structure_sync` (~L1413) y el barrido pglast (~L910).

## 事前確認

- 事項: external-send — `git push` + PR (Cursor) y merge (lead)
  理由: patrón del repo; batería completa en CI
  scope: 1.1
- 事項: destructive — deploy al contenedor (lead, con BIDS 01 · 1.5) y `ingest structure` de verificación
  理由: D6; el sync es idempotente y se verifica por conteos
  scope: 1.2
