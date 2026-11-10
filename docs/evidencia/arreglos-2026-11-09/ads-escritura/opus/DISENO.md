# Diseño de arreglos — grupo ads-escritura (candidato opus)

Código leído: copia de master `3c841f4` en `scratchpad/orbit-master` (sin `graphify-out/graph.json` en esa
copia: se usó `rg`). Sondas corridas hoy: `I_cap_gigante.py`, `I_entradas_dinero.py`, `I_vigia_desarmado.py`,
`F/F1_reconcilia_bids_ignora_goal.py`, `F/F2_guarda_frescura_enmascarada.py`. Las cinco reproducen tal como dicen
los reportes.

Resumen de la forma: cinco unidades chicas. Ninguna agrega una capa. Dos de ellas **quitan** código
(el reenvío de `reconcilia_bids` y la lectura de `max(synced_at)` como medida de frescura). Para lo que
se teclea a mano, la regla vive en el lector que ya usa el motor, o en una sola tabla por moneda
junto a `DEFAULTS_POR_MONEDA`.

| Unidad | Hallazgos | Cambio en vivo | Prueba nueva |
|---|---|---|---|
| U1 | I-H1 | no (solo rechaza un guardado) | 1 |
| U2 | E-H1 = F-H2 | solo si muere el sync de estructura | 1 (con base) |
| U3 | E-H3 + F-H1 | sí: elimina un camino que reenvía bids | 2 (una por hallazgo) |
| U4 | I-H2 | sí: el vigía cierra impulsos desarmados | 1 + migración |
| U5 | I-H4 | no en el motor; rechaza montos tecleados | 1, necesita cifras del dueño |

---

## I-H1 — cap diario ≥ 2,147,483,648 tumba la fase de apply

### Causa raíz
El lector del cap que usa el motor acepta un dominio más ancho que la columna donde termina el valor.
- `app/apply.py:330-348` `_cap_de_config`: `int(valor)` sin rango. Además acepta `50.0` (JSON float) y dígitos
  Unicode, que Python convierte y Postgres no.
- Destino: `apply_quota_state.cap INTEGER NOT NULL CHECK (cap > 0)` (`migrations/0001_initial.sql:819-826`) y el
  cast `(settings ->> clave)::INTEGER` del trigger `apply_cap_de_config` (`migrations/0039_precio.sql:1175`,
  reemplaza a `0002_apply.sql:476`), llamado por `apply_quota_fila_desde_config` (`0002_apply.sql:499-531`).
- Escritura: `proxima_config` revalida la config resultante con "los lectores del motor"
  (`app/config_write.py:500-514`), pero el cap no tiene lector propio en esa lista; el lazo
  `app/config_write.py:487-497` solo exige `cap >= 0`. La API: `app/api_write.py:253`
  (`Annotated[int, Field(ge=0)]`).

El docstring del módulo (`app/config_write.py:28-32`) promete «lo que el motor rechazaría… jamás llega a una
fila». Con el cap no se cumple porque el lector del motor no rechaza lo que Postgres sí rechaza.

### Uso primero
```python
# app/config_write.py — dentro del try de validación de la resultante (línea ~500)
for kind in KINDS_QUOTA:
    apply.cap_desde_settings(nuevo, platform, kind)   # ValueError -> SettingsInvalido (422)

# app/apply.py — el motor, mismo lector
def _cap_de_config(conn, platform, kind):
    fila = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    return None if fila is None else cap_desde_settings(fila[0] or {}, platform, kind)

# operador: POST /api/ads-optimizer/settings/amazon_mx {"caps": {"bid": 9999999999}}
#   -> 422 "cap bid fuera de rango: 0..2147483647, llego 9999999999"
```

### Forma
```python
# app/apply.py (junto a KINDS_QUOTA)
CAP_MAXIMO = 2_147_483_647
"""apply_quota_state.cap es INTEGER (0001:823) y el trigger castea ::INTEGER
(0039:1175): un cap que no cabe ahi revienta el INSERT de la fila del dia."""

def cap_desde_settings(settings: Mapping, platform: str, kind: str) -> int | None:
    """Lector UNICO del cap ads_apply_cap_<platform>_<kind>.
    None = clave ausente o JSON null (fail-closed: no nace fila del dia).
    Presente: int (no bool) o str de digitos ASCII, en [0, CAP_MAXIMO].
    Cualquier otra cosa: ValueError ruidoso (config ROTA, regla 3)."""
    raise NotImplementedError
    # valor = settings.get(f"ads_apply_cap_{platform}_{kind}")
    # if valor is None: return None
    # if isinstance(valor, bool): raise ValueError(...)
    # if isinstance(valor, int): n = valor
    # elif isinstance(valor, str) and valor.isascii() and valor.isdigit(): n = int(valor)
    # else: raise ValueError(f"...: valor no entero: {valor!r}")
    # if not 0 <= n <= CAP_MAXIMO: raise ValueError(f"cap {kind} fuera de rango 0..{CAP_MAXIMO}, llego {n}")
    # return n
```
- `app/config_write.py:487-497`: se queda la revisión de `kind in KINDS_QUOTA`. Se quita `if cap < 0` porque esa
  regla pasa al lector y se valida con la resultante.
- `app/api_write.py:253`: `Annotated[int, Field(strict=True, ge=0, le=CAP_MAXIMO)]`, importando la constante (no un
  número copiado). `strict=True` cierra el `True → 1` que muestra la sonda `I_entradas_dinero.py`.
- Invariante estructural: el rango ya vive en la base (columna INTEGER, CHECK `cap > 0`, cast). La forma nueva
  hace que el borde de Python use exactamente ese dominio, con una sola constante. Validación de borde: pydantic.

### Datos que ya existen
Los caps de producción valen 1,000,000 y siguen siendo válidos. Solo se lee la `config_version` vigente
(`app/apply.py:325-327`), así que las filas viejas no importan. No hace falta reparar datos.

### Cambio de comportamiento en vivo
Ninguno en lo que el motor escribe. Un guardado fuera de rango pasa de 200 a 422. Si una config rota llega
por SQL manual, el ciclo sigue fallando cerrado como hoy, pero el mensaje dice qué clave está mal
(«cap bid fuera de rango») en vez de un error crudo de Postgres. Se deshace revirtiendo el commit.

### Prueba (dinero: los caps gobiernan cuántos bids salen)
`tests/test_config_write.py`: `proxima_config({"ads_apply_cap_amazon_mx_bid": "50", ...}, "amazon_mx",
caps={"bid": 2**31})` debe levantar `SettingsInvalido`. Hoy devuelve la config con `"2147483648"` (sonda
`I_cap_gigante.py`).

### Alternativas
- **CHECK en `config_version`** con una función IMMUTABLE que recorra las claves `ads_apply_cap_*`. Esa regla
  ya está en el `CASE` del trigger y quedaría repetida en una tercera lista sincronizada a mano, y además
  validaría filas históricas append-only (habría que usar `NOT VALID`). Pierde.
- **Ampliar `apply_quota_state.cap` a BIGINT.** Quita el error, pero un cap de 10^10 sigue sin significar
  nada, la migración toca el trigger y la función, y el lector sigue aceptando `50.0`. Pierde.

### Tamaño
`app/apply.py`, `app/config_write.py`, `app/api_write.py` y una prueba. Es una unidad.

---

## E-H1 = F-H2 — la guarda de 48 h mide al aplicador, no al sync

### Causa raíz
`app/optimizer/windows.py:358-363` (`_SQL_SYNC_PLATAFORMA = SELECT max(s.synced_at) FROM ad_entity_state …`),
leída en `windows.py:565` y comparada en `:589`. `ad_entity_state.synced_at` tiene varios escritores:
el sync (`app/ads/structure.py:202-226`) y los aplicadores después de cada readback (`app/apply.py:647-649`,
`app/apply_cola.py:310`, `app/apply_producto.py:66`, `app/apply_harvest_reconciliacion.py:121`). Por fila,
`synced_at` es honesto: es la última vez que esa fila se comparó con Amazon. El error está en tomar el
**máximo de la plataforma** como medida de «estructura sincronizada». Con eso, una sola fila escrita por
el aplicador da la plataforma entera por fresca. Pantalla de Salud: `app/api.py:214` y
`app/api_dashboard.py:1163` importan el mismo SQL.

Hay un precedente exacto en el repo: `ledger_fresco_at` dejó de usar `MAX(observed_at)` y pasó a medir la
corrida de ingesta por este mismo motivo (`migrations/0016_target_margen_correcciones.sql:106-120`).

### Uso primero
```python
# app/cycle.py:3376 — sin cambios
guarda = windows.guarda_plataforma(conn, platform, ahora=decided_at)
# -> MotivoSkip(guarda="synced_at",
#      detalle="plataforma amazon_us: ultimo sync de estructura ok hace 5 days > 48h")

# Salud (app/api_dashboard.py:1163, app/api.py:214) — sin cambios de código:
# "Sync estructura" pasa a mostrar la hora de la corrida de las 06:45, no la del último bid aplicado.
```

### Forma
Solo cambia el SQL (y los docstrings que lo describen: `windows.py:63-64`, `:559-560`):
```python
# app/optimizer/windows.py
# Frescura de estructura = ultima corrida OK del sync de estructura que levanto acta
# de ESTA plataforma (ads_listado_plataforma, 0051). NO max(ad_entity_state.synced_at):
# los aplicadores tambien escriben esa columna por fila (patron ledger_fresco_at, 0016).
# Fuente literal: app/ads/structure.py:177 (SOURCE).
_SQL_SYNC_PLATAFORMA = """
SELECT max(r.started_at)
  FROM ingest_run r
  JOIN ads_listado_plataforma p ON p.ingest_run_id = r.id
 WHERE r.source = 'amazon_ads_structure_v2'
   AND r.ok
   AND p.platform = %s::platform
"""
```
- Se usa `started_at` porque `sync_structure` recibe la estructura ya descargada y abre la corrida después (`structure.py:357-367`).
  Así la edad que se mide es la más cercana a la foto real y nunca más fresca que ella. Es la misma elección que hace 0016.
- `ads_listado_plataforma` se escribe por plataforma dentro de la misma transacción que sella la corrida `ok`
  (`structure.py:297-322`, `:451-466`). Si el perfil de una plataforma fue rechazado, esa plataforma no tiene acta y
  la guarda salta: falla cerrada, que es lo correcto. La columna `ingest_run.platform` no sirve aquí porque el
  sync abre una sola corrida para las dos plataformas (`structure.py:179`, sin plataforma).
- Permisos: `app_decide`/`app_read` ya pueden leer las dos tablas (`0001_initial.sql:1460`, `0051:86-87`).
- El mensaje de `synced_at is None` (`windows.py:576-580`) pasa a decir «sin corrida ok del sync de estructura». La
  clave `guarda='synced_at'` y su etiqueta (`app/api_dashboard.py:323`) se conservan.
- Lo que el diseño deja a propósito: no se tocan los escritores de `synced_at` por fila, porque
  `v_anuncio_regresado` (`0079:42-53`), `propuestas_campana` y `jev_catalogo` leen esa columna por fila con su
  significado actual.
- Plan: `plans/orbit-03.md:85` (task 2.1) dice «saltada si … `synced_at` > 48h». Este diseño cambia la
  columna que se lee, pero conserva la intención («estructura vieja → salta»). Dilo en el PR.

### Datos que ya existen
Nada que reparar. **Paso previo de lectura**, que corre el lead antes del deploy:
`SELECT p.platform, max(r.started_at) FROM ingest_run r JOIN ads_listado_plataforma p ON p.ingest_run_id=r.id
WHERE r.source='amazon_ads_structure_v2' AND r.ok GROUP BY 1;` debe mostrar la corrida de hoy (~06:45) para MX y US.
Si alguna plataforma no aparece, el primer ciclo después del deploy la saltaría.

### Cambio de comportamiento en vivo
Hoy en producción `max = mediana = 06:45:55` en MX y US, así que la decisión no cambia. Cambia el caso de falla:
si el sync muere, a las 48 h el ciclo de esa plataforma sale `degraded` con `guarda_synced_at` en lugar de seguir
decidiendo con bids viejos. Se enciende directo (es una guarda que ya existía y no se cumplía). Se deshace
revirtiendo el commit.

### Prueba (dinero: el motor escribe bids calculados sobre un cache viejo)
`tests/test_optimizer_windows.py`, con base de pruebas: una corrida `amazon_ads_structure_v2` ok con acta de
`amazon_us` de hace 5 días, una métrica de ayer y una fila de `ad_entity_state` con `synced_at = now()`
(la del aplicador). `guarda_plataforma(...)` debe devolver `MotivoSkip(guarda="synced_at")`. Hoy devuelve `None`.

### Alternativas
- **Que los aplicadores no escriban `synced_at`.** Así son cuatro escritores que hay que mantener quietos, y una
  quinta escritura futura reabre el hueco (es el problema de «dueño repartido»). Además rompe a los lectores que usan
  `synced_at` por fila, como `v_anuncio_regresado`. Pierde.
- **Usar la mediana o un percentil de `synced_at`.** Depende de cuántas filas toque el aplicador y sigue midiendo al
  escritor equivocado. Pierde.

### Tamaño
`app/optimizer/windows.py` (SQL y docstrings) y una prueba. Es una unidad. Salud se arregla sola.

---

## E-H3 — un bid sin sello deja recortar dos veces en ~24 h

### Causa raíz
La fase de decisión no ve el estado «bid en vuelo». La trayectoria (`v_cambio_bid`,
`migrations/0067_bids02_campana_ajuste.sql:65-78`, `WHERE da.verify_ok IS TRUE`) y el enfriamiento
(`app/optimizer/goals.py:581-601`, «apply VERIFICADO») solo cuentan cambios sellados. El intento sin sello solo vive
en `apply_attempt` (`app/apply.py:705-714`). El ciclo decide en `app/cycle.py:3760` (`_fase_lecturas`) y reconcilia
después, en `app/cycle.py:3501`, justo antes de `aplica_bids` (`:3502`). El mismo ciclo puede confirmar D1 y aplicar
D2 encima.

Nota sobre el plan: `docs/CONTEXTO.md:426-428` dice «Reconciliación al inicio del ciclo contra Amazon VIVO». El
código la corre al final. Este diseño no cambia el orden (ver alternativas), pero cierra el riesgo con la guarda.

### Uso primero
```python
# app/cycle.py, en la lectura de TX2 por plataforma (junto a grupos_pausa_producto, ~:3266)
hojas_bid_en_vuelo = apply.hojas_con_bid_sin_sello(conn, platform)

# app/cycle.py, _procesa_decisora, junto al chequeo I.5 de pausa de producto (~:2209)
if entidad_id in hojas_bid_en_vuelo and not isinstance(veredicto, Pausar):
    contadores.skips_entidad[MOTIVO_BID_EN_VUELO] += 1   # "bid_en_vuelo"
    return

# Operador, en la pantalla de decisiones: "Bid anterior sin confirmar: se decide cuando Amazon lo confirme"
```
Día N: el PUT queda ambiguo. Día N+1: la hoja se salta con `bid_en_vuelo`, y en la fase de apply
`reconcilia_bids` confirma (`confirmed_at` = N+1, con lo que R2 espera 7 días desde ahí) o cierra el intento (U3/F-H1).
Día N+2 en adelante: la decisión ya ve el cambio sellado, o decide de nuevo con el bid leído.

### Forma
```python
# app/apply.py — junto a _SQL_INTENTOS_BID_SIN_SELLO, para que la guarda y el reconciliador
# usen el MISMO predicado (las filas que la guarda bloquea son exactamente las que la
# reconciliacion sella en la fase de apply del mismo ciclo live).
def hojas_con_bid_sin_sello(conn: psycopg.Connection, platform: str) -> frozenset[int]:
    """ad_entity_id con un intento tipo 'normal' de decision kind bid SIN sello en la
    plataforma. Solo lectura. Mismo SQL que reconcilia_bids (una fuente)."""
    raise NotImplementedError
    # return frozenset(f[3] for f in conn.execute(_SQL_INTENTOS_BID_SIN_SELLO, (platform,)))

# app/cycle.py
MOTIVO_BID_EN_VUELO = "bid_en_vuelo"
# _recorre_plataforma: lee hojas_bid_en_vuelo una vez en TX2 (mismo snapshot REPEATABLE READ)
# _procesa_decisora(..., hojas_bid_en_vuelo: frozenset[int] = frozenset())
```
- Se bloquean solo los movimientos de bid. La pausa no, porque va a la cola con veto, revalidación y su propio
  candado (`claves_bloqueadas`, `cycle.py:3379`).
- Solo se cuenta `tipo='normal'`. Las reversas y los probes sin sello no tienen reconciliador
  (`docs/APPLY.md:1092-1098`). Si se gatearan, la hoja quedaría bloqueada para siempre.
- La guarda corre en todos los modos. En shadow solo cambia el registro de la decisión, nunca una escritura.
- `app/api_dashboard.py:~300-345`: se agrega la etiqueta del motivo nuevo (la lista de etiquetas ya trae los
  `MOTIVO_*` del orquestador).
- Invariante: «una hoja con bid en vuelo no recibe otro bid» queda en la estructura de lectura (un conjunto
  inmutable tomado del mismo snapshot), no en texto.

### Datos que ya existen
Hoy hay 0 intentos sin sello en producción (HALLAZGOS). No hay nada que reparar.

### Cambio de comportamiento en vivo
Sí, y es más conservador: el día siguiente a un error ambiguo, la hoja no recibe un bid nuevo. Se enciende
directo, sin interruptor, porque solo resta escrituras. Se deshace revirtiendo el commit.

### Prueba (dinero)
`tests/test_cycle*.py`, con base: un `apply_attempt` `normal` sin `finished_at` para la decisión bid de ayer de la
hoja X, y X con datos que piden un −25 %. Corre el ciclo en shadow (no hace falta HTTP). Resultado esperado:
ninguna decisión `bid` para X y `notes.skips` con `bid_en_vuelo = 1`. Hoy sale una decisión bid para X.

### Alternativas
- **Reordenar: `reconcilia_bids` antes de `_fase_lecturas`**, como dice CONTEXTO. Habría que crear el `Aplicador`
  con credenciales antes de decidir y meter HTTP antes del snapshot. Si la reconciliación falla (sin credenciales,
  LWA caído), las filas siguen sin sello y la decisión vuelve a pasar por encima de ellas, así que la guarda haría
  falta de todos modos. Pierde como arreglo único. Con U3/F-H1 la reconciliación ya no hace PUT, así que este
  reorden queda como mejora posible más adelante.
- **Que la trayectoria cuente los intentos sin sello como cambios.** Mete un dato no verificado en la política pura y
  en el replay (por ejemplo, un R2 sobre un cambio que tal vez nunca ocurrió). Pierde.

### Tamaño
`app/apply.py` (una función), `app/cycle.py` (constante, una lectura, un parámetro y un `if`),
`app/api_dashboard.py` (etiqueta) y una prueba. Va en la misma unidad que F-H1 (U3).

---

## F-H1 — `reconcilia_bids` reenvía un bid viejo sin mirar modo ni bids más nuevos

### Causa raíz
`reconcilia_bids` es un segundo camino que escribe bids y se salta las reglas del primero. En divergencia hace un
PUT nuevo (`app/apply.py:799-818` → `_reintento_divergente`, `:824-875`). Ese PUT no pasa por `modo_efectivo`
(`app/apply.py:1268-1300`, que `aplica_bids` sí resuelve por decisión en `:1362`), no tiene límite de edad y no
revisa si hay decisiones posteriores (`_SQL_INTENTOS_BID_SIN_SELLO`, `:705-714`). El único filtro es
`gate_ancestros` (`:752`). La sonda F1 lo confirma: con goal en shadow, off o `enabled=false`, y con un 0.80 más
nuevo en Amazon, envía el PUT de 1.50 en los cuatro casos.

Por diseño, `aplica_bids` es el único que debería mover un bid: decide con datos frescos, el modo, la cuota y el
tope de intentos. El reenvío reaplica una decisión tomada con datos de otro día.

### Uso primero
```python
# app/cycle.py:3501 — la llamada no cambia
rec_bids = apply.reconcilia_bids(conn, aplicador, platform)
# notes.apply.bids_reconciliados = {"confirmados": n, "fallidos": m}
# divergencia: el intento queda 'fallo:divergencia_readback', el cache queda con lo LEIDO y
# decision_application.verify_ok = false. No sale ningun PUT. Al dia siguiente la hoja se decide de nuevo
# (ya sin bid en vuelo) con el bid real y pasa por aplica_bids (modo, cuota, tope).
```

### Forma
```python
def reconcilia_bids(conn, aplicador, platform) -> tuple[int, int]:
    """Reconciliacion del ledger de BIDS sin sello: CONFIRMA O CIERRA, JAMAS REENVIA.
    Por fila: gate de ancestros -> identidad -> readback (GET)
      - GET == pedido  -> 'ok:reconciliado' + resumen verify_ok + applied_cycle_id + cache (igual que hoy)
      - GET != pedido  -> 'fallo:divergencia_readback' + cache con lo leido + resumen verify_ok FALSE
      - sin bid / 5xx  -> 'fallo:readback_sin_bid' / 'fallo:readback_ambiguo' (igual que hoy)
    El unico escritor de bids es aplica_bids. La unica llamada HTTP aqui es el GET del readback."""
    raise NotImplementedError
```
- Se borran `_reintento_divergente` (`apply.py:824-875`) y el bloque `:798-818`. `_SQL_COUNT_INTENTOS` y
  `TOPE_INTENTOS` se quedan porque `aplica_bids` los sigue usando (`:1393`, `:1424`).
- La rama de divergencia ya no necesita cliente de escritura. El cliente perezoso (`:747-756`) se queda para el GET.
- Idempotencia: si corre dos veces, la segunda no encuentra filas sin sello y termina sin hacer nada. Si se cae a
  la mitad, cada fila se sella en su propia transacción (`with conn.transaction()`), así que las ya selladas no
  vuelven a salir. Como ya no hay PUT, una caída no puede dejar un intento nuevo.
- Plan: `docs/APPLY.md:529` dice «GET != pedido → divergencia → reintento (tope COUNT) **o** `failed`». El diseño
  deja siempre `failed`. Hay que actualizar esa fila en el mismo PR. No contradice la letra (la celda ya admite
  `failed`), pero sí cambia la rama que se toma por omisión.

### Datos que ya existen
0 intentos sin sello en producción. No hay nada que reparar.

### Cambio de comportamiento en vivo
Sí: se quita un camino que escribía a Amazon. Una divergencia pasa a esperar una decisión fresca (uno o dos días)
en vez de reenviar lo de ayer. Se enciende directo y se deshace revirtiendo el commit. Juntos, E-H3 y este cambio
dejan el ciclo de vida del bid así: *en vuelo → confirmado o cerrado → se decide de nuevo*, con un solo escritor.

### Prueba (dinero)
Convertir la sonda `F/F1_reconcilia_bids_ignora_goal.py` en prueba pura (conexión falsa y `httpx.MockTransport`) en
`tests/test_apply*.py`: con una fila sin sello que pidió 1.50 y un readback de 1.00, `reconcilia_bids` debe enviar
**cero** PUT y devolver `(0, 1)`. Hoy envía `{'keywordId': 'KW1', 'bid': 1.5}` y devuelve `(1, 0)`.

### Alternativas
- **Mantener el reintento y protegerlo con `modo_efectivo`, una edad máxima y «sin decisión posterior».** Son tres
  reglas nuevas para sostener un segundo escritor. Siguen reenviando decisiones con datos viejos (con la plataforma en
  shadow semanas y luego de vuelta en live, la edad máxima tendría que ser arbitraria). Es el patrón de «dos formas de
  hacer una tarea». Pierde.
- **Reconstruir una `DecisionBid` y pasarla por `aplica_bids`.** Obliga a cobrar cuota otra vez o a meter una
  excepción, y la decisión sigue siendo la de otro día. Pierde.

### Tamaño
`app/apply.py` (resta unas 70 líneas), `docs/APPLY.md` (una fila) y una prueba. **U3 = E-H3 + F-H1**, una unidad
verificable: las dos pruebas y la sonda F1 en verde.

---

## I-H2 — «PAUSAR GRUPO» sobre un impulso deja al vigía con 409 para siempre

### Causa raíz (más amplia que la del reporte)
1. **El 409 diario.** En `app/impulso_io.py:973-976`, la rama `pausar` solo reconoce `pausado` como «ya detenido».
   Con el lote `desarmado` llama a `pausador.pausa`, y `_cambia_estado_lote` responde
   `Abortar("desarmado: nunca revive")` (`tools/fabrica_campanas.py:1726-1727`). Eso pasa por
   `_lote_no_pausable` → `ErrorImpulso(409)` (`impulso_io.py:170-190`) y el vigía lo atrapa sin guardar lectura
   (`:1162-1165`).
2. **Distinta a lo descrito: aunque se arregle el 409, el impulso puede no cerrarse nunca.** Con las campañas
   detenidas, el gasto deja de crecer. Si el impulso ya juntó ≥ 2,000 impresiones y ≥ 20 clics sin venta, y el gasto
   quedó bajo `tope − 2×diario`, `veredicto_impulso` cae en el último caso (`app/impulso.py:187`) y devuelve
   `en_curso/seguir` para siempre. Lo comprobé con el código real: con 14, 30 y 90 días, `5000 impr, 40 clics,
   300 MXN de 1000` da `en_curso`. Ese impulso nunca llega a `pausar`, nunca escribe lectura final, sigue «vivo» en
   `_SQL_VIVOS` (`impulso_io.py:871-877`) y en `_SQL_VIVO_PRODUCTO` (`:457-462`), y el producto queda bloqueado para
   un impulso nuevo (`_falla_si_hay_vivo`, `:710-720`).

Por eso un candado aguas abajo («tratar `desarmado` como `pausado` en la rama pausar») no basta. El estado
`desarmado` es legítimo, es terminal («nunca revive») y el vigía debe **cerrar** el impulso.

### Uso primero
```text
Dueño: «PAUSAR GRUPO» sobre impulso-5-abcd1234 (fábrica) -> lote desarmado.
08:20 del día siguiente, impulso-vigia:
  - lee Amazon (pausa_huerfanas): ninguna campaña ENABLED -> no hace PUT
  - guarda la lectura FINAL: veredicto='desarmado', hecho='pausado', es_final=true
  - exit 0, sin 409
Pantalla del producto: «Lo pausaste desde la fábrica. Ya no gasta.»
El producto puede volver a impulsarse.
```

### Forma
```python
# app/impulso.py
EstadoVeredicto = Literal["en_curso", "vende", "clics_sin_ventas",
                          "impresiones_sin_clics", "sin_impresiones", "desarmado"]
VEREDICTO_DESARMADO = VeredictoImpulso("desarmado", True, "pausar")
# veredicto_impulso(lectura, tope) NO cambia (sigue pura, solo metricas).

# app/impulso_io.py, _vigila_uno, despues de veredicto = im.veredicto_impulso(...) (:964)
estado_lote = conn.execute(_SQL_ESTADO_LOTE, (lote,)).fetchone()
if estado_lote is not None and estado_lote[0] == "desarmado" and veredicto.accion != "graduar":
    veredicto = im.VEREDICTO_DESARMADO
...
if veredicto.accion == "pausar":
    if estado_lote is None or estado_lote[0] not in ("pausado", "desarmado"):
        pausador.pausa(lote)            # applied -> pausado (igual que hoy)
        conn.execute("UPDATE fabrica_lote SET estado='pausado' ... WHERE estado='applied'")
    else:
        pausador.pausa_huerfanas(lote)  # G7: verifica en Amazon; PUT solo si algo sigue ENABLED
    hecho = "pausado"
```
- Se conserva la precedencia de `graduar`: ya existe el caso «desarmado + vende → goals a live sin reencender»
  (`impulso_io.py:989-1006`) y no se toca.
- `pausa_huerfanas` (`impulso_io.py:224-264`) ya hace readback por campaña, escribe la fila del ledger antes del PUT
  y levanta si algo queda ENABLED. Si levanta, el vigía hace rollback de ese impulso y lo reintenta mañana, como hoy.
  Nunca anota «pausado» sin verificar (comentario G7, `:977-981`).
- Migración `0083_impulso_veredicto_desarmado.sql` y su reversa, con el mismo patrón de cabecera de 0079 (sin
  BEGIN/COMMIT, `psql -1`):
  ```sql
  ALTER TABLE impulso_lectura DROP CONSTRAINT lectura_veredicto;
  ALTER TABLE impulso_lectura ADD CONSTRAINT lectura_veredicto CHECK (veredicto IN
    ('en_curso','vende','clics_sin_ventas','impresiones_sin_clics','sin_impresiones','desarmado'));
  -- reversa: el mismo par sin 'desarmado'; falla si ya hay filas 'desarmado' (append-only): dilo en la cabecera.
  ```
  `lectura_en_curso_no_final` (`0065:56`) se queda: `desarmado` no es `en_curso`.
- `app/pantalla_productos.py:179-184` (`FRASES_FINALES`): agregar `"desarmado": "Lo pausaste desde la fábrica. Ya no
  gasta."`. Sin esa entrada, `frase_final` (`:187`) lanza KeyError al pintar la pantalla. Es la tercera lista del
  mismo vocabulario (CHECK, Literal, frases). Una prueba que recorra `get_args(EstadoVeredicto)` contra
  `FRASES_FINALES` sería de texto y el repo no las quiere, así que la cobertura es la propia prueba de abajo, que
  llama a `frase_final("desarmado")`.
- Invariante: «impulso cerrado = tiene lectura final». Se mantiene una sola fuente: `v_ad_group_impulso_abierto`
  (`0079:24-29`), `_SQL_VIVOS` y `_SQL_VIVO_PRODUCTO` leen esa misma condición y no se tocan.
- Idempotencia: el INSERT es `ON CONFLICT (impulso_id, corte) DO NOTHING` (`impulso_io.py:1065-1069`) y una lectura
  final saca al impulso de `_SQL_VIVOS`. Una segunda corrida no lo vuelve a ver.

### Datos que ya existen
No se midió en producción. **Lectura previa** (la corre el lead): `SELECT i.id, i.lote FROM impulso i JOIN
fabrica_lote f ON f.lote=i.lote LEFT JOIN impulso_lectura l ON l.impulso_id=i.id AND l.es_final WHERE
f.estado='desarmado' AND l.impulso_id IS NULL;`. Si devuelve filas, el primer vigía después del deploy las cierra
solo. No hace falta reparación manual.

### Cambio de comportamiento en vivo
Sí: el vigía escribe la lectura final de los impulsos desarmados y, si encuentra alguna campaña de ese lote
ENABLED en Amazon, la pausa (PUT y readback). Eso ya pasa hoy con los lotes `pausado` (G7). Se enciende directo.
Para deshacer: revertir el commit. La reversa de la migración solo aplica si no se escribió ninguna fila
`desarmado`.

### Prueba (escribe datos de producción: `impulso_lectura` y PUT a Amazon)
`tests/test_impulso_io.py`, a partir de la sonda `I_vigia_desarmado.py`: un lote `desarmado` con métricas
estancadas (19 días, 5,000 impresiones, 40 clics, 0 pedidos) y un pausador falso cuyo `pausa` falla la prueba si
lo llaman. Se espera exactamente una lectura con `veredicto='desarmado'`, `es_final=True`, `hecho='pausado'`, una
llamada a `pausa_huerfanas` y `frase_final('desarmado')` sin error. Hoy sale `ErrorImpulso 409` y 0 lecturas.

### Alternativas
- **Esconder o rechazar «PAUSAR GRUPO» para lotes `impulso-*`** en `app/fabrica_web.py:632-639` / `app/api_fabrica.py:389`.
  El estado sigue al alcance por CLI (`tools/fabrica_campanas.py --desarmar`, `:1832`), el dueño pierde la acción
  «parar todo, goals incluidos» y los impulsos que ya están desarmados quedan atascados. Pierde como arreglo; se
  puede sumar como mejora de pantalla si el dueño lo pide.
- **Sacar los lotes desarmados de `_SQL_VIVOS`.** Desbloquea el re-impulso, pero `v_ad_group_impulso_abierto`
  (la «única fuente de impulso abierto», `0079:31-34`) seguiría diciendo abierto, y retiro y pausa por producto no
  coincidirían. Serían dos definiciones de «abierto». Pierde.

### Tamaño
Migración y reversa, `app/impulso.py`, `app/impulso_io.py`, `app/pantalla_productos.py` y una prueba. Es una unidad.

---

## I-H4 — no hay tope superior para el dinero que teclea una persona

### Causa raíz
Cada borde valida solo `> 0` y finito, o un límite de forma de columna (`9999999999.99`), nunca uno de dominio:
- Presupuesto de campaña: `app/api_write.py:675` (`gt=0`), `app/campana_ajustes.py:143-152`.
- Presupuesto de rol en la fábrica: `app/api_fabrica.py:113-125` (límite de forma) y `app/fabrica_plan.py:373-382`
  (`> 0` y `>= bid`). Las campañas nacen ENABLED.
- Tope del impulso: `app/api_fabrica.py:455-468` (límite de forma) y `app/impulso_io.py:340-344`.
- Techo de bid y `harvest_default_bid` del goal: `app/api_write.py:213-217` y `app/goals_write.py:217-230`.
- Meta manual de plataforma: `app/api_write.py:251` (`gt=0`) y `app/optimizer/goals.py:202-221`. En cambio, el
  derivado se recorta a [10, 45] y la meta manual de la fábrica ya exige esa banda (`app/fabrica_plan.py:261-276`,
  `MARGEN_BANDA_MIN/MAX`, `goals.py:608-609`).

Lo que falta es un número de dominio por moneda. El repo ya tiene la forma para eso: `DEFAULTS_POR_MONEDA`
(`goals.py:111-114`, regla 4 de CONTEXTO).

### Uso primero
```python
# app/campana_ajustes.py:planea_ajuste (preview y aplicar usan la misma funcion; la huella no cambia)
g.exige_bajo_techo("presupuesto_diario", monto, despues.moneda)
#  -> ValueError "presupuesto diario de 5000 MXN supera el maximo de 2000 MXN" -> 422 en la pantalla

# app/fabrica_plan.py:valida_parametros (por rol)
g.exige_bajo_techo("presupuesto_diario", p.budget, moneda)   # -> PlanInvalido

# app/impulso_io.py:_plan_producto, sobre el diario derivado del tope (una sola cifra: el diario)
g.exige_bajo_techo("presupuesto_diario", tope.presupuesto_diario, moneda)

# app/goals_write.py:_valida_pre_editar — SOLO sobre campos que CAMBIAN
g.exige_bajo_techo("bid", cambios["bid_ceiling"], fila["bid_currency"])

# app/config_write.py:proxima_config — SOLO cuando target_manual_pct cambia
# banda [MARGEN_BANDA_MIN, MARGEN_BANDA_MAX] (la misma de la fabrica)
```

### Forma
```python
# app/optimizer/goals.py, junto a DEFAULTS_POR_MONEDA
@dataclass(frozen=True)
class TechoHumano:
    """Tope de lo que una PERSONA puede teclear (no del motor). Decision del dueno."""
    bid: Decimal                  # techo de bid / harvest_default_bid de un goal
    presupuesto_diario: Decimal   # presupuesto diario de campana (ajuste, fabrica, impulso/14)

TECHOS_HUMANOS_POR_MONEDA: dict[str, TechoHumano] = {
    "USD": TechoHumano(bid=Decimal("<dueno>"), presupuesto_diario=Decimal("<dueno>")),
    "MXN": TechoHumano(bid=Decimal("<dueno>"), presupuesto_diario=Decimal("<dueno>")),
}

def exige_bajo_techo(clase: Literal["bid", "presupuesto_diario"], monto: Decimal, moneda: str) -> None:
    """ValueError con el texto que ve el operador si monto > techo de su moneda.
    Moneda fuera de la tabla = ValueError (regla 3, igual que DEFAULTS_POR_MONEDA).
    Cada llamador traduce a su excepcion de borde (PlanInvalido, GoalInvalido, 422)."""
    raise NotImplementedError

def exige_banda_meta_manual(acos_pct: Decimal) -> None:
    """La meta tecleada vive en [MARGEN_BANDA_MIN, MARGEN_BANDA_MAX]. fabrica_plan.valida_banda_manual
    pasa a llamar a esta (un mensaje, una fuente)."""
    raise NotImplementedError
```
- La regla se aplica **solo al construir o cambiar** un valor, nunca al releer lo guardado. Es el mismo criterio que
  ya sigue `valida_banda_manual` (`fabrica_plan.py:262-268`, «una regla de creación no puede ser candado retroactivo
  de lectura»). El motor (`target_desde_settings`, `resuelve_floor_ceiling`) **no** cambia: una config o un goal viejo
  fuera de banda no aborta ningún ciclo.
- Invariante: la tabla es la estructura (una sola lista por moneda). El chequeo es validación de borde en los cinco
  sitios de alta. No hay CHECK en la base, porque el techo es una decisión que puede cambiar y no un hecho del
  esquema.

### Datos que ya existen
No se tocan. **Lectura previa**, para fijar las cifras con datos (la corre el lead): el presupuesto diario máximo
vigente por moneda, el `bid_ceiling` y el `harvest_default_bid` máximos de `ads_optimizer_goal` por moneda, y el
`target_acos_pct` de los goals y de la config vigente fuera de [10, 45]. Lo que hoy esté por encima de un techo se
queda como está y solo se rechaza si alguien intenta cambiarlo a otro valor también por encima.

### Cambio de comportamiento en vivo
Ninguno en lo que el motor escribe. Solo rechaza entradas humanas por encima del techo (422 con el motivo en
palabras llanas). Se enciende directo cuando el dueño da las cifras. Se deshace revirtiendo el commit o subiendo
la cifra.

### Prueba (dinero: presupuesto)
`tests/test_campana_ajustes.py`: `planea_ajuste(vigente_100_MXN, CambiarPresupuesto(Decimal("5000")), …)` con un
techo de MXN menor a 5000 debe levantar `ValueError`. Hoy devuelve la frase «de 100 MXN a 5000 MXN» (sonda
`I_entradas_dinero.py`). Para el resto de los sitios basta comprobar que corre: comparten la función.

### Alternativas
- **Confirmación reforzada relativa** (si el monto nuevo pasa de K veces el vigente, el literal debe incluir el
  monto). La campaña nueva de la fábrica no tiene vigente, settings y goals no tienen paso de literal, y habría que
  cambiar tres pantallas y crear un segundo protocolo de confirmación. Pierde. Se puede sumar más adelante sobre la
  tabla si el dueño quiere más fricción.
- **Techo como clave de `config_version`, editable en settings.** El mismo error de dedo que se quiere atajar podría
  subir el techo desde la misma pantalla, y además suma un lector y una regla de validación. Pierde frente a una
  constante con decisión del dueño escrita junto a `DEFAULTS_POR_MONEDA`.

### Tamaño
`app/optimizer/goals.py`, `app/campana_ajustes.py`, `app/fabrica_plan.py`, `app/impulso_io.py`, `app/goals_write.py`,
`app/config_write.py` y una prueba. Es una unidad, **bloqueada por las cifras del dueño**.

---

## Orden propuesto y dependencias

1. **U1 (I-H1)**: es la única ALTA del grupo, la más chica y no depende de nada.
2. **U2 (E-H1)**: un SQL. Antes hay que correr la lectura de actas por plataforma. Conviene antes de U3 para
   que la guarda de plataforma ya mida bien cuando cambie el ciclo de vida del bid.
3. **U3 (E-H3 + F-H1)**: van juntos. Sin la guarda, quitar el reenvío deja E-H3. Sin quitar el reenvío, la guarda
   deja el reenvío viejo de F-H1(a). No depende de U1 ni de U2 en el código. Toca `app/apply.py` igual que U1 pero en
   otras funciones (conflicto de merge trivial: se hacen en serie).
4. **U4 (I-H2)**: independiente. Lleva migración, así que va en su propio despliegue.
5. **U5 (I-H4)**: independiente en el código. Espera las cifras del dueño. Toca `app/config_write.py` igual que U1
   (en serie).

No hay dependencia de datos entre unidades. Ninguna necesita escribir en producción fuera del deploy normal.

## Decisiones del dueño

1. **¿Cuánto es lo máximo que una persona puede poner como presupuesto diario de una campaña, y como puja máxima
   de un goal, en USD y en MXN?** Recomiendo: puja máxima = 4 veces el techo por omisión (USD 10.00, MXN 180.00).
   Presupuesto diario = el doble del más alto que hoy tiene una campaña en esa moneda, redondeado (sale de la
   lectura previa de U5).
2. **¿La meta manual de ACoS de la plataforma (y de un goal) debe vivir en la misma banda de 10 % a 45 % que ya se
   exige al crear con la fábrica?** Recomiendo que sí, validado solo al cambiarla.
3. **¿Un impulso que pausaste con «PAUSAR GRUPO» debe darse por terminado ese mismo día, para que el producto
   pueda impulsarse otra vez?** Recomiendo que sí, salvo que ya haya vendido (en ese caso se gradúa como hoy).
4. **(Relacionada, fuera del alcance de I-H2) ¿Un impulso que pausaste con su propio botón «pausar impulso» debe
   cerrarse también?** Hoy deja el lote `pausado` y, si ya tuvo impresiones y clics sin venta, el vigía puede
   quedarse en «sigue aprendiendo» para siempre (mismo `impulso.py:187`) y bloquear el producto. Recomiendo
   decidirlo aparte. No lo incluyo para no cambiar una pausa que hoy es reversible.
5. **¿Aceptas que, cuando Amazon deja una puja en un estado dudoso, Orbit no la reenvíe al día siguiente y la
   vuelva a decidir con datos frescos?** Recomiendo que sí (U3). Hay que actualizar una fila de `docs/APPLY.md`.
6. **Con U2, si la sincronización de estructura deja de funcionar dos días, el motor de esa plataforma se detiene
   (como siempre se diseñó). ¿Quieres un aviso por Telegram cuando falle esa sincronización?** Hoy `app/ads/salud.py`
   solo vigila los reportes. Recomiendo que sí, como trabajo del grupo operación.

## Riesgos

- **U2 puede detener una plataforma si su acta no se escribe** aunque el sync salga ok (por ejemplo, un perfil
  rechazado). Es falla cerrada y correcta, pero hay que correr antes la lectura de actas por plataforma.
- **U3 cambia el ciclo de vida del bid.** El día siguiente a un error ambiguo, esa hoja no se mueve. El costo es un
  día de retraso, y solo en hojas con un error de Amazon (0 a 4 bids por plataforma al día).
- **U4 suma un valor a un vocabulario que vive en tres lugares** (CHECK, `EstadoVeredicto`, `FRASES_FINALES`). Si
  falta la frase, la pantalla de productos falla. La prueba de U4 llama a `frase_final("desarmado")`.
- **U5 con cifras mal elegidas** puede rechazar un presupuesto legítimo. Por eso las cifras salen de la lectura de
  producción, y la regla es solo de alta, nunca retroactiva.
- **Residual fuera del grupo:** si `_cap_de_config` levanta (config rota), toda la fase de apply se aborta, incluida
  la liberación de la cola (`cycle.py:3560-3564`). Es falla cerrada declarada. U1 evita que se llegue ahí desde la
  pantalla, pero no cambia el aborto en sí.
- **Residual declarado que se conserva:** las reversas y los probes de bid sin sello no tienen reconciliador
  (`docs/APPLY.md:1092-1098`). U3 no las gatea a propósito.
