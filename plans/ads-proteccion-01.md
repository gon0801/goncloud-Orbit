# ADS PROTECCION 01 — datos frescos y cortes que frenan gasto anormal

> **Proposito.** Cerrar los cuatro huecos observados al pausar A1U/AU2 Exact:
> ingesta principal tardia, PAUSE bloqueada por cooldown de bids, falta de
> proteccion economica aunque haya ventas o el umbral adaptativo crezca, y
> ausencia de una senal temprana de datos atrasados. Evidencia primaria:
> [`docs/evidencia/ads/2026-09-24-exact-us.md`](../docs/evidencia/ads/2026-09-24-exact-us.md).
> **Estado (foto 24-sep-2026, ver filas para el estado al 26-sep-2026): borrador corregido tras cross-review Fable 5.1; ejecucion no aprobada entonces.**
> Este plan propone tareas y gates. Las elecciones de producto registradas el
> 24-sep-2026 no autorizan por si mismas implementar el plan, mergear PR,
> desplegar ni activar PAUSE live. El runbook y la aprobacion del dueno vienen
> despues de corregir este plan (el runbook existe: `docs/runbooks/ads-proteccion-01.md`, hito H0b; las autorizaciones fueron por go literal, ver filas y H6/estado.md). No autoriza una pausa automatica de campana.

## Contrato y decisiones

- **Fuente de verdad del producto:** `docs/CONTEXTO.md`; no hay `spec.md` raiz.
  El [`Spec delta ADS PROTECCION 01`](../docs/superpowers/specs/2026-09-24-ads-proteccion-design.md)
  registra las opciones de producto elegidas el 24-sep-2026:
  (1) PAUSE madura no espera al cooldown de BID; (2) limite economico de
  hoja `cost > 3 * target * revenue / 100` y exceso minimo de 80 USD o
  1000 MXN, incluido `revenue=0` medido; (3) propuesta de campana con
  pausa manual en Amazon y readback en Orbit. Las alertas de ingesta tienen
  su contrato operativo mas abajo. Antes de tratar cada opcion como aprobada
  para ejecucion, C.0 coteja el mensaje literal del dueno con el spec; lo
  que no conste queda pendiente, sin inferir permiso de la frase
  "en los 3 usa lo recomendado". `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md`
  enlaza el delta y declara su precedencia. La fuente del target es la
  cascada ya sellada.
- **Invariantes:** evidencia de corte madura >=10 dias, ventana independiente
  de bids, >=7 fechas, dinero con moneda, `None` distinto de cero, metricas
  bitemporales, una decision por entidad/ciclo, veto de 48h, revalidacion
  fresca antes del claim, quota, readback externo y reversa previa a live.
  Una venta atribuida no demuestra utilidad neta. Los reportes de campaign
  y leaf no se suman entre si: el grano de dinero se fija por regla.
- **Regla economica documentada, pendiente del gate C.0:** ACoS Ads maduro >3x target efectivo
  cuando hay revenue>0 (comparacion algebraica con revenue=0 medido) y
  exceso de gasto >=80 USD/1000 MXN; PAUSE automatica de hoja con las
  salvaguardas actuales y propuesta de campana para pausa manual. El
  caso 17.563% / USD 246.03 / USD 115.20 es evidencia para replay, no un
  umbral inventado. La medicion C.2 y la decision C.2b son gates para live.
- **Decision operativa:** Telegram al fallo principal, a las 10:30 UTC
  sin exito de hoy y una vez al recuperarse. `cycle=done` no certifica
  frescura ni rentabilidad. Apagar el optimizador no detiene gasto Ads.
- **Alcance descartado:** mover budgets automaticamente. El diseno v2
  evita dos motores escribiendo el mismo presupuesto. Amazon describe el
  presupuesto diario como promedio mensual, por lo que tampoco es un
  freno intradia garantizado
  ([Amazon Ads, presupuestos](https://advertising.amazon.com/help/GTGPQGUXNCTHE2DS)).

## Gate de autorizacion y estado real

- **Plan pendiente:** la correccion de este documento no aprueba su ejecucion.
  Hasta que el dueno apruebe el plan y el runbook posterior, no avanzar las
  ramas preparadas, mergear PR, desplegar ni activar live por este plan.
  La autorizacion de PR #329 fue puntual; no se extiende a los otros bloques.
- **Operaciones separadas:** preparacion/PR, merge, deploy y cambio a live
  son hitos distintos. El runbook posterior presentara alcance, SHA, comandos,
  evidencia, rollback y condicion de parada para cada hito antes del go del
  dueno. CI verde o review `APPROVE` no sustituyen ese go. Cualquier codigo
  nuevo de PAUSE automatica necesita aislamiento off por defecto, probado
  antes de merge, para que un deploy ajeno no lo active por arrastre.
- **B.2 ya en `master`:** PR #332, merge `beae9ec`, no desplegado segun la
  evidencia disponible al 24-sep-2026. B.2 no tiene flag propio: un deploy de
  `master` puede cambiar PAUSE en goals ya live. Antes de cualquier deploy de
  ese SHA o posterior, B.2a verifica SHA productivo y modo efectivo, y deja
  preparada una contencion aprobada (flag off por defecto o revert). Si no
  se puede demostrar aislamiento, el deploy queda bloqueado.
- **Estado de trabajo, no autorizacion:** PR #333 (A.2), #335 (B.3) y #334
  (C.2) abiertos entonces; C.3 y C.4 tenian ramas locales; A.3 pausado por el
  mismo bloqueante en dos rondas de Quality Kit. (Foto 24-sep-2026; estado al
  26-sep-2026: #333, #335, #334, #340, #341 y #342 mergeados, ver filas.)

## Fase A — recuperar y vigilar la ingesta [lane:gate]

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| A.1 | `[Operacion]` `[lane:release]` `[tdd:skip:deploy-y-conciliacion]` Cerrar PR #329: esperar la primera ingesta principal con 300 polls, confirmar datos nuevos US/MX y conciliar fechas e importes contra reportes Amazon; registrar el respaldo y el unico checklist post-deploy | `ingest_run` principal `ok=true`; cada grano/perfil llega a la fecha maxima publicada por su reporte Amazon, con desfase registrado; D-1 solo si esta disponible; muestras de costo/venta por report_id coinciden con Amazon; health y DB conservados | - | cc:完了 #329 merge 43ddd93 (run 426 ok=true, 9731 filas; docs/evidencia/ads/2026-09-24-exact-us.md) |
| A.2 | `[Contrato+datos]` `[lane:gate]` `[tdd:required]` Distinguir pipeline principal de productos y resultado por perfil/plataforma/reporte; registrar fallo global si ocurre antes de identificar perfil; usar datos existentes antes de migrar | Test rojo previo: tres fallos principales con productos `ok=true` se leen como fallo principal; perfil/reporte fallido no se presenta como todo sano ni se atribuye a otro; consulta prueba grano y permisos; migracion solo si fuente actual insuficiente | A.1 | cc:完了 #333 merge 8cfe7b0 (CI gate+rapido pass; review bot + H3/pre-merge.md; desplegado: 0040 en prod + codigo en H5 25bded0 (arrastrado a ad79eeb; 25bded0 ancestro verificado); observacion 7d = A.4) |
| A.3d | `[Decision]` `[lane:gate]` `[tdd:skip:decision-operador]` Resolver el bloqueo repetido de A.3: una recuperacion pendiente puede impedir un fallo o atraso nuevo; Quality Kit exige decision del operador antes de otra correccion | Registrar la decision literal del dueno y el caso reproducible de ambas secuencias; si no se autoriza una nueva correccion, A.3/A.4/C.6 siguen pausadas | A.2 | cc:完了 decision literal 24-sep-2026 "Corregir simetrico (Recomendado)" + caso ambas secuencias (A.3d/caso.md) + definiciones (A.3d/definiciones.md) |
| A.3 | `[Salud+aviso]` `[lane:gate]` `[tdd:required]` Exponer ultima corrida principal exitosa, fecha metrica y edad por plataforma; Telegram al fallo, a las 10:30 UTC sin exito de hoy y al recuperarse | A.2 produce aviso por incidente aunque `cycle=done`; productos no lo limpia; fallo y atraso nuevos no se pierden tras una recuperacion pending; entrega HTTP fallida queda pending y reintenta sin duplicar; recovery solo tras exito principal y alerta entregada; estados terminal/superseded, cadencia por episodio y ejecucion observable del chequeo 10:30 quedan definidos antes de implementacion; `/salud` muestra fuente, fecha y estado de entrega sin secretos | A.2, A.3d | cc:完了 #340 merge b3b2c8e (CI pass; opus r4 APROBADO CON OBSERVACIONES docs/evidencia/ads-proteccion-01/A.3/review-a3-r4-opus.md + CodeRabbit; desplegado en ad79eeb; residuales M6 (`episodios_abiertos` scoped sin test) y M3 (cancel global inalcanzable, mutante equivalente documentado)) |
| A.4 | `[Release]` `[lane:release]` `[tdd:skip:verificacion-operativa]` Preparar release de A.2/A.3 sin activar por arrastre el cambio B.2; comprobar cron y avisos sin tocar otros servicios | Runbook posterior fija metodo seguro y plazo de observacion de alerta/recovery; CI completo, cross-review, SHA/backup y readback de `/salud` e `ingest_run` por perfil/plataforma; aislamiento B.2 comprobado; checklist de deploy una vez por SHA, despues del go de deploy | A.3, B.2a | cc:TODO; codigo desplegado por arrastre: A.2 en H5 25bded0 (8cfe7b0 ancestro verificado), A.3 recien en C.5 ad79eeb (b3b2c8e posterior a 25bded0); falta operacion H4 (observacion 7 dias + readbacks) + go |

## Fase B — PAUSE no espera al cooldown de bids [lane:gate]

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| B.1 | `[Spec]` `[lane:gate]` `[tdd:skip:contrato-producto]` Aprobar y escribir en `docs/CONTEXTO.md` la precedencia del PAUSE maduro sobre el cooldown de bids; preservar gates de goal, ancestros, estado, veto en vuelo, quota y revalidacion; fijar efecto sobre inertes y negativos; enlazar delta desde diseno v2 con precedencia explicita | Spec delta aprobado con ejemplos 4925 (14-sep) y frontera de 7d; explica que BID aplicado no frena PAUSE, pero PAUSE/reversa conservan su enfriamiento; no cambia el umbral existente | A.1 | cc:完了 (politica en docs/CONTEXTO.md + spec docs/superpowers/specs/2026-09-24-ads-proteccion-design.md) |
| B.2 | `[Motor]` `[lane:gate]` `[tdd:required]` Separar PAUSE del cooldown originado por BID, sin dos decisiones por entidad/ciclo ni loop tras reversa | Prueba roja previa: 4925 propone PAUSE el 14-sep y BID queda en cooldown; PAUSE aplicada/revertida conserva sus 7d; falta de madurez, `None`, PAUSED, veto pendiente, inertes y frontera exacta discriminan | B.1 | cc:完了 #332 merge beae9ec (CI pass; review bot; desplegado en sombra H5 25bded0; live pendiente de B.4) |
| B.2a | `[Contencion]` `[lane:release]` `[tdd:required]` Resolver la exposicion de B.2 en `master` antes de cualquier deploy, incluido A.4 | Registrar SHA productivo y goals/modos efectivos; demostrar mediante test y readback que el deploy no activa la PAUSE nueva sin go, o elegir y verificar revert; runbook incluye rollback. Sin evidencia, bloquear deploy | B.2 | cc:完了 #339 merge f40270d (CI pass; H2/medicion.md: SHA prod + goals/modos; B.2a/flag.md: test + readback + rollback; encendido config 21 en H5) |
| B.3 | `[Replay]` `[lane:gate]` `[tdd:required]` Congelar procedencia y era para replay, medir propuestas historicas y validar el recorrido cola→48h→revalidacion→apply | Replay de 11–19 sep conserva decisiones antiguas y muestra candidatos nuevos desde el 14; target, config, cache y estado se toman as-of cuando exista historial; lo no reconstruible se marca `unknown` y no cuenta como apply posible; no afirma ahorro ni fecha de apply contrafactual; comparar con readback externo de applies reales | B.2 | cc:完了 #335 merge 93bcdab (CI pass; review bot + H3/pre-merge.md; docs/evidencia/ads/B.3/replay-4925.sql + reporte.md; era pause_after_bid_v1 congelada) |
| B.4 | `[Release]` `[lane:release]` `[tdd:skip:activacion-operativa]` Validar B.2/B.3 en shadow y preparar posible live | Runbook posterior fija ciclos shadow, criterio de parada y rollback; CI completo, cross-review y evidencia de B.3; go de merge, go de deploy y go de live separados; ninguna activacion por el mero deploy de A.4 | B.2a, B.3 | cc:WIP; sombra H5 en curso 1/5 (H5/ciclo-1.txt); falta completar 5 ciclos + go de live H5.4 |

## Fase C — proteccion economica con ventas y por campana [lane:gate]

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| C.0 | `[Trazabilidad]` `[lane:gate]` `[tdd:skip:decision-producto]` Cotejar el mensaje literal que eligio las tres opciones, la extension a `revenue=0` y la modalidad manual de campana con el spec; separar eleccion de producto de autorizacion de ejecucion | Cada opcion tiene cita/fecha o queda `decision_needed`; ninguna falta de evidencia se convierte en aprobacion implicita; el dueno resuelve cualquier discrepancia antes de merge o live | A.1 | cc:完了 cotejo 25-sep-2026 con 3 literales (C.0/cotejo.md: "1 PAUSE sin cooldown", "2 Limite economico", "3 Campana manual") |
| C.1 | `[Spec+decision]` `[lane:gate]` `[tdd:skip:contrato-producto]` Documentar las opciones elegidas, extension a cero ventas y efecto tras revision humana de campana; usar A1U/AU2 y contraejemplos rentables | Spec publicado con regla literal por moneda, modalidad y caso cero ventas; separa riesgo Ads de utilidad neta, define target ausente, floor y mayor umbral adaptativo; reversa de hoja y opt-out. C.0 verifica la cita de aprobacion antes de ejecutar | A.1 | cc:完了 spec docs/superpowers/specs/2026-09-24-ads-proteccion-design.md (regla por moneda, revenue=0, A1U/AU2, modalidad manual) + cita C.0 (C.0/cotejo.md) |
| C.2 | `[Medicion]` `[lane:gate]` `[tdd:required]` Medir la regla documentada con vintages `observed_at<=decided_at` y grano campaign/leaf separado; publicar impacto y falsos positivos | Prueba discriminante excluye observacion posterior al reloj de decision; tabla por ciclo de candidatos, vetos 48h, applies posibles y cambios de regla; target y estado as-of o `unknown` si falta historial; no sumar dinero duplicado ni convertir propuesta en ahorro garantizado; reportar candidatos por peldano de target | C.0, C.1 | cc:完了 #334 merge 4512a5a (head final 0dfea74 gate+rapido success tras rebase; H3/pre-merge.md cubre SHA pre-rebase 97d74388, NO LISTO por CONFLICTING; run post-merge en master cancelled por push posterior sin fallo de tests; review bot; tools/replay_ads_economico.py + tests + docs/evidencia/ads/2026-09-24-proteccion-economica-replay.md) |
| C.2a | `[Replay durable]` `[lane:gate]` `[tdd:required]` Congelar por entidad/ciclo la procedencia y el valor de target para nuevas mediciones, sin depender de `ads_optimizer_goal.updated_at` mutable | Una edicion posterior del goal no cambia el target ni la cobertura de un ciclo ya sellado; prueba con cambio de goal despues del freeze; los ciclos viejos sin evidencia siguen indeterminados | C.2 | cc:TODO; sin evidencia de freeze de target por entidad/ciclo en master |
| C.2b | `[Decision de riesgo]` `[lane:gate]` `[tdd:skip:aceptacion-operador]` Revisar la medicion de C.2 antes de integrar la proteccion economica | Definir falso positivo como candidato que deja de cruzar el limite con atribucion madurada al horizonte elegido; el dueno fija ese horizonte y la tolerancia, decide si el target requiere piso propio y acepta o cambia la regla con referencia al reporte; sin decision literal no hay merge de C.3/C.4 ni live | C.2 | cc:完了 literal "Aceptado" 2026-09-25 (C.2b/aceptacion.md: horizonte 10d, tolerancia 0, sin piso propio; merges C.3/C.4 previos declarados hecho consumado) |
| C.3 | `[Hoja]` `[lane:gate]` `[tdd:required]` Implementar limite economico de C.1 en keyword/product_target y separar su revalidacion de la antigua regla `orders=0` en `apply_cola` | Tests rojos previos: 2423 compara limite literal, cero ventas segun decision final, 100→231 clics, floor, venta tardia, `None`, moneda invalida e inmadurez; una venta sobre limite recorre decision→cola→48h→revalidacion→apply/readback; si deja de cruzarlo se descarta antes del cobro, tambien tras espera por quota; version congelada para replay y politica/target vigentes al aplicar; reactivacion manual y reversa conservadas; aislamiento off por defecto probado antes de merge. Rama preparada no se mergea antes de C.2b | C.2b, B.3 | cc:完了 #341 merge 024c1f7 (CI pass; opus APROBADO docs/evidencia/ads-proteccion-01/C.3/review-c3-opus.md; desplegado en ad79eeb, flag off fail-closed; merge previo al literal C.2b declarado en C.2b/aceptacion.md) |
| C.4 | `[Campana]` `[lane:gate]` `[tdd:required]` Medir campana desde una unica fuente de dinero; emitir propuesta trazable con ancestros y limites visibles en registro separado de `apply_queue` | A1U/AU2 muestran costo, `sales30d` atribuido, target, ventana, estado y motivo; sin doble conteo ni propuesta sobre PAUSED manual; identidad de episodio y dedupe ante cron repetido, retry, venta tardia y cambio de target; propuesta visible por una superficie definida en el runbook, con entrega fallida observable. Rama preparada no se mergea antes de C.2b | C.2b | cc:完了 #342 merge 787869a (CI pass; opus APROBADO docs/evidencia/ads-proteccion-01/C.4/review-c4-opus.md + rb2 + rb3; desplegado en ad79eeb; misma desviacion C.2b declarada) |
| C.5 | `[Cierre humano]` `[lane:gate]` `[tdd:required]` Tras propuesta, el dueno pausa manualmente en Amazon o la descarta; Orbit sincroniza estructura y cierra solo con readback `PAUSED` del mismo campaignId/profile, sin escribir PAUSE/RESUME de campana | Runbook define el canal visible y el descarte autenticado con rol minimo; propuesta revisada con campana ENABLED sigue pendiente; descarte no muta Amazon; PAUSED externo cierra con snapshot/fecha sin inferir autor ni causalidad; cola descendiente no aplica tras el readback; cron repetido no duplica ni reabre hasta nuevo episodio; cero llamadas de mutacion de campana | C.4 | cc:完了 #344 merge ad79eeb (CI pass; CodeRabbit + AI review pass; DoD en C.5/cierre.md; desplegado H6/deploy-c5.md: 0041+0042+0043 + endpoint 200/401; deploy sin go literal registrado, desviacion declarada; master rojo en bateria completa desde ad79eeb hasta #347 (run 36167946686, 1 failed/3432 passed; D.1/deploy.md); pausa manual pendiente: open = []) |
| C.6 | `[Release]` `[lane:release]` `[tdd:skip:despliegue-y-medicion]` Preparar shadow, review cruzada, CI completo, rampa live y seguimiento de resultados maduros; incluir A.3 como senal de datos | Runbook posterior fija ciclos shadow, riesgo aceptado en C.2b, criterio de parada y rollback; comparar candidatos vs applies y resultados por campana/hoja sin lookahead; cero mutaciones sin readback o reversa; deploy y live con go separados y checklist una vez por SHA final | C.3, C.5, A.4, B.4 | cc:TODO; BLOQUEADO hasta cerrar A.4 y B.4 (sombra H5 en curso 1/5; C.6 exige su propio INICIO_SHADOW, runbook H6.4) |

## Fase D — huecos del analisis Kimi 24-sep [lane:gate]

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| D.1 | `[Motor+datos]` `[lane:gate]` `[tdd:required]` Todo lo que no se aplica deja su motivo por decision: tabla append-only `decision_sin_aplicar` (0044) con motivo cerrado por decision y ciclo ejecutor, y vista `v_decision_huerfana` (`sin_registro` / `en_cola` / `huerfana`; excluye filas shadow). Sin tocar `decision_application`, cooldown ni quota | Tests rojos previos por motivo; espejo constante/CHECK; vista discrimina shadow, en_cola y precedencia; bateria completa verde; deploy con readback | - | cc:完了 PR #345 merge `03faa24` (+ #347 `cd6e63d`, master rojo desde C.5); deploy 2026-09-25 23:53 UTC. Evidencia: [`D.1/deploy.md`](../docs/evidencia/ads-proteccion-01/D.1/deploy.md) |
| D.1b | `[Motor+datos]` `[lane:gate]` `[tdd:required]` No bloqueantes de la revision IA de #345: (M2) corte que pierde el choque de clave al encolar queda sin fila de cola y sale `huerfana` para siempre: registrar el choque con motivo propio; (L1) `perdida` se escribe sobre una fila ya `vetoed` (duplica un desenlace terminal); (L4) ficha de `decision_sin_aplicar` y `v_decision_huerfana` en `docs/DATABASE.md`; test estatico kinds de la vista vs `KINDS_QUOTA` (CodeRabbit) | Test rojo previo del choque; `perdida` sin duplicar el veto; doc y test espejo | D.1 | cc:TODO |
| D.2 | `[Decision→Motor]` `[lane:gate]` `[tdd:required]` Evitar que un bid se invierta (caso 3835: +13-sep, −21-sep, termino bajo el original). Ventana de bids 30d (`windows.py:131`) y cooldown 7d: la decision del 21-sep juzgo el bid nuevo con ~75% de datos del bid viejo. Recomendado: tras un BID aplicado, la direccion contraria exige N dias maduros de evidencia posterior al cambio | Decision literal del dueno (N) antes de codigo; test rojo con el caso 3835; misma direccion y PAUSE sin cambio; replay de sep con inversiones evitadas | D.2d: decision del dueno | cc:TODO; decision pendiente |

## Evaluacion y validacion del plan

- **Clasificacion:** Required A.1–A.4 (incluida A.3d), B.1–B.4
  (incluida B.2a), C.0–C.6 (incluida C.2b). C.5 cierra la modalidad
  humana; ninguna fila constituye aprobacion anticipada de PAUSE live.
  Recommended: C.2a para replay futuro estable y aviso de pacing con datos
  intradia en otro bloque. Reject: pausa automatica de campana (el dueno
  eligio la ruta manual), budget automatico y ACoS intradia decisorio.
- **Puntuacion (5=mejor):** recuperar/alertar datos 5 producto, 5 evidencia,
  5 factibilidad, 5 seguridad; PAUSE antes de cooldown 5/5/4/4;
  proteccion hoja 5/4/3/3; propuesta de campana con pausa manual en Amazon
  y readback `PAUSED` en Orbit 4/4/4/5. C.5 implementa solo esa ruta.
- **team_validation_mode: subagent.** Producto/QA, Arquitectura/Seguridad y
  Skeptic revisaron premisas y DoD por separado; el lead cotejo codigo,
  `docs/CONTEXTO.md`, diseno v2 y spec de CORTES 01. Cross-review adicional
  de Fable 5.1 el 24-sep-2026: `REQUEST_CHANGES`; esta revision del plan
  incorpora sus gates de autorizacion, estado, contencion y medicion. `spec.md` raiz no
  existe; la politica nueva esta en el spec fino enlazado. Memoria
  harness-mem no estuvo consultable; se reutilizaron las decisiones
  rastreadas en docs y planes del repo. No se inventa ausencia de otras
  memorias. Product fit: gasto Ads; security fit: ninguna API de mutacion
  Amazon para campanas; el descarte en Orbit requiere rol minimo y
  autenticacion definida en C.5, sin exponer secretos.
- **formatter_baseline: configured.** Ruff en `pyproject.toml`, candados
  `pre-commit`, guardas y bateria completa en `.github/workflows/quality.yml`.
  Cada bug lleva prueba que falla en codigo previo; durante implementacion
  solo tests focalizados, bateria completa una vez sobre SHA final en CI.
  Reproduccion de produccion antes de test de invariante, reviewers sin
  bloqueantes y conciliacion externa son DoD de cada bloque.
- **Secuencia verificable:** A.1 precede a la medicion con datos nuevos.
  A.3d precede a cualquier arreglo nuevo de A.3. B.2a precede a TODO deploy
  posterior al merge B.2. C.0 precede a integrar la politica; C.2 mide y
  C.2b exige aceptar el riesgo antes de mergear C.3/C.4. Preparar una rama
  draft no satisface ni salta ese gate. C.6 espera C.3, C.5, A.4 y B.4.
  El runbook posterior concreta comandos y parametros de observacion.

## Operaciones que requieren go del dueno en el runbook posterior

| Operacion | Gate previo | Alcance |
| --- | --- | --- |
| Continuar implementacion y PR preparados | Plan y runbook aprobados; A.3d para A.3; C.0 y C.2b para integrar C.3/C.4 | A.2–A.4, B.2a–B.4, C.0–C.6 |
| Merge de cada PR | DoD, review y CI sobre SHA final; go especifico de merge | A.2/B.3/C.2 mergeados; resto y PR futuros |
| Deploy de `orbit-app-1` | B.2a resuelto; backup, SHA, rollback y go especifico de deploy | A.4, B.4, C.6 |
| Activacion live de cortes | Shadow y riesgo medido aceptados; go especifico de live y readback | B.4, C.6 |
| Pausa manual de campana en Amazon | Decision humana sobre propuesta concreta; Orbit solo verifica estado externo | C.5 |

La revision de este plan no concede ningun go. El merge/deploy del PR #329
fue autorizado aparte el 24-sep-2026; no autoriza cambiar goals, presupuesto
ni reactivar campanas. El runbook se redactara despues de aprobar el alcance
del plan y presentara por separado cada operacion pendiente.
