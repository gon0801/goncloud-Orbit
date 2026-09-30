# H5.4 — primer ciclo live, 30-sep-2026

Lectura de produccion a las 22:30 UTC con `orbit_read`, `BEGIN READ ONLY` y
`ROLLBACK`. Consulta: `H5/primer-live-readback.sql`, exit 0. `/health` dio
`{"status":"ok"}`; `orbit-app-1` y `orbit-db-1` siguieron levantados.

| Ciclo | Plataforma | Estado | Decisiones | Applies confirmados |
| --- | --- | --- | ---: | ---: |
| 92 | amazon_us | `live`, `done` | 24 bid | 24 bid |
| 93 | amazon_mx | `live`, `done` | 48 bid, 1 harvest | 45 bid |

`decision_application.verify_ok=true`: 69; distinto de true: 0. Los 69
`apply_attempt` fueron bid normal con resultado `ok`. Cero PAUSE nuevas,
cero filas aplicadas de la cola, cero mutaciones con origen shadow desde
`INICIO_SHADOW=2026-09-25T06:13:41Z`. Los nueve goals estan `live`, el flag
`ads_pause_sin_cooldown_bid` sigue `true`, no hay ciclos running ni jobs
harvest en curso. No se activo el criterio de parada H5.3.

La decision harvest 2707 dejo `apply_queue` 17 en `pending_veto`, con
vencimiento 2026-10-02 08:41:02 UTC. Es una cosecha por terna del goal de
plataforma (`resuelto_por=terna`, `grupo_id=null`), no el primer harvest de
grupo de FABRICA 02 D.3. No hubo POST de cosecha ni consumo de su cuota;
la ventana de veto sigue vigente. Revisar esa fila antes de cualquier
operacion posterior que exija cero cosechas en vuelo.

La ingesta principal del dia, run 534, termino `ok=true` a las 08:07:39 UTC
con 10 111 filas. `/api/dashboard/salud` mostro los ocho reportes MX/US
`written`, `metric_date=2026-09-29` e `incidentes=[]` en ambas plataformas.

Evidencia de origen: consulta de solo lectura de este archivo, salidas de
`decision_application`, `apply_queue`, `ingest_run` y `/api/dashboard/salud`
leidas el 30-sep-2026 22:29-22:36 UTC. No se modifico produccion.
