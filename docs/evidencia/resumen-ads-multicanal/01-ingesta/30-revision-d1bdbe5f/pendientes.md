# Gates no ejecutados

Estos gates del plan no se pudieron ejecutar en este checkout. No se
sustituyen con afirmaciones.

- **Lane 1 Amazon (regresión base vs head).** Requiere credenciales AWS y la
  fuente Amazon viva. No están en este checkout.
- **Perf de `/` (30 requests intercalados, mediana y p95).** Requiere levantar
  FastAPI con el fixture Amazon y la base completa. No se levantó aquí.
- **Lanes live del plan (10 carriles en VM con `grok-4.7-xhigh-fast` y
  capturas).** No hay VM de revisión en este entorno. Los equivalentes CLI sí
  se ejecutaron y están en este directorio.
- **Reinicio del servicio FastAPI.** Se reinició el proceso de ingesta (CLI) y
  se verificó la base en procesos nuevos; el servicio HTTP no se reinició.

El resto de los gates solicitados se ejecutó: focales, Ruff, formato,
`bash -n`, `git diff --check`, pre-commit, suite completa con
`ORBIT_TEST_DSN`, conciliación, 3 ingestas de 100,000 filas y las
reproducciones antes/después.
