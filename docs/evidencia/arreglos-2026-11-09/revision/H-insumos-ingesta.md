# H · Insumos de dinero e ingesta de datos: resultado

Copia revisada: `orbit07-wt` en master `3c841f4`. Sondas puras en
`scratchpad/orbit07-review/sondas/H/` (sin base, sin red). Comando base de
todas las sondas:

```
cd /private/tmp/claude-501/-Users-dn-dev-goncloud-Orbit/ae5b1bef-ffcc-4133-9d5d-a3f633c9263b/scratchpad/orbit07-wt
PYTHONPATH=$PWD /Users/dn/dev/goncloud-Orbit/.venv/bin/python ../orbit07-review/sondas/H/<sonda>.py
```

Conteo: 0 CRÍTICO, 1 ALTO, 3 MEDIO, 4 sospechas.

## Hallazgos confirmados

### H1. Un cambio de costo que contabilidad corrige el mismo día congela ese costo para siempre y detiene el motor de precios en las tres plataformas, sin aviso
- Severidad: ALTO
- Frente: dinero y motor
- Dónde: `app/costs.py:416-462` (`_plan_sku`: lo publicado es inmutable; si difiere rechaza el SKU), `app/costs.py:527-529` (SKU ausente del origen = skip permanente), `app/costs.py:532-539` (sella `ok=True` con `rows_skipped>0`), `app/costs.py:668` (sale 0), `app/estimacion_insumos.py:459` y `:479` y `:523-525` (la validación exige `rows_skipped = 0`; sin ella todo producto queda `costo_desactualizado`), `migrations/0073_precio02_meli_miembro_costo.sql:262-269` (el trigger tampoco acepta un escenario `disponible` con skips), `migrations/0018_fabrica_campanas.sql` vista `v_margen_producto` (une `sku_cost` abierta por fecha).
- Escenario: la ingesta de costos corre una vez al día (08:15 UTC) y contabilidad se sincroniza con Odoo cada hora. Día D: a las 06:00 UTC el costo pasa de 100 a 110, Orbit publica la vigencia `[D, abierta) = 110`. A las 12:00 UTC del mismo día el costo pasa a 120. Desde D+1 el origen colapsa el día D a 120 (último valor del día) y choca con la vigencia publicada de 110: `costo distinto para vigencia ya publicada`, SKU rechazado. Esto se repite todos los días para siempre, aunque el costo vuelva a cambiar. Lo mismo pasa si contabilidad borra o corrige una fila histórica, o si un SKU desaparece del origen.
  - Lo que pasa: (a) la vigencia de 110 se queda abierta y `v_margen_producto`, `v_target_margen_plataforma` y `v_margen_familia` la usan para cada venta nueva. Ese costo viejo alimenta la meta de ACoS por producto (`margen_producto`, en live en MX y US según `tools/despliegue/interruptores.env`). Si el costo real subió, el margen sale inflado y el motor puja de más. (b) Cada corrida de costos queda con `rows_skipped >= 1`. La estimación exige una corrida del día con cero skips, así que **todos** los productos de amazon_mx, amazon_us y meli quedan `costo_desactualizado` y el motor de precios deja de evaluar cualquier publicación. Costos sale 0, estimación sale 0, y no hay Telegram en ninguno de los dos caminos (`rg notifica app/precio app/estimacion*.py app/costs.py` no da nada).
  - Lo que debería pasar: un SKU con historia en conflicto no debería apagar los precios de todo el catálogo, y su costo publicado no debería seguir usándose como vigente. Además debería avisar.
- Alcance: se dispara con el cron y los defaults de hoy cuando contabilidad corrige un costo después de las 08:15 UTC del mismo día UTC en que ya cambió, o cuando borra o corrige historia. La evidencia del 23-oct al 5-nov muestra `rows_skipped=0` todos los días, así que **hoy no está disparado**. Pero el plan midió 604 pares (SKU, día) con más de un costo distinto (`plans/orbit-06.md:228-231`), y hubo un cambio masivo de 937 SKUs el 18-ago (`docs/DEPLOY.md:391-394`). La corrección automática de kits (Odoo recalcula después de cambiar los componentes) es exactamente un doble cambio en el mismo día. No encontré este residual aceptado en `plans/` ni en `docs/`.
- Evidencia (sonda ejecutada, `h1_costo_congelado.py`):
  ```
  corrida 2 (D+1): cierres= [] inserciones= [] motivo= costo distinto para vigencia ya publicada
  corrida 3 (D+10): motivo= costo distinto para vigencia ya publicada
  vigencia ABIERTA que sigue usando Orbit: [(datetime.date(2026, 11, 3), Decimal('110.0000'), None)]
  corrida de costos ok con 1 skip aceptable para validar: False
  producto sano sin corrida validadora -> costo_desactualizado
  ```
- Cómo reproducir: `... python ../orbit07-review/sondas/H/h1_costo_congelado.py`

### H2. Si la ingesta de Ads de las 07:10 no termina antes de las 08:40, el ciclo de pujas no corre en todo el día, aunque Telegram dice que "espera"
- Severidad: MEDIO
- Frente: motor
- Dónde: `app/cli.py:111-115` (sin métricas de ayer, sale 0 con "espera al siguiente horario"), `docs/DEPLOY.md:1196-1199` y la salida real del instalador `docs/evidencia/ingesta-01/ejecucion/D.1/instalador-salida-20261021-183311.txt:68-70` (el ciclo solo está a las 08:40 y 08:41), `app/ads/diario.py:124-137` (textos "mientras, el ciclo de pujas espera"), `app/cli_bids.py:125-127` (impulso-vigia: misma guarda, sale 0 en silencio antes de su propio aviso R09-F).
- Escenario: Amazon tarda más de unos 90 min en entregar algún reporte de métricas (el sondeo permite hasta 2 h 30 min, o sea hasta las 09:40). A las 08:40 la guarda ve que no hay corrida ok del día y el ciclo sale 0. La ingesta se salva a las 09:xx o a las 10:40, pero no existe otra línea del ciclo en el crontab ni otro llamador de `corre_ciclo` (solo `app/cli.py:121`). Resultado: ese día no hay bids, pausas, negativos ni cosecha en MX ni en US. Lo mismo pasa con impulso-vigia (08:20). Telegram manda "atraso" y luego "recuperada", y nadie avisa que el ciclo no corrió. El plan `plans/ingesta-01.md:52-60` define reintentos del ciclo a las 10:10, 13:10 y 16:10, y su meta (línea 114) es "cero días con el ciclo saltado todo el día". A.4 quedó marcado DONE sin esas líneas.
  - Lo que debería pasar: el ciclo debería reintentar después de cada reintento de la ingesta, como dice el plan, o avisar que el día se perdió.
- Alcance: crontab de producción de hoy. Antes de INGESTA 01 hubo 5 días lentos en 15 (`docs/evidencia/ingesta-01/diagnostico/README.md`). Con la descarga en paralelo, el 22 al 28-oct salió bien. Basta un día lento de Amazon.
- Evidencia: lectura del camino completo, con `rg -n "corre_ciclo\(" app/ tools/` (solo el CLI y un bench) y las líneas instaladas citadas arriba.
- Cómo reproducir: `grep -n "cycle" docs/evidencia/ingesta-01/ejecucion/D.1/instalador-salida-20261021-183311.txt` contra `sed -n 52,60p plans/ingesta-01.md`.

### H3. Una ingesta SP-API que falla una vez por error del servidor de Amazon, red o contrato no avisa, y el vigilante la da por buena
- Severidad: MEDIO
- Frente: motor
- Dónde: `app/spapi/salud.py:162-173` (fuera de LWA y 429, solo avisa en la segunda fallida seguida), `app/spapi/vigilante.py:52-67` (`ok=false` cuenta como presente, "ya avisó por su camino"), `scripts/spapi-diario.sh:12,28-37` (la repetición no corre después de las 06:15 UTC).
- Escenario: `spapi_pricing amazon_mx` falla con un 503 a las 05:40. Si las 8 ingestas pasan de las 06:15 (día con 429 y esperas), la repetición se salta. `evaluar_alertas` no avisa porque es la primera fallida de la clase "resto". A las 07:30 el vigilante cuenta la corrida como presente y no avisa. El motor de precios de las 13:10 exige la observación de precio de hoy (`app/spapi/precio_mercado.py:370-377`, `metric_date = hoy`), así que esa plataforma se queda sin evaluar ese día. Nadie se entera hasta el "recuperada" del día siguiente. Esto contradice la regla del dueño (una corrida con `ok=false` es un bug) y `docs/DEPLOY.md:1060-1062` ("si una ingesta falla, ella misma ... avisa").
- Alcance: cron de hoy, en días lentos. Si la repetición sí corre y vuelve a fallar, ahí sí avisa (segunda seguida).
- Evidencia (sonda ejecutada, `h2_spapi_primer_fallo_mudo.py`):
  ```
  avisos tras primera fallida 5xx (repeticion saltada por pasar de 06:15): []
  vigilante 07:30, faltantes: []
  ```
- Cómo reproducir: `... python ../orbit07-review/sondas/H/h2_spapi_primer_fallo_mudo.py`

### H4. Si se corrige el producto de una publicación de Amazon (por ejemplo NH- contra SET-), las ventas ya registradas siguen cargándose al producto equivocado para siempre
- Severidad: MEDIO
- Frente: dinero
- Dónde: `app/listings.py:333-350` (la corrección legítima del mapa actualiza `listing.product_id`), `app/ledger.py:376-379` (`product_id` sale del ASIN en la primera ingesta), `app/ledger.py:504-510` (`ON CONFLICT ... DO NOTHING`), `migrations/0001_initial.sql:1554-1556` (trigger `ledger_append_only`: no se puede corregir), `migrations/0018_fabrica_campanas.sql` `v_margen_producto` (agrupa por `ledger_event.product_id`; la ventana arranca fija el 2026-02-20, así que esas ventas nunca salen).
- Escenario: el ASIN B0X estaba mapeado a un producto NH- (16 mm, costo menor) y se corrige a SET- (22 mm, costo mayor). La ingesta de listings cambia el producto del listing, pero cada venta de B0X ya en el ledger conserva el producto NH-. El margen de NH- suma el precio de venta del SET- con el costo del NH- (inflado), y la meta de ACoS por producto (`margen_producto`, live) sube para NH-. Al SET- le faltan esas ventas. La atribución por publicación del motor de precios sí sigue al listing; el margen de Ads no.
- Alcance: solo después de un re-mapeo, y la ingesta de listings del bridge es manual (`docs/DEPLOY.md:398-418`; no está en ningún cron). La primera corrida tuvo 0 re-mapeos. Cuando ocurre, el daño es permanente.
- Evidencia (sonda ejecutada, `h3_remapeo_ledger.py`):
  ```
  product_id en la 1a ingesta (mapa equivocado, p.ej. NH-): 101
  product_id que calcularia hoy (mapa corregido, p.ej. SET-): 202
  mismo source_event_id: True
  SQL usado en la re-ingesta: INSERT INTO ledger_event (...) ON CONFLICT (platform, kind, source_event_id) WHERE source_event_id IS NOT NULL DO NOTHING
  ```
- Cómo reproducir: `... python ../orbit07-review/sondas/H/h3_remapeo_ledger.py`

## Sospechas sin confirmar

1. **El margen que fija la meta de ACoS cuenta el IVA de la venta como ingreso.** `v_target_margen_plataforma` y `v_margen_producto` usan `ledger_event.amount`, que en MX incluye IVA (`plans/orbit-06.md:868`: `item_price/amount = 0.8604 = 1/1.16`), y restan un costo sin IVA. Al revés, la estimación de precios sí quita el IVA (`app/estimacion_venta.py:457-459`). Si la parte del IVA que no retiene Amazon se paga al SAT y no aparece en el ledger, el margen MX sale varios puntos arriba y la meta también (con fracción 0.5). Falta: que contabilidad confirme dónde vive el IVA por pagar, y un SELECT que desglose `fee_type` de cargos y retenciones de una orden MX. Es diseño aprobado por el dueño (`docs/superpowers/specs/2026-09-03-target-margen-plataforma-design.md` §2-3), no un error de código.
2. **El atribuidor de Amazon no usa el ASIN de la venta.** `app/ledger_atribucion.py:160-186` decide por producto y canal de la orden. Pero el ASIN viaja en `ev.payload` (`app/ledger.py:116-117,134`) e identifica la publicación exacta. La evidencia F.1 dice que "el evento no trae ASIN", y eso es falso. Sonda `h4_atribucion_ignora_asin.py` (ejecutada): una venta FBA del ASIN-A se atribuye a la publicación B (`listing_id=2, resuelto_por='canal_orden'`) cuando la A conservó su SKU FBM. Así las ventas de una publicación se cuentan en otra dentro de la señal de "perdiendo ventas" del motor de precios. Falta: un SELECT en el bridge que busque ASIN con dos `seller_sku` de canal distinto dentro de productos con varias publicaciones (71 en MX, 44 en US).
3. **El tipo de cambio no tiene rango razonable.** `app/fx.py:153-176` solo exige que la tasa sea mayor que 0, y `app/estimacion_venta.py:348-357` también. Si contabilidad empezara a guardar `(MXN, USD, 0.058)` con las etiquetas bien puestas, Orbit lo invertiría a `(USD, MXN, 0.058)` y el costo en US saldría unas 300 veces más alto. Falta: revisar `sync_fx_rates.py` de contabilidad y decidir si se fija una banda, por ejemplo de 10 a 30.
4. **El ciclo lee métricas por producto sin exigir la corrida de productos del día.** `plans/ingesta-01.md:93` (A.3) pide que el comando que lea `ads_product_metric_observation` lo exija, y el ciclo lo lee en `app/productos_io.py:100` vía `app/cycle.py:2356`. La guarda (`app/cli.py:113`) solo mira campañas, y el aviso de productos dice "el ciclo de pujas espera". Hoy el impacto es bajo: la meta por producto no usa D-1 y la pausa por producto está en shadow. Falta medirlo cuando la pausa pase a live (la venta reciente de ORBIT 07 M4 lee hasta D-1, aunque la revalidación al liberar mitiga).

## Zonas revisadas sin hallazgos
- Costos (`app/costs.py`): un costo en cero, nulo, negativo, menor a un centavo, con más de 4 decimales o con moneda fuera de MXN/USD se rechaza y se cuenta, nunca se vuelve cero. Leído, y las funciones puras se ejercieron en la sonda H1.
- SET- contra NH-: Orbit no mapea por parecido. Amazon va solo por `amazon_sku_mapping` (un mapeo ambiguo se rechaza, `app/listings.py:170-186,257-292`); MeLi va por SKU exacto o por alias verificado por el administrador (`migrations/0075`). Leído.
- Tipo de cambio: las etiquetas invertidas de la fuente se corrigen sin tocar el número. `fx_resolve` tolera hasta 7 días y sin tasa devuelve cero filas, nunca una constante. La estimación divide el costo en MXN entre la tasa USD→MXN y valida la dirección. Leído.
- Ledger y vistas de margen: signos impuestos por la base, ventas siempre mayores que 0 (no hay división entre cero en el prorrateo), cargos de publicidad fuera, cargo sin tipo, moneda mezclada o cobertura menor al 95 % dan NULL. Leído.
- Estimación de contribución (`app/estimacion_venta.py`): ingreso − costo − fees − logística − ISR, IVA quitado con el divisor de la política, fees negativos rechazados, fee con impuesto bloqueado. Leído.
- Métricas de Ads (`app/ads/reports.py`, `descarga.py`, `diario.py`): solo se agregan filas, nada se pisa. La reingesta de 31 días crea observaciones nuevas y las vistas toman la más reciente (no suma dos veces). El dedupe va por id de reporte. Un reporte vacío no escribe ceros, una fila sin métricas se salta, los duplicados de producto se suman, y un grupo es ok solo si se escribieron todos sus reportes. Leído.
- Guarda del ciclo (`app/metricas_listas.py`) y salud de Ads (`app/ads/salud.py`): sin corrida ok de hoy con D-1 no hay ciclo, y el atraso abre incidente a las 10:30. Leído (el hueco es H2, no la guarda).
- Términos de búsqueda y tráfico del motor de precios: los dos toman la última observación por día antes de sumar (`app/optimizer/windows.py:304-336`, `app/spapi/precio_mercado.py:178-190`). Leído.
- Vigilante SP-API: detecta bien la corrida ausente o colgada; el único hueco es H3. Sonda H2.
- Las ventas de un ASIN que todavía no está en `listing` quedan sin producto para siempre (el ledger no se corrige), pero eso baja la cobertura y el margen se abstiene; no infla nada. Leído.
