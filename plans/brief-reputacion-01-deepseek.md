# Brief para DeepSeek: plan formal del módulo Reputación (REPUTACION 01)

> **SUSTITUIDO 2026-09-07:** el lead redactó directo `plans/reputacion-01.md`.
> Se conserva como antecedente; no entregar a implementadores.

Redacta **solo el plan formal** `plans/reputacion-01.md`. Nada de código, nada de
migraciones, nada de deploy. El lead revisa el plan, el dueño cierra las
decisiones abiertas, y la implementación llega después en otro brief.

## Por qué tú

Superficie delgada de lectura + documentos: tu carril. El plan describe ingesta
read-only, snapshots, alertas y una pantalla. No hay motor de decisión ni
escrituras a Amazon/MeLi en v1.

## Alcance v1 (fijo, no ampliable sin el dueño)

**Dentro:** snapshots diarios de rating/conteo por publicación (Amazon MX/US,
MeLi); reviews con texto completo donde la fuente lo dé; reputación del vendedor
MeLi; preguntas MeLi en lectura; alertas (rating bajo, 1 estrella, subida de
reclamos, caída sostenida); pantalla `/reputacion` (el sidebar ya lista
Reputación como próxima, `app/templates/base.html:57`).

**Fuera:** cualquier acción automática (pausar/bajar pujas por rating — exige
contrato, evidencia y reversa y no es v1); respuestas automáticas a preguntas o
reviews (v2, con checkpoint humano como el viejo `meli_questions.status`);
detección de reviews falsas; descubrimiento/competidores (era `discover_*`,
sistema aparte); Buy Box intradía y Account Health salvo que salgan de una
fuente oficial barata (si no, v2).

## Hechos verificados (úsalos, no los re-derives)

Base: `origin/master` `1395a9b` (PR #192). ORBIT 19 en B.6 (cierre); este plan
no toca sus archivos ni fija número de migración (posterior a 0019–0022, el
número se fija al aplicar, como F2 de FABRICA 01).

El sistema anterior (competitive intel, apagado 2026-08-22) traía reviews por
**scraping Apify**, no por API oficial (`APIFY_TOKEN` = "scraping de reviews y
discover", TRASPASO-1 §1.4). Tablas reales del backup
`/mnt/data/appdata/orbit/archive/competitive-2026-08-22.db`:

- `product_reviews` (163 filas): `marketplace, marketplace_id, external_review_id,
  rating, title, content, reviewer, verified, helpful_votes, review_date,
  language, images_json, review_url, fetched_at`. Texto completo en 162/163.
  amazon_mx 86 (2020-03-16 → 2026-07-26), amazon_us 33 (2022-07-24 →
  2026-05-22), meli 44 (2023-08-31 → 2026-08-08). URLs `amazon.com.mx/review/…`
  (Apify raspaba MX directo).
- `review_snapshots` (30,450): `(marketplace, marketplace_id, rating REAL,
  review_count, snapshot_date)`. amazon_mx 15,412 + amazon_us 4,031 solo hasta
  **2026-06-02** (el scraping Amazon murió 2.5 meses antes del apagón);
  meli 11,007 hasta 2026-08-22. **Lección: el scraping se rompe; diseña fuente
  ausente como estado explícito (Sin verificar), jamás como cero.**
- `meli_seller_reputation` (123): snapshot diario vía API oficial
  (`user_id, snapshot_date, level_id, power_seller_status, transactions_*,
  claims_period_pct, delayed_handling_pct`), 2026-04-14 → 2026-08-22.
- `meli_questions` (13): `question_text` + `suggested_answer` por IA con flujo
  humano (`status, final_answer, sent_at`). Referencia para v2, no para v1.

Keys ya migradas a secrets de Orbit (ORBIT 02): Apify, Keepa (rating/historial
sin scraping), Anthropic, SP-API, token MeLi de bridge. No propongas re-OAuth.

## Lee, en este orden

1. `AGENTS.md`, `docs/CONTEXTO.md` (reglas innegociables 1–10, dinero, madurez,
   append-only, reversa).
2. `docs/traspaso/MODULOS-AVANZADOS.md` Módulo 3 (spec base verbatim) y § Dependencias.
3. `docs/PENDIENTES-AUTOMATIZACION.md` AUTO-09 (el registro de este pendiente).
4. `plans/catalogo-campanas-01.md` (ORBIT 19): **espejo de estructura** —
   Resultado/límites, Decisiones, Evidencia, Etapas/tareas con DoD binario y
   `stage/lane/tdd`, Propiedad de archivos, Aceptación verificable, Despliegue y
   reversa, Confirmación operativa. §§1–10 de `plans/fabrica-01.md` si necesitas
   el patrón de ingesta/ledger (adaptalo a solo-lectura, sin ledger de mutación).
5. `app/templates/base.html` (sidebar), `app/ui.py` (patrón de pantallas),
   `app/notifica.py` si existe digest (reutiliza el canal de alertas, no inventes otro).

## Estructura exigida de `plans/reputacion-01.md`

- Resultado y límites (v1 de arriba, textual).
- Decisiones abiertas para el dueño (ver abajo): tabla ID/pregunta/opciones.
- Fase 0 — verificación de fuentes: una sonda read-only por fuente con comando
  exacto que el **lead** corre (tú no tienes ssh): actor Apify vigente +
  cobertura amazon.com.mx/com + costo; endpoints MeLi (reputación, preguntas,
  opiniones); Keepa para rating/historial; SP-API Account Health si aplica.
  Fuente no verificada = fuera de v1, declarada como ampliación.
- Fase A — ingesta + snapshots + alertas + pantalla: tareas con DoD binario,
  dependencias, evidencia `docs/evidencia/reputacion-01/<task>/`.
- Propiedad de archivos y concurrencia (no chocar con B.6 ni F2).
- Aceptación verificable (casos: review con/sin texto, rating ausente ≠ 0,
  fuente caída → Sin verificar sin romper la pantalla, alerta Almeida 1★, XSS
  en texto externo neutralizado).
- Despliegue y reversa (v1 es solo-lectura: la reversa es apagar ingesta y
  conservar datos; escríbelo explícito).
- Confirmación operativa previa (tabla de operaciones, sin fingir aprobaciones).
- Snippet de la entrada `manifest.json` (la aplica el lead, no la commitees tú).

## Decisiones abiertas mínimas (redáctalas accionables)

D1 cadencia (diaria + bajo demanda, ¿algo más frecuente?); D2 alcance (solo
listings propios vinculados, ¿ASIN sueltos?); D3 umbrales de alerta (rating,
1★, % reclamos, ventana de caída); D4 canal de alerta (digest Telegram
existente, ¿qué más?); D5 preguntas MeLi v1 (solo lectura, ¿o borrador IA como
antes?); D6 presupuesto Apify/recurrencia del scraping; D7 texto de reviewer
visible en UI (dato público, ¿completo o truncado?).

## Reglas de diseño que el plan debe respetar

Append-only con `(entidad, fecha, observed_at)`; dinero siempre `(valor,
moneda)`; ausente = `Sin datos`/`Sin verificar`, nunca 0; madurez y ventanas
UTC explícitas; texto externo por `textContent`, CSP sin inline, monedas
separadas; evidencia real antes de cerrar cada tarea; ninguna promesa de
cobertura ("Por probar") sin fuente verificada.

## Proceso y entrega

No ssh, no push, no tracker, no PRs: el lead cierra. Entrega el contenido de
`plans/reputacion-01.md` (y spec delta si hace falta, en
`docs/superpowers/specs/`, sin tocar specs aprobados). No escribas código ni
tests ni migraciones. No pongas fechas ni presupuestos. Si algo de MODULOS-03
contradice CONTEXTO, lo señalas y gana CONTEXTO. Si una fuente no se puede
verificar en papel, la marcas para sonda del lead en Fase 0, no la asumes.

## Criterio de cierre del brief

El plan trae todas las secciones de arriba, cada tarea con DoD binario y
dependencias existentes, cada fuente v1 con su sonda exacta para el lead, las
D1–D7 redactadas para respuesta sí/no/valor, y cero código. El lead lo revisa
contra repo y lo commitea; solo entonces el brief está Done.
