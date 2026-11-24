# ARREGLOS 05: ciclo, escrituras y cierre de la revisión completa

Estado: **diseñado, por implementar**. Fecha: 15-nov-2026. Base del diagnóstico:
`ff03ed61f7a847ab190b1695c2efe8d785baa364`. Este documento no acredita arreglos hechos.
Orden de integración: [ORDEN-IMPLEMENTACION.md](ORDEN-IMPLEMENTACION.md).
Dependencias externas: ARREGLOS 03/04 ampliados y TEMPORADA 01 terminados; 01/02 ya
están integrados. Al cerrar H de TEMPORADA, abrir BASE desde el master integrado. En el PR de BASE, actualizar `plans/manifest.json` a `active: arreglos-05`.

Hermes coordina este plan en el proyecto `goncloud-orbit` de Agent Orchestrator en `Gon-PC` con `opencode-v2` (`zai-coding-plan/glm-5.4`). GLM 5.4 de OpenCode 2 (`zai-coding-plan/glm-5.4`) implementó los bloques BASE, A y B con `poteto-mode` completo. Por orden de David (2026-11-24: GLM 5.4 está lentísimo, se deja de usar como implementador), desde las correcciones del PR #751 y para los bloques C a K y Z implementa cada bloque `deepseek/deepseek-flash` de OpenCode 2, también con `poteto-mode` completo. En la PC, el skill está en `C:/dev/goncloud-hermes/shared/sistema-agentes/poteto/skills/poteto-mode/SKILL.md`. El revisor asignado es `GPT-6.2-Astra` de Codex (`gpt-6.2-astra`) u otro modelo distinto al implementador, más `ai-review` en GitHub (informativo); Devin no cuenta ni abre rondas. Cada bloque entra por PR a `master` bajo la política de `AGENTS.md`: **tope de 3 rondas totales por PR** (atendiendo `ai-review` y revisor juntos), bloqueando solo fallos P0/P1 reales (uso real roto, seguridad/secretos, o dinero/datos ante operación normal o fallo simple). Esquinas de 2 o más fallos/carreras encadenadas son P2: van a notas y no abren ronda. Tras la ronda 3, si no quedan P0/P1 reales se integra con notas; si queda un P0/P1 real, se lanza automáticamente **1 relevo con contexto limpio y el modelo más fuerte disponible distinto al implementador y al revisor** (máx 2 rondas, aplicando `principle-attack-the-premise`); si incluso el relevo deja un P0/P1 real, el PR se estaciona en Draft para David y el coordinador continúa con el siguiente trabajo independiente.

Contrato: [DISENO.md](../docs/evidencia/arreglos-05/DISENO.md).
Cobertura: [COBERTURA.md](../docs/evidencia/arreglos-05/COBERTURA.md).
Comprobaciones: [ACEPTACION.md](../docs/evidencia/arreglos-05/ACEPTACION.md).
Leerlos antes del bloque. Prevalecen sobre las candidaturas archivadas. Si el código
posterior a TEMPORADA difiere, registrar el delta concreto en `BASE/diferencias.md`
y ajustar firmas/fixtures por contenido, conservando comportamiento y política.

Para el dueño: un fallo de Amazon no dejará sin atender el resto del ciclo; una
recuperación no duplicará acciones ni renovará el cupo. Reversas y reposiciones
conservarán la identidad real. Se limita a 150 pujas ordinarias por ciclo; entrada
y regreso de temporada quedan fuera. La meta seguirá convergiendo con datos
vencidos y Ajustes rechazará valores que hoy descarta silenciosamente.

Son **13 bloques: BASE, A–K y Z**. Orden serial BASE → A → B → C → D → E → F → G →
H → I → J → K → Z, por contratos y archivos compartidos; las dependencias de cada
bloque indican además qué resultado necesita. No abrir implementaciones simultáneas
en `cycle.py`, `apply.py` o `config_write.py`. 03/04 son dueños de sus hallazgos: 05
consume su evidencia, no hace otro arreglo de lo mismo.

Reglas de ejecución futura:

- Rama aislada desde el master vigente; plan/diseño/evidencia disponibles en master
  al abrir BASE.
- En cada bloque, red/green contra el padre real para las regresiones que se
  incorporen. Errores de fixture, skips o cero casos recogidos no prueban el fallo.
  Elegir vecinos por riesgo; no pruebas de texto ni mutantes.
- Cada comando `N letra` de las tablas está definido en ACEPTACION; exige crear
  las regresiones `test_arreglos05_<letra>_*` descritas, que verifican efectos reales
  con PG desechable y HTTP simulado. `T grupo filtro` usa reproducciones existentes.
  Cada shell nuevo puede ejecutar `uv run --frozen --python 3.12 bash
  docs/evidencia/arreglos-05/comprobar.sh N a` (o `T grupo filtro`) sin funciones
  cargadas de otra sesión. Sus prerrequisitos siguen en ACEPTACION.
- Migración con reversa antes de activación, número libre, sonda del instalador,
  permisos mínimos y consulta productiva previa al invariante. No cambiar 0001/0002.
- Guardar comando, SHA, resultado, impacto y reversa en
  `docs/evidencia/arreglos-05/<bloque>/`; sanear secretos. Antes de integrar cada
  cambio, aplicar la política de revisión (tope de 3 rondas, P0/P1 vs P2) y la revisión de secretos de AGENTS.md.
- Una evidencia que sigue siendo válida se reutiliza; un cambio de contrato exige
  revalidar sus consumidores. No repetir suite completa sin una razón nueva.
- Las `prueba-real` de este plan son comprobaciones en laboratorio con SQL/HTTP/UI reales o simulados solo en su frontera externa; no acreditan un despliegue. La verificación productiva posterior conserva lectura RO de schema, huella del código y resultado del bloque, con recibo separado.
- **Autorización completa del dueño (2026-11-22, actualizada 2026-11-24):** ningún bloque espera una nueva confirmación de David. Este plan autoriza de punta a punta las lecturas de solo lectura a producción (`lee_prod.sh`), la actualización de `plans/manifest.json` a `active: arreglos-05` en `BASE`, la creación de PRs a `master`, el merge a `master` con CI verde cuando el revisor asignado apruebe el `headRefOid` actual o al cerrar el tope de rondas sin P0/P1 reales abiertos (anotando P2 en el PR y sin esperar a Devin), y el despliegue a producción de cada bloque `lane: release` mediante `docs/DEPLOY.md` (`tools/despliegue/desplegar.sh` y `tools/despliegue/checklist.sh`), guardando el recibo de despliegue en `docs/evidencia/arreglos-05/<bloque>/`. Si tras la ronda 3 sigue abierto un P0/P1 real, se autoriza automáticamente 1 relevo con contexto limpio y el modelo más fuerte disponible distinto al implementador y al revisor (máx 2 rondas); si incluso el relevo deja un P0/P1 real abierto, ese PR se estaciona en Draft para David y el coordinador sigue con el siguiente trabajo independiente. Esta autorización **no** enciende precios ni pausa por producto automáticamente (conservan su modo vigente) ni permite force-push a `master`. Revisión de código y despliegue productivo quedan acreditados por recibos distintos.

## Bloque BASE — Fijar el estado integrado y preparar aceptación
lane: fast
depende: -
pruebas: reutilizar evidencia; fixtures de regresiones
aviso: Se fijó el código que recibirá los arreglos y quedó identificada cada prueba de cierre.

| Fila | Contenido | Acepta |
|---|---|---|
| BASE.0 | Verificar que plan, diseño, cobertura y aceptación están en master; registrar SHA exacto y evidencia de cierre 03/04/TEMP. Comparar las firmas B/E/F/G de TEMP con DISENO; no usar líneas antiguas. | `git merge-base --is-ancestor origin/master HEAD && (for f in plans/arreglos-05.md docs/evidencia/arreglos-05/{DISENO.md,COBERTURA.md,ACEPTACION.md,comprobar.sh}; do git cat-file -e "origin/master:$f" \|\| exit 1; done)` más `BASE/contratos.md` con firmas reales y SHA. |
| BASE.1 | Preparar el laboratorio Python 3.12/PG16/Chromium descrito en ACEPTACION, sin credenciales reales. Portar fixtures que cambiaron conservando la aserción del usuario; 31 casos base y casos vecinos identificados. Las cerradas por 03/04 deben estar verdes, no esperar 17 rojos otra vez. | Runner de ACEPTACION; `BASE/resultados.xml` distingue cada fallo de producto del de entorno; cero errores de fixture/omisiones. |
| BASE.2 | Leer esquema y datos de producción con SELECT RO: envelopes históricos con/sin decisiones, intentos abiertos, claves shadow/live y colisiones, reposición, versión de inputs/temporada y modos usados. Comparar con `base/` sin sustituir observación por suposición. | `BASE/previa.sql`, resultado saneado y lista de invariantes demostrados; número de migración disponible confirmado. |
| BASE.3 | Confirmar controles 01/08/09/D2, banda manual 01X y TEMP B.2/E/F. Recuperar actas residuales de 01/02/DISEÑOS sin rehacer entregas. | `T writes test_01`; `T pricing 'test_08 or test_D2'`; `T ingest test_09`; `CI=1 PYTHONPATH=.:tests uv run --frozen --python 3.12 python -m pytest -q tests/test_config_write.py -k arreglos01_x` y controles TEMP existentes. |

## Bloque A — Completar un ciclo sin duplicar sus decisiones
lane: release
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
depende: BASE
pruebas: dinero, escritura y recuperación tras crash
aviso: Un fallo individual ya no detiene todo el ciclo y el horario siguiente puede completar lo pendiente.
prueba-real: uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N a

| Fila | Contenido | Acepta |
|---|---|---|
| A.1 | Regresiones de ciclo F1/F2/F3; promover la reproducción antigua contra la base posterior a TEMP. Primer bid 5xx, segundo elegible y cola; ciclo degraded recuperable; lock expirado con running reciente; caída antes/después de TX3; dos workers y pérdida de owner, con respuesta HTTP retenida. | `N a`: otros objetos/fases progresan ante fallo individual; ningún envío nuevo autorizado ni sello del owner vencido. Un HTTP ya enviado puede terminar y se concilia sin repetirlo. Cero decisiones/HTTP duplicados. |
| A.2 | `cycle.py`, `cli.py`: operación completa `atender_plataforma`; claim único, conciliación pre-TX2 de TEMP, progreso durable y ejecutor `resume_of` ligado al origen. Migración aditiva `decision_committed_at`/`resume_of`, sello único y grants por columna. Retirar heurística de 3 h y atajo degraded que impide apply. | `N a`; suite existente de ciclo/cron/locks; lectura desde segunda conexión ve TX3 comprometida, incluso lote válido vacío. |
| A.3 | `apply.py` y fase de cola: aislamiento por entidad/fase; fallos globales abortan. Revalidar goal/mode/ancestros/vigencia en recuperación. Al día siguiente conciliar ambiguos y descartar elegibilidad ordinaria vencida, sin borrar historia. Ajustar resúmenes de ciclos para no duplicar decisiones. | `N a`; control `T writes test_01`; cero PUT de una puja incierta, sin un segundo cupo por recuperación. |
| A.4 | Migración/reversa e instalador, docs/APPLY y DATABASE reflejan origen/ejecutor y cambio de aislamiento. Rollback conserva intentos y no vuelve a binario incompatible. | `N a` con roles reales y migración ida/reversa; revisión de secretos del bloque. |

## Bloque B — Reversas sobre identidad y valor comprobados
lane: release
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
depende: A
pruebas: dinero, escritura y reversa
aviso: La reversa conserva objetos ajenos y puede deshacer un negative confirmado por conciliación.
prueba-real: uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N b

| Fila | Contenido | Acepta |
|---|---|---|
| B.1 | `apply_harvest.py` y reconciliación: ID del ACK inmutable, lectura completa de 04 B-C, distinción propio/adoptado. Mismo resolver para reversa manual, automática y hermanas. | `T writes test_03`; `N b`: ID sustituido/preexistente conserva cero DELETE ajenos y ninguna autoría transferida. |
| B.2 | `apply.py`: resolver confirmación normal o reconciliada del negative; evidencia previa e ID/procedencia durable, sin falsificar ACK. Reversa manual confirma una vez; falta de identidad o evidencia mantiene pendiente. | `T writes test_10`; `N b`: 200 y un DELETE en el caso inequívoco, segundo intento cero HTTP; vecino preexistente/duplicado/ID cambiado no se borra. |
| B.3 | Reversa bid usa valor leído antes del PUT por TEMP E, no old_value de cache; relectura viva y respeto a cambio manual posterior. Crash durante reversa concilia antes de otro envío. Documentar límite de autoría de la reconciliación. | `N b`: vuelve al valor previo comprobado; intervención posterior se conserva; reverse timeout no duplica HTTP. |

## Bloque C — Reponer keywords con intención y ACK durables
lane: release
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
depende: B
pruebas: escritura, concurrencia, reversa e instalación
aviso: Repetir la reposición ya no crea otra keyword cuando falla la lectura de confirmación.
prueba-real: uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N c

| Fila | Contenido | Acepta |
|---|---|---|
| C.1 | Migración/reversa de `keyword_reposicion_intento` según diseño; owner/lease por fila, payload inmutable, sellos y exclusión de intentos simultáneos. Medir tabla/IDs otra vez antes de aplicar; no asumir que sigue vacía. | `N c`: roles, invariantes, ida/reversa y dos conexiones; intención visible antes de HTTP. |
| C.2 | `ads/archivar.py` posee `reponer_keyword`; CLI conserva argumentos, sin protocolo duplicado. Guardar ACK antes de readback, confirmar por ID y sellar archivo/intento atómicamente. | `T writes test_11`; `N c`: dos CLI y crash antes/después de POST/ACK/readback producen como máximo un POST incierto y un objeto PAUSED. |
| C.3 | Implementar antes de habilitar el nuevo camino su reversa auditada sobre el ID repuesto propio; desconocido solo concilia. Rechazo definitivo permite otro intento registrado. No borrar herramienta ni historial. | `N c`: reversa propia confirmada/idempotente, crash no repite; resultado ambiguo jamás habilita recreación ciega. |

## Bloque D — Toda mutación Ads pasa por el cliente protegido
lane: release
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
depende: C
pruebas: seguridad, escritura y reversas
aviso: Fábrica, herramientas y sondas usan la misma protección de rutas y perfiles al escribir en Amazon.
prueba-real: uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N d

| Fila | Contenido | Acepta |
|---|---|---|
| D.1 | Inventario de escritores: fábrica y consumidores API/impulso/vigía, archivo/reposición, reactivación y sondas. Por cada caller: método/ruta/perfil, modo, ledger, confirmación y reversa. | `D/escritores.tsv`, contrastado con `rg -n -e '_client[.]request' -e 'def _post' app tools`; revisión semántica, no criterio grep=cero. |
| D.2 | Extender operaciones específicas de `ads/write.py` solo para capacidades existentes; migrar callers y retirar bypass. `preparar_escritor` recibe modo real. No ampliar cliente read-only ni hacer público request arbitrario. ACK por ítem conserva tratamiento 207. | `N d`: perfil/ruta/método ajenos denegados; cada mutación tiene intención pre-HTTP; errores por ítem no se sellan ok. |
| D.3 | Verificar fábrica parcial, retiro, reactivación, sonda y reposición/reversa por sus dueños. Mantener candados de imports existentes con el mapa nuevo. | `N d` y suites existentes `test_ads_write`/fábrica/archivo aplicables; cero bypass mutantes sin ledger/guard. |

## Bloque E — La práctica vencida no bloquea el trabajo real
lane: release
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
depende: D
pruebas: escritura, veto e instalación
aviso: Una propuesta de práctica vencida deja de congelar la keyword, conservando su historial.
prueba-real: uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N e

| Fila | Contenido | Acepta |
|---|---|---|
| E.1 | `apply_cola.py`, migración/reversa del índice mixto: unicidad live y deduplicación de prácticas vigentes bajo claim. No dar permiso de descartar shadow al motor; mantener historia, 48 h y UTC. | `N e`: shadow vencida no bloquea SELECT ni INSERT de la nueva decisión, live/veto vigente sigue protegido. |
| E.2 | Probar mismo efecto con shadow vieja, shadow vigente y live; rollback con colisiones rehúsa eliminar filas. Cola/temporada conserva prioridad y reactivación aprobadas. | `N e`; comparación de filas históricas antes/después idéntica, caso de dos workers sin doble efecto live. |

## Bloque F — La meta base sigue avanzando sin confundirse con temporada
lane: release
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
depende: E
pruebas: dinero y replay
aviso: La meta sigue acercándose a su destino cuando el libro está vencido y conserva separada la meta de temporada.
prueba-real: uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N f

| Fila | Contenido | Acepta |
|---|---|---|
| F.1 | `cycle.py`, inputs/lecturas y replay: ancla del último base live comprometido independiente de procedencia, apply y overlay. Versionar base/efectiva conservando historia; práctica no cambia ancla live. | `T optimizer test_12`; `N f`: 29/28.5/28 también con overlay alta25, salida retoma base; sin inferir margen fresco. |
| F.2 | Snapshot viejo sin base inequívoca conserva semántica vieja; no reconstruir overlay por suposición. Demostrar replay de decisiones antiguas y lote nuevo después de recuperación parcial. | `N f`; replay contra inputs congelados sin diferencias no previstas, delta de versión documentado. |

## Bloque G — Un término reobservado no revierte todo el lote
lane: release
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
depende: F
pruebas: datos, dinero y replay
aviso: El ciclo conserva decisiones válidas cuando recibe una observación nueva de un término maduro.
prueba-real: uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N g

| Fila | Contenido | Acepta |
|---|---|---|
| G.1 | `cycle.py`: aplicar sello bitemporal común también a harvest sin corte, preservar observed_at original y ventana. No relajar trigger ni madurez. | `T optimizer test_13`; `N g`: lote mixto se confirma, replay igual, datos inmaduros siguen fuera y observaciones no se reescriben. |

## Bloque H — Reservar como máximo 150 pujas ordinarias por ciclo
lane: release
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
depende: G
pruebas: dinero y concurrencia
aviso: Cada ciclo puede intentar hasta 150 pujas normales; una recuperación no vuelve a abrir el cupo.
prueba-real: uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N h

| Fila | Contenido | Acepta |
|---|---|---|
| H.1 | `apply.py`: reserva e intención en la misma TX, bajo claim y lock del origen. Contar intentos normales bid del ledger, incluidos ambiguos/rechazados. Solo entrada/regreso de TEMP están exentos por marcador congelado; reversas no cuentan. | `N h`: 151 ordinarias → como máximo150 intentos, 149+ambiguo=150, recuperación/cambio de ejecutor no concede otro. |
| H.2 | Integrar motivo de tope sin abrir un ciclo nuevo para eludirlo; cuotas diarias y prioridad TEMP siguen vigentes. No reducir cortes ni excluir toda recuperación ordinaria. | `N h`: entrada/regreso elegibles fuera del cupo, Recupera ordinaria dentro; dos procesos y crash pre/post-HTTP no superan150. |

## Bloque I — Ajustes rechaza una cuota que no puede guardar
lane: release
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
depende: H
pruebas: configuración que controla dinero; flujo real UI
aviso: Ajustes muestra el error si una cuota no es un entero válido y deja de decir Guardado cuando la ignoró.
prueba-real: uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N i

| Fila | Contenido | Acepta |
|---|---|---|
| I.1 | `settings.js`, `config_write.py` y endpoint existente: validar cuotas antes de serializar; API distingue omitido/null inválido y preserva null permitido en otros campos. Usar registro canónico y versión optimista. | `N i`: formulario real con `1,000` no envía; API con null/bool/fracción ilegible rechaza sin nueva versión; entero válido guarda y versión vieja da409. |
| I.2 | Banda manual 01X ya corregida, Goals pertenece a04 W-I. Preservar campos TEMP y controles apagadores. Actualizar ficha Settings y recorrerla. | Control01X y `orbit-verify drive-settings` tras launch/doctor; caso adverso del formulario además del recorrido básico. |

## Bloque J — Retirar modos sin uso conservando historia y apagadores
lane: release
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
depende: I
pruebas: dinero, equivalencia y replay
aviso: Se retiraron caminos obsoletos comprobados, conservando los controles y la reproducción de decisiones antiguas.
prueba-real: uv run --frozen --python 3.12 bash docs/evidencia/arreglos-05/comprobar.sh N j

| Fila | Contenido | Acepta |
|---|---|---|
| J.1 | Inventario de lectores/configs/decisiones: política antigua, meta producto shadow, economía antigua y flag sin lector descritos en02. SELECT RO actual antes de eliminar. Conservar pausa producto shadow, modos globales, replay/reversa y práctica TEMP aún necesaria. | `J/modos.tsv` con usado/retirable/conservado y evidencia, sin borrar por coincidencia de texto. |
| J.2 | Eliminar ramas runtime y controles obsoletos; migrar callers en el mismo bloque. Config ausente/ilegible permanece sin permiso de mutación. No retirar lectores históricos necesarios. | `N j`: decisiones antes/después sobre inputs congelados equivalentes, replay y apagadores vigentes; UI muestra solo modos disponibles. |

## Bloque K — Adjudicar la sospecha sobre impuesto de comisiones
lane: fast
depende: J
pruebas: dinero y conciliación externa
aviso: La estimación explica los componentes que pudo conciliar y conserva desconocido lo que la fuente no permite demostrar.

| Fila | Contenido | Acepta |
|---|---|---|
| K.1 | Tras02 D y03 P-G, comparar cotización íntegra, cargo externo y escenario por plataforma/moneda/fecha. Separar incluido, separado o desconocido. No inferir tasa/regla fiscal de la sospecha. | `K/conciliacion.md` y datos saneados con fuente, importes y diferencia; conclusión sustentada o bloqueo de evidencia explícito. |
| K.2 | Si la fuente descarta la sospecha, cerrar sin código. Si confirma costo omitido según contrato vigente, incorporar importe una sola vez y versión/reversa. Si no adjudica, mantener abstención y K abierto; no inventar 16%/cero. | `N k` cuando cambie cálculo: TaxAmount anidado, total inclusivo, impuesto separado y dato ausente; conciliación contra fuente. Precios sigue shadow efectivo. |

## Bloque Z — Orbit completo pasa o queda explícitamente pendiente
lane: fast
depende: K
pruebas: aceptación integrada, secretos y revisión completa
aviso: Se publicó el resultado de la revisión completa del código integrado, con evidencia de cada cierre y cualquier pendiente real.

`depende: K` exige K evaluado y documentado. Si su fuente sigue insuficiente, Z
puede producir un informe parcial de comprobaciones y pendientes; K permanece
abierto y el plan no recibe pase global. No bloquear la publicación de evidencia
por esa espera ni marcar K terminado para habilitar artificialmente Z.

| Fila | Contenido | Acepta |
|---|---|---|
| Z.1 | Completar matriz de 14 IDs, controles cerrados y legado de53 fallas/decisiones. Cada fila tiene owner, commit y comprobación; K no se oculta si sigue sin evidencia. Confirmar 03/04/TEMP completos por su DoD. | `Z/cobertura.tsv`: sin bloqueo conocido huérfano ni cierre basado solo en tener archivo/checkbox. |
| Z.2 | Ejecutar aceptación en el SHA conjunto y vecinos relevantes: crash/retry/reversa, fuente/cache, agregados/reconstrucción, temporada/replay y UI adversa. Promover regresiones útiles a `tests/`; conservar el significado de las31 originales. | ACEPTACION: 31 casos base verdes (o reemplazo trazado de fixture, nunca assertion debilitada), vecinos verdes, cero errores/skips. |
| Z.3 | El revisor asignado (Astra u otro modelo distinto al implementador) realiza la revisión global de toda la app del SHA integrado. Cubrir módulos nuevos de TEMP, rutas ejecutables, fronteras y acumulación de condiciones. Solo bloquea P0/P1 real (según `AGENTS.md`, tope de 3 rondas); esquinas de 2+ fallos encadenados y notas estructurales van aparte como seguimiento. | `Z/review.md`: alcance/archivos/caminos revisados, hallazgos P0/P1 vs P2 y veredicto explicado; cero bloqueos P0/P1 reales abiertos para declarar PASA. |
| Z.4 | Reunir los recorridos del mapa UI, los flujos adversos afectados, la aceptación en PG16 de Z.2 y la revisión de secretos sobre el mismo SHA. Reutilizar los resultados de ese commit. | `Z/resultado.md`: SHA, XML de aceptación, resultados de UI, secretos y pendientes. Despliegue se acredita aparte. |

**Resultado esperado:** al terminar las correcciones y pasar Z, Orbit puede declararse
aprobado en esta revisión. Completar los planes no reemplaza Z. Un defecto P0/P1 real según la política de `AGENTS.md`
se asigna a su dueño y se corrige antes del pase (tope de 3 rondas; esquinas P2 de fallos encadenados se registran como seguimiento); una fuente externa
pendiente se informa como tal, sin afirmar que toda operación está certificada.
