# Orbit — contexto del proyecto

> **Stack (decidido 2026-08-22):** Python + FastAPI + PostgreSQL (en Docker, en
> el servidor propio junto a `bridge` y `accounting`). Sin Redis/colas hasta
> que la carga real lo justifique. La sugerencia de Supabase quedó descartada:
> dependencia externa, latencia para los crons y costo, sin necesidad de sus
> extras (auth, realtime) para un sistema interno de motores.

> Sistema **nuevo, construido desde cero** sobre una **base de datos nueva**.
> Reemplaza al stack de motores de Amazon Ads (`goncloud-MCP-2`, **cancelado el
> 2026-08-21**). **No se reutiliza código del sistema viejo.** Lo único que se
> migra son credenciales, datos verificados como limpios, y las lecciones.
>
> Documentos fuente (copia verbatim, leer antes de diseñar):
> - `docs/traspaso/TRASPASO-1-ACCESOS-E-INFRAESTRUCTURA.md` — qué credenciales
>   existen, dónde viven, qué rotar, qué datos son migrables.
> - `docs/traspaso/TRASPASO-2-AUTOPSIA-Y-CIMIENTOS.md` — por qué murió el
>   sistema viejo, trampas del dominio, reglas de diseño. **La parte 3
>   (trampas del dominio) es obligatoria**: un sistema nuevo perfecto se
>   estrella contra todas ellas igual.
> - `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md` — especificación de reglas y
>   umbrales del optimizador (escrita contra el repo viejo: ver "Cómo leer el
>   diseño v2" abajo).
> - `docs/traspaso/MODULOS-AVANZADOS.md` — spec de los 5 módulos avanzados
>   (Repricing, Campañas, Reputación, Promociones, Envíos). También se
>   construyen en Orbit — ver "Módulos avanzados" abajo.

## Qué es Orbit

Optimizador de Amazon Ads (Sponsored Products) con decisiones explícitas y
auditables: **cuánto pujar, qué pausar, qué harvestear**. Una decisión por
entidad por ciclo, todo registrado en tabla de auditoría, live automático con
topes. Nada de gates evidenciales apilados ni cooldowns implícitos: fue lo que
mató al stack viejo (94% de las decisiones con menos evidencia de la que el
propio motor exigía; un gate `live` que nunca disparó en 220,494 decisiones).

## Qué se migra del sistema viejo (y qué NO)

**SÍ se migra / reusa:**

- **Credenciales e integraciones** (Traspaso 1 §1, §5): tokens de Amazon Ads
  (misma app LWA → no hay que rehacer OAuth), MeLi, Odoo, SP-API, Keepa, Apify,
  Anthropic, Telegram. Cadena de fallback vieja: env → archivo JSON → fila en
  DB — al migrar hay que mirar **los tres lugares**. Ningún secreto se escribe
  en este repo.
- **Datos verificados limpios** (Traspaso 2 §4): las 4 tablas de métricas de
  ads (0 duplicados, cero huecos de días), `currency_rates` (diaria desde
  2025-10-31), `ledger_events` (moneda 100% verificada), precios de
  `product_economics_expected`, `own_items.current_qty` de Amazon.
- **`bridge` y `accounting`** son sistemas independientes, vivos y sanos: no se
  tocan, se integran.
- **El mapa de cadencias reales** de cada API externa.

**NO se migra:**

- Código de los motores viejos (62 módulos, 206 flags, 147 jobs para tomar 3
  decisiones). Las "primitivas de escritura" que el diseño v2 marca como
  reutilizables se **reescriben** en Orbit con las mismas precauciones (retries
  429/5xx, POSTs de creación no-idempotentes fail-closed).
- `sales_history.cogs` de MeLi (49% sin costo, y el cero se disfraza de dato),
  `snapshots`, tablas de backup, `decision_audit` (historia muerta desde
  2026-05-19), `own_items.current_qty` de MeLi.

## Cómo leer el diseño v2 (ADS_OPTIMIZER_V2_DESIGN.md)

Ese documento es la **fuente de verdad de reglas y umbrales** (bids, hygiene,
harvest, guardas de ciclo, cascada de target ACoS, escalera off→shadow→live).
Se adopta completo **como especificación**, con dos traducciones:

- La tabla "Qué reutiliza (no reescribe)" apunta al repo viejo: en Orbit eso
  se implementa nuevo (ingesta date-grained con upsert replace-por-fecha, HTTP
  con retries, envelope de ciclo, etc.).
- Los nombres de módulos (`engines/ads_optimizer_*.py`) son guía de
  arquitectura, no obligación de layout.

Spec delta ORBIT 03 (sellado; tres traducciones nuevas que el diseño v2 no
conocía):

1. **Doble ventana.** Bids: ventana 30d que termina en `max(metric_date) − 3d`.
   Cortes (pause/negative/harvest): agregado SEPARADO cuya ventana termina en
   `min(max(metric_date) − 3d, decided_at − 10d)` — madurez ≥10d, es decir
   `window_end <= decided_at − 10d` (regla 6; el trigger
   `decision_madurez_corte` lo hace imposible de violar). Prohibido calcular
   con la ventana de bids y solo "bajar" la columna `window_end`.
2. **Opt-out por campaña.** `campaign_optimization_state` del viejo no existe
   en Orbit: se traduce a goal de scope campaña con `enabled=false`, que PISA
   a un goal de plataforma habilitado. La gracia de 7d por reactivación manual
   sigue diferida a PR2 (residual #3 del diseño).
3. **Criterio de shadow para el cutover.** "Revisado contra lo que hizo
   adaptive" ya no aplica: el sistema viejo está APAGADO (ORBIT 02). El shadow
   se valida contra datos reales + recálculo manual, y se acepta el costo
   declarado de que nadie optimiza las campañas durante el freeze.

Reglas numéricas selladas (resumen; el documento manda):

- **PAUSE**: orders=0 ∧ clicks≥25 ∧ cost≥{us: 12 USD, mx: 200 MXN}
- **−25%** si ACoS > 1.35×target (orders≥1); **−12%** si > 1.15×target;
  **+15%** si ACoS < 0.85×target ∧ orders≥3. Clamp por decisión ∈ [−30%, +20%],
  resultado ∈ [floor, ceiling] (defaults 0.10/2.50).
- **NEGATIVE_EXACT**: orders=0 ∧ clicks≥20 ∧ cost≥{us: 8, mx: 130}; términos
  ASIN-like siempre skip.
- **HARVEST**: orders≥2 ∧ ACoS ≤ min(35%, target); requiere config de campaña
  manual en el goal, sin placeholders.
- Guardas: solo campañas con goal habilitado; frescura (ventana termina en
  `max(metric_date) − 3d`); completitud ≥7 fechas por entidad; claim con TTL;
  cooldown 7d solo cuenta applies verificados.

## Reglas de diseño innegociables (Traspaso 2 §5)

Cada una tiene atrás dinero perdido. Aplican a todo el código de Orbit:

1. **Una decisión, un camino, un dueño.** Lo que no está en el camino de una
   decisión que importa, no se construye.
2. **Un número, una fuente.** (El motor viejo usó 10 márgenes distintos en un
   día desde 3 fuentes.)
3. **Dato faltante = `None` y la fila no se escribe.** Nunca una constante
   inventada (el FX de fallback a 20.5 infló revenue +28,549 MXN).
4. **Todo dinero lleva `(valor, moneda, fecha_fx)` por schema**, y se guarda
   siempre el importe original además del convertido (la conversión es
   irreversible). Un `SUM()` mezclando monedas debe ser imposible por schema.
5. **Toda métrica es append-only con clave `(entidad, metric_date,
   observed_at)`.** El UPSERT in-place del sistema viejo hacía que el backtest
   viera números que el motor jamás pudo ver (lookahead sin síntomas — la
   trampa más grave para un sistema nuevo).
6. **La edad mínima del dato depende del tipo de decisión**: cortar/pausar
   exige ≥10 días de maduración (a día 0 el corte tiene 8.7% de falsos
   positivos; a día 10, 0.0%). El día en curso se descarta (tiene ~20% del
   costo y ~12% del revenue finales).
7. **Ninguna acción irreversible sin su reversa implementada antes** de
   encenderse.
8. **Antes de escribir el test de un invariante, correr el `SELECT` que
   confirma la forma real del dato en producción** (la suite vieja probaba
   `NULL` donde producción escribe cadena vacía).
9. **Toda prueba de regresión debe demostrarse fallando contra el código
   anterior** — si pasa antes y después, no prueba nada.
10. **Conciliar contra la fuente externa** (reportes de liquidación de Amazon),
    no contra la propia consistencia interna.

## Trampas del dominio que Orbit hereda intactas

No dependen del código. Resumen mínimo (detalle y números en Traspaso 2 §3):

- **Tres relojes desalineados**: venta atribuida madura a 5–8 días, costo al
  día 15 (madura *hacia abajo* por clawback), fees del P&L a 15–30 días. Un
  ACoS de día 1 en MX sale ~1.5× peor que el real.
- **Halo**: 56–58.5% del ingreso atribuido es de otros SKUs (Amazon; MeLi
  reporta 0). Es atribución de Amazon, **no causalidad**. Numerador de
  ROAS/POAS = `ad_revenue` completo; `sales_same_sku` solo para atribuir.
- **Tablas point-in-time**: jamás consultar sin filtro de vintage explícito
  (inflan 13–17× e invierten el signo de tendencias).
- **Monedas**: `sales_history` reportaba MXN hasta para amazon_us → error de
  18.66× siempre a favor de "todo es rentabilísimo".
- **ISR de Amazon nunca trae `order_id`** y llega en bultos quincenales: los
  costos sin order_id se prorratean explícitamente o se excluyen por escrito.
- **MeLi es estructuralmente incomparable con Amazon** (cero halo, lag de 1
  día, ISR con order_id, escritura de ads bloqueada a nivel cuenta → MeLi Ads
  es proposal-only).
- **La pregunta sin respuesta**: la cuenta US da entre +1,671 y −2,238 USD en
  91 días según se cuente o no el halo — **ni el signo se conoce**. Decisión
  de diseño pendiente ANTES de la lógica de decisión: acotar con ambos
  supuestos, holdout, o **decidir por TACoS** (la más barata; no necesita
  suposición de atribución).

## Integraciones externas (cadencias y trampas pagadas)

- **Amazon Ads API**: reporting v3 asíncrono (pipeline de métricas); escritura
  vía campaign management v3 (lo más frágil); los endpoints v4 unificados
  **rechazan Bearer LWA**.
- **Amazon Marketing Stream + SQS**: única fuente intra-día; sirve para
  alertas de gasto, no para decisiones (la atribución tiene lag de días).
- **SP-API**: Orders v0 deprecado (migración a `2026-01-01`, dos bugs de
  paginación documentados); Finances `2024-06-19` es la fuente de fees (la
  migración vieja perdía el ISR).
- **MeLi**: un solo refrescador de token (el viejo tenía dos compitiendo);
  rutas de ads llevan `site` (`/advertising/MLM/...`).
- **Odoo 17**: siempre mandar `tax_ids` o IVA doble.
- Shopify, Google Ads y Meta **no existen** (esqueleto sin cuenta en el viejo):
  no buscar esas credenciales.

## Estado del servidor viejo (para la migración de credenciales)

- Hay motores en **live** escribiendo a Amazon Ads; el scheduling real son 147
  jobs de APScheduler dentro del contenedor, **no el cron**. Apagado seguro:
  flags a `off` → `systemctl disable --now competitive-intel.service` →
  `docker compose stop` → recién entonces limpiar cron (Traspaso 1 §0).
- Antes de apagar: copiar `competitive.db` fuera de la rotación de respaldos
  (solo guarda 3), usando la API `.backup()` de SQLite (el `cp` deja fuera el
  WAL).
- Pendiente de rotación al cerrar el viejo: `API_AUTH_SECRET` (filtrado),
  deploy key `mcp2_deploy` (tiene escritura), token de cloudflared (inline en
  systemd), 4 backups de `accounting.env` con secretos.

## Módulos avanzados (spec: `docs/traspaso/MODULOS-AVANZADOS.md`)

Además del optimizador de ads, Orbit construye 5 módulos: **Repricing**,
**Creación de Campañas por API**, **Reputación**, **Promociones** y
**Envíos** (roadmap del documento: Fase 0 modelos+auth → Repricing → Campañas
→ Promociones → Reputación → Envíos → integraciones). La spec manda en
alcance y modelos; donde roza con las lecciones de la autopsia, manda la
autopsia. Reconciliaciones explícitas:

- **Stack decidido:** Python + FastAPI + PostgreSQL (ver arriba). La spec
  sugiere Redis + BullMQ "a título orientativo": queda fuera hasta que la
  carga real lo justifique — la autopsia mata infraestructura por defecto (el
  sistema viejo tenía 62 módulos y 147 jobs para 3 decisiones).
- **Repricing y Promociones viven del margen** → aplican las reglas 2, 3 y 4:
  una sola fuente de margen, dato faltante = fila no escrita, todo dinero con
  `(valor, moneda, fecha_fx)`. Ojo: los fees maduran a 15–30 días, así que el
  "margen proyectado" del simulador es una estimación con edad declarada, no
  un número exacto.
- **"Confirmación humana para acciones de alto impacto"** (spec §10.4)
  convive con el "live automático con topes" del optimizador: el optimizador
  actúa solo dentro de sus caps; cambios masivos de precio (>50 productos),
  borrado de campañas y promociones bajo el margen mínimo requieren
  confirmación.
- **Campañas en MeLi**: la escritura de MeLi Ads está bloqueada a nivel cuenta
  (proposal-only, Traspaso 1 §4). El Módulo 2 en MeLi genera propuestas, no
  crea vía API, hasta que la cuenta lo permita.
- **Reputación → pausar campañas** es una acción de corte: aplica la regla 7
  (reversa implementada antes de encender) y la edad mínima del dato según el
  tipo de decisión.
- **Trazabilidad completa** (spec) se implementa con las tablas de auditoría
  append-only de la regla 5: `(entidad, fecha, observed_at)`, nunca UPSERT
  in-place.

## Fases (adoptadas del diseño v2)

1. **PR 1 — shadow completo**: decisiones + auditoría + envelope + router +
   cron + flag. Cero escrituras a Amazon.
2. **PR 2 — apply**: caps diarios + reserva PAUSE, audit INSERT+COMMIT → HTTP
   → readback → UPDATE, harvest con fases y reconciliación, checklist de
   cutover (≥2 semanas de shadow revisadas, goals piloto, caps bajos día 1,
   off→shadow→live, monitoreo 48h).
3. **Fase 3 — consolidación**: digest diario por Telegram, vista de lectura,
   margin-aware targets.
4. **Fase 4 — señales, no gates**: cada señal nueva debe demostrar en shadow
   que AUMENTA la tasa de acción útil; máximo comportamiento: abstenerse.
5. **Fase 5 — palancas nuevas, una a la vez**, cada una apagable sin romper el
   resto.

El roadmap de los 5 módulos avanzados corre **después** del PR 2 del
optimizador (necesita la base de márgenes sana y la auth unificada
Amazon+MeLi). Su Fase 0 (modelos + auth) se puede solapar con el shadow del
PR 1.
