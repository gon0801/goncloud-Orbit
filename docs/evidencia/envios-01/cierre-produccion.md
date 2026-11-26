# ENVIOS 01 — Cierre en producción

Recibo saneado de la Task 6 del [plan](../../superpowers/plans/2026-11-25-envios-01.md). Sonda previa: [sonda.md](sonda.md).

Estado: integrado, desplegado y conciliado con lecturas. La primera corrida automática del cron no está verificada. Quedan pendientes al final.

## Integración

Las cifras de pruebas, `ai-review`, las corridas bajo `flock`, las cifras "antes" y el detalle de historia y catálogo son mediciones de la sesión de despliegue; sus registros crudos no se guardan en el repositorio.

- PR #780 integrado con squash a `master`: `52cb4b02935a4a6b9cd241fdbf800e96a2f81ebb`, 2026-11-26 02:45 UTC.
- Revisión independiente por tarea y de rama: sin P0/P1 abiertos (dos P1 hallados en revisión se corrigieron antes de integrar).
- Suite completa en Linux, repartida en 4 procesos: 6641 passed y 85 failed.
- Las 85 se repitieron en serie: fallan igual en la base `36fb8538`. Ninguna falla solo en la rama.
- `ai-review`: 1 hallazgo Low, un comentario desactualizado del checklist D.1.

## Despliegue

- `tools/despliegue/desplegar.sh` desde Gon-PC. SHA previo: `36c9cc10`.
- Ensayo previo de solo lectura.
- Única migración pendiente: `0102`, aplicada.
- `/health`: 200.
- Bloque de cron instalado, con respaldo en el servidor.
- Ninguna clave de interruptores cambia.
- `checklist.sh`: `CHECKLIST VERDE 52cb4b02` (28 migraciones OK).

## Ingestas iniciales

2026-11-26 02:53-02:54 UTC, como usuario `gon` y bajo el mismo `flock` del cron.

| Ingesta | Run | Resultado | Escrituras | Saltadas | Páginas | Aviso |
|---|---:|---|---|---|---:|---|
| `spapi_orders` `amazon_mx` | 1897 | ok | resumen 22; filas escritas del sello 281 | 237 (`resumen_ya_cubierto`) | 4 | sin aviso |
| `spapi_orders` `amazon_us` | 1898 | ok | resumen 3; filas escritas del sello 51 | 45 (`resumen_ya_cubierto`) | 1 | sin aviso |
| `envios_meli` | 1899 | ok | 84 escritos | 0 omitidos | no aplica | sin aviso |

## Conciliación (solo lectura)

1. **Cron.** `crontab -u gon -l | grep -A1 'job_key=envios:'` muestra las 3 líneas cada 4 h. El comando del plan, sin `-u gon`, no sirve porque ssh entra como root.
2. **Pedidos previos.** `v_spapi_order_ultima`: 924 antes, 927 después. `spapi_order_observation`: 3164 antes, 3189 después.
3. **Amazon FBM con paquete.** MX 116/116 y US 40/40 con `despachar_antes_de` y `entrega_prometida_el`.
4. **Vocabulario.** 366 envíos en la ventana de 30 días (391 en la base; los 25 restantes son de Amazon con pedido anterior a la ventana). `sin_clasificar`: 0. Sin tipo: 0. Los pares de paquete observados son todos conocidos por el spec.
5. **PII.** Columnas idénticas a la `0102`. Claves de artículos: solo `cantidad`, `pedido_ref`, `producto_ref`, `sku`, `titulo`. 0 filas con términos de PII en ambas tablas.
6. **Permisos.** `app_ingest`: SELECT e INSERT en las tablas y SELECT en la vista. `app_read`: solo SELECT. `app_decide`: nada.
7. **API y pantallas.** `GET /api/envios`, `/envios` y `/salud`: 200. Contador 8 = `resumen.atencion` 8. Vistas: atención 8, en curso 173, cerrados 193, todos 366. Ninguna lectura atrasada.
8. **Atención.** Los 6 paquetes de Amazon MX `UNDELIVERABLE/RETURNING_TO_SELLER` están en atención. `entrega_vencida`: 2, y ninguna es FBA, Full, DHL ni `waiting_for_withdrawal`.
9. **Orden de la vista en curso.** El primer envío por despachar está en la posición 11 (primera página).
10. **Salud.** En `/salud`, Pedidos de Amazon está en ok en MX y US. El ámbar restante es de precios y publicaciones, ajeno a este cambio. Sus causas son http 400, http 404, sin summary y asin inválido.
11. **Fuente externa.** 20 envíos de Mercado Libre releídos por GET (10 abiertos, 10 cerrados): 20 de 20 iguales en estado y subestado (la comparación fue del par estado y subestado).

## Hallazgo en producción

Esta sección describe `52cb4b02` y ya no es la regla: ver [Cambio posterior: #782 y #783](#cambio-posterior-782-y-783). La regla vigente es `in_hub` e `in_packing_list` = En camino.

- Mercado Libre reporta `ready_to_ship/in_packing_list` (4) y `ready_to_ship/in_hub` (1). La sonda no los vio.
- En `52cb4b02`, por el spec de entonces, ambos se mostraban como Por despachar.
- Su historia trae `date_handling` y `date_ready_to_ship`, sin `date_shipped` ni plazo de despacho.
- El catálogo `/shipment_statuses` los nombra "In hub" e "In packing list".
- En la primera corrida automática (2026-11-26 06:40 UTC, run `1910`), `48622258880` pasó de `in_hub` a `in_packing_list` y tres envíos (`48621392332`, `48621988669`, `48624926311`) pasaron de `in_packing_list` a `shipped`, confirmando que `in_packing_list` ocurre después de `in_hub` dentro de la red de Mercado Libre.

## Cambio posterior: #782 y #783

Posterior a las lecturas de arriba, que describen `52cb4b02`.

Comparación histórica previa a la decisión: [comparacion-782-783.md](comparacion-782-783.md).

- **#782 (`3c5e53fb`)** desplegado el 2026-11-26 UTC: `in_hub` e `in_packing_list` en En camino, tolerancia a envíos antiguos sin leer (>30 días) sin bajar `ok`, y enlace a `/envios` en la barra móvil con contador.
- **Primera corrida automática del cron (2026-11-26 06:25–06:40 UTC):** verificada en verde en las tres fuentes (`run 1908` `spapi_orders` MX `ok=true`, `run 1909` `spapi_orders` US `ok=true`, `run 1910` `envios_meli` `ok=true`).
- **#783 (`feat/envios-01-vencimiento`):** conserva `in_hub` e `in_packing_list` en En camino, la barra móvil y la tolerancia >30 días (extendida a `envio_viejo_ilegible`), y agrega tabla única de estados, cierre por vencimiento configurable (`0103`, `envios_meli_dias_cierre = 90`, `envios-ventana`, `--releer`), y que respuestas ilegibles o sin estado en envíos recientes fallen la corrida sin pisar el estado previo.

## Pendientes

1. **Resuelto (#782 + #783):** `in_hub` e `in_packing_list` en En camino (comprobado por transición real `in_hub` → `in_packing_list` → `shipped`); substatus desconocidos pasan a Sin clasificar.
2. **Resuelto (#783):** cierre por vencimiento configurable a 90 días (migración `0103`), `--releer` y respuestas ilegibles en envíos recientes marcan fallo sin bajar `ok` en envíos >30 días fuera de búsqueda.
3. **Resuelto (2026-11-26 06:25–06:40 UTC):** primera corrida automática del cron verificada en verde (runs `1908`, `1909`, `1910`).
4. La pantalla muestra 30 días: un problema sin resolver sale de la vista.
5. **Resuelto (#782):** enlace a `/envios` con contador en barra móvil.
6. Notas menores abiertas:
   - El vigilante de las 07:30 UTC puede tapar un fallo de pedidos de las 05:00.
   - `spapi_order_observation` crece más rápido por las 6 corridas diarias.
   - Los enviados sin confirmación de entrega nunca salen de En curso (por spec).
