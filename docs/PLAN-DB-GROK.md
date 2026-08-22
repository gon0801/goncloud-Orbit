# Plan de implementación — correcciones del esquema (ronda cross-review grok)

> **Para quien implementa (deepseek):** repo `C:/dev/goncloud-Orbit`, Windows,
> Git Bash. Archivos a tocar: `migrations/0001_initial.sql`, `pyproject.toml`,
> `docs/DATABASE.md`, y si algo cambia de invariantes, `tests/test_schema.py`.
> Contexto de diseño: `docs/CONTEXTO.md` (reglas innegociables) y
> `docs/DATABASE.md` (diseño vigente). Estilo: COMMENT ON con la justificación
> histórica de cada regla, español.
>
> **REGLAS DE TRABAJO — no negociables:**
> 1. **NO hagas `git add` ni `git commit`.**
> 2. **NO reescribas el archivo SQL de corrido.** Ediciones quirúrgicas con
>    Edit. Ya pasó dos veces que una reescritura revirtió correcciones ajenas
>    en silencio. Al terminar, corre `git diff` y revisa que SOLO cambiaste lo
>    de este plan.
> 3. Verificación final obligatoria (reportar salidas literales):
>    `./.venv/Scripts/python.exe -c "import pglast; print(len(pglast.parse_sql(open('migrations/0001_initial.sql',encoding='utf-8').read())))"`,
>    `./.venv/Scripts/python.exe -m pytest -q`,
>    `python -m pre_commit run --all-files`.
> 4. Si un punto del plan no aplica porque el código ya no es como se describe,
>    NO improvises: repórtalo como discrepancia y sigue con el siguiente.

Hallazgos validados uno por uno contra el SQL real (grok revisó un diff
truncado; cada punto abajo ya fue confirmado o descartado por mí).

---

## D1 [alta] — Restaurar cruce de plataforma en `search_term_observation` (REGRESIÓN)

**Validado:** el trigger `metric_moneda_de_plataforma` (~línea 390-420) hoy
hace `v_platform := NEW.platform` en la rama ELSE sin cruzar contra
`ad_entity.platform`. La corrección original existía y una reescritura la
revirtió. Consecuencia: una fila puede declarar `amazon_us` colgada de una
entidad `amazon_mx`, sellar la moneda equivocada, y la PK (que incluye
`platform`) admite el mismo hecho dos veces.

**Hacer:** en la rama ELSE de la función, cruzar contra la entidad:

```sql
ELSE
    SELECT e.platform INTO v_platform
      FROM ad_entity e
     WHERE e.id = NEW.ad_entity_id;
    IF v_platform IS DISTINCT FROM NEW.platform THEN
        RAISE EXCEPTION
            'search_term_observation: la fila declara plataforma % pero '
            'su entidad % es %.', NEW.platform, NEW.ad_entity_id, v_platform
            USING ERRCODE = 'check_violation';
    END IF;
END IF;
```

y restaurar el comentario que lo explica (la columna está desnormalizada; la
fila que declara otra plataforma es un bug de ingesta que se rechaza aquí).

## D2 [alta, parcialmente residual declarado] — Sellar `apply_quota_state`

**Validado:** `used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0)` y
`cap INTEGER NOT NULL CHECK (cap > 0)` (~línea 736-742). No hay
`CHECK (used <= cap)`, y `app_admin` no tiene INSERT (la fila del día la crea
el motor copiando el cap desde `config_version` — esto ya está DECLARADO en el
COMMENT y en DATABASE.md; no es hallazgo nuevo, pero el sellado sí falta).

**Hacer:**
- Agregar `CONSTRAINT quota_no_excedida CHECK (used <= cap)` a la tabla
  (backstop; el consumo atómico `WHERE used < cap` de la app sigue siendo la
  defensa de carrera). OJO: esto hace que el ORDER importe en la app —
  consumir es `UPDATE ... SET used = used + 1 WHERE used < cap`, nunca blind.
- Agregar `GRANT INSERT ON apply_quota_state TO app_admin;` (el admin sí puede
  fijar caps manualmente; el COMMENT ya lo da a entender).
- Documentar en el COMMENT que "used nunca decrece" no es enforceable por
  CHECK (requeriría comparar contra el valor viejo) y queda cubierto por el
  patrón de consumo atómico + la auditoría de `decision`.

## D3 [media] — `v_tacos`: ventanas simétricas y sin supuesto de moneda única

**Validado** (~línea 1161+): `gasto` lee de `v_metric_mature` (corte D−15)
pero `venta` toma TODAS las ventas del mes → el mes en curso sale con TACoS
sistemáticamente bajo (optimista: la dirección "todo se ve rentable"). Además
el FULL OUTER JOIN por (platform, mes) repite el gasto si un mes tuviera dos
monedas de venta (hoy solo declarado como supuesto en el COMMENT).

**Hacer:** reescribir la vista para que:
1. `venta` también corte a `event_date <= corte_madurez` (mismo D−15 que
   `gasto`, vía `v_metric_mature` o la misma expresión) — ventanas simétricas.
2. Cada lado se convierta por fila a una moneda canónica (MXN) con
   `fx_resolve`, y el JOIN sea por (platform, mes) sobre montos ya en MXN —
   así desaparece el supuesto de moneda única: si hubiera dos monedas de
   venta, ambas se convierten y se SUMAN, no duplican gasto. Sin tasa → ese
   monto queda fuera y `tacos_pct` sale NULL si falta cualquier lado
   (fail-loud, como hoy).
3. Actualizar COMMENT ON VIEW y la sección de vistas en DATABASE.md.

## D4 [media] — `CURRENT_DATE` en vistas es TZ-dependiente

**Validado:** `v_metric_mature` usa `CURRENT_DATE - 15`; se evalúa según el
TimeZone de la sesión (misma clase de problema que llevó la madurez a un
trigger con UTC fijado).

**Hacer:** en `v_metric_mature` (y cualquier otra vista con `CURRENT_DATE`),
usar `(now() AT TIME ZONE 'UTC')::date - 15` y documentar que la defensa real
es UTC fijado en la expresión (coherente con `decision_madurez_corte`).

## D5 [media] — `v_margen_plataforma`: COGS y venta sobre poblaciones distintas

**Validado:** el CTE de ventas/COGS incluye ventas con `order_id` NULL, pero
`por_orden.venta` solo suma `order_id IS NOT NULL` → con cobertura 100% el
margen resta COGS de ventas que no están en `venta`.

**Hacer:** `venta` debe ser TODAS las ventas (con o sin `order_id`) — sacar el
`SUM(amount) FILTER (WHERE kind='sale')` del CTE `por_orden` a un agregado sin
filtro de orden, manteniendo `cargos_con_orden` / `cargos_sin_orden` como
están. El comentario debe decir: venta total sin supuesto de atribución; los
cargos sí se separan por atribuible/no atribuible.

## D6 [media] — Declarar el hueco "ISR ausente = 0" (no es fix de schema)

**Validado:** `cargos_sin_orden` hace `COALESCE(..., 0)`; si el ISR no se
ingirió, se ve como cero (regla 3: dato faltante disfrazado de número). No se
puede resolver por schema (la ausencia es indistinguible del cero sin fuente
externa) — lo que lo atrapa es `external_reconciliation`.

**Hacer:** solo documentación — COMMENT en la vista y párrafo en DATABASE.md:
"`cargos_sin_orden = 0` puede significar 'no llegó', no 'no hubo'. Lo atrapa
la conciliación externa contra settlement reports, no esta vista."

## D7 [media] — `ad_entity`: UPDATE completo permite mutar platform/kind

**Validado:** `GRANT UPDATE ON product, listing, ad_entity TO app_ingest`
(~línea 1244). Mutar `platform`/`kind` tras insertar hechos rompe el sello de
moneda a posteriori y deja goals apuntando a kinds que ya no son campaign.

**Hacer:** cambiar a `GRANT UPDATE (name, listing_id) ON ad_entity TO
app_ingest;` (sacar ad_entity del UPDATE genérico). `platform`, `kind`,
`external_id`, `parent_id`, `match_type`, `keyword_text` se fijan en el INSERT
y son inmutables por permisos. Si la ingesta necesita corregir una entidad,
crea una nueva y desactiva la vieja — documentarlo en el COMMENT de la tabla.

## D8 [media] — Hueco de dedupe en `ledger_event` (con orden, sin source id)

**Validado:** `ledger_dedupe_source` cubre `source_event_id IS NOT NULL`;
`ledger_dedupe_sin_orden` cubre ambos NULL. Una fila con `order_id` NOT NULL y
`source_event_id` NULL (un re-sync de fees con orden pero sin id de fuente) no
cae en ninguno → duplica en silencio.

**Hacer:** tercer índice único parcial:

```sql
CREATE UNIQUE INDEX ledger_dedupe_con_orden
    ON ledger_event (platform, kind, order_id, fee_type, event_date, amount, amount_currency)
    NULLS NOT DISTINCT
    WHERE source_event_id IS NULL AND order_id IS NOT NULL;
```

con COMMENT: entre las tres claves queda cubierto todo el espacio
(source_event_id / order_id / ninguno).

## D9 [media] — `pglast` no declarado en dependencias

**Validado:** `pyproject.toml` dev group tiene pytest/httpx/ruff; pglast se
instaló a mano en el venv. En un install limpio `tests/test_schema.py` falla
en el import — el candado que "corre siempre" no arranca.

**Hacer:** agregar `"pglast>=6"` al `[dependency-groups] dev` de
pyproject.toml. (`psycopg` ya está en dependencies principales.)

## D10 [baja] — `revenue_same_sku` fuera del CHECK de no-negativos

**Validado:** `metric_no_negativos` (~línea 286-292) cubre cost, ad_revenue,
impressions, clicks, orders — falta `revenue_same_sku`. Revisar también
`st_metric_no_negativos` en search_term_observation (que cubra orders y
ad_revenue, no solo cost).

**Hacer:** agregar `(revenue_same_sku IS NULL OR revenue_same_sku >= 0)` (y lo
que falte en la tabla de search terms) al CHECK correspondiente.

## D11 [baja] — `harvest_job` acepta nacer en `done`/`failed`

**Validado:** el trigger `harvest_job_decision_coherente` valida coherencia
con la decisión pero no la fase inicial; el único en-vuelo no protege el POST
si la fila nunca nace en `pending`.

**Hacer:** en la función del trigger (o uno nuevo BEFORE INSERT), exigir
`NEW.fase = 'pending'` en INSERT — las fases posteriores solo se alcanzan por
UPDATE. Documentar: el job se registra ANTES del primer POST a Amazon
(fail-closed ante crash), siempre.

---

## Verificación de cierre (reporte obligatorio)

1. `git diff --stat` — solo los 3-4 archivos de este plan.
2. Parseo pglast (comando de arriba): sin errores; reportar número de
   statements (hoy: 140).
3. `pytest -q`: 11+ passed, 1 skipped. Si agregaste invariantes a
   test_schema.py, reportar el nuevo conteo.
4. `pre_commit run --all-files`: todos los candados en verde.
5. Si algún punto fortalece un invariante que test_schema.py ya afirma (D1,
   D7, D8, D10 son candidatos), considera agregar el test estático
   correspondiente — es la lección de esta ronda: cada corrección deja su
   candado.
