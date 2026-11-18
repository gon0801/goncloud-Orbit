# Candidato B: cerrar por propietario

## Problema y alcance
Cerrar los 14 pendientes TN y el legado asignado a ARREGLOS05, conservando las decisiones de negocio. Este paquete es documental: no modifica producto, planes compartidos, permisos ni producción. No se ejecutaron suite, red, despliegue ni PR. Flujo de architect: grounding y boceto completados; implementación y arena excluidos por encargo. No hay síntesis entre candidatos en este documento.
Base del review: `ff03ed61f7a847ab190b1695c2efe8d785baa364`; repositorio leído: `/Users/dn/dev/goncloud-Orbit-arreglos-05-plan`. No existe `graphify-out/graph.json`. Las firmas de `sketch.py` son propuestas, no APIs disponibles.

## Uso: tres llamadores antes de los tipos
Los nombres se distribuirían entre los propietarios del mapa, no en un módulo transversal `sketch`.
```python
# app/cli.py: un horario puede completar apply sin volver a decidir.
from app.cycle import atender_plataforma
resultado = atender_plataforma(conn, plataforma, ahora=ahora)
# Devuelve origen de decisiones, ejecución actual y pendientes; el caller no arma fases.

# tools/archiva_inertes.py: la misma orden retoma lectura, nunca recrea a ciegas.
from app.ads.archivar import reponer_keyword
resultado = reponer_keyword(conn_admin, archivo_id, actor=actor)
# El propietario conserva intención, ACK, readback y reversa por fila archivada.

# app/meli/precio_mercado.py: fresca y persistida pasan por el mismo adaptador.
from app.meli.precio_mercado import cotizacion_desde_evidencia
cotizacion = cotizacion_desde_evidencia(precio, evidencia_fee)
# Conserva fecha y árbol originales; retorna puerto.Cotizacion o error de dominio.
```

## Evidencia y fronteras confirmadas
Fuentes: `docs/CONTEXTO.md`, `docs/APPLY.md`, diseño histórico V2, `plans/arreglos-01.md` a `04.md`, `plans/temporada-01.md`, `docs/evidencia/temporada-01/DISENO.md`; grounding y review en `/private/tmp/orbit-review-current-evidence/{informe.txt,matriz.tsv,test_accept_*.py,criterio-cierre.txt,reproducir.txt}`. Los reportes `docs/evidencia/arreglos-2026-11-12/{HALLAZGOS,ciclo,apply,estimacion,web}.md` aportan legado; el estado actual prevalece.
Lectura dirigida: `cycle.py:257` mezcla degraded con bloqueo diario; `:2928` descarta el ancla convergente por procedencia; `:1224` condiciona el sello a corte; `:3530` agrupa fallos de apply. `apply_harvest.py:418/517` contiene lectores permisivo/estricto; `:1752/1831` permite cambiar ID conservando autoría. `apply.py:1559` busca solo ACK `ok`; `archiva_inertes.py:850/915` guarda después del readback. Estas fronteras bastan; no se repitió auditoría amplia.
`reputacion_clientes.py:170`, `meli/catalogo.py:289/394`, `meli/precio_mercado.py:189/524/548`, `precio/puerto.py:165` y `precio/tipos.py:343` confirman cobertura, savepoints y pérdida de fecha/detalle. `productos.js:445`, `pantalla_ruido.py:274`, `jev_juicios.py:287/316/325` confirman los demás caminos.
La evidencia recibida `docs/evidencia/arreglos-05/base/{consulta-estado.sql,estado-prod.txt,esquema-prod.txt}`, de 2026-11-16 01:43 UTC, muestra 191 ciclos (159 live/done, 30 shadow/done, 2 off/skipped), 31 shadow/pending_veto y nueve goals live. `keyword_archivo_manual` vacía, cero IDs de reposición duplicados y cero negatives `ok:reconciliado`: facilita migración sin backfill; no demuestra que los defectos sean imposibles.
Ambas políticas son niveles_v3, metas de producto live y economía producto_directo_v1; pausas de producto siguen shadow. Caps bid/cortes: 1e6. Precios global shadow domina universos live. Nada de esto autoriza borrar historial/replay, encender pausas o precios, cambiar los caps de cortes ni inventar tasas. Retirar selectores redundantes no vuelve una configuración ausente o ilegible permiso para escribir: se conserva abstención segura.
Web1 Settings NO queda abierto entero: `config_write.py:390/454-457` ya exige la banda canónica al cambiar la meta manual (01X). Se conserva como control. Goals sigue en 04 W-I. El residuo de Settings es cuota `1,000`/null explícito y compatibilidad con campos TEMPORADA.

## Forma y alternativas
| Forma | Complejidad que oculta | Complejidad expuesta y coste |
| --- | --- | --- |
| A, elegida: propietarios existentes con operaciones completas | Cada dueño resuelve intención, identidad, lectura, resultado y compensación de su dominio; caller hace una llamada | Persisten varios registros durables con contratos diferentes; hay que verificar sus fronteras |
| B: coordinador y journal transversal tipado | Unificaría reserva, retry, fases y contadores de todas las mutaciones | Cada dueño tendría adaptador, traducción de estados y doble escritura durante migración; cola de cortes, episodios, fábrica y reposición perderían dueño claro |
Elijo A por profundidad de interfaz y migración acotada. B no borra los ledgers de negocio ni sus invariantes; añade un segundo protocolo para mantenerlos sincronizados. Adoptaría de B únicamente resultados tipados e intención durable, dentro de cada propietario. No creo framework de jobs, Redis ni cola adicional.
Acepto varias máquinas de estados a cambio de preservar contratos sellados. Acepto que una creación incierta quede pendiente a cambio de no duplicarla. Acepto una migración pequeña para reposición y cupo durable a cambio de cerrar las ventanas de crash. No prometo exactly-once sobre Amazon.
Red flags: se eliminan lectores alternativos y accesos HTTP directos al migrar callers; ninguna fachada pasamanos ni etapas load/validate/save públicas. El dato canónico se valida al entrar, no en cada consumidor. No se sincronizan dos listas de campos Settings. Las dependencias externas reciben operaciones completas; auxiliares privados quedan internos y el candado arquitectónico existente vigila los imports migrados. Trayectos ordinarios: caller → dueño → cliente, máximo tres módulos.

## Mapa modular y propiedad de los datos
| Dueño | Datos y superficie; eliminación concreta |
| --- | --- |
| `cycle.py` | Envelope, claim vigente, ancla base y sello temporal; elimina heurística 3h y bloqueo diario por degraded |
| `apply.py` | Intentos por decisión, cupo de bids, confirmación y reversa; resumen deriva del ledger, nunca suma una segunda contabilidad |
| `apply_harvest.py` + reconciliación | Job y evidencia de IDs; una lectura completa pública, validadores existentes compartidos; desaparecen caminos permisivos |
| `apply_cola.py` | Cortes, veto y conflictos; no recibe bids ni reposiciones |
| `ads/write.py` | Único transporte mutante Ads, perfil fijo, allowlist y parseo por ítem; no decide política ni cobra cuotas |
| `ads/archivar.py` | Intención/reposición de keywords en `keyword_archivo_manual`; CLI solo argumentos. Fábrica conserva su ledger y dueño actuales |
| `reputacion_clientes.py` / `meli/catalogo.py` | Cobertura certificada en origen / escrituras por item y resultado solo tras commit |
| `meli/precio_mercado.py` | Adaptación única fresca/cache al puerto existente, Importe y Fees con fecha/árbol |
| `productos.js` / `pantalla_ruido.py` | Estado único de previa / cobertura por métrica, respectivamente; sin capa genérica UI |
| `jev_libro.py` / `config_write.py` | Redacción antes de persistir / validación canónica y versión optimista append-only |
| `temporada_io.py` | Único dueño de episodios/efectos; 05 lo consume, no crea otro registro estacional |

## Transiciones, concurrencia, retry y rollback
1. Ciclo: claim por plataforma → conciliación antes de TX2 → snapshot/decisión → TX3 durable → apply → cierre. TEMP B.2 es dueño de mover conciliación. Si falta TX3 válido se vuelve a decidir; si existe, se retoman solo decisiones no terminales del ciclo original. Una ejecución de recuperación tiene ID propio vinculado al origen: no reabre `done`, no duplica decisiones, conserva `applied_cycle_id` ejecutor y el cupo del origen. Consulta de pendientes antecede al atajo «ya done hoy». Cada retoma revalida modo, goal, ancestros y lectura actual; la conciliación de bids solo lee (control 01), nunca reenvía una puja incierta ni escribe con goal apagado.
2. El claim atómico, heartbeat y reloj DB existentes mandan; desaparece 3h. Owner perdido aborta TODAS las mutaciones. Fallo local de una puja deja su intento incierto y permite otras entidades/fases; fallo de DB, credenciales/perfil o ownership es global. No capturar indiscriminadamente todo y continuar. El cambio frente al antiguo aborto del lote en `docs/APPLY.md` se documenta como delta de ciclo1, sin relajar «5xx no reintenta».
3. Reserva durable del intento antes de HTTP; entidad con intento incierto queda bloqueada para nuevas mutaciones hasta conciliación. Claim/actualización por estado esperado y ownership en la misma TX; precheck antes de HTTP. El guard por sí solo no cerca Amazon: retener un candado advisory de sesión por plataforma durante el despacho evita solapar dos procesos vivos tras vencer TTL. Si cae esa conexión con HTTP en vuelo, la reserva incierta sigue bloqueando la entidad; una ausencia leída no la libera automáticamente. No nuevos grants.
4. Cupo: hasta 150 bids ordinarias por `cycle_id_origen` y plataforma, siempre además del cap diario. Reserva en TX con intento, único por decisión lógica, antes del primer HTTP; los ambiguos consumen y nunca se devuelven. Retomar cuenta las reservas existentes, no arranca contador. 429 del mismo intento no recobra; rechazo después de enviar conserva consumo. Solo entrada/regreso estacional identificados por inputs/version de TEMP quedan fuera; `Recupera`, ajuste ordinario y motivos desconocidos cuentan. No inferir excepción por signo de delta ni excluir todo origen temporada.
5. Harvest: ID del ACK es inmutable; readback por ese ID más identidad completa. Otro ID coincidente puede describir efecto observado, nunca hereda `keyword_creada=True`. Reversa automática o de cosecha elimina solo lo creado con evidencia vinculada a ese ID; adoptado/incierto se informa y se conserva. No reescribir ACKs para fabricar propiedad.
6. Negative TN10: conservar ID y fuente de confirmación en `decision_application.platform_ack`; el resolver de reversa acepta confirmación por LIST tanto como ACK, validando decisión, perfil, grupo, término y estado. No basta buscar resultado LIKE ok. La reversa manual solicitada deshace el efecto confirmado; no afirma autoría de una keyword ni habilita borrado automático. Un negative ya presente antes del intento no debe convertirse en creación propia. Repetir reversa confirmada hace cero HTTP.
7. Límite explícito TN10: ausencia previa + presencia posterior no prueba autoría frente a un operador externo concurrente. El caso existente exige reversa manual del efecto reconciliado (200 y un delete), no certificación de creador. Se guarda procedencia `reconciliada`, se muestra esa diferencia al revertir y se conserva un caso vecino de objeto preexistente. Sin identidad inequívoca, no se permite borrar; no se debilita TN03 para hacer pasar TN10.
8. Reposición: archivada → intención durable → ACK durable con ID → readback → repuesta; rechazo inequívoco permite nuevo intento auditado, resultado desconocido solo lectura. Crash antes/después de POST, ACK o readback no repite creación. El claim por fila y token de intento impiden dos CLI simultáneas. ACK se guarda antes de leer; sin ACK no se adopta un ID por texto. La reversa archiva únicamente el ID propio repuesto, con ledger previo y readback; el ID original archivado no se inventa ni se reactiva.
9. Shadow vencida: no convertir práctica en live ni borrarla. Separar unicidad de efectos live de prácticas: índice único en-vuelo solo live; prácticas se deduplican bajo lock por efecto mientras su ventana esté vigente. Consulta de bloqueo excluye prácticas vencidas y no bloquea live con prácticas. Veto live conserva vencimiento y prioridad; shadow nunca libera. Es delta explícito del índice mixto de 0002, no permiso para que el motor descarte shadow como admin. Reversa de schema exige comprobar colisiones; si las hay, detener escritores y conservar schema compatible, sin borrar filas para forzar rollback.
10. Catálogo: scan completo solo con contenedor válido, IDs únicos y cierre verificable compatible con total; total incoherente, scroll perdido/repetido o fin prematuro = parcial. Solo scan completo autoriza bajas por ausencia. Cada item devuelve delta local después de salir del savepoint; sumar al resultado persistido en la TX externa y publicarlo tras commit. Si toda la TX revierte, no publicar sus contadores.
11. Previa: vacía → cargando(revisión,pedido inmutable) → lista → enviando → resultado/error. Cambiar selección invalida plan, huella y confirmación; respuesta tardía se descarta por revisión. Mientras envía, se congela la selección y se impide doble submit. Timeout de mutación no autoriza reenvío automático; consultar el estado/ID del dueño de retiro o impulso. El payload sale del mismo estado que pinta y confirma, también con aviso de presupuesto W-N.
12. Rollback: volver código solo si comprende los registros nuevos; expandir schema antes de usarlo. No eliminar intentos, reservas o historia para degradar schema. Reversas de efectos tienen ledger propio y guardas de cambio manual; deshacer despliegue no equivale a revertir dinero. Cada bloque conserva versión/replay y compensa solo recursos propios.

## Matriz única de cierre TN
`A(grupo, filtro)` es el comando de aceptación definido más abajo. Una fila, un owner; 05 solo consume evidencias de 03/04.
| ID | Owner de cierre | Fallo observable → aceptación |
| --- | --- | --- |
| 02 | 04 B-C ampliado | Contenedor ausente/truncado permite repost → A(writes, test_02), cero POST y sin cierre terminal falso |
| 03 | 05 B | Reversa cosecha borra ajena → A(writes, test_03), nunca borrar ID ajeno ni transferir autoría |
| 04 | 04 W-P nuevo | Retiro/impulso envían selección vieja → A(ui, test_04), ambas variantes y respuestas fuera de orden |
| 05 | 03 D-G existente | Clave corrupta reaparece/subtotal → A(ingest, test_05), cero publicación en todos los órdenes |
| 06 | 03 D-M nuevo | Scan parcial desactiva ausencias → A(ingest, test_06), miembros conservados |
| 07 | 03 P-G nuevo | Cotización pierde fecha/detalle → A(pricing, test_07), fresca y persistida completas |
| 10 | 05 B | Negative conciliado no revierte → A(writes, test_10), 200 y un delete del ID confirmado |
| 11 | 05 C | Reponer duplica POST → A(writes, test_11), exactamente uno tras dos ejecuciones |
| 12 | 05 A | Base queda 29,29,29 → A(optimizer, test_12), 29,28.5,28 y mismo valor en hojas |
| 13 | 05 A | Reobservación aborta lote → A(optimizer, test_13), lote/replay válidos y sello ≤ decided_at |
| 14 | 03 D-M nuevo | Savepoint revertido cuenta éxito → A(ingest, test_14), conteo igual a filas y sin éxito ficticio |
| 15 | 04 W-E ampliado | NULL gasto produce ACoS 5% → A(ui, test_15), ratio desconocido, tampoco inventar 10% |
| 16 | 04 W-D.2/D.4 existente | Dice sin ventas con ventas → A(ui, test_16), faltan datos |
| S1 | 03 D-N nuevo | Error de proveedor guarda secreto → A(ingest, test_S1), secreto ficticio ausente en persistencia |
03 D-M debe migrar también consumidor reputación del scan; P-G añade `estimada_en` al SELECT cache y preserva Fees.detalle sin recalcular fecha actual; D-N aplica `scrub` en el límite de Libro antes de cualquier almacenamiento de fallos. 04 B-C reemplaza «lectura de ítems no cambia»: validar contenedor, cada elemento y paginación con validadores existentes; migrar todos los callers de creación/conciliación/reversa, sin cuarto lector. W-P depende de W-M/N al integrar `productos.js`; W-E mantiene madurez y añade cobertura por métrica. W-O existente (Salud) se conserva.

## Nuevo ARREGLOS05: bloques ejecutables después de TEMPORADA01
Cada bloque incluye revisión del vecino que comparte causa, red/green cuando haya regresión y pre-commit; estos comandos son instrucciones futuras, no ejecuciones de hoy. `L(filtro)` selecciona pruebas futuras `arreglos05_` descritas aquí: BASE deberá crearlas/promoverlas; cero recogidas NO es aceptación.
| Bloque / depende | Trabajo y legado cubierto | Comando y resultado requerido |
| --- | --- | --- |
| BASE / TEMP terminado | Rebase con contratos B/E/F/A/G reales; integrar propietarios 03/04 por contenido; registrar SHA, evidencias y delta de schema. Fijar disponibilidad del runner y fixtures con 83+ migraciones reales | `git diff --check`; A de cada pendiente antes de corregir: rojos de comportamiento, sin errores de fixture; controles cerrados verdes |
| A / BASE | TN12/13; ciclo1/2/3: aislamiento, recuperación y lock. Ciclo4 es control de TEMP B.2, no nueva implementación. Ancla base separada del overlay y sello común a toda decisión | A(optimizer, 'test_12 or test_13'); L(ciclo): primer bid 5xx no mata segundo ni cortes; owner perdido sí detiene; TTL vencido recupera; reinicio tras TX3 no duplica decisiones |
| B / A,04 B-C | TN03/10, legado apply7 y reversa de apply1; usar bid realmente leído por TEMP E, no old_value obsoleto; releer antes de revertir y respetar mano posterior | A(writes, 'test_03 or test_10'); L(reversa): bid vuelve al anterior observado, cambio manual posterior se conserva; segunda reversa cero HTTP |
| C / BASE,04 B-A/B-C | TN11/apply11; esquema reposición, claim y reversa; tabla vacía sin backfill pero sin DROP de historia | A(writes, test_11); L(reposicion): dos procesos/crashes en cuatro fronteras, un POST; ACK durable visible aun con readback fallido |
| D / B,C,04 B-A/B-B | Apply2: migrar fábrica y consumidores API/vigía, archiva_inertes, reactiva_campanas y sondas a AdsWriteClient; conservar ledger de cada dueño y parser 207 | L(escritores); `rg -n -e '_client\.request' -e 'def _post' app tools` revisado, no grep=cero ciego: cada mutación tiene ruta protegida, intención pre-HTTP y reversa; denegados método/perfil ajenos |
| E / A | Apply5/6: shadow vencida y cupo durable 150 por origen. Migración/reversa con casos de índices y triggers UTC; no cambia caps de cortes | L('shadow or cupo'): prácticas vencidas no bloquean; live/veto sí; 151 ordinarias → 150 envíos; ambiguo y retoma no abren slot 151; entrada/regreso no consumen |
| F / BASE,TEMP A | Web9: cuota ilegible rechazada antes de submit y null explícito rechazado en servidor. Web1 Settings es control resuelto; Goals W-I no se duplica. Preservar ausencia=no tocar y null de campos que sí permiten borrar | L(settings): `1,000`, booleano, decimal inválido y null de cap no guardan nada; válido guarda; versión obsoleta 409; bandas 01X y campos TEMP siguen válidos |
| G / A,E,F | Retirar solo selectores vivos obsoletos confirmados: ramas política apagada, meta producto shadow y economía auto03; auditar selector sin lectores. Conservar interruptores globales, pausa producto shadow y contrato de configs históricas | L(modos); replay legado/actual idéntico; comparación decisiones antes/después sobre entradas congeladas; Settings deja de ofrecer únicamente modos retirados, precios efectivo sigue shadow |
| H / BASE,03 P-G | Conciliación explícita de fee/impuesto descrita abajo; sin tasa nueva ni activar precios | L(fees) con fixtures de fuente adjudicados: total/desglose/moneda/fecha concilian; incertidumbre mantiene `impuesto_fee_pendiente`, no emite precio ejecutable |
| Z / A-H,03/04 cerrados | Revisión de Orbit integrado, no suma de ramas; cada ID y legado con evidencia de su owner | Runner 31 casos, controles 01/08/09/D2, UI afectada+mapa, CI y secretos verdes del mismo SHA; hallazgo nuevo solo con reproducción e impacto |
El esquema propuesto extiende `keyword_archivo_manual` con intención/token/estado de reposición y `apply_attempt` con ciclo origen y slot de reserva, sin tabla universal ni grants nuevos. Un slot 1..150 queda en el primer intento ordinario; únicos parciales por (origen, slot) y por decisión impiden doble reserva. Reintentos de la misma decisión consultan esa reserva durable; nunca crean otra ni la liberan al reconciliar. ACK/intención de cada intento de reposición quedan como eventos preservados en el registro existente, sin sobrescribir los anteriores. IDs únicos de reposición por plataforma; estado esperado y token en cada actualización. Número de migración se toma al rebasar; probar ida/reversa en PG desechable, integración con selector de migraciones y roles existentes. Invariantes dependientes de datos requieren evidencia SELECT suficiente: la foto disponible no prueba todos; el bloque registra lo faltante antes de aplicarlos, sin inventarlo hoy.

## Contrato posterior a TEMPORADA01
- BASE coteja `OrigenCambio`, `HistoriaBid`, `recuperacion.cadena_bid`, `temporada_io.{lee_estado,registra_ciclo,estampa,abre_efectos,registra_fines}`, `Aplicador.aplica_bids`, `_confirma_resumen`, `registra_sin_aplicar`, `aplica_reactivaciones` y `proxima_config`. Diferencia de firma → ajustar este boceto al contrato real y registrar delta, nunca crear una API paralela.
- TN12 guarda/lee meta BASE convergente del último ciclo live completado, independiente de procedencia de medición; luego aplica overlay alta/baja. Nunca usar el 25% estacional como próxima base. Añadir versión de snapshot/base si hace falta; un histórico sin base explícita no se reconstruye restando un overlay supuesto: usar el último ancla inequívoca y declarar ausencia si no existe. Replay consume su versión congelada, no settings actuales. Fixture postTEMP combina ledger rancio y entrada/salida de temporada.
- TN13 mueve el sello común fuera de `if corte`; conserva observed_at real en inputs y el contrato de snapshot existente, sin rejuvenecer métricas ni cambiar window_end/madurez. No alterar trigger para aceptar filas inválidas. Congelar inputs y demostrar replay del lote mixto.
- Confirmación de apply cierra efecto TEMP en la misma TX. Reanudación no crea segundo episodio/efecto ni repite entrada. TEMP E aporta lectura pre-PUT; 05 B completa reversa. TEMP B.2 aporta conciliación predecisión; 05 A la verifica. Reactivaciones F no entran al cupo de bids.
- Fixture de cupo: 149 reservas ordinarias + una ambigua = 150; recuperar con ciclo ejecutor nuevo envía cero ordinarias adicionales. Confirmar la ambigua tampoco libera slot. Entrada/regreso aún elegibles no cambian contador; una subida `Recupera` sí cuenta. Prioridad TEMP se mantiene entre elegibles, no evita la reserva durable.

## Decisión condicional de tasa/fee: bloque H
No es asesoría fiscal ni una nueva política. La decisión del dueño de 2026-11-13 sobre retenciones permanece. `estimacion_venta.py:479` ya abstiene con TaxAmount no cero, incluso anidado; conservarlo hasta conciliar. No multiplicar comisión por 1.16 ni usar tasa cero/16% como fallback.
H prepara comparación por plataforma, moneda, oferta/precio, fecha y versión: cotización externa íntegra (total y árbol, incluido tax), cargo real de liquidación/factura y componentes del escenario, sin confundir comisión duplicada que ya pertenece a 02 D ni P-F/P-G. La sospecha histórica por sí sola no fija base ni tasa. Hoy falta el detalle externo de esta conciliación; el SELECT de estado no lo reemplaza.
Decisión: (a) impuesto ya incluido y total conciliado → no sumarlo otra vez; (b) impuesto separado y coste efectivo documentado conforme al contrato vigente → incorporar el importe explícito una sola vez mediante versión nueva; derivar tasa solo si fuente/base la sustentan; (c) evidencia insuficiente o contradictoria → mantener desconocido/abstención, registrar causa y tarea de evidencia abierta. Ninguna rama levanta shadow. H cierra documentalmente solo con conclusión sustentada; «no sabemos» no se presenta como sospecha resuelta.
Verificación futura: mismos inputs contra fuente externa y cálculo versionado, sin reescribir escenarios anteriores; control TaxAmount anidado, total ya inclusivo y tasa ausente. Rollback selecciona versión anterior compatible sin borrar resultados ni afirmar que su margen es correcto.

## Comandos de aceptación y cierre
En entorno desechable Linux/Python3.12/PG16 con HTTP simulado y Chromium, siguiendo `reproducir.txt`; nunca DSN real. Configurar `E` como directorio copiado del review y `R` como checkout final. El runner suministrado hace esto para corrida completa; para selección:
```bash
export ORBIT_REVIEW_REPO="$R" ORBIT_REVIEW_ROOT="$R"
export ORBIT_REVIEW_DSN="$ORBIT_TEST_DSN" ORBIT_REVIEW_PG_DSN="$ORBIT_TEST_DSN"
export ORBIT_OPTIMIZER_TEST_DSN="$ORBIT_TEST_DSN" ORBIT_UI_DSN="$ORBIT_TEST_DSN"
export PYTHONPATH="$R:$R/tests:$E" PYTHONDONTWRITEBYTECODE=1
A() { python -m pytest -q -p no:cacheprovider "$E/test_accept_$1.py" -k "$2"; }
L() { python -m pytest -q "$R/tests" -k "arreglos05 and ($1)"; }
bash "$E/run-acceptance.sh" "$R"
pre-commit run --all-files
```
Promover reproducciones útiles de `pruebas/ciclo/test_revision_ciclo.py`, `apply/test_rev_apply.py` y `web/test_rev_settings.py`; revalidar fixture postTEMP y distinguir controles ya corregidos. No añadir pruebas de texto/mutantes ni duplicar regresiones existentes. Para C/D/E/B revisar crash, readback, reintento y reversa; para P-G fresca/cache; para UI los dos flujos reales con fetch simulado. Red/green contra padre real del arreglo, conservando hashes y error observable.
Leer skill/fichas `verify-orbit` antes de ejecutar UI. `V=.cursor/skills/verify-orbit/helpers/orbit-verify`; ejecutar `$V launch`, `$V doctor`, `$V drive-productos`, `$V drive-settings`, `$V drive-resumen` y resto de diez pantallas, finalmente `$V cleanup`. Baseline no envía retiros: los casos externos de navegador cubren selección, fallo y respuesta tardía; ampliar ese fixture para envío pendiente. Actualizar fichas afectadas; no inventar `drive-ruido` (no existe).
El gate final conserva compilación y suite completa con PG de `.github/workflows/quality.yml`, sin skips ni `--no-verify`. Registrar SHA/enlace CI, secretos y aceptación en el mismo commit; reutilizar los 5996 verdes previos solo como evidencia de base, nunca como pase de cambios futuros. 01/08/09/D2 permanecen controles; D1 es nota no bloqueante. No activar ni certificar producción mediante este cierre.

## Riesgos y siguiente paso
¿Qué evidencia externa adjudicará H y qué versiones de cuota/episodio trae TEMP integrado? Son entradas a resolver en BASE/H, no permisos solicitados hoy. ¿Existen colisiones que impidan revertir el índice shadow después de usarlo? Se debe responder con conteo antes de rollback, conservando filas. La foto actual vacía de reposición no vale como garantía perpetua.
Primer paso futuro: incorporar esta matriz a los owners 03/04 y preparar BASE de 05 contra TEMP integrado; cerrar 03/04 independientes mientras TEMP avanza. No crear ni implementar hoy esos planes.

Comprobación del paquete: 312 líneas totales; sintaxis del boceto válida, diez funciones con `NotImplementedError`; Gitleaks sin hallazgos. No se ejecutaron pruebas de producto.
