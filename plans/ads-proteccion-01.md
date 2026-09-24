# ADS PROTECCION 01 — datos frescos y cortes que frenan gasto anormal

> **Proposito.** Cerrar los cuatro huecos observados al pausar A1U/AU2 Exact:
> ingesta principal tardia, PAUSE bloqueada por cooldown de bids, falta de
> proteccion economica aunque haya ventas o el umbral adaptativo crezca, y
> ausencia de una senal temprana de datos atrasados. Evidencia primaria:
> [`docs/evidencia/ads/2026-09-24-exact-us.md`](../docs/evidencia/ads/2026-09-24-exact-us.md).
> Este plan es el contrato de tareas. No autoriza por si solo un umbral de
> perdida ni una pausa automatica de campana.

## Contrato y decisiones

- **Fuente de verdad del producto:** `docs/CONTEXTO.md`; no hay `spec.md` raiz.
  `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md` y el spec de CORTES 01 fijan
  las reglas vigentes. No se editan como si la nueva politica ya estuviera
  aprobada. **Spec skip reason:** este plan todavia no decide un umbral
  economico ni si la campana se pausa automaticamente. B.1 y C.1 producen
  el `Spec delta` aprobado en `docs/CONTEXTO.md` y en un spec fino antes del
  codigo que cambie comportamiento. El diseno v2 debe enlazar ese delta y
  declarar su precedencia sobre las reglas anteriores que sustituya.
- **Invariantes:** evidencia de corte madura >=10 dias, ventana independiente
  de bids, >=7 fechas, dinero con moneda, `None` distinto de cero, metricas
  bitemporales, una decision por entidad/ciclo, veto de 48h, revalidacion
  fresca antes del claim, quota, readback externo y reversa previa a live.
  Una venta atribuida no demuestra utilidad neta. Los reportes de campaign
  y leaf no se suman entre si: el grano de dinero se fija por regla.
- **Decision del dueno para C.1:** definir riesgo con ACoS Ads maduro o
  contribucion cubierta; limite relativo al target y limite absoluto por
  moneda/goal; cobertura minima y vintage si usa fees; accion al llegar al
  floor; campana como aviso, propuesta con confirmacion o PAUSE live con
  reversa. El caso 17.563% / USD 246.03 / USD 115.20 es evidencia, no un
  default. Sin decision se puede terminar A/B, pero no encender C en live.
- **Decision operativa para A.3:** canal y plazo maximo de aviso de atraso;
  declarar que un ciclo `done` no certifica frescura ni rentabilidad. Apagar
  el optimizador no detiene gasto de Ads. Reutilizar Telegram saliente si
  satisface el plazo; no abrir un webhook ni dar el token admin a alertas.
- **Alcance descartado:** mover budgets automaticamente. El diseno v2
  evita dos motores escribiendo el mismo presupuesto. Amazon describe el
  presupuesto diario como promedio mensual, por lo que tampoco es un
  freno intradia garantizado
  ([Amazon Ads, presupuestos](https://advertising.amazon.com/help/GTGPQGUXNCTHE2DS)).

## Fase A — recuperar y vigilar la ingesta [lane:gate]

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| A.1 | `[Operacion]` `[lane:release]` `[tdd:skip:deploy-y-conciliacion]` Cerrar PR #329: esperar la primera ingesta principal con 300 polls, confirmar datos nuevos US/MX y conciliar fechas e importes contra reportes Amazon; registrar el respaldo y el unico checklist post-deploy | `ingest_run` principal `ok=true`; cada grano/perfil llega a la fecha maxima publicada por su reporte Amazon, con desfase registrado; D-1 solo si esta disponible; muestras de costo/venta por report_id coinciden con Amazon; health y DB conservados | - | cc:WIP |
| A.2 | `[Contrato+datos]` `[lane:gate]` `[tdd:required]` Distinguir en el sello Ads pipeline principal vs productos y resultado por perfil/plataforma; conservar atribucion de un fallo parcial, usando primero los datos existentes antes de migrar | Test rojo previo: tres fallos principales con productos `ok=true` se leen como fallo principal; corrida con un perfil fallido no se presenta como todo sano; la consulta prueba grano y permisos; migracion solo si la fuente actual es insuficiente | A.1 | cc:TODO |
| A.3 | `[Salud+aviso]` `[lane:gate]` `[tdd:required]` Exponer ultima corrida principal exitosa, fecha metrica y edad por plataforma; avisar por flanco de atraso antes del gate vigente de >7d y de recuperacion tras exito real | Escenario de A.2 produce un aviso una vez dentro del plazo acordado aunque el ciclo diga `done`; productos no lo limpia; exito principal real si lo limpia; `/salud` muestra fuente y fecha, sin secretos | A.2 | cc:TODO |
| A.4 | `[Release]` `[lane:release]` `[tdd:skip:verificacion-operativa]` Desplegar A.2/A.3 con CI completo y cross-review; comprobar cron y avisos sin tocar otros servicios | PR mergeado, CI verde, readback de `/salud` y `ingest_run` por perfil/plataforma, alerta/recovery observados; checklist de deploy una vez | A.3 | cc:TODO |

## Fase B — PAUSE no espera al cooldown de bids [lane:gate]

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| B.1 | `[Spec]` `[lane:gate]` `[tdd:skip:contrato-producto]` Aprobar y escribir en `docs/CONTEXTO.md` la precedencia del PAUSE maduro sobre el cooldown de bids; preservar gates de goal, ancestros, estado, veto en vuelo, quota y revalidacion; fijar efecto sobre inertes y negativos; enlazar delta desde diseno v2 con precedencia explicita | Spec delta aprobado con ejemplos 4925 (14-sep) y frontera de 7d; explica que solo BID sigue enfriado y que no cambia el umbral de PAUSE | A.1 | cc:TODO |
| B.2 | `[Motor]` `[lane:gate]` `[tdd:required]` Separar la evaluacion de PAUSE del cooldown de BID sin dos decisiones para la misma entidad/ciclo | Prueba roja en version previa: keyword 4925 propone PAUSE el 14-sep con snapshot conocido entonces y BID queda en cooldown; pasa en codigo nuevo; falta de madurez, `None`, estado PAUSED, veto de corte pendiente, inertes y 7d exactos discriminan | B.1 | cc:TODO |
| B.3 | `[Replay+release]` `[lane:release]` `[tdd:required]` Congelar procedencia y era para replay, medir propuestas historicas y validar el recorrido cola→48h→revalidacion→apply | Replay sin lookahead de 11–19 sep conserva decisiones antiguas y muestra la propuesta nueva desde el 14; no afirma ahorro ni fecha de apply contrafactual; comparar candidatos con snapshots historicos y readback externo de applies reales; CI, cross-review, shadow y live verificados | B.2 | cc:TODO |

## Fase C — proteccion economica con ventas y por campana [lane:gate]

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| C.1 | `[Spec+decision]` `[lane:gate]` `[tdd:skip:contrato-producto]` Elegir regla economica, limites por moneda/goal, cobertura y accion por entidad/campana; publicar Spec delta antes de implementar y enlazarlo desde diseno v2 con precedencia explicita. Usar A1U/AU2 y contraejemplos rentables | Dueno decide literalmente umbrales y accion; spec separa riesgo Ads de utilidad neta, define que hacer con una venta, floor, target ausente y mayor umbral adaptativo; reversa y opt-out definidos | A.1 | cc:TODO |
| C.2 | `[Medicion]` `[lane:gate]` `[tdd:skip:analisis-contrafactual]` Medir la regla elegida con vintages `observed_at<=decided_at` y grano campaign/leaf separado; publicar impacto y falsos positivos | Tabla por ciclo de candidatos, vetos 48h, applies posibles y cambios de regla; no suma dinero duplicado ni convierte la propuesta en ahorro garantizado; el dueño acepta el riesgo medido antes de live | C.1 | cc:TODO |
| C.3 | `[Hoja]` `[lane:gate]` `[tdd:required]` Implementar en keyword/product_target la modalidad economica de C.1; si es PAUSE con ventas, separar la revalidacion de la antigua regla `orders=0` en `apply_cola` | Tests rojos previos: 2423 verifica el limite literal elegido (candidata solo si lo cruza), 100→231 clics, floor, venta tardia, `None`, moneda invalida y dato inmaduro; si PAUSE: una venta sobre el limite recorre decision→cola→48h→revalidacion fresca→apply/readback; si deja de cruzarlo, descartar antes del cobro; version del limite congelada para replay y limite vigente resuelto al revalidar; reversa de hoja conservada | C.2, B.3 | cc:TODO |
| C.4 | `[Campana]` `[lane:gate]` `[tdd:required]` Medir campana desde una unica fuente de dinero; emitir senal/registro de propuesta trazable con ancestros y limites visibles, sin `apply_queue` hasta decidir PAUSE live | Campanas A1U/AU2 aparecen con costo, revenue, target, ventana, estado y motivo; ninguna duplicacion campaign+leaf ni accion sobre campana manualmente PAUSED; propuesta humana define registro separado de aprobacion/descarte y nunca aplica por plazo; tests red/green y UI comprensible | C.2 | cc:TODO |
| C.5 | `[Accion condicional]` `[lane:gate]` `[tdd:required]` Si C.1 aprueba PAUSE live de campana: agregar migracion/clave de efecto, PUT, readback, ledger, quota, reconciliacion, bloqueo de pendientes descendientes y reversa resume propios ANTES de encender; si elige propuesta humana, cerrar con esa decision y su flujo verificado | Para live: prueba de pausa y reversa contra Amazon con mismo campaignId/profile, cola descendiente no aplica y error incierto reconcilia; para propuesta: aprobacion/descartes auditados fuera de la cola automatica; ambas rutas conservan las pausas manuales | C.4 | cc:TODO |
| C.6 | `[Release]` `[lane:release]` `[tdd:skip:despliegue-y-medicion]` Shadow, review cruzada, CI completo, rampa live autorizada y seguimiento de resultados maduros; incluir A.3 como senal de datos | Comparacion de candidatos vs applies y resultados por campana/hoja sin lookahead; cero mutaciones sin readback o reversa; despliegue y checklist una vez por SHA final | C.3, C.5, A.4 | cc:TODO |

## Evaluacion y validacion del plan

- **Clasificacion:** Required A.1–A.4, B.1–B.3, C.1–C.4 y C.6;
  C.5 Required como resolucion de la modalidad elegida, no como aprobacion
  anticipada de PAUSE live. Recommended: aviso de pacing con datos
  intradia en otro bloque. Reject: budget automatico y ACoS intradia
  decisorio, por conflicto de escritura y atribucion inmadura.
- **Puntuacion (5=mejor):** recuperar/alertar datos 5 producto, 5 evidencia,
  5 factibilidad, 5 seguridad; PAUSE antes de cooldown 5/5/4/4;
  proteccion hoja 5/4/3/3; pausa automatica campana 5/4/2/2 hasta
  que C.1 y C.2 fijen regla, reversa y falsos positivos. Por eso C.5
  tiene bifurcacion explicita y no se activa por defecto.
- **team_validation_mode: subagent.** Producto/QA, Arquitectura/Seguridad y
  Skeptic revisaron premisas y DoD por separado; el lead cotejo codigo,
  `docs/CONTEXTO.md`, diseño v2 y spec de CORTES 01. `spec.md` raiz no
  existe; no se promueve una politica nueva mediante este plan. Memoria
  harness-mem no estuvo consultable; se reutilizaron las decisiones
  rastreadas en docs y planes del repo. No se inventa ausencia de otras
  memorias. Product fit: gasto Ads; security fit: no nueva API de escritura
  ni acceso a secretos para avisos.
- **formatter_baseline: configured.** Ruff en `pyproject.toml`, candados
  `pre-commit`, guardas y bateria completa en `.github/workflows/quality.yml`.
  Cada bug lleva prueba que falla en codigo previo; durante implementacion
  solo tests focalizados, bateria completa una vez sobre SHA final en CI.
  Reproduccion de produccion antes de test de invariante, reviewers sin
  bloqueantes y conciliacion externa son DoD de cada bloque.
- **Secuencia verificable:** A.1 es requisito para medir cualquier politica
  con datos nuevos. A.2–A.4 y B.1–B.3 pueden avanzarse por separado tras
  A.1. C.1/2 preceden a la implementacion economica; C.3 y C.4 pueden
  correr en PRs distintos. C.6 espera ambos y la modalidad C.5.

## Preaprobaciones por bloque futuro

| Operacion | Motivo | Alcance |
| --- | --- | --- |
| Lectura de reportes Amazon y SQL de produccion como `orbit_read` | Contrafactual y conciliacion con fuente externa, sin exponer tokens | A.1, C.2, C.6 |
| Push, PR y CI | Validar codigo y documentacion sin repetir bateria local | A.2–A.4, B.1–B.3, C.1–C.6 |
| Deploy de `orbit-app-1` | Activar cada bloque tras CI, con respaldo y readback | A.4, B.3, C.6 |
| Escritura Ads de pausa/reversa de campana | Solo si la modalidad live se decide en C.1 y la reversa existe | C.5, C.6 |

Las operaciones futuras se autorizan por el dueño segun el bloque y la
politica elegida. El merge/deploy del PR #329 fue autorizado aparte el
24-sep-2026; no autoriza cambiar goals, presupuesto ni reactivar campanas.
