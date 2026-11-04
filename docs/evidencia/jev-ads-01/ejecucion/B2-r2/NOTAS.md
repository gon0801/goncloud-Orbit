# E/B2-r2 — correccion VEREDICTO-B2-r1 (2 bloqueantes + 1 arreglo)

Fecha UTC: 2026-10-04T00:27:29Z (ACK) a cierre. Rama `docs/jev-ads-b2`,
partiendo limpia de `7f923652b45f3674ba539346014cff1309f12bd0` (ahead 4).
Un solo escritor; SIN push (lo hace Claw tras VEREDICTO: APROBADO).

## SHA

| Commit | Contenido |
| --- | --- |
| `f3e1b3b678b44fd59c00d80e69ddc612b02eebd2` | B2-r2 completo: B1 (estado del anuncio), B2 (captured_at vs clock_timestamp), ASIN-like en texto, repros integradas como regresion |

HEAD final (40): `f3e1b3b678b44fd59c00d80e69ddc612b02eebd2`

## Rojo -> verde (TDD)

- ROJO: las dos repros del veredicto copiadas a `tests/` y corridas contra
  el HEAD sin arreglo (`jev_b2_r2_rojo_repros.txt`):
  `2 failed` con las salidas observadas por el veredicto
  (`campos de MiembroCenso` sin estado, `componer -> HayCompatible(...)`
  por un ARCHIVED; `psycopg.errors.CheckViolation: jev_revision:
  decided_at/captured_at posteriores a created_at`).
- VERDE: `39 passed` focalizadas (`jev_b2_r2_verde_focalizadas.txt`);
  bateria completa `3,786 passed, 1 warning in 227.50s`
  (`jev_b2_r2_bateria.txt`). Las repros se integraron con nombre
  definitivo y los archivos `test_repro_*` sueltos fueron BORRADOS.

## B1: el censo ahora conserva el estado por anuncio y componer excluye lo no anunciado

- `app/jev_ads.py`: nuevo `EstadoAnuncio(status, synced_at)`; `MiembroCenso`
  gana `estados: tuple[EstadoAnuncio, ...]` paralelo a `anuncio_ids`
  (default `()` = sin dato). Constante `ESTADOS_ACTIVOS = {ENABLED, PAUSED}`.
  Motivos nuevos: `no_anunciado` y `missing_state`.
- Regla probada en `componer`: miembro con TODOS los estados conocidos y
  ninguno activo (ARCHIVED) NO cuenta como anunciado: sale del universo
  (motivo `no_anunciado`), su `satisface` jamas produce HayCompatible y no
  exige juicio. Estado AUSENTE no excluye: se conserva como incidencia
  `missing_state` que impide el negativo universal pero no el compatible
  de otro miembro. `estados` no paralelo a `anuncio_ids` -> ValueError
  estructural. La logica vive en `_universo_anunciado` (ruff C901
  resuelto simplificando, no con noqa).
- `app/jev_catalogo.py`: `censo_grupo` hace LEFT JOIN a `ad_entity_state`
  y conserva `status`/`synced_at` por anuncio (None si falta la fila);
  NINGUN anuncio se descarta por estado (la exclusion es de composicion,
  no del censo).
- Regresion integrada:
  - `tests/test_jev_ads.py`: archived no acredita compatible
    (Indeterminado{no_anunciado}), todos-ARCHIVED no da NingunoCompatible,
    estado ausente bloquea negativo universal y no el compatible, mixto
    ARCHIVED+ENABLED cuenta como anunciado (NingunoCompatible), estados no
    paralelos es error estructural.
  - `tests/test_jev_catalogo.py::test_censo_lleva_estado_y_archived_no_acredita_compatible`:
    escenario exacto de la repro (X ARCHIVED + Y ENABLED, censo real con
    LEFT JOIN, juicios satisface/no_satisface) →
    `Indeterminado({no_anunciado, universo_desconocido})`, JAMAS
    HayCompatible. Ademas aserta estados por anuncio del censo
    (["ARCHIVED"] con synced_at, ["ENABLED"]).
  - Mutante demostrado: el codigo anterior (HEAD 7f92365, sin campo
    estado) falla las repros = rojo registrado arriba.

## B2: captured_at dentro de la transaccion se acepta; el futuro se rechaza

- `migrations/0049_jev_ads.sql`: `jev_revision_tiempos` compara ahora contra
  `clock_timestamp()` (insercion REAL; el DEFAULT now() de created_at fija
  el inicio de la transaccion y la captura del censo dentro de la misma tx
  es posterior a ese inicio y anterior al INSERT) y ademas rechaza
  `created_at`/`captured_at`/`decided_at` futuros (sin puerta a fechas
  futuras). Comentario ADR actualizado en la migracion. La reversa 0049 no
  cambia (borra la funcion por nombre; estructura intacta).
- Regresion integrada:
  - `tests/test_jev_catalogo.py::test_captura_dentro_de_la_transaccion_se_acepta_y_futura_se_rechaza`:
    tx abierta + sleep + captured_at -> INSERT ok; captured_at +1 dia ->
    CheckViolation.
  - `test_reglas_temporales_y_cobertura_en_triggers` actualizado: pasado
    creado+capturado (12:00/13:00 del dia) se acepta; futuro se rechaza.
  - Mutante demostrado: la migracion anterior (`> NEW.created_at`) falla la
    repro = rojo registrado arriba.

## Arreglo de una linea: ASIN-like dentro de texto

- `tests/test_jev_ads.py::test_asin_like_no_entra_al_clasificador` gana
  `assert not es_asin_like("kit b0abcdefgh rojo")` (y la equivalencia con
  `fabrica_plan.PATRON_ASIN` incluye la muestra).
- Mutante demostrado en vivo: `fullmatch` -> `search` con sed, la suite de
  jev_ads cae a `2 failed` (el caso nuevo y el de equivalencia); revertido,
  `1 passed` en el test y 20/20 verde.

## Comandos

```
PYTHONPATH=.:tests uv run --frozen python -m pytest -q -s tests/test_repro_b2_estado_censo.py tests/test_repro_b2_captured_at_tx.py   # 2 failed (rojo)
uv run --frozen python -m pytest tests/test_jev_ads.py tests/test_jev_catalogo.py -q   # 39 passed (verde)
uv run --frozen python -m pytest -q                                                    # 3,786 passed (bateria final)
uv run ruff check .          # All checks passed! (C901 resuelto simplificando)
pre-commit run --all-files   # 9 hooks Passed
git commit sin --no-verify   # hooks en verde
```

Integracion DB: Postgres 16 local 127.0.0.1:5432, DBs desechables
`orbit_jev01_*` borradas en `finally`. NUNCA prod.

## Limites respetados

- Cero llamadas a TypeSafe/Jev; cero claves; sin secretos en codigo,
  pruebas ni evidencia.
- El asesor no se llama desde `cycle.py`, `apply_cola.py` ni
  `apply_harvest.py` (diff limitado a: `app/jev_ads.py`,
  `app/jev_catalogo.py`, `migrations/0049_jev_ads.sql`,
  `tests/test_jev_ads.py`, `tests/test_jev_catalogo.py`, esta evidencia).
- Sin push ni PR: commit local `f3e1b3b`; arbol limpio.
- Archivos tocados = solo los permitidos por el encargo.

## Nota de diseño

La exclusion de ARCHIVED es regla de COMPOSICION (interpreta), no filtro
del censo: el censo conserva todo con su estado (spec: "Estados ausentes
o inciertos no se descartan"; el DoD exige el LEFT JOIN y conservar
ausentes). El synced_at se conserva por anuncio sin regla de frescura en
1.x: cualquier umbral de obsolescencia seria una decision nueva con su
propia prueba.
