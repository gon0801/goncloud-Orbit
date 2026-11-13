# Lector ejecutor: ARREGLOS 03, cabecera, BASE y bloques P (precios)

Leído y corrido el 2026-11-13 (05:00 UTC) sobre `la copia de trabajo del plan` (código = master `a4644aad`), contra `plans/arreglos-03.md` (líneas 1-30 y 196-277) y `docs/evidencia/arreglos-03/DISENO-precios.md`. Producción leída solo con `lee_prod.sh`, `desplegado.sh` y `migracion_aplicada.sh`. No corrí `en_prod.sh`: los guiones de P-D y P-E los corrí en local contra el mismo código.

Resultado: **1 bloqueante, 14 no bloqueantes.**

## Bloqueante

### 1. P-C: la base de las pruebas de `tests/test_precio_corrida.py` no aplica la migración nueva, así que P-C.1 no puede salir en verde

- **Dónde:** plan, filas P-C.1, P-C.2 y P-C.3. Diseño, «P-C», migración.
- **Hallazgo:** `tests/test_precio_corrida.py` arma su base con una lista fija, `ORDEN59` (`:40-53`), que termina en `0059_precio02_repara_retencion_coherente.sql`. `_db()` (`:109-122`) aplica solo esa lista y no lee la carpeta `migrations/`. Las dos pruebas nuevas de P-C.1 viven en ese archivo y necesitan que un `error` pase a `confirmado`, pero sin la migración nueva el trigger de la 0054 lo rechaza. Quien implemente verá un `CheckViolation` que parece decir que el diseño está mal, y por la regla del plan («si el código real contradice el diseño, para el bloque») puede parar P-C y, con él, la cadena P-D, P-E y P-F.
- **Evidencia** (prototipo en una carpeta temporal, con los ayudantes de la prueba y de la reproducción, sobre `ORDEN59`):
  ```
  $ pytest -q -s /private/tmp/.../scratchpad/proto/test_proto_pc.py
  ORDEN59 = 0059_precio02_repara_retencion_coherente.sql
  UPDATE revienta: CheckViolation precio_cambio 2: estado error -> confirmado fuera de la progresión sellada (pendiente -> enviado | error, enviado -> confirmado | no_confirmado; el cierre es por observación del día siguiente, S6).
  ```
  El UPDATE es el que hará `cerrar_por_observacion` según el diseño: `estado='confirmado', confirmado_por='observacion', ack=COALESCE(ack, '{"origen":"observacion"}')`. La única migración que sí corre la versión nueva es la plantilla de `tests/test_despliegue_motor.py:362-368`, que aplica toda la carpeta por glob. Esa no corre las pruebas de precio.
- **Corrección pegable** (al final del texto de la fila P-C.2):
  > La migración nueva se suma al final de `ORDEN59` en `tests/test_precio_corrida.py:40-53`. Esa base no lee la carpeta `migrations/`, y sin la migración el cierre de un `error` revienta con `CheckViolation ... estado error -> confirmado fuera de la progresión sellada`. Si otro archivo de pruebas de precio necesita la transición `error -> confirmado`, su lista (`ORDEN54`, `ORDEN58`, `ORDEN_M3`) también la suma.

## No bloqueantes (por gravedad)

### 2. P-E: el diseño dice que cambian de variante 3 publicaciones, pero cambian 25, y la consulta «después» no puede llegar a 0

- **Dónde:** diseño, «P-E», «Medido» (línea 367) y «Prueba-real en práctica» (línea 407: «la consulta de arriba debe dar `cambia_manda = 0`»). Plan, fila P-E.3 («línea base para ver el efecto»).
- **Hallazgo:**
  - La sexta columna de la consulta compara el costo más envío más alto de todas las variantes contra el más alto de las que tienen existencias. Ese número no depende de cuál variante manda, así que después del arreglo sigue dando 3.
  - Además, la variante que manda cambia en 25 publicaciones, no en 3. En 22 de ellas hay un empate exacto de costo más envío entre la variante agotada y otra con existencias: cambia la variante y se vuelve a sembrar el escenario, pero el precio queda igual. Solo en 1568, 1574 y 1594 baja el precio. Comprobé dos empates (1564 y 1566): costos idénticos (`304.65` y `304.65`; `459.29` y `459.29`), así que tampoco hay salto de costo que dispare el fusible por insumo.
- **Evidencia:**
  ```
  -- consulta del diseño, hoy: 36|30|0|0|0|3|1.04|1.62|1568,1574,1594
  -- st de la misma consulta, 1566:
  1566|NH-NOG-VEN-CEN-DOR|...|543.7900|True|0      <- manda hoy, sin existencias
  1566|NH-NOG-VEN-COR-DOR|...|543.7900|False|2     <- mismo C+L, con existencias
  -- consulta corregida (abajo), hoy:
  25|1564,1566,1567,1568,...,1601
  ```
- **Corrección pegable:**
  - En la línea 367 del diseño: «**25 cambian de variante que manda**; en 22 hay empate exacto de costo más envío (el escenario se vuelve a sembrar con el mismo precio) y en **3** (1568, 1574 y 1594) el precio objetivo baja entre 1.04 % y 1.62 %.»
  - En la línea 407 y en la fila P-E.3, para medir el efecto: la misma consulta hasta `FROM ms)` (sin la coma final), seguida de
    ```sql
    SELECT count(*) FILTER (WHERE manda_sin AND hay_con) manda_sin_existencia,
           string_agg(listing_id::text, ',') FILTER (WHERE manda_sin AND hay_con)
      FROM (SELECT listing_id, bool_or(es_manda AND stock = 0) manda_sin,
                   bool_or(stock IS DISTINCT FROM 0) hay_con FROM st GROUP BY listing_id) x;
    -- hoy: 25|1564,...  ; tras la siguiente estimación con el arreglo: 0
    ```

### 3. P-C.2 y P-D.4: ningún `Acepta` comprueba la sonda ni la reversa de las dos migraciones

- **Dónde:** plan, filas P-C.2 y P-D.4.
- **Hallazgo:** `tests/test_schema.py`, `tests/test_precio_migracion.py`, `tests/test_precio_d0.py` y `tests/test_precio02_base.py` leen archivos o listas fijas viejas (`ORDEN39`, `ORDEN58`, `ORDEN02`, `SQL39`) y no ven la migración nueva. Solo la plantilla de `tests/test_despliegue_motor.py:362` la ejecuta, y solo comprueba que no truene. Una migración que fuera `-- sonda-aplicada: ...` seguida de `SELECT 1;` pasaría los dos `Acepta`, y ninguna reversa se ejecuta nunca. Es importante en P-D.4, que escribe una fila de `config_version` en producción: es uno de los 4 casos con prueba de `AGENTS.md`.
- **Corrección pegable:**
  - Agregar a P-D.4: «Prueba (escribe datos de producción), en `tests/test_precio_migracion.py` con `ORDEN58`: `test_arreglos03_pd_migracion_sin_precio_cap_y_su_reversa`. Inserta una `config_version` con las tres claves, aplica la migración, corre el SELECT de su cabecera `-- sonda-aplicada:` (da `True`) y comprueba que `apply_cap_de_config('precio:meli')` es NULL; aplica la reversa y la sonda da `False`. `Acepta`: `... pytest -q tests/test_precio_migracion.py -k arreglos03_pd_`.»
  - Lo mismo en P-C.2 con `test_arreglos03_pc_migracion_y_su_reversa`: sonda `True`, un `error` pasa a `confirmado`, la reversa deja la sonda en `False`.

### 4. P-B: si falla la apertura del mercado, no sale aviso

- **Dónde:** plan, fila P-B.3. Diseño, «P-B», capa 2: «el CLI, que es el único lugar que ve cualquier caída (conexión, configuración, mercado, base)».
- **Hallazgo:** si `_clientes_precio` levanta (por ejemplo, falta la llave de MeLi), `_abrir_plataforma` (`app/cli_precio.py:47-52`) devuelve `1` y el lazo hace `rc = mercado_ctx; continue` (`:150-152`) sin pasar por ninguno de los tres `except` que P-B.3 manda tocar. Esa plataforma se queda sin precios todo el día y sin aviso, que es justo la falla F2.
- **Corrección pegable** (P-B.3, después de «falla de conexión»): «... y en la rama `if isinstance(mercado_ctx, int)` (`app/cli_precio.py:150-152`), una vez por esa plataforma.»

### 5. P-A.4: la lista de pruebas por borrar mezcla candados con pruebas que solo pasan `sonda=`

- **Dónde:** plan, fila P-A.4.
- **Hallazgo:** `tests/test_pase_meli.py:101` es la prueba del error 401 y solo pasa `sonda=True`: no es un candado. Pasa lo mismo con `tests/test_precio02_mercado_meli.py:143,178,210-222,243,268` y con los dobles de prueba de `tests/test_precio_write.py` (`:2229`, `:2407-2415`, `:2548-2551`, `:2632-2757`), `tests/test_precio_sonda.py:85` y `tests/test_precio02_mercado.py:157`. Al quitar el parámetro, todas revientan con `TypeError`. Con el texto actual, quien implemente puede borrarlas en vez de solo quitarles el argumento.
- **Corrección pegable:** «Se borran solo `tests/test_precio02_mercado_meli.py:82-111` y `:866`, `tests/test_precio_write.py:996`, y `test_sonda_true_solo_en_precio_sonda` y `test_candado_sonda_caza_fuga_sembrada` de `tests/test_arq_precio_m.py` (`:209`, `:218`). En las demás llamadas y dobles de prueba que pasan `sonda=` (incluida `tests/test_pase_meli.py:101`) se quita el argumento y la prueba se queda.»

### 6. P-D.2: las 21 llamadas que ya existen en `tests/test_precio_compuerta.py` rompen con los campos nuevos

- **Dónde:** plan, fila P-D.2.
- **Hallazgo:** `Intencion.en_vivo`, `Anterior.modo`, `es_nueva(..., modo)` y `evaluar(..., cambios_dia)` son obligatorios en el diseño. Hoy hay 21 llamadas a `Intencion(`, `Anterior(`, `es_nueva(` y `evaluar(` en `tests/test_precio_compuerta.py` (`rg -c` da 21) y todas rompen. Además, `es_nueva` es simétrica: el día que se pasa de en vivo a práctica, todo cuenta como nuevo y el fusible puede retener decisiones de práctica y avisar «Entra a /precios para soltarlo». El diseño solo habla del paso de práctica a en vivo.
- **Corrección pegable:** «Las llamadas existentes de `tests/test_precio_compuerta.py` pasan `en_vivo=`, `modo=` y `cambios_dia=` explícitos (sin valor por omisión en el código). Residuo declarado: el día que se baja de en vivo a práctica, las intenciones también cuentan como nuevas para el fusible.»

### 7. P-D: el aviso del tope puede decir «no se aplicó nada» de cambios que ya se aplicaron, y soltar tarde no sirve

- **Dónde:** diseño, «P-D», «Cómo se entera David» y «Dónde se retiene y cómo se suelta».
- **Recorrido con números** (a mano, sobre `evaluar`, `_medir_compuerta`, `_aplicar_pendientes`, `liberaciones.liberar` y `leer_seguridad`):
  - **51 en un universo, una corrida:** `cambios_dia=51` → `Retenida`. Se retienen los 51, sale 1 aviso y el botón suelta `max(corrida_id)`. El repaso siguiente los ve como liberados, `cambios_dia=0` y aplica los 51. Correcto.
  - **30 en fba y 25 en fbm, en la misma corrida:** `cambios_dia=55`. Se retienen los dos universos y salen 2 avisos. Un «Soltar» suelta los dos (misma corrida). Correcto.
  - **50 exactos:** `Abierta`, se aplican los 50. Correcto. **50 a las 13:10 y 1 nuevo a las 15:10:** `cambios_dia=51`, se retiene solo el nuevo. El tope se sostiene.
  - **Día del regreso a en vivo:** cuenta todo lo decidido en vivo ese día. Lo decidido en práctica antes del cambio ya tiene cambio virtual y no sale real. Con lo medido hoy saldrían 6 en US, que no se retienen.
  - **No encontré forma de pasar de 50 sin retención.** Las decisiones sin canal o sin `m_actual` no se miden ni se retienen, pero la base obliga `m_actual` en `subir`/`bajar` y en producción las 2,267 decisiones sin canal son todas `no_evaluado`. Lo único que deja aplicar de más es «Soltar», que es la confirmación del dueño.
  - **Retenido sin aviso:** solo el residuo que el diseño ya declara (el sello `precio_medicion_un_aviso_por_dia` es compartido con el fusible). Además, si Telegram no está configurado, `notifica_precio` devuelve `True` y sella sin mandar nada (`app/notifica.py:1487-1488`); en producción eso no debería pasar.
- **Hallazgos:**
  - Si los 30 de fba se aplicaron a las 13:10 y los 25 de fbm llegan a las 15:10, a las 15:10 fba también queda `Retenida`, porque tiene 30 cambios en vivo no soltados. Su aviso dice «30 de N quieren moverse hoy; no se aplico nada», y es falso.
  - Lo soltado después del último repaso (23:10 UTC) ya no se aplica ese día. Al día siguiente las decisiones son nuevas y vuelven a contar.
- **Corrección pegable** (residuos en el diseño, «P-D»): «Un universo cuyos cambios en vivo ya se aplicaron en una corrida anterior del mismo día también queda con medición `movimiento_masivo` si el tope se pasa después; su aviso dice "no se aplico nada" aunque esos cambios ya salieron. Soltar después del repaso de las 23:10 UTC no aplica nada: al día siguiente se vuelve a decidir y a contar.»

### 8. P-F.2: «deja `fee_details`» se puede leer al revés, y queda un import sin usar

- **Dónde:** plan, fila P-F.2.
- **Hallazgo:** «`_SQL_FEE` (`:370-373`) deja `fee_details`» puede leerse como «lo conserva». El diseño dice «deja de leer». Además, al borrar `_detalle_fee`, `from app.estimacion_venta import DetalleFee` (`app/precio/siembra.py:30`) queda sin uso.
- **Corrección pegable:** «`_SQL_FEE` (`:370-373`) ya no lee `fee_details`; se borra también la importación de `DetalleFee` (`app/precio/siembra.py:30`) si ya nadie la usa.»

### 9. P-D.4: otros documentos todavía nombran la cuota de precios

- **Dónde:** plan, fila P-D.4.
- **Hallazgo:** además de `docs/DATABASE.md:991-994`, `docs/DEPLOY.md:1886-1888` trae un comando de comprobación con `apply_cap_de_config('precio:amazon_mx')`, que después de la migración devuelve NULL, y `docs/CHAT-CONTEXT.md:173` habla de «los caps `precio:*` en 5». En el código, las pantallas y las pruebas nadie lee las claves para decidir. Solo las nombran pruebas de historia de la 0039, que no aplican la migración nueva y siguen verdes.
- **Corrección pegable:** «`docs/DATABASE.md:991-994` y `docs/DEPLOY.md:1886-1888` dejan de nombrar la cuota de precios (el comando de comprobación pierde la línea `apply_cap_de_config('precio:*')`).»

### 10. P-C: la `prueba-real` depende del nombre de una variable

- **Dónde:** plan, bloque P-C, `prueba-real`.
- **Hallazgo:** `desplegado.sh --no vivo_rb` sale en verde con solo renombrar la variable, aunque siga la relectura. El plan sí manda borrar la función `_readback`.
- **Corrección pegable:** `prueba-real: bash docs/evidencia/arreglos-01/migracion_aplicada.sh arreglos03_error_cierra_por_observacion && bash docs/evidencia/arreglos-01/desplegado.sh --no "def _readback" vivo_rb`

### 11. P-E.1: una trampa al sembrar la observación de prueba

- **Dónde:** plan, fila P-E.1.
- **Hallazgo:** `_publicacion` (`tests/test_precio02_escenario_meli.py:108-140`) ya inserta una observación con `variantes='[]'` en `FETCH`. `_SQL_SIN_EXISTENCIA` y `_SQL_PUB` (`app/meli/estimacion.py:67-71`) toman la más reciente.
  - Con el mismo `observed_at`, la inserción revienta por `meli_pub_obs_unica`.
  - Con una hora anterior, se usa la de `[]` y la prueba falla sin motivo real.
  - Con la hora real de hoy (posterior a `NOW`), la observación queda en el futuro de la prueba.
  - Las claves deben ser `v-<sku>`, o la oferta sale `variantes_sin_mapear`.
- **Corrección pegable:** «La observación nueva va con `_observacion(conn, "MLM1", observado=FETCH + timedelta(minutes=5), variantes=json.dumps([{"variante_ext": "v-SKU-...", "stock": 0}, ...]))`: posterior a la de `_publicacion`, anterior a `NOW` y con las `variante_ext` de `_publicacion`.»

### 12. Las migraciones de P-C y P-D no dicen cómo se llama su reversa

- **Dónde:** cabecera del plan, línea 3 («su `_reversa_`»).
- **Hallazgo:** el despliegue exige exactamente `<n>_reversa_arreglos03_<tema>.sql` (`reversa_de`, `tools/despliegue/comun.sh:213`) y aborta si no existe (`:207-208`).
- **Corrección pegable:** «... y su reversa `<n>_reversa_arreglos03_<tema>.sql` (nombre que exige `reversa_de` de `tools/despliegue/comun.sh`) ...»

### 13. Diseño P-C: cita equivocada de dónde se exige la observación del día

- **Dónde:** diseño, «P-C», «Qué pasa los días sin observación».
- **Hallazgo:** cita `app/spapi/precio_mercado.py:551-565`, que es `observado` (el lector del cierre). La regla «sin observación de hoy no se decide» está en `app/spapi/precio_mercado.py:373-383` (`metric_date = hoy`) y en `app/meli/precio_mercado.py:317-325`. Lo que afirma el diseño es cierto. Lo comprobé y el freno de 7 días sí cuenta mientras tanto: `reglas.py:283-300` solo salta los `sin_efecto`, y un `error` con `confirmado_por` NULL no lo es.
- **Corrección pegable:** reemplazar «`app/spapi/precio_mercado.py:551-565`» por «`app/spapi/precio_mercado.py:373-383`; en MeLi, `app/meli/precio_mercado.py:317-325`».

### 14. P-A y P-C no coinciden sobre `insertar_virtual`

- **Dónde:** diseño, «P-A», ejemplo de llamada y firma de `insertar_virtual`.
- **Hallazgo:** el diseño de P-A ya muestra `insertar_virtual(conn, did, lid, platform, decision, corrida_id=corrida_id)` sin `ahora`, pero `ahora` se quita hasta P-C.4. En P-A, `ahora` todavía llena `enviado_at`.
- **Corrección pegable:** en P-A.2: «`insertar_virtual(..., ahora, *, corrida_id)`: P-A conserva `ahora`; lo quita P-C.4.»

### 15. BASE: la `prueba-real` también sale en verde si `arreglos03` solo aparece en un comentario

- **Dónde:** plan, bloque BASE, `prueba-real`.
- **Hallazgo:** `grep -q 'arreglos03'` también acepta un comentario en `comun.sh`. Hoy sale 1, que es lo esperado.
- **Corrección pegable:** `prueba-real: git fetch -q origin && git show origin/master:tools/despliegue/comun.sh | grep -E "grep -E '_\(" | grep -q arreglos03`

## Verificado y correcto

- `uv run --frozen --python 3.12` corre en la copia. Los seis `-k arreglos03_p*_` y `-k arreglos03_base_` salen hoy con «no tests ran» y código 5: pueden fallar.
- La línea base de las suites de `Acepta` de precios (compuerta, corrida, goals, escenario MeLi, contratos, write, mercado, mercado MeLi, notifica) da 555 pasadas, sin saltadas y con Postgres local.
- Las reproducciones `test_f1`, `test_f1b`, `test_f2`, `test_f3`, `test_f4` y la de MeLi `_referencia` fallan hoy con la aserción que describen.
- Los 23 archivos de prueba que nombran los `Acepta` de BASE y P-A a P-F existen.
- BASE.0 sale 1 hoy (archivos sin versionar) y la `prueba-real` de BASE sale 1. `comun.sh:202`, `desplegar.sh:4,50,183` y `test_arreglos01_a_` (`:1119`) están donde dice el plan.
- Las `prueba-real` de P-A, P-B, P-C y la sonda de P-D salen 1 hoy contra producción. Los guiones de P-D y P-E dan `ImportError` en local con el mismo código.
- Las sondas de las migraciones de P-C y P-D dan `False` hoy en producción. `apply_cap_de_config(text)` existe con esa firma.
- Todas las citas `archivo:línea` de P-A a P-F que abrí dicen lo que el plan afirma: `reglas.py`, `tipos.py:131`, `puerto.py`, `write_client.py`, los dos `precio_mercado.py`, `precio_write.py`, `precio_sonda.py`, `corrida.py`, `cambios.py`, `fuentes.py`, `siembra.py`, `goals_write.py`, `estimacion_repository.py:660-664`, `0039:547-548`, `0039:1149-1185`, `0054:681-796` y `0002:455-491`.
- El botón «Soltar» existe (`_precios_seguridad.html:40` → `POST /api/precios/compuerta/{id}/liberar` → `liberaciones.liberar`) y suelta la última corrida retenida del día. El repaso siguiente aplica lo soltado (`v_precio_decision_liberada`, `_retener_por_compuerta`).
- El aviso de compuerta sale por `avisar_precio` → `_avisar_compuerta_precio` aunque la corrida no tenga decisiones nuevas. Se sella una vez por universo, causa y día.
- La retención con causa `movimiento_masivo` exige la medición con esa causa en la misma corrida y canal (trigger `precio_retencion_coherente`). El diseño la escribe antes de retener.
- La transición `error → confirmado` necesita `ack` (`precio_cambio_envio_exige_ack`) y el diseño lo cubre con `COALESCE(ack, ...)`. `app_decide` puede actualizar `ack` y `confirmado_por`.
- Las mediciones del diseño coinciden con producción: config 51 en `shadow`; 53 cambios, 37 reales, 0 en `error`; 0 retenciones; 0 de 10,644 con `corrida_id`; `apply_quota_state` sin motores `precio:*`; máximo en 60 días de 9 (MX) y 4 (US); 64 observaciones de MeLi por día a las 11:30; P-E `36|30|0|0|0|3|1.04|1.62`; P-F `342/111/52` sin nulos y `45|45|0|-1.6|0.0|0.0`.
- P-E respeta el dato faltante: una variante sin observación, sin dato de existencia o con `stock` nulo no se excluye. La observación más vieja que puede usar es la de ayer (unas 25 horas).
- Fuera de `docs/evidencia`, `plans` y la migración 0039, en el código y en las pantallas nadie lee `precio_cap_*`.
- La plantilla de `tests/test_despliegue_motor.py:362` aplica toda la carpeta `migrations/` por glob, así que las migraciones nuevas al menos se ejecutan en una prueba del `Acepta`.
