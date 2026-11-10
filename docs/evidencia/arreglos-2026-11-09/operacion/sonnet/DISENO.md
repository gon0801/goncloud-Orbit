# Diseño: grupo operacion (H-H2 = I-H3 = J-H2, H-H3, J-H1, J-H4)

Código leído: copia de master `3c841f4` (`orbit-master/`). Rutas relativas a esa copia.
No consulté producción. Lo que dependía del host (contenido de `cron-avisa`, crontab real) va marcado "inferido".

## Resumen

| Hallazgo | Causa raíz (una línea) | Arreglo | Unidad |
|---|---|---|---|
| H-H3 | El vigilante cuenta `ok=false` como presente y `salud` espera una segunda falla que la repetición (saltada después de 06:15) nunca produce | El vigilante deja de contar `ok=false` como presente | U1 |
| J-H4 | `compose up --build` recrea el contenedor minutos después de la única lectura de guardas | Separar `build` de `up` y volver a leer guardas justo antes de recrear (también en la reversa) | U2 |
| H-H2 = I-H3 = J-H2 | El mismo código de salida 0 significa "habrá otro horario" y "ya no hay otro horario", y el crontab no trae los reintentos del plan | Reintentos en el crontab + flags `--reintento`/`--ultimo`; la última guarda sale 3 (cron-avisa sí avisa) | U3 |
| J-H1 | Hay dos fuentes del crontab: el heredoc de `docs/DEPLOY.md` (sin `cron-avisa`) y el host (con él) | Un archivo versionado de líneas canónicas + un instalador `cron_orbit.sh` con `revisa`, como el de `jev-vigia` | U4 |

H-H2 y J-H1 comparten el lugar donde se arreglan (el bloque del crontab). Se arreglan en dos unidades porque una es código del CLI
y la otra es el instalador; la segunda es la que pone las líneas nuevas en vivo.

---

## H-H3: la primera falla de SP-API sin aviso

### Causa raíz
`app/spapi/vigilante.py:47-67`, función `faltantes`: la ventana recorre las filas `(source, platform, started_at, finished_at, ok)` y
agrega a `presentes` toda fila terminada, sin mirar `ok` (el `_ok` de la línea 62 se descarta a propósito; docstring `:55-56`
"`ok = false` CUENTA como presente (ya avisó por su camino)").
Esa premisa es falsa para la primera falla de clase "resto": `app/spapi/salud.py:162-173` solo avisa en la segunda falla seguida
(`_racha_fallida`, `:98-109`), y las clases inmediatas son solo `lwa_fallido` y `http_429` (`:112-118`). La segunda falla la produce
la repetición de `scripts/spapi-diario.sh:28-37`, que no corre si arranca después de las 06:15. Resultado: ninguno de los dos caminos avisa.
(Sonda H `h2_spapi_primer_fallo_mudo.py`: `avisos=[]`, `faltantes=[]`.)

### Uso primero
- 07:30, vigilante: `python -m app.cli spapi_vigilante` imprime `- spapi_pricing / amazon_mx: ausente` y `faltan 1 de 8`, manda UN Telegram y sale 1.
  Hoy imprime `presente`.
- Una falla que la repetición sí recupera (segunda corrida `ok=true` en la ventana): sigue `presente`; no hay aviso.
- Una racha de fallas que `salud` ya avisó: el vigilante la repite una vez a las 07:30 (ver Tradeoffs).

### Forma
Un solo cambio de condición, sin tipos nuevos:

```python
# app/spapi/vigilante.py  faltantes()
for source, platform, started_at, finished_at, ok in filas:
    ...
    if finished_at is None:
        continue
    if ok is False:          # NUEVO: una corrida fallida no cuenta como "dejó su corrida"
        continue
    presentes.add((source, platform))
```
`ok is False` (no `not ok`) para no tocar filas con `ok NULL` y `finished_at` presente (no debería existir; hoy cuenta presente, se queda así).
Una corrida `ok=true` posterior de la misma fuente y plataforma en la ventana la deja presente, porque el `set` se llena con cualquiera.

Texto del aviso: `app/notifica.py:799-802` dice "El cron de las 05:00 UTC no dejó estas corridas en ingest_run". Se cambia a
"…no dejó estas corridas ok en ingest_run (ausentes o fallidas). Revisar spapi-diario.log y el aviso de salud de cada fuente."
y el encabezado `[Orbit] ALERTA SP-API sin corrida` pasa a `... sin corrida ok`. Docstring del módulo y `docs/DEPLOY.md:1060-1062`
(el párrafo "una corrida presente, aunque sea `ok = false`… no es silencio") se corrigen en el mismo cambio.

Invariante: queda en la estructura de la función pura `faltantes` (es la única fuente de "presente", regla 2); no se agrega validación de borde.

### Datos que ya existen
Nada que reparar. No hay migración. Las corridas ya escritas se leen igual.

### Cambio de comportamiento en vivo
Solo el aviso de las 07:30 (Telegram). No toca Amazon ni dinero. Se enciende al desplegar el código (el cron de las 07:30 ya existe).
Se deshace revirtiendo el commit. Primer día: si ayer hubo una falla ya avisada por `lwa_fallido`/429, llegará también el resumen de las 07:30.

### Prueba
Regla 4: falla que ya pasó en producción en la forma (silencio de ingestas) y alimenta precios (dinero). Una comprobación:
cambiar `tests/test_spapi_vigilante.py::test_faltantes_ok_false_cuenta_como_presente` (`:59`) por su inverso
`test_faltantes_ok_false_no_cuenta` y añadir en la misma prueba el caso "falla y luego ok → presente". Hoy falla (devuelve `[]`),
con el arreglo devuelve `[("spapi_pricing","amazon_mx")]`. Pura, sin base. La sonda H h2 sirve de reproducción: su última línea cambia de `[]` a ese par.

### Alternativas
- **`salud.evaluar_alertas` avisa en la primera falla de toda clase** (borrar `_racha_fallida`). Deja el aviso donde nace, pero
  reintroduce el ruido que `_racha_fallida` evita (un 503 que la repetición recupera avisaría y luego "recuperada"). Pierde porque
  cambia el contrato de las 8 fuentes todos los días por un caso de días lentos.
- **El script avisa al saltar la repetición** (`no repito $par` llama a un CLI nuevo). Es la forma más precisa, pero agrega un
  comando, una ruta de aviso nueva y un tercer lugar que sabe qué pasó; el vigilante ya existe, ya lee la verdad (`ingest_run`) y ya es el último control.

### Tamaño
`app/spapi/vigilante.py` (1 condición + docstring), `app/notifica.py` (texto), `docs/DEPLOY.md` (un párrafo), `tests/test_spapi_vigilante.py`.
Cabe en una unidad. Dependencias: ninguna.

---

## J-H4: el despliegue recrea el contenedor sin volver a mirar las guardas

### Causa raíz
`tools/despliegue/desplegar.sh:104-130` es el único bucle de guardas (`estado_guardas`, `comun.sh:182-195`). Después vienen respaldo, migraciones,
copia y md5 (`:139-200`; minutos) y `compose_up` en `:201`. `compose_up` (`comun.sh:48-51`) solo llama `sigue_candado`, que vigila
la sesión ssh del propio despliegue, no los jobs de cron. `docker compose … up -d --no-deps --build app` hace el build y la recreación en
un solo comando, así que la parte larga (build) ocurre con las guardas ya vencidas. `revierte` llama `compose_up && espera_salud` (`comun.sh:281`)
y `rollback.sh:11-15` entra directo a `revierte`: ninguno de los dos mira guardas.

### Uso primero
- `desplegar.sh`, paso 5: tras copiar y verificar md5, imprime `build listo; leo guardas antes de recrear` y, si hay `app.cli` vivo,
  `guardas ocupadas antes de recrear (…); reviso en 15 s`. Si se agota el plazo común (`ESPERA`), sale 75 sin recrear y con el mismo
  manejo que ya existe en `:108-122` (revertir lo anotado y salir 75).
- `rollback.sh` / `revierte`: espera hasta `ESPERA_REVERSA` (300 s) a que las guardas queden libres y, vencido el plazo, recrea de todos modos
  con `AVISO: recreo con guardas ocupadas (<estado>)` en la salida. Una reversa no puede quedar sin terminar.
- El motor (que corre `desplegar.sh`) no cambia su contrato: sigue viendo 0, 1 o 75.

### Forma
```bash
# tools/despliegue/comun.sh
# Reemplaza compose_up. El build es lo largo y no toca el contenedor vivo; lo corto (up) va solo
# tras leer las guardas.
recrea() {   # recrea adelante|reversa   -> 0 ok, 75 guardas ocupadas (solo adelante), 1 error
  local modo=$1 plazo fin G
  plazo=$ESPERA; [ "$modo" = reversa ] && plazo=${ESPERA_REVERSA:-300}
  sigue_candado
  srv "docker compose -p orbit --project-directory $SRV -f $SRV/docker-compose.yml build app" || return 1
  fin=$((SECONDS + plazo))
  while :; do
    sigue_candado
    G=$(estado_guardas) || { echo "ERROR: no pude leer las guardas" >&2; return 1; }
    [ "$G" = "$GUARDAS_LIBRES" ] && break
    if [ "$SECONDS" -ge "$fin" ]; then
      if [ "$modo" = reversa ]; then echo "AVISO: recreo con guardas ocupadas ($G)"; break; fi
      echo "guardas ocupadas antes de recrear: $G"; return 75
    fi
    sleep 15
  done
  sigue_candado
  srv "docker compose -p orbit --project-directory $SRV -f $SRV/docker-compose.yml up -d --no-deps app"
}
```
- `desplegar.sh:201`: `recrea adelante` en lugar de `compose_up`; si devuelve 75, llama al mismo bloque que hoy está en `:109-122`
  (se extrae a una función `sale_por_guardas` para no duplicarlo; el diario ya trae `CODIGO=`, así que `tiene_cambios` es verdadero y revierte).
- `comun.sh:281`: `recrea reversa`.
- `docker compose up` sin `--build` recrea el contenedor porque la imagen cambió; la imagen ya está construida por `build`.

Invariante: "nadie recrea sin haber leído las guardas en los últimos segundos" queda en la estructura: `recrea` es la única función que ejecuta `up`
(`compose_up` se borra; callers migrados en el mismo cambio, sin alias).
Límite honesto: queda una ventana de segundos (lectura de guardas más recreación) donde un cron puede arrancar. Un `docker top` limpio no
garantiza que `docker exec` no entre un segundo después. Con U3 un ciclo así se recupera: ver "Alternativas" para la forma que la cierra del todo.

### Datos que ya existen
Nada. No toca base de datos. Si se corta a medias, el diario `ultimo.env` ya cubre el caso (`CODIGO=` anotado → la siguiente corrida o `rollback.sh` revierte);
`build` es idempotente.

### Cambio de comportamiento en vivo
Sí cambia lo que hace el próximo despliegue: puede esperar hasta `ESPERA` (600 s) más y puede salir 75 después de haber copiado código.
No cambia nada de Amazon. Se enciende al desplegar este cambio (el primero que lo usa es el siguiente despliegue). Se deshace revirtiendo el commit.
Efecto lateral a conocer: con `ESPERA=600` un despliegue ya no puede quedarse atascado en la recreación pero sí ocupar más tiempo.

### Prueba
Regla 4 (instalar/borrar/deshacer en las máquinas). Una comprobación en `tests/test_despliegue_motor.py` (usa `ORBIT_SIMULACION=1`, necesita Postgres: no la corrí):
el `docker` falso ya responde `top` con el archivo `$C/top` (`:93-95`) y registra todo en `docker.log` (`:91`). Preparar `top` con una línea `app.cli cycle`
solo a partir de la segunda lectura, con `AUTO03_ESPERA_S=1`: hoy el log contiene `compose … --build app` y sale 0; con el arreglo el log **no** contiene `up -d`, sale 75
y el estado final es `revertido`. El `docker` falso ya trata cualquier `compose` igual, así que `build` y `up` pasan sin tocarlo.

### Alternativas
- **Candado compartido** (`flock -s /tmp/orbit-motor.lock` en cada línea de cron, `flock -x` en el despliegue justo antes de `up`): cierra la ventana de segundos,
  pero obliga a tocar toda línea que entra al contenedor (incluidas las de root y las de precios fuera del bloque Orbit) y deja un "salto" más (exit 75 de los jobs). Pierde
  hoy por tamaño; es la respuesta si el dueño no acepta la ventana de segundos (pregunta 4).
- **Evitar la hora de los crons** (no recrear en `:19-:21`, `:39-:42`, `:09-:12`): una segunda tabla de horarios que se desactualiza. Pierde.
- **Esperar a que el job esté vivo** (el `docker top` actual): ya es la guarda; el defecto es cuándo se lee, no qué lee.

### Tamaño
`tools/despliegue/comun.sh`, `tools/despliegue/desplegar.sh`, `tests/test_despliegue_motor.py`, nota de 3 líneas en `docs/DEPLOY.md`. Una unidad, verificable con la simulación en Linux.

---

## H-H2 = I-H3 = J-H2: ciclo, vigía y avisos corren una sola vez al día

### Causa raíz
Dos defectos juntos, y hay que arreglar los dos:
1. `app/cli.py:113-115`: sin D-1 imprime `TEXTO_ESPERA` y devuelve 0; `app/cli_bids.py:125-127` (impulso-vigia) y `:72-77` (avisos-campana) igual.
   El 0 dice "espera al siguiente horario" aunque ese horario no exista. `app/cli_bids.py:102-103` promete salir 1 cuando el día no se procesó, pero el `return 0` de `:127` sale antes de ese aviso.
2. `docs/DEPLOY.md:1192-1199` (bloque instalado) trae `cycle` solo a `40 8` y `41 8`, `impulso-vigia` a `20 8`, `avisos-campana` a `5 8`. `plans/ingesta-01.md:52-62` aprobó reintentos
   a las 10:05/10:08/10:10/10:11, 13:05/13:08/13:10/13:11 y 16:05/16:08/16:10/16:11, y fijó la meta "cero días con el ciclo saltado todo el día" (`:114`); el único llamador de `corre_ciclo` es `app/cli.py:121`.
   `cron-avisa` no puede ayudar: avisa por salida ≠0 y esta sale 0.

### Uso primero
Crontab (líneas que instala U4; todas envueltas, ver J-H1):
```cron
# ya existente
40 8 * * * cron-avisa --nombre "Ciclo de pujas US" --cada 24 -- docker exec orbit-app-1 python -m app.cli cycle --platform amazon_us
# NUEVAS (mismo patrón para MX a :11, vigía a :08, avisos a :05)
10 10 * * * cron-avisa --nombre "Ciclo de pujas US (reintento 10:10)" --cada 24 -- docker exec orbit-app-1 python -m app.cli cycle --platform amazon_us --reintento
10 13 * * * ...                                                                                                     --reintento
10 16 * * * ...                                                                                                     --reintento --ultimo
```
- 08:40, la ingesta llega a las 08:50: el ciclo sale 0 con `sin métricas de ayer: espera al siguiente horario` (igual que hoy). A las 10:10 el guard pasa y corre el ciclo.
- Si a las 16:10 sigue sin D-1 y no hubo ciclo hoy: `sin métricas de ayer: ya no hay otro horario hoy` y sale 3 → `cron-avisa` manda Telegram "Ciclo de pujas US ... falló (3)".
- Si a las 10:10 ya hay un ciclo `done`/`degraded`/`failed`/`skipped` hoy: sale 0 con el motivo y no hace nada (idempotente). `--forzar` sigue saltando todo, a mano.

### Forma
```python
# app/cli.py (_cycle) — argumentos nuevos en el subparser de "cycle" (junto a --forzar, :601)
#   --reintento : corrida de reintento; no abre ciclo si hoy ya hay CUALQUIER ciclo terminal
#   --ultimo    : último horario del día; sin métricas de ayer sale 3, no 0

# Orden nuevo (hoy la guarda de métricas va antes que el salto, :113-119):
if not args.forzar:
    salto = ciclo.motivo_de_salto(conn, args.platform, reintento=args.reintento)   # barato, primero
    if salto is not None:
        print(salto); return 0                  # ya corrió hoy: nunca es alarma
if not metricas_listas.guarda(conn, (args.platform,), forzar=args.forzar):
    print(metricas_listas.TEXTO_ESPERA_ULTIMO if args.ultimo else metricas_listas.TEXTO_ESPERA)
    return EXIT_DIA_PERDIDO if args.ultimo else 0   # EXIT_DIA_PERDIDO = 3

# app/cycle.py:257-269  motivo_de_salto(conn, platform, *, reintento=False)
#   hoy: salta con status in (done, degraded) o running < 3 h
#   con reintento=True: salta con cualquier status != running (agrega failed y skipped)
#   sin reintento: igual que hoy (el de 08:40 y el manual no cambian)

# app/metricas_listas.py
TEXTO_ESPERA_ULTIMO = "sin métricas de ayer: ya no hay otro horario hoy"
EXIT_DIA_PERDIDO = 3

# app/cli_bids.py: _parsea_forzar (:36-44) acepta también "--ultimo" -> (forzar, ultimo, error)
#   impulso-vigia (:125-127) y avisos-campana (:72-77): guard falla -> ultimo ? exit 3 : exit 0
#   docstring de :102-103: "sale 3" (el contrato real), no "sale 1".
```
Por qué `failed` bloquea el reintento: el ciclo `failed` ya avisó por `cron-avisa` (exit 1) y puede haber dejado intentos de bid sin sellar. La causa de E-H3
(`app/cycle.py:3760` decide antes de reconciliar) es de otro grupo; este diseño no la agrava porque nunca reintenta sobre un ciclo fallido.
Un `running` de más de 3 h (huérfano) sí se reclama, como hoy.

Invariantes: "un ciclo por plataforma y día salvo `failed`/manual" queda en `motivo_de_salto` (una función, un lugar) y en el claim atómico que ya existe; "el último horario no calla"
queda en el código de salida (validación de borde del CLI). Vigía y avisos ya son idempotentes por diseño: vigía por `(impulso_id, corte)` (`app/impulso_io.py:1139-1141`), avisos por `_ya_salio` (`app/cli_bids.py:84`).

### Datos que ya existen
Nada que reparar. No hay migración. Filas `optimizer_cycle` de hoy ya existentes: un reintento las respeta por `motivo_de_salto`.

### Cambio de comportamiento en vivo
Sí, con dinero real: el ciclo (bids, pausas, harvest) y el vigía del impulso (pausa de campañas) pueden correr hasta ocho horas después que hoy, solo en días en que la ingesta llegó tarde.
- Orden de encendido: primero se despliega el código (flags; sin ellas el crontab nuevo daría `exit 2` de argparse); después U4 instala las líneas. Hasta entonces nada cambia.
- Interruptores existentes siguen mandando: modo por plataforma/meta (shadow/live) y `ads_bid_politica_<p>`; un reintento corre con los mismos que el de 08:40.
- Se deshace quitando las líneas de reintento (reinstalar el archivo de U4 del SHA anterior); el código sin las líneas es inerte.
- Efecto de ver: `ads-salud` y el Telegram de ingesta dirán "el ciclo espera" (`app/ads/diario.py:124-129`) y ahora es cierto.

### Prueba
Regla 4 (dinero: decide bids/pausas; vigía frena gasto de impulso). Una comprobación: adaptar `sondas/J/J1_ciclo_un_solo_horario.py` (pura, conexión falsa):
`cycle --platform amazon_us --reintento --ultimo` sin D-1 y sin ciclo hoy debe salir 3 y no llamar `corre_ciclo`; hoy ese flag no existe (error de uso) y sin él sale 0.
La misma prueba afirma el segundo caso: ciclo `failed` hoy + `--reintento` → sale 0 sin llamar `corre_ciclo`. No hace falta una prueba por cada comando; el vigía y los avisos comparten `_parsea_forzar`.

### Alternativas
- **Un solo proceso que reintenta** (el job duerme y sondea hasta las 16:00 con un bucle): un `docker exec` vivo ocho horas bloquea despliegues (guarda `app.cli`), choca con J-H4 y no sobrevive a una recreación del contenedor. Pierde.
- **Ciclo disparado por la ingesta** (la ingesta, al terminar ok con D-1, lanza el ciclo): es el diseño de menos horas perdidas, pero mueve el ciclo al camino de la ingesta, une dos jobs con permisos distintos (DSN de ingesta vs decide) y reescribe el plan aprobado. Pierde por tamaño.
- **Solo avisar (exit 3 a las 08:40) sin reintentos**: cumple "no calla", pero deja el día sin ciclo, contra la letra del plan (`ingesta-01.md:114`). Pierde.

### Tamaño
`app/cli.py`, `app/cli_bids.py`, `app/cycle.py` (una función), `app/metricas_listas.py` (2 constantes), la sonda/prueba. Una unidad. Las líneas del crontab van en U4.

---

## J-H1: el instalador del cron borra las envolturas `cron-avisa`

### Causa raíz
`docs/DEPLOY.md:1200-1212`: el "instalador" es un heredoc con el bloque en texto plano; descarta con `grep -v` toda línea con `app.cli cycle`, `app.cli impulso-vigia`, etc. (`:1210`) y escribe
las suyas, que no llevan `cron-avisa`. Las líneas con envoltura viven solo en el host (`/usr/local/bin/cron-avisa` y el crontab de `gon`), no en el repo
(sonda J5: tras correr el instalador, `*/10 10-12 … ads-salud` pierde la envoltura). La causa es que el repo es una segunda fuente del crontab y, de las dos, la que gana al reinstalar
es la que está equivocada. El precedente correcto ya existe: `tools/despliegue/cron_jev_vigia.sh`, con la línea exacta (envuelta) dentro del script y `revisa`/`instala` idempotentes.

### Uso primero
- Operador / agente con plan: `ssh goncloud 'bash -s -- revisa' < tools/despliegue/cron_orbit.sh` imprime `bloque Orbit igual al canónico` (0) o el `diff` y sale 1.
- Instalar: `… -- instala` respalda el crontab en `$ORBIT_CRON_ARCHIVO`, reemplaza solo el bloque `# === Orbit (…` por el canónico y escribe `sin cambios` si ya es igual. Las líneas fuera del bloque (accounting, precios, jev-vigia) no se tocan.
- `desplegar.sh`: en el ensayo y tras el paso 5 corre `revisa` e imprime `DRIFT del crontab: …` (aviso, no aborta); no instala solo (precio de instalar el bloque entero sin que el dueño lo sepa).
- `docs/DEPLOY.md`: el heredoc se reemplaza por las dos líneas de uso de arriba y la tabla de horarios apunta al archivo.

### Forma
```
tools/despliegue/crontab-orbit.txt   # las líneas canónicas del bloque (con `cron-avisa` y con los reintentos de U3). Única fuente.
tools/despliegue/cron_orbit.sh       # instala | revisa   (mismo estilo y respaldo que cron_jev_vigia.sh)
```
```bash
# cron_orbit.sh (pseudocódigo)
ACTUAL=$(crontab -u gon -l) ; NUEVO = (ACTUAL sin el bloque "# === Orbit (" … hasta el siguiente "# === ") + (crontab-orbit.txt)
case $1 in
  revisa) diff <(bloque de ACTUAL) crontab-orbit.txt && exit 0 || { diff; exit 1; } ;;
  instala)
    grep -q 'cron-avisa' crontab-orbit.txt && ! test -x /usr/local/bin/cron-avisa && { echo "ABORTA: falta cron-avisa en el host"; exit 1; }
    [ "$NUEVO" = "$ACTUAL" ] && { echo "sin cambios"; exit 0; }
    cp ACTUAL "$ARCHIVO/crontab-gon.<ts>.cron-orbit"; crontab -u gon NUEVO ;;   # reemplazo atómico
esac
```
Líneas del archivo (formato inferido de `claridad-01/F/servidor.txt` y `cron_jev_vigia.sh:21`): cada línea que hoy está envuelta en producción
(ciclo, impulso-vigia, precios, ads-salud, costos) conserva su `cron-avisa --nombre … --cada 24`; se agregan las de reintento de U3 con su propio `--nombre`.
Sin envoltura se quedan lo que hoy no la tiene (`ingest diario` ×3, `spapi-diario.sh`): la ingesta ya tiene `ads-salud` y SP-API tiene vigilante (H-H3).
**El archivo se siembra con el crontab real de `gon` (lectura por el agente con permiso), no con mi reconstrucción**: no vi el crontab de hoy, solo capturas del 21-oct, 4 y 9-nov.

Invariantes: "el bloque Orbit del repo y el del host son el mismo" queda en estructura (un archivo, un instalador, `revisa`); "no instalar líneas con `cron-avisa` si el host no lo tiene"
queda en validación de borde del instalador (sin él cada línea saldría 127 y los jobs no correrían: peor que no avisar).

### Datos que ya existen
El crontab de producción ya está bien envuelto en ciclo/vigía/precios (J-H1 es latente: aparece al reinstalar). Paso aparte, de lectura: guardar `crontab -u gon -l` y diffear contra el archivo
antes de la primera `instala`; la primera `instala` debe producir solo las líneas de reintento nuevas. Escribir en producción lo corre otro agente con plan.

### Cambio de comportamiento en vivo
`instala` cambia el crontab de `gon` y, con las líneas de U3, hace que el ciclo corra más veces (ver arriba). Se enciende con `instala` (manual, después del despliegue de U3);
se deshace reinstalando el archivo del SHA anterior (`git show <sha>:tools/despliegue/cron_orbit.sh | ssh goncloud 'bash -s -- instala'`) o con el respaldo que escribe el instalador.
Si el proceso muere entre el respaldo y `crontab -u gon NUEVO`, el crontab sigue siendo el anterior (el reemplazo es atómico); correrlo otra vez es seguro.

### Prueba
Regla 4 (instalar en las máquinas). Una comprobación (ya hay patrón en `tests/test_despliegue_motor.py` con `crontab` falso, `:305`): sobre un crontab de ejemplo con líneas envueltas en ciclo/vigía/ads-salud,
`cron_orbit.sh instala` las deja envueltas y `revisa` sale 0; hoy la sonda J5 las pierde. Las pruebas que pinzan líneas dentro de `docs/DEPLOY.md`
(`tests/test_ingesta_cron.py`, `test_impulso_vigia_cron.py`, `test_avisos_campana_cron.py`, `test_jev_senales_cron.py`, `test_ads_salud_cron.py`) se reapuntan al archivo: es mantenimiento, no pruebas nuevas.

### Alternativas
- **Parchar el heredoc** (agregar `cron-avisa` a las líneas del DEPLOY.md): arregla hoy y deja dos fuentes que se vuelven a separar la próxima vez que alguien envuelva una línea en el host. Pierde por la causa raíz.
- **Meter la envoltura en el job** (cada comando avisa por sí mismo y no necesita `cron-avisa`): eliminaría la dependencia del host, pero no detecta un job que no arranca ni salida de `docker exec` fallido, que es lo que `cron-avisa --revisa` cubre. Pierde.
- **Instalar el bloque en cada despliegue** (como `jev-vigia`): cierra el drift solo, pero un despliegue de código pasaría a cambiar horarios de dinero sin decisión; por eso solo `revisa`.

### Tamaño
`tools/despliegue/crontab-orbit.txt` + `cron_orbit.sh` (nuevos), `desplegar.sh` (2 llamadas a `revisa`), `docs/DEPLOY.md` (reemplazo del bloque), 5 pruebas de pinzado reapuntadas, 1 prueba nueva.
Cabe en una unidad, pero es la más grande del grupo; si el revisor la pide, se parte en (a) archivo + instalador + pruebas y (b) `revisa` dentro de `desplegar.sh`.

---

## Orden propuesto

1. **U2 (J-H4)**: es lo que protege a todos los despliegues siguientes. Independiente.
2. **U1 (H-H3)**: independiente, mínima, solo Telegram. Puede ir en paralelo con U2.
3. **U3 (H-H2)**: código con flags. Debe estar desplegado antes que cualquier línea que los use.
4. **U4 (J-H1)**: instalador + archivo canónico con los reintentos. Su `instala` en producción es el paso de encendido de U3.
5. **Paso de datos/operación (otro agente con plan)**: leer el crontab real → sembrar `crontab-orbit.txt` → `revisa` → `instala`.

Dependencias: U3 → U4 (código antes de las líneas). U4 → paso 5. U2 y U1 no dependen de nadie.
Si U3 y U4 se desplegaran al revés, cada reintento daría `exit 2` por argumento desconocido y `cron-avisa` mandaría Telegram tres veces al día.

## Decisiones solo del dueño

1. ¿Está bien que el ciclo corra hasta las 16:10 (ofertas de bid movidas tarde, solo en días de ingesta lenta)? Recomiendo sí: es lo que el plan aprobó y es mejor que perder el día.
2. ¿Quieres vendorizar `cron-avisa` en el repo (`tools/host/cron-avisa`) después de que alguien lo lea en el host? Recomiendo sí: hoy el instalador depende de un script que no está versionado ni revisado, y no sé si `--revisa`/`--cada 24` avisan por un salto 75 repetido.
3. ¿Aceptas que el vigilante de las 07:30 repita una falla que `salud` ya avisó (una vez al día mientras siga fallando)? Recomiendo sí: es el único control que no depende de la repetición.
4. ¿Aceptas la ventana residual de segundos del despliegue (U2) o quieres el candado compartido de las líneas de cron? Recomiendo aceptarla ahora: con U3 un ciclo muerto se recupera a las 13:10 (el `running` de menos de 3 h bloquea el de las 10:10).
5. ¿Envolver también `ingest diario` y `spapi-diario.sh` con `cron-avisa`? Recomiendo no: tienen `ads-salud` y el vigilante; `spapi-diario.sh` termina siempre en 0 (`scripts/spapi-diario.sh:28-37`), así que la envoltura solo serviría para `--revisa`.

## Riesgos

- No leí el crontab ni `cron-avisa` reales: el formato de las líneas envueltas es inferido de las capturas del repo. Por eso U4 exige sembrar el archivo desde el crontab real.
- Un despliegue que cae a las 08:39:50 sigue pudiendo cortar un ciclo (ventana de segundos); queda sin aviso salvo que el `running` huérfano llegue a las 13:10.
- `estado_guardas` (`comun.sh:182-195`) cuenta `pending_veto` y `harvest_job` no terminales como ocupado (sospecha 1 del reporte J): con la espera nueva, un despliegue urgente ocupa 600 s antes de salir 75. Es el comportamiento de hoy; U2 no lo empeora, pero tampoco lo arregla.
- El reintento de las 10:10 puede correr antes que la ingesta de las 10:40 termine; ese caso lo cubren el de 13:10 y el de 16:10 (nominal; la ingesta de 10:40 puede durar hasta 13:25 por `TOPE_PARED_SEGUNDOS` de 2.75 h).
- Causa raíz distinta a la descrita: ninguna. Una precisión: H-H3 no se arregla en la repetición del script (ahí solo se ve el síntoma) sino en el vigilante.
