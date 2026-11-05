# C6-P1 — fotografia productiva de lectura

Fotografia DB principal **5-nov-2026 01:44:49 UTC**, complemento 01:45:53 UTC
(4-nov en Vancouver). C6-P1 completa como inventario verificable; **SHA global
y aislamiento global quedan sin demostrar**, son precondiciones de P3, no
permiso para iniciar sombra. C6 economica sigue apagada. No hubo escrituras,
reportes nuevos, ciclos manuales, cambios de cron/procesos ni llamadas Ads.

## Resultado por criterio

| Criterio | Resultado actual | Evidencia / limite |
| --- | --- | --- |
| Identidad de codigo | Parcial: 11/11 archivos pertinentes presentes coinciden SHA256 con `1292000c867e72f179cf390fb814c587d147db8b` | `hashes.json`, `comparacion-hashes.json`. Coincidencia parcial no identifica SHA completo. Imagen/contenedor en `contenedor.txt`; label de revision ausente. SHA productivo global = unknown |
| Config/flags | Completo: config **42**, economica **ausente**, `ads_pause_sin_cooldown_bid=true`; `niveles_v3` MX/US; `ads_optimizer_mode=live` | `fotografia.json: config/limites`. Fraccion de margen MX0.5/US0.8; fallback setting target20% por plataforma. Ningun valor cambiado |
| Goals/IDs/modos | Completo: **4,5,6,7,8,9,10,11,12**, todos enabled/live, target explicito None | Snapshot completo `goals`, valores y fechas. IDs coinciden con H5 por lectura nueva, no por reutilizar su lista. Goals4/5 plataforma; seis a doce campana (6/7 US,8–12 MX). Los defaults de bid y monedas figuran en cada fila |
| Limites/cuota | Completo: ocho caps (bid/pause/negative/harvest × MX/US) = **1,000,000 operaciones** cada uno | Config42 y nueve filas quota de ultimos7d concuerdan. No son caps bajos de rampa. Sin setting gasto_para_concluir encontrado en consulta por clave; no se inventa su valor efectivo |
| Cola | Completo: una fila no terminal **25**, live/pending_veto, decision2861/hoja4919 US, motivo `pause_umbral`, vence **6-nov08:40:02 UTC** | `cola_no_terminal`; cero filas por filtro `pause_economica`. No equivale a cola vacia: P3 debe contener/verificar esta fila al planear cero-applies |
| Ledger principal/reversas | Completo: **539 attempts** globales, cero sin finished_at; 498 normales ok,10 reversas ok,31 probes historicos (23ok/8fallos) | `complemento.json: attempt_global`. Join de attempts con decision solo cubre508: no omitir probes huerfanos de decision. Los8 fallos son sondas agosto, no fallo actual |
| Resumen de aplicaciones | Completo: **487** filas verify_ok=true (472bid,11harvest,2pause,2negative) | `application_resumen`. 15 attempts normales en ultimos7d; no sumar attempts y applications como operaciones distintas |
| Propuestas economicas | Completo: cero registros totales y cero open | `propuestas_resumen/propuestas_abiertas`. Ahora si se revalido el antiguo open=[]; ninguna campana concreta que pausar |
| Salud por perfil | Completo para lectura actual: principal1337 terminada4-nov07:26:39UTC, cuatro reportes written por perfil, metric_date3-nov; cero incidentes abiertos | `salud.json`, DB complemento. US4387591165211712 y MX3850003733258937. Lectura antes del cron principal5-nov: no exigir exito del dia que aun no corre. Edad HTTP2d no es madurez de cortes |
| Ciclos recientes | Completo: US166 y MX167 live/done; ultimos20 ciclos en snapshot | `ciclos`, `/salud`. done no certifica rentabilidad ni sustituye ventana de cortes |
| Escritores concurrentes | Inventario completo dentro del alcance C6/BIDS02; aislamiento global aun no probado | Tabla siguiente. No se inspecciono infraestructura ni se cambio scheduling |

El primer SELECT de salud uso `report_type`, inexistente; error capturado en
`fotografia.json: ingesta_actual` mediante savepoint sin perder snapshot.
Se corrigio a `report_name` y ejecuto en `complemento.json`. Los eventos
pending/downloaded/written son etapas historicas, no tres corridas ni tres
fallos. Perfil1104602105442735 aparece rejected sin platform/report_name:
no se atribuye a US/MX ni se disfraza de reporte written.

## Inventario de escritores para sombra global

| Camino | Actividad/estado observado | Consecuencia para P3 |
| --- | --- | --- |
| Optimizador BIDS02, bids/cortes | Ambos mercados live; cola25 pendiente; ledgers anteriores | Flip de goals debe cubrir IDs actuales y modo efectivo al liberar; registrar todos attempts/applications desde inicio, no solo applied_at de cola |
| Harvest | 11 jobs, todos fase done; ultimo31-oct. Destinos por terna del goal aun visibles en Salud (MX27/US8 por terna y4 por grupo en MX) | No hay harvest a medio aplicar ahora; revalidar al comenzar. Puede generar nuevos trabajos con sistema live |
| Fabrica de campanas | Un lote MX applied;87 pasos applied (5campanas,5grupos,66keywords,5product_ads,6targets); cero pasos pendientes | `modo_goal=shadow` en lote describe su nacimiento, no los goals actuales live. Lanzamientos/pausas/reversas de fabrica no se presumen contenidos por cambiar goals |
| Impulsos/vigia | Cero impulso y cero lecturas | Sin exposicion actual por impulsos existentes. Codigo vigente `impulso_io.py` puede pausar/reactivar campanas, graduar goals live y retirar anuncios por su propio camino; nuevos lanzamientos durante sombra rompen la premisa si no se coordinan |
| Ajustes manuales campana | Cero campana_ajuste | API autenticada sigue siendo escritor potencial (presupuesto/placement/fuera Amazon), no depende del flip de goals. No se hizo ninguna sonda |
| Retiro/reposicion de anuncios | Cero anuncio_retiro, anuncio_retiro_intencion y anuncio_reposicion | Revalidar ausencia; intenciones no terminales cuentan como trabajo en vuelo si aparecen |
| Archivo/reposicion keywords | Cero keyword_archivo_manual | Capacidad manual separada, sin operaciones actuales |
| Reversa/regreso de bid |10 attempts reversa confirmados historicos, ultimos11-oct | Reusa apply_attempt; contabilizar tipo reversa tambien. Snapshot no prueba que nadie invoque ruta manual durante cinco dias |

El conjunto corresponde a tablas de escritura Ads de los caminos vigentes,
no a ledger financiero `ledger_event` ni a reversas de estimacion que no mutan
Ads. No se busca infraestructura adicional ni se afirma ausencia de acciones
manuales fuera de Orbit/Amazon console. Para cero-applies, P3 debe explicar
coordinacion de estos caminos y demostrar aislamiento antes de tocar modos.

## Fuentes, alcance y reproduccion

`esquema.py.txt` consulta columnas reales y hashes iniciales; `fotografia.py.txt`
y `complemento.py.txt` conservan SQL literal y resultados. Conexion exclusiva
`ORBIT_DSN_READ`, DSN registrado en redactor junto con password via redact_dsn,
transaccion REPEATABLE READ READ ONLY, timeout por sentencia15s, savepoint por
SELECT. No se imprimen DSN, request_payload, ack, credenciales ni actor/go_literal.
`hashes.py.txt` solo lee archivos. El intento inicial `app/campana_ajuste.py`
era un nombre incorrecto; inventario definitivo usa **app/campana_ajustes.py**.
`tools/replay_ads_economico.py` no esta en el contenedor, si en referencia local;
P2 lo sabe y ejecuta version controlada sin desplegarlo.

```bash
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/c6-p1/esquema.py.txt
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/c6-p1/fotografia.py.txt
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/c6-p1/complemento.py.txt
ssh goncloud 'docker exec -i orbit-app-1 python -' < docs/evidencia/quick-wins-2026-11-04/c6-p1/hashes.py.txt
ssh goncloud 'curl -sf http://127.0.0.1:8010/api/dashboard/salud'
ssh goncloud 'docker inspect --format "{{.Id}} {{.Image}} {{.Created}} {{index .Config.Labels \"org.opencontainers.image.revision\"}}" orbit-app-1'
```

Las lecturas no son simultaneas entre DB, hashes y HTTP; sus relojes se
conservan en artefactos. Salud y snapshot muestran los mismos ultimos ciclos.
No se consulto Amazon nuevo: P1 inventaria estado operativo, no certifica que
una PAUSE pendiente deba aplicarse. El readback externo de B.4 es evidencia
historica para su caso2736; no sustituye readback fresco de nuevos candidatos.
No hay conciliacion monetaria Amazon de C6 en este expediente.

Siguiente: completar P2 y preparar P3 con caps1M y cola25 explicitamente,
resolver cobertura/unknown de replay, delimitar escritores y reversa concreta.
Esta evidencia **no inicia sombra ni satisface cinco ciclos/FP0/live**. Releer
foto antes de cualquier cambio productivo, con los gos separados del plan.


Validacion local: seis JSON parseados, `pre-commit run --files` para todos
los archivos de P1 con gitleaks y conflictos Passed, `git diff --check` limpio.
Sin tests nuevos ni suite global: evidencia operativa/documental, sin cambio
de comportamiento. CI final y revision del host quedan fuera de esta lectura.
