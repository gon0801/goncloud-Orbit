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
cero filas aplicadas de la cola. La consulta (8), corregida para usar
`decision.decided_at` y `apply_queue.modo` en vez de `optimizer_cycle.mode`,
demostro cero mutaciones de decisiones anteriores al flip H5.4 desde
`INICIO_SHADOW=2026-09-25T06:13:41Z`; los 69 intentos y 69 aplicaciones
son bids decididos despues del flip. `optimizer_cycle.mode=live` tambien
durante H5 shadow porque describe el envelope, no el modo efectivo del goal.
Los nueve goals estan `live`, el flag
`ads_pause_sin_cooldown_bid` sigue `true`. Hay cero ciclos `running` y cero
`harvest_job` en curso; aparte, hay **una** fila harvest `pending_veto` en
`apply_queue` (17, vence el 2-oct). No se activo el criterio de parada H5.3.

Las tres decisiones bid de MX sin apply (2670, 2676 y 2694) tienen cada
una `decision_sin_aplicar.motivo=fuera_de_cap`, ciclo ejecutor 93. La vista
`v_decision_huerfana` no mostro ninguna `huerfana` de los ciclos 92/93;
solo mostro la cosecha 2707 como `en_cola`. El readback corregido corrio
de nuevo como `orbit_read` con exit 0 el 30-sep 22:47 UTC.

Salida literal relevante del ultimo readback de solo lectura (consulta
`H5/primer-live-readback.sql`, secciones 7-9, exit 0):

```text
cola_aplicada | intentos | intentos_bid | aplicaciones | bids_confirmados
0 | 69 | 69 | 69 | 69
fuente | origen | kind | filas
aplicacion | post_flip | bid | 69
intento_http | post_flip | bid | 69
cola_fila_shadow_aplicada | cola_decision_pre_flip_aplicada
0 | 0
decision_id | cycle_id | kind | ciclo_ejecutor | motivo | detalle
2670 | 93 | bid | 93 | fuera_de_cap |
2676 | 93 | bid | 93 | fuera_de_cap |
2694 | 93 | bid | 93 | fuera_de_cap |
2707 | 93 | harvest | | |
huerfanas | en_cola
0 | 1
decision_id | origen
2707 | en_cola
ROLLBACK
```

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
leidas el 30-sep-2026 22:29-22:47 UTC. No se modifico produccion.
