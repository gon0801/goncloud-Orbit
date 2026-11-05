# AUTO-03: comparacion estructural de candidatos

**Eleccion revisada: A como base.** Su `f * margen%` puede ser un target ACoS
estimado por producto, no una garantia de utilidad. B propone un control TACoS
mas directo, pero hoy no hay costo Ads para toda su ventana financiera.
Ambas politicas son propuestas, no autorizaciones para ejecutar pausas.

| Criterio (0-3) | A | B | Juicio |
|---|---:|---:|---|
| 1. Autoridad unica | 2 | 2 | Ambos delegan el PUT de product_ad a `retiro_anuncios`; falta cerrar la integracion con la cola actual. |
| 2. Economia y atribucion | 2 | 1 | A estima un target ACoS sin afirmar utilidad causal; B formula bien TACoS, pero carece hoy de costo para su ventana completa. |
| 3. Control real | 2 | 2 | Ambos reconocen el bid compartido. B documenta el costo de vetar subidas de otros productos; ninguna propuesta crea un cap monetario duro. |
| 4. Interfaz y profundidad | 2 | 1 | A expone un resolutor puro; B reparte la operacion entre `prepara`, `permite` y un segundo encolador, mas `evalua` publico. |
| 5. Recuperacion | 1 | 2 | B reconoce intencion, lock, readback y reconciliacion existentes. A da una firma incorrecta para la reversa y no cierra el paso de cola a retiro. |
| 6. Utilidad entregable | 2 | 1 | A puede evaluar una ventana Ads cubierta, sujeta a madurez y stock; la rama financiera de B debe abstener hoy. |
| **Total** | **11/18** | **9/18** | **A, con las correcciones siguientes.** |

## Semaforo

- **A: amarillo, viable como propuesta de target.** `v_margen_producto` mide
  `[2026-02-20,D-15)` (`0018:283-289,383-412`); Ads usa `[D-60,D-30)`.
  La diferencia no es error logico si `f*M` se declara estimacion de politica
  ACoS y nunca se calcula `C-A` entre esas ventanas. No demuestra beneficio
  causal, cap de gasto ni margen de cada venta atribuida. Madurez, halo y stock
  pueden reducir mucho la cobertura. Se requiere sello de David para usarla.
- **B: amarillo como arquitectura, rojo para su regla financiera hoy.** Ads
  empieza `2026-08-08`, mientras la vista financiera empieza `2026-02-20`.
  Sin gasto previo observado, `A` de la ventana completa es desconocido:
  `Abstener`; no completar meses faltantes con cero ni extrapolar.

## Injertos precisos en A

1. Conservar **una carga y una resolucion por ciclo** sobre el mismo snapshot
   que las decisiones. De B injertar el alcance tipado y la regla explicita de
   impedir subidas en grupos compartidos solo si David acepta el efecto sobre
   productos sanos; no imponer ahi un target ACoS individual.
2. Usar **el encolador vigente `apply_cola.encola_cortes`** para decisiones
   `product_ad` nuevas, ampliando su camino y su revalidacion. El
   `encola_cortes_producto` de B seria una segunda forma de hacer lo mismo.
   Tras liberar, delegar a una entrada automatica tipada de `retiro_anuncios`,
   sin fabricar el `go` humano. Una decision y una intencion deben vincularse.
3. Conservar el freeze de A en la decision accionable. Una tabla de evaluacion
   append-only puede servir para UI y abstenciones, pero `decision_producto`
   solo se justifica si no se obtiene el vinculo de `decision.inputs` y la
   intencion; evitar tres copias que puedan divergir.
4. Conservar la abstencion por identidad desconocida y la comprobacion
   de **todos** los product_ads vivos del ad group. Para corte individual,
   resolver `listing -> producto`, revisar opt-out e impulso y no tocar anuncios
   ajenos. No convertir stock FBA incompleto en cero ni en permiso de corte.
5. Antes de ofrecer reversa, definir propiedad: `repone` recibe
   `retiro_ids` y `go` hoy (`retiro_anuncios.py:423-435`); solo se repone un
   retiro AUTO-03 sellado y aun propio, nunca una pausa previa o externa.
   Revalidar estado bajo `retiro-ad:<id>` y mantener reposicion manual inicial.

## Correcciones obligadas y descartes

- **Ceros:** `0020_ads_producto_metrica.sql:74-80` dice expresamente que el
  reporte trae solo filas con actividad: ASIN ausente = sin datos. La frase de B
  sobre reporte `COMPLETED` no constituye prueba de cero; requeriria una fuente
  explicita de ausencia por producto/fecha. Igual para los cortes de A y B.
- **Ventana y monto:** Si B se retoma, debe derivar `C` de los componentes publicados de
  `v_economia_producto` y `A` del reporte de producto para exactamente su
  `ventana_desde/hasta`; `0021:140-162` no publica `A`. El margen excluye Ads
  (`0018:323-325,353-355`), asi que `C-A` los resta una sola vez. `A>B`
  permite limitar decisiones futuras; no impide exceder B durante el dia.
- **Umbrales:** `A>3B`, exceso `1000 MXN/80 USD`, y corte sin pedidos con
  `100 clicks + 500 MXN/40 USD` son traslados propuestos de reglas de hoja;
  el producto agregado no hereda automaticamente su causalidad ni autorizacion.
  Sellar con David despues de medir cobertura y mostrar ejemplos numericos.
- **Halo:** B puede usar ventas organicas totales para financiar Ads, sin
  adjudicar al producto el margen del SKU comprado por halo. El corte sin
  pedidos exige `purchases30d` total igual a cero observado; halo positivo lo
  bloquea. A descarta halo de toda decision ACoS de producto, lo cual es seguro
  pero deja sin control una parte del gasto.
- **Firmas reales:** `goals.cascada_target_acos` acepta cinco argumentos hoy
  (`goals.py:232-256`), no los seis posicionales mostrados por A; tambien hay
  `peldano_target_acos` y llamadas para negative que tendrian que migrarse.
  `apply_cola._ejecuta_pause` solo acepta keyword/product_target
  (`apply_cola.py:948-965`), de modo que registrar `kind='pause'` para
  product_ad sin ampliar ese camino deja el corte fallido.
- **Descartado:** usar el target ACoS de A como utilidad demostrada;
  minimo/promedio de productos compartidos; cron separado; presupuesto por
  producto ficticio; pausa por stock no reconciliado; reactivacion automatica
  por mejora de margen; cualquier cero inferido de una fila ausente.

**Nota por evidencia nueva:** observaciones Ads MX/US: `2026-08-08` a
`2026-10-23`, 77 fechas. La ausencia de febrero a agosto impide activar el
TACoS historico de B hoy. No prueba por si sola madurez/completitud de la
ventana corta de A; esta debe verificarse con `observed_at` y filas reales.
