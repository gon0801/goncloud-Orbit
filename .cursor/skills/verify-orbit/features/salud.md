# Salud

Salud muestra, por plataforma, el ultimo ciclo (estado, modo, decisiones, aplicadas), lo que se saltó en el ciclo (skips), los cambios permitidos hoy (quota), los últimos datos de métricas y la sincronización, y los 14 ciclos anteriores (el h3 dice `Últimos 14 ciclos`). Un ciclo `done` con Telegram caido debe verse en rojo, no "perfecto".

## Sub-features

- `salud-nav` abre `/salud` y marca `aria-current="page"`.
- `salud-ciclo` muestra `#<id>`, started_at en hora del centro de México («8 nov, 11:58»), `decisiones:` y `aplicadas:` (el 0 de práctica es dato). Los chips del h2 van en español: `done` es «Completo» y `shadow` «Práctica» (estados: En curso, Completo, Con fallas, Saltado, Falló; modos: Apagado, Práctica, En vivo).
- `salud-skips` bajo el h3 `Lo que se saltó en el último ciclo` dibuja `canvas#skips-<plataforma>` desde `script#datos-skips-<plataforma>`.
- `salud-quota` tabla bajo el h3 `Cambios permitidos hoy` (columnas Tipo / Usados / Tope / Estado; el tipo va en español, p. ej. «Puja»). Tope nulo se pinta `— (sin tope)` o `— (configuración ilegible)`; el chip de estado dice «Contado hoy», «Tope de la configuración», «Sin tope configurado: no se aplica nada» o «Configuración ilegible: revisar».
- `salud-historico` tabla `Últimos 14 ciclos` (Ciclo, Fecha, Estado, Decisiones, Motivo): ultimos 14 ciclos, no 14 dias de calendario.
- `salud-telegram` si `notes.telegram` existe, aparece `p.alerta` cuyo texto empieza `telegram: fallo del canal (` y lista las claves del dict (digest, …) `sin entregar)`. El chip «Completo» (`done`) del h2 no pasa a alerta.
- `salud-jev` tarjeta `Jev: señal por búsqueda en cada grupo` (si hay tablas de Jev): `Último cálculo terminado: <8 nov, 11:58> (<hace>)` sale del ultimo `fin` de `jev_corrida`. Con Jev encendido y mas de 13 h sin `fin` (o ninguno), el parrafo es `p.alerta` y agrega `Pasaron más de 13 horas: Gasto sin venta está desactualizado.` (CLARIDAD 01 B.6). El JSON trae `jev.ultima_fin` y `jev.fin_viejo`.
- `salud-api` `GET /api/dashboard/salud` trae el mismo ciclo, skips, quota e historico. El JSON tambien lleva `target_margen`; el HTML no lo pinta.

## How to get to it (user POV)

- Elegir `Salud` en el nav (`<a href="/salud">`).

## Driving it with curl

Preconditions:

- Doctor en verde.
- Semilla: ciclo `done` / `shadow` en amazon_us con `decisiones` 2, `applies` 0 y skip `estado_no_enabled`.

- **Abrir Salud.** Corre `curl -sS "$BASE/salud"`. Status 200. El HTML contiene `data-pantalla="salud"` y `h2` `Salud: último ciclo, los 14 anteriores y lo que se saltó`.
- **Leer el ultimo ciclo.** Hay una tarjeta `Amazon US` con chips `Completo` y `Práctica`, texto `decisiones: 2` y `aplicadas: 0` (no `—`).
- **Ver skips.** Existe `id="skips-amazon_us"` y `id="datos-skips-amazon_us"`.
- **Ver quota e historico.** El HTML contiene `Cambios permitidos hoy`, `— (sin tope)` (la semilla no pone caps) y `Últimos 14 ciclos`. Hay tarjeta `Amazon MX` aunque no tenga ciclo.
- **Confirmar lado JSON.** Corre `curl -sS "$BASE/api/dashboard/salud"`. Status 200. `plataformas.amazon_us.ultimo_ciclo.status` es `done`, `decisions_count` es `2` y `applied_count` es `0`.
- **Proof.** Guarda HTML y JSON bajo `evidence/<run_id>/salud/`.

## Gotchas

- El h3 dice `Últimos 14 ciclos`. El SQL es `ORDER BY id DESC LIMIT 14` (ultimos 14 ciclos, no 14 dias de calendario).
- `applied_count=0` en práctica (shadow) es el caso normal; en pantalla es `aplicadas: 0`. Pintarlo como `—` fue un bug de presentacion.
- Quota con `cap` null se muestra `— (sin tope)` o `— (configuración ilegible)`, nunca un tope 0 inventado.
- `jev.fin_viejo` es verdadero tambien sin ningun `fin`; con Jev apagado no se pinta en alerta. Gasto sin venta (`/gasto-sin-venta`) usa su propio umbral de 36 h: con el calculo viejo, el aviso es `p.alerta[role=alert]` y la lista vacia dice `No hay cálculo vigente desde el <8 nov, 11:58>. La lista está vacía porque la revisión no ha corrido.`
- La nota `telegram` es la unica visibilidad del canal caido. El HTML no es la frase corta `telegram: fallo del canal`: concatena las keys y cierra `sin entregar)`. Si el JSON trae `notes.telegram` y no hay `p.alerta`, es regresion. La semilla baseline no trae esa nota.
- «Últimos datos de métricas» (watermark, `v_metric_latest`) y «Estructura sincronizada» (`synced_at`) pueden ser `—` si no hay estado: eso es dato faltante, no fallo.
- CAMPANA ACTIVA 01 agrego dos motivos de skip en `MOTIVOS_ES_SALUD` (`campana_no_enabled` → «Campaña no habilitada…», `grupo_no_enabled` → «Ad group no habilitado…»). La semilla baseline sigue usando solo `estado_no_enabled`; si el JSON trae los nuevos, el canvas debe mostrar el `motivo_es` en español, no el id crudo.
