# A. Protección por venta reciente (M1) y espera por venta reciente del producto (M4): resultado

Copia revisada: `orbit07-wt` en `3c841f4` (master, contiene `7cf7c9a`). Todas las rutas son de esa copia.
Sonda: `sondas/A_venta_reciente.py`, con la salida guardada en `sondas/A_venta_reciente.txt`. Corre el código real. Solo se simula la conexión de `lee_cache`, con las mismas reglas de agregado que `_SQL_RECIENTES`.

Contexto de producción que cambia la lectura: según `docs/evidencia/estado-planes-2026-11-04/README.md:54` y `quick-wins-2026-11-04/c6-p2/elegibilidad.json`, `ads_pause_economica` estaba **ausente** hasta la config 42. Si sigue así, la "precedencia de la pausa económica" no existe en producción. La espera de M1 deja la pausa por cero pedidos sin ningún respaldo durante el episodio, y aun así está acotada a 10 días.

## Hallazgos confirmados

### H1. Un dato reciente desconocido deja pasar la pausa aunque haya un pedido reciente conocido
- Severidad: MEDIO
- Frente: dinero
- Dónde: `app/proteccion_venta.py:36-39` (pedidos = NULL si cualquier día trae `orders` NULL, aunque el ancla sí se calcula), `app/proteccion_venta.py:191-201` (sin cobertura de plataforma se borra el ancla), `app/optimizer/proteccion.py:83-88` (`pedidos is None` → `_aplica`), `app/cycle.py:1251-1254` (sin episodio y sin `hay_venta` no se protege), `app/optimizer/bid.py:261-268`.
- Escenario. Una hoja con cero pedidos maduros, clics y gasto sobre el umbral, un pedido conocido en D-3 y `orders` NULL en otro día de D-9..D-1 (por ejemplo D-7).
  - Qué pasa: la lectura queda `pedidos=None, ancla=D-3, cubierta=True`. La subpolítica devuelve `aplicar / venta_reciente_indeterminada` y `decide_pause` devuelve `pause pause_umbral`. La hoja se pausa aunque vendió hace 3 días.
  - Qué debería pasar: con un pedido reciente conocido, la pausa espera. El mismo NULL dentro de la ventana madura sí abstiene (`pause_orders_desconocido`), así que el dato desconocido frena la pausa en una ventana y la deja pasar en la otra.
  - Contradice el docstring del propio módulo (`proteccion.py:17-18`: "Fuente incompleta o métrica venenosa = desconocido: se espera") y la fila "Indeterminada → Esperar" de `m1/politica-implementacion.md:28`. La decisión 6 de ese documento solo cubre la "fuente incompleta", no el pedido conocido con un día envenenado.
- Variante transitoria, misma raíz: si falta un día completo de la plataforma en D-9..D-1 (`cubierta=False`), se borra el ancla de todas las hojas y la pausa por umbral sigue (sonda P2). Esta variante la ataja la revalidación de la cola (`apply_cola.py:1155-1174`), que relee a las 48 h, pero solo si la ingesta ya se recuperó. Con 2 o más días de ingesta caída, la pausa se aplica.
- Alcance con la config actual: se dispara hoy en live (M1 está encendido y global, y la pausa de hojas sale en vivo). Necesita un `orders` NULL en la ventana reciente de una hoja que sí vendió. No medí la frecuencia en producción. La revalidación de la cola no lo corrige si el NULL persiste, porque usa la misma lectura.
- Evidencia (sonda ejecutada, P1/P2):
  ```
  lectura: LecturaReciente(pedidos=None, ancla=datetime.date(2026, 11, 6), cubierta=True, ...)
  hay_venta: False | tiene_proteccion: False
  evalua -> aplicar venta_reciente_indeterminada
  decide_pause -> pause pause_umbral
  misma hoja SIN el NULL (control): decide_pause -> None venta_reciente_protegida
  el mismo NULL en la ventana MADURA: decide_pause -> None pause_orders_desconocido
  P2 cubierta: False ... decide_pause -> pause pause_umbral
  ```
- Cómo reproducir: `cd scratchpad/orbit07-review && PYTHONPATH=<orbit07-wt> /Users/dn/dev/goncloud-Orbit/.venv/bin/python sondas/A_venta_reciente.py`
- Para medir la frecuencia (no ejecutado, solo lectura): `SELECT e.platform, count(*) FROM v_metric_latest v JOIN ad_entity e ON e.id=v.ad_entity_id WHERE e.kind IN ('keyword','product_target') AND v.orders IS NULL AND v.metric_date >= current_date - 30 GROUP BY 1;`

### H2. Hueco de fechas: la venta de una hoja que se queda sin impresiones no la ve ninguna de las dos ventanas
- Severidad: MEDIO (el hueco existía antes de ORBIT 07; M1 no lo cierra si no abrió un episodio a tiempo)
- Frente: dinero
- Dónde: `app/optimizer/windows.py:413-421` (la ventana de cortes termina en `min(max(metric_date) DE LA HOJA - 3, D-10)`), `app/proteccion_venta.py:32,159-161` (la lectura reciente es fija, D-9..D-1), `app/optimizer/proteccion.py:75-76` (el estado indeterminado solo existe si ya hay episodio).
- Escenario. La hoja vende el día A y luego deja de tener filas, por ejemplo porque el producto se agota y Amazon deja de servir el anuncio. Su última fila es A (o A+2).
  - Qué pasa: desde D = A+10, la ventana madura termina en A-3 (o A-1) y la reciente empieza en A+1. El día A no está en ninguna. La lectura reciente da `pedidos=0` y la pausa procede. La exposición dura unos 5 ciclos, hasta que la hoja cae en "inerte" (14 días sin impresiones, `migrations/0082_claridad01_entidad_inerte.sql`). Al volver el stock, la keyword sigue pausada y se pierden ventas.
  - Qué debería pasar: la lectura reciente debería empezar donde termina la ventana de cortes de esa hoja, o una venta en el hueco debería contar como reciente.
  - M1 sí la protege si abrió el episodio mientras la venta estaba en D-9..D-1: con episodio vencido y ancla fuera de la ventana madura, devuelve `indeterminada`. Sin episodio previo no la protege. Eso pasa si el umbral del ad group (`cortes.umbral_corte` cambia a diario) no calificaba ese día y sí califica después, si la hoja estaba saltada por veto o clave en vuelo, si ese día faltó cobertura, o si la venta fue anterior al encendido del 2026-11-08.
- Alcance con la config actual: live hoy. Condición estrecha: agotado más el umbral cruzado después de que la venta salió de D-9..D-1.
- Evidencia (sonda ejecutada, P3):
  ```
  ultima fila 2026-10-30 venta 2026-10-30: ventana madura 2026-09-28..2026-10-27, reciente 2026-10-31..2026-11-08
    venta dentro de la madura: False
    sin episodio previo -> decide_pause: pause pause_umbral
    con episodio abierto antes -> decide_pause: None venta_reciente_indeterminada
  ultima fila 2026-11-01 venta 2026-10-30: ventana madura 2026-09-30..2026-10-29 ...
    sin episodio previo -> decide_pause: pause pause_umbral
  ```
- Cómo reproducir: el mismo comando de H1.
- Para medir cuántas hojas hay hoy en el hueco (no ejecutado): `WITH m AS (SELECT ad_entity_id, max(metric_date) mx FROM v_metric_latest GROUP BY 1) SELECT v.ad_entity_id, m.mx, v.metric_date, v.orders FROM v_metric_latest v JOIN m USING (ad_entity_id) WHERE m.mx BETWEEN current_date - 14 AND current_date - 8 AND v.orders > 0 AND v.metric_date > m.mx - 3 AND v.metric_date < current_date - 9;`

### H3. M4: un día reciente con una métrica ausente se descarta en silencio y la pausa del producto procede
- Severidad: MEDIO (hoy solo en shadow)
- Frente: dinero
- Dónde: `app/productos_io.py:310` (`dias_recientes, _ = _dias_halo(...)` tira el indicador de faltantes), `app/productos_io.py:574-578` (el día con cualquier métrica NULL se salta), `app/optimizer/productos.py:400-412`, `app/apply_producto.py:175-187` (la revalidación usa la misma lectura y repite el descarte).
- Escenario. Un producto sin compras ni ventas en [D-60, D-30) y sobre los límites. En D-5 hay una fila con `purchases30d=1, sales30d=399`, pero `attributed_sales_same_sku_30d` NULL.
  - Qué pasa: ese día no llega a `dias_recientes`, `venta_reciente_producto` devuelve None y `motivos_economicos_pausa` queda vacío. Se propone la pausa.
  - Qué debería pasar: un día con dato desconocido no puede probar "sin venta reciente". Debería dejar un motivo y no pausar, como ya hace la ventana madura (`_actividad` → `ads_con_metrica_ausente`).
  - En la ventana del halo el mismo descarte sí deja el impedimento `ads_con_metrica_ausente`, pero `motivos_economicos_pausa` solo copia `sku_distinto_en_asin` (`productos.py:430`). Ese impedimento no frena la pausa.
- Alcance con la config actual: la pausa por producto está en shadow en MX y US, así que hoy no mueve dinero. Se vuelve dinero real el día que el dueño la encienda en live. M4 no tiene interruptor y no se puede apagar sin un despliegue.
- Evidencia (sonda ejecutada, P5):
  ```
  dias_recientes: () | faltantes (descartado en productos_io.py:310): True
  venta_reciente_producto: None
  motivos_economicos_pausa (vacio = la pausa procede): ()
  cumple_limites_pausa: True
  ```
- Cómo reproducir: el mismo comando de H1.

## Sospechas sin confirmar

1. **Ingesta caída 2 o más días y pausa de hojas.** Es la variante transitoria de H1: si la ingesta de hojas falla el día de la decisión y sigue caída el día de la revalidación (48 h después), la pausa se aplica sobre una hoja que vendió. Para confirmarlo hay que cruzar las corridas `ingest_run ok=false` de keywords con pausas aplicadas cuya hoja tenía `orders>0` en D-9..D-1 del momento de liberar. No ejecutado.
2. **Pujas que se mueven con una pausa pendiente por cuota.** `tiene_proteccion` (`proteccion_venta.py:98-111`) es True con cualquier episodio histórico, aunque esté consumido hace meses. Una hoja con fila released esperando cuota (no protección) vuelve a juzgar pujas cada ciclo. Con pedidos antiguos en D-90..D-40, R6 podría subirle la puja +15 % mientras su pausa espera cuota. Está declarado como diseño ("activo o vencido") y no vi un caso real. Para confirmarlo: un ciclo sobre PostgreSQL desechable con una fila released `sin_quota`, un episodio viejo y `propia` con pedidos solo en el tramo antiguo. No ejecutado.

## Zonas revisadas sin hallazgos

- **Fechas del episodio M1**: ancla fija; espera mientras `D < ancla+10`; el día `ancla+10` la ventana de cortes termina justo en el ancla y libera. Sin error de un día (sonda P4: 11-08 espera, 11-09 consumida con `ventana_hasta=2026-10-30`). Todo en fecha UTC de `decided_at`, igual que la ventana de cortes. La ingesta corre a las 07:10 UTC y el ciclo a las 08:40 UTC, así que D-1 ya tiene filas (la muestra `m0/produccion-por-hoja` trae `ultima_fila = D-1`).
- **Renovación indefinida**: `_abre_otro` (`proteccion.py:96-108`) solo abre otro episodio si el ancla vieja ya salió de la ventana madura. El estado indeterminado "para siempre" exige que la hoja no tenga filas, es decir, que no gaste. No encontré un camino que deje sin pausar, por tiempo indefinido, una hoja que sigue gastando.
- **Bajadas de puja**: la protección solo suprime la pausa y el juicio sigue (`politica.py:225-228`). Lo que frena recortes durante la espera es R8 (`pedidos_inmaduros >= 1`), que es anterior a ORBIT 07.
- **Cola (P7)**: la revalidación lee su propia caché después del commit de TX3 (`cycle.py:3792-3833` y `apply_cola.py:1576`), con el mismo `ahora` y la misma subpolítica pura. Ve los episodios que el ciclo acaba de abrir. La espera devuelve antes de `consume_quota_y_sello` y del claim (`apply_cola.py:1629-1635`), así que no cobra cuota ni reinicia el veto. Al vencer con pedido maduro descarta `vendio_en_ventana`; si el pedido se corrigió a cero, aplica.
- **Append-only y duplicados**: hay restricción única `(platform, entidad, version, ancla)` con `ON CONFLICT DO NOTHING`, tanto en TX3 como en la cola. Los dos ciclos de una misma plataforma quedan bajo el mismo candado. Las anclas solo crecen. El trigger `vence_el = ancla+10` y el CHECK `observado_el <= abierta_el` coinciden con el cálculo del código (`proteccion_venta.py:144`, `caso.py:34`). La FK siempre existe y `app_decide` tiene INSERT y USAGE de la secuencia. No encontré un error de inserción determinista que tumbe TX3 o la cola.
- **Vocabulario `decision_sin_aplicar`**: el CHECK de 0081 coincide uno a uno con `apply.MOTIVOS_SIN_APLICAR`. `discard_motivo` no tiene CHECK, así que `venta_reciente_producto` como motivo de descarte no atora la cola.
- **Interruptor (P5)**: si se quita la clave, el ciclo y la cola vuelven a la semántica histórica. El valor `off` lo quita el CLI (`config_write.py:339-342`). Un valor ajeno lo rechaza `config_write.py:511` antes de guardar. Escrito a mano en la base, tumba los ciclos de las dos plataformas (ValueError en TX2), igual que cualquier otro setting corrupto.
- **Shadow contra live**: los episodios dependen solo de los datos, no del modo. Abrirlos en shadow no cambia lo que live habría decidido.
- **M4 contigüidad**: la ventana reciente [D-30, D) y la madura [D-60, D-30) son contiguas. La venta espera hasta D = ancla+30 y entra a la madura en ancla+31. Una ingesta de productos caída deja fechas maduras sin observación madura, se marca `fecha_inmadura` y no hay pausa (falla del lado seguro). En live, la revalidación descarta con `venta_reciente_producto` antes de cobrar.
