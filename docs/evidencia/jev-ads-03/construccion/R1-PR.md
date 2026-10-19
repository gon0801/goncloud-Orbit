# R1-PR: la PR queda lista para merge

PR 476, ready, contra `master`. https://github.com/gon0801/goncloud-Orbit/pull/476

Mergeable CLEAN. No es draft. Este archivo es el ultimo commit del encargo. El SHA final es el de `git rev-parse HEAD` al leer el commit que lo introduce. No se mergeo.

El codigo que cubre `completa` es `ab5221890035e88675133421a34b8828541423e2`. Este commit solo mueve evidencia y dos frases de spec. `rapido` y `gate` de este SHA los dispara el push de este archivo.

## Que cambia para el dueno

Si el POST del negative ya salio y el LIST de confirmacion pierde el token (401 y el refresh de LWA cae), la fila ya no dice fallido. Queda en `intencion`, con `por_confirmar` en la respuesta de ese clic. Quien recarga ve el paso `intencion` y el motivo vacio de esa fila. La reconciliacion de siempre la cierra despues.

## Commits

De R1-PR y de esta continuacion, sobre `331af50194471188ae472ac6268bf409e48c6d48`:

- `f94a0e8730a29bd89921427f86be1ffc6c423f5e` JEV ADS 03 R.1: pruebas del post incierto
- `9230f008acb589db808b00478333c906a28d7b75` JEV ADS 03 R.1: un 5xx o un corte de red no sella fallido
- `dd7d8e84dae8c305bca7a0a1bb963e2f2f0665d2` JEV ADS 03 R.1: paquete de produccion solo codigo
- `6ff49c66cb33dd5b89af9bc0425fa0dfbd2b8666` JEV ADS 03 R.1: evidencia de construccion
- `af62c63ec7283d843cece6ad017648afe6bdd70d` JEV ADS 03 R.1: un 4xx y un error previo al POST siguen en fallido
- `8bfcc5058a776710bdd33de8a06827cdf9d14c60` JEV ADS 03 R.1: el efecto sigue importando app.ads.write
- `ab5221890035e88675133421a34b8828541423e2` JEV ADS 03 R.1: un refresh caido en el readback deja la intencion abierta
- el commit de esta evidencia

## Baterias

Postgres del loop en el puerto 5436 respondio. No se arranco ni se apago.

Rojo, antes del arreglo, `tests/test_bloqueo_dueno_efecto.py::test_r1_auth_en_readback_deja_la_intencion_abierta`. Ultima linea: `1 failed in 0.36s`. El desacuerdo: `assert (200, 'fallido', 'sin_respuesta') == (200, 'intencion', 'por_confirmar')`.

Verde, el mismo archivo, despues del arreglo. Ultima linea: `28 passed in 3.53s`.

`completa` sobre `ab5221890035e88675133421a34b8828541423e2`, run 39194966019, conclusion success. Ultima linea de Python: `5393 passed, 1 warning in 903.00s (0:15:03)`. El paso de pre-commit de ese job paso. `pesada` en el mismo run: `20 passed in 0.90s`.

`rapido` y `gate` en ese mismo SHA y run: success. El push de `ab522189` (run 39194944420) tambien cerro success. Ahi `completa` y `pesada` salen skipped a proposito. El `completa` que cuenta es el dispatch.

La corrida anterior, 39193351005, tambien cerro success, pero sobre `8bfcc5058a776710bdd33de8a06827cdf9d14c60`, antes del arreglo del readback.

## Mutante

Uno, sobre la prueba nueva. Se quito `AdsAuthError` del `except` de `_lista_o`, dejo solo `AdsApiError`. La prueba fallo por el desacuerdo predicho y se revirtio. No quedo en el arbol.

| Mutante | Prueba | Resultado |
| --- | --- | --- |
| `_lista_o` atrapa solo `AdsApiError` | `test_r1_auth_en_readback_deja_la_intencion_abierta` | `assert (200, 'fallido', 'sin_respuesta') == (200, 'intencion', 'por_confirmar')`. Ultima linea: `1 failed in 0.28s` |

## CI en el SHA de codigo

SHA `ab5221890035e88675133421a34b8828541423e2`.

- `completa`: success. Run 39194966019.
- `gate`: success. Mismo run.
- `rapido`: success. Mismo run, y tambien el run 39194944420.

## Triage

Revision automatica con `ai-review:sha=ab5221890035e88675133421a34b8828541423e2`. Veredicto del bot: 1 Medium y 5 Low abiertos, 6 resueltos. El comentario de esta sesion esta en la PR.

- F10 real. Arreglado en `ab522189`. El bot lo cerro.
- F8 real. La frase de R1-cod ya no dice que toda excepcion del POST deja la intencion. El bot lo cerro.
- F5 real en el inciso 3 de las pruebas del spec, no en el paso 8. Ese inciso decia que un transporte que revienta deja `intencion` y despues `fallido`. Ahora dice que ese clic deja solo `intencion` / `por_confirmar`, y que la reconciliacion posterior adopta o sella `fallido` / `sin_resultado`. Va en este commit.
- F12 real como alcance, no como cifra mal copiada. R1-cod ahora dice que cubre hasta `6ff49c66` y que `25 passed` es de esa sesion. El head esta aqui.
- F4 falso. El spec, lineas 240-242, dice que no hay paso nuevo y que `por_confirmar` viaja en la respuesta. `test_r1_e_post_ok_sin_readback_queda_intencion` lo exige.
- F7 falso. Dos corridas del mismo comando. R1-cod trae `7 failed, 1 passed, 17 deselected in 3.19s`. El cuerpo de la PR trae la otra: `in 1.53s`. La duracion no es un contrato.
- F9 y F11 falsos. Una pagina sin `keywordId` no llega a `_readback`, ni a `_cierra_huerfano`, ni al LIST previo. `_elemento_list_valido` la rechaza y `_valida_pagina_list` devuelve `([], False)`. Comando, sin red:

```
PYTHONPATH=. .venv/bin/python -c 'from app.apply_harvest import _elemento_list_valido, _valida_pagina_list; item={"adGroupId":"g","keywordText":"t","matchType":"NEGATIVE_EXACT","state":"ENABLED"}; print(_elemento_list_valido(item,"negativeKeywords")); print(_valida_pagina_list({"negativeKeywords":[item]},"negativeKeywords"))'
```

Imprime `False` y `([], False)`. Con estado distinto de `ok`, el LIST previo sella `fallido` sin POST y la reconciliacion deja `pendiente`. No hay `KeyError` ni `"None"` como id.

CodeRabbit: los 9 hilos del review sobre `6ff49c66` estan resueltos. En `ab522189` el comentario dice `Review skipped` (incremental desactivado). No hay hilo abierto.

## Pendiente

- Merge: lo hace el operador. Este encargo no mergea.
- `rapido` y `gate` de este commit de evidencia quedan en el push. `completa` no se relanza: el unico cambio respecto de `ab522189` es evidencia y dos frases. `completa` verde es el de `ab5221890035e88675133421a34b8828541423e2`.
- La fila R.1 del plan sigue en `cc:TODO`. El cuerpo de la PR lo deja asi a proposito.
