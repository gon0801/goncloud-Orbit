# ARREGLOS 03 — D-N: los errores de JEV se redactan antes de persistirse (TNS1)

Base vigente: `origin/master` `06cbd79` (P-B #641, D-E #639, D-I #638 y D-K #636, encima de D-C #635 y D-J #631).
Rama `ao/goncloud-orbit-21/dn-redaccion-jev`. El código y las pruebas son los de `dfef446` (sobre `fa3c57d`), que aprobó
Kimi. Master entró con el merge `7e4eb39` sin cambiar código de D-N (`git merge-base --is-ancestor origin/master HEAD` → 0).
Rojo del 2026-11-18 11:31 UTC sobre `fa3c57d`; verde final 12:22 UTC sobre `7e4eb39`.

El secreto de todas las corridas es **ficticio** (`CLAVE_FICTICIA_REVISION_12345`, el mismo de
`revision/ingest.py` `casoS1`). Se demuestra el riesgo condicional (un proveedor que repite una clave ya
registrada dentro de una respuesta inválida) y su cierre; no se afirma ninguna filtración real.

## Forma de los datos

- `pedir_juicio` → `ResultadoPar` | `FalloPar(codigo, detalle, duracion_ms, usage)`. `FalloPar.__post_init__`
  recorta `detalle` a 300 caracteres. `pedir_juicio` registra la `api_key` con `register_secret`.
- `_validar_respuesta` devuelve el motivo como texto y repite el valor del proveedor con `!r`:
  `model distinto al contrato: '<model>'` y `choice fuera de las opciones: '<choice>'`.
- Límite durable: `Libro._resultado` escribe `jev_par_evento.error = "<codigo>: <detalle>"` y devuelve ese mismo
  texto como diagnóstico (`FalloProveedor.motivo`). `jev_asesor` solo relee el `error` guardado.

## Cambio

- `app/jev_libro.py` `Libro._resultado`: `error = scrub(f"{codigo}: {detalle}")`; ese texto se inserta en
  `jev_par_evento.error` y se devuelve en `FalloProveedor.motivo`. Clasificación (`respuesta_invalida: …`),
  duración y `usage` se conservan.
- `app/jev_juicios.py` `pedir_juicio`: `FalloPar("respuesta_invalida", scrub(validado), …)`. La redacción va antes
  de construir `FalloPar` porque el recorte a 300 podría partir un secreto y dejar un trozo que `scrub` ya no
  reconoce; es el mismo patrón que timeout y red ya seguían.
- Una sola utilidad: `app.redaction.scrub`. No se guarda el cuerpo crudo. TN09 intacto: no se movió la guarda
  ni cambia quién abre o cierra la transacción (`juicio`/`pagar` y sus `commit` siguen igual).

## Rojo previo (SHA `fa3c57d`, sin el arreglo)

Entorno TN de ENCARGO-COMUN (`127.0.0.1:55449`, bajo el candado `orbit-tn`), más `PYTHONUTF8=1`: en Windows,
`revision/ingest.py` lee las migraciones con `read_text()` sin codificación y sin esa variable el fixture muere
con `UnicodeDecodeError` (error de entorno, no un rojo; no se tocó el arnés).

```
CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs \
  docs/evidencia/arreglos-05/revision/test_accept_ingest.py -k "test_S1"
→ 1 failed, 5 deselected   (exit 1)
test_accept_ingest.py:40  AssertionError:
  [{'campo': 'model', 'clave_ficticia_persistida': True}, {'campo': 'choice', 'clave_ficticia_persistida': True}]
```

Pruebas nuevas (`tests/test_jev_juicios.py`, PG de prueba `127.0.0.1:55433`) con `app/` de `fa3c57d`:

```
CI=1 PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs tests/test_jev_juicios.py -k arreglos03_dn_
→ 4 failed   (exit 1)
test_jev_juicios.py:467  assert 'CLAVE_FICTI...VISION_12345' not in "model disti...ISION_12345'"     [model]
test_jev_juicios.py:467  assert 'CLAVE_FICTI...VISION_12345' not in "choice fuer...ISION_12345'"     [choice]
test_jev_juicios.py:505  assert "respuesta_in...VISION_12345'" == "respuesta_in...*REDACTED***'"     [libro model y choice]
```

Discriminación por capa (cada prueba falla sin SU arreglo):

| `app/` aplicado | Resultado | Caen |
| --- | --- | --- |
| ninguno (`fa3c57d`) | 4 failed | las 4 |
| solo `jev_juicios.py` | 2 failed, 2 passed | `libro_guarda_y_devuelve_el_fallo_redactado[model,choice]` |
| solo `jev_libro.py` | 2 failed, 2 passed | `validacion_model_y_choice_no_repite_la_clave[model,choice]` |
| los dos (`dfef446`) | 4 passed | — |

La prueba del libro entrega a `Libro` el motivo CRUDO que produce `_validar_respuesta` real, así que demuestra
el límite durable por sí mismo, aunque otro `pedir` no redacte. Revisa las dos salidas: la fila persistida
(`error`, `duracion_ms`) y el diagnóstico devuelto.

## Verde final (HEAD `7e4eb39` = `dfef446` + merge de `origin/master` `06cbd79`)

| Fila | Comando | Resultado |
| --- | --- | --- |
| D-N.1 | `CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs docs/evidencia/arreglos-05/revision/test_accept_ingest.py -k "test_S1"` | **1 passed**, 5 deselected, exit 0 |
| D-N.2 | `… test_accept_ingest.py -k "test_S1 or test_09"` | **2 passed**, 4 deselected, exit 0 (sin skips) |
| D-N.2 (frontera) | `CI=1 PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs tests/test_jev_juicios.py -k arreglos03_dn_` | 4 passed, 0 skipped |

Vecinos tras integrar master (7e4eb39): `test_jev_juicios`, `test_jev_senales`, `test_jev_senales_cron` y `test_redaction` → 77 passed.

Primera corrida (árbol `dfef446` sobre `fa3c57d`, antes de D-E): D-N.1 1 passed (exit 0); D-N.2 1 passed (`test_S1`) y
1 failed (`test_09`), exit 1, por lo que explica la sección siguiente.

## TN09: estuvo en rojo en master por D-J; D-E lo devolvió a verde

Antes de D-E, `test_09` fallaba igual antes y después de D-N, con la misma aserción:

```
test_accept_ingest.py:17  assert r["exit"] == 0 and r["cierres"] and r["avisos"] == 0
AssertionError: {'exit': 1, 'cierres': [('sin_api_key',)], 'avisos': 0, 'tx_ajena_conservada': 1, ...}
stderr: jev-senales: no se juzgó ninguna búsqueda: falta la llave del proveedor de JEV. …
```

Las tres aserciones de TN09 que D-N debe conservar siguen verdes (`guarda_real == [True]`,
`entradas_leer_mundo == ["IDLE"]`, `leer_mundo_completo == [True]`) y `tx_ajena_conservada == 1`. Lo único que cae es
`exit == 0`: `caso09` simula `leer_api_key` en `""` y, desde D-J.4 (#631), `jev-senales` devuelve 1 con
`sin_api_key`, que es lo que D-J quiso. Mismo comando en el padre de D-J, `d57c76f` (árbol extraído aparte e importado
de ahí): **1 passed**. En `fa3c57d`: 1 failed. El `acepta.txt` de D-J no corrió `test_09`.

El fixture `caso09` de `revision/ingest.py` lo adapta D-E.7 (`DISENO.md`, D-G: «Tras D-E, `T ingest 'test_control or
test_09'` debe dar dos verdes»), que también guarda la copia `historico/` contra el manifest. D-N no edita ese
archivo para no chocar con D-E; se avisó al orquestador. D-E (#639) ya está en master con su adaptación de
`caso09`. Sobre `7e4eb39` (master `06cbd79`, con D-E) el Acepta D-N.2 da **2 passed** (`test_S1` y `test_09`), sin tocar la guarda, la transacción
ni el cierre de TN09.

## Pruebas existentes de los módulos tocados

`CI=1 PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs` sobre `test_jev_juicios`, `test_jev_senales`,
`test_jev_senales_cron`, `test_jev_cli`, `test_jev_catalogo`, `test_jev_ads`, `test_jev_avisos`, `test_jev_lectura`,
`test_jev_perimetro`, `test_jev_bloqueo_api`, `test_jev_pantallas` y `test_redaction`: **287 passed, 1 failed**. La que
falla es `test_jev_catalogo.py::test_reversa_deja_la_base_como_0001` por entorno: hace `DROP ROLE app_jev` (rol global del
clúster) y el PG compartido de 55433 tiene bases de otras corridas en curso (`auto03_b5_gon_pc_*`,
`orbit_api_fabrica_gon-pc_*`) con objetos de ese rol. D-N no toca migraciones ni roles.

## Revisión de secretos del cambio

- `uvx pre-commit run --files app/jev_juicios.py app/jev_libro.py tests/test_jev_juicios.py`: gitleaks **Passed**,
  check-merge-conflict **Passed**. Lo mismo para esta evidencia en su commit.
- El diff no trae llaves, tokens ni contraseñas. La única cadena tipo clave es la ficticia del caso portado, ya
  versionada en `revision/ingest.py`; en las pruebas se nombra `CLAVE_FICTICIA`.
- No se leyó ni se tocó producción, `ORBIT_SECRETS_DIR` ni `.env`. Los PG usados son los desechables de 55449 (TN) y
  55433 (pruebas). Los logs crudos quedaron fuera del repo y solo contienen la clave ficticia.

## prueba-real (la corre el lead tras integrar y desplegar)

`CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs
docs/evidencia/arreglos-05/revision/test_accept_ingest.py -k "test_S1 or test_09"` → se espera 2 passed.
Reversa: revertir el commit de código (`git revert dfef446`, o el commit del PR si se integra con squash) y volver a desplegar; no hay
migración ni datos que regresar. Las filas ya guardadas antes del despliegue no se reescriben (la tabla es append-only).
