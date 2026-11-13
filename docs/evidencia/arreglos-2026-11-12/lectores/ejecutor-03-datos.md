# Lector ejecutor: ARREGLOS 03, cabecera, BASE y bloques D (D-A a D-L)

Fecha: 2026-11-13 (UTC). Copia: `la copia de trabajo del plan`, código = master `a4644aad`.
Método: corrí cada `Acepta` de los bloques D tal como está escrito (`uv run --frozen --python 3.12`), las lecturas de producción (`lee_prod.sh`, `desplegado.sh`, los dos crontab) y apliqué los cambios del plan en una copia aparte (fuera del repo) para ver qué rompe. No toqué el repo salvo este archivo.

## Hallazgos, de más grave a menos grave

### 1. BLOQUEANTE — D-E.5: con el horario nuevo fallan dos pruebas que el plan no nombra, y el diseño dice que una de ellas «sigue pasando»

**Dónde:** plan, fila D-E.5 (lista de pruebas por actualizar) y diseño, «DC», «Cruces», primer punto («La prueba de S (`tests/test_metricas_listas.py:106`) sigue pasando»).

**Evidencia:** en una copia apliqué solo lo que pide D-E.5: `HORARIOS_INGESTA = ("08:10", ...)`, las horas `10 8` y `9,10,13,16` en `crontab-orbit.txt`, las líneas que nombra (`tests/test_ingest_diario.py:77,82-88,238`, `tests/test_avisos_campana_cron.py:25`, `tests/test_impulso_vigia_cron.py:25`) y la tabla de `docs/DEPLOY.md`. Después corrí las pruebas de la `Acepta`:

```
$ PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_ingesta_cron.py tests/test_avisos_campana_cron.py tests/test_impulso_vigia_cron.py tests/test_ingest_diario.py tests/test_metricas_listas.py
E   AssertionError: cycle amazon_us: horas [(9, 40), (10, 40), (13, 40), (16, 40)]      tests/test_metricas_listas.py:128
E   AssertionError: assert '... Mañana a las 07:10 se intenta otra vez.' in ['... Mañana a las 08:10 ...']   tests/test_ingest_diario.py:639
FAILED tests/test_ingest_diario.py::test_agotado_en_el_ultimo_horario_mensaje_final
FAILED tests/test_metricas_listas.py::test_arreglos01_s_cada_consumidor_tiene_horario_tras_el_ultimo_plazo_de_ingesta
2 failed, 49 passed
```

`tests/test_metricas_listas.py:128` exige `>= {8, 10, 13, 16}`. Con 9 en lugar de 8 falla siempre. El plan le dice al ejecutor que pare el bloque si el código contradice el diseño, así que D-E se detiene, y con él D-F, D-G, D-K y D-L, que dependen de D-E.

**Corrección pegable** (fila D-E.5, reemplaza la frase de pruebas):

> Se actualizan las pruebas que fijan el horario: `tests/test_ingest_diario.py:77,82-88,238,641,645` (todas las «07:10» pasan a «08:10»), `tests/test_avisos_campana_cron.py:25`, `tests/test_impulso_vigia_cron.py:25` y `tests/test_metricas_listas.py:128` (la prueba S de ARREGLOS 01: `{8, 10, 13, 16}` pasa a `{9, 10, 13, 16}`; la regla que protege, «cada consumidor corre después de las 16:00», no cambia).

Y en el diseño, «DC», «Cruces», primer punto: cambiar «La prueba de S (`tests/test_metricas_listas.py:106`) sigue pasando: cada consumidor conserva su hora de las 16.» por «La prueba de S (`tests/test_metricas_listas.py:106-130`) fija las horas `{8, 10, 13, 16}` en la línea 128: D-E la cambia a `{9, 10, 13, 16}`. Su regla (un horario después de las 16:00) se conserva.»

### 2. BLOQUEANTE — D-A.2: `tests/test_reports_pipeline.py` entra por `sync_metrics` y `_rango_desde_args`, y el plan no dice qué hacer con ese archivo

**Dónde:** plan, fila D-A.2, y diseño, «B-ING», punto «Pruebas».

**Evidencia:** las referencias que hay hoy:

```
$ rg -c "sync_metrics\(" tests/test_reports_pipeline.py      -> 13 llamadas (líneas 1211 a 1863), más el import de la línea 54
$ rg -n "_rango_desde_args" tests                            -> tests/test_reports_pipeline.py:49 (import) y :904-926 (test_rango_desde_args_casos)
```

En una copia borré de `sync_metrics` hasta el final de `app/ads/reports.py`, como pide D-A.2:

```
$ PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q --co tests/test_reports_pipeline.py
E   ImportError: cannot import name '_rango_desde_args' from 'app.ads.reports'
no tests collected, 1 error
```

La `Acepta` de D-A.2 incluye ese archivo, así que falla. Además: (a) `sync_metrics` lanza una excepción cuando falla y `descarga` devuelve el fallo en `ResultadoGrupo` sin lanzarla, así que las llamadas envueltas en `pytest.raises` (líneas 1386, 1464, 1488, 1529 y 1573) no se arreglan cambiando solo la llamada; (b) `tests/test_ads_descarga.py:783` `test_sync_metrics_sellado_ok_y_unidades` y `tests/test_placements.py:415-490` (`test_main_placements_*`, prueban `reports.main`) tampoco están en la lista; (c) `ResultadoSync` (`app/ads/reports.py:585`) queda sin usar.

**Corrección pegable** (fila D-A.2, reemplaza la frase «Pruebas: ...»):

> Pruebas: las que entraban por `sync_metrics` (`tests/test_reports_pipeline.py:54` y sus 13 llamadas de las líneas 1211-1863, `tests/test_ads_descarga.py:572` y `:783`, `tests/test_ads_producto_ingesta.py:848,927,950,1044`) llaman `descarga(conn, client, [grupo], (ini, fin), al_completar=reports.escribir_grupo, reloj=..., sleep=...)` con el mismo `GrupoDescarga` que armaba `sync_metrics`. Donde la prueba esperaba una excepción (`pytest.raises`, `tests/test_reports_pipeline.py:1386,1464,1488,1529,1573`), afirma `res.ok is False` y `res.exc` o `res.motivo`. Se borran `test_rango_desde_args_casos` (`tests/test_reports_pipeline.py:904-926`) y el import de `_rango_desde_args`; las de `reports.main` (`tests/test_ads_salud.py:183,191`, `tests/test_cli.py:285`, `tests/test_placements.py:415-490`) y las de `sync_placements` en `tests/test_placements.py`. También se borra `ResultadoSync` (`app/ads/reports.py:585`), que solo devolvía `sync_metrics`.

### 3. BLOQUEANTE (regla de revisión) — D-D cambia el crontab de root sin prueba, sin comando y con una comprobación que no puede fallar

**Dónde:** plan, bloque D-D, filas D-D.2 y su `Acepta`.

**Evidencia:**

```
$ sed -n 72p plans/arreglos-03.md
pruebas: instala (cambia una línea del crontab de root)
$ rg -n "arreglos03_dd|cambia_cron_root" plans/arreglos-03.md tests
plans/arreglos-03.md:78  (solo el texto de D-D.2; ninguna fila pide prueba)
$ ssh goncloud 'crontab -l' | wc -l
6      <- respaldo de Orbit 03:30, chmod de tokens de MeLi, equivalencias de MeLi, estimaciones, pase de MeLi, Nextcloud
```

- El bloque se declara del caso «instalar en las máquinas» y no tiene fila de prueba. `CLAUDE.md` dice que un revisor bloquea por «un caso de los 4 sin su prueba».
- D-D.2 dice «escribir ... y correrlo», pero no dice dónde corre (Mac con `ssh`, o servidor con `bash -s`) ni da el comando.
- Solo comprueba después de escribir («vuelve a leer y sale 1»). Un archivo cortado instalado con `crontab` borra el respaldo diario de Orbit antes de que esa comprobación lo vea.
- La `Acepta` (`test -s .../despues.txt`) sale 0 aunque el script haya fallado, porque el archivo lleva la salida igual.

**Corrección pegable** (reemplaza las filas D-D.2 y D-D.3):

> | D-D.2 | `tools/despliegue/cron_estimacion.sh cambia\|revisa\|restaura <respaldo>`, con el molde de `tools/despliegue/cron_jev_vigia.sh` (`ORBIT_CRON_USUARIO=root`, `ORBIT_CRON_ARCHIVO` por omisión `/mnt/data/appdata/orbit/archive`). `cambia` lee el crontab, arma el nuevo en un temporal cambiando solo en la línea de `refresh_estimacion.sh` `45 */6` por `45 12,18` y `--cada 6` por `--cada 24`, y **antes de escribir** comprueba con `diff` que difiere exactamente una línea y que el número de líneas es igual; si no, sale 1 sin escribir. Respalda en `$ORBIT_CRON_ARCHIVO/crontab-root.<UTC>.arreglos03`, instala e imprime `respaldo=<ruta>`. Idempotente («sin cambios»). Prueba (instalar y deshacer en las máquinas), en `tests/test_despliegue_motor.py`, con un `crontab` falso en el PATH como `test_arreglos01_n_el_instalador_no_quita_cron_avisa`: `test_arreglos03_dd_cambia_solo_la_linea_de_estimacion` (cambia una línea, las otras cinco quedan iguales, `restaura` deja el original byte a byte). | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py -k arreglos03_dd_ |
> | D-D.3 | Correrlo en el servidor: `git show HEAD:tools/despliegue/cron_estimacion.sh \| ssh goncloud 'bash -s -- cambia' \| tee docs/evidencia/arreglos-03/D-D/despues.txt`. Deshacer: `git show HEAD:tools/despliegue/cron_estimacion.sh \| ssh goncloud 'bash -s -- restaura <ruta de respaldo=>'`. Textos: `docs/DEPLOY.md:452` dice «a las 12:45 y 18:45 UTC» y `:467` trae la línea nueva; `tools/refresh_estimacion.sh:10-11` igual. | test "$(ssh goncloud 'crontab -l' \| grep -F /mnt/data/appdata/orbit/refresh_estimacion.sh \| grep -v '^#' \| awk '{print $1, $2}')" = "45 12,18" && grep -q '^45 12,18 ' docs/DEPLOY.md |

Y en la cabecera del bloque: `pruebas: instala (cambia una línea del crontab de root; prueba en D-D.2)`.

### 4. D-A.1, D-B.1 y D-C.1: las búsquedas con `rg` no encuentran nada porque usan `\|`

**Dónde:** plan, filas D-A.1, D-B.1 y D-C.1.

**Evidencia:** en `rg`, `\|` busca una barra literal. Corrí los tres comandos tal como están:

```
$ rg -n "ingest metrics\|sync_metrics\|sync_placements\|reports\.main" app tools scripts docs --glob '!docs/evidencia/**' --glob '!docs/superpowers/**'   -> sin salida, rc=1
$ rg -n "sync_meli\|sync_seller\|...\|fuente amazon" app tools docs --glob '!docs/evidencia/**'                                                         -> sin salida, rc=1
$ rg -ln "jev_orden_opciones\|DecisionARevisar\|decision_a_revisar\|asesoria" app tools tests .cursor                                                  -> sin salida, rc=1
```

Con `|` los tres encuentran 16, 26 y 17 sitios. El archivo `usos-antes.txt` quedaría sin la lista de usos y su `test -s` pasa igual, porque lleva el conteo del crontab o el SQL. Además `grep -c` sale 1 cuando cuenta 0: dentro de un script con `set -e` corta la escritura de la evidencia.

**Corrección pegable:** en las tres filas, cambiar cada `\|` dentro de las comillas de `rg` por `|`, y el conteo de D-A.1 por:
`ssh goncloud 'crontab -u gon -l; crontab -l' | grep -v '^#' | grep -c 'ingest metrics' || true` (y en D-B.1 lo mismo con `-- '--fuente amazon'`).

### 5. D-K parte de un dato viejo: las líneas de reputación ya llevan `cron-avisa`

**Dónde:** diseño, «OK», tabla (fila «Reputación ... sin envoltura ... No. Se resuelve en D-K»), y «REP-A», «Qué pasa hoy» y «La otra forma que descarté» («su línea no está envuelta»).

**Evidencia** (crontab vivo, leído hoy):

```
$ ssh goncloud 'crontab -u gon -l' | grep -n 'reputacion'
34:30 9 * * * /usr/local/bin/cron-avisa --nombre "Reputación de MercadoLibre" --cada 24 -- /bin/bash -c 'docker exec orbit-app-1 python -m app.cli reputacion snapshot --fuente meli' >> ...
36:30 10 * * * /usr/local/bin/cron-avisa --nombre "Alertas de reputación" --cada 24 -- ... reputacion alertas' >> ...
```

El diseño tomó el crontab del 2026-10-21 (`crontab-antes.txt`). Hoy, una lectura que falla con salida 1 ya avisa a las 09:30. Con D-K, esa misma falla manda dos Telegram con nombres casi iguales: «Reputación de MercadoLibre» (09:30) y «Reputación de Mercado Libre» (10:45). D-K sigue sirviendo para sacar las alertas del resumen del ciclo y para el caso «no corrió», pero lo que justifica el bloque está mal. `docs/DEPLOY.md:2407-2408` también muestra las líneas sin envoltura.

**Corrección pegable:** en el diseño, fila «Reputación» de la tabla de «OK»: «`30 9 * * * /usr/local/bin/cron-avisa --nombre "Reputación de MercadoLibre" --cada 24 -- ...` (crontab vivo del 2026-11-13) | Sí, si el comando sale distinto de 0. No avisa si la tarea no arranca. D-K agrega la revisión de las 10:45 y saca las alertas del resumen del ciclo.» En D-K.5 y en «REP-A», el nombre del aviso nuevo pasa a `--nombre "Avisos de reputación de Mercado Libre"`, para que no se confunda con el de las 09:30. En D-K.5 se agrega: «`docs/DEPLOY.md:2407-2408` muestra las dos líneas con su `cron-avisa`, como están instaladas.»

### 6. D-E.2: su `Acepta` no comprueba el módulo nuevo, y el seudocódigo no da el `ValueError` que pide el plan

**Dónde:** plan, fila D-E.2, y diseño, «DC», «Forma elegida».

**Evidencia:** `tests/test_ingest_diario.py` no importa `app.dia_cerrado` (`grep -c dia_cerrado tests/test_ingest_diario.py` da 0), así que la `Acepta` pasa aunque el módulo no exista. El seudocódigo usa `instante.astimezone(...)`, que con una fecha sin zona no lanza nada: usa la zona de la máquina.

```
$ uv run --frozen --python 3.12 python -c "import datetime as dt; from zoneinfo import ZoneInfo; print(dt.datetime(2026,11,12,7,10).astimezone(ZoneInfo('America/Los_Angeles')))"
2026-11-12 06:10:00-08:00          <- sin error, y con la hora corrida por la zona de la Mac
$ ... {'amazon_us':1}['shopify']   -> KeyError, no ValueError
```

**Corrección pegable** (`Acepta` de D-E.2):

`PYTHONPATH=. uv run --frozen --python 3.12 python -c "import datetime as dt; from app.dia_cerrado import ultimo_dia_cerrado as u, fin_del_dia as f; i=dt.datetime(2026,11,12,7,10,tzinfo=dt.UTC); assert u('amazon_us',i)==dt.date(2026,11,10) and u('amazon_mx',i)==dt.date(2026,11,11) and u('meli',i)==dt.date(2026,11,11); assert f('amazon_us',dt.date(2026,11,11))==dt.datetime(2026,11,12,8,tzinfo=dt.UTC); import pytest; pytest.raises(ValueError,u,'amazon_us',dt.datetime(2026,11,12,7,10)); pytest.raises(ValueError,u,'shopify',i)" && PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_ingest_diario.py`

Y en el seudocódigo del diseño, primera línea de `dia_local`: `if instante.tzinfo is None or plataforma not in ZONAS: raise ValueError(...)`.

### 7. D-E.4: las pruebas que siembran «ayer» con el reloj real pasan entre las 08:00 y las 24:00 UTC aunque no se corrijan

**Dónde:** plan, fila D-E.4 («Las pruebas con base que siembran D-1 con el reloj real ...»).

**Evidencia:** `tests/test_jev_senales.py:951-969` (`_metricas_de_ayer`) siembra `(now() AT TIME ZONE 'UTC')::date - 1` y llama a `jev_senales.main` sin reloj fijo. Con la guarda nueva falla solo entre las 00:00 y las 08:00 UTC (invierno), cuando el último día cerrado de US es D-2. Si el ejecutor corre la `Acepta` después de las 08:00 UTC sale verde sin haberla tocado, y luego falla en CI entre las 18:00 y las 02:00 de México. La fila no nombra los archivos.

**Corrección pegable** (al final de D-E.4): «Sitios conocidos: `tests/test_jev_senales.py:951-969` (`_metricas_de_ayer`) y el `HOY`/`AYER` de `tests/test_metricas_listas.py:43-44`; siembran `ultimo_dia_cerrado(plataforma, dt.datetime.now(dt.UTC))` por plataforma. Comprobación: `rg -n "::date - 1" tests/test_jev_senales.py` no da nada.»

### 8. BASE.1: el parche que cita quita `arreglos02` si se pega tal cual

**Dónde:** plan, fila BASE.1 (remite a `docs/evidencia/arreglos-03/DISENO-precios.md`, «Cruces con otros planes»).

**Evidencia:** la línea `+` del parche es `_(auto03|orbit07|claridad01|arreglos01|temporada01|arreglos03)_`, sin `arreglos02`. Para cuando arranca este plan, ARREGLOS 02 A.2 ya habrá puesto `arreglos02` (`plans/arreglos-02.md:32`). El texto de BASE.1 sí dice «conserva las que ya tenga», pero el parche lo contradice.

**Corrección pegable** (en el parche del diseño de precios):
```diff
-    | grep -E '_(auto03|orbit07|claridad01|arreglos01|arreglos02)_' | grep -v '_reversa_' | LC_ALL=C sort); do
+    | grep -E '_(auto03|orbit07|claridad01|arreglos01|arreglos02|temporada01|arreglos03)_' | grep -v '_reversa_' | LC_ALL=C sort); do
```
(sin `temporada01` si TEMPORADA 01 todavía no entró).

### 9. D-D.1: «leer la ayuda o el encabezado de `cron-avisa`» no trae comando

**Dónde:** plan, fila D-D.1. `cron-avisa` no está en el repo (`rg -l "cron-avisa" tools` solo da archivos que lo llaman).

**Corrección pegable:** «... Leer `ssh goncloud 'sed -n 1,80p /usr/local/bin/cron-avisa'` y anotar en ese archivo qué hace `--cada` ...».

### 10. D-F y D-I: su `prueba-real` sale en rojo justo después del despliegue

**Dónde:** plan, `prueba-real` de D-F y de D-I.

**Evidencia:** las dos salen `False` hoy (corridas). D-F mira la última copia de MeLi Ads con filas, que corre a las 08:15 UTC (`refresh_costos.sh`). D-I necesita una venta nueva de US, que llega con el libro a las 08:15 UTC (`tools/refresh_costos.sh:73`). Si se despliega a otra hora, las dos quedan en rojo hasta las 08:15 siguientes. El diseño lo dice para D-F; el plan no lo dice para ninguna de las dos.

**Corrección pegable** (al final de cada `aviso`, o como nota bajo la `prueba-real`): «La prueba real da verdadero desde la primera corrida de las 08:15 UTC posterior al despliegue (D-I: la primera que traiga una venta de Estados Unidos). Antes de esa hora, el rojo no es una falla del bloque.»

### 11. Detalles de citas y textos (se corrigen en la misma ronda)

- D-A.2: `_rango_desde_args` está en `app/ads/reports.py:1918` e `_imprimir_resumen` en `:1941`. El rango `:1963-2078` es solo `main`. Texto: «`_rango_desde_args` (`:1918`), `_imprimir_resumen` (`:1941`) y `main` (`:1963-2078`)».
- D-B.2: además de `app/cli.py:712-716`, el despacho pasa `max_productos=args.max_productos, tope_usd=args.tope_usd` en `app/cli.py:176-177`, y hay que quitarlo con ellos.
- D-C.3: las filas de asesoría de `app/templates/cortes.html` van de `:59` a `:73` (el `{% endif %}` está en la 73), no `:59-66`. Las claves `asesoria` y `asesoria_disponible` de `app/api_dashboard.py` están en `:1557` y `:1578`.
- D-J.4: `tools/despliegue/crontab-orbit.txt:18` es el comentario `job_key`; la línea de `jev-senales` es la 19.
- D-E.4: el rango por omisión está en `app/ads/diario.py:318-320`.
- D-E: quedan textos con la hora vieja que el plan no nombra: `docs/DEPLOY.md:399`, `:972`, `:1141`; `app/ads/diario.py:14`; `tools/refresh_costos.sh:31`. Agregar a D-E.5: «y las menciones sueltas de 07:10/08:05/08:40 en `docs/DEPLOY.md:399,972,1141`, `app/ads/diario.py:14` y `tools/refresh_costos.sh:31`».
- Cruce con ARREGLOS 01: la `Acepta` de S.4 (`grep -c '8,10,13,16' tools/despliegue/crontab-orbit.txt | grep -qx 4`) da rojo después de D-E. Anotarlo en «Cruces» del diseño para que nadie la vuelva a correr como regresión.
- D-L: su `prueba-real` busca el texto literal `max(observed_at) FROM meli_question` en el código desplegado. En D-L.2 conviene decir «con ese texto exacto, en minúsculas y en una línea».

## Verificado y correcto (lo corrí)

- Las diez `Acepta` con `-k arreglos03_<bloque>_` (BASE, D-E, D-F, D-G, D-H, D-I, dos de D-J, D-K, D-L) salen con código 5 («N deselected»): ninguna prueba nueva existe todavía.
- Las `Acepta` de archivo completo de D-A a D-L salen en verde hoy (176, 168, 135, 505, 85, 130, 97, 117, 118, 49, 58, 18, 132, 54 y 97 pasadas), con Postgres local y sin pruebas saltadas.
- Existen hoy los 36 archivos de prueba que nombran los bloques D.
- Las reproducciones citadas fallan hoy por la falla que describen: `test_rev_ingesta.py` (4: US pide 2026-11-11 a las 23:10 de LA; MeLi acepta el día en curso; dos de términos), `test_estimacion_ok_falso.py` (ok=True con 2 de 2 en error), `test_jev_ok_falso.py` (sin llave: salida=0, sin aviso), `test_reputacion_revision.py` (4).
- Los `prueba.py` del diseño (D-E, D-G, D-L), corridos con el código de hoy: fallan con `ModuleNotFoundError app.dia_cerrado`, `TypeError ... 'ultimo_cerrado'` e `ImportError mejor_nivel_previo`. El de D-G, con solo el cambio de nombre de D-E aplicado, falla en `AssertionError: fila ilegible no invalida la clave`: sí comprueba D-G.
- `desplegado.sh` de D-A, D-B, D-C, D-J y D-L sale 1 hoy con el texto esperado.
- El crontab instalado de `gon` tiene el bloque `# === Orbit (` igual byte a byte a `tools/despliegue/crontab-orbit.txt` (`diff` sin diferencias), y coincide con la columna «Hoy» del diseño.
- La línea de estimación del crontab de root es idéntica a `docs/DEPLOY.md:467` y a `tools/refresh_estimacion.sh:10`. La `prueba-real` de D-D sale 1 hoy, y las mitades de crontab de D-E y D-K también. Con la línea cambiada a `10 8` en una copia del crontab, la de D-E sale 0: la comprobación puede pasar.
- Recorrido de horas con `zoneinfo` (12-nov-2026, 15-jul-2027, 1-nov-2026 y 8-mar-2027) a las 08:10, 09:05, 09:20, 09:30, 09:40, 10:40, 13:15, 13:40, 16:40 y 21:30 UTC. Amazon México, Amazon Estados Unidos y Mercado Libre dan como último día cerrado el D-1 UTC, que es el mismo día que pide hoy la guarda. No hay ninguna hora del horario nuevo en que la guarda pida otro día ni en que el ciclo se quede sin correr por la regla. Solo a las 07:10 de invierno US daría D-2, y a esa hora ya no corre nada.
- Duración de la corrida de la mañana con el código actual (desde el 22-oct), a la hora local de US que tendrá la 08:10: a las 00:10 de LA (verano), p90 de 15 min y máximo de 17 min, contra p90 de 17 min y máximo de 21 min a las 23:10 de LA. La holgura de 55 min hasta las 09:05 alcanza. Los 48 min y las 6 corridas fallidas del diseño son del 14 al 21 de octubre, con el código anterior.
- En octubre (horario de verano, misma hora local que tendrá la 08:10), la primera lectura del «ayer» de US difiere de la final entre −5% y +4%. En noviembre (23:10 de LA) queda hasta 12% abajo (7-nov: 55.60 contra 62.33). El cambio deja el invierno como el verano.
- Lecturas de producción que el plan da como «hoy»: 0 líneas activas con `ingest metrics` y con `--fuente amazon`; `reputation_snapshot` = `meli|3140`; `jev_revision` = `decision|3|2026-10-04`; 60 devoluciones de MeLi sin etiqueta (ninguna de otra plataforma); `v_target_margen_plataforma` de MeLi con margen vacío y `fees_sin_tipo` 11; estimación por hora 0/0/2611/2696 disponibles.
- El trigger se llama `ledger_solo_reatribuye` (BEFORE DELETE OR UPDATE); `ledger_event` y `reputation_alert` son del usuario `orbit`, que es superusuario; `reputation_alert` no tiene triggers.
- Las sondas de D-H, D-I y D-K dan `False` hoy con `orbit_read`, el mismo rol que usa `q` en `comun.sh`, y ese rol ve las dos tablas en `information_schema`. Las tres migraciones del diseño traen la sonda en la línea 1 y no traen BEGIN ni COMMIT.
- Agregar la línea de D-K al bloque de cron no rompe `test_ingesta_cron`, `test_jev_senales_cron` ni las de avisos e impulso (11 pasadas en la copia).
- Quitar `metrics` de `ingest` solo rompe `tests/test_cli.py:285`, que el plan ya manda borrar.
- No hay usos de lo que borran D-A a D-C en `.github/`, `verify/`, `scripts/`, `docs/runbooks/` ni `tools/despliegue/`, salvo los que el plan ya nombra.
- TEMPORADA 01 H.3 ya está a las 10:05 UTC (`plans/temporada-01.md:149`).
