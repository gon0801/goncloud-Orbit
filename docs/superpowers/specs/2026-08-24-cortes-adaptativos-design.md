# Cortes adaptativos per-producto (NEGATIVE_EXACT + PAUSE)

> Spec de diseño aprobado por el dueño (2026-08-24) vía brainstorming
> estructurado. Problema: los umbrales fijos de cortes (clicks≥20 negative /
> ≥25 pause) son prematuros para un catálogo de rotación lenta — caso real
> "arras for wedding ceremony": el motor negativizaría el término core.
> Solución: umbral adaptativo con evidencia PROPIA del producto y fallback
> estricto. Este spec es el contrato; el plan de tareas vive en
> `plans/cortes-01.md`.

## Decisiones del dueño (selladas en el brainstorm)

1. **Unidad de evidencia: AD GROUP como proxy del producto** (evaluó y
   descartó el ASIN literal: exigía ingesta nueva — advertised-product
   report + kind nuevo — sin necesidad hoy; verificado con datos vivos que
   sus campañas activas tienen 1 ad group c/u y son mono-producto). La
   evidencia del ad group = SUMA de sus hojas keyword+product_target.
   Escalada futura declarada: ASIN literal SOLO si aparece mezcla real de
   ASINs por ad group.
2. **Alcance: AMBOS cortes** — NEGATIVE_EXACT y PAUSE (misma estructura de
   corte prematuro).
3. **Enfoque 1 — maquinaria única compartida**: una ventana de evidencia,
   una elegibilidad, UN multiplicador M compartido, DOS fallbacks (espejo
   de la asimetría 20/25 actual). Aterrizaje en secuencia: negative
   primero, pause después (orden de tareas, no de diseño).
4. **Los 7 números sellados**: `O_min=3` órdenes, `C_min=60` clicks,
   `Z_min=14` fechas distintas, `M=1.5`, `F_neg=40`, `F_pause=50`,
   `L=90` días.

## La regla

Por ad group, una vez por ciclo:

```
evidencia(ad_group) = suma de sus hojas keyword+product_target en
                      [D-L, D-10], colapso bitemporal (v_metric_latest),
                      envenenamiento por None estándar de windows.py
califica ⟺ orders ≥ 3 ∧ clicks ≥ 60 ∧ fechas_distintas ≥ 14   (las TRES)
expected_clicks = total_clicks / total_orders          (solo si califica)
umbral(negative) = ceil(expected_clicks × 1.5)  si califica, si no 40
umbral(pause)    = ceil(expected_clicks × 1.5)  si califica, si no 50
```

- **NEGATIVE_EXACT**: `orders=0 ∧ clicks_término ≥ umbral(negative) ∧
  cost ≥ {us:8, mx:130}` en ventana madura ≥10d; ASIN-like jamás.
- **PAUSE**: `orders=0 ∧ clicks_entidad ≥ umbral(pause) ∧
  cost ≥ {us:12, mx:200}` en ventana de cortes madura.
- Intactos: pisos de cost por plataforma, maduración ≥10d, precedencia
  (PAUSE gana), motivos `negative_umbral`/`pause_umbral`, prohibición
  ASIN-like.

**Orden de evaluación (un camino)**: compuertas base → ad group →
evidencia → elegibilidad → umbral → comparación → congelar en `inputs`:
`umbral_clicks_usado`, `elegible`, `expected_clicks` (null si no calificó),
`evidencia {clicks, orders, fechas, ventana}`.

**Regla 3**: producto nuevo, historia corta o evidencia envenenada por
None → NO califica → fallback. Jamás un número inventado, jamás promedios
de cuenta/categoría.

Sanity con el caso real: ad group de arras a ~1 orden/50 clicks → umbral
75; el término con 116 clicks/0 ventas SIGUE cortándose (excede lo que su
producto necesita). Muere el corte a 20-25 clicks en productos que
necesitan 50 para vender.

## Arquitectura

- **Números = constantes en código** junto a los umbrales sellados
  existentes (misma práctica que 20/25/8/130): cambio = PR con review, no
  config en caliente.
- **`windows.py`** (única puerta a la DB): función nueva
  `ventanas_evidencia_ad_group(platform)` — una consulta por
  plataforma/ciclo, suma colapsada por ad group en [D-90, D-10],
  envenenamiento estándar.
- **`cycle.py`**: la llama una vez y pasa el diccionario al motor.
- **`hygiene.py`/`bid.py`**: reciben el umbral RESUELTO por una función
  compartida única (ambas reglas, cero deriva); el motor sigue puro (los
  candados de arquitectura no se tocan).
- **Golden replay**: el replay usa `inputs.umbral_clicks_usado` si existe;
  si no, las constantes legacy 20/25 — las decisiones históricas de shadow
  se reproducen exactas. Test regla 9.
- **Secuencia**: paralelo-seguro con ORBIT 04 Phase 1 (archivos
  disjuntos); aterriza ANTES de que ORBIT 04 Phase 2 toque `cycle.py` y
  ANTES del cutover de ORBIT 05 (el shadow debe validar la regla NUEVA
  varios días — los cortes que el apply ejecute serán los de esta regla).
- **Dashboard**: cero cambio de contrato; los conteos de skips en Salud
  suben (esperado).

## Riesgos aceptados (declarados)

1. **CPO extremo** → umbral enorme → ese ad group casi nunca corta:
   visible en dashboard + veto 48h de ORBIT 04 como red. Sin tope (sería
   un 8º número especulativo).
2. **Fallback dominante al inicio** (densidad real: 296/12,252 filas-hoja
   con órdenes): F_neg/F_pause son las decisiones más influyentes; por eso
   son deliberadamente > 20/25.
3. **Reduce la tasa de corte a propósito** — contra la lección "las
   señales aumentan acción útil" del diseño v2; sellado conscientemente
   por el dueño (caso arras).
4. **PAUSE mueve más dinero**: misma red de veto + el delta se observa en
   shadow pre-cutover.

## Testing

- Regla 9 demostrada fallando en: cada mínimo de elegibilidad discrimina;
  None envenena → fallback; adaptativo vs fallback; replay legacy 20/25;
  camino único compartido NEGATIVE/PAUSE.
- Regla 8: SELECT vivo de la ventana de evidencia antes de fijar tests.
- Goldens: los sellados históricos NO se tocan; goldens nuevos para la
  regla nueva.
- Cierre: ciclo shadow con regla nueva + reporte de delta de decisiones
  vs ciclo anterior (evidencia para el dueño).
