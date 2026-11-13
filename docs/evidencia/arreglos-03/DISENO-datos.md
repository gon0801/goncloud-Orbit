# ARREGLOS 03, frente DATOS Y TAREAS: diseño

Base: `plan/arreglos-02` nacida de master `a4644aad` (lo que corre en producción). Fecha: 2026-11-12/13 UTC.
Hallazgos: `docs/evidencia/arreglos-2026-11-12/ingesta.md` (F1 a F5 y movimientos 1 y 4) y `docs/evidencia/arreglos-2026-11-12/estimacion.md` (F3 a F9 y movimientos E4, E5 y E6).
Bloques: `plans/arreglos-03.md` (D-A a D-C y D-E a D-L; la sección «B-EST», que era D-D, no entra al plan). Las pruebas nuevas se llaman `test_arreglos03_<dos letras del bloque en minúscula>_<qué comprueba>` (D-E da `arreglos03_de_`).
Lecturas de producción: todas con `docs/evidencia/arreglos-01/lee_prod.sh`, `desplegado.sh`, `en_prod.sh` y `migracion_aplicada.sh`. La consulta va junto a cada número.

## Orden y por qué

Primero los borrados, cada uno en su bloque, antes de los arreglos que tocan los mismos archivos:

| Bloque | Qué deja resuelto | Depende |
| --- | --- | --- |
| D-A | Se borra el segundo camino de métricas (`ingest metrics`) | - |
| D-B | Se borran las entradas de reputación que el cron no usa y Apify | - |
| D-C | Se borran el experimento de JEV y la revisión por decisión | - |
| (D-D) | No entra al plan: ver la nota de «B-EST» | - |
| D-E | El «ayer» de Amazon US llega completo (día cerrado, guarda y horario) | D-A |
| D-F | MeLi no guarda el día en curso; el Resumen usa la misma regla | D-E |
| D-G | Un search term partido no se publica con 0 pedidos | D-E |
| D-H | Las devoluciones de MeLi traen su etiqueta y el margen de MeLi vuelve | ARREGLOS 02 completo |
| D-I | Las ventas de US guardan su importe en dólares | D-H |
| D-J | Estimación y JEV avisan cuando no hicieron nada | - |
| D-K | Las alertas de reputación salen solas y una vez; se avisa si la lectura no corrió | D-B |
| D-L | Reputación: caída de nivel, preguntas fantasma, reclamos vacíos | D-K |

D-E, D-F y D-G van en cadena porque cambian los mismos planificadores de `app/ads/reports.py`. D-K ya no edita `tools/despliegue/crontab-orbit.txt`, así que no espera a D-E. D-H y D-K traen una fila .0 que agrega `arreglos03` a `migraciones_de` (`tools/despliegue/comun.sh:199-202`, hoy `_(auto03|orbit07|claridad01|arreglos01)_`; `grep -q arreglos03 tools/despliegue/comun.sh` sale 1 hoy). Si el bloque base de ARREGLOS 03 ya lo hace, esas filas no cambian nada.

---

## DC. Una sola regla de «día cerrado» por plataforma (F1, F2, movimiento 1). Bloques D-E y D-F

### Qué pasa hoy

La ingesta pide y guarda hasta «hoy UTC menos 1». Desde el 1-nov, a las 07:10 UTC en Los Ángeles son las 23:10: el «ayer» de US se guarda con una hora menos y los reintentos ya no lo piden. MeLi copia a las 08:15 UTC la fila del día local en curso (unas dos horas de gasto) y la guarda como dato.

### Recorrido con horas reales

Salida de `zoneinfo` (corrida en esta sesión) para el 12-nov (invierno) y el 15-jul (verano):

```
11-12 07:10Z  MX 01:10 cerrado 11-11 | US 23:10 cerrado 11-10 | MeLi 01:10 cerrado 11-11 | UTC D-1 11-11
11-12 08:00Z  MX 02:00 cerrado 11-11 | US 00:00 cerrado 11-11
11-12 08:10Z  MX 02:10 cerrado 11-11 | US 00:10 cerrado 11-11
11-12 09:40Z  MX 03:40 cerrado 11-11 | US 01:40 cerrado 11-11
07-15 07:10Z  MX 01:10 cerrado 07-14 | US 00:10 cerrado 07-14
07-15 08:10Z  MX 02:10 cerrado 07-14 | US 01:10 cerrado 07-14
```

- El día de US queda completo a las 08:00 UTC en invierno y a las 07:00 UTC en verano. MX y MeLi (Ciudad de México, sin horario de verano) cierran a las 06:00 UTC todo el año.
- Con la regla nueva, a las 07:10 UTC de invierno la guarda de US pediría el 10-nov, no el 11. El ciclo de las 08:40 pediría el 11-nov de US, que la corrida de las 07:10 ya no guarda (está abierto). El ciclo saldría 75 y se iría al reintento de las 10:40, que choca con la ingesta de las 10:40: en la práctica, el ciclo de US de cada día de invierno pasaría a las 13:40. Por eso hay que mover horarios.

Duración real de la corrida de las 07:10 en 30 días (consulta abajo): mediana de 9 a 10 min, p90 de 21 a 32 min, máximo de 48 min. Hoy el ciclo tiene 90 min de holgura (07:10 a 08:40). Si solo se moviera la ingesta a las 08:10, el ciclo de las 08:40 tendría 30 min y caería al reintento uno de cada diez días; avisos-campana (08:05) correría antes de la ingesta todos los días.

**Horario nuevo** (todo lo de la mañana se recorre una hora y conserva su holgura de hoy):

| Tarea | Hoy (UTC) | Con D-E (UTC) |
| --- | --- | --- |
| `ingest diario --solo-faltantes` | 07:10, 10:40, 13:15 | 08:10, 10:40, 13:15 |
| `avisos-campana` | 08:05, 10:05, 13:05, 16:05 | 09:05, 10:05, 13:05, 16:05 |
| `impulso-vigia` | 08:20, 10:20, 13:20, 16:20 | 09:20, 10:20, 13:20, 16:20 |
| `cycle` US / MX | 08:40/41, 10:40/41, 13:40/41, 16:40/41 | 09:40/41, 10:40/41, 13:40/41, 16:40/41 |

`fin_de_intentos` (`app/metricas_listas.py:39`) no cambia: el último intento sigue a las 13:15 y la guarda da el día por perdido a las 16:00. `jev-senales` (09:30), `ads-salud` (desde las 10:30), costos (08:15) y SP-API (05:00) no cambian. El horario vive en `tools/despliegue/crontab-orbit.txt` y en `HORARIOS_INGESTA` (`app/ads/diario.py:56`); la receta de despliegue lo instala con `cron_orbit.sh` (bloque N de ARREGLOS 01).

### Lo que cambia en la guarda del ciclo, con todas sus letras

La guarda que usa el motor de pujas (`metricas_listas.guarda`, llamada en `app/cli.py:115`, `app/cli_bids.py:72` y `:126`, `app/jev_senales.py:811`) **cambia lo que considera «listo»**:

- Hoy: existe una corrida ok iniciada hoy (fecha UTC) que escribió filas con `metric_date` = fecha UTC de hoy menos 1, para esa plataforma.
- Con D-E: existe una corrida ok iniciada hoy (fecha UTC) que escribió filas con `metric_date` = `ultimo_dia_cerrado(plataforma, ahora)`.

A las 09:40 UTC (la hora nueva del ciclo) las dos fechas son la misma para MX y US, en invierno y en verano (tabla de arriba). Lo que cambia para el motor es que esa fecha de US ya llega completa. Solo antes de las 08:00 UTC (invierno) o 07:00 UTC (verano) la guarda de US pediría un día antes; a esa hora no corre ningún consumidor. La ventana de bids termina en `max(metric_date) - 3` días y no cambia de fecha.

### Forma elegida

Uso del llamador:

```python
from app.dia_cerrado import ultimo_dia_cerrado, dia_local, fin_del_dia, ZONAS

hasta = ultimo_dia_cerrado("amazon_us", ahora)      # date: el último día que ya terminó allá
hoy_mx = dia_local("meli", ahora)                     # date: el día que corre en México
cierre = fin_del_dia("amazon_us", fecha)              # datetime UTC: las 00:00 locales del día siguiente
```

Módulo nuevo `app/dia_cerrado.py` (solo biblioteca estándar, sin base ni red, para que lo importen la ingesta, las guardas y el Resumen sin ciclos):

```python
ZONAS: Mapping[str, str] = {
    "amazon_mx": "America/Mexico_City",   # el perfil MX de Amazon cierra su día aquí
    "amazon_us": "America/Los_Angeles",   # el perfil US cierra su día aquí
    "meli": "America/Mexico_City",        # site MLM
}

def dia_local(plataforma: str, instante: dt.datetime) -> dt.date:
    if instante.tzinfo is None:
        raise ValueError("instante sin zona")          # astimezone lo tomaría como hora de la máquina
    if plataforma not in ZONAS:
        raise ValueError(f"plataforma sin zona: {plataforma}")
    return instante.astimezone(ZoneInfo(ZONAS[plataforma])).date()

def ultimo_dia_cerrado(plataforma: str, instante: dt.datetime) -> dt.date:
    return dia_local(plataforma, instante) - dt.timedelta(days=1)

def fin_del_dia(plataforma: str, fecha: dt.date) -> dt.datetime:
    siguiente = fecha + dt.timedelta(days=1)
    return dt.datetime.combine(siguiente, dt.time.min, tzinfo=ZoneInfo(ZONAS[plataforma])).astimezone(dt.UTC)
```

Dónde se usa (los cinco «hoy UTC» del hallazgo y la copia del Resumen):

1. **Escritores de Amazon** (`app/ads/reports.py:1686` `escribir_grupo`; `app/ads/placements.py:190` `ingest_placements`). `_SQL_FECHA_HOY` (`reports.py:424`) pasa a leer `now()`; por cada reporte se calcula `ultimo_cerrado = ultimo_dia_cerrado(perfil.platform, ahora)`. Los cuatro planificadores (`_planea_filas` `:727`, `_planea_filas_terminos` `:893`, `_planea_filas_productos` `:1160`, `_planea_filas_placements` `placements.py:111`) cambian el parámetro `hoy` por `ultimo_cerrado` (obligatorio) y conservan su comparación `metric_date > ultimo_cerrado`. El motivo de descarte pasa de «fila con metric_date futura» a «fila de un día que aún no cierra» (en placements, «fila de placements de un día que aún no cierra»), también en `_SKIPS_SIN_ACTIVIDAD_DEL_RANGO` (`reports.py:1583`). Nadie fuera de `reports.py` lee ese texto (lo comprobé con `rg "metric_date futura" app`).
2. **Rango que pide el diario** (`app/ads/diario.py:316-320`): `fecha_fin = min(ultimo_dia_cerrado(p, ahora) for p in PAIS_POR_PLATAFORMA)` y `fecha_ini = fecha_fin - 30 días` (el mismo ancho de hoy: 31 días). Es la misma regla, usada para no pedir lo que se va a tirar; el filtro del escritor la cubre si alguien pasa `--fecha-fin` a mano.
3. **Guarda** (`app/metricas_listas.py:144`, `:155`, `:167`, `:180`): `metricas_de_ayer_listas`, `productos_de_ayer_listos`, `placements_de_ayer_listos` y `guarda` cambian `hoy: dt.date | None` por `ahora: dt.datetime | None`. El día exigido es `ultimo_dia_cerrado(plataforma, ahora)`; el día de la corrida sigue siendo la fecha UTC de `ahora`. Con `ahora=None` las cuatro leen el reloj de una sola función del módulo, `_ahora() -> dt.datetime` (`dt.datetime.now(dt.UTC)`), que las pruebas fijan con `monkeypatch` cuando el llamador (por ejemplo `jev_senales.main`) no recibe reloj. `diario._grupo_al_dia` (`:178`) y `diario._paises_rescatados` (`:195`, una fecha por plataforma) reciben `ahora`.
4. **MeLi** (`app/meli/ads_source.py:416` `leer_origen`): recibe `ahora: dt.datetime | None = None`. `hoy_local = dia_local("meli", ahora)`. `_fila` (`:312`) sigue rechazando una fecha posterior a `hoy_local` (eso sí es futuro y aborta el snapshot, como hoy). Una fila con fecha igual a `hoy_local` se salta sin abortar y se cuenta en `OrigenMeliAds.sin_cerrar` (campo nuevo, `int`, por omisión 0). `app/meli/ads_ingest.py` suma ese número a `rows_skipped` y al `skips` que ya reciben `_skip_reason` y `_skip_reason_reintento`, con el motivo «día local en curso descartado». Las marcas `as_of=`, `futuro=`, `piso=` y `snap=` del `skip_reason` no cambian (sus expresiones regulares, `ads_ingest.py:152-155`, siguen encontrándolas).
5. **Resumen** (`app/dashboard_resumen.py`): `Canal` pierde `zona` (`:120`). `hoy_por_canal` (`:207`) usa `dia_local`. `_fin_del_dia` (`:528`) se borra y `_cerrado` llama `fin_del_dia(canal.plataforma, dia.fecha)`. La respuesta sigue declarando `"zonas"` (`:955`), ahora desde `ZONAS`.

### La otra forma que descarté

Pedir a Amazon un rango distinto por perfil (US hasta un día antes) dentro de `app/ads/descarga.py` (`_Logico`, `:561`). Descartada: toca el módulo compartido de sondeo, no cubre a MeLi ni al relleno manual, y la regla 6 es de lo que se guarda, no de lo que se pide. El filtro en el escritor deja la regla en el punto donde se escribe.

También descarté leer la zona del perfil de Amazon de la base: el perfil trae `timezone` en la API (`app/ads/structure.py:7`) pero Orbit no la guarda (`rg timezone migrations` no da nada) y la zona de un marketplace no cambia.

### Qué se borra

Los cinco «hoy UTC» de la ingesta y la guarda (`diario.py:320`, las tres consultas de `metricas_listas.py`, `reports.py:1718`, `placements.py:235`, `meli/ads_source.py:427`), el campo `Canal.zona` y `_fin_del_dia` del Resumen.

### Pruebas

- D-E (datos, instala): `test_arreglos03_de_diario_no_pide_el_dia_en_curso_de_us_en_invierno`, la reproducción `test_diario_pide_el_dia_en_curso_de_us_en_invierno` de `pruebas/ingesta/test_rev_ingesta.py` sin cambios, en `tests/test_ingest_diario.py`. Falla hoy con `pide hasta 2026-11-11, pero en US son las 23:10`.
- D-F (datos): `test_arreglos03_df_meli_descarta_el_dia_local_en_curso` en `tests/test_meli_ads_ingest.py`. Es la reproducción F2 con el reloj fijo: `leer_origen(ruta, ahora=2026-11-13T03:17Z)` (21:17 del 12-nov en México) con filas del 11 y del 12; espera solo el 11, `sin_cerrar == 1` y cero rechazos. Hoy falla porque `leer_origen` no acepta `ahora`.
- El cambio de horario rompe y obliga a actualizar: `tests/test_ingest_diario.py:77,82-88,238,641,645` (todas las «07:10» pasan a «08:10», incluido el mensaje final de `test_agotado_en_el_ultimo_horario_mensaje_final`), `tests/test_avisos_campana_cron.py:25`, `tests/test_impulso_vigia_cron.py:25` y `tests/test_metricas_listas.py:128` (la prueba S de ARREGLOS 01: `{8, 10, 13, 16}` pasa a `{9, 10, 13, 16}`; su regla, un horario después de las 16:00, no cambia). Lo comprobó el lector de este plan aplicando el horario en una copia: sin esos cambios fallan 2 de 51.
- Trampa: las pruebas que siembran D-1 con el reloj real hoy salen en verde según la hora. `tests/test_metricas_listas.py:44-45` (`HOY`, `AYER`; comentario de `:41`), `_metricas_de_ayer` de `tests/test_jev_senales.py:951-969` (siembra `(now() AT TIME ZONE 'UTC')::date - 1` y llama a `jev_senales.main` sin reloj) y `_hoy` de `tests/test_cli_bids.py:81-82` si lo que siembra lo lee la guarda. Con la regla nueva fallarían entre las 00:00 y las 08:00 UTC en invierno, y a otra hora no prueban nada. Fijan `ahora` (un instante de invierno a las 07:30 UTC, cuando US y MX dan días distintos), lo pasan a la guarda o fijan `metricas_listas._ahora`, y siembran `ultimo_dia_cerrado(plataforma, ahora)`.

### Prueba real

D-E: `docs/evidencia/arreglos-03/D-E/prueba.py` por `en_prod.sh` más el crontab instalado. Contenido (lo corrí hoy contra producción y falla con `ModuleNotFoundError: No module named 'app.dia_cerrado'`):

```python
import datetime as dt

from app.ads.diario import HORARIOS_INGESTA
from app.dia_cerrado import ultimo_dia_cerrado

invierno = dt.datetime(2026, 11, 12, 7, 10, tzinfo=dt.UTC)
assert ultimo_dia_cerrado("amazon_us", invierno) == dt.date(2026, 11, 10), "US a las 23:10 de LA"
assert ultimo_dia_cerrado("amazon_mx", invierno) == dt.date(2026, 11, 11), "MX ya cerro"
assert ultimo_dia_cerrado("amazon_us", invierno.replace(hour=8)) == dt.date(2026, 11, 11), "US cierra 08:00 UTC"
assert HORARIOS_INGESTA[0] == "08:10", f"primer horario {HORARIOS_INGESTA[0]}"
print("D-E desplegado")
```

D-F: `docs/evidencia/arreglos-03/D-F/prueba.py` por `en_prod.sh`. Comprueba lo que es cierto en cuanto el código está desplegado (hoy falla con `ModuleNotFoundError: No module named 'app.dia_cerrado'`):

```python
import dataclasses
import datetime as dt
import inspect

from app import dashboard_resumen
from app.dia_cerrado import dia_local
from app.meli.ads_source import OrigenMeliAds, leer_origen

noche = dt.datetime(2026, 11, 13, 3, 17, tzinfo=dt.UTC)
assert dia_local("meli", noche) == dt.date(2026, 11, 12), "21:17 del 12-nov en Mexico"
assert "ahora" in inspect.signature(leer_origen).parameters, "leer_origen sin reloj"
assert "sin_cerrar" in {f.name for f in dataclasses.fields(OrigenMeliAds)}, "sin contador del dia en curso"
assert not hasattr(dashboard_resumen, "_fin_del_dia"), "el Resumen conserva su copia de zonas"
print("D-F desplegado")
```

El dato en producción solo aparece con la primera copia de MeLi Ads posterior al despliegue (08:15 UTC), así que va en una fila de evidencia que no detiene el bloque (D-F.5), programada con `at` para las 08:45 UTC siguientes. Su `.sql` (`docs/evidencia/arreglos-03/D-F/dia-en-curso.sql`): la última corrida de MeLi Ads con filas escritas no trae el día local en curso. Hoy da `False` (corrida 1556 del 12-nov 08:15 escribió el 12-nov).

```sql
BEGIN READ ONLY;
SELECT count(*) = 0 FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE e.platform = 'meli'
   AND o.ingest_run_id = (SELECT max(r.id) FROM ingest_run r
                           WHERE r.source = 'accounting_meli_ads_campaigns' AND r.ok AND r.rows_written > 0)
   AND o.metric_date >= (o.observed_at AT TIME ZONE 'America/Mexico_City')::date;
ROLLBACK;
```

### Medido en producción

```
-- primera observación de US contra la siguiente (del reporte; la confirmé con esta):
SELECT v.metric_date, min(o.observed_at) FROM ads_metric_observation o JOIN ad_entity e ON e.id=o.ad_entity_id ... WHERE e.platform='amazon_us'
2026-11-10|11-11 07:13      2026-11-11|11-12 07:15

-- MeLi: el día en curso guardado
SELECT v.metric_date, max(v.observed_at), sum(v.cost) FROM v_metric_latest v JOIN ad_entity e ON e.id=v.ad_entity_id
 WHERE e.platform='meli' AND e.kind='campaign' AND v.metric_date >= current_date-3 GROUP BY 1
2026-11-10|11-11 08:15|834.0000   2026-11-11|11-12 08:15|827.0000   2026-11-12|11-12 08:15|19.0800

-- duración de la corrida de las 07:10, 30 días, solo ok
SELECT source, count(*), percentile_disc(0.5) ..., percentile_disc(0.9) ..., max(finished_at-started_at)
  FROM ingest_run WHERE source IN ('amazon_ads_reports_v3','amazon_ads_products_v3','amazon_ads_placements_v3')
   AND started_at > now()-interval '30 days' AND ok AND extract(hour from started_at AT TIME ZONE 'UTC')=7 GROUP BY 1
placements 30 | 0:10:29 | 0:29:12 | 0:46:26
products   29 | 0:10:04 | 0:31:51 | 0:38:20
reports    24 | 0:09:31 | 0:20:46 | 0:48:06
```

### Cruces

- ARREGLOS 01 N y S: D-E edita `tools/despliegue/crontab-orbit.txt` y lo instala `cron_orbit.sh`. El número de líneas con `cron-avisa` no cambia, así que la guarda del instalador no aborta. La prueba de S (`tests/test_metricas_listas.py:106-130`) fija las horas `{8, 10, 13, 16}` en la línea 128: D-E la cambia a `{9, 10, 13, 16}`. Su regla (un horario después de las 16:00) se conserva. La `Acepta` de ARREGLOS 01 S.4 (`grep -c '8,10,13,16' tools/despliegue/crontab-orbit.txt` igual a 4) da rojo después de D-E: no se vuelve a correr como regresión.
- ARREGLOS 04 W-C apoya el reloj de las pantallas en `hoy_resumen` (`app/dashboard_resumen.py:220`). D-F.3 conserva su firma y hace que lea, por `hoy_por_canal`, la tabla `ZONAS` de `app/dia_cerrado.py`: funciona en cualquier orden de entrega.
- TEMPORADA 01 H.3 programa con `at` un chequeo «para las 09:05 UTC del día siguiente al cambio (el ciclo de Ads corre a las 08:40)». Con D-E el ciclo corre a las 09:40 y ese chequeo vería «aún no hay ciclo». Parche textual para `plans/temporada-01.md`, fila H.3: cambiar «para las 09:05 UTC del día siguiente al cambio (el ciclo de Ads corre a las 08:40)» por «para las 10:05 UTC del día siguiente al cambio (después del ciclo de Ads, que corre a las 08:40 o, con ARREGLOS 03 D-E, a las 09:40)», y en la misma fila «también para las 09:05 UTC del día de cada transición» por «también para las 10:05 UTC del día de cada transición». Las 10:05 sirven en cualquier orden de entrega.
- ARREGLOS 02 no toca este arreglo.

---

## ST. Search terms: un total incompleto no se publica (F3). Bloque D-G

### Qué pasa hoy

En `_planea_filas_terminos` (`app/ads/reports.py:893`) una fila con pedidos ilegibles se salta sola (`:976-983`) y la fila hermana se guarda como total: un término que vendió 2 queda con 0. Si dos filas se vacían al sumarse, la clave se borra (`:1031-1047`) y una tercera fila la vuelve a crear con solo sus números.

### Forma elegida

La regla de productos («una fila inválida o una suma vaciada invalidan la clave para todo el reporte», `_envenenar_clave_producto` `:1133` y `envenenadas` `:1336-1339`) pasa a ser una sola función que usan los dos planificadores:

```python
def _envenenar_clave(clave: tuple, *, por_clave: dict, plan: list, envenenadas: set, skips: Counter) -> None:
    previa = por_clave.pop(clave, None)
    if previa is not None:
        plan.remove(previa)
        skips["fila de clave envenenada (subtotal parcial)"] += 1
    envenenadas.add(clave)
```

En `_planea_filas_terminos`, con `clave = (external_id, search_term, metric_date)`:

```
si clave en envenenadas: skips["fila de clave envenenada (subtotal parcial)"] += 1; seguir
métrica ilegible (ValueError)  -> _envenenar_clave(clave, ...); contar el motivo de hoy; seguir
fila sin ninguna métrica       -> _envenenar_clave(clave, ...); contar el motivo de hoy; seguir
fila con clics o gasto y pedidos vacíos (purchases7d ausente o nulo)
                               -> _envenenar_clave(clave, ...); skips["fila con pedidos vacíos"] += 1; seguir
suma que queda vacía           -> _envenenar_clave(clave, ...) en lugar del del/remove de hoy
```

Los negativos siguen abortando la corrida (`:986-998`), igual que hoy.

Por qué la fila con pedidos vacíos también invalida la clave: hoy se guarda con `orders` en NULL, y `jev_senales._SQL_HISTORIAL` suma `COALESCE(sum(orders) FILTER (WHERE orders IS NOT NULL), 0)`: un día con 2 pedidos que llegó vacío cuenta 0 pedidos y sus clics sí cuentan. Medido por el adversario: 0 filas de search terms con `orders` NULL en 90 días (250,281 filas), así que la regla no tira nada de hoy; cierra el riesgo a futuro.

### La otra forma que descarté

El movimiento 2 completo (un planificador y un escritor genéricos para los cuatro reportes, unas 700 líneas menos). Resuelve F3 por construcción, pero cambia los cuatro caminos y los conteos de `skip_reason` de una vez. Aquí basta con que términos use la regla de productos; el movimiento 2 queda para un plan propio.

### Qué se borra

`_envenenar_clave_producto` (queda `_envenenar_clave`) y el borrado sin veneno de `:1031-1047`.

### Prueba (dinero)

Las dos reproducciones de `pruebas/ingesta/test_rev_ingesta.py` (`test_terminos_fila_invalida_no_envenena_la_clave` y `test_terminos_par_envenenado_se_recrea_con_la_tercera_fila`) pasan a `tests/test_reports_pipeline.py` como `test_arreglos03_dg_fila_ilegible_invalida_la_clave` y `test_arreglos03_dg_clave_vaciada_no_se_recrea`, con `ultimo_cerrado=` en lugar de `hoy=` (D-E ya renombró el parámetro). Una tercera, `test_arreglos03_dg_clics_sin_pedidos_invalida_la_clave`: una fila con clics y gasto y `purchases7d` vacío más una hermana con 0 pedidos dan un plan vacío.

Prueba real: `docs/evidencia/arreglos-03/D-G/prueba.py` por `en_prod.sh` (hoy falla con `TypeError: ... unexpected keyword argument 'ultimo_cerrado'`):

```python
import datetime as dt

from app.ads.reports import SEARCH_TERMS_CFG, _planea_filas_terminos

DIA = dt.date(2026, 11, 1)


def fila(**m):
    return {"date": DIA.isoformat(), "adGroupId": 111, "searchTerm": "arras boda", **m}


def plan(filas):
    p, _ = _planea_filas_terminos(SEARCH_TERMS_CFG, filas, ultimo_cerrado=DIA, fecha_ini=DIA, fecha_fin=DIA)
    return [(x.search_term, x.clicks, x.orders) for x in p]


assert plan([fila(clicks=12, cost=9.5, purchases7d="2?", sales7d=40.0),
             fila(clicks=30, cost=21.0, purchases7d=0, sales7d=0.0)]) == [], "fila ilegible no invalida la clave"
assert plan([fila(clicks=5, cost=1.0, purchases7d=None, sales7d=None),
             fila(clicks=None, cost=None, purchases7d=2, sales7d=40.0),
             fila(clicks=30, cost=20.0, purchases7d=0, sales7d=0.0)]) == [], "tercera fila recrea la clave"
assert plan([fila(clicks=12, cost=9.5, purchases7d=None, sales7d=40.0),
             fila(clicks=30, cost=21.0, purchases7d=0, sales7d=0.0)]) == [], "clics sin pedidos no invalida la clave"
print("D-G desplegado")
```

### Cruces

Ninguno fuera de este frente. Va después de D-E porque los dos cambian `_planea_filas_terminos`.

---

## DEV. Las devoluciones de MeLi traen su etiqueta (F5). Bloque D-H

### Qué pasa hoy

`_lookup_kind` (`app/ledger.py:214-232`) toma la etiqueta de una devolución de `fee_category`. MeLi no la manda: las 60 devoluciones de MeLi quedan con `fee_type` vacío, la guarda `fees_sin_tipo` de las vistas de margen las cuenta y el margen de MeLi sale vacío siempre.

### Respuesta a la pregunta c: se corrige al ingerir, y las 60 guardadas se corrigen una vez con una migración

Medido:

```
SELECT platform, kind, coalesce(fee_type,'<NULL>'), count(*) FROM ledger_event GROUP BY 1,2,3
  ... meli|refund|<NULL>|60 ...  (todas las demás filas de cargos de las tres plataformas traen etiqueta)
SELECT ... FROM ledger_event WHERE platform='meli' AND kind='refund'
  60 filas, cada una con su order_id, montos de -299 a -1989 MXN: son la venta completa devuelta, no publicidad.
```

En contabilidad, la publicidad nunca llega como `event_type='refund'`: entra por `sync_ads_to_ledger.py` como `fee` con categoría `ads` (repo `goncloud-accounting`, `scripts/sync_ads_to_ledger.py:62,97`). Así que una devolución sin categoría es una devolución.

Forma elegida:

1. Al ingerir: en `_lookup_kind`, rama `refund` (`:220-223`), `fee_type = fee_category or "refund"`. Una devolución que sí trae categoría la conserva (una devolución con categoría `ads` sigue siendo `ads` y queda fuera del margen, como hoy).
2. Las 60 guardadas: migración `<n>_arreglos03_devoluciones_meli.sql` que, en una sola transacción, apaga el trigger de solo-agregar de `ledger_event`, pone `fee_type = 'refund'` en las filas de MeLi `kind = 'refund' AND fee_type IS NULL` y lo vuelve a encender. Solo MeLi, igual que la reversa: la migración corre el día del despliegue, no el de D-H.1, y una devolución de Amazon sin etiqueta que entre en medio debe quedar vacía (su margen sale vacío, el lado seguro) en lugar de sumarse al margen de Amazon sin que la reversa la regrese. La corre el usuario `orbit` de la receta (`tools/despliegue/comun.sh:53`), que es dueño de la tabla y superusuario (medido: `SELECT tableowner, rolsuper ...` da `orbit|True`). El trigger vigente se llama `ledger_solo_reatribuye` (medido en `pg_trigger`); si al abrir el PR se llama distinto, se usa el nombre vigente.

```sql
-- sonda-aplicada: SELECT NOT EXISTS (SELECT 1 FROM ledger_event WHERE platform = 'meli' AND kind = 'refund' AND fee_type IS NULL)
-- ARREGLOS 03 D-H (F5 de la revisión del 2026-11-12): las devoluciones de MeLi
-- llegaban sin categoría y la guarda fees_sin_tipo anulaba el margen de MeLi.
-- Una devolución sin categoría es una devolución: la publicidad nunca llega como
-- refund desde contabilidad. Única mutación: fee_type de NULL a 'refund' en
-- kind = 'refund' de MeLi. Monto, fecha, orden y producto no cambian.
-- Sin BEGIN/COMMIT: la receta la aplica en una transacción (psql -1).
ALTER TABLE ledger_event DISABLE TRIGGER ledger_solo_reatribuye;
UPDATE ledger_event SET fee_type = 'refund' WHERE platform = 'meli' AND kind = 'refund' AND fee_type IS NULL;
ALTER TABLE ledger_event ENABLE TRIGGER ledger_solo_reatribuye;
```

Reversa `<n>_reversa_arreglos03_devoluciones_meli.sql`: lo mismo con `UPDATE ledger_event SET fee_type = NULL WHERE platform = 'meli' AND kind = 'refund' AND fee_type = 'refund'`. Es exacta: antes de D-H ninguna devolución de MeLi traía etiqueta, y después de la reversa el código viejo vuelve a escribirlas vacías.

La lista de las 60 filas (id, orden, fecha, monto) se guarda antes de desplegar en `docs/evidencia/arreglos-03/D-H/antes.txt`: es el rastro de qué se cambió.

### La otra forma que descarté

Corregir al leer: que la guarda de las vistas no cuente `kind = 'refund'`. Descartada por tres razones medidas: la guarda vive copiada en cinco migraciones (`rg -c fees_sin_tipo migrations` da 0015, 0016, 0018, 0021 y 0048), así que habría que reescribir tres o cuatro vistas completas; ARREGLOS 02 y TEMPORADA 01 I reescriben esas mismas vistas, y una tercera reescritura obliga a coordinar el texto de las tres; y la etiqueta seguiría mal en el dato para cualquier otro lector. Corregir el dato arregla todas las copias de la guarda de una vez y no toca ninguna vista. La regla de solo-agregar se respeta en la ingesta; la corrección es una sola, versionada, con reversa y con la lista guardada.

### Qué se borra

Nada.

### Prueba (dinero: el margen alimenta la meta de ACoS)

`test_arreglos03_dh_devolucion_sin_categoria_queda_como_refund` en `tests/test_ledger_sync.py`: `mapear_destino` de una fila MeLi `event_type='refund'`, `fee_category=None`, monto -889.0 da `fee_type == 'refund'`; con `fee_category='ads'` da `'ads'`. Hoy falla (da `None`).

Prueba de la migración (escribe datos de producción): `test_arreglos03_dh_migracion_devoluciones_y_su_reversa` en `tests/test_ledger_atribucion.py`, con `_db_0085()` (0001 y el trigger de la 0085). Una devolución de MeLi y una de Amazon con `fee_type` vacío; tras la migración la sonda da `True`, la de MeLi queda `refund`, la de Amazon sigue vacía y un UPDATE de otra columna sigue rechazado; tras la reversa la de MeLi vuelve a vacía y la sonda da `False`.

### Medido y lo que cambia

```
SELECT platform, margen_neto_pct, fees_sin_tipo, cobertura, dias_con_venta FROM v_target_margen_plataforma
amazon_mx|40.33|0|0.9969|89   amazon_us|23.64|0|0.9878|74   meli|None|11|1.0000|71
SELECT platform, count(*) FILTER (WHERE fees_sin_tipo > 0), count(*) FROM v_margen_producto GROUP BY 1
amazon_mx|0|159   amazon_us|0|58   meli|18|102
```

Con D-H, `fees_sin_tipo` de MeLi baja de 11 a 0 y el margen de MeLi se calcula (cobertura 1.0 y 71 días pasan las otras guardas); los 18 productos de MeLi recuperan su margen. Amazon no cambia: ya no tiene ninguna fila sin etiqueta. Ninguna decisión del motor usa hoy el margen de MeLi (el ciclo solo corre Amazon).

### Cruces

- ARREGLOS 02 (bloque D de `plans/arreglos-02.md`, leído en esta sesión) corrige la comisión de Amazon MX contada dos veces en las vistas, no en `app/ledger.py`: crea `v_ledger_cargo` y las cuatro vistas de margen (`v_target_margen_plataforma`, `v_margen_producto`, `v_economia_producto`, `v_margen_familia`) leen sus cargos de ahí (migración `0086_arreglos02_cargos.sql`). D-H no toca vistas ni choca con esa migración. La regla de `v_ledger_cargo` compara `fee_type` solo entre filas `fee_estimate` de Amazon y su comisión real; las devoluciones de MeLi no entran ahí. Su bloque A.2 suma `arreglos02` a `migraciones_de`; D-H.0 suma `arreglos03` detrás. Orden: ARREGLOS 02 entra antes; D-H no necesita rebasar código suyo.
- TEMPORADA 01 I reescribe `v_target_margen_plataforma`, `v_margen_producto` y `v_margen_familia` «con las mismas guardas». No choca. Su texto de Ajustes «hay cargos de Amazon sin clasificar» sale de `fees_sin_tipo` de las plataformas de Amazon, que ya están en 0.

---

## ORIG. Las ventas de US guardan su importe en dólares (F4). Bloque D-I

### Qué pasa hoy

Contabilidad entrega las ventas y cargos de US ya en pesos. Orbit guarda solo los pesos. El desglose fiscal en dólares del payload se tira porque su moneda no coincide (`app/ledger.py:245-272`, `:357-358`).

### Respuesta a la pregunta b: el importe en dólares sí viene para las ventas y no viene para los cargos

`raw_payload` no se guarda en Orbit (`ledger_event` no tiene esa columna; columnas leídas de `information_schema.columns`), así que la forma la tomé del código que lo escribe, en `goncloud-accounting` (commit `83b16e3`):

- Ventas de US (`scripts/sync_amazon.py:478-560`): `raw_payload = dict(item)`, el item de pedido de SP-API con `ItemPrice`, `ItemTax`, `ShippingPrice`, `ShippingTax`, `ShippingDiscount`, `ShippingDiscountTax`, `PromotionDiscount`, `PromotionDiscountTax`, cada uno `{"CurrencyCode": "USD", "Amount": "..."}`, más `fx_source` (`exact` o `nearest_prior`). El monto en pesos es `gross_price × tasa`, con `gross_price = ItemPrice + ItemTax + ShippingPrice + ShippingTax − ShippingDiscount − ShippingDiscountTax − PromotionDiscount − PromotionDiscountTax` (`:486-494`). La tasa no viaja; `exact` quiere decir que es la del día de la venta.
- Cargos, devoluciones y retenciones de US (`scripts/sync_amazon_finance_v2024.py:801-830, 948-957`): el payload trae `orig_currency` y `fx_source`, pero no el monto original. Ese importe no viene.

Lo confirma el dato de Orbit: `ventas con desglose (item_price): amazon_us 0/626` (sonda F4 del reporte), porque los bloques vienen en USD y la moneda del monto es MXN.

### Forma elegida

Tres columnas nuevas en `ledger_event`, migración `<n>_arreglos03_ledger_original.sql`:

```sql
-- sonda-aplicada: SELECT count(*) = 3 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'ledger_event' AND column_name IN ('amount_original', 'amount_original_currency', 'fx_fecha')
-- ARREGLOS 03 D-I (F4, regla 4): el importe original además del convertido.
-- amount_original NULL con amount_original_currency puesto = "se convirtió desde esa
-- moneda y la fuente no entregó el original" (cargos de US). Las tres NULL = no hubo
-- conversión. fx_fecha NULL = no se sabe qué día de tasa usó contabilidad.
ALTER TABLE ledger_event
    ADD COLUMN amount_original money_amount,
    ADD COLUMN amount_original_currency currency,
    ADD COLUMN fx_fecha DATE;
ALTER TABLE ledger_event
    ADD CONSTRAINT ledger_original_con_moneda
        CHECK (amount_original IS NULL OR amount_original_currency IS NOT NULL),
    ADD CONSTRAINT ledger_original_otra_moneda
        CHECK (amount_original_currency IS NULL OR amount_original_currency <> amount_currency),
    ADD CONSTRAINT ledger_original_mismo_signo
        CHECK (amount_original IS NULL OR sign(amount_original) = sign(amount));
```

Los CHECK aceptan NULL a propósito (NULL es «no aplica» o «no viene»). Reversa: `DROP CONSTRAINT` de los tres y `DROP COLUMN` de las tres. El trigger `ledger_solo_reatribuye` compara la fila entera menos `product_id` en JSON y no se ve afectado por columnas nuevas.

En `app/ledger.py`:

```python
def _original_de(payload: dict, kind: str, moneda: str, dia: date) -> tuple[Decimal | None, str | None, date | None]:
    # ventas: suma de los ocho bloques de SP-API si todos los presentes traen la misma
    # CurrencyCode y es distinta de `moneda`; un bloque ausente vale 0 (igual que contabilidad);
    # un bloque con otra moneda o ilegible -> (None, None, None). Resultado cuantizado a 4
    # decimales y > 0, si no -> None con la moneda puesta.
    # fee/refund/withholding: si payload["orig_currency"] está en MONEDAS y es distinta de
    # `moneda` -> (None, orig_currency, fx). Si no -> (None, None, None).
    # fx = dia si payload.get("fx_source") == "exact", si no None.
```

`EventoLedger` (`:113`) suma `amount_original`, `amount_original_currency`, `fx_fecha`. `mapear_destino` (`:338`) los llena con `_original_de(payload, kind, moneda, _dia(fila.event_date))`. `_COLS` y `_VALS` (`:497-502`) y la tupla de parámetros del INSERT suman las tres. La decisión 8 del docstring (`:21-22`) dice que el desglose fiscal de US sigue vacío y por qué.

### Lo que no se inventa

- Los cargos de US quedan con `amount_original` vacío y `amount_original_currency = 'USD'`: se declara que hubo conversión y que la fuente no dio el original. Dividir pesos entre una tasa sería inventarlo.
- Las 626 ventas de US ya guardadas quedan vacías: el libro es de solo agregar y el payload no se guardó. Desde el despliegue cada venta nueva de US trae su importe en dólares. Para conciliar ventas viejas está la otra fuente en dólares que Orbit ya tiene, los pedidos de SP-API.
- El desglose fiscal de US (`item_price` y compañía) sigue vacío: esas columnas están en la moneda del monto y guardar dólares ahí mezclaría monedas. Ninguna vista lo usa para US.

### La otra forma que descarté

Guardar en `amount` el importe en dólares (lo que pide el comentario de `migrations/0001_initial.sql:509-510`, «SIEMPRE el monto en su moneda ORIGINAL»). Descartada: la vista de margen exige que venta, costo y cargos estén en la misma moneda (`0016`, `c.cost_currency = l.amount_currency` y la guarda de moneda única), y los costos y cargos de US llegan en pesos; el margen de US se vaciaría.

### Qué se borra

Nada.

### Prueba (dinero)

`test_arreglos03_di_venta_de_us_guarda_su_importe_en_dolares` en `tests/test_ledger_sync.py`: una venta US con ItemPrice 120.00, ItemTax 9.60, ShippingPrice 5.00, ShippingTax 0.40 y PromotionDiscount 10.00 en USD y `fx_source: exact` da `amount_original == 125.0000`, `'USD'` y `fx_fecha` igual al día; con `nearest_prior` da `fx_fecha` vacío; un cargo US con `orig_currency: USD` da `(None, 'USD', día)`; una venta MX da las tres vacías. Hoy falla (el evento no tiene esos campos).

La migración solo agrega columnas y CHECK que aceptan NULL: no escribe datos ni configuración, así que no lleva prueba propia de sonda y reversa (la plantilla de `tests/test_despliegue_motor.py` la ejecuta). Su reversa borra los dólares guardados desde el despliegue: es lo esperado.

### Prueba real

`docs/evidencia/arreglos-03/D-I/prueba.py` por `en_prod.sh`, más la sonda de la migración (`migracion_aplicada.sh arreglos03_ledger_original`). Es cierto en cuanto el código está desplegado; hoy falla con `ImportError: cannot import name '_original_de'`:

```python
import datetime as dt
from decimal import Decimal

from app.ledger import _original_de

dia = dt.date(2026, 11, 12)


def usd(monto):
    return {"CurrencyCode": "USD", "Amount": monto}


venta = {"ItemPrice": usd("120.00"), "ItemTax": usd("9.60"), "ShippingPrice": usd("5.00"),
         "ShippingTax": usd("0.40"), "PromotionDiscount": usd("10.00"), "fx_source": "exact"}
assert _original_de(venta, "sale", "MXN", dia) == (Decimal("125.0000"), "USD", dia), "venta exacta"
assert _original_de({**venta, "fx_source": "nearest_prior"}, "sale", "MXN", dia) == (Decimal("125.0000"), "USD", None), "tasa de otro dia"
assert _original_de({"orig_currency": "USD", "fx_source": "exact"}, "fee", "MXN", dia) == (None, "USD", dia), "cargo sin original"
print("D-I desplegado")
```

La primera venta de US con dólares en producción llega con el libro de las 08:15 UTC: esa lectura va programada con `at` para las 08:45 UTC siguientes al despliegue, en una fila de evidencia que no detiene el bloque (D-I.5).

### Medido

```
SELECT column_name, data_type FROM information_schema.columns WHERE table_name='ledger_event'
  (17 columnas, sin payload ni original)
pedido 111-0178528-5045877 (12-nov): ledger 2917.74 MXN, SP-API 157.26 USD -> 18.554; fx_rate de Orbit ese día 18.659  (sonda F4 del reporte)
```

### Cruces

ARREGLOS 02 no cambia `app/ledger.py`. Su vista nueva `v_ledger_cargo` es `SELECT l.* FROM ledger_event l ...`: Postgres fija la lista de columnas al crear la vista, así que las tres columnas de D-I no entran a la vista ni la rompen (`ALTER TABLE ... ADD COLUMN` no choca con vistas que dependen de la tabla). TEMPORADA 01: no toca `ledger_event`.

---

## OK. Las tareas que dicen «ok» sin hacer nada (estimación F3, JEV F4). Bloque D-J

### Respuesta a la pregunta d: una regla, y `cron-avisa` hace el resto

Regla: **cero elementos procesados = salida distinta de 0**. Cómo está envuelta cada tarea:

| Tarea | Envoltura | Evidencia | ¿La salida 1 llega a Telegram? |
| --- | --- | --- | --- |
| Estimación | cron de root: `45 */6 * * * /usr/local/bin/cron-avisa ... '/mnt/data/appdata/orbit/refresh_estimacion.sh'` | `docs/evidencia/claridad-01/F/servidor.txt` (capturado 2026-11-09) y `docs/DEPLOY.md:467` | Sí: `tools/refresh_estimacion.sh:38` guarda en `sal` la salida de la ingesta y `:44` sale con ella |
| JEV | `tools/despliegue/crontab-orbit.txt:18`: `cron-avisa ... '/usr/bin/flock -n -E 75 ... jev-senales --aplicar'` | el archivo del repo, instalado por el bloque N | Sí: `flock` devuelve la salida del comando; solo el candado ocupado da 75 |
| Reputación | `30 9 * * * /usr/local/bin/cron-avisa --nombre "Reputación de MercadoLibre" --cada 24 -- ... reputacion snapshot --fuente meli` y `30 10 * * * /usr/local/bin/cron-avisa --nombre "Alertas de reputación" --cada 24 -- ... reputacion alertas`, en el bloque `# === Orbit reputacion (` | crontab vivo de `gon`, leído el 2026-11-13 (líneas 33-36) | Sí, si el comando sale distinto de 0. No avisa si la tarea no arranca, y las alertas solo viajan en el resumen del ciclo. D-K agrega eso a la revisión de las 10:30 |

La tabla de estimación y JEV sale de la evidencia nombrada; la fila de reputación, del crontab vivo.

### Estimación (F3)

Hoy `ok = True` siempre (`app/estimacion_ingest.py:528`) y `main` sale 1 solo si una plataforma Amazon se queda sin oferta fresca (`:644`). Sin cliente MeLi, toda MeLi se salta y sigue en 0 (`:405-409`).

Forma elegida:

```python
# _Tally (:116) suma dos contadores por plataforma
intentadas: Counter[str]   # publicaciones que llegaron a cotizarse (oferta resuelta y persistida)
cotizadas: Counter[str]    # cotización persistida con estado distinto de "error"

def plataformas_sin_cotizar(intentadas: Counter, cotizadas: Counter) -> tuple[str, ...]:
    return tuple(sorted(p for p, n in intentadas.items() if n and not cotizadas[p]))
```

- `intentadas[platform] += 1` justo antes de `_cotizar_item`; una excepción de cotización (`SKIP_FEE_EXCEPCION`) ya contó como intentada.
- `cotizadas[platform] += 1` cuando la cotización se persistió y su estado no es `error`.
- Sin cliente MeLi: `intentadas["meli"] += n_sin_cliente` (`:408`).
- `ok = not sin_cotizar`; `ResultadoIngestaEstimacion` (`:99`) suma `sin_cotizar: tuple[str, ...] = ()`.
- `main`: por cada plataforma sin cotizar escribe a stderr «estimación: ninguna publicación de <Amazon México | Amazon Estados Unidos | Mercado Libre> quedó cotizada (<n> intentadas); el motor de precios no la evaluará hoy», y sale `1 if (resultado.sin_oferta_fresca or not resultado.ok) else 0`.

Otra forma descartada: un umbral («menos del 50 % cotizado = falla»). Un número así no está en el diseño v2 y daría avisos con ruido; el caso que dañó es el de cero.

Prueba (dinero: sin cotización el motor de precios deja de evaluar): la reproducción `test_todas_las_cotizaciones_fallan_y_la_corrida_queda_ok` pasa a `tests/test_estimacion_fees.py` como `test_arreglos03_dj_cotizaciones_todas_fallidas_sellan_ok_falso`.

### JEV (F4)

Hoy `jev_senales.main` llama `correr(...)` (`:815`) e ignora cómo cerró; siempre sale 0 (`:836`). El vigía (`app/jev_vigia.py:33-37`) solo mira que haya un `inicio`.

Forma elegida, en `app/jev_senales.py`:

```python
MOTIVOS_SIN_JUICIO: frozenset[MotivoCierre] = frozenset({"sin_api_key", "proveedor_caido", "tope"})
TEXTO_SIN_JUICIO = {
    "sin_api_key": "falta la llave del proveedor de JEV",
    "proveedor_caido": "el proveedor de JEV no respondió",
    "tope": "se acabó el tope diario de consultas de JEV",
}
...
cierre = correr(...)
if cierre.motivo in MOTIVOS_SIN_JUICIO:
    print(f"jev-senales: no se juzgó ninguna búsqueda: {TEXTO_SIN_JUICIO[cierre.motivo]}."
          " La pantalla Gasto sin venta sigue con las lecturas anteriores.", file=sys.stderr)
    return 1
```

`cron-avisa` manda el aviso. El vigía no cambia: sigue cuidando lo que la salida no ve (que la tarea no arranque). La reproducción `test_vigia_calla_si_jev_corre_sin_juzgar` no se promueve: con la salida 1 el aviso ya sale una vez, y el vigía repetiría la misma señal.

Prueba (falla que ya pasó: 12 cierres `sin_api_key` del 7 al 12-oct y 7 `tope` del 12 al 14-oct, medido abajo): `test_sin_llave_el_job_sale_0_y_no_avisa` pasa a `tests/test_jev_senales.py` como `test_arreglos03_dj_sin_llave_jev_sale_1`, con la aserción `salida == 1`.

### Medido

```
SELECT cierre, count(*), min(at)::date, max(at)::date FROM jev_corrida WHERE evento='fin' GROUP BY 1
completa|21|2026-10-15|2026-11-12   sin_api_key|12|2026-10-07|2026-10-12   tope|7|2026-10-12|2026-10-14
```

### Cruces

Ninguno con ARREGLOS 02 ni TEMPORADA 01. D-C borra código de JEV en otros archivos (`jev_ads`, `jev_asesor`, `jev_catalogo`, `api_dashboard`); D-J toca solo `jev_senales.py`.

---

## REP-A. Reputación: las alertas salen solas y se avisa si la lectura no corrió (estimación F5 y F7). Bloque D-K

### Qué pasa hoy

Horario instalado (crontab de `gon`, leído el 2026-11-13, líneas 33-36):

```
30 9 * * * /usr/local/bin/cron-avisa --nombre "Reputación de MercadoLibre" --cada 24 -- /bin/bash -c 'docker exec orbit-app-1 python -m app.cli reputacion snapshot --fuente meli' >> .../reputacion-meli.log 2>&1
30 10 * * * /usr/local/bin/cron-avisa --nombre "Alertas de reputación" --cada 24 -- /bin/bash -c 'docker exec orbit-app-1 python -m app.cli reputacion alertas' >> .../reputacion-alertas.log 2>&1
```

Las dos líneas ya van envueltas en `cron-avisa`, así que una salida distinta de 0 ya llega a Telegram. `ejecuta_snapshot` (`app/reputacion.py:649-711`) sale 1 si falla y también si corre sin dejar lectura («cero snapshots con inputs validos», `:703-709`): el caso «corre pero no deja lectura» ya avisa a las 09:30. Lo que falta:

- **La lectura que no corrió.** Si la línea de las 09:30 no arranca, nadie avisa, y `reputacion alertas` evalúa en silencio con la última lectura que haya (`ejecuta_alertas`, `app/reputacion_alertas.py:397-429`). Del 28-sep al 16-oct no hubo ninguna corrida, ni fallida (medido abajo).
- **Las alertas.** Solo viajan dentro del resumen del ciclo de Ads (`app/notifica.py:682-689`), solo las de las últimas 24 h (`VENTANA_DIGEST_HORAS`) y salen dos veces (MX y US). Si el ciclo no corre ese día, la alerta no llega nunca (F7).

La versión anterior de este diseño (línea nueva `reputacion avisa` a las 10:45) partía del crontab del 21-oct, sin `cron-avisa`. Con el horario de hoy duplicaba avisos: una lectura que falla mandaba «Reputación de MercadoLibre» a las 09:30 y otro casi igual a las 10:45. Se descarta.

### Forma elegida: lo que falta va en la revisión de las 10:30, sin línea nueva

`reputacion alertas` ya corre a las 10:30, después de la lectura, con `ORBIT_DSN_DECIDE` y envuelta en `cron-avisa`. Ahí se agregan las dos cosas que faltan. No cambia ningún horario ni el crontab.

```python
# app/reputacion_alertas.py
def lectura_de_hoy(conn, hoy: dt.date) -> Literal["ok", "fallida", "ausente"]:
    # la última ingest_run source='reputacion_meli' (SOURCE_MELI) iniciada hoy (UTC):
    # ok=true -> "ok"; ok=false -> "fallida"; ninguna -> "ausente"

def pendientes_de_aviso(conn) -> list[dict]:
    # reputation_alert WHERE NOT resolved AND avisada_at IS NULL, críticas primero y por antigüedad

def avisa_pendientes(conn, *, envia=notifica.envia_aviso) -> bool:
    pendientes = pendientes_de_aviso(conn)
    if not pendientes:
        return True
    if not envia(notifica.aviso_reputacion(pendientes)):
        return False                                   # avisada_at queda vacío: mañana se reintenta
    conn.execute("UPDATE reputation_alert SET avisada_at = now() WHERE id = ANY(%s)", (ids,))
    return True

def ejecuta_alertas(fecha=None, dry_run=False) -> int:
    ... evalua_y_persiste como hoy ...
    if fecha is not None or dry_run:
        return 0                                       # relleno manual o ensayo: no avisa
    salida = 0
    if lectura_de_hoy(conn, hoy) == "ausente":
        print("La lectura de reputación de Mercado Libre de hoy no corrió. Las alertas y la"
              " pantalla de reputación siguen con la lectura del <fecha de la última ok>.", file=sys.stderr)
        salida = 1
    if not avisa_pendientes(conn):
        print("el aviso de alertas de reputación no salió", file=sys.stderr)
        salida = 1
    return salida
```

Una lectura `"fallida"` no hace salir 1 a la revisión: esa falla ya la avisó `cron-avisa` a las 09:30. Para que «ausente» quiera decir «no corrió» y no «se cayó antes de empezar», `ejecuta_snapshot` deja su corrida fallida también cuando cae antes de que `_corre_meli` la abra:

```python
# app/reputacion.py, ejecuta_snapshot, fuente meli y sin ensayo
try:
    cliente = ClienteMeli(MeliCredentials.from_secrets_dir(secrets_dir), transport=transport)
except Exception as exc:
    _sella_caida_temprana(dsn, exc)    # abre y sella una ingest_run reputacion_meli ok=false con el
    print(...); return 1               # nombre de la clase del error; si la base no responde, nada
```

Si la base tampoco responde, la revisión de las 10:30 tampoco puede conectarse y sale 1 con su propio mensaje, como hoy.

`notifica.aviso_reputacion(alertas) -> str` arma el texto llano: «[Orbit] Reputación de Mercado Libre» y una línea por alerta, «Urgente: <mensaje>» para críticas y «Aviso: <mensaje>» para las demás. Los mensajes guardados ya están en llano y solo traen ids y números (docstring de `reputacion_alertas.py:16-18`).

Migración `<n>_arreglos03_reputacion_avisada.sql`:

```sql
-- sonda-aplicada: SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'reputation_alert' AND column_name = 'avisada_at')
ALTER TABLE reputation_alert ADD COLUMN avisada_at TIMESTAMPTZ;
ALTER TABLE reputation_alert ADD CONSTRAINT reputation_alert_avisada_despues
    CHECK (avisada_at IS NULL OR avisada_at >= created_at);
-- Las 5 alertas que ya existen salieron en el resumen del ciclo del día siguiente
-- (ciclos del 9-sep y del 18-oct, medidos): no se reenvían.
UPDATE reputation_alert SET avisada_at = created_at;
GRANT UPDATE (avisada_at) ON reputation_alert TO app_decide;
```

Reversa: `DROP CONSTRAINT`, `DROP COLUMN` (el GRANT de columna cae con ella).

Se quita del resumen del ciclo: el bloque que carga reputación en `app/notifica.py:682-689`, las líneas del digest `:515-517` y `carga_reputacion_digest` (`reputacion_alertas.py:354`). `_lineas_reputacion` (`notifica.py:524`) se borra o se vuelve `aviso_reputacion`. Con eso deja de salir dos veces.

`docs/DEPLOY.md` (tabla de crons de reputación, `:2405-2410`) muestra hoy las dos líneas sin `cron-avisa`: se escriben como están instaladas.

### La otra forma que descarté

Una línea nueva `reputacion avisa` a las 10:45 (la versión anterior). Duplica avisos con las líneas ya envueltas, cambia el crontab (prueba de instalación y espera a D-E por el mismo archivo) y repite el trabajo de la revisión de las 10:30. Tampoco sirve mover las dos líneas al bloque del repo: `cron_orbit.sh` solo reemplaza el bloque `# === Orbit (` (`extrae_bloque`, `tools/despliegue/cron_orbit.sh:73-75`), y las de reputación viven en `# === Orbit reputacion (`.

### Qué se borra

`carga_reputacion_digest`, el bloque de reputación del resumen del ciclo y la ventana fija de 24 h (`VENTANA_DIGEST_HORAS`, `reputacion_alertas.py:351`).

### Pruebas

- Falla que ya pasó (19 días sin lectura): `test_arreglos03_dk_lectura_que_no_corrio_se_avisa` en `tests/test_reputacion_alertas.py`, con `db_reputacion` (`tests/test_reputacion.py:402`, da la conexión y su DSN). Sin corrida de hoy, `lectura_de_hoy` da `"ausente"` y `ejecuta_alertas()` sale 1 con «no corrió»; con una corrida fallida da `"fallida"` y ese punto no hace salir 1; `ejecuta_snapshot("meli")` con una carpeta de llaves vacía sale 1 y deja una corrida fallida de hoy. Hoy falla porque `lectura_de_hoy` no existe.
- Datos de producción (la migración marca las 5 alertas que ya existen): `test_arreglos03_dk_migracion_avisada_y_su_reversa`, con la sonda en `True` tras la migración, `avisada_at = created_at`, el permiso de `app_decide` y la sonda en `False` tras la reversa.
- F7 (una alerta crítica nunca llega) no es uno de los cuatro casos de prueba nueva; su comprobación va en la prueba real.

### Prueba real

`migracion_aplicada.sh arreglos03_reputacion_avisada`, `desplegado.sh --no "def carga_reputacion_digest"` y `docs/evidencia/arreglos-03/D-K/prueba.py` por `en_prod.sh` (hoy sale 1 con `ImportError: cannot import name 'aviso_reputacion'`):

```python
import inspect

from app import notifica, reputacion_alertas
from app.notifica import aviso_reputacion

texto = aviso_reputacion([{"severidad": "critica", "mensaje": "reseña de 1 estrella en MLM1"},
                          {"severidad": "aviso", "mensaje": "el rating bajó a 4.1"}])
assert "Urgente: reseña de 1 estrella en MLM1" in texto and "Aviso: el rating bajó a 4.1" in texto, texto
assert not hasattr(reputacion_alertas, "carga_reputacion_digest"), "las alertas siguen colgadas del resumen del ciclo"
assert "reputacion" not in inspect.getsource(notifica.notifica_digest), "el resumen del ciclo sigue cargando reputación"
assert "avisa_pendientes" in inspect.getsource(reputacion_alertas.ejecuta_alertas), "la revisión de las 10:30 no manda las alertas"
print("D-K desplegado")
```

### Medido

```
SELECT source, ok, count(*), min(started_at)::date, max(started_at)::date FROM ingest_run
 WHERE source LIKE 'reputacion%' AND started_at >= '2026-09-20' AND started_at < '2026-10-20' GROUP BY 1,2
reputacion_meli|True|11|2026-09-20|2026-10-19   (ninguna corrida, ni fallida, del 28-sep al 16-oct)
SELECT id, tipo, severidad, resolved, created_at::date FROM reputation_alert ORDER BY id
1 rating_bajo aviso 09-08 | 2,3,4 resena_1 critica 09-08 | 5 resena_1 critica 10-17   (ninguna resuelta)
SELECT started_at::date, platform FROM optimizer_cycle WHERE started_at::date IN ('2026-09-08','2026-09-09','2026-10-17','2026-10-18')
  ciclos de MX y US los cuatro días
SELECT has_table_privilege('app_decide','ingest_run','SELECT')  -> True
```

### Cruces

D-K ya no edita `tools/despliegue/crontab-orbit.txt`: no espera a D-E ni toca la guarda de `cron_orbit.sh`. Comparte `app/notifica.py` con P-B (otra función) y con ARREGLOS 04 W-G, y `app/cli.py` con D-A, D-B y ARREGLOS 04: cambios por contenido.

---

## REP-B. Caída de nivel, preguntas fantasma y reclamos vacíos (F6, F8, F9). Bloque D-L

### F6. Caída de nivel de la cuenta

Hoy `evalua_salud_cuenta(seller[0][1], seller[1][1])` (`app/reputacion_alertas.py:271-275`) compara las dos últimas lecturas: si el job de alertas salta el día de la caída, o la lectura corrió dos veces, la caída no se ve, y la alerta se cierra sola al día siguiente.

Forma elegida: comparar contra el mejor nivel de los 7 días anteriores a hoy, igual que `caida_rating` de MeLi (`_ventana_previa`):

```python
def mejor_nivel_previo(seller: list[tuple[dt.date, str | None, int | None]], hoy: dt.date) -> str | None:
    # entre las filas con hoy - 7 <= fecha < hoy y level_id en MAPA_LEVEL, el de mayor MAPA_LEVEL
```

`evalua_y_persiste` usa `evalua_salud_cuenta(seller[0][1], mejor_nivel_previo(seller, hoy))` y marca la llave como evaluada cuando los dos existen. Con verde, amarillo, amarillo, abre la alerta; sigue abierta mientras haya un verde en la semana; si la cuenta se queda una semana en amarillo, se cierra sola.

Otra forma descartada: guardar el «último nivel evaluado» en una tabla propia. Pide estado nuevo para lo que la serie ya tiene.

### F8. Preguntas que ya no existen

Hoy la pantalla toma la última fila de cada pregunta (`app/api_reputacion.py:140-155`) aunque no haya aparecido en la lectura de hoy. Medido: 4 pendientes con la regla de hoy y 1 con la regla nueva.

Forma elegida: pendiente = última fila `UNANSWERED` **y** observada en la última lectura, `observed_at = (SELECT max(observed_at) FROM meli_question)`. Cada lectura escribe todas las preguntas con el mismo `observed_at` en una sola transacción (`app/reputacion.py` `_escribe_todo_meli`; llave única con `observed_at`, `migrations/0026`). La misma condición vale para la lista y para el total.

Otra forma descartada: tomar la hora de la última corrida ok de `ingest_run`. Su `started_at` no es el `observed_at` de las filas.

### F9. «0 reclamos» inventado

Hoy `_pagina` (`app/reputacion.py:427-441`) devuelve una lista vacía si falta la clave `data`, y `plan_seller` (`app/reputacion_plan.py:197`) guarda `len([]) = 0`.

Forma elegida:

- `_pagina` devuelve `(None, False)` cuando la primera página no trae la clave (una lista vacía sigue siendo lista vacía).
- `fetch_seller`: con `None`, `plan_seller(rep, None, fetched)` y el skip «meli: claims sin lista (conteo vacío, no cero)». `plan_seller` acepta `list | None`: con `None`, `disputas_total` y `disputas_abiertas` quedan vacíos. Las columnas ya aceptan NULL (`migrations/0024_reputacion.sql:118-130`) y `reclamos_suben` ya no dispara con un total vacío.
- `fetch_questions`: con `None`, `ReputacionError("MeLi /questions/search respondió sin la lista de preguntas")`. La lectura entera falla (una sola transacción, `_corre_meli`), sale 1 y la regla de F8 no se queda con una lectura vacía. El aviso de las 09:30 (`cron-avisa`) llega ese mismo día.

### Pruebas

Solo F8, que ya pasó en producción: la reproducción `test_pregunta_que_desaparece_de_meli_queda_pendiente_para_siempre` pasa a `tests/test_ui_reputacion.py` como `test_arreglos03_dl_pregunta_que_ya_no_esta_en_meli_no_cuenta`. F6 y F9 no son dinero ni están medidos en producción, pero cada una tiene una comprobación que sale en rojo sin el arreglo:

- F6: la `Acepta` de D-L.3 exige `mejor_nivel_previo(seller, hoy)` en `app/reputacion_alertas.py` y que ya no esté `seller[1][1]` (hoy `:271` y `:273` comparan las dos últimas lecturas). El guion de abajo comprueba la función.
- F9: la falla vive en `_pagina` y `fetch_seller` (`app/reputacion.py:427-443`), no solo en `plan_seller`. El guion arma el cliente falso de `docs/evidencia/arreglos-2026-11-12/pruebas/estimacion/test_reputacion_revision.py:104-112` (reclamos sin la lista `data`) y exige `disputas_total` vacío.

Prueba real: `docs/evidencia/arreglos-03/D-L/prueba.py` por `en_prod.sh` más `desplegado.sh "max(observed_at) FROM meli_question"`. Hoy fallan los dos (`ImportError: cannot import name 'mejor_nivel_previo'` y «falta en producción»):

```python
import datetime as dt

from app.reputacion import fetch_seller
from app.reputacion_alertas import evalua_salud_cuenta, mejor_nivel_previo
from app.reputacion_plan import plan_seller

hoy = dt.date(2026, 11, 12)
serie = [(hoy, "3_yellow", 24), (hoy - dt.timedelta(days=1), "3_yellow", 24), (hoy - dt.timedelta(days=2), "5_green", 24)]
previo = mejor_nivel_previo(serie, hoy)
assert previo == "5_green", f"nivel previo {previo}"
assert evalua_salud_cuenta("3_yellow", previo) is not None, "verde -> amarillo sin alerta"
plan = plan_seller({"level_id": "5_green"}, None, dt.datetime(2026, 11, 12, 9, 30, tzinfo=dt.UTC))
assert plan.disputas_total is None and plan.disputas_abiertas is None, "reclamos inventados"


class ClienteFalso:
    """Imita ClienteMeli.get: devuelve (dict, fetched_at) por ruta, sin red."""

    def __init__(self, respuestas):
        self._r = respuestas

    def get(self, path, params=None):
        return self._r[path], dt.datetime(2026, 11, 12, 9, 30, tzinfo=dt.UTC)


sin_lista = ClienteFalso({
    "/users/me": {"id": 1},
    "/users/1": {"seller_reputation": {"level_id": "5_green", "transactions": {}}},
    "/post-purchase/v1/claims/search": {"paging": {"total": 24}},
})
leido, _skips = fetch_seller(sin_lista)
assert leido.disputas_total is None, f"reclamos sin lista dan {leido.disputas_total}"
print("D-L desplegado")
```

### Medido

```
WITH u AS (SELECT DISTINCT ON (external_id, question_external_id) ... FROM meli_question ORDER BY ..., observed_at DESC)
SELECT count(*) FILTER (WHERE estado='UNANSWERED'),
       count(*) FILTER (WHERE estado='UNANSWERED' AND observed_at = (SELECT max(observed_at) FROM meli_question)) FROM u
4|1
SELECT metric_date, level_id, disputas_total FROM seller_reputation_snapshot ORDER BY metric_date DESC LIMIT 3
2026-11-12|5_green|24 (los tres días)
```

### Cruces

Ninguno fuera de este frente. Va después de D-K (los dos tocan `reputacion_alertas.py`) y de D-B (borra las entradas paralelas de `reputacion.py`).

---

## B-ING. Borrar el segundo camino de métricas (movimiento 4). Bloque D-A

Qué es: `ingest metrics` (`app/cli.py:192-193`, opción en `:557`) llama `reports.main` (`app/ads/reports.py:1963-2078`), que usa `sync_metrics` (`:1846`) y `placements.sync_placements` (`app/ads/placements.py:361-394`). Unas 260 líneas.

Demostrado que producción no lo usa:

- El crontab del repo no lo nombra (`tools/despliegue/crontab-orbit.txt`, solo `ingest diario`). El crontab del 21-oct lo traía (`crontab-antes.txt:34,40,41`) y ingesta-01 A.4 lo quitó. La fila D-A.1 vuelve a leer el crontab vivo antes de borrar y para si aparece.
- Runbooks: un solo uso, `docs/DEPLOY.md:348-350` (ejemplo de CLI). Se cambia por `ingest diario --fecha ... --fecha-fin ...`. Los demás son planes y specs viejos (`plans/bids-02*.md`, `plans/ingesta-01.md`, `docs/superpowers/specs/`), que son historia y no se tocan.
- Código: nadie más importa `sync_metrics`, `sync_placements` ni `reports.main` (`rg` en `app`, `tools`, `scripts`).
- Pruebas, por grupo (lista completa en la fila D-A.2):
  - Entran por `sync_metrics` para probar el escritor: `tests/test_reports_pipeline.py` (import de `:54` y 13 llamadas de `:1211-1863`), `tests/test_ads_descarga.py:572` y `:783`, `tests/test_ads_producto_ingesta.py:848,927,950,1044`. Pasan a llamar `descarga(conn, client, [grupo], (ini, fin), al_completar=reports.escribir_grupo, ...)`, que es lo que `sync_metrics` hacía por dentro y lo que corre el diario. Ese camino devuelve el error en `ResultadoGrupo` y no lo lanza: donde hoy hay `pytest.raises` alrededor de `sync_metrics` (`tests/test_reports_pipeline.py:1385,1463,1487,1528,1572`), la prueba afirma `res.ok is False` y el tipo de `res.exc` o el texto de `res.skip_reason`.
  - Entran por `sync_placements` para probar el escritor de placements: `tests/test_placements.py:293-389` y `:492-560`. Llaman `descarga` con el `GrupoDescarga` de placements y `al_completar=placements.escribir_grupo`.
  - Se borran las que prueban lo borrado: `test_rango_desde_args_casos` (`tests/test_reports_pipeline.py:904-926`) con el import de `:49`, las de `reports.main` (`tests/test_ads_salud.py:183,191`, `tests/test_cli.py:285`, `test_main_placements_*` en `tests/test_placements.py:397-490`).
  - `ResultadoSync` (`app/ads/reports.py:585`) solo lo devolvía `sync_metrics`: se borra si nadie más lo usa.

`ingest diario --fecha/--fecha-fin` ya cubre el relleno manual. La otra forma (dejarlo como «herramienta manual») se descarta por la regla 1: dos caminos para lo mismo.

Prueba real: `desplegado.sh --no "def sync_metrics" "def sync_placements"`. Hoy sale 1 («sigue en producción (1 archivos): def sync_metrics»).

## B-REP. Borrar las entradas de reputación que el cron no usa y Apify (E6). Bloque D-B

Qué se borra:

- `sync_meli` (`app/reputacion.py:262`), `sync_seller` (`:586`) y `sync_questions` (`:615`): solo las usan las pruebas; el cron corre `ejecuta_snapshot` → `_corre_meli` (`:649`, `:715`).
- Las re-exportaciones de `app/reputacion.py:32-46` que solo sirven a las pruebas.
- Apify y Junglee: `sync_amazon` (`:308`), `SOURCE_AMAZON` (`:82`), las ramas de Amazon de `ejecuta_snapshot` (`:660-697`) y `_ensayo` (`:774`), `APIFY_BASE`, `APIFY_TOKEN_FILENAME`, `ApifyCredentials` (`app/reputacion_clientes.py:25,31,87`) y `ClienteJunglee` (`:199`), más lo que solo ellas usen en `app/reputacion_plan.py`. En `app/cli.py:712-716`: `--fuente` queda con `choices=("meli",)` (la línea del cron pasa `--fuente meli` y sigue valiendo) y se borran `--max-productos` y `--tope-usd`, que solo usaba Apify.
- En `docs/DEPLOY.md`: la llave `apify_token.json` (`:2392-2393`), la fila de `reputacion:amazon` (`:2409`) y el pendiente de Apify (`:2416-2418`). El archivo `apify_token.json` del servidor no se borra: sin código que lo lea no hace daño, y borrar una llave no tiene reversa.

Demostrado que no se usa: `reputation_snapshot` tiene 3140 filas y todas son de MeLi (`SELECT platform, count(*) FROM reputation_snapshot GROUP BY 1` da `meli|3140`). La línea de Amazon del crontab está comentada desde el 8-sep (`crontab-antes.txt:47-48`). D-B.1 vuelve a comprobar en el crontab vivo que ninguna línea activa pasa `--fuente amazon`.

No se toca: las tablas (las filas de MeLi siguen; no hay filas de Amazon que perder) y la lectura de la pantalla. Mover `ClienteMeli` a `app/meli/cliente.py` y unir el cálculo del «previo» son cambios de estructura, no borrados: quedan fuera de este plan.

Las pruebas de reputación que entraban por `sync_meli`, `sync_seller` o `sync_questions` se reescriben contra `fetch_meli`, `fetch_seller`, `fetch_questions` y `_corre_meli`.

Prueba real: `desplegado.sh --no "class ClienteJunglee" "def sync_questions" "def sync_seller" "def sync_meli("`. Hoy sale 1.

Cruce: el hallazgo de `web.md` sobre `reputacion snapshot --tope-usd` en float (`app/cli.py:716`) se cierra con este borrado.

## B-JEV. Borrar el experimento de JEV y la revisión por decisión (E5). Bloque D-C

Qué se borra:

- `tools/jev_orden_opciones.py` (533 líneas) y `tests/test_jev_orden_opciones.py` (303). Experimento cerrado el 13-oct; no está en cron; nadie lo importa (`rg -l jev_orden_opciones` solo da esos dos archivos y el grafo). Además paga con `Libro.pagar` sin respetar el tope.
- La revisión por decisión: `DecisionARevisar` (`app/jev_ads.py:341`) y la unión `Sujeto`, que queda en `SemillasARevisar`; `decision_a_revisar` (`app/jev_catalogo.py:358`); en `app/jev_asesor.py`, las ramas `isinstance(sujeto, DecisionARevisar)` (`:104`, `:119`, `:235`, `:250`, `:295-298`), `_destino_cambio` (`:427-437`) y la lectura por `decision_id` de `leer` (`:564-582`, queda solo `ReferenciaPlan`); el bloque de asesoría de `/cortes` (`app/api_dashboard.py:1478-1495`) y sus claves `asesoria` y `asesoria_disponible` (también `app/ui.py:674`); las filas de asesoría de `app/templates/cortes.html:14-15` y `:59-66`; la rama `evaluar --decision-id` de `tools/jev_ads.py` (`:156` y su docstring).

Demostrado que no se usa: `SELECT sujeto_tipo, count(*), max(created_at)::date FROM jev_revision GROUP BY 1` da `decision|3|2026-10-04`, `lote|40|2026-11-12`, `semillas|22|2026-10-13`. Tres usos en toda su historia; el último hace más de un mes. Las revisiones de semillas y los lotes siguen.

No se toca: las 3 filas viejas de `jev_revision` (solo-agregar; el CHECK de `sujeto_tipo` sigue aceptando `decision`). Dejan de verse en `/cortes`: es el riesgo que nombra el reporte. Meter `jev_avisos` en `jev_senales` y unir las reglas de bytes y JSON canónico son estructura, fuera de este plan.

La pantalla `/cortes` cambia: se actualiza `.cursor/skills/verify-orbit/features/cortes.md` y se comprueba con `orbit-verify` (regla de `CLAUDE.md`).

Prueba real: `desplegado.sh --no "class DecisionARevisar" "def decision_a_revisar"`. Hoy sale 1.

## B-EST. Estimación solo cuando sirve (E4). Bloque D-D (no entra al plan)

**Nota del lead (2026-11-13): esta sección no entra a `plans/arreglos-03.md`.** Quitar las dos corridas inútiles exige cambiar el crontab de root, que tiene seis líneas (entre ellas el respaldo diario de Orbit de las 03:30), sin un instalador probado. El ahorro (dos corridas que no llegan a precios) no paga ese riesgo. Lo de abajo queda como registro de lo medido.


Qué pasa hoy: en 7 días, las corridas de 00:45 y 06:45 escribieron 6244 escenarios y 0 disponibles; el costo se valida a las 08:15.

```
SELECT extract(hour from observed_at AT TIME ZONE 'UTC')::int, count(*), count(*) FILTER (WHERE estado='disponible')
  FROM estimacion_escenario WHERE observed_at > now()-interval '7 days' GROUP BY 1 ORDER BY 1
0|3535|0   6|2709|0   12|3073|2611   18|3019|2696
```

Forma elegida: el cron de root pasa de `45 */6` a `45 12,18`, con `--cada 24` como las demás tareas diarias (`--cada 6` supone una corrida cada 6 h y el hueco nuevo es de 18 h). El precio corre de 13:10 a 23:10 y usa la de 12:45; la de 18:45 refresca para los repasos de la tarde.

Por qué es un bloque gate y no la receta: ese cron vive en el crontab de root, no en el de `gon`, y `cron_orbit.sh` solo administra el de `gon`. El cambio es una edición única del crontab de root con respaldo, más los textos del repo (`docs/DEPLOY.md:452,467`, `tools/refresh_estimacion.sh:10-11`).

Otra forma descartada: correr la validación de costos antes de las 00:45 para que las cuatro corridas sirvan. Cambia contabilidad y costos para ganar nada en precios.

Demostrado que no se usa: ningún lector toma un escenario de otro día (`estimacion_reader.py:123-135`), así que lo de las 00:45 y 06:45 nunca llega a precios ni a pantallas con número.

Prueba real: el crontab de root trae `45 12,18` en la línea de `refresh_estimacion.sh`. No la corrí: el permiso de esta sesión no deja leer el crontab.

---

## Lo que no resolví

- **El crontab vivo.** En la primera versión no se pudo leer; el 2026-11-13 se leyó el de `gon` (solo lectura): el bloque `# === Orbit (` es igual byte a byte a `tools/despliegue/crontab-orbit.txt` y las dos líneas de reputación ya van con `cron-avisa` (por eso se rehízo REP-A). Las filas D-A.1, D-B.1 y D-K.1 vuelven a leerlo antes de cambiar nada y paran si no coincide.
- **Qué hace exactamente `--cada` de `cron-avisa`.** Vive solo en el servidor. Ya no hace falta: D-D salió del plan y D-K no agrega líneas.
- **El día de la tasa con nearest_prior.** Para ventas con `fx_source = nearest_prior` no se sabe qué día de tasa usó contabilidad; `fx_fecha` queda vacío. Saberlo exige que contabilidad guarde la tasa en el payload: es otro repo.
- **Ventas de US anteriores a D-I.** Quedan sin importe en dólares; conciliar el pasado se hace contra los pedidos de SP-API.
