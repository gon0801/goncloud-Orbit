# ORBIT 19 / B.R — Revision independiente del bloque B

## 1a ronda — commit `8b4f7fe` (2026-09-07)

Veredicto: **CHANGES_REQUESTED**. Informe completo del reviewer subagent
(verbatim en el historial de la sesion; resumen):

- **Bloqueante 1**: `_SQL_ADS_VENTANA` sumaba TODAS las re-observaciones
  append-only de la misma fecha (el cron D-31..D-1 re-observa cada fecha ~31
  veces) → gasto/ventas inflados en la primera corrida productiva. El test
  existente lo enmascaraba usando valores identicos en las re-lecturas.
- **Menor 2**: `_suma` ignoraba ausencias puntuales → un NULL de sales30d se
  comportaba como 0 y podia etiquetar "Gasto sin ventas" con ventas
  parcialmente desconocidas (0.4 §4.1 exige marcar subtotales parciales).
- **Menor 3**: ADR B.2-1 de 0021 declaraba un pineo muestra==madura imposible
  por diseno (D4: muestra es NULL con >= 30 dias).

Lo que aprobo: precedencia 0.4 sin por_probar, ratios desde sumas, madurez
observed_at >= D+30, ventana D-31..D-1, regresion F1 intacta (sync_metrics
default, cron, 138 tests), migraciones expansivas con GRANTs minimos y
trigger conservado, disponibilidad NULL≠0 sin cache prohibido, UX sin
bloqueos, evidencia honesta (B.1/B.3 en vivo declarados pendientes).

## Correcciones — commit `8b407cc`

1. Colapso a la observacion mas reciente por (asin, sku, metric_date):
   `DISTINCT ON ... ORDER BY observed_at DESC` en `app/fabrica_web.py` Y
   defensa en profundidad dentro de `evaluar_ads` (dict por fecha con max
   observed_at). Test de regresion con valores DISTINTOS (cost 10→12, no 22),
   demostrado fallando contra 8b4f7fe.
2. `_suma` ahora envenena a None si ALGUNA fila trae la metrica ausente;
   clicks con el mismo criterio. Test: sales parcialmente desconocidas no es
   "Gasto sin ventas".
3. ADR B.2-1 reescrito con la mitigacion real (snapshot antes/despues de
   v_margen_producto + pineo de valores exactos de muestra en rango 1-29).

Suite focal tras correcciones: 215 passed (incluye test_reports_pipeline y
test_schema). Ruff limpio.
