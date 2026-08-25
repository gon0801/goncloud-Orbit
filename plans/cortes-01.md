# CORTES 01 — umbral adaptativo per-producto (NEGATIVE_EXACT + PAUSE)

> **Propósito**: matar los cortes prematuros en catálogo de rotación lenta.
> CONTRATO: `docs/superpowers/specs/2026-08-24-cortes-adaptativos-design.md`
> v2 (precedencia spec > plan). Validación: brainstorm estructurado con el
> dueño + ronda 1 de cross-review codex (4A+4M) y grok (3A+7M) — todo
> incorporado; el dueño selló además el PISO `max(20/25, adaptativo)`.
> Números sellados: 3 órdenes / 60 clicks / 14 fechas / M=1.5 / F_neg=40 /
> F_pause=50 / L=90 (lookback: ventana literal BETWEEN D-90 AND D-10 = 81
> fechas maduras).
>
> **Reglas de secuencia**: aterriza ANTES de que ORBIT 04 Phase 2 toque
> `cycle.py` y ANTES del cutover ORBIT 05. Este PR también actualiza el DoD
> 2.2 de `plans/orbit-04.md` (la re-validación al liberar RE-RESUELVE
> `umbral_corte` con evidencia fresca — contrato cross-plan sellado).
>
> Spec skip reason: el contrato fino es el spec de este mismo PR;
> `docs/CONTEXTO.md` recibe el delta de umbrales en 1.2/1.3;
> `docs/traspaso/` (verbatim) no se toca.

## Phase 1 — La regla adaptativa [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | `windows.py`: `ventanas_evidencia_ad_group(conn, platform, decided_at)` — D = `_fecha_utc(decided_at)` (sin −3d), ventana literal `BETWEEN D-90 AND D-10` (NO el helper `inicio_ventana`), suma colapsada (v_metric_latest) de hojas keyword+product_target por ad group, envenenamiento por None estándar, `fechas_distintas = COUNT(DISTINCT metric_date)` del GRUPO (unión). Llamada UNA vez por plataforma dentro de TX2. ANTES de fijar tests: SELECT vivo (regla 8, evidencia anotada). `[tdd:required]` | Tests (_db_temporal): suma multi-hoja correcta; colapso bitemporal; None envenena (regla 9: sin bool_and daría parcial); bordes exactos D-90/D-10; unión de fechas con overlap multi-hoja el mismo día = 1 (regla 9: sumar conteos inflaría Z); grupo sin filas → ausente del dict (jamás ceros); `observed_at_max` del grupo incluido; pglast | - | cc:TODO |
| 1.2 | NEGATIVE_EXACT adaptativo: módulo puro NUEVO `app/optimizer/cortes.py` con `umbral_corte(evidencia, regla)` (elegibilidad 3/60/14 → `ceil(Decimal(clicks)/Decimal(orders) × Decimal("1.5"))` — ceil DEL PRODUCTO; si no → F por regla; SIEMPRE `max(legacy, bruto)`) + `hygiene.py` consume el umbral resuelto (int) + `cycle.py`: `_SQL_DECISORAS` gana **`k.parent_id AS ad_group_id`** (LITERAL con alias de tabla: `ag.parent_id` ya existe como campaign_id — sin calificar, PG falla por ambigüedad o peor, mapea la CAMPAÑA como grupo y todo cae a fallback en silencio — ronda 2 qwen), cablea la ventana, congela **`inputs.corte` top-level** (shape del spec, expected_clicks como string Decimal, evidencia con `observed_at_max`) y `data_observed_at = LEAST(decided_at, max(directo, evidencia))` (clamp del CHECK, ronda 2 qwen) + `reproduce()` LEE `inputs.corte.umbral_clicks_usado` (fila sin la clave → legacy 20). Delta de umbrales a `docs/CONTEXTO.md` (las balas NEGATIVE_EXACT y PAUSE de la sección de umbrales sellados — puntero exacto, ronda 2 kimi). `[tdd:required]` | Tests regla 9 demostrados fallando: cada mínimo discrimina (2/3, 59/60, 13/14); ceil fraccionario (61/3 → 31) y entero (50 → 75); PISO (expected×M=15 → umbral 20); grupo con orders=0 y clicks/fechas sobrados → NO elegible SIN dividir jamás (regla 9: una implementación que divide antes de la elegibilidad solo explota ahí); replay legacy con fixtures de inputs SIN `inputs.corte` → 20 (rojo sin el compat); bitemporal: evidencia más reciente que el dato directo Y el borde observed_at > decided_at → CLAMP a decided_at (regla 9: sin clamp, CHECK violation aborta el ciclo — sembrar observed_at futuro explícito, la convención _obs() del repo no lo produce); goldens de CICLO re-sembrados para que los 4 kinds disparen bajo la regla nueva (declarado: NO intactos) Y el golden asserta `inputs.corte.elegible=true` con `evidencia` no nula del grupo sembrado (ronda 2 qwen: sin esa aserción, un mapeo de grupo roto pasaría los goldens en fallback sistemático); la tupla literal de `_SQL_*` en tests/test_optimizer_windows.py incluye el SQL nuevo (lista hardcodeada, no vars()); motor puro (test_architecture; cortes.py sin IO) | 1.1 | cc:TODO |
| 1.3 | PAUSE adaptativo: `bid.py` consume LA MISMA `umbral_corte(evidencia, 'pause')` (F=50, legacy 25) y **congela `inputs.corte` en TODA decisión del motor de bids — incluidas las de kind final `bid`** (PAUSE se evalúa antes de las bandas: sin el freeze, el replay de un bid histórico podría volverse pause). `[tdd:required]` | Tests: misma batería que 1.2 para pause; camino ÚNICO (ambas reglas por la misma función — regla 9 si divergen); GOLDEN bid-que-bloqueó-pause (decisión bid con umbral pause adaptativo alto rejuega EXACTA; rojo con legacy 25); replay legacy de pauses históricos exacto; precedencia intacta | 1.2 | cc:TODO |
| 1.4 | PISO DE COSTO ADAPTATIVO de negative (enmienda sellada por el dueño tras su ejercicio de 57 anotaciones — spec v4 decisión 5bis): `umbral_corte` devuelve también `piso_cost` (`max(legacy, AOV×1.0)` elegible con revenue sano; `max(legacy, 45 USD/600 MXN)` si no); `hygiene.py` recibe el piso resuelto; `inputs.corte` gana `piso_cost_usado` + `aov` (strings Decimal); `_replay_hygiene` lee el piso congelado (fila vieja → legacy 8/130). `[tdd:required]` | Tests separados regla 9: AOV Decimal exacto; elegible-con-revenue-envenenado → respaldo en piso PERO adaptativo en umbral (independencia); piso jamás baja del legacy (max demostrado fallando); replay congelado y legacy; golden re-verificado | 1.3 | cc:TODO |
| 1.5 | Cierre: **reporte delta CONTRAFACTUAL** (mismo snapshot y reloj, maquinaria pura con umbrales legacy vs adaptativos — jamás dos ciclos consecutivos) con evidencia por decisión y su umbral, entregado al dueño y anotado en AppFlowy; actualización del DoD 2.2 de `plans/orbit-04.md` (re-resuelve `umbral_corte` con evidencia fresca al liberar) verificada en el diff; CHAT-CONTEXT al día; PR mergeado (CI batería completa); `CORTES 01` Done. `[tdd:skip:cierre-reporte]` | Reporte contrafactual entregado y anotado; el dueño lo vio; el delta a orbit-04.md está en el PR; CI verde; markers al día | 1.4 | cc:TODO |

## 事前確認

- 事項: external-send — `git push` + `gh pr create`/merge (un PR del plan completo)
  理由: cierre con batería completa en CI, patrón del repo
  scope: Phase 1 / todas
- 事項: external-send — SELECTs READ-ONLY por ssh a la base viva (regla 8 de 1.1) + anotaciones en AppFlowy
  理由: DoD de 1.1 y registro obligatorio
  scope: Phase 1 / 1.1, 1.4
- 事項: external-send — corrida CONTRAFACTUAL de solo lectura para el reporte de 1.4 (la maquinaria pura sobre un snapshot leído; NO escribe decisiones — a diferencia del plan v1, ya no se corre un ciclo shadow extra)
  理由: DoD de 1.4 (delta atribuible solo a la regla)
  scope: Phase 1 / 1.4
