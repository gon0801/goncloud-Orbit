# Diseño de los arreglos y cierre global

Contrato propuesto el 15-nov-2026 sobre `ff03ed61`. Implementar después de las
dependencias de [la cola](../../../plans/ORDEN-IMPLEMENTACION.md); no copiar números
de línea de esta base sobre el futuro código de TEMPORADA. Las firmas del
[boceto](diseno/boceto.py) son propuestas, no APIs existentes.

## Uso antes que interfaces

```python
# CLI: el dueño del ciclo resuelve claim, decisión o recuperación y cierre.
resultado = atender_plataforma(conn, plataforma, ahora=ahora)
# resultado conserva el ciclo de origen y el ejecutor de esta invocación.

# CLI de archivo: repetir --reponer retoma la evidencia de la misma fila.
resultado = reponer_keyword(conn_admin, archivo_id, actor=actor)
# Pendiente significa leer/conciliar; nunca volver a crear a ciegas.

# Adaptador MeLi: el mismo camino para la cotización fresca y la guardada.
cotizacion = cotizacion_desde_evidencia(precio, evidencia_fee)
# Conserva fecha original, moneda y árbol de fees; no rejuvenece el cache.
```

El caller no coordina pasos `load/validate/save`, no elige rutas HTTP ni interpreta
estados del ledger. Los propietarios actuales completan la operación de su dominio.
Los tipos del boceto se colocan en esos propietarios, no en un paquete genérico.

## Elección y fundamento

Se evaluaron dos formas: reforzar propietarios actuales o crear un coordinador con
journal transversal para bids, cortes, fábrica y reposición. Ambas candidaturas
eligieron la primera tras contrastarlas. La segunda requeriría adaptadores y doble
escritura mientras siguen vigentes los ledgers de negocio; no elimina sus reglas
de veto, autoría, cuotas o episodios. Añadirla ahora aumenta los estados a conciliar.

| Criterio revisado | Propietarios existentes | Coordinador transversal |
|---|---|---|
| Cobertura de los 14 hallazgos y del legado | Sí, con extensiones concretas de 03/04/05 | No aporta solución adicional a datos, ratios, UI o cotización |
| Una fuente y un dueño por efecto | Conserva los registros existentes | Coexistirían journal nuevo y registros de cada dominio |
| Crash, reintento y reversa | Intención durable y confirmación por identidad, dentro de cada dueño | Protocolo común, más migración y traducciones |
| TEMPORADA y replay | Contratos versionados, sin trasladar el dueño del episodio | Obliga a adaptar también episodios y su atomicidad |
| Superficie del caller | Una operación por intención del usuario | Una fachada adicional y adaptadores específicos |
| Complejidad que se elimina | Lectores permisivos, HTTP alternativo, heurística de 3 h, previa obsoleta | No elimina automáticamente ninguno de esos caminos |

Base de síntesis: candidato A por separar F/G/H en unidades comprobables, su ancla
base independiente de temporada y ledger de reposición separado. Se incorporan de
B la separación entre ciclo origen y ejecutor, el límite explícito de autoría de
negatives y la distinción entre ausencia de un campo e invalidez explícita.
Se descartan la reapertura del mismo envelope cerrado de A, el slot duplicado en
`apply_attempt` de B y el cambio de `keyword_archivo_manual` para almacenar una lista
mutable de intentos. El cupo se deriva del ledger; reposición necesita eventos propios.
No se agrega otro lock advisory: el claim existente gobierna, y una intención
incierta bloquea su entidad incluso si vence el lease. Ningún candado local puede
cancelar un HTTP ya enviado a Amazon.

La razón histórica importa: `0002_apply.sql`, `docs/APPLY.md` y el trabajo BIDS02
(`540a5e53`) introdujeron intención previa, sello y conciliación para sobrevivir al
crash entre base y Amazon. Se conserva esa decisión; se cierran sus caminos que
todavía no la cumplen. No se convierte `apply_queue` en cola de bids: es de cortes.

## Módulos, fuentes y eliminaciones

| Dueño | Operación/dato canónico | Se elimina o deja de duplicarse |
|---|---|---|
| `cycle.py` | Claim, origen/ejecutor, snapshot y progreso de decisión | Heurística de 3 h y bloqueo diario que impide completar apply |
| `apply.py` | Intentos, cupo, confirmación, reversas de bid/negative | Reversa basada solo en `resultado='ok'` y contador de cupo en memoria |
| `apply_harvest.py` y reconciliación | Lectura completa y evidencia de identidad | `_lista_todos` permisivo y sustitución de ID manteniendo autoría |
| `apply_cola.py` | Veto/cortes, unicidad live y práctica vigente | Práctica vencida bloqueando decisiones para siempre |
| `ads/write.py` | Único transporte mutante, perfil fijo y rutas exactas | Mutaciones directas por `_client.request` fuera del cliente protegido |
| `ads/archivar.py` | Reposición por fila, intentos y reversa propios | Protocolo HTTP/DB incrustado en `tools/archiva_inertes.py` |
| `reputacion_clientes.py` / `meli/catalogo.py` | Resultado completo/parcial / deltas confirmados | Inferir ausencia de una lectura parcial y contar antes del savepoint |
| `meli/precio_mercado.py` | Cotización fresca/cache al puerto existente | Constructores que pierden `estimada_en` o `Fees.detalle` |
| `productos.js` | Revisión, pedido y previa de cada flujo | Payload de una selección anterior o respuesta fuera de orden |
| `pantalla_ruido.py` / `ui_metricas.py` | Cobertura del ratio / frase derivada de hechos | Sumar NULL como cero y confundir dato faltante con venta cero |
| `jev_libro.py` | Saneamiento antes de persistir | Errores externos crudos almacenados |
| `config_write.py` | Validación y versión optimista existentes | `NaN → null → Guardado` y registro paralelo de campos |
| `temporada_io.py` futuro | Episodio, efecto, fin y exclusión | Ningún segundo registro estacional en 05 |

El transporte protegido no decide cuotas ni política de negocio. Fábrica conserva
su ledger y dueño; reposición conserva el suyo. Migrar todos los callers y retirar
el camino anterior en el mismo bloque. No crear fachadas que solo reenvían argumentos.
Los módulos grandes se reducen cuando una responsabilidad tiene dueño claro y
comportamiento verificable; partirlos por longitud no constituye un cierre.

## A. Recuperación del ciclo y aislamiento de fallos

Flujo: claim → conciliación previa a TX2 → snapshot/decisión → TX3 → apply → cierre.
TEMPORADA B.2 es el único dueño de mover la conciliación antes de TX2; 05 lo verifica.

Agregar de forma aditiva a `optimizer_cycle`:

- `decision_committed_at TIMESTAMPTZ NULL`: sello único en la misma TX3 que las
  decisiones, incluso si el lote válido tiene cero decisiones.
- `resume_of BIGINT NULL REFERENCES optimizer_cycle(id)`: solo un ejecutor de
  recuperación referencia directamente al origen; nunca una cadena de recuperaciones.

Los ciclos nuevos sin `resume_of` son dueños de decisiones. El ejecutor recuperado
no vuelve a tomar snapshot ni inserta decisiones; deja `decisions_count=0` y muestra
la referencia al origen en su resumen. `decision_application.applied_cycle_id`
conserva el ejecutor que confirmó. No reabrir envelopes históricos cerrados ni
sumar dos veces métricas de decisiones en dashboard/digest.

Antes del atajo «ya terminó hoy», consultar si el origen live tiene decisiones
comprometidas que aún pueden completarse. Si apply falló, un horario posterior
abre un ejecutor ligado a ese origen. El contador de cupo y la decisión permanecen
en el origen. Sin TX3 comprometida se puede decidir de nuevo; una caída antes de
TX3 no debe parecer un lote vacío finalizado. Para históricos sin sello, usar
evidencia inequívoca de decisiones/TX3 y registrar su clasificación; no backfill por
`status=done` a ciegas. BASE mide esa población antes de la migración.

La vigencia ordinaria se limita al día de decisión conforme al reloj ya aprobado.
Al cambiar de día se concilian los intentos ambiguos anteriores; lo ordinario no
intentado deja de ser elegible, con motivo auditable, y el nuevo ciclo decide con
datos frescos. No borrar decisiones ni promover propuestas shadow. Antes de una
escritura nueva se revalidan goal, modo, ancestros, vigencia, claim y lectura viva.
La reconciliación de bids sigue siendo **solo lectura**: nunca reenvía la incierta.

Un fallo remoto de una entidad no frena las demás ni la liberación de cortes.
Pérdida del claim, DB indisponible o identidad/permisos globales inválidos detienen
todas las mutaciones. El aislamiento se aplica por entidad y fase, no con un
`except Exception: continue` indiscriminado. Documentar el cambio frente al aborto
del lote de `docs/APPLY.md`; 5xx no autoriza repetir la misma mutación.

El claim atómico y su heartbeat son la autoridad, no la edad de 3 h. Owner distinto
por adquisición; validar owner en reserva, antes de HTTP y al sellar. Un sucesor
puede conciliar, pero la intención durable impide que vuelva a escribir sobre el
destino incierto. Probar dos workers y pérdida de lease con HTTP en vuelo. Un
HTTP tardío no se considera cancelado por haber vencido el lock.

## B. Identidad, confirmación y reversas

Harvest: el ID del ACK es inmutable y se guarda antes del readback. La lectura
verifica ese ID, perfil, grupo, texto, match y estado. Un ID distinto con el mismo
texto es ajeno/adoptado; nunca conserva `keyword_creada=True`. La reversa manual,
automática y de hermanas consume la misma evidencia. Un booleano solo no autoriza
archivar. Ante sustitución o identidad dudosa: pendiente y cero DELETE ajenos.

Negative conciliado: guardar antes del POST la identidad y ausencia previa
completa, vinculadas al intento; guardar luego ID y procedencia de confirmación
por LIST en `decision_application.platform_ack`, derivada de la evidencia durable.
No fabricar un ACK remoto ni buscar simplemente `resultado LIKE 'ok%'`. La reversa
manual resuelve la confirmación de ese efecto, revalida el ID vivo, registra su
intento y confirma una vez. Segunda reversa confirmada: cero HTTP.

Ausencia previa + aparición posterior no prueba autoría absoluta ante un creador
externo concurrente. `EfectoReconciliado` permanece distinto de `CreacionPropia`:
permite la reversa manual del efecto inequívoco con su procedencia visible, nunca
autoriza la limpieza automática de harvest ni adopta un objeto preexistente. Si
no hay evidencia previa, hay múltiples candidatos o el ID/identidad cambia, queda
pendiente. La foto actual no tiene negatives reconciliados; no inventar backfill.

Bid: TEMPORADA E aporta la lectura previa al PUT y su valor real congelado. La
reversa utiliza ese valor, verifica el valor actual y respeta una intervención
manual posterior. Caída después del PUT de reversa requiere readback antes de
otro envío. Ninguna reversa borra el ledger original.

## C. Reposición durable por fila

`ads.archivar.reponer_keyword` conserva la CLI `archiva_inertes --reponer`.
Agregar `keyword_reposicion_intento` con referencia a `keyword_archivo_manual`,
secuencia, owner/lease, tipo (`reponer`/`reversa`), payload exacto, ACK, ID, resultado
y tiempos. Identidad/payload son inmutables; ACK/resultado se sellan una vez, igual
que el protocolo existente. Índice único por fila/secuencia y exclusión de dos
intentos abiertos de la misma operación. Invariantes temporales en trigger UTC.

Claim por fila → intención **commit visible desde otra conexión** → POST → ACK/ID
durable → readback → sello de intento y de archivo en la misma TX. Dos CLI no crean
dos objetos. Crash incluso entre intención y POST queda incierto; no deducir que
no salió. Con ACK, releer por ID; sin ACK, mantener pendiente si no puede probarse
el resultado. Rechazo definitivo permite intento nuevo documentado; la ausencia
en una lista parcial jamás lo permite. La keyword repuesta nace PAUSED.

Reversa implementada antes de habilitar la reposición nueva: archiva solo el ID
repuesto propio mediante intención y readback. No recrea ni devuelve identidad o
historia a la keyword original archivada. La tabla madre estaba vacía en la foto
de diseño; repetir SELECT antes de migrar. No borrar intentos para hacer un down.

## D–E. Escritor único y prácticas vencidas

D migra fábrica (incluidos API/impulso/vigía), `archiva_inertes`,
`reactiva_campanas` y las sondas mutantes al `AdsWriteClient`. Métodos específicos,
perfil sellado, allowlist literal, validación del ACK por ítem y ledger del dueño
antes del HTTP. El cliente de lectura conserva su default-deny. `preparar_escritor`
no fabrica `live`: recibe el modo autorizado real. Inventariar cada caller, migrarlo
y retirar su bypass; conservar CLI y capacidades previamente autorizadas.

E separa en `apply_queue` la unicidad de efectos live de la deduplicación de
propuestas shadow vigentes. El índice mixto actual debe cambiar, además del SELECT:
una shadow vencida deja de bloquear y una nueva inserción real no choca con ella.
Las prácticas no se promocionan a live ni se borran; su vigencia deriva del reloj
DB. No conceder al motor el descarte shadow reservado al administrador. Mantener
unicidad live, veto, triggers UTC y espera de 48 h. Un down comprueba colisiones
antes de restaurar el índice mixto; no elimina filas para forzarlo.

## F–G. Meta base y sello temporal

F guarda por separado base convergente y overlay estacional en el snapshot
versionado. La base anterior procede del último ciclo live **con decisión
comprometida**, no de `procedencia='margen'`, un apply terminado ni la meta efectiva
de temporada. Con ledger rancio la secuencia aprobada es 29 → 28.5 → 28; el overlay
de alta 25 no pasa a ser la siguiente base. Práctica no modifica el ancla live.
Sin base histórica inequívoca, conservar ausencia explícita y la inicialización
ya aprobada; no reconstruirla restando un overlay supuesto. Replay histórico
consume su versión, nunca la configuración actual.

G aplica el `_sello_bitemporal` existente también a harvest cuando no hay corte.
Conservar `observed_at` real en inputs y métricas append-only; el sello del lote
respeta el contrato temporal y `decision.data_observed_at <= decided_at`; la fecha
original de observación permanece intacta en inputs aunque sea posterior. No quitar el trigger,
rejuvenecer datos ni cambiar ventanas para pasar. Un término maduro reobservado
no debe revertir el lote mixto; los días inmaduros siguen excluidos.

## H. Tope de 150 pujas ordinarias

Se concreta «unas 150» como **máximo 150 intentos ordinarios por ciclo de origen y
plataforma**. Se deriva del join `apply_attempt → decision → optimizer_cycle`:
`kind=bid`, `tipo=normal`, excluidas únicamente entrada/regreso identificados por
el contrato congelado de TEMPORADA. No hay contador paralelo ni cupo nuevo por
ejecutor, fecha de reintento o reinicio.

Bajo el claim y bloqueo transaccional del origen, contar/reservar junto con la
inserción durable del intento, antes del HTTP. Cada intento ordinario durable
consume, incluido incierto, rechazado o crash antes del envío; no se devuelve al
conciliar. Un nuevo intento permitido por el protocolo existente consume otro
cupo; no reinterpretar un retry como exento. Transporte y rate-limit conservan
su política documentada, sin crear reenvíos de resultados ambiguos. Si faltan
clasificadores/versiones de temporada, contar como ordinario o abstenerse.

La reserva 151 se rechaza. Las decisiones no intentadas por tope quedan con motivo
auditable y no arrancan otro ciclo para evadirlo ese día. Mantener las cuotas
diarias actuales como límite adicional; no bajar los topes de cortes. Reversas
y transición de entrada/regreso están exentas; una subida ordinaria `Recupera`
cuenta. 149 intentos más uno ambiguo agotan el cupo, incluso en recuperación.

## I–K. Configuración, retiro de modos y fees

I: cuotas son enteros válidos. UI rechaza `1,000` con error visible y cero POST;
no lo convierte en `NaN/null`. API rechaza null explícito, bool y valores ilegibles
para cuotas; omitir un campo conserva su valor. No quitar null válido de otros
campos. Reutilizar validación canónica, snapshot append-only y versión optimista.
La banda manual de Ajustes ya está corregida por 01X: control, no arreglo nuevo.

J: retirar únicamente ramas runtime obsoletas confirmadas por lecturas/usos:
política vieja, meta producto en práctica y economía antigua según el contrato de
02. Conservar decoders y replay históricos, reversas, apagadores y pausa producto
shadow vigente. Configuración ausente/ilegible nunca se convierte en permiso live.
La práctica de temporada no se elimina por haber integrado su código; requiere
su cierre live acreditado. Si sigue necesaria, queda expresamente conservada.

K: conciliar cotización íntegra, cargo externo y escenario por mercado, moneda,
precio, SKU y fecha. La sospecha de IVA sobre comisiones no prueba una tasa ni un
costo adicional. La decisión vigente sobre retenciones no cambia. Hoy
`_tiene_tax_amount` ya provoca abstención `impuesto_fee_pendiente` ante TaxAmount
no cero, incluso anidado. Preservarla mientras se concilia.

Si el impuesto ya está incluido, no sumarlo otra vez. Si un componente separado
es costo conforme al contrato y a la fuente, incorporarlo una sola vez, con versión
y reversa. Si la fuente no permite adjudicarlo, conservar desconocido/abstención y
registrar la tarea de evidencia abierta. No fabricar cero, 16% ni margen fiable.
K cierra solo con conclusión sustentada, incluso si descarta la sospecha sin cambiar
código. Precios conserva su modo efectivo; este plan no lo enciende.

## Integración con TEMPORADA y migraciones

BASE coteja las firmas reales de `temporada_io`, `OrigenCambio`, `HistoriaBid`,
`recuperacion.cadena_bid`, `Aplicador.aplica_bids`, `_confirma_resumen`,
`registra_sin_aplicar`, `aplica_reactivaciones` y `proxima_config`. Confirmación y
efecto de temporada siguen atómicos. Recuperar no abre un segundo episodio ni
repite entrada/regreso. Preservar prioridades, topes de presupuesto y centavos.
Registrar diferencias de firmas antes de implementar; no otra API paralela.

Migraciones aditivas previstas: progreso/origen (A), reposición (C), índice shadow
(E); F versiona inputs y H usa el ledger existente. B amplía evidencia sellada
sin falsificar ACK; si el esquema posterior lo exige, su delta se registra antes.
Numeración libre al rebasar; 0086 ya está ocupada. Incluir sonda, reversa, selector
del instalador, grants mínimos y pruebas de ida/reversa con los roles reales.
El grant de sello de `decision_committed_at` es por columna y con trigger de una
sola transición; `resume_of` se fija al INSERT. No ampliar permiso general UPDATE.

Antes de cada invariante dependiente de datos, correr su SELECT de producción y
guardar resultado redacted. La foto de esta entrega no demuestra todas las
colisiones futuras. Un rollback conserva historia y requiere binario compatible
con intentos abiertos; detener nuevas mutaciones y conciliar antes de degradar.
La reversa de efectos y la reversa de despliegue son operaciones diferentes.

## Cierre y límites

Todos los IDs tienen un owner en [COBERTURA.md](COBERTURA.md). Los comandos y el
tratamiento de fixtures están en [ACEPTACION.md](ACEPTACION.md). Z vuelve a aplicar
`pstack:thermo-nuclear-code-quality-review` a **toda la app integrada**, incluyendo
los nuevos caminos de temporada y vecinos de cada causa; no solo a los diffs.
No se declara pase con errores de entorno, casos omitidos ni CI de otro commit.

Las preferencias de estructura se separan de bloqueos con reproducción e impacto,
según AGENTS. D1 no cambia política por este plan. Los seguimientos externos del
inventario no se declaran hechos por integrar código; tampoco son automáticamente
defectos de la app. Fuente fiscal insuficiente deja K abierto, no un cierre ficticio.
Pase significa cero bloqueos confirmados pendientes dentro del alcance revisado,
con evidencia del mismo SHA; no certifica ausencia universal de bugs ni despliegue.
