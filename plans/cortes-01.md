# CORTES 01 — umbral adaptativo per-producto (NEGATIVE_EXACT + PAUSE)

> **Propósito**: matar los cortes prematuros en catálogo de rotación lenta.
> El CONTRATO es el spec aprobado por el dueño:
> `docs/superpowers/specs/2026-08-24-cortes-adaptativos-design.md`
> (precedencia: spec > este plan). Diseño validado por brainstorm
> estructurado con el dueño (unidad ad-group sellada tras descartar ASIN
> literal con datos vivos; 7 números sellados: 3 órdenes / 60 clicks /
> 14 fechas / M=1.5 / F_neg=40 / F_pause=50 / L=90d). Nacido de la
> evidencia arras (116 clicks / 0 ventas en término core). Validación del
> plan: ligera a propósito — el diseño ya nació revisado contra schema y
> datos vivos en el brainstorm; la red fuerte es la cadena de review por
> tarea (TDD + lead + reviewer fresco + bots).
>
> **Reglas de secuencia** (selladas en el spec): aterriza ANTES de que
> ORBIT 04 Phase 2 toque `cycle.py` y ANTES del cutover de ORBIT 05 (el
> shadow debe validar la regla NUEVA varios días). Paralelo-seguro con
> ORBIT 04 Phase 1 (archivos disjuntos).
>
> Spec skip reason: no se crea spec adicional — el contrato fino ya existe
> (spec de brainstorm committeado en este mismo PR); `docs/CONTEXTO.md`
> recibe el delta de umbrales en 1.2/1.3 (su tabla sellada mentiría tras el
> merge). `docs/traspaso/` (fuente verbatim) NO se toca: el cambio de regla
> es decisión del dueño registrada aquí y en el spec.

## Phase 1 — La regla adaptativa [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | `windows.py`: función `ventanas_evidencia_ad_group(platform)` — una consulta por plataforma/ciclo, suma colapsada (v_metric_latest) de las hojas keyword+product_target por ad group en [D-90, D-10], envenenamiento por None estándar, `fechas_distintas` incluida. ANTES de fijar tests: SELECT de forma real contra la base viva (regla 8, evidencia anotada). `[tdd:required]` | Tests (patrón _db_temporal): suma correcta multi-hoja; colapso bitemporal (gana la última); None envenena la métrica del grupo (demostrado fallando: sin bool_and daría suma parcial); ventana [D-90, D-10] exacta (bordes); ad group sin hojas/sin filas → ausente del dict (jamás ceros); pglast del SQL | - | cc:TODO |
| 1.2 | NEGATIVE_EXACT adaptativo: función compartida `umbral_corte(evidencia, regla)` (elegibilidad 3/60/14 → ceil(expected×1.5); si no → F por regla) + `hygiene.py` consume el umbral resuelto + `cycle.py` cablea la ventana nueva y congela `inputs.termino` enriquecido (umbral_clicks_usado, elegible, expected_clicks, evidencia) + replay compat (sin umbral_clicks_usado → legacy 20). Delta de umbrales a `docs/CONTEXTO.md`. `[tdd:required]` | Tests regla 9 demostrados fallando: cada mínimo de elegibilidad discrimina (falta 1 orden → fallback); adaptativo vs fallback con fixtures; caso arras de sanity (CPO 50, M 1.5 → umbral 75: término 116/0 SÍ corta; término 30/0 NO); replay legacy reproduce las decisiones históricas EXACTAS (goldens sellados intactos); motor sigue puro (test_architecture verde) | 1.1 | cc:TODO |
| 1.3 | PAUSE adaptativo: `bid.py` consume LA MISMA `umbral_corte(evidencia, 'pause')` (F_pause=50; cero reimplementación — test de camino único) + inputs congelados enriquecidos igual + replay compat (legacy 25). `[tdd:required]` | Tests: misma batería que 1.2 para pause; test de camino ÚNICO (negative y pause resuelven por la misma función — demostrado fallando si divergen); precedencia PAUSE>bandas intacta; goldens históricos de pause reproducen exactos | 1.2 | cc:TODO |
| 1.4 | Cierre: ciclo shadow manual con la regla nueva + REPORTE DELTA para el dueño (cuántos negatives/pauses dejan de dispararse, cuáles sobreviven y por qué — evidencia por decisión con su umbral), CHAT-CONTEXT al día, PR mergeado (CI batería completa), `CORTES 01` Done en AppFlowy con notas completas. `[tdd:skip:cierre-reporte]` | Reporte delta entregado y anotado en AppFlowy; el dueño lo vio; CI verde; markers al día | 1.3 | cc:TODO |

## 事前確認

- 事項: external-send — `git push` + `gh pr create`/merge (un PR del plan completo)
  理由: cierre con batería completa en CI, patrón del repo
  scope: Phase 1 / todas
- 事項: external-send — SELECTs READ-ONLY por ssh a la base viva (regla 8 de 1.1) + anotaciones de evidencia en AppFlowy
  理由: DoD de 1.1 y registro obligatorio del repo
  scope: Phase 1 / 1.1, 1.4
- 事項: external-send — UN ciclo shadow manual (docker exec app.cli cycle) para el reporte delta de 1.4 — escritura APPEND-ONLY de decisiones shadow, mismo patrón operativo diario
  理由: DoD de 1.4 (el delta se mide contra decisiones reales)
  scope: Phase 1 / 1.4
