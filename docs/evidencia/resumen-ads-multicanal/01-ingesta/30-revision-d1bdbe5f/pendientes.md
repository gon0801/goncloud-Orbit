# Gates no ejecutados

Estos gates del plan no se pudieron ejecutar en este checkout. No se
sustituyen con afirmaciones.

- **Lane 1 Amazon (regresión base vs head).** Requiere credenciales AWS y la
  fuente Amazon viva. No están en este checkout.
- El perf de `/` y el reinicio de FastAPI se completaron en la ronda
  `78888f3e` (`30-revision-78888f3e/ui-perf-http.txt`).
- **Lanes live del plan (10 carriles en VM con `grok-4.7-xhigh-fast` y
  capturas).** No hay VM de revisión en este entorno. Los equivalentes CLI sí
  se ejecutaron y están en este directorio.


El resto de los gates solicitados se ejecutó: focales, Ruff, formato,
`bash -n`, `git diff --check`, pre-commit, suite completa con
`ORBIT_TEST_DSN`, conciliación, 3 ingestas de 100,000 filas y las
reproducciones antes/después.
