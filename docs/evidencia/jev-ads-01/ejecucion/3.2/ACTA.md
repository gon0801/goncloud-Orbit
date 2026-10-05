# 3.2 — Acta de decisión sobre la asesoría V1

**Fecha:** 2026-10-04. **Decide:** David (dueño).

## Decisión

**Seguir y ampliar.** La asesoría de Jev se conserva en producción como
aviso (lectura en `/cortes` y en la fábrica, CLI manual) y se abre un plan
nuevo, `plans/jev-ads-02.md`, para que pase de avisar a proponer y rutear.

## Con qué se decidió

- Reporte: `docs/evidencia/jev-ads-01/ejecucion/3.1/REPORTE.md`.
- Dataset versionado: `docs/evidencia/jev-ads-01/ejecucion/3.1/dataset-piloto.csv`
  (sha256 `210e5ac9cecde696a17a7290fe136156146a9dfc1e1f2894e6e95a2442712ecb`).
- Código desplegado: `25cebe7` (deploy `20261004-2253`, checklist en
  `ejecucion/2.3/`). Modelo `jev-1.13.0`.
- Método: 6 lotes manuales con fichas aprobadas por el dueño; 648 pares; cada
  respuesta de Jev comparada con la etiqueta humana del par.

## Por qué

- Con la ficha completa Jev acertó en todos los pares de los lotes de
  comprobación (4 y 6) y en las 135 respuestas del lote de negativos reales.
- En la primera pasada Jev no acertó cerca del 25% de los pares, contando
  abstenciones y fallos (desacuerdos solos: 13% en US y 19.5% en MX). Se
  corrigió con datos y reglas del dueño en la ficha, sin tocar el sistema.
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
