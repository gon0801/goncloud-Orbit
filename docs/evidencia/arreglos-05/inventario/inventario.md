# Inventario de planes y dependencias de Orbit

Corte: **15-nov-2026**, checkout `/Users/dn/dev/goncloud-Orbit-arreglos-05-plan`, **`ff03ed61f7a847ab190b1695c2efe8d785baa364`**, limpio al inicio. Solo lectura; no suite, endpoints externos, producción, subdelegación ni cambios en el repositorio. Se leyeron AGENTS, CONTEXTO, el diseño v2, los **74 `plans/*.md`**, manifest, ROADMAP, evidencia y Git local. No existe Graphify en este checkout.

**Por implementar: ARREGLOS 03, ARREGLOS 04 y TEMPORADA 01. ARREGLOS 05 está por redactar y construir**, con el alcance legado y cierre de los 14 hallazgos revalidados. No sumar como nuevas implementaciones ARREGLOS 01/02, ORBIT 07, CLARIDAD 01 ni DISEÑOS 01.

**Actualización del lead:** las huellas de los **202 archivos de runtime** (`.py/.js/.css/.html`) coinciden con `ff03ed61`. Evidencia comunicada: `docs/evidencia/arreglos-05/base/huella-local.json` y `huella-prod.json`, SHA conjunto abreviado `e13a6f...a597967`. Esta lectura productiva la hizo el lead; no la repetí. Acredita presencia del código, **no todas las migraciones, configuración, crons, actas o efectos externos**. La consulta de estado/esquema también fue comunicada; no inferí resultados no aportados.

## Cola de implementación completa

| Frente | Pendiente real | Evidencia y dependencia |
|---|---|---|
| **ARREGLOS 03** | **17 bloques**: BASE; D-A/B/C/E/F/G/H/I/J/K/L; P-A/B/C/D/F. Ingesta, día cerrado y horarios, search terms, devoluciones/moneda original del ledger, avisos, reputación y precios. | `plans/arreglos-03.md:3,25`; los caminos que se propone retirar aún existen (`app/ads/reports.py:1846`, `app/reputacion_clientes.py:199`). ARREGLOS 01 X y ARREGLOS 02 C ya integrados. D-G es dueño actual del hallazgo 05. |
| **ARREGLOS 04** | **19 bloques**: W-A a W-O y B-A a B-D. Reloj/cobertura en pantallas, errores/avisos, fecha manual, goals, fábrica/retiro, presupuesto con confirmación, bloqueos y listas completas. | `plans/arreglos-04.md:3,23,192,252`. W-N/W-O son planes incorporados, no código entregado. X de ARREGLOS 01 ya integrado. W-D cubre 16; B-C solo parte de 02. |
| **TEMPORADA 01** | **10 bloques**, orden I → A → B → C → D → P → E → F → G → H: margen al costo vigente, calendario, episodios, práctica, readback previo, reactivación, propuestas de presupuesto y live. | `plans/temporada-01.md:3,33`; `b82f8f85` modifica contrato/plan. No existen módulos ni migraciones de temporada. I parte de la vista corregida por 0086; política conserva centavos/revisión de ARREGLOS 02. |
| **ARREGLOS 05** | **Sin archivo de plan todavía.** Legado de ciclo/apply/settings, tope aproximado de 150 pujas y retirada de modos sin uso, más aceptación completa de los **14 IDs** vigentes. | `docs/evidencia/arreglos-2026-11-12/HALLAZGOS.md:13,28,38,101,116,118`; petición actual. La secuencia expresa deja legado después de TEMPORADA. No duplicar los arreglos que cierren 03/04: exigir su resultado observable en el commit conjunto. |

El manifest contiene 47 entradas y mantiene `active=disenos-01`; **ARREGLOS 01 y TEMPORADA 01 están fuera**. ROADMAP tiene base operativa del 4/5-nov y todavía dice que el registro apunta a Resumen (`plans/ROADMAP.md:3,19`). Ninguno debe usarse como cola vigente sin esta reconciliación. Los otros archivos fuera del manifest son briefs, validaciones y ROADMAP; el JSON enumera los 74 sin omisiones.

## Ya implementado: no empezar otra vez

| Plan | Integración comprobada | Resto, con límite de evidencia |
|---|---|---|
| **ARREGLOS 01 A-Z** | Cadena `3524b41b`…`976df4dc`; Y `ac539365`, Z `f53ad3f8`, S `de895d18`. | Runtime coincidente según lead. A documenta cola ya vetada; C documenta reversas confirmadas; U documenta `economia_cambio_v2` en MX/US. Consolidar actas/migraciones restantes sin repetir esas operaciones (`docs/evidencia/arreglos-01/{A/cola-26.txt,C/reversa.txt,U/encendido.txt}:1`). |
| **ARREGLOS 02 A/D/B/C** | A `dabfac13`, D `9f0181f6`, B `5b73e2ed`, C `8d5a84c9`; todos ancestros de HEAD. | Runtime coincidente. 0086 y reversa están en Git; su aplicación necesita evidencia de esquema. Replay B/C dice `distintas 0`, previa C dice `0`. Recuperar resultado D.4; `D/despues.sh` no acredita ejecución y vence a >3 días (`plans/arreglos-02.md:48`). |
| **DECISIONES PRODUCTO 01 / AUTO-03** | A-I integrados, cierre `f86ed7dc`. | La pausa está lista para vivo y permanece en práctica por decisión documentada; encenderla no es implementación faltante (`plans/decisiones-producto-01.md:145`). No deducir modo actual de hashes. |
| **ORBIT 07** | `7cf7c9ab` (#547); M1/M2/M4/M5/M6 presentes, M3 omitido justificadamente. | Runtime coincidente. Los 145 checks antiguos abiertos no implican 145 tareas pendientes. `estado.md:14` es foto previa; `activacion-orbit07.md:3` es receta, no recibo. U de ARREGLOS 01 acredita activación posterior de v2, sin certificar toda operación. |
| **CLARIDAD 01** | `c44bd995` y entregas posteriores, `4be5081c`, `0e5abaf0`; manual y evidencias presentes. | Runtime coincidente. Lectura productiva y correcciones del manual documentadas en `docs/evidencia/claridad-01/J/lector.md:1`. Manifest «pendiente de lanzar» obsoleto; solo consolidación operativa residual. |
| **DISEÑOS 01 A/B/C** | `19c2c7b3`, `f15ffc77`, `c6cb744f`. | Runtime coincidente. Comparaciones guardadas son locales/copia RO; recuperar actas productivas A.4/B.4/C.3, sin rehacer pantallas (`docs/evidencia/disenos-01/A/comparacion.md:5`, `B/comparacion.md:54`, `C/comparacion.md:86`). |

## Implementado con seguimiento; bloqueos externos

«Pendiente de comprobar» no afirma defecto ni ausencia de despliegue. Producción actual no fue auditada aquí.

| Frente | Pendiente y cómo cerrarlo | Evidencia |
|---|---|---|
| **FABRICA 01 + FABRICA UI 01 + FABRICA 02 / P02** | Un único cierre operativo: anexar paso 4 de tarea 11; observar primer harvest natural por grupo, hermanas, biblioteca y ledger; ensayo de reversa previsto. La sonda de creación del 9-sep ya ocurrió. D.4 ya cerró. Corregir/revalidar 02/03/10 antes de certificar la ruta. | `plans/fabrica-01.md:5102,5110`; `plans/fabrica-02.md:417,418`; `plans/ROADMAP.md:32`. |
| **ADS PROTECCION 01 C.6 / P03** | Código entregado, económica apagada en última foto. P2 tiene 48 cruces, elegibilidad/ventanas/maduración incompletas; no demuestra FP=0. Completar P2, expediente P3, cinco ciclos propios P4 y live P5 con sus gates separados. | `plans/ads-proteccion-01.md:213,261`. |
| **BIDS 01 / P07** | Herramienta entregada; primer lote manual de archivo sin registro en última consulta. Datos actuales y go específico; reparar reposición 11 antes de operar. Reponer no equivale a recuperar identidad/historia archivada. | `plans/bids-01.md:3,1302`; `plans/ROADMAP.md:37`. |
| **JEV ADS 03 / P08** | Construido y encendido documentadamente; medición de 30 días programada para **17-nov-2026 09:00 UTC**. Leer resultado cuando exista, sin adelantarlo ni darlo por ejecutado. R.1/R.2 ya cerradas. | `plans/ROADMAP.md:38`; `plans/jev-ads-03.md:107,108`. |
| **CATALOGO CAMPANAS 01 / ORBIT 19 / P09** | Núcleo entregado; falta cierre específico de conciliación 0.3, cobertura solo-actividad y Featured Offer en este consumidor. Cliente SP-API disponible no prueba consumo correcto. | `plans/catalogo-campanas-01.md:98,116`; `plans/ROADMAP.md:39`. |
| **SP-API 02** | Código/despliegue cerrados; acta conserva solo nocturna **anterior** al despliegue del 22-oct. Recuperar recibo de nocturna posterior; no afirmar que no corrió. | `plans/sp-api-02.md:9`; `docs/evidencia/sp-api-02/ejecucion/C.1/NOTA.md:20`. |
| **RESUMEN ADS 01-04** | Integrado y hashes productivos documentados; **P01 cerrado** por job10 (812/812 filas y 203/203 días). Falta revisión visual productiva expresamente declarada; no reconstruir las cuatro fases. | `plans/resumen-ads-multicanal.md:127`; `plans/ROADMAP.md:31,83`. |
| **ORBIT 02 3.4 / P06 — externo** | Migración/apagado cerrados; cloudflared espera inventario de rutas y ventana de rotación. No hay nueva lectura que cierre ese bloqueo. | `plans/orbit-02.md:62`; `plans/ROADMAP.md:36`. |
| **REPUTACION 01 / P05 — externo y contrato contradictorio** | MeLi v1 entregada; Amazon sin reseñas/ingestas exitosas en última foto, crédito Apify como bloqueo histórico. **ARREGLOS 03 D-B prevé retirar Apify**: conciliar esa decisión antes de recargar o reactivar por el roadmap viejo. | `plans/reputacion-01.md:3`; `plans/ROADMAP.md:35`; `plans/arreglos-03.md:48`. |
| **PEZ 02 / P13 — externo** | Ocho de nueve descripciones aplicadas; solo `MLM2727437671` inhabilitada por categoría, edición HTTP400. **22mm ya confirmado**. No recostear ni reabrir PEZ01. | `plans/pez-02.md:3,77`; `b509acb2`. |

## Histórico cerrado o sustituido

Se conserva el alcance original: un defecto nuevo no vuelve a convertir su módulo entero en proyecto pendiente. Las siguientes clasificaciones combinan entrega integrada/presencia del artefacto y actas históricas; no son una certificación nueva de cada módulo.

| Planes | Evidencia de entrega / límite |
|---|---|
| ORBIT 01, ORBIT 03, ORBIT 04 | Esquema `2418544e`; ingesta/shadow `2bf6b012`, `00d9ec2e`; apply `8d118530` y `plans/orbit-04.md:293,319`. |
| GUARDRAILS 01, DASHBOARD 01 / ORBIT16 | Candados presentes (`tests/test_architecture.py`, `c1d2b4e3`); dashboard con edición real documentada (`plans/dashboard-01.md:149`). |
| CAMPANAS 01, CAMPANA ACTIVA 01, CORTES 01 | Reactivación `7923f23d`; ancestros en `plans/campana-activa-01.md:126`; adaptativos `69685402`. No confundir campañas entregadas con futuras campañas MeLi. |
| ORBIT 05 preflight y ORBIT 05 / AUTO-01 | Preflight `plans/orbit-05-preflight.md:56`; cierre real 1-oct `docs/evidencia/orbit-05/cierre-2026-10-01.md`; harvest por grupo queda en P02. |
| ORBIT 06, ESTRUCTURA 01, CORTES UI 01 | `plans/orbit-06.md:3,122`; separación presente `app/ads/structure_plan.py` y `plans/estructura-01.md:194`; confirmación del dueño en `plans/cortes-ui-01.md:62`. |
| ORBIT UI 01 / ORBIT18 | PR181 integrado según `plans/orbit-ui-01.md:21`; DoD de entrega excluía despliegue (`:27`). |
| MARGEN ESTIMADO 01 / AUTO-10, REPRICING 02 / AUTO-07 | Original FBA MX entregado; ampliaciones FBM/US/MeLi en `plans/repricing-02.md:167,216`. Costos PEZ cerrados después. El live documentado en octubre no certifica modo actual. |
| REPRICING 01 | **Sustituido**, no 22 tareas nuevas: `plans/repricing-01.md:3` remite a REPRICING02 para D.2/D.3/E/0/B/M. |
| BIDS 02 | Política `niveles_v3` y herramientas manuales entregadas; remate/encendido documentados en `docs/CHAT-CONTEXT.md:20`. Automatismos AUTO04-06 siguen siendo ideas sin plan. |
| SP-API 01, INGESTA 01 | Cliente/lecturas y deploy en `plans/sp-api-01.md:113,119`; informe de ingesta 28-oct recuperado, `plans/ingesta-01.md:3` y `docs/evidencia/estado-planes-2026-11-04/README.md`. |
| JEV ADS 01 y JEV ADS 02 | Piloto/señales entregados, avisos apagados por decisión; `plans/ROADMAP.md:78`, `docs/CHAT-CONTEXT.md:26`, módulos Jev presentes. No inventar autorización de ruteo/fábrica. |
| PEZ 01 / P04 | `3470797f`, `plans/pez-01.md:63`: nueve correspondencias/costos conciliados; P13 separado. |

**Auxiliares sin implementación independiente:** ADS PROTECCION Fase D, BIDS02 construcción, REPRICING02 construcción y JEV02 bloque S acompañan entregas cerradas. Las dos validaciones de Catálogo/Reputación son snapshots. Los **22 briefs** se asignan a sus padres en el JSON; ninguno añade otra cola: incluyen Fábrica01/02, PR177, Reputación01, SP-API01, modos de goals (`858c767a`) y vigilante (`359f1f85`, corregido después por `ebc56d55`).

También se registra `plans/jev-ads-03.md-E`: copia anterior con TODO, sustituida por el plan vigente. Fuera de `plans/`, `docs/PLAN-DB-GROK.md:3` cerró con `2418544e`; Orbit Bench (`docs/superpowers/plans/2026-09-05-orbit-bench.md:3`) está implementado en `ba24c44c`, aunque conserve checks abiertos. No convertirlos en pendientes.

## ARREGLOS 05: cobertura que debe quedar explícita

El informe vigente de `/private/tmp/orbit-review-current-evidence/` revisa **este mismo SHA**. Sus 14 hashes de artefactos coinciden con su manifest. Documenta **31 casos: 17 rojos correspondientes a 14 hallazgos y 14 controles verdes**, sin errores de fixture ni omitidos. **No ejecuté esos casos**. La CI verde de 5996 pruebas, también documentada allí, no invalida estos defectos.

| ID | Impacto y cierre observable | Dueño/solapamiento |
|---|---|---|
| 02 | Una lista incompleta o sin contenedor permite duplicar creación. Exigir completitud; cero POST/DELETE ante ausencia no demostrada. | 04 B-C cubre paginación, **no contenedor ausente**; completar en05. |
| 03 | Reversa archiva keyword ajena. Exigir evidencia durable de creación propia, no coincidencia textual. | 05; mismo lector/reversa que02/10. |
| 04 | Retiro/impulso envía selección anterior tras fallar nueva previa. Invalidar plan, botones, confirmación y payload; cubrir respuesta tardía. | 05, después de04 W-M/W-N. |
| 05 | Search terms publica subtotal corrupto como completo. Una fila inválida no recrea la clave ni produce subtotal válido. | 03 D-G;05 verifica el caso exacto. |
| 06 | Scan MeLi incompleto desactiva ausentes. Completitud explícita antes de bajas. | 05; coordinar03 D-B. |
| 07 | Cotización MeLi pierde fecha/desglose y afecta evaluación. Conservarlos en camino fresco **y persistido**. | 05 tras03 P-B/P-F; P-B solo no corrige causa. |
| 10 | Negative reconciliado queda sin reversa. Guardar identidad durable que la reversa consume. | 05; también legado apply7. |
| 11 | Reposición repite POST tras ACK y lectura ambigua. Intención antes del HTTP, reconciliar antes de repetir. | 05; también legado apply11. |
| 12 | Target queda 29,29,29 con ledger rancio. Avanzar desde estado aplicado:29,28.5,28, sin inventar margen fresco. | 05 después de TEMPORADA I/D. |
| 13 | Reobservación de término maduro revierte lote por sello temporal. Reloj común coherente antes de persistir. | 05 después de TEMPORADA B/D. |
| 14 | Catálogo cuenta escrituras revertidas por savepoint. Contar solo las que sobreviven. | 05 junto con06. |
| 15 | Ruido publica ACoS con gasto desconocido. Cobertura explícita; ratio ausente cuando falta gasto. | 05 tras04 W-E; madurez no equivale a cobertura. |
| 16 | Resumen dice sin ventas aunque la API trae ventas. Distinguir dato faltante de cero. | 04 W-D.2/W-D.4;05 verifica el caso exacto. |
| S1 | Error de Jev puede persistir secreto devuelto por proveedor. Redactar antes de guardar model/choice inválidos. | 05, coordinado con03 D-J; caso condicional, no filtración real demostrada. |

Rutas/líneas y nombres exactos de pruebas están por ID en `inventario.json`, tomados de `matriz.tsv`. Comando de aceptación ya existente: `bash /private/tmp/orbit-review-current-evidence/run-acceptance.sh`, siguiendo `reproducir.txt`; no se ejecuta en esta tarea. Conservar controles **01,08,09,D2**, ya resueltos; **D1** es nota de contrato, no bloqueo.

El legado adicional de05 no desaparece por cerrar esos14:

- **Ciclo F1-F4:** contener fallo individual sin omitir otros bids/cortes; permitir recuperación de apply fallido; reclamar proceso muerto según lease; reconciliar antes de decidir. El código aún muestra bloqueo de `degraded` y regla paralela de3h (`app/cycle.py:247`), y reconciliación en fase posterior (`:3530`). Reproducciones antiguas en `docs/evidencia/arreglos-2026-11-12/ciclo.md:26`; revalidarlas tras TEMPORADA.
- **Apply:** cliente protegido en todos los escritores; revisar reversa del bid tras readback previo que entrega TEMPORADA E; veto de práctica no bloquea hoja para siempre; tope real por ciclo, **aproximadamente150, excluyendo entrada/regreso de temporada**; reversa del negative reconciliado y reposición durable (10/11, no dos arreglos más). `HALLAZGOS.md:38,43,44,48`; `app/apply.py:1300`, `app/apply_cola.py:160`.
- **Settings:** rangos manuales y error visible al escribir `1,000`, hoy convertido con `Number` (`app/static/js/settings.js:99`); separar tope por ciclo de cuota diaria. `HALLAZGOS.md:101,109,116`.
- **Modos legacy:** no borrar por búsqueda de texto. Contrato `docs/evidencia/arreglos-02/DISENO.md:417`: política apagada, meta producto en práctica, economía auto03 y flag sin lector; proteger replay y reversa deV. **Conservar pausa producto en práctica y apagadores vigentes**. La práctica de temporada solo sería retirable cuando su live esté acreditado, no por integrar código.
- **Sospecha fiscal:** IVA de comisiones en estimación, `HALLAZGOS.md:115`; requiere fuente externa antes de llamarlo defecto. La decisión sobre IVA retenido ya está tomada; no inventar otro impuesto ni corregirlo por suposición.

## Dependencias reales y orden conservador

**0. Solo evidencia residual:** runtime ya coincide. Cruzar esquema/actas suministrados por lead con migraciones y resultados esperados; recuperar comparaciones finales. Nada justifica reiniciar01/02/07/CLARIDAD/DISEÑOS.

**1. ARREGLOS03 → 2. ARREGLOS04 → 3. TEMPORADA01 → 4. ARREGLOS05.** Es una cola conservadora para minimizar conflictos; **no afirma que03/04 sean requisitos técnicos completos de TEMPORADA**. Los planes autorizan paralelo, pero ambos pueden cambiar archivos comunes. La dependencia fuerte previa de TEMPORADA es01/02; la secuencia05 legacy después de TEMPORADA sí está explícita (`HALLAZGOS.md:13`). El inventario permite preparar05 ahora sin fingir que ya conoce el código posterior a TEMPORADA.

| Cruce | Archivos / contrato | Regla de orden |
|---|---|---|
| TEMPORADA ↔05 | `cycle.py`, `apply.py`, `apply_cola.py`, `lecturas_caso.py`, `optimizer/{caso,politica}.py`, `config_write.py`, `api_write.py`, settings JS/HTML | Terminar temporada antes del legacy; releer firmas/replay/reversas, no aplicar parches por líneas viejas. |
| 03 D-E ↔04 W-C/W-D | `dia_cerrado.py`, `dashboard_resumen.py`, `api_dashboard.py` | Preferir03 D-E primero.04 puede consumir `hoy_resumen` existente; no crear otra tabla de zonas. |
| 03 ↔04 | `cli.py`, `notifica.py`, `api_dashboard.py`, `ui.py`, cortes y crontab | Serializar ediciones compartidas; paralelismo solo con propiedad explícita. |
| 04 W-N ↔ TEMPORADA G | `api_write.py`, `campana_ajustes.py`, `dinero.js`; aviso/confirmación | Si W-N ya está integrado, G consume `aviso_presupuesto` y `acepto_presupuesto_alto`; propuesta automática mantiene topes. `plans/temporada-01.md:8`. |
| 04 B-C/B-B ↔05 02/03/10 | `apply_harvest.py`, reconciliación, `apply.py`, `bloqueo_dueno_efecto.py` | Un lector completo y una evidencia de propiedad; no dos soluciones parciales. |
| 04 W-M/W-N ↔05 04 | `productos.js`, `api_fabrica.py`, `impulso_io.py`, retiro | Un estado de previa vigente para selección y confirmación. |
| 03 precios/reputación/Jev ↔05 06/07/14/S1 | `reputacion_clientes.py`, `meli/catalogo.py`, `meli/precio_mercado.py`, `precio/corrida.py`, `jev_juicios.py`, `jev_libro.py` | Resolver en una sola cadena por consumidor; mantener prueba fresca/cache y contadores de transacción. |
| Migraciones/despliegue | `migrations/`, `tools/despliegue/{comun,desplegar}.sh`, DATABASE/DEPLOY y pruebas del instalador | **0086 ya ocupada**. Numerar sobre base final, conservar etiquetas/reversas. Que un plan cite checklist no significa que tenga que editarlo. |

P02 y P07 necesitan rutas seguras antes de operar; P03 no debe superponer su sombra global cero-applies con encendidos de TEMPORADA. **P08 sigue su calendario; P06/P13 sus bloqueos externos. Ninguno bloquea indiscriminadamente todo el desarrollo.**

## Fuera de la cola autorizada

No presentar como implementaciones comprometidas: **AUTO04 placements, AUTO05 presupuestos, AUTO06 reactivación/limpieza, meli-ads-01, AUTO08/promociones-01, envios-01, AUTO09/REPUTACION02 e integraciones cruzadas** (`plans/ROADMAP.md:53` a`:60`). REPUTACION02 es un **stub sin brief** (`plans/reputacion-02.md:5`), aunque figure en manifest. MeLi Ads conserva contrato proposal-only.

Tampoco añadir automáticamente JEV E.1/E.3/fábrica, P10 reseñas compartidas, P11 lotes Apify, P12 transversales, ni una refactorización global posterior (`HALLAZGOS.md:123`). ADS PROTECCION conserva nueve notas TODO antiguas no bloqueantes en `plans/ads-proteccion-01.md:127` a`:140`; están enumeradas en JSON como **notas no revalidadas**, sin inventar cierre ni convertirlas en autorización. Si alguna coincide con causa actual, se revisa por ese impacto.

## Evidencia que resolvería las incertidumbres restantes

Recibos/sondas de0083-0086 y configuración vigente; comparación D.4 con referencia válida; actas finales de DISEÑOS; primer caso natural de Fábrica02; cobertura/maduración de C.6; resultado de JEV del17-nov; nocturna posterior a SP-API02; conciliación y Featured Offer del catálogo; estado externo de la publicación PEZ. Son lecturas o recuperación de evidencia concretas, **no preguntas al usuario ni motivos para reconstruir código entregado**.

La revisión futura de05 debe usar el commit combinado: aceptar los14 casos, conservar controles ya verdes y comprobar los flujos vecinos que comparten causa, además de CI/secretos y UI afectada. No sumar bloqueos de estilo sin reproducción e impacto. Este inventario no ejecutó suite ni verificó efectos externos.

Validación de entrega: JSON válido, cobertura74/74,14 hallazgos y Gitleaks sin secretos. Sin cambios a archivos versionados; apareció la carpeta no versionada `docs/evidencia/arreglos-05/` comunicada por el lead. Este trabajo solo escribió los dos inventarios.
