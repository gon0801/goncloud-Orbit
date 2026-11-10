# Diseño de arreglos: grupo operación (opus)

Código leído: copia de master `3c841f4` en `scratchpad/orbit-master` (sin `graphify-out/graph.json`, así
que usé `rg`). Sondas puras corridas: `H/h2_spapi_primer_fallo_mudo.py` (reproduce: `avisos [] /
faltantes []`). No me conecté a producción; lo de producción sale de HALLAZGOS.md y de la evidencia
versionada que cito.

## Problema y causas compartidas

Los cuatro hallazgos tienen dos causas de fondo, y cada una se arregla una sola vez:

1. **El crontab del repo no es el de producción.** Las envolturas `cron-avisa` se pusieron a mano en el
   host (evidencia: `docs/evidencia/claridad-01/F/servidor.txt:34-35`, `estado-planes-2026-11-04/cron-vigente.txt`,
   `quick-wins-2026-11-04/cron/README.md:5-9`). El repo sigue describiendo líneas planas, y además nunca
   tuvo los reintentos que prometía el plan. De aquí salen J-H1 (el instalador quita las envolturas) y la
   mitad de H-H2 (no hay segundo horario).
2. **"Salió bien sin hacer nada" no se distingue de "salió bien".** Las guardas salen 0 aunque ya no quede
   ningún intento más ese día (H-H2), y el vigilante SP-API cuenta una corrida `ok=false` como presente porque
   supone que alguien más ya avisó (H-H3). `cron-avisa` solo ve el código de salida, así que la arreglo
   haciendo que el código de salida diga la verdad. No agrego un canal nuevo.

J-H4 es independiente: la revisión de guardas del despliegue no es atómica con la recreación del contenedor.

Límites que respeté: `docs/CONTEXTO.md` (sin capas nuevas; un número, una fuente), `plans/ingesta-01.md`
(horarios y guardas A.3/A.4) y la regla del dueño en `plans/claridad-01.md:100` («una corrida que no corre
es un bug, con aviso»). Se despliega a cualquier hora; la única guarda permitida es técnica.

---

## J-H1. El instalador de cron borra las envolturas `cron-avisa`

### Causa raíz
`docs/DEPLOY.md:1182-1206`: el bloque `ORBIT_BLOCK` trae líneas planas (por ejemplo `:1197` y `:1199`, el ciclo
sin `cron-avisa`), y el filtro de `:1206` borra por subcadena (`grep -v "app.cli cycle"`, `"app.cli ads-salud"`,
`"app.cli impulso-vigia"`…). Esas subcadenas también aparecen en las líneas envueltas de producción. El instalador
borra lo que no conoce y pone su versión plana encima. La sonda `J5` lo reproduce: después de instalar, ads-salud
queda `cron-avisa: False`. Las demás «líneas exactas» de DEPLOY.md también describen la forma plana, aunque producción
ya las envuelve: precios en `docs/DEPLOY.md:894-895` contra `servidor.txt:34-35`. Las pruebas fijan esa forma plana
(`tests/test_precio_corrida.py::LINEA_CRONTAB_PRECIO*`, `tests/test_ingesta_cron.py:22-88`).

### Uso primero (operador)
El agente que despliega con plan corre el mismo heredoc de DEPLOY.md:

```text
$ ssh goncloud 'bash -s' < bloque          # el heredoc de docs/DEPLOY.md
cron-avisa: antes 6 envolturas en lineas Orbit, despues 9
# === Orbit (ORBIT 03 / 4.2; ingesta-01 A.4; cron-avisa) ===
...
```
y si el bloque quitara alguna envoltura:
```text
ABORTA: el bloque quitaria envolturas cron-avisa (antes 6, despues 4); no toque el crontab
```
y si el host no tiene la herramienta:
```text
ABORTA: /usr/local/bin/cron-avisa no existe o no es ejecutable; no toque el crontab
```

### Forma
Sin código Python. El bloque de `docs/DEPLOY.md` es la única fuente del bloque Orbit (ya lo es para las pruebas)
y pasa a traer la forma envuelta. Tres reglas:

1. **Qué línea se envuelve:** cada línea cuyo fallo con salida distinta de cero no avisa por ningún otro lado.
   Se envuelven ingest structure, avisos-campana, impulso-vigia, cycle US/MX, jev-senales y ads-salud (este ya
   envuelto en producción con `--tras 3`, forma exacta en `tools/check_ads_salud_cron.sh:7`). No se envuelven
   `ingest diario` x3 ni `spapi-diario.sh`. El diario ya avisa por su cuenta de productos y ubicaciones
   (`app/ads/diario.py:124-145`) y ads-salud cubre las métricas de campañas desde las 10:30. `spapi-diario.sh`
   siempre sale 0 (`scripts/spapi-diario.sh:28-38`: sin `set -e`, `|| true`, `continue`) y el vigilante de las
   07:30 lo cubre una vez arreglado H-H3. Envolverlos solo daría avisos dobles.
2. **Las líneas con `flock -n` pasan a `flock -n -E 75`** dentro del `bash -c`, como precios
   (`servidor.txt:34`). Sin eso, un candado ocupado sale 1 y `cron-avisa` da una falsa alarma.
3. **`--nombre` se copia byte a byte del crontab vigente** para las líneas que producción ya envuelve (ciclo,
   vigía, ads-salud). El dueño ya reconoce esos textos. No tengo el texto exacto del ciclo y del vigía; el
   implementador lo toma de una lectura de `crontab -u gon -l` (paso 0, abajo). Para las que hoy no van
   envueltas propongo `"Ingesta de estructura de Ads"`, `"Avisos de campaña"` y `"Señales de JEV"`.

Forma de una línea (patrón de `servidor.txt:34`):
```cron
# job_key=ads_optimizer:amazon_us
40 8 * * * /usr/local/bin/cron-avisa --nombre "<texto vigente>" --cada 24 -- /bin/bash -c 'docker exec orbit-app-1 python -m app.cli cycle --platform amazon_us' >> /mnt/data/appdata/orbit/logs/optimizer.log 2>&1
```

Candado estructural en el heredoc, antes de escribir:
```bash
test -x /usr/local/bin/cron-avisa || { echo "ABORTA: /usr/local/bin/cron-avisa no existe o no es ejecutable; no toque el crontab" >&2; exit 1; }
ACTUAL=$(crontab -u gon -l 2>/dev/null || true)
FILTRO='Orbit \(ORBIT 03|job_key=ingest:|job_key=ads-salud|...|app\.cli jev-senales'   # las mismas 13 subcadenas de hoy
ANTES=$(printf '%s\n' "$ACTUAL" | grep -E "$FILTRO" | grep -c cron-avisa || true)
DESPUES=$(printf '%s\n' "$ORBIT_BLOCK" | grep -c cron-avisa || true)
[ "$DESPUES" -ge "$ANTES" ] || { echo "ABORTA: el bloque quitaria envolturas cron-avisa (antes $ANTES, despues $DESPUES); no toque el crontab" >&2; exit 1; }
echo "cron-avisa: antes $ANTES envolturas en lineas Orbit, despues $DESPUES"
```
El filtro `grep -v` de hoy se queda igual (`:1206`), porque los nombres de las líneas no cambian. Lo nuevo es
que el bloque trae las envolturas y que el conteo impide perderlas.

Qué invariante queda en la estructura: «el instalador nunca baja el número de envolturas `cron-avisa` del bloque
Orbit» queda en el propio instalador, y si no se cumple aborta antes de escribir. Qué queda en validación de borde:
que `cron-avisa` exista en el host (`test -x`). La herramienta sigue fuera del repo (ver decisiones).

Alineación de las otras «líneas exactas» con producción, con la misma causa y en la misma unidad: precios
Amazon (`DEPLOY.md:894-895`, más las constantes de `tests/test_precio_corrida.py`) toman la forma de
`servidor.txt:34-35`; costos y estimación ya están documentados envueltos (`DEPLOY.md:455`). Precios MeLi
(`DEPLOY.md:901-902`) se envuelve con `-E 75`, igual que Amazon. La cobertura de su salida 1 la pide el hallazgo
J-H1 original del reporte J, y se deja escrita como la quiera producción (paso 0).

### Datos que ya existen
No toca la base. En producción, el crontab de `gon` ya envuelve el ciclo, el vigía, precios, costos y ads-salud.
**Paso 0 (solo lectura, otro agente con plan):** guardar `crontab -u gon -l` en la evidencia y copiar al bloque
los `--nombre` exactos. **Paso de instalación (otro agente con plan):** correr el heredoc. El resultado esperado
es que las líneas ya envueltas no cambien y que se agreguen envolturas a structure, avisos y jev-senales. El
respaldo `crontab-gon.$STAMP` (`DEPLOY.md:1179`) sirve de reversa.

### Cambio de comportamiento en vivo
No cambia nada de lo que se escribe a Amazon ni a MeLi. Llegarán avisos nuevos por Telegram cuando structure,
avisos-campana o jev-senales salgan distinto de 0. Se enciende al instalar el bloque y se deshace con
`crontab -u gon <respaldo>`.

### Prueba (instalar en máquinas, y lo que ya pasó con precios MeLi y jev-senales)
Una función nueva en `tests/test_ingesta_cron.py`: `test_el_instalador_no_quita_cron_avisa`. Toma
`CRONTAB_DE_HOY` con las líneas del ciclo y de ads-salud en su forma envuelta y aplica `_reinstala`. Exige que
cada línea Orbit que antes llevaba `cron-avisa` siga con `cron-avisa` después. Hoy falla, igual que la sonda J5,
y pasa con el bloque nuevo. Las pruebas que fijan líneas (`test_impulso_vigia_cron.py`, `test_avisos_campana_cron.py`,
`test_jev_senales_cron.py`, `test_ads_salud_cron.py`, `test_placements_cron.py`, `test_compose_deploy.py`,
`test_precio_corrida.py`) se ajustan a mano a la forma nueva. No es una batería: son las mismas pruebas con el
texto nuevo.

### Alternativas
- **Sacar el bloque a `tools/despliegue/cron_orbit.sh instala|revisa`** (patrón de `cron_jev_vigia.sh`), corrido
  por `desplegar.sh` en el paso 5b. Pierde por ahora: obliga a mover el lector de todas las pruebas de cron de
  DEPLOY.md al script y a que `desplegar.sh` reescriba el crontab completo en cada despliegue. Es más superficie
  para cerrar la misma causa. Conviene si algún día el crontab se instala solo.
- **Filtro exacto (borrar solo líneas idénticas a las del bloque)** en vez de subcadenas. Pierde: así las líneas
  viejas o envueltas a mano quedan duplicadas junto a las nuevas, y dos ciclos al minuto 40 es peor que perder
  una envoltura.
- **Versionar `cron-avisa` en este repo.** Pierde: es una herramienta del host que también usa el crontab de root
  y otros servicios. Tenerla aquí partiría la propiedad. Basta con la comprobación `test -x`.

### Tamaño
`docs/DEPLOY.md` y unas 8 pruebas de cron (cambios de texto), más una función de prueba nueva. Sin código de
`app/`. Cabe en una unidad verificable.

---

## H-H2 = I-H3 = J-H2. El ciclo y el vigía tienen un solo horario y el salto sale 0

### Causa raíz
Son dos errores juntos:
1. **Falta el horario.** `docs/DEPLOY.md:1195,1197,1199` agenda el vigía, el ciclo US y el ciclo MX solo a las 08:20,
   08:40 y 08:41. El plan aprobado mandaba reintentos a las 10:xx, 13:xx y 16:xx (`plans/ingesta-01.md:56-60`) y
   ponía como criterio «cero días con el ciclo saltado todo el día» (`:114`). La ingesta de las 07:10 puede seguir
   hasta las 09:55 (`app/ads/diario.py:56-60`).
2. **El salto se calla.** `app/cli.py:113-115`, `app/cli_bids.py:125-127` y `:74-76`, y `app/jev_senales.py:809-811`
   imprimen `TEXTO_ESPERA` (`app/metricas_listas.py:26`) y salen 0 incluso cuando ya no queda ningún intento ese
   día. Eso contradice el contrato del vigía (`app/cli_bids.py:102-103`: «sin ingesta avisa y sale 1»). `cron-avisa`
   no ve un 0.

Repetir el ciclo es seguro porque es idempotente por día: `app/cycle.py:247-269` no abre un ciclo nuevo si hoy ya
hay uno `done`/`degraded` o uno `running` de menos de 3 h. El vigía es idempotente por `(impulso_id, corte)` y
excluye los impulsos ya cerrados (`app/impulso_io.py:870-877,1137-1149`). Avisos-campana marca lo ya enviado
(`app/cli_bids.py:27,77-84`, `_ya_salio`).

### Uso primero
Crontab (una línea por trabajo, como hoy; solo cambian las horas):
```cron
5 8,10,13,16 * * *  ... app.cli avisos-campana
20 8,10,13,16 * * * ... app.cli impulso-vigia
40 8,10,13,16 * * * ... app.cli cycle --platform amazon_us
41 8,10,13,16 * * * ... app.cli cycle --platform amazon_mx
```
Lo que ve el operador en un día con la ingesta tarde:
```text
08:40  sin métricas de ayer: espera al siguiente horario            (exit 0)
10:40  == Ciclo del optimizador (ads_optimizer:amazon_us) == ... status=done
13:40  cycle amazon_us: ya hay un ciclo done hoy; espera al siguiente horario   (exit 0)
```
Y en un día en que no llegó nada:
```text
16:40  cycle amazon_us: hoy no corrió; las métricas de ayer no llegaron en ningún intento de la ingesta (el último terminó a las 16:00 UTC). Mañana se intenta otra vez.   (stderr, exit 1 → Telegram de cron-avisa)
```
Sitios de llamada (los cuatro que hoy imprimen `TEXTO_ESPERA`):
```python
# app/cli.py:113
if not metricas_listas.guarda(conn, (args.platform,), forzar=args.forzar):
    return metricas_listas.salida_sin_metricas(f"cycle {args.platform}")

# app/cli_bids.py:125 (impulso-vigia)
if not metricas_listas.guarda(conn, plataformas, forzar=forzar):
    return metricas_listas.salida_sin_metricas("impulso-vigia")

# app/jev_senales.py:809
if not metricas_listas.guarda(lector, ("amazon_us", "amazon_mx"), forzar=args.forzar):
    return metricas_listas.salida_sin_metricas("jev-senales")
```

### Forma
```python
# app/metricas_listas.py  (el modulo ya es la unica fuente de "llegaron las metricas")

TEXTO_ESPERA = "sin métricas de ayer: espera al siguiente horario"  # sin cambio


def fin_de_intentos(hoy: dt.date) -> dt.datetime:
    """Momento UTC en que ya no puede llegar D-1 hoy: inicio del ultimo
    HORARIOS_INGESTA mas TOPE_PARED_SEGUNDOS (13:15 + 2 h 45 = 16:00).
    Derivado de app.ads.diario, no copiado (un numero, una fuente). Import
    local: diario importa este modulo."""
    raise NotImplementedError


def salida_sin_metricas(comando: str, *, ahora: dt.datetime | None = None) -> int:
    """Lo que hace un consumidor cuando `guarda` dijo que no.
    Antes de fin_de_intentos(hoy): imprime TEXTO_ESPERA en stdout y devuelve 0
    (queda otro horario hoy). Desde fin_de_intentos(hoy): imprime en stderr
    '<comando>: hoy no corrió; ...' y devuelve 1 para que cron-avisa avise:
    el dia se perdio. Nunca escribe en la base ni manda Telegram por su cuenta
    (el canal de 'trabajo que no hizo su trabajo' es cron-avisa, como en
    impulso-vigia R09-F)."""
    raise NotImplementedError
```
El horario vive en `docs/DEPLOY.md` (bloque y tabla de `:849-856`). La prueba exige que concuerde con
`HORARIOS_INGESTA` y `TOPE_PARED_SEGUNDOS`, con el mismo patrón que hoy fija los minutos del diario
(`tests/test_ingesta_cron.py:149-157`). Las horas de los consumidores no se duplican en Python. El código solo
conoce `fin_de_intentos` y lo deriva.

Por qué las horas 8, 10, 13 y 16 con el minuto de siempre en lugar de los 10:10/13:10/16:10 del plan: el plan
suponía la ingesta a las 10:15 (`plans/ingesta-01.md:57`) y la instalada corre a las 10:40 (`app/ads/diario.py:56`).
Con un minuto por trabajo cada uno se queda en una sola línea y con su `job_key`, y `cron-avisa` lo sigue como hoy.
Además cada intento cae después del plazo de una ingesta: 10:xx después del límite de las 07:10 (09:55); el ciclo
13:40 después del de las 10:40 (13:25); todos los de 16:xx después del de las 13:15 (16:00). El vigía de las 13:20 y
los avisos de las 13:05 alcanzan la ingesta de las 10:40 solo si termina rápido, y si no, la de 16:xx los cubre.
Es la misma cantidad de reintentos que el plan, en otros minutos. Lo pongo como decisión del dueño.

Textos que quedan verdaderos sin tocarlos: «mientras, el ciclo de pujas espera» (`app/ads/diario.py:124-137`) y
«espera al siguiente horario». Hay que actualizar el docstring del vigía (`app/cli_bids.py:100-103`) y el de
`metricas_listas` (`:3-8`) para decir «sale 0 mientras queda horario; sale 1 cuando ya no».

### Datos que ya existen
Ninguno. En 14 días hubo 15 ciclos completos por plataforma y ningún día perdido. La instalación del crontab la
hace otro agente con plan, usando el instalador de J-H1.

### Cambio de comportamiento en vivo
**Sí cambia lo que se escribe a Amazon.** En un día con la ingesta tarde, el ciclo decide y aplica bids, pausas,
negatives y harvest a las 10:40, 13:40 o 16:40 en vez de no hacer nada, y el vigía puede pausar o graduar un
impulso ese mismo día. En un día normal no cambia nada, porque los intentos posteriores salen por `motivo_de_salto`.
No hay shadow: son las mismas decisiones con el mismo interruptor de escalera y metas, solo que más tarde. Se
enciende instalando el bloque y se deshace con el respaldo del crontab. El código de `salida_sin_metricas` puede
desplegarse antes que el crontab: antes de las 16:00 sigue saliendo 0, así que no se nota (el 21:30 de Jev sí
saldría 1 si ese día no llegó nada, lo cual es cierto).

**Interacción con E-H3 (otro grupo):** el plan A.3 reintenta un ciclo `failed` el mismo día. Con reintentos, «el
siguiente ciclo» llega unas 2 h después y no 24 h. Si E-H3 sigue abierto, un intento sin sellar puede recibir un
segundo recorte el mismo día. Ver orden y decisiones.

### Prueba (falla que ya pasó en producción: 5 días saltados en 15 antes de ingesta-01; además dinero: impulsos)
Una función nueva en `tests/test_ingesta_cron.py`: `test_cada_consumidor_tiene_horario_tras_el_ultimo_plazo_de_ingesta`.
Sobre `_reinstala(...)`, para cycle US, cycle MX, impulso-vigia y avisos-campana, exige que haya un horario agendado
después del límite de cada ingesta (`HORARIOS_INGESTA[i] + TOPE_PARED_SEGUNDOS`, ventana abierta hasta el siguiente
inicio para los dos primeros). Exige también que el último horario sea mayor o igual que `fin_de_intentos`. Hoy falla
(solo 08:xx) y pasa con el bloque nuevo. El cambio de exit se comprueba corriendo la sonda `J/J1_ciclo_un_solo_horario.py`
con `ahora` en 16:40: debe dar exit 1.

### Alternativas
- **La ingesta dispara a los consumidores al terminar** (event-driven). Pierde: la ingesta corre con `ORBIT_DSN_INGEST`
  y el ciclo con `ORBIT_DSN_DECIDE`. Juntarlos en un proceso mezcla roles. Además ataría la duración del ciclo al
  plazo de pared de la ingesta, y un fallo del ciclo se vería como fallo de la ingesta.
- **Reintento cada hora (`40 8-16`)**. Funciona y cubre mejor la ingesta de las 10:40. Pierde por poco: el plan
  aprobó tres reintentos. Con escalera off cada intento deja un envelope `skipped` (`app/cycle.py:3752-3757`), así
  que serían 9 por día en vez de 4. Queda como respaldo si el dueño lo prefiere.
- **Un vigilante aparte del ciclo** (que `ads-salud` lea `optimizer_cycle`). Pierde: agrega un segundo juez del
  mismo hecho. El propio consumidor ya sabe a las 16:xx que perdió el día, y decirlo con su exit es más corto.

### Tamaño
`app/metricas_listas.py`, `app/cli.py`, `app/cli_bids.py`, `app/jev_senales.py`, `docs/DEPLOY.md` (horas del
bloque y de la tabla) y una función nueva en `tests/test_ingesta_cron.py`, más los ajustes de horas en las pruebas
de cron que fijan `5 8`/`20 8`/`40 8`/`41 8`. Una unidad.

---

## H-H3. La primera falla de SP-API (5xx/red/contrato) queda sin aviso

### Causa raíz
Tres módulos suponen cada uno que otro cubre el caso:
- `app/spapi/salud.py:139,172`: para la clase «resto» solo avisa con la segunda fallida seguida (`_racha_fallida`),
  porque espera que la repetición la produzca.
- `scripts/spapi-diario.sh:28-37`: la repetición no corre si ya pasó de las 06:15.
- `app/spapi/vigilante.py:52-53,58-67`: **primer lugar donde el sistema se equivoca.** El juez del día a las 07:30
  cuenta `ok=false` como presente («ya avisó por su camino»), y para una primera fallida «resto» eso es falso. La
  documentación repite el error en `docs/DEPLOY.md:1054-1063` («una corrida presente, aunque sea ok=false… no es silencio»),
  y la prueba `tests/test_spapi_vigilante.py:59` (`test_faltantes_ok_false_cuenta_como_presente`) lo fija.

Arreglarlo en el vigilante cierra todos los caminos: repetición saltada, repetición fallida, corrida manual que
falló. Es el único módulo que ve el día completo.

### Uso primero
Telegram de las 07:30 en el escenario del hallazgo:
```text
[Orbit] ALERTA SP-API: hoy faltan datos
ventana: 2026-11-09 04:30 UTC → 2026-11-09 07:30 UTC
faltan 1 de 8:
- spapi_pricing / amazon_mx: falló y no se repitió (http_5xx: 503 Service Unavailable)
Sin estos datos, la corrida de precios de las 13:10 no evalúa esa plataforma hoy.
Para recuperarla: docker exec orbit-app-1 python -m app.cli ingest spapi_pricing --platform amazon_mx antes de las 13:10 UTC.
```
Llamada en `main` (`app/spapi/vigilante.py:176-188`):
```python
pares = evalua(filas, desde=desde, hasta=hasta)
con_problema = [p for p in pares if p.estado is not Estado.PRESENTE]
for p in pares:
    print(f"- {p.fuente} / {p.plataforma}: {p.estado}")
print(f"faltan {len(con_problema)} de {len(pares)}")
if con_problema and not args.dry_run:
    notifica_spapi_silencio(con_problema, desde, hasta)
return 1 if con_problema else 0
```

### Forma
```python
# app/spapi/vigilante.py

class Estado(StrEnum):
    PRESENTE = "presente"  # al menos una corrida terminada con ok=true en la ventana
    FALLIDA = "fallida"    # hubo corridas terminadas y todas ok=false: el dia quedo sin dato
    AUSENTE = "ausente"    # ninguna corrida terminada (no corrio o quedo colgada)


@dataclass(frozen=True)
class Par:
    fuente: str
    plataforma: str
    estado: Estado
    motivo: str | None  # skip_reason de la ultima fallida si estado es FALLIDA; si no, None


def evalua(filas, *, desde: datetime, hasta: datetime) -> list[Par]:
    """Los 8 pares en orden de catalogo (FUENTES_SPAPI x PLATAFORMAS_SPAPI).
    filas: (source, platform, started_at, finished_at, ok, skip_reason).
    Reemplaza a `faltantes`: un solo juez del dia. Presente exige una ok=true
    (lo que precios necesita es la observacion de hoy, no "que algo corrio")."""
    raise NotImplementedError
```
- `_SQL_VENTANA` (`:76-79`) agrega `skip_reason`. Una sola consulta, como hoy.
- `faltantes` se borra y sus pruebas pasan a `evalua`. No dejo dos jueces.
- `app/notifica.py:783-803` `aviso_spapi_silencio(pares, desde, hasta)` recibe `Par` y escribe una línea por estado:
  «no corrió» o «falló y no se repitió (motivo)». El cierre se arma según los estados presentes: la pista del crontab
  si hay ausentes, la de precios y el comando manual si hay fallidas. Sigue siendo una función pura con `scrub` en
  el motivo.
- `salud.py` no cambia: «una alerta por racha» sigue igual para lo que pasa durante la noche. El vigilante emite el
  veredicto del día.
- Actualizar `docs/DEPLOY.md:1054-1063` y el docstring del módulo (`vigilante.py:3-10`).

Invariante: el estado queda como enum cerrado (tipo). La regla «presente = hay una ok» vive en `evalua`, que es una
función pura.

### Datos que ya existen
Ninguno. El vigilante solo lee.

### Cambio de comportamiento en vivo
No escribe en Amazon ni en MeLi. Habrá un Telegram más en los días en que un par termina sin dato. Si `salud.py` ya
avisó a las 05:xx (429/LWA, o segunda fallida), a las 07:30 llega también el veredicto del día. Lo acepto: son hechos
distintos («falló una corrida» contra «hoy no hay dato»). Se enciende con el despliegue y se deshace revirtiendo el
commit.

### Prueba
Fuera de los 4 casos del repo no hace falta prueba nueva. La comprobación que falla hoy y pasa con el arreglo es
invertir la prueba existente `tests/test_spapi_vigilante.py:59` a `test_evalua_ok_false_sin_ok_es_fallida`, con el
historial de la sonda `H/h2`: debe dar `fallida` con motivo `http_5xx…`. Se agrega el caso «ok=false y después ok=true
en la ventana = presente» dentro de la misma prueba. La sonda `H/h2` adaptada debe imprimir un par con problema.

### Alternativas
- **`salud.py` avisa también en la primera fallida «resto».** Pierde: rompe «una alerta por racha» (SP-API 01 A.5)
  y da ruido en los 5xx pasajeros que la repetición sí salva, que son la mayoría.
- **`spapi-diario.sh` sale 1 si queda algún par fallido, envuelto en `cron-avisa`.** Pierde: depende de una
  herramienta del host que no está en el repo, el aviso es genérico y deja sin cubrir la corrida manual o la que
  falla fuera del script.
- **Quitar o mover el tope de las 06:15.** Pierde: el tope protege la ventana antes del sync de estructura de las
  06:45 (`scripts/spapi-diario.sh:6-9`), y sin tope la repetición puede volver a fallar igual.
- **Quitar duplicados haciendo que el vigilante replique la regla de alertas de `salud`.** Pierde: dos módulos con
  la misma regla (fuga de información). El ahorro, un mensaje en días ya malos, no lo paga.

### Tamaño
`app/spapi/vigilante.py`, `app/notifica.py` (el builder), `docs/DEPLOY.md` (un párrafo) y
`tests/test_spapi_vigilante.py` (migración de `faltantes` a `evalua` y una prueba invertida). Una unidad.

---

## J-H4. El despliegue recrea el contenedor sin volver a mirar si hay un trabajo en vuelo

### Causa raíz
`tools/despliegue/desplegar.sh:104-130` mira las guardas una sola vez. Después vienen respaldo, `pg_dump`,
migraciones, copia y build (`:139-200`), y al final `compose_up` en `:201`. `tools/despliegue/comun.sh:48-51`
`compose_up` hace `up -d --no-deps --build app`, que junta en un solo comando de minutos el build (inofensivo) y la
recreación (la que mata los `docker exec`). Solo comprueba el candado ssh. `revierte` llama a `compose_up` sin
guardas (`comun.sh:281`), y lo mismo pasan `rollback.sh:11-15` y la reversa interna de `desplegar.sh:108-117`, que
corre justo cuando las guardas están ocupadas. La verdad de «hay un trabajo vivo» ya existe: es la cuenta `app.cli`
de `docker top` (`comun.sh:192-193`). El problema es que se lee minutos antes de recrear.

### Uso primero
Salida de `desplegar.sh`, paso 5:
```text
== (5) codigo de <sha>
md5 OK: 412 archivos
imagen construida (el contenedor vivo no se toco)
espero a que orbit-app-1 no tenga procesos app.cli (plazo 600 s)
procesos app.cli vivos (1); reviso en 5 s
recreado; /health 200; .../sha = <sha>
```
Sitios de llamada (la única forma de recrear):
```bash
# desplegar.sh:201
rc=0; recrea_contenedor || rc=$?
case "$rc" in
  0) ;;
  75) sal_75_con_cambios "procesos app.cli vivos al recrear" ;;   # mismo camino que :108-122
  *) echo "ABORTA: no pude recrear orbit-app-1" >&2; exit 1 ;;
esac
# comun.sh:281 (revierte)
... && recrea_contenedor && espera_salud; then
```

### Forma
```bash
# tools/despliegue/comun.sh

# recrea_contenedor: UNICA forma de recrear orbit-app-1 (desplegar paso 5 y revierte).
# 1) build de la imagen sin tocar el contenedor vivo; 2) en UN solo comando remoto,
# espera a que `docker top` no tenga procesos app.cli (plazo $ESPERA) y recrea sin
# build, sin soltar el shell entre la ultima mirada y el `up`.
# Sale 0 recreado; 75 plazo agotado con procesos vivos (NO recreo); otra = error.
# Idempotente: correrlo dos veces reconstruye (cache) y recrea otra vez la misma imagen.
recrea_contenedor() {
  sigue_candado
  srv "docker compose -p orbit --project-directory $SRV -f $SRV/docker-compose.yml build app" || return 1
  echo "imagen construida (el contenedor vivo no se toco)"
  sigue_candado
  # TODO pseudocodigo del comando remoto:
  #   fin = now + ESPERA
  #   loop:
  #     n = cuenta 'app\.cli' en `docker top orbit-app-1` (contenedor parado => n = 0)
  #     si n == 0: break
  #     si now >= fin: exit 75
  #     sleep 5
  #   docker compose ... up -d --no-deps --no-build app      # mismo shell, sin ssh de por medio
  echo "recrea_contenedor: no implementado" >&2; return 1
}

# sal_75_con_cambios <motivo>: el bloque de desplegar.sh:108-122 extraido como funcion
# (anota guardas_con_cambios, corre revierte, sale 75 o 1). Lo usan el paso 1 y el paso 5.
```
- `compose_up` se borra y sus dos llamadores pasan a `recrea_contenedor`. Así queda una sola forma de recrear.
- `estado_guardas` y el paso 1 no cambian. Las guardas de base (harvest en cola, ciclos `running`) son política de
  despliegue: no cambiar código a media harvest. La guarda de recreación es técnica y de procesos: no matar un proceso
  vivo. Son dos preguntas distintas y cada una se responde en su momento.
- En `revierte`, si `recrea_contenedor` da 75, el código queda en `PEND` y se llega a `rollback_incompleto`
  (semántica existente, `comun.sh:311-319`). Volver a correr `rollback.sh` continúa.

Ventana residual: el tiempo entre la última lectura de `docker top` y el SIGTERM de `compose up --no-build`, dentro
del mismo shell remoto. Calculo menos de un segundo, aunque no lo medí. Un cron que dispare justo ahí, o falla el
`docker exec` (sale distinto de 0 y `cron-avisa` avisa con J-H1; H-H2 lo reintenta), o muere durante el arranque de
Python antes de abrir la base. Esto es inferido: el import de `app` y la conexión tardan más que esa ventana. No
queda una escritura a medias.

Invariante: «solo se recrea con cero procesos `app.cli` vistos en el mismo shell» queda en la estructura, porque
hay una sola función que recrea.

### Datos que ya existen
Ninguno. Los ciclos `running` huérfanos que haya dejado un despliegue anterior los cierra el rastro del claim
(claim con TTL y rastro de huérfanos desde `app/cycle.py:363`, según el reporte J).

### Cambio de comportamiento en vivo
No escribe en Amazon ni en MeLi. El despliegue puede tardar hasta `$ESPERA` más, o salir 75 (revirtiendo, como ya
hace con guardas ocupadas) si un trabajo dura más que el plazo. Se deshace revirtiendo el commit de `tools/despliegue/`.

### Prueba (instalar/deshacer en máquinas)
Una función nueva en `tests/test_despliegue_motor.py`, con la simulación existente (`ORBIT_SIMULACION=1`, el doble
de `docker` lee `$C/top`, `:91-128`). Guardas libres en el paso 1. El doble de `docker`, al ver `compose ... build`,
pone `UID PID CMD\n10001 9 python -m app.cli cycle` en `$C/top`. Con `AUTO03_ESPERA_S=1` exige que `docker.log` no
tenga ninguna línea `compose ... up` después del build y que la salida sea 75 o 1, nunca 0. Hoy falla porque
`compose_up` recrea sin mirar. Pasa con el arreglo. Necesita Postgres, así que corre en Linux o CI y no en esta Mac.

### Alternativas
- **Candado compartido con los trabajos:** cada línea de cron toma `flock -s /tmp/orbit-contenedor.lock` y el
  despliegue toma `flock -x` alrededor de la recreación. Cierra la ventana del todo. Pierde: obliga a tocar todas las
  líneas que hacen `docker exec` (también las de root y las que no son del bloque), `flock` no es justo (con trabajos
  encadenados el exclusivo puede esperar sin fin) y queda un segundo mecanismo de exclusión además del claim del ciclo
  y los `flock -n` existentes. La ventana residual del diseño elegido no deja escrituras a medias.
- **Volver a correr `estado_guardas` completo justo antes de `compose_up`.** Pierde: sigue separado por un ssh y
  minutos de build (con `--build` adentro), así que no cierra la ventana. Además mezcla las guardas de política (que
  pueden bloquear días, sospecha 1 del reporte J) con la técnica, y deja la reversa sin poder recrear.
- **Ventana horaria para desplegar.** Pierde: el dueño la descartó (se despliega 24/7).

### Tamaño
`tools/despliegue/comun.sh`, `tools/despliegue/desplegar.sh` y una función en `tests/test_despliegue_motor.py`, más
la extensión del doble de `docker` para `compose build`. `docs/DEPLOY.md` (sección «Despliegue por el motor»), un
párrafo. Una unidad.

---

## Orden propuesto

| # | Unidad | Depende de | Por qué en este lugar |
|---|---|---|---|
| 1 | **J-H1** crontab canónico con `cron-avisa` y candado del instalador | paso 0 de lectura del crontab | Es la base: H-H2 avisa con exit 1 a través de `cron-avisa` y edita el mismo bloque. Además, cualquier reinstalación antes de esto borra envolturas. |
| 2 | **H-H2** reintentos y `salida_sin_metricas` | 1, y el arreglo de E-H3 (ver decisión 4) | Sin envoltura, el exit 1 del último intento no avisa. Con E-H3 abierto, el reintento del mismo día puede recortar dos veces. |
| 3 | **H-H3** vigilante | ninguna | Independiente, puede ir en paralelo con 1. |
| 4 | **J-H4** recreación atómica | ninguna | Independiente. Conviene antes o junto con 2, porque los reintentos agregan horas de cron que un despliegue puede cruzar (10:xx, 13:xx, 16:xx). |

Cada unidad es un PR. 1 y 2 cambian el crontab de producción a través del mismo instalador, y lo instala otro agente
con plan. El código de 2 puede desplegarse antes que su crontab sin efecto, porque antes de las 16:00 sigue saliendo 0.

## Decisiones del dueño

1. **¿Los reintentos del ciclo, del vigía y de los avisos van a las 10, 13 y 16 con el minuto de siempre (10:40,
   13:40, 16:40 para el ciclo US) en lugar de 10:10/13:10/16:10 como dice el plan?** Recomiendo que sí. Son los mismos
   tres reintentos, cada trabajo sigue en una sola línea, y quedan detrás de la ingesta real de las 10:40 (el plan
   suponía 10:15).
2. **El día que no llega ninguna métrica, ¿está bien que avise cada trabajo que lo perdió (avisos 16:05, vigía 16:20
   solo si hay impulsos vivos, ciclo US 16:40, ciclo MX 16:41, Jev 21:30)?** Recomiendo que sí: cada mensaje nombra
   algo distinto que no pasó, y ese día ya es raro (el plan lo aceptó, `plans/ingesta-01.md:64-66`). La alternativa
   es que solo avisen el ciclo y el vigía, con una segunda forma de manejar el salto en avisos y Jev, que es lo que
   la regla de una sola forma desaconseja.
3. **¿`ingest diario` y `spapi-diario.sh` se quedan sin `cron-avisa`?** Recomiendo que sí. Sus fallas ya avisan por
   ads-salud y por el vigilante arreglado, y envolverlos solo duplicaría mensajes.
4. **¿El reintento del mismo día también vuelve a correr un ciclo que terminó `failed`, como dice el plan A.3?**
   Recomiendo que sí, pero solo con E-H3 cerrado antes o en el mismo lote. Si E-H3 se retrasa, mientras tanto el
   reintento debería saltar a una plataforma con un ciclo `failed` hoy. Ese cambio va en `motivo_de_salto`
   (`app/cycle.py:265`) y se quita al cerrar E-H3.
5. **¿`cron-avisa` sigue solo en el host?** Recomiendo que sí, con la comprobación `test -x` en el instalador. Si el
   dueño quiere poder reinstalar el servidor desde cero, que se versione en el repo de infraestructura del host, no
   en Orbit.

## Riesgos

- **Semántica de `cron-avisa` inferida.** No está en el repo. Tomo de la descripción del brief que avisa con salida
  distinta de 0 e ignora la 75. No sé si incluye stderr en el mensaje, así que el texto de `salida_sin_metricas`
  podría no llegar a David y solo llegaría «<nombre> falló». Hay que comprobarlo con una lectura del script antes del
  paso 1.
- **Los `--nombre` del ciclo y del vigía en producción no los vi.** Si el bloque no los copia exactos, el primer
  `--revisa` de `cron-avisa` puede tratar las líneas como tareas nuevas o viejas. El paso 0 lo resuelve.
- **La ventana residual de J-H4 no está medida.** Supongo que el arranque de Python más la conexión duran más que el
  hueco entre `docker top` y el SIGTERM. Si no fuera así, la alternativa del candado compartido lo cierra.
- **La API (uvicorn) también muere al recrear.** Una petición en vuelo, por ejemplo `POST /api/ads-optimizer/reversa/bid`,
  se corta. No está en estos hallazgos y `docker top` no la ve. Lo dejo anotado para el lead.
- **Envelopes `skipped` repetidos.** Con escalera off, los reintentos dejan hasta 4 envelopes `skipped` por día por
  plataforma (`app/cycle.py:3752-3757`). No mueve dinero.
