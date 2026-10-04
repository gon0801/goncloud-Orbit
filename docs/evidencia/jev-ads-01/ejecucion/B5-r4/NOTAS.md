# B5-r4: prueba que fija el punto 3 de la guarda B5a (cuarta vuelta del delta 2.R)

Fecha: 2026-10-04. Base de la ronda: 1deb7d46ac3e7cbba0ffc8440b1680070cae332e
(B5-r3). VEREDICTO-B5-r3: CAMBIOS con UN solo bloqueante B1 (revisor Claude):
el punto 3 del rediseno ("permitidos rige tambien los anidados") estaba
implementado pero SIN prueba que lo fije: con la parte 2 revertida a solo
lista negra, las 26 pruebas seguian verdes y un `import sqlite3` anidado en
`componer` volvia a pasar sin que nada truene.

## Tabla de la correccion (TDD; solo tests/test_jev_ads.py, +10 lineas)

| Paso | Que | Resultado |
|---|---|---|
| Repro del bloqueante | Mutante literal del VEREDICTO (parte 2 -> `any(candidato == q or ...)` contra _PROHIBIDOS_MODULOS_PUROS) contra el arbol base | `26 passed in 0.12s`: el mutante sobrevive, B1 vivo. mutante-26passed.txt |
| Prueba nueva | test_guarda_pureza_caza_anidado_fuera_de_permitidos: afirma el hallazgo EXACTO de un anidado de stdlib que no es IO de lista negra ni esta concedido: `_fugas_pureza("def componer():\n    import sqlite3\n    return sqlite3\n") == [("componer", "sqlite3")]` | `1 passed` con la guarda real |
| ROJO | Mismo mutante del veredicto con la prueba nueva en el arbol | `1 failed, 26 passed`: la UNICA roja es la nueva (linea 484); la prueba discrimina. rojo-mutante.txt |
| Restauracion | La copia de respaldo (hecha antes de mutar) devuelve la guarda real; `git diff --stat` = solo las 10 lineas de la prueba | arbol limpio, sin rastro del mutante |
| VERDE | Bateria focalizada completa + Ruff + pre-commit | `27 passed`, Ruff ok, 9 hooks Passed. bateria-focalizada.txt, precommit.txt |

Por que ese hallazgo y no otro: `sqlite3` NO esta en _PROHIBIDOS_MODULOS_PUROS
(lista negra de red/DB del proyecto) ni en _PERMITIDOS_PUROS (lista blanca
pura), y `return sqlite3` no agrega hallazgo de nombres (sqlite3 no esta en
_PROHIBIDOS_NOMBRES_PUROS): el resultado es exactamente la tupla
("componer", "sqlite3"). Con el mutante (solo lista negra) la guarda
devuelve [] y la igualdad exacta se vuelve roja: la prueba muere justo con
la regresion que el veredicto demostro.

## Alcance

- NO rediseno: cero cambios de logica en la guarda; solo la prueba nueva.
- app/*, tools/* y migrations/* INTACTOS (0049 sigue ocupada por B2).
- Sin auto-revision delta esta vuelta, por alcance minimo (una prueba); lo
  comprobara el VEREDICTO final con prueba + mutante.
- Los otros 6 arreglos de r2 y el rediseno r3 NO se tocaron; B6 sigue
  REFUTADO, no tocado (residual para el PR).

## Comandos exactos

- Repro/ROJO (mutante del veredicto): el one-liner literal del VEREDICTO-B5-r3
  (python3 -c "import pathlib;..._PROHIBIDOS_MODULOS_PUROS...") seguido de
  `uv run --frozen python -m pytest -q tests/test_jev_ads.py | tail -1`;
  para el ROJO se uso copia de respaldo en scratch en lugar de
  `git checkout` para no perder la prueba nueva (el mutante es el reemplazo
  del bloque `if _violacion_import(candidato, _PERMITIDOS_PUROS):` de la
  parte 2; ver comandos.txt).
- VERDE: `uv run --frozen python -m pytest -q tests/test_jev_ads.py`;
  `uv run ruff check tests/test_jev_ads.py`;
  `pre-commit run --all-files`.
- Delta: `git diff 1deb7d46ac3e7cbba0ffc8440b1680070cae332e -- tests/test_jev_ads.py`

## Evidencia

mutante-26passed.txt (bloqueante vivo), rojo-mutante.txt (la prueba nueva
muere con el mutante), bateria-focalizada.txt (27 passed + Ruff),
precommit.txt (9 hooks), delta-codigo.diff (+10 lineas), comandos.txt.
