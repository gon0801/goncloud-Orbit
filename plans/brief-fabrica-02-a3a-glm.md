# Brief para GLM: FABRICA 02 (F2) — tarea A.3a

Base `origin/master` `2e3192f` (A.0, A.1 y A.2 cerradas; tras `git fetch` usa
el HEAD vigente). Rama desde `origin/master`, **jamas** desde tu master local.
**Una sola tarea: A.3a.** Nada de A.3, aunque este refactor la prepara.

Contrato: fila **A.3a** de `plans/fabrica-02.md`, la frontera declarada en
`tests/test_architecture.py:62-71` y la conducta actual fijada por
`tests/test_apply_harvest.py`. Este brief baja ese contrato a una separacion
segura; no reabre decisiones de F2.

`team_validation_mode: not_required_lightweight`: es un refactor acotado, sin
cambio de producto, API externa, schema, permisos, dinero ni HTTP.

**Spec skip reason:** A.3a no cambia el contrato del producto. La especificacion
de FABRICA 02 y `plans/fabrica-02.md` ya fijan la conducta; el criterio de esta
tarea es equivalencia observable antes/despues.

## Antes de escribir una linea

1. `plans/fabrica-02.md`: fila A.3a, «Propiedad de archivos y concurrencia» y
   «Estado para la siguiente sesion».
2. `docs/CONTEXTO.md`, reglas 1–10. Innegociables.
3. `app/apply_harvest.py` completo. No muevas bloques por rango sin entender
   que SQL y helpers consume cada funcion.
4. `tests/test_apply_harvest.py` completo. En `origin/master` recolecta
   **48 tests**; ese conteo no puede cambiar en esta tarea.
5. `tests/test_architecture.py:1-120`: presupuesto de 900 lineas, allowlist
   auto-limpiante y regla anti-Goodhart.
6. Callers que fijan la superficie compatible:
   `app/apply.py:1451-1457`, `app/apply_cola.py`, `app/cycle.py`,
   `app/notifica.py` y los imports de `tests/test_apply_harvest.py`.

Antes de editar, corre y guarda en la descripcion del PR la linea final de:

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q tests/test_apply_harvest.py
uv run --frozen python -m pytest -q tests/test_architecture.py
```

La primera debe dar **48 passed, 0 skipped**. Sin `ORBIT_TEST_DSN`, un verde con
skips no es baseline valido.

## Frontera obligatoria

Separa el modulo en dos responsabilidades de runtime:

- `app/apply_harvest.py`: **ejecucion y superficie compatible**. Conserva el
  nacimiento y avance del job, contexto, bid, identidad/acks compartidos,
  reversas, `_paso_*`, `_continua_job`, `aplica_harvest`, dataclasses y
  constantes publicas.
- `app/apply_harvest_reconciliacion.py`: **revalidacion y reconciliacion**.
  Recibe `revalida_harvest`, `reconcilia_harvest` y sus helpers/SQL exclusivos.

No crees un paquete ni una tercera capa `common` en A.3a. La separacion de dos
responsabilidades es suficiente y minimiza el diff. `app/apply_harvest.py`
seguira sobre 900 lineas: se mantiene en la allowlist con una razon actualizada
que diga que la reconciliacion ya fue extraida y que el resto es la maquina de
ejecucion sellada. El modulo nuevo debe quedar bajo el limite y no entra en la
allowlist.

### Lo que se mueve

Mueve a `app/apply_harvest_reconciliacion.py`, sin reescribir la logica:

- `revalida_harvest`;
- `_reconcilia_pauses`;
- `_cola_de`;
- `_reconcilia_negativas`;
- `_reconcilia_harvest_huerfanas`;
- `reconcilia_harvest`;
- `_TARGET_REVALIDA`, `_FLOOR_REVALIDA`, `_CEILING_REVALIDA`;
- el SQL usado solo por esas funciones:
  `_SQL_JOBS_EN_VUELO`, `_SQL_DESCARTA`, `_SQL_COLA_DE`,
  `_SQL_NEGATIVAS_APLICANDO`, `_SQL_HARVEST_APLICANDO`,
  `_SQL_JOB_EN_VUELO_DE`, `_SQL_PAUSES_APLICANDO`, `_SQL_PAUSE_PROPIO`,
  `_SQL_INSERT_REACTIVACION` y `_SQL_CACHE_ESTADO`.

El SQL y los helpers compartidos por ejecucion y reconciliacion **se quedan en
`app/apply_harvest.py` y se reutilizan**. No dupliques SQL, parsers de ack,
identidad, sellos, quota ni transacciones para hacer el corte mas comodo.

### Direccion de imports y compatibilidad

La dependencia de runtime es una sola:

```text
apply_harvest_reconciliacion -> apply_harvest
```

El modulo de reconciliacion puede importar `app.apply_harvest` con un alias y
usar desde ahi los helpers compartidos. `app.apply_harvest` **no** importa el
modulo nuevo en top-level. Sus dos funciones compatibles delegan con import
local, ya con el modulo original inicializado:

```python
def revalida_harvest(conn, platform, fila, ahora):
    from app import apply_harvest_reconciliacion

    return apply_harvest_reconciliacion.revalida_harvest(conn, platform, fila, ahora)


def reconcilia_harvest(conn, aplicador, platform):
    from app import apply_harvest_reconciliacion

    return apply_harvest_reconciliacion.reconcilia_harvest(conn, aplicador, platform)
```

Conserva las firmas y anotaciones actuales; el snippet muestra la direccion,
no autoriza a perder tipos ni docstrings. No uses un import bidireccional de
top-level, `__getattr__`, carga dinamica por nombre ni copia de funciones.

Los callers existentes siguen importando **solo** `app.apply_harvest`. No
cambies `app/apply.py`, `app/apply_cola.py`, `app/cycle.py`, `app/notifica.py`
ni los imports de tests para apuntarlos al modulo nuevo: hacerlo esconderia una
rotura de compatibilidad en lugar de demostrar equivalencia.

La superficie que debe seguir resolviendo desde `app.apply_harvest` incluye:

- `AlertaHarvest`, `ResultadoHarvest`, `ResumenReconciliacion`;
- constantes `MOTIVO_*`, `PATH_BID_SUGERIDO` y `TOPE_PAGINAS_LIST`;
- `bid_sugerido`, `bid_efectivo`, `aplica_harvest`,
  `revalida_harvest`, `reconcilia_harvest`;
- `reversa_harvest_parcial`, `reversa_harvest_completo`;
- privados consumidos fuera del modulo: `_id_de_ack`, `_errores_de_ack`,
  `_reversa_rechazada`, `_resultado_reversa_rechazada`, `_identidad` y
  `_solo_en_otro_ad_group`.

## Conducta que no se toca

A.3a tiene `[tdd:skip:refactor-sin-comportamiento]`. No aproveches el traslado
para limpiar, renombrar o corregir nada. En particular quedan literales:

- fases actuales que consume la app: `pending`, `negative_created` y
  `exact_created`; A.2 ya admite `hermanas_negadas` en schema, pero **A.3** es
  quien la cablea en codigo;
- orden y limites de transacciones/commits alrededor del ledger y HTTP;
- una unidad de quota por harvest y cero recobro en reconciliacion;
- shapes de payload/ack, paginacion, identidad completa y fail-closed;
- orden de reversa, sellos de cola/job y alertas;
- revalidacion por evidencia fresca, resolutor de destino y fallback de
  compatibilidad para fixtures pre-F2;
- SQL compartido, strings de resultado, logs y manejo de excepciones.

No agregues tests de comportamiento ni cambies fixtures. Esta tarea demuestra
equivalencia con la suite existente. La unica edicion esperada en tests es la
razon de `ALLOWLIST_TAMANO` en `tests/test_architecture.py`; el conteo de sus
tests tambien permanece en **19**.

## Trampas que el lead revisara

1. **Ciclo de imports:** `apply_harvest` importando arriba el modulo nuevo, que
   a su vez necesita helpers de `apply_harvest`.
2. **Compatibilidad falsa:** cambiar callers/tests al modulo nuevo para que el
   viejo nombre deje de importar o delegar.
3. **Duplicacion:** copiar `_lista_todos`, `_identidad`, SQL compartido o
   sellos en ambos archivos.
4. **Cambio escondido:** reordenar `commit`, `transaction`, quota, HTTP o
   transiciones mientras se mueve codigo.
5. **A.3 adelantada:** meter `hermanas_negadas`, `tipo='hermana'`, LIST
   filtrado, reintentos, reversa nueva o biblioteca. Todo eso queda fuera.
6. **Goodhart:** crear pedazos incoherentes solo para bajar de 900 lineas. La
   entrada de ejecucion puede seguir temporalmente en allowlist con razon.
7. **Skips verdes:** declarar exito sin DSN aunque la suite focal haya saltado
   pruebas de Postgres.

## DoD binario

1. `app/apply_harvest_reconciliacion.py` contiene revalidacion y los cuatro
   barridos; queda bajo 900 lineas y fuera de `ALLOWLIST_TAMANO`.
2. `app/apply_harvest.py` contiene ejecucion y conserva toda la superficie
   compatible; su allowlist explica el estado posterior a A.3a.
3. No hay SQL ni helpers compartidos duplicados, ni ciclo de imports
   top-level.
4. `app/apply.py`, `app/apply_cola.py`, `app/cycle.py`, `app/notifica.py` y
   `tests/test_apply_harvest.py` no cambian.
5. Antes y despues, `tests/test_apply_harvest.py` da exactamente **48 passed,
   0 skipped** con el mismo DSN de test.
6. `tests/test_architecture.py` da exactamente **19 passed**.
7. Ruff pasa sobre los archivos tocados y `pre-commit run --all-files` pasa
   antes del commit; jamas `--no-verify`.
8. `git log origin/master..HEAD` muestra solo el commit de A.3a.
9. Un PR a `master`; la bateria completa corre **una sola vez en CI** sobre el
   SHA final. No la repitas localmente si CI ya valido ese SHA.

## Reglas de proceso

- Cero produccion, ssh, secretos, Amazon, migraciones o cambios de base.
- No toques `app/ads/write.py`, `app/apply.py`, `app/apply_cola.py`,
  `app/cycle.py`, `app/notifica.py`, migraciones, specs, tracker,
  `plans/ROADMAP.md` ni `docs/CHAT-CONTEXT.md`.
- No cambies markers de `plans/fabrica-02.md`; los cierra el lead tras revisar
  evidencia.
- Una rama, un commit de implementacion, un PR. El lead agrupa hallazgos en una
  sola ronda. No hagas cross-review adicional salvo que el lead lo pida.
- Si el corte exige cambiar comportamiento o otro caller, para y decláralo en
  el PR; eso ya no es A.3a.

## Entrega

La descripcion del PR incluye:

- baseline y resultado posterior: `48 passed, 0 skipped` en
  `test_apply_harvest.py` y `19 passed` en `test_architecture.py`;
- lineas antes/despues de los dos modulos;
- confirmacion de que los callers y tests de comportamiento no cambiaron;
- salida de `git log origin/master..HEAD`;
- enlace al CI completo verde del SHA final.

No cierres A.3a por haber creado el archivo nuevo: se cierra solo si la suite
existente prueba la misma conducta a traves de `app.apply_harvest`.
