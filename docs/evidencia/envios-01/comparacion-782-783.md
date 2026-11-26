# ENVIOS 01: comparación entre #782 y #783

Qué versión gana lo decide David. **La decisión está pendiente.** Este documento solo describe diferencias.

- **#782** es el commit `3c5e53fb`, comparado contra `07fae18c`.
- **#783** es el estado actual de la rama `feat/envios-01-vencimiento`.

Aplica a Mercado Libre. "Corrida fallida" significa `ok=False` en `ingest_run`.

| Punto | #782 | #783 |
|---|---|---|
| `ready_to_ship` / `in_hub` | En camino. | En camino. |
| `ready_to_ship` / `in_packing_list` | En camino. Puede marcar entrega vencida. | Por despachar. Puede marcar despacho vencido si venció el plazo de despacho. |
| Substatus desconocido en `handling` | Por despachar. | Sin clasificar. En `handling` solo "sin substatus" es Por despachar. |
| Substatus desconocido en `ready_to_ship` | Por despachar. | Sin clasificar. |
| Substatus desconocido en `shipped` | En camino. | Sin clasificar. |
| Atención con substatus desconocido | Puede marcar despacho o entrega vencida, según el estado asignado. | No marca atención. |
| Corte de relectura | Constante en código, `DIAS_TOPE_RELECTURA = 90`. | Config `envios_meli_dias_cierre`, de 30 a 365. Sin la clave, 90. |
| Cuándo corta | Deja de leer pasados 90 días. A los 90 exactos todavía lee. | Cierra pasados N días desde la compra. A los N exactos todavía lee. |
| Estado visible al cortar | Ninguno nuevo. Conserva el último estado. | `Cerrado por vencimiento, sin confirmar entrega`. Cuenta como cerrado. Ver nota 1. |
| Contador de cortados | No hay. | Línea en `/envios` y `vencimiento.cerrados` en la API, sobre toda la historia. |
| Consultas documentadas | No hay (no toca `docs/DEPLOY.md`). | Dos consultas SQL en `docs/DEPLOY.md`: cuántos son y cuáles referencias. |
| Relectura manual | No hay. La ingesta solo acepta `--max-paginas`. | `--releer REF`, repetible. Una referencia desconocida sale 2 sin consultar. |
| Envío sin fecha de pedido | Deja de releerse cuando su última observación guardada pasa de 90 días. Ver nota 2. | No vence. Se sigue releyendo mientras no tenga estado final. |
| Envío vencido que aún viene en la búsqueda | Se sigue leyendo mientras no tenga estado final. | No se consulta. Ver nota 3. |
| Fallo de lectura en envío reciente | `envio_no_leido`. Corrida fallida. | Igual. |
| Fallo de lectura en envío antiguo (compra de más de 30 días, fuera de la búsqueda) | `envio_viejo_no_leido`. No hace fallar la corrida. Aplica hasta los 90 días. | Igual hasta N días. Con `--releer`, sí hace fallar la corrida. |
| Respuesta ilegible, envío reciente | `envio_ilegible`. La corrida queda correcta. | `envio_ilegible`. Corrida fallida. Ver nota 4. |
| Respuesta ilegible, envío antiguo | `envio_ilegible`. La corrida queda correcta. | `envio_viejo_ilegible`. La corrida queda correcta, salvo con `--releer`. |
| Respuesta sin `status` | Se guarda con estado vacío. Reemplaza el último estado y se ve Sin clasificar. | Se trata como ilegible. No se guarda y se conserva el último estado. Un envío nuevo así no se crea. |
| Enlace en teléfono | Barra móvil con enlace a `/envios` y contador. | Igual. Sin cambios en esos archivos. |
| Migración | No hay. | `0103` con reversa. Siembra `"90"` si falta la clave. |
| Escritura en config | No escribe. | `0103` inserta una `config_version`. `envios-ventana --dias N` escribe otra si el valor cambia. |
| Valor de ventana inválido | No aplica. | La ingesta sale 1. `/envios`, la API y el contador dan 503. El mensaje dice clave, rango y comando, sin repetir el valor guardado. |
| Estado de despliegue | Integrado el 2026-11-26 04:09 UTC. Producción corre `3c5e53fb`. | **No está desplegado ni integrado.** |
| Pruebas | Base con la que se compararon las fallas de #783. | Suite Linux sobre `da32e7aa`: 6729 passed y 85 failed, idénticas a las de `3c5e53fb`. Después, solo pruebas focales. |

## Notas

1. En #783 la lista de `/envios` muestra 30 días desde la compra. Un envío vence con más de N días y N es 30 o más. Por eso un cerrado por vencimiento no aparece en la lista, y el filtro por ese estado sale vacío. Lo visible es el contador.
2. En #782 la fecha de respaldo es `observed_at`. Solo se guarda una observación cuando el envío cambia. Los 90 días cuentan desde el último cambio guardado, no desde la última lectura.
3. La fecha de compra de un envío es la de su orden más antigua. En un pack con una orden vieja y otra reciente, #783 deja de consultarlo al vencer la vieja.
4. En #783, una respuesta ilegible reciente que persista impide corridas correctas. Pasadas 8 horas sin una, Mercado Libre se marca `Atrasada` y las demoras se calculan solo hasta la última correcta. En #782 la corrida queda correcta aunque ese envío no se actualice.

## Producción

Lectura de las 05:02 UTC:

- 4 envíos `in_packing_list`.
- 1 envío `in_hub`.
- 0 envíos sin fecha de pedido.
- 0 subestados fuera de la tabla de #783.

Si esos 4 siguen en `ready_to_ship`, #782 los muestra En camino y #783 los mostraría Por despachar. En esa lectura no había casos de envíos sin fecha ni de subestados fuera de la tabla. Esas cifras no dicen cuántos envíos cortaría cada versión por antigüedad.

El [recibo de cierre](cierre-produccion.md) anota reglas de #783 como decididas por el coordinador. Eso no sustituye la decisión de David sobre qué versión gana.

## Cómo se comprobó

- Lectura de `git diff 07fae18c 3c5e53fb` y del código de la rama.
- Los mismos escenarios, con datos sintéticos y una base local de pruebas, contra `3c5e53fb` y contra la rama: substatus, cortes de 90 días, envío sin fecha, vencido en la búsqueda, fallos, ilegibles, respuesta sin `status`, filtro de vencidos (solo #783) y opciones de la ingesta. El guion fue temporal y no se guarda.
- Plantilla base, archivos estáticos, store e `ingest_run` son idénticos entre `3c5e53fb` y la rama.
- Las consultas documentadas, la migración y la escritura en config se revisaron en el código y en sus pruebas.
- Las fechas de integración, lo que corre en producción, la lectura de las 05:02 UTC y la suite Linux son datos entregados para este documento. No salen del código.
