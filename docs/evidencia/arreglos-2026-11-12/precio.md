# Revisión de precios (repricing) en Amazon y Mercado Libre

Revisión de solo lectura sobre `master a4644aad` (la misma app que corre en producción).
Pruebas de reproducción: `docs/evidencia/arreglos-2026-11-12/pruebas/precio/test_revision_precio.py`. Se corren con:

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q docs/evidencia/arreglos-2026-11-12/pruebas/precio/
```

Las 5 pruebas fallan, cada una por la aserción que describe su falla.

## Cómo funciona

1. Cada publicación con meta («goal») tiene un margen objetivo. Las metas se sembraron el 16 y 17 de octubre con el margen que había ese día, así que el motor solo se mueve cuando cambia algo: costo, comisiones, tipo de cambio o envío. Hoy 142 de 154 publicaciones de Amazon MX están exactamente en su meta.
2. La corrida es a las 13:10 UTC y hay repasos cada 2 horas de 15:10 a 23:10. Amazon MX y US corren uno tras otro en un proceso, y MeLi en otro. Cada plataforma toma su propio candado (flock, reclamo en la base y candado de sesión de Postgres).
3. Antes de decidir, la corrida cierra los cambios de ayer contra la observación del día (`cerrar_por_observacion`) y resuelve los que quedaron a medias (`cerrar_huerfanas`).
4. Después decide una vez por publicación y por día: lee la cuenta del escenario de estimación (precio, ingreso, costo, comisiones, envío, retención) y el precio propio que reportó Amazon o MeLi ese día.
5. Si falta un dato, la decisión queda «no evaluado» y no se inventa nada. Si la observación diverge del escenario más de 1 %, tampoco se decide.
6. Las reglas (`app/precio/reglas.py`) frenan en este orden: «no converge», «perdiendo tras subida», 7 días entre cambios, dirección, precio objetivo P* con hasta dos cotizaciones reales de comisiones, la regla 11 (nunca más del doble ni por debajo del costo), movimiento mínimo y escalón máximo de 10 %.
7. Solo baja precio si la meta ya está superada y las ventas cayeron 40 % o más.
8. La compuerta (`app/precio/compuerta.py`) mide el día por universo (plataforma/canal). Retiene si más de la mitad de las publicaciones (y al menos 10) se mueven por primera vez, o si un insumo brincó en 30 % de ellas.
9. Al aplicar, la corrida vuelve a leer la configuración y la meta antes de cada escritura. En `live` inserta la fila del cambio, la confirma, escribe en la plataforma y sella el acuse. En sombra deja un cambio «virtual» y no escribe nada en la plataforma.
10. El modo efectivo es el menor de tres: global, universo y meta. **Hoy gana el global en `shadow`** (config 51, desde el 9-nov), aunque `meli/meli`, `amazon_mx/fba`, `amazon_mx/fbm` y también `amazon_us/fbm` digan `live`. Todo es virtual: el último cambio real fue el 3-nov en MX y el 31-oct en US.
11. Un universo que no aparece en la configuración queda apagado y deja un error visible. Una clave mal escrita la rechaza el registro de `config_write.py`.
12. La reversa es manual (`tools/precio_reversa.py`): primero un dry-run con huella, luego el go. Se rechaza si el universo está en `live`.

## Fallas (con reproducción)

### F1. El freno de 7 días se salta cuando Amazon tarda en mostrar el precio nuevo, y el registro queda diciendo que no hubo cambio

**Qué te pasa.** El motor manda un precio nuevo y Amazon lo acepta, pero la conexión se corta antes de que llegue la respuesta (tiempo agotado o error de red). Orbit lo registra como «error». Enseguida relee el precio en Amazon y ve el viejo, porque Amazon tarda en reflejar el cambio. Con eso concluye «no tuvo efecto» y no cuenta los 7 días. Al día siguiente vuelve a mover el mismo producto otro escalón de hasta 10 %, aunque el precio ya había cambiado: dos subidas en dos días donde la regla pide 7. Ese cambio además queda como «error» para siempre: el cierre del día siguiente solo revisa los «enviado». El plan de reversa lo marca «sin efecto», así que no lo verías como algo que hay que revertir.

**Evidencia de producción.** La relectura inmediata nunca ha visto el precio nuevo: 37 de 37 escrituras reales tienen relectura igual al precio anterior (MX 24/24, US 10/10, reversas 3/3), y todas se confirmaron después por la observación del día siguiente:

```
platform  es_reversa total rb_antes rb_despues
amazon_us False      10    10       0
amazon_mx True        3     3       0
amazon_mx False      24    24       0
```

La relectura no distingue «entró» de «no entró». Aun así decide si un error cuenta para el freno.

**Dónde.**
- `app/spapi/precio_mercado.py:654-656`: un error de red al escribir devuelve un acuse «no aceptada».
- `app/precio/cambios.py:292-302`: la relectura se hace justo después de escribir y se guarda.
- `app/precio/fuentes.py:397-404`: un «error» con relectura igual al precio anterior se vuelve `sin_efecto`.
- `app/precio/reglas.py:283-291`: el freno de 7 días ignora los `sin_efecto`.
- `app/precio/cambios.py:986`: el cierre solo mira `estado = 'enviado'`.
- `app/precio/cambios.py:721-724`: el plan de reversa marca `sin_efecto`.

**Prueba.**
- `test_f1_patch_que_si_entro_no_frena_7_dias_y_se_vuelve_a_mover`: hace 2 días hubo un cambio 105.46 → 116.00 que quedó en «error» por la red. Hoy Amazon muestra 116.00.
  ```
  AssertionError: el precio ya se movio hace 2 dias y el motor decidio ('subir', None) con 1 PATCH
  ```
- `test_f1b_cambio_que_si_entro_queda_como_error_y_la_reversa_lo_salta`:
  ```
  AssertionError: Amazon muestra 116.00 (= precio_despues) y el registro dice error / plan sin_efecto
  ```

**Qué debería pasar.** Un error de escritura solo debería contar como «sin efecto» cuando la observación del día siguiente lo pruebe, no por una relectura inmediata. Mientras tanto debe respetar los 7 días. El cierre del día siguiente debería revisar también los «error»: si la observación es igual al precio nuevo, «confirmado»; si es igual al anterior, «sin efecto».

### F2. Si una sola publicación provoca un error de base, la plataforma entera se queda sin precios todo el día y Orbit no avisa

**Qué te pasa.** La corrida aísla los errores normales de cada publicación. Pero un error de la base de datos, como un trigger que rechaza la fila de una publicación, se trata como «falla de infraestructura» y tumba la corrida completa. Se pierden las demás decisiones y lo que ya estaba decidido no se aplica. Cada repaso repite lo mismo, porque la publicación rota se vuelve a decidir primero. El aviso por Telegram solo sale de una corrida que termina bien, así que no llega nada. **Ya pasó:** MeLi abortó todas sus corridas del 22 al 25 de octubre (22 corridas, 4 días sin decidir un solo precio) y nadie se enteró hasta el arreglo #504 del 25-oct. Ese arreglo corrigió el caso puntual, pero el patrón sigue igual.

```
fecha       count  (precio_corrida meli estado='abortada')
2026-10-17  1
2026-10-22  6
2026-10-23  6
2026-10-24  6
2026-10-25  3
```

**Dónde.**
- `app/precio/corrida.py:632-643`: todo `CheckViolation` que no sea `goal_reemplazado` se vuelve a lanzar, y cualquier `psycopg.Error` aborta.
- `app/precio/corrida.py:1454-1516`: sin `except`, la fila de la corrida queda `abierta` y el siguiente repaso la pasa a `abortada`.
- `app/precio/corrida.py:1508`: `avisar` solo se llama al final del camino feliz.
- `app/notifica.py:1723-1727`: el resumen diario exige una corrida `cerrada`.

**Prueba.** `test_f2_corrida_que_se_cae_no_avisa_y_tumba_a_todas`: una publicación cuya fila rechaza la base y otra sana; las dos en `live`.

```
AssertionError: publicacion sana decidida=0 patch=0, corrida=abierta, avisos=0
```

**Qué debería pasar.** El rechazo de la fila de una publicación debería quedar como error de esa publicación y la corrida debería seguir con las demás. Y una corrida que se cae debería avisar por Telegram: «la corrida de precios de MeLi falló».

### F3. El fusible no ve la ola de cambios del día en que el global pasa de sombra a live, que es justo el paso siguiente desde el estado actual

**Qué te pasa.** En sombra, cada publicación que quiere moverse «sube» virtualmente una vez por semana, y los días intermedios quedan como «mantener (7 días)». El precio real nunca se movió. El día que el global regresa a `live` con las mismas metas, todas esas publicaciones se mueven de verdad a la vez: en `live` los cambios virtuales no cuentan para el freno de 7 días. El fusible no las cuenta como nuevas, porque la decisión anterior fue «subir» o «mantener por 7 días» con la misma meta, y no retiene nada. Es justo el caso de hoy: metas en `live` y global en `shadow` desde el 9-nov. US ya acumuló «subidas» de sombra el 10 y el 12 de noviembre (publicaciones 1408, 1411, 1415, 1420, 1479 y 1534).

**Dónde.**
- `app/precio/compuerta.py:153-167` (`es_nueva` no sabe si la decisión anterior fue de sombra).
- `app/precio/corrida.py:963-979` (`_SQL_MEDIR_PREVIA` toma la decisión previa sin mirar su modo).
- `app/precio/corrida.py:1113-1127`.

**Prueba.** `test_f3_fusible_ciego_al_pasar_de_sombra_a_live`: 20 de 20 publicaciones con «subir» de sombra ayer.

```
AssertionError: 20 de 20 precios reales se mueven el primer dia en live y el fusible dice Abierta(medidas=20, nuevas=0)
```

**Qué debería pasar.** Una intención cuya decisión anterior fue de sombra, o cuyo cambio anterior fue virtual, debería contar como nueva el primer día en `live`.

### F4. Se pueden mover más de 50 precios en un día sin confirmación, y el «tope de 1,000,000» ni siquiera existe en el código

**Qué te pasa.** `docs/CONTEXTO.md:289-293` dice: «cambios masivos de precio (>50 productos) … requieren confirmación». Lo construido no lo cumple:
- El único freno de volumen es el fusible: más de la mitad del universo y al menos 10.
- Con las medidas reales (MX FBA 125, MX FBM 59, US FBM 97), hoy pasan sin confirmación 62 + 29 = 91 cambios en MX y 48 en US.
- Los cambios que no son «nuevos» (F3) no cuentan para nada.
- Las claves `precio_cap_amazon_mx/_us/_meli = 1000000` no las lee ningún archivo de `app/`. Solo existen en `migrations/0039_precio.sql:1163-1165`, para la cuota de Ads.

Ojo: el plan REPRICING 02 (S.3) aprobó de forma expresa «79 de 158 no retienen». Hay una contradicción entre CONTEXTO y el plan, y el plan declara que CONTEXTO tiene precedencia. Te toca decidir cuál manda.

**Dónde.**
- `app/precio/compuerta.py:210-212` y `261-272`.
- `plans/repricing-02.md:176`.

```
platform  canal  medidas_max  (precio_compuerta_medicion desde 1-nov)
amazon_mx fba    125
amazon_mx fbm     59
amazon_us fbm     97
meli      meli    39
```

**Prueba.** `test_f4_mas_de_50_precios_en_un_dia_sin_confirmacion`:

```
AssertionError: 91 precios de amazon_mx se mueven hoy sin confirmacion: Abierta(medidas=125, nuevas=62) / Abierta(medidas=59, nuevas=29)
```

**Qué debería pasar.** Que se cumpla la regla que elijas. Si manda CONTEXTO: un tope por plataforma y por día (más de 50 movimientos se retienen hasta que confirmes). Si manda el plan: corregir CONTEXTO y borrar las claves `precio_cap_*`.

### F5. La auditoría tiene horas falsas: «enviado» y «releído» son la hora de arranque de la corrida

**Qué te pasa.** El historial de cada cambio dice que se envió y se releyó en el mismo instante en que arrancó la corrida. Cinco cambios distintos del 30-oct comparten la misma hora al microsegundo (13:10:02.703306), 0.01 s *antes* de que la corrida se registrara. Ese historial no sirve para saber cuándo cambió de verdad un precio ni en qué orden. Hoy el efecto es menor, porque las corridas duran segundos. Pero las fechas del freno de 7 días y del cierre del día siguiente salen de esa hora: una corrida que cruce la medianoche quedaría con la fecha del día anterior.

**Dónde.**
- `app/precio/corrida.py:1343` (`aplicar(..., ahora=ahora)` recibe la hora de arranque).
- `app/precio/cambios.py:329` y `432` (`enviado_at = momento`).
- `app/precio/cambios.py:290`, `296` y `301` (`readback_at = momento`).

**Sonda.**

```
id  enviado_at                  readback_at                 iniciada_at                 seg
29  2026-10-30 13:10:02.703306  2026-10-30 13:10:02.703306  2026-10-30 13:10:02.708395  -0.01
30  2026-10-30 13:10:02.703306  ... (idéntico en 31, 32, 33)
```

`readback_at - enviado_at` da 0.0 s en 37 de 37 cambios.

**Qué debería pasar.** `enviado_at` debería ser la hora real de la base al momento de escribir, y `readback_at`, la hora real de la relectura.

## Sospechas (no reproducidas)

- **S1. El PATCH de Amazon reemplaza la oferta completa.** El cuerpo es `op: replace` sobre `/attributes/purchasable_offer` con una sola entrada (`audience: ALL`, `our_price`) (`app/spapi/precio_write.py:80-100`). Según la semántica de JSON Patch de Listings, eso puede borrar el precio de oferta (`discounted_price`), el precio Business/B2B (otra entrada de `audience`) y los mínimos o máximos permitidos. Además, si tienes un precio de oferta activo, el «precio propio» que reporta Pricing puede ser el de oferta, y el motor calcularía sobre esa base. No se puede comprobar sin llamar a Amazon. La sonda A.4 solo validó un cambio de un centavo.
- **S2. MeLi pone un solo precio a variantes de productos distintos.** 30 publicaciones de MeLi con meta mezclan variantes NH (16 mm) y SET (22 mm). Todas reciben el mismo precio, calculado con el costo de la variante más cara, aunque esa variante no tenga existencias. Ejemplo: en MLM2158744542 solo `NH-PLA-M-COR-PLA` tiene stock (6); las variantes SET tienen 0 y aun así fijan el precio. Lo dice la regla del plan («manda la variante que necesita el precio más alto»). No es emparejamiento por parecido, pero conviene que confirmes que es lo que quieres.
- **S3. La compuerta puede soltar sola lo retenido.** Cada repaso vuelve a medir el día completo. Si a las 13:10 un universo quedó retenido y más tarde entran más publicaciones a la medición (por ejemplo, metas sembradas a media tarde), el denominador crece y lo retenido puede aplicarse sin que lo sueltes (`app/precio/corrida.py:1062-1170`).
- **S4. La reversa no detiene al motor.** Revertir no cierra la meta. A los 7 días del cambio original, el motor repite el mismo movimiento que revertiste (`app/precio/reglas.py:283-302` ignora la reversa).
- **S5. US estuvo ciego 12 de 16 días.** Del 28-oct al 9-nov, Amazon US tuvo 99 de 99 publicaciones en «oferta desactualizada» casi todos los días. El arreglo J-H3 del 11-nov ya espera hasta las 21:00, pero si el insumo no llega, el día sigue ciego. Además, las publicaciones atrasadas que son minoría se escriben a las 13:10 y no se vuelven a evaluar ese día.
- **S6. La racha de ventas no tiene fecha.** Sale de la última decisión, sin importar su edad (`app/meli/precio_mercado.py:327-336`, `app/spapi/precio_mercado.py:385-394`). Una racha de hace semanas suma con la de hoy después de una pausa.

## Estructura: movimientos que borran complejidad

### E1. Quitar la relectura inmediata y cerrar «error» con la observación del día siguiente

- **Problema.** 37 de 37 relecturas ven el precio viejo, y cada escritura gasta una lectura extra de Pricing (cuota 0.5/s). Hay tres sitios que razonan con ese dato inútil: `fuentes.py:397-404`, `cambios.py:721-724` y `cerrar_huerfanas`.
- **Movimiento.** `cerrar_por_observacion` también cierra los `error` (observación = precio nuevo → confirmado; = precio anterior → sin efecto).
- **Qué desaparece.** `_readback`, `_leer_vivo_suave` en la escritura, las columnas `readback_*` como insumo de decisiones y la rama `sin_efecto` por relectura. Arregla F1 de paso.
- **Riesgo.** Un error real tarda un día en liberarse. Es aceptable, porque el freno de 7 días ya es más largo.

### E2. Un solo armado de la lectura para Amazon y MeLi

- **Problema.** `_lectura_unidad` (114 y 104 líneas, solo 42 distintas), `insumos` (86 y 105, con 47 distintas) y `cotizacion_guardada` (34 y 34, con 2 distintas) están casi copiados entre `app/spapi/precio_mercado.py` (676 líneas) y `app/meli/precio_mercado.py` (666 líneas).
- **Movimiento.** Pasar el flujo a `app/precio/` y dejar en cada adaptador cuatro ganchos: partir comisiones, historia, observación de precio y lector del detalle de comisión.
- **Qué desaparece.** Unas 250 líneas, y la posibilidad de que un arreglo entre en una plataforma y en la otra no (ya pasó con «sin miembros»).
- **Riesgo.** El candado de imports (MeLi no importa `app.spapi`) se respeta si lo compartido vive en `app/precio/`.

### E3. Un solo lugar para el modo y la configuración

- **Problema.** La escala off < shadow < live está escrita 4 veces: `config.py:24` y `config.py:137-141` (`ConfigMotor.modo_de`, que nadie usa), `compuerta.py:43`, `fuentes.py:109`, y además las relecturas en `corrida.py:1184-1202` y `cambios.py:792-810`. `correr` lee las mismas claves tres veces (`leer_config_motor`, `_modo_config`, `_modo_universo_config`, `corrida.py:1443-1447`). `ConfigMotor` valida 9 campos que nadie lee de ahí (`modo_global`, `modo_universo`, `banda_goal`, `freno_dias_error`, `senal_trafico_min`, …). Los dos adaptadores vuelven a leer `precio_senal_trafico_min` de la base en cada lote.
- **Movimiento.** Leer `ConfigMotor` una vez, pasarlo, y usar una sola función `modo_para(config, plataforma, canal, meta)`, también para las filas sin canal (hoy hay un «neutro live» especial en `corrida.py:683` y `826`).
- **Qué desaparece.** `validar_freno_dias_error`, `_numero_entero`, los envoltorios `fuentes._numero` y `fuentes._entero`, `ConfigMotor.modo_de` y las lecturas de configuración dentro de `insumos`.
- **Riesgo.** Bajo. La relectura antes de cada escritura sigue existiendo, pero llama a la misma función.

### E4. Borrar lo muerto y juntar lo gemelo en las reglas y las tablas

- `lineal=True` no lo pasa ningún llamador. La rama `lineal` de `_subir` y `_bajar` y `verificacion='lineal'` son código muerto (`reglas.py:211-220`, `382-388` y `473-479`).
- `_subir` y `_bajar` tienen 89 líneas cada una y solo difieren en 15. Basta una función con la dirección como parámetro.
- `precio_cap_*` no lo lee nadie (F4). Se borra o se usa.
- Las columnas `corrida_id` de `precio_decision` y `precio_cambio` nunca se llenan (0 de 10,644 decisiones y 0 de 53 cambios). Por eso «revertir por corrida» revierte el día entero de la plataforma (`cambios.py:765-767`): se llenan o se borran.
- Hay guardas que comparan una constante consigo misma: `FORMA_ESCRITURA_MELI != _FORMA_SELLADA` (`app/meli/write_client.py:149`) y `FORMA_PARCHE != ...` (`app/spapi/precio_write.py:82`).

Riesgo: nulo en comportamiento. Las pruebas de mutantes que fijan esas ramas se borran con ellas.

### E5. Partir `corrida.py` (1,585 líneas): la medición a la compuerta y el candado a un solo módulo

- **Problema.** `_medir_compuerta` (`corrida.py:953-1170`) arma tuplas con índices posicionales (`_INSUMO_DECISION = {"costo": (6, 7), ...}` y `_INGRESO = (13, 14)`) que dependen del orden de columnas de un SELECT: agregar una columna rompe todo sin aviso. El reclamo y el candado de sesión están copiados en `corrida.py:136-188` y `cambios.py:644-677`. `_a_la_venta` relee el catálogo completo por cada decisión pendiente (`corrida.py:1293`).
- **Movimiento.** La medición pasa a `compuerta.py` con filas por nombre; el candado queda en un solo administrador de contexto `candado_precio(conn, plataforma, dueño)`; el catálogo se lee una vez por corrida.
- **Qué desaparece.** Unas 300 líneas de `corrida.py`, una copia del candado y N lecturas del catálogo.
- **Riesgo.** Medio-bajo: hay que mover las pruebas que sabotean `_medir_compuerta` por nombre.

## Qué comprobé que está bien

- **Modo.** Gana el menor de global, universo y meta; producción lo confirma (desde el 9-nov todo es virtual). Un universo sin entrada queda apagado con error visible (`corrida.py:687-698`). Una clave inválida la rechaza `config_write.py:151`. Si la configuración no se puede leer a media corrida, la corrida apaga (`corrida.py:1184-1202`). La meta y la configuración se releen antes de cada escritura.
- **Costo faltante o en cero.** No decide. La estimación exige costo > 0, vigente, único y validado ese día (`estimacion_insumos.py:510-524`). En producción hay 0 decisiones con costo nulo o cero desde el 15-oct. Sin miembros en MeLi el motivo es `costo_ausente`.
- **Redondeo y moneda.** P* se redondea al centavo hacia arriba; el tope del escalón, hacia abajo y la bajada nunca queda por debajo del precio de meta (`objetivo.py:147-154`, `reglas.py:418-419` y `509-510`). Todo precio llega en centavos exactos a la escritura; MeLi rechaza más de dos decimales. `Cuenta` no se construye con monedas mezcladas y la moneda del escenario se compara con la de Pricing. MeLi solo escribe MXN.
- **Frenos.** Regla 11 (P* > 2·P o P* ≤ costo + envío), máximo dos cotizaciones, escalón de 10 % (el caso 1207: objetivo 2093.45, aplicado 2077.90 = 1889 × 1.10), 7 días (salvo F1), «no converge» y «perdiendo tras subida». Que un cambio virtual no frene en `live` es intencional: el precio real no se movió.
- **Escritura.** Antes de escribir compara el precio vivo con el precio con que se decidió, al centavo (`cambios.py:387-407`). El orden es INSERT + COMMIT → escritura → sello, con un índice único de cambio abierto. Las huérfanas se resuelven al arrancar. Ante un 429 reintenta una vez; ante un 5xx o un error de red no repite a ciegas. En producción, 34 cambios reales, todos confirmados por la observación del día siguiente, sin errores ni «no confirmado».
- **Reversa.** Es manual, con dry-run, huella y go. Revalida el precio vivo por fila, comparte candado con la corrida y se rechaza si el universo está en `live`.
- **MeLi.** Con variantes, manda la de costo más alto (`estimacion_repository.py:660-664`). La escritura manda todas las variantes y se niega si el item vivo trae variantes que Orbit no conoce, porque MeLi borra las omitidas (`app/meli/precio_mercado.py:640-646`). Una variante sin producto deja la publicación sin evaluar.
- **NH- y SET-.** Ningún emparejamiento por parecido de nombre o de SKU en precios, catálogo ni listings: todo es por SKU exacto. La única excepción es `meli_sku_alias`, con 9 correspondencias PEZ que verificaste tú (PEZ 01). En Amazon ningún ASIN está repetido entre publicaciones.
- **Concurrencia.** Amazon y MeLi usan candados distintos sobre datos distintos, sin carrera. Una corrida que dura más de 2 h la cubren `flock -n` y el candado de sesión de Postgres, aunque venza el reclamo de 1 h.
- **Señal de ventas.** Está bien acotada: hueco del ledger, ventas sin atribuir, historia menor de 75 días, días sin existencia o sin observar, u60 mínimo y racha de 3. Producción no ha bajado ningún precio.
