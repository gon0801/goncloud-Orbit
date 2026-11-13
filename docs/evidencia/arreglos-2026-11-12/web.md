# Revisión de la API, las pantallas, la configuración y los avisos

Revisión de solo lectura del worktree `wt-orbit-revision` (master a4644aad, igual a producción).
Las reproducciones están en `docs/evidencia/arreglos-2026-11-12/pruebas/web/`. Las 17 pruebas fallan a propósito, cada una
porque su aserción describe la falla:

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q docs/evidencia/arreglos-2026-11-12/pruebas/web/
→ 17 failed
```

## Cómo funciona

- **Una sola app web.** `app/main.py` arranca una app FastAPI. En producción escucha en 127.0.0.1 y en la IP de tu VPN, puerto 8010.
- **Pantallas: cuatro pasos.** Cada pantalla HTML pasa por los mismos cuatro pasos:
  1. Una ruta en `app/ui.py`.
  2. Una función en `app/api_dashboard.py` (que también es la API JSON).
  3. Un lector en `app/dashboard_resumen.py` o en `app/pantalla_*.py`, que consulta la base con el rol de solo lectura `orbit_read`.
  4. Una plantilla Jinja de `app/templates/`, que escapa todo el texto.
- **Presentación.** Las tarjetas, frases y estados («Bien» o «Atención») se arman en `app/ui_*.py`. Las gráficas y los botones los maneja JavaScript propio, servido desde `/static`. La regla de seguridad CSP `default-src 'self'` impide cargar código de fuera.
- **Escrituras protegidas por token.** Todo lo que escribe es un POST que exige el token de escritura en el encabezado `x-orbit-token`. El token se teclea en cada pantalla, se lee de un archivo de secretos y se compara sin filtrar tiempos. Ese POST abre la base con el rol `orbit_admin`. Así funcionan el veto de cortes, el regreso de pujas, los goals, los settings, las familias, los ajustes de campaña y el bloqueo JEV.
- **Settings: cada guardado es una versión nueva.** Al guardar settings se copia la configuración vigente completa, se cambian solo las claves tocadas y se inserta una fila nueva en `config_version`:
  - Un candado de base serializa dos guardados casi simultáneos.
  - `base_config_version_id` rechaza con 409 una pantalla vieja.
  - Antes de guardar, el valor nuevo pasa por los mismos lectores que usa el motor.
- **Goals: se editan en su lugar.** `app/goals_write.edita_goal` es el único camino; lo comparten la pantalla y la terminal.
- **Avisos por Telegram.** Salen de `app/notifica.py`, en texto plano. Cada envío tiene un tiempo límite de 5 a 10 segundos. Si Telegram falla, el error queda anotado en el ciclo y el ciclo sigue.
- **Comandos de terminal.** `app/cli.py` concentra los comandos que se corren por ssh: ciclo, goals, precios, archivar y otros.

## Fallas (con reproducción)

### 1. Las metas de ACoS y el techo de puja se guardan sin ningún rango (dinero)

**Qué te pasa.** En Settings puedes guardar como meta de ACoS 0.2 % o 2000 %. Basta con confundirla con la fracción 0.5 del mismo formulario o teclear un cero de más. El sistema la acepta y el motor la usa:

- **Meta muy baja (0.2 %):** casi todas las keywords con venta quedan «por encima de la meta». El motor les recorta la puja 12 % o 25 % en cada ciclo, hasta el piso.
- **Meta muy alta (2000 %):** las pujas suben hasta el techo.

**Dónde pega primero.** El campo «Target ACoS» del goal de plataforma manda sobre la meta calculada del margen. Hoy los goals de plataforma 4 (MX) y 5 (US) tienen la meta vacía, así que el primer número que escribas ahí gobierna de inmediato.

**El techo de puja tampoco tiene límite.** Un techo de 4500 MXN (en vez de 45.00) se guarda sin aviso.

**Por qué es incoherente.** La meta derivada del margen sí vive en la banda dura [10, 45] (`MARGEN_BANDA_MIN/MAX`, `app/optimizer/goals.py:608`). La manual y la del goal solo exigen «mayor que 0».

**Dónde:**
- `app/api_write.py:211` y `:251`: `gt=0`, sin máximo.
- `app/config_write.py:446-456`.
- `app/goals_write.py:213-222`.
- `app/optimizer/goals.py:219` y `:231`: el lector del motor tampoco tiene tope.
- La meta gobierna los recortes de puja en `app/optimizer/politica.py:345-359`.

**Prueba:** `test_rev_settings.py`
- `test_target_manual_fuera_de_banda_se_rechaza[0.2|2000]`, `test_target_de_goal_fuera_de_banda_se_rechaza[0.2|2000]` y `test_techo_de_puja_de_goal_sin_tope_se_rechaza` → `Failed: DID NOT RAISE`.
- `test_endpoint_settings_guarda_target_2000` (por la misma vía que usa la pantalla) → `se guardo un target de 2000%: {... "label":"settings UI · settings-ui · ... amazon_mx: target manual 20 -> 2000"}`, `assert 200 == 422`.

**Qué debería pasar.**
- La meta manual y la de cualquier goal se rechazan fuera de la misma banda [10, 45] del margen, o de la banda que el dueño decida. Error 422 con un mensaje claro.
- El techo de puja se rechaza por encima de un tope por moneda.

### 2. El ciclo corrido a mano con una fecha futura se salta el veto de 48 h, tus vetos y la madurez de 10 días (dinero)

**Qué te pasa.** `orbit cycle --decided-at <fecha>` acepta cualquier fecha futura sin preguntar. Basta un error de mes para que el ciclo, en vivo:
- libere y aplique en Amazon los cortes que todavía están en su ventana de veto;
- trate como vencidos tus vetos de 30 días;
- vuelva a mover pujas que siguen en su enfriamiento;
- corte con datos de unos 4 días de maduración en lugar de 10.

**Dónde:**
- `app/cli.py:68-86`: `_parse_decided_at` solo exige la zona horaria.
- Esa fecha llega sin tope a `cycle.py:3533`, que hace `libera_vencidos(ahora=decided_at)`.
- También llega a `claves_bloqueadas`, a `en_cooldown` y a `windows.fin_ventana_cortes` (`windows.py:429`).
- El trigger de madurez de `decision` compara contra esa misma fecha, así que no lo detiene.
- Ninguna migración tiene un CHECK contra una fecha futura en `decision`.

**Prueba:** `test_rev_cli.py`
- `test_cycle_rechaza_decided_at_en_el_futuro` → `el CLI paso decided_at=2026-12-23 ... al ciclo (exit 0)`.
- `test_madurez_de_cortes_con_reloj_futuro` → `ventana de cortes termina 2026-11-09: 4 dias de maduracion real`.

**Qué debería pasar.** Rechazar (salida 2) una fecha posterior a ahora, con un margen chico. En modo vivo, pedir la misma ceremonia de confirmación que ya exige `goals set --mode live`.

### 3. El Resumen dice «Atención: las ventas bajaron» comparando días que todavía maduran (decisiones equivocadas)

**Qué te pasa.** En la vista de 7 días, los 7 días actuales todavía están madurando: la venta atribuida sigue llegando hasta 8 días después. En cambio, el periodo anterior ya está maduro. La tarjeta se pone en «Atención» y escribe «Las ventas bajaron X % y el ACoS subió Y pp» aunque el negocio no haya cambiado.

**Evidencia en producción.** Para el 10 al 31 de octubre, la venta de la primera lectura frente a la última creció +32 % en MX (97,860 → 129,214) y +21 % en US. El aviso «cifras inmaduras» sí aparece, pero el estado y la frase se calculan igual.

**Dónde:**
- `app/dashboard_resumen.py:697`: la variación no tiene ningún motivo de madurez.
- `app/ui_metricas.py:194-216` y `:260-283`.

**Prueba:** `test_rev_numeros.py::test_resumen_7d_no_concluye_caida_por_dias_inmaduros` → `Las ventas bajaron -21.30% y el ACoS cambio +6.31 pp vs el periodo anterior.`

**Qué debería pasar.** No dar estado ni frase de variación mientras el periodo actual tenga días inmaduros y el anterior no. Otra opción: comparar los dos periodos a la misma edad.

### 4. «Campañas» y «Resumen» dan gastos distintos para los mismos 30 días, todas las noches (un número, una fuente)

**Qué te pasa.** Entre las 18:00 y las 24:00 hora del centro, el gasto de 30 días no coincide entre las dos pantallas.

| Plataforma | Campañas | Resumen |
|---|---|---|
| MX | 23,113.11 MXN | 23,713.53 MXN |
| US | 1,478.11 USD | 1,538.90 USD |

Datos de la sonda de producción a las 21:16 CDMX.

**Causa.** Hay dos relojes:
- Campañas toma «hoy» en hora UTC (`_hoy_utc`). A esa hora UTC ya es el día siguiente, así que su ventana termina en el día en curso de México, que está vacío, y pierde un día con datos.
- El Resumen usa la zona horaria de cada canal.

**Dónde:**
- `app/api_dashboard.py:238-240` y `:703-705`.
- El mismo reloj UTC aparece en `/series/*` (`:351`, `:392`), en `pantalla_dinero` («días al tope 7d»), en búsquedas (`:2072`) y en productos (`:1993`).

**Prueba:** `test_rev_numeros.py::test_campanas_30d_y_resumen_30d_dan_el_mismo_gasto_a_las_21h_cdmx` → `assert Decimal('290.0000') == Decimal('300.0000')`.

**Qué debería pasar.** Un solo reloj, el de la zona del canal (`hoy_resumen`), para todas las pantallas.

### 5. «Búsquedas que gastan sin vender» te invita a negar a mano con datos de 3 días; el motor exige 10

**Qué te pasa.** La pantalla dice que los negatives los agregas a mano, pero su ventana termina en hoy menos 3 días. Una búsqueda cuyo pedido todavía no se atribuye aparece como «sin venta». En producción, los pedidos por búsqueda de MX crecen +17 % después de la primera lectura (de 54 a 63).

**Además.** «Últimos 90 días» son en realidad 91.

**Dónde:** `app/pantalla_busquedas.py:287`. Usa un 3 fijo en lugar de `windows.DIAS_MADUREZ_CORTES = 10`.

**Prueba:** `test_rev_numeros.py::test_busquedas_sin_venta_respeta_la_madurez_de_cortes` → `assert date(2026, 11, 9) <= date(2026, 11, 12) - 10d`.

**Qué debería pasar.** La ventana termina en hoy menos 10 días y cubre exactamente 90 días.

### 6. El tablero de ruido mete días inmaduros en «Después del encendido» y no lo avisa

**Qué te pasa.** El ACoS de «después» sale inflado y hace ver peor el efecto del motor. En la prueba, el ACoS real es 20 % en todos los días maduros y la pantalla muestra 22.12 %.

**Dónde:**
- `app/pantalla_ruido.py:62-72` y `:248-310`: la consulta no tiene tope en D-9.
- La plantilla `ruido.html:51-63` no lleva ningún aviso de madurez.

**Prueba:** `test_rev_numeros.py::test_ruido_antes_y_despues_no_mete_dias_inmaduros_sin_marca` → `{'gasto': '400', ..., 'acos_pct': '22.12'}`.

**Qué debería pasar.** Cortar «después» en D-9, o marcar los días inmaduros como ya hace el Resumen.

### 7. Los avisos de cortes «de práctica» prometen que se aplican en 48 h, y llega uno por corte

**Qué te pasa.** Un corte de práctica (shadow) nunca se ejecuta, pero el aviso dice «ventana de veto 48h» y da una fecha de vencimiento. Además no hay tope de mensajes: el ciclo 174 (8-nov, US) encoló 31 cortes de práctica, o sea 31 mensajes en una sola corrida. Los avisos de precio sí tienen tope: 5 y uno agrupado.

**Dónde:** `app/notifica.py:251-264` (el texto no mira el modo) y `app/apply_cola.py:676` (un aviso por fila).

**Prueba:** `test_rev_notifica.py::test_aviso_de_corte_en_practica_no_promete_ventana_48h` → `[Orbit] corte encolado — ventana de veto 48h ... modo: shadow`.

**Qué debería pasar.**
- En práctica, decir «práctica: no se aplicará».
- Juntar los cortes de un ciclo en un solo mensaje, como ya hacen los precios.

### 8. Un error al guardar se ve como «Error: [object Object]»

**Qué te pasa.** Basta teclear «20%» en una meta, un tope con decimales o dejar vacío el nombre en «Regresar». La pantalla muestra «Error: [object Object]» y no dice qué está mal. Detrás hay un mensaje de validación en inglés que nunca llega a la pantalla.

**Dónde:**
- `settings.js:125` y `:154`.
- `danadas.js:35` y `:72`.
- `dinero.js:36` y `:139`.
- `familias.js:42` y `:72`.

**Por qué pasa.** Todos pintan `r.data.detail` tal cual. Solo `cortes.js:14-27` tiene el traductor `mensajeError`, que se agregó en CLARIDAD 01 únicamente ahí.

**Prueba:** `test_rev_settings.py::test_error_422_de_settings_es_texto_legible` → `detail no es texto; la pantalla mostraria [object Object]: [{'type': 'decimal_parsing', ... 'msg': 'Input should be a valid decimal', 'input': '20%'}]`.

**Qué debería pasar.** Un mensaje en español que diga qué campo está mal.

### 9. Un tope diario mal escrito se tira en silencio y la pantalla dice «Guardado»

**Qué te pasa.** Si escribes un tope como «1,000», la pantalla lo convierte en un valor vacío. El servidor toma vacío como «no tocar». Si en el mismo guardado cambiaste otra cosa, responde «Guardado» y el tope que escribiste nunca se guardó.

**Dónde:**
- `app/static/js/settings.js:99`: `Number("1,000")` da NaN y el envío lo convierte en `null`.
- `app/config_write.py:490-491`: un tope `null` se toma como «no tocar».

**Prueba:** `test_rev_settings.py`
- `test_cap_ilegible_se_descarta_en_silencio_y_dice_guardado` → `assert 200 != 200`, con la etiqueta «target manual 20 -> 21», sin el tope.
- `test_settings_js_number_de_cap_con_coma_viaja_como_null` → `settings.js manda {"bid":null}`.

**Qué debería pasar.** Rechazar el tope ilegible antes de enviar, o que el servidor no acepte `null` en un tope que se mandó.

### 10. La frase de la tarjeta dice «Sin ventas atribuidas» cuando sí hubo ventas (menor; hoy no se ve)

**Qué te pasa.** Con un «ACoS objetivo del Resumen» configurado, si un día no tiene dato de gasto, la tarjeta dice que no hubo ventas aunque haya 5,000 de venta. Hoy no pasa porque esa meta está vacía en producción (config 51).

**Dónde:** `app/ui_metricas.py:259`.

**Prueba:** `test_rev_numeros.py::test_tarjeta_no_afirma_sin_ventas_cuando_el_acos_falta_por_huecos` → `Sin ventas atribuidas frente a la meta de 20%.`

**Qué debería pasar.** Distinguir «faltan datos» de «no hubo ventas».

## Sospechas

No las logré reproducir.

**Configuración y pantallas**
- **Configuración que cambia a mitad de ciclo.** El ciclo lee la configuración al empezar (`cycle.py:455`), pero `apply.py:361` y `apply_cola.py:301` la vuelven a leer más tarde. Un guardado desde Settings durante un ciclo puede mezclar los topes nuevos con decisiones tomadas con la configuración vieja. Es zona del motor.
- **Inserciones a mano en `config_version`.** Los runbooks insertan con SQL a mano (configs 27-30, 32, 34 y 42 en producción) y esa vía no toma el candado de la app. Un SQL a mano al mismo tiempo que un guardado de pantalla podría perder un cambio. La probabilidad es muy baja.
- **Una falla del servidor se lee como falla de internet.** Si el servidor devuelve un error 500, las pantallas dicen «Error de red al guardar…»: `resp.json()` revienta con una respuesta que no es JSON y cae en el `catch`.
- **Goals sin control de versión.** Dos pestañas que editan el mismo goal se pisan campo por campo, sin aviso.
- **Total en 0 inventado.** `api_dashboard.py:1662` pinta 0 si falta el total de una sección (`{"busquedas": 0, "gasto": "0"}`), contra la regla 3.
- **«Dónde poner el dinero» puede decir «sin target».** Solo muestra meta cuando gana el margen (`pantalla_dinero.py:496`). Si gobierna el setting o un goal, diría «sin target». Hoy los dos ciclos usan margen.
- **Orden por gasto mezcla monedas.** En Campañas sin filtro de plataforma, el orden por gasto pone 100 USD debajo de 500 MXN. No suma, pero confunde.
- **`pantalla_ruido` sin límite.** Lee todo el historial de decisiones y ciclos sin tope de fecha. Hoy son pocos registros, pero crece.

**Avisos**
- **Un aviso de corte puede no salir nunca.** Si el ciclo muere entre guardar la cola y mandar el aviso (`cycle.py:3517` y `:3647`), nadie lo reenvía. El corte se aplica solo a las 48 h y lo único que lo muestra es la pantalla Propuestas.
- **avisos-campana usa la libreta equivocada.** Anota «ya avisé» como corridas falsas de ingesta con resultado correcto: 112 filas en 30 días en producción (`cli_bids.py:16-24` y `:92`). Hoy todos los lectores las filtran, pero es una trampa.

**Comandos de terminal**
- **`productos-modo --pausa live` sin ceremonia.** Enciende pausas reales sin ensayo ni confirmación (`cli_productos.py`), mientras `goals set --mode live` sí la pide.
- **`cycle` acepta abreviaturas.** `--decided` cuenta como `--decided-at` y `--forz` como `--forzar` (`cli.py:587`). `goals` y `archivar` ya las bloquean.
- **Un tope de dinero en float.** `reputacion snapshot --tope-usd` usa float (`cli.py:716`), contra la regla 4. Es de bajo impacto.

## Textos con jerga

**Settings (`app/templates/settings.html`)**
- Títulos y etiquetas en inglés o técnicos:
  - «Caps diarios» (:121)
  - «Target manual (respaldo)» (:44)
  - «Margen (fraccion)» y «Fraccion (0, 1]» (:49, :55)
  - «Confianzas del motor [0.50, 0.99]» (:62)
  - «Motor de bids» y la opción «niveles v3» (:74, :77)
  - «Goal de plataforma #4» (:135)
  - «Enabled», «Floor», «Ceiling» (:142-148, :177-183)
  - «Config vigente: 51» (:13)
- **«Enabled» es un riesgo, no solo jerga.** Desmarcarlo en el goal de plataforma saca a toda la plataforma del motor, sin confirmación. Los campos «Floor» y «Ceiling» de los goals de campaña no dicen en qué moneda van.
- Avisos y errores que ve el dueño:
  - «Marca el ack: el target manual NO gobierna mientras el margen este encendido.» (`settings.js:57`)
  - «… Confirma con ack_respaldo para guardarlo como respaldo» (`config_write.py:452`)
  - «fraccion sin interruptor: manda margen.habilitado» (`config_write.py:431`)
  - «la config vigente es 52, no 51: recarga la pantalla y vuelve a editar» (`config_write.py:561`)
  - «edicion vacia: nada que cambiar» (`config_write.py:498`)

**Avisos de Telegram (`app/notifica.py`)**
- «corte encolado — ventana de veto 48h», «kind: pause (familia entity_cut)», «search_term:», «modo: shadow» y «vence_el: 2026-11-10T17:03:04.123456+00:00» (:256-263). La hora sale en UTC y con microsegundos.
- Digest: «bids fuera de cap hoy», «cortes encolados: live=0 shadow=0 choques=0», «apply_abortado_owner: true» (:460-480).
- «residual tacos campaign: … MXN» (:510); «ALERTA cap agotado / kind: / used: / cap:» (:583-587).
- «Revisar crontab de gon, flock, spapi-diario.log…» (:794-796).
- «… revisar con tools/harvest_excepcion.py --limpiar-terna --grupo N (dry-run primero)» (:981-988).

**Pantallas**
- Campañas: «Cost (30d)», «Revenue Ads (30d)», «Target efectivo», «Procedencia», y «La ventana 30d incluye siempre días inmaduros (D-8..D-1)…» (`campanas.html:9`, `:118-123`).
- Resumen: «Cobertura del periodo: X/Y dias con dato.», «▲ +X% vs ant.», «+X pp», «cobertura incompleta» y «ROAS» sin explicación (`ui_metricas.py:228`, `:294`, `:331`; `resumen.html:154`, `:271-288`).
- «La conversion clic -> orden fue X% (N ordenes / N clics)» (`ui_metricas.py:415`).

## Estructura: movimientos que borran complejidad

### 1. Una sola lectura de «la configuración vigente»

- **Problema:** la consulta `SELECT settings FROM config_version ORDER BY id DESC LIMIT 1` está escrita 23 veces en `app/` (cycle, apply, apply_cola, notifica, 6 pantallas, precios…). Por eso nadie ve que el ciclo la relee a mitad de corrida (sospecha 1).
- **Movimiento:** una sola función `config_vigente(conn)` en `config_write`, que ya tiene `config_vigente_id`.
- **Qué desaparece:** 22 constantes SQL. El que tenga que congelar la configuración por ciclo tendrá un solo punto donde hacerlo.
- **Riesgo:** bajo; es reemplazo mecánico.

### 2. Un solo POST con token y un solo traductor de errores en el JavaScript

- **Problema:** hay 16 `fetch` con `x-orbit-token` repartidos en 7 archivos JS (productos 5, cortes 3, dinero 2, danadas 2, precios 2, settings 1, familias 1). Cada uno repite cómo leer el token, cómo armar el fetch y cómo pintar el error. El traductor de errores solo existe en `cortes.js`, y de ahí sale la falla 8.
- **Movimiento:** una función `Orbit.post(url, cuerpo, form)` en `formato.js`, que ya se carga en todas las páginas, con `mensajeError` adentro. Debe distinguir «falla del servidor» de «falla de red».
- **Qué desaparece:** unos 15 bloques `.then` duplicados, 6 variantes de «Error: …» y la falla 8 completa.
- **Riesgo:** bajo; los textos de error cambian.

### 3. Que cada error de dominio diga su propio código HTTP

- **Problema:** `api_write.py` tiene 7 diccionarios de «excepción → código» y 13 bloques `try/except tuple(_ERRORES_…)` casi iguales. Además, `api_fabrica` y `api_precios_write` repiten el patrón.
- **Movimiento:** una clase base de error de dominio con su `status` y un solo `app.exception_handler` en `main.py`.
- **Qué desaparece:** los 7 diccionarios y unos 13 `try/except`, alrededor de 120 líneas. Los endpoints quedan de 3 líneas.
- **Riesgo:** medio. Un error que hoy da 500 por accidente pasaría a tener su código: hay que listar las excepciones.

### 4. Settings como tabla de claves, no como 19 parámetros opcionales

- **Problema:** `proxima_config` recibe 19 parámetros con «None = no tocar» (`config_write.py:390-411`). Además hay 8 helpers `_aplica_*` con tres formas distintas de borrar una clave: `"off"` (productos y ORBIT 07), `"apagado"` (motor de bids) y un valor `null` (meta del Resumen). Por eso un `null` en un tope se toma como «no tocar» (falla 9). `CuerpoSettings` repite los mismos 14 campos.
- **Movimiento:** una tabla declarativa `{clave, lector del motor, valores permitidos, rango}` y un aplicador genérico con un solo marcador de «quitar».
- **Qué desaparece:** unos 6 helpers (unas 150 líneas) y la mitad de `CuerpoSettings`. El rango que hoy falta para la meta (falla 1) se declara en una línea.
- **Riesgo:** medio. Las etiquetas del historial de versiones cambian de redacción.

### 5. Cuatro nombres, dos capas reales; y borrar lo que nadie usa

- **Problema:** `ui.py` → `api_dashboard.py` → `pantalla_*`/`dashboard_resumen` → plantilla, con la plataforma validada dos veces (`ui._vocab_o_422` y otra vez en `api_dashboard`).
  - `api_dashboard.py` (2,079 líneas) mezcla rutas con SQL propio de campañas, cortes, salud, decisiones y settings.
  - `ui_*` decide negocio («Bien/Atención», «sobre target»), y ahí viven las fallas 3 y 10.
  - Hay código sin uso:
    - las rutas `/series/plataforma` y `/series/campana`, con sus ayudantes (unas 185 líneas de `api_dashboard.py:151-177` y `:248-405`);
    - `graficarSeries` en `dashboard.js` (unas 70 líneas);
    - los filtros `kpis_serie` y `clase_cambio`;
    - una rama de `_frase_tarjeta` que nunca corre (`ui_metricas.py:284-292`).
- **Movimiento:**
  - `api_dashboard` queda solo con rutas.
  - Los lectores pasan a `pantalla_*`.
  - Los veredictos salen al contrato de datos, junto con la madurez.
  - Se borra lo que no tiene uso.
  - Un solo reloj (`hoy_resumen`) reemplaza los 6 `_hoy_utc` y los 4 `now(UTC).date()` sueltos; con eso desaparece la falla 4.
- **Qué desaparece:** más de 250 líneas sin uso, una validación duplicada por pantalla y el segundo reloj.
- **Riesgo:** medio. Conviene hacerlo pantalla por pantalla, y hay que actualizar la ficha `features/resumen.md`, que usa `/series` con curl.

### 6. Sacar los avisos de precio de `notifica.py` y un solo envoltorio de envío

- **Problema:**
  - Las líneas 1013-1934 de `notifica.py`, el 46 % del archivo, son un motor de avisos de precio con 14 consultas propias.
  - Hay 12 funciones `notifica_*` casi idénticas: `try` → `canal_activo()` → `_envia_texto` → `except`, con 29 `except Exception`.
  - El `canal_activo()` sobra, porque `_envia_texto` ya devuelve True con el canal apagado (`notifica.py:211-213`).
- **Movimiento:**
  - Mover lo de precios a `app/precio/avisos.py`.
  - Dejar un solo `_envia(builder, *args)`.
- **Qué desaparece:** unas 1,000 líneas de `notifica.py`, 11 funciones y unas 140 líneas repetidas.
- **Riesgo:** bajo. Es mover e importar; hay que ajustar las pruebas que reemplazan funciones por nombre.

## Qué comprobé que está bien

**Seguridad de las escrituras**
- **Token en todas las escrituras.** Todos los POST de esta área exigen el token, y se revisa antes de abrir la conexión de administrador. El token solo se acepta en el encabezado, nunca en la URL; se compara sin filtrar tiempos, y si falta el archivo responde 503 genérico.
- **Sin envíos cruzados desde otra página (CSRF).** El token va en un encabezado propio y no hay CORS abierto, así que el navegador bloquea un POST desde otro sitio. Las rutas GET no escriben (los `commit` de `api_dashboard.py:2001`, `:2038` y `:2055` corren con el rol de solo lectura).
- **Roles de base.** Las lecturas usan `orbit_read`. Las escrituras usan `orbit_admin`, y el veto lo exige un trigger.

**Escape del texto que viene de fuera**
- Jinja escapa todo por defecto y no hay `|safe` ni `Markup`. En el JavaScript no hay `innerHTML`.
- La CSP `default-src 'self'` bloquea scripts metidos en la página.
- Los datos que pasan del servidor al JavaScript van con `htmlsafe_json_dumps` o `tojson`.
- En producción hay 3 búsquedas con `<`, `>`, `&` o comillas en 30 días, y salen escapadas.
- Telegram recibe texto plano, sin `parse_mode`.

**Settings**
- **Dos guardados casi simultáneos.** `pg_advisory_xact_lock` más `base_config_version_id` dan 409 al segundo en vez de pisar. Los tres caminos que escriben (pantalla, `productos-modo` y `cambia_pausa`) usan conexión sin autocommit, así que el candado sí dura toda la transacción.
- **El motor no ve un guardado a medias.** Cada guardado es un solo INSERT de la configuración completa.
- **Valores que tumbarían el motor.** Una fracción fuera de (0, 1], un tope fuera de 0..`CAP_MAXIMO`, un modo de precio o de motor de bids mal escrito, un NaN o un Infinity: todos se rechazan con 422 antes de guardar, usando los lectores del motor. El modo global del optimizador no se puede editar desde la pantalla.

**Veto contra la liberación automática a las 48 h**
- Las tres transiciones son UPDATE atómicos con el estado esperado en el WHERE: veto `pending_veto|released → vetoed`, liberación `pending_veto → released` y toma `released → applying`. Gane quien gane la carrera, el otro afecta 0 filas.
- Un veto sobre una fila que ya se está aplicando responde 409 «en vuelo».
- El cobro de cupo ocurre después de tomar la fila.

**Números**
- Los totales no mezclan monedas: el Resumen agrupa por moneda y Campañas no tiene total al pie.
- Si un día no tiene dato, el total queda vacío y no se pinta 0.
- Las divisiones están protegidas contra cero.
- Las cifras salen de `v_metric_latest` con `kind='campaign'`, sin doble conteo.
- El target de Campañas es el que congeló el ciclo, y el de Settings usa la cascada del motor.
- Los filtros de la URL responden 422 y no 500.
- No hay consultas por fila en Resumen ni en Campañas.

**Avisos**
- Si Telegram no responde, el ciclo no se cuelga: hay tiempo límite por envío y doble protección; el fallo queda anotado en el ciclo.
- El token del bot está registrado como secreto y se borra de los errores.
- Los resúmenes de precio se marcan como enviados solo después de un envío correcto.

**Comandos de terminal**
- `archivar-anuncios` y `reponer-anuncios` son ensayo por defecto.
- `goals set --mode live` exige ceremonia.
