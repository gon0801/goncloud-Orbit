# 3.2 — Acta de decisión sobre la asesoría V1

**Fecha:** 2026-10-04. **Decide:** David (dueño).

## Decisión

**Seguir y ampliar.** La asesoría de Jev se conserva en producción como
aviso (lectura en `/cortes` y en la fábrica, CLI manual) y se abre un plan
nuevo, `plans/jev-ads-02.md`, para que pase de avisar a proponer y rutear.

## Con qué se decidió

- Reporte: `docs/evidencia/jev-ads-01/ejecucion/3.1/REPORTE.md`.
- Dataset versionado: `docs/evidencia/jev-ads-01/ejecucion/3.1/dataset-piloto.csv`
  (sha256 `25efa2d1fce011be9a69d7814a7d00d371e73d9086f3670135a59c3358e6d950`).
- Código desplegado: `25cebe7` (deploy `20261004-2253`, checklist en
  `ejecucion/2.3/`). Modelo `jev-1.13.0`.
- Método: 6 lotes manuales con fichas aprobadas por el dueño; 648 pares; cada
  respuesta de Jev comparada con la etiqueta humana del par.

## Por qué

- Con la ficha completa Jev acertó en todos los pares de los lotes de
  comprobación (4 y 6) y en las 135 respuestas del lote de negativos reales.
- Los desacuerdos de la primera pasada (cerca del 25%) se corrigieron con
  datos y reglas del dueño en la ficha, sin tocar el sistema.
- La asesoría no actúa sola: un error suyo hoy no cambia ninguna acción de Ads.

## Límites de la muestra

Un solo tipo de producto, dos mercados, 31 búsquedas. Sin destino de harvest,
sin plan de fábrica y sin medir el orden de opciones. Las etiquetas de los
lotes 2 a 6 las propuso el lead y las confirmó el dueño. Detalle en el reporte.

## Qué se amplía y con qué condición

Cualquier efecto sobre propuestas o acciones de Ads va en `jev-ads-02`, con
su interruptor, su reversa y sus criterios medidos. Hasta que ese plan lo
cambie, sigue vigente el contrato de `jev-ads-01`: el asesor no se llama
desde el ciclo ni desde los apply.
