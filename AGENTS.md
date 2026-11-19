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
- Para pruebas, revisión y secretos, sigue las Reglas de trabajo de este archivo.

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
uv run --frozen python -m pytest -q       # suite local; ORBIT_TEST_DSN apunta a PostgreSQL de prueba
ruff check --fix . && ruff format .       # lint/format (line-length 100)
pre-commit run --all-files                # gitleaks y conflictos
```
Windows: el venv es `.venv/Scripts/python.exe`; sin Postgres local, lo PG-backed
se skipea por diseno. Si un push se cuelga, es credencial:
`gh auth setup-git`.

La revisión local de secretos usa Gitleaks mediante pre-commit.

## Reglas de diseño innegociables

Aplican a todo el código (detalle en `docs/CONTEXTO.md`; los invariantes de tiempo van en **trigger con UTC fijado**, nunca en CHECK):

1. Una decisión, un camino, un dueño — lo que no está en ese camino no se construye.
2. Un número, una fuente.
3. Dato faltante = `None` / fila no escrita; **nunca** una constante inventada.
4. Dinero = `(valor, moneda)` por schema (`NUMERIC(14,4)` + ENUM MXN/USD; prohibido float).
5. Métricas **append-only** `(entidad, metric_date, observed_at)`; corregir = insertar fila nueva.
6. Cortar/pausar exige dato con ≥10 días de maduración; el día en curso se descarta.
7. Ninguna acción irreversible sin su reversa implementada antes.
8. Antes de definir un invariante que dependa del dato real, correr el `SELECT` que lo confirma en producción.
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
- Trabajo en planes `ORBIT NN` (PR a `master` según las Reglas de trabajo).

## Reglas de trabajo

Iguales en todos los repos de David. Si otro documento de este repo dice otra cosa sobre calidad,
candados, evidencia, rondas o pruebas, manda esto, salvo un plan que David aprobó para ese trabajo:
ahí manda el plan, salvo en las dos reglas sin excepción. Si una regla no se puede cumplir, el
trabajo sigue y se anota por qué en el PR. Un paso solo se detiene por secretos, por seguridad, por
algo que el plan no autoriza (borrar datos, gastar, escribir en producción, hablar con terceros en
nombre de David) o por un bloqueo de revisión que quedó sin resolver; eso espera a David. Lo demás
no espera a nadie.

Sin excepción:

- Todo cambio entra por PR; nadie sube directo a la rama principal.
- Las llaves y contraseñas quedan fuera del repo, y la revisión de secretos corre siempre (sin
  `--no-verify`). Si marca algo que no es una llave, se agrega a `.gitleaksignore` con su razón y
  se sigue.

Con criterio:

- Pruebas obligatorias en 4 casos: dinero (precios, pujas, presupuestos, cobros), escribir datos de
  producción, instalar o borrar en las máquinas, y una falla que ya pasó en producción (una prueba,
  no una batería). Fuera de esos casos, una prueba solo si es la forma más barata de comprobar que
  funciona. Por defecto no se hacen baterías, pruebas de texto ni mutantes; sí cuando son la forma
  más barata de demostrar un riesgo de dinero o de datos (por ejemplo, el texto exacto con el que
  alguien decide). Fuera de eso, el revisor no las pide.
- Las pruebas que ya existen deben pasar antes de mergear. Si una ya falla igual en la rama
  principal, no frena: se anota en el PR.
- El revisor normal es un modelo distinto del que escribió; si no está disponible, el siguiente que
  sí lo esté. La revisión no espera a una persona. Solo bloquea por seguridad, datos, el
  comportamiento pedido roto o uno de los 4 casos sin su prueba, con el comando que lo reproduce o,
  si no se puede reproducir, la línea exacta y el riesgo. Lo demás va en notas.
- Sin tope de rondas mientras cada una traiga avance. Si queda un bloqueo concreto, el implementador
  lo corrige y el revisor comprueba la corrección; cuando solo quedan notas, se integra el PR. Si el
  mismo bloqueo vuelve 3 rondas seguidas sin avance, lo revisa otro modelo; si ese tampoco lo
  resuelve, ese PR queda sin integrar y se avisa a David con el bloqueo y su evidencia. El resto del
  trabajo sigue.
- Todo cambio que toca producción dice en el PR cómo regresarlo.
- Compuertas (tamaño, lint, tipos, checklists, baterías, CI obligatorio) y revisores extra además
  del normal se agregan solo si David lo pide.

## Graphify y Feature Map (navegacion y verificacion UI)

- **Graphify (`graphify-out/graph.json`):** cuando exista `graphify-out/graph.json`, corre `graphify query "<pregunta>"` antes de tu primer `grep`, busqueda o lectura de archivos para dudas del codigo, e incluye esta regla en todo prompt de subagente que explore codigo. Usa `graphify path "<A>" "<B>"` para relaciones y `graphify explain "<concepto>"` para conceptos puntuales. Despues de modificar codigo, corre `graphify update .` (solo AST, sin costo de API) para mantener el grafo al dia.
- **Feature Map (`.cursor/skills/verify-orbit/`):** antes de tocar o verificar pantallas del dashboard, lee `.cursor/skills/verify-orbit/SKILL.md` y la ficha de la pantalla en `.cursor/skills/verify-orbit/features/<pantalla>.md`. Si cambias selectores, filtros o flujos visibles, actualiza su ficha en `features/` y comprueba la pantalla con `.cursor/skills/verify-orbit/helpers/orbit-verify` (`launch`, `doctor`, `drive-*`, `cleanup`).
