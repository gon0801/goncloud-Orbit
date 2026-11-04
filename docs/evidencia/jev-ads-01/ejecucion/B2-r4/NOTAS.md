# E/B2-r4 — correccion de la revision automatica post-push (F1/F2/F3)

Fecha UTC: 2026-10-04T01:20:57Z (ACK) a cierre. Rama `docs/jev-ads-b2`,
partiendo limpia de `a495f76268f5204e511c92b013a613bcd36a49af`
(VEREDICTO-B2-r3 APROBADO; PR #391 abierto, sin merge hasta corregir esto).
Un solo escritor; SIN push (lo hace Claw tras VEREDICTO: APROBADO).

## SHA

| Commit | Contenido |
| --- | --- |
| `150fa77a40cb8202fb52c7f00f3510cdf2338b47` | B2-r4: F1 cardinality en listings, F2 iterables materializados, F3 relacion fuera del contrato levanta ValueError |

HEAD final de CODIGO (40): `150fa77a40cb8202fb52c7f00f3510cdf2338b47`
(commits posteriores, si los hay, SOLO evidencia documental).

## F1 (Medium, 0049:71): ficha con listings vacio pasaba CHECK y trigger

- CAUSA: `array_length('{}',1)` es NULL y `NULL >= 1` no es FALSE; el
  trigger caia en el mismo hueco (`count <> NULL` es NULL). Una ficha que
  no cubre nada quedaba insertada en tabla append-only.
- ARREGLO (minimo, 2 lineas + comentarios): CHECK
  `cardinality(listings) >= 1`; trigger con
  `COALESCE(array_length(NEW.listings, 1), 0)`. Sin tocar
  `0049_reversa_jev_ads.sql` (estructura intacta) ni nada mas de 0049.
- REGRESION: `test_listings_vacios_no_registran_ficha` (inserta con
  `listings=()` → CheckViolation y cero filas).
- MUTANTE demostrado: CHECK revertido a `array_length` → `1 failed`;
  restaurado → verde.

## F2 (Medium, jev_catalogo): iterable de un solo uso desalineaba hash y fila

- CAUSA: `desconocidos` se consumia tres veces (hash_ficha, INSERT,
  FichaVersion); con un generador, hash guardaba el contenido completo y
  la fila `'{}'`: hash y contenido dejaban de corresponder. Riesgo igual
  para `hechos` y `listings`.
- ARREGLO: `registrar_ficha` materializa UNA vez al inicio
  (`tuple(listings)`, `tuple(hechos)`,
  `desconocidos = sorted(desconocidos)`) y reutiliza.
- REGRESION: `test_desconocidos_generador_no_corrompe_hash_ni_ficha`
  (registra con `iter(...)` en listings/hechos/desconocidos; el sha
  guardado debe ser el del contenido COMPLETO y la fila debe traer los
  dos desconocidos).
- MUTANTE demostrado: materializacion a `pass` → `1 failed`;
  restaurado → verde.

## F3 (Low, jev_ads:343): relacion no reconocida pasaba como juicio valido

- CAUSA: `componer` solo cubria satisface/informacion_insuficiente; otro
  valor contaba en `juzgados` sin motivo y con censo exhaustivo producia
  `NingunoCompatible` falso (demostrado: DID NOT RAISE).
- ARREGLO: `elif par.relacion != "no_satisface": raise ValueError(...)` —
  `no_satisface` es el unico caso sin efecto propio; cualquier otro valor
  fuera del contrato es error estructural del llamador.
- REGRESION: `test_relacion_no_reconocida_es_error_estructural`
  (Juicio con `relacion="desconocida"` → ValueError).
- MUTANTE demostrado: raise → `pass` → `1 failed`; restaurado → verde.
- C901 de `componer` (23 > 22) resuelto simplificando de verdad: la
  clasificacion de pares vive en `_absorber_pares` (funcion pura); sin
  noqa ni allowlist.

## Rojo -> verde (TDD)

- ROJO (`jev_b2_r4_rojo.txt`): 3 failed, cada uno por su causa —
  F1 `DID NOT RAISE CheckViolation` (ficha vacia insertada), F2 hash
  guardado con contenido desalineado (`frozenset()` contra
  `{"material","peso"}`), F3 `DID NOT RAISE ValueError`.
- VERDE (`jev_b2_r4_verde.txt`): 43 passed focalizadas
  (22 jev_ads + 21 catalogo).
- BATERIA completa una vez sobre el commit de codigo:
  `3,790 passed, 1 warning in 228.57s` (`jev_b2_r4_bateria.txt`).
- ruff `All checks passed!`; `pre-commit run --all-files` 9 hooks Passed;
  commits con hooks en verde, sin `--no-verify`.

## Comandos

```
uv run --frozen python -m pytest tests/test_jev_ads.py::test_relacion_no_reconocida_es_error_estructural tests/test_jev_catalogo.py::test_listings_vacios_no_registran_ficha tests/test_jev_catalogo.py::test_desconocidos_generador_no_corrompe_hash_ni_fila -q   # 3 failed (rojo)
uv run --frozen python -m pytest tests/test_jev_ads.py tests/test_jev_catalogo.py -q   # 43 passed (verde)
uv run --frozen python -m pytest -q                                                    # 3,790 passed (bateria)
uv run ruff check . && pre-commit run --all-files                                      # limpio
git commit sin --no-verify
```

Integracion DB: Postgres 16 local, DBs desechables `orbit_jev01_*`;
NUNCA prod.

## Limites respetados

- Archivos tocados: `app/jev_ads.py`, `app/jev_catalogo.py`,
  `migrations/0049_jev_ads.sql` (solo F1, minimo), tests de ambas areas y
  esta evidencia. `0049_reversa` INTACTA; `cycle.py`/`apply_cola.py`/
  `apply_harvest.py` intactos.
- Cero llamadas a TypeSafe/Jev; cero claves; sin secretos.
- Sin push ni PR: commit local `150fa77`; arbol limpio tras la evidencia.
  El push lo hace Claw tras VEREDICTO: APROBADO.

## Skills/agentes/modelos

- Sesion principal opencode: `zai-coding-plan/glm-5.3-flash`, un solo
  escritor, dueno del diff.
- Skills activas de la sesion: poteto-mode (playbook Feature, skips
  razonados), unslop, tdd, typesafe-ai + pstack-opencode. B2-r4 es
  arreglo pautado de tres hallazgos: sin delegacion (contrato de
  un-escritor) y sin paneles (no hay diseño contestado).
