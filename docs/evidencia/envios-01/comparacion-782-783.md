# ENVIOS 01: comparación entre #782 y #783

> **Fotografía histórica (resuelta el 2026-11-26 UTC):** la primera corrida automática (run `1910`, `06:40 UTC`) probó la transición real `in_hub` → `in_packing_list` → `shipped` en producción (`48622258880` y tres envíos adicionales). Con esa evidencia, David decidió integrar `#783` manteniendo `in_packing_list` en `en_camino` (commit `2aec1632`).

Qué versión gana lo decide David. **La decisión está pendiente.** Este documento describe qué ve y qué tiene que hacer el dueño con cada versión. No recomienda ninguna.

- **#782** es el commit `3c5e53fb`, comparado contra `07fae18c`. Se integró el 2026-11-26 a las 04:09 UTC y es lo que corre en producción.
- **#783** es la rama `feat/envios-01-vencimiento` en `9fe98ccf`. **No está integrada ni desplegada.**

Todo aplica solo a Mercado Libre, salvo donde se menciona Amazon. "Atención" es la vista que va primero en `/envios`, junto con el número rojo del menú.

## Qué ves y qué haces, según los casos de producción

Lectura de producción del 2026-11-26 a las 05:02 UTC: 4 envíos `ready_to_ship/in_packing_list`, 1 `ready_to_ship/in_hub`, 0 sin fecha de pedido y 0 con subestado fuera de la tabla de #783.

| Caso en producción | Con #782 (lo que corre hoy) | Con #783 |
|---|---|---|
| **`in_packing_list` (4), fecha de entrega todavía no vencida** | Ves **En camino** en En curso, ordenado por fecha de entrega. No tienes que hacer nada. | Ves **Por despachar** en En curso, en el grupo sin plazo, que va después de todos los envíos con plazo. No tienes que hacer nada. |
| **`in_packing_list` (4), fecha de entrega ya vencida** | Ves **En camino** con **Entrega vencida**. Sube a Atención y entra en el número del menú. Tienes que revisarlo con Mercado Libre. | Ves **Por despachar**, sin atención y sin número en el menú. **La pantalla nunca lo resalta.** Para enterarte tendrías que revisar tú mismo los Por despachar. A los 30 días de la compra sale de la lista. Pasados N días (90 por omisión) solo suma 1 a la línea "Cerrado por vencimiento". |
| **`in_hub` (1)** | Ves **En camino**. Si vence la fecha de entrega, pasa a Atención con Entrega vencida. | Igual. |
| Sin fecha de pedido (0 hoy) | No aplica hoy. Ver "Casos que hoy no existen". | No aplica hoy. |
| Subestado fuera de la tabla de #783 (0 hoy) | No aplica hoy. | No aplica hoy. |
| **Cualquier envío de Mercado Libre** | Ninguna línea nueva. Pasados 90 días desde la compra, deja de releerse y conserva su último estado, aunque ya no aparece en la lista desde el día 30. | Nueva línea en `/envios`: "Mercado Libre, en toda la historia: N en Cerrado por vencimiento". Para ver cuáles son, usas la consulta de `docs/DEPLOY.md`. Para releer uno, usas `--releer REF`. |

### Por qué #783 no resalta un `in_packing_list` atorado

- En #783, Por despachar solo pide atención si venció el **plazo de despacho** o si el subestado es `delayed` o `stale` (`app/envios/estado.py:245-250`). Un subestado `in_packing_list` no es ninguno de los dos.
- Esos 4 envíos no traen plazo de despacho (`cierre-produccion.md`, "Hallazgo en producción"). La sonda vio lo mismo en `xd_drop_off`: `estimated_handling_limit` en `null`, con fecha de entrega sí poblada (`sonda.md:53`).
- Sin ese plazo, #783 no marca **ninguna** atención en `in_packing_list`, aunque la fecha de entrega haya vencido hace días. En #782 el mismo envío cuenta como En camino, y ahí sí se juzga la fecha de entrega (`estado.py:228-229` y `:251-252`, igual en las dos versiones).
- Es el mismo defecto que el recibo anotó para `52cb4b02`: "Un paquete atorado ahí no se resaltaba". #782 lo corrigió de paso al mostrarlo En camino; #783 lo devuelve para `in_packing_list`.
- Dos condiciones sin medir en producción. Para que #782 avise, Mercado Libre tiene que mandar la fecha de entrega de esos 4 envíos, y el recibo no la anota. Además, si fueran Full, ninguna versión los resalta, porque Full no pide atención al vendedor (`estado.py:178`). El recibo tampoco anota su tipo logístico.

### Qué dice cada etiqueta sobre `in_packing_list`

Que el paquete ya salió de manos del vendedor es una inferencia sin confirmar (recibo, "Hallazgo en producción"). El coordinador decidió que siga como Por despachar porque el vendedor aún lo tiene, pero esa decisión no sustituye la de David.

- Si el paquete sigue en tus manos, el "En camino" de #782 te dice algo falso, aunque te avisa si se pasa la entrega.
- Si ya lo entregaste, el "Por despachar" de #783 te pide algo que ya hiciste, y además nunca te avisa si se atora.

## Costos de cada versión

| | #782 | #783 |
|---|---|---|
| `in_packing_list` | Lo muestra En camino aunque el vendedor quizá aún lo tenga. | Lo muestra Por despachar y nunca pide atención (ver arriba). Afecta a los 4 envíos de producción. |
| Subestado fuera de la tabla de #783 | Le asigna un estado según el status y puede pedir atención. Ese estado puede ser equivocado. | Lo deja en **Sin clasificar** y no pide atención. |
| Cierre a los 90 días | Silencioso, sin estado ni conteo. El valor está fijo en el código. | Estado y conteo visibles, ajustables de 30 a 365 días con `envios-ventana --dias N`. |
| Respuesta ilegible de un envío reciente | La corrida queda correcta y ese envío no se actualiza. Solo queda anotado en la corrida. | La corrida falla. Si persiste, Mercado Libre sale **Atrasada** a las 8 horas (nota 4). |
| Respuesta sin `status` | Reemplaza el último estado por uno vacío, que se ve Sin clasificar. | Se conserva el último estado. |
| Historia de Mercado Libre | Cada corrida relee solo los envíos abiertos de hasta 90 días. | Cada corrida y cada carga de `/envios` o `/api/envios` cargan **todos** los envíos de Mercado Libre de la historia, también los entregados hace años. Crece sin tope. |
| Envío sin fecha de pedido con lectura que falla siempre | Las corridas fallan hasta 90 días después del último cambio guardado y luego deja de consultarse. | Las corridas fallan **sin límite** y Mercado Libre queda Atrasada. Hoy hay 0 casos. |
| Valor de ventana inválido en la config | No aplica. | `/envios` y `/api/envios` dan 503, **también para Amazon**. El número del menú desaparece sin aviso, igual que si no hubiera nada que atender. La ingesta de Mercado Libre falla. |
| Escritor de config | No lo toca. | Modifica `config_write`, el único escritor de `config_version`. Para la plataforma `meli` omite las validaciones por producto. Solo se llega a eso por el comando `envios-ventana`, porque la API solo acepta Amazon. |
| Tamaño | Ver "Tamaño". | Ver "Tamaño". |

## Casos que hoy no existen en producción

Ninguno de estos casos aparecía en la lectura del 2026-11-26 a las 05:02 UTC. No se sabe si llegarán a ocurrir.

- **Subestado fuera de la tabla de #783.** Son pares de status y subestado que #783 no lista, por ejemplo `ready_to_ship/invoice_pending`, `shipped/receiver_absent` y `handling/manufacturing`. No está comprobado que Mercado Libre los mande en esta cuenta: son ejemplos para la prueba.
  - #783 los muestra Sin clasificar y no pide atención.
  - #782 los muestra Por despachar (los de `handling` y `ready_to_ship`) o En camino (los de `shipped`). Con el plazo vencido pide atención: Despacho vencido o Entrega vencida. Si un `ready_to_ship` no trae plazo de despacho, #782 tampoco pide atención.
- **Envío sin fecha de pedido.** Si su lectura falla siempre, #783 hace fallar todas las corridas mientras el envío no tenga un estado final. #782 deja de consultarlo cuando pasan 90 días desde su último cambio guardado (nota 2).
- **Valor de ventana inválido.** Solo se llega a esto si alguien escribe la clave sin pasar por el comando, que valida el valor.

## Detalle punto por punto

"Corrida fallida" significa `ok=False` en `ingest_run`.

| Punto | #782 | #783 |
|---|---|---|
| `ready_to_ship` / `in_hub` | En camino. Puede marcar entrega vencida. | Igual. |
| `ready_to_ship` / `in_packing_list` | En camino. Marca entrega vencida si vence la fecha de entrega. | Por despachar. Solo marcaría despacho vencido con un plazo de despacho, que estos envíos no traen. **En la práctica no marca ninguna atención.** |
| Orden en En curso de `in_packing_list` | Por fecha de entrega. | En el grupo sin plazo, al final (`app/envios/lectura.py:133-136`). |
| Subestado fuera de la tabla de #783 en `handling` | Por despachar. | Sin clasificar. En `handling` solo "sin subestado" es Por despachar. |
| Subestado fuera de la tabla en `ready_to_ship` | Por despachar. | Sin clasificar. |
| Subestado fuera de la tabla en `shipped` | En camino. | Sin clasificar. |
| Atención con subestado fuera de la tabla | Despacho o entrega vencida si venció el plazo de ese estado. | Ninguna. |
| Corte de relectura | Constante en el código, `DIAS_TOPE_RELECTURA = 90`. | Config `envios_meli_dias_cierre`, de 30 a 365. Sin la clave, 90. |
| Cuándo corta | Deja de leer pasados 90 días. A los 90 exactos todavía lee. | Cierra pasados N días desde la compra. A los N exactos todavía lee. |
| Estado visible al cortar | Ninguno nuevo. Conserva el último estado. | `Cerrado por vencimiento, sin confirmar entrega`. Cuenta como cerrado y no pide atención, aunque antes la pidiera. Ver nota 1. |
| Contador de cortados | No hay. | Línea en `/envios` y `vencimiento.cerrados` en la API, sobre toda la historia. |
| Historia cargada | La ingesta carga los abiertos de hasta 90 días. `/envios` carga solo la ventana de 30 días. | La ingesta carga todos los envíos de Mercado Libre (`meli_ingesta.py:226`). `/envios` y `/api/envios` también, para el contador (`lectura.py:154-163`). |
| Consultas documentadas | No hay. No toca `docs/DEPLOY.md`. | Dos consultas SQL en `docs/DEPLOY.md`: cuántos son y cuáles referencias. |
| Relectura manual | No hay. La ingesta solo acepta `--max-paginas`. | `--releer REF`, repetible. Si la referencia no existe, sale con código 2 sin consultar a Mercado Libre. |
| Envío sin fecha de pedido | Deja de releerse cuando su última observación guardada pasa de 90 días. Ver nota 2. | No vence. Se sigue releyendo mientras no tenga estado final. |
| Envío vencido que aún sale en la búsqueda | Se sigue leyendo mientras no tenga estado final. | No se consulta. Ver nota 3. |
| Fallo de lectura en envío reciente | `envio_no_leido`. Corrida fallida. | Igual. |
| Fallo de lectura en envío antiguo (compra de más de 30 días, fuera de la búsqueda) | `envio_viejo_no_leido`. No hace fallar la corrida. Aplica hasta los 90 días. | Igual hasta N días. Con `--releer`, sí hace fallar la corrida. |
| Respuesta ilegible, envío reciente | `envio_ilegible`. La corrida queda correcta. | `envio_ilegible`. Corrida fallida. Ver nota 4. |
| Respuesta ilegible, envío antiguo | `envio_ilegible`. La corrida queda correcta. | `envio_viejo_ilegible`. La corrida queda correcta, salvo con `--releer`. |
| Respuesta sin `status` | Se guarda con estado vacío. Reemplaza el último estado y se ve Sin clasificar. | Se trata como ilegible. No se guarda y se conserva el último estado. Un envío nuevo así no se crea. |
| Enlace en teléfono | Barra móvil con enlace a `/envios` y contador. | Igual. Sin cambios en esos archivos. |
| Migración | No hay. | `0103` con reversa. Siembra `"90"` si falta la clave. |
| Escritura en config | No escribe. | `0103` inserta una `config_version`. `envios-ventana --dias N` escribe otra si el valor cambia. Omite las validaciones por producto para `meli` (`app/config_write.py:718`). |
| Valor de ventana inválido | No aplica. La clave se ignora y `/envios` carga normal. | La ingesta sale con código 1. `/envios`, `/api/envios` y el contador dan 503, también con envíos de Amazon. El menú se queda sin número porque el error se descarta (`app/static/js/shell.js:81-88`). El mensaje dice clave, rango y comando, sin repetir el valor guardado. |
| Pruebas | Base con la que se compararon las fallas de #783. | Suite Linux sobre `da32e7aa`: 6729 passed y 85 failed, idénticas a las de `3c5e53fb`. Después, solo pruebas focales. |

## Notas

1. En #783 la lista de `/envios` muestra 30 días desde la compra. Un envío vence con más de N días, y N es 30 o más. Por eso un cerrado por vencimiento no aparece en la lista y el filtro por ese estado sale vacío. Lo único visible es el contador.
2. En #782 la fecha de respaldo es `observed_at`. Solo se guarda una observación cuando el envío cambia. Los 90 días cuentan desde el último cambio guardado, no desde la última lectura.
3. La fecha de compra de un envío es la de su orden más antigua. En un pack con una orden vieja y otra reciente, #783 deja de consultarlo cuando vence la vieja.
4. En #783, una respuesta ilegible reciente que persiste impide las corridas correctas. Pasadas 8 horas sin una, Mercado Libre se marca `Atrasada` y las demoras se calculan solo hasta la última corrida correcta. En #782 la corrida queda correcta aunque ese envío no se actualice.

## Tamaño

Medido con `git diff --numstat`. Las pruebas cuentan aparte.

| Cambio | Código en `app/` | Migraciones | Pruebas | Otros (plan, manifest, ficha de pantalla) |
|---|---|---|---|---|
| #782 (`07fae18c..3c5e53fb`) | 5 archivos, +74 −18 | ninguna | 4 archivos, +80 −12 | 3 archivos, +4 −4 |
| #783 sobre lo que corre hoy (`3c5e53fb..9fe98ccf`) | 10 archivos, +364 −165 | 2 archivos, +40 | 5 archivos, +918 −21 | 3 archivos, +7 −3 |

#783 también cambia `docs/DEPLOY.md`, el spec, el recibo de cierre y este documento.

## Producción

- Lectura del 2026-11-26 a las 05:02 UTC: 4 `in_packing_list`, 1 `in_hub`, 0 sin fecha de pedido y 0 subestados fuera de la tabla de #783.
- Con #782, que es lo que corre, los 4 `in_packing_list` se ven En camino y pasan a Atención si vence su fecha de entrega.
- Con #783 se verían Por despachar y **no pasarían a Atención en ningún caso**.
- El `in_hub` se ve igual en las dos versiones.
- Esas cifras no dicen cuántos envíos cortaría cada versión por antigüedad.
- El [recibo de cierre](cierre-produccion.md) anota reglas de #783 como decididas por el coordinador. Eso no sustituye la decisión de David sobre qué versión gana.

## Cómo se comprobó

- Se extrajo `3c5e53fb` a un directorio temporal. El mismo guion temporal, que no se guarda, corrió contra esa copia y contra la rama en `9fe98ccf`, con datos sintéticos y una base local de pruebas.
- Resultados del clasificador, juzgando al 2026-11-26 05:02 UTC con envíos `xd_drop_off`:
  - `in_packing_list` sin plazo de despacho y entrega vencida. #782: `en_camino` con `entrega_vencida`. #783: `por_despachar` sin motivo y sin plazo.
  - Lo mismo con 60 días desde la compra. Mismo resultado.
  - Lo mismo con 95 días. #782: `en_camino` con `entrega_vencida`. #783: `cerrado_sin_confirmar` sin motivo.
  - `in_packing_list` con un plazo de despacho vencido, que en producción no existe. #783: `despacho_vencido`. #782: sin motivo.
  - `in_hub` con entrega vencida. Las dos: `en_camino` con `entrega_vencida`.
  - `ready_to_ship/invoice_pending` y `handling/manufacturing` con despacho vencido. #782: `por_despachar` con `despacho_vencido`. #783: `sin_clasificar` sin motivo.
  - `ready_to_ship/invoice_pending` sin plazo de despacho. #782: `por_despachar` sin motivo.
  - `shipped/receiver_absent` con entrega vencida. #782: `en_camino` con `entrega_vencida`. #783: `sin_clasificar` sin motivo.
- Resultados de la ingesta y de la carga de `/envios`:
  - Envío sin fecha de pedido cuya lectura siempre da 500, guardado hace 10 días. Las dos versiones lo consultan y la corrida falla.
  - El mismo envío guardado hace 100 días. #782 no lo consulta y la corrida queda correcta. #783 lo consulta y la corrida falla.
  - Seis envíos de Mercado Libre guardados: cinco entregados con compra de hace 400 días y uno abierto reciente.
    - #782 carga 1 en la ingesta y ninguno extra al cargar `/envios`.
    - #783 carga los 6 en la ingesta y otra vez los 6 al cargar `/envios`.
  - Clave de ventana `"abc"` con solo un envío de Amazon. #782 carga la lista con ese envío. #783 responde 503.
- Lo que se leyó en el código con archivo y línea está citado en cada punto. Se leyó también el contador del menú (`shell.js:81-88`), el escritor de config (`config_write.py:549-563`, `:718`) y la API de settings, que solo acepta Amazon (`app/api_write.py:509`).
- Las filas del detalle que no se repitieron esta vez vienen de una comparación anterior con el mismo método. Esa comparación cubrió los cortes de 90 días, el vencido en la búsqueda, los fallos, los ilegibles, la respuesta sin `status`, el filtro de vencidos y las opciones de la ingesta. Plantilla base, archivos estáticos, store e `ingest_run` son idénticos entre `3c5e53fb` y la rama.
- Las fechas de integración, lo que corre en producción, la lectura de producción y la suite Linux son datos entregados para este documento. No salen del código.
