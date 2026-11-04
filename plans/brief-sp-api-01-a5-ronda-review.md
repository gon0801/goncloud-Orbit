# Brief para Muse: SP-API 01 A.5 — ronda de correcciones (review del lead)

Rama `feat/sp-api-01-a5`, HEAD `7b7dc54`, PR #246 abierto (CI verde, CodeRabbit
pasado). **Commits nuevos encima; no rebases ni fuerces la rama** (rompe el PR y
Muse y el lead comparten checkout). UNA sola ronda: aquí está todo, no habrá una
segunda. No se reabre ninguna decisión de diseño: 0036 y su justificación quedan.

Evidencia de la review: mi pase + un reviewer fresco + auditoría de mutación (29
mutantes: 8 mordidos, **21 sobrevivieron**). Por eso varias correcciones son de
test, no de código: el verde actual no prueba lo que el reporte afirma.

## Regla de esta ronda (la más importante)

Cada corrección lleva su test **en rojo primero contra el código actual**, y el
reporte debe listar **la mutación exacta que ese test mata**. El lead vuelve a
mutar: un test que pasa igual sin el fix cuenta como no entregado.

---

## BLOQUEANTE 1 — `evaluar_alertas` deja la conexión en transacción abierta

`app/spapi/salud.py:99-100`: `_historial` hace `conn.execute(...)` fuera de todo
`with conn.transaction()`, y `app/db.py::connect` usa `autocommit=False`. Medido
contra Postgres real:

```
tras SELECT suelto    : INTRANS
tras with transaction : INTRANS   <- savepoint, NO commit
filas persistidas     : 0
control (sin SELECT)  : IDLE / 1 fila
```

Consecuencias: (a) si entra un `BaseException` mientras corre (Ctrl-C/SIGINT
durante el POST a Telegram, que va con `connect=5s read=10s`), el sello
`ok=False` del handler cae en un savepoint que `conn.close()` descarta y la
corrida **queda ABIERTA** — el síntoma exacto del incidente 145/147; (b)
`tests/test_spapi_orders.py:878` ya corre una segunda ingesta sobre la misma
conexión y desde A.5 no commitea nada (pasa sólo porque lee por esa conexión);
(c) la conexión queda *idle in transaction* durante la notificación. Contradice
el invariante escrito en `app/spapi/orders.py:425-427`.

**Fix**: leer dentro de un bloque y notificar FUERA de él:

```python
with conn.transaction():
    ultimas = _historial(conn, fuente, platform, run_id)
# ... decidir aquí, sin tocar la conexión ...
if debe_alertar:
    notifica_spapi_fallo(fuente, platform, motivo)
```

**Tests que deben morder**: (1) tras `evaluar_alertas`, la conexión queda IDLE
(`conn.info.transaction_status`); (2) dos `ejecutar_ingesta` seguidas sobre la
misma conexión y la verificación de persistencia **desde otra conexión** (hoy da
0 filas).

## BLOQUEANTE 2 — las alertas "inmediatas" no tienen flanco

`app/spapi/salud.py:118-120`: `inmediato` cortocircuita `_racha_fallida`, así que
cada corrida sellada con `lwa_fallido`/`http_429` manda Telegram. Con el cron que
propusiste (8 corridas diarias), un LWA caído de viernes a lunes son **24
mensajes idénticos**; y en la 2ª corrida de la racha dispara por las dos rutas.
Viola la regla dura "alertas sólo en flanco".

**Semántica correcta** (una alerta por racha, sin esperar dos fallos en las
clases inmediatas): alerta si y sólo si la corrida recién sellada **abre** una
situación nueva —
- clase inmediata (`lwa_fallido`/`http_429`): la corrida anterior **no** era
  fallida de esa misma clase;
- resto: se cumple `_racha_fallida` (segunda fallida consecutiva).

Las dos rutas son **excluyentes**: nunca dos mensajes por la misma corrida. Un
**cambio de clase dentro de una racha** (`contrato` → `lwa_fallido`) SÍ re-alerta:
es información nueva. Documenta esa decisión en el docstring.

**Tests que deben morder**: tres 429 seguidos = 1 mensaje; tres LWA seguidos = 1;
`contrato,contrato` seguido de `lwa_fallido` = 2 mensajes (el 2º por cambio de
clase); **una sola fallida `contrato` sin historia = 0 mensajes** (hoy sobrevive
la mutación `if len(ultimas) < 2: return ultimas[0][3] is False`).

## BLOQUEANTE 3 — el candado de GRANT no ejerce el rol que importa

`tests/test_spapi_salud.py:588-597` hace el INSERT con `platform` como **dueño de
la base**, no como `app_ingest`, y el `DO $$` de `migrations/0036:38-49` sólo
asserta `SELECT` (que ya venía a nivel tabla desde `0001:1459`, o sea que no
puede fallar nunca) y el negativo de `UPDATE`. El privilegio que las 4 ingestas
realmente ejercen sobre la columna nueva —`INSERT`— no está asertado en ningún
lado. Mutación demostrada: agregar `REVOKE INSERT ON ingest_run FROM app_ingest;`
a 0036 deja **142 tests verdes con las 4 ingestas rotas en producción**. Es la
clase 0033→0034 que el brief mandaba cubrir. (Hoy NO hay caída: el `INSERT` de
`0001:1467` es a nivel tabla y cubre columnas futuras. Falta el candado.)

**Fix**: (a) en el `DO $$`, assert positivo de `has_column_privilege('app_ingest',
'ingest_run','platform','INSERT')`; (b) en el test, ejercerlo de verdad:

```python
conn.execute("SET ROLE app_ingest")
conn.execute("INSERT INTO ingest_run (source, platform) VALUES ('spapi_orders','amazon_mx')")
with pytest.raises(psycopg.errors.InsufficientPrivilege):
    conn.execute("UPDATE ingest_run SET platform = 'amazon_us'")
conn.rollback(); conn.execute("RESET ROLE")
```

Debe morder contra el `REVOKE` de arriba.

## BLOQUEANTE 4 — el historial cuenta corridas abiertas y no ancla la corrida sellada

`app/spapi/salud.py:40-44`: `_SQL_ULTIMAS` ordena por `id DESC` incluyendo filas
con `ok IS NULL`. Dos escenarios reales: (a) una corrida huérfana abierta
(producción ya tuvo 145 y 147) entre dos fallidas enmascara la racha — corrida N
matada + N+1 fallida = cero alertas; (b) si hay una corrida más nueva abierta
(re-corrida manual encima del cron), `ultimas[0]` **no es la que acabas de
sellar** y el fallo se traga entero.

**Fix**: `AND ok IS NOT NULL` en `_SQL_ULTIMAS`, **y** pasar el `run_id` a
`evaluar_alertas` (ya está en scope en las 4 llamadas) para anclar exactamente la
corrida sellada en vez de asumir que es la más nueva. Deja `_SQL_ULTIMA` de
`bloque_salud` como está: mostrar una corrida abierta en `/salud` es informativo,
y la plantilla ya renderiza `—` para `ok` nulo. Documenta esa asimetría.

**Tests que deben morder**: fallida + huérfana abierta + fallida ⇒ alerta en la
segunda fallida; y con una corrida abierta más nueva, la alerta sigue evaluando
la corrida sellada.

## BLOQUEANTE 5 — el cableado de A.5 está sin probar en 3 de 4 ingestas

Con toda la suite verde sobreviven: borrar `evaluar_alertas` de la rama `except`
de `pricing.py:663`, `listings.py:432` e `inventario.py:395`; abrir el run con la
**plataforma cambiada**; y abrirlo con **`platform = NULL`** (las volvería
invisibles para `/salud` y para las alertas). Sólo `orders` está cableado-testeado
(`tests/test_spapi_salud.py:425`).

**Fix**: parametrizar ese test de aislamiento sobre los 4 pipelines
(`spapi_orders|spapi_pricing|spapi_listings|spapi_inventario`) afirmando, con LWA
caído: `main` retorna 1 sin excepción no controlada, la fila sellada tiene
`ok=False`, `skip_reason` con prefijo `lwa_fallido: `, `platform == "amazon_mx"`,
y **exactamente un** mensaje.

---

## Correcciones baratas (van en la misma ronda)

1. **La evidencia afirma de más.** `import app.spapi.salud` **sí** mete `app.ads`
   y `app.ads.config` en `sys.modules`, vía `app/notifica.py:38`
   (`from app.ads.config import DEFAULT_SECRETS_DIR`). No hay riesgo funcional
   (ese módulo es inerte: constantes y dataclasses, cero IO), pero la frase del
   reporte ("5 archivos sin imports `app.ads`") sugiere una propiedad que en
   runtime es falsa. Corrige la redacción **y** endurece el candado
   (`tests/test_spapi_salud.py:399`): cubre `from app import ads`, `__import__` e
   `importlib.import_module` con literal `app.ads*`, y agrega un check de grafo
   real en subproceso con allowlist explícita y su razón:
   `assert modulos_app_ads == ["app.ads", "app.ads.config"]` (nada de
   `app.ads.write`/`client`). Quita el `sys.modules.pop(...)` de la l.421, que no
   hace nada y confunde.
2. **Orden de deploy en `docs/DEPLOY.md`**: una línea explícita de que 0035 y
   0036 se aplican **ANTES** de reconstruir la app; si el código sale primero,
   las 4 ingestas truenan al abrir el run (`platform` inexistente).
3. **`migrations/0036`**: declara "No re-runnable" en el encabezado, como 0033 y
   0035 (no lleva `IF NOT EXISTS`).
4. **Ancla del prefijo**: `salud.py:83-84` usa `LIKE 'http_429%'`, donde `_` es
   comodín de un carácter. Usa `starts_with(skip_reason, %s)` (o `LIKE` con
   `ESCAPE`), y un test con motivo `contrato: ... 429 ...` que **no** debe caer en
   `ultima_429`.
5. **Asserts laxos** (todos sobreviven hoy):
   - `:368` — `"spapi_pricing" in mensajes[0]` sobrevive a intercambiar
     `fuente`/`platform` en la llamada de `salud.py:120`. Usa
     `"fuente: spapi_pricing"` y `"plataforma: amazon_mx"`.
   - `:378` — `"Ads" in mensajes[0]` sobrevive a poner el matiz en **todas** las
     alertas. Afirma la frase completa en LWA **y** su ausencia en el caso 429.
   - Un caso con secreto/PII en el motivo que verifique que no sale por Telegram
     (regla dura, hoy sin cobertura).
   - `:459` `test_canal_roto_no_impide_el_sello` **no ejerce el código nuevo**
     (pasa con `evaluar_alertas` convertido en `return None`). Rómpelo por dentro:
     `_historial` que levanta `psycopg.errors.InFailedSqlTransaction`, y afirma
     sello + salida limpia + el log "no pudo correr". Eso cubre además la
     excepción de BD, que hoy no tiene ningún test.
   - `:148` y `:526` `bloque_salud` — siembra **2 corridas por fuente**: hoy
     sobreviven `ORDER BY id DESC`→`ASC`, `rows_skipped` hardcodeado y
     `started_at = fin.isoformat()`. Afirma ids exactos, que `ultima_429`
     sobreviva a una corrida ok posterior, y valores (no sólo presencia de clave).
   - `:544` la plantilla — pasa con las 6 celdas de datos reemplazadas por `-`.
     Recorta la fila de `spapi_orders` y afirma filas/llamadas/`#id`/chip, más una
     fuente con `sin corridas` (el brief pedía los dos casos también en la página).
   - `tests/test_notifica.py` no tiene **ni un** test de `notifica_spapi_fallo` /
     `aviso_spapi_fallo`: quitar su `try/except` deja todo verde. Agrégalos con el
     patrón de los otros senders (sin canal → `True` y cero HTTP; transport que
     revienta → `False` sin levantar).
6. **Commitea los briefs**: `plans/brief-sp-api-01-a5-muse.md` está sin trackear,
   así que el contrato no viaja en el PR. Commitéalo junto con este archivo.

## Opcionales que el dueño pidió incluir

7. **Índice**: `/salud` hace hoy 24 Seq Scans por carga (3 consultas × 4 fuentes ×
   2 plataformas) sobre una tabla append-only y sin purga (D7). Agrega
   `CREATE INDEX ... ON ingest_run (source, platform, id DESC)` **dentro de 0036**,
   que aún no está aplicada en producción (verifícalo antes; si ya estuviera
   aplicada en algún entorno, va en una 0037 nueva y lo dices en el PR).
8. **Guarda en `/salud`**: `app/api_dashboard.py:845` llama `bloque_salud` sin
   protección; una `UndefinedColumn` tumba la pantalla entera (watermarks, ciclo,
   quota, histórico). Espeja el try/except por pieza de `_quota_de`
   (`api_dashboard.py:850`), con test de que `/salud` responde 200 y conserva el
   resto de las claves cuando `bloque_salud` levanta.
9. **Taxonomía viva en pricing/listings**: ahí un 503 o un corte de red no son
   fatales por ítem — se cuentan y la corrida muere por umbral, cuyo mensaje
   (`pricing.py:424`, `listings.py:230`) no lleva status, así que cae en
   `contrato`. Resultado: el operador lee "cambio de contrato" ante una caída de
   Amazon, y las clases `http_5xx`/`red` quedan muertas justo donde más se van a
   dar. Haz que `_vigilar_umbral` incluya la clase/último status dominante
   (`... racha 25, ultimo status=503`) para que `prefijo_motivo` lo clasifique.
   Test: 25 fallos 503 ⇒ sello con prefijo `http_5xx`.

---

## Fuera de alcance (no lo toques)

`app/ads/*`, los `notifica_*` existentes y su contrato, el esquema salvo 0036,
el cron instalado (sigue siendo propuesta), producción (cero corridas reales:
MockTransport + base desechable), `plans/sp-api-01.md`, `plans/ROADMAP.md`,
`plans/manifest.json`, `docs/CHAT-CONTEXT.md` y el tracker. **A.5 la cierra el
lead** tras esta ronda; A.R y A.6 son del lead y del dueño.

## Entrega

Commits normales sobre `feat/sp-api-01-a5`; `pre-commit run --all-files` verde
(**jamás `--no-verify`**); CI verde en el PR #246. Actualiza
`docs/evidencia/sp-api-01/A.5/reporte.md` con una sección "Ronda de review del
lead": qué se corrigió, la mutación exacta que mata cada test nuevo, y la
corrección de la afirmación sobre `app.ads`. En el PR, un párrafo con la
semántica final de las alertas (cuándo sale una, cuándo no, y qué pasa al cambiar
de clase dentro de una racha).
