# H4 / A.4 — primer dia con cron programado

Readback de produccion el 30-sep-2026 22:29-22:36 UTC. Solo lecturas:
`crontab -u gon -l`, `stat`/`tail` de `logs/ads-salud.log`, checker,
`/health`, `/api/dashboard/salud` y SQL con `orbit_read` en
`BEGIN READ ONLY`/`ROLLBACK`. No se modifico produccion.

El checker devolvio exit 0: `ads-salud cron ok: una linea exacta en crontab
de gon`. `cron` esta `active`, `orbit-app-1` y `orbit-db-1` levantados,
`/health={"status":"ok"}`. El log tuvo tres abstenciones `pre_1030` a
las 10:00, 10:10 y 10:20 UTC. La primera comprobacion util corrio a las
**10:30:02 UTC**: `chequeo=ejecutado unidades=2 episodios_abiertos=0`.
Los reintentos cada diez minutos hasta 12:50 UTC registraron el mismo
estado; el log termino a las 12:50:02 UTC.

La ingesta principal run 534 `amazon_ads_reports_v3` termino `ok=true` a
las 08:07:39 UTC con 10 111 filas. `/api/dashboard/salud` mostro cuatro
reportes `written` en MX y cuatro en US, todos con
`metric_date=2026-09-29`, `ultima_corrida=534` e `incidentes=[]`.
`ads_ingest_incident` conserva solo el episodio historico 1, cerrado el
28-sep; ninguno abierto o nuevo. No hubo un fallo real, por lo que la
ruta de aviso y recovery aun no tiene observacion natural.

Este es el dia **1/7** de observacion del cron (30-sep a 6-oct inclusive).
El cierre de A.4 requiere seguir el runbook durante los seis dias restantes.
