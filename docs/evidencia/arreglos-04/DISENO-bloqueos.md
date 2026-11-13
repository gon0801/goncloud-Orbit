# ARREGLOS 04: escrituras a Amazon fuera del ciclo (diseño)

Alcance vigente: el de `plans/arreglos-04.md` (bloques B-A a B-D). La falla 11 de `apply.md`
(`tools/archiva_inertes.py --reponer`) va en ARREGLOS 05; donde este diseño diga otra cosa, manda el plan.

Frente: las fallas 3, 4, 8, 9, 10 y la segunda viñeta de la 12 de
`docs/evidencia/arreglos-2026-11-12/apply.md`. Código leído en `a4644aad` (lo que corre en
producción), worktree `plan/arreglos-02`. Las citas `archivo:línea` son de ese SHA.

Resumen de la forma:

- Se borra la herramienta de terminal que archiva anuncios sin registro y que producción no usa
  (falla 3). Se borra, no se arregla: es la decisión del dueño del 2026-11-12.
  `tools/archiva_inertes.py` se queda: tiene su procedimiento y un lote pendiente del dueño.
- El bloqueo del dueño (`app/bloqueo_dueno_efecto.py`) solo cuenta como bloqueado un negative
  ENABLED, lee el rechazo por ítem del 207 y deja abierto un «quitar» dudoso para que lo cierre
  la reconciliación de cada hora (fallas 4, 9 y 8).
- La sonda `tools/smoke_apply.py` lee el rechazo por ítem del 207 (falla 12, segunda viñeta).
- El lector de listas del harvest avisa cuando no leyó todo, y ese aviso nunca autoriza un POST
  ni un borrado (falla 10). Antes del cambio se mide cuántas páginas ocupan hoy las listas.
- Un paso de operación revisa en Amazon los 48 bloqueos «aplicados» y repara solo los que Amazon
  confirma que no bloquean (pregunta a).

Ningún arreglo toca los cinco archivos de TEMPORADA 01 (`app/cycle.py`, `app/optimizer/politica.py`,
`app/lecturas_caso.py`, `app/apply.py`, `app/apply_cola.py`). Ninguno lleva migración, así que no
toca `tools/despliegue/comun.sh`, que TEMPORADA 01 sí cambia. La falla 11 queda fuera, en ARREGLOS 05.

## Medido en producción (12-nov-2026, solo lectura)

Todas con `echo "<select>" | bash docs/evidencia/arreglos-01/lee_prod.sh`.

| Pregunta | Consulta | Resultado |
| --- | --- | --- |
| Bloqueos por último paso | `select ult.paso, count(*), min(v.created_at)::date, max(v.created_at)::date from bloqueo_veredicto v join lateral (select e.paso from bloqueo_efecto e where e.veredicto_id=v.id order by e.created_at desc, e.id desc limit 1) ult on true where v.tipo='aprobado' group by 1` | `aplicado|48|2026-11-12|2026-11-12`. Los 48 se aprobaron hoy entre 04:40 y 04:44 UTC. |
| Tiempo entre intención y aplicado | `select v.plataforma, count(*), min(...), percentile_cont(0.5) ..., max(...) from bloqueo_veredicto v join bloqueo_efecto i on ... paso='intencion' join bloqueo_efecto a on ... paso='aplicado' where v.tipo='aprobado' group by 1` | MX 25: mínimo 0.01 s, mediana 0.02 s, máximo 2176.55 s. US 23: 0.01 a 0.02 s. |
| ¿Algún id de bloqueo lo creó el motor? | `select count(*) filter (where exists (select 1 from apply_attempt t where t.ack::text like '%'||e.negative_keyword_id||'%' or t.resultado like '%'||e.negative_keyword_id||'%')), count(*) from bloqueo_efecto e where e.paso='aplicado'` | `0|48` |
| ¿Alguno lo creó la fábrica? | `select count(*) from bloqueo_efecto e where e.paso='aplicado' and exists (select 1 from fabrica_lote_paso p where p::text like '%'||e.negative_keyword_id||'%')` | `0` |
| Tablas que guardan ids de negatives | `select table_name from information_schema.columns where column_name ilike '%negative%' or (data_type='jsonb' and table_name ilike '%fabrica%') group by 1` | Solo `bloqueo_efecto`, `fabrica_lote` y `fabrica_lote_paso`. Los negatives no entran a `ad_entity`. |
| Señal vigente de cada clave bloqueada | `select v.vigente, v.lectura, count(*) from bloqueo_veredicto b join jev_senal_vigente v on v.ad_group_id=b.ad_group_id join jev_senal s on s.id=v.senal_id and s.termino_sha256=b.termino_sha256 where b.tipo='aprobado' group by 1,2` | `True|ajena|47`, `True|sin_lectura|1`. Ninguna con el mismo `senal_id` que se aprobó (`left join jev_senal_vigente s on s.senal_id=v.senal_id` da 48 nulos). |
| Keywords por cuenta | `select e.platform, e.kind, coalesce(s.status,'(sin estado)'), count(*) from ad_entity e left join ad_entity_state s on s.ad_entity_id=e.id where e.kind in ('keyword','ad_group','product_ad') group by 1,2,3` | MX 2,719 keywords (305 ENABLED, 76 PAUSED, 2,338 ARCHIVED). US 1,340 (292, 122, 926). |
| Harvests y negatives del ciclo | `select j.platform, j.fase, count(*), max(j.created_at)::date from harvest_job j group by 1,2` y `select q.platform, q.kind, q.estado, count(*) from apply_queue q where q.kind in ('negative','harvest') group by 1,2,3` | MX 8 harvests `done` (último 31-oct), US 3 (15-oct). US 2 negatives `applied`. |
| Uso de las herramientas de archivo | `select (select count(*) from anuncio_retiro), (select count(*) from anuncio_recreacion_intento), (select count(*) from keyword_archivo_manual)` | `0|0|0` |
| Uso de la sonda | `select tipo, count(*), min(started_at)::date, max(started_at)::date from apply_attempt group by 1` | `probe|31|2026-08-26|2026-09-12`, `normal|512|...|2026-11-12`, `reversa|14|...|2026-11-12` |
| Anuncios sin publicación | `select e.platform, s.status, count(*) from ad_entity e join ad_entity_state s on s.ad_entity_id=e.id where e.kind='product_ad' and e.listing_id is null group by 1,2` | MX 1,168 ENABLED, 429 PAUSED, 1,063 ARCHIVED. US 920, 23, 264. |
| ¿Gastan los anuncios sin publicación? | `select e.platform, count(distinct e.id), sum(o.cost) from ad_entity e join ads_metric_observation o on o.ad_entity_id=e.id where e.kind='product_ad' and e.listing_id is null and o.metric_date >= current_date - 30 and o.cost > 0 group by 1` | Sin filas: cero gasto en 30 días. |

## Preguntas que pidió cerrar el lead

**a. Falla 4: ¿se sabe desde la base cuáles bloqueos se adoptaron sin POST?** No. El camino que
adopta y el que postea dejan las mismas filas (`intencion` y luego `aplicado` con id,
`bloqueo_dueno_efecto.py:219-223` contra `:238-240`). El `created_at` es `now()`, la hora de inicio
de la transacción, que abre la primera consulta después del commit de la intención y antes de
cualquier HTTP: los 0.01 s medidos no incluyen la llamada a Amazon y no separan los dos caminos. Ningún
id coincide con un negative del motor ni de la fábrica. Inferencia, no medición: un negative ENABLED
previo frena el gasto del término y la señal «ajena» no se habría disparado; uno PAUSED deja gastar y
la señal sí se dispara. Por eso el caso que adopta es justo el que la señal propone. Se cierra leyendo
Amazon (bloque B-D).

**b. Falla 3: ¿quién usa `archivar-anuncios` y `reponer-anuncios`?** Nadie en automático. No hay
línea en `tools/despliegue/crontab-orbit.txt`. No hay herramienta que los llame (`rg` en `app`,
`tools`, `scripts`, `verify`, `.github`). Solo los citan `docs/DEPLOY.md:2314-2368` (el runbook) y
`tests/test_cli.py`. El único uso conocido es el archivado de 203 anuncios del 30-ago, sin registro
en la base (`anuncio_retiro` y `anuncio_recreacion_intento` tienen 0 filas). Lo que David pierde es
archivar por terminal anuncios sin publicación. Esos anuncios no gastaron nada en 30 días. Para
sacar de circulación un anuncio con publicación ya existe el retiro de Fábrica
(`POST /api/fabrica/retiro/ejecutar`, `app/api_fabrica.py:766`), que pausa con registro y se regresa
con `retiro/reponer` y `retiro/recrear`. El retiro no busca anuncios sin publicación
(`retiro_anuncios.planea_retiro`, `app/retiro_anuncios.py:253-255`); si alguna vez hace falta archivar
uno de esos, se hace a mano en la consola de Amazon, que es lo mismo que hacía el CLI pero sin
prometer una reversa.

**c. Falla 10: ¿qué tan lejos está cada cuenta del corte de 20 páginas?** Desde la base solo se ven
las keywords: MX 2,719 y US 1,340, contando las archivadas, que el LIST también devuelve
(`apply_harvest.py:601-608`). Los negatives no están en la base. El tamaño de página no está medido
en el repo: `_lista_todos` manda el cuerpo vacío (`apply_harvest.py:427`) y Amazon usa su tamaño por
omisión. Con páginas de 1,000 el corte queda en 20,000 y las dos cuentas están lejos. Con páginas de
100 el corte queda en 2,000 y MX ya lo pasa en keywords. Los 8 harvests de MX terminaron bien, lo
que sugiere páginas grandes, pero no lo prueba. Por eso B-C mide antes de cambiar código (fila B-C.0,
sección F10, «Medición antes del cambio»): páginas, ítems y tamaño de la primera página de cada lista
en cada perfil. Con lo medido fija el tope en al menos cuatro veces la lista más larga. Sin medir, MX
podría quedar detenido en cada ciclo desde el despliegue: 2,719 keywords en páginas de 100 son 28.

**d. Falla 8: ¿la reconciliación de cada hora puede cerrar un «quitar» dudoso?** Sí, y ya sabe
hacerlo. `_PASOS_HUERFANOS` incluye `reversa_intencion` (`bloqueo_dueno_efecto.py:39`) y
`_cierra_huerfano` ya lo resuelve con la lista: ausente o ARCHIVED da `revertido`, presente da
`reversa_fallida` (`:317-322`). Lo único que lo impide hoy es que `_quitar_con_candado` sella
`reversa_fallida` ante cualquier excepción (`:265-275`) y `_readback_reversa` la sella ante una
lista fallida (`:280-283`), y `reversa_fallida` no es huérfano. Que la reconciliación corre lo
muestra la base: el bloqueo MX con intención a las 04:40:45 se cerró a las 05:17:02, el minuto 17
de la corrida horaria. Ojo: esa línea de cron no está en `tools/despliegue/crontab-orbit.txt`
(cruce con el bloque N de ARREGLOS 01).

## F3 Borrar la herramienta de terminal que archiva anuncios sin registro

`tools/archiva_inertes.py` no se borra: tiene su procedimiento en `docs/DEPLOY.md` y un primer lote
pendiente del visto bueno del dueño. Su falla 11 (`--reponer` sin intención guardada antes del HTTP)
va en ARREGLOS 05.

**Qué pasa hoy.** `orbit archivar-anuncios` archiva product ads sin intención durable; la línea
para recrearlos solo se imprime al final y se pierde si la relectura falla
(`app/ads/archivar.py:411-508`, `app/cli.py:240-291`). `reponer-anuncios` no mira si ya hay un
anuncio vivo y, corrido dos veces, duplica (`app/ads/archivar.py:316-408`).

**Forma elegida.** Borrar los dos comandos. Lo que queda para escribir product ads es el retiro con
registro (`app/retiro_anuncios.py`), que pausa en vez de archivar y cuya recreación ya escribe la
intención antes del POST y busca un anuncio vivo del sku antes de crear otro
(`app/retiro_anuncios.py:927-975`).

Qué se borra y qué se queda:

| Archivo | Se borra | Se queda (y por qué) |
| --- | --- | --- |
| `app/cli.py` | `_archivar_anuncios` (:240-291), `_reponer_anuncios` (:294-330), `_lineas_de` (:333-344, sin otro llamador), los subparsers `archivar-anuncios` (:678-699) y `reponer-anuncios` (:725-745), sus dos ramas en `main` (:819-823, :834-838), `MODO_ARCHIVADO_LIVE` (:65) y `archivar` del import (:53) | nada de esto tiene otro llamador |
| `app/ads/archivar.py` | `archivar_anuncios` (:411-508) y `resumen` (:689-695, solo lo usa el CLI); en el docstring del módulo, la ceremonia del archivado | `preparar_escritor`, `leer_anuncios`, `anuncios_vivos_del_grupo`, `reversa_de_fila`, `parsear_reversa`, `reponer_anuncios`, `pausar_anuncios`, `reactivar_anuncios`: los usan `retiro_anuncios`, `apply_producto` e `impulso_io` |
| `app/ads/write.py` | `archivar_product_ad` (:512-546), el par `("POST", "/sp/productAds/delete")` de `MUTATION_REQUEST_TYPES` (:112-113) y la mención de la limpieza en el docstring (:36-38) | `crear_product_ad`: es la recreación del retiro |
| `docs/DEPLOY.md` | la sección «Limpieza de product ads muertos (ORBIT 06)» (`:2314-2368`) y, en el párrafo de la reversa de 0011, la mención de los dos comandos (`:1555-1557`) | una nota corta que diga qué usar en su lugar; la sección de inertes (`:2246-2313`) no se toca |

Uso del llamador después del cambio: no hay llamador. `python -m app.cli archivar-anuncios ...`
sale con el error de argparse (código 2) y no manda nada a Amazon. El cliente de escritura rechaza
`POST /sp/productAds/delete` con `MutationNotAllowedError` antes de pedir token, como cualquier par
fuera de la lista.

**Forma descartada.** Arreglar los dos comandos: intención en una tabla nueva antes del HTTP, línea
de reversa guardada y chequeo de anuncio vivo antes de reponer. Pide una migración (choca con el
patrón de `comun.sh` que cambia TEMPORADA 01) y conserva una capacidad irreversible que no corre
desde el 30-ago. El dueño decidió borrar lo que producción no usa.

**Regla 7.** Borrar quita acciones irreversibles; no agrega ninguna. La recreación del retiro sigue
siendo la reversa de un anuncio archivado por fuera.

**Cruces.** El frente de la falla 2 (una sola puerta al cliente de escritura, otro plan) nombra
`app/cli.py:260, 306`, que este bloque borra: B-A debe entrar antes. ARREGLOS 03 edita en `app/cli.py`
el subparser `reputacion` y la rama de `main` que quedan pegados a los que borra B-A, y la sección de
`docs/DEPLOY.md` que sigue a la que B-A reemplaza: se cambia por contenido y se conservan sus cambios.
ARREGLOS 02 cambia `_bid_payload` de `app/ads/write.py`, otra función. TEMPORADA 01 no toca ninguno
de estos archivos.

## F4 Solo un negative ENABLED cuenta como bloqueo

**Qué pasa hoy.** `_ejecuta` adopta el primer negative no archivado que devuelve `_identidad`
(`bloqueo_dueno_efecto.py:219-223`) y sella «aplicado» sin POST aunque esté PAUSED. `_readback` y
`_cierra_huerfano` sí exigen ENABLED, pero sobre ese mismo primer ítem (`:237-238`, `:311-312`): si
hay uno PAUSED y otro ENABLED, pueden mirar el equivocado.

**Forma elegida.** Una sola regla en el módulo, usada por los tres lugares.

```python
# app/bloqueo_dueno_efecto.py
from app.apply_harvest import _coincidencias, _errores_de_ack, _lista_filtrada

def _negative_que_bloquea(items: list[dict], grupo_ext: str, termino: str) -> dict | None:
    """Solo un negative ENABLED bloquea: PAUSED no niega nada y ARCHIVED está muerto."""
    for item in _coincidencias(items, grupo_ext, termino):
        if str(item.get("state", "")).upper() == apply.ESTADO_WIRE_ENABLED:
            return item
    return None
```

- `_ejecuta`: `vivo = _negative_que_bloquea(items, grupo_ext, senal.termino)`. Con un PAUSED y
  ningún ENABLED no adopta: sigue al POST.
- `_readback`: `item = _negative_que_bloquea(...) if estado == "ok" else None`; si hay item, sella
  aplicado. Desaparece la comprobación de estado aparte.
- `_cierra_huerfano`, rama `intencion`: lo mismo.
- `_identidad` sale del import.

Ítems de ejemplo (los usa la prueba de producción de B-B.6):

```python
k1 = {"keywordId": "k1", "adGroupId": "9", "keywordText": "baño", "matchType": "NEGATIVE_EXACT", "state": "PAUSED"}
k2 = {"keywordId": "k2", "adGroupId": "9", "keywordText": "baño", "matchType": "NEGATIVE_EXACT", "state": "ENABLED"}
assert _negative_que_bloquea([k1], "9", "baño") is None
assert _negative_que_bloquea([k1, k2], "9", "baño") == k2
```

Si Amazon rechaza el POST porque ya existe el PAUSED (duplicado), el 207 trae el error por ítem y la
falla 9 lo sella `fallido` con el motivo de Amazon. La pantalla dice la verdad: no está bloqueado.

**Forma descartada.** Reactivar el negative PAUSED con `PUT /sp/negativeKeywords` y `state=ENABLED`.
Agrega un par a la lista cerrada del cliente (APPLY.md §8.1), que es decisión del dueño, y una reversa
nueva. La forma elegida usa solo mutaciones que ya existen.

**Cruces.** ARREGLOS 01 B/C cambió qué decide el motor sobre negatives de keywords propias; no toca
este módulo. TEMPORADA 01 no toca este módulo.

## F9 El bloqueo lee el rechazo por ítem del 207

**Qué pasa hoy.** `_ejecuta` descarta la respuesta del POST (`:225`). Un 207 con `error[]` queda
«por confirmar» y la reconciliación lo sella `fallido/sin_resultado` sin el motivo de Amazon. El
`quitar` también descarta la respuesta del delete (`:263`).

**Forma elegida.** Reusar el lector que ya existe, `apply_harvest._errores_de_ack`
(`apply_harvest.py:704`), el mismo que usan el harvest y la reconciliación.

```python
def _rechazo(resp) -> str | None:
    """El rechazo por ítem del 207, saneado y corto; None si Amazon no rechazó nada."""
    errores = _errores_de_ack(apply._json_seguro(resp))
    if not errores:
        return None
    return scrub(json.dumps(errores, ensure_ascii=False))[:500]

# en _ejecuta, después del POST que no lanzó:
rechazo = _rechazo(resp)
if rechazo is not None:
    _sella(conn, vid, "fallido", motivo="rechazo_amazon",
           http_status=resp.status_code, respuesta=rechazo)
    return Efecto(200, veredicto_id=vid, paso="fallido", motivo="rechazo_amazon")
```

En `quitar`, un rechazo por ítem del delete no basta para sellar: Amazon también rechaza borrar un
id que ya está archivado o que ya no existe, y ese es justo el caso que repara B-D. Con rechazo se
relee la lista: id ausente o ARCHIVED da `revertido`; id todavía vivo sella `reversa_fallida` con
`motivo="rechazo_amazon"` (Amazon dijo que no y el bloqueo sigue). En `aprobar` no hace falta
releer: se postea solo cuando la lista recién leída no tiene un ENABLED, y bajo el candado de la clave.

La pantalla. `app/static/js/cortes.js:139-140` hoy imprime el código crudo («fallido (http_400).»).
Con este bloque aparecen resultados nuevos, así que el mensaje pasa por las etiquetas:
`Orbit.etiqueta(paso, "paso_bloqueo")` y `Orbit.etiqueta(motivo, "motivo_bloqueo")`, con estas
entradas nuevas en `app/etiquetas.py`: `PASO_BLOQUEO` suma `"fallido": "No se aplicó"`,
`"anulado": "No se aplicó"`, `"revertido": "Quitado"`, `"rechazado": "Rechazado"` y
`"reanudacion": "Reanudado"`; `MOTIVO_BLOQUEO` suma `"rechazo_amazon": "Amazon lo rechazó"`,
`"sin_resultado": "Amazon no lo confirmó"`, `"sigue_presente": "Amazon todavía lo muestra"`,
`"senal_cambio": "la señal cambió"`, `"ya_bloqueada": "ya estaba bloqueado"` y
`"motor": "el motor ya lo está cortando"`.

Los motivos que hoy salen de `_fallo_de` y de `_lista_o` no tienen texto, y `formato.js:46-60` los
muestra como «Http 400», «Lista ambiguo» o «Sin respuesta» (también la tabla de bloqueos de
`app/templates/cortes.html:306`). `MOTIVO_BLOQUEO` suma también:

| Código | Texto que ve David |
| --- | --- |
| `sin_respuesta` | Amazon no respondió |
| `lista_ambiguo` | no se pudo leer la lista de Amazon |
| `lista_truncado` | Amazon no entregó la lista completa |
| `sin_externos` | Orbit no tiene los datos del grupo de anuncios en Amazon |
| `http_400`, `http_401`, `http_403`, `http_404`, `http_409`, `http_422`, `http_429`, `http_500`, `http_502`, `http_503`, `http_504` | Amazon respondió con error |
| `candado` | otro cambio de este término está en curso; vuelve a intentar en un minuto |
| `reconciliacion_pendiente` | Orbit todavía está confirmando en Amazon un cambio anterior de este término; vuelve a intentar en una hora |
| `senal_ausente` | esa propuesta ya no existe; recarga la página |
| `no_vive` | ese bloqueo ya no está activo; recarga la página |

En `cortes.js`, un motivo que empieza con `http_` y no tiene entrada se muestra como «Amazon respondió
con error». La rama de error del mismo `fetch` (un 409 trae el motivo como `detail`) pasa por
`Orbit.etiqueta(detalle, "motivo_bloqueo")` cuando el `detail` es un código (`/^[a-z_]+$/`); si no, usa
`mensajeError` como hoy. Un código sin entrada sale humanizado, como hoy hace `formato.js:46-60`.

**Forma descartada.** Un lector público en `app/ads/write.py` y migrar las diez copias (movimiento 4
de Estructura). Toca copias dentro de `app/apply.py` y `app/apply_cola.py`, que reescribe TEMPORADA
01. Queda para un plan posterior; este bloque no agrega copia nueva.

## F8 Un «quitar» dudoso queda abierto y lo cierra la reconciliación

**Qué pasa hoy.** `_quitar_con_candado` trata cualquier excepción del delete como `reversa_fallida`
(`:261-275`), sin releer. `_readback_reversa` sella `reversa_fallida` si la lista falla (`:280-283`)
o si todavía ve el negative (`:288`). `reversa_fallida` no está en `_PASOS_HUERFANOS`, así que nadie
la vuelve a mirar.

**Forma elegida.** La misma regla que ya sigue la ida (`_ejecuta`, `:226-232`, y `_readback`,
`:241`): solo un «no» de Amazon es definitivo; la duda deja la intención abierta y la reconciliación
de cada hora es la única que convierte duda en veredicto.

```python
# _quitar_con_candado, después de escribir reversa_intencion
try:
    cliente = _cliente(origen.plataforma, transport)
    resp = cliente.borrar_negative(kid)
except AdsApiError as exc:
    if exc.status is not None and 400 <= exc.status < 500:
        ... sella reversa_fallida con _fallo_de(exc), como hoy; return
    _anota_incierto(vid, exc)          # 5xx o red: pudo aplicarse
except Exception as exc:              # el cliente no se construyó o el token falló: no salió nada
    ... sella reversa_fallida con _fallo_de(exc), como hoy; return
else:
    rechazo = _rechazo(resp)
return _readback_reversa(conn, cliente, origen, kid, externos[0], rechazo=rechazo)
# (rechazo = None en la rama de duda)

def _readback_reversa(conn, cliente, origen, kid, grupo_ext, *, rechazo=None) -> Efecto:
    items, estado = _lista_o(cliente, grupo_ext)
    item = _por_id(items, kid) if estado == "ok" else None
    if estado == "ok" and (item is None or str(item.get("state", "")).upper() == "ARCHIVED"):
        _sella(conn, origen.id, "revertido", negative_keyword_id=kid)
        return Efecto(200, veredicto_id=origen.id, paso="revertido")
    if rechazo is not None and estado == "ok":
        _sella(conn, origen.id, "reversa_fallida", motivo="rechazo_amazon", respuesta=rechazo)
        return Efecto(200, veredicto_id=origen.id, paso="reversa_fallida", motivo="rechazo_amazon")
    return Efecto(200, veredicto_id=origen.id, paso="reversa_intencion", motivo="por_confirmar")
```

`_cierra_huerfano` no cambia en la rama de reversa: ya da `revertido` o `reversa_fallida
sigue_presente`. La pantalla muestra «Quitándose» mientras tanto (`app/etiquetas.py:390`).

Prototipo. Monté esta forma y la de F4 y F9 como parche de prueba fuera del repo y corrí
`tests/test_bloqueo_dueno_efecto.py`, `tests/test_bloqueo_dueno.py`, las tres reproducciones
`test_sub_a_*` del bloqueo y una prueba nueva (delete con 503, primera relectura caída,
reconciliación con la lista ya ARCHIVED) y otra de un delete rechazado por ítem sobre un id ya
archivado (da `revertido`): 43 pasaron. Sin el parche, las cuatro de la falla fallan.

**Forma descartada.** Releer solo después del 5xx y sellar `reversa_fallida` si la relectura no
confirma. Deja atorado el caso en que la relectura también falla, que es el mismo 5xx de Amazon un
segundo después.

**Cruces.** ARREGLOS 01 K hizo lo mismo para un bid dudoso en `app/apply.py`; este bloque no lo toca.

## F12b La sonda no sella «ok» un 207 con rechazo

**Qué pasa hoy.** `tools/smoke_apply.py:498-501` sella `ok` cualquier 2xx.

**Forma elegida.** Después del ack, `errores = _errores_de_ack(ack)` (importado de
`app.apply_harvest`). Si hay errores, `sella_probe(conn, id_attempt, ack,
f"fallo:rechazo_por_item: {str(errores)[:300]}")`, commit y `paso.update(ok=False, error="rechazo
por ítem", http=_evidencia_respuesta(resp))`. Prototipo: con el parche, `tests/test_smoke_apply.py` y
la reproducción pasan (24).

**Forma descartada.** Borrar la sonda. Es el procedimiento que APPLY.md §11d manda para fijar la
forma de una mutación nueva (última corrida 12-sep, 31 filas `probe`), y el plan de la falla 2 la va a
necesitar para la fábrica.

## F10 Una lista incompleta nunca autoriza un POST ni un borrado

**Qué pasa hoy.** `apply_harvest._lista_todos` (`:418-437`) lee hasta 20 páginas y, si el
`nextToken` sigue vivo, devuelve lo leído como si fuera todo. La usan `_reversa_automatica`
(`:1510`, `:1521`), `_paso_negative` (`:1570`), `_paso_keyword` (`:1650`, `:1673`), `_paso_readback`
(`:1802`) y `apply_harvest_reconciliacion._reconcilia_negativas` (`:393`).

**Forma elegida.** `_lista_todos` dice que no leyó todo lanzando `ListaIncompleta`, que hereda de
`AdsApiError`. No cambia ningún llamador: el harvest ya trata `AdsApiError` como ambiguo («sube»,
el job queda en su fase, `apply_harvest.py:2180-2186`), la reconciliación de negatives ya lo atrapa y
sigue el próximo ciclo (`apply_harvest_reconciliacion.py:392-397`), la reconciliación de harvest lo
atrapa por job (`:647-650`) y `_reversa_automatica` lo atrapa como `AdsClientError` y no borra
(`apply_harvest.py:1532-1533`).

```python
class ListaIncompleta(AdsApiError):
    """Amazon no entregó la lista completa: no prueba que algo falte."""

def _lista_todos(cliente, path: str, profile_id: str | int) -> list[dict]:
    contenedor = _CONTENEDORES_LIST[path]
    items, token, vistos = [], None, set()
    for _ in range(TOPE_PAGINAS_LIST):
        body = {"nextToken": token} if token else {}
        data = ... (igual que hoy, ilegible lanza AdsApiError)
        items.extend(x for x in data.get(contenedor) or [] if isinstance(x, dict))
        estado = _valida_token_list(data.get("nextToken"))
        if estado == "fin":
            return items
        if estado == "ambiguo":
            raise ListaIncompleta(f"nextToken ilegible: {contenedor}")
        token = data.get("nextToken")
        if token in vistos:
            raise ListaIncompleta(f"nextToken repetido: {contenedor}")
        vistos.add(token)
    raise ListaIncompleta(f"mas de {TOPE_PAGINAS_LIST} paginas: {contenedor}")
```

Qué ve el dueño: si una cuenta pasa del corte, el harvest se detiene en su fase con el aviso del ciclo
en vez de duplicar un negative o borrar una keyword recién creada. El primer ciclo que lo encuentra
corta su fase de apply, igual que hoy un 5xx en el harvest; los siguientes lo saltan en la
reconciliación. Para que eso no pase el día del despliegue, el tope sale de la medición de abajo.

**Medición antes del cambio (fila B-C.0).** Con `ListaIncompleta`, una lista más larga que el tope ya
no se trunca en silencio: detiene el harvest de esa cuenta. Por eso, antes de tocar código, se mide
desde el contenedor con el cliente de lectura (`AdsClient` rechaza PUT, PATCH y DELETE, y
`list_objects` solo acepta los paths de lectura de `LIST_REQUEST_TYPES`):

```python
# docs/evidencia/arreglos-04/B-C/mide_paginas.py
# Correr: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-04/B-C/mide_paginas.py
from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import perfiles_aceptados
from app.apply_harvest import _CONTENEDORES_LIST, TOPE_PAGINAS_LIST

TOPE_MEDICION = 500
cliente = AdsClient(AdsCredentials.from_secrets_dir())
for perfil in perfiles_aceptados(cliente):
    for path, contenedor in _CONTENEDORES_LIST.items():
        paginas, items, primera, token, fin = 0, 0, None, None, False
        while paginas < TOPE_MEDICION:
            body = {"nextToken": token} if token else {}
            datos = cliente.list_objects(path, body, profile_id=perfil.profile_id).json()
            paginas += 1
            n = len(datos.get(contenedor) or [])
            items += n
            primera = n if primera is None else primera
            token = datos.get("nextToken")
            if not token:
                fin = True
                break
        print(f"{perfil.platform}|{path}|{paginas}|{items}|{primera}|{'fin' if fin else 'sin_fin'}")
        if paginas > TOPE_PAGINAS_LIST:
            print(f"corte_callado|{perfil.platform}|{path}|{paginas} páginas contra el tope de {TOPE_PAGINAS_LIST}")
```

Con la salida en `docs/evidencia/arreglos-04/B-C/paginas.txt`, `TOPE_PAGINAS_LIST` queda en
`max(20, 4 × páginas de la lista más larga)`, redondeado hacia arriba a la decena. Una línea
`corte_callado` dice que hoy el harvest ya trabaja con una lista cortada sin saberlo: se anota en la
evidencia del PR y el tope nuevo lo cubre. Una lista `sin_fin` (más de 500 páginas) para el bloque.
B-D ya no mide páginas.

Prototipo. Con esta forma montada como parche, `tests/test_apply_harvest.py`, `test_d3_adversary.py`,
`test_fabrica_f2_hermanas.py`, `test_cycle_apply.py`, `test_apply_cola.py` y la reproducción del
negative huérfano: 238 pasan y 2 fallan. Una es la reproducción de la keyword, que ahora sale por
`ListaIncompleta` antes del POST de la keyword (la prueba promovida espera esa excepción y cero
borrados). La otra es `test_fabrica_f2_hermanas.py::test_previo_truncado_cero_post_y_pendientes`: su
transporte trunca todas las listas de negatives, y el paso del negative en origen pasaba solo porque
el corte callado lo dejaba postear. Se arregla la prueba, no el código: el truncado se limita al LIST
filtrado por ad group.

**Formas descartadas.**
- Cambiar los siete usos a `_lista_completa` (`:517`), que valida cada ítem. Con ese parche fallan 14
  pruebas que hoy pasan: la validación estricta convierte en «incompleta» filas que el harvest hoy sí
  usa, y eso cambia el comportamiento en producción más allá de la falla.
- El movimiento 2 completo (un lector único con filtro por ad group, también para
  `apply._fila_de_readback`). Toca `app/apply.py`, de TEMPORADA 01. Queda para un plan posterior.

**Cruces.** El frente de la falla 7 (otro plan) toca `apply_harvest_reconciliacion.py`; este bloque
no cambia ese archivo. TEMPORADA 01 no toca `apply_harvest.py`.

## Revisión en Amazon de los bloqueos aplicados (pregunta a)

**Regla.** B-D solo repara lo que Amazon confirma: un negative que la lectura devuelve PAUSED,
ARCHIVED o AUSENTE. Si Amazon no respondió (`LECTURA_*`), no se sabe si el negative bloquea, y
`quitar` lo archivaría sin vuelta atrás: esa línea nunca dispara nada, se anota y se vuelve a leer al
final del bloque. Una clave sin señal vigente no se quita: se anota para que David la revise a mano.

**Qué se lee.** Un script de solo lectura, `docs/evidencia/arreglos-04/B-D/lee_amazon.py`, corrido
con `bash docs/evidencia/arreglos-01/en_prod.sh`. A diferencia de los scripts de funciones puras que
corre esa ayuda, este lee: la base con `ORBIT_DSN_READ` y `read_only=True` (como `lee_prod.sh`) y
Amazon con `AdsClient`, el cliente de lectura, que rechaza PUT, PATCH y DELETE. No mide páginas: eso
lo hace B-C.0.

```python
# docs/evidencia/arreglos-04/B-D/lee_amazon.py
# Correr: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-04/B-D/lee_amazon.py
import os
import time

import psycopg

from app import bloqueo_dueno_efecto as bde
from app.ads.client import AdsClient
from app.ads.config import AdsCredentials
from app.ads.structure import perfiles_aceptados

_ULTIMO = """select v.id, v.plataforma::text, v.ad_group_id, v.termino, v.termino_sha256,
                    ult.negative_keyword_id
               from bloqueo_veredicto v
               join lateral (select e.paso, e.negative_keyword_id from bloqueo_efecto e
                              where e.veredicto_id = v.id
                              order by e.created_at desc, e.id desc limit 1) ult on true
              where v.tipo = 'aprobado' and ult.paso = %s
              order by v.plataforma, v.created_at"""
_SENAL = """select g.senal_id from jev_senal_vigente g join jev_senal s on s.id = g.senal_id
             where g.ad_group_id = %s and s.termino_sha256 = %s
               and g.vigente and g.lectura = 'ajena'"""

cliente = AdsClient(AdsCredentials.from_secrets_dir())
perfiles = {p.platform: p.profile_id for p in perfiles_aceptados(cliente)}


class _Sellado:  # adapta el cliente de lectura a _lista_filtrada
    def __init__(self, pid):
        self.pid = pid

    def list_sellado(self, path, body):
        return cliente.list_objects(path, body, profile_id=self.pid)


def _lee(pid, grupo_ext):
    # _lista_o envuelve _lista_filtrada y convierte un error de Amazon en "ambiguo"
    items, estado = bde._lista_o(_Sellado(pid), grupo_ext)
    if estado != "ok":                      # una segunda oportunidad antes de rendirse
        time.sleep(30)
        items, estado = bde._lista_o(_Sellado(pid), grupo_ext)
    return items, estado


with psycopg.connect(os.environ["ORBIT_DSN_READ"]) as conn:
    conn.read_only = True
    for vid, plat, grupo, termino, sha, kid in conn.execute(_ULTIMO, ("aplicado",)).fetchall():
        senal = conn.execute(_SENAL, (grupo, sha)).fetchone()
        senal = str(senal[0]) if senal else "-"
        externos = bde._externos(conn, grupo)
        if externos is None:
            print(f"{plat}|{vid}|{grupo}|{kid}|LECTURA_sin_externos|{senal}|-")
            continue
        items, estado = _lee(perfiles[plat], externos[0])
        if estado != "ok":
            print(f"{plat}|{vid}|{grupo}|{kid}|LECTURA_{estado}|{senal}|-")
            continue
        item = bde._por_id(items, kid)
        estado_id = "AUSENTE" if item is None else str(item.get("state", "")).upper()
        otro = bde._negative_que_bloquea(items, externos[0], termino)   # de B-B
        otro = str(otro["keywordId"]) if otro and str(otro.get("keywordId")) != str(kid) else "-"
        print(f"{plat}|{vid}|{grupo}|{kid}|{estado_id}|{senal}|{otro}")
    for (vid, *_resto) in conn.execute(_ULTIMO, ("reversa_fallida",)).fetchall():
        print(f"reversa_fallida|{vid}")
```

Cada línea de bloqueo es `plataforma|veredicto|grupo|id|estado|senal_vigente|otro_enabled`. Se usa el
`senal_id` vigente y no el de la aprobación vieja: `_vigente_ajena` exige que sea el mismo id
(`bloqueo_dueno_efecto.py:423-433`), y hoy ninguno de los 48 lo es. `otro_enabled` es el id de otro
negative ENABLED del mismo término en el mismo ad group (con B-B, `_negative_que_bloquea`): si existe,
el término ya está bloqueado. Las líneas `reversa_fallida|veredicto` son bloqueos que David pidió
quitar y Amazon no dejó (falla 8): siguen bloqueando, así que solo se anotan para él.

**Reparación (fila B-D.2).** El bucle, tal cual, con los valores tomados de cada línea y sin escribir
nada a mano:

```bash
A=docs/evidencia/arreglos-04/B-D
grep -E '^amazon_(mx|us)[|]' $A/antes.txt | while IFS='|' read -r plat vid grupo kid estado senal otro; do
  case "$estado" in
    ENABLED) continue ;;
    PAUSED|ARCHIVED|AUSENTE) ;;
    *) echo "$vid|$plat|$estado|sin lectura de Amazon: no se tocó; se vuelve a leer en B-D.3" >> $A/reparacion.txt
       continue ;;
  esac
  if [ "$otro" != "-" ]; then
    echo "$vid|$plat|$estado|bloqueado por otro negative $otro: no se tocó" >> $A/reparacion.txt
    echo "$plat|$vid|el término está bloqueado por otro negative ($otro), no por el de Orbit" >> $A/revisar.txt
    continue
  fi
  if [ "$senal" = "-" ]; then
    echo "$vid|$plat|$estado|sin señal vigente: no se tocó" >> $A/reparacion.txt
    echo "$plat|$vid|en Amazon el negative está $estado y no hay señal reciente para volver a bloquearlo" >> $A/revisar.txt
    continue
  fi
  r=$(bash docs/evidencia/arreglos-01/escribe_api.sh /api/jev-bloqueo/quitar "{\"veredicto_id\": \"$vid\"}") || true
  echo "$vid|$plat|$estado|quitar: $r" >> $A/reparacion.txt
  if ! echo "$r" | grep -q '"paso": *"revertido"'; then
    echo "$vid|parar: el quitar no quedó revertido" >> $A/reparacion.txt
    echo "select paso, motivo, created_at from bloqueo_efecto where veredicto_id = '$vid' order by created_at desc, id desc limit 1" \
      | bash docs/evidencia/arreglos-01/lee_prod.sh >> $A/reparacion.txt
    break
  fi
  r=$(bash docs/evidencia/arreglos-01/escribe_api.sh /api/jev-bloqueo/aprobar "{\"senal_id\": \"$senal\"}") || true
  echo "$vid|$plat|$estado|aprobar $senal: $r" >> $A/reparacion.txt
done
grep '^reversa_fallida|' $A/antes.txt | sed 's/$/|Amazon no dejó quitar este bloqueo; sigue bloqueando/' >> $A/revisar.txt || true
[ -s $A/reparacion.txt ] || echo "nada que reparar: $(grep -cE '^amazon_(mx|us)[|]' $A/antes.txt) bloqueos, todos ENABLED" > $A/reparacion.txt
```

`escribe_api.sh` lee su JSON de un heredoc, así que no consume la entrada del `while`. Con B-B
desplegado, el `aprobar` no adopta nada que no sea ENABLED y postea uno nuevo.

Qué registro deja cada reparación y cómo se deshace (regla 7):

| Paso | Registro | Cómo se deshace |
| --- | --- | --- |
| `quitar` del veredicto viejo | `bloqueo_veredicto` tipo `quitado`; `bloqueo_efecto` `reversa_intencion` y `revertido`; su línea en `reparacion.txt` | No se deshace en Amazon (archivar no tiene vuelta atrás) y no hace falta: el negative estaba PAUSED (no negaba nada), ARCHIVED o ya no existía, así que no cambia qué se gasta |
| `aprobar` de la señal vigente | `bloqueo_veredicto` tipo `aprobado`; `bloqueo_efecto` `intencion` y `aplicado` con el id nuevo; su línea en `reparacion.txt` | `quitar` de su veredicto, el botón «Quitar» de Propuestas |

**Lista para David.** `docs/evidencia/arreglos-04/B-D/revisar.txt` junta lo que no se reparó solo: las
claves sin señal vigente (en Amazon no bloquean y Orbit no puede volver a proponerlas hasta que
reaparezca su señal), las bloqueadas por otro negative, las `reversa_fallida` y las que en B-D.3
cambiaron de `LECTURA_*` a PAUSED, ARCHIVED o AUSENTE. El reporte del bloque la copia en lenguaje llano.

**Lectura final (fila B-D.3).** `comprueba.sh` vuelve a leer y falla si queda un bloqueo vivo que no
está ENABLED y que nadie anotó:

```bash
#!/usr/bin/env bash
# docs/evidencia/arreglos-04/B-D/comprueba.sh: solo lee. Sale 1 si un bloqueo vivo no ENABLED no está anotado.
set -euo pipefail
A=docs/evidencia/arreglos-04/B-D
salida=$(bash docs/evidencia/arreglos-01/en_prod.sh $A/lee_amazon.py)
printf '%s\n' "$salida"
printf '%s\n' "$salida" | grep -E '^amazon_(mx|us)[|]' | awk -F'|' '$5 != "ENABLED" {print $2}' \
  | while read -r v; do grep -q "^$v[|]" $A/reparacion.txt || { echo "sin anotar: $v" >&2; exit 1; }; done
```

Es también la `prueba-real` de B-D.

## Lo que no se resolvió

- Si alguno de los 48 bloqueos está sobre un negative pausado. Lo dice B-D.
- Un «aprobar» que adopta un negative ENABLED ajeno, y un «quitar» posterior que lo archiva. Escenario:
  el negative guardado de «centenario de oro puro» (MX) está PAUSED, y en el mismo ad group hay otro
  ENABLED del mismo término que puso el harvest o alguien a mano. El término ya está bloqueado. Si
  David aprueba la señal, `_ejecuta` (con B-B) adopta ese ENABLED y lo registra como suyo; cuando
  después lo quite en Propuestas, `quitar` archiva en Amazon un negative que no era del dueño, sin
  vuelta atrás, y el término vuelve a gastar. B-D no entra en ese camino (salta las claves con
  `otro_enabled`), pero la pantalla sí. Va en ARREGLOS 05: adoptar solo un negative cuyo origen
  conste, o que `quitar` no archive lo adoptado.
- `bloqueo_dueno_efecto` importa `apply._cliente_ajuste` y `apply_cola._SQL_EXTERNALES_GRUPO`. Si
  TEMPORADA 01 los renombra, B-B debe seguir el nombre nuevo. Su diseño no los nombra.
