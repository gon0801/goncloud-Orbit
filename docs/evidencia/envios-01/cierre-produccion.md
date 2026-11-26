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

- Mercado Libre reporta `ready_to_ship/in_packing_list` (4) y `ready_to_ship/in_hub` (1). La sonda no los vio.
- Por spec se muestran como Por despachar.
- Su historia trae `date_handling` y `date_ready_to_ship`, sin `date_shipped` ni plazo de despacho.
- El catálogo `/shipment_statuses` los nombra "In hub" e "In packing list".
- Que ya salieron de manos del vendedor es una inferencia por confirmar.
- La sonda vio 5 envíos `ready_to_ship/dropped_off`. Producción muestra 0 `dropped_off` y 5 entre `in_hub` e `in_packing_list`. Que sean etapas posteriores a la entrega en agencia es inferencia.
- En esos dos subestados no se genera ninguna atención: no hay plazo de despacho y `entrega_vencida` exige en camino o en reparto. Un paquete atorado ahí no se resaltaría.

## Pendientes

1. **Decisión del dueño:** mostrar `in_hub` e `in_packing_list` como En camino o dejarlos Por despachar.
2. **Decisión del dueño:** tope de relectura de envíos de Mercado Libre que nunca cierran. Propuesta: 90 días desde `pedido_creado_el`, y que el fallo de un envío con más de 30 días no baje `ok`.
3. Verificar la primera corrida automática del cron (2026-11-26 06:25-06:40 UTC), en especial que el pase de Mercado Libre siga vigente. La revisa David o el coordinador: si falla el candado o la redirección del log, `cron-avisa` no se entera y la señal sería "Atrasada" en `/envios` tras 8 h.
4. La pantalla muestra 30 días: un problema sin resolver sale de la vista.
5. En teléfono no hay enlace a `/envios`.
6. Notas menores abiertas:
   - El vigilante de las 07:30 UTC puede tapar un fallo de pedidos de las 05:00.
   - Un envío ilegible de Mercado Libre no marca la corrida como fallida.
   - `spapi_order_observation` crece más rápido por las 6 corridas diarias.
   - Los enviados sin confirmación de entrega nunca salen de En curso (por spec).
