# ARREGLOS 04 · Diseño del frente «pantallas y fábrica»

Base: master `a4644aad` (lo que corre en producción). Hallazgos: `docs/evidencia/arreglos-2026-11-12/web.md` y `fabrica.md`.
Bloques: `plans/arreglos-04.md` (W-A a W-J, W-L y W-M).
Alcance vigente: el de `plans/arreglos-04.md`. W-K (semillas, falla 2 de `fabrica.md`) está en `DESCARTADO-semillas-estrictas.md` y
no forma parte del plan; donde este diseño diga otra cosa, manda el plan.

Para quién: el dueño. Hoy dos pantallas le dan gastos distintos para los mismos 30 días, el Resumen le dice «las ventas
bajaron» cuando no bajaron, una pantalla lo invita a negar búsquedas con datos de 3 días, un error al guardar se ve como
«[object Object]», le llegan 31 avisos de cortes de práctica que prometen aplicarse, pausar un lote puede dejar una
campaña nueva activa, y sacar anuncios «sin pedidos» puede sacar los de un producto que sí vende.

Todo lo medido en producción se leyó con `docs/evidencia/arreglos-01/lee_prod.sh` (solo SELECT) o con
`docs/evidencia/arreglos-01/en_prod.sh` (script puro, sin base ni red). La consulta va junto a cada número.

## Decisiones de alcance

| Hallazgo | Dónde queda | Razón |
| --- | --- | --- |
| web F3, F4, F5, F6, F7, F8, F10 | Dentro (W-C, W-D, W-E, W-F, W-G) | No tocan el motor de pujas ni los archivos de Ajustes. |
| web F2 (`cycle --decided-at` futuro) | Dentro (W-H) | Solo toca `app/cli.py:68-86`. TEMPORADA 01 no lista `app/cli.py` en su mapa de archivos (`docs/evidencia/temporada-01/DISENO.md:1464-1480`). |
| web F1, mitad de goals (meta y techos de un goal) | Dentro (W-I) | Solo toca `app/goals_write.py`, que TEMPORADA 01 no toca. Reusa `exige_banda_meta_manual` y `exige_bajo_techo` del bloque X de ARREGLOS 01. Es el lado que pega primero: los goals de plataforma 4 y 5 tienen la meta vacía. |
| web F1, mitad de Ajustes (meta manual de la plataforma) | Fuera, a un plan después de TEMPORADA 01 | Toca `config_write.proxima_config` y `CuerpoSettings` (`app/api_write.py:242-257`). TEMPORADA 01 bloque A rehace los dos (`DISENO.md:1341-1352` y fila A.2 de `plans/temporada-01.md:55`). |
| web F9 (tope «1,000» se tira) | Fuera, a un plan después de TEMPORADA 01 | El arreglo vive en `app/static/js/settings.js:99` o en `config_write._aplica_caps` (`app/config_write.py:490-491`). Los dos archivos los reescribe TEMPORADA 01 bloque A. El mensaje de error que verá David cuando se arregle ya lo deja listo W-F. |
| fábrica F1, F3, F4 y F5 | Dentro (W-J, W-L, W-M) | Crean, pausan o retiran en Amazon: van con prueba de dinero. |
| fábrica F2 (semillas de otro producto) | Fuera, en espera (`DESCARTADO-semillas-estrictas.md`) | Con la regla estricta casi ningún producto tendría semillas (pregunta c): lo decide el dueño. |
| Borrar `/series` y sus ayudantes | Dentro (W-A) | Nada los usa (ver W-A). Va antes de W-C y W-D, que tocan los mismos archivos. |
| Borrar el plan v1 de la fábrica | Dentro (W-B) | Producción no lo usa (ver W-B). Va antes de W-J y W-L. |

Parte de la prueba `test_madurez_de_cortes_con_reloj_futuro` de F2 (`windows.fin_ventana_cortes` con reloj futuro) es del
motor y no entra: con W-H ya no se le puede pasar un reloj futuro desde la terminal.

## Respuestas a las preguntas del encargo

**a. Reloj único.** W-C apoya el reloj de las pantallas en `hoy_resumen` de `app/dashboard_resumen.py` (`:220`) y no
crea ninguna tabla de zonas ni otra función de zona. La tabla única la pone ARREGLOS 03: su bloque D-E crea
`app/dia_cerrado.py` (`ZONAS`, `dia_local`, `ultimo_dia_cerrado`) y su D-F.3 hace que `hoy_por_canal`, y con él
`hoy_resumen`, lean de ahí. Funciona en cualquier orden de llegada mientras `hoy_resumen` conserve su firma: si W-C
entra primero, D-F.3 solo cambia lo de adentro; si entra D-F.3 primero, W-C llama a un `hoy_resumen` que ya lee
`ZONAS`. Nada de W-C toca `Canal.zona`, `CANALES` ni `_fin_del_dia`. `app/formato.py:16` (`ZONA_PANTALLA`) es solo la
zona en que se pintan las horas; no decide qué día es hoy.

**b. F3.** Se comparan los dos periodos a la misma edad. Callar la frase no inventa un número, pero en Amazon el periodo
visible siempre termina en D-1, así que callaría siempre: en la vista por omisión la tarjeta quedaría para siempre en
«Sin dato» (la meta del Resumen está vacía, config 51). La misma edad tampoco inventa nada: usa la lectura que Orbit
guardó entonces. Producción guarda una lectura nueva de cada día durante 20 a 23 días (medido abajo), así que la lectura
a la misma edad existe. Si falta, la cobertura de los dos periodos no coincide y la variación ya sale vacía por la regla
de cobertura de hoy (`app/dashboard_resumen.py:656-673`). Mercado Libre no publica madurez y solo tiene una lectura por
día, tomada 13 a 15 días después: ahí se sigue comparando como hoy.

**c. Semillas con la regla estricta (W-K, fuera del plan: espera decisión del dueño).** Con «solo ad groups que anuncian únicamente este producto», 248 de 249 productos
de México y 119 de 119 de Estados Unidos se quedan sin semillas de búsquedas. La biblioteca de keywords está vacía en
producción, así que tampoco aportan. En la vista previa, David ve en exacta, frase, amplia y por producto el texto nuevo
de la sección W-K, en lugar de «0 semillas para esta campaña.». La campaña automática sigue igual: busca sola y el
harvest pasa a exacta lo que venda. No hay una regla intermedia con evidencia: el reporte de productos junta los ads de
varios ad groups en una fila (`ad_ids` es una lista), así que no dice qué producto vendió en cada ad group.

**d. Negative cruzado.** Sí. Cada negative es un `Paso` más del mismo lote, con su fila en `fabrica_lote_paso` escrita
antes del envío y su lectura de comprobación, igual que los negatives de la biblioteca que ya nacen en la automática
(`app/fabrica_plan.py:888-897`). Entra en la huella sin campo nuevo: sale de `semillas.exact`, que ya está en el JSON
firmado del plan v2 (`huella_plan_v2`, `app/fabrica_plan.py:722-725`). El mismo plan da los mismos pasos. Hay un solo
lote en producción y está aplicado, así que ningún lote a medias mezcla las dos reglas.

**e. DISEÑOS 01.** Sus tres bloques ya están en master: A por #587, B por #588 y C por #590. El PR #584 sigue abierto,
pero es un intento anterior del bloque A, con el mismo contenido que ya entró (`_filtros_decisiones` está en
`app/api_dashboard.py:818`). `gh pr diff 584 --name-only` comparte con este frente `app/api_dashboard.py`, `app/ui.py`,
`app/formato.py`, `tests/test_api_dashboard.py`, `tests/test_ui.py`, `.cursor/skills/verify-orbit/helpers/orbit-verify` y
`.cursor/skills/verify-orbit/SKILL.md`. El choque se evita si el dueño cierra #584 sin mergear (los agentes del motor no cierran PRs): si se mergeara
hoy, chocaría también con master. Los bloques W parten de master, que ya trae DISEÑOS 01 completo, y tocan otras
funciones de esos archivos (series, campañas, filtros de plantilla), no Decisiones, Salud ni Contribución.

## Cruces con los otros planes

| Plan | Archivo compartido | Cómo se evita |
| --- | --- | --- |
| ARREGLOS 01 X | `tools/fabrica_campanas.py` (X llama `_exige_techos_humanos` en `:595` y `:621`; `:621` está en la rama v1 de `_arma_plan`) | W-B, al borrar la rama v1, conserva la llamada en el camino v2. W-I usa las funciones de X tal como X las define. W-N quita después esas llamadas (sección `## W-N`). |
| ARREGLOS 01 Q y Z | `tools/fabrica_campanas.py` (pausa y reanudación del impulso) | W-J cambia solo la consulta de «campañas del lote»; el impulso sigue con `_SQL_EXTERNALS_CAMPANA_DEL_LOTE`, que ya no filtra por estado. |
| TEMPORADA 01 A | `app/api_dashboard.py` (datos de Ajustes), `tests/test_api_write.py` | W-C toca otras funciones de `api_dashboard.py`. W-F cambia una sola aserción de `tests/test_api_write.py:1705`. |
| TEMPORADA 01 G | `app/pantalla_dinero.py`, `app/static/js/dinero.js` | W-C no toca `pantalla_dinero.py`: le pasa `hasta` desde `api_dashboard.py:1973`. W-F arregla F8 en el servidor y no toca `dinero.js`. W-N sí lo toca: ver «Cruce con TEMPORADA 01, bloque G» en `## W-N`. |
| TEMPORADA 01 (C a F) | `app/cycle.py`, `app/notifica.py` | W-G cambia un bucle de 6 líneas en `cycle._fase_notifica` (`app/cycle.py:3645-3654`) y agrega funciones en `notifica.py`. Si TEMPORADA agrega su aviso en el mismo bloque, el choque es de texto y se resuelve a mano. |
| ARREGLOS 03 D-F.3 | `app/dashboard_resumen.py` (`Canal.zona`, `hoy_por_canal`, la línea `"zonas"` de `resumen()`) | W-C y W-M usan `hoy_resumen` tal como quede (pregunta a). W-D edita `bloque_canal` y `resumen()`: si D-F.3 ya entró, sobre su versión, conservando su línea `"zonas"`. |
| ARREGLOS 03 D-A, D-B, D-K, P-B | `app/cli.py` (`_construye_parser`, `main`), `app/notifica.py` | W-H edita `_parse_decided_at`; W-G agrega funciones nuevas en `notifica.py` y borra dos. Funciones distintas, por contenido. |
| ARREGLOS 03 D-C | `app/api_dashboard.py` (cortes), `app/ui.py`, ficha `resumen.md` | W-A, W-C y W-E tocan otras funciones; en la ficha, se conservan sus cambios. |
| DISEÑOS 01 | ver pregunta e | El dueño cierra #584 sin mergear; este plan no lo toca. |

En todo archivo compartido se cambia por contenido, no por número de línea, y al rebasar se conservan los cambios del
otro plan.

## W-A — Borrar `/series` y lo que solo vive para ella

**Hoy.** `GET /api/dashboard/series/plataforma` y `/series/campana` (`app/api_dashboard.py:341-405`) no los llama
ninguna pantalla. Viven con 6 ayudantes y su propio reloj UTC, y el Resumen ya tiene su contrato.

**Qué se borra.**
- `app/api_dashboard.py`: rutas `serie_plataforma` y `serie_campana`; `_SQL_ENTIDAD`, `_SQL_SERIE_PLATAFORMA`,
  `_SQL_SERIE_CAMPANA` (`:147-177`); `_ventana_efectiva`, `_ventana_inmaduros`, `_fila_serie`, `_arma_serie`
  (`:248-338`); `MAX_DIAS_VENTANA` y `DIAS_VENTANA_DEFAULT` de este archivo (`:130`, `:137`). `_acoso` se queda: lo usa
  Campañas (`:660`). El docstring del módulo pierde el párrafo de `_hoy_utc` y la frase «La ruta `/series/plataforma`
  conserva su contrato intacto» de `resumen_ads`.
- `app/static/js/dashboard.js`: `graficarSeries` (`:74` en adelante) y el bucle `canvas[data-serie]` del
  `DOMContentLoaded` (`:325-332`). Ninguna plantilla tiene `data-serie`. El comentario de la línea 3 se ajusta.
- `app/ui_metricas.py`: `kpis_serie` (`:44`) y `clase_cambio` (`:79`), y la rama de `_frase_tarjeta` en `:284-292`.
  Esa rama nunca corre: pide `var["motivo"] == "cobertura_incompleta"`, y `variacion` solo da `None` o
  `"cobertura_distinta"` (`app/dashboard_resumen.py:742`). `_picos_ma7` se queda: lo usa `:448`.
- `app/ui.py:58` y `:115-116`: el import y el registro de los dos filtros.
- Pruebas: las `test_serie_*` y `test_arma_serie_*` de `tests/test_api_dashboard.py`, las de `kpis_serie` en
  `tests/test_ui_metricas.py` y la línea `sin_escape.filters["clase_cambio"]` de `tests/test_ui.py:120`. Dos garantías se
  conservan reescritas contra `/api/dashboard/resumen`, porque el Resumen usa la misma forma de consulta y no tiene esas
  pruebas con base: `test_serie_plataforma_colapso_bitemporal_gana_la_ultima` (la última lectura gana) y
  `test_serie_plataforma_anti_doble_conteo_solo_campaign` (solo cuenta el grano campaña). No son pruebas nuevas.
  Tres pruebas más dependen de lo borrado: `test_ventana_efectiva_default_recorte_y_validaciones` (`:392`) se borra;
  `test_series_sql_filtran_kind_campaign_en_ad_entity` (`:451`) se conserva porque también guarda `_SQL_CAMPANAS_30D`
  (que Campañas no cuente el dinero dos veces): su bucle queda solo con esa consulta y el mutante `OR TRUE` que ya
  trae se arma sobre ella; `test_sin_dsn_de_lectura_fail_closed_503` (`:530`) pide `/api/dashboard/resumen`, que usa
  la misma conexión de lectura.
- Documentos: `docs/DASHBOARD.md` secciones 3.1 y 3.2, la fila 3 de la tabla (`:24`) y las menciones de `:71`, `:485` y
  `:700`. Ficha `.cursor/skills/verify-orbit/features/resumen.md`: la sub-feature `resumen-api` (`:18`) y el paso
  «Confirmar lado JSON antiguo» (`:37`) pasan a usar `GET /api/dashboard/resumen?canal=amazon_us`. Ayudante
  `.cursor/skills/verify-orbit/helpers/orbit-verify:1449`, `:1470` y `:1575`: igual; los chequeos `seed_*` y
  `mx_all_cost_none` leen `canales.amazon_us.series` y `canales.amazon_mx.series`.

**Prueba de que nada lo usa** (fila W-A.1 la guarda):
- `rg -n "series/(plataforma|campana)" app tools scripts verify .github docs/runbooks` solo da `app/api_dashboard.py`.
- `rg -n "data-serie" app/templates` no da nada. `rg -n "kpis_serie|clase_cambio" app/templates` no da nada.
- Fuera del repo: `rg -l "api/dashboard/series"` sobre `~/dev` (sin carpetas de Orbit) no da nada.
- No pude revisar los monitores de Kuma desde aquí. Si alguno pidiera `/series`, después del despliegue daría 404 y
  avisaría por «Server Alerts»; el bloque lo anota como riesgo en su evidencia.

**La otra forma.** Dejar `/series` y solo cambiarle el reloj. Se descarta: mantiene 185 líneas y un reloj que nadie lee.

## W-B — Borrar el plan v1 de la fábrica

**Hoy.** La fábrica tiene dos planes: v1 (por producto) y v2 (por publicación). La página solo manda `listing_ids`, y
producción solo tiene un lote, que es v2.

**Medido.**
```
select count(*), count(*) filter (where plan ? 'productos'), count(*) filter (where plan ? 'publicaciones') from fabrica_lote
→ 1 | 0 | 1
select min(id), min(created_at)::date, max(id) from config_version where settings->>'fabrica.creacion' = 'v2'
→ 16 | 2026-09-07 | 51      (y 0 configuraciones con 'v1')
```

**Qué se borra.**
- `app/fabrica_plan.py`: `ProductoGrupo`, `PlanGrupo`, `PlanCanonico` (queda `PlanGrupoV2` en su lugar),
  `plan_como_json`, `huella_plan`, `plan_desde_json`, `lineas_dry_run`, `CLAVE_CREACION`, `VERSIONES_CREACION`,
  `version_creacion_desde_settings` y cada rama `isinstance(plan, PlanGrupoV2)` (queda solo el lado v2).
- `app/fabrica_web.py`: `_huella_plan` y `_plan_como_json` (`:115-125`) se reemplazan por `fp.huella_plan_v2` y
  `fp.plan_v2_como_json`; `_creacion_v2_habilitada` (`:294-297`) y su guarda en `:691`; la rama v1 de `:608`.
- `tools/fabrica_campanas.py`: `_SQL_PRODUCTOS` (`:86`), `_productos` (`:393`), `_ids_productos` (`:530`), la rama v1
  de `_arma_plan` (`:592-628`), `_creacion_v2_habilitada` (`:387-390`) y su guarda (`:771-776`), la opción
  `--productos` (`:807`, `:848`, `:858`) y las ramas v1 de `_plan_como_json` (`:1004-1007`) e `_imprime_dry_run`.
  Si el bloque X de ARREGLOS 01 dejó una llamada a `exige_bajo_techo` en la rama v1, se mueve al camino v2
  (`_arma_plan_v2`) antes de borrar la rama.
- `app/api_fabrica.py`: el campo `productos` de `SolicitudPlan` (`:194`), su validador y el «o uno o el otro»
  (`:207-223`); `listing_ids` y `objetivo` pasan a obligatorios. La entrada `"productos"` de `_NOMBRE_CAMPO` se va.
- `app/static/js/fabrica.js:281`: `esV2 ? plan.publicaciones : plan.productos` queda `plan.publicaciones`.
- `docs/DEPLOY.md:1735-1746`: el párrafo de `fabrica.creacion=v1` se reemplaza por una línea: «Desde ARREGLOS 04 la
  fábrica solo crea planes v2; la clave `fabrica.creacion` ya no se lee.»
- Pruebas de v1 en `tests/test_fabrica_plan.py`, `tests/test_fabrica_campanas.py` (las de `--productos`) y
  `tests/test_api_fabrica.py`. Las que prueban la mutación por el camino v1 se pasan a `--listing-ids`.
  `tests/test_estimacion_aislamiento_fabrica.py:100-104` elige entre `plan_v2_desde_json` y `plan_desde_json`: queda
  `plan = fp.plan_v2_desde_json(plan_json)`.

**Prueba de que nada lo usa** (fila W-B.1): la página manda `listing_ids` (`app/static/js/fabrica.js:442`), los
runbooks no llaman `--productos` de la fábrica (`rg -n -- "--productos" docs/runbooks` vacío; las menciones de
`docs/CHAT-CONTEXT.md` son de la ingesta de productos, otra herramienta) y las dos consultas de arriba.

**La otra forma.** Dejar v1 y solo apagarlo. Se descarta: el interruptor ya vale v2 desde el 7-sep y las 16 ramas
`isinstance` siguen costando en cada arreglo de la fábrica.

## W-C — Un solo reloj en las pantallas (F4)

**Hoy.** Campañas toma «hoy» en UTC (`app/api_dashboard.py:703`). De 18:00 a 24:00 en el centro de México ya es mañana en
UTC, así que su ventana de 30 días termina en el día en curso, vacío, y pierde un día con datos. El Resumen usa la zona
del canal.

**Medido.**
```
select now() at time zone 'America/Mexico_City', (now() at time zone 'UTC')::date, (now() at time zone 'America/Mexico_City')::date,
  (select sum(v.cost) from v_metric_latest v join ad_entity e on e.id=v.ad_entity_id where e.kind='campaign' and e.platform='amazon_mx'
     and v.metric_date between (now() at time zone 'UTC')::date - 30 and (now() at time zone 'UTC')::date - 1),
  (select sum(v.cost) ... between (now() at time zone 'America/Mexico_City')::date - 30 and (now() at time zone 'America/Mexico_City')::date - 1)
→ 2026-11-12 22:33 | 2026-11-13 | 2026-11-12 | 23113.1100 | 23713.5300
```

**Uso del llamador.**
```python
# app/api_dashboard.py, campanas_resumen
plataformas = (platform,) if platform is not None else tuple(PLATAFORMAS_MONEDA)
desde, hasta = _ventana_campanas(plataformas, _ahora_utc())

# productos y búsquedas
hoy=_hoy_pantalla(mercado)
# Dónde poner el dinero: el día lo pone la pantalla; pantalla_dinero no se toca (TEMPORADA 01 G)
pantalla_dinero.lee_dinero(conn, plataforma=mercado, hasta=_hoy_pantalla(mercado) - dt.timedelta(days=1))
```

**Firmas.**
```python
def _hoy_pantalla(*plataformas: str) -> dt.date:
    """Hoy de las pantallas: el del Resumen (hoy_resumen), fecha local mínima de esos canales."""
    return hoy_resumen(plataformas, _ahora_utc())

def _ventana_campanas(plataformas: Sequence[str], instante: dt.datetime) -> tuple[dt.date, dt.date]:
    """[D-30, D-1] con el hoy del Resumen: la misma ventana que «Resumen · 30 días» de esos canales."""
    hoy = hoy_resumen(plataformas, instante)
    return hoy - dt.timedelta(days=DIAS_VENTANA_CAMPANAS), hoy - dt.timedelta(days=1)
```

**Sitios.** `_hoy_utc` (`:238-240`) se borra. Cambian `:703` (Campañas), `:1183` (`_avisos_campana_de`: `hasta =
_hoy_pantalla(plataforma) - 1 día`), `:1973` (dinero, con `hasta`), `:1993` (productos), `:2072` (búsquedas) y `:2030`
(`productos_simula_pausa` pasa `ahora=_ahora_utc()`). `:351` y `:392` ya no existen tras W-A. `app/ui.py:25` y el
docstring del módulo dejan de decir que el reloj es UTC. W-A ya quitó la fila 3 de la tabla de `docs/DASHBOARD.md` (`:24`)
y la sección 3.1 (`:112`); la sección 3.3 «Resumen de campañas» dice: «La ventana es [D-30, D-1] con el hoy del Resumen:
la fecha local mínima de los canales pedidos (`hoy_resumen`).» La fila W-C.1 comprueba que en `app/api_dashboard.py` no
queda `_hoy_utc` ni `now(dt.UTC).date()`, así productos y búsquedas no pueden quedarse con el reloj viejo.

**Pruebas que cambian.** `tests/test_api_dashboard.py:175-177` (`_hoy`) y `tests/test_ui.py:754` y `:794` fijan
`_ahora_utc` en `dt.datetime.combine(fecha, dt.time(12), tzinfo=dt.UTC)`: a mediodía UTC las dos zonas de Amazon están en
el mismo día, así que cada prueba ve el mismo «hoy» que antes.

**Sin cambio de texto visible.** Campañas sigue diciendo 30 días; ahora son los mismos 30 días del Resumen.

**La otra forma.** Mover Campañas a UTC en las dos pantallas. Se descarta: el día de reporte de Amazon MX cierra en la
zona de México, y el Resumen ya lo hace bien.

**Fuera.** `app/pantalla_ruido.py:70` (encogimiento de 90 días) sigue con la fecha UTC de la base; es una ventana de
cambios de puja, no de métricas, y no se compara con otra pantalla. Los relojes de `metricas_listas.py`, `cli_bids.py`
y `notifica.py` no son pantallas.

## W-D — El Resumen compara a la misma edad y distingue «faltan datos» (F3 y F10)

**Hoy.** F3: el periodo actual está madurando y el anterior ya maduró; la tarjeta dice «Atención: las ventas bajaron».
F10: con meta configurada, un ACoS vacío por un hueco se escribe «Sin ventas atribuidas» aunque haya 5,000 de venta.

**Medido** (vista de 7 días, 12-nov 22:18 CDMX):
```
-- lecturas por día: cada día se vuelve a leer a diario durante 20 a 23 días
select e.platform, o.metric_date, count(*), count(distinct (o.observed_at at time zone 'UTC')::date),
       min((o.observed_at at time zone 'UTC')::date - o.metric_date), max(...)
  from ads_metric_observation o join ad_entity e on e.id = o.ad_entity_id
 where e.kind = 'campaign' and e.platform in ('amazon_mx','amazon_us') and o.metric_date between '2026-10-20' and '2026-10-23' group by 1,2
→ amazon_mx|2026-10-20|532|28|22|2|23 ... amazon_us|2026-10-23|160|8|20|1|20
-- Mercado Libre: una sola lectura por día, 13 a 15 días después
→ meli|2026-10-20|4|1|15 ; meli|2026-10-21|4|1|14 ; meli|2026-10-22|4|1|13
-- totales (consulta completa en el anexo A)
actual 5-11 nov        MX venta 41,337.52   US venta   969.25
anterior, final        MX venta 36,885.75   US venta 1,295.07   → hoy dice +12.1 % MX y −25.2 % US
anterior, misma edad   MX venta 34,957.49   US venta 1,145.67   → con el arreglo: +18.2 % MX y −15.4 % US
```
La consulta del periodo anterior a la misma edad tarda lo mismo que la de hoy más medio segundo en 30 días
(3.27 s contra 2.75 s de un `select 1`, ambos por ssh).

**Forma elegida (F3).** En `bloque_canal`, el periodo anterior de un canal con madurez publicada (`canal.dias_inmaduros
is not None`, es decir Amazon) se lee tal como estaba en `instante - ventana.dias`. Cada día anterior queda con la misma
edad que su par del periodo actual. Los totales que se muestran como «anterior» y la variación salen de la misma lectura,
así que la tabla «Periodo anterior por canal» cuadra con sus Δ.

```python
# app/dashboard_resumen.py, resumen()
bloques = {n: bloque_canal(conn, CANALES[n], ventana, hoy, instante=instante) for n in canales}

def bloque_canal(conn, canal: Canal, ventana: Ventana, hoy: dt.date, *, instante: dt.datetime) -> dict:
    filas = conn.execute(_SQL_DIAS, (canal.plataforma, ventana.calentamiento_desde, ventana.hasta)).fetchall()
    ...                                            # dias_actuales y serie como hoy
    if canal.dias_inmaduros is None:                # Mercado Libre: sin madurez publicada, se compara lo último leído
        corte = None
        filas_ant = conn.execute(_SQL_DIAS, (canal.plataforma, ventana.anterior_desde, ventana.anterior_hasta)).fetchall()
    else:
        corte = instante - dt.timedelta(days=ventana.dias)
        filas_ant = conn.execute(_SQL_DIAS_A_LA_EDAD,
                                 (canal.plataforma, ventana.anterior_desde, ventana.anterior_hasta, corte)).fetchall()
    dias_anteriores = arma_dias({f[0]: f[1:] for f in filas_ant}, ventana.anterior_desde, ventana.anterior_hasta)
    anterior = totales_dias(dias_anteriores, ventana.dias)
    "comparison": {..., "totals": anterior,
                   "observado_hasta": None if corte is None else corte.astimezone(dt.UTC).isoformat(),
                   "variacion": variacion(totals, anterior)}
```

```sql
-- _SQL_DIAS_A_LA_EDAD: las mismas columnas y el mismo colapso que _SQL_DIAS, con la última lectura hasta el corte
SELECT v.metric_date,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL) THEN sum(v.clicks)::bigint END,
       CASE WHEN bool_and(v.orders IS NOT NULL) THEN sum(v.orders)::bigint END,
       max(v.observed_at)
  FROM (SELECT DISTINCT ON (o.ad_entity_id, o.metric_date)
               o.metric_date, o.cost, o.ad_revenue, o.clicks, o.orders, o.observed_at
          FROM ads_metric_observation o
          JOIN ad_entity e ON e.id = o.ad_entity_id
         WHERE e.kind = 'campaign' AND e.platform = %s::platform
           AND o.metric_date BETWEEN %s AND %s
           AND o.observed_at <= %s
         ORDER BY o.ad_entity_id, o.metric_date, o.observed_at DESC) v
 GROUP BY v.metric_date
 ORDER BY v.metric_date
```
`v_metric_latest` es `DISTINCT ON (ad_entity_id, metric_date) ... ORDER BY observed_at DESC`
(`migrations/0001_initial.sql:1159-1162`): el colapso es el mismo, solo se agrega el corte.

**Textos nuevos (F3).**
- `app/templates/resumen.html`, después de `<p class="ch-takeaway">` y solo si `bloque.comparison.observado_hasta`:
  «La comparación usa el periodo anterior tal como se veía hace {{ bloque.dias }} días, para que los dos lleven el mismo
  retraso en las ventas que todavía llegan.»
- El `<caption>` de «Periodo anterior por canal» (`:361`): «Comparación contra el periodo anterior de igual longitud. En
  Amazon, el periodo anterior se mide tal como se veía hace los mismos días, para que los dos lleven el mismo retraso.»
- La frase de la tarjeta no cambia de redacción.

**Forma elegida (F10).** En `_frase_tarjeta` (`app/ui_metricas.py:259`), con meta y ACoS vacío:
```python
if bloque["totals"].get("sin_ventas"):
    return f"Sin ventas atribuidas frente a la meta de {meta}%."
return f"Faltan datos de algunos días: no se puede comparar el ACoS con la meta de {meta}%."
```
El estado de la tarjeta no cambia.

**La otra forma (F3).** Callar estado y frase mientras el periodo actual tenga días inmaduros. Se descarta: en Amazon
siempre los tiene (D-1 a D-8), así que la comparación desaparecería de las vistas de 7, 30 y 90 días.

**Contrato.** `docs/DASHBOARD.md`, sección del Resumen: `comparison.totals` es el periodo anterior tal como se veía en
`comparison.observado_hasta` (Amazon) o la última lectura (Mercado Libre, `observado_hasta: null`). Ficha `resumen.md`:
`resumen-tarjetas` menciona la línea nueva, y el paso «Abrir Resumen» busca «en las ventas que todavía llegan» en el
HTML. Esa frase solo está en la línea de la tarjeta; «tal como se veía hace» también sale en el caption, que siempre se
pinta, así que no probaría nada. Aparte busca el caption nuevo. `drive-resumen` suma el chequeo `tarjeta_misma_edad`
con la misma búsqueda, para que la verificación de la pantalla falle si la línea se pierde.

**Pruebas que cambian.** Las de `tests/test_dashboard_resumen.py` y `tests/test_api_dashboard.py` que siembran el
periodo anterior con `observed_at = now()` y fijan un reloj en el pasado: siembran `observed_at` antes del corte. Sin
prueba nueva: ningún número de esta tarjeta lleva a David a mover dinero desde ella.

**Comprobación que puede fallar (falla 3).** Como no hay prueba nueva, la comprueba el `prueba.py` de W-D (fila W-D.4)
contra el código desplegado. La reproducción de la revisión no sirve tal cual: su conexión falsa responde por el texto
`GROUP BY v.metric_date`, que `_SQL_DIAS_A_LA_EDAD` también trae. El guion responde primero a la consulta que contiene
`o.observed_at <=` con la venta a la misma edad (76 por día en el periodo anterior) y después a `GROUP BY v.metric_date`
con la foto final de `test_resumen_7d_no_concluye_caida_por_dias_inmaduros`. Medido hoy con esa conexión sobre
`a4644aad`: `comparison.totals.ad_revenue` da 676.0000 (la foto final), no hay `observado_hasta` y la frase dice «Las
ventas bajaron -21.30%». Con el arreglo da 532 (7 × 76) y la frase ya no dice «bajaron».

## W-E — Búsquedas sin venta con 10 días de madurez, y ruido sin días inmaduros (F5 y F6)

**Hoy.** F5: «Búsquedas que gastan sin vender» invita a negar a mano con datos que terminan en hoy menos 3
(`app/pantalla_busquedas.py:287-288`) y cubre 91 días. F6: «Después del encendido» llega hasta ayer
(`app/pantalla_ruido.py:80-90`, sin tope superior).

**Medido.** `en_prod.sh` con una conexión falsa: `lee_busquedas(hoy=2026-11-12)` da `hasta 2026-11-09` y 91 días;
`_comparacion` recibe solo `(conn, plataforma, dias)`.

**F5, forma.**
```python
from app.optimizer.windows import DIAS_MADUREZ_CORTES
DIAS_BUSQUEDAS = 90
hasta = hoy - dt.timedelta(days=DIAS_MADUREZ_CORTES)          # la misma madurez que exige el motor para cortar
desde = hasta - dt.timedelta(days=DIAS_BUSQUEDAS - 1)          # 90 días exactos
```
Es dinero: la pantalla lleva a David a negar búsquedas a mano. Lleva prueba.

Textos (`app/templates/busquedas_sin_venta.html`):
- `:5-6`: «Solo campañas encendidas, 90 días que terminan el {{ hasta | fecha_corta }}. Los últimos 10 días se dejan
  fuera porque sus ventas todavía llegan, igual que hace Orbit antes de cortar. Esta pantalla no niega nada en Amazon: los
  negatives los agregas a mano.»
- `:15`: «Sin pedidos o sin clics en estos 90 días: no hay referencia de azar.» queda igual.
  El contrato ya trae `desde` y `hasta`. Si la plantilla no recibe `hasta`, se pasa desde la ruta de `app/ui.py`.

**F6, forma.**
```python
def _comparacion(conn, plataforma: Plataforma, dias: int, *, hoy: dt.date) -> dict | None:
    ultimo_maduro = hoy - dt.timedelta(days=DIAS_INMADUROS + 1)    # dashboard_resumen.DIAS_INMADUROS = 8 → D-9
    filas = conn.execute(_SQL_MERCADO, (plataforma, dia - dt.timedelta(days=dias), ultimo_maduro)).fetchall()
    despues = [f for f in filas if dia <= f.metric_date <= ultimo_maduro]
    return {..., "despues_hasta": ultimo_maduro.isoformat()}

def lee_ruido(conn, *, plataforma: Plataforma, hoy: dt.date, dias: int = 30) -> PantallaRuido
```
`_SQL_MERCADO` suma `AND m.metric_date <= %s`. El filtro en Python repite el tope para que una fila fuera de rango no
cuente aunque llegue. La ruta (`app/api_dashboard.py:1960`) pasa `hoy=_hoy_pantalla(mercado)`. Si el encendido es más
reciente que D-9, «después» queda sin filas y la plantilla ya dice «Sin datos.».

Texto (`app/templates/ruido.html`, el `<h4>` de después): «Después del encendido ({{ n }} días, hasta el
{{ antes_y_despues.despues_hasta | fecha_corta }}; los últimos 8 días se dejan fuera porque sus ventas todavía llegan)».

No hay ficha de verify-orbit para estas dos pantallas (`ls .cursor/skills/verify-orbit/features/`).

**La otra forma (F6).** Marcar los días inmaduros en lugar de cortarlos. Se descarta: el ACoS de «después» seguiría
mezclando días a medio llegar; la marca solo avisa del error.

## W-F — Un error al guardar dice qué campo está mal (F8)

**Hoy.** FastAPI responde un 422 de validación con `detail` como lista de objetos en inglés. `settings.js:125` y `:154`,
`danadas.js:35` y `:72`, `dinero.js:36` y `:139` y `familias.js:42` y `:72` pintan `"Error: " + detail`: sale
«Error: [object Object]». La fábrica ya traduce esos errores en el servidor (`_RutaFabrica`, `app/api_fabrica.py:80-99`).

**Forma elegida: un traductor en el servidor.** Un manejador de `RequestValidationError` en `app/main.py` responde
`{"detail": "<texto>"}` para toda la app. La fábrica usa el mismo traductor dentro de su envoltura (su `detail` sigue
siendo `{"mensaje", "lote"}`).

```python
# app/main.py
@app.exception_handler(RequestValidationError)
async def _validacion_en_espanol(_request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": mensaje_validacion(exc.errors())})

# app/api_common.py
NOMBRE_CAMPO: dict[str, str]          # el _NOMBRE_CAMPO de api_fabrica, más los campos de abajo
CAMPOS_DE_PAGINA = frozenset({"base_config_version_id", "hoja_id", "queue_id", "decision_id", "campana_id"})

def mensaje_validacion(errores: Sequence[Mapping]) -> str:
    """Un 422 en español: qué campo y qué le falta. Nunca repite lo que se tecleó."""
    if any(_ultima_parte(e) in CAMPOS_DE_PAGINA for e in errores):
        return "La página quedó vieja: recárgala y vuelve a intentar."
    if any(e.get("type") == "extra_forbidden" for e in errores):
        return "La pantalla mandó un dato que no se esperaba: recárgala y vuelve a intentar."
    partes = dict.fromkeys(f"«{_campo(e)}» ({_razon(e)})" for e in errores)
    return "Revisa " + ", ".join(partes) + "."
```
`_ultima_parte(e)` es la última parte de `loc` que sea texto. Un error en un id que la página manda sola
(`CAMPOS_DE_PAGINA`) no es algo que David haya tecleado: solo puede pasar con una página vieja o rota, así que el
mensaje completo le dice que la recargue. Lo mismo con un campo de más (`extra_forbidden`). `_campo(e)`: busca primero
la llave compuesta de las dos últimas partes de `loc` (`"caps.bid"`), luego cada parte de `loc` de atrás para adelante
en `NOMBRE_CAMPO`. Sin nombre conocido, «un dato del formulario».

Campos que se agregan a `NOMBRE_CAMPO`: `target_manual_pct` «Meta manual de ACoS»; `target_acos_pct` «Meta de ACoS»;
`caps.bid` «Tope diario de cambios de puja»; `caps.pause` «Tope diario de pausas»; `caps.negative` «Tope diario de
negatives»; `caps.harvest` «Tope diario de búsquedas que pasan a exacta»; `confianza_recorte` «Confianza para bajar
pujas»; `confianza_subida` «Confianza para subir pujas»; `fraccion` «Fracción del margen»; `bid_floor` «Puja mínima»;
`bid_ceiling` «Puja máxima»; `harvest_default_bid` «Puja inicial de las búsquedas que pasan a exacta»;
`resumen_acos_objetivo` «Meta de ACoS del Resumen»; `nombre` «Nombre»; `presupuesto` «Presupuesto diario». Los ids de
`CAMPOS_DE_PAGINA` no llevan nombre: su mensaje es el de la página vieja. `actor` pasa de «Actor» a «Tu nombre».

`_razon(e)` por `e["type"]`:

| Tipo de Pydantic | Razón |
| --- | --- |
| `decimal_parsing`, `float_parsing`, `int_parsing`, `int_from_float`, `decimal_type`, `float_type`, `int_type`, `finite_number` | escribe solo el número, sin % ni comas |
| `greater_than`, `greater_than_equal`, `less_than`, `less_than_equal`, `decimal_max_digits`, `decimal_max_places` | está fuera del rango permitido |
| `missing` | falta |
| `string_too_short` | no puede quedar vacío |
| `string_too_long` | es demasiado largo |
| `bool_parsing`, `bool_type` | debe ser sí o no |
| cualquier otro | no es válido |

Ejemplo con «20%» en la meta: «Error: Revisa «Meta manual de ACoS» (escribe solo el número, sin % ni comas).»
La fábrica conserva su caso «no es finito» (`app/api_fabrica.py:89-95`) y cambia «Los datos no son válidos en: …» por
`mensaje_validacion(errores)`. `_NOMBRE_CAMPO` y `_campo_fallido` se borran de `api_fabrica.py`.

**La otra forma.** El movimiento 2 de `web.md`: `Orbit.post` con `mensajeError` en `formato.js` y los 16 `fetch` de 7
archivos migrados. Se descarta para este plan: el texto en inglés nace en el servidor y se arregla una vez para todas
las pantallas y para quien use la API. Además `settings.js` y `dinero.js` los reescriben TEMPORADA 01 A y G. Juntar los
`fetch` sigue valiendo como limpieza después de TEMPORADA 01.

**Contrato que cambia.** `tests/test_api_write.py:1705` pasa de `isinstance(..., list)` a `isinstance(..., str)`.
`tests/test_api_fabrica.py:1062` exige hoy «Los datos no son válidos»; pasa a exigir «Revisa» (su error no es de
un id de página ni un campo de más). No hay JavaScript que lea la lista: `cortes.js:14-27` y `productos.js:59-` ya aceptan
texto. Sin prueba nueva: no es dinero.

## W-G — Un aviso por ciclo y «práctica: no se aplicará» (F7)

**Hoy.** `aviso_corte_encolado` (`app/notifica.py:251-264`) dice «ventana de veto 48h» aunque el corte sea de práctica,
y `cycle._fase_notifica` manda uno por corte (`app/cycle.py:3645-3654`).

**Medido.**
```
select d.cycle_id, q.platform, q.modo, count(*) from apply_queue q join decision d on d.id = q.decision_id
 where d.decided_at >= timestamptz '2026-10-13' group by 1,2,3 order by 4 desc limit 5
→ 174|amazon_us|shadow|31 ; 124|amazon_us|live|2 ; 147|amazon_mx|live|1 ...
```

**Uso del llamador.**
```python
# app/cycle.py, _fase_notifica, en lugar del for de 6 líneas
if not notifica.notifica_cortes_encolados(avisos):
    notas["aviso_encola"] = ("fallo: aviso de corte encolado no enviado por Telegram "
                             "(revisar la cola de veto: la ventana 48h corre igual)")
_latido()
```

**Firmas.**
```python
TOPE_CORTES_POR_AVISO = 5

def avisos_cortes_del_ciclo(avisos: Sequence[CorteEncolado]) -> list[str]:
    """Un mensaje por plataforma con todos los cortes nuevos del ciclo. Lista vacía sin cortes."""

def notifica_cortes_encolados(avisos: Sequence[CorteEncolado], *, transport=None) -> bool:
    """Canal apagado o sin cortes -> True. False si algún mensaje no salió. Fail-silent como el resto."""
```
`aviso_corte_encolado` y `notifica_encola` se borran (único llamador: `app/cycle.py:3647`). `etiqueta` se importa
diferido, como `pantalla_dinero` en `app/notifica.py:1947`. Las fechas van con `formato.fecha_hora`.

**Texto exacto** (plataforma con `etiqueta(platform, "plataforma")`):
```
[Orbit] Amazon US: 31 cortes nuevos en este ciclo
En práctica: 31. Son de prueba: no se aplicarán.
- Negar la búsqueda «funda iphone»
- Pausar una keyword o un producto
- ... (hasta 5)
y 26 más
```
Con cortes en vivo, antes de la sección de práctica:
```
En vivo: 2. Se aplican solos el 14 nov, 11:03 (hora del centro de México) si no los rechazas en Propuestas.
```
La fecha es el `vence_el` más temprano de los cortes en vivo. Cada línea: `negative` con término «Negar la búsqueda
«…»», `harvest` con término «Pasar a exacta la búsqueda «…»», `pause` «Pausar una keyword o un producto»; otro tipo,
`etiqueta(kind, "kind")`. Un corte da «1 corte nuevo en este ciclo».

**Prueba.** Falla que ya pasó (ciclo 174): 31 cortes de práctica dan un solo mensaje, sin «48h» y con «no se
aplicarán». En `tests/test_notifica.py`. Las pruebas de `aviso_corte_encolado` (`tests/test_notifica.py:146-175`) y de
`notifica_encola` (`:888`) pasan a las funciones nuevas.

**La otra forma.** Dejar un aviso por corte con el texto arreglado. Se descarta: 31 mensajes en una corrida siguen
siendo ruido; los avisos de precio ya agrupan (tope 5 y uno agrupado).

## W-H — La terminal no acepta un ciclo con fecha futura (F2)

**Hoy.** `_parse_decided_at` (`app/cli.py:68-86`) solo exige zona horaria. Con el mes equivocado, el ciclo libera cortes
en veto, da por vencidos los vetos y corta con 4 días de madurez.

**Medido.** `en_prod.sh`: `cli._parse_decided_at(ahora + 40 días)` devuelve `2026-12-23 04:31:12+00:00` sin error.

**Forma.**
```python
TOLERANCIA_RELOJ = dt.timedelta(minutes=5)

def _parse_decided_at(texto: str | None, *, ahora: dt.datetime | None = None) -> dt.datetime:
    ahora = ahora or dt.datetime.now(dt.UTC)
    ...  # lo de hoy
    momento = momento.astimezone(dt.UTC)
    if momento > ahora + TOLERANCIA_RELOJ:
        raise ValueError(
            f"--decided-at no puede ir en el futuro: llegó {momento.isoformat()} y ahora es "
            f"{ahora.isoformat()} (UTC). Un reloj adelantado liberaría cortes en veto y cortaría sin madurez."
        )
    return momento
```
`_cycle` ya convierte el `ValueError` en salida 2 sin llamar al ciclo (`app/cli.py:100-106`). Solo las pruebas usan
`--decided-at` (`tests/test_cli.py:95`, fecha pasada). La ceremonia de confirmación que sugiere `web.md` no entra: el
cron no pasa `--decided-at`, y una fecha pasada no libera nada antes de tiempo.

**Prueba.** Dinero: es la promovida de `test_rev_cli.py::test_cycle_rechaza_decided_at_en_el_futuro`, en
`tests/test_cli.py`.

## W-I — La meta y los techos de un goal tienen rango (F1, lado goals)

**Hoy.** `edita_goal` acepta una meta de 0.2 % o 2000 % y un techo de puja de 4500 MXN (`app/goals_write.py:213-230`
solo exige «mayor que 0»). Lo comparten la pantalla de Ajustes y `orbit goals set`.

**Forma.** Al principio de `_valida_pre_editar` (`app/goals_write.py:195`), solo sobre lo que cambia:
```python
for nombre, valor in cambios.items():
    if valor is None:
        continue
    try:
        if nombre == "target_acos_pct":
            g.exige_banda_meta_manual(valor)                       # 10 a 45 %, bloque X de ARREGLOS 01
        elif nombre in ("bid_floor", "bid_ceiling", "harvest_default_bid"):
            g.exige_bajo_techo("bid", valor, fila["bid_currency"])  # 10 USD / 180 MXN, bloque X
    except ValueError as exc:
        raise GoalInvalido(str(exc)) from None
```
`crea_goal` llama `_valida_pre_editar(fila_nueva, {})` (`:559`): sin cambios no revisa nada, igual que pide X («solo al
construir o cambiar un valor»). Un goal viejo fuera de rango sigue funcionando. El mensaje es el de X, en español llano.
El diseño de X (`docs/evidencia/arreglos-2026-11-09/ads-escritura/opus/DISENO.md:486`) ya pone
`exige_bajo_techo("bid", cambios["bid_ceiling"], ...)` en `_valida_pre_editar`. Si X lo dejó así, este bloque solo
agrega lo que falte (la meta, `bid_floor` y `harvest_default_bid`) dentro del mismo bucle y no repite la llamada.

**Medido.** `en_prod.sh`: `hasattr(goals, "exige_banda_meta_manual")` da `False` hoy. Por eso W-I depende de que el
bloque X de ARREGLOS 01 esté en master; su fila W-I.0 lo comprueba importando los tres símbolos de
`app.optimizer.goals` (hoy da `ImportError`). No usa un `-k` de pruebas de X: X reparte las suyas en tres archivos y
el filtro podría no juntar ninguna aunque X ya esté.

**Prueba.** Dinero: las promovidas de `test_rev_settings.py::test_target_de_goal_fuera_de_banda_se_rechaza[0.2|2000]` y
`test_techo_de_puja_de_goal_sin_tope_se_rechaza`, en `tests/test_goals_write.py`.

## W-J — Pausar un lote pausa toda campaña con id (fábrica F4)

**Hoy.** `_SQL_CAMPANAS_DEL_LOTE` (`tools/fabrica_campanas.py:317-330`) y `_SQL_CAMPANAS_DEL_LOTE_PARA_PAUSA`
(`:331-340`) piden `estado IN ('applied', 'failed')`. Una campaña con id guardado y sin comprobación (`planeado`, tras un
reinicio a media creación) no se pausa, y el lote dice «Pausado».

**Forma: una sola consulta de «campañas del lote».**
```sql
_SQL_CAMPANAS_DEL_LOTE = """
SELECT t.rol, t.external_id, t.goal_id
  FROM (
    SELECT DISTINCT ON (p.rol) p.rol::text AS rol, p.external_id, g.id AS goal_id, p.orden
      FROM fabrica_lote_paso p
      LEFT JOIN campana_grupo cg ON cg.lote = p.lote
      LEFT JOIN campana_grupo_rol r ON r.grupo_id = cg.id AND r.rol = p.rol
      LEFT JOIN ads_optimizer_goal g ON g.ad_entity_id = r.ad_entity_id AND g.scope = 'campaign'
     WHERE p.lote = %s AND p.recurso = 'campaign' AND p.external_id IS NOT NULL
     ORDER BY p.rol, p.orden DESC
  ) t
 ORDER BY t.orden
"""
```
La usan `_desarmar` (`:1839`), `_cambia_estado_lote` (`:1732`) y `reactiva_lote` (`:1804`, que ignora `goal_id`). Se
borran `_SQL_CAMPANAS_DEL_LOTE_PARA_PAUSA` y `_SQL_CAMPANAS_CON_EXTERNAL`. `_SQL_EXTERNALS_CAMPANA_DEL_LOTE` (impulso,
`:237-240`) ya sigue la misma regla y se queda. El texto de `_desarmar` cuando no hay filas (`:1842-1846`) deja de
decir «(applied, o failed con external)».

**La otra forma.** El movimiento 2 completo de `fabrica.md` (una función `fija_estado` para los 5 bucles). Se descarta
aquí: la causa es el filtro, y los 5 bucles tocan la pausa del impulso que ARREGLOS 01 Q y Z acaban de cambiar.

**Prueba.** Dinero: la promovida de `test_fab_desarme.py::test_desarmar_pausa_campana_planeada_con_external`, en
`tests/test_fabrica_campanas.py`. Producción: 1 lote, aplicado; ningún lote está a medias hoy.

## W-K — Las semillas salen solo de ad groups del mismo producto (fábrica F2): fuera de ARREGLOS 04

> Decisión del dueño (2026-11-13): la regla estricta de esta sección NO se hace. La siembra se queda como hoy y el bloque W-K del plan solo agrega un aviso en la revisión del plan, con este texto exacto: «Estas búsquedas vendieron en campañas donde esta publicación se anuncia junto con otros productos. Alguna puede ser de otro producto del mismo grupo, por ejemplo de otra medida. Revisa la lista antes de crear.» Lo que sigue queda como registro.

Esta sección no forma parte del plan: espera decisión del dueño (`DESCARTADO-semillas-estrictas.md`). Se conserva como diseño listo.

**Hoy.** `_SQL_TERMINOS` (`tools/fabrica_campanas.py:127-165`) toma la campaña de cada product ad y de ahí todos sus
ad groups. El NH- hereda las búsquedas que vendió el SET- del mismo ad group.

**Medido.**
```
-- productos con anuncio, con un ad group propio, con semillas hoy y con semillas con la regla estricta
-- ventana [hoy-105, hoy-15) del 12-nov; consulta completa en el anexo B
→ amazon_mx | 249 | 2 | 249 | 1
→ amazon_us | 119 | 0 |  91 | 0
select 'kw '||platform::text, count(*) from keyword_biblioteca where orders >= 1 group by platform → (vacío)
select platform, count(*), count(ad_ids) from ads_product_metric_observation where metric_date >= '2026-10-01' group by 1
→ ad_ids es una lista por (ASIN, SKU, día): no separa ventas por ad group
```

**Forma.** Un ad group es origen solo si todos sus product ads son de productos del plan. Un product ad sin listing o
sin producto lo descalifica (no se sabe de quién es).
```sql
_SQL_TERMINOS = """
WITH ventana AS (
    SELECT %s::date - %s AS desde, %s::date - %s AS hasta
),
productos AS (
    SELECT DISTINCT product_id FROM listing WHERE id = ANY(%s) AND product_id IS NOT NULL
),
origenes AS (
    SELECT pa.parent_id AS id
      FROM ad_entity pa
      LEFT JOIN listing li ON li.id = pa.listing_id
     WHERE pa.kind = 'product_ad' AND pa.platform = %s::platform
     GROUP BY pa.parent_id
    HAVING bool_and(li.product_id IS NOT NULL AND li.product_id IN (SELECT product_id FROM productos))
),
ultimas AS ( ... igual que hoy ... )
SELECT ... igual que hoy ...
"""
```
`_terminos_producto` (`:451-470`) pasa `(hoy, desde, hoy, hasta, listing_ids, platform, platform)`.
`_SQL_TERMINOS_EXACT` sigue saliendo de `_SQL_TERMINOS.replace("ventana", "ventana_cortes")`. El comentario de
`:112-126` dice la regla nueva y cita «NH- y SET- son productos distintos».

**Texto nuevo** (`app/static/js/fabrica.js:341`). Si `semillas.keywords` y `semillas.asins` vienen vacías, cada campaña
exacta, frase, amplia y por producto dice: «0 semillas: ningún ad group anuncia solo estos productos, así que ninguna
búsqueda vendida es suya con certeza. La campaña automática las irá encontrando y Orbit pasará a exacta las que
vendan.» Si hay semillas en otras campañas, se queda «0 semillas para esta campaña.». Ficha
`.cursor/skills/verify-orbit/features/fabrica.md` con el texto nuevo.

**La otra forma.** Repartir por ventas del producto en el ad group. Se descarta: el reporte de productos no separa por
ad group (medido arriba), así que el reparto sería por parecido.

**Prueba.** Dinero: un NH- en un ad group compartido con un SET- no hereda sus búsquedas; un ad group solo del NH- sí
siembra. En `tests/test_fabrica_campanas.py`.

## W-L — La exact sembrada nace negada en frase, amplia y automática (fábrica F3)

**Hoy.** Lo que nace en exacta también nace en frase y amplia sin negative, y la automática solo lleva los negatives de
la biblioteca (`app/fabrica_plan.py:442-494` y `:869-897`). En el lote real: 4 exact, 31 keywords, 4 repetidas en frase y
amplia, 0 negatives.

**Forma.** La regla del harvest (decisión 10): el término pasa a exacta y queda negado en las hermanas. Las hermanas del
harvest son automática, frase, amplia y por producto (`ROLES_DISCOVERY`, `app/apply_harvest.py:142-147`), y el impulso
no niega hermanas (`app/apply_harvest.py:1767-1768`). La fábrica niega solo en frase, amplia y automática: la campaña
por producto no recibe el negative. No hay evidencia de que Amazon acepte un negative keyword en un ad group de
segmentación por producto: el único lote real no creó ningún negative (su `fabrica_lote_paso` no tiene ningún paso
`negative_keyword`; en por producto solo `product_targeting|target|applied|6`), ningún harvest ha negado hermanas
(`select count(*) filter (where external_ids ? 'hermanas'), count(*) from harvest_job` da `0|11`), y en Sponsored Products
un ad group de segmentación por producto se niega con productos, no con palabras. En frase, amplia y automática el
negative keyword sí es la forma normal de Amazon. Si Amazon rechazara ese
paso, el lote quedaría a medias con campañas ya creadas. Una campaña por producto apunta a ASINs, no a búsquedas, así
que dejarla sin el negative cuesta poco. Cuando una sonda de APPLY.md §11d confirme un ack 207 sin error de un
NEGATIVE_EXACT en un ad group de segmentación por producto, se agrega `product_targeting` a la tupla.
```python
# Dónde nace el negative de lo que va a exacta. Sin product_targeting: falta la prueba de que Amazon lo acepta ahí.
ROLES_CON_NEGATIVO_CRUZADO = ("category_phrase", "category_broad", "auto_discovery")

def negativos_cruzados(semillas: Semillas, roles: tuple[str, ...]) -> tuple[str, ...]:
    """Decisión 10 al sembrar: cada exact nace NEGATIVE_EXACT en ROLES_CON_NEGATIVO_CRUZADO del grupo de cinco.
    El grupo de impulso no las lleva, igual que su harvest."""
    return semillas.exact if tuple(roles) == ROLES_ORDEN_CREACION else ()

def _pasos_negativos(rol: str, textos: Iterable[str]) -> list[Paso]:   # el shape de :889-896, NEGATIVE_EXACT

# _semillas_del_rol(plan, rol)
cruzados = negativos_cruzados(plan.semillas, plan.roles) if rol in ROLES_CON_NEGATIVO_CRUZADO else ()
exacta           -> _pasos_keywords(plan, rol)
frase y amplia   -> _pasos_keywords(plan, rol) + _pasos_negativos(rol, cruzados)
por producto     -> targets de hoy (sin negatives)
automática       -> _pasos_negativos(rol, sorted(set(plan.semillas.negativos) | set(cruzados)))
```
Frase y amplia conservan la keyword: «arras» en frase sigue alcanzando «arras de boda», pero la búsqueda exacta
«arras» va a la exacta. Así lo deja un harvest.

**Huella y lote.** Ver la respuesta d. No cambia `plan_v2_como_json`.

**La otra forma.** Quitar de frase y amplia lo que va a exacta. Se descarta: el harvest no lo hace así, y la frase
perdería alcance sobre búsquedas largas.

**Prueba.** Dinero: con exact `("arras",)` en un plan de cinco, frase, amplia y automática traen un paso
`negative_keyword` NEGATIVE_EXACT «arras»; por producto no trae ningún paso `negative_keyword`; en un plan de impulso la
automática no lo trae. En `tests/test_fabrica_plan.py`.

## W-M — Sacar anuncios «sin pedidos» comprueba que no vendió, y candidatos por SKU (fábrica F1 y F5)

**Hoy.** F1: `planea_retiro` (`app/retiro_anuncios.py:245-272`) arma el retiro con cualquier publicación que mande la
página. F5: los candidatos a impulso unen clics solo por ASIN (`app/pantalla_productos.py:219`) y suman los de otro SKU.

**Medido.**
```
select count(*) filter (where li.seller_sku = u.advertised_sku), count(*) filter (where li.seller_sku <> u.advertised_sku), count(*)
  from (select distinct platform, advertised_asin, advertised_sku from ads_product_metric_observation where metric_date >= '2026-09-13') u
  join listing li on li.platform = u.platform and li.external_id = u.advertised_asin
→ 370 | 0 | 370       (unir también por SKU no cambia ningún candidato de hoy)
anuncio_retiro vacía (fabrica.md)
```

**F1, forma.** Una sola regla de «sin pedido»: la CTE de ventas de `pantalla_productos` pasa a una constante que usan
`_SQL_CANDIDATOS`, `_SQL_SIN_PEDIDO` y la consulta nueva. El retiro no confía solo en el libro: si el libro no está al
día no saca nada, y un pedido atribuido por Ads del mismo SKU también cuenta como venta.

Medido el 12-nov (`lee_prod.sh`): en 330 días, 265 de 2,723 ventas del libro tienen `product_id` vacío y no cuentan para
ningún producto; la última corrida ok de `accounting_ledger_events` en `ingest_run` fue el 12-nov 08:15 UTC (corre a
diario).
```python
# app/pantalla_productos.py
_CTE_VENTAS = """ventas AS (
    SELECT product_id, sum(quantity)::bigint AS unidades FROM ledger_event
     WHERE platform = %s AND kind = 'sale' AND event_date >= %s::date - %s GROUP BY 1)"""

DIAS_PEDIDOS_ADS = 60
_SQL_VENDIO_POR_LISTING = f"""
WITH {_CTE_VENTAS},
ads AS (
    SELECT DISTINCT ON (advertised_asin, advertised_sku, metric_date)
           advertised_asin, advertised_sku, purchases_same_sku30d
      FROM ads_product_metric_observation
     WHERE platform = %s AND metric_date >= %s::date - %s
     ORDER BY advertised_asin, advertised_sku, metric_date, observed_at DESC)
SELECT li.id,
       coalesce(v.unidades, 0) > 0
       OR EXISTS (SELECT 1 FROM ads a WHERE a.advertised_asin = li.external_id
                     AND a.advertised_sku = li.seller_sku AND a.purchases_same_sku30d > 0)
  FROM listing li LEFT JOIN ventas v USING (product_id)
 WHERE li.platform = %s AND li.id = ANY(%s) AND li.product_id IS NOT NULL
"""
_SQL_ULTIMO_LIBRO = "SELECT max(finished_at) FROM ingest_run WHERE source = 'accounting_ledger_events' AND ok"

def vendio_por_listing(conn, plataforma: str, listing_ids: Sequence[int], hoy: dt.date) -> dict[int, bool]:
    """Solo publicaciones con producto conocido. True si su producto vendió en DIAS_SIN_PEDIDO días (la misma regla
    de la lista) o su SKU tuvo pedidos atribuidos por Ads en DIAS_PEDIDOS_ADS días."""

def ultimo_libro(conn) -> dt.datetime | None:
    """Cuándo terminó la última carga buena de ventas de la contabilidad."""

# app/retiro_anuncios.py
DIAS_LIBRO_AL_DIA = 2

def planea_retiro(conn, plataforma, *, listing_ids, motivo, conservar_grupo_id, hoy: dt.date | None = None) -> PlanRetiro:
    vendieron: tuple[int, ...] = ()
    if motivo == "sin_pedidos":
        hoy = hoy or hoy_resumen((plataforma,), dt.datetime.now(dt.UTC))
        libro = pp.ultimo_libro(conn)
        if libro is None or libro.date() < hoy - dt.timedelta(days=DIAS_LIBRO_AL_DIA):
            return PlanRetiro(plataforma, motivo, (), _huella_plan(()), (), ventas_al_dia=False, ventas_al_dia_hasta=libro)
        vendio = pp.vendio_por_listing(conn, plataforma, listing_ids, hoy)
        vendieron = tuple(sorted(lid for lid, si in vendio.items() if si))
        listing_ids = tuple(sorted(lid for lid, si in vendio.items() if not si))   # sin producto: fuera, sin mención
    ...  # lo de hoy
    return PlanRetiro(plataforma, motivo, anuncios, _huella_plan(anuncios), vendieron)
```
`PlanRetiro` suma al final `vendieron: tuple[int, ...] = ()`, `ventas_al_dia: bool = True` y
`ventas_al_dia_hasta: dt.datetime | None = None` (la última carga buena cuando el libro está viejo; `None` si nunca
cargó). `como_dict` los manda como `"vendieron"`, `"ventas_al_dia"` y `"ventas_al_dia_hasta"` (ISO o `null`). Las
publicaciones sin producto conocido no salen en el plan ni en `vendieron`: Orbit no sabe de qué producto son, así que no
puede decir si vendieron. La huella no cambia de forma: si un producto vende entre la vista previa y «Sacar», su anuncio
sale del plan, la huella cambia y el servidor responde 409 «El plan cambió» (`app/api_fabrica.py:777-778`). W-M depende
de W-C: con el reloj de las pantallas, la lista y el retiro usan el mismo día.

Textos nuevos (`app/static/js/productos.js`, en `verRetiro`, antes de pintar los anuncios):
- Si `r.data.ventas_al_dia` es falso, lo que ve David en una frase: «No se sacó ningún anuncio: las ventas de la
  contabilidad no se actualizan desde el {fecha}, así que Orbit no puede comprobar que estos productos no vendieron.
  Vuelve a intentarlo cuando se actualicen.» (fecha con `Orbit.fechaHora`; si `ventas_al_dia_hasta` es `null`, «las
  ventas de la contabilidad todavía no se han cargado» en lugar de «no se actualizan desde el {fecha}»).
- Si `r.data.vendieron.length`: «{n} de los productos marcados vendieron en los últimos 11 meses: se quedan con sus
  anuncios.» (con n = 1: «1 de los productos marcados vendió…»). Si además no queda ningún anuncio, sigue «Nada que
  sacar…».
Ficha `.cursor/skills/verify-orbit/features/productos.md` con los dos textos.

**F5, forma.** En `_SQL_CANDIDATOS`, la subconsulta de clics selecciona también `advertised_sku`, y el JOIN queda
`JOIN listing li ON li.platform = %s AND li.external_id = u.advertised_asin AND li.seller_sku = u.advertised_sku`. Es la
regla S5 del motor (`app/productos_io.py:12-13`, `claves_por_producto` en `:503`).

**La otra forma (F1).** Rechazar toda la petición si un producto vendió. Se descarta: David marcó varios a la vez; es
más claro sacar los que sí cumplen y decirle cuáles se quedan.

**Pruebas.** Dinero (llevan a sacar anuncios o a lanzar un impulso): las promovidas de
`test_cat_productos.py::test_cat_retiro_sin_pedidos_no_verifica_que_no_haya_pedidos` (más el pedido por Ads y la
publicación sin producto) y `::test_cat_candidatos_cuentan_clics_de_otro_sku_del_mismo_asin`, y una nueva con el libro
de hace 3 días que no saca nada; en `tests/test_retiro_anuncios.py` y `tests/test_pantalla_productos.py`. Usan Postgres:
su `Acepta` lleva `CI=1` para que no se salten en silencio.

## W-N — El presupuesto diario alto pide confirmación en vez de rechazarse

> Decisión del dueño (2026-11-13): un presupuesto diario alto ya no se rechaza. Orbit avisa y lo aplica solo con una confirmación expresa. El tope de puja (10 USD y 180 MXN) y la banda de la meta manual de ACoS (10 a 45 %) siguen rechazando: no se tocan.

**Hoy** (master `976df4dc`, con el bloque X de ARREGLOS 01 ya integrado). `exige_bajo_techo("presupuesto_diario", …)` (`app/optimizer/goals.py:138-161`) lanza `ValueError` si el monto pasa de una constante medida el 2026-11-13: 53 USD y 10,708 MXN, el doble del presupuesto más alto de ese día (`TECHOS_HUMANOS_POR_MONEDA`, `:132-135`). Se llama en cuatro lugares:

| Camino | Dónde | Qué ve la persona |
| --- | --- | --- |
| Fábrica, pantalla `/campanas/nuevas` | validador de `SolicitudPlan` (`app/api_fabrica.py:240-242`; corre en `/plan`, `/crear` y `/export-semillas`) | 422 «el presupuesto diario de 20000 MXN supera el maximo de 10708 MXN» |
| Fábrica, armado del plan | `_exige_techos_humanos` (`tools/fabrica_campanas.py:577-582`), llamada en `_arma_plan` (`:595` y `:621`). La web pasa por ahí: `app/fabrica_web.py:104` | `Abortar`, salida 2 en la terminal |
| Ajuste de una campaña, pantalla `/donde-poner-el-dinero` | `planea_ajuste` (`app/campana_ajustes.py:152`), que es pura. La re-llaman `app/apply.py:2248` y `:2338` | 422 con el mismo texto, pintado como «Error: … .» (`app/static/js/dinero.js`) |
| Impulso, pantalla de productos sin clics | `_exige_diario_por_campana` (`app/impulso_io.py:308-313`), sobre el diario que el impulso pone a cada campaña. Sale del gasto para concluir de Ajustes: la pantalla no manda monto | el producto cae en `no_lanzables` con «por campana: … supera el maximo …» |

Los tres caminos de pantalla ya son de dos pasos y el segundo queda ligado a la cifra exacta del primero:

- Fábrica: `POST /api/fabrica/plan` y luego `/crear` con `{solicitud, huella, confirmacion: "CREAR 5 CAMPAÑAS"}`. La huella firma el JSON del plan, que lleva `budget` por campaña (`app/fabrica_plan.py:515` y `:719-722`). Si cambió, 409 (`app/fabrica_web.py:697-699`).
- Ajuste: `GET /api/ads-optimizer/campana-ajuste/plan` y luego `POST …/aplicar` con `{…, huella, confirmacion: "APLICAR AJUSTE", presupuesto}`. La huella incluye `despues` (`app/campana_ajustes.py:89-101`). Si cambió, 409 (`app/apply.py:2248-2251`, `app/api_write.py:803-811`).
- Impulso: `POST /api/fabrica/impulso/plan` y luego `/lanzar`. `lanza` vuelve a previsualizar y da 409 si la huella cambió (`app/impulso_io.py:753-768`).

**Medido** (producción, 2026-11-13, campañas ENABLED con presupuesto diario):

| Moneda | Campañas | Mediana | P90 | Más alto | Un cero de más pasa sin aviso con «el doble del más alto» | Y con «el doble del P90» |
| --- | --- | --- | --- | --- | --- | --- |
| MXN | 34 | 123.07 | 312.08 | 5,353.78 | en 33 campañas | en 7, las de 62 MXN o menos |
| USD | 9 | 15.00 | 24.57 | 26.22 | en 0 | en 0 |

La regla del 2026-11-09 (el doble del más alto) casi no protege en pesos: una sola campaña de 5,353.78 pone el umbral en 10,707, y un presupuesto de 300 tecleado como 3,000 pasa. Además el más alto sube con cada ajuste aplicado (`app/apply.py:2149-2151` guarda la lectura nueva al confirmar): un alto ya confirmado deja mudo el aviso para todas las demás campañas. La tabla `ads_campana_config_observation` todavía no guarda ningún cambio de presupuesto, así que no hay historia para medir cada cuánto se sube uno a más del doble.

**Forma.** Un presupuesto es alto cuando es más del doble de su referencia. Nunca se rechaza por eso. La referencia depende de si la campaña ya existe:

- Ajuste de una campaña: el presupuesto diario que esa campaña tiene hoy (el `antes` del plan). No necesita leer nada más.
- Campaña nueva (fábrica, terminal e impulso): el P90 del presupuesto diario de las campañas ENABLED en esa moneda, o sea la cifra que 9 de cada 10 no pasan. Hoy 312.08 MXN y 24.57 USD, así que el aviso salta arriba de 624.16 MXN y de 49.14 USD. Una campaña muy alta no lo mueve, y muchas campañas chicas (las del impulso nacen con uno o dos dólares diarios) tampoco lo hunden, como sí hunden la mediana. Con pocas campañas el P90 es casi el más alto y la regla vuelve a ser la del 2026-11-09.
- Sin referencia (la campaña no tiene presupuesto anterior, o no hay campañas activas en esa moneda): se compara contra el número fijo de la tabla, 53 USD y 10,708 MXN.

Todo lo que hoy se rechaza sigue avisando: el número fijo es el doble del más alto, y ni el doble de una campaña ni el doble del P90 pueden pasarlo mientras el más alto no cambie. Un dedazo es un salto de 10 o de 100 veces. En un ajuste, el doble lo atrapa también cuando la persona quería bajar (campaña de 26.22 USD, quería 12.50 y teclea 125).

```python
# app/optimizer/goals.py, puro
VECES_PRESUPUESTO_ALTO = 2


@dataclass(frozen=True)
class PresupuestoAlto:
    monto: Decimal                      # por campaña y por día
    moneda: str
    referencia: Decimal | None          # None: no hay con qué comparar
    contra: Literal["esta_campana", "tus_campanas"]

    def texto(self) -> str: ...         # las frases exactas de «Textos»


def presupuesto_alto(
    monto: Decimal,
    moneda: str,
    referencia: Decimal | None,
    contra: Literal["esta_campana", "tus_campanas"],
) -> PresupuestoAlto | None:
    techo = TECHOS_HUMANOS_POR_MONEDA.get(moneda)
    if techo is None:
        raise ValueError(f"moneda {moneda!r} sin tope humano: TECHOS_HUMANOS_POR_MONEDA")   # como hoy
    umbral = techo.presupuesto_diario if referencia is None else VECES_PRESUPUESTO_ALTO * referencia
    return PresupuestoAlto(monto, moneda, referencia, contra) if monto > umbral else None
```

`exige_bajo_techo` y `TECHOS_HUMANOS_POR_MONEDA` no cambian. `TechoHumano.presupuesto_diario` es el número fijo del caso sin referencia, y TEMPORADA 01 acota con `exige_bajo_techo("presupuesto_diario", …)` las propuestas que arma Orbit (`docs/evidencia/temporada-01/DISENO.md:1784-1785`): ese tope es para lo que propone la máquina y sigue rechazando. Este bloque solo quita las cuatro llamadas de lo que teclea una persona.

```python
# app/ads/campana_config.py, lectura
def presupuesto_p90(conn, moneda: str) -> Decimal | None:
    """P90 del presupuesto diario de las campañas ENABLED en esa moneda; None si no hay ninguna."""
    # SELECT (percentile_cont(0.9) WITHIN GROUP (ORDER BY c.presupuesto_diario))::numeric(14,2)
    #   FROM v_campana_config_vigente c
    #   JOIN ad_entity e ON e.id = c.ad_entity_id
    #   JOIN ad_entity_state s ON s.ad_entity_id = c.ad_entity_id
    #  WHERE e.kind = 'campaign' AND s.status = 'ENABLED'
    #    AND c.presupuesto_diario IS NOT NULL AND c.presupuesto_moneda::text = %s
```

Es la consulta de `docs/evidencia/arreglos-01/X/presupuestos.txt` con el P90 en lugar del máximo y filtrada por moneda. Sin campañas devuelve una fila con `NULL`. Se lee una vez por previa, no una vez por campaña ni por producto. El ajuste no la usa.

**Regla de los dos pasos**, igual en los tres caminos de pantalla:

1. La previa nunca rechaza por presupuesto alto. Devuelve el aviso en un campo propio.
2. El segundo paso vuelve a calcular el aviso. Si hay alguno y el cuerpo no trae `acepto_presupuesto_alto: true`, responde 422 con el texto «Falta confirmar…» y no escribe nada, ni en la base ni en Amazon. Con `true` sigue como hoy.
3. El aviso no entra en ninguna huella. La huella ya liga la cifra; si la cifra cambia hay 409 y previa nueva.
4. La pantalla pinta el aviso como alerta junto a la confirmación y muestra la casilla «Sí, quiero ese presupuesto», sin marcar. Con aviso y la casilla sin marcar, el botón del segundo paso no manda nada: la página pinta «Marca «Sí, quiero ese presupuesto» para continuar». El 422 del servidor queda como defensa, para una pestaña vieja o una carrera.
5. El campo `acepto_presupuesto_alto` se manda solo cuando la casilla está marcada. Los cuerpos de la fábrica rechazan campos de más (`extra="forbid"`, `app/api_fabrica.py:116`): así, en una reversa del servidor con la página nueva abierta, solo fallan las peticiones con presupuesto alto.
6. Cada previa nueva deja la casilla sin marcar, y sin aviso la casilla no se ve.

| Camino | Monto que se juzga | Referencia | Campo de la previa | Cuerpo que gana `acepto_presupuesto_alto: bool = False` |
| --- | --- | --- | --- | --- |
| Fábrica | el `budget` de cada campaña del plan | el P90 | `avisos_presupuesto: list[str]`, una línea por campaña alta, en el orden de `campanas` | `SolicitudCrear` (`app/api_fabrica.py`) |
| Ajuste | `despues.presupuesto_diario`, solo con `clase = presupuesto` | `antes.presupuesto_diario` de esa campaña | `aviso_presupuesto: str \| None` | `CuerpoAplicaAjuste` (`app/api_write.py`) |
| Impulso | el diario que el impulso pone a cada campaña: `(tope.presupuesto_diario / 2)` cuantizado a centavos, la misma cuenta de `_exige_diario_por_campana` y de `app/impulso.py:225` | el P90 | `presupuesto_alto: str \| None` en cada plan, junto a `subfamilia_nueva` y `sin_negativos` | `CuerpoLanzarImpulso` (`app/api_fabrica.py`) |

Dónde se calcula y en qué orden responde el segundo paso:

- **Ajuste.** Todo es puro y vive en `app/campana_ajustes.py`: `planea_ajuste` pierde la línea del tope (`:152`) y `PlanAjuste` gana la propiedad calculada `aviso_presupuesto`, que usa `presupuesto_alto(despues, moneda, antes, "esta_campana")`, sale en `como_dict()` y no entra en `huella()`. Es propiedad y no campo porque `app/apply.py:2398` construye `PlanAjuste(...)` a mano y ese archivo no se toca. Así la previa y el `plan_nuevo` del 409 lo traen sin cambiar las rutas. `app/api_write.py` solo gana el campo del cuerpo y, en `aplicar_ajuste`, el 422 después de `_plan_de` y antes de `apply.aplica_ajuste_campana`. En este camino el 422 va antes que el 409, porque el 409 nace dentro de `aplica_ajuste_campana` (`app/apply.py:2251`). Ese archivo tiene 899 líneas y el tope es 900 (`MAX_LINEAS_MODULO`, `tests/test_architecture.py:46`): antes de agregar nada, `_ajuste_de` (`:687-711`, pura, arma el ajuste desde la petición con clases de `app/campana_ajustes.py`) se muda a ese módulo y las rutas la llaman desde ahí. No se agrega el archivo a `ALLOWLIST_TAMANO`. «Regresar» un ajuste no pasa por el aviso: restaura un valor que ya estuvo puesto.
- **Fábrica.** `fw.previsualizar` lee el P90 con su conexión y devuelve `avisos_presupuesto`. `fw.crear` hoy es `crear(solicitud, huella, confirmacion)` y no recibe conexión: gana `acepto_presupuesto_alto: bool = False`, calcula los avisos con la conexión de lectura que ya abre para planificar (`app/fabrica_web.py:690-697`) y responde primero el 409 de la huella (`:698`) y después el 422, antes del `try` de `:705`. Ahí todavía no hay lote: el lote se inserta dentro de `fc._mutar` o en el `except` de `:710-712`. Ese 422 lleva en `detail`, además de `mensaje` y `lote`, la llave `falta_confirmar: true`. Las líneas del aviso las arma un ayudante puro de `tools/fabrica_campanas.py`, que recibe el plan y el P90; la terminal usa el mismo. `app/api_fabrica.py` está en 891 de 900 líneas: lo que no sea el campo del cuerpo y pasar el valor vive en `app/fabrica_web.py`.
- **Pantalla de la fábrica.** El aviso va arriba de la revisión del plan, junto a `advertencia_mezcla`, y la casilla junto al botón de crear. `crear` de `app/static/js/fabrica.js` mete la huella en `intentados` antes del `POST` y solo un 401 la libera; cualquier otro error pinta «…no se reintentará la creación» y da el plan por perdido (`:1143`, `:1164-1175`). Por eso, con aviso y la casilla sin marcar, `crear` sale antes de tocar `intentados` (el patrón ya existe en `:1137-1143`), y un 422 con `falta_confirmar` se trata como el 401: libera la huella, pinta solo el mensaje y deja la previa en pie.
- **Terminal** (`tools/fabrica_campanas.py`). Se borra `_exige_techos_humanos`. La corrida de práctica (sin `--acepto-mutacion-real`) imprime cada aviso, sigue imprimiendo «huella del conjunto:» y sale 0. La corrida que crea (con `--acepto-mutacion-real`) entra por `_crear` (`:773-809`): con algún aviso y sin `--acepto-presupuesto-alto` aborta con el aviso y «Repite con --acepto-presupuesto-alto para crearlas así», salida 2. La lista de imports permitidos de la herramienta (`tests/test_architecture.py:676-737`) es positiva: se agregan `app.ads.campana_config`, `app.ads.campana_config.presupuesto_p90` y `app.optimizer.goals.presupuesto_alto`, y se quita `exige_bajo_techo` (`:719`) si la herramienta ya no lo importa.
- **Impulso.** No se agrega nada a `PlanImpulso` (`app/impulso.py:74`) ni a su `como_json()`, que es lo que firma la huella; `app/impulso.py` no cambia. `previsualiza` (`app/impulso_io.py:390`) lee el P90 una vez y lo deja en `VistaPreviaImpulso`, que tiene un solo constructor (`:451`) y cuya huella solo firma los planes. Un ayudante puro de `app/impulso_io.py` devuelve el aviso de cada plan; lo usan `_vista_impulso` (`app/api_fabrica.py:660-670`, donde hoy se arman `subfamilia_nueva` y `sin_negativos`) y `lanza`. Se borran `_exige_diario_por_campana` y el `except` que volvía el producto no lanzable (`app/impulso_io.py:353-356`). `lanza` responde en este orden: 409 de la huella, 422 de la frase de confirmación como hoy, y al final el 422 del presupuesto, antes de crear nada. Ese chequeo va en una función aparte: `lanza` ya está en el límite de complejidad de ruff (C901, 22 de 22).

Arneses de prueba. Las bases de prueba de la fábrica y del impulso no cargan la migración `0061_bids02_campana_config.sql`, que crea `v_campana_config_vigente`: con la consulta nueva dan `UndefinedTable` (probado el 2026-11-13 sobre `db_fabrica`). Se agrega la 0061 a `ORDEN` de `tests/test_fabrica_migracion.py:42-57` y nada más: `escenario` (`tests/test_api_fabrica.py:42`) y `_db_i3` (`tests/test_impulso_io.py:37-54`) arman su base con `db_fabrica`, y la 0061 no se puede aplicar dos veces. Probado: con la 0061 en `ORDEN` pasan las mismas 561 pruebas de los 19 archivos que usan ese arnés. Las pruebas de terminal no usan base: `_ConnFalsa` (`tests/test_fabrica_campanas.py:315-424`) truena con toda consulta que no conoce, así que aprende la del P90 y devuelve la fila `(None,)`. Sin esa línea fallan 30 pruebas con «SQL inesperado». Con esas bases sin campañas activas no hay referencia y vale el número fijo: ningún presupuesto de las pruebas existentes por debajo de 10,708 MXN o 53 USD avisa.

Se borra además, si sigue ahí cuando corra este bloque, la rama de `_RutaFabrica` que deja pasar el texto «supera el maximo» (`app/api_fabrica.py`, cerca de `:97-102`): ya nada en esas rutas produce ese texto. El bloque W-F reescribe esa clase; se cambia por contenido.

**Textos** (exactos). Las cifras van con coma de miles y dos decimales, también el `antes` del ajuste, que la base entrega con cuatro:

- Ajuste: «Vas a poner 125.00 USD diarios. Es más del doble de lo que esta campaña tiene hoy (26.22 USD).»
- Ajuste sin referencia: «Vas a poner 20,000.00 MXN diarios. Esta campaña no tiene un presupuesto anterior para comparar.»
- Campaña nueva: «Vas a poner 1,500.00 MXN diarios. 9 de cada 10 de tus campañas activas tienen 312.08 MXN o menos.»
- Campaña nueva sin referencia: «Vas a poner 20,000.00 MXN diarios. Hoy no tienes campañas activas en MXN para comparar.»
- Fábrica, cada línea de `avisos_presupuesto`: el nombre de la campaña, dos puntos y el texto.
- Impulso, `presupuesto_alto` de cada plan: «Cada campaña de este producto: » y el texto.
- Casilla, en las tres pantallas: «Sí, quiero ese presupuesto».
- Botón con aviso y casilla sin marcar (lo pinta la página, no viaja al servidor): «Marca «Sí, quiero ese presupuesto» para continuar».
- 422 del segundo paso sin confirmación: «Falta confirmar el presupuesto alto: recarga la página, vuelve a ver el plan y marca «Sí, quiero ese presupuesto»». Va sin punto final: `dinero.js` y `productos.js` le agregan uno al pintar un error, y `fabrica.js` lo pinta solo, sin la frase del lote.

Selectores nuevos: el aviso lleva `data-aviso-presupuesto` y la casilla `data-acepto-presupuesto-alto`, en `app/templates/donde_poner_el_dinero.html` con `app/static/js/dinero.js`, en `app/static/js/fabrica.js` (junto a `advertencia_mezcla`) y en `app/static/js/productos.js` (junto a las marcas `data-subfamilia-nueva` y `data-sin-negativos`).

**Límites conocidos.** No hay techo absoluto: es la decisión, con la confirmación cualquier cifra pasa. Subir una campaña en varios pasos de hasta el doble no avisa: cada paso es una decisión con su frase «APLICAR AJUSTE». Un cero de más que caiga dentro de lo normal no se puede distinguir: en una campaña nueva de 62 MXN o menos, o al bajar una campaña a menos de la quinta parte. El P90 sigue a lo que el dueño crea: si una de cada diez campañas ya es alta, el aviso sube con ellas. Si el sync deja de leer una campaña, su último presupuesto sigue contando. La confirmación vale para todo el plan y no para la lista de avisos: si la referencia cambia entre la previa y el segundo paso, una campaña puede pasar a alta sin que su aviso se haya pintado, pero su cifra es la misma que la persona vio en el plan, porque la huella la liga. No se manda la lista de avisos de vuelta al servidor: sería otro 409 por una carrera que no deja pasar ninguna cifra sin revisar.

**La otra forma.** Quitar el tope: se descarta, un cero de más pasaría directo a Amazon. El doble del más alto como única referencia (la regla del 2026-11-09): se descarta con la medición de arriba, una sola campaña alta la deja muda. Cinco veces la mediana: se probó y se descartó; deja pasar cifras que hoy se rechazan (70 USD en una campaña nueva) y diez campañas de impulso de un dólar hunden la mediana hasta que todo avisa. Un tope que el dueño edita en Ajustes: es un paso extra cada vez y otra clave que mantener. Un eco del monto en lugar de una casilla: no hace falta, la huella ya liga la cifra.

**Cruce con TEMPORADA 01, bloque G.** Sus propuestas de presupuesto se aplican por las mismas rutas `campana-ajuste/plan` y `/aplicar` y desde `app/static/js/dinero.js` (`docs/evidencia/temporada-01/DISENO.md:371-373`). Una propuesta de más del doble del presupuesto actual trae `aviso_presupuesto` y, sin `acepto_presupuesto_alto`, `/aplicar` responde 422. El bloque que entre segundo deja las dos cosas juntas: el diálogo «Revisar» de TEMPORADA pinta el aviso y la casilla igual que el formulario del ajuste. La fila W-C de la tabla «Cruces con los otros planes» sigue siendo cierta para W-F; este bloque sí toca `dinero.js`.

**Pruebas.** Dinero: son presupuestos que Amazon puede gastar el mismo día. Las seis pruebas `arreglos01_x_` que hoy afirman el rechazo del presupuesto se reescriben para afirmar lo nuevo y cambian su nombre a lo que ahora afirman, con el prefijo `test_arreglos04_w_n_`; no se borran sin reemplazo: `tests/test_optimizer_goals.py::test_arreglos01_x_planea_ajuste_rechaza_presupuesto_sobre_el_techo`, `tests/test_fabrica_campanas.py::test_arreglos01_x_el_cli_rechaza_un_presupuesto_sobre_el_techo` y `::test_arreglos01_x_el_cli_end_to_end_rechaza_presupuesto_sobre_el_techo`, `tests/test_impulso_io.py::test_arreglos01_x_el_tope_del_diario_derivado_del_impulso` y `::test_arreglos01_x_el_impulso_mide_el_diario_por_campana`, y `tests/test_api_fabrica.py::test_arreglos01_x_preview_rechaza_presupuesto_sobre_el_techo`. Las cuatro `arreglos01_x_` de puja y de banda de meta no cambian. En el impulso, un rol sin puja sugerida toma como puja el diario de su campaña (`app/impulso.py:210-211`) y el producto queda no lanzable por la puja: con `_sugeridor_fijo()` a secas (`tests/test_impulso_io.py:207`) el producto nunca llega a `planes`, ni con 300000 MXN ni con 800 USD. Las pruebas del impulso usan un sugeridor que da puja a todos los roles. `tests/test_ui.py:2381-2383` fija el cuerpo exacto del `POST` de aplicar y su formulario falso (`:2300-2304`) no tiene los nodos nuevos: se actualiza. `docs/evidencia/arreglos-01/X/prueba.py` sigue pasando: `exige_bajo_techo` no cambia. Ningún documento de `docs/` fuera de `docs/evidencia/` describe el rechazo: no hay otro texto que actualizar.

## W-O — Salud explica los motivos que hoy salen sin texto

**Hoy.** Salud traduce cada motivo de `notes.skips.entidad` con `etiqueta(motivo, "motivo_salud")` (`app/api_dashboard.py:1062-1080`, `app/ui_salud.py:43-47`). Un motivo sin entrada sale armado a partir de su nombre interno y deja un aviso en el log (`app/etiquetas.py:973-974`). Cuatro motivos que el ciclo puede contar no tienen texto:

| Motivo | Dónde se cuenta | Qué significa |
| --- | --- | --- |
| `subida_en_espera_de_venta` | `politica.MOTIVO_SUBIDA_EN_ESPERA` (`app/optimizer/politica.py:55`, usado en `:278`), del bloque P de ARREGLOS 01 | Orbit no sube la puja de una keyword cuya pausa está en espera por una venta reciente |
| `venta_reciente_protegida` | `proteccion.MOTIVO_PROTEGIDA` (`app/optimizer/proteccion.py:37`), contado en `app/cycle.py:2099-2111` | la pausa por cero pedidos espera: hay un pedido reciente que todavía no entra a la ventana madura |
| `venta_reciente_indeterminada` | `proteccion.MOTIVO_INDETERMINADA` (`:38`), contado en el mismo lugar | la pausa espera: la lectura de pedidos recientes vino incompleta |
| `pausa_producto_pendiente` | `MOTIVO_PAUSA_PRODUCTO_PENDIENTE` (`app/cycle.py:308`, contado en `:2230`) | no se sube la puja en un grupo cuyo producto tiene una pausa en camino |

**Medido** (2026-11-13). Sobre master `976df4dc`: `etiqueta("subida_en_espera_de_venta", "motivo_salud")` da «Subida en espera de venta», y los otros tres salen igual de armados. De los 28 `MOTIVO_*` de `politica`, 19 tienen texto en `MOTIVO_SALUD`, 8 en `motivo_decision` y falta solo ese. En producción, los 27 motivos que los ciclos contaron en los últimos 30 días tienen texto: estos cuatro todavía no han salido en pantalla.

**Forma.** Cuatro entradas en `MOTIVO_SALUD` (`app/etiquetas.py:110`), junto a `politica.MOTIVO_VENTA_RECIENTE`:

```python
politica.MOTIVO_SUBIDA_EN_ESPERA: "Su pausa está en espera por una venta reciente: Orbit no sube la puja mientras tanto",
proteccion.MOTIVO_PROTEGIDA: "Iba a pausarse, pero tiene un pedido reciente: la pausa espera",
proteccion.MOTIVO_INDETERMINADA: "Iba a pausarse, pero falta el dato de sus pedidos recientes: la pausa espera",
ciclo.MOTIVO_PAUSA_PRODUCTO_PENDIENTE: "La pausa de su producto está en camino: Orbit no sube la puja de lo que va a pausar",
```

`app/etiquetas.py` ya importa `app.cycle` como `ciclo` (`:19`) y `politica` (`:24`); a esa línea se le agrega `proteccion`. El texto llega solo al navegador por `registro_navegador()` y `Orbit.etiqueta` (`app/static/js/formato.js`); no se toca JS.

**Para que no vuelva a pasar.** `app/etiquetas.py` ya truena al importar cuando un vocabulario cerrado crece sin texto (el bucle de `:916-940`). Se agrega ahí el vocabulario de Salud con el dominio `motivo_salud`: las constantes `MOTIVO_*` de texto de `politica` que no están en `MOTIVO_DECISION`, las de `ciclo`, y `proteccion.MOTIVO_PROTEGIDA` y `proteccion.MOTIVO_INDETERMINADA`. Contra `976df4dc` ese vocabulario tiene exactamente cuatro motivos sin texto, los de la tabla. Las otras dos constantes de `proteccion` (`proteccion_consumida` y `sin_venta_reciente`) no se cuentan como motivo de Salud y quedan fuera. Es estructura y no una prueba nueva: el caso no está entre los cuatro de `AGENTS.md`, porque el defecto está desplegado pero todavía no salió en pantalla. Con un motivo nuevo sin texto, la aplicación no importa y ninguna prueba corre.

## Lo medido en producción el 2026-11-12 con los ayudantes

Sonda de código desplegado (`en_prod.sh`, script puro), estado previo de cada bloque:
```
W-A series: True ['/api/dashboard/series/campana', '/api/dashboard/series/plataforma']
W-B v1: True True True
W-C: True False                       (_hoy_utc existe, _ventana_campanas no)
W-D F10: Sin ventas atribuidas frente a la meta de 20%.
W-E F5: hasta 2026-11-09 dias 91
W-E F6: _comparacion params ['conn', 'plataforma', 'dias']
W-F F8: el manejador de RequestValidationError es el de FastAPI (detail en lista)
W-G F7: False True                    (sin aviso agrupado; el texto de práctica dice 48h)
W-H F2: acepta futuro 2026-12-23 04:31:12+00:00
W-I F1: X presente False False
W-J F4: filtro estado en desarmar True True
W-K F2: siembra por campaña True       (W-K fuera del plan: DESCARTADO-semillas-estrictas.md)
W-L F3: False
W-M F1: ['conn', 'plataforma', 'listing_ids', 'motivo', 'conservar_grupo_id'] False
```

### Anexo A. Consulta de F3

```sql
WITH ult AS (
  SELECT DISTINCT ON (o.ad_entity_id, o.metric_date) e.platform, o.metric_date, o.cost, o.ad_revenue
    FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
   WHERE e.kind = 'campaign' AND e.platform IN ('amazon_mx','amazon_us')
     AND o.metric_date BETWEEN date '2026-10-29' AND date '2026-11-11'
   ORDER BY o.ad_entity_id, o.metric_date, o.observed_at DESC),
edad AS (
  SELECT DISTINCT ON (o.ad_entity_id, o.metric_date) e.platform, o.metric_date, o.cost, o.ad_revenue
    FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
   WHERE e.kind = 'campaign' AND e.platform IN ('amazon_mx','amazon_us')
     AND o.metric_date BETWEEN date '2026-10-29' AND date '2026-11-04'
     AND o.observed_at <= now() - interval '7 days'
   ORDER BY o.ad_entity_id, o.metric_date, o.observed_at DESC)
SELECT 'actual_nov5_11', platform, sum(cost), sum(ad_revenue), count(distinct metric_date) FROM ult WHERE metric_date >= date '2026-11-05' GROUP BY platform
UNION ALL SELECT 'anterior_final', platform, sum(cost), sum(ad_revenue), count(distinct metric_date) FROM ult WHERE metric_date <= date '2026-11-04' GROUP BY platform
UNION ALL SELECT 'anterior_misma_edad', platform, sum(cost), sum(ad_revenue), count(distinct metric_date) FROM edad GROUP BY platform
ORDER BY 2, 1
-- amazon_mx: actual 5966.04 | 41337.52 | 7 ; final 5802.44 | 36885.75 | 7 ; misma edad 5802.44 | 34957.49 | 7
-- amazon_us: actual  364.92 |   969.25 | 7 ; final  295.38 |  1295.07 | 7 ; misma edad  299.29 |  1145.67 | 7
```

Esta consulta junta MX y US y corta en `now() - interval '7 days'`: sirve para medir, no para comparar con una respuesta
concreta del Resumen.

#### Comparación después del desplegar (fila W-D.5)

Lee el `comparison` de amazon_mx y suma la misma ventana con el mismo corte que declara la respuesta, solo amazon_mx:

```bash
r=$(curl -fsS "http://10.13.13.1:8010/api/dashboard/resumen?periodo=7&canal=amazon_mx")
read d h c v <<<"$(echo "$r" | python3 -c 'import json,sys;x=json.load(sys.stdin)["canales"]["amazon_mx"]["comparison"];print(x["desde"],x["hasta"],x["observado_hasta"],x["totals"]["ad_revenue"])')"
s=$(echo "SELECT sum(ad_revenue) FROM (SELECT DISTINCT ON (o.ad_entity_id, o.metric_date) o.ad_revenue FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id WHERE e.kind = 'campaign' AND e.platform = 'amazon_mx' AND o.metric_date BETWEEN date '$d' AND date '$h' AND o.observed_at <= timestamptz '$c' ORDER BY o.ad_entity_id, o.metric_date, o.observed_at DESC) t" | bash docs/evidencia/arreglos-01/lee_prod.sh)
printf 'comparison %s\nconsulta %s\n' "$v" "$s" > docs/evidencia/arreglos-04/W-D/comparacion.txt
```

El `Acepta` de W-D.5 compara las dos cifras como decimales y sale 1 si no son iguales.

### Anexo B. Consulta de F2 de fábrica

```sql
WITH pa AS (
  SELECT pa.platform, pa.parent_id AS ag_id, li.product_id
    FROM ad_entity pa LEFT JOIN listing li ON li.id = pa.listing_id
   WHERE pa.kind = 'product_ad' AND pa.platform IN ('amazon_mx','amazon_us')),
ag_exclusivo AS (
  SELECT platform, ag_id, min(product_id) AS product_id FROM pa GROUP BY platform, ag_id
   HAVING count(DISTINCT product_id) = 1 AND bool_and(product_id IS NOT NULL)),
productos AS (SELECT DISTINCT platform, product_id FROM pa WHERE product_id IS NOT NULL),
terminos AS (
  SELECT DISTINCT ON (s.ad_entity_id, s.search_term, s.metric_date) s.platform, s.ad_entity_id, s.search_term, s.orders
    FROM search_term_observation s
   WHERE s.platform IN ('amazon_mx','amazon_us')
     AND s.metric_date >= date '2026-11-12' - 105 AND s.metric_date < date '2026-11-12' - 15
   ORDER BY s.ad_entity_id, s.search_term, s.metric_date, s.observed_at DESC, s.source_report_id DESC NULLS LAST),
semillas_estrictas AS (
  SELECT x.platform, x.product_id, t.search_term FROM ag_exclusivo x JOIN terminos t ON t.ad_entity_id = x.ag_id
   GROUP BY 1, 2, 3 HAVING coalesce(sum(t.orders), 0) >= 1),
camp_de_producto AS (
  SELECT DISTINCT p.platform, p.product_id, ag.parent_id AS camp_id FROM pa p JOIN ad_entity ag ON ag.id = p.ag_id
   WHERE p.product_id IS NOT NULL),
semillas_hoy AS (
  SELECT c.platform, c.product_id, t.search_term FROM camp_de_producto c
    JOIN ad_entity ag ON ag.parent_id = c.camp_id AND ag.kind = 'ad_group' JOIN terminos t ON t.ad_entity_id = ag.id
   GROUP BY 1, 2, 3 HAVING coalesce(sum(t.orders), 0) >= 1)
SELECT p.platform, count(*),
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM ag_exclusivo x WHERE x.platform = p.platform AND x.product_id = p.product_id)),
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM semillas_hoy s WHERE s.platform = p.platform AND s.product_id = p.product_id)),
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM semillas_estrictas s WHERE s.platform = p.platform AND s.product_id = p.product_id))
  FROM productos p GROUP BY p.platform ORDER BY 1
-- amazon_mx|249|2|249|1
-- amazon_us|119|0|91|0
```
