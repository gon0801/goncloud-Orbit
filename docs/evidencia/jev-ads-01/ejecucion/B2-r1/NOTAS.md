# E/B2-r1 — filas 1.1 y 1.2 del plan JEV ADS 01 (nucleo)

Fecha UTC: 2026-10-03T23:27Z (ACK) a cierre. Rama `docs/jev-ads-b2`, base
`afc1f3b68715c5bf05bdf939eefc9a6683adc0cd` (origin/master con B1 #390
squash mergeado). Un solo escritor; sin push (lo hace Claw tras el
VEREDICTO: APROBADO).

## Shas

| Commit | Contenido |
| --- | --- |
| `be961ec` | 1.1: tipos puros y `componer` en `app/jev_ads.py` + `tests/test_jev_ads.py` |
| `6dd9763` | 1.2: migracion `0049_jev_ads.sql` + reversa, `app/jev_catalogo.py`, `tools/jev_fichas.py`, `tests/test_jev_catalogo.py`; ademas `es_asin_like` alineado a `fabrica_plan.PATRON_ASIN` |
| `b06a723` | conteo de tablas BASE 59 -> 63 en `verify/Launch.md` y `verify/Doctor.md` (candado `test_schema_docs` roto por las cuatro tablas de 0049) |

## Modelo fijado por el plan: verificado contra la documentacion viva

`jev-1.13.0` NO queda invalidado: la pagina
https://docs.typesafe.ai/models.md (leida 2026-10-03) lo lista como modelo
vigente ("Current models", alias `jev-latest` apunta a el), con contexto
64k (32k state+pregunta mas larga) e input de texto. La fila 1.1/1.2 no
llama a TypeSafe; el dato queda registrado para 1.3. Indice y API tambien
consultados (`llms.txt`); el contrato Choice es de 1.3.

## TDD 1.1 — `app/jev_ads.py` (puro: sin red ni DB)

- ROJO: `tests/test_jev_ads.py` no importa → `ModuleNotFoundError: No
  module named 'app.jev_ads'` (`jev_b2_r1_rojo_11.txt`).
- VERDE: 15 pruebas (`jev_b2_r1_verde_11.txt`); casos del DoD: compatible
  con hueco (HayCompatible con cobertura parcial visible: 2/3 con juicio),
  todos negativos con universo desconocido, todos negativos con hueco de
  ficha, control positivo NingunoCompatible (universo exhaustivo + fichas
  + no_satisface en cada par), conjunto vacio (universo_vacio; vacio no
  prueba exclusion), ficha de otra variante no acredita (ficha_ausente +
  juicio_ausente), miembro sin ficha, juicio insuficiente, fallo del
  proveedor (nunca incompatibilidad), ASIN-like (NoAplicaTexto ->
  texto_no_aplica), errores estructurales (ficha repetida, dos juicios
  por ficha) y pureza (AST sin imports de red/DB).
- Nota de diseño: `es_asin_like` usa la MISMA regla que
  `fabrica_plan.PATRON_ASIN` (`^b0[a-z0-9]{8}$` ignore-case), con prueba
  de equivalencia contra `PATRON_ASIN`; una fuente para el numero.

## TDD 1.2 — migracion 0049, catalogo y comando administrativo

- ROJO: `tests/test_jev_catalogo.py` no importa → `ModuleNotFoundError:
  No module named 'app.jev_catalogo'` (`jev_b2_r1_rojo_12.txt`).
- VERDE: 17 pruebas (`jev_b2_r1_verde_12.txt`), estaticas (pglast) y de
  integracion en Postgres 16 local (DB desechable por test, 0001 + 0004 +
  0049):
  - cuatro tablas + FKs muerden (revocacion, revision/decision,
    par_evento/revision; producto inexistente lo muerde ANTES el trigger
    de cobertura, defensa en profundidad);
  - hash canonico + registro idempotente (mismo contenido -> misma fila,
    `ya_existia`; correccion -> version nueva);
  - UNIQUE `solicitud` (idempotencia de revision; el mismo solicitud con
    otro payload se rechaza) y CHECK discriminador del sujeto
    (decision/semillas; semillas exige plan + hash + fuentes);
  - append-only por motor (UPDATE/DELETE con `prohibir_mutacion` en las
    cuatro tablas; TRUNCATE incluido);
  - roles de minimo privilegio con SET ROLE: `app_jev` lee catalogo e
    inserta revision/eventos y NO puede leer decision/ledger/goals/
    search_term_observation ni insertar fichas ni hacer UPDATE;
    `app_admin` registra y revoca fichas y no escribe revisiones;
    `app_read` lee y no inserta;
  - reglas temporales en triggers UTC (`revisar_antes_de >=
    observado_at`; `decided_at/captured_at <= created_at`; listings sin
    NULLs/duplicados y del producto/plataforma correctos; created_at
    vuelve con offset UTC 0);
  - lookup por IDs (`ficha_vigente` por producto+plataforma+listing:
    cubre, no revocada, no vencida; otra variante NO acredita; doble
    revocacion rechazada);
  - censo LEFT JOIN que conserva: anuncio sin `listing_id` (miembro sin
    producto, no se descarta), anuncio sin `ad_entity_state`, dedup de
    productos conservando identidad de anuncios; `exhaustivo=False`
    SIEMPRE en grupos Amazon (universo `desconocido`, E/0.2);
  - extremo a extremo del fallo mas probable del plan: producto sin
    listing + juicio `no_satisface` en el resto → Indeterminado
    (ficha_ausente), JAMAS NingunoCompatible;
  - `jev_par_evento` encadenado: resultado exige intencion de la misma
    revision y clave (CHECK + trigger), reutilizacion exige exito de la
    misma revision y clave (a fallo se rechaza), UNIQUE
    revision/par/ordinal/tipo;
  - reversa `0049_reversa_jev_ads.sql` ensayada en DB de prueba: guarda
    aborta con datos, sin datos deja la base como 0001 y elimina
    `app_jev` (revoca antes sus grants sobre tablas ajenas);
  - CLI `tools/jev_fichas.py` con `ORBIT_DSN_ADMIN` de prueba: registrar
    en seco imprime hash y no escribe; `--aplicar` es idempotente;
    revocar y doble revocacion (salida != 0).

## Hallazgo de la bateria completa (arreglado en el bloque)

`tests/test_schema_docs.py::test_launch_doctor_mismo_conteo_que_esquema`
fallo tras crear 0049 (59 declaradas vs 63 reales): las cuatro tablas Jev
suben el conteo de tablas BASE. Arreglado actualizando
`verify/Launch.md` y `verify/Doctor.md` 59 -> 63 (y 73 -> 77 en la nota
de vistas). Primera corrida completa: 1 fallo, 3,778 pasan
(`jev_b2_r1_bateria.txt`); final sobre `b06a723`: 3,779 pasan, 0 fallan
(`jev_b2_r1_bateria_final.txt`).

## Comandos

```
uv run --frozen python -m pytest tests/test_jev_ads.py -q                     # rojo 1.1 y verde
uv run --frozen python -m pytest tests/test_jev_catalogo.py -q                # rojo 1.2 y verde
uv run --frozen python -m pytest tests/test_jev_ads.py tests/test_jev_catalogo.py -q   # 32 passed
uv run --frozen python -m pytest -q                                           # 3,779 passed (final)
uv run ruff check . && uv run ruff format .                                   # limpio
pre-commit run --all-files                                                    # limpio
git commit ...                                                                # sin --no-verify
```

Integracion DB: Postgres 16 local en 127.0.0.1:5432 (superuser de prueba;
DBs desechables `orbit_jev01_*`, borradas en `finally`). NUNCA prod; sin
consultas a datos reales en este bloque.

## Limites respetados

- Cero llamadas a TypeSafe/Jev; cero claves; sin secretos en codigo,
  pruebas o evidencia (el DSN de prueba viene de `ORBIT_TEST_DSN`/default
  local y el CLI recibe `ORBIT_DSN_ADMIN` via monkeypatch).
- El asesor no se llama desde `cycle.py`, `apply_cola.py` ni
  `apply_harvest.py` (ningun archivo de aplicacion fue tocado; diff
  limitado a `app/jev_ads.py`, `app/jev_catalogo.py`, `tools/jev_fichas.py`,
  `migrations/0049_*`, `tests/test_jev_*`, `verify/*.md`).
- GET no implementado (fila 2.1); no hay red en 1.1/1.2.
- Sin push; commits locales hasta `b06a723`.

## Decisiones de diseño (spec manda; aqui solo lo no fijado)

- `componer` no recibe `ahora`: la vigencia del juicio la garantiza el
  llamador (reutilizacion solo de exitos validos, fila 1.4); `componer`
  agrega motivos, no revalida fechas.
- Motivo nuevo `juicio_ausente` (ademas de los cuatro del encargo): un
  miembro con ficha sin par alineado dejaria un Indeterminado sin causa
  declarada. `universo_vacio` separado de `universo_desconocido` porque
  el DoD distingue conjunto vacio de censo sin exhaustividad.
- `ficha_ausente` tambien cubre el juicio cuya ficha no coincide con
  ninguna del censo (otra variante): la acreditacion falla por ficha.
- `censo_grupo` no consulta `ad_entity_state` a proposito: al no filtrar
  por estado, el estado ausente o incierto no puede descartar un anuncio
  (docstring del modulo).
- `censo_grupo` devuelve `MiembroCenso` con `ficha_version_id=None`: las
  fichas se congelan al construir los pares (1.4), no en el censo.
- `jev_revision` exige `fuentes_semillas` para sujetos semillas (CHECK
  discriminador) y `app_jev` SIN lectura de `search_term_observation`
  (los terminos llegan dentro del contexto congelado de la revision; si
  1.4 necesita lectura directa, se agrega grant visible con su prueba).
- Error estructural (ValueError) para censo malformado (misma ficha en
  dos miembros, dos juicios por ficha): es bug del llamador, no estado de
  datos.
