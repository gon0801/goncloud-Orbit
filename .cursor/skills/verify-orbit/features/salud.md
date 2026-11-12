# Salud

Salud muestra, por plataforma, una tarjeta de resumen (chips del ultimo ciclo, datos de metricas y seis chequeos con punto verde, ambar o neutro) y el detalle en pestanas: 14 ciclos, avisos de campana, lo que se salto, Ingesta Ads, SP-API, ultimo ciclo, cambios permitidos hoy y precios. Cierra con la tarjeta oscura de Jev. Un ciclo `done` con Telegram caido debe verse en rojo, no "perfecto".

## Sub-features

- `salud-nav` abre `/salud` y marca `aria-current="page"`.
- `salud-plataformas`: una `article.salud-tarjeta` por plataforma del JSON, con `h2` (`Amazon US`, `Amazon MX`), el chip del estado (`done` es `Completo`, `shadow` `Practica`; estados En curso, Completo, Con fallas, Saltado, Fallo) y el chip del modo (Apagado, Practica, En vivo). La linea `ul.salud-checks` trae Ingesta Ads, SP-API, Ultimo ciclo, Ultimos 14 ciclos, Avisos de campana y Avisos de propuesta. El punto es ambar solo si hay filas saltadas, avisos de "se queda sin presupuesto" o un estado distinto de Escrito, ok o Completo.
- `salud-tabs`: `nav.salud-tabs` con un enlace `a.seg-btn[href="/salud?plataforma=<plataforma>"]` por plataforma; la activa lleva `class="... active"` y `aria-selected="true"`. El detalle de cada plataforma vive en `section#detalle-<plataforma>`; la inactiva lleva `hidden`, asi que todo el HTML trae los valores de las dos plataformas y solo una se ve. Sin `plataforma`, abre la primera. Una plataforma nueva en los datos saca tarjeta y pestana sin tocar codigo.
- `salud-ciclos`: `details.salud-ciclos-tabla` con la tabla completa de los ultimos 14 ciclos (Ciclo, Fecha, Estado, Decisiones, Motivo). Arriba, `.salud-ciclos-barras` dibuja las decisiones por ciclo del mas antiguo al mas reciente, con el numero arriba y altura relativa al maximo (0 es una barra gris de 2 px).
- `salud-avisos`: `div#bloque-avisos-campana-<plataforma>` agrupa por el inicio del texto en "Se queda sin presupuesto", "El presupuesto no limita" y "Otros avisos" (la alerta de ubicacion "Fuera de Amazon"). Cada item separa la campana (negrita) del texto y lleva `data-dato-libre`.
- `salud-skips` bajo el h3 `Lo que se salto en el ultimo ciclo`: dos columnas (`Keywords y productos` con la clave `entidad`, `Busquedas` con `termino`), cada motivo con su barra horizontal y el total de la columna. El mismo JSON sigue en `script#datos-skips-<plataforma>` con `{entidad, termino}` como listas `[{motivo, count, motivo_es}]`.
- `salud-ingesta`: tabla completa de Ingesta Ads (Perfil, Reporte, Ultimo exito, Fecha de la metrica, Edad, Ultimo estado) con la fuente, y tabla SP-API (Fuente, Ultima, Filas, Saltadas, Motivo, Ultimo limite de Amazon, Ultima falla de acceso). `Saltadas` mayor que 0 va en ambar.
- `salud-quota` tabla `Cambios permitidos hoy` (Tipo / Usados / Tope / Estado; el tipo va en espanol, p. ej. `Puja`). Tope nulo se pinta `— (sin tope)` o `— (configuracion ilegible)`; el chip de estado dice `Contado hoy`, `Tope de la configuracion`, `Sin tope configurado: no se aplica nada` o `Configuracion ilegible: revisar`. Con `used >= cap` y cap no nulo aparece el chip `saturada`.
- `salud-hechos`: tarjeta `Ultimo ciclo` con `decisiones: N` y `aplicadas: N` (el 0 de practica es dato), la linea de Avisos de propuesta, Destino de harvest y sus campanas sin destino; tarjeta Precios con la linea del dia.
- `salud-telegram`: si `notes.telegram` existe, sale `p.alerta` cuyo texto empieza `telegram: fallo del canal (` y lista las claves del dict (`digest`, ...) `sin entregar)`.
- `salud-jev`: tarjeta oscura `Jev: senal por busqueda en cada grupo` (si hay tablas de Jev) con `Ultimo calculo terminado: <8 nov, 11:58> (<hace>)`. Con Jev encendido y mas de 13 h sin `fin` (o ninguno), el parrafo es `p.alerta` y agrega `Pasaron mas de 13 horas: Gasto sin venta esta desactualizado.`
- `salud-api` `GET /api/dashboard/salud` no cambia: trae plataformas, ciclo, skips, quota, historico, spapi, ads_ingest, precios, avisos y jev. El HTML solo presenta esos valores.

## How to get to it (user POV)

- Elegir `Salud` en el nav (`<a href="/salud">`).
- Elegir la pestana `Amazon US` o `Amazon MX` (`/salud?plataforma=amazon_us` / `/salud?plataforma=amazon_mx`).

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: ciclo `done` / `shadow` en amazon_us con `decisiones` 2, `applies` 0 y skip `estado_no_enabled`.

- **Abrir Salud.** Corre `curl -sS "$BASE/salud"`. Status 200. El HTML contiene `data-pantalla="salud"`, `class="tarjeta salud-tarjeta"`, `class="salud-checks"`, `id="detalle-amazon_us"`, `id="detalle-amazon_mx" hidden`, `id="datos-skips-amazon_us"` y `Cambios permitidos hoy`.
- **Leer el ultimo ciclo.** La tarjeta `Amazon US` tiene los chips `Completo` y `Practica`; el detalle dice `decisiones: 2` y `aplicadas: 0` (no `—`).
- **Cambiar de pestana.** Corre `curl -sS "$BASE/salud?plataforma=amazon_mx"`. Ahora `id="detalle-amazon_mx"` no lleva `hidden` y `id="detalle-amazon_us"` si.
- **Ver barras.** Existe `class="salud-ciclos-barras"`, al menos una `.salud-skip-barra` y el JSON `script#datos-skips-amazon_us` con `motivo_es` humano.
- **Ver quota.** El HTML contiene `Cambios permitidos hoy` y `— (sin tope)` (la semilla no pone caps).
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/salud"`. Status 200. `plataformas.amazon_us.ultimo_ciclo.status` es `done`, `decisions_count` es `2` y `applied_count` es `0`.
- **Proof.** `.cursor/skills/verify-orbit/helpers/orbit-verify drive-salud` guarda HTML, JSON y las capturas de 1440 y 390 px bajo `evidence/<run_id>/salud/`.

## Gotchas

- El h3 dice `Ultimos 14 ciclos`. El SQL es `ORDER BY id DESC LIMIT 14` (ultimos 14 ciclos, no 14 dias de calendario). La tabla va del mas reciente al mas antiguo; las barras, del mas antiguo al mas reciente.
- `applied_count=0` en practica (shadow) es el caso normal; en pantalla es `aplicadas: 0`. Pintarlo como `—` fue un bug de presentacion.
- Quota con `cap` null se muestra `— (sin tope)` o `— (configuracion ilegible)`, nunca un tope 0 inventado.
- `jev.fin_viejo` es verdadero tambien sin ningun `fin`; con Jev apagado no se pinta en alerta. Gasto sin venta (`/gasto-sin-venta`) usa su propio umbral de 36 h.
- La nota `telegram` es la unica visibilidad del canal caido. Si el JSON trae `notes.telegram` y no hay `p.alerta`, es regresion.
- `Ultimos datos de metricas` (watermark, `v_metric_latest`) y `Estructura sincronizada` (`synced_at`) pueden ser `—` si no hay estado: eso es dato faltante, no fallo.
- Los avisos de campana se cortan por la frase conocida (`: se queda sin presupuesto`, `: este presupuesto no limita`), no por el primer `: `, porque el nombre puede traer dos puntos (`AD_READY: B0D9F4V1CC`). Lo que no empieza con esas frases va a `Otros avisos` y no se pierde.
- La CSP `default-src 'self'` prohibe JS y estilos inline: las pestanas son enlaces GET y las barras usan clases de ancho/alto.
