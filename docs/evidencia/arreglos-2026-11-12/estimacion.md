# Revisión: Estimación y contribución, JEV y Reputación

Revisión de solo lectura sobre `master` a4644aad (el mismo `app/` que corre en producción), hecha el 2026-11-12. No se tocó código. Las pruebas que reproducen las fallas están en `docs/evidencia/arreglos-2026-11-12/pruebas/estimacion/` (8 pruebas, las 8 fallan por la aserción que describe su falla). Las consultas a producción se hicieron con la sonda de solo lectura.

Comando de las pruebas:

```
PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q docs/evidencia/arreglos-2026-11-12/pruebas/estimacion/
# 8 failed in 1.61s
```

---

## Cómo funciona

**Estimación y contribución (lo que más dinero mueve).**
- Cada 6 horas (a las :45) un script copia la base del bridge y corre `ingest estimacion`. Por cada publicación de Amazon MX, Amazon US FBM y MeLi toma el precio vigente, pide a Amazon (o a MeLi) la comisión que cobraría a ese precio y la junta con el costo del producto (de contabilidad), el envío, el ISR y el IVA de la política vigente.
- Con eso calcula la «contribución»: precio sin IVA − costo − comisiones − envío − ISR. La guarda como un «escenario» por publicación, y nunca se borra ni se corrige.
- Si falta un dato (costo, comisión, tipo de cambio, envío), el escenario queda «incompleto» y no da número. No inventa ceros: un costo de 0 cuenta como costo faltante.
- **Quién usa ese número:** el motor de precios, que está en *live* en los 4 universos y escribe precios en Amazon y MeLi; la herramienta de goals de precio; y las pantallas. **No** alimenta las pujas de Ads.
- **El target de Ads usa otro margen:** sale del libro contable (`v_target_margen_plataforma`, 90 días). Además existe un cuarto cálculo «pre-cargos» por keyword (`v_contribucion_entidad`), marcado como «no decisorio».
- **Horario útil:** el costo solo se acepta si contabilidad lo validó ese mismo día UTC, y eso pasa a las 08:15. Por eso las corridas de 00:45 y 06:45 nunca dan un número utilizable; solo las de 12:45 y 18:45 lo dan. Los precios se mueven de 13:10 a 23:10, dentro de esa ventana.

**JEV.**
- Dos veces al día (09:30 y 21:30 UTC) toma las búsquedas con gasto, cero ventas y al menos 3 clics en datos de 10 días o más.
- Por cada pareja búsqueda-producto le pregunta a un modelo de IA (TypeSafe) si la búsqueda tiene que ver con el producto, y guarda una «lectura» que vale 36 horas.
- **No mueve pujas ni frena al motor:** ningún módulo del optimizador, del ciclo ni de la cola lee las tablas de JEV.
- `jev.bloqueo=true` solo muestra en /cortes las búsquedas «ajenas» como propuestas. El negativo en Amazon se crea únicamente cuando el dueño da clic en «Bloquear» (`app/bloqueo_dueno_efecto.py:225`), y tiene reversa («Quitar»).
- `jev.min_clics=3` no es un umbral para cortar: solo decide qué búsquedas se le preguntan al modelo.
- `jev.tope_diario=10000` cuenta llamadas al modelo, no dinero (unos 0.5 USD al día como máximo).

**Reputación.**
- A las 09:30 UTC lee de MeLi, solo con GET: las reseñas de cada artículo, la reputación de la cuenta y las preguntas. Todo queda guardado sin poder modificarse.
- A las 10:30 evalúa 5 tipos de alerta. Las alertas no se envían por su cuenta: solo viajan dentro del resumen del ciclo de Amazon Ads.
- Ningún código pausa campañas ni toca precios por reputación.
- La parte de Amazon (Apify) nunca se instaló: tiene 0 filas.

---

## Fallas (con reproducción)

### F1. La comisión de Amazon MX se cuenta dos veces y por eso las pujas de México salen más bajas de lo que manda la regla

**Qué te pasa:**
- En 257 de 620 pedidos de Amazon MX de los últimos 120 días, el libro trae la comisión real de Amazon **y además** una comisión «estimada» que contabilidad manda antes de la liquidación.
- Dentro de la ventana que usa el target de Ads, eso suma **27,925.63 MXN de cargos que no existen**, sobre 539,230.93 MXN de venta.
- El margen de México sale en **40.3 %**, cuando sin el duplicado sería **≈ 45.5 %**.
- Como el target de Ads de México es la mitad del margen, **el target queda en ≈ 20 % en vez de ≈ 22.8 %**: todas las pujas de México salen ~13 % más bajas de lo que pide tu regla.
- Las decisiones de los últimos 14 días usaron `margen_plataforma` con target de 19.96 a 20.19.

**Dónde:**
- `app/ledger.py:508-525`: la ingesta del libro deduplica por `source_event_id`. La estimada (`amazon|fee_estimate|referral_fee|<pedido>`) y la real (`amazon|finance|fee|<pedido>|<sku>|Commission`) tienen claves distintas, así que entran las dos.
- `migrations/0016_target_margen_correcciones.sql:64-75`: la vista suma todos los cargos que no son de Ads, sin quitar la estimada cuando ya llegó la real.
- Ningún archivo de `app/` menciona `fee_estimate`.
- Nota: este código es de la ingesta del libro, pero el margen que produce es el de esta área. Conviene avisar al revisor del libro.

**Reproducción (sonda):**

```sql
WITH w AS (SELECT * FROM ledger_event WHERE platform='amazon_mx' AND event_date >= current_date-105 AND event_date < current_date-15),
dup AS (SELECT e.order_id, e.amount FROM w e WHERE e.source_event_id LIKE 'amazon|fee_estimate|%'
        AND EXISTS (SELECT 1 FROM w f WHERE f.order_id=e.order_id AND f.source_event_id LIKE 'amazon|finance|fee|%Commission'))
SELECT (SELECT sum(amount) FROM w WHERE kind='sale') ventas,
       (SELECT sum(amount) FROM w WHERE kind IN ('fee','refund','withholding') AND COALESCE(fee_type,'')<>'ads') cargos,
       (SELECT sum(amount) FROM dup) duplicado, (SELECT count(*) FROM dup) n_dup;
-- ventas 539230.93 | cargos -205523.31 | duplicado -27925.63 | n_dup 206
```

Ejemplo: el pedido 701-0603650-9181060 (venta 1,123.20) trae `Commission −168.48` (real, 15 %) y además `fee_estimate referral_fee −125.21`.

**Qué debería pasar:** cuando llega la comisión real de un pedido, la estimada de ese pedido ya no cuenta. Hay dos formas de hacerlo: en la vista, o no importando las estimadas.

### F2. El plan de goals de precio nunca calcula el precio objetivo de MeLi, y el freno de «el precio salta más de 25 %» nunca actúa en MeLi

**Qué te pasa:**
- Cuando se siembran o cambian goals de precio, la herramienta calcula para cada publicación el precio que resultaría (P\*). Si ese precio se aleja más de 25 % del actual, frena y pide confirmación.
- En MeLi ese cálculo falla siempre. Toda fila sale «referencia_sin_derivar», sin precio objetivo, y el freno del 25 % nunca se dispara.
- Hoy hay **45 goals de MeLi en live** sembrados así.

**Dónde:**
- La estimación de MeLi guarda la comisión como `SaleFee` (`app/meli/estimacion.py:421-438`).
- `app/precio/siembra.py:312` la parte con `derivar_ref_fijo` (`app/precio/objetivo.py:83`), que solo reconoce el `ReferralFee` de Amazon.
- La regla «parte variable de la comisión» está escrita tres veces: `estimacion_amazon.partir_fees`, `meli/estimacion.partir_fees_meli` y `precio/objetivo.derivar_ref_fijo`. Este llamador eligió la de Amazon. El motor de precios de MeLi sí usa la correcta (`meli/precio_mercado.py:201`), así que el problema es del plan de goals, no de la escritura diaria de precios.

**Prueba:** `docs/evidencia/arreglos-2026-11-12/pruebas/estimacion/test_estimacion_meli_referencia.py`, con la forma real de producción (989.00 MXN, `SaleFee` 192.86).

```
AssertionError: MeLi completo sale sin P*: motivo='referencia_sin_derivar'
```

**Qué debería pasar:** con un escenario MeLi completo, el P\* se calcula igual que en Amazon (variable = `SaleFee`) y el freno del 25 % aplica.

### F3. La actualización de estimaciones dice «ok» aunque no haya podido cotizar nada

**Qué te pasa:**
- Si Amazon rechaza todas las cotizaciones de comisión (credencial vencida, 403, cuota) o el token de MeLi no carga, la corrida se registra como `ok=true` y sale con código 0.
- `cron-avisa` solo avisa cuando la salida es distinta de 0, así que nadie se entera.
- El motor de precios deja de evaluar esas publicaciones, y el único aviso llega **a los 3 días** (`precio_aviso_dias_sin_evaluar`).

**Dónde:**
- `app/estimacion_ingest.py:528`: `ok = True` siempre.
- `:644`: la salida solo vale 1 si una plataforma Amazon se queda sin oferta fresca.
- `:405-409` y `:601-602`: sin cliente MeLi, toda MeLi se salta y la salida sigue en 0.
- Nadie más lee el `ok` de `bridge_estimacion_fees`.

**Prueba:** `docs/evidencia/arreglos-2026-11-12/pruebas/estimacion/test_estimacion_ok_falso.py` (2 publicaciones, Amazon responde 403 a todo).

```
AssertionError: 100% de cotizaciones en error y la corrida se sella ok=true
  ResultadoIngestaEstimacion(ok=True, ..., fees_error=2, ... skip_reason='2x fee_http_403')
```

**Qué debería pasar:** si ninguna publicación de una plataforma quedó cotizada, la corrida sale con código distinto de 0 (o `ok=false`) y `cron-avisa` avisa ese mismo día.

### F4. JEV: una corrida que no puede juzgar nada cierra como éxito, y el vigía no avisa

**Qué te pasa:**
- Sin la llave de TypeSafe, con el proveedor caído o con el tope agotado, el job termina con código 0 y sin Telegram.
- La pantalla «Gasto sin venta» se queda sin lecturas nuevas y no salen propuestas de bloqueo.
- El vigía de las 11:00 solo revisa que la corrida haya empezado, no cómo cerró.

**En producción ya pasó:** `jev_corrida` tiene **12 cierres `sin_api_key`** (del 7 al 12 de octubre) y **7 cierres `tope`** (del 12 al 14 de octubre), y nadie se enteró.

**Dónde:**
- `app/jev_senales.py:679-686` (motivo de cierre) y `:836` (siempre devuelve 0).
- `:823-830`: el aviso solo sale cuando hay excepción.
- `app/jev_vigia.py:33-37`: el vigía solo mira que exista un `inicio`.

**Prueba:** `docs/evidencia/arreglos-2026-11-12/pruebas/estimacion/test_jev_ok_falso.py`, 2 fallan:

```
corrida 'sin_api_key' sin juzgar nada: salida=0, avisos=[]
proveedor caido y el vigia no avisa
```

**Qué debería pasar:** un cierre `sin_api_key`, `proveedor_caido` o `tope` sale con código distinto de 0 o manda aviso, y el vigía revisa cómo cerró la última corrida.

### F5. Reputación: la lectura de MeLi estuvo apagada 19 días y nadie se enteró

**Qué te pasa:**
- Del 28 de septiembre al 16 de octubre no se leyeron reseñas, preguntas ni la reputación de la cuenta.
- Tampoco pudo salir ninguna alerta de reseña de 1 estrella.
- Las demás tareas sí corrieron esos días, así que solo faltaba el cron de reputación.

**Dónde:** ninguna revisión de salud ni de avisos mira las fuentes `reputacion_*`. Fuera de los archivos de reputación no hay ninguna referencia a ellas.

**Reproducción (sonda):**

```sql
SELECT min(d)::date, max(d)::date, count(*) FROM generate_series('2026-09-08'::date,'2026-11-12'::date,'1 day') d
 WHERE d::date NOT IN (SELECT DISTINCT metric_date FROM reputation_snapshot);
-- 2026-09-28 | 2026-10-16 | 19
```

**Qué debería pasar:** un aviso por Telegram si el día no tiene una corrida exitosa de reputación, como ya pasa con la ingesta principal.

### F6. Reputación: una caída del nivel de la cuenta MeLi se puede perder sin alerta

**Qué te pasa:**
- La cuenta puede bajar de verde a amarillo sin que recibas la alerta.
- Pasa si el job de alertas no corre justo ese día, o si la lectura corrió dos veces. Producción ya tuvo 3 lecturas de la cuenta el 8 de septiembre.
- Además, la alerta se cierra sola al día siguiente aunque la cuenta siga en amarillo.

**Dónde:** `app/reputacion_alertas.py:271-275`. Solo compara las dos últimas lecturas, no el último nivel ya evaluado.

**Prueba:** `test_reputacion_revision.py::test_caida_de_nivel_de_cuenta_se_pierde_si_el_job_de_alertas_salta_un_dia` (verde, amarillo, amarillo):

```
AssertionError: la cuenta cayo a 3_yellow y nunca hubo alerta: {'abiertas': 0, ...}
```

**Qué debería pasar:** comparar contra el último nivel evaluado o contra el mejor de los últimos 7 días, como ya hace la alerta de caída de rating.

### F7. Reputación: una alerta crítica (reseña de 1 estrella) puede no llegar nunca al Telegram

**Qué te pasa:**
- Las alertas solo viajan dentro del resumen del ciclo de Ads, y solo las creadas en las últimas 24 horas.
- El ciclo corre a las 08:40, unas 22 horas después de que se crean las alertas (10:30).
- Basta un ciclo fallido, un despliegue a esa hora o la bandera de Ads apagada para que la alerta no salga jamás.
- No queda ningún registro de «enviada».
- Además, cada alerta sale dos veces: en el resumen de MX y en el de US.

**Dónde:** `app/reputacion_alertas.py:374` (ventana fija de 24 h) y `app/notifica.py:684-689`.

**Prueba:** `test_reputacion_revision.py::test_alerta_nunca_llega_a_telegram_si_el_ciclo_no_corre_en_24h` (alerta crítica de hace 46 horas):

```
alerta critica abierta y nunca enviada no sale: []
```

**Qué debería pasar:** guardar cuándo se avisó cada alerta y enviar las pendientes, sin depender del ciclo de Ads.

### F8. Reputación: la pantalla muestra preguntas «sin responder» que ya no existen

**Qué te pasa:**
- Hoy la pantalla dice 4 preguntas pendientes y en MeLi solo hay 1.
- Las preguntas 13677843937, 13677906827 y 13678842036 (artículo MLM2736115339) se vieron por última vez el 27 de octubre, aunque el artículo se sigue leyendo a diario.

**Dónde:** `app/api_reputacion.py:140-155`. Toma la última fila de cada pregunta sin mirar si apareció en la lectura de hoy. `reputacion_plan.py:233` además descarta estados como «borrada», y eso congela la última fila «sin responder».

**Prueba:** `test_reputacion_revision.py::test_pregunta_que_desaparece_de_meli_queda_pendiente_para_siempre`:

```
pendientes fantasma en pantalla: ['Q_BORRADA', 'Q_SIGUE']
```

**Qué debería pasar:** una pregunta que no aparece en la última lectura exitosa deja de contar como pendiente.

### F9. Reputación: si MeLi responde sin la lista de reclamos, se guarda «0 reclamos» (regla 3)

**Qué te pasa:**
- Hoy el histórico real es de 22 a 24 reclamos acumulados.
- Un 0 inventado apaga en silencio la alerta de reclamos, o la dispara en falso cuando el dato vuelve.
- Gravedad media: lo que dispara el error (un cambio en la forma de la respuesta de MeLi) es hipotético, pero la violación de la regla 3 está en el código.

**Dónde:** `app/reputacion.py:442-443` (lista vacía si falta la clave `data`) y `app/reputacion_plan.py:213` (`len(disputas)`).

**Prueba:** `test_reputacion_revision.py::test_respuesta_de_disputas_sin_la_clave_data_se_guarda_como_cero_disputas`:

```
disputas_total=0
```

**Qué debería pasar:** si falta la clave, el conteo queda vacío (`None`), no en 0.

---

## Sospechas

**Estimación y contribución**

1. **La comisión estimada no coincide con la que Amazon cobra de verdad (regla 10: nunca se concilió).**
   - Concilié 191 pedidos de una sola pieza de Amazon MX contra el escenario del mismo producto.
   - La comisión real del libro es exactamente **15.00 % del precio con IVA**; la del escenario es **12.93 %**, o sea 15 % / 1.16.
   - La cuota de FBA real es 75.01 contra 67.53 estimada (×1.11).
   - Es decir: Amazon cobra la comisión **más IVA** y la estimación la cuenta **sin IVA**.
   - Eso solo es correcto si ese IVA se acredita. Si el régimen es de pago definitivo, ese IVA no se acredita y la contribución de cada publicación sale ~2 puntos más alta de lo real, y el motor de precios fija precios con ese margen inflado.
   - **Pregunta para tu contador**, no se resuelve con código.
   - Pasa lo mismo al revés en el margen del target de Ads (F1): ese sí cuenta el IVA de las comisiones. Hoy hay dos márgenes para lo mismo que no cuadran entre sí (regla 2): la mediana del escenario de México FBA es 53.8 %; el margen del libro, 40.3 %.
2. **El plan de goals «margen de hoy» lee el último escenario disponible sin importar su edad.** `app/precio/goals_write.py:364-368` no filtra por fecha (su docstring dice «sin overlay de frescura»). Si una publicación lleva días sin escenario válido, su goal se siembra con un margen viejo. Es una herramienta manual con confirmación; no lo reproduje.
3. **La comisión fija de MeLi es un supuesto sin verificar.** `app/meli/estimacion.py:400-403` lo admite: «verificar contra una categoría con fijo > 0 antes de operar en firme». Hoy todos los precios de MeLi están arriba de 299 MXN y el fijo es 0 (sonda), así que hoy no afecta.
4. **Motivo engañoso en US.** A las 00:45 y 06:45, 102 publicaciones de US salen con «fx_ausente» además de «costo_desactualizado». El tipo de cambio sí existe: el envío pide el tipo de cambio del costo, y como el costo se descartó, no lo hay (`estimacion_repository.py:1064-1078`). Además, un producto US con costo en USD y envío en MXN se quedaría sin número para siempre por la misma razón.
5. **El cuarto margen («contribución pre-cargos») se muestra en pantalla y en el resumen de Telegram** (`app/notifica.py:65-84`) con la etiqueta «no decisoria». Es otra cifra de margen más, calculada en una vista SQL de ~515 líneas (`migrations/0010_…`), distinta de las otras tres (regla 2).

**JEV** (sin reproducir)

6. **El motor no sabe de los negativos del dueño.** La ventana de cortes conserva el gasto de antes del bloqueo, así que el motor puede proponer el mismo negativo; y si lo adopta, «Quitar» lo borra también para el motor. El spec de JEV ADS 03 lo reconoce.
7. **El vigía nunca ve la corrida de 21:30.** A las 11:00 siempre ve la de 09:30. Desde el 9 de noviembre la de 21:30 escribe 0 lecturas nuevas.
8. **La pausa automática por baja aprobación no protege si se aprueba todo.** Hoy, entre 04:40 y 04:44 UTC, se aprobaron 48 bloqueos (~5 s cada uno; mediana de 3 clics en MX). Uno tenía solo 2 clics, porque una lectura vigente se recalcula aunque ya no cumpla el mínimo (`jev_lectura.py:480-483`, así lo pide el diseño).
9. **No hay tope de tiempo por corrida.** Con TypeSafe lento (30 s por llamada × hasta 10,000), una corrida duraría horas y el candado frenaría la siguiente.

**Reputación** (sin reproducir)

10. **`carga_reputacion_digest` se traga cualquier error y regresa `None`** (`reputacion_alertas.py:390`): el resumen sale sin el bloque de reputación y sin nota.
11. **Un solo artículo con timeout o respuesta mal formada tumba la lectura completa del día** (`reputacion.py:245` solo atrapa `ReputacionError`). Pasó 1 vez en 47 corridas.
12. **Con cero filas el comando sale con error, pero `ingest_run` queda como exitosa** (`reputacion.py:704`).

---

## Estructura: movimientos que borran complejidad

### E1. Tratar una publicación de Amazon como una de MeLi con un solo producto

- **Problema:** `app/estimacion_repository.py` tiene 1,399 líneas.
  - El camino de Amazon (`sembrar_escenario_desde_refs`, 256 líneas) y el de MeLi (`_sembrar_meli` + `_fila_meli_disponible` + `_fila_meli_sin_cuenta`, 443 líneas) arman lo mismo dos veces.
  - El diccionario de la «foto de entrada», de 30 líneas, está copiado (`:774-803` y `:1140-1169`).
  - El constructor de 35 campos `EscenarioPersistible(...)` aparece 4 veces (`:832`, `:937`, `:1195`, `:1369`).
  - La escalera «ambigua > desactualizada > incompleta» está escrita 4 veces (`estimacion_venta.py:402-433`, `repository.py:522`, `:1111-1117`, `:1335-1340`).
- **Movimiento:** Amazon pasa por el camino de MeLi con miembros = [`listing.product_id`]. Con un solo miembro, «manda el de mayor carga» da lo mismo que hoy.
- **Qué desaparece:**
  - la rama de Amazon (~200 líneas);
  - una de las dos fotos de entrada;
  - 2 de los 4 constructores;
  - 3 de las 4 escaleras de estado.
- **Riesgo:** cambia el `source_event_id` de los escenarios de Amazon (la identidad incluye «miembros»). Se reescribe un escenario por publicación una sola vez y no hay pérdida, porque todo es append-only. Hay que fijarlo con las pruebas de escenario que ya existen.

### E2. Una sola tabla de motivos

- **Problema:** el vocabulario de motivos vive en 5 conjuntos:
  - `MOTIVOS_ESTIMACION` (33);
  - `bloqueantes` (21, `estimacion_venta.py:562-583`);
  - `ESTADOS_DESACTUALIZADA` (4);
  - `MOTIVOS_TRANSICION_NEGATIVA` (5, `estimacion_ingest.py:76`);
  - su copia `motivos_admitidos` (`estimacion_repository.py:1321`).
- **Por qué importa:** como `calcular_contribucion` devuelve `motivos=()` cuando el estado es «disponible» (`:614-616`), un motivo que no esté en `bloqueantes` se pierde sin ruido. Hoy no pasa nada solo porque cada motivo «no bloqueante» deja de rebote otro dato vacío que sí bloquea. Eso es frágil.
- **Movimiento:** un `dict` motivo → (estado, bloquea, transición) en `estimacion_venta.py`.
- **Qué desaparece:** 4 conjuntos, la copia y la regla implícita.
- **Riesgo:** bajo. Ya hay una prueba que recorre los literales con `ast`.

### E3. Una sola regla para partir comisiones y validar dinero

- **Problema:**
  - «Parte variable de la comisión» está escrita 3 veces: `estimacion_amazon.partir_fees:57`, `meli/estimacion.partir_fees_meli:80` y `precio/objetivo.derivar_ref_fijo:76`. Hay además un candado de prueba solo para que dos de ellas coincidan, y la tercera ya causó F2.
  - «Decimal finito con 4 decimales en NUMERIC(14,4)» está escrita 4 veces: `estimacion_venta.validar_dinero:153`, `estimacion_fees._decimal_dinero:226`, `meli/estimacion._decimal:299` (el comentario dice «espejo») y `estimacion_repository._parse_decimal_estricto:166`.
  - `_MAX_DINERO` está definido 3 veces.
- **Movimiento:**
  - `partir_fees(detalles, total)` con el tipo variable que diga la plataforma (`ReferralFee`/`SaleFee`), y `derivar_ref_fijo` la llama;
  - un solo validador de dinero.
- **Qué desaparece:** 2 funciones de partir, 3 validadores, el candado de coincidencia y la causa de F2.
- **Riesgo:** bajo. Son funciones puras con pruebas.

### E4. Quitar las dos corridas diarias de estimación que nunca sirven

- **Problema:** en 7 días, las corridas de 00:45 y 06:45 escribieron 6,244 escenarios y **0 disponibles**: todos «costo_desactualizado», porque el costo se valida a las 08:15. Cada una gasta unas 400 cotizaciones de SP-API (a 1 por segundo) y ~1,200 filas.
- **Sonda:**

  ```sql
  SELECT extract(hour from observed_at)::int, count(*),
         count(*) FILTER (WHERE estado='disponible')
    FROM estimacion_escenario WHERE observed_at > now()-interval '7 days' GROUP BY 1;
  -- 0 | 3535 | 0
  -- 6 | 2709 | 0
  ```

- **Movimiento:** cron `45 12,18 * * *`. La alternativa es correr la validación de costos antes de las 00:45; con eso las 4 corridas servirían y las pantallas no dirían «desactualizada» toda la mañana.
- **Qué desaparece:** la mitad de las llamadas y filas, y la mitad de los avisos posibles de `cron-avisa` por nada.
- **Riesgo:** ninguno para precios, que solo corren de 13:10 a 23:10.

### E5. JEV: borrar el experimento cerrado y la revisión manual que ya nadie usa

- **Problema:**
  - `tools/jev_orden_opciones.py` (533 líneas) y su prueba (303) son un experimento de una sola vez, cerrado el 13 de octubre (0 de 99 respuestas cambiaron). No está en cron ni en la imagen. Además es un llamador de `Libro.pagar` que paga sin respetar el tope.
  - La revisión por decisión `DecisionARevisar` tiene 3 usos en toda su historia (el último, el 4 de octubre). Aun así, /cortes ejecuta `AsesorAds.leer` por cada corte en cada visita (`api_dashboard.py:1480-1495`).
- **Movimiento:** borrar las dos cosas. Fuera de eso, meter `jev_avisos` en `jev_senales` (existe «por el presupuesto de 900 líneas», lo dice su docstring) y dejar una sola regla de «límite de bytes» y una sola de «JSON canónico» (hoy hay 2 y 3 copias).
- **Qué desaparece:** ~836 + ~300 líneas, un módulo y 23 nombres privados importados entre módulos.
- **Riesgo:** las 3 revisiones viejas dejan de verse.

### E6. Reputación: borrar las entradas que el cron no usa y Apify, y mover el cliente de MeLi a su lugar

- **Problema:**
  - `sync_meli`, `sync_seller` y `sync_questions` (`reputacion.py:262/586/615`, ~82 líneas) y 45 líneas de re-exportaciones solo las usan las pruebas: la suite prueba un camino que el cron no recorre.
  - Junglee/Apify (~220 líneas) lleva 2 meses con 0 filas.
  - `ClienteMeli`/`ReputacionError` viven en `reputacion_clientes.py`, pero los usan 7 módulos ajenos; el motor de precios lanza errores llamados «ReputacionError».
  - El cálculo del «valor previo» está escrito dos veces (`api_reputacion.py:56-88` y `reputacion_alertas.py:162-232`).
- **Movimiento:** borrar las entradas paralelas y Apify, crear `app/meli/cliente.py` y dejar una sola función de «previo».
- **Qué desaparece:** ~350 líneas y una dependencia cruzada falsa.
- **Riesgo:** hay que reescribir las pruebas de reputación contra `_corre_meli`.

**Patrón común a las tres piezas (F3, F4, F5, sospecha 12):** las tres terminan en «ok» o con salida 0 sin haber hecho nada útil, y ningún vigía lo nota. Una sola regla para los tres jobs evitaría las cuatro: «cero elementos procesados = salida distinta de 0», y `cron-avisa` ya hace el resto.

---

## Qué comprobé que está bien

**Estimación**
- **Costo faltante o en cero:** un costo de 0 se descarta como no vigente (`estimacion_insumos.py:516`) y uno faltante bloquea con «costo_ausente». No hay productos «rentabilísimos» por costo vacío: en producción, las contribuciones disponibles van de −17.3 % a 66.5 %, y las más altas tienen costo real (97.82 contra un precio de 1,189).
- **División entre cero:** el porcentaje divide entre un ingreso que ya se validó como positivo. El tipo de cambio exige una tasa mayor que 0, con 8 decimales.
- **Moneda:** la dirección del tipo de cambio es la misma en la estimación y en la vista (USD←MXN = costo/tasa). `leer_cuenta` rechaza un escenario con monedas distintas (`estimacion_reader.py:330`). La cotización de Amazon rechaza una moneda distinta en cada detalle y exige que el total cuadre con la suma.
- **ISR:** se concilió contra la retención real. Amazon MX retuvo 3,888.71 MXN el 12 de octubre sobre 151,597.34 de venta sin IVA de septiembre (2.57 %, contra 2.5 % de política).
- **Corrida a medias:** cada publicación se guarda en su propia transacción y su escenario apunta a su oferta, su comisión y su costo, así que no hay números de dos corridas mezclados dentro de una publicación.
- **Escenario viejo:** el lector invalida cualquier escenario de otro día o con oferta de más de 6 horas (`estimacion_reader.py:123-135`). Como las corridas van cada 6 horas, un escenario que una corrida no reemplazó ya está vencido cuando corre el motor de precios. El caso de variantes de MeLi sin mapear además tiene un segundo freno (`meli/precio_mercado.py:638`).
- **Las cuotas que tardan 15 a 30 días en madurar:** aquí no se usan. La estimación usa la cotización de la API, con su fecha guardada, no el cargo del libro.
- **La estimación no alimenta las pujas de Ads:** ningún módulo de `app/optimizer` la lee.

**JEV**
- **No toca las decisiones del motor.** «Ajena» exige que todos los productos tengan ficha y que todos digan «no satisface»; cualquier falla da «Indeterminado».
- **Respuestas del modelo:** una mal formada (modelo, opciones, probabilidades que no suman 1, confianza fuera de [0, 1]) o un timeout de 30 s quedan como `respuesta_invalida`, sin reintento, y la búsqueda no se juzga.
- **Si JEV se cae:** las lecturas vencen a las 36 horas y las propuestas desaparecen. Los negativos aprobados tienen reversa con confirmación.
- **Madurez y veto:** las ventanas de los bloqueos aprobados terminan 11 días o más antes del bloqueo. El veto de 30 días se respeta.

**Reputación**
- **Historial intacto:** las 4 tablas solo admiten filas nuevas, por trigger.
- **Rating vacío, no cero:** un artículo sin reseñas guarda el rating vacío, no 0 (1,739 de 3,140 filas).
- **Sin mezclas:** toda la lectura de red termina antes de abrir la transacción.
- **Sin acciones automáticas:** no hay ninguna sobre campañas ni precios, así que la regla 7 todavía no aplica.
