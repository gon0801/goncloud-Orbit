# D-F: rojo antes del cambio y verde después

Base: `origin/master` `1f15c855e0d05c56c1abc710748b963db3f7da24` al empezar (D-E integrado:
`app/dia_cerrado.py` con `ZONAS`, `dia_local` y `fin_del_dia`). Rebasado después sobre
`f19942f` (D-N y P-B, que no tocan estos archivos). Verde de cada `Acepta`: `acepta.txt`.

## D-F.1 — `test_arreglos03_df_meli_descarta_el_dia_local_en_curso`

En `tests/test_meli_ads_ingest.py`, con el DDL real del origen (el mismo de la reproducción
`test_meli_acepta_el_dia_en_curso`): filas del 2026-11-11 y del 2026-11-12, `updated_at`
2026-11-13T03:00Z, `leer_origen(ruta, ahora=2026-11-13T03:17Z)` (21:17 del 12-nov en México).
Espera solo el 11-nov, `sin_cerrar == 1` y cero rechazos.

Rojo en `1f15c855`, con solo la prueba agregada:

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_meli_ads_ingest.py -k arreglos03_df_
E       TypeError: leer_origen() got an unexpected keyword argument 'ahora'
tests\test_meli_ads_ingest.py:306: TypeError
FAILED tests/test_meli_ads_ingest.py::test_arreglos03_df_meli_descarta_el_dia_local_en_curso
1 failed, 79 deselected
```

Es el fallo que anticipa el diseño («Hoy falla porque `leer_origen` no acepta `ahora`»).

Discrimina. Con el arreglo puesto, dos mutaciones temporales de `app/meli/ads_source.py` la
ponen en rojo (el archivo se restauró después de cada una):

- `hoy_local` con la fecha UTC de `ahora` (la regla vieja con reloj inyectable):
  `Left contains one more item: datetime.date(2026, 11, 12)`. El 12-nov entra como dato.
- Rechazar en vez de saltar (`metric_date >= hoy_local` en `_fila`):
  `AssertionError: assert 0 == 1` sobre `sin_cerrar`; el 12-nov queda como rechazo y
  abortaría el snapshot.

## D-F.2 — la ingesta cuenta el día en curso como saltado

`ads_ingest` no tiene prueba nueva (el plan nombra solo la de D-F.1). Se comprobó con una
prueba desechable fuera del repo que reutiliza el fixture `pg` contra el PG de pruebas
(127.0.0.1:55433), con el reloj real y `HOY` = día de México:

```
normal:    rows_written=1 rows_skipped=1
           skip_reason='as_of=1794993916; snap=63c5…363e; 1x día local en curso descartado'
           (solo D1 en ads_metric_observation)
reintento: rows_written=0 rows_skipped=2
           skip_reason='reintento=63c5…363e; 1x día local en curso descartado,
                        1x misma version canonica (recopia o reintento)'
vacío:     un snapshot que solo trae el día en curso
           rows_written=0 rows_skipped=1
           skip_reason='snapshot vacio (no-op); 1x día local en curso descartado'
2 passed
```

Las marcas `as_of=`, `snap=` (y `futuro=`/`piso=` en su rama) siguen al principio: sus
expresiones regulares (`_MARCA_RE`, `_HUELLA_RE`…) las encuentran igual. El motivo va al final.

## Pruebas existentes que dependían de la hora

`tests/test_meli_ads_ingest.py` sembraba casi todo con `D1 = fecha UTC - 1`. Con la regla nueva,
entre las 00:00 y las 06:00 UTC ese día todavía corre en México y `leer_origen` lo saltaría:
las pruebas fallarían según la hora a la que se corren. `HOY` pasa a ser
`dia_local("meli", now)`, así `D1` y `D2` ya cerraron en México a cualquier hora.
`test_leer_filas_rechaza_fecha_futura` (fecha UTC + 1) sigue siendo futura a cualquier hora,
porque el día de México nunca va adelante del UTC.

`tests/test_conciliar_meli_ads.py` no usa `leer_origen` (corre el script de conciliación, que
tiene su propio reloj): solo es regresión.

## Pruebas vecinas de los módulos tocados

`tests/test_architecture.py tests/test_cli.py tests/test_ui_metricas.py tests/test_ingest_diario.py
tests/test_metricas_listas.py tests/test_placements.py tests/test_reports_pipeline.py
tests/test_ads_producto_ingesta.py tests/test_jev_senales.py` (las que importan `dashboard_resumen`,
`meli.ads_*` o `dia_cerrado`), con `-rs`, sobre `cfce88f`: `1 failed, 285 passed, 2 skipped`.

- El fallo era `test_presupuesto_de_tamano_por_modulo`: `app/meli/ads_ingest.py` llegaba a 914
  líneas (tope 900). Se recortó sin cambiar comportamiento (`_sella_reintento` recibe el origen,
  `+Counter` en lugar de un helper): 897 líneas. La misma prueba sigue en rojo por
  `app/ledger.py` (1000 líneas), que ya falla igual en `origin/master` desde D-I (#638) y no
  está en la allowlist: ajeno a D-F.
- Los 2 skipped son de `test_architecture.py` («el OS no soporta archivos ilegibles»): Windows.

## D-F.3 — el Resumen lee las zonas de `app/dia_cerrado.py`

`Canal` pierde `zona`; `hoy_por_canal` usa `dia_local`; `_cerrado` usa
`fin_del_dia(canal.plataforma, dia.fecha)`; se borra `_fin_del_dia`; `"zonas"` sale de `ZONAS`.
`hoy_resumen` conserva su firma. El comentario de zonas de `CANALES` pasó a `ZONAS`.

La respuesta declara las mismas zonas que antes
(`{'amazon_mx': 'America/Mexico_City', 'amazon_us': 'America/Los_Angeles', 'meli': 'America/Mexico_City'}`)
y ningún texto visible de la pantalla cambia, así que la ficha
`.cursor/skills/verify-orbit/features/resumen.md` no se toca.

## D-F.4 — `prueba.py` para `en_prod.sh`

Copia literal de la sección «DC», «Prueba real» de D-F.

```
rama (cfce88f):          D-F desplegado                                  exit=0
origin/master (f19942f): AssertionError: leer_origen sin reloj          exit=1
```

En producción hoy falla igual o antes (el diseño lo midió con `ModuleNotFoundError` antes de D-E).

## D-F.5 — `dia-en-curso.sql` y `dia-en-curso.sh` (a la espera del despliegue)

El `.sql` es la consulta de la sección «DC» entre `BEGIN READ ONLY;` y `ROLLBACK;`. El `.sh`
sigue el de D-I (`ventas-usd.sh`): corre el `.sql` con `docker exec … psql` y manda por
Telegram, con `envia_recordatorio.py`, si dio `t` (sin día en curso), `f` o si falló.

Ensayo en WSL, sin tocar goncloud: un `docker` falso que manda el `psql` al PG desechable y un
`envia_recordatorio.py` falso que solo imprime; el `.sh` es el del repo con `D=` apuntando a
una carpeta temporal.

- Copia hecha con el código nuevo (salta el día en curso): `t` y «Mercado Libre Ads ya no guarda
  el día en curso».
- Una copia posterior que sí guardó el día en curso: `f` y «Mercado Libre Ads todavía guardó el
  día en curso».
- `ingest_run` renombrada: «La lectura del día en curso de Mercado Libre Ads FALLÓ (psql salió
  con 3)» con el error de psql.

El `at` no está programado: se programa en goncloud después del despliegue, cuando el lead lo
indique, para las 08:45 UTC siguientes (la copia de MeLi Ads corre a las 08:15), copiando los dos archivos a
`/mnt/data/appdata/orbit/recordatorios/`. El número de trabajo y la salida de `at -c <número>`
van en `docs/evidencia/arreglos-03/D-F/at-c.txt`, que es lo que pide la `Acepta` de D-F.5.
