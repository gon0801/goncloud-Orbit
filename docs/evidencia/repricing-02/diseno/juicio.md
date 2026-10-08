# Arena de diseño y juicio cruzado (2026-10-08)

Tres candidatos independientes, cada uno en un modelo distinto, recibieron la
misma tarea y el mismo fundamento: la lectura del código y del historial del
motor de precios, los 29 huecos encontrados (G1 a G29) y las diez decisiones
del dueño del 2026-10-08 (D1 a D10). Para forzar formas distintas, cada uno
partió de una dirección diferente. Un juez en otro modelo los puntuó con la
rúbrica de abajo y cotejó contra el repo las afirmaciones que cargaban peso.
Los paquetes completos de cada candidato viven en una carpeta privada del lead.

El resultado sintetizado es el [diseño](../../../superpowers/specs/2026-10-08-repricing-02-design.md),
con su [bosquejo de tipos](bosquejo.py) y su [modelo de datos](datos.sql).

## Los candidatos

- **A, «frontera de plataforma dentro del motor».** Dos fronteras con registro:
  abajo el universo (`platform/canal`, qué deja una venta, en la estimación) y
  arriba el mercado (cómo se observa y se mueve un precio, puerto
  `Vitrina`/`Mercado`). Las decisiones se guardan tal como son antes de medir el
  fusible; retener y soltar son tablas aparte. La unidad de precio es la fila de
  `listing`, con sus miembros.
- **B, «hechos neutros, un actuador».** Cada plataforma produce las mismas filas
  y cinco vistas `precio_hecho_*`; el motor nunca sabe qué plataforma valúa y
  recibe un `Actuador` de tres verbos. Fees como `Tarifa` tipada. Mercado Libre
  solo con publicaciones de un único producto.
- **C, «primeros principios, el cambio más chico».** Híbrido: puerto `Mercado`
  de tres verbos y vistas SQL neutras para todo lo demás. Interruptor y fusible
  en cuatro tablas con un escritor cada una; fusible por universo; `goal_id` en
  la decisión.

## Rúbrica y puntajes del juez (1 a 5)

| Criterio | A | B | C |
| --- | ---: | ---: | ---: |
| Cobertura de G1 a G29 | 5 | 3 | 4 |
| Frontera de plataforma y profundidad de la interfaz | 4 | 4 | 3 |
| Reglas de Orbit y decisiones del dueño | 4 | 2 | 4 |
| Seguridad sin cupo | 4 | 2 | 4 |
| Modelo de datos y migración | 4 | 3 | 3 |
| Construible en paralelo | 4 | 3 | 5 |
| **Total** | **25** | **17** | **23** |

En seguridad C traía la semántica más completa, pero no vio que el apagador en
`shadow` revienta contra el trigger `precio_decision_coherente`; por eso baja
de 5 a 4.

## Base elegida: A

El juez y el lead coincidieron. A es el que un mantenedor futuro extiende con
menos riesgo de romper invariantes:

- La estimación sigue siendo la única fuente del margen. El puerto lee el
  escenario, no lo produce, y conserva las FK de auditoría de la 0039.
- Retener no reescribe la decisión. Queda `subir` con su cuenta, y una fila
  aparte dice qué corrida no la aplicó. Por eso soltar aplica el mismo día.
- El dinero es de una sola moneda por construcción y `Convertido` es la única
  puerta entre monedas.
- El envío imputado es una clase distinta, además de una columna.
- Fue el único que leyó la 0039 con el cuidado suficiente para ver el choque
  del apagador con el trigger de modo.

## Dónde coincidieron los tres

Acuerdo fuerte, se toma tal cual:

- Un puerto de plataforma con tres verbos de efecto: cotizar, leer el precio
  vivo y escribir.
- El protocolo de `precio_cambio` sale de `app/spapi/precio_write.py` a un
  módulo genérico de `app/precio/`.
- El envío como tipo con origen (`propio | familia | marketplace | politica`),
  columna tipada en escenario y decisión, mediana de medianas, nunca constante.
- `confirmado_por = 'lectura_viva'` para revertir el mismo día.
- Índice único parcial para resembrar un goal el mismo día.
- El plan de goals con huella (`planear` y `aplicar`) dentro de `app/`.
- Borrar el cupo, la prioridad, `_ingreso_60d` y las fases de cobertura.
- El fusible donde estaba `repartir_cupo`, contando la sombra.
- La política por universo como filas, no como código.
- Un `flock` por grupo de cuota.

## Injertos

De **C**:

1. `precio_decision.goal_id` y la validación de ese goal en el trigger. A
   afirmaba que el trigger rechazaba la decisión cuando el goal cambia a media
   corrida; el juez comprobó que no: el trigger encuentra el goal nuevo y lo
   escribe en la fila.
2. Producto ancla en `listing.product_id`, que sigue `NOT NULL`. A lo volvía
   nulo para Mercado Libre; siete módulos y doce vistas cruzan por esa columna.
3. Fusible por universo `(platform, canal)`, sobre unidades medidas y con mayor
   estricto.
4. `resumen_enviado_at` y el aviso de hombre muerto en `/salud`.
5. Las claves de config sembradas dentro de la migración.
6. Candados de arquitectura en un archivo por carril, criterio de salida del
   corte 0b y prueba de paridad de la activa canónica.
7. Auditoría de quién lee `ledger_event` antes del primer INSERT de `meli`.

De **B**:

8. Fees partidos como columnas del escenario (`fee_variable`, `fee_fijo`,
   `fee_cotizable`).
9. `SinCotizador` y `precio_decision.verificacion` (`cotizada | lineal`).
10. Lectura por lote en `Vitrina.insumos`.
11. La corrección de que `meli_sku_mapping` es del bridge, no de Orbit.

## Rechazado, y por qué

- **B: el cooldown ignora todo cambio en `error`.** Un PATCH aplicado con
  respuesta fallida se volvería a mover al día siguiente. Se queda la regla de
  A: no cuenta solo si el readback probó que el precio no se movió.
- **B: goal solo para publicaciones de Mercado Libre de un único producto.**
  Deja fuera a 48 de 65 y contradice D8.
- **B: recortar a la banda al sembrar.** Sembrar 10 % a quien gana 8 % es
  decidir subirle el precio. Se listan aparte.
- **B: dos escritores en `precio_fusible` y un CHECK con fecha literal.**
- **C: vistas SQL como frontera de lectura.** La regla de «se podía comprar» y
  el partido de fees terminarían en migraciones que dos carriles editan.
- **C: el árbol de fees de Amazon como contrato del puerto**, que obliga a
  Mercado Libre a fabricar un `ReferralFee`, y `Mercado.sitio` a la vista.
- **C: la reversa sin tomar el candado de la corrida**, y el cron único que
  pone a Mercado Libre en fila detrás de Amazon.
- **C: tabla de fuentes de envío y mediana de cambio de margen.** La regla de
  envío queda en código puro y probado, donde lo desconocido ya cae en `otros`
  contado. La deriva por insumo de A ya dice cuál insumo saltó; se le sumó
  `fee_variable` para cubrir todos los componentes.
- **C: un CHECK sobre `precio_decision` sin `NOT VALID`.** Fallaría en
  producción contra las decisiones que ya existen.
- **A: la Buy Box como evidencia de pérdida de ventas.** Contradice una decisión
  vigente del dueño: perderla avisa, no frena ni baja.

## Lo que los tres pasaron por alto

El juez lo señaló y el diseño lo resuelve o lo pregunta:

1. La cotización guardada del día cuando el goal cambia a media tarde. Resuelto:
   esa unidad sale `no_evaluado(cotizacion_de_otro_goal)` y se decide mañana.
2. La banda de goals de 10 a 60 % frente a los márgenes de Estados Unidos, que
   llegan a 63 %. Queda como pregunta 9 al dueño.
3. Los repasos y la cuota de cotizaciones de Amazon. Resuelto: un repaso no
   cotiza.
4. El cierre de una corrida que muere. Resuelto: la siguiente la pasa a
   `abortada`, y `/salud` avisa si falta la corrida del día.
5. El aviso del fusible una sola vez por disparo. Resuelto con `avisada_at`.
6. Cómo comparte el token el escritor de Mercado Libre. Resuelto: recibe el
   refresco que ya vive en `app/reputacion_clientes.py`.

## Afirmaciones cotejadas contra el repo

Las que sostienen el diseño y el juez confirmó en el código:

- `precio_decision_coherente` exige `g.mode = NEW.mode` (0039, cerca de la
  línea 982): con el apagador en `shadow` y un goal `live`, el INSERT revienta.
- El mismo trigger asigna `NEW.goal := v_goal` desde el goal vigente.
- `precio_decision` no tiene columna `aplicado`: un `subir` sin cambio ya es
  legal.
- `ledger_event` es append-only por trigger (0001).
- `source_event_id` trae la identidad del cargo de envío por prefijo.
- `spapi_order_observation.fulfillment_channel` existe (0030).
- `SpapiWriteClient` ya admite `amazon_us`.
- `cobertura.py` clasifica `canal == 'fbm'` como `fuera_de_alcance`.
- `precio_envio_muestra.product_id` es `NOT NULL`; `seller_sku` y `asin` son
  `NOT NULL` en las tablas de estimación.

Sin cotejar a fondo: que `cerrar_por_observacion` no filtra por plataforma (se
tomó de la lectura previa del código).

## Verificación del resultado sintetizado

- `bosquejo.py` compila (`python -m py_compile`) y pasa `ruff check` y
  `ruff format` con la configuración del repo.
- `datos.sql` no se aplica: es bosquejo. Todo CHECK nuevo sobre tablas con
  filas vivas lleva `NOT VALID`.
- Ningún archivo de `app/`, `migrations/`, `tests/` ni `tools/` cambió.
