# Cortes adaptativos per-producto (NEGATIVE_EXACT + PAUSE) — v2

> Spec aprobado por el dueño (2026-08-24) vía brainstorming estructurado;
> v2 tras ronda 1 de cross-review (codex 4A+4M, grok 3A+7M — todo
> incorporado; el dueño selló además el PISO del umbral). Problema: los
> umbrales fijos (20/25) son prematuros para rotación lenta (caso arras:
> 116 clicks / 0 ventas en término core). Contrato: este spec; el plan de
> tareas vive en `plans/cortes-01.md` (precedencia spec > plan).

## Decisiones del dueño (selladas)

1. **Unidad de evidencia: AD GROUP como proxy del producto** (evaluó y
   descartó ASIN literal: ingesta nueva sin necesidad; sus ad groups son
   1:1 mono-producto, verificado con datos vivos). Evidencia = suma de las
   hojas keyword+product_target del grupo.
2. **Alcance: AMBOS cortes** (NEGATIVE_EXACT y PAUSE).
3. **Maquinaria única compartida**: una ventana, una elegibilidad, UN
   multiplicador, DOS fallbacks. Secuencia de aterrizaje: negative → pause.
4. **Números sellados**: `O_min=3`, `C_min=60`, `Z_min=14`, `M=1.5`,
   `F_neg=40`, `F_pause=50`, `L=90`.
5. **PISO sellado (ronda 1, grok)**: `umbral_final = max(legacy, umbral)`
   con legacy 20 (negative) / 25 (pause) — el adaptativo solo puede SUBIR
   umbrales, jamás bajar de los actuales. Sin piso, un producto de
   conversión rápida (60 clicks/6 órdenes → expected 10) quedaría con
   umbral 15: más agresivo que hoy, contra el propósito del plan.

## La regla

Por ad group, una vez por ciclo (dentro de TX2, REPEATABLE READ):

```
D = _fecha_utc(decided_at)          (mismo reloj UTC que la madurez;
                                     SIN el ajuste -3d de frescura)
ventana_evidencia = BETWEEN D-90 AND D-10   (literal; son 81 fechas
                    maduras — L=90 es LOOKBACK desde D, no longitud;
                    NO se usa el helper inicio_ventana del patrón 30d)
evidencia(ad_group) = suma de hojas keyword+product_target del grupo en
                      esa ventana, colapso bitemporal (v_metric_latest),
                      envenenamiento por None estándar;
                      fechas_distintas = COUNT(DISTINCT metric_date) del
                      GRUPO (unión de fechas — dos hojas el mismo día = 1)
califica ⟺ orders ≥ 3 ∧ clicks ≥ 60 ∧ fechas_distintas ≥ 14
expected_clicks = Decimal(total_clicks) / Decimal(total_orders)
umbral_bruto(regla) = ceil(expected_clicks × Decimal("1.5"))   si califica
                      — ceil DEL PRODUCTO (jamás ceil-luego-multiplica);
                      con M=1.5 equivale al racional ceil(3·clicks/2·orders)
                    = F_neg=40 | F_pause=50                    si no
umbral_final(regla) = max(legacy_regla, umbral_bruto(regla))
                      legacy: 20 negative / 25 pause
```

- **NEGATIVE_EXACT**: `orders=0 ∧ clicks_término ≥ umbral_final(neg) ∧
  cost ≥ {us:8, mx:130}` — el término sigue midiéndose en SU ventana
  madura existente. **PAUSE**: `orders=0 ∧ clicks_entidad ≥
  umbral_final(pause) ∧ cost ≥ {us:12, mx:200}` — la entidad sigue en SU
  ventana de cortes existente. **La ventana de 90d SOLO resuelve el
  umbral; las ventanas de comparación de término/entidad NO cambian**
  (sellado explícito: "alinear" a 90d sería otro contrato).
- Intactos: pisos de cost, maduración ≥10d, precedencia PAUSE>bandas,
  motivos, prohibición ASIN-like.

**Regla 3**: grupo sin datos, historia corta o evidencia envenenada → NO
califica → fallback. Grupo ausente del diccionario → fallback. Jamás un
número inventado.

## Contrato de inputs congelados (sellado, ronda 1)

**Clave TOP-LEVEL común `inputs.corte`** en TODA decisión producida por
`decide_bid` y `decide_hygiene` — **incluidas las decisiones cuyo kind
final es `bid`** (el motor evalúa PAUSE antes de las bandas: sin el
umbral congelado, el replay de un bid histórico podría convertirse en
pause). Shape idéntico en ambos motores:

```
inputs.corte = {
  umbral_clicks_usado: int,        (el FINAL, con piso aplicado)
  elegible: bool,
  expected_clicks: string|null,    (Decimal serializado como string)
  evidencia: { clicks, orders, fechas, ventana_desde, ventana_hasta,
               observed_at_max } | null
}
```

`inputs.termino` y `inputs.ventanas` NO cambian de contrato.

**Sello bitemporal (ronda 1, codex)**: `evidencia.observed_at_max` se
congela y `decision.data_observed_at = max(observed_at del dato directo,
observed_at_max de la evidencia)` — la edad del dato decidido incluye la
evidencia; con test donde la evidencia es la observación más reciente.

**Replay**: `reproduce()` LEE `inputs.corte.umbral_clicks_usado` (jamás
recalcula evidencia); fila sin `inputs.corte` (histórica) → legacy 20/25.

## Arquitectura

- Números = constantes en código (misma práctica que 20/25/8/130).
- **`umbral_corte(evidencia, regla)` vive en módulo puro NUEVO
  `app/optimizer/cortes.py`** (hygiene ya importa bid: meterla en hygiene
  crearía import circular; en windows violaría la frontera IO). El motor
  RECIBE el umbral resuelto (int); `cycle.py` resuelve.
- **`windows.py`**: `ventanas_evidencia_ad_group(conn, platform,
  decided_at)` — firma real del patrón del repo; una consulta por
  plataforma/ciclo dentro de TX2.
- **`cycle.py`**: `_SQL_DECISORAS` agrega `parent_id AS ad_group_id` (hoy
  no lo selecciona y PAUSE no podría mapear su evidencia); congela
  `inputs.corte` en todas las decisiones de ambos motores.
- **Golden replay — la verdad de cómo funciona (ronda 1, ambos)**: el
  golden del repo siembra un CICLO VIVO y lo reproduce — no hay fixtures
  históricos. Por tanto: (a) las siembras/expectativas de los goldens de
  ciclo SE ACTUALIZAN para que los 4 kinds sigan disparando bajo la regla
  nueva (declarado: los goldens de ciclo NO quedan intactos — son
  fixtures sintéticos, se re-siembran); (b) la compatibilidad legacy se
  demuestra con tests NUEVOS de `reproduce()` sobre fixtures de inputs
  SIN `inputs.corte` → 20/25, demostrados rojos sin el compat; (c) golden
  nuevo: una decisión `bid` cuyo resultado depende de que el umbral
  adaptativo de pause BLOQUEÓ el PAUSE (sin el freeze, rejugaría como
  pause).
- **Secuencia**: antes de ORBIT 04 Phase 2 y del cutover ORBIT 05.
  **Contrato con ORBIT 04 sellado (ronda 1)**: su re-validación al
  liberar un corte RE-RESUELVE `umbral_corte` con evidencia FRESCA (no
  reusa el congelado ni se limita a "¿vendió?") — el DoD de su tarea 2.2
  queda actualizado en `plans/orbit-04.md` en este mismo PR.
- **Dashboard**: cero cambio de contrato; skips suben en Salud.

## Riesgos aceptados (declarados)

1. CPO extremo → umbral enorme → ese grupo casi nunca corta: visible en
   dashboard; nota honesta (grok): el veto 48h es red de "sí cortó", no
   de "no cortó" — el no-corte solo lo vigila el dashboard.
2. Fallback dominante al inicio (296/12,252 filas-hoja con órdenes).
3. Reduce la tasa de corte a propósito (sellado por el dueño, caso arras).
4. PAUSE mueve más dinero: veto 48h + shadow pre-cutover.

## Testing

- Regla 9 demostrada fallando: cada mínimo de elegibilidad discrimina
  (2/3 órdenes, 59/60 clicks, 13/14 fechas); ceil con fraccionario Y
  entero (61/3 → expected 20.33… → umbral 31; 50 → 75); piso
  (expected×M < legacy → gana legacy); None envenena → fallback; unión de
  fechas (overlap multi-hoja el mismo día = 1); replay legacy rojo sin
  compat; golden bid-que-bloqueó-pause; bitemporal max.
- Regla 8: SELECT vivo de la ventana de evidencia antes de fijar tests.
- Cierre: **reporte delta CONTRAFACTUAL** — mismo snapshot y reloj,
  maquinaria pura corrida con umbrales legacy vs adaptativos (no dos
  ciclos consecutivos: mezclaría el cambio de regla con datos nuevos);
  evidencia por decisión con su umbral, entregada al dueño.
