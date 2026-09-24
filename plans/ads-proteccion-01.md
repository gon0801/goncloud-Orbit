# ADS PROTECCION 01 — datos frescos y cortes que frenan gasto anormal

> **Proposito.** Cerrar los cuatro huecos observados al pausar A1U/AU2 Exact:
> ingesta principal tardia, PAUSE bloqueada por cooldown de bids, falta de
> proteccion economica aunque haya ventas o el umbral adaptativo crezca, y
> ausencia de una senal temprana de datos atrasados. Evidencia primaria:
> [`docs/evidencia/ads/2026-09-24-exact-us.md`](../docs/evidencia/ads/2026-09-24-exact-us.md).
> Este plan es el contrato de tareas. El dueno eligio los tres parametros
> operativos el 24-sep-2026; no autoriza una pausa automatica de campana.

## Contrato y decisiones

- **Fuente de verdad del producto:** `docs/CONTEXTO.md`; no hay `spec.md` raiz.
  El [`Spec delta ADS PROTECCION 01`](../docs/superpowers/specs/2026-09-24-ads-proteccion-design.md)
  registra las tres opciones recomendadas elegidas por el dueno el
  24-sep-2026. `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md` enlaza el delta y
  declara su precedencia donde sustituye PAUSE/cooldown. El dueno tambien
  aprobo aplicar el mismo tope a revenue=0 medido y eligio pausa manual en
  Amazon para propuestas de campana con readback en Orbit. La fuente del
  target es la cascada ya sellada.
- **Invariantes:** evidencia de corte madura >=10 dias, ventana independiente
  de bids, >=7 fechas, dinero con moneda, `None` distinto de cero, metricas
  bitemporales, una decision por entidad/ciclo, veto de 48h, revalidacion
  fresca antes del claim, quota, readback externo y reversa previa a live.
  Una venta atribuida no demuestra utilidad neta. Los reportes de campaign
  y leaf no se suman entre si: el grano de dinero se fija por regla.
- **Decision economica del dueno:** ACoS Ads maduro >3x target efectivo
  cuando hay revenue>0 (comparacion algebraica con revenue=0 medido) y
  exceso de gasto >=80 USD/1000 MXN; PAUSE automatica de hoja con las
  salvaguardas actuales y propuesta de campana para pausa manual. El
  caso 17.563% / USD 246.03 / USD 115.20 es evidencia para replay, no un
  umbral inventado. La medicion C.2 decide si hay bloqueantes para live.
- **Decision operativa:** Telegram al fallo principal, a las 10:30 UTC
  sin exito de hoy y una vez al recuperarse. `cycle=done` no certifica
  frescura ni rentabilidad. Apagar el optimizador no detiene gasto Ads.
- **Alcance descartado:** mover budgets automaticamente. El diseno v2
  evita dos motores escribiendo el mismo presupuesto. Amazon describe el
  presupuesto diario como promedio mensual, por lo que tampoco es un
  freno intradia garantizado
  ([Amazon Ads, presupuestos](https://advertising.amazon.com/help/GTGPQGUXNCTHE2DS)).

## Fase A — recuperar y vigilar la ingesta [lane:gate]

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| A.1 | `[Operacion]` `[lane:release]` `[tdd:skip:deploy-y-conciliacion]` Cerrar PR #329: esperar la primera ingesta principal con 300 polls, confirmar datos nuevos US/MX y conciliar fechas e importes contra reportes Amazon; registrar el respaldo y el unico checklist post-deploy | `ingest_run` principal `ok=true`; cada grano/perfil llega a la fecha maxima publicada por su reporte Amazon, con desfase registrado; D-1 solo si esta disponible; muestras de costo/venta por report_id coinciden con Amazon; health y DB conservados | - | cc:完了 |
| A.2 | `[Contrato+datos]` `[lane:gate]` `[tdd:required]` Distinguir pipeline principal de productos y resultado por perfil/plataforma/reporte; registrar fallo global si ocurre antes de identificar perfil; usar datos existentes antes de migrar | Test rojo previo: tres fallos principales con productos `ok=true` se leen como fallo principal; perfil/reporte fallido no se presenta como todo sano ni se atribuye a otro; consulta prueba grano y permisos; migracion solo si fuente actual insuficiente | A.1 | cc:TODO |
| A.3 | `[Salud+aviso]` `[lane:gate]` `[tdd:required]` Exponer ultima corrida principal exitosa, fecha metrica y edad por plataforma; Telegram al fallo, a las 10:30 UTC sin exito de hoy y al recuperarse | A.2 produce aviso por incidente aunque `cycle=done`; productos no lo limpia; salida HTTP fallida queda pending y reintenta sin duplicar, recovery solo tras exito principal y alerta entregada; `/salud` muestra fuente, fecha y estado de entrega sin secretos | A.2 | cc:TODO |
| A.4 | `[Release]` `[lane:release]` `[tdd:skip:verificacion-operativa]` Desplegar A.2/A.3 con CI completo y cross-review; comprobar cron y avisos sin tocar otros servicios | PR mergeado, CI verde, readback de `/salud` y `ingest_run` por perfil/plataforma, alerta/recovery observados; checklist de deploy una vez | A.3 | cc:TODO |

## Fase B — PAUSE no espera al cooldown de bids [lane:gate]

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| B.1 | `[Spec]` `[lane:gate]` `[tdd:skip:contrato-producto]` Aprobar y escribir en `docs/CONTEXTO.md` la precedencia del PAUSE maduro sobre el cooldown de bids; preservar gates de goal, ancestros, estado, veto en vuelo, quota y revalidacion; fijar efecto sobre inertes y negativos; enlazar delta desde diseno v2 con precedencia explicita | Spec delta aprobado con ejemplos 4925 (14-sep) y frontera de 7d; explica que BID aplicado no frena PAUSE, pero PAUSE/reversa conservan su enfriamiento; no cambia el umbral existente | A.1 | cc:完了 |
| B.2 | `[Motor]` `[lane:gate]` `[tdd:required]` Separar PAUSE del cooldown originado por BID, sin dos decisiones por entidad/ciclo ni loop tras reversa | Prueba roja previa: 4925 propone PAUSE el 14-sep y BID queda en cooldown; PAUSE aplicada/revertida conserva sus 7d; falta de madurez, `None`, PAUSED, veto pendiente, inertes y frontera exacta discriminan | B.1 | cc:TODO |
| B.3 | `[Replay+release]` `[lane:release]` `[tdd:required]` Congelar procedencia y era para replay, medir propuestas historicas y validar el recorrido cola→48h→revalidacion→apply | Replay sin lookahead de 11–19 sep conserva decisiones antiguas y muestra la propuesta nueva desde el 14; no afirma ahorro ni fecha de apply contrafactual; comparar candidatos con snapshots historicos y readback externo de applies reales; CI, cross-review, shadow y live verificados | B.2 | cc:TODO |

## Fase C — proteccion economica con ventas y por campana [lane:gate]

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| C.1 | `[Spec+decision]` `[lane:gate]` `[tdd:skip:contrato-producto]` Sellar las tres opciones elegidas y resolver extension a cero ventas y efecto tras revision humana de campana; publicar Spec delta antes del codigo. Usar A1U/AU2 y contraejemplos rentables | Regla literal por moneda, modalidad y caso cero ventas aprobados; spec separa riesgo Ads de utilidad neta, define target ausente, floor y mayor umbral adaptativo; reversa de hoja y opt-out definidos | A.1 | cc:完了 |
| C.2 | `[Medicion]` `[lane:gate]` `[tdd:skip:analisis-contrafactual]` Medir la regla elegida con vintages `observed_at<=decided_at` y grano campaign/leaf separado; publicar impacto y falsos positivos | Tabla por ciclo de candidatos, vetos 48h, applies posibles y cambios de regla; no suma dinero duplicado ni convierte la propuesta en ahorro garantizado; el dueño acepta el riesgo medido antes de live | C.1 | cc:TODO |
| C.2a | `[Replay durable]` `[lane:gate]` `[tdd:required]` Congelar por entidad/ciclo la procedencia y el valor de target para nuevas mediciones, sin depender de `ads_optimizer_goal.updated_at` mutable | Una edicion posterior del goal no cambia el target ni la cobertura de un ciclo ya sellado; prueba con cambio de goal despues del freeze; los ciclos viejos sin evidencia siguen indeterminados | C.2 | cc:TODO |
| C.3 | `[Hoja]` `[lane:gate]` `[tdd:required]` Implementar limite economico de C.1 en keyword/product_target y separar su revalidacion de la antigua regla `orders=0` en `apply_cola` | Tests rojos previos: 2423 compara limite literal, cero ventas segun decision final, 100→231 clics, floor, venta tardia, `None`, moneda invalida e inmadurez; una venta sobre limite recorre decision→cola→48h→revalidacion→apply/readback; si deja de cruzarlo se descarta antes del cobro, tambien tras espera por quota; version congelada para replay y politica/target vigentes al aplicar; reversa conservada | C.2, B.3 | cc:TODO |
| C.4 | `[Campana]` `[lane:gate]` `[tdd:required]` Medir campana desde una unica fuente de dinero; emitir propuesta trazable con ancestros y limites visibles en registro separado de `apply_queue` | A1U/AU2 muestran costo, revenue, target, ventana, estado y motivo; sin doble conteo ni propuesta sobre PAUSED manual; identidad de episodio y dedupe probados ante cron repetido, retry, venta tardia y cambio de target | C.2 | cc:TODO |
| C.5 | `[Cierre humano]` `[lane:gate]` `[tdd:required]` Tras propuesta, el dueno pausa manualmente en Amazon o la descarta; Orbit sincroniza estructura y cierra solo con readback `PAUSED` del mismo campaignId/profile, sin escribir PAUSE/RESUME de campana | Propuesta revisada con campana ENABLED sigue pendiente; descarte no muta; PAUSED externo cierra con snapshot/fecha sin inferir autor ni causalidad; cola descendiente no aplica tras el readback; cron repetido no duplica ni reabre hasta un nuevo episodio; cero llamadas de mutacion de campana | C.4 | cc:TODO |
| C.6 | `[Release]` `[lane:release]` `[tdd:skip:despliegue-y-medicion]` Shadow, review cruzada, CI completo, rampa live autorizada y seguimiento de resultados maduros; incluir A.3 como senal de datos | Comparacion de candidatos vs applies y resultados por campana/hoja sin lookahead; cero mutaciones sin readback o reversa; despliegue y checklist una vez por SHA final | C.3, C.5, A.4 | cc:TODO |

## Evaluacion y validacion del plan

- **Clasificacion:** Required A.1–A.4, B.1–B.3, C.1–C.4 y C.6;
  C.5 Required como cierre de la modalidad humana, no como aprobacion
  anticipada de PAUSE live. Recommended: C.2a para replay futuro estable
  y aviso de pacing con datos
  intradia en otro bloque. Reject: pausa automatica de campana (el dueno
  eligio la ruta manual), budget automatico y ACoS intradia decisorio.
- **Puntuacion (5=mejor):** recuperar/alertar datos 5 producto, 5 evidencia,
  5 factibilidad, 5 seguridad; PAUSE antes de cooldown 5/5/4/4;
  proteccion hoja 5/4/3/3; propuesta de campana con pausa manual en Amazon
  y readback `PAUSED` en Orbit 4/4/4/5. C.5 implementa solo esa ruta.
- **team_validation_mode: subagent.** Producto/QA, Arquitectura/Seguridad y
  Skeptic revisaron premisas y DoD por separado; el lead cotejo codigo,
  `docs/CONTEXTO.md`, diseno v2 y spec de CORTES 01. `spec.md` raiz no
  existe; la politica nueva esta en el spec fino enlazado. Memoria
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
| Pausa manual de campana en Amazon | El dueno ejecuta la accion fuera de Orbit; Orbit solo verifica el estado externo y registra el cierre | C.5 |

Las operaciones futuras se autorizan por el dueño segun el bloque y la
politica elegida. El merge/deploy del PR #329 fue autorizado aparte el
24-sep-2026; no autoriza cambiar goals, presupuesto ni reactivar campanas.
