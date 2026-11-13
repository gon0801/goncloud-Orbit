# ARREGLOS 03, frente PRECIOS: diseño

Base: `master a4644aad` (lo que corre en producción). Todo `archivo:línea` es de ese SHA.
Hallazgos: `docs/evidencia/arreglos-2026-11-12/precio.md` (F1 a F5, S2, E1, E4) y `estimacion.md` (F2).
Decisiones del dueño que aplican: 3 (se respeta la confirmación arriba de 50 cambios de precio al día; las claves `precio_cap_*` se borran). La 4 (en MeLi las variantes sin existencias no fijan el precio) espera la confirmación del dueño: su sección «P-E» no entra al plan (`PENDIENTE-P-E.md`).
Bloques: P-A a P-D y P-F, en cadena (P-F depende de P-D) porque comparten `app/precio/`. Las letras son provisionales. Al pegarlos en el plan, la letra final cambia también el nombre de las pruebas (`test_arreglos03_pa_...` pasa a `test_arreglos03_<letra>_...`) y la carpeta de evidencia (`docs/evidencia/arreglos-03/P-D/` pasa a `docs/evidencia/arreglos-03/<letra>/`).

## Estado de hoy, medido

Todo con `echo "<SELECT>" | bash docs/evidencia/arreglos-01/lee_prod.sh`, el 2026-11-13 04:15 UTC.

- Modo global `shadow` desde la config 51. Ningún cambio real desde el 3-nov (MX) y el 31-oct (US).
- `precio_cambio`: 53 filas. 37 reales, todas `confirmado` por observación; 16 virtuales. **Cero filas en `error` en toda la historia.**
  `SELECT platform, aplicado, es_reversa, estado, confirmado_por, count(*) FROM precio_cambio GROUP BY 1,2,3,4,5`
- El fusible nunca ha retenido nada: 0 mediciones con causa, 0 retenciones, 0 liberaciones. El botón «Soltar» nunca se ha usado en producción.
  `SELECT count(*) FROM precio_compuerta_medicion WHERE causa IS NOT NULL` → 0. `SELECT count(*) FROM precio_retencion` → 0. `SELECT * FROM precio_compuerta_liberacion` → sin filas.
- `corrida_id` vacío: 0 de 10,644 decisiones y 0 de 53 cambios. `verificacion`: 41 `cotizada`, 10,603 nulas, 0 `lineal`.
  `SELECT verificacion, count(*), count(corrida_id) FROM precio_decision GROUP BY 1`
- `precio_cap_amazon_mx/_us/_meli = 1000000` en las configs 50 y 51. `apply_quota_state` no tiene ningún motor `precio:*`.
  `SELECT id, settings->'precio_cap_meli' FROM config_version ORDER BY id DESC LIMIT 2` y `SELECT motor, count(*) FROM apply_quota_state GROUP BY 1`

## P-A. Código muerto de E4 y `corrida_id`

**Qué pasa hoy.** `decidir(lineal=...)` no lo pasa nadie, y `SinCotizador` no lo devuelve ningún adaptador. Dos guardas comparan una constante consigo misma. `corrida_id` nunca se llena, así que «revertir por corrida» revierte el día entero de la plataforma (`app/precio/cambios.py:765-767`).

**Forma elegida.**

Se borra, sin reemplazo:
- `lineal` en `decidir`, `_subir` y `_bajar` (`app/precio/reglas.py:211`, `:219-220`, `:311`, `:330`, `:355`, `:382-388`, `:435`, `:446`, `:473-479`, `:526`). En la rama `PideCotizacion` queda solo `freno = _frena_regla11(...)` y `return freno if freno is not None else salida`. `por_formula` desaparece y la decisión sale con `verificacion=None` (la corrida ya escribe `"cotizada"`, `app/precio/corrida.py:599-601`).
- `VERIFICACIONES` queda `frozenset({"cotizada"})` (`app/precio/tipos.py:131`).
- `SinCotizador` (`app/precio/puerto.py:180-187`, `:33`) y la unión del tipo de retorno de `Vitrina.cotizar` (`:261`).
- `FORMA_ESCRITURA_MELI`, `_FORMA_SELLADA` y `MeliWriteClient.exigir_sello` (`app/meli/write_client.py:50`, `:52`, `:141-153`, `:173-174`) y su llamada en `app/meli/precio_mercado.py:633-634`.
- `FORMA_PARCHE`, `FormaParcheSinSellar` y la guarda de `construir_cuerpo_parche` (`app/spapi/precio_write.py:54`, `:57`, `:82-83`, `:166-167`).
- El parámetro `sonda`, que solo existía para saltar `exigir_sello`: `MeliWriteClient.poner_precio` (`:165`), `MercadoMeli.escribir` (`app/meli/precio_mercado.py:620`, `:646-647`), `MercadoAmazon.escribir` (`app/spapi/precio_mercado.py:633`) y sus llamadas en `tools/precio_sonda.py:64`, `:139`, `:147`.

Se queda `EscrituraNoDisponible`: `MercadoAmazon.escribir` la sigue levantando si falla el armado del cuerpo (`app/spapi/precio_mercado.py:641-644`). El CHECK de la base que admite `'lineal'` también se queda: no hay filas y quitarlo pide una migración que no cambia nada.

`corrida_id` se **llena**. Uso del llamador:

```python
# correr (app/precio/corrida.py:1458): ya tiene corrida_id
_decidir_pendientes(conn, mercado, config, hoy, ahora, owner=..., freno_dias=..., cfg_id=cfg_id,
                    corrida_id=corrida_id, modo_global=..., modo_universo=...)
# _aplicar_pendientes ya recibe corrida_id (:1227)
cambios_mod.insertar_virtual(conn, did, lid, platform, decision, ahora, corrida_id=corrida_id)  # P-C.4 quita `ahora`
cambios_mod.aplicar(conn, mercado, did, ahora=ahora, corrida_id=corrida_id)
```

Firmas:

```python
def persistir_decision(conn, decision, *, listing_id, platform, product_id, canal, escenario_id,
                       fee_observation_id, cotizacion_id, config_version_id, corrida_id: int,
                       goal_id=None, ...) -> int          # obligatorio: un llamador olvidado falla
def _persistir_uno(..., cfg_id, corrida_id: int, lineas, errores, ...) -> tuple[int, int]
def _decidir_lectura(..., cfg_id, corrida_id: int, ...) -> tuple[int, int]
def _decidir_pendientes(..., cfg_id, corrida_id: int, modo_global, modo_universo) -> ...
def aplicar(conn, mercado, decision_id, *, ahora=None, corrida_id: int | None = None) -> ResultadoCambio
def insertar_virtual(conn, decision_id, listing_id, platform, decision, ahora, *, corrida_id: int) -> int
# P-A conserva `ahora` (todavía llena `enviado_at`); P-C.4 lo quita cuando `enviado_at` nace con now()

_SQL_PLAN_POR_CORRIDA = (
    _SQL_FILA_PLAN + " WHERE c.corrida_id = %s AND NOT c.es_reversa AND c.aplicado ORDER BY c.id"
)
```

`aplicar` lleva `None` por omisión porque 28 pruebas la llaman sola, sin corrida, y un cambio fuera de corrida es legítimo. `persistir_decision` e `insertar_virtual` solo los llama la corrida: ahí es obligatorio. `planear_reversa` con `SeleccionPorCorrida` sigue validando que la corrida exista (`_SQL_CORRIDA_DE`) y luego usa `_SQL_PLAN_POR_CORRIDA`. Las corridas viejas (sin `corrida_id`) dan un plan vacío; para ellas está `--fecha`.

**Otra forma descartada.** Borrar las columnas `corrida_id` y el selector `--corrida`. Es más resta, pero pide una migración que reescribe `precio_cambio_sella_transicion` (la fila inmutable nombra `NEW.corrida_id`, `migrations/0054_precio02_base.sql:770-782`) y quita una herramienta que el dueño ya tiene. Llenarla no toca la base: la columna, la llave foránea y el permiso de INSERT ya existen.

**Qué no se hace.** Juntar `_subir` y `_bajar` (otro bullet de E4) no está en este frente: no es código muerto y cambia el camino de dinero sin una falla que lo pida.

**Cruces.** Ninguno con ARREGLOS 01 ni TEMPORADA 01.

## P-B. F2: un error de base de una publicación tumba la plataforma, sin aviso

**Qué pasa hoy.** Todo `psycopg.Error` sale de la corrida, salvo `goal_reemplazado` (`app/precio/corrida.py:632-643`, `:744-745`, `:1331-1332`, `:1344-1345`). La fila de la corrida queda `abierta` y el aviso solo sale del camino feliz (`:1508`). Ya pasó: MeLi abortó 22 corridas del 22 al 25-oct sin que nadie se enterara.

**Forma elegida.** Dos capas, cada una en su sitio.

1. Aislar. Un rechazo de la base por el dato de una fila es un error de esa publicación. La conexión está en autocommit (`:1431-1432`), así que un INSERT rechazado no deja una transacción rota.

```python
ERRORES_DE_FILA = (psycopg.IntegrityError, psycopg.DataError)  # CHECK, llave, único, número fuera de rango

# _persistir_uno
try:
    persistir_decision(...)
except ERRORES_DE_FILA as exc:
    if isinstance(exc, psycopg.errors.CheckViolation) and "goal_reemplazado" in str(exc):
        ...                       # igual que hoy: (0, 1)
    msg = scrub(f"listing={lid} {platform} rechazo_de_base: {exc.__class__.__name__}: {exc}")
    logger.error("corrida %s", msg); errores.append(msg)
    return 0, 0
except psycopg.Error:
    raise                         # la infra sigue abortando
```

Igual en `_decidir_lectura` (cae a la rama aislada que ya existe y deja `no_evaluado(escenario_incoherente)`), en `_aplicar_pendientes` para `insertar_virtual` y para `cambios.aplicar` (ahí además alimenta el corte: `cortacircuito.tras("error")`). `OperationalError`, `InterfaceError`, `ProgrammingError` y los demás siguen abortando: esos sí son de toda la plataforma.

2. Avisar. Lo que sí tumba la corrida avisa desde el CLI, que es el único lugar que ve cualquier caída (conexión, configuración, mercado, base). Eso incluye la apertura del mercado: si `_clientes_precio` levanta (por ejemplo, falta la llave de MeLi), `_abrir_plataforma` devuelve 1 y el lazo hace `continue` sin pasar por los tres `except`; sin aviso ahí, esa plataforma se queda sin precios todo el día en silencio, que es justo F2.

```python
# app/notifica.py
@dataclass(frozen=True)
class CorridaFallidaPrecio:
    platform: str
    dia: dt.date
    motivo: str                    # nombre de la clase del error; nunca su texto
    tipo: str = "corrida_fallida"

def aviso_precio_corrida_fallida(aviso: CorridaFallidaPrecio) -> str: ...
def avisar_corrida_fallida(platform: str, exc: BaseException) -> bool: ...  # jamás levanta

# app/cli_precio.py, en main_precio
except (mercados.ConfigInvalida, corrida_precios.ConfigInvalida) as exc:   # :161
    for p in plataformas: avisar_corrida_fallida(p, exc)
except Exception as exc:                                                  # :164
    avisar_corrida_fallida(platform, exc)
# y en la falla de conexión (:144-146), una vez por cada plataforma pedida
if isinstance(mercado_ctx, int):                                          # :153-155
    avisar_corrida_fallida(platform, ...)  # _abrir_plataforma (:47-52) no pudo armar los clientes
    rc = mercado_ctx; continue
```

Texto del aviso, en el estilo sin acentos de los demás avisos de precio:

```
[Orbit] precio: corrida fallida
plataforma: meli
dia: 2026-11-12
la corrida se cayo y no decidio ni aplico precios; el siguiente repaso lo vuelve a intentar
motivo: OperationalError
```

`LockOcupado` no avisa: otra corrida está trabajando. Una caída repetida avisa en cada repaso (hasta 6 al día): el riesgo que se arregla es el silencio de 4 días, y un aviso por repaso durante una caída real es aceptable.

**Otra forma descartada.** Envolver las líneas de precio del crontab con `cron-avisa`. Esas líneas no viven en `tools/despliegue/crontab-orbit.txt` (están en `docs/DEPLOY.md:895-917`), así que habría que mover la corrida de precios bajo `cron_orbit.sh`: es un cambio de instalación más grande para el mismo aviso. Tampoco se pone el aviso dentro de `correr`: no vería las caídas antes de abrir la corrida (configuración, conexión).

**Prueba.** Falla que ya pasó. Una por mitad: la reproducción `test_f2_...` (aislar) y una del CLI (avisar). Un trigger que levante sin código de error (`P0001`) seguiría abortando la plataforma, pero ahora con aviso: hoy todos los triggers de precio usan `check_violation` o `restrict_violation` (23 y 22 usos).

## P-C. F1, E1 y F5: un error que sí entró, y las horas falsas

**Qué pasa hoy.** Tras un error de red, la relectura inmediata ve el precio viejo (37 de 37 escrituras reales) y el cambio queda «sin efecto»: no cuenta para los 7 días y se vuelve a mover al día siguiente. El cierre del día siguiente solo mira `enviado` (`app/precio/cambios.py:986`), así que el error se queda para siempre. `enviado_at` y `readback_at` son la hora de arranque de la corrida (`:329`, `:432`, `:290-301`).

**Forma elegida (E1).** «Sin efecto» solo lo prueba la observación del día siguiente. Mientras no hay prueba, el error cuenta como cambio.

```python
# app/precio/tipos.py: la regla en un solo lugar
def cambio_sin_efecto(estado: str, confirmado_por: str | None) -> bool:
    """Un `error` que la observación del día siguiente encontró en el precio anterior."""
    return estado == "error" and confirmado_por == "observacion"
```

`cerrar_por_observacion(conn, vitrina, hoy)` (`app/precio/cambios.py:966-1011`):

```
abiertos = cambios reales (aplicado) de la plataforma, enviados antes de hoy, con
           estado = 'enviado'  O  (estado = 'error' Y confirmado_por IS NULL)
por cada uno: obs = vitrina.observado(unidad, despues_de=dia_envio, hasta=hoy)
  sin obs                      -> no se toca (intactos)
  enviado, obs == despues      -> confirmado / observacion            (como hoy)
  enviado, obs != despues      -> no_confirmado / observacion         (como hoy)
  error,   obs == despues      -> confirmado / observacion, ack = COALESCE(ack, {"origen": "observacion"})
  error,   obs == antes        -> sigue error, confirmado_por = observacion   (sin efecto probado)
  error,   otro precio         -> no se toca (no se adivina; cuenta como cambio)
cuenta = {"confirmado", "no_confirmado", "sin_efecto", "intactos"}
```

Usan `cambio_sin_efecto`:
- `fuentes.cambios_previos` (`app/precio/fuentes.py:360-414`): la consulta lee `confirmado_por` en vez de `error_code, readback_precio, readback_precio_currency`.
- `cambios._clasificar_plan` (`app/precio/cambios.py:711-725`): `_SQL_FILA_PLAN` (`:680-690`) cambia `c.error_code, c.readback_precio, c.readback_precio_currency` por `c.confirmado_por`; el `EXISTS` pasa del índice 15 al 13.

Se borra: `_readback` (`:217-239`), las tres relecturas de `_escribir_y_sellar` (`:289-290`, `:292`, `:296`, `:301`) y la regla «error con relectura igual al precio anterior» (`fuentes.py:397-404`, `cambios.py:721-724`). `_leer_vivo_suave` se queda solo para `_revertir_una` (`:831`), que no es relectura. `error_code = 'huerfana_sin_efecto'` deja de ser prueba de «sin efecto»: la huérfana quieta también se cierra con la observación del día siguiente. Las columnas `readback_*` se quedan como historia de las 37 filas viejas.

F5: `enviado_at` nace con `now()` de la base en los tres INSERT (`aplicar` `:419-432`, `_revertir_fila` `:541-553`, `insertar_virtual` `:1029-1040`). `insertar_virtual` pierde su parámetro `ahora`. `readback_at` desaparece con la relectura.

Migración `<n>_arreglos03_error_cierra_por_observacion.sql`:

```sql
-- sonda-aplicada: SELECT pg_get_constraintdef(oid) LIKE '%confirmado%' FROM pg_constraint WHERE conname = 'precio_cambio_error_code_solo_error'
ALTER TABLE precio_cambio DROP CONSTRAINT precio_cambio_error_code_solo_error;
ALTER TABLE precio_cambio ADD CONSTRAINT precio_cambio_error_code_solo_error
    CHECK (error_code IS NULL OR estado IN ('error', 'confirmado'));
-- precio_cambio_sella_transicion(): el texto de migrations/0054_precio02_base.sql:681-796
-- (función y COMMENT) con ('error', 'confirmado') en la lista de transiciones y en el mensaje.
```

La reversa restaura el CHECK de `migrations/0039_precio.sql:547-548` y la función de `0054:681-796` tal cual. Su cabecera dice que falla, con el error del CHECK, si ya hay cambios `confirmado` con `error_code` (un error que se confirmó): esos no se deshacen solos. El permiso alcanza: `app_decide` ya puede actualizar `estado`, `ack` y `confirmado_por` (`migrations/0039_precio.sql:1132-1134`).

Las pruebas de `tests/test_precio_corrida.py` arman su base con la lista fija `ORDEN59` (`:40-53`), que no lee la carpeta `migrations/`: la migración nueva se suma al final de esa lista (y de `ORDEN54`, `ORDEN58` u `ORDEN_M3` si otra prueba de precio necesita `error -> confirmado`). Sin eso, el cierre de un `error` revienta con `CheckViolation ... estado error -> confirmado fuera de la progresión sellada` y parece que el diseño está mal (lo comprobó el lector del plan con un prototipo sobre `ORDEN59`).

Prueba de la migración (instala: cambia un CHECK y un trigger en producción): `test_arreglos03_pc_migracion_y_su_reversa` en `tests/test_precio_migracion.py`, con `db_58` (`ORDEN58`). Tras la migración, la sonda da `True` y, dentro de una transacción que se deshace, un `error` con `error_code` pasa a `confirmado` con `confirmado_por = 'observacion'` y su `ack`; tras la reversa, la sonda da `False` y la misma transición vuelve a ser rechazada. Ninguna otra prueba ejecuta la reversa ni comprueba la sonda: la plantilla de `tests/test_despliegue_motor.py:362` solo aplica la carpeta por glob.

**Qué pasa los días sin observación (pregunta e).** El error queda abierto y cuenta para los 7 días: el lado seguro. Ese día la publicación tampoco se decide, porque la decisión exige el precio observado del día (es la misma tabla, `spapi_price_observation`, `app/spapi/precio_mercado.py:373-383`; en MeLi, `meli_publicacion_observation`, `app/meli/precio_mercado.py:317-325`). El freno de 7 días sí cuenta mientras tanto: `reglas.py:283-300` solo salta los `sin_efecto`, y un `error` con `confirmado_por` vacío no lo es. El cierre corre al arrancar cada repaso, antes de decidir, así que cuando llega la observación se cierra primero y se decide después: no se pierde un día por el orden. Un error que nunca ve su precio anterior ni el nuevo (alguien movió el precio a mano) queda en `error`; a los 7 días ya no frena nada y `freno_por_error` lo sigue contando como hoy.

**Otra forma descartada.** Guardar la observación del día siguiente en las columnas `readback_*`. No se puede: el trigger las sella una sola vez y la relectura inmediata ya las llenó. Un estado nuevo `sin_efecto` pide más vocabulario y más triggers que usar `confirmado_por` sobre un `error`, que el CHECK ya permite.

**Cruce.** Contradice a propósito la decisión S.4 de REPRICING 02 («un error con readback igual a `precio_antes` no consume cooldown», `plans/repricing-02.md:177`): sus pruebas (`tests/test_precio_corrida.py:4917-5181`) pasan a poner la observación del día siguiente en lugar de la relectura. Las de `tests/test_precio_write.py` que solo comprueban la relectura (`:351`, `:557`, `:699`, `:854`, `:879`) se borran o pierden esa aserción.

**Solo se comprueba en vivo.** Con todo en práctica no hay escrituras reales: no habrá un `error` real que cerrar ni un `enviado_at` real que mirar. En producción hay 0 errores en toda la historia. La prueba-real comprueba la migración y que el código desplegado ya no relee.

## P-D. F3 y F4: el fusible ve el paso a en vivo, y el tope de 50

**Qué pasa hoy.** `es_nueva` no sabe si la decisión anterior fue de práctica (`app/precio/compuerta.py:153-167`): el primer día en vivo todo lo acumulado en práctica sale junto sin contar como nuevo. El único freno de volumen es el fusible por universo (más de la mitad y al menos 10, `:261-272`): hoy pasan 62 + 29 = 91 cambios en un día en MX sin confirmación. `precio_cap_*` no lo lee nadie.

**Pregunta a: qué cuenta como «cambio».** Una decisión del día `subir` o `bajar`, con modo efectivo `live` (la que sí mueve el precio real), de cualquier universo de la plataforma (MX suma `fba` y `fbm`), que el dueño no ha soltado. Cuenta aunque ya se haya aplicado en una corrida anterior del mismo día. No cuentan las de práctica (son virtuales), ni `mantener`, ni `no_evaluado`, ni las reversas (son del dueño, con su propia herramienta). El conteo del día solo crece, porque hay una decisión por publicación y por día: el tope no se suelta solo en un repaso (la sospecha S3 no le aplica).

**Dónde se retiene y cómo se suelta.** Con la retención que ya existe: causa `movimiento_masivo`, una fila en `precio_retencion` por decisión pendiente y la medición del universo con su causa y su detalle. Se suelta con el botón «Soltar» de `/precios` (`POST /api/precios/compuerta/{corrida_id}/liberar`, `app/precio/liberaciones.py:55-86`). Lo soltado deja de contar, así que el repaso siguiente (cada 2 horas) aplica lo retenido de hoy. Si no se suelta, mañana el motor vuelve a decidir y vuelve a contar.

**Cómo se entera David.** Con el aviso de compuerta que ya existe (`app/notifica.py:1245-1268`, `_avisar_compuerta_precio` `:1853-1885`), uno por universo, causa y día, con el detalle «91 cambios de precio hoy en la plataforma; mas de 50 en un dia se confirman a mano». Residuos declarados:
- Si ese mismo día el universo ya avisó un `movimiento_masivo`, el sello `precio_medicion_un_aviso_por_dia` no deja mandar un segundo aviso. Pasa solo si se juntan más de 50 cambios nuevos después de soltar los primeros, el mismo día.
- Un universo cuyos cambios en vivo ya se aplicaron en una corrida anterior del mismo día también queda con medición `movimiento_masivo` si el tope se pasa después (por ejemplo, 30 de `fba` aplicados a las 13:10 y 25 de `fbm` a las 15:10: a las 15:10 `fba` cuenta 30 en vivo sin soltar). Su aviso dice «no se aplico nada» aunque esos cambios ya salieron.
- Soltar después del último repaso del día (23:10 UTC) no aplica nada ese día: al día siguiente se vuelve a decidir y a contar.
- `es_nueva` es simétrica: el día que se baja de en vivo a práctica, las intenciones también cuentan como nuevas para el fusible y pueden retener decisiones de práctica. En práctica no se mueve ningún precio real; el aviso puede pedir soltar algo que no hace falta.

**Forma elegida.** La regla vive en `evaluar`, junto al fusible. `_medir_compuerta` solo cuenta.

```python
# app/precio/compuerta.py
TOPE_CAMBIOS_DIA = 50  # docs/CONTEXTO.md:289-293: más de 50 cambios de precio al día se confirman

@dataclass(frozen=True)
class Intencion:
    decision_id: int; listing_id: int; canal: str; nueva: bool; liberada: bool
    en_vivo: bool                       # su modo efectivo es live: mueve el precio real

@dataclass(frozen=True)
class Anterior:
    resultado: str; motivo: str | None; goal_id: int | None
    modo: str                           # modo efectivo de la decisión previa

def es_nueva(anterior: Anterior | None, *, goal_id: int | None, modo: str) -> bool:
    if anterior is None or goal_id is None or anterior.goal_id is None:
        return True
    if anterior.goal_id != goal_id or anterior.modo != modo:   # F3: de práctica a en vivo es nueva
        return True
    ... (igual que hoy)

def cambios_en_vivo(por_canal: Mapping[str, Sequence[Intencion]]) -> int:
    """Cambios reales del día en la plataforma: suma sus universos, sin lo soltado."""
    return sum(1 for intenciones in por_canal.values() for i in intenciones
               if i.en_vivo and not i.liberada)

def evaluar(intenciones, *, medidas, derivas, config, cambios_dia: int) -> Veredicto:
    ... insumo_sistemico y movimiento_masivo del fusible, igual que hoy ...
    en_vivo = sum(1 for i in intenciones if i.en_vivo and not i.liberada)
    if cambios_dia > TOPE_CAMBIOS_DIA and en_vivo:
        return Retenida(causa="movimiento_masivo", medidas=medidas, nuevas=en_vivo, insumo=None,
                        detalle=f"{cambios_dia} cambios de precio hoy en la plataforma;"
                                f" mas de {TOPE_CAMBIOS_DIA} en un dia se confirman a mano")
    return Abierta(medidas=medidas, nuevas=nuevas)
```

`en_vivo`, `modo` y `cambios_dia` no llevan valor por omisión: las 21 llamadas que ya existen en `tests/test_precio_compuerta.py` a `Intencion(`, `Anterior(`, `es_nueva(` y `evaluar(` (`rg -c` da 21) pasan los campos nuevos explícitos.

En la retención por tope, `nuevas` lleva los cambios en vivo de ese universo. Así el aviso dice «30 de 59 quieren moverse hoy» y no «0 de 59» cuando son segundos escalones. Un universo sin cambios en vivo no se retiene ni avisa.

`app/precio/corrida.py`:
- `_SQL_MEDIR_HOY` (`:953-961`) y `_SQL_MEDIR_PREVIA` (`:963-979`) agregan `d.mode::text` al final, en la misma posición en las dos (índice 15). `_MODO = 15`. La cláusula `NOT EXISTS` de ARREGLOS 01 E.2 (`:969-977`) se queda igual, y `_INGRESO = (13, 14)` no se mueve.
- `_medir_compuerta` (`:1062-1170`) se parte en dos vueltas. La primera arma, por canal, las `Intencion` (con `en_vivo = fila[_MODO] == "live"` y `nueva = es_nueva(Anterior(..., modo=previa[_MODO]), goal_id=fila[5], modo=fila[_MODO])`) y los pares de insumos, como hoy. Luego `cambios_dia = cambios_en_vivo(intenciones_por_canal)`. La segunda llama `evaluar(..., cambios_dia=cambios_dia)` e inserta la medición de cada canal.

Las claves `precio_cap_*` se borran con la migración `<n>_arreglos03_sin_precio_cap.sql`:

```sql
-- sonda-aplicada: SELECT NOT (settings ? 'precio_cap_meli') AND pg_get_functiondef('apply_cap_de_config(text)'::regprocedure) NOT LIKE '%precio_cap_%' FROM config_version ORDER BY id DESC LIMIT 1
CREATE OR REPLACE FUNCTION apply_cap_de_config(...)   -- el texto de migrations/0002_apply.sql:455-491 (los 8 mapeos de Ads) con su COMMENT
INSERT INTO config_version (label, settings)
SELECT 'ARREGLOS 03: sin precio_cap_*',
       settings - 'precio_cap_amazon_mx' - 'precio_cap_amazon_us' - 'precio_cap_meli'
  FROM config_version ORDER BY id DESC LIMIT 1;
```

La reversa vuelve a crear la función con el texto de `migrations/0039_precio.sql:1149-1185` e inserta una config con las tres claves en `1000000`, el valor de hoy. `docs/DATABASE.md:991-994` y `docs/DEPLOY.md:1886-1888` (el comando de comprobación con `apply_cap_de_config('precio:*')`, que después de la migración devuelve NULL) se ponen al día. `docs/CHAT-CONTEXT.md:173` es historia y no se toca.

Prueba de la migración (escribe configuración de producción): `test_arreglos03_pd_migracion_sin_precio_cap_y_su_reversa` en `tests/test_precio_migracion.py`, con `db_58` (`ORDEN58` ya trae la función de la 0039). Inserta una `config_version` con las tres claves; tras la migración la sonda da `True`, `apply_cap_de_config('precio:meli')` es NULL y la config más nueva no trae las claves; tras la reversa la sonda da `False` y la función vuelve a dar `1000000`. Sin ella, una migración que fuera solo la cabecera y `SELECT 1;` pasaría todas las pruebas del bloque. El tope es una constante y no una clave nueva de configuración: es la regla del negocio de `docs/CONTEXTO.md`, y la decisión 3 borra justo las claves que nadie leía.

**Otra forma descartada.** Una causa nueva `tope_diario`. Da una etiqueta propia en pantalla, pero pide migrar el vocabulario en cinco lugares (dos CHECK, la vista `v_precio_decision_liberada`, el trigger `precio_retencion_coherente` y `_SQL_MEDIR_PREVIA`), más `app/etiquetas.py`. Con `movimiento_masivo` el botón, el aviso, la vista de liberadas y la exclusión de días retenidos de ARREGLOS 01 E.2 funcionan sin tocar nada; el detalle dice que fue el tope. También se descartó una función aparte que reescribe los veredictos después de `evaluar`: dejaría dos lugares que deciden retener.

**Medido (pregunta a).** En los últimos 60 días ningún día pasó de 50, ni contando práctica: el máximo fue 9 en MX y 4 en US; MeLi no tuvo ningún `subir` ni `bajar`.

```sql
SELECT platform, count(DISTINCT decision_date) dias, max(n) maximo, count(*) FILTER (WHERE n > 50) dias_mas_50,
       max(nl) max_en_vivo, count(*) FILTER (WHERE nl > 50) dias_en_vivo_mas_50
  FROM (SELECT platform, decision_date, count(*) n, count(*) FILTER (WHERE mode = 'live') nl
          FROM precio_decision WHERE decision_date >= current_date - 60 AND resultado IN ('subir', 'bajar')
         GROUP BY 1, 2) x GROUP BY 1;
-- amazon_mx|10|9|0|9|0
-- amazon_us|6|4|0|4|0
```

**Medido (pregunta c).** Si hoy se regresara a en vivo, saldrían juntas 6 publicaciones de US (1408, 1411, 1415, 1420, 1479 y 1534), de 83 medidas: las 2 que hoy suben en práctica y 4 en «mantener (7 días)» por un cambio virtual del 10-nov, que en vivo no frena. MX y MeLi: 0 (todo en tolerancia). Con el arreglo las 6 cuentan como nuevas, pero ni el fusible (6 es menos de 10 y de la mitad de 83) ni el tope (6 de 50) las retienen. F3 importa cuando la ola es grande, por ejemplo tras resembrar metas.

```sql
SELECT platform, canal, resultado, motivo, count(*) FROM precio_decision
 WHERE decision_date = '2026-11-12' GROUP BY 1,2,3,4 ORDER BY 1,2,3,4;
SELECT d.listing_id, d.resultado, d.motivo,
       (SELECT string_agg(c.created_at::date || ':' || c.aplicado || ':' || c.estado, ',' ORDER BY c.id)
          FROM precio_cambio c WHERE c.listing_id = d.listing_id AND c.platform = d.platform
           AND c.created_at > now() - interval '10 days') cambios
  FROM precio_decision d
 WHERE d.decision_date = '2026-11-12' AND d.platform = 'amazon_us' AND (d.resultado = 'subir' OR d.motivo = 'cooldown');
-- 1408|mantener|cooldown|2026-11-10:false:confirmado   (y 1411, 1420, 1479 igual)
-- 1415|subir|None|2026-11-12:false:confirmado          (y 1534 igual)
SELECT platform, canal, medidas, nuevas, causa FROM precio_compuerta_medicion WHERE fecha = '2026-11-12';
-- amazon_us|fbm|83|2|None ; amazon_mx|fba|121|0 ; amazon_mx|fbm|33|0 ; meli|meli|36|0
```

**Pregunta b: qué sirve en práctica.** La prueba-real corre en el contenedor de producción, con `en_prod.sh`, un guion puro contra el `evaluar` y el `es_nueva` desplegados (91 en vivo se retienen, 50 pasan, práctica a en vivo es nueva), más la sonda de la migración. Solo el día que regrese a en vivo se verá una retención real por tope y su aviso por Telegram.

Guion `docs/evidencia/arreglos-03/P-D/prueba.py` (probado hoy contra producción: sale 1 con `ImportError: cannot import name 'TOPE_CAMBIOS_DIA'`):

```python
from decimal import Decimal
from types import SimpleNamespace

from app.precio.compuerta import TOPE_CAMBIOS_DIA, Abierta, Anterior, Intencion, Retenida, es_nueva, evaluar

motor = SimpleNamespace(fusible_frac=Decimal("0.50"), fusible_min_movimientos=10,
                        insumo_salto_pct=Decimal("0.05"), insumo_salto_frac=Decimal("0.30"))

def lote(n, canal, *, nueva=True, en_vivo=True):
    return [Intencion(decision_id=i, listing_id=i, canal=canal, nueva=nueva, liberada=False, en_vivo=en_vivo)
            for i in range(n)]

assert TOPE_CAMBIOS_DIA == 50
assert isinstance(evaluar(lote(62, "fba"), medidas=125, derivas=[], config=motor, cambios_dia=91), Retenida)
assert isinstance(evaluar(lote(29, "fbm"), medidas=59, derivas=[], config=motor, cambios_dia=91), Retenida)
assert isinstance(evaluar(lote(50, "fba", nueva=False), medidas=125, derivas=[], config=motor, cambios_dia=50), Abierta)
assert es_nueva(Anterior("subir", None, 7, "shadow"), goal_id=7, modo="live")
assert not es_nueva(Anterior("subir", None, 7, "live"), goal_id=7, modo="live")
print("ok")
```

**Cruces.** ARREGLOS 01 E.2: la previa sigue sacando los días retenidos y no soltados; un día retenido por tope tampoco es línea base. Con F3 eso es lo correcto: si el primer día en vivo se retiene y no se suelta, al día siguiente la previa vuelve a ser la de práctica y la intención vuelve a contar como nueva.

## P-E. Decisión 4: en MeLi las variantes sin existencias no fijan el precio

**No entra al plan.** Espera que el dueño confirme la decisión 4 con lo medido (`docs/evidencia/arreglos-03/PENDIENTE-P-E.md`). Se conserva como diseño listo.

**Qué pasa hoy.** Con variantes, manda la de mayor costo más envío (`app/estimacion_repository.py:660-664`), tenga o no existencias. Además hay una segunda regla de «quién manda» que nadie llama: `puerto.miembro_que_manda` (`app/precio/puerto.py:310-334`), solo usada por dos pruebas.

**De dónde sale la existencia y qué tan fresca es (pregunta d).** De `meli_publicacion_observation.variantes` (`stock` por `variante_ext`), una observación diaria por publicación hacia las 11:30 UTC; 64 publicaciones observadas cada día del 8 al 12 de noviembre. La estimación de las 12:45 UTC, la que usa la corrida de las 13:10, ya ve la del día. Se compara por `variante_ext`, no por SKU, para que los 9 alias PEZ de `meli_sku_alias` no confundan.

```sql
SELECT metric_date, count(DISTINCT item_id) FROM meli_publicacion_observation
 WHERE metric_date >= current_date - 5 GROUP BY 1 ORDER BY 1;   -- 64 cada día
```

**Forma elegida.** La regla vive donde ya vive «quién manda»: al sembrar el escenario.

```python
# app/estimacion_repository.py
def quien_manda(cargas: Mapping[str, Decimal], sin_existencia: frozenset[str]) -> str:
    """SKU que fija el precio: mayor costo + envío entre los que tienen existencia;
    empate por SKU ascendente. Si ninguno tiene existencia, entre todos (la regla de hoy)."""
    candidatos = {s: c for s, c in cargas.items() if s not in sin_existencia} or dict(cargas)
    return min(candidatos, key=lambda s: (-candidatos[s], s))

_SQL_SIN_EXISTENCIA = """
SELECT m.seller_sku
  FROM v_precio_unidad_miembro m
  JOIN listing l ON l.id = m.listing_id AND l.platform = m.platform
 WHERE m.listing_id = %(listing_id)s AND m.platform = 'meli'
   AND (SELECT (v->>'stock')::int
          FROM (SELECT o.variantes FROM meli_publicacion_observation o
                 WHERE o.item_id = l.external_id AND o.metric_date = %(ayer)s
                 ORDER BY o.observed_at DESC LIMIT 1) o,
               jsonb_array_elements(o.variantes) v
         WHERE v->>'variante_ext' = m.variante_ext LIMIT 1) = 0
   AND (SELECT (v->>'stock')::int
          FROM (SELECT o.variantes FROM meli_publicacion_observation o
                 WHERE o.item_id = l.external_id AND o.metric_date = %(hoy)s
                 ORDER BY o.observed_at DESC LIMIT 1) o,
               jsonb_array_elements(o.variantes) v
         WHERE v->>'variante_ext' = m.variante_ext LIMIT 1) = 0
"""
# _sembrar_meli, en :660-664
sin_existencia = frozenset(r[0] for r in conn.execute(
    _SQL_SIN_EXISTENCIA,
    {"listing_id": listing_id, "ayer": valoracion_date - timedelta(days=1), "hoy": valoracion_date}))
sku = quien_manda({i["sku"]: _carga_de(i["resultado"]) for i in disponibles}, sin_existencia)
manda = next(i for i in disponibles if i["sku"] == sku)
```

Solo cuenta como «sin existencias» una variante con `stock = 0` conocido en la última observación de ayer **y** en la última de hoy. Una sola lectura en 0 no basta: una variante que se agotó hoy, o que vuelve a tener existencias, sigue fijando el precio. Sin observación de alguno de los dos días, o con una variante que no aparece, no se excluye a nadie: la regla de hoy, que da el precio más alto. Es el lado seguro. Las estimaciones que corren antes de la observación del día (hacia las 11:30 UTC) no ven la de hoy y usan la regla de siempre; ninguna de esas da escenarios disponibles (diseño de datos, «B-EST»). Si ninguna variante tiene existencias, se conserva la regla de hoy. Se sigue exigiendo que todas las variantes tengan cuenta (`len(disponibles) == len(infos)`): una variante sin costo frena la fila como hoy. Se borra `puerto.miembro_que_manda` con sus dos pruebas (`tests/test_precio02_contratos.py:187-205`).

La identidad del escenario no cambia de forma: si cambia la variante que manda, cambia su `sku_cost_id` y el escenario se resiembra; si cambia la existencia pero no quién manda, se reusa.

**Otra forma descartada.** Filtrar en el motor de precios (`app/meli/precio_mercado.py`). El motor lee la cuenta ya armada del escenario; filtrar ahí dejaría dos reglas de «quién manda» y una pantalla de margen que no coincide con la decisión.

**Riesgo declarado.** Medido por el adversario y recalculado aquí con la regla de los dos días: hoy 14 publicaciones tienen solo 2 unidades en la variante que fija el precio (con el arreglo). Si se venden y la variante queda en 0 dos días, la que fija el precio pasa a una más barata con existencias y el precio objetivo baja entre 8 y 28 % (en 9 de las 14, más de 10 %; la mayor, 1570, 27.7 %). El motor decide `bajar`. Si esa variante vuelve a tener existencias, la subida queda frenada por la regla de 7 días (`mantener` con motivo `cooldown`): hasta 7 días se vende al precio calculado para la variante barata. En 1586 son unos 63 MXN menos de contribución por unidad, que sigue positiva. El adversario contó 7 publicaciones, entre 10 y 26 %; su lista cabe en la de aquí, que además trae 1570, 1581 y 1587 y las 5 que bajan menos de 10 %.

```sql
WITH g AS (SELECT listing_id FROM precio_goal WHERE platform = 'meli' AND valid_from <= current_date
             AND (valid_to IS NULL OR valid_to > current_date)),
e AS (SELECT DISTINCT ON (e.listing_id) e.listing_id, e.componentes, e.contribucion
        FROM estimacion_escenario e JOIN g USING (listing_id)
       WHERE e.platform = 'meli' AND e.estado = 'disponible' ORDER BY e.listing_id, e.observed_at DESC, e.id DESC),
base AS (SELECT e.listing_id, e.contribucion,
                (SELECT sum((c->>'importe_normalizado')::numeric) FROM jsonb_array_elements(e.componentes) c
                  WHERE c->>'nombre' IN ('costo_normalizado', 'logistica')) carga_manda FROM e),
otros AS (SELECT e.listing_id, substr(c->>'nombre', 9) sku, (c->>'importe_normalizado')::numeric contrib
            FROM e, jsonb_array_elements(e.componentes) c WHERE c->>'nombre' LIKE 'miembro:%'),
ms AS (SELECT m.listing_id, m.seller_sku, m.variante_ext,
              CASE WHEN o.sku IS NULL THEN b.carga_manda ELSE b.carga_manda + b.contribucion - o.contrib END carga,
              (o.sku IS NULL) es_manda
         FROM v_precio_unidad_miembro m JOIN base b USING (listing_id)
         LEFT JOIN otros o ON o.listing_id = m.listing_id AND o.sku = m.seller_sku WHERE m.platform = 'meli'),
dia AS (SELECT max(metric_date) hoy FROM meli_publicacion_observation),
st AS (SELECT ms.*,
              (SELECT (v->>'stock')::int FROM listing l
                 JOIN LATERAL (SELECT ob.variantes FROM meli_publicacion_observation ob
                                WHERE ob.item_id = l.external_id AND ob.metric_date = (SELECT hoy - 1 FROM dia)
                                ORDER BY ob.observed_at DESC LIMIT 1) ob ON true,
                      jsonb_array_elements(ob.variantes) v
                WHERE l.id = ms.listing_id AND l.platform = 'meli' AND v->>'variante_ext' = ms.variante_ext LIMIT 1) stock_ayer,
              (SELECT (v->>'stock')::int FROM listing l
                 JOIN LATERAL (SELECT ob.variantes FROM meli_publicacion_observation ob
                                WHERE ob.item_id = l.external_id AND ob.metric_date = (SELECT hoy FROM dia)
                                ORDER BY ob.observed_at DESC LIMIT 1) ob ON true,
                      jsonb_array_elements(ob.variantes) v
                WHERE l.id = ms.listing_id AND l.platform = 'meli' AND v->>'variante_ext' = ms.variante_ext LIMIT 1) stock_hoy
         FROM ms),
sx AS (SELECT st.*, (stock_ayer = 0 AND stock_hoy = 0) IS TRUE sin FROM st),
por AS (SELECT listing_id,
               bool_or(es_manda AND sin) manda_sin, bool_or(NOT sin) hay_con,
               max(carga) carga_hoy, max(carga) FILTER (WHERE NOT sin) carga_nueva
          FROM sx GROUP BY listing_id)
, r AS (SELECT listing_id, seller_sku, carga, stock_hoy,
               row_number() OVER (PARTITION BY listing_id ORDER BY carga DESC, seller_sku) rn
          FROM sx WHERE NOT sin AND (stock_hoy IS NULL OR stock_hoy > 0)),
x AS (SELECT a.listing_id, a.stock_hoy stock_manda, a.carga, (SELECT max(b.carga) FROM r b WHERE b.listing_id = a.listing_id AND b.rn > 1) carga_sig
        FROM r a WHERE a.rn = 1)
SELECT count(*), round(min(100 * (1 - carga_sig / carga)), 1), round(max(100 * (1 - carga_sig / carga)), 1),
       count(*) FILTER (WHERE carga_sig <= 0.9 * carga),
       string_agg(listing_id::text, ',' ORDER BY carga_sig / carga)
  FROM x WHERE stock_manda = 2 AND carga_sig < carga;
-- 14|8.4|27.7|9|1570,1581,1586,1587,1573,1576,1572,1589,1566,1593,1568,1601,1596,1592
```

**Medido.** 45 metas de MeLi; 36 con miembros vigentes (las otras 9 no tienen miembros activos y hoy salen `oferta_ausente`). De las 36, 30 tienen variantes, 0 tienen todas sus variantes sin existencias y 0 tienen una variante sin dato. **25 cambian de variante que manda**: en 22 hay un empate exacto de costo más envío entre la variante agotada y otra con existencias (el escenario se vuelve a sembrar con el mismo precio; en 1564 y 1566 los costos son idénticos, 304.65 y 459.29, así que tampoco salta el fusible por insumo) y en **3** (1568, 1574 y 1594) el precio objetivo baja entre 1.04 % y 1.62 %. La versión anterior de este diseño contaba 3 «cambian»: su sexta columna comparaba la carga más alta de todas las variantes contra la más alta de las que tienen existencias, que no depende de cuál manda y no llega a 0 después del arreglo. Las cifras dan lo mismo con la regla de los dos días (11 y 12 de noviembre) que con la última observación. Con comisión fija 0, el precio objetivo baja en la misma proporción que el costo más envío (`objetivo.precio_estrella`, `app/precio/objetivo.py:91-115`). Ojo: el ejemplo de la sospecha S2, MLM2158744542, no cambia: ya manda su única variante con existencias.

Consulta (la que copia P-E.3 a `docs/evidencia/arreglos-03/P-E/cambia-manda.sql`; «hoy» es el último día con observación y «ayer» el anterior):

```sql
WITH g AS (SELECT listing_id FROM precio_goal WHERE platform = 'meli' AND valid_from <= current_date
             AND (valid_to IS NULL OR valid_to > current_date)),
e AS (SELECT DISTINCT ON (e.listing_id) e.listing_id, e.componentes, e.contribucion
        FROM estimacion_escenario e JOIN g USING (listing_id)
       WHERE e.platform = 'meli' AND e.estado = 'disponible' ORDER BY e.listing_id, e.observed_at DESC, e.id DESC),
base AS (SELECT e.listing_id, e.contribucion,
                (SELECT sum((c->>'importe_normalizado')::numeric) FROM jsonb_array_elements(e.componentes) c
                  WHERE c->>'nombre' IN ('costo_normalizado', 'logistica')) carga_manda FROM e),
otros AS (SELECT e.listing_id, substr(c->>'nombre', 9) sku, (c->>'importe_normalizado')::numeric contrib
            FROM e, jsonb_array_elements(e.componentes) c WHERE c->>'nombre' LIKE 'miembro:%'),
ms AS (SELECT m.listing_id, m.seller_sku, m.variante_ext,
              CASE WHEN o.sku IS NULL THEN b.carga_manda ELSE b.carga_manda + b.contribucion - o.contrib END carga,
              (o.sku IS NULL) es_manda
         FROM v_precio_unidad_miembro m JOIN base b USING (listing_id)
         LEFT JOIN otros o ON o.listing_id = m.listing_id AND o.sku = m.seller_sku WHERE m.platform = 'meli'),
dia AS (SELECT max(metric_date) hoy FROM meli_publicacion_observation),
st AS (SELECT ms.*,
              (SELECT (v->>'stock')::int FROM listing l
                 JOIN LATERAL (SELECT ob.variantes FROM meli_publicacion_observation ob
                                WHERE ob.item_id = l.external_id AND ob.metric_date = (SELECT hoy - 1 FROM dia)
                                ORDER BY ob.observed_at DESC LIMIT 1) ob ON true,
                      jsonb_array_elements(ob.variantes) v
                WHERE l.id = ms.listing_id AND l.platform = 'meli' AND v->>'variante_ext' = ms.variante_ext LIMIT 1) stock_ayer,
              (SELECT (v->>'stock')::int FROM listing l
                 JOIN LATERAL (SELECT ob.variantes FROM meli_publicacion_observation ob
                                WHERE ob.item_id = l.external_id AND ob.metric_date = (SELECT hoy FROM dia)
                                ORDER BY ob.observed_at DESC LIMIT 1) ob ON true,
                      jsonb_array_elements(ob.variantes) v
                WHERE l.id = ms.listing_id AND l.platform = 'meli' AND v->>'variante_ext' = ms.variante_ext LIMIT 1) stock_hoy
         FROM ms),
sx AS (SELECT st.*, (stock_ayer = 0 AND stock_hoy = 0) IS TRUE sin FROM st),
por AS (SELECT listing_id,
               bool_or(es_manda AND sin) manda_sin, bool_or(NOT sin) hay_con,
               max(carga) carga_hoy, max(carga) FILTER (WHERE NOT sin) carga_nueva
          FROM sx GROUP BY listing_id)
SELECT (SELECT hoy FROM dia) hoy,
       count(*) FILTER (WHERE manda_sin AND hay_con) cambia_manda,
       count(*) FILTER (WHERE manda_sin AND hay_con AND carga_nueva < carga_hoy) baja,
       round(min(100 * (1 - carga_nueva / carga_hoy)) FILTER (WHERE manda_sin AND hay_con AND carga_nueva < carga_hoy), 2),
       round(max(100 * (1 - carga_nueva / carga_hoy)) FILTER (WHERE manda_sin AND hay_con AND carga_nueva < carga_hoy), 2),
       string_agg(listing_id::text, ',') FILTER (WHERE manda_sin AND hay_con AND carga_nueva < carga_hoy)
  FROM por;
-- 2026-11-12|25|3|1.04|1.62|1568,1574,1594
```

La consulta de la versión anterior daba `36|30|0|0|0|3|1.04|1.62|1568,1574,1594` (metas, con variantes, `n_manda <> 1`, sin carga nueva, con variante sin dato, y su sexta columna, la que se leía mal).

**Prueba-real en práctica.** La estimación de MeLi corre igual en práctica, así que el efecto se verá en producción en la primera estimación con el arreglo (12:45 UTC): la consulta «Medido» debe dar 0 en `cambia_manda`. Esa lectura queda programada con `at` para las 13:00 UTC siguientes al despliegue (fila P-E.3) y no detiene el bloque. La prueba-real del bloque es el guion puro contra `quien_manda` desplegado (hoy sale 1 con `ImportError`):

```python
from decimal import Decimal

from app.estimacion_repository import quien_manda

cargas = {"NH-A": Decimal("380.50"), "SET-B": Decimal("384.50")}
assert quien_manda(cargas, frozenset({"SET-B"})) == "NH-A"
assert quien_manda(cargas, frozenset({"NH-A", "SET-B"})) == "SET-B"
assert quien_manda(cargas, frozenset()) == "SET-B"
print("ok")
```

**Cruces.** `estimacion.md` E1 (pasar Amazon por el camino de MeLi) no se hace aquí; con un solo miembro `quien_manda` da lo mismo que hoy, así que no lo estorba.

## P-F. `estimacion.md` F2: el plan de metas nunca calcula el precio objetivo de MeLi

**Qué pasa hoy.** `siembra._referencia` parte la comisión con `derivar_ref_fijo` (`app/precio/siembra.py:311-312`), que solo reconoce el `ReferralFee` de Amazon. En MeLi toda fila sale `referencia_sin_derivar` y el freno «el precio salta más de 25 %» nunca actúa.

**Forma elegida.** El plan lee la comisión ya partida que el escenario guardó al sembrarse, con la regla correcta de cada plataforma (`app/estimacion_repository.py:496-500`). No vuelve a partir nada.

```python
# app/precio/siembra.py
@dataclass(frozen=True)
class LecturaHoy:
    m_pct: Decimal | None
    p_actual: Decimal | None
    moneda: str | None
    componentes: list | None
    fee_total: Decimal | None
    fee_quoted: Decimal | None
    fee_variable: Decimal | None    # del escenario, ya partido por la regla de su plataforma
    fee_fijo: Decimal | None
    fee_estado: str | None          # sin fee_detalles

# _referencia, en lugar de :311-312
if lectura.fee_variable is None or lectura.fee_fijo is None or not lectura.fee_quoted:
    raise ValueError("escenario sin comision partida")       # cae a referencia_sin_derivar, como hoy
ref = lectura.fee_variable / lectura.fee_quoted
fijo = lectura.fee_fijo
```

`app/precio/goals_write.py`: `_SQL_ESCENARIO` (`:363-368`) agrega `fee_variable, fee_fijo`; `_SQL_FEE` (`:370-373`) deja de leer `fee_details`; el armado de `LecturaHoy` (`:432-441`) pasa los dos campos nuevos. Se borran `_detalle_fee` de `siembra.py` (`:264-274`), su mención en el docstring (`:3`) y la importación de `derivar_ref_fijo` (`:33`). Para Amazon el resultado es idéntico: `partir_fees` usa el mismo predicado que `derivar_ref_fijo` y hay un candado que lo fija (`app/estimacion_amazon.py:57-69`).

**Otra forma descartada.** Elegir la regla por plataforma dentro de `siembra` (`partir_fees_meli` para MeLi). Sería la cuarta copia de «parte variable de la comisión» (`estimacion.md` E3 ya cuenta tres) y `siembra` tendría que conocer a MeLi. Leer lo ya partido deja la regla donde se siembra.

**Medido.** Las columnas están llenas en todos los escenarios vigentes: Amazon MX 342, US 111, MeLi 52, ninguna nula.

```sql
SELECT platform, count(*), count(*) FILTER (WHERE fee_variable IS NULL), count(*) FILTER (WHERE fee_fijo IS NULL)
  FROM (SELECT DISTINCT ON (listing_id, platform) listing_id, platform, fee_variable, fee_fijo
          FROM estimacion_escenario WHERE estado = 'disponible'
         ORDER BY listing_id, platform, observed_at DESC) x GROUP BY 1;
-- amazon_mx|342|0|0 ; amazon_us|111|0|0 ; meli|52|0|0
```

Con el arreglo, las 45 metas de MeLi tienen precio objetivo y **ninguna** pasa el freno de 25 %: van de −1.6 % a 0.0 % del precio actual (mediana 0.0 %), porque las metas se sembraron con el margen del día. `margen_goal_pct` ya es fracción (0.3354), no porcentaje.

```sql
WITH g AS (SELECT listing_id, margen_goal_pct FROM precio_goal WHERE platform = 'meli'
             AND valid_from <= current_date AND (valid_to IS NULL OR valid_to > current_date)),
e AS (SELECT DISTINCT ON (e.listing_id) e.listing_id, e.componentes, e.fee_variable, e.fee_fijo,
             f.quoted_price_amount q, o.price_amount p
        FROM estimacion_escenario e JOIN g USING (listing_id)
        JOIN estimacion_fee_observation f ON f.id = e.fee_observation_id
        JOIN estimacion_oferta_observation o ON o.id = e.oferta_observation_id
       WHERE e.platform = 'meli' AND e.estado = 'disponible' ORDER BY e.listing_id, e.observed_at DESC),
c AS (SELECT e.listing_id, e.p, e.q, e.fee_variable, e.fee_fijo, g.margen_goal_pct goal,
             (SELECT sum((x->>'importe_normalizado')::numeric) FILTER (WHERE x->>'nombre' = 'costo_normalizado') FROM jsonb_array_elements(e.componentes) x) costo,
             (SELECT sum((x->>'importe_normalizado')::numeric) FILTER (WHERE x->>'nombre' = 'logistica') FROM jsonb_array_elements(e.componentes) x) envio,
             (SELECT sum((x->>'importe_normalizado')::numeric) FILTER (WHERE x->>'nombre' = 'isr') FROM jsonb_array_elements(e.componentes) x) isr,
             (SELECT sum((x->>'importe_normalizado')::numeric) FILTER (WHERE x->>'nombre' = 'ingreso_normalizado') FROM jsonb_array_elements(e.componentes) x) ingreso,
             (SELECT sum((x->>'importe_normalizado')::numeric) FILTER (WHERE x->>'nombre' = 'precio_bruto') FROM jsonb_array_elements(e.componentes) x) bruto
        FROM e JOIN g USING (listing_id)),
s AS (SELECT listing_id, p, (costo + fee_fijo + envio) / ((1 - isr / ingreso - goal) / (bruto / ingreso) - fee_variable / q) pstar FROM c)
SELECT count(*), count(pstar), count(*) FILTER (WHERE pstar > 1.25 * p OR pstar < 0.75 * p),
       round(min(100 * (pstar / p - 1)), 1), round(percentile_cont(0.5) WITHIN GROUP (ORDER BY 100 * (pstar / p - 1))::numeric, 1),
       round(max(100 * (pstar / p - 1)), 1)
  FROM s;
-- 45|45|0|-1.6|0.0|0.0
```

**Prueba-real en práctica.** El plan de metas no corre solo en producción. La prueba-real es el guion puro contra `_referencia` desplegado con la forma real de producción (989.00 MXN, `SaleFee` 192.86); hoy sale 1 con `TypeError: LecturaHoy.__init__() got an unexpected keyword argument 'fee_variable'`. El guion está en la fila P-F.3.

**Cruces.** Ninguno con ARREGLOS 01. `derivar_ref_fijo` sigue en el camino de Amazon (`app/spapi/precio_mercado.py:245`, `:471`); juntar las copias es `estimacion.md` E3, fuera de este frente.

## Cruces con otros planes

- **ARREGLOS 01.** E.2 (`_SQL_MEDIR_PREVIA`) se respeta en P-D. L (`insumo_atrasado`) y R (`Vitrina.unidades`) no se tocan. Los bloques T a X no tocan `app/precio/`.
- **TEMPORADA 01.** Su bloque I cambia las vistas de margen de Ads (`v_target_margen_plataforma`, `v_margen_producto`, `v_margen_familia`). Ningún archivo de `app/precio/` ni de la estimación de precios lee esas vistas (`rg` sobre `app/precio`, `app/estimacion_*.py`, `app/meli/precio_mercado.py`, `app/spapi/precio_mercado.py`: cero). El único archivo compartido es `tools/despliegue/comun.sh` `migraciones_de` (`:196-202`) y sus listas en `tests/test_despliegue_motor.py`: TEMPORADA I.1 agrega `temporada01` en la misma línea donde ARREGLOS 03 necesita `arreglos03` para que se apliquen las migraciones de P-C y P-D. Parche para el bloque base de ARREGLOS 03, sobre lo que haya en master al abrir el PR: al grupo de etiquetas de `migraciones_de` se le suma `|arreglos03` al final, sin quitar ninguna de las que ya estén (`arreglos01`; `arreglos02`, que ARREGLOS 02 A.2 pone antes de que este plan arranque; y `temporada01` si ya entró). Con todas en master queda:

```diff
-    | grep -E '_(auto03|orbit07|claridad01|arreglos01|arreglos02|temporada01)_' | grep -v '_reversa_' | LC_ALL=C sort); do
+    | grep -E '_(auto03|orbit07|claridad01|arreglos01|arreglos02|temporada01|arreglos03)_' | grep -v '_reversa_' | LC_ALL=C sort); do
```

  Sin `temporada01` si TEMPORADA 01 todavía no entró. La prueba de BASE.1 lista una migración de `arreglos01`, una de `arreglos02` y una de `arreglos03`: si el parche borra una etiqueta ajena, sale en rojo. Si este frente no tiene un bloque base que lo haga, P-C y P-D no aplican su migración en el despliegue y no avisan.
- **ARREGLOS 02.** No toca `app/precio/`. Sin cruce.
- **ARREGLOS 04 y los bloques D de este plan.** P-B agrega a `app/notifica.py` el aviso `corrida_fallida` (`FRASEO_TIPO_PRECIO`, `notifica_precio` y funciones nuevas). En el mismo archivo D-K cambia el resumen del ciclo y agrega `aviso_reputacion`, y ARREGLOS 04 W-G cambia `aviso_corte_encolado` y `notifica_encola`. Son funciones distintas: se cambia por contenido y al rebasar se conservan los cambios del otro.

## Lo que queda sin resolver

- Sin escrituras reales (todo en práctica), no se puede ver en producción un error real cerrado por observación, una hora real de envío ni una retención real por tope con su aviso. Se verá el primer día en vivo.
- El aviso de compuerta se sella una vez por universo, causa y día: una segunda retención por tope el mismo día, después de soltar la primera, no manda otro aviso.
- Un trigger que levante sin código de error (`P0001`) sigue abortando la plataforma; ahora avisa.
