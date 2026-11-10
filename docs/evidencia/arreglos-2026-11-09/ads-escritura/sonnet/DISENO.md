# DISENO: grupo ads-escritura (I-H1, E-H1 = F-H2, E-H3, F-H1, I-H2, I-H4)

Código leído: copia `orbit-master` (master `3c841f4`). Solo lectura; no se tocó producción. Las rutas `app/...` son relativas a esa copia. No corrí pruebas del repo ni sondas nuevas; la evidencia de las sondas es la de los reportes I, E y F.

Resumen de decisiones de forma:

| ID | Arreglo (una línea) | Dónde queda el invariante |
|---|---|---|
| I-H1 | Rechazar en el guardado cualquier cap > `CAP_MAX` (INTEGER de Postgres) | Validación de borde en `config_write.proxima_config`; la columna INTEGER sigue siendo el límite duro |
| E-H1 | Columna `ad_entity_state.estructura_sync_at`, que solo escribe el sync de estructura; la guarda y Salud la leen | Estructura: `GRANT UPDATE` por columna a `app_decide` NO la incluye, así que el aplicador no puede escribirla |
| E-H3 | `claves_bloqueadas` incluye las hojas con bid sin sellar; el ciclo las salta | Validación en la fase de decisión, reusando el mecanismo de clave en vuelo que ya existe |
| F-H1 | `reconcilia_bids` no reenvía el PUT si el modo efectivo no es live o ya hay una decisión de bid más nueva de esa hoja | Validación pre-HTTP dentro de `reconcilia_bids` |
| I-H2 | `_vigila_uno` trata el lote `desarmado` como ya pausado (misma rama que `pausado`) | Validación en el vigía; el estado `desarmado` ya es terminal en la fábrica |
| I-H4 | Tabla única de topes por moneda en `goals.py` junto a `DEFAULTS_POR_MONEDA`, aplicada en los bordes de creación | Validación de borde, nunca en lectores (no retroactiva) |

---

## I-H1. Un cap diario >= 2,147,483,648 tumba la fase de apply

### Causa raíz
El primer lugar donde el sistema se equivoca es el guardado: `app/config_write.py:487-497` solo revisa `cap < 0` y escribe `str(cap)` en `config_version`. El tipo del body (`app/api_write.py:253`, `Annotated[int, Field(ge=0)]`) no tiene máximo. El valor viaja intacto hasta `apply._cap_de_config` (`app/apply.py:330-348`, `int(valor)` sin límite) y revienta en el INSERT a `apply_quota_state.cap INTEGER` (`migrations/0001_initial.sql:823`; el trigger `apply_cap_de_config` también hace `::INTEGER`, `migrations/0039_precio.sql:1175`). La excepción sube al `except Exception` de `app/cycle.py:3560` y aborta la fase entera (incluye `libera_vencidos`). El docstring de `config_write` ya promete que "lo que el motor rechazaría... jamás llega a una fila"; ese tope es el único caso que el motor rechaza y el guardado no.

### Uso primero
- Pantalla/API: `POST /api/ads-optimizer/settings/amazon_mx` con `caps={"bid": 3000000000}` responde 422 con "cap bid debe ser <= 2147483647" (hoy 200).
- CLI/otros llamadores de `config_write.guarda_config`: reciben `SettingsInvalido`, igual que con un cap negativo.
- Operador que quiere "sin tope": escribe 2147483647 (o el 1,000,000 actual); ambos caben.

### Forma
```python
# app/apply.py, junto a KINDS_QUOTA (:312): UNA fuente del límite de la columna.
CAP_MAX = 2_147_483_647  # apply_quota_state.cap INTEGER; el trigger de 0039 castea a INTEGER

# app/config_write.py:487-497 (bucle de caps), tras `if cap < 0`:
#     if cap > CAP_MAX:
#         raise SettingsInvalido(f"cap {kind} debe ser <= {CAP_MAX}, llego {cap}")
```
`config_write` ya importa `KINDS_QUOTA` de `app.apply` (`config_write.py:47`), así que importa `CAP_MAX` por la misma línea. No se toca `api_write.py:253`: así la regla vive en un solo sitio (la cascada de `SettingsInvalido` ya da 422 en `api_write.py:531`) y no hay dos números que mantener sincronizados.

Invariante: el límite duro queda en la columna `INTEGER` (estructura); el guardado solo traduce ese límite a un 422 con mensaje. No cambio el tipo a BIGINT: habría que cambiar también el trigger de 0039 y las 4 rutas de consumo para permitir un valor que nadie necesita (hoy 1,000,000 ya no limita nada).

### Datos que ya existen
Los caps de producción están en 1,000,000 (HALLAZGOS); no hay config con valor fuera de rango, así que no hay reparación de datos. Si alguien guardó un valor gigante antes de desplegar, el ciclo falla hoy; la salida es guardar una config nueva con un cap válido (la pantalla ya lo permite).

### Cambio de comportamiento en vivo
Ninguno en el motor. Solo cambia lo que se acepta al guardar. Deshacer: revertir el commit; sin migración.

### Prueba
Dinero (topes). Una prueba pura: `config_write.proxima_config(actual, "amazon_mx", caps={"bid": 3_000_000_000})` debe lanzar `SettingsInvalido`; falla hoy (la sonda `I_cap_gigante.py` imprime "proxima_config acepta") y pasa con el arreglo. Un caso, sin base.

### Alternativas
- Columna `BIGINT` + trigger y consumidores en bigint: pierde, cambia 4 rutas de dinero para aceptar un valor sin uso real.
- Capturar el error en `_cap_de_config` y tratar el valor como "sin tope": pierde, disfraza config rota de cap infinito, que es lo que el propio docstring de `_cap_de_config` (`apply.py:333-347`) prohíbe (regla 3).
- Poner `le=CAP_MAX` en pydantic además: pierde por duplicar la regla y porque el CLI no pasa por pydantic.

### Tamaño
2 archivos (`app/apply.py`, `app/config_write.py`) + 1 prueba. Una unidad verificable.

---

## E-H1 = F-H2. La guarda de 48 h de estructura lee una columna que también escribe el aplicador

### Causa raíz
`_SQL_SYNC_PLATAFORMA` (`app/optimizer/windows.py:358`) toma `max(ad_entity_state.synced_at)` de toda la plataforma. Esa columna la pisan también `apply._SQL_ACTUALIZA_CACHE` (`app/apply.py:647-648`), `apply_cola._SQL_CACHE_ESTADO` (`:309-310`), `apply_producto` (`:65-66`) y `apply_harvest_reconciliacion` (`:120-121`). Una sola escritura del motor hace que `guarda_plataforma` (`windows.py:565`, uso en `:589`) vea "fresco". El mismo SQL lo importan `app/api.py:55,214` y `app/api_dashboard.py:115,1163`, por eso Salud muestra la hora del aplicador (evidencia `salud.json`: `amazon_us.synced_at = 08:40:05Z`).

No cambio las escrituras del aplicador: `synced_at` tiene otros lectores que sí quieren "última vez que alguien observó esta fila" (`app/propuestas_campana.py:161`, `app/jev_catalogo.py:315`, y `migrations/0079_auto03_vista_impulso.sql:52`, `s.synced_at > u.retirado_el`). Quitarle el `now()` al aplicador rompería esa vista. La causa es de lectura: la guarda mide una columna con dos dueños (split ownership). El arreglo le da a la guarda una columna con un solo dueño.

### Uso primero
- Ciclo: `windows.guarda_plataforma(conn, platform, ahora=...)` sin cambio de firma; ahora el skip `guarda='synced_at'` ocurre cuando el sync de estructura no corre 48 h, aunque el motor haya aplicado un bid ayer.
- Salud (`/api/ads-optimizer/status`, `app/api.py:214`; dashboard `app/api_dashboard.py:1163`): el campo `synced_at` pasa a ser la hora del sync de estructura de verdad (MX y US dejan de diferir por lo que aplicó el motor). Clave JSON sin renombrar.
- Operador: el texto "Estructura sincronizada hace > 48h" (`api_dashboard.py:323`) vuelve a ser cierto.

### Forma
```sql
-- migrations/0083_estructura_sync_at.sql
ALTER TABLE ad_entity_state ADD COLUMN estructura_sync_at TIMESTAMPTZ;
UPDATE ad_entity_state SET estructura_sync_at = synced_at;   -- punto de partida honesto
COMMENT ON COLUMN ad_entity_state.estructura_sync_at IS
  'Instante del ultimo sync de ESTRUCTURA (app.ads.structure). Solo lo escribe app_ingest; '
  'app_decide no tiene UPDATE sobre esta columna (0002:699 es por columna). La guarda de 48h la lee.';
-- Sin GRANT nuevo: app_ingest ya tiene INSERT, UPDATE de tabla (0001:1481);
-- app_decide conserva GRANT UPDATE (current_bid, status, synced_at) y NO incluye la nueva.
-- migrations/0083_reversa_estructura_sync_at.sql: ALTER TABLE ad_entity_state DROP COLUMN estructura_sync_at;
```
```python
# app/ads/structure.py:199-212 _SQL_UPSERT_STATE: agregar estructura_sync_at al INSERT (now())
#   y al DO UPDATE SET (estructura_sync_at = now()).
# app/ads/structure.py:223-231 _SQL_MARCAR_ARCHIVADOS: SET status = %s, synced_at = now(),
#   estructura_sync_at = now()   (viene del payload explicito, es sync de estructura).
# app/optimizer/windows.py:358
_SQL_SYNC_PLATAFORMA = """
SELECT max(s.estructura_sync_at)
  FROM ad_entity_state s JOIN ad_entity e ON e.id = s.ad_entity_id
 WHERE e.platform = %s::platform
"""
```
Las demás funciones (`guarda_plataforma`, `salta_por_sync`) no cambian: ya tratan `None` como "sin filas" (`windows.py:572-576`). Los tres lectores comparten el mismo SQL, así que se arreglan juntos.

Invariante: el aplicador no puede escribir la columna (permisos por columna: estructura). Que la guarda lea esa columna es convención de un solo SQL compartido (borde). Nullable a propósito: no obligo a tocar los INSERT de `tools/bench_orbit07_proteccion.py`; una fila sin sello simplemente no cuenta en el `max`.

### Datos que ya existen
El backfill copia `synced_at`. Producción hoy está sana (max = mediana = 2026-11-09 06:45:55 en MX y US, HALLAZGOS), así que el backfill no hereda contaminación significativa. Paso aparte antes de aplicar: leer `SELECT e.platform, max(s.synced_at), percentile_disc(0.5) WITHIN GROUP (ORDER BY s.synced_at) FROM ad_entity_state s JOIN ad_entity e ON e.id=s.ad_entity_id GROUP BY 1`; si la mediana va días atrás del max, el backfill debe usar la mediana por plataforma. Lo aplica otro agente con plan.

### Cambio de comportamiento en vivo
La guarda ahora puede saltar una plataforma que hoy no saltaba (solo si el sync de estructura lleva >48 h muerto; hoy no lo está). Es el comportamiento documentado ("dato stale ES alarma"). Se enciende al desplegar; no hay interruptor. Deshacer: revertir código + la migración de reversa (el código viejo vuelve a leer `synced_at`).
Orden de deploy: la migración antes del contenedor nuevo (inferido de `tools/despliegue/*`; no lo leí), porque el SQL nuevo falla si la columna no existe y la guarda cae en excepción del ciclo.

### Prueba
Escribir datos de producción / dinero indirecto (decide con estructura vieja). UNA prueba contra la base de pruebas: sembrar 3590 filas con `estructura_sync_at` de hace 5 días, ejecutar `apply._SQL_ACTUALIZA_CACHE` sobre una fila, y comprobar que `guarda_plataforma` devuelve `MotivoSkip(guarda='synced_at')`. Hoy devuelve `None` (sonda `E_guarda_synced_at.py`, "con 1 bid aplicado ayer -> None"). Es la versión con base de esa sonda, que usa conexión falsa y por eso no sirve como prueba permanente.

### Alternativas
- Medir con `ingest_run` (`source='amazon_ads_structure_v2'`, `ok`): no usa columna nueva, pero una corrida cubre ambas plataformas y no dice cuál escribió filas, y una corrida `ok` con 0 filas de una plataforma pasaría la guarda. Pierde por precisión.
- Dejar de escribir `synced_at` en el aplicador: rompe la vista `v_anuncio_regresado` y los lectores de frescura por entidad.
- Usar `percentile_disc(0.5)` en vez de `max`: sin migración, pero la mediana sigue moviéndose con aplicaciones masivas (harvest) y es una heurística, no un dueño único.

### Tamaño
5 archivos: migración y reversa, `app/ads/structure.py`, `app/optimizer/windows.py` (más `salud.html`/textos si se quiere aclarar la etiqueta), 1 prueba. Una unidad verificable.

---

## E-H3. Un bid sin sellar deja que el ciclo siguiente recorte otra vez la misma hoja

### Causa raíz
El ciclo decide antes de reconciliar: `_fase_lecturas` corre la decisión en TX2 (`app/cycle.py:3355-3395`, llamada en `:3760`) y solo después `_fase_apply` ejecuta `reconcilia_bids` (`:3501`) y `aplica_bids` (`:3502`). La trayectoria de la política solo ve cambios con `verify_ok IS TRUE` (`migrations/0067_bids02_campana_ajuste.sql:77`), y el intento sin sellar (`apply_attempt.finished_at IS NULL`, `apply.py:705-714`) todavía no lo es. Nadie en la decisión mira ese rastro. El mecanismo correcto ya existe para otros efectos en vuelo: `claves_bloqueadas` (`app/apply_cola.py:409-420`, SQL `:156-162`) alimenta el gate "clave en vuelo" de la hoja (`cycle.py:1906-1922`), pero solo cubre `apply_queue`, no el ledger de bids.

### Uso primero
- Ciclo N: el PUT de bid queda ambiguo (sin sello). Ciclo N+1: `claves_bloqueadas` trae `(ad_entity_id, "bid_sin_sello", None)`; `_procesa_decisora` salta la hoja con motivo `bid_sin_sello` (aparece en `skips_entidad` y en el dashboard como "Bid sin confirmar: se reconcilia antes de decidir"). En el mismo ciclo `reconcilia_bids` sella la fila. Ciclo N+2: la política ve el cambio verificado y aplica su espera de 7 días normal.
- Operador: ve el motivo en los skips en lugar de un segundo recorte silencioso.
- Costo: una hoja con intento ambiguo pierde un día de decisión (el único camino sin segundo recorte; 0 a 4 decisiones de bid por día hoy).

### Forma
```python
# app/apply.py (junto a _SQL_INTENTOS_BID_SIN_SELLO :705): mismo predicado, una sola definicion
_SQL_ENTIDADES_BID_SIN_SELLO = """
SELECT d.ad_entity_id
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id
  JOIN optimizer_cycle oc ON oc.id = d.cycle_id
 WHERE a.finished_at IS NULL AND a.tipo = 'normal' AND d.kind = 'bid'
   AND oc.platform = %s::platform
"""
# (en la implementacion, _SQL_INTENTOS_BID_SIN_SELLO y esta consulta comparten el FROM/WHERE por constante)

# app/apply_cola.py: claves_bloqueadas (:409) une las entidades de apply._SQL_ENTIDADES_BID_SIN_SELLO
#   como (ad_entity_id, "bid_sin_sello", None) al set que ya devuelve. Pseudocodigo:
#   claves = {fila de _SQL_CLAVES_BLOQUEADAS}
#   claves |= {(e, "bid_sin_sello", None) for (e,) in conn.execute(apply._SQL_ENTIDADES_BID_SIN_SELLO, (platform,))}
#   return claves

# app/cycle.py: MOTIVO_BID_SIN_SELLO = "bid_sin_sello"
#   en _procesa_decisora, ANTES del chequeo de clave_pausa (:1906):
#       if (entidad_id, "bid_sin_sello", None) in bloqueadas:
#           contadores.skips_entidad[MOTIVO_BID_SIN_SELLO] += 1; tick(); return
# app/api_dashboard.py:293 (diccionario de etiquetas de skips): agregar la etiqueta.
```
No se agrega capa ni función de lectura nueva: es una rama más de un conjunto que el ciclo ya pide una vez por plataforma (`cycle.py:3379`, dentro del snapshot REPEATABLE READ de TX2). Los cortes de términos (`_procesa_grupo`, `:2746`) filtran por `(grupo_id, "term_cut", term)` y no se ven afectados por la familia nueva.

Invariante: validación en la fase de decisión (no hay estructura que lo exprese; el estado "sin sello" es el rastro de crash ya declarado). La idempotencia viene de que el gate lee el ledger: dos corridas del ciclo hacen lo mismo.

### Datos que ya existen
0 intentos sin sellar en producción (HALLAZGOS). Sin reparación.

### Cambio de comportamiento en vivo
Solo reduce decisiones: salta hojas con bid sin sellar. No escribe nada nuevo a Amazon. Sin interruptor; deshacer = revertir commit. Riesgo residual: filas sin sellar que quedan mientras la plataforma está en shadow/off (la reconciliación solo corre en live, `cycle.py:3497`) bloquearían decisiones shadow de esas hojas; hoy hay 0 y F-H1 sella esas filas sin HTTP al volver a live.

### Prueba
Dinero. Una prueba con base: sembrar `apply_attempt` tipo `normal` sin `finished_at` para un bid de la hoja X y comprobar que `claves_bloqueadas` contiene `(X, "bid_sin_sello", None)` y que el ciclo no emite decisión `bid` nueva para X. Falla hoy (conjunto vacío; el escenario del reporte E-H3 exige exactamente esa siembra).

### Alternativas
- Reordenar: reconciliar antes de decidir. Estructuralmente distinta y más "limpia", pero obliga a construir el `Aplicador` con credenciales, lease y cliente antes de TX2 y a mover la fase de apply fuera del manejo de errores fail-closed del ciclo (`cycle.py:3440-3570`); en shadow/off no hay aplicador con cliente. Pierde por tamaño y riesgo.
- Que la trayectoria de la política cuente intentos sin sellar como cambios: pierde, no se sabe si Amazon lo aplicó; la política operaría con un hecho desconocido.
- Cooldown propio en `goals.en_cooldown`: el cooldown mide `verify_ok IS TRUE` en ventana de 7 días; mezclar "en vuelo" con "verificado" rompe su contrato (`goals.py:584-594`).

### Tamaño
4 archivos (`app/apply.py`, `app/apply_cola.py`, `app/cycle.py`, `app/api_dashboard.py`) + 1 prueba. Una unidad. Va junto con F-H1 en el mismo PR si se quiere (comparten `reconcilia_bids`), pero son separables.

---

## F-H1. `reconcilia_bids` reenvía el bid viejo sin mirar modo ni decisiones más nuevas

### Causa raíz
`reconcilia_bids` (`app/apply.py:717-822`) tiene un solo filtro previo al HTTP: `gate_ancestros` (`:752`). En la rama de divergencia (`:787-818`) crea fila de ledger y llama `_reintento_divergente` (`:825+`), que hace el PUT. La consulta de filas (`:705-714`) no tiene tope de edad ni filtro por decisiones posteriores. Para comparar: `aplica_bids` resuelve `modo_efectivo` por decisión (`:1362`, función `:1268-1308`) y las reconciliaciones de negatives y harvest también (`apply_harvest_reconciliacion.py:420,577`). Nota de causa: la lectura GET que confirma `bid_leido == new_value` (`:781`) es un hecho de Amazon, no una escritura, y no necesita guarda; el problema está solo en el PUT del reintento.

### Uso primero
- Llamador: `cycle.py:3501` pasa lo que ya tiene: `apply.reconcilia_bids(conn, aplicador, platform, escalera_global=modo.modo)`.
- Operador: pasa la meta a shadow/off, o una hoja con intento sin sello ya tiene una decisión de bid más nueva; al día siguiente la fila se sella con `fallo:reintento_no_vigente` y no sale ningún PUT. Aparece en `bids_reconciliados.fallidos`.
- Dos sitios de llamada reales: el único `cycle.py:3501` y `tests/test_apply.py:1251,1275`, que hoy llaman sin el argumento.

### Forma
```python
# app/apply.py
RESULTADO_REINTENTO_NO_VIGENTE = "fallo:reintento_no_vigente"

_SQL_BID_MAS_NUEVO = """
SELECT EXISTS (SELECT 1 FROM decision d2
                WHERE d2.ad_entity_id = %s AND d2.kind = 'bid' AND d2.id > %s)
"""

def reconcilia_bids(conn, aplicador, platform, *, escalera_global: str) -> tuple[int, int]:
    ...  # sin cambio hasta la rama de divergencia
    # En la rama "divergencia con tope disponible" (antes de _ledger/_reintento_divergente):
    #   if not _reintento_vigente(conn, aplicador, ad_entity_id, decision_id, escalera_global):
    #       -> la fila de la divergencia ya quedo sellada arriba (fallo:divergencia_readback);
    #          contar fallida y `continue`, SIN fila nueva de ledger ni HTTP.

def _reintento_vigente(conn, aplicador, ad_entity_id, decision_id, escalera_global) -> bool:
    """True solo si la hoja sigue en live (modo efectivo, la misma regla que aplica_bids)
    y ninguna decision de bid posterior la reemplazo."""
    if aplicador.modo_efectivo_de_entidad(conn, ad_entity_id, escalera_global=escalera_global) != "live":
        return False
    return not conn.execute(_SQL_BID_MAS_NUEVO, (ad_entity_id, decision_id)).fetchone()[0]

# Aplicador: extraer el cuerpo de modo_efectivo (:1268) a modo_efectivo_de_entidad(conn, ad_entity_id, *, escalera_global)
#   (el cuerpo solo usa decision.ad_entity_id, :1279-1285). modo_efectivo(conn, decision, ...) queda como
#   delegado de una linea para los llamadores y pruebas existentes.
```
Dos reglas, un solo punto. Reconocer que `modo_efectivo_de_entidad` es una extracción, no una segunda implementación: la lógica sigue en un cuerpo.

Invariante: validación pre-HTTP; el ledger y el readback ya impiden doble escritura. Idempotencia: si el proceso muere después de sellar la divergencia y antes de decidir el reintento, la fila queda sellada y el próximo ciclo no la reprocesa (`finished_at` ya está); no hay reintento perdido peligroso porque la política vuelve a decidir la hoja. Si muere a medias del reintento, aplica la semántica actual (fila nueva sin sello = rastro; con E-H3 la hoja se salta ese día).

### Datos que ya existen
0 intentos sin sellar hoy. Sin reparación. Filas sin sello futuras de otras épocas se sellan sin HTTP con el resultado nuevo.

### Cambio de comportamiento en vivo
Quita un camino que escribe a Amazon con dinero real (el reintento del bid viejo) cuando la meta no es live o hay una decisión posterior. En el caso normal (live, sin decisión posterior) el comportamiento es idéntico. Se enciende al desplegar; deshacer = revertir commit.

### Prueba
Dinero. La sonda `F/F1_reconcilia_bids_ignora_goal.py` ya tiene el montaje (conexión falsa + `httpx.MockTransport`): se vuelve prueba con la aserción "PUTs enviados == []" para goal `shadow`, `off`, `enabled=False`, y para el caso Amazon=0.80 con decisión posterior. Hoy envía `{'keywordId':'KW1','bid':1.5}` en los cuatro casos.

### Alternativas
- Tope de edad (p. ej. no reintentar filas de más de 24 h): más simple, pero no cubre "la meta pasó a shadow ayer" ni "D2 entró hoy" y agrega un umbral que alguien tiene que justificar. Pierde.
- Quitar el reintento divergente y dejar solo confirmar/sellar: estructuralmente distinta y la más segura, pero cambia el contrato de ADV-04 (el reintento bajo tope-3 fue decisión de la revisión adversaria) y deja bids que sí debían reintentarse. No la recomiendo sin decisión del dueño.

### Tamaño
1 archivo (`app/apply.py`) + `cycle.py:3501` (una línea) + ajuste de `tests/test_apply.py:1251,1275` + 1 prueba. Una unidad.

---

## I-H2. «PAUSAR GRUPO» sobre un lote de impulso hace fallar a `impulso-vigia` cada día

### Causa raíz
En `app/impulso_io.py:973-976` (`_vigila_uno`, rama `veredicto.accion == "pausar"`) la condición `estado_lote is None or estado_lote[0] != "pausado"` manda a `pausador.pausa(lote)` también a un lote `desarmado`. `pausa` → `fc.pausar_lote` → `_cambia_estado_lote` lanza `Abortar("lote ... desarmado: nunca revive")` (`tools/fabrica_campanas.py:1717-1727`), que `_lote_no_pausable` convierte en `ErrorImpulso(409)` (`impulso_io.py:187-199`) y el bucle del vigía atrapa y descarta (`:1162`). La rama `graduar` de la misma función ya sabe que `desarmado` es terminal ("Desarmado no revive", `:1000-1008`); la rama `pausar` quedó sin ese caso. Ese estado nace del botón «PAUSAR GRUPO» (`app/fabrica_web.py:720`, `fc._desarmar`), que `fabrica_web.lotes` (`:632-639`) también muestra para lotes `impulso-*`.

### Uso primero
- Vigía: con el lote `desarmado` y veredicto "pausar" (p. ej. `sin_impresiones` al día 14), `_vigila_uno` verifica en Amazon con `pausador.pausa_huerfanas(lote)` (lee y pausa solo las campañas que sigan ENABLED), guarda la lectura con `hecho='pausado'` y el impulso sale de `_SQL_VIVOS`. Día 0 deja de fallar; no más 409 diarios.
- Dueño: el producto vuelve a poder impulsarse (`_falla_si_hay_vivo`, `impulso_io.py:714` ya no ve un vivo).
- `impulso-vigia` deja de salir 1 por este motivo (`app/cli_bids.py:137-143`).

### Forma
```python
# app/impulso_io.py ~:973 (sin funciones ni tipos nuevos)
#   estado_lote = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()
#   if estado_lote is None or estado_lote[0] not in ("pausado", "desarmado"):
#       pausador.pausa(lote); UPDATE ... WHERE estado='applied'   # igual que hoy
#   else:
#       # pausado o desarmado: ya hay pausa propia; el dueno pudo reencender en consola
#       # (G7) -> pausa_huerfanas verifica contra Amazon antes de anotar "pausado".
#       pausador.pausa_huerfanas(lote)
#   hecho = "pausado"
```
`pausa_huerfanas` no depende del estado del lote (lee `fabrica_lote_paso` y Amazon, `impulso_io.py:224-264`) y levanta si alguna queda ENABLED, así que "nunca anotar pausado sin verificar" (G7) se conserva. El `UPDATE fabrica_lote SET estado='pausado'` sigue con `WHERE estado='applied'`: un `desarmado` no se degrada a `pausado`.

Invariante: validación en el vigía; `desarmado` ya es terminal en la fábrica (`_cambia_estado_lote`, `_desarmar:1837`). Idempotencia: corre dos veces → la segunda ve `impulso_lectura` con ese `corte` y devuelve `ya[0]` (`impulso_io.py:965-969`).

### Datos que ya existen
"No medido" en producción. Si existe un impulso vivo con lote `desarmado`, el próximo `impulso-vigia` ya lo cierra solo con el arreglo; no hace falta reparación. Lectura de comprobación (para quien tenga acceso con plan): `SELECT i.id, l.estado FROM impulso i JOIN fabrica_lote l USING (lote) LEFT JOIN impulso_lectura r ON r.impulso_id=i.id AND r.es_final WHERE r.impulso_id IS NULL AND l.estado='desarmado'`.

### Cambio de comportamiento en vivo
Para un lote desarmado el vigía puede hacer lecturas y, solo si el dueño reencendió campañas en consola, PUTs de PAUSED con readback (el mismo camino que hoy usa para `pausado`). Reduce gasto, no lo crea. Sin interruptor; deshacer = revertir commit.

### Prueba
Falla ya vista en el diseño pero sin dinero perdido y sin caso de los 4 (no escribe producción nuevo ni toca dinero: pausa lo ya pausado). Basta comprobar que corre: la sonda `I_vigia_desarmado.py` imprime "dia 0 atendido" y "lecturas guardadas: 1" con el arreglo (hoy FALLA 409 y 0 lecturas). No se agrega prueba permanente.

### Alternativas
- Ocultar «PAUSAR GRUPO» para lotes `impulso-*` en `fabrica_web.lotes`: la UI deja de provocar el estado, pero el CLI (`fc._desarmar`) y lotes ya desarmados siguen rompiendo al vigía, y no cierra el impulso atascado. Pierde (sirve como complemento, no como arreglo).
- Que `desarmar` de un lote con impulso cierre el impulso con una lectura final: toca dos módulos (`fabrica_web`, `impulso_io`) y acopla la fábrica al impulso. Pierde por superficie.

### Tamaño
1 archivo (`app/impulso_io.py`, ~3 líneas). Una unidad.

---

## I-H4. Ninguna cifra de dinero tecleada por una persona tiene tope superior

### Causa raíz
Los bordes validan solo `> 0` o un máximo de formato: presupuesto de fábrica `<= 9999999999.99` (`app/api_fabrica.py:113-125`; `fabrica_plan.valida_parametros` `:360-390` solo exige > 0 y >= bid), cambio de presupuesto de campaña `gt=0` (`app/api_write.py:675`; `planea_ajuste`, `app/campana_ajustes.py:143-152`), tope de impulso `<= 9999999999.9999` (`api_fabrica.py:455-468`), techo del goal `gt=0` (`api_write.py:214`; `goals_write.py:217-226`), target manual `gt=0` (`api_write.py:251`; `config_write.py:447`). No hay en el código ninguna cifra "razonable" contra la cual comparar, salvo `DEFAULTS_POR_MONEDA` (`app/optimizer/goals.py:111-114`, piso/techo de bid por moneda), que ya es la tabla por moneda del repo.

### Uso primero
- `POST /api/ads-optimizer/campana-ajuste/aplicar` con presupuesto 5000 MXN sobre una campaña de 100: 422 "presupuesto diario 5000 MXN supera el tope de 2000 MXN" (la vista previa `planea_ajuste` ya lo rechaza, así la persona lo ve antes de confirmar).
- `POST /api/fabrica/plan` / `crear` con `budget` de un rol por encima del tope: 422 en el mismo borde que hoy valida el bid contra `[piso, techo]`.
- `POST /api/fabrica/impulso/plan` con `tope_monto=9999999999`: `PlanInvalido` desde `_plan_producto` (`impulso_io.py:340`).
- `POST .../goals/{id}` con `bid_ceiling=4500`: `GoalInvalido`.
- `POST .../settings/{platform}` con `target_manual_pct=100000`: `SettingsInvalido`.

### Forma
Una sola tabla, junto a `DEFAULTS_POR_MONEDA`, y una sola función; cada borde la llama y traduce `ValueError` a su error propio.
```python
# app/optimizer/goals.py (al lado de DEFAULTS_POR_MONEDA :111)
@dataclass(frozen=True)
class TopesHumanos:
    presupuesto_diario: Decimal   # por campana
    tope_impulso: Decimal         # gasto total del aprendizaje
    bid_techo: Decimal            # bid_ceiling / harvest_default_bid de un goal

TOPES_HUMANOS_POR_MONEDA: dict[str, TopesHumanos] = {
    "MXN": TopesHumanos(Decimal("2000"), Decimal("5000"), Decimal("135")),
    "USD": TopesHumanos(Decimal("100"),  Decimal("250"),  Decimal("7.50")),
}   # VALORES PROPUESTOS: los confirma el dueño (pregunta abajo)
TARGET_MANUAL_MAX = Decimal("100")  # ACoS manual de plataforma, en %

def exige_bajo_tope(concepto: Literal["presupuesto_diario","tope_impulso","bid_techo"],
                    monto: Decimal, moneda: str) -> None:
    """ValueError con el monto, el tope y la moneda si lo supera; moneda sin fila = ValueError
    (mismo trato que DEFAULTS_POR_MONEDA: no se inventan numeros)."""
    raise NotImplementedError
```
Llamadas (solo en bordes de CREACIÓN o EDICIÓN humana, jamás en lectores ni en `plan_desde_json`/`_valida_plan_v2`):
- `app/campana_ajustes.py:143-152`, dentro de `planea_ajuste` tras el `quantize`: `exige_bajo_tope("presupuesto_diario", monto, vigente.moneda or PLATAFORMAS_MONEDA[plataforma])`. Es el punto común de CLI y API.
- `app/api_fabrica.py:226` (validador de la solicitud) y `tools/fabrica_campanas.py:587,612` (CLI): un bucle que llama `exige_bajo_tope("presupuesto_diario", p.budget, moneda)` por rol. NO en `fp.valida_parametros`: esa función también corre al releer lotes persistidos (`fabrica_plan.py:536,664,810`) y sería un candado retroactivo (precedente: el docstring de `valida_banda_manual`, `fabrica_plan.py:264-269`).
- `app/impulso_io.py:340`: `if tope_monto is not None: exige_bajo_tope("tope_impulso", tope_monto, moneda)`.
- `app/goals_write.py:217-226`: sobre `ceiling` y `bid_harvest` efectivos, con la moneda efectiva del goal.
- `app/config_write.py:447`: `target_manual_pct > TARGET_MANUAL_MAX` → `SettingsInvalido`.

Invariante: validación de borde (el CHECK de base no puede conocer una cifra por moneda cambiable por el dueño). Los lectores del motor no cambian, para que un valor ya guardado no tumbe el ciclo.

Idempotencia: son validaciones puras; repetir una petición rechazada da el mismo rechazo.

### Datos que ya existen
Ninguna fila se reescribe ni se invalida: el tope solo aplica a escrituras nuevas. Paso previo (lectura, otro agente): comprobar que ningún valor vigente hoy (presupuestos de campañas ENABLED, goals, `ads_target_acos_pct_*` manual) ya excede la tabla propuesta; si alguno la excede, subir el tope o aceptar que esa edición exigirá un valor menor la próxima vez. Aplicar la tabla con el máximo observado × 2 como piso de decisión evita rechazar ediciones legítimas.

### Cambio de comportamiento en vivo
No cambia lo que el motor escribe solo; reduce lo que una persona puede mandar. Sin interruptor (la tabla es el interruptor: subir el valor y redesplegar). Deshacer = revertir commit.

### Prueba
Dinero. UNA prueba pura: `planea_ajuste(vigente_100_MXN, CambiarPresupuesto(Decimal("5000")), ...)` lanza `ValueError`. Falla hoy (la sonda `I_entradas_dinero.py`: "ACEPTA planea presupuesto=1e25"). Los otros bordes se comprueban con la misma sonda, sin pruebas permanentes extra.

### Alternativas
- Doble confirmación cuando el monto supera N veces el vigente (flujo con segundo literal): cubre el "cero de más" sin cifras absolutas, pero agrega estado y UI en tres pantallas y no cubre altas sin vigente (fábrica, impulso). Pierde por tamaño; es el complemento natural si el dueño quiere más.
- Topes relativos al presupuesto o margen del producto (sin cifras absolutas): más "inteligente", pero depende de datos que pueden faltar (regla 3) y de lectores nuevos. Pierde.
- Repartir las cifras por módulo (cada borde con su constante): es lo que evita esta tabla; deriva en 5 listas que se desincronizan. Pierde.

### Tamaño
8 archivos tocados con ~3 líneas cada uno (`goals.py`, `campana_ajustes.py`, `api_fabrica.py`, `fabrica_campanas.py`, `impulso_io.py`, `goals_write.py`, `config_write.py`, + 1 prueba). Cabe en una unidad verificable porque la lógica es una función y las llamadas son homogéneas; si el revisor prefiere partir, hacerlo por moneda de riesgo: primero presupuestos e impulso (gastan solos desde el día uno), después techo de bid y target.

---

## Orden propuesto, dependencias, decisiones del dueño, riesgos

### Orden de unidades
1. **I-H1** (ALTO, chico, sin dependencias): un cap gigante ya hoy aborta cada ciclo.
2. **F-H1 + E-H3** (mismo PR si cabe, tocan `reconcilia_bids`/`claves_bloqueadas`): cierran las dos rutas de doble escritura del bid ambiguo. F-H1 primero en el commit (no depende de nada); E-H3 después. E-H3 depende de que la reconciliación siga corriendo a diario (existe).
3. **I-H2** (independiente, 3 líneas).
4. **E-H1 = F-H2** (migración; requiere paso de lectura previo y que otro agente aplique la migración antes del contenedor).
5. **I-H4** al final: es el único que necesita decisión de cifras del dueño; sus bordes no dependen de las demás.

Dependencias reales: ninguna fuerte. E-H3 se apoya en F-H1 solo para que las filas viejas sin sello se sellen sin HTTP al volver a live.

### Decisiones que solo puede tomar el dueño (con recomendación)
1. ¿Qué topes por moneda para presupuesto diario por campaña, tope de impulso y techo de bid? Recomiendo MXN 2,000 / 5,000 / 135 y USD 100 / 250 / 7.50 (el impulso por defecto es 350 MXN / 36 USD y el techo por defecto 45 MXN / 2.50 USD, `goals.py:111-114,812-830`), tras comprobar los valores vigentes.
2. ¿ACoS manual de plataforma máximo en 100%? Recomiendo sí: arriba de 100% el gasto supera la venta en cualquier caso.
3. ¿Se acepta que una hoja con intento de bid sin confirmar pierda un día de decisión (E-H3)? Recomiendo sí: es el costo mínimo de no recortar dos veces.
4. En F-H1: ¿se conserva el reintento divergente bajo tope-3 de ADV-04, o se elimina? Recomiendo conservarlo con la guarda de modo y decisión más nueva.

### Riesgos
- E-H1: el backfill hereda `synced_at`; si el sync llevara días muerto al migrar, la guarda no dispararía hasta 48 h después de la primera corrida real. Mitigado con la lectura previa (mediana vs max).
- E-H1: nadie más debe escribir `estructura_sync_at`; los permisos por columna lo impiden para `app_decide`, pero cualquier migración futura que dé `GRANT UPDATE` de tabla completa a ese rol lo reabriría (agregar una línea al COMMENT, ya incluida).
- E-H3: filas sin sello en shadow/off bloquean decisiones de esas hojas hasta volver a live (hoy 0 filas).
- I-H4: un tope mal calibrado bloquea una edición legítima; el remedio es subir la tabla y redesplegar. Por eso va en bordes y no en lectores.
- Hallazgos cuya causa raíz resultó distinta a la descrita: ninguno. Matices: E-H3 y F-H1 comparten el ledger pero tienen causas separadas (decide antes de reconciliar / reenvío sin guarda); E-H1 no se arregla quitando la escritura del aplicador porque `synced_at` tiene otros lectores legítimos (`v_anuncio_regresado`).
