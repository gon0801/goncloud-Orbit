# Brief para Muse: FABRICA 02 (F2) — R.1, catálogo de mutantes

Base `origin/master` (Fase A cerrada: A.0–A.6 en master, HEAD de código
`7a32ec3`; el cierre documental es `41bc6aa`). Rama desde el remoto,
**jamás** desde tu master local; `git log origin/master..HEAD` debe mostrar
solo los commits de esta tarea. **Una sola tarea: la parte del implementador
de R.1**, el catálogo enumerado `docs/evidencia/fabrica-02/R.1/mutantes.md`
(en el plan se le llama `E/R.1/mutantes.md`).

Contrato: fila R.1 de `plans/fabrica-02.md` (v1.9), la tabla de criterios de
aceptación AC1a–AC12 del mismo plan, y los residuales declarados en las filas
A.3, A.4, A.5 y A.6. Este brief es ejecutable y subordinado a esos documentos;
donde precisa la letra de la fila, lo dice y manda.

Roles de R.1, para que no haya confusión: **tú entregas el catálogo y lo
ejecutas**; el lead lo audita; el revisor cruzado (kimi) lo re-muta y verifica
guard, dinero, append-only, quota, reversa, GRANTs por columna y replay. El
loop no termina con tu PR ni con el visto de kimi: termina con el APPROVE del
lead sobre un SHA. Hasta entonces el PR queda abierto y en cola; **no se
mergea**.

## Resultado exacto

Un archivo `docs/evidencia/fabrica-02/R.1/mutantes.md` con una tabla, una
fila por mutante, donde cada fila es **reproducible por alguien que no eres
tú**:

| # | AC | Sitio | Mutación (diff mínimo) | Test asesino | Rojo literal | Estado |

- **#**: numeración estable (M01, M02, …); kimi y el lead la citan.
- **AC**: el criterio de aceptación que el mutante ataca (AC1a … AC12), o
  `OBL` para los catorce obligatorios de la fila, o `RES` para un residual
  declarado en A.3–A.6.
- **Sitio**: `archivo:función` (y línea al momento del SHA).
- **Mutación**: el diff unificado mínimo, de 1 a 5 líneas, tal cual se
  aplica con `git apply` o a mano. Nada de "quitar la validación": el texto
  exacto que cambia.
- **Test asesino**: `tests/archivo.py::test_nombre`. Uno basta; si mueren
  varios, el más específico.
- **Rojo literal**: la primera línea de `assert` fallida o el `E   …` de
  pytest, copiada. Sin esto la fila no cuenta.
- **Estado**: `muere` o `sobrevive → test nuevo <tests/archivo.py::test>` —
  un sobreviviente **no se declara**: se cierra con un test en este mismo PR
  (regla de la fila R.1). Si al cerrarlo descubres que el mutante revela un
  bug real (el test nuevo falla también sin la mutación), paras, lo dices en
  el PR con el rojo, y el lead decide; no arreglas código de `app/` por tu
  cuenta.

Cobertura mínima: **al menos un mutante por cada AC** (AC1a, AC1b, AC2 …
AC12: trece filas mínimo) **más los catorce obligatorios** de la fila R.1,
listados abajo con su sitio. Los mutantes que ya documentaron los PRs
#258, #261, #264, #267, #269, #272, #274 y #275 se reutilizan (cita el PR),
pero **se vuelven a ejecutar** sobre `7a32ec3`: el catálogo es evidencia
fresca, no un índice de evidencia vieja.

Al final del archivo, tres secciones cortas: (1) comando exacto con el que
corriste cada mutante y cómo lo revertiste (`git stash`/`git checkout --`),
con `git status` limpio como cierre; (2) lista de sobrevivientes cerrados y
sus tests nuevos; (3) lo que NO cubre el catálogo y por qué (por ejemplo, el
residual de A.6: el envío en `_fase_notifica` no tiene test conductual y el
mutante «enviar en TX2» es indistinguible en verde — se enumera como `RES`
con estado `no discriminable`, no como muerto).

## Antes de escribir una línea

Lee, en este orden, y anota en el PR el SHA que leíste:

1. `plans/fabrica-02.md`: fila R.1, tabla AC1a–AC12 (líneas ~454–467), y
   las notas de cierre (columna de estado, marca 完了) de A.1, A.3, A.4, A.5
   y A.6, que ya enumeran mutantes
   ejecutados y residuales.
2. Los cuerpos de los PRs #267, #269, #272, #274 (`gh pr view N --json
   body`): sección «Mutantes» de cada uno.
3. El código de los sitios de abajo. No mutes lo que no leíste.
4. `docs/evidencia/fabrica-02/0.1/` y `0.2/` como referencia de formato de
   evidencia del repo.

Baseline con DSN real **antes** del primer mutante, y pégalo en el PR:

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_fabrica_f2_hermanas.py tests/test_fabrica_f2_biblioteca.py \
  tests/test_fabrica_f2_visibilidad.py tests/test_harvest_destino.py \
  tests/test_harvest_excepcion.py tests/test_apply_harvest.py \
  tests/test_fabrica_0038.py tests/test_reversa_harvest.py
```

Todo verde y `0 skipped` es la condición de partida. Un skip es un test que
no discrimina nada: si aparece, dilo antes de seguir.

## Los catorce obligatorios, con su sitio

Cada uno es una fila `OBL`. El sitio es orientación al SHA `7a32ec3`; si el
código movió, corrige la línea, no el mutante.

1. **`_avanza` merge superficial de `hermanas`** —
   `app/apply_harvest.py:_avanza` (~894): hoy `ext.update(...)` con `ids`
   planos; el mutante hace que un `ids={"hermanas": {...}}` **reemplace** el
   dict `hermanas` acumulado en vez de conservar las hermanas ya sembradas
   (o al revés: que un merge profundo resucite una hermana ya `creada`).
   Debe morir por el test de roster congelado / reintento por ciclo.
2. **LIST sin `adGroupIdFilter`** — `app/apply_harvest.py` (~553–572):
   quitar el filtro del body en alguna página. Muere por el test que exige el
   filtro en TODAS las páginas.
3. **LIST que cuenta `ARCHIVED`** — `app/apply_harvest.py` (~603–647,
   ~1255, ~1321): tratar `state=ARCHIVED` como vivo en la identidad o en el
   conteo de la página. Muere por identidad/`_solo_en_otro_ad_group`.
4. **Hermanas antes del readback de keyword** —
   `app/apply_harvest.py` fase `exact_created` (~1589–1626): sembrar el
   roster o avanzar a `hermanas_negadas` antes de confirmar la keyword por
   readback. Muere por el sello durable (decisión/cola/cooldown/roster
   **después** del evento de valor).
5. **Reversa origen-antes-que-hermanos** — `tools/reversa_harvest.py` y
   `app/apply_harvest.py` reversa (~979–1022): invertir el orden (borrar el
   negativo del origen antes que las hermanas / la keyword). Muere por el
   test de orden de la reversa.
6. **`quota_cobrada=True` en filas `hermana`** — `app/apply_harvest.py`
   (~1789): cobrar quota en el ledger de hermana. Muere por AC12.
7. **`COALESCE` de moneda** — `app/optimizer/harvest_destino.py:_monto`
   (~160) y la escritura de bibliotecas en `app/biblioteca.py`: meter un
   default de moneda (o de costo) donde hoy viaja `None`. Muere por «sin
   dinero en biblioteca» / bid del goal sin moneda inventada.
8. **Resolutor por nombre** — `app/optimizer/harvest_destino.py:_lee` /
   `decide` (~97, ~175): elegir la exacta por nombre (`LIKE '%exact%'`) en
   vez de por `campana_grupo_rol.rol = 'category_exact'`. Muere por el
   fixture con señuelo fuera del grupo llamado `category_exact` (A.1 a).
9. **Fallback al goal con terna presente** — `harvest_destino.decide`
   (~177–209): cuando la terna del goal está presente y contradice la exacta
   del grupo, resolver por la terna en vez de `destino_inconsistente`. Muere
   por AC1b.
10. **`USAGE` de secuencia ausente** —
    `migrations/0038_fabrica_hermanas_biblioteca.sql` (~246): quitar el
    `GRANT USAGE ON SEQUENCE …` a `app_decide`. Muere por el test de GRANTs
    con rol real (`tests/test_fabrica_0038.py`), que debe hacer el INSERT
    como `app_decide`, no comprobar el catálogo.
11. **Fase nueva fuera del índice parcial** — misma migración (~79–81):
    quitar `hermanas_negadas` del `WHERE` de `harvest_job_en_vuelo`. Muere
    por el test de «un segundo job del mismo (platform, ad_entity_id,
    search term) mientras el primero está en `hermanas_negadas`».
12. **Fase nueva fuera de los SELECT de reconciliación** —
    `app/apply_harvest_reconciliacion.py` (~54, ~92) y
    `app/apply_harvest.py` (~165): quitar `'hermanas_negadas'` de un `IN`.
    Muere por el test de reanudación en higiene (AC3).
13. **`completa` derivada de la terna** —
    `harvest_destino._terna_completa` (~146) y los SELECT de reconciliación
    que usan `completa`: derivar «completa» de que la terna exista en vez de
    del estado real del job/goal. Muere por el test de goal bid-solo (terna
    NULL) que sí cosecha por grupo (AC11).
14. **Dedupe apuntado al goal** — `app/apply_harvest_reconciliacion.py`
    (~151) y `app/apply_harvest.py` (~177): deduplicar por el destino del
    goal fresco en vez del destino **resuelto y congelado** en la decisión.
    Muere por AC7 (replay tras cambiar el grupo o limpiar la terna).

Si un obligatorio no tiene test que lo mate hoy, ese es exactamente el
hallazgo que R.1 busca: escribe el test, verifica que sin la mutación pasa y
con la mutación falla, y anótalo como `sobrevive → test nuevo`.

## Archivos y fronteras

Puedes crear o tocar **solo**:

- `docs/evidencia/fabrica-02/R.1/mutantes.md` (nuevo) y, si hace falta,
  `docs/evidencia/fabrica-02/R.1/*.log` con salidas literales.
- `tests/test_fabrica_f2_*.py`, `tests/test_harvest_destino.py`,
  `tests/test_harvest_excepcion.py`, `tests/test_apply_harvest.py`,
  `tests/test_reversa_harvest.py`, `tests/test_fabrica_0038.py`: **solo para
  agregar tests** que maten sobrevivientes. Ningún test existente se borra,
  se relaja ni se marca `skip`/`xfail`.

Prohibido: cualquier archivo de `app/`, `tools/`, `migrations/`, `ops/`;
`plans/` y `docs/` fuera de `docs/evidencia/fabrica-02/R.1/`; el tracker.
Las mutaciones se aplican y se revierten en tu árbol de trabajo; **ninguna
mutación se commitea**. El último commit debe dejar `git diff origin/master
--stat` con solo evidencia y tests.

Cero producción, cero SSH a `goncloud`, cero Amazon, cero secretos.
**Prohibido tocar el contenedor de producción**; la corrida real de F2 es
D.1–D.3 y la corre el dueño con el lead.

## Orden obligatorio

1. Baseline verde (arriba) y pegado en el PR.
2. Inventario: lista de tests existentes por AC (una tabla AC → tests). Si
   un AC no tiene test, anótalo antes de mutar.
3. Los catorce obligatorios, en orden, uno a la vez: aplicar, correr los
   focales del archivo relevante, copiar el rojo, revertir, `git status`
   limpio. Commit del catálogo parcial.
4. Un mutante por AC restante (los que no quedaron cubiertos por los
   obligatorios).
5. Sobrevivientes → test nuevo, rojo-primero: el test falla con la mutación
   y pasa sin ella, y **pasa también sin el test viejo** (no lo dupliques).
6. Secciones finales del archivo (comandos, sobrevivientes cerrados, no
   cubierto). Último commit; `git log origin/master..HEAD` solo tuyo.

## DoD binario

1. `docs/evidencia/fabrica-02/R.1/mutantes.md` existe, con ≥27 filas
   (13 AC + 14 OBL), numeradas, cada una con diff mínimo, test asesino y
   rojo literal.
2. Los catorce obligatorios están todos, con su sitio real en `7a32ec3`.
3. Cero filas en estado `sobrevive` sin `→ test nuevo`; cada test nuevo
   demostrado rojo con la mutación y verde sin ella.
4. Ningún test existente borrado, relajado, `skip` ni `xfail`; baseline
   final igual o mayor al inicial y `0 skipped`.
5. `git diff origin/master --stat` solo muestra evidencia y tests.
6. Ruff, `pre-commit run --all-files` y la batería completa verdes **una
   sola vez en CI** sobre el SHA final.

## Verificación y entrega

```bash
uv run --frozen ruff check tests/
uv run --frozen ruff format --check tests/
pre-commit run --all-files
pre-commit run --hook-stage pre-push   # el push normal también lo ejecuta; jamás --no-verify
```

Abre PR a `master` desde `origin/master`, carril **gate**. El PR **queda en
cola**: no se mergea hasta el APPROVE del lead; el dueño mergea todo al final
del día en el orden que el lead le pase.

La descripción del PR incluye: SHA base leído, baseline literal, la tabla
AC → tests, el conteo de mutantes por estado, la lista de sobrevivientes
cerrados con sus tests, los hallazgos que crees bugs reales (si los hay, con
el rojo y **sin** arreglo en `app/`), residuales (`RES`, con la razón), y el
enlace al CI verde.

Loop de cross-review, sin tope de rondas: kimi re-muta sobre tu SHA; lo que
encuentre lo corriges en el mismo PR y kimi vuelve; cuando kimi sale limpio
entra el lead; si el lead encuentra algo, vuelve a ti y kimi lo vuelve a ver.
Solo el APPROVE del lead cierra el loop.

## Hallazgos del pre-pase de kimi que este PR cierra (obligatorio)

El pre-pase nocturno de kimi (`docs/evidencia/fabrica-02/R.1/revision-kimi.md`)
salió sin hallazgos altos ni medios y con cinco bajos de tests. Tres de ellos
son sobrevivientes reales y se cierran aquí, rojo-primero, además del
catálogo:

- **H2 — `tests/test_reversa_harvest.py::test_plan_precondiciones_fallan_cerrado`**
  anuncia cuatro precondiciones y ejercita dos. Agrega los dos casos que
  faltan: cola de la decisión no `applied` → `ValueError("cola no applied")`,
  y `external_ids` sin `keyword_id` o sin `negative_id` →
  `ValueError("sin keyword_id o negative_id")`. Demuestra que sin las guardas
  de `app/apply_harvest.py:1150-1157` ambos tests caen.
- **H3 — `tests/test_fabrica_f2_visibilidad.py::test_a6_campana_suelta_salta_en_skips_pero_no_en_saltos_grupo`**
  no siembra términos y sus asserts se cumplen vacíos. O siembra el término
  (como su gemelo de las líneas siguientes) y aserta que el skip
  `sin_destino_de_harvest` aparece en `skips.termino` y no en `saltos_grupo`,
  o bórralo si el gemelo ya cubre exactamente eso (dilo en el PR).
- **H5 — los cinco tests del CLI en `tests/test_reversa_harvest.py:677-960`**
  fijan `ORBIT_DSN_DECIDE` a `orbit:orbit@localhost:5432`. Derívalo de
  `_test_dsn()` como el resto de la suite.

**H4** (espía de `_envia_texto` en `tests/test_fabrica_f2_biblioteca.py:1015-1106`
cuyo assert de timing se traga el `try/except` del sender) se enumera en el
catálogo como `RES` con estado `no discriminable`, salvo que encuentres cómo
asertar el timing sin tocar `app/notifica.py`. **H1** ya no aplica (cerrado
en `41bc6aa`).

Y una verificación extra en el catálogo: el residual de A.6 «envío en
`_fase_notifica` sin test conductual». Kimi sostiene que
`tests/test_notifica.py::test_fase_notifica_mapea_salto_destino_a_nota_y_acumula`,
`::test_fase_notifica_salto_destino_canal_caido_deja_nota` y
`::test_notifica_destino_grupo_envia_y_tumba` ya lo cubren. Demuéstralo con el
mutante «enviar dentro de la transacción» y su rojo, o déjalo como `RES`.
