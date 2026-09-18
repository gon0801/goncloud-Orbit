=== quality-kit cross-review.ps1 ===
Repo: /Users/dn/dev/_wt/f11-kimi-r1
CLI: kimi
Alcance: last-commit
Archivos (pathspec de la tarea): app/estimacion_fees.py, app/precio/__init__.py, app/precio/config.py, app/precio/objetivo.py, app/precio/reglas.py, app/precio/tipos.py, app/precio/ventas.py
Tamano del diff: 57892 caracteres
==> Invocando kimi de forma no interactiva (tope: 900 s)...

=== Respuesta de kimi (codigo de salida: 0) ===
• Voy a verificar en el repo los puntos que el diff no muestra (imports, tipos, constantes del plan).

• Reviso cómo se serializa el precio en el request y en la identidad canónica, para el edge de `Decimal` con exponente.

• Un punto limite a verificar: el freno de subida a los 22 dias contra los tests.

• Un par de verificaciones finales en `app/estimacion_fees.py`.

• Reviso como estan sellados los tests de `no_converge` para la regla 10.

• Revision completa. Verifique contra el repo los puntos que el diff no muestra: `app/estimacion_fees.py` (imports, `_MAX_DINERO`, firma de `cotizar_oferta`, serializacion del request vs identidad canonica), `app/estimacion_venta.py` (`DetalleFee` con `fee_type/final_fee/tax_amount/included_fee_details`), el plan `plans/repricing-01.md` (cotas de config, lista Reject), el spec S4 (`docs/superpowers/specs/2026-09-15-repricing-01-design.md`) y `tests/test_precio_reglas.py`. La formula cerrada, las cotas de `leer_config`, el cooldown contando `no_confirmado`/`error` (Reject explicito de lo contrario), el borde 22/23 del freno y la maquina de dos cotizaciones cuadran con spec, plan y tests sellados.

  Hallazgos:

  1. (baja) `cotizar_a_precio` (app/estimacion_fees.py:742-751): la guarda de "centavos exactos" compara por valor numerico (`precio != centavos`), asi que `Decimal("1E+2")` o `Decimal("100.000")` pasan, pero `str(precio)` (que entra a la identidad canonica en `construir_fee_canonical_input`, linea 116) queda `"1E+2"`/`"100.000"` mientras el request serializa `Amount` como `"100"` (`_decimal_a_json_number`). Es exactamente el desfase identidad-vs-request que el propio docstring (r5-J3) dice prevenir, y fragmenta la dedup por `source_event_id` para el mismo precio economico. En la practica el motor solo alimenta salidas de `techo_centavo` (exponente -2), asi que es borde de uso directo; fix trivial: exigir `precio.as_tuple().exponent >= -2` ademas de la igualdad, o normalizar `precio = centavos` antes del `replace`.

  2. (baja) VERIFICAR: en `reglas.decidir` la regla 10 (`no_converge`) mete la distancia de HOY en la cadena monotona (`cadena = [...ultimos] + [distancia]`), asi que tres cambios consecutivos sin disminuir NO frenan si hoy la distancia mejoro. La lectura literal de S4 #10 ("tres cambios consecutivos en la misma direccion sin que |m-goal| disminuya") frenaria mirando solo los tres cambios. Los tests sellados cubren "distancias 0.08/0.07/0.06 no frena" y "iguales si frena", pero no el caso "tres sin disminuir y hoy mejor". Confirmar que esa inclusion de hoy fue decision del brief y no un desliz; si lo fue, no hay nada que hacer.

  3. (baja) `config._rango_fechas`: si el rango llega con objetos `date` en vez de strings ISO cae en "rango invalido" con mensaje enganoso. Los settings vienen de JSON asi que es defensivo, pero la asuncion "solo str" no esta documentada y el error no distingue tipo de formato.

  4. (baja) `cotizar_a_precio` hace `from dataclasses import replace` dentro de la funcion pese a que el modulo ya importa de `dataclasses` arriba (linea 22); nit de estilo sin impacto.

  Nada de severidad alta o media: no encontre bugs logicos, regresiones ni riesgos de seguridad; los puntos delicados que un diff parcial podia esconder (serializacion del precio, estructura de `DetalleFee`, cotas de config, orden cooldown/freno, bordes 22 dias y racha) quedaron verificados contra codigo, spec, plan y tests.



=== stderr ===
kimi version 0.39.1
• The user explicitly says: act as external reviewer, don't use skills, respond directly. Read the diff file. Let's read it.Now I need to review. Let me think about potential issues in the diff, and verify against repo code.

  Potential findings:

  1. `app/estimacion_fees.py` `cotizar_a_precio`: imports `_MAX_DINERO` — verify it exists in that file and the check direction. Also `InvalidOperation` import, `Callable`, `datetime`, `UTC` imports. Verify.

  2. `objetivo.py` `precio_estrella` denominator: `(1 - isr_tasa - goal) / divisor - ref`. Wait — the formula P* = (C + fijo + L) / ((1 - r - goal)/d - ref). Check: contribucion = I - C - F - L - R where I = P/d, R = r*I, F = ref*P + fijo, contribucion/I = goal. So (P/d - C - ref*P - fijo - L - r*P/d) / (P/d) = goal → 1 - ref*d... let me solve: P/d - C - ref*P - fijo - L - r*P/d = goal*P/d → P/d*(1 - r - goal) = C + fijo + L + ref*P → P*( (1-r-goal)/d - ref ) = C+fijo+L. Yes correct.

  3. `margen_a_precio` — consistent.

  4. In `paso` — the machine: with zero cotizaciones computes estrella using ref/fijo passed in (derived from scenario fee_detalles). Then requests quote at techo_centavo(estrella). With one cotizacion, re-derives ref1/fijo1 from the cotizacion, computes margin m1, if within tolerance verified; else recompute estrella with ref1/fijo1 and ask second. With two, verify m2 else goal_inalcanzable fee_no_lineal. OK.

  Potential bug: in `paso`, second quote request uses `Importe(techo_centavo(estrella), moneda), 2` — fine.

  5. `_revisar_cotizacion`: checks suma_top == fee_total. Note `_suma_top` only sums top-level final_fee, not included_fee_details; presumably fee_total is the top-level sum. Verify DetalleFee structure in app/estimacion_venta.

  6. reglas.py `decidir`: the cooldown loop iterates over all cambios and uses enviado_en < dias_entre_cambios → mantener cooldown. Note it doesn't filter by estado "confirmado" — the perdiendo_tras_subida loop filters estado != "confirmado" and goal_vigente_desde; the cooldown loop only filters es_reversa and live/aplicado. Is that intended? Might be per spec; can't verify. Also cooldown doesn't filter goal_vigente_desde — hmm, the freno #10 filters historial by goal_vigente_desde, #6 filters cambio.enviado_en < goal_vigente_desde, but cooldown (#9) doesn't filter by goal_vigente_desde. Could be intended (any recent change triggers cooldown regardless of goal change). Hard to call it a bug; VERIFICAR maybe against plan. I can check plans/ for REPRICING 01 plan.

  7. `reales` for freno: filter `h.fecha >= entrada.goal_vigente_desde` and sorts. The no_converge check: `len(direcciones) == 1 and all(posterior >= anterior ...)`. cadena = distances of last N + current distancia. monotonic non-decreasing → no_converge. OK.

  8. ventas.py `evaluar_senal`: n15 < 10 hardcoded while config has... config has `precio_senal_dias`, `u60_min`. The "10" minimum for n15 — is that a plan constant? It's a hardcoded literal 10, whereas rule "dato faltante = constante inventada" — but might be plan constant. Verify with plan.

  9. racha logic: racha only increments when perdiendo condition met; when no_perdiendo returns racha 0. But sin_dato cases return racha 0 — meaning a sin_dato day resets the racha. Is that intended? "perdiendo = ... solo con racha completa (senal_dias corridas)" — corridas = consecutive. If a day is sin_dato, racha resets to 0, meaning the caller stores racha from senal.racha. Plausible.

  10. In `ventas.py` the condition check `if not Decimal(u15) < esperado: return no_perdiendo` — this includes equality (not strictly less) → no_perdiendo. Matches "< estricto".

  11. `contados15` uses `d <= cubierto` filter, so n15 can be up to 15 only if cubierto >= hoy-1; if cubierto = hoy-3, n15 = 13. n15<10 guard. OK.

  12. In `decidir`, direction: `if m_actual < goal - tol: _subir` — margin below goal → raise price. Correct. `_subir` asks for quote at P* and applies min(p_objetivo, tope). But wait: `_subir` checks `if p_objetivo <= comp.p_actual.valor or p_aplicado <= comp.p_actual.valor: escenario_incoherente`. p_aplicado = min(p_objetivo, tope); tope = piso(p_actual*(1+escalon)) which is > p_actual normally. If p_objetivo > p_actual but tope rounding... tope = piso_centavo(P*(1+esc)) could equal P if escalon small and P like... e.g. P=100, esc=0.01 → 101.00 fine. piso could only round down fractions of a cent; tope >= P always since 1+esc > 1 and piso rounds down at most 0.01... P*(1+0.01) with P having centavos: P*1.01 - floor to centavo >= P? P=0.05? min price... Actually P*(1.01) >= P + 0.01 iff P >= 1. If P < 1 MXN, tope could equal P. Edge case, low severity. Also there's guard catching p_aplicado <= p_actual → escenario_incoherente — but that's not incoherent scenario, it's just no room; minor.

  13. `_subir` movimiento_minimo check uses `abs(p_objetivo - p_actual)` where p_objetivo = salida.precio (verified price within tolerance). But if |p_objetivo - p_actual| < umbral → mantener. But direction: we're in _subir because m_actual < goal - tol. Verified price could be below p_actual? regla11 doesn't check that. Then guard catches p_objetivo <= p_actual → escenario_incoherente. OK defensive.

  14. In `_bajar`, piso = techo_centavo(p_actual * (1 - escalon)); p_aplicado = max(p_goal, piso). Fine.

  15. `repartir_cupo`: sorting key `candidatos[pos][1].prioridad is None, -(prioridad or 0), listing_id`. OK. Note: `compiten` uses pos; `pasan = set(ordenados[:cupo])`. Fine. One subtlety: decisions with resultado subir/bajar and aplicado=True — but in shadow mode aplicado = mode=="live" is False, so shadow never consumes cupo. OK per docstring.

  Note: `repartir_cupo` replaces decisions losing cupo with `mantener/cuota` but keeps `p_objetivo` (only clears p_aplicado). Decision validation: p_aplicado only in subir/bajar — cleared. motivo "cuota" in MOTIVOS_MANTENER. OK.

  16. `tipos.py` `motivo_permitido` for no_evaluado: base "fee_error" requires sub non-empty. `_revisar_cotizacion` returns f"fee_error:{cot.error_code or 'desconocido'}" — fine.

  17. In `reglas.py` `_no_evaluado` with motivo from `derivar_ref_fijo` exception = "fee_error:referral_ausente" — permitted.

  18. Check: in `paso` with zero cotizaciones, `ResultadoObjetivo("goal_inalcanzable", ...)` uses ref,fijo from args. Fine.

  19. `_tiene_tax` recursion over included_fee_details — check DetalleFee has `included_fee_details` tuple and `tax_amount`, `final_fee`, `fee_type` attributes. Verify in app/estimacion_venta.py.

  20. `cotizar_a_precio` in estimacion_fees: `precio >= _MAX_DINERO` check. Also `quantize` without rounding mode — Decimal default ROUND_HALF_EVEN; quantize can raise InvalidOperation if result exceeds precision context (e.g., huge number with many digits). They catch InvalidOperation. But note: they check `precio != centavos` after quantize with default rounding — fine.

  Also `_MAX_DINERO` — but check `precio <= 0` earlier raises; the second `if precio >= _MAX_DINERO` — verify _MAX_DINERO exists and sensible (NUMERIC(14,4) → 10^10).

  Let me verify a few things in the repo:
  - app/estimacion_venta.py DetalleFee definition.
  - app/estimacion_fees.py: _MAX_DINERO, OfertaResuelta, ResultadoCotizacion, cotizar_oferta signature, imports of InvalidOperation, Callable, UTC, ProductFeesClient.
  - Check whether there's a plan file plans/ with the constants (senal_dias, n15<10, 75 days, 3 days ledger hueco, _DIAS_FRENO_SUBIDA=22) to verify.

  Also possible issue in objetivo.py `paso`: when first cotizacion fails review (`_revisar_cotizacion` returns motivo), we return no_evaluado — but loop `for cot in cotizaciones` reviews all; fine.

  A subtle bug: in `paso`, when len(cotizaciones)==2 and first one was already verified within tolerance... Actually if m1 within tolerance it returns verificado with primera even when 2 cotizaciones exist. Fine.

  Another subtle: `paso` with one cotizacion where m1 not within tolerance computes new estrella using ref1/fijo1 and asks for quote #2 at techo_centavo(estrella). But what if that estrella equals the first quoted price again (fixed point slightly off)? Then quote #2 at same price → m2 = m1 → not within tolerance → goal_inalcanzable fee_no_lineal. Acceptable design.

  Edge: in `decidir`, `_subir` calls `paso` with cotizaciones=() initially → returns PideCotizacion(precio = techo_centavo(P*)) → regla11 check via `_frena_regla11` on salida.precio.valor. motivo_regla11 docstring says "sobre P* ya calculado (sin redondear)" but here applied to rounded price — minor doc/impl mismatch, low.

  Bigger: regla11 check `p_estrella > 2 * p_actual` applies to subir direction — fine. But for _bajar, regla11 `precio_no_cubre_costo` applies. OK.

  Now check the freno no_converge: `reales` filters `(entrada.mode == "shadow" or h.aplicado)`. In shadow mode, all historial rows count (including non-aplicado virtual ones); in live only aplicado. Matches docstring.

  Cooldown loop: `for cambio in entrada.cambios: if cambio.es_reversa or (live and not aplicado): continue; dias = hoy - enviado_en; if dias < dias_entre_cambios → cooldown`. Note: no estado filter — a cambio with estado "fallido"/"pendiente" also triggers cooldown. And no goal_vigente_desde filter. Could be intended; flag as VERIFICAR against plan maybe. Let me check the plan doc.

  Also `dias` for future dates: negative → cooldown triggered with "fecha futura" note. OK.

  Also potential issue: in perdiendo_tras_subida loop, condition `cambio.enviado_en < entrada.goal_vigente_desde` continue — but cooldown doesn't. Inconsistent but possibly deliberate.

  ventas.py: `n15 < 10` literal — check plan. Also `75` days and `3` days ledger hueco, 60/15 windows.

  Also: evaluar_senal racha: when condition perdiendo true but racha_previa+1 < senal_dias → sin_dato("racha_incompleta", racha). The racha returned is the incremented one so caller can persist. OK.

  Edge in ventas: `contados15` requires `d <= cubierto`; cubierto could be > hoy? (hoy - cubierto).days > 3 negative... if cubierto in future, fine.

  por_dia sums ventas across all dias — includes days outside windows, harmless.

  Now the money check in `_freno_entrada`: divergencia = abs(pricing - p_actual)/p_actual > divergencia_max → precio_divergente. Fine.

  `_coherencia_escenario` checks abs(comp.isr - isr_tasa*ingreso) > 0.01. OK.

  One more: `Decision.__post_init__` requires motivo vocabulary; `_base_senal` uses motivo "en_tolerancia" placeholder for mantener, replaced later. In `_no_evaluado`, replace sets resultado="no_evaluado" and motivo → valid.

  In `_subir` final: `motivo=None` with resultado "subir" — valid.

  Check `config.py` `_numero`: `Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))` — redundant but fine. Note `Decimal(str(float))` e.g. 0.1 float → "0.1" fine. Bool rejected. OK.

  `_fechas_excluidas`: requires the key present; empty list ok.

  `leer_config`: escalon < minimo check. Note movimiento_min_pct minima "0" — allowed 0. Then umbral = max(0*P, min_abs) = min_abs. Fine.

  Now, one real potential bug: in `objetivo.py` `paso`, when there are 0 cotizaciones, it computes estrella with the `ref`/`fijo` passed in (from scenario). But `_revisar_cotizacion` loop runs first with empty tuple — fine.

  In `reglas.py` `decidir`: cooldown check happens before direction even when m within tolerance — meaning cooldown returns "mantener/cooldown" even if en_tolerancia. Order: frenos (#10, #6) → cooldown (#9) → direction. Docstring states this order is sealed. OK.

  Another: in `decidir`, `if entrada.senal.estado == "perdiendo"` loop over cambios for #6 — uses cambio.estado != "confirmado" skip, and direction "subir", dias <= 22. If a cambio has enviado_en in the future, dias negative ≤ 22 → frenado. OK.

  Now `repartir_cupo`: candidates tuple of (listing_id:int, Decision). Fine.

  Potential issue in `repartir_cupo` sort: `-(candidatos[pos][1].prioridad or Decimal(0))` — if prioridad is None, `or` gives 0; but first key element already separates None to the end. Prioridad Decimal(0) vs None: 0 priority goes before None. OK per "ausente no es cero".

  Hmm — one thing: in `_prioridad`, `abs(m_actual - goal) * ingreso_60d.valor` — for subir/bajar with ingreso_60d None → prioridad None → al fondo. Fine.

  Now check the actual repo files to verify imports and DetalleFee. Also verify `app/estimacion_fees.py` context around line 706, `_MAX_DINERO`, and that `cotizar_oferta` signature has `now_utc` param.

  Also check whether tests exist for these (tests/ dir). Not strictly needed for review but quality.

  Also check plan constants: search plans/ for "senal_dias" / "freno" / "22" / "n15".

  Let me do the verification greps.

• Good — imports and DetalleFee check out. Now check `cotizar_oferta` signature (observed_at param?) and `OfertaResuelta` with `price_amount` field. Also check plan constants for senal/n15<10/75/3/22 days. Search plans.Let me look at the repricing-01.md plan for the S4 rules detail: cooldown filtering, n15 minimum (10), ledger hueco 3 days, 75 days history, _DIAS_FRENO_SUBIDA=22, and whether cooldown should filter by estado/goal_vigente_desde. Let me read the relevant sections of plans/repricing-01.md.Key line: plan line 330: "cooldown que ignore `no_confirmado`/`error`" — this appears in a list of residuals/things to avoid. Let me read context around line 330 to confirm: it seems the plan demands the cooldown ignore no_confirmado/error states. In reglas.py, the cooldown loop does NOT filter by estado — only es_reversa and live/aplicado. The #6 loop filters `cambio.estado != "confirmado"`. So cooldown counting an `error` or `no_confirmado` change would contradict the plan. Wait — but the test expectation in A.2 says: "`no_confirmado` de hace 6 días → `mantener(cooldown)`" — hmm, that says a no_confirmado from 6 days ago → mantener(cooldown)! So the cooldown SHOULD count no_confirmado? Wait, that's confusing. Line 330: "cooldown que ignore `no_confirmado`/`error`" — listed among... let me read line 320-340 to see if that's a list of "errores a evitar" or of requirements.

  Actually the A.2 test spec says: "subida confirmada hace 10 días + caída → frenado(perdiendo_tras_subida); `no_confirmado` de hace 6 días → `mantener(cooldown)`". Hmm — that says no_confirmado 6 days ago → cooldown. With dias_entre_cambios=7, 6 days < 7 → cooldown. So cooldown counts no_confirmado? But then line 330 "cooldown que ignore no_confirmado/error" — need context. Let me read lines 300-400 of the plan.Important: the "Reject (revisión 2026-09-16)" list includes "cooldown que ignore `no_confirmado`/`error`" — meaning a cooldown that IGNORES no_confirmado/error states is REJECTED. I.e., cooldown MUST count no_confirmado/error changes too. So reglas.py's cooldown loop NOT filtering by estado matches the plan (it counts all non-reversa changes). Good — not a bug.

  Also "goal nuevo reinicia el freno, no el cooldown" (AC7) — freno filters historial by goal_vigente_desde, cooldown does not. Matches.

  Now check remaining potential issues:

  1. `_DIAS_FRENO_SUBIDA = 22` literal — plan? Search for "22" in the plan S4 section. The plan said p50 27 días MX, 22 US for rezago... Let me check S4 rule #6 in the spec. Search "perdiendo_tras_subida" context in the plan or docs. Actually AC13: "Subida confirmada hace 10 días". Let me grep for the spec of rule 6 (S4 #6).

  2. ventas.py n15 < 10 literal — check plan: A.2 test says "n15=9" → sin_dato. So minimum is 10; n15=9 fails. OK matches. But is 10 a plan constant or should it be config? Check config keys list lines 50-66 of plan. Config has precio_senal_dias, precio_u60_min... no n15 min key. Plan line ~53-63 showed keys. Let me read plan lines 40-80 for S4 spec of rule 5 and rule 6.The plan mentions the spec S4 is in a separate doc — likely docs/traspaso or a spec file for repricing. Rules #5, #6, #9, #10 are in the spec. The `_DIAS_FRENO_SUBIDA = 22` and the 75-day historia and 3-day ledger hueco and n15<10 — those are probably in the spec S4. Let me find the spec. Search docs for "perdiendo_tras_subida" or "senal_dias".Good — spec constants match: n15 ≥ 10, historia ≥ 75 días, 22 días freno subida. Let me check the spec lines around 150-230 to verify details: rule #5 (u15 window, exclusions, ledger coverage 3 days?), #6, #9 cooldown, #10 freno, #12 cuota, #13 shadow. Let me read the spec section.Now I have the spec. Let me compare carefully against the diff:

  Rule 5: "en cada uno de los 15 días hay observación de inventario con total_quantity > 0 y el listing estuvo activo" — in ventas.py, the loop is over `contados15` (days not excluded, ≤ cubierto). Spec says "cada uno de los 15 días" — but the docstring says excluded days don't count. Spec says excluded ranges "no cuentan... el promedio se escala por días contados" — so contados15 is right.

  Rule 5 ordering: spec lists guardas: u60 ≥ min; n15 ≥ 10; historia ≥ 75; inventario/listing. Code order: historia → n60==0 → u60_min → n15 → inventario/listing. The diff docstring says S4 doesn't fix order; sealed by brief. OK.

  Rule 6: spec says "Si perdiendo_ventas es true y hay un cambio propio confirmado (subida) en los últimos 22 días, no se sube ni se baja: frenado". In reglas.py, the freno #6 loop is inside `if entrada.senal.estado == "perdiendo":` — good, matches "si perdiendo es true". But: spec says the freno applies also to NOT bajar — in code, the check happens before direction split, so it blocks both subir and bajar. Good.

  But wait — order: code checks #6 freno before cooldown, and before direction. Rule 6 filter: `cambio.estado != "confirmado"` skip; `cambio.enviado_en < entrada.goal_vigente_desde` skip ("Se reanuda con un goal nuevo"). dias <= 22. Matches.

  Rule 9 cooldown: spec says measured against ANY non-reversa precio_cambio with enviado_at (pendiente, enviado, confirmado, no_confirmado or error). Code: skips es_reversa and (live and not aplicado) — matches shadow rule #13 (virtual counts in shadow, not live). But note: spec says cooldown per (listing, platform) — entrada.cambios presumably already filtered by caller. OK.

  Rule 10: "Tres cambios consecutivos en la misma dirección sin que |m−goal| disminuya". Code: reales = historial filtered (shadow: all; live: aplicado) and fecha >= goal_vigente_desde, sorted by fecha; takes last config.freno_cambios; checks single direction and non-decreasing distances (including current). Wait — "sin que disminuya": the code requires posterior >= anterior for all consecutive pairs INCLUDING the current distancia appended. Hmm, the spec: three changes without the distance diminishing. The code builds cadena = [d1, d2, d3, distancia_actual] and requires d1<=d2<=d3<=actual. That's stricter: it also requires the CURRENT distance to be ≥ last. Consider: 3 changes non-converging (distances increasing) but today's distance improved — then no freno. Plausible interpretation. But also consider: if today's distance is smaller but the 3 previous didn't decrease among themselves → spec "tres cambios consecutivos sin que disminuya" arguably still frena. Ambiguous; code's choice is defensible. Not a bug per se; borderline. Also `reales` sorted by fecha only — if two entries same fecha, order between them is whatever tuple order was; sorted is stable so original order preserved. Minor.

  Another subtlety in rule 10: `if len(reales) >= config.freno_cambios` — takes the LAST 3. OK.

  Rule 3: "Movimiento del día = min(P*, P·(1+escalón)); P* redondeado hacia arriba, tope hacia abajo." Code: p_objetivo = salida.precio (techo de P*); tope = piso(P*(1+esc)); p_aplicado = min. OK.

  Rule 3: "una cotización real a P* y recálculo; si |m(P1) − goal| > tol, una segunda y última". The machine `paso`: with zero cotizaciones asks for cotizacion at techo(P*). With one: derives ref1/fijo1 from that cotizacion, computes m1 with closed form; if within tol → verificado. Else recompute P* with ref1/fijo1 and ask second. Matches.

  Hmm wait — in `paso` with zero cotizaciones, estrella computed with ref/fijo passed in. In `_subir`, ref/fijo derived from escenario fee_detalles via derivar_ref_fijo. Fine.

  Rule 4: bajar a max(P_goal, P·(1−escalón)), P_goal hacia arriba. Code: p_goal = salida.precio (techo), piso = techo(P*(1-esc)), p_aplicado = max. OK. Note spec says "(1 − escalón)" rounding not specified for the floor; code uses techo for piso which makes p_aplicado larger (less aggressive cut) — conservative, docstring says piso hacia arriba. OK.

  Rule 8: movimiento mínimo "sin consumir cooldown ni cuota" — code returns mantener(movimiento_minimo). OK.

  Rule 11: P* > 2·P_actual or P* ≤ C + L → goal_inalcanzable. Code motivo_regla11 checks p_estrella > 2*p_actual → "precio_mayor_al_doble"; p_estrella <= costo + envio → "precio_no_cubre_costo". Applied via _frena_regla11 to the price requested (PideCotizacion) and to verified price. Note: spec says "P* > 2·P" — the check on the QUOTED price (techo(P*)) not raw P*; docstring notes this residual. OK.

  But: regla 11 is applied to the verified price too — in subir, salida.precio is the verified P1 or P2. Fine.

  Now rule 2/m_actual: computed from componentes. OK.

  Rule 13: shadow virtual changes consume cooldown and freno — code: in shadow mode, reales includes all historial (aplicado filter only in live), cooldown includes all cambios. Matches.

  Rule 12: cuota — repartir_cupo. Spec: "un no_confirmado, un error y las reversas también consumen" — that's about precio_cambio consuming quota at apply time (A.5), not repartir_cupo. OK.

  Prioridad: |m−goal|·ingreso_60d; desempate listing_id. Code matches.

  Now potential bugs to dig deeper:

  A. In `paso`, the first cotizacion request: PideCotizacion(precio, intento=1). In `reglas._subir`, when salida is PideCotizacion, it applies regla11 to salida.precio and returns the PideCotizacion — the caller (A.5) will cotizar and call again. Fine.

  B. `_freno_entrada` — check `comp.p_actual.moneda not in ("MXN","USD")` occurs AFTER _coherencia_escenario — fine.

  C. `monedas` set in _freno_entrada — 6 monedas must be equal. OK.

  D. In `decidir`, when `m_actual > goal + tol` and senal "perdiendo" → _bajar. But wait: rule 6 check earlier only frens when senal perdiendo AND a confirmed subida within 22 days. Then in the bajar branch: senal sin_dato → mantener ventas_sin_dato; perdiendo → _bajar; else sobre_goal_sin_perdida. Matches rule 4.

  E. Now ventas.py racha: "Debe ser true en 3 corridas consecutivas." Code: racha = racha_previa + 1 only when the perdiendo condition holds today; if racha < senal_dias → sin_dato("racha_incompleta", racha). Note: returns estado sin_dato while condition holds — meaning decision sees "sin_dato" not "perdiendo" until racha complete. And if condition doesn't hold → no_perdiendo racha 0. And sin_dato earlier guards reset racha to 0 — "consecutivas" requires consecutive days with full data AND condition. Hmm: if a day is sin_dato (e.g. dia_sin_stock), the racha resets. Spec: "Debe ser true en 3 corridas consecutivas" — if a corrida is sin_dato, the condition isn't evaluable; resetting is conservative and defensible. OK.

  F. Now the interesting one: `evaluar_senal` uses `insumos.dia_cubierto_hasta` and requires (hoy - cubierto).days <= 3. Where does 3 come from? Not in config. Spec #5 mentions "días cubiertos por ingest_run" per plan-validacion line 20. Is there a spec line about ledger hueco 3 days? Let me check spec around S2. Not critical.

  G. `contados15` includes `d <= cubierto`. If cubierto = hoy-3, n15 = 13 ≥ 10 OK. If cubierto is in future, fine.

  H. Now the objective formula detail — ISR: `R = r * I`. In `margen_a_precio`: contribucion = ingreso - costo - fees - envio - isr_tasa*ingreso. Consistent with precio_estrella. OK.

  I. `derivar_ref_fijo` — `referrals = [det for det in detalles if det.fee_type == "ReferralFee" and det.final_fee > 0]`. Note: only top-level. If ReferralFee appears nested in included_fee_details, missed — but top-level is the shape. OK.

  J. `_revisar_cotizacion`: `_suma_top(cot.detalles) != cot.fee_total` — exact equality of Decimals from JSON parse. estimacion_venta has same pattern (line 195). OK.

  K. In objetivo.py `paso` — when len(cotizaciones)==1 and m1 out of tolerance, recompute estrella with ref1/fijo1 and PideCotizacion intento=2. But it does NOT check that the new requested price differs from the first quoted price. If equal, second quote gives same m → fee_no_lineal. Fine.

  L. A potential real issue in `reglas.py decidir`: `reales` uses `h.fecha >= entrada.goal_vigente_desde` for rule 10 — spec says "se reanuda con un goal nuevo". OK. But the cooldown (rule 9) doesn't filter by goal_vigente_desde — spec explicitly: "Un goal nuevo reinicia el freno (#10), no el cooldown." Matches.

  M. Rule 6 uses `cambio.enviado_en < entrada.goal_vigente_desde: continue` — "Se reanuda con un goal nuevo". Matches.

  N. Check `Decision` invariant: `p_aplicado` only in subir/bajar. In `_subir` movimiento_minimo branch, p_objetivo set, p_aplicado None → OK. In frenado no_converge etc., p_aplicado None. In repartir_cupo replace sets p_aplicado=None. OK.

  O. `_base_senal` sets p_actual = comp.p_actual — note Decision.p_actual Importe. OK.

  P. `repartir_cupo` — `if not isinstance(cupo, int) or isinstance(cupo, bool) or cupo < 0`. OK. Sorting: `-(prioridad or Decimal(0))` — Decimal negation OK. But wait: key's first element `prioridad is None` (False sorts before True) → those with priority first, descending, then listing_id asc. OK. Note: two candidates could share listing_id? Then sort still deterministic by position? No — listing_id tie would compare equal keys... then sorted is stable → original order. Fine.

  Q. A subtle one: in `repartir_cupo`, decisions that compiten but lose are replaced with mantener/cuota — but `p_objetivo` is kept while `p_aplicado=None`. Decision validation passes (p_aplicado None for mantener). But is keeping p_objetivo on a mantener row desired? S5 schema allows p_objetivo. Fine.

  R. Now `tipos.py` `motivo_permitido` for "mantener": base "ventas_sin_dato" requires sub in SUBMOTIVOS_SIN_DATO. In reglas, `motivo=f"ventas_sin_dato:{senal.submotivo}"` — submotivo validated in SenalVentas. OK. For "cooldown"/"cuota"/etc. requires no sub. OK.

  S. `MOTIVOS_NO_EVALUADO` includes "impuesto_fee_pendiente" — `_revisar_cotizacion` returns "impuesto_fee_pendiente" (no sub) → permitted (base != fee_error requires not sub). OK. "fee_error:fee_no_concilia" → base fee_error with sub → OK.

  T. `ResultadoObjetivo.__post_init__` doesn't validate motivo against vocabulary, but it's internal. In `_subir`, `salida.motivo` for goal_inalcanzable could be "margen_imposible" or "fee_no_lineal" — both in MOTIVOS_GOAL_INALCANZABLE. OK.

  U. Possible issue: in `_subir`/`_bajar`, when `paso` returns ResultadoObjetivo "verificado", they don't check which cotizacion's price... fine.

  V. Real candidate bug: In `objetivo.paso`, when there are 2 cotizaciones, the FIRST cotizacion is checked for m1 within tolerance → verificado with primera. But the scenario: first quote at P*(ref_escenario), out of tol; second quote at P*(ref1). With 2 quotes, m2 out → goal_inalcanzable(fee_no_lineal). Matches spec rule 3: "si sigue fuera, goal_inalcanzable(fee_no_lineal)". OK.

  W. Now `cotizar_a_precio` — note the check order: `precio >= _MAX_DINERO` uses _MAX_DINERO = 10**10. Schema NUMERIC(14,4) → max ~10^10. Consistent with existing validar at line 218 (`abs(dec) >= _MAX_DINERO`). OK.

  X. quantize without explicit rounding: default context rounding ROUND_HALF_EVEN; if precio has >2 decimals, centavos != precio → ValueError. But quantize could also round e.g. Decimal("10.005") → 10.00 (half-even) → mismatch → error. Good.

  Edge: precio = Decimal("1E+2") = 100 → quantize 0.01 → 100.00, equal? Decimal("1E+2") == Decimal("100.00") → True numerically. replace price_amount=100 (not 100.00) — the identity canonical uses str(precio)? The docstring says canonical identity uses str(precio). Decimal("1E+2") str = "1E+2" vs str of quantized "100.00" — mismatch with the request body? Hmm: "El precio va en centavos exactos (r5-J3: con más de dos decimales el request cuantizado y la identidad canónica con str(precio) no coincidirían)." The check `precio != centavos` is numeric equality — Decimal("1E+2") == Decimal("100.00") is True, so it passes, but str("1E+2") != "100.00"! So a price like Decimal("1E+2") would pass validation yet its str() doesn't match the quantized request form. Does that matter? Depends on how cotizar_oferta builds the request and canonical input — need to check `construir_fee_canonical_input` and how price is serialized in the request. If the request serializes Decimal("1E+2") as "1E+2" in JSON... json.dumps of Decimal isn't native; the client probably formats. Let me check how oferta.price_amount is used in client.cotizar and construir_fee_canonical_input. This could be a real (low-severity) edge: exponent-form Decimals passing the centavos check while str forms differ. Both the request and the canonical input derive from the same price_amount though — the docstring's concern was request (quantized by server?) vs identity str(precio). Actually both use the same value so they'd be consistent with each other... The r5-J3 concern: "el request cuantizado" — maybe the request body quantizes to 2 decimals while canonical uses str(precio). Let me check the code.

  Let me look at OfertaResuelta, client.cotizar body building, and construir_fee_canonical_input.

• So the request body serializes `_decimal_a_json_number` (quantize to 4 decimals, format "f") while canonical uses `str(oferta.price_amount)`. For a price like Decimal("1E+2"), str = "1E+2" but request Amount = "100". That mismatch is exactly what r5-J3 feared — but with cotizar_a_precio's centavos check (`precio != centavos` numerically), Decimal("1E+2") passes since numerically equal to 100.00. So canonical identity str "1E+2" vs request "100" — but does that break anything? The identity (source_event_id) is derived from canonical input which includes str(price) = "1E+2". Two calls with Decimal("100") and Decimal("1E+2") would produce different source_event_ids for the same economic quote — a duplicate-quote dedup miss, not a collision. Actually the docstring says the opposite concern: request quantized vs identity str not matching — that was about >2 decimals, which IS blocked. The exponent form is an edge that produces divergent identities for the same price, but within one motor run the price comes from techo_centavo() which always returns a plain 2-decimal Decimal, so in practice unreachable from the engine; only a direct caller of cotizar_a_precio could pass exponent form. Low severity at most. Also, Decimal("100.000") == Decimal("100.00") numerically → passes; str = "100.000" vs request "100". Same class. Minor/normalization nit — baja.

  Actually wait, does it cause real harm? The docstring explicitly says identity must match the quantized request (r5-J3). With Decimal("100.000"), the check `precio != centavos` is False (they're numerically equal) so it passes; canonical str(precio)="100.000" while the request body sends "100". So the persisted identity doesn't match what was sent — a traceability mismatch the design explicitly tried to prevent, plus dedup fragmentation. Easy fix: compare `precio == centavos and precio.as_tuple().exponent >= -2` or just use `precio.quantize(...)` result / compare str. I'd report as baja (or media?). The engine only feeds techo_centavo outputs, so impact is limited to direct misuse; and cotizar_a_precio is new API intended for the engine. Baja.

  Now other candidate findings:

  1. In `reglas.decidir` — rule 10 freno: `reales` sorted by `h.fecha` — but HistorialMargen has `fecha` (date). If two rows same date... fine.

  But hold on: rule 10 spec: "Tres cambios consecutivos en la misma dirección sin que |m−goal| disminuya". Code takes `reales[-freno_cambios:]` — the LAST 3. But if there were 4 changes, the last 3 same direction non-decreasing → frena. OK.

  Subtle: `direcciones = {h.direccion for h in ultimos}` — len==1 required. OK.

  2. In `decidir` — the cooldown loop happens even when `m_actual` within tolerance. That means a recent change returns mantener(cooldown) instead of mantener(en_tolerancia). Both mantener; motivo differs. Spec doesn't fix priority between cooldown and en_tolerancia. The sealed order says cooldown before direction. Fine.

  3. `_subir`: when `PideCotizacion` returned, `_frena_regla11(entrada, m_actual, salida.precio.valor)` — regla 11 on the price TO QUOTE. But spec rule 11 is about P* (goal inalcanzable: P* > 2P or P* ≤ C+L → no movement, aviso). Applying to the quoted price (techo of P*) fine.

  But note: `_frena_regla11` also checks `precio <= costo + envio` for a SUBIDA — with denominator < 1 always true P* > C+fijo+L > C+L so never fires (docstring admits). OK.

  4. Now `_bajar` regla11 check on PideCotizacion: `precio > 2*p_actual` → for bajada, P_goal < P_actual so never; `precio <= costo+envio` → that's the real guard for bajada. Good.

  5. Real potential bug in `_subir`/`_bajar` flow: when the first cotizacion is requested, the price asked is `techo_centavo(P*)`. Then verification computes m at that price. But rule 3 says "Movimiento del día = min(P*, P·(1+escalón))" — the escalon cap is applied AFTER verification, to p_aplicado, but the verified margin corresponds to the UNCAPPED price. If the cap binds (P* > P·1.10), the applied price differs from the verified one — m(p_aplicado) ≥ goal? For subida, p_aplicado < p_objetivo → margin lower than verified... The verified m(P1) ≥ goal - tol; applying a lower price gives margin between m_actual and m(P1). Since m_actual < goal - tol and m(P1) ≈ goal, m(p_aplicado) < goal possibly. Is that per spec? Spec says movimiento del día = min(P*, tope) — yes, spec explicitly defines that. The plan's DoD says "P* hacia arriba con m(P_aplicado) ≥ goal y tope hacia abajo (116.01·1.10 → ≤ 127.61)". Hmm "m(P_aplicado) ≥ goal" — when the cap binds, p_aplicado = tope < P1, so m(p_aplicado) < m(P1) ≈ goal... Actually for subida, margin is increasing in price? d m/dP: m = 1 - (C+f+L)/(P/d)... m increases with P. So m(tope) < m(P1). If m(P1) = goal + tol, m(tope) could be below goal. The DoD parenthetical is about rounding directions, not the cap case. Spec rule 3 is explicit: movimiento = min(P*, P·(1+escalón)). So code matches spec. Fine.

  6. `_subir` guard: `if p_objetivo <= comp.p_actual.valor or p_aplicado <= comp.p_actual.valor: escenario_incoherente`. Consider: p_objetivo > p_actual but tope = piso(P*1.10). For P ≥ 1 MXN, tope ≥ P·1.10 - 0.01 ≥ P + 0.09 > P. For tiny P (< ~0.011 MXN) tope could equal P → escenario_incoherente mislabeled. Extremely unlikely price; movimiento_min_abs_mxn=1.00 would have caught it earlier (umbral=1.00 > |p_obj - P|). Non-issue.

  7. ventas.py: `if n15 < 10` — magic literal matches spec (n15 ≥ 10), but it's not config (spec doesn't make it config either). OK.

  8. ventas.py: historia check `(hoy - insumos.primera_venta).days < 75` → sin_dato historia_corta. Spec: "historia ≥ 75 días desde la primera venta". primera_venta = hoy-75 → days=75 → OK passes. Boundary correct.

  But note the ordering: `ledger_hueco` check first: `(hoy - cubierto).days > 3`. Where's 3 from? Possibly spec S2. Let me not chase; it's plausible (plan-validacion mentions "días cubiertos por ingest_run"). Could flag VERIFICAR? Let me quickly grep spec for "cubiert".

  Actually I saw in spec line ~161-162: "u15 = Σ quantity de los 15 días cubiertos hasta ayer". Let me grep the spec for "hueco" or "cubierto" definition.

  9. ventas.py `u15` guard: note `contados15` filters `d <= cubierto`, so if cubierto = hoy-3, days hoy-2, hoy-1 excluded → n15=13. Spec says "15 días cubiertos hasta ayer" — days not covered are just not counted; n15 ≥ 10 still guards. OK.

  10. objetivo.py `paso` — `exigir_decimal(tolerancia)` but the other args (costo, fijo, envio...) are validated inside precio_estrella/margen_a_precio only when those funcs are called. E.g., with cotizaciones provided and primera fails review, `ResultadoObjetivo("no_evaluado", motivo, None, ref, fijo)` — ref/fijo unvalidated but they came from derivar (validated). With zero cotizaciones and precio_estrella raising TypeError for non-Decimal costo — propagates. Fine.

  11. `_tiene_tax` vs estimacion_venta._tiene_tax_amount — duplicated logic, consistent.

  12. config.py `_numero`: `Decimal(str(valor).strip() if isinstance(valor, str) else str(valor))` — for float values, str(float) e.g. 0.1 → "0.1" fine; but float 1e-07 → "1e-07" → Decimal handles. NaN float → "nan" → InvalidOperation? Decimal("nan") → actually Decimal("nan") is valid → returns NaN → then `numero.is_finite()` check catches → ValueError. Good. Infinity → "inf" → Decimal("Infinity")? str(float('inf')) = "inf" → Decimal("inf") works (case-insensitive) → not finite → error. Good.

  13. config.py: leer_config requires ALL keys present including precio_fechas_excluidas (must exist, can be []). OK per "sin defaults".

  14. `_rango_fechas`: `date.fromisoformat(desde_raw) if isinstance(desde_raw, str) else None` — if value is a `date` object (not str), desde = None → "rango invalido". Settings come from JSON so strings. But could also legitimately be a date if caller constructs in Python. Minor: rejects date objects silently. Baja/nit.

  15. reglas.py `decidir` — `m_actual = (...)/ingreso` — ingreso > 0 guaranteed by _freno_entrada. OK.

  16. `motivo_regla11` — `p_estrella > 2 * p_actual`. Spec: "P* > 2 · P_actual". OK.

  17. In `repartir_cupo` docstring says "Las que compiten con p_actual en monedas distintas son error de quien llama: ValueError (r5-J2)" — but candidates from different platforms? cupo is per platform, fine.

  Also note `Decision.p_actual` can be None per type, but for subir/bajar it's always set via _base_senal. The monedas set handles None. OK.

  18. One more check — `reglas._freno_entrada`: `divergencia = abs(pricing - p_actual)/p_actual > divergencia_max_pct`. AC16: "Pricing 1.5% distinto → no_evaluado(precio_divergente)" with max 1%. OK. Note: uses pricing vs p_actual — but which should win? It just no-evaluates. OK.

  19. Now the freno #6: spec says "Si perdiendo_ventas es true y hay un cambio propio confirmado (subida) en los últimos 22 días". Code: `dias <= _DIAS_FRENO_SUBIDA` with dias = (hoy - enviado_en).days. "últimos 22 días" → dias <= 22 means includes day exactly 22 days ago — window of 23 days. Boundary nit: "en los últimos 22 días" — a change exactly 22 days ago: is it within? Ambiguous; plan DoD test "subida confirmada hace 10 días" only. Mutantes doc says "a 22 días ya no frena" — wait, mutantes.md line 741: "MUERTO (a 22 días ya no frena y sigue a la máquina)". Hmm, that says at 22 days it no longer frens — implying the expected boundary is dias < 22, i.e. dias <= 21! The mutant that changed boundary died because "a 22 días ya no frena". So the test expects a change 22 days ago NOT to fren. But code: `dias <= 22` → 22 <= 22 → frens! That contradicts the mutant evidence. Let me check the test to be sure. Grep tests for perdiendo_tras_subida / 22.

  That's a potentially real boundary bug (media). Let me verify in tests.

• Test `test_r1_c_r4_freno_22_si_23_no`: 22 days → frens; 23 → not. So `dias <= 22` is intended and tested. The mutantes.md line "a 22 días ya no frena" probably referred to a mutant with a different boundary (e.g. < 22 means at 22 no longer frens — i.e., the mutant). So the code matches the sealed test. Not a bug.

  Now let me double check remaining candidate issues:

  A. The exponent-Decimal edge in cotizar_a_precio (baja).

  B. `config.py._rango_fechas` rejects `date` objects (baja, nit — settings come from JSON).

  C. Check `decidir`'s rule-10: `cadena` uses `zip(cadena, cadena[1:], strict=False)` — fine.

  D. Another look at `_subir`: after verificado, `p_objetivo = salida.precio.valor`. Note the guard `if p_objetivo <= comp.p_actual.valor ... escenario_incoherente` — could a verified price legitimately be ≤ p_actual while m_actual < goal - tol? If fees non-linear... then escenario_incoherente is arguably wrong label but it's an invariant violation path. Fine.

  E. Look at `paso` second-quote logic more carefully for a REAL bug: with 1 cotizacion out of tolerance, it recomputes estrella with ref1/fijo1 and asks PideCotizacion(techo(estrella), 2). But the machine never checks that intento ordering matches: caller could pass cotizaciones in any order. A.5's job. Fine.

  F. `objetivo.paso` — `_revisar_cotizacion` raises ValueError on currency mismatch — documented deliberate.

  G. `reglas._bajar` — for bajada, `_frena_regla11` checks `p_estrella > 2 * p_actual` first — for bajada P_goal < P_actual, fine; `precio_no_cubre_costo` guards. OK.

  H. Now think about `Decision` for "subir"/"bajar": motivo=None required. In repartir_cupo replace: motivo="cuota", resultado="mantener" — replace on frozen dataclass runs __post_init__ → validation. OK.

  I. `repartir_cupo` — candidates that compiten include only `aplicado=True` (live). Spec rule 12: cuota per platform; shadow doesn't consume (rule 13 "sin cuota"). OK.

  J. ventas.py: `por_dia` built from insumos.ventas — raises ValueError on negative qty. Fine.

  K. ventas.py `evaluar_senal`: when signal condition holds and racha completes → "perdiendo". But notice: when `no_perdiendo`, racha resets to 0 — and when `sin_dato` from early guards, racha=0 too. What about "racha_incompleta" returning racha — the caller must persist racha from the SenalVentas. If next day the early guard fails (e.g. dia_sin_stock), racha resets. "3 corridas consecutivas" — consecutive days where signal true; a sin_dato day breaks consecutiveness. Defensible.

  L. Hmm, one more in ventas: `if not Decimal(u15) < esperado: return no_perdiendo`. esperado = u60*n15/n60*(1-caida). If n15=0? n15 < 10 guard returns earlier (0 < 10). OK. n60 == 0 guarded. Division fine.

  M. Check `contados60`: no `d <= cubierto` filter — comment explains: cubierto ≥ hoy−3 and window ends hoy−16. But what if cubierto < hoy-16? Can't be: ledger_hueco if (hoy - cubierto).days > 3 → cubierto ≥ hoy-3 > hoy-16. But wait — what if cubierto ≥ hoy-3 but the ledger has holes WITHIN the 60-day window (days not covered)? dia_cubierto_hasta only says coverage up to that date; presumably coverage is contiguous from ingest_run. Fine.

  N. `_en_rango` inclusive both ends — config doc says ranges inclusive. OK.

  O. Now `tipos.py` `CambioPrevio.enviado_en: date` and `HistorialMargen.fecha: date`. In decidir, `(hoy - cambio.enviado_en).days`. OK.

  P. reglas.py freno #6 vs spec rule 6: spec says freno when perdiendo is true. Code checks `entrada.senal.estado == "perdiendo"` — right. But rule 4 says "Bajar solo si ... y no aplica la regla 6" and rule 6 says "no se sube ni se baja". Code places #6 before direction split but AFTER... wait, actually #6 is evaluated before direction, so it also blocks subir. Spec rule 6: "no se sube ni se baja". Matches.

  Q. Ordering issue: freno #6 before cooldown — docstring says freno before cooldown "porque lleva aviso". Fine.

  R. Now one thing in `_freno_entrada`: when `entrada.motivo_estimacion is not None` → `_no_evaluado(entrada, entrada.motivo_estimacion)`. But _base_senal accesses entrada.escenario.componentes.p_actual — if estimation failed, is escenario still present? EntradaDecision requires escenario: Escenario (non-optional). So caller must always supply an escenario even when motivo_estimacion set. Hmm — if estimation failed (e.g. fee_ausente), can the caller still construct a full Escenario with fee_detalles, precio_cotizado, etc.? That could be an API design friction: motivo_estimacion signals estimation failure but escenario is mandatory. Maybe the caller passes the last available escenario. VERIFICAR? This is A.5 integration; out of diff scope. Not a bug per se — but worth a low note? The componentes get registered in the Decision even when no_evaluado — that's intended (S5 registers components). Skip.

  S. `Decision.componentes` — in _base_senal always set. OK.

  T. Also check: `_no_evaluado` when motivo not permitted → "estimacion_motivo_desconocido" with crudo in diagnostico — matches r2-A1.

  U. `Importe.__post_init__` — `self.moneda.strip()` nonempty; no uppercasing. Fine.

  V. objetivo.py `_suma_top` vs fee_total — cotizacion error estado returns early. OK.

  W. Now, config `_fraccion` for "precio_movimiento_min_pct" minima "0" — allows 0; spec cota 0–0.10. OK. escalon 0.01–0.25 matches. tolerancia 0–0.05 matches. divergencia 0–0.10 matches. caida 0.10–0.90 matches. senal_dias 1–7 matches. u60_min 1–1000 matches. dias_entre_cambios 1–30 matches. freno_cambios 2–10 matches. All match the plan table.

  X. `leer_config` does NOT read precio_cap_amazon_mx etc. (cuota lives elsewhere A.5) — fine for A.2 scope.

  Y. `movimiento_min_abs_mxn/_usd` `_positivo` — cota "> 0" matches.

  Z. One more potential issue: in `objetivo.paso`, when `len(cotizaciones) == 2` and the FIRST is within tolerance → verificado with primera. Fine.

  Also in `paso`, the tolerance check `abs(m1 - goal) <= tolerancia` — spec: "si |m(P₁) − goal| > tol, una segunda". So <= tol → verified. Boundary correct.

  AA. `margen_a_precio` division by ingreso: ingreso = precio/divisor; precio > 0? cotizacion prices from techo_centavo(P*) > 0 since numerator positive... P* could be negative if C+fijo+L negative? costo/fijo/envio could sum negative? Unlikely; if P* negative, techo negative, cotizacion at negative price — cotizar_a_precio would reject (precio <= 0 → ValueError) at A.5. But paso itself would emit PideCotizacion with negative price. Hmm: precio_estrella raises only when denominador <= 0. Numerator (costo+fijo+envio) could be negative only with negative cost inputs — exigir_decimal doesn't check sign. Edge: fijo = fee_total - referral could be negative if data weird. Low; regla11 would catch P* <= C+L? If numerator negative and denominator positive, P* negative ≤ C+L... only if C+L ≥ 0. motivo_regla11 checks precio <= costo+envio → negative price <= costo+envio (if costo+envio ≥ 0) → caught. If costo+envio also negative... pathological. Skip.

  BB. Real check — `_subir` applies `_frena_regla11` to the PideCotizacion price and if frenado returns Decision goal_inalcanzable. Good: "sin movimiento, aviso".

  CC. `motivo_regla11` docstring admits precio_no_cubre_costo unreachable for subida. For bajada reachable. OK.

  DD. tests exist (test_precio_reglas.py, 2000+ lines). Good.

  EE. Check architecture purity: app/precio imports app.estimacion_venta (pure?) and app.precio.*; reglas imports dataclasses replace, datetime date, decimal. No I/O. objetivo imports app.estimacion_venta DetalleFee — check estimacion_venta doesn't do I/O at import time. It imports psycopg maybe but module-level side effects unlikely. There may be a test_architecture candado. Fine.

  FF. `app/precio/__init__.py` says "Sin I/O ni reloj" but config/objetivo don't use clocks. OK.

  GG. `cotizar_a_precio` in estimacion_fees.py — inside function `from dataclasses import replace` — function-level import; ruff might flag? It's legal; maybe style. Estimacion_fees already imports dataclass stuff? Check top imports — `from dataclasses import ...`? Line 40 imports replace locally. Why? Maybe to avoid... whatever, it's odd but harmless. Actually check if `replace` already imported at module top. Grep showed line 21-24 imports; let me check dataclasses import in that file. If already imported at top, the local import is redundant nit.

  Let me check.

• OK, the local import of replace is a minor style nit (could be top-level) — harmless.

  Also check the OfertaResuelta has price_amount field (dataclass line 84 presumably). Yes — cotizar uses oferta.price_amount everywhere.

  One more: `cotizar_a_precio` — `precio >= _MAX_DINERO` check duplicates `_decimal_dinero` validations; fine.

  Also note the `except InvalidOperation: raise ValueError ... from None` — fine.

  Now, the exponent edge: `precio != centavos` — numeric equality. Decimal("100.000") == Decimal("100.00") → True → passes, but `str(precio)` = "100.000" goes into canonical input while the request sends "100". The docstring explicitly cites r5-J3: identity vs request mismatch. So the guard fails to catch normalization-equivalent-but-string-different Decimals. Impact: identity fragmentation/dedup miss; and contradicts the stated invariant. Severity baja (in practice the engine feeds techo_centavo output, always exponent -2).

  Anything else? Let me reconsider `repartir_cupo` — cupo per platform and candidates may mix platforms? Caller passes per platform. Fine.

  One more possible issue in `reglas.decidir` — the cooldown loop: `if dias < config.dias_entre_cambios` — for a change sent TODAY (dias=0) → cooldown. Spec: "Un cambio cada 7 días" — a change 7 days ago: dias=7, 7 < 7 false → allowed. Boundary OK.

  And in cooldown loop, `cambio.estado` is NOT filtered — per the Reject list ("cooldown que ignore no_confirmado/error" is rejected → must count them). Code counts all — matches. Good.

  Also `_freno_entrada` divergence: uses `pricing.precio` vs `p_actual` but never uses pricing otherwise in the diff (the divergence is a control only). OK per S2 control.

  Now, the `_subir` — when `salida` is `PideCotizacion` and regla11 frena → returns Decision goal_inalcanzable — but note `_frena_regla11` sets `m_actual` and prioridad. OK.

  Potential issue: in `_subir`/`_bajar`, when `paso` returns PideCotizacion with intento=2 (second quote), `_frena_regla11` is applied again — fine.

  Edge in `paso`: the machine trusts `cotizaciones` order (primera = intento 1). If A.5 passes them reversed, m1 computed on the second quote... internal contract; the intento field isn't in CotizacionVerificada. VERIFICAR? It's A.5's contract; PideCotizacion carries intento. Not a bug in this diff.

  Let me also double-check `objetivo.py` line: `denominador = (Decimal(1) - isr_tasa - goal) / divisor - ref`. With incluye_iva, divisor = iva_divisor (e.g. 1.16). I = P/1.16. R = r*I. m = contrib/I = goal. Derived earlier correct.

  But wait — in `margen_a_precio`, ingreso = precio/divisor, fees = ref*precio + fijo. In `precio_estrella`, denominator uses (1 - r - goal)/divisor - ref, numerator (C+fijo+L). Consistent.

  One inconsistency check with `_coherencia_escenario`: I vs P/divisor tolerance 0.01 absolute — fine.

  Now the racha semantics one more: `evaluar_senal` returns `sin_dato("racha_incompleta", racha)` — SenalVentas requires submotivo in vocab when sin_dato — "racha_incompleta" is in SUBMOTIVOS_SIN_DATO. OK. And `reglas` builds motivo f"ventas_sin_dato:{submotivo}" — permitted.

  But note: `reglas.decidir` — when senal sin_dato and m > goal+tol → mantener(ventas_sin_dato:...). But when m < goal - tol (subir), senal isn't consulted at all — correct per spec (subir doesn't need ventas).

  Also when senal is sin_dato, perdiendo=False in Decision base (senal.estado == "perdiendo" → False). OK.

  I think the findings list:

  1. (baja) cotizar_a_precio: la validacion de centavos exactos usa igualdad numerica (`precio != centavos`), asi que Decimals equivalentes con otra representacion (Decimal("1E+2"), Decimal("100.000")) pasan, pero `str(precio)` (que va a la identidad canonica, linea 116) difiere del Amount cuantizado del request — exactamente el desfase que r5-J3 dice evitar; fragmenta la identidad/dedup. Fix: comparar representacion (p.ej. `precio == centavos and precio.as_tuple().exponent >= -2`) o normalizar `precio = centavos` antes de replace.

  Hmm wait — actually if precio = Decimal("100.000"), `precio != centavos` is False (numerically equal), passes; then replace uses precio (100.000). str = "100.000". Request amount: _decimal_a_json_number quantizes to 4 decimals → "100". Canonical: "100.000". Mismatch. Yes.

  2. (baja) config._rango_fechas: si el rango llega con objetos `date` (no str), cae en "rango invalido" aunque sea valido — settings vienen de JSON asi que es defensivo, pero el mensaje es enganoso; nit.

  3. (baja) import local `from dataclasses import replace` dentro de cotizar_a_precio pudiendo ser top-level junto a `dataclass` — nit de estilo. Probably not even worth listing. Maybe skip.

  Any media findings? Let me think harder about real logic risks.

  Reconsider `decidir` rule 10 (no_converge): spec "Tres cambios consecutivos en la misma dirección sin que |m−goal| disminuya → frenado". Code requires the chain INCLUDING today's distancia to be non-decreasing: cadena = [d_-3, d_-2, d_-1, distancia_hoy], all(posterior >= anterior). Consider scenario: distances 0.10, 0.12, 0.11 (last change DID diminish), today 0.15. Code: 0.10<=0.12 True, 0.12<=0.11 False → no freno. Correct per spec (one change diminished → not "sin que disminuya"). Scenario 0.10, 0.12, 0.14, today 0.09 (improved today): code → 0.14<=0.09 False → no freno. Spec literal: "tres cambios sin que disminuya" — the three changes didn't diminish; today's improvement isn't a cambio. Strictly, spec says frena. Code doesn't. Is that a bug or a sane refinement? Arguably the improvement today means convergence resumed — the code's choice is defensible and likely deliberate (the mutantes doc would show). Hmm, also note: including today's distance means the freno only fires if STILL not converging. I could flag as VERIFICAR/baja: la regla 10 incluye la distancia de hoy en la cadena monotona; la lectura literal de S4 #10 ("tres cambios consecutivos sin que disminuya") frenaria aunque hoy mejoro. Let me check the test to see if sealed. Grep test for no_converge.

• The tests cover no_converge scenarios with distances all equal (0.05, 0.05, 0.05) → frens (non-decreasing). And "convergiendo" case doesn't fren. The today-distance-in-chain question: not clearly tested here, but this is a deliberate sealed semantic (r1/r2/r4 markers suggest multiple review rounds). I won't push it as a bug; at most a VERIFICAR? The behavior "doesn't fren if today improved" — the plan AC7 says "Tercer cambio sin acercarse al goal → frenado(no_converge)". The code frens when 3 changes non-decreasing AND today didn't improve. Given extensive test sealing with markers from multiple review rounds, this is deliberate. Skip.

  Now finalize findings. Honestly the code is well-reviewed already (mutantes doc shows extensive adversarial testing). My genuine findings:

  1. Baja: cotizar_a_precio centavos check numeric vs representation (r5-J3 mismatch) — Decimal("100.000")/("1E+2") pass the check but str(precio) in canonical identity ≠ quantized Amount in request.

  2. Baja: config._rango_fechas rejects date objects silently with misleading "rango invalido" (nit; settings are JSON).

  Anything media? Let me think once more about `_subir`'s movimiento_minimo check: `abs(p_objetivo - p_actual) < umbral` where p_objetivo is the VERIFIED price (P1 or P2), not the capped p_aplicado. Spec rule 8: "Si |P_objetivo − P_actual| es menor..." — P_objetivo, matches. OK.

  And `_bajar` same. OK.

  One more: `repartir_cupo` — candidates that compiten require `decision.aplicado` True. In live mode `_subir` sets aplicado=True. But the same decision in shadow (aplicado=False) doesn't consume cupo — spec 13 "sin cuota". OK.

  Another: in `repartir_cupo`, when a competing decision loses, replace sets motivo="cuota" but leaves `aplicado=False` and p_aplicado=None — but keeps p_objetivo. And prioridad kept. OK.

  Also `repartir_cupo` raises on mixed currencies among compiten — but what if candidates from MX and US both compiten with same cupo? Caller error per contract. OK.

  Security: no secrets, no I/O. Fine.

  One more check on `objetivo._revisar_cotizacion`: `cot.estado == "error"` returns fee_error:... before tax check — fine. But note for estado "success" with empty detalles and fee_total 0: suma_top = 0 == fee_total 0 → passes review, then derivar_ref_fijo raises referral_ausente → no_evaluado. OK.

  And `_tiene_tax` recursive generator — `_tiene_tax(det.included_fee_details)` inside `any(... or _tiene_tax(...) for det in detalles)` — fine.

  Check `ResultadoObjetivo` returned by paso "verificado" carries ref1/fijo1 — used by caller? reglas ignores ref/fijo from salida. Fine.

  Edge in `decidir`: `entrada.cambios` could include cambios from other listings? Caller contract. OK.

  Also `decidir` cooldown: iterates ALL cambios; if two cambios, first triggers return. Order of entrada.cambios irrelevant since any triggers. OK.

  `_freno_entrada` monedas: checks 6 componentes same moneda, then pricing moneda matches. But doesn't check escenario.precio_cotizado moneda vs — yes it does in _coherencia_escenario. OK.

  Now — one thing worth double-checking: `config.leer_config` error message contains "escalón" with accent — project convention says "sin acentos en el código". Line: "consumiría cooldown y cuota" and "quedaría" and "mínimo" — accents in code strings/comments! The convention: "Español en comentarios, docstrings, tests y docs; sin acentos en el código". The diff has multiple accents in comments/docstrings/messages: "propósito" (estimacion_fees docstring), "escalón", "quedaría", "mínimo", "está" (repartir_cupo docstring: "pasa idéntico"), "máquina", "opción" in reglas.py comments. Ruff won't catch it, but repo convention explicitly says no accents in code. Is there a lint for it? pre-commit may include a check. Let me check .pre-commit-config or ruff config for an accent check. Actually many comments in the diff contain accents: "a propósito", "cotización", "máquina", "está", "idéntico", "demás". If the repo enforces sin acentos, CI/pre-commit would have failed — the commit exists, so presumably no automated check. It's a convention nit. Baja. Let me quickly check if there's a checker (tools/ or pre-commit) for accents.app/api_dashboard.py
app/api_fabrica.py
app/apply_cola.py
app/apply_harvest_reconciliacion.py
app/apply_harvest.py
Existing files already contain accents, so the "sin acentos" convention isn't enforced and is widely violated — not worth flagging.

  Final findings list. Let me weigh once more whether there's anything media-level I'm missing.

  Reconsider `cotizar_a_precio` — validation order: `precio >= _MAX_DINERO` — but `_decimal_dinero` in the parsing path uses 4-decimal NUMERIC(14,4) limit 10^10. Price with 2 decimals max ~10^10. Consistent.

  Reconsider `objetivo.paso` first-branch: `PideCotizacion(Importe(techo_centavo(estrella), moneda), 1)` — if estrella negative (numerator negative), Importe allows negative? Importe only checks Decimal finite. techo_centavo of negative → negative Importe → PideCotizacion emitted → regla11 wrapper: motivo_regla11(precio<0, ...) → precio <= costo+envio? If costo+envio ≥ 0 → yes → goal_inalcanzable. OK guarded by wrapper in reglas. But `paso` called directly (as public API) could emit negative price request. Internal use only via reglas. Fine.

  `techo_centavo` on a huge P* (denominator tiny positive) → enormous price; regla11 2*P check catches. OK.

  `_frena_regla11` is applied in `_subir`/`_bajar` to PideCotizacion — good.

  One more: `reglas.decidir` computes m_actual BEFORE checking frenos — fine.

  `_freno_entrada` ordering: motivo_estimacion → pricing None → monedas → pricing moneda → precios positivos → divergencia → coherencia → moneda MXN/USD → ingreso > 0. Note: `_coherencia_escenario` runs BEFORE checking moneda in (MXN,USD) and before ingreso>0 — fine.

  But hmm: `_freno_entrada` checks `pricing.precio.valor <= 0` and divergence, but never uses `pricing` afterwards — the actual decision uses comp.p_actual (scenario). pricing is just a freshness control (S2). OK.

  `_coherencia_escenario` — `escenario.precio_cotizado.valor != comp.p_actual.valor` — exact Decimal equality. If precio_cotizado = Decimal("100.00") and p_actual = Decimal("100.0")? == is numeric in Decimal → True. Fine.

  I'm fairly confident the remaining findings are minor. Also possibly worth noting as VERIFICAR nothing — I verified everything I flagged.

  One more candidate: `ventas.py` guard order puts `historia_corta` BEFORE `n60==0/u60_min/n15` — spec order listed u60, n15, historia, inventario; but docstring declares the sealed order from brief. OK.

  And `evaluar_senal` — when `dia_cubierto_hasta` is None → ledger_hueco. OK.

  Also `evaluar_senal` racha param `racha_previa` — the caller reads racha from yesterday's senal. But note: `sin_dato` early returns reset racha to 0 EXCEPT racha_incompleta which increments. And no_perdiendo resets to 0. Consistent with "consecutivas".

  Wait, one subtle: when the early guard fires (e.g. u60_bajo_minimo), racha resets to 0. But suppose perdiendo was true for 2 days, then a u60 dip day (sin_dato), then true again — racha restarts. "3 corridas consecutivas" — defensible.

  Also `decidir`: senal sin_dato + m < goal - tol → _subir proceeds (subida no requiere ventas). Per spec yes.

  Final: also double check `repartir_cupo` type of candidates tuple[tuple[int, Decision], ...] — listing_id int. OK.

  I'll write up the review: mostly clean; findings:

  1. (baja) cotizar_a_precio: centavos check numeric-only; exponent/trailing-zero Decimals bypass r5-J3 intent (canonical str vs request Amount mismatch).

  2. (baja) config._rango_fechas: date objects rejected as "rango invalido" — misleading; JSON-only assumption undocumented.

  Maybe also:
  3. (baja) `from dataclasses import replace` local import inside function — style nit, module already imports from dataclasses at top.

  And a VERIFICAR? Everything else checked out. Should I mention the rule-10 today-in-chain nuance? It's deliberately sealed with tests (test_reglas_no_converge_tres_sin_acercarse etc.). Actually let me quickly check whether a test covers "3 cambios sin disminuir pero hoy mejoró". Search test file for that scenario... test_r4_g1 and test around line 1990 "convergiendo" — hist 0.08... let me not dig further; the semantics is plausible and tested-adjacent. I could add it as VERIFICAR with low priority: "la cadena monotona incluye la distancia de hoy; la lectura literal de S4 #10 frenaria con 3 cambios sin disminuir aunque hoy haya mejorado". Actually let me quickly check the convergiendo test to see if it covers this.The converging case tested is distances diminishing among the 3 changes (0.08, 0.07, 0.06) — not the "3 non-decreasing but today improved" case. So the inclusion of today's distance in the chain is not directly tested either way. It's a semantic choice: literal S4 #10 would fren; code doesn't if today improved. I'll report it as VERIFICAR (baja): confirm with the lead that the current-day distance belongs in the monotonic chain, since the spec's literal reading frens on the 3 changes alone.

  Now write the final review in Spanish, plain text, numbered list with severities.

To resume this session: kimi -r session_2378e515-861a-4c94-a5fc-4f9c4aa4aa5d


=== Revisor efectivo: kimi (binario 'kimi') ===


---
sha=39cba88568c74dca2a826dd192df725b84cdd186 archivos=app/estimacion_fees.py,app/precio/__init__.py,app/precio/config.py,app/precio/objetivo.py,app/precio/reglas.py,app/precio/tipos.py,app/precio/ventas.py inicio=2026-09-18T17:17:14Z fin=2026-09-18T17:28:50Z exit=0
