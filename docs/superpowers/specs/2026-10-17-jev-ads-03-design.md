# Diseño de JEV ADS 03: el dueño bloquea lo que Jev marca como ajeno

**Estado:** sellado por el dueño el 2026-10-17. Es la fila E.2 de
[JEV ADS 02](../../../plans/jev-ads-02.md), que se cerró como "no se
construye" porque no había medición. La medición ya existe
(`docs/evidencia/jev-ads-03/diseno/`). El
[diseño de JEV ADS 02](2026-10-04-jev-ads-02-design.md) sigue vigente para la
señal; aquí solo va el efecto. Precedencia: `docs/CONTEXTO.md` >
`docs/APPLY.md` > diseño 02 > este documento > plan.

## Lo que el dueño selló

Después de la revisión a ciegas y del análisis de oro con quilates
(2026-10-17, en chat con el lead):

1. **La meta baja de 95% a 70%.** "hasta 2000 y meta a 70% esta bien". El 95%
   de E.2 era una propuesta del autor de JEV ADS 02, sin derivación y sin sello.
2. **Basta con 2,000 MXN al año de ahorro.** "si hasta 5000 me parece
   suficiente, hasta 2000". Hoy las `ajena` vigentes gastan 976 MXN en la
   ventana de cortes en MX.
3. **MX y US desde el inicio.** "mexico y US siempre".
4. **Las propuestas se ven en `/cortes`.**
5. **Rechazar no hace nada en Amazon.** El rechazo se recuerda. El dueño
   preguntó "las que yo no concuerde simplemente las rechazo y ya no?" y aceptó
   la regla de abajo.

## Problema

La señal `ajena` dice que ningún producto anunciado hoy en el grupo le sirve a
la búsqueda, con el roster probado y cero ventas en todo el historial. Hoy el
dueño la ve en `/gasto-sin-venta` y, si quiere bloquearla, tiene que ir a
Amazon a mano. El motor no la toma, porque una señal no decide (Fase 4 de
`docs/CONTEXTO.md`).

Jev acierta 85% en `ajena` (46/54 tras el oro). No basta para que actúe solo,
y sí basta para proponer. El dueño filtra el resto con un clic.

Cuatro restricciones chocan:

- **No por la cola.** `docs/CONTEXTO.md` dice "Solo cortes en la cola; nada
  nuevo se cuelga de ella", y la liberación re-evalúa la regla económica y
  descartaría el bloqueo (`plans/jev-ads-02.md`, "Los efectos siguen
  condicionados").
- **No es un botón «aprobar».** `plans/cortes-ui-01.md` rechazó aprobar
  propuestas del motor porque "no actuar YA es aprobar". Aquí no hay propuesta
  del motor. Es una acción que el motor no propone, y el dueño la inicia.
- **El motor no depende de Jev.** Nada de esto entra a `cycle`, `apply_cola`
  ni `app/optimizer`.
- **Un clic puede llegar tarde.** Entre que el dueño ve la fila y la aprueba
  pudo llegar una venta. La aprobación cita una señal y se anula sola si esa
  señal ya no es la vigente.

## Uso

```text
GET  /cortes
     Debajo de las propuestas del motor, una sección por plataforma:
     "Búsquedas que Jev marca como ajenas". Cada fila: término, grupo y
     campaña, gasto y clics de la ventana, la frase de la señal y los botones
     Bloquear y Rechazar. Debajo, "Tus bloqueos", cada uno con Quitar.
     Sin el interruptor solo queda "Tus bloqueos", para poder quitarlos.

POST /api/jev-bloqueo/aprobar     {"senal_id": "..."}
POST /api/jev-bloqueo/rechazar    {"senal_id": "..."}
POST /api/jev-bloqueo/quitar      {"veredicto_id": "..."}
POST /api/jev-bloqueo/reanudar    {"plataforma": "amazon_mx"}
     Las cuatro con el header x-orbit-token, como el resto de `api_write`.
```

## Forma

### Dos módulos, uno por conocimiento

- `app/bloqueo_dueno.py` es puro más lecturas. Decide qué se propone, la
  huella de catálogo, el rechazo recordado y la pausa. No importa
  `app.ads.write`.
- `app/bloqueo_dueno_efecto.py` escribe en Amazon. Es el único importador
  nuevo de `app.ads.write` y entra a `PERMITIDOS_IMPORTAR_ADS_WRITE` con su
  razón. Lo importan `app/api_write.py` y `app/cli.py`, este con import tardío
  dentro del subcomando. El CLI escribe con `ORBIT_DSN_ADMIN`.

Ninguno se llama `jev_*` ni importa módulos `jev_*`. Leen
`jev_senal_vigente` y `jev_senal` con SQL crudo, como prevé la costura del
diseño 02 ("Costuras para los efectos"). La vista solo trae `senal_id`,
`plataforma`, `ad_group_id`, `termino`, `lectura`, `created_at` y `vigente`.
`termino_sha256`, `roster_sha256`, gasto y clics salen de unir `jev_senal`
por `id`. `termino_sha256` se copia de esa fila y nunca se recalcula. La guarda
de imports de Jev no cambia. La frase de la señal la arma `api_dashboard` con
`app.jev_vista`, como ya hace en `/cortes`.

`ad_group_id` es el id interno de `ad_entity`, igual que en `jev_senal`. Los ids
de Amazon del grupo y de la campaña se resuelven al aprobar con
`_SQL_EXTERNALES_GRUPO` de `app/apply_cola.py`.

**Excepción a `docs/APPLY.md` §8.3.** Ese apartado dice que solo
`app/apply.py` y `tools/smoke_apply.py` construyen el cliente de escritura, con
`modo_confirmado` re-resuelto por decisión. El bloqueo es una acción del dueño,
no una decisión del motor. Resuelve el modo igual que `aplica_ajuste_campana` y
no consume la quota `negative` del motor. La fila B.3 del plan agrega esta
excepción a §8.3.

### Persistencia (migración 0074)

Dos tablas append-only, con el patrón de triggers de 0052:

- `bloqueo_veredicto`. Una fila por decisión del dueño: `id`, `plataforma`,
  `ad_group_id` (FK a `ad_entity`), `termino`, `termino_sha256`, `senal_id`
  (FK a `jev_senal`), `huella_catalogo`, `origen_id` (FK a la misma tabla),
  `tipo` en `aprobado`, `rechazado`, `quitado` o `reanudacion`, y
  `created_at`. Una `reanudacion` lleva solo `plataforma`. Un `quitado` cita en
  `origen_id` el `aprobado` que quita. Un CHECK obliga a que las demás traigan
  grupo, término, señal y huella.
- `bloqueo_efecto`. Los pasos de un `aprobado` en Amazon: `veredicto_id`,
  `paso`, `intento`, `negative_keyword_id`, `http_status`, `respuesta`
  (redactada), `motivo` y `created_at`. Un CHECK por paso exige su evidencia.
  `aplicado`, `reversa_intencion` y `revertido` exigen `negative_keyword_id`.
  `fallido`, `anulado` y `reversa_fallida` exigen `motivo`.

Permisos: INSERT a `app_admin`, SELECT a `app_admin` y `app_read`. REVOKE a
`app_decide`, `app_ingest` y `app_jev`. Un bloque `DO` al final aborta si el
ciclo puede leerlas, como en 0052. La reversa de 0074 se aplica antes que la
de 0052, por la FK a `jev_senal`.

### El efecto es una máquina de estados

```text
aprobado ──> intencion ──> aplicado ──> reversa_intencion ──> revertido
   │             │            ▲                    └────────> reversa_fallida
   │             ├──> fallido  └── (quitar otra vez) ─────────────┘
   └──> anulado
```

El estado de un `aprobado` es el último paso de sus efectos. Un `fallido` no se
reintenta solo. El dueño lo vuelve a aprobar, y eso crea un veredicto nuevo.

Un bloqueo está **vivo** cuando su último paso es `intencion`, `aplicado`,
`reversa_intencion` o `reversa_fallida`. En los cuatro el negative existe en
Amazon o puede existir. Quitar se permite desde `aplicado` y desde
`reversa_fallida`.

### Qué se propone

Una fila de `jev_senal_vigente` se propone cuando cumple todo esto:

1. `vigente` y `lectura = 'ajena'`.
2. No hay un bloqueo vivo para `(plataforma, ad_group_id, termino_sha256)`.
3. No hay rechazo recordado con la misma huella (abajo).
4. El motor no tiene la misma clave de efecto `(platform, ad_entity_id,
   'term_cut', search_term)` en `apply_queue`, donde `ad_entity_id` es el
   `ad_group_id` y `search_term` es el término crudo, como en
   `jev_senales._propuestas`. Ocultan la propuesta los estados
   `pending_veto`, `released` y `applying`; un `applied` sin reversa aplicada;
   y un `vetoed` dentro de su veto durable de 30 días, porque el dueño ya dijo
   que no se corta. Si el motor revirtió su negative, la búsqueda vuelve a
   proponerse.
5. La plataforma no está en pausa.

Orden por gasto de la ventana, de mayor a menor.

### El rechazo se recuerda por huella de catálogo

`huella_catalogo` se calcula con el roster de la señal (`jev_roster` por
`roster_sha256`). `miembros.censo` es una lista de
`[anuncio_ids, producto_id, listing_ids, estados]`, y `jev_roster.ficha_version_ids`
trae las fichas. La receta:

1. Los `producto_id` distintos del censo (posición 1).
2. Por cada uno, el contenido de su ficha en `jev_ficha_version`: `hechos` y
   `desconocidos`, sin `aprobador`, `observado_at`, `revisar_antes_de` ni
   `listings`. Un producto sin ficha aporta `null`.
3. JSON canónico de la lista ordenada por `producto_id`, y su sha256.

Un rechazo, o un bloqueo quitado, oculta la búsqueda en ese grupo mientras la
huella sea la misma. La búsqueda vuelve a proponerse en dos casos: entra o sale
un producto del grupo, o cambia lo que dice una ficha. Pausar o activar un
anuncio no la regresa, porque el censo cuenta `PAUSED` como anunciado. Renovar
una ficha sin cambiar su texto tampoco la regresa. Archivar el último anuncio
de un producto sí, porque el producto sale del roster.

No se usa `roster_sha256` porque incluye el estado de los anuncios, y una
pausa soltaría el rechazo. No se usa el `sha256` de la ficha porque incluye
`revisar_antes_de`, y una renovación soltaría el rechazo. No se usa
`insumos_sha256` porque cambia cada día.

### La pausa es una abstención

Por plataforma, se toman los veredictos `aprobado` y `rechazado` posteriores
a la última `reanudacion`, ordenados por `created_at`. Un `aprobado` que después
se quitó cuenta como rechazo, en su lugar original. Lo que pasó en Amazon
(`anulado`, `fallido`) no cuenta: la pausa mide el juicio del dueño sobre Jev,
no a Amazon. Si hay 20 o más y en los últimos 30 la proporción de aprobados es
menor a 70%, la plataforma queda en pausa:

- `/cortes` muestra "Pausado: aprobaste 12 de las últimas 30 (40%)" y el
  botón Reanudar, sin filas.
- `aprobar` responde 409.
- Reanudar escribe una `reanudacion` y la cuenta empieza de cero.

Los 70% son del dueño. La ventana de 30 y el mínimo de 20 son propuestas del
autor, sin derivación.

### Aprobar

Todo dentro de un candado de sesión,
`pg_try_advisory_lock(hashtext('bloqueo:<ad_group_id>:<termino_sha256>'))`,
que se sostiene a través del commit de la intención, el LIST, el POST y el
readback, y se suelta al final, como `_bloqueados` en `app/retiro_anuncios.py`.
Un candado de transacción no sirve: se soltaría en el commit de la intención y
un segundo clic trataría esa intención como huérfana. Si el candado está
tomado, 409. Sin intención huérfana, el cliente de Amazon se construye después
del commit de la intención, así que los pasos 1, 2, 4 y 5 no hacen ningún HTTP,
tampoco el de `/v2/profiles`.

1. **Interruptor.** `jev.bloqueo` debe ser exactamente `true` en el
   `config_version.settings` vigente. Si no, 409 y nada se escribe.
2. **Pausa.** Plataforma en pausa, 409.
3. **Reconciliar** los `intencion` huérfanos de esa clave (abajo). Es el único
   paso previo al ledger que puede llamar a Amazon: construye su propio cliente
   solo si hay un huérfano, y ese huérfano ya tiene su ledger. Sin huérfano no
   hay HTTP.
4. **La señal sigue igual.** `senal_id` es la fila vigente de
   `(ad_group_id, termino_sha256)`, con `vigente` y `lectura = 'ajena'`. Si no,
   se escribe `aprobado` + `anulado` con motivo `senal_cambio`, sin HTTP.
5. **Nadie lo cortó.** Si ya hay bloqueo vivo, `anulado` con motivo
   `ya_bloqueada`. Si el motor tiene la clave activa, `anulado` con motivo
   `motor`. Sin HTTP.
6. **Ledger antes del HTTP.** Se escriben `aprobado` + `intencion` y se hace
   commit.
7. **LIST por identidad.** Grupo, texto y `NEGATIVE_EXACT`, sin `ARCHIVED`,
   con `_lista_filtrada` y `_identidad` de `app/apply_harvest.py`. Si la lista
   no sale `ok` (`truncado` o `ambiguo`), `fallido` con motivo
   `lista_<estado>` y sin POST. Si ya existe, `aplicado` con ese id y sin
   POST.
8. **POST.** `AdsWriteClient.crear_negative_exacto` con el perfil de la
   plataforma y `modo_confirmado="live"`, como `aplica_ajuste_campana`. Un
   4xx, incluido un 429 agotado, sella `fallido` con `http_<status>` y sin
   readback: Amazon rechazo y no proceso. Un 5xx o un corte de red no sella
   `fallido`: se anota el status y el cuerpo ya redactados, y sigue el readback.
   Un error que ocurre antes de enviar el POST (token, guard) sella `fallido`.
9. **Readback.** LIST por identidad. Si aparece `ENABLED` con id, `aplicado`.
   Si no, no hay paso nuevo. La respuesta es 200 con `paso=intencion` y
   `motivo=por_confirmar`. El ledger sigue en la `intencion` de antes del HTTP.

### Rechazar

Interruptor y pausa como al aprobar (409). La señal citada debe ser la vigente
y `ajena`; si no, 409 y nada se escribe, porque la fila ya no está en pantalla.
Escribe un `rechazado`. Sin HTTP.

### Quitar

Mismo candado, sin interruptor ni pausa. Se permite desde `aplicado` y desde
`reversa_fallida`. Reconcilia, escribe un veredicto `quitado` con `origen_id` y
el paso `reversa_intencion` con el `negative_keyword_id` del ledger (nunca uno
del body), hace commit, llama a `borrar_negative` y hace readback por ese
`negative_keyword_id`. Si no aparece o aparece `ARCHIVED`, el resultado es
`revertido`. Si no, `reversa_fallida`.

### Reconciliar un intento a medias

Un `intencion` sin paso posterior, o un `reversa_intencion` sin resultado,
quiere decir que el proceso murió a media llamada. Como el candado de sesión se
suelta cuando muere la conexión, quien tome el candado sabe que nadie más está
en vuelo. Antes de actuar sobre la misma clave se hace LIST por identidad (por
id en la reversa). Una lista que no sale `ok` deja la clave pendiente, sin
conclusión:

- Tras `intencion`, si existe `ENABLED`, el resultado es `aplicado`. Si no,
  `fallido` con motivo `sin_resultado`.
- Tras `reversa_intencion`, si no existe, el resultado es `revertido`. Si
  existe, `reversa_fallida`.

Correr dos veces deja lo mismo. El comando `python -m app.cli bloqueo-reconcilia`
hace lo mismo para todas las claves; si el candado de una está tomado, la
salta. Lo corre el checklist del despliegue.

### Superficies

- `GET /api/dashboard/cortes` gana la clave `bloqueos_jev`, con las
  propuestas, los bloqueos vivos con su paso, los fallidos y anulados de los
  últimos 7 días y el estado de pausa por plataforma. Usa la conexión de lectura y degrada como la
  señal (`bloqueos_disponibles = false`) si la consulta falla.
- `cortes.html` gana la sección y `app/static/js/cortes.js` gana los cuatro
  POST, con el mini-form del token que ya usa la pantalla.
- Las rutas van en un router nuevo de `app/api_write.py` con prefijo
  `/api/jev-bloqueo`, registrado en `app/main.py`. La prueba
  `test_toda_ruta_post_declara_exige_token_antes_de_la_conexion` hoy recorre
  solo `router`; se extiende a todos los routers del módulo.
- `/salud` no cambia.

## Interruptor y reversa

| Interruptor | Apagar deja | Reversa de lo hecho |
| --- | --- | --- |
| `jev.bloqueo` en `config_version.settings`. Ausente o distinto de `true` es apagado | No hay propuestas, y aprobar, rechazar y reanudar responden 409. Los bloqueos ya puestos siguen en Amazon | Quitar, uno por uno, desde "Tus bloqueos". Quitar funciona con el interruptor apagado: una reversa nunca espera un interruptor |

## Qué pasa si

| Situación | Resultado |
| --- | --- |
| Doble clic en Bloquear | El segundo choca con el candado (409) o encuentra el bloqueo vivo (`anulado`, `ya_bloqueada`). Un solo negative |
| Llegó una venta entre la vista y el clic | El job ya selló otra señal y la aprobación queda `anulado`, `senal_cambio` |
| Amazon responde 5xx o se corta la red | La `intencion` queda abierta. Si el LIST siguiente ve el negative `ENABLED`, queda `aplicado`; si no, la reconciliacion sella `fallido` / `sin_resultado` |
| El contenedor muere tras el commit de `intencion` | La siguiente acción sobre esa clave, o el checklist, reconcilia por LIST |
| El motor propone después la misma búsqueda | Sigue su camino de siempre. Ver "Riesgos" |
| La lista de Amazon sale truncada o ambigua | `fallido` sin POST, o reconciliación pendiente |
| El dueño rechaza y luego cambia una ficha | La huella cambia y la búsqueda vuelve a proponerse |
| Jev se apaga o se queda sin cupo | Las señales vencen a las 36 h y la sección queda vacía. Los bloqueos puestos no cambian |

## Pruebas que deben discriminar

1. Interruptor ausente o `"true"` como texto: no hay propuestas, aprobar,
   rechazar y reanudar dan 409 y el transporte HTTP no recibe ninguna llamada.
   Quitar sigue funcionando.
2. La señal cambió y no hay intención huérfana: `anulado` y cero HTTP, tampoco
   a `/v2/profiles`.
3. Ledger antes del HTTP: con un transporte que revienta, la base guarda
   solo `intencion` y la respuesta es `por_confirmar`. Ese clic no sella
   `fallido`. Con un crash simulado tras `intencion`, la reconciliación
   adopta un negative existente o sella `fallido` / `sin_resultado`.
4. Dos aprobaciones concurrentes de la misma clave, con conexiones distintas,
   dejan un solo POST: la segunda recibe 409 mientras la primera sostiene el
   candado, y no marca `fallido` la intención de la primera.
5. Rechazo recordado: misma huella, oculta. Ficha con texto nuevo o producto
   nuevo, reaparece. Cambio de estado de un anuncio o ficha renovada con el
   mismo texto, sigue oculta.
6. Pausa: 20 veredictos con 13 aprobados de los últimos 20 (65%) pausan. 14 de
   20 (70%) no pausan. Tras reanudar, la cuenta empieza de cero.
7. Quitar borra el id del ledger aunque el body traiga otro. `ARCHIVED` cuenta
   como revertido. Tras quitar, la misma huella no se vuelve a proponer.
8. Clave del motor en `apply_queue`: `pending_veto`, `released`, `applying`,
   `applied` sin reversa y `vetoed` dentro de 30 días ocultan la propuesta;
   `applied` revertido y `vetoed` de hace más de 30 días no. Aprobar con la
   clave activa da `anulado`, `motor`.
9. Perímetro con logins reales: `orbit_decide` y `orbit_ingest` no leen las
   tablas, `orbit_read` lee, solo `orbit_admin` inserta, nadie actualiza ni
   borra.
10. Arquitectura: `bloqueo_dueno.py` no importa `app.ads.write`;
    `bloqueo_dueno_efecto.py` está en la allowlist con su razón; ningún módulo
    del motor importa ninguno de los dos.
11. Una aprobación en `amazon_us` construye el cliente con el perfil de US.
12. Un término con `<script>` se pinta escapado.

## Orden de entrega

1. Migración 0074 con su reversa y el perímetro.
2. Núcleo puro y lecturas (`bloqueo_dueno.py`).
3. Efecto, reconciliación y comandos (`bloqueo_dueno_efecto.py`, `app/cli.py`).
4. Rutas y pantalla.
5. Despliegue apagado, checklist, encendido de `jev.bloqueo` y recordatorio de
   la medición a 30 días.

## Alternativas consideradas

- **Meter la aprobación en `apply_queue`.** Rechazada. La liberación
  re-evalúa la regla económica y la descartaría, y `docs/CONTEXTO.md` prohíbe
  colgar nada nuevo de la cola.
- **Una herramienta de línea de comandos, como `tools/archiva_inertes.py`.**
  Rechazada como superficie principal porque el dueño decide en `/cortes`. Se
  conserva el patrón de ledger, LIST previo y readback.
- **Que el job de Jev excluya lo bloqueado.** Innecesario. La lista de
  propuestas ya lo excluye, y el job reutiliza juicios pagados sin costo. Así el
  job no gana lecturas.
- **Recordar el rechazo por `roster_sha256`.** Rechazada. Una pausa de anuncio
  soltaría el rechazo.
- **Aprobar desde Telegram.** Fuera de alcance. No hay receptor de entrada y
  quedaría fuera de la VPN.

## Riesgos

- **El motor puede proponer la misma búsqueda después del bloqueo.** El motor
  no lee negativos (hueco conocido de JEV ADS 02), y la ventana de cortes
  conserva el gasto previo. Su POST puede chocar con el negative existente. No
  sabemos qué responde Amazon a un duplicado. Si responde un 207 sin id, la
  fila de la cola queda `failed` en el primer intento y el bloqueo sigue
  puesto. Si la fila queda `applying` huérfana, la reconciliación del motor
  adopta el negative del dueño por identidad (`docs/APPLY.md` §6.1), y quitarlo
  después desde `/cortes` lo borra también para el motor. La fila B.3 del plan
  exige leer ese camino y dejar una prueba con la respuesta simulada.
- **El criterio de E.2 de "ninguna vendió en los 30 días siguientes".** No se
  corre. Lo sustituye el filtro del dueño en cada clic, que el dueño aceptó al
  bajar la meta a 70%, y el reporte de 30 días del plan, que cuenta ventas
  posteriores en los grupos bloqueados.
- **Lo que no se ingiere.** Los negativos no entran a la estructura. Un
  bloqueo quitado a mano en Amazon sigue "vivo" para Orbit hasta que el dueño lo
  quite en `/cortes`. El LIST de quitar lo resuelve como `revertido`.
- **US vale poco.** 38.63 USD en la ventana. Se construye igual por decisión
  del dueño.
