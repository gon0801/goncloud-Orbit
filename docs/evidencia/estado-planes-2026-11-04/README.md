# Conciliación de planes y pendientes — 4 de noviembre de 2026

Solicitud: revisar, guardar el estado actualizado y presentar pendientes y planes
por construir. Fecha local: America/Vancouver, 4-nov. Las lecturas del servidor
son del **5-nov UTC** (desde 00:32 UTC). Base: `origin/master`
`6bce3589c17f6c9d5d99e2979e5c55b50dd07ec7`, confirmada contra GitHub.

El resultado consultable está en [ROADMAP](../../../plans/ROADMAP.md) y
[manifest](../../../plans/manifest.json). Se actualizaron estados de planes y
encabezados de actas históricas; no se cambió código de producto, configuración,
cron, credenciales, precios, pujas ni publicaciones. Los cambios locales previos
del dueño se conservaron; la revisión se preparó en un worktree desde master.

## Fuentes y límites

- [github.json](github.json): estado, fecha y commit de integración de #373,
  #374, #504, #507, #508, #509, #520, #521 y #522, consultados con `gh pr view`.
  La consulta de PR abiertos devolvió cero antes de preparar esta actualización.
- [produccion.json](produccion.json): cada consulta SQL, resultados y fecha;
  rol `ORBIT_DSN_READ`, `READ ONLY`, límite de 15 s por consulta. Los DSN no se
  imprimieron. Las claves de config consultadas fueron una lista explícita;
  no se volcó configuración completa ni secretos.
- [seguimientos.json](seguimientos.json): pausas, harvest por grupo, actividad
  MeLi y guardas D-1 para 22–28 oct. Se llamaron las funciones de lectura de
  `app/metricas_listas.py` con fecha explícita, en transacciones de lectura.
- [costos-y-pausa.json](costos-y-pausa.json): últimos escenarios por publicación
  MeLi y evidencia de la PAUSE aplicada. Los escenarios tienen fechas distintas;
  el conteo de costo ausente no certifica que todas sigan activas.
- [despliegue.json](despliegue.json): SHA256 de seis archivos del Resumen y del
  arreglo MeLi, iguales entre master y el contenedor. Comprueba esos archivos,
  no la identidad de toda la imagen ni una revisión visual nueva.
- [ingesta-7d.txt](ingesta-7d.txt): copias del informe y salida ya generados en
  `recordatorios/ingesta01-d2.txt` / `.out`. Sus fechas, el GET `/health` y la
  respuesta `ok` están en [operacion.txt](operacion.txt). No se volvió a enviar
  el informe ni se comprobó su recepción en Telegram.
- [cron-vigente.txt](cron-vigente.txt): solo líneas pertinentes del crontab de
  `gon` y `atq`; evita exportar el entorno de los jobs.
- [cron-salud.txt](cron-salud.txt): resultado real del checker existente (exit 1).
- Diseño AUTO-03 y evidencia del incidente MeLi de octubre: archivos que ya
  existían sin seguimiento Git en el checkout del dueño, incorporados sin cambiar
  su contenido. Son evidencia histórica, no mediciones repetidas hoy.

## Conclusiones y cómo cerrarlas

| Hallazgo | Evidencia / impacto | Comprobación siguiente |
|---|---|---|
| ORBIT 05 ya cerró | Acta del 1-oct y filas 2.3/2.5. El registro viejo daba un pendiente falso. | Consultar `docs/evidencia/orbit-05/cierre-2026-10-01.md`; no repetir el cutover. |
| REPRICING 01 fue sustituido | REPRICING 02 lo sustituyó el 8-oct y cerró el 17-oct. Las 22 filas antiguas no son otra implementación. | Leer REPRICING 02 y P04 para cobertura. Se retiraron sus marcadores TODO/WIP vigentes conservando el estado histórico. |
| Resumen integrado y presente en producción | Seis hashes coinciden; una ingesta `accounting_meli_ads_campaigns` exitosa el 4-nov 23:22. | P01: primera corrida programada posterior al despliegue y conciliación con accounting según `docs/DEPLOY.md`. No confundir ingesta exitosa con reconciliación externa. |
| Fábrica 02 aún necesita el caso de grupo | 11 jobs `done`, ninguno con hermanas; consulta de decisiones resueltas por grupo devuelve 0. | P02: primer caso natural y reversa prevista en D.3. Los harvests por terna no prueban F2. |
| Recuperación de alertas ya observada | Desde 30-sep: 6 atrasos + 6 fallos; 12 avisos y 12 recuperaciones enviadas según ledger; 0 abiertos. | A.4 debe consolidar su periodo de observación y la discrepancia del checker; ya no necesita esperar el primer fallo real. |
| Checker de cron desalineado | `total=1 exacta=0`. La línea instalada mantiene `*/10 10-12` y `app.cli ads-salud`, envueltos en `/usr/local/bin/cron-avisa`. | Repro: `ssh goncloud 'bash -s' < tools/check_ads_salud_cron.sh` → exit 1. Conciliar el contrato del checker/instalador con el wrapper vigente. No reinstalar ni duplicar el cron por este resultado. |
| PAUSE natural existe | Decisión 2736, `pause_umbral`, decidida 5-oct y confirmada 7-oct, `verify_ok=true`. | B.4 debe cerrar con el caso y verificación externa del runbook. No se volvió a consultar Amazon en esta revisión; no se afirma que ejercite por sí sola el caso de cooldown. |
| Protección económica apagada | Config 42: `ads_pause_economica` ausente; `ads_pause_sin_cooldown_bid=true`. | P03/C.6: revisar vigencia frente a BIDS 02, sombra y decisión de encendido. |
| MeLi conserva escenarios sin costo | Último escenario por listing: 7 `costo_ausente` del 4-nov + 2 del 30-oct. Goals MeLi `live`: 45. | P04: identidad/actividad actual, costo verificado y metas; no inferir cobertura actual solo por las 9 filas. |
| Precios MeLi no repiten el aborto conocido | Hash de `app/precio/corrida.py` coincide con #504; 2–4 nov: 45 decisiones/día, 6 corridas/día, 0 errores. | Mantener el incidente cerrado. Cero movimientos no implica un fallo. |
| Reputación Amazon sigue sin datos | 0 ingestas exitosas y 0 reseñas; MeLi sí tiene observaciones recientes. | P05: resolver Apify y verificar ingesta semanal. No se consultó crédito actual ni se ejecutaron actores de pago. |
| Primer archivo de inertes sin registro | `keyword_archivo_manual` vacío. | P07: ensayo actualizado y decisión del dueño; no archivar por haber vencido una fecha. |
| Informe de ingesta de 7 días recuperado | Generado 28-oct 18:42 UTC: 21 éxitos/21 corridas, 14 ciclos `done`, cero huecos por fuente. Las 14 combinaciones día/plataforma pasan las tres guardas D-1. | Seguimiento de generación/revisión cerrado; no se certifica recepción en Telegram. Placements no enlaza `ingest_run_id`: su guarda verifica éxito de la fuente + existencia de D-1, límite ya documentado en `app/metricas_listas.py`. |
| Informe Jev aún futuro | `atq`: job 8, 17-nov 09:00 UTC. | P08: leer resultado y decidir continuidad. No ejecutar el recordatorio antes. |

Cloudflared, residuales de ORBIT 19 y transversales no se volvieron a auditar en
producción: conservan su última evidencia y un siguiente paso explícito. Las
integraciones/apagados deliberadamente excluidos de JEV 02 no pasan a pendientes
obligatorios por aparecer en un diseño antiguo.

## Reproducibilidad y mantenimiento

Los JSON incluyen los SELECT exactos, resultados y/o fecha. Repetirlos usando
el rol de lectura, `BEGIN READ ONLY`, timeout de 15 s y cierre de transacción.
No consultar ni publicar valores de credenciales. Los hashes se obtuvieron con
`hashlib.sha256` leyendo rutas explícitas dentro de `orbit-app-1` y en master.

La diferencia que permitió esta revisión fue separar estado del plan, evidencia
de producción y seguimiento: un merge no prueba un despliegue; una fecha vencida
no ejecuta una tarea; una fila antigua puede estar sustituida. Al cambiar un
estado, actualizar ROADMAP, descripción del manifest y fila del plan en la misma
entrega, enlazando su evidencia. Esto evita volver a contar cierres como trabajo.

## Verificación de esta actualización

- `pre-commit run --all-files`: gitleaks y conflictos aprobados.
- `git diff --check`: sin errores de formato.
- JSON legible; 38 nombres de plan únicos, todos los archivos y el activo existen;
  el snippet de REPRICING 01 coincide con el manifest.
- Enlaces relativos de las líneas nuevas y de los documentos incorporados: todos
  resuelven. Las 22 filas sustituidas ya no llevan TODO/WIP vigentes.
- Los siete hashes comparados coinciden. Los JSON de consultas no contienen
  errores de ejecución. La salida negativa del checker de cron está conservada
  como hallazgo P03, no presentada como una verificación aprobada.
- Sin cambios en `app/`, `migrations/`, `tools/`, `tests/` ni workflows. No se
  añadieron pruebas ni se ejecutó la suite de aplicación por esta edición documental.
