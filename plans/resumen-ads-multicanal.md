# Plan de Resumen Ads multicanal

> **Para agentes.** Usa `superpowers:subagent-driven-development` o `superpowers:executing-plans`. Marca cada paso solo cuando exista su evidencia.

**Objetivo.** Convertir `/` en un resumen de publicidad con periodos configurables, comparación, tendencias legibles y datos de Amazon MX, Amazon US y Mercado Libre.

**Arquitectura.** Reutiliza `ads_metric_observation` y `v_metric_latest` si la inspección confirma el grano y el versionado del origen. Separa el catálogo del Resumen del vocabulario del optimizador. La API concentra KPI, comparaciones y medias móviles. La interfaz solo presenta esos resultados.

**Stack.** Python 3.12, FastAPI, PostgreSQL 16, `psycopg` 3, SQLite de solo lectura, Jinja, JavaScript, Chart.js local y pytest.

**Spec.** El diseño aprobado en la conversación del 2026-10-25 queda reproducido en `plans/resumen-ads-multicanal.md`, Appendix B.

El programa entrega tres PR en orden. `RESUMEN-ADS-01` ingesta Mercado Libre. `RESUMEN-ADS-02` expone el contrato multicanal. `RESUMEN-ADS-03` cambia la interfaz. Ningún PR modifica bids, pausas, precios, accounting ni bridge.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists, a file, a log line, a screenshot, a test run, or a SHA. The body is a how-to. The appendices explain and record.

The program runs `/Users/dn/.agents/skills/poteto-mode/playbooks/autopilot-stack.md`. Each owner stops at stack-ready. The operator reviews and lands the stack. `RESUMEN-ADS-03` also stops for UI review.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

## Program checklist

### Arm the program

- [ ] State the protocol and this plan to the operator, then stop. Start execution only on the operator's explicit go.
- [ ] Read these from trunk at program start. Re-read them at every tick.
  - [ ] Run `git show origin/main:AGENTS.md` if `origin/main` exists. Otherwise run `git show origin/master:AGENTS.md`.
  - [ ] Read `/Users/dn/.agents/skills/poteto-mode/playbooks/autopilot-stack.md`, `/Users/dn/.agents/skills/swarm/SKILL.md`, `/Users/dn/.agents/skills/poteto-mode/playbooks/opening-a-pr.md`, and `/Users/dn/.agents/skills/poteto-mode/playbooks/shipping.md`.
  - [ ] Read `docs/CONTEXTO.md`, `docs/DATABASE.md`, `docs/DEPLOY.md`, and `docs/DASHBOARD.md` from the current trunk.
  - [ ] Confirm that `control-ui` from `cursor-team-kit` is available before UI execution. If it is unavailable, record the risk in Appendix C and use only the existing `orbit-verify` browser driver without claiming equivalent coverage.
- [ ] On the operator's go, arm the audit tick as `/loop 1h` with the tick prompt below. Never leave the cadence to memory.
- [ ] Use this tick prompt, verbatim. "Re-read the execution playbook from trunk. Audit the operation against it and fix drift in this tick. Probe every active lane and judge progress by side effects only. Stand down a stuck lane and dispatch its replacement now. Then post a short status message to the operator in chat only when the audit found a tracked change that no earlier status message reported, such as a PR opened, a code-ready head, a round launched or closed, a verdict, a merge, a stuck agent and the action taken, a blocker added or cleared, or a decision only the operator can make. Name every such change and nothing else. Do not repeat a table, the merged list, or an unchanged blocker. If the audit found none, end the turn with no reply text. Either way, log this tick's row in your decision trail. The row names the items reported, or none."
- [ ] On the operator's hold or stand-down, send every owner a zero-writes order at once.

### Spawn owners

- [ ] Spawn one owner per PR with the full lifecycle the execution playbook names.
- [ ] Follow this dependency graph. Start dependent work only after its parent merges, or base it on the parent branch when the execution playbook stacks.
  - [ ] Branch `RESUMEN-ADS-01` from `master`.
  - [ ] Branch `RESUMEN-ADS-02` from `RESUMEN-ADS-01`.
  - [ ] Branch `RESUMEN-ADS-03` from `RESUMEN-ADS-02`.
- [ ] Hold the file boundaries. Each PR touches only the files listed in its section and its evidence directory.
- [ ] Hold the review gate. `RESUMEN-ADS-03` changes an interaction. It waits for the operator's review in chat with screenshots and a video before landing.

### PR mechanics, for every PR

- [ ] Resolve the forge once. Default to `gh`; if `command -v origin` succeeds and Origin can resolve the repository, use `origin pr` for every PR operation. Record any fallback to `gh`. Never require `gt`.
- [ ] Open the PR ready, never draft, per **Opening a PR**. Use `origin pr create --status open` or `gh pr create` according to the resolved forge. Target `master` for the first PR and the parent branch for each stacked child.
- [ ] Run `ruff check --fix . && ruff format .` and the focused tests before the PR-facing push. Push with hooks on.
- [ ] Run `/deslop` before each commit and `/no-comments` before review.
- [ ] Triage every Bugbot and security-reviewer comment per `/Users/dn/.agents/skills/poteto-mode/references/bugbot-triage.md`.
- [ ] Keep the parent's merge base during fix rounds. Only the root changes stack topology.

### Verdict and merge, for every PR

- [ ] A full swarm runs once, at the code-ready head. Another full swarm runs only for new behavior, a redesign, or a large new diff. A minor fix gets a single verifier at the new head. Run the gates, ten live lanes, perf lane, and at least two audit lanes.
- [ ] Clean only when every lane is `PASS`. Send findings to the owner. A new head gets a fresh verdict except for results valid under the patch-id rule in `playbooks/shipping.md`.
- [ ] Append each clean PR in dependency order. Do not land until the operator starts shipping. After landing starts, allow only the retarget sanctioned by `playbooks/shipping.md`.

### Boot recipe, for every live lane

Each live lane runs on its own cloud VM at the PR head. Use `control-ui` for browser lanes. Use the real CLI for ingestion lanes.

- [ ] Run `git fetch origin <head-branch> && git checkout <head SHA>`.
- [ ] Start PostgreSQL, apply the launch recipe from `verify/Launch.md`, seed the declared fixture, and start FastAPI for browser or API lanes.
- [ ] Deliver browser input only through the control skill. Use read-only SQL and HTTP GET for diagnostics. Deliver ingestion input through the documented CLI against a disposable SQLite snapshot.
- [ ] Save every screenshot to `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png` and return the paths with the report.

## Ingestar Mercado Libre Ads (RESUMEN-ADS-01)

**Depends on.** None.

**Files.**

- [ ] Create `app/meli/ads_ingest.py` for the canonical input model, read-only source mapping, deterministic version, entity sync, and append-only metric ingestion.
- [ ] Modify `app/cli.py` to add `ingest meli-ads --sqlite <snapshot>`.
- [ ] Modify `tools/refresh_costos.sh` to snapshot accounting and invoke the new command without coupling unrelated pipelines.
- [ ] Create `tests/test_meli_ads_ingest.py` and modify `tests/test_cli.py`.
- [ ] Modify `docs/DEPLOY.md` and `docs/DATABASE.md` with the source contract, schedule, reconciliation, and rollback.
- [ ] Do not create a migration. Reuse the existing entity, observation, dedupe, and ingest-run schema.
- [ ] Save source inspection and production reconciliation under `docs/evidencia/resumen-ads-multicanal/01-ingesta/`.

**Build.**

- [ ] Inspect the live source before writing fixtures. Run `PRAGMA table_info(meli_ads_metrics_daily)`, inspect indexes, and query a bounded sample. Record campaign identity, day grain, timezone, currency, metric definitions, zero semantics, update behavior, retention, and last successful sync. Stop and revise this plan if the source is not campaign/day or lacks stable campaign identity.
- [ ] Query production before defining invariants. Confirm applied migrations, existing MeLi entities and observations, effective ingest grants, and duplicate counts. Reconcile one source window by campaign/day against accounting. Save queries and results without credentials.
- [ ] Write failing tests with a real SQLite file in `tmp_path`. Cover same snapshot as no-op, corrected source row as a new observation, latest correction visible in `v_metric_latest`, campaign name update, unknown currency rejection, missing metric as `None`, source zero preserved, partial failure rollback, failed `ingest_run`, and absent snapshot or DSN. Run `uv run --frozen python -m pytest -q tests/test_meli_ads_ingest.py tests/test_cli.py` and confirm the new cases fail for the missing command or behavior.

```python
def test_reintento_no_duplica_y_correccion_agrega_observacion(conn, snapshot_meli):
    ingest_meli_ads(conn, snapshot_meli)
    primera = conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0]
    ingest_meli_ads(conn, snapshot_meli)
    assert conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0] == primera

    corrige_costo(snapshot_meli, campaign_id="MLM123", metric_date="2026-10-20", cost="121.00")
    ingest_meli_ads(conn, snapshot_meli)
    assert conn.execute("SELECT count(*) FROM ads_metric_observation").fetchone()[0] == primera + 1
```

- [ ] Define the internal boundary before database code. Use the following shape and keep source column names inside the SQLite adapter. Do not add source-specific branches to the dashboard.

```python
@dataclass(frozen=True)
class FilaMeliAds:
    campaign_external_id: str
    campaign_name: str | None
    metric_date: dt.date
    source_version: str
    metric_currency: str
    cost: Decimal | None
    ad_revenue: Decimal | None
    impressions: int | None
    clicks: int | None
    orders: int | None
```

Expose `ingest_meli_ads(conn: psycopg.Connection, sqlite_path: Path) -> dict[str, int]`. Keep the database connection injected so tests and the CLI use the same behavior.

- [ ] Implement the minimal ingestion. Open SQLite with `mode=ro` and create or update only `kind='campaign'` entities. Use the upstream revision as `source_version` when one exists. Otherwise hash the canonical row, including campaign, date, currency, and every metric. Derive `source_report_id` from source identity plus `source_version`. Set `observed_at` to the UTC ingestion time and use `INSERT ... ON CONFLICT DO NOTHING`. Never use the new `ingest_run_id` as the dedupe key. Record duplicates and rejected rows with reasons in `ingest_run`.
- [ ] Reuse the transaction pattern in `app/ledger.py:645`. Make each source snapshot atomic and seal `ok=false` on failure. Do not derive missing metrics from ledger fees or total Mercado Libre sales.
- [ ] Add the thin CLI command and snapshot wiring. Keep `accounting`, `bridge`, and Amazon jobs unchanged. Schedule Orbit after the observed accounting sync window. Document the exact cron only after measuring the current schedule and duration.
- [ ] Run the focused tests and confirm they pass. Run `uv run --frozen python -m pytest -q tests/test_meli_ads_ingest.py tests/test_cli.py`.
- [ ] Commit the verified ingestion unit with `git commit -m "RESUMEN ADS 01: ingesta MeLi append-only"`.

**You see.**

- [ ] A second run over the same snapshot reports all canonical rows as duplicates and adds no observations.
- [ ] A corrected source row adds one observation and `v_metric_latest` returns the corrected values.
- [ ] A source sample reconciles exactly by campaign/day in MXN, with every difference explained before activation.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Run `uv run --frozen python -m pytest -q tests/test_meli_ads_ingest.py tests/test_cli.py`. Pass when every new behavioral case and the existing CLI cases pass.
- [ ] Run the regression tests against the parent SHA and confirm that each new failure identifies the missing ingestion behavior. Save the commands and output.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-xhigh-fast` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run the same Amazon ingestion smoke at trunk and head. Save `amazon-regresion.png`. Pass when Amazon rows and run counts match.
- [ ] Lane 2. Ingest a disposable MeLi snapshot through the real CLI. Save `meli-carga.png`. Pass when campaign entities and observations match the source fixture.
- [ ] Lane 3. Repeat the identical snapshot. Save `meli-reintento.png`. Pass when row counts stay unchanged and duplicates are reported.
- [ ] Lane 4. Correct one source row and rerun. Save `meli-correccion.png`. Pass when history grows by one and the latest view shows the correction.
- [ ] Lane 5. Introduce one invalid currency and one invalid identity. Save `meli-rechazos.png`. Pass when the transaction fails closed and no partial canonical rows remain.
- [ ] Lane 6. Run without the snapshot. Save `meli-sin-snapshot.png`. Pass when the CLI exits 2 and writes no canonical metrics.
- [ ] Lane 7. Run without the ingest DSN. Save `meli-sin-dsn.png`. Pass when the error is redacted and no secret appears in logs.
- [ ] Lane 8. Reconcile a bounded production source sample read-only. Save `meli-conciliacion.png`. Pass when campaign/day totals match accounting or activation remains blocked with an explained difference.
- [ ] Lane 9. Run the surrounding refresh script with one unrelated pipeline forced to fail. Save `refresh-aislamiento.png`. Pass when unrelated failures retain their current behavior and the final status is nonzero.
- [ ] Lane 10. Restart the app and repeat the read-only reconciliation. Save `meli-reinicio.png`. Pass when ingested history and latest values remain stable.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Measure `/` total response time with the same Amazon fixture at trunk and head. Also measure elapsed time for the new ingestion over 100,000 synthetic canonical source rows.
- [ ] Probe. Interleave 30 warm requests at trunk and head with identical PostgreSQL data. Run the isolated ingestion three times on a fresh copy of the same snapshot.
- [ ] Baseline. Record median and p95 `/` latency at trunk before measuring head. Record the source snapshot size and row count.
- [ ] Rule. Fail if head p95 exceeds trunk p95 by more than 10 percent or if any 100,000-row ingestion exceeds 120 seconds. Do not compare ingestion against trunk because trunk lacks that command.

**Review gate.** None. RESUMEN-ADS-01 does not change an operator interaction.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Keep the verified patch-id and append the PR to the stack.
- [ ] Stop at stack-ready. The operator lands it bottom-up.

## Exponer el contrato del Resumen (RESUMEN-ADS-02)

**Depends on.** RESUMEN-ADS-01.

**Files.**

- [ ] Create `app/dashboard_resumen.py` for channel metadata, pure aggregation, comparison, moving averages, and summary queries.
- [ ] Modify `app/api_dashboard.py` to delegate the multichannel summary without widening optimizer platform vocabularies.
- [ ] Create `tests/test_dashboard_resumen.py` only for monetary aggregation and ratio invariants.
- [ ] Modify `app/ui_metricas.py` only to route old KPI callers through the single aggregation implementation.
- [ ] Modify `docs/DASHBOARD.md` with request parameters, response examples, null semantics, currencies, and freshness.

**Build.**

- [ ] Before implementation, write failing tests only for monetary behavior. Cover native-currency totals, missing money values, zero revenue, zero spend, ACoS and ROAS from sums, and campaign-only anti-double-counting. Run `uv run --frozen python -m pytest -q tests/test_dashboard_resumen.py` and confirm the new monetary cases fail. Verify periods, filters, maturity, and error responses through the live API instead of adding tests.
- [ ] Add an explicit Resumen catalog. Do not import `PLATAFORMAS_MONEDA` and do not add MeLi to `app/optimizer/bid.py`. Keep `dias_inmaduros=None` for MeLi until the source contract supplies a verified value.

```python
@dataclass(frozen=True)
class CanalResumen:
    platform: str
    label: str
    currency: Literal["MXN", "USD"]
    dias_inmaduros: int | None


CANALES_RESUMEN = (
    CanalResumen("amazon_mx", "Amazon MX", "MXN", 8),
    CanalResumen("amazon_us", "Amazon US", "USD", 8),
    CanalResumen("meli", "Mercado Libre", "MXN", None),
)
```

- [ ] Produce one response per selected channel and currency. Include `totals`, `comparison`, `series`, `coverage`, `max_metric_date`, and `last_ingest_at`. Never emit a cross-currency grand total. Keep monetary values and ratios as decimal strings or `None`. Expose `construye_resumen(conn: ConexionLectura, platforms: tuple[str, ...], desde: dt.date, hasta: dt.date, comparar: bool, hoy: dt.date) -> dict[str, object]`.

- [ ] Query six days before `desde` for MA7 and return values only inside the requested range. Calculate each moving ACoS or ROAS from seven-day sums of cost and revenue. Never average daily ratios. Return `None` when any required daily value is unknown, and expose the count of observed days. Use the existing series row dictionaries as input and expose `media_movil_7(filas: Sequence[dict[str, object]]) -> list[dict[str, object]]`. Each output row contains `fecha`, `cost`, `ad_revenue`, `acos`, `roas`, and `dias_observados`. Add a monetary test where daily ACoS values average to a different number than the ratio of seven-day sums, then assert the ratio of sums.

- [ ] Calculate KPI and deltas in this module. A delta requires known values on both sides. A zero denominator returns `None`. Preserve coverage by metric and do not convert missing days to zero.
- [ ] Delegate the route while keeping the existing `/api/dashboard/series/plataforma` response compatible for current consumers. Add a summary route or additive fields without changing the old series fields. Validate channels at the FastAPI boundary using the new catalog.
- [ ] Run the monetary tests and the existing dashboard suites. Run `uv run --frozen python -m pytest -q tests/test_dashboard_resumen.py tests/test_api_dashboard.py tests/test_ui_metricas.py tests/test_architecture.py` and confirm they pass.
- [ ] Commit the verified contract with `git commit -m "RESUMEN ADS 02: contrato multicanal del resumen"`.

**You see.**

- [ ] `GET` for Amazon MX, Amazon US, and MeLi returns three channel blocks with MXN totals separate from USD totals.
- [ ] The 30-day response includes 30 visible points, six warm-up days excluded, and a previous period of exactly 30 days.
- [ ] Missing MeLi maturity metadata produces no Amazon-style shaded immature interval.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Run `uv run --frozen python -m pytest -q tests/test_dashboard_resumen.py tests/test_api_dashboard.py tests/test_ui_metricas.py tests/test_architecture.py`. Pass when the monetary cases and all existing cases pass.
- [ ] Demonstrate each new monetary regression test failing against the parent SHA. Save one command and failure per monetary behavior.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-xhigh-fast` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Request the same default Amazon series at trunk and head. Save `api-amazon-regresion.png`. Pass when the original fields and values match.
- [ ] Lane 2. Request all three channels for 30 days. Save `api-tres-canales.png`. Pass when each block has the correct channel and native currency.
- [ ] Lane 3. Request 7, 30, and 90 days. Save `api-presets.png`. Pass when each effective range has the requested number of days and ends at D-1.
- [ ] Lane 4. Request a custom range and an invalid 367-day range. Save `api-limites.png`. Pass when the valid range succeeds and the invalid range returns 422.
- [ ] Lane 5. Request previous-period comparison with known and missing values. Save `api-comparacion.png`. Pass when periods have equal lengths and unknown deltas remain null.
- [ ] Lane 6. Inspect MA7 at the left boundary and over a missing day. Save `api-ma7.png`. Pass when warm-up stays hidden and incomplete windows return null with coverage.
- [ ] Lane 7. Use zero revenue and zero spend fixtures. Save `api-ceros.png`. Pass when ACoS and ROAS follow their null contracts without division errors.
- [ ] Lane 8. Seed campaign plus child metrics that would double-count if included. Save `api-grano.png`. Pass when only campaign rows affect totals.
- [ ] Lane 9. Hide MeLi freshness and maturity source data. Save `api-sin-frescura.png`. Pass when the response reports unknown freshness and no invented maturity window.
- [ ] Lane 10. Restart FastAPI and repeat the all-channel request. Save `api-reinicio.png`. Pass when responses remain identical and contain no mixed-currency total.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Measure total response time and SQL statement count for the existing Amazon 30-day series request at trunk and head. Also measure the new three-channel 90-day summary.
- [ ] Probe. Interleave 30 warm trunk/head requests against identical Amazon data. Run 30 new summary requests against a fixture with three channels and 96 queried days including MA7 warm-up.
- [ ] Baseline. Record trunk median, p95, and statement count first.
- [ ] Rule. Fail if Amazon p95 regresses by more than 10 percent or statement count grows per channel in the legacy route. Fail if the three-channel 90-day p95 exceeds 500 ms on the launch VM or its statement count grows with the number of visible days.

**Review gate.** None. RESUMEN-ADS-02 changes JSON but does not change an operator interaction.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Keep the verified patch-id and append the PR to the stack.
- [ ] Stop at stack-ready. The operator lands it bottom-up after its parent.

## Rediseñar la interfaz del Resumen (RESUMEN-ADS-03)

**Depends on.** RESUMEN-ADS-02.

**Files.**

- [ ] Modify `app/ui.py` to parse GET filters and call the single summary contract.
- [ ] Replace `app/templates/resumen.html` with filters, KPI cards, comparison, trend controls, channel charts, comparison table, and accessible textual summaries.
- [ ] Modify `app/static/js/dashboard.js` for raw and MA7 datasets, comparison display, theme updates, and chart replacement without stale state.
- [ ] Modify `app/static/css/dashboard.css` for filter layout, responsive charts, table overflow, and visible focus.
- [ ] Modify `app/templates/base.html` and `app/static/js/shell.js` only where Amazon-only copy or freshness would contradict the multichannel page.
- [ ] Modify `.cursor/skills/verify-orbit/helpers/orbit-verify` to drive filters and capture desktop and mobile evidence. Do not create UI test files or test the verification helper.
- [ ] Modify `docs/DASHBOARD.md` with the operator workflow and metric definitions.

**Build.**

- [ ] Capture the current UI in the browser before editing. Do not add server-rendered, JavaScript, copy, or helper tests. Verify the new behavior through the live browser lanes below.
- [ ] Implement GET filters with a `fieldset`, associated labels, and server validation. Default to all channels and 30 days. Preserve selected values after submission. Use URLs such as `/?periodo=30&platform=amazon_mx&platform=meli&comparar=1&tendencia=ma7`. A custom range uses `desde=YYYY-MM-DD&hasta=YYYY-MM-DD` and excludes the current day through the API.
- [ ] Render the API totals without recalculating them in Jinja. Show spend, ad revenue, ACoS, ROAS, clicks, and attributed orders when supported. Show previous-period deltas only when the API supplies them. Keep currency and coverage next to each channel total.
- [ ] Render one spend/revenue chart and one ACoS chart for the selected channel or an aggregated-by-currency view that never mixes currencies. For all channels, include the comparison table and weekly or monthly channel composition. Keep native currencies separate in every visual.
- [ ] Add raw daily lines as low-emphasis context and MA7 as the prominent trend. Make the selected mode explicit. Keep null gaps, zero-sales markers, theme tokens, local Chart.js, JSON inert payloads, and disabled animation. Never mark MeLi with Amazon D-8 maturity.
- [ ] Add a textual equivalent for each chart and a keyboard-scrollable comparison table. At 390 px, keep controls usable, prevent page-level horizontal overflow, and allow only the table region to scroll horizontally.
- [ ] Extend the real browser driver to submit filters, toggle trends, read effective dates, inspect rendered canvas state, and save 1280 px and 390 px screenshots. Do not replace interaction checks with static HTML assertions.
- [ ] Run the existing suites with `uv run --frozen python -m pytest -q tests/test_ui.py tests/test_ui_metricas.py tests/test_verify_orbit.py`. If an old assertion pins removed UI, delete that stale assertion without adding a replacement text test. Confirm the remaining existing cases pass.
- [ ] Commit the verified UI with `git commit -m "RESUMEN ADS 03: tendencias y filtros multicanal"`.

**You see.**

- [ ] Opening `/` shows all connected ad channels, a 30-day period, native-currency KPI, and MA7 trends.
- [ ] Selecting 90 days updates the effective dates, KPI, comparisons, and charts without a hardcoded `30d` label.
- [ ] Selecting Mercado Libre shows its real advertising metrics and its own freshness without Amazon maturity markings.
- [ ] Missing values remain visible as gaps or `—`, while known zeros remain `0`.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Run `uv run --frozen python -m pytest -q tests/test_ui.py tests/test_ui_metricas.py tests/test_verify_orbit.py`. Pass when all remaining existing cases pass and no new UI test files exist.
- [ ] Run the real `orbit-verify` browser flow against the head. Save its proof and screenshots. Do not substitute static assertions for the observed interactions.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.7-xhigh-fast` at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Open `/` with the same Amazon fixture at trunk and head. Save `ui-amazon-regresion.png`. Pass when head preserves Amazon totals and adds working period and trend controls.
- [ ] Lane 2. Select 7, 30, and 90 days through the UI. Save `ui-periodos.png`. Pass when each selection updates effective dates, KPI, and chart point counts.
- [ ] Lane 3. Submit a custom range ending today. Save `ui-personalizado.png`. Pass when the effective range ends at D-1 and the selected dates remain visible.
- [ ] Lane 4. Toggle daily and MA7 views. Save `ui-tendencia.png`. Pass when raw values remain available, MA7 becomes prominent, and null gaps remain gaps.
- [ ] Lane 5. Enable previous-period comparison. Save `ui-comparacion.png`. Pass when equal-period deltas appear and unknown deltas display `—`.
- [ ] Lane 6. Select all channels and inspect the table. Save `ui-multicanal.png`. Pass when Amazon MX, Amazon US, and MeLi appear with MXN separate from USD and no grand total mixes currencies.
- [ ] Lane 7. Select only MeLi with missing freshness. Save `ui-meli-sin-frescura.png`. Pass when real MeLi metrics remain visible and freshness says unknown without D-8 shading.
- [ ] Lane 8. Drive the page at 390 px using keyboard only. Save `ui-movil-teclado.png`. Pass when every control is reachable, focus is visible, and the page has no horizontal overflow.
- [ ] Lane 9. Toggle light and dark themes after changing filters. Save `ui-temas.png`. Pass when charts use current theme tokens and no stale chart remains.
- [ ] Lane 10. Use zero revenue, zero spend, missing days, and malicious campaign-name fixtures. Save `ui-nulos-xss.png`. Pass when zeros and gaps remain distinct, no script executes, and textual chart equivalents remain readable.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Measure navigation response time, browser time to `DOMContentLoaded`, and transferred bytes for `/` with the same Amazon 30-day fixture at trunk and head. Also measure the new three-channel 90-day page.
- [ ] Probe. Interleave 30 warm trunk/head browser navigations on identical VMs and data. Record median and p95. Measure the three-channel page separately without comparing its payload to the smaller trunk page.
- [ ] Baseline. Record trunk server p95, browser p95, and transferred bytes first.
- [ ] Rule. Fail if head Amazon browser p95 regresses by more than 10 percent or 200 ms, whichever is smaller. Fail if the three-channel 90-day page exceeds 2 seconds to `DOMContentLoaded` or 1 MB transferred on the launch VM.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 2, lane 4, lane 6, and lane 8 screenshots into `docs/evidencia/resumen-ads-multicanal/03-ui/resumen-ads-03-review-<slug>.png`.
- [ ] Record a 30 to 60 second video of period selection, trend toggle, channel filtering, comparison, and mobile layout. Save it as `docs/evidencia/resumen-ads-multicanal/03-ui/resumen-ads-03-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at stack-ready and wait for the operator's explicit approval.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Keep the verified patch-id and append the PR to the stack.
- [ ] Stop after UI approval at stack-ready. The operator lands it last.

## Close the program

- [ ] Every box above is checked with its evidence.
- [ ] Run `uv run --frozen python -m pytest -q` once on the final stack head, or reuse CI evidence for that exact SHA.
- [ ] Run `pre-commit run --all-files` once on the final head if push hooks or CI have not already validated that SHA.
- [ ] Reconcile the deployed MeLi dashboard against accounting after deployment and save the read-only queries and results.
- [ ] Reply to the operator with the stack root and tip links, one verdict per PR, review evidence, deployed SHA, reconciliation result, and any excluded scope.

## Appendix A. Prototype evidence

No prototype ran. The operator approved the information architecture and interaction in conversation and requested a plan without implementation. Visual taste remains unproven until `RESUMEN-ADS-03` produces screenshots and video.

The accounting source schema, correction semantics, freshness, and production coverage remain unproven. `RESUMEN-ADS-01` must inspect them before fixtures or migrations. A failed source gate stops execution and requires a plan revision.

## Appendix B. Alternatives rejected

The approved scope is advertising only. It excludes organic sales, general profitability, and a second business-overview tab.

The design uses daily context plus a seven-day moving average. Raw-only charts lost because they hide direction behind daily spikes. Smoothing-only charts lost because they hide the observed values.

The design keeps totals in native currencies. A global MXN or USD total lost because it requires an explicit FX policy and changes the approved advertising comparison into a financial reporting feature.

The design reuses `ads_metric_observation` if the source gate passes. A parallel MeLi metrics table lost because it would create a second source for the same campaign/day facts. Staging remains justified only if inspection disproves compatible grain or correction identity.

The design creates a Resumen channel catalog. Extending `PLATAFORMAS_MONEDA` lost because that dictionary governs the Amazon optimizer, cycles, settings, and health checks.

Shopify is excluded from implementation. The channel response avoids Amazon-specific labels so a future source can be added deliberately, including its database enum and metric contract.

## Appendix C. Risks

`RESUMEN-ADS-01` owns source grain, attribution semantics, deterministic corrections, missing-day meaning, and MeLi maturity. The owner inspects production before fixing the adapter contract.

`RESUMEN-ADS-01` owns duplicate history and incorrect latest values. The owner tests same-snapshot retries and corrected source versions against `v_metric_latest`.

`RESUMEN-ADS-02` owns mixed currencies, ratio averaging, MA7 warm-up, and anti-double-counting. The owner tests each invariant through the public response.

`RESUMEN-ADS-03` owns misleading smoothing, inaccessible filters, mobile overflow, stale charts, and XSS. The operator reviews real browser evidence before landing.

`control-ui` was not present in the available skill list when this plan was written. The execution owner must confirm its availability before browser lanes. The existing `orbit-verify` helper can drive Chrome, but the plan does not claim that it supplies all `control-ui` capabilities.

No production query ran during planning. The local cron evidence proves configuration on 2026-10-07, not present freshness or successful synchronization.

## Appendix D. Links and reading list

Read `docs/CONTEXTO.md`, `docs/DATABASE.md`, `docs/DEPLOY.md`, and `docs/DASHBOARD.md` before editing.

Read `app/ledger.py` for snapshot, transaction, run sealing, and redaction patterns. Read `app/api_dashboard.py`, `app/ui_metricas.py`, `app/ui.py`, `app/templates/resumen.html`, and `app/static/js/dashboard.js` for the current behavior.

Read `migrations/0001_initial.sql` for append-only observations, dedupe, currency guards, and `v_metric_latest`. Read `migrations/0036_ingest_run_platform.sql` for source health metadata.

Use `/Users/dn/.agents/skills/how/SKILL.md` for subsystem questions in every PR. Use `/Users/dn/.agents/skills/interrogate/SKILL.md` before `RESUMEN-ADS-02` seals the response contract and before `RESUMEN-ADS-03` review if the design is contested.

Keep the decision trail required by `/Users/dn/.agents/skills/show-me-your-work/SKILL.md`. Store run evidence under `docs/evidencia/resumen-ads-multicanal/`.
