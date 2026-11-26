# Envíos (`/envios`)

Envíos muestra los pedidos de los últimos 30 días de Amazon MX, Amazon US y Mercado Libre y dónde va cada envío. Es solo lectura: no hay formularios que escriban ni JavaScript propio de la pantalla.

## Sub-features

- `envios-nav` abre `/envios` desde el grupo Catalogo (después de Reputacion) y marca `aria-current="page"`. El header dice `Amazon MX · Amazon US · Mercado Libre` (`span.alcance`) y el subtítulo `Pedidos de los últimos 30 días y dónde va cada envío · solo consulta`.
- `envios-contador` `span#envios-contador` en el sidebar y `[data-rol="envios"]` en la barra móvil: `shell.js` los llena desde `GET /api/envios/contador`. Solo aparecen cuando hay envíos que requieren atención; en cero o con error quedan ocultos.
- `envios-lecturas` `p#envios-lecturas`: la última lectura correcta por mercado (`Amazon MX: 24 nov, 22:00` o `sin lectura correcta`) con el chip `Atrasada` si pasó más de 8 h o nunca hubo.
- `envios-atraso` `p#envios-atraso` (solo si algún mercado está atrasado): `Lectura atrasada: Amazon US, Mercado Libre. …`. Las demoras de ese mercado se calculan solo hasta su última lectura.
- `envios-vencimiento` `p#envios-vencimiento`: `Mercado Libre, en toda la historia: M en Cerrado por vencimiento, sin confirmar entrega (pasaron más de N días desde la compra sin un estado final).` M no se limita a los 30 días de la lista; N es `envios_meli_dias_cierre` de la config vigente (90 sin la clave). Con un valor inválido `/envios`, `/api/envios` y el contador responden 503 con el motivo.
- `envios-vistas` `nav[aria-label="Vistas"]`: `Requieren atención (N)`, `En curso (N)` (por defecto), `Cerrados (N)`, `Todos (N)`. Los conteos no cambian con los filtros.
- `envios-filtros` `form.filtros[action="/envios"]` (GET): `input[name="q"]` (pedido, guía o SKU; busca un fragmento sin distinguir mayúsculas), `select[name="plataforma"]`, `select[name="tipo"]`, `select[name="estado"]`, `input[type="hidden"][name="vista"]`, botón `Filtrar` y enlace `Limpiar`. Un valor ajeno en `vista`, `plataforma`, `tipo`, `estado` o `page=0` da 422.
- `envios-lista` `article[data-envio="<referencia>"]`: chip de motivo (`chip alerta`, solo si requiere atención), estado, mercado y tipo de envío, pedidos (y `Venta en carrito` en Mercado Libre), productos `cantidad × título · SKU`, fechas (`Comprado`, `Despachar antes de`, `Entrega prometida`; `Entrega límite`, `Entregado` y `Devuelto` solo si vienen) y cada paquete (`Paquete 1 de 2`, transportista, guía, fecha de despacho). Un dato que la fuente no mandó se ve como `—`. Orden: primero los que requieren atención, luego por plazo más próximo; sin plazo corriendo (por ejemplo `Enviado sin confirmación` y `Esperando recolección en agencia`) al final.
- `envios-vacio` sin resultados dice `Sin envíos con esta vista y estos filtros.`; si hay búsqueda fuera de `Todos`, ofrece `Buscar en todos los envíos`.
- `envios-paginas` `nav[aria-label="Páginas"]`: 50 envíos por página; los enlaces conservan vista y filtros.
- `envios-api` `GET /api/envios` (mismos parámetros): `generado_el`, `ventana_dias`, `lecturas`, `resumen` (conteo por vista), `vencimiento` (`dias_cierre`, `cerrados`), `total`, `page`, `paginas`, `envios` (códigos crudos). `GET /api/envios/contador`: `{"atencion": N}`, igual a `resumen.atencion`.

## How to get to it (user POV)

- Elegir `Envíos` en el sidebar (`<a href="/envios">`) o el tab `Envíos` en la barra inferior móvil (`nav.tab-bar a[href="/envios"]`, con contador `[data-rol="envios"]`), que se marca activo con `aria-current="page"`.

## Driving it with curl

Preconditions:

- Doctor en verde. La semilla baseline no trae envíos ni corridas: `/envios` responde 200, avisa las tres lecturas atrasadas y la lista llega vacía.
- Para ver tarjetas, sembrar en la base desechable corridas `ingest_run` (`spapi_orders` con `platform` `amazon_mx`/`amazon_us`; `envios_meli` con `meli`; `ok = true`) y envíos con `app.envios.store.guardar`.

- **Abrir Envíos.** Corre `curl -sS "$BASE/envios"`. Status 200, `data-pantalla="envios"`.
- **Filtrar.** Corre `curl -sS "$BASE/envios?vista=todos&plataforma=meli"`: solo tarjetas de Mercado Libre.
- **Lado JSON.** `curl -sS "$BASE/api/envios?vista=atencion"` debe listar las mismas referencias que las tarjetas y `curl -sS "$BASE/api/envios/contador"` el mismo número que `Requieren atención (N)`.
- **Proof.** Guarda HTML y JSON bajo `evidence/<run_id>/envios/`.

## Gotchas

- La ventana va por la fecha del pedido: un envío de un pedido de hace más de 30 días no se lista aunque siga abierto.
- Por la misma ventana, un vencido nunca aparece como tarjeta (el filtro `estado=cerrado_sin_confirmar` llega vacío): se ve en `#envios-vencimiento`.
- Un envío de Mercado Libre sin leer o ilegible hace que la corrida no cuente como lectura correcta. No pasa con un envío abierto comprado antes de la ventana de 30 días que ya no viene en la búsqueda (queda como `envio_viejo_no_leido` o `envio_viejo_ilegible`, con aviso en `/salud`), salvo que se haya pedido con `--releer`.
- Producción (26-nov UTC, dato del coordinador) corre #782 (`3c5e53fb`): muestra `ready_to_ship/in_packing_list` como `En camino` y no tiene `#envios-vencimiento`. Esta ficha describe la rama que lo corrige a `Por despachar`, aún sin desplegar.
- Un mercado sin lectura reciente no marca demoras nuevas: revisa `#envios-lecturas` antes de concluir que todo va a tiempo.
- FBA, Full, DHL sin confirmación de entrega y `Esperando recolección en agencia` no cuentan como entrega atrasada.
- `orbit-verify` no tiene todavía una receta `drive-envios`.
