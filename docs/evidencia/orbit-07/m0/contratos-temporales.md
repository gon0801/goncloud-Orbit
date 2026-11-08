# M0: contrato temporal de hojas (M1)

Fecha: 2026-11-07 (UTC). Base: `f86ed7dcd77f0af7b540217c3df9422ebe5c7154`.

## Ventanas

| Ventana | Rango | Fuente | Uso |
|---|---|---|---|
| Reciente | `[D-9, D-1]` | `v_metric_latest`, `orders > 0` | Abre la proteccion; el ancla es la `metric_date` mas antigua con pedido |
| Madura de cortes | 30 dias inclusive, fin `min(max(metric_date)-3, D-10)` | `windows.ventana_cortes` | Decide la pausa por umbral |
| Proteccion | `[ancla, ancla + 10]` UTC | Episodio persistido | La pausa por cero ventas espera hasta `vence_el` |

`D` es la fecha UTC de `decided_at` (o del reloj de liberacion en la cola).
Nunca se usa la fecha local ni la de ejecucion para el ancla.

## Reglas

1. **Cobertura.** Un dia de `D-9..D-1` esta cubierto si hubo ingesta de alguna
   hoja keyword/product_target de la plataforma. Con el rango completo, una
   entidad sin filas ese dia cuenta cero MEDIDO; una metrica NULL o negativa es
   desconocida (`None`), jamas cero.
2. **Fuente incompleta.** Sin cobertura completa no se abre una gracia nueva ni
   se convierte la ausencia en cero. Un episodio ya activo se conserva con la
   lectura incompleta.
3. **Ancla fija.** El ancla se congela al abrir el episodio. Otra corrida, una
   reobservacion, una propuesta o una venta posterior NO la mueven ni extienden
   `vence_el`.
4. **Vencimiento.** Al cumplirse `ancla + 10`, el ancla ya debe estar dentro de
   la ventana madura. Si aun no lo esta (rezago de ingesta), el estado es
   indeterminado y la pausa sigue esperando; no se libera con dato inmaduro.
5. **Correccion a cero.** Si al vencer el ancla sigue con cero pedidos maduros,
   el pedido ancla se corrigio a cero: la proteccion queda consumida y la pausa
   procede si sus requisitos siguen cumpliendose.
6. **Pedido maduro.** Si el ancla entro a la ventana madura con pedido, la
   condicion de cero ventas desaparece; no hay pausa y el episodio queda
   cerrado por la propia regla.
7. **Sin renovacion sucesiva.** Un pedido inmaduro posterior no renueva un
   episodio consumido. Solo se abre otro episodio si el ancla anterior ya salio
   de la ventana madura (condicion nueva, no una gracia encadenada).
8. **Descenso de gasto.** Un descenso pasajero de gasto no abre un episodio.
9. **Pujas.** La espera solo suprime la pausa por cero ventas. El juicio de
   pujas sigue con el mismo evaluador; una puja autorizada conserva su
   aplicacion. La pausa economica con datos completos conserva precedencia.

## Contrato temporal de producto (M4)

| Ventana | Rango | Fuente | Uso |
|---|---|---|---|
| Reciente inmadura | `[D-30, D-1]` | reporte de productos hasta D-1 (no el watermark de hojas), última observación por fecha | Una compra o venta total ahí deja la pausa en espera |
| Madura de la pausa | `[D-60, D-30)` | reporte de productos | Cero compras y ventas: la pausa procede |

1. **Madurez.** La fuente de productos reobserva cada fecha hasta los 30 días
   (`productos.MADUREZ_PRODUCTO`; AUTO-03 E.4 midió una reatribución en el día
   31). Una fecha con menos de 30 días aún puede cambiar. No se reutiliza el
   vencimiento de 10 días de M1.
2. **Frescura.** La espera lee las observaciones del reporte de productos hasta
   D-1 (`productos_io._lee_observaciones` extiende el rango y arma
   `dias_recientes`). No usa `dias_halo` ni el watermark de hojas, que termina
   en `watermark - 3` y dejaría fuera las ventas de los últimos días.
3. **Ancla.** La fecha más antigua con una compra o venta total observada en
   la ventana reciente. `vence_el = ancla + 30 días`.
4. **Reobservación.** Corregir es una fila nueva. Una reobservación del ancla
   no la mueve; una venta posterior no mueve el ancla mientras la anterior
   siga inmadura.
5. **Contigüidad y liberación.** El día de madurez (`D = ancla + 30`) la venta
   sigue en la reciente, así que la pausa espera; `libera_el = ancla + 31`, el
   primer día en que la venta entra a `[D-60, D-30)` y cuenta como
   `vendio_en_ventana` (o se corrigió a cero y la pausa procede). No hay hueco
   entre las dos ventanas.
6. **Decide y revalida.** La misma subpolítica pura. La fila en cola se
   descarta con `venta_reciente_producto`, sin PUT ni cuota; se conserva el
   único `kind='pause'` sobre `product_ad` y el ancla no se renueva al
   revalidar.
7. **Otro SKU.** Una venta de otro SKU (ventas totales > ventas del mismo SKU)
   también deja la pausa en espera: no se puede afirmar cero ventas.

Evidencia: [`../m3m4/comparacion-auto03.md`](../m3m4/comparacion-auto03.md),
[`../m3m4/aprendizaje.json`](../m3m4/aprendizaje.json),
[`../m3m4/sonda_aprendizaje.py`](../m3m4/sonda_aprendizaje.py),
[`../m3m4/regresion-venta-reciente.txt`](../m3m4/regresion-venta-reciente.txt) y
[`../m3m4/regresion-frescura-venta.txt`](../m3m4/regresion-frescura-venta.txt).

## Evidencia local

- El caso sintetico de 500 MXN, 120 clics, cero pedidos maduros y un pedido
  reciente da `Pausar(pause_umbral)` en la base y `Mantener(venta_reciente)` con
  la subpolitica nueva; el episodio queda en `ancla 2026-10-05` y
  `vence 2026-10-15`.
- Pruebas: `tests/test_optimizer_proteccion.py` (puro) y
  `tests/test_orbit07_m1.py` (ciclo y cola sobre PostgreSQL desechable).
- El SELECT de produccion: la revision independiente Astra ejecuto la version
  inicial (ventana fija D-39..D-10) con `orbit_read` y `READ ONLY`; devolvio
  dos hojas por mercado con pedido reciente y cero pedidos maduros
  ([`produccion-2026-11-08.json`](produccion-2026-11-08.json)). La consulta
  corregida con la ventana por hoja
  ([`consulta-pedidos-recientes.sql`](consulta-pedidos-recientes.sql)) sigue
  pendiente de ejecucion: este encargo no tiene acceso de lectura a produccion.
  La muestra no demuestra pausas elegibles ni cuantifica perdidas.
