# Candidato A: cerrar en los propietarios existentes

Fecha: 2026-11-15 Vancouver. Base leida: `ff03ed61f7a847ab190b1695c2efe8d785baa364`.
Alcance cumplido hoy: grounding y boceto documental; implementacion y pruebas de producto quedan para los bloques descritos. Sin arena, delegacion, red, produccion ni cambios al repo.
Fuentes relativas al checkout `/Users/dn/dev/goncloud-Orbit-arreglos-05-plan`; evidencia TN en `/private/tmp/orbit-review-current-evidence`.

## Problema y evidencia

Hay 14 hallazgos TN abiertos; ejecutar los planes actuales no los cierra todos. El objetivo es cerrar su comportamiento observable y el legado asignado a ARREGLOS05, conservando duenos, decisiones economicas y replay.
Referencia: `informe.txt`, `hallazgos.json`, `matriz.tsv`, `criterio-cierre.txt`, `test_accept_*.py` y `run-acceptance.sh` del review vigente. Sus 31 casos (17 rojos, 14 controles verdes) y CI de 5996 casos son evidencia heredada, no ejecutada hoy.
Legado: `docs/evidencia/arreglos-2026-11-12/HALLAZGOS.md`, reportes `ciclo.md`, `apply.md`, `web.md`, `estimacion.md` y sus reproductores. Mandan `docs/CONTEXTO.md`, `docs/APPLY.md` y los contratos de planes 01-04/TEMPORADA01 sobre el diseno historico de Ads.
SELECT recibido: `docs/evidencia/arreglos-05/base/{consulta-estado.sql,estado-prod.txt,esquema-prod.txt}`, tomado 2026-11-16 01:43 UTC: 191 ciclos; 159 live/done, 30 shadow/done, 2 off/skipped; 31 shadow/pending_veto; 9 goals live; reposicion vacia y 0 IDs duplicados; 0 negatives ok:reconciliado.
Ambas politicas bid son niveles_v3, meta producto live, pausa producto shadow, economia producto_directo_v1; caps actuales 1000000. Precios global shadow domina universos live: el modo efectivo sigue shadow.
La foto permite preparar migracion de reposicion sin backfill; no prueba que cualquier ciclo done termino todos sus efectos ni autoriza borrar historia. Los invariantes adicionales necesitan su SELECT especifico antes de aplicarse, fuera del trabajo de hoy.
Comprobacion del legado actual: `config_write.py:390,454-457` ya exige banda manual via el lector canonico (ARREGLOS01X). Web1-Ajustes no se reabre; Goals sigue 04 W-I. 01/08/09/D2 de TN son controles resueltos; D1 es nota, no bloqueo.

## Uso primero: tres llamadores

Firmas propuestas, no APIs existentes ni codigo listo para instalar. `sketch.py` contiene sus tipos; importar producto solo se propone para la implementacion futura.
```python
# app/cli.py: una invocacion decide o retoma el ciclo que corresponde.
from app.cycle import continuar_ciclo
resultado = continuar_ciclo(plataforma="amazon_us", ahora=ahora_utc)
# resultado.cycle_id conserva el origen; pendientes distingue recuperacion de cierre.

# tools/archiva_inertes.py: conservar --reponer; una fila determina identidad y permisos.
resultado = reponer_keyword(archivo_id=fila_id)
# Confirmada / PendienteLectura / Rechazada; repetir no implica otro POST.

# MercadoMeli.cotizar y cotizacion_guardada: mismo constructor para fresca/cache.
cotizacion = _cotizacion_de_arbol_meli(
    precio=precio, total=total, detalles=detalles, estimada_en=fecha_de_la_fuente,
)
# La fecha nunca es ahora() para rejuvenecer una cotizacion persistida.
```

## Forma y mapa modular

Elegida A: contratos profundos dentro de propietarios actuales; extender sus registros locales, sin coordinador universal ni cola de bids. Tres fronteras: lector completo, ejecutor con evidencia durable y resultado confirmado por transaccion.
Por boundary-discipline, JSON/HTTP se validan al entrar; por model-the-domain, lista incompleta y ausencia comprobada son estados distintos. Dinero usa `Importe`/`Fees` existentes, Decimal y moneda; el boceto no crea otro modelo monetario.

| Modulo propietario | Responsabilidad y complejidad que oculta |
| --- | --- |
| `app/cycle.py` | Claim, seleccion de ciclo/reanudacion, TX2/TX3; avanzar target base y sellar observacion sin cambiar la politica pura. |
| `app/apply.py` | Intencion, limites, intento, ACK/readback y reversa bid/negative; proyeccion `decision_application` derivada de evidencia del intento. |
| `app/apply_harvest.py` + reconciliacion | Un lector estricto con filtros; propiedad por ID estable; fases y reversa harvest. Reconciliacion usa el mismo contrato, no otro escritor. |
| `app/apply_cola.py` | Veto, expiracion logica de shadow y cortes; no recibe bids. TEMPORADA conserva sus reactivaciones. |
| `app/ads/write.py` | Unico transporte mutador, perfil fijo, operaciones tipadas y allowlist exacta; nunca recibe una ruta arbitraria del caller. |
| `tools/archiva_inertes.py` | Reposicion durable local por fila; CLI conserva su uso. Fabrica conserva su ledger `fabrica_lote_paso`. |
| `app/reputacion_clientes.py` / `app/meli/catalogo.py` | Cobertura del scan en origen / delta de escrituras confirmado tras savepoint. |
| `app/meli/precio_mercado.py` | Adaptacion fresca/cache con fecha, detalle y moneda completos hacia `app/precio/puerto.py`. |
| `app/static/js/productos.js` | Estado unico por flujo: solicitud, revision y previa; detalle, confirmacion y payload derivados. |
| `app/pantalla_ruido.py` / `app/ui_metricas.py` | Cobertura del ratio / frase derivada de hechos; sin framework nuevo de metricas. |
| `app/jev_libro.py` / `app/jev_juicios.py` | Libro sanea antes de persistir; transporte no entrega errores crudos a logs ni consumidores. |
| `app/config_write.py` | Validacion canonica y append-only con version optimista; no registro paralelo de 19 claves. |

Indices de acceso: intentos por (decision_id, seq), cuota por join decision.cycle_id de origen y tipo normal; reposicion por (archivo_id, secuencia) con unico parcial de intento abierto; ultima base por plataforma y ciclo comprometido; scan por conjunto de IDs unicos. Verificar planes SQL con volumen local antes de agregar indices redundantes.
La superficie nueva se limita a operaciones completas: continuar un ciclo, reponer una fila, construir una cotizacion. El caller no encadena claim/intencion/HTTP/sello. Se conservan los puertos existentes; los tipos del boceto se distribuyen en esos modulos, no en un paquete transversal.

## Ownership unico y orden

Acepto la particion propuesta por el lead: 03/04 adelantan datos/UI durante TEMPORADA; 05 empieza su codigo despues de TEMPORADA. W-O ya es Salud: la previa nueva es W-P.

| Hallazgo | Unico bloque responsable | Fallo observable que debe desaparecer |
| --- | --- | --- |
| TN02 | 04 B-C ampliado | Contenedor ausente o paginacion incompleta permite repetir POST. |
| TN03 | 05 B | ID del ACK sustituido por keyword ajena que luego se archiva. |
| TN04 | 04 W-P nuevo | Retiro/impulso envia A tras cambiar a B y fallar su previa. |
| TN05 | 03 D-G existente | Subtotal corrupto se publica completo o recrea clave invalidada. |
| TN06 | 03 D-M nuevo | Scan incompleto desactiva publicaciones ausentes. |
| TN07 | 03 P-G nuevo | Cotizacion fresca/cache pierde fecha y detalle. |
| TN10 | 05 B | Negative conciliado no tiene reversa utilizable. |
| TN11 | 05 C | Reponer repite POST tras ACK y readback ambiguo. |
| TN12 | 05 F | Meta base repite 29,29,29 en vez de converger 29,28.5,28. |
| TN13 | 05 G | Reobservacion de harvest maduro revierte todo TX3. |
| TN14 | 03 D-M nuevo | Contadores incluyen escrituras que el savepoint revirtio. |
| TN15 | 04 W-E ampliado | Ruido publica 5% con gasto parcialmente desconocido. |
| TN16 | 04 W-D.2/.4 existentes | Resumen afirma sin ventas cuando solo falta cobertura. |
| TNS1 | 03 D-N nuevo | Secreto ficticio en respuesta invalida llega al libro sin scrub. |

No duplicar D-G ni W-D. En 04 B-C sustituir expresamente «la lectura de items no cambia»: validar objeto, contenedor presente/lista, cada item e identidad, tokens y fin completo; reutilizar `_valida_pagina_list` y el lector estricto existente, migrar los siete consumidores y retirar `_lista_todos` permisivo. Lectura desconocida mantiene pendiente, cero POST/delete y cero sello terminal por ausencia supuesta.
03 D-M: IDs unicos, total valido y cobertura demostrable del scan; token ausente/fin prematuro/duplicados no certifican ausencia. Reputacion consume el mismo resultado. Cada item devuelve un delta inmutable, acumulado solo al salir bien de su savepoint; tambien mapas locales se fusionan despues. Si revierte la TX exterior, no publicar contadores exitosos.
03 P-G: fresca y guardada conservan `estimada_en`, `Fees.detalle`, precio/moneda y desglose conciliado; SQL de cache incluye fecha original. Falta de fecha es error explicito, nunca success. La politica de impuestos no se modifica aqui.
03 D-N: saneamiento de codigo/detalle en el limite durable `Libro._resultado` y en salidas del transporte; probar model/choice con secreto ficticio. No tocar la transaccion ya corregida de TN09.
04 W-P: cambiar seleccion o pedir previa incrementa revision e invalida confirmacion; respuesta tardia de otra revision se ignora. `enviando` congela solicitud y bloquea doble envio/cambio de seleccion hasta resolver; timeout no rehabilita una previa para reenviar ciegamente. Retiro y todos los accesos a impulso comparten esta regla, no un estado global entre ambos flujos.
04 W-E: ratio exige gasto y venta completos sobre la misma poblacion/ventana/moneda. Se puede mostrar subtotal etiquetado, pero ACoS queda None con causa; no sustituir 5% por un 10% inventado. W-D conserva su propia frase y recibe esa semantica.
Integracion: 03 D-M/D-N/P-G despues de sus vecinos D-K/D-L, D-C/D-J y P-B/P-F respectivamente; 04 W-P despues de W-M/W-N; W-E junto a su cambio de madurez. Integracion serial por contenido en cli/ui/notifica; nada de branches paralelas editando la misma frontera.

## Estados, ownership, retry y rollback

Orden del nuevo 05: BASE -> A -> B -> C -> D -> E -> F -> G -> H -> I -> J -> K -> Z; se integra serialmente por archivos compartidos, conservando ademas las dependencias externas de la tabla.
Ciclo: claim vigente por plataforma es la unica autoridad; fuera la heuristica de 3h. Tras TX1, reconciliar bajo claim antes de TX2 (dueno TEMPORADA B.2); TX3 guarda decisiones, target base y marca durable de decision comprometida juntos. Proponer `decision_committed_at` y `apply_finished_at` en `optimizer_cycle`, sin copiar estados de cada decision a otro journal.
Un corte tras TX3 retoma ese `cycle_id`; antes de TX3 no hay lote comprometido y se puede recalcular. Retoma del mismo dia no ejecuta nuevamente TX2. Al cambiar de dia se concilian ambiguos antiguos y se descartan destinos ordinarios no intentados que ya no sean vigentes; el ciclo nuevo decide con evidencia fresca. No aplicar indefinidamente decisiones viejas.
Pendientes se derivan de `decision`, `apply_attempt`, `decision_application` y cola, no de `status=degraded`. Datos deficientes se distinguen de apply incompleto; si la ingesta se recupera sin lote comprometido, el cron vuelve a decidir. `finished_at` no debe fingir que apply termino en TX3.
Cada bid/fase registra fallo propio y permite otros objetos/cola; perder lease, permisos globales o identidad de perfil aborta el resto. Revalidar claim antes de reservar intento y de cada HTTP; owner unico por adquisicion, nunca reutilizado. La renovacion y sellos verifican ese owner en DB.
No hay fencing remoto/CAS en Amazon: una peticion ya enviada puede terminar tras vencer el lease. El sucesor ve su intento durable y solo concilia; nunca reenvia ese destino ambiguo. Fallo por objeto no significa retry de la misma mutacion.
Bids: reconciliacion sigue solo lectura (TN01). Reversa lee bid vivo y ultima intervencion: si cambio el dueno, no lo pisa; usa el valor previo observado y congelado por TEMPORADA E, no `decision.old_value` obsoleto. Un efecto confirmado se revierte una vez; caida tras enviar reversa exige readback, no segundo PUT automatico.
Creacion: reservado -> enviado/ambiguo -> ACK durable -> confirmado por ID -> reversa pendiente -> revertido. Caida entre intencion y POST queda ambigua tambien; preferimos retencion a duplicar. Un rechazo definitivo prueba no-creacion y permite nuevo intento segun el tope existente.
TN03: ID del ACK es inmutable; mismo texto con otro ID es adoptado/ajeno, nunca propio. El readback valida ID+identidad+estado. Reversa completa, automatica y hermanas consumen la misma evidencia; flag booleano aislado no autoriza borrar.
TN10: antes del POST persistir la ausencia completa y la identidad esperada junto a la intencion, para que el escenario de timeout del review produzca evidencia recuperable desde cero. Conservar en el intento la evidencia de conciliacion y su ID canonico, sin fabricar un ACK remoto. Reconstruccion exige ausencia previa completa guardada, intento durable y un unico resultado compatible; el resumen se deriva en la misma TX. Reversa obtiene ese resultado, revalida el ID vivo y sella su intento propio; tambien acepta ACK normal. Identidad aislada no reconstruye autoria; legado ambiguo queda pendiente. La foto tiene 0 reconciliados, no se necesita inventar backfill.
Limite de TN10: ausencia+intento+aparicion unica es evidencia causal del protocolo, no prueba absoluta frente a un creador externo concurrente invisible. Con multiplicidad, sustitucion o evidencia contradictoria se retiene la reversa; revisar este limite con el caso TN03, sin prometer exclusividad de la cuenta.
TN11: agregar registro local `keyword_reposicion_intento` ligado a archivo_id, secuencia, owner/lease, intencion, ACK/ID y readback; unico intento abierto por fila. Confirmar/sellar archivo en misma TX; ACK se guarda antes de readback. La tabla madre vacia permite migracion aditiva sin reinterpretar estados antiguos. Dos CLI simultaneos producen un solo POST; timeout permanece pendiente y relee el ID conocido.
Shadow vencida: excluirla de claves bloqueadas sin mutarla. El indice actual de efecto en vuelo incluye shadow: migrarlo a unicidad de efectos live y conservar unicidad por decision para propuestas shadow. El encolado sigue bajo claim por plataforma y consulta propuestas shadow aun vigentes para evitar repeticion; historial vencido queda consultable como practica vencida, nunca promocionado a live. No conceder al motor el descarte shadow que 0002 reserva a admin. Conservar triggers UTC de veto/48h y la unicidad live; probar encolado real, no solo el SELECT de claves. La reversa del indice debe comprobar colisiones antes de restaurarlo.
Rollback: primero detener nuevas mutaciones con controles existentes; conciliar intentos abiertos. Reversa de negocio usa evidencia, no restore ciego. Migraciones reversibles antes de activar; su down debe negarse si borraria intentos/efectos nuevos, hasta exportacion y resolucion. Mantener lectores compatibles durante despliegue y no volver a binario que ignore intentos pendientes.

## Contrato post-TEMPORADA01

BASE fija el SHA integrado y compara `plans/temporada-01.md` B.2/E.1/F.2 y `docs/evidencia/temporada-01/DISENO.md` con las firmas reales. Diferencia -> documento de ajuste del bloque, no redisenar la politica por intuicion.
`temporada_io` conserva ownership de episodio/efecto/fin/exclusion; `_confirma_resumen` y `registra_sin_aplicar` cierran efectos atomicos. Retomar no duplica entrada/regreso, fin ni exclusion; practica no escribe esos efectos. Cola conserva `aplica_reactivaciones`, pausa propia y prioridad aprobada.
TN12 separa `TargetBase` del target efectivo: ancla de convergencia es el ultimo base live comprometido, aunque la medicion sea ledger_rancio o el apply parcial; no se toma del overlay estacional de 25%. Guardar base y overlay por separado en el snapshot versionado, efectivo derivado para cada hoja. No anclar al origen textual `procedencia` ni a un target de practica.
Compatibilidad: snapshots viejos se leen con su semantica original, sin inferir una base a partir de un efectivo estacional. Nueva version y ausencia explicita si falta base; migracion/reversa conserva JSON historico. Replay usa version congelada; cero cambios de umbrales, prior D1 o politicas aprobadas.
150 ordinarias por ciclo de origen: reservar cupo e intento atomicamente bajo claim, derivar consumo de intentos ordinarios durables del ciclo original; cuenta ambiguos y no reinicia por retoma/ejecutor/fecha. El intento 151 no sale; 150 decisiones elegibles si alcanzan las otras cuotas, sin margen artificial inferior. Mantener cuotas existentes por dia/kind/plataforma como limites adicionales.
Exencion solo entrada/regreso identificados por el marcador congelado de TEMPORADA; no eximir todo `origen=temporada` ni toda recuperacion ordinaria por prioridad. Un fallo tras reservar cuenta conservadoramente aunque no sepamos si salio; un reintento conciliador no reserva ni envia otra vez.

## Bloques del nuevo 05

Los codigos siguientes son propuesta documental. Cada owner significa un bloque del plan, no un nuevo servicio. Aceptaciones nuevas nombradas `arreglos05_*` son por escribir; exigir coleccion no vacia y rojo previo, nunca aceptar un `-k` sin casos.

| Bloque | Alcance, dependencias y acceptance observable |
| --- | --- |
| BASE | Tras TEMPORADA y 02; fijar SHA, contratos y SELECT recibidos. Revalidar fixtures y controles TEMP B/E/F, 01/08/09/D2; si ya pasa un legado, documentarlo y no duplicar implementacion. |
| A: ciclo | Legado ciclo1/2/3; ciclo4 se verifica en TEMP B.2. Reanudacion durable, fases aisladas, claim real. `L ciclo/test_revision_ciclo.py -k 'not piso_aprendido'`: segundo bid y cola progresan tras 5xx, horario retoma, lease vencido recupera; dos workers/crash pre y post-TX3 no duplican efectos. |
| B: propiedad/reversas | TN03/TN10 y legado apply1-reversa/apply7; depende 04 B-C y TEMP E/F. `T writes -k 'test_03 or test_10 or test_01'`; control adicional cambio manual posterior, sustitucion de ID y caida durante reversa. Cero DELETE ajeno; una reversa conciliada confirmada. |
| C: reposicion | TN11/legado apply11, despues B y 04 B-A. `T writes -k test_11`; `L apply/test_sub_b_inertes_reponer_sin_intencion.py`. Una intencion visible desde otra conexion antes de POST; ACK durable antes de readback; dos CLI/un reinicio producen un solo objeto PAUSED. |
| D: frontera Ads | Legado apply2. Migrar fabrica, vigia, retiro, reactiva y sondas al cliente protegido; conservar ledgers por dominio. Operaciones ya usadas se expresan con metodos exactos, perfil sellado, money y ACK por item; no ampliar permisos/capacidades del negocio. `N -k arreglos05_d_`: ruta/perfil no permitidos bloqueados, cada caller registra intencion antes de HTTP. Migrar callers y borrar HTTP crudo en el mismo bloque; conservar la herramienta de reposicion. |
| E: shadow | Legado apply5; despues TEMP F y D. `L apply/test_rev_apply.py -k fila_shadow_vencida`; `N -k arreglos05_e_`: shadow vencida no bloquea nueva decision, historial intacto, veto live aun exige 48h, encolado idempotente sin permisos nuevos ni choque con el indice viejo. |
| F: target | TN12, tras TEMP I/B. `T optimizer -k test_12`; `N -k arreglos05_f_`: base 29/28.5/28 tambien bajo overlay alta 25%, salida de temporada retoma base; replay historico igual. |
| G: sello | TN13. Aplicar `_sello_bitemporal` existente tambien cuando `corte=None`; conservar observed_at original en inputs y ventana madura, sin reescribir metricas. `T optimizer -k 'test_13 or test_control'`: TX3 no aborta, replay igual y ninguna decision usa ventana inmadura. |
| H: 150 bids | Legado apply6, despues A y TEMP E. `N -k arreglos05_h_`: 151 ordinarias -> 150 intentos maximos; ambiguos consumen; retoma no renueva cupo; entrada/regreso exentos y prioridad TEMP preservada. No bajar por accidente cuotas de cortes. |
| I: Ajustes | Legado web9 pendiente; web1-manual ya cerrado por 01X. `L web/test_rev_settings.py -k 'target_manual or endpoint_settings or cap_ilegible'`; `N -k arreglos05_i_` con JS real: `1,000` muestra error y cero POST, nunca NaN->null->Guardado. API distingue ausencia de invalido, rechaza bool/NaN/infinito/fraccion en cuotas; conserva version optimista. TEMP A posee sus campos; Goals 04 W-I. |
| J: modos | Retirar solo caminos runtime confirmados sin uso y redundantes tras TEMP; no historia, decoder/replay, modo global off/shadow/live ni pausa producto shadow vigente. `N -k arreglos05_j_` para equivalencia de decision/replay y apagador; inventario actual vs usos/lectores antes de cada eliminacion. Conservar interruptores requeridos por TEMP y despliegue. |
| K: conciliacion fees | Legado sospecha IVA, depende 02 D y 03 P-G. Contrato condicional abajo; sin cambio fiscal ni activar precios. `N -k arreglos05_k_`: fixtures externos conciliados, ausencia/impuesto desconocido se abstiene; preserva global shadow. |
| Z: cierre | Todos los bloques 02/03/04/05/TEMP y matriz sin huerfanos. Ejecutar aceptacion global y Quality sobre mismo SHA integrado; flujos vecinos de errores/retry/reversa/cache/replay y diez pantallas. Ningun despliegue ni PR pertenece a esta entrega documental. |

## Conciliacion fees: decision condicional explicita

Owner unico 05 K. `estimacion_venta._tiene_tax_amount` detecta importe no cero tambien en hijos y la estimacion se abstiene con `impuesto_fee_pendiente`; no asumir que hoy incorpora ese impuesto ni sumar 16% a todo.
Preparar comparacion por fuente, mercado, SKU, fecha, precio/moneda y componente: cotizacion original con arbol, liquidacion/cargo externo clasificado y escenario. Separar comision, envio/FBA, impuestos incluidos y desfases; medir diferencia en importe, no adoptar una tasa de un ejemplo.
Si la fuente demuestra que el total ya incluye impuesto, conservarlo sin duplicarlo. Si demuestra componente excluido y el contrato aprobado lo trata como costo, incorporarlo una sola vez con evidencia versionada y reversa antes de uso. Si no demuestra base/inclusion o contradice el contrato, conservar abstencion y registrar diferencia pendiente, sin fabricar cero/tasa.
La decision del dueno de 2026-11-13 sobre retenciones permanece; no se asesora fiscalmente ni se cambia regimen. Resolver la discrepancia con evidencia de fuente es el cierre de K; una sospecha sin evidencia no se declara arreglo. Si la fuente basta para descartar la sospecha, cierre documentado sin codigo.
Hoy no se consulta ninguna cuenta; el plan deja la necesidad de evidencia explicita. Mientras no se resuelva, no proclamar cerrado TODO el legado; precios permanece efectivo shadow en todas las ramas.

## Comandos de acceptance para el ejecutor futuro

En entorno Linux/Python3.12/PG16 desechable del review, HTTP remoto simulado, Chromium/Node disponibles; `/repo` y `/evidence` como en `reproducir.txt`. No correrlos hoy. DSN exclusivo de prueba proporcionado por el entorno, nunca copiar credenciales reales.
```bash
export PYTHONPATH=/repo:/repo/tests:/evidence PYTHONDONTWRITEBYTECODE=1
export ORBIT_REVIEW_ROOT=/repo ORBIT_REVIEW_REPO=/repo
export ORBIT_REVIEW_DSN="$ORBIT_TEST_DSN" ORBIT_REVIEW_PG_DSN="$ORBIT_TEST_DSN"
export ORBIT_OPTIMIZER_TEST_DSN="$ORBIT_TEST_DSN" ORBIT_UI_DSN="$ORBIT_TEST_DSN"
T() { grupo="$1"; shift; python -B -m pytest -p no:cacheprovider -q "/evidence/test_accept_${grupo}.py" "$@"; }
L() { archivo="$1"; shift; python -B -m pytest -p no:cacheprovider -q "/repo/docs/evidencia/arreglos-2026-11-12/pruebas/$archivo" "$@"; }
N() { python -B -m pytest -p no:cacheprovider -q /repo/tests "$@"; }
T writes -k test_02                         # 04 B-C: missing y truncated
T ingest -k 'test_06 or test_14'            # 03 D-M
T pricing -k test_07                       # 03 P-G: fresca/cache
T ingest -k test_S1                        # 03 D-N
T ui -k test_04                            # 04 W-P: retiro/impulso
T ui -k test_15                            # 04 W-E: ratio desconocido
T ingest -k test_05                        # 03 D-G, no duplicar
T ui -k test_16                            # 04 W-D, no duplicar
bash /evidence/run-acceptance.sh /repo       # Z: 31 casos juntos, serial
```
Para cada bloque, guardar comando, SHA y fallo observable previo y posterior; revisar fixtures contra el contrato nuevo (ejemplo: TEMP E ahora evita aplicar bid obsoleto, no perpetuar el viejo test de reversa). Errores de entorno/coleccion/skips no son rojo de regresion.
Vecinos adicionales: tokens malformados y pagina con item corrupto; scan con duplicados y fin precoz; rollback exterior catalogo; previa tardia A/B y doble clic; captura invalida de cuotas mediante formulario real. Elegir pruebas por riesgo/evidencia, no por lista cerrada ni por texto de archivo.
UI: leer fichas afectadas de `.cursor/skills/verify-orbit/features/`, actualizarlas al implementar; usar helper `orbit-verify launch`, `doctor`, `drive-productos`, `drive-settings`, `drive-resumen`, `drive-salud` y `cleanup` en instancia propia. El baseline no envia retiro: complementar con Chromium y POST interceptado de TN04. Z recorre las diez pantallas.
Antes de integrar producto: `pre-commit run --all-files`, `python -m compileall -q app tools verify` y `PYTHONPATH=. python -m pytest -q -x` con PG16; Quality verde del SHA exacto y evidencia de secretos. Reutilizar evidencia valida; ampliar solo por causas compartidas. No confundir pase de este review con certificacion universal o de produccion.

## Decision de sintesis, alternativas y tradeoffs

Decision del candidato, no resultado de arena: elegir propietarios existentes y adoptar el reparto 03/04/05 del lead. No hubo otros runners ni sintesis multiproveedor.
Alternativa B estructural: coordinador/journal transversal con `submit/claim/reconcile/undo` para bids, fabrica, cortes y reposicion. Ocultaria mejor el protocolo de crash al caller, pero exigiria migrar cuatro identidades/ciclos de vida y delegar otra vez reglas de veto, presupuesto y propiedad a adaptadores; dos journals coexistirian durante la transicion. No elimina por si solo TN06/07/12/13/15/S1.
A acepta protocolos locales a cambio de conservar reversas y ownership ya auditados; elimina lectores permisivos, HTTP mutador alternativo, estado UI duplicado y heuristica de 3h. Profundidad: cada owner completa su operacion; el cliente Ads solo domina transporte, no simula dominar autorizacion de todos los negocios.
A acepta dos columnas de progreso y ledger local de reposicion a cambio de reanudacion verificable; no convertirlos en otra lista sincronizada de acciones. A acepta retener resultados ambiguos a cambio de no duplicar POST; ninguna arquitectura logra exactly-once remoto sin soporte del proveedor.
Screen de red flags: sin modulo por etapa load/validate/save, wrappers transparentes ni nuevo registro de claves; sin dos escritores del mismo efecto. Python permite importar privados: migrar imports externos y reforzar el candado arquitectonico existente para internals/HTTP, sin afirmar encapsulacion que el lenguaje no da.
Riesgos por resolver con evidencia: ¿las firmas integradas de TEMP conservan los sellos atomicos descritos? BASE lo comprueba. ¿hay invariantes adicionales de reposicion/claim no cubiertos por el SELECT recibido? Se miden antes de migrar. ¿la fuente permite conciliar inclusion de impuesto? K responde o queda explicitamente abierto; no pregunta de permiso nueva.
Siguiente paso de implementacion futura: integrar primero las ampliaciones concretas de 03/04 y preparar BASE de 05 contra el commit final de TEMPORADA; esta candidatura termina aqui en documentos.
