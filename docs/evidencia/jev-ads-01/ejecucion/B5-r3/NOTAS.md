# B5-r3: rediseno de la guarda de pureza B5a (tercera vuelta del delta 2.R)

Fecha: 2026-10-04. Base de la ronda: 8372f5aaf9e73b3c791396de8b317c560618a90e
(B5-r2, PARADA declarada). El bloqueante de grok reproducido en
B5-r2/delta-bloqueante-repro.txt: la guarda de pureza endurecida de r2 sigue
sin discriminar (a) `from . import db` anidado (relativo sin resolver) ni
(b) `try: import boto3` a nivel de modulo (un Try no es nodo Import y escapa
a la parte 1), y no aplica `permitidos` a los anidados.

## Tabla de la correccion (TDD rojo-verde, solo tests/*)

Archivo tocado: tests/test_jev_ads.py (la guarda vive aqui; app/*, tools/* y
migrations/* intocados, 0049 ocupada por B2).

| Paso | Que | Resultado |
|---|---|---|
| Repro | Logica VIEJA (literal de 8372f5aa) en script de scratch contra los 3 mutantes | (a) PASA, (b) PASA (no discrimina); (c) PASA (control). repro-guarda-vieja.txt |
| ROJO | Pruebas sembradas nuevas contra la logica vieja extraida al helper `_fugas_pureza` | mutante (a) FAILED, (b) FAILED; control (c) passed y archivo real passed. rojo-sembradas.txt |
| VERDE | Rediseno: `_candidatos_import` resuelve relativos contra `app.`, la parte 1 recorre el cuerpo de try/if de alcance de modulo (sin bajar a def/class), y `_violacion_import` aplica denylist O (stdlib puro O permitidos) a top-level y anidados por igual | 4 passed (las 3 sembradas + archivo real). verde-sembradas.txt |

Salida ROJO literal (uv run --frozen python -m pytest -q sobre las 4 pruebas):

```
tests/test_jev_ads.py:434: AssertionError  (mutante a: assert False, sin "app.db")
tests/test_jev_ads.py:448: AssertionError  (mutante b: assert False, sin "boto3")
2 failed, 2 passed in 0.14s
```

## Hallazgos de esta correccion

- La extraccion al helper fue neutra (mismo criterio y misma lista): el test
  del archivo real siguio en verde antes del rediseno (la ROJO solo encendio
  las semillas nuevas, nunca el archivo real).
- PERO la primera version del rediseno cometio una regresion que detecto el
  delta review (kimi, ronda 1 de esta vuelta): `_violacion_import` agrego el
  escape `sys.stdlib_module_names`, que convirtio la lista blanca de modulo
  en "cualquier stdlib" (`import sqlite3`/`subprocess`/`os` pasaban en
  verde; la version de 8372f5aa los rechazaba). Corregido en ronda 2: el
  escape quedo fuera y `permitidos` volvio a ser lista blanca estricta; la
  prueba nueva test_guarda_pureza_permitidos_sigue_siendo_lista_blanca se
  demostro en ROJO contra la version ensanchada (devolvia []) y VERDE tras
  el arreglo.
- Los imports perezosos reales de `app/jev_ads.py` viven todos dentro de
  `AsesorAds` (lineas 437-461, 531 y 749-751, entre las lineas 413 y 933),
  asi que aplicar `permitidos` a los anidados de los DEMAS nodos no toca el
  archivo real (sigue en verde, verificado tambien por kimi).
- La resolucion de relativos usa la misma semantica que la guarda B3 de
  r2 (test_jev_cli.py: `(("app." if level else "") + module).rstrip(".")`),
  un solo criterio en el repo.
- (c) control positivo: un anidado de `permitidos` a cualquier profundidad
  sigue pasando; la guarda no se volvio paranoica.

## Delta review

Ronda 1, revisor: KIMI (binario kimi, exit 0, diff de 30489 caracteres,
sin truncamiento; reporte completo en delta-reporte.txt). Comando (nota: el
del encargo combinaba -Base y -Desde y el script los rechaza por
excluyentes; se corrio con la intencion exacta del delta, solo los arreglos
desde 8372f5aa, igual que se resolvio en B5-r2):

```
export PATH=/opt/homebrew/bin:/Users/dn/.local/bin:/Users/dn/bin:$PATH
pwsh -NoProfile -File /Users/dn/quality-kit/cross-review.ps1 -Con kimi \
  -Excluir glm -Desde 8372f5aaf9e73b3c791396de8b317c560618a90e \
  -RepoPath /Users/dn/dev/wt/jev-ads-worker
```

Hallazgos kimi: 1 BLOQUEANTE (el escape stdlib de arriba, con repro:
`uv run --frozen python -c "import sys; sys.path.insert(0,'tests'); from
test_jev_ads import _fugas_pureza; assert _fugas_pureza('import
sqlite3\nimport subprocess\nimport os\n') != [], 'guarda ciega ante DB/IO
stdlib en top-level'"` corria FALLANDO contra la version ensanchada) y 2 NO
BLOQUEANTES anotados como residual para el PR: (i) un import en try/if
top-level se reporta dos veces con rotulos distintos ("top-level" en parte
1 y "<modulo>" en parte 2; solo ensucia el mensaje, no cambia el veredicto)
y (ii) `_candidatos_import` fija "app." sin importar el nivel relativo
(level>1 resolveria mal, caso invalido en el unico archivo que hoy se
parsea).

Ronda 2, revisor: (se completa al correr cross-review sobre SOLO los
arreglos desde el SHA que kimi vio: 6070bbbb) — reporte en
delta-reporte-r2.txt.

## Comandos exactos

- Repro vieja: `uv run --frozen python <scratch>/repro-b5-r3-vieja.py`
  (script conservado fuera del repo; su salida es repro-guarda-vieja.txt).
- ROJO/VERDE: `uv run --frozen python -m pytest -q
  "tests/test_jev_ads.py::test_modulo_puro_sin_red_ni_db_en_top_level"
  "tests/test_jev_ads.py::test_guarda_pureza_caza_relativo_anidado"
  "tests/test_jev_ads.py::test_guarda_pureza_caza_import_en_try_de_modulo"
  "tests/test_jev_ads.py::test_guarda_pureza_deja_pasar_permitido_anidado"`
- Bateria focalizada: `uv run --frozen python -m pytest -q tests/test_jev_ads.py`
- Lint: `uv run ruff check tests/test_jev_ads.py && uv run ruff format tests/test_jev_ads.py`
- Candados: `pre-commit run --all-files`
- Delta: `git diff 8372f5aaf9e73b3c791396de8b317c560618a90e -- tests/test_jev_ads.py > delta-codigo.diff`

## Mutantes registrados

| Mutante | Contra guarda vieja | Contra guarda nueva |
|---|---|---|
| (a) `from . import db` anidado en componer() | PASA (no discrimina) -> prueba sembrada ROJO | RECHAZA (`app.db` resuelto), VERDE |
| (b) `try: import boto3` a nivel de modulo | PASA (no discrimina) -> prueba sembrada ROJO | RECHAZA (Try es alcance de modulo; boto3 fuera de permitidos), VERDE |
| (c) `from decimal import Decimal` + `import uuid` anidados (control) | PASA | PASA (no se volvio paranoica), VERDE |
| (d) ronda 2 (kimi): `import sqlite3`/`subprocess`/`os` a nivel de modulo | ROJO en 8372f5aa (lista blanca); PASA en la primera version del rediseno (escape stdlib) | RECHAZA de nuevo tras quitar el escape (permitidos lista blanca estricta), VERDE |
