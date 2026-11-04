# S.2: revision adversaria (panel GLM por `opencode run`)

Diversidad: familias Muse y GLM. Lado Muse: revision inline propia (este
runtime no concede herramientas de subagentes: sin `subagent_spawn` ni
`workflow`, no hubo hijos Muse ni comment-sicko; deslop/no-comments se
aplicaron a mano sobre el diff, sin hallazgos). Lado GLM, 3 revisores en
paralelo con el mismo prompt solo-lectura (prompt en
`/tmp/s2-panel-prompt.txt`, salidas completas en `/tmp/s2-panel-glm*.txt`):

- `zai-coding-plan/glm-5.3` (rc=0): sin bloqueantes; 4 no bloqueantes.
- `zai-coding-plan/glm-5.2` (rc=0): sin bloqueantes; 5 no bloqueantes.
- `zai-coding-plan/glm-4.7` (rc=0): sin bloqueantes; 3 no bloqueantes.

Sintesis del lead (consenso = 2+ modelos):

- Aplicados: `bool` como total declarado -> `type(d) is not int` (3/3);
  docstring de `jev_libro` menciona `siguiente_ordinal` (1/3);
  la variante pura pincha la clausula de fichas de `_mismo_origen` (1/3);
  docstring de `_necesita_a_jev` ya no cita "pasos 5 a 7" (2/3).
- Desestimados con razon: `assert grupo is not None` (2/3) se queda: hay
  precedente en `app/` y ningun run path usa `python -O` (verificado en
  Dockerfile, compose y DEPLOY.md); el tramo `desempate` cubre el paso 8
  (2/3) porque en planificacion no se distingue (condicion economica).
- Para S.4 (no es cambio aqui): `planear` salta claves sin entrada en
  `economia` (3/3); `_economia` debe traer Economia (con `aqui=None` si
  toca) para toda propuesta y toda clave vigente, o la unidad se pierde
  sin rastro. Fijarlo en la prueba e2e de S.4.
