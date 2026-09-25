# A.3 — estados, cadencia y observabilidad (DoD H4.2)

Definiciones selladas para los avisos de ingesta principal
(`app/ads/salud.py`, migracion `0041_ads_ingest_alert.sql`).

## Estados del episodio (`ads_ingest_incident`)

- Abierto: `closed_at IS NULL`. Uno solo por (scope, tipo) via indice
  unico parcial; abrir duplicado es no-op (`ON CONFLICT DO NOTHING`).
- Terminal: `closed_at IS NOT NULL`. Se cierra al entregarse el recovery
  (`recovery_sent_at` set), al recuperarse sin aviso previo que recuperar
  (`alert_sent_at IS NULL`), o al cancelarse (abajo).
- Superseded: `recovery_cancelled_at IS NOT NULL` (+ `closed_at` set). Un
  fallo/atraso nuevo sobre un episodio con recovery pendiente cancela ese
  recovery y abre episodio nuevo; el viejo queda cerrado como superseded,
  nunca se reabre.
- Pending: `alert_sent_at IS NULL` (aviso) o `recovery_sent_at IS NULL`
  con `recovered_at` set (recovery). Solo un acuse Telegram `ok=true`
  los vuelve sent.

## Cadencia

- 1 episodio abierto por scope/tipo; reintentos acotados a 6
  (`MAX_INTENTOS`, `alert_attempts`/`recovery_attempts`).
- Entrega: al final de cada `procesar_run` + cron `*/10 10-12 * * *`
  (`comprobar_atraso`, aporta su propio reintento cada 10 min 10:30-12:50
  UTC; antes de las 10:30 no abre atraso).

## Final de recovery agotada (definido)

Si el recovery agota sus 6 intentos sin acuse, el episodio SIGUE ABIERTO
sin limite hasta que un fallo/atraso nuevo del mismo scope/tipo lo
supersede (A.3d) o el `recovery_sent_at` se complete en un reintento
posterior. No se auto-cierra: cerrarlo fingiria un aviso que el dueno
nunca recibio. Cambiar esto requiere decision del dueno.

## Observabilidad del chequeo 10:30

Cada corrida de `ads-salud` imprime una linea a stdout (la captura
`ads-salud.log` del cron), tambien cuando se omite por horario:

```
ads-salud chequeo=ejecutado ahora=<ISO UTC> unidades=<N> episodios_abiertos=<M>
ads-salud chequeo=omitido motivo=pre_1030 ahora=<ISO UTC>
```

"El cron no corrio" = hueco en el log. Tests:
`test_a3d_resumen_*`, `test_a3d_main_imprime_heartbeat_y_sale_cero`.
