# DISENO: grupo ads-decision (candidato sonnet)

Código leído: copia de solo lectura de master `3c841f4`
(`/private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit-master`). Todas las rutas `archivo:línea` son de esa copia.
No había `graphify-out/graph.json` en la copia; usé `rg`/lectura directa. No corrí pruebas ni toqué producción.
Lo que no leí lo marco "inferido".

## Resumen: 6 unidades, 11 hallazgos

Causas compartidas (se arreglan una vez):

| Causa | Hallazgos | Dónde se arregla |
|---|---|---|
| El negative EXACT se escribe sobre el texto de la keyword propia | E-S1 | `hygiene.decide_hygiene` (una sola regla para decisión y las 3 revalidaciones) |
| La protección M1 (`proteccion.evalua`) trata "dato desconocido" como "sin venta" y el juicio de bids no sabe que hay una pausa en espera | A-H1, A-H2, C-H4, D-H1 | `proteccion.evalua`, `lee_cache`, `politica.decide`, `libera_vencidos` |
| El episodio económico M5/M6 no caduca ni exige muestra en la ventana que juzga | C-H1, C-H3 (y C-H2 por orden de guardas) | `economia_cambio.py` + `_episodio_economico` |
| `piso_aprendido` ignora la dirección del regreso | E-H2 | `caso.Trayectoria.piso_aprendido` |
| Respaldo de familia ancla en un número ajeno | B-H1 (decisión del dueño) | `cycle._margen_familia_de_hoja` |
| M4 descarta día NULL en la lectura reciente | A-H3 (solo shadow) | `productos_io` + `productos.motivos_economicos_pausa` |

Causas raíz distintas a la descrita en HALLAZGOS.md:
- **E-S1 es más ancho de lo reportado**: el camino **harvest** también escribe un negative EXACT en el ad group de origen como primer paso de su cadena (`app/apply_harvest.py:14-15`) y su dedupe solo mira la campaña destino (`hygiene.py:350`, `keywords_campana_destino`). Un término igual a una keyword EXACT del grupo de origen y con ≥2 pedidos también apagaría la keyword propia. La regla va para ambos kinds.
- **E-H2**: la reversa y `_cambios_por_hoja` (`app/lecturas_caso.py:382-399`) están bien (el `bid_antes` derivado es el bid subido, que es verdad). El error vive solo en cómo `piso_aprendido` interpreta un regreso (`app/optimizer/caso.py:251-260`).
- **C-H3 y C-H1** comparten la falta de vigencia del episodio; una sola caducidad cierra las dos (C-H3 queda inalcanzable).
- **A-H1** tiene dos mitades: el ancla se pierde en `lee_cache` cuando la plataforma no está cubierta (`app/proteccion_venta.py:188-199`), y `evalua` convierte `pedidos=None` en "aplicar" (`app/optimizer/proteccion.py:83-88`).

---

## U1. E-S1: el negative no puede negar el texto de una keyword EXACT propia

### Causa raíz
`decide_hygiene` evalúa el término contra umbrales de clics/costo y emite `negative` sin mirar qué keywords tiene el ad group de origen: la rama negative está en `app/optimizer/hygiene.py:296-326`; solo la rama harvest consulta `keywords_norm` (`hygiene.py:291`, uso en `:350-351`) y contra la campaña DESTINO, no contra el origen. Resultado: un término idéntico al `keyword_text` de una keyword EXACT del mismo ad group (que es su propio tráfico) se niega con umbral 20 clics/8 USD, más bajo que la pausa (100/40) y sin la protección M1.

### Uso primero
- Ciclo (`app/cycle.py:2798`, `_procesa_grupo`): antes de llamar, `propias = hygiene.keywords_exactas_del_grupo(conn, grupo_id)` y se pasa `keywords_propias=propias`. Un término que coincide sale como skip `termino_es_keyword_propia` (cuenta en `contadores.skips_termino`, línea 2813), no como decisión.
- Revalidación del negative (`app/apply_cola.py:1282`, `_revalida_negative`): mismo lector con `grupo = fila.ad_entity_id`; si el resultado ya no es `negative` y el motivo es `termino_es_keyword_propia`, devuelve ese motivo como `discard_motivo` (hoy devuelve `ya_no_califica`, línea 1294-1296).
- Revalidación del harvest (`app/apply_harvest_reconciliacion.py:231`): igual, mismo lector; el descarte sigue siendo `ya_no_califica` (comentario de línea 238).
- Replay (`app/optimizer/replay.py:96`): no pasa nada; el default vacío reproduce las filas históricas.

### Forma
```python
# app/optimizer/hygiene.py
MOTIVO_TERMINO_ES_KEYWORD_PROPIA = "termino_es_keyword_propia"

_SQL_KEYWORDS_EXACT_GRUPO = """
SELECT k.keyword_text
  FROM ad_entity k
 WHERE k.parent_id = %s AND k.kind = 'keyword' AND k.match_type = 'EXACT'
"""  # ciega al estado, como keywords_campana_destino (hygiene.py:395-405): una archivada tambien cuenta (fail-closed)

def keywords_exactas_del_grupo(conn: psycopg.Connection, grupo_id: int) -> frozenset[str]:
    raise NotImplementedError

def decide_hygiene(*, ..., keywords_existentes, umbral_negative=..., piso_negative=None,
                   keywords_propias: frozenset[str] = frozenset()) -> tuple[ResultadoTermino, ...]:
    ...
    # tras (2) asin_like y (3) orders None, ANTES de bifurcar por orders:
    #   if _normaliza_texto(t.search_term) in propias_norm:
    #       resultados.append(_skip(t.search_term, MOTIVO_TERMINO_ES_KEYWORD_PROPIA)); continue
```
- Se coloca antes de la bifurcación por pedidos para cubrir negative y harvest con una sola línea (ambos escriben el negative EXACT en el origen).
- Invariante: queda en la capa pura (`decide_hygiene`), que es el único dueño de "qué se decide" (docstring `hygiene.py:1-9`). No hay CHECK de base posible: la relación término-keyword vive en datos de ingesta.
- Default `frozenset()` solo existe para el replay; los 3 callers vivos lo pasan explícito. Es la única concesión; el replay lee decisiones ya tomadas.
- Solo `EXACT`: un negative exacto sobre el texto de una PHRASE/BROAD no apaga la keyword (sigue sirviendo otros términos). Variantes cercanas ("arras boda" vs "arras de boda") no se cubren (inferido: Amazon las agrupa en el reporte de la keyword).

### Datos que ya existen
- 14 decisiones históricas con este patrón (HALLAZGOS.md). Las `done`/shadow no se tocan.
- **Aplicadas en vivo (2321 "arras de boda", 2322)**: pasos de reparación aparte, ejecutados por otro agente con plan: (1) en Amazon, `GET /sp/negativeKeywords/list` del ad group y confirmar si el negative sigue; (2) si sigue, borrarlo con la ruta existente `apply_cola.reversa_negative` (`app/apply_cola.py:1762`; necesita el `negative_id` del ack, que sale de `apply_attempt` de esas decisiones); (3) confirmar que la keyword vuelve a recibir impresiones. La decisión se queda como historia.
- **2869 en `pending_veto` live**: no requiere SQL. Al vencer el veto, `libera_vencidos` llama `_revalida_negative`, que con el cambio descarta con `termino_es_keyword_propia` antes de cobrar cuota (`apply_cola.py:1636-1638`). Si se quiere antes, el dueño la veta desde la pantalla.

### Cambio de comportamiento en vivo
Solo quita escrituras: el motor deja de crear negatives (y harvests) sobre el texto propio. No agrega dinero. Se enciende directo (no hay interruptor y no hace falta: es una regla de seguridad). Se deshace revirtiendo el commit. Idempotente: pura y sin estado; dos corridas dan lo mismo. Si el ciclo cae a medias, nada queda escrito de más.

### Prueba (caso dinero + falla ya pasada en prod)
Una sola, en `tests/test_optimizer_hygiene.py`: término con 20 clics, 40 USD, 0 pedidos, en un grupo cuyo `keywords_propias={"Arras de boda "}` y `search_term="arras de boda"` -> `kind is None`, `motivo == "termino_es_keyword_propia"`. Hoy devuelve `negative`. Variante de una línea para harvest (2 pedidos) con el mismo término.

### Alternativas
- Guarda en el apply (`apply_cola._ejecuta_negative`) con LIST fresco de keywords: duplica la regla en el borde de escritura y deja que el ciclo siga proponiendo y quemando el veto de 48 h. Pierde por "dos dueños de la misma decisión".
- Filtro en SQL de `windows.terminos_cortes` (excluir términos igual a keyword): esconde el motivo (no queda auditable) y mezcla lectura de ventanas con política.

### Tamaño
`hygiene.py`, `cycle.py`, `apply_cola.py`, `apply_harvest_reconciliacion.py` + 1 caso de prueba. Una unidad verificable. La reparación de datos es otro paso.

---

## U2. Protección M1: A-H1, A-H2, C-H4, D-H1

### A-H1. Un día reciente con `orders` NULL deja pasar la pausa

**Causa raíz.** `app/optimizer/proteccion.py:83-88`: sin episodio, `not lectura.cubierta or lectura.pedidos is None` devuelve `_aplica(MOTIVO_INDETERMINADA)` aunque haya un pedido conocido (`lectura.ancla`). Antes, `lee_cache` ya borra el ancla cuando la plataforma no está cubierta (`app/proteccion_venta.py:188-199`, `ancla=None`), y `hay_venta` exige `pedidos > 0` (`:89-95`), así que el ciclo ni evalúa la protección (`app/cycle.py:1251-1254`). Un pedido conocido es una cota inferior: un NULL en otro día no la anula.

**Uso primero.** `proteccion.evalua(insumos)` sigue siendo la única función que usan el ciclo (`politica.py:212-221`) y la cola (`apply_cola.py:1155-1174`). Una hoja con pedido en D-3 y NULL en D-7 ahora devuelve `esperar/venta_reciente_protegida` con `abre_episodio=True` (ancla D-3), y `decide_pause` devuelve `(None, venta_reciente_protegida)`.

**Forma.**
```python
# proteccion.evalua, rama sin episodio (reemplaza proteccion.py:83-91)
if lectura.ancla is not None:                       # pedido conocido, aunque haya NULL o dia faltante
    return _espera(lectura.ancla, _vence(lectura.ancla), MOTIVO_PROTEGIDA, abre=True)
if _hay_hueco(insumos) or not lectura.cubierta or lectura.pedidos is None:
    return _indeterminada(None, None)               # desconocido != cero (regla 3): la pausa espera
return _aplica(MOTIVO_SIN_VENTA, None, None)        # cubierta y pedidos == 0
```
```python
# proteccion_venta.lee_cache: la rama "no cubierta" conserva ancla (solo borra pedidos)
LecturaReciente(pedidos=None, ancla=lectura.ancla, cubierta=False, observado_al=...)
# CacheProteccion.hay_venta: True si lectura.ancla is not None
```
Invariante: queda en la función pura. Cambio de significado explícito: "fuente incompleta sin pedido conocido" pasa de *aplicar* a *indeterminada*. Esto sigue la letra del plan ("Tratar una fuente incompleta como desconocida. No convertirla en cero", `plans/orbit-07-motor-decisiones.md`, bullet de M1), que el código contradecía; ver pregunta al dueño Q3.

**Datos que ya existen.** 0 filas NULL en 30 días (HALLAZGOS). Episodios ya escritos no cambian. Cola: las filas `released` se revalidan solas con la regla nueva; no hay reparación.

**Cambio en vivo.** Solo retrasa pausas (más espera, nunca más gasto). Va con el interruptor existente `ads_proteccion_venta`; se deshace quitándolo (D-H1 abajo cubre qué pasa con las filas en espera al apagarlo).

**Prueba (dinero).** La sonda `A_venta_reciente.py` P1 ya es el caso: `pedidos=None, ancla=D-3, cubierta=True` -> hoy `decide_pause -> pause pause_umbral`, con el arreglo `None venta_reciente_protegida`. Se vuelve una prueba de `tests/test_optimizer_proteccion.py`.

**Alternativas.** (a) Dejar `evalua` y que `lee_cache` sustituya NULL por 0 solo si hay ancla: reparte la regla en dos módulos. (b) Hacer que `pause_orders_desconocido` también cubra la ventana reciente en `bid._decide_pause`: dos reglas para lo mismo.

### A-H2. Hueco entre la ventana de cortes y la lectura reciente

**Causa raíz.** La ventana de cortes de la hoja termina en `min(max(metric_date)-3, D-10)` (`app/optimizer/windows.py:413-421`); la lectura reciente es fija D-9..D-1 (`app/proteccion_venta.py:32,159-161`). Si la hoja deja de recibir filas, la venta de su última fecha A queda entre ambas y `lectura.pedidos == 0` sale como cero medido.

**Uso primero.** Nada cambia para los llamadores: `InsumosProteccion` ya trae `ventana_hasta` de la hoja (`proteccion_venta.py:121-135`, lo pasan ciclo y cola). `evalua` compara `ventana_hasta` con `evaluado_el - DIAS_MADUREZ` (`caso.DIAS_MADUREZ`, `caso.py:27`) y, si es menor, hay un hueco de fechas que ninguna ventana mide.

**Forma.**
```python
def _hay_hueco(insumos: InsumosProteccion) -> bool:
    return (insumos.ventana_hasta is not None
            and insumos.ventana_hasta < insumos.evaluado_el - dt.timedelta(days=DIAS_MADUREZ))
```
Ya está usado en el bloque de A-H1: sin pedido conocido y con hueco -> `indeterminada` (no pausa). No se toca el SQL ni se agrega una tercera lectura: se evita la fórmula duplicada de `fin_ventana_cortes` en SQL. Invariante: pura, en `evalua`.

**Datos que ya existen.** Tabla `proteccion_venta_reciente` con 0 episodios (HALLAZGOS). Nada que reparar. Efecto lateral conocido y aceptado: la hoja agotada que cruzó el umbral queda con su fila de pausa `released` en espera mientras siga sin filas (ya ocurre hoy con episodio, `D_sonda_fila_inerte.txt`; cuesta un LIST diario). Pasa a `v_entidad_inerte` a los 14 días (`migrations/0082`). Sin gasto, no hay dinero en juego.

**Cambio en vivo.** Solo más espera. Mismo interruptor. Prueba: P3 de `A_venta_reciente.py` (`ultima fila 2026-10-30 venta 2026-10-30`, sin episodio) pasa de `pause pause_umbral` a `None venta_reciente_indeterminada`; entra en la misma prueba de A-H1.

**Alternativa.** Hacer la lectura reciente por hoja (desde `ventana_hasta+1`): más exacta pero exige reescribir `_SQL_RECIENTES` por hoja y repetir la fórmula de `windows.py`. Pierde por "información duplicada".

### C-H4. Con M1 la pausa espera pero el juicio puede SUBIR la puja con pedidos de hace 41-90 días

**Causa raíz.** `politica.decide` descarta el motivo de bloqueo (`_motivo_bloqueado`, `app/optimizer/politica.py:212`) y sigue al juicio normal; R6 usa la ventana ponderada D-90..D-10 y puede devolver +15% a una hoja que "calificaba para pausa" (`politica.py:225-228`, comentario de diseño: "no se devuelve un Mantener aquí").

**Uso primero.** `decide` sigue siendo el único dueño de la puja. Después de obtener `factor`, y antes de `_freno_trayectoria`/`_paso` (`politica.py:~283-296`):
```python
if factor > 0 and _motivo_bloqueado in (MOTIVO_PROTEGIDA, MOTIVO_INDETERMINADA):
    return Mantener(MOTIVO_PAUSA_EN_ESPERA)   # una hoja con pausa pendiente no sube; los recortes siguen igual
```
**Forma.** Una constante `MOTIVO_PAUSA_EN_ESPERA = "pausa_en_espera"` en `politica.py` y la condición de arriba. Los recortes conservan su camino (lo autoriza `tests/test_optimizer_proteccion.py:191`). Invariante en `decide` (función pura).

**Datos que ya existen.** Pujas ya subidas no se revierten (el estado histórico no se corrige solo). Replay: una decisión `bid` posterior al 2026-11-08 con ese caso reproduciría ahora `Mantener`; la ventana M1 tiene 1-2 días de vida, el efecto es mínimo (inferido: no medí cuántas existen). Si el dueño no lo acepta, se versiona como `proteccion_venta_v2` (más pesado, no lo recomiendo).

**Cambio en vivo.** Quita subidas (reduce gasto). Directo, sin interruptor propio; se deshace con el commit o apagando M1. **Prueba (dinero):** `C_m1_subida_coherente.py` -> hoy `Mover(0.15, bajo_target)`; con el arreglo `Mantener(pausa_en_espera)`.

**Alternativa.** Hacer que R6 use solo la ventana madura cuando hay protección: toca la ventana de evidencia de un juicio entero. Pierde por alcance.

### D-H1. Apagar M1 (o `rollback.sh`) pausa de golpe las hojas en espera

**Causa raíz.** Con `ads_proteccion_venta` ausente, `cycle._fase_apply` pasa `proteccion_activa=False` (`app/cycle.py:3831`), `libera_vencidos` no lee la caché (`app/apply_cola.py:1576-1578`) y `_revalida_pause` llama `decide_pause(proteccion=None)` (`apply_cola.py:1155-1174`): la fila `released` califica y se aplica sin veto nuevo.

**Uso primero.** `python -m app.cli ... --proteccion off` no cambia de sintaxis. En el siguiente ciclo `libera_vencidos` descarta con `proteccion_apagada` las filas `pause` que fueron decididas bajo M1; el ciclo vuelve a decidir con la regla histórica y la pausa (si aún califica) nace como `pending_veto` con 48 h de veto del dueño.

**Forma.**
```python
# app/apply_cola.py
MOTIVO_PROTECCION_APAGADA = "proteccion_apagada"
_SQL_DECIDIDA_CON_PROTECCION = """
SELECT (inputs #> '{caso,pausa,proteccion}') IS NOT NULL FROM decision WHERE id = %s
"""  # ruta inferida de cycle.py:1034,1072 y caso.py:576; confirmar al implementar

# en libera_vencidos, antes de _revalida (apply_cola.py:~1608):
if not proteccion_activa and fila.kind == "pause" and decidida_con_proteccion(conn, fila.decision_id):
    conn.execute(_SQL_DESCARTA, (MOTIVO_PROTECCION_APAGADA, fila.id, fila.estado)); descartadas.append(...); continue
```
`discard_motivo` es TEXT sin CHECK (`migrations/0002_apply.sql:82`), no hace falta migración. El `WHERE estado = %s` hace la transición atómica e idempotente: correr dos veces o caer a medias no duplica ni reaplica. Invariante: validación de borde en el único punto que libera (`libera_vencidos`).

**Cubrir `rollback.sh`.** El código anterior no tiene este descarte. Paso de runbook (no de código): antes de `rollback.sh`, descartar las filas con `decision_sin_aplicar.motivo='espera_proteccion'` (consulta en `D-motor.md`) con el plan de despliegue. Se anota en `docs/DEPLOY.md` y `docs/evidencia/orbit-07/activacion-orbit07.md`.

**Datos que ya existen.** 0 pausas en `espera_proteccion` hoy. Sin reparación.

**Cambio en vivo.** Solo ocurre al apagar M1; convierte "pausa inmediata" en "pausa con veto de 48 h". Se prueba apagando en un ciclo de prueba; se deshace volviendo a encender (las filas descartadas se re-deciden).

**Prueba (dinero).** Con base desechable, en `tests/test_orbit07_m1.py`: caso "cola que espera sin cuota" + `config_version` sin la clave -> fila `discarded/proteccion_apagada` y 0 unidades de cuota cobradas (hoy: `applying/applied`). El lead del repo marcó que no se corren pruebas con base en esta Mac; va a Linux.

**Alternativas.** (a) Separar en la receta `--proteccion off` de M2/M5-M6: solo docs; no protege a quien apague M1 a propósito. (b) Re-leer la caché aunque el interruptor esté apagado: mantiene M1 viva de facto y contradice la semántica de reversa.

### Tamaño U2
`proteccion.py`, `proteccion_venta.py`, `politica.py`, `apply_cola.py` + 3 pruebas. Se puede dividir en 2 unidades verificables si el implementador prefiere: U2a (A-H1, A-H2: pura, ~30 líneas) y U2b (C-H4 y D-H1). Recomiendo dos PRs.

---

## U3. Episodio económico M5/M6: C-H1, C-H2, C-H3

**Causa raíz compartida.** El episodio `aplicable` vive hasta que ocurra otro evento (`app/lecturas_caso.py:723`) y su ventana post se juzga sin muestra mínima: `_juicio_subida` frena con `post.venta <= pre.venta` (`app/optimizer/economia_cambio.py:114-116`) aunque `post.clics` sea 3. Un episodio maduro desde x+17 dura hasta x+90 (74 días). Además, el cambio de x-3 sale de `_SQL_CAMBIOS` en x+88 (`app/lecturas_caso.py:155,816`, solo D-90) y deja un episodio contaminado como `maduro` (C-H3). C-H2: `if pre.venta <= 0: return SIN_EVIDENCIA` (`economia_cambio.py:112-113`) va antes de las dos reglas que sí podían juzgar.

### Uso primero
- `politica._freno_trayectoria` (`politica.py:380-392`) llama `juicio_repeticion(episodio, target_acos_pct=, factor=)` igual que hoy.
- El caso sale con `aplicable=False, motivo_aplicabilidad="vencido"` pasados 30 días, y `juicio_repeticion` devuelve `sin_evidencia` -> R16/R15 deciden como antes de ORBIT 07 (idéntico al comportamiento sin episodio, `politica.py:386`).
- Sin muestra de 20 clics en `[x+1, x+7]` no se frena ni se permite.

### Forma
```python
# app/optimizer/caso.py
DIAS_VIGENCIA_EPISODIO = 30                         # desde x; el episodio nace maduro en x+17 -> 13 dias utiles
MOTIVO_ECONOMIA_VENCIDO = "vencido"
CLICS_NUEVOS = 20                                   # se MUEVE desde politica.py:36 (una sola fuente)

# app/lecturas_caso._episodio_economico (linea 723)
vencido = (d - x).days > DIAS_VIGENCIA_EPISODIO
aplicable = not posteriores and not vencido
motivo_aplicabilidad = (MOTIVO_ECONOMIA_CAMBIO_POSTERIOR if posteriores
                        else MOTIVO_ECONOMIA_VENCIDO if vencido else None)

# app/optimizer/economia_cambio.py
def _ventanas_medidas(...): ... and post.clics >= CLICS_NUEVOS      # muestra en la ventana que se juzga
def _juicio_subida(...):
    # SE QUITA el "if pre.venta <= 0: return SIN_EVIDENCIA" de la linea 112
    if post.venta <= pre.venta: return frena          # pre.gasto > 0 ya lo exige _ventanas_medidas
    ... contribucion (no necesita ACoS) ...
    acos_pre/acos_post None -> sin_evidencia           # ya existe: cubre pre.venta == 0 sin inventar ACoS
```
`_juicio_recorte` conserva su guarda `pre.venta <= 0` (un recorte solo se permite con ventas conservadas); la muestra mínima la cubre `_ventanas_medidas` para ambos sentidos.

Invariantes: la vigencia queda en el dato congelado del episodio (`aplicable`), así el replay lee `aplicable` del caso y no consulta el reloj; la muestra mínima y el orden de guardas quedan en la función pura. Con vigencia 30, C-H3 es inalcanzable (el cambio de x-3 sigue visible hasta x+87).

### Datos que ya existen
Los episodios viven dentro de `decision.inputs.caso.trayectoria.episodio` (`docs/evidencia/orbit-07/m5/contrato-episodio.md`); no hay tabla. Las decisiones pasadas se rejuegan con el episodio congelado y no cambian. Sin reparación.

### Cambio en vivo
Cambia lo que el motor sube/recorta en live (MX y US): se levantan frenos y permisos viejos. Efecto neto: más subidas posibles después de x+30 (hojas rentables ya no frenadas hasta x+90) y, con C-H2, algunos frenos nuevos donde la semana previa tenía 0 ventas. M6 sigue sin poder subir por sí solo (solo `frena` o `sin_evidencia`, `C-economia-cambio.md` conclusión). Interruptor existente: `ads_bid_economia_<plat>` (`off` quita la clave, `config_write.py:337-341`); se enciende directo, se deshace con ese `off`. No hay shadow; el cambio es chico y sin rutas de gasto nuevas.

### Prueba (dinero)
Una prueba de `tests/test_optimizer_politica.py` que usa `C_m6_politica.py` y `C_h2_pre_sin_venta.py`: (1) episodio post 3 clics/0 ventas -> hoy `Mantener(subida_sin_economia)`, con el arreglo `Mover(+0.15)`; (2) pre 0 ventas/10 gasto, post 0 ventas/20 gasto con 40 clics -> hoy `Mover(+0.15)`, con el arreglo `Mantener(subida_sin_economia)`. El caso de vigencia es una línea más con `d - x = 31`.

### Alternativas
- Ampliar `_SQL_CAMBIOS` a D-97 para C-H3 y dejar el freno de 74 días: arregla un hallazgo y deja el otro; más SQL.
- Dejar vigencia indefinida y exigir muestra: una semana con 20+ clics y mala suerte seguiría frenando 74 días.

### Tamaño
`caso.py`, `lecturas_caso.py`, `economia_cambio.py`, `politica.py` (import de la constante) + 1 prueba. Una unidad.

---

## U4. E-H2: `piso_aprendido` solo de regresos que deshacen recortes

### Causa raíz
`Trayectoria.piso_aprendido` toma `max(bid_antes)` de todo cambio `regreso_por_desplome`/`regreso_del_dueno` (`app/optimizer/caso.py:251-260`). Un regreso del dueño sobre una SUBIDA tiene `bid_antes` = el bid subido y `bid_despues` menor (dirección -1), que es un piso por encima del bid vigente. La vista (`migrations/0067_bids02_campana_ajuste.sql:80-89`) y `_cambios_por_hoja` (`app/lecturas_caso.py:382-399`) son correctos. El diseño dice que un regreso del dueño tiene dirección +1 (`plans/bids-02-construccion.md:732`).

### Uso primero
`politica._paso` y R17 (`politica.py:402-406`, `:615-616`) siguen leyendo `caso.trayectoria.piso_aprendido`. Tras revertir una subida 1.00 -> 1.15 -> 1.00, `piso_aprendido` es `None` y un `pierde_dinero_fuerte` vuelve a dar `Mover(-0.25, 0.75)` (sonda `E_piso_aprendido_subida.py`). La pantalla de reversa y `POST /api/ads-optimizer/reversa/bid` (`app/api_write.py:383-394`) no cambian: sigue permitido revertir una subida.

### Forma
```python
@property
def piso_aprendido(self) -> Decimal | None:
    pisos = [c.bid_antes for c in self.cambios
             if c.origen in ("regreso_por_desplome", "regreso_del_dueno")
             and c.bid_despues > c.bid_antes]          # solo regresos hacia ARRIBA (deshacen un recorte)
    return max(pisos) if pisos else None
```
Invariante en el tipo `Trayectoria` (propiedad derivada, único lugar que define el piso). Ninguna migración.

### Datos que ya existen
9 reversas manuales en producción, todas de recortes (HALLAZGOS): el piso no cambia para ninguna. Replay: igual salvo para la combinación subida+reversa, que hoy son 0.

### Cambio en vivo
Vuelve a permitir recortes que hoy se bloquean erróneamente (solo en hojas con reversa de subida; 0 hoy). Directo; se deshace con el commit. Idempotente por ser derivado.

### Prueba (dinero)
`E_piso_aprendido_subida.py` convertida en caso de `tests/test_optimizer_caso.py`: hoy `Mantener(piso_aprendido)`, con el arreglo `Mover(factor=-0.25, bid_nuevo=0.75)`.

### Alternativas
- Impedir en `reversa_manual(tipo="bid")` revertir subidas (`apply.py:1665-1698`): quita una función que el dueño puede querer, y no repara filas ya escritas.
- Filtrar en `v_cambio_bid` (rama reversa) por dirección: cambia una vista compartida con otras lecturas (pantalla, M5) para arreglar una interpretación del piso.

### Tamaño
`caso.py` (3 líneas) + 1 prueba. Unidad mínima, va primero entre las de bid.

---

## U5. B-H1: respaldo de familia al perder la meta de producto (DECISIÓN DEL DUEÑO)

### Causa raíz
Con `producto_directo_v1`, el previo con procedencia `margen_producto` no es ancla ni cuenta como previo de familia: `ultimo = familias.destino` y `tiene_previo = previo.procedencia == "margen_familia"` (`app/cycle.py:3127-3141`). La hoja salta de su meta (4%) a la economía de plataforma (25.5%) y desde ahí la familia solo baja 0.5 por ciclo (`app/optimizer/goals.py:996-1000`, 26 ciclos hasta el 12%). Con ledger rancio todos los peldaños se apagan a la vez (`B_meta_directa.txt` §3-4). Cumple la letra de `plans/orbit-07-motor-decisiones.md:237` ("el target de producto no es ancla de la familia").

### Uso primero
`_margen_familia_de_hoja` (`cycle.py:3099`) lo llaman el ciclo (`cycle.py:2412`/hoja) y la cola (`app/economia_revalidacion_io.py:151-152`) con la misma función, así que el cambio llega a ambos. La hoja que venía en 4% queda en 4% y sube a la economía de su familia (12%) de una vez cuando la familia mide (la subida es inmediata por la asimetría de `resuelve_target_margen`, `goals.py:~1000`: `recortado >= ultimo -> aplicado`), o camina +0.5/ciclo hacia el destino si la familia no tiene dato. Nunca queda por encima de la economía de su familia mientras ésta mide.

### Forma (recomendada: opción A)
```python
# app/cycle.py _margen_familia_de_hoja, rama familias.economia == POLITICA_ECONOMIA (lineas 3135-3141)
ajeno = previo is not None and previo.procedencia == "margen_producto"
if previo is None:                 ultimo = familias.destino
elif ajeno:                        ultimo = min(previo.target, familias.destino)   # el producto solo sirve de ancla hacia ARRIBA
else:                              ultimo = previo.target
tiene_previo = previo is not None and previo.procedencia in ("margen_familia", "margen_producto")
```
Invariante: en `cycle._margen_familia_de_hoja`, único lugar donde se elige el ancla de familia; `resuelve_target_margen_familia` (puro) no cambia. `min(...)` mantiene el comportamiento actual cuando el target de producto era mayor que el destino (ancla en plataforma, baja 0.5/ciclo).

### Datos que ya existen
0 keywords bajo meta de producto hoy (`docs/evidencia/orbit-07-plan/prearranque-2026-11-07/README.md:26-28`); no hay nada escrito que reparar. `target_acos_ciclo` es histórico y no se toca.

### Cambio en vivo
Cambia el target en live de las hojas que pierdan la meta de producto (MX y US, hoy ninguna). Se enciende directo con el despliegue; es más conservador que lo actual (target más bajo). Se deshace con el commit. Si el dueño prefiere dejarlo como está, no se hace nada y se documenta el exceso.

### Prueba (dinero)
`B_respaldo_familia.py` convertida a prueba de `tests/test_orbit07_m2.py`: ciclos con target por encima de la economía de la familia (12) = 26 hoy, 0 con el arreglo. Hoy `tests/test_orbit07_m2.py:402-438` fija la caminata 29.5 -> 29.0 -> 28.5 para el otro caso (previo mayor al destino): sigue pasando.

### Alternativas
- **B (solo falla de datos)**: anclar en el target vigente únicamente cuando el motivo de pérdida es dato faltante (`ledger_rancio`, `ads_con_metrica_ausente`) y dejar el salto cuando el producto deja de ser elegible por otra causa. Más lógica en el ciclo, y el escenario A (halo) seguiría igual. Pierde por parcial.
- **C**: volver al ancla AUTO-03 (todo previo es ancla): arreglaría el salto pero la hoja con target alto (45) bajaría 0.5/ciclo desde 45, peor que hoy (`min` evita eso).
- **D**: no cambiar y documentar. Gasto adicional posible de hasta 26 días con ACoS por encima de su familia.

### Tamaño
`cycle.py` (≈6 líneas) + 1 prueba. Una unidad, bloqueada por la decisión Q1.

---

## U6. A-H3: M4 descarta en silencio un día reciente con métrica NULL (solo shadow)

### Causa raíz
`app/productos_io.py:310`: `dias_recientes, _ = _dias_halo(...)` tira el indicador de faltantes; `_dias_halo` salta el día NULL (`productos_io.py:574-578`). `venta_reciente_producto` (`app/optimizer/productos.py:400-412`) queda en `None` y `motivos_economicos_pausa` no registra nada. La revalidación (`app/apply_producto.py:175-187`) usa la misma lectura y repite el descarte.

### Uso primero
`lee_entradas_productos` marca un impedimento, igual que ya hace con la ventana del halo (`productos_io.py:311-312`). `motivos_economicos_pausa(entrada, reciente=True)` lo trata como bloqueo de la pausa; el caller (ciclo y `apply_producto`) no cambia.

### Forma
```python
# productos_io.lee_entradas_productos
dias_recientes, faltantes_reciente = _dias_halo(ventana_reciente, mismo_sku, moneda)
if faltantes_reciente: impedimentos.append("venta_reciente_ausente")

# productos.motivos_economicos_pausa (linea ~430)
motivos.extend(m for m in entrada.impedimentos
               if m == "sku_distinto_en_asin" or (reciente and m == "venta_reciente_ausente"))
```
Invariante: validación en `motivos_economicos_pausa` (única regla de la pausa por producto). Con `reciente=False` (replay v2) conserva la semántica anterior. Las entradas congeladas antes no traen el impedimento: replay intacto.

### Datos que ya existen / en vivo
Pausa por producto está en shadow MX/US: no mueve dinero hoy. Las evaluaciones guardadas no se corrigen. Al encender el live, el arreglo ya está. **Hacer esto antes de que el dueño encienda `ads_producto_pausa_<plat>` en live.** Idempotente (puro).

### Prueba
Para este caso no hace falta prueba nueva (shadow; no es dinero hasta el live); basta correr la sonda P5 de `A_venta_reciente.py` y ver `motivos_economicos_pausa != ()`. Si el dueño enciende live antes, aplica regla "dinero" y se escribe un caso en `tests/test_orbit07_m3_m4.py` (nombre inferido).

### Alternativa
Reutilizar `ads_con_metrica_ausente` (ya existente) en vez de un impedimento nuevo: ese impedimento tiene otro significado (halo/margen) y hoy no bloquea la pausa (`productos.py:430`). Mezclarlos cambiaría la meta de producto (M2). Pierde por acoplamiento.

### Tamaño
`productos_io.py`, `productos.py` (≈4 líneas). Una unidad.

---

## Orden propuesto y dependencias

1. **U1 (E-S1)**: único ALTO, ya tiene 2 negatives en vivo y 1 en cola; independiente. La reparación de datos (borrar negatives 2321/2322) es un paso aparte con plan, puede ir en paralelo y no depende del código.
2. **U4 (E-H2)**: 3 líneas, independiente, evita bloqueo de recortes de 90 días.
3. **U2a (A-H1, A-H2)** luego **U2b (C-H4, D-H1)**: U2b usa `MOTIVO_PROTEGIDA/INDETERMINADA` de `proteccion.py`, que U2a ensancha (indeterminada ahora cubre hueco y NULL); mejor probar C-H4 sobre la regla ya corregida.
4. **U3 (C-H1/H2/H3)**: independiente del resto; después de U2 porque ambos tocan `politica.py` (evitar choque de merge, ver Riesgos).
5. **U6 (A-H3)**: antes de encender la pausa por producto en live; sin urgencia hoy.
6. **U5 (B-H1)**: cuando el dueño responda Q1.

Dependencias duras: ninguna entre U1, U3, U4, U6. U2b después de U2a (recomendada, no dura). U5 depende de Q1.

## Decisiones que solo el dueño puede tomar

- **Q1 (B-H1).** ¿Quieres que una keyword que pierde la meta de su producto se quede en su meta vigente y suba a la economía de su familia, en vez de saltar a la de plataforma (hoy 4% -> 25.5% y 26 ciclos de bajada)? Esto cambia la letra de `plans/orbit-07-motor-decisiones.md:237`. Recomiendo que sí (opción A); hoy no hay ninguna keyword afectada, así que no corre prisa.
- **Q2 (C-H1).** ¿Cuánto tiempo puede gobernar un episodio de cambio de puja? Recomiendo 30 días desde el cambio (13 días útiles tras madurar en x+17) y exigir 20 clics en la semana que se juzga (la misma cifra de R15). Alternativa: 45.
- **Q3 (A-H1).** Cuando falta un día de ingesta de plataforma y la hoja no tiene pedido conocido, ¿la pausa debe esperar (recomendado, es lo que dice el plan: "fuente incompleta = desconocido") o seguir su curso como hoy? Esperar solo retrasa pausas; no gasta más.
- **Q4 (E-S1).** ¿Quieres que además se vigilen las keywords PHRASE/BROAD con el mismo texto? Recomiendo que no: el negative exacto no las apaga.
- **Q5 (D-H1).** Al apagar M1 hoy las pausas en espera salen sin veto nuevo. ¿Prefieres que al apagarlo vuelvan a pasar por 48 h de veto (recomendado) o se apliquen directo?

## Riesgos

- U2b y U3 tocan `politica.py` (constante `CLICS_NUEVOS` movida a `caso.py`, rama `Mantener` nueva): mantener PRs separados y secuenciales, o hacer el movimiento de la constante primero.
- C-H4: una decisión `bid` de M1 ya congelada reproduce distinto en replay (solo desde 2026-11-08). No medí cuántas hay; riesgo bajo, mitigable con versión `proteccion_venta_v2`.
- U1: la ruta `decision.inputs` de D-H1 (`caso.pausa.proteccion`) y el nombre de la prueba de M4 son inferidos de la lectura del serializador (`caso.py:576`, `cycle.py:1034`); el implementador debe confirmarlos primero.
- U1: si el negative de septiembre sigue en Amazon, la keyword lleva semanas sin servir; la reparación manual (reversa) debe ir antes de otro cambio de bids en ese grupo.
- D-H1 vía `rollback.sh` solo se cubre con paso de runbook, no con código.
- Pruebas del repo dan fallas falsas en esta Mac; las de U2b (con base) deben correr en Linux (motor de planes).
