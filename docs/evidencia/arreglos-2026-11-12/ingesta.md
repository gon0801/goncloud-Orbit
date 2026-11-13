# Revisión de la ingesta y el dinero base

Revisor: ingesta. Código: worktree en master `a4644aad` (idéntico a producción). Fecha: 2026-11-12/13 UTC.
Pruebas de reproducción: `docs/evidencia/arreglos-2026-11-12/pruebas/ingesta/test_rev_ingesta.py` (4 pruebas, las 4 fallan por la falla que describen). Sondas: `docs/evidencia/arreglos-01/lee_prod.sh` (solo lectura).

## Cómo funciona

1. **Amazon Ads (07:10, 10:40 y 13:15 UTC).** `ingest diario` pide de una vez a Amazon los reportes de los últimos 31 días (D-31 a D-1): campañas, keywords, targets, search terms, productos y ubicaciones. Espera hasta 2 h 30 a que estén listos, los descarga y los guarda en tres grupos (métricas, productos, ubicaciones), cada uno en su propia transacción con su `ingest_run`.
2. **Nada se sobrescribe.** Cada corrida agrega una observación nueva por entidad y día. Los lectores toman siempre la más reciente (`v_metric_latest` o un `DISTINCT ON` equivalente). Pedir 31 días cada día es lo que deja madurar las ventas atribuidas, que Amazon sigue moviendo hasta 30 días.
3. **Reintentos.** A las 10:40 y 13:15 `--solo-faltantes` salta un grupo si hoy ya hubo una corrida buena que escribió D-1 para cada plataforma. La misma consulta (`app/metricas_listas.py`) es la guarda que usan el ciclo de bids y los avisos antes de decidir.
4. **Estructura (06:45).** Lista campañas, ad groups, keywords, targets y anuncios, y actualiza su estado. Las métricas de una entidad que la estructura no conoce no se guardan.
5. **SP-API (05:00).** Ocho ingestas en serie (pedidos, listings, inventario y precios, MX y US), cada una con su `ingest_run`, más un vigilante a las 07:30.
6. **Contabilidad (08:15).** `tools/refresh_costos.sh` saca una copia de la base de contabilidad e ingiere costos (vigencias por SKU), tipos de cambio, el ledger (ventas, cargos, retenciones) y el gasto diario de Mercado Libre Ads.
7. **Dinero base.** `v_target_margen_plataforma` calcula el margen por plataforma (90 días maduros, COGS a la fecha, cobertura de 95 % o más, ISR sin pedido prorrateado). Ese margen alimenta la meta de ACoS del optimizador.

## Fallas (con reproducción)

### F1. En invierno, el «ayer» de Amazon US se guarda antes de que termine y nadie lo vuelve a pedir ese día

**Qué te pasa:** desde el cambio de horario (1-nov), las 07:10 UTC son las 23:10 del día anterior en Los Ángeles, que es el reloj de la cuenta US. Orbit guarda como «ayer» un día al que todavía le falta su última hora. Las 10:40 y las 13:15 lo dan por completo y no lo vuelven a pedir. Durante todo ese día, el gasto y los clics de ayer en US salen entre 5 y 11 % abajo en el Resumen, en el vigía de impulsos y en lo que lea D-1. La regla 6 dice que el día en curso se descarta. Al día siguiente se corrige solo, porque se vuelve a pedir dentro de los 31 días. Los bids no lo usan: su ventana termina en D-3.

**Dónde:** `app/ads/diario.py:318-320` (el rango termina en «hoy UTC − 1»). `app/metricas_listas.py:98-152` (la guarda «ayer listo» usa la fecha UTC). `app/ads/reports.py:768` (el filtro de fecha futura usa la fecha UTC). La propia nota de `app/ads/reports.py:39-46` reconoce que Amazon entrega el día en curso. El Resumen sí conoce la zona horaria de cada canal (`app/dashboard_resumen.py:125-130`); la ingesta no.

**Sonda (gasto y clics de campañas US: primera observación contra la siguiente):**

```
metric_date  observado            costo   clics
2026-11-06   2026-11-07 07:14     37.70    94   <- guardado a las 23:14 hora de LA
2026-11-06   2026-11-08 07:11     40.47   102   (+7 %, +8 % clics)
2026-11-07   2026-11-08 07:11     55.60   140
2026-11-07   2026-11-09 07:26     62.33   157   (+12 %, +12 % clics)
2026-11-08   2026-11-09 07:26     65.86   158
2026-11-08   2026-11-10 07:14     68.54   164
2026-11-09   2026-11-10 07:14     47.54   115
2026-11-09   2026-11-11 07:13     50.74   123
```

En octubre (horario de verano, cuando las 07:10 UTC eran las 00:10 en LA), la primera observación salía igual o más alta que la final, porque el gasto madura hacia abajo. Desde noviembre sale más baja. MX no cambia de horario y no lo sufre.

**Prueba:** `test_diario_pide_el_dia_en_curso_de_us_en_invierno`

```
AssertionError: pide hasta 2026-11-11, pero en US son las 23:10 del 2026-11-11: ese dia sigue en curso (ultimo cerrado: 2026-11-10)
```

**Qué debería pasar:** el último día que se pide y que la guarda exige debería ser el último día cerrado en la zona horaria de cada perfil, no la fecha UTC.

### F2. Mercado Libre Ads: el día en curso se guarda como dato

**Qué te pasa:** el cron de las 08:15 UTC (las 02:15 en México) copia de contabilidad la fila de *hoy*, que trae unas dos horas de gasto, y Orbit la guarda como observación vigente. Hasta la corrida del día siguiente, ese día aparece con alrededor del 2 % de su gasto real. El Resumen lo esconde casi siempre porque corta por la fecha local del canal. Pero cuando se mira solo Mercado Libre entre las 06:00 y las 08:15 UTC, «ayer» sale con unos 20 MXN en lugar de unos 800. Además, la frescura de MeLi siempre dice «al día» aunque el último día completo sea ayer.

**Dónde:** `app/meli/ads_source.py:328` (solo rechaza fechas *posteriores* a hoy UTC; el día de hoy pasa). `app/meli/ads_source.py:427`.

**Sonda (`v_metric_latest` de MeLi, consultada el 13-nov a las 03:17 UTC):**

```
2026-11-11   observado 11-12 08:15   827.00 MXN   202 clics
2026-11-12   observado 11-12 08:15    19.08 MXN     6 clics   <- dia en curso al copiarlo
```

Lo mismo pasa todos los días: el 10-nov se guardó primero con 22.97 MXN y su valor final es 834.00.

**Prueba:** `test_meli_acepta_el_dia_en_curso`

```
AssertionError: el dia en curso (2026-11-13) entra como dato: [datetime.date(2026, 11, 12), datetime.date(2026, 11, 13)]
```

**Qué debería pasar:** las filas con fecha igual o posterior al día local en curso de México no se escriben (regla 6).

### F3. Search terms: un total incompleto puede guardarse como completo y disparar un negative

**Qué te pasa:** Amazon a veces manda un mismo término de búsqueda, en el mismo ad group y el mismo día, partido en varias filas, y Orbit las suma. En productos, una fila con datos ilegibles invalida la suma entera, para no publicar un total incompleto como si estuviera completo. En search terms esa protección no existe:

- Si una fila trae los pedidos ilegibles, se descarta solo esa fila y la otra se guarda como si fuera el total. Un término que vendió 2 pedidos puede quedar con 0.
- Si dos filas se complementan y vacían la suma, una tercera fila la vuelve a crear con solo sus números.

Un término con 0 pedidos y suficientes clics es justo el candidato a NEGATIVE_EXACT. Es poco probable (Amazon tendría que mandar filas incompletas), pero toca dinero y la regla ya existe en productos.

**Dónde:** `app/ads/reports.py:976-983` (la fila inválida se salta sin invalidar la clave). `app/ads/reports.py:1031-1047` (borra la clave sin marcarla). Comparar con `_envenenar_clave_producto` y `envenenadas` en `app/ads/reports.py:1133-1157` y `1336-1339`.

**Pruebas:** `test_terminos_fila_invalida_no_envenena_la_clave` y `test_terminos_par_envenenado_se_recrea_con_la_tercera_fila`

```
AssertionError: subtotal parcial publicado como completo: [('arras boda', 30, 0)]
AssertionError: clave envenenada recreada con un subtotal: [('arras boda', 30, 0)]
```

**Qué debería pasar:** lo mismo que en productos: una fila inválida o una suma vaciada invalidan la clave para todo ese reporte.

### F4. Las ventas de Amazon US pierden su importe original en dólares (regla 4)

**Qué te pasa:** contabilidad entrega las ventas y cargos de US ya convertidos a pesos, con su propio tipo de cambio. Orbit guarda solo el monto en pesos. No guarda el importe en dólares ni la fecha del tipo de cambio que se usó. Además, el desglose fiscal que trae el payload en dólares se tira porque la moneda no coincide. La regla 4 pide guardar siempre el original además del convertido, con su fecha de cambio. Sin eso, el margen de US (23.64 %) no se puede conciliar contra la liquidación de Amazon en dólares (regla 10), y usa un tipo de cambio que no es el de Orbit (diferencias de hasta 0.6 % contra `fx_rate` en los mismos días).

**Dónde:** `app/ledger.py:245-272` (`_money_from_payload` devuelve None si la moneda del bloque no es la del monto). `app/ledger.py:357-358` (la moneda es la del monto). Decisión 8 del docstring, `app/ledger.py:21-22`.

**Sondas:**

```
ventas con desglose (item_price):  amazon_mx 1453/1621   amazon_us 0/626   meli 0/1117
pedido 111-0178528-5045877 (12-nov): ledger 2917.74 MXN, SP-API 157.26 USD -> 18.554; fx_rate de Orbit ese dia 18.659
```

**Qué debería pasar:** guardar el monto original y su moneda (USD) junto con el convertido y la fecha de cambio. Si la fuente no los trae, declararlo y no presentar el monto en pesos como si fuera el original.

### F5. El margen de Mercado Libre nunca se calcula: las devoluciones llegan sin etiqueta

**Qué te pasa:** la vista de margen anula el resultado si encuentra un cargo sin clasificar. Esa guarda existe para no restar dos veces la publicidad mal etiquetada. Las devoluciones de Mercado Libre llegan sin categoría y quedan con `fee_type` vacío; las de Amazon llegan con `refund`. Con eso, el margen de plataforma de MeLi queda vacío siempre, y 18 productos de MeLi quedan sin margen por esta razón. Hoy ninguna decisión del optimizador usa el margen de MeLi (el ciclo solo corre Amazon), pero el número que se muestra está vacío por un problema de etiqueta, no por falta de datos.

**Dónde:** `app/ledger.py:220-223` (`refund` toma `fee_category` o NULL). `migrations/0016_target_margen_correcciones.sql:94` y `:131` (`fees_sin_tipo > 0` anula el margen).

**Sondas:**

```
v_target_margen_plataforma: meli  cobertura 1.0000  dias 71  fees_sin_tipo 11  margen NULL
ledger_event meli refund: 60 filas, todas con fee_type NULL
v_margen_producto meli: 102 productos, 18 anulados por fees_sin_tipo
```

**Qué debería pasar:** las devoluciones de MeLi deberían tener su etiqueta (`refund`), igual que las de Amazon. La guarda debería seguir viva solo para cargos de verdad sin clasificar.

## Sospechas

- **IVA en el margen.** La venta del ledger incluye IVA: en MX `item_tax` es exactamente el 16 % de `item_price`, o sea 13.8 % del monto. La vista de margen solo resta el IVA que retiene Amazon (6.9 % del monto, la mitad). Si el negocio paga la otra mitad al SAT, el margen MX (40.33 %) está inflado hasta unos 7 puntos, y la meta de ACoS (0.5 × margen) hasta unos 3.5 puntos, menos lo que se acredite de IVA en comisiones y compras. Depende de cómo declare el contador; no lo pude cerrar con datos. `migrations/0016_target_margen_correcciones.sql:121-125`.
- **Mapa de listings de Amazon congelado.** `ingest listings` (bridge → `listing`) es solo manual. La última corrida fue el 2026-09-02 y ningún cron la corre. Hoy no hace daño: todos los ASIN con gasto de los últimos 30 días tienen listing, y no hay ventas sin producto desde septiembre. Pero el próximo producto nuevo quedará sin COGS ni cobertura hasta que alguien la corra a mano.
- **Cargos con signo «al revés».** El ledger descarta 210 filas por signo (decisión sellada D4). Como cada corrida relee toda la tabla de contabilidad, es el acumulado y no 210 nuevas por día. Probablemente son reembolsos de comisión en devoluciones. Si es así, el costo queda un poco inflado (del lado conservador). No pude ver la SQLite de origen.
- **Tipo de cambio que nunca se corrige.** `app/fx.py` inserta con `ON CONFLICT DO NOTHING`: si contabilidad corrige la tasa de un día, Orbit se queda con la primera.
- **Espera inútil de 2 h 30.** Si un reporte queda FAILED tres veces, no queda ningún intento por sondear, pero la descarga sigue durmiendo hasta agotar el plazo. El aviso de ese fallo llega 2 h 30 tarde. `app/ads/descarga.py:325-334` y `692-698`.
- **Un día sin actividad parece un hueco.** Si una plataforma tiene cero actividad un día (todas las campañas pausadas), Amazon no manda filas. La guarda «ayer listo» nunca se cumple, el ciclo espera todo el día y a las 16:00 avisa «día perdido».
- **Pedidos SP-API.** Si la paginación se corta, la siguiente corrida avanza la ventana sobre el hueco. Está documentado y queda marcado como ok=false, pero repararlo depende de que alguien corra `--desde`. `app/spapi/orders.py:493-503`.
- **Fuera de mi área:** `app/spapi/precio_mercado.py:181-192`. Cuando un ASIN tiene varios SKU, el tráfico del día toma la fila de un solo SKU en vez de sumarlas.
- **Cosmético:** `finished_at` de las corridas es el inicio de la transacción de escritura, no su fin. Por eso la de estructura aparece durando 3 ms.

## Estructura: movimientos que borran complejidad

1. **Una sola regla de «día cerrado» por plataforma.**
   - *Problema:* el Resumen tiene la tabla de zonas horarias por canal (`app/dashboard_resumen.py:125-142`). La ingesta y las guardas usan la fecha UTC en cinco lugares distintos: `diario.py:318`, `metricas_listas.py` (tres consultas), `reports.py:1718`, `placements.py:235` y `meli/ads_source.py:427`. De ahí salen F1 y F2.
   - *Movimiento:* una función `ultimo_dia_cerrado(plataforma, instante)` en un solo módulo, que usen la ingesta, las guardas y el Resumen.
   - *Qué desaparece:* los cinco «hoy UTC» sueltos y la copia de zonas horarias del Resumen.
   - *Riesgo:* la tabla de horarios del cron tiene que asegurar que a las 07:10 UTC el día ya cerró en US. En invierno no cierra hasta las 08:00, así que la guarda daría «espera» y el reintento de las 10:40 lo traería completo.

2. **Un solo planificador y un solo escritor de reportes.**
   - *Problema:* hay cuatro planificadores de filas: `_planea_filas` (~110 líneas), `_planea_filas_terminos` (~170), `_planea_filas_productos` con sus ayudantes (~320) y `_planea_filas_placements` (~80). Repiten los mismos filtros (fecha, futura, rango, numérico, todo vacío), pero con tres políticas distintas ante un negativo (el CHECK aborta, se aborta a mano, o se salta la fila) y dos ante una fila inválida. Además hay cuatro escritores casi iguales: `ingest_metrics`, `ingest_search_terms`, `ingest_productos` e `ingest_placements`, de unas 90 líneas cada uno. `reports.py` tiene 2,078 líneas.
   - *Movimiento:* una especificación por reporte (campos de identidad, columnas de métricas, si suma duplicados) con un planificador y un escritor genéricos, que usen la semántica de productos.
   - *Qué desaparece:* unas 700 líneas, y F3 por construcción.
   - *Riesgo:* cambia el conteo de `skip_reason`, que los avisos leen por texto.

3. **Un solo abridor y sellador de `ingest_run`.**
   - *Problema:* 15 módulos definen su propio `_SQL_ABRIR_RUN`, `_SQL_SELLAR_RUN`, `_sellar_run` y `_formato_skip_reason`: listings, ledger, disponibilidad, costs, estimacion_ingest, reputacion, fx, ads/structure, meli/catalogo, meli/ads_ingest y las cuatro de spapi.
   - *Movimiento:* un `with corrida(conn, source, platform) as run:` que abra la corrida, la selle ok o fallida con el texto limpiado de secretos y deje el registro.
   - *Qué desaparece:* unas 15 × 30 líneas y el `finished_at` engañoso.
   - *Riesgo:* bajo; las pruebas de cada pipeline afirman el sello.

4. **Borrar el segundo camino de métricas.**
   - *Problema:* `ingest metrics` → `reports.main`/`sync_metrics` (`reports.py:1846-2078`, unas 230 líneas) y `placements.sync_placements` (`placements.py:361-394`) ya no están en el crontab (`tools/despliegue/crontab-orbit.txt`). Son un segundo camino para lo mismo (regla 1).
   - *Movimiento:* borrarlos. `ingest diario --fecha/--fecha-fin` ya cubre el relleno manual.
   - *Qué desaparece:* unas 260 líneas, la opción `metrics` del CLI y sus pruebas.
   - *Riesgo:* hay que revisar runbooks que citen `ingest metrics`.

5. **MeLi Ads sin reloj propio escondido en texto.**
   - *Problema:* para copiar unas 830 filas diarias, `meli/ads_ingest.py` (885 líneas) y `ads_source.py` (476) mantienen un modelo temporal propio (as_of, futuro, piso, huella). Lo guardan dentro de `ingest_run.skip_reason` y lo vuelven a leer con expresiones regulares (`_marca_de_as_of`, `_futuro_de_marca`, `_piso_de_marca` y `_huella_de_marca`, `ads_ingest.py:320-350`).
   - *Movimiento:* tratarlo como las demás fuentes. Por cada (campaña, día), comparar con la última observación y agregar una nueva solo si cambió; descartar el día en curso (F2).
   - *Qué desaparece:* la mayor parte de las 885 líneas.
   - *Riesgo:* pierde la defensa contra una copia vieja que llegue después de una nueva. Hoy el cron es uno solo y en serie; si hace falta, basta comparar el as-of dentro del `source_report_id`, que ya lo trae.

6. **Una sola política de reintento para el POST de reportes.**
   - *Problema:* el cliente de Ads se niega a reintentar un POST con error 5xx porque no es idempotente (`app/ads/client.py:375-415`). Acto seguido, `descarga` lo vuelve a pedir con otro nombre (`-r1`/`-r2`, `descarga.py:205-214`). Son dos capas que se contradicen.
   - *Movimiento:* dejar la decisión solo en `descarga`; para reportes, que son de lectura, el cliente puede reintentar.
   - *Qué desaparece:* la excepción de reportes dentro del cliente y el bucle de reemisión.
   - *Riesgo:* bajo; un reporte duplicado solo cuesta cuota.

## Qué comprobé que está bien

- **Sin doble conteo.** Todos los lectores de `ads_metric_observation`, `search_term_observation`, `ads_product_metric_observation` y `ads_placement_observation` colapsan a la observación más reciente: `v_metric_latest`, `windows.py`, `pantalla_*`, `propuestas_campana`, `fabrica_web` y `productos_io`. En producción no hay dos observaciones con el mismo (entidad, día, observado) entre octubre y noviembre, ni en métricas ni en search terms.
- **Sin sobrescritura.** Ningún escritor de métricas hace UPDATE: todos son INSERT con `ON CONFLICT DO NOTHING` por id de reporte.
- **Un reporte vacío o parcial no se guarda como cero.** Si no llega una fila, no se escribe nada y queda la observación anterior. Una fila sin ninguna métrica se salta. Una corrida que falla se sella ok=false y no cuenta para «ayer listo».
- **Maduración cubierta.** Cada corrida exitosa vuelve a pedir los 31 días, así que `--solo-faltantes` no deja días sin madurar. Un grupo solo se escribe cuando llegaron todos sus reportes de todos los perfiles; no hay escritura parcial por perfil.
- **Estructura al día.** Todas las entidades no archivadas se sincronizaron hoy. Los anuncios archivados se marcan como tales, y campañas, keywords y targets archivados llegan con su estado.
- **Cobertura del margen.** MX 99.69 %, US 98.78 %. No hay ventas de Amazon sin producto desde septiembre. Todo evento del ledger trae `source_event_id`, así que la deduplicación por llave es segura. El ISR sin pedido se prorratea por cobertura y los cargos de publicidad quedan fuera.
- **Moneda de US.** El ledger de US está de verdad convertido a pesos: el cociente contra los totales en dólares de SP-API es de 18.55 a 18.70, igual al tipo de cambio. No es el error de 18.66× del sistema viejo.
- **Costo y tipo de cambio.** Un costo de 0 o vacío se rechaza y corta la vigencia. `fx_resolve` no inventa tasa (tope de 7 días, sin respaldo constante).
- **Zona horaria de las vistas.** La base de producción corre en UTC, así que el `CURRENT_DATE` de las vistas de margen no se corre de día.
- **Secretos.** Cada cliente (Ads, SP-API, Telegram, estimación) registra sus secretos para `scrub`, y las URL firmadas se recortan antes de llegar al log.
- **Paginación de pedidos SP-API.** Un token repetido o una página vacía con token detienen la corrida y la marcan.
