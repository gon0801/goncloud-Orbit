# ADS PROTECCION: evidencia operativa A.4 / B.4

Lecturas del 5-nov-2026 00:51–00:53 UTC (4-nov en Vancouver).
Solo `ORBIT_DSN_READ`, transaccion `READ ONLY`, timeout 15 s; Amazon por
`AdsClient.get` / `list_objects` con guard default-deny. No hubo escrituras
productivas, nuevos reportes ni mensajes Telegram.

## Resultado y alcance del cierre

| Criterio | Estado | Evidencia y limite |
| --- | --- | --- |
| A.4: siete dias naturales con cron 10:30 real | Cumplido | `cron-log.json`: 29-oct a 4-nov, siete heartbeats `chequeo=ejecutado` a 10:30; 18 lineas diarias, dos unidades y cero episodios nuevos. Es historia de ejecucion, no inferencia por codigo desplegado. |
| A.4: exito principal >=3/7 por perfil/plataforma | Cumplido | `readback-db.json` y `readback-detalle.json`: 7/7 para MX y US, cuatro reportes `written` por dia/perfil (`campanas`, `keywords`, `search_terms`, `targets`). Solo `amazon_ads_reports_v3`, nunca se sustituye por productos. |
| A.4: readback `/salud` | Actual cumplido; historico acotado | `salud.json`: ambos perfiles y sus cuatro reportes con ultimo exito 4-nov 07:26:39 UTC, metric_date 3-nov. No existen aqui siete capturas historicas de `/salud`: la serie de siete dias se demuestra con `ingest_run` / `ads_report_result` y log, no se reconstruyen respuestas HTTP antiguas. |
| A.4: fallo real, aviso y recovery | Cumplido segun ledger | Desde 30-sep hay 12 episodios (6 fallo, 6 atraso), 12 `alert_sent_at`, 12 `recovery_sent_at`, cero abiertos. No se consulto historial de Telegram ni se envia un mensaje para probarlo. |
| A.4: cero falsos recovery | Cumplido contra fuente de ingesta | Cero filas en `recovery_sin_soporte`: cada recovery tiene run principal `ok=true` y reporte `written` del mismo perfil/plataforma. Cero filas en `cronologia_anomala`: cierre y tiempos coherentes. Esto verifica el recovery registrado, no la visualizacion en Telegram. |
| A.4r: checker acepta cron instalado | Cumplido; integracion por host | `../cron/README.md`: regresion roja previa, ocho pruebas verdes y checker remoto salida 0: `ads-salud cron ok: una linea exacta en crontab de gon`. No se reinstalo el cron. |
| B.4: shadow y go live separados | Evidencia historica reutilizada | `../../ads-proteccion-01/H5/live-h5-4.md` y `ciclo-1..5.txt`: cinco dias, ciclos 82–91 done, cero applies/attempts/applications; regreso de nueve IDs a live 30-sep 03:04:56 UTC con go citado. No se repitio shadow ni se activo nada. |
| B.4: caso natural causal PAUSE durante cooldown BID | Cumplido | Misma entidad 5892: BID 2654 confirmado 30-sep 08:40:05.488275 UTC y PAUSE 2736 decidida 5-oct 08:40:01.858222 UTC. El intervalo es menor a cinco dias y por tanto menor a siete; el cooldown BID seguia vigente al decidir. Ambos ciclos live/done y flags true en configs 21/22. |
| B.4: aplicacion y readback externo | Cumplido | Decision 2736 confirmada 7-oct 08:40:05.417819 UTC, verify_ok=true. Lectura nueva 5-nov 00:52:25 UTC: perfil US 4387591165211712, target 555998919865227, campaign 140602818838686, adGroup 529927551250133, `PAUSED`. Los tres IDs coinciden con parentesco DB. Lista completa: 549 recibidos / 549 declarados. |
| C.6: proteccion economica | Pendiente | Configs historicas 21/22 no tienen `ads_pause_economica`; estado actual config 42 ausente ya medido en `estado-planes-2026-11-04/produccion.json`. El motivo de 2736 es `pause_umbral`, no corte economico. Este caso no cierra C.6 ni autoriza encenderla. |

Se recomienda cerrar A.4 y B.4 con esta evidencia operativa y la evidencia de
release ya registrada. A.4r puede cerrarse al integrar la correccion y evidencia
de `../cron/README.md`; no requiere instalar cambios en el servidor.
La serie DB por perfil/reporte y los heartbeats prueban el comportamiento de los
siete dias; `/salud` se concilia actualmente contra ese historial. Se declara
la limitacion de formato: no existen siete capturas HTTP diarias historicas.
No corresponde inventarlas ni esperar otro fallo real: los fallos y recovery
ya ocurrieron y tienen soporte en la fuente principal. C.6 permanece pendiente.

## Caso B.4

PAUSE 2736 usa politica congelada `pause_after_bid_v1`, ventana madura
27-ago–25-sep y motivo `pause_umbral`: 196 clics, cero pedidos, 144.9000 USD,
umbral 100 y piso 40 USD. La presencia de un objeto `economic_policy` en inputs
no indica que el corte economico estuviera encendido: su version es null y el
motivo efectivo es `pause_umbral`. El BID previo 2654 es verificado, no una
propuesta shadow ni un intento fallido. La confirmacion PAUSE ocurrio dos dias
despues por el flujo existente; el reloj relevante para la precedencia es la
fecha de decision, que si cae dentro de siete dias del BID.

La lectura externa prueba el estado actual; la fecha de aplicacion historica
proviene del ledger. No se presenta el readback nuevo como captura de octubre.

## Reproduccion

Desde la raiz del repo, estos comandos no imprimen credenciales:

```bash
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/alertas-pausa/consultas.py.txt
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/alertas-pausa/consultas-detalle.py.txt
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/alertas-pausa/readback-amazon.py.txt
ssh goncloud 'curl -sf http://127.0.0.1:8010/api/dashboard/salud'
ssh goncloud 'bash -s' < tools/check_ads_salud_cron.sh
```

`cron-log.json` conserva las lineas seleccionadas del archivo
`/mnt/data/appdata/orbit/logs/ads-salud.log`, una por fecha a las 10:30 mas primera,
ultima y conteo diario. `cron-log.py.txt` permite repetir esa seleccion.
Las consultas DB incluyen su SQL y resultado completo; la consulta de recovery
revisa todos los episodios existentes, no solo la semana sana.

Impacto: B.4 ya no debe esperar un caso natural; existe el caso causal y esta
conciliado contra Amazon. A.4 ya no depende de provocar fallos ni de enviar
mensajes: tiene fallos reales cerrados y siete dias de cron ejecutado. C.6 sigue
siendo trabajo de activacion separado y no se confunde con esta PAUSE.
