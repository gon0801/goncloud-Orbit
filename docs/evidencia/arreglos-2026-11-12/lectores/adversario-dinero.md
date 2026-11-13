# Lector adversario: dónde estos planes pueden costar dinero o dañar datos

Fecha: 2026-11-12. Árbol leído: `la copia de trabajo del plan` (rama `plan/arreglos-02`, código = master `a4644aad`).
Lecturas de producción: solo SELECT con `docs/evidencia/arreglos-01/lee_prod.sh`. Prototipos fuera del repo, en el scratchpad de la sesión
(`proto_c.py` corre la política real de `a4644aad` sobre las 49 decisiones `niveles_v3` bajadas de producción y sustituye
`estado_grupo` por la forma C1 del diseño).

Resultado: **2 bloqueantes y 6 no bloqueantes**. Están ordenados por el dinero que tocan.

---

## 1. BLOQUEANTE. ARREGLOS 02, bloque C1: después de D, todo lo que C1 cambia son keywords que pierden dinero

**Escenario con números (decisión real 2865, hoja 4934, Amazon US).** El grupo de anuncios de la hoja 4934 es casi solo ella
(13 de 13 pedidos y 98 % del gasto). Sus datos en el tramo reciente son 291.74 USD de gasto y 646.18 USD de venta, o sea 45 % de ACoS.
El ACoS que usa la política (`fondo.acos_hoy_pct`) es 33.0 %. El equilibrio era 24.46 % y con D pasa a cerca de 29-30 %.
Hoy R4 la recorta −12 % («grupo_sangra_vendedora»). Con C1 el resto del grupo tiene 0 pedidos y queda `sin_veredicto`, así que R4
no dispara. R3 tampoco dispara, porque `p_sobre(equilibrio)` = 0.47 con D, menos que la confianza de 0.80, y la hoja queda en
`Mantener`. La prueba del propio plan fija este resultado: la fila C.1 espera que el fixture 2865 con revisión 2 dé
`Mantener("vende_dentro_del_margen")`, y C.4 lo comprueba en producción.

**Recorrido (prototipo sobre las 49 decisiones reales).** Con la economía guardada en cada decisión, C1 cambia 8 decisiones, igual
que mide el diseño. Pero D se despliega antes que C, así que la economía con la que corre C es la de D: metas de 22.75 y 23.19 y
equilibrios de 45.5 y 29.0. Con esa economía, C1 cambia solo 3 de las 49, y las 3 son hojas cuyo ACoS supera el equilibrio:

| Decisión | Hoja | ACoS de la política | Equilibrio con D | p_sobre(eq) | Hoy | Con C1 |
|---|---|---|---|---|---|---|
| 2865 | 4934 | 33.0 % | 30.0 % | 0.47 | Mover −12 % | Mantener |
| 2867 | 5567 | 34.7 % | 30.0 % | 0.33 | Mover −12 % | Mantener |
| 2902 | 5891 | 30.2 % | 29.4 % | 0.40 | Mover −12 % | Mantener |

(Salida de `proto_c.py`, líneas `conD`.) Para que R3 llegue a 0.80 con un ACoS 15 % sobre el equilibrio, el modelo pide unos
30 pedidos en la ventana madura. Estas hojas tienen 6 a 13. Mientras el resto de su grupo no junte 3 pedidos, nunca se recortan.

**Dinero hoy.** La pérdida del tramo reciente (40 días) contra el equilibrio de 29 % es gasto − 0.29 × venta.
4934: 291.74 − 187.39 = 104 USD. 5891: 371.67 − 259.83 = 112 USD. 5567: 92.23 − 59.74 = 32 USD.
En total son unos 248 USD cada 40 días, cerca de 186 USD al mes (unos 3,400 MXN). Es el 16 % del gasto de Ads de US
(20,768 MXN en 30 días, del libro). La frase del aviso, «habría evitado 8 de los 15 recortes», se midió con la economía anterior a D.

**Corrección pegable.**
- `docs/evidencia/arreglos-02/DISENO.md`, `## C1`, seudocódigo de `_estado_heredado`. Agregar como primera regla después de la de revisión:
  `# if vendedora and caso.economia.plataforma.equilibrio_acos_pct is not None and acos_hoy_pct > equilibrio: return estado_grupo(caso.grupo, caso.economia)   # pierde dinero: hereda del grupo completo, como hoy`
  Para eso `_estado_heredado` recibe `acos_hoy_pct` (el `fondo.acos_hoy_pct` que ya calcula `_juzga_vendedora`), y R4 pasa a
  juzgarse dentro de `_juzga_vendedora` con ese estado.
- `plans/arreglos-02.md`, C.1. En lugar de «el fixture `arreglos02_decision_2865.json` con revisión 2 da `Mantener("vende_dentro_del_margen")`», poner
  «con revisión 2 sigue dando `Mover(-0.12, 0.30, "grupo_sangra_vendedora", "ad_group")`: su ACoS (33.0 %) supera el equilibrio (24.46 %)».
  C.4 cambia igual.
- Aviso de C: quitar «En el último mes eso habría evitado 8 de los 15 recortes de ese tipo». Poner «Si la keyword pierde dinero
  (ACoS sobre el punto de equilibrio), se sigue juzgando con su grupo completo».
- Otra salida igual de válida: sacar C1 del plan y dejar solo C2. Con D primero y la guarda de arriba, C1 cambia 0 de las 49 decisiones.

---

## 2. BLOQUEANTE (datos). ARREGLOS 04, bloque B-D: un error de lectura de Amazon dispara el «quitar» de un bloqueo que sí funciona

**Escenario.** El script `lee_amazon.py` puede dar cinco estados por bloqueo: ENABLED, PAUSED, ARCHIVED, AUSENTE o `LECTURA_<estado>`.
La fila B-D.2 dice: «Por cada línea de `antes.txt` con estado distinto de ENABLED: `quitar`…». Eso incluye `LECTURA_*`, que es un
fallo de lectura y no dice nada del negative. Ejemplo: Amazon responde 429 o 5xx a la lista del ad group de «centenario de oro puro»
(MX), que hoy está ENABLED. El script imprime `LECTURA_error` y el operador corre `quitar`. El delete archiva el negative que sí
bloqueaba, y archivar no se puede deshacer en Amazon. Como la relectura también puede fallar, la respuesta trae `reversa_intencion`.
B-D.2 manda parar, pero el borrado ya ocurrió. Esa clave no tiene señal vigente «ajena» (medido: `sin_lectura|1`), así que
B-D.2 no la vuelve a aprobar y el término queda sin bloqueo hasta que reaparezca su señal, es decir, hasta que vuelva a gastar.
Además, B-D.3 exige que el script salga 0, y eso empuja a «reparar» también las líneas `LECTURA_*`.

Un segundo camino lleva al mismo lugar. Si el negative guardado está PAUSED pero en el grupo hay otro ENABLED del mismo término
(del harvest o puesto a mano), el término ya está bloqueado. Aun así, el `aprobar` de B-D.2 adopta ese otro negative
(B-B `_negative_que_bloquea`) y lo registra como del dueño. El siguiente «quitar» del dueño archiva un negative que no es suyo.

**Dinero y entidades hoy.** Son 48 bloqueos vivos: 25 en MX y 23 en US. Las señales vigentes de esos términos suman 481 MXN de gasto
sin venta (2-oct a 2-nov) y 58 USD (25-may a 2-nov). El dinero es poco. Lo grave es el dato: un bloqueo que funcionaba se pierde
sin reversa automática, y eso va contra la regla 7.

**Corrección pegable** (`plans/arreglos-04.md`, fila B-D.2, primera frase):
«Por cada línea de `antes.txt` con estado PAUSED, ARCHIVED o AUSENTE, en orden. Una línea `LECTURA_*` no se toca: se vuelve a
correr el script para esa clave, y si sigue sin lectura se anota en `reparacion.txt` como "sin lectura, no se tocó". Si el script ve
en el mismo grupo otro negative ENABLED del mismo término, no se corre ni `quitar` ni `aprobar`: el término ya está bloqueado y se
anota "bloqueado por otro negative <id>".»
En B-D.3, la salida 0 admite también las claves anotadas «sin lectura». Hace falta que `lee_amazon.py` imprima, por clave, si hay
otro ENABLED del mismo término; agregarlo a B-D.1.

---

## 3. No bloqueante. ARREGLOS 02 D con el IVA de México: la meta sube y el error del IVA, de tamaño parecido y signo contrario, sigue

**Escenario con números.** Pedido 701-5496313-1052217: venta de 2,288.00 MXN con IVA. Amazon retiene 157.80
(`MarketplaceFacilitatorVAT-Principal`), que es el 8 % de 1,972.41, la mitad del IVA. La otra mitad (157.80) la paga el vendedor
al SAT, descontado su IVA acreditable, y el libro no la resta. En la ventana de la meta, la venta de MX es de 539,231 MXN y la
retención de 36,837. La otra mitad pesa hasta 6.9 puntos de margen.
- Margen de MX sin el duplicado: 45.50 %. Lo reproduje con la vista de D aplicada a mano y da lo mismo que el diseño. Con la mitad
  del IVA como costo quedaría entre 38.6 % y unos 42 %, según el IVA acreditable.
- La meta de MX (0.5 × margen) pasa de 20.13 a 22.75 %. Si el contador dice que esa mitad es costo, la meta correcta queda entre
  19.3 y 21 %. D aleja la meta de la correcta en lugar de acercarla.
- R3 («pierde dinero») usaría 45.5 % y no la cifra correcta de 38.6-42 %. Medido: 1 keyword vendedora de MX tiene hoy ACoS en
  esa banda (gasto de 451 MXN y venta de 1,076 en 40 días). Con D, R3 no la recortaría aunque pierda dinero.

**Dinero.** Por R3: unos 340 MXN al mes. Por la meta: el gasto de MX es de 18,470 MXN en 30 días, y una meta 13 % más alta
permite hasta unos 2,400 MXN de gasto extra al mes. Ese gasto sigue debajo del equilibrio, así que no es pérdida por venta, pero
se sale de la regla del dueño (la mitad del margen). US no tiene este problema: el impuesto de US se retiene completo.

**Corrección pegable** (`plans/arreglos-02.md`, decisión 2): «El IVA dentro del margen se le pregunta al contador. Mientras no
conteste, D se despliega igual, y el aviso de D dice: "si el contador confirma que la mitad del IVA que Amazon no retiene es costo
tuyo, tu margen de México baja hasta 6.9 puntos y la meta vuelve a quedar cerca de 20 %".» Si el dueño prefiere no subir la meta
de MX antes de esa respuesta, D.4 deja `ads_target_*` de MX fijo en la meta de hoy hasta entonces.

---

## 4. No bloqueante. ARREGLOS 03, bloque P-E: una variante cara con 2 unidades baja el precio de toda la publicación entre 10 y 26 %, y la regla de 7 días lo deja así aunque vuelva a tener existencias

**Escenario con números (publicación 1586).** Las variantes y su costo más envío en MXN: COR 245 con 2 en existencia, VBU 182 con
82, PEZ 172 con 2 y otras en 0. Hoy manda COR. Si se venden esas 2 unidades de COR, la siguiente estimación (cada 6 h) pone a VBU
como la que manda. El precio objetivo baja en la misma proporción que la carga, un 25.7 %, y el motor decide `bajar`. Al volver a
haber COR, la subida queda frenada por la regla de 7 días (`mantener` con motivo `cooldown`). Durante hasta 7 días, COR se vende al
precio calculado para VBU: unos 63 MXN menos de contribución por unidad (sigue positiva; la contribución de la que manda es 394).

**Cuántas hoy.** El diseño dice que cambian 3 publicaciones, entre 1.0 y 1.6 %. Esa cifra es la foto de hoy. Medido con la misma
consulta del diseño, agrupada por variante: hay 7 publicaciones donde la variante que manda tiene 2 unidades. Al venderlas, la que
manda pasa a una más barata con existencias:

| Publicación | Baja del precio objetivo |
|---|---|
| 1586 | −25.7 % |
| 1573 | −17 % |
| 1576 | −16.5 % |
| 1572 | −16 % |
| 1589 | −15 % |
| 1566 | −11 % |
| 1568 | −10 % |

El «Riesgo declarado» del diseño («hasta 1.6 %, el daño es chico») no ve este caso.

**Corrección pegable** (`DISENO-precios.md`, P-E, `_SQL_SIN_EXISTENCIA`): «Solo cuenta como sin existencias una variante con
`stock = 0` en la observación de ayer **y** en la de hoy.» En «Riesgo declarado», reemplazar la última frase por «Hoy 7
publicaciones tienen 2 unidades en la variante que manda; al venderlas el precio baja entre 10 y 26 %, y si la variante vuelve, la
regla de 7 días la deja a ese precio hasta 7 días.» Que el dueño confirme la decisión 4 con este número.

---

## 5. No bloqueante. ARREGLOS 04, bloque B-C se despliega antes de medir cuántas páginas tiene la lista de Amazon

**Escenario.** Con B-C, `_lista_todos` lanza `ListaIncompleta` si la lista pasa de 20 páginas. Los siete usos del harvest leen la
cuenta completa (`/sp/keywords/list` con cuerpo `{}`). MX tiene 2,719 keywords contando las archivadas, que la lista también
devuelve. Si Amazon pagina de 100 en 100, son 28 páginas. Desde el despliegue, todo harvest y toda reconciliación de negatives de
MX se detienen para siempre: nada pasa a exacta ni se niega en origen. El diseño lo reconoce («con páginas de 100… MX ya lo pasa»)
y deja la medición para B-D, que no tiene orden respecto de B-C.

**Dinero.** Depende del tamaño de página, que no está medido. Los 8 harvests de MX de octubre terminaron bien, lo que sugiere
páginas grandes, pero no lo prueba.

**Corrección pegable** (`plans/arreglos-04.md`, B-C): `depende: B-D.1` y una fila B-C.0: «Antes de desplegar, `antes.txt` de B-D
dice páginas leídas por plataforma y lista; si alguna pasa de 15, B-C no se despliega y se escribe en `docs/evidencia/arreglos-04/B-C/diferencia.md`.»
B-D.1 es de solo lectura, así que puede ir primero sin riesgo.

---

## 6. No bloqueante. ARREGLOS 04, W-M: «vendió» se mira solo en el libro, sin revisar que esté al día ni que la venta tenga producto

**Escenario.** `listings_sin_pedido` cuenta `ledger_event kind='sale'` por `product_id` en 330 días. Medido en esos 330 días:
180 ventas de MX (217,117 MXN), 63 de US (142,855 MXN) y 35 de MeLi tienen `product_id` vacío. Una venta así no cuenta para
ningún producto. Si un producto vende solo por SKUs sin mapear, o si la sincronización de contabilidad se cae varios días, W-M
puede sacar sus anuncios aunque haya vendido. Es una pausa con registro y con reversa, así que no es irreversible. Medido hoy:
de los 86 productos «sin pedido» de MX y los 34 de US, ninguno tiene pedidos atribuidos por Ads en 60 días.

**Corrección pegable** (DISENO W-M, `planea_retiro`): «Si la última corrida ok de `accounting_ledger_events` tiene más de 2 días,
`planea_retiro` con motivo `sin_pedidos` no saca nada y dice "las ventas no están al día: no se puede comprobar que no vendió".
Un listing con pedidos atribuidos por Ads (`ads_product_metric_observation.purchases_same_sku30d > 0`) en 60 días también va a `vendieron`.»

---

## 7. No bloqueante. ARREGLOS 03, D-H: la migración corrige filas de todas las plataformas y la reversa solo las de MeLi

**Escenario.** La ida corre `UPDATE ... WHERE kind = 'refund' AND fee_type IS NULL`, sin filtrar plataforma. La reversa solo
regresa `platform = 'meli'`. Hoy las 60 filas sin etiqueta son de MeLi, pero la migración corre el día del despliegue, no el de
D-H.1. Si en medio entra una devolución de Amazon sin etiqueta, la ida la etiqueta y se suma al margen de Amazon. Sin la ida,
`fees_sin_tipo` habría dejado ese margen en vacío, que es el lado seguro. La reversa no la regresa. El aviso dice «Amazon no
cambia». Cuántas toca hoy: 0, porque todas las devoluciones de Amazon traen etiqueta `refund`.

**Corrección pegable** (DISENO-datos, «DEV», y fila D-H.4): `UPDATE ledger_event SET fee_type = 'refund' WHERE platform = 'meli' AND kind = 'refund' AND fee_type IS NULL;`
La sonda queda `SELECT NOT EXISTS (SELECT 1 FROM ledger_event WHERE platform = 'meli' AND kind = 'refund' AND fee_type IS NULL)`.
D-H.1 compara en ese momento los ids con la lista de `antes.txt`.

---

## 8. No bloqueante. ARREGLOS 03, D-G: una fila con pedidos vacíos sigue aceptada, y la señal JEV la suma como 0

**Escenario.** D-G envenena la clave si la métrica es ilegible, si la fila no trae ninguna métrica o si la suma queda vacía. Pero
una fila con clics y gasto y `purchases7d` vacío no entra en ninguno de esos casos: se guarda con `orders` en NULL.
`jev_senales._SQL_HISTORIAL` suma `COALESCE(sum(orders) FILTER (WHERE orders IS NOT NULL), 0)`: un día con 2 pedidos que llegó
vacío cuenta 0 pedidos y sus clics sí cuentan. Medido: 0 filas de search terms con `orders` NULL en 90 días (250,281 filas).
JEV cuenta esos días aparte (`dias_sin_dato`). El riesgo es solo a futuro.

**Corrección pegable** (DISENO-datos, «ST», tabla del seudocódigo): agregar la línea
`fila con clics o gasto y pedidos vacíos -> _envenenar_clave(clave, ...); skips["fila con pedidos vacíos"] += 1; seguir`.

---

## Intenté y no pude

- D (vista `v_ledger_cargo`), dos productos en un pedido: 0 de los 362 pedidos con estimada tienen más de un producto o más de una estimada.
- D, pedidos sin id: 0 estimadas y 0 comisiones reales sin `order_id`.
- D, devoluciones o un cargo real de otro tipo con la misma etiqueta: las filas `referral_fee` de finanzas `refund` (30 MX, 21 US)
  y `RefundCommission` (8 MX, 4 US) sí borrarían la estimada si llegaran solas. Hoy los 5 pedidos con estimada que las tienen
  también tienen su `Commission`. No hay ningún caso en que solo haya llegado la devolución.
- D, cargos con fecha fuera de la ventana: la rama con orden de las cuatro vistas no filtra por fecha (`0016:57-59`), así que quitar
  una estimada nunca deja un pedido sin comisión por la ventana.
- D, ¿infla el margen?: con la vista aplicada a mano reproduje 45.50 % y 28.99 % (iguales al diseño). La comisión real es mayor que
  la estimada en 257 de 257 pedidos de MX (−46,969 contra −34,763 en total): quitar la estimada nunca deja menos que la real.
- D, definiciones vigentes de las vistas: ninguna migración posterior a 0016, 0018, 0021 y 0048 las redefine, y las líneas que cita D.2 coinciden.
- B (centavos), bids chicos: el umbral para moverse es el mismo que hoy, porque el delta mínimo de 0.01 es un centavo. Un recorte
  de −12 % se queda quieto desde 0.08 USD hacia abajo y una subida de +15 % desde 0.06 USD hacia abajo, igual que hoy. El redondeo
  nunca va al revés (`bid_antes`: 0 de 496 sin centavos), nunca sale del piso o del techo, y R1 recibe `bid_antes` ya en centavos.
- D-E (horarios), días incompletos o días enteros sin correr: a las 09:40 UTC el último día cerrado coincide para MX y US en invierno
  y en verano. Ningún consumidor corre entre las 00:00 y las 08:00 UTC (JEV a las 09:30 y 21:30 da el mismo día). El diario vuelve a
  bajar 31 días cada mañana, así que un día de US incompleto se corrige al día siguiente. Además, la ventana de bids termina en el
  último día menos 3.
- D-I (importe en dólares): no cambia `amount`, así que el margen de Amazon no se mueve. Ninguna venta tiene monto ≤ 0, así que el
  CHECK de signo no rechaza filas existentes. La reversa pierde los dólares guardados, pero eso es esperado.
- P-C: 37 de 37 cambios reales se confirmaron con la observación del día siguiente, así que «error y el precio sigue el de antes»
  no confunde un cambio lento con uno sin efecto. Un error con otro precio sigue contando para los 7 días.
- P-D: «Soltar» va por corrida (todas las variantes de la plataforma juntas), así que soltar una parte no deja pasar sola a la otra.
  Una retención sin soltar se vuelve a decidir y a avisar cada día; no se queda colgada.
- W-I: los 9 goals vigentes tienen meta derivada (NULL) y pujas dentro del rango (techo máximo de 45 MXN y 2.50 USD). El bucle solo
  revisa lo que cambia, así que ningún goal válido queda fuera.
- W-L: el grupo de cinco es de un solo conjunto de productos y la exacta anuncia los mismos productos. El código del harvest ya
  incluye `product_targeting` entre las campañas donde niega (`ROLES_DISCOVERY`, `apply_harvest.py:142-147`); no comprobé en
  Amazon que ese negative se haya creado alguna vez. No encontré un producto del grupo que pierda su única búsqueda que vende.
- TEMPORADA 01, el equilibrio de 45.5 y 29.0: con mi propia consulta da 45.50 y 28.99 con el costo de cada venta, y 46.37 y 29.25
  con el costo de hoy, que es lo que usa I.2. Por producto, los márgenes de Amazon van de 31.3 a 48.0 % antes de D. Aun restando
  la mitad del IVA en MX, ninguno queda debajo de 25 %. El parche no le quita a TEMPORADA ningún aviso de pérdida que debiera
  salir, y la pantalla lo calcula en vivo de todos modos.
